# Note: M4-D Batch Graph & Serial Reconciliation Implementation

**Date**: 2026-08-01
**Component**: `src/invoice_agent/graphs/batch.py`, `tests/test_budget_serialization.py`
**Specification**: `docs/superpowers/specs/2026-08-01-m4d-batch-graph-design.md`
**Plan**: `docs/superpowers/plans/2026-08-01-m4d-batch-graph.md`

## Summary of Accomplishments

M4-D completes the LangGraph batch processing pipeline, assembling fan-out parallel enrichment with single-transaction serial join and order-invariant budget reconciliation.

### Delivered Features & Guarantees
1. **Gate Batch Freeze & Drift Guard (Task 1)**:
   - Added `DELIBERATE_PDF_MEMBERS = ("INV-0122", "INV-0123")` and updated comments in `scripts/generator/gate_batch.py`.
   - Protected against drift with unit tests in `tests/test_gate_batch.py`. Verified via 2 mutations.
2. **`enrich_one` Node & Correlation Key (Task 2)**:
   - Implemented `enrich_one` wrapper handling exceptions cleanly without terminating the batch.
   - Implemented per-run `thread_id_for(batch_id, invoice_id)` returning `enrich:{batch_id}:{invoice_id}`, settling M3-C debt. Verified via 3 mutations.
3. **Single-Transaction `serial_join` (Task 3)**:
   - Implemented `serial_join` enforcing canonical ordering `(issue_date, invoice_series, invoice_number)`.
   - Integrated vendor profile updates, duplicate detection, and domain budget consumption. Verified via 2 mutations.
4. **Batch Graph Assembly & Diagram Drift Guard (Task 4)**:
   - Built full LangGraph `BatchState` and `build_batch_graph()`:
     `START -> ingest -> cost_gate -> fan_out -> enrich_one -> serial_join -> route_outcomes -> finalize -> END`.
   - Updated `scripts/render_graph.py` to generate `docs/diagrams/batch_graph.mmd`. Added drift guard in `tests/test_batch_graph.py`.
5. **Portfolio Test 3 & End-to-End Gate Replay (Task 5)**:
   - Created `tests/test_budget_serialization.py`:
     - 10 arrival order shuffles inside `SAVEPOINT` rollbacks producing byte-identical balance chains (`35M -> 15M -> -5M`) and identical breach flags (`INV-0001`).
     - Re-verified ordering mutation post-Task 4.
     - End-to-end gate batch replay over 30 invoices in `LLM_MODE=replay`: 2 corrupt PDFs land in `failed`, 28 complete, exact match with `gate_batch.json`.
6. **Architecture Decision Records & Documentation (Task 6)**:
   - Created ADR `0001`: Three graphs, not one.
   - Created ADR `0003`: Fixed pipeline ordering, no LLM supervisor agent.
   - Created ADR `0004`: Serial budget reconciliation in canonical order.
