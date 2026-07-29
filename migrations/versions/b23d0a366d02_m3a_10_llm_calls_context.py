"""m3a_10_llm_calls_context

Revision ID: b23d0a366d02
Revises: b25d8a939562
Create Date: 2026-07-29 08:06:49.121853

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b23d0a366d02'
down_revision: str | Sequence[str] | None = 'b25d8a939562'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Design doc §2.9's context columns, missing from migration 09.

    sa.Uuid(), not a literal CHAR(32): invoices.id and batches.id are declared
    sa.Uuid(as_uuid=True), which renders as CHAR(32) on this dialect. Declaring
    the raw type instead makes --autogenerate see a type change and turns the
    M1.6 empty-migration guard red.
    """
    op.add_column("llm_calls", sa.Column("invoice_id", sa.Uuid(), nullable=True), schema="app")
    op.add_column("llm_calls", sa.Column("batch_id", sa.Uuid(), nullable=True), schema="app")
    op.add_column("llm_calls", sa.Column("thread_id", sa.Text(), nullable=True), schema="app")
    op.add_column("llm_calls", sa.Column("agent", sa.Text(), nullable=True), schema="app")
    op.add_column("llm_calls", sa.Column("trace_id", sa.Text(), nullable=True), schema="app")
    op.create_foreign_key(
        "fk_llm_calls_invoice_id", "llm_calls", "invoices",
        ["invoice_id"], ["id"], source_schema="app", referent_schema="app",
    )
    op.create_foreign_key(
        "fk_llm_calls_batch_id", "llm_calls", "batches",
        ["batch_id"], ["id"], source_schema="app", referent_schema="app",
    )
    # No new GRANT: migration 09 granted SELECT, INSERT, UPDATE at TABLE level
    # to invoice_app, and table-level grants cover columns added later.


def downgrade() -> None:
    op.drop_constraint("fk_llm_calls_batch_id", "llm_calls", schema="app", type_="foreignkey")
    op.drop_constraint("fk_llm_calls_invoice_id", "llm_calls", schema="app", type_="foreignkey")
    for col in ("trace_id", "agent", "thread_id", "batch_id", "invoice_id"):
        op.drop_column("llm_calls", col, schema="app")

