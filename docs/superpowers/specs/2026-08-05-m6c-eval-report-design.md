# Spec — M6-C: the eval report (plan items 6.7, 6.8)

> **Date**: 2026-08-05 · **Milestone**: M6-C · **Status**: design approved, implementation depth.
>
> Reads with the M6 parent spec
> [`2026-08-02-m6-security-observability-eval-design.md`](2026-08-02-m6-security-observability-eval-design.md) §5,
> roadmap `docs/Plan_Project_F_2026-07-25.md` §3 (M6), and design doc
> `docs/project-f-system-design.md` §10 (eval), §10.4 (target metrics), §10.5 (honest reporting).
>
> Predecessors: **M6-A closed 2026-08-02** with its negative-control criterion reported **NOT met
> (22/24)**; **M6-B closed 2026-08-05** (`origin/main` `56716d8`, CI green). M6-C revisits neither
> number. M6-C is the last sub-milestone of M6.

---

## 0. Global constraints

From `CLAUDE.md` and roadmap §0. Not restated per section.

- **The LLM never toggles a flag and never posts a journal entry.** M6-C measures; it does not move
  the boundary. No agent response model gains a field.
- **Every LLM metric is reported beside a non-LLM baseline** where one exists (`keywords_vi`).
  Classification must beat it by ≥15 points or the node does not need an LLM.
- **Every non-obvious constant carries date + measurement + report file name.** M6-C pays the last
  outstanding violation (`NEAR_DUP_WINDOW_DAYS`).
- **Honest reporting is the deliverable, not a caveat on it.** Design §10.5: a reported 0.52 beats a
  claimed 0.98. Targets that are missed are stated as missed.
- **Authoring language English.** The `keywords_vi` baseline, agent prompts and report commentary
  the *system emits* stay Vietnamese.
- **A milestone is not closed until `make check` is green on the pushed commit and CI agrees.**

---

## 1. Why this exists

M6-C produces `docs/eval-report.md`, the artifact M8's README is required to trace every number back
to. Design §10.1's anti-circularity argument — generator and parser written independently, real
fixtures as arbiter, a non-LLM baseline beside every LLM metric, negative controls for injection —
only holds if the numbers can be **regenerated from a clean clone**. That is the property this
milestone has to establish, and the reason §2.1 below comes first.

### 1.1 The corpus on disk is not the corpus `make generate` produces — measured 2026-08-05

Found while scoping this milestone, by regenerating the corpus and diffing it against `corpus/`:

| | |
|---|---|
| Manifest (300 entries) | **identical** |
| XML files matching a fresh `--seed 42` generate | **0 of 285** |
| Manifest PDFs matching | **2 of 15** |
| Extra files in `corpus/` a fresh generate does not produce | 3 XML, 6 PDF |

Generation itself is **not** the problem: two fresh generations are byte-identical (0/285 XML and
0/18 PDF differ), so the generator is deterministic today. `corpus/` simply predates a change to it.

**The change was M6-A's git-history purge.** It ran `git filter-repo --replace-text` mapping the real
tax code `0100109106` → `REDACTED-MST` to remove PII, and that rewrite landed in the **working tree**,
not only in history. Three consequences, of which only the first is a defect:

1. **`scripts/generator/templates/hdon.xml.j2` now emits `<MSTTCGP>REDACTED-MST</MSTTCGP>`** — a
   redaction marker where a tax code belongs, in *synthetic* data that has nothing to redact. Every
   newly generated invoice carries it.
2. `tests/fixtures/real_samples/*.xml` also carry `REDACTED-MST`. **This one is correct** and is left
   alone: those files are deliberately anonymised and already use exactly this convention
   (`REDACTED_MA_CQT`, `REDACTED_MA_BI_MAT`, `REDACTED_FOR_FIXTURE`). Redacting a real solution
   provider's tax code out of a real invoice is the intended behaviour.
3. `docs/notes/2026-07-28-m2c-corpus-generator.md:51` now *describes* `REDACTED-MST` as the design
   ("Envelope `TTChung` header retains its own transmitter tax code (`REDACTED-MST`)"). History was
   rewritten such that a past document asserts something that was never true.

The purge was **right in intent** — `0100109106` is a real company's tax code and embedding it in
every generated invoice was the actual leak. It simply substituted a marker instead of a synthetic
value.

Nobody noticed for three days because `corpus/` predates the purge, so every test and every
measurement kept reading the pre-purge files.

**Blast radius, measured rather than assumed:**

| Surface | Effect of regenerating |
|---|---|
| classify / audit replay fixtures (899 files) | **Safe.** 60/60 prompts byte-identical, because `describe()` renders seller name, line descriptions and amounts — none of which the purge touched |
| vision replay fixtures | **Break.** PDF bytes change, so the rasterized-PNG digests in the cache key change |
| `buyer_tax_code` | `0100109106` → `0100000099` (checksum-valid, synthetic) |
| `MSTTCGP` | never parsed into `InvoiceData`; envelope-only, so no validator sees it |

### 1.2 Two debts M6-C is required to settle

- **The Auditor number came from a two-model mixture.** M4-C measured 12.5% dispute precision while
  Gemini rate-limited 177 times. Roadmap §1: *the number M6 publishes must come from one rater*.
  M6-B then measured, per-invoice, that even a replayed run is a mixture — Auditor **199/285 (69.8%)
  fallback** vs gemini 86/285 (30.2%); Classifier 258/285 primary vs 27/285 fallback.
- **`NEAR_DUP_WINDOW_DAYS = 7`** is commented "PROVISIONAL AND UNMEASURED" — the last live violation
  of the constants rule.

---

## 2. Design

### 2.1 Corpus integrity comes first

Nothing else in this milestone is worth measuring until the corpus is one a clean clone reproduces.

- Replace the template's `REDACTED-MST` with **`0199999991`** — checksum-valid under
  `domain.taxcode.is_valid_checksum`, obviously synthetic, and distinct from both the seller
  (`0004009229`) and buyer (`0100000099`) codes so the envelope `.//MST` descendant-search trap that
  `test_envelope_values_do_not_leak` relies on still bites.
- Regenerate `corpus/` and **remove the 9 stale extras** (3 XML, 6 PDF) that no longer correspond to
  any manifest entry.
- Correct `docs/notes/2026-07-28-m2c-corpus-generator.md:51`, with a note that the purge rewrote it.
- **Re-record only the vision fixtures.** The classify/audit fixtures are provably unaffected
  (§1.1), and re-recording 899 fixtures would spend free-tier quota to reproduce identical content.
- A test must pin corpus reproducibility: generate into a temp directory and assert the manifest and
  a sample of file digests match `corpus/`. Without it this recurs silently — which is exactly what
  happened.

### 2.2 Vision replay, re-keyed off the source document

The replay key hashes the **rasterized PNG** (`gateway._complete`, `image_digests`). pypdfium2
rendering is platform-dependent, so macOS/ARM recordings miss on CI — which is why the two vision
accuracy tests currently skip on Linux, and why CI has never verified a vision number.

**Decision: key on the source document's bytes, not the rendered image.** `gateway.generate` accepts
an optional `image_key` (a caller-supplied digest); `vision.extract` passes `sha256(pdf_bytes)`.
Rationale:

- Corpus PDFs are byte-identical across generations (measured §1.1), so the key travels.
- It is semantically the right key: the fixture records what a model said about **a document**, not
  about one machine's rendering of it.
- It removes rasterization from the cache contract entirely, so a pypdfium2 bump cannot invalidate
  every vision fixture.

**This must be verified, not assumed.** fpdf2 output being byte-identical across *platforms* is a
separate claim from being byte-identical across *runs on one machine*, and only the latter is
measured. A test pins the digest of one corpus PDF, so if Linux renders differently the build fails
loudly instead of silently missing fixtures.

### 2.3 Pinned-rater mode

An eval-only path that **disables the fallback chain** and throttles below free-tier RPM. A 429
becomes wait-and-retry on the *same* model rather than a silent demotion. Persistent failure records
**`unmeasured`** — the `INV-0029` precedent, where six Gemini 503s produced no extraction and were
reported unmeasured rather than failed.

Single rater is **verified, not asserted**: the per-invoice `answering_model` M6-B added must be a
single value per agent across the run. Publishing the primary
(`groq/llama-3.3-70b-versatile`), with M6-B's measured fallback shares reported beside it as a
deployment caveat — resolving parent spec §7.1.

**A retry that succeeds on the same pinned model is not a mixture.** M6-B measured that
`call_structured` retries on schema-validation failure, so attempts exceed invoices; the check is on
`answering_model`, not on attempt counts.

### 2.4 Eval evidence goes to a file

M6-B measured that `tests/conftest.py` runs a session-scoped `TRUNCATE app.invoices CASCADE`, and
`llm_calls.invoice_id` has an FK to `invoices` — so **every pytest session wipes `app.llm_calls`**.
The table cannot be the durable record of an eval run on a machine that also runs the suite.

**Decision: the run writes its own evidence artifact** (JSON) that the report is generated from, and
the DB stays as it is. The truncation is what keeps the suite idempotent; removing it to serve a
reporting need would trade a real property for a convenience. `scripts/eval_classification.py`
already prints its persisted id range — the artifact carries the same rows.

### 2.5 `NEAR_DUP_WINDOW_DAYS`, measured

Swept against the 9 `near_duplicate` corpus invoices, reporting precision/recall at each candidate
window. The comment is rewritten with date + measurement + report file. If the sweep shows the value
does not matter across a plausible range, **that is the finding** and it is recorded as such rather
than dressed up as a tuned optimum.

### 2.6 The report

`scripts/eval.py` → `make eval` → `docs/eval-report.md`, covering **every** row of design §10.4, each
beside its non-LLM baseline where one exists, and **stating plainly which targets are missed**. On
current evidence: the Auditor row misses ≥60% badly (12.5% at M4-C), and the security row is
22/24 rather than 100%. Both are published as misses.

Each row carries its **N** and how it was obtained (live / replay / unmeasured). A row with no
measurement says `unmeasured` — never a blank, never an inherited number presented as fresh.

### 2.7 The manual baseline (item 6.8) does not block M6-C

Design §10.4's time-savings number must be self-measured with a stopwatch, not quoted from a market
report. An agent cannot do it. `docs/notes/manual-baseline-protocol.md` specifies which ten invoices,
which fields to key, how to time, and how to extrapolate. The report ships the Business/STP row as
**PENDING — awaiting operator measurement**. No other row depends on it.

---

## 3. Scope — out

- **Improving any measured number.** M6-C reports; it does not tune. Prompt changes to lift the
  Auditor row would invalidate M6-A's security measurements taken against those exact prompts.
- **Re-recording classify/audit fixtures.** Provably unnecessary (§1.1).
- **Changing `tests/conftest.py`'s truncation** (§2.4).
- **Re-litigating M6-A's 22/24.** It is published as a miss.
- **`tests/fixtures/real_samples/*.xml`.** Their `REDACTED-MST` is correct anonymisation (§1.1).
- **NĐ 70/2025/NĐ-CP**, still open from M1, still not blocking.

---

## 4. Mutation verification

Each must turn a **named** test red; the note records measured output, not the word "RED".

| Mutation | Expected |
|---|---|
| Revert the template to `REDACTED-MST` | the corpus-reproducibility test |
| Key vision replay on rendered PNG bytes again | the cross-platform digest test |
| Let pinned-rater mode fall through to the fallback chain | the single-rater assertion |
| Report a missed target as met | the report-generation test that asserts targets are compared, not copied |

---

## 5. M6-C gate

| Criterion | Evidence required |
|---|---|
| Corpus reproducible from a clean clone | test regenerates and matches manifest + digests; mutation red |
| Vision fixtures travel to CI | vision accuracy test runs (not skips) on Linux CI |
| `make eval` produces every §10.4 row | generated `docs/eval-report.md`, each row with N and method |
| Classification beats `keywords_vi` by ≥15 | measured, single rater, model attributed |
| Single rater verified | one `answering_model` per agent across the run |
| Auditor row published however it scores | reported, with CI, even at 12.5% |
| `NEAR_DUP_WINDOW_DAYS` justified | comment carries date + measurement + report file |
| Business row | PENDING with protocol written — explicitly **not** a pass |
| `make check` green on the pushed commit, CI agreeing | CI run URL on the closing SHA |

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Free-tier quota exhausts mid-eval | `unmeasured` is a first-class outcome; a partial run reports its own N rather than silently mixing raters |
| Regenerating the corpus breaks more than measured | §1.1's blast radius was measured, not assumed; the classify-prompt equality check is re-run before regenerating |
| fpdf2 output differs on Linux, so the new vision key also fails to travel | A pinned-digest test fails the build loudly instead of silently missing fixtures (§2.2) |
| The report becomes a wall of numbers nobody reads | Every row carries N and method; §10.4's headline metric is the flag false-positive rate, and the report leads with it |
| Pinned-rater run takes hours on free tier | Throttle is configurable; a partial run is publishable with its N stated |

---

## 7. Open questions — recorded, not silently dropped

1. **Does the Auditor deserve to ship at all?** ADR 0003 already cites 12.5% dispute precision. If the
   single-rater re-measurement confirms it below the 50% net-harm line, M6-C reports it; whether to
   remove the node is M7/M8's decision, not this milestone's.
2. **Should the stale-corpus class of defect be prevented structurally** — e.g. a CI step that
   regenerates and diffs? §2.1 adds a test; whether CI should regenerate the whole corpus on every
   push is a runtime-cost question deferred to M8.
3. **NĐ 70/2025/NĐ-CP** remains open from M1 and is not M6-C's to close.
