"""Two narrow agents, and the Python that compares them.

`dispute` is the safety-relevant function here: the models return opinions and
PYTHON decides whether that becomes a flag. No response model has a field that
reaches `invoice_flags`.
"""



from invoice_agent.agents import audit as audit_mod
from invoice_agent.agents import classification
from invoice_agent.agents.models import AuditVerdict, ClassificationResult
from invoice_agent.domain import findings


def _result(category="MARKETING", department="MKT"):
    return ClassificationResult(
        category=category, department=department, confidence=0.9, reasoning="vì"
    )


def test_dispute_is_none_when_the_auditor_agrees():
    verdict = AuditVerdict(agrees=True, reason="hợp lý")
    assert classification.dispute(_result(), verdict) is None


def test_dispute_raises_the_flag_when_categories_differ():
    verdict = AuditVerdict(
        agrees=False, proposed_category="OFFICE_SUPPLIES",
        proposed_department="ADMIN", reason="mực in là văn phòng phẩm",
    )
    finding = classification.dispute(_result(), verdict)
    assert finding is not None
    assert finding.code == findings.CLASSIFICATION_DISPUTED
    assert finding.raised_by == "agent_disagreement"


def test_dispute_carries_BOTH_opinions_and_BOTH_reasons():
    """Design §6.4 hands the reviewer both opinions with their reasons. A
    finding that recorded only the disagreement would make the human re-derive
    what the agents already said."""
    verdict = AuditVerdict(
        agrees=False, proposed_category="OFFICE_SUPPLIES",
        proposed_department="ADMIN", reason="mực in là văn phòng phẩm",
    )
    detail = classification.dispute(_result(), verdict).detail
    assert detail["classifier_category"] == "MARKETING"
    assert detail["auditor_category"] == "OFFICE_SUPPLIES"
    assert "vì" in detail["classifier_reason"]
    assert "mực in" in detail["auditor_reason"]


def test_disagreement_without_an_alternative_is_not_a_dispute():
    """`agrees=False` with no proposed category is an objection the reviewer
    cannot act on, and it must not raise a flag that says two labels conflict."""
    verdict = AuditVerdict(agrees=False, reason="không chắc")
    assert classification.dispute(_result(), verdict) is None


def test_agreeing_flag_and_matching_category_disagree_is_resolved_by_the_CATEGORY():
    """`agrees` is prose; the categories are the data. If a model says it
    disagrees but proposes the same label, there is nothing to dispute."""
    verdict = AuditVerdict(
        agrees=False, proposed_category="MARKETING", proposed_department="MKT",
        reason="thực ra là đồng ý",
    )
    assert classification.dispute(_result(), verdict) is None


def test_classify_fences_invoice_text(monkeypatch, sample_invoice):
    """Untrusted invoice text reaches the prompt only through sanitize.wrap."""
    seen = {}

    def fake_call_structured(gateway, *, response_model, prompt, **kw):
        seen["prompt"] = prompt
        return ClassificationResult(
            category="MARKETING", department="MKT", confidence=0.5, reasoning="x"
        )

    monkeypatch.setattr(classification, "call_structured", fake_call_structured)
    classification.classify(sample_invoice, gateway=object())
    assert "<invoice_data>" in seen["prompt"]


def test_audit_runs_on_the_auditor_chain(monkeypatch, sample_invoice):
    """The whole point of design §6.3. If `audit` used the default chain it
    could land on the Classifier's own model and the metric would be noise."""
    from invoice_agent.config import Settings

    seen = {}

    def fake_call_structured(gateway, *, response_model, prompt, model=None, **kw):
        seen["model"] = model
        return AuditVerdict(agrees=True, reason="ok")

    monkeypatch.setattr(audit_mod, "call_structured", fake_call_structured)
    audit_mod.audit(sample_invoice, _result(), gateway=object())
    assert seen["model"] == Settings().auditor_model_chain
