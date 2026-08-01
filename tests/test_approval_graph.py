"""The approval graph — design §5, spec 2026-08-01-m5b.

Every test here builds its invoice through `_persisted_invoice`, which commits.
Nodes open their own connections (they must: `apply_decision` commits so
`post_entry`'s guard can re-read), so an uncommitted fixture transaction is
invisible to them. `committed_rows` removes what the test committed — the same
arrangement `tests/test_batch_graph.py` uses, and for the same reason.
"""

import json
import pathlib
import uuid
from decimal import Decimal

import pytest

from invoice_agent.graphs import approval


@pytest.fixture
def committed_rows(requires_postgres):
    """Delete rows these tests commit, in FK order."""
    created: list[uuid.UUID] = []
    yield created
    if not created:
        return
    from sqlalchemy import text

    from invoice_agent.db.engine import get_owner_engine

    with get_owner_engine().begin() as conn:
        tax_codes = [
            r[0]
            for r in conn.execute(
                text("SELECT DISTINCT seller_tax_code FROM app.invoices WHERE id = ANY(:ids)"),
                {"ids": created},
            )
        ]
        conn.execute(
            text("DELETE FROM app.journal_entries WHERE invoice_id = ANY(:ids)"), {"ids": created}
        )
        for table in ("budget_consumption", "episodic_decisions", "approvals"):
            conn.execute(
                text(f"DELETE FROM app.{table} WHERE invoice_id = ANY(:ids)"), {"ids": created}
            )
        conn.execute(text("DELETE FROM app.invoices WHERE id = ANY(:ids)"), {"ids": created})
        if tax_codes:
            conn.execute(
                text(
                    "UPDATE app.vendors SET invoice_count=0, total_amount=0, sum_sq_amount=0,"
                    " avg_amount=NULL, stddev_amount=NULL, rejection_count=0,"
                    " reviewer_notes=NULL WHERE tax_code = ANY(:codes)"
                ),
                {"codes": tax_codes},
            )


def _persisted_invoice(created, *, category="OFFICE_SUPPLIES", flag=None):
    """Commit one invoice, optionally flagged, and open an approval for it.

    Returns (invoice_db_id, approval_id).
    """
    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import approvals as approvals_repo
    from invoice_agent.db.repositories import flags, invoices
    from invoice_agent.extraction.xml_parser import parse_invoice_xml

    data = parse_invoice_xml(
        pathlib.Path("tests/fixtures/real_samples/viettelpost_gtgt_2.1.0.xml").read_bytes()
    ).model_copy(update={"invoice_number": uuid.uuid4().hex[:8]})

    with get_owner_engine().begin() as conn:
        db_id = invoices.insert_with_lines(
            conn, data, source_type="xml", category=category, department="ADMIN"
        )
        assert db_id is not None
        created.append(db_id)
        if flag is not None:
            flags.raise_flag(
                conn, db_id, code=flag[0], severity=flag[1], raised_by=flag[2]
            )
        approval_id = approvals_repo.open_approval(
            conn,
            invoice_id=db_id,
            thread_id=f"appr:{db_id}",
            interrupt_payload={},
            flags_snapshot={},
            suggestions={},
        )
    return db_id, approval_id


def _initial_state(db_id, approval_id) -> dict:
    return {
        "invoice_id": str(db_id),
        "approval_id": str(approval_id),
        "snapshot": {},
        "flags": [],
        "suggestions": [],
        "decision": "",
        "decided_by": "",
        "reason": None,
        "corrections": None,
        "entry_id": None,
        "status": "",
    }


def test_load_snapshot_carries_only_json_types(committed_rows):
    """Spec §0 constraint 3. An `appr:` thread lives DAYS — no other state in
    this system does — so its checkpoint must survive a deploy landing between
    the interrupt and the decision. A pydantic model that gained a required
    field will not deserialize; a dict will. `Decimal` and `date` are
    stringified here, at the boundary, not deep in a node.
    """
    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    out = approval.load_snapshot_node(_initial_state(db_id, approval_id))

    # The real assertion: the whole payload survives json.dumps with no encoder.
    json.dumps({"snapshot": out["snapshot"], "flags": out["flags"]})

    assert out["snapshot"]["invoice_number"]
    assert isinstance(out["snapshot"]["total_amount"], str)
    assert out["snapshot"]["lines"], "a reviewer needs the line items"
    assert [f["code"] for f in out["flags"]] == ["PRICE_OUTLIER"]


def test_load_snapshot_shows_only_open_flags(committed_rows):
    """A resolved flag is history, not a reason to hold the invoice."""
    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import flags

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    with get_owner_engine().begin() as conn:
        for row in flags.open_flags_for(conn, db_id):
            flags.resolve_flag(conn, row["id"])

    out = approval.load_snapshot_node(_initial_state(db_id, approval_id))
    assert out["flags"] == []


def test_suggest_is_empty_on_a_fresh_database(committed_rows):
    """Spec §4.2, reported rather than hidden. `episodic_decisions` is empty
    until reviewers decide things, so the first batch's suggestions are all
    empty lists. Seeding fixtures to make the feature look alive would measure
    the fixtures — the same reason item 4.4's tuning stays deferred.
    """
    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {**_initial_state(db_id, approval_id)}
    state.update(approval.load_snapshot_node(state))

    out = approval.suggest_node(state)
    assert out["suggestions"] == []


def test_suggest_recalls_an_earlier_decision_for_the_same_vendor(committed_rows):
    """The half that proves `suggest` is wired to memory at all, not just to an
    empty table."""
    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import episodic

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {**_initial_state(db_id, approval_id)}
    state.update(approval.load_snapshot_node(state))
    tax_code = state["snapshot"]["seller_tax_code"]

    with get_owner_engine().begin() as conn:
        episodic.record_episode(
            conn,
            invoice_id=db_id,
            vendor_tax_code=tax_code,
            amount=Decimal("100"),
            flags={},
            decision="rejected",
            reason="sai số học",
        )

    out = approval.suggest_node(state)
    assert len(out["suggestions"]) == 1
    assert out["suggestions"][0]["decision"] == "rejected"
    assert out["suggestions"][0]["reason"] == "sai số học"


def test_suggest_persists_the_context_it_built(committed_rows):
    """`approvals` carries `interrupt_payload`/`flags_snapshot`/`suggestions`
    (design §2.8). They are written HERE, before the gate, because the gate must
    contain `interrupt()` and nothing else — anything added to that node runs
    twice across a restart.
    """
    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import approvals as approvals_repo

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    state = {**_initial_state(db_id, approval_id)}
    state.update(approval.load_snapshot_node(state))
    approval.suggest_node(state)

    with get_owner_engine().begin() as conn:
        row = approvals_repo.get_approval(conn, approval_id)
    assert row["flags_snapshot"]["flags"][0]["code"] == "PRICE_OUTLIER"
    assert "invoice" in row["interrupt_payload"]


def test_the_graph_stops_at_the_interrupt_and_checkpoints(committed_rows):
    """The property design §5.2 calls the largest difference between this system
    and a confirm dialog: execution stops and the whole state lands in Postgres,
    so the process may die."""
    from invoice_agent.checkpointer import build_checkpointer

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    config = {"configurable": {"thread_id": f"appr:{db_id}"}}
    with build_checkpointer() as saver:
        saver.setup()
        app = approval.build_approval_graph(checkpointer=saver)
        result = app.invoke(_initial_state(db_id, approval_id), config)

    assert "__interrupt__" in result, "the graph ran past its own gate"
    payload = result["__interrupt__"][0].value
    assert payload["invoice"]["invoice_number"]
    assert payload["flags"][0]["code"] == "PRICE_OUTLIER"


def test_the_gate_node_has_no_side_effect_beyond_the_interrupt():
    """A STATIC guard, and deliberately so. The behavioural cost of a write in
    this node only appears across a `kill -9` (Task 9 measures it), which is
    slow and easy to skip. This one is instant and fails at the commit that
    introduces the write.

    M1.5 measured the consequence: the node containing `interrupt()` runs TWICE
    across resume, so anything here happens twice.
    """
    import inspect

    source = inspect.getsource(approval.approval_gate_node)
    for forbidden in ("conn", "insert", "update", "record_", "get_engine", "repositories"):
        assert forbidden not in source, (
            f"approval_gate_node touches {forbidden!r}; it must contain interrupt() and "
            "nothing else — M1.5 measured that this node runs twice across a restart"
        )


def test_apply_decision_commits_so_a_separate_connection_can_see_it(committed_rows):
    """Spec §2.3 — the boundary that can rot silently.

    `post_entry`'s guard re-reads `approvals` from Postgres. If the two nodes
    shared a transaction the guard would read its own caller's uncommitted
    write, verify nothing, and pass every ordinary test. So this asserts the
    decision is visible from a connection the node never touched.
    """
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import approvals as approvals_repo

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "approved",
        "decided_by": "ketoan",
        "reason": "ok",
    }
    approval.apply_decision_node(state)

    with get_engine().begin() as other:
        row = approvals_repo.get_approval(other, approval_id)
    assert row["decision"] == "approved"
    assert row["decided_by"] == "ketoan", "decided_by is stored BARE, no human: prefix"


def test_apply_decision_accepts_no_connection_parameter():
    """Static guard on the same property. A `conn=` parameter is the one change
    that would let a caller hand `apply_decision` the transaction `post_entry`
    later reads on — re-creating the defect above with the behavioural test
    still green, because the test above opens its own connection either way.
    """
    import inspect

    assert "conn" not in inspect.signature(approval.apply_decision_node).parameters


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_deciding_an_invoice_closes_its_flags(committed_rows, decision):
    """M5-A spec §8 question 1, answered. A human has adjudicated the flags
    either way. Left open, `invoices.status` says `flagged` forever,
    `route_outcomes` keeps treating the invoice as blocked, and M5-C's queue
    re-serves invoices somebody already decided.

    The rows are NOT deleted — `resolved_at` is set, so §10.4's flag
    false-positive metric can still count them.
    """
    from sqlalchemy import select

    from invoice_agent.db import models
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import flags

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    state = {
        **_initial_state(db_id, approval_id),
        "decision": decision,
        "decided_by": "ketoan",
    }
    approval.apply_decision_node(state)

    with get_engine().begin() as conn:
        assert flags.open_flags_for(conn, db_id) == []
        rows = list(
            conn.execute(
                select(models.invoice_flags).where(models.invoice_flags.c.invoice_id == db_id)
            ).mappings()
        )
    assert len(rows) == 1, "the flag row must survive; only resolved_at changes"
    assert rows[0]["resolved_at"] is not None


