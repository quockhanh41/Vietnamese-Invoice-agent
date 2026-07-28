"""The manifest is the eval ground truth. If it drifts from the domain
vocabulary, M6 measures against codes that can never be raised."""

import json
from decimal import Decimal

from invoice_agent.domain import findings  # TEST-ONLY: the cross-check
from scripts.generator import manifest


def record(**kw):
    base = dict(
        invoice_id="INV-0001", path="corpus/INV-0001.xml", source_type="xml",
        seller_tax_code="0100000003", template_no="1", invoice_series="C26TAA",
        invoice_number="00000001", issue_date="2026-07-01", department="MKT",
        category="MARKETING", total_amount=Decimal("1100"), defects=[],
        expected_finding_codes=[], twin_of=None, duplicate_of=None,
        canonical_budget_rank=None,
    )
    base.update(kw)
    return manifest.ManifestRecord(**base)


def test_manifest_codes_exist_in_domain_findings():
    """Cross-boundary check. The generator names findings as literal STRINGS
    and imports nothing from domain/; this test imports both sides and asserts
    they agree — the same pattern M2-B used against the DB CHECK constraint."""
    declared = {c for codes in manifest.DEFECT_CODES.values() for c in codes}
    unknown = declared - set(findings.CODE_REGISTRY)
    assert not unknown, f"manifest predicts findings that cannot be raised: {unknown}"


def test_defects_is_a_list_not_a_label(tmp_path):
    """Item 2.2. Real invoices arrive as 'new vendor AND over budget'; a
    one-label manifest makes multi-flag evaluation impossible (§10.2)."""
    path = tmp_path / "manifest.jsonl"
    manifest.write_manifest(path, [record(defects=["new_vendor", "budget_overrun"])])
    row = manifest.read_manifest(path)[0]
    assert row["defects"] == ["new_vendor", "budget_overrun"]


def test_decimals_survive_the_json_round_trip(tmp_path):
    """Amounts reach 8 decimal places (M1: 21618409.09000000). json.dumps
    cannot serialise Decimal, and float would silently round."""
    path = tmp_path / "manifest.jsonl"
    manifest.write_manifest(path, [record(total_amount=Decimal("21618409.09000000"))])
    assert manifest.read_manifest(path)[0]["total_amount"] == "21618409.09000000"


def test_one_json_object_per_line(tmp_path):
    path = tmp_path / "manifest.jsonl"
    manifest.write_manifest(path, [record(invoice_id="A"), record(invoice_id="B")])
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert [json.loads(x)["invoice_id"] for x in lines] == ["A", "B"]
