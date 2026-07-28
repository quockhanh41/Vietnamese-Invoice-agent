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
from invoice_agent.db.repositories import flags, journal, vendors
from invoice_agent.db.repositories import invoices as invoice_repo
from invoice_agent.db.repositories.journal import JournalLine


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
        runtime_conn.execute(models.invoices.insert(), _invoice_values(seller_tax_code=None))


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


def test_flag_source_must_be_deterministic(runtime_conn):
    """MUTATION GUARD, and the point of the whole milestone.

    Principle 1: no LLM ever raises a flag; flags are computed by Python from
    DB state. Today that is a convention enforced by code review. As a CHECK
    listing only deterministic producers, adding an agent as a flag source
    requires a MIGRATION — it cannot happen by accident inside a node.
    """
    from invoice_agent.db.repositories import flags, vendors

    vendors.ensure_vendor(runtime_conn, "0100000003", name="NCC")
    invoice_id = invoice_repo.insert_row(
        runtime_conn, _invoice_values(invoice_number=f"FL{uuid.uuid4().hex[:6]}")
    )
    with pytest.raises(IntegrityError):
        flags.raise_flag(
            runtime_conn,
            invoice_id,
            code="ANYTHING",
            severity="warn",
            raised_by="classifier_agent",
        )


BALANCED = [
    # A real office-supplies entry (design doc §2.7):
    #   Nợ 6422  3.250.000 / Nợ 1331  260.000 / Có 331  3.510.000
    JournalLine("6422", "Chi phí quản lý doanh nghiệp", Decimal("3250000"), Decimal("0"), None),
    JournalLine("1331", "Thuế GTGT được khấu trừ", Decimal("260000"), Decimal("0"), None),
    JournalLine("331", "Phải trả cho người bán", Decimal("0"), Decimal("3510000"), None),
]


def _clean_invoice(conn, suffix):
    vendors.ensure_vendor(conn, "0100000003", name="NCC")
    return invoice_repo.insert_row(conn, _invoice_values(invoice_number=suffix))


def test_unbalanced_journal_entry_rejected(runtime_conn):
    """M2 GATE CONDITION + MUTATION GUARD (drop CHECK total_debit=total_credit)."""
    invoice_id = _clean_invoice(runtime_conn, f"JB{uuid.uuid4().hex[:6]}")
    with pytest.raises(IntegrityError):
        journal.create_entry_with_lines(
            runtime_conn,
            invoice_id=invoice_id,
            entry_date=date(2026, 7, 1),
            posting_authority="auto_clean",
            lines=[
                JournalLine("6422", "Chi phí QLDN", Decimal("3250000"), Decimal("0"), None),
                JournalLine("331", "Phải trả người bán", Decimal("0"), Decimal("9999999"), None),
            ],
        )


def test_human_approved_requires_an_approval_row(runtime_conn):
    """MUTATION GUARD. Without this CHECK, `posting_authority='human_approved'`
    is a string an application can simply write — the DB layer of the safety
    property would be asserting nothing."""
    invoice_id = _clean_invoice(runtime_conn, f"JH{uuid.uuid4().hex[:6]}")
    with pytest.raises(IntegrityError):
        journal.create_entry_with_lines(
            runtime_conn,
            invoice_id=invoice_id,
            entry_date=date(2026, 7, 1),
            posting_authority="human_approved",
            approval_id=None,
            lines=BALANCED,
        )


def test_auto_clean_blocked_while_flags_open(runtime_conn):
    """MUTATION GUARD (drop the trigger). An invoice carrying an unresolved
    flag must never be auto-posted; only a human decision can clear it."""
    invoice_id = _clean_invoice(runtime_conn, f"JF{uuid.uuid4().hex[:6]}")
    flags.raise_flag(
        runtime_conn,
        invoice_id,
        code="ARITHMETIC_MISMATCH",
        severity="critical",
        raised_by="validation",
    )
    with pytest.raises((IntegrityError, DBAPIError), match="open flag"):
        journal.create_entry_with_lines(
            runtime_conn,
            invoice_id=invoice_id,
            entry_date=date(2026, 7, 1),
            posting_authority="auto_clean",
            lines=BALANCED,
        )


def test_auto_clean_allowed_once_every_flag_is_resolved(runtime_conn):
    """The trigger must not be a blanket ban — otherwise it would pass the test
    above while breaking the 85% straight-through-processing target."""
    invoice_id = _clean_invoice(runtime_conn, f"JR{uuid.uuid4().hex[:6]}")
    flag_id = flags.raise_flag(
        runtime_conn, invoice_id, code="X", severity="info", raised_by="validation"
    )
    flags.resolve_flag(runtime_conn, flag_id)
    entry_id = journal.create_entry_with_lines(
        runtime_conn,
        invoice_id=invoice_id,
        entry_date=date(2026, 7, 1),
        posting_authority="auto_clean",
        lines=BALANCED,
    )
    assert journal.get_for_invoice(runtime_conn, invoice_id)["id"] == entry_id


def test_line_cannot_be_both_debit_and_credit(runtime_conn):
    """MUTATION GUARD (drop CHECK debit=0 OR credit=0)."""
    invoice_id = _clean_invoice(runtime_conn, f"JD{uuid.uuid4().hex[:6]}")
    with pytest.raises(IntegrityError):
        journal.create_entry_with_lines(
            runtime_conn,
            invoice_id=invoice_id,
            entry_date=date(2026, 7, 1),
            posting_authority="auto_clean",
            lines=[
                JournalLine("6422", "Chi phí QLDN", Decimal("100"), Decimal("100"), None),
                JournalLine("331", "Phải trả người bán", Decimal("0"), Decimal("0"), None),
            ],
        )


def test_negative_amounts_rejected(runtime_conn):
    """MUTATION GUARD (drop CHECK debit>=0 AND credit>=0). Beyond the design
    doc, and it closes a real hole: debit=-100/credit=0 satisfies
    `debit = 0 OR credit = 0`, so without this a 'balanced' entry can be
    assembled entirely from negative amounts."""
    invoice_id = _clean_invoice(runtime_conn, f"JN{uuid.uuid4().hex[:6]}")
    with pytest.raises(IntegrityError):
        journal.create_entry_with_lines(
            runtime_conn,
            invoice_id=invoice_id,
            entry_date=date(2026, 7, 1),
            posting_authority="auto_clean",
            lines=[
                JournalLine("6422", "Chi phí QLDN", Decimal("-100"), Decimal("0"), None),
                JournalLine("331", "Phải trả người bán", Decimal("0"), Decimal("-100"), None),
            ],
        )


def test_one_invoice_cannot_be_posted_twice(runtime_conn):
    """Design doc §2.7: invoice_id is UNIQUE on journal_entries. Measured at
    M1.5: a `kill -9` inside a post-gate node makes the node run again on
    resume, producing TWO rows. Node placement does not stop that — this
    constraint does."""
    invoice_id = _clean_invoice(runtime_conn, f"JT{uuid.uuid4().hex[:6]}")
    journal.create_entry_with_lines(
        runtime_conn,
        invoice_id=invoice_id,
        entry_date=date(2026, 7, 1),
        posting_authority="auto_clean",
        lines=BALANCED,
    )
    with pytest.raises(IntegrityError):
        journal.create_entry_with_lines(
            runtime_conn,
            invoice_id=invoice_id,
            entry_date=date(2026, 7, 1),
            posting_authority="auto_clean",
            lines=BALANCED,
        )


def test_no_production_caller_of_journal_repository():
    """Spec §6.1 — an ORDERING RISK made measurable.

    This repository can create a journal entry, and its code-layer guard
    (`tools/guards.py`, item 5.3) does not exist until M5. Until then the
    protections are the DB constraints above plus the fact that NOTHING CALLS
    IT. That last one is an assumption, so it gets asserted.

    When M5.3 lands, DELETE this test and let
    `test_never_post_without_approval.py` take over.
    """
    import pathlib

    src = pathlib.Path("src/invoice_agent")
    offenders = [
        p
        for p in src.rglob("*.py")
        if "repositories.journal" in p.read_text(encoding="utf-8")
        or "repositories import journal" in p.read_text(encoding="utf-8")
    ]
    offenders = [p for p in offenders if p.name != "journal.py"]
    assert not offenders, (
        f"journal repository gained a production caller before its guard exists: {offenders}"
    )


def test_audit_log_update_denied_to_runtime_role(runtime_conn):
    """MUTATION GUARD 1 of 2 — the PRIVILEGE layer.

    Verified red 2026-07-28 by granting UPDATE on audit_log to invoice_app.
    Kept separate from the trigger test on purpose: each mechanism is checked
    with the other still in place, so neither can be credited for the other's
    work.
    """
    from invoice_agent.db import audit

    audit.record(runtime_conn, actor="system", action="probe", allowed=True)
    with pytest.raises((DBAPIError, IntegrityError), match="permission denied"):
        runtime_conn.execute(models.audit_log.update().values(reason="tampered"))


def test_audit_log_immutable_even_as_owner(owner_conn):
    """MUTATION GUARD 2 of 2 — the TRIGGER layer, and the reason it exists.

    Measured 2026-07-28: POSTGRES_USER=invoice is a SUPERUSER, and superusers
    bypass privilege checks entirely — the REVOKE lands in the ACL
    ({invoice=arDxtm/invoice}, w and d gone) and the UPDATE succeeds anyway.
    Every psql session opened during a demo is such a session. Without this
    trigger, "append-only" would be a README claim rather than a property.
    """
    from invoice_agent.db import audit

    audit.record(owner_conn, actor="system", action="probe-owner", allowed=True)
    with pytest.raises((DBAPIError, IntegrityError), match="append-only"):
        owner_conn.execute(models.audit_log.update().values(reason="tampered"))


def test_audit_log_delete_denied_even_as_owner(owner_conn):
    from invoice_agent.db import audit

    audit.record(owner_conn, actor="system", action="probe-delete", allowed=True)
    with pytest.raises((DBAPIError, IntegrityError), match="append-only"):
        owner_conn.execute(models.audit_log.delete())


def test_audit_log_records_grants_not_only_refusals(runtime_conn):
    """A refusal-only log cannot be audited: you cannot tell "nothing happened"
    from "the writer was never called"."""
    from invoice_agent.db import audit

    row_id = audit.record(
        runtime_conn,
        actor="mcp:claude-desktop",
        action="create_journal_entry",
        target_table="journal_entries",
        target_id="abc",
        allowed=False,
        reason="invoice not approved",
    )
    assert row_id is not None
