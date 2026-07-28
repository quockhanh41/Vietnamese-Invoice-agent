# Milestone 2-A Verification: The Database Defends Itself

**Date:** 2026-07-28 · **Milestone:** M2-A (Schema, Seed & Repositories)
**Status:** schema and guards **verified**; one gate condition **NOT met** — see §5.

> **Revision note (2026-07-28, review).** The first version of this document claimed a
> `ck_vendors_tax_code_format` CHECK constraint that **does not exist in the schema**, and listed it
> as a mutation guard "verified RED". It also claimed Alembic autogenerate was clean, which is false.
> Both are corrected below. The lesson is the milestone's own: a verification document that describes
> constraints rather than measuring them is exactly the "invented number" this project exists to
> avoid. **Every claim below was re-measured on 2026-07-28 against the live database.**

---

## Executive Summary

The `app` schema does not merely store data. It enforces business rules and safety invariants in DDL,
so that application bugs, retried runs, and agent misbehaviour are refused by Postgres rather than by
convention.

- **15 tables** across **9 sequential Alembic revisions** (17 relations in `app`, counting
  `spike_counter` from M1.5 and `alembic_version`).
- **Role segregation**: `invoice` (owner, SUPERUSER) vs `invoice_app` (runtime, NOSUPERUSER).
- **All 11 mutation guards re-broken from scratch and confirmed RED**, then restored.
- ⚠️ **Alembic autogenerate is NOT clean** — it emits ~30 phantom foreign-key operations. This is an
  open defect, documented in §5, not a passing condition.

---

## 1. Alembic migration history (9 revisions)

| Revision | Name | Enforces |
| :--- | :--- | :--- |
| `ed4293988a59` | `m2a_01_runtime_role` | Creates `invoice_app` (NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOBYPASSRLS); `USAGE` on schema `app` |
| `4cbc1ec07560` | `m2a_02_reference` | `departments`, `expense_categories`, `budgets`; `uq_budgets_department_period_category` **UNIQUE NULLS NOT DISTINCT** |
| `9e318cfe38bb` | `m2a_03_vendors_batches` | `vendors` (`tax_code TEXT`, **no format CHECK** — see below), `batches` with `ck_batches_status` |
| `7579bc07df86` | `m2a_04_invoices` | `invoices`: `seller_tax_code NOT NULL`, `uq_invoices_natural_key`, `ck_invoices_status` (11 values), `ck_invoices_source_type`; `invoice_line_items` with unconstrained `sa.Numeric` |
| `e16a3ef22e5c` | `m2a_05_flags` | `invoice_flags` with `ck_flags_raised_by` — Principle 1 in DDL |
| `73a7e77bede6` | `m2a_06_approvals` | `approvals` with `latency_seconds` GENERATED column |
| `2cde91bba492` | `m2a_07_journal` | `ck_journal_balanced`, `ck_journal_human_approved_needs_approval`, `invoice_id` UNIQUE, `trg_forbid_auto_clean_with_open_flags`; lines: `ck_journal_line_one_side`, `ck_journal_line_non_negative` |
| `be6e5173bcf4` | `m2a_08_audit_log` | `audit_log` append-only: `REVOKE UPDATE, DELETE` from `invoice_app` **and** `trg_audit_log_append_only` |
| `b25d8a939562` | `m2a_09_consumption_episodic_llm` | `budget_consumption` (`uq_budget_consumption_invoice` on `(budget_id, invoice_id)`), `episodic_decisions`, `llm_calls` |

**`vendors.tax_code` deliberately carries no format CHECK.** Three real shapes were measured on the
eight fixtures — `0100000003` (10 digits), `0300000018-042` (branch, dashed), `050000000000`
(12-digit household business). The 12-digit household MST is derived from CCCD and **has no known
public checksum rule** (spec open question #2). A validator that rejected it would flag real,
valid, tax-authority-signed invoices — the precise false-positive the headline metric measures.

---

## 2. Least-privilege role segregation

- **`invoice`** — owner. Runs Alembic and `scripts/seed_reference.py`. It is a **SUPERUSER**, which is
  exactly why the trigger layer in §3 exists.
- **`invoice_app`** — runtime. Measured attributes: `rolsuper=false`, `rolcreatedb=false`,
  `rolcreaterole=false`, `rolbypassrls=false`, `rolinherit=**true**` (the role does inherit; an
  earlier version of this note wrongly claimed `NOINHERIT`).
  - `SELECT, INSERT, UPDATE` on business tables.
  - `SELECT, INSERT` **only** on `app.audit_log`. Measured ACL: `invoice_app=ar/invoice` — `a` =
    INSERT, `r` = SELECT. No `w`, no `d`.
  - `GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app` — required, not decorative: without it,
    every `BIGSERIAL` insert fails with `permission denied for sequence`.

---

## 3. All 11 mutation guards — re-measured 2026-07-28

Each constraint was dropped from the live database, the named test run, then the constraint restored.
A guard that stays green with its constraint removed guards nothing; M1 shipped four such tests, so
these were re-broken from scratch rather than trusted.

| # | Mutation applied | Guard | Result |
| :---: | :--- | :--- | :---: |
| 1 | Drop `ck_journal_balanced` | `test_unbalanced_journal_entry_rejected` | **RED** |
| 2 | Drop `ck_journal_human_approved_needs_approval` | `test_human_approved_requires_an_approval_row` | **RED** |
| 3 | Drop `ck_journal_line_one_side` | `test_line_cannot_be_both_debit_and_credit` | **RED** |
| 4 | Drop `ck_journal_line_non_negative` | `test_negative_amounts_rejected` | **RED** |
| 5 | Drop `ck_flags_raised_by` | `test_flag_source_must_be_deterministic` | **RED** |
| 6 | Drop `uq_invoices_natural_key` | `test_duplicate_invoice_is_idempotent` | **RED** |
| 7 | `seller_tax_code` → nullable | `test_invoice_requires_seller_tax_code` | **RED** |
| 8 | `UNIQUE NULLS NOT DISTINCT` → plain `UNIQUE` on `budgets` | `test_two_department_wide_budgets_rejected` | **RED** |
| 9 | Drop `trg_forbid_auto_clean_with_open_flags` | `test_auto_clean_blocked_while_flags_open` | **RED** |
| 10 | Drop `trg_audit_log_append_only` | `test_audit_log_immutable_even_as_owner` | **RED** |
| 11 | `GRANT UPDATE ON audit_log TO invoice_app` | `test_audit_log_update_denied_to_runtime_role` | **RED** |

Guards 10 and 11 are deliberately separate: each `audit_log` mechanism is verified **with the other
still in place**, so neither can be credited for the other's work. That matters because the privilege
layer alone is not sufficient — measured, `REVOKE UPDATE` lands in the ACL
(`{invoice=arDxtm/invoice}`, `w` and `d` gone) and the SUPERUSER owner's `UPDATE` succeeds anyway.
The trigger is what makes append-only true for every session, including every `psql` window opened
during a demo.

---

## 4. LangGraph schema isolation

`tests/test_alembic_ignores_langgraph.py` (M1.6, ADR 0002) is **restored to its original form** after
a rewrite during M2-A removed its `saver.setup()` precondition. Re-verified 2026-07-28 against two
separate mutations of `migrations/env.py`:

| Mutation to `include_object` | Result |
| :--- | :---: |
| Delete **only** the `langgraph` exclusion, leave the rest intact | **RED** |
| Replace the whole body with `return True` | **RED** |

The `saver.setup()` call is load-bearing and must not be removed again: it guarantees the four
checkpoint tables exist. Measured — with it removed, the langgraph-only mutation **passes** on a
database whose `langgraph` schema is empty. That is the CI state exactly: `init_db.sql` creates empty
schemas, `alembic upgrade head` runs, then pytest — and this file sorts **first** alphabetically,
before anything else calls `saver.setup()`.

---

## 5. ⚠️ Open defect — Alembic autogenerate is not clean

Success criterion 2 of the M2-A spec requires `alembic revision --autogenerate` to produce an
**empty** migration. It does not. Measured 2026-07-28:

```
op.drop_constraint(op.f('approvals_invoice_id_fkey'), 'approvals', type_='foreignkey')
op.create_foreign_key(None, 'approvals', 'invoices', ['invoice_id'], ['id'],
                      source_schema='app', referent_schema='app')
...  (~30 such pairs, one per foreign key in the schema)
```

**The database itself is correct** — `pg_get_constraintdef` confirms
`FOREIGN KEY (batch_id) REFERENCES app.batches(id)` and so on. This is a comparison artefact:
`migrations/env.py` connects with `options=-csearch_path=app,public`, so `app` is the connection's
default schema and reflection reports `referred_schema=None`, while the metadata says `'app'`.
Alembic sees a difference that does not exist and proposes to rewrite every FK.

Two consequences, both real:

1. Every future `--autogenerate` starts with ~30 spurious operations that a developer must delete by
   hand — and the generated `op.drop_constraint` calls omit `schema='app'`, so running them
   unedited would fail.
2. Genuine drift is now hidden in the noise, which is the opposite of what the M1.6 guard is for.

This was not caught during implementation because Task 10 Step 1 — the explicit "confirm the probe
migration's `upgrade()` body is `pass`" check — was not run. **Fix is not attempted in this note.**

---

## 6. Verification evidence

| Claim | Status |
| :--- | :--- |
| `make check` — ruff, mypy, pytest | ✅ **159 passed**, ruff clean, mypy clean |
| 11 mutation guards RED, then restored | ✅ re-measured 2026-07-28 (§3) |
| LangGraph isolation guard catches both mutations | ✅ re-measured 2026-07-28 (§4) |
| Unbalanced journal entry refused by CHECK *(M2 gate)* | ✅ guard #1 |
| `UPDATE audit_log` refused — privilege **and** trigger *(M2 gate)* | ✅ guards #10, #11 |
| Autogenerate emits an empty migration | ❌ **fails — see §5** |
| `make db-reset && make seed && make check` from an empty volume | ⚠️ **not independently verified**; all measurements above were taken against an incrementally migrated database |
