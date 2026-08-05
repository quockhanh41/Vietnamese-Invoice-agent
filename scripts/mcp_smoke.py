"""Smoke test for the MCP server: proves the M7 gate question end-to-end.

Drives `build_server()` through a real `fastmcp.Client` -- the exact same
machinery `tests/test_mcp_server.py`'s "... over a real MCP client" tests
already use (e.g. `test_get_budget_status_over_a_real_mcp_client`). Reused
here rather than inventing stdio subprocess plumbing: `Client(build_server())`
already speaks the real MCP protocol (tool discovery, JSON-schema argument
validation, `call_tool`) against the in-process server object; it just skips
spawning a second OS process to do it, which is not a shortcut this script
takes but the pattern the test suite already established and this script
follows (per the Task 6 brief).

Answers the M7 gate's question -- "which department went over budget in
July?" -- from two angles, per the brief:

  * `get_budget_status(period=...)`: the AUTHORITATIVE answer. Its `remaining`
    field is `amount_limit - consumed`, and `consumed` is a sum over
    `app.budget_consumption` -- rows the batch graph's `serial_join` writes
    only for an invoice whose department was DECLARED at upload time (design
    decision in `api/routes/batches.py`, not this script's). A negative
    `remaining` is a real overspend against a real seeded limit.

  * `spend_summary(period=..., group_by="department")`: a SEPARATE, cheaper
    metric -- the raw sum of `invoices.total_amount` grouped by department,
    with NO reference to any budget limit or to whether the invoice was ever
    reconciled against one. Printed for context, not as the answer: an
    invoice can be persisted (and so counted here) without ever being
    budget-reconciled (`BUDGET_UNRECONCILED`), so this total and
    `get_budget_status`'s `consumed` are expected to disagree.

Point-in-time note: this reads whatever is currently committed in Postgres.
`tests/conftest.py`'s session-scoped `TRUNCATE app.invoices CASCADE` means a
later `make check` empties `app.invoices` again -- and Postgres's
`TRUNCATE ... CASCADE` empties every table with a foreign key referencing it
(including `app.budget_consumption`) regardless of that key's own `ON DELETE`
rule -- this script's output is a snapshot of one manual data-load, not a
permanent fact about the database. See `docs/mcp-claude-desktop.md` and the
Task 6 report for exactly how that snapshot was produced.
"""

import asyncio
from decimal import Decimal
from typing import Any

from fastmcp import Client

from invoice_agent.mcp.server import build_server

PERIOD = "2026-07"


async def _fetch(period: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    async with Client(build_server()) as client:
        budget_result = await client.call_tool("get_budget_status", {"period": period})
        spend_result = await client.call_tool(
            "spend_summary", {"period": period, "group_by": "department"}
        )
    budget_lines: list[dict[str, Any]] = budget_result.structured_content["lines"]
    spend_lines: list[dict[str, Any]] = spend_result.structured_content["lines"]
    return budget_lines, spend_lines


def _print_budget_table(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    print(f"get_budget_status(period={PERIOD!r}):")
    over_budget = []
    for line in sorted(lines, key=lambda ln: ln["department_code"]):
        remaining = Decimal(line["remaining"])
        marker = "  <-- OVER BUDGET" if remaining < 0 else ""
        print(
            f"  {line['department_code']:<6} limit={line['amount_limit']:>12} "
            f"consumed={line['consumed']:>12} remaining={line['remaining']:>13}{marker}"
        )
        if remaining < 0:
            over_budget.append(line)
    return over_budget


def _print_spend_table(lines: list[dict[str, Any]]) -> None:
    print()
    print(f"spend_summary(period={PERIOD!r}, group_by='department') -- for context only, "
          "NOT the answer (see module docstring: unreconciled invoices count here):")
    for line in sorted(lines, key=lambda ln: ln["group_key"] or ""):
        print(
            f"  {line['group_key']:<6} invoices={line['invoice_count']:<4} "
            f"total={line['total_amount']}"
        )


def _print_answer(over_budget: list[dict[str, Any]]) -> None:
    print()
    if not over_budget:
        print(
            f"ANSWER: no department shows a negative `remaining` for {PERIOD} in "
            "app.budget_consumption right now -- either nothing has been loaded, or "
            "everything loaded is within budget."
        )
        return
    names = ", ".join(line["department_code"] for line in over_budget)
    print(f"ANSWER: {names} went over budget in {PERIOD}.")
    for line in over_budget:
        over_by = -Decimal(line["remaining"])
        print(
            f"  {line['department_code']} consumed {line['consumed']} against a "
            f"{line['amount_limit']} limit -- over by {over_by}."
        )


async def main() -> None:
    budget_lines, spend_lines = await _fetch(PERIOD)
    over_budget = _print_budget_table(budget_lines)
    _print_spend_table(spend_lines)
    _print_answer(over_budget)


if __name__ == "__main__":
    asyncio.run(main())
