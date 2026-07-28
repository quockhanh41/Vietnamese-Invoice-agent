"""tests/helpers/hitl_child.py

Chạy trong process RIÊNG, để process cha giết bằng SIGKILL.

Phải là process thật (`sys.executable`), không phải thread hay fork dùng chung
bộ nhớ — cả điểm của test là chứng minh state sống sót khi process CHẾT, không
có `__del__`, không có flush lúc thoát, không có cache Python nào còn lại.

Dùng: python -m tests.helpers.hitl_child <thread_id>
In "INTERRUPTED" ra stdout (đã flush) khi graph dừng, rồi ngủ vô hạn chờ bị giết.
"""

import sys
import time

from tests.helpers.spike_graph import build_spike_graph

from invoice_agent.checkpointer import build_checkpointer


def main(thread_id: str) -> None:
    config = {"configurable": {"thread_id": thread_id}}
    with build_checkpointer() as saver:
        saver.setup()
        app = build_spike_graph().compile(checkpointer=saver)
        result = app.invoke({"thread_id": thread_id, "trace": [], "decision": ""}, config)
        if "__interrupt__" not in result:
            print("NO_INTERRUPT", flush=True)
            sys.exit(2)
        print("INTERRUPTED", flush=True)

    # Ngủ vô hạn. Process cha có trách nhiệm SIGKILL. Nếu cha chết trước thì
    # timeout của pytest dọn hộ.
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main(sys.argv[1])
