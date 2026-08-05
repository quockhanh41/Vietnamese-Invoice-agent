"""`spend_summary`, the `list_invoices` search filters, and the two Task 1 gaps
folded into Task 3: `budget_report`'s `department` filter and `invoice_detail`.

Uses `runtime_conn` (a transaction rolled back on teardown, tests/conftest.py) with
a local `_seed_invoice` helper — mirrors `tests/test_rebuild_vendor_profiles.py`'s
`_seed()` — rather than `committed_rows`/`_persisted_invoice`, because these tests
read on the SAME connection that writes and never need a second connection to see
committed state. `invoice_detail` is the one exception: it needs a real invoice
WITH line items and a flag, which `_persisted_invoice` already builds.
"""

import datetime
import uuid
from decimal import Decimal

import pytest
from tests.helpers.approval_fixtures import _persisted_invoice

from invoice_agent.db import queries
from invoice_agent.db.engine import get_engine
from invoice_agent.db.repositories import invoices as invoices_repo
from invoice_agent.db.repositories import vendors as vendor_repo

# Any period far from what the real fixtures and other tests use (they cluster
# around 2026-07) so this file's totals are never polluted by another test's
# committed rows.
_PERIOD = "2031-03"


def _seed_invoice(
    conn,
    *,
    seller_tax_code: str = "0100000003",
    issue_date: datetime.date = datetime.date(2031, 3, 15),
    total_amount: Decimal,
    department: str | None = None,
    category: str | None = None,
) -> uuid.UUID:
    vendor_repo.ensure_vendor(conn, seller_tax_code)
    invoice_id = uuid.uuid4()
    invoices_repo.insert_row(
        conn,
        {
            "id": invoice_id,
            "template_no": "1",
            "invoice_series": "C26TAA",
            "invoice_number": invoice_id.hex[:8],
            "seller_tax_code": seller_tax_code,
            "issue_date": issue_date,
            "total_amount": total_amount,
            "source_type": "xml",
            "department": department,
            "category": category,
        },
    )
    return invoice_id


# ---------------------------------------------------------------------------
# spend_summary
# ---------------------------------------------------------------------------


def test_spend_summary_by_department_and_category_sum_to_the_same_total(runtime_conn):
    _seed_invoice(
        runtime_conn, total_amount=Decimal("1000000"), department="MKT", category="MARKETING"
    )
    _seed_invoice(
        runtime_conn, total_amount=Decimal("2000000"), department="ADMIN", category="MARKETING"
    )
    _seed_invoice(
        runtime_conn,
        total_amount=Decimal("500000"),
        department="MKT",
        category="OFFICE_SUPPLIES",
    )

    by_department = queries.spend_summary(runtime_conn, period=_PERIOD, group_by="department")
    by_category = queries.spend_summary(runtime_conn, period=_PERIOD, group_by="category")

    assert {r["group_key"] for r in by_department} == {"MKT", "ADMIN"}
    assert {r["group_key"] for r in by_category} == {"MARKETING", "OFFICE_SUPPLIES"}

    total_by_department = sum(Decimal(r["total_amount"]) for r in by_department)
    total_by_category = sum(Decimal(r["total_amount"]) for r in by_category)
    assert total_by_department == Decimal("3500000")
    assert total_by_category == Decimal("3500000")
    assert total_by_department == total_by_category

    mkt = next(r for r in by_department if r["group_key"] == "MKT")
    assert mkt["invoice_count"] == 2
    assert Decimal(mkt["total_amount"]) == Decimal("1500000")


def test_spend_summary_rejects_an_unknown_group_by(runtime_conn):
    """An injection surface: a free-form column name must never silently
    become a GROUP BY column."""
    with pytest.raises(ValueError, match="group_by"):
        queries.spend_summary(runtime_conn, period=_PERIOD, group_by="seller_tax_code")


def test_spend_summary_empty_period_returns_an_empty_list(runtime_conn):
    """Not a zero-amount row per department/category — that would read as real
    data to an analyst asking whether anyone spent anything that month."""
    _seed_invoice(runtime_conn, total_amount=Decimal("100000"), department="MKT")

    rows = queries.spend_summary(runtime_conn, period="1999-01", group_by="department")

    assert rows == []


# ---------------------------------------------------------------------------
# list_invoices: vendor + min_amount
# ---------------------------------------------------------------------------


def test_list_invoices_filters_by_vendor(runtime_conn):
    hit = _seed_invoice(
        runtime_conn, seller_tax_code="0111111111", total_amount=Decimal("100000")
    )
    miss = _seed_invoice(
        runtime_conn, seller_tax_code="0222222222", total_amount=Decimal("100000")
    )

    rows = queries.list_invoices(runtime_conn, vendor="0111111111")

    ids = {r["id"] for r in rows}
    assert hit in ids
    assert miss not in ids


def test_list_invoices_min_amount_is_inclusive(runtime_conn):
    below = _seed_invoice(runtime_conn, total_amount=Decimal("999999.99"))
    equal = _seed_invoice(runtime_conn, total_amount=Decimal("1000000"))
    above = _seed_invoice(runtime_conn, total_amount=Decimal("1000000.01"))

    rows = queries.list_invoices(runtime_conn, min_amount=Decimal("1000000"))

    ids = {r["id"] for r in rows}
    assert equal in ids, "min_amount must be inclusive of the boundary value"
    assert above in ids
    assert below not in ids


# ---------------------------------------------------------------------------
# Extra scope (a): budget_report's department filter
# ---------------------------------------------------------------------------


def test_budget_report_filters_by_department(requires_postgres):
    """Seeded rows for 2026-07 (scripts/seed_reference.BUDGETS): MKT, ADMIN, OPS
    all carry a July budget line, so filtering to one department must narrow
    the result without dropping it to zero."""
    with get_engine().begin() as conn:
        mkt_only = queries.budget_report(conn, period="2026-07", department="MKT")
        every_department = queries.budget_report(conn, period="2026-07")

    assert mkt_only, "expected the seeded MKT/2026-07 budget row"
    assert {r["department_code"] for r in mkt_only} == {"MKT"}
    assert len(every_department) > len(mkt_only)


def test_budget_report_department_filter_defaults_to_every_caller_working(requires_postgres):
    """Default `None` must keep every existing caller (e.g. the /reports/budgets
    route, which never passes `department`) working exactly as before."""
    with get_engine().begin() as conn:
        rows = queries.budget_report(conn, period="2026-07")
    assert {r["department_code"] for r in rows} >= {"MKT", "ADMIN", "OPS"}


# ---------------------------------------------------------------------------
# Extra scope (b): invoice_detail composes get_by_id + get_lines + open_flags_for
# ---------------------------------------------------------------------------


def test_invoice_detail_composes_fields_lines_and_flags(committed_rows):
    db_id, _ = _persisted_invoice(committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly"))
    with get_engine().begin() as conn:
        detail = queries.invoice_detail(conn, db_id)

    assert detail is not None
    assert detail["invoice"]["id"] == db_id
    assert detail["lines"], "a reviewer needs the line items"
    assert [f["code"] for f in detail["flags"]] == ["PRICE_OUTLIER"]


def test_invoice_detail_is_none_for_an_unknown_id(requires_postgres):
    with get_engine().begin() as conn:
        assert queries.invoice_detail(conn, uuid.uuid4()) is None
