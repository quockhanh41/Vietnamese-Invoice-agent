"""Money aggregation. The rule here is measured, not derived from the spec."""

from decimal import Decimal

from invoice_agent.domain import money
from invoice_agent.extraction.models import InvoiceLine


def line(**kw) -> InvoiceLine:
    return InvoiceLine(**kw)


def test_note_lines_are_not_money():
    """MUTATION GUARD. TChat=4 is a NOTE line — description only, no money.
    Summing it is wrong, and it is the first of the three facts that make the
    measured rule work (found in `hokinhdoanh_…`)."""
    lines = [
        line(item_kind=1, amount=Decimal("1000")),
        line(item_kind=4, description="Ghi chú: giao hàng trong 3 ngày"),
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
