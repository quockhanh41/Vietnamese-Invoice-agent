# M4-A Corpus & Label Alignment Measurement Report

**Date:** 2026-07-31  
**Spec Reference:** `docs/superpowers/specs/2026-07-31-m4a-corpus-reference-alignment-design.md`  
**Plan Reference:** `docs/superpowers/plans/2026-07-31-m4a-corpus-reference-alignment.md`

## Baseline Classification Measurement (`keywords_vi`)

### Spec §4.1 Framing & Honesty Note
> **Spec §4.1 Framing:** Tier-keyword descriptions contain seeded `keywords_vi` terms verbatim, so the baseline's score is a **construction parameter, not a measurement** — the spec chooses it — and only the LLM's margin over it is evidence, itself bounded by whoever wrote the synonym tier. The synthetic baseline score is set into a 55–70% target band so that the baseline is a real non-LLM competitor (Principle 3), while leaving the LLM >=15 points of headroom.

### Before Alignment (2026-07-30 Measurement)
- **Correct agreement:** 5 / 285 (1.8%)
- **No-match count:** 249 / 285 (87.4%)
- **Chance baseline:** 16.7% (1/6 categories)
- **Defect cause:** Line descriptions were near-constant boilerplate drawn independently of manifest labels.

### After Alignment (2026-07-31 Measurement)
- **Correct agreement:** 163 / 285 (57.2%)
- **No-match count:** 118 / 285 (41.4%)
- **Total XML invoices scored:** 285
- **Tuned Tier Mix:** `{"keyword": 0.60, "synonym": 0.25, "jargon": 0.15}`
- **Independently reproduced** 2026-07-31 by the M4-A review, using the probe
  script written on 2026-07-30 before any of this code existed: same 163 / 118.

### ⚠️ The 57.2% is measured on a SKEWED distribution

Categories are not balanced. Measured 2026-07-31:

| Category | Count |
|---|---:|
| MARKETING | 70 |
| TOOLS | 50 |
| LOGISTICS | 49 |
| IT_SERVICES | 45 |
| UTILITIES | 44 |
| OFFICE_SUPPLIES | 42 |

MARKETING's 70 decomposes as **12 `budget_overrun`** invoices (pinned to marketing
text so they breach the MKT budget) + **16 duplicates** inheriting a MARKETING
source + ~42 from the round-robin. MARKETING is also the category the baseline
matches most reliably, because all 12 `budget_overrun` descriptions are
keyword-tier marketing text by construction. **So 57.2% is somewhat inflated
relative to a balanced corpus.**

Consequences, recorded now so M4-C does not rediscover them as bugs:

- **M4-C and M6.7 must report macro-F1 beside accuracy.** Accuracy on a skewed
  corpus rewards a classifier that over-predicts MARKETING, which is exactly the
  degenerate strategy the `category_laundering` injection payload (§9.1 #6)
  tries to induce.
- The ≥15-point margin in the M6 gate is measured against this same skew, so the
  comparison stays fair as long as both sides are scored on the same corpus.

## Department Classification Boundary

- **`DEPARTMENT_NOISE_RATE` (declared):** `0.15`
- **Realized rate (measured 2026-07-31):** **`0.160`** — the constant is a
  per-invoice probability, not a quota, so the achieved share differs from the
  declared one. `test_department_noise_is_present_and_bounded` allows ±0.05.
- **M4-C ceiling:** **84.0%**, not 85%. The realized rate is what bounds any
  classifier, because 16.0% of invoices carry a secondary department that no
  model can recover from invoice text alone — the assignment is an
  organisational fact the document does not encode. **An agent scoring 84% on
  department is scoring perfectly.** Zero noise would have made department a
  lossless function of category and measured nothing.
- All five seeded departments appear (`MKT` 60, `ADMIN` 77, `OPS` 92, `IT` 44,
  `SALES` 27). `SALES` is reachable only through the noise share, which closes
  spec §10 open question 2: it is predictable at the ceiling, not unpredictable.

## Held-Out Real-Fixture Check

- **Scored Real Fixtures:** 5 / 8 (3 marked `UNLABELABLE`: 2 dormitory fee invoices, 1 bare subscription code)
- **Baseline Accuracy:** 2 / 5 (40.0%)
- **Correct Predictions:** `lienket_gtgt_7dong_2.1.0.xml` (`GOODS`), `viettelpost_gtgt_2.1.0.xml` (`LOGISTICS`)
- **Unmatched Predictions:** `dienluc_gtgt_cccd_2.1.0.xml` (`UTILITIES`), `newimage_gtgt_pban2.0.1.xml` (`GOODS`), `phongvu_gtgt_tdiep_2.0.0.xml` (`TOOLS`)
- **Gap & Distribution Note:** The synthetic baseline accuracy is 57.2% while real-fixture accuracy is 40.0% (2/5). The real fixture set skews toward `GOODS`, which is the one seeded category the synthetic corpus does not use (spec §10 open question 1). Excluding `UNLABELABLE` fixtures ensures we do not fabricate held-out accuracy numbers.

## M4-A Exit Review — 2026-07-31

Every headline number above was re-measured independently rather than re-read,
and all three mandated mutations were re-run. **The numbers held**: 163/285 and
118/285 reproduced exactly, the canonical-rank and gate-batch mutations were
genuinely red, no existing guard was weakened (`git diff` on
`test_generator_cli.py`, `test_generator_independence.py`,
`test_generator_manifest.py`, `test_domain_budget.py` is empty), and CI was green
on the pushed commit — success criterion 10, which the three previous milestones
had all failed.

This is the **first milestone piece in the project where the review did not find
a defect the implementation had reported as passing.** Two design issues were
found, both introduced by later tasks eroding earlier ones, and both fixed:

### A guard that was real and went blind — a new variant of the pattern

`test_every_manifest_department_is_seeded_and_budgeted` was **genuinely red**
under its prescribed mutation at the commit that introduced it (`0b83774`:
restore the literal `DEPARTMENTS` → **2 failed**), and **green** under the same
mutation two commits later (`81f1930` onward → **1 failed**). Task 4 replaced
`DEPARTMENTS[i % len(...)]` with `_pick_department(cat, rng)` reading
`CATEGORY_TO_DEPARTMENT` / `SECONDARY_DEPARTMENT`, leaving `DEPARTMENTS`
referenced only by its own test. Nothing re-ran the mutation afterwards.

The behavioural guard was never vacuous — breaking a map value did turn it red —
so **D1 could not have recurred silently**. What was false was the stated
rationale: deriving `DEPARTMENTS` from the seed no longer made drift
unrepresentable, because the maps were plain literals.

M1–M3 produced nine tests that *never* guarded anything. This is the first that
guarded, then stopped. The lesson is narrower and worth stating: **a mutation
result expires when the code it mutated stops being the producer**, and nothing
in the process re-checks it.

Fixed by making `DEPARTMENTS` load-bearing again — an import-time check that the
maps' values are a subset of it, plus a fast direct test on the maps themselves.
Mutation-verified three ways: map broken → clean `ValueError` at import; import
check disabled + map broken → both tests red; and the check raises `ValueError`
rather than `SystemExit`, because a module-scope `SystemExit` escapes pytest's
collection handler and aborts the session with `INTERNALERROR`, hiding every
other result (measured while writing the fix).

### Two reference fields, two identifier spaces, undocumented

Measured 2026-07-31: `twin_of` resolves **6/6** against `invoice_id` and **0/6**
against the doc_id; `duplicate_of` resolves **9/9** against the doc_id and
**0/9** against `invoice_id`. Neither field said so. The split arose from making
a test pass, not from a design decision.

Both are defensible — a duplicate *is* defined by repeating the natural key — but
a consumer joining one the way it joined the other gets an **empty join and no
error**, which is how this hides in eval code. M6.2's negative control and
M4-D's duplicate detection are both such consumers. Fixed by documenting both
spaces at the field definitions and pinning them with
`test_the_two_reference_fields_resolve_in_their_declared_spaces`, which is
mutation-verified: swap `twin_of` back to doc_id space and it goes red. Unifying
them later is now a deliberate red test rather than a silent change.

## Vision Accuracy Measurement Status (Debt Re-declaration)

- **Status:** DEFERRED / RE-DECLARED (Task 9 Step 1 Precondition check)
- **Precondition Check Date:** 2026-07-31
- **Findings:** `s.gemini_api_keys` count = 0, `s.groq_api_keys` count = 0. No vision-capable API key (`GEMINI_API_KEY`) is configured in the environment.
- **Action:** Per Task 9 Precondition stop-rule, execution stopped after step 1 without fabricating fixtures or creating dummy replay files. The vision accuracy measurement debt remains outstanding and is re-declared explicitly in the roadmap (`docs/Plan_Project_F_2026-07-25.md`).
