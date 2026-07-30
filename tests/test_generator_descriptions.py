"""The description pools are what make `category` a function of the invoice.

Measured 2026-07-30 before this module existed: the keywords_vi baseline agreed
with the manifest label on 5 of 285 XML invoices (1.8%), below the 16.7% chance
rate, because descriptions were near-constant boilerplate drawn independently of
the label. See spec 2026-07-31-m4a §1.1 D3.
"""

from random import Random

from scripts.generator import descriptions

CATEGORIES = (
    "MARKETING", "IT_SERVICES", "OFFICE_SUPPLIES",
    "LOGISTICS", "UTILITIES", "TOOLS",
)


def test_every_corpus_category_has_a_pool():
    for category in CATEGORIES:
        assert category in descriptions.CATEGORY_POOLS
        assert descriptions.CATEGORY_POOLS[category], f"{category} pool is empty"


def test_every_pool_covers_all_three_tiers():
    """A pool missing the keyword tier makes the baseline unbeatable-downward;
    one missing synonym/jargon makes it unbeatable-upward. Both break the M6
    gate, in opposite directions."""
    for category, pool in descriptions.CATEGORY_POOLS.items():
        tiers = {d.tier for d in pool}
        assert tiers == {"keyword", "synonym", "jargon"}, f"{category}: {tiers}"


def test_tier_mix_is_a_distribution():
    assert set(descriptions.TIER_MIX) == {"keyword", "synonym", "jargon"}
    assert abs(sum(descriptions.TIER_MIX.values()) - 1.0) < 1e-9


def test_draw_is_deterministic_for_a_given_rng():
    a = [descriptions.draw("MARKETING", Random(7)) for _ in range(5)]
    b = [descriptions.draw("MARKETING", Random(7)) for _ in range(5)]
    assert a == b


def test_draw_returns_text_from_the_requested_category():
    rng = Random(42)
    texts = {d.text for d in descriptions.CATEGORY_POOLS["LOGISTICS"]}
    for _ in range(60):
        assert descriptions.draw("LOGISTICS", rng) in texts


def test_descriptions_are_unique_across_categories():
    """Two categories sharing a description string would make that invoice's
    label unrecoverable from its text — the exact defect this module fixes."""
    seen: dict[str, str] = {}
    for category, pool in descriptions.CATEGORY_POOLS.items():
        for entry in pool:
            assert entry.text not in seen, (
                f"{entry.text!r} is in both {seen.get(entry.text)} and {category}"
            )
            seen[entry.text] = category
