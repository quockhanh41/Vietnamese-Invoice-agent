"""The corpus must leave the LLM room to win, and the baseline room to compete.

Measured 2026-07-30 before Task 4: baseline agreement 5/285 = 1.8%, below the
16.7% chance rate. A baseline near 100% would be the opposite failure — it would
mean the classify node does not need an LLM at all (principle 3), which is a
legitimate finding but not the one this corpus is built to support.
"""

import pathlib

from scripts import generate_invoices
from scripts.generator import manifest
from scripts.seed_reference import CATEGORIES

from invoice_agent.eval import baseline  # TEST-ONLY
from invoice_agent.extraction.xml_parser import parse_invoice_xml  # TEST-ONLY

KEYWORDS = {code: list(keywords) for code, _n, _d, _v, _c, keywords in CATEGORIES}

# The band declared in spec 2026-07-31-m4a §3.2. Measured value and the TIER_MIX
# that produced it are recorded in docs/notes/2026-07-31-m4a-corpus-labels.md.
BAND_LOW, BAND_HIGH = 0.55, 0.70


def measure(out: pathlib.Path, records: list[dict]) -> tuple[int, int, int]:
    """Returns (correct, no_match, total) over XML invoices."""
    correct = no_match = total = 0
    for record in records:
        if record["source_type"] != "xml":
            continue
        invoice = parse_invoice_xml((out.parent / record["path"]).read_bytes())
        text = " ".join(line.description or "" for line in invoice.lines)
        predicted = baseline.classify(text, KEYWORDS)
        total += 1
        if predicted is None:
            no_match += 1
        elif predicted == record["category"]:
            correct += 1
    return correct, no_match, total


def test_baseline_accuracy_is_in_the_tuned_band(tmp_path):
    out = tmp_path / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    records = manifest.read_manifest(out / "manifest.jsonl")
    correct, no_match, total = measure(out, records)
    accuracy = correct / total
    assert BAND_LOW <= accuracy <= BAND_HIGH, (
        f"baseline accuracy {accuracy:.1%} ({correct}/{total}, {no_match} no-match) "
        f"is outside the declared band {BAND_LOW:.0%}-{BAND_HIGH:.0%}. "
        f"Retune descriptions.TIER_MIX, or record the real number and drop the "
        f"band per principle 2 — do not adjust the number to fit."
    )


def test_baseline_beats_chance_by_a_wide_margin(tmp_path):
    """The floor the previous corpus failed: 1.8% against a 16.7% chance rate."""
    out = tmp_path / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    records = manifest.read_manifest(out / "manifest.jsonl")
    correct, _no_match, total = measure(out, records)
    assert correct / total > 2 * (1 / 6)
