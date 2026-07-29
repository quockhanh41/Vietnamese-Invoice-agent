"""The compiled graph. Includes the M3 gate's reducer-trap condition."""

import json
import pathlib
from decimal import Decimal

import pytest

from invoice_agent.config import Settings
from invoice_agent.graphs.enrichment import (
    MAX_VISION_ATTEMPTS,
    build_enrichment_graph,
)
from invoice_agent.llm.gateway import LLMGateway
from scripts.generator import pdf as pdf_gen
from tests.helpers.fake_llm import ok_response
from tests.helpers.invoice_specs import make_spec

REAL_XML = pathlib.Path("tests/fixtures/real_samples/viettelpost_gtgt_2.1.0.xml")

# Parses into InvoiceData, but the total ignores the line — so `validate` finds
# an ARITHMETIC_MISMATCH on every attempt and the loop can never converge.
ALWAYS_INVALID = json.dumps(
    {
        "template_no": "1",
        "invoice_series": "C26TAA",
        "invoice_number": "00000123",
        "issue_date": "2026-07-01",
        "seller_tax_code": "0100000003",
        "total_amount": "999999",
        "lines": [
            {
                "line_no": 1,
                "item_kind": 1,
                "description": "Dịch vụ",
                "amount": "100",
            }
        ],
    }
)



def stub_gateway(monkeypatch, reply: str) -> LLMGateway:
    from invoice_agent.llm import gateway as gw_module

    monkeypatch.setattr(
        gw_module.litellm,
        "completion",
        lambda **kw: ok_response(reply, prompt=900, completion=120),
    )
    return LLMGateway(
        Settings(
            _env_file=None,
            gemini_api_keys=["gem1"],
            vision_model_chain=["gemini/gemini-3.5-flash"],
            llm_mode="live",
        )
    )


def initial(**overrides) -> dict:
    state = {
        "invoice_id": "INV-TEST",
        "source_path": str(REAL_XML),
        "pdf_path": None,
        "source_type": "xml",
        "strategy": "xml",
        "extracted": None,
        "extraction_attempts": 0,
        "current_errors": [],
        "error_history": [],
        "findings": [],
        "llm_calls": [],
        "status": "received",
    }
    state.update(overrides)
    return state


def test_a_real_xml_invoice_runs_clean_end_to_end():
    final = build_enrichment_graph().invoke(initial())
    assert final["extracted"] is not None
    assert final["findings"] == []
    assert final["status"] != "manual_queue"


def test_error_history_grows_LINEARLY_not_quadratically(tmp_path, monkeypatch):
    """THE M3 GATE CONDITION.

    Three vision attempts, each producing exactly one finding, must leave
    THREE entries. A node that returns the accumulated list instead of a delta
    gives 1, 3, 7 — which at three iterations reads as an off-by-one and only
    at scale reads as quadratic. That is why it is a trap and not a bug.
    """
    gw = stub_gateway(monkeypatch, ALWAYS_INVALID)
    pdf_path = tmp_path / "invoice.pdf"
    pdf_path.write_bytes(pdf_gen.render_pdf(make_spec()))

    final = build_enrichment_graph(gateway=gw).invoke(
        initial(source_path=str(pdf_path), source_type="pdf", strategy="vision")
    )

    assert final["extraction_attempts"] == MAX_VISION_ATTEMPTS
    assert len(final["error_history"]) == MAX_VISION_ATTEMPTS
    assert final["status"] == "manual_queue"


def test_a_truncated_pdf_reaches_manual_queue_without_spending_a_token(
    tmp_path, monkeypatch
):
    from invoice_agent.llm import gateway as gw_module

    monkeypatch.setattr(
        gw_module.litellm,
        "completion",
        lambda **kw: pytest.fail("a PDF that cannot be opened must not reach a model"),
    )
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(pdf_gen.render_truncated(make_spec()))
    gw = LLMGateway(Settings(_env_file=None, gemini_api_keys=["g"], llm_mode="live"))

    final = build_enrichment_graph(gateway=gw).invoke(
        initial(source_path=str(broken), source_type="pdf", strategy="vision")
    )
    assert final["status"] == "manual_queue"
    assert final["llm_calls"] == []


def test_an_unparseable_xml_with_a_pdf_twin_climbs_to_vision(tmp_path, monkeypatch):
    """The gate's escalation clause, on the compiled graph."""
    good = json.dumps(
        {
            "template_no": "1",
            "invoice_series": "C26TAA",
            "invoice_number": "00000123",
            "issue_date": "2026-07-01",
            "total_amount": "1194544",
            "lines": [
                {
                    "line_no": 1,
                    "item_kind": 1,
                    "description": "Dịch vụ",
                    "amount": "1194544",
                }
            ],
        }
    )
    gw = stub_gateway(monkeypatch, good)
    broken_xml = tmp_path / "broken.xml"
    broken_xml.write_text("<HDon><oops", encoding="utf-8")
    twin = tmp_path / "twin.pdf"
    twin.write_bytes(pdf_gen.render_pdf(make_spec()))

    final = build_enrichment_graph(gateway=gw).invoke(
        initial(source_path=str(broken_xml), pdf_path=str(twin))
    )
    assert final["strategy"] == "vision"
    assert final["extracted"] is not None
    assert final["extracted"].total_amount == Decimal("1194544")


def test_the_committed_diagram_matches_the_graph():
    """The artefact is a drift guard, not decoration — which is the one thing a
    committed PNG could never be. Regenerate with:
        .venv/bin/python -m scripts.render_graph
    """
    committed = pathlib.Path("docs/diagrams/enrichment_graph.mmd")
    assert committed.exists(), "run `.venv/bin/python -m scripts.render_graph`"
    current = build_enrichment_graph().get_graph().draw_mermaid()
    assert committed.read_text(encoding="utf-8").strip() == current.strip()

