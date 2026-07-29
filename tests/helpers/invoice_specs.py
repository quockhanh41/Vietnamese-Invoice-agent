"""One realistic InvoiceSpec, shared by the PDF and vision tests.

Values are shaped after the real fixtures: a unit price that does NOT divide
evenly (M1 measured 3 × 398181.33 = 1194543.99 against a stated ThTien of
1194544), a dash-suffixed branch tax code, and diacritics in every free-text
field.
"""

from datetime import date
from decimal import Decimal

from scripts.generator.templates import InvoiceSpec, LineSpec


def make_spec() -> InvoiceSpec:
    return InvoiceSpec(
        root="hdon",
        schema_version="2.1.0",
        template_no="1",
        invoice_series="C26TAA",
        invoice_number="00000123",
        issue_date=date(2026, 7, 1),
        seller_name="CÔNG TY TNHH THƯƠNG MẠI DỊCH VỤ PHÚ QUÝ",
        seller_tax_code="0100000003",
        seller_address="Số 1 Đường Lê Duẩn, Quận Hoàn Kiếm, Hà Nội",
        buyer_name="NGUYỄN THỊ BÍCH NGỌC",
        buyer_tax_code="0300000018-042",
        buyer_address="Phường Bến Nghé, Quận 1, TP Hồ Chí Minh",
        ext_order="ttruong_first",
        thousand="nghìn",
        lines=[
            LineSpec(
                line_no=1,
                item_kind=1,
                description="Dịch vụ tư vấn quản trị doanh nghiệp",
                unit="Gói",
                quantity=Decimal("3"),
                unit_price=Decimal("398181.33"),
                amount=Decimal("1194544"),
                vat_rate=Decimal("0.08"),
                vat_amount=Decimal("95563.52"),
                ext_amount=None,
                self_closing=False,
            )
        ],
        subtotal=Decimal("1194544"),
        vat_total=Decimal("95563.52"),
        total_amount=Decimal("1290107.52"),
        total_in_words=None,
    )
