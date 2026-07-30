"""The gate batch M4-D measures against.

BUDGET_BREACH is a property of the BATCH, not of the invoice: all 12
budget_overrun invoices are MKT, MKT's seeded limit is 50,000,000, and the corpus
holds 59 MKT invoices totalling 993,010,000 (measured 2026-07-30). So a
per-invoice expectation is only true relative to a declared batch, and this is
that declaration. See spec 2026-07-31-m4a §1.1 D5 and §5.
"""

import datetime
import json
from decimal import Decimal

from scripts import generate_invoices
from scripts.generator import gate_batch, manifest

from invoice_agent.domain import budget  # TEST-ONLY

MKT_LIMIT = Decimal("50000000")
WARN_PCT = Decimal("0.8")


def build(tmp_path):
    out = tmp_path / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    return out, manifest.read_manifest(out / "manifest.jsonl")


def test_gate_batch_has_thirty_distinct_invoices():
    assert len(gate_batch.GATE_BATCH_IDS) == 30
    assert len(set(gate_batch.GATE_BATCH_IDS)) == 30


def test_gate_batch_ids_all_exist(tmp_path):
    _out, records = build(tmp_path)
    known = {r["invoice_id"] for r in records}
    missing = [i for i in gate_batch.GATE_BATCH_IDS if i not in known]
    assert not missing, f"declared ids not in the corpus: {missing}"


def test_gate_batch_covers_the_cases_it_claims(tmp_path):
    """Coverage, asserted rather than intended. If regeneration moves a defect
    off one of these ids, this goes red instead of the batch quietly losing
    the case M4-D exists to exercise."""
    _out, records = build(tmp_path)
    chosen = [r for r in records if r["invoice_id"] in set(gate_batch.GATE_BATCH_IDS)]
    defect_union = {d for r in chosen for d in r["defects"]}
    for required in ("budget_overrun", "exact_duplicate", "near_duplicate",
                     "new_vendor", "price_outlier"):
        assert required in defect_union, f"gate batch has no {required} case"


def test_the_budget_case_invoices_are_the_only_MKT_members(tmp_path):
    """Any other MKT spend consumes the same 50,000,000 and changes which
    invoice breaches — the expected chain below would silently stop holding."""
    _out, records = build(tmp_path)
    chosen = [r for r in records if r["invoice_id"] in set(gate_batch.GATE_BATCH_IDS)]
    mkt = sorted(r["invoice_id"] for r in chosen if r["department"] == "MKT")
    assert mkt == ["INV-0001", "INV-0002", "INV-0003"], mkt


def test_declared_breach_matches_domain_budget(tmp_path):
    """The declaration is checked against the arithmetic, not asserted beside it."""
    _out, records = build(tmp_path)
    by_id = {r["invoice_id"]: r for r in records}
    mkt_inputs = [
        budget.BudgetInput(
            invoice_id=r["invoice_id"],
            issue_date=datetime.date.fromisoformat(r["issue_date"]),
            invoice_series=r["invoice_series"],
            invoice_number=r["invoice_number"],
            amount=Decimal(r["total_amount"]),
        )
        for r in (by_id[i] for i in gate_batch.GATE_BATCH_IDS)
        if r["department"] == "MKT"
    ]
    consumptions = budget.consume(MKT_LIMIT, WARN_PCT, mkt_inputs)
    breached = [c.invoice_id for c in consumptions
                if any(f.code == "BUDGET_BREACH" for f in c.findings)]
    assert breached == [gate_batch.EXPECTED_BREACH_ID], breached


def test_generate_emits_gate_batch_json(tmp_path):
    out, _records = build(tmp_path)
    payload = json.loads((out / "gate_batch.json").read_text(encoding="utf-8"))
    assert payload["invoice_ids"] == list(gate_batch.GATE_BATCH_IDS)
    assert payload["expected_breach_id"] == gate_batch.EXPECTED_BREACH_ID
    assert len(payload["balance_after_chain"]) == 3
