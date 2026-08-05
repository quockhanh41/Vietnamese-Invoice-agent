"""One declaration of every tool, per design §8.1.

⚠️ SCOPE, stated because the design doc's phrasing overreaches. §8.1 says "both
the LangGraph executor and the MCP server are generated from tools/registry.py".
That is NOT true today: the graphs call their functions directly and there is no
tool executor to generate. The registry's real consumers are the MCP server
(`invoice_agent/mcp/`) and `tests/test_no_write_tools_in_mcp.py`. Written down so
a future reader does not inherit a claim the code never made.

Handlers are the functions that ALREADY EXIST. A tool that reimplements a query
is a second source of truth and the first thing to drift out of agreement with
the API and the UI.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from invoice_agent.db import queries
from invoice_agent.db.repositories import approvals as approvals_repo
from invoice_agent.memory import vendor_profile
from invoice_agent.tools import explain, models


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    return_model: type[BaseModel] | None
    side_effect: Literal["read", "write"]
    mcp_exposed: bool
    handler: Callable[..., Any]


# The ONLY write reachable over MCP. Design §8.2 permits it because it is
# human-authorised and resumes an existing interrupt; it invents no approval and
# posts no entry itself. Widening this set must be a deliberate act — the
# architecture test asserts the set's exact contents, so a second entry cannot
# be added without editing a test that says why.
MCP_WRITE_ALLOWLIST: frozenset[str] = frozenset({"submit_approval_decision"})

REGISTRY: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="search_invoices",
        description=(
            "List invoices, optionally filtered by status, department, vendor "
            "(seller tax code) and/or a minimum amount, most recent first."
        ),
        args_model=models.SearchInvoicesArgs,
        return_model=models.SearchInvoicesResult,
        side_effect="read",
        mcp_exposed=True,
        handler=queries.list_invoices,
    ),
    ToolSpec(
        name="get_invoice_detail",
        description="Fetch one invoice's fields, line items and open flags by id.",
        args_model=models.GetInvoiceDetailArgs,
        return_model=models.InvoiceDetail,
        side_effect="read",
        mcp_exposed=True,
        handler=queries.invoice_detail,
    ),
    ToolSpec(
        name="get_budget_status",
        description=(
            "Budget lines (limit, consumed, remaining), optionally filtered to "
            "one department and/or one period."
        ),
        args_model=models.GetBudgetStatusArgs,
        return_model=models.GetBudgetStatusResult,
        side_effect="read",
        mcp_exposed=True,
        handler=queries.budget_report,
    ),
    ToolSpec(
        name="get_vendor_profile",
        description=(
            "Vendor entity-memory statistics — invoice count, average and "
            "stddev of amount — for one tax code."
        ),
        args_model=models.GetVendorProfileArgs,
        return_model=models.VendorProfile,
        side_effect="read",
        mcp_exposed=True,
        handler=vendor_profile.stats_for,
    ),
    ToolSpec(
        name="list_pending_approvals",
        description="Every approval awaiting a human decision.",
        args_model=models.ListPendingApprovalsArgs,
        return_model=models.ListPendingApprovalsResult,
        side_effect="read",
        mcp_exposed=True,
        handler=approvals_repo.list_pending,
    ),
    ToolSpec(
        name="explain_flags",
        description=(
            "Narrate, in Vietnamese, every open flag on one invoice — why it "
            "was raised and the evidence behind it. Template-rendered from "
            "deterministic facts; no model is involved."
        ),
        args_model=models.ExplainFlagsArgs,
        return_model=models.ExplainedFlags,
        side_effect="read",
        mcp_exposed=True,
        handler=explain.explain_flags,
    ),
    ToolSpec(
        name="spend_summary",
        description=(
            "Invoice count and total spend, grouped by department or category, "
            "optionally narrowed to one period (YYYY-MM)."
        ),
        args_model=models.SpendSummaryArgs,
        return_model=models.SpendSummaryResult,
        side_effect="read",
        mcp_exposed=True,
        handler=queries.spend_summary,
    ),
    # `submit_approval_decision` (Task 5) arrives in a later M7 task.
)


def exposed_tools() -> tuple[ToolSpec, ...]:
    """What the MCP server registers. The server never filters for itself."""
    return tuple(spec for spec in REGISTRY if spec.mcp_exposed)
