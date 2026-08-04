# M6-B — Observability, Cost Cap, Model Attribution: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Langfuse tracing so it actually emits, prove all 24 graph nodes are traced with a test that cannot pass on an unwired codebase, and make the cost cap refuse a batch before any LLM call — with every threshold measured rather than chosen.

**Architecture:** `observability.py` gains a `traced()` decorator that sets a marker attribute *and* delegates to the real `observe`, so a keyless CI can still verify coverage structurally. The batch cost gate collapses to one predicate used by both the node and the router. A per-invoice token budget lands in `route_after_validate`, which already routes to `manual_queue`, so no graph edge changes.

**Tech Stack:** Python 3.13, LangGraph 1.2.9 (pinned), Langfuse SDK v3, LiteLLM, SQLAlchemy + Postgres 17, pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-04-m6b-observability-cost-cap-design.md`

## Global Constraints

- **Authoring language is English** — docs, comments, docstrings, commit messages. Text the *system emits or ingests* stays Vietnamese (agent prompts, UI copy, flag narratives). Do not translate any existing Vietnamese prose.
- **The LLM never toggles a flag and never posts a journal entry.** No task here adds a field to any agent response model. `ClassificationResult` and `AuditVerdict` are not touched.
- **Every non-obvious constant carries date + measurement + report file name** in a comment.
- **Do not change any prompt, `sanitize.wrap()`, or agent response model.** M6-A measured its security numbers against those exact surfaces on 2026-08-02; moving one invalidates a security measurement to add an observability feature.
- **Pinned versions:** `langgraph==1.2.9`, `langgraph-checkpoint-postgres==3.1.0`, `langfuse>=3.15,<4.0.0`. Do not bump.
- **Run `make check` after every task**, not only at the end. M4-C batched seven commits, pushed once, and CI first saw the tip — whose docs-only title made the breakage look impossible.
- **Postgres must be running** (`make dev`) for the suite. Tests needing it use the `runtime_conn` / `owner_conn` / `requires_postgres` fixtures from `tests/conftest.py`.
- **A milestone is not closed until `make check` is green on the pushed commit and CI agrees.**

---

### Task 1: Make configured Langfuse keys actually enable tracing

Closes spec §1.1 / §3.1. Today `observability.py` reads `os.environ`, the keys live in `.env`, and only pydantic-settings reads `.env` — so tracing has been off in every process this project has ever run.

**Files:**
- Modify: `src/invoice_agent/observability.py:18-24`
- Test: `tests/test_observability.py` (create)

**Interfaces:**
- Consumes: `invoice_agent.config.get_settings`
- Produces: `observability._tracing_enabled() -> bool`, `observability.TRACING_ENABLED: bool`

- [ ] **Step 1: Confirm there is no import cycle before writing anything**

Run: `.venv/bin/python -c "import invoice_agent.config as c; print([m for m in dir(c) if 'observ' in m])"`
Expected: `[]` — `config.py` imports nothing from `observability.py`, so the new dependency direction is safe.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_observability.py`:

```python
"""Tracing is decided at import time, so `_tracing_enabled` is tested as a
function rather than by reloading the module.

The ORDER of the two checks is the load-bearing part. `tests/conftest.py` keeps
the whole suite off the network by setting LANGFUSE_TRACING_ENABLED=false and
popping both keys; if the settings fallback were consulted before the
kill-switch, every test run would start firing traces at this developer's real
Langfuse project — the exact leak conftest was written to prevent.
"""

import pytest

from invoice_agent import observability
from invoice_agent.config import Settings


def _settings_with_keys(**kw):
    return Settings(_env_file=None, langfuse_public_key="pk", langfuse_secret_key="sk", **kw)


def test_kill_switch_wins_over_keys_configured_in_dotenv(monkeypatch):
    """LANGFUSE_TRACING_ENABLED=false must win even when `.env` has a real pair."""
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(observability, "get_settings", _settings_with_keys)

    assert observability._tracing_enabled() is False


def test_keys_in_settings_alone_enable_tracing(monkeypatch):
    """The defect this task fixes: a pair in `.env` never reaches os.environ."""
    monkeypatch.delenv("LANGFUSE_TRACING_ENABLED", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(observability, "get_settings", _settings_with_keys)

    assert observability._tracing_enabled() is True


def test_environment_still_enables_tracing_without_settings(monkeypatch):
    monkeypatch.delenv("LANGFUSE_TRACING_ENABLED", raising=False)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr(
        observability,
        "get_settings",
        lambda: Settings(_env_file=None),
    )

    assert observability._tracing_enabled() is True


def test_no_keys_anywhere_means_no_tracing(monkeypatch):
    monkeypatch.delenv("LANGFUSE_TRACING_ENABLED", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(
        observability,
        "get_settings",
        lambda: Settings(_env_file=None),
    )

    assert observability._tracing_enabled() is False


def test_settings_failure_does_not_break_the_import(monkeypatch):
    """Telemetry that can kill a run is worse than telemetry that under-reports
    — the rule `gateway._count_tokens` already follows."""
    monkeypatch.delenv("LANGFUSE_TRACING_ENABLED", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    def _boom():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(observability, "get_settings", _boom)

    assert observability._tracing_enabled() is False
```

- [ ] **Step 3: Run the tests and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_observability.py -v`
Expected: `test_keys_in_settings_alone_enable_tracing` FAILS (`assert False is True`); the others may already pass.

- [ ] **Step 4: Implement**

In `src/invoice_agent/observability.py`, add the import and replace `_tracing_enabled`:

```python
import os

from invoice_agent.config import get_settings
```

```python
def _tracing_enabled() -> bool:
    # The kill-switch is checked FIRST and unconditionally. tests/conftest.py
    # relies on it to keep the suite off the network; consulting settings before
    # it would fire every test run's traces at the developer's real project.
    if os.getenv("LANGFUSE_TRACING_ENABLED", "true").strip().lower() == "false":
        return False
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        return True
    # `.env` is read by pydantic-settings and by NOTHING else, so a key
    # configured there never reaches os.environ. Measured 2026-08-04 with a
    # valid pair in `.env`: TRACING_ENABLED was False, i.e. every trace this
    # project ever emitted was a no-op. See
    # docs/superpowers/specs/2026-08-04-m6b-observability-cost-cap-design.md §1.1.
    #
    # This is why the file is no longer the verbatim Project A copy M0.4
    # recorded: verbatim is what made it read os.environ. Same defect class as
    # gateway.py, which M0.4 already ported to take Settings.
    try:
        settings = get_settings()
    except Exception:
        return False
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)
```

Update the module docstring's first paragraph to say keys come from the environment **or** `Settings`, and drop any claim that the file is unchanged from Project A.

- [ ] **Step 5: Run the tests and verify they pass**

Run: `.venv/bin/python -m pytest tests/test_observability.py -v`
Expected: 5 passed.

- [ ] **Step 6: Measure the fix — this number goes in the note**

Run:
```bash
.venv/bin/python -c "
from invoice_agent.observability import TRACING_ENABLED
from invoice_agent.config import get_settings
import os
print('LANGFUSE_PUBLIC_KEY in os.environ:', 'LANGFUSE_PUBLIC_KEY' in os.environ)
print('settings.langfuse_public_key set :', bool(get_settings().langfuse_public_key))
print('TRACING_ENABLED                  :', TRACING_ENABLED)
"
```
Expected: `TRACING_ENABLED: True` with the key still absent from `os.environ`. Record the before (`False`, from spec §1.1) and after verbatim.

- [ ] **Step 7: Mutation 3 — reorder the checks and prove the kill-switch test bites**

Temporarily move the settings block ABOVE the `LANGFUSE_TRACING_ENABLED` check.
Run: `.venv/bin/python -m pytest tests/test_observability.py::test_kill_switch_wins_over_keys_configured_in_dotenv -v`
Expected: FAIL. Record the output, then revert.

- [ ] **Step 8: Verify the suite is still off the network**

Run: `.venv/bin/python -m pytest tests/ -x -q 2>&1 | tail -5`
Expected: green, and no Langfuse auth errors in the output. If traces are being sent, `conftest.py`'s guard has been broken — stop and fix before continuing.

- [ ] **Step 9: `make check` and commit**

```bash
make check
git add src/invoice_agent/observability.py tests/test_observability.py
git commit -m "fix(m6b): tracing has been off since M0.4 — keys in .env never reached os.environ"
```

---

### Task 2: `traced()` marker on all 24 nodes, with a test a keyless CI can trust

Spec §3.2. CI has no keys, so `observe` is the identity decorator; a naive "every node is traced" test would pass on a completely unwired codebase.

**Files:**
- Modify: `src/invoice_agent/observability.py` (add `traced`, `TRACED_ATTR`)
- Modify: `src/invoice_agent/graphs/enrichment.py` (8 node functions)
- Modify: `src/invoice_agent/graphs/batch.py` (9 node functions)
- Modify: `src/invoice_agent/graphs/approval.py` (7 node functions)
- Test: `tests/test_node_tracing.py` (create)

**Interfaces:**
- Consumes: `observability.observe`
- Produces: `observability.traced(*, node: str, agent: str | None = None)`, `observability.TRACED_ATTR: str`, `observability.tracing_marker(fn) -> dict | None`, `observability.tag_current_span(**fields) -> None`, `observability._langfuse_client()`

- [ ] **Step 1: Write the failing test**

Create `tests/test_node_tracing.py`:

```python
"""Every node in all three graphs carries a tracing marker.

The marker exists because CI has no Langfuse keys, so `observe` degrades to the
identity decorator and a test that could only see the real decorator would pass
on a completely unwired codebase — the guard-that-guards-nothing pattern every
milestone review in this repo has found.

Two traps, both measured against langgraph==1.2.9 on 2026-08-04:

  * `vision_extract`, `classify` and `audit` are registered as
    functools.partial objects, so `bound.func` returns the PARTIAL and the
    marker on the underlying function is invisible without unwrapping `.func`.
    They are the only three nodes that call an LLM.
  * `graph.nodes` contains `__start__`, so enrichment reports 9 keys for 8
    nodes. Filtering by name is the fix; adjusting the expected count to 25 is
    the bug that hides it.
"""

import functools

import pytest

from invoice_agent.graphs.approval import build_approval_graph
from invoice_agent.graphs.batch import build_batch_graph
from invoice_agent.graphs.enrichment import build_enrichment_graph
from invoice_agent.observability import tracing_marker

EXPECTED_NODE_COUNTS = {"enrichment": 8, "batch": 9, "approval": 7}


def _node_functions(graph) -> dict[str, object]:
    """name -> the function `add_node` was ultimately given, partials unwrapped."""
    out = {}
    for name in graph.nodes:
        if name.startswith("__"):
            continue
        fn = graph.nodes[name].bound.func
        out[name] = fn.func if isinstance(fn, functools.partial) else fn
    return out


def _graphs():
    return {
        "enrichment": build_enrichment_graph(),
        "batch": build_batch_graph(),
        "approval": build_approval_graph(),
    }


def test_all_three_graphs_have_the_node_count_this_test_claims():
    """Pins the denominator. Without it, deleting a node silently shrinks the
    set this suite certifies and the coverage claim quietly weakens."""
    for name, graph in _graphs().items():
        found = _node_functions(graph)
        assert len(found) == EXPECTED_NODE_COUNTS[name], (
            f"{name}: expected {EXPECTED_NODE_COUNTS[name]} nodes, got {sorted(found)}"
        )


def test_every_node_in_every_graph_is_traced():
    untraced = []
    for graph_name, graph in _graphs().items():
        for node_name, fn in _node_functions(graph).items():
            if tracing_marker(fn) is None:
                untraced.append(f"{graph_name}.{node_name}")
    assert untraced == [], f"nodes registered without @traced: {untraced}"


def test_the_marker_names_the_node_it_is_on():
    """A copy-pasted decorator carrying the wrong node name would tag every
    span with a neighbour's identity, which is worse than no tag."""
    for graph_name, graph in _graphs().items():
        for node_name, fn in _node_functions(graph).items():
            marker = tracing_marker(fn)
            assert marker["node"] == node_name, (
                f"{graph_name}.{node_name} is marked as {marker['node']!r}"
            )


def test_the_three_llm_nodes_are_registered_as_partials():
    """Pins the trap itself. If a refactor stops using functools.partial, the
    unwrap above becomes dead code and this test says so instead of rotting."""
    graph = build_enrichment_graph()
    partials = [
        name
        for name in graph.nodes
        if not name.startswith("__")
        and isinstance(graph.nodes[name].bound.func, functools.partial)
    ]
    assert sorted(partials) == ["audit", "classify", "vision_extract"]
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `.venv/bin/python -m pytest tests/test_node_tracing.py -v`
Expected: FAIL with `ImportError: cannot import name 'tracing_marker'`.

- [ ] **Step 3: Add `traced` and `tracing_marker` to `observability.py`**

```python
TRACED_ATTR = "__invoice_agent_traced__"


def traced(*, node: str, agent: str | None = None):
    """Mark a graph node as traced, and trace it.

    The marker is set whether or not tracing is enabled. CI has no Langfuse
    keys, so `observe` is the identity decorator there, and a structural test
    that could only see the real decorator would pass on an unwired codebase.
    """

    def decorate(fn):
        wrapped = observe(name=node)(fn)
        setattr(wrapped, TRACED_ATTR, {"node": node, "agent": agent})
        return wrapped

    return decorate


def tracing_marker(fn) -> dict | None:
    """The marker `traced` left, or None. Used by tests/test_node_tracing.py."""
    return getattr(fn, TRACED_ATTR, None)
```

- [ ] **Step 3b: Add the runtime tags — `invoice_id` and `batch_id`**

Roadmap 6.5 asks for four tags. `node` and `agent` are known at decoration time; `invoice_id` and `batch_id` are only known per call, and they live in the state dict every node receives as its first argument. Extracting them centrally is what keeps 24 nodes from each needing their own tagging line.

Extend `traced` and add the tagger:

```python
import functools


def tag_current_span(**fields) -> None:
    """Attach per-call identifiers to the active span.

    No-ops when tracing is off, and never raises: this runs on the hot path of
    every node, and observability must not be able to fail an invoice run.
    """
    if not TRACING_ENABLED:
        return
    known = {k: v for k, v in fields.items() if v is not None}
    if not known:
        return
    try:
        _langfuse_client().update_current_trace(metadata=known)
    except Exception:
        return


def traced(*, node: str, agent: str | None = None):
    """Mark a graph node as traced, and trace it.

    The marker is set whether or not tracing is enabled. CI has no Langfuse
    keys, so `observe` is the identity decorator there, and a structural test
    that could only see the real decorator would pass on an unwired codebase.
    """

    def decorate(fn):
        @functools.wraps(fn)
        def tag_then_run(*args, **kwargs):
            state = args[0] if args else None
            if isinstance(state, dict):
                tag_current_span(
                    node=node,
                    agent=agent,
                    invoice_id=state.get("invoice_id"),
                    batch_id=state.get("batch_id"),
                )
            return fn(*args, **kwargs)

        wrapped = observe(name=node)(tag_then_run)
        setattr(wrapped, TRACED_ATTR, {"node": node, "agent": agent})
        return wrapped

    return decorate
```

`_langfuse_client()` is defined in Task 4 Step 3. **If Task 4 has not been done yet, add that helper now** — it is three lines and both tasks need it:

```python
def _langfuse_client():
    from langfuse import get_client

    return get_client()
```

> ⚠️ **Signature transparency is the risk in this step.** LangGraph inspects a node's signature to decide how to call it (`func_accepts`, `explode_args` — visible in the `RunnableCallable` repr). `functools.wraps` sets `__wrapped__` so `inspect.signature` still reports the original, but this must be **verified, not assumed**: the current code has no wrapper other than `observe`, and this adds one. Step 3c checks it before 24 nodes depend on it.

- [ ] **Step 3c: Verify the wrapper did not change how LangGraph sees a node**

```bash
.venv/bin/python -c "
import inspect
from invoice_agent.observability import traced

def node(state, *, gateway=None):
    return {}

wrapped = traced(node='probe')(node)
print('signature :', inspect.signature(wrapped))
print('__wrapped__ present:', hasattr(wrapped, '__wrapped__'))
"
```
Expected: `signature : (state, *, gateway=None)` — the original, not `(*args, **kwargs)`. If it reports `(*args, **kwargs)`, stop: LangGraph may mis-dispatch every node. Fix the wrapper before continuing rather than proceeding and debugging 24 broken nodes.

- [ ] **Step 3d: Add a test for the tags**

Append to `tests/test_node_tracing.py`:

```python
def test_a_traced_node_reports_the_ids_in_its_state(monkeypatch):
    """Roadmap 6.5 asks for four tags. `node` and `agent` are known when the
    decorator is applied; `invoice_id` and `batch_id` are only known per call.
    """
    from invoice_agent import observability

    seen = []
    monkeypatch.setattr(observability, "tag_current_span", lambda **kw: seen.append(kw))

    @observability.traced(node="probe", agent="ProbeAgent")
    def a_node(state):
        return {"ok": True}

    a_node({"invoice_id": "INV-1", "batch_id": "BATCH-9", "other": "ignored"})

    assert seen == [
        {"node": "probe", "agent": "ProbeAgent", "invoice_id": "INV-1", "batch_id": "BATCH-9"}
    ]


def test_tagging_never_breaks_a_node(monkeypatch):
    """Observability must not be able to fail an invoice run."""
    from invoice_agent import observability

    monkeypatch.setattr(observability, "TRACING_ENABLED", True)
    monkeypatch.setattr(
        observability,
        "_langfuse_client",
        lambda: (_ for _ in ()).throw(RuntimeError("langfuse down")),
    )

    @observability.traced(node="probe")
    def a_node(state):
        return {"ok": True}

    assert a_node({"invoice_id": "INV-1"}) == {"ok": True}
```

> The first test monkeypatches `observability.tag_current_span`, so `traced` must call it as a **module-level lookup** (`tag_current_span(...)` inside the module), which the Step 3b code does. If it were captured as a local reference at decoration time the patch would not be seen and the test would fail for the wrong reason.

- [ ] **Step 4: Decorate the 8 enrichment nodes**

In `src/invoice_agent/graphs/enrichment.py`, add `from invoice_agent.observability import traced` and decorate each node function. `agent=` is set only on the two nodes that own an agent:

```python
@traced(node="route_source")
def route_source(state: EnrichmentState) -> dict[str, Any]:
    ...

@traced(node="parse_xml")
def parse_xml_node(state: EnrichmentState) -> dict[str, Any]:
    ...

@traced(node="vision_extract")
def vision_extract_node(...):
    ...

@traced(node="validate")
def validate_node(state: EnrichmentState) -> dict[str, Any]:
    ...

@traced(node="classify", agent="ClassificationAgent")
def classify_node(state: EnrichmentState, *, gateway: LLMGateway) -> dict[str, Any]:
    ...

@traced(node="audit", agent="AuditorAgent")
def audit_node(state: EnrichmentState, *, gateway: LLMGateway) -> dict[str, Any]:
    ...

@traced(node="signals")
def signals_node(state: EnrichmentState, *, conn: Any = None) -> dict[str, Any]:
    ...

@traced(node="manual_queue")
def manual_queue_node(state: EnrichmentState) -> dict[str, Any]:
    ...
```

Do NOT decorate `route_after_validate` — it is a conditional-edge router, not a node, and is not in `graph.nodes`.

- [ ] **Step 5: Decorate the 9 batch nodes**

In `src/invoice_agent/graphs/batch.py`: `ingest_node`, `cost_gate_node`, `fan_out_node`, `enrich_one_task_node`, `serial_join`, `route_outcomes_node`, `auto_post_node`, `open_approval_node`, `finalize_node` — each with `@traced(node="<the name passed to add_node>")`. The names are `ingest`, `cost_gate`, `fan_out`, `enrich_one`, `serial_join`, `route_outcomes`, `auto_post`, `open_approval`, `finalize`.

Do NOT decorate `fan_out_router` or `cost_gate_condition` — routers, not nodes.

- [ ] **Step 6: Decorate the 7 approval nodes**

In `src/invoice_agent/graphs/approval.py`: `load_snapshot_node`, `suggest_node`, `approval_gate_node`, `apply_decision_node`, `post_entry_node`, `record_rejection_node`, `learn_node` — names `load_snapshot`, `suggest`, `approval_gate`, `apply_decision`, `post_entry`, `record_rejection`, `learn`.

- [ ] **Step 7: Run the test and verify it passes**

Run: `.venv/bin/python -m pytest tests/test_node_tracing.py -v`
Expected: 4 passed.

- [ ] **Step 8: Mutation 1 — remove one decorator**

Delete `@traced(node="serial_join")` from `batch.py`.
Run: `.venv/bin/python -m pytest tests/test_node_tracing.py::test_every_node_in_every_graph_is_traced -v`
Expected: FAIL naming `batch.serial_join`. Record output, revert.

- [ ] **Step 9: Mutation 2 — verify the test's OWN coverage of the partial trap**

This verifies the test, not the production code. Remove the `.func` unwrap from `_node_functions` (`return fn` instead of the isinstance check) **and** remove `@traced(node="classify", ...)` from `enrichment.py`.
Run: `.venv/bin/python -m pytest tests/test_node_tracing.py::test_every_node_in_every_graph_is_traced -v`
Expected: the test reports `classify`, `audit` and `vision_extract` as untraced *even though two of them are decorated* — proving that without the unwrap it cannot see any of the three. Record the output, revert both changes.

- [ ] **Step 10: Verify nothing regressed in the graphs**

Run: `.venv/bin/python -m pytest tests/test_enrichment_graph.py tests/test_batch_graph.py tests/test_approval_graph.py tests/test_prompt_injection.py -q`
Expected: all green. A decorator that changed a node's signature or return value would show here.

- [ ] **Step 11: `make check` and commit**

```bash
make check
git add src/invoice_agent/observability.py src/invoice_agent/graphs/ tests/test_node_tracing.py
git commit -m "feat(m6b): trace all 24 nodes, with a marker a keyless CI can verify"
```

---

### Task 3: Prove tracing does not break HITL

Spec §3.3. `approval_gate` works by **raising** `GraphInterrupt`, and M1.5 measured that its body runs twice across a resume. A decorator that catches or re-types that exception would break the one property this project pins its LangGraph version for.

The CI-runnable half imports the **real** `observe` directly from `langfuse`, bypassing the key gating — otherwise the test would exercise the identity decorator and prove nothing.

**Files:**
- Test: `tests/test_node_tracing.py` (append)

**Interfaces:**
- Consumes: `observability.traced`, `langgraph.errors.GraphInterrupt`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_node_tracing.py`:

```python
def test_the_real_observe_decorator_does_not_swallow_graph_interrupt():
    """`interrupt()` raises GraphInterrupt, and HITL durability depends on it
    propagating out of the node untouched.

    The real decorator is imported straight from langfuse rather than through
    observability, because the suite runs with the kill-switch on — through
    observability this would decorate with the identity function and assert
    nothing at all.
    """
    from langfuse import observe as real_observe
    from langgraph.errors import GraphInterrupt

    @real_observe(name="approval_gate")
    def raises_interrupt():
        raise GraphInterrupt(("pause here",))

    with pytest.raises(GraphInterrupt):
        raises_interrupt()
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_node_tracing.py::test_the_real_observe_decorator_does_not_swallow_graph_interrupt -v`
Expected: PASS. If it FAILS, tracing is incompatible with HITL as wired — stop, and report it rather than working around it. That result would be a finding, not a blocker to route past.

- [ ] **Step 3: Measure the integration case with real keys — this goes in the note**

The unit test above covers the decorator. This covers the whole restart path with tracing genuinely enabled.

> ⚠️ **Corrected 2026-08-04 during implementation. The obvious command does not work**, and it fails *silently* — it reports a number that looks like a tracing measurement and is not one. `tests/conftest.py:14-16` sets `LANGFUSE_TRACING_ENABLED=false` and pops both keys **unconditionally, at import time**, before `observability.py` is ever imported. Shell environment variables do not survive that. So this:
>
> ```bash
> LANGFUSE_TRACING_ENABLED=true LANGFUSE_PUBLIC_KEY=... HITL_RUNS=10 .venv/bin/python -m pytest ...
> ```
>
> measures the **identity decorator**, exactly as a keyless run does. Getting a real reading requires temporarily neutralising conftest's override — a git-tracked edit that must be reverted and diff-verified before committing, never committed.

Record the result verbatim, and record the timing beside the tracing-off baseline.

> ⚠️ **Do not report the timing delta as "tracing overhead" without decomposing it.** This test spawns a **fresh subprocess per run**, each of which cold-imports litellm/langfuse and authenticates a new Langfuse client. That is a fixed per-spawn cost which amortises away in a long-lived process, and it dominates the measured delta. The M5-B baseline (20.4s for a 30-invoice live batch) was measured in **one continuous process**, so the two are not comparable. What this step establishes is the gate criterion — **HITL durability holds with tracing genuinely on** — not a steady-state overhead figure.

- [ ] **Step 4: `make check` and commit**

```bash
make check
git add tests/test_node_tracing.py
git commit -m "test(m6b): tracing must not swallow the interrupt HITL depends on"
```

---

### Task 4: Write `trace_id` into `app.llm_calls`

Spec §3.4. The column's own comment says it is "written by nothing until M6.5 wires Langfuse". This is M6.5.

**Files:**
- Modify: `src/invoice_agent/observability.py` (add `current_trace_id`)
- Modify: `src/invoice_agent/graphs/enrichment.py:133-149` (`_persist_cost`)
- Test: `tests/test_llm_telemetry.py` (append)

**Interfaces:**
- Consumes: `observability.TRACING_ENABLED`
- Produces: `observability.current_trace_id() -> str | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm_telemetry.py`:

```python
def test_current_trace_id_is_none_when_tracing_is_off(monkeypatch):
    from invoice_agent import observability

    monkeypatch.setattr(observability, "TRACING_ENABLED", False)
    assert observability.current_trace_id() is None


def test_current_trace_id_never_raises(monkeypatch):
    """Telemetry that can kill an invoice run is worse than telemetry that
    under-reports — the rule gateway._count_tokens already follows."""
    from invoice_agent import observability

    monkeypatch.setattr(observability, "TRACING_ENABLED", True)
    monkeypatch.setattr(
        observability, "_langfuse_client", lambda: (_ for _ in ()).throw(RuntimeError("no client"))
    )
    assert observability.current_trace_id() is None


def test_persist_cost_writes_the_active_trace_id(runtime_conn, monkeypatch):
    """The `trace_id` column gets its first writer. Monkeypatching the source
    keeps this runnable on a keyless CI while still proving the value is
    threaded from observability through telemetry into the row."""
    import uuid

    from sqlalchemy import text

    from invoice_agent.graphs import enrichment
    from invoice_agent.llm.cost import LLMCallRecord

    invoice_id = str(uuid.uuid4())
    monkeypatch.setattr(enrichment, "current_trace_id", lambda: "trace-abc-123")

    enrichment._persist_cost(
        [
            LLMCallRecord(
                model="groq/llama-3.3-70b-versatile",
                provider="groq",
                prompt_tokens=10,
                completion_tokens=5,
                latency_ms=1,
                attempt=1,
                succeeded=True,
                error=None,
            )
        ],
        invoice_id=invoice_id,
        node="classify",
    )

    row = runtime_conn.execute(
        text("SELECT trace_id FROM app.llm_calls WHERE thread_id = :t"),
        {"t": f"enrich:{invoice_id}"},
    ).scalar_one()
    assert row == "trace-abc-123"
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_llm_telemetry.py -k "trace" -v`
Expected: FAIL — `current_trace_id` does not exist.

- [ ] **Step 3: Implement `current_trace_id`**

In `observability.py` (`_langfuse_client` already exists if Task 2 Step 3b is done — do not define it twice):

```python
def _langfuse_client():
    from langfuse import get_client

    return get_client()


def current_trace_id() -> str | None:
    """The active Langfuse trace, for `app.llm_calls.trace_id`.

    Returns None rather than raising: a cost row is written on the hot path of
    every LLM call, and telemetry must never be able to kill an invoice run.
    """
    if not TRACING_ENABLED:
        return None
    try:
        return _langfuse_client().get_current_trace_id()
    except Exception:
        return None
```

- [ ] **Step 4: Thread it through `_persist_cost`**

In `enrichment.py`, import `current_trace_id` and pass it:

```python
        telemetry.record(
            conn, calls, node=node, thread_id=f"enrich:{invoice_id}",
            trace_id=current_trace_id(),
        )
```

- [ ] **Step 5: Run and verify pass**

Run: `.venv/bin/python -m pytest tests/test_llm_telemetry.py -k "trace" -v`
Expected: 3 passed.

- [ ] **Step 5b: Attach cost and shadow cost as span metadata**

Spec §3.2 and design §2.9: the two money columns are the point, and a trace that shows tokens but not the shadow price cannot answer "what does this agent cost?" — the question §2.2 exists to answer.

In `_persist_cost`, after the `telemetry.record` call:

```python
    tag_current_span(
        cost_usd=str(sum(actual_cost_usd(c) for c in calls)),
        shadow_cost_usd=str(sum(shadow_cost_usd(c) for c in calls)),
    )
```

`str(...)` because these are `Decimal` — money is never a float in this project (M1 measured amounts to 8 decimal places), and a JSON metadata payload would otherwise coerce it. Import `actual_cost_usd` and `shadow_cost_usd` from `invoice_agent.llm.cost` and `tag_current_span` from `invoice_agent.observability`.

Append the test to `tests/test_llm_telemetry.py`:

```python
def test_persist_cost_reports_both_money_figures_to_the_trace(runtime_conn, monkeypatch):
    """$0 actual on free tier reads as "you did not measure" without the shadow
    figure beside it — design §2.9's whole reason for two columns."""
    import uuid

    from invoice_agent import observability
    from invoice_agent.graphs import enrichment
    from invoice_agent.llm.cost import LLMCallRecord

    seen = []
    monkeypatch.setattr(enrichment, "tag_current_span", lambda **kw: seen.append(kw))
    monkeypatch.setattr(enrichment, "current_trace_id", lambda: None)

    enrichment._persist_cost(
        [
            LLMCallRecord(
                model="groq/llama-3.3-70b-versatile", provider="groq",
                prompt_tokens=1000, completion_tokens=200, latency_ms=1,
                attempt=1, succeeded=True, error=None,
            )
        ],
        invoice_id=str(uuid.uuid4()),
        node="classify",
    )

    assert len(seen) == 1
    assert seen[0]["cost_usd"] == "0.000000"       # free tier
    assert Decimal(seen[0]["shadow_cost_usd"]) > 0  # priced at gpt-4o-mini
```

Add `from decimal import Decimal` to the test file's imports if it is not already there.

> The exact `cost_usd` string depends on `actual_cost_usd`'s quantisation. Run the test, read the real value, and set the literal to match — **do not** relax the assertion to `!= None`. The point is that actual is zero *while* shadow is not.

- [ ] **Step 6: Update the stale column comment**

In `src/invoice_agent/db/models.py:438`, replace "written by nothing until M6.5 wires Langfuse" with a note that M6-B wires it via `enrichment._persist_cost`, and that it is NULL whenever tracing is disabled — which is CI always.

- [ ] **Step 7: Measure a real trace id — this goes in the note**

With Postgres up and keys exported as in Task 3 Step 3, run one invoice through `scripts/run_one.py` and query:

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c \
  "SELECT node, model, trace_id IS NOT NULL AS has_trace FROM app.llm_calls ORDER BY id DESC LIMIT 5"
```
Expected: `has_trace = t` on the newest rows. Paste the table into the note. **Do not paste the trace ids themselves** — M6-A's note failed the secrets scan for a 16-hex prompt digest that matched gitleaks' `generic-api-key` rule.

- [ ] **Step 8: `make check` and commit**

```bash
make check
git add src/invoice_agent/observability.py src/invoice_agent/graphs/enrichment.py src/invoice_agent/db/models.py tests/test_llm_telemetry.py
git commit -m "feat(m6b): llm_calls.trace_id gets its first writer"
```

---

### Task 5: Batch cost cap — honest rejection, configured threshold, corrected boundary

Spec §1.2, §1.3, §3.5. Three defects in ten lines of `batch.py`.

**Files:**
- Modify: `src/invoice_agent/config.py` (add `max_batch_files`)
- Modify: `src/invoice_agent/graphs/batch.py:441-454`
- Test: `tests/test_batch_cost_gate.py` (create)

**Interfaces:**
- Consumes: `Settings.max_batch_files`
- Produces: `batch.batch_is_oversized(state) -> bool`; `cost_gate_node` returns `{"status": "cost_gate_rejected"}` or `{"status": "cost_gate_passed"}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_batch_cost_gate.py`:

```python
"""The cost cap is the one guardrail design §9.2 states in terms of what must
NOT happen: "Upload 10.000 file -> reject BEFORE any LLM call".

So the test counts rows in app.llm_calls rather than asserting a status string.
A status can be correct while a call has already happened, which is exactly the
defect this task fixes in the other direction.
"""

import uuid

import pytest
from sqlalchemy import text

from invoice_agent.graphs.batch import (
    batch_is_oversized,
    cost_gate_condition,
    cost_gate_node,
)


def _state(n: int) -> dict:
    return {"batch_id": str(uuid.uuid4()), "inputs": [{"path": f"{i}.xml"} for i in range(n)]}


def test_exactly_ten_thousand_files_is_rejected():
    """Design §9.2 names 10,000 as the rejection case. The original `> 10000`
    let exactly 10,000 through."""
    assert batch_is_oversized(_state(10_000)) is True
    assert cost_gate_condition(_state(10_000)) == "rejected"


def test_one_below_the_threshold_passes():
    assert batch_is_oversized(_state(9_999)) is False
    assert cost_gate_condition(_state(9_999)) == "ok"


def test_a_rejected_batch_does_not_report_that_it_passed():
    """The node returned `cost_gate_passed` unconditionally, so a refused batch
    finished reporting success — and status is all an API caller can see."""
    assert cost_gate_node(_state(10_000))["status"] == "cost_gate_rejected"
    assert cost_gate_node(_state(1))["status"] == "cost_gate_passed"


def test_a_rejected_batch_makes_no_llm_call(runtime_conn):
    """The measurable form of "before any LLM call"."""
    from invoice_agent.graphs.batch import build_batch_graph

    before = runtime_conn.execute(text("SELECT count(*) FROM app.llm_calls")).scalar_one()

    graph = build_batch_graph()
    result = graph.invoke(_state(10_000))

    after = runtime_conn.execute(text("SELECT count(*) FROM app.llm_calls")).scalar_one()
    assert after == before, f"{after - before} LLM calls made by a rejected batch"
    assert result["status"] == "cost_gate_rejected"


def test_the_threshold_comes_from_config_not_a_literal(monkeypatch):
    """A configured cap is testable at a small size; a magic number forces every
    test of it to build 10,000 dicts."""
    from invoice_agent.config import get_settings
    from invoice_agent.graphs import batch as batch_mod

    settings = get_settings()
    monkeypatch.setattr(
        batch_mod, "get_settings", lambda: settings.model_copy(update={"max_batch_files": 3})
    )
    assert batch_is_oversized(_state(3)) is True
    assert batch_is_oversized(_state(2)) is False
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_batch_cost_gate.py -v`
Expected: FAIL — `cannot import name 'batch_is_oversized'`.

- [ ] **Step 3: Add the setting**

In `src/invoice_agent/config.py`, in the `--- Batch ---` block:

```python
    # Design §9.2: "Upload 10.000 file -> reject BEFORE any LLM call". The
    # number is the design's, not a measurement — it is a denial-of-wallet
    # ceiling, not a tuned parameter, and no run in this project has approached
    # it (largest measured batch: 30 invoices, M5-B 2026-08-01). Comparison is
    # `>=`, so exactly 10,000 rejects: the original `> 10000` let the stated
    # rejection case through.
    max_batch_files: int = 10_000
```

- [ ] **Step 4: Implement the predicate and use it in both places**

In `src/invoice_agent/graphs/batch.py`, import `get_settings` and replace lines 441-454:

```python
def batch_is_oversized(state: BatchState) -> bool:
    """One predicate, called by both the node and the router.

    Two copies of this rule is how the node came to report success while the
    router rejected the batch.
    """
    return len(state.get("inputs") or []) >= get_settings().max_batch_files


@traced(node="cost_gate")
def cost_gate_node(state: BatchState) -> dict[str, Any]:
    """Refuse an oversized batch before any LLM call (design §9.2).

    Reports the decision it actually made. The previous version returned
    `cost_gate_passed` unconditionally while `cost_gate_condition` separately
    routed to END, so a rejected batch finished reporting that it passed.
    """
    if batch_is_oversized(state):
        return {"status": "cost_gate_rejected"}
    return {"status": "cost_gate_passed"}


def cost_gate_condition(state: BatchState) -> str:
    return "rejected" if batch_is_oversized(state) else "ok"
```

- [ ] **Step 5: Run and verify pass**

Run: `.venv/bin/python -m pytest tests/test_batch_cost_gate.py -v`
Expected: 5 passed.

- [ ] **Step 6: Mutation 4 — revert the boundary**

Change `>=` to `>`.
Run: `.venv/bin/python -m pytest tests/test_batch_cost_gate.py::test_exactly_ten_thousand_files_is_rejected -v`
Expected: FAIL. Record, revert.

- [ ] **Step 7: Mutation 5 — restore the unconditional status**

Make `cost_gate_node` return `{"status": "cost_gate_passed"}` unconditionally.
Run: `.venv/bin/python -m pytest tests/test_batch_cost_gate.py::test_a_rejected_batch_does_not_report_that_it_passed -v`
Expected: FAIL. Record, revert.

- [ ] **Step 8: Mutation 6 — let the gate run after fan-out**

In `build_batch_graph`, temporarily route `ingest -> fan_out` and put `cost_gate` after the join.
Run: `.venv/bin/python -m pytest tests/test_batch_cost_gate.py::test_a_rejected_batch_makes_no_llm_call -v`
Expected: FAIL, reporting LLM calls made by a rejected batch. Record, revert.

> If this mutation cannot be made to produce LLM calls under `LLM_MODE=replay` (a replay miss may raise before any row is written), say so in the note and record what it did instead. **Do not report the mutation as verified if it went red for the wrong reason** — that is exactly the M6-A Mutation B defect, where a red came from a changed cache key rather than from the property under test.

- [ ] **Step 9: `make check` and commit**

```bash
make check
git add src/invoice_agent/config.py src/invoice_agent/graphs/batch.py tests/test_batch_cost_gate.py
git commit -m "fix(m6b): a rejected batch reported that it passed, and 10,000 files got through"
```

---

### Task 6: A prompt-token estimator, and the measurement that sets its threshold

Spec §3.5. The constant must be **derived from the corpus**, not chosen — the parent spec left this open saying no measurement existed. Task 6 produces the measurement; Task 7 spends it.

**Files:**
- Create: `src/invoice_agent/llm/budget.py`
- Test: `tests/test_llm_budget.py` (create)

**Interfaces:**
- Produces: `budget.classify_messages(invoice: InvoiceData) -> list[dict[str, str]]`, `budget.estimate_prompt_tokens(invoice: InvoiceData, *, model: str) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_llm_budget.py`:

```python
"""The estimate must count the messages that would ACTUALLY be sent.

`describe()` alone is roughly half a short prompt: `call_structured` appends
`_schema_instruction`'s full JSON schema, and the system prompt is another
constant block. An estimate missing those under-counts every invoice by about
the same amount, which makes any threshold derived from it meaningless.
"""

import functools

from invoice_agent.llm.budget import classify_messages, estimate_prompt_tokens


def test_the_estimate_matches_the_messages_the_gateway_really_sends(sample_invoice, monkeypatch):
    """Pins the estimator to the production prompt by capturing the real one.

    Patches litellm.completion — the same seam tests/helpers/hostile_gateway.py
    uses — so the whole prompt-assembly path runs for real.
    """
    import json

    from tests.helpers.fake_llm import ok_response

    from invoice_agent.agents import classification
    from invoice_agent.config import Settings
    from invoice_agent.llm import gateway as gw_module
    from invoice_agent.llm.gateway import LLMGateway

    captured = {}

    def _completion(**kw):
        captured["messages"] = kw["messages"]
        return ok_response(
            json.dumps(
                {"category": "OFFICE_SUPPLIES", "department": "ADMIN",
                 "confidence": 0.9, "reasoning": "ok"},
                ensure_ascii=False,
            ),
            prompt=900,
            completion=50,
        )

    monkeypatch.setattr(gw_module.litellm, "completion", _completion)
    gw = LLMGateway(Settings(_env_file=None, groq_api_keys=["k"], llm_mode="live"))
    gw.mode = "live"
    classification.classify(sample_invoice, gateway=gw)

    assert classify_messages(sample_invoice) == captured["messages"]


def test_a_bigger_invoice_estimates_more_tokens(sample_invoice):
    small = estimate_prompt_tokens(sample_invoice, model="groq/llama-3.3-70b-versatile")

    fat = sample_invoice.model_copy(deep=True)
    fat.lines = list(fat.lines) * 40
    big = estimate_prompt_tokens(fat, model="groq/llama-3.3-70b-versatile")

    assert big > small


def test_an_unknown_model_does_not_raise(sample_invoice):
    """Same rule as gateway._count_tokens: returns 0 rather than killing a run."""
    assert estimate_prompt_tokens(sample_invoice, model="nonexistent/model-xyz") >= 0
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_llm_budget.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `src/invoice_agent/llm/budget.py`**

```python
"""Per-invoice prompt size, estimated before the call is made (roadmap 6.6).

Its own module because it must rebuild EXACTLY what `classify()` sends without
importing the graph: a copy of the prompt that drifts would silently estimate a
prompt nobody sends. `tests/test_llm_budget.py` pins it against the real one by
capturing what reaches litellm.
"""

from typing import Any

import litellm

from invoice_agent.agents.classification import SYSTEM_PROMPT, describe
from invoice_agent.agents.models import ClassificationResult
from invoice_agent.extraction.models import InvoiceData
from invoice_agent.extraction.sanitize import wrap
from invoice_agent.llm.structured import _schema_instruction


def classify_messages(invoice: InvoiceData) -> list[dict[str, Any]]:
    """The messages `classify()` would send for this invoice.

    Mirrors classification.classify + call_structured + gateway._generate:
    system prompt, wrapped invoice text, then the JSON schema instruction.
    `vendor` context is omitted — it is at most one short line and is absent
    for every invoice on its vendor's first sighting.
    """
    prompt = f"{wrap(describe(invoice))}\n\n{_schema_instruction(ClassificationResult)}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


def estimate_prompt_tokens(invoice: InvoiceData, *, model: str) -> int:
    """Input tokens the classify call would spend.

    Per model, because the tokenizers differ — M3-A measured 26 vs 34 tokens for
    the same Vietnamese sentence. Returns 0 rather than raising when litellm has
    no tokenizer, the same rule gateway._count_tokens follows.
    """
    try:
        return int(litellm.token_counter(model=model, messages=classify_messages(invoice)))
    except Exception:
        return 0
```

- [ ] **Step 4: Run and verify pass**

Run: `.venv/bin/python -m pytest tests/test_llm_budget.py -v`
Expected: 3 passed.

> If `test_the_estimate_matches_the_messages_the_gateway_really_sends` fails on an exact-equality comparison, do **not** weaken it to a length or substring check. Find what differs and mirror it. The exactness is the whole value of the test.

- [ ] **Step 5: Mutation 7 — drop the schema hint**

Change `classify_messages` to omit `_schema_instruction(...)`.
Run: `.venv/bin/python -m pytest tests/test_llm_budget.py::test_the_estimate_matches_the_messages_the_gateway_really_sends -v`
Expected: FAIL. Record, revert.

- [ ] **Step 6: Measure the distribution across the corpus**

```bash
.venv/bin/python -c "
import pathlib, statistics
from scripts.generator import manifest
from invoice_agent.extraction.xml_parser import parse_invoice_xml
from invoice_agent.llm.budget import estimate_prompt_tokens

MODEL = 'groq/llama-3.3-70b-versatile'
corpus = pathlib.Path('corpus')
rows = [r for r in manifest.read_manifest(corpus / 'manifest.jsonl') if r['source_type'] == 'xml']
counts = []
for r in rows:
    inv = parse_invoice_xml((corpus.parent / r['path']).read_bytes())
    counts.append(estimate_prompt_tokens(inv, model=MODEL))
counts.sort()
n = len(counts)
print('n       :', n)
print('min     :', counts[0])
print('median  :', statistics.median(counts))
print('mean    :', round(statistics.mean(counts), 1))
print('p95     :', counts[int(n * 0.95)])
print('p99     :', counts[int(n * 0.99)])
print('max     :', counts[-1])
"
```

Record the full output. It goes in the note and is cited by the constant's comment.

- [ ] **Step 7: Commit**

```bash
make check
git add src/invoice_agent/llm/budget.py tests/test_llm_budget.py
git commit -m "feat(m6b): estimate a classify prompt before spending it"
```

---

### Task 7: Route an oversized invoice to `manual_queue`

Spec §3.5. Placement is in `route_after_validate`, which already returns `"manual_queue"` — so no new node and no new edge.

**Files:**
- Modify: `src/invoice_agent/config.py` (add `max_invoice_prompt_tokens`)
- Modify: `src/invoice_agent/graphs/enrichment.py` (`route_after_validate`)
- Test: `tests/test_enrichment_routing.py` (append)

**Interfaces:**
- Consumes: `budget.estimate_prompt_tokens`, `Settings.max_invoice_prompt_tokens`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_enrichment_routing.py`:

```python
def test_an_invoice_too_large_to_classify_goes_to_manual_queue(sample_invoice, monkeypatch):
    """Roadmap 6.6: over the per-invoice token budget -> manual queue.

    The threshold is monkeypatched to a tiny value rather than building a
    genuinely enormous invoice: the rule under test is the comparison and the
    route, and a 300-line fixture would test litellm's tokenizer instead.
    """
    from invoice_agent.config import get_settings
    from invoice_agent.graphs import enrichment

    settings = get_settings()
    monkeypatch.setattr(
        enrichment,
        "get_settings",
        lambda: settings.model_copy(update={"max_invoice_prompt_tokens": 1}),
    )

    state = {
        "invoice_id": "INV-BIG",
        "strategy": "xml",
        "extracted": sample_invoice,
        "findings": [],
    }
    assert enrichment.route_after_validate(state) == "manual_queue"


def test_an_ordinary_invoice_still_reaches_classify(sample_invoice, monkeypatch):
    """The negative control. Without it, a threshold of 0 would pass the test
    above while sending every invoice on the corpus to the manual queue."""
    from invoice_agent.config import get_settings
    from invoice_agent.graphs import enrichment
    from langgraph.graph import END

    settings = get_settings()
    monkeypatch.setattr(
        enrichment,
        "get_settings",
        lambda: settings.model_copy(update={"max_invoice_prompt_tokens": 100_000}),
    )

    state = {
        "invoice_id": "INV-OK",
        "strategy": "xml",
        "extracted": sample_invoice,
        "findings": [],
    }
    assert enrichment.route_after_validate(state) == END
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_enrichment_routing.py -k "manual_queue or reaches_classify" -v`
Expected: the oversized test FAILS (returns `END`).

- [ ] **Step 3: Add the setting, with the measured comment**

**The value is computed, not chosen.** Apply this rule to Task 6 Step 6's output so two implementers would land on the same number:

> `value` = the measured **max**, multiplied by 3, rounded **up** to the next multiple of 1,000.
> Example: a measured max of 2,180 gives 6,540 → **7,000**.

The 3× headroom is not a measurement and must not be presented as one. Its job is to be far enough above every real invoice that the gate only ever fires on something pathological — a parser fault that concatenates a document, or a description field used as a payload carrier — while still being a bound rather than no bound at all. State that reasoning in the comment rather than implying the multiplier was derived.

Fill every angle-bracketed slot below with the real measured numbers before committing:

```python
    # Per-invoice classify-prompt ceiling (roadmap 6.6). Measured 2026-08-04
    # over all 285 corpus XML invoices: median <median>, p95 <p95>, p99 <p99>,
    # max <max> tokens. Set to 3x the measured max, rounded up to the next
    # thousand. The 3x is headroom, NOT a measurement: the gate exists to catch
    # a pathological invoice (a parser fault, or a description used as a payload
    # carrier), so it must sit well clear of everything real while still being a
    # bound. Full sweep in docs/notes/2026-08-04-m6b-observability.md.
    max_invoice_prompt_tokens: int = <value>
```

Task 7 Step 6 then verifies the choice against the whole corpus, so a mis-derived value fails loudly rather than silently routing real invoices to the manual queue.

- [ ] **Step 4: Implement the route**

In `enrichment.py`, import `get_settings` and `estimate_prompt_tokens`, then add the check in `route_after_validate` immediately before the `if not state.get("findings"): return END` line:

```python
    # Roadmap 6.6: refuse to spend an unbounded prompt on one invoice. Checked
    # HERE rather than in classify_node because `classify -> audit` is an
    # unconditional edge, so a node that gave up could not reach manual_queue
    # without changing the graph shape M3-C measured.
    data = state.get("extracted")
    if data is not None:
        settings = get_settings()
        estimated = estimate_prompt_tokens(data, model=settings.llm_model_chain[0])
        if estimated > settings.max_invoice_prompt_tokens:
            return "manual_queue"
```

- [ ] **Step 5: Run and verify pass**

Run: `.venv/bin/python -m pytest tests/test_enrichment_routing.py -v`
Expected: all green, including the pre-existing routing tests.

- [ ] **Step 6: Verify no corpus invoice trips the new gate**

```bash
.venv/bin/python -c "
import pathlib
from scripts.generator import manifest
from invoice_agent.config import get_settings
from invoice_agent.extraction.xml_parser import parse_invoice_xml
from invoice_agent.llm.budget import estimate_prompt_tokens

s = get_settings()
corpus = pathlib.Path('corpus')
rows = [r for r in manifest.read_manifest(corpus / 'manifest.jsonl') if r['source_type'] == 'xml']
over = [r['invoice_id'] for r in rows
        if estimate_prompt_tokens(parse_invoice_xml((corpus.parent / r['path']).read_bytes()),
                                  model=s.llm_model_chain[0]) > s.max_invoice_prompt_tokens]
print('over budget:', len(over), over[:10])
"
```
Expected: `over budget: 0 []`. A non-zero count means the threshold is too tight and would send real invoices to the manual queue — fix the constant, and record both the number and the correction.

- [ ] **Step 7: `make check` and commit**

```bash
make check
git add src/invoice_agent/config.py src/invoice_agent/graphs/enrichment.py tests/test_enrichment_routing.py
git commit -m "feat(m6b): an oversized invoice reaches a human instead of a token bill"
```

---

### Task 8: Record which model produced each classification

Spec §3.6. This is what makes M6-C's single-rater claim verifiable instead of asserted.

**Files:**
- Modify: `scripts/eval_classification.py:103-147, 180-205`
- Test: `tests/test_eval_classification_attribution.py` (create)

**Interfaces:**
- Consumes: `LLMCallRecord.model`, `telemetry.record`
- Produces: eval rows carry `classifier_model` / `auditor_model`; `results["models"]` maps agent -> sorted list of models seen

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_classification_attribution.py`:

```python
"""M4-C measured 95.4% classification accuracy without recording which model
produced each answer, and M5-B then measured 86% of live classifications being
served by the FALLBACK. The number is therefore not currently attributable.

This test pins the fix at the unit that produces it, so M6-C's single-rater
verification has something to verify.
"""

from invoice_agent.llm.cost import LLMCallRecord


def _record(model: str) -> LLMCallRecord:
    return LLMCallRecord(
        model=model, provider=model.split("/")[0], prompt_tokens=10,
        completion_tokens=5, latency_ms=1, attempt=1, succeeded=True, error=None,
    )


def test_models_used_reports_the_successful_model_per_agent():
    from scripts.eval_classification import models_used

    calls = [
        _record("groq/llama-3.3-70b-versatile"),
        _record("gemini/gemini-3.5-flash"),
    ]
    assert models_used(calls[:1]) == ["groq/llama-3.3-70b-versatile"]
    assert models_used(calls) == ["gemini/gemini-3.5-flash", "groq/llama-3.3-70b-versatile"]


def test_a_failed_attempt_is_not_credited_with_the_answer():
    """A 429 leaves a record too (design §2.9). Counting it as the rater is how
    a two-model mixture gets reported as one.

    `LLMCallRecord` is frozen — a record of what happened must not be editable
    after the fact — so the failure is constructed, not mutated.
    """
    from scripts.eval_classification import models_used

    failed = LLMCallRecord(
        model="groq/llama-3.3-70b-versatile", provider="groq", prompt_tokens=10,
        completion_tokens=0, latency_ms=1, attempt=1, succeeded=False,
        error="RateLimitError: 429",
    )
    succeeded = _record("groq/llama-3.1-8b-instant")

    assert models_used([failed, succeeded]) == ["groq/llama-3.1-8b-instant"]


def test_empty_calls_reports_nothing_rather_than_guessing():
    from scripts.eval_classification import models_used

    assert models_used([]) == []
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_eval_classification_attribution.py -v`
Expected: FAIL — `cannot import name 'models_used'`.

- [ ] **Step 3: Implement in `scripts/eval_classification.py`**

Add the helper:

```python
def models_used(calls: list[LLMCallRecord]) -> list[str]:
    """Which model(s) actually produced an answer, from one agent's call list.

    SUCCESSFUL attempts only: a 429 leaves an LLMCallRecord too (design §2.9),
    and crediting it as the rater is precisely how a two-model mixture comes to
    be reported as a single rater.
    """
    return sorted({c.model for c in calls if c.succeeded})
```

Split the per-invoice call lists so each agent is attributable, replacing the shared `calls` list in `evaluate()`:

```python
        classify_calls: list[LLMCallRecord] = []
        audit_calls: list[LLMCallRecord] = []
        class_res = classification.classify(invoice, gateway=gateway, calls=classify_calls)
        audit_res = audit_mod.audit(invoice, class_res, gateway=gateway, calls=audit_calls)
        dispute_finding = classification.dispute(class_res, audit_res)

        total_calls.extend(classify_calls)
        total_calls.extend(audit_calls)
```

Add to the `eval_rows.append({...})` dict:

```python
                "classifier_model": models_used(classify_calls),
                "auditor_model": models_used(audit_calls),
```

Add to `results`:

```python
        "models": {
            "classifier": sorted({m for r in eval_rows for m in r["classifier_model"]}),
            "auditor": sorted({m for r in eval_rows for m in r["auditor_model"]}),
        },
```

- [ ] **Step 4: Persist to `llm_calls` so `GROUP BY model` has data**

In `evaluate()`, after the two agent calls, write the telemetry rows. Guard it so the script still runs without a database:

```python
        if persist_conn is not None:
            telemetry.record(
                persist_conn, classify_calls, node="classify", agent="ClassificationAgent"
            )
            telemetry.record(
                persist_conn, audit_calls, node="audit", agent="AuditorAgent"
            )
```

Add `persist_conn: Any = None` as a keyword parameter of `evaluate()`, and in `main()` open an engine connection unless `--no-persist` is passed. Add that flag to the argparse block with help text `"skip writing app.llm_calls rows"`.

- [ ] **Step 5: Run and verify pass**

Run: `.venv/bin/python -m pytest tests/test_eval_classification_attribution.py -v`
Expected: 3 passed.

- [ ] **Step 6: Prove `GROUP BY` has data**

Run a small live slice — 5 invoices is enough to prove the wiring, and Task 6's full run belongs to M6-C:

```bash
LLM_MODE=replay .venv/bin/python -m scripts.eval_classification --out /tmp/m6b-attr.json 2>&1 | tail -5
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c \
  "SELECT agent, model, count(*) FROM app.llm_calls WHERE agent IS NOT NULL GROUP BY 1,2 ORDER BY 1,2"
```
Expected: rows grouped by agent and model. Paste into the note.

- [ ] **Step 7: `make check` and commit**

```bash
make check
git add scripts/eval_classification.py tests/test_eval_classification_attribution.py
git commit -m "feat(m6b): record which model produced each classification"
```

---

### Task 9: Gate — measure, write the note, update the roadmap

**Files:**
- Create: `docs/notes/2026-08-04-m6b-observability.md`
- Modify: `docs/Plan_Project_F_2026-07-25.md` (items 6.5, 6.6; M6 gate table; §1 status paragraph)

- [ ] **Step 1: Run the full check from a clean state**

```bash
make check 2>&1 | tail -20
```
Expected: ruff clean, mypy clean, all tests pass. Record the test count.

- [ ] **Step 2: Confirm the suite still runs with no keys at all**

```bash
env -u GROQ_API_KEY -u GROQ_API_KEYS -u GEMINI_API_KEY -u OPENROUTER_API_KEY \
  LLM_MODE=replay .venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
```
Expected: green. This is the condition CI and the live demo run under.

- [ ] **Step 3: Write `docs/notes/2026-08-04-m6b-observability.md`**

Sections, each carrying **measured output pasted verbatim**, not prose claims:

1. **The tracing gap** — before/after `TRACING_ENABLED` (Task 1 Step 6), and why `observability.py` stopped being the verbatim M0.4 copy.
2. **24 nodes traced** — the per-graph counts, the four tags actually carried, and the mutation table. Include the signature-transparency check from Task 2 Step 3c: it is the reason the wrapper is safe to put in front of every node.
3. **Mutation results** — one row per mutation 1-7: which named test bit, its measured output, and whether it was reverted. **State honestly which suite bit.** M6-A's first cut claimed 4/4 and cited a test that did not exist; the corrected count was 3/4.
4. **HITL with tracing on** — the `HITL_RUNS=10` result and any latency delta.
5. **`trace_id`** — the psql table (no raw trace ids: M6-A's note failed the secrets scan on a hash that matched gitleaks' `generic-api-key` rule).
6. **Cost cap** — the boundary tests and the zero-`llm_calls` measurement.
7. **Token distribution** — the full n/min/median/mean/p95/p99/max output, the threshold chosen from it, and the `over budget: 0` corpus check.
8. **Model attribution** — the `GROUP BY agent, model` output.
9. **Anything that did not work** — carried forward as owed, in the roadmap's own idiom. Do not omit a partial result.

- [ ] **Step 4: Update the roadmap**

- Item **6.5**: mark done, cite the note, and state the tracing-was-off finding — it is the milestone's most portfolio-worthy result.
- Item **6.6**: mark done with both layers and the measured threshold.
- **Gate M6 table**: fill the "cost cap 1/1" row and add rows for the 24-node trace and `trace_id`. Leave the negative-control row at its measured **22/24, NOT met**.
- **§1**: update the "Next" paragraph — M6-B closed, M6-C open.

- [ ] **Step 5: Push and confirm CI is green on the pushed SHA**

```bash
git add docs/
git commit -m "docs(m6b): close M6-B on measured mutations and a tracing gap that was six days old"
git push origin main
gh run list --limit 3
```

Then wait for the run and confirm it passed **on the exact SHA just pushed**:

```bash
gh run watch "$(gh run list --limit 1 --json databaseId --jq '.[0].databaseId')"
```

- [ ] **Step 6: The gate is not met until Step 5 is green**

Four milestones in this project were declared closed on a red or unverified commit. If CI is red, fix it and re-push before writing any completion claim. Record the CI run URL in the note.

---

## Definition of done

| Criterion | Evidence |
|---|---|
| All 24 nodes traced | `tests/test_node_tracing.py`, mutations 1 and 2 red |
| Four tags carried | `node` + `agent` from the decorator, `invoice_id` + `batch_id` from state; tagging cannot fail a node |
| Cost + shadow cost as span metadata | `_persist_cost` test: actual $0 while shadow > 0 |
| Tracing enables from configured keys | before/after `TRACING_ENABLED` in the note |
| Kill-switch still wins | `tests/test_observability.py`, mutation 3 red |
| HITL durable with tracing ON | `HITL_RUNS=10` green with keys exported |
| `trace_id` written | non-null rows under a keyed run |
| Cost cap 1/1 | zero `llm_calls` rows after a rejected 10,000-file batch; mutations 4, 5, 6 |
| Token budget routes to manual queue | routing tests + `over budget: 0` across the corpus |
| Model recorded per call | `GROUP BY agent, model` returns rows |
| `make check` green on the pushed commit, CI agreeing | CI run URL on the closing SHA |
