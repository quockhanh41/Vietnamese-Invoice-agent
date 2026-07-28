"""The frozen tax-code pool.

The generator contains NO checksum algorithm. Measured 2026-07-28: random
10-digit strings pass the real checksum only 9.6% of the time, so inventing
codes would flag ~90% of the clean corpus. And all 9 alternative check digits
of a valid code are invalid, so the defect group needs no algorithm either.
"""

import random

from invoice_agent.domain import taxcode  # TEST-ONLY: the independent checker
from scripts.generator import vendors


def test_pool_is_large_enough_for_vendor_history():
    """Price-outlier needs vendors with >=5 invoices; new-vendor needs vendors
    with none. Too small a pool and neither group can be seeded."""
    assert len(vendors.VALID_MSTS) >= 40


def test_every_pooled_code_passes_the_independent_checker():
    """Cross-check across the boundary: the pool was frozen by a one-off script,
    and `domain.taxcode` was written separately. Agreement is evidence."""
    bad = [m for m in vendors.VALID_MSTS if taxcode.check_tax_code(m) is not None]
    assert not bad, f"pool contains codes the validator rejects: {bad}"


def test_pool_contains_all_three_real_shapes():
    """Measured on the 8 fixtures: 10-digit, 10-digit + 3-digit branch, and
    12-digit household. A CHAR(10) assumption rejects two of three."""
    plain = [m for m in vendors.VALID_MSTS if len(m) == 10 and m.isdigit()]
    branch = [m for m in vendors.VALID_MSTS if "-" in m]
    household = [m for m in vendors.VALID_MSTS if len(m) == 12 and m.isdigit()]
    assert plain and branch and household


def test_invalidate_checksum_always_produces_an_invalid_code():
    """Guaranteed by construction: the check digit is uniquely determined by
    the first nine, so changing it cannot stay valid."""
    for mst in vendors.VALID_MSTS:
        if "-" in mst or len(mst) == 12:
            continue  # branch suffix and household codes have no check digit to break
        broken = vendors.invalidate_checksum(mst)
        assert broken != mst
        assert taxcode.check_tax_code(broken) is not None


def test_build_vendors_is_deterministic():
    a = vendors.build_vendors(random.Random(42), 10)
    b = vendors.build_vendors(random.Random(42), 10)
    assert [v.tax_code for v in a] == [v.tax_code for v in b]
    assert [v.name for v in a] == [v.name for v in b]


def test_both_ext_orders_are_represented():
    """MUTATION GUARD. Measured 2026-07-28: Viettel emits TTKhac children as
    DLieu,KDLieu,TTruong while VNPT emits TTruong,KDLieu,DLieu. A parser reading
    by POSITION is wrong on half the corpus — but only if the corpus contains
    both orders."""
    built = vendors.build_vendors(random.Random(42), 40)
    assert {v.ext_order for v in built} == {"dlieu_first", "ttruong_first"}
