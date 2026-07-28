"""Two engines, two privilege levels.

Repositories use `get_engine()` (role `invoice_app`, no UPDATE/DELETE on
audit_log). Alembic and `scripts/seed_reference.py` use `get_owner_engine()`
because both are administrative. Measured 2026-07-28 (spec §2.1): if runtime
used the owner, every REVOKE in this schema would be decorative.
"""

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from invoice_agent.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().runtime_sqlalchemy_url, pool_pre_ping=True)


@lru_cache
def get_owner_engine() -> Engine:
    return create_engine(get_settings().app_sqlalchemy_url, pool_pre_ping=True)
