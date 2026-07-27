"""Single source of truth for QĐ-1450 element paths — written against real files.

THE ORDERING RULE (design doc §10.1, plan M1.3): this module is written against
`tests/fixtures/real_samples/` FIRST. The Jinja generator at M2.1 is written
AFTER and must NOT import or read this module. If both rested on one
understanding of the format, a 100% parser score would prove only that the
generator and the parser agree with each other, and the eval report's
`false-positive rate 0%` would be an invented number.

That risk is not hypothetical. The structure table written from memory at M1.2
was wrong in four places, and the naive rule `sum(ThTien) == TgTTTBSo` flags
4 of 5 real, valid, tax-authority-signed invoices as arithmetic errors.

EVERY PATH HERE IS RELATIVE TO `HDon` AND CONTAINS NO `.//`. Measured
2026-07-27: `phongvu_gtgt_tdiep_2.0.0.xml` has root `TDiep`, whose `TTChung`
envelope carries its own `PBan`, its own `MST` (the transmitter's, not the
seller's) and its own `SLuong` — the COUNT OF INVOICES in the envelope. So
`.//SLuong` finds that count before any line quantity, and `.//MST` finds the
transmitter before the seller. `.//` is used only to locate `HDon` itself.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import Element


class XmlExtractionError(Exception):
    """We could not READ the document.

    Deliberately says nothing about whether the data is CORRECT. Arithmetic,
    MST checksums and VAT rates belong to M2.6's `validate` node. Design doc
    §3.3 routes on exactly this distinction: an EXTRACTION error escalates the
    strategy (XML -> vision -> manual_queue), a DATA error travels onward as a
    flag for a human to judge. Keeping the parser incapable of producing the
    second kind puts that split on the right side of the module boundary.

    Lives here rather than in xml_parser.py because the coercers below raise
    it, and xml_parser imports this module — the other direction would be a
    circular import.
    """


# --- Root shapes --------------------------------------------------------------
# Both are real. 7/8 fixtures have root `HDon`; `phongvu_…` is wrapped in a
# `TDiep` transport envelope. A parser hard-coded to one shape fails half the
# corpus outright.
HDON_TAG = "HDon"
TDIEP_TAG = "TDiep"
TDIEP_HDON = "DLieu/HDon"

# --- Scopes, relative to HDon -------------------------------------------------
TTCHUNG = "DLHDon/TTChung"
NDHDON = "DLHDon/NDHDon"
SELLER = f"{NDHDON}/NBan"
BUYER = f"{NDHDON}/NMua"
LINES = f"{NDHDON}/DSHHDVu/HHDVu"
TOTALS = f"{NDHDON}/TToan"

# --- TTChung (header) ---------------------------------------------------------
SCHEMA_VERSION = "PBan"  # 2.0.0, 2.0.1 and 2.1.0 all present in the corpus
# KHMSHDon is `mẫu số` and doubles as the ONLY trustworthy invoice-kind signal.
# THDon is free-text garbage: 6 variants observed, including 'TT78 AC'.
TEMPLATE_NO = "KHMSHDon"
INVOICE_SERIES = "KHHDon"
INVOICE_NUMBER = "SHDon"
ISSUE_DATE = "NLap"
CURRENCY = "DVTTe"
EXCHANGE_RATE = "TGia"  # '1' and '1.00' both occur

# --- Party --------------------------------------------------------------------
PARTY_NAME = "Ten"
PARTY_TAX_CODE = "MST"
PARTY_ADDRESS = "DChi"

# --- Line ---------------------------------------------------------------------
LINE_NO = "STT"
LINE_KIND = "TChat"
LINE_DESC = "THHDVu"
LINE_UNIT = "DVTinh"
LINE_QTY = "SLuong"
LINE_PRICE = "DGia"
LINE_AMOUNT = "ThTien"
LINE_VAT_RATE = "TSuat"
LINE_VAT_AMOUNT = "TThue"

# --- Totals -------------------------------------------------------------------
SUBTOTAL = "TgTCThue"
VAT_TOTAL = "TgTThue"
GRAND_TOTAL = "TgTTTBSo"
TOTAL_IN_WORDS = "TgTTTBChu"

# --- TChat / KHMSHDon values --------------------------------------------------
# TChat 4 is a NOTE line: THHDVu only, no money. Summing it is wrong, and
# requiring ThTien on it crashes. Found in `hokinhdoanh_…`.
ITEM_KIND_GOODS = 1
ITEM_KIND_NOTE = 4
TEMPLATE_NO_VAT = "1"
TEMPLATE_NO_SALES = "2"

# --- TTKhac (vendor extensions) ----------------------------------------------
EXT_NODE = "TTKhac"
EXT_ENTRY = "TTin"
EXT_KEY = "TTruong"
EXT_VALUE = "DLieu"

# Six header-level TTKhac scopes observed across the 8 fixtures (measured
# 2026-07-27, spec §2.6). Name -> path relative to HDon.
EXT_SCOPES: dict[str, str] = {
    "dlhdon": f"DLHDon/{EXT_NODE}",
    "ttchung": f"{TTCHUNG}/{EXT_NODE}",
    "seller": f"{SELLER}/{EXT_NODE}",
    "buyer": f"{BUYER}/{EXT_NODE}",
    "ndhdon": f"{NDHDON}/{EXT_NODE}",
    "totals": f"{TOTALS}/{EXT_NODE}",
}

# Measured 2026-07-27 on all 8 fixtures: ONLY `vnpt_…` uses this exact key, and
# there it holds the real line amount because `ThTien` is 0. Four other vendors
# carry a similar-looking key, `'Thành tiền thanh toán của hàng hóa'`, whose
# value is the GROSS (lienket_ line 1: 1720144 == ThTien 1592726 + TThue
# 127418; newimage_: 702000 == 650000 + 52000; viettelpost_: 51599 ==
# 47777 + 3822). Matching loosely — "any amount-shaped TTKhac key" — would
# double-count tax on 4/8 files. MATCH THIS KEY EXACTLY.
EXT_KEY_LINE_AMOUNT = "Amount"


# --- Coercers ----------------------------------------------------------------
# Every one takes `where` purely so a failure names the offending element.
# "TSuat: unsupported value 'KCT'" is actionable; "invalid input" is not.

# `TSuat` is a percent STRING. Anchored and fully explicit: a bare '8' must NOT
# parse, because we cannot tell whether a vendor meant 8% or 800%.
_PERCENT = re.compile(r"^(\d+(?:\.\d+)?)%$")


def required_text(node: Element, path: str) -> str:
    """Text that must be present AND non-empty, else the document is unreadable."""
    value = node.findtext(path)
    if value is None or value == "":
        raise XmlExtractionError(f"{path}: required element missing or empty")
    return value


def money(raw: str | None, *, where: str) -> Decimal | None:
    """Any monetary or quantity value. `Decimal` only — never `float`.

    Empty and absent both return None, NEVER Decimal(0): `<SLuong />` in
    `vnpt_…` means "not stated", which is a different fact from a quantity of
    zero, and M2.6 needs to tell them apart.
    """
    if raw is None or raw == "":
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise XmlExtractionError(f"{where}: not a number: {raw!r}") from exc
    # Decimal('NaN') and Decimal('Infinity') SUCCEED. Without this check they
    # would flow into a money field and poison every downstream comparison.
    if not value.is_finite():
        raise XmlExtractionError(f"{where}: not a finite number: {raw!r}")
    return value


def integer(raw: str | None, *, where: str) -> int | None:
    """`STT` and `TChat`. Absent stays absent — see InvoiceLine.item_kind."""
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise XmlExtractionError(f"{where}: not an integer: {raw!r}") from exc


def vat_rate(raw: str | None, *, where: str) -> Decimal | None:
    """`'8%'` -> `Decimal('0.08')`. Absent -> None.

    Absent is normal, not an error: `TSuat` is missing from every non-VAT
    invoice (KHMSHDon=2).

    Anything else raises. `KCT` (không chịu thuế) and `KKKNT` are believed to
    occur in real invoices but appear in NONE of the 8 fixtures, so we refuse
    to invent a rate for them: the first such invoice fails loudly and routes
    to vision/manual_queue instead of silently carrying a wrong tax rate.
    Revisit when a fixture proves the shape (spec §9).
    """
    if raw is None or raw == "":
        return None
    matched = _PERCENT.match(raw)
    if matched is None:
        raise XmlExtractionError(
            f"{where}: unsupported TSuat {raw!r}; only 'N%' is observed in the corpus"
        )
    # scaleb(-2) keeps the two-decimal form: '10' -> 0.10, not 0.1.
    return Decimal(matched.group(1)).scaleb(-2)


def issue_date(raw: str | None, *, where: str) -> date:
    """`NLap`. ISO `YYYY-MM-DD` in 8/8 fixtures. Required."""
    if raw is None or raw == "":
        raise XmlExtractionError(f"{where}: required element missing or empty")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise XmlExtractionError(f"{where}: not an ISO date: {raw!r}") from exc


def ext_bag(scope: Element | None) -> dict[str, str]:
    """One `TTKhac` block -> `{TTruong: DLieu}`.

    Reads children BY TAG NAME. Never by position: measured 2026-07-27, Viettel
    emits `DLieu, KDLieu, TTruong` while VNPT emits `TTruong, KDLieu, DLieu`,
    so positional reads are wrong on half the corpus.

    Keys are verbatim — several carry real double spaces
    ('Loại giấy tờ  người mua'), and normalizing would collide keys between
    vendors. A missing or empty `DLieu` becomes '': within a bag, absence is
    already expressed by the key not being there. Duplicate `TTruong` is
    last-wins; the corpus has none, so that is a documented choice.
    """
    if scope is None:
        return {}
    bag: dict[str, str] = {}
    for entry in scope.findall(EXT_ENTRY):
        key = entry.findtext(EXT_KEY)
        if not key:
            continue
        bag[key] = entry.findtext(EXT_VALUE) or ""
    return bag


def locate_hdon(root: Element) -> Element:
    """Find the invoice element. Both root shapes are real.

    Measured 2026-07-27: 7/8 fixtures have root `HDon`; `phongvu_…` wraps it in
    a `TDiep` transport envelope. A parser hard-coded to either shape fails
    outright on the other — which is why the gate says "2–3 files", not "1".

    The count check is load-bearing, not decoration: `TDiep/TTChung/SLuong` IS
    the number of invoices in the envelope. Our fixture has SLuong=1, so N>1 is
    unobserved but structurally real, and silently taking the first `HDon`
    would drop the rest of a batch without a sound. Raising routes the document
    to vision/manual_queue via §3.3 instead of losing data quietly.
    """
    if root.tag == HDON_TAG:
        return root
    if root.tag == TDIEP_TAG:
        found = root.findall(TDIEP_HDON)
        if len(found) != 1:
            raise XmlExtractionError(
                f"TDiep envelope holds {len(found)} HDon; exactly 1 is supported"
            )
        return found[0]
    raise XmlExtractionError(f"unrecognised root element {root.tag!r}")
