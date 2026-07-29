"""The vision branch: PDF in, InvoiceData out, or a controlled give-up."""

import json
from decimal import Decimal

import pytest
from scripts.generator import pdf as pdf_gen
from tests.helpers.invoice_specs import make_spec

from invoice_agent.config import Settings
from invoice_agent.extraction import vision
from invoice_agent.extraction.models import InvoiceData
from invoice_agent.llm.gateway import LLMGateway


def make_gateway(**overrides) -> LLMGateway:
    defaults = {
        "gemini_api_keys": ["gem1"],
        "llm_mode": "live",
        "vision_model_chain": ["gemini/gemini-3.5-flash"],
    }
    return LLMGateway(Settings(_env_file=None, **{**defaults, **overrides}))


GOOD_RESPONSE = json.dumps({
    "template_no": "1",
    "invoice_series": "C26TAA",
    "invoice_number": "00000123",
    "issue_date": "2026-07-01",
    "total_amount": "1290107.52",
    "seller_tax_code": "0100000003",
    "lines": [
        {
            "line_no": 1,
            "item_kind": 1,
            "description": "Dịch vụ tư vấn",
            "amount": "1194544",
        }
    ],
})


def scripted(monkeypatch, replies):
    from tests.helpers.fake_llm import ok_response

    from invoice_agent.llm import gateway as gw_module

    seen = []

    def fake_completion(**kwargs):
        seen.append(kwargs)
        return ok_response(replies[len(seen) - 1], prompt=900, completion=120)

    monkeypatch.setattr(gw_module.litellm, "completion", fake_completion)
    return seen


class TestRasterize:
    def test_a_readable_pdf_becomes_png_pages(self):
        pages = vision.rasterize(pdf_gen.render_pdf(make_spec()))
        assert len(pages) == 1
        assert pages[0].startswith(b"\x89PNG\r\n\x1a\n")

    def test_truncated_pdf_raises_vision_extraction_error(self):
        """MEASURED 2026-07-29 (spec §2.4): pypdfium2 raises PdfiumError rather
        than handing back a zero-page document."""
        with pytest.raises(vision.VisionExtractionError):
            vision.rasterize(pdf_gen.render_truncated(make_spec()))


class TestExtract:
    def test_a_readable_pdf_produces_invoice_data(self, monkeypatch):
        scripted(monkeypatch, [GOOD_RESPONSE])
        data = vision.extract(pdf_gen.render_pdf(make_spec()), gateway=make_gateway())
        assert isinstance(data, InvoiceData)
        assert data.total_amount == Decimal("1290107.52")

    def test_a_broken_pdf_never_calls_the_model(self, monkeypatch):
        """The cheap failure must stay cheap: no tokens for a file that cannot
        be opened."""
        seen = scripted(monkeypatch, [GOOD_RESPONSE])
        calls = []
        with pytest.raises(vision.VisionExtractionError):
            vision.extract(
                pdf_gen.render_truncated(make_spec()),
                gateway=make_gateway(),
                calls=calls,
            )
        assert seen == []
        assert calls == []

    def test_amounts_are_requested_as_strings(self, monkeypatch):
        """Real invoices carry 21618409.09000000. A JSON float loses the last
        đồng — the exact bug M1 measured, and why every amount here is Decimal.
        Pydantic parses Decimal from a string losslessly, from a float lossily."""
        seen = scripted(monkeypatch, [GOOD_RESPONSE])
        vision.extract(pdf_gen.render_pdf(make_spec()), gateway=make_gateway())
        sent = (
            seen[0]["messages"][0]["content"]
            + str(seen[0]["messages"][-1]["content"])
        )
        assert "chuỗi" in sent or "string" in sent.lower(), (
            "the prompt must require amounts as strings"
        )

    def test_current_errors_are_injected_on_retry(self, monkeypatch):
        """Design §3.3: retrying vision with the concrete arithmetic error in
        the prompt is what makes the loop real."""
        seen = scripted(monkeypatch, [GOOD_RESPONSE])
        vision.extract(
            pdf_gen.render_pdf(make_spec()),
            gateway=make_gateway(),
            current_errors=["Tổng tiền không khớp: 1.290.107 ≠ 1.194.544 + 95.563"],
        )
        assert "1.290.107" in str(seen[0]["messages"][-1]["content"])

    def test_vision_never_calls_a_blind_model(self, monkeypatch):
        """MEASURED 2026-07-29 (spec §2.1): four of the six chain models return
        supports_vision=False, and Groq is FIRST. Routing vision through the
        text chain burns two failed llm_calls rows per invoice."""
        seen = scripted(monkeypatch, [GOOD_RESPONSE])
        gw = make_gateway(
            groq_api_keys=["gk1"],
            llm_model_chain=["groq/llama-3.3-70b-versatile"],
            vision_model_chain=["gemini/gemini-3.5-flash"],
        )
        vision.extract(pdf_gen.render_pdf(make_spec()), gateway=gw)
        assert seen[0]["model"] == "gemini/gemini-3.5-flash"


class TestSettings:
    def test_vision_chain_defaults_to_the_two_models_that_can_see(self):
        assert Settings(_env_file=None).vision_model_chain == [
            "gemini/gemini-3.5-flash",
            "openai/gpt-4o-mini",
        ]

    def test_vision_chain_parses_csv_without_json(self, monkeypatch):
        """The NoDecode trap from M0.2 applies to every list[str] setting."""
        monkeypatch.setenv("VISION_MODEL_CHAIN", "gemini/a,openai/b")
        assert Settings(_env_file=None).vision_model_chain == ["gemini/a", "openai/b"]
