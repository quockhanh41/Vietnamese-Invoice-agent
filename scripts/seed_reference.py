"""Seed reference data — plan item 2.5.

Account numbers are real TT133/2016/TT-BTC codes, not invented ones. TT133
applies to small and medium enterprises, which matches the scenario (a
40-person trading company); TT200 is for large enterprises. Using real codes is
a credibility signal in a Vietnamese accounting-adjacent interview — design doc
§2.1 says so explicitly.

`BUDGETS` is imported by M2-C's generator. Do not hardcode these limits a
second time there: item 2.3 needs three Marketing invoices seeded against a
known limit so that under canonical order the THIRD one overruns, and two
copies of the limit is how that test starts passing for the wrong reason.
"""

from decimal import Decimal

from sqlalchemy import Connection

from invoice_agent.db.engine import get_owner_engine
from invoice_agent.db.repositories import reference

DEPARTMENTS: list[tuple[str, str]] = [
    ("MKT", "Phòng Marketing"),
    ("ADMIN", "Phòng Hành chính"),
    ("OPS", "Phòng Vận hành"),
    ("IT", "Phòng Công nghệ thông tin"),
    ("SALES", "Phòng Kinh doanh"),
]

# (code, name_vi, debit, vat, credit, keywords_vi)
# `1331` = thuế GTGT được khấu trừ, `331` = phải trả cho người bán.
# `6421` = chi phí bán hàng, `6422` = chi phí quản lý doanh nghiệp.
CATEGORIES: list[tuple[str, str, str, str | None, str, list[str]]] = [
    (
        "OFFICE_SUPPLIES",
        "Văn phòng phẩm",
        "6422",
        "1331",
        "331",
        ["văn phòng phẩm", "giấy a4", "bút", "mực in", "sổ", "ghim", "kẹp"],
    ),
    (
        "MARKETING",
        "Chi phí marketing",
        "6421",
        "1331",
        "331",
        ["quảng cáo", "marketing", "truyền thông", "sự kiện", "in ấn", "banner", "facebook ads"],
    ),
    (
        "IT_SERVICES",
        "Dịch vụ công nghệ thông tin",
        "6422",
        "1331",
        "331",
        ["phần mềm", "hosting", "tên miền", "máy chủ", "cloud", "bản quyền", "license"],
    ),
    (
        "LOGISTICS",
        "Chi phí vận chuyển",
        "6421",
        "1331",
        "331",
        ["vận chuyển", "giao hàng", "cước", "bưu điện", "chuyển phát", "logistics"],
    ),
    (
        "UTILITIES",
        "Điện nước, viễn thông",
        "6422",
        "1331",
        "331",
        ["tiền điện", "tiền nước", "cước viễn thông", "internet", "điện thoại"],
    ),
    (
        "GOODS",
        "Hàng hóa mua vào",
        "156",
        "1331",
        "331",
        ["hàng hóa", "nhập kho", "mua hàng", "sản phẩm"],
    ),
    (
        "TOOLS",
        "Công cụ, dụng cụ",
        "153",
        "1331",
        "331",
        ["công cụ", "dụng cụ", "thiết bị", "máy móc nhỏ"],
    ),
]

# (department_code, period, category, amount_limit)
# MKT's limit is deliberately tight: M2-C item 2.3 seeds three Marketing
# invoices against it so that in canonical order (issue_date, invoice_series,
# invoice_number) the THIRD overruns, while a shuffled arrival order would
# overrun a different one if reconciliation were not serial.
BUDGETS: list[tuple[str, str, str | None, Decimal]] = [
    ("MKT", "2026-07", None, Decimal("50000000")),
    ("ADMIN", "2026-07", None, Decimal("30000000")),
    ("OPS", "2026-07", None, Decimal("80000000")),
    ("IT", "2026-07", None, Decimal("60000000")),
    ("SALES", "2026-07", None, Decimal("40000000")),
]


def seed(conn: Connection) -> None:
    """Idempotent — every write is an upsert. `make seed` is safe to re-run."""
    for code, name_vi in DEPARTMENTS:
        reference.upsert_department(conn, code, name_vi)
    for code, name_vi, debit, vat, credit, keywords in CATEGORIES:
        reference.upsert_category(conn, code, name_vi, debit, vat, credit, keywords)
    for dept, period, category, limit in BUDGETS:
        reference.upsert_budget(conn, dept, period, category, limit)


def main() -> None:
    with get_owner_engine().begin() as conn:
        seed(conn)
    print("✅ reference data seeded")


if __name__ == "__main__":
    main()
