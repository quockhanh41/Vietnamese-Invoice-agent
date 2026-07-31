"""Entity memory, materialized (design doc §2.2).

The exact state is (invoice_count, total_amount, sum_sq_amount). `avg_amount`
and `stddev_amount` are DERIVED from it by `derive`, and the rebuild script uses
the same function — which is what makes "zero drift" an exact criterion rather
than one needing a tolerance.
"""

import statistics
from decimal import Decimal

from invoice_agent.db.repositories import vendors as vendor_repo
from invoice_agent.memory import vendor_profile

AMOUNTS = [
    Decimal("20000000"),
    Decimal("15500000"),
    Decimal("1234567.89"),
    Decimal("98765432.10"),
]


def test_derive_matches_two_pass_exactly():
    """The accumulator form and the two-pass form must agree BIT FOR BIT.

    Measured 2026-07-31: with Decimal they both return
    43993644.37602409504841441727. The `sum(x^2)` form is catastrophically
    imprecise in FLOATING POINT, which is the objection this test retires —
    Postgres NUMERIC and Python Decimal are exact decimal arithmetic.

    Mutation: compute the accumulator in float and this goes red.
    """
    n = len(AMOUNTS)
    total = sum(AMOUNTS, Decimal(0))
    sum_sq = sum((a * a for a in AMOUNTS), Decimal(0))

    avg, stddev = vendor_profile.derive(n, total, sum_sq)

    mean = total / n
    two_pass = (sum(((a - mean) ** 2 for a in AMOUNTS), Decimal(0)) / (n - 1)).sqrt()
    assert avg == mean
    assert stddev == two_pass


def test_derive_returns_none_stddev_below_two_samples():
    """One invoice has no spread. Returning 0 would make every later amount look
    infinitely anomalous; `anomaly.price_outlier` guards on None."""
    assert vendor_profile.derive(0, Decimal(0), Decimal(0)) == (None, None)
    avg, stddev = vendor_profile.derive(1, Decimal("500"), Decimal("250000"))
    assert avg == Decimal("500")
    assert stddev is None


def test_apply_invoice_accumulates_across_calls(runtime_conn):
    vendor_repo.ensure_vendor(runtime_conn, "0001000002", name="NCC A")
    for amount in AMOUNTS:
        vendor_profile.apply_invoice(runtime_conn, tax_code="0001000002", amount=amount)

    row = vendor_repo.get_vendor(runtime_conn, "0001000002")
    assert row["invoice_count"] == len(AMOUNTS)
    assert row["total_amount"] == sum(AMOUNTS, Decimal(0))
    expected = Decimal(str(statistics.stdev([float(a) for a in AMOUNTS])))
    assert abs(row["stddev_amount"] - expected) < Decimal("1")


def test_stats_for_feeds_the_pure_rules(runtime_conn):
    """`stats_for` is an adapter onto the M2-B pure functions. Those stay
    untouched — the rules must not learn about the database."""
    from invoice_agent.domain import anomaly

    vendor_repo.ensure_vendor(runtime_conn, "0001079193", name="NCC B")
    for amount in AMOUNTS:
        vendor_profile.apply_invoice(runtime_conn, tax_code="0001079193", amount=amount)

    stats = vendor_profile.stats_for(runtime_conn, "0001079193")
    assert isinstance(stats, anomaly.VendorStats)
    assert stats.invoice_count == len(AMOUNTS)
    assert anomaly.new_vendor(stats) is None


def test_stats_for_unknown_vendor_is_none(runtime_conn):
    """None, not a zeroed VendorStats — `anomaly.new_vendor` treats None as
    'never transacted', which is exactly what an absent row means."""
    assert vendor_profile.stats_for(runtime_conn, "0009999999") is None


def test_apply_invoice_never_touches_judgement_fields(runtime_conn):
    """`rejection_count`, `reviewer_notes` and `is_trusted` belong to M5's
    `learn`. A statistics update that clears them would silently erase a
    reviewer's accumulated judgement."""
    vendor_repo.ensure_vendor(runtime_conn, "0001158381", name="NCC C")
    vendor_repo.update_profile(
        runtime_conn, "0001158381", rejection_count=3, reviewer_notes="hay sai MST",
        is_trusted=True,
    )
    vendor_profile.apply_invoice(
        runtime_conn, tax_code="0001158381", amount=Decimal("1000000")
    )
    row = vendor_repo.get_vendor(runtime_conn, "0001158381")
    assert row["rejection_count"] == 3
    assert row["reviewer_notes"] == "hay sai MST"
    assert row["is_trusted"] is True


def test_category_histogram_ignores_none(runtime_conn):
    """`category` arrives only at M4-C. Until then it is None and must record
    NOTHING — writing a null key would make `modal_category` return 'None'."""
    vendor_repo.ensure_vendor(runtime_conn, "0001316768", name="NCC D")
    vendor_profile.apply_invoice(
        runtime_conn, tax_code="0001316768", amount=Decimal("1000000"), category=None
    )
    row = vendor_repo.get_vendor(runtime_conn, "0001316768")
    assert row["category_histogram"] == {}
    assert row["modal_category"] is None


def test_category_histogram_accumulates_when_present(runtime_conn):
    vendor_repo.ensure_vendor(runtime_conn, "0001395953", name="NCC E")
    for category in ("MARKETING", "MARKETING", "TOOLS"):
        vendor_profile.apply_invoice(
            runtime_conn, tax_code="0001395953", amount=Decimal("1000000"),
            category=category, department="MKT",
        )
    row = vendor_repo.get_vendor(runtime_conn, "0001395953")
    assert row["category_histogram"] == {"MARKETING": 2, "TOOLS": 1}
    assert row["modal_category"] == "MARKETING"
    assert row["modal_department"] == "MKT"
