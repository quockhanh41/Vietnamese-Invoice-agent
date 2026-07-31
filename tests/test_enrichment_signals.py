"""`signals` — entity memory turned into findings (design §3.2).

THE ORDERING IS THE DESIGN. `signals` runs inside `enrich_one` during fan-out;
invoices are persisted by M4-D's serial node AFTER the join. So the profile it
reads is "as of batch start" and the scored invoice is absent from its own
statistics for free.

That is not a nicety. Measured 2026-07-31: a single extreme point's z-score
against a sample CONTAINING it is bounded by (n-1)/sqrt(n) — 1.79 at n=5, 2.27
at n=7 — so with PRICE_OUTLIER_Z = 3 the finding is arithmetically impossible
below n=11, against a corpus median of 7. If anything ever persists before
`signals` runs, PRICE_OUTLIER degrades silently rather than failing.
"""

from decimal import Decimal

import pytest

from invoice_agent.db.repositories import vendors as vendor_repo
from invoice_agent.domain import findings
from invoice_agent.graphs import enrichment
from invoice_agent.memory import vendor_profile


def _state(invoice, **over):
    base = {
        "invoice_id": "INV-TEST", "source_path": "x.xml", "pdf_path": None,
        "source_type": "xml", "strategy": "xml", "extracted": invoice,
        "extraction_attempts": 0, "current_errors": [], "error_history": [],
        "findings": [], "llm_calls": [], "signals": [], "status": "",
    }
    base.update(over)
    return base


def test_unknown_vendor_raises_NEW_VENDOR(runtime_conn, sample_invoice):
    out = enrichment.signals_node(_state(sample_invoice), conn=runtime_conn)
    codes = [f.code for f in out["signals"]]
    assert findings.NEW_VENDOR in codes


def test_known_vendor_does_not_raise_NEW_VENDOR(runtime_conn, sample_invoice):
    vendor_repo.ensure_vendor(runtime_conn, sample_invoice.seller_tax_code, name="NCC")
    vendor_profile.apply_invoice(
        runtime_conn, tax_code=sample_invoice.seller_tax_code, amount=Decimal("1000000")
    )
    out = enrichment.signals_node(_state(sample_invoice), conn=runtime_conn)
    assert findings.NEW_VENDOR not in [f.code for f in out["signals"]]


def test_scored_invoice_is_absent_from_its_own_stats(runtime_conn, sample_invoice):
    """The load-bearing invariant. Build a vendor history that does NOT include
    this invoice, then assert the stats `signals` reads have that exact count —
    persist the invoice first and this goes red."""
    tax_code = sample_invoice.seller_tax_code
    vendor_repo.ensure_vendor(runtime_conn, tax_code, name="NCC")
    for amount in (Decimal("1000000"), Decimal("1100000"), Decimal("900000")):
        vendor_profile.apply_invoice(runtime_conn, tax_code=tax_code, amount=amount)

    stats = vendor_profile.stats_for(runtime_conn, tax_code)
    assert stats.invoice_count == 3, (
        "the invoice being scored must not be in its own vendor statistics"
    )
    enrichment.signals_node(_state(sample_invoice), conn=runtime_conn)
    assert vendor_profile.stats_for(runtime_conn, tax_code).invoice_count == 3, (
        "signals must not write — persistence is M4-D's serial node"
    )


def test_signals_node_calls_no_model(runtime_conn, sample_invoice, monkeypatch):
    """One of the five non-LLM nodes whose ratio design §3.2 calls 'the design,
    not thrift'. A node that quietly starts calling a model breaks the cost
    story with nothing failing."""
    from invoice_agent.llm import gateway as gw

    def explode(*a, **k):
        raise AssertionError("signals must not call a model")

    monkeypatch.setattr(gw.LLMGateway, "generate", explode, raising=False)
    monkeypatch.setattr(gw.LLMGateway, "generate_with_model", explode, raising=False)
    enrichment.signals_node(_state(sample_invoice), conn=runtime_conn)


def test_signals_sees_no_intra_batch_sibling(runtime_conn, sample_invoice):
    """Two invoices from one vendor in one batch must both see the SAME profile.

    Fan-out is parallel, so if `signals` could see a sibling processed moments
    earlier, the finding would depend on which `Send` finished first — the exact
    order-dependence `test_budget_serialization` exists to disprove. At M4-B the
    expressible form is: `signals` writes nothing, so consecutive calls observe
    identical statistics.
    """
    tax_code = sample_invoice.seller_tax_code
    vendor_repo.ensure_vendor(runtime_conn, tax_code, name="NCC")
    vendor_profile.apply_invoice(
        runtime_conn, tax_code=tax_code, amount=Decimal("1000000")
    )
    before = vendor_profile.stats_for(runtime_conn, tax_code)
    enrichment.signals_node(_state(sample_invoice), conn=runtime_conn)
    enrichment.signals_node(_state(sample_invoice), conn=runtime_conn)
    assert vendor_profile.stats_for(runtime_conn, tax_code) == before


def test_missing_seller_tax_code_raises_nothing(runtime_conn, sample_invoice):
    """No seller means no vendor to remember. NEW_VENDOR would be wrong — the
    vendor may be well known and merely unidentifiable here, which is what
    validation's TAX_CODE_MISSING already reports."""
    anonymous = sample_invoice.model_copy(update={"seller_tax_code": None})
    assert enrichment.signals_node(_state(anonymous), conn=runtime_conn)["signals"] == []


def test_signals_returns_a_delta_not_the_whole_list(runtime_conn, sample_invoice):
    """`signals` accumulates with operator.add. Returning the accumulated list
    makes each pass double the last — the reducer trap design §3.1 names."""
    prior = [findings.make(findings.DUPLICATE_INVOICE)]
    out = enrichment.signals_node(_state(sample_invoice, signals=prior), conn=runtime_conn)
    assert prior[0] not in out["signals"]


def test_graph_routes_validate_into_signals():
    """Until M4-C splices classify/audit in front, `signals` is what a clean
    invoice reaches instead of END."""
    graph = enrichment.build_enrichment_graph()
    rendered = graph.get_graph().draw_mermaid()
    assert "signals" in rendered
