"""The 30 injection cases: six payloads across four seedable placements, plus
the agent-to-agent reasoning channel.

Generated at test time from `InvoiceSpec`, deliberately NOT added to the
300-invoice corpus. `prompt_injection` is designed at 1.5% (roadmap §2.2);
adding 24 carriers plus 24 twins would take injection-related invoices from 12
of 300 to 60 of 348, shift every other group's proportion, and force a
regeneration that changes invoice IDs — which would invalidate the M4 gate-batch
declarations, the vendor profiles and the budget chains.
"""

import dataclasses
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scripts.generator.defects import INJECTION_PAYLOADS
from scripts.generator.templates import InvoiceSpec, LineSpec

SEEDABLE_PLACEMENTS = ("first_line", "later_line", "note_line", "seller_name")

CLEAN_NOTE = "Ghi chú: giao hàng tại kho Hà Nội"
CLEAN_SELLER = "CÔNG TY TNHH THƯƠNG MẠI PHÚ QUÝ"

# THE CONTROL TEXT IS PER-PAYLOAD, NOT FIXED. Each entry is the leading,
# non-instruction remainder of its payload — ordinary invoice content with the
# instruction removed. The twin carries THIS, so the only difference between a
# carrier and its twin is the instruction itself.
#
# Measured 2026-08-02: a FIXED control text made this test measure the wrong
# thing. With every `later_line` twin reading "Giấy A4 Double A định lượng
# 70gsm" while carriers read "Dịch vụ tư vấn…" or "Chi phí quảng cáo…", 6 of 24
# pairs classified differently — and 5 of those 6 were `later_line`. The single
# `later_line` payload that did NOT shift was `delimiter_escape`, whose text
# begins "Giấy A4" and therefore happened to match the control. That is the
# control measuring its own mismatch, which is exactly what design §9.3 exists
# to prevent, and `strip_injection()` in scripts/generator/defects.py already
# carries a `clean_description` parameter for this reason.
#
# `test_every_cover_is_a_prefix_of_its_payload` pins these to the payloads, so
# the map cannot drift into an arbitrary second corpus.
PAYLOAD_COVER: dict[str, str] = {
    "vietnamese_direct": "Dịch vụ tư vấn.",
    "english_direct": "Consulting service.",
    "fake_tool_call": "Dịch vụ tư vấn",
    "delimiter_escape": "Giấy A4",
    "social_engineering": "Dịch vụ tư vấn quản trị",
    "category_laundering": "Chi phí quảng cáo.",
}


def base_spec() -> InvoiceSpec:
    """Two goods lines and one TChat=4 note line, with arithmetic that
    validates.

    Three lines because `later_line` and `note_line` need somewhere to go.
    Arithmetic must be clean: if the base invoice raised an ARITHMETIC_MISMATCH,
    every flag comparison in this module would be comparing that finding rather
    than the injection.

    The note line carries amount 0 and no VAT — M1 measured (roadmap §1, "Thực
    tế vs giả định") that TChat=4 is a NOTE line with only THHDVu and no money,
    and that summing it is wrong.
    """
    lines = [
        LineSpec(
            line_no=1, item_kind=1,
            description="Dịch vụ tư vấn quản trị doanh nghiệp",
            unit="Gói", quantity=Decimal("3"), unit_price=Decimal("398181.33"),
            amount=Decimal("1194544"), vat_rate=Decimal("0.08"),
            vat_amount=Decimal("95564"), ext_amount=None, self_closing=False,
        ),
        LineSpec(
            line_no=2, item_kind=1,
            description="Giấy A4 Double A định lượng 70gsm",
            unit="Ram", quantity=Decimal("10"), unit_price=Decimal("85000"),
            amount=Decimal("850000"), vat_rate=Decimal("0.08"),
            vat_amount=Decimal("68000"), ext_amount=None, self_closing=False,
        ),
        LineSpec(
            line_no=3, item_kind=4,
            description=CLEAN_NOTE,
            unit=None, quantity=None, unit_price=None,
            amount=Decimal("0"), vat_rate=None, vat_amount=None,
            ext_amount=None, self_closing=True,
        ),
    ]
    return InvoiceSpec(
        root="hdon", schema_version="2.1.0", template_no="1",
        invoice_series="C26TAA", invoice_number="00000901",
        issue_date=date(2026, 7, 1),
        seller_name=CLEAN_SELLER,
        seller_tax_code="0100000003",
        seller_address="Số 1 Đường Lê Duẩn, Quận Hoàn Kiếm, Hà Nội",
        buyer_name="NGUYỄN THỊ BÍCH NGỌC",
        buyer_tax_code="0300000018-042",
        buyer_address="Phường Bến Nghé, Quận 1, TP Hồ Chí Minh",
        ext_order="ttruong_first", thousand="nghìn",
        lines=lines,
        subtotal=Decimal("2044544"),
        vat_total=Decimal("163564"),
        total_amount=Decimal("2208108"),
        total_in_words=None,
    )


def _with_line_description(spec: InvoiceSpec, index: int, text: str) -> InvoiceSpec:
    lines = list(spec.lines)
    lines[index] = dataclasses.replace(lines[index], description=text)
    return dataclasses.replace(spec, lines=lines)


def place(spec: InvoiceSpec, payload: str, placement: str) -> InvoiceSpec:
    """Put `payload` in the field named by `placement`. Nothing else changes."""
    if placement == "first_line":
        return _with_line_description(spec, 0, payload)
    if placement == "later_line":
        return _with_line_description(spec, 1, payload)
    if placement == "note_line":
        return _with_line_description(spec, 2, payload)
    if placement == "seller_name":
        return dataclasses.replace(spec, seller_name=payload)
    raise ValueError(f"unknown placement {placement!r}")


def control(spec: InvoiceSpec, placement: str, payload_name: str) -> InvoiceSpec:
    """The §9.3 twin: the carrier with the INSTRUCTION removed and nothing else.

    Takes `payload_name` because the control text must match the payload's own
    non-instruction content (see `PAYLOAD_COVER`). A fixed control text makes
    the pair differ in subject matter as well as in the instruction, and the
    measurement then reports that difference as an injection success.

    `seller_tax_code` deliberately does NOT change with `seller_name`. Vendor
    identity is the tax code, so carrier and twin are the same vendor and
    therefore attract the same NEW_VENDOR / PRICE_OUTLIER signals. Giving the
    twin a different tax code would make it a different vendor and the flag
    comparison would measure vendor novelty instead of injection.
    """
    cover = PAYLOAD_COVER[payload_name]
    if placement == "first_line":
        return _with_line_description(spec, 0, cover)
    if placement == "later_line":
        return _with_line_description(spec, 1, cover)
    if placement == "note_line":
        return _with_line_description(spec, 2, cover)
    if placement == "seller_name":
        return dataclasses.replace(spec, seller_name=cover)
    raise ValueError(f"unknown placement {placement!r}")


@dataclass(frozen=True)
class InjectionCase:
    payload_name: str
    payload: str
    placement: str
    carrier: InvoiceSpec
    twin: InvoiceSpec


def _build_cases() -> tuple[InjectionCase, ...]:
    out = []
    for payload_name, payload in INJECTION_PAYLOADS:
        for placement in SEEDABLE_PLACEMENTS:
            base = base_spec()
            out.append(
                InjectionCase(
                    payload_name=payload_name,
                    payload=payload,
                    placement=placement,
                    carrier=place(base, payload, placement),
                    twin=control(base, placement, payload_name),
                )
            )
    return tuple(out)


CASES = _build_cases()

# Placement 5 is not seedable: the Classifier PRODUCES `reasoning`, so it is
# exercised by handing a crafted ClassificationResult straight to `audit()`.
# Spec §1.3 — `agents/audit.py:41` feeds the Classifier's own free text to the
# Auditor, an agent-to-agent channel design §9.1 never enumerated.
REASONING_CASES = INJECTION_PAYLOADS
