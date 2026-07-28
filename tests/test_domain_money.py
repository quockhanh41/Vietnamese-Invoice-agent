"""Money aggregation. The rule here is measured, not derived from the spec."""

from decimal import Decimal

import pytest

from invoice_agent.domain import money
from invoice_agent.extraction.models import InvoiceLine


def line(**kw) -> InvoiceLine:
    return InvoiceLine(**kw)


def test_note_lines_are_not_money():
    """MUTATION GUARD. TChat=4 is a NOTE line — description only, no money.
    Summing it is wrong, and it is the first of the three facts that make the
    measured rule work (found in `hokinhdoanh_…`).

    THE NOTE LINE CARRIES AN AMOUNT ON PURPOSE. Fixed 2026-07-28 after the
    first version gave it `amount=None`: `line_amount` returns 0 for an
    empty line anyway, so the assertion held whether or not the ITEM_KIND_NOTE
    branch existed — measured by replacing that branch with `if False:` and
    watching the test stay GREEN. A note line with money is exactly the shape
    the guard exists for, and exactly what M2-C can seed as a defect.
    """
    lines = [
        line(item_kind=1, amount=Decimal("1000")),
        line(item_kind=4, amount=Decimal("500"), description="Ghi chú: giao hàng trong 3 ngày"),
    ]
    assert money.goods_subtotal(lines) == Decimal("1000")


def test_note_line_with_money_is_still_excluded_via_the_ext_fallback():
    """The other way a note line can carry money. `line_amount` checks
    ITEM_KIND_NOTE before consulting ext, so a note line with an ext Amount is
    still worth zero — without that ordering, VNPT-style extensions would drag
    note lines back into the sum."""
    lines = [
        line(item_kind=1, amount=Decimal("1000")),
        line(item_kind=4, amount=Decimal("0"), ext={"Amount": "700"}),
    ]
    assert money.goods_subtotal(lines) == Decimal("1000")


def test_vnpt_amount_comes_from_ext():
    """MUTATION GUARD. VNPT writes ThTien=0 and puts the real amount in
    TTKhac['Amount']. No amount of reading QĐ-1450 predicts this; it was
    measured. Drop the fallback and the invoice silently totals to zero."""
    lines = [line(item_kind=1, amount=Decimal("0"), ext={"Amount": "3500000"})]
    assert money.goods_subtotal(lines) == Decimal("3500000")


def test_real_amount_wins_over_the_ext_fallback():
    """The fallback is for ThTien=0 ONLY. If both are present the element wins —
    four vendors carry a similar-looking TTKhac key holding the GROSS amount,
    and preferring ext would double-count tax on half the corpus."""
    lines = [line(item_kind=1, amount=Decimal("1592726"), ext={"Amount": "1720144"})]
    assert money.goods_subtotal(lines) == Decimal("1592726")


def test_missing_item_kind_counts_as_goods():
    """Spec §5.4. Under-counting drops real money and produces a FALSE
    arithmetic mismatch; over-counting a note line is at least visible."""
    lines = [line(item_kind=None, amount=Decimal("500"))]
    assert money.goods_subtotal(lines) == Decimal("500")


def test_empty_lines_total_zero():
    assert money.goods_subtotal([]) == Decimal("0")


def test_line_amount_of_a_note_line_is_zero_not_none():
    assert money.line_amount(line(item_kind=4, description="ghi chú")) == Decimal("0")


def test_unparseable_ext_amount_is_ignored_not_crashed():
    """`ext` is verbatim vendor text. A non-numeric value there must not take
    down the whole invoice — that would be an EXTRACTION failure raised from
    the DATA layer, the wrong side of the §3.3 split."""
    lines = [line(item_kind=1, amount=Decimal("0"), ext={"Amount": "n/a"})]
    assert money.goods_subtotal(lines) == Decimal("0")


# Every value is REAL, read from tests/fixtures/real_samples/ on 2026-07-28.
REAL_AMOUNT_WORDS = [
    ("Bảy trăm sáu mươi ba nghìn không trăm bốn mươi bốn đồng", "763044"),
    ("Chín mươi nghìn đồng", "90000"),
    ("Ba triệu bốn trăm mười nghìn  đồng", "3410000"),
    ("Mười triệu chín trăm chín mươi bốn nghìn ba trăm chín mươi bảy đồng", "10994397"),
    ("Bảy trăm lẻ hai nghìn đồng", "702000"),
    ("Hai mươi ba triệu bảy trăm tám mươi nghìn hai trăm năm mươi đồng", "23780250"),
    ("Năm mươi mốt nghìn năm trăm chín mươi chín đồng", "51599"),
    ("Ba  triệu năm trăm ngàn đồng./.", "3500000"),
]


@pytest.mark.parametrize("text,expected", REAL_AMOUNT_WORDS)
def test_parses_all_eight_real_amount_words(text, expected):
    """MUTATION GUARD. §10.3 uses TgTTTBChu as a SECOND INDEPENDENT ENCODING of
    the total, so the parser has to survive real data: 'ngàn' as well as
    'nghìn', 'lẻ' and 'không trăm' as zero-fillers, 'mốt' for 1 after a tens
    word, double spaces, and a './.' suffix."""
    assert money.parse_amount_words(text) == Decimal(expected)


@pytest.mark.parametrize(
    "text,expected",
    [
        # Unobserved in the corpus but structurally real and unambiguous, so
        # supported rather than rejected (spec §2.5, open question 3).
        ("Hai mươi lăm nghìn đồng", "25000"),
        ("Hai mươi tư nghìn đồng", "24000"),
        ("Hai mươi nhăm nghìn đồng", "25000"),
    ],
)
def test_supports_lam_tu_nham_though_no_fixture_has_them(text, expected):
    assert money.parse_amount_words(text) == Decimal(expected)


@pytest.mark.parametrize("text", [None, "", "   ", "không đọc được", "abc xyz"])
def test_unparseable_returns_none_never_zero(text):
    """`None` means 'could not read', `Decimal(0)` means 'the amount is zero'.
    Conflating them makes validation compare 0 against a real total and raise
    WORDS_DIGITS_MISMATCH on every invoice it cannot parse — the same
    None-vs-zero distinction xml_schema.money() already makes."""
    assert money.parse_amount_words(text) is None
