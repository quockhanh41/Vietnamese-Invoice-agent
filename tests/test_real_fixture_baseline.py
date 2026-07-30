"""The only non-synthetic check on the classification label.

Spec 2026-07-31-m4a §4.1: tier-`keyword` descriptions contain seeded keywords
verbatim, so the synthetic baseline score is a CONSTRUCTION PARAMETER, not a
measurement. Only the LLM's margin over it is evidence, and even that margin is
bounded by whoever wrote the synonym tier. These 8 real invoices are the held-out
check — weak at n=5 labelable, and the only real one that exists.

This test RECORDS rather than gates. It fails only if the fixture set or the
label file drifts out of agreement, never on the score itself.
"""

import json
import pathlib

from scripts.seed_reference import CATEGORIES

from invoice_agent.eval import baseline  # TEST-ONLY
from invoice_agent.extraction.xml_parser import parse_invoice_xml  # TEST-ONLY

FIXTURES = pathlib.Path("tests/fixtures/real_samples")
KEYWORDS = {code: list(keywords) for code, _n, _d, _v, _c, keywords in CATEGORIES}


def _labels() -> dict[str, str]:
    raw = json.loads((FIXTURES / "categories.json").read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def test_every_real_fixture_carries_a_label():
    """Add a fixture without labelling it and this goes red, rather than the
    held-out check silently shrinking."""
    on_disk = {p.name for p in FIXTURES.glob("*.xml")}
    labelled = set(_labels())
    assert on_disk == labelled, f"unlabelled: {on_disk - labelled}, stale: {labelled - on_disk}"


def test_labels_name_seeded_categories_or_UNLABELABLE():
    seeded = {row[0] for row in CATEGORIES} | {"UNLABELABLE"}
    for name, label in _labels().items():
        assert label in seeded, f"{name}: {label} is not a seeded category"


def test_baseline_on_real_invoices_is_recorded():
    """Prints the held-out number. Run with `-s` to read it."""
    labels = _labels()
    scored = correct = 0
    lines = []
    for name, truth in sorted(labels.items()):
        if truth == "UNLABELABLE":
            continue
        invoice = parse_invoice_xml((FIXTURES / name).read_bytes())
        text = " ".join(line.description or "" for line in invoice.lines)
        predicted = baseline.classify(text, KEYWORDS)
        scored += 1
        correct += predicted == truth
        lines.append(f"  {name}: truth={truth} predicted={predicted}")
    print(f"\nreal-fixture baseline: {correct}/{scored}")
    print("\n".join(lines))
    assert scored >= 5, "fewer than 5 labelable real fixtures — the check is too weak to report"
