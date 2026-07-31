"""Recall and false-positive rate for the two entity-memory signals.

This is M4-B's contribution to the M4 gate, and it is deterministic: no LLM is
involved, so it costs nothing to repeat.

Statistics come from each vendor's OTHER invoices, mirroring what `signals` sees
at run time — M4-D persists after the fan-out join, so the scored invoice is
never in its own profile. Scoring it any other way measures a system that does
not exist.
"""

import collections
from decimal import Decimal

import pytest
from scripts import generate_invoices
from scripts.generator import manifest

from invoice_agent.domain import anomaly
from invoice_agent.memory.vendor_profile import derive


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    out = tmp_path_factory.mktemp("corpus_pod") / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    return manifest.read_manifest(out / "manifest.jsonl")


def _stats_excluding(records, tax_code, invoice_id):
    amounts = [
        Decimal(r["total_amount"]) for r in records
        if r["seller_tax_code"] == tax_code and r["invoice_id"] != invoice_id
    ]
    count = len(amounts)
    total = sum(amounts, Decimal(0))
    sum_sq = sum((a * a for a in amounts), Decimal(0))
    avg, stddev = derive(count, total, sum_sq)
    return anomaly.VendorStats(invoice_count=count, avg_amount=avg, stddev_amount=stddev)


def score(records):
    tp = fp = fn = 0
    for r in records:
        stats = _stats_excluding(records, r["seller_tax_code"], r["invoice_id"])
        raised = anomaly.price_outlier(Decimal(r["total_amount"]), stats) is not None
        declared = "price_outlier" in r["defects"]
        tp += raised and declared
        fp += raised and not declared
        fn += declared and not raised
    return tp, fp, fn


def test_price_outlier_recall_and_false_positives(corpus):
    tp, fp, fn = score(corpus)
    total_declared = tp + fn
    clean = len(corpus) - total_declared
    print(
        f"\nPRICE_OUTLIER  recall {tp}/{total_declared}  "
        f"false positives {fp}/{clean} ({fp / clean:.1%})  "
        f"Z={anomaly.PRICE_OUTLIER_Z} MIN_N={anomaly.PRICE_OUTLIER_MIN_INVOICES}"
    )
    assert tp >= 5, f"recall {tp}/{total_declared} — was 0/6 before M4-B"
    # Design §10.4 targets a flag false-positive rate <= 5%.
    assert fp / clean <= 0.05, f"false-positive rate {fp / clean:.1%} exceeds 5%"


def test_new_vendor_is_exact(corpus):
    """9 declared, and no invoice from a vendor with history may raise it."""
    seen: collections.Counter = collections.Counter()
    tp = fp = fn = 0
    for r in sorted(corpus, key=lambda x: x["invoice_id"]):
        prior = seen[r["seller_tax_code"]]
        stats = None if prior == 0 else anomaly.VendorStats(prior, None, None)
        raised = anomaly.new_vendor(stats) is not None
        declared = "new_vendor" in r["defects"]
        tp += raised and declared
        fp += raised and not declared
        fn += declared and not raised
        seen[r["seller_tax_code"]] += 1
    print(f"\nNEW_VENDOR  tp={tp} fp={fp} fn={fn}")
    assert fn == 0, f"{fn} declared new vendors were missed"
