"""Vendor entity memory (design doc §2.2).

`ensure_vendor` writes a STUB only. Statistics are materialized at post/approve
inside a transaction (M4.3) rather than recomputed per node — design doc §2.2 is
explicit that recomputing in the hot path is too slow, and that "memory growing
over time" is the point being demonstrated.
"""

from typing import Any

from sqlalchemy import Connection, func, select
from sqlalchemy.dialects.postgresql import insert

from invoice_agent.db import models


def ensure_vendor(conn: Connection, tax_code: str, name: str | None = None) -> None:
    """Create on first sight; on later sightings touch `last_seen_at` ONLY.

    Never overwrite accumulated statistics here — a second invoice from a known
    vendor must not reset the memory built from the first hundred.
    """
    stmt = insert(models.vendors).values(tax_code=tax_code, name=name, last_seen_at=func.now())
    conn.execute(
        stmt.on_conflict_do_update(index_elements=["tax_code"], set_={"last_seen_at": func.now()})
    )


def get_vendor(conn: Connection, tax_code: str) -> dict[str, Any] | None:
    row = (
        conn.execute(select(models.vendors).where(models.vendors.c.tax_code == tax_code))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def update_profile(conn: Connection, tax_code: str, **fields: Any) -> None:
    """Materialize the derived profile. Callers pass only the fields they computed.

    `scripts/rebuild_vendor_profiles.py` (M4.3) recomputes all of this from
    `invoices` — the mandatory escape hatch for anything materialized from a
    derivable source (design doc §2.2).
    """
    if not fields:
        return
    conn.execute(
        models.vendors.update().where(models.vendors.c.tax_code == tax_code).values(**fields)
    )
