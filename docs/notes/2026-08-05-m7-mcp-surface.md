# M7 — the MCP analyst surface: what it exposes, what it does not, and the gate

Date: 2026-08-05 (implementation) / closed 2026-08-06.
Roadmap items 7.1–7.6. Spec `docs/superpowers/specs/2026-08-05-m7-mcp-surface-design.md`, design
doc §8. ADR `docs/adr/0006-no-nextjs-streamlit-is-the-ui.md`.

M7 gives the project a second consumer: seven read tools and one human-authorised write, generated
from `src/invoice_agent/tools/registry.py` — the same registry the architecture test reads — plus
five further resources, reusing the same handlers, registered separately in
`src/invoice_agent/mcp/server.py`. All eight are served over MCP to Claude Desktop (or any MCP
client). This note is distilled from
the milestone's execution ledger (`.superpowers/sdd/2026-08-05-m7-mcp-surface/progress.md`) and the
six task reports in the same directory — that directory is git-ignored and is deleted once the
milestone finishes, so anything worth keeping from it had to end up here.

---

## 1. Dependency verification — no load-bearing pin moved

M0 predicted a conflict here and pinned `fastapi>=0.140` specifically so a future MCP library could
coexist: `fastapi<0.137` caps `starlette<0.50`, while `fastmcp 3.x` needs `starlette>=1.0.1`.

Measured 2026-08-05, controller-verified independently rather than taken on the implementer's
report — before and after installing `fastmcp==3.4.5` (which pulls in `mcp==1.29.0`):

```
fastapi                          0.140.0
starlette                        1.3.1
langgraph                        1.2.9
langgraph-checkpoint-postgres    3.1.0
langfuse                         3.15.0
litellm                          1.93.0
sqlalchemy                       2.0.51
psycopg                          3.3.4
pydantic                         2.13.4
```

All nine numbers are byte-identical before and after installing `fastmcp`. **No load-bearing pin
moved.** This matches the spec's §1.2 dry-run exactly (`uv pip install --dry-run fastmcp` against
the pre-M7 venv predicted `fastmcp==3.4.5 · mcp==1.29.0`, 38 new packages, zero pin changes) —
reality agreed with the prediction, so this was confirmation, not a finding. Re-checked again after
`make lock` regenerated `requirements.lock` (pins survive lock regeneration too).

**The cost is real and is recorded, not omitted.** Installing `fastmcp` pulled in **38 new
transitive packages**, including `cryptography`, `authlib`, `keyring`, `pyjwt` and `rich` — a real
supply-chain surface added to get one demo, for a project whose own stated principle is "a service
with no job is a burden" (roadmap §2.1). Accepted because the demo is this milestone's artefact;
`gitleaks` and CI still run over the full dependency tree, and nothing in that set widened this
project's own attack surface (no new code path reads them; they are `fastmcp`'s transport/auth
machinery for other use cases we do not enable).

---

## 2. The registry — what it generates, and what it does not

`src/invoice_agent/tools/registry.py` holds `ToolSpec` exactly as design §8.1 specifies (frozen
dataclass: `name`, `args_model`, `return_model`, `side_effect`, `mcp_exposed`, `handler`,
`description`), plus `MCP_WRITE_ALLOWLIST = frozenset({"submit_approval_decision"})` and
`exposed_tools()`.

**An overclaim design §8.1 makes, that spec §3.1 refuses to repeat:**

> Design §8.1 states, verbatim: *"Cả LangGraph executor lẫn MCP server đều sinh từ
> `tools/registry.py`"* — "Both the LangGraph executor and the MCP server are generated from
> `tools/registry.py`."

**That is not true today, and M7 does not make it true.** The three LangGraph graphs
(`enrichment_graph`, `batch_graph`, `approval_graph`) call their functions directly — there is no
tool executor anywhere in this codebase for the registry to feed. The registry's real consumers
are exactly two: `src/invoice_agent/mcp/server.py`'s `build_server()`, which loops over
`registry.exposed_tools()` to register each MCP tool, and the architecture test
(`tests/test_no_write_tools_in_mcp.py`), which enumerates `registry.REGISTRY` to enforce the write
allowlist. Writing "both are generated from the registry" into this note would have been the same
species of overclaim M6-A and M6-B each had to retract in their own notes. If a tool executor ever
exists, the registry is ready to feed it — that is the honest framing, not a claim that it already
does.

**A second, narrower version of the same overclaim, caught by review: resources are not generated
from the registry either.** `REGISTRY` holds exactly eight entries, all `ToolSpec` — the seven read
tools plus `submit_approval_decision` — and nothing else; there is no resource-spec type and no
resource entries in it. The five resources (`invoice://`, `invoice-xml://`, `budget://`,
`vendor://`, `report://`) are registered by a separate, hand-written function,
`mcp/server.py::_register_resources()`, whose own docstring says so directly: *"Unlike the tool loop
in `build_server`, this is a fixed set of five -- there is no 'resource registry' in
`tools/registry.py` to generate them from."* Each resource reuses an existing handler — the same
one its sibling tool calls, or `db/queries` directly — so none of them reimplements a query, which
is the property that matters; it is a fixed, hand-registered set, not a generated one. A first draft
of this note's own headline sentence made exactly this smaller version of the claim it exists to
refuse, which review caught before it shipped.

Handlers are **existing functions**, never reimplementations — a second implementation of, say,
`budget_report` reachable only over MCP would be a second source of truth and the first thing to
drift. This was tested, not just asserted: Task 3's args-model-vs-handler-signature check
introspected every `ToolSpec` in the registry and confirmed every `args_model` field name is a real
parameter on the handler it wraps, for all seven read tools.

### 2.1 Two gaps Task 1's review found in the design doc's own tool table, closed in Task 3

Design §8.2 sketches `get_budget_status(department, period)` and `get_invoice_detail(invoice_id) →
fields + line items + flags`. Task 1 registered both tools narrower than that sketch, because no
existing function matched it and writing a new query is explicitly forbidden (a reimplementation is
a second source of truth) — flagged as a concern rather than silently shipped narrower. The Task 1
reviewer caught both gaps; the ruling closed them in Task 3, not as a Task 1 fix round, so the
review is what found this, not the plan:

- **`budget_report` gained a `department` filter** — `db/queries.budget_report(conn, *,
  period=None, department=None)`, matched on `budgets.department_code`, parallel to the existing
  `period` filter. The default `None` preserves every existing caller.
- **`invoice_detail` was added as a composition of three existing functions** —
  `db/queries.invoice_detail(conn, invoice_id)` calls `invoices_repo.get_by_id`,
  `invoices_repo.get_lines`, `flags_repo.open_flags_for` (the same three calls
  `api/routes/invoices.py::get_invoice` already made inline), with no new SQL written. The
  `get_invoice_detail` `ToolSpec` was repointed at it, and `InvoiceDetail`'s docstring — which had
  previously claimed a trim parity with the route that was not accurate (the route keeps ~23
  columns; the old flat model kept 11) — was corrected to describe the real subset relationship.

---

## 3. The seven read tools, and `explain_flags` — Vietnamese, and LLM-free

| Tool | Backed by |
|---|---|
| `search_invoices(status?, department?, vendor?, min_amount?)` | `db/queries.list_invoices` |
| `get_invoice_detail(invoice_id)` | `db/queries.invoice_detail` (composition, §2.1) |
| `get_budget_status(department?, period?)` | `db/queries.budget_report` |
| `get_vendor_profile(tax_code)` | `memory/vendor_profile.stats_for` |
| `list_pending_approvals()` | `db/repositories/approvals.list_pending` |
| `explain_flags(invoice_id)` | new — `tools/explain.py` |
| `spend_summary(period, group_by)` | new — `db/queries.spend_summary` |

`explain_flags` is the one tool with genuinely new content: it renders, in Vietnamese, why an
invoice was flagged and the evidence behind it, from 18 hand-written templates (one per
`findings.CODE_REGISTRY` code), checked against every real `findings.make`/`raise_flag` call site
rather than invented field names. **No LLM is in this path** —
`tests/test_explain_flags.py::test_no_gateway_call_happens` monkeypatches `LLMGateway.generate` to
raise, and `render()` still succeeds, because nothing in `tools/explain.py` imports
`invoice_agent.llm.gateway` at all. This keeps the property CLAUDE.md's exception names explicitly:
`explain_flags` is text a human reads, so it stays Vietnamese, and the flags themselves are
deterministic Python facts narrated from a template, not an LLM decision re-explained by another
LLM.

Two codes turned out to carry thinner real-world detail than their key names suggest, recorded
rather than smoothed over: `BUDGET_UNRECONCILED`'s `department` detail key is `None` in the only
call site that raises it today (`batch.py:386-411` only fires for `undeclared_department`
invoices), so it typically renders through the missing-value placeholder; and `DUPLICATE_INVOICE`
has a template but, as of §7 below, no call site that ever reaches `render()` with it.

`spend_summary(period, group_by)` — new aggregate, invoice count + summed `total_amount` grouped by
`department` or `category`. **`period` is required**, not optional — this was a review correction
(§5, mutation-derived table below): the brief and design doc's tool table both put `period` on the
required side, distinct from `search_invoices`'s optional filters, and none of the tests exercised
the all-time path either way, so the untested path was removed rather than tested for a capability
nobody asked for.

`search_invoices` gained `vendor` (matched on `seller_tax_code`) and `min_amount` (inclusive) —
both new optional filters over the existing `list_invoices` query, no new SQL.

---

## 4. The five mutation results, naming which test bit each

Global numbering follows the DoD table (`task-7-brief.md`'s Definition of Done). Every mutation
was applied by hand, run, and reverted; each revert was confirmed by `diff` against a pre-mutation
backup.

| # | Mutation | Test that bit | Measured output |
|---|---|---|---|
| 1 | Add `ToolSpec(name="wipe_budgets", side_effect="write", mcp_exposed=True)` to `REGISTRY`, not in the allowlist | `test_no_unlisted_write_tool_is_exposed_over_mcp` | `AssertionError: write tools exposed over MCP without an allowlist entry: ['wipe_budgets']` |
| 2 | Add `ToolSpec(name="create_journal_entry", side_effect="write", mcp_exposed=False, handler=guards.create_journal_entry)` to `REGISTRY` | `test_the_ledger_write_is_not_in_the_registry_at_all` | `AssertionError: assert 'create_journal_entry' not in {...}` — fires even though the tool was correctly marked `mcp_exposed=False`, proving the "one boolean away" case is caught, not just the obvious one |
| 3 | Remove the `mcp_allow_approvals` check in `mcp/approvals.py` | `test_disabled_refuses_and_records_no_decision` | `Failed: DID NOT RAISE NotAuthorized` |
| 4 | Remove the `actor_token` check | `test_wrong_token_refuses_and_records_no_decision` **and** `test_empty_configured_token_refuses_even_a_matching_empty_call` | both `Failed: DID NOT RAISE NotAuthorized` |
| 5 | Replace the `graphs.approval.submit_decision` call with a direct `approvals_repo.record_decision` write | `test_calls_the_seam_not_the_database_directly` | `assert 0 == 1` — the monkeypatched `submit_decision` was never invoked |

All five turned a named test red with measured output, not the word "RED"; all five were reverted
and confirmed clean by diff afterward.

**A sixth mutation, not counted in the five above, pins a distinct and equally load-bearing
property** — that the server is *generated from* the registry rather than carrying a second,
hand-written list. Flipping `search_invoices`'s `mcp_exposed` to `False` in the registry alone does
**not** turn any test red — correctly so, because `test_every_exposed_tool_is_registered_on_the_server`
reads the same live, freshly-mutated registry on both sides of its own comparison, so a
registry-only edit can never desync them. To prove the test still has teeth, the *implementation*
was mutated instead — `build_server()`'s loop changed from `registry.exposed_tools()` to
`registry.REGISTRY` (simulating a server that forgets to filter), combined with the `mcp_exposed`
flip so there was a hidden tool to leak:

```
FAILED test_every_exposed_tool_is_registered_on_the_server — Extra items in the left set: 'search_invoices'
FAILED test_no_unexposed_tool_leaks_onto_the_server — assert {'search_invoices'} == set()
FAILED test_list_tools_over_a_real_mcp_client — same extra-item mismatch
3 failed, 3 passed
```

Three tests correctly went red, naming exactly the leaked tool. Both files were reverted
immediately after.

---

## 5. The audit-actor divergence — accepted, not an oversight

The M7 gate's wording (roadmap 7.2, design §8.2, spec §3.4) asks for the audit row to record
`actor='mcp:<client>'`. Measured against a real MCP-driven decision (Task 5's live transcript):

```
approvals row after the call:
  decided_by  = 'mcp:claude-desktop'

journal_entries row posted by the SAME guard chain HTTP uses:
  posted_by   = 'human:mcp:claude-desktop'
```

`approvals.decided_by` is exactly the bare form the gate asks for. `audit_log.actor` and
`journal_entries.posted_by` are not — they read `human:mcp:claude-desktop`, because pre-existing
`graphs/approval.py:264-265` (`post_entry_node`) unconditionally computes
`actor=f"human:{state['decided_by']}"` before calling `guards.create_journal_entry`, regardless of
which transport produced the decision. This is not new in M7; M7 is the first transport whose value
of `decided_by` itself already contains a colon, which is what makes the nesting visible.

**The user ruled on 2026-08-05 to accept `human:mcp:<client>` rather than edit
`post_entry_node`.** The reasoning: the spec's stated purpose — a Claude Desktop decision must stay
distinguishable from a UI decision, forever — still holds, since `human:mcp:claude-desktop` !=
`human:ketoan`. Fixing `post_entry_node` to special-case a bare `mcp:<client>` form would mean
editing the HITL path pinned to `langgraph==1.2.9` and covered by `test_hitl_survives_restart.py`,
for a milestone whose scope is adding a transport, not touching that path. `db/models.py:382`'s
`audit_log.actor` column comment, which previously predicted a bare third form that this ruling
means will now never be written, was corrected to describe reality (a comment-only change; no
`comment=` kwarg on the `Column`, so no migration was implied or needed).

---

## 6. `report://{period}`'s payload was never specified

The design doc pins the URI shape of all five resources (§8.2, §3.3) but never specifies
`report://{period}`'s payload. It currently returns `budget_report(period=period)` with
`department` left unset — structurally the same as `budget://{department}/{period}` with the
department wildcarded across every department for that period. No `ReportingAgent` module exists
(that is roadmap item 8.1, M8's, not M7's), and nothing in this codebase consumes `report://`
today. **Recorded as a limitation, not expanded**: a controller ruling during Task 4's review
confirmed there was nothing to widen it against, so it stays as the period-scoped budget view it
already is.

---

## 7. Observations found along the way, not fixed (outside M7's scope)

- **`DUPLICATE_INVOICE` has a narrative template but no `raise_flag` call site.**
  `graphs/batch.py:231` appends a plain dict (`{"invoice_id": ..., "code":
  findings.DUPLICATE_INVOICE}`) to an in-memory list when `insert_with_lines` returns `None` —
  meaning the invoice was never persisted — rather than calling `flags.raise_flag`. The template
  written for this code in Task 2 is therefore currently unreachable in production. Pre-existing,
  outside M7's scope, not a defect this milestone introduced.
- **A real `ClassificationAgent` misclassification, from Task 6's first data-load attempt.**
  Loading the M4 gate batch's 30 invoices with no `department` declared on any task (so budget
  reconciliation fell back to the classifier's own department guess) produced `INV-0080` classified
  as `MKT`; the corpus's ground truth is `SALES`. This is real corpus behaviour, consistent with the
  ~4.6% error rate M4-C measured (95.4% accuracy), not staged. **It is not visible in the shipped
  transcript** — the committed loader (`scripts/load_gate_overspend.py`) selects a clean,
  8-invoice, all-correctly-MKT-classified set, so the transcript in §8 below shows no
  misclassification and `spend_summary`'s MKT total agrees exactly with `get_budget_status`'s
  reconciled `consumed` figure. The observation is about the classifier, surfaced by an earlier,
  uncommitted load attempt — not a property of what is committed.

---

## 8. The smoke transcript, and how to regenerate it

Load the data with the committed loader, then ask the gate question through a real MCP client:

```
LLM_MODE=replay .venv/bin/python -m scripts.load_gate_overspend
.venv/bin/python -m scripts.mcp_smoke
```

`scripts/load_gate_overspend.py` runs the real `build_batch_graph()` (the same graph `POST
/batches` drives) over 8 real, already-committed corpus invoices
(`INV-0101/0151/0127/0055/0031/0181/0107/0157`), each declared `department="MKT"` — the same field
`POST /batches`'s form parameter exposes for exactly this purpose — under `LLM_MODE=replay`, so
**zero LLM tokens are spent**. `scripts/mcp_smoke.py` drives `build_server()` through a real
`fastmcp.Client` and calls `get_budget_status` and `spend_summary` for `2026-07`. Captured
2026-08-06 against a freshly-truncated database:

```
get_budget_status(period='2026-07'):
  ADMIN  limit=    30000000 consumed=           0 remaining=     30000000
  IT     limit=    60000000 consumed=           0 remaining=     60000000
  MKT    limit=    50000000 consumed=    56525000 remaining=     -6525000  <-- OVER BUDGET
  OPS    limit=    80000000 consumed=           0 remaining=     80000000
  SALES  limit=    40000000 consumed=           0 remaining=     40000000

spend_summary(period='2026-07', group_by='department') -- for context only, NOT the answer (see module docstring: unreconciled invoices count here):
  MKT    invoices=8    total=56525000

ANSWER: MKT went over budget in 2026-07.
  MKT consumed 56525000 against a 50000000 limit -- over by 6525000.
```

This is a **point-in-time snapshot, not a permanent fact**: `tests/conftest.py`'s session-scoped
`TRUNCATE app.invoices CASCADE` empties `app.invoices` and `app.budget_consumption` again at the
start of every test session, so `make check` alone never leaves this data in place. Re-running
`scripts/load_gate_overspend.py` reproduces the identical consumption chain — verified by running
it twice: the second run persists 0 new rows (an `ON CONFLICT DO NOTHING` no-op), confirming the
loader's idempotency claim by measurement rather than by reasoning alone. Full detail (env vars,
the two DSNs, why `DATABASE_URL` alone is not sufficient, enabling the write tool) lives in
`docs/mcp-claude-desktop.md`.

**The Claude Desktop recording itself is the user's to capture, not delivered here.** The server,
the config block, and this client-driven transcript are what M7 can produce without a Desktop
install; a real Desktop session needs the user's own machine — the same division of labour M6-C
gave the hand-timed manual baseline (§8's protocol vs. its measurement).

---

## 9. `invoice-xml://` — a stated PII limitation, not a claim of safety

`invoice-xml://{invoice_id}` serves whatever raw XML was uploaded, unconditionally — no redaction,
no gating. Today's corpus is synthetic, but this repo has leaked PII from real vendor documents
**twice** in its history (roadmap §1), and a real uploaded invoice carries a tax code, an address,
and possibly a signing certificate. **This is recorded as a limitation a real deployment must
address, not claimed to be safe.** The resource's own docstring states this as a Task 7 follow-up,
not something this milestone solves.

### 9.1 A real FastMCP library bug, found and worked around

Before this was even a policy question, it was a wire-format bug: `invoice-xml://` was serving
`mimeType: "text/plain"` despite `mime_type="application/xml"` being passed to
`@server.resource(...)`. Root cause, found by reading the installed `fastmcp==3.4.5` source
directly: `ResourceTemplate.convert_result` (`fastmcp/resources/template.py:243-257`) does **not**
forward `mime_type` for a bare-`str` return, unlike `Resource.convert_result` — the `mime_type=`
kwarg only affects what `list_resource_templates()` *advertises*, not what a template-based read
actually returns, unless the read function constructs the `ResourceResult`/`ResourceContent`
explicitly. **All five of this project's resources are templates** (every URI has a `{param}`), so
this is the path that mattered, not an edge case.

Fixed by having `read_invoice_xml` return
`ResourceResult(contents=[ResourceContent(raw_xml, mime_type="application/xml")])` instead of a
bare `str`. Verified before/after:

```
# before: mimeType: text/plain
# after:  mimeType: application/xml
```

**A review round found this had zero regression coverage** — only resource *registration* was
tested, not what reading one actually returns — so a future "simplify this back to `return raw_xml`"
pass (which looks more obviously correct by inspection, exactly how the bug shipped the first time)
would have silently regressed with nothing catching it. Five new tests were added, one per resource,
each driving a real `fastmcp.Client` read against real committed/seeded data. The regression test
was proven to catch the exact regression it is named for — reverting `read_invoice_xml` to its
pre-fix bare-`str` shape and re-running:

```
FAILED test_reading_invoice_xml_returns_application_xml_mime_type
AssertionError: assert 'text/plain' == 'application/xml'
```

then restored, confirmed green.

---

## 10. What was deliberately not built

- **Next.js (roadmap 7.5).** Stopped before the 4-day time-box, not at the end of it. Full
  reasoning in `docs/adr/0006-no-nextjs-streamlit-is-the-ui.md`: the roadmap's own risk table names
  Next.js the biggest remaining schedule risk and states it proves none of the seven GĐ5 skills;
  M5-D already ships a working Streamlit UI with an enforced HTTP-only boundary. **Item 7.5 is met
  by the gate's own stopping-rule branch** — the roadmap's Gate M7 text accepts either "Next.js does
  the full approve/reject loop" or "the decision was made to stop and keep Streamlit" — not by
  default and not by accident.
- **pgvector for episodic `reason_embedding` (roadmap 7.6).** Marked "only if time" in the roadmap
  and already deferred once at M4 (item 4.4, for the same reason). Principle 2 of this project
  ("measure first, optimize second") would require before/after numbers against a baseline to
  justify merging it — a measurement this milestone had no budget for. **Not done, and recorded as
  not done**, not folded silently into "future work."
- **Mounting MCP into the FastAPI app.** Claude Desktop launches an MCP server over **stdio**, so
  the in-process HTTP mount the `fastapi>=0.140` pin was protecting against a version conflict was
  never needed to satisfy this milestone's gate. The pin still did its job — it is why `fastmcp`
  coexists in the same venv as `fastapi` at all (§1 above). An HTTP-transport MCP endpoint, if M8
  wants one for a hosted demo, is a separate decision with its own auth story this project
  deliberately does not have (roadmap §4).

---

## 11. Gate verification

```
$ make check
.venv/bin/ruff check src tests migrations scripts
All checks passed!
.venv/bin/mypy
Success: no issues found in 76 source files
.venv/bin/python -m pytest
825 passed, 2 xfailed, 1 warning in 41.80s
```

Zero skipped — Postgres was up throughout. The suite also runs green with **every provider key
unset**, the condition CI and the live demo run under:

```
$ env -u GROQ_API_KEY -u GROQ_API_KEYS -u GEMINI_API_KEY -u GEMINI_API_KEYS -u OPENROUTER_API_KEY \
  LLM_MODE=replay .venv/bin/python -m pytest tests/ -q
```

`pyproject.toml` already sets `addopts = "-q"`, so an explicit `-q` on the command line becomes
`-qq` and pytest **drops the summary line entirely** — the exact "ran it and it was green with no
visible count" trap M6-C's own note warns about. Recorded honestly rather than silently re-run with
a different flag and passed off as the literal command: the same run **without** the extra `-q`
gives the count —

```
$ env -u GROQ_API_KEY -u GROQ_API_KEYS -u GEMINI_API_KEY -u GEMINI_API_KEYS -u OPENROUTER_API_KEY \
  LLM_MODE=replay .venv/bin/python -m pytest tests/
825 passed, 2 xfailed, 1 warning in 47.67s
```

Same count as `make check`, zero skipped, every provider key unset.

### 11.1 CI on the pushed SHA

The rule this project has broken five times before: a milestone is not closed until `make check` is
green on the **pushed** commit and CI agrees.

| | |
|---|---|
| SHA | `4b98cc1d209be1329b5abda5fff986c00721de3a` |
| Run | https://github.com/quockhanh41/Vietnamese-Invoice-agent/actions/runs/31032966630 |
| Conclusion | **success** — both jobs (`secrets`, `test`) |

```
secrets   gitleaks (toàn bộ history)   success
test      Lint (ruff)                 All checks passed!
test      Typecheck (mypy)            success
test      Test (pytest)               832 passed, 2 xfailed, 1 warning in 76.81s
```

This SHA is the commit that fixed the resources-are-not-generated overclaim a review round found in
this note's own headline (§2) — the record was re-taken after that fix rather than left pointing at
a superseded, pre-fix commit.

**832 on CI vs 825 locally is not a discrepancy**: `ci.yml` sets `HITL_RUNS=10` where the local
default is 3, and 825 + 7 = 832 — the exact accounting M6-B/M6-C's own notes already established for
this same gap. Zero skipped on CI.

---

## 12. One test comment corrected as part of this task

`tests/test_mcp_approval_gate.py`'s comment above `test_unknown_invoice_is_refused` predicted the
guard-removed mutation would leak a raw `EmptyInputError`. That was a prediction from Task 5's
review, made **before** the mutation was actually run — the measurement disproved it: the real
exception is `KeyError: 'invoice_id'`, raised from `graphs/approval.py::_load_snapshot` when
`Command(resume=...)` reaches a thread LangGraph never started and the first node that reads state
throws. `mcp/approvals.py`'s `NotFound` docstring already stated the measured exception correctly;
only the test file's comment still named the disproven one. Corrected to match the measurement
(comment-only change, no behaviour affected) — two comments from the same commit disagreeing is
exactly the kind of drift this project's "verify, don't assume" discipline exists to catch.
