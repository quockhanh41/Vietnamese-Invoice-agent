# Spec — M8-A: the ReportingAgent, and the guard that makes it publishable (item 8.1)

> **Date**: 2026-08-06 · **Milestone**: M8 · **Status**: design approved, implementation depth.
>
> Reads with roadmap `docs/Plan_Project_F_2026-07-25.md` §3 (M8, item 8.1) and design doc
> `docs/project-f-system-design.md` §6.2 (the five agents).
>
> Predecessor **M7 closed 2026-08-06** (`b840040`, CI run `31061588419` on `fc9171a`, 834 passed /
> 2 xfailed). M8-A revisits none of M7's measurements.
>
> This spec answers M7 spec §8 open question 2 — *"does the narrative want an LLM rather than a
> template?"* — with a **measurement**, not a preference.

---

## 0. Global constraints

From `CLAUDE.md` and roadmap §0. Not restated per section.

- **The LLM never toggles a flag and never posts a journal entry.** The ReportingAgent writes prose
  over numbers Python already computed. It decides nothing.
- **A response model must not contain approval/flag/status fields.** `ReportCommentary` carries free
  text and nothing else; a mutation adding a decision field must turn a named test red.
- **Authoring English, product Vietnamese.** The commentary is read by a human — CLAUDE.md names
  `ReportingAgent` report commentary explicitly — so prompts and output stay **Vietnamese**.
- **Every LLM metric ships beside a non-LLM baseline** (Principle 3), and **deterministic work does
  not go to an LLM** (Principle 4). §4 below is how those two rules decide whether this agent ships
  with a model at all.
- **Every non-obvious constant carries date + measurement + report file name.**
- **A milestone is not closed until `make check` is green on the pushed commit and CI agrees.**

---

## 1. Why this exists, and why it is nearly cut

`api/routes/reports.py` already carries the intent in its module docstring, written at M5-C:

> *"M8's ReportingAgent writes Vietnamese commentary ON TOP of these numbers rather than recomputing
> them, so the narrative and the dashboard cannot disagree."*

Design §6.2 names five agents and four exist. The fifth is the only unwritten code in the project.

**The case against building it is real and is recorded here rather than skipped**: `tools/explain.py`
already proves Vietnamese narration with no model in the path, and M7's gate verified it LLM-free.
Nothing in Gate M8 asks for a fifth agent. Principle 4 says deterministic work should not go to an
LLM, and rendering "MKT is over budget by 6,525,000" from a number Python computed is deterministic.

The case for building it is that **"period commentary" is not the same task as "flag narration"**.
`explain_flags` narrates one fact about one invoice. A period report has eight or nine competing
facts and the useful sentence is the one that says *which two matter this month* — selection and
emphasis, which is what a language model is actually for.

So M8-A builds both, measures both, and **ships whichever wins on a stated rule** (§4). The verdict
is genuinely open at the time of writing; this spec does not pre-announce it.

---

## 2. Design

### 2.1 Three functions, one file

`src/invoice_agent/agents/reporting.py`, following `agents/classification.py`'s shape: pure
functions, data in, structured data out, no DB handle and no HTTP.

```python
def build_facts(*, budgets, flags, posting, cost, spend) -> ReportFacts     # pure Python
def render_template(facts: ReportFacts) -> ReportCommentary                 # LLM-free baseline
def narrate(facts, *, gateway, calls=None) -> ReportCommentary              # LLM + guard + fallback
```

The four inputs are the **existing** `db/queries` aggregates the `/reports` routes already return,
plus `spend_summary`. No new SQL. A second implementation of `budget_report` reachable only from the
narrator would be a second source of truth and the first thing to drift — the same rule M7 §3.1 set
for MCP handlers.

### 2.2 `ReportFacts` — Python computes every number the narrative may speak

This is the load-bearing decision. `build_facts` computes not only the aggregates but **every derived
quantity** a sentence could want:

| Derived | From |
|---|---|
| `over_budget[]` — department, period, `overspend` | `amount_limit - consumed < 0` |
| `warning[]` — department, `pct_used` | `consumed / amount_limit >= warn_threshold_pct` |
| `top_flag` — code, `total`, `open_count` | `flag_report` first row |
| `auto_pct` / `human_pct` | `posting_report` counts |
| `cost_per_invoice_usd` | `cost_report.shadow_cost_usd / invoice_count` |

Money stays `Decimal` and is stringified at the boundary, matching `db/queries.budget_report`'s money
rule (JSON numbers are IEEE 754 doubles; this system decided at M1 that money is `Decimal`).

The model therefore never computes anything — it **selects and phrases**. That is Principle 4 applied
inside an LLM node rather than around it.

### 2.3 `ReportCommentary` — the security boundary is the shape

In `agents/models.py`, beside `ClassificationResult` and `AuditVerdict`:

```python
class ReportCommentary(BaseModel):
    summary: str                  # 1–2 Vietnamese sentences
    observations: list[str]       # 2–4 Vietnamese bullets
```

Free text only. **No** `approved`, `flag`, `status`, `severity`, `action`, no numeric fields. A
reviewer asking CLAUDE.md's question — *could the LLM use this to self-authorize?* — must get "there
is no field to do it with". `source: Literal["llm", "template"]` is attached by **Python** on the way
out, not by the model, and is not part of the structured-output schema.

### 2.4 The numeric-fidelity guard

`narrate()` does not trust the model's arithmetic, because there is no arithmetic it is allowed to
do.

1. Extract every numeric token from `summary + observations`.
2. Normalize Vietnamese formatting — `6.525.000` → `6525000`, `12,5%` → `12.5` — so the guard reads
   the way a Vietnamese reader writes.
3. Compare against the fact set: every number `build_facts` produced, normalized identically.
4. **Any unsupported number rejects the whole commentary** and `narrate()` returns
   `render_template(facts)` with `source="template"`.

Rejected, not repaired. A retry loop asking the model to fix its own number would put the model back
in the arithmetic path through the back door.

Two properties the tests must pin **in both directions**, because a guard that rejects everything is
as broken as one that accepts everything:

- a fabricated number (`8.000.000` when no fact holds 8000000) → rejected;
- a supported number written Vietnamese-style (`6.525.000` for fact `6525000`) → **accepted**.

**The fact set is built from every fact value, including the string ones.** A period is stored as
`"2026-07"`, so `2026` and `7` are extractable facts and `tháng 7/2026` is sayable; a department code
carries no digits and contributes none. Numbers are extracted from fact values by the *same*
normalizer that reads the narrative — one function, used twice, so the two sides cannot disagree
about what a number is. Anything not derivable that way is not sayable. If that proves too strict in
measurement, the finding is *reported*, not silently loosened.

### 2.5 The API and the UI

- `GET /reports/commentary?period=YYYY-MM` — assembles facts from the same four queries, calls
  `narrate()`, returns `{source, summary, observations, facts}`. Returning `facts` alongside the
  prose is deliberate: the reader can check the sentence against the numbers it was built from.
- Streamlit `page_reports()` gains three **`st.bar_chart`** charts — budget consumed vs limit, flags
  by code, posting authority — and renders the commentary above them. No new dependency.
- **Money is `str(Decimal)`; charting needs `float`.** The conversion happens in the UI, display-only,
  with a comment saying so. A float that reaches the database or the API would undo M1's money rule.
- The UI stays HTTP-only. `ui/` imports nothing from `invoice_agent` except `config`, enforced by the
  M5-D source guard, which this milestone does not touch.

---

## 3. What must not happen

- The commentary endpoint must not recompute aggregates. It calls `db/queries`, like every other
  reader. A mutation that inlines a `SELECT` must turn a named test red.
- `narrate()` must not return model text when the guard failed. A mutation removing the fallback must
  turn a named test red.
- No new field on any existing agent response model, no prompt change to Classifier or Auditor, no
  touch to `sanitize.wrap()` or the guard chain.
- The facts payload passed to the model goes through `sanitize.wrap()` like every other model input —
  a vendor name is attacker-controlled text and reaches this prompt through `spend_summary`.

---

## 4. The measurement, and the rule stated before the number exists

`scripts/eval_reporting.py`, artefact `docs/notes/2026-08-06-m8a-reporting-raw.json`, written up in
the milestone note.

**Sample**: N ≥ 20 report payloads — every distinct `(period)` present in the loaded corpus, plus
seeded perturbations (a budget pushed over, a flag count zeroed, an empty period) generated from a
fixed seed so the run is repeatable.

**Two deterministic metrics, both scored for LLM *and* template**:

| Metric | Definition |
|---|---|
| **Numeric fidelity** | fraction of narratives whose numbers are all in the fact set — scored **pre-guard**, since post-guard fidelity is 1.0 by construction and would measure the guard, not the model |
| **Coverage** | fraction of payloads where every over-budget department is named in the prose |

The template's fidelity is 1.0 by construction and is **still measured** — "by construction" claims
are precisely what this project's reviews keep disproving.

**The shipping rule, fixed now**:

> The LLM ships only if `coverage_llm > coverage_template` **and** `fidelity_llm >= 0.90`.
> Otherwise `render_template` is the default and the model is not called.

Either outcome is written into **ADR 0007** and into the note with its numbers. A template victory is
a result, not a failure — it is the same shape as M4-C's Auditor result, which this project published
rather than buried.

---

## 5. Scope — out

- Trend comparison across periods (design §6.2's "so sánh xu hướng"). There is one loaded period of
  real data; a trend line over one point is decoration. Recorded as not done, with the reason.
- pgvector episodic embeddings — deferred at M4 and again at M7; not resurrected here.
- Any change to the four existing report queries.
- Re-measuring anything M6 or M7 published.

---

## 6. Mutation verification

Each must turn a **named** test red; the note records measured output, not the word "RED".

| Mutation | Expected to catch it |
|---|---|
| Delete the fidelity check in `narrate()` | the fabricated-number test |
| Break Vietnamese separator normalization | the supported-number-survives test (guard over-rejects) |
| Add `approved: bool` to `ReportCommentary` | the response-model boundary test |
| Return model text when the guard failed | the fallback test |
| Make `/reports/commentary` run its own `SELECT` | the single-source-of-truth test |

---

## 7. M8-A gate

| Criterion | Evidence required |
|---|---|
| `ReportCommentary` has no decision field | boundary test + mutation 3 red |
| Numeric fidelity guard works both ways | mutations 1 and 2 red, with measured output |
| Commentary and dashboard read the same numbers | mutation 5 red |
| LLM vs template measured on N ≥ 20 | fidelity + coverage for both, artefact JSON committed |
| The shipping rule of §4 applied as written | ADR 0007 states the verdict and the numbers behind it |
| Charts render in the Streamlit reports page | `make demo` screenshot in the note |
| `make check` green on the pushed commit, CI agreeing | CI run URL on the closing SHA |

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| The guard is too strict and rejects every LLM narrative | Measured pre-guard fidelity (§4) shows this as a number instead of a hunch; if it is near zero, the template ships and that is the result |
| Replay fixtures do not exist for the commentary prompt, so the public demo cannot narrate | M8-C records the fixture for the seeded demo payload; the endpoint must degrade to `source="template"` on a replay miss, never to an error page |
| The model writes a number in words (`sáu triệu`) and evades the digit-based guard | Stated as a known limitation of a digit scanner; the coverage metric does not depend on it, and the fallback still bounds the damage to prose |
| The fifth agent is scope for its own sake | §1 records the case against, and §4's rule can end with no model shipping at all |

---

## 9. Open questions — recorded, not silently dropped

1. **Does the commentary belong in the MCP surface as a tool?** Not in M8-A. The registry's write/read
   split and the architecture test would both need a ruling on a tool whose output is model prose.
2. **NĐ 70/2025/NĐ-CP** remains open from M1 and is still not blocking.
