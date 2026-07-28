"""Defect and shape injectors.

Each function here reproduces something a REAL invoice does. The corpus exists
to be as awkward as reality: if it is easier, every number measured on it is
optimistic, and the eval report's headline false-positive rate is flattering
rather than true.
"""

import dataclasses
from decimal import Decimal

from scripts.generator.templates import InvoiceSpec, LineSpec


def make_note_line(line_no: int) -> LineSpec:
    """TChat=4 — description only, no money (`hokinhdoanh_`)."""
    return LineSpec(
        line_no=line_no, item_kind=4,
        description="Ghi chú: giao hàng trong 3 ngày làm việc",
        unit=None, quantity=None, unit_price=None, amount=Decimal("0"),
        vat_rate=None, vat_amount=None, ext_amount=None, self_closing=False,
    )


def make_vnpt_line(line_no: int, amount: Decimal) -> LineSpec:
    """ThTien=0 with the real amount in TTKhac['Amount'], and self-closing
    `<SLuong />`. No amount of reading QĐ-1450 predicts this; it was measured."""
    return LineSpec(
        line_no=line_no, item_kind=1,
        description="Thu lệ phí ký túc xá Học kỳ 2, Năm học 2025 - 2026",
        unit=None, quantity=None, unit_price=None, amount=Decimal("0"),
        vat_rate=None, vat_amount=None, ext_amount=amount, self_closing=True,
    )


def make_non_vat_spec(base: InvoiceSpec) -> InvoiceSpec:
    """KHMSHDon=2: subtotal and VAT headers are ZERO while the lines carry the
    full amount. M2-B §2.3 measured that comparing Σ ThTien to TgTCThue here
    flags a valid invoice."""
    total = sum((line.amount for line in base.lines), Decimal("0"))
    lines = [dataclasses.replace(line, vat_rate=None, vat_amount=None) for line in base.lines]
    return dataclasses.replace(
        base, template_no="2", lines=lines,
        subtotal=None, vat_total=None, total_amount=total,
    )


def make_header_only_vat_spec(base: InvoiceSpec) -> InvoiceSpec:
    """TSuat stated per line, TThue absent — only the header carries VAT.
    M2-B §2.3 measured that Σ TThue is then 0 and the comparison is meaningless."""
    lines = [dataclasses.replace(line, vat_amount=None) for line in base.lines]
    subtotal = sum((line.amount for line in lines), Decimal("0"))
    vat_total = (subtotal * Decimal("0.1")).quantize(Decimal("1"))
    return dataclasses.replace(
        base, lines=lines, subtotal=subtotal, vat_total=vat_total,
        total_amount=subtotal + vat_total,
    )


from scripts.generator.amounts import to_amount_words
from scripts.generator.vendors import invalidate_checksum


def seed_arithmetic_error(base: InvoiceSpec) -> InvoiceSpec:
    """6% group. The declared total disagrees with the lines.

    Note this is a DATA error, not an extraction error (§3.3): the document
    stays perfectly readable, and it travels onward as a flag for a human.
    """
    return dataclasses.replace(
        base, total_amount=base.total_amount + Decimal("100000"), total_in_words=None
    )


def seed_words_mismatch(base: InvoiceSpec) -> InvoiceSpec:
    """2% group. TgTTTBChu is a SECOND, INDEPENDENT encoding of the total
    (§10.3), so making the words disagree is a real cross-field defect.
    Rendered through this module's own number->words, never the parser's."""
    return dataclasses.replace(
        base, total_in_words=to_amount_words(base.total_amount + Decimal("1000"))
    )


def seed_bad_tax_code(base: InvoiceSpec) -> InvoiceSpec:
    """4% group. Perturbing the check digit is guaranteed to invalidate, so no
    checksum algorithm is needed here (spec §2.1)."""
    return dataclasses.replace(base, seller_tax_code=invalidate_checksum(base.seller_tax_code))


def seed_wrong_vat_rate(base: InvoiceSpec) -> InvoiceSpec:
    """3% group: states 8% but charges 10%. The stated rate is what a reviewer
    reads; the charged amount is what they pay."""
    lines = [
        dataclasses.replace(
            line, vat_rate=Decimal("0.08"),
            vat_amount=(line.amount * Decimal("0.10")).quantize(Decimal("1")),
        )
        for line in base.lines
        if line.item_kind != 4
    ]
    vat_total = sum((line.vat_amount for line in lines if line.vat_amount is not None), Decimal("0"))
    subtotal = sum((line.amount for line in lines), Decimal("0"))
    return dataclasses.replace(
        base, lines=lines, subtotal=subtotal, vat_total=vat_total,
        total_amount=subtotal + vat_total, total_in_words=None,
    )
