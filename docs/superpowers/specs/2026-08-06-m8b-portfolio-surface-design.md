# Spec — M8-B: the portfolio surface, and a test for the gate's own sentence (items 8.2, 8.3, 8.4-script)

> **Date**: 2026-08-06 · **Milestone**: M8 · **Status**: design approved, implementation depth.
>
> Reads with roadmap `docs/Plan_Project_F_2026-07-25.md` §3 (M8, items 8.2–8.4) and design doc
> `docs/project-f-system-design.md` §14 (the six tests that are the portfolio).
>
> Runs after **M8-A** (`2026-08-06-m8a-reporting-agent-design.md`), because the README's eval table
> cannot be final until M8-A's shipping rule has produced a verdict.

---

## 0. Global constraints

From `CLAUDE.md` and roadmap §0. Not restated per section.

- **Authoring English.** The README is the one deliberate exception, ruled by the user 2026-08-06:
  English top-to-bottom with a **Vietnamese section** mirroring the safety property and the run
  instructions, because §0 names Vietnamese recruiters as an audience. `docs/demo-script.md` is
  Vietnamese in full — it is a script for spoken product narration.
- **Do not mass-translate.** ADRs 0001–0004 are Vietnamese. They are **extended**, not rewritten, so
  no translation diff appears anywhere (§3.2).
- **Report honestly (design §10.5).** The README publishes the misses beside the wins.
- **A milestone is not closed until `make check` is green on the pushed commit and CI agrees.**

---

## 1. Why this exists

Gate M8 is the project's Definition of Done and it is written entirely about this spec's output:

> *người lạ mở README hiểu dự án trong 5 phút; bấm URL dùng được ngay; **mọi con số trong README
> trace được về `docs/eval-report.md` trong repo***

The README on disk today still says **"Trạng thái: M0 (nền móng). Chưa có pipeline chạy được."** —
eight milestones stale. It is the single most-read file in the repository and the only one a
recruiter is guaranteed to open.

---

## 2. The traceability criterion becomes a test

The third clause of the gate is a mechanically checkable property, and this repo's own history —
three milestones closed on red commits, four tests that guarded nothing — is the argument for
checking it mechanically. **`tests/test_readme_numbers_trace.py`**:

1. Extract every numeric token from `README.md`.
2. Normalize: strip `%`, `,`/`.` thousand separators, trailing zeros; compare as `Decimal`.
3. A number **passes** if it appears in `docs/eval-report.md` under the same normalization, allowing
   the ×100 / ÷100 pair so `95.4%` matches the report's `0.954` — the two files legitimately render
   the same measurement in different units, and the test must not force the README into fractions a
   human would not write.
4. Otherwise it must appear in an **explicit allowlist inside the test file**, each entry carrying a
   one-line reason: `"3.13": "Python version, pyproject requires-python"`, `"7860": "HF Space port"`,
   `"834": "CI test count, run 31061588419"`.

The allowlist is the design, not a loophole. Numbers that legitimately do not come from the eval
report — versions, ports, counts — become a short reviewed list instead of an unenforced convention,
the same move M7 made with its one-entry write-tool allowlist. A number that is neither traceable nor
listed fails the build.

**Mutations** (each must turn this named test red):

| Mutation | Why it must fail |
|---|---|
| Change `95.4` → `96.4` in the README | the classic portfolio inflation, caught by digit |
| Add an untraceable claim (`"precision 0.71"`) | no report row, no allowlist entry |
| Delete a row from `docs/eval-report.md` | the README's number loses its source |
| Normalize-and-accept everything (gut the check) | the guard must not certify by construction |

---

## 3. Deliverables

### 3.1 `README.md` — rewritten, English, ~120 lines

Section order chosen for the five-minute reader, who does not scroll:

1. **What it is** — one paragraph, one sentence of it Vietnamese-domain-accurate (QĐ-1450 XML →
   TT133 accounts → budget → human approval → journal entry).
2. **Live demo** — the URL from M8-C, plus what to click. Written as PENDING until the URL exists;
   never as a link that 404s.
3. **The safety property, three layers** — the sentence CLAUDE.md protects, with the three enforcing
   artefacts named (structured output, DB constraints + `REVOKE`, `test_never_post_without_approval`).
4. **Architecture** — the three-graph table plus `docs/diagrams/overview_architecture.png`, which
   already exists and is generated.
5. **Results** — the eval table, inside `<!-- eval-numbers:start -->` markers, **wins and misses
   together**: classification 95.4% vs the 57.9% `keywords_vi` baseline **and** Auditor override
   precision 0.000 (CI 0.000–0.243, n=12, underpowered) **and** negative control 22/24. A README that
   publishes only the wins contradicts §10.5 and is the easiest thing for an interviewer to catch.
6. **The 7 GĐ5 skills map** — table, one row per skill, each pointing at the file that implements it
   (source: `docs/project-f-phase5-mapping.md`).
7. **Run it** — `make install` / `make dev` / `make demo`, Python 3.13, Docker, uv.
8. **Deliberately not built** — supervisor, Redis, Qdrant, custom OCR, auth, real ERP integration,
   each linking its ADR or roadmap §4 line.
9. **Tiếng Việt** — the Vietnamese section: what it is, the safety property, how to run.

The eval table's numbers are **copied from `docs/eval-report.md`**, which `make eval` generates from
`docs/notes/2026-08-05-m6c-eval-raw.json`. §2's test is what keeps the copy honest.

### 3.2 ADR audit — six exist, three are thin

Gate item 8.3 asks for "5 complete ADRs — this is the interview script, not paperwork". Measured
2026-08-06: `0001` 24 lines, `0003` 24, `0004` 28 (thin); `0002` 90, `0005` 80, `0006` 70
(substantial).

The three thin ones predate the measurements that now exist. Each gets an **appended English section
titled "Evidence, measured"** — appended rather than rewritten, so no Vietnamese passage is
translated and no diff mixes languages:

| ADR | Evidence it can now cite |
|---|---|
| 0001 three graphs not one | `test_batch_not_blocked_by_pending` green at 15 posted / 12 parked (M5-B); HITL 10/10 across a process boundary |
| 0003 no supervisor | Auditor override precision **0.000 (CI 0.000–0.243, n=12)** — the project's own multi-agent experiment argues against adding a *second* LLM coordination layer, and that is a stronger ADR than the original prediction |
| 0004 serial budget reconciliation | `test_budget_serialization` — shuffled arrival order, same invoice breaches; the MKT chain `35M → 15M → −5M` |

**ADR 0007** is written by M8-A's verdict (LLM commentary or template). No ADR is invented to reach a
count of five: six exist, seven after M8-A.

### 3.3 `docs/demo-script.md` — Vietnamese, 3 minutes

Referenced by roadmap item 8.4 and **absent from disk**. A shot list with timecodes, spoken lines,
and the exact commands to type:

| Time | Shot |
|---|---|
| 0:00–0:25 | The problem, one real invoice on screen |
| 0:25–1:10 | Upload → enrichment graph → flags appear, no LLM in the flag path |
| 1:10–2:00 | Budget breach → approval queue → the human decides → journal entry posted |
| 2:00–2:35 | **`kill -9` during the interrupt, restart, resume completes** — the money shot |
| 2:35–3:00 | The eval table, including the two misses |

**You record it.** The script, the seeded data and the commands are delivered here; the recording
needs your machine and your voice — the same division of labour M6-C used for the stopwatch baseline
and M7 for the Claude Desktop session.

### 3.4 Debt paid: `scripts/` enters mypy

Carried since M6, and M7 **widened** it by adding two more untyped scripts. `pyproject.toml`'s
`packages = ["invoice_agent"]` means `make check`'s typecheck never looks at `scripts/` — which is
how a type error in M6-C's pinned-rater monkeypatch stayed invisible.

Extend the mypy target to `scripts/`, fix what it finds, keep CI green. If a script needs a
`type: ignore`, it carries a reason on the same line. The count of errors found is reported in the
note — a debt paid silently teaches nothing.

---

## 4. Scope — out

- The deploy, the live URL and the load test — M8-C.
- The blog post — M8-C.
- Recording the video — the user's, per §3.3.
- Translating any existing Vietnamese prose (§0).
- Re-measuring anything M6, M7 or M8-A published. The README **copies**; it does not re-derive.
- The PII git-history purge — planned in M8-C, executed only on explicit go-ahead.

---

## 5. M8-B gate

| Criterion | Evidence required |
|---|---|
| README explains the project in 5 minutes | §3.1 structure delivered; a reader unfamiliar with the repo can name the safety property and the three graphs |
| Every README number traces to `docs/eval-report.md` or a reasoned allowlist entry | `test_readme_numbers_trace.py` green + all four §2 mutations red with measured output |
| README publishes the misses | Auditor 0.000, negative control 22/24, Business/STP pending — visible in the table, not a footnote |
| 5+ complete ADRs | 0001/0003/0004 extended with measured evidence; 0007 written by M8-A |
| `docs/demo-script.md` exists and is shootable | timecoded shot list with commands |
| `scripts/` type-checked in CI | mypy target extended; error count reported; CI green |
| `make check` green on the pushed commit, CI agreeing | CI run URL on the closing SHA |

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| The number-trace test is so loose it certifies anything | Mutation 4 exists precisely to prove it can fail; the ×100 rule is the only tolerance and it is stated |
| The allowlist becomes a dumping ground | Each entry carries a reason on its own line; a review reads the list, and the list is short by construction |
| The README's numbers drift when `make eval` regenerates | The test fails on the next `make check`, which is the point |
| Extending ADRs turns into rewriting them | Append-only, English section, existing Vietnamese untouched (§3.2) |
| mypy over `scripts/` uncovers a large mess and swallows the milestone | Time-boxed: if the error count is large, fix the type-unsafe ones and record the rest with `type: ignore` + reason rather than leaving CI red |

---

## 7. Open questions — recorded, not silently dropped

1. **Does the README claim the live URL before M8-C deploys?** No — it reads PENDING until the Space
   answers. A dead link on the front page is worse than an honest gap.
2. **NĐ 70/2025/NĐ-CP** remains open from M1 and is still not blocking.
