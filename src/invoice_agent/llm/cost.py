"""What a call cost, and what it would have cost.

Design doc §2.9 gives `llm_calls` two money columns, and the difference between
them is the point. `cost_usd` is what we actually paid — $0 on every free tier
in the configured chain. `shadow_cost_usd` prices the same tokens at
gpt-4o-mini, so the report can say "$0 in practice on free tier; $0.0005 per
invoice at reference price" instead of a bare $0 that reads as "you did not
measure".

Measured 2026-07-29 (spec §2.2) against litellm 1.93.0: `completion_cost`
prices groq/llama-3.3-70b-versatile at $0.000748 for a call that costs nothing,
and RAISES "isn't mapped yet" on both openrouter :free models. Delegating to it
would collapse the two columns into one wrong number and crash on 2 of 6 models
in the chain. Hence explicit tables.
"""

from dataclasses import dataclass
from decimal import Decimal

_PER_MILLION = Decimal(1_000_000)

# gpt-4o-mini reference price, USD per 1M tokens. Source: roadmap §2.2
# (2026-07-25). Re-measured 2026-07-29 against litellm 1.93.0's own map, which
# agrees to the cent: $0.000270 for 1000 in / 200 out. Kept hardcoded anyway so
# a future revision of that map cannot silently make historical
# shadow_cost_usd rows incomparable to new ones —
# tests/test_llm_cost.py::test_hardcoded_shadow_price_still_matches_litellm
# turns the drift into a failing build instead.
SHADOW_PRICE = (Decimal("0.15"), Decimal("0.60"))

_FREE = (Decimal(0), Decimal(0))

# What each model in DEFAULT_MODEL_CHAIN actually bills this project, USD per
# 1M tokens. Everything but openai/* runs on a free tier (roadmap §2.1: Groq
# free + key rotation, Gemini AI Studio free, OpenRouter :free), so the honest
# figure is zero — not the provider's list price.
ACTUAL_PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "groq/llama-3.3-70b-versatile": _FREE,
    "groq/openai/gpt-oss-120b": _FREE,
    "gemini/gemini-3.5-flash": _FREE,
    "openrouter/qwen/qwen3-next-80b-a3b-instruct:free": _FREE,
    "openrouter/meta-llama/llama-3.3-70b-instruct:free": _FREE,
    # The only model we would really pay for. Its actual price is deliberately
    # the same pair as SHADOW_PRICE — that identity is asserted by a test, and
    # it is what makes the shadow column legible as a reference rather than an
    # unrelated third number.
    "openai/gpt-4o-mini": SHADOW_PRICE,
}


@dataclass(frozen=True)
class LLMCallRecord:
    """One ATTEMPT against one model — not one logical call.

    Frozen for the same reason `domain.findings.Finding` is: a record of what
    happened must not be editable after the fact.

    This is the `LLMCallRecord` design doc §3.1 names in `EnrichmentState`,
    where it accumulates under `Annotated[list[...], operator.add]`. A single
    `generate()` can produce several of these — two rate-limited keys then a
    success is three records — and §2.9 requires all three, because the two
    failures burned input tokens.
    """

    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    attempt: int
    succeeded: bool
    error: str | None = None


def _price(rates: tuple[Decimal, Decimal], record: LLMCallRecord) -> Decimal:
    input_rate, output_rate = rates
    return (
        Decimal(record.prompt_tokens) * input_rate
        + Decimal(record.completion_tokens) * output_rate
    ) / _PER_MILLION


def actual_cost_usd(record: LLMCallRecord) -> Decimal:
    """What we paid. Unknown models price at zero rather than raising — see the
    module docstring's rule about telemetry never killing a run. The parametrized
    `test_every_configured_model_is_priced` is what stops that leniency from
    quietly becoming permanent."""
    return _price(ACTUAL_PRICES.get(record.model, _FREE), record)


def shadow_cost_usd(record: LLMCallRecord) -> Decimal:
    """What the same tokens would have cost at gpt-4o-mini, whichever model
    actually answered. Always the same rates — that is what makes the number
    comparable across a six-model fallback chain."""
    return _price(SHADOW_PRICE, record)
