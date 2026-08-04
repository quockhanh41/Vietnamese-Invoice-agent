# Spec — M6-B: observability, cost cap, model attribution (plan items 6.5, 6.6)

> **Date**: 2026-08-04 · **Milestone**: M6-B · **Status**: design approved, implementation depth.
>
> Reads with the M6 parent spec
> [`2026-08-02-m6-security-observability-eval-design.md`](2026-08-02-m6-security-observability-eval-design.md) §4,
> roadmap `docs/Plan_Project_F_2026-07-25.md` §3 (M6), and design doc
> `docs/project-f-system-design.md` §2.9 (cost tracking), §9.2 (cost cap).
>
> Predecessor **M6-A closed 2026-08-02** with its negative-control criterion reported as **NOT met
> (22/24)**. M6-B does not revisit that number. Successor is **M6-C**, which cannot verify its
> single-rater claim until §3.6 here is wired.

---

## 0. Global constraints

From `CLAUDE.md` and roadmap §0. Not restated per section.

- **The LLM never toggles a flag and never posts a journal entry.** M6-B adds no agent field and
  changes no response model. Tracing is a read of what happened, never a write path.
- **Every non-obvious constant carries date + measurement + report file.** M6-B introduces two
  constants and pays one existing violation.
- **Measure first.** Both new thresholds are set from a measurement taken in this repo, not chosen.
- **A milestone is not closed until `make check` is green on the *pushed* commit and CI agrees.**
  Four milestones have failed this rule; M4-C failed it on a docs-only commit whose title made the
  breakage look impossible.

---

## 1. Why this exists, and what planning already found

Item 6.5 says "LangFuse `@observe` every node". Item 6.6 says "cost cap". Both are one-line roadmap
rows hiding four defects, all of which were found by reading the code rather than by running it.

### 1.1 Tracing is decided by import order — measured, and worse than "off"

> **Corrected during implementation, 2026-08-04.** The first version of this section claimed tracing
> "has been off in every process this project has ever run". **That was an overclaim**, produced the
> same way M6-A's retracted claim was: a single measurement generalised without checking what varied.
> The measured truth is below. Recorded rather than quietly rewritten, because the correction is the
> more interesting finding.

`observability.py:21` decides tracing from `os.getenv("LANGFUSE_PUBLIC_KEY")`, and the keys live in
`.env`. Nothing in this repo calls `load_dotenv()` — but **`litellm` does, on import**. So whether
`os.environ` carries the keys depends on whether anything has imported `litellm` yet, and
`TRACING_ENABLED` is a module-level constant fixed at first import of `observability`.

Measured 2026-08-04, working copy with a real pair and `LANGFUSE_TRACING_ENABLED=true` in `.env`:

```
fresh process, nothing imported     LANGFUSE_PUBLIC_KEY in os.environ: False
after `import litellm`              LANGFUSE_PUBLIC_KEY in os.environ: True
```

Which makes the old behaviour depend on who imports first:

| Entry point | `TRACING_ENABLED` (old code) |
|---|:---:|
| `invoice_agent.api.app` | **False** |
| `invoice_agent.graphs.batch` | **False** |
| `invoice_agent.graphs.approval` | **False** |
| `invoice_agent.graphs.enrichment` | **True** |

Three of four off, one on, decided by an import graph nobody was maintaining for this property. That
is worse than uniformly off: a setting that is silently on in one process and off in another cannot
be reasoned about at all, and "we have tracing" would have been true or false depending on which
module the process happened to reach first.

**A third fact makes both readings academic**: `grep -rn "observability" src/ scripts/` returns
**nothing**. `observe` has never had a single caller outside tests, so no node was traced under
either state. That is item 6.5's job and is exactly what M6-B does — but it means the decorator's
gating was never load-bearing until now, which is why the defect survived six milestones unnoticed.

**Item 6.5 implemented naively would decorate 24 nodes and emit nothing on 3 of 4 entry points**, and
the obvious test — "every node is traced" — would pass in exactly that state.

After the fix, measured at the same four entry points **and** with `observability` imported first:
`TRACING_ENABLED = True` in all five cases. The point is not merely that it is now on; it is that it
no longer depends on import order.

The confirming detail is in `tests/conftest.py`, whose docstring says the dev machine's real Langfuse
keys would otherwise "leak into the test session and fire traces at the cloud". Its mental model was
right — and under the old code the leak was real but *intermittent*, firing only when an import of
`litellm` preceded the first import of `observability`. The kill-switch it sets is what actually
prevented it, and §3.1 keeps that check first.

### 1.2 `cost_gate_node` reports success unconditionally

`graphs/batch.py:441-447` returns `{"status": "cost_gate_passed"}` regardless of outcome, while
`cost_gate_condition` (line 450) separately decides the route. **A rejected batch therefore finishes
reporting that it passed.** The status is the only thing an API caller or the UI can see.

### 1.3 The threshold is a bare magic number, and it is off by one

`cost_gate_condition` tests `len(inputs) > 10000` with no justifying comment — a live violation of
the constants rule. `>` also means a batch of **exactly** 10,000 files *passes*, while design §9.2
names 10,000 as the rejection case: *"Upload 10.000 file → từ chối **trước khi** gọi LLM lần nào."*

### 1.4 `llm_calls.trace_id` has never had a writer

`telemetry.record()` accepts `trace_id` and `db/models.py:438` carries the TODO in the column's own
comment: *"written by nothing until M6.5 wires Langfuse"*. This is M6.5.

---

## 2. Scope

### 2.1 In

- `traced()` in `observability.py` — marker attribute + delegation to the real `observe`.
- All 24 graph nodes decorated; a structural test that is meaningful with **and** without keys.
- The `.env` → `os.environ` gap closed, so configured keys actually enable tracing.
- `trace_id` written into `app.llm_calls`.
- Batch cost cap: honest rejection status, threshold in `config.py`, boundary corrected.
- Per-invoice token budget → `manual_queue`, with a **measured** default.
- Model attribution in `scripts/eval_classification.py`.

### 2.2 Out

- **Self-hosting Langfuse.** Roadmap §2.1 already decided this: v3 self-host needs ClickHouse + Redis
  + MinIO, four services to trace one agent.
- **Tracing anything that is not a graph node.** Repository functions, the API layer and the UI stay
  untraced. The unit of work this project reasons about is the node; per-function tracing would make
  the 24-node claim unfalsifiable by making "node" mean nothing.
- **Changing any agent response model, prompt, or the `wrap()` boundary.** M6-A's numbers were
  measured against those exact surfaces two days ago. Moving one invalidates a security measurement
  to add an observability feature.
- **Cost *enforcement* below the batch and per-invoice level.** No per-day budget, no spend circuit
  breaker. Not asked for, and there is no measurement to set one from.
- **Re-measuring anything M6-A published.** The 22/24 negative control stands as reported.

---

## 3. Design

### 3.1 Closing the `.env` gap — and why `observability.py` stops being a verbatim copy

M0.4 recorded `observability.py` as copied **verbatim** (41 lines, unchanged) from Project A. That
property is now load-bearing in the wrong direction: verbatim is why it reads `os.environ`, and
`os.environ` is why it is off.

The same document already records the precedent. M0.4 ported `gateway.py` to take `Settings`
*instead of* calling `os.getenv` itself, for exactly this defect class, and roadmap §1 lists
"`os.getenv` scattered → one `config.py`" as an inherited debt marked **paid**. `observability.py` is
the one file where it was never actually paid.

**Decision: pay it, and record that the verbatim property is deliberately given up.**

The constraint that shapes the fix is that `TRACING_ENABLED` and the `observe` binding are both
resolved at **import time**. A function called later cannot retroactively re-decorate 24 nodes. So
the settings read must happen inside `observability.py`, before it decides:

```python
def _tracing_enabled() -> bool:
    if os.getenv("LANGFUSE_TRACING_ENABLED", "true").strip().lower() == "false":
        return False
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        return True
    # `.env` is read by pydantic-settings and by nothing else, so a key
    # configured there never reaches os.environ. Measured 2026-08-04: with a
    # valid pair in `.env`, TRACING_ENABLED was False and every trace this
    # project ever emitted was a no-op. See spec §1.1.
    settings = get_settings()
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)
```

Three properties this must preserve, each with a test:

| Property | Why it matters |
|---|---|
| The `LANGFUSE_TRACING_ENABLED=false` kill-switch still wins over a configured key | `conftest.py` relies on it to keep the suite off the network. If the settings fallback ran first, **every test run would start firing traces at the dev's real Langfuse project** — the exact leak `conftest.py` was written to prevent. |
| `os.environ` still wins over `.env` | Ordinary precedence; CI sets env vars and must not be overridden by a checked-in file. |
| No import cycle | `config.py` imports nothing from `observability.py`. Verified before implementing, not assumed. |

The kill-switch test is the sharp one: it is checked **first**, before the settings fallback, and a
mutation that reorders them must turn it red.

### 3.2 `traced()` — a marker that survives a keyless CI

CI has no keys, so `observe` is identity, so a test asserting "every node is traced" passes on a
completely unwired codebase. That is the guard-that-guards-nothing pattern **every** milestone review
in this repo has found (roadmap §1 lists six instances).

`traced()` sets a marker attribute on the function *and* delegates to the real `observe`:

```python
TRACED_ATTR = "__invoice_agent_traced__"

def traced(*, node: str, agent: str | None = None):
    """Mark a graph node as traced, and trace it.

    The marker is set whether or not tracing is enabled, because CI has no keys
    and `observe` is the identity decorator there — a structural test that
    could only see the real decorator would pass on an unwired codebase.
    """
    def decorate(fn):
        wrapped = observe(name=node)(fn)
        setattr(wrapped, TRACED_ATTR, {"node": node, "agent": agent})
        return wrapped
    return decorate
```

**The structural test enumerates the compiled graphs, not a hand-written list.** A list of node names
in the test is a second source of truth that drifts silently; asking the graph is what makes "all 24"
true by construction. The three `build_*_graph()` functions already return compiled graphs, so no
signature changes to reach them.

The traversal, verified against `langgraph==1.2.9` on 2026-08-04 rather than assumed:

```
graph.nodes[name].bound.func        -> the callable add_node received
    functools.partial? -> .func     -> the underlying function carrying the marker
```

Two traps on that path, both measured:

- **The partial trap.** `vision_extract`, `classify` and `audit` are registered as
  `functools.partial(...)` objects (`enrichment.py:287-292`), so the marker is **not** visible on
  what `add_node` receives — `bound.func` returns the partial itself. Without the unwrap the test
  silently passes on 3 of 8 enrichment nodes, and they are the three that matter most: the only
  nodes in the graph that call an LLM.
- **`__start__` is in `graph.nodes`.** LangGraph adds it, so enrichment reports **9** keys for 8
  nodes. A count assertion written against the raw dict would expect 25, not 24, and the natural
  "fix" — adjusting the expected number until it passes — hides the pseudo-node instead of excluding
  it. The test filters `__`-prefixed keys and asserts the counts per graph (8 / 9 / 7).

The 24 nodes, from the three builders:

| Graph | N | Nodes |
|---|:---:|---|
| enrichment | 8 | `route_source`, `parse_xml`, `vision_extract`*, `validate`, `classify`*, `audit`*, `signals`, `manual_queue` |
| batch | 9 | `ingest`, `cost_gate`, `fan_out`, `enrich_one`, `serial_join`, `route_outcomes`, `auto_post`, `open_approval`, `finalize` |
| approval | 7 | `load_snapshot`, `suggest`, `approval_gate`, `apply_decision`, `post_entry`, `record_rejection`, `learn` |

`*` = registered as a partial.

Tags per design §2.9 and roadmap 6.5: `invoice_id`, `batch_id`, `node`, `agent`. Cost and shadow cost
as metadata.

### 3.3 The HITL risk, and the test that covers it

`approval_gate` contains `interrupt()`, which works by **raising `GraphInterrupt`**. M1.5 measured
that this node's body runs **twice** across a resume. Wrapping it in a decorator that catches,
suppresses, or re-raises a different exception type would break Human-in-the-Loop durability — the
one property this project pins its LangGraph version for (roadmap §0, principle 6).

**Requirement: `test_hitl_survives_restart.py` must be green with tracing ENABLED**, not only in its
usual keys-absent state. Running it only in the default configuration would test the identity
decorator and prove nothing about the real one. This is a run-condition on an existing test, not a
new test.

### 3.4 `trace_id` into `llm_calls`

`_persist_cost` (`enrichment.py:133`) is the single call site of `telemetry.record` in `src/`. It
reads the active trace id when tracing is enabled and passes `None` otherwise:

```python
from langfuse import get_client
trace_id = get_client().get_current_trace_id() if TRACING_ENABLED else None
```

Verified 2026-08-04 that `get_current_trace_id()` exists on the installed SDK v3 client. It must not
be able to fail the run: telemetry that can kill an invoice is worse than telemetry that
under-reports — the rule `gateway._count_tokens` already follows.

Evidence for the gate is **non-null `trace_id` rows under a keyed run**, which by construction cannot
be produced by CI. It is measured locally and the transcript goes in the note.

### 3.5 Cost cap — two layers

**Batch cap.** Three changes, all in `graphs/batch.py` and `config.py`:

1. `cost_gate_node` computes the decision and reports it, so `status` is `cost_gate_rejected` when
   the batch is refused. `cost_gate_condition` reads the same predicate — one function, called
   twice, not two copies of a rule.
2. The threshold moves to `config.py` as `max_batch_files`, with a comment carrying design §9.2 as
   its justification.
3. The boundary becomes `>=`, so exactly 10,000 rejects.

**The test asserts zero rows in `app.llm_calls` after a rejected batch**, not a status string. Design
§9.2's requirement is "before any LLM call", and a status can be correct while a call has already
happened. Counting rows is the measurable form of the claim; asserting the string is the form that
looks right and proves nothing.

**Per-invoice token budget.** Placement is the whole design question. The candidates:

| Placement | Verdict |
|---|---|
| Inside `classify_node` | **Wrong.** `classify → audit` is an unconditional edge, so a node that decided to give up could not route to `manual_queue` without new edges. |
| A new node before `classify` | Rejected. A 25th node to hold one comparison, and it changes the graph shape M3-C measured. |
| In `route_after_validate` | **Chosen.** It already returns `"manual_queue"`, so no new edge is needed; it is a pure function of state; and it is directly unit-testable. |

The estimate must count **the messages that would actually be sent** — system prompt, wrapped invoice
text, and `_schema_instruction`'s JSON schema — not just `describe()`. The schema hint is a large
constant fraction of a short prompt, so estimating without it would under-count every invoice by
roughly the same amount and make the threshold meaningless. A helper in `llm/` builds the same
messages `classify()` builds and counts them with `litellm.token_counter`, per model, because the
tokenizers differ (M3-A measured 26 vs 34 tokens for the same Vietnamese sentence).

**The default is measured, not chosen.** Spec §7.2 of the parent left this open with "there is no
measurement to derive one from yet". There is now: the estimator runs over all 285 corpus XML
invoices before the threshold is written, and the constant is set from the observed distribution with
date + measurement + report file in its comment, per the constants rule. A round number would be a
new violation of the rule this milestone is paying an old violation of.

### 3.6 Model attribution

Smaller than roadmap §1 implies. `scripts/eval_classification.py` **already** passes `calls=` to both
`classify()` and `audit()` (lines 115-117) — the parent spec §1.2's claim that it "simply never
passes one" is out of date. What is missing:

- the per-row `model`, so an invoice's classification is attributable;
- a `telemetry.record` write, so `GROUP BY llm_calls.model` has anything to group.

Both are additive. Neither touches a response model, so the security boundary does not move.

This is what makes M6-C's single-rater claim **verifiable rather than asserted**, which is the entire
reason it is in M6-B and not in M6-C.

---

## 4. Tests and mutation verification

Every review in this project has found a test that passed while guarding nothing. Each mutation below
must turn a **named** test red, and the note records measured output — which suite, which assertion,
what it printed — not the word "RED".

| # | Mutation | Must turn red |
|---|---|---|
| 1 | Remove `@traced` from one node (`serial_join`) | the 24-node structural test |
| 2 | Stop unwrapping `.func` in that test | the test itself must fail to see 3 partial-registered nodes — verified by also removing `@traced` from `classify` |
| 3 | Reorder `_tracing_enabled` so the settings fallback precedes the kill-switch | the kill-switch test |
| 4 | Revert `>=` to `>` in the batch threshold | the exactly-10,000 boundary test |
| 5 | Make `cost_gate_node` return `cost_gate_passed` unconditionally again | the rejection-status test |
| 6 | Let the cost gate run after fan-out instead of before | the zero-`llm_calls`-rows test |
| 7 | Drop the schema hint from the token estimate | the estimator's agreement-with-real-prompt test |

Mutation 2 is the one worth stating plainly: it verifies **the test's own coverage**, not the
production code. It exists because the partial trap is invisible in a passing run, and a test that
cannot see 3 of 24 nodes is worse than no test — it certifies them.

Existing suites that must stay green, and are part of this gate rather than assumed:
`test_hitl_survives_restart.py` (with tracing on, §3.3), `test_prompt_injection.py`,
`test_agent_schema_boundary.py`, `test_never_post_without_approval.py`.

---

## 5. M6-B gate

| Criterion | Evidence required |
|---|---|
| All 24 nodes traced | structural test over the three builders, partials unwrapped, mutations 1 and 2 red |
| Tracing actually enables from configured keys | measured before/after of `TRACING_ENABLED` with keys in `.env` only |
| Kill-switch still wins | test green, mutation 3 red |
| HITL durable with tracing ON | `test_hitl_survives_restart.py` green at `HITL_RUNS=10` with keys present |
| `trace_id` written | non-null rows in `app.llm_calls` under a keyed run, transcript in the note |
| Cost cap 1/1 | 10,000-file batch rejected with **zero** `llm_calls` rows; mutations 4, 5, 6 red |
| Token budget routes to manual queue | test with an oversized invoice; threshold set from the 285-invoice distribution, comment carries date + measurement + report file |
| Model recorded per call | `GROUP BY app.llm_calls.model` returns rows for an eval run |
| `make check` green on the pushed commit, CI agreeing | CI run URL on the closing SHA |

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Enabling tracing fires traces at the dev's real Langfuse project from the test suite | `conftest.py` already sets the kill-switch and pops both keys; §3.1 keeps the kill-switch checked **first** and mutation 3 pins the ordering |
| `@observe` interferes with `GraphInterrupt` and breaks HITL | §3.3 makes the existing durability test a gate criterion **with tracing on**, rather than trusting the decorator |
| Langfuse network calls slow every node in a live batch | SDK v3 batches and flushes in the background. If a live batch regresses against M5-B's measured 20.4s, report it and gate tracing behind the existing kill-switch rather than removing the wiring |
| The token-budget default is set from a corpus that is synthetic | Stated as a limitation in the comment. The corpus is what exists; a number derived from it and labelled as such beats a round number labelled as nothing |
| Giving up the verbatim `observability.py` looks like unnecessary churn | §3.1 records the measurement that forced it and the `gateway.py` precedent, in the same commit as the change |

---

## 7. Open questions — recorded, not silently dropped

1. **Should the API and UI layers be traced later?** Out of scope here (§2.2). If M7's MCP server
   needs request-level traces, the unit of tracing gets revisited then, with a reason.
2. **Does `enrich_one` produce a usable nested trace under `Send` fan-out?** LangGraph runs fan-out
   tasks concurrently and the OTel context may or may not propagate into them. If nesting is flat,
   that is reported as a measured limitation rather than worked around — the `batch_id` tag still
   correlates the calls.
3. **Per-invoice token budget for the vision path** is not covered: a vision call's cost is dominated
   by image tokens, which `litellm.token_counter` does not estimate for a data URI. Left to M6-C's
   vision re-measurement, where the real numbers appear.
