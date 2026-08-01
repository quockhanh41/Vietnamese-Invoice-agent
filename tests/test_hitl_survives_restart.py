"""tests/test_hitl_survives_restart.py

MỘT TRONG SÁU TEST PORTFOLIO. Nó chứng minh điều mà "confirm dialog" trong các
demo agent hời hợt không bao giờ chứng minh được: quyết định của con người có
thể đến sau khi process đã chết và được khởi động lại.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import select
from tests.helpers.approval_fixtures import _persisted_invoice

from invoice_agent.db import models
from invoice_agent.db.engine import get_engine
from invoice_agent.graphs import approval

# Mặc định 3 cho vòng lặp local nhanh; CI đặt 10 để đóng gate M1.
# Đọc lúc pytest COLLECTION vì `parametrize` chạy ở thì đó — đổi biến giữa
# chừng không có tác dụng.
HITL_RUNS = int(os.getenv("HITL_RUNS", "3"))

# 30s: đo 2026-07-27, interrupt + checkpoint xuống Postgres local mất <1s.
# Để rộng vì CI chậm hơn máy dev, nhưng vẫn phải hữu hạn — treo mãi thì
# thông báo lỗi vô dụng.
CHECKPOINT_TIMEOUT_S = 30


def _count(dsn: str, thread_id: str, node: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT count(*) FROM app.spike_counter WHERE thread_id = %s AND node = %s",
            (thread_id, node),
        ).fetchone()[0]


def _interrupt_persisted(dsn: str, thread_id: str) -> bool:
    """Đã có `interrupt` NẰM TRONG Postgres cho thread này chưa.

    Hỏi đúng câu hỏi, không phải câu gần đúng. Bản đầu tiên hỏi "có row nào
    trong `langgraph.checkpoints` chưa" — đo 2026-07-27 cho thấy đó là câu sai:
    một lần chạy tới interrupt ghi **3** checkpoint, và cái đầu tiên
    (`step=-1, source=input`) hạ cánh TRƯỚC KHI node nào chạy. Khoảng cách từ
    nó tới checkpoint của interrupt là ~9ms (đọc từ timestamp trong
    checkpoint_id kiểu UUIDv6). Một lần poll rơi vào khe đó thì cha giết con
    trước cả `n1_record`, test đỏ, và ta đi đổ tội cho LangGraph.

    `checkpoint_writes.channel = '__interrupt__'` chỉ tồn tại khi cổng đã thật
    sự dừng và đã được ghi xuống — không có khe hở nào.
    """
    with psycopg.connect(dsn) as conn:
        return (
            conn.execute(
                "SELECT count(*) FROM langgraph.checkpoint_writes "
                "WHERE thread_id = %s AND channel = '__interrupt__'",
                (thread_id,),
            ).fetchone()[0]
            > 0
        )


def _wait_for_interrupt(dsn: str, thread_id: str, child: subprocess.Popen) -> None:
    """Đợi tới khi state THẬT SỰ nằm trong Postgres.

    Đây là toàn bộ khác biệt giữa test xanh 10/10 và test flaky. Không có bước
    này, cha có thể giết con TRƯỚC khi checkpoint kịp ghi; resume thất bại và ta
    đổ tội nhầm cho LangGraph trong khi lỗi nằm ở test.
    """
    deadline = time.monotonic() + CHECKPOINT_TIMEOUT_S
    while time.monotonic() < deadline:
        if child.poll() is not None:
            stdout, stderr = child.communicate()
            pytest.fail(
                f"process con chết sớm (rc={child.returncode}).\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        if _interrupt_persisted(dsn, thread_id):
            return
        time.sleep(0.2)
    child.kill()
    pytest.fail(
        f"interrupt() không checkpoint xuống Postgres trong {CHECKPOINT_TIMEOUT_S}s — "
        "đây là phát hiện về framework, không phải lỗi test"
    )


@pytest.mark.parametrize("run", range(HITL_RUNS))
def test_hitl_survives_restart(requires_postgres, committed_rows, run):
    """PORTFOLIO TEST 1, now on the REAL approval graph.

    Proves what a confirm dialog in a shallow agent demo cannot: the human
    decision may arrive after the process that asked for it has died.

    Asserts BOTH halves.

    Mechanism — `suggest` = 1, `approval_gate` = 2 across the process boundary.
    The gate's node runs again from the top on resume; that is LangGraph
    semantics, not a bug, and it is why `post_entry` is a separate node.

    Consequence — exactly ONE journal entry, ONE audit grant, ONE decided
    approval. M1 could only count node executions because the spike had no real
    side effect to count. This graph does, and the consequence is the property
    that actually matters.
    """
    db_id, approval_id = _persisted_invoice(committed_rows)
    project_root = Path(__file__).parent.parent
    env = {**os.environ, "PYTHONPATH": str(project_root)}
    thread_id = f"appr:{db_id}"
    child = subprocess.Popen(
        [sys.executable, "-m", "tests.helpers.hitl_child", str(db_id), str(approval_id)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        _wait_for_interrupt(requires_postgres, thread_id, child)

        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
        assert child.returncode == -signal.SIGKILL, (
            f"the child must die of SIGKILL, got rc={child.returncode}"
        )

        assert _count(requires_postgres, thread_id, "suggest") == 1
        assert _count(requires_postgres, thread_id, "approval_gate") == 1

        from tests.helpers.hitl_child import build_counted_graph

        # A brand-new process. Nothing from the dead one is reused.
        result = approval.submit_decision(
            invoice_id=str(db_id),
            decision="approved",
            decided_by="ketoan",
            graph_builder=lambda checkpointer: build_counted_graph(thread_id, checkpointer),
        )
        assert "__interrupt__" not in result, "the resume was ignored"
        assert result["status"] == "learned"


        assert _count(requires_postgres, thread_id, "suggest") == 1, (
            "a node BEFORE the gate must not run twice"
        )
        assert _count(requires_postgres, thread_id, "approval_gate") == 2, (
            "the node CONTAINING interrupt() runs again from the top on resume"
        )

        with get_engine().begin() as conn:
            entries = list(
                conn.execute(
                    select(models.journal_entries).where(
                        models.journal_entries.c.invoice_id == db_id
                    )
                ).mappings()
            )
            grants = [
                r
                for r in conn.execute(
                    select(models.audit_log).where(
                        models.audit_log.c.target_id == str(db_id)
                    )
                ).mappings()
                if r["allowed"]
            ]
        assert len(entries) == 1, "the irreversible write happened more than once"
        assert entries[0]["posting_authority"] == "human_approved"
        assert len(grants) == 1
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        with psycopg.connect(requires_postgres) as conn:
            conn.execute("DELETE FROM app.spike_counter WHERE thread_id = %s", (thread_id,))
