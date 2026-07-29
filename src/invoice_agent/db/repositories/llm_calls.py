"""LLM telemetry (design doc §2.9).

Records every call — including failures (429, timeout) — so token and cost
metrics are honest.
"""

import uuid
from decimal import Decimal

from sqlalchemy import Connection

from invoice_agent.db import models


def record_call(
    conn: Connection,
    *,
    model: str,
    provider: str,
    node: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: Decimal,
    shadow_cost_usd: Decimal,
    latency_ms: int,
    attempt: int = 1,
    succeeded: bool = True,
    error: str | None = None,
    invoice_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
    thread_id: str | None = None,
    agent: str | None = None,
    trace_id: str | None = None,
) -> int:
    stmt = (
        models.llm_calls.insert()
        .values(
            model=model,
            provider=provider,
            node=node,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            shadow_cost_usd=shadow_cost_usd,
            latency_ms=latency_ms,
            attempt=attempt,
            succeeded=succeeded,
            error=error,
            invoice_id=invoice_id,
            batch_id=batch_id,
            thread_id=thread_id,
            agent=agent,
            trace_id=trace_id,
        )
        .returning(models.llm_calls.c.id)
    )
    return int(conn.execute(stmt).scalar_one())

