"""Structural guards. Neither can be satisfied by behaviour — both read source.

The behavioural tests in test_api_approvals.py stay GREEN if a route imports the
guard and posts directly, because the outcome looks identical from outside. Only
an import check can see the second door.

M7 added a second transport that reaches the same handlers (`src/invoice_agent/mcp`),
so `test_no_route_reaches_the_ledger_directly` below walks that package too, not
only ROUTES: a mutation that changes the body of an existing MCP resource to call
`tools.guards.create_journal_entry` directly passes every behavioural test in
`tests/test_mcp_server.py` (the resource still returns a value) and, before this
change, passed `tests/test_no_write_tools_in_mcp.py` as well — that suite only
enumerates `registry.REGISTRY`, which holds the eight `ToolSpec`s, not the five
resources `mcp/server.py::_register_resources()` registers separately. Only an
import check can see this door either.
"""

import pathlib

ROUTES = pathlib.Path("src/invoice_agent/api/routes")
MCP = pathlib.Path("src/invoice_agent/mcp")
QUERIES = pathlib.Path("src/invoice_agent/db/queries.py")


def test_no_route_reaches_the_ledger_directly():
    """THE LEDGER HAS ONE DOOR. Posting reaches `journal_entries` only through
    submit_decision -> post_entry -> the guard.

    Extends `test_only_the_guard_may_call_the_journal_repository` (M5-A), which
    allowlists guards.py as the sole caller of the journal repository. This stops
    a ROUTE, or an MCP tool/resource body, from becoming a second caller by
    importing the guard and choosing its own posting_authority — a bypass no
    behavioural test would notice, because a correctly-posted entry looks the
    same either way.

    `repositories.flags` is additionally forbidden for MCP specifically (not for
    ROUTES, where `api/routes/invoices.py` legitimately reads it inline via
    `flags.open_flags_for`): MCP has no legitimate reason to import the flags
    repository directly at all — every read of a flag already goes through an
    existing handler (`queries.invoice_detail`, `tools.explain.explain_flags`)
    that imports it elsewhere. A resource or tool body that imported it directly
    would be one call away from `flags_repo.resolve_flag` — a write nothing
    else in this project's architecture tests catches, because it is named
    neither `create_journal_entry` nor `post_batch` and its handler module is
    not `tools.guards`.
    """
    offenders = []
    for path in ROUTES.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "tools.guards" in source or "tools import guards" in source:
            offenders.append(f"{path}: imports tools.guards")
        if "repositories.journal" in source or "repositories import journal" in source:
            offenders.append(f"{path}: imports the journal repository")
    for path in MCP.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "tools.guards" in source or "tools import guards" in source:
            offenders.append(f"{path}: imports tools.guards")
        if "repositories.journal" in source or "repositories import journal" in source:
            offenders.append(f"{path}: imports the journal repository")
        if "repositories.flags" in source or "repositories import flags" in source:
            offenders.append(f"{path}: imports the flags repository")
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
