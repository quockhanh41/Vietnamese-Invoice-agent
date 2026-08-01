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
