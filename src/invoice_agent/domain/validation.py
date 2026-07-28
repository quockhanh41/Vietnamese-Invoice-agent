"""Deterministic invoice validation — DATA errors only.

WHAT THIS NEVER DOES: raise. Design doc §3.3 splits EXTRACTION errors ("we
misread the document" -> escalate XML -> vision -> manual_queue) from DATA
errors ("the invoice itself is wrong" -> carry a flag to a human).
XmlExtractionError belongs to the parser. Keeping this module incapable of
producing one puts the split on the right side of the module boundary.

EVERY CHECK IS INDEPENDENTLY SKIPPABLE. One absent field must never suppress
the others, and — more importantly — must never invent a finding. See the two
guards in §2.3 of the spec, which exist because the obvious version of these
rules flags 3 of 8 real, valid, tax-authority-signed invoices.
"""

from collections.abc import Sequence
from decimal import Decimal

from invoice_agent.domain import findings, money, taxcode
from invoice_agent.extraction import xml_schema as sch
from invoice_agent.extraction.models import InvoiceData, InvoiceLine

# Measured 2026-07-28 on the 8 real fixtures (spec §2.2):
#   max |SLuong x DGia - ThTien| = 0.01   (3 x 398181.33 = 1194543.99, ThTien = 1194544
#                                          — the seller divided the unit price back out
#                                          of a rounded line total)
#   max |ThTien x TSuat - TThue| = 0.48   (tax rounded to the đồng, deviating BOTH ways)
# Rounded up to a round number; the measurement is the justification, the
# rounding is headroom.
LINE_AMOUNT_TOLERANCE = Decimal("0.01")
LINE_VAT_TOLERANCE = Decimal("0.50")


def _goods_lines(lines: Sequence[InvoiceLine]) -> list[InvoiceLine]:
    return [ln for ln in lines if ln.item_kind != sch.ITEM_KIND_NOTE]


def validate(invoice: InvoiceData) -> list[findings.Finding]:
    out: list[findings.Finding] = []
    goods = _goods_lines(invoice.lines)
    subtotal = money.goods_subtotal(invoice.lines)
    vat_total = invoice.vat_amount or Decimal("0")

    # 1. Header arithmetic — the measured 8/8 rule (spec §2.1). Exact.
    if subtotal + vat_total != invoice.total_amount:
        out.append(findings.make(
            findings.ARITHMETIC_MISMATCH,
            goods_subtotal=subtotal, vat_total=vat_total, total_amount=invoice.total_amount,
        ))

    # 2. Subtotal — ONLY when TgTCThue is stated AND non-zero.
    #    A non-VAT invoice (KHMSHDon=2) reports TgTCThue = 0 while its lines
    #    carry the full amount; comparing them flags a valid invoice.
    if invoice.subtotal:
        if subtotal != invoice.subtotal:
            out.append(findings.make(
                findings.SUBTOTAL_MISMATCH,
                computed=subtotal, declared=invoice.subtotal,
            ))

    # 3. VAT total — ONLY when EVERY goods line states TThue.
    #    dienluc_ and phongvu_ state TSuat per line but not TThue; only the
    #    header has VAT, so Σ TThue is 0 and the comparison is meaningless.
    if invoice.vat_amount is not None and goods and all(
        ln.vat_amount is not None for ln in goods
    ):
        line_vat = sum((ln.vat_amount or Decimal("0") for ln in goods), Decimal("0"))
        if line_vat != invoice.vat_amount:
            out.append(findings.make(
                findings.VAT_TOTAL_MISMATCH,
                computed=line_vat, declared=invoice.vat_amount,
            ))

    out.extend(_check_lines(goods))
    out.extend(_check_words(invoice))
    out.extend(_check_tax_codes(invoice))
    if any(ln.item_kind is None for ln in invoice.lines):
        out.append(findings.make(findings.ITEM_KIND_MISSING))
    return out


def _check_lines(goods: Sequence[InvoiceLine]) -> list[findings.Finding]:
    out: list[findings.Finding] = []
    for index, ln in enumerate(goods, start=1):
        if ln.quantity is not None and ln.unit_price is not None and ln.amount is not None:
            if abs(ln.quantity * ln.unit_price - ln.amount) > LINE_AMOUNT_TOLERANCE:
                out.append(findings.make(
                    findings.LINE_AMOUNT_MISMATCH, line_no=index,
                    quantity=ln.quantity, unit_price=ln.unit_price, amount=ln.amount,
                ))
        if ln.amount is not None and ln.vat_rate is not None and ln.vat_amount is not None:
            if abs(ln.amount * ln.vat_rate - ln.vat_amount) > LINE_VAT_TOLERANCE:
                out.append(findings.make(
                    findings.LINE_VAT_MISMATCH, line_no=index,
                    amount=ln.amount, vat_rate=ln.vat_rate, vat_amount=ln.vat_amount,
                ))
    return out


def _check_words(invoice: InvoiceData) -> list[findings.Finding]:
    """TgTTTBChu is a second, independent encoding of the total (§10.3), so a
    disagreement is a real finding. An UNPARSEABLE value is not: returning None
    means we could not read it, and flagging that would punish vendors for
    phrasing rather than for arithmetic."""
    parsed = money.parse_amount_words(invoice.total_in_words)
    if parsed is not None and parsed != invoice.total_amount:
        return [findings.make(
            findings.WORDS_DIGITS_MISMATCH,
            words=invoice.total_in_words, parsed=parsed, digits=invoice.total_amount,
        )]
    return []


def _check_tax_codes(invoice: InvoiceData) -> list[findings.Finding]:
    """Seller MST is required — measured present 8/8, and M2-A makes the column
    NOT NULL. Buyer MST is never a real value in the corpus, so absence there
    is not a finding."""
    out = []
    seller = taxcode.check_tax_code(invoice.seller_tax_code, required=True)
    if seller is not None:
        out.append(seller)
    buyer = taxcode.check_tax_code(invoice.buyer_tax_code)
    if buyer is not None:
        out.append(buyer)
    return out
