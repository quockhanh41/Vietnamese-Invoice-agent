"""Which of our types may cross a checkpoint boundary — and what happens if not.

MEASURED 2026-07-29 (spec §2.1): LangGraph's permissive default warns that it
"will be blocked in a future version". Blocked does NOT mean raised — a Finding
comes back as a plain `dict`, so the first symptom is an AttributeError far from
the cause. This file pins both halves.
"""

from datetime import date
from decimal import Decimal

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from invoice_agent.domain import findings
from invoice_agent.extraction.models import InvoiceData, InvoiceLine
from invoice_agent.llm.cost import LLMCallRecord
from invoice_agent.serde import ALLOWED_MSGPACK_MODULES, build_serde


def sample_objects() -> dict[str, object]:
    return {
        "Finding": findings.make(
            findings.ARITHMETIC_MISMATCH, total_amount=Decimal("1290107.52")
        ),
        "InvoiceData": InvoiceData(
            template_no="1",
            invoice_series="C26TAA",
            invoice_number="00000123",
            issue_date=date(2026, 7, 1),
            total_amount=Decimal("1290107.52"),
            lines=[
                InvoiceLine(
                    line_no=1,
                    item_kind=1,
                    description="Dịch vụ tư vấn",
                    amount=Decimal("1194544"),
                )
            ],
        ),
        "LLMCallRecord": LLMCallRecord(
            model="gemini/gemini-3.5-flash",
            provider="gemini",
            prompt_tokens=900,
            completion_tokens=120,
            latency_ms=800,
            attempt=1,
            succeeded=True,
        ),
    }


@pytest.mark.parametrize("name", list(sample_objects()))
def test_allowlisted_types_round_trip_with_their_type_intact(name):
    obj = sample_objects()[name]
    serde = build_serde()
    back = serde.loads_typed(serde.dumps_typed(obj))
    assert type(back) is type(obj), f"{name} degraded to {type(back).__name__}"
    assert back == obj


def test_without_the_allowlist_a_finding_degrades_to_a_dict_silently():
    """The measurement that justifies this module. Strict mode does not raise —
    it substitutes a dict, and nothing downstream notices until an attribute
    access fails somewhere unrelated."""
    strict = JsonPlusSerializer(allowed_msgpack_modules=None)
    obj = findings.make(findings.NEW_VENDOR)
    back = strict.loads_typed(strict.dumps_typed(obj))
    assert isinstance(back, dict), "measurement changed — re-read spec §2.1"
    assert back != obj


def test_every_allowlist_entry_is_importable():
    """A typo here fails open: the type silently leaves the allowlist and
    degrades exactly as above."""
    import importlib

    for module_name, attr in ALLOWED_MSGPACK_MODULES:
        module = importlib.import_module(module_name)
        assert hasattr(module, attr), f"{module_name}.{attr} does not exist"


def test_checkpointer_carries_the_allowlist(requires_postgres):
    """`from_conn_string` takes no serde (spec §2.2), so this is assigned after
    the saver is yielded. If that assignment is dropped, checkpointed state
    degrades — and nothing else would notice."""
    from invoice_agent.checkpointer import build_checkpointer

    with build_checkpointer() as saver:
        assert saver.serde._allowed_msgpack_modules == ALLOWED_MSGPACK_MODULES
        obj = findings.make(findings.NEW_VENDOR)
        back = saver.serde.loads_typed(saver.serde.dumps_typed(obj))
        assert type(back) is findings.Finding



def test_a_populated_enrichment_state_round_trips_with_every_type_intact():
    """The guard that actually matters: not "the list has four entries" but
    "every value in a real state survives". Add a state field of an
    unregistered type and this fails here, not in a checkpoint."""
    from invoice_agent.graphs.enrichment import EnrichmentState

    assert ("invoice_agent.extraction.models", "InvoiceLine") in ALLOWED_MSGPACK_MODULES
    objects = sample_objects()

    state: EnrichmentState = {
        "invoice_id": "INV-0001",
        "source_path": "corpus/INV-0001.xml",
        "pdf_path": None,
        "source_type": "xml",
        "strategy": "xml",
        "extracted": objects["InvoiceData"],
        "extraction_attempts": 1,
        "current_errors": ["ARITHMETIC_MISMATCH: total_amount=1"],
        "error_history": ["ARITHMETIC_MISMATCH: total_amount=1"],
        "findings": [objects["Finding"]],
        "llm_calls": [objects["LLMCallRecord"]],
        "status": "received",
    }
    serde = build_serde()
    back = serde.loads_typed(serde.dumps_typed(state))
    assert type(back["extracted"]) is type(objects["InvoiceData"])
    assert type(back["extracted"].lines[0]) is type(objects["InvoiceData"].lines[0])
    assert type(back["findings"][0]) is type(objects["Finding"])
    assert type(back["llm_calls"][0]) is type(objects["LLMCallRecord"])
    assert back == state

