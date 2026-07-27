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
