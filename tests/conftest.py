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

pytest_plugins = ["tests.helpers.approval_fixtures"]


@pytest.fixture(scope="session", autouse=True)
def _reset_db_state(requires_postgres) -> None:  # type: ignore[misc]
    """Truncate mutable app tables before every test session.

    This makes the suite idempotent against runs that crashed before rollback
    could fire (e.g. SIGKILL, OOM, ad-hoc manual DB writes).  The owner role
    is needed for TRUNCATE CASCADE; the runtime role cannot do this.

    Only runs when Postgres is reachable (depends on `requires_postgres`).
    """
    from sqlalchemy import text

    from invoice_agent.db.engine import get_owner_engine

    engine = get_owner_engine()
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE app.invoices CASCADE"))
        conn.execute(
            text(
                "UPDATE app.vendors"
                " SET invoice_count=0, total_amount=0, sum_sq_amount=0,"
                "     avg_amount=NULL, stddev_amount=NULL"
                " WHERE invoice_count != 0"
                "    OR total_amount != 0"
                "    OR sum_sq_amount != 0"
            )
        )


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

    engine = get_owner_engine()
    conn = engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()


@pytest.fixture
def runtime_conn(requires_postgres):
    """Least-privilege connection — what repositories actually use in production."""
    from invoice_agent.db.engine import get_engine

    engine = get_engine()
    conn = engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()


@pytest.fixture
def sample_invoice():
    """A parsed real fixture — the arbiter this project uses for anything that
    must behave like a real invoice."""
    import pathlib

    from invoice_agent.extraction.xml_parser import parse_invoice_xml

    return parse_invoice_xml(
        pathlib.Path(
            "tests/fixtures/real_samples/viettelpost_gtgt_2.1.0.xml"
        ).read_bytes()
    )


@pytest.fixture
def three_mkt_invoices():
    """The corpus's budget case: three MKT invoices whose canonical order is the
    REVERSE of their invoice-number order, so a sorted implementation and an
    unsorted one give different answers (defects.py:163)."""
    import json
    import pathlib

    from invoice_agent.extraction.xml_parser import parse_invoice_xml

    manifest = pathlib.Path("corpus/manifest.jsonl")
    if not manifest.exists():
        pytest.skip("corpus not generated — run `make generate` proposed")
    rows = {r["invoice_id"]: r for r in map(json.loads, manifest.open(encoding="utf-8"))}
    out = []
    for inv_id in ("INV-0001", "INV-0002", "INV-0003"):
        record = rows[inv_id]
        out.append((inv_id, parse_invoice_xml(pathlib.Path(record["path"]).read_bytes())))
    return out


