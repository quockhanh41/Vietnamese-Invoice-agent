"""tests/test_checkpointer.py"""

import psycopg

from invoice_agent.checkpointer import build_checkpointer

EXPECTED_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


class TestCheckpointerSchemaIsolation:
    """Bốn bảng của LangGraph phải hạ cánh ở schema `langgraph`, không phải
    `app` và không phải `public` — design doc §2.10. Sai chỗ thì Alembic
    autogenerate sẽ nhìn thấy chúng như bảng nghiệp vụ mồ côi."""

    def test_setup_creates_tables_in_langgraph_schema(self, requires_postgres):
        with build_checkpointer() as saver:
            saver.setup()

        with psycopg.connect(requires_postgres) as conn:
            rows = conn.execute(
                "SELECT schemaname, tablename FROM pg_tables "
                "WHERE tablename = ANY(%s)",
                (list(EXPECTED_TABLES),),
            ).fetchall()

        assert {t for _, t in rows} == EXPECTED_TABLES
        assert {s for s, _ in rows} == {"langgraph"}

    def test_yields_usable_saver(self, requires_postgres):
        with build_checkpointer() as saver:
            assert saver.list(None) is not None
