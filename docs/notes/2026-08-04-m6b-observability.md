# M6-B — Observability, Cost Cap, and Model Attribution: Measurement Record

Date: 2026-08-04 (closed 2026-08-05)
Roadmap items 6.5 and 6.6. Spec `docs/superpowers/specs/2026-08-04-m6b-observability-cost-cap-design.md`.

Nine tasks, each with its own mutation. Two of the milestone's own planning documents turned out to
be wrong and were corrected in-tree. Two tests turned out to be guarding nothing and were rebuilt.
One published percentage turned out to be right by luck while its companion figure was wrong. All of
that is below, because the alternative is a note that reads like the work went smoothly, and it did
not.

---

## 1. The tracing gap: `@observe` had no callers, and the flag was decided by import order

### 1.1 Nothing imported the module

`observability.py` was written at M0.4 as a verbatim copy from Project A. Before this milestone,
nothing in `src/` or `scripts/` imported it:

```
$ git grep -n "from invoice_agent.observability\|from invoice_agent import observability\|@observe" 6977dda^ -- src scripts
6977dda^:src/invoice_agent/observability.py:5:the real langfuse @observe decorator and registers LiteLLM's langfuse_otel
6977dda^:src/invoice_agent/observability.py:7:auto-nest under the active @observe span via the shared OpenTelemetry
6977dda^:src/invoice_agent/observability.py:8:context, including failed 429/fallback attempts). Otherwise @observe is a
6977dda^:src/invoice_agent/observability.py:35:        """Identity no-op decorator: supports @observe and @observe(...)."""
```

Every hit is inside the file's own docstring or its own no-op branch. **`@observe` decorated nothing,
anywhere, for six milestones.** That is why the defect below survived so long: a flag nobody reads
cannot be observed to be wrong.

### 1.2 The flag was not "off" — it was import-order dependent

The spec's §1.1 originally claimed tracing was off in *every* process. That was an overclaim,
generalised from a single measurement — the same shape of error M6-A had to retract about the LLM
choosing which budget is checked — and it was corrected in the same commit as the fix (`6977dda`).

The real mechanism: the old `_tracing_enabled()` read `os.environ` and nothing else. Nothing in this
project calls `load_dotenv()` — but **`litellm` calls it on import**. So whether `.env`'s Langfuse
keys had reached `os.environ` by the time `observability` was first imported depended entirely on
which module got there first.

Reproduced on the old predicate (2026-08-05, valid key pair in `.env`, keys absent from the shell):

```
$ for m in api.app graphs.batch graphs.approval graphs.enrichment; do ... import $m, then read TRACING_ENABLED ...

invoice_agent.api.app              False
invoice_agent.graphs.batch         False
invoice_agent.graphs.approval      False
invoice_agent.graphs.enrichment    True
```

**Three of four entry points off, one on.** Not a uniform failure — a nondeterministic one, which is
strictly worse to diagnose. Whichever process happened to import `litellm` before `observability`
got tracing; the rest silently did not.

### 1.3 After the fix

`_tracing_enabled()` now checks the kill-switch first and unconditionally, then `os.environ`, then
falls back to `Settings` (which reads `.env` deliberately rather than by accident). Same measurement
on the shipped code:

```
invoice_agent.api.app              True
invoice_agent.graphs.batch         True
invoice_agent.graphs.approval      True
invoice_agent.graphs.enrichment    True
(bare import, no graph first)      True
```

All four, plus a bare import with no graph loaded at all. The ordering matters and the file says so:
consulting `Settings` *before* the kill-switch would fire every test run's spans at the developer's
real Langfuse project — see §9.1, where exactly that was happening by a different route.

This is why `observability.py` is **no longer the verbatim M0.4 copy**, and the module docstring
records that: verbatim is precisely what kept it reading `os.environ` only. `gateway.py` had already
been ported off `os.getenv` onto `Settings` at M0.4 for the same reason; this file was missed.

---

## 2. 24 nodes traced

```
enrichment  8 nodes  traced=8
batch       9 nodes  traced=9
approval    7 nodes  traced=7
total       24
```

Four tags are carried. Two come from the decorator (`node` always; `agent` only where an agent
exists — `classify` → `ClassificationAgent`, `audit` → `AuditorAgent`, the only two `agent=`
arguments in the codebase), and two are read out of graph state at call time (`invoice_id`,
`batch_id`). `tag_current_span` drops `None` values, no-ops entirely when tracing is off, and wraps
the Langfuse call in `try/except` — **tagging cannot fail a node**, pinned by
`test_tagging_never_breaks_a_node`.

### 2.1 Why it is safe to wrap all 24

A decorator in front of every node is only acceptable if it is invisible to everything that
introspects the node. Measured before decorating anything:

```
$ .venv/bin/python -c "
import inspect
from invoice_agent.observability import traced
def node(state, *, gateway=None):
    return {}
wrapped = traced(node='probe')(node)
print('signature :', inspect.signature(wrapped))
print('__wrapped__ present:', hasattr(wrapped, '__wrapped__'))
"
signature : (state, *, gateway=None)
__wrapped__ present: True
```

`inspect.signature` reports the original `(state, *, gateway=None)`, not `(*args, **kwargs)`. LangGraph
binds `gateway` through `functools.partial` on three nodes; a decorator that erased the keyword-only
parameter would have broken them at graph-build time.

### 2.2 The marker exists so a keyless CI can verify this

CI has no Langfuse keys, so `observe` there is the identity decorator. A structural test that could
only see the *real* decorator would pass on a completely unwired codebase. `traced()` therefore sets
`TRACED_ATTR` unconditionally, and `test_every_node_in_every_graph_is_traced` reads the marker, not
Langfuse. Mutation 2 below is the proof that this test can actually see through the partial wrapper.

---

## 3. Mutation results

Numbering follows the plan. Every mutation was applied by hand, run, and reverted; each revert was
verified by `diff` against a pre-mutation backup and by re-running the affected suite.

| # | Mutation | Test that bit | Measured output | Reverted |
|---|---|---|---|---|
| 1 | Remove `@traced(node="serial_join")` from `batch.py` | `test_node_tracing.py::test_every_node_in_every_graph_is_traced` | `AssertionError: nodes registered without @traced: ['batch.serial_join']` | Yes, diff empty |
| 2 | Remove the `functools.partial` unwrap in `_node_functions` **and** one decorator | same test | `AssertionError: nodes registered without @traced: ['enrichment.vision_extract', 'enrichment.classify', 'enrichment.audit']` | Yes, diff empty |
| 3 | Move the `Settings` block **above** the kill-switch check | `test_observability.py::test_kill_switch_wins_over_keys_configured_in_dotenv` | `assert True is False` — `_tracing_enabled()` returned `True` with `LANGFUSE_TRACING_ENABLED=false` | Yes, `git status` clean |
| 4 | `>=` → `>` in `batch_is_oversized` | `test_batch_cost_gate.py::test_exactly_ten_thousand_files_is_rejected` | `AssertionError: assert False is True` | Yes |
| 5 | `cost_gate_node` returns `"cost_gate_passed"` unconditionally | `test_batch_cost_gate.py::test_a_rejected_batch_does_not_report_that_it_passed` | `AssertionError: assert 'cost_gate_passed' == 'cost_gate_rejected'` | Yes |
| 6 | `ingest → fan_out` directly, `cost_gate` moved after `serial_join` | `test_batch_cost_gate.py::test_a_rejected_batch_makes_no_llm_call` | **First attempt: NOT verified** (see §3.1). After the test was rebuilt: `AssertionError: 5 LLM calls made by a rejected batch` / `assert 5 == 0` | Yes, diff against HEAD empty |
| 7 | Drop the JSON schema hint from `classify_messages` | `test_llm_budget.py::test_the_estimate_matches_the_messages_the_gateway_really_sends` | `assert [...] == [...]` — diverges at index 1, exactly at the schema tail | Yes |

Mutation 3 was re-run independently at closing time rather than carried forward from Task 1's ledger
entry, because Task 1 predates the SDD reporting and left no report file of its own. The output above
is from that re-run.

Mutation 2 deserves a note on why it is not tautological. It removes the `partial` unwrap **and only
one** of three decorators, and the test then reports **all three** partial-wrapped nodes as untraced.
That asymmetry is the point: it proves the test is reading through `functools.partial` rather than
being satisfied by whatever the graph happens to hand it.

### 3.1 Mutation 6 was reported as NOT verified before it was fixed

The first cut of `test_a_rejected_batch_makes_no_llm_call` built its 10,000 inputs as bare
`{"path": f"{i}.xml"}` dicts — cheap on purpose, because 10,000 of them. Under the mutated topology
`enrich_one`'s first line, `invoice_id = task["invoice_id"]`, sits **outside** its own `try/except`,
so the test went red with `KeyError: 'invoice_id'` **before any LLM call was reachable at all**.

Red for a schema mismatch is not red for the property. That is precisely the M6-A Mutation B failure
mode — a red from a changed cache key reported as a verified security assertion — and it was
**reported as not verified rather than counted**.

A separate uncommitted diagnostic confirmed the underlying property was real (two real corpus
invoices, `max_batch_files` monkeypatched to 1, mutated topology): `status: cost_gate_rejected` while
`app.llm_calls` grew by 7 rows. So the mutation's design was sound; the fixture could not exercise it.

The test was then rebuilt on one real corpus invoice under `LLM_MODE=replay` with `max_batch_files`
monkeypatched to 1 — the literal 10,000 boundary stays covered by
`test_exactly_ten_thousand_files_is_rejected`. Re-running the same mutation now gives:

```
FAILED tests/test_batch_cost_gate.py::test_a_rejected_batch_makes_no_llm_call
    assert after == before, f"{after - before} LLM calls made by a rejected batch"
E   AssertionError: 5 LLM calls made by a rejected batch
E   assert 5 == 0
```

The failure message **is** the property, measured as a row count.

The unmutated direction was checked structurally too, rather than inferred from the test being fast —
`graph.stream(..., stream_mode="updates")` against the real graph:

```
nodes that ran, in order: ['ingest', 'cost_gate']
```

`fan_out` and `enrich_one` never appear.

---

## 4. HITL durability with tracing genuinely on

### 4.1 The plan's own measurement command was a silent no-op

The plan told the implementer to set `LANGFUSE_TRACING_ENABLED=true` plus real keys on the shell and
run pytest. `tests/conftest.py` sets `LANGFUSE_TRACING_ENABLED=false` and pops both keys at
collection time — **before any test module, and therefore before `observability` is imported**. Shell
variables never win. Confirmed directly:

```
$ LANGFUSE_TRACING_ENABLED=true LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=... \
  .venv/bin/python -c "
import sys; sys.path.insert(0, 'tests'); import conftest
from invoice_agent import observability
print('TRACING_ENABLED:', observability.TRACING_ENABLED)
"
TRACING_ENABLED: False
```

Run literally, the command returns `11 passed` in ~8.6s and looks like a clean tracing-on result. It
is the tracing-**off** baseline wearing a tracing-on command line. Reporting it would have been a
fabricated measurement. The plan was corrected in-tree (`fbbb7dc`) rather than left to mislead the
next reader.

A genuine reading required temporarily neutralising conftest's three env-forcing lines for one run,
reverted immediately and verified clean by `git diff` before anything else proceeded.

### 4.2 The gate criterion, measured

```
$ HITL_RUNS=10 .venv/bin/python -m pytest tests/test_hitl_survives_restart.py -v   # tracing OFF
11 passed in 8.03s

$ LANGFUSE_TRACING_ENABLED=true LANGFUSE_PUBLIC_KEY=<redacted> LANGFUSE_SECRET_KEY=<redacted> \
  HITL_RUNS=10 .venv/bin/python -m pytest tests/test_hitl_survives_restart.py -v   # tracing ON
11 passed in 24.57s
```

**10/10 subprocess runs pass with the real decorator active**, each crossing a `kill`/resume boundary
with a real `GraphInterrupt` propagating out of `approval_gate`. That is the gate criterion and it
holds. (11 collected, 10 of which spawn a child; `test_ci_runs_the_full_ten` only asserts
`HITL_RUNS >= 10` under `CI`.)

### 4.3 The 3x is a cold start, not a tracing tax — and steady state is UNMEASURED

The first write-up of this attributed the 24.57s to "24 traced nodes across enrichment + batch +
approval." **That was wrong on both counts.** This test only ever builds the **approval** graph —
7 `add_node` calls in `tests/helpers/hitl_child.py`, and neither that file nor the test references
enrichment or batch anywhere.

Isolating measurement, using `HITL_RUNS=1` (1 spawn) against `HITL_RUNS=10` (10 spawns) so that
shared session-level overhead cancels:

```
tracing OFF, N=1: 1.13s      tracing OFF, N=10: 8.03s
tracing ON,  N=1: 5.11s      tracing ON,  N=10: 24.57s

marginal_off = (8.03 - 1.13) / 9 = 0.767s per additional spawn
marginal_on  = (24.57 - 5.11) / 9 = 2.162s per additional spawn
delta         = 1.396s ≈ 1.40s per child subprocess
```

~1.40s of **fixed per-subprocess Langfuse client cold start** — a new process authenticating a new
client each time. In a long-lived process that cost is paid once and amortises away.

Two things follow, and both are stated as limits rather than smoothed over:

- This is **not** comparable to M5-B's 20.4s live 30-invoice batch, which ran in one continuous
  process.
- **Steady-state per-node tracing overhead in a long-lived process is UNMEASURED.** No number is
  offered for it. Recording it as unmeasured is the honest option; estimating it is not.

---

## 5. `trace_id` gets its first writer

`app.llm_calls.trace_id` has existed since M2-A with a comment saying "written by nothing until M6.5
wires Langfuse." It now has a writer: `enrichment._persist_cost` passes
`trace_id=current_trace_id()` into `telemetry.record`, and attaches both money figures as span
metadata.

One invoice end to end, tracing confirmed live (`TRACING_ENABLED: True`) before the run:

```
$ docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c \
  "SELECT node, model, trace_id IS NOT NULL AS has_trace FROM app.llm_calls ORDER BY id DESC LIMIT 5"

   node   |            model             | has_trace
----------+------------------------------+-----------
 audit    | groq/openai/gpt-oss-120b     | t
 audit    | gemini/gemini-3.5-flash      | t
 classify | groq/llama-3.3-70b-versatile | t
 classify | groq/llama-3.3-70b-versatile | f
(4 rows)
```

The 3 newest rows are the run's own (`llm calls: 3` from the script's output), all non-null. The 4th
predates it.

> **No raw trace id, key, or digest appears in this note, and the query above asks for
> `trace_id IS NOT NULL` rather than the value.** M6-A's note failed CI's `secrets` job on run
> `30734144866` because a 16-hex *prompt digest* — a hash of a prompt, not a credential — matched
> gitleaks' `generic-api-key` rule. A document describing observability should not be the thing that
> breaks the secret scan.

### 5.1 Both money figures, and why there are two

```
actual_cost_usd -> Decimal('0')        -> "0"
shadow_cost_usd -> Decimal('0.00027')  -> "0.00027"
```

Attached as `str(Decimal)`, never float — a JSON metadata payload would otherwise coerce money to
binary floating point, which this project does not do anywhere. Actual is `$0` because the run is on
free tiers; shadow is what the same tokens would cost at paid rates. That contrast is design §2.9's
entire reason for two columns, and the test would fail if shadow were also zero, so it cannot pass on
a code path that computes nothing.

---

## 6. Cost cap

One predicate, `batch_is_oversized`, now serves both `cost_gate_node` and `cost_gate_condition`. Two
copies of the same rule is how "a rejected batch reported that it passed" arose in the first place.

Three defects fixed together:

| Defect | Before | After |
|---|---|---|
| The node lied about its own outcome | `cost_gate_node` returned `"cost_gate_passed"` unconditionally | branches on the shared predicate |
| The rule existed twice | router carried its own inline `> 10000` | router delegates to the predicate |
| The boundary was off by one | `> 10_000`, so exactly 10,000 files **passed** — the literal case design §9.2 names | `>= max_batch_files`: 10,000 rejects, 9,999 passes |

`max_batch_files = 10_000` lives in `config.py` with a comment that says outright it is a
**design-derived denial-of-wallet ceiling, not a measurement** — the largest batch this project has
ever run is 30 invoices (M5-B, 2026-08-01). Per the project rule that every non-obvious constant
carries its date, measurement and source, the honest thing to record here is the *absence* of a
measurement.

The gate criterion — zero LLM calls from a rejected batch — is measured as an `app.llm_calls` row
count, not a status string, and is proven both ways: green because only `['ingest', 'cost_gate']`
execute (§3.1), red under the topology mutation with `5 LLM calls made by a rejected batch`.

---

## 7. Token distribution — and why it does not mean what it looks like

`llm/budget.py` reconstructs the classify prompt and estimates it before it is spent. It is pinned to
the **real** prompt by exact message-list equality against what actually reaches
`litellm.completion`, not by an approximation — which is what Mutation 7 verifies.

Full corpus sweep, `groq/llama-3.3-70b-versatile`, every `source_type == "xml"` row:

```
rows examined: 285
n       : 285
errors  : 0
min     : 424
median  : 431
mean    : 432.1
p95     : 440
p99     : 450
max     : 456
```

Percentiles are **nearest-rank**, matching how the measurement was taken and the comment already
committed in `config.py`. Linear interpolation gives p95 439.8 / p99 445.8; mixing the two
conventions in one document is how a figure becomes unreproducible, so only nearest-rank appears
here.

### 7.1 The caveat is larger than the measurement

Two facts, found by review rather than by the measurement, gut the obvious reading of that table:

1. **~89% of every prompt is fixed overhead.** `SYSTEM_PROMPT` (161 tokens) + the JSON schema
   instruction (221 tokens) = **382 identical tokens on every single call**.
2. **The corpus has no size variance at all.** `Counter` over `InvoiceData.lines` across all 285 XML
   invoices returns `Counter({1: 285})` — every one has exactly one line item, verified against
   `scripts/generator`.

So the 424–456 spread measures "fixed overhead plus one short line." It is **not** a characterisation
of how large a real invoice gets. A max-derived threshold presented as a general size ceiling would
have been a number that looks measured and is not.

### 7.2 Real fixtures, which are more varied than the generator

| fixture | lines | tokens |
|---|---|---|
| `dienluc_gtgt_cccd_2.1.0.xml` | 1 | 470 |
| `hokinhdoanh_banhang_qrcode_2.1.0.xml` | 2 | 435 |
| `ktx_banhang_cccd_2.1.0.xml` | 1 | 457 |
| `lienket_gtgt_7dong_2.1.0.xml` | 7 | **522** |
| `newimage_gtgt_pban2.0.1.xml` | 1 | 434 |
| `phongvu_gtgt_tdiep_2.0.0.xml` | 1 | 470 |
| `viettelpost_gtgt_2.1.0.xml` | 1 | 459 |
| `vnpt_hoadon_banhang_2.1.0.xml` | 1 | 442 |

**5 of the 8 real fixtures exceed the synthetic corpus's max of 456**, and **4 of them are
single-line invoices** (`dienluc` 470, `ktx` 457, `phongvu` 470, `viettelpost` 459). Real invoice text
is simply wordier than the generator's. An intermediate report said "six of eight single-line
fixtures exceed 456" — that was a miscount, it was caught in review, and it is corrected here rather
than propagated.

### 7.3 The threshold

`max_invoice_prompt_tokens = 2000` — the corpus max of 456 × 3, rounded up to the next thousand. **The
3× is headroom, not a measurement**, and `config.py` says so in those words. The gate exists to catch
a pathological invoice (a parser fault, or a description field used as a payload carrier), so it must
sit clear of everything real while still being a bound.

Expressed in a unit a reviewer can check: fitting `tokens = intercept + slope × n_lines` by expanding
the 7-line real fixture's own content gives ≈13.0 tokens per line on a ≈431-token intercept, so 2000
accommodates roughly **120 line items** — against a real corpus that peaks at 7 and a synthetic one
fixed at 1.

Corpus gate, run over all 285:

```
over budget: 0 []
threshold: 2000
```

Zero trip it. A non-zero count would have meant the constant was mis-derived. An oversized invoice
routes to `manual_queue` — a human — via `route_after_validate`; no new node, no new edge.

---

## 8. Model attribution — and a number that was right by accident

The eval harness now records which model produced each classification, into `app.llm_calls` with
`agent` set. Raw `GROUP BY agent, model` over one full 285-invoice replay run:

```
        agent        |            model             | count
---------------------+------------------------------+-------
 AuditorAgent        | gemini/gemini-3.5-flash      |   293
 AuditorAgent        | groq/openai/gpt-oss-120b     |   199
 ClassificationAgent | groq/llama-3.1-8b-instant    |    27
 ClassificationAgent | groq/llama-3.3-70b-versatile |   285
```

### 8.1 That query counts ATTEMPTS, not invoices

The first pass read those rows per-invoice and reported "94 gemini successes + 199 groq successes =
293" against **285 invoices**. The sum did not reconcile, and the review caught it.

`call_structured` retries up to 3× on Pydantic validation failure, re-entering the model chain from
the top each time. **A reply that arrived, burned tokens, and was then discarded for failing schema
validation is still `succeeded=True` at the transport layer** — correctly so, per design §2.9, which
records failed and superseded attempts on purpose. `models_used()` answers "was this run a mixture at
all," which is a different question from "how many invoices did each model actually answer."

The mechanical fix: `gateway.generate()` appends exactly one `succeeded=True` record and returns on
the first model that transport-succeeds; `call_structured` returns the instant validation passes. So
the **last** successful entry in an invoice's call list is always the attempt whose JSON became the
answer. `answering_model()` walks the list in reverse.

### 8.2 Corrected per-invoice attribution

```
Classifier answering-model mixture (of 285): {'groq/llama-3.1-8b-instant': 27, 'groq/llama-3.3-70b-versatile': 258}
Auditor answering-model mixture (of 285): {'gemini/gemini-3.5-flash': 86, 'groq/openai/gpt-oss-120b': 199}
```

| Agent | Model | Invoices answered | Share |
|---|---|---|---|
| ClassificationAgent | `groq/llama-3.3-70b-versatile` | 258 | 90.5% |
| ClassificationAgent | `groq/llama-3.1-8b-instant` (fallback) | 27 | 9.5% |
| AuditorAgent | `groq/openai/gpt-oss-120b` (fallback) | 199 | **69.8%** |
| AuditorAgent | `gemini/gemini-3.5-flash` | 86 | 30.2% |

Both mixtures sum to exactly 285 — the property raw attempt counts could never guarantee.

### 8.3 The most instructive result in this milestone

**The 69.8% turned out to be exactly right — by luck. The companion figure was wrong.**

The first pass derived 69.8% from raw attempt counts (94 vs 199) by reasoning that was unsound, and
it happened to land on the correct number. The same unsound reasoning produced **33.0%** for gemini's
share. The true per-invoice figure is **30.2%** — 8 invoices where gemini transport-succeeded, failed
schema validation, and something later actually produced the answer that was used.

So the *direction* of the original finding ("the fallback dominates the Auditor") was right, and the
*precision* was not. A correct headline number does not certify the method that produced it. This is
worth more to the reader than the finding itself.

### 8.4 Why this matters downstream

Both agents were served by two different models **even in a deterministic replay run**. M4-C's 95.4%
classification accuracy and M4-C's 12.5% dispute precision were both measured without recording which
model served each call, and the roadmap has owed "the number M6 publishes must come from one rater"
since M4-C. This milestone does not pay that debt — it builds the instrument that makes it
*checkable* instead of assumed.

---

## 9. What did not work, what is owed

### 9.1 The test suite was sending real spans to a live Langfuse project

`test_the_real_observe_decorator_does_not_swallow_graph_interrupt` imports `langfuse.observe`
**directly**, bypassing `observability`'s kill-switch — deliberately, because the whole point is to
prove the *real* decorator does not eat `GraphInterrupt`. Having bypassed the kill-switch, it
inherited whatever credentials were in `os.environ` at call time.

`tests/conftest.py` pops both Langfuse keys at collection. But another test module importing
`litellm` later in the same session **refills them from `.env` via `load_dotenv()`** — the identical
import-order mechanism as §1.2, reached from a different direction.

The tell was inverted from the usual one: the "Authentication error: Langfuse client initialized
without public_key" warning that appears when this test runs *alone* was **absent** from full-suite
logs. Something was authenticating. Reproduced directly, and the client came up against
`cloud.langfuse.com` with a real key.

**Every ordinary `pytest` / `make check` run had been constructing an authenticated client and
sending a real `approval_gate` span to the developer's live project**, then blocking ~3-4s at
interpreter exit joining background threads after pytest had closed its streams — which is where the
9 `--- Logging error --- / ValueError: I/O operation on closed file` blocks came from. Those blocks
had been dismissed as cosmetic noise. They were the symptom.

Fixed with `monkeypatch.delenv` for both keys immediately before the decorated call. The assertion is
untouched: `real_observe` is still imported straight from `langfuse` and still wraps a function that
raises `GraphInterrupt`. Only the ambient credentials go. Full suite afterwards: **0 occurrences**,
down from 9.

This is exactly the leak `conftest.py`'s own docstring says it exists to prevent, actually happening,
for an unknown number of milestones.

### 9.2 Two tests were found guarding nothing

Both are written up in full above; collected here because "we found two tests that passed for no
reason" is the milestone's most transferable result.

- **The cost-cap LLM-call test** (§3.1) — fixtures too cheap to reach the property. Reported as NOT
  verified, then rebuilt.
- **Task 4's `trace_id` tests permanently polluted `app.llm_calls`.** `_persist_cost` commits on its
  own connection by deliberate production design (spec §5.3: write the row the moment tokens are
  spent, so a crashed run does not lose an already-billed call), so `runtime_conn`'s rollback fixture
  never touched the rows. Fixed with `try/finally` cleanup scoped to each test's own minted
  `thread_id`. Cleanup had to switch to `get_owner_engine()`, because the runtime role has **no
  DELETE grant** on `llm_calls` (migration `b25d8a939562`, the same immutable-audit-trail pattern as
  `audit_log`) — the first attempt failed with `InsufficientPrivilege: permission denied for table
  llm_calls`. That is not a bug; it is least privilege working.

### 9.3 `app.llm_calls` is not a durable audit trail on a dev machine — OWED to M6-C

Found while re-measuring §8, and it is the finding with the longest reach.

`tests/conftest.py` runs a session-scoped, autouse `TRUNCATE app.invoices CASCADE`, and
`llm_calls.invoice_id` carries a foreign key to `invoices`. Postgres's `TRUNCATE ... CASCADE` clears
an entire dependent table unconditionally — it has no row-level "only if referenced" mode. So **every
pytest session wipes `app.llm_calls` completely**, including rows with `invoice_id IS NULL`, and
including sessions whose collected tests touch no database at all.

Demonstrated at closing time. Task 8 wrote 804 attribution rows and Task 4 wrote rows with non-null
`trace_id`; after the `make check` and keyless runs performed for this gate:

```
$ docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -c \
  "SELECT id, node, agent, thread_id, trace_id IS NOT NULL AS has_trace FROM app.llm_calls ORDER BY id"
  id   |   node   | agent |           thread_id           | has_trace
-------+----------+-------+-------------------------------+-----------
 35353 | classify |       | enrich:viettelpost_gtgt_2.1.0 | f
 35354 | audit    |       | enrich:viettelpost_gtgt_2.1.0 | f
(2 rows)
```

Everything M6-B measured into that table is gone. The two survivors are rows written *within* the
last session by `test_enrichment_nodes.py` / `test_enrichment_graph.py`, which drive the real
`classify_node`/`audit_node` and hit the same commit-bypasses-rollback path — pre-existing, and
`agent IS NULL` because the graph's `_persist_cost` does not pass `agent=`, which is why the
attribution query filters `WHERE agent IS NOT NULL`.

**M6-C intends to read this table as eval evidence. It cannot, without either capturing the rows
immediately or persisting them outside the table.** Mitigated but not solved: the eval script now
prints its own persisted id range (`Persisted 804 app.llm_calls rows: id 32775–33578`), which is
currently the only durable record a run leaves of itself.

`conftest.py`'s reset behaviour is deliberate — it makes the suite idempotent after a crashed run —
so this is recorded as a design tension for M6-C to resolve, not a bug to patch reflexively.

### 9.4 Also owed

- **Steady-state tracing overhead is unmeasured** (§4.3). What exists is a per-subprocess cold start
  of ~1.40s, which is not the same thing and is not presented as such.
- **The eval script now writes to `app.llm_calls` by default** (`--no-persist` opts out). Anyone
  running it routinely accumulates rows in a table the runtime role cannot delete from.
- **`llm/telemetry.py`'s docstring still carries the stale "written by nothing until M6.5" sentence**
  that `db/models.py` had. Left alone deliberately — outside the file list of the task that found it,
  and a drive-by edit is how a functional diff becomes unreviewable.
- **The negative-control gate stays at 22/24, NOT MET** (M6-A). Nothing in M6-B improves it and
  nothing in M6-B should appear to.

---

## 10. Gate verification

```
$ make check
.venv/bin/ruff check src tests migrations scripts
All checks passed!
.venv/bin/mypy
Success: no issues found in 70 source files
.venv/bin/python -m pytest
760 passed, 2 xfailed, 1 warning in 37.36s
```

Zero skips — Postgres was up throughout, so no test silently degraded into a pass.

The suite also runs green with **every provider key unset**, which is the condition CI and the live
demo run under:

```
$ env -u GROQ_API_KEY -u GROQ_API_KEYS -u GEMINI_API_KEY -u GEMINI_API_KEYS \
      -u OPENROUTER_API_KEY -u OPENROUTER_API_KEYS \
      LLM_MODE=replay .venv/bin/python -m pytest tests/
760 passed, 2 xfailed, 1 warning in 37.45s
```

### 10.1 CI on the pushed SHA

The rule this project has broken four times: a milestone is not closed until `make check` is green on
the **pushed** commit and CI agrees. One of those four was pushed red, CI failed, and nobody looked.

```
$ gh run view 30942059913 --json headSha,status,conclusion,jobs
sha        : b4a6c5b25e0e35d007b540f182abb1171db8d6b5
status     : completed
conclusion : success
jobs       : secrets  -> success
             test     -> success
```

https://github.com/quockhanh41/Vietnamese-Invoice-agent/actions/runs/30942059913

Both jobs green, including `secrets` — the gitleaks job that M6-A's note broke. It was also run
locally against the full history before pushing, exactly as CI invokes it: `254 commits scanned`,
`no leaks found`.

**This milestone repeated the batching mistake the §1 scoreboard describes**, and it is worth saying
so rather than letting the green tick imply otherwise. All 15 M6-B commits were pushed at once, so CI
first saw the tip — per-task commits are a safety net only if per-task verification runs. Here it
did: `make check` was run at every task boundary and again from a clean state at the gate.

#### The CI count does not match the local count, and that is accounted for

```
local : 760 passed,               2 xfailed
CI    : 765 passed, 2 skipped,    2 xfailed
```

A mismatch is not evidence of a pass. Reconciled rather than waved through:

- **+7**: `HITL_RUNS` defaults to `3` in `tests/test_hitl_survives_restart.py` and the workflow sets
  it to `10`. Seven more parametrized subprocess runs.
- **−2**: `tests/test_vision_accuracy.py` skips `INV-0196` and `INV-0275` on CI with
  *"vision fixture recorded on another platform — PDF rasterization is not byte-stable across
  OS/arch, so the replay key differs."* Those two pass locally on macOS.

760 + 7 − 2 = **765 passed, 2 skipped**. Exact.

The two skips are the **already-owed** roadmap item "vision fixtures do not travel across platforms"
(§1), untouched by M6-B and not counted here as passes. The vision accuracy number in the M4 gate
table is therefore measured locally and *not* re-verified by CI — a pre-existing gap this milestone
neither created nor closes.
