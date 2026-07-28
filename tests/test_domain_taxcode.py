"""MST validation. Three real shapes; only one of them has a public checksum."""

import pytest

from invoice_agent.domain import findings, taxcode

# Read from tests/fixtures/real_samples/ on 2026-07-28. Already anonymised —
# and the anonymiser preserved valid check digits, which is why they can be
# used as ground truth here (spec §2.4).
REAL_TAX_CODES = [
    "0400100009-006", "0300000018", "0102000009-006",
    "0311110005", "0300000018-042", "0100000003",
]


@pytest.mark.parametrize("mst", REAL_TAX_CODES)
def test_real_tax_codes_pass_checksum(mst):
    """MUTATION GUARD. 7/7 checksummable fixture codes validate under the
    General Dept. of Taxation algorithm (weights 31,29,23,19,17,13,7,5,3).
    Alter a weight and this goes red."""
    assert taxcode.check_tax_code(mst) is None


def test_household_tax_code_accepted_without_checksum():
    """MUTATION GUARD, and the point of open question 2. The 12-digit household
    MST is derived from CCCD and has NO KNOWN PUBLIC CHECKSUM RULE. A validator
    that rejects it flags real invoices — the headline metric."""
    assert taxcode.check_tax_code("050000000000") is None


def test_bad_check_digit_is_flagged():
    bad = "0100000004"  # 0100000003 is valid; the last digit is the check digit
    result = taxcode.check_tax_code(bad)
    assert result is not None
    assert result.code == findings.TAX_CODE_CHECKSUM_INVALID


def test_branch_suffix_is_checked_on_the_base_only():
    """The 3-digit branch suffix has no checksum of its own; the 10-digit base
    carries it. '-999' is a legal branch number."""
    assert taxcode.check_tax_code("0100000003-999") is None


@pytest.mark.parametrize("mst", ["123", "01000000031234567", "abcdefghij", "0100-000003"])
def test_unrecognised_shapes_are_flagged(mst):
    result = taxcode.check_tax_code(mst)
    assert result is not None
    assert result.code == findings.TAX_CODE_SHAPE_UNRECOGNISED


@pytest.mark.parametrize("mst", [None, "", "   ", "0"])
def test_absent_buyer_tax_code_is_not_a_finding(mst):
    """Measured 8/8: buyer MST is absent, '' or '0' on every real invoice, and
    one file has no NMua/Ten at all. Flagging that would flag the whole corpus.
    Absence is only a finding when the caller says the field is required."""
    assert taxcode.check_tax_code(mst) is None


def test_absent_tax_code_is_flagged_when_required():
    result = taxcode.check_tax_code(None, required=True)
    assert result is not None
    assert result.code == findings.TAX_CODE_MISSING


def test_whitespace_is_tolerated():
    assert taxcode.check_tax_code("  0100000003  ") is None
