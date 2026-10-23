"""B12 underwriter queue, bank card and manual decisions on PostgreSQL 16."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from test_auth_postgres import csrf, headers
from test_client_actions_postgres import drain, events, in_rig, post
from test_decision_worker_postgres import company_key as company_key
from test_decision_worker_postgres import queued as queued
from test_decision_worker_postgres import ready as ready
from test_drafts_postgres import actors as actors
from test_drafts_postgres import demo_hash as demo_hash
from test_drafts_postgres import draft_settings as draft_settings
from test_submissions_postgres import path
from test_worker_postgres import rig as rig

from app.models import Decision, Offer, Product

QUEUE = "/api/underwriter/applications"
PUBLIC_KEYS = {"id", "created_at", "outcome", "reasons", "explanation", "required_documents"}


def bank_path(application):
    return f"{QUEUE}/{application['id']}"


@pytest.fixture
async def review(ready):
    """A real MANUAL_REVIEW from decide() for the seed company `manual`."""
    rig = ready
    await drain(rig)
    rig.bank_h = headers(csrf(rig.actors.bank))
    card = rig.actors.bank.get(bank_path(rig.application))
    assert card.status_code == 200, card.text
    rig.card = card.json()
    assert rig.card["application"]["status"] == "MANUAL_REVIEW"
    return rig


def terms(rig, code="express", **changes):
    products = {p["code"]: p["id"] for p in rig.actors.first.get("/api/products").json()["items"]}
    return {
        "product_id": products[code],
        "amount": "2000000.00",
        "term_months": 24,
        "annual_rate_percent": "25.5",
        **changes,
    }


def body(rig, outcome, **changes):
    base = {
        "expected_revision": rig.card["application"]["revision"],
        "outcome": outcome,
        "reasons": [],
        "explanation": "Решение принято после проверки документов.",
        "analytical_explanation": "Внутренний комментарий аналитика.",
    }
    if outcome in {"APPROVED", "COUNTER_OFFER"}:
        base["offer_terms"] = terms(rig)
    if outcome == "COUNTER_OFFER":
        base["reasons"] = [{"code": "term_extended", "message": "Увеличили срок."}]
    if outcome == "NEEDS_INFO":
        base["required_documents"] = [
            {"code": "debt_details", "title": "Расшифровка долгов", "explanation": "Список."}
        ]
    if outcome == "REJECTED":
        base["reasons"] = [{"code": "low_score", "message": "Недостаточно оснований."}]
    return {**base, **changes}


def decide(rig, payload, *, client=None, h=None):
    return (client or rig.actors.bank).post(
        bank_path(rig.application) + "/decision", json=payload, headers=h or rig.bank_h
    )


@pytest.mark.anyio
@pytest.mark.parametrize("company_key", ["manual"])
async def test_queue_card_and_role_boundaries(review):
    rig, actors = review, review.actors
    response = actors.bank.get(QUEUE, params={"limit": 100})
    assert response.status_code == 200
    page = response.json()
    assert page["total"] >= 1 and page["offset"] == 0 and page["limit"] == 100
    assert all(item["status"] == "MANUAL_REVIEW" for item in page["items"])
    stamps = [(item["updated_at"], item["id"]) for item in page["items"]]
    assert stamps == sorted(stamps)
    item = next(item for item in page["items"] if item["id"] == rig.application["id"])
    assert item == {
        "id": rig.application["id"],
        "owner_id": rig.application["owner_id"],
        "company_name": rig.company["company"]["name"],
        "status": "MANUAL_REVIEW",
        "revision": rig.card["application"]["revision"],
        "updated_at": rig.card["application"]["updated_at"],
    }
    drafts = actors.bank.get(QUEUE, params={"status": "DRAFT", "limit": 1}).json()
    assert drafts["limit"] == 1 and len(drafts["items"]) <= 1
    assert all(i["status"] == "DRAFT" for i in drafts["items"])
    assert actors.bank.get(QUEUE, params={"offset": 10_000}).json()["items"] == []
    assert actors.first.get(QUEUE).status_code == 403
    assert actors.first.get(bank_path(rig.application)).status_code == 403
    assert actors.bank.get(f"{QUEUE}/{uuid4()}").status_code == 404

    card = rig.card
    assert card["allowed_actions"] == ["decide"]
    assert card["application"] == actors.first.get(path(rig.application)).json()
    [automatic] = card["decisions"]
    assert automatic["source"] == "automatic" and automatic["author_id"] is None
    assert automatic["policy_version"] and automatic["analytical_explanation"]
    assert automatic["review_reason"] == "gray_zone"

    async def saved_reason(db):
        row = await db.get(Decision, UUID(automatic["public"]["id"]))
        return row.risk_result["review_reason"]

    assert await in_rig(rig, saved_reason) == automatic["review_reason"]
    assert automatic["scoring_factors"] and all(
        set(f) == {"code", "value", "contribution", "explanation"}
        for f in automatic["scoring_factors"]
    )
    assert automatic["public"] == card["application"]["decision"]
    assert set(automatic["public"]) == PUBLIC_KEYS
    for public_payload in (card["application"], automatic["public"]):
        assert "review_reason" not in str(public_payload)
        assert "risk_result" not in str(public_payload)
    payload = body(rig, "REJECTED")
    second = headers(csrf(actors.second))
    assert decide(rig, payload, client=actors.first, h=actors.h).status_code == 403
    assert decide(rig, payload, client=actors.second, h=second).status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize("company_key", ["manual"])
@pytest.mark.parametrize("outcome", ["APPROVED", "COUNTER_OFFER", "NEEDS_INFO", "REJECTED"])
async def test_four_manual_outcomes_are_appended_audited_and_public(review, outcome):
    rig, actors = review, review.actors
    revision = rig.card["application"]["revision"]
    payload = body(rig, outcome)
    assert decide(rig, {**payload, "expected_revision": revision - 1}).status_code == 409

    response = decide(rig, payload)
    assert response.status_code == 200, response.text
    card = response.json()
    application = card["application"]
    assert application["status"] == outcome and application["revision"] == revision + 1
    assert card["allowed_actions"] == []
    automatic, manual = card["decisions"]
    assert automatic == rig.card["decisions"][0]
    assert automatic["review_reason"] == "gray_zone"
    assert manual["review_reason"] is None
    me = actors.bank.get("/api/auth/me").json()["id"]
    assert manual["source"] == "underwriter" and manual["author_id"] == me
    assert manual["policy_version"] is None and manual["scoring_factors"] == []
    assert manual["analytical_explanation"] == "Внутренний комментарий аналитика."
    public = manual["public"]
    assert public["outcome"] == outcome and public["explanation"] == payload["explanation"]
    assert public["reasons"] == payload["reasons"]
    assert public["required_documents"] == payload.get("required_documents", [])
    if outcome in {"APPROVED", "COUNTER_OFFER"}:
        assert public["offer"]["terms"] == payload["offer_terms"]
    else:
        assert "offer" not in public

    client_card = actors.first.get(path(rig.application)).json()
    assert client_card == application and client_card["decision"] == public
    assert "Внутренний" not in str(client_card) and me not in str(client_card)
    assert actors.bank.get(bank_path(rig.application)).json() == card
    assert decide(rig, {**payload, "expected_revision": revision + 1}).status_code == 409

    last = (await events(rig))[-1]
    assert (last.previous_status, last.status, last.actor_kind) == (
        "MANUAL_REVIEW",
        outcome,
        "underwriter",
    )
    assert str(last.actor_user_id) == me and last.public_message == payload["explanation"]
    assert last.audit_payload == {"decision_id": public["id"], "source": "underwriter"}

    async def stored(db):
        row = await db.get(Decision, UUID(public["id"]))
        offer = await db.scalar(select(Offer).where(Offer.decision_id == row.id))
        return row, offer

    row, offer = await in_rig(rig, stored)
    assert row.submission_id is None and row.risk_result is None
    assert row.revision == revision + 1 and row.input_revision == 2
    if offer is not None:
        assert offer.product_snapshot["code"] == "express"
        assert str(offer.annual_rate_percent) == "25.5"

    # Even a snapshot with a reason must not expose it for a non-review outcome.
    async def add_stale_reason(db):
        await db.execute(
            update(Decision)
            .where(Decision.id == row.id)
            .values(risk_result={"review_reason": "documents_review"})
        )

    await in_rig(rig, add_stale_reason)
    reread = actors.bank.get(bank_path(rig.application)).json()
    assert reread == card
    assert "review_reason" not in str(actors.first.get(path(rig.application)).json())
    assert "risk_result" not in str(actors.first.get(path(rig.application)).json())

    if outcome in {"APPROVED", "COUNTER_OFFER"}:
        accept = {"expected_revision": application["revision"], "offer_id": public["offer"]["id"]}
        accepted = post(actors, application, "accept-offer", accept).json()
        sign = {**accept, "expected_revision": accepted["revision"], "consent": True}
        assert post(actors, application, "sign", sign).json()["status"] == "ISSUED"


@pytest.mark.anyio
@pytest.mark.parametrize("company_key", ["manual"])
@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        pytest.param(None, None, id="legacy-null-snapshot"),
        pytest.param({}, None, id="legacy-missing-field"),
        pytest.param({"score": 175, "risk_class": "D"}, None, id="no-inference-from-score"),
        pytest.param([], None, id="snapshot-array"),
        pytest.param("gray_zone", None, id="snapshot-string"),
        pytest.param(123, None, id="snapshot-number"),
        pytest.param({"review_reason": None}, None, id="null-reason"),
        pytest.param({"review_reason": 123}, None, id="numeric-reason"),
        pytest.param({"review_reason": True}, None, id="boolean-reason"),
        pytest.param({"review_reason": []}, None, id="array-reason"),
        pytest.param({"review_reason": {}}, None, id="object-reason"),
        pytest.param({"review_reason": ""}, None, id="empty-reason"),
        pytest.param({"review_reason": " \t\n"}, None, id="blank-reason"),
        *[
            pytest.param({"review_reason": code}, code, id=code)
            for code in (
                "gray_zone",
                "collateral_review",
                "documents_review",
                "revenue_mismatch_review",
                "incomplete_checks",
                "future_review_code",
            )
        ],
    ],
)
async def test_review_reason_uses_saved_snapshot_safely(review, snapshot, expected):
    rig = review
    decision_id = UUID(rig.card["decisions"][0]["public"]["id"])

    async def replace_snapshot(db):
        await db.execute(
            update(Decision).where(Decision.id == decision_id).values(risk_result=snapshot)
        )

    await in_rig(rig, replace_snapshot)
    response = rig.actors.bank.get(bank_path(rig.application))
    assert response.status_code == 200, response.text
    [decision] = response.json()["decisions"]
    assert decision["review_reason"] == expected
    assert "risk_result" not in decision
    assert set(decision["public"]) == PUBLIC_KEYS
    public_card = rig.actors.first.get(path(rig.application))
    assert public_card.status_code == 200
    assert "review_reason" not in public_card.text and "risk_result" not in public_card.text

    async def stored_snapshot(db):
        return (await db.get(Decision, decision_id)).risk_result

    # Reading old snapshots never recalculates or repairs historical data.
    assert await in_rig(rig, stored_snapshot) == snapshot


@pytest.mark.anyio
@pytest.mark.parametrize("company_key", ["manual"])
async def test_offer_product_must_exist_and_be_active(review):
    rig = review
    revision = rig.card["application"]["revision"]
    unknown = body(rig, "APPROVED", offer_terms=terms(rig, product_id=str(uuid4())))
    response = decide(rig, unknown)
    assert response.status_code == 422 and response.json()["field_errors"]
    huge = body(rig, "APPROVED", offer_terms=terms(rig, term_months=2**31))
    assert decide(rig, huge).status_code == 422
    no_rate = body(rig, "COUNTER_OFFER", offer_terms=terms(rig, annual_rate_percent=None))

    express = UUID(no_rate["offer_terms"]["product_id"])

    async def toggle(active):
        async def run(db):
            await db.execute(update(Product).where(Product.id == express).values(active=active))

        return run

    await in_rig(rig, await toggle(False))
    try:
        assert decide(rig, no_rate).status_code == 422
    finally:
        await in_rig(rig, await toggle(True))
    card = rig.actors.bank.get(bank_path(rig.application)).json()
    assert card == rig.card and card["application"]["revision"] == revision

    response = decide(rig, no_rate)
    assert response.status_code == 200, response.text
    offer = response.json()["application"]["decision"]["offer"]
    assert offer["terms"]["annual_rate_percent"] is None
