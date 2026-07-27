"""Unit tests for the QĐ-1450 path map and coercers (M1.3).

No Postgres, no fixtures loaded from disk except where a test says so.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from defusedxml.ElementTree import fromstring

from invoice_agent.extraction import xml_schema as sch
from invoice_agent.extraction.xml_schema import XmlExtractionError

SYNTHETIC = Path(__file__).parent / "fixtures" / "synthetic"
REAL = Path(__file__).parent / "fixtures" / "real_samples"


def test_no_field_path_uses_descendant_search():
    """`.//` anywhere in a FIELD path is a bug, not a style choice.

    Measured 2026-07-27: `phongvu_gtgt_tdiep_2.0.0.xml` has root `TDiep`, and
    `TDiep/TTChung` carries its own `PBan`, its own `MST` (the transmitter's,
    not the seller's) and its own `SLuong` — which is the COUNT OF INVOICES in
    the envelope, not a line quantity. `.//SLuong` finds that count first.

    This test is the cheap guard. `test_envelope_values_do_not_leak` in
    tests/test_xml_parser.py is the behavioural one.
    """
    paths = {
        name: value
        for name, value in vars(sch).items()
        if name.isupper() and isinstance(value, str)
    }
    offenders = {n: v for n, v in paths.items() if ".//" in v}
    assert offenders == {}, f"field paths must be anchored at HDon: {offenders}"

    scope_offenders = {n: v for n, v in sch.EXT_SCOPES.items() if ".//" in v}
    assert scope_offenders == {}


def test_ext_scopes_covers_the_six_observed_scopes():
    """Measured 2026-07-27: six distinct header-level TTKhac scopes across the
    8 real fixtures, plus one per line (handled separately on InvoiceLine).
    """
    assert set(sch.EXT_SCOPES) == {
        "dlhdon",
        "ttchung",
        "seller",
        "buyer",
        "ndhdon",
        "totals",
    }


# --- money -------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, ""])
def test_money_absent_or_empty_is_none_never_zero(raw):
    """`<SLuong />` in vnpt_ parses to '' — that is NOT the number zero.

    Three distinct 'missing' manifestations exist in the corpus: value '0',
    empty element '', absent element None. Collapsing empty to Decimal(0) would
    make a missing quantity indistinguishable from a genuine zero quantity.
    """
    assert sch.money(raw, where="probe") is None


def test_money_zero_is_zero():
    assert sch.money("0", where="probe") == Decimal("0")


def test_money_preserves_eight_decimal_places():
    """Real value from phongvu_. float would lose the last đồng."""
    assert str(sch.money("21618409.09000000", where="probe")) == "21618409.09000000"


@pytest.mark.parametrize("raw", ["abc", "1,5", "NaN", "Infinity"])
def test_money_rejects_non_finite_and_garbage(raw):
    """NaN and Infinity are the trap: Decimal('NaN') SUCCEEDS, so a plain
    try/except InvalidOperation would let them through into a money field.
    """
    with pytest.raises(XmlExtractionError):
        sch.money(raw, where="probe")


# --- vat_rate ----------------------------------------------------------------


def test_vat_rate_parses_percent_string():
    """TSuat is a STRING with a '%': '8%', '10%'. Not 0.08, not 8."""
    assert sch.vat_rate("8%", where="probe") == Decimal("0.08")
    assert sch.vat_rate("10%", where="probe") == Decimal("0.10")


@pytest.mark.parametrize("raw", [None, ""])
def test_vat_rate_absent_is_none(raw):
    """Absent on every non-VAT invoice (KHMSHDon=2). Not an error."""
    assert sch.vat_rate(raw, where="probe") is None


@pytest.mark.parametrize("raw", ["KCT", "KKKNT", "\\", "8"])
def test_vat_rate_rejects_unknown_codes_loudly(raw):
    """KCT / KKKNT are believed real but appear in NONE of the 8 fixtures.

    Raising means the first such invoice fails with a named reason and routes
    to vision/manual_queue, instead of being silently assigned a wrong tax
    rate. Guessing a semantic from memory is the mistake M1.2 already paid for.
    Revisit when a fixture proves the shape — see spec §9.
    """
    with pytest.raises(XmlExtractionError):
        sch.vat_rate(raw, where="probe")


# --- integer / issue_date / required_text -------------------------------------


def test_integer_and_date():
    assert sch.integer("7", where="probe") == 7
    assert sch.integer(None, where="probe") is None
    assert sch.integer("", where="probe") is None
    assert sch.issue_date("2026-06-30", where="probe") == date(2026, 6, 30)
    with pytest.raises(XmlExtractionError):
        sch.issue_date("30/06/2026", where="probe")
    with pytest.raises(XmlExtractionError):
        sch.issue_date(None, where="probe")


def test_required_text_rejects_absent_and_empty():
    node = fromstring(b"<a><present>x</present><empty/></a>")
    assert sch.required_text(node, "present") == "x"
    with pytest.raises(XmlExtractionError):
        sch.required_text(node, "empty")
    with pytest.raises(XmlExtractionError):
        sch.required_text(node, "absent")


# --- ext_bag -----------------------------------------------------------------


def test_ext_bag_reads_by_tag_not_position():
    """Child order REVERSES between vendors: Viettel emits
    `DLieu, KDLieu, TTruong`; VNPT emits `TTruong, KDLieu, DLieu`.
    Reading by position is broken on half the corpus.
    """
    viettel = fromstring(
        b"<TTKhac><TTin><DLieu>51599</DLieu><KDLieu>string</KDLieu>"
        b"<TTruong>Amount</TTruong></TTin></TTKhac>"
    )
    vnpt = fromstring(
        b"<TTKhac><TTin><TTruong>Amount</TTruong><KDLieu>string</KDLieu>"
        b"<DLieu>51599</DLieu></TTin></TTKhac>"
    )
    assert sch.ext_bag(viettel) == sch.ext_bag(vnpt) == {"Amount": "51599"}


def test_ext_bag_handles_absent_and_empty_scopes():
    """`ktx_…/TToan/TTKhac` is present with zero TTin children."""
    assert sch.ext_bag(None) == {}
    assert sch.ext_bag(fromstring(b"<TTKhac/>")) == {}


def test_ext_bag_keeps_keys_verbatim_and_empty_values_as_empty_string():
    """Keys carry real double spaces ('Loại giấy tờ  người mua'). Normalizing
    them would collide keys across vendors.
    """
    node = fromstring(
        b"<TTKhac>"
        b"<TTin><TTruong>Loai giay to  nguoi mua</TTruong><DLieu/></TTin>"
        b"<TTin><TTruong>Zero</TTruong><DLieu>0</DLieu></TTin>"
        b"<TTin><KDLieu>string</KDLieu></TTin>"
        b"</TTKhac>"
    )
    assert sch.ext_bag(node) == {"Loai giay to  nguoi mua": "", "Zero": "0"}


def test_ext_bag_duplicate_key_last_wins():
    """Zero duplicates in the corpus, so this is a documented CHOICE rather
    than a measurement. Pinned by a test so it cannot drift silently.
    """
    node = fromstring(
        b"<TTKhac>"
        b"<TTin><TTruong>K</TTruong><DLieu>first</DLieu></TTin>"
        b"<TTin><TTruong>K</TTruong><DLieu>second</DLieu></TTin>"
        b"</TTKhac>"
    )
    assert sch.ext_bag(node) == {"K": "second"}


# --- locate_hdon -------------------------------------------------------------


def test_locate_hdon_when_root_is_hdon():
    root = fromstring((REAL / "vnpt_hoadon_banhang_2.1.0.xml").read_bytes())
    assert sch.locate_hdon(root) is root


def test_locate_hdon_inside_tdiep_envelope():
    """7/8 fixtures have root HDon; phongvu_ is wrapped in a TDiep envelope.
    Both shapes are real, so the parser must LOCATE HDon, not assume it.
    """
    root = fromstring((REAL / "phongvu_gtgt_tdiep_2.0.0.xml").read_bytes())
    hdon = sch.locate_hdon(root)
    assert hdon.tag == "HDon"
    assert hdon is not root


def test_locate_hdon_raises_on_multi_invoice_envelope():
    """`TDiep/TTChung/SLuong` IS the invoice count in the envelope. Taking the
    first HDon would drop the rest of a batch silently. M1 raises instead;
    supporting envelopes is out of scope (spec §3).
    """
    root = fromstring((SYNTHETIC / "tdiep_two_invoices.xml").read_bytes())
    with pytest.raises(XmlExtractionError, match="2 HDon"):
        sch.locate_hdon(root)


def test_locate_hdon_raises_on_unrecognised_root():
    with pytest.raises(XmlExtractionError, match="root"):
        sch.locate_hdon(fromstring(b"<SomethingElse/>"))
