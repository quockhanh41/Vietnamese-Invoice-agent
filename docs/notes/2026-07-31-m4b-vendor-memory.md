# M4-B Vendor Memory & Signals Analysis Note

**Date:** 2026-07-31  
**Milestone:** M4-B (Vendor Memory & Signals)  
**Author:** Antigravity AI  

---

## Executive Summary

Before M4-B, the `PRICE_OUTLIER` anomaly signal detected **0 of 6** declared outliers in the benchmark corpus. This document records the empirical diagnosis, the structural fixes applied to both the graph ordering and the synthetic invoice generator, the parameter sweep results, and the zero-drift verification between incremental updates and profile rebuilds.

---

## 1. Root Cause Analysis

Two independent defects caused the zero recall prior to M4-B:

### 1.1 Self-Inclusion Bound
Calculating the z-score of an invoice against a sample that **includes** itself imposes a strict mathematical upper bound on the achievable z-score.

For a sample of $n$ points where one point is an extreme value $x_0$, the z-score $Z(x_0)$ relative to the sample mean and sample standard deviation is bounded by:
$$Z(x_0) \le \frac{n-1}{\sqrt{n}}$$

| $n$ (Sample Size) | Max Possible Z-Score | Threshold $Z=3.0$ Achievable? |
|-------------------|----------------------|--------------------------------|
| 3                 | 1.15                 | ❌ No                         |
| 5                 | 1.79                 | ❌ No                         |
| 7                 | 2.27                 | ❌ No                         |
| 10                | 2.85                 | ❌ No                         |
| 11                | 3.02                 | ✅ Yes (only for $n \ge 11$)   |

With corpus median vendor history size $n \approx 7$, requiring $Z \ge 3.0$ while including the scored invoice made detection arithmetically impossible.

**Fix:** `signals_node` executes in graph fan-out **before** invoice persistence (which occurs in M4-D's serial node). Thus, the vendor profile read at batch start excludes the scored invoice from its own statistics for free.

### 1.2 Generator Sourcing & Scale Misalignment
- **Corpus Sourcing Overlap:** 3 of 6 `price_outlier` invoices were simultaneously assigned to `new_vendor`. A new vendor has no prior invoice history ($n=0$), causing `anomaly.price_outlier` to reject the invoice immediately before any z-score threshold was evaluated.
- **Flat Multiplier Scaling:** `OUTLIER_MULTIPLIER = 10` scaled an invoice's own amount without regard to vendor peer distribution. Within-vendor amounts had a coefficient of variation $CV \approx 0.95$, causing `INV-0093` to land at $z = 0.14$.

**Fix:** 
1. Separated `new_vendor` allocation (6 standalone + 3 `budget_overrun`) from `price_outlier`.
2. Generator sizes `price_outlier` target relative to the **mean** of peer invoices for that vendor (`OUTLIER_VENDOR_MEAN_MULTIPLE = Decimal("8")`), ensuring anti-circularity (the detector uses standard deviation $\sigma$). Integer scaling preserves exact VAT and line amount relationships.

---

## 2. Parameter Sweep Results

Evaluated across the 300-invoice corpus (seed 42) with scored invoices excluded from vendor profiles:

| $Z$ Threshold | $MIN\_N$ | Outlier Recall | False Positives | FP Rate (%) |
|:-------------:|:--------:|:--------------:|:---------------:|:-----------:|
| 2.5           | 3        | 6/6            | 10              | 3.4%        |
| 2.5           | 5        | 6/6            | 10              | 3.4%        |
| 2.5           | 7        | 6/6            | 6               | 2.0%        |
| 2.5           | 10       | 4/6            | 0               | 0.0%        |
| **3.0**       | **5**    | **6/6**        | **7**           | **2.4%**    |
| 3.0           | 7        | 6/6            | 4               | 1.4%        |
| 3.0           | 10       | 4/6            | 0               | 0.0%        |
| 3.5           | 3        | 6/6            | 5               | 1.7%        |
| 3.5           | 5        | 6/6            | 5               | 1.7%        |
| 3.5           | 7        | 6/6            | 2               | 0.7%        |
| 3.5           | 10       | 4/6            | 0               | 0.0%        |
| 4.0           | 5        | 6/6            | 5               | 1.7%        |
| 4.0           | 7        | 6/6            | 2               | 0.7%        |

**Chosen Parameters:** `PRICE_OUTLIER_Z = Decimal("3.5")`, `PRICE_OUTLIER_MIN_INVOICES = 7`.
- **Recall:** 6/6 (100.0%)
- **False-Positive Rate:** 0.7% (2 of 294 clean invoices; design §10.4 target $\le 5.0\%$)

> **Corrected 2026-07-31 by the M4-B exit review.** The first pass chose
> `Z=3`, `MIN_N=5` — FP 7 (2.4%) — which is **strictly dominated by rows in the
> table above**: `(3.5, 7)` and `(4.0, 7)` both give FP 2 at the same 6/6
> recall. Those were also the pre-existing values, so despite the commit
> title nothing was actually retuned. The sweep was run and recorded
> accurately and then not acted on, which is a different failure from the ones
> M1–M3 kept producing and worth recording as such: the measurement was honest,
> the decision rule was not applied.
>
> The rule is: highest recall → FP ≤ 5% → **larger `MIN_N`**, because a bigger
> sample makes sigma less noisy. Eight pairs tie at 6/6; `MIN_N=7` wins.
>
> **`Z=3.5` over `Z=4.0` on margin**, both giving FP=2. Declared outliers score
> `[4.2, 5.3, 5.7, 6.3, 7.3, 10.7]`, so `Z=4.0` sits **0.24** from losing a true
> positive while `Z=3.5` has **0.74** — three times the headroom at identical
> cost. `MIN_N=7` and not higher because the thinnest outlier vendor has 9
> peers; `MIN_N=10` is what drops recall to 4/6.
>
> **The two classes overlap, and this is the honest limit.** The highest-scoring
> non-outlier reaches **z=5.82**, above the lowest true outlier's **4.24**. No
> threshold separates them, so **FP=2 is the floor at full recall** — chasing
> zero false positives here costs real detections. That is a property of the
> corpus and of z-scoring on ~10 samples, not a tuning failure.

---

## 3. Signal Performance Summary

1. **`PRICE_OUTLIER`**: Recall 6/6 (100%), FP Rate **0.7%** (2/294 clean invoices flagged).
2. **`NEW_VENDOR`**: fires on **all 51 first sightings**; 9 of those carry the injected
   `new_vendor` defect. Scored against that label it reads as 42 false positives —
   **14% of the corpus, which would blow design §10.4's 5% ceiling on its own.**

   **The metric was wrong, not the rule.** `anomaly.new_vendor` asserts a true
   property: this vendor has no history. The `new_vendor` *defect label* marks
   something different — that the generator deliberately injected a
   never-before-seen vendor. Declaring all 51 first sightings as defects would
   conflate an injected defect with a natural property of any corpus read in order.

   **Decision, pinned by `test_new_vendor_is_excluded_from_the_flag_false_positive_metric`:**
   the flag false-positive rate covers findings that **assert something is wrong**.
   `NEW_VENDOR` is severity `info` and asserts something is **new**, which is true
   every time it fires — reviewer context, not an error claim — so it is excluded.
   Severity is the discriminator: `warn` and `critical` count, `info` does not.
   The M4-B plan's criterion of "9/9 with 0 false positives" was itself the defect.
3. **Zero Drift Verification**: Rebuilding vendor profiles via `scripts.rebuild_vendor_profiles --check` against incrementally updated profiles yielded **0 drifted fields**.
4. **Roadmap Relocation**: Item 4.4 (`episodic_decisions` tuning) was moved to M5 where both its producer (`learn` node) and consumer (`suggest` node) live.
