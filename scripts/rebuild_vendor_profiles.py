"""Recompute every vendor profile from `invoices` — the escape hatch design §2.2
requires for anything materialized from a derivable source.

AND COMPARE. A rebuild nobody diffs against the incremental path is a second way
to be wrong. `--check` recomputes WITHOUT writing and exits non-zero on any
disagreement, which is the mode CI and the tests drive.

Statistical fields only. `rejection_count`, `reviewer_notes` and `is_trusted`
came from a human and are not derivable from `invoices`; a rebuild that reset
them would destroy the only part of the profile nothing can reconstruct.
"""

import argparse
import sys
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, func, select

from invoice_agent.db import models
from invoice_agent.db.engine import get_engine
from invoice_agent.memory.vendor_profile import derive

_STAT_FIELDS = ("invoice_count", "total_amount", "sum_sq_amount", "avg_amount", "stddev_amount")


def _aggregate(conn: Connection) -> dict[str, dict[str, Any]]:
    """Ground truth, straight from `invoices`."""
    amount = models.invoices.c.total_amount
    stmt = select(
        models.invoices.c.seller_tax_code,
        func.count().label("invoice_count"),
        func.coalesce(func.sum(amount), 0).label("total_amount"),
        func.coalesce(func.sum(amount * amount), 0).label("sum_sq_amount"),
    ).group_by(models.invoices.c.seller_tax_code)

    out: dict[str, dict[str, Any]] = {}
    for row in conn.execute(stmt).mappings():
        count = int(row["invoice_count"])
        total = Decimal(row["total_amount"])
        sum_sq = Decimal(row["sum_sq_amount"])
        avg, stddev = derive(count, total, sum_sq)
        out[row["seller_tax_code"]] = {
            "invoice_count": count,
            "total_amount": total,
            "sum_sq_amount": sum_sq,
            "avg_amount": avg,
            "stddev_amount": stddev,
        }
    return out


def check(conn: Connection) -> list[str]:
    """Describe every disagreement between the materialized rows and the truth."""
    truth = _aggregate(conn)
    drift: list[str] = []
    stored_rows = conn.execute(select(models.vendors)).mappings().all()
    for stored in stored_rows:
        tax_code = stored["tax_code"]
        expected = truth.get(
            tax_code,
            {"invoice_count": 0, "total_amount": Decimal(0), "sum_sq_amount": Decimal(0),
             "avg_amount": None, "stddev_amount": None},
        )
        for field in _STAT_FIELDS:
            have, want = stored[field], expected[field]
            if have is None and want is None:
                continue
            if have is None or want is None or Decimal(have) != Decimal(want):
                drift.append(f"{tax_code}.{field}: stored={have} rebuilt={want}")
    for tax_code in set(truth) - {r["tax_code"] for r in stored_rows}:
        drift.append(f"{tax_code}: has invoices but no vendors row")
    return drift


def recompute(conn: Connection) -> dict[str, dict[str, Any]]:
    """Write the truth back. Judgement fields are not in `_STAT_FIELDS` and are
    therefore never named in the UPDATE."""
    truth = _aggregate(conn)
    for tax_code, values in truth.items():
        conn.execute(
            models.vendors.update()
            .where(models.vendors.c.tax_code == tax_code)
            .values(**values)
        )
    return truth


def main(conn: Connection | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="report drift and exit non-zero; write nothing",
    )
    args = parser.parse_args()

    def _run(c: Connection) -> None:
        if args.check:
            drift = check(c)
            for line in drift:
                print(line)
            print(f"{len(drift)} field(s) drifted")
            sys.exit(1 if drift else 0)
        rebuilt = recompute(c)
        print(f"rebuilt {len(rebuilt)} vendor profile(s)")

    if conn is not None:
        _run(conn)
    else:
        with get_engine().begin() as c:
            _run(c)



if __name__ == "__main__":
    main()
