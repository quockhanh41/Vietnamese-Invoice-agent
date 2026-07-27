"""Deterministic QĐ-1450 XML -> InvoiceData (M1.4).

A pure function: same bytes in, byte-identical model out. That property is why
design doc §3.3 does NOT retry this node — retrying a deterministic parser three
times produces three identical results. Escalation goes XML -> vision instead.

The parser raises when it cannot READ the document, and never judges whether
the data is CORRECT. Arithmetic, MST checksums, VAT-rate consistency and
words-vs-digits all belong to M2.6's `validate` node. That boundary is what
makes §3.3's EXTRACTION-vs-DATA routing decidable: by construction, everything
raised here is an extraction error.
"""

from decimal import Decimal
from xml.etree.ElementTree import Element, ParseError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring

from invoice_agent.extraction import xml_schema as sch
from invoice_agent.extraction.models import InvoiceData, InvoiceLine
from invoice_agent.extraction.xml_schema import XmlExtractionError

__all__ = ["XmlExtractionError", "parse_invoice_xml"]


def parse_invoice_xml(raw: bytes) -> InvoiceData:
    """Read one invoice. Raises `XmlExtractionError` and nothing else."""
    root = _read(raw)
    hdon = sch.locate_hdon(root)
    header = _require_node(hdon, sch.TTCHUNG)
    totals = _require_node(hdon, sch.TOTALS)
    seller = hdon.find(sch.SELLER)
    buyer = hdon.find(sch.BUYER)

    lines = [
        _line(element, index)
        for index, element in enumerate(hdon.findall(sch.LINES), start=1)
    ]
    if not lines:
        raise XmlExtractionError(f"{sch.LINES}: no invoice lines found")

    return InvoiceData(
        schema_version=header.findtext(sch.SCHEMA_VERSION),
        template_no=sch.required_text(header, sch.TEMPLATE_NO),
        invoice_series=sch.required_text(header, sch.INVOICE_SERIES),
        invoice_number=sch.required_text(header, sch.INVOICE_NUMBER),
        issue_date=sch.issue_date(
            header.findtext(sch.ISSUE_DATE), where=f"{sch.TTCHUNG}/{sch.ISSUE_DATE}"
        ),
        currency=header.findtext(sch.CURRENCY),
        exchange_rate=sch.money(
            header.findtext(sch.EXCHANGE_RATE), where=f"{sch.TTCHUNG}/{sch.EXCHANGE_RATE}"
        ),
        seller_name=_party(seller, sch.PARTY_NAME),
        seller_tax_code=_party(seller, sch.PARTY_TAX_CODE),
        seller_address=_party(seller, sch.PARTY_ADDRESS),
        buyer_name=_party(buyer, sch.PARTY_NAME),
        buyer_tax_code=_party(buyer, sch.PARTY_TAX_CODE),
        buyer_address=_party(buyer, sch.PARTY_ADDRESS),
        subtotal=sch.money(
            totals.findtext(sch.SUBTOTAL), where=f"{sch.TOTALS}/{sch.SUBTOTAL}"
        ),
        vat_amount=sch.money(
            totals.findtext(sch.VAT_TOTAL), where=f"{sch.TOTALS}/{sch.VAT_TOTAL}"
        ),
        total_amount=_grand_total(totals),
        total_in_words=totals.findtext(sch.TOTAL_IN_WORDS),
        lines=lines,
        ext={
            name: bag
            for name, path in sch.EXT_SCOPES.items()
            if (bag := sch.ext_bag(hdon.find(path)))
        },
    )


def _read(raw: bytes) -> Element:
    """Parse untrusted bytes. Every failure becomes XmlExtractionError.

    `DefusedXmlException` is the base of EntitiesForbidden / DTDForbidden /
    ExternalReferenceForbidden, so catching it covers all three defences at
    once. Verified 2026-07-27: both a billion-laughs payload and an XXE payload
    arrive here as EntitiesForbidden.
    """
    try:
        return fromstring(raw)
    except DefusedXmlException as exc:
        raise XmlExtractionError(f"unsafe XML rejected: {exc}") from exc
    except ParseError as exc:
        raise XmlExtractionError(f"malformed XML: {exc}") from exc


def _require_node(hdon: Element, path: str) -> Element:
    node = hdon.find(path)
    if node is None:
        raise XmlExtractionError(f"{path}: required element missing")
    return node


def _party(node: Element | None, field: str) -> str | None:
    """Seller/buyer field. Absent party is not an error — one fixture has no
    NMua/Ten at all, and buyer MST is missing in three forms: '0', '', absent.
    """
    return None if node is None else node.findtext(field)


def _grand_total(totals: Element) -> Decimal:
    """TgTTTBSo — the one total present in 8/8 fixtures, hence required.

    TgTCThue and TgTThue are absent from 3/8, so they stay Optional.
    """
    value = sch.money(
        totals.findtext(sch.GRAND_TOTAL), where=f"{sch.TOTALS}/{sch.GRAND_TOTAL}"
    )
    if value is None:
        raise XmlExtractionError(f"{sch.TOTALS}/{sch.GRAND_TOTAL}: required")
    return value


def _line(element: Element, index: int) -> InvoiceLine:
    where = f"{sch.LINES}[{index}]"
    return InvoiceLine(
        line_no=sch.integer(element.findtext(sch.LINE_NO), where=f"{where}/{sch.LINE_NO}"),
        item_kind=sch.integer(
            element.findtext(sch.LINE_KIND), where=f"{where}/{sch.LINE_KIND}"
        ),
        description=element.findtext(sch.LINE_DESC),
        unit=element.findtext(sch.LINE_UNIT),
        quantity=sch.money(element.findtext(sch.LINE_QTY), where=f"{where}/{sch.LINE_QTY}"),
        unit_price=sch.money(
            element.findtext(sch.LINE_PRICE), where=f"{where}/{sch.LINE_PRICE}"
        ),
        amount=sch.money(
            element.findtext(sch.LINE_AMOUNT), where=f"{where}/{sch.LINE_AMOUNT}"
        ),
        vat_rate=sch.vat_rate(
            element.findtext(sch.LINE_VAT_RATE), where=f"{where}/{sch.LINE_VAT_RATE}"
        ),
        vat_amount=sch.money(
            element.findtext(sch.LINE_VAT_AMOUNT), where=f"{where}/{sch.LINE_VAT_AMOUNT}"
        ),
        ext=sch.ext_bag(element.find(sch.EXT_NODE)),
    )
