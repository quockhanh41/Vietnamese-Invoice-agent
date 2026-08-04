"""M4-C measured 95.4% classification accuracy without recording which model
produced each answer, and M5-B then measured 86% of live classifications being
served by the FALLBACK. The number is therefore not currently attributable.

This test pins the fix at the unit that produces it, so M6-C's single-rater
verification has something to verify.
"""

from invoice_agent.llm.cost import LLMCallRecord


def _record(model: str) -> LLMCallRecord:
    return LLMCallRecord(
        model=model, provider=model.split("/")[0], prompt_tokens=10,
        completion_tokens=5, latency_ms=1, attempt=1, succeeded=True, error=None,
    )


def test_models_used_reports_the_successful_model_per_agent():
    from scripts.eval_classification import models_used

    calls = [
        _record("groq/llama-3.3-70b-versatile"),
        _record("gemini/gemini-3.5-flash"),
    ]
    assert models_used(calls[:1]) == ["groq/llama-3.3-70b-versatile"]
    assert models_used(calls) == ["gemini/gemini-3.5-flash", "groq/llama-3.3-70b-versatile"]


def test_a_failed_attempt_is_not_credited_with_the_answer():
    """A 429 leaves a record too (design §2.9). Counting it as the rater is how
    a two-model mixture gets reported as one.

    `LLMCallRecord` is frozen — a record of what happened must not be editable
    after the fact — so the failure is constructed, not mutated.
    """
    from scripts.eval_classification import models_used

    failed = LLMCallRecord(
        model="groq/llama-3.3-70b-versatile", provider="groq", prompt_tokens=10,
        completion_tokens=0, latency_ms=1, attempt=1, succeeded=False,
        error="RateLimitError: 429",
    )
    succeeded = _record("groq/llama-3.1-8b-instant")

    assert models_used([failed, succeeded]) == ["groq/llama-3.1-8b-instant"]


def test_empty_calls_reports_nothing_rather_than_guessing():
    from scripts.eval_classification import models_used

    assert models_used([]) == []
