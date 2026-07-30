"""The non-LLM baseline principle 3 requires beside every LLM metric.

This is also the instrument that found the corpus label defect (spec
2026-07-31-m4a §1.1 D3), so its own behaviour is pinned here.
"""

from invoice_agent.eval import baseline

KEYWORDS = {
    "OFFICE_SUPPLIES": ["văn phòng phẩm", "giấy a4", "mực in"],
    "LOGISTICS": ["cước", "vận chuyển", "chuyển phát"],
    "UTILITIES": ["cước viễn thông", "internet", "điện thoại"],
}


def test_matches_a_single_keyword():
    assert baseline.classify("Mua giấy A4 cho văn phòng", KEYWORDS) == "OFFICE_SUPPLIES"


def test_returns_none_rather_than_guessing():
    """A baseline that guessed would have hidden the defect this module found:
    249 of 285 descriptions matched NO keyword, and a default category would
    have reported that as ordinary error rather than as absent signal."""
    assert baseline.classify("Dịch vụ tư vấn quản trị doanh nghiệp", KEYWORDS) is None


def test_longest_keyword_wins():
    """`cước` (LOGISTICS) is a substring-neighbour of `cước viễn thông`
    (UTILITIES). Shortest-match would send every phone bill to LOGISTICS."""
    text = "Cước viễn thông tháng 07"
    assert baseline.classify(text, KEYWORDS) == "UTILITIES"


def test_match_is_case_and_accent_form_insensitive():
    assert baseline.classify("MỰC IN laser", KEYWORDS) == "OFFICE_SUPPLIES"


def test_ties_break_by_declaration_order():
    """Two categories with an equally long match must resolve deterministically,
    or the baseline's score changes between runs on the same corpus."""
    tied = {"A": ["abcd"], "B": ["abcd"]}
    assert baseline.classify("xx abcd yy", tied) == "A"


def test_empty_text_is_none_not_a_crash():
    assert baseline.classify("", KEYWORDS) is None


def test_load_keywords_reads_the_seeded_rows(runtime_conn):
    """The only DB-touching function M4-A added, and nothing exercised it.

    Its first real execution would otherwise be inside M4-C's agent comparison
    or M6.7's eval — i.e. the moment a headline number depends on it. What is
    actually checked here is the shape the classifier needs: a mapping of
    category code to a LIST of terms. `keywords_vi` is a Postgres TEXT[], and a
    driver returning it as a string would make every `term in haystack` a
    per-character substring test that silently matches almost anything.
    """
    from scripts.seed_reference import CATEGORIES, seed

    seed(runtime_conn)
    loaded = baseline.load_keywords(runtime_conn)

    seeded = {code: list(keywords) for code, _n, _d, _v, _c, keywords in CATEGORIES}
    assert set(loaded) == set(seeded)
    for code, terms in loaded.items():
        assert isinstance(terms, list), f"{code}: {type(terms)} — not a list of terms"
        assert terms == seeded[code]

    # And it composes with the classifier it exists to feed.
    assert baseline.classify("Mua giấy A4 cho văn phòng", loaded) == "OFFICE_SUPPLIES"
