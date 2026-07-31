# Spec — M4-C: ClassificationAgent + AuditorAgent (plan items 4.1, 4.2)

> **Date**: 2026-07-31 · **Milestone**: M4, third of four specs
> **Reads with**: [`Plan_Project_F_2026-07-25.md`](../../Plan_Project_F_2026-07-25.md) §3 M4,
> [`project-f-system-design.md`](../../project-f-system-design.md) §3.2, §6, §9.2, §10.4
> **Preceded by**: M4-A corpus alignment · M4-B vendor memory + `signals` (both closed 2026-07-31)
> **Followed by**: M4-D batch graph + serial reconciliation

---

## 0. Global constraints

Promoted to the top because the last two milestones lost their rationales to
summarisation while keeping their procedures. Every one of these can be
satisfied by code that passes all tests while producing a meaningless number.

1. **Structured output IS the security boundary.** No agent response model may
   carry an approval, flag, or status field. Before adding any field, ask: could
   the LLM use this to self-authorise? (§3.2)
2. **The LLM never raises a flag.** `CLASSIFICATION_DISPUTED` is computed by
   Python comparing two structured outputs. The models supply opinions; Python
   decides. (§3.4)
3. **The Auditor must not share a family with the Classifier.** A stronger model
   of the same family shares its failure mode and will agree with its mistakes
   more confidently (design §6.3). Exclusion by model *name* does not achieve
   this — see §1.2.
4. **Report macro-F1 beside accuracy.** The corpus is skewed (MARKETING 70 vs
   OFFICE_SUPPLIES 42, measured 2026-07-31), and accuracy on a skew rewards
   over-predicting the majority class — the exact behaviour §9.1's
   `category_laundering` payload tries to induce.
5. **Do not tune until the number appears.** If classification does not beat the
   baseline by ≥15 points, principle 3 says record the reason and consider
   dropping the LLM at that node. Any prompt iteration is recorded with its
   before/after. A gate that is missed is portfolio content.
6. **84.0% is department's ceiling, not its target** (M4-B measured the realised
   noise rate at 0.160). An agent scoring 84% on department is scoring perfectly.

---

## 1. Why this exists, and two things measured while planning it

M4-C builds the multi-agent half of M4: a cheap Classifier, an Auditor on a
different model family, and the disagreement between them — which design §6.4
calls a feature, and which is M4's portfolio artefact.

### 1.1 This is the first milestone that cannot measure itself without a key

M4-A and M4-B were deterministic; they measured fine with no provider
configured. M4-C is two LLM agents whose entire gate is measurement:

- classification must beat `keywords_vi` by **≥15 points** — the baseline is
  **57.9%** (M4-A, re-measured after M4-B's regeneration), so the agent needs
  **≥72.9%**;
- **Auditor override precision measured**, reported even if ugly.

Confirmed 2026-07-31: `tests/fixtures/llm_cache/` is **empty**, and no
`GROQ_API_KEYS` / `GEMINI_API_KEYS` is configured — the same state M4-A's note
recorded when it stopped at Task 9's precondition.

**A key is therefore a precondition of this spec, not a task inside it.** Both
tiers are free (plan §2.1). Everything structural — the response models, the
disputed flag, graph wiring, the schema audit — is testable against a stubbed
gateway and could ship without one; every *number* the milestone exists to
produce cannot. Shipping the agents unmeasured is what principle 3 exists to
prevent, and it would hand M6 three unpaid measurements at once (classification,
override precision, vision) each needing its own milestone's context rebuilt.

### 1.2 `exclude_model=model_used` does not do what item 4.1 says it does

Roadmap 4.1 says to extend `generate_with_model` with `exclude_model=model_used`.
Two findings, both measured 2026-07-31:

**The parameter does not exist.** `gateway.py:332` returns `(text, model_name)`
and its docstring calls itself "the hook the Auditor agent needs (M4.1)", but no
exclusion is implemented. Item 4.1 is a real code change, not wiring.

**Excluding by model name cannot achieve "different family".** The default chain
(`config.py:26`) is:

```
1 groq/llama-3.3-70b-versatile          4 openrouter/qwen/qwen3-next-80b-a3b-instruct:free
2 groq/openai/gpt-oss-120b              5 openrouter/meta-llama/llama-3.3-70b-instruct:free
3 gemini/gemini-3.5-flash               6 openai/gpt-4o-mini
```

Entries **1 and 5 are the same model** — Llama 3.3 70B Instruct — reached through
two providers. A Classifier on entry 1, audited with entry 1 excluded, can land
on entry 5: not merely the same family, **the same weights grading their own
generation**. That is exactly the failure design §6.3 exists to prevent, and it
is invisible to name-based exclusion.

**Resolution: declared disjoint chains.** A new `auditor_model_chain` in config,
plus a frozen model→family table and a test asserting the two chains share no
family. Declaring is more honest and more reviewable than deriving families by
parsing provider-specific model ids — `groq/openai/gpt-oss-120b` and
`openai/gpt-4o-mini` are not the same family despite both containing `openai`.
The frozen table is the pattern `generator/vendors.py` already uses for tax
codes, and the test is what makes "different family" a fact.

---

## 2. Scope

### 2.1 In

1. `src/invoice_agent/agents/` — `classification.py`, `audit.py`, response models.
2. `auditor_model_chain` in `config.py`, the frozen family table, disjointness test.
3. `CLASSIFICATION_DISPUTED` — finding code, `raised_by='agent_disagreement'`,
   Alembic migration **12** extending `ck_flags_raised_by`.
4. `classify` and `audit` nodes spliced into the enrichment graph.
5. **The §9.2 schema audit, pulled forward from roadmap item 6.4** — see §3.2.
6. `scripts/eval_classification.py` — the measurement harness.
7. Recorded LLM fixtures for the eval run, committed, so replay stays hermetic.
8. Roadmap updates; a note recording every number.

### 2.2 Out

- **Prompt-injection testing (6.1–6.3)** stays at M6. The corpus's 6 payloads and
  their negative-control twins exist, but the CI-blocking test and the 26-case
  matrix are M6's. M4-C's schema audit is the *structural* half of that defence
  and is pulled forward only because it guards models this milestone creates.
- `ReconciliationAgent` and `ReportingAgent` (design §6.2) — M4-D and M8.
- Persisting `invoices.category` / `department`, and feeding classification into
  the vendor profile — M4-D. See the caveat in §3.5.
- Vision accuracy. Still owed, still unpaid, and a configured key makes it
  cheap — but it is M3's debt and belongs in its own commit, not smuggled here.

---

## 3. Components

### 3.1 The two agents

`classification.py` and `audit.py`, each a thin function over
`llm.structured.call_structured` with a focused system prompt. Both take a
`calls: list[LLMCallRecord]` out-param so cost lands in `llm_calls` exactly as
`vision_extract` already does (M3-A).

Invoice text reaches the prompt through `extraction.sanitize.wrap` — untrusted
data stays fenced, as M3-B established.

```python
def classify(invoice: InvoiceData, *, gateway, vendor: VendorContext | None,
             calls: list[LLMCallRecord] | None = None) -> ClassificationResult: ...

def audit(invoice: InvoiceData, proposal: ClassificationResult, *, gateway,
          calls: list[LLMCallRecord] | None = None) -> AuditVerdict: ...
```

### 3.2 The response models are the security boundary

```python
class ClassificationResult(BaseModel):
    category: str
    department: str
    confidence: float
    reasoning: str

class AuditVerdict(BaseModel):
    agrees: bool
    proposed_category: str | None
    proposed_department: str | None
    reason: str
```

Neither carries an approval, flag, or status field, and neither may acquire one.
This is CLAUDE.md's "structured output *is* the security boundary" and design
§9.2's "make injection inexpressible": payloads 5 and 6 defeat every keyword
detector, and the defence is not detection but that **the model has no channel
to act through**.

**The schema audit moves here from roadmap item 6.4.** Creating the project's
first two agent response models and deferring their guard to M6 is the gap this
project keeps finding — M4-A's D4 discarded a working mechanism one line before
use; M4-B shipped a sweep it did not act on. The guard is cheap: walk every
`BaseModel` defined under `agents/`, assert no field name matches an
approval/flag/status vocabulary, and mutation-verify by adding
`approval_status: str` to `ClassificationResult`.

`defects.py:198` already states this property in a comment explaining why
payloads 5 and 6 are harmless. M4-C turns that comment into a test.

### 3.3 The Auditor's chain

`auditor_model_chain` in `config.py`, defaulting to a chain sharing no family
with `llm_model_chain`. A frozen `MODEL_FAMILIES` table maps each configured
model id to a family, with provenance in a comment, and
`test_auditor_shares_no_family_with_classifier` asserts disjointness over the
*configured* chains rather than the defaults — so an operator who edits `.env`
into a self-grading configuration gets a red test, not a silent metric.

`generate_with_model` still returns `(text, model_name)`; M4-C adds no
`exclude_model` parameter, because §1.2 shows it cannot deliver the property.
Row 4.1 of the roadmap is corrected rather than implemented as written.

### 3.4 `CLASSIFICATION_DISPUTED` — raised by Python, never by a model

```python
if verdict.agrees is False and verdict.proposed_category != result.category:
    findings.make(findings.CLASSIFICATION_DISPUTED, ...)
```

Python compares two structured outputs and raises the flag. The models supply
opinions and reasons; neither can set it, because neither response model has a
field that reaches it. Both opinions and both reasons go into the finding's
`detail`, which is what design §6.4 hands the reviewer.

New `raised_by` value **`agent_disagreement`** — the producer as a reviewer
understands it, following the convention `findings.py` records for `NEW_VENDOR`.
It needs Alembic migration 12 extending `ck_flags_raised_by`, and
`domain.findings.SOURCES` must be extended in step; the existing equality test
between `findings.SOURCES` and `db.models.FLAG_SOURCES` turns red otherwise,
which is the intended behaviour.

Severity: **`warn`**. It asserts something may be wrong, so unlike `NEW_VENDOR`
it **does** count toward the flag false-positive metric (M4-B's §10.4 decision,
where severity is the discriminator).

### 3.5 Graph wiring, and an honest caveat about vendor memory

M4-B left `validate → signals → END`. M4-C splices in front:

```
validate → classify → audit → signals → END
```

Design §3.2 says `classify` reads `vendor_profile`. It can read the vendor's
name, `invoice_count` and `reviewer_notes` — but **`modal_category` is populated
by `apply_invoice(category=…)`, whose category comes from `classify`, and
nothing persists invoices until M4-D.**

So during M4-C the profile carries **no category history**, and the number this
milestone measures is **classification without entity memory**. That is not a
defect and not a reason to reorder the milestones — but reporting the number as
though vendor history contributed would be false. The note states it, and M4-D
is expected to move the number; the spec does not predict by how much, because
guessing would invite fitting the later measurement to the guess.

---

## 4. Measurement

`scripts/eval_classification.py`, over **all 285 XML invoices**. The scope is set
by the *dispute* metric, not the classification one: if the agents agree ~85% of
the time, 285 invoices yield only ~40 disagreements, already thin for a precision
estimate. A subset would make the headline number uninterpretable.

~570 calls, ≈$0.13 shadow at `gpt-4o-mini` rates and $0 real on free tier. The
recording run must **throttle**; Groq's free TPM is the binding constraint and
key rotation is the safety net, not the control (design §4.2).

| Metric | Reported against |
|---|---|
| Classification accuracy **and macro-F1** | `keywords_vi` at **57.9%**, same corpus, same scorer — gate needs **≥72.9%** |
| Department accuracy | The **84.0%** ceiling |
| **Dispute precision** (headline) | When the Auditor objected, was the Classifier actually wrong? |
| **Override precision** | When the Auditor objected, was its proposed label correct? |
| Ambiguous group (9) | Scored apart, using `alternative_category` — "chose the plausible alternative" is not "wrong" |
| Cost + shadow cost | `llm_calls`, wired at M3-A |

**Why both precision metrics.** Dispute precision is the one this system's design
depends on: a disagreement raises a flag and a human sees both opinions, so the
Auditor never decides. What matters is whether the flag is worth the reviewer's
time. Override precision answers the different question of whether the
suggestion is any good. The two can diverge sharply — an Auditor that reliably
spots errors while proposing wrong corrections is useful for the first and
useless for the second — and reporting one number hides that.

---

## 5. Success criteria

1. Classification accuracy **and macro-F1** recorded against the baseline on the
   same corpus. Beating it by ≥15 points closes the gate; **not** beating it is
   recorded with its reason, and dropping the LLM at that node is considered on
   the number (principle 3, constraint 5).
2. Department accuracy recorded against the 84.0% ceiling.
3. Dispute precision and override precision both recorded, with **n and a
   confidence interval**. If disagreements number fewer than ~20, the metric is
   reported as **underpowered** rather than quoted — §6.4's "below 50% is net
   harmful" threshold means nothing at n=6.
4. Ambiguous group scored separately.
5. Schema audit green, mutation-verified by adding `approval_status` to
   `ClassificationResult`.
6. `test_auditor_shares_no_family_with_classifier` green over the *configured*
   chains, mutation-verified by pointing both at the same family.
7. A test that `CLASSIFICATION_DISPUTED` is raised by Python comparison and
   cannot be produced by model output alone.
8. Migration 12 applied; `--autogenerate` emits an empty migration afterwards.
9. Fixtures recorded and committed; full suite green under `LLM_MODE=replay`
   with **no API keys**.
10. `make check` green **on the pushed commit**, CI green on that commit.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| **The gate is missed** — classification does not clear the baseline by 15 | Pre-committed in constraint 5: record it, do not tune until it appears. Principle 3 treats "this node does not need an LLM" as a result, and Project A's M3.7 is the precedent |
| **The dispute metric is underpowered** | Criterion 3 requires n and a CI, and the word "underpowered" rather than a number. The failure mode is quoting 0.67 computed on 3 disputes |
| Prompt iteration silently fits the corpus | Every iteration recorded with before/after in the note. The corpus's `ambiguous_category` group is the honest hard set and must not be tuned against |
| Rate limits during the recording run | Throttle explicitly. Rotation is the net, not the control |
| ~570 committed fixtures | A few MB. Acceptable, and the alternative — a non-hermetic CI — was rejected at M3 |
| Vendor memory absent, so the number moves at M4-D | Stated in §3.5 and in the note. No prediction of the delta, because a prediction invites fitting |
| The Auditor agrees with everything | A real finding, not a bug: report the agreement rate. An Auditor that never disagrees has an override precision that is undefined, and that is the honest report |

---

## 7. Open questions — recorded, not silently dropped

1. **Does `confidence` belong on `ClassificationResult`?** It is self-reported
   and models are badly calibrated, so it risks being mistaken for a measured
   quantity. Kept because `invoices.classification_confidence` exists (M2-A) and
   M5's `suggest` may order by it — but M4-C should report whether it correlates
   with correctness at all, and drop it if it does not.
2. **Should the Auditor see the Classifier's `reasoning`?** Seeing it may anchor
   the Auditor to the same error; not seeing it discards the information a human
   reviewer would use. M4-C shows it, because §6.4 hands the reviewer both
   reasons and an Auditor reasoning about an unexplained label is a different
   task. Worth an A/B if disputes turn out to be rare.
3. **`NEAR_DUP_WINDOW_DAYS` stays provisional** — its consumer is M4-D.
4. Still open from M1: the NĐ 70/2025/NĐ-CP field check.
