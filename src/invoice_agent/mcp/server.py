"""The MCP server, GENERATED from `tools/registry.py` (design §8.1/§8.2).

`build_server()` loops `registry.exposed_tools()` -- there is no hand-written
per-tool registration list. A hand-written list could drift from the registry
(a tool added to one and not the other) invisibly to `test_no_write_tools_in_mcp.py`,
which only ever reads the registry; looping the registry here is what keeps
that drift structurally impossible rather than merely disciplined against.

Handlers are the EXISTING functions from `db/queries`, the repositories,
`memory/vendor_profile` and `tools/explain` -- never reimplemented here. Each
call opens its own connection via `get_engine()`: the MCP process is a
long-lived stdio server, not a FastAPI request, so there is no request-scoped
connection to reuse (verified against `db/engine.py`'s own docstring).

`submit_approval_decision` (Task 5, the one write MCP is allowed under
`registry.MCP_WRITE_ALLOWLIST`) needs no separate wiring here -- it is a
`ToolSpec` like any other, registered by the same loop. `mcp/approvals.py`
holds its two gates; this file stays exactly as generic as it already was.
"""

import dataclasses
import inspect
import uuid
from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.resources import ResourceContent, ResourceResult
from pydantic import BaseModel

from invoice_agent.db import queries
from invoice_agent.db.engine import get_engine
from invoice_agent.db.repositories import invoices as invoices_repo
from invoice_agent.memory import vendor_profile
from invoice_agent.tools import models, registry


def _adapt_result(return_model: type[BaseModel], result: Any) -> BaseModel:
    """Shape a handler's raw return value into its `ToolSpec.return_model`.

    Handlers predate the registry and each returns whatever it always
    returned: a bare list (`list_invoices`, `budget_report`, `list_pending`,
    `spend_summary`), a dict already keyed to match (`invoice_detail`), a
    dataclass (`vendor_profile.stats_for`'s `VendorStats`), or -- for
    `explain_flags` -- the exact `return_model` instance already. This
    dispatches on the VALUE'S SHAPE, not on which tool is calling: every
    `*Result` model in `tools/models.py` carries exactly one list field (true
    by inspection of every model in that file as of Task 3/4), so "the sole
    field" is never ambiguous, and a model that stopped satisfying that would
    fail loudly here (tuple-unpack mismatch) rather than silently.

    `result is None` means "the handler found nothing for these arguments"
    (`invoice_detail`, `stats_for` both return `None` on a miss) -- raised as
    a `ValueError` so it surfaces the same legible way §Task 6 verified for
    `spend_summary`'s bad `group_by`, not swallowed into an empty object that
    would misreport a real "not found" as "found, all fields blank".
    """
    if isinstance(result, return_model):
        return result
    if result is None:
        raise ValueError(f"no {return_model.__name__} for the given arguments")
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        result = dataclasses.asdict(result)
    if isinstance(result, list):
        (field_name,) = return_model.model_fields.keys()
        return return_model(**{field_name: result})
    return return_model(**result)


def _make_tool_fn(spec: registry.ToolSpec) -> Callable[..., dict[str, Any]]:
    """Build the callable FastMCP registers for one `ToolSpec`.

    Verified against the installed fastmcp==3.4.5 source
    (`tools/function_parsing.py::ParsedFunction.from_function`): a function
    accepting `**kwargs` is REJECTED outright ("Functions with **kwargs are
    not supported as tools"), and a function taking a single argument typed
    as `spec.args_model` is accepted but nests every field one level down
    under an "args" key in the generated schema -- confirmed by constructing
    both and inspecting `FunctionTool.from_function(...).parameters` -- which
    would make every tool call read `{"args": {"period": ...}}` instead of
    the flat `{"period": ...}` a human (or Claude Desktop) would actually
    type.

    The fix, also verified empirically: `inspect.signature()` and pydantic's
    own call-validation both honor an explicitly assigned `fn.__signature__`
    (and, separately, `fn.__annotations__` for `typing.get_type_hints`), even
    though the function's real code still takes `**kwargs`. So one generic
    wrapper, with a signature built from `spec.args_model.model_fields`,
    gives FastMCP a flat, per-field schema while the function body still
    forwards through `**kwargs` to `spec.handler(conn, **kwargs)`. This also
    means pydantic's own type coercion (str -> `uuid.UUID`, str -> `Decimal`,
    etc.) and its `extra="forbid"`-equivalent rejection of an unrecognized
    keyword argument both apply for free -- verified by calling a
    reconstructed tool with an unexpected field and observing
    `ToolError: ... Unexpected keyword argument`.
    """
    parameters = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            default=inspect.Parameter.empty if field.is_required() else field.default,
            annotation=field.annotation,
        )
        for name, field in spec.args_model.model_fields.items()
    ]
    annotations = {name: field.annotation for name, field in spec.args_model.model_fields.items()}
    assert spec.return_model is not None  # every read tool in the registry declares one
    return_model = spec.return_model

    def tool_fn(**kwargs: Any) -> dict[str, Any]:
        with get_engine().begin() as conn:
            result = spec.handler(conn, **kwargs)
        return _adapt_result(return_model, result).model_dump(mode="json")

    tool_fn.__name__ = spec.name
    # Not a typeshed-known attribute on a plain function, but verified load-bearing
    # (see docstring above): both `inspect.signature()` and pydantic's call-validation
    # honor it.
    tool_fn.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters, return_annotation=return_model
    )
    annotations["return"] = return_model
    tool_fn.__annotations__ = annotations
    return tool_fn


def _resource_payload(return_model: type[BaseModel], result: Any) -> dict[str, Any]:
    """`.model_dump(mode="json")` BEFORE returning to FastMCP.

    Verified against fastmcp==3.4.5 (`resources/base.py::Resource.convert_result`):
    a resource function's plain-dict return is serialized with stdlib
    `json.dumps`, NOT `pydantic_core.to_jsonable_python` the way a TOOL result
    is (`tools/base.py::Tool.convert_result`). `json.dumps` raises
    `TypeError: Object of type Decimal is not JSON serializable` on a raw
    Decimal -- reproduced directly against `budget://{department}/{period}`
    returning an un-dumped dict of Decimal amounts. `model_dump(mode="json")`
    turns Decimal/UUID/date into `str` before the dict ever reaches
    `json.dumps`, so the money rule (Decimal or string, never float) holds on
    the resource path too, not only the tool path.
    """
    return _adapt_result(return_model, result).model_dump(mode="json")


def _register_resources(server: FastMCP) -> None:
    """The five read-only resources design §3.3 names.

    Unlike the tool loop in `build_server`, this is a fixed set of five --
    there is no "resource registry" in `tools/registry.py` to generate them
    from. Each reuses the same handler its sibling tool (or `db/queries`
    directly) already calls; none reimplements a query.

    `report://{period}` is a judgment call the design doc leaves open (it
    pins the five URI shapes, §3.3, but not each one's exact content): reused
    here as `budget_report(period=period)` with `department` left unset --
    the budget report across every department for the period -- rather than
    inventing a `group_by` default for `spend_summary`, which the resource's
    URI has no parameter to carry.
    """

    @server.resource(
        "invoice://{invoice_id}",
        description="Full detail (fields, line items, open flags) for one invoice.",
    )
    def read_invoice(invoice_id: str) -> dict[str, Any]:
        with get_engine().begin() as conn:
            result = queries.invoice_detail(conn, uuid.UUID(invoice_id))
        return _resource_payload(models.InvoiceDetail, result)

    @server.resource(
        "invoice-xml://{invoice_id}",
        mime_type="application/xml",
        description=(
            "Raw source XML for one invoice, exactly as uploaded -- NOT redacted. "
            "A real deployment handling real invoices must gate this (Task 7 note); "
            "this resource serves whatever was uploaded, unconditionally."
        ),
    )
    def read_invoice_xml(invoice_id: str) -> ResourceResult:
        # A bare `str` return would NOT carry the `mime_type="application/xml"`
        # declared above: verified against fastmcp==3.4.5
        # (`resources/template.py::ResourceTemplate.convert_result`) that for a
        # template resource, a plain str/bytes return goes through
        # `ResourceResult(raw_value)`, which ALWAYS normalizes to "text/plain"
        # (`ResourceResult._normalize_contents`) -- the decorator's `mime_type`
        # only affects the ADVERTISED template metadata (what
        # `list_resource_templates()` shows), not the actually-delivered
        # content, unless the function itself returns an explicit
        # `ResourceResult`/`ResourceContent` carrying the mime type. Confirmed by
        # reading back a real committed invoice's raw XML through a real client
        # before this fix (mimeType came back "text/plain") and after (came
        # back "application/xml").
        with get_engine().begin() as conn:
            row = invoices_repo.get_by_id(conn, uuid.UUID(invoice_id))
        if row is None:
            raise ValueError(f"invoice {invoice_id} not found")
        return ResourceResult(
            contents=[ResourceContent(row["raw_xml"] or "", mime_type="application/xml")]
        )

    @server.resource(
        "budget://{department}/{period}",
        description="Budget status (limit, consumed, remaining) for one department and period.",
    )
    def read_budget(department: str, period: str) -> dict[str, Any]:
        with get_engine().begin() as conn:
            result = queries.budget_report(conn, department=department, period=period)
        return _resource_payload(models.GetBudgetStatusResult, result)

    @server.resource(
        "vendor://{tax_code}",
        description="Vendor entity-memory statistics for one seller tax code.",
    )
    def read_vendor(tax_code: str) -> dict[str, Any]:
        with get_engine().begin() as conn:
            result = vendor_profile.stats_for(conn, tax_code)
        return _resource_payload(models.VendorProfile, result)

    @server.resource(
        "report://{period}",
        description="Budget report for one period, across every department.",
    )
    def read_report(period: str) -> dict[str, Any]:
        with get_engine().begin() as conn:
            result = queries.budget_report(conn, period=period)
        return _resource_payload(models.GetBudgetStatusResult, result)


def build_server() -> FastMCP:
    """Generated from `registry.exposed_tools()` -- never a hand-written list.

    The server never filters for itself (mirroring `registry.exposed_tools()`'s
    own docstring): everything this loop is handed, it registers.
    """
    server: FastMCP = FastMCP(name="invoice-agent")

    for spec in registry.exposed_tools():
        server.tool(name=spec.name, description=spec.description)(_make_tool_fn(spec))

    _register_resources(server)
    return server


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
