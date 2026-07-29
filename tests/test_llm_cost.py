"""Pricing — and the reason there are two prices rather than one.

Measured 2026-07-29 (spec §2.2): litellm.completion_cost prices a free-tier
Groq call at $0.000748 and raises on both openrouter :free models. Delegating
to it would make cost_usd and shadow_cost_usd say the same wrong number.
"""

from decimal import Decimal

import pytest

from invoice_agent.config import DEFAULT_MODEL_CHAIN
from invoice_agent.llm.cost import (
    ACTUAL_PRICES,
    LLMCallRecord,
    actual_cost_usd,
    shadow_cost_usd,
)


def make_record(model="groq/llama-3.3-70b-versatile", **overrides) -> LLMCallRecord:
    base = {
        "model": model,
        "provider": model.split("/", 1)[0],
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "latency_ms": 120,
        "attempt": 1,
        "succeeded": True,
    }
    base.update(overrides)
    return LLMCallRecord(**base)


class TestTheTwoPrices:
    def test_free_tier_costs_zero_and_shadow_does_not(self):
        """The whole reason §2.9 has two columns. A bare $0 reads as
        'unmeasured'; $0 beside a non-zero reference price reads as 'cheap'."""
        record = make_record()
        assert actual_cost_usd(record) == Decimal(0)
        assert shadow_cost_usd(record) > Decimal(0)

    def test_shadow_price_is_gpt4o_mini_regardless_of_model(self):
        """1000 in / 200 out at $0.15/$0.60 per 1M = $0.00027 exactly."""
        for model in DEFAULT_MODEL_CHAIN:
            assert shadow_cost_usd(make_record(model)) == Decimal("0.00027")

    def test_gpt4o_mini_actual_equals_shadow(self):
        """The one model we would really pay for. Its two columns coincide,
        which is what makes the shadow column readable as a reference."""
        record = make_record("openai/gpt-4o-mini")
        assert actual_cost_usd(record) == shadow_cost_usd(record) == Decimal("0.00027")

    def test_unmapped_model_returns_zero_instead_of_raising(self):
        """Pricing is telemetry. Telemetry that can kill an invoice run is
        worse than telemetry that under-reports: the under-report is visible in
        the report, a crash loses the invoice."""
        assert actual_cost_usd(make_record("groq/some-model-added-tomorrow")) == Decimal(0)

    def test_prices_are_decimal_not_float(self):
        """The column is Numeric and per-invoice figures live near $0.0005."""
        assert isinstance(shadow_cost_usd(make_record()), Decimal)

    def test_a_failed_call_is_still_priced_on_its_input_tokens(self):
        """§2.9: a 429 burned the input tokens even though it produced nothing."""
        record = make_record("openai/gpt-4o-mini", completion_tokens=0, succeeded=False)
        assert actual_cost_usd(record) == Decimal("0.00015")


class TestDriftGuards:
    """These fail when the WORLD changed, not when this repo did. Failing means
    a decision is owed, not that a bug was introduced."""

    def test_hardcoded_shadow_price_still_matches_litellm(self):
        import litellm
        from litellm.utils import Choices, Message, ModelResponse, Usage

        response = ModelResponse(
            model="gpt-4o-mini",
            choices=[Choices(message=Message(content="x"), finish_reason="stop", index=0)],
            usage=Usage(prompt_tokens=1000, completion_tokens=200, total_tokens=1200),
        )
        response.model = "openai/gpt-4o-mini"
        # Quantize before comparing: measured 2026-07-29, completion_cost
        # returns the FLOAT 0.00026999999999999995, so a bare
        # Decimal(str(...)) comparison fails on binary float error rather than
        # on a real price change — a drift guard that cries wolf gets deleted.
        measured = Decimal(str(litellm.completion_cost(completion_response=response))).quantize(
            Decimal("0.00000001")
        )
        assert measured == shadow_cost_usd(make_record("openai/gpt-4o-mini")), (
            "litellm revised its gpt-4o-mini price. Old shadow_cost_usd rows are "
            "no longer comparable to new ones — decide, do not just re-pin."
        )

    @pytest.mark.parametrize("model", DEFAULT_MODEL_CHAIN)
    def test_every_configured_model_is_priced(self, model):
        """Adding a model to the chain without pricing it would silently record
        $0 for a call that cost money."""
        assert model in ACTUAL_PRICES
