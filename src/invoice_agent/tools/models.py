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


# ---- get_invoice_detail : db.repositories.invoices.get_by_id ---------------


class GetInvoiceDetailArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_id: uuid.UUID


class InvoiceDetail(BaseModel):
    """`get_by_id` returns the whole `invoices` row; this keeps the subset that
    is useful to read over chat, the same trim `api/routes/invoices.py` makes
    when it drops `raw_xml` at its own JSON boundary.

    Not lines, not flags — `get_by_id` has neither. Design §8.2's
    `get_invoice_detail` wants "fields + line items + flags"; there is no
    single existing function that assembles all three (the route calls
    `get_by_id`, `get_lines` and `flags.open_flags_for` separately), and this
    milestone forbids writing a new query to close that gap. See the Task 1
    report for the concern this leaves open.
    """

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


# ---- get_budget_status : db.queries.budget_report ---------------------------


class GetBudgetStatusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
