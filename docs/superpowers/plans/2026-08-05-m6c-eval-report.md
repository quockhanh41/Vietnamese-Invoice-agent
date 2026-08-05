# M6-C — The Eval Report: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce `docs/eval-report.md` covering every row of design §10.4 — each with its N, its method, and its non-LLM baseline — from a corpus a clean clone can reproduce, with the classification number attributable to a single verified rater.

**Architecture:** Corpus integrity is repaired first, because nothing measured against an unreproducible corpus is worth publishing. The vision replay key moves off a platform-dependent rasterized PNG onto the source document, so CI can verify a vision number for the first time. A pinned-rater eval path disables the fallback chain and records `unmeasured` rather than demoting. The report is generated from a JSON evidence artifact, not from the database, because every pytest session truncates `app.llm_calls`.

**Tech Stack:** Python 3.13, LiteLLM, LangGraph 1.2.9 (pinned), Postgres 17, pytest, ruff, mypy, fpdf2, pypdfium2.

**Spec:** `docs/superpowers/specs/2026-08-05-m6c-eval-report-design.md`

## Global Constraints

- **Authoring language English** — docs, comments, docstrings, commit messages. Text the system *emits or ingests* stays Vietnamese: agent prompts, `keywords_vi`, report commentary, UI copy. Do not translate existing Vietnamese prose.
- **The LLM never toggles a flag and never posts a journal entry.** No agent response model gains a field. Do not touch `ClassificationResult`, `AuditVerdict`, agent prompts, or `sanitize.wrap()` — M6-A measured its security numbers against those exact surfaces.
- **M6-C measures; it does not tune.** Improving a number by changing a prompt or a threshold is out of scope and invalidates prior measurements.
- **Every non-obvious constant carries date + measurement + report file name.**
- **Every LLM metric is reported beside its non-LLM baseline** (`keywords_vi`) where one exists.
- **Missed targets are published as missed.** Design §10.5: a reported 0.52 beats a claimed 0.98.
- Pinned: `langgraph==1.2.9`, `langgraph-checkpoint-postgres==3.1.0`, `langfuse>=3.15,<4.0.0`. Do not bump.
- **Postgres must be running.** If tests mass-SKIP with "Cần Postgres đang chạy", run `docker compose up -d postgres`, wait for healthy, retry. **Never read SKIPs as passes** — a session-scoped autouse fixture skips the whole suite when Postgres is down.
- **`app.llm_calls` is append-only for the runtime role** (no DELETE grant, migration `b25d8a939562`). Test rows need `get_owner_engine()` cleanup scoped to a self-minted identifier — never a broad predicate.
- Run `make check` after every task. Baseline entering M6-C: **761 passed, 2 xfailed**.
- **A milestone is not closed until `make check` is green on the pushed commit and CI agrees.**

---

### Task 1: Repair corpus integrity

Spec §1.1, §2.1. `make generate` currently emits `<MSTTCGP>REDACTED-MST</MSTTCGP>` — a redaction marker in synthetic data — and `corpus/` on disk predates the change, which is why nothing broke.

**Files:**
- Modify: `scripts/generator/templates/hdon.xml.j2`, and the `tdiep` template if it carries the same literal
- Modify: `docs/notes/2026-07-28-m2c-corpus-generator.md:51`
- Test: `tests/test_corpus_reproducible.py` (create)

**Interfaces:**
- Produces: a corpus that a fresh `--seed 42` generate reproduces byte-for-byte

- [ ] **Step 1: Confirm the blast radius before changing anything**

```bash
SP=$(mktemp -d)
.venv/bin/python -m scripts.generate_invoices --count 300 --seed 42 --out "$SP/before" >/dev/null
.venv/bin/python - "$SP/before" <<'PY'
import pathlib, sys
from invoice_agent.extraction.xml_parser import parse_invoice_xml
from invoice_agent.llm.budget import classify_messages
gen = pathlib.Path(sys.argv[1]); cur = pathlib.Path("corpus")
same = diff = 0
for n in sorted(p.name for p in cur.glob("INV-*.xml")):
    if not (gen/n).exists(): continue
    a = classify_messages(parse_invoice_xml((cur/n).read_bytes()))
    b = classify_messages(parse_invoice_xml((gen/n).read_bytes()))
    same += (a == b); diff += (a != b)
print(f"classify prompts identical: {same}  differing: {diff}")
PY
```
Expected: `differing: 0`. **If any differ, STOP** — the classify/audit replay fixtures would be invalidated by regeneration and the plan's re-record scope is wrong. Report it rather than proceeding.

- [ ] **Step 2: Write the failing test**

Create `tests/test_corpus_reproducible.py`:

```python
"""The corpus must be what `make generate` produces — not what it produced once.

Found 2026-08-05 (spec §1.1): `corpus/` had drifted from the generator and
nobody noticed for three days, because the stale files kept every test green.
M6-A's history purge rewrote the generator template in the WORKING TREE, so a
fresh generate emitted `REDACTED-MST` where a tax code belongs while the
on-disk corpus still carried the pre-purge value.

Design §10.1's anti-circularity argument requires the published numbers to be
regenerable from a clean clone. This test is that requirement, executable.
"""

import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

CORPUS = pathlib.Path("corpus")


def _digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory) -> pathlib.Path:
    if not (CORPUS / "manifest.jsonl").exists():
        pytest.skip("corpus not generated — run `make generate`")
    out = tmp_path_factory.mktemp("corpus-repro")
    subprocess.run(
        [sys.executable, "-m", "scripts.generate_invoices",
         "--count", "300", "--seed", "42", "--out", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_the_manifest_regenerates_identically(regenerated):
    a = sorted((CORPUS / "manifest.jsonl").read_text(encoding="utf-8").splitlines())
    b = sorted((regenerated / "manifest.jsonl").read_text(encoding="utf-8").splitlines())
    assert a == b


def test_every_manifest_file_regenerates_byte_identically(regenerated):
    """The check that would have caught the drift. Compares CONTENT, not just
    the manifest — the manifest matched throughout while all 285 XMLs differed."""
    manifest = [
        json.loads(line)
        for line in (CORPUS / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    stale = []
    for record in manifest:
        name = pathlib.Path(record["path"]).name
        current, fresh = CORPUS / name, regenerated / name
        if not current.exists() or not fresh.exists():
            stale.append(f"{name} (missing on one side)")
        elif _digest(current) != _digest(fresh):
            stale.append(name)
    assert stale == [], f"{len(stale)} corpus files differ from a fresh generate: {stale[:10]}"


def test_the_generator_emits_no_redaction_marker():
    """`REDACTED-MST` is correct in tests/fixtures/real_samples/ — those are
    deliberately anonymised real invoices. It is wrong in SYNTHETIC data, which
    has nothing to redact, and it is not a tax code."""
    templates = pathlib.Path("scripts/generator/templates")
    offenders = [
        p.name for p in templates.glob("*.j2") if "REDACTED" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"generator templates carry a redaction marker: {offenders}"
```

- [ ] **Step 3: Run and verify it fails**

Run: `.venv/bin/python -m pytest tests/test_corpus_reproducible.py -v`
Expected: `test_the_generator_emits_no_redaction_marker` FAILS naming `hdon.xml.j2`; `test_every_manifest_file_regenerates_byte_identically` FAILS listing ~285 files.

- [ ] **Step 4: Fix the template**

In every `scripts/generator/templates/*.j2` carrying it, replace `<MSTTCGP>REDACTED-MST</MSTTCGP>` with:

```
<MSTTCGP>0199999991</MSTTCGP>
```

Verify the choice before committing to it:

```bash
.venv/bin/python -c "
from invoice_agent.domain import taxcode
print('checksum valid:', taxcode.is_valid_checksum('0199999991'))
print('distinct from seller 0004009229 and buyer 0100000099:', '0199999991' not in ('0004009229','0100000099'))
"
```
Expected: `True` / `True`. It must be checksum-valid (it sits in a field a reader would treat as a tax code), obviously synthetic, and **distinct from the seller and buyer codes** so the envelope `.//MST` descendant-search trap that `test_envelope_values_do_not_leak` relies on still bites.

- [ ] **Step 5: Regenerate the corpus and remove the stale extras**

```bash
make generate
.venv/bin/python - <<'PY'
import json, pathlib
corpus = pathlib.Path("corpus")
declared = {pathlib.Path(json.loads(l)["path"]).name
            for l in (corpus/"manifest.jsonl").read_text(encoding="utf-8").splitlines()}
extras = [p for p in corpus.iterdir()
          if p.name not in declared and p.name not in {"manifest.jsonl", "gate_batch.json"}]
for p in extras:
    print("removing stale:", p.name); p.unlink()
print("removed", len(extras))
PY
```
Expected: removes the files no manifest entry references. Record the list — it goes in the note.

- [ ] **Step 6: Run and verify the tests pass**

Run: `.venv/bin/python -m pytest tests/test_corpus_reproducible.py -v`
Expected: 3 passed.

- [ ] **Step 7: Correct the note the purge rewrote**

`docs/notes/2026-07-28-m2c-corpus-generator.md:51` currently states the envelope header "retains its own transmitter tax code (`REDACTED-MST`)". That sentence originally named a real tax code; the purge rewrote it so a past document now describes a redaction marker as the design. Correct it to name `0199999991`, and add a bracketed note that the value was changed by M6-A's history purge on 2026-08-02 and repaired by M6-C on 2026-08-05.

- [ ] **Step 8: Mutation — revert the template**

Put `REDACTED-MST` back.
Run: `.venv/bin/python -m pytest tests/test_corpus_reproducible.py -v`
Expected: FAIL on both the marker test and the byte-identity test. Record output, revert.

- [ ] **Step 9: `make check` and commit**

The regenerated corpus is gitignored (`/corpus/`), so only source and docs are committed.

```bash
make check
git add scripts/generator/templates/ tests/test_corpus_reproducible.py docs/notes/2026-07-28-m2c-corpus-generator.md
git commit -m "fix(m6c): the history purge left a redaction marker in the corpus generator"
```

---

### Task 2: Re-key vision replay off the source document

Spec §2.2. The cache key hashes the rasterized PNG, and pypdfium2 rendering is platform-dependent, so macOS recordings miss on CI — which is why the vision accuracy tests skip on Linux and CI has never verified a vision number.

**Files:**
- Modify: `src/invoice_agent/llm/gateway.py` (`_complete`, `generate`, `generate_with_model`, `_generate_impl`)
- Modify: `src/invoice_agent/llm/structured.py` — **signature only**, threading `image_key`; no logic change
- Modify: `src/invoice_agent/extraction/vision.py` (`extract`)
- Test: `tests/test_vision_replay_key.py` (create)

**Interfaces:**
- Produces: `gateway.generate(..., image_key: str | None = None)`; when supplied it replaces the per-image digests in the cache key

- [ ] **Step 1: Write the failing test**

Create `tests/test_vision_replay_key.py`:

```python
"""The vision replay key must not depend on how a PDF was rendered.

Measured before M6-C: the key hashed the rasterized PNG, and pypdfium2 renders
differently across platforms, so macOS/ARM recordings missed on Linux CI. The
consequence was not a loud failure — it was two vision accuracy tests SKIPPING
on CI, so the vision number in the M4 gate table was never verified by CI at
all.

Keying on the source document is also the semantically correct choice: a
fixture records what a model said about a DOCUMENT, not about one machine's
rendering of it.
"""

import hashlib
import pathlib

import pytest

from invoice_agent.llm import cache

CORPUS = pathlib.Path("corpus")


def _a_corpus_pdf() -> bytes:
    pdfs = sorted(CORPUS.glob("*.pdf"))
    if not pdfs:
        pytest.skip("corpus not generated — run `make generate`")
    return pdfs[0].read_bytes()


def test_the_key_is_stable_when_the_rendering_changes():
    """Two different renderings of one document must produce ONE key."""
    base = dict(
        model="gemini/gemini-3.5-flash", messages=[{"role": "user", "content": "x"}],
        temperature=0.1, max_tokens=2048, json_mode=True, reasoning_effort=None,
    )
    doc_digest = hashlib.sha256(b"the-pdf-bytes").hexdigest()
    assert cache.make_key(**base, image_digests=[doc_digest]) == cache.make_key(
        **base, image_digests=[doc_digest]
    )
    # ...and differs from a key built from rendered-page digests
    assert cache.make_key(**base, image_digests=[doc_digest]) != cache.make_key(
        **base, image_digests=[hashlib.sha256(b"page-1-png").hexdigest()]
    )


def test_vision_extract_keys_on_the_pdf_not_the_png(monkeypatch):
    """Captures the digest list the gateway actually used."""
    from invoice_agent.config import Settings
    from invoice_agent.extraction import vision
    from invoice_agent.llm.gateway import LLMGateway

    pdf = _a_corpus_pdf()
    captured: dict = {}
    real_make_key = cache.make_key

    def _spy(**kw):
        captured.update(kw)
        return real_make_key(**kw)

    monkeypatch.setattr(cache, "make_key", _spy)
    monkeypatch.setattr("invoice_agent.llm.gateway.cache.make_key", _spy)

    gw = LLMGateway(Settings(_env_file=None, gemini_api_keys=[], llm_mode="replay"))
    with pytest.raises(Exception):
        vision.extract(pdf, gateway=gw)

    assert captured.get("image_digests") == [hashlib.sha256(pdf).hexdigest()], (
        "vision.extract must key on the source PDF, not the rasterized pages"
    )


def test_the_corpus_pdf_digest_is_pinned():
    """fpdf2 output is byte-identical across RUNS on one machine (measured
    2026-08-05). Byte-identity across PLATFORMS is a separate claim and is NOT
    measured. This test pins it, so if Linux renders differently the build fails
    loudly instead of silently missing every vision fixture again.

    Regenerate the expected digest with:
        make generate && sha256sum corpus/<name>.pdf
    """
    pdfs = sorted(CORPUS.glob("*.pdf"))
    if not pdfs:
        pytest.skip("corpus not generated — run `make generate`")
    name = pdfs[0].name
    actual = hashlib.sha256(pdfs[0].read_bytes()).hexdigest()
    assert actual == PINNED_PDF_DIGESTS[name], (
        f"{name} digest changed: fpdf2 output is not reproducible here. "
        "If this fires on CI only, PDF generation is platform-dependent and the "
        "vision replay key cannot travel — report it, do not update the constant."
    )
```

Add at module level, filled in at Step 5:

```python
# Measured 2026-08-05 on macOS/ARM after Task 1's regeneration. See
# docs/notes/2026-08-05-m6c-eval-report.md.
PINNED_PDF_DIGESTS: dict[str, str] = {}
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_vision_replay_key.py -v`
Expected: `test_vision_extract_keys_on_the_pdf_not_the_png` FAILS — the digests are per-page PNGs.

- [ ] **Step 3: Thread `image_key` through the gateway**

In `src/invoice_agent/llm/gateway.py`, add `image_key: str | None = None` as a keyword parameter to `generate`, `generate_with_model`, `_generate_impl` and `_complete`, passing it down unchanged. In `_complete`, replace the digest computation:

```python
        # `image_key` lets the CALLER name what the images depict, instead of
        # hashing the bytes it happened to render. Vision passes the source
        # PDF's digest: pypdfium2 renders differently across platforms, so a
        # key built from PNG bytes made every macOS recording miss on Linux CI
        # — silently, as a SKIP rather than a failure. Measured 2026-08-05,
        # docs/notes/2026-08-05-m6c-eval-report.md.
        if image_key is not None:
            digests = [image_key]
        elif images:
            digests = [hashlib.sha256(blob).hexdigest() for blob in images]
        else:
            digests = []
```

- [ ] **Step 4: Thread it through `call_structured` and `vision.extract`**

In `src/invoice_agent/llm/structured.py`, add `image_key: str | None = None` to `call_structured`'s signature and forward it to `gateway.generate(...)`. **Change nothing else in that file** — it is on the security path M6-A measured.

In `src/invoice_agent/extraction/vision.py`, inside `extract`:

```python
    pages = rasterize(pdf_bytes)
    # Key the replay fixture on the DOCUMENT, not on this machine's rendering
    # of it (spec §2.2).
    image_key = hashlib.sha256(pdf_bytes).hexdigest()
```
and pass `image_key=image_key` to `call_structured`. Add `import hashlib`.

- [ ] **Step 5: Fill in the pinned digest and run**

```bash
.venv/bin/python -c "
import hashlib, pathlib
p = sorted(pathlib.Path('corpus').glob('*.pdf'))[0]
print(p.name, hashlib.sha256(p.read_bytes()).hexdigest())
"
```
Put the result in `PINNED_PDF_DIGESTS`. Run: `.venv/bin/python -m pytest tests/test_vision_replay_key.py -v` → 3 passed.

- [ ] **Step 6: Re-record the vision fixtures**

The old fixtures are keyed on PNG digests and are now unreachable. Re-record with a Gemini key present:

```bash
LLM_MODE=record .venv/bin/python -m pytest tests/test_vision_accuracy.py -v 2>&1 | tail -20
```
Record how many recorded and how many failed. **A provider 503 is `unmeasured`, not a vision failure** — that is the `INV-0029` precedent. Then delete fixture files no longer reachable by any key, listing what you removed.

- [ ] **Step 7: Verify replay works with no keys**

```bash
env -u GEMINI_API_KEY -u GEMINI_API_KEYS LLM_MODE=replay .venv/bin/python -m pytest tests/test_vision_accuracy.py -v 2>&1 | tail -10
```
Expected: passes (not skips) with every key unset.

- [ ] **Step 8: Mutation — key on PNG bytes again**

Make `_complete` ignore `image_key`.
Run: `.venv/bin/python -m pytest tests/test_vision_replay_key.py::test_vision_extract_keys_on_the_pdf_not_the_png -v`
Expected: FAIL. Record, revert.

- [ ] **Step 9: `make check` and commit**

```bash
make check
git add src/invoice_agent/llm/gateway.py src/invoice_agent/llm/structured.py src/invoice_agent/extraction/vision.py tests/test_vision_replay_key.py tests/fixtures/llm_cache/
git commit -m "fix(m6c): key vision replay on the document, not on one machine's rendering"
```

---

### Task 3: Pinned-rater mode

Spec §2.3. The published classification number must come from one rater, verified rather than asserted.

**Files:**
- Modify: `scripts/eval_classification.py`
- Test: `tests/test_eval_pinned_rater.py` (create)

**Interfaces:**
- Produces: `pinned_gateways(settings, classifier_model, auditor_model) -> tuple[LLMGateway, LLMGateway]`; `call_with_retry(fn, *, attempts, backoff_seconds) -> tuple[Any, str | None]` returning `(result, None)` or `(None, "unmeasured: <reason>")`
- CLI: `--classifier-model`, `--auditor-model`, `--rpm`, `--retry-attempts`

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_pinned_rater.py`:

```python
"""Pinning a rater means the fallback chain is GONE, not merely deprioritised.

Roadmap §1: "the number M6 publishes must come from one rater." M4-C's Auditor
number came from a two-model mixture because Gemini rate-limited 177 times and
the chain silently demoted. M6-B then measured that even a replayed run is a
mixture: Auditor 199/285 fallback vs 86/285 primary.

A silent demotion is the failure mode this exists to prevent, so the test
asserts the chain has exactly ONE entry — not that the primary is first.
"""

import pytest

from invoice_agent.config import Settings


def test_a_pinned_gateway_has_no_fallback_at_all():
    from scripts.eval_classification import pinned_gateways

    base = Settings(_env_file=None, groq_api_keys=["k"], gemini_api_keys=["k"])
    classifier, auditor = pinned_gateways(
        base,
        classifier_model="groq/llama-3.3-70b-versatile",
        auditor_model="gemini/gemini-3.5-flash",
    )
    assert classifier.model_chain == ["groq/llama-3.3-70b-versatile"]
    assert auditor.model_chain == ["gemini/gemini-3.5-flash"]


def test_the_two_pinned_raters_must_not_be_the_same_model():
    """Design §6.3: the Auditor exists to have a DIFFERENT failure mode. A
    self-graded run would report agreement as if it were corroboration."""
    from scripts.eval_classification import pinned_gateways

    base = Settings(_env_file=None, groq_api_keys=["k"])
    with pytest.raises(ValueError, match="same model"):
        pinned_gateways(
            base,
            classifier_model="groq/llama-3.3-70b-versatile",
            auditor_model="groq/llama-3.3-70b-versatile",
        )


def test_persistent_failure_is_recorded_unmeasured_not_dropped():
    """The INV-0029 precedent: six Gemini 503s produced no extraction, and it
    was reported unmeasured rather than scored as a failure. Dropping it would
    silently shrink N; scoring it as wrong would blame the model for the
    provider."""
    from scripts.eval_classification import call_with_retry

    calls = {"n": 0}

    def always_429():
        calls["n"] += 1
        raise RuntimeError("RateLimitError: 429 Too Many Requests")

    result, unmeasured = call_with_retry(always_429, attempts=3, backoff_seconds=0)
    assert result is None
    assert unmeasured is not None and "429" in unmeasured
    assert calls["n"] == 3, "must retry the SAME model, not fall through"


def test_a_transient_failure_still_returns_a_result():
    from scripts.eval_classification import call_with_retry

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("RateLimitError: 429")
        return "ok"

    result, unmeasured = call_with_retry(flaky, attempts=3, backoff_seconds=0)
    assert (result, unmeasured) == ("ok", None)
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_eval_pinned_rater.py -v`
Expected: FAIL — `cannot import name 'pinned_gateways'`.

- [ ] **Step 3: Implement**

In `scripts/eval_classification.py`:

```python
def pinned_gateways(settings, *, classifier_model: str, auditor_model: str):
    """Two gateways with single-entry chains — no fallback to demote to.

    Pinning by rebuilding Settings rather than by adding a `model=` parameter to
    `classify()`: the agents' signatures are part of the surface M6-A measured
    its security numbers against, and this needs no change to them.
    """
    if classifier_model == auditor_model:
        raise ValueError(
            f"Classifier and Auditor pinned to the same model ({classifier_model}). "
            "Design §6.3 wants a different failure mode; a self-graded run reports "
            "agreement as corroboration."
        )
    classifier = LLMGateway(settings.model_copy(update={"llm_model_chain": [classifier_model]}))
    auditor = LLMGateway(settings.model_copy(update={"auditor_model_chain": [auditor_model]}))
    return classifier, auditor


def call_with_retry(fn, *, attempts: int, backoff_seconds: float):
    """Retry the SAME model on a rate limit. Returns (result, None) or
    (None, reason).

    A pinned chain has nothing to fall through to, so a 429 raises. Waiting and
    retrying is what turns a quota bump into a delay instead of a silent
    demotion — and when it still fails, `unmeasured` keeps the run honest about
    its own N.
    """
    last = ""
    for attempt in range(attempts):
        try:
            return fn(), None
        except Exception as exc:  # noqa: BLE001 - reason is recorded, not swallowed
            last = f"{type(exc).__name__}: {exc}"[:200]
            if attempt + 1 < attempts and backoff_seconds:
                time.sleep(backoff_seconds * (2**attempt))
    return None, f"unmeasured: {last}"
```

Wire `evaluate()` to accept the two gateways and a `rpm` throttle, use `call_with_retry` around each agent call, and record `"unmeasured": reason` on the row when it fires. Add the four CLI flags with help text.

- [ ] **Step 4: Run and verify pass**

Run: `.venv/bin/python -m pytest tests/test_eval_pinned_rater.py -v`
Expected: 4 passed.

- [ ] **Step 5: Mutation — let the pin fall through**

Make `pinned_gateways` return gateways built on the full default chains.
Run: `.venv/bin/python -m pytest tests/test_eval_pinned_rater.py::test_a_pinned_gateway_has_no_fallback_at_all -v`
Expected: FAIL. Record, revert.

- [ ] **Step 6: `make check` and commit**

```bash
make check
git add scripts/eval_classification.py tests/test_eval_pinned_rater.py
git commit -m "feat(m6c): pin one rater per agent, and record unmeasured instead of demoting"
```

---

### Task 4: Start the live pinned-rater run

This is the milestone's long pole — 285 classify + 285 audit, throttled, with the Auditor on a family-disjoint model. Start it before the remaining desk work so it runs in parallel.

**Files:**
- Create: `docs/notes/2026-08-05-m6c-eval-raw.json` — **committed**, and read by Task 6.

> **Why this artifact is committed when M4-C's was not.** M8's gate requires *every number in the
> README to trace back to `docs/eval-report.md` in the repo*, and a report generated from a file in
> `/tmp` cannot be regenerated by anyone — including the author, next week. The run costs real
> free-tier quota and cannot be casually repeated, which makes its output evidence rather than
> scratch. It is synthetic-corpus output with no PII. Note it also holds `classifier_reason` free
> text produced by a model reading invoices that contain the injection payloads: skim it before
> committing, and confirm `gitleaks` passes (it runs in CI on every push).

- [ ] **Step 1: Precondition check — record it either way**

```bash
.venv/bin/python -c "
from invoice_agent.config import get_settings
s = get_settings()
print('groq keys   :', len(s.groq_api_keys))
print('gemini keys :', len(s.gemini_api_keys))
print('llm_mode    :', s.llm_mode)
"
```
If zero keys for either provider, **stop and report the precondition block** exactly as M4-C did — do not stub, do not fabricate. The report ships those rows `unmeasured`.

- [ ] **Step 2: Launch the run in the background**

```bash
LLM_MODE=live .venv/bin/python -m scripts.eval_classification \
  --classifier-model groq/llama-3.3-70b-versatile \
  --auditor-model gemini/gemini-3.5-flash \
  --rpm 20 --retry-attempts 5 \
  --out docs/notes/2026-08-05-m6c-eval-raw.json 2>&1 | tee /tmp/m6c-eval.log
```

Expect 30-60+ minutes. **Do not block on it.** Proceed to Tasks 5 and 7 while it runs, then collect in Task 6.

- [ ] **Step 3: When it finishes, verify single rater from the artifact**

```bash
.venv/bin/python -c "
import json; d=json.load(open('docs/notes/2026-08-05-m6c-eval-raw.json'))
print('n rows      :', len(d['rows']))
print('classifier  :', d['models']['classifier'])
print('auditor     :', d['models']['auditor'])
print('mixture cls :', d.get('classifier_mixture'))
print('mixture aud :', d.get('auditor_mixture'))
print('unmeasured  :', sum(1 for r in d['rows'] if r.get('unmeasured')))
"
```
Expected: exactly ONE answering model per agent. **If two appear, the pin leaked** — that is a finding, and the report must not claim a single rater. Investigate before publishing.

---

### Task 5: Measure `NEAR_DUP_WINDOW_DAYS`

Spec §2.5. Its comment says "PROVISIONAL AND UNMEASURED" — the last live violation of the constants rule.

**Files:**
- Modify: `src/invoice_agent/domain/anomaly.py:19-21`
- Test: `tests/test_near_dup_window.py` (create)

- [ ] **Step 1: Sweep the window against the corpus**

```bash
.venv/bin/python - <<'PY'
import json, pathlib, itertools
from invoice_agent.domain import anomaly
from invoice_agent.extraction.xml_parser import parse_invoice_xml

corpus = pathlib.Path("corpus")
rows = [json.loads(l) for l in (corpus/"manifest.jsonl").read_text(encoding="utf-8").splitlines()]
xml = [r for r in rows if r["source_type"] == "xml"]
truth = {r["invoice_id"] for r in xml if "near_duplicate" in (r.get("defects") or [])}
print("seeded near_duplicate invoices:", len(truth))

keys = {}
for r in xml:
    inv = parse_invoice_xml((corpus.parent / r["path"]).read_bytes())
    keys[r["invoice_id"]] = anomaly.InvoiceKey(
        seller_tax_code=inv.seller_tax_code or "",
        total_amount=inv.total_amount,
        invoice_number=inv.invoice_number or "",
        issue_date=inv.issue_date,
    )

order = [r["invoice_id"] for r in xml]
for window in (1, 3, 5, 7, 10, 14, 21, 30):
    anomaly.NEAR_DUP_WINDOW_DAYS = window
    flagged = set()
    for i, inv_id in enumerate(order):
        priors = [keys[o] for o in order[:i]]
        if anomaly.near_duplicate(keys[inv_id], priors):
            flagged.add(inv_id)
    tp = len(flagged & truth); fp = len(flagged - truth); fn = len(truth - flagged)
    prec = tp/(tp+fp) if tp+fp else 0.0
    rec = tp/(tp+fn) if tp+fn else 0.0
    print(f"window={window:3d}  flagged={len(flagged):3d}  TP={tp:2d} FP={fp:3d} FN={fn:2d}  P={prec:.3f} R={rec:.3f}")
PY
```

Record the whole table verbatim.

> **The likely outcome is that the window barely matters**, because the generator seeds duplicates a fixed number of days apart. If the sweep is flat across the plausible range, **that is the finding** — record it as "the corpus cannot discriminate this constant" rather than presenting an arbitrary pick as a tuned optimum. A number chosen from a flat curve and reported as measured is exactly the dishonesty this project's constants rule exists to prevent.

- [ ] **Step 2: Write the test that pins whatever the sweep showed**

Create `tests/test_near_dup_window.py` asserting the measured recall at the shipped window against the seeded `near_duplicate` invoices, with a docstring stating the measurement date and the report file. If the sweep was flat, the test asserts recall at the shipped value **and** states in its docstring that neighbouring windows score identically.

- [ ] **Step 3: Rewrite the constant's comment**

Replace the "PROVISIONAL AND UNMEASURED" comment with date + the sweep result + `docs/notes/2026-08-05-m6c-eval-report.md`. If the value stays 7, say why 7 rather than implying it won a comparison it did not.

- [ ] **Step 4: `make check` and commit**

```bash
make check
git add src/invoice_agent/domain/anomaly.py tests/test_near_dup_window.py
git commit -m "docs(m6c): NEAR_DUP_WINDOW_DAYS is measured, and says what the measurement showed"
```

---

### Task 6: `scripts/eval.py`, `make eval`, and the report

Spec §2.6. Every row of design §10.4, each with N, method, and its non-LLM baseline.

**Files:**
- Create: `scripts/eval.py`
- Modify: `Makefile` (add `eval` target and its `help` line)
- Create: `docs/eval-report.md` (generated)
- Test: `tests/test_eval_report.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_report.py`:

```python
"""The report must compare against targets, not restate them.

Design §10.5: a reported 0.52 beats a claimed 0.98. The failure mode worth
guarding is a generator that prints a target beside a number without deciding
whether the target was met — which reads as a pass to every skimming reader.
"""

from scripts.eval import Row, render_report, verdict


def test_a_missed_target_is_rendered_as_missed():
    row = Row(stage="Auditor", target="override precision >= 0.60",
              value=0.125, n=24, method="live", met=False)
    out = render_report([row])
    assert "MISSED" in out
    assert "0.125" in out


def test_an_unmeasured_row_is_never_rendered_as_a_pass():
    row = Row(stage="Vision", target="F1 >= 0.90", value=None, n=0,
              method="unmeasured", met=None)
    out = render_report([row])
    assert "unmeasured" in out.lower()
    assert "MET" not in out.split("Vision")[1].split("\n")[0]


def test_verdict_is_computed_not_supplied():
    """A generator that trusted a hand-written `met` flag could publish a
    missed target as met. The comparison happens in code."""
    assert verdict(0.125, ">=", 0.60) is False
    assert verdict(0.954, ">=", 0.60) is True
    assert verdict(None, ">=", 0.60) is None
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_eval_report.py -v`
Expected: FAIL — `scripts.eval` does not exist.

- [ ] **Step 3: Implement `scripts/eval.py`**

```python
"""Generate docs/eval-report.md — every row of design §10.4, with its N.

The verdict is COMPUTED here, never supplied. A generator that printed a target
beside a number without deciding whether the target was met reads as a pass to
every skimming reader, which is the failure mode design §10.5 is written
against: "a reported 0.52 beats a claimed 0.98."
"""

import argparse
import json
import pathlib
from dataclasses import dataclass
from typing import Any

OPS = {">=": lambda v, t: v >= t, "<=": lambda v, t: v <= t, "==": lambda v, t: v == t}


@dataclass
class Row:
    stage: str
    target: str
    value: float | None
    n: int
    method: str          # "live" | "replay" | "deterministic" | "unmeasured" | "pending"
    met: bool | None     # None = not decidable, and rendered as such


def verdict(value: float | None, op: str, threshold: float) -> bool | None:
    """None when there is nothing to judge. An unmeasured row is NOT a pass."""
    if value is None:
        return None
    return OPS[op](value, threshold)


def _cell(row: Row) -> str:
    if row.met is None:
        return "— unmeasured"
    return "✅ MET" if row.met else "❌ MISSED"


def render_report(rows: list[Row]) -> str:
    lines = [
        "# Eval report",
        "",
        "> Generated by `make eval`. Every row carries its N and how it was obtained.",
        "> A row with no measurement says `unmeasured` — never a blank, and never an",
        "> inherited number presented as fresh.",
        "",
        "| Stage | Target | Measured | N | Method | Verdict |",
        "|---|---|---:|---:|---|---|",
    ]
    for r in rows:
        shown = "—" if r.value is None else f"{r.value:.3f}"
        lines.append(
            f"| {r.stage} | {r.target} | {shown} | {r.n} | {r.method} | {_cell(r)} |"
        )
    return "\n".join(lines) + "\n"


def deterministic_rows(corpus: pathlib.Path) -> list[Row]:
    """The rows that need no LLM and therefore cost nothing to re-run.

    XML parse rate, seeded-error recall, and the flag false-positive rate —
    §10.4's headline metric, because it measures reviewer fatigue, which is what
    decides whether the system gets used at all.
    """
    raise NotImplementedError  # fill in against corpus/manifest.jsonl


def agent_rows(artifact: dict[str, Any] | None) -> list[Row]:
    """Classification, Auditor and Cost, from the pinned-rater artifact.

    `None` (no artifact) yields unmeasured rows rather than absent ones: a
    missing row is invisible, an unmeasured row is a question someone can act on.
    """
    raise NotImplementedError


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the eval report")
    parser.add_argument("--eval-json", type=pathlib.Path, default=None)
    parser.add_argument("--corpus", type=pathlib.Path, default=pathlib.Path("corpus"))
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("docs/eval-report.md"))
    args = parser.parse_args()

    artifact = (
        json.loads(args.eval_json.read_text(encoding="utf-8"))
        if args.eval_json and args.eval_json.exists()
        else None
    )
    rows = deterministic_rows(args.corpus) + agent_rows(artifact)
    args.out.write_text(render_report(rows), encoding="utf-8")
    print(f"wrote {args.out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
```

Fill in the two `NotImplementedError` bodies. Rows required, from design §10.4, all of them:

| Stage | Target | Source |
|---|---|---|
| XML extraction | field exact match 100%, parse failures 0 | deterministic, corpus |
| Vision fallback | F1 ≥ 0.90; `total_amount` exact ≥ 0.85 | `tests/test_vision_accuracy.py`, replay |
| Validation | seeded-error recall 100%, false positives 0 | deterministic, corpus |
| Classification | accuracy ≥ 92% **and** beats `keywords_vi` by ≥15 points | artifact |
| Auditor | override precision ≥ 0.60 (below 0.50 is net harmful) | artifact |
| **Anomaly — flag false-positive rate ≤ 5%** | **headline metric, put it first** | deterministic, corpus |
| Security | injection success 0/30 | M6-A, `tests/test_prompt_injection.py` |
| HITL durability | 10/10 across `kill -9` + resume | M6-B measurement |
| Cost | shadow cost ≤ $0.01/invoice | artifact |
| Business | STP rate ≥ 85% | **PENDING — awaiting operator measurement** |

The classification row must render the `keywords_vi` baseline beside it, not only the delta — principle 3 requires the baseline to be visible, not inferable.

- [ ] **Step 4: Add the Makefile target**

```make
eval:
	$(PY) -m scripts.eval --eval-json docs/notes/2026-08-05-m6c-eval-raw.json --out docs/eval-report.md
```
Add a matching line to the `help` block, in the style of its neighbours.

- [ ] **Step 5: Generate and read the report**

```bash
make eval && head -60 docs/eval-report.md
```
Read it as an interviewer would. Every number must carry N and method; no row may be blank.

- [ ] **Step 6: Mutation — publish a missed target as met**

Make `verdict` return `True` unconditionally.
Run: `.venv/bin/python -m pytest tests/test_eval_report.py -v`
Expected: FAIL. Record, revert.

- [ ] **Step 7: `make check` and commit**

```bash
make check
git add scripts/eval.py tests/test_eval_report.py Makefile docs/eval-report.md
git commit -m "feat(m6c): make eval, and a report that states which targets it missed"
```

---

### Task 7: The manual baseline protocol

Spec §2.7. Item 6.8 requires a self-measured number and cannot be done by an agent.

**Files:**
- Create: `docs/notes/manual-baseline-protocol.md`

- [ ] **Step 1: Write the protocol**

Name the ten specific corpus invoice IDs (a mix of clean, arithmetic-error and multi-line), the exact fields to key into a spreadsheet, how to time each one (start when the file opens, stop when the row is complete), how to record per-invoice times, and how to extrapolate to the 300-invoice corpus. State plainly that quoting a market report instead is the thing this replaces.

- [ ] **Step 2: Confirm the report marks the row PENDING**

The Business/STP row must read **PENDING — awaiting operator measurement**, not a guess and not an omission.

- [ ] **Step 3: Commit**

```bash
git add docs/notes/manual-baseline-protocol.md
git commit -m "docs(m6c): protocol for the one number an agent cannot measure"
```

---

### Task 8: Gate — note, roadmap, push, CI

- [ ] **Step 1: Full check from a clean state**

```bash
make check 2>&1 | tail -5
```

- [ ] **Step 2: Confirm the suite runs with no provider keys**

```bash
env -u GROQ_API_KEY -u GROQ_API_KEYS -u GEMINI_API_KEY -u GEMINI_API_KEYS -u OPENROUTER_API_KEY \
  LLM_MODE=replay .venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
```
Expected: green, and the vision accuracy tests **run rather than skip**.

- [ ] **Step 3: Write `docs/notes/2026-08-05-m6c-eval-report.md`**

Measured output pasted verbatim, not prose claims: the corpus drift and its repair (including the removed stale files); the vision re-key and re-record results; the pinned-rater run with its N and any `unmeasured` rows; the `NEAR_DUP_WINDOW_DAYS` sweep table; the mutation results per task, stating **which named test bit**; and a section for anything that did not work.

- [ ] **Step 4: Update the roadmap**

Items 6.7 and 6.8; the Gate M6 table; and §1's "Next" paragraph. **Leave M6-A's negative-control row at 22/24 NOT MET.** Record the corpus-integrity defect against M6-A in §1, since that is where it originated.

- [ ] **Step 5: Push and confirm CI green on the pushed SHA**

```bash
git push origin main
gh run watch "$(gh run list --limit 1 --json databaseId --jq '.[0].databaseId')"
```

- [ ] **Step 6: The gate is not met until Step 5 is green**

If CI is red, fix and re-push before writing any completion claim. Record the CI run URL in the note.

---

## Definition of done

| Criterion | Evidence |
|---|---|
| Corpus reproducible from a clean clone | `tests/test_corpus_reproducible.py`, mutation red |
| Vision fixtures travel to CI | vision accuracy tests run (not skip) on Linux CI |
| `make eval` produces every §10.4 row | `docs/eval-report.md`, each row with N and method |
| Classification beats `keywords_vi` by ≥15 | measured, single rater, model attributed |
| Single rater verified | one `answering_model` per agent in the artifact |
| Auditor row published however it scores | reported with CI, even at 12.5% |
| `NEAR_DUP_WINDOW_DAYS` justified | comment carries date + measurement + report file |
| Business row | PENDING with protocol written — explicitly not a pass |
| `make check` green on the pushed commit, CI agreeing | CI run URL on the closing SHA |
