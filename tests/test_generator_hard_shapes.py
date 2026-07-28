"""One assertion per row of spec §5.1. These are the shapes that made M1's
measurements surprising; a corpus without them cannot exercise the guards they
motivated."""

from decimal import Decimal

from scripts.generator import defects, templates
from tests.test_generator_templates import spec

from invoice_agent.extraction.xml_parser import parse_invoice_xml  # TEST-ONLY


def parse(s):
    return parse_invoice_xml(templates.render(s).encode("utf-8"))


def test_note_line_carries_no_money():
    """`hokinhdoanh_`: TChat=4 is a NOTE line — THHDVu only. Summing it is
    wrong and requiring ThTien on it crashes."""
    invoice = parse(spec(lines=[
        templates.LineSpec(line_no=1, item_kind=1, description="Giấy A4", unit="Ram",
                           quantity=Decimal("1"), unit_price=Decimal("1000000"),
                           amount=Decimal("1000000"), vat_rate=Decimal("0.1"),
                           vat_amount=Decimal("100000"), ext_amount=None, self_closing=False),
        defects.make_note_line(2),
    ]))
    note = invoice.lines[1]
    assert note.item_kind == 4
    assert note.amount is None


def test_vnpt_shape_puts_the_amount_in_ttkhac():
    """`vnpt_`: ThTien=0, the real money in TTKhac['Amount'], and
    `<SLuong />` self-closing so it parses to None rather than 0."""
    invoice = parse(spec(
        lines=[defects.make_vnpt_line(1, Decimal("3500000"))],
        subtotal=None, vat_total=None, total_amount=Decimal("3500000"), template_no="2",
    ))
    line = invoice.lines[0]
    assert line.amount == Decimal("0")
    assert line.ext["Amount"] == "3500000"
    assert line.quantity is None, "<SLuong /> must parse to None, not 0"


def test_vat_rate_is_rendered_as_a_percent_string():
    invoice = parse(spec())
    assert invoice.lines[0].vat_rate == Decimal("0.10")


def test_non_vat_invoice_reports_zero_headers():
    """`ktx_banhang_` (KHMSHDon=2): TgTCThue and TgTThue are 0 while the line
    carries the full total. This is the shape that makes M2-B's subtotal guard
    necessary."""
    invoice = parse(defects.make_non_vat_spec(spec()))
    assert invoice.template_no == "2"
    assert invoice.subtotal in (None, Decimal("0"))


def test_header_only_vat_invoice_omits_line_vat():
    """`dienluc_`, `phongvu_`: TSuat present per line, TThue absent. This is the
    shape that makes M2-B's VAT guard necessary."""
    invoice = parse(defects.make_header_only_vat_spec(spec()))
    assert invoice.vat_amount is not None
    assert all(line.vat_amount is None for line in invoice.lines)


def test_odd_unit_price_survives_rendering():
    """`lienket_`: 3 x 398181.33 = 1194543.99 but ThTien = 1194544."""
    invoice = parse(spec(lines=[
        templates.LineSpec(line_no=1, item_kind=1, description="Dịch vụ tư vấn", unit="Gói",
                           quantity=Decimal("3"), unit_price=Decimal("398181.33"),
                           amount=Decimal("1194544"), vat_rate=None, vat_amount=None,
                           ext_amount=None, self_closing=False),
    ], subtotal=None, vat_total=None, total_amount=Decimal("1194544")))
    line = invoice.lines[0]
    assert line.quantity * line.unit_price != line.amount
    assert abs(line.quantity * line.unit_price - line.amount) <= Decimal("0.01")
