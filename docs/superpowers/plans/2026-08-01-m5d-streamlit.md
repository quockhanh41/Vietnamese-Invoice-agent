# M5-D: the Streamlit approval UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the demo the roadmap promised from M5 onward — upload, reviewer queue, detail, approve/reject — as a Streamlit client that reaches the system only over HTTP, so the demo is itself the proof that M5-C's API works.

**Architecture:** `ui/client.py` holds every HTTP call and all the logic, with an injectable `httpx.Client` so tests drive it through `TestClient(create_app())` in-process. `ui/app.py` is layout only; two structural tests keep it that way.

**Tech Stack:** Streamlit ≥1.28 (**new main dependency**) · httpx 0.28.1 (already present) · FastAPI TestClient · pytest

**Spec:** [`docs/superpowers/specs/2026-08-01-m5d-streamlit-design.md`](../specs/2026-08-01-m5d-streamlit-design.md)

## Global Constraints

Copied from spec §0.

1. **The UI reaches the system only over HTTP.** No module under `ui/` may import `invoice_agent.db`, `.graphs`, `.tools`, `.domain`, `.llm`, `.memory` or `.extraction`. Exactly one exception: `invoice_agent.config`.
2. **`app.py` contains no HTTP.** Every call goes through `client.py`.
3. **A non-2xx never becomes a silent success.** `client.py` raises `ApiError`.
4. **UI copy is Vietnamese.** Code, comments, docstrings and commits stay English. Anything a reviewer reads on screen — labels, buttons, errors, flag narratives — is Vietnamese.
5. **No new backend behaviour.** Nothing outside `ui/` changes except one `config.py` setting. If the UI wants an endpoint that does not exist, record it as a finding.
6. **`make check` after EVERY task; push before claiming anything.** M5-C was the first milestone to pass this unprompted — do not regress it.

## Preconditions

```bash
make dev && make generate && make check     # must be green before starting
```

## File Structure

**Create:** `src/invoice_agent/ui/{__init__,client,app}.py` · `tests/test_ui_client.py` · `tests/test_ui_boundary.py` · `tests/test_ui_app.py` · `docs/notes/2026-08-01-m5d-streamlit.md`

**Modify:** `pyproject.toml` (streamlit) · `requirements.lock` · `src/invoice_agent/config.py` (`api_base_url`) · `Makefile` (`demo`) · `docs/Plan_Project_F_2026-07-25.md`

---

### Task 1: `config.api_base_url`, `ApiError`, and the client's transport seam

Everything else depends on the error contract and the injectable client, so they come first.

**Files:**
- Create: `src/invoice_agent/ui/__init__.py`, `src/invoice_agent/ui/client.py`
- Modify: `src/invoice_agent/config.py`, `pyproject.toml`
- Test: `tests/test_ui_client.py`

**Interfaces:**
- Consumes: `invoice_agent.config.get_settings`
- Produces:
  - `class ApiError(Exception)` with `.status_code: int` and `.detail: str`
  - `health(*, client: httpx.Client | None = None) -> dict[str, Any]`
  - the `_session` / `_get` / `_post` helpers every later function uses

- [ ] **Step 1: Add the dependency and the setting**

`pyproject.toml`, in `dependencies` (**main**, not dev — roadmap §0 makes the demo the deliverable, not a dev tool):

```toml
    # The demo IS the deliverable (roadmap §0: "Luôn có demo chạy được từ M5"),
    # so it ships with the package. Floor is 1.28 because `st.testing.v1.AppTest`
    # — the only thing that can catch an AttributeError in app.py — appeared
    # there. Verified at install time by tests/test_ui_app.py's import.
    "streamlit>=1.28",
```

`src/invoice_agent/config.py`, near `upload_dir`:

```python
    # Where `ui/` reaches the API. The UI is an HTTP client and nothing else
    # (spec §2.1); this is the ONLY thing it imports from this package, because
    # reading an env var directly would reintroduce the scattered-os.getenv debt
    # M0.2 paid off.
    api_base_url: str = "http://127.0.0.1:8000"
```

Then install and confirm the floor is real:

```bash
uv pip install -e ".[dev]"
.venv/bin/python -c "import streamlit; from streamlit.testing.v1 import AppTest; print('streamlit', streamlit.__version__, '· AppTest OK')"
```

If `AppTest` does not import, raise the floor to the version that provides it and
record the measured version in the pyproject comment — do not silently drop the
smoke test.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_ui_client.py`:

```python
"""The UI's HTTP client, driven through the real app in-process.

`TestClient` IS an `httpx.Client` over an ASGI transport (verified 2026-08-01:
`issubclass(TestClient, httpx.Client)` is True), so every test here exercises the
whole request/response path — routing, validation, serialisation — with no
uvicorn and no port.
"""

import pytest
from fastapi.testclient import TestClient
# `committed_rows` is a FIXTURE and arrives via conftest's pytest_plugins, but
# `_persisted_invoice` and `_initial_state` are plain functions — they must be
# imported. Verified 2026-08-01: tests/test_api_read.py imports them the same way.
from tests.helpers.approval_fixtures import _initial_state, _persisted_invoice

from invoice_agent.api.app import create_app
from invoice_agent.ui import client as ui


@pytest.fixture
def api():
    with TestClient(create_app()) as c:
        yield c


def test_health_round_trips(api):
    assert ui.health(client=api) == {"status": "ok", "database": "ok"}


def test_a_non_2xx_raises_ApiError_carrying_the_status(api):
    """A UI that renders success on a failed write is the same class of defect as
    a guard swallowing its own denial (spec §2.3). Nothing may return quietly."""
    with pytest.raises(ui.ApiError) as excinfo:
        ui.get_invoice("00000000-0000-0000-0000-000000000000", client=api)
    assert excinfo.value.status_code == 404
    assert "not found" in excinfo.value.detail


def test_a_validation_error_becomes_readable_text(api, committed_rows):
    """FastAPI returns 422 with a LIST of error dicts. Rendering that list raw in
    a Streamlit `st.error` is unreadable, so the client flattens it here — at the
    boundary — rather than leaving app.py to do it untestably."""
    db_id, _ = _persisted_invoice(committed_rows)
    with pytest.raises(ui.ApiError) as excinfo:
        ui.submit_decision(
            str(db_id), decision="aproved", decided_by="ketoan", client=api
        )
    assert excinfo.value.status_code == 422
    assert isinstance(excinfo.value.detail, str)
    assert "decision" in excinfo.value.detail


def test_a_borrowed_client_is_not_closed(api):
    """The caller owns an injected client. Closing it would break the second call
    in any test — and, in `app.py`, a reused session."""
    ui.health(client=api)
    ui.health(client=api)
    assert not api.is_closed
```

- [ ] **Step 3: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_ui_client.py -v
```

Expected: FAIL — `No module named 'invoice_agent.ui'`.

- [ ] **Step 4: Implement**

`src/invoice_agent/ui/__init__.py`: empty.

`src/invoice_agent/ui/client.py`:

```python
"""The UI's only route to the system: HTTP.

THIS MODULE MAY NOT IMPORT `invoice_agent.db`, `.graphs`, `.tools`, `.domain`,
`.llm`, `.memory` OR `.extraction`. `tests/test_ui_boundary.py` enforces it.
`invoice_agent.config` is the single allowed exception, for the base URL.

The rule is what makes the demo PROOF. Streamlit is Python, so importing
`submit_decision` directly would work and would be shorter — and M5-C's only
consumer would then bypass the API, leaving it dead code with a passing test
suite.

Every function takes an optional `client`, so tests drive the whole stack through
`TestClient(create_app())` in-process. Same shape as `serial_join(conn=...)`.
"""

import contextlib
from collections.abc import Iterator
from typing import Any

import httpx

from invoice_agent.config import get_settings

# Long enough for a live batch (20.4s measured at M5-B,
# docs/notes/2026-08-01-m5b-approval-graph.md), short enough that a dead API
# fails the page instead of hanging it.
TIMEOUT_S = 60.0


class ApiError(Exception):
    """A non-2xx from the API, carrying enough to render a Vietnamese message."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


@contextlib.contextmanager
def _session(client: httpx.Client | None) -> Iterator[httpx.Client]:
    """Borrow the caller's client, or own one for the duration.

    A BORROWED client is never closed — the caller owns it, and closing a
    `TestClient` mid-test breaks every later call.
    """
    if client is not None:
        yield client
        return
    with httpx.Client(base_url=get_settings().api_base_url, timeout=TIMEOUT_S) as owned:
        yield owned


def _detail(response: httpx.Response) -> str:
    """Flatten FastAPI's error body into one readable line.

    422 bodies are a LIST of `{"loc": [...], "msg": ...}` dicts. Rendering that
    raw in `st.error` is unreadable, so it is flattened HERE — at the boundary,
    where a test can reach it — rather than in `app.py`, where none can.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, list):
        return "; ".join(
            f"{'.'.join(str(p) for p in item.get('loc', []))}: {item.get('msg', '')}"
            for item in detail
            if isinstance(item, dict)
        )
    return str(detail)


def _unwrap(response: httpx.Response) -> Any:
    if not response.is_success:
        raise ApiError(response.status_code, _detail(response))
    return response.json()


def _get(path: str, *, client: httpx.Client | None, params: dict[str, Any] | None = None) -> Any:
    with _session(client) as session:
        return _unwrap(session.get(path, params=params))


def health(*, client: httpx.Client | None = None) -> dict[str, Any]:
    return _get("/health", client=client)
```

- [ ] **Step 5: Run to verify they pass**

`test_a_validation_error_becomes_readable_text` and the 404 test need functions
from Task 2/3 — run only what exists now:

```bash
.venv/bin/python -m pytest tests/test_ui_client.py -k "health or borrowed" -v
```

Expected: PASS.

- [ ] **Step 6: `make check` and commit**

```bash
make check
git add src/invoice_agent/ui src/invoice_agent/config.py pyproject.toml requirements.lock tests/test_ui_client.py
git commit -m "feat(m5d): add the UI HTTP client seam, ApiError and api_base_url

Every client function takes an optional httpx.Client, so tests drive the whole
request/response path through TestClient(create_app()) in-process — TestClient
IS an httpx.Client subclass (verified 2026-08-01). A borrowed client is never
closed; the caller owns it.

_detail flattens FastAPI's 422 list-of-dicts into one line AT THE BOUNDARY,
where a test can reach it, rather than in app.py where none can.

streamlit is a MAIN dependency: roadmap §0 makes the demo the deliverable, not a
dev tool.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 2: The approval functions — the safety-critical half

**Files:**
- Modify: `src/invoice_agent/ui/client.py`
- Test: `tests/test_ui_client.py` (append)

**Interfaces:**
- Consumes: Task 1's `_get`, `_session`, `_unwrap`, `ApiError`
- Produces:
  - `pending_approvals(*, client=None) -> list[dict[str, Any]]`
  - `approval_detail(invoice_id: str, *, client=None) -> dict[str, Any]`
  - `submit_decision(invoice_id: str, *, decision: str, decided_by: str, reason: str | None = None, corrections: dict[str, Any] | None = None, client=None) -> dict[str, Any]`
  - `get_invoice(invoice_id: str, *, client=None) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

```python
def _park(committed_rows):
    """Persist an invoice and drive its approval thread to the interrupt."""
    from invoice_agent.checkpointer import build_checkpointer
    from invoice_agent.graphs import approval

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    with build_checkpointer() as saver:
        saver.setup()
        approval.build_approval_graph(checkpointer=saver).invoke(
            _initial_state(db_id, approval_id),
            {"configurable": {"thread_id": f"appr:{db_id}"}},
        )
    return db_id


def test_the_queue_and_the_detail_a_reviewer_reads(api, committed_rows):
    db_id = _park(committed_rows)

    queue = ui.pending_approvals(client=api)
    assert str(db_id) in [row["invoice_id"] for row in queue]

    detail = ui.approval_detail(str(db_id), client=api)
    assert detail["decision"] is None
    payload = detail["interrupt_payload"]
    assert payload["invoice"]["invoice_number"]
    # Vietnamese narratives arrive untranslated — an accountant reads them.
    assert payload["flags"][0]["code"] == "PRICE_OUTLIER"


def test_a_decision_through_the_client_posts_exactly_one_entry(api, committed_rows):
    """THE GATE. The UI drives the same `submit_decision` a test, a script and
    curl drive — which is what makes the demo proof rather than decoration."""
    from sqlalchemy import select

    from invoice_agent.db import models
    from invoice_agent.db.engine import get_engine

    db_id = _park(committed_rows)
    result = ui.submit_decision(
        str(db_id), decision="approved", decided_by="ketoan", reason="đã kiểm tra",
        client=api,
    )
    assert result["decision"] == "approved"
    assert result["entry_id"]

    with get_engine().begin() as conn:
        entries = list(
            conn.execute(
                select(models.journal_entries).where(
                    models.journal_entries.c.invoice_id == db_id
                )
            ).mappings()
        )
    assert len(entries) == 1
    assert entries[0]["posting_authority"] == "human_approved"


def test_a_409_becomes_an_ApiError_not_a_silent_success(api, committed_rows):
    """THE MUTATION THAT MATTERS (spec §2.3). A client that returned the error
    body would make the UI render "đã duyệt" while NOTHING was posted, and no
    screenshot would reveal it."""
    db_id = _park(committed_rows)
    ui.submit_decision(str(db_id), decision="approved", decided_by="ketoan", client=api)

    with pytest.raises(ui.ApiError) as excinfo:
        ui.submit_decision(
            str(db_id), decision="approved", decided_by="ketoan", client=api
        )
    assert excinfo.value.status_code == 409
    assert "already" in excinfo.value.detail.lower()


def test_get_invoice_returns_lines_and_flags(api, committed_rows):
    db_id = _park(committed_rows)
    body = ui.get_invoice(str(db_id), client=api)
    assert body["invoice"]["id"] == str(db_id)
    assert body["lines"]
    assert [f["code"] for f in body["flags"]] == ["PRICE_OUTLIER"]
```

- [ ] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_ui_client.py -k "queue or decision or 409 or get_invoice" -v
```

Expected: FAIL — `module 'invoice_agent.ui.client' has no attribute 'pending_approvals'`.

- [ ] **Step 3: Implement**

Append to `src/invoice_agent/ui/client.py`:

```python
def pending_approvals(*, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = _get("/approvals", client=client)
    return result


def approval_detail(invoice_id: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    result: dict[str, Any] = _get(f"/approvals/{invoice_id}", client=client)
    return result


def get_invoice(invoice_id: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    result: dict[str, Any] = _get(f"/invoices/{invoice_id}", client=client)
    return result


def submit_decision(
    invoice_id: str,
    *,
    decision: str,
    decided_by: str,
    reason: str | None = None,
    corrections: dict[str, Any] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Resume a parked approval thread over HTTP.

    Sends ONLY the four fields `DecisionRequest` declares. `posting_authority`
    and friends are not omitted by politeness — the schema forbids extras, so a
    request carrying them is a 422 (M5-C spec §2.3). Nothing here needs to
    defend against that; the point is that this client has no way to express it
    either.
    """
    body: dict[str, Any] = {"decision": decision, "decided_by": decided_by}
    if reason is not None:
        body["reason"] = reason
    if corrections is not None:
        body["corrections"] = corrections
    with _session(client) as session:
        result: dict[str, Any] = _unwrap(
            session.post(f"/approvals/{invoice_id}/decision", json=body)
        )
    return result
```

- [ ] **Step 4: Run to verify they pass**

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -tAc "TRUNCATE app.invoices CASCADE;"
.venv/bin/python -m pytest tests/test_ui_client.py -v
```

Expected: all PASS, including Task 1's two deferred tests.

- [ ] **Step 5: `make check` and commit**

```bash
make check
git add src/invoice_agent/ui/client.py tests/test_ui_client.py
git commit -m "feat(m5d): add the approval client functions

submit_decision sends only DecisionRequest's four fields — not by politeness but
because this client has no way to express posting_authority, and the schema
would 422 it anyway.

A 409 raises ApiError rather than returning the body: a client that returned it
would make the UI render 'đã duyệt' while nothing was posted, and no screenshot
would reveal that.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 3: Batches, invoices list, reports, and the department selector's source

**Files:**
- Modify: `src/invoice_agent/ui/client.py`
- Test: `tests/test_ui_client.py` (append)

**Interfaces:**
- Consumes: Task 1's helpers
- Produces:
  - `upload_batch(files: list[tuple[str, bytes]], *, department: str | None = None, client=None) -> dict[str, Any]`
  - `get_batch(batch_id: str, *, client=None) -> dict[str, Any]`
  - `list_invoices(*, status=None, department=None, limit=50, client=None) -> list[dict[str, Any]]`
  - `budget_report(*, period=None, client=None)`, `flag_report(*, client=None)`, `posting_report(*, client=None)`, `cost_report(*, client=None)`
  - `departments(*, client=None) -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
def _one_real_invoice() -> tuple[str, bytes]:
    import pathlib

    p = pathlib.Path("tests/fixtures/real_samples/viettelpost_gtgt_2.1.0.xml")
    return p.name, p.read_bytes()


def test_upload_returns_a_batch_id_and_the_summary_answers(api, monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODE", "replay")
    for key in ("GROQ_API_KEYS", "GEMINI_API_KEYS", "OPENROUTER_API_KEYS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    # get_settings is lru_cached — without this the monkeypatch does nothing and
    # the upload makes LIVE calls (measured 2026-08-01 at M4-D).
    from invoice_agent.config import get_settings

    get_settings.cache_clear()

    import time

    name, data = _one_real_invoice()
    posted = ui.upload_batch([(name, data)], department="ADMIN", client=api)
    assert posted["invoice_count"] == 1

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        summary = ui.get_batch(posted["batch_id"], client=api)
        if summary["status"] in ("completed", "failed"):
            break
        time.sleep(0.2)
    else:
        pytest.fail("batch did not finish within 60s")

    assert summary["status"] == "completed"
    assert summary["persisted"] == 1
    get_settings.cache_clear()


def test_departments_come_from_the_budget_report_not_a_hardcoded_list(api):
    """Spec §3.1. Hardcoding the five seeded departments in the UI would create a
    SECOND source of truth for seed data — exactly what seed_reference.py's
    docstring forbids for BUDGETS. The selector reads them from a report the API
    already serves, so adding a department to the seed changes the UI without
    touching the UI."""
    from scripts.seed_reference import DEPARTMENTS

    assert set(ui.departments(client=api)) == {code for code, _name in DEPARTMENTS}


def test_every_report_round_trips(api, committed_rows):
    _persisted_invoice(committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly"))
    assert ui.budget_report(client=api)
    assert any(r["code"] == "PRICE_OUTLIER" for r in ui.flag_report(client=api))
    ui.posting_report(client=api)  # may be empty; must not raise
    assert "shadow_cost_usd" in ui.cost_report(client=api)


def test_list_invoices_passes_filters_through(api, committed_rows):
    db_id, _ = _persisted_invoice(committed_rows)
    hit = ui.list_invoices(department="ADMIN", client=api)
    miss = ui.list_invoices(department="MKT", client=api)
    assert str(db_id) in [r["id"] for r in hit]
    assert str(db_id) not in [r["id"] for r in miss]
```

- [ ] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_ui_client.py -k "upload or departments or report or filters" -v
```

Expected: FAIL — no `upload_batch`.

- [ ] **Step 3: Implement**

```python
def upload_batch(
    files: list[tuple[str, bytes]],
    *,
    department: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """`files` is [(filename, bytes)]. `department` is OPTIONAL and its absence
    has a consequence: `serial_join` groups budgets by the department declared on
    the task, and `InvoiceData` carries none, so an upload without one is
    persisted and may be posted but is never reconciled against a budget
    (M5-C note §3). The UI offers the selector so a reviewer can avoid that."""
    multipart = [("files", (name, data, "application/octet-stream")) for name, data in files]
    form = {"department": department} if department is not None else None
    with _session(client) as session:
        result: dict[str, Any] = _unwrap(session.post("/batches", files=multipart, data=form))
    return result


def get_batch(batch_id: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    result: dict[str, Any] = _get(f"/batches/{batch_id}", client=client)
    return result


def list_invoices(
    *,
    status: str | None = None,
    department: str | None = None,
    limit: int = 50,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": limit}
    if status is not None:
        params["status"] = status
    if department is not None:
        params["department"] = department
    result: list[dict[str, Any]] = _get("/invoices", client=client, params=params)
    return result


def budget_report(
    *, period: str | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    params = {"period": period} if period is not None else None
    result: list[dict[str, Any]] = _get("/reports/budgets", client=client, params=params)
    return result


def flag_report(*, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = _get("/reports/flags", client=client)
    return result


def posting_report(*, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = _get("/reports/posting", client=client)
    return result


def cost_report(*, client: httpx.Client | None = None) -> dict[str, Any]:
    result: dict[str, Any] = _get("/reports/cost", client=client)
    return result


def departments(*, client: httpx.Client | None = None) -> list[str]:
    """The seeded departments, READ FROM A REPORT rather than hardcoded.

    There is no `/departments` endpoint and M5-D adds no backend behaviour
    (spec §0 constraint 5), but `/reports/budgets` already returns
    `department_code` per row. Hardcoding the five would be a second source of
    truth for seed data — what `scripts/seed_reference.py` forbids for BUDGETS,
    because two copies is how a test starts passing for the wrong reason.
    """
    return sorted({row["department_code"] for row in budget_report(client=client)})
```

- [ ] **Step 4: Run to verify they pass**

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -tAc "TRUNCATE app.invoices CASCADE;"
.venv/bin/python -m pytest tests/test_ui_client.py -v
```

Expected: all PASS.

- [ ] **Step 5: `make check` and commit**

```bash
make check
git add src/invoice_agent/ui/client.py tests/test_ui_client.py
git commit -m "feat(m5d): add batch, invoice-list and report client functions

departments() reads the seeded list from /reports/budgets rather than hardcoding
it. There is no /departments endpoint and M5-D adds no backend behaviour, but a
hardcoded list would be a second source of truth for seed data — what
seed_reference.py forbids for BUDGETS, because two copies is how a test starts
passing for the wrong reason.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 4: `app.py` — four pages, and graceful degradation

**Files:**
- Create: `src/invoice_agent/ui/app.py`, `tests/test_ui_app.py`
- Test: `tests/test_ui_app.py`

**Interfaces:**
- Consumes: every `client.py` function from Tasks 1–3
- Produces: a Streamlit script runnable by `streamlit run src/invoice_agent/ui/app.py` and by `AppTest.from_file`

> **A requirement the spec did not state, found while planning.** `AppTest` runs
> `app.py` in-process with **no API server listening**, so the first client call
> raises `httpx.ConnectError`. If `app.py` does not handle that, the smoke test
> can only ever assert "it crashed". Handling it is also correct behaviour — a
> reviewer whose API is down should see *"Không kết nối được tới API"*, not a
> traceback. So every page wraps its calls and renders a Vietnamese message on
> `ApiError` **or** `httpx.HTTPError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ui_app.py`:

```python
"""Smoke tests for the Streamlit script.

The ONLY test that can catch an AttributeError in `app.py` — no client test
reaches it, and the app is the milestone's actual artefact. Fragile by nature:
AppTest re-executes the script in a harness that is not a browser. If it becomes
unmaintainable, DELETE it and say so in the note; never leave it skipped, because
a skipped test reports as a pass.
"""

import pytest

pytest.importorskip("streamlit", minversion="1.28")

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = "src/invoice_agent/ui/app.py"
PAGES = ["Tải lên hóa đơn", "Hàng chờ duyệt", "Chi tiết & quyết định", "Báo cáo"]


def test_the_app_renders_when_the_api_is_unreachable():
    """No server is listening in this process. The app must say so in Vietnamese
    rather than raising — which is both the correct reviewer experience and what
    makes every other AppTest case possible."""
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception, f"app raised: {at.exception}"
    assert any("Không kết nối được" in str(e.value) for e in at.error), (
        "an unreachable API must produce a Vietnamese error, not a blank page"
    )


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_with_stubbed_data(page, monkeypatch):
    """Layout smoke. Stubs the client so the pages render real widgets without a
    server; catches the AttributeError class of bug that turns the demo into a
    stack trace."""
    from invoice_agent.ui import client as ui

    monkeypatch.setattr(ui, "health", lambda **kw: {"status": "ok", "database": "ok"})
    monkeypatch.setattr(ui, "departments", lambda **kw: ["ADMIN", "MKT"])
    monkeypatch.setattr(
        ui, "pending_approvals",
        lambda **kw: [{"invoice_id": "11111111-1111-1111-1111-111111111111",
                       "approval_id": "2", "thread_id": "appr:1",
                       "requested_at": "2026-08-01T00:00:00+00:00"}],
    )
    monkeypatch.setattr(
        ui, "approval_detail",
        lambda _id, **kw: {"invoice_id": _id, "approval_id": "2", "decision": None,
                           "interrupt_payload": {"invoice": {"invoice_number": "1"},
                                                 "flags": [], "suggestions": []},
                           "suggestions": {}},
    )
    monkeypatch.setattr(
        ui, "get_invoice",
        lambda _id, **kw: {"invoice": {"id": _id}, "lines": [], "flags": []},
    )
    monkeypatch.setattr(ui, "budget_report", lambda **kw: [{"department_code": "ADMIN"}])
    monkeypatch.setattr(ui, "flag_report", lambda **kw: [])
    monkeypatch.setattr(ui, "posting_report", lambda **kw: [])
    monkeypatch.setattr(ui, "cost_report", lambda **kw: {"calls": 0})

    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["page"] = page
    at.run()
    assert not at.exception, f"page {page!r} raised: {at.exception}"
```

- [ ] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_ui_app.py -v
```

Expected: FAIL — `app.py` does not exist.

- [ ] **Step 3: Implement**

Create `src/invoice_agent/ui/app.py`. **All on-screen copy is Vietnamese**
(global constraint 4); comments and docstrings stay English.

```python
"""The Streamlit approval UI — layout only.

NO HTTP HERE. Every call goes through `ui/client.py`, because Streamlit reruns
this script top to bottom on every interaction and nothing in it is reachable by
a unit test. `tests/test_ui_boundary.py` asserts this file contains no `httpx`.

On-screen copy is Vietnamese: a Vietnamese accountant reads it, which CLAUDE.md
puts on the product side of the authoring-language rule.
"""

import time
from typing import Any

import streamlit as st

from invoice_agent.ui import client as api

PAGES = ["Tải lên hóa đơn", "Hàng chờ duyệt", "Chi tiết & quyết định", "Báo cáo"]


def _guard(call: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a client call, or render a Vietnamese error and return None.

    Catches `httpx.HTTPError` as well as `ApiError` because the API being DOWN is
    the most likely failure a reviewer meets, and it is also the state
    `AppTest` runs in — so this is what makes the smoke test able to assert
    something other than "it crashed".
    """
    import httpx

    try:
        return call(*args, **kwargs)
    except api.ApiError as exc:
        st.error(f"API trả về lỗi {exc.status_code}: {exc.detail}")
    except httpx.HTTPError:
        st.error("Không kết nối được tới API. Hãy chạy `make demo` hoặc `make api`.")
    return None


def page_upload() -> None:
    st.header("Tải lên hóa đơn")
    depts = _guard(api.departments) or []
    department = st.selectbox("Phòng ban (để trống thì không đối chiếu ngân sách)", ["—", *depts])
    uploaded = st.file_uploader("Chọn file XML hoặc PDF", accept_multiple_files=True)

    if st.button("Xử lý") and uploaded:
        payload = [(f.name, f.getvalue()) for f in uploaded]
        started = _guard(
            api.upload_batch, payload, department=None if department == "—" else department
        )
        if started is None:
            return
        with st.status("Đang xử lý…", expanded=True) as box:
            summary: dict[str, Any] = {}
            for _ in range(120):  # 60s at 0.5s — a live batch measured 20.4s at M5-B
                summary = _guard(api.get_batch, started["batch_id"]) or {}
                if not summary:
                    return
                st.write(
                    f"đã lưu {summary['persisted']} · đã hạch toán {summary['posted']}"
                    f" · chờ duyệt {summary['pending_approval']}"
                )
                if summary.get("status") in ("completed", "failed"):
                    break
                time.sleep(0.5)
            box.update(label=f"Xong — trạng thái {summary.get('status')}", state="complete")


def page_queue() -> None:
    st.header("Hàng chờ duyệt")
    rows = _guard(api.pending_approvals)
    if rows is None:
        return
    if not rows:
        st.info("Không có hóa đơn nào đang chờ duyệt.")
        return
    st.dataframe(rows, use_container_width=True)
    st.caption("Sao chép `invoice_id` sang tab “Chi tiết & quyết định”.")


def page_detail() -> None:
    st.header("Chi tiết & quyết định")
    invoice_id = st.text_input("invoice_id")
    if not invoice_id:
        st.info("Nhập `invoice_id` từ hàng chờ duyệt.")
        return

    detail = _guard(api.approval_detail, invoice_id)
    if detail is None:
        return
    if detail.get("decision") is not None:
        st.warning(f"Hóa đơn này đã được quyết định: {detail['decision']}.")

    payload = detail.get("interrupt_payload") or {}
    st.subheader("Hóa đơn")
    st.json(payload.get("invoice", {}))
    st.subheader("Cảnh báo")
    st.dataframe(payload.get("flags") or [], use_container_width=True)
    st.subheader("Gợi ý từ lần duyệt trước")
    st.dataframe(payload.get("suggestions") or [], use_container_width=True)

    with st.form("quyet_dinh"):
        decision = st.radio(
            "Quyết định", ["approved", "rejected", "edited"],
            format_func=lambda v: {"approved": "Duyệt", "rejected": "Từ chối",
                                   "edited": "Duyệt có sửa"}[v],
        )
        # Free text because there is no auth — design §11.3 cut it, and the API
        # records what the client claims. Said on screen rather than defaulted.
        decided_by = st.text_input("Người duyệt (chưa có đăng nhập — tự khai)")
        reason = st.text_area("Lý do")
        if st.form_submit_button("Gửi quyết định"):
            if not decided_by:
                st.error("Phải nhập tên người duyệt.")
                return
            result = _guard(
                api.submit_decision, invoice_id, decision=decision,
                decided_by=decided_by, reason=reason or None,
            )
            if result is not None:
                st.success(f"Đã ghi nhận: {result['decision']} · bút toán {result.get('entry_id')}")


def page_reports() -> None:
    st.header("Báo cáo")
    st.subheader("Ngân sách")
    st.dataframe(_guard(api.budget_report) or [], use_container_width=True)
    st.subheader("Cảnh báo theo mã")
    st.dataframe(_guard(api.flag_report) or [], use_container_width=True)
    st.subheader("Hạch toán tự động vs người duyệt")
    st.dataframe(_guard(api.posting_report) or [], use_container_width=True)
    st.subheader("Chi phí LLM")
    st.json(_guard(api.cost_report) or {})


def main() -> None:
    st.set_page_config(page_title="Trợ lý hóa đơn", layout="wide")
    st.sidebar.title("Trợ lý hóa đơn")
    page = st.sidebar.radio("Trang", PAGES, key="page")
    {
        PAGES[0]: page_upload,
        PAGES[1]: page_queue,
        PAGES[2]: page_detail,
        PAGES[3]: page_reports,
    }[page]()


main()
```

- [ ] **Step 4: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_ui_app.py -v
```

Expected: all 5 PASS. If `AppTest` cannot drive `session_state["page"]` before
the first run in the installed version, set the page with
`at.sidebar.radio[0].set_value(page).run()` instead — and record which form
worked, with the version, in the test's docstring.

- [ ] **Step 5: `make check` and commit**

```bash
make check
git add src/invoice_agent/ui/app.py tests/test_ui_app.py
git commit -m "feat(m5d): add the four-page Streamlit app

Every page degrades to a Vietnamese message when the API is unreachable. That is
the correct reviewer experience AND what makes the AppTest smoke test able to
assert something other than 'it crashed' — AppTest runs the script with no server
listening.

AppTest is the only test that can catch an AttributeError in app.py, which no
client test reaches and which turns the milestone's artefact into a stack trace.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 5: `tests/test_ui_boundary.py` — the two structural guards

**Files:**
- Create: `tests/test_ui_boundary.py`

**Interfaces:**
- Consumes: nothing at runtime — reads source
- Produces: the guards Task 6's mutations turn red

- [ ] **Step 1: Write the tests**

```python
"""Structural guards. Neither is satisfiable by behaviour.

A UI that imported `submit_decision` directly would produce identical
screenshots and identical database state — only an import check can see that
M5-C's API was bypassed.
"""

import pathlib

UI = pathlib.Path("src/invoice_agent/ui")

# Everything the UI must reach over HTTP instead. `invoice_agent.config` is
# absent on purpose: it is the ONE allowed import (spec §2.1), because reading an
# env var directly would reintroduce the scattered-os.getenv debt M0.2 paid off.
FORBIDDEN = (
    "invoice_agent.db",
    "invoice_agent.graphs",
    "invoice_agent.tools",
    "invoice_agent.domain",
    "invoice_agent.llm",
    "invoice_agent.memory",
    "invoice_agent.extraction",
)


def test_ui_talks_only_over_http():
    """THE POINT OF THIS MILESTONE. Streamlit is Python, so importing
    `submit_decision` directly would work and would be shorter — and M5-C's only
    consumer would bypass the API, leaving it dead code with a green suite."""
    offenders = []
    for path in UI.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for module in FORBIDDEN:
            if module in source:
                offenders.append(f"{path.name}: imports {module}")
    assert not offenders, (
        "the UI must reach the system over HTTP only: " + "; ".join(offenders)
    )


def test_app_makes_no_http_call_of_its_own():
    """Streamlit reruns this script on every interaction, so anything in it is
    unreachable by a unit test. Keeping HTTP in client.py is what makes every
    request path testable; without this guard the logic leaks back one call at a
    time and the split becomes decorative."""
    source = (UI / "app.py").read_text(encoding="utf-8")
    for forbidden in ("httpx.", "requests.", "urllib"):
        assert forbidden not in source, f"app.py makes its own HTTP call: {forbidden!r}"


def test_the_ui_imports_config_and_nothing_else_from_the_package():
    """The allowlist stated positively, so a NEW forbidden module added to
    `invoice_agent/` later is caught even though FORBIDDEN above does not list
    it."""
    import re

    allowed = {"invoice_agent.config", "invoice_agent.ui"}
    found: set[str] = set()
    for path in UI.rglob("*.py"):
        for match in re.finditer(
            r"(?:from|import)\s+(invoice_agent(?:\.[a-z_]+)*)", path.read_text(encoding="utf-8")
        ):
            found.add(match.group(1))
    extra = {m for m in found if not any(m.startswith(a) for a in allowed)}
    assert not extra, f"the UI may import only {sorted(allowed)}; found {sorted(extra)}"
```

- [ ] **Step 2: Run — these should pass immediately**

```bash
.venv/bin/python -m pytest tests/test_ui_boundary.py -v
```

Expected: all PASS. If one fails, a real boundary is already broken — fix the
source, never the assertion.

- [ ] **Step 3: `make check` and commit**

```bash
make check
git add tests/test_ui_boundary.py
git commit -m "test(m5d): guard the HTTP-only boundary and app.py's no-HTTP rule

Neither is satisfiable by behaviour: a UI that imported submit_decision directly
would produce identical screenshots and identical database state. Only an import
check can see that the API was bypassed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 6: Mutation verification

**Files:**
- Create: `docs/notes/2026-08-01-m5d-streamlit.md` (started here)

- [ ] **Step 1: Mutation 1 — the UI reaches past the API**

Add to `src/invoice_agent/ui/client.py`:

```python
from invoice_agent.db import models  # MUTATION
```

Run:

```bash
.venv/bin/python -m pytest tests/test_ui_boundary.py -v
.venv/bin/python -m pytest tests/test_ui_client.py -q
```

Expected: both boundary tests **RED**; record whether `test_ui_client.py` stays
**GREEN** — that asymmetry is why the guard reads source. Revert.

- [ ] **Step 2: Mutation 2 — `app.py` calls HTTP directly**

In `page_queue`, replace `_guard(api.pending_approvals)` with
`httpx.get("http://127.0.0.1:8000/approvals").json()` and add `import httpx`. Run:

```bash
.venv/bin/python -m pytest tests/test_ui_boundary.py -k no_http_call -v
```

Expected: **RED**. Revert.

- [ ] **Step 3: Mutation 3 — the client swallows a non-2xx**

In `_unwrap`, replace the body with `return response.json()`. Run:

```bash
.venv/bin/python -m pytest tests/test_ui_client.py -k "409 or ApiError or validation" -v
```

Expected: **RED**. This is the mutation that matters: without it the UI renders
`đã duyệt` while nothing was posted. Revert.

- [ ] **Step 4: Confirm clean and write the table**

```bash
git status --short   # must be empty
make check
```

Create `docs/notes/2026-08-01-m5d-streamlit.md` with a `## Mutation results`
section recording all three with their **exact assertion messages**, as M5-C's
note did — and mutation 1's asymmetry.

- [ ] **Step 5: Commit**

```bash
git add docs/notes/2026-08-01-m5d-streamlit.md
git commit -m "docs(m5d): record the three-mutation verification

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
```

---

### Task 7: `make demo`, the note, the roadmap, and closing M5

- [ ] **Step 1: Add `make demo`**

```make
demo:
	@echo "API  → http://127.0.0.1:8000"
	@echo "UI   → http://127.0.0.1:8501"
	.venv/bin/uvicorn invoice_agent.api.app:app --port 8000 & \
	.venv/bin/streamlit run src/invoice_agent/ui/app.py --server.port 8501 --server.headless true
```

Add `demo` to `.PHONY` and to the `help` target:
`@echo "  make demo             - chạy API + Streamlit UI cùng lúc"`.

- [ ] **Step 2: Prove both processes answer**

```bash
docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -tAc "TRUNCATE app.invoices CASCADE;"
make demo &
sleep 12
curl -sI http://127.0.0.1:8000/health | head -1
curl -sI http://127.0.0.1:8501/       | head -1
curl -s  http://127.0.0.1:8000/health
```

Expected: `HTTP/1.1 200 OK` from both ports. Paste into the note. Stop both.

**This is the gate's evidence, and it is textual on purpose** — an implementing
agent cannot take a screenshot, so a screenshot criterion would be unmeetable and
therefore quietly skipped (spec §8.4). Screenshots for the README and M8's video
remain a human step.

- [ ] **Step 3: Record the AppTest widget enumeration**

```bash
.venv/bin/python -m pytest tests/test_ui_app.py -v 2>&1 | tail -12
```

Paste the five passing case names into the note as criterion 5b's evidence.

- [ ] **Step 4: Measure what Streamlit cost**

`streamlit` is a main dependency now (spec §5.3), and §7 says to measure it:

```bash
wc -l requirements.lock          # compare against the pre-M5-D count in git
gh run list --branch main --limit 5   # CI wall time before vs after
```

Record both in the note. If the lock grew a lot, that is the honest cost of the
decision, not a reason to hide it.

- [ ] **Step 5: Finish the note**

`docs/notes/2026-08-01-m5d-streamlit.md` must carry:

- the three-mutation table from Task 6, with exact assertion messages
- the `curl -sI` output from step 2 and the `AppTest` case list from step 3
- the `requirements.lock` growth and CI wall-time delta from step 4
- **the SSE statement**: M5-C shipped `GET /batches/{id}/events` and M5-D does
  not consume it, because a blocking stream freezes Streamlit's rerun loop and
  there is no `Last-Event-ID` resumption. Its consumer is M7. An endpoint whose
  only client never calls it is a fact about the system, not an embarrassment
- spec §8's five open questions
- the statement that the database was truncated before any measurement

- [ ] **Step 6: Update the roadmap — and close M5**

In `docs/Plan_Project_F_2026-07-25.md`:

- mark item **5.7** done in the §3 M5 table
- add the M5-D line to the §3 sub-milestone block and mark the block complete
- **change the §7 M5 row from 🟡 to ✅** with the completion date — M5-D is the
  last of the four, so this closes M5
- record the M5 gate results against §3's stated gate: `test_never_post_without_approval.py`
  (M5-A), `test_batch_not_blocked_by_pending.py` (M5-B), curl approval (M5-C),
  `kill -9` + `recover_pending` (M5-B), reviewer feedback in
  `vendors.reviewer_notes` (M5-B), and the working Streamlit demo (M5-D)
- update §8's portfolio-test table if any row's status changed

- [ ] **Step 7: `make check`, push, confirm CI**

```bash
make check
git add -A
git commit -m "docs(m5d): close M5 on a working demo and measured mutations

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016trAebLxBJCuoFxsdQCQWW"
git push
gh run watch
```

**M5-D is not closed until CI is green on this exact SHA and no test SKIPs that
did not skip before.** Watch for `test_ui_app.py` skipping — `pytest.importorskip`
turns a missing Streamlit into a silent pass, and Task 1 step 1 exists to stop
that. If it skips in CI, the dependency did not install and the smoke test is
measuring nothing.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §2.1 HTTP-only boundary | 1 (config), 5 (guard), 6 (mutation 1) |
| §2.2 no HTTP in `app.py` | 4, 5 (guard), 6 (mutation 2) |
| §2.3 `ApiError`, no silent success | 1, 2, 6 (mutation 3) |
| §2.4 injectable client | 1 |
| §3 four pages | 4 |
| §3.1 departments from the budget report | 3 |
| §3.2 polling, not SSE | 4 (`page_upload`), 7 (recorded in the note) |
| §4 three test files | 1–3 → `test_ui_client.py`, 4 → `test_ui_app.py`, 5 → `test_ui_boundary.py` |
| §4.1 three mutations | 6 |
| §5.1 scope incl. `make demo` | 1 (dep, config), 7 (demo) |
| §5.3 streamlit is a main dependency | 1, measured in 7 |
| §6 criteria 1–6 | 6 (1), 2 (2), 4 (3), 5 (4), 7 (5, 5b, 6) |

No gaps.

**Type consistency:** every `client.py` function keeps `*, client: httpx.Client | None = None` across Tasks 1–3, and `app.py` calls them by the same names. `ApiError.status_code`/`.detail` are set in Task 1 and read in Tasks 2 and 4. `PAGES` is defined once in `app.py` and imported by name in `test_ui_app.py`.

**One requirement the spec did not state, added here:** `app.py` must degrade to a Vietnamese message when the API is unreachable (Task 4). Without it `AppTest` — which runs with no server listening — could only ever assert that the app crashed, and the smoke test would be worthless. It is also the correct reviewer experience.

**A gap found in this plan's own review:** the test code called
`_persisted_invoice` and `_initial_state` without importing them. `committed_rows`
arrives as a fixture through `conftest.pytest_plugins`, but those two are plain
functions and would have raised `NameError`. The import is now in Task 1's file
header, covering Tasks 2 and 3 as well since they append to the same file.

**Two things the implementer must verify rather than assume:**

- That the installed Streamlit exposes `streamlit.testing.v1.AppTest` (Task 1 step 1). Streamlit is not currently installed, so the ≥1.28 floor comes from documentation, not from this repo.
- How `AppTest` selects a sidebar page in the installed version (Task 4 step 4). Two forms are given; record which one worked and the version.

**Known interaction:** Task 1 adds a dependency, so `make check` fails until `uv pip install -e ".[dev]"` has been re-run.
