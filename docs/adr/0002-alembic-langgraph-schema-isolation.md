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
   gọi `alembic revision --autogenerate` thật — tức là nạp `migrations/env.py`
   và dùng đúng `include_object` của production — lên database đã có 4 bảng
   checkpoint, rồi khẳng định file sinh ra không chứa `drop_table` nào.

   Chi tiết này là *nội dung* của quyết định, không phải tiểu tiết triển khai.
   Bản test đầu tiên tự chép lại logic lọc vào trong test rồi gọi
   `compare_metadata`; đo 2026-07-27: thay thân `include_object` trong `env.py`
   bằng `return True` thì bản đó vẫn **XANH**, trong khi autogenerate thật sinh
   ra `drop_table` cho `checkpoints`, `checkpoint_writes`, `checkpoint_blobs`,
   `checkpoint_migrations` **và** `alembic_version`. Một test chép lại logic
   của thứ nó bảo vệ thì nó bảo vệ bản chép, không bảo vệ production.

## Hệ quả

- Alembic không bao giờ quản lý schema `langgraph`. Nâng cấp schema checkpoint
  là việc của `PostgresSaver.setup()`, gắn với version của
  `langgraph-checkpoint-postgres` — thêm một lý do pin chặt gói đó.
- Test yêu cầu Postgres thật và bảng checkpoint tồn tại. Không có chúng, test
  xanh một cách vô nghĩa; nên nó tự gọi `build_checkpointer().setup()` trước.

## Phát hiện kèm theo về ngữ nghĩa `interrupt()`

Đo 2026-07-27, LangGraph 1.2.9: **node chứa `interrupt()` chạy lại từ đầu khi
resume; node trước nó thì không.** Cả hai node của spike đều ghi vào bảng đếm,
nên đây là hai con số chứ không phải một câu khẳng định — sau `kill -9` +
resume, qua ranh giới process:

| node | vị trí so với cổng | số row |
|---|---|---:|
| `n1_record` | TRƯỚC | **1** |
| `n2_gate` | CHỨA `interrupt()` | **2** |

`trace` không thay được phép đo này, và đó là lý do phải có bảng đếm: lần chạy
đầu của `n2_gate` ném `GraphInterrupt` nên không commit gì, vậy nên `n2_gate`
xuất hiện đúng **một** lần trong `trace` dù thân nó đã chạy **hai** lần.

Hệ quả cho M5: `post_entry` phải là node **riêng, sau** `approval_gate`, và
`suggest` phải nằm **trước**. Đặt write action cùng node với `interrupt()` sẽ
post hai lần.

## Tách node là ĐIỀU KIỆN CẦN, không phải điều kiện đủ

Đừng đọc mục trên thành "tách node xong là an toàn". Đo 2026-07-27, cùng
LangGraph 1.2.9: `kill -9` **ở giữa node sau cổng** — sau khi nó đã ghi row,
trước khi checkpoint của nó commit — rồi resume từ process mới, thì node đó
**chạy lại và ghi row thứ hai**. Kết quả: **2 row**, tức là hai bút toán.

Vị trí node chỉ chặn được việc lặp do `interrupt()`. Nó không chặn được việc
lặp do crash *trong lúc* write. Ba lớp của safety property mới là thứ chặn,
và cả ba đều thuộc milestone sau:

- `UNIQUE(seller_tax_code, template_no, invoice_series, invoice_number)` trên
  `journal_entries` (M2.4) — lớp database, lớp duy nhất crash không vượt qua được.
- guard `create_journal_entry` đọc lại Postgres xác nhận `approvals` row đã
  persist (M5.3).
- `scripts/recover_pending.py` (M5.5) phải chịu được việc resume một thread mà
  bút toán của nó có thể đã được ghi.

Nói cách khác: `post_entry` phải **idempotent**, không chỉ **đặt đúng chỗ**.
Ghi ở đây để M5 không thừa hưởng một kết luận đúng một nửa.
