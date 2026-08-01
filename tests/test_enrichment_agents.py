"""classify -> audit -> signals, and what must stay true through it."""

from invoice_agent.agents.models import AuditVerdict, ClassificationResult
from invoice_agent.domain import findings
from invoice_agent.graphs import enrichment


def _state(invoice, **over):
    base = {
        "invoice_id": "INV-TEST", "source_path": "x.xml", "pdf_path": None,
        "source_type": "xml", "strategy": "xml", "extracted": invoice,
        "extraction_attempts": 0, "current_errors": [], "error_history": [],
        "findings": [], "llm_calls": [], "signals": [], "status": "",
        "classification": None, "audit_verdict": None,
    }
    base.update(over)
    return base


def test_graph_order_is_validate_classify_audit_signals():
    rendered = enrichment.build_enrichment_graph().get_graph().draw_mermaid()
    for node in ("classify", "audit", "signals"):
        assert node in rendered
    assert rendered.index("classify") < rendered.index("signals")


def test_a_dispute_reaches_the_findings_channel(monkeypatch, sample_invoice):
    monkeypatch.setattr(
        enrichment, "run_classify",
        lambda *a, **k: ClassificationResult(
            category="MARKETING", department="MKT", confidence=0.9, reasoning="a"
        ),
    )
    monkeypatch.setattr(
        enrichment, "run_audit",
        lambda *a, **k: AuditVerdict(
            agrees=False, proposed_category="TOOLS", proposed_department="OPS",
            reason="b",
        ),
    )
    state = _state(sample_invoice)
    state.update(enrichment.classify_node(state, gateway=object()))
    out = enrichment.audit_node(state, gateway=object())
    assert findings.CLASSIFICATION_DISPUTED in [f.code for f in out["signals"]]


def test_manual_queue_path_never_reaches_the_agents(sample_invoice):
    """An invoice that gave up on extraction has nothing to classify, and
    spending two model calls on it would be paying for a known dead end."""
    out = enrichment.classify_node(_state(None), gateway=object())
    assert out == {}


def test_agent_nodes_return_deltas(monkeypatch, sample_invoice):
    """`signals` accumulates with operator.add — the reducer trap design §3.1
    names, and the one M3 already hit once."""
    monkeypatch.setattr(
        enrichment, "run_classify",
        lambda *a, **k: ClassificationResult(
            category="MARKETING", department="MKT", confidence=0.9, reasoning="a"
        ),
    )
    monkeypatch.setattr(
        enrichment, "run_audit",
        lambda *a, **k: AuditVerdict(
            agrees=False, proposed_category="TOOLS", proposed_department="OPS", reason="b"
        ),
    )
    prior = [findings.make(findings.NEW_VENDOR)]
    state = _state(sample_invoice, signals=prior)
    state.update(enrichment.classify_node(state, gateway=object()))
    out = enrichment.audit_node(state, gateway=object())
    assert prior[0] not in out["signals"]
