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


def test_canonical_budget_rank_is_canonical_position(tmp_path):
    """The field name says canonical; the value was file order.

    Measured 2026-07-30: rank was assigned by `enumerate` over construction
    order (generate_invoices.py:164), which for this case is invoice-number
    order — and defects.py:163 DELIBERATELY makes invoice-number order the
    reverse of canonical order, so shuffling can tell a sorted implementation
    from an unsorted one. So rank=1 was INV-0001, which is THIRD in canonical
    order and the one that breaches. M4-D reading this field as canonical
    position would have got exactly the wrong answer, and passed.
    See spec 2026-07-31-m4a §1.1 D2.
    """
    import json

    from scripts import generate_invoices

    out = tmp_path / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    rows = [json.loads(line) for line in (out / "manifest.jsonl").open(encoding="utf-8")]

    ranked = [r for r in rows if r["canonical_budget_rank"] is not None]
    assert len(ranked) == 3, f"expected the 3-invoice budget case, got {len(ranked)}"

    canonical = sorted(
        ranked, key=lambda r: (r["issue_date"], r["invoice_series"], r["invoice_number"])
    )
    for position, record in enumerate(canonical, start=1):
        assert record["canonical_budget_rank"] == position, (
            f"{record['invoice_id']} issued {record['issue_date']} is #{position} in "
            f"canonical order but carries rank {record['canonical_budget_rank']}"
        )

    # The one that breaches is LAST in canonical order, not first in file order.
    assert canonical[-1]["canonical_budget_rank"] == 3
    assert canonical[-1]["issue_date"].endswith("-20")
