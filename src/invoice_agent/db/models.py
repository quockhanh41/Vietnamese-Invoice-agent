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

import sqlalchemy as sa
from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, func
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
        "department_code",
        "period",
        "category",
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
    # Sum of squared amounts. With `invoice_count` and `total_amount` this is
    # the EXACT state of the profile; avg and stddev are derived from the three
    # by `memory.vendor_profile.derive`. Stored rather than recomputed so the
    # incremental update stays O(1) (design §2.2), and stored as the raw
    # accumulator rather than as Welford's M2 so the rebuild can compute the
    # identical value by the identical expression — which is what makes M4-B's
    # zero-drift criterion exact instead of tolerance-based.
    Column("sum_sq_amount", sa.Numeric, nullable=False, server_default="0"),
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
    sa.CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_batches_status"),
)

# Predicted from design doc §3.3's routing, NOT measured — M3-M5 will amend it.
# TEXT + CHECK (rather than a native ENUM) is exactly what makes amending it
# cheap: measured 2026-07-28, a native enum cannot add a value and backfill
# rows to it in one migration ("unsafe use of new value").
INVOICE_STATUSES = (
    "received",
    "extracted",
    "validated",
    "classified",
    "flagged",
    "pending_approval",
    "approved",
    "rejected",
    "posted",
    "manual_queue",
    "failed",
)
SOURCE_TYPES = ("xml", "pdf_vision", "manual")

invoices = Table(
    "invoices",
    metadata,
    Column("id", sa.Uuid(as_uuid=True), primary_key=True),
    Column("batch_id", sa.Uuid(as_uuid=True), sa.ForeignKey("batches.id")),
    Column("template_no", sa.Text, nullable=False),  # KHMSHDon
    Column("invoice_series", sa.Text, nullable=False),  # KHHDon
    Column("invoice_number", sa.Text, nullable=False),  # SHDon
    # NOT NULL although InvoiceData types it `str | None`. Measured 2026-07-28:
    # seller MST is present in 8/8 real fixtures. An invoice without one cannot
    # be given an identity, so ingestion routes it to manual_queue rather than
    # storing it identity-less. The parser is still right to report what it saw;
    # this is the boundary that refuses.
    Column("seller_tax_code", sa.Text, sa.ForeignKey("vendors.tax_code"), nullable=False),
    # Nullable and in no key: measured 8/8 either absent or ''.
    Column("buyer_tax_code", sa.Text),
    Column("issue_date", sa.Date, nullable=False),
    Column("currency", sa.Text),
    Column("exchange_rate", sa.Numeric),
    Column("subtotal", sa.Numeric),
    Column("vat_amount", sa.Numeric),
    Column("total_amount", sa.Numeric, nullable=False),
    Column("total_in_words", sa.Text),
    # Header TTKhac bags. Stored, not dropped: ext already proved load-bearing
    # once — VNPT sets ThTien=0 and puts the real amount in ext['Amount'].
    Column("ext", postgresql.JSONB, nullable=False, server_default="{}"),
    Column("source_type", sa.Text, nullable=False),
    Column("source_path", sa.Text),
    Column("raw_xml", sa.Text),
    Column("extraction_confidence", sa.Numeric),
    Column("extraction_attempts", sa.Integer, nullable=False, server_default="0"),
    Column("category", sa.Text, sa.ForeignKey("expense_categories.code")),
    Column("department", sa.Text, sa.ForeignKey("departments.code")),
    Column("classification_confidence", sa.Numeric),
    Column("status", sa.Text, nullable=False, server_default="received"),
    Column("cost_usd", sa.Numeric),
    Column("shadow_cost_usd", sa.Numeric),
    Column("tokens_in", sa.Integer),
    Column("tokens_out", sa.Integer),
    Column("langfuse_trace_id", sa.Text),
    Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    # THE identity of a Vietnamese e-invoice (design doc §2.3). invoice_number
    # alone is NOT unique across sellers — two vendors both issue no. 00001234.
    sa.UniqueConstraint(
        "seller_tax_code",
        "template_no",
        "invoice_series",
        "invoice_number",
        name="uq_invoices_natural_key",
    ),
    sa.CheckConstraint("status IN " + str(INVOICE_STATUSES), name="ck_invoices_status"),
    sa.CheckConstraint("source_type IN " + str(SOURCE_TYPES), name="ck_invoices_source_type"),
)

invoice_line_items = Table(
    "invoice_line_items",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column(
        "invoice_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    ),
    # NOT NULL although InvoiceLine.line_no is `int | None`. STT was present on
    # 15/15 lines across 8/8 fixtures (2026-07-28); where absent the repository
    # substitutes 1-based document position. One column, not two: a line index
    # is an ordinal, not a fact about the invoice.
    Column("line_no", sa.Integer, nullable=False),
    # ⚠️ PROMPT-INJECTION SURFACE (design doc §2.4, §9.1). Free text written by
    # whoever issued the invoice. Never concatenate into a system prompt —
    # M3.3 wraps it in an escaped <invoice_data> block.
    Column("description", sa.Text),
    Column("unit", sa.Text),
    Column("quantity", sa.Numeric),
    Column("unit_price", sa.Numeric),
    Column("amount", sa.Numeric),
    Column("vat_rate", sa.Numeric),
    Column("vat_amount", sa.Numeric),
    # QĐ-1450 TChat: 1 goods, 2 promotion, 3 discount, 4 NOTE.
    # Nullable, NOT defaulted to 1 — that waits on M1.2's outstanding
    # NĐ 70/2025 check. A wrong default folds a note line into the money sum.
    Column("item_kind", sa.Integer),
    Column("ext", postgresql.JSONB, nullable=False, server_default="{}"),
    sa.UniqueConstraint("invoice_id", "line_no", name="uq_line_items_invoice_line_no"),
)

# Deterministic producers ONLY. This list is the DDL encoding of principle 1
# ("the LLM never raises a flag"): adding an agent here requires a migration,
# so it cannot happen by accident in a node. Design doc §2.5.
FLAG_SOURCES = ("validation", "taxcode", "anomaly", "budget", "duplicate", "vendor_memory")
FLAG_SEVERITIES = ("info", "warn", "critical")

invoice_flags = Table(
    "invoice_flags",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column(
        "invoice_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("code", sa.Text, nullable=False),
    Column("severity", sa.Text, nullable=False),
    Column("detail", postgresql.JSONB, nullable=False, server_default="{}"),
    Column("raised_by", sa.Text, nullable=False),
    # Open == NULL. The journal-entry trigger reads this on every insert.
    Column("resolved_at", sa.DateTime(timezone=True)),
    Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    sa.CheckConstraint("severity IN " + str(FLAG_SEVERITIES), name="ck_flags_severity"),
    sa.CheckConstraint("raised_by IN " + str(FLAG_SOURCES), name="ck_flags_raised_by"),
    sa.Index(
        "ix_flags_open_by_invoice",
        "invoice_id",
        postgresql_where=sa.text("resolved_at IS NULL"),
    ),
)

APPROVAL_DECISIONS = ("approved", "rejected", "edited")

approvals = Table(
    "approvals",
    metadata,
    Column("id", sa.Uuid(as_uuid=True), primary_key=True),
    Column("invoice_id", sa.Uuid(as_uuid=True), sa.ForeignKey("invoices.id"), nullable=False),
    # The approval_graph thread. Convention `appr:{invoice_id}` (design doc §1.3).
    Column("thread_id", sa.Text, nullable=False),
    Column("interrupt_payload", postgresql.JSONB, nullable=False, server_default="{}"),
    Column("flags_snapshot", postgresql.JSONB, nullable=False, server_default="{}"),
    Column("suggestions", postgresql.JSONB, nullable=False, server_default="{}"),
    Column("decision", sa.Text),
    Column("field_corrections", postgresql.JSONB),
    Column("reason", sa.Text),
    Column("decided_by", sa.Text),
    Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("decided_at", sa.DateTime(timezone=True)),
    # Time-to-approve, computed by Postgres. Verified 2026-07-28: a row 90s
    # apart yields 90. Application code never computes it, so it cannot drift.
    Column(
        "latency_seconds",
        sa.Numeric,
        sa.Computed("EXTRACT(EPOCH FROM (decided_at - requested_at))", persisted=True),
    ),
    sa.CheckConstraint(
        "decision IS NULL OR decision IN " + str(APPROVAL_DECISIONS),
        name="ck_approvals_decision",
    ),
    sa.Index("ix_approvals_thread_id", "thread_id"),
    # Exactly the query scripts/recover_pending.py runs at M5.5.
    sa.Index(
        "ix_approvals_pending",
        "invoice_id",
        postgresql_where=sa.text("decision IS NULL"),
    ),
)

JOURNAL_STATUSES = ("draft", "posted", "void")
POSTING_AUTHORITIES = ("auto_clean", "human_approved")

journal_entries = Table(
    "journal_entries",
    metadata,
    Column("id", sa.Uuid(as_uuid=True), primary_key=True),
    # UNIQUE: one invoice can never be posted twice. Measured at M1.5 that a
    # `kill -9` inside a post-gate node makes it run again on resume and write
    # a SECOND row — graph shape does not prevent that, this constraint does.
    Column(
        "invoice_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("invoices.id"),
        nullable=False,
        unique=True,
    ),
    Column("entry_date", sa.Date, nullable=False),
    Column("status", sa.Text, nullable=False, server_default="draft"),
    Column("posting_authority", sa.Text, nullable=False),
    Column("approval_id", sa.Uuid(as_uuid=True), sa.ForeignKey("approvals.id")),
    Column("posted_at", sa.DateTime(timezone=True)),
    Column("posted_by", sa.Text),
    Column("total_debit", sa.Numeric, nullable=False),
    Column("total_credit", sa.Numeric, nullable=False),
    Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    sa.CheckConstraint("total_debit = total_credit", name="ck_journal_balanced"),
    # Without this, 'human_approved' is just a string the application writes.
    sa.CheckConstraint(
        "posting_authority <> 'human_approved' OR approval_id IS NOT NULL",
        name="ck_journal_human_approved_needs_approval",
    ),
    sa.CheckConstraint("status IN " + str(JOURNAL_STATUSES), name="ck_journal_status"),
    sa.CheckConstraint(
        "posting_authority IN " + str(POSTING_AUTHORITIES), name="ck_journal_authority"
    ),
)

journal_entry_lines = Table(
    "journal_entry_lines",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column(
        "journal_entry_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("journal_entries.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("line_no", sa.Integer, nullable=False),
    Column("account_code", sa.Text, nullable=False),
    Column("account_name", sa.Text, nullable=False),
    Column("debit", sa.Numeric, nullable=False, server_default="0"),
    Column("credit", sa.Numeric, nullable=False, server_default="0"),
    Column("description", sa.Text),
    sa.UniqueConstraint("journal_entry_id", "line_no", name="uq_journal_lines_entry_line_no"),
    sa.CheckConstraint("debit = 0 OR credit = 0", name="ck_journal_line_one_side"),
    # Beyond the design doc, and load-bearing: debit=-100/credit=0 satisfies the
    # constraint above, so without this a "balanced" entry can be built entirely
    # from negative amounts.
    sa.CheckConstraint("debit >= 0 AND credit >= 0", name="ck_journal_line_non_negative"),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    # 'system' | 'human:<name>' | 'mcp:<client>' (M7.2 writes the third form).
    Column("actor", sa.Text, nullable=False),
    Column("action", sa.Text, nullable=False),
    Column("target_table", sa.Text),
    Column("target_id", sa.Text),
    # Grants AND refusals. A refusal-only log cannot distinguish "nothing
    # happened" from "the writer was never called".
    Column("allowed", sa.Boolean, nullable=False),
    Column("reason", sa.Text),
    Column("detail", postgresql.JSONB, nullable=False, server_default="{}"),
)

budget_consumption = Table(
    "budget_consumption",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column("budget_id", sa.BigInteger, sa.ForeignKey("budgets.id"), nullable=False),
    Column("invoice_id", sa.Uuid(as_uuid=True), sa.ForeignKey("invoices.id"), nullable=False),
    Column("amount", sa.Numeric, nullable=False),
    Column("consumed_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("balance_before", sa.Numeric, nullable=False),
    Column("balance_after", sa.Numeric, nullable=False),
    Column("sequence_no", sa.Integer, nullable=False),
    # UNIQUE: an invoice can be recorded against a budget AT MOST ONCE.
    # Without this, a retried batch double-counts an invoice against a budget.
    sa.UniqueConstraint("budget_id", "invoice_id", name="uq_budget_consumption_invoice"),
)

episodic_decisions = Table(
    "episodic_decisions",
    metadata,
    Column("id", sa.Uuid(as_uuid=True), primary_key=True),
    Column("approval_id", sa.Uuid(as_uuid=True), sa.ForeignKey("approvals.id")),
    Column("invoice_id", sa.Uuid(as_uuid=True), sa.ForeignKey("invoices.id"), nullable=False),
    Column("vendor_tax_code", sa.Text, nullable=False),
    Column("category", sa.Text),
    Column("department", sa.Text),
    Column("amount", sa.Numeric, nullable=False),
    Column("flags", postgresql.JSONB, nullable=False, server_default="{}"),
    Column("decision", sa.Text, nullable=False),
    Column("reason", sa.Text),
    Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    sa.Index("ix_episodic_vendor", "vendor_tax_code"),
    sa.Index("ix_episodic_category", "category"),
)

llm_calls = Table(
    "llm_calls",
    metadata,
    Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("model", sa.Text, nullable=False),
    Column("provider", sa.Text, nullable=False),
    Column("node", sa.Text, nullable=False),
    # Design doc §2.9 context. All nullable: a call from scripts/run_one.py has
    # no invoice yet, and an eval-harness call has neither invoice nor batch.
    # `trace_id` is written by nothing until M6.5 wires Langfuse — the column
    # ships now because one migration is cheaper than two.
    Column("invoice_id", sa.Uuid(as_uuid=True), sa.ForeignKey("invoices.id")),
    Column("batch_id", sa.Uuid(as_uuid=True), sa.ForeignKey("batches.id")),
    Column("thread_id", sa.Text),
    Column("agent", sa.Text),
    Column("trace_id", sa.Text),
    Column("prompt_tokens", sa.Integer, nullable=False),
    Column("completion_tokens", sa.Integer, nullable=False),
    Column("cost_usd", sa.Numeric, nullable=False, server_default="0"),
    Column("shadow_cost_usd", sa.Numeric, nullable=False, server_default="0"),
    Column("latency_ms", sa.Integer, nullable=False),
    Column("attempt", sa.Integer, nullable=False, server_default="1"),
    Column("succeeded", sa.Boolean, nullable=False, server_default="true"),
    Column("error", sa.Text),
)
