"""Request models — the security boundary at the HTTP edge.

Design §9.2 makes this argument for agent response models against prompt
injection; it transposes directly to clients. The defence is not validation, it
is that the dangerous instruction IS NOT EXPRESSIBLE: `DecisionRequest` has no
`posting_authority`, no `approval_id`, no `status` and no `entry_id`, so no
request body can carry them.

`extra="forbid"` is the other half. Without it an unknown field is silently
ignored, and an audit of the field list would be checking a schema the request
is not actually held to.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected", "edited"]
    # BARE name. `approvals.decided_by` stores "ketoan"; `audit_log.actor` gets
    # the `human:` prefix, and `post_entry` is the only place it is added.
    #
    # NO AUTH, deliberately: design §11.3 cut it, and §4 defines the reviewer as
    # a string in `approvals.decided_by`. This records what the client CLAIMS. A
    # real deployment would take it from a session.
    decided_by: str = Field(min_length=1)
    reason: str | None = None
    corrections: dict[str, Any] | None = None
