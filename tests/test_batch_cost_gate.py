"""The cost cap is the one guardrail design §9.2 states in terms of what must
NOT happen: "Upload 10.000 file -> reject BEFORE any LLM call".

So the test counts rows in app.llm_calls rather than asserting a status string.
A status can be correct while a call has already happened, which is exactly the
defect this task fixes in the other direction.
"""

import uuid

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
    finished reporting that it passed — and status is all an API caller can see."""
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
