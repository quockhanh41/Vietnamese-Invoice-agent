"""The extraction contract: what we read off an invoice, and nothing else.

⚠️ SAFETY PROPERTY (CLAUDE.md). At M3.4 `InvoiceData` becomes the
structured-output schema for an LLM vision call, and structured output IS the
security boundary. Every field here is therefore a field the model can write.

So this is NOT the `invoices` DB row. It deliberately omits `category`,
`department`, `classification_confidence`, `status`, and every flag or approval
field, even though design doc §2.3 has those columns. Before adding a field,
ask: could the LLM use this to self-authorize? `extra="forbid"` is what makes
the omission enforceable rather than aspirational.

Fidelity notes, all measured 2026-07-27 on `tests/fixtures/real_samples/`:
`Decimal` everywhere because amounts reach 8 decimal places
(`21618409.09000000`); almost everything is Optional because real invoices omit
far more than the regulation implies; and text is stored verbatim, since
`TgTTTBChu` carries double spaces and a `./.` suffix that M2.6 must normalize
itself.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class InvoiceLine(BaseModel):
    """One `HHDVu` element."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    line_no: int | None = None  # STT
    # TChat: 1 goods, 2 promotion, 3 discount, 4 NOTE.
    #
    # `int | None`, NOT `int = 1`. TChat is present on 15/15 lines across 8/8
    # fixtures, but whether QĐ-1450 permits omission with an implied default of
    # 1 is UNVERIFIED — it waits on M1.2's outstanding NĐ 70/2025 check. A wrong
    # default would silently fold a TChat=4 note line into the money sum, which
    # is the exact bug the measured validation rule exists to catch.
    item_kind: int | None = None
    # THHDVu. ⚠️ PROMPT-INJECTION SURFACE (design doc §2.4, §9.1): free text
    # written by whoever issued the invoice. Never concatenate into a system
    # prompt — M3.3 wraps it in an escaped <invoice_data> block.
    description: str | None = None
    unit: str | None = None  # DVTinh — absent on non-VAT invoices
    quantity: Decimal | None = None  # SLuong — `<SLuong />` is None, not 0
    unit_price: Decimal | None = None  # DGia
    # ThTien. 0 at VNPT, where the real amount hides in ext['Amount'].
    amount: Decimal | None = None
    vat_rate: Decimal | None = None  # TSuat — '8%' parsed to Decimal('0.08')
    vat_amount: Decimal | None = None  # TThue
    # The line's whole TTKhac block, verbatim. Kept because TTKhac already
    # proved load-bearing once: VNPT sets ThTien=0 and puts the real amount in
    # ext['Amount'], which no amount of reading the regulation would predict.
    ext: dict[str, str] = Field(default_factory=dict)


class InvoiceData(BaseModel):
    """One invoice, as read from XML (M1.4) or vision (M3.4)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str | None = None  # PBan — 2.0.0, 2.0.1 and 2.1.0 all real

    # --- Identity. §2.3's UNIQUE key is
    # (seller_tax_code, template_no, invoice_series, invoice_number) — an
    # invoice_number alone is NOT unique across sellers.
    #
    # template_no IS KHMSHDon, and it is the only trustworthy invoice-kind
    # signal (1 = GTGT, 2 = bán hàng). There is deliberately no separate
    # `invoice_kind` field to disagree with it: THDon looks like it names the
    # kind but is free-text garbage — 6 variants observed, including 'TT78 AC'.
    template_no: str
    invoice_series: str  # KHHDon
    invoice_number: str  # SHDon
    issue_date: date  # NLap

    currency: str | None = None  # DVTTe
    exchange_rate: Decimal | None = None  # TGia — '1' and '1.00' both occur

    seller_name: str | None = None
    # MST has three real shapes: '0100000003' (10), '0300000018-042' (branch,
    # with a dash) and '050000000000' (12, household business). M2.6's
    # validator must accept all three.
    seller_tax_code: str | None = None
    seller_address: str | None = None
    # Buyer fields are Optional because real invoices omit them: one fixture has
    # no NMua/Ten at all, and buyer MST is missing in three forms — '0', '' and
    # absent. Real garbage also lives here: DChi = 'Người mua không lấy hóa đơn'.
    buyer_name: str | None = None
    buyer_tax_code: str | None = None
    buyer_address: str | None = None

    subtotal: Decimal | None = None  # TgTCThue — absent in 3/8 fixtures
    vat_amount: Decimal | None = None  # TgTThue — absent in 3/8 fixtures
    total_amount: Decimal  # TgTTTBSo — the one total present in 8/8
    # TgTTTBChu, verbatim. §10.3 uses it as a second, independent encoding of
    # the total, so M2.6 compares words against digits. Real value:
    # 'Ba  triệu năm trăm ngàn đồng./.' — two spaces, 'ngàn' not 'nghìn', './.'
    # suffix. Normalizing here would hide the very cases M2.6 must handle.
    total_in_words: str | None = None

    lines: list[InvoiceLine]
    # Header-level TTKhac bags, keyed by xml_schema.EXT_SCOPES name.
    ext: dict[str, dict[str, str]] = Field(default_factory=dict)
