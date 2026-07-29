"""One invoice, end to end, with its cost linked to it — the M3 gate."""

import json
import pathlib

import pytest
from sqlalchemy import select

from invoice_agent.config import Settings
from invoice_agent.db import models
from invoice_agent.llm.gateway import LLMGateway
from scripts.run_one import run_one
from tests.helpers.fake_llm import ok_response

GOOD = json.dumps(
    {
        "template_no": "1",
        "invoice_series": "C26TAA",
        "invoice_number": "00099123",
        "issue_date": "2026-07-01",
        "total_amount": "1194544",
        "seller_tax_code": "0100000003",
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


@pytest.fixture
def pdf_corpus(tmp_path):
    """A two-entry corpus holding PDF-only invoices."""
    from scripts.generator import pdf as pdf_gen
    from tests.helpers.invoice_specs import make_spec

    (tmp_path / "INV-9001.pdf").write_bytes(pdf_gen.render_pdf(make_spec()))
    (tmp_path / "INV-9002.pdf").write_bytes(pdf_gen.render_pdf(make_spec()))
    manifest_lines = [
        json.dumps({"invoice_id": "INV-9001", "path": "INV-9001.pdf", "pdf_path": None, "source_type": "pdf_vision"}),
        json.dumps({"invoice_id": "INV-9002", "path": "INV-9002.pdf", "pdf_path": None, "source_type": "pdf_vision"}),
    ]
    (tmp_path / "manifest.jsonl").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    return tmp_path


def stub_gateway(monkeypatch) -> LLMGateway:
    from invoice_agent.llm import gateway as gw_module

    monkeypatch.setattr(
        gw_module.litellm,
        "completion",
        lambda **kw: ok_response(GOOD, prompt=900, completion=120),
    )
    return LLMGateway(
        Settings(
            _env_file=None,
            gemini_api_keys=["gem1"],
            vision_model_chain=["gemini/gemini-3.5-flash"],
            llm_mode="live",
        )
    )


def test_run_one_links_cost_to_the_invoice(runtime_conn, pdf_corpus, monkeypatch):
    """THE M3 GATE SENTENCE: one invoice end to end, cost recorded in
    llm_calls — and reachable FROM the invoice, which is what the column was
    added for."""
    result = run_one(
        "INV-9001", corpus=pdf_corpus, gateway=stub_gateway(monkeypatch)
    )
    invoice_id = result["db_invoice_id"]
    assert invoice_id is not None

    rows = (
        runtime_conn.execute(
            select(models.llm_calls).where(models.llm_calls.c.invoice_id == invoice_id)
        )
        .mappings()
        .all()
    )
    assert rows, "cost was recorded but never linked to the invoice"
    assert rows[0]["node"] == "vision_extract"
    assert rows[0]["thread_id"] == "enrich:INV-9001"
    assert rows[0]["shadow_cost_usd"] > 0


def test_cost_row_exists_before_the_graph_finishes(
    runtime_conn, pdf_corpus, monkeypatch
):
    """Spec §5.3: rows are written the moment the tokens are spent, so a run
    that dies afterwards cannot lose them. A node placed after vision raises;
    the row must still be there.
    """
    from invoice_agent.graphs import enrichment

    original = enrichment.validate_node

    def exploding_validate(state):
        raise RuntimeError("simulated crash after the tokens were spent")

    monkeypatch.setattr(enrichment, "validate_node", exploding_validate)
    with pytest.raises(RuntimeError):
        run_one("INV-9002", corpus=pdf_corpus, gateway=stub_gateway(monkeypatch))
    monkeypatch.setattr(enrichment, "validate_node", original)

    rows = (
        runtime_conn.execute(
            select(models.llm_calls).where(
                models.llm_calls.c.thread_id == "enrich:INV-9002"
            )
        )
        .mappings()
        .all()
    )
    assert rows, "the call was billed and the record was lost"

