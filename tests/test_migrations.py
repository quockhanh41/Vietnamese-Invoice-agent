"""tests/test_migrations.py"""

import psycopg


class TestSpikeCounterTable:
    """Bảng đếm side-effect phải do MIGRATION tạo, không phải CREATE TABLE trong
    test — vì Task 6 cần một bảng `app` nằm trong target_metadata để chứng minh
    Alembic vẫn quản lý `app` trong khi bỏ qua `langgraph`."""

    def test_table_exists_in_app_schema(self, requires_postgres):
        with psycopg.connect(requires_postgres) as conn:
            row = conn.execute(
                "SELECT schemaname FROM pg_tables WHERE tablename = 'spike_counter'"
            ).fetchone()
        assert row is not None, "chạy `make migrate` trước"
        assert row[0] == "app"

    def test_has_expected_columns(self, requires_postgres):
        with psycopg.connect(requires_postgres) as conn:
            cols = {
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='app' AND table_name='spike_counter'"
                )
            }
        assert {"id", "thread_id", "created_at"} <= cols
