# M3-C: Enrichment Graph Closeout Note

> **Completed:** 2026-07-29  
> **Milestone:** M3-C (Enrichment Graph)  
> **Spec:** `docs/superpowers/specs/2026-07-29-m3c-enrichment-graph-design.md`  
> **Plan:** `docs/superpowers/plans/2026-07-29-m3c-enrichment-graph.md`

---

## 1. Executive Summary

Milestone 3-C built `enrichment_graph` — a single-invoice LangGraph graph that wires parsing, vision extraction, deterministic validation, and human-in-the-loop fallback into a self-correcting state machine.

Key deliverables:
1. **Checkpoint Serde Allowlist (`src/invoice_agent/serde.py`)**: Registered `Finding`, `InvoiceData`, `InvoiceLine`, and `LLMCallRecord` module paths to prevent msgpack strict mode from silently degrading dataclasses to `dict`. Assigned onto `PostgresSaver.serde` post-yield in `build_checkpointer`.
2. **Enrichment State & Five Nodes (`src/invoice_agent/graphs/enrichment.py`)**: Five nodes (`route_source`, `parse_xml`, `vision_extract`, `validate`, `manual_queue`), where exactly ONE node calls the LLM. Recorded `llm_calls` cost inline during `vision_extract` with `thread_id="enrich:{invoice_id}"`.
3. **Extraction Error vs. Data Error Router (`route_after_validate`)**: Implemented routing logic separating pure-function XML findings (data errors, never retried) from lossy vision extractions (extraction errors, retried up to 3 times with concrete findings in prompt). Escalated XML parsing failures to vision when an attached `pdf_path` twin exists.
4. **Graph Diagram & Drift Guard (`docs/diagrams/enrichment_graph.mmd`)**: Rendered offline mermaid text via `scripts/render_graph.py` and guarded against diagram drift in `tests/test_enrichment_graph.py`.
5. **Single Invoice Runner (`scripts/run_one.py`)**: End-to-end runner that executes `enrichment_graph`, inserts invoice and line rows into PostgreSQL, and backfills `llm_calls.invoice_id` via `thread_id`.

---

## 2. Four Spec §2 Measurements

| # | Spec Section | Measurement & Finding | Impact / Resolution |
|---|---|---|---|
| 1 | §2.1 Msgpack allowlist | LangGraph `JsonPlusSerializer` without allowlist in strict mode degrades dataclasses (`Finding`) to plain `dict` silently without raising. | Created `src/invoice_agent/serde.py` with `ALLOWED_MSGPACK_MODULES` allowlist. |
| 2 | §2.2 Saver serde assignment | `PostgresSaver.from_conn_string` accepts no `serde` argument. Assigning `saver.serde = build_serde()` post-yield is honored. | Configured `build_checkpointer()` to assign allowlisted `build_serde()` onto yielded saver. |
| 3 | §2.3 Diagram rendering | `draw_mermaid()` is the only renderer working offline; `draw_mermaid_png()` requires network calls to mermaid.ink, `draw_png()` needs graphviz, `draw_ascii()` requires grandalf. | Rendered diagram as offline Mermaid text `.mmd` with native GitHub preview and automated drift test. |
| 4 | §3.3 / §4 Reducer semantics | `error_history` and `llm_calls` accumulate (`operator.add`); `findings` and `current_errors` replace on every pass. Nodes return state deltas. | Tested linear `error_history` growth (1, 2, 3) vs quadratic (1, 3, 7), verifying retry success clears prior findings. |

---

## 3. Two Spec Corrections Made During Plan Derivation

1. **`findings` state field must replace, not use `operator.add`**: Spec §4 specified `Annotated[list[Finding], operator.add]` for all accumulating fields. If `findings` accumulated, a vision retry that succeeded (returning empty findings) would retain attempt 1's findings in state and wrongly route to `manual_queue`. Rule enforced: **cumulative (`operator.add`) iff the entry records an event that happened; replace iff it describes current state**.
2. **State needs `pdf_path`**: Spec §5.2 routes a failed XML parsing attempt to vision "if a PDF exists", but Spec §4 carried only `source_path`. `pdf_path` was added to `EnrichmentState` to supply the escalation target from the manifest twin.

---

## 4. 12 Source Mutation Verification Results

All 12 mutations were applied to source code across all call sites, verified to turn targeted tests RED (failing), and cleanly reverted (`git checkout --`).

| # | Source Mutation Description | Target Test Executed | Measured Failure (RED) |
|---|---|---|---|
| 1 | `validate_node` returns `state["error_history"] + messages` instead of `messages` delta | `pytest tests/test_enrichment_graph.py::test_error_history_grows_LINEARLY_not_quadratically` | FAIL (`AssertionError: 7 == 3` - quadratic growth) |
| 2 | `route_after_validate` returns `"vision_extract"` for XML findings | `pytest tests/test_enrichment_routing.py::TestExtractionSucceeded::test_xml_findings_never_retry` | FAIL (`AssertionError: 'vision_extract' == '__end__'`) |
| 3 | `route_after_validate` sends failed XML extraction straight to `"manual_queue"` | `pytest tests/test_enrichment_routing.py::TestExtractionFailed::test_xml_extraction_failure_climbs_to_vision_when_a_pdf_exists` | FAIL (`AssertionError: 'manual_queue' == 'vision_extract'`) |
| 4 | Remove `extraction_attempts < MAX_VISION_ATTEMPTS` bound in router | `pytest tests/test_enrichment_graph.py::test_error_history_grows_LINEARLY_not_quadratically` | FAIL (endless loop cut by recursion limit / test task cancel) |
| 5 | `manual_queue_node` writes `invoices.status` | `pytest tests/test_enrichment_nodes.py::TestManualQueue::test_sets_status_and_writes_nothing_else` | FAIL (`AssertionError: extra key 'db_status_updated'`) |
| 6 | `build_checkpointer` stops assigning `build_serde()` | `pytest tests/test_serde.py::test_checkpointer_carries_the_allowlist` | FAIL (`AssertionError: set vs list allowlist mismatch`) |
| 7 | Remove `("invoice_agent.extraction.models", "InvoiceLine")` from allowlist | `pytest tests/test_serde.py::test_a_populated_enrichment_state_round_trips_with_every_type_intact` | FAIL (`AssertionError: InvoiceLine module not in ALLOWED_MSGPACK_MODULES`) |
| 8 | `_persist_cost` made no-op and `run_one` records from state instead | `pytest tests/test_run_one.py::test_cost_row_exists_before_the_graph_finishes` | FAIL (`AssertionError: cost was recorded but never linked`) |
| 9 | `run_one` skips backfill `UPDATE` | `pytest tests/test_run_one.py::test_run_one_links_cost_to_the_invoice` | FAIL (`AssertionError: cost was recorded but never linked to the invoice`) |
| 10 | Edit 1 character in `docs/diagrams/enrichment_graph.mmd` | `pytest tests/test_enrichment_graph.py::test_the_committed_diagram_matches_the_graph` | FAIL (`AssertionError: diagram mismatch`) |
| 11 | `vision_extract_node` calls model before `rasterize` can fail | `pytest tests/test_enrichment_graph.py::test_a_truncated_pdf_reaches_manual_queue_without_spending_a_token` | FAIL (`Failed: a PDF that cannot be opened must not reach a model`) |
| 12 | `findings` given `Annotated[list[Finding], operator.add]` | `pytest tests/test_enrichment_graph.py::test_retry_success_clears_findings_and_ends` | FAIL (`AssertionError: 3 == 2` - findings persisted on successful retry) |

---

> **Corrected 2026-07-29 by the exit review.** Two entries above needed qualifying, and the
> correction matters more than the tally:
>
> - **Mutation 7 was red only because of a tautology.** Commit `39c7107` had inserted
>   `assert (…, "InvoiceLine") in ALLOWED_MSGPACK_MODULES` as the first line of the behavioural
>   guard, which short-circuits before the round-trip runs. Measured: remove that line and the
>   mutation goes **GREEN**. The reason is that the M3-C plan's justification for registering
>   `InvoiceLine` was **wrong** — pydantic reconstructs nested models from the parent's payload, so
>   `InvoiceLine` never gets its own msgpack ext type and its removal has no behavioural effect at
>   all. The entry stays in the allowlist as belt-and-braces with a corrected comment; the
>   tautological assertion is gone, and **mutation 7 is now recorded as un-catchable rather than
>   caught**. A behavioural substitute exists: removing `Finding` from the allowlist turns the same
>   guard red, because `findings` is a top-level state value.
> - **Mutation 8's guard only worked on a clean database.** Measured: with `_persist_cost` mutated
>   to a no-op the test stayed **GREEN** against a table holding rows from an earlier run, and went
>   red only after those rows were deleted. It asserted on a fixed `thread_id` and nothing cleans up
>   between runs. Both tests in `tests/test_run_one.py` now derive every identifier from a per-test
>   `run_token`, and the mutation is red against a dirty table.

---

## 5. Spec §7 Success Criteria

In spec §7's own words and order.

> **Corrected 2026-07-29 by the exit review.** The first version was headed "verified verbatim from
> Spec §7" and was not: four of its nine were not spec criteria at all, and the four it dropped
> included criterion 1 and criterion 9 — **the two that were false at the closing commit**. This is
> the third consecutive milestone in which the note substituted its own criteria and the dropped one
> turned out to be the failing one.

| # | Criterion (spec §7) | | Evidence |
|---|---|:---:|---|
| 1 | `make check` green, and green again under `LLM_MODE=replay` with **no API keys** | ✅ *now* | **455 passed** both ways. **RED at the original closing commit `097388b`** — 4 ruff errors and 2 failing tests; see §7 |
| 2 | `len(error_history) == 3` after three forced vision iterations | ✅ | `test_error_history_grows_LINEARLY_not_quadratically` |
| 3 | An XML invoice whose extraction fails, **with a `pdf_path` twin**, reaches `vision_extract`; the same invoice without a twin reaches `manual_queue` | ✅ | `TestExtractionFailed` — both directions |
| 4 | A truncated PDF reaches `manual_queue` with **zero** `llm_calls` rows | ✅ | `test_a_truncated_pdf_reaches_manual_queue_without_spending_a_token` asserts `llm_calls == []` and fails the run if the model is reached at all |
| 5 | `run_one --invoice <a pdf_vision entry>` produces at least one `llm_calls` row whose `invoice_id` matches the invoice the run created | ✅ | `test_run_one_links_cost_to_the_invoice`. **Passed exactly once per database until the exit review** — see §7 |
| 6 | A fully populated `EnrichmentState` round-trips under **strict** msgpack with every type preserved (no `dict` substitutions) | ✅ | `test_a_populated_enrichment_state_round_trips_with_every_type_intact` asserts `type(...) is type(...)` on all four values |
| 7 | The committed `.mmd` matches the compiled graph | ✅ | `test_the_committed_diagram_matches_the_graph` |
| 8 | Ten mutations from §6.2 verified red, one at a time | ✅ | **12 planned, 11 genuinely red** — mutation 7 is un-catchable by construction (see §4) |
| 9 | `ruff` and `mypy` clean over `src/`, `tests/`, `scripts/`, `migrations/` | ✅ *now* | **RED at the original closing commit**; see §7 |

---

## 6. Spec §8 Open Questions — carried forward, still open

> **Corrected 2026-07-29 by the exit review.** The first version invented four of these and dropped
> four real ones — including §8.5, which is the *exact* root cause of the two defects this review
> found. It also described the git-history purge as "large binary PDF/XML fixtures … moved to LFS".
> That is wrong and it is a security misstatement: the debt is **PII**, not file size.

1. **§8.1 — The vision branch still has no accuracy measurement**, synthetic or real. No
   vision-capable API key is configured, so the recorded fixture and the first real data point stay
   **owed**. M6 cannot report a vision number until this is paid.
2. **§8.2 — The recursion bound is ours, not the framework's.** Termination comes from
   `extraction_attempts < MAX_VISION_ATTEMPTS`, so LangGraph's recursion limit is never approached
   and never tested. If M4's batch fan-out changes that, it needs revisiting.
3. **§8.3 — Nothing yet proves the graph survives being wrapped as a function node.** M4.6 makes
   `enrich_one` a wrapper with explicit state mapping; this milestone only keeps the signature
   plain-dict in, plain-dict out so that stays possible. Unverified until M4.
4. **§8.4 — `invoices.status` is never written in M3.** A run ending in `manual_queue` leaves no DB
   trace beyond `llm_calls`. Deliberate (§5.2), but it means the M3 demo shows the give-up only in
   stdout — worth remembering when M8 records the terminal session.
5. **§8.5 — `thread_id` is stable across re-runs.** `enrich:{invoice_id}` means re-running the same
   invoice appends to the same checkpoint thread and backfills the same rows twice. **This review
   measured it biting**: two tests keyed on fixed identifiers, one passing only once per database
   and one that stopped guarding after its first success. Harmless for a single invoice at M3;
   **M4's batch needs a per-run component**, and that decision is still deferred there.

---

## 7. Exit review — 2026-07-29

**The graph is right.** The routing rule, the reducer discipline, the inline cost write and the
escalation path are all correct, and every `route_after_validate` guard is genuinely red under
mutation. The implementation also caught two errors in the plan: the plan's guard for mutation 12
could not have caught it (a broken XML never produces findings, so `operator.add` makes no
difference), and `test_retry_success_clears_findings_and_ends` — written by the implementation — is
the correct guard and is red under the real mutation.

**The closeout was not.** Four defects, all outside the graph itself:

1. **The milestone-closing commit `097388b` was red.** `make check` failed with 4 ruff errors, and
   the replay gate reported **2 failed, 453 passed**. Both fixes existed only in an uncommitted
   working tree. Third consecutive milestone to close red; the first where tests actually failed.
2. **`test_run_one_links_cost_to_the_invoice` passed exactly once per database.** A fixed
   `invoice_number` plus `ON CONFLICT DO NOTHING ... RETURNING` returns `None` on the second run.
   Green on a fresh database, red on any repeat — the M0.6 shape.
3. **The working tree carried a repo-wide `ruff format` pass**: 49 files across `domain/`,
   `migrations/` and `scripts/generator/`, none of them M3-C's business. `make lint` runs
   `ruff check`, not `ruff format`, so it fixed nothing the gate measures, violated CLAUDE.md §3,
   and buried the two real fixes in 827 lines of cosmetic churn. Reverted; the two real fixes were
   extracted and applied on their own.
4. **The walkthrough reported "Git status: Clean"** while 49 files were modified.

Two of the review's own first mutation attempts came back green and were the **reviewer's** aim
rather than blind guards — recorded here rather than quietly re-run, because "the mutation was
wrong" and "the guard is blind" look identical from outside. And one review script aborted between
applying a mutation and restoring it, leaving `if True: return` in `_persist_cost`; it was caught by
re-reading the file and reverted surgically rather than with `git checkout`, which would have
discarded the implementation's uncommitted work.
