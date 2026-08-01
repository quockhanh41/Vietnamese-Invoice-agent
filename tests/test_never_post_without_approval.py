"""Portfolio test 2 (roadmap §8) — the safety property, all three layers.

Cases 1-8 call the guard normally and assert it refuses. Cases 9-11 BYPASS the
guard and call `repositories.journal` directly, so each database constraint is
exercised on its own. Without that split the database mutations in the closeout
note cannot go red: an intact guard refuses first and the constraint beneath it
is never reached.

Assertions on `audit_log` filter by the invoice's UUID rather than counting
rows, because the guard's audit write is autonomous (its own connection, its own
commit) and therefore survives the rollback of a test's transaction.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from invoice_agent.db import models
from invoice_agent.db.repositories import approvals, invoices, journal
from invoice_agent.domain import accounting
from invoice_agent.tools import guards

BALANCED = [
    accounting.JournalLine("6422", "Chi phí quản lý", Decimal("100"), Decimal("0")),
    accounting.JournalLine("331", "Phải trả người bán", Decimal("0"), Decimal("100")),
]


def _insert_invoice(conn, sample_invoice) -> uuid.UUID:
    """A persisted invoice with a unique natural key.

    The key must vary per call: `uq_invoices_natural_key` makes a fixed
    invoice_number return None from `insert_with_lines` on the second run
    against the same database. M3-A shipped a test that passed exactly once per
    database for precisely this reason.
    """
    data = sample_invoice.model_copy(update={"invoice_number": uuid.uuid4().hex[:8]})
    invoice_id = invoices.insert_with_lines(conn, data, source_type="xml")
    assert invoice_id is not None
    return invoice_id


def _audit_rows(invoice_id: uuid.UUID) -> list[dict]:
    """Read audit_log on a FRESH connection — the guard's writes are autonomous
    and are not visible inside the test's own open transaction."""
    from sqlalchemy import select

    from invoice_agent.db.engine import get_engine

    with get_engine().begin() as conn:
        stmt = select(models.audit_log).where(
            models.audit_log.c.target_id == str(invoice_id)
        )
        return [dict(r) for r in conn.execute(stmt).mappings()]


def test_rejects_an_unknown_posting_authority(runtime_conn, sample_invoice):
    invoice_id = _insert_invoice(runtime_conn, sample_invoice)
    with pytest.raises(guards.GuardDenied, match="posting_authority"):
        guards.create_journal_entry(
            runtime_conn,
            invoice_id=invoice_id,
            posting_authority="whatever_i_like",
            entry_date=date(2026, 7, 1),
            lines=BALANCED,
            actor="system",
        )


def test_human_approved_without_an_approval_id_is_denied(runtime_conn, sample_invoice):
    invoice_id = _insert_invoice(runtime_conn, sample_invoice)
    with pytest.raises(guards.GuardDenied, match="approval_id"):
        guards.create_journal_entry(
            runtime_conn,
            invoice_id=invoice_id,
            posting_authority="human_approved",
            entry_date=date(2026, 7, 1),
            lines=BALANCED,
            actor="human:ketoan",
        )
    denials = [r for r in _audit_rows(invoice_id) if not r["allowed"]]
    assert denials, "a refusal that writes nothing is indistinguishable from never being called"


def test_an_undecided_approval_cannot_post(runtime_conn, sample_invoice):
    """Design §5.3 step 2. The state may say approved; the database is the arbiter."""
    invoice_id = _insert_invoice(runtime_conn, sample_invoice)
    approval_id = approvals.open_approval(
        runtime_conn,
        invoice_id=invoice_id,
        thread_id=f"appr:{invoice_id}",
        interrupt_payload={},
        flags_snapshot={},
        suggestions={},
    )
    with pytest.raises(guards.GuardDenied, match="not decided"):
        guards.create_journal_entry(
            runtime_conn,
            invoice_id=invoice_id,
            posting_authority="human_approved",
            approval_id=approval_id,
            entry_date=date(2026, 7, 1),
            lines=BALANCED,
            actor="human:ketoan",
        )


def test_a_rejected_approval_cannot_post(runtime_conn, sample_invoice):
    invoice_id = _insert_invoice(runtime_conn, sample_invoice)
    approval_id = approvals.open_approval(
        runtime_conn,
        invoice_id=invoice_id,
        thread_id=f"appr:{invoice_id}",
        interrupt_payload={},
        flags_snapshot={},
        suggestions={},
    )
    approvals.record_decision(
        runtime_conn, approval_id, decision="rejected", decided_by="human:ketoan"
    )
    with pytest.raises(guards.GuardDenied, match="rejected"):
        guards.create_journal_entry(
            runtime_conn,
            invoice_id=invoice_id,
            posting_authority="human_approved",
            approval_id=approval_id,
            entry_date=date(2026, 7, 1),
            lines=BALANCED,
            actor="human:ketoan",
        )


def test_an_approval_for_a_different_invoice_cannot_post(runtime_conn, sample_invoice):
    """The case a naive guard misses: the approval IS approved, just not for this
    invoice. Without the invoice_id comparison, one approved invoice would
    authorise posting any other."""
    approved_invoice = _insert_invoice(runtime_conn, sample_invoice)
    other_invoice = _insert_invoice(runtime_conn, sample_invoice)
    approval_id = approvals.open_approval(
        runtime_conn,
        invoice_id=approved_invoice,
        thread_id=f"appr:{approved_invoice}",
        interrupt_payload={},
        flags_snapshot={},
        suggestions={},
    )
    approvals.record_decision(
        runtime_conn, approval_id, decision="approved", decided_by="human:ketoan"
    )
    with pytest.raises(guards.GuardDenied, match="different invoice"):
        guards.create_journal_entry(
            runtime_conn,
            invoice_id=other_invoice,
            posting_authority="human_approved",
            approval_id=approval_id,
            entry_date=date(2026, 7, 1),
            lines=BALANCED,
            actor="human:ketoan",
        )


@pytest.mark.parametrize("decision", ["approved", "edited"])
def test_an_approved_or_edited_approval_posts(runtime_conn, sample_invoice, decision):
    """`edited` posts. APPROVAL_DECISIONS allows it while design §5.1 routes only
    approved/rejected; a reviewer who corrected a field and let the invoice
    through has approved it. Decided in spec §4.1 so M5-B inherits an answer."""
    invoice_id = _insert_invoice(runtime_conn, sample_invoice)
    approval_id = approvals.open_approval(
        runtime_conn,
        invoice_id=invoice_id,
        thread_id=f"appr:{invoice_id}",
        interrupt_payload={},
        flags_snapshot={},
        suggestions={},
    )
    approvals.record_decision(
        runtime_conn, approval_id, decision=decision, decided_by="human:ketoan"
    )
    entry_id = guards.create_journal_entry(
        runtime_conn,
        invoice_id=invoice_id,
        posting_authority="human_approved",
        approval_id=approval_id,
        entry_date=date(2026, 7, 1),
        lines=BALANCED,
        actor="human:ketoan",
    )
    entry = journal.get_for_invoice(runtime_conn, invoice_id)
    assert entry is not None
    assert entry["id"] == entry_id
    assert entry["approval_id"] == approval_id
    assert invoices.get_by_id(runtime_conn, invoice_id)["status"] == "posted"
    assert any(r["allowed"] for r in _audit_rows(invoice_id))


def test_auto_clean_is_denied_when_a_warn_flag_is_open(runtime_conn, sample_invoice):
    from invoice_agent.db.repositories import flags
    from invoice_agent.domain import findings

    invoice_id = _insert_invoice(runtime_conn, sample_invoice)
    flags.raise_flag(
        runtime_conn,
        invoice_id,
        code=findings.PRICE_OUTLIER,
        severity="warn",
        raised_by="anomaly",
    )
    with pytest.raises(guards.GuardDenied, match="open flag"):
        guards.create_journal_entry(
            runtime_conn,
            invoice_id=invoice_id,
            posting_authority="auto_clean",
            entry_date=date(2026, 7, 1),
            lines=BALANCED,
            actor="system",
        )
    assert [r for r in _audit_rows(invoice_id) if not r["allowed"]]


def test_auto_clean_posts_over_an_info_flag(runtime_conn, sample_invoice):
    """`info` is context for a human, never a block (spec §0 constraint 3).
    NEW_VENDOR fires on every vendor's first sighting — 51 across the corpus —
    so blocking on it makes the auto path unreachable."""
    from invoice_agent.db.repositories import flags
    from invoice_agent.domain import findings

    invoice_id = _insert_invoice(runtime_conn, sample_invoice)
    flags.raise_flag(
        runtime_conn,
        invoice_id,
        code=findings.NEW_VENDOR,
        severity="info",
        raised_by="vendor_memory",
    )
    entry_id = guards.create_journal_entry(
        runtime_conn,
        invoice_id=invoice_id,
        posting_authority="auto_clean",
        entry_date=date(2026, 7, 1),
        lines=BALANCED,
        actor="system",
    )
    entry = journal.get_for_invoice(runtime_conn, invoice_id)
    assert entry["id"] == entry_id
    assert entry["posting_authority"] == "auto_clean"
    assert entry["approval_id"] is None


def test_auto_clean_refuses_to_carry_an_approval_id(runtime_conn, sample_invoice):
    """A caller passing both is confused about which path it is on, and
    `ck_journal_human_approved_needs_approval` would not catch it — that CHECK
    only constrains the human_approved direction."""
    invoice_id = _insert_invoice(runtime_conn, sample_invoice)
    approval_id = approvals.open_approval(
        runtime_conn,
        invoice_id=invoice_id,
        thread_id=f"appr:{invoice_id}",
        interrupt_payload={},
        flags_snapshot={},
        suggestions={},
    )
    with pytest.raises(guards.GuardDenied, match="auto_clean"):
        guards.create_journal_entry(
            runtime_conn,
            invoice_id=invoice_id,
            posting_authority="auto_clean",
            approval_id=approval_id,
            entry_date=date(2026, 7, 1),
            lines=BALANCED,
            actor="system",
        )


def test_posting_the_same_invoice_twice_yields_one_entry(runtime_conn, sample_invoice):
    """Roadmap §1, measured 2026-07-27: `kill -9` INSIDE a post node, after its
    write and before its checkpoint commits, makes the node run again on resume
    → two journal entries. Node placement does not prevent that. This does, with
    UNIQUE(invoice_id) underneath it (case 11)."""
    from sqlalchemy import func, select

    invoice_id = _insert_invoice(runtime_conn, sample_invoice)
    first = guards.create_journal_entry(
        runtime_conn,
        invoice_id=invoice_id,
        posting_authority="auto_clean",
        entry_date=date(2026, 7, 1),
        lines=BALANCED,
        actor="system",
    )
    second = guards.create_journal_entry(
        runtime_conn,
        invoice_id=invoice_id,
        posting_authority="auto_clean",
        entry_date=date(2026, 7, 1),
        lines=BALANCED,
        actor="system",
    )
    assert second == first, "a resume must complete, not raise"

    count = runtime_conn.execute(
        select(func.count())
        .select_from(models.journal_entries)
        .where(models.journal_entries.c.invoice_id == invoice_id)
    ).scalar_one()
    assert count == 1

    replays = [r for r in _audit_rows(invoice_id) if r["reason"] == "idempotent replay"]
    assert len(replays) == 1


def test_reposting_with_different_totals_is_denied(runtime_conn, sample_invoice):
    """Returning the existing entry here would hide a genuine divergence, which
    is worse than a crash."""
    invoice_id = _insert_invoice(runtime_conn, sample_invoice)
    guards.create_journal_entry(
        runtime_conn,
        invoice_id=invoice_id,
        posting_authority="auto_clean",
        entry_date=date(2026, 7, 1),
        lines=BALANCED,
        actor="system",
    )
    divergent = [
        accounting.JournalLine("6422", "Chi phí quản lý", Decimal("999"), Decimal("0")),
        accounting.JournalLine("331", "Phải trả người bán", Decimal("0"), Decimal("999")),
    ]
    with pytest.raises(guards.GuardDenied, match="already posted"):
        guards.create_journal_entry(
            runtime_conn,
            invoice_id=invoice_id,
            posting_authority="auto_clean",
            entry_date=date(2026, 7, 1),
            lines=divergent,
            actor="system",
        )


