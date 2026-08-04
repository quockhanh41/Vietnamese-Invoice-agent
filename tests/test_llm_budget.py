"""The estimate must count the messages that would ACTUALLY be sent.

`describe()` alone is roughly half a short prompt: `call_structured` appends
`_schema_instruction`'s full JSON schema, and the system prompt is another
constant block. An estimate missing those under-counts every invoice by about
the same amount, which makes any threshold derived from it meaningless.
"""

from invoice_agent.llm.budget import classify_messages, estimate_prompt_tokens


def test_the_estimate_matches_the_messages_the_gateway_really_sends(sample_invoice, monkeypatch):
    """Pins the estimator to the production prompt by capturing the real one.

    Patches litellm.completion — the same seam tests/helpers/hostile_gateway.py
    uses — so the whole prompt-assembly path runs for real.
    """
    import json

    from tests.helpers.fake_llm import ok_response

    from invoice_agent.agents import classification
    from invoice_agent.config import Settings
    from invoice_agent.llm import gateway as gw_module
    from invoice_agent.llm.gateway import LLMGateway

    captured = {}

    def _completion(**kw):
        captured["messages"] = kw["messages"]
        return ok_response(
            json.dumps(
                {"category": "OFFICE_SUPPLIES", "department": "ADMIN",
                 "confidence": 0.9, "reasoning": "ok"},
                ensure_ascii=False,
            ),
            prompt=900,
            completion=50,
        )

    monkeypatch.setattr(gw_module.litellm, "completion", _completion)
    gw = LLMGateway(Settings(_env_file=None, groq_api_keys=["k"], llm_mode="live"))
    gw.mode = "live"
    classification.classify(sample_invoice, gateway=gw)

    assert classify_messages(sample_invoice) == captured["messages"]


def test_a_bigger_invoice_estimates_more_tokens(sample_invoice):
    small = estimate_prompt_tokens(sample_invoice, model="groq/llama-3.3-70b-versatile")

    # InvoiceData is frozen (extraction/models.py) — `model_copy(update=...)`
    # is the frozen-model equivalent of an in-place attribute mutation.
    fat = sample_invoice.model_copy(update={"lines": list(sample_invoice.lines) * 40})
    big = estimate_prompt_tokens(fat, model="groq/llama-3.3-70b-versatile")

    assert big > small


def test_an_unknown_model_does_not_raise(sample_invoice):
    """Same rule as gateway._count_tokens: returns 0 rather than killing a run."""
    assert estimate_prompt_tokens(sample_invoice, model="nonexistent/model-xyz") >= 0
