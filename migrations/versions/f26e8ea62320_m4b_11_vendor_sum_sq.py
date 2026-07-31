"""m4b_11_vendor_sum_sq

Revision ID: f26e8ea62320
Revises: b23d0a366d02
Create Date: 2026-07-31 07:47:10.844705

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f26e8ea62320'
down_revision: Union[str, Sequence[str], None] = 'b23d0a366d02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vendors",
        sa.Column("sum_sq_amount", sa.Numeric(), nullable=False, server_default="0"),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("vendors", "sum_sq_amount", schema="app")

