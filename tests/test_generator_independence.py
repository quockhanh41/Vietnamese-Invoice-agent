"""The anti-circularity mechanism, asserted rather than intended.

Design doc §10.1: you generate the XML, you generate the ground truth, AND you
write the parser — so 100% F1 means nothing unless the two sides were built
independently. This test is what makes "independently" a fact.

The risk is measured, not theoretical. M1 found a structure table written from
memory wrong in FOUR places, and the naive arithmetic rule flags 6 of 8 real
invoices. A generator sharing that memory would have scored 300/300 against the
wrong rule and printed `false-positive rate 0%`.
"""

import pathlib
from random import Random

from scripts.seed_reference import BUDGETS as _BUDGETS
from tests.test_generator_templates import spec as _spec

BANNED = (
    "invoice_agent.extraction.xml_schema",
    "invoice_agent.extraction.xml_parser",
    "invoice_agent.domain",
)

_BASE_SPEC = _spec()


def _RNG():
    return Random(42)


GENERATOR_FILES = [
    *pathlib.Path("scripts/generator").rglob("*.py"),
    pathlib.Path("scripts/generate_invoices.py"),
]


def test_generator_files_exist():
    """Without this, the scan below would find nothing and pass vacuously."""
    existing = [p for p in GENERATOR_FILES if p.exists()]
    assert existing, "no generator files found — the scan below would prove nothing"


def test_generator_imports_neither_parser_nor_domain():
    offenders = []
    for path in GENERATOR_FILES:
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        for banned in BANNED:
            if f"import {banned}" in source or f"from {banned}" in source:
                offenders.append(f"{path}: {banned}")
    assert not offenders, (
        "the generator must be written independently of the parser and the "
        f"domain rules, or the eval score is meaningless: {offenders}"
    )


def test_seed_reference_budgets_are_actually_used_not_merely_imported():
    """M2-A §7 made BUDGETS a DESIGNED dependency: the budget limits must live
    in one place, or item 2.3's overrun case is tuned against a second copy and
    starts passing for the wrong reason.

    THIS TEST USED TO CHECK FOR THE STRING "seed_reference" AND NOTHING ELSE.
    Measured 2026-07-28 during review: the import was present, ruff reported it
    as F401 unused, and the test passed anyway — a decorative dependency that
    proved exactly nothing. Asserting the symbol is *referenced* is what makes
    it real.
    """
    source = pathlib.Path("scripts/generate_invoices.py").read_text(encoding="utf-8")
    assert "from scripts.seed_reference import BUDGETS" in source
    # ...and used somewhere other than the import line itself.
    uses = [
        line for line in source.splitlines()
        if "BUDGETS" in line and not line.lstrip().startswith(("from ", "import "))
    ]
    assert uses, "BUDGETS is imported but never used — the dependency is decorative"


def test_generate_refuses_a_budget_case_that_cannot_breach():
    """The seeded limit is checked at generation time, not assumed.

    Raise the MKT budget and the three seeded invoices quietly stop overrunning:
    the 4% budget_overrun group becomes 12 invoices that breach nothing. That is
    invisible in the corpus and surfaces only as a bad recall number in M6, so
    `make generate` must refuse to produce it.
    """
    from decimal import Decimal

    from scripts.generator import defects

    case_total = sum(
        (s.total_amount for s in defects.build_budget_overrun_case(_BASE_SPEC, _RNG())),
        Decimal("0"),
    )
    limit = {(d, p): a for d, p, _c, a in _BUDGETS}[
        (defects.BUDGET_CASE_DEPARTMENT, defects.BUDGET_CASE_PERIOD)
    ]
    assert case_total > limit, (
        f"the seeded case totals {case_total} against a limit of {limit} — it would never breach"
    )
