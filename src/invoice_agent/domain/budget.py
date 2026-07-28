"""Budget consumption arithmetic (design doc §4.3, §2.6). Pure.

`consume` SORTS ITS INPUT. That is not a convenience — it is the property M4's
test_budget_serialization.py exists to prove: shuffle the arrival order of a
batch and the same invoice must still breach, with a byte-identical
balance_after chain. Canonical order is (issue_date, invoice_series,
invoice_number), NOT the order files happened to arrive in.

Locking (`SELECT ... FOR UPDATE`) and writing `budget_consumption` rows are
M4.5's job. This function only does the arithmetic.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from invoice_agent.domain import findings

ZERO = Decimal("0")


@dataclass(frozen=True)
class BudgetInput:
    invoice_id: Any
    issue_date: date
    invoice_series: str
    invoice_number: str
    amount: Decimal


@dataclass(frozen=True)
class Consumption:
    invoice_id: Any
    amount: Decimal
    balance_before: Decimal
    balance_after: Decimal
    sequence_no: int
    findings: list[findings.Finding]


def consume(
    limit: Decimal, warn_pct: Decimal, invoices: Sequence[BudgetInput]
) -> list[Consumption]:
    """Walk the invoices in canonical order, draining the budget."""
    ordered = sorted(
        invoices, key=lambda i: (i.issue_date, i.invoice_series, i.invoice_number)
    )
    # Warn once cumulative spend reaches warn_pct of the limit — i.e. once the
    # remaining balance falls to limit x (1 - warn_pct). With the seeded default
    # of 0.8 that is "80% or more of the budget is gone".
    warn_at = limit * (Decimal("1") - warn_pct)

    out: list[Consumption] = []
    balance = limit
    for sequence_no, inv in enumerate(ordered, start=1):
        before = balance
        balance = before - inv.amount
        found: list[findings.Finding] = []
        if balance < ZERO:
            found.append(findings.make(
                findings.BUDGET_BREACH,
                limit=limit, balance_before=before, balance_after=balance,
            ))
        elif balance <= warn_at:
            found.append(findings.make(
                findings.BUDGET_WARNING,
                limit=limit, balance_after=balance, warn_threshold_pct=warn_pct,
            ))
        out.append(Consumption(inv.invoice_id, inv.amount, before, balance, sequence_no, found))
    return out
