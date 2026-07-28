"""Vietnamese tax code (MST) validation.

THREE SHAPES ARE REAL, measured 2026-07-28 across the 8 fixtures:

    0100000003        10 digits, ordinary enterprise      -> checksum applies
    0300000018-042    10 digits + 3-digit branch suffix   -> checksum on the base
    050000000000      12 digits, household business       -> NO PUBLIC RULE

A CHAR(10) assumption rejects two of the three. Plan §M1.2 opened the question
of whether the 12-digit household form (derived from CCCD) has a checksum; no
public rule was found, so it is ACCEPTED UNCONDITIONALLY. A validator that
rejects it flags real, valid invoices, which is the metric §10.4 headlines.
"""

import re

from invoice_agent.domain import findings

# General Department of Taxation algorithm for the 10-digit form: weight the
# first nine digits, check digit = 10 - (sum mod 11). Verified 2026-07-28
# against all 7 checksummable fixture codes — 7/7 valid, which also confirms
# the anonymiser preserved real check digits.
_WEIGHTS = (31, 29, 23, 19, 17, 13, 7, 5, 3)

_TEN = re.compile(r"^\d{10}$")
_TEN_BRANCH = re.compile(r"^(\d{10})-\d{3}$")
_TWELVE = re.compile(r"^\d{12}$")


def normalise(mst: str | None) -> str | None:
    """Trim surrounding whitespace. The dash is significant and is kept."""
    if mst is None:
        return None
    cleaned = mst.strip()
    return cleaned or None


def is_valid_checksum(mst10: str) -> bool:
    total = sum(int(d) * w for d, w in zip(mst10[:9], _WEIGHTS, strict=True))
    return (10 - (total % 11)) == int(mst10[9])


def check_tax_code(mst: str | None, *, required: bool = False) -> findings.Finding | None:
    """None when the code is acceptable, a Finding when it is not.

    Absence is NOT a finding by default. Measured 8/8: buyer MST is missing in
    three forms — absent, '' and '0' — on every real invoice, and real garbage
    lives in these fields ('.', 'Người mua không lấy hóa đơn'). Only the seller
    code is passed with required=True.
    """
    cleaned = normalise(mst)
    if cleaned is None or cleaned == "0":
        return findings.make(findings.TAX_CODE_MISSING) if required else None

    if _TWELVE.match(cleaned):
        return None  # household business — no public checksum rule, see module docstring

    branch = _TEN_BRANCH.match(cleaned)
    base = branch.group(1) if branch else (cleaned if _TEN.match(cleaned) else None)
    if base is None:
        return findings.make(findings.TAX_CODE_SHAPE_UNRECOGNISED, tax_code=cleaned)
    if not is_valid_checksum(base):
        return findings.make(findings.TAX_CODE_CHECKSUM_INVALID, tax_code=cleaned)
    return None
