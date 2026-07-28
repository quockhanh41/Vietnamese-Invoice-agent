"""Generator-side money and Vietnamese amount-words.

WRITTEN INDEPENDENTLY OF `domain/money.py`, ON PURPOSE. That module parses
words -> number; this one renders number -> words. Two directions, two
implementations, no shared table.

If both directions came from one table, a shared misconception — writing 51 as
'năm mươi một' instead of the correct 'năm mươi mốt' — would round-trip GREEN
and fail on every real invoice. The 2% "chữ-vs-số lệch" defect group would then
be testing this module against itself.

Verified 2026-07-28: reproduces 7/7 real TgTTTBChu strings exactly, and
round-trips through domain.money.parse_amount_words on 13 amounts.
"""

from dataclasses import dataclass
from decimal import Decimal
from random import Random

# Vietnamese numerals — data, so Vietnamese (CLAUDE.md).
_UNITS = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
_SCALES = ((10**9, "tỷ"), (10**6, "triệu"), (1000, None))  # None -> the `thousand` argument


def _group(value: int, leading: bool) -> list[str]:
    """One 3-digit group. `leading` suppresses the 'không trăm' filler."""
    hundreds, tens, units = value // 100, (value // 10) % 10, value % 10
    out: list[str] = []
    if hundreds:
        out += [_UNITS[hundreds], "trăm"]
    elif not leading and (tens or units):
        # 'không trăm' — measured in dienluc_ (763044 -> '...nghìn không trăm
        # bốn mươi bốn'). 'lẻ' does the same job elsewhere; both are real.
        out += ["không", "trăm"]

    if tens == 0:
        if units and (hundreds or not leading):
            out += ["lẻ", _UNITS[units]]     # newimage_: 702000 -> 'bảy trăm lẻ hai'
        elif units:
            out += [_UNITS[units]]
    elif tens == 1:
        out += ["mười"]                       # ktx_: 3410000 -> 'bốn trăm mười'
        if units == 1:
            out += ["một"]
        elif units == 5:
            out += ["lăm"]
        elif units:
            out += [_UNITS[units]]
    else:
        out += [_UNITS[tens], "mươi"]
        if units == 1:
            out += ["mốt"]                    # viettelpost_: 51 -> 'năm mươi mốt'
        elif units == 5:
            out += ["lăm"]
        elif units:
            out += [_UNITS[units]]
    return out


def to_amount_words(amount: Decimal, thousand: str = "nghìn") -> str:
    """`Decimal('3500000')` -> `'Ba triệu năm trăm nghìn đồng'`.

    `thousand` is 'nghìn' or 'ngàn'. Both are real: vnpt_ writes 'ngàn', the
    other seven fixtures write 'nghìn', so the corpus must contain both or the
    parser's handling of one of them is never exercised.
    """
    remaining = int(amount)
    if remaining == 0:
        return "Không đồng"

    words: list[str] = []
    leading = True
    for divisor, name in _SCALES:
        group, remaining = divmod(remaining, divisor)
        if group:
            words += _group(group, leading) + [name or thousand]
            leading = False
    if remaining:
        words += _group(remaining, leading)

    sentence = " ".join(words)
    return sentence[0].upper() + sentence[1:] + " đồng"


@dataclass(frozen=True)
class LineMoney:
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    vat_rate: Decimal
    vat_amount: Decimal


def make_line_money(
    rng: Random, *, vat_rate: Decimal, odd_unit_price: bool = False
) -> LineMoney:
    """One line's money.

    `odd_unit_price=True` reproduces the `lienket_` shape measured at M1:
    3 x 398181.33 = 1194543.99 while ThTien = 1194544, because the seller
    divided the unit price back out of a rounded line total. A generator that
    always multiplies forward divides evenly and never produces it — leaving
    validation.py's 0.01 tolerance untested by the whole corpus.
    """
    if odd_unit_price:
        quantity = Decimal("3")
        total = Decimal(rng.randrange(500_000, 5_000_000))
        if total % 3 == 0:
            total += Decimal("1")
        unit_price = (total / quantity).quantize(Decimal("0.01"))
        amount = total                       # deliberately != quantity * unit_price
    else:
        quantity = Decimal(rng.randint(1, 50))
        unit_price = Decimal(rng.randrange(10_000, 2_000_000))
        amount = quantity * unit_price
    vat_amount = (amount * vat_rate).quantize(Decimal("1"))
    return LineMoney(quantity, unit_price, amount, vat_rate, vat_amount)
