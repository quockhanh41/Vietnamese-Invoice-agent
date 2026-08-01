# Spec — M5-D: the Streamlit approval UI (plan item 5.7)

> **Date**: 2026-08-01 · **Milestone**: M5, fourth and last spec
> **Reads with**: [`Plan_Project_F_2026-07-25.md`](../../Plan_Project_F_2026-07-25.md) §0, §3 M5,
> [`project-f-system-design.md`](../../project-f-system-design.md) §11.2, §11.3
> **Preceded by**: M5-A write guard · M5-B approval graph · M5-C the HTTP API
> **Closes**: M5

---

## 0. Global constraints

1. **The UI reaches the system only over HTTP** (§2.1). No module under `ui/`
   may import `invoice_agent.db`, `.graphs`, `.tools`, `.domain`, `.llm`,
   `.memory` or `.extraction`. Exactly one exception: `invoice_agent.config`.
2. **`app.py` contains no HTTP** (§2.2). Every call goes through `client.py`,
   so every call is testable.
3. **A non-2xx never becomes a silent success** (§2.3). `client.py` raises
   `ApiError`; a UI that renders "đã duyệt" while nothing posted is the same
   class of defect as a guard swallowing its own denial.
4. **UI copy is Vietnamese.** CLAUDE.md's language rule puts anything a person
   reads on the product side. Code, comments and commits stay English.
5. **No new backend behaviour.** If the UI needs an endpoint that does not
   exist, that is a finding, not a task. Nothing under `src/invoice_agent/`
   outside `ui/` changes, except one `config.py` setting.
6. **A milestone is not closed until `make check` is green on the PUSHED commit
   and CI agrees.** Six milestones have failed this; M5-C was the first to
   pass it unprompted.

---

## 1. Why this exists

Roadmap §0 fixed the UI decision on 2026-07-25: *"Streamlit ở M5 (bảo hiểm) →
Next.js ở M7 (nâng cấp) … Luôn có demo chạy được từ M5."* This is the insurance
policy. From here the project always has a demo that does not depend on M7's
time-boxed Next.js work landing.

It is also what turns M5-C from an assertion into a demonstration. An API whose
only consumer bypasses it is dead code with a passing test suite.

---

## 2. Architecture

```
src/invoice_agent/ui/
    __init__.py
    client.py    every HTTP call and all the logic — fully testable
    app.py       Streamlit layout only
```

**Inside the package, not a top-level `ui/`**: mypy's `packages = ["invoice_agent"]`
and ruff's `src` target already cover it, so `make check` includes the UI with no
config change. The boundary is not the directory — it is §2.1's test, which fails
loudly where a directory convention would not.

### 2.1 The UI reaches the system only over HTTP

`test_ui_talks_only_over_http` asserts no module under `src/invoice_agent/ui/`
imports `invoice_agent.db`, `.graphs`, `.tools`, `.domain`, `.llm`, `.memory` or
`.extraction`.

**One exception, deliberately: `invoice_agent.config`.** Reading
`INVOICE_API_URL` straight from the environment would make the rule absolute, but
it reintroduces the scattered-`os.getenv` debt M0.2 paid off, and §11.2's "một
`config.py`" is a stated principle. So `api_base_url: str = "http://127.0.0.1:8000"`
joins `config.py` and the guard allowlists exactly that import.

Without this test, the tempting shortcut — Streamlit is Python, just call
`submit_decision` — would leave M5-C's only consumer bypassing it. The API would
still pass all its tests and would still be dead.

### 2.2 `app.py` contains no HTTP

`test_app_makes_no_http_call_of_its_own` asserts `app.py` does not contain
`httpx` or `requests`.

Streamlit reruns its script top to bottom on every interaction, so anything in
`app.py` is hard to unit test. Keeping HTTP out of it means every request/response
path lives in `client.py`, where §3's tests reach it. Without the guard, logic
leaks into the untestable file one call at a time and the split becomes
decorative.

### 2.3 Errors become Vietnamese messages, not tracebacks

```python
class ApiError(Exception):
    status_code: int
    detail: str
```

`client.py` raises it on any non-2xx; `app.py` maps it to `st.error` with
Vietnamese copy. The **409** case is the realistic one — two reviewers opening
the same invoice — and *"Hóa đơn này đã được quyết định rồi."* is the correct
outcome, not a stack trace in the browser.

The mutation that matters (§4.1) is a `client.py` that returns the error body
instead of raising: the UI would render *"đã duyệt"* while **nothing was
posted**. That is the UI-layer analogue of a guard swallowing its own denial, and
no screenshot would reveal it.

### 2.4 `client.py` is testable without a running server

Every function takes an optional client:

```python
def submit_decision(
    invoice_id: str, *, decision: str, decided_by: str,
    reason: str | None = None, corrections: dict[str, Any] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]
```

Tests pass `TestClient(create_app())` — which **is** an `httpx.Client` over an
ASGI transport — so the whole request/response path runs in-process with no
uvicorn and no port. Production passes nothing and gets a real client against
`api_base_url`.

---

## 3. Pages

Sidebar navigation, four pages. **All copy Vietnamese** (constraint 4).

| Page | Endpoints | Contents |
|---|---|---|
| **Tải lên hóa đơn** | `POST /batches`, then poll `GET /batches/{id}` | multi-file uploader, department selector, progress inside `st.status` |
| **Hàng chờ duyệt** | `GET /approvals` | the pending queue |
| **Chi tiết & quyết định** | `GET /approvals/{id}`, `GET /invoices/{id}`, `POST …/decision` | snapshot, flags, suggestions, decision form |
| **Báo cáo** | the four `/reports/*` | four tables |

### 3.1 The department selector reuses a report rather than duplicating the seed

The selector needs the five seeded departments and there is no `/departments`
endpoint. Hardcoding `["MKT", "ADMIN", "OPS", "IT", "SALES"]` would create a
second source of truth for seed data — precisely what `scripts/seed_reference.py`'s
module docstring forbids for `BUDGETS` ("*hai bản sao của limit là cách test bắt
đầu pass vì lý do sai*").

`GET /reports/budgets` already returns `department_code` per row, so the selector
populates from it. No new endpoint, no duplicated list, and adding a department
to the seed changes the UI without touching the UI.

### 3.2 Progress polls; it does not consume SSE

The upload page polls `GET /batches/{id}` inside `st.status` until `status` is
`completed`, showing persisted / posted / pending counts.

**M5-D does not consume `GET /batches/{id}/events`.** A blocking stream inside a
Streamlit rerun freezes the widget loop, and there is no `Last-Event-ID`
resumption (M5-C spec §8). So M5-C shipped an endpoint whose only client never
calls it; its consumer is M7's Next.js. Recorded here rather than left for
someone to notice — an unused endpoint is a fact about the system, not an
embarrassment to hide.

Polling the DB-truth endpoint also exercises the one that survives a restart,
which is the property M5-C's mutation 5 was added to protect.

---

## 4. Testing

- **`tests/test_ui_client.py`** — every client function against
  `TestClient(create_app())`: upload, batch summary, queue, detail, decision,
  invoice, all four reports. Including a decision posting exactly one entry, a
  second decision raising `ApiError(409)`, and a bad decision string raising
  `ApiError(422)`.
- **`tests/test_ui_boundary.py`** — §2.1 and §2.2's guards.
- **One `AppTest` smoke test** — every page renders without exception.

`AppTest` is the most fragile thing in this milestone and it is worth saying so.
It is version-sensitive and it re-executes the script in a harness that is not a
browser, which is why §5.1 pins `streamlit>=1.28` (where `AppTest` appeared) and
the plan verifies the import before relying on it. It earns its place anyway: it
is the only test that catches an `AttributeError` in `app.py`, which no client
test can reach and which turns the demo — the milestone's actual artefact — into
a stack trace.

### 4.1 Mutations

| Mutation | Turns red |
|---|---|
| `client.py` gains `from invoice_agent.db import models` | `test_ui_talks_only_over_http` |
| `app.py` calls `httpx.get(...)` directly | `test_app_makes_no_http_call_of_its_own` |
| `client.submit_decision` returns `response.json()` without checking status | `test_a_409_becomes_an_ApiError_not_a_silent_success` |

Three, each re-broken from scratch and re-run, with the **measured** result
recorded — including the exact assertion message, as M5-C's note did.

---

## 5. Scope

### 5.1 In

- `src/invoice_agent/ui/{__init__,client,app}.py`
- `streamlit>=1.28` in **main** dependencies (§5.3), `requirements.lock` regenerated.
  The floor is not arbitrary: `st.testing.v1.AppTest` — the only thing that can
  catch an `AttributeError` in `app.py` — was introduced in 1.28. The plan
  verifies the installed version exposes it before relying on it
- `api_base_url` in `config.py` — the only change outside `ui/`
- `make demo` starting uvicorn and Streamlit together
- The three test files above
- `docs/notes/2026-08-01-m5d-streamlit.md`

### 5.2 Out

- **Next.js** — M7 · **auth** — §11.3 · **ReportingAgent narrative** — M8.1
- **SSE consumption** — §3.2
- Any backend change beyond `api_base_url`. If the UI wants an endpoint that
  does not exist, record it as a finding (constraint 5).

### 5.3 `streamlit` is a main dependency, not a dev one

Roadmap §0: *"Luôn có demo chạy được từ M5."* The demo is the deliverable, not a
development tool, so it ships with the package. The cost is a large dependency
tree that CI now installs on every run, and a materially bigger
`requirements.lock`; §7 records it.

---

## 6. Success criteria

| # | Criterion | Evidence |
|---|---|---|
| 1 | 3/3 mutations red, re-broken from scratch, with assertion messages | mutation table in the note |
| 2 | A decision made through `client.py` posts exactly one journal entry; the second attempt is a 409 | test |
| 3 | Every page renders without exception | `AppTest` |
| 4 | The UI imports nothing from `invoice_agent` except `config` | test |
| 5 | `make demo` starts both processes and both ports answer | note: the command output plus `curl -sI` against 8000 and 8501 |
| 5b | Every page's rendered widgets enumerated | note: `AppTest` output listing the elements each page produced |
| 6 | `make check` green on the **pushed** commit, CI agreeing, no new SKIPs | run id |

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| The UI quietly bypasses the API | §2.1's guard, mutation-verified. This is the milestone's whole point |
| A failed decision renders as success | §2.3's `ApiError` and §4.1's third mutation |
| `AppTest` is version-sensitive and breaks on a Streamlit bump | Pin a floor; if it becomes unmaintainable, delete it and say so rather than leaving it skipped. A skipped test reports as a pass |
| Streamlit's dependency tree slows CI and bloats the lock | Accepted (§5.3) and measured in the note: CI wall time before and after |
| The demo needs two processes and someone starts one | `make demo` starts both |

---

## 8. Open questions — recorded, not silently dropped

1. **The queue has no pagination.** `GET /approvals` returns every pending row
   and the page renders all of them. Correct at 30 invoices, wrong at 3,000;
   the endpoint would need `limit`/`offset` first, which is a backend change and
   therefore out of scope here.
2. **`decided_by` is a free-text box.** There is no auth (§11.3), so the UI asks
   the reviewer to type their own name and the API records what it is told.
   Stated on screen, not hidden behind a default.
3. **Corrections are entered as raw JSON.** `field_corrections` is a free-form
   `dict`, and M5-B's open question 2 records that nothing applies them to the
   invoice — they only reach `vendors.reviewer_notes`. A structured editor would
   imply they take effect, which would be a lie until that question is answered.
4. **The gate's evidence is textual, not visual.** An implementing agent cannot
   take a screenshot, so demanding one would make the gate unmeetable and
   therefore skippable — the shape of criterion that gets quietly dropped.
   Criterion 5 asks instead for the `make demo` output plus a `curl -sI` against
   both ports, and 5b for `AppTest`'s enumeration of the widgets each page
   rendered. Both are machine-producible and both fail loudly. Screenshots for
   the README and M8's video remain a human step, and are not gated here.
5. **Inherited and still open**: `invoices.status` has no state machine (M5-A
   §8.3); `/health` does not check the checkpointer schema (M5-C §8.4).
