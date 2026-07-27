"""spike_counter_node

Revision ID: ac97264e9f52
Revises: 4776f151f9e6
Create Date: 2026-07-27 09:59:16.676615

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ac97264e9f52'
down_revision: str | Sequence[str] | None = '4776f151f9e6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Hai chỗ sửa tay so với bản autogenerate, cả hai đều cần:
    #
    # 1. `DELETE` trước: cột `node` là NOT NULL và không có default. Bảng đếm
    #    của spike chỉ chứa row rác còn lại từ những lần chạy test trước, xoá
    #    là đúng — nhưng phải xoá TRƯỚC add_column, không thì ALTER TABLE bị
    #    Postgres từ chối vì không điền được giá trị cho row cũ.
    # 2. `schema="app"` tường minh: autogenerate BỎ nó ở lệnh add_column này
    #    (migration đầu tiên thì lại có). Không có nó, lệnh chạy vào schema đầu
    #    tiên của search_path — đúng chỉ vì `app_sqlalchemy_url` tình cờ đặt
    #    `app` trước. Không dựa vào sự tình cờ đó.
    op.execute("DELETE FROM app.spike_counter")
    op.add_column(
        'spike_counter',
        sa.Column('node', sa.String(length=32), nullable=False),
        schema='app',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('spike_counter', 'node', schema='app')
