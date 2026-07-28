"""Round-trip tests, one per repository. All run as the RUNTIME role.

Running these as the owner would hide a whole class of bug: a missing GRANT.
The runtime role is what production uses, so it is what the tests use.
"""

import uuid
from decimal import Decimal

import pytest

from invoice_agent.db.repositories import batches, vendors

# Three real MST shapes, read from tests/fixtures/real_samples/ on 2026-07-28
# (already anonymised — see spec §2.2 provenance note). A CHAR(10) assumption
# rejects two of the three.
MST_SHAPES = ["0100000003", "0300000018-042", "050000000000"]


@pytest.mark.parametrize("tax_code", MST_SHAPES)
def test_vendors_accept_all_three_real_mst_shapes(runtime_conn, tax_code):
    vendors.ensure_vendor(runtime_conn, tax_code, name="Nhà cung cấp thử nghiệm")
    row = vendors.get_vendor(runtime_conn, tax_code)
    assert row is not None
    assert row["tax_code"] == tax_code


def test_ensure_vendor_is_idempotent_and_does_not_clobber_the_profile(runtime_conn):
    """Ingestion creates a STUB row to satisfy the invoices FK. Statistics are
    filled transactionally at post/approve (M4.3, design doc §2.2), so a second
    sighting must not reset them."""
    tax_code = f"ZZ{uuid.uuid4().hex[:8]}"
    vendors.ensure_vendor(runtime_conn, tax_code, name="Lần đầu")
    vendors.update_profile(runtime_conn, tax_code, invoice_count=7,
                           total_amount=Decimal("1000000"))
    vendors.ensure_vendor(runtime_conn, tax_code, name="Lần hai")
    row = vendors.get_vendor(runtime_conn, tax_code)
    assert row["invoice_count"] == 7, "second sighting wiped accumulated memory"
    assert row["total_amount"] == Decimal("1000000")


def test_batch_lifecycle(runtime_conn):
    batch_id = batches.create_batch(runtime_conn, label="test-batch")
    assert batches.get_batch(runtime_conn, batch_id)["status"] == "running"
    batches.complete_batch(runtime_conn, batch_id, status="completed", invoice_count=30)
    row = batches.get_batch(runtime_conn, batch_id)
    assert row["status"] == "completed"
    assert row["invoice_count"] == 30
    assert row["completed_at"] is not None


def test_batch_status_is_constrained(runtime_conn):
    from sqlalchemy.exc import IntegrityError

    batch_id = batches.create_batch(runtime_conn, label="bad-status")
    with pytest.raises(IntegrityError):
        batches.complete_batch(runtime_conn, batch_id, status="finished", invoice_count=1)
