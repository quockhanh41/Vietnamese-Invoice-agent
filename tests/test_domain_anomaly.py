"""Structural anomaly signals. No fuzzy matching — see the module docstring."""

from datetime import date
from decimal import Decimal

from invoice_agent.domain import findings
from invoice_agent.domain.anomaly import (
    InvoiceKey, VendorStats, near_duplicate, new_vendor, price_outlier,
)

KNOWN = VendorStats(invoice_count=50, avg_amount=Decimal("1000"), stddev_amount=Decimal("100"))


def key(number="00001234", day=1, amount="1000", seller="0100000003"):
    return InvoiceKey(seller_tax_code=seller, invoice_number=number,
                      issue_date=date(2026, 7, day), total_amount=Decimal(amount))


def test_unknown_vendor_is_flagged():
    assert new_vendor(None).code == findings.NEW_VENDOR
    assert new_vendor(VendorStats(0, None, None)).code == findings.NEW_VENDOR


def test_known_vendor_is_not_flagged():
    assert new_vendor(KNOWN) is None


def test_price_far_from_the_mean_is_flagged():
    """The 2% defect group: 10x the usual price."""
    assert price_outlier(Decimal("10000"), KNOWN).code == findings.PRICE_OUTLIER


def test_ordinary_price_is_not_flagged():
    assert price_outlier(Decimal("1050"), KNOWN) is None


def test_no_outlier_verdict_without_enough_history():
    """A vendor with two invoices has no meaningful distribution. Computing a
    z-score from one is how a clean invoice gets flagged."""
    thin = VendorStats(invoice_count=2, avg_amount=Decimal("1000"), stddev_amount=Decimal("10"))
    assert price_outlier(Decimal("99999"), thin) is None


def test_no_outlier_verdict_when_stddev_is_zero():
    """Every prior invoice identical -> stddev 0 -> division by zero, and any
    different amount would look infinitely anomalous."""
    flat = VendorStats(invoice_count=50, avg_amount=Decimal("1000"), stddev_amount=Decimal("0"))
    assert price_outlier(Decimal("1001"), flat) is None


def test_near_duplicate_same_seller_same_total_different_number():
    """The 'wow' case: a vendor re-issues an almost identical invoice under a
    new number to be paid twice."""
    result = near_duplicate(key(number="00009999", day=2), [key(number="00001234", day=1)])
    assert result.code == findings.NEAR_DUPLICATE


def test_not_a_duplicate_when_the_seller_differs():
    other = key(seller="0311110005", number="00001234")
    assert near_duplicate(key(number="00009999"), [other]) is None


def test_not_a_duplicate_when_the_total_differs():
    assert near_duplicate(key(number="00009999", amount="2000"), [key()]) is None


def test_same_invoice_number_is_not_a_NEAR_duplicate():
    """An identical natural key is an EXACT duplicate — the UNIQUE index and
    DUPLICATE_INVOICE own that case, not this one."""
    assert near_duplicate(key(), [key()]) is None


def test_outside_the_window_is_not_flagged():
    from invoice_agent.domain import anomaly

    far = key(number="00009999", day=1 + anomaly.NEAR_DUP_WINDOW_DAYS + 1)
    assert near_duplicate(far, [key(day=1)]) is None
