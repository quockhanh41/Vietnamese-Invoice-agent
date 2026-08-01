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
