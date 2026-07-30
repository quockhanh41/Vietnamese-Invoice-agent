# Spec — M4-B: vendor memory + the `signals` node (plan item 4.3)

> **Date**: 2026-07-31 · **Milestone**: M4, second of four specs
> **Reads with**: [`Plan_Project_F_2026-07-25.md`](../../Plan_Project_F_2026-07-25.md) §3 M4,
> [`project-f-system-design.md`](../../project-f-system-design.md) §2.2, §3.2, §7, §10.1,
> [`2026-07-31-m4a-corpus-reference-alignment-design.md`](2026-07-31-m4a-corpus-reference-alignment-design.md)
> **Preceded by**: M4-A corpus & reference alignment (closed 2026-07-31)
> **Followed by**: M4-C agents · M4-D batch graph + serial reconciliation

---

## 1. Why this exists, and one thing it cannot do yet

M4-B builds the entity-memory half of M4: the materialized vendor profile, the
escape hatch that keeps it honest, and the `signals` node that turns it into
findings. It is deliberately the deterministic milestone — **no LLM**, so its
numbers cost nothing to repeat and land before any agent can be blamed for them.

Two things were measured while planning it, and both change what the spec
contains.

### 1.1 `PRICE_OUTLIER` cannot currently fire, and the reason is arithmetic

`domain.anomaly.price_outlier` (M2-B) scores an invoice against its vendor's own
`avg_amount` / `stddev_amount`. If those statistics **include the invoice being
scored**, a single extreme point's z-score against a sample containing itself is
bounded by `(n-1)/sqrt(n)`. Verified 2026-07-31, theoretically and empirically:

| n | max achievable z |
|---:|---:|
| 5 | 1.79 |
| 7 | 2.27 |
| 9 | 2.67 |
| 11 | 3.02 |
| 13 | 3.33 |

`PRICE_OUTLIER_Z = 3` therefore requires **n >= 11** before the finding is
*possible*. But `PRICE_OUTLIER_MIN_INVOICES = 5` — where the ceiling is 1.79 —
so the guard admits exactly the vendors for which the rule is arithmetically
incapable of firing. The corpus median is **7 invoices per vendor**.

Measured against the corpus's six declared `price_outlier` invoices:

| Invoice | vendor n | also `new_vendor` | z incl. self | z excl. self |
|---|---:|:---:|---:|---:|
| INV-0088 / 0089 / 0090 | 1 | **yes** | n/a | n/a |
| INV-0091 | 13 | – | 2.93 | **6.38** |
| INV-0092 | 13 | – | 1.75 | 2.13 |
| INV-0093 | 12 | – | 0.14 | 0.15 |

**Recall at Z=3: 0/6 with self included, 1/6 with self excluded.**

Two independent causes, and both are fixed here:

- **The rule.** Statistics must exclude the scored invoice. Non-negotiable — the
  rule cannot fire otherwise. §3.3 explains why this falls out of M4-A's
  persistence decision rather than needing exclusion logic.
- **The corpus.** Three of six are `new_vendor` (`generate_invoices.py:297-301`
  deliberately sources 3 of the 9 `new_vendor` invoices from the `price_outlier`
  group), so they have no history and are undetectable by construction. And
  `OUTLIER_MULTIPLIER = 10` scales the invoice's *own* amount, which against a
  within-vendor coefficient of variation of ~0.95 can land anywhere — INV-0093
  reached z = 0.14, i.e. is not an outlier at all.

Left unfixed, M6 would publish a `PRICE_OUTLIER` recall near zero, and the number
would describe the corpus rather than the detector. That is M4-A's D3 in a
different column.

### 1.2 Episodic recall has no producer and no consumer until M5

Roadmap item **4.4 moves to M5.** `episodic_decisions` is written by design §5.4's
`learn` node and read by §5.1's `suggest` node; both are M5. Confirmed
2026-07-31: `record_episode` and `similar_episodes` have no caller anywhere
outside a single repository test.

Building the recall channel here would mean tuning its two real choices — amount
bucket width and flag-overlap ordering — against hand-seeded fixtures, producing
numbers that measure the fixtures. This follows the precedent M3 already set:
item 3.1 `tools/registry.py` moved to M7 because `ToolSpec` had no consumer
inside the M3 gate. Same reasoning, same project, recorded rather than quietly
dropped.

---

## 2. Scope

### 2.1 In

1. `src/invoice_agent/memory/vendor_profile.py` — incremental transactional
   update of the profile's **statistical** fields, plus `stats_for`.
2. `scripts/rebuild_vendor_profiles.py` — the escape hatch, **with a drift check**.
3. The `signals` node in `graphs/enrichment.py` — `NEW_VENDOR` + `PRICE_OUTLIER`.
4. Generator fix: break the `price_outlier` ∧ `new_vendor` collision; make the
   outlier magnitude relative to the vendor's own scale.
5. Retune `PRICE_OUTLIER_Z` and `PRICE_OUTLIER_MIN_INVOICES` with measured
   before/after numbers, discharging the "PROVISIONAL AND UNMEASURED" admission
   standing in `anomaly.py` since 2026-07-28.
6. Re-verify M4-A's gate batch after regeneration (§6.3).
7. Roadmap update: item 4.4 → M5; M4-B status row.
8. A note recording every number.

### 2.2 Out

- **Episodic recall (4.4)** → M5, per §1.2.
- `NEAR_DUPLICATE` and `DUPLICATE_INVOICE` → M4-D's serial join. Both need a
  corpus-wide view and both must be order-invariant, which is the join's job.
- The profile's **judgement** fields — `rejection_count`, `reviewer_notes`,
  `is_trusted` → M5's `learn`. §3.1 explains the split.
- Persisting `invoices` rows and calling the profile update → M4-D. M4-B builds
  the function; M4-D is its first caller.
- Any LLM. `signals` is a `✗ LLM` node in design §3.2 and stays that way.

---

## 3. Components

### 3.1 `memory/vendor_profile.py`

Design §2.2 says the profile is materialized "at post/approve, in a transaction,
not recomputed per node — too slow in the hot path". There is no approve until
M5, so taken literally nothing would populate the profile during M4 and `signals`
would read an empty one.

**The fields split by who can know them:**

| Field group | Written by | When |
|---|---|---|
| `invoice_count`, `total_amount`, `avg_amount`, `stddev_amount`, `category_histogram`, `modal_category`, `modal_department` | **M4-B**, called from M4-D's serial join | as each invoice is persisted |
| `rejection_count`, `reviewer_notes`, `is_trusted` | M5's `learn` | on a human decision |

This honours §2.2's actual concern — never recompute in the hot path — while
making M4's signals measurable. It also inherits the serial join's
order-invariance: the profile after a batch is identical whatever order the files
arrived in.

**Interface:**

```python
def apply_invoice(conn, *, tax_code: str, amount: Decimal,
                  category: str | None, department: str | None) -> None: ...
def stats_for(conn, tax_code: str) -> anomaly.VendorStats | None: ...
```

`apply_invoice` accumulates incrementally — it reads the current row and folds in
one invoice — so cost is O(1) per invoice rather than a re-aggregation over
history. Variance is accumulated in a numerically stable form (Welford), not as
`sum(x^2) - n*mean^2`, which loses precision catastrophically on VND amounts in
the tens of millions where the two terms nearly cancel.

**`category` and `department` arrive only at M4-C.** They are produced by
`classify`, which does not exist yet, so at M4-B both parameters are `None` and
`category_histogram` / `modal_category` / `modal_department` stay empty. The
signature takes them now so M4-C wires a value rather than changing a signature,
and `apply_invoice` must treat `None` as "nothing to record" rather than writing
a null key into the histogram. The amount-based fields — which are the ones
`PRICE_OUTLIER` depends on — are complete at M4-B, so the milestone's gate is
fully measurable without M4-C.

`stats_for` returns the `anomaly.VendorStats` the M2-B pure functions already
consume. It is an adapter, deliberately thin: the rules stay pure and untouched.

### 3.2 `scripts/rebuild_vendor_profiles.py`

Design §2.2 calls the rebuild "the mandatory escape hatch you must have when you
materialize something derivable". It recomputes every profile from `invoices`.

**It also compares.** A rebuild nobody diffs against the incremental path is
simply a second way to be wrong; the value is in the disagreement. The script
takes `--check`, which recomputes without writing and exits non-zero on any
drift, and that mode is what the test drives.

### 3.3 The `signals` node — and the mechanism that makes it correct

`signals` sits between `audit` and `END` in design §3.2's node table. **`classify`
and `audit` do not exist until M4-C**, so M4-B attaches it directly after
`validate` — every edge `route_after_validate` currently returns `END` on instead
routes to `signals`, and `signals` goes to `END`. M4-C then splices `classify` and
`audit` *in front of* it, leaving `signals → END` untouched. Stated because the
alternative reading — wait for M4-C and wire all three at once — would leave M4-B
with nothing runnable and no way to measure its own gate.

It raises two findings from `domain.anomaly`, both pure and both already written
at M2-B:

- `new_vendor(stats)` → `NEW_VENDOR`
- `price_outlier(amount, stats)` → `PRICE_OUTLIER`

**The scored invoice is absent from its own statistics, for free.** M4-A decided
that persistence happens in M4-D's serial node *after* the fan-out join.
`signals` runs inside `enrich_one` *during* fan-out. So the profile it reads is
necessarily "as of batch start" — prior invoices only. No exclusion parameter, no
subtraction, no special case.

Two consequences that are invisible in the code and therefore must be tested:

1. **Invoice #2 from vendor X does not see invoice #1 of the same batch.** This
   is correct, not a limitation. Fan-out is parallel, so intra-batch visibility
   would make the finding depend on which `Send` finished first — the precise
   order-dependence `test_budget_serialization` exists to disprove.
2. **If anything ever persists before `signals` runs, `PRICE_OUTLIER` degrades
   silently** toward the §1.1 ceiling rather than failing. Nothing errors; the
   recall number just quietly drops. A test pins that the scored invoice is
   absent from its own statistics.

`signals` reads the database, so the enrichment graph gains a second DB surface
beyond `llm_calls`. That is a deliberate change to an M3 decision and is recorded
as such: M3-C confined the graph to `llm_calls` because nothing else needed
state; entity memory does.

---

## 4. The corpus fix

### 4.1 Break the `price_outlier` ∧ `new_vendor` collision

`generate_invoices.py:297-301` builds `new_vendor_indices` as 3 standalone + 3
`budget_overrun` + 3 `price_outlier`. Those last 3 are undetectable: a new vendor
has no history, so the z-score guard rejects them before any threshold applies.

Redistribute to **6 standalone + 3 `budget_overrun`** — count unchanged at 9, and
one realistic co-occurrence preserved. `defects.py`'s own docstring records that
real invoices arrive as "new vendor AND over budget"; that pairing is meaningful
and stays. The `price_outlier` pairing is pathological and goes.

### 4.2 Make the outlier magnitude relative to the vendor's scale

`OUTLIER_MULTIPLIER = 10` multiplies the invoice's **own** amount. Within-vendor
amounts have a coefficient of variation of ~0.95 (measured 2026-07-31 across the
30 vendors with >=5 invoices), so the product lands anywhere relative to the
vendor's distribution — hence INV-0093 at z = 0.14.

Replace with: the outlier's amount is a fixed multiple of the **mean of that
vendor's other invoices**.

> **The choice of statistic is the anti-circularity mechanism, not an
> implementation detail.** The generator uses the **mean**; the detector uses the
> **standard deviation**. Tuning the generator against the detector's own
> statistic would manufacture agreement and produce a recall number that measures
> nothing — design §10.1's circularity problem, which this corpus exists to
> avoid. Using a different statistic makes the invoice genuinely large in a way
> the detector still has to discover. `statistics.mean` is arithmetic rather than
> a domain rule, so `tests/test_generator_independence.py` continues to hold; the
> generator must still not import `domain.anomaly`.

### 4.3 Retune, on numbers

With the scored invoice excluded, the `(n-1)/sqrt(n)` ceiling **disappears
entirely** — z is unbounded — so `PRICE_OUTLIER_Z = 3` becomes reachable and the
question becomes an ordinary trade: recall on the 6 declared outliers against the
false-positive rate on the other 279.

`PRICE_OUTLIER_MIN_INVOICES` is measured at 3 / 5 / 7 / 10. The arithmetic bound
is gone, but four remaining samples still give a noisy sigma, and the guard's
real job is protecting the flag false-positive rate (design §10.4 targets <=5%).

Both constants then carry date + measurement + report filename per CLAUDE.md,
discharging the explicit "PROVISIONAL AND UNMEASURED" comment standing in
`anomaly.py` since 2026-07-28. **That comment says the constants are "set at M6
from docs/eval-report.md"; M4-B moves that earlier and the comment must be
rewritten, not merely appended to** — leaving a stale instruction beside a fresh
measurement is how the next reader ends up retuning against the wrong report.
`NEAR_DUP_WINDOW_DAYS` stays provisional — its consumer is M4-D.

---

## 5. Tests

| Test | Mutation that must turn it red |
|---|---|
| `test_scored_invoice_is_absent_from_its_own_stats` | persist the invoice before `signals` runs |
| `test_signals_sees_no_intra_batch_sibling` | make the profile update run inside `enrich_one` |
| `test_incremental_profile_matches_rebuild` | perturb one accumulator in `apply_invoice` |
| `test_rebuild_check_exits_nonzero_on_drift` | make `--check` always return 0 |
| `test_variance_is_numerically_stable` | swap Welford for `sum(x^2) - n*mean^2` |
| `test_price_outlier_group_is_detectable_in_principle` | restore the `new_vendor` ∧ `price_outlier` overlap |
| `test_signals_node_calls_no_model` | give `signals` a gateway and call it |

The last one matters more than it looks: `signals` is one of the five `✗ LLM`
nodes whose ratio design §3.2 calls "the design, not thrift". A node that quietly
starts calling a model breaks the cost story with no test failing.

---

## 6. Success criteria

1. `PRICE_OUTLIER` recall and false-positive rate on the corpus, **before and
   after** both fixes, recorded. Before is 0/6 and is already measured.
2. `NEW_VENDOR` recall **9/9** with 0 false positives. Anything else means the
   profile write ordering is wrong.
3. Incremental profile vs `rebuild_vendor_profiles.py --check`: **zero drift**
   across the full corpus.
4. `PRICE_OUTLIER_Z` and `PRICE_OUTLIER_MIN_INVOICES` carry date + measurement +
   report filename; the "PROVISIONAL AND UNMEASURED" comment is gone.
5. Every test in §5 mutation-verified red, and the result recorded.
6. **M4-A's artifacts re-verified after regeneration** — `GATE_BATCH_IDS` may no
   longer name the same invoices, because changing the `new_vendor` allocation
   shifts `_allocate_indices` consumption. Re-run the Task 7 discovery script,
   re-confirm the batch's MKT-only membership and the `35M → 15M → −5M` chain,
   and re-confirm the baseline band (descriptions do not change, so 57.2% should
   hold — but it is re-measured, not assumed).
7. Roadmap updated: 4.4 moved to M5 with the reason; M4-B status row.
8. `make check` green **on the pushed commit**, CI green on that commit.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Retuning still leaves recall low | Record the number and report `PRICE_OUTLIER` as weak (principle 2). M4-A recorded its distribution skew rather than hiding it; same standard |
| The generator is tuned until the detector succeeds | §4.2's mean-vs-sigma split is the structural defence, not a promise. If a later change makes the generator compute a standard deviation, the anti-circularity argument is gone and the recall number becomes decorative |
| Regeneration invalidates M4-A's frozen gate batch | Criterion 6 makes re-verification part of M4-B rather than a surprise in M4-D |
| Incremental and rebuilt profiles drift in production | `--check` exists for exactly this and is driven by a test; drift is a hard failure, not a warning |
| The second DB surface in the enrichment graph spreads | Recorded in §3.3 as a deliberate reversal of an M3-C decision. `signals` reads only; it writes nothing |

---

## 8. Open questions — recorded, not silently dropped

1. **Should `stddev_amount` be sample or population?** M4-B uses sample
   (`n-1`), matching `statistics.stdev` and the measurements in §1.1. At n=5 the
   difference moves a z-score by ~11%, which is inside the retune's resolution —
   flagged so a later change is deliberate.
2. **Does a rejected invoice belong in the vendor's spend statistics?** M4-B says
   yes: the statistical fields count everything *persisted*, and rejection is
   recorded separately in `rejection_count` (M5). The alternative — excluding
   rejected invoices — makes the profile depend on approval state and therefore
   on time, which would break the rebuild's determinism. Revisit at M5 if the
   `learn` node makes it awkward.
3. **`NEAR_DUP_WINDOW_DAYS = 7` stays unmeasured.** Its consumer is M4-D's serial
   join; measuring it here would be measuring nothing.
4. Still open from M1, still not blocking: the NĐ 70/2025/NĐ-CP field check.
