"""tests/test_spike_graph.py"""

import uuid

import psycopg
from langgraph.types import Command

from invoice_agent.checkpointer import build_checkpointer
from tests.helpers.spike_graph import build_spike_graph


def _count(dsn: str, thread_id: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT count(*) FROM app.spike_counter WHERE thread_id = %s", (thread_id,)
        ).fetchone()[0]


class TestSpikeGraphInProcess:
    """Cơ chế cơ bản, chưa có kill -9. Task 5 mới thêm phần process chết."""

    def test_first_invoke_stops_at_interrupt(self, requires_postgres):
        thread_id = f"spike:{uuid.uuid4()}"
        config = {"configurable": {"thread_id": thread_id}}
        with build_checkpointer() as saver:
            saver.setup()
            app = build_spike_graph().compile(checkpointer=saver)
            result = app.invoke(
                {"thread_id": thread_id, "trace": [], "decision": ""}, config
            )

        assert "__interrupt__" in result, "graph phải dừng ở interrupt()"
        assert result["trace"] == ["n1_record"], "chỉ node trước cổng được chạy"
        assert _count(requires_postgres, thread_id) == 1

    def test_resume_completes_and_does_not_repeat_side_effect(self, requires_postgres):
        thread_id = f"spike:{uuid.uuid4()}"
        config = {"configurable": {"thread_id": thread_id}}
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
        # Đo 2026-07-27: node TRƯỚC interrupt không chạy lại khi resume.
        # Node CHỨA interrupt thì có (nên phần trace của nó chỉ commit một lần).
        assert _count(requires_postgres, thread_id) == 1
