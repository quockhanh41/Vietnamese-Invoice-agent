# Note — M8-A ReportingAgent & Numeric-Fidelity Guard

> **Date**: 2026-08-06 · **Milestone**: M8-A · **Status**: Completed & Verified

## Overview

M8-A built design §6.2's fifth agent — `ReportingAgent` — which provides Vietnamese period commentary over financial aggregates from `/reports`. It features an arithmetic numeric-fidelity guard (`numbers_in`) ensuring that every number in the prose was computed by Python in `ReportFacts`, rejecting unsupported numbers and falling back to `render_template`.

Following spec §4's pre-announced shipping rule, an empirical measurement was conducted comparing the LLM-generated commentary against the LLM-free template baseline on N = 24 payloads.

## Key Components Built

1. `src/invoice_agent/agents/models.py`: Added `ReportCommentary` schema (free text `summary` and `observations` only; zero decision fields).
2. `src/invoice_agent/agents/reporting.py`: Implemented `ReportFacts`, `build_facts`, `numbers_in` (Vietnamese thousand separator & decimal normalizer), `fact_numbers`, `render_template`, and `narrate`.
3. `src/invoice_agent/api/routes/reports.py`: Added `GET /reports/commentary?period=YYYY-MM` reading existing aggregates from `db/queries`.
4. `src/invoice_agent/ui/client.py` & `app.py`: Integrated `commentary()` API call and rendered three `st.bar_chart` components (budgets, flags, posting authority) plus period commentary on Streamlit.
5. `scripts/eval_reporting.py`: Evaluated numeric fidelity and department coverage for LLM vs template across N = 24 report payloads.

## Empirical Measurement & Verdict (§4)

- **Dataset**: N = 24 payloads generated with fixed seed=42 under `LLM_MODE=live` using `groq/llama-3.3-70b-versatile`.
- **Artefact**: `docs/notes/2026-08-06-m8a-reporting-raw.json`

| Metric | LLM (`llama-3.3-70b-versatile`) | Template (`render_template`) | Target Rule |
|---|---|---|---|
| **Numeric Fidelity** (Pre-guard) | **0.8333** (20/24) | **1.0000** (24/24) | `fidelity_llm >= 0.90` |
| **Coverage** (Over-budget depts named) | **0.5000** (12/24) | **1.0000** (24/24) | `coverage_llm > coverage_template` |

### Verdict & Shipping Decision (ADR 0007)

The LLM failed to meet the shipping criteria (`0.8333 < 0.90` and `0.5000 <= 1.0000`). Per spec §4, **`render_template` was selected as the default implementation for `GET /reports/commentary`**, recorded in [ADR 0007](file:///Users/quockhanh/Code/learn_AI/LLM/vietnamese-invoice-agent/docs/adr/0007-template-not-llm-for-report-commentary.md).

## Mutation Verification

All 5 mutation scenarios were applied individually and turned their expected test RED:

| # | Mutation | Expected Test | Measured Failure Output |
|---|---|---|---|
| 1 | Delete `if rejected:` check in `narrate()` | `test_a_fabricated_number_rejects_the_whole_commentary` | `AssertionError: assert 'llm' == 'template'` |
| 2 | Drop `_VN_THOUSANDS` handling in `_to_decimal` | `test_vietnamese_thousand_separators_collapse_to_one_number` | `AssertionError: assert set() == {Decimal('6525000')}` |
| 3 | Add `approved: bool = False` to `ReportCommentary` | `test_no_agent_model_can_authorise_anything` | `AssertionError: ... ["ReportCommentary.approved contains 'approv'"]` |
| 4 | Return `Narration(commentary, source="llm")` on rejection | `test_a_fabricated_number_rejects_the_whole_commentary` | `AssertionError: assert 'llm' == 'template'` |
| 5 | Inline a `select(...)` into `api/routes/reports.py` | `test_the_route_module_runs_no_sql_of_its_own` | `AssertionError: 'select(' in the reports route: aggregates come from db/queries` |

## Scope Exclusions

- **Trend comparison across periods**: Out of scope per spec §5 (there is one primary loaded period of real data; multi-period trends would be decorative).

## Verification

- `make check`: Rint clean, mypy clean, all tests passing.
- Keyless path (`LLM_MODE=replay` with no keys): Passed cleanly.
