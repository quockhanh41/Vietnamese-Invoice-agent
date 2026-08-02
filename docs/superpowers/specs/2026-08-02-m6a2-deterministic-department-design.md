# Spec — M6-A2: a deterministic `department`, so the LLM cannot choose which budget is checked

> **Date**: 2026-08-02 · **Milestone**: M6-A2 (closes roadmap item 6.3b) · **Status**: design approved
>
> Reads with `docs/superpowers/specs/2026-08-02-m6-security-observability-eval-design.md` (M6),
> `docs/notes/2026-08-02-m6a-injection.md` §6 (the measurement that motivates this), and
> `docs/adr/0005-structured-output-as-security-boundary.md`.

---

## 0. Global constraints

- **The LLM never toggles a flag and never posts a journal entry.** This spec closes a path that
  reached a financial control *without* violating either clause literally.
- **No agent response model may gain an approval/flag/status field.** `ClassificationResult` keeps
  `department` but loses its authority; nothing is added.
- **Authoring language is English.** Vietnamese stays in anything a model or a user reads.
- **Every non-obvious constant carries date + measurement + report file name.**
- **`make check` green on the *pushed* commit, CI agreeing**, per roadmap §1.
- Pinned: `langgraph==1.2.9`, `langgraph-checkpoint-postgres==3.1.0`.

---

## 1. Why this exists

M6-A measured (`docs/notes/2026-08-02-m6a-injection.md` §6) that budget is keyed on `department`
(`graphs/batch.py:278-301` groups by `(department, period)` and looks up `budgets.department_code`),
and that `department` is a field the model chooses. One 51,000,000 đồng invoice, department varied,
everything else identical:

| Department | Seeded limit | `BUDGET_BREACH` |
|---|---:|:---:|
| MKT (true) | 50,000,000 | ✅ |
| ADMIN (what the payload achieves) | 30,000,000 | ✅ |
| **OPS** | **80,000,000** | ❌ **no flag at all** |

The real payload moves MKT → ADMIN and is caught **only because ADMIN's budget is smaller**. Steered
at OPS or IT the spend escapes every budget control, and the escaping invoice is otherwise clean —
against an established vendor it raises nothing and auto-posts.

### 1.1 The obvious fix would close nothing

`department` in the corpus is already a pure function of `category`
(`scripts/generate_invoices.py:69`, `CATEGORY_TO_DEPARTMENT`): `MARKETING→MKT`,
`OFFICE_SUPPLIES→ADMIN` — precisely the measured shift. So "derive `department` deterministically
from `category`" leaves it fully steerable, because `category` is still LLM-chosen. **Stated here
because it is the change a reasonable reader would propose first, and it would look principled while
changing nothing measurable.**

### 1.2 A second defect on the same path

`memory/vendor_profile.py:100-101` writes `values["modal_department"] = department` — the current
invoice's value overwrites, so the column is **last-write-wins, not a mode**, unlike `modal_category`
which is derived from `category_histogram`. The vendor's stored department is therefore itself
poisoned by each LLM choice. Fixed here because this feature depends on that column being trustworthy.

---

## 2. Decision: the LLM's department is non-authoritative for the auto path

**The rule, in one sentence:**

> An invoice may auto-post only if its `department` was resolved without reading LLM output.

Deliberately weaker than "get the department right." It does not require solving classification; it
removes the LLM from the authority chain and falls back to the approval loop M5-B already built.

Rejected alternatives, recorded rather than dropped:

- **Delete `department` from `ClassificationResult`.** Strictest, but leaves reports and the reviewer
  UI with a blank department on every first sighting, and buys no additional safety once the field is
  non-authoritative.
- **Seeded vendor→department reference table.** No cold-start ambiguity, but every genuinely new
  vendor blocks until a human curates a row — a worse version of the same human step, paid earlier.
- **Check the spend against every candidate department.** No new state, but flags almost everything
  near any budget edge; the false-positive rate is §10.4's headline metric.

---

## 3. Components

### 3.1 `memory/department.py` — resolution (new)

```
resolve(conn, tax_code) -> str | None
```

Returns the mode of `vendors.department_histogram`, or `None`. Pure lookup: no LLM, no invoice text.

Ties break by the **highest** department code, matching `modal_category` exactly —
`vendor_profile.py:99` is `max(histogram.items(), key=lambda kv: (kv[1], kv[0]))`, and because `max`
compares the key second, a tie resolves to the alphabetically *last* code, not the first. Verified
2026-08-02: `{'MKT': 2, 'ADMIN': 2, 'OPS': 1}` → `MKT`. Copied deliberately rather than "improved",
so two histograms of the same shape cannot resolve differently depending on which column is read.

| Order | Source | Authoritative? |
|---|---|---|
| 1 | `vendors.department_histogram` mode | ✅ may auto-post |
| 2 | nothing resolved (`None`) | ❌ LLM value kept for display, invoice forced to `needs_approval` |

### 3.2 `vendors.department_histogram` — data model

New JSONB column, Alembic migration 14, mirroring `category_histogram`. `modal_department` becomes
derived from it, fixing §1.2 in the same change.

### 3.3 What feeds the histogram — the rule that keeps it clean

**Only the resolved department, never the raw LLM value.**

| Invoice path | Recorded into the histogram |
|---|---|
| Auto-posted | the value already resolved from history |
| Human-approved | the reviewer's value, from `corrections["department"]` |
| Rejected | nothing |

Without this, the histogram launders itself: one successful attack becomes "history," and the second
attempt resolves to the attacker's department with full authority. This is the single most important
rule in the spec.

`DecisionRequest.corrections` already exists (`api/schemas.py:31`) and currently lands only as
free-text `vendors.reviewer_notes` (`graphs/approval.py:370-377`). M6-A2 promotes
`corrections["department"]` to a structured field update. Other keys keep their present behaviour.

### 3.4 Bootstrap

First invoice from a vendor has no history → `needs_approval` → the reviewer's department seeds the
histogram → subsequent invoices resolve and may auto-post. No new mechanism; it reuses M5-B's
approval loop.

### 3.5 Budget computation when unresolved

`serial_join` still computes a budget for unresolved invoices, against the LLM's proposal, so the
reviewer sees budget context rather than a blank. The invoice is simultaneously forced to
`needs_approval`, so **that flag is advisory for a human and never an input to auto-posting.** The
safety property governs the auto path, not the flag's accuracy on invoices a human will read anyway.

### 3.6 `ClassificationResult` keeps `department`

It is no longer authoritative, so it is not a security-boundary field and
`tests/test_agent_schema_boundary.py` is unaffected. A new guard asserts it never reaches the budget
lookup.

---

## 4. Scope

### 4.1 In

- `memory/department.py`, migration 14, `department_histogram`, `modal_department` as a true mode.
- Resolution wired into the enrichment/batch path; unresolved forces `needs_approval`.
- `corrections["department"]` as a structured update.
- `rebuild_vendor_profiles.py` builds `department_histogram` from `invoices.department`.
- Tests and mutations per §6.

### 4.2 Out

- Changing `dispute()` or `CLASSIFICATION_DISPUTED` semantics. It still compares both fields; a
  department disagreement remains a signal. Not needed for this property.
- Re-measuring classification accuracy. Owed, but it belongs to M6-C's pinned-rater run.
- Any change to `category` resolution. `category` has no path to a financial control.

---

## 5. Blast radius — three existing measurements move

1. **The M4 gate batch's 23 auto / 7 needs-approval split.** On a cold database every invoice is
   unresolved, giving 0 auto / 30. `rebuild_vendor_profiles.py` gains `department_histogram`, and
   **running it becomes a stated precondition of the M4 gate** rather than something discovered by a
   red run. The roadmap gate text is updated in the same commit.
2. **STP rate (§10.4's business headline).** Same cause. M6-C reports it with profiles built and says
   so explicitly.
3. **M4-C's classification accuracy.** Department stops being authoritative, so the metric reports
   category and department **separately** instead of as one number. That re-measurement was already
   owed for the single-rater reason (M6 spec §1.2).

---

## 6. Tests, each with the mutation that must turn it red

| Test | Mutation that must turn it red |
|---|---|
| Laundering into a larger budget now breaches — the 6.3b `xfail` flips | resolution reads the LLM value first |
| An unresolved department cannot auto-post | drop the forced `needs_approval` |
| The histogram records only resolved values, never raw LLM output | record `ClassificationResult.department` |
| `ClassificationResult.department` never reaches the budget lookup | wire it in |
| `modal_department` is a mode, not last-write-wins | restore the overwrite at `vendor_profile.py:101` |

`test_department_laundering_moves_spend_into_another_budget` is `xfail(strict=True)`, so it goes red
the moment the fix lands. That is intended: it forces the mark, ADR 0005, note §6 and roadmap 6.3b to
be updated together instead of the fix passing silently.

---

## 7. Success criteria

| Criterion | Evidence |
|---|---|
| 6.3b closed | the `xfail` removed, test passing, all three attacks in §1 blocked |
| Histogram uncontaminated | mutation-verified: recording the raw LLM value turns a test red |
| `modal_department` is a mode | mutation-verified against the restored overwrite |
| M4 gate split preserved | 23 auto / 7 needs-approval after `rebuild-profiles`, precondition documented |
| ADR 0005 + note §6 + roadmap 6.3b updated together | the gap is recorded as closed, with the measurement |
| `make check` green on the pushed commit, CI agreeing | `gh run` URL on the closing SHA |

---

## 8. Open questions — recorded, not silently dropped

1. **Should a department resolved from history but *contradicted* by the LLM raise a finding?** A
   deterministic `DEPARTMENT_REASSIGNED` signal would surface laundering attempts to a human instead
   of silently ignoring them. Attractive, and out of scope here: it is a detection mechanism, and
   this spec's job is to close the channel. Revisit once the channel is closed and there is a
   measurable attempt rate.
2. **How many invoices does the `needs_approval` fallback actually cost?** The corpus has 300
   invoices across an unmeasured number of distinct vendors; the STP impact on a cold database is
   therefore unknown until measured. M6-C measures it with profiles built, which is the realistic
   operating condition.
3. **Should the histogram decay?** A vendor that genuinely moves between departments will resolve to
   its historical mode indefinitely. Not a security question, and no evidence yet that it matters.
