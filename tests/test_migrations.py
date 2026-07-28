"""tests/test_migrations.py"""

import os
import pathlib

import psycopg
from alembic import command
from alembic.config import Config

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_autogenerate_emits_an_empty_migration(requires_postgres, tmp_path):
    """`db/models.py` and `migrations/versions/` must agree EXACTLY.

    This is M2-A success criterion 2, and it had no test until 2026-07-28 —
    which is precisely why it was broken and nobody noticed. Measured then:
    Alembic connected with `-csearch_path=app,public`, treated `app` as the
    connection's default schema, reflected its tables under the `None` key, and
    emitted **32 phantom diffs** (16 `remove_fk` + 16 `add_fk`) against a
    database that exactly matched its migrations:

        remove_fk  table: None.approvals -> referred None.invoices
        add_fk     table: app.approvals  -> referred app.invoices

    Two real consequences, which is why this is a test and not a preference.
    The emitted `op.drop_constraint` calls omit `schema='app'`, so running them
    unedited fails; and genuine schema drift hides inside 30 lines of noise,
    inverting the purpose of the M1.6 isolation guard next door. Fixed by
    `Settings.alembic_sqlalchemy_url`, which keeps `app` out of Alembic's
    search_path. Break that and this test goes red.

    Distinct from `test_alembic_ignores_langgraph.py`: that one asserts nothing
    is DROPPED, this one asserts nothing is emitted AT ALL.
    """
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    # Both directories, same reason as the M1.6 guard: with only tmp_path,
    # Alembic cannot locate the revision `app.alembic_version` currently holds.
    cfg.set_main_option(
        "version_locations",
        f"{REPO_ROOT / 'migrations' / 'versions'}{os.pathsep}{versions_dir}",
    )
    # `version_path` keeps the probe out of migrations/versions/ — a stray file
    # there breaks `alembic upgrade head` for the whole repo.
    command.revision(cfg, message="probe", autogenerate=True, version_path=str(versions_dir))

    body = next(versions_dir.glob("*.py")).read_text(encoding="utf-8")
    upgrade = body[body.index("def upgrade") : body.index("def downgrade")]
    operations = [line.strip() for line in upgrade.splitlines() if line.strip().startswith("op.")]
    assert not operations, (
        "autogenerate is not clean — models.py and migrations/versions/ disagree:\n"
        + "\n".join(operations)
    )


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
