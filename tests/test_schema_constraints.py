"""The database defending itself — M2 gate evidence.

Every test here has a MUTATION entry in the plan: drop the constraint, confirm
this test goes red. M1 shipped four tests that passed while guarding nothing,
and a schema milestone is where that repeats most easily — a test that inserts
VALID data passes whether or not the constraint exists.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from invoice_agent.db import models
from invoice_agent.db.repositories import invoices as invoice_repo
from invoice_agent.db.repositories import vendors


def _invoice_values(**overrides):
    base = {
        "id": uuid.uuid4(),
        "template_no": "1",
        "invoice_series": "C26TAA",
        "invoice_number": "00001234",
        "seller_tax_code": "0100000003",
        "issue_date": date(2026, 7, 1),
        "total_amount": Decimal("1000000"),
        "source_type": "xml",
        "status": "received",
    }
    base.update(overrides)
    return base


def test_invoice_requires_seller_tax_code(runtime_conn):
    """MUTATION GUARD. Measured 2026-07-28: seller MST is present 8/8 in the
    real fixtures, so NOT NULL is safe — and it is what keeps the natural key
    free of NULLs. With a nullable column, Postgres treats NULLs as distinct
    and the SAME invoice inserts twice, defeating the UNIQUE that design doc
    §2.3 calls an e-invoice's identity."""
    with pytest.raises(IntegrityError):
        runtime_conn.execute(
            models.invoices.insert(), _invoice_values(seller_tax_code=None)
        )


def test_duplicate_invoice_is_idempotent(runtime_conn):
    """MUTATION GUARD: drop the UNIQUE and the second insert returns an id.

    Re-uploading the same file must NOT crash — design doc §2.3 routes it to
    duplicate handling. Verified 2026-07-28 that ON CONFLICT DO NOTHING
    RETURNING yields 0 rows rather than raising.
    """
    vendors.ensure_vendor(runtime_conn, "0100000003", name="NCC")
    values = _invoice_values(invoice_number=f"DUP{uuid.uuid4().hex[:6]}")
    first = invoice_repo.insert_row(runtime_conn, values)
    second = invoice_repo.insert_row(runtime_conn, dict(values, id=uuid.uuid4()))
    assert first is not None
    assert second is None, "duplicate silently inserted — the natural key is not enforced"


def test_invoice_status_is_constrained(runtime_conn):
    vendors.ensure_vendor(runtime_conn, "0100000003", name="NCC")
    with pytest.raises((IntegrityError, DBAPIError)):
        runtime_conn.execute(
            models.invoices.insert(),
            _invoice_values(status="whatever", invoice_number=f"ST{uuid.uuid4().hex[:6]}"),
        )
