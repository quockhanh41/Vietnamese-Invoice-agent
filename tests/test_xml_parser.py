"""M1.4 — parse the 8 real invoices, and the cases the real corpus cannot cover.

The golden table below is TRANSCRIBED BY HAND from measurements taken
2026-07-27, never generated from the parser's own output. Generating it would
rebuild exactly the circularity this milestone exists to break (design §10.1).
"""

from decimal import Decimal
from pathlib import Path

import pytest

from invoice_agent.extraction import XmlExtractionError, parse_invoice_xml
from invoice_agent.extraction import xml_schema as sch

REAL = Path(__file__).parent / "fixtures" / "real_samples"
SYNTHETIC = Path(__file__).parent / "fixtures" / "synthetic"

# file, template_no, invoice_series, invoice_number, total_amount, item_kinds
GOLDEN = [
    ("dienluc_gtgt_cccd_2.1.0.xml", "1", "K26TAG", "3260056", "763044", [1]),
    ("hokinhdoanh_banhang_qrcode_2.1.0.xml", "2", "C26TLN", "23900", "90000", [1, 4]),
    ("ktx_banhang_cccd_2.1.0.xml", "2", "C25TCC", "2779", "3410000", [1]),
    ("lienket_gtgt_7dong_2.1.0.xml", "1", "C25TAC", "15547", "10994397", [1] * 7),
    ("newimage_gtgt_pban2.0.1.xml", "1", "C23TNI", "103049", "702000", [1]),
    ("phongvu_gtgt_tdiep_2.0.0.xml", "1", "C22TDN", "5227", "23780250.00000000", [1]),
    ("viettelpost_gtgt_2.1.0.xml", "1", "K26TAN", "2219110", "51599", [1]),
    ("vnpt_hoadon_banhang_2.1.0.xml", "2", "C25TKT", "78", "3500000", [1]),
]


def _parse(directory: Path, name: str):
    return parse_invoice_xml((directory / name).read_bytes())


@pytest.mark.parametrize(
    "name,template_no,series,number,total,item_kinds",
    GOLDEN,
    ids=[row[0] for row in GOLDEN],
)
def test_all_real_fixtures_parse(name, template_no, series, number, total, item_kinds):
    invoice = _parse(REAL, name)
    assert invoice.template_no == template_no
    assert invoice.invoice_series == series
    assert invoice.invoice_number == number
    assert invoice.total_amount == Decimal(total)
    assert [line.item_kind for line in invoice.lines] == item_kinds


def test_empty_element_is_none_not_zero():
    """`<SLuong />` in vnpt_ — 'not stated', not a quantity of zero."""
    invoice = _parse(REAL, "vnpt_hoadon_banhang_2.1.0.xml")
    assert invoice.lines[0].quantity is None


def test_note_line_has_no_money():
    """TChat=4 is a NOTE line: THHDVu only. Summing it is wrong, and requiring
    ThTien on it would crash. Found in hokinhdoanh_.
    """
    invoice = _parse(REAL, "hokinhdoanh_banhang_qrcode_2.1.0.xml")
    note = invoice.lines[1]
    assert note.item_kind == 4
    assert note.amount is None
    assert note.description is not None


def test_vnpt_amount_hidden_in_ext():
    """VNPT sets ThTien=0 and puts the real amount in TTKhac['Amount'].

    This is why ext bags are preserved verbatim: nothing in the regulation
    predicts it, and the naive rule sum(ThTien) misses 3.5M đồng here.
    """
    invoice = _parse(REAL, "vnpt_hoadon_banhang_2.1.0.xml")
    assert invoice.lines[0].amount == Decimal("0")
    assert invoice.lines[0].ext["Amount"] == "3500000"


def test_decimal_precision_preserved():
    """phongvu_ carries 8 decimal places. float would lose the last đồng."""
    invoice = _parse(REAL, "phongvu_gtgt_tdiep_2.0.0.xml")
    assert str(invoice.total_amount) == "23780250.00000000"


def test_words_amount_is_verbatim():
    """Two spaces after 'Ba', 'ngàn' not 'nghìn', './.' suffix. §10.3 uses this
    as a second independent encoding of the total, so M2.6's normalizer must
    face the real string — stripping it here would hide the test case.
    """
    invoice = _parse(REAL, "vnpt_hoadon_banhang_2.1.0.xml")
    assert invoice.total_in_words == "Ba  triệu năm trăm ngàn đồng./."


def test_ext_bag_is_order_independent():
    """Viettel emits DLieu,KDLieu,TTruong; VNPT emits TTruong,KDLieu,DLieu."""
    viettel = _parse(REAL, "viettelpost_gtgt_2.1.0.xml")
    vnpt = _parse(REAL, "vnpt_hoadon_banhang_2.1.0.xml")
    assert viettel.lines[0].ext["Tiền thuế dòng (Tiền thuế GTGT)"] == "3822"
    assert vnpt.lines[0].ext["VATAmount"] == "0"


def test_missing_buyer_is_not_an_error():
    """hokinhdoanh_ has no NMua/Ten at all. A validator demanding one would
    flag a real, valid, tax-authority-signed invoice.
    """
    invoice = _parse(REAL, "hokinhdoanh_banhang_qrcode_2.1.0.xml")
    assert invoice.buyer_name is None


def test_envelope_values_do_not_leak():
    """The './/' regression test. All three collision points at once, because
    any one alone could pass by luck — and in the REAL TDiep fixture all three
    coincide in value, so it cannot catch this bug at all (spec §2.3).
    """
    invoice = _parse(SYNTHETIC, "tdiep_envelope_distinct.xml")
    assert invoice.schema_version == "2.1.0"  # envelope says 9.9.9
    assert invoice.seller_tax_code == "0104093672"  # envelope says 9999999999
    assert invoice.lines[0].quantity == Decimal("3")  # envelope SLuong says 99


def test_multi_invoice_envelope_raises():
    with pytest.raises(XmlExtractionError, match="2 HDon"):
        _parse(SYNTHETIC, "tdiep_two_invoices.xml")


BILLION_LAUGHS = (
    b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY e0 "AAAA">'
    b'<!ENTITY e1 "&e0;&e0;"><!ENTITY e2 "&e1;&e1;">]><r>&e2;</r>'
)
XXE = (
    b'<?xml version="1.0"?><!DOCTYPE r ['
    b'<!ENTITY x SYSTEM "file:///etc/hostname">]><r>&x;</r>'
)


@pytest.mark.parametrize("payload", [BILLION_LAUGHS, XXE], ids=["billion-laughs", "xxe"])
def test_entity_declarations_rejected(payload):
    """Vendor XML is untrusted. Measured 2026-07-27 on Python 3.13.5: stdlib
    ElementTree blocks external entities but EXPANDS internal ones — 11 nested
    levels produced 4096 chars, so the real payload is a memory DoS. defusedxml
    raises EntitiesForbidden for both; we wrap it as XmlExtractionError so the
    graph routes it like any other unreadable document.
    """
    with pytest.raises(XmlExtractionError):
        parse_invoice_xml(payload)


def test_malformed_xml_raises_extraction_error():
    with pytest.raises(XmlExtractionError):
        parse_invoice_xml(b"<HDon><unclosed></HDon>")


def test_missing_required_field_raises():
    with pytest.raises(XmlExtractionError):
        parse_invoice_xml(b"<HDon><DLHDon><TTChung/></DLHDon></HDon>")


# --- Sufficiency test ---------------------------------------------------------


def _total_from_model(invoice) -> Decimal:
    """The M1.2 validation rule, computed FROM InvoiceData rather than from XML.

    Not the validator — that is M2.6's domain/validation.py. This exists only to
    prove the model carries every input the rule needs: item_kind, the line
    amount, the ext['Amount'] fallback, and vat_amount.
    """
    total = Decimal("0")
    for line in invoice.lines:
        # TChat=4 is a NOTE line with no money. Summing it is wrong.
        if line.item_kind != sch.ITEM_KIND_GOODS:
            continue
        amount = line.amount
        if not amount:
            # VNPT sets ThTien=0 and hides the real amount here. Match the key
            # EXACTLY: four other vendors carry
            # 'Thành tiền thanh toán của hàng hóa', which is the GROSS, so a
            # loose match would double-count tax on 4/8 files.
            raw = line.ext.get(sch.EXT_KEY_LINE_AMOUNT)
            amount = Decimal(raw) if raw else Decimal("0")
        total += amount
    # On a VAT invoice ThTien is the amount BEFORE tax; absent means no VAT.
    return total + (invoice.vat_amount or Decimal("0"))


@pytest.mark.parametrize("name", [row[0] for row in GOLDEN])
def test_invoice_data_is_sufficient_for_the_measured_rule(name):
    """8/8 — the same result measured on raw XML on 2026-07-27.

    If this fails, InvoiceData is missing a field that M2.6 will need.
    """
    invoice = _parse(REAL, name)
    assert _total_from_model(invoice) == invoice.total_amount


def test_the_naive_rule_would_be_wrong():
    """The negative control. Without it, the test above could pass for the
    wrong reason and we would never learn that the naive rule is the trap.

    Measured 2026-07-27: sum(ThTien) alone mismatches the stated total on 6 of
    8 real, valid, tax-authority-signed invoices — 5 because ThTien excludes VAT
    (dienluc_, lienket_, newimage_, phongvu_, viettelpost_) and 1 because VNPT
    parks the amount in TTKhac. Only hokinhdoanh_ and ktx_ match, both non-VAT.
    Every mismatch would be a false positive on a clean invoice, i.e. the
    headline metric `flag false-positive rate <= 5%`.
    """
    mismatching = 0
    for name, *_ in GOLDEN:
        invoice = _parse(REAL, name)
        naive = sum((line.amount or Decimal("0") for line in invoice.lines), Decimal("0"))
        if naive != invoice.total_amount:
            mismatching += 1
    assert mismatching == 6
