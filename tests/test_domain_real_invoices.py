"""The anchor: real invoices must produce NO findings.

Design doc §10.4 makes flag false-positive rate the HEADLINE metric, because it
measures the thing that decides whether the system gets used — reviewer fatigue.
These 8 files are real, valid, tax-authority-signed invoices. Every finding here
is a false positive, and this test measures that today, before a single
synthetic invoice exists.

M1 measured that the NAIVE rule flags 6 of 8, and spec §2.3 measured that M1's
own subtotal/VAT rules flag 3 of 8. Both were caught by exactly this test.
"""

import pathlib

import pytest

from invoice_agent.domain import money, validation
from invoice_agent.extraction.xml_parser import parse_invoice_xml

FIXTURES = sorted(pathlib.Path("tests/fixtures/real_samples").glob("*.xml"))


def test_fixtures_are_present():
    """Without this, the parametrized tests below would silently collect zero
    cases and pass — a green suite proving nothing."""
    assert len(FIXTURES) == 8


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_real_invoice_produces_no_findings(path):
    invoice = parse_invoice_xml(path.read_bytes())
    result = validation.validate(invoice)
    assert result == [], (
        f"{path.name} is a real, valid invoice but produced "
        f"{[f.code for f in result]} — every one is a false positive"
    )


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_real_amount_words_agree_with_the_digits(path):
    """TgTTTBChu as a second independent encoding (§10.3), on real data."""
    invoice = parse_invoice_xml(path.read_bytes())
    parsed = money.parse_amount_words(invoice.total_in_words)
    assert parsed is not None, f"{path.name}: could not parse {invoice.total_in_words!r}"
    assert parsed == invoice.total_amount


def test_domain_imports_nothing_with_io():
    """Spec §7 criterion 8. Purity is the interface: these functions run inside
    M4's `SELECT ... FOR UPDATE`, and an accidental query there is a deadlock.
    Asserted rather than intended."""
    import pathlib as pl

    banned = ("sqlalchemy", "psycopg", "invoice_agent.db", "requests", "httpx", "litellm")
    offenders = []
    for module in pl.Path("src/invoice_agent/domain").glob("*.py"):
        source = module.read_text(encoding="utf-8")
        for name in banned:
            if f"import {name}" in source or f"from {name}" in source:
                offenders.append(f"{module.name} imports {name}")
    assert not offenders, offenders
