"""The cost cap is the one guardrail design §9.2 states in terms of what must
NOT happen: "Upload 10.000 file -> reject BEFORE any LLM call".

So the test counts rows in app.llm_calls rather than asserting a status string.
A status can be correct while a call has already happened, which is exactly the
defect this task fixes in the other direction.
"""

import json
import pathlib
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
    finished reporting that it passed — and status is all an API caller can see."""
    assert cost_gate_node(_state(10_000))["status"] == "cost_gate_rejected"
    assert cost_gate_node(_state(1))["status"] == "cost_gate_passed"


def test_a_rejected_batch_makes_no_llm_call(runtime_conn, monkeypatch):
    """The measurable form of "before any LLM call".

    Uses ONE REAL corpus invoice, not the bare `{"path": ...}` dicts `_state()`
    builds. Those dicts are cheap enough to build 10,000 of, but that cheapness
    is exactly the problem: `enrich_one`'s first line is `task["invoice_id"]`,
    outside its own try/except, so a `{"path": ...}` task raises `KeyError`
    before the enrichment subgraph — and therefore before any LLM call — is
    ever reached. A mutant that moves the gate AFTER fan-out would still make
    this test go red, but for that KeyError, not because a call happened. A
    test that cannot tell "the gate caught it" apart from "the fixture was
    malformed" is not guarding the property it names (Task 5 report, Mutation 8).

    `max_batch_files` is patched to 1 so a single real invoice is already
    oversized — no need for 10,000 real files. The boundary value itself is
    covered separately by `test_exactly_ten_thousand_files_is_rejected`.
    """
    from invoice_agent.config import get_settings
    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.graphs import batch as batch_mod
    from invoice_agent.graphs.batch import build_batch_graph

    monkeypatch.setenv("LLM_MODE", "replay")
    for key in ("GROQ_API_KEYS", "GEMINI_API_KEYS", "OPENROUTER_API_KEYS"):
        monkeypatch.delenv(key, raising=False)
    # WITHOUT THIS THE MONKEYPATCH DOES NOTHING — get_settings is lru_cached,
    # so a Settings built earlier in the session keeps llm_mode="live" with
    # real keys loaded (test_batch_not_blocked_by_pending.py hit exactly this
    # at M4-D: a gate test made LIVE calls and passed only by luck).
    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(
        batch_mod, "get_settings", lambda: settings.model_copy(update={"max_batch_files": 1})
    )

    manifest = pathlib.Path("corpus/manifest.jsonl")
    if not manifest.exists():
        pytest.skip("corpus not generated — run `make generate`")
    rows = {r["invoice_id"]: r for r in map(json.loads, manifest.open(encoding="utf-8"))}
    row = rows["INV-0001"]

    # A synthetic invoice_id — NOT the corpus's own "INV-0001" — so the
    # cleanup below (app.llm_calls.thread_id is f"enrich:{invoice_id}", set by
    # enrichment._persist_cost) can scope to exactly this run's rows, never a
    # broad predicate that could delete another test's or a live demo's real
    # "INV-0001" telemetry from this shared dev database.
    test_invoice_id = f"cost-gate-mutation-check-{uuid.uuid4()}"
    inputs = [
        {
            "invoice_id": test_invoice_id,
            "source_path": row["path"],
            "source_type": row["source_type"],
            "pdf_path": row.get("pdf_path"),
            "department": row.get("department"),
        }
    ]

    before = runtime_conn.execute(text("SELECT count(*) FROM app.llm_calls")).scalar_one()

    try:
        graph = build_batch_graph()
        result = graph.invoke(
            {
                "batch_id": str(uuid.uuid4()),
                "inputs": inputs,
                "enriched": [],
                "failed": [],
                "persisted": [],
                "consumption": [],
                "raised": [],
                "unroutable": [],
            }
        )

        after = runtime_conn.execute(text("SELECT count(*) FROM app.llm_calls")).scalar_one()
        assert after == before, f"{after - before} LLM calls made by a rejected batch"
        assert result["status"] == "cost_gate_rejected"
    finally:
        # app.llm_calls has no DELETE grant for the runtime role (append-only
        # by design, migration b25d8a939562) — cleanup needs the owner role.
        # Scoped to exactly the synthetic thread_id this run minted, so it
        # cannot touch anyone else's rows even if this assertion fails above.
        with get_owner_engine().begin() as conn:
            conn.execute(
                text("DELETE FROM app.llm_calls WHERE thread_id = :t"),
                {"t": f"enrich:{test_invoice_id}"},
            )


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
