"""Assembly: ratios, determinism, and the round-trip that is the M2 gate."""

import collections
import hashlib

from scripts import generate_invoices
from scripts.generator import manifest

from invoice_agent.extraction.xml_parser import parse_invoice_xml  # TEST-ONLY


def build(tmp_path, count=300, seed=42):
    out = tmp_path / "corpus"
    generate_invoices.generate(count=count, seed=seed, out=out)
    return out, manifest.read_manifest(out / "manifest.jsonl")


def test_manifest_accounts_for_every_entry(tmp_path):
    """285 XML + 15 PDF-only = 300. Spec §9 criterion 2: the gate's phrase
    'round-trip 300/300' means 300 ACCOUNTED FOR, not 300 files parsed —
    PDF-only entries carry no XML by design."""
    out, records = build(tmp_path)
    assert len(records) == 300
    with_xml = [r for r in records if r["source_type"] == "xml"]
    pdf_only = [r for r in records if r["source_type"] == "pdf_vision"]
    assert len(with_xml) + len(pdf_only) == 300
    assert len(pdf_only) == 15
    assert all(r["path"] is not None and r["path"].endswith(".pdf") for r in pdf_only)


def test_every_generated_xml_parses(tmp_path):
    """**THE M2 GATE.** Every XML file the generator wrote must parse into
    InvoiceData — including the defective ones, which are readable documents
    carrying wrong data (§3.3)."""
    out, records = build(tmp_path)
    failures = []
    for record in records:
        if record["source_type"] != "xml":
            continue
        path = out.parent / record["path"]
        try:
            parse_invoice_xml(path.read_bytes())
        except Exception as exc:  # noqa: BLE001 - we want the whole list, not the first
            failures.append(f"{path.name}: {exc}")
    assert not failures, failures


def test_defect_ratios_match_the_design_doc(tmp_path):
    """§10.2 counts at count=300. Exact, not approximate — the corpus is
    deterministic, so 'about right' would hide a broken allocator."""
    _, records = build(tmp_path)
    counts = collections.Counter(d for r in records for d in r["defects"])
    assert counts["arithmetic_error"] == 18
    assert counts["words_digits_mismatch"] == 6
    assert counts["bad_tax_code"] == 12
    assert counts["wrong_vat_rate"] == 9
    assert counts["exact_duplicate"] == 9
    assert counts["near_duplicate"] == 9
    assert counts["new_vendor"] == 9
    assert counts["budget_overrun"] == 12
    assert counts["price_outlier"] == 6
    assert counts["prompt_injection"] == 6
    assert counts["pdf_only"] == 12
    assert counts["unreadable_pdf"] == 3
    assert counts["ambiguous_category"] == 9


def test_defects_is_a_list_and_some_invoices_carry_several(tmp_path):
    """Item 2.2: a one-label manifest makes multi-flag eval impossible."""
    _, records = build(tmp_path)
    assert all(isinstance(r["defects"], list) for r in records)
    assert any(len(r["defects"]) > 1 for r in records)


def test_same_seed_produces_identical_bytes(tmp_path):
    """Without this the eval is not reproducible, and no before/after
    comparison in M6 means anything."""
    def digest(root):
        files = sorted(root.rglob("*"))
        h = hashlib.sha256()
        for f in files:
            if f.is_file():
                h.update(f.name.encode())
                h.update(f.read_bytes())
        return h.hexdigest()

    a, _ = build(tmp_path / "a")
    b, _ = build(tmp_path / "b")
    assert digest(a) == digest(b)


def test_clean_invoices_produce_no_findings(tmp_path):
    """Spec §8.1 — read that section before "fixing" a failure here.

    This is a genuine cross-check: the generator was written from the
    regulation and the real files, not from domain/. But when it fails the
    reflex is to change the generator, and TWICE ALREADY the validator was what
    was wrong (M1's naive rule 6/8; M2-B's unconditioned guards 3/8).
    THE 8 REAL FIXTURES ARBITRATE.
    """
    from invoice_agent.domain import validation  # TEST-ONLY

    out, records = build(tmp_path)
    noisy = []
    for record in records:
        if record["defects"] or record["path"] is None:
            continue
        invoice = parse_invoice_xml((out.parent / record["path"]).read_bytes())
        found = [f.code for f in validation.validate(invoice)]
        if found:
            noisy.append(f"{record['invoice_id']}: {found}")
    assert not noisy, f"clean invoices produced findings (false positives): {noisy[:5]}"


# Codes `domain.validation.validate()` can decide from ONE invoice, with no
# database. The rest (duplicates, budget, vendor stats) need corpus-wide or DB
# state and belong to M4's tests, not here.
LOCALLY_DECIDABLE = frozenset(
    {
        "ARITHMETIC_MISMATCH",
        "WORDS_DIGITS_MISMATCH",
        "TAX_CODE_CHECKSUM_INVALID",
        "LINE_VAT_MISMATCH",
    }
)


def test_no_invoice_carries_an_UNDECLARED_finding(tmp_path):
    """Every locally-decidable finding the validator raises must be declared in
    `expected_finding_codes` — the corpus's own labels must describe the corpus.

    THE ANTI-CIRCULARITY PAYOFF, and the guard that was missing. The generator
    imports neither `xml_schema` nor `domain/` (enforced by
    test_generator_independence), so agreement between what it SEEDS and what
    `validate()` FINDS is evidence rather than a tautology. Disagreement in this
    direction is a mislabelled corpus, and at M6 the eval scores predictions
    against these labels — an undeclared true positive is counted as a false
    positive and UNDERSTATES precision.

    Found 2026-07-29 by the M3 exit audit, which measured 6 such invoices:
      * 4 `price_outlier` rows scaled the line amount 10x but not the line's
        TThue, turning a price outlier into an undeclared VAT error;
      * 2 duplicates inherited `wrong_vat_rate` from the invoice they copied
        while declaring only their duplicate marker.
    """
    from invoice_agent.domain import validation  # TEST-ONLY

    out, records = build(tmp_path)
    undeclared = []
    for record in records:
        if record["source_type"] != "xml":
            continue
        data = parse_invoice_xml((out / f"{record['invoice_id']}.xml").read_bytes())
        raised = {f.code for f in validation.validate(data)} & LOCALLY_DECIDABLE
        declared = set(record["expected_finding_codes"])
        if raised - declared:
            undeclared.append(
                f"{record['invoice_id']} defects={record['defects']} "
                f"raised={sorted(raised - declared)}"
            )

    assert not undeclared, "corpus rows carry findings their manifest does not declare:\n" + "\n".join(
        undeclared
    )


def test_every_declared_local_finding_is_actually_raised(tmp_path):
    """The other direction: a label with nothing behind it would inflate recall.

    Together with the test above this pins the corpus's labels to its contents
    in both directions, which is what makes M6's numbers mean anything.
    """
    from invoice_agent.domain import validation  # TEST-ONLY

    out, records = build(tmp_path)
    missing = []
    for record in records:
        if record["source_type"] != "xml":
            continue
        declared = set(record["expected_finding_codes"]) & LOCALLY_DECIDABLE
        if not declared:
            continue
        data = parse_invoice_xml((out / f"{record['invoice_id']}.xml").read_bytes())
        raised = {f.code for f in validation.validate(data)}
        if declared - raised:
            missing.append(f"{record['invoice_id']} declared={sorted(declared - raised)}")

    assert not missing, "manifest declares findings the validator does not raise:\n" + "\n".join(
        missing
    )
