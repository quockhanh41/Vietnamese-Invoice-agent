"""Money aggregation over invoice lines, and Vietnamese amount-words parsing.

THE SUBTOTAL RULE IS MEASURED, NOT DERIVED. Measured 2026-07-28 on
tests/fixtures/real_samples/ (spec §2.1):

    sum(ThTien) == TgTTTBSo                                   -> WRONG on 6/8
    sum(ThTien | TChat==1, fallback ext['Amount']) + TgTThue  -> holds  8/8

Three independent facts make it work, each found in a different real file and
none of them guessable from the regulation:

  TChat == 1            excludes note lines            (hokinhdoanh_…)
  ext['Amount']         recovers VNPT's amount, ThTien=0 (vnpt_…)
  + TgTThue             ThTien is PRE-TAX on VAT invoices (viettelpost_, phongvu_)

`goods_subtotal` is the single place the first two live. validation.py and
accounting.py both call it; neither reimplements it.
"""

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from invoice_agent.extraction import xml_schema as sch
from invoice_agent.extraction.models import InvoiceLine


def line_amount(line: InvoiceLine) -> Decimal:
    """One line's money, with the VNPT fallback. Note lines are worth zero.

    The fallback applies ONLY when ThTien is absent or zero. Measured
    2026-07-28: four vendors carry a similar-looking TTKhac key holding the
    GROSS amount (lienket_ line 1: 1720144 == ThTien 1592726 + TThue 127418),
    so preferring `ext` whenever present would double-count tax on 4/8 files.
    """
    if line.item_kind == sch.ITEM_KIND_NOTE:
        return Decimal("0")
    if line.amount:
        return line.amount
    raw = line.ext.get(sch.EXT_KEY_LINE_AMOUNT)
    if raw:
        try:
            return Decimal(raw)
        except InvalidOperation:
            # Vendor extension text is untrusted and free-form. A bad value here
            # is not an extraction failure — §3.3 keeps those in the parser — so
            # fall through to zero and let the arithmetic check report the gap.
            return Decimal("0")
    return Decimal("0")


def goods_subtotal(lines: Sequence[InvoiceLine]) -> Decimal:
    """Pre-tax total of the money-bearing lines.

    `item_kind is None` counts as GOODS (spec §5.4): TChat is present on 15/15
    lines across 8/8 fixtures, so absence is unobserved, and whether QĐ-1450
    permits omitting it waits on the NĐ 70/2025 check M1.2 still owes. Dropping
    such a line would under-count and raise a FALSE ARITHMETIC_MISMATCH on a
    valid invoice — the exact false positive the headline metric measures.
    validation.py additionally emits ITEM_KIND_MISSING so the assumption is
    visible to a reviewer.
    """
    return sum((line_amount(line) for line in lines), Decimal("0"))
