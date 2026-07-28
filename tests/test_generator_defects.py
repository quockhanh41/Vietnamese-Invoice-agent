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
