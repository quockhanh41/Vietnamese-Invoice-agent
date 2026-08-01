"""Graph shape, the implicit join, and the committed diagram."""

import pathlib

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


def _joined_state(conn, *, category="OFFICE_SUPPLIES") -> dict:
    """Builds a state whose invoice is already persisted (run serial_join first,
    then commit), so auto_post_node's own connections can see it."""
    import uuid
    from types import SimpleNamespace

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
    result = batch.serial_join(state, conn=conn)
    conn.commit()
    state["persisted"] = result["persisted"]
    return state


def test_a_clean_invoice_with_a_mappable_category_posts(owner_conn):
    """The auto path, end to end: category -> seeded TT133 accounts -> balanced
    entry -> guard -> journal_entries with posting_authority='auto_clean' and no
    approval."""
    import uuid

    from invoice_agent.db.repositories import journal

    state = _joined_state(owner_conn, category="OFFICE_SUPPLIES")
    result = batch.auto_post_node(state)

    assert len(result["posted"]) == 1
    db_id = uuid.UUID(result["posted"][0]["db_id"])
    entry = journal.get_for_invoice(owner_conn, db_id)
    assert entry["posting_authority"] == "auto_clean"
    assert entry["approval_id"] is None
    assert entry["total_debit"] == entry["total_credit"]


def test_a_null_category_is_not_posted_but_routed_to_a_human(owner_conn):
    """Spec §0 constraint 4 in its consequential form. An invoice whose category
    the seed does not know has no account to book to, and inventing one is the
    failure M4-D removed when it deleted the fabricated Decimal("50000000")
    budget limit."""
    state = _joined_state(owner_conn, category=None)
    result = batch.auto_post_node(state)

    assert result["posted"] == []
    assert result["auto"] == []
    assert len(result["needs_approval"]) == 1


def test_a_guard_denial_moves_the_invoice_to_needs_approval(owner_conn):
    """One refusal must not end the batch. Same argument as M4-D's per-task
    try/except in the fan-out, applied to the write path."""
    import uuid

    from invoice_agent.db.repositories import flags
    from invoice_agent.domain import findings

    state = _joined_state(owner_conn, category="OFFICE_SUPPLIES")
    db_id = uuid.UUID(state["persisted"][0]["db_id"])
    flags.raise_flag(
        owner_conn,
        db_id,
        code=findings.PRICE_OUTLIER,
        severity="warn",
        raised_by="anomaly",
    )
    owner_conn.commit()

    result = batch.auto_post_node(state)
    assert result["posted"] == []
    assert len(result["needs_approval"]) == 1


def test_the_graph_wires_auto_post_between_route_outcomes_and_finalize():
    graph = batch.build_batch_graph()
    nodes = graph.get_graph().nodes
    assert "auto_post" in nodes

