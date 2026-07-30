"""The non-LLM classification baseline: `keywords_vi` substring matching.

Principle 3 (CLAUDE.md): every LLM metric is reported beside a non-LLM baseline,
and classification must beat this by >=15 points or that node does not need an
LLM. This module is that baseline, and it is also the instrument that measured
the corpus label defect on 2026-07-30 (spec 2026-07-31-m4a §1.1 D3).

PURE BY DESIGN. `classify` takes the keyword map as a parameter so tests need no
database, and `load_keywords` reads the SAME seeded `expense_categories` rows the
system classifies against — not a second copy that could drift from the seed.
"""

import unicodedata
from collections.abc import Mapping, Sequence

from sqlalchemy import Connection, select

from invoice_agent.db import models


def _fold(text: str) -> str:
    """Normalise for comparison. NFC because the corpus and the seed are both
    written in composed form; comparing NFC to NFD silently matches nothing."""
    return unicodedata.normalize("NFC", text).casefold()


def classify(text: str, keywords: Mapping[str, Sequence[str]]) -> str | None:
    """Longest matching keyword wins; ties break by declaration order.

    Returns None when nothing matches. NEVER guesses a default: a 249/285
    no-match rate is exactly what exposed the corpus defect, and a guessing
    baseline would have reported it as ordinary error instead of absent signal.
    """
    if not text:
        return None
    haystack = _fold(text)
    best: str | None = None
    best_len = 0
    for category, terms in keywords.items():
        for term in terms:
            if len(term) > best_len and _fold(term) in haystack:
                best, best_len = category, len(term)
    return best


def load_keywords(conn: Connection) -> dict[str, list[str]]:
    """Read `keywords_vi` from the seeded reference data."""
    stmt = select(
        models.expense_categories.c.code, models.expense_categories.c.keywords_vi
    ).order_by(models.expense_categories.c.code)
    return {row.code: list(row.keywords_vi) for row in conn.execute(stmt)}
