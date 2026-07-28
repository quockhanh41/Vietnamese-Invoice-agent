"""Templates are written from tests/fixtures/real_samples/, never from
xml_schema.py. This test round-trips them through the parser — which is the
first moment the two independent sides meet."""

from datetime import date
from decimal import Decimal

import pytest

from invoice_agent.extraction.xml_parser import parse_invoice_xml  # TEST-ONLY
from scripts.generator import templates
from scripts.generator.templates import InvoiceSpec, LineSpec


def spec(**kw) -> InvoiceSpec:
    base = dict(
        root="hdon", schema_version="2.1.0", template_no="1",
        invoice_series="C26TAA", invoice_number="00000001", issue_date=date(2026, 7, 1),
        seller_name="CÔNG TY TNHH THƯƠNG MẠI MINH ANH", seller_tax_code="0100000003",
        seller_address="Số 1 Nguyễn Trãi, Hà Nội", buyer_name="CÔNG TY CP AN BÌNH",
        buyer_tax_code="", buyer_address="Số 2 Lê Lợi, Hà Nội",
        ext_order="dlieu_first", thousand="nghìn",
        lines=[LineSpec(line_no=1, item_kind=1, description="Giấy A4 Double A",
                        unit="Ram", quantity=Decimal("10"), unit_price=Decimal("100000"),
                        amount=Decimal("1000000"), vat_rate=Decimal("0.1"),
                        vat_amount=Decimal("100000"), ext_amount=None, self_closing=False)],
        subtotal=Decimal("1000000"), vat_total=Decimal("100000"),
        total_amount=Decimal("1100000"), total_in_words=None,
    )
    base.update(kw)
    return InvoiceSpec(**base)


@pytest.mark.parametrize("root", ["hdon", "tdiep"])
def test_both_root_shapes_parse(root):
    """M1 measured BOTH are real: 7/8 fixtures are root HDon, phongvu_ is
    wrapped in a TDiep transport envelope. A corpus with only one shape leaves
    half the parser untested."""
    invoice = parse_invoice_xml(templates.render(spec(root=root)).encode("utf-8"))
    assert invoice.invoice_number == "00000001"
    assert invoice.total_amount == Decimal("1100000")


def test_tdiep_envelope_carries_its_own_mst_and_count():
    """This is WHY `.//MST` and `.//SLuong` are wrong: the envelope's TTChung
    holds the transmitter's MST and the invoice COUNT. If the generator omitted
    them, the corpus could never catch a parser that used descendant search."""
    xml = templates.render(spec(root="tdiep"))
    envelope = xml[: xml.index("<DLieu>")]
    assert "<MST>" in envelope and "<SLuong>1</SLuong>" in envelope
    # ...and the parser still finds the seller, not the transmitter.
    assert parse_invoice_xml(xml.encode("utf-8")).seller_tax_code == "0100000003"


def test_generated_words_match_the_generated_total():
    invoice = parse_invoice_xml(templates.render(spec()).encode("utf-8"))
    from invoice_agent.domain.money import parse_amount_words  # TEST-ONLY

    assert parse_amount_words(invoice.total_in_words) == invoice.total_amount


@pytest.mark.parametrize("ext_order", ["dlieu_first", "ttruong_first"])
def test_both_ttkhac_child_orders_parse(ext_order):
    """MUTATION GUARD for the parser's ext_bag. Measured: Viettel emits
    DLieu,KDLieu,TTruong and VNPT emits TTruong,KDLieu,DLieu, so a positional
    reader is wrong on half the corpus."""
    xml = templates.render(spec(ext_order=ext_order))
    invoice = parse_invoice_xml(xml.encode("utf-8"))
    assert invoice.ext["ttchung"], "TTChung TTKhac bag did not survive the round trip"


def test_render_is_pure_text_and_deterministic():
    assert templates.render(spec()) == templates.render(spec())
