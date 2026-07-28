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
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

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

departments = Table(
    "departments",
    metadata,
    Column("code", sa.Text, primary_key=True),
    Column("name_vi", sa.Text, nullable=False),
)

expense_categories = Table(
    "expense_categories",
    metadata,
    Column("code", sa.Text, primary_key=True),
    Column("name_vi", sa.Text, nullable=False),
    Column("debit_account", sa.Text, nullable=False),
    Column("vat_account", sa.Text),
    Column("credit_account", sa.Text, nullable=False),
    # The non-LLM baseline (design doc §10.4). Vietnamese by design: it is
    # ingested data, not authoring style (CLAUDE.md language rule).
    Column("keywords_vi", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
)

budgets = Table(
    "budgets",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column("department_code", sa.Text, sa.ForeignKey("departments.code"), nullable=False),
    Column("period", sa.Text, nullable=False),  # YYYY-MM
    Column("category", sa.Text, sa.ForeignKey("expense_categories.code")),
    Column("amount_limit", sa.Numeric, nullable=False),
    Column("warn_threshold_pct", sa.Numeric, nullable=False, server_default="0.8"),
    sa.CheckConstraint("amount_limit > 0", name="ck_budgets_limit_positive"),
    sa.CheckConstraint(
        "warn_threshold_pct > 0 AND warn_threshold_pct <= 1",
        name="ck_budgets_warn_threshold_range",
    ),
    # NULLS NOT DISTINCT because `category IS NULL` MEANS "whole department"
    # and two such rows must not coexist. Measured 2026-07-28 (spec §2.2): a
    # plain UNIQUE admits both. This is the one place in the schema where a
    # NULL in a key is meaningful — `invoices` solves the same problem by
    # making seller_tax_code NOT NULL instead.
    sa.UniqueConstraint(
        "department_code", "period", "category",
        name="uq_budgets_department_period_category",
        postgresql_nulls_not_distinct=True,
    ),
)

vendors = Table(
    "vendors",
    metadata,
    # TEXT with no length constraint. Three real shapes measured 2026-07-28 on
    # the 8 fixtures: '0100000003' (10), '0300000018-042' (branch, with a dash)
    # and '050000000000' (12, household business). CHAR(10) rejects two of three.
    Column("tax_code", sa.Text, primary_key=True),
    Column("name", sa.Text),
    Column("normalized_name", sa.Text),
    Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_seen_at", sa.DateTime(timezone=True)),
    Column("invoice_count", sa.Integer, nullable=False, server_default="0"),
    Column("total_amount", sa.Numeric, nullable=False, server_default="0"),
    Column("avg_amount", sa.Numeric),
    Column("stddev_amount", sa.Numeric),
    Column("category_histogram", postgresql.JSONB, nullable=False, server_default="{}"),
    Column("modal_category", sa.Text),
    Column("modal_department", sa.Text),
    Column("rejection_count", sa.Integer, nullable=False, server_default="0"),
    Column("is_trusted", sa.Boolean, nullable=False, server_default=sa.false()),
    Column("reviewer_notes", sa.Text),
)

BATCH_STATUSES = ("running", "completed", "failed")

batches = Table(
    "batches",
    metadata,
    Column("id", sa.Uuid(as_uuid=True), primary_key=True),
    Column("label", sa.Text),
    Column("status", sa.Text, nullable=False, server_default="running"),
    Column("invoice_count", sa.Integer, nullable=False, server_default="0"),
    Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("completed_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint(
        "status IN ('running', 'completed', 'failed')", name="ck_batches_status"
    ),
)
