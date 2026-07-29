"""Run one invoice through `enrichment_graph` — the M3 gate demo.

Deliberately thin: M4's `batch_graph` replaces it. Its one non-obvious job is
the cost backfill, and the reason is in spec §5.3 — a PDF-only invoice has no
`invoices` row until vision has already run, so `vision_extract` writes its
`llm_calls` rows with `invoice_id` NULL and `thread_id` set, and this script
links them once the row exists.
"""

import argparse
import pathlib
from typing import Any

from sqlalchemy import update

from invoice_agent.db import models
from invoice_agent.db.engine import get_engine
from invoice_agent.db.repositories import invoices as invoice_repo
from invoice_agent.graphs.enrichment import build_enrichment_graph
from invoice_agent.llm.gateway import LLMGateway
from scripts.generator import manifest


def run_one(
    invoice_id: str,
    *,
    corpus: pathlib.Path = pathlib.Path("corpus"),
    gateway: LLMGateway | None = None,
) -> dict[str, Any]:
    entries = {
        row["invoice_id"]: row
        for row in manifest.read_manifest(corpus / "manifest.jsonl")
    }
    if invoice_id not in entries:
        raise SystemExit(f"{invoice_id} is not in {corpus / 'manifest.jsonl'}")
    entry = entries[invoice_id]

    source = corpus / pathlib.Path(entry["path"]).name
    twin = entry.get("pdf_path")
    state = {
        "invoice_id": invoice_id,
        "source_path": str(source),
        "pdf_path": str(corpus / pathlib.Path(twin).name) if twin else None,
        "source_type": "xml" if entry["source_type"] == "xml" else "pdf",
        "strategy": "xml",
        "extracted": None,
        "extraction_attempts": 0,
        "current_errors": [],
        "error_history": [],
        "findings": [],
        "llm_calls": [],
        "status": "received",
    }

    final = build_enrichment_graph(gateway=gateway).invoke(state)

    db_invoice_id = None
    if final.get("extracted") is not None:
        with get_engine().begin() as conn:
            db_invoice_id = invoice_repo.insert_with_lines(
                conn,
                final["extracted"],
                source_type="pdf_vision" if final["strategy"] == "vision" else "xml",
                source_path=str(source),
            )
            if db_invoice_id is not None:
                # The backfill. `thread_id` is the correlation key design §1.3
                # specifies; migration 10 added the column and migration 09
                # already granted UPDATE on this table.
                conn.execute(
                    update(models.llm_calls)
                    .where(models.llm_calls.c.thread_id == f"enrich:{invoice_id}")
                    .where(models.llm_calls.c.invoice_id.is_(None))
                    .values(invoice_id=db_invoice_id)
                )

    return {**final, "db_invoice_id": db_invoice_id}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoice", required=True)
    parser.add_argument("--corpus", type=pathlib.Path, default=pathlib.Path("corpus"))
    args = parser.parse_args()

    result = run_one(args.invoice, corpus=args.corpus)
    print(f"strategy   : {result['strategy']}")
    print(f"attempts   : {result['extraction_attempts']}")
    print(f"status     : {result['status']}")
    print(f"findings   : {[f.code for f in result['findings']]}")
    print(f"llm calls  : {len(result['llm_calls'])}")
    print(f"invoice row: {result['db_invoice_id']}")


if __name__ == "__main__":
    main()
