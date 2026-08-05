"""Pinning a rater means the fallback chain is GONE, not merely deprioritised.

Roadmap §1: "the number M6 publishes must come from one rater." M4-C's Auditor
number came from a two-model mixture because Gemini rate-limited 177 times and
the chain silently demoted. M6-B then measured that even a replayed run is a
mixture: Auditor 199/285 fallback vs 86/285 primary.

A silent demotion is the failure mode this exists to prevent, so the test
asserts the chain has exactly ONE entry — not that the primary is first.
"""

import pytest

from invoice_agent.config import Settings


def test_a_pinned_gateway_has_no_fallback_at_all():
    from scripts.eval_classification import pinned_gateways

    base = Settings(_env_file=None, groq_api_keys=["k"], gemini_api_keys=["k"])
    classifier, auditor = pinned_gateways(
        base,
        classifier_model="groq/llama-3.3-70b-versatile",
        auditor_model="gemini/gemini-3.5-flash",
    )
    assert classifier.model_chain == ["groq/llama-3.3-70b-versatile"]
    assert auditor.model_chain == ["gemini/gemini-3.5-flash"]


def test_the_two_pinned_raters_must_not_be_the_same_model():
    """Design §6.3: the Auditor exists to have a DIFFERENT failure mode. A
    self-graded run would report agreement as if it were corroboration."""
    from scripts.eval_classification import pinned_gateways

    base = Settings(_env_file=None, groq_api_keys=["k"])
    with pytest.raises(ValueError, match="same model"):
        pinned_gateways(
            base,
            classifier_model="groq/llama-3.3-70b-versatile",
            auditor_model="groq/llama-3.3-70b-versatile",
        )


def test_the_two_pinned_raters_must_not_share_a_family_even_with_different_names():
    """Design §6.3's invariant is a different FAILURE MODE, not a different
    model NAME. `groq/llama-3.3-70b-versatile` and `groq/llama-3.1-8b-instant`
    are different strings but both `meta-llama` (agents/families.py) — the
    exact case a name-only equality check misses (review 2026-08-05: this
    pair was silently ACCEPTED before the guard compared families instead of
    raw strings)."""
    from scripts.eval_classification import pinned_gateways

    base = Settings(_env_file=None, groq_api_keys=["k"])
    with pytest.raises(ValueError, match="family"):
        pinned_gateways(
            base,
            classifier_model="groq/llama-3.3-70b-versatile",
            auditor_model="groq/llama-3.1-8b-instant",
        )


def test_persistent_failure_is_recorded_unmeasured_not_dropped():
    """The INV-0029 precedent: six Gemini 503s produced no extraction, and it
    was reported unmeasured rather than scored as a failure. Dropping it would
    silently shrink N; scoring it as wrong would blame the model for the
    provider."""
    from scripts.eval_classification import call_with_retry

    calls = {"n": 0}

    def always_429():
        calls["n"] += 1
        raise RuntimeError("RateLimitError: 429 Too Many Requests")

    result, unmeasured = call_with_retry(always_429, attempts=3, backoff_seconds=0)
    assert result is None
    assert unmeasured is not None and "429" in unmeasured
    assert calls["n"] == 3, "must retry the SAME model, not fall through"


def test_a_transient_failure_still_returns_a_result():
    from scripts.eval_classification import call_with_retry

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("RateLimitError: 429")
        return "ok"

    result, unmeasured = call_with_retry(flaky, attempts=3, backoff_seconds=0)
    assert (result, unmeasured) == ("ok", None)


def test_pinning_the_auditor_actually_reaches_the_single_model(monkeypatch, sample_invoice):
    """`pinned_gateways` alone builds a gateway with the right `.model_chain`,
    but `AuditorAgent.audit()` does not read it — it takes an explicit `model`
    keyword (agents/audit.py) that defaults to the CONFIGURED chain, not the
    gateway's. `evaluate()` must pass `model=auditor_gateway.model_chain`
    explicitly at the call site (review 2026-08-05, replacing an earlier
    monkeypatch-based approach) or a "pinned" Auditor would silently reach the
    full `DEFAULT_AUDITOR_CHAIN` on every call — exactly the leak Task 4 is
    told to watch for ("if two appear, the pin leaked")."""
    from scripts.eval_classification import pinned_gateways

    from invoice_agent.agents import audit as audit_mod
    from invoice_agent.agents.models import AuditVerdict, ClassificationResult

    base = Settings(_env_file=None, groq_api_keys=["k"], gemini_api_keys=["k"])
    _classifier, auditor = pinned_gateways(
        base,
        classifier_model="groq/llama-3.3-70b-versatile",
        auditor_model="gemini/gemini-3.5-flash",
    )

    seen = {}

    def fake_call_structured(gateway, *, response_model, prompt, model=None, **kw):
        seen["model"] = model
        return AuditVerdict(agrees=True, reason="ok")

    monkeypatch.setattr(audit_mod, "call_structured", fake_call_structured)

    proposal = ClassificationResult(
        category="MARKETING", department="MKT", confidence=0.9, reasoning="vì"
    )
    audit_mod.audit(
        sample_invoice, proposal, gateway=auditor, model=auditor.model_chain
    )
    assert seen["model"] == ["gemini/gemini-3.5-flash"]
