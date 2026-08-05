# Spec — M7: the MCP analyst surface, and the Next.js stopping rule (items 7.1–7.5)

> **Date**: 2026-08-05 · **Milestone**: M7 · **Status**: design approved, implementation depth.
>
> Reads with roadmap `docs/Plan_Project_F_2026-07-25.md` §3 (M7) and design doc
> `docs/project-f-system-design.md` §8 (tool layer & MCP).
>
> Predecessor **M6 closed 2026-08-05** (`origin/main` `806650c`, CI green) with three gate criteria
> reported NOT met and one PENDING. M7 revisits none of them.

---

## 0. Global constraints

From `CLAUDE.md` and roadmap §0. Not restated per section.

- **The LLM never toggles a flag and never posts a journal entry.** M7 adds a *second transport* to
  an existing safety boundary; it must not add a second *path through* it.
- **`create_journal_entry` is never exposed over MCP.** Design §8.2 is explicit that doing so
  "destroys the safety property", because MCP is stateless request/response and knows nothing about
  approval state. An architecture test enforces this.
- **Authoring language English.** Text the system *emits* stays Vietnamese — notably `explain_flags`,
  whose narrative a human reads.
- **Every non-obvious constant carries date + measurement + report file name.**
- **A milestone is not closed until `make check` is green on the pushed commit and CI agrees.**

---

## 1. Why this exists

M7 is the milestone where the project acquires a **second consumer**. Everything to date is exercised
by its own tests, its own API and its own Streamlit UI. Design §8.2's defensible argument for MCP is
that Claude Desktop is a consumer that *already exists* and that the author already uses — so the
tool surface stops being a hypothesis.

### 1.1 The honest assessment, restated because it shapes the scope

Design §8.2 argues **against** naive MCP adoption, and M7 keeps that argument rather than quietly
dropping it:

- Routing this project's own graph through a protocol hop to reach its own functions adds latency
  and buys nothing. **M7 therefore does not do that.** The graph keeps calling its functions
  directly.
- "Project B will reuse it" is a hypothesis, and a real Project B would query Postgres directly.
- Exposing `create_journal_entry` over MCP would contradict the one thing this project exists to
  demonstrate.

What survives is exactly two things: a **read-only analyst surface**, and **one human-authorised
write** that proves `interrupt()` is transport-agnostic.

### 1.2 Verified before specifying, not assumed

M0 predicted a dependency conflict here: `fastapi <0.137` caps `starlette <0.50`, while `fastmcp 3.x`
needs `starlette >=1.0.1`, so `fastapi >=0.140` was pinned specifically to keep MCP in-process.

Measured 2026-08-05 with `uv pip install --dry-run fastmcp` against the current venv
(`fastapi 0.140.0`, `starlette 1.3.1`, `pydantic 2.13.4`):

```
Resolved 66 packages · would install 38
fastmcp==3.4.5 · mcp==1.29.0
```

**No load-bearing pin is added, removed or changed** — `fastapi`, `starlette`, `langgraph`,
`langgraph-checkpoint-postgres`, `langfuse`, `litellm`, `sqlalchemy`, `psycopg` and `pydantic` are
all untouched. The M0 pin did its job.

**The cost is worth stating**: 38 new transitive packages, including `cryptography`, `authlib`,
`keyring`, `pyjwt` and `rich`. For a project whose §2.1 principle is "a service with no job is a
burden", that is a real supply-chain surface added to get one demo. It is accepted because the demo
is the milestone's artefact, and recorded so the trade is visible.

---

## 2. Decisions

### 2.1 The Next.js stopping rule is invoked now — before the time-box, not after

Roadmap 7.5 time-boxes Next.js to 4 days, and the stopping rule says: out of time without a working
approve/reject loop → keep Streamlit. The gate accepts either outcome: *"Next.js does the full
approve/reject loop **or** the decision was made to stop and keep Streamlit."*

**Decision (2026-08-05, with the user): stop now, keep Streamlit.** Reasoning, recorded in ADR
`0006-no-nextjs-streamlit-is-the-ui.md`:

- The roadmap's own assessment is that Next.js is "the biggest schedule risk that **proves none of
  the 7 GĐ5 skills**", and that "a bad UI on a correct agent beats a beautiful UI on a bad agent".
- M5-D already ships a working Streamlit UI with an HTTP-only boundary (`ui/` imports nothing from
  `invoice_agent` but `config`, enforced by a source guard). The demo requirement is already met.
- Deciding at the *start* of the time-box rather than the end is the difference between a decision
  and a sunk cost. The rule exists to be invoked; invoking it late is how it fails.

This is a **decision, not an omission**, and the ADR says so — the same treatment §4's "intentionally
NOT doing" list gets.

### 2.2 `submit_approval_decision` is included, gated, and off by default

Design §8.2 calls it "the impressive part": a write, but **human-authorised**, travelling the same
`Command(resume=)` path and the same DB constraints. A chief accountant approving from Claude Desktop
on a phone instead of opening a web UI.

Gated by `MCP_ALLOW_APPROVALS` (**default `false`**) plus an actor token; the audit row records
`actor='mcp:<client>'`.

### 2.3 Not doing

- **7.6 pgvector episodic `reason_embedding`** — marked "only if time" and already deferred once at
  M4. Principle 2 would require before/after numbers to justify merging it, which is a measurement
  this milestone has no budget for. Recorded as deferred, with the reason.
- **Mounting MCP into the FastAPI app.** Claude Desktop launches an MCP server over **stdio**, so the
  in-process HTTP mount the M0 pin was protecting is not needed for the gate. The pin still mattered:
  it is why `fastmcp` coexists in one venv at all. If M8 wants an HTTP-transport MCP endpoint, that
  is a separate decision with its own reason.

---

## 3. Design

### 3.1 The registry, and one claim it must not make

`src/invoice_agent/tools/registry.py` holds `ToolSpec` exactly as design §8.1 specifies:

```python
ToolSpec(
    name="get_budget_status",
    args_model=BudgetQuery,      # pydantic
    return_model=BudgetInfo,     # pydantic
    side_effect="read",          # read | write
    mcp_exposed=True,
    handler=...,                 # the function that already exists
)
```

> **An overclaim this spec refuses up front.** Design §8.1 says *"both the LangGraph executor and the
> MCP server are generated from `tools/registry.py`"*. That is **not true today and M7 does not make
> it true**: the graphs call their functions directly and there is no tool executor to generate. The
> registry's real consumers are the MCP server and the architecture test. Writing "both are generated
> from the registry" into a note would be the same species of claim M6-A and M6-B each had to
> retract. If a tool executor ever exists, the registry is ready for it — that is the honest framing.

Handlers are **existing functions**, not reimplementations. A second implementation of
`budget_report` reachable only over MCP is a second source of truth, and the first thing to drift.

### 3.2 The seven read tools

| Tool | Backed by | New work |
|---|---|---|
| `search_invoices(department?, period?, status?, vendor?, min_amount?)` | `db/queries.list_invoices` | filter args |
| `get_invoice_detail(invoice_id)` | `api/routes/invoices` read path | — |
| `explain_flags(invoice_id)` | `domain/findings` + `invoice_flags` | **new — narrative** |
| `get_budget_status(department, period)` | `db/queries.budget_report` | shape to args |
| `get_vendor_profile(tax_code)` | `memory/vendor_profile.stats_for` | — |
| `spend_summary(period, group_by)` | `db/queries` aggregate | **new — grouping** |
| `list_pending_approvals()` | `db/repositories/approvals` | — |

**`explain_flags` is the one with real content.** It renders, in **Vietnamese**, why an invoice was
flagged and the evidence behind it. This is product output a human reads — CLAUDE.md's exception
names `explain_flags` explicitly — so it stays Vietnamese and is **not** LLM-generated: the flags are
deterministic Python facts, and narrating them from a template keeps the "no LLM decides anything"
property intact. An LLM narration would put a model back in the explanation path for a decision it
did not make.

### 3.3 The five resources

`invoice://{id}` · `invoice-xml://{id}` · `budget://{dept}/{period}` · `vendor://{tax_code}` ·
`report://{period}`

> **`invoice-xml://` needs a decision recorded, not just an implementation.** It returns the raw
> source XML. Today's corpus is synthetic, but this repo has leaked PII **twice** (roadmap §1), and a
> real uploaded invoice carries a tax code, an address and possibly a signing certificate. The
> resource is implemented, and the note states plainly that it serves whatever was uploaded — so a
> deployment handling real invoices must gate it. That is a smaller claim than "it is safe", and it
> is the true one.

### 3.4 `submit_approval_decision` — the same seam, a different transport

It calls `graphs/approval.submit_decision(...)` and adds **no logic**. That function's own docstring
already names this milestone:

> *"THE SEAM M5-C WRAPS. The FastAPI route parses a request body and calls this; it adds no logic.
> That is what makes 'interrupt is transport-agnostic' (the M7 artefact) demonstrable rather than
> asserted — a test, a script, HTTP and MCP all drive the identical function."*

Three gates, all of which must hold:

1. `MCP_ALLOW_APPROVALS` is true (default **false**).
2. An actor token matches the configured value.
3. The existing guard chain runs unchanged — `create_journal_entry` still re-reads Postgres to
   confirm the `approvals` row persisted, exactly as it does over HTTP.

The audit row records `actor='mcp:<client>'`, so a decision taken from Claude Desktop is
distinguishable from one taken in the UI, forever.

**What must NOT happen**: MCP must not reach the database directly to write an approval, and must not
bypass `submit_decision` to post an entry. The architecture test covers the tool surface; the
existing `test_never_post_without_approval.py` covers the guard.

### 3.5 `tests/test_no_write_tools_in_mcp.py`

Every `ToolSpec` with `side_effect == "write"` and `mcp_exposed=True` must be in an explicit
allowlist — `{"submit_approval_decision"}`. Anything else fails the build.

Design §8.2's framing is the point: *"I deliberately do not expose write tools over MCP, and here is
the test that stops future-me from doing it"* is a better interview answer than "I exposed 6 tools".

The test must enumerate the **registry**, not a hand-written list — a second list drifts, and this
project has already shipped one test that certified nodes it could not see (M6-B).

---

## 4. Scope — out

- Any Next.js work (§2.1).
- pgvector episodic embeddings (§2.3).
- Mounting MCP into FastAPI (§2.3).
- Auth beyond the actor token. Roadmap §4 lists auth/RBAC as deliberately not done; the token is a
  gate, not an identity system, and the note must not imply otherwise.
- Changing any agent response model, prompt, `sanitize.wrap()`, or the guard chain.
- Re-measuring anything M6 published.

---

## 5. Mutation verification

Each must turn a **named** test red; the note records measured output, not the word "RED".

| Mutation | Expected |
|---|---|
| Add a `ToolSpec(side_effect="write", mcp_exposed=True)` not in the allowlist | the architecture test |
| Expose `create_journal_entry` via the registry | the architecture test |
| Remove the `MCP_ALLOW_APPROVALS` check | the gating test |
| Remove the actor-token check | the gating test |
| Make `submit_approval_decision` write the DB directly instead of calling `submit_decision` | the transport-agnostic test |

---

## 6. M7 gate

| Criterion | Evidence required |
|---|---|
| Claude Desktop queries the invoice DB over MCP | config + a recorded session answering *"which department went over budget in July?"* |
| `test_no_write_tools_in_mcp.py` green | plus mutations 1 and 2 red |
| Next.js: full loop **or** a recorded decision to stop | ADR `0006`, per §2.1 |
| `submit_approval_decision` gated | mutations 3 and 4 red; audit row shows `actor='mcp:<client>'` |
| `explain_flags` narrative is Vietnamese and LLM-free | test asserting no gateway call |
| `make check` green on the pushed commit, CI agreeing | CI run URL on the closing SHA |

**Division of labour on the recording**: the server, the Claude Desktop config, and a transcript
produced through an MCP client are built and verified here. **The Desktop recording itself needs the
user's machine** and is theirs to capture — the same treatment M6-C gave the manual baseline.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| `fastmcp` 3.x churns and breaks the server | Pin it like the other load-bearing deps; it is now in the same category as `langgraph` |
| 38 new transitive packages widen the supply-chain surface | Recorded in §1.2 as a stated trade, and `gitleaks` + CI still run over everything |
| The MCP surface duplicates query logic and drifts | §3.1 requires handlers to be the existing functions; a reimplementation is a review finding |
| `invoice-xml://` leaks PII in a real deployment | §3.3 records the limitation rather than claiming safety |
| The write tool becomes a second approval path | §3.4 routes it through `submit_decision`; a mutation pins that it cannot bypass it |

---

## 8. Open questions — recorded, not silently dropped

1. **Should the MCP server also be reachable over HTTP for a hosted demo (M8)?** Deferred: stdio
   satisfies the gate, and an HTTP transport needs an auth story this project deliberately does not
   have.
2. **Does `explain_flags` want the `ReconciliationAgent` narrative from design §3.2 rather than a
   template?** M7 ships the template because it is deterministic. If M8 wants richer prose, that is a
   measured change, not a refactor.
3. **NĐ 70/2025/NĐ-CP** remains open from M1 and is still not blocking.
