# ADR 0002 — Cô lập schema `langgraph` khỏi Alembic

**Ngày**: 2026-07-27 · **Trạng thái**: chấp nhận
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
