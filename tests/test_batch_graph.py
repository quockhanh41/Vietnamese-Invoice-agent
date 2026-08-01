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
