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

### Asymmetry Note on Mutation 5

Dropping `UNIQUE(invoice_id)` leaves `test_posting_the_same_invoice_twice_yields_one_entry` **GREEN**, because `tools/guards.py` re-reads Postgres for an existing entry before attempting an insert. `test_unique_blocks_a_second_entry_with_the_guard_bypassed` directly calls `repositories.journal` (bypassing the guard) and turns **RED** when `UNIQUE(invoice_id)` is dropped. Both layers are necessary.

---

## Gate Batch Evaluation Numbers (30 Invoices)

Measured 2026-08-01 under `LLM_MODE=replay` with `GROQ_API_KEYS`, `GEMINI_API_KEYS`, and `OPENROUTER_API_KEYS` all unset:

- **Total Inputs**: 30
- **Persisted**: 27 (28 XML files attempted; 1 exact duplicate `INV-0070` returned `None` from `insert_with_lines`)
- **Failed**: 2 (`INV-0122`, `INV-0123` — deliberate unreadable PDF members)
- **Auto (Routed to Auto-Post)**: 18
- **Posted**: 18 (`posting_authority='auto_clean'`, 0 open `warn`/`critical` flags, valid seeded category)
- **Needs Approval**: 12 (2 PDFs + 1 duplicate + 9 flagged or unmappable invoices)

### Reconciliation against M4-D

- **M4-D split**: 23 auto / 7 needs_approval (measured in-memory before database persistence was added).
- **M5-A split**: 18 posted / 12 needs_approval.
- **Why the split moved**: In M5-A, invoices with `NULL` category (e.g. out-of-seed categories or unclassified invoices) or open flags in database tables now route to `needs_approval`. An unmappable category has no seeded TT133 account to book against, so routing to a human is the correct behavior specified in §0 constraint 4.

### Flag-Count Discontinuity Note

M4-D's flag numbers were in-memory counts against an empty table; M5-A's flag numbers are real rows persisted in `app.invoice_flags` during `serial_join`. The two sets are not comparable across milestone boundaries, and stating so prevents reading the difference as a contradiction.

---

## Risk Statement (Spec §4.4)

A mis-classified clean invoice books to account 6421 (Management Expenses) instead of 6422 (Selling Expenses) with nobody looking. Account codes come deterministically from seed data based on the category chosen by the `ClassificationAgent` (95.4% accurate, M4-C). Zero open `warn`/`critical` flags is enforced by the database layer. This is an accuracy cost, not a breach of the safety property — the LLM never toggles a flag and never decides that posting is permitted.

---

## Open Questions (Spec §8) Status

1. **Unparsed file handling**: Handled by routing to `needs_approval` / `failed` (resolved).
2. **`resolve_flag` caller**: Targeted for M5-B (reviewer queue).
3. **`edited` decision details**: Targeted for M5-B (reviewer queue).
4. **`invoices.status` state machine**: Tracked for M5-C (state machine transitions).
