# M2-A Schema, Seed & Repositories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `app` schema — 15 tables, the three-layer safety property, seeded TT133 reference data, and repositories for every table — so M3 has a system of record to write into.

**Architecture:** Nine Alembic revisions, each creating a coherent table group and granting on exactly the tables it created. SQLAlchemy Core `Table` objects in `db/models.py` are the single source of truth: Alembic autogenerates from them and repositories query through them, so a renamed column breaks at import time. Runtime connects as a NOSUPERUSER role `invoice_app`; Alembic and the seed script connect as the owner `invoice`.

**Tech Stack:** Postgres 17 + pgvector · SQLAlchemy 2.0.51 Core (no ORM) · Alembic · psycopg 3 · pytest

**Spec:** `docs/superpowers/specs/2026-07-28-m2a-schema-seed-repositories-design.md`

## Global Constraints

- **Language:** all docs, comments, docstrings and commit messages in **English** (CLAUDE.md, in effect 2026-07-27). Product data stays Vietnamese: `name_vi`, `keywords_vi`, seeded category names.
- **Do not touch** `langgraph` schema, `migrations/env.py:include_object`, or `checkpointer.py`. ADR 0002 governs; `tests/test_alembic_ignores_langgraph.py` is mutation-verified and must stay green.
- **Do not modify** `spike_counter` — `test_hitl_survives_restart.py` (closed M1 gate) counts rows in it.
- **Money and quantity columns are unconstrained `sa.Numeric`** — never `Numeric(18,2)`. M1 measured 8 decimal places (`21618409.09000000`) and the validation rule is an exact comparison.
- **Every non-obvious constant carries a comment with date + measurement + source file** (CLAUDE.md).
- **No LLM anything in this milestone.** No agent may write a flag; `invoice_flags.raised_by` enforces it in DDL.
- `line-length = 100`, ruff `E,F,I,UP,B,SIM` with `E501` ignored; mypy over `packages = ["invoice_agent"]`.
- Verify with `make check` (ruff + mypy + pytest). Postgres must be up: `make dev`.
- Baseline at plan time: **123 tests passing**.

## Deviation from the spec, and why

Spec §3 sketches a single `<rev>_m2_core_schema.py`. This plan uses **nine revisions** instead. One revision cannot be built test-first: after the first `alembic upgrade head` the implementer would have to `downgrade` to amend it, and a half-applied 15-table migration is the worst possible state to debug. Nine revisions also let each one grant on exactly the tables it creates, so `invoice_app` is never mid-way through a permission set. The end state is identical.

## File Structure

| File | Responsibility |
|---|---|
| `src/invoice_agent/config.py` | **modify** — add `runtime_database_url`, `runtime_dsn`, `runtime_sqlalchemy_url` |
| `src/invoice_agent/db/models.py` | **modify** — 15 `Table` definitions alongside `spike_counter` |
| `src/invoice_agent/db/engine.py` | new — cached `get_engine()` (runtime role) and `get_owner_engine()` (admin) |
| `src/invoice_agent/db/audit.py` | new — `audit_log` writer |
| `src/invoice_agent/db/repositories/__init__.py` | new — package marker |
| `src/invoice_agent/db/repositories/reference.py` | new — departments, expense_categories, budgets |
| `src/invoice_agent/db/repositories/vendors.py` | new — vendors |
| `src/invoice_agent/db/repositories/batches.py` | new — batches |
| `src/invoice_agent/db/repositories/invoices.py` | new — invoices + invoice_line_items |
| `src/invoice_agent/db/repositories/flags.py` | new — invoice_flags |
| `src/invoice_agent/db/repositories/budget_consumption.py` | new — budget_consumption |
| `src/invoice_agent/db/repositories/journal.py` | new — journal_entries + journal_entry_lines |
| `src/invoice_agent/db/repositories/approvals.py` | new — approvals |
| `src/invoice_agent/db/repositories/episodic.py` | new — episodic_decisions |
| `src/invoice_agent/db/repositories/llm_calls.py` | new — llm_calls |
| `scripts/seed_reference.py` | new — item 2.5 |
| `migrations/versions/*.py` | new — 9 revisions |
| `tests/test_schema_constraints.py` | new — gate evidence, mutation-verified |
| `tests/test_repositories.py` | new — round-trip per repository |
| `tests/test_seed_reference.py` | new — idempotency + TT133 numbers |
| `tests/conftest.py` | **modify** — add `runtime_conn` and `owner_conn` fixtures |
| `Makefile`, `.env.example`, `.github/workflows/ci.yml` | **modify** |

---

### Task 1: `invoice_app` role + runtime DSN

**Files:**
- Modify: `src/invoice_agent/config.py`
- Modify: `tests/test_config.py`
- Create: `migrations/versions/<rev>_m2a_01_runtime_role.py`
- Modify: `.env.example`, `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `Settings._with_options` (existing, `config.py`)
- Produces: `Settings.runtime_database_url: str`, `Settings.runtime_dsn: str`, `Settings.runtime_sqlalchemy_url: str`; DB role `invoice_app`

**Background the implementer needs:** `_with_options` currently hardcodes `self.database_url`. It URL-encodes the `options` parameter, and that encoding is load-bearing — M0.2 shipped without it and psycopg rejected the DSN while SQLAlchemy silently accepted, so Alembic worked and `PostgresSaver` died. Reuse it; do not write a second one.

- [ ] **Step 1: Write the failing test**

In `tests/test_config.py`:

```python
def test_runtime_dsn_is_a_different_role_from_the_owner():
    """The whole point of the runtime role is that it is NOT the owner.

    Measured 2026-07-28 (spec §2.1): `invoice` is a SUPERUSER, and superusers
    bypass privilege checks entirely, so `REVOKE UPDATE ON audit_log` applies
    to the ACL and changes nothing. If both DSNs resolve to the same role the
    permission layer is decorative.
    """
    settings = make_settings()
    assert "invoice_app" in settings.runtime_dsn
    assert settings.runtime_dsn != settings.app_dsn


def test_runtime_dsn_url_encodes_search_path_options():
    """Same trap as M0.2: an unencoded `options=-csearch_path=app,public`
    carries a second `=`, which psycopg rejects as `extra key/value separator`
    while SQLAlchemy silently rebuilds the conninfo and accepts it."""
    dsn = make_settings().runtime_dsn
    assert "options=-csearch_path%3Dapp%2Cpublic" in dsn
    assert "options=-csearch_path=" not in dsn


def test_runtime_sqlalchemy_url_selects_psycopg3():
    """Bare `postgresql://` means psycopg2, which is not installed."""
    assert make_settings().runtime_sqlalchemy_url.startswith("postgresql+psycopg://")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py -k runtime -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'runtime_dsn'`

- [ ] **Step 3: Write minimal implementation**

In `config.py`, generalise `_with_options` and add the three members:

```python
# Runtime role. NOT the owner: measured 2026-07-28 (spec §2.1), `invoice`
# is a SUPERUSER, so REVOKE against it lands in the ACL and is then
# bypassed. Repositories connect as this role; Alembic and the seed script
# keep using `database_url` because both are administrative.
runtime_database_url: str = "postgresql://invoice_app:invoice_app@localhost:5433/invoice_agent"


@property
def runtime_dsn(self) -> str:
    """Business connection with least privilege — see `app_dsn` for the options rules."""
    return self._with_options(f"-csearch_path={APP_SCHEMA},public", base=self.runtime_database_url)


@property
def runtime_sqlalchemy_url(self) -> str:
    return self.runtime_dsn.replace("postgresql://", "postgresql+psycopg://", 1)


def _with_options(self, options: str, base: str | None = None) -> str:
    url = self.database_url if base is None else base
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}options={quote(options, safe='')}"
```

Create the migration with `.venv/bin/alembic revision -m "m2a_01_runtime_role"`, then:

```python
def upgrade() -> None:
    # Role creation lives in the migration, not scripts/init_db.sql: init_db.sql
    # runs only on first initialisation of a fresh volume, so every existing dev
    # volume would silently lack the role until a `make db-reset`, and CI would
    # need an extra step. DO block because Postgres has no CREATE ROLE IF NOT EXISTS.
    #
    # The password is a local-dev default matching docker-compose's `invoice:invoice`.
    # Deployment (M8) sets a real one out of band with ALTER ROLE.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'invoice_app') THEN
                CREATE ROLE invoice_app LOGIN PASSWORD 'invoice_app'
                    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
            END IF;
        END $$;
    """)
    op.execute("GRANT USAGE ON SCHEMA app TO invoice_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON SCHEMA app FROM invoice_app")
    op.execute("DROP ROLE IF EXISTS invoice_app")
```

Add to `.env.example`:

```
# Least-privilege runtime role. Repositories use this; Alembic uses DATABASE_URL.
RUNTIME_DATABASE_URL=postgresql://invoice_app:invoice_app@localhost:5433/invoice_agent
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
make migrate
.venv/bin/python -m pytest tests/test_config.py -v
```
Expected: PASS. Then confirm the role exists and is not a superuser:

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent \
  -c "SELECT rolname, rolsuper FROM pg_roles WHERE rolname='invoice_app'"
```
Expected: `invoice_app | f`

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/config.py tests/test_config.py migrations/versions/ .env.example .github/workflows/ci.yml
git commit -m "feat(db): least-privilege invoice_app role + runtime DSN

Measured 2026-07-28: POSTGRES_USER=invoice is a SUPERUSER, so the design
doc's REVOKE lands in the ACL and is bypassed anyway. Runtime now connects
as a NOSUPERUSER role so the privilege layer is real."
```

---

### Task 2: Reference tables, seed, and `make seed`

**Files:**
- Modify: `src/invoice_agent/db/models.py`
- Create: `src/invoice_agent/db/engine.py`, `src/invoice_agent/db/repositories/__init__.py`, `src/invoice_agent/db/repositories/reference.py`, `scripts/seed_reference.py`
- Create: `tests/test_seed_reference.py`; Modify: `tests/conftest.py`, `Makefile`
- Create: `migrations/versions/<rev>_m2a_02_reference.py`

**Interfaces:**
- Consumes: `Settings.runtime_sqlalchemy_url`, `Settings.app_sqlalchemy_url` (Task 1)
- Produces: `db.engine.get_engine() -> Engine`, `db.engine.get_owner_engine() -> Engine`; tables `departments`, `expense_categories`, `budgets`; `repositories.reference.{list_departments, get_category, list_budgets, upsert_department, upsert_category, upsert_budget}`; `scripts.seed_reference.{DEPARTMENTS, CATEGORIES, BUDGETS, seed}`
- Produces for M2-C: `scripts.seed_reference.BUDGETS` is the authoritative limit table the corpus generator imports (spec §7)

- [ ] **Step 1: Write the failing test**

`tests/test_seed_reference.py`:

```python
"""Seed reference data — item 2.5.

`keywords_vi` is not decoration: it is the non-LLM baseline every
classification metric must be reported against (principle 3, design doc
§10.4). It stays Vietnamese because it is data the system ingests.
"""

import pytest
from sqlalchemy import select

from invoice_agent.db import models
from invoice_agent.db.engine import get_owner_engine
from scripts.seed_reference import BUDGETS, seed

# TT133/2016/TT-BTC, not TT200: the scenario is a 40-person trading company,
# i.e. an SME (design doc §2.1).
TT133_ACCOUNTS = {"6421", "6422", "1331", "331", "156", "153"}


@pytest.fixture
def seeded(requires_postgres):
    engine = get_owner_engine()
    with engine.begin() as conn:
        seed(conn)
    return engine


def test_seed_uses_real_tt133_account_numbers(seeded):
    with seeded.connect() as conn:
        rows = conn.execute(
            select(
                models.expense_categories.c.debit_account,
                models.expense_categories.c.vat_account,
                models.expense_categories.c.credit_account,
            )
        ).all()
    used = {a for row in rows for a in row if a is not None}
    assert used <= TT133_ACCOUNTS, f"non-TT133 account numbers seeded: {used - TT133_ACCOUNTS}"
    assert {"6421", "6422", "1331", "331"} <= used


def test_every_category_has_a_non_empty_keywords_baseline(seeded):
    with seeded.connect() as conn:
        rows = conn.execute(
            select(models.expense_categories.c.code, models.expense_categories.c.keywords_vi)
        ).all()
    assert rows, "no categories seeded"
    empty = [code for code, kw in rows if not kw]
    assert not empty, f"categories with no keywords_vi baseline: {empty}"


def test_seed_is_idempotent(seeded):
    """`make seed` must be safe to re-run — it is wired into `make dev`."""
    with seeded.connect() as conn:
        before = conn.execute(select(models.expense_categories)).all()
    with seeded.begin() as conn:
        seed(conn)
    with seeded.connect() as conn:
        after = conn.execute(select(models.expense_categories)).all()
    assert before == after


def test_two_department_wide_budgets_rejected(seeded):
    """MUTATION GUARD (spec §8.2).

    `category IS NULL` means whole-department budget. Postgres treats NULLs as
    distinct, so a plain UNIQUE admits two such rows and the department silently
    gets two budgets. Verified red 2026-07-28 by dropping
    `postgresql_nulls_not_distinct=True`.
    """
    from sqlalchemy.exc import IntegrityError

    from invoice_agent.db.repositories import reference

    with seeded.begin() as conn:
        reference.upsert_department(conn, "ZZTEST", "Phòng thử nghiệm")
    with pytest.raises(IntegrityError):
        with seeded.begin() as conn:
            conn.execute(
                models.budgets.insert(),
                [
                    {
                        "department_code": "ZZTEST",
                        "period": "2026-07",
                        "category": None,
                        "amount_limit": 1000,
                        "warn_threshold_pct": Decimal("0.8"),
                    },
                    {
                        "department_code": "ZZTEST",
                        "period": "2026-07",
                        "category": None,
                        "amount_limit": 2000,
                        "warn_threshold_pct": Decimal("0.8"),
                    },
                ],
            )
```

Add `from decimal import Decimal` to the imports.

Add to `tests/conftest.py`:

```python
@pytest.fixture
def owner_conn(requires_postgres):
    """Admin connection — Alembic and the seed script use this role."""
    from invoice_agent.db.engine import get_owner_engine

    with get_owner_engine().begin() as conn:
        yield conn


@pytest.fixture
def runtime_conn(requires_postgres):
    """Least-privilege connection — what repositories actually use in production."""
    from invoice_agent.db.engine import get_engine

    with get_engine().begin() as conn:
        yield conn
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_seed_reference.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'invoice_agent.db.engine'`

- [ ] **Step 3: Write minimal implementation**

`src/invoice_agent/db/engine.py`:

```python
"""Two engines, two privilege levels.

Repositories use `get_engine()` (role `invoice_app`, no UPDATE/DELETE on
audit_log). Alembic and `scripts/seed_reference.py` use `get_owner_engine()`
because both are administrative. Measured 2026-07-28 (spec §2.1): if runtime
used the owner, every REVOKE in this schema would be decorative.
"""

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from invoice_agent.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().runtime_sqlalchemy_url, pool_pre_ping=True)


@lru_cache
def get_owner_engine() -> Engine:
    return create_engine(get_settings().app_sqlalchemy_url, pool_pre_ping=True)
```

In `db/models.py`, add (keeping `spike_counter` and its comment untouched):

```python
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

departments = Table(
    "departments",
    metadata,
    Column("code", sa.Text, primary_key=True),
    Column("name_vi", sa.Text, nullable=False),
)

expense_categories = Table(
    "expense_categories",
    metadata,
    Column("code", sa.Text, primary_key=True),
    Column("name_vi", sa.Text, nullable=False),
    Column("debit_account", sa.Text, nullable=False),
    Column("vat_account", sa.Text),
    Column("credit_account", sa.Text, nullable=False),
    # The non-LLM baseline (design doc §10.4). Vietnamese by design: it is
    # ingested data, not authoring style (CLAUDE.md language rule).
    Column("keywords_vi", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
)

budgets = Table(
    "budgets",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column("department_code", sa.Text, sa.ForeignKey("departments.code"), nullable=False),
    Column("period", sa.Text, nullable=False),  # YYYY-MM
    Column("category", sa.Text, sa.ForeignKey("expense_categories.code")),
    Column("amount_limit", sa.Numeric, nullable=False),
    Column("warn_threshold_pct", sa.Numeric, nullable=False, server_default="0.8"),
    sa.CheckConstraint("amount_limit > 0", name="ck_budgets_limit_positive"),
    sa.CheckConstraint(
        "warn_threshold_pct > 0 AND warn_threshold_pct <= 1",
        name="ck_budgets_warn_threshold_range",
    ),
    # NULLS NOT DISTINCT because `category IS NULL` MEANS "whole department"
    # and two such rows must not coexist. Measured 2026-07-28 (spec §2.2): a
    # plain UNIQUE admits both. This is the one place in the schema where a
    # NULL in a key is meaningful — `invoices` solves the same problem by
    # making seller_tax_code NOT NULL instead.
    sa.UniqueConstraint(
        "department_code",
        "period",
        "category",
        name="uq_budgets_department_period_category",
        postgresql_nulls_not_distinct=True,
    ),
)
```

`src/invoice_agent/db/repositories/reference.py`:

```python
"""Reference data access — departments, expense categories, budgets."""

from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert

from invoice_agent.db import models


def list_departments(conn: Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(select(models.departments)).mappings()]


def get_category(conn: Connection, code: str) -> dict[str, Any] | None:
    row = (
        conn.execute(
            select(models.expense_categories).where(models.expense_categories.c.code == code)
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def list_budgets(conn: Connection, department_code: str, period: str) -> list[dict[str, Any]]:
    stmt = select(models.budgets).where(
        models.budgets.c.department_code == department_code,
        models.budgets.c.period == period,
    )
    return [dict(r) for r in conn.execute(stmt).mappings()]


def upsert_department(conn: Connection, code: str, name_vi: str) -> None:
    stmt = insert(models.departments).values(code=code, name_vi=name_vi)
    conn.execute(stmt.on_conflict_do_update(index_elements=["code"], set_={"name_vi": name_vi}))


def upsert_category(
    conn: Connection,
    code: str,
    name_vi: str,
    debit_account: str,
    vat_account: str | None,
    credit_account: str,
    keywords_vi: list[str],
) -> None:
    values = {
        "code": code,
        "name_vi": name_vi,
        "debit_account": debit_account,
        "vat_account": vat_account,
        "credit_account": credit_account,
        "keywords_vi": keywords_vi,
    }
    stmt = insert(models.expense_categories).values(**values)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["code"], set_={k: v for k, v in values.items() if k != "code"}
        )
    )


def upsert_budget(
    conn: Connection,
    department_code: str,
    period: str,
    category: str | None,
    amount_limit: Decimal,
    warn_threshold_pct: Decimal = Decimal("0.8"),
) -> int:
    values = {
        "department_code": department_code,
        "period": period,
        "category": category,
        "amount_limit": amount_limit,
        "warn_threshold_pct": warn_threshold_pct,
    }
    stmt = insert(models.budgets).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_budgets_department_period_category",
        set_={"amount_limit": amount_limit, "warn_threshold_pct": warn_threshold_pct},
    ).returning(models.budgets.c.id)
    return int(conn.execute(stmt).scalar_one())
```

`scripts/seed_reference.py`:

```python
"""Seed reference data — plan item 2.5.

Account numbers are real TT133/2016/TT-BTC codes, not invented ones. TT133
applies to small and medium enterprises, which matches the scenario (a
40-person trading company); TT200 is for large enterprises. Using real codes is
a credibility signal in a Vietnamese accounting-adjacent interview — design doc
§2.1 says so explicitly.

`BUDGETS` is imported by M2-C's generator. Do not hardcode these limits a
second time there: item 2.3 needs three Marketing invoices seeded against a
known limit so that under canonical order the THIRD one overruns, and two
copies of the limit is how that test starts passing for the wrong reason.
"""

from decimal import Decimal

from sqlalchemy import Connection

from invoice_agent.db.engine import get_owner_engine
from invoice_agent.db.repositories import reference

DEPARTMENTS: list[tuple[str, str]] = [
    ("MKT", "Phòng Marketing"),
    ("ADMIN", "Phòng Hành chính"),
    ("OPS", "Phòng Vận hành"),
    ("IT", "Phòng Công nghệ thông tin"),
    ("SALES", "Phòng Kinh doanh"),
]

# (code, name_vi, debit, vat, credit, keywords_vi)
# `1331` = thuế GTGT được khấu trừ, `331` = phải trả cho người bán.
# `6421` = chi phí bán hàng, `6422` = chi phí quản lý doanh nghiệp.
CATEGORIES: list[tuple[str, str, str, str | None, str, list[str]]] = [
    (
        "OFFICE_SUPPLIES",
        "Văn phòng phẩm",
        "6422",
        "1331",
        "331",
        ["văn phòng phẩm", "giấy a4", "bút", "mực in", "sổ", "ghim", "kẹp"],
    ),
    (
        "MARKETING",
        "Chi phí marketing",
        "6421",
        "1331",
        "331",
        ["quảng cáo", "marketing", "truyền thông", "sự kiện", "in ấn", "banner", "facebook ads"],
    ),
    (
        "IT_SERVICES",
        "Dịch vụ công nghệ thông tin",
        "6422",
        "1331",
        "331",
        ["phần mềm", "hosting", "tên miền", "máy chủ", "cloud", "bản quyền", "license"],
    ),
    (
        "LOGISTICS",
        "Chi phí vận chuyển",
        "6421",
        "1331",
        "331",
        ["vận chuyển", "giao hàng", "cước", "bưu điện", "chuyển phát", "logistics"],
    ),
    (
        "UTILITIES",
        "Điện nước, viễn thông",
        "6422",
        "1331",
        "331",
        ["tiền điện", "tiền nước", "cước viễn thông", "internet", "điện thoại"],
    ),
    (
        "GOODS",
        "Hàng hóa mua vào",
        "156",
        "1331",
        "331",
        ["hàng hóa", "nhập kho", "mua hàng", "sản phẩm"],
    ),
    (
        "TOOLS",
        "Công cụ, dụng cụ",
        "153",
        "1331",
        "331",
        ["công cụ", "dụng cụ", "thiết bị", "máy móc nhỏ"],
    ),
]

# (department_code, period, category, amount_limit)
# MKT's limit is deliberately tight: M2-C item 2.3 seeds three Marketing
# invoices against it so that in canonical order (issue_date, invoice_series,
# invoice_number) the THIRD overruns, while a shuffled arrival order would
# overrun a different one if reconciliation were not serial.
BUDGETS: list[tuple[str, str, str | None, Decimal]] = [
    ("MKT", "2026-07", None, Decimal("50000000")),
    ("ADMIN", "2026-07", None, Decimal("30000000")),
    ("OPS", "2026-07", None, Decimal("80000000")),
    ("IT", "2026-07", None, Decimal("60000000")),
    ("SALES", "2026-07", None, Decimal("40000000")),
]


def seed(conn: Connection) -> None:
    """Idempotent — every write is an upsert. `make seed` is safe to re-run."""
    for code, name_vi in DEPARTMENTS:
        reference.upsert_department(conn, code, name_vi)
    for code, name_vi, debit, vat, credit, keywords in CATEGORIES:
        reference.upsert_category(conn, code, name_vi, debit, vat, credit, keywords)
    for dept, period, category, limit in BUDGETS:
        reference.upsert_budget(conn, dept, period, category, limit)


def main() -> None:
    with get_owner_engine().begin() as conn:
        seed(conn)
    print("✅ reference data seeded")


if __name__ == "__main__":
    main()
```

Generate the migration with `.venv/bin/alembic revision --autogenerate -m "m2a_02_reference"`, review it, then append the grants to `upgrade()`:

```python
op.execute(
    "GRANT SELECT, INSERT, UPDATE ON app.departments, app.expense_categories, app.budgets TO invoice_app"
)
op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO invoice_app")
```

Add to `Makefile` (and append `$(MAKE) seed` to the `dev` target after `$(MAKE) migrate`):

```makefile
seed:
	$(PY) -m scripts.seed_reference
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
make migrate && make seed
.venv/bin/python -m pytest tests/test_seed_reference.py -v
```
Expected: PASS (4 tests). Then verify the mutation guard is real — temporarily remove `postgresql_nulls_not_distinct=True` from `budgets`, regenerate, and confirm `test_two_department_wide_budgets_rejected` goes **red**. Restore afterwards.

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/db/ src/invoice_agent/db/repositories/ scripts/seed_reference.py tests/ migrations/versions/ Makefile
git commit -m "feat(db): reference tables + TT133 seed + make seed

budgets uses UNIQUE NULLS NOT DISTINCT: category IS NULL means
whole-department, and measured 2026-07-28 a plain UNIQUE admits two such
rows. Guard is mutation-verified red."
```

---

### Task 3: `vendors` and `batches`

**Files:**
- Modify: `src/invoice_agent/db/models.py`
- Create: `src/invoice_agent/db/repositories/vendors.py`, `src/invoice_agent/db/repositories/batches.py`, `tests/test_repositories.py`
- Create: `migrations/versions/<rev>_m2a_03_vendors_batches.py`

**Interfaces:**
- Consumes: `db.engine.get_engine` (Task 2)
- Produces: tables `vendors`, `batches`; `repositories.vendors.{ensure_vendor, get_vendor, update_profile}`, `repositories.batches.{create_batch, complete_batch, get_batch}`

- [ ] **Step 1: Write the failing test**

`tests/test_repositories.py`:

```python
"""Round-trip tests, one per repository. All run as the RUNTIME role.

Running these as the owner would hide a whole class of bug: a missing GRANT.
The runtime role is what production uses, so it is what the tests use.
"""

import uuid
from decimal import Decimal

import pytest

from invoice_agent.db.repositories import batches, vendors

# Three real MST shapes, read from tests/fixtures/real_samples/ on 2026-07-28
# (already anonymised — see spec §2.2 provenance note). A CHAR(10) assumption
# rejects two of the three.
MST_SHAPES = ["0100000003", "0300000018-042", "050000000000"]


@pytest.mark.parametrize("tax_code", MST_SHAPES)
def test_vendors_accept_all_three_real_mst_shapes(runtime_conn, tax_code):
    vendors.ensure_vendor(runtime_conn, tax_code, name="Nhà cung cấp thử nghiệm")
    row = vendors.get_vendor(runtime_conn, tax_code)
    assert row is not None
    assert row["tax_code"] == tax_code


def test_ensure_vendor_is_idempotent_and_does_not_clobber_the_profile(runtime_conn):
    """Ingestion creates a STUB row to satisfy the invoices FK. Statistics are
    filled transactionally at post/approve (M4.3, design doc §2.2), so a second
    sighting must not reset them."""
    tax_code = f"ZZ{uuid.uuid4().hex[:8]}"
    vendors.ensure_vendor(runtime_conn, tax_code, name="Lần đầu")
    vendors.update_profile(runtime_conn, tax_code, invoice_count=7, total_amount=Decimal("1000000"))
    vendors.ensure_vendor(runtime_conn, tax_code, name="Lần hai")
    row = vendors.get_vendor(runtime_conn, tax_code)
    assert row["invoice_count"] == 7, "second sighting wiped accumulated memory"
    assert row["total_amount"] == Decimal("1000000")


def test_batch_lifecycle(runtime_conn):
    batch_id = batches.create_batch(runtime_conn, label="test-batch")
    assert batches.get_batch(runtime_conn, batch_id)["status"] == "running"
    batches.complete_batch(runtime_conn, batch_id, status="completed", invoice_count=30)
    row = batches.get_batch(runtime_conn, batch_id)
    assert row["status"] == "completed"
    assert row["invoice_count"] == 30
    assert row["completed_at"] is not None


def test_batch_status_is_constrained(runtime_conn):
    from sqlalchemy.exc import IntegrityError

    batch_id = batches.create_batch(runtime_conn, label="bad-status")
    with pytest.raises(IntegrityError):
        batches.complete_batch(runtime_conn, batch_id, status="finished", invoice_count=1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_repositories.py -v`
Expected: FAIL — `ImportError: cannot import name 'vendors'`

- [ ] **Step 3: Write minimal implementation**

Add to `db/models.py`:

```python
vendors = Table(
    "vendors",
    metadata,
    # TEXT with no length constraint. Three real shapes measured 2026-07-28 on
    # the 8 fixtures: '0100000003' (10), '0300000018-042' (branch, with a dash)
    # and '050000000000' (12, household business). CHAR(10) rejects two of three.
    Column("tax_code", sa.Text, primary_key=True),
    Column("name", sa.Text),
    Column("normalized_name", sa.Text),
    Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_seen_at", sa.DateTime(timezone=True)),
    Column("invoice_count", sa.Integer, nullable=False, server_default="0"),
    Column("total_amount", sa.Numeric, nullable=False, server_default="0"),
    Column("avg_amount", sa.Numeric),
    Column("stddev_amount", sa.Numeric),
    Column("category_histogram", postgresql.JSONB, nullable=False, server_default="{}"),
    Column("modal_category", sa.Text),
    Column("modal_department", sa.Text),
    Column("rejection_count", sa.Integer, nullable=False, server_default="0"),
    Column("is_trusted", sa.Boolean, nullable=False, server_default=sa.false()),
    Column("reviewer_notes", sa.Text),
)

BATCH_STATUSES = ("running", "completed", "failed")

batches = Table(
    "batches",
    metadata,
    Column("id", sa.Uuid(as_uuid=True), primary_key=True),
    Column("label", sa.Text),
    Column("status", sa.Text, nullable=False, server_default="running"),
    Column("invoice_count", sa.Integer, nullable=False, server_default="0"),
    Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("completed_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_batches_status"),
)
```

`repositories/vendors.py`:

```python
"""Vendor entity memory (design doc §2.2).

`ensure_vendor` writes a STUB only. Statistics are materialized at post/approve
inside a transaction (M4.3) rather than recomputed per node — design doc §2.2 is
explicit that recomputing in the hot path is too slow, and that "memory growing
over time" is the point being demonstrated.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, func, select
from sqlalchemy.dialects.postgresql import insert

from invoice_agent.db import models


def ensure_vendor(conn: Connection, tax_code: str, name: str | None = None) -> None:
    """Create on first sight; on later sightings touch `last_seen_at` ONLY.

    Never overwrite accumulated statistics here — a second invoice from a known
    vendor must not reset the memory built from the first hundred.
    """
    stmt = insert(models.vendors).values(tax_code=tax_code, name=name, last_seen_at=func.now())
    conn.execute(
        stmt.on_conflict_do_update(index_elements=["tax_code"], set_={"last_seen_at": func.now()})
    )


def get_vendor(conn: Connection, tax_code: str) -> dict[str, Any] | None:
    row = (
        conn.execute(select(models.vendors).where(models.vendors.c.tax_code == tax_code))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def update_profile(conn: Connection, tax_code: str, **fields: Any) -> None:
    """Materialize the derived profile. Callers pass only the fields they computed.

    `scripts/rebuild_vendor_profiles.py` (M4.3) recomputes all of this from
    `invoices` — the mandatory escape hatch for anything materialized from a
    derivable source (design doc §2.2).
    """
    if not fields:
        return
    conn.execute(
        models.vendors.update().where(models.vendors.c.tax_code == tax_code).values(**fields)
    )
```

`repositories/batches.py`:

```python
"""Batch bookkeeping. `batch_id` is referenced by design doc §2.3 but the table
is never defined there, so it is defined minimally here: enough to answer "which
run produced this invoice" and nothing speculative."""

import uuid
from typing import Any

from sqlalchemy import Connection, func, select

from invoice_agent.db import models


def create_batch(conn: Connection, label: str | None = None) -> uuid.UUID:
    batch_id = uuid.uuid4()
    conn.execute(models.batches.insert().values(id=batch_id, label=label, status="running"))
    return batch_id


def complete_batch(
    conn: Connection, batch_id: uuid.UUID, *, status: str, invoice_count: int
) -> None:
    conn.execute(
        models.batches.update()
        .where(models.batches.c.id == batch_id)
        .values(status=status, invoice_count=invoice_count, completed_at=func.now())
    )


def get_batch(conn: Connection, batch_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        conn.execute(select(models.batches).where(models.batches.c.id == batch_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None
```

Generate the migration (`--autogenerate -m "m2a_03_vendors_batches"`) and append:

```python
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.vendors, app.batches TO invoice_app")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `make migrate && .venv/bin/python -m pytest tests/test_repositories.py -v`
Expected: PASS (6 tests — 3 parametrized MST shapes + 3)

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/db/ tests/test_repositories.py migrations/versions/
git commit -m "feat(db): vendors + batches with all three real MST shapes"
```

---

### Task 4: `invoices` and `invoice_line_items`

This is the milestone's identity model. Read spec §2.2 before starting.

**Files:**
- Modify: `src/invoice_agent/db/models.py`, `tests/test_repositories.py`
- Create: `src/invoice_agent/db/repositories/invoices.py`, `tests/test_schema_constraints.py`
- Create: `migrations/versions/<rev>_m2a_04_invoices.py`

**Interfaces:**
- Consumes: `repositories.vendors.ensure_vendor`, `repositories.batches.create_batch` (Task 3); `invoice_agent.extraction.models.InvoiceData` (M1.4)
- Produces: tables `invoices`, `invoice_line_items`; `repositories.invoices.{insert_with_lines, get_by_natural_key, get_lines, set_status}`; `models.INVOICE_STATUSES`

- [ ] **Step 1: Write the failing test**

`tests/test_schema_constraints.py`:

```python
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
from invoice_agent.db.repositories import invoices as invoice_repo
from invoice_agent.db.repositories import vendors


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
```

And in `tests/test_repositories.py`:

```python
def test_insert_with_lines_round_trips_a_real_fixture(runtime_conn):
    """The eight real fixtures are the anti-circularity anchor (design doc
    §10.1). If the schema cannot store what the M1 parser produces, the
    corpus generator at M2-C will paper over it."""
    from pathlib import Path

    from invoice_agent.db.repositories import batches
    from invoice_agent.extraction.xml_parser import parse_invoice_xml

    path = Path("tests/fixtures/real_samples/lienket_gtgt_7dong_2.1.0.xml")
    data = parse_invoice_xml(path.read_bytes())
    batch_id = batches.create_batch(runtime_conn, label="fixture")

    invoice_id = invoice_repo.insert_with_lines(
        runtime_conn, data, batch_id=batch_id, source_type="xml", source_path=str(path)
    )
    assert invoice_id is not None

    lines = invoice_repo.get_lines(runtime_conn, invoice_id)
    assert len(lines) == 7, "the 7-line fixture lost lines in storage"
    # line_no is NOT NULL and dense; STT was present on 15/15 measured lines.
    assert [r["line_no"] for r in lines] == [1, 2, 3, 4, 5, 6, 7]


def test_decimal_precision_survives_storage(runtime_conn):
    """Money columns are unconstrained NUMERIC. M1 measured 21618409.09000000
    (8 dp) and a unit price of 398181.33 where 3 x 398181.33 = 1194543.99 but
    ThTien = 1194544. NUMERIC(18,2) rounds, and the validation rule that holds
    8/8 is an EXACT comparison."""
    from invoice_agent.db.repositories import batches

    vendors.ensure_vendor(runtime_conn, "0100000003", name="NCC")
    batch_id = batches.create_batch(runtime_conn, label="precision")
    exact = Decimal("21618409.09000000")
    values = _invoice_values(
        batch_id=batch_id, total_amount=exact, invoice_number=f"PR{uuid.uuid4().hex[:6]}"
    )
    invoice_id = invoice_repo.insert_row(runtime_conn, values)
    row = invoice_repo.get_by_id(runtime_conn, invoice_id)
    assert row["total_amount"] == exact
```

Import `_invoice_values` and `invoice_repo` into `test_repositories.py` from the constraints module, or duplicate the helper — duplication is fine here and avoids a test-to-test import.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_schema_constraints.py tests/test_repositories.py -v`
Expected: FAIL — `ImportError: cannot import name 'invoices'`

- [ ] **Step 3: Write minimal implementation**

Add to `db/models.py`:

```python
# Predicted from design doc §3.3's routing, NOT measured — M3-M5 will amend it.
# TEXT + CHECK (rather than a native ENUM) is exactly what makes amending it
# cheap: measured 2026-07-28, a native enum cannot add a value and backfill
# rows to it in one migration ("unsafe use of new value").
INVOICE_STATUSES = (
    "received",
    "extracted",
    "validated",
    "classified",
    "flagged",
    "pending_approval",
    "approved",
    "rejected",
    "posted",
    "manual_queue",
    "failed",
)
SOURCE_TYPES = ("xml", "pdf_vision", "manual")

invoices = Table(
    "invoices",
    metadata,
    Column("id", sa.Uuid(as_uuid=True), primary_key=True),
    Column("batch_id", sa.Uuid(as_uuid=True), sa.ForeignKey("batches.id")),
    Column("template_no", sa.Text, nullable=False),  # KHMSHDon
    Column("invoice_series", sa.Text, nullable=False),  # KHHDon
    Column("invoice_number", sa.Text, nullable=False),  # SHDon
    # NOT NULL although InvoiceData types it `str | None`. Measured 2026-07-28:
    # seller MST is present in 8/8 real fixtures. An invoice without one cannot
    # be given an identity, so ingestion routes it to manual_queue rather than
    # storing it identity-less. The parser is still right to report what it saw;
    # this is the boundary that refuses.
    Column("seller_tax_code", sa.Text, sa.ForeignKey("vendors.tax_code"), nullable=False),
    # Nullable and in no key: measured 8/8 either absent or ''.
    Column("buyer_tax_code", sa.Text),
    Column("issue_date", sa.Date, nullable=False),
    Column("currency", sa.Text),
    Column("exchange_rate", sa.Numeric),
    Column("subtotal", sa.Numeric),
    Column("vat_amount", sa.Numeric),
    Column("total_amount", sa.Numeric, nullable=False),
    Column("total_in_words", sa.Text),
    # Header TTKhac bags. Stored, not dropped: ext already proved load-bearing
    # once — VNPT sets ThTien=0 and puts the real amount in ext['Amount'].
    Column("ext", postgresql.JSONB, nullable=False, server_default="{}"),
    Column("source_type", sa.Text, nullable=False),
    Column("source_path", sa.Text),
    Column("raw_xml", sa.Text),
    Column("extraction_confidence", sa.Numeric),
    Column("extraction_attempts", sa.Integer, nullable=False, server_default="0"),
    Column("category", sa.Text, sa.ForeignKey("expense_categories.code")),
    Column("department", sa.Text, sa.ForeignKey("departments.code")),
    Column("classification_confidence", sa.Numeric),
    Column("status", sa.Text, nullable=False, server_default="received"),
    Column("cost_usd", sa.Numeric),
    Column("shadow_cost_usd", sa.Numeric),
    Column("tokens_in", sa.Integer),
    Column("tokens_out", sa.Integer),
    Column("langfuse_trace_id", sa.Text),
    Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    # THE identity of a Vietnamese e-invoice (design doc §2.3). invoice_number
    # alone is NOT unique across sellers — two vendors both issue no. 00001234.
    sa.UniqueConstraint(
        "seller_tax_code",
        "template_no",
        "invoice_series",
        "invoice_number",
        name="uq_invoices_natural_key",
    ),
    sa.CheckConstraint("status IN " + str(INVOICE_STATUSES), name="ck_invoices_status"),
    sa.CheckConstraint("source_type IN " + str(SOURCE_TYPES), name="ck_invoices_source_type"),
)

invoice_line_items = Table(
    "invoice_line_items",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column(
        "invoice_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    ),
    # NOT NULL although InvoiceLine.line_no is `int | None`. STT was present on
    # 15/15 lines across 8/8 fixtures (2026-07-28); where absent the repository
    # substitutes 1-based document position. One column, not two: a line index
    # is an ordinal, not a fact about the invoice.
    Column("line_no", sa.Integer, nullable=False),
    # ⚠️ PROMPT-INJECTION SURFACE (design doc §2.4, §9.1). Free text written by
    # whoever issued the invoice. Never concatenate into a system prompt —
    # M3.3 wraps it in an escaped <invoice_data> block.
    Column("description", sa.Text),
    Column("unit", sa.Text),
    Column("quantity", sa.Numeric),
    Column("unit_price", sa.Numeric),
    Column("amount", sa.Numeric),
    Column("vat_rate", sa.Numeric),
    Column("vat_amount", sa.Numeric),
    # QĐ-1450 TChat: 1 goods, 2 promotion, 3 discount, 4 NOTE.
    # Nullable, NOT defaulted to 1 — that waits on M1.2's outstanding
    # NĐ 70/2025 check. A wrong default folds a note line into the money sum.
    Column("item_kind", sa.Integer),
    Column("ext", postgresql.JSONB, nullable=False, server_default="{}"),
    sa.UniqueConstraint("invoice_id", "line_no", name="uq_line_items_invoice_line_no"),
)
```

`repositories/invoices.py`:

```python
"""Invoice persistence — the identity model (design doc §2.3).

Re-uploading the same file is IDEMPOTENT, not an error: `insert_row` returns
None on conflict rather than raising, and the caller routes that to duplicate
handling. Verified 2026-07-28 that ON CONFLICT DO NOTHING ... RETURNING yields
zero rows without raising.
"""

import uuid
from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert

from invoice_agent.db import models
from invoice_agent.db.repositories import vendors
from invoice_agent.extraction.models import InvoiceData


class MissingSellerTaxCode(ValueError):
    """An invoice with no seller MST cannot be given an identity.

    Measured 2026-07-28: seller MST is present in 8/8 real fixtures, so this is
    an exceptional document. Design doc §3.3 routes it to manual_queue; storing
    it with a null identity would put it outside the natural key and make
    duplicate detection meaningless for it.
    """


def insert_row(conn: Connection, values: dict[str, Any]) -> uuid.UUID | None:
    """Insert one invoice row. Returns None if the natural key already exists."""
    stmt = (
        insert(models.invoices)
        .values(**values)
        .on_conflict_do_nothing(constraint="uq_invoices_natural_key")
        .returning(models.invoices.c.id)
    )
    return conn.execute(stmt).scalar_one_or_none()


def insert_with_lines(
    conn: Connection,
    data: InvoiceData,
    *,
    batch_id: uuid.UUID | None = None,
    source_type: str = "xml",
    source_path: str | None = None,
    raw_xml: str | None = None,
) -> uuid.UUID | None:
    """Persist an InvoiceData plus its lines. Returns None if it is a duplicate."""
    if not data.seller_tax_code:
        raise MissingSellerTaxCode(
            f"{data.invoice_series}/{data.invoice_number}: no seller tax code; route to manual_queue"
        )

    vendors.ensure_vendor(conn, data.seller_tax_code, name=data.seller_name)

    invoice_id = insert_row(
        conn,
        {
            "id": uuid.uuid4(),
            "batch_id": batch_id,
            "template_no": data.template_no,
            "invoice_series": data.invoice_series,
            "invoice_number": data.invoice_number,
            "seller_tax_code": data.seller_tax_code,
            "buyer_tax_code": data.buyer_tax_code,
            "issue_date": data.issue_date,
            "currency": data.currency,
            "exchange_rate": data.exchange_rate,
            "subtotal": data.subtotal,
            "vat_amount": data.vat_amount,
            "total_amount": data.total_amount,
            "total_in_words": data.total_in_words,
            "ext": data.ext,
            "source_type": source_type,
            "source_path": source_path,
            "raw_xml": raw_xml,
            "status": "extracted",
        },
    )
    if invoice_id is None:
        return None

    if data.lines:
        conn.execute(
            models.invoice_line_items.insert(),
            [
                {
                    "invoice_id": invoice_id,
                    # Document position when STT is absent — see the column comment.
                    "line_no": line.line_no if line.line_no is not None else position,
                    "description": line.description,
                    "unit": line.unit,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "amount": line.amount,
                    "vat_rate": line.vat_rate,
                    "vat_amount": line.vat_amount,
                    "item_kind": line.item_kind,
                    "ext": line.ext,
                }
                for position, line in enumerate(data.lines, start=1)
            ],
        )
    return invoice_id


def get_by_id(conn: Connection, invoice_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        conn.execute(select(models.invoices).where(models.invoices.c.id == invoice_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def get_by_natural_key(
    conn: Connection,
    seller_tax_code: str,
    template_no: str,
    invoice_series: str,
    invoice_number: str,
) -> dict[str, Any] | None:
    row = (
        conn.execute(
            select(models.invoices).where(
                models.invoices.c.seller_tax_code == seller_tax_code,
                models.invoices.c.template_no == template_no,
                models.invoices.c.invoice_series == invoice_series,
                models.invoices.c.invoice_number == invoice_number,
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def get_lines(conn: Connection, invoice_id: uuid.UUID) -> list[dict[str, Any]]:
    stmt = (
        select(models.invoice_line_items)
        .where(models.invoice_line_items.c.invoice_id == invoice_id)
        .order_by(models.invoice_line_items.c.line_no)
    )
    return [dict(r) for r in conn.execute(stmt).mappings()]


def set_status(conn: Connection, invoice_id: uuid.UUID, status: str) -> None:
    conn.execute(
        models.invoices.update()
        .where(models.invoices.c.id == invoice_id)
        .values(status=status, updated_at=func.now())
    )
```

Note the import line at the top of this module is
`from sqlalchemy import Connection, func, select`.

Generate the migration (`--autogenerate -m "m2a_04_invoices"`) and append:

```python
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.invoices, app.invoice_line_items TO invoice_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO invoice_app")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `make migrate && .venv/bin/python -m pytest tests/test_schema_constraints.py tests/test_repositories.py -v`
Expected: PASS

Then run both mutations and confirm red, restoring after each:
1. `seller_tax_code` `nullable=False` → remove → `test_invoice_requires_seller_tax_code` red.
2. Drop `uq_invoices_natural_key` → `test_duplicate_invoice_is_idempotent` red.

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/db/ tests/ migrations/versions/
git commit -m "feat(db): invoices + line items, natural key with no nullable part

seller_tax_code is NOT NULL (measured 8/8 present), so the UNIQUE that
identifies a VN e-invoice carries no NULL. Measured 2026-07-28: with a
nullable component the same invoice inserts twice."
```

---

### Task 5: `invoice_flags` — flags are deterministic, in DDL

**Files:**
- Modify: `src/invoice_agent/db/models.py`, `tests/test_schema_constraints.py`
- Create: `src/invoice_agent/db/repositories/flags.py`
- Create: `migrations/versions/<rev>_m2a_05_flags.py`

**Interfaces:**
- Consumes: `repositories.invoices.insert_row` (Task 4)
- Produces: table `invoice_flags`; `models.FLAG_SOURCES`, `models.FLAG_SEVERITIES`; `repositories.flags.{raise_flag, open_flags_for, resolve_flag}`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_schema_constraints.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_schema_constraints.py::test_flag_source_must_be_deterministic -v`
Expected: FAIL — `ImportError: cannot import name 'flags'`

- [ ] **Step 3: Write minimal implementation**

Add to `db/models.py`:

```python
# Deterministic producers ONLY. This list is the DDL encoding of principle 1
# ("the LLM never raises a flag"): adding an agent here requires a migration,
# so it cannot happen by accident in a node. Design doc §2.5.
FLAG_SOURCES = ("validation", "taxcode", "anomaly", "budget", "duplicate", "vendor_memory")
FLAG_SEVERITIES = ("info", "warn", "critical")

invoice_flags = Table(
    "invoice_flags",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column(
        "invoice_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("code", sa.Text, nullable=False),
    Column("severity", sa.Text, nullable=False),
    Column("detail", postgresql.JSONB, nullable=False, server_default="{}"),
    Column("raised_by", sa.Text, nullable=False),
    # Open == NULL. The journal-entry trigger reads this on every insert.
    Column("resolved_at", sa.DateTime(timezone=True)),
    Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    sa.CheckConstraint("severity IN " + str(FLAG_SEVERITIES), name="ck_flags_severity"),
    sa.CheckConstraint("raised_by IN " + str(FLAG_SOURCES), name="ck_flags_raised_by"),
    sa.Index(
        "ix_flags_open_by_invoice",
        "invoice_id",
        postgresql_where=sa.text("resolved_at IS NULL"),
    ),
)
```

`repositories/flags.py`:

```python
"""Flag persistence.

Note what is NOT here: any way for a caller to declare itself an LLM. The
`raised_by` CHECK enumerates deterministic producers only (models.FLAG_SOURCES),
so the safety property is enforced by the database rather than by discipline.
"""

import uuid
from typing import Any

from sqlalchemy import Connection, func, select

from invoice_agent.db import models


def raise_flag(
    conn: Connection,
    invoice_id: uuid.UUID,
    *,
    code: str,
    severity: str,
    raised_by: str,
    detail: dict[str, Any] | None = None,
) -> int:
    stmt = (
        models.invoice_flags.insert()
        .values(
            invoice_id=invoice_id,
            code=code,
            severity=severity,
            raised_by=raised_by,
            detail=detail or {},
        )
        .returning(models.invoice_flags.c.id)
    )
    return int(conn.execute(stmt).scalar_one())


def open_flags_for(conn: Connection, invoice_id: uuid.UUID) -> list[dict[str, Any]]:
    stmt = select(models.invoice_flags).where(
        models.invoice_flags.c.invoice_id == invoice_id,
        models.invoice_flags.c.resolved_at.is_(None),
    )
    return [dict(r) for r in conn.execute(stmt).mappings()]


def resolve_flag(conn: Connection, flag_id: int) -> None:
    conn.execute(
        models.invoice_flags.update()
        .where(models.invoice_flags.c.id == flag_id)
        .values(resolved_at=func.now())
    )
```

Generate the migration (`--autogenerate -m "m2a_05_flags"`) and append:

```python
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.invoice_flags TO invoice_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO invoice_app")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `make migrate && .venv/bin/python -m pytest tests/test_schema_constraints.py -v`
Expected: PASS. Mutation: drop `ck_flags_raised_by` → `test_flag_source_must_be_deterministic` red. Restore.

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/db/ tests/ migrations/versions/
git commit -m "feat(db): invoice_flags with deterministic-source CHECK

Principle 1 ('the LLM never raises a flag') stops being a convention and
becomes DDL: adding an agent as a flag source needs a migration."
```

---

### Task 6: `approvals`

Built before `journal_entries` because `journal_entries.approval_id` is an FK to it.

**Files:**
- Modify: `src/invoice_agent/db/models.py`, `tests/test_repositories.py`
- Create: `src/invoice_agent/db/repositories/approvals.py`
- Create: `migrations/versions/<rev>_m2a_06_approvals.py`

**Interfaces:**
- Consumes: Task 4's `invoices`
- Produces: table `approvals`; `repositories.approvals.{open_approval, record_decision, list_pending, get_approval}`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_repositories.py`:

```python
def test_approval_latency_is_computed_by_the_database(runtime_conn):
    """Design doc §2.8 calls time-to-approve a free metric. Verified
    2026-07-28 that the GENERATED column produces it — a row 90s apart
    yields latency 90 — so no application code computes it and it cannot
    drift from requested_at/decided_at."""
    import time

    from invoice_agent.db.repositories import approvals, vendors

    vendors.ensure_vendor(runtime_conn, "0100000003", name="NCC")
    invoice_id = invoice_repo.insert_row(
        runtime_conn, _invoice_values(invoice_number=f"AP{uuid.uuid4().hex[:6]}")
    )
    approval_id = approvals.open_approval(
        runtime_conn,
        invoice_id=invoice_id,
        thread_id=f"appr:{invoice_id}",
        interrupt_payload={"q": "duyệt?"},
        flags_snapshot={},
        suggestions={},
    )
    assert approvals.get_approval(runtime_conn, approval_id)["latency_seconds"] is None
    assert any(r["id"] == approval_id for r in approvals.list_pending(runtime_conn))

    time.sleep(1)
    approvals.record_decision(
        runtime_conn, approval_id, decision="approved", decided_by="human:test"
    )
    row = approvals.get_approval(runtime_conn, approval_id)
    assert row["latency_seconds"] >= 1
    assert not any(r["id"] == approval_id for r in approvals.list_pending(runtime_conn))


def test_approval_decision_is_constrained(runtime_conn):
    from sqlalchemy.exc import IntegrityError

    from invoice_agent.db.repositories import approvals, vendors

    vendors.ensure_vendor(runtime_conn, "0100000003", name="NCC")
    invoice_id = invoice_repo.insert_row(
        runtime_conn, _invoice_values(invoice_number=f"AD{uuid.uuid4().hex[:6]}")
    )
    approval_id = approvals.open_approval(
        runtime_conn,
        invoice_id=invoice_id,
        thread_id="t",
        interrupt_payload={},
        flags_snapshot={},
        suggestions={},
    )
    with pytest.raises(IntegrityError):
        approvals.record_decision(
            runtime_conn, approval_id, decision="maybe", decided_by="human:test"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_repositories.py -k approval -v`
Expected: FAIL — `ImportError: cannot import name 'approvals'`

- [ ] **Step 3: Write minimal implementation**

Add to `db/models.py`:

```python
APPROVAL_DECISIONS = ("approved", "rejected", "edited")

approvals = Table(
    "approvals",
    metadata,
    Column("id", sa.Uuid(as_uuid=True), primary_key=True),
    Column("invoice_id", sa.Uuid(as_uuid=True), sa.ForeignKey("invoices.id"), nullable=False),
    # The approval_graph thread. Convention `appr:{invoice_id}` (design doc §1.3).
    Column("thread_id", sa.Text, nullable=False),
    Column("interrupt_payload", postgresql.JSONB, nullable=False, server_default="{}"),
    Column("flags_snapshot", postgresql.JSONB, nullable=False, server_default="{}"),
    Column("suggestions", postgresql.JSONB, nullable=False, server_default="{}"),
    Column("decision", sa.Text),
    Column("field_corrections", postgresql.JSONB),
    Column("reason", sa.Text),
    Column("decided_by", sa.Text),
    Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("decided_at", sa.DateTime(timezone=True)),
    # Time-to-approve, computed by Postgres. Verified 2026-07-28: a row 90s
    # apart yields 90. Application code never computes it, so it cannot drift.
    Column(
        "latency_seconds",
        sa.Numeric,
        sa.Computed("EXTRACT(EPOCH FROM (decided_at - requested_at))", persisted=True),
    ),
    sa.CheckConstraint(
        "decision IS NULL OR decision IN " + str(APPROVAL_DECISIONS),
        name="ck_approvals_decision",
    ),
    sa.Index("ix_approvals_thread_id", "thread_id"),
    # Exactly the query scripts/recover_pending.py runs at M5.5.
    sa.Index(
        "ix_approvals_pending",
        "invoice_id",
        postgresql_where=sa.text("decision IS NULL"),
    ),
)
```

`repositories/approvals.py`:

```python
"""Approval records (design doc §2.8).

`list_pending` is what `scripts/recover_pending.py` (M5.5) scans after a
restart: the API must never assume an in-RAM registry survived.
"""

import uuid
from typing import Any

from sqlalchemy import Connection, func, select

from invoice_agent.db import models


def open_approval(
    conn: Connection,
    *,
    invoice_id: uuid.UUID,
    thread_id: str,
    interrupt_payload: dict[str, Any],
    flags_snapshot: dict[str, Any],
    suggestions: dict[str, Any],
) -> uuid.UUID:
    approval_id = uuid.uuid4()
    conn.execute(
        models.approvals.insert().values(
            id=approval_id,
            invoice_id=invoice_id,
            thread_id=thread_id,
            interrupt_payload=interrupt_payload,
            flags_snapshot=flags_snapshot,
            suggestions=suggestions,
        )
    )
    return approval_id


def record_decision(
    conn: Connection,
    approval_id: uuid.UUID,
    *,
    decision: str,
    decided_by: str,
    reason: str | None = None,
    field_corrections: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        models.approvals.update()
        .where(models.approvals.c.id == approval_id)
        .values(
            decision=decision,
            decided_by=decided_by,
            reason=reason,
            field_corrections=field_corrections,
            decided_at=func.now(),
        )
    )


def list_pending(conn: Connection) -> list[dict[str, Any]]:
    stmt = select(models.approvals).where(models.approvals.c.decision.is_(None))
    return [dict(r) for r in conn.execute(stmt).mappings()]


def get_approval(conn: Connection, approval_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        conn.execute(select(models.approvals).where(models.approvals.c.id == approval_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None
```

Generate the migration (`--autogenerate -m "m2a_06_approvals"`) and append:

```python
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.approvals TO invoice_app")
```

> Autogenerate renders `sa.Computed` correctly, but **check the generated file**: the column must appear with `sa.Computed(..., persisted=True)` and no separate `server_default`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `make migrate && .venv/bin/python -m pytest tests/test_repositories.py -k approval -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/db/ tests/ migrations/versions/
git commit -m "feat(db): approvals with database-computed latency_seconds"
```

---

### Task 7: `journal_entries` + `journal_entry_lines` — the safety property

The most important task in the milestone. Read spec §4.4 and §6.1 first.

**Files:**
- Modify: `src/invoice_agent/db/models.py`, `tests/test_schema_constraints.py`
- Create: `src/invoice_agent/db/repositories/journal.py`
- Create: `migrations/versions/<rev>_m2a_07_journal.py`

**Interfaces:**
- Consumes: Tasks 4, 5, 6
- Produces: tables `journal_entries`, `journal_entry_lines`; `repositories.journal.{JournalLine, create_entry_with_lines, get_for_invoice, get_lines}`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_schema_constraints.py`:

```python
from invoice_agent.db.repositories import approvals, flags, journal, vendors
from invoice_agent.db.repositories.journal import JournalLine

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
                JournalLine("331", "Phải trả người bán", Decimal("-100"), Decimal("0"), None),
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
```

Also add the ordering-risk guard from spec §6.1, in `tests/test_schema_constraints.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_schema_constraints.py -v`
Expected: FAIL — `ImportError: cannot import name 'journal'`

- [ ] **Step 3: Write minimal implementation**

Add to `db/models.py`:

```python
JOURNAL_STATUSES = ("draft", "posted", "void")
POSTING_AUTHORITIES = ("auto_clean", "human_approved")

journal_entries = Table(
    "journal_entries",
    metadata,
    Column("id", sa.Uuid(as_uuid=True), primary_key=True),
    # UNIQUE: one invoice can never be posted twice. Measured at M1.5 that a
    # `kill -9` inside a post-gate node makes it run again on resume and write
    # a SECOND row — graph shape does not prevent that, this constraint does.
    Column(
        "invoice_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("invoices.id"),
        nullable=False,
        unique=True,
    ),
    Column("entry_date", sa.Date, nullable=False),
    Column("status", sa.Text, nullable=False, server_default="draft"),
    Column("posting_authority", sa.Text, nullable=False),
    Column("approval_id", sa.Uuid(as_uuid=True), sa.ForeignKey("approvals.id")),
    Column("posted_at", sa.DateTime(timezone=True)),
    Column("posted_by", sa.Text),
    Column("total_debit", sa.Numeric, nullable=False),
    Column("total_credit", sa.Numeric, nullable=False),
    Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    sa.CheckConstraint("total_debit = total_credit", name="ck_journal_balanced"),
    # Without this, 'human_approved' is just a string the application writes.
    sa.CheckConstraint(
        "posting_authority <> 'human_approved' OR approval_id IS NOT NULL",
        name="ck_journal_human_approved_needs_approval",
    ),
    sa.CheckConstraint("status IN " + str(JOURNAL_STATUSES), name="ck_journal_status"),
    sa.CheckConstraint(
        "posting_authority IN " + str(POSTING_AUTHORITIES), name="ck_journal_authority"
    ),
)

journal_entry_lines = Table(
    "journal_entry_lines",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column(
        "journal_entry_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("journal_entries.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("line_no", sa.Integer, nullable=False),
    Column("account_code", sa.Text, nullable=False),
    Column("account_name", sa.Text, nullable=False),
    Column("debit", sa.Numeric, nullable=False, server_default="0"),
    Column("credit", sa.Numeric, nullable=False, server_default="0"),
    Column("description", sa.Text),
    sa.UniqueConstraint("journal_entry_id", "line_no", name="uq_journal_lines_entry_line_no"),
    sa.CheckConstraint("debit = 0 OR credit = 0", name="ck_journal_line_one_side"),
    # Beyond the design doc, and load-bearing: debit=-100/credit=0 satisfies the
    # constraint above, so without this a "balanced" entry can be built entirely
    # from negative amounts.
    sa.CheckConstraint("debit >= 0 AND credit >= 0", name="ck_journal_line_non_negative"),
)
```

`repositories/journal.py`:

```python
"""Journal entries — the accounting ledger (design doc §2.7).

⚠️ ORDERING RISK, stated rather than assumed (spec §6.1). This module can
create a journal entry, and its CODE-layer guard — `tools/guards.py`, which
asserts `decision == "approved"` and re-reads Postgres to confirm the approvals
row persisted — is item 5.3 and does not exist yet. Until it lands, the
protections are the DB constraints plus the fact that nothing calls this;
`test_no_production_caller_of_journal_repository` asserts the second one.

Do not add a production caller before M5.3.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, select

from invoice_agent.db import models


@dataclass(frozen=True)
class JournalLine:
    account_code: str
    account_name: str
    debit: Decimal
    credit: Decimal
    description: str | None = None


def create_entry_with_lines(
    conn: Connection,
    *,
    invoice_id: uuid.UUID,
    entry_date: date,
    posting_authority: str,
    lines: list[JournalLine],
    approval_id: uuid.UUID | None = None,
    posted_by: str | None = None,
    status: str = "draft",
) -> uuid.UUID:
    """Totals are SUMMED HERE, never passed in.

    A caller that supplied its own totals could satisfy
    `CHECK (total_debit = total_credit)` with numbers unrelated to the lines,
    which would turn the balance guard into decoration.
    """
    entry_id = uuid.uuid4()
    conn.execute(
        models.journal_entries.insert().values(
            id=entry_id,
            invoice_id=invoice_id,
            entry_date=entry_date,
            posting_authority=posting_authority,
            approval_id=approval_id,
            posted_by=posted_by,
            status=status,
            total_debit=sum((line.debit for line in lines), Decimal("0")),
            total_credit=sum((line.credit for line in lines), Decimal("0")),
        )
    )
    conn.execute(
        models.journal_entry_lines.insert(),
        [
            {
                "journal_entry_id": entry_id,
                "line_no": position,
                "account_code": line.account_code,
                "account_name": line.account_name,
                "debit": line.debit,
                "credit": line.credit,
                "description": line.description,
            }
            for position, line in enumerate(lines, start=1)
        ],
    )
    return entry_id


def get_for_invoice(conn: Connection, invoice_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        conn.execute(
            select(models.journal_entries).where(models.journal_entries.c.invoice_id == invoice_id)
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def get_lines(conn: Connection, entry_id: uuid.UUID) -> list[dict[str, Any]]:
    stmt = (
        select(models.journal_entry_lines)
        .where(models.journal_entry_lines.c.journal_entry_id == entry_id)
        .order_by(models.journal_entry_lines.c.line_no)
    )
    return [dict(r) for r in conn.execute(stmt).mappings()]
```

Generate the migration (`--autogenerate -m "m2a_07_journal"`), then **hand-add the trigger** (autogenerate never emits triggers):

```python
op.execute("""
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
""")
op.execute("""
    CREATE TRIGGER trg_forbid_auto_clean_with_open_flags
    BEFORE INSERT OR UPDATE ON app.journal_entries
    FOR EACH ROW EXECUTE FUNCTION app.forbid_auto_clean_with_open_flags();
""")
op.execute(
    "GRANT SELECT, INSERT, UPDATE ON app.journal_entries, app.journal_entry_lines TO invoice_app"
)
op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO invoice_app")
```

`downgrade()` must drop the trigger then the function, before dropping the tables.

> The exception message contains `open flag`, which `test_auto_clean_blocked_while_flags_open` matches on. Do not reword it without updating the test — a bare `pytest.raises(IntegrityError)` would be satisfied by *any* integrity error, which is exactly how M1's entity guard passed while guarding nothing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `make migrate && .venv/bin/python -m pytest tests/test_schema_constraints.py -v`
Expected: PASS

Then run all five mutations, restoring after each:

| Mutation | Expect red |
|---|---|
| Drop `ck_journal_balanced` | `test_unbalanced_journal_entry_rejected` |
| Drop `ck_journal_human_approved_needs_approval` | `test_human_approved_requires_an_approval_row` |
| Drop `ck_journal_line_one_side` | `test_line_cannot_be_both_debit_and_credit` |
| Drop `ck_journal_line_non_negative` | `test_negative_amounts_rejected` |
| Drop `trg_forbid_auto_clean_with_open_flags` | `test_auto_clean_blocked_while_flags_open` |

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/db/ tests/ migrations/versions/
git commit -m "feat(db): journal entries — balance CHECK, authority CHECK, open-flag trigger

Two of the safety property's three layers. The code layer (tools/guards.py)
is M5.3; until then a test asserts the repository has no production caller."
```

---

### Task 8: `audit_log` — append-only by two mechanisms

**Files:**
- Modify: `src/invoice_agent/db/models.py`, `tests/test_schema_constraints.py`
- Create: `src/invoice_agent/db/audit.py`
- Create: `migrations/versions/<rev>_m2a_08_audit_log.py`

**Interfaces:**
- Produces: table `audit_log`; `db.audit.record`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_schema_constraints.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_schema_constraints.py -k audit -v`
Expected: FAIL — `ImportError: cannot import name 'audit'`

- [ ] **Step 3: Write minimal implementation**

Add to `db/models.py`:

```python
audit_log = Table(
    "audit_log",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    # 'system' | 'human:<name>' | 'mcp:<client>' (M7.2 writes the third form).
    Column("actor", sa.Text, nullable=False),
    Column("action", sa.Text, nullable=False),
    Column("target_table", sa.Text),
    Column("target_id", sa.Text),
    # Grants AND refusals. A refusal-only log cannot distinguish "nothing
    # happened" from "the writer was never called".
    Column("allowed", sa.Boolean, nullable=False),
    Column("reason", sa.Text),
    Column("detail", postgresql.JSONB, nullable=False, server_default="{}"),
)
```

`src/invoice_agent/db/audit.py`:

```python
"""Append-only audit trail (design doc §2.8).

Append-only is enforced by TWO independent mechanisms, because measurement
2026-07-28 showed one is not enough:

1. PRIVILEGE — `invoice_app` holds only SELECT, INSERT. UPDATE and DELETE are
   revoked.
2. TRIGGER — a BEFORE UPDATE OR DELETE trigger that raises. This one also binds
   the table owner and any superuser, i.e. `invoice`, which the Docker image
   creates as a SUPERUSER. Privilege checks alone are bypassed by superusers.

There is deliberately no `update` or `delete` function in this module.
"""

from typing import Any

from sqlalchemy import Connection

from invoice_agent.db import models


def record(
    conn: Connection,
    *,
    actor: str,
    action: str,
    allowed: bool,
    target_table: str | None = None,
    target_id: str | None = None,
    reason: str | None = None,
    detail: dict[str, Any] | None = None,
) -> int:
    stmt = (
        models.audit_log.insert()
        .values(
            actor=actor,
            action=action,
            allowed=allowed,
            target_table=target_table,
            target_id=target_id,
            reason=reason,
            detail=detail or {},
        )
        .returning(models.audit_log.c.id)
    )
    return int(conn.execute(stmt).scalar_one())
```

Generate the migration (`--autogenerate -m "m2a_08_audit_log"`) and hand-add:

```python
    op.execute("""
        CREATE OR REPLACE FUNCTION app.forbid_audit_log_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_log_append_only
        BEFORE UPDATE OR DELETE ON app.audit_log
        FOR EACH ROW EXECUTE FUNCTION app.forbid_audit_log_mutation();
    """)
    # Layer 1: privilege. INSERT and SELECT only — no UPDATE, no DELETE.
    op.execute("GRANT SELECT, INSERT ON app.audit_log TO invoice_app")
    op.execute("REVOKE UPDATE, DELETE ON app.audit_log FROM invoice_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE app.audit_log_id_seq TO invoice_app")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `make migrate && .venv/bin/python -m pytest tests/test_schema_constraints.py -k audit -v`
Expected: PASS (4 tests)

Two mutations, each with the other mechanism left in place:

| Mutation | Expect red |
|---|---|
| `GRANT UPDATE ON app.audit_log TO invoice_app` | `test_audit_log_update_denied_to_runtime_role` |
| `DROP TRIGGER trg_audit_log_append_only ON app.audit_log` | `test_audit_log_immutable_even_as_owner` |

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/db/ tests/ migrations/versions/
git commit -m "feat(db): append-only audit_log via privilege AND trigger

Measured 2026-07-28: REVOKE alone is bypassed by the superuser the Docker
image creates, so the trigger carries the property for owner sessions."
```

---

### Task 9: `budget_consumption`, `episodic_decisions`, `llm_calls`

The three remaining tables, each with a straightforward repository.

**Files:**
- Modify: `src/invoice_agent/db/models.py`, `tests/test_repositories.py`
- Create: `src/invoice_agent/db/repositories/budget_consumption.py`, `.../episodic.py`, `.../llm_calls.py`
- Create: `migrations/versions/<rev>_m2a_09_consumption_episodic_llm.py`

**Interfaces:**
- Produces: tables `budget_consumption`, `episodic_decisions`, `llm_calls`; `repositories.budget_consumption.{record_consumption, consumption_for_budget}`, `repositories.episodic.{record_episode, similar_episodes}`, `repositories.llm_calls.record_call`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_repositories.py`:

```python
def test_budget_consumption_is_unique_per_budget_and_invoice(runtime_conn):
    """Design doc §2.6. Without this UNIQUE, a retried batch double-counts an
    invoice against a budget and `test_budget_serialization.py` (M4) becomes
    unprovable."""
    from sqlalchemy.exc import IntegrityError

    from invoice_agent.db.repositories import (
        budget_consumption,
        reference,
        vendors,
    )

    reference.upsert_department(runtime_conn, "MKT", "Phòng Marketing")
    budget_id = reference.upsert_budget(runtime_conn, "MKT", "2026-07", None, Decimal("50000000"))
    vendors.ensure_vendor(runtime_conn, "0100000003", name="NCC")
    invoice_id = invoice_repo.insert_row(
        runtime_conn, _invoice_values(invoice_number=f"BC{uuid.uuid4().hex[:6]}")
    )
    budget_consumption.record_consumption(
        runtime_conn,
        budget_id=budget_id,
        invoice_id=invoice_id,
        amount=Decimal("1000"),
        balance_before=Decimal("50000000"),
        balance_after=Decimal("49999000"),
        sequence_no=1,
    )
    with pytest.raises(IntegrityError):
        budget_consumption.record_consumption(
            runtime_conn,
            budget_id=budget_id,
            invoice_id=invoice_id,
            amount=Decimal("1000"),
            balance_before=Decimal("49999000"),
            balance_after=Decimal("49998000"),
            sequence_no=2,
        )


def test_llm_calls_records_failures_too(runtime_conn):
    """Design doc §2.9: a 429 still burned input tokens. Omitting failed calls
    makes the cost number wrong, and cost honesty is a portfolio claim."""
    from invoice_agent.db.repositories import llm_calls

    row_id = llm_calls.record_call(
        runtime_conn,
        model="groq/llama-3.3-70b-versatile",
        provider="groq",
        node="classify",
        prompt_tokens=800,
        completion_tokens=0,
        cost_usd=Decimal("0"),
        shadow_cost_usd=Decimal("0.00012"),
        latency_ms=340,
        attempt=1,
        succeeded=False,
        error="429 rate limit",
    )
    assert row_id is not None


def test_episodic_similar_uses_the_sql_channel(runtime_conn):
    """Design doc §7.1: episodic recall is SQL FIRST — same vendor OR same
    category, ordered by flag overlap and recency. pgvector is M7 and only if
    there is time, which is why there is no embedding column yet."""
    from invoice_agent.db.repositories import episodic, vendors

    vendors.ensure_vendor(runtime_conn, "0100000003", name="NCC")
    invoice_id = invoice_repo.insert_row(
        runtime_conn, _invoice_values(invoice_number=f"EP{uuid.uuid4().hex[:6]}")
    )
    episodic.record_episode(
        runtime_conn,
        approval_id=None,
        invoice_id=invoice_id,
        vendor_tax_code="0100000003",
        category="MARKETING",
        department="MKT",
        amount=Decimal("5000000"),
        flags={"BUDGET_EXCEEDED": True},
        decision="approved",
        reason="Giám đốc đã duyệt ngoài hệ thống",
    )
    hits = episodic.similar_episodes(
        runtime_conn, vendor_tax_code="0100000003", category=None, limit=5
    )
    assert any(h["invoice_id"] == invoice_id for h in hits)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_repositories.py -k "budget_consumption or llm_calls or episodic" -v`
Expected: FAIL — `ImportError: cannot import name 'budget_consumption'`

- [ ] **Step 3: Write minimal implementation**

Add to `db/models.py`:

```python
budget_consumption = Table(
    "budget_consumption",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column("budget_id", sa.BigInteger, sa.ForeignKey("budgets.id"), nullable=False),
    Column("invoice_id", sa.Uuid(as_uuid=True), sa.ForeignKey("invoices.id"), nullable=False),
    Column("amount", sa.Numeric, nullable=False),
    # balance_before/after/sequence_no are what turn "I serialized budget
    # reconciliation" from a claim into a proof: M4's test shuffles arrival
    # order and asserts the balance_after chain is byte-identical.
    Column("balance_before", sa.Numeric, nullable=False),
    Column("balance_after", sa.Numeric, nullable=False),
    Column("sequence_no", sa.BigInteger, nullable=False),
    Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    sa.UniqueConstraint("budget_id", "invoice_id", name="uq_budget_consumption_budget_invoice"),
)

episodic_decisions = Table(
    "episodic_decisions",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column("approval_id", sa.Uuid(as_uuid=True), sa.ForeignKey("approvals.id")),
    Column("invoice_id", sa.Uuid(as_uuid=True), sa.ForeignKey("invoices.id"), nullable=False),
    Column("vendor_tax_code", sa.Text),
    Column("category", sa.Text),
    Column("department", sa.Text),
    Column("amount", sa.Numeric),
    Column("flags", postgresql.JSONB, nullable=False, server_default="{}"),
    Column("decision", sa.Text, nullable=False),
    Column("reason", sa.Text),
    Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    # NO reason_embedding vector(1024) yet. pgvector is M7-only-if-time (items
    # 4.4, 7.6), and a column present in the DB but absent from this metadata
    # makes --autogenerate emit a DROP — the M1.6 trap. M7 adds one column.
    sa.Index("ix_episodic_vendor", "vendor_tax_code"),
    sa.Index("ix_episodic_category", "category"),
)

llm_calls = Table(
    "llm_calls",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column("invoice_id", sa.Uuid(as_uuid=True), sa.ForeignKey("invoices.id")),
    Column("batch_id", sa.Uuid(as_uuid=True), sa.ForeignKey("batches.id")),
    Column("thread_id", sa.Text),
    Column("node", sa.Text),
    Column("agent", sa.Text),
    Column("model", sa.Text, nullable=False),
    Column("provider", sa.Text),
    Column("prompt_tokens", sa.Integer),
    Column("completion_tokens", sa.Integer),
    Column("cost_usd", sa.Numeric),
    # Groq free tier reports cost_usd = 0, which reads as "you did not measure"
    # rather than "it was cheap". Shadow pricing every call at gpt-4o-mini rates
    # is what makes the cost claim legible (design doc §2.9).
    Column("shadow_cost_usd", sa.Numeric),
    Column("latency_ms", sa.Integer),
    Column("attempt", sa.Integer, nullable=False, server_default="1"),
    # Failed calls are recorded too: a 429 still burned input tokens.
    Column("succeeded", sa.Boolean, nullable=False),
    Column("error", sa.Text),
    Column("trace_id", sa.Text),
    Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
)
```

`repositories/budget_consumption.py`:

```python
"""Budget consumption ledger (design doc §2.6).

Every row carries balance_before, balance_after and sequence_no. Without them
"budget reconciliation is serial" is a sentence; with them M4's
test_budget_serialization.py can shuffle arrival order and assert the
balance_after chain is byte-identical and the SAME invoice overruns.
"""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, select

from invoice_agent.db import models


def record_consumption(
    conn: Connection,
    *,
    budget_id: int,
    invoice_id: uuid.UUID,
    amount: Decimal,
    balance_before: Decimal,
    balance_after: Decimal,
    sequence_no: int,
) -> int:
    stmt = (
        models.budget_consumption.insert()
        .values(
            budget_id=budget_id,
            invoice_id=invoice_id,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            sequence_no=sequence_no,
        )
        .returning(models.budget_consumption.c.id)
    )
    return int(conn.execute(stmt).scalar_one())


def consumption_for_budget(conn: Connection, budget_id: int) -> list[dict[str, Any]]:
    stmt = (
        select(models.budget_consumption)
        .where(models.budget_consumption.c.budget_id == budget_id)
        .order_by(models.budget_consumption.c.sequence_no)
    )
    return [dict(r) for r in conn.execute(stmt).mappings()]
```

`repositories/episodic.py`:

```python
"""Episodic memory — SQL channel (design doc §7.1).

Recall is SQL first, not vector first: "same vendor OR same category" with
recency ordering answers the question a reviewer actually asks, and it needs no
embedding model in the hot path. pgvector arrives at M7 only if there is time,
which is why there is no embedding column.
"""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, or_, select

from invoice_agent.db import models


def record_episode(
    conn: Connection,
    *,
    invoice_id: uuid.UUID,
    decision: str,
    approval_id: uuid.UUID | None = None,
    vendor_tax_code: str | None = None,
    category: str | None = None,
    department: str | None = None,
    amount: Decimal | None = None,
    flags: dict[str, Any] | None = None,
    reason: str | None = None,
) -> int:
    stmt = (
        models.episodic_decisions.insert()
        .values(
            approval_id=approval_id,
            invoice_id=invoice_id,
            vendor_tax_code=vendor_tax_code,
            category=category,
            department=department,
            amount=amount,
            flags=flags or {},
            decision=decision,
            reason=reason,
        )
        .returning(models.episodic_decisions.c.id)
    )
    return int(conn.execute(stmt).scalar_one())


def similar_episodes(
    conn: Connection,
    *,
    vendor_tax_code: str | None = None,
    category: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    predicates = []
    if vendor_tax_code is not None:
        predicates.append(models.episodic_decisions.c.vendor_tax_code == vendor_tax_code)
    if category is not None:
        predicates.append(models.episodic_decisions.c.category == category)
    if not predicates:
        return []
    stmt = (
        select(models.episodic_decisions)
        .where(or_(*predicates))
        .order_by(models.episodic_decisions.c.created_at.desc())
        .limit(limit)
    )
    return [dict(r) for r in conn.execute(stmt).mappings()]
```

`repositories/llm_calls.py`:

```python
"""LLM cost tracking (design doc §2.9).

Two things most implementations miss, both deliberate here: FAILED calls are
recorded (a 429 already burned input tokens, so omitting them makes the cost
number wrong), and every call is shadow-priced at gpt-4o-mini rates so "$0 on
Groq free tier" reads as cheap rather than as unmeasured.
"""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection

from invoice_agent.db import models


def record_call(
    conn: Connection,
    *,
    model: str,
    succeeded: bool,
    invoice_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
    thread_id: str | None = None,
    node: str | None = None,
    agent: str | None = None,
    provider: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cost_usd: Decimal | None = None,
    shadow_cost_usd: Decimal | None = None,
    latency_ms: int | None = None,
    attempt: int = 1,
    error: str | None = None,
    trace_id: str | None = None,
) -> int:
    values: dict[str, Any] = {
        "model": model,
        "succeeded": succeeded,
        "invoice_id": invoice_id,
        "batch_id": batch_id,
        "thread_id": thread_id,
        "node": node,
        "agent": agent,
        "provider": provider,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": cost_usd,
        "shadow_cost_usd": shadow_cost_usd,
        "latency_ms": latency_ms,
        "attempt": attempt,
        "error": error,
        "trace_id": trace_id,
    }
    stmt = models.llm_calls.insert().values(**values).returning(models.llm_calls.c.id)
    return int(conn.execute(stmt).scalar_one())
```

Generate the migration (`--autogenerate -m "m2a_09_consumption_episodic_llm"`) and append:

```python
op.execute(
    "GRANT SELECT, INSERT, UPDATE ON app.budget_consumption, app.episodic_decisions, app.llm_calls TO invoice_app"
)
op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO invoice_app")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `make migrate && .venv/bin/python -m pytest tests/test_repositories.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/db/ tests/ migrations/versions/
git commit -m "feat(db): budget_consumption, episodic_decisions, llm_calls

Completes the 15 tables. episodic_decisions deliberately has no
reason_embedding: pgvector is M7-only-if-time, and a column absent from
metadata makes autogenerate emit a DROP."
```

---

### Task 10: Full verification and the gate artefact

No new behavior. This task proves the milestone slice is closed and produces the portfolio evidence.

**Files:**
- Create: `docs/notes/2026-07-28-m2a-database-defends-itself.md`
- Modify: `docs/Plan_Project_F_2026-07-25.md` (§7 status table)

- [ ] **Step 1: Verify autogenerate is still empty**

The single most likely regression: 15 new tables in metadata must produce **no** diff, and the M1.6 guard must stay green.

```bash
make migrate
.venv/bin/python -m pytest tests/test_alembic_ignores_langgraph.py -v
rm -rf /tmp/probe_versions && mkdir -p /tmp/probe_versions
.venv/bin/alembic revision --autogenerate -m "probe" --version-path /tmp/probe_versions
cat /tmp/probe_versions/*.py
```
Expected: `upgrade()` body is `pass`. If it contains any operation, the metadata and the migrations have diverged — fix the migration, do not edit the probe. Delete the probe file afterwards.

- [ ] **Step 2: Verify from a genuinely empty volume**

Everything so far ran against an incrementally migrated database. The gate says "from an empty volume", and the M0 CI hole (schemas created, migrations never run) is exactly what that catches.

```bash
make db-reset && make seed && make check
```
Expected: all green, 123 baseline tests plus this milestone's.

- [ ] **Step 3: Re-verify all 11 mutations in one pass**

M1 closed on the *claim* that guards were mutation-verified and the exit review found otherwise. Re-break each guard from scratch and record the result:

| # | Mutation | Guard |
|---|---|---|
| 1 | Drop `ck_journal_balanced` | `test_unbalanced_journal_entry_rejected` |
| 2 | Drop `ck_journal_human_approved_needs_approval` | `test_human_approved_requires_an_approval_row` |
| 3 | Drop `ck_journal_line_one_side` | `test_line_cannot_be_both_debit_and_credit` |
| 4 | Drop `ck_journal_line_non_negative` | `test_negative_amounts_rejected` |
| 5 | Drop `trg_forbid_auto_clean_with_open_flags` | `test_auto_clean_blocked_while_flags_open` |
| 6 | Drop `trg_audit_log_append_only` | `test_audit_log_immutable_even_as_owner` |
| 7 | `GRANT UPDATE ON audit_log TO invoice_app` | `test_audit_log_update_denied_to_runtime_role` |
| 8 | Remove `postgresql_nulls_not_distinct` on budgets | `test_two_department_wide_budgets_rejected` |
| 9 | `seller_tax_code` → nullable | `test_invoice_requires_seller_tax_code` |
| 10 | Drop `uq_invoices_natural_key` | `test_duplicate_invoice_is_idempotent` |
| 11 | Drop `ck_flags_raised_by` | `test_flag_source_must_be_deterministic` |

Record the true result for each. **If a guard does not go red, say so and fix the guard** — a mutation that fails to turn its test red is the finding, not an inconvenience.

- [ ] **Step 4: Capture the gate artefact**

The M2 artefact is a psql session showing the database refusing bad writes. Capture real output:

```bash
docker exec -i invoice-agent-postgres psql -U invoice -d invoice_agent <<'SQL'
\echo '--- unbalanced journal entry ---'
INSERT INTO app.journal_entries (id, invoice_id, entry_date, posting_authority, total_debit, total_credit)
VALUES (gen_random_uuid(), (SELECT id FROM app.invoices LIMIT 1), '2026-07-01', 'auto_clean', 100, 999);
\echo '--- human_approved with no approval row ---'
INSERT INTO app.journal_entries (id, invoice_id, entry_date, posting_authority, total_debit, total_credit)
VALUES (gen_random_uuid(), (SELECT id FROM app.invoices LIMIT 1), '2026-07-01', 'human_approved', 100, 100);
\echo '--- UPDATE audit_log as the SUPERUSER owner ---'
UPDATE app.audit_log SET reason = 'tampered';
\echo '--- a flag raised by an agent ---'
INSERT INTO app.invoice_flags (invoice_id, code, severity, raised_by)
VALUES ((SELECT id FROM app.invoices LIMIT 1), 'X', 'warn', 'classifier_agent');
SQL
```

Save the transcript to `docs/notes/2026-07-28-m2a-database-defends-itself.md` with a paragraph naming which layer refused each write.

- [ ] **Step 5: Update the plan document and commit**

In `docs/Plan_Project_F_2026-07-25.md` §7, update the M2 row: items 2.4, 2.5, 2.7 done; 2.6 (M2-B) and 2.1–2.3 (M2-C) still open. Record the four design-doc corrections and the true mutation results.

```bash
git add docs/ && git commit -m "docs(m2a): close schema/seed/repositories, 11 guards mutation-verified

make db-reset && make seed && make check green from an empty volume.
Autogenerate emits an empty migration with 15 tables in metadata, so the
M1.6 isolation guard still holds."
```

---

## Self-Review

**Spec coverage.** §2.1 role+trigger → Tasks 1, 8. §2.2 natural key → Task 4. §2.3 TEXT+CHECK → Tasks 3–7. §2.4 generated column → Task 6. §2.5 unconstrained NUMERIC → Global Constraints + Task 4. §4.1 reference → Task 2. §4.2 core → Tasks 3–5. §4.3 memory → Tasks 3, 9. §4.4 safety → Tasks 6–8. §4.5 ops → Task 9. §4.6 statuses → Task 4. §5 config/grants → Task 1 + per-migration grants. §6 repositories, all 15 tables → Tasks 2–9. §6.1 ordering risk → Task 7. §7 seed → Task 2. §8.2 mutation matrix, all 11 → Tasks 2, 4, 5, 7, 8, re-run in Task 10. §8.3 autogenerate regression → Task 10. §9 success criteria → Task 10.

**Gap found and closed:** the spec's §8.1 lists `test_seed_reference.py` checking TT133 numbers; Task 2 covers it. The spec did not name a guard for `journal_entries.invoice_id UNIQUE`, though §4.4 specifies the constraint — `test_one_invoice_cannot_be_posted_twice` was added in Task 7.

**Type consistency.** `get_engine`/`get_owner_engine` (Task 2) used by every later repository. `insert_row` returns `uuid.UUID | None` and is used that way in Tasks 4, 5, 6, 9. `JournalLine` is defined in Task 7 and used only there. `reference.upsert_budget` returns `int`, consumed as `budget_id: int` in Task 9. `flags.raise_flag` returns `int`, consumed by `resolve_flag(flag_id: int)` in Task 7.

**Placeholder scan:** no TBDs. `<rev>` in migration filenames is the Alembic-generated hash, produced by the `alembic revision` command each task specifies — not a placeholder for the implementer to invent.

**One thing the implementer will hit:** `db/models.py` currently imports individual names (`BigInteger, Column, DateTime, MetaData, String, Table, func`). Tasks 2–9 use `sa.` prefixes and `postgresql.` types, so add `import sqlalchemy as sa` and `from sqlalchemy.dialects import postgresql` at the top on the first task that touches the file, and leave the existing `spike_counter` definition exactly as it is.
