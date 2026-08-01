# Spec — M5-C: the HTTP API (plan item 5.6)

> **Date**: 2026-08-01 · **Milestone**: M5, third of four specs
> **Reads with**: [`Plan_Project_F_2026-07-25.md`](../../Plan_Project_F_2026-07-25.md) §3 M5,
> [`project-f-system-design.md`](../../project-f-system-design.md) §2.10, §5.5, §9.2, §11.2, §11.3
> **Preceded by**: M5-A write guard + auto path · M5-B approval graph + HITL loop
> **Followed by**: M5-D Streamlit

---

## 0. Global constraints

At the top because five milestones running lost their rationales to summarisation
while keeping their procedures.

1. **The API adds no logic, and no route contains SQL.** Every route is a thin
   adapter over a function in the data or graph layer — `submit_decision`, a
   repository, `db/queries.py` (§2.5), the batch graph. A route that computes
   something is a route that can disagree with the thing it wraps.
2. **The ledger has exactly one door.** No route module may import
   `tools.guards` or `db.repositories.journal`. Posting reaches
   `journal_entries` only through `submit_decision → post_entry → the guard`
   (§2.2).
3. **The request schema is the security boundary** (§2.3), exactly as agent
   response models are for prompt injection (design §9.2). A client cannot
   self-authorize because the fields do not exist.
4. **Progress is ephemeral; the database is truth.** The SSE registry lives in
   process memory and dies on restart, by design §5.5's rule that the API never
   assumes an in-RAM registry survived (§2.4).
5. **Sync all the way down** (§2.1). Design §2.10's `AsyncConnectionPool` line is
   deliberately not followed, and §2.1 records why.
6. **No auth.** §11.3 cut it on purpose; the reviewer is a string in
   `approvals.decided_by`. Stated so it reads as a decision, not a hole.
7. **A milestone is not closed until `make check` is green on the PUSHED commit
   and CI agrees.** M5-B was declared COMPLETE with twelve commits unpushed and
   CI never run. Run `make check` after every task; push before claiming
   anything.

---

## 1. Why this exists

Items 5.6 and the inherited debt in §11.2 — *"`main.py` 643 dòng, `routes/` rỗng
→ tách routes thật."* Project A's API was one 643-line module beside an empty
`routes/` package. This one is split from the first commit.

It also turns M5-B's `submit_decision` into a **transport-agnostic** seam that is
demonstrated rather than asserted: the same function is already driven by a test
and a script; M5-C adds HTTP, and M7 adds MCP. Nothing about the approval
mechanism changes to accommodate any of them, which is the point.

---

## 2. Architecture

```
src/invoice_agent/api/
    __init__.py
    app.py          create_app(), router registration, the GuardDenied handler
    schemas.py      request/response models
    progress.py     in-process per-batch progress queues
    routes/
        __init__.py  health.py  batches.py  invoices.py  approvals.py  reports.py
```

### 2.1 Sync routes, and why design §2.10 is not followed

Every route is `def`, not `async def`. FastAPI runs sync routes in a threadpool,
so `get_engine()` and every existing repository are reused unchanged.

Design §2.10 says *"Pool cho FastAPI: `AsyncConnectionPool(...)`"*. **That line is
deliberately not implemented**, and the reason is measurable rather than
stylistic: the repository contains **zero** `async def` and zero `asyncio`
imports. Adopting an async pool means a second way to reach Postgres beside the
sync engine, async variants of every repository the API touches, and
`asyncio.to_thread` around each sync LangGraph invoke. That is a large surface to
serve one reviewer and one Streamlit demo.

The cost is stated rather than hidden: sync routes occupy a threadpool worker for
the duration of the call. The longest is `GET /batches/{id}/events`, which holds
one worker per streaming client. For this system's audience that is acceptable;
for real concurrency it would not be, and §8 records it as the condition under
which §2.10 should be revisited.

### 2.2 The ledger has one door

`test_no_route_reaches_the_ledger_directly` asserts no module under
`api/routes/` imports `tools.guards` or `db.repositories.journal`.

This extends `test_only_the_guard_may_call_the_journal_repository` (M5-A) rather
than duplicating it. That test allowlists `guards.py` as the sole caller of the
journal repository; this one stops a route from becoming the second caller by
importing the guard and choosing its own `posting_authority`. Without it, the
HTTP layer is a second door to the ledger and the three-layer safety property
gains a bypass that no existing test would notice.

### 2.3 The request schema is the security boundary

```python
class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected", "edited"]
    decided_by: str = Field(min_length=1)
    reason: str | None = None
    corrections: dict[str, Any] | None = None
```

It carries no `posting_authority`, no `approval_id`, no `status`, no `entry_id`.
The argument is design §9.2's, transposed from models to clients: the defence is
not validation, it is that *"post this as auto_clean"* **is not expressible**.
`extra="forbid"` is the other half — without it an unknown field is silently
accepted and the audit of the field list checks a schema the request is not held
to.

`decision` is `Literal`-constrained here *and* validated inside
`submit_decision`. That is deliberate duplication at a trust boundary, not an
oversight: the route rejects it at the edge with a 422, and the seam stays safe
for its non-HTTP callers.

**`decided_by` arrives in the request body.** There is no authentication — §11.3
cut it on purpose ("*Auth tốn một tuần và không chứng minh kỹ năng nào*"), and
design §4 defines the reviewer as a string in `approvals.decided_by`. A real
deployment would take this from a session; this one records what the client
claims, and the README says so.

### 2.4 Progress is ephemeral, the database is truth

`POST /batches` spools the uploaded files, starts a background thread, and
returns `202 {batch_id}` immediately. The thread drives:

```python
for chunk in graph.stream(initial, config, stream_mode="updates"):
    for node, update in chunk.items():
        registry.publish(batch_id, {"node": node, ...})
```

`GET /batches/{id}/events` drains that queue as SSE. Because the fan-out emits
one update per `Send`, the stream gives per-invoice granularity through
`enrich_one` for free, then one update each for `serial_join`,
`route_outcomes`, `auto_post`, `open_approval` and `finalize`.

**The registry is in process memory and is expected to be lost.** Design §5.5:
the API never assumes an in-RAM registry survived a restart. So `GET
/batches/{id}` reads Postgres — `batches` plus counts from `invoices`,
`journal_entries` and `approvals` — and answers correctly whether or not anyone
was ever streaming.
`test_a_batch_is_queryable_after_the_registry_is_cleared` is the measurement.

A dropped SSE client loses progress and nothing else. There is no
`Last-Event-ID` resumption (§8).

### 2.5 `db/queries.py` — because half these routes have nothing to wrap

Found during this spec's own review, and worth recording rather than quietly
patching: **§0 constraint 1 was unsatisfiable as first written.** `GET /invoices`
needs a list query and the four reports need aggregates, and none of them exists.
`db/repositories/invoices.py` has `get_by_id`, `get_by_natural_key`, `get_lines`
and `set_status` — no list, no filter, no counts.

The tempting shortcut is to put the SQL in the route modules. That makes "the
API adds no logic" false on the first commit, and puts aggregate SQL somewhere no
non-HTTP caller can reuse — M5-D's Streamlit and M8's ReportingAgent both want
the same numbers.

So M5-C adds one new module, `src/invoice_agent/db/queries.py`, holding
**read-only** functions:

```python
list_invoices(conn, *, status=None, department=None, limit=50) -> list[dict]
batch_summary(conn, batch_id) -> dict            # status + persisted/posted/pending/failed
budget_report(conn, period=None) -> list[dict]   # consumed vs limit per department
flag_report(conn) -> list[dict]                  # counts by code x severity
posting_report(conn) -> list[dict]               # auto_clean vs human_approved
cost_report(conn) -> dict                        # cost_usd + shadow_cost_usd
```

Read-only is a rule, not a description: nothing here writes. `test_api_boundary.py`
asserts the module contains no `insert`, `update` or `delete`, so it cannot grow
into a second write path beside the repositories.

Existing repository files are **not** modified — the constraint in §3.2 stands
for them.

---

## 3. Scope

### 3.1 In

- `src/invoice_agent/api/` — the package above, six route modules
- `src/invoice_agent/db/queries.py` — read-only list and aggregate queries (§2.5)
- `python-multipart` added to `pyproject.toml`; FastAPI raises without it on any
  multipart route, and it is **not currently installed**
- Spool directory for uploads, path from `config.py`
- `tests/test_api_batches.py`, `test_api_approvals.py`, `test_api_read.py`,
  `test_api_boundary.py`
- A real `curl` transcript against a running uvicorn, in the closeout note

### 3.2 Out

- **Auth / JWT / RBAC** — §11.3, cut deliberately
- **Streamlit** — M5-D · **ReportingAgent narrative** — M8.1 · **MCP** — M7
- **Token-by-token streaming** — §11.3; per-node SSE is enough
- **Async routes and `AsyncConnectionPool`** — §2.1
- Any change to `graphs/`, `tools/guards.py`, or the **existing** repository
  modules. `db/queries.py` is a new, additive, read-only module (§2.5); touching
  anything already there is a finding, not a task.

---

## 4. Routes

| Method | Path | Wraps | Notes |
|---|---|---|---|
| `GET` | `/health` | — | **Pings Postgres.** A health check that does not touch the database reports healthy while the database is gone, which is worse than no health check |
| `POST` | `/batches` | `build_batch_graph().stream` | multipart; spools; 202 `{batch_id}` |
| `GET` | `/batches/{id}` | `queries.batch_summary` | DB truth: status + persisted/posted/pending/failed counts |
| `GET` | `/batches/{id}/events` | `progress` registry | SSE; ephemeral |
| `GET` | `/invoices` | `queries.list_invoices` | filters: `status`, `department`, `limit` |
| `GET` | `/invoices/{id}` | `invoices`, `flags` | + lines + open flags |
| `GET` | `/approvals` | `approvals.list_pending` | the reviewer queue |
| `GET` | `/approvals/{invoice_id}` | `approvals.get_by_invoice` | the `interrupt_payload` a reviewer reads |
| `POST` | `/approvals/{invoice_id}/decision` | **`approval.submit_decision`** | the gate path |
| `GET` | `/reports/budgets` | `queries.budget_report` | consumed vs limit per department/period |
| `GET` | `/reports/flags` | `queries.flag_report` | counts by code × severity |
| `GET` | `/reports/posting` | `queries.posting_report` | `auto_clean` vs `human_approved` |
| `GET` | `/reports/cost` | `queries.cost_report` | `cost_usd` + `shadow_cost_usd` from `llm_calls` |

Reports are plain SQL in `db/queries.py`, with no model call and none of it in a
route module. M8's ReportingAgent writes commentary
**on top of these numbers** rather than recomputing them, so the narrative and
the dashboard cannot disagree.

### 4.1 Errors, and one deliberate restraint

`GuardDenied` gets an exception handler → **409**. The guard refusing is correct
behaviour; a 500 would read as a bug and bury it in logs.

Everything else raises `HTTPException` at the call site: **404** unknown id,
**422** bad decision, **409** resuming a thread with no pending interrupt.

**There is deliberately no global `ValueError → 422` handler.** It would catch
genuine bugs and serve them as client errors — the API equivalent of a bare
`except`. M5-B's review found a bare `assert` in `serial_join` that killed a
whole batch and was invisible under replay; a broad handler here is the same
mistake with a friendlier status code.

---

## 5. Testing

`fastapi.testclient.TestClient` — `httpx` 0.28.1 and starlette 1.3.1 are already
installed, verified 2026-08-01.

- **`test_api_batches.py`** — upload → batch runs → SSE emits per-node events →
  DB agrees; and `GET /batches/{id}` still correct after the registry is cleared
- **`test_api_approvals.py`** — the gate path: park an invoice, `GET` its
  payload, `POST` a decision, assert exactly one journal entry with
  `posting_authority='human_approved'`
- **`test_api_read.py`** — health including the database-unreachable branch,
  invoices, reports
- **`test_api_boundary.py`** — the two guards in §2.2 and §2.3

### 5.1 Mutations

| Mutation | Turns red |
|---|---|
| a route module imports `tools.guards` and posts directly | `test_no_route_reaches_the_ledger_directly` |
| `DecisionRequest` drops `extra="forbid"` | `test_a_client_cannot_set_posting_authority` |
| `db/queries.py` gains an `UPDATE` | `test_queries_module_is_read_only` |
| `GET /batches/{id}` reads the in-process registry instead of the DB | `test_a_batch_is_queryable_after_the_registry_is_cleared` |

Four, each re-broken from scratch and re-run, with the **measured** result
recorded. M5-B shipped with no mutation table at all; the five were run during
its review instead.

### 5.2 The `curl` gate is not a TestClient test

Roadmap M5's gate says *"duyệt được qua `curl`"*. `TestClient` runs in the same
process and never opens a socket, so it cannot establish that. The note carries a
transcript of real `curl` against a running `uvicorn`: park an invoice, `GET` the
queue, `POST` the decision, `GET` the invoice showing `status=posted`.

---

## 6. Success criteria

| # | Criterion | Evidence |
|---|---|---|
| 1 | Approve an invoice end-to-end over real `curl` against uvicorn | transcript in the note |
| 2 | 4/4 mutations red, re-broken from scratch | mutation table |
| 3 | Upload → SSE emits one event per node → DB agrees with the stream | test |
| 4 | `GET /batches/{id}` correct after the progress registry is cleared | test |
| 5 | `/health` reports unhealthy when Postgres is unreachable | test |
| 6 | No route module imports `tools.guards` or the journal repository | test |
| 7 | `make check` green on the **pushed** commit, CI agreeing, no new SKIPs | run id |

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| A route becomes a second door to the ledger | §2.2's import guard, mutation-verified |
| A client self-authorizes through an extra field | §2.3's `extra="forbid"`, mutation-verified |
| SSE holds a threadpool worker per client | Accepted and recorded (§2.1). Revisit §2.10 if concurrency ever matters |
| Two uploads at once exhaust free-tier quota | Real. M5-B measured **266 rate-limited calls** for one 30-invoice batch at `max_concurrency=4`; quota is already the binding constraint. M5-C adds no queue — recorded in §8 rather than solved |
| Uploaded files carry PII | `.gitignore` already blocks `*.xml`/`*.pdf`/`*.zip` unanchored, so spooled files cannot be committed. This repo has leaked PII twice; the spool directory is also added explicitly |
| The background thread dies silently | The batch's failure lands in `batches.status`; `GET /batches/{id}` reads it. SSE ending without `finalize` is the client-visible symptom |

---

## 8. Open questions — recorded, not silently dropped

1. **No concurrency limit on batch threads.** Two simultaneous uploads run two
   batches sharing one free-tier quota. M5-B measured that quota, not
   concurrency, is the binding constraint, so a limit here would be guessing at
   a number; M6's cost cap (item 6.6) is where it belongs.
2. **Spooled uploads have no retention policy.** Files accumulate. Deleting them
   after a batch would break re-runs against `source_path`; keeping them forever
   is what happens today.
3. **SSE has no `Last-Event-ID` resumption.** A dropped client loses progress but
   never the batch, because the DB is truth. Adding resumption means persisting
   progress, which contradicts §2.4's decision that it is ephemeral.
4. **`/health` does not check the LangGraph checkpointer.** It pings the business
   schema only; a broken `langgraph` schema would report healthy until the first
   approval thread failed.
5. **Inherited from M5-B**: `invoices.status` still has no state machine, and the
   API adds more writers of it indirectly. Two `JournalLine` dataclasses remain.
