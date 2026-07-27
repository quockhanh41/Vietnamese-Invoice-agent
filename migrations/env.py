"""Alembic environment.

Hai điều chỉnh so với template mặc định, cả hai đều là điều kiện sống còn cho
việc `app` và `langgraph` dùng chung một database (design doc §2.10):

1. **`include_object` loại schema `langgraph`.** Không có nó,
   `alembic revision --autogenerate` nhìn thấy `checkpoints`,
   `checkpoint_blobs`, `checkpoint_writes` (do `PostgresSaver.setup()` tạo,
   Alembic không hề biết) và sinh ra `DROP TABLE` cho từng bảng — tức là một
   lệnh migration vô tình xoá sạch mọi HITL đang chờ duyệt.
2. **`version_table_schema="app"`.** Bảng `alembic_version` phải nằm trong
   schema nghiệp vụ, không phải `public`.

Cả hai đã được verify với bảng checkpoint thật ở M1.6 (2026-07-27):
`tests/test_alembic_ignores_langgraph.py` chạy `alembic revision --autogenerate`
THẬT trên database đã có 4 bảng checkpoint và khẳng định không sinh lệnh DROP
nào. **Đừng sửa `include_object` mà không chạy lại test đó** — đo được: thay
thân hàm bằng `return True` thì autogenerate sinh ngay 5 lệnh `drop_table`.
Lý do đầy đủ ở ADR `0002-alembic-langgraph-schema-isolation.md`.

DSN đến từ `invoice_agent.config`, không phải `sqlalchemy.url` trong
alembic.ini — một nơi duy nhất đọc environment.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from invoice_agent.config import APP_SCHEMA, LANGGRAPH_SCHEMA, get_settings
from invoice_agent.db.models import metadata as app_metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# `app_sqlalchemy_url` chứ không phải `app_dsn`: SQLAlchemy đọc scheme để chọn
# driver, và `postgresql://` trần nghĩa là psycopg2 (không cài trong repo này).
# `%` trong query param phải được escape thành `%%` cho ConfigParser của Alembic.
config.set_main_option(
    "sqlalchemy.url", get_settings().app_sqlalchemy_url.replace("%", "%%")
)


# Trỏ vào metadata của schema `app`. KHÔNG để None: với None, autogenerate coi
# mọi bảng phản chiếu được là thừa và đề nghị DROP — kể cả bảng `app` hợp lệ,
# làm hỏng ý nghĩa của test cô lập schema (M1.6).
target_metadata = app_metadata



def include_object(object, name, type_, reflected, compare_to) -> bool:
    """Alembic không được nhìn vào lãnh thổ của LangGraph. Xem docstring module."""
    schema = getattr(object, "schema", None)
    if schema == LANGGRAPH_SCHEMA:
        return False
    return not (name == "alembic_version" and schema != APP_SCHEMA)




def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        include_schemas=True,
        version_table_schema=APP_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            include_schemas=True,
            version_table_schema=APP_SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
