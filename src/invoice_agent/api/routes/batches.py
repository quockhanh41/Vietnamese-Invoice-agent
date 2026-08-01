"""Upload, run, and report on batches.

The batch runs on a BACKGROUND THREAD and the request returns 202 immediately.
A batch takes seconds (4s replayed, 20.4s live — measured 2026-08-01,
docs/notes/2026-08-01-m5b-approval-graph.md), and holding the request open for
it would make a dropped client kill the run.

`department` form field is OPTIONAL and its absence has a consequence worth
stating. `serial_join` groups budgets by the department declared on the TASK;
`InvoiceData` carries no department, so an upload without one is persisted and
may be posted, but lands in `unroutable` and is never reconciled against a
budget. Recorded in the note (Task 8 step 3) rather than papered over with a
default — M4-D deleted an invented budget limit for the same reason.
"""

import json
import pathlib
import threading
import uuid
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from invoice_agent.api import progress
from invoice_agent.config import get_settings
from invoice_agent.db import queries
from invoice_agent.db.engine import get_engine
from invoice_agent.db.repositories import batches as batches_repo

router = APIRouter(prefix="/batches", tags=["batches"])


def _spool(batch_id: uuid.UUID, files: list[UploadFile]) -> list[dict[str, Any]]:
    root = pathlib.Path(get_settings().upload_dir) / str(batch_id)
    root.mkdir(parents=True, exist_ok=True)
    inputs = []
    for index, upload in enumerate(files, start=1):
        name = pathlib.Path(upload.filename or f"upload-{index}").name
        target = root / name
        target.write_bytes(upload.file.read())
        inputs.append(
            {
                "batch_id": str(batch_id),
                "invoice_id": target.stem,
                "source_path": str(target),
                # `pdf` routes to the vision branch, anything else to the XML
                # parser — the same two strategies `route_source` knows.
                "source_type": "pdf" if target.suffix.lower() == ".pdf" else "xml",
                "pdf_path": str(target) if target.suffix.lower() == ".pdf" else None,
            }
        )
    return inputs


def _run(batch_id: uuid.UUID, inputs: list[dict[str, Any]], department: str | None) -> None:
    from invoice_agent.graphs.batch import build_batch_graph

    if department is not None:
        for item in inputs:
            item["department"] = department

    state = {
        "batch_id": str(batch_id), "inputs": inputs, "enriched": [], "failed": [],
        "persisted": [], "consumption": [], "raised": [], "unroutable": [],
        "auto": [], "needs_approval": [], "posted": [], "opened": [], "unopened": [],
        "status": "",
    }
    try:
        for chunk in build_batch_graph().stream(state, stream_mode="updates"):
            for node, update in chunk.items():
                progress.publish(
                    str(batch_id),
                    {"node": node, "status": (update or {}).get("status")},
                )
    except Exception as exc:  # noqa: BLE001 - a failed batch must be reportable
        # The failure has to reach the DATABASE, not just this thread's stack.
        # A client polling GET /batches/{id} is the only observer that survives
        # the thread, so `batches.status` is where the truth goes.
        with get_engine().begin() as conn:
            batches_repo.complete_batch(conn, batch_id, status="failed", invoice_count=0)
        progress.publish(str(batch_id), {"node": "error", "detail": str(exc)})
    finally:
        progress.publish(str(batch_id), {"event": "end"})
        progress.finish(str(batch_id))


@router.post("", status_code=202)
def create_batch(
    files: list[UploadFile] = File(...),  # noqa: B008
    department: str | None = Form(default=None),
) -> dict[str, Any]:
    """`department` is OPTIONAL and its absence has a consequence worth stating.

    `serial_join` groups budgets by the department declared on the TASK;
    `InvoiceData` carries no department, so an upload without one is persisted
    and may be posted, but lands in `unroutable` and is never reconciled against
    a budget. Recorded in the spec's open questions rather than papered over
    with a default — M4-D deleted an invented budget limit for the same reason.
    """
    if not files:
        raise HTTPException(status_code=422, detail="at least one file is required")

    batch_id = uuid.uuid4()
    inputs = _spool(batch_id, files)
    with get_engine().begin() as conn:
        batches_repo.ensure_batch(conn, batch_id, label=f"upload:{len(inputs)}")

    threading.Thread(target=_run, args=(batch_id, inputs, department), daemon=True).start()
    return {"batch_id": str(batch_id), "invoice_count": len(inputs)}


@router.get("/{batch_id}")
def get_batch(batch_id: uuid.UUID) -> dict[str, Any]:
    """DB truth: status + persisted/posted/pending/failed counts.

    Design §5.5: the in-process registry is expected to be lost on restart.
    This always counts rows in Postgres, so it answers correctly whether or not
    anyone was ever streaming.
    """
    with get_engine().begin() as conn:
        summary = queries.batch_summary(conn, batch_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"batch {batch_id} not found")
    return summary


@router.get("/{batch_id}/events")
def events(batch_id: uuid.UUID) -> StreamingResponse:
    def _generate():
        for event in progress.stream(str(batch_id)):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")
