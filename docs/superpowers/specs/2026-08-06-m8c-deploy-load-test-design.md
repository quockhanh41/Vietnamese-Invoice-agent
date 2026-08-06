# Spec — M8-C: the public URL, the 100-invoice load test, and two things that need the user (items 8.5, 8.6)

> **Date**: 2026-08-06 · **Milestone**: M8 · **Status**: design approved, implementation depth.
>
> Reads with roadmap `docs/Plan_Project_F_2026-07-25.md` §3 (M8, items 8.5–8.6), §2.1 (the $0 stack,
> where "Deploy | HF Spaces free / Railway free" is marked *quyết ở M8*), and §5 (risks).
>
> Runs after **M8-B**. The README's "Live demo" section stays PENDING until this spec's Space
> answers.

---

## 0. Global constraints

From `CLAUDE.md` and roadmap §0. Not restated per section.

- **$0.** Free tier end to end, and nothing that starts billing when a recruiter clicks it in six
  months.
- **The demo cannot break on someone else's rate limit** — roadmap §5 names this risk and names its
  mitigation: `LLM_MODE=replay`. The Space runs replay. No provider key is ever set as a Space secret.
- **This repo has leaked PII twice** (roadmap §1). A public deployment is where that stops being
  theoretical.
- **A milestone is not closed until `make check` is green on the pushed commit and CI agrees.**

---

## 1. Deploy — HF Spaces (Docker) + Neon Postgres

Chosen by the user 2026-08-06 over a snapshot-only demo, Railway and Fly: §2.1 already names HF
Spaces, the user has an account (`quockhanh41`), and Neon's free tier gives real Postgres with
`pgvector` — so the deployed thing is **the system**, not a screenshot of it.

### 1.1 One container, one port

HF Spaces exposes exactly one port (7860). The container runs what `make demo` runs:

```
uvicorn invoice_agent.api.app:app --port 8000     (background, loopback only)
streamlit run src/invoice_agent/ui/app.py --port 7860
```

`ui/client.py` already reaches the API through a configured base URL, so the UI talks to
`127.0.0.1:8000` and nothing else is exposed. The M5-D boundary holds unchanged: the UI is an HTTP
client and imports nothing from `invoice_agent` but `config`.

**Deliverables**: `Dockerfile`, `docker/entrypoint.sh`, `docs/deploy-hf-spaces.md`.

### 1.2 Neon needs the two-role setup reproduced by hand

Local Postgres gets its schemas from `scripts/init_db.sql` via the compose init hook. **Neon has no
such hook**, and the safety property depends on the split M2-A measured: `invoice` (owner, superuser
locally) versus `invoice_app` (runtime role), because a `REVOKE` against a superuser lands in the ACL
and is then bypassed. `audit_log`'s REVOKE UPDATE/DELETE — layer 2 of the three-layer property — is
worthless if the app connects as the owner.

So `docs/deploy-hf-spaces.md` carries a **provisioning transcript**, not a paragraph: the exact
`psql` invocation creating both schemas and the runtime role, then `alembic upgrade head`, then
`scripts/seed_reference.py`, then the demo load. Space secrets: `DATABASE_URL`,
`RUNTIME_DATABASE_URL`, `LLM_MODE=replay`, `API_BASE_URL`.

**To verify during implementation, not assert here**: that Neon's free tier permits
`CREATE EXTENSION vector` and a second role. If either fails, the fallback is the snapshot-only
Streamlit demo (the option the user declined) and that becomes a recorded finding, not a silent
substitution.

### 1.3 Migrations on boot, seed once

The entrypoint runs `alembic upgrade head` — idempotent, and the alternative is a container that
crashes on first boot against an empty database. Seeding runs only when the invoice table is empty,
so a Space restart does not duplicate the demo dataset.

### 1.4 What a stranger can do, stated honestly

`scripts/seed_demo.py` loads a fixed, replay-backed set of invoices — the same shape as M7's
`load_gate_overspend.py`, which already produces a real over-budget answer (MKT over by 6,525,000 in
2026-07). The visitor sees real flags, a real approval queue, a real journal entry, real charts and
M8-A's commentary.

**The upload page is the honest edge.** In replay mode an invoice with no recorded fixture raises
`LLM_MODE=replay and no fixture matched any model in the chain` at the classify node, and the batch
records it as failed (M4-D's 28 enriched / 2 failed path). The demo therefore leads with a **sample
picker** over corpus invoices that are fixture-backed, keeps arbitrary upload available, and says in
Vietnamese on the page that a new file will parse and validate deterministically but cannot be
classified without a live key. That is the true statement; hiding the upload button would be a
prettier lie.

**Not exposed publicly**: `MCP_ALLOW_APPROVALS` stays default-false, no provider keys, no
`invoice-xml://` MCP resource (the Space runs no MCP server), and the demo dataset is synthetic —
none of the eight real vendor fixtures ship in the image.

---

## 2. Load test — 100 invoices

`scripts/load_test.py`, artefact `docs/notes/2026-08-06-m8c-load-test-raw.json`.

**Step 0, before anything is measured: replay coverage.** 890 fixtures exist in
`tests/fixtures/llm_cache`; whether they cover 100 invoices' classify **and** audit prompts is
**unverified at the time of writing**. The script computes coverage first and refuses to report a
throughput number for a run that silently fell back to fewer invoices. If coverage is short, the test
runs on the covered subset and reports **the subset size in the headline**, never "100" with a
footnote.

**Measured**: wall-clock total; per-invoice p50/p95; enriched / failed / persisted counts; peak
`max_concurrency` (stays 4 — M5-B measured that free-tier quota, not concurrency, is the binding
constraint, and nothing here re-measures that).

**Where it runs, and what the number means**: locally, on the dev machine, with the machine named in
the note. A free HF CPU Space is not a performance-claim environment; the Space gets a *smoke* check
(health, one report endpoint, one invoice detail) and no timing claim. Publishing a p95 measured on
free shared CPU as though it were a system property is exactly the kind of number this project keeps
refusing to print.

---

## 3. Blog post (8.6)

`docs/blog/2026-08-06-invoice-agent-writeup.md`, English, built only from what is already measured.
The spine is the honest one:

- the three-layer safety property, and why the structured output *is* the boundary;
- **the multi-agent experiment produced a negative result** — Auditor override precision 0.000
  (CI 0.000–0.243, n=12, underpowered), worse than M4-C's 12.5% once a single rater was pinned — and
  publishing it is the point;
- the deterministic layer at 0% false positives before any LLM existed;
- the reviews that found tests guarding nothing, which is the most transferable material in the repo.

No number appears in the post that is not in `docs/eval-report.md` or a `docs/notes/` artefact.

---

## 4. The PII git-history purge — planned here, executed only on your word

Six PII-carrying vendor PDFs and the M1 tax-code leak remain reachable in `origin`'s history.
Deferred four times. Making the repo public-facing is the moment it stops being deferrable.

**This spec delivers a plan, not a rewrite**: `docs/notes/2026-08-06-m8c-pii-purge-plan.md` naming
the exact blobs and paths, the `git filter-repo` invocation, the force-push, what breaks for anyone
who has cloned, how to verify afterwards (`git log --all --diff-filter=A` over the paths, plus a
`gitleaks` full-history run), and the fact that **GitHub retains unreachable objects until garbage
collection** so the purge must be paired with checking whether the repo was ever public.

Rewriting `origin`'s history is irreversible and it is the user's repo. Implementation stops at the
plan and asks.

---

## 5. Scope — out

- Any paid tier, any autoscaling, any CDN.
- Auth on the public demo — roadmap §4 lists auth/RBAC as deliberately not built; the Space is a
  demo with synthetic data and says so.
- An HTTP transport for the MCP server (M7 §2.3 deferred it; it needs an auth story this project
  deliberately does not have).
- Recording the video (M8-B §3.3 — the user's).
- Executing the history rewrite (§4).

---

## 6. M8-C gate

| Criterion | Evidence required |
|---|---|
| Public URL answers | a stranger loads the Space, sees seeded data, opens the approval queue — screenshot + URL in the note |
| The deployed thing is the system | Neon Postgres with both roles; `audit_log` REVOKE verified **against the deployed DB**, not assumed |
| The demo cannot break on rate limits | Space runs `LLM_MODE=replay`, zero provider keys in secrets |
| Load test on 100 invoices | coverage-checked; enriched/failed/persisted + p50/p95 + wall-clock, machine named |
| Blog post drafted | every number traceable to `eval-report.md` or a note |
| PII purge decided | plan written; executed **or** recorded as declined — not left silent a fifth time |
| `make check` green on the pushed commit, CI agreeing | CI run URL on the closing SHA |

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Neon free tier refuses `pgvector` or a second role | §1.2 verifies before building on it; failure becomes a recorded finding and the snapshot demo is the fallback |
| Neon free tier suspends an idle project and the URL rots | Documented in the deploy note with the wake-up behaviour; the README links the Space, and a cold start is slow, not broken |
| Replay fixtures do not cover 100 invoices | §2 step 0 measures coverage first and reports the true N |
| A visitor uploads a real invoice with real PII to a public demo | The page says synthetic-only in Vietnamese; uploads land in an ephemeral container filesystem that dies with the Space; no upload is committed |
| The Space image ships real vendor fixtures | `.dockerignore` excludes `tests/fixtures/real_samples*`, and the note verifies the shipped image does not contain them |
| The history rewrite breaks clones or is done hastily | §4 stops at a plan and requires explicit go-ahead |

---

## 8. Open questions — recorded, not silently dropped

1. **Does Gate M8's "click the URL and it works" close inside this session?** Only after the user
   creates the Space and the Neon project. Until then the criterion reads **PENDING**, the same
   treatment M6-C's stopwatch and M7's Desktop recording received.
2. **Should the Space run the MCP server over HTTP so Claude Desktop can reach the live demo?**
   Deferred with M7's reason: no auth story.
3. **NĐ 70/2025/NĐ-CP** remains open from M1 and is still not blocking.
