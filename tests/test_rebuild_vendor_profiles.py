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
        rebuild.main(conn=runtime_conn)
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
