"""`explain_flags` — narrate a deterministic flag, in Vietnamese, from a template.

Flags are computed by Python from database state; that is this project's central
safety property (CLAUDE.md, design §3.2). Narrating them with an LLM would put a
model back into the explanation path for a decision it did not make, and a
reviewer reading the narrative could not tell which parts were computed and
which were generated. Templates keep that boundary visible: every sentence below
is fixed text plus the flag's own `detail` payload, nothing else.

The narrative TEXT is Vietnamese on purpose — a human reads it, so CLAUDE.md's
product-output exception applies (this file is named there explicitly) and it
is not translated. `TEMPLATES_VI` keys are matched against the real `detail`
payload each code's `raise_flag` call site actually produces (see the call
sites in `domain/validation.py`, `domain/taxcode.py`, `domain/anomaly.py`,
`domain/budget.py`, `agents/classification.py` and `graphs/batch.py`), not
invented field names.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import Connection

from invoice_agent.db.repositories import flags as flags_repo
from invoice_agent.domain import findings
from invoice_agent.tools import models

# A flag's `detail` may be partial (a future call site might add a key without
# updating every template) or entirely absent (ITEM_KIND_MISSING, TAX_CODE_MISSING,
# NEW_VENDOR and DUPLICATE_INVOICE carry none at all, by their own call sites).
# `str.format` raises KeyError on the first one; a narrative that crashes in
# front of an accountant is worse than one that quietly omits a number, so a
# missing or null value renders as this placeholder instead.
MISSING_DETAIL_VI = "(không rõ)"


class _MissingAsPlaceholder(dict):
    def __missing__(self, key: str) -> str:
        return MISSING_DETAIL_VI

    def __getitem__(self, key: str) -> Any:
        value = super().__getitem__(key)
        return MISSING_DETAIL_VI if value is None else value


# One entry per `domain.findings.CODE_REGISTRY` code (18 today) —
# `test_every_registered_flag_code_has_a_narrative_template` enumerates the
# registry so a future code cannot be added without a template for it.
TEMPLATES_VI: dict[str, str] = {
    findings.ARITHMETIC_MISMATCH: (
        "Sai lệch số học: tiền hàng {goods_subtotal} cộng thuế GTGT {vat_total} "
        "không khớp với tổng tiền thanh toán {total_amount} ghi trên hóa đơn."
    ),
    findings.SUBTOTAL_MISMATCH: (
        "Tiền hàng cộng từ các dòng chi tiết là {computed}, không khớp với tiền "
        "hàng khai báo trên hóa đơn là {declared}."
    ),
    findings.VAT_TOTAL_MISMATCH: (
        "Tổng thuế GTGT cộng từ các dòng chi tiết là {computed}, không khớp với "
        "tổng thuế GTGT khai báo trên hóa đơn là {declared}."
    ),
    findings.LINE_AMOUNT_MISMATCH: (
        "Dòng hàng số {line_no}: số lượng {quantity} nhân đơn giá {unit_price} "
        "không khớp với thành tiền {amount} ghi trên dòng."
    ),
    findings.LINE_VAT_MISMATCH: (
        "Dòng hàng số {line_no}: thành tiền {amount} nhân thuế suất {vat_rate} "
        "không khớp với tiền thuế GTGT {vat_amount} ghi trên dòng."
    ),
    findings.WORDS_DIGITS_MISMATCH: (
        'Số tiền bằng chữ "{words}" (đọc ra là {parsed}) không khớp với số tiền '
        "bằng số {digits} trên hóa đơn."
    ),
    findings.ITEM_KIND_MISSING: (
        "Có ít nhất một dòng hàng trên hóa đơn không xác định được là hàng hóa, "
        "dịch vụ hay ghi chú."
    ),
    findings.TAX_CODE_CHECKSUM_INVALID: (
        "Mã số thuế {tax_code} không vượt qua kiểm tra chữ số kiểm tra theo "
        "thuật toán của Tổng cục Thuế."
    ),
    findings.TAX_CODE_SHAPE_UNRECOGNISED: (
        "Mã số thuế {tax_code} không đúng định dạng đã biết (10 số, 10 số kèm "
        "chi nhánh, hoặc 12 số hộ kinh doanh)."
    ),
    findings.TAX_CODE_MISSING: (
        "Hóa đơn không có mã số thuế người bán, hoặc mã số thuế bị bỏ trống."
    ),
    findings.PRICE_OUTLIER: (
        "Số tiền {amount} lệch bất thường so với trung bình lịch sử của nhà "
        "cung cấp là {avg} (z-score = {z_score})."
    ),
    findings.NEAR_DUPLICATE: (
        "Hóa đơn cùng người bán, cùng số tiền {total_amount}, gần ngày với hóa "
        "đơn số {matched_invoice_number} — nghi ngờ bị xuất trùng dưới số khác."
    ),
    findings.NEW_VENDOR: (
        "Đây là nhà cung cấp chưa từng giao dịch trước đây, hệ thống chưa có "
        "lịch sử để đối chiếu."
    ),
    findings.BUDGET_WARNING: (
        "Ngân sách sắp cạn: hạn mức {limit}, sau khi trừ hóa đơn này còn lại "
        "{balance_after}, đã vượt ngưỡng cảnh báo {warn_threshold_pct}."
    ),
    findings.BUDGET_BREACH: (
        "Vượt ngân sách: hạn mức {limit}, số dư trước khi trừ hóa đơn này là "
        "{balance_before}, sau khi trừ còn lại {balance_after} (đã âm)."
    ),
    findings.BUDGET_UNRECONCILED: (
        "Hóa đơn chưa được đối chiếu với ngân sách nào vì không xác định được "
        "phòng ban (phòng ban ghi nhận: {department})."
    ),
    findings.DUPLICATE_INVOICE: (
        "Hóa đơn trùng khớp hoàn toàn (cùng người bán, số hóa đơn, ngày phát "
        "hành) với một hóa đơn đã có trong hệ thống."
    ),
    findings.CLASSIFICATION_DISPUTED: (
        'Bộ phân loại đề xuất hạng mục "{classifier_category}" (phòng ban '
        "{classifier_department}, lý do: {classifier_reason}), nhưng bộ kiểm "
        'toán không đồng ý và đề xuất hạng mục "{auditor_category}" (phòng ban '
        "{auditor_department}, lý do: {auditor_reason})."
    ),
}

_SEVERITY_LABEL_VI = {"critical": "nghiêm trọng", "warn": "cảnh báo", "info": "thông tin"}


def render(code: str, detail: dict[str, Any]) -> str:
    """Format `code`'s template against `detail`. A key the template wants but
    `detail` does not carry renders as `MISSING_DETAIL_VI`, not a KeyError."""
    return TEMPLATES_VI[code].format_map(_MissingAsPlaceholder(detail))


def _summary_vi(explained: list[models.ExplainedFlag]) -> str:
    if not explained:
        return "Hóa đơn hiện không có cảnh báo nào đang mở."
    counts: dict[str, int] = {}
    for flag in explained:
        counts[flag.severity] = counts.get(flag.severity, 0) + 1
    # Most severe first, matching findings.SEVERITIES reversed — a reviewer
    # scans critical before info.
    parts = [
        f"{counts[sev]} {_SEVERITY_LABEL_VI[sev]}"
        for sev in ("critical", "warn", "info")
        if sev in counts
    ]
    return f"Hóa đơn có {len(explained)} cảnh báo đang mở: " + ", ".join(parts) + "."


def explain_flags(conn: Connection, invoice_id: UUID) -> models.ExplainedFlags:
    """Every open flag on `invoice_id`, narrated. No model, no write."""
    rows = flags_repo.open_flags_for(conn, invoice_id)
    explained = []
    for row in rows:
        severity, raised_by = findings.CODE_REGISTRY[row["code"]]
        explained.append(
            models.ExplainedFlag(
                code=row["code"],
                severity=severity,
                raised_by=raised_by,
                narrative_vi=render(row["code"], row["detail"]),
            )
        )
    return models.ExplainedFlags(flags=explained, summary_vi=_summary_vi(explained))
