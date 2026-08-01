# Spec — M5-A: the write guard and the auto path (plan item 5.3, plus three persistence gaps)

> **Date**: 2026-08-01 · **Milestone**: M5, first of four specs
> **Reads with**: [`Plan_Project_F_2026-07-25.md`](../../Plan_Project_F_2026-07-25.md) §3 M5 · §8,
> [`project-f-system-design.md`](../../project-f-system-design.md) §2.7, §2.8, §4, §5.3
> **Preceded by**: M4-D batch graph + serial reconciliation
> **Followed by**: M5-B approval graph + HITL loop · M5-C FastAPI · M5-D Streamlit

---

## 0. Global constraints

At the top because three milestones running lost their rationales to
summarisation while keeping their procedures. Every one of these can be
satisfied by code that passes all tests while proving nothing.

1. **The three layers must encode ONE rule, and each must bite alone.** Code
   (`tools/guards.py`), database (CHECK + trigger + UNIQUE) and test
   (`test_never_post_without_approval.py`). A layer that agrees with the others
   only because it is never exercised is decoration — §5's mutation table is how
   this is measured, not asserted.
2. **The guard re-reads Postgres. It never trusts the state it was handed.**
   Design §5.3 step 2. Graph state is forgeable through a time-travel fork, so
   `decision == "approved"` in state is an input, not evidence.
3. **`info` severity is context for a human, never a block.** M4-D measured that
   routing on the mere existence of a finding sent 28 of 28 invoices to a
   reviewer, because `NEW_VENDOR` fires on every vendor's first sighting and
   M4-B counted 51 of those across the corpus. All three layers adopt this rule
   in M5-A — including the database, which currently does not (§2).
4. **A category the model invented is never written.** `invoices.category` is a
   FK to `expense_categories.code`. An unmappable category becomes `NULL`, and
   `NULL` routes to a human. No fabricated fallback — the same rule M4-D applied
   when it deleted the invented `Decimal("50000000")` budget limit.
5. **The serial join stays ONE transaction.** M4-D constraint 2. Gap-closing
   writes extend that node rather than adding a second commit point.
6. **The gate replays with every API key unset.** M4-D was declared closed on a
   gate that was making live calls while claiming to replay, and on tests CI
   never ran because no step generated the corpus. Both are pre-conditions here.
7. **A milestone is not closed until `make check` is green on the PUSHED commit
   and CI agrees.** Four milestones have now failed this. Run `make check` after
   each task, not once at the end — M4-C's seven-commit batch push meant CI first
   saw the tip, and the tip was a docs commit that read as harmless.

---

## 1. Why this exists

M5-A encodes the safety property this project's README leads with: **the LLM
never posts an accounting entry.** Until now that has been true by absence —
`repositories/journal.py` carries a comment saying "Do not add a production
caller before M5.3", and `test_no_production_caller_of_journal_repository`
asserts nobody has. M5-A adds the first caller, and therefore must add the guard
in the same change.

### 1.1 Three gaps found while planning, all of which break the auto path

Measured 2026-08-01 by reading the code, not by running it:

| Gap | Evidence | Consequence |
|---|---|---|
| `invoice_flags` receives only near-duplicate and budget flags | `batch.py:214` and `batch.py:303` are the only callers of `raise_flag` outside tests | Validation findings, signals and `CLASSIFICATION_DISPUTED` exist only in graph state and are discarded when the batch ends. The `forbid_auto_clean_with_open_flags` trigger — the *database* layer of the three-layer property — fires on an almost-empty table |
| `invoices.category` and `invoices.department` are never written | `invoices.insert_with_lines` (`invoices.py:58-81`) omits both | The classification result is discarded. `auto_post` has no category from which to pick TT133 accounts |
| `invoices.status` never leaves `'extracted'` | `set_status` has zero callers in `src/` or `scripts/` | Nothing downstream — the reviewer queue, the API, the report — can tell a posted invoice from an unexamined one |

None is optional. The first is the load-bearing one: without it the database
layer of the safety property guards nothing, and the milestone's headline claim
would be false while every test passed.

### 1.2 The conflict that shapes the whole spec

Closing gap 1 collides with the trigger as written:

```sql
IF NEW.posting_authority = 'auto_clean' AND EXISTS (
    SELECT 1 FROM app.invoice_flags
    WHERE invoice_id = NEW.invoice_id AND resolved_at IS NULL
) THEN RAISE EXCEPTION ...
```

**Any** open flag blocks `auto_clean`. `NEW_VENDOR` is severity `info`
(`CODE_REGISTRY`, `findings.py:60`) and M4-B measured 51 first sightings across
the corpus — on a fresh database that is nearly every invoice. Persist findings
honestly and the auto path becomes unreachable, which is exactly the 28-of-28
failure `route_outcomes_node`'s docstring already measured and fixed **in memory
only**.

So the code layer and the database layer currently encode **different rules for
the same decision**: `route_outcomes` requires `warn`/`critical`, the trigger
requires nothing more than existence. Today the disagreement is invisible
because the table is empty. Fill it, and one of them is wrong.

This is resolved in §2, and the resolution is the one place M5-A relaxes a
database constraint — so it carries its own mutation test.

---

## 2. Decision: narrow the trigger to `warn`/`critical`

Migration 13 replaces the trigger function body with a severity predicate:

```sql
WHERE invoice_id = NEW.invoice_id
  AND resolved_at IS NULL
  AND severity IN ('warn', 'critical')
```

The migration carries the justification inline, per CLAUDE.md's rule about
non-obvious constants: date 2026-08-01, M4-D's 28-of-28 measurement, and the
report file `docs/notes/2026-08-01-m4d-batch-graph.md`.

**Alternative considered and rejected**: persist only `warn`/`critical` findings
and leave the trigger untouched. It keeps the constraint maximally strict, but
throws `NEW_VENDOR` and `ITEM_KIND_MISSING` away entirely — and M5-B's reviewer
queue wants precisely that context on screen. Discarding data to avoid changing
a predicate is the wrong trade.

**Why this is not a weakening.** The trigger's purpose is to be *independent* of
the code path, not maximally restrictive. A layer that disagrees with the rule
the system actually implements does not add safety; it adds a bug that fires in
production. After the change all three layers say the same sentence — *an
invoice with an unresolved `warn` or `critical` flag cannot post without a
human* — and §5 proves each says it alone.

---

## 3. Scope

### 3.1 In

- `src/invoice_agent/tools/guards.py` — `create_journal_entry`, `GuardDenied`
- Migration 13 — trigger narrowed to `warn`/`critical`
- `serial_join` in `graphs/batch.py` — persist all findings and signals to
  `invoice_flags`; persist `category`/`department`; set `invoices.status`
- `EnrichedInvoice` gains `classification`; `enrich_one` propagates it
- `invoices.insert_with_lines` gains `category=` / `department=` keyword args,
  validated against the seed before write
- `auto_post` node in `graphs/batch.py`, wired `route_outcomes → auto_post → finalize`
- `tests/test_never_post_without_approval.py` — portfolio test 2
- `corpus/gate_batch.json` + `scripts/generator/gate_batch.py` — per-department
  budget-chain declarations, replacing the MKT-only declaration
- `test_no_production_caller_of_journal_repository` narrowed to an allowlist

### 3.2 Out

- `graphs/approval.py`, `open_approval`, `recover_pending.py` — **M5-B**
- FastAPI, SSE — **M5-C** · Streamlit — **M5-D**
- Item 4.4's episodic tuning — deferred past M5-B for M4-B's own reason: no real
  episodes exist yet, so tuning would measure hand-seeded fixtures
- The throttled Auditor re-measurement, the PII git-history purge, CI-platform
  vision fixtures — untouched by M5, deferred with the reason recorded
- `max_concurrency` against a real rate limit — **M5-B**, where the batch first
  runs live
- Refactoring the two identical `JournalLine` dataclasses in
  `domain/accounting.py` and `db/repositories/journal.py`. Noted, not fixed:
  the guard converts between them. Fixing it is unrelated to this request

---

## 4. Components

### 4.1 `tools/guards.py` — the code layer

```python
def create_journal_entry(
    conn, *, invoice_id: uuid.UUID, posting_authority: str,
    entry_date: date, lines: list[JournalLine], actor: str,
    approval_id: uuid.UUID | None = None,
) -> uuid.UUID
```

Checks run in order. Every denial writes `audit_log(allowed=False)` **before**
raising `GuardDenied`, so a refusal is never distinguishable from "the writer was
never called" (the reason `db/audit.py` logs grants as well as refusals).

**The audit write uses its own connection and commits independently.** Found
while decomposing this spec into tasks: the guard runs inside the caller's
transaction, so a denial that raises would have its own audit row rolled back
with the transaction it aborted — design §5.3's "every refusal writes
`audit_log`" would be silently false, and the test asserting the row exists
would pass only because it never committed either. A log must not be rolled back
by the thing it is logging. `audit_log` is append-only and has no FK to
`invoices`, so an autonomous write is safe; the cost is that audit rows survive
a rolled-back test, which is why every assertion on them filters by the
invoice's UUID rather than counting rows in the table.

| # | Rule | Why |
|---|---|---|
| 1 | `posting_authority ∈ ('auto_clean','human_approved')` | Mirrors `ck_journal_authority`; a typo must not reach the database as a silent third mode |
| 2 | `human_approved` → `approval_id` required; **re-read `approvals` via `conn`**; require the row exists, its `invoice_id` matches the argument, and `decision ∈ ('approved','edited')` | Design §5.3 step 2. The `invoice_id` match is what catches an approval belonging to a different invoice |
| 3 | `auto_clean` → `approval_id` must be `None`; re-read `invoice_flags`; deny on any open `warn`/`critical` row | Same predicate as the narrowed trigger. The code layer must never be *more permissive* than the layer beneath it |
| 4 | Idempotency — see §4.2 | |
| 5 | On success: `audit_log(allowed=True)`, then `invoices.status = 'posted'` | Gap 3 closed at the only point where "posted" becomes true |

**`edited` posts.** `APPROVAL_DECISIONS` allows `('approved','rejected','edited')`
while design §5.1 routes only approved/rejected. A reviewer who corrected a field
and let the invoice through has approved it. Deciding it here means M5-B inherits
an answer instead of a guess.

### 4.2 Idempotency, and why it is a guard rule rather than graph shape

Roadmap §1 records the measurement: `kill -9` **inside** a post node, after its
write and before its checkpoint commits, then resume from a new process → the
node runs again → **two journal entries**. Node placement only stops the repeat
caused by `interrupt()`. What stops this is the `UNIQUE(invoice_id)` constraint
plus this guard.

Before inserting, the guard calls `journal.get_for_invoice`:

- **Entry exists, same `total_debit` and same `posting_authority`** → return the
  existing id; `audit_log(allowed=True, reason="idempotent replay")`. A resume
  after a crash completes instead of dying.
- **Entry exists, different totals or authority** → deny. Returning silently here
  would hide a genuine divergence, which is worse than a crash.
- **`IntegrityError` on `UNIQUE(invoice_id)`** — the race where two processes
  both pass the existence check — is caught, the row re-read, and the same fork
  taken. The constraint is the arbiter; the guard just interprets it.

### 4.3 Closing the persistence gaps in `serial_join`

M4-D constraint 2 says the batch has exactly one commit point. These writes
extend that node rather than adding a second.

**Flags.** After `db_id` is known, persist every `Finding` in `e["findings"]` and
`e["signals"]` through `flags.raise_flag`. The existing near-duplicate and budget
calls are unchanged. `severity` and `raised_by` already come from `CODE_REGISTRY`
via `findings.make`, and all seven sources it can produce — `validation`,
`taxcode`, `anomaly`, `budget`, `duplicate`, `vendor_memory`,
`agent_disagreement` — are already in `FLAG_SOURCES`, so no CHECK needs widening.

**Category and department.** `EnrichedInvoice` gains `classification`;
`enrich_one` propagates it from the enrichment result.
`invoices.insert_with_lines` gains `category=` / `department=`. Both columns are
FKs (`expense_categories.code`, `departments.code`), so a value the model
invented cannot be written at all. `insert_with_lines` looks the code up in
`expense_categories` / `departments` before writing and passes `NULL` when it is
absent, which routes the invoice to a human per §4.4 — the lookup exists so the
batch gets a routable invoice rather than an `IntegrityError`. **The FK is a
fourth deterministic layer** and the note should say so: even with that lookup
deleted, the database still refuses an invented category without any application
code deciding to.

**Status.** `set_status` gains its first callers: `extracted` → `flagged` when a
`warn`/`critical` flag persisted, else `validated`. `posted` is written by the
guard (§4.1 rule 5). `pending_approval` belongs to M5-B.

### 4.4 `auto_post` in `graphs/batch.py`

Design §4 specifies `route_outcomes ─cond→ { auto_post, open_approval, both }`.
M5-A builds the first arm only, so the wiring is
`route_outcomes → auto_post → finalize`.

For each id in `state["auto"]`:

```
db_id from `persisted`
  → invoice.category → seeded expense_categories row → CategoryAccounts
  → accounting.build_journal_entry(invoice, accounts)
  → guards.create_journal_entry(..., posting_authority="auto_clean", actor="system")
```

Category `NULL`, no seeded accounts for it, or a `GuardDenied` → the id **moves
from `auto` to `needs_approval`**, and the batch continues. `auto` and
`needs_approval` are plain `BatchState` keys with no reducer, so a node may
rewrite them.

**Each invoice posts in its own transaction.** A single bad entry must not roll
back every other entry in the batch — the same argument M4-D's `try/except` per
task makes for the fan-out, applied to the write path.

**What auto_post is allowed to book, stated because it is the milestone's one
real risk.** The account codes come from the invoice's category, which the
ClassificationAgent chose (95.4% accurate, M4-C). The LLM proposes; the
category→account map is deterministic seed data; zero open `warn`/`critical`
flags is enforced by the database. A mis-classified *clean* invoice therefore
books to 6421 instead of 6422 with nobody looking. That is an accuracy cost, not
a breach of the safety property — the LLM still never toggles a flag and never
decides that posting is permitted. The note must say this plainly rather than
let the 95.4% imply it never happens.

### 4.5 Per-department gate-batch declaration

`corpus/gate_batch.json` pins the MKT chain only. M4-D's gate raised **9**
`BUDGET_BREACH` flags; 8 were correct and undeclared, so "exact agreement"
overclaimed. `scripts/generator/gate_batch.py` is extended to declare the budget
chain for **every** department the 30-invoice batch touches.

The **auto/posted/flagged split stays reported, not declared**: the corpus's
finding rate is a property of the corpus, and M4-D already made that argument.
The per-department budget chains are declarable because they are deterministic
arithmetic over a fixed input.

---

## 5. `test_never_post_without_approval.py` — portfolio test 2

**Guard-layer cases** — the guard is called normally and must refuse:

1. Flagged invoice (`warn`), no `approvals` row, `auto_clean` → denied
2. `approvals` row with `decision IS NULL`, `human_approved` → denied
3. `approvals` row with `decision='rejected'` → denied
4. `approvals` row `decision='approved'` **belonging to a different invoice** → denied
5. Post twice, identical → **one row**; second logged as idempotent replay; `audit_log` holds two rows
6. Post twice, second with different totals → denied
7. Clean invoice, mappable category → posts with `posting_authority='auto_clean'`, `approval_id IS NULL`, `invoices.status='posted'`
8. Clean invoice, `NULL` category → not posted, moved to `needs_approval`

**Database-layer cases** — the guard is *bypassed* and `repositories.journal` is
called directly, so each database constraint is exercised on its own. Without
these the DB mutations in §5.1 cannot go red, because an intact guard refuses
first and the constraint beneath it is never reached:

9. Bypass, `auto_clean` on an invoice with an open `warn` flag → rejected by `trg_forbid_auto_clean_with_open_flags`
10. Bypass, `human_approved` with `approval_id = NULL` → rejected by `ck_journal_human_approved_needs_approval`
11. Bypass, insert twice for the same invoice → second rejected by `UNIQUE(invoice_id)`

Every denial case in 1–6 additionally asserts an `audit_log` row exists with
`allowed=false` — a guard that refuses silently leaves the reviewer unable to
tell refusal from absence.

### 5.1 Mutations — each proves a different layer bites alone

| Mutation applied to source | Layer | Turns red |
|---|---|---|
| Delete the `approvals` re-read in `create_journal_entry` | code | 2, 3, 4 |
| Narrow the guard's `auto_clean` check to `critical` only | code | 1 |
| Drop `trg_forbid_auto_clean_with_open_flags` | database | 9 |
| Drop `ck_journal_human_approved_needs_approval` | database | 10 |
| Drop `UNIQUE(invoice_id)` on `journal_entries` | database | 11 |

Note what the pairing forces: dropping `UNIQUE` does **not** turn case 5 red,
because the guard's idempotency read (§4.2) returns the existing entry before any
insert is attempted. Case 5 measures the guard; case 11 measures the constraint.
Claiming one covers the other is exactly the shape of error M1's exit review
found four times.

Five for five, re-broken from scratch and re-run — not "verified earlier". M1's
exit review found four separate tests that passed while guarding nothing, and
M2/M3/M4 each found more.

### 5.2 A test that changes rather than disappears

`test_no_production_caller_of_journal_repository` says in its own docstring to
delete it when M5.3 lands. It is **narrowed instead**: only
`src/invoice_agent/tools/guards.py` may import the journal repository. Deleting
it would drop the invariant entirely; the allowlist form keeps it and costs one
line.

---

## 6. Success criteria

| # | Criterion | Evidence |
|---|---|---|
| 1 | `test_never_post_without_approval.py` green, 5/5 mutations red | Mutation table in the note, run fresh |
| 2 | Gate batch replayed with `GROQ`/`GEMINI`/`OPENROUTER` unset; auto/posted/flagged split **reported** and reconciled against M4-D's 23 auto / 7 needs-approval | Note |
| 3 | Every finding and signal produced by the **persisted** gate invoices is present in `invoice_flags` — M4-D persisted 27 of 30, with 2 in `failed` and 1 an exact duplicate | Row count vs the manifest |
| 4 | All 9 `BUDGET_BREACH` flags declared in `gate_batch.json`, not 1 of 9 | Diff of the declaration |
| 5 | Posting the same invoice twice yields exactly one `journal_entries` row, through the guard (case 5) **and** through the constraint with the guard bypassed (case 11) | Both cases |
| 6 | `invoices.category` / `department` / `status` populated for all persisted gate invoices; an unmappable category lands as `NULL`, never invented | Query in the note |
| 7 | `make check` green on the **pushed** commit, CI agreeing, and the gate test actually executing in CI | CI run id |

Criterion 2 will likely **change** the split, because an invoice whose category
is `NULL` now routes to a human. Whatever it reads, it is reported as measured —
a smaller auto count is a real finding about classification coverage, not a
regression to hide.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Narrowing the trigger is a genuine relaxation of a safety constraint | §5.1 mutation 3 proves it still rejects a `warn`-flagged invoice with the code guard removed. The migration carries date + measurement + report filename |
| The auto path could become unreachable anyway if classification coverage is low | Criterion 2 measures it. If the auto count collapses, that is M5-A's headline number and gets reported, not tuned away |
| Persisting all findings changes flag counts M4 reported | M4's numbers were in-memory counts; the note must state that the table was empty before and say so explicitly rather than let the two sets of numbers look contradictory |
| `auto_post` per-invoice transactions could leave a batch half-posted | Intended. `journal_entries` is the ledger; a partial batch is recoverable and visible, whereas rolling back every good entry in the batch for one bad one is not |
| The guard becomes a second place where posting rules live, alongside `route_outcomes` | Accepted and named: `route_outcomes` decides *routing*, the guard decides *permission*. Both use the same `warn`/`critical` predicate, and mutation 2 catches them drifting apart |

---

## 8. Open questions — recorded, not silently dropped

1. **Should `resolve_flag` ever be called by the auto path?** No caller exists.
   A reviewer resolving a flag in M5-B could make a previously-blocked invoice
   auto-postable on a re-run. Left undecided here because M5-A adds no reviewer;
   M5-B must answer it.
2. **Does `edited` require `field_corrections` to be non-empty?** The guard
   treats `edited` as approved regardless. If M5-B writes `edited` with no
   corrections, that is a UI bug the guard will not catch.
3. **`invoices.status` has no state machine.** `set_status` accepts any value the
   CHECK allows, so nothing prevents `posted → validated`. Out of scope; worth a
   transition guard if the API in M5-C starts writing statuses.
4. **Two `JournalLine` dataclasses.** Noted in §3.2, deliberately not fixed.
