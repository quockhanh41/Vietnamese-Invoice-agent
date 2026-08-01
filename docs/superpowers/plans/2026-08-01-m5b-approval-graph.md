# M5-B: the approval graph and the HITL loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the human decision that authorises a journal entry durable across a process death — a reviewer may decide hours later, from a different process, after a deploy — and close the loop where that decision feeds vendor and episodic memory.

**Architecture:** `graphs/approval.py` compiles a six-node graph whose `approval_gate` contains `interrupt()` and nothing else, because M1.5 measured that the node containing the interrupt re-runs from the top on resume. Every write lives after the gate; `suggest` lives before it. `batch.py` gains `open_approval`, which writes an `approvals` row and drives the graph to its interrupt, then returns so the batch can finish.

**Tech Stack:** Python 3.13 · LangGraph 1.2.9 (pinned) + `PostgresSaver` 3.1.0 (pinned) · SQLAlchemy 2 Core · Postgres 17 · pytest

**Spec:** [`docs/superpowers/specs/2026-08-01-m5b-approval-graph-design.md`](../specs/2026-08-01-m5b-approval-graph-design.md)

## Global Constraints

Copied from spec §0. Every task's requirements implicitly include these.

1. **The node containing `interrupt()` re-runs from the top on resume.** Measured 2026-07-27 at M1.5 on langgraph 1.2.9: node-before-gate = 1, node-containing-gate = 2. `approval_gate` therefore contains `interrupt()` **and nothing else**.
2. **`apply_decision` must COMMIT before `post_entry` reads, on a different connection.** A guard reading its own uncommitted write verifies nothing and passes every ordinary test.
3. **`ApprovalState` carries plain JSON only** — no `InvoiceData`, no `Finding`. An `appr:` thread lives days; its checkpoint must survive a deploy.
4. **No LLM anywhere in this graph.** `suggest` is on the path the batch waits for (design §5.1).
5. **`decided_by` is a BARE name.** `approvals.decided_by` = `"ketoan"`; `audit_log.actor` = `"human:ketoan"`. Prefixing an already-prefixed value yields `human:human:ketoan`, which nothing catches.
6. **Do not bump `langgraph==1.2.9` or `langgraph-checkpoint-postgres==3.1.0`.**
7. **Language:** new code, comments, docstrings and commits in **English**. Product output stays **Vietnamese** — the flag narratives in the `interrupt()` payload are read by an accountant and must not be translated. When retargeting `test_hitl_survives_restart.py` (Task 9), the passages you rewrite become English; the helpers you leave untouched keep their Vietnamese docstrings. Do **not** mass-translate that file.
8. **Every non-obvious constant carries a comment with date + measurement + report filename.**
9. **Run `make check` after EVERY task**, and a milestone is not closed until it is green on the *pushed* commit with CI agreeing.

## Preconditions

```bash
make dev          # Postgres 17, migrations, seed
make generate     # corpus/ — required or the gate tests SKIP silently
make check        # must be green BEFORE any change
```

Stop and report if `make check` is not green — do not start on a red baseline.

## File Structure

**Create:**
- `src/invoice_agent/graphs/approval.py` — state, six nodes, `build_approval_graph`, `submit_decision`
- `scripts/recover_pending.py`
- `tests/test_approval_graph.py`, `tests/test_recover_pending.py`, `tests/test_batch_not_blocked_by_pending.py`
- `docs/notes/2026-08-01-m5b-approval-graph.md`

**Modify:**
- `src/invoice_agent/db/repositories/approvals.py` — `record_context`, `get_by_invoice`
- `src/invoice_agent/graphs/batch.py` — `open_approval_node`, `unopened` in `BatchState`, wiring
- `tests/test_hitl_survives_restart.py`, `tests/helpers/hitl_child.py` — retargeted
- `docs/diagrams/batch_graph.mmd` (regenerated), `docs/Plan_Project_F_2026-07-25.md`

**Delete (Task 9, only once the retargeted test is green):**
- `tests/helpers/spike_graph.py`, `tests/test_spike_graph.py`

---

### Task 1: `ApprovalState`, `load_snapshot`, `suggest`

The read-only half, all of which runs before the gate and therefore exactly once.

**Files:**
- Create: `src/invoice_agent/graphs/approval.py`
- Modify: `src/invoice_agent/db/repositories/approvals.py`
- Test: `tests/test_approval_graph.py`

**Interfaces:**
- Consumes: `db.repositories.invoices.get_by_id/get_lines`, `flags.open_flags_for`, `episodic.similar_episodes`
- Produces:
  - `ApprovalState` TypedDict with keys `invoice_id, approval_id, snapshot, flags, suggestions, decision, decided_by, reason, corrections, entry_id, status`
  - `load_snapshot_node(state, *, conn=None) -> dict[str, Any]`
  - `suggest_node(state, *, conn=None) -> dict[str, Any]`
  - `approvals.record_context(conn, approval_id, *, interrupt_payload, flags_snapshot, suggestions) -> None`

- [x] **Step 1: Write the failing tests**

Create `tests/test_approval_graph.py`:

```python
"""The approval graph — design §5, spec 2026-08-01-m5b.

Every test here builds its invoice through `_persisted_invoice`, which commits.
Nodes open their own connections (they must: `apply_decision` commits so
`post_entry`'s guard can re-read), so an uncommitted fixture transaction is
invisible to them. `committed_rows` removes what the test committed — the same
arrangement `tests/test_batch_graph.py` uses, and for the same reason.
"""

import uuid
from decimal import Decimal

import pytest

from invoice_agent.graphs import approval


@pytest.fixture
def committed_rows(requires_postgres):
    """Delete rows these tests commit, in FK order."""
    created: list[uuid.UUID] = []
    yield created
    if not created:
        return
    from sqlalchemy import text

    from invoice_agent.db.engine import get_owner_engine

    with get_owner_engine().begin() as conn:
        tax_codes = [
            r[0]
            for r in conn.execute(
                text("SELECT DISTINCT seller_tax_code FROM app.invoices WHERE id = ANY(:ids)"),
                {"ids": created},
            )
        ]
        conn.execute(
            text("DELETE FROM app.journal_entries WHERE invoice_id = ANY(:ids)"), {"ids": created}
        )
        for table in ("budget_consumption", "episodic_decisions", "approvals"):
            conn.execute(
                text(f"DELETE FROM app.{table} WHERE invoice_id = ANY(:ids)"), {"ids": created}
            )
        conn.execute(text("DELETE FROM app.invoices WHERE id = ANY(:ids)"), {"ids": created})
        if tax_codes:
            conn.execute(
                text(
                    "UPDATE app.vendors SET invoice_count=0, total_amount=0, sum_sq_amount=0,"
                    " avg_amount=NULL, stddev_amount=NULL, rejection_count=0,"
                    " reviewer_notes=NULL WHERE tax_code = ANY(:codes)"
                ),
                {"codes": tax_codes},
            )


def _persisted_invoice(created, *, category="OFFICE_SUPPLIES", flag=None):
    """Commit one invoice, optionally flagged, and open an approval for it.

    Returns (invoice_db_id, approval_id).
    """
    import pathlib

    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import approvals as approvals_repo
    from invoice_agent.db.repositories import flags, invoices
    from invoice_agent.extraction.xml_parser import parse_invoice_xml

    data = parse_invoice_xml(
        pathlib.Path("tests/fixtures/real_samples/viettelpost_gtgt_2.1.0.xml").read_bytes()
    ).model_copy(update={"invoice_number": uuid.uuid4().hex[:8]})

    with get_owner_engine().begin() as conn:
        db_id = invoices.insert_with_lines(
            conn, data, source_type="xml", category=category, department="ADMIN"
        )
        assert db_id is not None
        created.append(db_id)
        if flag is not None:
            flags.raise_flag(
                conn, db_id, code=flag[0], severity=flag[1], raised_by=flag[2]
            )
        approval_id = approvals_repo.open_approval(
            conn,
            invoice_id=db_id,
            thread_id=f"appr:{db_id}",
            interrupt_payload={},
            flags_snapshot={},
            suggestions={},
        )
    return db_id, approval_id


def _initial_state(db_id, approval_id) -> dict:
    return {
        "invoice_id": str(db_id),
        "approval_id": str(approval_id),
        "snapshot": {},
        "flags": [],
        "suggestions": [],
        "decision": "",
        "decided_by": "",
        "reason": None,
        "corrections": None,
        "entry_id": None,
        "status": "",
    }


def test_load_snapshot_carries_only_json_types(committed_rows):
    """Spec §0 constraint 3. An `appr:` thread lives DAYS — no other state in
    this system does — so its checkpoint must survive a deploy landing between
    the interrupt and the decision. A pydantic model that gained a required
    field will not deserialize; a dict will. `Decimal` and `date` are
    stringified here, at the boundary, not deep in a node.
    """
    import json

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    out = approval.load_snapshot_node(_initial_state(db_id, approval_id))

    # The real assertion: the whole payload survives json.dumps with no encoder.
    json.dumps({"snapshot": out["snapshot"], "flags": out["flags"]})

    assert out["snapshot"]["invoice_number"]
    assert isinstance(out["snapshot"]["total_amount"], str)
    assert out["snapshot"]["lines"], "a reviewer needs the line items"
    assert [f["code"] for f in out["flags"]] == ["PRICE_OUTLIER"]


def test_load_snapshot_shows_only_open_flags(committed_rows):
    """A resolved flag is history, not a reason to hold the invoice."""
    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import flags

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    with get_owner_engine().begin() as conn:
        for row in flags.open_flags_for(conn, db_id):
            flags.resolve_flag(conn, row["id"])

    out = approval.load_snapshot_node(_initial_state(db_id, approval_id))
    assert out["flags"] == []


def test_suggest_is_empty_on_a_fresh_database(committed_rows):
    """Spec §4.2, reported rather than hidden. `episodic_decisions` is empty
    until reviewers decide things, so the first batch's suggestions are all
    empty lists. Seeding fixtures to make the feature look alive would measure
    the fixtures — the same reason item 4.4's tuning stays deferred.
    """
    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {**_initial_state(db_id, approval_id)}
    state.update(approval.load_snapshot_node(state))

    out = approval.suggest_node(state)
    assert out["suggestions"] == []


def test_suggest_recalls_an_earlier_decision_for_the_same_vendor(committed_rows):
    """The half that proves `suggest` is wired to memory at all, not just to an
    empty table."""
    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import episodic

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {**_initial_state(db_id, approval_id)}
    state.update(approval.load_snapshot_node(state))
    tax_code = state["snapshot"]["seller_tax_code"]

    with get_owner_engine().begin() as conn:
        episodic.record_episode(
            conn,
            invoice_id=db_id,
            vendor_tax_code=tax_code,
            amount=Decimal("100"),
            flags={},
            decision="rejected",
            reason="sai số học",
        )

    out = approval.suggest_node(state)
    assert len(out["suggestions"]) == 1
    assert out["suggestions"][0]["decision"] == "rejected"
    assert out["suggestions"][0]["reason"] == "sai số học"


def test_suggest_persists_the_context_it_built(committed_rows):
    """`approvals` carries `interrupt_payload`/`flags_snapshot`/`suggestions`
    (design §2.8). They are written HERE, before the gate, because the gate must
    contain `interrupt()` and nothing else — anything added to that node runs
    twice across a restart.
    """
    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import approvals as approvals_repo

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    state = {**_initial_state(db_id, approval_id)}
    state.update(approval.load_snapshot_node(state))
    approval.suggest_node(state)

    with get_owner_engine().begin() as conn:
        row = approvals_repo.get_approval(conn, approval_id)
    assert row["flags_snapshot"]["flags"][0]["code"] == "PRICE_OUTLIER"
    assert "invoice" in row["interrupt_payload"]
```

- [x] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -v
```

Expected: all FAIL with `ModuleNotFoundError: No module named 'invoice_agent.graphs.approval'`.

- [x] **Step 3: Add the repository function**

In `src/invoice_agent/db/repositories/approvals.py`:

```python
def record_context(
    conn: Connection,
    approval_id: uuid.UUID,
    *,
    interrupt_payload: dict[str, Any],
    flags_snapshot: dict[str, Any],
    suggestions: dict[str, Any],
) -> None:
    """Fill the columns design §2.8 specifies, from the node BEFORE the gate.

    Not from the gate: `approval_gate` must contain `interrupt()` and nothing
    else, because M1.5 measured that node re-running from the top on resume.
    """
    conn.execute(
        models.approvals.update()
        .where(models.approvals.c.id == approval_id)
        .values(
            interrupt_payload=interrupt_payload,
            flags_snapshot=flags_snapshot,
            suggestions=suggestions,
        )
    )


def get_by_invoice(conn: Connection, invoice_id: uuid.UUID) -> dict[str, Any] | None:
    """The most recent approval for an invoice. `submit_decision` resolves a
    thread through this."""
    stmt = (
        select(models.approvals)
        .where(models.approvals.c.invoice_id == invoice_id)
        .order_by(models.approvals.c.requested_at.desc())
        .limit(1)
    )
    row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None
```

- [x] **Step 4: Write the module and the two nodes**

Create `src/invoice_agent/graphs/approval.py`:

```python
"""approval_graph — the human decision, made durable (design doc §5).

    START → load_snapshot → suggest → approval_gate ⏸ → apply_decision
    apply_decision ─cond→ { approved|edited: post_entry, rejected: record_rejection }
    post_entry → learn → END          record_rejection → learn → END

THREE THINGS ABOUT THIS FILE ARE LOAD-BEARING, and each was measured rather
than reasoned:

1. `approval_gate` contains `interrupt()` AND NOTHING ELSE. Measured 2026-07-27
   at M1.5 (langgraph 1.2.9): the node containing the interrupt runs TWICE
   across a `kill -9` + resume, the node before it runs once. A write placed in
   that node happens twice; graph shape is what stops everything except the
   journal entry, which `UNIQUE(invoice_id)` and M5-A's guard stop.

2. `apply_decision` COMMITS on its own connection, and `post_entry` reads on a
   different one. The guard's whole value is re-reading `approvals` from
   Postgres (design §5.3 step 2). Share a transaction and it reads its own
   caller's uncommitted write — which passes every test that does not
   specifically mutate for it.

3. `ApprovalState` is plain JSON. An `appr:` thread lives HOURS TO DAYS
   (design §1.3) while every other thread here lives minutes, so its checkpoint
   must survive a deploy landing between the interrupt and the decision.
"""

from datetime import date
from decimal import Decimal
from typing import Any, TypedDict

from invoice_agent.db.engine import get_engine


class ApprovalState(TypedDict):
    invoice_id: str
    approval_id: str
    snapshot: dict[str, Any]
    flags: list[dict[str, Any]]
    suggestions: list[dict[str, Any]]
    decision: str
    # BARE name. `approvals.decided_by` stores "ketoan"; `audit_log.actor`
    # stores "human:ketoan" (the convention in db/models.py). Prefixing an
    # already-prefixed value yields "human:human:ketoan" and no constraint
    # catches it.
    decided_by: str
    reason: str | None
    corrections: dict[str, Any] | None
    entry_id: str | None
    status: str


def _jsonable(value: Any) -> Any:
    """Decimal and date become strings AT THE BOUNDARY.

    Both land in JSONB and in a checkpoint. Converting here rather than deep in
    a node means one place to look when a number arrives as a string.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _with_conn(conn: Any, fn: Any, state: Any) -> dict[str, Any]:
    if conn is not None:
        return fn(conn, state)
    with get_engine().begin() as owned:
        return fn(owned, state)


def load_snapshot_node(state: ApprovalState, *, conn: Any = None) -> dict[str, Any]:
    return _with_conn(conn, _load_snapshot, state)


def _load_snapshot(conn: Any, state: ApprovalState) -> dict[str, Any]:
    import uuid as _uuid

    from invoice_agent.db.repositories import flags, invoices

    invoice_id = _uuid.UUID(state["invoice_id"])
    row = invoices.get_by_id(conn, invoice_id)
    if row is None:
        raise ValueError(f"approval opened for an invoice that does not exist: {invoice_id}")

    snapshot = _jsonable({k: v for k, v in row.items() if k not in ("raw_xml",)})
    snapshot["id"] = str(row["id"])
    snapshot["lines"] = _jsonable([dict(line) for line in invoices.get_lines(conn, invoice_id)])
    open_flags = _jsonable([dict(f) for f in flags.open_flags_for(conn, invoice_id)])
    return {"snapshot": snapshot, "flags": open_flags, "status": "loaded"}


def suggest_node(state: ApprovalState, *, conn: Any = None) -> dict[str, Any]:
    return _with_conn(conn, _suggest, state)


def _suggest(conn: Any, state: ApprovalState) -> dict[str, Any]:
    """Episodic recall. NO LLM — design §5.1: this node sits on the path the
    batch is waiting for, and a model call here would make every reviewer queue
    entry cost a token and a round trip.

    Returns [] on a fresh database, because `episodic_decisions` is empty until
    reviewers decide things. Reported as measured rather than seeded.
    """
    import uuid as _uuid

    from invoice_agent.db.repositories import approvals as approvals_repo
    from invoice_agent.db.repositories import episodic

    snapshot = state["snapshot"]
    episodes = episodic.similar_episodes(
        conn,
        vendor_tax_code=snapshot.get("seller_tax_code", ""),
        category=snapshot.get("category"),
        limit=5,
    )
    suggestions = _jsonable([dict(e) for e in episodes])

    payload = {
        "invoice": snapshot,
        "flags": state["flags"],
        "suggestions": suggestions,
    }
    # Written HERE, before the gate. See the module docstring, point 1.
    approvals_repo.record_context(
        conn,
        _uuid.UUID(state["approval_id"]),
        interrupt_payload=payload,
        flags_snapshot={"flags": state["flags"]},
        suggestions={"episodes": suggestions},
    )
    return {"suggestions": suggestions, "status": "suggested"}
```

- [x] **Step 5: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -v
```

Expected: all 5 PASS.

- [x] **Step 6: `make check` and commit**

```bash
make check
git add src/invoice_agent/graphs/approval.py src/invoice_agent/db/repositories/approvals.py tests/test_approval_graph.py
git commit -m "feat(m5b): add ApprovalState, load_snapshot and suggest

State is plain JSON by design: an appr: thread lives hours to days while every
other thread in this system lives minutes, so its checkpoint must survive a
deploy landing between the interrupt and the decision.

suggest calls no LLM (design §5.1 — it sits on the path the batch waits for) and
writes the approvals row's context columns, because the gate node must contain
interrupt() and nothing else.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 2: `approval_gate` and a compilable graph that reaches the interrupt

**Files:**
- Modify: `src/invoice_agent/graphs/approval.py`
- Test: `tests/test_approval_graph.py` (append)

**Interfaces:**
- Consumes: Task 1's nodes
- Produces:
  - `approval_gate_node(state) -> dict[str, Any]`
  - `build_approval_graph(*, checkpointer: Any = None) -> Any`

- [x] **Step 1: Write the failing tests**

```python
def test_the_graph_stops_at_the_interrupt_and_checkpoints(committed_rows):
    """The property design §5.2 calls the largest difference between this system
    and a confirm dialog: execution stops and the whole state lands in Postgres,
    so the process may die."""
    from invoice_agent.checkpointer import build_checkpointer

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    config = {"configurable": {"thread_id": f"appr:{db_id}"}}
    with build_checkpointer() as saver:
        saver.setup()
        app = approval.build_approval_graph(checkpointer=saver)
        result = app.invoke(_initial_state(db_id, approval_id), config)

    assert "__interrupt__" in result, "the graph ran past its own gate"
    payload = result["__interrupt__"][0].value
    assert payload["invoice"]["invoice_number"]
    assert payload["flags"][0]["code"] == "PRICE_OUTLIER"


def test_the_gate_node_has_no_side_effect_beyond_the_interrupt():
    """A STATIC guard, and deliberately so. The behavioural cost of a write in
    this node only appears across a `kill -9` (Task 9 measures it), which is
    slow and easy to skip. This one is instant and fails at the commit that
    introduces the write.

    M1.5 measured the consequence: the node containing `interrupt()` runs TWICE
    across resume, so anything here happens twice.
    """
    import inspect

    source = inspect.getsource(approval.approval_gate_node)
    for forbidden in ("conn", "insert", "update", "record_", "get_engine", "repositories"):
        assert forbidden not in source, (
            f"approval_gate_node touches {forbidden!r}; it must contain interrupt() and "
            "nothing else — M1.5 measured that this node runs twice across a restart"
        )
```

- [x] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -k "interrupt or gate_node" -v
```

Expected: FAIL — `approval` has no attribute `approval_gate_node` / `build_approval_graph`.

- [x] **Step 3: Implement**

Append to `src/invoice_agent/graphs/approval.py`:

```python
def approval_gate_node(state: ApprovalState) -> dict[str, Any]:
    """`interrupt()` and nothing else.

    NOT a stylistic preference. Measured 2026-07-27 at M1.5 across a real
    `kill -9`: this node's body runs TWICE — once in the process that died,
    once in the process that resumed. Anything added here happens twice.
    `tests/test_approval_graph.py` guards it statically and Task 9's durability
    test guards it behaviourally.
    """
    from langgraph.types import interrupt

    decision = interrupt(
        {
            "invoice": state["snapshot"],
            # Vietnamese, and it stays Vietnamese: a Vietnamese accountant reads
            # these. CLAUDE.md puts product output on the other side of the
            # authoring-language rule.
            "flags": state["flags"],
            "suggestions": state["suggestions"],
        }
    )
    return {
        "decision": decision["decision"],
        "decided_by": decision["decided_by"],
        "reason": decision.get("reason"),
        "corrections": decision.get("corrections"),
    }


def build_approval_graph(*, checkpointer: Any = None) -> Any:
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(ApprovalState)
    builder.add_node("load_snapshot", load_snapshot_node)
    builder.add_node("suggest", suggest_node)
    builder.add_node("approval_gate", approval_gate_node)

    builder.add_edge(START, "load_snapshot")
    builder.add_edge("load_snapshot", "suggest")
    builder.add_edge("suggest", "approval_gate")
    builder.add_edge("approval_gate", END)

    return builder.compile(checkpointer=checkpointer)
```

`approval_gate → END` is temporary; Task 4 replaces it with the conditional edge.

- [x] **Step 4: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -v
```

Expected: all PASS.

- [x] **Step 5: `make check` and commit**

```bash
make check
git add src/invoice_agent/graphs/approval.py tests/test_approval_graph.py
git commit -m "feat(m5b): add approval_gate and a graph that reaches its interrupt

The gate contains interrupt() and nothing else, guarded statically as well as
behaviourally: M1.5 measured that this node's body runs twice across kill -9 +
resume, and the behavioural cost only shows up in a slow durability test.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 3: `apply_decision` — the commit boundary and `resolve_flag`'s first caller

**Files:**
- Modify: `src/invoice_agent/graphs/approval.py`
- Test: `tests/test_approval_graph.py` (append)

**Interfaces:**
- Consumes: `approvals.record_decision`, `flags.open_flags_for`, `flags.resolve_flag`
- Produces: `apply_decision_node(state) -> dict[str, Any]` — **always opens and commits its own connection; accepts no `conn` parameter**

- [x] **Step 1: Write the failing tests**

```python
def test_apply_decision_commits_so_a_separate_connection_can_see_it(committed_rows):
    """Spec §2.3 — the boundary that can rot silently.

    `post_entry`'s guard re-reads `approvals` from Postgres. If the two nodes
    shared a transaction the guard would read its own caller's uncommitted
    write, verify nothing, and pass every ordinary test. So this asserts the
    decision is visible from a connection the node never touched.
    """
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import approvals as approvals_repo

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "approved",
        "decided_by": "ketoan",
        "reason": "ok",
    }
    approval.apply_decision_node(state)

    with get_engine().begin() as other:
        row = approvals_repo.get_approval(other, approval_id)
    assert row["decision"] == "approved"
    assert row["decided_by"] == "ketoan", "decided_by is stored BARE, no human: prefix"


def test_apply_decision_accepts_no_connection_parameter():
    """Static guard on the same property. A `conn=` parameter is the one change
    that would let a caller hand `apply_decision` the transaction `post_entry`
    later reads on — re-creating the defect above with the behavioural test
    still green, because the test above opens its own connection either way.
    """
    import inspect

    assert "conn" not in inspect.signature(approval.apply_decision_node).parameters


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_deciding_an_invoice_closes_its_flags(committed_rows, decision):
    """M5-A spec §8 question 1, answered. A human has adjudicated the flags
    either way. Left open, `invoices.status` says `flagged` forever,
    `route_outcomes` keeps treating the invoice as blocked, and M5-C's queue
    re-serves invoices somebody already decided.

    The rows are NOT deleted — `resolved_at` is set, so §10.4's flag
    false-positive metric can still count them.
    """
    from sqlalchemy import select

    from invoice_agent.db import models
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import flags

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    state = {
        **_initial_state(db_id, approval_id),
        "decision": decision,
        "decided_by": "ketoan",
    }
    approval.apply_decision_node(state)

    with get_engine().begin() as conn:
        assert flags.open_flags_for(conn, db_id) == []
        rows = list(
            conn.execute(
                select(models.invoice_flags).where(models.invoice_flags.c.invoice_id == db_id)
            ).mappings()
        )
    assert len(rows) == 1, "the flag row must survive; only resolved_at changes"
    assert rows[0]["resolved_at"] is not None
```

- [x] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -k "apply_decision or closes_its_flags" -v
```

Expected: FAIL — no attribute `apply_decision_node`.

- [x] **Step 3: Implement**

```python
def apply_decision_node(state: ApprovalState) -> dict[str, Any]:
    """Record the decision and close the flags. OWNS ITS TRANSACTION.

    NO `conn` PARAMETER, deliberately. `post_entry` re-reads `approvals` from
    Postgres to verify the decision persisted (design §5.3 step 2). If a caller
    could hand this node the same transaction `post_entry` later reads on, the
    guard would be verifying its own caller's uncommitted write — indistinguish-
    able from a working guard in every test that does not mutate for it
    (spec §2.3). Accepting no connection makes that arrangement unexpressible.

    Flags are resolved on BOTH outcomes, which answers M5-A spec §8 question 1:
    a human has adjudicated them either way. `resolved_at` is set; nothing is
    deleted, so the false-positive metric can still count them. Note the
    consequence: the DB trigger stops blocking `auto_clean` for this invoice.
    It cannot be re-posted regardless — `UNIQUE(invoice_id)` and M5-A's
    idempotency fork both hold.
    """
    import uuid as _uuid

    from invoice_agent.db.repositories import approvals as approvals_repo
    from invoice_agent.db.repositories import flags

    invoice_id = _uuid.UUID(state["invoice_id"])
    approval_id = _uuid.UUID(state["approval_id"])

    with get_engine().begin() as conn:
        approvals_repo.record_decision(
            conn,
            approval_id,
            decision=state["decision"],
            decided_by=state["decided_by"],
            reason=state.get("reason"),
            field_corrections=state.get("corrections"),
        )
        for row in flags.open_flags_for(conn, invoice_id):
            flags.resolve_flag(conn, row["id"])

    return {"status": "decided"}
```

- [x] **Step 4: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -v
```

Expected: all PASS.

- [x] **Step 5: `make check` and commit**

```bash
make check
git add src/invoice_agent/graphs/approval.py tests/test_approval_graph.py
git commit -m "feat(m5b): add apply_decision, which commits and closes the flags

Takes no conn parameter on purpose. post_entry's guard re-reads approvals from
Postgres; if a caller could hand this node the transaction post_entry later
reads on, the guard would verify its own caller's uncommitted write and pass
every ordinary test. Refusing the parameter makes that unexpressible.

resolve_flag gains its first caller, on approve and reject alike — M5-A spec §8
question 1. Rows survive with resolved_at set; nothing is deleted.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 4: `post_entry`, `record_rejection`, and the conditional edge

**Files:**
- Modify: `src/invoice_agent/graphs/approval.py`
- Test: `tests/test_approval_graph.py` (append)

**Interfaces:**
- Consumes: `tools.guards.create_journal_entry`, `domain.accounting`, `reference.get_category`
- Produces:
  - `post_entry_node(state) -> dict[str, Any]`
  - `record_rejection_node(state) -> dict[str, Any]`
  - `route_after_decision(state) -> str` returning `"post_entry"` or `"record_rejection"`

- [x] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("decision", ["approved", "edited"])
def test_an_approved_invoice_posts_with_human_authority(committed_rows, decision):
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import invoices, journal

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": decision,
        "decided_by": "ketoan",
    }
    state.update(approval.load_snapshot_node(state))
    approval.apply_decision_node(state)
    out = approval.post_entry_node(state)

    with get_engine().begin() as conn:
        entry = journal.get_for_invoice(conn, db_id)
        assert invoices.get_by_id(conn, db_id)["status"] == "posted"
    assert entry["posting_authority"] == "human_approved"
    assert entry["approval_id"] == approval_id
    assert out["entry_id"] == str(entry["id"])


def test_the_audit_actor_is_prefixed_exactly_once(committed_rows):
    """Spec §4.5. `decided_by` is bare in `approvals`; `human:` is added only
    here. Prefixing an already-prefixed value yields `human:human:ketoan`, which
    no constraint rejects and no other test would notice.
    """
    from sqlalchemy import select

    from invoice_agent.db import models
    from invoice_agent.db.engine import get_engine

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "approved",
        "decided_by": "ketoan",
    }
    state.update(approval.load_snapshot_node(state))
    approval.apply_decision_node(state)
    approval.post_entry_node(state)

    with get_engine().begin() as conn:
        actors = {
            r["actor"]
            for r in conn.execute(
                select(models.audit_log).where(models.audit_log.c.target_id == str(db_id))
            ).mappings()
        }
    assert actors == {"human:ketoan"}


def test_a_rejected_invoice_is_marked_rejected_and_never_posted(committed_rows):
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import invoices, journal

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "rejected",
        "decided_by": "ketoan",
        "reason": "hóa đơn trùng",
    }
    state.update(approval.load_snapshot_node(state))
    approval.apply_decision_node(state)
    approval.record_rejection_node(state)

    with get_engine().begin() as conn:
        assert journal.get_for_invoice(conn, db_id) is None
        assert invoices.get_by_id(conn, db_id)["status"] == "rejected"


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("approved", "post_entry"),
        ("edited", "post_entry"),
        ("rejected", "record_rejection"),
    ],
)
def test_routing_after_the_decision(decision, expected):
    assert approval.route_after_decision({"decision": decision}) == expected


def test_a_guard_denial_in_post_entry_is_not_swallowed(committed_rows):
    """Spec §4.5. In `auto_post` a denial routes the invoice to a human; here a
    human has ALREADY decided, so a denial means the guard and the reviewer
    disagree. Absorbing it would hide exactly the disagreement the guard exists
    to surface.
    """
    from invoice_agent.tools import guards

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "approved",
        "decided_by": "ketoan",
    }
    state.update(approval.load_snapshot_node(state))
    # Deliberately NOT calling apply_decision — the approvals row stays
    # undecided, which is precisely what the guard refuses.
    with pytest.raises(guards.GuardDenied):
        approval.post_entry_node(state)
```

- [x] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -k "posts_with_human or audit_actor or rejected or routing_after or guard_denial" -v
```

Expected: FAIL — no attribute `post_entry_node`.

- [x] **Step 3: Implement**

```python
def post_entry_node(state: ApprovalState) -> dict[str, Any]:
    """Post through the guard, with human authority.

    A `GuardDenied` here PROPAGATES. `auto_post` catches it and routes the
    invoice to a reviewer, but a reviewer has already decided this one — a
    denial means the guard and the human disagree, and that has to surface. The
    thread stays resumable, so the disagreement can be shown to them.
    """
    import uuid as _uuid

    from invoice_agent.db.repositories import invoices as invoices_repo
    from invoice_agent.db.repositories import reference
    from invoice_agent.domain import accounting
    from invoice_agent.tools import guards

    invoice_id = _uuid.UUID(state["invoice_id"])
    approval_id = _uuid.UUID(state["approval_id"])

    with get_engine().begin() as conn:
        row = invoices_repo.get_by_id(conn, invoice_id)
        cat_row = reference.get_category(conn, row["category"]) if row["category"] else None
        if cat_row is None:
            raise ValueError(
                f"{invoice_id}: approved but its category {row['category']!r} has no seeded "
                "accounts — nothing to book against, and inventing one is what M4-D removed"
            )
        accounts = accounting.CategoryAccounts(
            debit_account=cat_row["debit_account"],
            debit_name=cat_row["name_vi"],
            vat_account=cat_row["vat_account"],
            vat_name="Thuế GTGT được khấu trừ" if cat_row["vat_account"] else None,
            credit_account=cat_row["credit_account"],
            credit_name="Phải trả cho người bán",
        )
        invoice_data = _rebuild_invoice_data(conn, invoice_id)
        lines = accounting.build_journal_entry(invoice_data, accounts)
        entry_id = guards.create_journal_entry(
            conn,
            invoice_id=invoice_id,
            posting_authority="human_approved",
            approval_id=approval_id,
            entry_date=invoice_data.issue_date,
            lines=lines,
            # The ONLY place `human:` is added. See ApprovalState.decided_by.
            actor=f"human:{state['decided_by']}",
        )
    return {"entry_id": str(entry_id), "status": "posted"}


def _rebuild_invoice_data(conn: Any, invoice_id: Any) -> Any:
    """Rebuild `InvoiceData` from the DATABASE, not from the snapshot.

    The snapshot is stringified JSON built for a human to read, and it may be
    days old. The journal entry must be computed from the source of truth, by
    the same `accounting.build_journal_entry` the auto path uses — otherwise
    the two paths could book different numbers for the same invoice.
    """
    from invoice_agent.db.repositories import invoices as invoices_repo
    from invoice_agent.extraction.models import InvoiceData, InvoiceLine

    row = invoices_repo.get_by_id(conn, invoice_id)
    lines = [
        InvoiceLine(
            line_no=line["line_no"],
            description=line["description"],
            unit=line["unit"],
            quantity=line["quantity"],
            unit_price=line["unit_price"],
            amount=line["amount"],
            vat_rate=line["vat_rate"],
            vat_amount=line["vat_amount"],
            item_kind=line["item_kind"],
            ext=line["ext"],
        )
        for line in invoices_repo.get_lines(conn, invoice_id)
    ]
    return InvoiceData(
        template_no=row["template_no"],
        invoice_series=row["invoice_series"],
        invoice_number=row["invoice_number"],
        seller_tax_code=row["seller_tax_code"],
        seller_name=None,
        buyer_tax_code=row["buyer_tax_code"],
        issue_date=row["issue_date"],
        currency=row["currency"],
        exchange_rate=row["exchange_rate"],
        subtotal=row["subtotal"],
        vat_amount=row["vat_amount"],
        total_amount=row["total_amount"],
        total_in_words=row["total_in_words"],
        ext=row["ext"],
        lines=lines,
    )


def record_rejection_node(state: ApprovalState) -> dict[str, Any]:
    import uuid as _uuid

    from invoice_agent.db.repositories import invoices as invoices_repo

    with get_engine().begin() as conn:
        invoices_repo.set_status(conn, _uuid.UUID(state["invoice_id"]), "rejected")
    return {"status": "rejected"}


def route_after_decision(state: ApprovalState) -> str:
    """`edited` posts. A reviewer who corrected a field and let the invoice
    through has approved it — decided in M5-A spec §4.1 so this inherits an
    answer rather than a guess.
    """
    return "post_entry" if state["decision"] in ("approved", "edited") else "record_rejection"
```

Then rewire in `build_approval_graph`, replacing `builder.add_edge("approval_gate", END)`:

```python
    builder.add_node("apply_decision", apply_decision_node)
    builder.add_node("post_entry", post_entry_node)
    builder.add_node("record_rejection", record_rejection_node)

    builder.add_edge("approval_gate", "apply_decision")
    builder.add_conditional_edges(
        "apply_decision",
        route_after_decision,
        {"post_entry": "post_entry", "record_rejection": "record_rejection"},
    )
    builder.add_edge("post_entry", END)
    builder.add_edge("record_rejection", END)
```

**On the fields `_rebuild_invoice_data` omits.** `InvoiceData` also declares `schema_version`, `seller_address`, `buyer_name` and `buyer_address`; the `invoices` table stores none of them, and all four are `Optional` with defaults, so leaving them out is valid. `build_journal_entry` reads only `lines`, `vat_amount` and `issue_date`, none of which are affected. `InvoiceLine`'s field names above match `extraction/models.py:27-55` exactly — verified 2026-08-01. The model is `frozen=True, extra="forbid"`, so a *wrong* name raises at construction rather than silently dropping; a *missing optional* one does not.

- [x] **Step 4: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -v
```

Expected: all PASS.

- [x] **Step 5: `make check` and commit**

```bash
make check
git add src/invoice_agent/graphs/approval.py tests/test_approval_graph.py
git commit -m "feat(m5b): add post_entry, record_rejection and the decision router

post_entry rebuilds InvoiceData from the DATABASE rather than the snapshot: the
snapshot is stringified JSON built for a human and may be days old, and the two
posting paths must book identical numbers for the same invoice.

A GuardDenied here propagates rather than routing to a human — a human has
already decided, so a denial means the guard and the reviewer disagree.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 5: `learn` — HITL feeds memory

**Files:**
- Modify: `src/invoice_agent/graphs/approval.py`
- Test: `tests/test_approval_graph.py` (append)

**Interfaces:**
- Consumes: `episodic.record_episode`, `vendors.get_vendor`, `vendors.update_profile`
- Produces: `learn_node(state) -> dict[str, Any]`

- [x] **Step 1: Write the failing tests**

```python
def test_a_rejection_is_remembered_with_its_reason(committed_rows):
    """Design §5.4. HITL and memory are one mechanism, not two features."""
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import episodic, vendors

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "rejected",
        "decided_by": "ketoan",
        "reason": "sai số học",
    }
    state.update(approval.load_snapshot_node(state))
    approval.learn_node(state)

    tax_code = state["snapshot"]["seller_tax_code"]
    with get_engine().begin() as conn:
        episodes = episodic.similar_episodes(conn, vendor_tax_code=tax_code)
        vendor = vendors.get_vendor(conn, tax_code)
    assert [e["reason"] for e in episodes] == ["sai số học"]
    assert vendor["rejection_count"] == 1


def test_an_approval_is_remembered_too(committed_rows):
    """Past design §5.4's literal wording, deliberately. `suggest` asks "what
    happened last time for this vendor"; a memory holding only refusals biases
    every suggestion it ever makes. Recorded as a decision, not a drift.
    """
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import episodic, vendors

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "approved",
        "decided_by": "ketoan",
    }
    state.update(approval.load_snapshot_node(state))
    approval.learn_node(state)

    tax_code = state["snapshot"]["seller_tax_code"]
    with get_engine().begin() as conn:
        episodes = episodic.similar_episodes(conn, vendor_tax_code=tax_code)
        vendor = vendors.get_vendor(conn, tax_code)
    assert [e["decision"] for e in episodes] == ["approved"]
    assert vendor["rejection_count"] == 0, "an approval is not a rejection"


def test_field_corrections_reach_the_field_classify_reads(committed_rows):
    """Design §5.4's second arrow: `field_corrections` → `vendors.reviewer_notes`
    → what `classify` reads next time. Without this the loop is decorative."""
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import vendors

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "edited",
        "decided_by": "ketoan",
        "corrections": {"category": "IT_SERVICES"},
    }
    state.update(approval.load_snapshot_node(state))
    approval.learn_node(state)

    with get_engine().begin() as conn:
        vendor = vendors.get_vendor(conn, state["snapshot"]["seller_tax_code"])
    assert "IT_SERVICES" in vendor["reviewer_notes"]
```

- [x] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -k "remembered or corrections_reach" -v
```

Expected: FAIL — no attribute `learn_node`.

- [x] **Step 3: Implement**

```python
def learn_node(state: ApprovalState) -> dict[str, Any]:
    """HITL feeds memory (design §5.4) — one mechanism, not two features.

    Records an episode on EVERY decision, not only rejections. Design §5.4 names
    the reject arrow, but `suggest` asks "what happened last time for this
    vendor" and approvals are the majority answer; a memory holding only
    refusals biases every suggestion it makes. Deliberate extension past the
    design doc's literal wording (spec §4.6).
    """
    import uuid as _uuid
    from decimal import Decimal as _Decimal

    from invoice_agent.db.repositories import episodic, vendors

    snapshot = state["snapshot"]
    tax_code = snapshot.get("seller_tax_code")
    if not tax_code:
        return {"status": "learned"}

    with get_engine().begin() as conn:
        episodic.record_episode(
            conn,
            invoice_id=_uuid.UUID(state["invoice_id"]),
            approval_id=_uuid.UUID(state["approval_id"]),
            vendor_tax_code=tax_code,
            amount=_Decimal(snapshot.get("total_amount") or "0"),
            flags={"codes": [f["code"] for f in state.get("flags") or []]},
            decision=state["decision"],
            category=snapshot.get("category"),
            department=snapshot.get("department"),
            reason=state.get("reason"),
        )

        if state["decision"] == "rejected":
            current = vendors.get_vendor(conn, tax_code) or {}
            vendors.update_profile(
                conn, tax_code, rejection_count=int(current.get("rejection_count") or 0) + 1
            )

        corrections = state.get("corrections")
        if corrections:
            # Appended, not overwritten: this field is a running log a reviewer
            # writes to over time, and `classify` reads the whole thing.
            current = vendors.get_vendor(conn, tax_code) or {}
            previous = current.get("reviewer_notes") or ""
            entry = f"[{state['decided_by']}] " + ", ".join(
                f"{k}={v}" for k, v in sorted(corrections.items())
            )
            vendors.update_profile(
                conn, tax_code, reviewer_notes=(previous + "\n" + entry).strip()
            )

    return {"status": "learned"}
```

Then add it to the graph, replacing the two `END` edges from Task 4:

```python
    builder.add_node("learn", learn_node)
    builder.add_edge("post_entry", "learn")
    builder.add_edge("record_rejection", "learn")
    builder.add_edge("learn", END)
```

- [x] **Step 4: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -v
```

Expected: all PASS.

- [x] **Step 5: `make check` and commit**

```bash
make check
git add src/invoice_agent/graphs/approval.py tests/test_approval_graph.py
git commit -m "feat(m5b): add learn, closing the HITL-feeds-memory loop

Records an episode on every decision, not only rejections: suggest asks what
happened last time for this vendor, and a memory holding only refusals biases
every suggestion it makes. reviewer_notes is appended to, not overwritten — it
is a running log classify reads whole.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 6: `submit_decision` — the seam M5-C wraps

**Files:**
- Modify: `src/invoice_agent/graphs/approval.py`
- Test: `tests/test_approval_graph.py` (append)

**Interfaces:**
- Consumes: `checkpointer.build_checkpointer`, `approvals.get_by_invoice`
- Produces: `submit_decision(*, invoice_id: str, decision: str, decided_by: str, reason: str | None = None, corrections: dict[str, Any] | None = None) -> dict[str, Any]`; `VALID_DECISIONS = ("approved", "rejected", "edited")`

- [x] **Step 1: Write the failing tests**

```python
def test_submit_decision_drives_a_pending_thread_to_completion(committed_rows):
    """The seam M5-C's route wraps. The API adds no logic — it parses a body and
    calls this — which is what makes M7's "interrupt is transport-agnostic"
    artefact a demonstration instead of a claim.
    """
    from invoice_agent.checkpointer import build_checkpointer
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import journal

    db_id, approval_id = _persisted_invoice(committed_rows)
    config = {"configurable": {"thread_id": f"appr:{db_id}"}}
    with build_checkpointer() as saver:
        saver.setup()
        app = approval.build_approval_graph(checkpointer=saver)
        first = app.invoke(_initial_state(db_id, approval_id), config)
    assert "__interrupt__" in first

    result = approval.submit_decision(
        invoice_id=str(db_id), decision="approved", decided_by="ketoan"
    )
    assert "__interrupt__" not in result
    assert result["status"] == "learned"

    with get_engine().begin() as conn:
        assert journal.get_for_invoice(conn, db_id) is not None


def test_an_unknown_decision_never_reaches_the_graph(committed_rows):
    """A typo must not resume a thread into a state `route_after_decision`
    cannot route — that would silently take the rejection branch and mark a
    perfectly good invoice rejected."""
    db_id, _approval_id = _persisted_invoice(committed_rows)
    with pytest.raises(ValueError, match="decision"):
        approval.submit_decision(
            invoice_id=str(db_id), decision="aproved", decided_by="ketoan"
        )
```

- [x] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -k submit_decision -v
```

Expected: FAIL — no attribute `submit_decision`.

- [x] **Step 3: Implement**

```python
VALID_DECISIONS = ("approved", "rejected", "edited")


def submit_decision(
    *,
    invoice_id: str,
    decision: str,
    decided_by: str,
    reason: str | None = None,
    corrections: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resume a pending approval thread. THE SEAM M5-C WRAPS.

    The FastAPI route parses a request body and calls this; it adds no logic.
    That is what makes "interrupt is transport-agnostic" (the M7 artefact)
    demonstrable rather than asserted — a test, a script, HTTP and MCP all drive
    the identical function.

    `decision` is validated BEFORE the graph is touched. `route_after_decision`
    sends anything that is not approved/edited to `record_rejection`, so an
    unvalidated typo would silently reject a good invoice.
    """
    from langgraph.types import Command

    from invoice_agent.checkpointer import build_checkpointer

    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of {VALID_DECISIONS}, got {decision!r}")

    config = {"configurable": {"thread_id": f"appr:{invoice_id}"}}
    with build_checkpointer() as saver:
        app = build_approval_graph(checkpointer=saver)
        return dict(
            app.invoke(
                Command(
                    resume={
                        "decision": decision,
                        "decided_by": decided_by,
                        "reason": reason,
                        "corrections": corrections,
                    }
                ),
                config,
            )
        )
```

- [x] **Step 4: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -v
```

Expected: all PASS.

- [x] **Step 5: `make check` and commit**

```bash
make check
git add src/invoice_agent/graphs/approval.py tests/test_approval_graph.py
git commit -m "feat(m5b): add submit_decision, the seam M5-C wraps

Validates the decision before touching the graph: route_after_decision sends
anything that is not approved/edited to record_rejection, so an unvalidated typo
would silently reject a good invoice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 7: `open_approval` in the batch graph

**Files:**
- Modify: `src/invoice_agent/graphs/batch.py`, `docs/diagrams/batch_graph.mmd`
- Test: `tests/test_batch_graph.py` (append)

**Interfaces:**
- Consumes: `approvals.open_approval`, `approval.build_approval_graph`, `checkpointer.build_checkpointer`
- Produces: `open_approval_node(state: BatchState) -> dict[str, Any]` returning `{"opened": [...], "unopened": [...], "status": "approvals_opened"}`; `BatchState` gains `opened: list[dict[str, Any]]` and `unopened: list[dict[str, str]]`

- [x] **Step 1: Write the failing tests**

```python
def test_open_approval_parks_a_flagged_invoice_at_its_interrupt(committed_rows):
    """Design §5.2: write the approvals row, drive the graph to interrupt, come
    straight back so the batch can finish."""
    import uuid

    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import approvals as approvals_repo
    from invoice_agent.domain import findings

    state = _joined_state(committed_rows, category="OFFICE_SUPPLIES")
    db_id = uuid.UUID(state["persisted"][0]["db_id"])
    _raise_flag_committed(
        db_id, code=findings.PRICE_OUTLIER, severity="warn", raised_by="anomaly"
    )
    state["needs_approval"] = ["INV-TEST"]
    state["auto"] = []

    result = batch.open_approval_node(state)

    assert [o["invoice_id"] for o in result["opened"]] == ["INV-TEST"]
    with get_owner_engine().begin() as conn:
        row = approvals_repo.get_by_invoice(conn, db_id)
    assert row is not None
    assert row["decision"] is None
    assert row["thread_id"] == f"appr:{db_id}"
    assert row["interrupt_payload"]["invoice"]["invoice_number"]


def test_an_invoice_that_never_persisted_gets_no_thread(committed_rows):
    """`approvals.invoice_id` is a NOT NULL FK, so the 2 failed PDFs and the 1
    unpersisted duplicate in the gate batch cannot have a thread. They are
    REPORTED in `unopened`, not silently dropped — the treatment M4-D gave
    `unroutable`."""
    state = _joined_state(committed_rows, category="OFFICE_SUPPLIES")
    state["needs_approval"] = ["INV-GHOST"]
    state["auto"] = []

    result = batch.open_approval_node(state)
    assert result["opened"] == []
    assert result["unopened"] == [{"invoice_id": "INV-GHOST", "reason": "not persisted"}]


def test_the_graph_wires_open_approval_between_auto_post_and_finalize():
    rendered = batch.build_batch_graph().get_graph().draw_mermaid()
    assert "auto_post --> open_approval;" in rendered
    assert "open_approval --> finalize;" in rendered
    assert "auto_post --> finalize;" not in rendered
```

- [x] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_batch_graph.py -k open_approval -v
```

Expected: FAIL — `batch` has no attribute `open_approval_node`.

- [x] **Step 3: Implement**

Add `opened` and `unopened` to `BatchState`, then:

```python
def open_approval_node(state: BatchState) -> dict[str, Any]:
    """Design §5.2's arm: one approval thread per flagged invoice, then return.

    The batch does NOT wait. Each thread runs to `interrupt()` and stops, its
    whole state checkpointed to Postgres, so the reviewer may decide hours later
    from a different process. That is the entire justification for three graphs
    rather than one (design §1.1), and `test_batch_not_blocked_by_pending.py`
    is the measurement of it.

    ONE checkpointer for the whole node, not one per invoice: a PostgresSaver
    per invoice would open a connection pool per invoice to accomplish nothing.
    """
    from invoice_agent.checkpointer import build_checkpointer
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import approvals as approvals_repo
    from invoice_agent.graphs.approval import build_approval_graph

    db_ids = {p["invoice_id"]: uuid.UUID(p["db_id"]) for p in state.get("persisted") or []}
    opened: list[dict[str, Any]] = []
    unopened: list[dict[str, str]] = []

    pending = [k for k in (state.get("needs_approval") or []) if k in db_ids]
    unopened.extend(
        {"invoice_id": k, "reason": "not persisted"}
        for k in (state.get("needs_approval") or [])
        if k not in db_ids
    )
    if not pending:
        return {"opened": opened, "unopened": unopened, "status": "approvals_opened"}

    with build_checkpointer() as saver:
        saver.setup()
        graph = build_approval_graph(checkpointer=saver)
        for invoice_key in pending:
            db_id = db_ids[invoice_key]
            thread_id = f"appr:{db_id}"
            with get_engine().begin() as conn:
                approval_id = approvals_repo.open_approval(
                    conn,
                    invoice_id=db_id,
                    thread_id=thread_id,
                    interrupt_payload={},
                    flags_snapshot={},
                    suggestions={},
                )
            initial = {
                "invoice_id": str(db_id),
                "approval_id": str(approval_id),
                "snapshot": {},
                "flags": [],
                "suggestions": [],
                "decision": "",
                "decided_by": "",
                "reason": None,
                "corrections": None,
                "entry_id": None,
                "status": "",
            }
            result = graph.invoke(initial, {"configurable": {"thread_id": thread_id}})
            if "__interrupt__" not in result:
                # The graph ran past its own gate. Reported rather than
                # asserted: a batch node must not die because one thread
                # misbehaved, and a silent success here would mean an invoice
                # nobody is ever asked about.
                unopened.append({"invoice_id": invoice_key, "reason": "no interrupt raised"})
                continue
            opened.append(
                {
                    "invoice_id": invoice_key,
                    "db_id": str(db_id),
                    "approval_id": str(approval_id),
                    "thread_id": thread_id,
                }
            )

    return {"opened": opened, "unopened": unopened, "status": "approvals_opened"}
```

Rewire in `build_batch_graph`:

```python
    builder.add_node("open_approval", open_approval_node)
    builder.add_edge("auto_post", "open_approval")
    builder.add_edge("open_approval", "finalize")
```

(removing `builder.add_edge("auto_post", "finalize")`)

- [x] **Step 4: Regenerate the diagram and run**

```bash
.venv/bin/python -m scripts.render_graph
.venv/bin/python -m pytest tests/test_batch_graph.py -v
```

Expected: all PASS, including `test_the_committed_diagram_matches_the_graph`.

- [x] **Step 5: `make check` and commit**

```bash
make check
git add src/invoice_agent/graphs/batch.py tests/test_batch_graph.py docs/diagrams
git commit -m "feat(m5b): open one approval thread per flagged invoice, then return

The batch does not wait. Each thread runs to interrupt() and stops with its
state checkpointed, so a reviewer may decide hours later from another process —
the entire justification for three graphs rather than one.

approvals.invoice_id is a NOT NULL FK, so an invoice that never persisted gets
no thread and is reported in `unopened` rather than dropped.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 8: `scripts/recover_pending.py`

**Files:**
- Create: `scripts/recover_pending.py`, `tests/test_recover_pending.py`

**Interfaces:**
- Consumes: `approvals.list_pending`, `build_approval_graph(...).get_state`
- Produces: `scan() -> dict[str, list[dict[str, Any]]]` with keys `"pending"` and `"orphaned"`; `main() -> None`

- [x] **Step 1: Write the failing tests**

Create `tests/test_recover_pending.py`:

```python
"""`recover_pending` — design §5.5.

Run from a COLD PROCESS. The claim under test is that the API never assumes an
in-RAM registry survived a restart; a test running in the process that created
the threads assumes exactly that, and would pass with the whole mechanism
missing.
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


def _run_scan_in_a_fresh_process() -> dict:
    project_root = Path(__file__).parent.parent
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json;from scripts.recover_pending import scan;print(json.dumps(scan()))",
        ],
        capture_output=True,
        text=True,
        cwd=project_root,
        env={"PYTHONPATH": str(project_root), **_clean_env()},
    )
    assert out.returncode == 0, f"scan crashed:\n{out.stderr}"
    return json.loads(out.stdout.strip().splitlines()[-1])


def _clean_env() -> dict:
    import os

    return {k: v for k, v in os.environ.items()}


def test_a_parked_thread_is_found_by_a_process_that_never_created_it(committed_rows):
    from invoice_agent.checkpointer import build_checkpointer
    from invoice_agent.graphs import approval

    db_id, approval_id = _persisted_invoice(committed_rows)
    with build_checkpointer() as saver:
        saver.setup()
        app = approval.build_approval_graph(checkpointer=saver)
        app.invoke(
            _initial_state(db_id, approval_id),
            {"configurable": {"thread_id": f"appr:{db_id}"}},
        )

    found = _run_scan_in_a_fresh_process()
    assert str(db_id) in [p["invoice_id"] for p in found["pending"]]


def test_an_approval_row_with_no_live_interrupt_is_reported_as_orphaned(committed_rows):
    """The case worth writing the script for. A row says a human is expected but
    no thread is waiting — the invoice would sit unreviewed forever. A script
    that reported only the healthy case would hide the failure it exists to
    find.
    """
    _db_id, _approval_id = _persisted_invoice(committed_rows)  # row, but no graph run

    found = _run_scan_in_a_fresh_process()
    assert str(_db_id) in [o["invoice_id"] for o in found["orphaned"]]
    assert str(_db_id) not in [p["invoice_id"] for p in found["pending"]]
```

Import `_persisted_invoice`, `_initial_state` and the `committed_rows` fixture from `tests/test_approval_graph.py` — move them into `tests/helpers/approval_fixtures.py` in this task and import from there in both files, rather than copying. Copying is how the two drift.

- [x] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_recover_pending.py -v
```

Expected: FAIL — `No module named 'scripts.recover_pending'`.

- [x] **Step 3: Implement**

Create `scripts/recover_pending.py`:

```python
"""Rebuild the reviewer queue after a restart (design doc §5.5).

WHAT IS LOST IS NOT THE STATE. The state is in Postgres. What is lost is the
LIST of which threads are waiting — LangGraph has no "enumerate pending threads"
API, so you have to already know which ones to ask about.

`approvals WHERE decision IS NULL` is that list. For each row, ask the graph
whether an interrupt is genuinely pending.

Two outcomes, and the second is why this script exists:

  pending   row undecided, interrupt live      -> back in the queue
  orphaned  row undecided, NO live interrupt   -> nobody will ever be asked

An orphan is an invoice a human waits for forever. A script that reported only
the healthy case would hide exactly the failure it was written to find.
"""

import json
from typing import Any

from invoice_agent.checkpointer import build_checkpointer
from invoice_agent.db.engine import get_engine
from invoice_agent.db.repositories import approvals as approvals_repo
from invoice_agent.graphs.approval import build_approval_graph


def scan() -> dict[str, list[dict[str, Any]]]:
    with get_engine().begin() as conn:
        rows = approvals_repo.list_pending(conn)

    pending: list[dict[str, Any]] = []
    orphaned: list[dict[str, Any]] = []
    if not rows:
        return {"pending": pending, "orphaned": orphaned}

    with build_checkpointer() as saver:
        graph = build_approval_graph(checkpointer=saver)
        for row in rows:
            config = {"configurable": {"thread_id": row["thread_id"]}}
            snapshot = graph.get_state(config)
            record = {
                "invoice_id": str(row["invoice_id"]),
                "approval_id": str(row["id"]),
                "thread_id": row["thread_id"],
            }
            # `.interrupts` is the live-interrupt list on the state snapshot.
            # An empty `.next` with no interrupts means the thread never ran or
            # its checkpoints are gone.
            if getattr(snapshot, "interrupts", ()):
                pending.append(record)
            else:
                orphaned.append(record)

    return {"pending": pending, "orphaned": orphaned}


def main() -> None:
    found = scan()
    print(json.dumps(found, indent=2, ensure_ascii=False))
    print(
        f"\n{len(found['pending'])} awaiting a reviewer, "
        f"{len(found['orphaned'])} orphaned (undecided with no live interrupt)"
    )


if __name__ == "__main__":
    main()
```

**Verify `snapshot.interrupts` exists on langgraph 1.2.9 before relying on it.** Run:

```bash
.venv/bin/python -c "
from langgraph.types import StateSnapshot
print([f for f in StateSnapshot._fields])"
```

If `interrupts` is absent, use `snapshot.tasks` and check `task.interrupts` on each — and record which one you used, with the langgraph version, in the module docstring.

- [x] **Step 4: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_recover_pending.py -v
```

Expected: both PASS.

- [x] **Step 5: `make check` and commit**

```bash
make check
git add scripts/recover_pending.py tests/test_recover_pending.py tests/helpers/approval_fixtures.py tests/test_approval_graph.py
git commit -m "feat(m5b): add recover_pending, which reports orphans as well as pending

What a restart loses is not the state — that is in Postgres — but the LIST of
which threads are waiting, because LangGraph has no API to enumerate them.

Reports orphans separately: a row that says a human is expected while no thread
waits is an invoice nobody will ever be asked about, and a script reporting only
the healthy case would hide the failure it exists to find. Tested from a cold
process, since the claim is that no in-RAM registry is assumed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 9: Retarget portfolio test 1 onto the real graph

**Files:**
- Modify: `tests/helpers/hitl_child.py`, `tests/test_hitl_survives_restart.py`
- Delete: `tests/helpers/spike_graph.py`, `tests/test_spike_graph.py`

**Interfaces:**
- Consumes: `build_approval_graph`, `submit_decision`
- Produces: nothing importable — this task produces evidence

**Language note:** the passages you rewrite become English (global constraint 7). `_interrupt_persisted` and `_wait_for_interrupt` are untouched and keep their Vietnamese docstrings. Do not mass-translate.

- [x] **Step 1: Rewrite the child to run the real graph, counting test-side**

`tests/helpers/hitl_child.py` — it now takes an invoice id and an approval id:

```python
"""Runs the REAL approval graph to its interrupt, in a process built to be killed.

Must be a real process (`sys.executable`), not a thread or a fork sharing
memory: the whole point is that state survives the process DYING — no
`__del__`, no atexit flush, no surviving Python cache.

THE COUNTERS LIVE HERE, NOT IN `approval.py`. The assertion "suggest ran once,
approval_gate ran twice" cannot come from checkpoint history — measured
2026-07-27 at M1.5: the gate's first pass raises GraphInterrupt and commits
nothing, so it appears once in `trace` whether its body ran once or twice. So
the nodes are wrapped HERE, before compiling, and `approval.py` never learns it
is being measured.

Usage: python -m tests.helpers.hitl_child <invoice_id> <approval_id>
Prints "INTERRUPTED" (flushed) when the graph parks, then sleeps forever.
"""

import sys
import time

from invoice_agent.checkpointer import build_checkpointer
from invoice_agent.graphs import approval


def _counting(fn, thread_id: str, node: str):
    def wrapped(state, *args, **kwargs):
        import psycopg

        from invoice_agent.config import get_settings

        with psycopg.connect(get_settings().app_dsn) as conn:
            conn.execute(
                "INSERT INTO app.spike_counter (thread_id, node) VALUES (%s, %s)",
                (thread_id, node),
            )
        return fn(state, *args, **kwargs)

    return wrapped


def build_counted_graph(thread_id: str, saver):
    """The real graph, with two nodes wrapped in counters."""
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(approval.ApprovalState)
    builder.add_node(
        "load_snapshot", _counting(approval.load_snapshot_node, thread_id, "load_snapshot")
    )
    builder.add_node("suggest", _counting(approval.suggest_node, thread_id, "suggest"))
    builder.add_node(
        "approval_gate", _counting(approval.approval_gate_node, thread_id, "approval_gate")
    )
    builder.add_node("apply_decision", approval.apply_decision_node)
    builder.add_node("post_entry", approval.post_entry_node)
    builder.add_node("record_rejection", approval.record_rejection_node)
    builder.add_node("learn", approval.learn_node)

    builder.add_edge(START, "load_snapshot")
    builder.add_edge("load_snapshot", "suggest")
    builder.add_edge("suggest", "approval_gate")
    builder.add_edge("approval_gate", "apply_decision")
    builder.add_conditional_edges(
        "apply_decision",
        approval.route_after_decision,
        {"post_entry": "post_entry", "record_rejection": "record_rejection"},
    )
    builder.add_edge("post_entry", "learn")
    builder.add_edge("record_rejection", "learn")
    builder.add_edge("learn", END)
    return builder.compile(checkpointer=saver)


def main(invoice_id: str, approval_id: str) -> None:
    thread_id = f"appr:{invoice_id}"
    config = {"configurable": {"thread_id": thread_id}}
    with build_checkpointer() as saver:
        saver.setup()
        app = build_counted_graph(thread_id, saver)
        result = app.invoke(
            {
                "invoice_id": invoice_id,
                "approval_id": approval_id,
                "snapshot": {},
                "flags": [],
                "suggestions": [],
                "decision": "",
                "decided_by": "",
                "reason": None,
                "corrections": None,
                "entry_id": None,
                "status": "",
            },
            config,
        )
        if "__interrupt__" not in result:
            print("NO_INTERRUPT", flush=True)
            sys.exit(2)
        print("INTERRUPTED", flush=True)

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
```

**A drift guard is mandatory here.** `build_counted_graph` duplicates the real
topology, so it can silently diverge. Add to `tests/test_approval_graph.py`:

```python
def test_the_counted_graph_matches_the_real_one():
    """`hitl_child.build_counted_graph` re-declares the topology in order to wrap
    two nodes. A copy of a graph is a copy that drifts — so this compares the
    rendered mermaid of both, and portfolio test 1 stops measuring the real
    graph the moment they differ."""
    from invoice_agent.checkpointer import build_checkpointer
    from tests.helpers.hitl_child import build_counted_graph

    with build_checkpointer() as saver:
        counted = build_counted_graph("appr:x", saver).get_graph().draw_mermaid()
        real = approval.build_approval_graph(checkpointer=saver).get_graph().draw_mermaid()
    assert counted == real
```

- [x] **Step 2: Rewrite the test body**

In `tests/test_hitl_survives_restart.py`: keep `_count`, `_interrupt_persisted` and `_wait_for_interrupt` verbatim. Replace the import of `build_spike_graph` with the approval imports and the fixtures helper, and rewrite `test_hitl_survives_restart` in English:

```python
@pytest.mark.parametrize("run", range(HITL_RUNS))
def test_hitl_survives_restart(requires_postgres, committed_rows, run):
    """PORTFOLIO TEST 1, now on the REAL approval graph.

    Proves what a confirm dialog in a shallow agent demo cannot: the human
    decision may arrive after the process that asked for it has died.

    Asserts BOTH halves.

    Mechanism — `suggest` = 1, `approval_gate` = 2 across the process boundary.
    The gate's node runs again from the top on resume; that is LangGraph
    semantics, not a bug, and it is why `post_entry` is a separate node.

    Consequence — exactly ONE journal entry, ONE audit grant, ONE decided
    approval. M1 could only count node executions because the spike had no real
    side effect to count. This graph does, and the consequence is the property
    that actually matters.
    """
    db_id, approval_id = _persisted_invoice(committed_rows)
    project_root = Path(__file__).parent.parent
    env = {**os.environ, "PYTHONPATH": str(project_root)}
    thread_id = f"appr:{db_id}"
    child = subprocess.Popen(
        [sys.executable, "-m", "tests.helpers.hitl_child", str(db_id), str(approval_id)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        _wait_for_interrupt(requires_postgres, thread_id, child)

        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
        assert child.returncode == -signal.SIGKILL, (
            f"the child must die of SIGKILL, got rc={child.returncode}"
        )

        assert _count(requires_postgres, thread_id, "suggest") == 1
        assert _count(requires_postgres, thread_id, "approval_gate") == 1

        # A brand-new process. Nothing from the dead one is reused.
        result = approval.submit_decision(
            invoice_id=str(db_id), decision="approved", decided_by="ketoan"
        )
        assert "__interrupt__" not in result, "the resume was ignored"
        assert result["status"] == "learned"

        assert _count(requires_postgres, thread_id, "suggest") == 1, (
            "a node BEFORE the gate must not run twice"
        )
        assert _count(requires_postgres, thread_id, "approval_gate") == 2, (
            "the node CONTAINING interrupt() runs again from the top on resume"
        )

        with get_engine().begin() as conn:
            entries = list(
                conn.execute(
                    select(models.journal_entries).where(
                        models.journal_entries.c.invoice_id == db_id
                    )
                ).mappings()
            )
            grants = [
                r
                for r in conn.execute(
                    select(models.audit_log).where(
                        models.audit_log.c.target_id == str(db_id)
                    )
                ).mappings()
                if r["allowed"]
            ]
        assert len(entries) == 1, "the irreversible write happened more than once"
        assert entries[0]["posting_authority"] == "human_approved"
        assert len(grants) == 1
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        with psycopg.connect(requires_postgres) as conn:
            conn.execute("DELETE FROM app.spike_counter WHERE thread_id = %s", (thread_id,))
```

- [x] **Step 3: Run it hard**

```bash
HITL_RUNS=10 .venv/bin/python -m pytest tests/test_hitl_survives_restart.py -v
```

Expected: **10/10 PASS**. If any run is flaky, fix the cause — do not lower `HITL_RUNS`. `test_ci_runs_the_full_ten` exists to stop exactly that.

- [x] **Step 4: Delete the spike, only now**

```bash
git rm tests/helpers/spike_graph.py tests/test_spike_graph.py
.venv/bin/python -m pytest -q
```

Expected: green. If anything still imports the spike, fix the import — do not restore the file.

The `spike_counter` **table stays**: it is now the node-execution counter for this test. Renaming it is a migration whose only payoff is tidiness (spec §3.2).

- [x] **Step 5: `make check` and commit**

```bash
make check
git add -A
git commit -m "test(m5b): retarget portfolio test 1 onto the real approval graph

The durability proof now runs the real graph: kill -9 between the interrupt and
the decision, resume in a new process through submit_decision, post. It asserts
the mechanism (suggest=1, approval_gate=2 across the process boundary) and the
consequence M1 could not — exactly one journal entry, one audit grant, one
decided approval — because the spike had no real side effect to count.

The counters live in tests/helpers/hitl_child.py, not in approval.py: checkpoint
history cannot show gate=2, since the first pass raises GraphInterrupt and
commits nothing. A drift guard compares the counted graph's topology to the real
one.

Retires tests/helpers/spike_graph.py as its own docstring instructed. The
spike_counter table stays, now as the node-execution counter.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 10: `test_batch_not_blocked_by_pending.py` — portfolio test 4

**Files:**
- Create: `tests/test_batch_not_blocked_by_pending.py`

**Interfaces:**
- Consumes: `batch.build_batch_graph`, `scripts.generator.gate_batch`
- Produces: evidence

- [x] **Step 1: Write the test**

```python
"""PORTFOLIO TEST 4 — the test that justifies three graphs instead of one.

Design §1.1: a single graph would hold the whole batch open for hours while one
invoice waits for a person. This asserts the batch COMPLETES while approvals are
still undecided.

IT ASSERTS THE RELATIONSHIP, NOT THE COUNTS. `gate_batch.json` declares budget
chains, not an auto/pending split — M5-A deliberately kept that split *reported,
not targeted*, because the finding rate is a property of the corpus. Pinning
today's 15/12 here would reverse that decision and turn this into a
change-detector for corpus generation. The measured numbers live in the note.
"""

import json
import pathlib
import uuid

import pytest
from sqlalchemy import select

from invoice_agent.db import models
from invoice_agent.db.engine import get_engine
from invoice_agent.graphs import batch


def test_the_batch_completes_while_approvals_are_still_open(requires_postgres, monkeypatch):
    monkeypatch.setenv("LLM_MODE", "replay")
    for key in ("GROQ_API_KEYS", "GEMINI_API_KEYS", "OPENROUTER_API_KEYS"):
        monkeypatch.delenv(key, raising=False)
    # WITHOUT THIS THE MONKEYPATCH DOES NOTHING — `get_settings` is lru_cached,
    # so a Settings built earlier in the session keeps llm_mode="live" with the
    # real keys loaded. Measured 2026-08-01 at M4-D: a gate test was making LIVE
    # calls and passed only because a key happened to be configured.
    from invoice_agent.config import get_settings

    get_settings.cache_clear()

    from scripts.generator import gate_batch

    manifest = pathlib.Path("corpus/manifest.jsonl")
    if not manifest.exists():
        # SKIP, not return: a bare return reports as a PASS, indistinguishable
        # from having run the gate.
        pytest.skip("corpus not generated — run `make generate`")

    rows = {r["invoice_id"]: r for r in map(json.loads, manifest.open(encoding="utf-8"))}
    inputs = [
        {
            "batch_id": "not-blocked",
            "invoice_id": i,
            "source_path": rows[i]["path"],
            "source_type": rows[i]["source_type"],
            "pdf_path": rows[i].get("pdf_path"),
            "department": rows[i].get("department"),
        }
        for i in gate_batch.GATE_BATCH_IDS
    ]

    result = batch.build_batch_graph().invoke(
        {
            "batch_id": str(uuid.uuid4()),
            "inputs": inputs,
            "enriched": [],
            "failed": [],
            "persisted": [],
            "consumption": [],
            "raised": [],
            "unroutable": [],
            "auto": [],
            "needs_approval": [],
            "posted": [],
            "opened": [],
            "unopened": [],
            "status": "",
        }
    )

    # THE CLAIM: both are true at the same moment.
    assert result["posted"], "nothing posted — the auto path is not exercised"
    assert result["opened"], "no approval parked — the HITL path is not exercised"
    assert result["status"] == "completed", "the batch did not finish while approvals waited"

    posted_ids = {uuid.UUID(p["db_id"]) for p in result["posted"]}
    opened_ids = {uuid.UUID(o["db_id"]) for o in result["opened"]}
    assert not (posted_ids & opened_ids), "an invoice was both posted and parked"

    with get_engine().begin() as conn:
        undecided = {
            r["invoice_id"]
            for r in conn.execute(
                select(models.approvals).where(models.approvals.c.decision.is_(None))
            ).mappings()
        }
        entries = {
            r["invoice_id"]
            for r in conn.execute(select(models.journal_entries)).mappings()
        }
        batch_row = conn.execute(
            select(models.batches).where(models.batches.c.id == uuid.UUID(result["batch_id"]))
        ).mappings().first()

    assert opened_ids <= undecided, "a parked invoice is not awaiting a decision"
    assert posted_ids <= entries, "a posted invoice has no journal entry"
    assert not (opened_ids & entries), "a parked invoice was posted anyway"
    assert batch_row["status"] == "completed"
```

- [x] **Step 2: Run it**

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -tAc "TRUNCATE app.invoices CASCADE;"
.venv/bin/python -m pytest tests/test_batch_not_blocked_by_pending.py -v
```

Expected: PASS. Record the measured `len(posted)` / `len(opened)` for the note.

- [x] **Step 3: `make check` and commit**

```bash
make check
git add tests/test_batch_not_blocked_by_pending.py
git commit -m "test(m5b): add portfolio test 4 — the batch completes while approvals wait

Asserts the relationship, not the counts: posted and opened both non-empty at
the same moment, disjoint, every parked invoice undecided and unposted, batch
status completed. gate_batch.json declares budget chains, not an auto/pending
split, and M5-A deliberately kept that split reported rather than targeted.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 11: Mutation verification

Its own task, because M1's exit review found four tests that passed while guarding nothing and every milestone since has found more.

**Files:**
- Create: `docs/notes/2026-08-01-m5b-approval-graph.md` (started here)

- [x] **Step 1: Mutation 1 — `apply_decision` shares `post_entry`'s transaction**

Give `apply_decision_node` a `conn` parameter that, when supplied, is used instead of opening its own; then in `post_entry_node` open one connection and pass it to both. Run:

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -k "commits_so_a_separate or accepts_no_connection" -v
```

Expected: **RED**. Revert. This is the mutation spec §2.3 exists for — it is the only one that can tell a real guard from one verifying its own caller.

- [x] **Step 2: Mutation 2 — a write inside the gate node**

Add `invoices_repo.set_status(conn, ..., "pending_approval")` to `approval_gate_node` before the `interrupt()` call. Run:

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -k gate_node -v
HITL_RUNS=3 .venv/bin/python -m pytest tests/test_hitl_survives_restart.py -q
```

Expected: the static guard is **RED** immediately; record whether the durability test also goes red. Revert.

- [x] **Step 3: Mutation 3 — `apply_decision` stops resolving flags**

Delete the `resolve_flag` loop. Run:

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -k closes_its_flags -v
```

Expected: **RED** (both parametrised cases). Revert.

- [x] **Step 4: Mutation 4 — `submit_decision` skips validation**

Delete the `if decision not in VALID_DECISIONS` check. Run:

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -k unknown_decision -v
```

Expected: **RED**. Revert.

- [x] **Step 5: Mutation 5 — `learn` records only rejections**

Wrap `record_episode` in `if state["decision"] == "rejected":`. Run:

```bash
.venv/bin/python -m pytest tests/test_approval_graph.py -k "approval_is_remembered" -v
```

Expected: **RED**. Revert.

- [x] **Step 6: Confirm clean, write the table**

```bash
git status --short   # must be empty
make check
```

Create `docs/notes/2026-08-01-m5b-approval-graph.md` with a `## Mutation results` section recording the **measured** pass/fail for all five, including whether mutation 2 turned the durability test red as well as the static guard.

- [x] **Step 7: Commit**

```bash
git add docs/notes/2026-08-01-m5b-approval-graph.md
git commit -m "docs(m5b): record the five-mutation verification

Mutation 1 is the load-bearing one: give apply_decision a conn parameter and
share post_entry's transaction, and the guard verifies its own caller's
uncommitted write. Nothing else detects it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 12: The live `max_concurrency` measurement, and closing the milestone

**Files:**
- Modify: `docs/notes/2026-08-01-m5b-approval-graph.md`, `docs/Plan_Project_F_2026-07-25.md`, `src/invoice_agent/config.py` (only if the measurement says so)

- [x] **Step 1: Baseline the replay run**

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -tAc "TRUNCATE app.invoices CASCADE; UPDATE app.vendors SET invoice_count=0,total_amount=0,sum_sq_amount=0,avg_amount=NULL,stddev_amount=NULL;"
time .venv/bin/python -m pytest tests/test_batch_not_blocked_by_pending.py -q
```

Record wall time.

- [x] **Step 2: Run the batch LIVE, once**

Confirm keys are present, then run the same gate batch with `LLM_MODE=live` and `LLM_MODE=record` so fixtures land:

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -tAc "TRUNCATE app.invoices CASCADE; UPDATE app.vendors SET invoice_count=0,total_amount=0,sum_sq_amount=0,avg_amount=NULL,stddev_amount=NULL;"
LLM_MODE=record time .venv/bin/python -m pytest tests/test_batch_not_blocked_by_pending.py -q
```

Then read what actually happened from `llm_calls` — that table records failures too, which is the whole reason M3-A added it:

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c "
SELECT provider, succeeded, count(*),
       count(*) FILTER (WHERE error ILIKE '%429%' OR error ILIKE '%rate%') AS rate_limited
FROM app.llm_calls GROUP BY 1,2 ORDER BY 1,2"
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c "
SELECT model, count(*), round(avg(latency_ms)) AS avg_ms FROM app.llm_calls GROUP BY 1 ORDER BY 2 DESC"
```

- [x] **Step 3: Record the numbers, and act on them or say why not**

Into the note: 429s per provider, invoices failed, wall time live vs replay, and whether `max_concurrency=4` survived. M4-C measured **177** Gemini rate-limits in a *serial* run, so this is the first honest test of the control.

If 4 is too high, change `config.py:126` **and** put the date, the measurement and this note's filename in a comment beside it — CLAUDE.md's rule. If 4 held, say so; an unchanged constant with a measurement behind it is a result.

State plainly that these fixtures **do** travel across platforms (the text replay key hashes `(model, messages, params)`), unlike the vision fixtures whose key hashes a rasterized PNG — paying this debt does not pay that one.

- [x] **Step 4: Finish the note**

Add to `docs/notes/2026-08-01-m5b-approval-graph.md`:

- the measured portfolio test 1 result (`HITL_RUNS=10`, 10/10) and both halves it asserts
- portfolio test 4's measured posted/opened counts, labelled as reported-not-targeted
- **`suggest` returned `[]` for every invoice on a fresh database** — spec §7 criterion 7, reported rather than hidden, with the consequence that item 4.4's tuning stays deferred
- `recover_pending` output: pending and orphaned counts
- spec §9's four open questions, with anything M5-B answered marked as such

- [x] **Step 5: Update the roadmap**

In `docs/Plan_Project_F_2026-07-25.md`: mark 5.1, 5.2, 5.4, 5.5 done in the M5 table; add the M5-B line to the sub-milestone block in §3; update the §7 M5 row; update §8's portfolio-test table (test 1 now runs on the real graph; test 4 ✅); and move `max_concurrency` off §1's owed list.

- [x] **Step 6: `make check`, push, confirm CI**

```bash
make check
git add -A
git commit -m "docs(m5b): close M5-B on measured numbers

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
git push
gh run watch
```

**M5-B is not closed until `gh run watch` reports green on this exact SHA**, and until you have confirmed `test_hitl_survives_restart.py` did not SKIP in CI. Five milestones have failed this rule.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §2.1 graph shape | 2, 4, 5 |
| §2.2 plain-JSON state | 1 |
| §2.3 commit boundary | 3, 11 (mutation 1) |
| §2.4 `submit_decision` seam | 6 |
| §2.5 `open_approval`, `unopened` | 7 |
| §4.1 `load_snapshot` | 1 |
| §4.2 `suggest`, empty-on-fresh | 1, 12 (reported) |
| §4.3 `approval_gate` | 2 |
| §4.4 `apply_decision`, `resolve_flag` | 3 |
| §4.5 `post_entry`/`record_rejection`, actor prefix | 4 |
| §4.6 `learn` | 5 |
| §4.7 `recover_pending` | 8 |
| §5.1 portfolio test 1 retargeted | 9 |
| §5.2 portfolio test 4 | 10 |
| §5.3 five mutations | 11 |
| §5.4 cold-process recovery | 8 |
| §6 live `max_concurrency` | 12 |
| §7 criteria 1–8 | 9 (1), 10 (2), 11 (3), 5 (4), 8 (5), 12 (6,7,8) |

No gaps.

**Type consistency:** `ApprovalState` keys are identical in Tasks 1, 2, 7, 9. `submit_decision`'s signature is identical in Tasks 6, 9. `open_approval_node` returns `opened`/`unopened` in Task 7 and Task 10 reads exactly those. `decided_by` is bare everywhere and prefixed only in `post_entry_node` (Task 4).

**One thing the implementer must verify rather than assume:**

- `StateSnapshot.interrupts` on langgraph 1.2.9 (Task 8 step 3). This is the only API in the plan I could not confirm from the repo — `get_state` is not currently called anywhere in `src/`. The command to check it is in the step; if the attribute is absent, use `snapshot.tasks[*].interrupts` and record which one, with the langgraph version, in the module docstring.

`InvoiceLine`'s and `InvoiceData`'s field names were verified against `extraction/models.py` on 2026-08-01 while writing this plan, so Task 4's code block is accurate rather than indicative.

**Known interaction:** Task 7 changes the batch graph topology, so `test_the_committed_diagram_matches_the_graph` goes red until step 4 regenerates the mermaid.
