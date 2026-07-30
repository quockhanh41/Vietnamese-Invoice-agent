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


def test_department_maps_only_name_seeded_departments():
    """The maps are the real producer, so they are the real guard.

    Found 2026-07-31 by the M4-A review: `DEPARTMENTS` was load-bearing when the
    guard below was written (mutating it back to the old literal turned the
    guard RED at commit 0b83774), and stopped being load-bearing two commits
    later when departments began coming from these maps instead — the same
    mutation left the guard GREEN at 81f1930. A guard that was real and went
    blind is a new variant of the pattern M1-M3 kept hitting, so it is recorded
    rather than quietly repaired.

    This test is fast and direct where the guard below is slow and indirect:
    break a map value here and it fails in milliseconds, without building 300
    invoices to notice.
    """
    seeded = {code for code, _name in SEEDED}
    mapped = set(generate_invoices.CATEGORY_TO_DEPARTMENT.values()) | set(
        generate_invoices.SECONDARY_DEPARTMENT.values()
    )
    assert mapped <= seeded, f"unseeded departments in the maps: {sorted(mapped - seeded)}"

    budgeted = {dept for dept, period, _cat, _amt in BUDGETS if period == BUDGET_PERIOD}
    assert mapped <= budgeted, (
        f"departments the maps can produce that have no {BUDGET_PERIOD} budget: "
        f"{sorted(mapped - budgeted)}"
    )


def test_every_category_is_routable_to_a_department():
    """A category missing from either map raises KeyError mid-generation, on
    whichever invoice happens to draw it — a failure whose message names the
    invoice rather than the missing mapping."""
    categories = set(generate_invoices.CATEGORIES)
    assert set(generate_invoices.CATEGORY_TO_DEPARTMENT) == categories
    assert set(generate_invoices.SECONDARY_DEPARTMENT) == categories


def test_every_manifest_department_is_seeded_and_budgeted(tmp_path):
    """End-to-end confirmation that the maps above actually reach the manifest.

    Mutate `CATEGORY_TO_DEPARTMENT["MARKETING"]` to an unseeded code and this
    goes red — that is the mutation this test catches. Restoring the old literal
    `DEPARTMENTS` tuple does NOT turn it red, because that tuple no longer
    produces anything; see `test_department_maps_only_name_seeded_departments`.
    """
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
