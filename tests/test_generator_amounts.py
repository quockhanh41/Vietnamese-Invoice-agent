"""Generator-side money. Written independently of domain/money.py — that is
what makes the 2% words-vs-digits defect group measurable instead of
self-confirming."""

import random
from decimal import Decimal

import pytest
from scripts.generator import amounts

from invoice_agent.domain.money import parse_amount_words  # TEST-ONLY

# The REAL strings from tests/fixtures/real_samples/, read 2026-07-28. The
# generator must be able to reproduce these exactly, or the corpus is written
# in a dialect no real vendor uses.
REAL = [
    (763044, "Bảy trăm sáu mươi ba nghìn không trăm bốn mươi bốn đồng"),
    (90000, "Chín mươi nghìn đồng"),
    (3410000, "Ba triệu bốn trăm mười nghìn đồng"),
    (10994397, "Mười triệu chín trăm chín mươi bốn nghìn ba trăm chín mươi bảy đồng"),
    (702000, "Bảy trăm lẻ hai nghìn đồng"),
    (23780250, "Hai mươi ba triệu bảy trăm tám mươi nghìn hai trăm năm mươi đồng"),
    (51599, "Năm mươi mốt nghìn năm trăm chín mươi chín đồng"),
]


@pytest.mark.parametrize("amount,expected", REAL)
def test_reproduces_real_fixture_wording(amount, expected):
    assert amounts.to_amount_words(Decimal(amount)) == expected


def test_ngan_variant_matches_vnpt():
    """`vnpt_` writes 'ngàn', the other seven write 'nghìn'. Both are real and
    the corpus must contain both."""
    assert amounts.to_amount_words(Decimal(3500000), thousand="ngàn") == (
        "Ba triệu năm trăm ngàn đồng"
    )


@pytest.mark.parametrize("amount", [105, 1000, 25000, 24000, 51599, 1000000000, 3500000])
def test_round_trips_through_the_independent_parser(amount):
    """The anti-circularity check made concrete: this module and
    domain/money.py were written separately, in opposite directions. Agreement
    is evidence; a shared table would make it a tautology."""
    assert parse_amount_words(amounts.to_amount_words(Decimal(amount))) == Decimal(amount)


def test_odd_unit_price_makes_quantity_times_price_disagree_with_amount():
    """Reproduces the `lienket_` case measured at M1: 3 x 398181.33 =
    1194543.99 but ThTien = 1194544. A generator that always multiplies forward
    divides evenly and never produces this — so the 0.01 tolerance in
    validation.py would never be exercised."""
    money = amounts.make_line_money(random.Random(1), vat_rate=Decimal("0.1"), odd_unit_price=True)
    product = money.quantity * money.unit_price
    assert product != money.amount
    assert abs(product - money.amount) <= Decimal("0.01")
