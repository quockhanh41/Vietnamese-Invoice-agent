# M5-A: the write guard and the auto path — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first production caller of the journal repository together with the code-layer guard that makes posting safe, and close the three persistence gaps that currently leave the database layer of the safety property guarding an empty table.

**Architecture:** `tools/guards.py` becomes the single door to `journal_entries`; it re-reads Postgres rather than trusting graph state, and is idempotent so a crash-resume completes instead of double-posting. `serial_join` in the batch graph gains the writes that make `invoice_flags`, `invoices.category`/`department` and `invoices.status` real. A new `auto_post` node walks `state["auto"]` and posts each clean invoice through the guard, in its own transaction.

**Tech Stack:** Python 3.13 · SQLAlchemy 2 Core · Alembic · LangGraph 1.2.9 (pinned) · Postgres 17 · pytest

**Spec:** [`docs/superpowers/specs/2026-08-01-m5a-write-guard-auto-post-design.md`](../specs/2026-08-01-m5a-write-guard-auto-post-design.md)

## Global Constraints

Copied from spec §0. Every task's requirements implicitly include these.

1. **The three layers must encode ONE rule, and each must bite alone.** Code (`tools/guards.py`), database (CHECK + trigger + UNIQUE), test (`test_never_post_without_approval.py`). Task 6's mutation table is how this is measured, not asserted.
2. **The guard re-reads Postgres. It never trusts the state it was handed.** Design §5.3 step 2 — graph state is forgeable through a time-travel fork.
3. **`info` severity is context for a human, never a block.** All three layers adopt this in M5-A.
4. **A category the model invented is never written.** `invoices.category` is a FK to `expense_categories.code`. Unmappable → `NULL` → routes to a human. No fabricated fallback.
5. **The serial join stays ONE transaction.** Gap-closing writes extend that node; they do not add a second commit point.
6. **The gate replays with every API key unset** (`GROQ_API_KEYS`, `GEMINI_API_KEYS`, `OPENROUTER_API_KEYS`), and the gate test must actually execute in CI.
7. **Run `make check` after EVERY task, not once at the end.** M4-C pushed seven commits as one batch, CI first saw the tip, and the tip was a docs commit that read as harmless. A milestone is not closed until `make check` is green on the *pushed* commit and CI agrees.
8. **Language:** all code comments, docstrings, commit messages and docs in **English**. Product output (flag narratives, report commentary, `keywords_vi`, UI copy) stays **Vietnamese**. M5-A emits no user-facing Vietnamese text, so everything it writes is English.
9. **Every non-obvious constant carries a comment with date + measurement + report filename.**
10. **Do not bump `langgraph==1.2.9` or `langgraph-checkpoint-postgres==3.1.0`.**

## Preconditions

Run once before Task 1:

```bash
make dev          # Postgres 17 + pgvector, migrations, seed
make generate     # corpus/ — 300 invoices, seed 42; gate_batch.json lands here too
make check        # must be green BEFORE any change, so a later red is attributable
```

If `make check` is not green at this point, stop and report — do not start on a red baseline.

## File Structure

**Create:**
- `src/invoice_agent/tools/__init__.py` — package marker, empty
- `src/invoice_agent/tools/guards.py` — `GuardDenied`, `create_journal_entry`, `_audit`
- `migrations/versions/<rev>_m5a_13_flag_severity_trigger.py` — trigger narrowed to `warn`/`critical`
- `tests/test_never_post_without_approval.py` — portfolio test 2, 11 cases
- `docs/notes/2026-08-01-m5a-write-guard.md` — closeout note (Task 10)

**Modify:**
- `src/invoice_agent/db/repositories/invoices.py` — `insert_with_lines` gains `category=`/`department=`
- `src/invoice_agent/graphs/batch.py` — `EnrichedInvoice.classification`; `enrich_one` propagates it; `_join` persists flags + category/department + status; `auto_post_node`; graph wiring
- `scripts/generator/gate_batch.py` — per-department budget chains
- `tests/test_schema_constraints.py` — `test_no_production_caller_of_journal_repository` narrowed to an allowlist

---

### Task 1: Migration 13 — narrow the trigger to `warn`/`critical`

Spec §2. This is the one place M5-A relaxes a database constraint, so it is measured before it is written.

**Files:**
- Create: `migrations/versions/<rev>_m5a_13_flag_severity_trigger.py`
- Test: `tests/test_schema_constraints.py` (append)

**Interfaces:**
- Consumes: nothing
- Produces: the DB predicate `severity IN ('warn','critical')` that `guards.create_journal_entry` mirrors in Task 3

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_schema_constraints.py`. Reuse whatever invoice-insert helper that file already uses for its existing flag tests (see `test_schema_constraints.py:87` and `:146` for the established pattern) rather than inventing a second one.

```python
def test_an_info_flag_does_not_block_auto_clean_posting(runtime_conn):
    """Migration 13. NEW_VENDOR is severity `info` and M4-B measured 51 first
    sightings across the corpus — on a fresh database that is nearly every
    invoice. Before this migration the trigger blocked auto_clean on ANY open
    flag, so persisting findings honestly (Task 7) would have made the auto path
    unreachable: exactly the 28-of-28 failure route_outcomes_node's docstring
    already measured in memory.
    """
    invoice_id = _insert_invoice(runtime_conn)
    flags.raise_flag(
        runtime_conn, invoice_id,
        code=findings.NEW_VENDOR, severity="info", raised_by="vendor_memory",
    )
    entry_id = journal.create_entry_with_lines(
        runtime_conn,
        invoice_id=invoice_id,
        entry_date=date(2026, 7, 1),
        posting_authority="auto_clean",
        lines=[
            journal.JournalLine("6422", "Chi phí quản lý", Decimal("100"), Decimal("0")),
            journal.JournalLine("331", "Phải trả người bán", Decimal("0"), Decimal("100")),
        ],
    )
    assert entry_id is not None


def test_a_warn_flag_still_blocks_auto_clean_posting(runtime_conn):
    """The half of the trigger migration 13 must NOT relax. This is the database
    layer of the three-layer safety property; if it stops biting, the property
    rests on application code alone.
    """
    invoice_id = _insert_invoice(runtime_conn)
    flags.raise_flag(
        runtime_conn, invoice_id,
        code=findings.PRICE_OUTLIER, severity="warn", raised_by="anomaly",
    )
    with pytest.raises(Exception, match="open flag"):
        journal.create_entry_with_lines(
            runtime_conn,
            invoice_id=invoice_id,
            entry_date=date(2026, 7, 1),
            posting_authority="auto_clean",
            lines=[
                journal.JournalLine("6422", "Chi phí quản lý", Decimal("100"), Decimal("0")),
                journal.JournalLine("331", "Phải trả người bán", Decimal("0"), Decimal("100")),
            ],
        )
```

- [ ] **Step 2: Run the tests to verify the first one fails**

```bash
.venv/bin/python -m pytest tests/test_schema_constraints.py -k "auto_clean_posting" -v
```

Expected: `test_an_info_flag_does_not_block_auto_clean_posting` **FAILS** with the trigger's `invoice ... has an open flag; auto_clean posting is forbidden`. `test_a_warn_flag_still_blocks_auto_clean_posting` **PASSES** already — it is the regression guard, and it must stay green through step 4.

- [ ] **Step 3: Write the migration**

```bash
.venv/bin/alembic revision -m "m5a_13_flag_severity_trigger"
```

Fill the generated file (keep the generated `revision` / `down_revision` values; `down_revision` must be `'9c82b5515db9'`):

```python
"""m5a_13_flag_severity_trigger

Revision ID: <generated>
Revises: 9c82b5515db9
Create Date: <generated>

"""
from collections.abc import Sequence

from alembic import op

revision: str = "<generated>"
down_revision: str | Sequence[str] | None = "9c82b5515db9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# WHY THIS NARROWS RATHER THAN TIGHTENS.
# Measured 2026-08-01, docs/notes/2026-08-01-m4d-batch-graph.md: routing on the
# mere existence of a finding sent 28 of 28 enriched invoices to a reviewer and
# left the auto path empty. The cause is NEW_VENDOR, severity `info`, which
# fires on every vendor's FIRST sighting — 51 of them across the corpus
# (docs/notes/2026-07-31-m4b-vendor-memory.md). `info` asserts something is NEW,
# which is true every time it fires, not that something is WRONG.
#
# Before M5-A this trigger was inert: batch.py raised flags only for duplicates
# and budgets, so the table it reads was nearly empty. M5-A persists every
# finding and signal (plan Task 7), at which point the trigger and the
# application's routing rule must agree or one of them is a bug that fires in
# production. They now both say: an open `warn` or `critical` flag blocks
# auto_clean.
_BODY_WITH_SEVERITY = """
CREATE OR REPLACE FUNCTION app.forbid_auto_clean_with_open_flags()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.posting_authority = 'auto_clean' AND EXISTS (
        SELECT 1 FROM app.invoice_flags
        WHERE invoice_id = NEW.invoice_id
          AND resolved_at IS NULL
          AND severity IN ('warn', 'critical')
    ) THEN
        RAISE EXCEPTION
            'invoice % has an open flag; auto_clean posting is forbidden',
            NEW.invoice_id;
    END IF;
    RETURN NEW;
END $$;
"""

_BODY_ANY_FLAG = """
CREATE OR REPLACE FUNCTION app.forbid_auto_clean_with_open_flags()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.posting_authority = 'auto_clean' AND EXISTS (
        SELECT 1 FROM app.invoice_flags
        WHERE invoice_id = NEW.invoice_id AND resolved_at IS NULL
    ) THEN
        RAISE EXCEPTION
            'invoice % has an open flag; auto_clean posting is forbidden',
            NEW.invoice_id;
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    # CREATE OR REPLACE only — the TRIGGER itself is unchanged and still points
    # at this function, so replacing the body is the whole migration.
    op.execute(_BODY_WITH_SEVERITY)


def downgrade() -> None:
    op.execute(_BODY_ANY_FLAG)
```

- [ ] **Step 4: Apply and verify both tests pass**

```bash
make migrate
.venv/bin/python -m pytest tests/test_schema_constraints.py -k "auto_clean_posting" -v
```

Expected: both PASS.

- [ ] **Step 5: Verify the migration is reversible and autogenerate stays clean**

```bash
.venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head
.venv/bin/python -m pytest tests/test_alembic_ignores_langgraph.py -v
```

Expected: downgrade/upgrade round-trips without error; the Alembic isolation guard stays green (a function-body migration must not make autogenerate emit anything).

- [ ] **Step 6: `make check` and commit**

```bash
make check
git add migrations/versions tests/test_schema_constraints.py
git commit -m "feat(m5a): narrow the auto_clean trigger to warn/critical severity

The trigger blocked auto_clean on ANY open flag. Persisting findings honestly
(Task 7) would then have made the auto path unreachable, because NEW_VENDOR is
severity info and fires on every vendor's first sighting — 51 across the corpus.

Measured 2026-08-01 in docs/notes/2026-08-01-m4d-batch-graph.md: routing on the
existence of a finding sent 28 of 28 invoices to a reviewer. All three layers
now encode one rule.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 2: `tools/guards.py` — the human_approved path

Spec §4.1 rules 1, 2 and the autonomous audit write.

**Files:**
- Create: `src/invoice_agent/tools/__init__.py`, `src/invoice_agent/tools/guards.py`
- Test: `tests/test_never_post_without_approval.py`

**Interfaces:**
- Consumes: `db.repositories.approvals.get_approval`, `db.repositories.journal.create_entry_with_lines`, `db.audit.record`, `domain.accounting.JournalLine`
- Produces:
  - `class GuardDenied(Exception)`
  - `def create_journal_entry(conn: Connection, *, invoice_id: uuid.UUID, posting_authority: str, entry_date: date, lines: Sequence[accounting.JournalLine], actor: str, approval_id: uuid.UUID | None = None) -> uuid.UUID`
  - Tasks 3, 4 and 8 extend and call exactly this signature.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_never_post_without_approval.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_never_post_without_approval.py -v
```

Expected: all FAIL with `ModuleNotFoundError: No module named 'invoice_agent.tools'`.

- [ ] **Step 3: Write the implementation**

Create `src/invoice_agent/tools/__init__.py` (empty file), then `src/invoice_agent/tools/guards.py`:

```python
"""The code layer of the three-layer safety property (design doc §2.7, §5.3).

THE ONLY PRODUCTION DOOR TO `journal_entries`. `repositories/journal.py` is
reachable from here and nowhere else — `test_no_production_caller_of_journal_repository`
enforces the allowlist.

Two rules carry this module, and both exist because state is not evidence:

  * `human_approved` re-reads `approvals` FROM POSTGRES. Design §5.3 step 2:
    graph state can be forged through a time-travel fork or a replay, so
    `decision == "approved"` arriving in an argument is an input, not proof.
  * `auto_clean` re-reads `invoice_flags` and refuses on any open `warn` or
    `critical` row — the same predicate migration 13 gave the trigger, so the
    code layer is never MORE PERMISSIVE than the layer beneath it.
"""

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import Connection

from invoice_agent.db import audit
from invoice_agent.db.repositories import approvals, invoices, journal
from invoice_agent.domain import accounting

# Decisions that authorise a post. `edited` is here deliberately: a reviewer who
# corrected a field and let the invoice through has approved it (spec §4.1).
POSTING_DECISIONS = ("approved", "edited")

ACTION = "tool.create_journal_entry"


class GuardDenied(Exception):
    """A write the guard refused. Always paired with an `audit_log` row."""


def _audit(
    *, actor: str, allowed: bool, invoice_id: uuid.UUID, reason: str, **detail: Any
) -> None:
    """Write the audit row on its OWN connection, committed independently.

    The guard runs inside the caller's transaction. A denial raises, the caller
    rolls back, and an audit row written on that same connection would vanish
    with it — design §5.3's "every refusal writes audit_log" would be silently
    false, and a test asserting the row exists would pass only because it never
    committed either. A log must not be rolled back by the thing it logs.

    Safe because `audit_log` is append-only and holds no FK to `invoices`.
    """
    from invoice_agent.db.engine import get_engine

    with get_engine().begin() as own:
        audit.record(
            own,
            actor=actor,
            action=f"{ACTION}.{'granted' if allowed else 'denied'}",
            allowed=allowed,
            target_table="journal_entries",
            target_id=str(invoice_id),
            reason=reason,
            detail=detail,
        )


def _deny(*, actor: str, invoice_id: uuid.UUID, reason: str, **detail: Any) -> GuardDenied:
    _audit(actor=actor, allowed=False, invoice_id=invoice_id, reason=reason, **detail)
    return GuardDenied(f"{invoice_id}: {reason}")


def create_journal_entry(
    conn: Connection,
    *,
    invoice_id: uuid.UUID,
    posting_authority: str,
    entry_date: date,
    lines: Sequence[accounting.JournalLine],
    actor: str,
    approval_id: uuid.UUID | None = None,
) -> uuid.UUID:
    if posting_authority not in ("auto_clean", "human_approved"):
        raise _deny(
            actor=actor,
            invoice_id=invoice_id,
            reason=f"unknown posting_authority {posting_authority!r}",
        )

    if posting_authority == "human_approved":
        if approval_id is None:
            raise _deny(
                actor=actor,
                invoice_id=invoice_id,
                reason="human_approved requires an approval_id",
            )
        approval = approvals.get_approval(conn, approval_id)
        if approval is None:
            raise _deny(
                actor=actor,
                invoice_id=invoice_id,
                reason=f"approval {approval_id} does not exist",
            )
        if approval["invoice_id"] != invoice_id:
            raise _deny(
                actor=actor,
                invoice_id=invoice_id,
                reason="approval belongs to a different invoice",
                approval_invoice_id=str(approval["invoice_id"]),
            )
        if approval["decision"] is None:
            raise _deny(
                actor=actor, invoice_id=invoice_id, reason="approval is not decided"
            )
        if approval["decision"] not in POSTING_DECISIONS:
            raise _deny(
                actor=actor,
                invoice_id=invoice_id,
                reason=f"approval was {approval['decision']}",
            )

    entry_id = journal.create_entry_with_lines(
        conn,
        invoice_id=invoice_id,
        entry_date=entry_date,
        posting_authority=posting_authority,
        approval_id=approval_id,
        posted_by=actor,
        status="posted",
        lines=[
            journal.JournalLine(
                account_code=line.account_code,
                account_name=line.account_name,
                debit=line.debit,
                credit=line.credit,
                description=line.description,
            )
            for line in lines
        ],
    )
    invoices.set_status(conn, invoice_id, "posted")
    _audit(
        actor=actor,
        allowed=True,
        invoice_id=invoice_id,
        reason="posted",
        entry_id=str(entry_id),
        posting_authority=posting_authority,
    )
    return entry_id
```

Note the two identical `JournalLine` dataclasses (`domain.accounting` and `db.repositories.journal`) — spec §3.2 records this as noted-not-fixed. The conversion above is where they meet. Do **not** refactor them in this milestone.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_never_post_without_approval.py -v
```

Expected: all PASS. The `auto_clean` cases do not exist yet — they are Task 3.

- [ ] **Step 5: `make check` and commit**

```bash
make check
git add src/invoice_agent/tools tests/test_never_post_without_approval.py
git commit -m "feat(m5a): add tools/guards.py with the human_approved path

The guard re-reads `approvals` from Postgres rather than trusting the decision
it was handed (design §5.3 step 2) — graph state is forgeable through a
time-travel fork. It also compares the approval's invoice_id, without which one
approved invoice would authorise posting any other.

The audit write uses its own connection and commits independently: the guard
runs inside the caller's transaction, so a denial's audit row would otherwise
roll back with the transaction it aborted.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 3: `guards.py` — the auto_clean path

Spec §4.1 rule 3. Same predicate as migration 13, so the code layer is never more permissive than the database.

**Files:**
- Modify: `src/invoice_agent/tools/guards.py`
- Test: `tests/test_never_post_without_approval.py` (append)

**Interfaces:**
- Consumes: `db.repositories.flags.open_flags_for` and the `create_journal_entry` signature from Task 2
- Produces: `BLOCKING_SEVERITIES = ("warn", "critical")` — Task 7 reads the same tuple when it sets `invoices.status`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_never_post_without_approval.py`:

```python
def test_auto_clean_is_denied_when_a_warn_flag_is_open(runtime_conn, sample_invoice):
    from invoice_agent.db.repositories import flags
    from invoice_agent.domain import findings

    invoice_id = _insert_invoice(runtime_conn, sample_invoice)
    flags.raise_flag(
        runtime_conn, invoice_id,
        code=findings.PRICE_OUTLIER, severity="warn", raised_by="anomaly",
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
        runtime_conn, invoice_id,
        code=findings.NEW_VENDOR, severity="info", raised_by="vendor_memory",
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
```

- [ ] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_never_post_without_approval.py -k auto_clean -v
```

Expected: `..._denied_when_a_warn_flag_is_open` FAILS (the guard has no flag check yet, so the *trigger* raises a `DBAPIError`, not `GuardDenied`); `..._refuses_to_carry_an_approval_id` FAILS (no check); `..._posts_over_an_info_flag` PASSES already thanks to Task 1.

- [ ] **Step 3: Implement**

In `src/invoice_agent/tools/guards.py`, add the constant next to `POSTING_DECISIONS`:

```python
# The SAME predicate migration 13 gave the trigger. Two layers, one rule: an
# open `warn` or `critical` flag blocks auto_clean, `info` does not. Task 7's
# status write reads this tuple too, so the three places cannot drift apart
# without the mutation in the closeout note going red.
BLOCKING_SEVERITIES = ("warn", "critical")
```

Add the import `from invoice_agent.db.repositories import approvals, flags, invoices, journal`, and insert this branch immediately after the `human_approved` block, before the call to `journal.create_entry_with_lines`:

```python
    else:  # auto_clean
        if approval_id is not None:
            raise _deny(
                actor=actor,
                invoice_id=invoice_id,
                reason="auto_clean must not carry an approval_id",
            )
        blocking = [
            f
            for f in flags.open_flags_for(conn, invoice_id)
            if f["severity"] in BLOCKING_SEVERITIES
        ]
        if blocking:
            raise _deny(
                actor=actor,
                invoice_id=invoice_id,
                reason="invoice has an open flag",
                codes=sorted({f["code"] for f in blocking}),
            )
```

- [ ] **Step 4: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_never_post_without_approval.py -v
```

Expected: all PASS.

- [ ] **Step 5: `make check` and commit**

```bash
make check
git add src/invoice_agent/tools/guards.py tests/test_never_post_without_approval.py
git commit -m "feat(m5a): add the auto_clean path to the write guard

Denies on any open warn/critical flag — the same predicate migration 13 gave
the trigger, so the code layer is never more permissive than the layer beneath
it. info does not block: it asserts something is NEW, not that something is
WRONG, and NEW_VENDOR fires 51 times across the corpus.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 4: `guards.py` — idempotency

Spec §4.2. Roadmap §1 measured that `kill -9` inside a post node makes it run again on resume and write a **second** journal entry. Graph shape does not stop that; this does.

**Files:**
- Modify: `src/invoice_agent/tools/guards.py`
- Test: `tests/test_never_post_without_approval.py` (append)

**Interfaces:**
- Consumes: `journal.get_for_invoice`
- Produces: no new names; `create_journal_entry` becomes safe to call twice

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_never_post_without_approval.py -k "twice or different_totals" -v
```

Expected: both FAIL with an `IntegrityError` on `journal_entries.invoice_id` — the UNIQUE constraint firing, which is the layer this task puts a guard in front of.

- [ ] **Step 3: Implement**

In `create_journal_entry`, insert this block after the authority checks and **before** `journal.create_entry_with_lines`:

```python
    total = sum((line.debit for line in lines), Decimal("0"))
    existing = journal.get_for_invoice(conn, invoice_id)
    if existing is not None:
        return _resolve_existing(
            existing,
            actor=actor,
            invoice_id=invoice_id,
            total=total,
            posting_authority=posting_authority,
        )
```

Then wrap the insert so the concurrent-race path lands in the same fork:

```python
    try:
        entry_id = journal.create_entry_with_lines(...)   # unchanged call from Task 2
    except IntegrityError:
        # Two processes both passed the existence check above. The constraint is
        # the arbiter; re-read and take the same fork. `conn` is unusable after
        # an IntegrityError, so the re-read runs on its own connection.
        from invoice_agent.db.engine import get_engine

        with get_engine().begin() as fresh:
            raced = journal.get_for_invoice(fresh, invoice_id)
        assert raced is not None, "IntegrityError on invoice_id with no row to show for it"
        return _resolve_existing(
            raced,
            actor=actor,
            invoice_id=invoice_id,
            total=total,
            posting_authority=posting_authority,
        )
```

And add the shared resolver plus its imports (`from decimal import Decimal`, `from sqlalchemy.exc import IntegrityError`):

```python
def _resolve_existing(
    existing: dict[str, Any],
    *,
    actor: str,
    invoice_id: uuid.UUID,
    total: Decimal,
    posting_authority: str,
) -> uuid.UUID:
    """An entry already exists. Same entry → idempotent success; different entry
    → denial.

    Decimal("100") and Decimal("100.00") are equal under `==` but not under
    `str`, and Postgres NUMERIC round-trips the scale it was given — so this
    compares Decimals, never their text.
    """
    same = (
        Decimal(existing["total_debit"]) == total
        and existing["posting_authority"] == posting_authority
    )
    if not same:
        raise _deny(
            actor=actor,
            invoice_id=invoice_id,
            reason="already posted with different content",
            existing_total=str(existing["total_debit"]),
            attempted_total=str(total),
        )
    _audit(
        actor=actor,
        allowed=True,
        invoice_id=invoice_id,
        reason="idempotent replay",
        entry_id=str(existing["id"]),
    )
    return uuid.UUID(str(existing["id"]))
```

- [ ] **Step 4: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_never_post_without_approval.py -v
```

Expected: all PASS.

- [ ] **Step 5: `make check` and commit**

```bash
make check
git add src/invoice_agent/tools/guards.py tests/test_never_post_without_approval.py
git commit -m "feat(m5a): make create_journal_entry idempotent

Roadmap §1, measured 2026-07-27: kill -9 inside a post node makes it run again
on resume and write a SECOND entry. Node placement does not prevent that.

Identical replay returns the existing id so a resume completes; divergent
content is DENIED rather than silently accepted. The IntegrityError path covers
the race where two processes both pass the existence check — the constraint is
the arbiter and the guard just interprets it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 5: Database-layer cases — the guard bypassed

Spec §5, cases 9–11. Without these the database mutations in Task 6 cannot go red: an intact guard refuses first and the constraint beneath it is never reached.

**Files:**
- Test: `tests/test_never_post_without_approval.py` (append)

**Interfaces:**
- Consumes: `db.repositories.journal` **directly**, deliberately bypassing `tools.guards`
- Produces: the three cases Task 6's DB mutations turn red

- [ ] **Step 1: Write the tests**

```python
# ---------------------------------------------------------------------------
# Database layer. These call `repositories.journal` DIRECTLY, bypassing the
# guard, so each constraint is exercised alone. An intact guard refuses first,
# which is why the guard-layer cases above cannot measure these.
# ---------------------------------------------------------------------------


def test_the_trigger_blocks_auto_clean_with_the_guard_bypassed(runtime_conn, sample_invoice):
    from invoice_agent.db.repositories import flags
    from invoice_agent.domain import findings

    invoice_id = _insert_invoice(runtime_conn, sample_invoice)
    flags.raise_flag(
        runtime_conn, invoice_id,
        code=findings.ARITHMETIC_MISMATCH, severity="critical", raised_by="validation",
    )
    with pytest.raises(Exception, match="open flag"):
        journal.create_entry_with_lines(
            runtime_conn,
            invoice_id=invoice_id,
            entry_date=date(2026, 7, 1),
            posting_authority="auto_clean",
            lines=[
                journal.JournalLine("6422", "Chi phí quản lý", Decimal("100"), Decimal("0")),
                journal.JournalLine("331", "Phải trả người bán", Decimal("0"), Decimal("100")),
            ],
        )


def test_the_check_blocks_human_approved_without_an_approval(runtime_conn, sample_invoice):
    """`ck_journal_human_approved_needs_approval`. Without it, 'human_approved'
    is just a string the application writes."""
    invoice_id = _insert_invoice(runtime_conn, sample_invoice)
    with pytest.raises(Exception, match="ck_journal_human_approved_needs_approval"):
        journal.create_entry_with_lines(
            runtime_conn,
            invoice_id=invoice_id,
            entry_date=date(2026, 7, 1),
            posting_authority="human_approved",
            approval_id=None,
            lines=[
                journal.JournalLine("6422", "Chi phí quản lý", Decimal("100"), Decimal("0")),
                journal.JournalLine("331", "Phải trả người bán", Decimal("0"), Decimal("100")),
            ],
        )


def test_unique_blocks_a_second_entry_with_the_guard_bypassed(runtime_conn, sample_invoice):
    """UNIQUE(invoice_id). Note what this measures that the idempotency case
    does NOT: the guard's existence read returns the prior entry before any
    insert is attempted, so dropping this constraint leaves that case green.
    Claiming one covers the other is the shape of error M1's exit review found
    four times."""
    invoice_id = _insert_invoice(runtime_conn, sample_invoice)
    lines = [
        journal.JournalLine("6422", "Chi phí quản lý", Decimal("100"), Decimal("0")),
        journal.JournalLine("331", "Phải trả người bán", Decimal("0"), Decimal("100")),
    ]
    journal.create_entry_with_lines(
        runtime_conn,
        invoice_id=invoice_id,
        entry_date=date(2026, 7, 1),
        posting_authority="auto_clean",
        lines=lines,
    )
    with pytest.raises(Exception, match="journal_entries_invoice_id_key|duplicate key"):
        journal.create_entry_with_lines(
            runtime_conn,
            invoice_id=invoice_id,
            entry_date=date(2026, 7, 1),
            posting_authority="auto_clean",
            lines=lines,
        )
```

- [ ] **Step 2: Run — these should pass immediately**

```bash
.venv/bin/python -m pytest tests/test_never_post_without_approval.py -k "bypassed or the_check_blocks" -v
```

Expected: all PASS. The constraints already exist (migrations 07 and Task 1); these tests exist to make them *falsifiable* in Task 6. If one fails, a constraint is missing — stop and report before continuing.

- [ ] **Step 3: `make check` and commit**

```bash
make check
git add tests/test_never_post_without_approval.py
git commit -m "test(m5a): exercise each DB constraint with the guard bypassed

Three cases calling repositories.journal directly. Without them the database
mutations cannot go red — an intact guard refuses first and the constraint
beneath it is never reached, so the DB layer would be untested while appearing
covered.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 6: Mutation verification — five mutations, five layers

Spec §5.1. Its own task because M1's exit review found **four** tests that passed while guarding nothing, and M2/M3/M4 each found more. Each mutation is applied to source, the named case is run, the mutation is reverted.

**Files:**
- Modify (temporarily, reverted each time): `src/invoice_agent/tools/guards.py`, database constraints
- Create: `docs/notes/2026-08-01-m5a-write-guard.md` (started here, finished in Task 10)

**Interfaces:**
- Consumes: everything from Tasks 1–5
- Produces: the mutation table the closeout note publishes

- [ ] **Step 1: Mutation 1 — delete the approvals re-read**

In `guards.py`, replace the whole `if posting_authority == "human_approved":` block body with `pass`. Run:

```bash
.venv/bin/python -m pytest tests/test_never_post_without_approval.py -k "undecided or rejected_approval or different_invoice" -v
```

Expected: **RED**, 3 failed. Record the count. `git checkout src/invoice_agent/tools/guards.py`.

- [ ] **Step 2: Mutation 2 — narrow the guard to `critical` only**

Change `BLOCKING_SEVERITIES = ("warn", "critical")` to `("critical",)`. Run:

```bash
.venv/bin/python -m pytest tests/test_never_post_without_approval.py -k "warn_flag_is_open" -v
```

Expected: **RED**. Record. Revert.

- [ ] **Step 3: Mutation 3 — drop the trigger**

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c \
  "DROP TRIGGER trg_forbid_auto_clean_with_open_flags ON app.journal_entries"
.venv/bin/python -m pytest tests/test_never_post_without_approval.py -k "trigger_blocks_auto_clean" -v
```

Expected: **RED**. Restore:

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c \
  "CREATE TRIGGER trg_forbid_auto_clean_with_open_flags BEFORE INSERT OR UPDATE ON app.journal_entries FOR EACH ROW EXECUTE FUNCTION app.forbid_auto_clean_with_open_flags()"
```

- [ ] **Step 4: Mutation 4 — drop the CHECK**

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c \
  "ALTER TABLE app.journal_entries DROP CONSTRAINT ck_journal_human_approved_needs_approval"
.venv/bin/python -m pytest tests/test_never_post_without_approval.py -k "check_blocks_human_approved" -v
```

Expected: **RED**. Restore:

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c \
  "ALTER TABLE app.journal_entries ADD CONSTRAINT ck_journal_human_approved_needs_approval CHECK (posting_authority <> 'human_approved' OR approval_id IS NOT NULL)"
```

- [ ] **Step 5: Mutation 5 — drop UNIQUE, and confirm what it does NOT turn red**

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c \
  "ALTER TABLE app.journal_entries DROP CONSTRAINT journal_entries_invoice_id_key"
.venv/bin/python -m pytest tests/test_never_post_without_approval.py -k "unique_blocks_a_second_entry" -v
.venv/bin/python -m pytest tests/test_never_post_without_approval.py -k "twice_yields_one_entry" -v
```

Expected: the first is **RED**, the second is **GREEN**. That asymmetry is the point — record both results. The idempotency case measures the guard; case 11 measures the constraint. Restore:

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c \
  "ALTER TABLE app.journal_entries ADD CONSTRAINT journal_entries_invoice_id_key UNIQUE (invoice_id)"
```

- [ ] **Step 6: Confirm the database is back to head, then write the table**

```bash
make migrate
.venv/bin/python -m pytest tests/test_never_post_without_approval.py -v
```

Expected: all PASS. Then create `docs/notes/2026-08-01-m5a-write-guard.md` with a `## Mutation results` section holding the five rows and the asymmetry note from step 5, recording the **measured** pass/fail counts — not the expected ones.

- [ ] **Step 7: `make check` and commit**

```bash
make check
git add docs/notes/2026-08-01-m5a-write-guard.md
git commit -m "docs(m5a): record the five-mutation verification of the safety property

Each mutation turns a different case red, so each of the three layers is shown
to bite alone. Records the asymmetry that matters: dropping UNIQUE(invoice_id)
leaves the idempotency case GREEN, because the guard's existence read returns
the prior entry before any insert is attempted.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 7: Close the persistence gaps in `serial_join`

Spec §4.3. Flags, category/department, status — all inside the batch's single commit point.

**Files:**
- Modify: `src/invoice_agent/graphs/batch.py` (`EnrichedInvoice`, `enrich_one`, `_join`)
- Modify: `src/invoice_agent/db/repositories/invoices.py` (`insert_with_lines`)
- Test: `tests/test_batch_serial_join.py` (append — it already holds the `_join` tests)

**Interfaces:**
- Consumes: `domain.findings.Finding`, `db.repositories.flags.raise_flag`, `db.repositories.reference.get_category`, `db.repositories.invoices.set_status`, `tools.guards.BLOCKING_SEVERITIES`
- Produces:
  - `EnrichedInvoice["classification"]: ClassificationResult | None`
  - `invoices.insert_with_lines(..., category: str | None = None, department: str | None = None)`
  - populated `invoice_flags` rows, on which Task 8's guard depends

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_batch_serial_join.py`, following the `_join` invocation pattern already in that file:

```python
def test_validation_findings_reach_the_invoice_flags_table(owner_conn):
    """Gap 1. Before M5-A, batch.py raised flags ONLY for duplicates and budgets
    (batch.py:214 and :303 were the only callers), so every validation finding
    and every signal was discarded when the batch ended — and the
    forbid_auto_clean_with_open_flags trigger, the DATABASE layer of the safety
    property, fired on an almost-empty table."""
    from invoice_agent.db.repositories import flags
    from invoice_agent.domain import findings

    state = _state_with_one_invoice(
        owner_conn,
        findings=[findings.make(findings.ARITHMETIC_MISMATCH, expected="100", actual="110")],
        signals=[findings.make(findings.NEW_VENDOR, tax_code="0100000003")],
    )
    result = batch.serial_join(state, conn=owner_conn)

    db_id = uuid.UUID(result["persisted"][0]["db_id"])
    codes = {f["code"] for f in flags.open_flags_for(owner_conn, db_id)}
    assert findings.ARITHMETIC_MISMATCH in codes
    assert findings.NEW_VENDOR in codes


def test_the_classified_category_and_department_are_persisted(owner_conn):
    """Gap 2. insert_with_lines omitted both columns, so the classification
    result was discarded and auto_post had no category from which to pick TT133
    accounts."""
    from invoice_agent.db.repositories import invoices as invoices_repo

    state = _state_with_one_invoice(
        owner_conn, category="OFFICE_SUPPLIES", department="ADMIN"
    )
    result = batch.serial_join(state, conn=owner_conn)

    row = invoices_repo.get_by_id(owner_conn, uuid.UUID(result["persisted"][0]["db_id"]))
    assert row["category"] == "OFFICE_SUPPLIES"
    assert row["department"] == "ADMIN"


def test_a_category_outside_the_seed_lands_as_NULL_not_invented(owner_conn):
    """Spec §0 constraint 4. A model proposed OFFICE_REPAIR_MAINTENANCE live on
    2026-08-01 (agents/models.py). A category the seed does not know becomes
    NULL, which routes the invoice to a human — it is never coerced to a
    neighbouring category and never written raw, which would raise IntegrityError
    against the FK and kill the batch."""
    from invoice_agent.db.repositories import invoices as invoices_repo

    state = _state_with_one_invoice(owner_conn, category="OFFICE_REPAIR_MAINTENANCE")
    result = batch.serial_join(state, conn=owner_conn)

    row = invoices_repo.get_by_id(owner_conn, uuid.UUID(result["persisted"][0]["db_id"]))
    assert row["category"] is None


def test_status_reflects_whether_a_blocking_flag_persisted(owner_conn):
    """Gap 3. `set_status` had zero callers, so every invoice sat at 'extracted'
    forever and nothing downstream could tell a clean invoice from a flagged
    one."""
    from invoice_agent.db.repositories import invoices as invoices_repo
    from invoice_agent.domain import findings

    clean = _state_with_one_invoice(owner_conn)
    clean_result = batch.serial_join(clean, conn=owner_conn)
    clean_row = invoices_repo.get_by_id(
        owner_conn, uuid.UUID(clean_result["persisted"][0]["db_id"])
    )
    assert clean_row["status"] == "validated"

    dirty = _state_with_one_invoice(
        owner_conn,
        findings=[findings.make(findings.ARITHMETIC_MISMATCH, expected="1", actual="2")],
    )
    dirty_result = batch.serial_join(dirty, conn=owner_conn)
    dirty_row = invoices_repo.get_by_id(
        owner_conn, uuid.UUID(dirty_result["persisted"][0]["db_id"])
    )
    assert dirty_row["status"] == "flagged"


def test_an_info_only_invoice_is_validated_not_flagged(owner_conn):
    """NEW_VENDOR alone must not read as a defect — the same rule as the trigger
    and the guard, in a third place, so all three can be shown to agree."""
    from invoice_agent.db.repositories import invoices as invoices_repo
    from invoice_agent.domain import findings

    state = _state_with_one_invoice(
        owner_conn, signals=[findings.make(findings.NEW_VENDOR, tax_code="0100000003")]
    )
    result = batch.serial_join(state, conn=owner_conn)
    row = invoices_repo.get_by_id(owner_conn, uuid.UUID(result["persisted"][0]["db_id"]))
    assert row["status"] == "validated"
```

Add the local helper at the top of the appended block. Give the invoice a unique natural key per call for the reason recorded in Task 2 step 1:

```python
def _state_with_one_invoice(
    conn, *, findings=None, signals=None, category=None, department="ADMIN"
) -> dict:
    """A minimal BatchState carrying exactly one enriched invoice."""
    import pathlib

    from invoice_agent.extraction.xml_parser import parse_invoice_xml

    data = parse_invoice_xml(
        pathlib.Path("tests/fixtures/real_samples/viettelpost_gtgt_2.1.0.xml").read_bytes()
    ).model_copy(update={"invoice_number": uuid.uuid4().hex[:8]})
    return {
        "batch_id": str(uuid.uuid4()),
        "inputs": [],
        "enriched": [
            {
                "invoice_id": "INV-TEST",
                "source_path": "x.xml",
                "source_type": "xml",
                "department": department,
                "extracted": data,
                "findings": findings or [],
                "signals": signals or [],
                "classification": None if category is None else _fake_classification(category, department),
                "status": "enriched",
            }
        ],
        "failed": [],
        "persisted": [],
        "consumption": [],
        "raised": [],
        "unroutable": [],
        "auto": [],
        "needs_approval": [],
        "status": "",
    }


def _fake_classification(category: str, department: str):
    """A stand-in for ClassificationResult that carries only the two fields
    `_join` reads. The real model is a frozen Literal-constrained pydantic
    model, and `OFFICE_REPAIR_MAINTENANCE` is deliberately NOT in its Literal —
    so the out-of-seed test could not construct one."""
    from types import SimpleNamespace

    return SimpleNamespace(category=category, department=department)
```

- [ ] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_batch_serial_join.py -k "flags_table or persisted or NULL_not_invented or status" -v
```

Expected: all FAIL — no flags written, `category` is `None` in every case, `status` is `'extracted'`.

- [ ] **Step 3: Widen `insert_with_lines`**

In `src/invoice_agent/db/repositories/invoices.py`, add the parameters to the signature:

```python
def insert_with_lines(
    conn: Connection,
    data: InvoiceData,
    *,
    batch_id: uuid.UUID | None = None,
    source_type: str = "xml",
    source_path: str | None = None,
    raw_xml: str | None = None,
    category: str | None = None,
    department: str | None = None,
) -> uuid.UUID | None:
```

Before the `insert_row` call, add the seed lookup:

```python
    # `category` and `department` are FKs to the seeded reference tables, so a
    # value the model invented cannot be written at all. The lookup exists so the
    # batch gets a routable invoice instead of an IntegrityError — measured
    # 2026-08-01: a live gemini-3.5-flash verdict proposed
    # OFFICE_REPAIR_MAINTENANCE, a category no seed row carries
    # (agents/models.py). NULL routes the invoice to a human; nothing is
    # coerced to a neighbouring category.
    if category is not None and reference.get_category(conn, category) is None:
        category = None
    if department is not None and not any(
        d["code"] == department for d in reference.list_departments(conn)
    ):
        department = None
```

Add `"category": category, "department": department,` to the `insert_row` values dict, and `reference` to the repository imports.

- [ ] **Step 4: Carry `classification` through `enrich_one`**

In `src/invoice_agent/graphs/batch.py`, add to `EnrichedInvoice`:

```python
    # Read by `_join` to persist `invoices.category`/`department`, and by
    # `auto_post` to pick TT133 accounts. Carried rather than re-derived: the
    # classification is an LLM output and re-running it in the serial join would
    # both cost tokens and risk a different answer than the one that was scored.
    classification: Any
```

and to the dict `enrich_one` returns:

```python
                "classification": result.get("classification"),
```

- [ ] **Step 5: Persist flags, category/department and status in `_join`**

In `_join`, pass the classification through to the insert:

```python
        cls = e.get("classification")
        db_id = invoices.insert_with_lines(
            conn,
            inv,
            batch_id=batch_uuid,
            source_type=e["source_type"],
            source_path=e["source_path"],
            category=getattr(cls, "category", None),
            department=e.get("department") or getattr(cls, "department", None),
        )
```

Immediately after `persisted.append(...)`, add:

```python
        # Gap 1 (spec §1.1). Every finding and signal the enrichment produced
        # becomes a row, because the trigger and the reviewer queue both read
        # this table and neither can see graph state. `severity`/`raised_by`
        # come from CODE_REGISTRY via `findings.make`, so a caller cannot
        # declare its own — and every source it can produce is already in
        # FLAG_SOURCES, so no CHECK needs widening.
        for finding in (*e.get("findings", []), *e.get("signals", [])):
            flags.raise_flag(
                conn,
                db_id,
                code=finding.code,
                severity=finding.severity,
                raised_by=finding.raised_by,
                detail=finding.detail,
            )
```

At the very **end** of `_join`, after the budget loop, add the status pass and include its output in the return dict:

```python
    # Status is set LAST and read back from the table, not accumulated in
    # Python. Budget flags are raised after the per-invoice loop, so an invoice
    # that is clean at insert time can acquire a BUDGET_BREACH before this node
    # returns — computing status earlier would record it as `validated` while
    # `invoice_flags` says otherwise, and the guard reads the table.
    for invoice_key, db_id in db_id_map.items():
        blocking = [
            f
            for f in flags.open_flags_for(conn, db_id)
            if f["severity"] in guards.BLOCKING_SEVERITIES
        ]
        invoices.set_status(conn, db_id, "flagged" if blocking else "validated")
```

Import `guards` at the top of `_join`'s local import block (`from invoice_agent.tools import guards`), matching the module's existing function-local import style.

- [ ] **Step 6: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_batch_serial_join.py tests/test_batch_graph.py tests/test_budget_serialization.py -v
```

Expected: all PASS, including the pre-existing M4-D tests. If `test_budget_serialization.py` goes red, the status pass has broken the byte-identical chain — stop and report rather than adjusting the test.

- [ ] **Step 7: `make check` and commit**

```bash
make check
git add src/invoice_agent/graphs/batch.py src/invoice_agent/db/repositories/invoices.py tests/test_batch_serial_join.py
git commit -m "feat(m5a): persist flags, classification and status in the serial join

Three gaps measured 2026-08-01 by reading the code:

  * batch.py:214 and :303 were the only raise_flag callers, so validation
    findings and signals were discarded when the batch ended — and the
    forbid_auto_clean_with_open_flags trigger fired on an almost-empty table;
  * insert_with_lines omitted category/department, discarding the
    classification result auto_post needs to pick TT133 accounts;
  * set_status had zero callers, so every invoice sat at 'extracted' forever.

All three writes extend the existing single commit point rather than adding a
second (M4-D constraint 2). A category outside the seed lands as NULL and routes
to a human; it is never coerced.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 8: `auto_post` node

Spec §4.4. The first production caller of the journal repository, through the guard.

**Files:**
- Modify: `src/invoice_agent/graphs/batch.py`
- Test: `tests/test_batch_graph.py` (append)

**Interfaces:**
- Consumes: `tools.guards.create_journal_entry`, `domain.accounting.build_journal_entry`, `domain.accounting.CategoryAccounts`, `db.repositories.reference.get_category`
- Produces: `auto_post_node(state: BatchState) -> dict[str, Any]` returning `{"posted": [...], "auto": [...], "needs_approval": [...]}`; `BatchState["posted"]: list[dict[str, Any]]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_batch_graph.py`:

```python
def test_a_clean_invoice_with_a_mappable_category_posts(owner_conn):
    """The auto path, end to end: category -> seeded TT133 accounts -> balanced
    entry -> guard -> journal_entries with posting_authority='auto_clean' and no
    approval."""
    from invoice_agent.db.repositories import journal

    state = _joined_state(owner_conn, category="OFFICE_SUPPLIES")
    result = batch.auto_post_node(state)

    assert len(result["posted"]) == 1
    db_id = uuid.UUID(result["posted"][0]["db_id"])
    entry = journal.get_for_invoice(owner_conn, db_id)
    assert entry["posting_authority"] == "auto_clean"
    assert entry["approval_id"] is None
    assert entry["total_debit"] == entry["total_credit"]


def test_a_null_category_is_not_posted_but_routed_to_a_human(owner_conn):
    """Spec §0 constraint 4 in its consequential form. An invoice whose category
    the seed does not know has no account to book to, and inventing one is the
    failure M4-D removed when it deleted the fabricated Decimal("50000000")
    budget limit."""
    state = _joined_state(owner_conn, category=None)
    result = batch.auto_post_node(state)

    assert result["posted"] == []
    assert result["auto"] == []
    assert len(result["needs_approval"]) == 1


def test_a_guard_denial_moves_the_invoice_to_needs_approval(owner_conn):
    """One refusal must not end the batch. Same argument as M4-D's per-task
    try/except in the fan-out, applied to the write path."""
    from invoice_agent.db.repositories import flags
    from invoice_agent.domain import findings

    state = _joined_state(owner_conn, category="OFFICE_SUPPLIES")
    db_id = uuid.UUID(state["persisted"][0]["db_id"])
    flags.raise_flag(
        owner_conn, db_id,
        code=findings.PRICE_OUTLIER, severity="warn", raised_by="anomaly",
    )
    owner_conn.commit()

    result = batch.auto_post_node(state)
    assert result["posted"] == []
    assert len(result["needs_approval"]) == 1


def test_the_graph_wires_auto_post_between_route_outcomes_and_finalize():
    graph = batch.build_batch_graph()
    nodes = graph.get_graph().nodes
    assert "auto_post" in nodes
```

`_joined_state` builds a state whose invoice is already persisted (run `serial_join` first, then commit), so `auto_post_node`'s own connections can see it. Follow the fixture style already in `tests/test_batch_graph.py` for building and committing batch state.

- [ ] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_batch_graph.py -k "auto_post or mappable_category or null_category or guard_denial" -v
```

Expected: all FAIL with `AttributeError: module 'invoice_agent.graphs.batch' has no attribute 'auto_post_node'`.

- [ ] **Step 3: Implement the node**

Add `posted: list[dict[str, Any]]` to `BatchState`, then add to `src/invoice_agent/graphs/batch.py`:

```python
def auto_post_node(state: BatchState) -> dict[str, Any]:
    """Post every clean invoice through the guard — design §4's auto_post arm.

    WHAT THIS IS ALLOWED TO BOOK, stated because it is M5-A's one real risk.
    The account codes come from the invoice's category, which the
    ClassificationAgent chose (95.4% accurate, M4-C). The LLM proposes; the
    category -> account map is deterministic seed data; zero open warn/critical
    flags is enforced by the database. A MIS-CLASSIFIED CLEAN INVOICE THEREFORE
    BOOKS TO 6421 INSTEAD OF 6422 WITH NOBODY LOOKING. That is an accuracy cost,
    not a breach of the safety property — the LLM still never toggles a flag and
    never decides that posting is permitted.

    ONE TRANSACTION PER INVOICE. A single bad entry must not roll back every
    other entry in the batch; the same argument `enrich_one`'s try/except makes
    for the fan-out, applied to the write path.
    """
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import invoices as invoices_repo
    from invoice_agent.db.repositories import reference
    from invoice_agent.domain import accounting
    from invoice_agent.tools import guards

    db_ids = {p["invoice_id"]: uuid.UUID(p["db_id"]) for p in state.get("persisted") or []}
    by_id = {e["invoice_id"]: e for e in state.get("enriched") or []}

    posted: list[dict[str, Any]] = []
    still_auto: list[str] = []
    to_human: list[str] = list(state.get("needs_approval") or [])

    for invoice_key in state.get("auto") or []:
        db_id = db_ids.get(invoice_key)
        entry = by_id.get(invoice_key)
        if db_id is None or entry is None or entry.get("extracted") is None:
            # Routed rather than dropped: an invoice in `auto` that never
            # persisted is a state a reviewer has to see. M4-D added
            # `unroutable` for the same reason.
            to_human.append(invoice_key)
            continue

        with get_engine().begin() as conn:
            row = invoices_repo.get_by_id(conn, db_id)
            category = row["category"] if row else None
            cat_row = reference.get_category(conn, category) if category else None
            if cat_row is None:
                to_human.append(invoice_key)
                continue

            accounts = accounting.CategoryAccounts(
                debit_account=cat_row["debit_account"],
                debit_name=cat_row["name_vi"],
                vat_account=cat_row["vat_account"],
                vat_name="Thuế GTGT được khấu trừ" if cat_row["vat_account"] else None,
                credit_account=cat_row["credit_account"],
                credit_name="Phải trả cho người bán",
            )
            lines = accounting.build_journal_entry(entry["extracted"], accounts)
            try:
                entry_id = guards.create_journal_entry(
                    conn,
                    invoice_id=db_id,
                    posting_authority="auto_clean",
                    entry_date=entry["extracted"].issue_date,
                    lines=lines,
                    actor="system",
                )
            except guards.GuardDenied:
                # The guard already wrote the audit row, on its own connection,
                # so it survives this transaction being abandoned.
                to_human.append(invoice_key)
                continue

        posted.append(
            {"invoice_id": invoice_key, "db_id": str(db_id), "entry_id": str(entry_id)}
        )
        still_auto.append(invoice_key)

    return {"posted": posted, "auto": still_auto, "needs_approval": to_human}
```

The account *names* on the credit and VAT sides are Vietnamese by design — they land in `journal_entry_lines.account_name`, which a Vietnamese accountant reads. CLAUDE.md's language rule keeps product output Vietnamese.

- [ ] **Step 4: Wire it into the graph**

In `build_batch_graph`, replace the `route_outcomes → finalize` edge:

```python
    builder.add_node("auto_post", auto_post_node)
    builder.add_edge("serial_join", "route_outcomes")
    builder.add_edge("route_outcomes", "auto_post")
    builder.add_edge("auto_post", "finalize")
```

`open_approval` — design §4's other arm — is M5-B and is deliberately absent.

- [ ] **Step 5: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_batch_graph.py -v
```

Expected: all PASS.

- [ ] **Step 6: Regenerate the committed diagram**

`test_the_committed_diagram_matches_the_graph` (M3-C's drift guard) will be red until the mermaid is regenerated:

```bash
.venv/bin/python -m scripts.render_graph
.venv/bin/python -m pytest -k "committed_diagram" -v
```

- [ ] **Step 7: `make check` and commit**

```bash
make check
git add src/invoice_agent/graphs/batch.py tests/test_batch_graph.py docs/diagrams
git commit -m "feat(m5a): add the auto_post node, the first caller of the journal repo

Walks state['auto'], maps each invoice's category to its seeded TT133 accounts,
and posts through tools/guards.py. One transaction per invoice: a single bad
entry must not roll back every other entry in the batch.

A NULL category, an unseeded category or a guard denial moves the invoice to
needs_approval rather than ending the batch — and no account is invented for it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 9: Per-department gate-batch declaration

Spec §4.5. `gate_batch.json` pins the MKT chain only; M4-D's gate raised **9** `BUDGET_BREACH` flags and 8 were correct-but-undeclared, so "exact agreement" overclaimed.

**Files:**
- Modify: `scripts/generator/gate_batch.py`
- Test: `tests/test_gate_batch.py` (append)

**Interfaces:**
- Consumes: `scripts.seed_reference.BUDGETS` (already the single source of the limits — item 2.3's note forbids a second copy)
- Produces: `gate_batch.json` key `balance_chains: {department: [{invoice_id, balance_before, balance_after}]}`, replacing `balance_after_chain`

- [ ] **Step 1: Write the failing test**

```python
def test_every_department_in_the_batch_declares_a_budget_chain(tmp_path):
    """M4-D's gate raised 9 BUDGET_BREACH flags; gate_batch.json declared 1.
    The other 8 were correct behaviour in departments the declaration never
    covered, so "exact agreement between the declaration and what the batch
    flags" was measuring one department and claiming all of them."""
    payload = _write_and_load(tmp_path)
    departments_in_batch = {r["department"] for r in _gate_records(tmp_path)}
    assert set(payload["balance_chains"]) == departments_in_batch


def test_the_declared_chains_match_domain_budget(tmp_path):
    """The declaration is arithmetic over the manifest, so it cannot drift from
    the corpus it describes — the same property `write_gate_batch` already had
    for MKT, extended to every department."""
    from decimal import Decimal

    from invoice_agent.domain import budget as domain_budget
    from scripts.seed_reference import BUDGETS

    payload = _write_and_load(tmp_path)
    limits = {dept: limit for dept, _period, _cat, limit in BUDGETS}

    for dept, chain in payload["balance_chains"].items():
        inputs = _budget_inputs_for(tmp_path, dept)
        expected = domain_budget.consume(limits[dept], Decimal("0.8"), inputs)
        assert [c["invoice_id"] for c in chain] == [c.invoice_id for c in expected]
        assert [Decimal(c["balance_after"]) for c in chain] == [
            c.balance_after for c in expected
        ]
```

Reuse the existing helpers in `tests/test_gate_batch.py` for loading the generated payload and the manifest records rather than adding parallel ones.

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_gate_batch.py -k "declares_a_budget_chain or match_domain_budget" -v
```

Expected: FAIL with `KeyError: 'balance_chains'`.

- [ ] **Step 3: Implement**

In `scripts/generator/gate_batch.py`, replace the MKT-only block in `write_gate_batch`. Import the limits rather than restating them — `seed_reference`'s module docstring explicitly forbids a second copy, because two copies is how item 2.3's test starts passing for the wrong reason:

```python
from scripts.seed_reference import BUDGETS

...

    # PER-DEPARTMENT, not MKT-only. M4-D's gate raised 9 BUDGET_BREACH flags and
    # declared 1; the other 8 were correct behaviour in departments this file
    # never covered, so its "exact agreement" criterion measured one department
    # and claimed all of them (spec 2026-08-01-m4d §3's closing note).
    limits = {dept: limit for dept, _period, _category, limit in BUDGETS}
    chains: dict[str, list[dict[str, str]]] = {}
    for dept in sorted({r["department"] for r in chosen.values()}):
        members = sorted(
            (r for r in chosen.values() if r["department"] == dept),
            key=lambda r: (r["issue_date"], r["invoice_series"], r["invoice_number"]),
        )
        balance = limits[dept]
        chain = []
        for record in members:
            before = balance
            balance = before - Decimal(record["total_amount"])
            chain.append(
                {
                    "invoice_id": record["invoice_id"],
                    "balance_before": str(before),
                    "balance_after": str(balance),
                }
            )
        chains[dept] = chain
```

Emit `"balance_chains": chains` and keep `expected_breach_id` / `mkt_limit` so `test_declared_breach_matches_domain_budget` stays green. Drop `balance_after_chain`, and update its readers in `tests/test_gate_batch.py` to `payload["balance_chains"]["MKT"]`.

- [ ] **Step 4: Regenerate and verify**

```bash
make generate
.venv/bin/python -m pytest tests/test_gate_batch.py -v
```

Expected: all PASS.

- [ ] **Step 5: `make check` and commit**

```bash
make check
git add scripts/generator/gate_batch.py tests/test_gate_batch.py
git commit -m "feat(m5a): declare a budget chain per department, not MKT only

M4-D's gate raised 9 BUDGET_BREACH flags and gate_batch.json declared 1. The
other 8 were correct behaviour in departments the declaration never covered, so
'exact agreement between the declaration and what the batch flags' measured one
department and claimed all of them.

Limits are imported from seed_reference.BUDGETS rather than restated — a second
copy is how item 2.3's test starts passing for the wrong reason.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 10: Narrow the caller guard, run the gate, close the milestone

Spec §5.2 and §6. Nothing here is optional: M4-D was declared closed on a red commit whose gate did not execute in CI at all.

**Files:**
- Modify: `tests/test_schema_constraints.py`, `.github/workflows/ci.yml` (only if the gate does not already run)
- Modify: `docs/notes/2026-08-01-m5a-write-guard.md`, `docs/Plan_Project_F_2026-07-25.md`

- [ ] **Step 1: Narrow `test_no_production_caller_of_journal_repository`**

Its docstring says to delete it when M5.3 lands. Narrow it instead — deleting drops the invariant, the allowlist keeps it:

```python
def test_only_the_guard_may_call_the_journal_repository():
    """Was `test_no_production_caller_of_journal_repository`. M5.3 has landed,
    so the repository now HAS a production caller — exactly one. Its docstring
    said to delete this test; narrowing it to an allowlist keeps the invariant
    that made the original worth writing, at the cost of one line.

    `tools/guards.py` is the only door because it holds the checks: the
    approvals re-read, the open-flag predicate and the idempotency fork. A
    second caller bypasses all three.
    """
    import pathlib

    ALLOWED = {"journal.py", "guards.py"}
    src = pathlib.Path("src/invoice_agent")
    offenders = [
        p
        for p in src.rglob("*.py")
        if p.name not in ALLOWED
        and (
            "repositories.journal" in p.read_text(encoding="utf-8")
            or "repositories import journal" in p.read_text(encoding="utf-8")
        )
    ]
    assert not offenders, (
        f"journal repository must be reached through tools/guards.py: {offenders}"
    )
```

- [ ] **Step 2: Verify the guard test is falsifiable**

Temporarily add `from invoice_agent.db.repositories import journal` to `src/invoice_agent/graphs/enrichment.py`, run the test, confirm **RED**, then revert.

```bash
.venv/bin/python -m pytest tests/test_schema_constraints.py -k only_the_guard -v
```

- [ ] **Step 3: Run the gate batch, replaying, with every key unset**

```bash
env -u GROQ_API_KEYS -u GEMINI_API_KEYS -u OPENROUTER_API_KEYS \
  LLM_MODE=replay .venv/bin/python -m pytest tests/test_batch_graph.py tests/test_gate_batch.py tests/test_never_post_without_approval.py -v
```

Then record the numbers criterion 2 asks for — auto / posted / needs-approval / failed / persisted — and reconcile them against M4-D's **23 auto / 7 needs-approval**. Query the database directly for the counts rather than reading them off log lines:

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c \
  "SELECT status, count(*) FROM app.invoices GROUP BY status ORDER BY status"
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c \
  "SELECT severity, count(*) FROM app.invoice_flags WHERE resolved_at IS NULL GROUP BY severity"
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c \
  "SELECT posting_authority, count(*) FROM app.journal_entries GROUP BY posting_authority"
```

**The split will probably move**, because an invoice whose category is `NULL` now routes to a human. Report whatever it reads. A smaller auto count is a real finding about classification coverage — do not tune it away, and do not describe it as a regression.

- [ ] **Step 4: Confirm the gate actually runs in CI, and does not run vacuously**

`.github/workflows/ci.yml` **already** has `Seed reference data` and `Generate corpus` before the pytest step — M4-D's exit review added both after measuring that hiding `corpus/` made 6 tests SKIP silently, including two of the six portfolio tests. So nothing needs adding here. What needs checking is that M5-A's new tests do not join that skipping set:

```bash
gh run view --log | grep -E "skipped|SKIPPED"
```

Confirm neither `test_never_post_without_approval.py` nor the new `auto_post` cases appear. A test that skips in CI is a test CI is not running, and M4-D was closed on exactly that.

If any of the new tests skip, find the guard clause causing it (usually the `requires_postgres` fixture or a missing `corpus/`) and fix the cause — never the assertion.

- [ ] **Step 5: Finish the closeout note**

Complete `docs/notes/2026-08-01-m5a-write-guard.md`:

- the mutation table from Task 6, including the UNIQUE asymmetry
- the measured gate numbers from step 3, reconciled against M4-D's
- **the flag-count discontinuity, stated explicitly**: M4-D's flag numbers were in-memory counts against an empty table; M5-A's are rows. The two sets are not comparable, and saying so prevents them reading as contradictory
- the plain statement from spec §4.4 that a mis-classified clean invoice books to the wrong account with nobody looking
- spec §8's four open questions, with anything M5-A answered marked as such

- [ ] **Step 6: Update the roadmap**

In `docs/Plan_Project_F_2026-07-25.md`: mark 5.3 done in the M5 table, add an M5-A sub-milestone line in the style of M4's, set the §7 status row for M5 to in-progress, and move the per-department declaration out of §1's owed list.

- [ ] **Step 7: `make check`, push, and confirm CI on the pushed SHA**

```bash
make check
git add -A
git commit -m "docs(m5a): close M5-A on measured numbers

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
git push
gh run watch
```

**M5-A is not closed until `gh run watch` reports green on this exact SHA.** Five milestones have now failed this rule. If CI is red, fix forward and push again before declaring anything.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §2 trigger narrowing | 1 |
| §4.1 rules 1–2, autonomous audit | 2 |
| §4.1 rule 3 | 3 |
| §4.1 rule 5 (`status='posted'`) | 2 (in the guard's success path) |
| §4.2 idempotency | 4 |
| §4.3 flags / category / status | 7 |
| §4.4 `auto_post` | 8 |
| §4.5 per-department declaration | 9 |
| §5 cases 1–8 | 2, 3, 4 |
| §5 cases 9–11 | 5 |
| §5.1 mutations | 6 |
| §5.2 narrowed caller test | 10 |
| §6 criteria 1–7 | 6 (1), 10 (2–7) |

No gaps.

**Type consistency:** `create_journal_entry` keeps one signature across Tasks 2, 3, 4 and 8. `accounting.JournalLine` is the parameter type throughout; the conversion to `journal.JournalLine` happens once, inside the guard. `BLOCKING_SEVERITIES` is defined in Task 3 and consumed in Task 7. `EnrichedInvoice["classification"]` is added in Task 7 and read in Task 8.

**Known interactions to watch during execution:**

- Task 7 changes what `serial_join` writes, and `test_budget_serialization.py` asserts a byte-identical balance chain across 10 shuffles. Task 7 step 6 runs it explicitly for this reason.
- Task 8 changes the graph topology, so M3-C's committed-diagram drift guard goes red until step 6 regenerates it.
- Task 9 changes the shape of `gate_batch.json`; existing readers of `balance_after_chain` must move to `balance_chains["MKT"]`.
