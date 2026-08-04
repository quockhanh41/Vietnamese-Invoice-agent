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
    out: dict[str, object] = {}
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
            assert marker is not None
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
        {
            "node": "probe",
            "agent": "ProbeAgent",
            "invoice_id": "INV-1",
            "batch_id": "BATCH-9",
        }
    ]


def test_tag_current_span_updates_the_span_not_the_trace(monkeypatch):
    """Observes the REAL call `tag_current_span` makes to the Langfuse client,
    rather than the arguments `traced()` passes into `tag_current_span` (that
    is `test_a_traced_node_reports_the_ids_in_its_state`, above — it
    monkeypatches `tag_current_span` itself and so cannot see this).

    `update_current_trace` is trace-scoped: with all 24 nodes sharing one
    trace, tagging the trace makes `node`/`agent`/`invoice_id`/`batch_id`
    last-writer-wins across the whole run instead of per-node.
    `update_current_span` is scoped to the node's own span, which is what
    "four tags carried per node" (roadmap 6.5, note §2) actually requires.
    This fails if the implementation regresses to trace-scoped tagging, or
    stops calling the client altogether.
    """
    from invoice_agent import observability

    monkeypatch.setattr(observability, "TRACING_ENABLED", True)

    span_calls = []
    trace_calls = []

    class FakeClient:
        def update_current_span(self, **kwargs):
            span_calls.append(kwargs)

        def update_current_trace(self, **kwargs):
            trace_calls.append(kwargs)

    monkeypatch.setattr(observability, "_langfuse_client", lambda: FakeClient())

    observability.tag_current_span(
        node="probe", agent="ProbeAgent", invoice_id="INV-1", batch_id=None
    )

    assert span_calls == [
        {"metadata": {"node": "probe", "agent": "ProbeAgent", "invoice_id": "INV-1"}}
    ]
    assert trace_calls == []


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


def test_the_real_observe_decorator_does_not_swallow_graph_interrupt(monkeypatch):
    """`interrupt()` raises GraphInterrupt, and HITL durability depends on it
    propagating out of the node untouched.

    The real decorator is imported straight from langfuse rather than through
    observability, because the suite runs with the kill-switch on — through
    observability this would decorate with the identity function and assert
    nothing at all.

    LANGFUSE_PUBLIC_KEY/SECRET_KEY are deleted again right here rather than
    trusted to stay absent from conftest.py's earlier pop: `litellm` calls
    `load_dotenv()` on import (the same import-order effect `observability.py`
    documents), and some other test module importing `litellm` before this one
    runs refills both keys from `.env`. Measured 2026-08-04
    (`.superpowers/sdd/2026-08-04-m6b-observability-cost-cap/task-3-report.md`):
    without this delenv, the real `langfuse.observe` call below picks up the
    developer's live keys, opens an authenticated client against
    cloud.langfuse.com, and blocks ~3-4s at interpreter exit pausing/joining
    its background threads — after pytest has closed its output streams,
    producing `--- Logging error ---` spam. That is a genuine credential/data
    leak, not just noise: exactly what conftest.py exists to prevent, via a
    path that deliberately bypasses it. With the delenv, the client
    initializes disabled and shuts down instantly, with no threads to clean
    up. This does not weaken the test: the decorator under test is still the
    real one, unaffected by whether the client behind it is authenticated.
    """
    from langfuse import observe as real_observe
    from langgraph.errors import GraphInterrupt

    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    @real_observe(name="approval_gate")
    def raises_interrupt():
        raise GraphInterrupt(("pause here",))

    with pytest.raises(GraphInterrupt):
        raises_interrupt()
