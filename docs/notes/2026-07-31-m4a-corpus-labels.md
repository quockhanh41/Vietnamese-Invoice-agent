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

## Department Classification Boundary

- **`DEPARTMENT_NOISE_RATE`:** `0.15` (15%)
- **M4-C Ceiling Note:** `DEPARTMENT_NOISE_RATE` IS the theoretical accuracy ceiling on department classification for any model or agent in M4-C, because 15% of invoices carry secondary department assignments that no classifier can recover from invoice text alone. Zero noise would make department a free lunch; 15% noise tests agent robustness under realistic org-chart ambiguity.
