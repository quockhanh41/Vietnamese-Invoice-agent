"""The Streamlit approval UI — layout only.

NO HTTP HERE. Every call goes through `ui/client.py`, because Streamlit reruns
this script top to bottom on every interaction and nothing in it is reachable by
a unit test. `tests/test_ui_boundary.py` asserts this file contains no HTTP client logic.

On-screen copy is Vietnamese: a Vietnamese accountant reads it, which CLAUDE.md
puts on the product side of the authoring-language rule.
"""

import time
from typing import Any

import streamlit as st

from invoice_agent.ui import client as api

PAGES = ["Tải lên hóa đơn", "Hàng chờ duyệt", "Chi tiết & quyết định", "Báo cáo"]


def _guard(call: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a client call, or render a Vietnamese error and return None.

    Catches `HTTPError` as well as `ApiError` because the API being DOWN is
    the most likely failure a reviewer meets, and it is also the state
    `AppTest` runs in — so this is what makes the smoke test able to assert
    something other than "it crashed".
    """
    from httpx import HTTPError

    try:
        return call(*args, **kwargs)
    except api.ApiError as exc:
        st.error(f"API trả về lỗi {exc.status_code}: {exc.detail}")
    except HTTPError:
        st.error("Không kết nối được tới API. Hãy chạy `make demo` hoặc `make api`.")
    return None


def page_upload() -> None:
    st.header("Tải lên hóa đơn")
    depts = _guard(api.departments) or []
    department = st.selectbox("Phòng ban (để trống thì không đối chiếu ngân sách)", ["—", *depts])
    uploaded = st.file_uploader("Chọn file XML hoặc PDF", accept_multiple_files=True)

    if st.button("Xử lý") and uploaded:
        payload = [(f.name, f.getvalue()) for f in uploaded]
        started = _guard(
            api.upload_batch, payload, department=None if department == "—" else department
        )
        if started is None:
            return
        with st.status("Đang xử lý…", expanded=True) as box:
            summary: dict[str, Any] = {}
            for _ in range(120):  # 60s at 0.5s — a live batch measured 20.4s at M5-B
                summary = _guard(api.get_batch, started["batch_id"]) or {}
                if not summary:
                    return
                st.write(
                    f"đã lưu {summary['persisted']} · đã hạch toán {summary['posted']}"
                    f" · chờ duyệt {summary['pending_approval']}"
                )
                if summary.get("status") in ("completed", "failed"):
                    break
                time.sleep(0.5)
            box.update(label=f"Xong — trạng thái {summary.get('status')}", state="complete")


def page_queue() -> None:
    st.header("Hàng chờ duyệt")
    rows = _guard(api.pending_approvals)
    if rows is None:
        return
    if not rows:
        st.info("Không có hóa đơn nào đang chờ duyệt.")
        return
    st.dataframe(rows, use_container_width=True)
    st.caption("Sao chép `invoice_id` sang tab “Chi tiết & quyết định”.")


def page_detail() -> None:
    st.header("Chi tiết & quyết định")
    invoice_id = st.text_input("invoice_id")
    if not invoice_id:
        st.info("Nhập `invoice_id` từ hàng chờ duyệt.")
        return

    detail = _guard(api.approval_detail, invoice_id)
    if detail is None:
        return
    if detail.get("decision") is not None:
        st.warning(f"Hóa đơn này đã được quyết định: {detail['decision']}.")

    payload = detail.get("interrupt_payload") or {}
    st.subheader("Hóa đơn")
    st.json(payload.get("invoice", {}))
    st.subheader("Cảnh báo")
    st.dataframe(payload.get("flags") or [], use_container_width=True)
    st.subheader("Gợi ý từ lần duyệt trước")
    st.dataframe(payload.get("suggestions") or [], use_container_width=True)

    with st.form("quyet_dinh"):
        decision = st.radio(
            "Quyết định",
            ["approved", "rejected", "edited"],
            format_func=lambda v: {
                "approved": "Duyệt",
                "rejected": "Từ chối",
                "edited": "Duyệt có sửa",
            }[v],
        )
        # Free text because there is no auth — design §11.3 cut it, and the API
        # records what the client claims. Said on screen rather than defaulted.
        decided_by = st.text_input("Người duyệt (chưa có đăng nhập — tự khai)")
        reason = st.text_area("Lý do")
        if st.form_submit_button("Gửi quyết định"):
            if not decided_by:
                st.error("Phải nhập tên người duyệt.")
                return
            result = _guard(
                api.submit_decision,
                invoice_id,
                decision=decision,
                decided_by=decided_by,
                reason=reason or None,
            )
            if result is not None:
                st.success(
                    f"Đã ghi nhận: {result['decision']} · bút toán {result.get('entry_id')}"
                )


def page_reports() -> None:
    st.header("Báo cáo")

    period = st.text_input("Kỳ (YYYY-MM)", value="2026-07")
    note = _guard(api.commentary, period)
    if note:
        st.info(note["summary"])
        for line in note["observations"]:
            st.write(f"- {line}")
        # Which one wrote it is not decoration: the model's version is used only
        # when every number in it is one Python computed (agents/reporting.py).
        st.caption(f"Nguồn nhận xét: {note['source']}")

    budgets = _guard(api.budget_report) or []
    st.subheader("Ngân sách")
    if budgets:
        # float() is DISPLAY ONLY. Money is Decimal everywhere else in this
        # system (M1); a float that reached the API or the database would lose
        # the last đồng. st.bar_chart cannot plot Decimal.
        chart = {
            row["department_code"]: {
                "Hạn mức": float(row["amount_limit"]),
                "Đã dùng": float(row["consumed"]),
            }
            for row in budgets
        }
        st.bar_chart(chart)
    st.dataframe(budgets, use_container_width=True)

    flags = _guard(api.flag_report) or []
    st.subheader("Cảnh báo theo mã")
    if flags:
        st.bar_chart({row["code"]: row["total"] for row in flags})
    st.dataframe(flags, use_container_width=True)

    posting = _guard(api.posting_report) or []
    st.subheader("Hạch toán tự động vs người duyệt")
    if posting:
        st.bar_chart({row["posting_authority"]: row["entries"] for row in posting})
    st.dataframe(posting, use_container_width=True)

    st.subheader("Chi phí LLM")
    st.json(_guard(api.cost_report) or {})



def main() -> None:
    st.set_page_config(page_title="Trợ lý hóa đơn", layout="wide")
    st.sidebar.title("Trợ lý hóa đơn")
    page = st.sidebar.radio("Trang", PAGES, key="page")
    {
        PAGES[0]: page_upload,
        PAGES[1]: page_queue,
        PAGES[2]: page_detail,
        PAGES[3]: page_reports,
    }[page]()


main()
