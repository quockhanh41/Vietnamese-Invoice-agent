"""Each node in isolation. The graph that wires them is Task 3's problem."""

import pathlib
from decimal import Decimal

import pytest

from invoice_agent.config import Settings
from invoice_agent.graphs import enrichment
from invoice_agent.llm.gateway import LLMGateway

REAL_XML = pathlib.Path("tests/fixtures/real_samples/viettelpost_gtgt_2.1.0.xml")


def base_state(**overrides) -> dict:
    state = {
        "invoice_id": "INV-0001",
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


class TestRouteSource:
    def test_xml_source_picks_the_xml_strategy(self):
        assert enrichment.route_source(base_state())["strategy"] == "xml"

    def test_pdf_source_picks_vision(self):
        out = enrichment.route_source(base_state(source_type="pdf"))
        assert out["strategy"] == "vision"


class TestParseXml:
    def test_a_real_invoice_parses(self):
        out = enrichment.parse_xml_node(base_state())
        assert out["extracted"] is not None
        assert out["extracted"].total_amount > Decimal(0)
        assert out["current_errors"] == []

    def test_malformed_xml_becomes_an_extraction_error_not_a_crash(self, tmp_path):
        broken = tmp_path / "broken.xml"
        broken.write_text("<HDon><oops", encoding="utf-8")
        out = enrichment.parse_xml_node(base_state(source_path=str(broken)))
        assert out["extracted"] is None
        assert out["current_errors"], "the reason must survive for the router"
        assert len(out["error_history"]) == 1, "a DELTA, never the accumulated list"


class TestValidateNode:
    def test_a_clean_real_invoice_produces_no_findings(self):
        """8/8 real invoices validate clean (M2-B). If this goes red, the
        false-positive rate the whole project is judged on has moved."""
        parsed = enrichment.parse_xml_node(base_state())
        out = enrichment.validate_node(base_state(extracted=parsed["extracted"]))
        assert out["findings"] == []
        assert out["error_history"] == []

    def test_findings_become_prompt_ready_error_strings(self):
        from datetime import date

        from invoice_agent.extraction.models import InvoiceData, InvoiceLine

        broken = InvoiceData(
            template_no="1",
            invoice_series="C26TAA",
            invoice_number="00000123",
            issue_date=date(2026, 7, 1),
            total_amount=Decimal("999999"),
            lines=[
                InvoiceLine(
                    line_no=1, item_kind=1, description="X", amount=Decimal("100")
                )
            ],
        )
        out = enrichment.validate_node(base_state(extracted=broken))
        assert out["findings"], "an invoice whose total ignores its lines must flag"
        assert out["current_errors"], "the router and the retry prompt need text"
        assert len(out["error_history"]) == len(out["findings"])

    def test_no_extraction_means_nothing_to_validate(self):
        assert enrichment.validate_node(base_state(extracted=None)) == {}


class TestManualQueue:
    def test_sets_status_and_writes_nothing_else(self):
        """Spec §5.2: state only. `invoices.status` belongs to M4's batch."""
        assert enrichment.manual_queue_node(base_state()) == {"status": "manual_queue"}


class TestVisionNode:
    def test_a_broken_pdf_records_no_cost_and_no_extraction(
        self, tmp_path, monkeypatch
    ):
        from invoice_agent.llm import gateway as gw_module

        monkeypatch.setattr(
            gw_module.litellm,
            "completion",
            lambda **kw: pytest.fail("a broken PDF must never reach a model"),
        )
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"%PDF-1.7\nnot really")
        gw = LLMGateway(
            Settings(_env_file=None, gemini_api_keys=["g"], llm_mode="live")
        )

        out = enrichment.vision_extract_node(
            base_state(source_path=str(broken), source_type="pdf", strategy="vision"),
            gateway=gw,
        )
        assert out["extracted"] is None
        assert out["llm_calls"] == []
        assert out["extraction_attempts"] == 1, "a failed attempt still counts"
        assert len(out["error_history"]) == 1
