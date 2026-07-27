"""tests/helpers/spike_graph.py

Graph 3 node cho spike HITL (M1.5). ĐỒ CHƠI — sẽ bị xoá ở M5 khi
`graphs/approval.py` thật ra đời. Nó không thể LÀ graph thật vì `post_entry`
cần bảng `approvals`/`journal_entries` đến M2.4 mới có.

Vì sao đúng ba node, không phải một: **node chứa `interrupt()` chạy lại TỪ ĐẦU
khi resume** — đó là ngữ nghĩa LangGraph, không phải bug. Side effect nằm cùng
node với `interrupt()` sẽ chạy hai lần. Tách `n1_record` ra node RIÊNG phía
trước cổng là cách duy nhất để nó không bị lặp.

Đây là kiến thức load-bearing cho M5: `post_entry` phải là node riêng SAU
`approval_gate`, `suggest` phải nằm TRƯỚC. Ánh xạ sang §5.1 design doc:

    n1_record  <->  suggest
    n2_gate    <->  approval_gate
    n3_finish  <->  apply_decision
"""

import operator
from typing import Annotated, Any, TypedDict

import psycopg
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from invoice_agent.config import get_settings


class SpikeState(TypedDict):
    """State tối giản. `trace` dùng reducer `operator.add` nên mỗi node trả về
    DELTA (list một phần tử), không bao giờ trả cả list — trả cả list thì mỗi
    vòng nhân đôi, tăng trưởng bậc hai (bẫy đã ghi ở §3.1 design doc)."""

    thread_id: str
    trace: Annotated[list[str], operator.add]
    decision: str


def record_side_effect(thread_id: str) -> None:
    """Insert one row. Stand-in for the irreversible write M5 must never repeat."""
    with psycopg.connect(get_settings().database_url) as conn:
        conn.execute(
            "INSERT INTO app.spike_counter (thread_id) VALUES (%s)", (thread_id,)
        )


def n1_record(state: SpikeState) -> dict[str, Any]:
    record_side_effect(state["thread_id"])
    return {"trace": ["n1_record"]}


def n2_gate(state: SpikeState) -> dict[str, Any]:
    decision = interrupt(
        {
            "cau_hoi": "Duyệt hóa đơn này?",
            "thread_id": state["thread_id"],
        }
    )
    return {"trace": ["n2_gate"], "decision": decision["decision"]}


def n3_finish(state: SpikeState) -> dict[str, Any]:
    return {"trace": ["n3_finish"]}


def build_spike_graph() -> StateGraph:
    """Build the 3-node spike graph. Caller compiles it with a checkpointer."""
    graph = StateGraph(SpikeState)
    graph.add_node("n1_record", n1_record)
    graph.add_node("n2_gate", n2_gate)
    graph.add_node("n3_finish", n3_finish)
    graph.add_edge(START, "n1_record")
    graph.add_edge("n1_record", "n2_gate")
    graph.add_edge("n2_gate", "n3_finish")
    graph.add_edge("n3_finish", END)
    return graph
