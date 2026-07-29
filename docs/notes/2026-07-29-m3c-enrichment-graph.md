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

## 5. Spec §7 Success Criteria Verification

All nine criteria verified verbatim from Spec §7:

1. **Criteria 1**: `build_serde()` produces a `JsonPlusSerializer` whose allowlist includes `Finding`, `InvoiceData`, `InvoiceLine`, and `LLMCallRecord`.  
   *Verified:* `tests/test_serde.py::test_allowlisted_types_round_trip_with_their_type_intact` and `test_a_populated_enrichment_state_round_trips_with_every_type_intact` pass.
2. **Criteria 2**: `build_checkpointer()` yields a `PostgresSaver` whose `.serde` attribute carries `build_serde()`.  
   *Verified:* `tests/test_serde.py::test_checkpointer_carries_the_allowlist` passes.
3. **Criteria 3**: `route_after_validate` returns `END` on clean invoices (both XML and vision).  
   *Verified:* `tests/test_enrichment_routing.py::TestExtractionSucceeded::test_a_clean_invoice_ends` passes.
4. **Criteria 4**: `route_after_validate` returns `END` on XML invoices with findings, and `vision_extract` on vision invoices with findings (up to attempt 3).  
   *Verified:* `tests/test_enrichment_routing.py::TestExtractionSucceeded::test_xml_findings_never_retry` and `test_vision_findings_do_retry` pass.
5. **Criteria 5**: `route_after_validate` escalates a failed XML extraction to `vision_extract` when `pdf_path` is present, and to `manual_queue` when `pdf_path` is `None`.  
   *Verified:* `tests/test_enrichment_routing.py::TestExtractionFailed::test_xml_extraction_failure_climbs_to_vision_when_a_pdf_exists` and `test_xml_extraction_failure_without_a_pdf_gives_up` pass.
6. **Criteria 6**: `error_history` grows linearly (1, 2, 3 entries) across three retries, not quadratically.  
   *Verified:* `tests/test_enrichment_graph.py::test_error_history_grows_LINEARLY_not_quadratically` passes.
7. **Criteria 7**: `vision_extract_node` persists cost rows to `llm_calls` immediately via `_persist_cost` with `thread_id="enrich:{invoice_id}"` and `invoice_id=None`.  
   *Verified:* `tests/test_run_one.py::test_cost_row_exists_before_the_graph_finishes` passes.
8. **Criteria 8**: `scripts/run_one.py` executes one invoice end-to-end, persists the invoice row, and backfills `llm_calls.invoice_id` from `thread_id`.  
   *Verified:* `tests/test_run_one.py::test_run_one_links_cost_to_the_invoice` passes.
9. **Criteria 9**: `docs/diagrams/enrichment_graph.mmd` is committed and passes `test_the_committed_diagram_matches_the_graph`.  
   *Verified:* `tests/test_enrichment_graph.py::test_the_committed_diagram_matches_the_graph` passes.

---

## 6. Spec §8 Open Questions Carried Forward

1. **Vision Fixture & Real Accuracy Number**: How will the real vision fixture and baseline accuracy benchmark be established once real vision API keys are connected?
2. **Git History Purge**: When and how will the large binary PDF/XML test fixtures in git history be purged or moved to LFS?
3. **ADR `0001-three-graphs-not-one.md`**: When will ADR 0001 be formalized documenting the architectural choice of three independent graphs (`enrichment_graph`, `batch_graph`, `approval_graph`) over a single monolithic graph?
4. **Batch Graph Splice Points**: How will `batch_graph` in M4 handle error propagation and state checkpointing when invoking `enrichment_graph` concurrently across thousands of invoices?
5. **HITL State Resume**: How will `approval_graph` in M5 resume a checkpointed `EnrichmentState` from PostgreSQL after human intervention?
