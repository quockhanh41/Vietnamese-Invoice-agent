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

# Bảng đếm side-effect của spike HITL (M1.5). Mỗi lần một node chạy thì ghi
# thêm một row mang tên node đó.
#
# `node` không phải để cho đẹp — không có nó thì claim load-bearing của ADR
# 0002 không đo được. Sau `kill -9` + resume, đếm theo từng node phải ra:
#   n1_record (TRƯỚC cổng)  == 1  <- điều kiện CẦN của "không bao giờ post hai lần"
#   n2_gate   (CHỨA cổng)   == 2  <- node chứa interrupt() chạy lại TỪ ĐẦU
# Đo 2026-07-27, LangGraph 1.2.9. `trace` không thay được phép đo này: lần chạy
# đầu của `n2_gate` ném GraphInterrupt nên không commit gì, vậy nên `n2_gate`
# xuất hiện đúng một lần trong `trace` dù thân nó đã chạy hai lần.
spike_counter = Table(
    "spike_counter",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("thread_id", String(255), nullable=False, index=True),
    Column("node", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
