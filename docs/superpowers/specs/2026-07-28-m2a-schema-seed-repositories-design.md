# Spec — M2-A: schema, seed, repositories (plan items 2.4, 2.5, 2.7)

> **Date**: 2026-07-28 · **Milestone**: M2 (opened 2026-07-28 when the M1 gate closed 4/4)
> **Covers**: plan items **2.4** (migrations, constraints, trigger, REVOKE), **2.5** (seed reference
> data), **2.7** (repositories + `audit_log` writer).
> **Does not cover**: 2.6 (`domain/` pure core → spec M2-B) and 2.1–2.3 (corpus generator → spec
> M2-C). See §1.2 for why M2 is three specs and not one.

---

## 1. Why this, and why it comes first

### 1.1 What it owes

M2's gate has five conditions. Three of them are this spec's:

- `make generate && make seed` runs clean — the `seed` half.
- `INSERT` an unbalanced journal entry → **rejected by a CHECK constraint**.
- `UPDATE audit_log` → **rejected**.

The remaining two (≥60 `domain/` unit tests; parser round-trip 300/300 synthetic) belong to M2-B and
M2-C. M2's artefact — *"a psql screenshot showing the database defending itself"* — is produced
entirely here.

### 1.2 Why M2 is three specs

M2 as written in the plan is three near-independent subsystems. 2.4+2.5+2.7 is fifteen tables plus
constraints, seed and repositories; 2.6 alone is six pure modules behind a ≥60-test gate; 2.1–2.3 is
a Jinja generator with fourteen defect groups. Each is comparable in size to all of M1.3+M1.4, which
had its own spec and plan.

There is also a hard dependency: the corpus's order-dependent budget-overrun case (2.3) has to know
the seeded budget limits, so the corpus cannot be finalised before this spec ships. `domain/` (2.6)
depends on neither and is anchored on the eight real fixtures.

Order: **M2-A → M2-B → M2-C**.

### 1.3 Why the schema comes before the domain core

`domain/` is pure and could be written first. The schema goes first because the *safety property* is
this milestone's headline, and it is the only part of M2 that later milestones cannot proceed
without: M3 writes invoices, M4 writes `budget_consumption`, M5 writes `approvals` and
`journal_entries`. A domain function with no table to run against is a unit test; a table with no
domain function is still a working system of record.

---

## 2. Measurements taken 2026-07-28 for this spec

Every one of these changed the design. None was assumed.

### 2.1 `REVOKE` alone cannot make `audit_log` append-only here

Design doc §2.8 says `REVOKE UPDATE, DELETE ON audit_log FROM app_user`. There is no `app_user`.
`docker-compose.yml` sets `POSTGRES_USER: invoice`, and the Docker entrypoint creates that role as a
**SUPERUSER** — confirmed:

```
SELECT rolname, rolsuper FROM pg_roles WHERE rolname NOT LIKE 'pg\_%';
 invoice | t
```

Measured on a probe table:

| Setup | `UPDATE` |
|---|---|
| `REVOKE UPDATE, DELETE FROM invoice`, then UPDATE as `invoice` | **`UPDATE 1` — succeeds** |
| Same, as a separate NOSUPERUSER role with only `SELECT, INSERT` | `ERROR: permission denied for table` |
| `BEFORE UPDATE OR DELETE` trigger that `RAISE`s, as SUPERUSER `invoice` | `ERROR: audit_log is append-only` |

The REVOKE *is* applied — the ACL drops `w` and `d`, landing on `{invoice=arDxtm/invoice}`. It simply
does not matter, because superusers bypass privilege checks entirely.

This is the M1 failure mode wearing different clothes. M1 produced four tests that passed while
guarding nothing; this would have been a *guard* that guarded nothing, shipped under a README
sentence claiming append-only storage.

**Decision — two independent mechanisms, matching the project's own three-layer thesis:**

1. A NOSUPERUSER role `invoice_app` with `SELECT, INSERT` on `audit_log` and `UPDATE, DELETE`
   revoked. This is the privilege layer, and the runtime connects as this role.
2. A `BEFORE UPDATE OR DELETE` trigger that raises. This holds for **every** caller including the
   owner and any superuser — i.e. including every `psql` session opened during a demo.

### 2.2 A NULL in the natural key silently admits duplicate invoices

Design doc §2.3 calls `UNIQUE (seller_tax_code, template_no, invoice_series, invoice_number)` the
identity of a Vietnamese e-invoice, and M1 typed `InvoiceData.seller_tax_code` as `str | None`.
Postgres treats NULLs as distinct, so:

```
CREATE TABLE zz_u(seller text, series text, num text, UNIQUE (seller, series, num));
INSERT INTO zz_u VALUES (NULL,'C22TAA','00001234');   -- INSERT 0 1
INSERT INTO zz_u VALUES (NULL,'C22TAA','00001234');   -- INSERT 0 1
SELECT count(*) -> 2
```

The same invoice, twice, past the constraint the design doc calls the most important one. The 3%
"trùng lặp hoàn toàn" defect group would be undetectable for any invoice missing a seller MST.

`UNIQUE NULLS NOT DISTINCT` (PG15+) fixes it, and `ON CONFLICT DO NOTHING ... RETURNING` still works
against it (returns 0 rows, no crash) — so §2.3's idempotent re-upload survives.

**But it is the wrong fix here.** Measured across all eight fixtures:

| File | `NBan/MST` | `NMua/MST` | lines missing `STT` |
|---|---|---|---|
| dienluc_gtgt_cccd_2.1.0 | `0400100009-006` | `None` | 0 / 1 |
| hokinhdoanh_banhang_qrcode_2.1.0 | `050000000000` | `None` | 0 / 2 |
| ktx_banhang_cccd_2.1.0 | `0300000018` | `None` | 0 / 1 |
| lienket_gtgt_7dong_2.1.0 | `0102000009-006` | `''` | 0 / 7 |
| newimage_gtgt_pban2.0.1 | `0311110005` | `''` | 0 / 1 |
| phongvu_gtgt_tdiep_2.0.0 | `0300000018-042` | `None` | 0 / 1 |
| viettelpost_gtgt_2.1.0 | `0100000003` | `''` | 0 / 1 |
| vnpt_hoadon_banhang_2.1.0 | `0300000018` | `None` | 0 / 1 |

**Seller MST is present 8/8. Buyer MST is never a real value (8/8 `None` or `''`). STT is present on
all 15 lines.**

> **Provenance of the values above.** Every MST in that table was read out of
> `tests/fixtures/real_samples/` on 2026-07-28 — i.e. they are the **anonymised** codes, the ones
> `c97da4e` put there. They are not the pre-anonymisation values. This note exists because the same
> table in `Plan_Project_F_2026-07-25.md` §M1.2 carried the *real* codes until the M1 exit review
> caught it: PII leaks into prose about the data as readily as into the data, and gitleaks looks for
> tokens, not tax codes. Any future edit that adds a measured value here must say where it came from.

So `seller_tax_code` is `NOT NULL` and the natural key has no nullable component at all. That is
strictly better than `NULLS NOT DISTINCT`, which would make two *different* unknown-seller invoices
with the same series+number collide — and under `ON CONFLICT DO NOTHING` the second would silently
not be stored. Losing an invoice is worse than flagging one.

An invoice arriving without a seller MST cannot be given an identity, so it routes to
`manual_queue` rather than being stored identity-less. `InvoiceData.seller_tax_code` stays
`str | None` — the parser is right to report what it saw; the boundary that refuses is ingestion.

`UNIQUE NULLS NOT DISTINCT` is still used, once, where a NULL is genuinely meaningful:
`budgets(department_code, period, category)`, where `category IS NULL` means whole-department budget
and two such rows must not coexist.

### 2.3 Native `ENUM` costs a second migration whenever a value is added

Design doc writes `ENUM(...)` five times. On PG17:

| | native `ENUM` | `TEXT` + `CHECK` |
|---|---|---|
| `ADD VALUE` inside a migration transaction | works (the pre-PG12 restriction is gone) | works |
| Add a value **and backfill rows to it** in one migration | `ERROR: unsafe use of new value "void"` → whole migration rolls back | works |
| Remove or rename a value | recreate type, rewrite dependent columns | one-line constraint swap |
| Alembic autogenerate detects value changes | no | no |

Measured:

```
BEGIN;
ALTER TYPE app.zz_status ADD VALUE 'void';
UPDATE app.zz_t SET s = 'void';
-- ERROR: unsafe use of new value "void" of enum type app.zz_status
-- HINT:  New enum values must be committed before they can be used.
```

`invoices.status` is the column design doc §2.3 left literally as `ENUM(...)`, never enumerated. It
will gain values through M3–M5, plausibly with backfills. **Decision: `TEXT` + named `CHECK` for all
five columns.** It also keeps one mechanism on screen in the gate screenshot — §2.7's balance and
posting-authority guards are already CHECK constraints.

### 2.4 The `latency_seconds` generated column works as designed

```
latency_seconds numeric GENERATED ALWAYS AS
  (EXTRACT(EPOCH FROM (decided_at - requested_at))) STORED
```

Inserted a row 90 seconds apart → `latency = 90`. Design doc §2.8 calls this metric "free"; verified
that it is.

### 2.5 Money cannot be `NUMERIC(18,2)`

M1 measured amounts including `21618409.09000000` (eight decimal places) and a unit price of
`398181.33` where `3 × 398181.33 = 1194543.99` but `ThTien = 1194544`. The measured validation rule
`sum(ThTien | TChat==1, fallback TTKhac[Amount]) + TgTThue == TgTTTBSo` holds exactly on 8/8 real
files, and an exact comparison is only exact if storage did not round.

**Decision: unconstrained `NUMERIC` for every monetary and quantity column.** Postgres `NUMERIC`
without precision is arbitrary-precision, so nothing is silently lost between parse and comparison.

---

## 3. Scope

**In:**

```
migrations/versions/<rev>_m2_core_schema.py   15 tables, constraints, triggers, role + grants
src/invoice_agent/db/models.py                extended (spike_counter stays — M1.5 depends on it)
src/invoice_agent/db/repositories/            11 modules covering all 15 tables
src/invoice_agent/db/audit.py                 audit_log writer
scripts/seed_reference.py                     item 2.5
src/invoice_agent/config.py                   + runtime_dsn (the invoice_app role)
Makefile                                      + `make seed`
tests/test_schema_constraints.py              the gate evidence, mutation-verified
tests/test_repositories.py
tests/test_seed_reference.py
```

**Out:** `domain/` (M2-B) · generator (M2-C) · `tools/guards.py` (M5.3) · pgvector column (M7) ·
vision/PDF anything (M3).

---

## 4. Schema — 15 tables

`spike_counter` (M1.5) stays: `test_hitl_survives_restart.py` counts rows in it, and that test is a
closed M1 gate condition.

### 4.1 Reference — seeded by item 2.5

**`departments`** — `code TEXT PK`, `name_vi TEXT NOT NULL`.

**`expense_categories`** — `code TEXT PK`, `name_vi TEXT NOT NULL`, `debit_account TEXT NOT NULL`,
`vat_account TEXT NULL`, `credit_account TEXT NOT NULL`, `keywords_vi TEXT[] NOT NULL DEFAULT '{}'`.

`keywords_vi` is not decoration — it is the non-LLM baseline every classification metric must be
reported against (principle 3; design doc §10.4). It stays Vietnamese: it is data the system
ingests, not authoring style (CLAUDE.md language rule).

**`budgets`** — `id BIGSERIAL PK`, `department_code TEXT NOT NULL FK`, `period TEXT NOT NULL`
(`YYYY-MM`), `category TEXT NULL FK expense_categories`, `amount_limit NUMERIC NOT NULL CHECK > 0`,
`warn_threshold_pct NUMERIC NOT NULL DEFAULT 0.800 CHECK (> 0 AND <= 1)`,
`UNIQUE NULLS NOT DISTINCT (department_code, period, category)` — see §2.2 for why `NULLS NOT
DISTINCT` is right *here* and wrong on `invoices`.

### 4.2 Core

**`batches`** — referenced by design doc §2.3 as `batch_id FK` but never defined. Minimal:
`id UUID PK`, `label TEXT`, `status TEXT NOT NULL CHECK IN (running, completed, failed)`,
`invoice_count INT NOT NULL DEFAULT 0`, `created_at`, `completed_at NULL`.

**`invoices`** — design doc §2.3, with §2.2's and §2.5's corrections applied.

```
id UUID PK, batch_id UUID NULL FK batches,
template_no TEXT NOT NULL, invoice_series TEXT NOT NULL, invoice_number TEXT NOT NULL,
seller_tax_code TEXT NOT NULL FK vendors,      -- NOT NULL: measured 8/8, see §2.2
buyer_tax_code TEXT NULL,                      -- never a real value in 8/8, in no key
issue_date DATE NOT NULL, currency TEXT, exchange_rate NUMERIC,
subtotal NUMERIC, vat_amount NUMERIC, total_amount NUMERIC NOT NULL, total_in_words TEXT,
ext JSONB NOT NULL DEFAULT '{}',               -- header TTKhac bags, six scopes
source_type TEXT NOT NULL CHECK IN (xml, pdf_vision, manual),
source_path TEXT, raw_xml TEXT,
extraction_confidence NUMERIC, extraction_attempts INT NOT NULL DEFAULT 0,
category TEXT NULL FK expense_categories, department TEXT NULL FK departments,
classification_confidence NUMERIC,
status TEXT NOT NULL CHECK IN (...11 values, §4.6),
cost_usd NUMERIC, shadow_cost_usd NUMERIC, tokens_in INT, tokens_out INT,
langfuse_trace_id TEXT, created_at, updated_at

UNIQUE (seller_tax_code, template_no, invoice_series, invoice_number)
```

**`invoice_line_items`** — `id BIGSERIAL PK`, `invoice_id UUID FK CASCADE`, `line_no INT NOT NULL`,
`description TEXT`, `unit TEXT`, `quantity NUMERIC`, `unit_price NUMERIC`, `amount NUMERIC`,
`vat_rate NUMERIC`, `vat_amount NUMERIC`, `item_kind INT NULL`, `ext JSONB NOT NULL DEFAULT '{}'`,
`UNIQUE (invoice_id, line_no)`.

`line_no` is `NOT NULL` while `InvoiceLine.line_no` is `int | None`: STT is present on 15/15 lines
(§2.2), and where it is absent the repository substitutes 1-based document position. One column, not
two — a line index is an ordinal, not a fact about the invoice.

`ext` is stored, not dropped. It already proved load-bearing once: VNPT sets `ThTien = 0` and puts
the real amount in `ext['Amount']`, and the measured validation rule depends on that fallback.

`description` is the **prompt-injection surface** (design doc §2.4, §9.1). Marked here so the column
carries the warning, not just the Pydantic model.

**`invoice_flags`** — `id BIGSERIAL PK`, `invoice_id UUID FK CASCADE`, `code TEXT NOT NULL`,
`severity TEXT NOT NULL CHECK IN (info, warn, critical)`, `detail JSONB NOT NULL DEFAULT '{}'`,
`raised_by TEXT NOT NULL CHECK IN (validation, taxcode, anomaly, budget, duplicate, vendor_memory)`,
`resolved_at TIMESTAMPTZ NULL`, `created_at`. Partial index on `(invoice_id) WHERE resolved_at IS
NULL` — the open-flags trigger reads it on every journal-entry insert.

The `raised_by` CHECK is an addition beyond the design doc, and it is deliberate. Principle 1 says no
LLM ever raises a flag. Today that is a convention enforced by code review. As a CHECK listing only
deterministic producers, adding an agent as a flag source requires a **migration** — it cannot happen
by accident in a node.

**`budget_consumption`** — design doc §2.6, unchanged: `id`, `budget_id FK`, `invoice_id FK`,
`amount NUMERIC NOT NULL`, `balance_before NUMERIC NOT NULL`, `balance_after NUMERIC NOT NULL`,
`sequence_no BIGINT NOT NULL`, `created_at`, `UNIQUE (budget_id, invoice_id)`.

### 4.3 Memory

**`vendors`** — design doc §2.2 verbatim: `tax_code TEXT PK`, `name`, `normalized_name`,
`first_seen_at`, `last_seen_at`, `invoice_count INT NOT NULL DEFAULT 0`,
`total_amount NUMERIC NOT NULL DEFAULT 0`, `avg_amount`, `stddev_amount`,
`category_histogram JSONB NOT NULL DEFAULT '{}'`, `modal_category`, `modal_department`,
`rejection_count INT NOT NULL DEFAULT 0`, `is_trusted BOOLEAN NOT NULL DEFAULT false`,
`reviewer_notes TEXT`.

`tax_code` is `TEXT` with no length constraint: three real shapes, including a dash
(`0300000018-042`) and a 12-digit household form. A `CHAR(10)`-shaped assumption rejects two of
three.

Because `invoices.seller_tax_code` is a `NOT NULL` FK here, ingestion upserts a **stub** vendor row
(tax_code, name, first_seen_at) on first sight. Statistics stay at their defaults until M4.3 fills
them transactionally — design doc §2.2 is explicit that they are updated at post/approve, not
recomputed in the hot path.

**`episodic_decisions`** — `id BIGSERIAL PK`, `approval_id UUID FK`, `invoice_id UUID FK`,
`vendor_tax_code TEXT`, `category TEXT`, `department TEXT`, `amount NUMERIC`,
`flags JSONB NOT NULL DEFAULT '{}'`, `decision TEXT NOT NULL`, `reason TEXT`, `created_at`.

**No `reason_embedding vector(1024)` column.** pgvector is "M7 only if there is time" (items 4.4,
7.6). A column present in the database but absent from SQLAlchemy metadata makes `--autogenerate`
emit a DROP — the exact M1.6 trap — so including it would mean adding the `pgvector` Python package
now and carrying it unused for five milestones. M7 adds one nullable column.

### 4.4 Safety — design doc §2.7

**`journal_entries`**

```
id UUID PK, invoice_id UUID NOT NULL UNIQUE FK invoices,   -- cannot post twice for one invoice
entry_date DATE NOT NULL,
status TEXT NOT NULL CHECK IN (draft, posted, void),
posting_authority TEXT NOT NULL CHECK IN (auto_clean, human_approved),
approval_id UUID NULL FK approvals,
posted_at TIMESTAMPTZ, posted_by TEXT,
total_debit NUMERIC NOT NULL, total_credit NUMERIC NOT NULL, created_at

CHECK (total_debit = total_credit)
CHECK (posting_authority <> 'human_approved' OR approval_id IS NOT NULL)
TRIGGER: posting_authority = 'auto_clean' AND the invoice has any invoice_flags
         WHERE resolved_at IS NULL  ->  RAISE
```

**`journal_entry_lines`** — `id BIGSERIAL PK`, `journal_entry_id UUID FK CASCADE`,
`line_no INT NOT NULL`, `account_code TEXT NOT NULL`, `account_name TEXT NOT NULL`,
`debit NUMERIC NOT NULL DEFAULT 0`, `credit NUMERIC NOT NULL DEFAULT 0`, `description TEXT`,
`UNIQUE (journal_entry_id, line_no)`, `CHECK (debit = 0 OR credit = 0)`,
`CHECK (debit >= 0 AND credit >= 0)`.

The second CHECK is beyond the design doc: without it a line of `debit = -100, credit = 0` satisfies
`debit = 0 OR credit = 0` and lets a "balanced" entry be assembled from negative amounts.

**`approvals`** — design doc §2.8, with the verified generated column:

```
id UUID PK, invoice_id UUID NOT NULL FK, thread_id TEXT NOT NULL,
interrupt_payload JSONB NOT NULL DEFAULT '{}', flags_snapshot JSONB NOT NULL DEFAULT '{}',
suggestions JSONB NOT NULL DEFAULT '{}',
decision TEXT NULL CHECK (decision IS NULL OR decision IN (approved, rejected, edited)),
field_corrections JSONB, reason TEXT, decided_by TEXT,
requested_at TIMESTAMPTZ NOT NULL DEFAULT now(), decided_at TIMESTAMPTZ NULL,
latency_seconds NUMERIC GENERATED ALWAYS AS
    (EXTRACT(EPOCH FROM (decided_at - requested_at))) STORED
```

Index on `thread_id`, plus a partial index `WHERE decision IS NULL` — that is exactly the query
`scripts/recover_pending.py` runs at M5.5.

**`audit_log`** — columns are unspecified in the design doc; proposed:

```
id BIGSERIAL PK, occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
actor TEXT NOT NULL,            -- 'system', 'human:<name>', 'mcp:<client>' (M7.2)
action TEXT NOT NULL, target_table TEXT, target_id TEXT,
allowed BOOLEAN NOT NULL,       -- refusals AND grants; a refusal-only log cannot be audited
reason TEXT, detail JSONB NOT NULL DEFAULT '{}'
```

Append-only by two mechanisms (§2.1): `invoice_app` holds only `SELECT, INSERT`, and a
`BEFORE UPDATE OR DELETE` trigger raises for every caller including superusers.

### 4.5 Ops

**`llm_calls`** — design doc §2.9 verbatim. `succeeded BOOLEAN NOT NULL` and `attempt INT NOT NULL
DEFAULT 1` exist because §2.9 is explicit that failed calls must be recorded: a 429 still burned
input tokens, and omitting it makes the cost number wrong. `shadow_cost_usd` is what makes "$0 on
Groq free tier" readable as *cheap* rather than as *unmeasured*.

### 4.6 `invoices.status` values

```
received, extracted, validated, classified, flagged,
pending_approval, approved, rejected, posted, manual_queue, failed
```

`manual_queue` is a status, not a table — design doc §3.2 lists it as a graph endpoint, and a
one-column state beats a table that duplicates `invoices`.

This list is a **prediction** about what M3–M5 produce, not a measurement. TEXT+CHECK (§2.3) is
precisely what makes being wrong about it cheap.

---

## 5. Role, grants, and config

`invoice_app` is created in the **migration**, not `scripts/init_db.sql`. `init_db.sql` runs only on
first initialisation of a fresh volume, so putting it there would leave every existing dev volume
without the role until a `make db-reset`, and would make the role invisible to anyone reading the
migration history. Role creation is idempotent via a `DO $$ ... IF NOT EXISTS ... $$` block.

```
GRANT USAGE ON SCHEMA app TO invoice_app;
GRANT SELECT, INSERT, UPDATE ON <all business tables> TO invoice_app;
GRANT SELECT, INSERT ON app.audit_log TO invoice_app;
REVOKE UPDATE, DELETE ON app.audit_log FROM invoice_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO invoice_app;
```

`config.py` gains `runtime_database_url` (default
`postgresql://invoice_app:invoice_app@localhost:5433/invoice_agent`) and a `runtime_dsn` property
that reuses the existing `_with_options` helper — the same helper whose missing URL-encoding was the
M0.2 defect that killed `PostgresSaver` while leaving Alembic working. Reusing it means the fix
covers the new DSN too.

Division of labour: **Alembic and `scripts/seed_reference.py` connect as the owner** (`invoice`) —
both are administrative. **Repositories connect as `invoice_app`.** Tests asserting permission
denial connect as `invoice_app`; tests asserting the trigger connect as `invoice`.

Also updated: `.env.example` and the CI workflow, which must export the runtime DSN.
`docker-compose.yml` and `scripts/init_db.sql` are deliberately **not** touched — the role lives in
the migration, so a clean CI runner gets it from `alembic upgrade head` with no extra step.

---

## 6. Repositories — all 15 tables

Eleven modules under `src/invoice_agent/db/repositories/`, covering every table. SQLAlchemy Core
against the **same `Table` objects** `db/models.py` gives Alembic, so a renamed column breaks the
query at import time rather than at runtime — the single-source-of-truth rule that `xml_schema.py`
already follows for XML paths.

| Module | Tables | Key operations |
|---|---|---|
| `reference.py` | departments, expense_categories, budgets | read; upsert (used by seed) |
| `vendors.py` | vendors | `ensure_vendor` (stub); `update_profile` (transactional, M4.3 fills) |
| `batches.py` | batches | create, complete, count |
| `invoices.py` | invoices, invoice_line_items | `insert_with_lines` (idempotent), `get_by_natural_key`, `set_status` |
| `flags.py` | invoice_flags | `raise_flag`, `open_flags_for`, `resolve` |
| `budget_consumption.py` | budget_consumption | `record` with `balance_before/after/sequence_no` |
| `journal.py` | journal_entries, journal_entry_lines | `create_entry_with_lines`, `get_for_invoice` |
| `approvals.py` | approvals | `open`, `record_decision`, `list_pending` |
| `episodic.py` | episodic_decisions | `record`, `similar` (SQL channel; pgvector is M7) |
| `llm_calls.py` | llm_calls | `record` (successes **and** failures) |
| `../audit.py` | audit_log | `record(actor, action, target, allowed, reason, detail)` |

`insert_with_lines` uses `ON CONFLICT DO NOTHING ... RETURNING` — verified in §2.2 to return 0 rows
without raising, which is what makes re-uploading the same file idempotent instead of a crash.

### 6.1 An ordering risk, stated rather than assumed

Building `journal.py` now creates a **journal-entry write path five milestones before its code-layer
guard**. `tools/guards.py` — which asserts `decision == "approved"` and re-reads Postgres to confirm
the `approvals` row persisted — is item 5.3.

Between M2-A and M5.3 the protections are: the two CHECK constraints, the open-flags trigger, and the
fact that nothing calls it. The first two are real and tested here. The third is an assumption, so it
gets a test: **`test_no_production_caller_of_journal_repository`** asserts that outside of
`tests/`, nothing imports `repositories.journal`. When M5.3 lands, that test is replaced by
`test_never_post_without_approval.py`, which is one of the six portfolio tests.

This does not weaken the safety property — it dates it. The property is "three independent layers";
until M5.3, two of the three exist, and the spec says so rather than letting the README imply
otherwise.

---

## 7. Seed data — item 2.5

`scripts/seed_reference.py`, idempotent (upsert on PK), runnable repeatedly, wired to `make seed`.

- **`departments`**: MKT, ADMIN, OPS, IT, SALES.
- **`expense_categories`**: real TT133/2016/TT-BTC account numbers — `6421` (chi phí bán hàng),
  `6422` (chi phí quản lý doanh nghiệp), `1331` (thuế GTGT được khấu trừ), `331` (phải trả cho người
  bán), `156` (hàng hóa), `153` (công cụ, dụng cụ). TT133 not TT200: the scenario is a 40-person
  trading company, i.e. an SME.
- **`keywords_vi`** per category — the non-LLM baseline (§4.1).
- **`budgets`**: per department for the corpus period, with at least one department whose limit M2-C
  can seed three invoices against to produce the order-dependent overrun (item 2.3). **The limits
  chosen here are an input to M2-C** — that is the dependency that fixes the A → C ordering.

Seed values live in one module-level table so M2-C can import the limits rather than hardcode them a
second time.

---

## 8. Tests

### 8.1 Files

- `tests/test_schema_constraints.py` — the gate evidence.
- `tests/test_repositories.py` — round-trip per repository; idempotent re-insert.
- `tests/test_seed_reference.py` — seed twice → identical rows; TT133 account numbers present.

### 8.2 Mutation verification — required, not optional

M1 shipped **four** tests that passed while guarding nothing. A schema milestone is where that is
easiest to repeat, because a test that inserts *valid* data passes whether or not the constraint
exists. Every guard below is verified red by removing the constraint from the migration and re-running.

| Mutation | Guard that must go red |
|---|---|
| Drop `CHECK (total_debit = total_credit)` | `test_unbalanced_journal_entry_rejected` |
| Drop `CHECK (posting_authority <> 'human_approved' OR approval_id IS NOT NULL)` | `test_human_approved_requires_approval_row` |
| Drop `CHECK (debit = 0 OR credit = 0)` | `test_line_cannot_be_both_debit_and_credit` |
| Drop `CHECK (debit >= 0 AND credit >= 0)` | `test_negative_amounts_rejected` |
| Drop the open-flags trigger | `test_auto_clean_blocked_while_flags_open` |
| Drop the `audit_log` trigger | `test_audit_log_immutable_even_as_owner` |
| Grant `UPDATE` on `audit_log` to `invoice_app` | `test_audit_log_update_denied_to_runtime_role` |
| `UNIQUE NULLS NOT DISTINCT` → plain `UNIQUE` on `budgets` | `test_two_department_wide_budgets_rejected` |
| `seller_tax_code` `NOT NULL` → nullable | `test_invoice_requires_seller_tax_code` |
| Drop `UNIQUE (seller_tax_code, template_no, invoice_series, invoice_number)` | `test_duplicate_invoice_is_idempotent` |
| Drop `raised_by` CHECK | `test_flag_source_must_be_deterministic` |

The two `audit_log` rows are separate on purpose: each mechanism is verified with the other still in
place, so neither can be credited for the other's work. That is the specific error §2.1 exists to
prevent.

### 8.3 Regression that must stay green

`test_alembic_ignores_langgraph.py` (M1.6, mutation-verified) must still emit an **empty**
autogenerate diff with 15 new tables in metadata. This is the single most likely thing to break, and
the reason `episodic_decisions.reason_embedding` is deferred.

---

## 9. Success criteria

1. `make db-reset && make seed` runs clean from an empty volume.
2. `alembic upgrade head` then `alembic revision --autogenerate` produces an **empty** migration.
3. `INSERT` an unbalanced journal entry → rejected by CHECK. *(M2 gate)*
4. `UPDATE app.audit_log` → rejected as `invoice_app` (permission) **and** as `invoice` (trigger).
   *(M2 gate)*
5. `INSERT` a journal entry with `posting_authority='auto_clean'` for an invoice with open flags →
   rejected by trigger.
6. Re-inserting the same invoice → 1 row, no exception.
7. All 11 mutations in §8.2 verified red.
8. `make check` green — ruff, mypy, and the full suite (123 tests today, plus this spec's).

---

## 10. Open questions — recorded, not silently dropped

1. **NĐ 70/2025/NĐ-CP + `item_kind`** — inherited from M1.2. `InvoiceLine.item_kind` stays
   `int | None` rather than defaulting to 1 until the regulation answers whether QĐ-1450 permits
   omitting `TChat`. Does not block M2-A: the column is nullable and no constraint depends on it.
2. **12-digit household MST checksum** — plan §M1.2 records that it is derived from CCCD with no
   known public checksum rule. `vendors.tax_code` therefore carries no format CHECK. Belongs to
   M2-B's `taxcode.py`; a rejecting validator would flag real invoices.
3. **`TSuat` codes `KCT` / `KKKNT`** — the parser rejects them loudly today because no fixture
   contains one. Nothing in M2-A depends on this, but M2-C must not generate them before
   `vat_rate()` is extended.
4. **Neon `CREATE ROLE`** — the migration creates `invoice_app`. Whether Neon's free tier permits
   role creation is unverified and is an M8 deploy question, not an M2 one.
5. **`invoices.status` values** — a prediction, not a measurement (§4.6). Expect M3–M5 to amend it.
