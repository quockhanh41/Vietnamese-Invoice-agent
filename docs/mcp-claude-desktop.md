# Claude Desktop -> the invoice-agent MCP server

M7 exposes seven read tools, five resources, and one gated write over MCP (`src/invoice_agent/mcp/server.py`,
`build_server()`, generated from `src/invoice_agent/tools/registry.py`). This document is the config that
points Claude Desktop at it, plus a reproducible transcript that answers the M7 gate question --
*"which department went over budget in July?"* -- through a real MCP client, independent of anyone's
Desktop install (see [Reproducing the transcript yourself](#reproducing-the-transcript-yourself) below).

## Prerequisites

- Postgres running and migrated: `make dev` (or, if already running, just `make migrate && make seed`).
- The project installed into `.venv`: `make install`.
- `corpus/` generated if you intend to reload data: `make generate`.

## The `claude_desktop_config.json` block

Claude Desktop's config file is strict JSON -- it has no comment syntax. The block below uses
`//` lines to show, in one place, both the working config and the three approval env vars it
deliberately leaves out. **If you copy this block verbatim, delete every `//` line first** (or
copy only the active part); a `//` line left in the real file fails to parse.

```jsonc
{
  "mcpServers": {
    "invoice-agent": {
      "command": "/Users/quockhanh/Code/learn_AI/LLM/vietnamese-invoice-agent/.venv/bin/python",
      "args": ["-m", "invoice_agent.mcp.server"],
      "env": {
        "DATABASE_URL": "postgresql://invoice:invoice@localhost:5433/invoice_agent",
        "RUNTIME_DATABASE_URL": "postgresql://invoice_app:invoice_app@localhost:5433/invoice_agent"

        // -- The approval write tool. OFF by default (design doc §8.2) --
        // `submit_approval_decision` ships disabled on purpose: a tool that can
        // resolve a pending approval needs a deliberate act to turn on, not a
        // client that merely knows its name. Uncomment all three together --
        // see "Enabling the write tool" below before you do.
        // "MCP_ALLOW_APPROVALS": "true",
        // "MCP_ACTOR_TOKEN": "REPLACE-WITH-A-LONG-RANDOM-SECRET",
        // "MCP_CLIENT_NAME": "claude-desktop"
      }
    }
  }
}
```

Replace the two absolute paths (`command`, and the host/port inside both DSNs if your Postgres
differs from the `docker-compose.yml` default) for your own machine. `args` and the module entry
point (`python -m invoice_agent.mcp.server`, `src/invoice_agent/mcp/server.py:main`) do not change
across machines.

### Why two DSNs, not just `DATABASE_URL`

The brief for this doc names `DATABASE_URL` as the variable to show. Verified against
`src/invoice_agent/config.py` (not assumed) that this is necessary but not sufficient:

- All **seven read tools and all five resources** call `db.engine.get_engine()`, which connects
  with `Settings.runtime_sqlalchemy_url` -- built from `RUNTIME_DATABASE_URL` (`config.py`'s
  `runtime_database_url` field), the least-privilege `invoice_app` role. This is the connection
  that answers `get_budget_status`, `spend_summary`, and everything else in the transcript below.
- The one write tool's checkpoint resume (`submit_approval_decision` ->
  `graphs.approval.submit_decision` -> `checkpointer.build_checkpointer()`) connects with
  `Settings.checkpointer_dsn`, which is built from **`DATABASE_URL`** (`config.py`'s bare
  `database_url` field, the owner role `invoice`) -- `PostgresSaver` needs the owner's
  privileges against the `langgraph` schema.

Setting only `DATABASE_URL` on a non-default Postgres host would silently leave every read tool
pointed at the old default. Both are shown above for that reason. On the stock
`docker-compose.yml` used by this repo's `make dev`, both already default to these exact values,
so neither line is strictly required for the local demo below -- they are spelled out so the block
is self-contained and portable to a different host by editing only these two lines.

## Enabling the write tool

`submit_approval_decision` resumes a pending `approval_graph` interrupt with a human decision
(approved / rejected / edited) -- the same `graphs.approval.submit_decision` seam
`api/routes/approvals.py`'s HTTP route calls, so "interrupt is transport-agnostic" is demonstrable
rather than asserted. Turning it on means:

- `MCP_ALLOW_APPROVALS=true` -- the first gate. Without it the tool always raises
  `NotAuthorized`, regardless of anything else in the call.
- `MCP_ACTOR_TOKEN=<secret>` -- the second gate, checked with `hmac.compare_digest` against
  whatever `actor_token` the caller sends. **This is a shared-secret gate, not an identity or
  authentication system** -- the roadmap (§4 of the design plan) lists auth/RBAC as deliberately
  not built for this project. A token that matches only proves "this caller was configured with
  the secret," not who is typing. Generate a long random value (e.g. `openssl rand -hex 32`); do
  not reuse a password.
- `MCP_CLIENT_NAME` -- not a gate. It is the ONLY identity MCP is allowed to assert: the actor
  recorded on the decision is always `mcp:{MCP_CLIENT_NAME}`, fixed server-side
  (`mcp/approvals.py::submit_approval_decision`) and never taken from the caller's own arguments,
  for the same reason `api/schemas.py`'s `DecisionRequest` never lets a client name its own
  `posting_authority`: the dangerous value must not be expressible by the client, not merely
  validated away.

Enabling this is a decision about who gets to approve invoices from a chat client, which is why
it is off by default and shown commented out above, not a toggle to flip reflexively.

## The tool and resource surface

| Read tool | Returns |
|---|---|
| `search_invoices(status?, department?, vendor?, min_amount?)` | invoices, most recent first |
| `get_invoice_detail(invoice_id)` | fields, line items, open flags |
| `get_budget_status(department?, period?)` | limit, consumed, remaining per budget line |
| `get_vendor_profile(tax_code)` | entity-memory stats (count, avg, stddev) |
| `list_pending_approvals()` | every approval awaiting a human decision |
| `explain_flags(invoice_id)` | Vietnamese narrative + evidence for each open flag |
| `spend_summary(period, group_by)` | invoice count + total spend, grouped by department or category |

| Write tool (gated) | Condition |
|---|---|
| `submit_approval_decision(invoice_id, decision, actor_token, reason?, corrections?)` | `MCP_ALLOW_APPROVALS=true` + a matching `actor_token` |

| Resource | Note |
|---|---|
| `invoice://{invoice_id}` | fields, line items, flags |
| `invoice-xml://{invoice_id}` | **raw source XML, exactly as uploaded -- not redacted.** Serves whatever bytes were stored, unconditionally; a real deployment handling real invoices must gate this itself (noted in the resource's own docstring as a Task 7 follow-up, not solved here). |
| `budget://{department}/{period}` | one budget line |
| `vendor://{tax_code}` | entity-memory stats |
| `report://{period}` | budget lines for every department in that period |

Every tool that can toggle a flag, post a journal entry, or write anywhere outside the one gated
approval decision is absent from this surface on purpose -- `tests/test_no_write_tools_in_mcp.py`
asserts the write allowlist is exactly `{"submit_approval_decision"}`.

## The gate question, answered through a real MCP client

`scripts/mcp_smoke.py` drives `build_server()` through a real `fastmcp.Client` -- the same
machinery `tests/test_mcp_server.py`'s "... over a real MCP client" tests already use -- and calls
`get_budget_status` and `spend_summary` for `2026-07`.

```
.venv/bin/python -m scripts.mcp_smoke
```

confirmed to also work as `python -m scripts.mcp_smoke` run from the repo root with the project's
own `.venv` interpreter (no `scripts/__init__.py` needed -- `scripts/seed_reference.py` and the
`Makefile`'s other targets already rely on the same implicit-namespace-package behaviour).

### Loading data first

A freshly-migrated database has **zero rows in `app.budget_consumption`** -- nothing is
reconciled against a budget yet, so the gate question has no answer until real invoices are run
through the real ingestion pipeline. `tests/conftest.py` also `TRUNCATE`s `app.invoices CASCADE`
at the start of every test session, so `make check` alone never leaves this data in place.

The transcript below was produced by running the real `build_batch_graph()`
(`src/invoice_agent/graphs/batch.py` -- the same graph `POST /batches` drives) over 8 real,
already-committed invoices from `corpus/` (seeded corpus, `make generate --count 300 --seed 42`),
under `LLM_MODE=replay` so classification/audit read `tests/fixtures/llm_cache` instead of calling
a live model -- **no LLM tokens were spent producing it**. Each task declared `department="MKT"`
(the same `department` field `POST /batches`'s form parameter exposes, for exactly this purpose:
budget reconciliation groups by the department **declared at upload time**, never by the LLM's own
classification -- see `api/routes/batches.py`'s own docstring). Full commands, DB row counts
before/after, and why this path was chosen over the alternatives are in
`.superpowers/sdd/2026-08-05-m7-mcp-surface/task-6-report.md`.

Cumulative MKT spend for July 2026 crosses the seeded 50,000,000 limit at the 8th invoice
(`INV-0157`) -- a real `BUDGET_BREACH` from real, deterministically-generated corpus data.

### The transcript

Captured 2026-08-05 with:

```
.venv/bin/python -m scripts.mcp_smoke 2>&1 | tee /tmp/m7-mcp-smoke.txt
```

```
get_budget_status(period='2026-07'):
  ADMIN  limit=    30000000 consumed=           0 remaining=     30000000
  IT     limit=    60000000 consumed=           0 remaining=     60000000
  MKT    limit=    50000000 consumed=    56525000 remaining=     -6525000  <-- OVER BUDGET
  OPS    limit=    80000000 consumed=           0 remaining=     80000000
  SALES  limit=    40000000 consumed=           0 remaining=     40000000

spend_summary(period='2026-07', group_by='department') -- for context only, NOT the answer (see module docstring: unreconciled invoices count here):
  ADMIN  invoices=2    total=5795000
  IT     invoices=8    total=74935000
  MKT    invoices=12   total=171525000
  OPS    invoices=13   total=136495000

ANSWER: MKT went over budget in 2026-07.
  MKT consumed 56525000 against a 50000000 limit -- over by 6525000.
```

`spend_summary`'s MKT total (171,525,000) is **larger** than `get_budget_status`'s consumed
(56,525,000) and that is expected, not a bug: `spend_summary` sums every persisted invoice's
`total_amount` for the period regardless of whether it was ever reconciled against a budget. This
database has 4 MKT invoices with no `budget_consumption` row (verified with a `LEFT JOIN` against
`app.budget_consumption` in the Task 6 report) -- 3 from an earlier, separate load that had no
department declared at upload time, and 1 (`INV-0080`) whose true department is `SALES`
(`corpus/manifest.jsonl`) but which the `ClassificationAgent` misclassified as `MKT` (a real
instance of the ~4.6% error rate M4-C measured, not something staged for this doc). All 4 count
toward `spend_summary`'s raw total but not toward `get_budget_status`'s reconciled `consumed`
figure. `get_budget_status`'s `remaining` field is the authoritative answer to "went over
budget"; `spend_summary` is shown alongside it for context, per this task's brief, not as a
second vote.

### This is a point-in-time snapshot, not a permanent fact

The transcript above reflects one manual data load on 2026-08-05. `tests/conftest.py`'s
session-scoped `TRUNCATE app.invoices CASCADE` means the **next** `make check` empties
`app.invoices` again -- and Postgres's `TRUNCATE ... CASCADE` truncates every table with a foreign
key referencing it regardless of that key's own `ON DELETE` rule (verified against
`pg_constraint`: `invoice_flags`/`invoice_line_items` are `ON DELETE CASCADE`,
`approvals`/`journal_entries`/`budget_consumption`/`episodic_decisions`/`llm_calls` are
`ON DELETE NO ACTION` -- `TRUNCATE CASCADE` empties all seven regardless of which). Re-running
`scripts/mcp_smoke.py` against a freshly-migrated, unseeded database will show every department at
zero consumed and no answer to the gate question -- that is correct behaviour, not a regression.
Reproduce the load in `.superpowers/sdd/2026-08-05-m7-mcp-surface/task-6-report.md` before
expecting the same transcript again.

## Reproducing the transcript yourself

1. `make dev` (starts Postgres, migrates, seeds departments/categories/budgets).
2. `make generate` (writes `corpus/`, if not already present).
3. Load a real over-budget batch: see the exact commands in
   `.superpowers/sdd/2026-08-05-m7-mcp-surface/task-6-report.md` -- in short, run
   `build_batch_graph()` (`invoice_agent.graphs.batch`) over a set of July-2026 MKT invoices from
   `corpus/manifest.jsonl` whose combined total exceeds MKT's seeded 50,000,000 limit, with
   `LLM_MODE=replay` set and `department="MKT"` declared on every task.
4. `.venv/bin/python -m scripts.mcp_smoke`

This is independent of Claude Desktop entirely -- it is the same `fastmcp.Client` machinery the
test suite drives the server through, proving the server answers correctly before anyone points a
Desktop install at it.
