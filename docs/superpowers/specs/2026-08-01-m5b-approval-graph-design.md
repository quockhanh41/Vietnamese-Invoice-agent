# Spec — M5-B: the approval graph and the HITL loop (plan items 5.1, 5.2, 5.4, 5.5)

> **Date**: 2026-08-01 · **Milestone**: M5, second of four specs
> **Reads with**: [`Plan_Project_F_2026-07-25.md`](../../Plan_Project_F_2026-07-25.md) §3 M5 · §8,
> [`project-f-system-design.md`](../../project-f-system-design.md) §1.2, §1.3, §5, §7.1
> **Preceded by**: M5-A write guard + auto path
> **Followed by**: M5-C FastAPI · M5-D Streamlit

---

## 0. Global constraints

At the top because four milestones running lost their rationales to summarisation
while keeping their procedures. Every one of these can be satisfied by code that
passes all tests while proving nothing.

1. **The node containing `interrupt()` re-runs from the top on resume.** Measured
   2026-07-27 at M1.5 on langgraph 1.2.9: `n1_record` (before the gate) = 1,
   `n2_gate` (contains the gate) = 2. `approval_gate` therefore contains
   `interrupt()` **and nothing else**; every write lives in a node after it and
   `suggest` lives in a node before it.
2. **`apply_decision` must COMMIT before `post_entry` reads.** They must not
   share a connection. The guard's whole value is re-reading `approvals` from
   Postgres (design §5.3 step 2); reading its own uncommitted write verifies
   nothing and looks identical to a working guard in every test that does not
   specifically mutate for it (§5.3).
3. **`ApprovalState` carries plain JSON only.** No `InvoiceData`, no `Finding`.
   This state sits in Postgres for hours or days — no other state in this system
   does — so it must survive a deploy that lands between the interrupt and the
   decision (§2.2).
4. **Deterministic stays deterministic.** `suggest` calls no LLM: it is on the
   path the batch waits for, and design §5.1 says so. `learn` calls no LLM.
   Nothing in this graph asks a model whether to post.
5. **A milestone is not closed until `make check` is green on the PUSHED commit
   and CI agrees**, and the gate must actually execute in CI. Run `make check`
   after every task.
6. **Do not bump `langgraph==1.2.9` / `langgraph-checkpoint-postgres==3.1.0`.**
   Interrupt-and-resume durability is what this milestone ships.

---

## 1. Why this exists

M5-A made posting safe. M5-B makes the human decision that authorises a post
durable — the property design §5.2 calls the largest difference between this
system and "a confirm dialog in a shallow agent demo": the reviewer may decide
three hours later, from a different process, after a deploy.

It also closes the loop the design says is one mechanism rather than two
features: **HITL feeds memory.** A rejection with a reason becomes an episode;
a field correction becomes `vendors.reviewer_notes`, which is what `classify`
reads next time.

### 1.1 What M5-A left standing for this milestone

- `route_outcomes` now routes on persisted flags, so `needs_approval` is a real
  queue: **12 flagged invoices** in the 30-invoice gate batch, measured
  2026-08-01 from a truncated database.
- `resolve_flag` still has **zero callers**. M5-A's spec §8 question 1 comes due
  here and is answered in §4.4.
- `tools/guards.py` already implements the `human_approved` path in full,
  including the `invoice_id` comparison and the idempotency fork. M5-B adds its
  first caller; it does not modify the guard.

---

## 2. Architecture

### 2.1 The graph

```
START → load_snapshot → suggest → approval_gate ⏸ → apply_decision
apply_decision ─cond→ { approved|edited: post_entry, rejected: record_rejection }
post_entry → learn → END
record_rejection → learn → END
```

Node placement is not a style choice — it is constraint 1 made structural.
`approval_gate` holds `interrupt()` alone. M1.5 measured that putting a write in
that node makes the write happen twice across a `kill -9`, and that the
`UNIQUE(invoice_id)` constraint plus M5-A's guard — not the graph shape — is
what actually stops the second journal entry. The shape stops the *rest*.

### 2.2 `ApprovalState` is plain JSON, deliberately

```python
class ApprovalState(TypedDict):
    invoice_id: str            # app.invoices.id, as str
    approval_id: str
    snapshot: dict[str, Any]   # invoice header + lines, for the reviewer
    flags: list[dict[str, Any]]
    suggestions: list[dict[str, Any]]
    decision: str
    decided_by: str            # BARE name; `human:` is added at the audit boundary
    reason: str | None
    corrections: dict[str, Any] | None
    entry_id: str | None
    status: str
```

`decided_by` arrives through `Command(resume=…)` alongside the decision, because
`post_entry` needs it for `audit_log.actor` and `learn` needs it for the episode.
Omitting it was an error in the first cut of this spec, caught while planning:
without it, `post_entry` would have had to re-read `approvals` a second time
purely to learn who decided, or invent a placeholder actor.

Every other state in this system is short-lived: `enrich:` and `batch:` threads
live minutes. An `appr:` thread lives **hours to days** (design §1.3), which means
a checkpoint written on Monday must deserialize on Thursday, after a deploy.
A pydantic model that gained a required field in between will not deserialize; a
dict will. Keeping models out also means `serde.py`'s `ALLOWED_MSGPACK_MODULES`
does not grow, so the M3-C warning surface stays where it was measured.

`suggestions` and `flags` are lists of dicts for the same reason, and because
they go straight into the `interrupt()` payload and into `approvals.suggestions`
/ `approvals.flags_snapshot`, both JSONB.

### 2.3 The transaction boundary — the one that can silently rot

```
apply_decision   connection A   UPDATE approvals SET decision=…   COMMIT
                 ─────────── checkpoint ───────────
post_entry       connection B   SELECT … FROM approvals           ← must see A's row
```

If both nodes ran on one transaction, `post_entry`'s guard would read
`apply_decision`'s own uncommitted write. Every ordinary test would still pass:
the decision is there, the post succeeds, the assertions hold. The guard would
be verifying its own caller. §5.3's mutation is the only thing that can tell the
two apart, and it is mandatory.

### 2.4 The resume seam

There is no HTTP until M5-C, so `graphs/approval.py` exposes the callable the
API will wrap:

```python
def submit_decision(
    *, invoice_id: str, decision: str, decided_by: str,
    reason: str | None = None, corrections: dict[str, Any] | None = None,
) -> dict[str, Any]
```

It resolves `appr:{invoice_id}`, calls
`graph.invoke(Command(resume={...}), config)` and returns the outcome. **M5-C's
route adds no logic** — it parses a request body and calls this. That is what
makes M7's "interrupt is transport-agnostic" artefact a demonstration instead of
a claim: the same function is driven by a test, a script, HTTP and MCP.

`submit_decision` rejects a `decision` outside
`("approved", "rejected", "edited")` before touching the graph, so a typo cannot
resume a thread into an unroutable state.

### 2.5 `open_approval` in the batch graph

Design §5.2: write the `approvals` row, invoke the approval graph on
`appr:{invoice_id}` until it hits `interrupt()`, confirm `__interrupt__` is in
the result, return immediately. Wiring becomes
`route_outcomes → auto_post → open_approval → finalize`.

`approvals.invoice_id` is a NOT NULL FK, so a thread can only be opened for an
invoice that persisted. Of the gate batch's 15 `needs_approval` entries, **12
qualify**; the 2 failed PDFs and the 1 unpersisted duplicate have no invoice row.
Those 3 are returned in a new `unopened` state key with a reason, rather than
silently dropped — the treatment M4-D gave `unroutable`.

One checkpointer is built per `open_approval` call and reused across the
invoices in that batch. Building a `PostgresSaver` per invoice would open 12
connection pools to accomplish nothing.

---

## 3. Scope

### 3.1 In

- `src/invoice_agent/graphs/approval.py` — state, six nodes, `build_approval_graph`, `submit_decision`
- `open_approval_node` in `graphs/batch.py`; `unopened` added to `BatchState`; diagram regenerated
- `scripts/recover_pending.py`
- `learn`'s writes: `episodic_decisions`, `vendors.reviewer_notes`, `vendors.rejection_count`
- `flags.resolve_flag` gains its first caller (§4.4)
- `tests/test_hitl_survives_restart.py` retargeted onto the real graph (portfolio test 1)
- `tests/test_batch_not_blocked_by_pending.py` (portfolio test 4)
- `tests/test_approval_graph.py`, `tests/test_recover_pending.py`
- Deleting `tests/helpers/spike_graph.py` and `tests/test_spike_graph.py`
- One **live** batch run at `max_concurrency`, measured and recorded (§6)

### 3.2 Out

- FastAPI routes and SSE — **M5-C**. Streamlit — **M5-D**.
- Episodic tuning (item 4.4's amount-bucket width, flag-overlap ordering).
  Deferred again, with the reason: after M5-B the database holds a handful of
  real episodes, which is fewer than the fixtures M4-B refused to tune against.
- Any change to `tools/guards.py`. M5-B is its first `human_approved` caller;
  if the guard needs a change, that is a finding, not a task.
- Dropping the `spike_counter` table. It keeps its name and becomes the
  node-execution counter for the retargeted durability test (§5.1). Renaming it
  is a migration whose only payoff is tidiness.
- The throttled Auditor re-measurement, the PII git-history purge, CI-platform
  vision fixtures — untouched by M5.

---

## 4. Components

### 4.1 `load_snapshot`

Reads `invoices`, `invoice_line_items` and open `invoice_flags` into plain dicts.
`Decimal` and `date` are stringified here, at the boundary, because the snapshot
lands in JSONB and in a checkpoint — not deep in a node where the conversion
would be invisible.

### 4.2 `suggest` — no LLM, and honest about being empty

`episodic.similar_episodes(vendor_tax_code=…, category=…, limit=5)`, straight
into state. Design §5.1 forbids an LLM call here because the batch is waiting on
this path.

**On a fresh database this returns `[]` for every invoice**, because
`episodic_decisions` is empty until reviewers decide things. The closeout note
reports that as measured rather than seeding fixtures to make the feature look
alive. It is also the reason item 4.4's tuning stays deferred.

### 4.3 `approval_gate`

```python
def approval_gate_node(state: ApprovalState) -> dict[str, Any]:
    decision = interrupt({
        "invoice": state["snapshot"],
        "flags": state["flags"],            # Vietnamese narratives — a person reads these
        "suggestions": state["suggestions"],
    })
    return {...}
```

Nothing else. No logging, no status write, no counter. Anything added here runs
twice across a restart.

The flag narratives stay **Vietnamese**: they are read by a Vietnamese
accountant, which CLAUDE.md's language rule puts on the product side of the line.

### 4.4 `apply_decision` — and `resolve_flag`'s first caller

Writes `approvals.decision`, `decided_by`, `reason`, `field_corrections`, then
calls `flags.resolve_flag` on **every** open flag for the invoice, on approve and
on reject alike. Commits.

Resolving on both outcomes is the answer to M5-A spec §8 question 1. A human has
adjudicated those flags either way; leaving them open means `invoices.status`
says `flagged` forever, `route_outcomes` keeps treating the invoice as blocked,
and M5-C's queue re-serves invoices somebody already decided. The rows are
**not deleted** — `resolved_at` is set, so the evidence survives and the flag
false-positive metric in §10.4 can still count them.

One consequence to state rather than discover: resolving flags makes the DB
trigger stop blocking `auto_clean` for that invoice. It cannot be re-posted
anyway — `UNIQUE(invoice_id)` and M5-A's idempotency fork both hold — but the
reasoning belongs in the code, not in somebody's memory.

### 4.5 `post_entry` / `record_rejection`

`post_entry` builds the entry exactly as `auto_post` does — category → seeded
`expense_categories` row → `CategoryAccounts` → `accounting.build_journal_entry`
— then calls `guards.create_journal_entry(posting_authority="human_approved",
approval_id=…, actor=…)`.

**`decided_by` is a bare name; the `human:` prefix belongs to the audit boundary
alone.** `approvals.decided_by` stores `"ketoan"`; `audit_log.actor` stores
`"human:ketoan"`, per the `'system' | 'human:<name>' | 'mcp:<client>'` convention
in `db/models.py`. Stated because both M5-A's tests and design §5.3 pass
`"human:ketoan"` around informally, and prefixing an already-prefixed value
yields `human:human:ketoan` — a corruption no constraint catches and no test
would notice.

A `GuardDenied` here is **not** swallowed. In `auto_post` a denial routes the
invoice to a human; here a human has already decided, so a denial means the guard
and the reviewer disagree, and that must surface rather than be absorbed. It
propagates, and the thread stays resumable.

`record_rejection` sets `invoices.status = 'rejected'`.

### 4.6 `learn`

- `episodic.record_episode(...)` on **every** decision, not only rejections.
  Design §5.4 names the reject arrow, but `suggest` asks "what happened last time
  for this vendor" and approvals are the majority answer — a memory holding only
  refusals biases every suggestion it makes. Deliberate extension past the design
  doc's literal wording, recorded here so it is a decision rather than a drift.
- reject → `vendors.rejection_count += 1`
- `corrections` present → `vendors.reviewer_notes`. This is the field `classify`
  reads next time, which is the whole point of §5.4.

### 4.7 `scripts/recover_pending.py`

`approvals WHERE decision IS NULL` is the list; LangGraph has no "list pending
threads" API (design §5.5). For each row, `graph.get_state(config)` and check
whether an interrupt is actually pending.

Two outcomes, reported separately:

- **pending** — row undecided, interrupt live. Goes back in the queue.
- **orphaned** — row undecided, **no** live interrupt. The thread never started,
  or its checkpoints were lost. Surfacing these is the point: an orphan is an
  invoice a human will wait for forever, and a script that reports only the
  healthy case would hide exactly the failure it exists to find.

---

## 5. Testing

### 5.1 `test_hitl_survives_restart.py`, retargeted — portfolio test 1

The child process runs the **real** approval graph to its interrupt; the parent
SIGKILLs it and asserts `returncode == -9`; a new process resumes with
`Command(resume=…)` and posts.

**Counting stays test-side.** The assertion "`suggest` ran once, `approval_gate`
ran twice" cannot come from checkpoint history — M1.5 measured why: the gate's
first pass raises `GraphInterrupt` and commits nothing, so it appears once in
`trace` whether its body ran once or twice. So `tests/helpers/hitl_child.py`
wraps the real `suggest_node` and `approval_gate_node` in counters that write to
`spike_counter` **before compiling**. `approval.py` never learns it is being
measured, and the table earns its keep.

Asserts both halves:

| | |
|---|---|
| Mechanism | `suggest` = 1, `approval_gate` = 2, across the process boundary |
| Consequence | exactly **1** `journal_entries` row, **1** audit grant, **1** decided `approvals` row |

The consequence half is new and is the stronger of the two: M1 could only count
node executions because the spike had no real side effect to count. This one does.

### 5.2 `test_batch_not_blocked_by_pending.py` — portfolio test 4

Runs the gate batch and asserts the batch reached `status='completed'` **while**
approvals sit undecided — clean invoices posted, flagged invoices parked.

The roadmap's "26 sạch post được trong khi 4 đang chờ" is not the assertion.
That phrase predates the corpus; M4-A found it matches neither the 28% finding
rate nor the gate batch, and M5-A's routing fix moved the split again.

**The test asserts the relationship, not the counts.** `gate_batch.json` declares
budget chains, not an auto/pending split — M5-A deliberately kept that split
*reported, not targeted*, because the finding rate is a property of the corpus.
Pinning 15/12 here would quietly reverse that decision and turn portfolio test 4
into a change-detector for corpus generation. So it asserts:

- `posted` is non-empty **and** undecided `approvals` is non-empty *at the same
  time* — the actual claim, and the one that justifies three graphs;
- every posted invoice has a `journal_entries` row and no undecided approval;
- every pending invoice has **no** `journal_entries` row;
- the batch's own `status` is `completed`, and `batches.status` agrees.

The measured 15/12 goes in the closeout note as a number, where a number that
moves is information rather than a red build.

### 5.3 Mutations

| Mutation | Turns red |
|---|---|
| `apply_decision` skips its commit (shares `post_entry`'s transaction) | `test_the_guard_sees_a_committed_decision_not_its_own_write` |
| `approval_gate` gains a write before `interrupt()` | the durability test's consequence half — 2 journal entries |
| `apply_decision` stops calling `resolve_flag` | `test_deciding_an_invoice_closes_its_flags` |
| `submit_decision` accepts an unknown decision string | `test_an_unknown_decision_never_reaches_the_graph` |
| `learn` records episodes only on rejection | `test_an_approval_is_remembered_too` |

Five, each re-broken from scratch and re-run. M1's exit review found four tests
that passed while guarding nothing; every milestone since has found more.

### 5.4 `recover_pending`

Run from a **cold process** — the claim being tested is that the API never
assumes an in-RAM registry survived, and a test in the process that created the
threads assumes exactly that. Covers both outcomes, including a deliberately
orphaned row.

---

## 6. The live `max_concurrency` measurement

Owed since M4-D and deferred four times; 6 keys per provider are now configured,
so it is payable. One live run of the 30-invoice gate batch at
`max_concurrency=4`, recording:

- 429s absorbed by key rotation, per provider
- invoices that failed, if any
- wall time, against the replay run's wall time

M4-C measured **177** Gemini rate-limits in a *serial* run, so this is the first
honest test of the concurrency control. Whatever it reads is reported: if 4 is
too high, that is the finding and the constant changes with the measurement and
report filename beside it, per CLAUDE.md.

Fixtures recorded during the live run are committed so CI keeps replaying. The
gate itself still runs with every key unset.

**These fixtures do travel across platforms, unlike the vision ones.** The replay
key hashes `(model, messages, params)`, all platform-independent for text calls.
M4's outstanding "vision fixtures do not travel" debt comes from the vision key
hashing a *rasterized PNG*, which differs between macOS/ARM and CI. Paying this
debt does not pay that one, and the note must not imply it does.

---

## 7. Success criteria

| # | Criterion | Evidence |
|---|---|---|
| 1 | Retargeted `test_hitl_survives_restart.py` green **10/10** at `HITL_RUNS=10` on a clean CI runner | CI run id |
| 2 | `test_batch_not_blocked_by_pending.py` green: batch `completed` with approvals still undecided | measured counts |
| 3 | 5/5 mutations red, re-broken from scratch | mutation table in the note |
| 4 | Reviewer feedback reaches `vendors.reviewer_notes` **and** `episodic_decisions` | query in the note |
| 5 | `recover_pending.py` lists pending threads from a cold process and reports orphans separately | note |
| 6 | Live batch at `max_concurrency=4`: 429s, failures and wall time recorded | note |
| 7 | `suggest`'s empty-on-a-fresh-database behaviour reported as measured | note |
| 8 | `make check` green on the **pushed** commit, CI agreeing, gate executing in CI | run id |

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Retargeting portfolio test 1 destabilises a green test | The spike is deleted only after the retargeted test is green 10/10. If it cannot be made green, the spike stays and this is reported as a failure to deliver, not quietly reverted |
| `apply_decision`/`post_entry` sharing a connection is invisible | §5.3 mutation 1 is the only detector, and it is mandatory |
| The live run burns free-tier quota mid-milestone | It is one run of 30 invoices. Replay fixtures are recorded from it so it never needs repeating |
| `learn` writing on every decision inflates `episodic_decisions` | 30 rows per gate batch. pgvector embeddings are M7 and optional; SQL recall at `limit=5` does not care |
| A `GuardDenied` in `post_entry` propagates and looks like a crash | Intended — a human decided and the guard disagreed. §4.5 states it; the thread stays resumable so the reviewer can be shown the refusal |

---

## 9. Open questions — recorded, not silently dropped

1. **What happens to an invoice whose reviewer never decides?** No expiry, no
   escalation. `recover_pending.py` reports it forever. A TTL belongs with the
   API that surfaces the queue (M5-C) or with reporting (M8), not here.
2. **`corrections` are recorded but never applied to the invoice.** `learn`
   writes them to `vendors.reviewer_notes`; nothing rewrites
   `invoices.category` or an amount. Design §5.4 only claims the memory arrow.
   Applying a correction to the posted entry is a bigger question — it changes
   what was booked — and M5-C should answer whether the UI even offers it.
3. **`invoices.status` still has no state machine** (inherited from M5-A §8.3).
   M5-B adds `rejected` and reaches `posted` by a second route, so the number of
   writers grows without a transition guard.
4. **Two `JournalLine` dataclasses** (M5-A §8.4). Still open, still unfixed.
