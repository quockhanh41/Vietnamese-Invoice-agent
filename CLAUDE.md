# Karpathy-Inspired Claude Code Guidelines

A single file to improve Claude Code behavior, derived from [Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876) on LLM coding pitfalls. Source: https://github.com/multica-ai/andrej-karpathy-skills

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks (typo fixes, obvious one-liners), use judgment — not every change needs the full rigor.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.
- Remove imports/variables/functions that YOUR changes made unused. Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
```

## Project-Specific Guidelines

**Safety property — three layers, never thin out any layer.**
The LLM never toggles a flag and never posts an accounting entry (journal entry). Flags are deterministically computed by Python from source-of-truth DB state. Write actions must pass guard code + DB constraints + tests. An agent's response model **must not contain** approval/flag/status fields — structured output *is* the security boundary. Before adding any field to a Pydantic response model, ask: Could the LLM use this to self-authorize?

**Measure first, optimize second.** A new technique is merged only with before/after numbers on the evaluation set, and all LLM metrics must be reported alongside a non-LLM baseline (`keywords_vi`). If it does not beat the baseline by ≥15 points, that node does not need an LLM. Dropped techniques → **record the reason**; this is portfolio content, not a failure.

**Verify with runnable commands, not intuition.** Run `make check` (ruff + mypy + pytest) before declaring completion. If Postgres is needed, run `make dev` first. Every non-obvious constant in code must include a comment stating the **date + measurement + report file name** justifying it.

**Do not expand scope arbitrarily.** The plan is located at `docs/Plan_Project_F_2026-07-25.md`, and design at `docs/project-f-system-design.md`. Milestones have **gates** — do not open subsequent milestones without passing the gate. The "intentionally NOT doing" list in §4 of the plan consists of finalized decisions, not omissions: supervisor agent, Redis, Qdrant, custom OCR, auth/multi-tenant, real ERP integration.

**Pin LangGraph versions — do not bump mid-way.** `langgraph==1.2.9` and `langgraph-checkpoint-postgres==3.1.0` are strictly pinned because the correctness of Human-in-the-Loop (HITL) durability depends on them. Re-pinning requires an intentional decision backed by a passing `test_hitl_survives_restart.py`, not an arbitrary `uv pip -U`.

**Stack**: LangGraph (3 graphs: enrichment / batch / approval) · Postgres 17 + pgvector (system of record **and** checkpoint, two schemas `app` / `langgraph`) · LiteLLM gateway with model-chain fallback × key rotation · FastAPI · Langfuse (SDK v3, env-gated). No Redis, no Qdrant.
