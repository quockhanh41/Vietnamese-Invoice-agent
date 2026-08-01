"""Graph shape, the implicit join, and the committed diagram."""

import pathlib

import pytest

from invoice_agent.graphs import batch


def test_graph_has_the_shape_design_section_4_specifies():
    rendered = batch.build_batch_graph().get_graph().draw_mermaid()
    for node in (
        "ingest",
        "cost_gate",
        "fan_out",
        "enrich_one",
        "serial_join",
        "route_outcomes",
        "finalize",
    ):
        assert node in rendered, node


def test_serial_join_runs_after_every_send_completes(monkeypatch):
    """The implicit join is the property the whole design rests on: LangGraph
    must complete every Send before the next superstep. Assumed everywhere,
    tested here."""
    seen = {"tasks": 0, "join_saw": None}

    def fake_enrich(task, *, graph=None):
        seen["tasks"] += 1
        return {
            "enriched": [
                {
                    "invoice_id": task["invoice_id"],
                    "extracted": None,
                    "findings": [],
                    "signals": [],
                    "status": "",
                    "source_path": "",
                    "source_type": "xml",
                }
            ],
            "failed": [],
        }

    def fake_join(state, *, conn=None):
        seen["join_saw"] = len(state["enriched"])
        return {"persisted": [], "consumption": [], "raised": []}

    monkeypatch.setattr(batch, "enrich_one", fake_enrich)
    monkeypatch.setattr(batch, "serial_join", fake_join)
    graph = batch.build_batch_graph()
    graph.invoke(
        {
            "batch_id": "b",
            "inputs": [
                {
                    "invoice_id": f"INV-{i}",
                    "source_path": "x",
                    "source_type": "xml",
                    "pdf_path": None,
                    "batch_id": "b",
                }
                for i in range(5)
            ],
            "enriched": [],
            "failed": [],
            "persisted": [],
            "consumption": [],
            "auto": [],
            "needs_approval": [],
            "status": "",
        }
    )
    assert seen["tasks"] == 5
    assert seen["join_saw"] == 5, "serial_join ran before every Send completed"


def test_the_committed_diagram_matches_the_graph():
    """Same drift guard the enrichment graph has. Regenerate with
    `python -m scripts.render_graph`; never hand-edit the .mmd."""
    committed = pathlib.Path("docs/diagrams/batch_graph.mmd")
    assert committed.exists()
    assert (
        committed.read_text(encoding="utf-8").strip()
        == batch.build_batch_graph().get_graph().draw_mermaid().strip()
    )


@pytest.fixture
def committed_rows(requires_postgres):
    """Track and remove rows these tests must COMMIT, then clean them up.

    `auto_post_node` opens its own connections, so it cannot see anything held
    inside an `owner_conn` transaction — its input has to be committed. But
    committing on the fixture's own connection defeats the `trans.rollback()`
    that fixture relies on, which is why the first cut of these tests emitted
    `SAWarning: transaction already deassociated from connection` and left rows
    behind: measured 2026-08-01, running this file against a truncated database
    left **3 invoices, 1 journal entry and 1 vendor with non-zero accumulators**.

    That is not tidiness. `4f870bc` fixed this exact leak in this exact file
    once already. A leaked invoice carries a `vendor_profile.apply_invoice`
    update, and polluted `(count, total, sum_sq)` changes `PRICE_OUTLIER` — a
    `warn` flag — which changes routing, which changes the gate numbers the
    milestone reports. The first M5-A measurement (18 posted / 12 needs) could
    not be reproduced from a clean database for exactly this reason.

    Deletion order follows the FKs: only `invoice_flags` cascades from
    `invoices`, so journal, budget, approval and episodic rows go first.
    """
    import uuid as _uuid

    created: list[_uuid.UUID] = []
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
            text(
                "DELETE FROM app.journal_entries WHERE invoice_id = ANY(:ids)"
            ),  # journal_entry_lines cascades
            {"ids": created},
        )
        for table in ("budget_consumption", "episodic_decisions", "approvals"):
            conn.execute(
                text(f"DELETE FROM app.{table} WHERE invoice_id = ANY(:ids)"), {"ids": created}
            )
        conn.execute(text("DELETE FROM app.invoices WHERE id = ANY(:ids)"), {"ids": created})
        if tax_codes:
            # Every invoice these vendors had is now gone, so zero is the
            # correct accumulator — `_reset_db_state` starts each session the
            # same way.
            conn.execute(
                text(
                    "UPDATE app.vendors SET invoice_count=0, total_amount=0,"
                    " sum_sq_amount=0, avg_amount=NULL, stddev_amount=NULL"
                    " WHERE tax_code = ANY(:codes)"
                ),
                {"codes": tax_codes},
            )


def _joined_state(created, *, category="OFFICE_SUPPLIES") -> dict:
    """Builds a state whose invoice is already persisted and COMMITTED, so
    `auto_post_node`'s own connections can see it.

    Opens its own connection rather than borrowing `owner_conn`. Committing on
    a fixture connection deassociates the transaction its teardown expects to
    roll back — that is what produced `SAWarning: transaction already
    deassociated from connection`, and a warning about broken transaction
    ownership is not cosmetic in a file that tests write guards.

    `created` is the `committed_rows` tracker; every id committed here is
    registered so the fixture can remove it.
    """
    import uuid
    from types import SimpleNamespace

    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.extraction.xml_parser import parse_invoice_xml

    data = parse_invoice_xml(
        pathlib.Path("tests/fixtures/real_samples/viettelpost_gtgt_2.1.0.xml").read_bytes()
    ).model_copy(update={"invoice_number": uuid.uuid4().hex[:8]})

    state = {
        "batch_id": str(uuid.uuid4()),
        "inputs": [],
        "enriched": [
            {
                "invoice_id": "INV-TEST",
                "source_path": "x.xml",
                "source_type": "xml",
                "department": "ADMIN",
                "extracted": data,
                "findings": [],
                "signals": [],
                "classification": None if category is None else SimpleNamespace(category=category, department="ADMIN"),
                "status": "enriched",
            }
        ],
        "failed": [],
        "persisted": [],
        "consumption": [],
        "raised": [],
        "unroutable": [],
        "auto": ["INV-TEST"],
        "needs_approval": [],
        "posted": [],
        "status": "",
    }
    with get_owner_engine().begin() as own:
        result = batch.serial_join(state, conn=own)
    state["persisted"] = result["persisted"]
    created.extend(uuid.UUID(p["db_id"]) for p in result["persisted"])
    return state


def _raise_flag_committed(db_id, *, code: str, severity: str, raised_by: str) -> None:
    """Raise a flag on its own committed transaction — `auto_post_node` and
    `route_outcomes_node` open their own connections and cannot see an
    uncommitted one."""
    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import flags

    with get_owner_engine().begin() as own:
        flags.raise_flag(own, db_id, code=code, severity=severity, raised_by=raised_by)


def test_a_clean_invoice_with_a_mappable_category_posts(committed_rows):
    """The auto path, end to end: category -> seeded TT133 accounts -> balanced
    entry -> guard -> journal_entries with posting_authority='auto_clean' and no
    approval."""
    import uuid

    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import journal

    state = _joined_state(committed_rows, category="OFFICE_SUPPLIES")
    result = batch.auto_post_node(state)

    assert len(result["posted"]) == 1
    db_id = uuid.UUID(result["posted"][0]["db_id"])
    with get_owner_engine().begin() as conn:
        entry = journal.get_for_invoice(conn, db_id)
    assert entry["posting_authority"] == "auto_clean"
    assert entry["approval_id"] is None
    assert entry["total_debit"] == entry["total_credit"]


def test_a_null_category_is_not_posted_but_routed_to_a_human(committed_rows):
    """Spec §0 constraint 4 in its consequential form. An invoice whose category
    the seed does not know has no account to book to, and inventing one is the
    failure M4-D removed when it deleted the fabricated Decimal("50000000")
    budget limit."""
    state = _joined_state(committed_rows, category=None)
    result = batch.auto_post_node(state)

    assert result["posted"] == []
    assert result["auto"] == []
    assert len(result["needs_approval"]) == 1


def test_a_guard_denial_moves_the_invoice_to_needs_approval(committed_rows):
    """One refusal must not end the batch. Same argument as M4-D's per-task
    try/except in the fan-out, applied to the write path.

    Note this reaches `auto_post` with a flag `route_outcomes` never saw — the
    flag is raised after routing. That is the guard acting as the layer beneath
    routing, which is the arrangement M5-A's review restored deliberately."""
    import uuid

    from invoice_agent.domain import findings

    state = _joined_state(committed_rows, category="OFFICE_SUPPLIES")
    db_id = uuid.UUID(state["persisted"][0]["db_id"])
    _raise_flag_committed(
        db_id, code=findings.PRICE_OUTLIER, severity="warn", raised_by="anomaly"
    )

    result = batch.auto_post_node(state)
    assert result["posted"] == []
    assert len(result["needs_approval"]) == 1


def test_the_graph_wires_open_approval_between_auto_post_and_finalize():
    rendered = batch.build_batch_graph().get_graph().draw_mermaid()
    assert "route_outcomes --> auto_post;" in rendered
    assert "auto_post --> open_approval;" in rendered
    assert "open_approval --> finalize;" in rendered
    assert "auto_post --> finalize;" not in rendered


def test_routing_sees_budget_flags_the_fan_out_could_not_have_returned(committed_rows):
    """The defect this node was changed to fix, pinned.

    Measured 2026-08-01 on the gate batch: routing on a task's in-memory
    findings sent 8 invoices to `auto` that the guard then refused, every one
    for BUDGET_BREACH. Budget findings are computed in `serial_join`, after the
    fan-out, so no fan-out result can carry them — the old node was blind to the
    whole family. Reading `invoice_flags` is what closes it, so this test raises
    a flag that exists ONLY in the table and asserts routing honours it.
    """
    import uuid

    from invoice_agent.domain import findings

    state = _joined_state(committed_rows, category="OFFICE_SUPPLIES")
    db_id = uuid.UUID(state["persisted"][0]["db_id"])

    # `enriched[0]` carries NO findings and NO signals — exactly the state a
    # budget-breached invoice is in when it leaves the fan-out.
    assert state["enriched"][0]["findings"] == []
    assert state["enriched"][0]["signals"] == []

    _raise_flag_committed(
        db_id, code=findings.BUDGET_BREACH, severity="critical", raised_by="budget"
    )

    routed = batch.route_outcomes_node(state)
    assert routed["auto"] == []
    assert routed["needs_approval"] == ["INV-TEST"]


def test_an_unpersisted_invoice_never_reaches_the_auto_path(committed_rows):
    """A duplicate the natural key rejected has no `db_id`, so there is nothing
    to post. It used to be routed to `auto` and bounced by `auto_post`; routing
    decides it now, so `auto` means "postable"."""
    state = _joined_state(committed_rows, category="OFFICE_SUPPLIES")
    state["persisted"] = []

    routed = batch.route_outcomes_node(state)
    assert routed["auto"] == []
    assert routed["needs_approval"] == ["INV-TEST"]


def test_open_approval_parks_a_flagged_invoice_at_its_interrupt(committed_rows):
    """Design §5.2: write the approvals row, drive the graph to interrupt, come
    straight back so the batch can finish."""
    import uuid

    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import approvals as approvals_repo
    from invoice_agent.domain import findings

    state = _joined_state(committed_rows, category="OFFICE_SUPPLIES")
    db_id = uuid.UUID(state["persisted"][0]["db_id"])
    _raise_flag_committed(
        db_id, code=findings.PRICE_OUTLIER, severity="warn", raised_by="anomaly"
    )
    state["needs_approval"] = ["INV-TEST"]
    state["auto"] = []

    result = batch.open_approval_node(state)

    assert [o["invoice_id"] for o in result["opened"]] == ["INV-TEST"]
    with get_owner_engine().begin() as conn:
        row = approvals_repo.get_by_invoice(conn, db_id)
    assert row is not None
    assert row["decision"] is None
    assert row["thread_id"] == f"appr:{db_id}"
    assert row["interrupt_payload"]["invoice"]["invoice_number"]


def test_an_invoice_that_never_persisted_gets_no_thread(committed_rows):
    """`approvals.invoice_id` is a NOT NULL FK, so the 2 failed PDFs and the 1
    unpersisted duplicate in the gate batch cannot have a thread. They are
    REPORTED in `unopened`, not silently dropped — the treatment M4-D gave
    `unroutable`."""
    state = _joined_state(committed_rows, category="OFFICE_SUPPLIES")
    state["needs_approval"] = ["INV-GHOST"]
    state["auto"] = []

    result = batch.open_approval_node(state)
    assert result["opened"] == []
    assert result["unopened"] == [{"invoice_id": "INV-GHOST", "reason": "not persisted"}]


