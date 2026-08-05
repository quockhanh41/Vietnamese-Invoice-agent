"""The server is GENERATED from the registry, so the two cannot disagree.

A hand-written registration list would let a tool exist in one and not the other
— and the architecture test only reads the registry, so the drift would be
invisible to exactly the guard meant to catch it.
"""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from invoice_agent.mcp.server import build_server
from invoice_agent.tools import registry


async def _tool_names(server: FastMCP) -> set[str]:
    """FastMCP's real tool-enumeration API, verified against the installed
    fastmcp==3.4.5 source rather than assumed (the mistake M6-B made for
    LangGraph node introspection): `FastMCP.list_tools()`
    (fastmcp/server/server.py:651, overriding `Provider.list_tools`) is
    async and returns a `Sequence[Tool]`; `Tool.name`
    (fastmcp/utilities/components.py:92, `FastMCPComponent`) is a plain str.
    """
    tools = await server.list_tools()
    return {tool.name for tool in tools}


async def _resource_uri_templates(server: FastMCP) -> set[str]:
    """Same verification, for resources: all five of ours have `{param}`
    placeholders, so they register as `ResourceTemplate`, not `Resource`.
    `FastMCP.list_resource_templates()` returns `Sequence[ResourceTemplate]`,
    and `.uri_template` (fastmcp/resources/template.py:174) holds the exact
    registered string, e.g. "invoice://{invoice_id}" -- confirmed by
    registering one and reading it back before relying on it here.
    """
    templates = await server.list_resource_templates()
    return {t.uri_template for t in templates}


async def test_every_exposed_tool_is_registered_on_the_server():
    server = build_server()
    registered = await _tool_names(server)
    expected = {spec.name for spec in registry.exposed_tools()}
    assert registered == expected


async def test_no_unexposed_tool_leaks_onto_the_server():
    hidden = {spec.name for spec in registry.REGISTRY if not spec.mcp_exposed}
    registered = await _tool_names(build_server())
    assert registered & hidden == set()


async def test_the_five_resources_are_registered():
    expected = {
        "invoice://{invoice_id}",
        "invoice-xml://{invoice_id}",
        "budget://{department}/{period}",
        "vendor://{tax_code}",
        "report://{period}",
    }
    assert await _resource_uri_templates(build_server()) == expected


# ---------------------------------------------------------------------------
# Step 4: a real MCP client drives the server end-to-end, not just objects
# constructed in-process.
# ---------------------------------------------------------------------------


async def test_list_tools_over_a_real_mcp_client():
    async with Client(build_server()) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools} == {spec.name for spec in registry.exposed_tools()}


async def test_get_budget_status_over_a_real_mcp_client(requires_postgres):
    """MKT/2026-07 is seeded by `scripts/seed_reference.py` (limit
    50,000,000 -- the same row `tests/test_api_read.py`'s
    `test_budget_report_shows_consumed_against_limit` reads), so this
    exercises real, already-committed data: the tool's own fresh connection
    (opened inside the server, via `get_engine()`) has to see it independent
    of any fixture transaction this test might otherwise hold open.
    """
    async with Client(build_server()) as client:
        result = await client.call_tool(
            "get_budget_status", {"department": "MKT", "period": "2026-07"}
        )
    lines = result.structured_content["lines"]
    mkt = next(line for line in lines if line["department_code"] == "MKT")
    assert mkt["amount_limit"] == "50000000"
    assert isinstance(mkt["amount_limit"], str)  # money rule: string, never float


async def test_invalid_group_by_surfaces_legibly_to_the_client(requires_postgres):
    """The Task 3 review's follow-up: does a `ValueError` raised inside a
    handler body (`queries.SPEND_SUMMARY_GROUP_COLUMNS` check) reach the
    client legibly, or does it come back as an opaque 500-style message?

    Verified against fastmcp==3.4.5 (`server/server.py::FastMCP.call_tool`):
    an uncaught `Exception` from a tool body is re-raised as `ToolError(f"Error
    calling tool {name!r}: {e}")` -- the ORIGINAL message text is included by
    default (`mask_error_details=False`, `settings.py`), not swallowed.
    """
    async with Client(build_server()) as client:
        raw = await client.call_tool_mcp(
            "spend_summary", {"period": "2026-07", "group_by": "bogus"}
        )
        assert raw.isError
        assert "group_by must be one of" in raw.content[0].text

        with pytest.raises(ToolError, match="group_by must be one of"):
            await client.call_tool("spend_summary", {"period": "2026-07", "group_by": "bogus"})
