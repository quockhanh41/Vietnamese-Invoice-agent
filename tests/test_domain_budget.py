"""Serial budget reconciliation arithmetic (design doc §4.3, §2.6)."""

import random
from datetime import date
from decimal import Decimal

from invoice_agent.domain import findings
from invoice_agent.domain.budget import BudgetInput, consume

LIMIT = Decimal("1000")
WARN = Decimal("0.8")


def item(n, amount, day=1):
    return BudgetInput(
        invoice_id=n, issue_date=date(2026, 7, day),
        invoice_series="C26TAA", invoice_number=f"{n:08d}", amount=Decimal(amount),
    )


def test_balances_chain_correctly():
    result = consume(LIMIT, WARN, [item(1, "300"), item(2, "400")])
    assert [(c.balance_before, c.balance_after) for c in result] == [
        (Decimal("1000"), Decimal("700")),
        (Decimal("700"), Decimal("300")),
    ]
    assert [c.sequence_no for c in result] == [1, 2]


def test_consume_is_order_independent():
    """MUTATION GUARD, and the whole reason this function sorts internally.

    M4's test_budget_serialization.py shuffles arrival order and asserts the
    balance_after chain is byte-identical and the SAME invoice breaches. A
    consume() that trusted the caller's order would make that unprovable.
    Canonical order is (issue_date, invoice_series, invoice_number) — NOT
    arrival order (design doc §2.6).
    """
    items = [item(1, "300", day=1), item(2, "400", day=2), item(3, "500", day=3)]
    expected = [c.balance_after for c in consume(LIMIT, WARN, items)]
    breached = [c.invoice_id for c in consume(LIMIT, WARN, items)
                if any(f.code == findings.BUDGET_BREACH for f in c.findings)]
    rng = random.Random(42)
    for _ in range(10):
        shuffled = items[:]
        rng.shuffle(shuffled)
        got = consume(LIMIT, WARN, shuffled)
        assert [c.balance_after for c in got] == expected
        assert [c.invoice_id for c in got
                if any(f.code == findings.BUDGET_BREACH for f in c.findings)] == breached


def test_breach_is_flagged_when_balance_goes_negative():
    result = consume(LIMIT, WARN, [item(1, "600"), item(2, "600")])
    assert not result[0].findings
    assert [f.code for f in result[1].findings] == [findings.BUDGET_BREACH]


def test_warning_fires_at_the_threshold_but_not_before():
    """warn_pct=0.8 means warn once 80% of the limit is gone, i.e. when the
    remaining balance falls to 200 or below."""
    result = consume(LIMIT, WARN, [item(1, "700"), item(2, "100")])
    assert not result[0].findings                       # balance 300, 30% left
    assert [f.code for f in result[1].findings] == [findings.BUDGET_WARNING]  # balance 200


def test_breach_and_warning_are_mutually_exclusive():
    """A breaching invoice gets BUDGET_BREACH only — a reviewer should not see
    two findings saying the same thing."""
    result = consume(LIMIT, WARN, [item(1, "1500")])
    assert [f.code for f in result[0].findings] == [findings.BUDGET_BREACH]


def test_empty_input_returns_empty():
    assert consume(LIMIT, WARN, []) == []
