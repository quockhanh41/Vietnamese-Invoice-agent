# ADR 0006 — No Next.js: Streamlit Is the UI Through M8

**Date**: 2026-08-06 (decision made with the user 2026-08-05) · **Status**: Accepted
**Context**: M7 · **Related**: `docs/Plan_Project_F_2026-07-25.md` §0, §3 item 7.5, §5;
`docs/superpowers/specs/2026-08-05-m7-mcp-surface-design.md` §2.1; `src/invoice_agent/ui/`

## Context

Roadmap item 7.5 scoped a Next.js frontend — upload, batch progress over SSE, an approval queue, a
split-view invoice detail (PDF next to extracted fields), and reports — and time-boxed it to 4 days,
with an explicit stopping rule stated at the roadmap's framing decisions (§0): if the approve/reject
loop is not fully working by the end of day 4, stop and keep the Streamlit UI already shipped.

Two facts were already true going into M7:

1. **M5-D shipped a working Streamlit UI on 2026-08-01.** Upload, the approval queue, invoice
   detail, and approve/reject all work today, driven entirely over HTTP: `ui/` imports nothing from
   `invoice_agent` except `config`, enforced by a source guard, so the UI cannot reach
   `submit_decision` or any other write path except through `api/routes/approvals.py`. The demo
   requirement the roadmap set at M0 ("always have a runnable demo from M5 onward") is already met.
2. **The roadmap's own risk assessment names Next.js as the single biggest schedule risk in the
   remaining plan** (§5: "Next.js phình scope nuốt M7–M8" — "Next.js scope creep swallows M7–M8" —
   likelihood "Trung bình-Cao", medium-high) **and states plainly that building it proves none of
   the seven GĐ5 skills** this portfolio exists to demonstrate. The stopping-rule paragraph itself
   (§3, directly under Gate M7) puts it in one sentence: *"Một UI xấu trên agent đúng thắng một UI
   đẹp trên agent làm dở"* — a bad UI on a correct agent beats a beautiful UI on a bad agent.

M7's spec (`docs/superpowers/specs/2026-08-05-m7-mcp-surface-design.md` §2.1) surfaced the actual
question this ADR answers: the stopping rule as written fires at the *end* of a 4-day time-box. A
rule that is only ever evaluated after the time has already been spent is not a stopping rule — it
is a justification written after the fact for whatever the 4 days produced. The rule has value only
if it can be invoked *before* the cost is paid, when the same reasoning that would apply on day 4
already applies on day 0.

## Decision

**Stop before the time-box, not at the end of it.** No Next.js implementation was started for M7.
The Streamlit UI (M5-D) remains the project's demo surface through M8.

This is decided now, in the same milestone the time-box belongs to, for the same reason the roadmap
gives for the rule existing at all: the three facts above (Streamlit already works, Next.js is the
named biggest schedule risk, Next.js proves none of the seven target skills) are as true on day 0 of
the time-box as they would be on day 4. Spending the four days to confirm what is already known
would be four days spent to arrive at a conclusion already available — a sunk cost manufactured on
purpose. Deciding at the start is a decision; deciding at the end, having already spent the budget,
is a rationalisation of a sunk cost.

## Consequences

- **The demo stays Streamlit through M8.** No frontend rewrite is planned; `ui/app.py` +
  `ui/client.py` (M5-D, `docs/notes/2026-08-01-m5d-streamlit.md`) remain the only UI this project
  ships.
- **Gate M7's Next.js criterion is met by its own second branch.** The roadmap's gate text accepts
  either outcome verbatim: *"Next.js làm được vòng approve/reject đầy đủ **hoặc** đã quyết định dừng
  và giữ Streamlit"* — Next.js completes the full approve/reject loop, **or** the decision was made
  to stop and keep Streamlit. This ADR is the record of the second branch being exercised
  deliberately, not the record of a milestone that ran out of time.
- **Item 7.5 is recorded as met by the stopping-rule branch — stated plainly, not presented as an
  accident.** Item 7.6 (pgvector for episodic `reason_embedding`) was separately not started this
  milestone; that is recorded on its own terms in the M7 note
  (`docs/notes/2026-08-05-m7-mcp-surface.md`) rather than folded into this decision, because the two
  have different reasons (schedule risk and skill coverage here; no measurement budget there).
- **No code changed as a result of this decision.** There is nothing to revert if a later milestone
  wants Next.js — this ADR is the record of *why* it was not built at M7, in the register §4's
  "intentionally NOT doing" list uses: a decision, not an omission. A future reader who wants a
  richer frontend inherits this reasoning rather than having to reconstruct it.
- **The MCP surface (the rest of M7) is the actual second-consumer story this milestone tells.**
  Claude Desktop querying the invoice database over MCP, not a new web frontend, is what M7 uses to
  demonstrate that the system has more than one client — which is also a skill Next.js would not
  have added.
