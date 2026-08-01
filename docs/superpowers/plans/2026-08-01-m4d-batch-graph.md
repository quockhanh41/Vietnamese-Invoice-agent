# M4-D Batch Graph & Serial Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `Send` fan-out over the enrichment graph, a serial join that persists and reconciles in one transaction, and `test_budget_serialization.py` — proof that arrival order changes nothing.

**Architecture:** Fan-out tasks are read-only; every write happens in one serial node after the implicit join, walking canonical `(issue_date, invoice_series, invoice_number)` order. That single ordering discipline covers budget reconciliation, duplicate detection and vendor-profile updates at once.

**Tech Stack:** LangGraph 1.2.9 (pinned — do not bump) · SQLAlchemy Core · Postgres 17 · Pydantic v2 · pytest.

## Global Constraints

Reproduce spec §0 in full — these are the six that can be satisfied by code that passes every test while proving nothing:

1. **Canonical order `(issue_date, invoice_series, invoice_number)`, never arrival order.** The whole claim M4 exists to prove.
2. **The serial join is ONE transaction.** Persist + profiles + duplicates + reconcile commit together or not at all.
3. **`signals` must keep seeing an empty profile.** `apply_invoice` runs in the serial join, *after* fan-out. Move it earlier and `PRICE_OUTLIER` silently stops firing — M4-B measured the self-inclusion ceiling at 1.79 for n=5.
4. **The 10× shuffle must be genuinely independent.** The natural key is `UNIQUE`; an unrolled second shuffle hits `ON CONFLICT DO NOTHING` and measures nothing while passing.
5. **`route_outcomes` produces lists nothing consumes until M5.** Deliberate.
6. **Green `make check` on the PUSHED commit, CI agreeing.** Four milestones have failed this. M4-C failed it because seven commits were pushed as one batch and CI first saw the tip — **push and verify per task, not at the end.**

Plus standing project rules: authoring English, product text Vietnamese; money is `Decimal`, never `float`; every non-obvious constant carries date + measurement + report filename; never `git add corpus/`; Postgres must be up (`make dev`).

---

### Task 1: Repair the gate batch and close the drift

**Files:**
- Modify: `scripts/generator/gate_batch.py`
- Modify: `tests/test_gate_batch.py`

Do this first: it is independent, and everything downstream measures against this declaration.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gate_batch.py`:

```python
def _declared_comments() -> dict[str, str]:
    """Parse the per-id comments in the frozen list.

    Format, as written by the M4-A discovery script:
        "INV-0001",  # MKT / MARKETING — budget_overrun, new_vendor
    """
    import re

    source = pathlib.Path("scripts/generator/gate_batch.py").read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r'"(INV-\d+)",\s*#\s*(.+)', source):
        out[m.group(1)] = m.group(2).strip()
    return out


def test_gate_batch_comments_still_describe_their_invoices(tmp_path):
    """The assertion whose absence let the batch drift.

    Measured 2026-08-01: INV-0122 and INV-0123 were commented "clean" while the
    corpus had made them `unreadable_pdf`. M4-B's regeneration shifted
    `_allocate_indices` consumption, so the frozen ids moved to different
    invoices — and every existing assertion (count, existence, defect coverage,
    MKT membership, breach chain) still passed, because none of them looks at
    what an id actually IS.

    This parses the comments and checks them against the corpus, so the next
    regeneration that moves an id fails loudly instead of silently.
    """
    _out, records = build(tmp_path)
    by_id = {r["invoice_id"]: r for r in records}
    comments = _declared_comments()

    assert set(comments) == set(gate_batch.GATE_BATCH_IDS), (
        "every frozen id needs a comment and vice versa"
    )

    wrong = []
    for inv_id, comment in comments.items():
        record = by_id[inv_id]
        head, _, defect_text = comment.partition("—")
        dept, _, category = head.strip().partition("/")
        expected_defects = (
            set()
            if defect_text.strip() == "clean"
            else {d.strip() for d in defect_text.split(",") if d.strip()}
        )
        actual = set(record["defects"])
        if dept.strip() != record["department"] or category.strip() != record["category"]:
            wrong.append(f"{inv_id}: comment says {head.strip()}, corpus says "
                         f"{record['department']} / {record['category']}")
        if expected_defects != actual:
            wrong.append(f"{inv_id}: comment says {expected_defects or 'clean'}, "
                         f"corpus says {actual or 'clean'}")
    assert not wrong, wrong


def test_gate_batch_source_types_match_the_declaration(tmp_path):
    """Two members are deliberately `unreadable_pdf` — they exercise "one
    corrupt file does not kill the batch" inside the gate itself rather than in
    a contrived side test. Pinned so neither the count nor the intent drifts."""
    _out, records = build(tmp_path)
    by_id = {r["invoice_id"]: r for r in records}
    chosen = [by_id[i] for i in gate_batch.GATE_BATCH_IDS]

    pdfs = [r["invoice_id"] for r in chosen if r["source_type"] != "xml"]
    assert sorted(pdfs) == sorted(gate_batch.DELIBERATE_PDF_MEMBERS)
    assert all("unreadable_pdf" in by_id[i]["defects"] for i in pdfs)
    assert len(chosen) - len(pdfs) == 28
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gate_batch.py -v`
Expected: FAIL — `DELIBERATE_PDF_MEMBERS` undefined, and the comment test reports `INV-0122` / `INV-0123` mismatches.

- [ ] **Step 3: Repair the declaration**

In `scripts/generator/gate_batch.py`, add above `GATE_BATCH_IDS`:

```python
# The two members that are NOT XML, named so the composition cannot drift
# unnoticed again. They are `unreadable_pdf` — deliberately corrupt files —
# and they are in the batch ON PURPOSE: they exercise the gate's own criterion
# that one corrupt file lands in `failed`/`manual_queue` without killing the
# batch, inside the gate rather than in a contrived side test. M3-C established
# they reach `manual_queue` without spending a token, so they need no fixture
# and the gate still replays with no API key.
#
# They arrived by accident, not design: M4-B's regeneration shifted
# `_allocate_indices` consumption and moved these two ids onto different
# invoices, while every assertion then in `test_gate_batch.py` kept passing
# because none looked at what an id actually was (spec 2026-08-01-m4d §1.1).
# Keeping them is a decision; the two tests added with this comment are what
# make it a decision rather than a drift.
DELIBERATE_PDF_MEMBERS = ("INV-0122", "INV-0123")
```

Then correct those two entries in the frozen list — replace the stale
`# IT / IT_SERVICES — clean` and `# ADMIN / OFFICE_SUPPLIES — clean` with the
department, category and defects the corpus actually has. Get them from:

```bash
make generate
.venv/bin/python -c "
import json
rows={r['invoice_id']: r for r in map(json.loads, open('corpus/manifest.jsonl'))}
for i in ('INV-0122','INV-0123'):
    r=rows[i]; print(f'    \"{i}\",  # {r[\"department\"]} / {r[\"category\"]} — {\", \".join(r[\"defects\"])}')
"
```

Paste the printed lines verbatim. Then re-run the same command for **every** id
in the frozen list and fix any other comment that has drifted — the new test
checks all 30, not just these two.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gate_batch.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Mutation-verify**

Change one comment's category to a wrong value → `test_gate_batch_comments_still_describe_their_invoices` red. Add a third id to `DELIBERATE_PDF_MEMBERS` → `test_gate_batch_source_types_match_the_declaration` red. Restore both, confirm green.

- [ ] **Step 6: Commit**

```bash
git add scripts/generator/gate_batch.py tests/test_gate_batch.py
git commit -m "fix(corpus): repair the gate batch and close the drift that hid it

INV-0122/INV-0123 were commented 'clean' while the corpus had made them
unreadable_pdf: M4-B's regeneration shifted _allocate_indices consumption
and moved the frozen ids onto different invoices. Every existing
assertion still passed, because none looked at what an id actually is.

They stay, deliberately — two corrupt files exercise 'one corrupt file
does not kill the batch' inside the gate rather than in a side test — and
two new tests make that a decision instead of a drift: one pins the
source-type composition, the other parses the per-id comments and checks
them against the corpus, so the next regeneration fails loudly."
```

- [ ] **Step 7: `make check`, push, confirm CI green**

Constraint 6 — per task, not at the end.

---

### Task 2: `enrich_one` wrapper and the per-run `thread_id`

**Files:**
- Create: `src/invoice_agent/graphs/batch.py`
- Test: `tests/test_batch_enrich_one.py`

**Interfaces:**
- Consumes: `graphs.enrichment.build_enrichment_graph`, `EnrichmentState`.
- Produces: `BatchState` (TypedDict); `enrich_one(state, *, graph) -> dict`; `thread_id_for(batch_id, invoice_id) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_batch_enrich_one.py`:

```python
"""The wrapper function node — design §4.1, and M3-C §8.3's unpaid debt.

§4.1 gives two reasons for a wrapper rather than a compiled subgraph, and M3-C
recorded that neither had ever been proven: explicit state mapping, and a
try/except per task so one bad file cannot end the superstep.
"""

import uuid

import pytest

from invoice_agent.graphs import batch


def _task(invoice_id="INV-0001", path="corpus/INV-0001.xml", batch_id=None):
    return {
        "batch_id": str(batch_id or uuid.uuid4()),
        "invoice_id": invoice_id,
        "source_path": path,
        "pdf_path": None,
        "source_type": "xml",
    }


def test_thread_id_carries_the_batch(sample_invoice):
    """M3-C §8.5: `enrich:{invoice_id}` is stable across re-runs, so a second
    batch over the same invoice resumes the first batch's checkpoints instead of
    starting clean."""
    a, b = uuid.uuid4(), uuid.uuid4()
    assert batch.thread_id_for(a, "INV-0001") != batch.thread_id_for(b, "INV-0001")
    assert str(a) in batch.thread_id_for(a, "INV-0001")


def test_a_raising_task_lands_in_failed_without_ending_the_superstep():
    """Design §4.1's second reason. Without the try/except one broken PDF ends a
    30-invoice batch."""
    class Exploding:
        def invoke(self, *a, **k):
            raise RuntimeError("boom")

    out = batch.enrich_one(_task(), graph=Exploding())
    assert out["enriched"] == []
    assert len(out["failed"]) == 1
    assert out["failed"][0]["invoice_id"] == "INV-0001"
    assert "boom" in out["failed"][0]["error"]


def test_state_is_mapped_explicitly_not_by_name(sample_invoice):
    """§4.1 calls implicit key matching the number-one source of
    incomprehensible LangGraph bugs. The wrapper must build EnrichmentState
    field by field — so a task dict carrying an unexpected key must not leak
    into the enrichment state."""
    seen = {}

    class Recording:
        def invoke(self, state, config=None):
            seen.update(state)
            return {**state, "extracted": sample_invoice, "findings": [], "signals": []}

    task = _task()
    task["unexpected_key"] = "must not leak"
    batch.enrich_one(task, graph=Recording())
    assert "unexpected_key" not in seen
    assert seen["invoice_id"] == "INV-0001"
    assert seen["source_type"] == "xml"


def test_successful_task_returns_a_delta_not_the_whole_list():
    """`enriched` and `failed` accumulate with operator.add across Sends. A node
    returning the accumulated list makes each superstep double the last — the
    reducer trap design §3.1 names and M3 already hit once."""
    class Ok:
        def invoke(self, state, config=None):
            return {**state, "extracted": None, "findings": [], "signals": []}

    out = batch.enrich_one(_task(), graph=Ok())
    assert isinstance(out["enriched"], list)
    assert len(out["enriched"]) <= 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_batch_enrich_one.py -v`
Expected: FAIL — `ModuleNotFoundError: invoice_agent.graphs.batch`

- [ ] **Step 3: Write the module**

Create `src/invoice_agent/graphs/batch.py`:

```python
"""batch_graph — fan out over invoices, then join serially (design doc §4).

THE SHAPE IS THE ARGUMENT. Every fan-out task is READ-ONLY; every write happens
in one serial node after the join. That single decision buys three properties at
once, and they are the milestone:

  * budget reconciliation is order-invariant, because one walker in canonical
    order draws down the budget;
  * duplicate detection is order-invariant, because the same walker decides
    which member of a pair is the original;
  * `signals` never sees the invoice it is scoring in that invoice's own vendor
    statistics, because `apply_invoice` runs here rather than in the task.

That third one is silent when broken. M4-B measured the self-inclusion ceiling:
a single extreme point's z-score against a sample containing itself is bounded
by (n-1)/sqrt(n) — 1.79 at n=5 — so persisting before `signals` runs does not
fail, it just stops PRICE_OUTLIER ever firing.
"""

import operator
import uuid
from typing import Annotated, Any, TypedDict

from invoice_agent.extraction.models import InvoiceData


class EnrichedInvoice(TypedDict):
    """What a fan-out task hands the serial join. Deliberately small: the join
    re-derives everything else from the database."""

    invoice_id: str
    source_path: str
    source_type: str
    extracted: InvoiceData | None
    findings: list[Any]
    signals: list[Any]
    status: str


class BatchState(TypedDict):
    batch_id: str
    inputs: list[dict[str, Any]]
    # Accumulate across Sends. Nodes return DELTAS — returning the accumulated
    # list makes each superstep double the last (design §3.1's reducer trap).
    enriched: Annotated[list[EnrichedInvoice], operator.add]
    failed: Annotated[list[dict[str, str]], operator.add]
    persisted: list[dict[str, Any]]
    consumption: list[dict[str, Any]]
    auto: list[str]
    needs_approval: list[str]
    status: str


def thread_id_for(batch_id: uuid.UUID | str, invoice_id: str) -> str:
    """Design §1.3's correlation key, with the per-run component M3-C §8.5 owes.

    `enrich:{invoice_id}` alone is stable across re-runs, so a second batch over
    the same invoice resumes the first batch's checkpoints rather than starting
    clean — and the interrupt/resume semantics M1 proved would then apply to the
    wrong run.
    """
    return f"enrich:{batch_id}:{invoice_id}"


def enrich_one(task: dict[str, Any], *, graph: Any) -> dict[str, Any]:
    """One invoice through the enrichment graph. A WRAPPER, not a subgraph.

    Design §4.1: map state explicitly rather than letting LangGraph match keys
    by name, and wrap each task so a raising file lands in `failed` instead of
    ending the superstep.
    """
    invoice_id = task["invoice_id"]
    state = {
        "invoice_id": invoice_id,
        "source_path": task["source_path"],
        "pdf_path": task.get("pdf_path"),
        "source_type": task["source_type"],
        "strategy": "xml" if task["source_type"] == "xml" else "vision",
        "extracted": None,
        "extraction_attempts": 0,
        "current_errors": [],
        "error_history": [],
        "findings": [],
        "signals": [],
        "llm_calls": [],
        "classification": None,
        "audit_verdict": None,
        "status": "",
    }
    config = {"configurable": {"thread_id": thread_id_for(task["batch_id"], invoice_id)}}

    try:
        result = graph.invoke(state, config)
    except Exception as exc:  # noqa: BLE001 - a bad file must not end the batch
        return {"enriched": [], "failed": [{"invoice_id": invoice_id, "error": str(exc)}]}

    return {
        "enriched": [
            {
                "invoice_id": invoice_id,
                "source_path": task["source_path"],
                "source_type": task["source_type"],
                "extracted": result.get("extracted"),
                "findings": result.get("findings") or [],
                "signals": result.get("signals") or [],
                "status": result.get("status") or "",
            }
        ],
        "failed": [],
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_batch_enrich_one.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Mutation-verify both §4.1 reasons**

Remove the `try/except` (let the exception propagate) → `test_a_raising_task_lands_in_failed…` red. Replace the explicit state dict with `state = dict(task)` → `test_state_is_mapped_explicitly_not_by_name` red. Revert `thread_id_for` to `f"enrich:{invoice_id}"` → `test_thread_id_carries_the_batch` red. Restore all three.

- [ ] **Step 6: Commit, `make check`, push, confirm CI**

```bash
git add src/invoice_agent/graphs/batch.py tests/test_batch_enrich_one.py
git commit -m "feat(batch): enrich_one wrapper node and per-run thread_id

Pays two M3-C debts. §8.3: nothing proved the enrichment graph survives
function-node wrapping — now three tests do, one per reason design §4.1
gives. §8.5: enrich:{invoice_id} is stable across re-runs, so a second
batch resumed the first batch's checkpoints; the thread_id now carries
batch_id. All three mutation-verified."
```

---

### Task 3: The serial join

**Files:**
- Modify: `src/invoice_agent/graphs/batch.py`
- Test: `tests/test_batch_serial_join.py`

**Interfaces:**
- Consumes: `repositories.{vendors,invoices,flags,budget_consumption,reference}`, `memory.vendor_profile`, `domain.{budget,anomaly,findings}`.
- Produces: `serial_join(state, *, conn=None) -> dict`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_batch_serial_join.py`:

```python
"""One node, one transaction, canonical order (spec 2026-08-01-m4d §3.3)."""

import datetime
import uuid
from decimal import Decimal

from invoice_agent.domain import findings
from invoice_agent.graphs import batch
from invoice_agent.memory import vendor_profile


def _enriched(invoice, invoice_id):
    return {
        "invoice_id": invoice_id, "source_path": f"corpus/{invoice_id}.xml",
        "source_type": "xml", "extracted": invoice, "findings": [], "signals": [],
        "status": "",
    }


def test_invoices_are_persisted_in_canonical_order(runtime_conn, three_mkt_invoices):
    """Arrival order is shuffled; `sequence_no` must follow issue_date."""
    state = {
        "batch_id": str(uuid.uuid4()),
        "enriched": [_enriched(inv, iid) for iid, inv in reversed(three_mkt_invoices)],
        "failed": [],
    }
    out = batch.serial_join(state, conn=runtime_conn)
    seqs = [(c["invoice_id"], c["sequence_no"]) for c in out["consumption"]]
    assert [s for _, s in seqs] == sorted(s for _, s in seqs)
    # canonical order is by issue_date, so the EARLIEST-dated invoice is #1
    assert seqs[0][0].endswith("0003")


def test_the_budget_chain_matches_domain_budget(runtime_conn, three_mkt_invoices):
    state = {
        "batch_id": str(uuid.uuid4()),
        "enriched": [_enriched(inv, iid) for iid, inv in three_mkt_invoices],
        "failed": [],
    }
    out = batch.serial_join(state, conn=runtime_conn)
    chain = [c["balance_after"] for c in out["consumption"]]
    assert chain == [Decimal("35000000"), Decimal("15000000"), Decimal("-5000000")]


def test_a_repeated_natural_key_is_a_duplicate_not_a_second_row(
    runtime_conn, three_mkt_invoices
):
    """`insert_with_lines` returns None on conflict — design §2.3's UNIQUE index
    IS the exact-duplicate detector, with no second implementation to disagree
    with it."""
    iid, inv = three_mkt_invoices[0]
    state = {
        "batch_id": str(uuid.uuid4()),
        "enriched": [_enriched(inv, iid), _enriched(inv, iid + "-copy")],
        "failed": [],
    }
    out = batch.serial_join(state, conn=runtime_conn)
    codes = [f["code"] for f in out.get("raised", [])]
    assert findings.DUPLICATE_INVOICE in codes


def test_vendor_profile_is_updated_here_not_in_the_fan_out(
    runtime_conn, three_mkt_invoices
):
    """Constraint 3. If this moves into the task, `signals` starts seeing the
    invoice it is scoring and PRICE_OUTLIER silently stops firing."""
    iid, inv = three_mkt_invoices[0]
    before = vendor_profile.stats_for(runtime_conn, inv.seller_tax_code)
    assert before is None or before.invoice_count == 0
    batch.serial_join(
        {"batch_id": str(uuid.uuid4()), "enriched": [_enriched(inv, iid)], "failed": []},
        conn=runtime_conn,
    )
    after = vendor_profile.stats_for(runtime_conn, inv.seller_tax_code)
    assert after.invoice_count == 1


def test_failed_invoices_are_not_persisted(runtime_conn):
    """A file that never parsed has no InvoiceData; there is nothing to persist
    beyond the batch counter (spec §8 open question 1)."""
    out = batch.serial_join(
        {"batch_id": str(uuid.uuid4()), "enriched": [],
         "failed": [{"invoice_id": "INV-BAD", "error": "boom"}]},
        conn=runtime_conn,
    )
    assert out["persisted"] == []
    assert out["consumption"] == []
```

Add to `tests/conftest.py`:

```python
@pytest.fixture
def three_mkt_invoices():
    """The corpus's budget case: three MKT invoices whose canonical order is the
    REVERSE of their invoice-number order, so a sorted implementation and an
    unsorted one give different answers (defects.py:163)."""
    import json
    import pathlib

    from invoice_agent.extraction.xml_parser import parse_invoice_xml

    manifest = pathlib.Path("corpus/manifest.jsonl")
    if not manifest.exists():
        pytest.skip("corpus not generated — run `make generate`")
    rows = {r["invoice_id"]: r for r in map(json.loads, manifest.open(encoding="utf-8"))}
    out = []
    for inv_id in ("INV-0001", "INV-0002", "INV-0003"):
        record = rows[inv_id]
        out.append((inv_id, parse_invoice_xml(pathlib.Path(record["path"]).read_bytes())))
    return out
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_batch_serial_join.py -v`
Expected: FAIL — `serial_join` undefined.

- [ ] **Step 3: Write `serial_join`**

Append to `src/invoice_agent/graphs/batch.py`. Structure, in one transaction:

```python
def serial_join(state: BatchState, *, conn: Any = None) -> dict[str, Any]:
    """Persist, profile, de-duplicate and reconcile — ONE transaction.

    Constraint 2: the batch must never end with invoices recorded and budgets
    not drawn against them, so this is the batch's single commit point. A
    failure here loses persistence but not the expensive work: `llm_calls` rows
    were written inline during enrichment and survive independently.
    """
    if conn is not None:
        return _join(conn, state)
    with get_engine().begin() as owned:
        return _join(owned, state)


def _join(conn: Any, state: BatchState) -> dict[str, Any]:
    survivors = [e for e in state["enriched"] if e.get("extracted") is not None]
    ordered = sorted(
        survivors,
        key=lambda e: (
            e["extracted"].issue_date,
            e["extracted"].invoice_series,
            e["extracted"].invoice_number,
        ),
    )
    ...
```

Implement in this order, and keep each step's rationale in a comment:

1. per invoice in `ordered`: `vendors.ensure_vendor` → `invoices.insert_with_lines`
   (a `None` return is `DUPLICATE_INVOICE`, raised via `flags.raise_flag`) →
   `vendor_profile.apply_invoice`;
2. `anomaly.near_duplicate` against the invoices already walked in this batch
   plus any prior ones the repository returns;
3. group survivors by `department`, then for each department in **sorted** order:
   `reference.list_budgets(conn, dept, period)` with `FOR UPDATE`, then
   `domain.budget.consume(limit, warn_pct, inputs)`, then
   `budget_consumption.record_consumption(...)` per result and
   `flags.raise_flag` for each `BUDGET_BREACH` / `BUDGET_WARNING`.

Return `{"persisted": [...], "consumption": [...], "raised": [...]}` — the
`consumption` entries carry `invoice_id`, `balance_before`, `balance_after`,
`sequence_no`, which is what §4's serialization test compares.

**Departments are walked in sorted order** for the same reason invoices are: two
departments never contend for one budget row, but a deterministic lock order is
free and removes a question that would otherwise need answering.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_batch_serial_join.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Mutation-verify the ordering and the profile placement**

Remove the `sorted(...)` in `_join` → `test_invoices_are_persisted_in_canonical_order` and `test_the_budget_chain_matches_domain_budget` red. Move `apply_invoice` out of the join → `test_vendor_profile_is_updated_here_not_in_the_fan_out` red. Restore both.

- [ ] **Step 6: Commit, `make check`, push, confirm CI**

---

### Task 4: Assemble the graph

**Files:**
- Modify: `src/invoice_agent/graphs/batch.py`, `scripts/render_graph.py`
- Create: `docs/diagrams/batch_graph.mmd`
- Test: `tests/test_batch_graph.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_batch_graph.py`:

```python
"""Graph shape, the implicit join, and the committed diagram."""

import pathlib

from invoice_agent.graphs import batch


def test_graph_has_the_shape_design_section_4_specifies():
    rendered = batch.build_batch_graph().get_graph().draw_mermaid()
    for node in ("ingest", "cost_gate", "fan_out", "enrich_one",
                 "serial_join", "route_outcomes", "finalize"):
        assert node in rendered, node


def test_serial_join_runs_after_every_send_completes(monkeypatch):
    """The implicit join is the property the whole design rests on: LangGraph
    must complete every Send before the next superstep. Assumed everywhere,
    tested here."""
    seen = {"tasks": 0, "join_saw": None}

    def fake_enrich(task, *, graph):
        seen["tasks"] += 1
        return {"enriched": [{"invoice_id": task["invoice_id"], "extracted": None,
                              "findings": [], "signals": [], "status": "",
                              "source_path": "", "source_type": "xml"}],
                "failed": []}

    def fake_join(state, *, conn=None):
        seen["join_saw"] = len(state["enriched"])
        return {"persisted": [], "consumption": [], "raised": []}

    monkeypatch.setattr(batch, "enrich_one", fake_enrich)
    monkeypatch.setattr(batch, "serial_join", fake_join)
    graph = batch.build_batch_graph()
    graph.invoke({
        "batch_id": "b", "inputs": [
            {"invoice_id": f"INV-{i}", "source_path": "x", "source_type": "xml",
             "pdf_path": None, "batch_id": "b"} for i in range(5)
        ],
        "enriched": [], "failed": [], "persisted": [], "consumption": [],
        "auto": [], "needs_approval": [], "status": "",
    })
    assert seen["tasks"] == 5
    assert seen["join_saw"] == 5, "serial_join ran before every Send completed"


def test_the_committed_diagram_matches_the_graph():
    """Same drift guard the enrichment graph has. Regenerate with
    `python -m scripts.render_graph`; never hand-edit the .mmd."""
    committed = pathlib.Path("docs/diagrams/batch_graph.mmd")
    assert committed.exists()
    assert committed.read_text(encoding="utf-8").strip() == (
        batch.build_batch_graph().get_graph().draw_mermaid().strip()
    )
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — `build_batch_graph` undefined.

- [ ] **Step 3: Build the graph**

Add to `batch.py`: `ingest` (calls `batches.create_batch`, resolves inputs),
`cost_gate` (simple invoice-count threshold; M6.6 owns the real token budget —
say so in a comment), `fan_out` returning `list[Send]`, `route_outcomes`
(splits `auto` / `needs_approval` by whether any finding or signal was raised;
consumed by nothing until M5, per constraint 5), `finalize` (`complete_batch`).

Compile with `max_concurrency` configurable, defaulting to **5** (design §4.2's
4–6). Comment that this ships **unexercised against real rate limits**, because
the gate replays — and that M4-C measured 177 Gemini rate-limits in a *serial*
run, so a live fan-out will hit that harder.

- [ ] **Step 4: Extend the renderer**

`scripts/render_graph.py` currently writes one file. Make it write both, keeping
the existing enrichment output byte-identical so its drift guard stays green.

- [ ] **Step 5: Run, regenerate, verify**

```bash
.venv/bin/python -m scripts.render_graph
.venv/bin/python -m pytest tests/test_batch_graph.py tests/test_enrichment_graph.py -v
```

- [ ] **Step 6: Commit, `make check`, push, confirm CI**

---

### Task 5: `test_budget_serialization.py` — portfolio test 3 of 6

**Files:**
- Create: `tests/test_budget_serialization.py`

- [ ] **Step 1: Write the test**

```python
"""Shuffle the arrival order; the answer must not move.

Portfolio test 3 of 6 (plan §8). The claim: budget reconciliation is a function
of the invoices, not of the order they arrived in.

WHY THIS RUNS AT THE PERSIST+RECONCILE LAYER and not through the full graph.
The agents cannot affect ordering — they never touch it — so running them ten
times tests the wrong layer. And each run would re-persist the same invoices,
whose natural key is UNIQUE: runs 2..10 would hit ON CONFLICT DO NOTHING,
insert nothing, and measure nothing while passing. That is exactly the "passed
exactly once per database" defect M3-A's review found, and it is not worth
rebuilding.

ROLLBACK IS LOAD-BEARING. Each shuffle runs inside a SAVEPOINT that is rolled
back, so the ten runs are independent. A bug in that isolation makes them
non-independent while the test stays green — which is why the mutation in the
plan (remove the canonical sort) is the evidence, not the passing run.
"""

import random
import uuid

from invoice_agent.graphs import batch


def _enriched(invoice, invoice_id):
    return {
        "invoice_id": invoice_id, "source_path": f"corpus/{invoice_id}.xml",
        "source_type": "xml", "extracted": invoice, "findings": [], "signals": [],
        "status": "",
    }


def test_ten_shuffles_produce_one_chain(runtime_conn, three_mkt_invoices):
    chains = []
    breachers = []
    rng = random.Random(42)

    for _ in range(10):
        order = list(three_mkt_invoices)
        rng.shuffle(order)
        savepoint = runtime_conn.begin_nested()
        try:
            out = batch.serial_join(
                {"batch_id": str(uuid.uuid4()),
                 "enriched": [_enriched(inv, iid) for iid, inv in order],
                 "failed": []},
                conn=runtime_conn,
            )
            chains.append([
                (c["invoice_id"], str(c["balance_before"]), str(c["balance_after"]))
                for c in out["consumption"]
            ])
            breachers.append(sorted(
                f["invoice_id"] for f in out["raised"] if f["code"] == "BUDGET_BREACH"
            ))
        finally:
            savepoint.rollback()

    assert len(chains) == 10
    assert all(c == chains[0] for c in chains), "arrival order changed the chain"
    assert all(b == breachers[0] for b in breachers), "a different invoice breached"
    assert breachers[0], "no invoice breached — the case proves nothing"


def test_the_shuffles_were_actually_different(three_mkt_invoices):
    """Guards the vacuous case: if the shuffle never permutes anything, the test
    above compares ten identical runs and proves nothing."""
    rng = random.Random(42)
    seen = set()
    for _ in range(10):
        order = list(three_mkt_invoices)
        rng.shuffle(order)
        seen.add(tuple(iid for iid, _ in order))
    assert len(seen) > 1, "the shuffle never changed the order"
```

- [ ] **Step 2: Run — expect PASS**

Run: `.venv/bin/python -m pytest tests/test_budget_serialization.py -v`

- [ ] **Step 3: MUTATION — this is the evidence**

Remove the `sorted(...)` in `_join` so it walks arrival order. Run the test.
**Expected: RED on `test_ten_shuffles_produce_one_chain`.** Restore and confirm
green. Record the result; a passing run alone establishes nothing here.

- [ ] **Step 4: End-to-end gate run**

Run the compiled graph once over the 30 declared gate-batch invoices under
`LLM_MODE=replay` with all keys unset. Assert: the two `unreadable_pdf` members
land in `failed`/`manual_queue`, the other 28 complete, the flagged set matches
`corpus/gate_batch.json` exactly, and the breach is `INV-0001` on the
`35,000,000 → 15,000,000 → −5,000,000` chain.

- [ ] **Step 5: Commit, `make check`, push, confirm CI**

---

### Task 6: ADRs, roadmap, and closing M4

**Files:**
- Create: `docs/adr/0001-three-graphs-not-one.md`, `0003-no-supervisor-agent.md`, `0004-serial-budget-reconciliation.md`
- Modify: `docs/Plan_Project_F_2026-07-25.md`
- Create: `docs/notes/2026-08-01-m4d-batch-graph.md`

- [ ] **Step 1: Write the three ADRs**

Each follows `0002-alembic-langgraph-schema-isolation.md`'s shape. They are
writable now and were not before, because each is finally *evidenced*:

- **0001** — three graphs, not one. Evidence: `thread_id` per run, and the fact
  that a batch cannot block on a human because approval is a separate graph
  (design §1.1). Owed since M1.
- **0003** — no supervisor. Evidence: pipeline order is a business invariant, and
  M4-C measured what a second opinion is worth — dispute precision **12.5%**
  (CI 0.043–0.310, n=24), i.e. an LLM second-guessing a 95.4% classifier is net
  harmful. Cite it: this is the ADR's strongest argument and it is a measurement,
  not an opinion.
- **0004** — serial budget reconciliation. Evidence: `test_budget_serialization.py`,
  mutation-verified.

- [ ] **Step 2: Write the note**

Record: the gate result, the auto/flagged split as *reported*, the serialization
mutation result, and §1.1's drift finding as the third form of the guard pattern.

- [ ] **Step 3: Close M4 in the roadmap**

Update the M4 gate text to the restated criterion, mark items 4.5/4.6 done, add
the 4-D row, and set §7's M4 status to ✅ with the four-spec summary.

- [ ] **Step 4: Final verification**

```bash
make check
env -u GROQ_API_KEYS -u GEMINI_API_KEYS -u OPENROUTER_API_KEYS LLM_MODE=replay .venv/bin/python -m pytest -q
git push && gh run watch --exit-status
```

**Do not report M4 closed until CI is green on the pushed commit.**

---

## Notes for the implementer

**Push and verify per task.** M4-C failed constraint 6 not because anyone
skipped `make check` deliberately, but because seven commits went up as one
batch — so CI's first signal landed on the tip, whose title was
`docs(roadmap): …` and read as something that could not break the build. Seven
well-scoped commits gave no more protection than one.

**A mutation result expires when the code it mutated stops being the producer.**
M4-A found a guard genuinely red at the commit that introduced it and blind two
commits later. Task 3 changes what feeds the chain; after Task 4 wires the graph,
re-run Task 3's ordering mutation.

**When a corpus test goes red, the validator may be what is wrong.** Twice it
was: M1's naive arithmetic rule flagged 6 of 8 real invoices; M2-B's guards 3 of
8. The 8 real fixtures arbitrate.
