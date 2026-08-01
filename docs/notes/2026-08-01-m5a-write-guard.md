# M5-A: Write Guard & Auto-Post Closeout Note

**Date:** 2026-08-01

## Mutation results

Verified 2026-08-01: five mutations applied to code and database constraints, each targeting a specific case in `tests/test_never_post_without_approval.py`.

| # | Target | Mutation | Test Case | Measured Result |
|---|---|---|---|---|
| 1 | `tools/guards.py` | Delete `human_approved` re-read | `test_an_undecided_approval_cannot_post`, `test_a_rejected_approval_cannot_post`, `test_an_approval_for_a_different_invoice_cannot_post` | **RED** (3 failed) |
| 2 | `tools/guards.py` | Narrow `BLOCKING_SEVERITIES` to `("critical",)` | `test_auto_clean_is_denied_when_a_warn_flag_is_open` | **RED** (1 failed with DB exception) |
| 3 | `app.journal_entries` | `DROP TRIGGER trg_forbid_auto_clean_with_open_flags` | `test_the_trigger_blocks_auto_clean_with_the_guard_bypassed` | **RED** (1 failed: DID NOT RAISE) |
| 4 | `app.journal_entries` | `DROP CONSTRAINT ck_journal_human_approved_needs_approval` | `test_the_check_blocks_human_approved_without_an_approval` | **RED** (1 failed: DID NOT RAISE) |
| 5 | `app.journal_entries` | `DROP CONSTRAINT journal_entries_invoice_id_key` | `test_unique_blocks_a_second_entry_with_the_guard_bypassed` vs `test_posting_the_same_invoice_twice_yields_one_entry` | **ASYMMETRY**: `test_unique_blocks_a_second_entry...` **RED** (1 failed), `test_posting_the_same_invoice_twice...` **GREEN** (1 passed) |

**Mutation 6, added by the review** — the routing fix needs its own falsification, or the change
would rest on one measurement and no guard:

| # | Target | Mutation | Test Case | Measured Result |
|---|---|---|---|---|
| 6 | `graphs/batch.py` | `_route` reads in-memory `findings`/`signals` again instead of `invoice_flags` | `test_routing_sees_budget_flags_the_fan_out_could_not_have_returned` | **RED** (1 failed) |

Mutation 2 was also re-run after the routing change, because `route_outcomes` now shares
`guards.BLOCKING_SEVERITIES`. Its blast radius grew from 1 test to 2
(`test_auto_clean_is_denied_when_a_warn_flag_is_open` **and**
`test_a_guard_denial_moves_the_invoice_to_needs_approval`) — which is the intended consequence of the
two layers reading one constant instead of two copies that can drift.

### Asymmetry Note on Mutation 5

Dropping `UNIQUE(invoice_id)` leaves `test_posting_the_same_invoice_twice_yields_one_entry` **GREEN**, because `tools/guards.py` re-reads Postgres for an existing entry before attempting an insert. `test_unique_blocks_a_second_entry_with_the_guard_bypassed` directly calls `repositories.journal` (bypassing the guard) and turns **RED** when `UNIQUE(invoice_id)` is dropped. Both layers are necessary.

---

## Gate Batch Evaluation Numbers (30 Invoices)

Measured 2026-08-01 under `LLM_MODE=replay` with `GROQ_API_KEYS`, `GEMINI_API_KEYS` and
`OPENROUTER_API_KEYS` all unset, **from a truncated database** — see the reproducibility note below,
which is why that clause is load-bearing rather than decorative. Two consecutive clean runs give
byte-identical numbers.

- **Total inputs**: 30
- **Failed**: 2 (`INV-0122`, `INV-0123` — the deliberate unreadable-PDF members)
- **Enriched**: 28
- **Persisted**: 27 (1 exact duplicate, `INV-0070`, returned `None` from `insert_with_lines`)
- **Routed to auto**: 15
- **Posted**: 15 — `posting_authority='auto_clean'`, no open `warn`/`critical` flag, seeded category
- **Needs approval**: 15 (2 failed PDFs + 1 duplicate + 12 carrying a blocking flag)
- **Invoice status after the run**: `posted` 15, `flagged` 12
- **`audit_log` rows for this batch's invoices**: **15 grants, 0 denials**

### Reproducibility — and why the first measurement was wrong

The first cut of this note reported **18 posted / 12 needs_approval**. That number could not be
reproduced. Re-measured from a truncated database it is **15/15**; re-run a second time *without*
truncating, against the invoices the first run had just committed, it is **0 posted / 30
needs_approval**, because every invoice is then a duplicate.

The cause was a test-isolation leak, not a measurement slip. `tests/test_batch_graph.py` called
`conn.commit()` on the `owner_conn` fixture, defeating the `trans.rollback()` its teardown depends
on and emitting `SAWarning: transaction already deassociated from connection`. Measured: running
that one file against a truncated database left **3 invoices, 1 journal entry and 1 vendor with
non-zero accumulators** committed. Polluted `(count, total, sum_sq)` changes `PRICE_OUTLIER` — a
`warn` flag — which changes routing, which changes this number.

`4f870bc` fixed this exact leak in this exact file once already. The tests now open their own
connections and a `committed_rows` fixture removes every row they commit, in FK order.

**Any future run of this gate must state that the database was reset.** A number measured against
leftover state is not a measurement of the batch.

### Reconciliation against M4-D

- **M4-D**: 23 auto / 7 needs_approval — computed **in memory**, before anything was persisted, and
  before any invoice could be refused a post.
- **M5-A**: 15 posted / 15 needs_approval.
- **Why it moved**: routing now reads `invoice_flags` instead of a fan-out task's own result, so the
  12 invoices carrying a blocking flag are identified before the auto path rather than after it; the
  unpersisted duplicate is routed by `route_outcomes` rather than bounced by `auto_post`; and an
  invoice whose category the seed does not know has no account to book against and goes to a human
  (§0 constraint 4). The two splits are not the same measurement and should not be read as a
  regression.

### The routing defect this milestone's review found

Measured before the fix: `route_outcomes` sent **23** invoices to the auto path and the guard refused
**8 of them, every one for `BUDGET_BREACH`**:

```
INV-0080  0088  0089  0124  0125  0137  0138  0140
```

`route_outcomes` read the in-memory `findings`/`signals` a fan-out task returned. Budget findings are
computed in `serial_join`, **after** the fan-out, so no task result can ever carry one — the routing
node was structurally blind to the entire budget family. The guard re-reads Postgres, so the system
stayed safe; but every batch wrote 8 `audit_log` denial rows, which read as eight safety refusals
when they were one routing bug.

`route_outcomes_node` now reads `invoice_flags` with the same `BLOCKING_SEVERITIES` predicate the
guard and the trigger use — roadmap principle 5 (*do not trust graph state, trust the database*)
applied to routing. After the fix: **0 invoices routed to auto and then refused**, and the
`audit_log` for this batch holds 15 grants and no denials. Pinned by
`test_routing_sees_budget_flags_the_fan_out_could_not_have_returned`, which raises a flag that exists
only in the table and asserts routing honours it.

### Flag-Count Discontinuity Note

M4-D's flag numbers were in-memory counts against an empty table; M5-A's flag numbers are real rows persisted in `app.invoice_flags` during `serial_join`. The two sets are not comparable across milestone boundaries, and stating so prevents reading the difference as a contradiction.

---

## Risk Statement (Spec §4.4)

A mis-classified clean invoice books to account **6422** (*chi phí quản lý doanh nghiệp* —
administrative expense) instead of **6421** (*chi phí bán hàng* — selling expense), with nobody
looking. Account codes come deterministically from seed data, keyed on the category the
`ClassificationAgent` chose (95.4% accurate, M4-C). Zero open `warn`/`critical` flags is enforced by
the database layer. This is an accuracy cost, not a breach of the safety property — the LLM never
toggles a flag and never decides that posting is permitted.

At the measured 95.4% classification accuracy, roughly **one posted invoice in twenty-two carries the
wrong expense account**, and nothing in M5-A detects it. That is the honest form of this risk, and
M6's eval owes a per-category breakdown rather than a single accuracy figure.

> The first cut of this paragraph had the two accounts swapped — it glossed 6421 as "Management
> Expenses" and 6422 as "Selling Expenses". `scripts/seed_reference.py:32` is the source of truth:
> `6421` = *chi phí bán hàng*, `6422` = *chi phí quản lý doanh nghiệp*. Recorded rather than quietly
> corrected, because a portfolio artefact that mislabels TT133 accounts is read by accountants.

---

## Open Questions (Spec §8) Status

Numbered as the spec numbers them. The first cut of this section opened with an invented question
("unparsed file handling — resolved") that appears nowhere in §8, shifted the three real ones down a
slot, and **dropped the fourth entirely**. The roadmap records this exact failure at M3-B —
*"invented open questions replacing two safety-relevant real ones"* — so it is corrected here rather
than silently rewritten.

1. **Should `resolve_flag` ever be called by the auto path?** Still open. No caller exists. A
   reviewer resolving a flag in M5-B could make a previously-blocked invoice auto-postable on a
   re-run, and after this milestone that matters more than it did: `route_outcomes` now reads the
   same table, so resolving a flag changes routing as well as permission. **M5-B must answer it.**
2. **Does `edited` require `field_corrections` to be non-empty?** Still open. The guard treats
   `edited` as approved regardless; if M5-B writes `edited` with no corrections, that is a UI bug the
   guard will not catch.
3. **`invoices.status` has no state machine.** Still open, and now partly exercised: `set_status`
   gained real callers in this milestone (`extracted` → `validated`/`flagged` → `posted`) but nothing
   prevents `posted → validated`. Worth a transition guard if M5-C's API starts writing statuses.
4. **Two `JournalLine` dataclasses** (`domain/accounting.py` and `db/repositories/journal.py`).
   Still open, still deliberately unfixed — the guard converts between them at one point (spec §3.2).

### Answered by this milestone

- **Is the auto path reachable once flags are persisted?** Yes — 15 of 30, measured above. This was
  the open risk behind narrowing the trigger, and it is now a number rather than an argument.
