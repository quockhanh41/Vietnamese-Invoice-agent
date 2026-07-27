# Spec — HITL durability + cô lập schema Alembic (M1.5 + M1.6)

> **Ngày**: 2026-07-27 · **Phạm vi**: M1.5, M1.6 · **Trạng thái**: chờ duyệt
> Spec chị em: *XML extraction (M1.3 + M1.4)* — `2026-07-27-xml-extraction-design.md`, độc lập
> hoàn toàn với spec này.

## 1. Vì sao làm cái này, và vì sao làm TRƯỚC

Mục đích của M1 theo plan là **dồn rủi ro lên đầu**: "nếu XML sai hoặc PostgresSaver gây khó, biết ở
tuần 1 chứ không phải tuần 5". Tính đến 2026-07-27, rủi ro XML **đã trả xong** — 8 hóa đơn thật, 8
nhà cung cấp, luật validation đo được 8/8. Rủi ro còn lại nằm nguyên ở đây và chưa ai chạm vào.

Nó khác loại với rủi ro XML. XML sai thì sửa parser. **`interrupt()` + `PostgresSaver` mà không sống
qua `kill -9` thì cả thiết kế ba graph phải xem lại** — vì lý do tồn tại của việc cắt thread tại
ranh giới con người (§1.2 design doc) là state sống sót được ngoài vòng đời process. Đó là câu hỏi
phải trả lời trước khi xây thêm bất cứ thứ gì lên trên.

Đây cũng là artefact mạnh nhất của cả dự án theo plan: một terminal recording cho thấy graph dừng ở
`interrupt()`, process bị `kill -9`, khởi động lại, resume, hoàn tất. Khác biệt so với "confirm
dialog" trong các demo agent hời hợt — confirm dialog chỉ chạy được nếu người dùng đang ngồi chờ
ngay lúc đó.

## 2. Phạm vi

**Trong phạm vi:**

- `src/invoice_agent/checkpointer.py` — dựng `PostgresSaver` ở schema `langgraph`
- `src/invoice_agent/db/models.py` — SQLAlchemy metadata, **đúng một** bảng đếm side-effect
- Migration Alembic đầu tiên của repo (tạo bảng đếm đó)
- `migrations/env.py` — trỏ `target_metadata` vào metadata trên
- Graph 3 node dùng cho spike, sống trong `tests/`
- `tests/test_hitl_survives_restart.py` — 1 trong 6 test portfolio
- `tests/test_alembic_ignores_langgraph.py`
- `docs/adr/0002-alembic-langgraph-schema-isolation.md`

**Cố ý KHÔNG làm** (không phải thiếu sót):

- `recover_pending.py` — M5.5
- Bảng `approvals` / `journal_entries` thật — M2.4
- `graphs/approval.py` thật — M5.1. Spike **không thể** là graph thật vì `post_entry` cần bảng chưa
  tồn tại.
- ADR `0001-three-graphs-not-one.md` — đó là quyết định kiến trúc có từ trước, không phải phát hiện
  của spike này
- Terminal recording — quay ở M8 khi đã có gì đó đáng nhìn; spec này chỉ đảm bảo *có thể* quay

## 3. Kiến trúc

```
src/invoice_agent/
  checkpointer.py          SỐNG tới M5+.  build_checkpointer() -> PostgresSaver
                           Biết checkpointer_dsn (search_path=langgraph,public)
                           và biết phải gọi setup(). Không biết graph nào cả.
  db/models.py             SỐNG.  MetaData + bảng spike_counter (schema app):
                             id BIGSERIAL PK
                             thread_id TEXT NOT NULL      <- lọc theo lần chạy
                             created_at TIMESTAMPTZ DEFAULT now()

migrations/
  env.py                   target_metadata -> db.models.metadata
  versions/<rev>_*.py      migration đầu tiên: tạo app.spike_counter

tests/
  helpers/spike_graph.py   CHẾT ở M5.  Graph 3 node
  helpers/hitl_child.py    CHẾT ở M5.  Script chạy trong process CON
  test_hitl_survives_restart.py
  test_alembic_ignores_langgraph.py
```

**Vì sao `checkpointer.py` tách khỏi graph.** Nó là nơi duy nhất biết `checkpointer_dsn` và biết
phải gọi `setup()`. Để lẫn trong test thì M5 sẽ viết lại, và cấu hình test sẽ lệch cấu hình
production — đúng loại bug spec này sinh ra để chặn. Một việc, một interface, phụ thuộc duy nhất
`config.py`.

**Vì sao bảng đếm tạo bằng migration thật, không phải `CREATE TABLE` trong test.** 1.6 cần một
migration để chạy `--autogenerate` lên, và cần ít nhất một bảng ở `app` để chứng minh Alembic **vẫn
quản lý được `app`** trong khi **bỏ qua `langgraph`**. Một việc phục vụ hai mục đích.

## 4. Graph 3 node — và vì sao đúng ba

```
n1_record   ghi 1 row vào app.spike_counter        ứng với `suggest`        (§5.1)
n2_gate     interrupt({...})                       ứng với `approval_gate`
n3_finish   tiêu thụ giá trị resume, ghi kết quả   ứng với `apply_decision`
```

Ba node không phải con số tuỳ tiện. **Node chứa `interrupt()` chạy lại TỪ ĐẦU khi resume** — đó là
ngữ nghĩa của LangGraph, không phải bug. Nếu side effect nằm *cùng node* với `interrupt()`, nó sẽ
chạy hai lần. Tách `n1_record` khỏi `n2_gate` chính là để side effect nằm ở node **trước** cổng, nơi
nó không bị chạy lại.

Đây là kiến thức load-bearing cho M5: `post_entry` phải là node **riêng** sau `approval_gate`, và
`suggest` phải nằm **trước**. Bảng đếm là cách biến ngữ nghĩa đó thành một con số kiểm được.

## 5. `test_hitl_survives_restart.py`

Mỗi lần chạy dùng `thread_id = f"spike:{uuid4()}"` để các lần chạy không giẫm lên nhau.

```
1. cha: sinh thread_id mới (uuid4) -> không cần dọn trước, không thể va chạm
        đăng ký teardown: xoá row của thread_id này khi test kết thúc
2. cha: Popen(sys.executable, tests/helpers/hitl_child.py, thread_id)
3. con: build_checkpointer() -> graph -> invoke()
        n1_record ghi 1 row
        n2_gate  interrupt()  -> LangGraph checkpoint xuống Postgres
        con in "INTERRUPTED", rồi ngủ vô hạn
4. cha: POLL langgraph.checkpoints WHERE thread_id = ...   (timeout 30s)
5. cha: os.kill(pid, SIGKILL); wait()
        assert returncode == -9
6. cha: build_checkpointer() MỚI (connection mới, không tái dùng của bước 2)
        compile lại CÙNG định nghĩa graph với saver mới đó
        invoke(Command(resume={"decision": "approved"}), config)
7. assert kết quả cuối chứa giá trị resume
   assert SELECT count(*) FROM app.spike_counter WHERE thread_id=... == 1
```

**Bước 4 là toàn bộ khác biệt giữa xanh 10/10 và flaky.** Không có nó, cha có thể giết con *trước
khi* checkpoint kịp ghi; resume thất bại và ta đổ tội nhầm cho LangGraph trong khi lỗi ở test. Chỉ
giết sau khi tận mắt thấy state đã nằm trong Postgres.

**Bước 5 khẳng định `returncode == -9`, không chỉ "con đã chết".** Con tự thoát vì lỗi khác (import
sai, không nối được DB) mà test vẫn xanh thì test đó vô giá trị.

**Bước 7 là điều kiện cần của "không bao giờ post hai lần".** Nếu LangGraph replay node trước
interrupt, con số thành 2 và test đỏ.

### Ba chế độ hỏng, ba thông báo khác nhau

| Hỏng | Phát hiện bằng | Thông báo |
|---|---|---|
| Con chết sớm | `poll()` khác `None` trước bước 4 | Đọc stderr của con, fail kèm nguyên văn |
| Checkpoint không xuất hiện | Poll hết 30s | *"interrupt không checkpoint xuống Postgres"* — đây là phát hiện thật về framework, không phải lỗi test |
| Resume không được nhận | Kết quả còn khoá `__interrupt__` | *"resume bị bỏ qua"* |

### Số lần chạy

`@pytest.mark.parametrize("run", range(HITL_RUNS))` — 10 case riêng biệt, hỏng cái nào biết cái đó.

`HITL_RUNS` đọc từ env **lúc pytest collection** (vì `parametrize` chạy ở thì đó, không phải lúc
test chạy) — nghĩa là đổi biến giữa chừng không có tác dụng, phải đặt trước khi gọi pytest.
**Mặc định 3** (local, nhanh), **CI đặt 10**. Ước lượng 10 lần ≈ 20–50s.

**Chốt chặn**: một test riêng khẳng định `HITL_RUNS >= 10` khi `CI=true`. Không có nó, một ngày nào
đó ai đó sửa workflow, CI âm thầm chạy 3 lần, và gate "10/10" tự suy yếu mà không ai biết.

## 6. `test_alembic_ignores_langgraph.py`

Chạy `alembic.command.revision(autogenerate=True)` với `version_locations` trỏ vào `tmp_path`, đọc
file sinh ra, khẳng định **không có `drop_table`** và **không nhắc `langgraph`**. Dùng `tmp_path` để
test không bao giờ để rác lại trong `migrations/versions/`.

Điều kiện tiên quyết: DB phải đã có bảng checkpoint thật. Test này **phụ thuộc `PostgresSaver.setup()`
đã chạy** — nên nó gọi `build_checkpointer()` trước.

| Alembic nhìn thấy | Trong `target_metadata`? | Kỳ vọng |
|---|:---:|---|
| `app.spike_counter` | ✅ | không đổi |
| `langgraph.checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` | ❌ | **bị `include_object` lọc** → không DROP |
| `app.alembic_version` | — | Alembic tự loại |

**Test này mạnh nhất đúng lúc này**: `langgraph` có 4 bảng thật, `app` gần như trống. `include_object`
sai thì autogenerate sinh 4 lệnh `DROP TABLE` — một migration xoá sạch mọi HITL đang chờ duyệt.

`target_metadata` hiện là `None`; spec này đổi nó thành metadata của `db/models.py`. Đây là **mở
scope thật sự** (vốn thuộc M2.4), nhưng không có nó thì với `None`, autogenerate coi cả
`app.spike_counter` là thừa và đề nghị DROP — test đỏ vì một DROP hợp lệ, không phải vì `langgraph`.
Chỉ một bảng, không đụng bảng nghiệp vụ nào của M2.4.

## 7. Tiêu chí thành công (= gate M1 phần HITL)

| # | Điều kiện | Đo bằng |
|---|---|---|
| 1 | `test_hitl_survives_restart.py` xanh **10/10** | `HITL_RUNS=10 pytest tests/test_hitl_survives_restart.py` |
| 2 | Không side-effect lặp | `count(spike_counter) == 1` trong mỗi lần chạy |
| 3 | Con thật sự chết vì SIGKILL | `returncode == -9` |
| 4 | `--autogenerate` không sinh DROP nào | `test_alembic_ignores_langgraph.py` xanh |
| 5 | CI không thể âm thầm chạy thiếu | test chốt chặn `HITL_RUNS >= 10 khi CI=true` |
| 6 | `make check` vẫn xanh, ruff + mypy sạch | `make check` |

## 8. Phải xác minh trong lúc implement (chưa biết chắc)

Ghi ra để không giả vờ là đã biết:

1. **`PostgresSaver.setup()` có tạo bảng đúng schema `langgraph` không**, khi DSN mang
   `options=-csearch_path=langgraph,public`. Thiết kế dựa trên giả định này (§2.10 design doc). Nếu
   sai, phải truyền schema tường minh.
2. **Node chứa `interrupt()` chạy lại từ đầu khi resume** — tin là đúng theo tài liệu LangGraph
   1.2.9, và bảng đếm chính là phép đo. Nếu hoá ra node *trước* cổng cũng chạy lại, đó là phát hiện
   lớn và phải ghi vào ADR ngay, vì nó đổi cách thiết kế `post_entry` ở M5.
3. **`alembic.command.revision` với `version_locations=tmp_path`** có hoạt động khi `alembic.ini`
   trỏ `script_location` sang chỗ khác không. Nếu vướng, lùi về `alembic.autogenerate.compare_metadata()`
   — kém sát lời gate hơn nhưng kiểm đúng cơ chế `include_object`.
4. **Con ngủ vô hạn có bị pytest treo khi test fail giữa chừng không** — cần `try/finally` giết con
   trong mọi nhánh thoát.

## 9. ADR

`docs/adr/0002-alembic-langgraph-schema-isolation.md` — viết **ngay hôm test xanh**, không để sau.

Nội dung: vì sao `app` và `langgraph` chung một database mà phải tách schema; `--autogenerate` sẽ
sinh `DROP TABLE checkpoints` nếu không có `include_object`; `version_table_schema='app'`; và số đo
từ `test_alembic_ignores_langgraph.py`. Kèm phát hiện về ngữ nghĩa re-run của `interrupt()` nếu mục
8.2 cho kết quả khác dự đoán.
