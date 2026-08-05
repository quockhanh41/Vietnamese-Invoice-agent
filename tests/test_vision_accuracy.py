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

# Invoice IDs where a replay miss is a KNOWN, recorded gap — e.g. the
# INV-0029 provider-503 precedent above — rather than a stale cache key.
# Empty since M6-C: now that the replay key is sha256(pdf_bytes) instead of
# sha256(rasterized PNG) (spec §2.2), a miss on a TWINS entry can no longer be
# explained away as "recorded on another platform" — it means the fixture is
# genuinely missing or the document content changed, which is a real defect
# and must FAIL, not SKIP. See tests/test_vision_replay_key.py.
ALLOWED_MISSES: dict[str, str] = {}

# The fields a reviewer would check first, and the ones that decide whether an
# invoice can be posted at all: its identity and its amount.
FIELDS = ("invoice_number", "invoice_series", "seller_tax_code", "total_amount")


def _manifest() -> dict[str, dict]:
    path = CORPUS / "manifest.jsonl"
    if not path.exists():
        pytest.skip("corpus not generated — run `make generate`")
    return {r["invoice_id"]: r for r in map(json.loads, path.open(encoding="utf-8"))}


def _measure(invoice_id: str, gateway):
    """Read one twin's XML truth and its vision extraction — or raise
    `LLMReplayMiss` if no fixture matches. Shared by the scoring test and the
    vacuous-case guard below, so both agree on what "measured" means."""
    from invoice_agent.extraction import vision
    from invoice_agent.extraction.xml_parser import parse_invoice_xml

    record = _manifest()[invoice_id]
    truth = parse_invoice_xml(pathlib.Path(record["path"]).read_bytes())
    extracted = vision.extract(
        pathlib.Path(record["pdf_path"]).read_bytes(),
        gateway=gateway,
        current_errors=None,
        calls=[],
    )
    return truth, extracted


@pytest.mark.parametrize("invoice_id", TWINS)
def test_vision_extraction_matches_its_xml_twin(invoice_id, monkeypatch, capsys):
    monkeypatch.setenv("LLM_MODE", "replay")

    from invoice_agent.config import Settings
    from invoice_agent.llm.cache import LLMReplayMiss
    from invoice_agent.llm.gateway import LLMGateway

    try:
        truth, extracted = _measure(invoice_id, LLMGateway(Settings()))
    except LLMReplayMiss as miss:
        if invoice_id in ALLOWED_MISSES:
            pytest.skip(f"{invoice_id}: recorded gap — {ALLOWED_MISSES[invoice_id]}")
        # FOUND by review (M6-C task 2): the old code converted ANY
        # LLMReplayMiss into a SKIP under one generic "cross-platform
        # rasterization" message. That made a real, content-driven key change
        # (exactly what M6-C Task 1's corpus fix caused) indistinguishable
        # from a benign platform difference — a silent-green pattern. Since
        # the key is now sha256(pdf_bytes), not sha256(rendered PNG), a miss
        # on a TWINS entry is no longer explainable by rendering differences
        # at all: it means the fixture was never (re-)recorded. Fail loudly
        # unless the invoice is explicitly allow-listed above.
        pytest.fail(
            f"{invoice_id}: no replay fixture for this vision call. The key is "
            f"sha256(pdf_bytes) (spec §2.2), so this is not a cross-platform "
            f"rasterization difference — the fixture is genuinely missing. "
            f"Re-record with LLM_MODE=record, or add {invoice_id} to "
            f"ALLOWED_MISSES with a recorded reason. ({miss})"
        )

    disagreed = [
        f"{f}: xml={getattr(truth, f)!r} vision={getattr(extracted, f)!r}"
        for f in FIELDS
        if getattr(extracted, f) != getattr(truth, f)
    ]
    with capsys.disabled():
        print(f"\n  {invoice_id}: {len(FIELDS) - len(disagreed)}/{len(FIELDS)} fields agree")
    assert not disagreed, disagreed


def test_at_least_one_twin_is_measured(monkeypatch):
    """Guards the vacuous case for real, not just in appearance.

    The old version only asserted `TWINS` was non-empty — it could not catch
    the case where every twin SKIPPED, which is exactly what happened for
    three milestones: a non-empty tuple proved nothing about whether any
    fixture actually replayed. This attempts every twin itself and fails if
    none of them produce a result, independent of whatever the parametrized
    test above did.
    """
    monkeypatch.setenv("LLM_MODE", "replay")

    from invoice_agent.config import Settings
    from invoice_agent.llm.cache import LLMReplayMiss
    from invoice_agent.llm.gateway import LLMGateway

    gateway = LLMGateway(Settings())
    measured = []
    for invoice_id in TWINS:
        try:
            _measure(invoice_id, gateway)
            measured.append(invoice_id)
        except LLMReplayMiss:
            pass
    assert measured, (
        "every vision twin replay-missed — the debt is unpaid again. "
        f"TWINS={TWINS!r}"
    )
