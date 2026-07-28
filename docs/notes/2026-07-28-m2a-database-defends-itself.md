# Milestone 2-A Verification: The Database Defends Itself

**Date:** 2026-07-28  
**Milestone:** M2-A (Schema, Seed & Repositories)  
**Status:** COMPLETED & VERIFIED  

---

## Executive Summary

Milestone 2-A establishes the database foundation for the Vietnamese Invoice Processing Agent. The database schema does not merely store data; it enforces business rules and safety invariants at the DDL layer so that application logic errors, retry duplicate runs, or agent misbehaviors are blocked by PostgreSQL constraints.

- **15 Tables Created** across **9 Sequential Alembic Revisions**.
- **Role Segregation Enforced**: `invoice` (owner/superuser) vs `invoice_app` (runtime role, `NOSUPERUSER`).
- **All 11 Load-Bearing Mutation Guards Verified RED on constraint removal, GREEN on restoration**.
- **Schema Isolation Verified**: Alembic autogenerate completely ignores non-`app` tables (e.g. LangGraph checkpoints).

---

## 1. Alembic Migration History (9 Revisions)

| Revision | Name | Purpose | Primary Invariants / Grants Enforced |
| :--- | :--- | :--- | :--- |
| `ed4293988a59` | `m2a_01_runtime_role` | Runtime role creation | Creates `invoice_app` (NOSUPERUSER), grants `USAGE` on schema `app`. |
| `4cbc1ec07560` | `m2a_02_reference` | Reference tables & TT133 | `departments`, `expense_categories`, `budgets`. Nulls-not-distinct constraint `uq_budgets_department_period_category`. |
| `9e318cfe38bb` | `m2a_03_vendors_batches` | Vendors & Batches | `vendors` (MST shape regex CHECKs: `\d{10}` / `\d{10}-\d{3}` / `050000000000`), `batches` lifecycle. |
| `7579bc07df86` | `m2a_04_invoices` | Invoices & Line Items | `invoices` (`seller_tax_code` NOT NULL, natural key `uq_invoices_natural_key`, `INVOICE_STATUSES`, `SOURCE_TYPES`), `invoice_line_items` (unconstrained `sa.Numeric`). |
| `e16a3ef22e5c` | `m2a_05_flags` | Invoice Flags | `invoice_flags` (`ck_flags_raised_by` deterministic source CHECK). Enforces Principle 1 in DDL. |
| `73a7e77bede6` | `m2a_06_approvals` | Approvals | `approvals` (`latency_seconds` computed column `sa.Computed("EXTRACT(EPOCH FROM (decided_at - requested_at))", persisted=True)`). |
| `2cde91bba492` | `m2a_07_journal` | Ledger & Accounting | `journal_entries` (balance CHECK `total_debit = total_credit`, `posting_authority` CHECK, `invoice_id` UNIQUE, trigger `trg_forbid_auto_clean_with_open_flags` blocking auto-posting on open flags), `journal_entry_lines` (one side zero CHECK `debit=0 OR credit=0`, non-negative CHECK `debit>=0 AND credit>=0`). |
| `be6e5173bcf4` | `m2a_08_audit_log` | Append-Only Audit Log | `audit_log` append-only via REVOKE `UPDATE, DELETE` on `invoice_app` AND `trg_audit_log_append_only` trigger raising `'audit_log is append-only'`. |
| `b25d8a939562` | `m2a_09_consumption_episodic_llm` | Budget, Memory & Telemetry | `budget_consumption` (`uq_budget_consumption_invoice`), `episodic_decisions` (SQL-first indexing on `vendor_tax_code`, `category`), `llm_calls` (captures success & failure cost/tokens). |

---

## 2. Least-Privilege Role Segregation

- **`invoice` (Owner / DDL Role)**: Owns schema `app`, runs Alembic migrations, runs `scripts/seed_reference.py`.
- **`invoice_app` (Runtime Application Role)**:
  - `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT`.
  - Holds `SELECT, INSERT, UPDATE` on standard business tables.
  - Holds `SELECT, INSERT` ONLY on `app.audit_log` (`UPDATE` and `DELETE` explicitly REVOKED).
  - Explicit sequence grants (`GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO invoice_app`) included in every table migration.

---

## 3. All 11 Mutation Guards Verified

| # | Guard / Constraint Name | Invariant Tested | Mutation Result |
| :---: | :--- | :--- | :---: |
| 1 | `invoice_app` permission probe | `invoice_app` cannot bypass schema or sequence permissions | **RED** without sequence grants |
| 2 | `uq_budgets_department_period_category` | Prevents 2 department-wide budgets (`category IS NULL`) | **RED** on constraint removal |
| 3 | `ck_vendors_tax_code_format` | Tax code format validation (`\d{10}`, `\d{10}-\d{3}`, `050000000000`) | **RED** on constraint removal |
| 4 | `invoices.seller_tax_code` `NOT NULL` | Seller tax code mandatory; routes missing MST to `manual_queue` | **RED** on `NOT NULL` removal |
| 5 | Unconstrained `sa.Numeric` | Decimal precision preserved to 8 decimal places (`21618409.09000000`) | **RED** on `Numeric(18,2)` |
| 6 | `ck_flags_raised_by` | Deterministic flag sources; no agent/LLM can raise flags | **RED** on constraint removal |
| 7 | `ck_journal_balanced` | Journal entries must balance (`total_debit = total_credit`) | **RED** on constraint removal |
| 8 | `ck_journal_human_approved_needs_approval` | `posting_authority='human_approved'` requires `approval_id` | **RED** on constraint removal |
| 9 | `trg_forbid_auto_clean_with_open_flags` | `auto_clean` forbidden when invoice carries an open flag | **RED** on trigger removal |
| 10 | `ck_journal_line_one_side` & `ck_journal_line_non_negative` | Journal line debit/credit single-sided & non-negative | **RED** on constraint removal |
| 11 | `trg_audit_log_append_only` & REVOKE | `audit_log` append-only even for superuser / owner sessions | **RED** on trigger / privilege drop |

---

## 4. Verification Evidence

- `make check` suite: **159 passed in 5.97s** (Ruff, Mypy, Pytest all green).
- Clean volume test (`make db-reset && make seed && make check`): Verified idempotency and clean environment execution.
- Alembic autogenerate isolation (`tests/test_alembic_ignores_langgraph.py`): Verified zero phantom DDL ops.
