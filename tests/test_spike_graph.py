"""tests/test_spike_graph.py"""

import uuid

import psycopg
from langgraph.types import Command
from tests.helpers.spike_graph import build_spike_graph

from invoice_agent.checkpointer import build_checkpointer


def _count(dsn: str, thread_id: str, node: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT count(*) FROM app.spike_counter WHERE thread_id = %s AND node = %s",
            (thread_id, node),
        ).fetchone()[0]


def _cleanup(dsn: str, thread_id: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM app.spike_counter WHERE thread_id = %s", (thread_id,))


class TestSpikeGraphInProcess:
    """Cơ chế cơ bản, chưa có kill -9. Task 5 mới thêm phần process chết."""

    def test_first_invoke_stops_at_interrupt(self, requires_postgres):
        thread_id = f"spike:{uuid.uuid4()}"
        config = {"configurable": {"thread_id": thread_id}}
        try:
            with build_checkpointer() as saver:
                saver.setup()
                app = build_spike_graph().compile(checkpointer=saver)
                result = app.invoke({"thread_id": thread_id, "trace": [], "decision": ""}, config)

            assert "__interrupt__" in result, "graph phải dừng ở interrupt()"
            assert result["trace"] == ["n1_record"], "chỉ node trước cổng được commit"
            assert _count(requires_postgres, thread_id, "n1_record") == 1
            # Thân `n2_gate` ĐÃ chạy một lần dù `trace` không thấy nó: lần chạy
            # đầu ném GraphInterrupt nên không commit gì.
            assert _count(requires_postgres, thread_id, "n2_gate") == 1
        finally:
            _cleanup(requires_postgres, thread_id)

    def test_resume_completes_and_does_not_repeat_side_effect(self, requires_postgres):
        thread_id = f"spike:{uuid.uuid4()}"
        config = {"configurable": {"thread_id": thread_id}}
        try:
            with build_checkpointer() as saver:
                saver.setup()
                app = build_spike_graph().compile(checkpointer=saver)
                app.invoke({"thread_id": thread_id, "trace": [], "decision": ""}, config)

            # checkpointer MỚI — mô phỏng process khác, dù vẫn cùng process Python
            with build_checkpointer() as saver2:
                app2 = build_spike_graph().compile(checkpointer=saver2)
                final = app2.invoke(Command(resume={"decision": "approved"}), config)

            assert "__interrupt__" not in final
            assert final["decision"] == "approved"
            assert final["trace"] == ["n1_record", "n2_gate", "n3_finish"]

            # Đo 2026-07-27, LangGraph 1.2.9 — HAI NỬA của cùng một phát hiện,
            # và nửa thứ hai mới là nửa đắt:
            assert _count(requires_postgres, thread_id, "n1_record") == 1, (
                "node TRƯỚC cổng không được chạy lại"
            )
            assert _count(requires_postgres, thread_id, "n2_gate") == 2, (
                "node CHỨA interrupt() PHẢI chạy lại từ đầu — nếu con số này là 1 "
                "thì tiền đề của ADR 0002 sai và M5 được phép gộp post_entry vào cổng"
            )
            # `trace` KHÔNG thay được hai assert trên: `n2_gate` xuất hiện đúng
            # một lần trong trace dù thân nó chạy hai lần.
            assert final["trace"].count("n2_gate") == 1
        finally:
            _cleanup(requires_postgres, thread_id)
