"""The extraction contract, and the safety property it must satisfy."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from invoice_agent.extraction.models import InvoiceData, InvoiceLine

# Fields that would let an LLM authorize its own work. At M3.4 InvoiceData
# becomes the structured-output schema for a vision call, and per CLAUDE.md
# structured output IS the security boundary — so a field here is a field the
# model can write. Flags are computed by Python from DB state; nothing else.
FORBIDDEN_FIELDS = {
    "category",
    "department",
    "classification_confidence",
    "status",
    "approved",
    "approval",
    "flags",
    "auto_post",
    "is_valid",
}


def test_invoice_data_has_no_self_authorizing_field():
    assert set(InvoiceData.model_fields) & FORBIDDEN_FIELDS == set()
    assert set(InvoiceLine.model_fields) & FORBIDDEN_FIELDS == set()


def test_invoice_data_rejects_unknown_fields():
    """extra='forbid' is what makes the test above meaningful: without it, an
    LLM could smuggle a field the schema never declared.
    """
    with pytest.raises(ValidationError):
        InvoiceData(
            template_no="1",
            invoice_series="C26TSY",
            invoice_number="4242",
            issue_date=date(2026, 5, 20),
            total_amount=Decimal("324000"),
            lines=[],
            category="office_supplies",
        )


def test_optional_fields_default_to_none_and_ext_to_empty_dict():
    """A minimal real invoice supplies only the required fields. Absent seller
    MST, absent buyer name and absent TgTThue are all normal in the corpus.
    """
    invoice = InvoiceData(
        template_no="2",
        invoice_series="C26TLN",
        invoice_number="23900",
        issue_date=date(2026, 6, 10),
        total_amount=Decimal("90000"),
        lines=[InvoiceLine(line_no=1, item_kind=1, description="Hàng mẫu")],
    )
    assert invoice.seller_tax_code is None
    assert invoice.buyer_name is None
    assert invoice.vat_amount is None
    assert invoice.ext == {}
    assert invoice.lines[0].quantity is None
    assert invoice.lines[0].ext == {}


def test_models_are_frozen():
    line = InvoiceLine(line_no=1, item_kind=1)
    with pytest.raises(ValidationError):
        line.amount = Decimal("1")  # type: ignore[misc]
