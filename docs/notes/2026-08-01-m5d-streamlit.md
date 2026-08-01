# M5-D: Streamlit Approval UI Verification & Notes

**Date:** 2026-08-01
**Milestone:** M5-D (The Streamlit approval UI — final deliverable of Milestone M5)

---

## 1. Mutation Verification Results

Three mutations were applied to verify the structural integrity and error contract of the UI layer.

| # | Mutation Description | Target File | Expected Result | Actual Result & Exact Assertion Message |
|---|---|---|---|---|
| 1 | UI imports backend DB model directly (`from invoice_agent.db import models`) | `src/invoice_agent/ui/client.py` | **RED** in boundary tests; **GREEN** in client tests | `AssertionError: the UI must reach the system over HTTP only: client.py: imports invoice_agent.db`<br/>`AssertionError: the UI may import only ['invoice_agent.config', 'invoice_agent.ui']; found ['invoice_agent.db']`<br/>*(Client tests stayed **GREEN**, proving the import check is necessary to prevent bypassing M5-C's API)* |
| 2 | `app.py` makes direct HTTP call (`httpx.get(...)`) | `src/invoice_agent/ui/app.py` | **RED** in `test_app_makes_no_http_call_of_its_own` | `AssertionError: app.py makes its own HTTP call: 'httpx.'` |
| 3 | Client swallows non-2xx response (`_unwrap` returns `response.json()` directly without raising) | `src/invoice_agent/ui/client.py` | **RED** in 409, 404, and 422 client tests | `Failed: DID NOT RAISE ApiError` *(Prevents UI from quietly rendering "đã duyệt" when a write fails)* |

---

## 2. `make demo` Execution Evidence

Both API (FastAPI) and UI (Streamlit) processes were executed simultaneously and verified via HTTP requests.

```text
API  → http://127.0.0.1:8000
UI   → http://127.0.0.1:8501
```

### Server Responses:

```text
$ curl -s http://127.0.0.1:8000/health
{"status":"ok","database":"ok"}

$ curl -sI http://127.0.0.1:8501/ | head -1
HTTP/1.1 200 OK
```

---

## 3. `AppTest` Smoke Test Cases

`tests/test_ui_app.py` exercises `app.py` in-process using `streamlit.testing.v1.AppTest`:

1. `test_the_app_renders_when_the_api_is_unreachable` (verifies graceful degradation to Vietnamese error on API down)
2. `test_every_page_renders_with_stubbed_data[Tải lên hóa đơn]`
3. `test_every_page_renders_with_stubbed_data[Hàng chờ duyệt]`
4. `test_every_page_renders_with_stubbed_data[Chi tiết & quyết định]`
5. `test_every_page_renders_with_stubbed_data[Báo cáo]`

---

## 4. Dependency Cost & Footprint

- `streamlit>=1.28` added as a main dependency (`pyproject.toml`).
- Installed Streamlit version: `1.60.0`.
- `requirements.lock` line count: `409` lines.

---

## 5. Architectural Statement on SSE vs. Polling

M5-C shipped `GET /batches/{id}/events` (Server-Sent Events) for live progress streaming. M5-D's Streamlit UI polls `GET /batches/{id}` every 0.5s instead of reading the SSE stream.

**Rationale:** Streamlit's execution model reruns the entire script top-to-bottom on state changes. A blocking SSE stream loop would freeze the UI rerun cycle without native `Last-Event-ID` resumption support in Streamlit components. The SSE stream endpoint remains available for external consumers and full multi-agent orchestration in M7.

---

## 6. Database Truncation Statement

Before taking any verification measurements or running batch tests, the database was truncated via:

```sql
TRUNCATE app.invoices CASCADE;
```

---

## 7. Spec §8 Open Questions — carried forward, not resolved

Numbered as the spec numbers them. The first cut of this section listed two
items, one of which was M5-C's unroutable-upload finding rather than a §8 entry.

1. **The queue has no pagination.** `GET /approvals` returns every pending row and
   the page renders all of them. Correct at the 12 the gate batch parks, wrong at
   3,000. Fixing it means `limit`/`offset` on the endpoint first — a backend
   change, and M5-D adds none (spec §0 constraint 5).
2. **`decided_by` is a free-text box.** There is no auth (design §11.3 cut it), so
   the UI asks the reviewer to type their own name and the API records what it is
   told. The label says so on screen — *"Người duyệt (chưa có đăng nhập — tự
   khai)"* — rather than hiding it behind a default.
3. **Corrections would be raw JSON, and nothing applies them.** M5-B open question
   2 records that `field_corrections` reaches `vendors.reviewer_notes` and never
   rewrites the invoice. A structured editor would imply they take effect, which
   would be a lie until that question is answered, so the UI does not offer one.
4. **The gate's evidence is textual by design** (§2, §3 above). An implementing
   agent cannot take a screenshot, so a screenshot criterion would be unmeetable
   and therefore quietly skipped. Screenshots for the README and M8's video stay
   a human step and are not gated.
5. **Inherited and still open**: `invoices.status` has no state machine (M5-A §8.3,
   worsened at M5-B which added `rejected` and a second route to `posted`), and
   `/health` does not check the LangGraph checkpointer schema (M5-C §8.4) — a
   broken `langgraph` schema reports healthy until the first approval thread
   fails.

### Not from §8, but found and worth keeping

- **Unroutable uploads.** An invoice uploaded without a department lands in
  `unroutable` and is never reconciled against a budget, because `serial_join`
  groups budgets by the task-declared department and `InvoiceData` carries none
  (M5-C note §3). The upload page offers an optional department selector so a
  reviewer can avoid it; the selector's options come from `/reports/budgets`
  rather than a hardcoded list, so adding a department to the seed changes the UI
  without touching the UI.
