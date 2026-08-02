"""Design §9.2's claim, measured: injection is inexpressible, not undetected.

The four assertions correspond to plan item 6.3's four definitions of attacker
success. Three are driven by a hostile stub and need no keys; the fourth is the
§9.3 negative control and needs recorded model output.
"""

import dataclasses

from tests.helpers import injection_cases as cases


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
