"""The Auditor must not share a family with the Classifier (design §6.3).

"A stronger model of the SAME family shares the same failure mode. It will agree
with the Classifier's mistake more confidently." So the axis is family, not
strength — and NOT model name.

Measured 2026-07-31: entries 1 and 5 of DEFAULT_MODEL_CHAIN are the same model,
Llama 3.3 70B Instruct, reached through groq and openrouter. Excluding by name —
which is what roadmap 4.1 asks for — would let a Classifier on entry 1 be audited
by entry 5: the same weights grading their own generation. That is invisible to
name-based exclusion, which is why the chains are DECLARED disjoint instead.
"""


from invoice_agent.agents import families
from invoice_agent.config import Settings


def test_every_configured_model_has_a_declared_family():
    """An unmapped model is the failure mode this table exists to prevent: it
    would silently compare as 'no family' and pass the disjointness test."""
    settings = Settings()
    unmapped = [
        m for m in [*settings.llm_model_chain, *settings.auditor_model_chain]
        if m not in families.MODEL_FAMILIES
    ]
    assert not unmapped, f"models with no declared family: {unmapped}"


def test_the_two_llama_entries_share_a_family():
    """The exact case name-based exclusion misses. If this ever goes green, the
    table has stopped describing reality."""
    assert families.family_of("groq/llama-3.3-70b-versatile") == families.family_of(
        "openrouter/meta-llama/llama-3.3-70b-instruct:free"
    )


def test_openai_substring_does_not_imply_family():
    """`groq/openai/gpt-oss-120b` and `openai/gpt-4o-mini` both contain 'openai'
    and are different families — which is why families are declared rather than
    parsed out of model ids."""
    assert families.family_of("groq/openai/gpt-oss-120b") != families.family_of(
        "openai/gpt-4o-mini"
    )


def test_auditor_shares_no_family_with_classifier():
    """Over the CONFIGURED chains, not the defaults — an operator who edits .env
    into a self-grading setup must get a red test, not a silent metric."""
    settings = Settings()
    classifier = {families.family_of(m) for m in settings.llm_model_chain}
    auditor = {families.family_of(m) for m in settings.auditor_model_chain}
    overlap = classifier & auditor
    assert not overlap, (
        f"the Auditor could run on the Classifier's own family {sorted(overlap)} — "
        f"design §6.3: same family, same failure mode"
    )


def test_auditor_chain_is_not_empty():
    assert Settings().auditor_model_chain
