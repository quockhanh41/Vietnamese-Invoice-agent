"""Global test config — deterministic observability baseline.

Chạy TRƯỚC mọi import của test module (pytest load conftest.py sớm nhất).
`observability.py` quyết định bật/tắt tracing **ngay lúc import**, và
`config.py` đọc file `.env`, nên nếu không có mấy dòng dưới đây thì key
Langfuse thật của máy dev sẽ rò vào test session và test bắn trace lên cloud.

Trick copy từ Project A `tests/conftest.py` — pydantic-settings và
`load_dotenv()` đều KHÔNG override env var đã tồn tại, nên set ở đây là thắng.
"""

import os

os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
os.environ.pop("LANGFUSE_SECRET_KEY", None)


import pytest

from invoice_agent.config import get_settings


@pytest.fixture(scope="session")
def requires_postgres() -> str:
    """DSN nghiệp vụ, đã kiểm là nối được. Skip có lý do rõ ràng nếu chưa `make dev`.

    Fail thẳng thay vì để test đỏ với lỗi kết nối khó hiểu ở giữa chừng.
    """
    import psycopg

    dsn = get_settings().database_url
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            pass
    except Exception as exc:  # pragma: no cover - đường thoát môi trường
        pytest.skip(f"Cần Postgres đang chạy (`make dev`): {exc}")
    return dsn


@pytest.fixture
def owner_conn(requires_postgres):
    """Admin connection — Alembic and the seed script use this role."""
    from invoice_agent.db.engine import get_owner_engine

    with get_owner_engine().begin() as conn:
        yield conn


@pytest.fixture
def runtime_conn(requires_postgres):
    """Least-privilege connection — what repositories actually use in production."""
    from invoice_agent.db.engine import get_engine

    with get_engine().begin() as conn:
        yield conn


