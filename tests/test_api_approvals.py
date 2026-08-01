"""The gate path over HTTP, and the schema that is its security boundary."""

import uuid

import pytest
from fastapi.testclient import TestClient
from tests.helpers.approval_fixtures import _initial_state, _persisted_invoice

from invoice_agent.api.app import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


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
    return db_id, approval_id


def test_the_queue_lists_a_parked_invoice(client, committed_rows):
    db_id, _ = _park(committed_rows)
    body = client.get("/approvals").json()
    assert str(db_id) in [r["invoice_id"] for r in body]


def test_the_payload_a_reviewer_reads(client, committed_rows):
    db_id, _ = _park(committed_rows)
    body = client.get(f"/approvals/{db_id}").json()
    assert body["interrupt_payload"]["invoice"]["invoice_number"]
    assert body["interrupt_payload"]["flags"][0]["code"] == "PRICE_OUTLIER"
    assert body["decision"] is None


def test_approving_over_http_posts_exactly_one_entry(client, committed_rows):
    """THE GATE PATH. The route calls `submit_decision` and adds nothing — the
    same function a test, a script and later MCP drive."""
    from sqlalchemy import select

    from invoice_agent.db import models
    from invoice_agent.db.engine import get_engine

    db_id, approval_id = _park(committed_rows)
    response = client.post(
        f"/approvals/{db_id}/decision",
        json={"decision": "approved", "decided_by": "ketoan", "reason": "ok"},
    )
    assert response.status_code == 200

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
    assert entries[0]["approval_id"] == approval_id


def test_a_client_cannot_set_posting_authority(client, committed_rows):
    """THE SECURITY BOUNDARY (spec §2.3). Design §9.2's argument, transposed from
    models to clients: the defence is not validation, it is that
    "post this as auto_clean" IS NOT EXPRESSIBLE. `extra="forbid"` is the half
    that makes an unknown field a 422 instead of a silent no-op."""
    db_id, _ = _park(committed_rows)
    response = client.post(
        f"/approvals/{db_id}/decision",
        json={
            "decision": "approved",
            "decided_by": "attacker",
            "posting_authority": "auto_clean",
        },
    )
    assert response.status_code == 422


def test_an_unknown_decision_is_422(client, committed_rows):
    db_id, _ = _park(committed_rows)
    response = client.post(
        f"/approvals/{db_id}/decision",
        json={"decision": "aproved", "decided_by": "ketoan"},
    )
    assert response.status_code == 422


def test_deciding_twice_is_409_not_a_second_entry(client, committed_rows):
    """The second POST finds no pending interrupt. It must not be a 500, and it
    must not post again."""
    db_id, _ = _park(committed_rows)
    first = client.post(
        f"/approvals/{db_id}/decision",
        json={"decision": "approved", "decided_by": "ketoan"},
    )
    assert first.status_code == 200
    second = client.post(
        f"/approvals/{db_id}/decision",
        json={"decision": "approved", "decided_by": "ketoan"},
    )
    assert second.status_code == 409


def test_deciding_an_unknown_invoice_is_404(client):
    response = client.post(
        f"/approvals/{uuid.uuid4()}/decision",
        json={"decision": "approved", "decided_by": "ketoan"},
    )
    assert response.status_code == 404
