"""The wrapper function node — design §4.1, and M3-C §8.3's unpaid debt.

§4.1 gives two reasons for a wrapper rather than a compiled subgraph, and M3-C
recorded that neither had ever been proven: explicit state mapping, and a
try/except per task so one bad file cannot end the superstep.
"""

import uuid

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
