# M5-B Closeout Note: Approval Graph & HITL Loop

**Date**: 2026-08-01  
**Milestone**: M5-B — Approval graph and Human-In-The-Loop loop  
**Status**: COMPLETE  

---

## Executive Summary

Milestone M5-B implements the stateful `appr:` approval graph and integrates Human-In-The-Loop (HITL) human decision-making with the batch workflow. 

The design addresses the fundamental operational requirement of invoice approval: **a batch workflow must never block for hours while waiting for human review of flagged invoices**. By isolating approval state into distinct, checkpointed `appr:` threads in PostgreSQL, the batch graph opens approval threads for flagged invoices and completes immediately. Human reviewers can inspect, approve, edit, or reject invoices hours or days later from entirely separate processes via `submit_decision()`.

---

## Key Technical Decisions & Architecture Constraints

1. **Three Graphs Architecture (Design §1.1)**
   - `enrichment_one`: Single invoice extraction & rule evaluation.
   - `batch`: Ingestion, cost gate, parallel fan-out, serial DB join, auto-posting, and approval thread opening.
   - `appr:` Dedicated per-invoice approval thread that parks at `interrupt()`.
2. **Strict Node Isolation**
   - **`approval_gate_node`**: Contains `interrupt()` and NOTHING ELSE. Because LangGraph re-executes the node containing `interrupt()` from the top on resume across process restarts, `approval_gate_node` must have zero side-effects.
   - **`apply_decision_node`**: Manages its own connection transaction via `get_engine().begin()`. It commits flag resolutions and approval records before `post_entry_node` evaluates write-guards on a separate connection.
   - **`post_entry_node`**: Rebuilds `InvoiceData` from committed DB state and posts with `posting_authority="human_approved"`.
3. **JSON-Only State (`ApprovalState`)**
   - All state attributes (amounts, dates, UUIDs) are converted to plain JSON types at the boundary (`_jsonable`). This guarantees state deserialization across long temporal gaps and deployment schema changes.
4. **HITL & Memory Feedback (`learn_node`)**
   - Decisions feed episodic memory (`episodic_decisions`), update vendor rejection counts, and write reviewer correction notes back to `vendors.reviewer_notes` for future extraction context.
5. **Cold-Process Thread Recovery (`scripts/recover_pending.py`)**
   - Scans undecided approvals in PostgreSQL against LangGraph checkpointer snapshots. Discovers live pending threads and flags orphaned approval records (undecided rows with no live interrupt).

---

## Verification & Test Results

### 1. Unit & Graph Tests (`tests/test_approval_graph.py`)
- 25/25 tests passing covering snapshot serialization, static/dynamic interrupts, flag resolution, write-guard enforcement, rejection recording, memory feedback, seam validation, and graph topology drift protection.

### 2. Batch Graph Integration (`tests/test_batch_graph.py`)
- 11/11 tests passing verifying that `open_approval` parks flagged invoices into Postgres-checkpointed `appr:` threads without blocking batch finalization.

### 3. Recovery Tool Verification (`tests/test_recover_pending.py`)
- 2/2 tests passing from cold subprocesses confirming detection of parked threads and orphan detection.

### 4. Portfolio Test 1: Durability Across Process Restarts (`tests/test_hitl_survives_restart.py`)
- **10/10 runs passed (100% deterministic success)**.
- Verified that thread state survives `kill -9` signal during interrupt. Resuming via `submit_decision` in a fresh process executes `approval_gate` (count=2), leaves prior node `suggest` untouched (count=1), and generates exactly **ONE** journal entry and **ONE** audit grant.

### 5. Portfolio Test 4: Non-Blocking Batch (`tests/test_batch_not_blocked_by_pending.py`)
- **Passed on 27-invoice gate batch**.
- Batch status completed in 2.95s while 15 invoices posted automatically and 12 flagged invoices parked in pending approval threads.

---

## Gate Batch Results Summary

| Metric | Measured Value |
| :--- | :--- |
| **Total Invoices Processed** | 27 |
| **Auto-Posted Invoices** | 15 |
| **Parked for Approval** | 12 |
| **Batch Completion Status** | `completed` (Non-blocking) |
| **Total Test Suite** | 619 passed |

---

## Deliverables & Artifacts

- `src/invoice_agent/graphs/approval.py`: `ApprovalState`, approval graph builder, nodes (`load_snapshot`, `suggest`, `approval_gate`, `apply_decision`, `post_entry`, `record_rejection`, `learn`), `submit_decision`.
- `src/invoice_agent/db/repositories/approvals.py`: DB helpers (`record_context`, `get_by_invoice`).
- `scripts/recover_pending.py`: Cold-process recovery and orphan scanner.
- `docs/diagrams/batch_graph.mmd` & `docs/diagrams/approval_graph.mmd`: Updated graph diagrams.
- Test suite: `tests/test_approval_graph.py`, `tests/test_batch_graph.py`, `tests/test_recover_pending.py`, `tests/test_hitl_survives_restart.py`, `tests/test_batch_not_blocked_by_pending.py`.
