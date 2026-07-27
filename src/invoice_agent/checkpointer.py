"""PostgresSaver dựng đúng schema — nơi DUY NHẤT biết checkpoint sống ở đâu.

Tách khỏi định nghĩa graph có chủ đích: M5 sẽ dùng lại nguyên hàm này cho
`approval_graph` thật. Nếu để logic dựng saver nằm trong test, M5 sẽ viết lại
và cấu hình test sẽ lệch cấu hình production — đúng loại bug mà cả spike HITL
sinh ra để chặn.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver

from invoice_agent.config import Settings, get_settings


@contextmanager
def build_checkpointer(settings: Settings | None = None) -> Iterator[PostgresSaver]:
    """Yield a PostgresSaver bound to the `langgraph` schema.

    The DSN carries `options=-csearch_path=langgraph,public`, so every table
    `setup()` creates lands in `langgraph` — never in the business schema.
    Caller is responsible for calling `setup()` once per deployment.
    """
    s = settings or get_settings()
    with PostgresSaver.from_conn_string(s.checkpointer_dsn) as saver:
        yield saver
