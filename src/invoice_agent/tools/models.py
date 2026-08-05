"""Pydantic arg/return models for `tools/registry.py` (design §8.1).

Field sets mirror what the underlying handlers already return — `db/queries.py`
and the repositories — not the full column list `db/models.py` carries for a
table. A model this shallow degrades gracefully as the schema grows (Pydantic
ignores unknown keys by default) and avoids becoming a second list of every
column that must move in lockstep with a migration, the exact drift
`db/queries.py`'s own module docstring warns about.

`extra="forbid"` on every args model, matching `api/schemas.py::DecisionRequest`:
these arguments arrive from an external MCP client, so an unexpected field is
rejected rather than silently dropped. Return models carry no such config —
they describe OUR OWN query output, not client input, so there is no boundary
to defend.
"""

import datetime
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

# ---- search_invoices : db.queries.list_invoices -----------------------------


class SearchInvoicesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    department: str | None = None
    vendor: str | None = None
    min_amount: Decimal | None = None
    limit: int = 50


class InvoiceSummary(BaseModel):
    id: uuid.UUID
    invoice_series: str
    invoice_number: str
    seller_tax_code: str
    issue_date: datetime.date
    total_amount: Decimal
    category: str | None
    department: str | None
    status: str
    batch_id: uuid.UUID | None


class SearchInvoicesResult(BaseModel):
    invoices: list[InvoiceSummary]


# ---- get_invoice_detail : db.queries.invoice_detail -------------------------


class GetInvoiceDetailArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_id: uuid.UUID


class InvoiceDetailFields(BaseModel):
    """The same subset of the `invoices` row `InvoiceSummary` above keeps, plus
    `buyer_tax_code` — one field a detail view can afford that a search-result
    list should not."""

    id: uuid.UUID
    batch_id: uuid.UUID | None
    invoice_series: str
    invoice_number: str
    seller_tax_code: str
    buyer_tax_code: str | None
    issue_date: datetime.date
    total_amount: Decimal
    category: str | None
    department: str | None
    status: str


class InvoiceDetailLine(BaseModel):
    line_no: int
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None
    vat_rate: Decimal | None
    vat_amount: Decimal | None


class InvoiceDetailFlag(BaseModel):
    """Only the open ones — `open_flags_for` never returns a resolved row, so
    a `resolved_at` field here would always read `None`."""

    code: str
    severity: str
    raised_by: str


class InvoiceDetail(BaseModel):
    """`db.queries.invoice_detail` composes `get_by_id`, `get_lines` and
    `flags.open_flags_for` — the same three calls
    `api/routes/invoices.py::get_invoice` makes — so this carries all three:
    fields, line items and open flags. Each nested model keeps its own shallow
    subset of its table's columns, not the full column list (see this file's
    module docstring); it is not the trim `api/routes/invoices.py` makes,
    which keeps every column except `raw_xml` (~23 columns) — this model
    keeps far fewer, on all three of its parts.
    """

    invoice: InvoiceDetailFields
    lines: list[InvoiceDetailLine]
    flags: list[InvoiceDetailFlag]


# ---- get_budget_status : db.queries.budget_report ---------------------------


class GetBudgetStatusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department: str | None = None
    period: str | None = None


class BudgetLine(BaseModel):
    # Already stringified Decimal by `budget_report` itself (money rule, M1):
    # JSON numbers are IEEE 754 doubles and would lose the last đồng.
    department_code: str
    period: str
    category: str | None
    amount_limit: str
    warn_threshold_pct: str
    consumed: str
    remaining: str


class GetBudgetStatusResult(BaseModel):
    lines: list[BudgetLine]


# ---- get_vendor_profile : memory.vendor_profile.stats_for -------------------


class GetVendorProfileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tax_code: str


class VendorProfile(BaseModel):
    """Mirrors `domain.anomaly.VendorStats`, which is a plain dataclass and so
    cannot itself be `ToolSpec.return_model` (a `type[BaseModel]`)."""

    invoice_count: int
    avg_amount: Decimal | None
    stddev_amount: Decimal | None


# ---- list_pending_approvals : db.repositories.approvals.list_pending -------


class ListPendingApprovalsArgs(BaseModel):
    """No fields: `list_pending` takes no filter, it returns every undecided
    approval."""

    model_config = ConfigDict(extra="forbid")


class PendingApproval(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    thread_id: str
    requested_at: datetime.datetime


class ListPendingApprovalsResult(BaseModel):
    approvals: list[PendingApproval]


# ---- explain_flags : tools.explain.explain_flags ----------------------------


class ExplainFlagsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_id: uuid.UUID


class ExplainedFlag(BaseModel):
    """`code`/`severity`/`raised_by` come from `domain.findings.CODE_REGISTRY`,
    not from the `invoice_flags` row's own (redundant) columns of the same
    name — CODE_REGISTRY is the one place severity/producer are decided, and
    reading it from there rather than from the row keeps that a single source
    of truth even though the schema happens to store both."""

    code: str
    severity: str
    raised_by: str
    narrative_vi: str


class ExplainedFlags(BaseModel):
    flags: list[ExplainedFlag]
    summary_vi: str


# ---- spend_summary : db.queries.spend_summary --------------------------------


class SpendSummaryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_by: str
    period: str | None = None


class SpendSummaryLine(BaseModel):
    # Already stringified Decimal by `spend_summary` itself, the same money
    # rule `BudgetLine` above follows: JSON numbers are IEEE 754 doubles and
    # would lose the last đồng.
    group_key: str | None
    invoice_count: int
    total_amount: str


class SpendSummaryResult(BaseModel):
    lines: list[SpendSummaryLine]
