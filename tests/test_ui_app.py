"""Smoke tests for the Streamlit script.

The ONLY test that can catch an AttributeError in `app.py` — no client test
reaches it, and the app is the milestone's actual artefact. Fragile by nature:
AppTest re-executes the script in a harness that is not a browser. If it becomes
unmaintainable, DELETE it and say so in the note; never leave it skipped, because
a skipped test reports as a pass.
"""

import pytest

pytest.importorskip("streamlit", minversion="1.28")

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = "src/invoice_agent/ui/app.py"
PAGES = ["Tải lên hóa đơn", "Hàng chờ duyệt", "Chi tiết & quyết định", "Báo cáo"]


def test_the_app_renders_when_the_api_is_unreachable():
    """No server is listening in this process. The app must say so in Vietnamese
    rather than raising — which is both the correct reviewer experience and what
    makes every other AppTest case possible."""
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception, f"app raised: {at.exception}"
    assert any("Không kết nối được" in str(e.value) for e in at.error), (
        "an unreachable API must produce a Vietnamese error, not a blank page"
    )


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_with_stubbed_data(page, monkeypatch):
    """Layout smoke. Stubs the client so the pages render real widgets without a
    server; catches the AttributeError class of bug that turns the demo into a
    stack trace."""
    from invoice_agent.ui import client as ui

    monkeypatch.setattr(ui, "health", lambda **kw: {"status": "ok", "database": "ok"})
    monkeypatch.setattr(ui, "departments", lambda **kw: ["ADMIN", "MKT"])
    monkeypatch.setattr(
        ui,
        "pending_approvals",
        lambda **kw: [
            {
                "invoice_id": "11111111-1111-1111-1111-111111111111",
                "approval_id": "2",
                "thread_id": "appr:1",
                "requested_at": "2026-08-01T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        ui,
        "approval_detail",
        lambda _id, **kw: {
            "invoice_id": _id,
            "approval_id": "2",
            "decision": None,
            "interrupt_payload": {
                "invoice": {"invoice_number": "1"},
                "flags": [],
                "suggestions": [],
            },
            "suggestions": {},
        },
    )
    monkeypatch.setattr(
        ui,
        "get_invoice",
        lambda _id, **kw: {"invoice": {"id": _id}, "lines": [], "flags": []},
    )
    monkeypatch.setattr(ui, "budget_report", lambda **kw: [{"department_code": "ADMIN"}])
    monkeypatch.setattr(ui, "flag_report", lambda **kw: [])
    monkeypatch.setattr(ui, "posting_report", lambda **kw: [])
    monkeypatch.setattr(ui, "cost_report", lambda **kw: {"calls": 0})

    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["page"] = page
    at.run()
    assert not at.exception, f"page {page!r} raised: {at.exception}"
