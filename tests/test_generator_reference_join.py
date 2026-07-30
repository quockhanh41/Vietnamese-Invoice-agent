"""The corpus must join the reference data it will be inserted against.

Measured 2026-07-30: the generator used ("MKT","IT","HR","LOG","ADM","SAL") and
the seed created ("MKT","ADMIN","OPS","IT","SALES"). Only MKT and IT overlapped,
so 194 of 300 corpus invoices could not satisfy invoices.department -> FK
departments.code, and four corpus departments had no budgets row to reconcile
against. M2-A built the schema, M2-C built the generator, and nothing ever
joined them — M4.5 would have been the first code to try.
See spec 2026-07-31-m4a §1.1 D1.
"""

from scripts import generate_invoices
from scripts.generator import manifest
from scripts.seed_reference import BUDGETS
from scripts.seed_reference import DEPARTMENTS as SEEDED

BUDGET_PERIOD = "2026-07"


def test_generator_departments_come_from_the_seed():
    assert set(generate_invoices.DEPARTMENTS) <= {code for code, _name in SEEDED}


def test_every_manifest_department_is_seeded_and_budgeted(tmp_path):
    """The load-bearing guard. Restore the literal DEPARTMENTS tuple and this
    goes red — that is the mutation this test exists to catch."""
    out = tmp_path / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    records = manifest.read_manifest(out / "manifest.jsonl")

    seeded = {code for code, _name in SEEDED}
    budgeted = {dept for dept, period, _cat, _amt in BUDGETS if period == BUDGET_PERIOD}

    used = {r["department"] for r in records}
    assert used <= seeded, f"departments with no departments row: {sorted(used - seeded)}"
    assert used <= budgeted, (
        f"departments with no {BUDGET_PERIOD} budgets row, so nothing to "
        f"reconcile against: {sorted(used - budgeted)}"
    )


def test_every_manifest_category_is_seeded(tmp_path):
    """Same failure mode one column over: invoices.category is also a FK."""
    from scripts.seed_reference import CATEGORIES

    out = tmp_path / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    records = manifest.read_manifest(out / "manifest.jsonl")

    seeded = {row[0] for row in CATEGORIES}
    used = {r["category"] for r in records}
    assert used <= seeded, f"categories with no expense_categories row: {sorted(used - seeded)}"
