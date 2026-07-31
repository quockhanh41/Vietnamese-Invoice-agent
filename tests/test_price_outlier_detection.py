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


def test_new_vendor_fires_on_every_first_sighting_and_only_those(corpus):
    """NEW_VENDOR is NOT scored against the `new_vendor` defect label.

    Measured 2026-07-31: 9 declared, 51 distinct vendors, so 42 invoices are a
    vendor's genuine first sighting without carrying the injected defect. Scored
    against the label that reads as 42 false positives — 14% of the corpus,
    which would blow design §10.4's 5% flag false-positive ceiling on its own.

    THE METRIC WAS WRONG, NOT THE RULE. `anomaly.new_vendor` fires on a true
    property: this vendor has no history. The `new_vendor` DEFECT label marks
    something else — that the generator deliberately injected a never-before-seen
    vendor. Declaring all 51 first sightings as defects would conflate an
    injected defect with a natural property of any corpus read in order.

    DECISION, recorded here so M6.7 does not rediscover it: the flag
    false-positive rate covers findings that ASSERT SOMETHING IS WRONG.
    NEW_VENDOR is severity `info` and asserts something is NEW, which is true
    every time it fires. It is reviewer context, not an error claim, and is
    excluded from that metric. The M4-B plan's criterion of "9/9 with 0 false
    positives" was itself the defect; this is the correction.

    What must hold is the property, not the count: fires on exactly the first
    sighting of each vendor, never on a later one.
    """
    seen: collections.Counter = collections.Counter()
    first_sightings = wrong = missed_declared = 0
    for r in sorted(corpus, key=lambda x: x["invoice_id"]):
        prior = seen[r["seller_tax_code"]]
        stats = None if prior == 0 else anomaly.VendorStats(prior, None, None)
        raised = anomaly.new_vendor(stats) is not None
        if prior == 0:
            first_sightings += 1
            wrong += not raised
        else:
            wrong += raised
        missed_declared += ("new_vendor" in r["defects"]) and not raised
        seen[r["seller_tax_code"]] += 1

    print(
        f"\nNEW_VENDOR  first sightings={first_sightings} "
        f"declared={sum(1 for r in corpus if 'new_vendor' in r['defects'])} "
        f"disagreements={wrong}"
    )
    assert wrong == 0, (
        f"{wrong} invoices where NEW_VENDOR disagreed with 'is this the vendor's "
        f"first sighting' — that is the only thing this rule claims"
    )
    assert missed_declared == 0, "a declared new_vendor was not a first sighting"
    assert first_sightings == len({r["seller_tax_code"] for r in corpus})


def test_new_vendor_is_excluded_from_the_flag_false_positive_metric():
    """Pin the §10.4 decision above so a later change is deliberate.

    Severity is the discriminator: `info` findings are reviewer context and do
    not enter the flag false-positive rate; `warn` and `critical` assert a
    defect and do.
    """
    from invoice_agent.domain import findings

    severity, _source = findings.CODE_REGISTRY[findings.NEW_VENDOR]
    assert severity == "info", (
        "NEW_VENDOR is excluded from the flag false-positive metric BECAUSE it "
        "is informational. Promote it to warn/critical and 42 of 300 corpus "
        "invoices (14%) start counting against design §10.4's 5% ceiling."
    )
    assert findings.CODE_REGISTRY[findings.PRICE_OUTLIER][0] == "warn"
