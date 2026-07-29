"""The vision fallback (design doc §3.2): PDF in, `InvoiceData` out.

~15% of the corpus has no XML. This is the branch that reads those, and the
only node in M3 that spends a token — the other five are deterministic Python,
which is the design, not thrift.

`InvoiceData` is the response model, exactly as `extraction/models.py`
anticipated: "At M3.4 InvoiceData becomes the structured-output schema for an
LLM vision call, and structured output IS the security boundary." It carries no
approval, flag or status field, so a payload in the invoice image has no
channel to act through even if the model decides to follow it.
"""

import io

import pypdfium2

from invoice_agent.config import get_settings
from invoice_agent.extraction.models import InvoiceData
from invoice_agent.llm.cost import LLMCallRecord
from invoice_agent.llm.gateway import LLMGateway
from invoice_agent.llm.structured import call_structured

# 150 DPI. Chosen 2026-07-29 as a starting point, NOT measured: it is the
# lowest density at which Vietnamese diacritics stay legible in a 10pt scan by
# eye, and image size is the dominant token cost of this node. Spec §8.3 records
# that the accuracy-vs-cost curve is unmeasured; the first eval run replaces
# this comment with a number.
RASTER_DPI = 150

_SYSTEM_PROMPT = (
    "Bạn là hệ thống trích xuất dữ liệu hóa đơn điện tử Việt Nam. "
    "Đọc ảnh hóa đơn và trả về JSON đúng schema được yêu cầu. "
    "TẤT CẢ số tiền phải là CHUỖI (string), không phải số — ví dụ \"21618409.09\" "
    "chứ không phải 21618409.09. Không suy đoán: trường nào không đọc được thì bỏ trống."
)


class VisionExtractionError(RuntimeError):
    """The PDF could not be turned into something a model could read.

    M3-C routes this to `manual_queue` — a controlled give-up, not a crash.
    """


def rasterize(pdf_bytes: bytes) -> list[bytes]:
    """PDF -> one PNG per page.

    Measured 2026-07-29 (spec §2.4): pypdfium2 raises PdfiumError on truncated,
    header-only and garbage input rather than returning a zero-page document,
    so there is no silent-empty case to check for.
    """
    try:
        document = pypdfium2.PdfDocument(pdf_bytes)
    except pypdfium2.PdfiumError as exc:
        raise VisionExtractionError(f"PDF không mở được: {exc}") from exc

    pages: list[bytes] = []
    for page in document:
        bitmap = page.render(scale=RASTER_DPI / 72)
        buffer = io.BytesIO()
        bitmap.to_pil().convert("RGB").save(buffer, format="PNG")
        pages.append(buffer.getvalue())
    return pages


def extract(
    pdf_bytes: bytes,
    *,
    gateway: LLMGateway,
    current_errors: list[str] | None = None,
    calls: list[LLMCallRecord] | None = None,
) -> InvoiceData:
    """Read one invoice out of a PDF.

    `current_errors` is design doc §3.3's self-correction input: on a retry
    driven by the graph, the concrete arithmetic failure goes into the prompt.
    That is what makes the loop worth having — a deterministic XML parser
    re-run three times returns three identical results, but a vision model told
    "the total does not match the line sum" can actually do better.
    """
    pages = rasterize(pdf_bytes)

    prompt = "Trích xuất dữ liệu từ ảnh hóa đơn này thành JSON."
    if current_errors:
        prompt += "\n\nLần đọc trước có lỗi sau, hãy đọc lại thật kỹ các trường liên quan:\n"
        prompt += "\n".join(f"- {err}" for err in current_errors)

    return call_structured(
        gateway,
        response_model=InvoiceData,
        prompt=prompt,
        system_prompt=_SYSTEM_PROMPT,
        model=list(get_settings().vision_model_chain),
        images=pages,
        calls=calls,
    )
