"""Load a real, reconciled MKT budget overspend for July 2026 -- the data
behind `docs/mcp-claude-desktop.md`'s gate transcript and `scripts/mcp_smoke.py`.

WHY THIS SCRIPT EXISTS (2026-08-06, code review of Task 6): the first version
of the transcript's data load lived only as a throwaway script in a
session-scoped scratchpad, cited from the doc as
`.superpowers/sdd/2026-08-05-m7-mcp-surface/task-6-report.md` -- a directory
`.superpowers/sdd/.gitignore` excludes entirely and which does not survive
past this milestone's execution. A doc that points a reader at a file that
will not exist does not satisfy "reproducible" (M7's gate criterion), so the
loader is committed here instead. See that report for the full investigation
of why `build_batch_graph()` -- not `make seed`, not `scripts/run_one.py` --
is the only path that populates `app.budget_consumption` at all.

WHAT IT LOADS AND WHY THESE EIGHT IDS. `corpus/manifest.jsonl` has 62
MKT-department, July-2026 invoices (measured 2026-08-05,
docs/notes/2026-08-05-m7-mcp-surface.md §8, which names the same eight ids and their total):
3 of them are `scripts/generator/gate_batch.GATE_BATCH_IDS`'s own MKT members
(a different, already-declared demo batch -- deliberately NOT reused here so
this script never collides with anything the M4 gate or its tests persist),
leaving 59 to choose from. `MKT_INVOICE_IDS` below is the first 8 of those 59
in canonical order (issue_date, invoice_series, invoice_number) -- the same
order `domain.budget.consume` sorts by -- and their total (56,525,000) is the
first prefix of that ordering to cross MKT's seeded July 2026 limit of
50,000,000 (`scripts/seed_reference.py`'s `BUDGETS`). Any other 8-or-more
prefix of the 59 would also work; these are simply the ones measured and
written into the doc.

`department="MKT"` is set on every task. This is not a fabrication: it is
exactly the `department` Form field `api/routes/batches.py`'s `POST /batches`
exposes for exactly this purpose. `serial_join` (`graphs/batch.py::_join`)
reconciles budget consumption against the department DECLARED AT UPLOAD TIME,
never against the `ClassificationAgent`'s own guess -- a deliberate safety
property (an LLM misclassification must not silently draw down the wrong
department's budget), not a default this script invented.

REQUIREMENTS TO RUN:
  * `LLM_MODE=replay` in the environment. `classify_node`/`audit_node`
    (`graphs/enrichment.py`) each call the LLM gateway once per invoice;
    replay mode reads `tests/fixtures/llm_cache` instead of a live model, so
    running this script spends ZERO tokens. `main()` asserts the mode rather
    than silently falling back to `live`, which would spend real money the
    moment the fixture cache misses.
  * Postgres migrated and seeded (`make dev`, or `make migrate && make seed`
    if already running) -- `app.budgets` must have the MKT/2026-07 row.
  * `corpus/` generated (`make generate --count 300 --seed 42`) -- the exact
    ids above only exist in that corpus.
  * Must NOT run while the test suite is running. `classify_node`/
    `audit_node` write to `app.llm_calls` unconditionally
    (`enrichment.py::_persist_cost`), even under replay -- concurrently with
    `tests/conftest.py`'s session-scoped `TRUNCATE app.invoices CASCADE`,
    this is the exact Postgres deadlock M6-C measured. Run this script and
    `pytest`/`make check` at separate times, never together.

IDEMPOTENCY. Safe to run twice against the SAME already-loaded database: each
invoice's natural key (`issue_date`, `invoice_series`, `invoice_number`) is
already in `app.invoices`, so `insert_with_lines` (via `ON CONFLICT DO
NOTHING`) returns no id for every one of them, and `serial_join` only ever
writes a `budget_consumption` row for an invoice it just persisted --
verified by reading `db/repositories/invoices.py::insert_row` and
`graphs/batch.py::_join`'s `if budget_row and c.invoice_id in db_id_map`
guard. A second run is therefore a true no-op: no duplicate invoices, no
doubled consumption. Also safe on a FRESHLY TRUNCATED database (the normal
case after `make check`), which is what it is designed for.

NOT idempotent against a PARTIAL prior state -- if some but not all of these
8 ids were already persisted by an unrelated earlier load, the budget
arithmetic (`domain_budget.consume`) still walks all 8 in canonical order to
compute the running balance, but only WRITES a `budget_consumption` row for
the ones newly persisted in this run; it does not net against consumption
already recorded by a DIFFERENT, earlier invocation covering only some of
these ids. This script's only supported starting states are "fresh" and
"already fully loaded by this same script" -- not a partial mix.
"""

import json
import pathlib
import uuid
from typing import Any

from invoice_agent.config import get_settings
from invoice_agent.graphs.batch import build_batch_graph

CORPUS = pathlib.Path("corpus")

# The first 8 (of 59) non-gate-batch MKT/July-2026 invoices in canonical
# order -- see the module docstring for why these and not some other 8.
MKT_INVOICE_IDS: tuple[str, ...] = (
    "INV-0101", "INV-0151", "INV-0127", "INV-0055",
    "INV-0031", "INV-0181", "INV-0107", "INV-0157",
)


def _load_manifest() -> dict[str, dict[str, Any]]:
    manifest = [
        json.loads(line)
        for line in (CORPUS / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    return {r["invoice_id"]: r for r in manifest}


def _build_inputs(batch_id: str, by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    inputs = []
    for inv_id in MKT_INVOICE_IDS:
        r = by_id[inv_id]
        if r["department"] != "MKT" or not r["issue_date"].startswith("2026-07"):
            raise SystemExit(
                f"{inv_id}: expected a July-2026 MKT invoice in corpus/manifest.jsonl, "
                f"found department={r['department']!r} issue_date={r['issue_date']!r} -- "
                "the corpus may have been regenerated with different content."
            )
        path = CORPUS / pathlib.Path(r["path"]).name
        inputs.append(
            {
                "batch_id": batch_id,
                "invoice_id": inv_id,
                "source_path": str(path),
                "source_type": "xml",
                "pdf_path": None,
                "department": "MKT",
            }
        )
    return inputs


def load() -> dict[str, Any]:
    """Run the batch graph over `MKT_INVOICE_IDS` and return its final state."""
    if get_settings().llm_mode != "replay":
        raise SystemExit(
            "LLM_MODE=replay is required (see module docstring) -- refusing to run "
            f"with llm_mode={get_settings().llm_mode!r}, which would spend real tokens "
            "on a fixture cache miss."
        )

    by_id = _load_manifest()
    batch_id = str(uuid.uuid4())
    inputs = _build_inputs(batch_id, by_id)

    state = {
        "batch_id": batch_id, "inputs": inputs, "enriched": [], "failed": [],
        "persisted": [], "consumption": [], "raised": [], "unroutable": [],
        "auto": [], "needs_approval": [], "posted": [], "opened": [], "unopened": [],
        "status": "",
    }
    return build_batch_graph().invoke(state)


def main() -> None:
    final = load()
    print("persisted      :", len(final.get("persisted") or []))
    print("failed         :", [f["invoice_id"] for f in (final.get("failed") or [])])
    print("unroutable     :", final.get("unroutable"))
    print("auto           :", final.get("auto"))
    print("needs_approval :", final.get("needs_approval"))
    print("posted         :", [p["invoice_id"] for p in (final.get("posted") or [])])
    print("opened         :", [o["invoice_id"] for o in (final.get("opened") or [])])
    print("unopened       :", final.get("unopened"))
    print(
        "raised codes   :",
        sorted({(r["invoice_id"], r["code"]) for r in (final.get("raised") or [])}),
    )
    print("consumption    :")
    for c in final.get("consumption") or []:
        print("  ", c)


if __name__ == "__main__":
    main()
