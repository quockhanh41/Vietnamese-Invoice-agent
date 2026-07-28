"""Synthetic vendors, drawn from a FROZEN pool of valid tax codes.

WHY A FROZEN POOL AND NOT AN ALGORITHM. The generator must not import
`domain.taxcode`, and writing the checksum a second time here would create two
implementations that could share one misreading — in which case their agreement
proves nothing. A frozen table means this module contains no checksum logic at
all, which is the strongest form of independence available.

PROVENANCE. Generated 2026-07-28 by a one-off script applying the General Department
of Taxation weights 31,29,23,19,17,13,7,5,3 with check digit 10 - (sum mod 11),
stepping the 9-digit base by the prime 7919 so the pool is spread rather than consecutive.
`tests/test_generator_vendors.py` cross-checks every entry against `domain.taxcode`,
which was written separately.

Measured 2026-07-28: random 10-digit strings pass that checksum only 9.6% of
the time, so a generator that invented codes would raise
TAX_CODE_CHECKSUM_INVALID on ~90% of its own clean corpus and destroy the
false-positive measurement before it was taken.
"""

from dataclasses import dataclass
from random import Random

# 40 ten-digit codes, frozen. See PROVENANCE above.
_TEN_DIGIT: tuple[str, ...] = (
    "0001000002", "0001079193", "0001158381", "0001316768",
    "0001395953", "0001475140", "0001554339", "0001633527",
    "0001712715", "0001791900", "0001871095", "0001950283",
    "0002029477", "0002108665", "0002187850", "0002267048",
    "0002346236", "0002425424", "0002504612", "0002583808",
    "0002662993", "0002742180", "0002821379", "0002900567",
    "0002979750", "0003058946", "0003138133", "0003217321",
    "0003296517", "0003375705", "0003454890", "0003534088",
    "0003613276", "0003692461", "0003850848", "0003930035",
    "0004009229", "0004088414", "0004167602", "0004246798",
)

# Branch form: a valid 10-digit base plus a 3-digit branch number. The suffix
# carries no checksum of its own — measured on `phongvu_` (0300000018-042) and
# `dienluc_` (0400100009-006).
_BRANCH: tuple[str, ...] = tuple(f"{m}-{i:03d}" for i, m in enumerate(_TEN_DIGIT[:4], start=6))

# Household business: 12 digits, derived from CCCD. NO PUBLIC CHECKSUM RULE
# exists (plan §M1.2 open question), so these are accepted unconditionally by
# the validator and need no construction rule here.
_HOUSEHOLD: tuple[str, ...] = ("050000000000", "051000000001", "052000000002", "053000000003")

VALID_MSTS: tuple[str, ...] = _TEN_DIGIT + _BRANCH + _HOUSEHOLD

# Vietnamese company-name fragments. Data the system ingests, so Vietnamese.
_PREFIX = ("CÔNG TY TNHH", "CÔNG TY CP", "DOANH NGHIỆP TƯ NHÂN", "HỘ KINH DOANH")
_TRADE = ("THƯƠNG MẠI", "DỊCH VỤ", "SẢN XUẤT", "VẬN TẢI", "CÔNG NGHỆ", "XÂY DỰNG")
_NAME = ("MINH ANH", "HOÀNG LONG", "PHÚ THÀNH", "AN BÌNH", "ĐẠI VIỆT", "TIẾN PHÁT",
         "NAM SƠN", "BẢO NGỌC", "THIÊN HÀ", "VẠN XUÂN")
_STREET = ("Nguyễn Trãi", "Lê Lợi", "Trần Hưng Đạo", "Hai Bà Trưng", "Quang Trung")
_CITY = ("Hà Nội", "TP. Hồ Chí Minh", "Đà Nẵng", "Hải Phòng", "Cần Thơ")


@dataclass(frozen=True)
class Vendor:
    tax_code: str
    name: str
    address: str
    # Which order this vendor writes TTKhac children in. Measured 2026-07-28:
    # Viettel emits DLieu,KDLieu,TTruong; VNPT emits TTruong,KDLieu,DLieu.
    # Reading that block by POSITION is wrong on half the real corpus, so both
    # orders must appear here or the trap is never exercised.
    ext_order: str


def build_vendors(rng: Random, count: int) -> list[Vendor]:
    """Deterministic vendor list. `rng` is passed in — never module-level."""
    vendors: list[Vendor] = []
    for index in range(count):
        tax_code = VALID_MSTS[index % len(VALID_MSTS)]
        name = f"{rng.choice(_PREFIX)} {rng.choice(_TRADE)} {rng.choice(_NAME)}"
        address = f"Số {rng.randint(1, 300)} {rng.choice(_STREET)}, {rng.choice(_CITY)}"
        # Alternate deterministically so both orders are guaranteed present
        # rather than left to chance.
        ext_order = "dlieu_first" if index % 2 == 0 else "ttruong_first"
        vendors.append(Vendor(tax_code, name, address, ext_order))
    return vendors


def invalidate_checksum(mst: str) -> str:
    """Break a valid 10-digit code by changing its check digit.

    No algorithm required: the check digit is uniquely determined by the first
    nine, so any other digit is invalid. Verified 2026-07-28 — all 9
    alternatives of a valid code fail the checker.
    """
    return mst[:9] + str((int(mst[9]) + 1) % 10)
