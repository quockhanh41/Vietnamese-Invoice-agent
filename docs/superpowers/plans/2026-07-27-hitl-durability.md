# HITL Durability + Cô lập schema Alembic — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chứng minh bằng test chạy được rằng `interrupt()` + `PostgresSaver` sống sót qua `kill -9` mà không lặp side effect, và `alembic --autogenerate` không bao giờ đề nghị xoá bảng checkpoint.

**Architecture:** `checkpointer.py` trong `src/` (sống tới M5) dựng `PostgresSaver` ở schema `langgraph`; graph 3 node trong `tests/` (chết ở M5) đóng vai `suggest → approval_gate → apply_decision`. Test cha spawn process con thật, poll Postgres cho tới khi thấy checkpoint, rồi `SIGKILL`, rồi resume bằng checkpointer mới. Bảng đếm ở schema `app` là bằng chứng "không lặp side effect", đồng thời là bảng để 1.6 chứng minh Alembic vẫn quản lý `app` trong khi bỏ qua `langgraph`.

**Tech Stack:** Python 3.13 · LangGraph 1.2.9 · langgraph-checkpoint-postgres 3.1.0 · psycopg 3 · SQLAlchemy 2 · Alembic · Postgres 17 + pgvector · pytest

## Global Constraints

- Pin **chặt**, không bump: `langgraph==1.2.9`, `langgraph-checkpoint-postgres==3.1.0`. Re-pin là quyết định có chủ đích kèm `test_hitl_survives_restart.py` xanh.
- Python **3.13** (`python3.13`; python hệ thống 3.9.6 không đủ). Dùng `uv`, không `pip` trần.
- Tài liệu, comment giải thích "vì sao", commit message: **tiếng Việt**. Tên biến/hàm và docstring API: **tiếng Anh**.
- `make check` (ruff + mypy + pytest) phải xanh trước khi nói xong. Test cần Postgres → `make dev` trước.
- Mọi hằng số không hiển nhiên phải có comment ghi **ngày + phép đo** biện minh.
- Response model của agent **không được có** field approval/flag/status. (Không áp dụng trong plan này nhưng là ràng buộc thường trực.)
- Không mở scope ngoài spec. Ngoại lệ duy nhất đã duyệt: kéo `db/models.py` + `target_metadata` từ M2.4 về.

## Đã xác minh LIVE trước khi viết plan (2026-07-27)

Bốn mục "chưa biết chắc" ở §8 của spec — đã chạy thật trên Postgres 17, kết quả:

| Giả định | Kết quả |
|---|---|
| §8.1 `setup()` tạo bảng đúng schema `langgraph` | ✅ cả 4 bảng nằm ở `langgraph` |
| §8.2 node **trước** `interrupt()` không chạy lại khi resume | ✅ `trace = ['n1','n2','n3']`, `n1` đúng **1** lần |
| Kết quả sau interrupt có khoá `__interrupt__` | ✅ có |
| **BUG MỚI** — `config.py` sinh DSN psycopg không parse được | ❌ `ProgrammingError: extra key/value separator "=" in URI query parameter` |

Bug đó thành **Task 1**. Test M0 hiện có không bắt được vì chúng chỉ so chuỗi, chưa bao giờ mở kết nối.

---

### Task 1: Sửa bug encode DSN trong `config.py`

`_with_options()` nhét `options=-csearch_path=app,public` vào query string. Dấu `=` thứ hai làm psycopg từ chối parse. Alembic không lộ bug này vì SQLAlchemy tự dựng lại conninfo; nó chỉ cắn khi psycopg nối trực tiếp — tức đúng lúc `PostgresSaver` chạy.

**Files:**
- Modify: `src/invoice_agent/config.py` (hàm `_with_options`)
- Test: `tests/test_config.py` (thêm class mới), `tests/conftest.py` (thêm fixture)

**Interfaces:**
- Consumes: `Settings.app_dsn`, `Settings.checkpointer_dsn` (đã có từ M0)
- Produces: `requires_postgres` fixture trong `conftest.py` — dùng lại ở Task 2, 4, 5, 6

- [ ] **Step 1: Thêm fixture `requires_postgres` vào `tests/conftest.py`**

```python
# --- thêm vào cuối tests/conftest.py ---
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
```

- [ ] **Step 2: Viết test thất bại — test này KẾT NỐI THẬT, không so chuỗi**

```python
# --- thêm vào tests/test_config.py ---
import psycopg


class TestDsnActuallyConnects:
    """Hồi quy cho bug 2026-07-27: `options=-csearch_path=app,public` có hai dấu
    `=` trong một query param, psycopg từ chối parse. Test M0 cũ chỉ so chuỗi
    nên không bắt được — nó chỉ cắn khi psycopg nối TRỰC TIẾP, đúng lúc
    PostgresSaver chạy."""

    def test_app_dsn_connects_and_sets_search_path(self, requires_postgres, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", requires_postgres)
        with psycopg.connect(make_settings().app_dsn, connect_timeout=3) as conn:
            search_path = conn.execute("SHOW search_path").fetchone()[0]
        assert search_path == "app,public"

    def test_checkpointer_dsn_connects_and_sets_search_path(self, requires_postgres, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", requires_postgres)
        with psycopg.connect(make_settings().checkpointer_dsn, connect_timeout=3) as conn:
            search_path = conn.execute("SHOW search_path").fetchone()[0]
        assert search_path == "langgraph,public"
```

- [ ] **Step 3: Chạy test, xác nhận ĐỎ đúng lý do**

Run: `.venv/bin/python -m pytest tests/test_config.py::TestDsnActuallyConnects -v`
Expected: FAIL với `psycopg.ProgrammingError: extra key/value separator "=" in URI query parameter: "options"`

- [ ] **Step 4: Sửa `_with_options` bằng URL-encode**

```python
# src/invoice_agent/config.py — thêm import ở đầu file
from urllib.parse import quote
```

```python
# src/invoice_agent/config.py — thay thế _with_options
    def _with_options(self, options: str) -> str:
        # `quote(..., safe="")` là bắt buộc, không phải cho chắc: giá trị options
        # chứa dấu `=` (`-csearch_path=app,public`), và psycopg coi dấu `=` thứ
        # hai trong một query param là lỗi cú pháp — `ProgrammingError: extra
        # key/value separator "="`. Đã đo 2026-07-27: bản chưa encode nối được
        # qua SQLAlchemy (nó tự dựng lại conninfo) nhưng psycopg trực tiếp thì
        # không, tức là PostgresSaver chết còn Alembic vẫn chạy — kiểu bug chỉ
        # lộ ra ở đúng chỗ tệ nhất.
        separator = "&" if "?" in self.database_url else "?"
        return f"{self.database_url}{separator}options={quote(options, safe='')}"
```

- [ ] **Step 5: Chạy lại, xác nhận XANH**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS toàn bộ, gồm cả các test so chuỗi cũ.

Nếu test so chuỗi cũ (`test_app_and_checkpointer_use_different_search_paths`, `test_existing_query_string_is_preserved`, `test_public_is_always_on_the_path_for_pgvector`) đỏ vì chuỗi giờ đã encode, **sửa kỳ vọng của chúng** sang dạng encode:

```python
        assert s.app_dsn.endswith("?options=-csearch_path%3Dapp%2Cpublic")
        assert s.checkpointer_dsn.endswith("?options=-csearch_path%3Dlanggraph%2Cpublic")
```

```python
assert s.app_dsn == "postgresql://u:p@host/db?sslmode=require&options=-csearch_path%3Dapp%2Cpublic"
```

```python
        # public vẫn trên path, nhưng dấu phẩy giờ là %2C
        assert s.app_dsn.endswith("%2Cpublic")
        assert s.checkpointer_dsn.endswith("%2Cpublic")
```

- [ ] **Step 6: Xác nhận Alembic vẫn chạy được sau khi đổi**

Run: `.venv/bin/alembic upgrade head`
Expected: không lỗi. (SQLAlchemy chịu cả hai dạng — đã đo 2026-07-27.)

- [ ] **Step 7: Commit**

```bash
git add src/invoice_agent/config.py tests/test_config.py tests/conftest.py
git commit -m "fix(config): URL-encode options trong DSN — psycopg không parse được dấu = thứ hai

Phát hiện lúc verify spec M1.5: PostgresSaver nối trực tiếp bằng psycopg và
chết với ProgrammingError 'extra key/value separator \"=\"'. Alembic không lộ
bug vì SQLAlchemy tự dựng lại conninfo.

Test M0 cũ chỉ so chuỗi nên không thể bắt được. Thay bằng test KẾT NỐI THẬT
và khẳng định SHOW search_path trả đúng giá trị."
```

---

### Task 2: `checkpointer.py`

**Files:**
- Create: `src/invoice_agent/checkpointer.py`
- Test: `tests/test_checkpointer.py`

**Interfaces:**
- Consumes: `Settings.checkpointer_dsn` (Task 1)
- Produces: `build_checkpointer(settings: Settings | None = None) -> Iterator[PostgresSaver]` — context manager. Task 4, 5, 6 dùng.

- [ ] **Step 1: Viết test thất bại**

```python
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
                "SELECT schemaname, tablename FROM pg_tables WHERE tablename = ANY(%s)",
                (list(EXPECTED_TABLES),),
            ).fetchall()

        assert {t for _, t in rows} == EXPECTED_TABLES
        assert {s for s, _ in rows} == {"langgraph"}

    def test_yields_usable_saver(self, requires_postgres):
        with build_checkpointer() as saver:
            assert saver.list(None) is not None
```

- [ ] **Step 2: Chạy, xác nhận đỏ**

Run: `.venv/bin/python -m pytest tests/test_checkpointer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'invoice_agent.checkpointer'`

- [ ] **Step 3: Viết `checkpointer.py`**

```python
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
```

- [ ] **Step 4: Chạy, xác nhận xanh**

Run: `.venv/bin/python -m pytest tests/test_checkpointer.py -v`
Expected: PASS (2 test)

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/checkpointer.py tests/test_checkpointer.py
git commit -m "feat(checkpointer): PostgresSaver dựng đúng schema langgraph

Nơi duy nhất biết checkpoint sống ở đâu. M5 dùng lại nguyên cho
approval_graph thật, nên nó ở src/ chứ không phải tests/ — tránh cấu hình
test lệch cấu hình production."
```

---

### Task 3: `db/models.py` + migration đầu tiên + `target_metadata`

**Files:**
- Create: `src/invoice_agent/db/__init__.py`, `src/invoice_agent/db/models.py`
- Create: `migrations/versions/<rev>_spike_counter.py` (sinh bằng lệnh, không viết tay)
- Modify: `migrations/env.py` (dòng `target_metadata = None`)
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: `APP_SCHEMA` từ `invoice_agent.config`
- Produces: `invoice_agent.db.models.metadata` (SQLAlchemy `MetaData`), bảng `app.spike_counter` với cột `id`, `thread_id`, `created_at`. Task 4, 5, 6 dùng.

- [ ] **Step 1: Viết test thất bại**

```python
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
```

- [ ] **Step 2: Chạy, xác nhận đỏ**

Run: `.venv/bin/python -m pytest tests/test_migrations.py -v`
Expected: FAIL — `assert row is not None`

- [ ] **Step 3: Viết `db/models.py`**

```python
"""src/invoice_agent/db/__init__.py"""

from invoice_agent.db.models import metadata

__all__ = ["metadata"]
```

```python
"""src/invoice_agent/db/models.py

SQLAlchemy metadata cho schema nghiệp vụ `app`.

Hiện chỉ có MỘT bảng, và nó tồn tại để phục vụ spike HITL. M2.4 sẽ thêm các
bảng nghiệp vụ thật vào đúng metadata này.

Vì sao cần metadata ngay từ M1 dù bảng nghiệp vụ đến M2.4 mới có: với
`target_metadata = None`, Alembic autogenerate coi MỌI bảng phản chiếu được là
thừa và đề nghị DROP — kể cả bảng ở `app`. Lúc đó test cô lập schema ở M1.6 sẽ
đỏ vì một lệnh DROP hợp lệ chứ không phải vì `langgraph`, tức là mất hẳn ý
nghĩa phép thử.
"""

from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, func

from invoice_agent.config import APP_SCHEMA

metadata = MetaData(schema=APP_SCHEMA)

# Bảng đếm side-effect của spike HITL (M1.5). Mỗi lần node `n1_record` chạy thì
# ghi thêm một row. Sau `kill -9` + resume, số row của một thread_id phải vẫn là
# 1 — đó là điều kiện CẦN của "không bao giờ post hai lần".
spike_counter = Table(
    "spike_counter",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("thread_id", String(255), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
```

- [ ] **Step 4: Trỏ `target_metadata` trong `migrations/env.py`**

```python
# migrations/env.py — thêm vào khối import
from invoice_agent.db.models import metadata as app_metadata
```

```python
# migrations/env.py — thay dòng `target_metadata = None`
# Trỏ vào metadata của schema `app`. KHÔNG để None: với None, autogenerate coi
# mọi bảng phản chiếu được là thừa và đề nghị DROP — kể cả bảng `app` hợp lệ,
# làm hỏng ý nghĩa của test cô lập schema (M1.6).
target_metadata = app_metadata
```

- [ ] **Step 5: Sinh migration bằng lệnh**

Run:
```bash
.venv/bin/alembic revision --autogenerate -m "spike_counter"
```
Expected: sinh một file trong `migrations/versions/`. **Mở file đó ra đọc.** Nó phải chứa `op.create_table("spike_counter", ..., schema="app")` và **không được** chứa `op.drop_table` nào.

Nếu nó có `drop_table` nhắc `langgraph` → `include_object` hỏng, dừng lại và sửa `migrations/env.py` trước khi đi tiếp. Đó chính là thảm hoạ Task 6 sinh ra để chặn.

- [ ] **Step 6: Áp migration và chạy test**

Run:
```bash
.venv/bin/alembic upgrade head
.venv/bin/python -m pytest tests/test_migrations.py -v
```
Expected: PASS (2 test)

- [ ] **Step 7: Commit**

```bash
git add src/invoice_agent/db migrations/env.py migrations/versions tests/test_migrations.py
git commit -m "feat(db): metadata schema app + migration đầu tiên (spike_counter)

Kéo db/models.py + target_metadata từ M2.4 về M1 có chủ đích: với
target_metadata=None, autogenerate coi mọi bảng phản chiếu được là thừa và đề
nghị DROP, kể cả bảng app hợp lệ — test cô lập schema ở M1.6 sẽ đỏ vì lý do
sai. Chỉ một bảng, không đụng bảng nghiệp vụ nào của M2.4."
```

---

### Task 4: Graph 3 node + test resume trong cùng process

Trước khi đụng tới `kill -9`, chứng minh cơ chế cơ bản chạy được. Tách ra để reviewer có thể duyệt graph mà vẫn bác cơ chế giết process.

**Files:**
- Create: `tests/helpers/__init__.py`, `tests/helpers/spike_graph.py`
- Test: `tests/test_spike_graph.py`

**Interfaces:**
- Consumes: `build_checkpointer` (Task 2), `invoice_agent.db.models.spike_counter` (Task 3)
- Produces: `SpikeState` (TypedDict), `build_spike_graph() -> StateGraph`, `record_side_effect(thread_id: str) -> None`. Task 5 dùng.

- [ ] **Step 1: Viết test thất bại**

```python
"""tests/test_spike_graph.py"""

import uuid

import psycopg
from langgraph.types import Command

from invoice_agent.checkpointer import build_checkpointer
from tests.helpers.spike_graph import build_spike_graph


def _count(dsn: str, thread_id: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT count(*) FROM app.spike_counter WHERE thread_id = %s", (thread_id,)
        ).fetchone()[0]


class TestSpikeGraphInProcess:
    """Cơ chế cơ bản, chưa có kill -9. Task 5 mới thêm phần process chết."""

    def test_first_invoke_stops_at_interrupt(self, requires_postgres):
        thread_id = f"spike:{uuid.uuid4()}"
        config = {"configurable": {"thread_id": thread_id}}
        with build_checkpointer() as saver:
            saver.setup()
            app = build_spike_graph().compile(checkpointer=saver)
            result = app.invoke({"thread_id": thread_id, "trace": [], "decision": ""}, config)

        assert "__interrupt__" in result, "graph phải dừng ở interrupt()"
        assert result["trace"] == ["n1_record"], "chỉ node trước cổng được chạy"
        assert _count(requires_postgres, thread_id) == 1

    def test_resume_completes_and_does_not_repeat_side_effect(self, requires_postgres):
        thread_id = f"spike:{uuid.uuid4()}"
        config = {"configurable": {"thread_id": thread_id}}
        with build_checkpointer() as saver:
            saver.setup()
            app = build_spike_graph().compile(checkpointer=saver)
            app.invoke({"thread_id": thread_id, "trace": [], "decision": ""}, config)

        # checkpointer MỚI — mô phỏng process khác, dù vẫn cùng process Python
        with build_checkpointer() as saver2:
            app2 = build_spike_graph().compile(checkpointer=saver2)
            final = app2.invoke(Command(resume={"decision": "approved"}), config)

        assert "__interrupt__" not in final
        assert final["decision"] == "approved"
        assert final["trace"] == ["n1_record", "n2_gate", "n3_finish"]
        # Đo 2026-07-27: node TRƯỚC interrupt không chạy lại khi resume.
        # Node CHỨA interrupt thì có (nên phần trace của nó chỉ commit một lần).
        assert _count(requires_postgres, thread_id) == 1
```

- [ ] **Step 2: Chạy, xác nhận đỏ**

Run: `.venv/bin/python -m pytest tests/test_spike_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.helpers'`

- [ ] **Step 3: Viết graph**

```python
"""tests/helpers/__init__.py"""
```

```python
"""tests/helpers/spike_graph.py

Graph 3 node cho spike HITL (M1.5). ĐỒ CHƠI — sẽ bị xoá ở M5 khi
`graphs/approval.py` thật ra đời. Nó không thể LÀ graph thật vì `post_entry`
cần bảng `approvals`/`journal_entries` đến M2.4 mới có.

Vì sao đúng ba node, không phải một: **node chứa `interrupt()` chạy lại TỪ ĐẦU
khi resume** — đó là ngữ nghĩa LangGraph, không phải bug. Side effect nằm cùng
node với `interrupt()` sẽ chạy hai lần. Tách `n1_record` ra node RIÊNG phía
trước cổng là cách duy nhất để nó không bị lặp.

Đây là kiến thức load-bearing cho M5: `post_entry` phải là node riêng SAU
`approval_gate`, `suggest` phải nằm TRƯỚC. Ánh xạ sang §5.1 design doc:

    n1_record  <->  suggest
    n2_gate    <->  approval_gate
    n3_finish  <->  apply_decision
"""

import operator
from typing import Annotated, Any, TypedDict

import psycopg
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from invoice_agent.config import get_settings


class SpikeState(TypedDict):
    """State tối giản. `trace` dùng reducer `operator.add` nên mỗi node trả về
    DELTA (list một phần tử), không bao giờ trả cả list — trả cả list thì mỗi
    vòng nhân đôi, tăng trưởng bậc hai (bẫy đã ghi ở §3.1 design doc)."""

    thread_id: str
    trace: Annotated[list[str], operator.add]
    decision: str


def record_side_effect(thread_id: str) -> None:
    """Insert one row. Stand-in for the irreversible write M5 must never repeat."""
    with psycopg.connect(get_settings().database_url) as conn:
        conn.execute("INSERT INTO app.spike_counter (thread_id) VALUES (%s)", (thread_id,))


def n1_record(state: SpikeState) -> dict[str, Any]:
    record_side_effect(state["thread_id"])
    return {"trace": ["n1_record"]}


def n2_gate(state: SpikeState) -> dict[str, Any]:
    decision = interrupt(
        {
            "cau_hoi": "Duyệt hóa đơn này?",
            "thread_id": state["thread_id"],
        }
    )
    return {"trace": ["n2_gate"], "decision": decision["decision"]}


def n3_finish(state: SpikeState) -> dict[str, Any]:
    return {"trace": ["n3_finish"]}


def build_spike_graph() -> StateGraph:
    """Build the 3-node spike graph. Caller compiles it with a checkpointer."""
    graph = StateGraph(SpikeState)
    graph.add_node("n1_record", n1_record)
    graph.add_node("n2_gate", n2_gate)
    graph.add_node("n3_finish", n3_finish)
    graph.add_edge(START, "n1_record")
    graph.add_edge("n1_record", "n2_gate")
    graph.add_edge("n2_gate", "n3_finish")
    graph.add_edge("n3_finish", END)
    return graph
```

- [ ] **Step 4: Chạy, xác nhận xanh**

Run: `.venv/bin/python -m pytest tests/test_spike_graph.py -v`
Expected: PASS (2 test)

- [ ] **Step 5: Commit**

```bash
git add tests/helpers tests/test_spike_graph.py
git commit -m "test(spike): graph 3 node + resume trong cùng process

Đúng ba node vì node chứa interrupt() chạy lại TỪ ĐẦU khi resume — side effect
phải nằm ở node TRƯỚC cổng mới không bị lặp. Đã đo 2026-07-27: n1_record chạy
đúng 1 lần sau resume.

Kiến thức này chuyển thẳng sang M5: post_entry phải là node riêng SAU
approval_gate, suggest phải nằm TRƯỚC."
```

---

### Task 5: `test_hitl_survives_restart.py` — `kill -9` thật

**Files:**
- Create: `tests/helpers/hitl_child.py`
- Create: `tests/test_hitl_survives_restart.py`
- Modify: `.github/workflows/ci.yml` (thêm `HITL_RUNS: "10"` vào khối `env`)

**Interfaces:**
- Consumes: `build_spike_graph`, `SpikeState` (Task 4), `build_checkpointer` (Task 2)
- Produces: không có gì cho task sau

- [ ] **Step 1: Viết script chạy trong process CON**

```python
"""tests/helpers/hitl_child.py

Chạy trong process RIÊNG, để process cha giết bằng SIGKILL.

Phải là process thật (`sys.executable`), không phải thread hay fork dùng chung
bộ nhớ — cả điểm của test là chứng minh state sống sót khi process CHẾT, không
có `__del__`, không có flush lúc thoát, không có cache Python nào còn lại.

Dùng: python -m tests.helpers.hitl_child <thread_id>
In "INTERRUPTED" ra stdout (đã flush) khi graph dừng, rồi ngủ vô hạn chờ bị giết.
"""

import sys
import time

from invoice_agent.checkpointer import build_checkpointer
from tests.helpers.spike_graph import build_spike_graph


def main(thread_id: str) -> None:
    config = {"configurable": {"thread_id": thread_id}}
    with build_checkpointer() as saver:
        saver.setup()
        app = build_spike_graph().compile(checkpointer=saver)
        result = app.invoke({"thread_id": thread_id, "trace": [], "decision": ""}, config)
        if "__interrupt__" not in result:
            print("NO_INTERRUPT", flush=True)
            sys.exit(2)
        print("INTERRUPTED", flush=True)

    # Ngủ vô hạn. Process cha có trách nhiệm SIGKILL. Nếu cha chết trước thì
    # timeout của pytest dọn hộ.
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main(sys.argv[1])
```

- [ ] **Step 2: Viết test thất bại**

```python
"""tests/test_hitl_survives_restart.py

MỘT TRONG SÁU TEST PORTFOLIO. Nó chứng minh điều mà "confirm dialog" trong các
demo agent hời hợt không bao giờ chứng minh được: quyết định của con người có
thể đến sau khi process đã chết và được khởi động lại.
"""

import os
import signal
import subprocess
import sys
import time
import uuid

import psycopg
import pytest
from langgraph.types import Command

from invoice_agent.checkpointer import build_checkpointer
from tests.helpers.spike_graph import build_spike_graph

# Mặc định 3 cho vòng lặp local nhanh; CI đặt 10 để đóng gate M1.
# Đọc lúc pytest COLLECTION vì `parametrize` chạy ở thì đó — đổi biến giữa
# chừng không có tác dụng.
HITL_RUNS = int(os.getenv("HITL_RUNS", "3"))

# 30s: đo 2026-07-27, interrupt + checkpoint xuống Postgres local mất <1s.
# Để rộng vì CI chậm hơn máy dev, nhưng vẫn phải hữu hạn — treo mãi thì
# thông báo lỗi vô dụng.
CHECKPOINT_TIMEOUT_S = 30


def _count(dsn: str, thread_id: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT count(*) FROM app.spike_counter WHERE thread_id = %s", (thread_id,)
        ).fetchone()[0]


def _checkpoint_exists(dsn: str, thread_id: str) -> bool:
    with psycopg.connect(dsn) as conn:
        return (
            conn.execute(
                "SELECT count(*) FROM langgraph.checkpoints WHERE thread_id = %s",
                (thread_id,),
            ).fetchone()[0]
            > 0
        )


def _wait_for_checkpoint(dsn: str, thread_id: str, child: subprocess.Popen) -> None:
    """Đợi tới khi state THẬT SỰ nằm trong Postgres.

    Đây là toàn bộ khác biệt giữa test xanh 10/10 và test flaky. Không có bước
    này, cha có thể giết con TRƯỚC khi checkpoint kịp ghi; resume thất bại và ta
    đổ tội nhầm cho LangGraph trong khi lỗi nằm ở test.
    """
    deadline = time.monotonic() + CHECKPOINT_TIMEOUT_S
    while time.monotonic() < deadline:
        if child.poll() is not None:
            stdout, stderr = child.communicate()
            pytest.fail(
                f"process con chết sớm (rc={child.returncode}).\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        if _checkpoint_exists(dsn, thread_id):
            return
        time.sleep(0.2)
    child.kill()
    pytest.fail(
        f"interrupt() không checkpoint xuống Postgres trong {CHECKPOINT_TIMEOUT_S}s — "
        "đây là phát hiện về framework, không phải lỗi test"
    )


@pytest.mark.parametrize("run", range(HITL_RUNS))
def test_hitl_survives_restart(requires_postgres, run):
    thread_id = f"spike:{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}
    child = subprocess.Popen(
        [sys.executable, "-m", "tests.helpers.hitl_child", thread_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_checkpoint(requires_postgres, thread_id, child)

        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
        # -9 chứ không chỉ "đã chết": con tự thoát vì lỗi khác (import sai,
        # không nối được DB) mà test vẫn xanh thì test đó vô giá trị.
        assert child.returncode == -signal.SIGKILL, (
            f"con phải chết vì SIGKILL, nhận rc={child.returncode}"
        )

        assert _count(requires_postgres, thread_id) == 1, "n1_record phải đã chạy 1 lần"

        # Checkpointer HOÀN TOÀN MỚI — process cũ đã chết, không có gì tái dùng.
        with build_checkpointer() as saver:
            app = build_spike_graph().compile(checkpointer=saver)
            final = app.invoke(Command(resume={"decision": "approved"}), config)

        assert "__interrupt__" not in final, "resume bị bỏ qua"
        assert final["decision"] == "approved"
        assert final["trace"] == ["n1_record", "n2_gate", "n3_finish"]
        # Điều kiện CẦN của "không bao giờ post hai lần".
        assert _count(requires_postgres, thread_id) == 1, "side effect bị lặp sau resume"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        with psycopg.connect(requires_postgres) as conn:
            conn.execute("DELETE FROM app.spike_counter WHERE thread_id = %s", (thread_id,))


def test_ci_runs_the_full_ten():
    """Chốt chặn: không có test này, một ngày nào đó ai đó sửa workflow, CI âm
    thầm chạy 3 lần, và gate "10/10" tự suy yếu mà không ai biết."""
    if os.getenv("CI", "").lower() in ("true", "1"):
        assert HITL_RUNS >= 10, f"CI phải đặt HITL_RUNS>=10 để đóng gate M1, đang là {HITL_RUNS}"
```

- [ ] **Step 3: Chạy, xác nhận đỏ**

Run: `.venv/bin/python -m pytest tests/test_hitl_survives_restart.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.helpers.hitl_child'` hoặc lỗi từ `_wait_for_checkpoint`

- [ ] **Step 4: Chạy lại sau khi mọi file đã tại chỗ**

Run: `.venv/bin/python -m pytest tests/test_hitl_survives_restart.py -v`
Expected: PASS — 3 case `test_hitl_survives_restart` + 1 case `test_ci_runs_the_full_ten`

Nếu `ModuleNotFoundError: No module named 'tests'` khi chạy process con: thêm `env={**os.environ, "PYTHONPATH": str(Path(__file__).parent.parent)}` vào `subprocess.Popen`.

- [ ] **Step 5: Chạy đủ 10 lần như gate M1 đòi**

Run: `HITL_RUNS=10 .venv/bin/python -m pytest tests/test_hitl_survives_restart.py -v`
Expected: **10/10 PASS**. Ghi lại thời gian chạy để đối chiếu ước lượng 20–50s trong spec.

- [ ] **Step 6: Thêm `HITL_RUNS` vào CI**

```yaml
# .github/workflows/ci.yml — thêm vào khối `env:` của job `test`
      LLM_MODE: replay
      LANGFUSE_TRACING_ENABLED: "false"
      # Gate M1 đòi 10/10. Local mặc định 3 cho vòng lặp nhanh; CI chạy đủ.
      # `test_ci_runs_the_full_ten` chặn việc dòng này bị gỡ mà không ai biết.
      HITL_RUNS: "10"
```

- [ ] **Step 7: Commit**

```bash
git add tests/helpers/hitl_child.py tests/test_hitl_survives_restart.py .github/workflows/ci.yml
git commit -m "test(hitl): interrupt sống sót qua kill -9 thật — 1 trong 6 test portfolio

Process CON thật bị SIGKILL, không phải dựng lại checkpointer trong cùng
process. Chống flaky bằng cách POLL langgraph.checkpoints cho tới khi thấy
state đã nằm trong Postgres RỒI mới giết — không có bước đó thì cha có thể
giết trước khi checkpoint kịp ghi và ta đổ tội nhầm cho LangGraph.

assert returncode == -9, không chỉ 'con đã chết': con tự thoát vì lỗi khác mà
test vẫn xanh thì test đó vô giá trị.

Bảng đếm sau resume phải == 1 — điều kiện CẦN của 'không bao giờ post hai lần'.

HITL_RUNS: local 3, CI 10, kèm test chốt chặn để CI không thể âm thầm chạy
thiếu."
```

---

### Task 6: `test_alembic_ignores_langgraph.py`

**Files:**
- Create: `tests/test_alembic_ignores_langgraph.py`

**Interfaces:**
- Consumes: `build_checkpointer` (Task 2), `invoice_agent.db.models.metadata` (Task 3)
- Produces: không có gì cho task sau

- [ ] **Step 1: Viết test thất bại**

```python
"""tests/test_alembic_ignores_langgraph.py

`include_object` viết ở M0.8 nhưng tới giờ CHƯA TỪNG được kiểm, vì trước M1.5
không có bảng checkpoint nào tồn tại để nó lọc.

Test này mạnh nhất đúng lúc này: `langgraph` có 4 bảng thật, `app` gần như
trống. `include_object` sai thì autogenerate sinh 4 lệnh DROP TABLE — tức một
lệnh migration xoá sạch mọi HITL đang chờ duyệt.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config

from invoice_agent.checkpointer import build_checkpointer

LANGGRAPH_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
)


def test_autogenerate_never_drops_langgraph_tables(requires_postgres, tmp_path):
    # Điều kiện tiên quyết: bảng checkpoint phải TỒN TẠI THẬT thì phép thử mới
    # có nghĩa. Không có chúng, test xanh một cách vô nghĩa.
    with build_checkpointer() as saver:
        saver.setup()

    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    cfg = Config(str(Path("alembic.ini").resolve()))
    # tmp_path để test KHÔNG BAO GIỜ để rác lại trong migrations/versions/ —
    # một file migration lạc vào đó sẽ làm hỏng `alembic upgrade head` của repo.
    cfg.set_main_option("version_locations", str(versions_dir))

    command.revision(cfg, message="probe", autogenerate=True)

    generated = list(versions_dir.glob("*.py"))
    assert len(generated) == 1, f"kỳ vọng 1 file migration, có {len(generated)}"
    body = generated[0].read_text(encoding="utf-8")

    assert "drop_table" not in body, f"autogenerate đề nghị DROP — include_object hỏng.\n{body}"
    for table in LANGGRAPH_TABLES:
        assert table not in body, f"autogenerate nhìn thấy bảng langgraph `{table}`"
    assert "langgraph" not in body
```

- [ ] **Step 2: Chạy, quan sát kết quả THẬT**

Run: `.venv/bin/python -m pytest tests/test_alembic_ignores_langgraph.py -v`

Hai kết cục đều là thông tin có giá trị:
- **PASS** → `include_object` viết ở M0.8 đúng. Đi tiếp Step 4.
- **FAIL với `drop_table`** → guard hỏng, và ta vừa bắt được đúng thảm hoạ nó sinh ra để chặn. Sang Step 3.

- [ ] **Step 3: Chỉ làm nếu Step 2 ĐỎ — sửa `include_object`**

```python
# migrations/env.py
def include_object(object, name, type_, reflected, compare_to) -> bool:
    """Alembic không được nhìn vào lãnh thổ của LangGraph."""
    schema = getattr(object, "schema", None)
    if schema == LANGGRAPH_SCHEMA:
        return False
    # Bảng phản chiếu được mà không có schema tường minh: đối chiếu theo tên.
    if (
        reflected
        and type_ == "table"
        and name
        in {
            "checkpoints",
            "checkpoint_blobs",
            "checkpoint_writes",
            "checkpoint_migrations",
        }
    ):
        return False
    return True
```

Chạy lại Step 2 tới khi xanh.

Nếu `command.revision` báo lỗi về `version_locations` xung đột với `script_location` trong `alembic.ini`, lùi về `alembic.autogenerate.compare_metadata()`:

```python
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from invoice_agent.config import get_settings
    from invoice_agent.db.models import metadata

    engine = create_engine(get_settings().app_sqlalchemy_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={"include_object": include_object, "include_schemas": True},
        )
        diff = compare_metadata(ctx, metadata)

    drops = [d for d in diff if str(d[0]).startswith("remove_table")]
    assert not drops, f"autogenerate đề nghị DROP: {drops}"
```

Kém sát lời gate hơn (không chạy đúng lệnh CLI) nhưng kiểm đúng cơ chế `include_object`.

- [ ] **Step 4: Xác nhận `migrations/versions/` không bị rác**

Run: `git status --short migrations/versions/`
Expected: rỗng — không có file migration lạ nào.

- [ ] **Step 5: Chạy toàn bộ `make check`**

Run: `make check`
Expected: ruff sạch, mypy sạch, toàn bộ test xanh.

- [ ] **Step 6: Commit**

```bash
git add tests/test_alembic_ignores_langgraph.py migrations/env.py
git commit -m "test(alembic): --autogenerate không được đề nghị DROP bảng checkpoint

include_object viết ở M0.8 nhưng tới giờ chưa từng kiểm được vì chưa có bảng
checkpoint nào tồn tại. M1.5 tạo ra chúng thật, giờ mới verify được.

Test mạnh nhất đúng lúc này: langgraph có 4 bảng thật, app gần như trống.
include_object sai thì autogenerate sinh 4 lệnh DROP TABLE — một migration xoá
sạch mọi HITL đang chờ duyệt.

Dùng tmp_path cho version_locations để test không bao giờ để lại file migration
lạc trong migrations/versions/."
```

---

### Task 7: ADR 0002 + cập nhật trạng thái

**Files:**
- Create: `docs/adr/0002-alembic-langgraph-schema-isolation.md`
- Modify: `docs/Plan_Project_F_2026-07-25.md` (§1 và bảng §7)

**Interfaces:** không có

- [ ] **Step 1: Viết ADR**

```markdown
# ADR 0002 — Cô lập schema `langgraph` khỏi Alembic

**Ngày**: <ngày test xanh> · **Trạng thái**: chấp nhận
**Bối cảnh**: M1.6 · **Liên quan**: `migrations/env.py`, `tests/test_alembic_ignores_langgraph.py`

## Bối cảnh

`app` (system of record, Alembic sở hữu) và `langgraph` (checkpoint,
`PostgresSaver.setup()` sở hữu) sống chung một database Postgres. Hai chủ sở
hữu, một database.

Alembic không biết gì về bảng của LangGraph. Với `include_schemas=True`,
`--autogenerate` phản chiếu mọi schema, thấy `checkpoints`,
`checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` không có
trong `target_metadata`, và kết luận chúng là bảng mồ côi cần **DROP**.

Hệ quả nếu để nguyên: một lệnh `alembic revision --autogenerate` thường lệ sinh
ra migration xoá sạch **mọi HITL đang chờ duyệt**. Không mất dữ liệu nghiệp vụ,
nhưng mất toàn bộ công việc dở dang của người duyệt — và mất im lặng.

## Quyết định

1. `include_object` trong `migrations/env.py` trả `False` cho mọi object thuộc
   schema `langgraph`.
2. `version_table_schema="app"` — bảng `alembic_version` nằm trong schema
   nghiệp vụ, không phải `public`.
3. `target_metadata` trỏ vào `invoice_agent.db.models.metadata` chứ không phải
   `None`. Với `None`, autogenerate coi mọi bảng phản chiếu được là thừa và đề
   nghị DROP — kể cả bảng `app` hợp lệ, làm mất ý nghĩa phép thử.
4. Quyết định được **test bảo vệ**: `tests/test_alembic_ignores_langgraph.py`
   chạy autogenerate thật lên database đã có 4 bảng checkpoint, khẳng định
   không sinh `drop_table` nào.

## Hệ quả

- Alembic không bao giờ quản lý schema `langgraph`. Nâng cấp schema checkpoint
  là việc của `PostgresSaver.setup()`, gắn với version của
  `langgraph-checkpoint-postgres` — thêm một lý do pin chặt gói đó.
- Test yêu cầu Postgres thật và bảng checkpoint tồn tại. Không có chúng, test
  xanh một cách vô nghĩa; nên nó tự gọi `build_checkpointer().setup()` trước.

## Phát hiện kèm theo về ngữ nghĩa `interrupt()`

Đo 2026-07-27, LangGraph 1.2.9: **node chứa `interrupt()` chạy lại từ đầu khi
resume; node trước nó thì không.** Xác nhận bằng bảng đếm — sau `kill -9` +
resume, `count(spike_counter) == 1`.

Hệ quả cho M5: `post_entry` phải là node **riêng, sau** `approval_gate`, và
`suggest` phải nằm **trước**. Đặt write action cùng node với `interrupt()` sẽ
post hai lần.
```

Điền ngày thật vào chỗ `<ngày test xanh>` và bổ sung số đo thật nếu Step 2 của Task 6 phát hiện điều khác dự đoán.

- [ ] **Step 2: Cập nhật trạng thái M1 trong plan**

Trong `docs/Plan_Project_F_2026-07-25.md`, bảng §7, đổi dòng M1 thành:

```markdown
| M1 — XML thật + HITL spike | 🟡 | | ✅ 1.1 (8 file/8 NCC) · ✅ 1.2 · ✅ **1.5 HITL durability — `kill -9` 10/10, không lặp side effect** · ✅ **1.6 Alembic cô lập schema + ADR 0002** · 🔲 1.3 `xml_schema.py` · 🔲 1.4 `xml_parser.py` |
```

Và trong §1, thay nguyên đoạn bắt đầu bằng `**Chưa làm, theo đúng thứ tự**:` bằng:

```markdown
**M1.5 + 1.6 — xong.** `interrupt()` + `PostgresSaver` sống sót qua `kill -9` **10/10 lần**, và
side effect ở node trước cổng **không lặp** sau resume (`count == 1`). `alembic --autogenerate` trên
database đã có 4 bảng checkpoint **không sinh lệnh DROP nào**. ADR
`0002-alembic-langgraph-schema-isolation.md` đã viết. Phát hiện đo được, load-bearing cho M5: node
**chứa** `interrupt()` chạy lại từ đầu khi resume, node **trước** nó thì không — nên `post_entry`
bắt buộc phải là node riêng SAU `approval_gate`.

**Còn lại của M1**: 1.3 `xml_schema.py` · 1.4 `xml_parser.py` — viết theo 8 fixture thật, spec chưa
có, cần chạy brainstorming riêng.
```

- [ ] **Step 3: Chạy `make check` lần cuối**

Run: `make check`
Expected: tất cả xanh.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0002-alembic-langgraph-schema-isolation.md docs/Plan_Project_F_2026-07-25.md
git commit -m "docs(adr): 0002 cô lập schema langgraph khỏi Alembic

Viết ngay hôm test xanh, đúng nguyên tắc 'ghi ADR ngay hôm chạy được'.

Kèm phát hiện đo được về ngữ nghĩa interrupt(): node CHỨA interrupt chạy lại
từ đầu khi resume, node TRƯỚC nó thì không. Hệ quả cho M5: post_entry phải là
node riêng SAU approval_gate."
```

---

## Tiêu chí hoàn thành (= gate M1 phần HITL)

| # | Điều kiện | Lệnh |
|---|---|---|
| 1 | `test_hitl_survives_restart.py` xanh 10/10 | `HITL_RUNS=10 .venv/bin/python -m pytest tests/test_hitl_survives_restart.py` |
| 2 | Không lặp side effect | nằm trong test #1 (`count == 1`) |
| 3 | Con chết vì SIGKILL | nằm trong test #1 (`returncode == -9`) |
| 4 | `--autogenerate` không sinh DROP | `.venv/bin/python -m pytest tests/test_alembic_ignores_langgraph.py` |
| 5 | CI không thể chạy thiếu | `test_ci_runs_the_full_ten` |
| 6 | `make check` xanh | `make check` |
| 7 | `migrations/versions/` không có file lạc | `git status --short migrations/versions/` |
