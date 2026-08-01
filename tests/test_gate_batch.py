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
    assert len(payload["balance_chains"]["MKT"]) == 3


def _declared_comments() -> dict[str, str]:
    """Parse the per-id comments in the frozen list.

    Format, as written by the M4-A discovery script:
        "INV-0001",  # MKT / MARKETING — budget_overrun, new_vendor
    """
    import pathlib
    import re

    source = pathlib.Path("scripts/generator/gate_batch.py").read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r'"(INV-\d+)",\s*#\s*(.+)', source):
        out[m.group(1)] = m.group(2).strip()
    return out


def test_gate_batch_comments_still_describe_their_invoices(tmp_path):
    """The assertion whose absence let the batch drift.

    Measured 2026-08-01: INV-0122 and INV-0123 were commented "clean" while the
    corpus had made them `unreadable_pdf`. M4-B's regeneration shifted
    `_allocate_indices` consumption, so the frozen ids moved to different
    invoices — and every existing assertion (count, existence, defect coverage,
    MKT membership, breach chain) still passed, because none of them looks at
    what an id actually IS.

    This parses the comments and checks them against the corpus, so the next
    regeneration that moves an id fails loudly instead of silently.
    """
    _out, records = build(tmp_path)
    by_id = {r["invoice_id"]: r for r in records}
    comments = _declared_comments()

    assert set(comments) == set(gate_batch.GATE_BATCH_IDS), (
        "every frozen id needs a comment and vice versa"
    )

    wrong = []
    for inv_id, comment in comments.items():
        record = by_id[inv_id]
        head, _, defect_text = comment.partition("—")
        dept, _, category = head.strip().partition("/")
        expected_defects = (
            set()
            if defect_text.strip() == "clean"
            else {d.strip() for d in defect_text.split(",") if d.strip()}
        )
        actual = set(record["defects"])
        if dept.strip() != record["department"] or category.strip() != record["category"]:
            wrong.append(f"{inv_id}: comment says {head.strip()}, corpus says "
                         f"{record['department']} / {record['category']}")
        if expected_defects != actual:
            wrong.append(f"{inv_id}: comment says {expected_defects or 'clean'}, "
                         f"corpus says {actual or 'clean'}")
    assert not wrong, wrong


def test_gate_batch_source_types_match_the_declaration(tmp_path):
    """Two members are deliberately `unreadable_pdf` — they exercise "one
    corrupt file does not kill the batch" inside the gate itself rather than in
    a contrived side test. Pinned so neither the count nor the intent drifts."""
    _out, records = build(tmp_path)
    by_id = {r["invoice_id"]: r for r in records}
    chosen = [by_id[i] for i in gate_batch.GATE_BATCH_IDS]

    pdfs = [r["invoice_id"] for r in chosen if r["source_type"] != "xml"]
    assert sorted(pdfs) == sorted(gate_batch.DELIBERATE_PDF_MEMBERS)
    assert all("unreadable_pdf" in by_id[i]["defects"] for i in pdfs)
    assert len(chosen) - len(pdfs) == 28


def _write_and_load(tmp_path):
    out, _records = build(tmp_path)
    return json.loads((out / "gate_batch.json").read_text(encoding="utf-8"))


def _gate_records(tmp_path):
    _out, records = build(tmp_path)
    return [r for r in records if r["invoice_id"] in set(gate_batch.GATE_BATCH_IDS)]


def _budget_inputs_for(tmp_path, department: str):
    records = _gate_records(tmp_path)
    by_id = {r["invoice_id"]: r for r in records if r["department"] == department}
    return [
        budget.BudgetInput(
            invoice_id=r["invoice_id"],
            issue_date=datetime.date.fromisoformat(r["issue_date"]),
            invoice_series=r["invoice_series"],
            invoice_number=r["invoice_number"],
            amount=Decimal(r["total_amount"]),
        )
        for r in (by_id[i] for i in gate_batch.GATE_BATCH_IDS if i in by_id)
    ]


def test_every_department_in_the_batch_declares_a_budget_chain(tmp_path):
    """M4-D's gate raised 9 BUDGET_BREACH flags; gate_batch.json declared 1.
    The other 8 were correct behaviour in departments the declaration never
    covered, so "exact agreement between the declaration and what the batch
    flags" was measuring one department and claiming all of them."""
    payload = _write_and_load(tmp_path)
    departments_in_batch = {r["department"] for r in _gate_records(tmp_path)}
    assert set(payload["balance_chains"]) == departments_in_batch


def test_the_declared_chains_match_domain_budget(tmp_path):
    """The declaration is arithmetic over the manifest, so it cannot drift from
    the corpus it describes — the same property `write_gate_batch` already had
    for MKT, extended to every department."""
    from decimal import Decimal

    from scripts.seed_reference import BUDGETS

    from invoice_agent.domain import budget as domain_budget

    payload = _write_and_load(tmp_path)
    limits = {dept: limit for dept, _period, _cat, limit in BUDGETS}

    for dept, chain in payload["balance_chains"].items():
        inputs = _budget_inputs_for(tmp_path, dept)
        expected = domain_budget.consume(limits[dept], Decimal("0.8"), inputs)
        assert [c["invoice_id"] for c in chain] == [c.invoice_id for c in expected]
        assert [Decimal(c["balance_after"]) for c in chain] == [
            c.balance_after for c in expected
        ]


