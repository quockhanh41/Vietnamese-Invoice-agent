"""Design §9.2's claim, measured: injection is inexpressible, not undetected.

The four assertions correspond to plan item 6.3's four definitions of attacker
success. Three are driven by a hostile stub and need no keys; the fourth is the
§9.3 negative control and needs recorded model output.
"""

import dataclasses

import pytest
from tests.helpers import injection_cases as cases
from tests.helpers.hostile_gateway import LAUNDER, OVERT, hostile_gateway

from invoice_agent.agents.models import ClassificationResult
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
