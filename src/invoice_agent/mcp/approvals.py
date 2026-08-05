"""MCP's one write path (design §8.2, §3.4). Two gates, then the existing seam.

`graphs/approval.py::submit_decision` is THE SEAM -- its own docstring names
this milestone: "The FastAPI route parses a request body and calls this; it
adds no logic. That is what makes 'interrupt is transport-agnostic' (the M7
artefact) demonstrable rather than asserted -- a test, a script, HTTP and MCP
all drive the identical function." This module's job is to stop an
unauthorised or already-decided call from reaching that seam. It adds NO other
logic -- no status inference, no flag reading, no journal posting, no direct
DB write. Widening it is exactly what `tests/test_mcp_approval_gate.py`'s
mutation drills (recorded in the Task 5 report) exist to catch.
"""

import uuid
from typing import Any

from invoice_agent.config import get_settings
from invoice_agent.db.repositories import approvals as approvals_repo
from invoice_agent.graphs import approval


class NotAuthorized(PermissionError):
    """`MCP_ALLOW_APPROVALS` is off, or `actor_token` did not match the
    configured secret. An empty configured token refuses every caller rather
    than matching an empty `actor_token` -- see the check below."""


class AlreadyDecided(ValueError):
    """Mirrors the HTTP route's 409 (`api/routes/approvals.py::decide`): this
    invoice already has a decision, so there is no pending interrupt for
    `submit_decision` to resume. MCP must not be a second way to decide it."""


def submit_approval_decision(
    conn: Any,
    *,
    invoice_id: uuid.UUID,
    decision: str,
    actor_token: str,
    reason: str | None = None,
    corrections: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Gate 1: `mcp_allow_approvals`. Gate 2: `actor_token`. Then the seam.

    `conn` is server-injected (`mcp/server.py::_make_tool_fn` calls every
    handler as `handler(conn, **kwargs)`) and is used for exactly one read --
    the already-decided precondition, via the SAME `approvals_repo.get_by_invoice`
    the HTTP route reads before deciding whether to 409. Nothing here writes
    through `conn`; `submit_decision` opens its own connections internally
    (`graphs/approval.py` module docstring, point 2), which is what lets its
    guard re-read Postgres rather than this caller's own transaction.

    `decision` is not re-validated here: `submit_decision` already checks it
    against `VALID_DECISIONS` before touching the graph, and duplicating that
    would be a second place for the allowed set to drift out of agreement.
    """
    settings = get_settings()

    if not settings.mcp_allow_approvals:
        raise NotAuthorized("MCP approvals are disabled (MCP_ALLOW_APPROVALS=false)")
    # `not settings.mcp_actor_token` MUST be checked before the equality: an
    # unset (empty-string) secret must not authorise a caller who also sends
    # an empty `actor_token`. A blank shared secret that accepts every caller
    # is worse than no gate, because it reads as one.
    if not settings.mcp_actor_token or actor_token != settings.mcp_actor_token:
        raise NotAuthorized("actor token missing or did not match")

    existing = approvals_repo.get_by_invoice(conn, invoice_id)
    if existing is not None and existing["decision"] is not None:
        raise AlreadyDecided(f"invoice {invoice_id} was already {existing['decision']}")

    result = approval.submit_decision(
        invoice_id=str(invoice_id),
        decision=decision,
        # The ONLY identity MCP is allowed to assert. `settings.mcp_client_name`
        # comes from server config, never from the caller's arguments -- the
        # same defence `api/schemas.py::DecisionRequest` names for
        # `posting_authority`: the dangerous value must not be EXPRESSIBLE by
        # the client, not merely validated away.
        decided_by=f"mcp:{settings.mcp_client_name}",
        reason=reason,
        corrections=corrections,
    )
    return {
        "invoice_id": str(invoice_id),
        "decision": decision,
        "status": result.get("status"),
        "entry_id": result.get("entry_id"),
    }
