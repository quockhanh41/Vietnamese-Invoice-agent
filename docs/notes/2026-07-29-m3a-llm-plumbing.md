# Note: M3-A LLM Plumbing Closeout

> **Date**: 2026-07-29
> **Milestone**: M3-A (`src/invoice_agent/llm/`)
> **Status**: Completed, then hardened by exit review the same day · **380 tests passing**
> (376 at implementation; the 4 added are the review's guards — see §6)

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

**Re-verified independently 2026-07-29 (exit review).** All 12 were re-broken from scratch rather
than re-read — 11 by script against `gateway.py` / `cache.py` / `cost.py`, and #10 by a real
`alembic downgrade -1`. **12/12 red.** The migration also round-trips: `downgrade -1` turns the guard
red, `upgrade head` turns it green again.

The exit review then added four more, for defects the table above could not have caught because no
test covered the behaviour at all (see §6):

| # | Mutation Applied | Target Test Case | Result |
|---|---|---|---|
| 13 | Stop saving a fixture when `litellm.completion` raises in `record` mode | `test_record_mode_writes_a_fixture_for_a_FAILED_call` | **RED** |
| 14 | Same mutation, against the record→replay round trip | `test_a_failure_recorded_then_replayed_raises_the_same_way` | **RED** |
| 15 | Let `generate_stream` fall through to `litellm` under `LLM_MODE=replay` | `test_generate_stream_refuses_replay_rather_than_calling_the_network` | **RED** |
| 16 | Make `telemetry.record` skip records with `succeeded=False` | `test_gateway_records_reach_the_database_unchanged` | **RED** |

**16/16.**

---

## 4. Spec Success Criteria Verification

Stated in spec §7's own words and order. (An earlier draft of this note substituted six
differently-named criteria — dropping the "three rows" number from criterion 3 and replacing
criterion 6 with "Runtime Role Grants", which §7 does not contain. All six were in fact met, but
reporting against renamed criteria is the mechanism that produced M2-A's fabricated
`ck_vendors_tax_code_format` artefact, so the list is restored verbatim.)

- [x] **1.** `make check` green with `LLM_MODE=replay` and **no API keys in the environment** —
  measured 2026-07-29: **380 passed**, zero skips, with all seven key variables unset.
- [x] **2.** `alembic upgrade head` applies migration 10; `--autogenerate` afterwards emits an
  **empty** migration — `test_autogenerate_emits_an_empty_migration` green, and the M1.6 guard holds.
- [x] **3.** A **stubbed** fallback (live mode, `litellm.completion` monkeypatched to raise 429 twice
  then succeed) produces **three** `llm_calls` rows, two with `succeeded=false` and non-zero
  `prompt_tokens` — `test_gateway_records_reach_the_database_unchanged`, added by the exit review
  because nothing previously drove the gateway's records into the database (§6, finding 3).
- [x] **4.** A Groq call records `cost_usd = 0` and `shadow_cost_usd > 0`; a `gpt-4o-mini` call
  records both equal — `test_free_tier_records_zero_cost_and_a_non_zero_shadow` and
  `test_gpt4o_mini_actual_equals_shadow`.
- [x] **5.** Mutations verified red one at a time — **16**, up from the ten the spec asked for and
  the twelve the plan listed.
- [x] **6.** `ruff` and `mypy` clean over `src/`, `tests/`, `scripts/`, `migrations/` — the four
  directories `make lint` covers, per the M2-C exit finding that `scripts/` had never been linted.

---

## 5. Open Questions Carried Forward (Spec §8)

These are **open**, not settled. An earlier draft of this note restated three of them as
descriptions of current behaviour, which quietly converts a question into an answer and removes the
reason anyone would revisit it.

1. **Does replay need to cover streaming?** `generate_stream` has no fixture format. Nothing in the
   repo calls it (M5's SSE is per-node progress, not tokens). The exit review made it **raise**
   under `LLM_MODE=replay` rather than fall through to the network (§6, finding 2), so the hole is
   now closed *by refusal* — but the question of whether to build streaming replay is still open,
   and returns if M8's demo streams.
2. **Cache invalidation still has no story.** A changed prompt silently misses and raises
   `LLMReplayMiss`. That is the right failure but a noisy one during M3-B/M3-C development. If it
   becomes friction the answer is a `make record` target — *not* a softer miss.
3. **`attempt` numbering across a chain.** This milestone chose monotonic-per-call (1, 2, 3) so
   `COUNT(*) GROUP BY` over a call's rows equals its attempt count. Whether M6's cost report wants
   per-model retry rates instead is **unanswered**; that is the trigger to revisit.
4. **Whether `token_counter` stays offline for models added later.** Measured true for all six today.
   The pricing half of this is guarded by `test_every_configured_model_is_priced`; the **tokenizer
   half is not guarded at all**.

---

## 6. Exit Review — 2026-07-29

The implementation reported 12/12 mutations red. Re-broken independently, that claim **held** — the
first milestone in this repo where it did. M1 shipped four tests that passed while guarding nothing,
and M2 found one such defect in each of its three sub-specs.

Three real gaps were found, all in behaviour no mutation could have caught because no test covered
it. All three are fixed above.

1. **`LLM_MODE=record` could never produce a failure fixture.** `cache.save` was reachable only from
   the success path of `_complete`. But spec §5.2's sole surviving justification for
   `CachedResponse.error` is M3-C replaying the escalation path, where a *failing* vision call is
   the point — so those fixtures would have had to be hand-written JSON, i.e. the feature was
   half-built and its stated purpose undeliverable. **This originated in the plan**, which had the
   same shape; the implementation reproduced it faithfully. `_complete` now saves on the failure
   path too. Note the interaction with key rotation, which is deliberate: every key for a model
   shares one cache key, so a later successful key overwrites the failure fixture — replay
   reproduces a model's *outcome*, not the retry dance that reached it.
2. **`generate_stream` bypassed the replay seam entirely.** `litellm.completion` has two call sites;
   only `_complete` sat behind the seam. "Replay never touches the network" was therefore true of
   `generate()` but not of the class, with nothing stopping M5 or M8 from adding a caller and
   silently breaking it on a keyless runner. It now raises `LLMReplayMiss` under replay.
3. **Nothing exercised gateway → telemetry → database.** The gateway half asserted three *records*,
   the telemetry half asserted two *rows*, and no test joined them — so spec §7 criterion 3 was
   unmet as written. Low severity, since the shared dataclass makes a mismatch mypy-visible, but
   the criterion is a criterion. Closed by `test_gateway_records_reach_the_database_unchanged`.

**One defect in the review's own first fix, worth recording.** That new test initially failed under
the gate command while passing under `make check`: it built `Settings(_env_file=None, …)` without
pinning `llm_mode`, so the ambient `LLM_MODE=replay` leaked in and the test looked for a fixture
instead of exercising the recording path. `make_gateway()` in `tests/test_gateway.py` already pins
`"llm_mode": "live"` for exactly this reason. **A test that behaves differently under the gate
command than under `make check` is not hermetic** — and the only reason it surfaced is that both
commands were run, which is why the success criteria list them separately.
