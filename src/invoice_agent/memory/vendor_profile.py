"""Vendor entity memory, materialized (design doc §2.2, §7).

WHAT IS EXACT AND WHAT IS DERIVED. The persisted state is
`(invoice_count, total_amount, sum_sq_amount)` — three exactly-accumulable
values. `avg_amount` and `stddev_amount` are DERIVED from them by `derive`, and
`scripts/rebuild_vendor_profiles.py` calls the same function on the same triple.
That is why the two paths agree bit-for-bit and why M4-B's zero-drift criterion
is exact rather than tolerance-based.

Measured 2026-07-31: the accumulator form and the two-pass form both return
43993644.37602409504841441727 on the same input. The usual objection to
`sum(x^2) - n*mean^2` is catastrophic cancellation, which is a FLOATING-POINT
problem; Postgres NUMERIC and Python Decimal are exact decimal arithmetic. Keep
every value in this module a Decimal — one `float()` and the criterion is gone.

WHICH FIELDS THIS OWNS. Statistical fields only. `rejection_count`,
`reviewer_notes` and `is_trusted` are written by M5's `learn` node from human
decisions, and this module must never clear them: a statistics update that reset
a reviewer's accumulated judgement would destroy the only part of the profile a
person authored.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, select

from invoice_agent.db import models
from invoice_agent.domain import anomaly


def derive(
    count: int, total: Decimal, sum_sq: Decimal
) -> tuple[Decimal | None, Decimal | None]:
    """(avg, stddev) from the exact triple. Sample stddev, matching `statistics.stdev`.

    `stddev` is None below two samples: one invoice has no spread, and returning
    0 would make every later amount look infinitely anomalous — and divide by
    zero in `anomaly.price_outlier`, which guards on exactly this None.
    """
    if count <= 0:
        return None, None
    avg = total / count
    if count < 2:
        return avg, None
    variance = (sum_sq - total * total / count) / (count - 1)
    # Guard the boundary: identical amounts give a variance of exactly 0, and
    # accumulated rounding can push it a hair below, where sqrt() raises.
    if variance <= 0:
        return avg, Decimal(0)
    return avg, variance.sqrt()


def apply_invoice(
    conn: Connection,
    *,
    tax_code: str,
    amount: Decimal,
    category: str | None = None,
    department: str | None = None,
) -> None:
    """Fold ONE invoice into the vendor's profile. O(1), inside the caller's
    transaction (design §2.2: never recompute in the hot path).

    `category` and `department` are None until M4-C's `classify` exists. None
    means RECORD NOTHING — writing a null key would make `modal_category`
    return the string "None" for every vendor seen before M4-C.
    """
    row = (
        conn.execute(select(models.vendors).where(models.vendors.c.tax_code == tax_code))
        .mappings()
        .first()
    )
    if row is None:
        raise ValueError(
            f"no vendors row for {tax_code!r} — call repositories.vendors."
            f"ensure_vendor first. Creating one here would let a typo'd tax code "
            f"silently start its own history."
        )

    count = int(row["invoice_count"]) + 1
    total = Decimal(row["total_amount"]) + amount
    sum_sq = Decimal(row["sum_sq_amount"]) + amount * amount
    avg, stddev = derive(count, total, sum_sq)

    histogram: dict[str, int] = dict(row["category_histogram"] or {})
    if category is not None:
        histogram[category] = histogram.get(category, 0) + 1

    values: dict[str, Any] = {
        "invoice_count": count,
        "total_amount": total,
        "sum_sq_amount": sum_sq,
        "avg_amount": avg,
        "stddev_amount": stddev,
        "category_histogram": histogram,
    }
    if histogram:
        values["modal_category"] = max(histogram.items(), key=lambda kv: (kv[1], kv[0]))[0]
    if department is not None:
        values["modal_department"] = department

    conn.execute(
        models.vendors.update()
        .where(models.vendors.c.tax_code == tax_code)
        .values(**values)
    )


def stats_for(conn: Connection, tax_code: str) -> anomaly.VendorStats | None:
    """Adapter onto the M2-B pure rules. Returns None for an unknown vendor,
    which `anomaly.new_vendor` reads as 'never transacted'."""
    row = (
        conn.execute(
            select(
                models.vendors.c.invoice_count,
                models.vendors.c.avg_amount,
                models.vendors.c.stddev_amount,
            ).where(models.vendors.c.tax_code == tax_code)
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return anomaly.VendorStats(
        invoice_count=int(row["invoice_count"]),
        avg_amount=Decimal(row["avg_amount"]) if row["avg_amount"] is not None else None,
        stddev_amount=(
            Decimal(row["stddev_amount"]) if row["stddev_amount"] is not None else None
        ),
    )
