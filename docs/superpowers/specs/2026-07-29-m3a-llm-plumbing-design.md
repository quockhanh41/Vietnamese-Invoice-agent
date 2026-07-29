# Spec — M3-A: LLM plumbing (plan items 3.6, 3.7)

> **Date**: 2026-07-29 · **Milestone**: M3, first of three specs
> **Covers**: plan item **3.6** (cost capture into `llm_calls`, failures included, `shadow_cost_usd`)
> and **3.7** (`LLM_MODE=replay`), plus the migration that closes an M2-A defect these two depend on.
> **Does not cover**: 3.2/3.3/3.4 (M3-B — sanitize, structured-output retry, vision) and 3.5 (M3-C —
> the enrichment graph). Item **3.1** (`tools/registry.py`) is deferred to M7 — see §3.3.
> **Predecessor**: `docs/superpowers/specs/2026-07-28-m2c-corpus-generator-design.md`

---

## 1. Why this, and what it owes

### 1.1 The gate

M3's gate has four conditions. This spec owns one outright and is a precondition for two more:

| Gate condition | This spec |
|---|---|
| The whole suite runs under `LLM_MODE=replay` **without an API key** | **owns it** |
| One invoice end-to-end with cost recorded in `llm_calls` | supplies the recording half; M3-C supplies the invoice |
| Extraction error escalates to vision; broken vision falls to manual queue | precondition — the vision call must be replayable or CI cannot run it |
| 3-iteration loop asserts `len(error_history) == 3` | unrelated (M3-C) |

### 1.2 Why it is the first spec

Every later node in M3 calls an LLM. Until replay exists, none of them is testable on a runner with
no keys — which is every runner, since CI deliberately holds none (`ci.yml:36-40`). Building the
graph first would mean writing its tests twice: once against mocks, then again once replay landed.

The ordering is also forced from below, and harder than it looks: **with no API keys the gateway
cannot try any model at all** (§2.1). Replay is not a convenience layer over a working gateway; in
CI it is the only thing that makes the gateway run.

### 1.3 An M2-A defect this spec closes

The M2-A spec states `llm_calls` is "design doc §2.9 verbatim". It is not. §2.9 specifies:

```
id, invoice_id NULL, batch_id NULL, thread_id, node, agent,
model, provider, prompt_tokens, completion_tokens,
cost_usd, shadow_cost_usd, latency_ms, attempt,
succeeded BOOL, error, trace_id, created_at
```

Migration `b25d8a939562_m2a_09` creates the table with **five columns missing**: `invoice_id`,
`batch_id`, `thread_id`, `agent`, `trace_id`. Nothing caught it, because nothing wrote to the table
until now — the same shape as the M1/M2 pattern where a claim outran its measurement, except here
the claim was in a spec rather than a test.

The M3 gate cannot be met without at least `invoice_id`: "cost for **one invoice**" is not
expressible in a table that cannot name an invoice. Fixing it is defect repair, not scope growth.

---

## 2. Measurements taken 2026-07-29 for this spec

All four were run against the installed venv, `litellm 1.93.0`.

### 2.1 With zero API keys the model chain resolves to EMPTY, not to a shorter chain

```
chain length: 6
usable with ZERO keys: []
_keys_for("groq/…")   -> ['']
_keys_for("gemini/…") -> []
```

`_usable_models` (`gateway.py:66`) keeps a model only if `any(self._keys_for(m))`. For Groq the
fallback `self.groq_keys or [""]` yields `['']`, and `any([''])` is **False** — the empty string is
falsy. So on a keyless runner **all six models are filtered out**, `_generate_impl` skips its loop
entirely and raises `RuntimeError("All models failed. Last error: None")` without one HTTP call.

Two consequences, and the second is the sharp one:

1. Replay must **bypass `_usable_models`**, or the cache is never consulted.
2. A replay implementation that wrapped `generate()` from the outside would still work, and would
   therefore hide this. Anything that puts the seam *below* the chain resolution is testing a code
   path CI can never reach. The seam belongs **inside** `_generate_impl` (§5.3), and a mutation
   guard must prove the bypass is load-bearing.

> An earlier draft of this design predicted the chain would collapse to Groq-only. Measured, it
> collapses to nothing. The bypass is mandatory rather than merely tidy.

### 2.2 `litellm.completion_cost` reports list price for free tiers, and raises on 2 of 6

Priced against a synthetic 1000-in / 200-out response:

| Model | `completion_cost` | What we actually pay |
|---|---:|---:|
| `groq/llama-3.3-70b-versatile` | $0.000748 | **$0** (free tier) |
| `gemini/gemini-3.5-flash` | $0.003300 | **$0** (AI Studio free) |
| `openrouter/qwen/qwen3-next-80b-a3b-instruct:free` | **raises** `This model isn't mapped yet` | $0 |
| `openai/gpt-4o-mini` | $0.000270 | $0.000270 |

This settles the `cost_usd` question by measurement rather than preference. Using LiteLLM would
record $0.000748 for a call that cost nothing, which destroys the one sentence §2.9 exists to let
the report make — *"$0 in practice on free tier; $X at gpt-4o-mini reference price"* — by making
both columns say the same wrong thing. And it would crash outright on two of the six chain models.

### 2.3 LiteLLM's `gpt-4o-mini` price agrees with plan §2.2 to the cent

$0.000270 for 1000-in / 200-out is exactly `1000/1e6 × $0.15 + 200/1e6 × $0.60`. The plan's
reference price and LiteLLM's map are the same number **today**. That is worth a test but not worth
a dependency: if LiteLLM revises its map, every historical `shadow_cost_usd` row silently stops
being comparable to the new ones. The table is hardcoded with the date-and-measurement comment
CLAUDE.md requires, and a test asserts LiteLLM still agrees — so drift is *detected* rather than
absorbed.

### 2.4 `token_counter` works offline for all six chain models

```
26  groq/llama-3.3-70b-versatile          34  gemini/gemini-3.5-flash
34  groq/openai/gpt-oss-120b              34  openrouter/qwen/…-instruct:free
26  openrouter/meta-llama/…:free          34  openai/gpt-4o-mini
```

No network, no key. This is what makes §2.9's "record the failed call too" implementable: a 429
returns no usage payload, but the input tokens were burned regardless, and `token_counter` supplies
them. The counts differ per model (26 vs 34 for identical Vietnamese text) because the tokenizers
differ — so the estimate must be taken **per attempted model**, not once per call.

---

## 3. Scope

### 3.1 In

| # | Deliverable |
|---|---|
| 1 | Migration 10 — the five missing `llm_calls` columns |
| 2 | `llm/cost.py` — `LLMCallRecord` + the two price tables |
| 3 | `llm/cache.py` — record/replay fixture cache |
| 4 | `gateway.py` — `calls` out-param, replay seam, chain bypass |
| 5 | `llm/telemetry.py` — the glue that prices a record and writes the row |
| 6 | `db/repositories/llm_calls.py` — accept the five new columns |

### 3.2 Out

Vision, sanitize, structured-output retry, the graph, Langfuse. `trace_id` is added as a **column**
because §2.9 specifies it and one migration is cheaper than two, but nothing writes it until M6.5.

### 3.3 Item 3.1 (`tools/registry.py`) is deferred to M7 — recorded, not dropped

`ToolSpec(name, args_model, return_model, side_effect, mcp_exposed)` has no consumer inside M3: the
gate names cost, the reducer trap, escalation and replay, none of which route through a tool. Its
real consumers are M7's MCP surface (`mcp_exposed`, and `test_no_write_tools_in_mcp.py`, which is one
of the six portfolio tests) and M4's memory reads. Designing the interface now means designing it
against imagined callers — CLAUDE.md §2, "no abstractions for single-use code".

What M3 genuinely needs from item 3.2 is the **retry taxonomy**, which M3-B ports into
`llm/structured.py`. The registry moves to M7 with this paragraph as the reason.

---

## 4. Migration 10 — the five missing columns

```
ALTER TABLE app.llm_calls
  ADD COLUMN invoice_id CHAR(32) NULL REFERENCES app.invoices(id),
  ADD COLUMN batch_id   CHAR(32) NULL REFERENCES app.batches(id),
  ADD COLUMN thread_id  TEXT NULL,
  ADD COLUMN agent      TEXT NULL,
  ADD COLUMN trace_id   TEXT NULL;
```

`CHAR(32)` because that is what `invoices.id` and `batches.id` already are (measured: both
`CHAR(32)`, UUID hex) — a `BIGINT` FK would not even create.

**All five nullable, deliberately.** A call made by `scripts/run_one.py` before any invoice row
exists has no `invoice_id`; an eval harness call has neither. `NOT NULL` here would force callers to
invent an identity, which is how telemetry tables end up full of sentinel rows.

No new `GRANT`: migration 09 granted `SELECT, INSERT, UPDATE` at table level to `invoice_app`, and
table-level grants cover columns added later. A test asserts the runtime role can still insert.

---

## 5. Modules

### 5.1 `llm/cost.py` — pure, no I/O

```python
@dataclass(frozen=True)
class LLMCallRecord:
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    attempt: int
    succeeded: bool
    error: str | None = None
```

Frozen for the same reason `Finding` is: a record of what happened must not be editable after the
fact. This is the `LLMCallRecord` design doc §3.1 already names in `EnrichmentState`.

Two tables, and the distinction between them is the whole point of the module:

- `ACTUAL_PRICES` — what we pay. `$0` for every Groq model, every Gemini model, and every
  OpenRouter model whose name ends `:free`. `openai/*` at list price.
- `SHADOW_PRICE` — one model only, `gpt-4o-mini` at $0.15/$0.60 per 1M (plan §2.2, re-measured
  2026-07-29 against LiteLLM's map — agrees to the cent, see §2.3).

`Decimal`, never `float` — the column is `Numeric` and per-invoice figures live around $0.0005.

An unmapped model must return `Decimal(0)` and **not raise**: a pricing lookup is telemetry, and
telemetry that can kill an invoice run is worse than telemetry that under-reports. The under-report
is visible in the report; a crash is a lost invoice.

### 5.2 `llm/cache.py` — the record/replay store

**Key.** `sha256` over `json.dumps(..., sort_keys=True, ensure_ascii=False)` of:

```
{model, messages, temperature, max_tokens, json_mode, reasoning_effort, image_digests}
```

`model` is in the key (decided 2026-07-29): a response recorded from Groq must not replay as though
Gemini produced it, because `llm_calls.model`/`provider` feed the cost report and M4.1's
`exclude_model`. `image_digests` is a list of `sha256` of each raw image, present so M3-B's vision
calls key correctly; it is `[]` for every text call and costs nothing now.

**Layout.** `tests/fixtures/llm_cache/<key[:16]>.json`, one file per call:

```json
{"key": "...", "model": "...", "messages": [...], "response": "...",
 "usage": {"prompt_tokens": 812, "completion_tokens": 97},
 "error": null, "recorded_at": "2026-07-29T…"}
```

Messages are stored in the clear so a reviewer can read what was actually sent. **Only anonymised or
synthetic invoices may ever be recorded** — this milestone opened by finding six real vendor PDFs
committed under `tests/fixtures/`, and a replay fixture is exactly the same kind of quiet
plaintext container.

**Three modes** (`Settings.llm_mode`, already validated as a `Literal`):

| Mode | Behaviour |
|---|---|
| `live` | cache untouched |
| `record` | call live, write the fixture, return the response |
| `replay` | read only; **never** touch the network |

**A replayed failure is a first-class entry.** `error` non-null means replay *raises* that error
instead of returning text. Without it, §2.9's "record the failed call too" is untestable in CI: no
keys means no real 429, so the failure path would ship unmeasured — the exact shape of the M1/M2
defects.

**Miss in replay raises `LLMReplayMiss`**, naming the key, the model chain tried, and the command to
re-record. Never a silent fallthrough to live: a fallthrough turns a missing fixture into a
mysterious CI failure on a runner with no keys, or worse, a passing test that quietly spent money.

### 5.3 `gateway.py` — three changes, each small

1. **`calls: list[LLMCallRecord] | None = None` out-param** on `generate`, `generate_with_model`
   and `_generate_impl`, following the `used_model` out-param idiom already at `generate_stream:245`
   and for the same stated reason.

   It is a **list, not a dict**. §2.9 requires every attempt to be recorded, and one call can make
   several: two rate-limited keys then a success is three rows. A single out-dict can only hold the
   last one, which would record the success and silently drop the two failures that burned tokens —
   re-introducing exactly the error §2.9 warns about. `EnrichmentState.llm_calls` is likewise an
   `Annotated[list, operator.add]`.

2. **Replay seam inside the model loop**, replacing the direct `litellm.completion(...)` call with a
   dispatch on mode. Inside, so chain-walking is exercised in replay (§2.1 measured why).

3. **`_resolve_chain` skips `_usable_models` when mode is `replay`.** Without it the chain is empty
   on a keyless runner and nothing runs. `record` and `live` keep the filter — both need real keys.

Key rotation is skipped in replay: there are no keys to rotate, and a cached response is not
key-dependent.

### 5.4 `llm/telemetry.py` — glue, and the layering reason

```python
def record(conn, records: Sequence[LLMCallRecord], *, node: str,
           invoice_id=None, batch_id=None, thread_id=None,
           agent=None, trace_id=None) -> None
```

Prices each record via `llm/cost.py` and writes it through
`db.repositories.llm_calls.record_call`, whose signature grows the five keyword-only columns from
§4 (all defaulting to `None`, so M2-A's existing callers and tests keep working). It exists as its
own module so the dependency points
**downward from glue** — `telemetry` imports both `llm.cost` and `db.repositories`, while the
gateway imports only the pure dataclass and `db/` keeps knowing nothing about `llm/`. Putting the
pricing call inside the repository would invert that.

Three callers by M4 (vision, classify, audit), so it is not a single-use abstraction.

---

## 6. Tests

### 6.1 The anchor

`make check` green **with `LLM_MODE=replay` and every API key unset** — the M3 gate condition this
spec owns. Not "tests pass": tests pass under `monkeypatch` too. The measurement is the whole suite
running with the environment CI actually has.

### 6.2 Mutation verification

The M1/M2 lesson is that a guard is worth nothing until it has been seen red. Each of these must be
verified by breaking the source, not by inspection:

| Mutation applied to source | Guard that must go red |
|---|---|
| `_resolve_chain` keeps `_usable_models` in replay | `test_replay_runs_with_no_api_keys` |
| Replay seam moved outside `_generate_impl` (wrap `generate`) | `test_replay_walks_the_model_chain` |
| `cost_usd` computed by `litellm.completion_cost` | `test_free_tier_costs_zero_and_shadow_does_not` |
| `model` dropped from the cache key | `test_same_messages_different_model_are_distinct_keys` |
| `image_digests` dropped from the cache key | `test_image_bytes_change_the_key` |
| Failed attempts not appended to `calls` | `test_fallback_records_every_attempt` |
| Failed attempt records `prompt_tokens=0` instead of `token_counter` | `test_failed_call_still_counts_input_tokens` |
| Replay miss falls through to live | `test_replay_miss_raises_and_never_calls_litellm` |
| `shadow_cost_usd` priced with the actual-price table | `test_shadow_price_is_gpt4o_mini_regardless_of_model` |
| Migration 10 reverted | `test_llm_calls_has_every_column_in_design_2_9` |

### 6.3 Drift guards, not mutation guards

- `test_hardcoded_shadow_price_still_matches_litellm` — asserts LiteLLM's `gpt-4o-mini` map still
  produces $0.000270 for 1000/200. Failing means the reference price moved, which is a decision to
  take, not a bug to fix silently (§2.3).
- `test_free_model_list_covers_the_configured_chain` — every model in `DEFAULT_MODEL_CHAIN` resolves
  in `ACTUAL_PRICES`, so adding a model to the chain without pricing it fails at CI rather than
  recording a wrong $0.

### 6.4 Count

≈24 tests. The count is not the claim; the ten red mutations are.

---

## 7. Success criteria

1. `make check` green with `LLM_MODE=replay` and **no API keys in the environment**.
2. `alembic upgrade head` applies migration 10; `alembic revision --autogenerate` afterwards emits an
   **empty** migration (the M1.6 guard still holds).
3. A recorded fallback — two rate-limited keys then a success — produces **three** `llm_calls` rows,
   two with `succeeded=false` and non-zero `prompt_tokens`.
4. A Groq call records `cost_usd = 0` and `shadow_cost_usd > 0`; a `gpt-4o-mini` call records both
   equal.
5. Ten mutations from §6.2 verified red, one at a time.
6. `ruff` and `mypy` clean over `src/`, `tests/`, `scripts/`, `migrations/` — the M2-C exit found
   `scripts/` had never been linted; the lint target covers all four today and must stay that way.

---

## 8. Open questions — recorded, not silently dropped

1. **Does replay need to cover streaming?** `generate_stream` is used by nothing in this repo yet
   (M5's SSE is per-node progress, not tokens). Left uncovered; if M8's demo streams, this returns.
2. **Cache invalidation has no story.** A changed prompt silently misses and raises `LLMReplayMiss`,
   which is the right failure but a noisy one during M3-B/M3-C development. If it becomes friction,
   the answer is a `make record` target, not a softer miss.
3. **`attempt` numbering across a chain.** A call that tries 2 keys on model A then succeeds on
   model B: is that attempts 1,2,3, or per-model 1,2 then 1? This spec takes **monotonic per call**
   (1,2,3) so `COUNT(*) GROUP BY` over a call's rows equals its attempt count. Revisit if M6's cost
   report wants per-model retry rates.
4. **Whether `token_counter` stays offline for models added later.** Measured true for all six today
   (§2.4); a model outside LiteLLM's map may need a network fetch. The drift guard in §6.3 catches
   the pricing half of this, not the tokenizer half.
