"""Structural anomaly signals (design doc §3.2 `signals`, §7 entity memory).

STRUCTURAL ONLY — no fuzzy string matching, no similarity library. Decision
taken 2026-07-28: the corpus that would justify a text-similarity threshold does
not exist until M2-C, and the headline metric is flag FALSE-POSITIVE rate, so an
untuned similarity channel costs more than it earns. If M6's eval shows fraud
recall < 0.90 with structural signals alone, a description channel is added THEN,
with before/after numbers (principle 2). If it is dropped, the reason is
recorded — that is portfolio content, not a failure.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from invoice_agent.domain import findings

# NEAR_DUP_WINDOW_DAYS is still PROVISIONAL AND UNMEASURED: its only consumer is
# M4-D's serial join, so measuring it here would be measuring nothing.
NEAR_DUP_WINDOW_DAYS = 7

# Measured 2026-07-31 across the 300-invoice corpus, scoring each invoice against
# its vendor's OTHER invoices — which is what `signals` sees, because M4-D
# persists after the fan-out join. Numbers and the full sweep are in
# docs/notes/2026-07-31-m4b-vendor-memory.md.
#
# Before M4-B this rule detected 0 of 6. Two causes, both now fixed: statistics
# that included the invoice being scored (a single extreme point's z against a
# sample containing itself is bounded by (n-1)/sqrt(n) = 1.79 at n=5, below this
# threshold at every realistic vendor size), and a corpus that paired half the
# group with new vendors having no history at all.
PRICE_OUTLIER_Z = Decimal("3")
PRICE_OUTLIER_MIN_INVOICES = 5


@dataclass(frozen=True)
class VendorStats:
    invoice_count: int
    avg_amount: Decimal | None
    stddev_amount: Decimal | None


@dataclass(frozen=True)
class InvoiceKey:
    seller_tax_code: str
    invoice_number: str
    issue_date: date
    total_amount: Decimal


def new_vendor(stats: VendorStats | None) -> findings.Finding | None:
    """Never transacted before. Carries `raised_by='vendor_memory'` — the
    producer as a reviewer understands it, not the module it lives in."""
    if stats is None or stats.invoice_count == 0:
        return findings.make(findings.NEW_VENDOR)
    return None


def price_outlier(amount: Decimal, stats: VendorStats) -> findings.Finding | None:
    """Z-score against the vendor's own history.

    Returns None when the history is too thin or has no spread. Both guards
    exist to protect the false-positive rate: a vendor with two invoices has no
    distribution, and a zero stddev makes every different amount look infinitely
    anomalous (and divides by zero).
    """
    if (
        stats.invoice_count < PRICE_OUTLIER_MIN_INVOICES
        or not stats.avg_amount
        or not stats.stddev_amount
    ):
        return None
    z = abs(amount - stats.avg_amount) / stats.stddev_amount
    if z >= PRICE_OUTLIER_Z:
        return findings.make(
            findings.PRICE_OUTLIER, amount=amount, avg=stats.avg_amount, z_score=z
        )
    return None


def near_duplicate(
    candidate: InvoiceKey, priors: Sequence[InvoiceKey]
) -> findings.Finding | None:
    """Same seller, same total, close in time, DIFFERENT number.

    An identical natural key is an EXACT duplicate, owned by the UNIQUE index
    and DUPLICATE_INVOICE. This is the softer case: a vendor re-issuing an
    almost identical invoice under a new number to be paid twice.
    """
    for prior in priors:
        if (
            prior.seller_tax_code == candidate.seller_tax_code
            and prior.total_amount == candidate.total_amount
            and prior.invoice_number != candidate.invoice_number
            and abs((candidate.issue_date - prior.issue_date).days) <= NEAR_DUP_WINDOW_DAYS
        ):
            return findings.make(
                findings.NEAR_DUPLICATE,
                matched_invoice_number=prior.invoice_number,
                total_amount=candidate.total_amount,
            )
    return None
