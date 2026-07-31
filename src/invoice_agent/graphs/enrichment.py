"""enrichment_graph — one invoice, from bytes to findings (design doc §3).

Five nodes, exactly ONE of which calls an LLM. That ratio is the design (§3.2),
not thrift: parsing, arithmetic and raising a flag are things a computer must do
EXACTLY, not things a model does approximately.

M3 builds the extraction half. `classify`, `audit` and `signals` splice in
before END at M4.

REDUCER RULE. `error_history` and `llm_calls` accumulate with `operator.add`,
because each entry records something that HAPPENED and cannot un-happen.
`findings` and `current_errors` REPLACE, because they describe the current
extraction and a retry supersedes them. Get this backwards on `findings` and a
successful retry still routes as a failure — its predecessor's findings are
still in the list. Nodes therefore return DELTAS for the accumulating fields;
returning the whole list makes each pass double the last.
"""

import functools
import operator
import pathlib
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from invoice_agent.db.engine import get_engine
from invoice_agent.domain import anomaly, validation
from invoice_agent.domain.findings import Finding
from invoice_agent.extraction import vision
from invoice_agent.extraction.models import InvoiceData
from invoice_agent.extraction.xml_parser import parse_invoice_xml
from invoice_agent.extraction.xml_schema import XmlExtractionError
from invoice_agent.llm import telemetry
from invoice_agent.llm.cost import LLMCallRecord
from invoice_agent.llm.gateway import LLMGateway
from invoice_agent.llm.structured import StructuredOutputError
from invoice_agent.memory import vendor_profile

# Three vision attempts, from design doc §3.3. Termination is OURS, not the
# framework's: LangGraph's recursion limit is never approached because this
# bound cuts the loop first (spec §8.2).
MAX_VISION_ATTEMPTS = 3


class EnrichmentState(TypedDict):
    invoice_id: str
    source_path: str
    # The escalation target. Design §3.3 routes a failed XML extraction to
    # vision "if a PDF is attached"; the corpus supplies it as `pdf_path`.
    pdf_path: str | None
    source_type: Literal["xml", "pdf"]
    strategy: Literal["xml", "vision", "manual"]
    extracted: InvoiceData | None
    extraction_attempts: int
    current_errors: list[str]
    error_history: Annotated[list[str], operator.add]
    findings: list[Finding]
    llm_calls: Annotated[list[LLMCallRecord], operator.add]
    # Entity-memory findings (M4-B). Accumulates: each entry records something
    # observed and cannot un-happen. Nodes return DELTAS.
    signals: Annotated[list[Finding], operator.add]
    status: str


def _describe(finding: Finding) -> str:
    """A finding as one line a vision model can act on.

    Codes and detail keys are identifiers, not prose, so they stay as they are;
    this string lands inside `sanitize.wrap` on its way into the retry prompt.
    """
    detail = ", ".join(f"{k}={v}" for k, v in sorted(finding.detail.items()))
    return f"{finding.code}: {detail}" if detail else finding.code


def route_source(state: EnrichmentState) -> dict[str, Any]:
    """Pick the first extractor. No LLM — this is one comparison."""
    return {"strategy": "xml" if state["source_type"] == "xml" else "vision"}


def parse_xml_node(state: EnrichmentState) -> dict[str, Any]:
    """Deterministic extraction. Same bytes in, same InvoiceData out."""
    try:
        data = parse_invoice_xml(pathlib.Path(state["source_path"]).read_bytes())
    except XmlExtractionError as exc:
        return {
            "extracted": None,
            "current_errors": [str(exc)],
            "error_history": [str(exc)],
        }
    return {"extracted": data, "current_errors": []}


def vision_extract_node(
    state: EnrichmentState, *, gateway: LLMGateway
) -> dict[str, Any]:
    """The one node in this graph that spends a token.

    Reads `pdf_path` when escalating from XML, `source_path` when the invoice
    was PDF-only.
    """
    source = state.get("pdf_path") or state["source_path"]
    calls: list[LLMCallRecord] = []
    attempt = state.get("extraction_attempts", 0) + 1

    try:
        data: InvoiceData | None = vision.extract(
            pathlib.Path(source).read_bytes(),
            gateway=gateway,
            current_errors=state.get("current_errors") or None,
            calls=calls,
        )
        errors: list[str] = []
    except (vision.VisionExtractionError, StructuredOutputError) as exc:
        data, errors = None, [str(exc)]

    _persist_cost(calls, invoice_id=state["invoice_id"])
    return {
        "strategy": "vision",
        "extracted": data,
        "extraction_attempts": attempt,
        "current_errors": errors,
        "error_history": errors,
        "llm_calls": calls,
    }


def _persist_cost(calls: list[LLMCallRecord], *, invoice_id: str) -> None:
    """Write the rows the moment the tokens are spent (spec §5.3).

    `invoice_id` is left NULL on purpose: a PDF-only invoice has no `invoices`
    row until vision has already run, so the node that spends the token cannot
    name it. `thread_id` is the correlation key design §1.3 specifies, and
    `scripts/run_one.py` backfills through it. Writing after the run instead
    would lose every call already billed if the run dies — the exact failure
    design §2.9 exists to prevent.
    """
    if not calls:
        return
    with get_engine().begin() as conn:
        telemetry.record(
            conn, calls, node="vision_extract", thread_id=f"enrich:{invoice_id}"
        )


def validate_node(state: EnrichmentState) -> dict[str, Any]:
    """Deterministic checks (M2-B). Renders findings into the strings the retry
    prompt and the router both need."""
    data = state.get("extracted")
    if data is None:
        return {}
    found = validation.validate(data)
    messages = [_describe(f) for f in found]
    return {"findings": found, "current_errors": messages, "error_history": messages}


def manual_queue_node(state: EnrichmentState) -> dict[str, Any]:
    """A controlled give-up. State only — `invoices.status` belongs to M4's
    batch graph (design §4), and M3 agreed the graph's one DB surface is
    `llm_calls`."""
    return {"status": "manual_queue"}


def signals_node(state: EnrichmentState, *, conn: Any = None) -> dict[str, Any]:
    """Entity memory -> findings. No LLM, and no writes.

    Reads the vendor profile as it stands at BATCH START, because M4-D persists
    invoices in a serial node after the fan-out join and this runs inside the
    fan-out. That ordering is what keeps the scored invoice out of its own
    statistics — measured 2026-07-31, a single extreme point's z-score against a
    sample containing itself is bounded by (n-1)/sqrt(n), which is 1.79 at n=5
    and puts PRICE_OUTLIER below its own threshold at every realistic vendor
    size. Persist before this runs and the finding silently stops firing.
    """
    data = state.get("extracted")
    if data is None:
        return {}

    if conn is not None:
        found = _signals_for(conn, data)
    else:
        with get_engine().begin() as owned:
            found = _signals_for(owned, data)
    return {"signals": found}


def _signals_for(conn: Any, data: InvoiceData) -> list[Finding]:
    # `seller_tax_code` is `str | None` on InvoiceData — M1 measured that a tax
    # code has three ways of being absent ('0', empty element, missing element).
    # Without a seller there is no vendor to have memory of, and raising
    # NEW_VENDOR here would be wrong: the vendor may be well known, we just
    # cannot identify it. `validation.TAX_CODE_MISSING` already carries that.
    if not data.seller_tax_code:
        return []
    stats = vendor_profile.stats_for(conn, data.seller_tax_code)
    out: list[Finding] = []
    new = anomaly.new_vendor(stats)
    if new is not None:
        out.append(new)
    if stats is not None:
        outlier = anomaly.price_outlier(data.total_amount, stats)
        if outlier is not None:
            out.append(outlier)
    return out


def route_after_validate(state: EnrichmentState) -> str:
    """Tell an extraction error from a data error — design doc §3.3.

    The split is PER-STRATEGY, not per-finding-code, and it falls out of one
    property: the XML parser is a pure function and vision is not.

      * XML + findings  -> a DATA error. The invoice is wrong, we read it
        correctly, and retrying a deterministic parser three times yields three
        identical results. Carry the flags to the human.
      * vision + findings -> possibly an EXTRACTION error: the same
        ARITHMETIC_MISMATCH may be misread digits. Retry with the concrete
        error in the prompt, which §3.3 says is the retry that actually works.
    """
    if state.get("extracted") is None:
        # Nothing was read. Escalate a strategy at a time.
        if state["strategy"] == "xml" and state.get("pdf_path"):
            return "vision_extract"
        if (
            state["strategy"] == "vision"
            and state.get("extraction_attempts", 0) < MAX_VISION_ATTEMPTS
        ):
            return "vision_extract"
        return "manual_queue"

    if not state.get("findings"):
        return END

    if state["strategy"] == "xml":
        return END

    if state.get("extraction_attempts", 0) < MAX_VISION_ATTEMPTS:
        return "vision_extract"
    return "manual_queue"


def build_enrichment_graph(
    *, gateway: LLMGateway | None = None, checkpointer: Any = None
) -> Any:
    """Compile the graph. `gateway` is injected so tests can stub it without
    reaching into module globals."""
    gw = gateway or LLMGateway()

    builder = StateGraph(EnrichmentState)
    builder.add_node("route_source", route_source)
    builder.add_node("parse_xml", parse_xml_node)
    builder.add_node(
        "vision_extract", functools.partial(vision_extract_node, gateway=gw)
    )
    builder.add_node("validate", validate_node)
    builder.add_node("signals", signals_node)
    builder.add_node("manual_queue", manual_queue_node)

    builder.add_edge(START, "route_source")
    builder.add_conditional_edges(
        "route_source",
        lambda s: "vision_extract" if s["strategy"] == "vision" else "parse_xml",
        {"parse_xml": "parse_xml", "vision_extract": "vision_extract"},
    )
    builder.add_edge("parse_xml", "validate")
    builder.add_edge("vision_extract", "validate")
    builder.add_conditional_edges(
        "validate",
        route_after_validate,
        {
            "vision_extract": "vision_extract",
            "manual_queue": "manual_queue",
            # Until M4-C splices classify/audit in front, a clean invoice and a
            # DATA-error invoice both land here. `route_after_validate` still
            # returns END; this map sends that outcome to `signals` instead.
            END: "signals",
        },
    )
    builder.add_edge("signals", END)
    builder.add_edge("manual_queue", END)

    return builder.compile(checkpointer=checkpointer)

