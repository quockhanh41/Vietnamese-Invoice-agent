"""One invoice, end to end, with its cost linked to it — the M3 gate.

⚠️ EVERY IDENTIFIER HERE IS UNIQUE PER RUN, and that is load-bearing rather
than tidy. Both tests assert on rows *this* run wrote, and nothing cleans the
database between runs — the runtime role deliberately has no DELETE on
`llm_calls` (M2-A least privilege). Two failures were measured on 2026-07-29
during the M3-C exit review, both caused by fixed identifiers:

  * a fixed `invoice_number` made `test_run_one_links_cost_to_the_invoice` pass
    exactly ONCE per database. `insert_with_lines` is
    `ON CONFLICT DO NOTHING ... RETURNING`, so the second run returns None and
    `assert invoice_id is not None` fails — green locally on a fresh database,
    red on any repeat.
  * a fixed `thread_id` made `test_cost_row_exists_before_the_graph_finishes`
    pass forever once ANY earlier run had written a row. Measured: with
    `_persist_cost` mutated to a no-op the test stayed GREEN against a dirty
    table and only went red against a clean one — a guard that stops guarding
    after its first success.

Spec §8.5 predicted exactly this ("`enrich:{invoice_id}` is stable, so
re-running the same invoice appends to the same checkpoint thread").
"""

import json
import uuid

import pytest
from scripts.run_one import run_one
from sqlalchemy import select
from tests.helpers.fake_llm import ok_response

from invoice_agent.config import Settings
from invoice_agent.db import models
from invoice_agent.llm.gateway import LLMGateway


@pytest.fixture
def run_token() -> str:
    """Distinct per test, so neither two runs nor two tests collide in Postgres."""
    return uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def cleanup_run_one_db(run_token):
    yield
    from sqlalchemy import text

    from invoice_agent.db.engine import get_owner_engine

    inv_a, inv_b = _invoice_ids(run_token)
    with get_owner_engine().begin() as conn:
        conn.execute(
            text("UPDATE app.llm_calls SET invoice_id = NULL WHERE thread_id IN (:ta, :tb)"),
            {"ta": f"enrich:{inv_a}", "tb": f"enrich:{inv_b}"},
        )
        conn.execute(
            text("DELETE FROM app.invoices WHERE invoice_number IN (:a, :b) OR seller_tax_code = :tc"),
            {"a": inv_a, "b": inv_b, "tc": f"01{run_token[:8]}"},
        )


def _invoice_ids(run_token: str) -> tuple[str, str]:
    return f"INV-{run_token}-A", f"INV-{run_token}-B"


@pytest.fixture
def pdf_corpus(tmp_path, run_token):
    """A two-entry corpus whose invoice ids belong to this run alone."""
    from scripts.generator import pdf as pdf_gen
    from tests.helpers.invoice_specs import make_spec

    lines = []
    for invoice_id in _invoice_ids(run_token):
        (tmp_path / f"{invoice_id}.pdf").write_bytes(pdf_gen.render_pdf(make_spec()))
        lines.append(
            json.dumps(
                {
                    "invoice_id": invoice_id,
                    "path": f"{invoice_id}.pdf",
                    "pdf_path": None,
                    "source_type": "pdf_vision",
                }
            )
        )
    (tmp_path / "manifest.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def stub_gateway(monkeypatch, run_token: str) -> LLMGateway:
    """A vision model that always returns one valid invoice.

    `invoice_number` carries the run token so the natural key
    (seller_tax_code, template_no, invoice_series, invoice_number) is new every
    run — see this module's docstring.
    """
    from invoice_agent.llm import gateway as gw_module

    payload = json.dumps(
        {
            "template_no": "1",
            "invoice_series": "C26TAA",
            "invoice_number": f"9{run_token}",
            "issue_date": "2026-07-01",
            "total_amount": "1194544",
            "seller_tax_code": f"01{run_token[:8]}",
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
    monkeypatch.setattr(
        gw_module.litellm,
        "completion",
        lambda **kw: ok_response(payload, prompt=900, completion=120),
    )
    return LLMGateway(
        Settings(
            _env_file=None,
            gemini_api_keys=["gem1"],
            vision_model_chain=["gemini/gemini-3.5-flash"],
            llm_mode="live",
        )
    )


def test_run_one_links_cost_to_the_invoice(runtime_conn, pdf_corpus, monkeypatch, run_token):
    """THE M3 GATE SENTENCE: one invoice end to end, cost recorded in
    llm_calls — and reachable FROM the invoice, which is what the column was
    added for."""
    first, _ = _invoice_ids(run_token)
    result = run_one(first, corpus=pdf_corpus, gateway=stub_gateway(monkeypatch, run_token))
    invoice_id = result["db_invoice_id"]
    assert invoice_id is not None, "the invoice row was not created"

    rows = (
        runtime_conn.execute(
            select(models.llm_calls).where(models.llm_calls.c.invoice_id == invoice_id)
        )
        .mappings()
        .all()
    )
    assert rows, "cost was recorded but never linked to the invoice"
    assert rows[0]["node"] == "vision_extract"
    assert rows[0]["thread_id"] == f"enrich:{first}"
    assert rows[0]["shadow_cost_usd"] > 0


def test_cost_row_exists_before_the_graph_finishes(
    runtime_conn, pdf_corpus, monkeypatch, run_token
):
    """Spec §5.3: rows are written the moment the tokens are spent, so a run
    that dies afterwards cannot lose them. A node placed after vision raises;
    the row must still be there.

    The thread_id is unique to this run, so a row can only satisfy this
    assertion if THIS run wrote it.
    """
    from invoice_agent.graphs import enrichment

    _, second = _invoice_ids(run_token)

    def exploding_validate(state):
        raise RuntimeError("simulated crash after the tokens were spent")

    monkeypatch.setattr(enrichment, "validate_node", exploding_validate)
    with pytest.raises(RuntimeError):
        run_one(second, corpus=pdf_corpus, gateway=stub_gateway(monkeypatch, run_token))

    rows = (
        runtime_conn.execute(
            select(models.llm_calls).where(
                models.llm_calls.c.thread_id == f"enrich:{second}"
            )
        )
        .mappings()
        .all()
    )
    assert rows, "the call was billed and the record was lost"
