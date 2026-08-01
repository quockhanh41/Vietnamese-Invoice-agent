"""Structural guards. Neither can be satisfied by behaviour — both read source.

The behavioural tests in test_api_approvals.py stay GREEN if a route imports the
guard and posts directly, because the outcome looks identical from outside. Only
an import check can see the second door.
"""

import pathlib

ROUTES = pathlib.Path("src/invoice_agent/api/routes")
QUERIES = pathlib.Path("src/invoice_agent/db/queries.py")


def test_no_route_reaches_the_ledger_directly():
    """THE LEDGER HAS ONE DOOR. Posting reaches `journal_entries` only through
    submit_decision -> post_entry -> the guard.

    Extends `test_only_the_guard_may_call_the_journal_repository` (M5-A), which
    allowlists guards.py as the sole caller of the journal repository. This stops
    a ROUTE from becoming the second caller by importing the guard and choosing
    its own posting_authority — a bypass no behavioural test would notice,
    because a correctly-posted entry looks the same either way.
    """
    offenders = []
    for path in ROUTES.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "tools.guards" in source or "tools import guards" in source:
            offenders.append(f"{path}: imports tools.guards")
        if "repositories.journal" in source or "repositories import journal" in source:
            offenders.append(f"{path}: imports the journal repository")
    assert not offenders, (
        "the ledger must be reached through submit_decision only: " + "; ".join(offenders)
    )


def test_queries_module_is_read_only():
    """`db/queries.py` exists to serve reads. A write here would be a second
    write path beside the repositories, outside every guard they carry."""
    source = QUERIES.read_text(encoding="utf-8").lower()
    for forbidden in (".insert(", ".update(", ".delete(", "insert into", "update ", "delete from"):
        assert forbidden not in source, f"db/queries.py contains a write: {forbidden!r}"


def test_the_decision_schema_carries_no_authority_fields():
    """A field audit, not a behaviour test. `test_a_client_cannot_set_posting_authority`
    proves ONE field is rejected; this proves the dangerous ones were never
    declared — design §9.2's argument, transposed from models to clients."""
    from invoice_agent.api.schemas import DecisionRequest

    fields = set(DecisionRequest.model_fields)
    assert fields == {"decision", "decided_by", "reason", "corrections"}
    assert DecisionRequest.model_config.get("extra") == "forbid"
