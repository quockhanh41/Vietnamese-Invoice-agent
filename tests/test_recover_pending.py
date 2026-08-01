"""`recover_pending` — design §5.5.

Run from a COLD PROCESS. The claim under test is that the API never assumes an
in-RAM registry survived a restart; a test running in the process that created
the threads assumes exactly that, and would pass with the whole mechanism
missing.
"""

import json
import subprocess
import sys
from pathlib import Path

from tests.helpers.approval_fixtures import _initial_state, _persisted_invoice


def _run_scan_in_a_fresh_process() -> dict:
    project_root = Path(__file__).parent.parent
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json;from scripts.recover_pending import scan;print(json.dumps(scan()))",
        ],
        capture_output=True,
        text=True,
        cwd=project_root,
        env={"PYTHONPATH": str(project_root), **_clean_env()},
    )
    assert out.returncode == 0, f"scan crashed:\n{out.stderr}"
    return json.loads(out.stdout.strip().splitlines()[-1])


def _clean_env() -> dict:
    import os

    return {k: v for k, v in os.environ.items()}


def test_a_parked_thread_is_found_by_a_process_that_never_created_it(committed_rows):
    from invoice_agent.checkpointer import build_checkpointer
    from invoice_agent.graphs import approval

    db_id, approval_id = _persisted_invoice(committed_rows)
    with build_checkpointer() as saver:
        saver.setup()
        app = approval.build_approval_graph(checkpointer=saver)
        app.invoke(
            _initial_state(db_id, approval_id),
            {"configurable": {"thread_id": f"appr:{db_id}"}},
        )

    found = _run_scan_in_a_fresh_process()
    assert str(db_id) in [p["invoice_id"] for p in found["pending"]]


def test_an_approval_row_with_no_live_interrupt_is_reported_as_orphaned(committed_rows):
    """The case worth writing the script for. A row says a human is expected but
    no thread is waiting — the invoice would sit unreviewed forever. A script
    that reported only the healthy case would hide the failure it exists to
    find.
    """
    _db_id, _approval_id = _persisted_invoice(committed_rows)  # row, but no graph run

    found = _run_scan_in_a_fresh_process()
    assert str(_db_id) in [o["invoice_id"] for o in found["orphaned"]]
    assert str(_db_id) not in [p["invoice_id"] for p in found["pending"]]
