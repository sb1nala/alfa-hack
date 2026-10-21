import json
from datetime import date
from pathlib import Path
from typing import Any

from app.decision import ApplicationInput, BkiReport, EgrulReport, PbReport

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_json(relative: str) -> dict[str, Any]:
    return json.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))


def seed_companies() -> dict[str, Any]:
    return load_json("seed/companies.json")


def build_inputs(
    company: dict[str, Any],
    submitted_on: date,
    application_changes: dict[str, Any] | None = None,
    document_types: tuple[str, ...] = (),
) -> tuple[ApplicationInput, BkiReport, EgrulReport, PbReport]:
    application = {
        **company["application"],
        **(application_changes or {}),
        "legal_form": company["company"]["legal_form"],
        "submitted_on": submitted_on,
        "document_types": document_types,
    }
    mocks = company["mocks"]
    return (
        ApplicationInput.from_dict(application),
        BkiReport.from_dict(mocks["bki"]),
        EgrulReport.from_dict(mocks["egrul"]),
        PbReport.from_dict(mocks["pb"]),
    )
