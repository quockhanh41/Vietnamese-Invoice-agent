"""m2a_01_runtime_role

Revision ID: ed4293988a59
Revises: ac97264e9f52
Create Date: 2026-07-28 18:22:03.089559

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ed4293988a59"
down_revision: str | Sequence[str] | None = "ac97264e9f52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Role creation lives in the migration, not scripts/init_db.sql: init_db.sql
    # runs only on first initialisation of a fresh volume, so every existing dev
    # volume would silently lack the role until a `make db-reset`, and CI would
    # need an extra step. DO block because Postgres has no CREATE ROLE IF NOT EXISTS.
    #
    # The password is a local-dev default matching docker-compose's `invoice:invoice`.
    # Deployment (M8) sets a real one out of band with ALTER ROLE.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'invoice_app') THEN
                CREATE ROLE invoice_app LOGIN PASSWORD 'invoice_app'
                    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
            END IF;
        END $$;
    """)
    op.execute("GRANT USAGE ON SCHEMA app TO invoice_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON SCHEMA app FROM invoice_app")
    op.execute("DROP ROLE IF EXISTS invoice_app")
