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

from invoice_agent.checkpointer import build_checkpointer
from tests.helpers.spike_graph import build_spike_graph

# Mặc định 3 cho vòng lặp local nhanh; CI đặt 10 để đóng gate M1.
# Đọc lúc pytest COLLECTION vì `parametrize` chạy ở thì đó — đổi biến giữa
# chừng không có tác dụng.
HITL_RUNS = int(os.getenv("HITL_RUNS", "3"))

# 30s: đo 2026-07-27, interrupt + checkpoint xuống Postgres local mất <1s.
# Để rộng vì CI chậm hơn máy dev, nhưng vẫn phải hữu hạn — treo mãi thì
# thông báo lỗi vô dụng.
CHECKPOINT_TIMEOUT_S = 30


def _count(dsn: str, thread_id: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT count(*) FROM app.spike_counter WHERE thread_id = %s", (thread_id,)
        ).fetchone()[0]


def _checkpoint_exists(dsn: str, thread_id: str) -> bool:
    with psycopg.connect(dsn) as conn:
        return (
            conn.execute(
                "SELECT count(*) FROM langgraph.checkpoints WHERE thread_id = %s",
                (thread_id,),
            ).fetchone()[0]
            > 0
        )


def _wait_for_checkpoint(dsn: str, thread_id: str, child: subprocess.Popen) -> None:
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
        if _checkpoint_exists(dsn, thread_id):
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
        _wait_for_checkpoint(requires_postgres, thread_id, child)

        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
        # -9 chứ không chỉ "đã chết": con tự thoát vì lỗi khác (import sai,
        # không nối được DB) mà test vẫn xanh thì test đó vô giá trị.
        assert child.returncode == -signal.SIGKILL, (
            f"con phải chết vì SIGKILL, nhận rc={child.returncode}"
        )

        assert _count(requires_postgres, thread_id) == 1, "n1_record phải đã chạy 1 lần"

        # Checkpointer HOÀN TOÀN MỚI — process cũ đã chết, không có gì tái dùng.
        with build_checkpointer() as saver:
            app = build_spike_graph().compile(checkpointer=saver)
            final = app.invoke(Command(resume={"decision": "approved"}), config)

        assert "__interrupt__" not in final, "resume bị bỏ qua"
        assert final["decision"] == "approved"
        assert final["trace"] == ["n1_record", "n2_gate", "n3_finish"]
        # Điều kiện CẦN của "không bao giờ post hai lần".
        assert _count(requires_postgres, thread_id) == 1, "side effect bị lặp sau resume"
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
