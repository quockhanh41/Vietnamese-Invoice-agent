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

    try:
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
    finally:
        from sqlalchemy import text

        from invoice_agent.db.engine import get_owner_engine

        with get_owner_engine().begin() as conn:
            conn.execute(text("TRUNCATE app.invoices CASCADE"))
            conn.execute(
                text(
                    "UPDATE app.vendors SET invoice_count=0, total_amount=0, sum_sq_amount=0,"
                    " avg_amount=NULL, stddev_amount=NULL, rejection_count=0, reviewer_notes=NULL"
                )
            )


