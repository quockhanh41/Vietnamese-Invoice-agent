"""Task 5: the one write MCP is allowed, gated (design §3.4, §8.2).

Mirrors `test_api_approvals.py`'s own gate-path test: `submit_approval_decision`
drives the SAME `graphs.approval.submit_decision` seam the HTTP route uses, so
these tests park an invoice the same way (`_park`, copied rather than shared --
`test_api_approvals.py` makes the same choice) and assert against the same
`approvals` row the HTTP tests read.

Every refusal test asserts the ROW, not just the exception: an error return
with a written row is the exact failure these tests exist to catch (Task 5
brief, step 1).
"""

import uuid

import pytest
from tests.helpers.approval_fixtures import _initial_state, _persisted_invoice

from invoice_agent.config import Settings
from invoice_agent.db.engine import get_engine
from invoice_agent.db.repositories import approvals as approvals_repo
from invoice_agent.mcp import approvals as mcp_approvals


def _park(committed_rows):
    """Persist an invoice and drive its approval thread to the interrupt."""
    from invoice_agent.checkpointer import build_checkpointer
    from invoice_agent.graphs import approval

    db_id, approval_id = _persisted_invoice(committed_rows)
    with build_checkpointer() as saver:
        saver.setup()
        approval.build_approval_graph(checkpointer=saver).invoke(
            _initial_state(db_id, approval_id),
            {"configurable": {"thread_id": f"appr:{db_id}"}},
        )
    return db_id, approval_id


def _settings_with(**kw):
    return Settings(_env_file=None, **kw)


def _call(conn, invoice_id, *, decision="rejected", actor_token="", **kw):
    return mcp_approvals.submit_approval_decision(
        conn, invoice_id=invoice_id, decision=decision, actor_token=actor_token, **kw
    )


def _decision_row(invoice_id):
    with get_engine().begin() as conn:
        return approvals_repo.get_by_invoice(conn, invoice_id)


# ---------------------------------------------------------------------------
# 1. mcp_allow_approvals=False refuses -- and writes nothing.
# ---------------------------------------------------------------------------


def test_disabled_refuses_and_records_no_decision(requires_postgres, committed_rows, monkeypatch):
    db_id, _ = _park(committed_rows)
    monkeypatch.setattr(
        mcp_approvals,
        "get_settings",
        lambda: _settings_with(mcp_allow_approvals=False, mcp_actor_token="secret"),
    )

    with pytest.raises(mcp_approvals.NotAuthorized), get_engine().begin() as conn:
        _call(conn, db_id, actor_token="secret")

    assert _decision_row(db_id)["decision"] is None


# ---------------------------------------------------------------------------
# 2. flag true, wrong token -- refuses, and writes nothing.
# ---------------------------------------------------------------------------


def test_wrong_token_refuses_and_records_no_decision(
    requires_postgres, committed_rows, monkeypatch
):
    db_id, _ = _park(committed_rows)
    monkeypatch.setattr(
        mcp_approvals,
        "get_settings",
        lambda: _settings_with(mcp_allow_approvals=True, mcp_actor_token="secret"),
    )

    with pytest.raises(mcp_approvals.NotAuthorized), get_engine().begin() as conn:
        _call(conn, db_id, actor_token="WRONG")

    assert _decision_row(db_id)["decision"] is None


# ---------------------------------------------------------------------------
# 3. both correct: decision recorded, and the audit row's actor is mcp:<client>.
# ---------------------------------------------------------------------------


def test_correct_gate_records_decision_with_mcp_actor(
    requires_postgres, committed_rows, monkeypatch
):
    db_id, _ = _park(committed_rows)
    monkeypatch.setattr(
        mcp_approvals,
        "get_settings",
        lambda: _settings_with(
            mcp_allow_approvals=True, mcp_actor_token="secret", mcp_client_name="claude-desktop"
        ),
    )

    with get_engine().begin() as conn:
        result = _call(conn, db_id, decision="rejected", actor_token="secret")
    assert result["decision"] == "rejected"

    row = _decision_row(db_id)
    assert row["decision"] == "rejected"
    # THE AUDIT ROW: `approvals.decided_by` records who decided, verbatim --
    # the same column the HTTP path writes with a bare reviewer name (e.g.
    # "ketoan"). A decision made from Claude Desktop must be distinguishable
    # from one made in the UI, forever.
    assert row["decided_by"] == "mcp:claude-desktop"


# ---------------------------------------------------------------------------
# 4. calls graphs.approval.submit_decision -- not a direct DB write.
# ---------------------------------------------------------------------------


def test_calls_the_seam_not_the_database_directly(
    requires_postgres, committed_rows, monkeypatch
):
    db_id, _ = _park(committed_rows)
    monkeypatch.setattr(
        mcp_approvals,
        "get_settings",
        lambda: _settings_with(
            mcp_allow_approvals=True, mcp_actor_token="secret", mcp_client_name="claude-desktop"
        ),
    )

    from invoice_agent.graphs import approval

    calls: list[dict] = []

    def _fake_submit_decision(**kwargs):
        calls.append(kwargs)
        return {"status": "decided", "entry_id": None}

    monkeypatch.setattr(approval, "submit_decision", _fake_submit_decision)

    with get_engine().begin() as conn:
        _call(conn, db_id, decision="approved", actor_token="secret", reason="ok")

    assert len(calls) == 1
    assert calls[0] == {
        "invoice_id": str(db_id),
        "decision": "approved",
        "decided_by": "mcp:claude-desktop",
        "reason": "ok",
        "corrections": None,
    }
    # The fake never touched the database -- if a future implementation wrote
    # the row directly instead of calling the seam, this would still pass
    # spuriously without the assertion below.
    assert _decision_row(db_id)["decision"] is None


# ---------------------------------------------------------------------------
# 5. an already-decided invoice is refused (HTTP's 409; MCP must not be a way
#    around it).
# ---------------------------------------------------------------------------


def test_already_decided_invoice_is_refused(requires_postgres, committed_rows, monkeypatch):
    db_id, _ = _park(committed_rows)
    monkeypatch.setattr(
        mcp_approvals,
        "get_settings",
        lambda: _settings_with(
            mcp_allow_approvals=True, mcp_actor_token="secret", mcp_client_name="claude-desktop"
        ),
    )

    with get_engine().begin() as conn:
        _call(conn, db_id, decision="rejected", actor_token="secret")

    with pytest.raises(mcp_approvals.AlreadyDecided), get_engine().begin() as conn:
        _call(conn, db_id, decision="rejected", actor_token="secret")


# ---------------------------------------------------------------------------
# An invoice with no approval ever opened is refused (HTTP's 404, via the
# same `approvals_repo.get_by_invoice` precondition read). Without this guard,
# `Command(resume=...)` reaches a thread LangGraph never started and a raw
# `KeyError: 'invoice_id'` (a LangGraph internal, from
# `graphs/approval.py::_load_snapshot`) leaks through the MCP transport --
# review finding, Task 5 fix report. (An earlier version of this comment
# predicted `EmptyInputError`; that was disproven by measurement -- see the
# `NotFound` docstring in `mcp/approvals.py`, which matches what was actually
# raised.)
# ---------------------------------------------------------------------------


def test_unknown_invoice_is_refused(requires_postgres, monkeypatch):
    monkeypatch.setattr(
        mcp_approvals,
        "get_settings",
        lambda: _settings_with(
            mcp_allow_approvals=True, mcp_actor_token="secret", mcp_client_name="claude-desktop"
        ),
    )
    unknown_id = uuid.uuid4()

    with pytest.raises(mcp_approvals.NotFound), get_engine().begin() as conn:
        _call(conn, unknown_id, decision="approved", actor_token="secret")


# ---------------------------------------------------------------------------
# 6. empty configured token + approvals enabled must refuse, not accept
#    everything -- a blank shared secret that authorises all callers is worse
#    than no gate, because it reads as one.
# ---------------------------------------------------------------------------


def test_empty_configured_token_refuses_even_a_matching_empty_call(
    requires_postgres, committed_rows, monkeypatch
):
    db_id, _ = _park(committed_rows)
    monkeypatch.setattr(
        mcp_approvals,
        "get_settings",
        lambda: _settings_with(mcp_allow_approvals=True, mcp_actor_token=""),
    )

    with pytest.raises(mcp_approvals.NotAuthorized), get_engine().begin() as conn:
        _call(conn, db_id, actor_token="")

    assert _decision_row(db_id)["decision"] is None


# ---------------------------------------------------------------------------
# Driven through a REAL MCP client (fastmcp.Client over the registry-generated
# server), not the handler function called directly -- the same evidence
# standard `test_mcp_server.py`'s Task 4 tests hold themselves to. Constructing
# the handler is not proof the surface works end-to-end.
# ---------------------------------------------------------------------------


async def test_submit_approval_decision_over_a_real_mcp_client(
    requires_postgres, committed_rows, monkeypatch
):
    from fastmcp import Client

    from invoice_agent.mcp.server import build_server

    db_id, _ = _park(committed_rows)
    monkeypatch.setattr(
        mcp_approvals,
        "get_settings",
        lambda: _settings_with(
            mcp_allow_approvals=True, mcp_actor_token="secret", mcp_client_name="claude-desktop"
        ),
    )

    async with Client(build_server()) as client:
        result = await client.call_tool(
            "submit_approval_decision",
            {"invoice_id": str(db_id), "decision": "rejected", "actor_token": "secret"},
        )

    assert result.structured_content["decision"] == "rejected"
    row = _decision_row(db_id)
    assert row["decision"] == "rejected"
    assert row["decided_by"] == "mcp:claude-desktop"
