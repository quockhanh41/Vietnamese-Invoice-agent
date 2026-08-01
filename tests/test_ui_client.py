"""The UI's HTTP client, driven through the real app in-process.

`TestClient` IS an `httpx.Client` over an ASGI transport (verified 2026-08-01:
`issubclass(TestClient, httpx.Client)` is True), so every test here exercises the
whole request/response path — routing, validation, serialisation — with no
uvicorn and no port.
"""

import pytest
from fastapi.testclient import TestClient

# `committed_rows` is a FIXTURE and arrives via conftest's pytest_plugins, but
# `_persisted_invoice` and `_initial_state` are plain functions — they must be
# imported. Verified 2026-08-01: tests/test_api_read.py imports them the same way.
from tests.helpers.approval_fixtures import _initial_state, _persisted_invoice

from invoice_agent.api.app import create_app
from invoice_agent.ui import client as ui


@pytest.fixture
def api():
    with TestClient(create_app()) as c:
        yield c


def test_health_round_trips(api):
    assert ui.health(client=api) == {"status": "ok", "database": "ok"}


def test_a_non_2xx_raises_ApiError_carrying_the_status(api):
    """A UI that renders success on a failed write is the same class of defect as
    a guard swallowing its own denial (spec §2.3). Nothing may return quietly."""
    with pytest.raises(ui.ApiError) as excinfo:
        ui.get_invoice("00000000-0000-0000-0000-000000000000", client=api)
    assert excinfo.value.status_code == 404
    assert "not found" in excinfo.value.detail


def test_a_validation_error_becomes_readable_text(api, committed_rows):
    """FastAPI returns 422 with a LIST of error dicts. Rendering that list raw in
    a Streamlit `st.error` is unreadable, so the client flattens it here — at the
    boundary — rather than leaving app.py to do it untestably."""
    db_id, _ = _persisted_invoice(committed_rows)
    with pytest.raises(ui.ApiError) as excinfo:
        ui.submit_decision(
            str(db_id), decision="aproved", decided_by="ketoan", client=api
        )
    assert excinfo.value.status_code == 422
    assert isinstance(excinfo.value.detail, str)
    assert "decision" in excinfo.value.detail


def test_a_borrowed_client_is_not_closed(api):
    """The caller owns an injected client. Closing it would break the second call
    in any test — and, in `app.py`, a reused session."""
    ui.health(client=api)
    ui.health(client=api)
    assert not api.is_closed


def _park(committed_rows):
    """Persist an invoice and drive its approval thread to the interrupt."""
    from invoice_agent.checkpointer import build_checkpointer
    from invoice_agent.graphs import approval

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    with build_checkpointer() as saver:
        saver.setup()
        approval.build_approval_graph(checkpointer=saver).invoke(
            _initial_state(db_id, approval_id),
            {"configurable": {"thread_id": f"appr:{db_id}"}},
        )
    return db_id


def test_the_queue_and_the_detail_a_reviewer_reads(api, committed_rows):
    db_id = _park(committed_rows)

    queue = ui.pending_approvals(client=api)
    assert str(db_id) in [row["invoice_id"] for row in queue]

    detail = ui.approval_detail(str(db_id), client=api)
    assert detail["decision"] is None
    payload = detail["interrupt_payload"]
    assert payload["invoice"]["invoice_number"]
    # Vietnamese narratives arrive untranslated — an accountant reads them.
    assert payload["flags"][0]["code"] == "PRICE_OUTLIER"


def test_a_decision_through_the_client_posts_exactly_one_entry(api, committed_rows):
    """THE GATE. The UI drives the same `submit_decision` a test, a script and
    curl drive — which is what makes the demo proof rather than decoration."""
    from sqlalchemy import select

    from invoice_agent.db import models
    from invoice_agent.db.engine import get_engine

    db_id = _park(committed_rows)
    result = ui.submit_decision(
        str(db_id), decision="approved", decided_by="ketoan", reason="đã kiểm tra",
        client=api,
    )
    assert result["decision"] == "approved"
    assert result["entry_id"]

    with get_engine().begin() as conn:
        entries = list(
            conn.execute(
                select(models.journal_entries).where(
                    models.journal_entries.c.invoice_id == db_id
                )
            ).mappings()
        )
    assert len(entries) == 1
    assert entries[0]["posting_authority"] == "human_approved"


def test_a_409_becomes_an_ApiError_not_a_silent_success(api, committed_rows):
    """THE MUTATION THAT MATTERS (spec §2.3). A client that returned the error
    body would make the UI render "đã duyệt" while NOTHING was posted, and no
    screenshot would reveal it."""
    db_id = _park(committed_rows)
    ui.submit_decision(str(db_id), decision="approved", decided_by="ketoan", client=api)

    with pytest.raises(ui.ApiError) as excinfo:
        ui.submit_decision(
            str(db_id), decision="approved", decided_by="ketoan", client=api
        )
    assert excinfo.value.status_code == 409
    assert "already" in excinfo.value.detail.lower()


def test_get_invoice_returns_lines_and_flags(api, committed_rows):
    db_id = _park(committed_rows)
    body = ui.get_invoice(str(db_id), client=api)
    assert body["invoice"]["id"] == str(db_id)
    assert body["lines"]
    assert [f["code"] for f in body["flags"]] == ["PRICE_OUTLIER"]
