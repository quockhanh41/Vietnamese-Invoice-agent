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

BANNED = (
    "invoice_agent.extraction.xml_schema",
    "invoice_agent.extraction.xml_parser",
    "invoice_agent.domain",
)

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


def test_seed_reference_is_the_one_allowed_dependency():
    """M2-A §7 made BUDGETS a DESIGNED dependency: the budget limits must live
    in one place, or item 2.3's overrun case is tuned against a second copy and
    starts passing for the wrong reason."""
    source = pathlib.Path("scripts/generate_invoices.py").read_text(encoding="utf-8")
    assert "seed_reference" in source
