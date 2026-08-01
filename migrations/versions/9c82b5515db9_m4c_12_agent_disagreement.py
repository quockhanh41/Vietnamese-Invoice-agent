"""m4c_12_agent_disagreement

Revision ID: 9c82b5515db9
Revises: f26e8ea62320
Create Date: 2026-08-01 07:25:16.465213

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c82b5515db9'
down_revision: Union[str, Sequence[str], None] = 'f26e8ea62320'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "('validation', 'taxcode', 'anomaly', 'budget', 'duplicate', 'vendor_memory')"
_NEW = (
    "('validation', 'taxcode', 'anomaly', 'budget', 'duplicate', "
    "'vendor_memory', 'agent_disagreement')"
)


def upgrade() -> None:
    op.drop_constraint("ck_flags_raised_by", "invoice_flags", schema="app")
    op.create_check_constraint(
        "ck_flags_raised_by", "invoice_flags", f"raised_by IN {_NEW}", schema="app"
    )


def downgrade() -> None:
    op.drop_constraint("ck_flags_raised_by", "invoice_flags", schema="app")
    op.create_check_constraint(
        "ck_flags_raised_by", "invoice_flags", f"raised_by IN {_OLD}", schema="app"
    )
