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

from pydantic import BaseModel, ConfigDict, Field


class ClassificationResult(BaseModel):
    """What the cheap tier proposes. An opinion, not a decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str
    department: str
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
    proposed_category: str | None = None
    proposed_department: str | None = None
    reason: str
