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
import uuid
from pathlib import Path

import psycopg
import pytest
from langgraph.types import Command
from tests.helpers.spike_graph import build_spike_graph

from invoice_agent.checkpointer import build_checkpointer

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
def test_hitl_survives_restart(requires_postgres, run):
    thread_id = f"spike:{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}
    project_root = Path(__file__).parent.parent
    env = {**os.environ, "PYTHONPATH": str(project_root)}
    child = subprocess.Popen(
        [sys.executable, "-m", "tests.helpers.hitl_child", thread_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        _wait_for_interrupt(requires_postgres, thread_id, child)

        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
        # -9 chứ không chỉ "đã chết": con tự thoát vì lỗi khác (import sai,
        # không nối được DB) mà test vẫn xanh thì test đó vô giá trị.
        assert child.returncode == -signal.SIGKILL, (
            f"con phải chết vì SIGKILL, nhận rc={child.returncode}"
        )

        assert _count(requires_postgres, thread_id, "n1_record") == 1, (
            "n1_record phải đã chạy 1 lần"
        )
        assert _count(requires_postgres, thread_id, "n2_gate") == 1, (
            "thân n2_gate phải đã chạy 1 lần trước khi process chết"
        )

        # Checkpointer HOÀN TOÀN MỚI — process cũ đã chết, không có gì tái dùng.
        with build_checkpointer() as saver:
            app = build_spike_graph().compile(checkpointer=saver)
            final = app.invoke(Command(resume={"decision": "approved"}), config)

        assert "__interrupt__" not in final, "resume bị bỏ qua"
        assert final["decision"] == "approved"
        assert final["trace"] == ["n1_record", "n2_gate", "n3_finish"]

        # Điều kiện CẦN của "không bao giờ post hai lần" — và nó chỉ đúng cho
        # node TRƯỚC cổng.
        assert _count(requires_postgres, thread_id, "n1_record") == 1, (
            "side effect ở node trước cổng bị lặp sau resume"
        )
        # Nửa còn lại của phát hiện, đo qua RANH GIỚI PROCESS: 1 lần ở process
        # đã bị kill -9, 1 lần nữa ở process này. Đây là lý do `post_entry` của
        # M5 không được nằm cùng node với `approval_gate`.
        assert _count(requires_postgres, thread_id, "n2_gate") == 2, (
            "node CHỨA interrupt() phải chạy lại từ đầu sau resume"
        )
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        with psycopg.connect(requires_postgres) as conn:
            conn.execute(
                "DELETE FROM app.spike_counter WHERE thread_id = %s", (thread_id,)
            )


def test_ci_runs_the_full_ten():
    """Chốt chặn: không có test này, một ngày nào đó ai đó sửa workflow, CI âm
    thầm chạy 3 lần, và gate "10/10" tự suy yếu mà không ai biết."""
    if os.getenv("CI", "").lower() in ("true", "1"):
        assert HITL_RUNS >= 10, (
            f"CI phải đặt HITL_RUNS>=10 để đóng gate M1, đang là {HITL_RUNS}"
        )
