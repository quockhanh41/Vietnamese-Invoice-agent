"""Item 2.3: the budget case must be ORDER-DEPENDENT, or M4's serialization
test proves nothing."""

import itertools
import random
from decimal import Decimal

from scripts.generator import defects
from scripts.seed_reference import BUDGETS
from tests.test_generator_templates import spec

from invoice_agent.domain import findings  # TEST-ONLY
from invoice_agent.domain.budget import BudgetInput, consume  # TEST-ONLY

LIMIT = dict(((d, p), a) for d, p, c, a in BUDGETS)[(defects.BUDGET_CASE_DEPARTMENT, "2026-07")]


def as_inputs(specs):
    return [
        BudgetInput(s.invoice_number, s.issue_date, s.invoice_series,
                    s.invoice_number, s.total_amount)
        for s in specs
    ]


def test_case_is_seeded_against_the_real_limit():
    """MUTATION GUARD for the M2-A dependency. If the generator hardcoded a
    limit instead of importing BUDGETS, changing the seed would silently stop
    the case overrunning at all."""
    built = defects.build_budget_overrun_case(spec(), random.Random(42))
    assert sum(s.total_amount for s in built) > LIMIT


def test_exactly_one_invoice_breaches_in_canonical_order():
    built = defects.build_budget_overrun_case(spec(), random.Random(42))
    breaching = [
        c.invoice_id for c in consume(LIMIT, Decimal("0.8"), as_inputs(built))
        if any(f.code == findings.BUDGET_BREACH for f in c.findings)
    ]
    assert len(breaching) == 1


def test_the_same_invoice_breaches_under_every_shuffle():
    """The property M4's test_budget_serialization.py will assert on real data."""
    built = defects.build_budget_overrun_case(spec(), random.Random(42))
    expected = [
        c.invoice_id for c in consume(LIMIT, Decimal("0.8"), as_inputs(built))
        if any(f.code == findings.BUDGET_BREACH for f in c.findings)
    ]
    rng = random.Random(1)
    for _ in range(10):
        shuffled = built[:]
        rng.shuffle(shuffled)
        got = [
            c.invoice_id for c in consume(LIMIT, Decimal("0.8"), as_inputs(shuffled))
            if any(f.code == findings.BUDGET_BREACH for f in c.findings)
        ]
        assert got == expected


def test_a_naive_arrival_order_walk_would_pick_a_different_invoice():
    """THE POINT OF ITEM 2.3. If every arrival order breached the same invoice,
    the case would be order-INdependent and would prove nothing about serial
    reconciliation. Measured 2026-07-28: a naive walk can breach any of three."""
    built = defects.build_budget_overrun_case(spec(), random.Random(42))
    naive = set()
    for perm in itertools.permutations(built):
        balance = LIMIT
        for s in perm:
            balance -= s.total_amount
            if balance < 0:
                naive.add(s.invoice_number)
                break
    assert len(naive) > 1, "the seeded case is not order-dependent"


def test_canonical_order_differs_from_invoice_number_order():
    """If they matched, shuffling the input would not distinguish a sorted
    implementation from an unsorted one."""
    built = defects.build_budget_overrun_case(spec(), random.Random(42))
    by_number = sorted(built, key=lambda s: s.invoice_number)
    canonical = sorted(built, key=lambda s: (s.issue_date, s.invoice_series, s.invoice_number))
    assert [s.invoice_number for s in by_number] != [s.invoice_number for s in canonical]
