"""Journal entry construction (design doc §2.7)."""

from datetime import date
from decimal import Decimal

from invoice_agent.domain.accounting import CategoryAccounts, build_journal_entry
from invoice_agent.extraction.models import InvoiceData, InvoiceLine

# Real TT133 numbers, seeded by scripts/seed_reference.py.
OFFICE = CategoryAccounts(
    debit_account="6422", debit_name="Chi phí quản lý doanh nghiệp",
    vat_account="1331", vat_name="Thuế GTGT được khấu trừ",
    credit_account="331", credit_name="Phải trả cho người bán",
)


def inv(**kw) -> InvoiceData:
    base = dict(
        template_no="1", invoice_series="C26TAA", invoice_number="1",
        issue_date=date(2026, 7, 1), seller_tax_code="0100000003",
        lines=[InvoiceLine(item_kind=1, amount=Decimal("3250000"))],
        vat_amount=Decimal("260000"), total_amount=Decimal("3510000"),
    )
    base.update(kw)
    return InvoiceData(**base)


def test_builds_the_textbook_office_supplies_entry():
    """Design doc §2.7 gives this exact entry:
    Nợ 6422 3.250.000 / Nợ 1331 260.000 / Có 331 3.510.000"""
    lines = build_journal_entry(inv(), OFFICE)
    assert [(x.account_code, x.debit, x.credit) for x in lines] == [
        ("6422", Decimal("3250000"), Decimal("0")),
        ("1331", Decimal("260000"), Decimal("0")),
        ("331", Decimal("0"), Decimal("3510000")),
    ]


def test_entry_balances():
    lines = build_journal_entry(inv(), OFFICE)
    assert sum(x.debit for x in lines) == sum(x.credit for x in lines)


def test_journal_entry_balances_on_non_vat_invoice():
    """MUTATION GUARD. Measured spec §2.3: a non-VAT invoice reports
    TgTCThue = 0 while its line carries the full amount. Building the debit
    from `invoice.subtotal` emits `Nợ 6422 0 / Có 331 3.410.000` — unbalanced,
    and refused by ck_journal_balanced. Must use money.goods_subtotal."""
    non_vat = inv(
        template_no="2", subtotal=Decimal("0"), vat_amount=Decimal("0"),
        total_amount=Decimal("3410000"),
        lines=[InvoiceLine(item_kind=1, amount=Decimal("3410000"))],
    )
    lines = build_journal_entry(non_vat, OFFICE)
    assert sum(x.debit for x in lines) == sum(x.credit for x in lines) == Decimal("3410000")


def test_journal_entry_balances_on_arithmetic_defect_invoice():
    """SPEC CORRECTION GUARD. An invoice with total_amount disagreeing with
    lines (e.g. total 3520000 vs lines subtotal 3250000 + vat 260000) must still
    produce a BALANCED journal entry. Payable credit is subtotal + vat (3510000),
    balancing by construction. Using total_amount would create an unbalanced entry
    (debit 3510000 != credit 3520000) refused by ck_journal_balanced."""
    defect = inv(
        total_amount=Decimal("3520000"),  # arithmetic defect
        lines=[InvoiceLine(item_kind=1, amount=Decimal("3250000"))],
        vat_amount=Decimal("260000"),
    )
    lines = build_journal_entry(defect, OFFICE)
    assert sum(x.debit for x in lines) == sum(x.credit for x in lines) == Decimal("3510000")


def test_no_vat_line_when_there_is_no_vat():
    non_vat = inv(vat_amount=Decimal("0"), total_amount=Decimal("3250000"))
    assert [x.account_code for x in build_journal_entry(non_vat, OFFICE)] == ["6422", "331"]


def test_every_line_is_one_sided():
    """journal_entry_lines has CHECK (debit = 0 OR credit = 0)."""
    for x in build_journal_entry(inv(), OFFICE):
        assert x.debit == 0 or x.credit == 0


def test_no_negative_amounts():
    """journal_entry_lines has CHECK (debit >= 0 AND credit >= 0)."""
    for x in build_journal_entry(inv(), OFFICE):
        assert x.debit >= 0 and x.credit >= 0
