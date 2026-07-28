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


def test_insert_with_lines_round_trips_a_real_fixture(runtime_conn):
    """The eight real fixtures are the anti-circularity anchor (design doc
    §10.1). If the schema cannot store what the M1 parser produces, the
    corpus generator at M2-C will paper over it."""
    from pathlib import Path

    from invoice_agent.db.repositories import batches
    from invoice_agent.db.repositories import invoices as invoice_repo
    from invoice_agent.extraction.xml_parser import parse_invoice_xml

    path = Path("tests/fixtures/real_samples/lienket_gtgt_7dong_2.1.0.xml")
    data = parse_invoice_xml(path.read_bytes())
    batch_id = batches.create_batch(runtime_conn, label="fixture")

    invoice_id = invoice_repo.insert_with_lines(
        runtime_conn, data, batch_id=batch_id, source_type="xml", source_path=str(path)
    )
    assert invoice_id is not None

    lines = invoice_repo.get_lines(runtime_conn, invoice_id)
    assert len(lines) == 7, "the 7-line fixture lost lines in storage"
    # line_no is NOT NULL and dense; STT was present on 15/15 measured lines.
    assert [r["line_no"] for r in lines] == [1, 2, 3, 4, 5, 6, 7]


def test_decimal_precision_survives_storage(runtime_conn):
    """Money columns are unconstrained NUMERIC. M1 measured 21618409.09000000
    (8 dp) and a unit price of 398181.33 where 3 x 398181.33 = 1194543.99 but
    ThTien = 1194544. NUMERIC(18,2) rounds, and the validation rule that holds
    8/8 is an EXACT comparison."""
    from invoice_agent.db.repositories import batches
    from invoice_agent.db.repositories import invoices as invoice_repo
    from tests.test_schema_constraints import _invoice_values

    vendors.ensure_vendor(runtime_conn, "0100000003", name="NCC")
    batch_id = batches.create_batch(runtime_conn, label="precision")
    exact = Decimal("21618409.09000000")
    values = _invoice_values(
        batch_id=batch_id, total_amount=exact, invoice_number=f"PR{uuid.uuid4().hex[:6]}"
    )
    invoice_id = invoice_repo.insert_row(runtime_conn, values)
    row = invoice_repo.get_by_id(runtime_conn, invoice_id)
    assert row["total_amount"] == exact

