# ADR 0007 — Template Baseline Selected Over LLM for Report Commentary

**Date**: 2026-08-06 · **Status**: Accepted
**Context**: M8-A · **Related**: `docs/superpowers/specs/2026-08-06-m8a-reporting-agent-design.md` §4; `docs/notes/2026-08-06-m8a-reporting-raw.json`; `src/invoice_agent/agents/reporting.py`

## Context

M8-A built the fifth agent — `ReportingAgent` — to produce Vietnamese period commentary over financial aggregates (`/reports`). Design spec §4 established a strict, pre-announced shipping rule comparing the LLM-generated commentary against a non-LLM baseline (`render_template`):

> *The LLM ships only if `coverage_llm > coverage_template` **and** `fidelity_llm >= 0.90`. Otherwise `render_template` is the default and the model is not called.*

Per Principle 3 ("every LLM metric ships beside a non-LLM baseline") and Principle 4 ("deterministic work does not go to an LLM"), the decision to ship the LLM commentary is governed purely by empirical measurement rather than subjective preference.

## Decision

**`render_template` is selected as the default implementation for `GET /reports/commentary`. The LLM narrator is NOT enabled in production.**

On 2026-08-06, `scripts/eval_reporting.py` evaluated both approaches on N = 24 report payloads (seed=42, model `groq/llama-3.3-70b-versatile` under `LLM_MODE=live`).

### Measured Evidence (`docs/notes/2026-08-06-m8a-reporting-raw.json`)

| Metric | LLM (`llama-3.3-70b-versatile`) | Template (`render_template`) | Target / Requirement |
|---|---|---|---|
| **Numeric Fidelity** (Pre-guard) | **0.8333** (20/24) | **1.0000** (24/24) | `fidelity_llm >= 0.90` |
| **Coverage** (Over-budget depts named) | **0.5000** (12/24) | **1.0000** (24/24) | `coverage_llm > coverage_template` |

1. **Numeric Fidelity**: Pre-guard fidelity for LLM was 0.8333 (falling below the 0.90 threshold). In 4 of 24 cases, the LLM attempted to round numbers or introduce figures not explicitly present in `ReportFacts`.
2. **Coverage**: The LLM named all over-budget departments in only 50% of payloads, whereas `render_template` achieved 100% coverage deterministically.

Because neither condition of the shipping rule was satisfied (`0.5000 <= 1.0000` and `0.8333 < 0.90`), the pre-announced rule dictates that `render_template` is selected.

## Consequences

- **`GET /reports/commentary` serves `render_template` prose** with `source="template"`.
- **`narrate()`, `ReportCommentary`, and the numeric-fidelity guard remain in the codebase and fully tested** (`tests/test_reporting.py`). The guard remains a published mechanism proving arithmetic safety over free text.
- **Zero API cost and zero latency overhead** on report commentary calls in production.
- **Reproducible artefact**: The raw measurement dataset is recorded in `docs/notes/2026-08-06-m8a-reporting-raw.json`.
