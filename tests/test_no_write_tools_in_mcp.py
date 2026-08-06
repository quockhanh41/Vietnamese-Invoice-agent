"""The architecture decision M7 exists to defend.

Design §8.2 rejects exposing write tools over MCP: MCP is stateless
request/response and knows nothing about approval state, so a `create_journal_entry`
reachable from any client would let anyone post to the ledger — contradicting the
one property this whole project demonstrates.

Exactly ONE write is permitted, and it is human-authorised: `submit_approval_decision`,
which resumes an existing interrupt rather than inventing an approval.

These tests enumerate the REGISTRY, never a hand-written list. A second list drifts,
and this repo has already shipped a test that certified nodes it could not see (M6-B).
"""

from invoice_agent.tools import registry


def test_no_unlisted_write_tool_is_exposed_over_mcp():
    offenders = [
        spec.name
        for spec in registry.REGISTRY
        if spec.side_effect == "write"
        and spec.mcp_exposed
        and spec.name not in registry.MCP_WRITE_ALLOWLIST
    ]
    assert offenders == [], f"write tools exposed over MCP without an allowlist entry: {offenders}"


def test_the_allowlist_is_exactly_the_one_tool_design_8_2_permits():
    """Pins the allowlist ITSELF.

    Without this, widening the surface costs one word in a set literal and no
    test notices. The friction is the feature: adding a second write tool must
    require editing this assertion, which is a decision someone has to defend.
    """
    assert frozenset({"submit_approval_decision"}) == registry.MCP_WRITE_ALLOWLIST


def test_the_ledger_write_is_not_in_the_registry_at_all():
    """`create_journal_entry` must not be registered even as unexposed.

    Registering it with `mcp_exposed=False` would leave the safety property one
    boolean away from gone.
    """
    names = {spec.name for spec in registry.REGISTRY}
    assert "create_journal_entry" not in names
    assert "post_batch" not in names


def test_every_registered_handler_is_callable():
    """A ToolSpec whose handler is a string or None would fail only at call
    time, i.e. in front of Claude Desktop."""
    broken = [spec.name for spec in registry.REGISTRY if not callable(spec.handler)]
    assert broken == []


def test_read_tools_never_point_at_the_ledger_guard_module():
    """The brief's own `test_read_tools_declare_no_side_effect` asserted only
    `spec.side_effect in {"read", "write"}` — true of any ToolSpec by
    construction (the field is typed `Literal["read", "write"]`), so it could
    never fail and was checking nothing.

    A real version of "read tools declare no side effect": a read tool whose
    handler resolves into `tools.guards` would defeat
    `test_no_unlisted_write_tool_is_exposed_over_mcp` by mislabelling instead of
    by omission — labelled `side_effect="read"`, it would never be checked
    against `MCP_WRITE_ALLOWLIST` at all, yet it would still be the one
    production door to `journal_entries` (guards.py's own module docstring).
    This asserts no registered read tool takes that door.
    """
    guard_module = "invoice_agent.tools.guards"
    offenders = [
        spec.name
        for spec in registry.REGISTRY
        if spec.side_effect == "read" and spec.handler.__module__ == guard_module
    ]
    assert offenders == [], f"read tools whose handler lives in the ledger guard: {offenders}"


# Considered and rejected: widening the check above to "any read handler whose
# MODULE is a write-capable repository" rather than only `tools.guards`. It
# does not fit cleanly — `list_pending_approvals` is a real, already-registered
# read tool whose handler (`approvals_repo.list_pending`) lives in
# `invoice_agent.db.repositories.approvals`, a module that ALSO contains
# `open_approval`/`record_decision` writes. A module-level check would flag
# that legitimate tool as an offender on day one. Catching a mislabelled
# `ToolSpec(side_effect="read", handler=flags_repo.resolve_flag)` needs a
# per-FUNCTION write/read classification, not a per-module one — that is a
# second, hand-maintained list of which functions in each repository write,
# the exact kind of second source of truth this project's registry already
# exists to avoid. Left as a known gap rather than papered over with a check
# that would need its own allowlist to stay correct.
