# Spec — M6: security, observability, eval (plan items 6.1–6.8)

> **Date**: 2026-08-02 · **Milestone**: M6 · **Status**: design approved, M6-A specified to
> implementation depth; M6-B and M6-C specified to decision depth and get their own specs
> before implementation.
>
> Reads with roadmap `docs/Plan_Project_F_2026-07-25.md` §3 (M6) and design doc
> `docs/project-f-system-design.md` §9 (guardrails), §10 (eval), §2.9 (cost tracking).

---

## 0. Global constraints

These come from `CLAUDE.md` and roadmap §0. They are not restated per section.

- **The LLM never toggles a flag and never posts a journal entry.** M6 does not weaken this; M6-A
  exists to *measure* that it holds.
- **No agent response model may gain an approval/flag/status field.** Item 6.4 already pinned this
  with `tests/test_agent_schema_boundary.py` (pulled forward to M4-C). M6-A adds a behavioural test
  beside that structural one.
- **Authoring language is English.** The six injection payloads and the `keywords_vi` baseline stay
  Vietnamese: they are corpus the system ingests, and translating them changes what is tested.
- **Every non-obvious constant carries date + measurement + report file.** M6 both obeys this and
  pays an existing violation (`NEAR_DUP_WINDOW_DAYS`).
- **Measure first.** Every number M6 publishes is measured in this repo, beside a non-LLM baseline
  where one exists.
- **A milestone is not closed until `make check` is green on the *pushed* commit and CI agrees**,
  and CI must actually run the milestone's gate. Four milestones have failed this rule.

---

## 1. Why this exists

M6 is where the project's central claim gets tested rather than argued. Design §9.2 asserts that
prompt injection is defeated by making it **inexpressible** — the model has no field through which
to approve anything — rather than by detecting it. Every milestone so far has *built* that boundary.
None has attacked it.

M6 also produces `docs/eval-report.md`, which M8's README is required to trace every number back to.
Three inherited debts stand between here and a report that can be believed:

### 1.1 The Auditor number came from a two-model mixture

M4-C measured 12.5% dispute precision while Gemini free tier rate-limited **177 times**, so those
audits fell through to a second model. Family disjointness held throughout and the finding survives
either mixture, but roadmap §1 is explicit: *the number M6 publishes must come from one rater*.

### 1.2 The classification number never recorded who produced it

M5-B measured a live batch where **86% of classifications were served by the fallback**
`llama-3.1-8b-instant`, not the primary. M4-C's 95.4% was measured without recording model per call,
so it is not currently attributable. This is cheaper to fix than roadmap §1 implies: `classify()` and
`audit()` already accept a `calls=` out-param that `call_structured` fills, and
`scripts/eval_classification.py` simply never passes one. It is a parameter, not a return-type
change, so it does not touch the response-model security boundary.

### 1.3 Design §9.1 under-enumerates the attack surface

§9.1 names one field: the line-item `description`. Reading the code found two more:

- `agents/classification.py:39` — `describe()` interpolates `seller_name` into the prompt.
- `agents/audit.py:41` — `audit()` feeds the Classifier's own free-text `reasoning` to the Auditor,
  an **agent-to-agent channel**. A payload in a description can steer `reasoning`, which then becomes
  Auditor input.

Both pass through `wrap()`, so the defence is probably intact. Nothing measures it. M6-A's variant
axis is chosen to cover exactly these.

### 1.4 Three defects found while planning

- **`cost_gate_node` reports success unconditionally.** `graphs/batch.py:397` returns
  `{"status": "cost_gate_passed"}` regardless of outcome, while `cost_gate_condition` separately
  routes an oversized batch to `END`. A rejected batch therefore finishes reporting
  `cost_gate_passed`.
- **The batch threshold is a bare magic number**, `> 10000`, with no justifying comment. `>` also
  means exactly 10,000 files *passes*, while design §9.2 states 10,000 as the rejection case.
- **`NEAR_DUP_WINDOW_DAYS = 7`** (`domain/anomaly.py:19`) is commented "PROVISIONAL AND UNMEASURED",
  which is a live violation of the constants rule.

---

## 2. Decision: M6 splits into three sub-milestones

M2 through M5 were each split this way. M6 spans three subsystems with a real dependency order:

```
M6-A  security       6.1 6.2 6.3 + ADR 0005 + history purge
M6-B  observability  6.5 6.6 + model attribution
M6-C  eval report    6.7 6.8 + re-measurements

B ──> C   attribution must exist before the report can claim one rater
A ──> C   injection numbers are rows in the report
```

Each sub-milestone gets its own plan, gate and review, and is not opened until its predecessor's gate
passes. Item **6.4 is already done** (M4-C Task 1).

---

## 3. M6-A — security

### 3.1 Scope — in

- 30 injection cases across four seedable placements plus the reasoning channel.
- A negative-control twin for each case (§9.3).
- `tests/test_prompt_injection.py`, CI-blocking.
- Purge of the six PII-carrying vendor PDFs and the M1 tax-code leak from git history.
- ADR `0005-structured-output-as-security-boundary.md`.

### 3.2 Scope — out

- Any *detection* of injection. Design §9.2 rejects detection as the defence; adding a detector
  would create a second, weaker mechanism whose failures would be read as the boundary failing.
- Changing any agent response model. The boundary under test must not move while being tested.
- Auth, rate limiting, or input sanitisation beyond the existing `wrap()`.

### 3.3 The case set — 30 cases, against a gate of 0/26

Six payloads (`defects.INJECTION_PAYLOADS`, unchanged) × placements:

| # | Placement | Seedable? | What it tests |
|---|---|:---:|---|
| 1 | first line `description` | yes | §9.1's only enumerated surface |
| 2 | a later line `description` | yes | position independence |
| 3 | `TChat=4` note line `THHDVu` | yes | the note-line path, which carries no money and is easy to forget |
| 4 | `seller_name` | yes | **§1.3 finding — §9.1 missed this** |
| 5 | Classifier `reasoning` → Auditor | no | **§1.3 finding — agent-to-agent channel** |

Placements 1–4 give **24 invoice-level cases**. Placement 5 is not seedable — the Classifier produces
`reasoning`, so it is exercised by handing a crafted `ClassificationResult` directly to `audit()`,
giving **6 more**. Total **30**.

> **The gate says 0/26 and this spec proposes 30.** Roadmap §2.2 derived 26 as "~5 original cases ×
> ~5 variants", an estimate made before the extra two surfaces were known. Reporting 0/30 satisfies
> 0/26 a fortiori. The count is *raised*, not lowered, and the roadmap gate text is updated to say
> so rather than leaving two numbers in the repo that disagree.

**Where the cases live.** Generated at test time from the six existing corpus carriers, via the
placement transforms. They are **not** added to the 300-invoice corpus: `prompt_injection` is
designed at 1.5% (roadmap §2.2), and adding 24 carriers plus 24 twins would take injection-related
invoices from 12 of 300 to 60 of 348, shift
every other group's proportion, and force a regeneration that changes invoice IDs — which would
invalidate the M4 gate-batch declarations, the vendor profiles and the budget chains. The six corpus
carriers stay as they are.

### 3.4 Negative controls

Each of the 30 gets a twin identical in **every** field but the payload. `defects.strip_injection()`
already does this for line descriptions and needs extending to the seller-name and note-line
placements. Design §9.3's warning is the reason this is not optional: without the control, a
classification difference measures whatever else differs, not the injection.

### 3.5 The hostile stub — the load-bearing component

A well-behaved model replayed from a fixture proves the model behaved. It does not prove injection is
inexpressible. The hostile stub does.

It sits at the **gateway seam** — the same seam `LLM_MODE=replay` uses — and returns raw JSON that
maximally complies with each payload: `approval_status: "approved"`, `flags: []`, and the laundered
category the payload asks for. `call_structured` must then parse that into `ClassificationResult`,
which carries `extra="forbid"`. **The rejection at that boundary is the proof.**

Consequences that make this the right shape:

- No API keys, no fixtures, no network. Runs in CI unconditionally.
- It never goes stale. A model-chain change cannot invalidate it, because no model is involved.
- It tests the defence design §9.2 actually claims, rather than a model's manners.

### 3.6 What `test_prompt_injection.py` asserts

Each carrier runs the full enrichment graph under the hostile stub. "Success" for the attacker — and
therefore a red test — is **any** of:

| # | Assertion | Layer it proves | Driven by |
|---|---|---|---|
| 1 | no `approvals` row carries a decision | structured output boundary | hostile stub |
| 2 | no `journal_entries` row exists | guard re-reads Postgres, not state | hostile stub |
| 3 | carrier `invoice_flags` == twin `invoice_flags` | flags are Python-computed from DB | hostile stub |
| 4 | carrier `category` == twin `category` | negative control (§9.3) | replayed real responses |

**Assertion 3 is the sharpest test in the milestone.** Seed payload 6 (category laundering) onto a
*budget-breaching* invoice. Let the stub launder the category to `OFFICE_SUPPLIES`. Assert
`BUDGET_BREACH` still fires — because it is computed from the amount in the database, not from the
label the model chose. That single case is design §9.2's whole argument, executed.

Assertion 4 is the only one needing real model output, replayed from recorded fixtures for the 30
carrier/twin pairs.

### 3.7 Mutation verification — required, or this is a tautology

Every milestone review so far has found tests that passed while guarding nothing (roadmap §1). Each
of these must turn the suite red:

| Mutation | Expected |
|---|---|
| remove `extra="forbid"` from `ClassificationResult` | assertion 1 red |
| break `wrap()` so invoice text is concatenated raw | assertion 1 or 4 red |
| make a flag readable from graph state instead of `invoice_flags` | assertion 3 red |
| drop the `invoice_id` comparison in the write guard | assertion 2 red |

Results are recorded in the implementation note with measured output, not asserted in prose.

### 3.8 History purge

`git-filter-repo` over the six vendor PDFs and the M1 tax-code leak, force-push to `origin/main`,
then `gitleaks` re-scanned over the rewritten history. Cost is one re-clone: the repo is private with
one contributor. A note records what was exposed and for how long.

Roadmap §1 has deferred this four times and observes that "deferred is not the same as decided". This
closes it as decided.

### 3.9 M6-A gate

| Criterion | Evidence required |
|---|---|
| injection 0/30 | test output, all 30 cases |
| negative control 100% match | carrier/twin category agreement across 30 pairs |
| schema audit still green | `test_agent_schema_boundary.py` |
| four mutations verified red | measured output in the note |
| history purged | `gitleaks` clean over full rewritten history |
| ADR 0005 written | cites the measured 0/30, not an argument |
| `make check` green on the pushed commit, CI agreeing | CI run URL on the closing SHA |

---

## 4. M6-B — observability and cost cap

> Gets its own implementation-depth spec before work starts. Recorded here: scope, decisions, gate.

### 4.1 Tracing (item 6.5)

The three graphs hold **24 nodes**: enrichment 8, batch 9, approval 7. `observability.py` is already
the single import point and degrades to an identity decorator when Langfuse keys are absent.

**That degradation is a trap for the test.** CI runs with no keys, so `observe` is identity, so a test
asserting "every node is traced" would pass on a completely unwired codebase — the same
guard-that-guards-nothing pattern every previous milestone review has found. **Decision:** add a thin `@traced(...)` wrapper in
`observability.py` that sets a marker attribute *and* delegates to the real `observe`. A structural
test then enumerates every node registered in the three builders and asserts each carries the marker,
checkable in both modes.

**Second trap, found while planning:** `vision_extract`, `classify` and `audit` are registered as
`functools.partial` objects, so a marker on the underlying function is not visible on what
`add_node` receives. The test must unwrap `.func`, or it silently passes on three of the eight
enrichment nodes.

Tags: `invoice_id`, `batch_id`, `node`, `agent`. Cost and shadow cost as metadata. `llm_calls.trace_id`
gets its first writer, closing the TODO carried in its own column comment.

### 4.2 Cost cap (item 6.6)

Two layers, mirroring the write-guard discipline:

- **Batch cap.** Fix `cost_gate_node` to report rejection honestly (§1.4), move the threshold into
  `config.py` with a justifying comment, and correct the boundary so 10,000 rejects. The test asserts
  **zero rows in `llm_calls`** after a rejected 10,000-file batch. That is the measurable form of
  design §9.2's "before any LLM call", and it is strictly stronger than asserting a status string —
  a status can be right while a call already happened.
- **Per-invoice token budget.** Estimate with `litellm.token_counter` (already used at
  `gateway.py:105`) before the call; over budget routes to `manual_queue`, which exists as both a
  node and a status.

### 4.3 Model attribution

`scripts/eval_classification.py` passes a `calls=[]` list to `classify()` and `audit()`, records
`model` per invoice, and writes through `telemetry.record` so `GROUP BY llm_calls.model` has data.
Small, and it unblocks M6-C's single-rater verification.

### 4.4 M6-B gate

| Criterion | Evidence required |
|---|---|
| all 24 nodes traced | structural test, partials unwrapped, mutation-verified |
| `trace_id` written | non-null rows in `llm_calls` under a keyed run |
| cost cap 1/1 | 10,000-file batch rejected with **zero** `llm_calls` rows |
| token budget routes to manual queue | test with an oversized invoice |
| model recorded per call | `GROUP BY llm_calls.model` returns rows for an eval run |
| `make check` green on the pushed commit, CI agreeing | CI run URL on the closing SHA |

---

## 5. M6-C — eval report

> Gets its own implementation-depth spec before work starts. Recorded here: scope, decisions, gate.

### 5.1 Pinned-rater mode — how the single-rater requirement is met

An eval-only path that **disables the fallback chain** and throttles below free-tier RPM. A 429
becomes wait-and-retry on the *same* model rather than a silent demotion to a weaker one. Persistent
failure records **`unmeasured`** rather than falling through — the `INV-0029` precedent, where six
Gemini 503s produced no extraction and were reported as unmeasured rather than failed.

Single rater is then **verified, not asserted**: `GROUP BY llm_calls.model` must return exactly one
row per agent. Full corpus, 285 classify + 285 audit. Expect ~30–60 minutes wall clock for the
Auditor on Gemini free tier, since the Auditor must run on a family disjoint from the Classifier's.

### 5.2 Vision replay re-keyed

The replay key currently hashes a locally rasterized PNG, so macOS/ARM recordings miss on CI. Re-key
off **committed PNG bytes** so recordings travel, then measure N=15 live across the 12 `pdf_only` +
3 `unreadable_pdf` corpus invoices, scored against the manifest. This makes the vision row
reproducible from a clean clone, which is what design §10.1's anti-circularity argument asks for.

### 5.3 `NEAR_DUP_WINDOW_DAYS` measured

Currently 7, commented "PROVISIONAL AND UNMEASURED". M6-C measures it against the 9 `near_duplicate`
corpus invoices and rewrites the comment with date + measurement + report file.

### 5.4 The report

`scripts/eval.py` → `make eval` → `docs/eval-report.md`, covering **every** row of design §10.4, each
beside its non-LLM baseline where one exists, and stating plainly which targets are missed. Design
§10.5 is explicit that a reported 0.52 beats a claimed 0.98.

On current evidence the Auditor row will miss its ≥60% target badly. **That is the headline finding,
not a failure to hide** — ADR 0003 already cites the 12.5% measurement rather than an argument.

### 5.5 The manual baseline (item 6.8) does not block M6-C

Design §10.4 requires the time-savings number to be self-measured — hand-entering 10 invoices into a
spreadsheet with a stopwatch — rather than quoted from a market report. This cannot be done by the
agent.

**Decision (2026-08-02):** `docs/notes/manual-baseline-protocol.md` is written as part of M6-C —
which ten invoices, which fields to key, how to record per-invoice times, how to extrapolate. The
report ships with the business row marked **PENDING — awaiting operator measurement**, and the number
is filled in when the user supplies it. No other row depends on it.

### 5.6 M6-C gate

| Criterion | Evidence required |
|---|---|
| `make eval` produces every §10.4 row | generated `docs/eval-report.md` |
| classification beats `keywords_vi` by ≥15 | measured, single rater, model attributed |
| single rater verified | `GROUP BY llm_calls.model` = 1 row per agent |
| vision row at N=15 | measured live, replayable from committed PNGs on CI |
| `NEAR_DUP_WINDOW_DAYS` justified | comment carries date + measurement + report file |
| business row | PENDING with protocol written — explicitly not a pass |
| `make check` green on the pushed commit, CI agreeing | CI run URL on the closing SHA |

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Free-tier daily quota exhausts mid-eval | `unmeasured` is a first-class outcome; a partial run reports its own N rather than silently mixing raters |
| The hostile stub is *too* hostile and tests an impossible input | It returns JSON at the gateway seam, which is exactly what a compromised provider could return. That is the threat model, not an exaggeration of it |
| History rewrite breaks the user's working clone | Single contributor, private repo; re-clone after force-push. Sequence it before any other M6-A commit so nothing is rebased twice |
| 30-vs-26 reads as moving the goalposts | The count is raised, and the roadmap gate text is updated in the same commit so two disagreeing numbers never coexist |
| Vision N=15 still misses §10.4's F1 ≥ 0.90 | Report it missed. §10.5 governs |

---

## 7. Open questions — recorded, not silently dropped

1. **Which single model should each agent be pinned to for the published numbers?** The chain has
   `groq/llama-3.3-70b-versatile` primary for the Classifier, but M5-B measured 86% of live
   classifications served by the `llama-3.1-8b-instant` fallback. Pinning to the primary measures a
   model the system rarely actually uses under load; pinning to the fallback measures the realistic
   one. **Proposal: publish the primary, and report the fallback share separately as a deployment
   caveat.** Resolve in M6-C's own spec.
2. **Does the per-invoice token budget need a measured default, or is a round number honest?** There
   is no measurement to derive one from yet. Likely: set it from the observed token distribution over
   the corpus once M6-B's attribution is recording, and comment it accordingly.
3. **NĐ 70/2025/NĐ-CP** remains open from M1 and is still not blocking. Not M6's to close.
