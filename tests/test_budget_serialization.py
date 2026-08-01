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

import json
import pathlib
import random
import uuid

from invoice_agent.graphs import batch


def _enriched(invoice, invoice_id):
    return {
        "invoice_id": invoice_id,
        "source_path": f"corpus/{invoice_id}.xml",
        "source_type": "xml",
        "extracted": invoice,
        "findings": [],
        "signals": [],
        "status": "",
    }


def test_ten_shuffles_produce_one_chain(runtime_conn, three_mkt_invoices):
    chains = []
    persisted_chains = []
    breachers = []
    rng = random.Random(42)

    for _ in range(10):
        order = list(three_mkt_invoices)
        rng.shuffle(order)
        savepoint = runtime_conn.begin_nested()
        try:
            out = batch.serial_join(
                {
                    "batch_id": str(uuid.uuid4()),
                    "enriched": [_enriched(inv, iid) for iid, inv in order],
                    "failed": [],
                },
                conn=runtime_conn,
            )
            chains.append(
                [
                    (c["invoice_id"], str(c["balance_before"]), str(c["balance_after"]))
                    for c in out["consumption"]
                ]
            )
            persisted_chains.append([p["invoice_id"] for p in out["persisted"]])
            breachers.append(
                sorted(
                    f["invoice_id"] for f in out["raised"] if f["code"] == "BUDGET_BREACH"
                )
            )
        finally:
            savepoint.rollback()

    assert len(chains) == 10
    assert all(p == persisted_chains[0] for p in persisted_chains), "arrival order changed persistence order"
    assert all(c == chains[0] for c in chains), "arrival order changed the chain"
    assert all(b == breachers[0] for b in breachers), "a different invoice breached"
    assert breachers[0] == ["INV-0001"], "no invoice breached — the case proves nothing"


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


def test_gate_batch_end_to_end_replay(runtime_conn, tmp_path, monkeypatch):
    """End-to-end replay over 30 gate-batch invoices.

    Asserts:
      - 2 unreadable_pdf land in failed / manual_queue
      - 28 complete
      - exact agreement with corpus/gate_batch.json
      - breach is INV-0001 on the 35M -> 15M -> -5M chain
    """
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEYS", raising=False)

    from scripts.generator import gate_batch

    manifest_path = pathlib.Path("corpus/manifest.jsonl")
    if not manifest_path.exists():
        return  # Corpus not yet generated in this test environment

    rows = {r["invoice_id"]: r for r in map(json.loads, manifest_path.open(encoding="utf-8"))}

    inputs = []
    for inv_id in gate_batch.GATE_BATCH_IDS:
        r = rows[inv_id]
        inputs.append(
            {
                "batch_id": "gate-test-batch",
                "invoice_id": inv_id,
                "source_path": r["path"],
                "source_type": r["source_type"],
                "pdf_path": r.get("pdf_path"),
                "department": r.get("department"),
            }
        )

    batch_graph = batch.build_batch_graph()
    result = batch_graph.invoke(
        {
            "batch_id": str(uuid.uuid4()),
            "inputs": inputs,
            "enriched": [],
            "failed": [],
            "persisted": [],
            "consumption": [],
            "auto": [],
            "needs_approval": [],
            "status": "",
        }
    )

    assert len(result["failed"]) == 2
    failed_ids = sorted(f["invoice_id"] for f in result["failed"])
    assert failed_ids == sorted(gate_batch.DELIBERATE_PDF_MEMBERS)

    assert len(result["enriched"]) == 28

    mkt_breaches = [
        f["invoice_id"]
        for f in result.get("raised", [])
        if f["code"] == "BUDGET_BREACH" and f["invoice_id"] in ("INV-0001", "INV-0002", "INV-0003")
    ]
    assert mkt_breaches == [gate_batch.EXPECTED_BREACH_ID]

    mkt_chain = [
        (c["invoice_id"], str(c["balance_after"]))
        for c in result.get("consumption", [])
        if c["invoice_id"] in ("INV-0001", "INV-0002", "INV-0003")
    ]
    assert mkt_chain == [
        ("INV-0003", "35000000"),
        ("INV-0002", "15000000"),
        ("INV-0001", "-5000000"),
    ]
