# M4-B Vendor Memory & Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the materialized vendor profile, the rebuild script that keeps it honest, and the `signals` node — then fix the corpus and the two constants so `PRICE_OUTLIER` can actually fire.

**Architecture:** The profile's exact state is `(invoice_count, total_amount, sum_sq_amount)`; `avg_amount` and `stddev_amount` are derived from it by the same expression in both the incremental path and the rebuild, so the two agree bit-for-bit. `signals` reads that profile during fan-out, before anything is persisted, which is what keeps the scored invoice out of its own statistics.

**Tech Stack:** Python 3.13 · SQLAlchemy Core · Alembic · Postgres 17 · LangGraph 1.2.9 (pinned) · pytest.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-31-m4b-vendor-memory-signals-design.md`. Read §1 and §3.3 before touching the graph; they explain why the ordering is load-bearing.
- **Authoring language is English.** Invoice descriptions stay Vietnamese.
- **The generator must not import** `invoice_agent.extraction.*` or `invoice_agent.domain.*` — `tests/test_generator_independence.py` enforces it. `scripts.seed_reference` is the one allowed dependency.
- **`signals` calls no model.** It is one of the five `✗ LLM` nodes whose ratio design §3.2 calls "the design, not thrift".
- **Money is `Decimal`, never `float`.** The zero-drift criterion depends on exact decimal arithmetic; one `float()` anywhere in the accumulator destroys it.
- **Every non-obvious constant carries date + measurement + report filename** (CLAUDE.md).
- **Corpus is gitignored.** Never `git add corpus/`. Regenerate with `make generate`.
- Python is `.venv/bin/python`. Postgres must be up (`make dev`) for Tasks 1, 2, 3 and 6.
- **A task is not done until `make check` is green.** M4-B closes only when CI is green on the *pushed* commit.

---

### Task 1: The vendor profile accumulator

**Files:**
- Create: `migrations/versions/<hash>_m4b_11_vendor_sum_sq.py`
- Create: `src/invoice_agent/memory/__init__.py`, `src/invoice_agent/memory/vendor_profile.py`
- Modify: `src/invoice_agent/db/models.py` (add the column to the `vendors` table)
- Test: `tests/test_vendor_profile.py`

**Interfaces:**
- Consumes: `invoice_agent.domain.anomaly.VendorStats`, `invoice_agent.db.models.vendors`.
- Produces: `apply_invoice(conn, *, tax_code, amount, category=None, department=None) -> None`; `stats_for(conn, tax_code) -> anomaly.VendorStats | None`; `derive(count: int, total: Decimal, sum_sq: Decimal) -> tuple[Decimal | None, Decimal | None]` returning `(avg, stddev)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_vendor_profile.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_vendor_profile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'invoice_agent.memory'`

- [ ] **Step 3: Add the column to the model**

In `src/invoice_agent/db/models.py`, inside the `vendors` table, immediately after the `total_amount` column:

```python
    # Sum of squared amounts. With `invoice_count` and `total_amount` this is
    # the EXACT state of the profile; avg and stddev are derived from the three
    # by `memory.vendor_profile.derive`. Stored rather than recomputed so the
    # incremental update stays O(1) (design §2.2), and stored as the raw
    # accumulator rather than as Welford's M2 so the rebuild can compute the
    # identical value by the identical expression — which is what makes M4-B's
    # zero-drift criterion exact instead of tolerance-based.
    Column("sum_sq_amount", sa.Numeric, nullable=False, server_default="0"),
```

- [ ] **Step 4: Generate and edit the migration**

```bash
.venv/bin/alembic revision -m "m4b_11_vendor_sum_sq"
```

Edit the generated file so `upgrade()` and `downgrade()` read:

```python
def upgrade() -> None:
    op.add_column(
        "vendors",
        sa.Column("sum_sq_amount", sa.Numeric(), nullable=False, server_default="0"),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("vendors", "sum_sq_amount", schema="app")
```

Confirm `down_revision` is `"b23d0a366d02"` (the head as of 2026-07-31).

- [ ] **Step 5: Apply and confirm autogenerate is clean**

```bash
make migrate
.venv/bin/alembic revision --autogenerate -m "should_be_empty"
```

Expected: the generated file's `upgrade()` body is `pass`. **Delete that file.** A non-empty body means the model and the migration disagree.

```bash
rm migrations/versions/*should_be_empty*
```

- [ ] **Step 6: Write the implementation**

Create `src/invoice_agent/memory/__init__.py`:

```python
"""Memory: entity (vendors) and episodic (M5). Design doc §7."""
```

Create `src/invoice_agent/memory/vendor_profile.py`:

```python
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
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vendor_profile.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 8: Mutation-verify the exactness guard**

In `derive`, change `variance = (sum_sq - total * total / count) / (count - 1)` to compute in float:

```python
    variance = Decimal(str((float(sum_sq) - float(total) ** 2 / count) / (count - 1)))
```

Run `.venv/bin/python -m pytest tests/test_vendor_profile.py::test_derive_matches_two_pass_exactly -v`
Expected: RED. Restore and confirm green.

- [ ] **Step 9: Commit**

```bash
git add migrations/versions src/invoice_agent/memory src/invoice_agent/db/models.py tests/test_vendor_profile.py
git commit -m "feat(memory): vendor profile accumulator with exact derived stats

The persisted state is (invoice_count, total_amount, sum_sq_amount) and
avg/stddev are derived from it, so the rebuild can compute identical
values by the identical expression — making zero drift an exact
criterion instead of a tolerance.

Measured 2026-07-31: accumulator and two-pass forms agree bit for bit
(43993644.37602409504841441727). The usual objection to sum(x^2) is
catastrophic cancellation, which is a floating-point problem; NUMERIC and
Decimal are exact. Mutation-verified: compute in float and it goes red.

Statistical fields only. rejection_count, reviewer_notes and is_trusted
belong to M5's learn node and are never touched here."
```

---

### Task 2: The rebuild script, with a drift check

**Files:**
- Create: `scripts/rebuild_vendor_profiles.py`
- Test: `tests/test_rebuild_vendor_profiles.py`

**Interfaces:**
- Consumes: `memory.vendor_profile.derive` (Task 1).
- Produces: `recompute(conn) -> dict[str, dict]`; `check(conn) -> list[str]` returning drift descriptions; CLI `--check`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_rebuild_vendor_profiles.py`:

```python
"""The escape hatch, and the thing that stops it being decorative.

Design §2.2 calls a rebuild "mandatory when you materialize something
derivable". A rebuild nobody diffs against the incremental path is just a second
way to be wrong — the value is in the disagreement, so `--check` is what the
tests drive.
"""

import datetime
import uuid
from decimal import Decimal

import pytest

from scripts import rebuild_vendor_profiles as rebuild

from invoice_agent.db.repositories import invoices as invoice_repo
from invoice_agent.db.repositories import vendors as vendor_repo
from invoice_agent.memory import vendor_profile


def _seed(conn, tax_code: str, amounts: list[Decimal]) -> None:
    vendor_repo.ensure_vendor(conn, tax_code, name="NCC test")
    for i, amount in enumerate(amounts):
        invoice_repo.insert_row(
            conn,
            {
                "id": uuid.uuid4(),
                "template_no": "1",
                "invoice_series": "C26TAA",
                "invoice_number": f"{tax_code[-4:]}{i:04d}",
                "seller_tax_code": tax_code,
                # A real `date`, not a string — the column is sa.Date and the
                # rebuild's SUM() must aggregate typed rows.
                "issue_date": datetime.date(2026, 7, 1),
                "total_amount": amount,
                "source_type": "xml",
            },
        )
        vendor_profile.apply_invoice(conn, tax_code=tax_code, amount=amount)


def test_rebuild_agrees_with_the_incremental_path_exactly(runtime_conn):
    """Zero drift, not 'close'. Both paths derive from the same exact triple by
    the same expression, so anything other than equality is a real defect."""
    _seed(runtime_conn, "0002029477", [Decimal("20000000"), Decimal("15500000"),
                                       Decimal("1234567.89")])
    assert rebuild.check(runtime_conn) == []


def test_check_reports_drift_it_finds(runtime_conn):
    """Corrupt the materialized row and `check` must name it. Without this the
    script could return [] unconditionally and every test above would pass."""
    _seed(runtime_conn, "0002108665", [Decimal("1000000"), Decimal("3000000")])
    vendor_repo.update_profile(runtime_conn, "0002108665", avg_amount=Decimal("999"))

    drift = rebuild.check(runtime_conn)
    assert any("0002108665" in d for d in drift), drift


def test_recompute_repairs_a_corrupted_profile(runtime_conn):
    _seed(runtime_conn, "0002187850", [Decimal("1000000"), Decimal("3000000")])
    vendor_repo.update_profile(runtime_conn, "0002187850", invoice_count=99)

    rebuild.recompute(runtime_conn)
    row = vendor_repo.get_vendor(runtime_conn, "0002187850")
    assert row["invoice_count"] == 2
    assert row["total_amount"] == Decimal("4000000")
    assert rebuild.check(runtime_conn) == []


def test_check_cli_exits_nonzero_on_drift(runtime_conn, monkeypatch):
    """The EXIT CODE is the contract, not the return value. `check()` returning
    a list proves nothing about what CI sees — a script that printed drift and
    exited 0 would be green forever."""
    _seed(runtime_conn, "0002346236", [Decimal("1000000"), Decimal("3000000")])
    from invoice_agent.db.repositories import vendors as vr

    vr.update_profile(runtime_conn, "0002346236", avg_amount=Decimal("1"))

    monkeypatch.setattr("sys.argv", ["rebuild_vendor_profiles", "--check"])
    with pytest.raises(SystemExit) as exc:
        rebuild.main()
    assert exc.value.code == 1


def test_rebuild_leaves_judgement_fields_alone(runtime_conn):
    """The rebuild derives statistics from `invoices`. `rejection_count` and
    `reviewer_notes` are NOT derivable from invoices — they came from a human,
    and a rebuild that reset them would destroy the only unrecoverable part of
    the profile."""
    _seed(runtime_conn, "0002267048", [Decimal("1000000")])
    vendor_repo.update_profile(
        runtime_conn, "0002267048", rejection_count=2, reviewer_notes="ghi chú",
        is_trusted=True,
    )
    rebuild.recompute(runtime_conn)
    row = vendor_repo.get_vendor(runtime_conn, "0002267048")
    assert row["rejection_count"] == 2
    assert row["reviewer_notes"] == "ghi chú"
    assert row["is_trusted"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_rebuild_vendor_profiles.py -v`
Expected: FAIL — `ImportError: cannot import name 'rebuild_vendor_profiles'`

- [ ] **Step 3: Write the implementation**

Create `scripts/rebuild_vendor_profiles.py`:

```python
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="report drift and exit non-zero; write nothing",
    )
    args = parser.parse_args()

    with get_engine().begin() as conn:
        if args.check:
            drift = check(conn)
            for line in drift:
                print(line)
            print(f"{len(drift)} field(s) drifted")
            sys.exit(1 if drift else 0)
        rebuilt = recompute(conn)
        print(f"rebuilt {len(rebuilt)} vendor profile(s)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_rebuild_vendor_profiles.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Mutation-verify the drift check**

Change `check` to `return []` unconditionally. Run
`.venv/bin/python -m pytest tests/test_rebuild_vendor_profiles.py -v`
Expected: RED on `test_check_reports_drift_it_finds`. Restore and confirm green.

- [ ] **Step 6: Add a Makefile target**

In `Makefile`, after the `seed` target:

```make
rebuild-profiles:
	$(PY) -m scripts.rebuild_vendor_profiles
```

and add `rebuild-profiles` to the `.PHONY` line and to `help`.

- [ ] **Step 7: Commit**

```bash
git add scripts/rebuild_vendor_profiles.py tests/test_rebuild_vendor_profiles.py Makefile
git commit -m "feat(memory): rebuild_vendor_profiles with a drift check

Design §2.2 requires an escape hatch for anything materialized from a
derivable source. A rebuild nobody diffs against the incremental path is
just a second way to be wrong, so --check recomputes without writing and
exits non-zero on disagreement — that is the mode the tests drive, and
it is mutation-verified: make check() return [] and the test goes red.

Statistical fields only. rejection_count, reviewer_notes and is_trusted
are not derivable from invoices; resetting them would destroy the only
part of the profile nothing can reconstruct."
```

---

### Task 3: The `signals` node

**Files:**
- Modify: `src/invoice_agent/graphs/enrichment.py`
- Modify: `docs/diagrams/enrichment_graph.mmd` (regenerated, not hand-edited)
- Test: `tests/test_enrichment_signals.py`

**Interfaces:**
- Consumes: `memory.vendor_profile.stats_for` (Task 1), `domain.anomaly.new_vendor`, `domain.anomaly.price_outlier`.
- Produces: `signals_node(state) -> dict`; `EnrichmentState` gains `signals: Annotated[list[Finding], operator.add]`.

`classify` and `audit` do not exist until M4-C, so `signals` attaches directly after `validate`: every path `route_after_validate` currently returns `END` on routes to `signals` instead, and `signals` goes to `END`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_enrichment_signals.py`:

```python
"""`signals` — entity memory turned into findings (design §3.2).

THE ORDERING IS THE DESIGN. `signals` runs inside `enrich_one` during fan-out;
invoices are persisted by M4-D's serial node AFTER the join. So the profile it
reads is "as of batch start" and the scored invoice is absent from its own
statistics for free.

That is not a nicety. Measured 2026-07-31: a single extreme point's z-score
against a sample CONTAINING it is bounded by (n-1)/sqrt(n) — 1.79 at n=5, 2.27
at n=7 — so with PRICE_OUTLIER_Z = 3 the finding is arithmetically impossible
below n=11, against a corpus median of 7. If anything ever persists before
`signals` runs, PRICE_OUTLIER degrades silently rather than failing.
"""

from decimal import Decimal

import pytest

from invoice_agent.db.repositories import vendors as vendor_repo
from invoice_agent.domain import findings
from invoice_agent.graphs import enrichment
from invoice_agent.memory import vendor_profile


def _state(invoice, **over):
    base = {
        "invoice_id": "INV-TEST", "source_path": "x.xml", "pdf_path": None,
        "source_type": "xml", "strategy": "xml", "extracted": invoice,
        "extraction_attempts": 0, "current_errors": [], "error_history": [],
        "findings": [], "llm_calls": [], "signals": [], "status": "",
    }
    base.update(over)
    return base


def test_unknown_vendor_raises_NEW_VENDOR(runtime_conn, sample_invoice):
    out = enrichment.signals_node(_state(sample_invoice), conn=runtime_conn)
    codes = [f.code for f in out["signals"]]
    assert findings.NEW_VENDOR in codes


def test_known_vendor_does_not_raise_NEW_VENDOR(runtime_conn, sample_invoice):
    vendor_repo.ensure_vendor(runtime_conn, sample_invoice.seller_tax_code, name="NCC")
    vendor_profile.apply_invoice(
        runtime_conn, tax_code=sample_invoice.seller_tax_code, amount=Decimal("1000000")
    )
    out = enrichment.signals_node(_state(sample_invoice), conn=runtime_conn)
    assert findings.NEW_VENDOR not in [f.code for f in out["signals"]]


def test_scored_invoice_is_absent_from_its_own_stats(runtime_conn, sample_invoice):
    """The load-bearing invariant. Build a vendor history that does NOT include
    this invoice, then assert the stats `signals` reads have that exact count —
    persist the invoice first and this goes red."""
    tax_code = sample_invoice.seller_tax_code
    vendor_repo.ensure_vendor(runtime_conn, tax_code, name="NCC")
    for amount in (Decimal("1000000"), Decimal("1100000"), Decimal("900000")):
        vendor_profile.apply_invoice(runtime_conn, tax_code=tax_code, amount=amount)

    stats = vendor_profile.stats_for(runtime_conn, tax_code)
    assert stats.invoice_count == 3, (
        "the invoice being scored must not be in its own vendor statistics"
    )
    enrichment.signals_node(_state(sample_invoice), conn=runtime_conn)
    assert vendor_profile.stats_for(runtime_conn, tax_code).invoice_count == 3, (
        "signals must not write — persistence is M4-D's serial node"
    )


def test_signals_node_calls_no_model(runtime_conn, sample_invoice, monkeypatch):
    """One of the five non-LLM nodes whose ratio design §3.2 calls 'the design,
    not thrift'. A node that quietly starts calling a model breaks the cost
    story with nothing failing."""
    from invoice_agent.llm import gateway as gw

    def explode(*a, **k):
        raise AssertionError("signals must not call a model")

    monkeypatch.setattr(gw.LLMGateway, "generate", explode, raising=False)
    monkeypatch.setattr(gw.LLMGateway, "generate_with_model", explode, raising=False)
    enrichment.signals_node(_state(sample_invoice), conn=runtime_conn)


def test_signals_sees_no_intra_batch_sibling(runtime_conn, sample_invoice):
    """Two invoices from one vendor in one batch must both see the SAME profile.

    Fan-out is parallel, so if `signals` could see a sibling processed moments
    earlier, the finding would depend on which `Send` finished first — the exact
    order-dependence `test_budget_serialization` exists to disprove. At M4-B the
    expressible form is: `signals` writes nothing, so consecutive calls observe
    identical statistics.
    """
    tax_code = sample_invoice.seller_tax_code
    vendor_repo.ensure_vendor(runtime_conn, tax_code, name="NCC")
    vendor_profile.apply_invoice(
        runtime_conn, tax_code=tax_code, amount=Decimal("1000000")
    )
    before = vendor_profile.stats_for(runtime_conn, tax_code)
    enrichment.signals_node(_state(sample_invoice), conn=runtime_conn)
    enrichment.signals_node(_state(sample_invoice), conn=runtime_conn)
    assert vendor_profile.stats_for(runtime_conn, tax_code) == before


def test_missing_seller_tax_code_raises_nothing(runtime_conn, sample_invoice):
    """No seller means no vendor to remember. NEW_VENDOR would be wrong — the
    vendor may be well known and merely unidentifiable here, which is what
    validation's TAX_CODE_MISSING already reports."""
    anonymous = sample_invoice.model_copy(update={"seller_tax_code": None})
    assert enrichment.signals_node(_state(anonymous), conn=runtime_conn)["signals"] == []


def test_signals_returns_a_delta_not_the_whole_list(runtime_conn, sample_invoice):
    """`signals` accumulates with operator.add. Returning the accumulated list
    makes each pass double the last — the reducer trap design §3.1 names."""
    prior = [findings.make(findings.NEW_VENDOR)]
    out = enrichment.signals_node(_state(sample_invoice, signals=prior), conn=runtime_conn)
    assert prior[0] not in out["signals"]


def test_graph_routes_validate_into_signals():
    """Until M4-C splices classify/audit in front, `signals` is what a clean
    invoice reaches instead of END."""
    graph = enrichment.build_enrichment_graph()
    rendered = graph.get_graph().draw_mermaid()
    assert "signals" in rendered
```

Add to `tests/conftest.py`:

```python
@pytest.fixture
def sample_invoice():
    """A parsed real fixture — the arbiter this project uses for anything that
    must behave like a real invoice."""
    import pathlib

    from invoice_agent.extraction.xml_parser import parse_invoice_xml

    return parse_invoice_xml(
        pathlib.Path(
            "tests/fixtures/real_samples/viettelpost_gtgt_2.1.0.xml"
        ).read_bytes()
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_enrichment_signals.py -v`
Expected: FAIL — `AttributeError: module 'invoice_agent.graphs.enrichment' has no attribute 'signals_node'`

- [ ] **Step 3: Write the node**

In `src/invoice_agent/graphs/enrichment.py`, add `signals` to `EnrichmentState` after `findings`:

```python
    # Entity-memory findings (M4-B). Accumulates: each entry records something
    # observed and cannot un-happen. Nodes return DELTAS.
    signals: Annotated[list[Finding], operator.add]
```

Add the node after `validate_node`:

```python
def signals_node(state: EnrichmentState, *, conn: Any = None) -> dict[str, Any]:
    """Entity memory -> findings. No LLM, and no writes.

    Reads the vendor profile as it stands at BATCH START, because M4-D persists
    invoices in a serial node after the fan-out join and this runs inside the
    fan-out. That ordering is what keeps the scored invoice out of its own
    statistics — measured 2026-07-31, a single extreme point's z-score against a
    sample containing itself is bounded by (n-1)/sqrt(n), which is 1.79 at n=5
    and puts PRICE_OUTLIER below its own threshold at every realistic vendor
    size. Persist before this runs and the finding silently stops firing.
    """
    data = state.get("extracted")
    if data is None:
        return {}

    if conn is not None:
        found = _signals_for(conn, data)
    else:
        with get_engine().begin() as owned:
            found = _signals_for(owned, data)
    return {"signals": found}


def _signals_for(conn: Any, data: InvoiceData) -> list[Finding]:
    # `seller_tax_code` is `str | None` on InvoiceData — M1 measured that a tax
    # code has three ways of being absent ('0', empty element, missing element).
    # Without a seller there is no vendor to have memory of, and raising
    # NEW_VENDOR here would be wrong: the vendor may be well known, we just
    # cannot identify it. `validation.TAX_CODE_MISSING` already carries that.
    if not data.seller_tax_code:
        return []
    stats = vendor_profile.stats_for(conn, data.seller_tax_code)
    out: list[Finding] = []
    new = anomaly.new_vendor(stats)
    if new is not None:
        out.append(new)
    if stats is not None:
        outlier = anomaly.price_outlier(data.total_amount, stats)
        if outlier is not None:
            out.append(outlier)
    return out
```

Add the imports at the top:

```python
from invoice_agent.domain import anomaly, validation
from invoice_agent.memory import vendor_profile
```

- [ ] **Step 4: Wire it into the graph**

In `build_enrichment_graph`, register the node and re-point the `END` edges:

```python
    builder.add_node("signals", signals_node)
```

Change the conditional edges out of `validate` so the `END` branch goes to `signals`:

```python
    builder.add_conditional_edges(
        "validate",
        route_after_validate,
        {
            "vision_extract": "vision_extract",
            "manual_queue": "manual_queue",
            # Until M4-C splices classify/audit in front, a clean invoice and a
            # DATA-error invoice both land here. `route_after_validate` still
            # returns END; this map sends that outcome to `signals` instead.
            END: "signals",
        },
    )
    builder.add_edge("signals", END)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_enrichment_signals.py tests/test_enrichment_graph.py tests/test_enrichment_routing.py tests/test_enrichment_nodes.py -v`
Expected: PASS.

- [ ] **Step 6: Regenerate the committed diagram**

```bash
.venv/bin/python -m scripts.render_graph
.venv/bin/python -m pytest tests/test_enrichment_graph.py -k diagram -v
```

Expected: PASS — `test_the_committed_diagram_matches_the_graph` is a drift guard; regenerate rather than hand-editing the `.mmd`.

- [ ] **Step 7: Mutation-verify the self-exclusion invariant**

In `signals_node`, add a write before reading:

```python
    vendor_profile.apply_invoice(conn, tax_code=data.seller_tax_code, amount=data.total_amount)
```

Run `.venv/bin/python -m pytest tests/test_enrichment_signals.py::test_scored_invoice_is_absent_from_its_own_stats -v`
Expected: RED. Remove the line and confirm green.

- [ ] **Step 8: Commit**

```bash
git add src/invoice_agent/graphs/enrichment.py tests/test_enrichment_signals.py tests/conftest.py docs/diagrams/enrichment_graph.mmd
git commit -m "feat(graph): signals node — entity memory as findings

NEW_VENDOR and PRICE_OUTLIER from the vendor profile. No LLM, no writes:
signals runs inside fan-out and M4-D persists after the join, so the
profile it reads is 'as of batch start' and the scored invoice is absent
from its own statistics for free.

That ordering is load-bearing and invisible, so it is pinned by a test.
Measured 2026-07-31: a single extreme point's z against a sample
containing itself is bounded by (n-1)/sqrt(n) — 1.79 at n=5 — so
persisting first would silently stop PRICE_OUTLIER firing rather than
failing. Mutation-verified: add a write before the read and it goes red.

Attaches after validate; M4-C splices classify and audit in front."
```

---

### Task 4: Fix the corpus so the outliers are detectable

**Files:**
- Modify: `scripts/generate_invoices.py` (the `price_outlier` and `new_vendor` blocks)
- Modify: `scripts/generator/defects.py` (`OUTLIER_MULTIPLIER` → a relative target)
- Test: `tests/test_generator_price_outlier.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `defects.OUTLIER_VENDOR_MEAN_MULTIPLE: Decimal`; `_outlier_multiplier(target_total, current_total) -> Decimal`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_generator_price_outlier.py`:

```python
"""The price_outlier group must be detectable in principle.

Measured 2026-07-31 before this change: 3 of 6 were ALSO new_vendor, so their
vendor had no history and the z-score guard rejected them; and
OUTLIER_MULTIPLIER = 10 scaled the invoice's OWN amount against a within-vendor
coefficient of variation of ~0.95, so INV-0093 landed at z = 0.14 — not an
outlier at all. Recall was 0/6 (self-included) or 1/6 (self-excluded).
See spec 2026-07-31-m4b §1.1 and §4.
"""

import collections
import statistics

from scripts import generate_invoices
from scripts.generator import manifest


def build(tmp_path, count=300, seed=42):
    out = tmp_path / "corpus"
    generate_invoices.generate(count=count, seed=seed, out=out)
    return out, manifest.read_manifest(out / "manifest.jsonl")


def test_no_price_outlier_is_also_a_new_vendor(tmp_path):
    """A new vendor has no history, so the z-score guard rejects it before any
    threshold applies — the invoice is undetectable by construction."""
    _out, records = build(tmp_path)
    both = [
        r["invoice_id"] for r in records
        if "price_outlier" in r["defects"] and "new_vendor" in r["defects"]
    ]
    assert not both, f"price_outlier invoices with no vendor history: {both}"


def test_new_vendor_count_is_unchanged(tmp_path):
    """Redistributing must not shrink the group the ratios declare."""
    _out, records = build(tmp_path)
    assert sum(1 for r in records if "new_vendor" in r["defects"]) == 9


def test_budget_overrun_new_vendor_pairing_survives(tmp_path):
    """`defects.py` records that real invoices arrive as 'new vendor AND over
    budget'. That co-occurrence is meaningful and stays; only the pathological
    price_outlier pairing goes."""
    _out, records = build(tmp_path)
    both = [
        r for r in records
        if "budget_overrun" in r["defects"] and "new_vendor" in r["defects"]
    ]
    assert len(both) == 3


def test_every_price_outlier_is_far_from_its_vendors_other_invoices(tmp_path):
    """The property that makes the group detectable, stated in the generator's
    OWN terms: the outlier is a large multiple of the mean of its vendor's other
    invoices. Deliberately NOT a z-score — see spec §4.2: the generator uses the
    mean and the detector uses sigma, so this test cannot pass by encoding the
    detector's rule.
    """
    _out, records = build(tmp_path)
    by_vendor = collections.defaultdict(list)
    for r in records:
        by_vendor[r["seller_tax_code"]].append(r)

    checked = 0
    for r in records:
        if "price_outlier" not in r["defects"]:
            continue
        others = [
            float(p["total_amount"]) for p in by_vendor[r["seller_tax_code"]]
            if p["invoice_id"] != r["invoice_id"]
        ]
        assert len(others) >= 4, f"{r['invoice_id']}: vendor has too little history"
        checked += 1
        ratio = float(r["total_amount"]) / statistics.mean(others)
        assert ratio >= 4.0, f"{r['invoice_id']}: only {ratio:.1f}x its vendor's mean"
    assert checked == 6, f"expected 6 price_outlier invoices, checked {checked}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_generator_price_outlier.py -v`
Expected: FAIL on `test_no_price_outlier_is_also_a_new_vendor` (3 offenders) and on the ratio test.

- [ ] **Step 3: Replace the constant in `defects.py`**

In `scripts/generator/defects.py`, replace `OUTLIER_MULTIPLIER = Decimal("10")` with:

```python
# How far above its vendor's OWN scale a price outlier sits. Measured
# 2026-07-31 (docs/notes/2026-07-31-m4b-vendor-memory.md): the previous flat
# 10x of the invoice's own amount left INV-0093 at z = 0.14 against its vendor,
# because within-vendor amounts have a coefficient of variation of ~0.95 so the
# product landed anywhere.
#
# ANTI-CIRCULARITY, and the reason this is a MEAN and not a z-score: the
# detector uses the standard deviation. Sizing the corpus by the detector's own
# statistic would manufacture agreement and make M6's recall number measure
# nothing (design §10.1). Using a different statistic leaves the detector
# something to discover.
OUTLIER_VENDOR_MEAN_MULTIPLE = Decimal("8")
```

- [ ] **Step 4: Rewrite the two generator blocks**

The `price_outlier` block must run **after** `new_vendor`, because `new_vendor`
reassigns vendors and the multiplier depends on the final vendor cohort. Move
the whole `price_outlier` block to sit immediately after the `new_vendor` block,
and rewrite it as:

```python
    # price_outlier (6) — AFTER new_vendor, because that block reassigns vendors
    # and the target below is relative to the vendor's own other invoices.
    for idx in price_outlier_indices:
        s, d, meta = specs[idx]
        peers = [
            other.total_amount
            for j, (other, _od, _om) in enumerate(specs)
            if j != idx and other.seller_tax_code == s.seller_tax_code
        ]
        if len(peers) < 4:
            raise ValueError(
                f"spec {idx} was chosen as a price_outlier but its vendor has "
                f"only {len(peers)} other invoices — the z-score guard would "
                f"reject it and the defect would be undetectable (spec "
                f"2026-07-31-m4b §1.1)."
            )
        peer_mean = sum(peers, Decimal("0")) / len(peers)
        target = peer_mean * defects.OUTLIER_VENDOR_MEAN_MULTIPLE
        # INTEGER multiplier. Every amount in the spec is scaled by it, and an
        # integer preserves `quantity * unit_price == amount` and the VAT
        # relationships exactly. A fractional ratio would round them apart and
        # raise LINE_AMOUNT_MISMATCH — an UNDECLARED finding, which
        # `test_no_invoice_carries_an_UNDECLARED_finding` would catch as red.
        multiplier = max(Decimal("2"), (target / s.total_amount).to_integral_value())

        line = s.lines[0]
        new_line = dataclasses.replace(
            line,
            amount=line.amount * multiplier,
            unit_price=line.unit_price * multiplier,
            # Scale the LINE's VAT too. The header below is scaled in full
            # (subtotal / vat_total / total_amount), so leaving TThue at its
            # pre-outlier value keeps the header consistent while making the
            # LINE arithmetic wrong — a *price* outlier silently becomes a VAT
            # error as well. Measured 2026-07-29 (M3 exit audit): 4 invoices,
            # deviations up to 5,040,000 đồng. `defects` is a LIST so each one
            # stays attributable; a defect that drags an undeclared second
            # defect along makes M6's eval unattributable.
            vat_amount=(
                line.vat_amount * multiplier if line.vat_amount is not None else None
            ),
        )
        outlier_spec = dataclasses.replace(
            s,
            lines=[new_line],
            subtotal=s.subtotal * multiplier if s.subtotal else None,
            vat_total=s.vat_total * multiplier if s.vat_total else None,
            total_amount=s.total_amount * multiplier,
            total_in_words=None,
        )
        specs[idx] = (outlier_spec, d + ["price_outlier"], meta)
```

Where the old `price_outlier` block stood, keep only the index allocation so the
`_allocate_indices` consumption order is unchanged:

```python
    # price_outlier (6) — indices reserved here so the allocation order is
    # unchanged; the amounts are set after new_vendor, below.
    price_outlier_indices = _allocate_indices(counts["price_outlier"])
```

Rewrite the `new_vendor` block to drop the `price_outlier` source:

```python
    # new_vendor (9 total: 6 standalone + 3 on budget_overrun)
    #
    # Was 3 + 3 + 3, with the last 3 drawn from price_outlier. Measured
    # 2026-07-31: a new vendor has no history, so `anomaly.price_outlier`
    # rejects those 3 before any threshold applies and they are undetectable by
    # construction — half the group, silently (spec 2026-07-31-m4b §1.1).
    # The budget_overrun pairing STAYS: `defects.py` records that real invoices
    # arrive as "new vendor AND over budget", and that co-occurrence is
    # meaningful rather than pathological.
    new_vendor_indices = (
        _allocate_indices(6)
        + [idx for idx, (s, d, m) in enumerate(specs) if "budget_overrun" in d][:3]
    )
```

- [ ] **Step 5: Run the price-outlier tests**

Run: `.venv/bin/python -m pytest tests/test_generator_price_outlier.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 6: Run the full regression contract**

Run: `.venv/bin/python -m pytest tests/test_generator_cli.py tests/test_generator_independence.py tests/test_generator_labels.py tests/test_generator_reference_join.py tests/test_corpus_baseline_band.py -v`

Expected: all PASS. If `test_no_invoice_carries_an_UNDECLARED_finding` goes red,
the multiplier is not integral somewhere — check the `to_integral_value()` call
before touching anything else. **Do not "fix" the validator**: spec §6 and
`test_generator_cli.py:92` both record that twice already the validator was what
was wrong, and the 8 real fixtures arbitrate.

- [ ] **Step 7: Mutation-verify the detectability guard**

Restore the old `new_vendor` sourcing — put `[idx for idx, (s, d, m) in
enumerate(specs) if "price_outlier" in d][:3]` back and drop `_allocate_indices(6)`
to `_allocate_indices(3)`. Run:

`.venv/bin/python -m pytest tests/test_generator_price_outlier.py -v`

Expected: RED on `test_no_price_outlier_is_also_a_new_vendor`. Restore and
confirm green. This is the guard against silently re-creating the defect that
made half the group undetectable.

- [ ] **Step 8: Re-verify M4-A's frozen gate batch**

Changing the `new_vendor` allocation shifts `_allocate_indices` consumption, so
`GATE_BATCH_IDS` may no longer name the same invoices.

```bash
make generate
.venv/bin/python -m pytest tests/test_gate_batch.py -v
```

If red, re-run the discovery script from plan `2026-07-31-m4a` Task 7 step 1 and
paste the new frozen list into `scripts/generator/gate_batch.py`, then re-confirm
the batch's MKT-only membership and the `35M → 15M → −5M` chain.

- [ ] **Step 9: Commit**

```bash
git add scripts/generate_invoices.py scripts/generator/defects.py scripts/generator/gate_batch.py tests/test_generator_price_outlier.py
git commit -m "fix(generator): make the price_outlier group detectable

Measured 2026-07-31: 3 of 6 price_outlier invoices were also new_vendor,
so their vendor had no history and the z-score guard rejected them
before any threshold applied — undetectable by construction. And
OUTLIER_MULTIPLIER = 10 scaled the invoice's own amount against a
within-vendor coefficient of variation of ~0.95, so INV-0093 landed at
z = 0.14, not an outlier at all. Recall was 0/6.

new_vendor is now 6 standalone + 3 budget_overrun; the budget_overrun
pairing stays because real invoices do arrive as 'new vendor and over
budget'. The outlier is now a multiple of the mean of its vendor's other
invoices — a MEAN, deliberately, because the detector uses sigma and
sizing the corpus by the detector's own statistic would manufacture
agreement (design §10.1). The multiplier is integral so the scaled spec
keeps quantity * unit_price == amount exactly."
```

---

### Task 5: Retune the two constants, on numbers

**Files:**
- Modify: `src/invoice_agent/domain/anomaly.py` (the constants and their comment)
- Create: `tests/test_price_outlier_detection.py`
- Create: `docs/notes/2026-07-31-m4b-vendor-memory.md`

- [ ] **Step 1: Write the measurement harness**

Create `tests/test_price_outlier_detection.py`:

```python
"""Recall and false-positive rate for the two entity-memory signals.

This is M4-B's contribution to the M4 gate, and it is deterministic: no LLM is
involved, so it costs nothing to repeat.

Statistics come from each vendor's OTHER invoices, mirroring what `signals` sees
at run time — M4-D persists after the fan-out join, so the scored invoice is
never in its own profile. Scoring it any other way measures a system that does
not exist.
"""

import collections
from decimal import Decimal

import pytest

from scripts import generate_invoices
from scripts.generator import manifest

from invoice_agent.domain import anomaly
from invoice_agent.memory.vendor_profile import derive


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    out = tmp_path_factory.mktemp("corpus_pod") / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    return manifest.read_manifest(out / "manifest.jsonl")


def _stats_excluding(records, tax_code, invoice_id):
    amounts = [
        Decimal(r["total_amount"]) for r in records
        if r["seller_tax_code"] == tax_code and r["invoice_id"] != invoice_id
    ]
    count = len(amounts)
    total = sum(amounts, Decimal(0))
    sum_sq = sum((a * a for a in amounts), Decimal(0))
    avg, stddev = derive(count, total, sum_sq)
    return anomaly.VendorStats(invoice_count=count, avg_amount=avg, stddev_amount=stddev)


def score(records):
    tp = fp = fn = 0
    for r in records:
        stats = _stats_excluding(records, r["seller_tax_code"], r["invoice_id"])
        raised = anomaly.price_outlier(Decimal(r["total_amount"]), stats) is not None
        declared = "price_outlier" in r["defects"]
        tp += raised and declared
        fp += raised and not declared
        fn += declared and not raised
    return tp, fp, fn


def test_price_outlier_recall_and_false_positives(corpus):
    tp, fp, fn = score(corpus)
    total_declared = tp + fn
    clean = len(corpus) - total_declared
    print(
        f"\nPRICE_OUTLIER  recall {tp}/{total_declared}  "
        f"false positives {fp}/{clean} ({fp / clean:.1%})  "
        f"Z={anomaly.PRICE_OUTLIER_Z} MIN_N={anomaly.PRICE_OUTLIER_MIN_INVOICES}"
    )
    assert tp >= 5, f"recall {tp}/{total_declared} — was 0/6 before M4-B"
    # Design §10.4 targets a flag false-positive rate <= 5%.
    assert fp / clean <= 0.05, f"false-positive rate {fp / clean:.1%} exceeds 5%"


def test_new_vendor_is_exact(corpus):
    """9 declared, and no invoice from a vendor with history may raise it."""
    seen: collections.Counter = collections.Counter()
    tp = fp = fn = 0
    for r in sorted(corpus, key=lambda x: x["invoice_id"]):
        prior = seen[r["seller_tax_code"]]
        stats = None if prior == 0 else anomaly.VendorStats(prior, None, None)
        raised = anomaly.new_vendor(stats) is not None
        declared = "new_vendor" in r["defects"]
        tp += raised and declared
        fp += raised and not declared
        fn += declared and not raised
        seen[r["seller_tax_code"]] += 1
    print(f"\nNEW_VENDOR  tp={tp} fp={fp} fn={fn}")
    assert fn == 0, f"{fn} declared new vendors were missed"
```

- [ ] **Step 2: Run it and read the numbers**

Run: `.venv/bin/python -m pytest tests/test_price_outlier_detection.py -v -s`

Record recall and false-positive rate at the current `Z=3`, `MIN_N=5`.

- [ ] **Step 3: Sweep the two constants**

```bash
.venv/bin/python - <<'PY'
import itertools, pathlib, tempfile
from decimal import Decimal
from scripts import generate_invoices
from scripts.generator import manifest
from invoice_agent.domain import anomaly
import tests.test_price_outlier_detection as h

with tempfile.TemporaryDirectory() as d:
    out = pathlib.Path(d) / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    records = manifest.read_manifest(out / "manifest.jsonl")

print(f"{'Z':>5} {'MIN_N':>6} {'recall':>8} {'FP':>6} {'FP rate':>9}")
for z, min_n in itertools.product(("2.5", "3", "3.5", "4"), (3, 5, 7, 10)):
    anomaly.PRICE_OUTLIER_Z = Decimal(z)
    anomaly.PRICE_OUTLIER_MIN_INVOICES = min_n
    tp, fp, fn = h.score(records)
    clean = len(records) - (tp + fn)
    print(f"{z:>5} {min_n:>6} {f'{tp}/{tp+fn}':>8} {fp:>6} {fp/clean:>8.1%}")
PY
```

Pick the pair with the **highest recall subject to a false-positive rate <= 5%**
(design §10.4). On a tie, prefer the larger `MIN_N` — a bigger sample makes the
sigma less noisy, which is what the guard is for.

- [ ] **Step 4: Set the constants and rewrite their comment**

In `src/invoice_agent/domain/anomaly.py`, replace the whole provisional block.
**Rewrite it — do not append.** The old text says the constants are "set at M6
from docs/eval-report.md", and leaving that beside a fresh measurement is how the
next reader retunes against the wrong report.

```python
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
PRICE_OUTLIER_Z = Decimal("<chosen>")
PRICE_OUTLIER_MIN_INVOICES = <chosen>
```

Substitute the values chosen in step 3.

- [ ] **Step 5: Re-run and confirm**

Run: `.venv/bin/python -m pytest tests/test_price_outlier_detection.py -v -s`
Expected: PASS with the chosen constants, recall >= 5/6, FP rate <= 5%.

- [ ] **Step 6: Write the note**

Create `docs/notes/2026-07-31-m4b-vendor-memory.md` recording, with real numbers:

- the before state (0/6 self-included, 1/6 self-excluded) and the `(n-1)/sqrt(n)` table;
- the full `Z` × `MIN_N` sweep from step 3;
- the chosen pair and why;
- `NEW_VENDOR` recall and false positives;
- the incremental-vs-rebuild drift result (must be zero);
- that item 4.4 moved to M5, with the reason.

- [ ] **Step 7: Commit**

```bash
git add src/invoice_agent/domain/anomaly.py tests/test_price_outlier_detection.py docs/notes/2026-07-31-m4b-vendor-memory.md
git commit -m "fix(anomaly): retune PRICE_OUTLIER on measured numbers

Discharges the PROVISIONAL AND UNMEASURED admission standing in
anomaly.py since 2026-07-28. The old comment said these were 'set at M6
from docs/eval-report.md'; that is rewritten, not appended to, so nobody
retunes against the wrong report.

Scored against each vendor's OTHER invoices, mirroring what signals sees
at run time. Before M4-B: 0 of 6. Full Z x MIN_N sweep in the note; the
pair chosen is the highest recall with a false-positive rate within
design §10.4's 5% ceiling.

NEAR_DUP_WINDOW_DAYS stays provisional — its consumer is M4-D."
```

---

### Task 6: Roadmap, drift check, and gate verification

**Files:**
- Modify: `docs/Plan_Project_F_2026-07-25.md` (§3 M4 table, §7 status row)
- Modify: `docs/notes/2026-07-31-m4b-vendor-memory.md`

- [ ] **Step 1: Record the end-to-end drift number**

With Postgres up and the corpus generated:

```bash
make dev
.venv/bin/python -m pytest tests/test_rebuild_vendor_profiles.py -v
```

Record the drift result in the note. It must be zero — the criterion is exact
because both paths derive from the same triple by the same expression.

- [ ] **Step 2: Move item 4.4 in the roadmap**

In `docs/Plan_Project_F_2026-07-25.md` §3, change row 4.4 to record the move:

```markdown
| 4.4 | **Moved to M5** (spec M4-B §1.2). `episodic_decisions` is written by design §5.4's `learn` node and read by §5.1's `suggest` node — both M5 — and had no caller anywhere outside one repository test. Tuning amount-bucket width and flag-overlap ordering against hand-seeded fixtures would have produced numbers that measure the fixtures. Same reasoning that moved item 3.1 to M7 | §7.1 |
```

- [ ] **Step 3: Update §7's status row**

Change the M4 row to record M4-B alongside M4-A, following the M2/M3 style: what
was found, what was measured, what is owed. State that `PRICE_OUTLIER` was
detecting 0 of 6 and why, and that the fix was needed on both the rule and the
corpus.

- [ ] **Step 4: Run the full check**

```bash
make check
```

Expected: ruff clean, mypy clean, all tests green. Fix anything red before
continuing — a milestone is not closed on a red `make check`.

- [ ] **Step 5: Verify replay hermeticity**

```bash
env -u GROQ_API_KEY -u GEMINI_API_KEY LLM_MODE=replay .venv/bin/python -m pytest -q
```

Expected: green. `signals` calls no model, so this must be unaffected — if it is
not, `signals` is reaching a gateway.

- [ ] **Step 6: Commit and push**

```bash
git add docs/Plan_Project_F_2026-07-25.md docs/notes/2026-07-31-m4b-vendor-memory.md
git commit -m "docs(roadmap): close M4-B — vendor memory and signals

PRICE_OUTLIER was detecting 0 of 6 declared outliers, for two
independent reasons: the z-score was computed against statistics that
included the invoice being scored, which caps a single extreme point at
(n-1)/sqrt(n) — 1.79 at n=5, against a threshold of 3 — and the corpus
paired half the group with new vendors that have no history at all.

Fixed on both sides and re-measured. Item 4.4 moved to M5, where its
producer and its consumer both live."
git push
```

- [ ] **Step 7: Confirm CI is green on the pushed commit**

```bash
gh run list --limit 3
gh run watch --exit-status
```

Expected: green on the commit just pushed. **Do not report M4-B complete until
this shows green** — three milestones running failed exactly this, once on a
commit that was pushed, ran red in CI, and was not looked at.

---

## Notes for the implementer

**The ordering in Task 3 is the whole milestone.** `signals` must run before
anything persists the invoice it is scoring. If a later change moves persistence
earlier, `PRICE_OUTLIER` does not fail — it silently stops firing, and the only
symptom is a recall number that quietly drops. That is why the invariant has its
own test rather than living in a comment.

**Do not size the corpus by the detector's statistic.** Task 4 deliberately uses
the *mean* of a vendor's other invoices while the detector uses the *standard
deviation*. If a later change computes a z-score in the generator, the
anti-circularity argument is gone and M6's recall number stops meaning anything.

**When a corpus test goes red, the validator may be what is wrong.** Twice
already it was: M1's naive arithmetic rule flagged 6 of 8 real invoices, and
M2-B's unconditioned guards flagged 3 of 8. The 8 real fixtures arbitrate.

**A mutation result expires when the code it mutated stops being the producer.**
M4-A's exit review found a guard that was genuinely red at the commit that
introduced it and blind two commits later, because a later task changed what
produced the value. After Task 4 changes the corpus, re-run the Task 3 mutation.
