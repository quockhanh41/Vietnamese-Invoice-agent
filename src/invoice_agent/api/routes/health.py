"""Liveness that actually checks the thing it claims to check."""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from invoice_agent.db.engine import get_engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> Any:
    """PINGS POSTGRES. A health check that does not touch the database reports
    healthy while the database is gone — worse than no health check, because a
    deploy pipeline believes it.

    `get_engine` is module-level so a test can replace it; that is also how the
    unreachable branch is exercised without stopping the container.
    """
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503, content={"status": "degraded", "database": "unreachable"}
        )
    return {"status": "ok", "database": "ok"}
