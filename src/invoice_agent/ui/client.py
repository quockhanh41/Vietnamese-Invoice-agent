"""The UI's only route to the system: HTTP.

THIS MODULE MAY NOT IMPORT `invoice_agent.db`, `.graphs`, `.tools`, `.domain`,
`.llm`, `.memory` OR `.extraction`. `tests/test_ui_boundary.py` enforces it.
`invoice_agent.config` is the single allowed exception, for the base URL.

The rule is what makes the demo PROOF. Streamlit is Python, so importing
`submit_decision` directly would work and would be shorter — and M5-C's only
consumer would then bypass the API, leaving it dead code with a passing test
suite.

Every function takes an optional `client`, so tests drive the whole stack through
`TestClient(create_app())` in-process. Same shape as `serial_join(conn=...)`.
"""

import contextlib
from collections.abc import Iterator
from typing import Any

import httpx

from invoice_agent.config import get_settings

# Long enough for a live batch (20.4s measured at M5-B,
# docs/notes/2026-08-01-m5b-approval-graph.md), short enough that a dead API
# fails the page instead of hanging it.
TIMEOUT_S = 60.0


class ApiError(Exception):
    """A non-2xx from the API, carrying enough to render a Vietnamese message."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


@contextlib.contextmanager
def _session(client: httpx.Client | None) -> Iterator[httpx.Client]:
    """Borrow the caller's client, or own one for the duration.

    A BORROWED client is never closed — the caller owns it, and closing a
    `TestClient` mid-test breaks every later call.
    """
    if client is not None:
        yield client
        return
    with httpx.Client(base_url=get_settings().api_base_url, timeout=TIMEOUT_S) as owned:
        yield owned


def _detail(response: httpx.Response) -> str:
    """Flatten FastAPI's error body into one readable line.

    422 bodies are a LIST of `{"loc": [...], "msg": ...}` dicts. Rendering that
    raw in `st.error` is unreadable, so it is flattened HERE — at the boundary,
    where a test can reach it — rather than in `app.py`, where none can.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, list):
        return "; ".join(
            f"{'.'.join(str(p) for p in item.get('loc', []))}: {item.get('msg', '')}"
            for item in detail
            if isinstance(item, dict)
        )
    return str(detail)


def _unwrap(response: httpx.Response) -> Any:
    if not response.is_success:
        raise ApiError(response.status_code, _detail(response))
    return response.json()


def _get(path: str, *, client: httpx.Client | None, params: dict[str, Any] | None = None) -> Any:
    with _session(client) as session:
        return _unwrap(session.get(path, params=params))


def health(*, client: httpx.Client | None = None) -> dict[str, Any]:
    return _get("/health", client=client)


def pending_approvals(*, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = _get("/approvals", client=client)
    return result


def approval_detail(invoice_id: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    result: dict[str, Any] = _get(f"/approvals/{invoice_id}", client=client)
    return result


def get_invoice(invoice_id: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    result: dict[str, Any] = _get(f"/invoices/{invoice_id}", client=client)
    return result


def submit_decision(
    invoice_id: str,
    *,
    decision: str,
    decided_by: str,
    reason: str | None = None,
    corrections: dict[str, Any] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Resume a parked approval thread over HTTP.

    Sends ONLY the four fields `DecisionRequest` declares. `posting_authority`
    and friends are not omitted by politeness — the schema forbids extras, so a
    request carrying them is a 422 (M5-C spec §2.3). Nothing here needs to
    defend against that; the point is that this client has no way to express it
    either.
    """
    body: dict[str, Any] = {"decision": decision, "decided_by": decided_by}
    if reason is not None:
        body["reason"] = reason
    if corrections is not None:
        body["corrections"] = corrections
    with _session(client) as session:
        result: dict[str, Any] = _unwrap(
            session.post(f"/approvals/{invoice_id}/decision", json=body)
        )
    return result

