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

# Measured 2026-08-05 on macOS/ARM after Task 1's regeneration. See
# docs/notes/2026-08-05-m6c-eval-report.md.
PINNED_PDF_DIGESTS: dict[str, str] = {
    "INV-0029.pdf": "0f69ec37fad047057ca31fbfd342a898868040e7ad08e26f6679344d59e3bc32",
}


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
    with pytest.raises(Exception):  # noqa: B017 — LLMReplayMiss, but the key spy runs regardless of which error fires
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
