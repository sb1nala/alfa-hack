"""Credit decision (owner: Risk). Policy: docs/credit-policy.md.

Usage::

    decision = decide(application, bki, egrul, pb)

Inputs are built with ``ApplicationInput.from_dict``/``BkiReport.from_dict``/... from mock
responses; ``Decision`` has a public part for the client and a bank-only part.
"""

from app.decision.engine import decide
from app.decision.policy import POLICY_VERSION
from app.decision.types import (
    ApplicationInput,
    BkiLoan,
    BkiReport,
    Decision,
    EgrulReport,
    OfferTerms,
    PbReport,
    Reason,
    RequiredDocument,
    ScoringFactor,
)

__all__ = [
    "POLICY_VERSION",
    "ApplicationInput",
    "BkiLoan",
    "BkiReport",
    "Decision",
    "EgrulReport",
    "OfferTerms",
    "PbReport",
    "Reason",
    "RequiredDocument",
    "ScoringFactor",
    "decide",
]
