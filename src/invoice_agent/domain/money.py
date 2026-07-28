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

import re
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


# Vietnamese numerals. This is DATA the system ingests, not authoring style, so
# it stays Vietnamese (CLAUDE.md language rule).
_UNITS = {
    "không": 0, "một": 1, "hai": 2, "ba": 3, "bốn": 4,
    "năm": 5, "sáu": 6, "bảy": 7, "tám": 8, "chín": 9,
}
# Units-position variants after a tens word. 51 is 'năm mươi MỐT', never
# 'năm mươi một' — measured in viettelpost_. 'lăm'/'nhăm' (5) and 'tư' (4)
# appear in NO fixture but are unambiguous standard Vietnamese, so they are
# supported and marked unobserved (spec §2.5).
_UNIT_VARIANTS = {"mốt": 1, "lăm": 5, "nhăm": 5, "tư": 4}
_SCALES = {"nghìn": 1000, "triệu": 1_000_000, "tỷ": 1_000_000_000}
# Zero-fillers. Both forms are real and mean the same thing: 'lẻ' in newimage_
# (702000), 'không trăm' in dienluc_ (763044).
_FILLERS = {"lẻ", "linh"}

_TRAILING = re.compile(r"\s*(đồng)?\s*(chẵn)?\s*(\./\.)?\s*$")


def parse_amount_words(text: str | None) -> Decimal | None:
    """`'Ba  triệu năm trăm ngàn đồng./.'` -> `Decimal('3500000')`.

    Returns None when the text cannot be read — NEVER Decimal(0). 'unreadable'
    and 'zero' are different facts and only one of them is a finding.

    Normalisation, in order, each step justified by a real value measured
    2026-07-28: collapse whitespace runs ('Ba  triệu' has two spaces, so does
    'mười nghìn  đồng'); strip the './.' suffix and a trailing 'đồng' (vnpt_);
    lowercase; and treat 'ngàn' as 'nghìn' (vnpt_ says ngàn, the other seven
    say nghìn).
    """
    if not text:
        return None
    cleaned = _TRAILING.sub("", " ".join(text.split()).lower())
    cleaned = cleaned.replace("ngàn", "nghìn")
    if not cleaned:
        return None

    total = Decimal(0)
    group = Decimal(0)
    pending: int | None = None
    saw_number = False

    for token in cleaned.split():
        if token in _UNITS:
            pending = _UNITS[token]
            saw_number = True
        elif token in _UNIT_VARIANTS:
            group += _UNIT_VARIANTS[token]
            pending = None
            saw_number = True
        elif token == "trăm":
            group += (pending or 0) * 100
            pending = None
        elif token == "mươi":
            group += (pending or 0) * 10
            pending = None
        elif token == "mười":
            # 'mười' is a standalone ten ('mười nghìn' = 10 000), unlike 'mươi'
            # which multiplies the digit before it.
            group += 10
            pending = None
            saw_number = True
        elif token in _FILLERS:
            pending = None
        elif token in _SCALES:
            if pending is not None:
                group += pending
                pending = None
            total += group * _SCALES[token]
            group = Decimal(0)
        else:
            # An unknown token means we did not understand the sentence. Return
            # None rather than a partial sum: a confidently wrong number here
            # would silently defeat the cross-check TgTTTBChu exists to provide.
            return None

    if pending is not None:
        group += pending
    total += group
    return total if saw_number else None
