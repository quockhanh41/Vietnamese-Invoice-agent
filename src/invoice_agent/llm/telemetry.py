"""Priced records into app.llm_calls — the one place cost reaches the database.

Its own module so the dependency points DOWNWARD from glue: telemetry imports
both `llm.cost` and `db.repositories`, while the gateway imports only the pure
dataclass and `db/` keeps knowing nothing about `llm/`. Pricing inside the
repository would invert that; pricing at each call site would repeat it three
times by M4 (vision, classify, audit).
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import Connection

from invoice_agent.db.repositories import llm_calls
from invoice_agent.llm.cost import LLMCallRecord, actual_cost_usd, shadow_cost_usd


def record(
    conn: Connection,
    records: Sequence[LLMCallRecord],
    *,
    node: str,
    invoice_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
    thread_id: str | None = None,
    agent: str | None = None,
    trace_id: str | None = None,
) -> list[int]:
    """Write one row per attempt. Returns the new row ids, in order.

    Every attempt, not only the successful one: design doc §2.9 is explicit
    that a 429 still burned input tokens, and omitting it makes the cost number
    wrong. `trace_id` is wired: M6-B made `observability.current_trace_id()`
    the writer (`app.llm_calls.trace_id`).
    """
    return [
        llm_calls.record_call(
            conn,
            model=r.model,
            provider=r.provider,
            node=node,
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            cost_usd=actual_cost_usd(r),
            shadow_cost_usd=shadow_cost_usd(r),
            latency_ms=r.latency_ms,
            attempt=r.attempt,
            succeeded=r.succeeded,
            error=r.error,
            invoice_id=invoice_id,
            batch_id=batch_id,
            thread_id=thread_id,
            agent=agent,
            trace_id=trace_id,
        )
        for r in records
    ]
