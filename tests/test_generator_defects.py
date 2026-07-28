"""Seeded defects must produce the finding the manifest predicts — no more,
no less. A defect that raises nothing is a corpus bug; one that raises three
findings makes recall unmeasurable."""

from decimal import Decimal

from invoice_agent.domain import findings, validation  # TEST-ONLY
from invoice_agent.extraction.xml_parser import parse_invoice_xml  # TEST-ONLY
from scripts.generator import defects, templates
from tests.test_generator_templates import spec


def codes_for(s) -> set[str]:
    invoice = parse_invoice_xml(templates.render(s).encode("utf-8"))
    return {f.code for f in validation.validate(invoice)}


def test_clean_spec_produces_no_findings():
    """The baseline. If this fails, every 'defect raises X' test below is
    measuring the baseline's noise instead of the defect."""
    assert codes_for(spec()) == set()


def test_arithmetic_error_raises_exactly_that():
    assert findings.ARITHMETIC_MISMATCH in codes_for(defects.seed_arithmetic_error(spec()))


def test_words_mismatch_raises_exactly_that():
    assert findings.WORDS_DIGITS_MISMATCH in codes_for(defects.seed_words_mismatch(spec()))


def test_bad_tax_code_raises_exactly_that():
    assert findings.TAX_CODE_CHECKSUM_INVALID in codes_for(defects.seed_bad_tax_code(spec()))


def test_wrong_vat_rate_raises_exactly_that():
    """The 3% group 'ghi 8% nhưng tính 10%': the stated rate and the charged
    tax disagree."""
    assert findings.LINE_VAT_MISMATCH in codes_for(defects.seed_wrong_vat_rate(spec()))


def test_seeded_defects_still_parse():
    """A DATA defect must remain READABLE. §3.3 splits extraction errors from
    data errors; a defect that breaks parsing would route to vision instead of
    to a human, testing the wrong branch entirely."""
    for seed in (defects.seed_arithmetic_error, defects.seed_words_mismatch,
                 defects.seed_bad_tax_code, defects.seed_wrong_vat_rate):
        parse_invoice_xml(templates.render(seed(spec())).encode("utf-8"))


import random


def test_exact_duplicate_shares_the_whole_natural_key():
    """3% group. §2.3's UNIQUE is (seller, template, series, number); an exact
    duplicate must repeat ALL FOUR or ON CONFLICT DO NOTHING never fires and
    the idempotency path goes untested."""
    original = spec()
    dup = defects.duplicate_of(original)
    assert (dup.seller_tax_code, dup.template_no, dup.invoice_series, dup.invoice_number) == (
        original.seller_tax_code, original.template_no,
        original.invoice_series, original.invoice_number,
    )


def test_near_duplicate_keeps_seller_and_total_but_changes_the_number():
    """3% group, the 'wow' case: a vendor re-issues an almost identical invoice
    under a new number to be paid twice. Same seller and total, different
    number — exactly what domain.anomaly.near_duplicate looks for."""
    original = spec()
    near = defects.near_duplicate_of(original, random.Random(7))
    assert near.seller_tax_code == original.seller_tax_code
    assert near.total_amount == original.total_amount
    assert near.invoice_number != original.invoice_number


def test_near_duplicate_lands_inside_the_detection_window():
    from invoice_agent.domain import anomaly  # TEST-ONLY

    original = spec()
    near = defects.near_duplicate_of(original, random.Random(7))
    delta = abs((near.issue_date - original.issue_date).days)
    assert delta <= anomaly.NEAR_DUP_WINDOW_DAYS, (
        "a near-duplicate outside the window is not a near-duplicate — the "
        "corpus would be seeding a case the detector cannot see"
    )


def test_ambiguous_descriptions_are_genuinely_two_sided():
    """3% group. These exist to make the Classifier and Auditor DISAGREE at
    M4 — the multi-agent evidence. A description that is obviously one category
    produces agreement and proves nothing."""
    assert len(defects.AMBIGUOUS_DESCRIPTIONS) >= 5
    for text, options in defects.AMBIGUOUS_DESCRIPTIONS:
        assert len(options) == 2, f"{text!r} must plausibly fit exactly two categories"
