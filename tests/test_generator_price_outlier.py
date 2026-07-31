"""The price_outlier group must be detectable in principle.

Measured 2026-07-31 before this change: 3 of 6 were ALSO new_vendor, so their
vendor had no history and the z-score guard rejected them; and
OUTLIER_MULTIPLIER = 10 scaled the invoice's OWN amount against a within-vendor
coefficient of variation of ~0.95, so INV-0093 landed at z = 0.14 — not an
outlier at all. Recall was 0/6 (self-included) or 1/6 (self-excluded).
See spec 2026-07-31-m4b §1.1 and §4.
"""

import collections
import statistics

from scripts import generate_invoices
from scripts.generator import manifest


def build(tmp_path, count=300, seed=42):
    out = tmp_path / "corpus"
    generate_invoices.generate(count=count, seed=seed, out=out)
    return out, manifest.read_manifest(out / "manifest.jsonl")


def test_no_price_outlier_is_also_a_new_vendor(tmp_path):
    """A new vendor has no history, so the z-score guard rejects it before any
    threshold applies — the invoice is undetectable by construction."""
    _out, records = build(tmp_path)
    both = [
        r["invoice_id"] for r in records
        if "price_outlier" in r["defects"] and "new_vendor" in r["defects"]
    ]
    assert not both, f"price_outlier invoices with no vendor history: {both}"


def test_new_vendor_count_is_unchanged(tmp_path):
    """Redistributing must not shrink the group the ratios declare."""
    _out, records = build(tmp_path)
    assert sum(1 for r in records if "new_vendor" in r["defects"]) == 9


def test_budget_overrun_new_vendor_pairing_survives(tmp_path):
    """`defects.py` records that real invoices arrive as 'new vendor AND over
    budget'. That co-occurrence is meaningful and stays; only the pathological
    price_outlier pairing goes."""
    _out, records = build(tmp_path)
    both = [
        r for r in records
        if "budget_overrun" in r["defects"] and "new_vendor" in r["defects"]
    ]
    assert len(both) == 3


def test_every_price_outlier_is_far_from_its_vendors_other_invoices(tmp_path):
    """The property that makes the group detectable, stated in the generator's
    OWN terms: the outlier is a large multiple of the mean of its vendor's other
    invoices. Deliberately NOT a z-score — see spec §4.2: the generator uses the
    mean and the detector uses sigma, so this test cannot pass by encoding the
    detector's rule.
    """
    _out, records = build(tmp_path)
    by_vendor = collections.defaultdict(list)
    for r in records:
        by_vendor[r["seller_tax_code"]].append(r)

    checked = 0
    for r in records:
        if "price_outlier" not in r["defects"]:
            continue
        others = [
            float(p["total_amount"]) for p in by_vendor[r["seller_tax_code"]]
            if p["invoice_id"] != r["invoice_id"]
        ]
        assert len(others) >= 4, f"{r['invoice_id']}: vendor has too little history"
        checked += 1
        ratio = float(r["total_amount"]) / statistics.mean(others)
        assert ratio >= 4.0, f"{r['invoice_id']}: only {ratio:.1f}x its vendor's mean"
    assert checked == 6, f"expected 6 price_outlier invoices, checked {checked}"
