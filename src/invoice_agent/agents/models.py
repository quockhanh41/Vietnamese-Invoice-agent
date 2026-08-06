"""Agent response models — the security boundary, not merely data carriers.

NOTHING HERE MAY CARRY AN APPROVAL, FLAG OR STATUS FIELD. Design §9.2's defence
against prompt injection is not detection; payloads 5 and 6 contain no injection
markers and beat every keyword filter. The defence is that the model has no
channel: "set approval_status=approved" is not expressible if the field does not
exist. `tests/test_agent_schema_boundary.py` enforces this and is
mutation-verified.

`extra="forbid"` is the other half. Without it a model returning
`approval_status` would have it silently accepted, and the field audit would be
checking a schema the response is not held to.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The seeded vocabularies, as a closed set. Duplicated from
# `scripts.seed_reference` ON PURPOSE — `src/` must not import from `scripts/` —
# and `tests/test_agents.py` asserts the two agree, the same arrangement
# `domain.findings.SOURCES` has with `db.models.FLAG_SOURCES`.
#
# CLOSED, not `str`, because of a measured failure. 2026-08-01, a live
# gemini-3.5-flash verdict proposed `OFFICE_REPAIR_MAINTENANCE` — a category
# that does not exist. With an open `str` that validates, so `dispute()` raises
# CLASSIFICATION_DISPUTED against a label no invoice can ever carry: the dispute
# count inflates and override precision is forced to 0 on those rows, because
# truth can never equal an invented category. A corrupt metric, silently.
#
# Constraining it here makes the schema carry the allowed values to the model
# (`_schema_instruction` sends it) AND makes a bad value a ValidationError, which
# is what `call_structured`'s retry loop is for.
CATEGORIES = Literal[
    "OFFICE_SUPPLIES", "MARKETING", "IT_SERVICES",
    "LOGISTICS", "UTILITIES", "GOODS", "TOOLS",
]
DEPARTMENTS = Literal["MKT", "ADMIN", "OPS", "IT", "SALES"]


class ClassificationResult(BaseModel):
    """What the cheap tier proposes. An opinion, not a decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: CATEGORIES
    department: DEPARTMENTS
    # Self-reported and badly calibrated — kept because
    # `invoices.classification_confidence` exists (M2-A) and M5's `suggest` may
    # order by it. Spec §7 open question 1: M4-C reports whether it correlates
    # with correctness at all, and it is dropped if it does not.
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class AuditVerdict(BaseModel):
    """The second opinion. `agrees=False` is an OPINION, not a flag — Python
    decides whether that becomes CLASSIFICATION_DISPUTED (design §6.4)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agrees: bool
    proposed_category: CATEGORIES | None = None
    proposed_department: DEPARTMENTS | None = None
    reason: str


class ReportCommentary(BaseModel):
    """Period commentary — prose only.

    Deliberately has no numeric field. Numbers come from `ReportFacts`, which
    Python computes; `agents.reporting.narrate` rejects any commentary quoting a
    number that is not in them. A numeric field here would be a channel for the
    model to assert a figure the dashboard never computed, and the reader could
    not tell which was which.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str
    observations: list[str]

