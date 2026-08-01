# M5-B Closeout Note: the approval graph and the HITL loop

**Date**: 2026-08-01 · **Milestone**: M5-B, second of M5's four specs
**Spec**: `docs/superpowers/specs/2026-08-01-m5b-approval-graph-design.md`

Every number below was measured **from a truncated database**. A figure measured
against leftover rows is not a measurement of the batch — M5-A's first gate
number was wrong for exactly that reason, and the clause is load-bearing, not
decorative.

---

## 1. What it does

`batch_graph` opens one `appr:{invoice_id}` thread per flagged invoice, drives it
to `interrupt()`, and **returns**. The batch finishes in seconds while the
reviewer queue sits in Postgres for as long as it takes. A decision arriving
hours later, from a different process, after a deploy, resumes the graph exactly
where it stopped. That is design §1.1's justification for three graphs rather
than one, and portfolio test 4 is the measurement of it.

---

## 2. Mutation results — five, each re-broken from scratch and re-run

| # | Mutation | Case it must redden | Measured |
|---|---|---|---|
| 1 | `apply_decision` gains a `conn` parameter, `post_entry` passes it its own transaction | `test_apply_decision_accepts_no_connection_parameter` | **RED** |
| 1b | *(same mutation)* | `test_apply_decision_commits_so_a_separate_connection_can_see_it` | **GREEN** ⚠️ |
| 2 | a write inside `approval_gate_node` before `interrupt()` | `test_the_gate_node_has_no_side_effect_beyond_the_interrupt` | **RED** |
| 3 | `apply_decision` stops calling `resolve_flag` | `test_deciding_an_invoice_closes_its_flags` (both params) | **RED** |
| 4 | `submit_decision` drops its `VALID_DECISIONS` check | `test_an_unknown_decision_never_reaches_the_graph` | **RED** |
| 5 | `learn` records episodes only on rejection | `test_an_approval_is_remembered_too` | **RED** |

### Row 1b is the whole reason the static guard exists

Under mutation 1 the **behavioural** test stays **green**. It calls
`apply_decision_node(state)` with no connection, so the node opens and commits
its own either way — the test cannot see that a *different* caller could now
hand it `post_entry`'s transaction. In that arrangement the write guard re-reads
`approvals` and finds its own caller's uncommitted write, verifying nothing,
while every test in the suite passes.

The signature check is what makes the arrangement unexpressible. Spec §2.3
predicted this; the measurement confirms it. **Do not delete
`test_apply_decision_accepts_no_connection_parameter` on the grounds that a
behavioural test already covers it.**

---

## 3. Portfolio tests

### Test 1 — durability across a process death (`test_hitl_survives_restart.py`)

**10/10 at `HITL_RUNS=10`.** Now on the **real** approval graph; the spike it used
to run against is retired, as its own docstring instructed.

| Half | Asserted | Measured |
|---|---|---|
| Mechanism | `suggest` = 1, `approval_gate` = 2 across the process boundary | ✅ |
| Consequence | exactly **1** journal entry, **1** audit grant, **1** decided approval | ✅ |

The consequence half is new. M1 could only count node executions because the
spike had no real side effect to count; this graph posts a journal entry, so the
property that actually matters — *the irreversible write happened once* — is now
measured directly.

Counters live in `tests/helpers/hitl_child.py`, never in `approval.py`: the
`approval_gate` = 2 measurement cannot come from checkpoint history, because the
gate's first pass raises `GraphInterrupt` and commits nothing (measured
2026-07-27, M1.5). `test_the_counted_graph_matches_the_real_one` compares the
counted graph's mermaid to the real one, so the copy cannot drift into measuring
something else.

### Test 4 — the batch is not blocked (`test_batch_not_blocked_by_pending.py`)

Asserts the **relationship**, not the counts: `posted` and `opened` both
non-empty at the same moment, disjoint, every parked invoice undecided and
unposted, batch `status='completed'`. `gate_batch.json` declares budget chains,
not an auto/pending split, and M5-A kept that split *reported, not targeted*.

Replayed, keys unset, twice from a truncated database — **byte-identical both
runs**:

| | |
|---|---:|
| inputs | 30 |
| failed (deliberate unreadable PDFs) | 2 |
| enriched | 28 |
| persisted | 27 (1 exact duplicate) |
| **posted** | **15** |
| **parked for approval** | **12** |
| unopened (no invoice row to attach to) | 3 |

`unopened` = the 2 failed PDFs + the 1 unpersisted duplicate. `approvals.invoice_id`
is a NOT NULL FK, so those three cannot have a thread; they are reported rather
than dropped.

---

## 4. The live `max_concurrency` measurement — owed since M4-D, paid here

One live run of the 30-invoice gate batch at **`max_concurrency=4`**, 6 keys per
provider, `LLM_MODE=record`.

| | live | replay |
|---|---:|---:|
| wall time | **20.4s** | 4s |
| enriched / failed / persisted | 28 / 2 / 27 | 28 / 2 / 27 |

### Rate limits

| provider | calls | succeeded | failed | rate-limited |
|---|---:|---:|---:|---:|
| groq | 209 | 33 | 176 | **176** |
| gemini | 122 | 30 | 92 | **90** |

**266 rate-limited calls to produce 63 successful ones.** M4-C measured 177 in a
*serial* run; concurrency 4 produces half again as many.

### `max_concurrency=4` did not hold, and the constant is NOT being changed

No invoice was lost — 28 enriched, 27 persisted, 0 failures beyond the two
deliberate PDFs. The control "works" in the sense that key rotation plus the
model chain absorb every limit. **It works by silently demoting to weaker
models**, and that is the finding:

| node | model that actually served it | succeeded |
|---|---|---:|
| classify | `groq/llama-3.1-8b-instant` — the **fallback** | 24 |
| classify | `groq/llama-3.3-70b-versatile` — the **primary** | 4 |
| audit | `gemini/gemini-3.5-flash` | 25 |
| audit | `groq/openai/gpt-oss-120b` | 5 |

**86% of classifications were served by the fallback model, not the primary.**

The constant stays at 4 because nothing here measures a better value: M4-C's
*serial* run took 177 limits, so concurrency is not the binding constraint —
free-tier quota is. Changing 4 to some other number without a measurement
supporting it would be exactly the fabricated constant M4-D deleted. What this
run establishes is that **the number to tune is not concurrency**.

### Two consequences M6 inherits

1. **The Auditor is a two-model mixture again** — 25 gemini, 5 groq. This is the
   defect the roadmap already records for M4-C (*"the 177 Gemini rate-limits
   mixed two models into one rater; the number M6 publishes must come from one
   rater"*). It is not fixed, and at concurrency 4 it recurs.
2. **M4-C's 95.4% classification accuracy is now in question.** It was measured
   without knowing which model served each call. With the primary serving 14% of
   calls under load, M6 must report accuracy **per serving model**, or throttle
   to pin one.

### The live run found a bug replay structurally cannot reach

The first live attempt **killed the entire batch**:

```
File "src/invoice_agent/graphs/batch.py", line 191, in _join
    assert inv.seller_tax_code is not None
AssertionError
```

A live vision extraction returned an invoice with no seller tax code. The bare
`assert` took all 30 invoices down with it. Replay can never surface this — the
recorded fixture always carries a tax code, so the assert never fires.

Two things were wrong with it: it broke M4-D's own constraint that one bad file
must not end the batch (the reason `enrich_one` wraps every task in
`try/except`), and `assert` is stripped under `python -O`, so the protection
evaporates in exactly the deployment that needs it. It now records
`TAX_CODE_MISSING` and continues, pinned by
`test_an_invoice_with_no_seller_tax_code_is_skipped_not_fatal` and
mutation-verified red against the old `assert`.

**This is the argument for having run it live at all.** Four deferrals of this
debt were four milestones during which a single missing field could have taken
down a production batch.

### Fixtures

Recorded during this run and committed, so CI keeps replaying. **These travel
across platforms**: the text replay key hashes `(model, messages, params)`, all
platform-independent. The vision fixtures do **not** — their key hashes a
rasterized PNG, which differs between macOS/ARM and CI. **Paying this debt does
not pay that one**, which stays on the owed list.

---

## 5. `suggest` returns nothing on a fresh database

**Measured: `[]` for every invoice.** `episodic_decisions` is empty until
reviewers decide things, so the first batch's suggestions are all empty lists.

Reported rather than hidden, and rather than seeded with fixtures to make the
feature look alive. It is also why item 4.4's tuning (amount-bucket width,
flag-overlap ordering) stays deferred: after M5-B the table holds a handful of
real episodes, which is fewer than the fixtures M4-B already refused to tune
against.

`test_suggest_recalls_an_earlier_decision_for_the_same_vendor` proves the recall
path is wired to memory rather than merely to an empty table.

---

## 6. `recover_pending.py`

Run from a **cold process** — the claim is that no in-RAM registry is assumed,
and a test inside the process that created the threads assumes exactly that.

```
0 awaiting a reviewer, 0 orphaned
```

on a truncated database, with both branches covered by
`tests/test_recover_pending.py`: a parked thread found by a process that never
created it, and an `approvals` row with no live interrupt reported as
**orphaned**. The orphan branch is why the script exists — an undecided row with
no thread waiting is an invoice a human waits for forever, and a script
reporting only the healthy case would hide the failure it was written to find.

`snapshot.interrupts` is accessed directly, not through `getattr(…, ())`.
Verified 2026-08-01: `interrupts` is in `StateSnapshot._fields` on langgraph
1.2.9. A default would be error handling for a scenario that cannot happen, and
would turn a future rename into "every thread is orphaned" — silently emptying
the queue — rather than an `AttributeError` naming the cause.

---

## 7. Open questions (spec §9)

1. **An invoice whose reviewer never decides.** Still open. No expiry, no
   escalation; `recover_pending.py` reports it forever. A TTL belongs with the
   API that surfaces the queue (M5-C) or with reporting (M8).
2. **`corrections` are remembered but never applied.** Still open. `learn`
   writes them to `vendors.reviewer_notes`; nothing rewrites `invoices.category`
   or an amount. Applying a correction changes what was booked, so M5-C should
   answer whether the UI even offers it.
3. **`invoices.status` has no state machine.** Still open, and now worse: M5-B
   adds `rejected` and reaches `posted` by a second route, so the number of
   writers grew without a transition guard.
4. **Two `JournalLine` dataclasses.** Still open, still deliberately unfixed.

### Answered by this milestone

- **M5-A spec §8 question 1 — should approving resolve the invoice's flags?**
  Yes, on approve *and* reject: a human has adjudicated them either way. Rows
  survive with `resolved_at` set, so §10.4's false-positive metric can still
  count them. `resolve_flag` has its first caller after three milestones.

---

## 8. What this milestone got wrong before review

Recorded because the pattern is the finding, and this is the sixth milestone in
a row where the review found the closeout claiming more than the work did.

- **Nothing was pushed.** Twelve commits, milestone declared COMPLETE, and CI had
  never run on any of it.
- **No mutation table existed.** Spec §7 criterion 3. The five above were run
  during the review, not during the implementation.
- **The live measurement had not been run.** A fifth deferral of the debt — and
  it was hiding the `assert` crash in §4.
- **`docs/diagrams/approval_graph.mmd` was listed as a delivered artefact.** It
  was never regenerated once `graphs/approval.py` existed, and was **stale**.
  Two of the three graph diagrams had a drift guard since M3-C; the unguarded
  one is the one that rotted. `render_graph.py` now writes all three and
  `test_the_committed_diagram_matches_the_graph` covers the third.
- **The roadmap was never updated**, while the closing commit's message said it
  had been.

One of these was **my own plan's fault**, not the implementer's: Task 12 told
them to run the live batch via `pytest tests/test_batch_not_blocked_by_pending.py`
with `LLM_MODE=record`, but that test hard-codes `monkeypatch.setenv("LLM_MODE",
"replay")` internally. Following the instruction produces a 4-second replay run
that looks like a live measurement and records nothing. The measurement in §4
was taken by driving `build_batch_graph().invoke()` directly.
