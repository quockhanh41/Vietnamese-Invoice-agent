"""Validation rules. Every threshold and guard here is measured, not assumed."""

from datetime import date
from decimal import Decimal

from invoice_agent.domain import findings, validation
from invoice_agent.extraction.models import InvoiceData, InvoiceLine


def invoice(**kw) -> InvoiceData:
    base = dict(
        template_no="1", invoice_series="C26TAA", invoice_number="00001234",
        issue_date=date(2026, 7, 1), seller_tax_code="0100000003",
        total_amount=Decimal("1100"), lines=[
            InvoiceLine(item_kind=1, amount=Decimal("1000")),
        ],
        vat_amount=Decimal("100"),
    )
    base.update(kw)
    return InvoiceData(**base)


def codes(inv) -> set[str]:
    return {f.code for f in validation.validate(inv)}


def test_clean_invoice_produces_no_findings():
    assert validation.validate(invoice()) == []


def test_header_arithmetic_mismatch_is_flagged():
    assert findings.ARITHMETIC_MISMATCH in codes(invoice(total_amount=Decimal("9999")))


def test_subtotal_check_skipped_when_subtotal_is_zero():
    """MUTATION GUARD — spec §2.3, the correction to M1's record.

    `ktx_banhang_cccd` is KHMSHDon=2, a non-VAT sales invoice: TgTCThue and
    TgTThue are BOTH 0 while the single line carries the full 3,410,000. M1
    recorded `Σ ThTien == TgTCThue` as unconditionally exact. Applied that way
    this invoice is flagged — a false positive on a real, valid,
    tax-authority-signed document. Drop the non-zero guard and this goes red.
    """
    non_vat = invoice(
        template_no="2", subtotal=Decimal("0"), vat_amount=Decimal("0"),
        total_amount=Decimal("3410000"),
        lines=[InvoiceLine(item_kind=1, amount=Decimal("3410000"))],
    )
    assert validation.validate(non_vat) == []


def test_vat_total_check_skipped_when_any_line_omits_line_vat():
    """MUTATION GUARD — spec §2.3, second half.

    `dienluc_gtgt` and `phongvu_gtgt` state TSuat per line but NOT TThue; only
    the header carries VAT. Comparing Σ TThue (= 0) against the header flags
    both. Drop the all-lines-present guard and this goes red.
    """
    header_only_vat = invoice(
        subtotal=Decimal("706522"), vat_amount=Decimal("56522"),
        total_amount=Decimal("763044"),
        lines=[InvoiceLine(item_kind=1, amount=Decimal("706522"),
                           vat_rate=Decimal("0.08"), vat_amount=None)],
    )
    assert findings.VAT_TOTAL_MISMATCH not in codes(header_only_vat)


def test_subtotal_mismatch_is_flagged_when_the_check_does_apply():
    """The guards must not disable the check entirely — that would pass the two
    tests above while validating nothing."""
    wrong = invoice(
        subtotal=Decimal("999"), vat_amount=Decimal("100"), total_amount=Decimal("1100"),
        lines=[InvoiceLine(item_kind=1, amount=Decimal("1000"))],
    )
    assert findings.SUBTOTAL_MISMATCH in codes(wrong)


def test_vat_total_mismatch_is_flagged_when_every_line_states_vat():
    wrong = invoice(
        vat_amount=Decimal("999"), total_amount=Decimal("1999"),
        lines=[InvoiceLine(item_kind=1, amount=Decimal("1000"),
                           vat_rate=Decimal("0.1"), vat_amount=Decimal("100"))],
    )
    assert findings.VAT_TOTAL_MISMATCH in codes(wrong)


def test_validate_never_raises_on_a_sparse_invoice():
    """§3.3: DATA errors travel as findings; EXTRACTION errors belong to the
    parser. validate() must never raise, however little the invoice carries."""
    sparse = InvoiceData(
        template_no="2", invoice_series="X", invoice_number="1",
        issue_date=date(2026, 7, 1), total_amount=Decimal("0"), lines=[],
    )
    assert isinstance(validation.validate(sparse), list)


def test_rounded_unit_price_is_tolerated():
    """MUTATION GUARD. Real case measured 2026-07-28 in lienket_gtgt_7dong:
    3 x 398181.33 = 1194543.99 but ThTien = 1194544. The seller divided the
    unit price back out of a rounded line total. Set the tolerance to 0 and
    this valid invoice is flagged."""
    inv = invoice(
        total_amount=Decimal("1194544"), vat_amount=Decimal("0"),
        lines=[InvoiceLine(item_kind=1, quantity=Decimal("3"),
                           unit_price=Decimal("398181.33"), amount=Decimal("1194544"))],
    )
    assert findings.LINE_AMOUNT_MISMATCH not in codes(inv)


def test_line_amount_error_beyond_tolerance_is_flagged():
    inv = invoice(
        total_amount=Decimal("500"), vat_amount=Decimal("0"),
        lines=[InvoiceLine(item_kind=1, quantity=Decimal("2"),
                           unit_price=Decimal("100"), amount=Decimal("500"))],
    )
    assert findings.LINE_AMOUNT_MISMATCH in codes(inv)


def test_line_vat_rounding_is_tolerated():
    """Measured max deviation 0.48, in BOTH directions — tax is rounded to the
    đồng. 1000 x 0.10 = 100, and a stated 100.4 is the same invoice."""
    inv = invoice(
        vat_amount=Decimal("100.4"), total_amount=Decimal("1100.4"),
        lines=[InvoiceLine(item_kind=1, amount=Decimal("1000"),
                           vat_rate=Decimal("0.10"), vat_amount=Decimal("100.4"))],
    )
    assert findings.LINE_VAT_MISMATCH not in codes(inv)


def test_wrong_vat_rate_is_flagged():
    """The 3% defect group 'ghi 8% nhưng tính 10%'."""
    inv = invoice(
        vat_amount=Decimal("100"), total_amount=Decimal("1100"),
        lines=[InvoiceLine(item_kind=1, amount=Decimal("1000"),
                           vat_rate=Decimal("0.08"), vat_amount=Decimal("100"))],
    )
    assert findings.LINE_VAT_MISMATCH in codes(inv)


def test_words_disagreeing_with_digits_is_flagged():
    """The 2% defect group 'chữ-vs-số lệch'."""
    inv = invoice(total_in_words="Chín mươi nghìn đồng")  # 90000 vs total 1100
    assert findings.WORDS_DIGITS_MISMATCH in codes(inv)


def test_words_matching_digits_produces_nothing():
    inv = invoice(total_amount=Decimal("90000"), vat_amount=Decimal("0"),
                  lines=[InvoiceLine(item_kind=1, amount=Decimal("90000"))],
                  total_in_words="Chín mươi nghìn đồng")
    assert validation.validate(inv) == []


def test_unparseable_words_are_not_a_finding():
    """None means 'could not read', which is not the invoice's fault."""
    inv = invoice(total_in_words="viết tay không đọc được")
    assert findings.WORDS_DIGITS_MISMATCH not in codes(inv)


def test_missing_item_kind_raises_an_info_flag_but_still_sums():
    """Spec §5.4: include as goods so the arithmetic stays right, and record the
    assumption so a reviewer can see it."""
    inv = invoice(lines=[InvoiceLine(item_kind=None, amount=Decimal("1000"))])
    result = codes(inv)
    assert findings.ITEM_KIND_MISSING in result
    assert findings.ARITHMETIC_MISMATCH not in result


def test_bad_seller_tax_code_is_flagged():
    assert findings.TAX_CODE_CHECKSUM_INVALID in codes(invoice(seller_tax_code="0100000004"))


def test_missing_seller_tax_code_is_flagged():
    assert findings.TAX_CODE_MISSING in codes(invoice(seller_tax_code=None))


def test_missing_buyer_tax_code_is_not_flagged():
    assert validation.validate(invoice(buyer_tax_code=None)) == []
