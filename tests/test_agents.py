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


def test_audit_model_param_overrides_the_configured_chain(monkeypatch, sample_invoice):
    """M6-C's pinned-rater eval needs the Auditor on a single model the
    process-wide `get_settings()` singleton does not carry. `model=` is a
    plain function parameter — caller-chosen, unreachable by the LLM, so it
    cannot self-authorize — and wins over the configured chain when supplied."""
    seen = {}

    def fake_call_structured(gateway, *, response_model, prompt, model=None, **kw):
        seen["model"] = model
        return AuditVerdict(agrees=True, reason="ok")

    monkeypatch.setattr(audit_mod, "call_structured", fake_call_structured)
    audit_mod.audit(
        sample_invoice, _result(), gateway=object(), model=["gemini/gemini-3.5-flash"]
    )
    assert seen["model"] == ["gemini/gemini-3.5-flash"]


def test_agent_vocabularies_match_the_seeded_reference_data():
    """The Literal sets in `agents/models.py` are duplicated from
    `scripts.seed_reference` because `src/` must not import from `scripts/`.
    Duplication is only safe while something asserts the copies agree — the same
    arrangement `domain.findings.SOURCES` has with `db.models.FLAG_SOURCES`.

    Why closed sets at all: measured 2026-08-01, a live gemini-3.5-flash verdict
    proposed `OFFICE_REPAIR_MAINTENANCE`. Under an open `str` that validates,
    and `dispute()` then raises CLASSIFICATION_DISPUTED against a category no
    invoice can carry — inflating the dispute count and pinning override
    precision at 0 for those rows. A corrupt metric rather than a crash.
    """
    from typing import get_args

    from scripts.seed_reference import CATEGORIES as SEEDED_CATEGORIES
    from scripts.seed_reference import DEPARTMENTS as SEEDED_DEPARTMENTS

    from invoice_agent.agents import models as agent_models

    assert set(get_args(agent_models.CATEGORIES)) == {row[0] for row in SEEDED_CATEGORIES}
    assert set(get_args(agent_models.DEPARTMENTS)) == {code for code, _name in SEEDED_DEPARTMENTS}


def test_an_invented_category_is_rejected_not_disputed():
    """The failure this closes end to end: an out-of-vocabulary proposal must
    never reach `dispute()`."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AuditVerdict(agrees=False, proposed_category="OFFICE_REPAIR_MAINTENANCE", reason="x")
