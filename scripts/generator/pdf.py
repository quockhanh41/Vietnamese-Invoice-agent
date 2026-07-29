"""Corpus PDFs — the input the vision branch (M3.4) has no other source for.

Renders from the generator's own `InvoiceSpec`, exactly as the Jinja templates
do. It must NOT import `xml_schema`, `xml_parser` or `domain/`:
`tests/test_generator_independence.py` enforces that, and the round-trip result
is evidence only while the two sides stay independent.

⚠️ The layout here is a SYNTHETIC approximation. The six real vendor PDFs were
untracked in Task 1 because they carry PII and cannot be anonymised the way an
XML tree can, so no committed file anchors this layout to reality (spec §1.3).
Any vision accuracy number measured against these PDFs is a number about THESE
PDFs.
"""

import datetime
import pathlib
from decimal import Decimal

from fpdf import FPDF

from scripts.generator.templates import InvoiceSpec

FONT_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "assets"
    / "fonts"
    / "DejaVuSans.ttf"
)

# fpdf2 stamps a wall-clock /CreationDate, which breaks M2-C's byte-stability
# property. Measured 2026-07-29 (spec §2.3): two unpinned renders 2.2s apart
# differ (D:20260729014047Z vs D:20260729014049Z); pinned, they are identical.
# The value is the roadmap's date and carries no meaning beyond being fixed.
PINNED_CREATION_DATE = datetime.datetime(2026, 7, 25, tzinfo=datetime.timezone.utc)

_FONT = "dejavu"


def _vnd(amount: Decimal) -> str:
    """1290107.52 -> '1.290.107' — the thousands separator every real invoice uses."""
    return f"{int(amount):,}".replace(",", ".")


def _new_document() -> FPDF:
    doc = FPDF()
    doc.add_font(_FONT, "", str(FONT_PATH))
    doc.set_font(_FONT, size=10)
    doc.set_creation_date(PINNED_CREATION_DATE)
    doc.add_page()
    return doc


def render_pdf(spec: InvoiceSpec) -> bytes:
    """One readable page, laid out like a Vietnamese VAT invoice."""
    doc = _new_document()

    doc.set_font(_FONT, size=14)
    doc.cell(text="HÓA ĐƠN GIÁ TRỊ GIA TĂNG", align="C", w=0)
    doc.ln(8)
    doc.set_font(_FONT, size=9)
    doc.cell(text=f"Ký hiệu: {spec.invoice_series}    Số: {spec.invoice_number}", w=0)
    doc.ln(5)
    doc.cell(text=f"Ngày lập: {spec.issue_date.strftime('%d/%m/%Y')}", w=0)
    doc.ln(8)

    doc.cell(text=f"Đơn vị bán hàng: {spec.seller_name}", w=0)
    doc.ln(5)
    doc.cell(text=f"Mã số thuế: {spec.seller_tax_code}", w=0)
    doc.ln(5)
    doc.cell(text=f"Địa chỉ: {spec.seller_address}", w=0)
    doc.ln(8)

    doc.cell(text=f"Họ tên người mua hàng: {spec.buyer_name}", w=0)
    doc.ln(5)
    doc.cell(text=f"Mã số thuế: {spec.buyer_tax_code}", w=0)
    doc.ln(5)
    doc.cell(text=f"Địa chỉ: {spec.buyer_address}", w=0)
    doc.ln(10)

    doc.cell(
        text=(
            "STT   Tên hàng hóa, dịch vụ                     ĐVT      SL      Đơn"
            " giá        Thành tiền"
        ),
        w=0,
    )
    doc.ln(6)
    for line in spec.lines:
        quantity = "" if line.quantity is None else f"{line.quantity:g}"
        unit_price = "" if line.unit_price is None else _vnd(line.unit_price)
        doc.cell(
            text=(
                f"{line.line_no:<5} {line.description:<42.42} {line.unit or '':<8} "
                f"{quantity:<7} {unit_price:>12}  {_vnd(line.amount):>13}"
            ),
            w=0,
        )
        doc.ln(5)

    doc.ln(4)
    if spec.subtotal is not None:
        doc.cell(text=f"Cộng tiền hàng: {_vnd(spec.subtotal)}", align="R", w=0)
        doc.ln(5)
    if spec.vat_total is not None:
        doc.cell(text=f"Tiền thuế GTGT: {_vnd(spec.vat_total)}", align="R", w=0)
        doc.ln(5)
    doc.set_font(_FONT, size=11)
    doc.cell(
        text=f"Tổng cộng tiền thanh toán: {_vnd(spec.total_amount)}", align="R", w=0
    )

    return bytes(doc.output())


def render_truncated(spec: InvoiceSpec) -> bytes:
    """A structurally broken file. Measured 2026-07-29 (spec §2.4): pypdfium2
    raises PdfiumError on this, so it NEVER reaches a model — the deterministic
    half of the `unreadable_pdf` group."""
    whole = render_pdf(spec)
    return whole[: int(len(whole) * 0.4)]


def render_blank() -> bytes:
    """Valid PDF, one empty page. Opens and rasterizes fine, so it DOES reach
    the model and fails on content — the half that exercises the retry loop."""
    return bytes(_new_document().output())


def render_degraded(spec: InvoiceSpec) -> bytes:
    """Valid, readable as a document, unusable as an invoice: the header
    survives, every number is replaced by the smudge a bad scan leaves."""
    doc = _new_document()
    doc.set_font(_FONT, size=14)
    doc.cell(text="HÓA ĐƠN GIÁ TRỊ GIA TĂNG", align="C", w=0)
    doc.ln(8)
    doc.set_font(_FONT, size=9)
    for _ in range(6):
        doc.cell(text="▓▓▓▓▓▓  ▓▓▓▓▓▓▓▓▓▓  ▓▓▓▓▓  ▓▓▓▓▓▓▓▓", w=0)
        doc.ln(5)
    doc.cell(text=f"Ký hiệu: {spec.invoice_series}", w=0)
    return bytes(doc.output())
