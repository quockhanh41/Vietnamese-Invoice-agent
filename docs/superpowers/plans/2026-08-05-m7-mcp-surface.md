# M7 — MCP Analyst Surface: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the project a second consumer — Claude Desktop — through a registry-generated MCP surface of 7 read tools and 5 resources, plus exactly one human-authorised write that proves `interrupt()` is transport-agnostic, guarded by an architecture test that stops future-me widening it.

**Architecture:** `tools/registry.py` declares `ToolSpec`s whose handlers are the functions that already exist — no reimplementation, so no second source of truth. The MCP server is generated from that registry. The single write tool calls `graphs/approval.submit_decision` and adds no logic, so the guard chain, the DB constraints and the checkpoint semantics are unchanged.

**Tech Stack:** Python 3.13, FastMCP 3.4.5 + mcp 1.29.0 (stdio transport), Pydantic 2.13, SQLAlchemy + Postgres 17, pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-05-m7-mcp-surface-design.md`

## Global Constraints

- **Authoring language English** for code, comments, docstrings, commits. Text the system *emits* stays Vietnamese — `explain_flags`' narrative is read by a human and must be Vietnamese.
- **The LLM never toggles a flag and never posts a journal entry.** M7 adds a transport, not a path. No agent response model gains a field; prompts, `sanitize.wrap()`, `structured.py` and the guard chain are untouched.
- **`create_journal_entry` is never exposed over MCP** (design §8.2: doing so "destroys the safety property"). Enforced by test, not by intention.
- **Handlers must be existing functions.** A second implementation reachable only over MCP is a second source of truth and the first thing to drift.
- **Every non-obvious constant carries date + measurement + report file name.**
- Pinned and not to be bumped: `langgraph==1.2.9`, `langgraph-checkpoint-postgres==3.1.0`, `langfuse>=3.15,<4.0.0`, `fastapi>=0.140`. **Pin `fastmcp` on the way in** — it is now load-bearing.
- `make check` green before every commit. Baseline entering M7: **787 passed, 2 xfailed, 0 skipped**.
- **`make check`'s mypy covers only the `invoice_agent` package**, so anything under `scripts/` is untyped in CI. `src/invoice_agent/mcp/` IS covered — keep it there, and run mypy explicitly for anything outside it.
- Postgres must be running. If tests mass-SKIP with "Cần Postgres đang chạy", run `docker compose up -d postgres`, wait for healthy, retry. **Never read SKIPs as passes.**
- **Do not run a live eval or anything that inserts into `app.llm_calls` while tests run** — M6-C measured a Postgres deadlock between that and conftest's session-scoped `TRUNCATE app.invoices CASCADE`.

---

### Task 1: The registry and the architecture test

The test is the milestone's real artefact — design §8.2: *"I deliberately do not expose write tools over MCP, and here is the test that stops future-me from doing it"* beats "I exposed 6 tools". It lands first so everything after is built under it.

**Files:**
- Create: `src/invoice_agent/tools/registry.py`
- Create: `tests/test_no_write_tools_in_mcp.py`

**Interfaces:**
- Produces: `registry.ToolSpec`, `registry.REGISTRY: tuple[ToolSpec, ...]`, `registry.MCP_WRITE_ALLOWLIST: frozenset[str]`, `registry.exposed_tools() -> tuple[ToolSpec, ...]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_no_write_tools_in_mcp.py`:

```python
"""The architecture decision M7 exists to defend.

Design §8.2 rejects exposing write tools over MCP: MCP is stateless
request/response and knows nothing about approval state, so a `create_journal_entry`
reachable from any client would let anyone post to the ledger — contradicting the
one property this whole project demonstrates.

Exactly ONE write is permitted, and it is human-authorised: `submit_approval_decision`,
which resumes an existing interrupt rather than inventing an approval.

These tests enumerate the REGISTRY, never a hand-written list. A second list drifts,
and this repo has already shipped a test that certified nodes it could not see (M6-B).
"""

from invoice_agent.tools import registry


def test_no_unlisted_write_tool_is_exposed_over_mcp():
    offenders = [
        spec.name
        for spec in registry.REGISTRY
        if spec.side_effect == "write"
        and spec.mcp_exposed
        and spec.name not in registry.MCP_WRITE_ALLOWLIST
    ]
    assert offenders == [], f"write tools exposed over MCP without an allowlist entry: {offenders}"


def test_the_allowlist_is_exactly_the_one_tool_design_8_2_permits():
    """Pins the allowlist ITSELF.

    Without this, widening the surface costs one word in a set literal and no
    test notices. The friction is the feature: adding a second write tool must
    require editing this assertion, which is a decision someone has to defend.
    """
    assert registry.MCP_WRITE_ALLOWLIST == frozenset({"submit_approval_decision"})


def test_the_ledger_write_is_not_in_the_registry_at_all():
    """`create_journal_entry` must not be registered even as unexposed.

    Registering it with `mcp_exposed=False` would leave the safety property one
    boolean away from gone.
    """
    names = {spec.name for spec in registry.REGISTRY}
    assert "create_journal_entry" not in names
    assert "post_batch" not in names


def test_every_registered_handler_is_callable():
    """A ToolSpec whose handler is a string or None would fail only at call
    time, i.e. in front of Claude Desktop."""
    broken = [spec.name for spec in registry.REGISTRY if not callable(spec.handler)]
    assert broken == []


def test_read_tools_declare_no_side_effect():
    for spec in registry.REGISTRY:
        assert spec.side_effect in {"read", "write"}, f"{spec.name}: {spec.side_effect!r}"
```

- [ ] **Step 2: Run and verify it fails**

Run: `.venv/bin/python -m pytest tests/test_no_write_tools_in_mcp.py -v`
Expected: collection error — `invoice_agent.tools.registry` does not exist.

- [ ] **Step 3: Implement the registry**

Create `src/invoice_agent/tools/registry.py`. Register the **five read tools whose handlers already exist**; the remaining two arrive in Tasks 2-3, and the write tool in Task 5.

```python
"""One declaration of every tool, per design §8.1.

⚠️ SCOPE, stated because the design doc's phrasing overreaches. §8.1 says "both
the LangGraph executor and the MCP server are generated from tools/registry.py".
That is NOT true today: the graphs call their functions directly and there is no
tool executor to generate. The registry's real consumers are the MCP server
(`invoice_agent/mcp/`) and `tests/test_no_write_tools_in_mcp.py`. Written down so
a future reader does not inherit a claim the code never made.

Handlers are the functions that ALREADY EXIST. A tool that reimplements a query
is a second source of truth and the first thing to drift out of agreement with
the API and the UI.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    return_model: type[BaseModel] | None
    side_effect: Literal["read", "write"]
    mcp_exposed: bool
    handler: Callable[..., Any]


# The ONLY write reachable over MCP. Design §8.2 permits it because it is
# human-authorised and resumes an existing interrupt; it invents no approval and
# posts no entry itself. Widening this set must be a deliberate act — the
# architecture test asserts the set's exact contents, so a second entry cannot
# be added without editing a test that says why.
MCP_WRITE_ALLOWLIST: frozenset[str] = frozenset({"submit_approval_decision"})

REGISTRY: tuple[ToolSpec, ...] = (
    # populated below
)


def exposed_tools() -> tuple[ToolSpec, ...]:
    """What the MCP server registers. The server never filters for itself."""
    return tuple(spec for spec in REGISTRY if spec.mcp_exposed)
```

Define the Pydantic arg/return models in `src/invoice_agent/tools/models.py` and register these five, each `side_effect="read"`, `mcp_exposed=True`:

| name | handler |
|---|---|
| `search_invoices` | `db.queries.list_invoices` |
| `get_invoice_detail` | the invoices read path used by `api/routes/invoices.py` |
| `get_budget_status` | `db.queries.budget_report` |
| `get_vendor_profile` | `memory.vendor_profile.stats_for` |
| `list_pending_approvals` | `db.repositories.approvals.list_pending` |

- [ ] **Step 4: Run and verify pass**

Run: `.venv/bin/python -m pytest tests/test_no_write_tools_in_mcp.py -v`
Expected: 5 passed.

- [ ] **Step 5: Mutation 1 — an unlisted write tool**

Add `ToolSpec(name="wipe_budgets", side_effect="write", mcp_exposed=True, ...)` to `REGISTRY`.
Run: `.venv/bin/python -m pytest tests/test_no_write_tools_in_mcp.py -v`
Expected: `test_no_unlisted_write_tool_is_exposed_over_mcp` FAILS naming `wipe_budgets`. Record, revert.

- [ ] **Step 6: Mutation 2 — the ledger write, registered but not exposed**

Add `ToolSpec(name="create_journal_entry", side_effect="write", mcp_exposed=False, ...)`.
Run the same file.
Expected: `test_the_ledger_write_is_not_in_the_registry_at_all` FAILS. This is the sharper of the two — it proves the guard catches the *one boolean away* case, not just the obvious one. Record, revert.

- [ ] **Step 7: `make check` and commit**

```bash
make check
git add src/invoice_agent/tools/registry.py src/invoice_agent/tools/models.py tests/test_no_write_tools_in_mcp.py
git commit -m "feat(m7): one registry, and the test that keeps the MCP surface read-only"
```

---

### Task 2: `explain_flags` — a Vietnamese narrative with no model in it

Spec §3.2. The one read tool with real content.

**Files:**
- Create: `src/invoice_agent/tools/explain.py`
- Modify: `src/invoice_agent/tools/registry.py` (register it)
- Test: `tests/test_explain_flags.py` (create)

**Interfaces:**
- Produces: `explain.explain_flags(conn, invoice_id) -> ExplainedFlags`

- [ ] **Step 1: Write the failing test**

```python
"""`explain_flags` narrates deterministic facts. No model is involved.

The flags are computed by Python from database state — that is the project's
central safety property. Narrating them with an LLM would put a model back into
the explanation path for a decision it did not make, and a reviewer reading the
narrative would have no way to tell which parts were computed and which were
generated.

The narrative is VIETNAMESE: a human reads it, so CLAUDE.md's product-output
exception applies and it must not be translated.
"""

import uuid

import pytest

from invoice_agent.domain import findings
from invoice_agent.tools import explain


def test_every_registered_flag_code_has_a_narrative_template():
    """A code with no template would render as a bare enum to an accountant."""
    missing = [code for code in findings.CODE_REGISTRY if code not in explain.TEMPLATES_VI]
    assert missing == [], f"flag codes with no Vietnamese narrative: {missing}"


def test_the_narrative_is_vietnamese_not_english():
    text = explain.render(findings.BUDGET_BREACH, {"department": "MKT", "over_by": "5000000"})
    assert any(ch in text for ch in "ăâđêôơư"), f"narrative looks untranslated: {text!r}"


def test_explaining_an_invoice_with_no_flags_says_so_rather_than_returning_empty(runtime_conn):
    result = explain.explain_flags(runtime_conn, uuid.uuid4())
    assert result.flags == []
    assert result.summary_vi != ""


def test_no_gateway_call_happens(monkeypatch):
    """Mutation-proof: if someone later routes this through a model, this fails."""
    from invoice_agent.llm import gateway as gw

    def _boom(*a, **k):
        raise AssertionError("explain_flags must not call an LLM")

    monkeypatch.setattr(gw.LLMGateway, "generate", _boom)
    text = explain.render(findings.NEW_VENDOR, {})
    assert text
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_explain_flags.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

`TEMPLATES_VI: dict[str, str]` with one entry per code in `findings.CODE_REGISTRY` (there are 18 — the test enumerates the registry so a future code cannot be forgotten), `render(code, detail)` formatting a template with the flag's `detail` payload, and `explain_flags(conn, invoice_id)` reading `db.repositories.flags.open_flags_for` and returning a Pydantic `ExplainedFlags` carrying, per flag: `code`, `severity`, `raised_by` (all from `CODE_REGISTRY`) and the rendered Vietnamese sentence, plus a `summary_vi`.

Severity and producer come from `CODE_REGISTRY`, not from a second table — that mapping already encodes the reviewer-facing meaning (`NEW_VENDOR` is computed in `anomaly.py` but reads as `vendor_memory`).

- [ ] **Step 4: Run and verify pass, then register it**

Run: `.venv/bin/python -m pytest tests/test_explain_flags.py -v` → 4 passed.
Then add the `ToolSpec` (`side_effect="read"`, `mcp_exposed=True`) and re-run `tests/test_no_write_tools_in_mcp.py`.

- [ ] **Step 5: `make check` and commit**

```bash
make check
git add src/invoice_agent/tools/explain.py src/invoice_agent/tools/registry.py tests/test_explain_flags.py
git commit -m "feat(m7): explain_flags narrates computed facts, in Vietnamese, with no model"
```

---

### Task 3: `spend_summary`, and search filters

The last read tool, plus the filter arguments `search_invoices` promises.

**Files:**
- Modify: `src/invoice_agent/db/queries.py` (add `spend_summary`, extend `list_invoices`)
- Modify: `src/invoice_agent/tools/registry.py`
- Test: `tests/test_queries_spend_summary.py` (create)

- [ ] **Step 1: Write the failing test**

Cover: grouping by `department` and by `category` produces totals that **sum to the same overall total**; an unknown `group_by` raises rather than silently grouping by something else; a period with no invoices returns an empty list, not a zero row that reads as real data. For `list_invoices`, cover the new `vendor` (seller tax code) and `min_amount` filters, including that `min_amount` is inclusive.

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_queries_spend_summary.py -v`

- [ ] **Step 3: Implement**

`spend_summary(conn, *, period, group_by)` returning `list[dict]` with the group key, invoice count and summed `total_amount`. `group_by` is validated against an explicit `{"department", "category"}` set — a free-form column name reaching a query is an injection surface even through SQLAlchemy's API.

Extend `list_invoices` with `vendor: str | None` (matched on `seller_tax_code`) and `min_amount: Decimal | None`. **Money stays `Decimal`** — this project measured amounts to 8 decimal places at M1 and floats are wrong at the last đồng.

- [ ] **Step 4: Verify, register, `make check`, commit**

```bash
make check
git add src/invoice_agent/db/queries.py src/invoice_agent/tools/registry.py tests/test_queries_spend_summary.py
git commit -m "feat(m7): spend_summary and the search filters the tool surface promises"
```

---

### Task 4: The MCP server, generated from the registry

**Files:**
- Create: `src/invoice_agent/mcp/__init__.py`, `src/invoice_agent/mcp/server.py`
- Modify: `pyproject.toml` (pin `fastmcp`), `requirements.lock`
- Test: `tests/test_mcp_server.py` (create)

**Interfaces:**
- Produces: `mcp.server.build_server() -> FastMCP`; entry point `python -m invoice_agent.mcp.server`

- [ ] **Step 1: Install and pin**

```bash
uv pip install fastmcp
.venv/bin/python -c "import importlib.metadata as m; print('fastmcp', m.version('fastmcp'), '| mcp', m.version('mcp'))"
```
Add `fastmcp==<measured version>` to `pyproject.toml` with a comment recording that it is load-bearing for M7 and that M0's `fastapi>=0.140` pin is what lets it coexist. Regenerate `requirements.lock` with `make lock`.

**Then verify the pins survived** — this is the check M0 said mattered:
```bash
.venv/bin/python -c "
import importlib.metadata as m
for p in ('fastapi','starlette','langgraph','langgraph-checkpoint-postgres','langfuse','litellm','sqlalchemy','psycopg','pydantic'):
    print(f'{p:32s} {m.version(p)}')
"
```
Expected: `fastapi 0.140.0`, `starlette 1.3.1`, `langgraph 1.2.9`, `langgraph-checkpoint-postgres 3.1.0`. **If any changed, stop and report** — a silent downgrade here breaks HITL durability, which is the property this project pins versions for.

- [ ] **Step 2: Write the failing test**

```python
"""The server is GENERATED from the registry, so the two cannot disagree.

A hand-written registration list would let a tool exist in one and not the other
— and the architecture test only reads the registry, so the drift would be
invisible to exactly the guard meant to catch it.
"""

def test_every_exposed_tool_is_registered_on_the_server():
    from invoice_agent.mcp.server import build_server
    from invoice_agent.tools import registry

    server = build_server()
    registered = set(_tool_names(server))
    expected = {spec.name for spec in registry.exposed_tools()}
    assert registered == expected


def test_no_unexposed_tool_leaks_onto_the_server():
    from invoice_agent.mcp.server import build_server
    from invoice_agent.tools import registry

    hidden = {spec.name for spec in registry.REGISTRY if not spec.mcp_exposed}
    registered = set(_tool_names(build_server()))
    assert registered & hidden == set()


def test_the_five_resources_are_registered():
    expected = {"invoice://{invoice_id}", "invoice-xml://{invoice_id}",
                "budget://{department}/{period}", "vendor://{tax_code}", "report://{period}"}
    ...
```

Write `_tool_names(server)` against the installed FastMCP's real API — **verify how to enumerate registered tools before relying on it**, the way M6-B verified LangGraph node introspection rather than assuming it.

- [ ] **Step 3: Implement `build_server()`**

Loop `registry.exposed_tools()` and register each; register the five resources. Each handler opens its own connection via `get_engine()` — the MCP process is not inside a request scope.

- [ ] **Step 4: Verify pass, then drive the server through a real MCP client**

Use the `mcp` client library in-process (stdio) to `list_tools()` and call `get_budget_status`. This is the first evidence that the surface works end-to-end rather than that the objects were constructed.

- [ ] **Step 5: Mutation — register a hidden tool**

Flip one `mcp_exposed` to `False` without changing the server.
Expected: `test_every_exposed_tool_is_registered_on_the_server` FAILS. Record, revert.

- [ ] **Step 6: `make check` and commit**

```bash
make check
.venv/bin/mypy src/invoice_agent/mcp/
git add src/invoice_agent/mcp/ pyproject.toml requirements.lock tests/test_mcp_server.py
git commit -m "feat(m7): the MCP server is generated from the registry"
```

---

### Task 5: `submit_approval_decision` — gated, and going through the existing seam

**Files:**
- Create: `src/invoice_agent/mcp/approvals.py`
- Modify: `src/invoice_agent/config.py`, `src/invoice_agent/tools/registry.py`
- Test: `tests/test_mcp_approval_gate.py` (create)

- [ ] **Step 1: Write the failing test**

Cover, each as its own test:
1. With `mcp_allow_approvals=False`, calling the tool **raises/refuses and no decision is recorded** — assert the `approvals` row is still undecided, not merely that an error was returned.
2. With the flag true but a **wrong actor token**, same.
3. With both correct, the decision is recorded **and the audit row's actor is `mcp:<client>`**.
4. The tool **calls `graphs.approval.submit_decision`** — monkeypatch it and assert it was invoked with the expected arguments. This is what stops a future implementation writing the DB directly.
5. An already-decided invoice is refused (the API returns 409 for this; MCP must not be a way around it).

- [ ] **Step 2: Run and verify failure**

- [ ] **Step 3: Implement**

Add to `config.py`:
```python
    # --- MCP -----------------------------------------------------------------
    # Design §8.2 permits exactly one human-authorised write over MCP. It ships
    # OFF: a tool that can approve invoices should require a deliberate act to
    # enable, not merely a client that knows the name.
    mcp_allow_approvals: bool = False
    mcp_actor_token: str = ""
    mcp_client_name: str = "claude-desktop"
```

pydantic-settings maps these from the environment as **`MCP_ALLOW_APPROVALS`**, **`MCP_ACTOR_TOKEN`** and **`MCP_CLIENT_NAME`** — those are the names Task 6's Claude Desktop config block must use, and the names to add to `.env.example`.

`src/invoice_agent/mcp/approvals.py` checks both gates, then calls `approval.submit_decision(...)` with `decided_by=f"mcp:{settings.mcp_client_name}"`. **It adds no other logic** — no status inference, no flag reading, no journal posting.

An empty `mcp_actor_token` with `mcp_allow_approvals=True` must **refuse**, not accept everything. A blank shared secret that authorises all callers is worse than no gate, because it reads as one.

- [ ] **Step 4: Verify pass, register the ToolSpec**

`side_effect="write"`, `mcp_exposed=True`, name exactly `submit_approval_decision` so it matches the allowlist. Re-run `tests/test_no_write_tools_in_mcp.py` — it must stay green, which is the allowlist doing its job.

- [ ] **Step 5: Mutations 3, 4 and 5**

Remove the `mcp_allow_approvals` check → gating test red. Remove the token check → gating test red. Replace the `submit_decision` call with a direct repository write → the transport-agnostic test red. Record each, revert each.

- [ ] **Step 6: `make check` and commit**

```bash
make check
git add src/invoice_agent/mcp/approvals.py src/invoice_agent/config.py src/invoice_agent/tools/registry.py tests/test_mcp_approval_gate.py
git commit -m "feat(m7): one human-authorised write over MCP, off by default"
```

---

### Task 6: Claude Desktop config, and a transcript that proves the gate question

**Files:**
- Create: `docs/mcp-claude-desktop.md`
- Create: `scripts/mcp_smoke.py`

- [ ] **Step 1: Write the config document**

The exact `claude_desktop_config.json` block, with the absolute interpreter path, the module entry point, and the env vars needed (`DATABASE_URL`; and the approval vars **shown commented out and off**, with a sentence saying why enabling them is a decision).

- [ ] **Step 2: Write `scripts/mcp_smoke.py`**

Drives the server over stdio through the `mcp` client and prints the answer to the gate's question — *"which department went over budget in July?"* — by calling `get_budget_status` / `spend_summary`. This is the reproducible half of the artefact: it proves the server answers, independent of anyone's Desktop install.

- [ ] **Step 3: Run it and capture the transcript**

```bash
.venv/bin/python -m scripts.mcp_smoke 2>&1 | tee /tmp/m7-mcp-smoke.txt
```
Paste the output into the note. It must show a real department, a real limit and a real overspend from the seeded data — not an empty list. **If the corpus has not been loaded into the database, load it first and say so**; a smoke test that returns nothing proves nothing.

- [ ] **Step 4: Commit**

```bash
make check
git add docs/mcp-claude-desktop.md scripts/mcp_smoke.py
git commit -m "docs(m7): Claude Desktop config, and a smoke test that answers the gate's question"
```

---

### Task 7: ADR 0006, the note, the roadmap, and the gate

- [ ] **Step 1: Write ADR `docs/adr/0006-no-nextjs-streamlit-is-the-ui.md`**

Context: roadmap 7.5 time-boxed Next.js to 4 days with a stopping rule. Decision: stop **before** the time-box. Reasoning: the roadmap's own assessment that it proves none of the 7 GĐ5 skills and is the biggest schedule risk; M5-D already ships a working Streamlit UI with an enforced HTTP-only boundary; deciding at the start is a decision, deciding at the end is a sunk cost. Consequences: the demo stays Streamlit through M8; the gate is met by its second branch; anyone wanting Next.js later has this as the record of why it was not built.

Write it as a decision, in the register §4's "intentionally NOT doing" list uses — not as an apology.

- [ ] **Step 2: `make check` from clean, and the keyless run**

```bash
make check 2>&1 | tail -5
env -u GROQ_API_KEY -u GROQ_API_KEYS -u GEMINI_API_KEY -u GEMINI_API_KEYS -u OPENROUTER_API_KEY \
  LLM_MODE=replay .venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Step 3: Write `docs/notes/2026-08-05-m7-mcp-surface.md`**

Sections, each with measured output pasted verbatim: the dependency verification (before/after versions, and that no pin moved); the registry and what it does **and does not** generate (§3.1's scope note); the five mutation results naming which test bit; the smoke transcript; the `invoice-xml://` PII limitation; and what was deliberately not built (Next.js, pgvector) with reasons.

- [ ] **Step 4: Update the roadmap**

Items 7.1–7.6; the Gate M7 table; §1's "Next" paragraph. State plainly that 7.5 was met by the **stopping-rule branch** and 7.6 was not done. Do not present either as an accident.

- [ ] **Step 5: Push and confirm CI green on the pushed SHA**

```bash
git push origin main
gh run watch "$(gh run list --limit 1 --json databaseId --jq '.[0].databaseId')"
```

- [ ] **Step 6: The gate is not met until Step 5 is green**

Five milestones in this project were declared closed on red or unverified commits. If CI is red, fix and re-push before writing any completion claim. Record the CI run URL in the note.

---

## Definition of done

| Criterion | Evidence |
|---|---|
| `test_no_write_tools_in_mcp.py` green | plus mutations 1 and 2 red |
| Allowlist is exactly `{submit_approval_decision}` | pinned by its own assertion |
| `create_journal_entry` absent from the registry entirely | mutation 2 |
| Server generated from the registry, not a second list | mutation on `mcp_exposed` |
| MCP answers the gate question | smoke transcript with real seeded data |
| Write tool gated, off by default, via `submit_decision` | mutations 3, 4, 5 red; audit shows `actor='mcp:<client>'` |
| `explain_flags` Vietnamese and LLM-free | test asserting no gateway call |
| No load-bearing pin moved | before/after version table |
| Next.js decision recorded | ADR 0006 |
| `make check` green on the pushed commit, CI agreeing | CI run URL on the closing SHA |
| Claude Desktop recording | **user's to capture** — config and smoke test supplied here |
