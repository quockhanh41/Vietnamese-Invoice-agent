# Spec — M4-A: corpus & reference alignment (new item, not in the roadmap)

> **Date**: 2026-07-31 · **Milestone**: M4, first of four specs
> **Reads with**: [`Plan_Project_F_2026-07-25.md`](../../Plan_Project_F_2026-07-25.md) §3 M4,
> [`project-f-system-design.md`](../../project-f-system-design.md) §2.1, §6.2, §7, §10.1, §10.2
> **Followed by**: M4-B memory + `signals` · M4-C agents · M4-D batch graph + serial reconciliation

---

## 1. Why this exists

M4 as the roadmap writes it is six items: two agents, two memory modules, serial budget
reconciliation, and the batch graph. Planning it on 2026-07-30/31 turned up five defects in the
inputs those six items consume. Three of them make M4's own gate unmeasurable, and none is visible
from inside M2 or M3 because each sits exactly on a seam that no committed code has ever crossed.

This spec fixes the inputs. It ships no agent, no memory module and no graph.

### 1.1 The five defects, as measured

**D1 — department codes do not join.** The generator uses
`("MKT", "IT", "HR", "LOG", "ADM", "SAL")` (`scripts/generate_invoices.py:43`); the seed creates
`("MKT", "ADMIN", "OPS", "IT", "SALES")` (`scripts/seed_reference.py:22`). Only `MKT` and `IT`
overlap. `invoices.department` is `FK → departments.code`, so **194 of 300 corpus invoices cannot be
inserted at all**, and four of the six corpus departments have no `budgets` row to reconcile
against. M2-A built the schema, M2-C built the generator, and nothing has ever joined them —
M4.5 is the first code that would.

**D2 — `canonical_budget_rank` is mislabelled.** It is assigned by `enumerate` over construction
order (`generate_invoices.py:164`), which for the budget case is invoice-number order — and
`defects.py:163` *deliberately* makes invoice-number order the reverse of canonical order, so that
shuffling can distinguish a sorted implementation from an unsorted one. So `rank=1` is `INV-0001`,
which is **third** in canonical order and the one that breaches. A serialization test reading this
field as canonical position gets exactly the wrong answer, and would pass while proving nothing.
Nothing pins the field today: the only test mentioning it passes `None`.

**D3 — the corpus does not encode `category` in the invoice text.** Measured 2026-07-30 by running
the seeded `keywords_vi` baseline over all 285 XML invoices and comparing to the manifest label:

| | |
|---|---|
| Baseline agrees with manifest `category` | **5/285 — 1.8%** |
| Invoices whose description matches **no** category keyword | **249/285 — 87.4%** |
| Chance (1 of 6) | 16.7% |

The baseline scores *below chance*. Cause: `generate_invoices.py:391-392` assigns both `department`
and `category` as `i % len(...)` — round-robin on the loop index, drawn independently of the line
descriptions, which are near-constant boilerplate
(`f"Dịch vụ tư vấn và hỗ trợ kỹ thuật {index + 1}"`, `generate_invoices.py:90`). The sharpest proof
is the budget case: `INV-0001`, `INV-0002` and `INV-0003` carry the **identical** description
`"Chi phí quảng cáo trực tuyến tháng 07/2026"` and are labelled `MARKETING`, `IT_SERVICES` and
`OFFICE_SUPPLIES`. The label is provably not a function of the invoice.

Left unfixed: `ClassificationAgent` tops out near chance, Auditor override precision measures noise,
the M6 gate "classification beats `keywords_vi` by ≥15 points" is unreachable in either direction,
and the `category_laundering` injection payload (§9.1 #6) has no true category to launder away from.
This is M1's naive-arithmetic-rule finding again: the number would not have been wrong, it would
have been **fabricated**.

Relabelling alone cannot fix it. 87% of descriptions carry no category vocabulary, so there is
nothing to relabel *to*. The text has to change.

**D4 — the machinery for correct labelling exists and is discarded one line before use.**
`defects.AMBIGUOUS_DESCRIPTIONS` (`defects.py:116-123`) already pairs each description with
`(primary_category, plausible_alternative)`. `generate_invoices.py:313` reads it as `desc, _ = …`,
throws the categories away, and falls through to `cat = CATEGORIES[i % 6]`. So even the nine
deliberately-ambiguous invoices — the ones whose descriptions were chosen with care — get a random
label. Most of this spec is generalizing that table to all six categories and using what it returns.

**D5 — `BUDGET_BREACH` is a property of the batch, not of the invoice.** All 12 `budget_overrun`
invoices are MKT; MKT's seeded limit is 50,000,000; the corpus holds **59 MKT invoices totalling
993,010,000**. Any batch containing MKT invoices beyond the three-invoice case breaches far more
than 12 times, so a per-invoice `expected_finding_codes` entry of `BUDGET_BREACH` is only true
relative to a declared batch. The roadmap's gate phrase "batch 30 invoices → ~26 auto, ~4 flagged"
matches neither this nor the corpus's 28% finding rate (84/300 carry ≥1 expected finding, so a
random 30 flags ~8).

### 1.2 What is already true, and must stay true

Two things were checked rather than assumed, and both narrow this spec:

- **The M3 headline numbers are already guarded by tests.** `tests/test_generator_cli.py` builds at
  `count=300, seed=42` — the exact committed corpus — and pins recall and false positives as
  pass/fail properties: `test_clean_invoices_produce_no_findings`,
  `test_no_invoice_carries_an_UNDECLARED_finding`, `test_every_declared_local_finding_is_actually_raised`.
  M4-A does not need to build that harness. It needs to keep those green and report the percentages.
- **`corpus/` is gitignored** (`.gitignore:28`). "Regenerate the corpus" is `make generate`, not a
  300-file commit. Anything that must survive in git has to live in a module, with generated JSON
  emitted beside `manifest.jsonl`.

And one that makes the whole fix cheap: **`department` never reaches the XML bytes.** Verified by
grep across `scripts/generator/` and the generated corpus — it exists only in `manifest.jsonl`.
Fixing D1 alone would rewrite the manifest and leave all 285 XML files byte-identical. D3 does
change the bytes, because it changes line descriptions; the regression contract in §6 is what keeps
that safe.

---

## 2. Scope

### 2.1 In

1. `scripts/generator/descriptions.py` — per-category Vietnamese description pools, three tiers.
2. Generator wiring: category chosen first, description drawn from it, department derived from it.
3. `DEPARTMENTS` derived from `scripts.seed_reference.DEPARTMENTS`.
4. `src/invoice_agent/eval/baseline.py` — the `keywords_vi` non-LLM classifier.
5. `scripts/generator/gate_batch.py` + emitted `corpus/gate_batch.json`.
6. `canonical_budget_rank` fixed to mean canonical position.
7. One recorded vision fixture and the project's first vision accuracy number.
8. Hand-labelled categories for the 8 real fixtures, as a held-out real-data check.
9. Roadmap updates: §3's M4 table, the restated M4 gate, §7's status row.
10. A note recording every number this spec produces.

### 2.2 Out

- Every M4 item proper: agents, `exclude_model`, `CLASSIFICATION_DISPUTED`, vendor profile,
  episodic recall, `signals`, `reconcile_budget`, `graphs/batch.py`. Those are M4-B/C/D.
- Persisting `invoices` rows and duplicate detection. Decided for M4-D: a **serial node after the
  fan-out join**, walking canonical order, so that shuffling arrival order changes neither which
  invoice breaches budget nor which member of a duplicate pair is flagged. Recorded here because
  the gate batch in §5.2 is built to exercise it.
- pgvector on `episodic_decisions` (M7.6).
- The PII git-history purge. Still owed, still not decided, and unrelated to this seam.

---

## 3. The description model

### 3.1 Three tiers

`scripts/generator/descriptions.py` holds one pool per seeded category, each entry tagged with a
tier. The tiers exist to make the baseline **beatable but not trivial** — the property the M6 gate
depends on in both directions.

| Tier | Property | `keywords_vi` baseline | An LLM should |
|---|---|:---:|:---:|
| `keyword` | contains a seeded `keywords_vi` term verbatim | hits | hits |
| `synonym` | real invoice phrasing carrying no seeded term — *"Chi phí tổ chức hội thảo giới thiệu sản phẩm"* | misses | hits |
| `jargon` | vendor abbreviations, mixed Vietnamese/English — *"Triển khai chiến dịch digital branding Q3"* | misses | hits |

Descriptions stay Vietnamese: this is text the system **ingests**, so CLAUDE.md's product-language
exception applies. The `jargon` tier mixes English precisely because real Vietnamese invoices do.

Public surface, deliberately small:

```python
CATEGORY_POOLS: dict[str, tuple[Description, ...]]   # for tests to read
def draw(category: str, rng: Random) -> str: ...     # rng passed in, never module-level
```

`rng` is a parameter, matching `build_vendors` (`vendors.py:70`) — module-level randomness would
break `test_same_seed_produces_identical_bytes`.

### 3.2 The tier mix is a tuned constant

The mix is the knob that sets the baseline's score. Target band **55–70%**: high enough that the
baseline is a real competitor rather than a straw man, low enough that an LLM has 30–45 points of
headroom and can clear the ≥15 the M6 gate demands.

Per CLAUDE.md, the constant carries date + measurement + report filename in a comment. If the band
turns out not to be reachable without contrived text, **the actual number is recorded and the band
is abandoned** — principle 2 says a technique that does not hit its number gets its reason written
down, not its number adjusted.

### 3.3 Department is derived, with declared noise

`CATEGORY_TO_DEPARTMENT` maps the six categories onto the seeded five departments. A declared share
of invoices is routed to a **secondary** department instead — realistic, because any department buys
office supplies, and necessary, because a lossless function from category to department would make
department prediction a free lunch that measures nothing.

That noise rate **is the department accuracy ceiling**, and the note states it as such. An agent
scoring at the ceiling is scoring perfectly; the spec must say so before M4-C reports a number that
looks like a failure. Like the tier mix in §3.2, the rate is a constant carrying date + measurement
+ report filename, chosen during implementation and recorded — not left implicit in the code.

### 3.4 Two groups that need special handling

**`ambiguous_category` (9 invoices)** — takes the *primary* of the pair its description already
carries, instead of discarding it (D4). The alternative is recorded in the manifest so M6 can report
"the classifier chose the plausible alternative" separately from "the classifier was wrong". These
nine are the honest hard cases and should not be scored as ordinary errors.

**`prompt_injection` (6 invoices)** — the payload replaces the description, so drawing a category
from the description is impossible. These take their category from their **stripped twin**. Without
this the §9.3 negative control breaks: the payload would change both the text and the label, and a
classification difference between twin and original would measure the label change rather than the
injection.

---

## 4. `keywords_vi` baseline

`src/invoice_agent/eval/baseline.py`. In `src/`, not `scripts/`, because M4-C's comparison and
M6.7's `scripts/eval.py` both import it — a script would force one of them to shell out or
duplicate it.

```python
def classify(text: str, keywords: Mapping[str, Sequence[str]]) -> str | None: ...
```

Pure: the keyword map is a parameter, so tests need no database. A thin loader reads the seeded
`expense_categories.keywords_vi` for real use, so the baseline scores against the same reference data
the system classifies with — not a second copy that could drift.

Rule: longest matching keyword wins; ties break by declaration order; no match returns `None` rather
than a guess. `None` is reported as its own bucket, never silently counted as wrong — an
87.4% no-match rate is what exposed D3, and a baseline that guesses would have hidden it.

### 4.1 The circularity, stated here rather than discovered at M6

Tier-`keyword` descriptions contain seeded keywords verbatim. The baseline's ~60% is therefore a
**construction parameter, not a measurement** — this spec chooses it. Only the LLM's **margin** over
it is evidence, and even that margin is bounded by the fact that whoever writes the `synonym` tier
decides what an LLM has to be good at.

This is design doc §10.1's circularity problem reappearing one layer up, and the mitigation is the
one M1 used: **the 8 real fixtures carry real vendor descriptions.** M4-A hand-labels their
categories and keeps them as a held-out real-data check, scored and reported however it comes out.
Eight invoices is a weak check. It is also the only non-synthetic one that exists, and a weak real
check beats a strong synthetic one — that is the entire lesson of M1.

---

## 5. The gate batch

### 5.1 Why it is declared rather than sampled

D5 makes `BUDGET_BREACH` meaningless without a named batch, and the roadmap's "~26 auto, ~4 flagged"
matches nothing. So the gate criterion changes from a target count to **exact agreement**: every
invoice the batch flags is one the declaration expects, and every invoice the declaration expects is
flagged. The auto/flag split is then reported as whatever it is.

### 5.2 Composition

`scripts/generator/gate_batch.py` holds the committed source of truth — 30 invoice ids plus the
expected outcome. `generate_invoices.py` emits `corpus/gate_batch.json` beside `manifest.jsonl`,
exactly as the manifest is emitted, because `corpus/` is gitignored.

Chosen for coverage, not for a flattering number:

- the three budget-case invoices (`INV-0001/0002/0003`), whose canonical order is the reverse of
  their file order;
- one exact-duplicate pair and one near-duplicate — corpus duplicates sit 10 indices apart
  (`INV-0060…0068` originals, `INV-0070…0078` copies), so both members land in one batch and M4-D's
  serial join is genuinely exercised;
- one `NEW_VENDOR`, one `PRICE_OUTLIER`;
- the remainder clean.

The declaration records which single invoice breaches MKT and the **full `balance_after` chain**.
M4-D's `test_budget_serialization.py` shuffles arrival order and asserts that chain byte-identical.

---

## 6. Regression contract

Regeneration changes XML bytes. These keep it safe, and all exist already:

| Guard | What it protects |
|---|---|
| `test_generator_cli.py`, 8 tests at `count=300, seed=42` | 300 accounted for · 285 parse · 15 PDF-only · defect ratios · clean → no findings · no undeclared finding · every declared finding raised |
| `test_generator_independence.py` | the new description module must not import parser or domain |
| `test_same_seed_produces_identical_bytes` | determinism survives the new `rng` draws |
| the 8 real fixtures | arbitrate any generator↔validator disagreement — twice already the validator was what was wrong (M1's naive rule 6/8; M2-B's unconditioned guards 3/8) |

XML **structure** is untouched. Only line description text and manifest labels change.

---

## 7. Tests

New, each mutation-verified:

| Test | Mutation that must turn it red |
|---|---|
| `test_every_manifest_department_is_seeded_and_budgeted` | restore the literal `DEPARTMENTS` tuple |
| `test_canonical_budget_rank_is_canonical_position` | reverse the canonical sort |
| `test_description_implies_its_category` | draw the description from a different category's pool |
| `test_injection_twins_share_a_category` | draw the injected invoice's category from its payload |
| `test_gate_batch_breach_matches_domain_budget` | perturb the declared `balance_after` chain |
| `test_baseline_returns_none_rather_than_guessing` | make `classify` fall back to a default category |

The first is the test whose absence hid D1 for two milestones. It is the load-bearing one.

---

## 8. Success criteria

Every item is a command or a recorded number, not a judgement.

1. `keywords_vi` baseline accuracy on the regenerated corpus lands in **55–70%**, recorded with the
   tier mix that produced it — or, if unreachable, the actual number recorded and the band dropped.
2. Department noise rate recorded, and stated in the note as M4-C's accuracy ceiling.
3. `test_every_manifest_department_is_seeded_and_budgeted` green and mutation-verified red.
4. `canonical_budget_rank` equals canonical position; mutation-verified red.
5. `tests/test_generator_cli.py` — all 8 green at `count=300, seed=42`.
6. `corpus/gate_batch.json` emitted; declared breach and `balance_after` chain match
   `domain.budget.consume` on that batch.
7. One vision fixture committed; full suite green under `LLM_MODE=replay` **with no API keys**.
   The project's first vision accuracy number recorded.
8. Hand-labelled real-fixture check recorded, however it scores.
9. Roadmap §3 M4 table, restated M4 gate, and §7 status row updated.
10. `make check` green **on the pushed commit**, and CI green on that commit. Three milestones
   running have failed this, once on a commit that was pushed, ran red in CI, and was not looked at.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| The 55–70% band needs contrived text to hit | Record the real number, drop the band (principle 2). A baseline at 80% is a finding about the corpus, not a failure of this spec |
| The baseline number is by construction, so the LLM's margin is partly authored | Stated in §4.1 rather than buried. The 8 real fixtures are the held-out check. This is the honest ceiling on what a synthetic corpus can prove |
| No vision-capable key configured at implementation time | M4-A ships without item 6 and **re-declares the debt explicitly**. It has now been deferred twice; a third silent deferral is the failure mode, not the deferral itself |
| Regeneration breaks a guard nobody expected | The four guards in §6 run at the real 300/42, not a miniature. If one goes red, the 8 real fixtures arbitrate — not the generator, and not the validator |
| Department noise rate makes M4-C look like it failed | The rate is the accuracy ceiling and is written down here, before any agent reports a number against it |

---

## 10. Open questions — recorded, not silently dropped

1. **Should the six `GOODS`-category invoices exist?** The seed declares seven categories; the
   corpus uses six, never `GOODS` (`156 — Hàng hóa mua vào`). Leaving a seeded category unused means
   the baseline and the classifier both have a class they will never see, which is a mild
   distribution lie. Not fixed here because adding it changes the defect-ratio arithmetic that
   `test_defect_ratios_match_the_design_doc` pins.
2. **Is `SALES` reachable?** Under `CATEGORY_TO_DEPARTMENT` over the seeded five, `SALES` has no
   natural primary category. It is currently reachable only through the noise share. If M4-C finds
   `SALES` unpredictable, that is a corpus property, not an agent failure — flagged here so it is
   not rediscovered as a bug.
3. **NĐ 70/2025/NĐ-CP field check** — still open from M1, still not blocking. `InvoiceLine.item_kind`
   stays `int | None`, and `TSuat` still rejects `KCT`/`KKKNT` loudly because no fixture carries one.
