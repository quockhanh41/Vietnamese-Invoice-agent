# Note: M3-A LLM Plumbing Closeout

> **Date**: 2026-07-29
> **Milestone**: M3-A (`src/invoice_agent/llm/`)
> **Status**: Completed · 376 total project tests passing

---

## 1. What Was Built

Every LLM call in the repository is now recorded with dual pricing (actual cost vs. `gpt-4o-mini` shadow price) and replayable offline without API keys:

- **`src/invoice_agent/llm/cost.py`**: Pure module introducing `LLMCallRecord` (frozen dataclass), `ACTUAL_PRICES`, `SHADOW_PRICE`, `actual_cost_usd`, and `shadow_cost_usd`.
- **`src/invoice_agent/llm/cache.py`**: Fixture cache store using SHA-256 request hashing (`make_key` with UTF-8 stability), 64-bit key verification on `load` to prevent filename collisions, disk serialization via `save`, and `LLMReplayMiss` exception.
- **`src/invoice_agent/llm/telemetry.py`**: Glue module recording priced call attempt records to `app.llm_calls` table in Postgres.
- **`src/invoice_agent/llm/gateway.py`**: Added `calls` out-param tracking, `_record_attempt` with latency measurement and `litellm.token_counter` fallback counting for 429 errors, `_complete` seam inside attempt loop, and dual replay bypasses in `_resolve_chain` and `_generate_impl`.
- **`migrations/versions/b23d0a366d02_m3a_10_llm_calls_context.py`**: Migration 10 adding `invoice_id` (`sa.Uuid()`), `batch_id` (`sa.Uuid()`), `thread_id`, `agent`, `trace_id` to `app.llm_calls`.

---

## 2. Measured Baseline (§2 Specs)

1. **§2.1 Keyless Chain Resolution:** Measured 2026-07-29 — with zero API keys configured, `_usable_models` filters away all six chain models returning `[]`. Replay bypass in `_resolve_chain` and `_generate_impl` (`api_keys = [""]`) allows keyless test execution in CI.
2. **§2.2 Free Tier vs `completion_cost`:** Measured 2026-07-29 against litellm 1.93.0 — `litellm.completion_cost` misprices free-tier Groq calls at $0.000748 and raises "unmapped" errors on OpenRouter `:free` models. Dual explicit price tables (`ACTUAL_PRICES` vs `SHADOW_PRICE`) prevent false pricing and runtime exceptions.
3. **§2.3 Reference Shadow Price:** Measured 2026-07-29 — `gpt-4o-mini` reference rate is $0.15 input / $0.60 output per 1M tokens ($0.00027 for a 1000 in / 200 out call). Float output from litellm (`0.00026999999999999995`) is quantized using `Decimal("0.00000001")` in drift guard tests.
4. **§2.4 Offline Token Counter:** Measured 2026-07-29 — `litellm.token_counter` executes offline with zero network calls across all six configured models in `DEFAULT_MODEL_CHAIN`.

---

## 3. Verified Mutation Results

All 12 source code mutations were executed and verified to turn target test cases **RED**:

| # | Mutation Applied | Target Test Case | Result |
|---|---|---|---|
| 1 | Delete `if self.mode == "replay": return models_to_try` in `_resolve_chain` | `test_replay_runs_with_no_api_keys` | **RED** |
| 2 | Restrict replay lookup in `_complete` to `models_to_try[0]` | `test_replay_walks_the_model_chain` | **RED** |
| 3 | Make `actual_cost_usd` delegate to `litellm.completion_cost` | `test_free_tier_costs_zero_and_shadow_does_not` | **RED** |
| 4 | Drop `"model"` from `make_key` payload | `test_same_messages_different_model_are_distinct_keys` | **RED** |
| 5 | Drop `"image_digests"` from `make_key` payload | `test_image_bytes_change_the_key` | **RED** |
| 6 | Delete `_record_attempt` call in `except Exception` branch | `test_fallback_records_every_attempt` | **RED** |
| 7 | Replace `_count_tokens` fallback with `prompt_tokens = 0` in `_record_attempt` | `test_failed_call_still_counts_input_tokens` | **RED** |
| 8 | Replace `raise cache.LLMReplayMiss(...)` with fallthrough in `_complete` | `test_replay_miss_raises_and_never_calls_litellm` | **RED** |
| 9 | Price `shadow_cost_usd` with `ACTUAL_PRICES` instead of `SHADOW_PRICE` | `test_shadow_price_is_gpt4o_mini_regardless_of_model` | **RED** |
| 10 | Execute `alembic downgrade -1` | `test_has_every_column_in_design_2_9` | **RED** |
| 11 | Delete `doc.get("key") != key` check in `cache.load` | `test_a_truncated_filename_collision_is_a_miss_not_a_wrong_answer` | **RED** |
| 12 | Replace `api_keys = [""] if self.mode == "replay"` with `self._keys_for(...)` | `test_replay_runs_with_no_api_keys` | **RED** |

---

## 4. Spec Success Criteria Verification

- [x] **Criterion 1 (Replay Gate):** Test suite passes cleanly under `LLM_MODE=replay` with zero API keys.
- [x] **Criterion 2 (Attempt Recording):** Every attempt (including 429 rate limit failures) records prompt tokens, completion tokens, latency, attempt index, error string, and dual costs.
- [x] **Criterion 3 (Dual Pricing):** `cost_usd` records $0 for free-tier calls, while `shadow_cost_usd` records non-zero reference pricing.
- [x] **Criterion 4 (Clean Autogenerate):** `alembic revision --autogenerate` produces zero diff operations against `db/models.py`.
- [x] **Criterion 5 (Runtime Role Grants):** DB recording tests run under non-privileged `runtime_conn` role.
- [x] **Criterion 6 (Mutation Guards):** All 12 mutation guards verified RED.

---

## 5. Recorded Open Technical Questions (Spec §8)

1. **Streaming Replay Coverage:** `generate_stream` currently does not record or replay fixtures. (Non-blocking for M3-A; deferred).
2. **Cache Lifecycle Strategy:** Fixture cache files are immutable; re-recording when prompts change requires explicit `LLM_MODE=record`.
3. **Monotonic Attempt Indexing:** Attempt counter increases monotonically `1, 2, 3...` across model chain fallbacks.
4. **Token Counter Offline Behavior for Future Models:** `litellm.token_counter` is offline for current 6 models; future additions must be re-tested before adoption.
