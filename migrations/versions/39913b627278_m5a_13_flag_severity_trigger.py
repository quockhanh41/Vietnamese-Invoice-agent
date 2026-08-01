"""m5a_13_flag_severity_trigger

Revision ID: 39913b627278
Revises: 9c82b5515db9
Create Date: 2026-08-01 13:06:18.428992

"""
from collections.abc import Sequence

from alembic import op

revision: str = "39913b627278"
down_revision: str | Sequence[str] | None = "9c82b5515db9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# WHY THIS NARROWS RATHER THAN TIGHTENS.
# Measured 2026-08-01, docs/notes/2026-08-01-m4d-batch-graph.md: routing on the
# mere existence of a finding sent 28 of 28 enriched invoices to a reviewer and
# left the auto path empty. The cause is NEW_VENDOR, severity `info`, which
# fires on every vendor's FIRST sighting — 51 of them across the corpus
# (docs/notes/2026-07-31-m4b-vendor-memory.md). `info` asserts something is NEW,
# which is true every time it fires, not that something is WRONG.
#
# Before M5-A this trigger was inert: batch.py raised flags only for duplicates
# and budgets, so the table it reads was nearly empty. M5-A persists every
# finding and signal (plan Task 7), at which point the trigger and the
# application's routing rule must agree or one of them is a bug that fires in
# production. They now both say: an open `warn` or `critical` flag blocks
# auto_clean.
_BODY_WITH_SEVERITY = """
CREATE OR REPLACE FUNCTION app.forbid_auto_clean_with_open_flags()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.posting_authority = 'auto_clean' AND EXISTS (
        SELECT 1 FROM app.invoice_flags
        WHERE invoice_id = NEW.invoice_id
          AND resolved_at IS NULL
          AND severity IN ('warn', 'critical')
    ) THEN
        RAISE EXCEPTION
            'invoice % has an open flag; auto_clean posting is forbidden',
            NEW.invoice_id;
    END IF;
    RETURN NEW;
END $$;
"""

_BODY_ANY_FLAG = """
CREATE OR REPLACE FUNCTION app.forbid_auto_clean_with_open_flags()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.posting_authority = 'auto_clean' AND EXISTS (
        SELECT 1 FROM app.invoice_flags
        WHERE invoice_id = NEW.invoice_id AND resolved_at IS NULL
    ) THEN
        RAISE EXCEPTION
            'invoice % has an open flag; auto_clean posting is forbidden',
            NEW.invoice_id;
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    # CREATE OR REPLACE only — the TRIGGER itself is unchanged and still points
    # at this function, so replacing the body is the whole migration.
    op.execute(_BODY_WITH_SEVERITY)


def downgrade() -> None:
    op.execute(_BODY_ANY_FLAG)
