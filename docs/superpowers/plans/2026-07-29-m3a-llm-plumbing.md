# M3-A: LLM Plumbing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every LLM call in this repo replayable without an API key and recorded — including the attempts that failed — with both a real and a reference price.

**Architecture:** Three new pure-ish modules under `src/invoice_agent/llm/` plus three surgical changes to the existing `gateway.py`. `cost.py` holds the record dataclass and two price tables; `cache.py` is a filesystem fixture store keyed by a hash of the whole request; `telemetry.py` is the glue that prices a record and writes it through the existing repository. The gateway gains a `calls` out-param and a replay seam placed **inside** `_generate_impl`, below chain resolution.

**Tech Stack:** Python 3.13 · litellm 1.93.0 · SQLAlchemy 2 + Alembic · Postgres 17 · pytest

**Spec:** `docs/superpowers/specs/2026-07-29-m3a-llm-plumbing-design.md` — read §2 (the four measurements) before starting. The design decisions below are downstream of those measurements, not of taste.

## Global Constraints

- **Authoring language is English** — code comments, docstrings, commit messages, docs. Vietnamese stays only in text the *product* emits or ingests. None of this task touches such text.
- **Every non-obvious constant carries a comment with date + measurement + source.** Both price tables are non-obvious constants.
- **`domain/` must not import `db/`; `db/` must not import `llm/`.** `telemetry.py` is the glue that may import both.
- **An agent response model must never carry approval/flag/status fields.** Nothing in this plan defines a response model; do not add one.
- **Do not bump `langgraph==1.2.9` or `langgraph-checkpoint-postgres==3.1.0`.** No dependency changes at all in M3-A.
- **`make check` runs ruff + mypy + pytest** over `src`, `tests`, `migrations`, `scripts`. It must be green before any task is called done.
- **A guard is worth nothing until it has been seen red.** Task 7 re-breaks every guard from scratch; do not skip it on the grounds that a test "obviously" works.
- Postgres tests need `make dev` first. Tests that need it take the `runtime_conn` / `owner_conn` / `requires_postgres` fixtures from `tests/conftest.py`, which skip cleanly when it is not running.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/invoice_agent/llm/cost.py` | **Create.** `LLMCallRecord` + `ACTUAL_PRICES` / shadow price + two pricing functions. Pure, no I/O, no litellm import. |
| `src/invoice_agent/llm/cache.py` | **Create.** Request→key hashing, fixture load/save, `LLMReplayMiss`. Filesystem only; knows nothing about litellm or the gateway. |
| `src/invoice_agent/llm/telemetry.py` | **Create.** Prices a `Sequence[LLMCallRecord]` and writes rows. The only module importing both `llm.cost` and `db.repositories`. |
| `src/invoice_agent/llm/gateway.py` | **Modify.** `calls` out-param; `_complete` seam; replay bypass in `_resolve_chain`. |
| `src/invoice_agent/db/models.py` | **Modify.** Five columns on the `llm_calls` table object. |
| `src/invoice_agent/db/repositories/llm_calls.py` | **Modify.** Five keyword-only params, all defaulting to `None`. |
| `migrations/versions/<rev>_m3a_10_llm_calls_context.py` | **Create** via `alembic revision`. |
| `tests/test_llm_cost.py` | **Create.** Pricing + drift guards. No DB, no network. |
| `tests/test_llm_cache.py` | **Create.** Key identity, round-trip, miss behaviour. |
| `tests/test_llm_telemetry.py` | **Create.** DB round-trip as the runtime role; the gate-facing assertions. |
| `tests/test_gateway.py` | **Modify.** New classes for the out-param and the replay seam. |
| `tests/test_migrations.py` | **Modify.** Column-existence assertion for `llm_calls`. |
| `docs/notes/2026-07-29-m3a-llm-plumbing.md` | **Create** in Task 7. Mutation evidence. |
| `docs/Plan_Project_F_2026-07-25.md` | **Modify** in Task 7. Mark 3.6/3.7 done, record 3.1's move to M7. |

---

### Task 1: Migration 10 — the five missing `llm_calls` columns

Design doc §2.9 specifies `invoice_id, batch_id, thread_id, agent, trace_id`. Migration 09 shipped without them and the M2-A spec claimed the table was "§2.9 verbatim". Nothing caught it because nothing wrote to the table until now.

**Files:**
- Create: `migrations/versions/<generated>_m3a_10_llm_calls_context.py`
- Modify: `src/invoice_agent/db/models.py:417-433`
- Modify: `src/invoice_agent/db/repositories/llm_calls.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `llm_calls.record_call(conn, *, model, provider, node, prompt_tokens, completion_tokens, cost_usd, shadow_cost_usd, latency_ms, attempt=1, succeeded=True, error=None, invoice_id=None, batch_id=None, thread_id=None, agent=None, trace_id=None) -> int`. Task 6 calls exactly this.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_migrations.py` (it already imports `psycopg`):

```python
class TestLlmCallsContextColumns:
    """Design doc §2.9 names the context columns; migration 09 shipped without
    five of them. The M3 gate — 'cost for ONE INVOICE' — is not expressible in
    a table that cannot name an invoice."""

    def test_has_every_column_in_design_2_9(self, requires_postgres):
        with psycopg.connect(requires_postgres) as conn:
            cols = {
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='app' AND table_name='llm_calls'"
                )
            }
        assert {
            "invoice_id", "batch_id", "thread_id", "node", "agent",
            "model", "provider", "prompt_tokens", "completion_tokens",
            "cost_usd", "shadow_cost_usd", "latency_ms", "attempt",
            "succeeded", "error", "trace_id", "occurred_at",
        } <= cols

    def test_context_columns_are_nullable(self, requires_postgres):
        """A call made by scripts/run_one.py before any invoice row exists has
        no invoice_id. NOT NULL here would force callers to invent an identity,
        which is how telemetry tables fill up with sentinel rows."""
        with psycopg.connect(requires_postgres) as conn:
            nullable = dict(
                conn.execute(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_schema='app' AND table_name='llm_calls'"
                ).fetchall()
            )
        for col in ("invoice_id", "batch_id", "thread_id", "agent", "trace_id"):
            assert nullable[col] == "YES", f"{col} must be nullable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_migrations.py::TestLlmCallsContextColumns -v`
Expected: FAIL — the set comparison reports the five missing names. (If it SKIPs, run `make dev` first; a skip is not a failing test.)

- [ ] **Step 3: Generate the migration skeleton**

Run: `.venv/bin/alembic revision -m "m3a_10_llm_calls_context"`

This writes a file with the revision id and `down_revision = "b25d8a939562"` filled in. Do **not** hand-write the revision id.

- [ ] **Step 4: Write the migration body**

Replace the generated `upgrade`/`downgrade` with:

```python
def upgrade() -> None:
    """Design doc §2.9's context columns, missing from migration 09.

    sa.Uuid(), not a literal CHAR(32): invoices.id and batches.id are declared
    sa.Uuid(as_uuid=True), which renders as CHAR(32) on this dialect. Declaring
    the raw type instead makes --autogenerate see a type change and turns the
    M1.6 empty-migration guard red.
    """
    op.add_column("llm_calls", sa.Column("invoice_id", sa.Uuid(), nullable=True), schema="app")
    op.add_column("llm_calls", sa.Column("batch_id", sa.Uuid(), nullable=True), schema="app")
    op.add_column("llm_calls", sa.Column("thread_id", sa.Text(), nullable=True), schema="app")
    op.add_column("llm_calls", sa.Column("agent", sa.Text(), nullable=True), schema="app")
    op.add_column("llm_calls", sa.Column("trace_id", sa.Text(), nullable=True), schema="app")
    op.create_foreign_key(
        "fk_llm_calls_invoice_id", "llm_calls", "invoices",
        ["invoice_id"], ["id"], source_schema="app", referent_schema="app",
    )
    op.create_foreign_key(
        "fk_llm_calls_batch_id", "llm_calls", "batches",
        ["batch_id"], ["id"], source_schema="app", referent_schema="app",
    )
    # No new GRANT: migration 09 granted SELECT, INSERT, UPDATE at TABLE level
    # to invoice_app, and table-level grants cover columns added later.


def downgrade() -> None:
    op.drop_constraint("fk_llm_calls_batch_id", "llm_calls", schema="app", type_="foreignkey")
    op.drop_constraint("fk_llm_calls_invoice_id", "llm_calls", schema="app", type_="foreignkey")
    for col in ("trace_id", "agent", "thread_id", "batch_id", "invoice_id"):
        op.drop_column("llm_calls", col, schema="app")
```

- [ ] **Step 5: Mirror the columns in `db/models.py`**

In the `llm_calls = Table(...)` block (currently `src/invoice_agent/db/models.py:417-433`), add after `Column("node", ...)`:

```python
    # Design doc §2.9 context. All nullable: a call from scripts/run_one.py has
    # no invoice yet, and an eval-harness call has neither invoice nor batch.
    # `trace_id` is written by nothing until M6.5 wires Langfuse — the column
    # ships now because one migration is cheaper than two.
    Column("invoice_id", sa.Uuid(as_uuid=True), sa.ForeignKey("invoices.id")),
    Column("batch_id", sa.Uuid(as_uuid=True), sa.ForeignKey("batches.id")),
    Column("thread_id", sa.Text),
    Column("agent", sa.Text),
    Column("trace_id", sa.Text),
```

- [ ] **Step 6: Extend the repository**

In `src/invoice_agent/db/repositories/llm_calls.py`, add to `record_call`'s keyword-only params, after `error: str | None = None`:

```python
    invoice_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
    thread_id: str | None = None,
    agent: str | None = None,
    trace_id: str | None = None,
```

Add `import uuid` at the top, and add the five to the `.values(...)` call:

```python
            invoice_id=invoice_id,
            batch_id=batch_id,
            thread_id=thread_id,
            agent=agent,
            trace_id=trace_id,
```

All five default to `None` so every existing caller and test keeps working unchanged.

- [ ] **Step 7: Apply and verify**

Run: `.venv/bin/alembic upgrade head && .venv/bin/python -m pytest tests/test_migrations.py -v`
Expected: PASS, including `test_autogenerate_emits_an_empty_migration` — if that one goes red, the model and the migration disagree about a type (almost certainly `sa.Uuid` vs `CHAR`).

- [ ] **Step 8: Commit**

```bash
git add migrations/versions src/invoice_agent/db/models.py \
        src/invoice_agent/db/repositories/llm_calls.py tests/test_migrations.py
git commit -m "fix(m2a): llm_calls was five columns short of design §2.9"
```

---

### Task 2: `llm/cost.py` — two prices, and why they differ

**Files:**
- Create: `src/invoice_agent/llm/cost.py`
- Test: `tests/test_llm_cost.py`

**Interfaces:**
- Consumes: `invoice_agent.config.DEFAULT_MODEL_CHAIN` (for the drift guard only).
- Produces: `LLMCallRecord` (frozen dataclass: `model, provider, prompt_tokens, completion_tokens, latency_ms, attempt, succeeded, error=None`), `actual_cost_usd(record) -> Decimal`, `shadow_cost_usd(record) -> Decimal`, `ACTUAL_PRICES: dict[str, tuple[Decimal, Decimal]]`. Tasks 4, 5 and 6 all import from here.

- [ ] **Step 1: Write the failing test**

Create `tests/test_llm_cost.py`:

```python
"""Pricing — and the reason there are two prices rather than one.

Measured 2026-07-29 (spec §2.2): litellm.completion_cost prices a free-tier
Groq call at $0.000748 and raises on both openrouter :free models. Delegating
to it would make cost_usd and shadow_cost_usd say the same wrong number.
"""

from decimal import Decimal

import pytest

from invoice_agent.config import DEFAULT_MODEL_CHAIN
from invoice_agent.llm.cost import (
    ACTUAL_PRICES,
    LLMCallRecord,
    actual_cost_usd,
    shadow_cost_usd,
)


def make_record(model="groq/llama-3.3-70b-versatile", **overrides) -> LLMCallRecord:
    base = {
        "model": model,
        "provider": model.split("/", 1)[0],
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "latency_ms": 120,
        "attempt": 1,
        "succeeded": True,
    }
    base.update(overrides)
    return LLMCallRecord(**base)


class TestTheTwoPrices:
    def test_free_tier_costs_zero_and_shadow_does_not(self):
        """The whole reason §2.9 has two columns. A bare $0 reads as
        'unmeasured'; $0 beside a non-zero reference price reads as 'cheap'."""
        record = make_record()
        assert actual_cost_usd(record) == Decimal(0)
        assert shadow_cost_usd(record) > Decimal(0)

    def test_shadow_price_is_gpt4o_mini_regardless_of_model(self):
        """1000 in / 200 out at $0.15/$0.60 per 1M = $0.00027 exactly."""
        for model in DEFAULT_MODEL_CHAIN:
            assert shadow_cost_usd(make_record(model)) == Decimal("0.00027")

    def test_gpt4o_mini_actual_equals_shadow(self):
        """The one model we would really pay for. Its two columns coincide,
        which is what makes the shadow column readable as a reference."""
        record = make_record("openai/gpt-4o-mini")
        assert actual_cost_usd(record) == shadow_cost_usd(record) == Decimal("0.00027")

    def test_unmapped_model_returns_zero_instead_of_raising(self):
        """Pricing is telemetry. Telemetry that can kill an invoice run is
        worse than telemetry that under-reports: the under-report is visible in
        the report, a crash loses the invoice."""
        assert actual_cost_usd(make_record("groq/some-model-added-tomorrow")) == Decimal(0)

    def test_prices_are_decimal_not_float(self):
        """The column is Numeric and per-invoice figures live near $0.0005."""
        assert isinstance(shadow_cost_usd(make_record()), Decimal)

    def test_a_failed_call_is_still_priced_on_its_input_tokens(self):
        """§2.9: a 429 burned the input tokens even though it produced nothing."""
        record = make_record("openai/gpt-4o-mini", completion_tokens=0, succeeded=False)
        assert actual_cost_usd(record) == Decimal("0.00015")


class TestDriftGuards:
    """These fail when the WORLD changed, not when this repo did. Failing means
    a decision is owed, not that a bug was introduced."""

    def test_hardcoded_shadow_price_still_matches_litellm(self):
        import litellm
        from litellm.utils import Choices, Message, ModelResponse, Usage

        response = ModelResponse(
            model="gpt-4o-mini",
            choices=[Choices(message=Message(content="x"), finish_reason="stop", index=0)],
            usage=Usage(prompt_tokens=1000, completion_tokens=200, total_tokens=1200),
        )
        response.model = "openai/gpt-4o-mini"
        # Quantize before comparing: measured 2026-07-29, completion_cost
        # returns the FLOAT 0.00026999999999999995, so a bare
        # Decimal(str(...)) comparison fails on binary float error rather than
        # on a real price change — a drift guard that cries wolf gets deleted.
        measured = Decimal(str(litellm.completion_cost(completion_response=response))).quantize(
            Decimal("0.00000001")
        )
        assert measured == shadow_cost_usd(make_record("openai/gpt-4o-mini")), (
            "litellm revised its gpt-4o-mini price. Old shadow_cost_usd rows are "
            "no longer comparable to new ones — decide, do not just re-pin."
        )

    @pytest.mark.parametrize("model", DEFAULT_MODEL_CHAIN)
    def test_every_configured_model_is_priced(self, model):
        """Adding a model to the chain without pricing it would silently record
        $0 for a call that cost money."""
        assert model in ACTUAL_PRICES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_llm_cost.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'invoice_agent.llm.cost'`

- [ ] **Step 3: Write the implementation**

Create `src/invoice_agent/llm/cost.py`:

```python
"""What a call cost, and what it would have cost.

Design doc §2.9 gives `llm_calls` two money columns, and the difference between
them is the point. `cost_usd` is what we actually paid — $0 on every free tier
in the configured chain. `shadow_cost_usd` prices the same tokens at
gpt-4o-mini, so the report can say "$0 in practice on free tier; $0.0005 per
invoice at reference price" instead of a bare $0 that reads as "you did not
measure".

Measured 2026-07-29 (spec §2.2) against litellm 1.93.0: `completion_cost`
prices groq/llama-3.3-70b-versatile at $0.000748 for a call that costs nothing,
and RAISES "isn't mapped yet" on both openrouter :free models. Delegating to it
would collapse the two columns into one wrong number and crash on 2 of 6 models
in the chain. Hence explicit tables.
"""

from dataclasses import dataclass
from decimal import Decimal

_PER_MILLION = Decimal(1_000_000)

# gpt-4o-mini reference price, USD per 1M tokens. Source: roadmap §2.2
# (2026-07-25). Re-measured 2026-07-29 against litellm 1.93.0's own map, which
# agrees to the cent: $0.000270 for 1000 in / 200 out. Kept hardcoded anyway so
# a future revision of that map cannot silently make historical
# shadow_cost_usd rows incomparable to new ones —
# tests/test_llm_cost.py::test_hardcoded_shadow_price_still_matches_litellm
# turns the drift into a failing build instead.
SHADOW_PRICE = (Decimal("0.15"), Decimal("0.60"))

_FREE = (Decimal(0), Decimal(0))

# What each model in DEFAULT_MODEL_CHAIN actually bills this project, USD per
# 1M tokens. Everything but openai/* runs on a free tier (roadmap §2.1: Groq
# free + key rotation, Gemini AI Studio free, OpenRouter :free), so the honest
# figure is zero — not the provider's list price.
ACTUAL_PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "groq/llama-3.3-70b-versatile": _FREE,
    "groq/openai/gpt-oss-120b": _FREE,
    "gemini/gemini-3.5-flash": _FREE,
    "openrouter/qwen/qwen3-next-80b-a3b-instruct:free": _FREE,
    "openrouter/meta-llama/llama-3.3-70b-instruct:free": _FREE,
    # The only model we would really pay for. Its actual price is deliberately
    # the same pair as SHADOW_PRICE — that identity is asserted by a test, and
    # it is what makes the shadow column legible as a reference rather than an
    # unrelated third number.
    "openai/gpt-4o-mini": SHADOW_PRICE,
}


@dataclass(frozen=True)
class LLMCallRecord:
    """One ATTEMPT against one model — not one logical call.

    Frozen for the same reason `domain.findings.Finding` is: a record of what
    happened must not be editable after the fact.

    This is the `LLMCallRecord` design doc §3.1 names in `EnrichmentState`,
    where it accumulates under `Annotated[list[...], operator.add]`. A single
    `generate()` can produce several of these — two rate-limited keys then a
    success is three records — and §2.9 requires all three, because the two
    failures burned input tokens.
    """

    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    attempt: int
    succeeded: bool
    error: str | None = None


def _price(rates: tuple[Decimal, Decimal], record: LLMCallRecord) -> Decimal:
    input_rate, output_rate = rates
    return (
        Decimal(record.prompt_tokens) * input_rate
        + Decimal(record.completion_tokens) * output_rate
    ) / _PER_MILLION


def actual_cost_usd(record: LLMCallRecord) -> Decimal:
    """What we paid. Unknown models price at zero rather than raising — see the
    module docstring's rule about telemetry never killing a run. The parametrized
    `test_every_configured_model_is_priced` is what stops that leniency from
    quietly becoming permanent."""
    return _price(ACTUAL_PRICES.get(record.model, _FREE), record)


def shadow_cost_usd(record: LLMCallRecord) -> Decimal:
    """What the same tokens would have cost at gpt-4o-mini, whichever model
    actually answered. Always the same rates — that is what makes the number
    comparable across a six-model fallback chain."""
    return _price(SHADOW_PRICE, record)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_llm_cost.py -v`
Expected: PASS (11 tests — 6 in `TestTheTwoPrices`, 1 + 6 parametrized in `TestDriftGuards`)

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/llm/cost.py tests/test_llm_cost.py
git commit -m "feat(llm): two prices — what we paid, and gpt-4o-mini reference"
```

---

### Task 3: `llm/cache.py` — the record/replay store

**Files:**
- Create: `src/invoice_agent/llm/cache.py`
- Test: `tests/test_llm_cache.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `make_key(*, model, messages, temperature, max_tokens, json_mode, reasoning_effort, image_digests=None) -> str`; `CachedResponse(text, prompt_tokens, completion_tokens, error=None)`; `load(cache_dir, key) -> CachedResponse | None`; `save(cache_dir, key, *, model, messages, response) -> Path`; `LLMReplayMiss(RuntimeError)`. Task 5 imports all of these.

- [ ] **Step 1: Write the failing test**

Create `tests/test_llm_cache.py`:

```python
"""The fixture cache — key identity and round-trip. No gateway, no network."""

import json
from pathlib import Path

from invoice_agent.llm import cache

BASE_REQUEST = {
    "model": "groq/llama-3.3-70b-versatile",
    "messages": [{"role": "user", "content": "Hóa đơn số 00000123, tổng 1.500.000 đồng"}],
    "temperature": 0.1,
    "max_tokens": 2048,
    "json_mode": False,
    "reasoning_effort": None,
}


class TestKeyIdentity:
    def test_identical_requests_share_a_key(self):
        assert cache.make_key(**BASE_REQUEST) == cache.make_key(**BASE_REQUEST)

    def test_same_messages_different_model_are_distinct_keys(self):
        """Decided 2026-07-29 (spec §5.2): a response recorded from Groq must
        not replay as though Gemini produced it. llm_calls.model/provider feed
        the cost report and M4.1's exclude_model."""
        other = {**BASE_REQUEST, "model": "gemini/gemini-3.5-flash"}
        assert cache.make_key(**BASE_REQUEST) != cache.make_key(**other)

    def test_image_bytes_change_the_key(self):
        """[] for every text call; carries M3-B's vision images."""
        with_image = {**BASE_REQUEST, "image_digests": ["abc123"]}
        assert cache.make_key(**BASE_REQUEST) != cache.make_key(**with_image)

    def test_temperature_changes_the_key(self):
        hotter = {**BASE_REQUEST, "temperature": 0.9}
        assert cache.make_key(**BASE_REQUEST) != cache.make_key(**hotter)

    def test_key_is_stable_across_vietnamese_text(self):
        """ensure_ascii=False plus utf-8 encoding — a key that changed with the
        JSON escaping style would invalidate every fixture on a library bump."""
        key = cache.make_key(**BASE_REQUEST)
        assert key == cache.make_key(**json.loads(json.dumps(BASE_REQUEST, ensure_ascii=False)))


class TestRoundTrip:
    def test_save_then_load_returns_the_response(self, tmp_path: Path):
        key = cache.make_key(**BASE_REQUEST)
        cache.save(
            tmp_path, key,
            model=BASE_REQUEST["model"], messages=BASE_REQUEST["messages"],
            response=cache.CachedResponse(text="DỊCH VỤ", prompt_tokens=812, completion_tokens=97),
        )
        hit = cache.load(tmp_path, key)
        assert hit is not None
        assert hit.text == "DỊCH VỤ"
        assert (hit.prompt_tokens, hit.completion_tokens) == (812, 97)

    def test_miss_returns_none(self, tmp_path: Path):
        assert cache.load(tmp_path, "0" * 64) is None

    def test_a_truncated_filename_collision_is_a_miss_not_a_wrong_answer(self, tmp_path: Path):
        """Filenames use key[:16]; the file stores the FULL key and load()
        checks it. Without that check a 64-bit collision would silently replay
        someone else's response."""
        key = cache.make_key(**BASE_REQUEST)
        cache.save(
            tmp_path, key,
            model=BASE_REQUEST["model"], messages=BASE_REQUEST["messages"],
            response=cache.CachedResponse(text="x", prompt_tokens=1, completion_tokens=1),
        )
        path = tmp_path / f"{key[:16]}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["key"] = "f" * 64
        path.write_text(json.dumps(doc), encoding="utf-8")
        assert cache.load(tmp_path, key) is None

    def test_a_recorded_failure_round_trips(self, tmp_path: Path):
        """M3-C replays the escalation path: a vision call that FAILS, sending
        the graph to manual_queue. That needs a recorded failure."""
        key = cache.make_key(**BASE_REQUEST)
        cache.save(
            tmp_path, key,
            model=BASE_REQUEST["model"], messages=BASE_REQUEST["messages"],
            response=cache.CachedResponse(
                text="", prompt_tokens=812, completion_tokens=0,
                error="RateLimitError: 429 quota exceeded",
            ),
        )
        hit = cache.load(tmp_path, key)
        assert hit is not None and hit.error is not None
        assert "429" in hit.error

    def test_fixture_is_human_readable(self, tmp_path: Path):
        """Messages stored in the clear so a reviewer can read what was sent.
        ⚠️ Only anonymised or synthetic invoices may ever be recorded."""
        key = cache.make_key(**BASE_REQUEST)
        path = cache.save(
            tmp_path, key,
            model=BASE_REQUEST["model"], messages=BASE_REQUEST["messages"],
            response=cache.CachedResponse(text="ok", prompt_tokens=1, completion_tokens=1),
        )
        assert "Hóa đơn số 00000123" in path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_llm_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'invoice_agent.llm.cache'`

- [ ] **Step 3: Write the implementation**

Create `src/invoice_agent/llm/cache.py`:

```python
"""Record/replay fixture cache (design doc §11.4).

One mechanism, two problems: CI runs end-to-end tests with no API key, and a
live demo cannot be broken by somebody else's rate limit. Demos fail at the
worst possible moment; this removes the failure mode rather than hoping.

⚠️ Fixtures store the messages IN THE CLEAR, so a reviewer can read what was
actually sent to a model. **Only anonymised or synthetic invoices may ever be
recorded.** M3 opened by finding six real vendor PDFs committed under
tests/fixtures/ — a replay fixture is the same kind of quiet plaintext
container, and no automated layer in this repo looks at its contents.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class LLMReplayMiss(RuntimeError):
    """No fixture for this request, in a mode that must not touch the network.

    Raised rather than falling through to a live call. A fallthrough turns a
    missing fixture into one of two failures that look identical from the
    outside: a mysterious error on a keyless runner, or a test that quietly
    spent money.
    """


@dataclass(frozen=True)
class CachedResponse:
    """What one model returned — or the error it raised instead.

    `error` non-null means replay must RAISE rather than return text. M3-C
    needs it to replay the escalation path, where a failing vision call sends
    the graph to manual_queue.
    """

    text: str
    prompt_tokens: int
    completion_tokens: int
    error: str | None = None


def make_key(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    reasoning_effort: str | None,
    image_digests: list[str] | None = None,
) -> str:
    """sha256 over the whole request.

    `model` is IN the key on purpose (decided 2026-07-29, spec §5.2): a
    response recorded from Groq must not replay as though Gemini produced it,
    because llm_calls.model/provider feed the cost report and M4.1's
    exclude_model. The API key is deliberately NOT in the key — keys rotate
    within one model and a cached response does not depend on which one
    answered.

    `sort_keys=True, ensure_ascii=False` then utf-8: a key that moved with the
    JSON escaping style would invalidate every fixture on a library bump, and
    every message in this project contains Vietnamese.
    """
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "json_mode": json_mode,
        "reasoning_effort": reasoning_effort,
        "image_digests": image_digests or [],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _path_for(cache_dir: str | Path, key: str) -> Path:
    """Filenames are truncated for readability; `load` verifies the full key."""
    return Path(cache_dir) / f"{key[:16]}.json"


def load(cache_dir: str | Path, key: str) -> CachedResponse | None:
    path = _path_for(cache_dir, key)
    if not path.exists():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("key") != key:
        # 16 hex chars is 64 bits. A collision is vanishingly unlikely and
        # catastrophic if unchecked — it would replay an unrelated response as
        # though it were this request's. Treat it as a miss.
        return None
    return CachedResponse(
        text=doc["response"],
        prompt_tokens=doc["usage"]["prompt_tokens"],
        completion_tokens=doc["usage"]["completion_tokens"],
        error=doc.get("error"),
    )


def save(
    cache_dir: str | Path,
    key: str,
    *,
    model: str,
    messages: list[dict[str, Any]],
    response: CachedResponse,
) -> Path:
    path = _path_for(cache_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "key": key,
        "model": model,
        "messages": messages,
        "response": response.text,
        "usage": {
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        },
        "error": response.error,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_llm_cache.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/llm/cache.py tests/test_llm_cache.py
git commit -m "feat(llm): request-hashed fixture cache with a collision check"
```

---

### Task 4: Gateway — record every attempt, including the failures

§2.9's first easily-missed point: a 429 burned the input tokens. Not recording it makes the cost number wrong.

**Files:**
- Modify: `src/invoice_agent/llm/gateway.py:100-220`
- Test: `tests/test_gateway.py`

**Interfaces:**
- Consumes: `LLMCallRecord` from Task 2.
- Produces: `generate(..., calls: list[LLMCallRecord] | None = None)`, same on `generate_with_model` and `_generate_impl`. Task 5 adds the replay seam inside the loop this task restructures; Task 6 consumes the populated list.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gateway.py`:

```python
class TestCallRecording:
    """Design doc §2.9: record the FAILED calls too. A 429 still burned the
    input tokens, and omitting it makes the cost number wrong.

    Stubbed rather than replayed, deliberately (spec §5.2): API keys are not in
    the cache key, so 'key 1 rate-limits, key 2 succeeds' is unrepresentable as
    fixtures. Recording and replay are different behaviours, tested separately.
    """

    def test_success_records_one_attempt_with_provider_usage(self, monkeypatch):
        from invoice_agent.llm import gateway as gw_module

        def fake_completion(**kwargs):
            return _ok_response("phân loại: VĂN PHÒNG PHẨM", prompt=812, completion=97)

        monkeypatch.setattr(gw_module.litellm, "completion", fake_completion)
        calls = []
        gw = make_gateway(llm_model_chain=["groq/llama-3.3-70b-versatile"])
        text = gw.generate("xin chào", calls=calls)

        assert text == "phân loại: VĂN PHÒNG PHẨM"
        assert len(calls) == 1
        assert calls[0].succeeded is True
        assert calls[0].provider == "groq"
        assert (calls[0].prompt_tokens, calls[0].completion_tokens) == (812, 97)
        assert calls[0].attempt == 1

    def test_fallback_records_every_attempt(self, monkeypatch):
        """Two rate-limited keys then a success = THREE records, not one."""
        from invoice_agent.llm import gateway as gw_module

        seen = []

        def fake_completion(**kwargs):
            seen.append(kwargs["api_key"])
            if len(seen) <= 2:
                raise RuntimeError("429 rate limit exceeded")
            return _ok_response("ok", prompt=100, completion=10)

        monkeypatch.setattr(gw_module.litellm, "completion", fake_completion)
        calls = []
        gw = make_gateway(
            groq_api_keys=["gk1", "gk2", "gk3"],
            llm_model_chain=["groq/llama-3.3-70b-versatile"],
        )
        gw.generate("xin chào", calls=calls)

        assert [c.succeeded for c in calls] == [False, False, True]
        assert [c.attempt for c in calls] == [1, 2, 3]

    def test_failed_call_still_counts_input_tokens(self, monkeypatch):
        """The provider sends no usage payload with an error, but the tokens
        were burned. token_counter is offline for all six chain models
        (measured 2026-07-29, spec §2.4)."""
        from invoice_agent.llm import gateway as gw_module

        def always_429(**kwargs):
            raise RuntimeError("429 rate limit exceeded")

        monkeypatch.setattr(gw_module.litellm, "completion", always_429)
        calls = []
        gw = make_gateway(
            groq_api_keys=["gk1"], llm_model_chain=["groq/llama-3.3-70b-versatile"]
        )
        with pytest.raises(RuntimeError):
            gw.generate("Hóa đơn giá trị gia tăng số 00000123", calls=calls)

        assert len(calls) == 1
        assert calls[0].succeeded is False
        assert calls[0].prompt_tokens > 0, "input tokens were burned but not counted"
        assert calls[0].completion_tokens == 0
        assert "429" in (calls[0].error or "")

    def test_calls_out_param_is_optional(self, monkeypatch):
        """Every existing caller passes nothing and must keep working."""
        from invoice_agent.llm import gateway as gw_module

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: _ok_response("ok", prompt=1, completion=1),
        )
        gw = make_gateway(llm_model_chain=["groq/llama-3.3-70b-versatile"])
        assert gw.generate("xin chào") == "ok"

    def test_latency_is_measured_per_attempt(self, monkeypatch):
        from invoice_agent.llm import gateway as gw_module

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: _ok_response("ok", prompt=1, completion=1),
        )
        calls = []
        gw = make_gateway(llm_model_chain=["groq/llama-3.3-70b-versatile"])
        gw.generate("xin chào", calls=calls)
        assert calls[0].latency_ms >= 0
```

Add this helper near the top of `tests/test_gateway.py`, under the existing `make_gateway`:

```python
class _FakeUsage:
    def __init__(self, prompt: int, completion: int):
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    """Just enough of a litellm ModelResponse for the gateway's two accesses:
    `.choices[0].message.content` and `.usage`."""

    def __init__(self, content: str, prompt: int, completion: int):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage(prompt, completion)


def _ok_response(content: str, *, prompt: int, completion: int) -> _FakeResponse:
    return _FakeResponse(content, prompt, completion)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gateway.py::TestCallRecording -v`
Expected: FAIL with `TypeError: generate() got an unexpected keyword argument 'calls'`

- [ ] **Step 3: Add the record-building helper**

In `src/invoice_agent/llm/gateway.py`, add `import time` beside the existing `import re`, and add the import:

```python
from invoice_agent.llm.cost import LLMCallRecord
```

Then add this method to `LLMGateway`:

```python
    def _record_attempt(
        self,
        calls: list[LLMCallRecord] | None,
        model: str,
        *,
        started: float,
        attempt: int,
        messages: list[dict[str, Any]],
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        error: Exception | None = None,
    ) -> None:
        """One LLMCallRecord per ATTEMPT — including the ones that failed.

        Design doc §2.9: a 429 still burned the input tokens, and not recording
        it makes the cost number wrong. The provider sends no usage payload
        with an error, so input tokens come from litellm.token_counter, which
        works offline for all six chain models (measured 2026-07-29, spec §2.4)
        and must be counted PER MODEL — the tokenizers differ, 26 vs 34 tokens
        for the same Vietnamese sentence.
        """
        if calls is None:
            return
        if prompt_tokens is None:
            prompt_tokens = self._count_tokens(model, messages)
        calls.append(
            LLMCallRecord(
                model=model,
                provider=self._provider(model),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens or 0,
                latency_ms=int((time.perf_counter() - started) * 1000),
                attempt=attempt,
                succeeded=error is None,
                error=None if error is None else f"{type(error).__name__}: {error}"[:500],
            )
        )

    @staticmethod
    def _count_tokens(model: str, messages: list[dict[str, Any]]) -> int:
        """Input tokens for an attempt the provider never reported on.

        Returns 0 rather than raising if litellm has no tokenizer for the
        model: the same rule as pricing an unmapped model. Telemetry that can
        kill an invoice run is worse than telemetry that under-reports.
        """
        try:
            return int(litellm.token_counter(model=model, messages=messages))
        except Exception:
            return 0
```

- [ ] **Step 4: Thread `calls` through the three public entry points**

Add `calls: list[LLMCallRecord] | None = None` as the last parameter of `generate`, `generate_with_model` and `_generate_impl`, and pass it down. Document it once, on `generate`:

```python
            calls: If a list is passed, one LLMCallRecord is appended per
                ATTEMPT — including failed ones, which design doc §2.9 requires
                because a 429 still burned input tokens. A LIST, not a dict:
                one call can make several attempts (two rate-limited keys then
                a success is three records), and a single out-dict could only
                hold the last, silently dropping the failures. This is the same
                out-param idiom as `used_model` in `generate_stream`, and the
                same shape as `EnrichmentState.llm_calls` in design doc §3.1.
```

- [ ] **Step 5: Restructure the attempt loop in `_generate_impl`**

Replace the `for api_key in self._keys_for(attempt_model):` block with:

```python
            for api_key in self._keys_for(attempt_model):
                attempt_no += 1
                started = time.perf_counter()
                try:
                    response = litellm.completion(
                        model=attempt_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        api_key=api_key,
                        **model_kwargs,
                    )
                    usage = getattr(response, "usage", None)
                    self._record_attempt(
                        calls, attempt_model,
                        started=started, attempt=attempt_no, messages=messages,
                        prompt_tokens=getattr(usage, "prompt_tokens", None),
                        completion_tokens=getattr(usage, "completion_tokens", None),
                    )
                    return (
                        self._clean_thinking(response.choices[0].message.content),
                        attempt_model,
                    )
                except Exception as e:
                    last_error = e
                    self._record_attempt(
                        calls, attempt_model,
                        started=started, attempt=attempt_no, messages=messages, error=e,
                    )
                    if self._is_rate_limit(e):
                        continue  # rotate to the next key for the same model
                    break  # non-rate-limit failure — try the next model
```

Initialise `attempt_no = 0` beside the existing `last_error = None`. `attempt` numbers monotonically across the whole call (1, 2, 3), not per model — so `COUNT(*)` over one call's rows equals its attempt count (spec §8.3).

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_gateway.py -v`
Expected: PASS — the new `TestCallRecording` class plus every pre-existing gateway test unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/invoice_agent/llm/gateway.py tests/test_gateway.py
git commit -m "feat(gateway): one record per attempt, failures included (§2.9)"
```

---

### Task 5: Gateway — the replay seam, below chain resolution

Measured 2026-07-29 (spec §2.1): with no API keys `_usable_models` drops **all six** models, `_generate_impl` never enters its loop, and the call raises without one HTTP request. Replay is what makes the gateway run at all in CI.

**Files:**
- Modify: `src/invoice_agent/llm/gateway.py`
- Test: `tests/test_gateway.py`

**Interfaces:**
- Consumes: `cache.make_key/load/save/CachedResponse/LLMReplayMiss` (Task 3), `_record_attempt` (Task 4).
- Produces: no new public API. `LLMGateway.__init__` gains `self.mode` and `self.cache_dir` from `Settings`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gateway.py`:

```python
class TestReplayMode:
    """The M3 gate condition this spec owns: the suite runs with LLM_MODE=replay
    and NO API KEY."""

    def _record_fixture(self, tmp_path, *, model, prompt="xin chào", text="ok", error=None):
        from invoice_agent.llm import cache

        messages = [{"role": "user", "content": prompt}]
        key = cache.make_key(
            model=model, messages=messages, temperature=0.1, max_tokens=2048,
            json_mode=False, reasoning_effort=None,
        )
        cache.save(
            tmp_path, key, model=model, messages=messages,
            response=cache.CachedResponse(
                text=text, prompt_tokens=812, completion_tokens=97, error=error
            ),
        )

    def test_replay_runs_with_no_api_keys(self, tmp_path, monkeypatch):
        """Measured 2026-07-29: with zero keys _usable_models returns [] — not a
        shorter chain. Without the replay bypass NOTHING runs on a CI runner."""
        from invoice_agent.llm import gateway as gw_module

        def explode(**kwargs):
            raise AssertionError("replay must never reach the network")

        monkeypatch.setattr(gw_module.litellm, "completion", explode)
        model = "gemini/gemini-3.5-flash"
        self._record_fixture(tmp_path, model=model, text="DỊCH VỤ VẬN CHUYỂN")

        gw = make_gateway(
            groq_api_keys=[], gemini_api_keys=[], openrouter_api_keys=[], openai_api_key="",
            llm_mode="replay", llm_cache_dir=str(tmp_path), llm_model_chain=[model],
        )
        calls = []
        assert gw.generate("xin chào", calls=calls) == "DỊCH VỤ VẬN CHUYỂN"
        assert calls[0].prompt_tokens == 812, "replay must supply usage, or CI records no cost"

    def test_replay_walks_the_model_chain(self, tmp_path, monkeypatch):
        """A fixture recorded for the SECOND model must still be found — which
        only works if the seam sits inside the model loop."""
        from invoice_agent.llm import gateway as gw_module

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("no network in replay")),
        )
        second = "gemini/gemini-3.5-flash"
        self._record_fixture(tmp_path, model=second, text="từ model thứ hai")

        gw = make_gateway(
            groq_api_keys=[], gemini_api_keys=[], openrouter_api_keys=[], openai_api_key="",
            llm_mode="replay", llm_cache_dir=str(tmp_path),
            llm_model_chain=["groq/llama-3.3-70b-versatile", second],
        )
        text, used = gw.generate_with_model("xin chào")
        assert (text, used) == ("từ model thứ hai", second)

    def test_replay_miss_raises_and_never_calls_litellm(self, tmp_path, monkeypatch):
        from invoice_agent.llm import gateway as gw_module
        from invoice_agent.llm.cache import LLMReplayMiss

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("no network in replay")),
        )
        gw = make_gateway(
            groq_api_keys=[], llm_mode="replay", llm_cache_dir=str(tmp_path),
            llm_model_chain=["groq/llama-3.3-70b-versatile"],
        )
        with pytest.raises(LLMReplayMiss) as excinfo:
            gw.generate("một prompt chưa từng được ghi")
        assert "LLM_MODE=record" in str(excinfo.value), "the error must say how to fix it"

    def test_a_replay_miss_records_no_attempt(self, tmp_path):
        """Nothing happened — no tokens burned, so no llm_calls row."""
        from invoice_agent.llm.cache import LLMReplayMiss

        gw = make_gateway(
            groq_api_keys=[], llm_mode="replay", llm_cache_dir=str(tmp_path),
            llm_model_chain=["groq/llama-3.3-70b-versatile"],
        )
        calls = []
        with pytest.raises(LLMReplayMiss):
            gw.generate("chưa ghi bao giờ", calls=calls)
        assert calls == []

    def test_a_recorded_failure_replays_as_a_raise(self, tmp_path, monkeypatch):
        """M3-C replays the escalation path, where a failing call is the point."""
        from invoice_agent.llm import gateway as gw_module

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("no network in replay")),
        )
        model = "groq/llama-3.3-70b-versatile"
        self._record_fixture(tmp_path, model=model, text="", error="RateLimitError: 429 quota")

        gw = make_gateway(
            groq_api_keys=[], llm_mode="replay", llm_cache_dir=str(tmp_path),
            llm_model_chain=[model],
        )
        calls = []
        with pytest.raises(RuntimeError):
            gw.generate("xin chào", calls=calls)
        assert calls and calls[0].succeeded is False

    def test_record_mode_writes_a_fixture(self, tmp_path, monkeypatch):
        from invoice_agent.llm import gateway as gw_module

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: _ok_response("đã ghi", prompt=11, completion=3),
        )
        gw = make_gateway(
            llm_mode="record", llm_cache_dir=str(tmp_path),
            llm_model_chain=["groq/llama-3.3-70b-versatile"],
        )
        assert gw.generate("xin chào") == "đã ghi"
        assert list(tmp_path.glob("*.json")), "record mode wrote no fixture"

    def test_live_mode_ignores_the_cache(self, tmp_path, monkeypatch):
        from invoice_agent.llm import gateway as gw_module

        model = "groq/llama-3.3-70b-versatile"
        self._record_fixture(tmp_path, model=model, text="giá trị cũ trong cache")
        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: _ok_response("giá trị trực tiếp", prompt=1, completion=1),
        )
        gw = make_gateway(
            llm_mode="live", llm_cache_dir=str(tmp_path), llm_model_chain=[model]
        )
        assert gw.generate("xin chào") == "giá trị trực tiếp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gateway.py::TestReplayMode -v`
Expected: FAIL — `test_replay_runs_with_no_api_keys` raises `RuntimeError: All models failed. Last error: None`, which is the §2.1 measurement reproducing itself.

- [ ] **Step 3: Read mode and cache dir in `__init__`**

In `LLMGateway.__init__`, after `self.openai_key = s.openai_api_key`:

```python
        # Replay is not a test convenience wrapped around a working gateway:
        # measured 2026-07-29 (spec §2.1), with no API keys _usable_models
        # drops all six models, so on a CI runner this mode is the only thing
        # that makes the gateway run at all.
        self.mode = s.llm_mode
        self.cache_dir = s.llm_cache_dir
```

- [ ] **Step 4: Bypass the key filter in replay**

Change the last line of `_resolve_chain` from `return self._usable_models(models_to_try)` to:

```python
        if self.mode == "replay":
            # No keys are needed to read a fixture, and _usable_models would
            # filter the whole chain away — `_keys_for("groq/…")` returns [''],
            # and `any([''])` is False. `record` and `live` keep the filter:
            # both make real calls and need real keys.
            return models_to_try
        return self._usable_models(models_to_try)
```

- [ ] **Step 5: Add the `_complete` seam**

Add to `LLMGateway`, and add `from invoice_agent.llm import cache` to the imports:

```python
    def _complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        reasoning_effort: str | None,
        api_key: str,
        model_kwargs: dict[str, Any],
    ) -> tuple[str, int, int]:
        """The one point where a request becomes a network call — or does not.

        Returns (text, prompt_tokens, completion_tokens), normalised so the
        caller does not care which mode produced it.

        The seam is HERE, inside the model loop, rather than around generate():
        with no API keys the chain resolves to EMPTY (measured 2026-07-29, spec
        §2.1), so anything placed above chain resolution would be testing a
        code path CI can never reach.
        """
        key = cache.make_key(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            reasoning_effort=reasoning_effort,
        )

        if self.mode == "replay":
            hit = cache.load(self.cache_dir, key)
            if hit is None:
                raise cache.LLMReplayMiss(
                    f"No fixture for model={model} key={key[:16]}. "
                    f"Re-record with LLM_MODE=record, or check whether the prompt changed."
                )
            if hit.error:
                # A recorded failure replays as a raise. RuntimeError rather
                # than the original class because only the message survives
                # recording — and _is_rate_limit matches on the message anyway.
                raise RuntimeError(hit.error)
            return hit.text, hit.prompt_tokens, hit.completion_tokens

        response = litellm.completion(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            **model_kwargs,
        )
        usage = getattr(response, "usage", None)
        text = self._clean_thinking(response.choices[0].message.content)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

        if self.mode == "record":
            cache.save(
                self.cache_dir, key, model=model, messages=messages,
                response=cache.CachedResponse(
                    text=text,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                ),
            )
        return text, prompt_tokens, completion_tokens
```

- [ ] **Step 6: Route the attempt loop through `_complete`**

Rewrite the inner block from Task 4 Step 5 as:

```python
            # Replay must make exactly one pass per model, whatever the key
            # situation. `_keys_for` returns [] for a provider with no key, so
            # keeping it here would mean a keyless CI runner never enters this
            # loop at all — the §2.1 measurement biting a second time, one
            # level down. There is also nothing to rotate: API keys are
            # deliberately not in the cache key, so every key maps to the same
            # fixture.
            api_keys = [""] if self.mode == "replay" else self._keys_for(attempt_model)
            for api_key in api_keys:
                attempt_no += 1
                started = time.perf_counter()
                try:
                    text, prompt_tokens, completion_tokens = self._complete(
                        model=attempt_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        json_mode=json_mode,
                        reasoning_effort=reasoning_effort,
                        api_key=api_key,
                        model_kwargs=model_kwargs,
                    )
                except cache.LLMReplayMiss as miss:
                    # No fixture is not a failed call: nothing was sent, no
                    # tokens burned, so no record. Move to the next model —
                    # a later one in the chain may have been recorded.
                    replay_misses.append(miss)
                    break
                except Exception as e:
                    last_error = e
                    self._record_attempt(
                        calls, attempt_model,
                        started=started, attempt=attempt_no, messages=messages, error=e,
                    )
                    if self._is_rate_limit(e):
                        continue
                    break
                self._record_attempt(
                    calls, attempt_model,
                    started=started, attempt=attempt_no, messages=messages,
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                )
                return text, attempt_model
```

Note `_clean_thinking` moved into `_complete`, so it is no longer applied here.

Initialise `replay_misses: list[Exception] = []` beside `last_error`, and replace the final `raise` with:

```python
        if replay_misses:
            raise cache.LLMReplayMiss(
                "LLM_MODE=replay and no fixture matched any model in the chain "
                f"{models_to_try}. Re-record with LLM_MODE=record. "
                f"First miss: {replay_misses[0]}"
            )
        raise RuntimeError(f"All models failed. Last error: {last_error}")
```

The `LLM_MODE=record` substring in that message is asserted by a test: an error that does not say how to fix it costs the next person an hour.

- [ ] **Step 7: Run the full gateway suite**

Run: `.venv/bin/python -m pytest tests/test_gateway.py -v`
Expected: PASS — `TestReplayMode`, `TestCallRecording`, and every pre-existing class.

- [ ] **Step 8: Prove the gate condition for real**

Run: `env -u GROQ_API_KEY -u GROQ_API_KEYS -u GEMINI_API_KEY -u GEMINI_API_KEYS -u OPENROUTER_API_KEY -u OPENROUTER_API_KEYS -u OPENAI_API_KEY LLM_MODE=replay .venv/bin/python -m pytest tests/ -q`
Expected: PASS. This is the M3 gate condition, run the way CI runs it.

- [ ] **Step 9: Commit**

```bash
git add src/invoice_agent/llm/gateway.py tests/test_gateway.py
git commit -m "feat(gateway): replay seam below chain resolution (§11.4)"
```

---

### Task 6: `llm/telemetry.py` — priced records reach the database

**Files:**
- Create: `src/invoice_agent/llm/telemetry.py`
- Test: `tests/test_llm_telemetry.py`

**Interfaces:**
- Consumes: `LLMCallRecord`, `actual_cost_usd`, `shadow_cost_usd` (Task 2); `llm_calls.record_call` with its five new params (Task 1).
- Produces: `record(conn, records, *, node, invoice_id=None, batch_id=None, thread_id=None, agent=None, trace_id=None) -> list[int]`. M3-B's vision node and M4's classify/audit call exactly this.

- [ ] **Step 1: Write the failing test**

Create `tests/test_llm_telemetry.py`:

```python
"""Cost reaching the database — the recording half of the M3 gate.

Runs as the RUNTIME role, like tests/test_repositories.py: running as the owner
would hide a missing GRANT, and migration 10 adds columns to a granted table.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from invoice_agent.db import models
from invoice_agent.db.repositories import invoices as invoice_repo
from invoice_agent.db.repositories import vendors
from invoice_agent.llm import telemetry
from invoice_agent.llm.cost import LLMCallRecord


def _make_invoice(conn) -> uuid.UUID:
    tax_code = f"ZZ{uuid.uuid4().hex[:8]}"
    vendors.ensure_vendor(conn, tax_code, name="Nhà cung cấp thử nghiệm")
    invoice_id = uuid.uuid4()
    invoice_repo.insert_row(
        conn,
        {
            "id": invoice_id,
            "template_no": "1",
            "invoice_series": "C26TAA",
            "invoice_number": uuid.uuid4().hex[:8],
            "seller_tax_code": tax_code,
            "issue_date": date(2026, 7, 1),
            "total_amount": Decimal("1000000"),
            "source_type": "xml",
            "status": "received",
        },
    )
    return invoice_id


def _rows_for(conn, invoice_id):
    return conn.execute(
        select(models.llm_calls).where(models.llm_calls.c.invoice_id == invoice_id)
    ).mappings().all()


def test_one_invoice_end_to_end_has_its_cost_recorded(runtime_conn):
    """The M3 gate sentence, in one assertion."""
    invoice_id = _make_invoice(runtime_conn)
    telemetry.record(
        runtime_conn,
        [
            LLMCallRecord(
                model="groq/llama-3.3-70b-versatile", provider="groq",
                prompt_tokens=1000, completion_tokens=200,
                latency_ms=430, attempt=1, succeeded=True,
            )
        ],
        node="vision_extract",
        invoice_id=invoice_id,
        thread_id=f"enrich:{invoice_id}",
        agent="VisionExtractor",
    )
    rows = _rows_for(runtime_conn, invoice_id)
    assert len(rows) == 1
    assert rows[0]["node"] == "vision_extract"
    assert rows[0]["agent"] == "VisionExtractor"
    assert rows[0]["thread_id"] == f"enrich:{invoice_id}"


def test_free_tier_records_zero_cost_and_a_non_zero_shadow(runtime_conn):
    """A bare $0 reads as 'unmeasured'. The pair reads as 'cheap'."""
    invoice_id = _make_invoice(runtime_conn)
    telemetry.record(
        runtime_conn,
        [
            LLMCallRecord(
                model="groq/llama-3.3-70b-versatile", provider="groq",
                prompt_tokens=1000, completion_tokens=200,
                latency_ms=430, attempt=1, succeeded=True,
            )
        ],
        node="classify",
        invoice_id=invoice_id,
    )
    row = _rows_for(runtime_conn, invoice_id)[0]
    assert row["cost_usd"] == Decimal(0)
    assert row["shadow_cost_usd"] == Decimal("0.00027")


def test_a_failed_attempt_is_written_too(runtime_conn):
    """§2.9's first easily-missed point: a 429 burned the input tokens."""
    invoice_id = _make_invoice(runtime_conn)
    telemetry.record(
        runtime_conn,
        [
            LLMCallRecord(
                model="groq/llama-3.3-70b-versatile", provider="groq",
                prompt_tokens=1000, completion_tokens=0, latency_ms=90,
                attempt=1, succeeded=False, error="RateLimitError: 429 quota",
            ),
            LLMCallRecord(
                model="gemini/gemini-3.5-flash", provider="gemini",
                prompt_tokens=1000, completion_tokens=200, latency_ms=380,
                attempt=2, succeeded=True,
            ),
        ],
        node="classify",
        invoice_id=invoice_id,
    )
    rows = sorted(_rows_for(runtime_conn, invoice_id), key=lambda r: r["attempt"])
    assert [r["succeeded"] for r in rows] == [False, True]
    assert rows[0]["prompt_tokens"] == 1000, "the failed attempt burned these"
    assert "429" in rows[0]["error"]


def test_context_columns_are_optional(runtime_conn):
    """A call from scripts/run_one.py has no invoice yet."""
    ids = telemetry.record(
        runtime_conn,
        [
            LLMCallRecord(
                model="openai/gpt-4o-mini", provider="openai",
                prompt_tokens=10, completion_tokens=2, latency_ms=50,
                attempt=1, succeeded=True,
            )
        ],
        node="eval_harness",
    )
    assert len(ids) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_llm_telemetry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'invoice_agent.llm.telemetry'`

- [ ] **Step 3: Write the implementation**

Create `src/invoice_agent/llm/telemetry.py`:

```python
"""Priced records into app.llm_calls — the one place cost reaches the database.

Its own module so the dependency points DOWNWARD from glue: telemetry imports
both `llm.cost` and `db.repositories`, while the gateway imports only the pure
dataclass and `db/` keeps knowing nothing about `llm/`. Pricing inside the
repository would invert that; pricing at each call site would repeat it three
times by M4 (vision, classify, audit).
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import Connection

from invoice_agent.db.repositories import llm_calls
from invoice_agent.llm.cost import LLMCallRecord, actual_cost_usd, shadow_cost_usd


def record(
    conn: Connection,
    records: Sequence[LLMCallRecord],
    *,
    node: str,
    invoice_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
    thread_id: str | None = None,
    agent: str | None = None,
    trace_id: str | None = None,
) -> list[int]:
    """Write one row per attempt. Returns the new row ids, in order.

    Every attempt, not only the successful one: design doc §2.9 is explicit
    that a 429 still burned input tokens, and omitting it makes the cost number
    wrong. `trace_id` is accepted now and written by nothing until M6.5 wires
    Langfuse.
    """
    return [
        llm_calls.record_call(
            conn,
            model=r.model,
            provider=r.provider,
            node=node,
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            cost_usd=actual_cost_usd(r),
            shadow_cost_usd=shadow_cost_usd(r),
            latency_ms=r.latency_ms,
            attempt=r.attempt,
            succeeded=r.succeeded,
            error=r.error,
            invoice_id=invoice_id,
            batch_id=batch_id,
            thread_id=thread_id,
            agent=agent,
            trace_id=trace_id,
        )
        for r in records
    ]
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_llm_telemetry.py -v`
Expected: PASS (4 tests). If they SKIP, run `make dev`.

- [ ] **Step 5: Run the whole check**

Run: `make check`
Expected: ruff clean, mypy clean, all tests green.

- [ ] **Step 6: Commit**

```bash
git add src/invoice_agent/llm/telemetry.py tests/test_llm_telemetry.py
git commit -m "feat(llm): priced telemetry — one row per attempt, cost and shadow"
```

---

### Task 7: Mutation verification and the milestone note

Every guard in this plan is worth nothing until it has been seen red. M1 shipped four tests that passed while guarding nothing, and M2 found one more in each of its three sub-milestones. Do not skip this task, and do not report a mutation as red without having run it.

**Files:**
- Create: `docs/notes/2026-07-29-m3a-llm-plumbing.md`
- Temporarily modify (and revert) the source files under test.

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: the evidence table cited by the M3 gate.

- [ ] **Step 1: Run each mutation, one at a time**

For each row: apply the mutation, run the named test, record PASS or FAIL, then `git checkout --` the file before the next row. A mutation that leaves its guard **green** is a defect in the test, not a curiosity — fix the test and re-run.

| # | Mutation | Command to verify |
|---|---|---|
| 1 | In `_resolve_chain`, delete the `if self.mode == "replay": return models_to_try` bypass | `pytest tests/test_gateway.py::TestReplayMode::test_replay_runs_with_no_api_keys` |
| 2 | In `_complete`, move the replay lookup so it only runs for `models_to_try[0]` | `pytest tests/test_gateway.py::TestReplayMode::test_replay_walks_the_model_chain` |
| 3 | In `cost.py`, make `actual_cost_usd` call `litellm.completion_cost` | `pytest tests/test_llm_cost.py::TestTheTwoPrices::test_free_tier_costs_zero_and_shadow_does_not` |
| 4 | In `cache.make_key`, drop `"model"` from `payload` | `pytest tests/test_llm_cache.py::TestKeyIdentity::test_same_messages_different_model_are_distinct_keys` |
| 5 | In `cache.make_key`, drop `"image_digests"` from `payload` | `pytest tests/test_llm_cache.py::TestKeyIdentity::test_image_bytes_change_the_key` |
| 6 | In the attempt loop, delete the `_record_attempt` call in the `except Exception` branch | `pytest tests/test_gateway.py::TestCallRecording::test_fallback_records_every_attempt` |
| 7 | In `_record_attempt`, replace the `_count_tokens` fallback with `prompt_tokens = 0` | `pytest tests/test_gateway.py::TestCallRecording::test_failed_call_still_counts_input_tokens` |
| 8 | In `_complete`, replace `raise cache.LLMReplayMiss(...)` with a fallthrough to `litellm.completion` | `pytest tests/test_gateway.py::TestReplayMode::test_replay_miss_raises_and_never_calls_litellm` |
| 9 | In `cost.shadow_cost_usd`, price with `ACTUAL_PRICES.get(record.model, _FREE)` | `pytest tests/test_llm_cost.py::TestTheTwoPrices::test_shadow_price_is_gpt4o_mini_regardless_of_model` |
| 10 | `alembic downgrade -1` | `pytest tests/test_migrations.py::TestLlmCallsContextColumns` (then `alembic upgrade head`) |
| 11 | In `cache.load`, delete the `doc.get("key") != key` check | `pytest tests/test_llm_cache.py::TestRoundTrip::test_a_truncated_filename_collision_is_a_miss_not_a_wrong_answer` |
| 12 | In the attempt loop, replace `api_keys = [""] if self.mode == "replay" else …` with a plain `self._keys_for(attempt_model)` | `pytest tests/test_gateway.py::TestReplayMode::test_replay_runs_with_no_api_keys` |

- [ ] **Step 2: Confirm the tree is clean**

Run: `git status --porcelain src/ migrations/`
Expected: empty. A leftover mutation is the worst possible thing to commit.

- [ ] **Step 3: Write the note**

Create `docs/notes/2026-07-29-m3a-llm-plumbing.md` following the shape of `docs/notes/2026-07-28-m2b-domain-core.md`: what was built, the four §2 measurements with their numbers, the mutation table above with its measured results, and the M3-A success criteria from spec §7 marked met or not met. State honestly anything that came out differently from the spec's prediction — that record is the portfolio content, not the tidy version.

- [ ] **Step 4: Final verification**

Run: `make check`
Then: `env -u GROQ_API_KEY -u GROQ_API_KEYS -u GEMINI_API_KEY -u GEMINI_API_KEYS -u OPENROUTER_API_KEY -u OPENROUTER_API_KEYS -u OPENAI_API_KEY LLM_MODE=replay .venv/bin/python -m pytest tests/ -q`
Expected: both green. The second is the gate condition; do not claim M3-A complete on the first alone.

- [ ] **Step 5: Update the roadmap**

In `docs/Plan_Project_F_2026-07-25.md`, mark items 3.6 and 3.7 done with the date, add the M3-A line to the §7 status table's M3 row (following how the M2 row records its three sub-specs), and record in §M3 that item 3.1 moved to M7 with the reason from spec §3.3.

- [ ] **Step 6: Commit**

```bash
git add docs/notes/2026-07-29-m3a-llm-plumbing.md docs/Plan_Project_F_2026-07-25.md
git commit -m "docs(m3a): close LLM plumbing — 11 mutation guards verified red"
```

---

## Spec Coverage

| Spec section | Task |
|---|---|
| §1.3 + §4 — migration 10, the five missing columns | Task 1 |
| §5.1 — `llm/cost.py`, `LLMCallRecord`, two price tables | Task 2 |
| §5.2 — `llm/cache.py`, key, layout, three modes, recorded failure, miss | Task 3 |
| §5.3 change 1 — `calls` out-param as a list | Task 4 |
| §5.3 changes 2-3 — replay seam, chain bypass | Task 5 |
| §5.4 — `llm/telemetry.py` and its layering | Task 6 |
| §6.1 — the anchor: suite green under replay with no keys | Task 5 Step 8, Task 7 Step 4 |
| §6.2 — ten mutation guards | Task 7 Step 1 (**twelve** — two were added while writing the plan: the cache-collision check, and the replay key-loop bypass found by tracing `test_replay_runs_with_no_api_keys` through `_keys_for`) |
| §6.3 — drift guards | Task 2 (`TestDriftGuards`) |
| §7 — success criteria 1-6 | Task 7 Steps 2-4 |
| §3.3 — item 3.1 deferred to M7, reason recorded | Task 7 Step 5 |
