"""Sweep result for NEAR_DUP_WINDOW_DAYS — the constant's only measurement.

Measured 2026-08-05. Full sweep table and generator analysis are in
docs/notes/2026-08-05-m6c-eval-report.md.

`near_duplicate_of()` (scripts/generator/defects.py) shifts the seeded pair's
issue date by `rng.randint(1, 5)` days, so no seeded near_duplicate pair is
ever more than 5 days apart. Sweeping the window (mirroring `serial_join`'s
canonical-order priors, batch.py:267) confirms this directly: recall rises
0.0 -> 0.333 -> 1.0 across windows 1, 3, 5, then windows 5 through 30 score
IDENTICALLY (9/9 recall, 6 false positives every time). The corpus cannot
discriminate any window >= 5 from any other — `test_near_dup_window_plateau`
pins that plateau as a running assertion, not a docstring claim, so a future
change that breaks it fails loudly instead of silently.

ORDERING, checked because it could have mattered: `sweep_inputs` below sorts
invoices by `_join`'s actual canonical key, `(issue_date, invoice_series,
invoice_number)` (batch.py:175-179) — NOT raw manifest/generation order.
Verified 2026-08-05 (fix note in task-5-report.md): the two orderings differ
at 284 of the 300 positions in this corpus, so this is not a formality. Both
orderings were measured and produce the IDENTICAL table below — a stronger,
separately-checked statement than either measurement alone, and it means the
constant is insensitive to which order the priors arrive in.

The floor of 6 false positives is structural, not window-driven: it is the
six §9.1 prompt-injection payload / negative-control twin pairs (INV-0004/5,
0006/7, 0008/9, 0010/11, 0012/13, 0014/15) — same seller, same total, same
day, different invoice number by construction (`strip_injection` in
scripts/generator/defects.py). They are caught starting at window=0 and stay
caught at every window tested; they are not evidence about window size.
"""

from datetime import date
from decimal import Decimal

import pytest
from scripts import generate_invoices
from scripts.generator import manifest

from invoice_agent.domain import anomaly


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    out = tmp_path_factory.mktemp("corpus_ndw") / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    return manifest.read_manifest(out / "manifest.jsonl")


def _key(record):
    return anomaly.InvoiceKey(
        seller_tax_code=record["seller_tax_code"],
        invoice_number=record["invoice_number"],
        issue_date=date.fromisoformat(record["issue_date"]),
        total_amount=Decimal(record["total_amount"]),
    )


def _score(xml_records, truth, order, keys):
    """TP/FP against `near_duplicate`'s only production consumer: each
    candidate is scored against priors earlier in `order` — the caller
    (`sweep_inputs`) sorts `order` into `_join`'s canonical key so this
    matches what `serial_join` actually feeds it (batch.py:267)."""
    flagged = set()
    for i, inv_id in enumerate(order):
        priors = [keys[o] for o in order[:i]]
        if anomaly.near_duplicate(keys[inv_id], priors):
            flagged.add(inv_id)
    tp = len(flagged & truth)
    fp = len(flagged - truth)
    return tp, fp


@pytest.fixture
def sweep_inputs(corpus):
    xml = [r for r in corpus if r["source_type"] == "xml"]
    truth = {r["invoice_id"] for r in xml if "near_duplicate" in r["defects"]}
    assert len(truth) == 9, "corpus's seeded near_duplicate count drifted from 9"
    by_id = {r["invoice_id"]: r for r in xml}
    # `_join`'s canonical key (batch.py:175-179), not manifest/generation
    # order — see the module docstring's ORDERING note for why this matters.
    order = sorted(
        by_id,
        key=lambda iid: (
            by_id[iid]["issue_date"],
            by_id[iid]["invoice_series"],
            by_id[iid]["invoice_number"],
        ),
    )
    keys = {r["invoice_id"]: _key(r) for r in xml}
    return xml, truth, order, keys


def test_near_dup_window_sweep_matches_measurement(sweep_inputs, monkeypatch):
    """Pins the FULL sweep table measured 2026-08-05, verbatim: (TP, FP) per
    window. Windows 1 and 3 are BELOW the generator's max shift of 5 days and
    genuinely score worse — the curve is not flat everywhere, only from 5 up.
    """
    xml, truth, order, keys = sweep_inputs
    expected = {
        1: (0, 6),
        3: (3, 6),
        5: (9, 6),
        7: (9, 6),
        10: (9, 6),
        14: (9, 6),
        21: (9, 6),
        30: (9, 6),
    }
    for window, want in expected.items():
        monkeypatch.setattr(anomaly, "NEAR_DUP_WINDOW_DAYS", window)
        got = _score(xml, truth, order, keys)
        assert got == want, f"window={window} scored {got}, expected {want}"


def test_near_dup_window_plateau_is_flat_from_5_through_30(sweep_inputs, monkeypatch):
    """Windows 5, 7, 10, 14, 21, 30 all score IDENTICALLY (9 TP, 6 FP) — this
    is the "corpus cannot discriminate this constant" finding as a running
    assertion. It fails if a future corpus change makes any of these disagree.
    """
    xml, truth, order, keys = sweep_inputs
    results = set()
    for window in (5, 7, 10, 14, 21, 30):
        monkeypatch.setattr(anomaly, "NEAR_DUP_WINDOW_DAYS", window)
        results.add(_score(xml, truth, order, keys))
    assert results == {(9, 6)}, f"plateau broke: {results}"


def test_near_dup_window_recall_at_shipped_value(sweep_inputs):
    """Recall at the CURRENT shipped NEAR_DUP_WINDOW_DAYS (7): full recall,
    9/9, with the structural floor of 6 false positives from the injection
    negative-control twins (see module docstring) — unrelated to window size.
    """
    xml, truth, order, keys = sweep_inputs
    tp, fp = _score(xml, truth, order, keys)
    assert tp == len(truth), f"recall {tp}/{len(truth)} at window={anomaly.NEAR_DUP_WINDOW_DAYS}"
    assert fp == 6, f"false positives {fp} at window={anomaly.NEAR_DUP_WINDOW_DAYS}"
