"""`category` must be a function of the invoice text, and `department` of the category.

Measured 2026-07-30, before this change: INV-0001, INV-0002 and INV-0003 carried
the IDENTICAL description "Chi phí quảng cáo trực tuyến tháng 07/2026" and were
labelled MARKETING, IT_SERVICES and OFFICE_SUPPLIES. The label was provably not a
function of the invoice. See spec 2026-07-31-m4a §1.1 D3.
"""

import collections

from scripts import generate_invoices
from scripts.generator import descriptions, manifest

from invoice_agent.extraction.xml_parser import parse_invoice_xml  # TEST-ONLY


def build(tmp_path, count=300, seed=42):
    out = tmp_path / "corpus"
    generate_invoices.generate(count=count, seed=seed, out=out)
    return out, manifest.read_manifest(out / "manifest.jsonl")


def _text_to_category() -> dict[str, str]:
    return {
        entry.text: category
        for category, pool in descriptions.CATEGORY_POOLS.items()
        for entry in pool
    }


def test_identical_descriptions_never_carry_different_categories(tmp_path):
    """The sharpest form of the defect: same text, three different labels."""
    out, records = build(tmp_path)
    by_text: dict[str, set[str]] = collections.defaultdict(set)
    for record in records:
        if record["source_type"] != "xml":
            continue
        invoice = parse_invoice_xml((out.parent / record["path"]).read_bytes())
        for line in invoice.lines:
            if line.description:
                by_text[line.description].add(record["category"])
    ambiguous = {t: c for t, c in by_text.items() if len(c) > 1}
    assert not ambiguous, f"same text, different labels: {list(ambiguous.items())[:3]}"


def test_pool_descriptions_carry_their_own_category(tmp_path):
    """Every invoice whose description came from a pool must be labelled with
    that pool's category. Draw from a different pool and this goes red."""
    out, records = build(tmp_path)
    lookup = _text_to_category()
    wrong = []
    for record in records:
        if record["source_type"] != "xml":
            continue
        invoice = parse_invoice_xml((out.parent / record["path"]).read_bytes())
        for line in invoice.lines:
            expected = lookup.get(line.description or "")
            if expected and expected != record["category"]:
                wrong.append(f"{record['invoice_id']}: {expected} != {record['category']}")
    assert not wrong, wrong[:5]


def test_injection_twins_share_a_category(tmp_path):
    """§9.3 negative control: the payload replaces the description, so the pair
    must take its label from the STRIPPED twin. If the label moved with the
    payload, a classification difference between twin and carrier would measure
    the label change rather than the injection."""
    _out, records = build(tmp_path)
    by_id = {r["invoice_id"]: r for r in records}
    pairs = [(by_id[r["twin_of"]], r) for r in records if r.get("twin_of") and r["twin_of"] in by_id]
    assert pairs, "no injection twins found — the control is not being exercised"
    for carrier, twin in pairs:
        assert carrier["category"] == twin["category"], (
            f"{carrier['invoice_id']} vs {twin['invoice_id']}"
        )


def test_department_is_derived_from_category(tmp_path):
    """Every department is either the category's primary or its declared
    secondary — nothing else. Budget-overrun invoices are pinned to MKT."""
    _out, records = build(tmp_path)
    for record in records:
        if "budget_overrun" in record["defects"]:
            assert record["department"] == "MKT"
            continue
        category = record["category"]
        allowed = {
            generate_invoices.CATEGORY_TO_DEPARTMENT[category],
            generate_invoices.SECONDARY_DEPARTMENT[category],
        }
        assert record["department"] in allowed, f"{record['invoice_id']}: {record['department']}"


def test_department_noise_is_present_and_bounded(tmp_path):
    """The noise rate IS M4-C's department accuracy ceiling. Zero noise makes
    department a free lunch; too much makes it unpredictable."""
    _out, records = build(tmp_path)
    eligible = [r for r in records if "budget_overrun" not in r["defects"]]
    secondary = [
        r for r in eligible
        if r["department"] == generate_invoices.SECONDARY_DEPARTMENT[r["category"]]
        and r["department"] != generate_invoices.CATEGORY_TO_DEPARTMENT[r["category"]]
    ]
    rate = len(secondary) / len(eligible)
    target = generate_invoices.DEPARTMENT_NOISE_RATE
    assert abs(rate - target) < 0.05, f"noise rate {rate:.3f}, declared {target}"


def test_ambiguous_invoices_take_the_primary_of_their_pair(tmp_path):
    """defects.AMBIGUOUS_DESCRIPTIONS already pairs each description with
    (primary, plausible_alternative). generate_invoices.py:313 discarded it."""
    from scripts.generator import defects

    out, records = build(tmp_path)
    primaries = {desc: pair[0] for desc, pair in defects.AMBIGUOUS_DESCRIPTIONS}
    checked = 0
    for record in records:
        if "ambiguous_category" not in record["defects"] or record["source_type"] != "xml":
            continue
        invoice = parse_invoice_xml((out.parent / record["path"]).read_bytes())
        expected = primaries.get(invoice.lines[0].description or "")
        if expected:
            checked += 1
            assert record["category"] == expected, record["invoice_id"]
    assert checked, "no ambiguous invoices checked — the group is not being exercised"
