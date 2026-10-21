"""Synthetic contract examples only. Never returned by runtime stubs."""

APPLICATION_ID = "10000000-0000-4000-8000-000000000001"
USER_ID = "20000000-0000-4000-8000-000000000001"
PRODUCT_ID = "30000000-0000-4000-8000-000000000001"
OFFER_ID = "40000000-0000-4000-8000-000000000001"
DECISION_ID = "50000000-0000-4000-8000-000000000001"
DOCUMENT_ID = "60000000-0000-4000-8000-000000000001"
EVENT_ID = "70000000-0000-4000-8000-000000000001"
NOW = "2026-10-20T18:00:00Z"
USER = {"id": USER_ID, "login": "demo-client", "display_name": "Демо-клиент", "role": "client"}
COMPANY = {"inn": "7701234560", "name": "ООО «Синтетический пример»", "legal_form": "OOO"}
PRODUCT = {
    "id": PRODUCT_ID,
    "code": "demo-product",
    "name": "Демонстрационный продукт",
    "description": "Синтетический пример без кредитной политики и ставок",
    "purposes": ["Развитие бизнеса"],
}
FORM = {
    "company": COMPANY,
    "product_id": PRODUCT_ID,
    "amount": "100000.00",
    "term_months": 12,
    "purpose": "Развитие бизнеса",
    "annual_revenue": "1000000.00",
}
TERMS = {
    "product_id": PRODUCT_ID,
    "amount": "100000.00",
    "term_months": 12,
    "annual_rate_percent": None,
}
OFFER = {"id": OFFER_ID, "terms": TERMS, "created_at": NOW, "expires_at": None}
REASON = {"code": "DEMO_REASON", "message": "Синтетическая причина, не кредитное правило"}
REQUIRED_DOCUMENT = {
    "code": "DEMO_DOCUMENT",
    "title": "Демонстрационный документ",
    "explanation": "Пример запроса сведений; состав согласуется с рисками",
}
DECISION_BASE = {
    "id": DECISION_ID,
    "created_at": NOW,
    "reasons": [REASON],
    "explanation": "Синтетическое объяснение результата обработки",
    "required_documents": [],
}
DECISIONS = {
    "approved": {**DECISION_BASE, "outcome": "APPROVED", "offer": OFFER},
    "counter_offer": {**DECISION_BASE, "outcome": "COUNTER_OFFER", "offer": OFFER},
    "needs_info": {
        **DECISION_BASE,
        "outcome": "NEEDS_INFO",
        "required_documents": [REQUIRED_DOCUMENT],
    },
    "manual_review": {**DECISION_BASE, "outcome": "MANUAL_REVIEW"},
    "rejected": {**DECISION_BASE, "outcome": "REJECTED"},
}
SUMMARY = {
    "id": APPLICATION_ID,
    "company_name": COMPANY["name"],
    "product_id": PRODUCT_ID,
    "amount": "100000.00",
    "term_months": 12,
    "status": "DRAFT",
    "revision": 1,
    "allowed_actions": ["edit", "submit", "cancel", "upload_document"],
    "created_at": NOW,
    "updated_at": NOW,
}
APPLICATION = {
    **SUMMARY,
    "owner_id": USER_ID,
    "form": FORM,
    "decision": None,
    "accepted_offer_id": None,
}
APPLICATION_EXAMPLES = {"draft": APPLICATION}
for name, decision in DECISIONS.items():
    actions = {
        "APPROVED": ["accept_offer", "cancel"],
        "COUNTER_OFFER": ["accept_offer", "cancel"],
        "NEEDS_INFO": ["provide_info", "upload_document", "cancel"],
        "MANUAL_REVIEW": ["cancel"],
        "REJECTED": [],
    }[decision["outcome"]]
    APPLICATION_EXAMPLES[name] = {
        **APPLICATION,
        "revision": 5,
        "status": decision["outcome"],
        "allowed_actions": actions,
        "decision": decision,
    }
DOCUMENT = {
    "id": DOCUMENT_ID,
    "application_id": APPLICATION_ID,
    "uploaded_by": USER_ID,
    "document_type": "DEMO_DOCUMENT",
    "filename": "demo.txt",
    "media_type": "text/plain",
    "size_bytes": 4,
    "created_at": NOW,
}
EVENT = {
    "id": EVENT_ID,
    "application_id": APPLICATION_ID,
    "revision": 2,
    "event_type": "status_changed",
    "previous_status": "DRAFT",
    "status": "SUBMITTED",
    "occurred_at": NOW,
    "message": "Заявка отправлена",
}
SSE_DATA = {
    key: EVENT[key]
    for key in (
        "event_type",
        "application_id",
        "revision",
        "previous_status",
        "status",
        "occurred_at",
    )
}
SSE_FRAME = {"id": EVENT_ID, "event": "status_changed", "data": SSE_DATA}
QUEUE_ITEM = {
    "id": APPLICATION_ID,
    "owner_id": USER_ID,
    "company_name": COMPANY["name"],
    "status": "MANUAL_REVIEW",
    "revision": 5,
    "updated_at": NOW,
}
BANK_APPLICATION = {
    "application": APPLICATION_EXAMPLES["manual_review"],
    "decisions": [
        {
            "public": DECISIONS["manual_review"],
            "source": "automatic",
            "author_id": None,
            "policy_version": None,
            "scoring_factors": [],
            "analytical_explanation": "Демонстрационное внутреннее объяснение, шкала не определена",
        }
    ],
    "allowed_actions": ["decide"],
}
REVISION = {"expected_revision": 1}
LOGIN = {"login": "demo-client", "password": "synthetic-example-only"}
MANUAL_REQUESTS = {
    "approved": {"outcome": "APPROVED", "offer_terms": TERMS},
    "counter_offer": {"outcome": "COUNTER_OFFER", "offer_terms": TERMS},
    "needs_info": {"outcome": "NEEDS_INFO", "required_documents": [REQUIRED_DOCUMENT]},
    "rejected": {"outcome": "REJECTED"},
}
MANUAL_REQUESTS = {
    name: {
        "expected_revision": 5,
        **body,
        "reasons": [REASON],
        "explanation": "Публичное объяснение",
        "analytical_explanation": "Внутреннее объяснение",
    }
    for name, body in MANUAL_REQUESTS.items()
}


def examples(values: dict) -> dict:
    return {
        name: {"summary": f"Синтетический пример: {name}", "value": value}
        for name, value in values.items()
    }


def page(item: dict) -> dict:
    return {"items": [item], "total": 1, "offset": 0, "limit": 20}
