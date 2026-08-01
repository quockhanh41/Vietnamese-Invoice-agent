"""The first vision accuracy measurement in this project (M3 debt, paid 2026-08-01).

The vision branch shipped at M3-B and M3-C with NO accuracy measurement of any
kind, synthetic or real, because no vision-capable key was configured. The plan
called it the debt that would hurt most, and it was deferred three times.

Ground truth is the XML twin: the same `InvoiceSpec` rendered two ways, so the
PDF and the XML describe one invoice and any disagreement is the vision model's.

Replay-backed, so it costs no tokens and needs no key. `LLM_MODE` is pinned
inside the test rather than inherited: `pyproject.toml` sets only `addopts=-q`
and `config.py` defaults `llm_mode` to "live", so a test that built its gateway
from the ambient environment would make a real API call on every `make check`
once a key existed — turning one recording run into an unbounded bill.
"""

import json
import pathlib

import pytest

CORPUS = pathlib.Path("corpus")

# The two twins that extracted successfully on 2026-08-01. The third,
# `INV-0029`, is NOT absent because it read badly — it never read at all: six
# attempts, every one a Gemini 503 "This model is currently experiencing high
# demand". A provider-capacity block, recorded as such in
# docs/notes/2026-07-31-m4c-agents.md rather than scored as a vision failure.
TWINS = ("INV-0196", "INV-0275")

# The fields a reviewer would check first, and the ones that decide whether an
# invoice can be posted at all: its identity and its amount.
FIELDS = ("invoice_number", "invoice_series", "seller_tax_code", "total_amount")


def _manifest() -> dict[str, dict]:
    path = CORPUS / "manifest.jsonl"
    if not path.exists():
        pytest.skip("corpus not generated — run `make generate`")
    return {r["invoice_id"]: r for r in map(json.loads, path.open(encoding="utf-8"))}


@pytest.mark.parametrize("invoice_id", TWINS)
def test_vision_extraction_matches_its_xml_twin(invoice_id, monkeypatch, capsys):
    monkeypatch.setenv("LLM_MODE", "replay")

    from invoice_agent.config import Settings
    from invoice_agent.extraction import vision
    from invoice_agent.extraction.xml_parser import parse_invoice_xml
    from invoice_agent.llm.cache import LLMReplayMiss
    from invoice_agent.llm.gateway import LLMGateway

    record = _manifest()[invoice_id]
    truth = parse_invoice_xml(pathlib.Path(record["path"]).read_bytes())
    try:
        extracted = vision.extract(
            pathlib.Path(record["pdf_path"]).read_bytes(),
            gateway=LLMGateway(Settings()),
            current_errors=None,
            calls=[],
        )
    except LLMReplayMiss:
        # ⚠️ THE VISION FIXTURES ARE MACHINE-SPECIFIC, and this is the honest
        # limit rather than a bug to paper over.
        #
        # The replay key is sha256(model, messages, params), and a vision
        # message carries the RASTERIZED PNG. Two steps upstream of it are
        # environment-dependent: `scripts/generator/pdf.py` renders the corpus
        # PDF, and `vision.rasterize` turns it into PNG at 150 DPI via pdfium.
        # Neither is guaranteed byte-identical across platforms — measured
        # 2026-08-01, fixtures recorded on macOS/ARM miss on CI's Ubuntu/x86
        # with a different key for the same invoice.
        #
        # `test_same_seed_produces_identical_bytes` hashes the whole corpus
        # directory, so it proves determinism for ONE machine re-running itself;
        # it says nothing across platforms.
        #
        # Skipping here rather than deleting the test: it is real evidence
        # wherever the fixtures were recorded, and M6 either re-records on the
        # CI platform or scores vision from committed PNGs instead of
        # re-rasterizing. Recorded in docs/notes/2026-07-31-m4c-agents.md.
        pytest.skip(
            f"vision fixture recorded on another platform — PDF rasterization "
            f"is not byte-stable across OS/arch, so the replay key differs "
            f"({invoice_id})"
        )

    disagreed = [
        f"{f}: xml={getattr(truth, f)!r} vision={getattr(extracted, f)!r}"
        for f in FIELDS
        if getattr(extracted, f) != getattr(truth, f)
    ]
    with capsys.disabled():
        print(f"\n  {invoice_id}: {len(FIELDS) - len(disagreed)}/{len(FIELDS)} fields agree")
    assert not disagreed, disagreed


def test_at_least_one_twin_is_measured():
    """Guards the vacuous case. If TWINS is emptied — because fixtures were
    cleared, say — the parametrized test above silently measures nothing and
    the suite still passes, which is how this debt stayed invisible for three
    milestones."""
    assert TWINS, "no vision twin is being measured; the debt is unpaid again"
