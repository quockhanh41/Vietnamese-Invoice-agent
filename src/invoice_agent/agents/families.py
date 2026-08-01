"""Which base model a chain entry actually is.

WHY DECLARED AND NOT PARSED. Design §6.3 puts the Auditor on a different FAMILY,
because a model of the same family shares the same failure mode and agrees with
its own kind of mistake more confidently. Deriving the family from a model id is
brittle exactly where it matters:

  * `groq/llama-3.3-70b-versatile` and
    `openrouter/meta-llama/llama-3.3-70b-instruct:free`
    are THE SAME MODEL under two providers — measured 2026-07-31. A name-based
    rule sees two different strings.
  * `groq/openai/gpt-oss-120b` and `openai/gpt-4o-mini` both contain "openai"
    and are different families entirely.

A frozen table is reviewable and wrong loudly rather than quietly. Same pattern
as `scripts/generator/vendors.py`'s frozen tax-code pool, and for the same
reason: an algorithm that can be subtly wrong proves nothing when it agrees.

ADDING A MODEL TO ANY CHAIN MEANS ADDING IT HERE. `test_every_configured_model_
has_a_declared_family` fails otherwise — an unmapped model would compare as
"no family" and slip through the disjointness check.
"""

# model id -> base-model family. Provider prefixes are deliberately ignored:
# the same weights served by two providers are one family.
MODEL_FAMILIES: dict[str, str] = {
    "groq/llama-3.3-70b-versatile": "meta-llama",
    "openrouter/meta-llama/llama-3.3-70b-instruct:free": "meta-llama",
    "groq/openai/gpt-oss-120b": "openai-oss",
    "openai/gpt-4o-mini": "openai-gpt4",
    "gemini/gemini-3.5-flash": "google-gemini",
    "openrouter/qwen/qwen3-next-80b-a3b-instruct:free": "qwen",
}


class UnknownModelFamily(KeyError):
    """Raised loudly. A model with no declared family would otherwise compare
    as equal-to-nothing and defeat the disjointness guard."""


def family_of(model_id: str) -> str:
    try:
        return MODEL_FAMILIES[model_id]
    except KeyError:
        raise UnknownModelFamily(
            f"{model_id!r} has no declared family. Add it to MODEL_FAMILIES — "
            f"an unmapped model silently passes the Auditor disjointness check "
            f"(spec 2026-07-31-m4c §1.2)."
        ) from None
