"""tests/test_alembic_ignores_langgraph.py

`include_object` viết ở M0.8 nhưng tới giờ CHƯA TỪNG được kiểm, vì trước M1.5
không có bảng checkpoint nào tồn tại để nó lọc.

Test này mạnh nhất đúng lúc này: `langgraph` có 4 bảng thật, `app` gần như
trống. `include_object` sai thì autogenerate sinh 4 lệnh DROP TABLE — tức một
lệnh migration xoá sạch mọi HITL đang chờ duyệt.
"""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from invoice_agent.checkpointer import build_checkpointer
from invoice_agent.config import APP_SCHEMA, LANGGRAPH_SCHEMA, get_settings
from invoice_agent.db.models import metadata

LANGGRAPH_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
)


def _include_object(object, name, type_, reflected, compare_to) -> bool:
    """Cùng logic include_object trong migrations/env.py."""
    schema = getattr(object, "schema", None)
    if schema == LANGGRAPH_SCHEMA:
        return False
    return not (name == "alembic_version" and schema != APP_SCHEMA)



def test_autogenerate_never_drops_langgraph_tables(requires_postgres):
    # Điều kiện tiên quyết: bảng checkpoint phải TỒN TẠI THẬT thì phép thử mới
    # có nghĩa. Không có chúng, test xanh một cách vô nghĩa.
    with build_checkpointer() as saver:
        saver.setup()

    engine = create_engine(get_settings().app_sqlalchemy_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={"include_object": _include_object, "include_schemas": True},
        )
        diff = compare_metadata(ctx, metadata)

    drops = [d for d in diff if str(d[0]).startswith("remove_table")]
    assert not drops, f"autogenerate đề nghị DROP: {drops}"

    # Đảm bảo không có tuple nào trong diff nhắc tới các bảng langgraph
    for item in diff:
        item_str = str(item)
        for table in LANGGRAPH_TABLES:
            assert table not in item_str, f"autogenerate nhìn thấy bảng langgraph `{table}`: {item}"
