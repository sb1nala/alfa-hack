"""Pipeline stats for the analyst: every figure recomputed independently from raw rows."""

from collections import Counter
from decimal import Decimal

import pytest
from sqlalchemy import select
from test_client_actions_postgres import card, drain, in_rig, post
from test_decision_worker_postgres import company_key as company_key
from test_decision_worker_postgres import queued as queued
from test_decision_worker_postgres import ready as ready
from test_drafts_postgres import actors as actors
from test_drafts_postgres import demo_hash as demo_hash
from test_drafts_postgres import draft_settings as draft_settings
from test_worker_postgres import rig as rig

from app.models import Application, ApplicationSubmission, Decision, Offer
from app.schemas.common import ApplicationStatus

STATS = "/api/underwriter/stats"


def percentile(values, share):
    """PostgreSQL percentile_cont: linear interpolation between neighbours."""
    if not values:
        return None
    values = sorted(values)
    position = share * (len(values) - 1)
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


async def expected(rig):
    async def read(db):
        apps = list(await db.scalars(select(Application)))
        decisions = list(await db.scalars(select(Decision)))
        submissions = list(await db.scalars(select(ApplicationSubmission)))
        offers = {offer.id: offer for offer in await db.scalars(select(Offer))}
        return apps, decisions, submissions, offers

    apps, decisions, submissions, offers = await in_rig(rig, read)
    first = {}
    for row in sorted(decisions, key=lambda d: (d.created_at, d.revision)):
        first.setdefault(row.application_id, row)
    submitted = {}
    for row in submissions:
        current = submitted.get(row.application_id)
        submitted[row.application_id] = (
            min(current, row.submitted_at) if current else row.submitted_at
        )
    seconds = [
        (row.created_at - submitted[app_id]).total_seconds()
        for app_id, row in first.items()
        if app_id in submitted
    ]
    statuses = Counter(app.status for app in apps if app.status != "DRAFT")
    issued = [app for app in apps if app.status == "ISSUED" and app.accepted_offer_id]
    return {
        "statuses": statuses,
        "decided": sum(1 for app_id in first if app_id in submitted),
        "automated": sum(
            1
            for app_id, row in first.items()
            if app_id in submitted and row.source == "automatic" and row.outcome != "MANUAL_REVIEW"
        ),
        "median": percentile(seconds, 0.5),
        "p90": percentile(seconds, 0.9),
        "issued_count": len(issued),
        "issued_amount": sum((offers[app.accepted_offer_id].amount for app in issued), Decimal(0)),
        "manual": statuses["MANUAL_REVIEW"],
    }


@pytest.mark.anyio
@pytest.mark.parametrize("company_key", ["approve"])
async def test_stats_match_raw_rows_and_follow_an_issued_loan(ready):
    rig, actors = ready, ready.actors
    before = actors.bank.get(STATS).json()
    await drain(rig)
    decided = card(actors, rig.application)
    offer = decided["decision"]["offer"]
    accepted = post(
        actors,
        decided,
        "accept-offer",
        {"expected_revision": decided["revision"], "offer_id": offer["id"]},
    ).json()
    sign = {"expected_revision": accepted["revision"], "offer_id": offer["id"], "consent": True}
    assert post(actors, decided, "sign", sign).json()["status"] == "ISSUED"

    response = actors.bank.get(STATS)
    assert response.status_code == 200, response.text
    stats = response.json()
    want = await expected(rig)

    assert [item["status"] for item in stats["by_status"]] == [
        status.value for status in ApplicationStatus if status.value != "DRAFT"
    ]
    assert {item["status"]: item["count"] for item in stats["by_status"]} == {
        status.value: want["statuses"].get(status.value, 0)
        for status in ApplicationStatus
        if status.value != "DRAFT"
    }
    terminal = {"REJECTED", "ISSUED", "CANCELLED"}
    assert stats["in_progress"] == sum(
        count for status, count in want["statuses"].items() if status not in terminal
    )
    assert (stats["decided"], stats["automated"]) == (want["decided"], want["automated"])
    assert stats["decision_time"]["median_seconds"] == pytest.approx(want["median"], abs=0.06)
    assert stats["decision_time"]["p90_seconds"] == pytest.approx(want["p90"], abs=0.06)
    assert stats["issued"]["count"] == want["issued_count"]
    assert Decimal(stats["issued"]["amount"]) == want["issued_amount"]
    assert stats["manual_queue"]["count"] == want["manual"]
    if want["manual"]:
        assert stats["manual_queue"]["oldest_waiting_seconds"] >= 0
    else:
        assert stats["manual_queue"]["oldest_waiting_seconds"] is None

    # The loan driven by this test moved the figures in the expected direction.
    assert stats["issued"]["count"] == before["issued"]["count"] + 1
    assert Decimal(stats["issued"]["amount"]) - Decimal(before["issued"]["amount"]) == Decimal(
        offer["terms"]["amount"]
    )
    assert stats["decided"] == before["decided"] + 1
    assert stats["automated"] == before["automated"] + 1
    assert stats["generated_at"].endswith("Z")


def test_stats_are_bank_only_and_contain_no_client_data(actors):
    assert actors.first.get(STATS).status_code == 403
    response = actors.bank.get(STATS)
    assert response.status_code == 200
    assert set(response.json()) == {
        "generated_at",
        "by_status",
        "in_progress",
        "manual_queue",
        "decided",
        "automated",
        "decision_time",
        "issued",
    }
    for forbidden in ("inn", "company", "owner", "login"):
        assert forbidden not in response.text
