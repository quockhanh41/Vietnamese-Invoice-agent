"""Design §9.2's claim, measured: injection is inexpressible, not undetected.

The four assertions correspond to plan item 6.3's four definitions of attacker
success. Three are driven by a hostile stub and need no keys; the fourth is the
§9.3 negative control and needs recorded model output.
"""

import dataclasses
import pathlib

import pytest
from scripts.generator.templates import render
from sqlalchemy import text
from tests.helpers import injection_cases as cases
from tests.helpers.hostile_gateway import LAUNDER, OVERT, hostile_gateway

from invoice_agent.agents.models import ClassificationResult
from invoice_agent.extraction.xml_parser import parse_invoice_xml
from invoice_agent.graphs.enrichment import build_enrichment_graph
from invoice_agent.llm.structured import StructuredOutputError, call_structured


def test_there_are_thirty_cases_covering_every_payload_and_placement():
    """30, not 26. Roadmap §2.2 estimated 26 before the seller_name and
    reasoning surfaces were known (spec §1.3); the count is raised, not cut."""
    assert len(cases.CASES) == 24
    assert len(cases.REASONING_CASES) == 6

    seen = {(c.payload_name, c.placement) for c in cases.CASES}
    assert len(seen) == 24, "every (payload, placement) pair must be distinct"


def test_each_twin_differs_from_its_carrier_in_exactly_one_field():
    """§9.3: without this, a classification difference measures whatever else
    differs rather than the injection, and the control measures noise."""
    for case in cases.CASES:
        carrier = dataclasses.asdict(case.carrier)
        twin = dataclasses.asdict(case.twin)
        differing = [k for k in carrier if carrier[k] != twin[k]]
        assert differing in (["lines"], ["seller_name"]), (
            f"{case.payload_name}/{case.placement} differs in {differing}"
        )


def test_overt_compliance_cannot_be_expressed_as_a_ClassificationResult(monkeypatch):
    """THE CENTRAL CLAIM (design §9.2). The stub returns exactly what payloads
    1-4 demand — approval_status approved, flags emptied. `extra="forbid"`
    rejects it, `call_structured` exhausts its retries, and the model never
    obtains a channel. The rejection IS the defence."""
    gw = hostile_gateway(monkeypatch, mode=OVERT)

    with pytest.raises(StructuredOutputError):
        call_structured(gw, response_model=ClassificationResult, prompt="x")


def test_laundering_IS_expressible_and_that_is_the_point(monkeypatch):
    """Payload 6 works through the LEGAL channel: category is a field the model
    is supposed to choose. It succeeds here, and Task 5 proves it still cannot
    move a flag — which is the actual defence, not the schema."""
    gw = hostile_gateway(monkeypatch, mode=LAUNDER, category="OFFICE_SUPPLIES")

    result = call_structured(gw, response_model=ClassificationResult, prompt="x")

    assert result.category == "OFFICE_SUPPLIES"


def _parse(spec):
    """Render a spec and parse it back into InvoiceData, the shape the agents take."""
    return parse_invoice_xml(render(spec).encode("utf-8"))


def run_carrier(spec, gw, tmp_path: pathlib.Path, name: str) -> dict:
    """Render, then run the compiled graph. Returns the final state.

    `StructuredOutputError` is an ACCEPTABLE outcome, not a failure: under OVERT
    mode the boundary rejects every attempt, which is the defence working. What
    must never happen is a decision or a journal entry, and those are asserted
    against the database rather than against this return value.
    """
    path = tmp_path / f"{name}.xml"
    path.write_text(render(spec), encoding="utf-8")
    state = {
        "invoice_id": name,
        "source_path": str(path),
        "pdf_path": None,
        "source_type": "xml",
        "strategy": "xml",
        "extracted": None,
    }
    try:
        return build_enrichment_graph(gateway=gw).invoke(state)
    except Exception as exc:  # noqa: BLE001 — the boundary rejecting is expected
        return {"status": "rejected", "error": repr(exc)}


@pytest.mark.parametrize("case", cases.CASES, ids=lambda c: f"{c.payload_name}-{c.placement}")
def test_no_payload_can_set_an_approval_or_post_an_entry(
    case, monkeypatch, tmp_path, requires_postgres
):
    """Plan item 6.3, definitions 1 and 2 of attacker success.

    Every case runs inside a SAVEPOINT that rolls back, so 24 cases cannot
    contaminate each other through vendor memory or the duplicate key.
    """
    from invoice_agent.db.engine import get_engine

    gw = hostile_gateway(monkeypatch, mode=OVERT)
    engine = get_engine()
    with engine.begin() as conn:
        sp = conn.begin_nested()
        run_carrier(case.carrier, gw, tmp_path, f"INJ-{case.payload_name}-{case.placement}")

        decided = conn.execute(
            text("SELECT count(*) FROM app.approvals WHERE decision IS NOT NULL")
        ).scalar_one()
        posted = conn.execute(
            text("SELECT count(*) FROM app.journal_entries")
        ).scalar_one()
        sp.rollback()

    assert decided == 0, "a payload obtained an approval decision"
    assert posted == 0, "a payload caused a journal entry to be posted"


def test_a_laundered_category_still_breaches_the_budget(
    monkeypatch, tmp_path, requires_postgres
):
    from decimal import Decimal

    from invoice_agent.db.engine import get_engine
    from invoice_agent.graphs.batch import serial_join

    # MKT_LIMIT: read from scripts.seed_reference.BUDGETS on 2026-08-02 (Step 2).
    # Substitute the measured value; the invoice must exceed it in ONE invoice
    # so a single run breaches without seeding a prior chain.
    MKT_LIMIT = Decimal("50000000")

    laundering = dict(cases.INJECTION_PAYLOADS)["category_laundering"]
    spec = cases.place(cases.base_spec(), laundering, "first_line")
    new_total = MKT_LIMIT + Decimal("1000000")
    spec = dataclasses.replace(
        spec,
        subtotal=new_total - Decimal("163564"),
        vat_total=Decimal("163564"),
        total_amount=new_total,
    )

    gw = hostile_gateway(monkeypatch, mode=LAUNDER, category="OFFICE_SUPPLIES")

    engine = get_engine()
    with engine.begin() as conn:
        sp = conn.begin_nested()
        final = run_carrier(spec, gw, tmp_path, "INJ-launder-budget")

        assert final.get("classification") is not None, final
        assert final["classification"].category == "OFFICE_SUPPLIES", (
            "the stub must actually succeed — a laundering test where the "
            "model failed to launder proves nothing"
        )
        if "department" not in final:
            final["department"] = final["classification"].department

        serial_join(
            {"batch_id": "00000000-0000-0000-0000-000000000001", "enriched": [final], "inputs": []},
            conn=conn,
        )
        codes = [
            row[0]
            for row in conn.execute(
                text("SELECT code FROM app.invoice_flags")
            ).fetchall()
        ]
        sp.rollback()

    assert "BUDGET_BREACH" in codes, (
        f"laundering the category suppressed the budget flag; got {codes}"
    )

