# M3-C: Enrichment Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the extraction pipeline into one LangGraph graph whose routing tells an extraction error apart from a data error — and close the M3 gate.

**Architecture:** `graphs/enrichment.py` holds a `TypedDict` state, five nodes (one of which calls an LLM) and `route_after_validate`, the function this milestone exists to get right. The graph compiles with a `PostgresSaver` whose serializer carries an explicit type allowlist, because strict msgpack degrades our dataclasses to `dict` **silently**. `scripts/run_one.py` drives one invoice end to end and links its cost to the invoice row that extraction produced.

**Tech Stack:** Python 3.13 · langgraph 1.2.9 (pinned) · langgraph-checkpoint-postgres 3.1.0 (pinned) · Postgres 17 · pytest

**Spec:** `docs/superpowers/specs/2026-07-29-m3c-enrichment-graph-design.md` — read §2 (four measurements) and §5.2 (the routing rule) before starting.

## Global Constraints

- **Authoring language is English** — comments, docstrings, commits, docs. Vietnamese stays only in text the product emits or ingests.
- **Every non-obvious constant carries a comment with date + measurement + source.**
- **`domain/` must not import `db/`; `db/` must not know about `llm/`.** `graphs/` may import all of them — it is the composition layer.
- **An agent response model must never carry approval/flag/status fields.** Nothing here defines one; `InvoiceData` is unchanged.
- **Do not bump `langgraph==1.2.9` or `langgraph-checkpoint-postgres==3.1.0`.** Spec §2.1 is precisely why.
- **No new dependencies.** The diagram ships as mermaid text for this reason (spec §2.3).
- **`make check` = ruff + mypy + pytest** over `src`, `tests`, `migrations`, `scripts`. Green before any task is done — and green **at the closing commit**, which is where M2-C and M3-B both failed.
- **`LLM_MODE=replay` with no API keys must stay green.** Graph tests stub `litellm.completion` and pin `llm_mode="live"` on their `Settings`, exactly as `make_gateway()` does — a test that behaves differently under the two commands is not hermetic.
- **A guard is worth nothing until it has been seen red.** Task 7 re-breaks every guard.
- Tasks 1, 2, 3, 5 need Postgres (`make dev`). Task 5 also needs a generated corpus (`make generate`).

---

## Two corrections to the spec, both found while deriving the code

The plan implements these; the spec is wrong as written and Task 7 records it.

**1. `findings` must be `replace`, not `operator.add`.** Spec §4 gives it `Annotated[list[Finding], operator.add]` and says all three accumulating fields use `add`. That breaks the loop: after a failed vision attempt, retry 2's `validate` would *append* to retry 1's findings, so `if not state["findings"]` can never become true again and a successful retry still routes as if it had failed. The rule that works is **cumulative iff the entry records something that happened; replace iff it describes current state** — so `error_history` and `llm_calls` accumulate, `findings` and `current_errors` replace.

**2. The state needs `pdf_path`.** Spec §5.2 routes a failed XML extraction to vision "if a PDF exists", but spec §4's state carries only `source_path`. The escalation target is the manifest's `pdf_path`, so the state must carry it.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/invoice_agent/serde.py` | **Create.** The msgpack type allowlist and `build_serde()`. No graph knowledge. |
| `src/invoice_agent/checkpointer.py` | **Modify.** Assign the allowlist serde onto the yielded saver. |
| `src/invoice_agent/graphs/__init__.py` | **Create.** Empty package marker. |
| `src/invoice_agent/graphs/enrichment.py` | **Create.** State, five nodes, `route_after_validate`, `build_enrichment_graph`. |
| `scripts/run_one.py` | **Create.** Manifest → graph → invoice row → cost backfill. |
| `docs/diagrams/enrichment_graph.mmd` | **Create**, generated. |
| `tests/test_serde.py` | **Create.** Strict round-trip guards. |
| `tests/test_enrichment_nodes.py` | **Create.** Node behaviour in isolation. |
| `tests/test_enrichment_routing.py` | **Create.** `route_after_validate` — the heart. |
| `tests/test_enrichment_graph.py` | **Create.** Compiled-graph runs, the reducer trap, the diagram guard. |
| `tests/test_run_one.py` | **Create.** The M3 gate sentence. |
| `scripts/render_graph.py` | **Create** in Task 5. Regenerates the committed diagram. |
| `docs/notes/2026-07-29-m3c-enrichment-graph.md` | **Create** in Task 7. |
| `docs/Plan_Project_F_2026-07-25.md` | **Modify** in Task 7. Mark 3.5 done, close the M3 gate, record what M3 still owes. |

---

### Task 1: The msgpack allowlist

Spec §2.1: with no allowlist, a `Finding` round-trips as a plain `dict` and **does not raise**.

**Files:**
- Create: `src/invoice_agent/serde.py`
- Modify: `src/invoice_agent/checkpointer.py`
- Test: `tests/test_serde.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ALLOWED_MSGPACK_MODULES: list[tuple[str, str]]` and `build_serde() -> JsonPlusSerializer`. Task 2 adds a state-level guard using the same serde.

- [ ] **Step 1: Write the failing test**

Create `tests/test_serde.py`:

```python
"""Which of our types may cross a checkpoint boundary — and what happens if not.

MEASURED 2026-07-29 (spec §2.1): LangGraph's permissive default warns that it
"will be blocked in a future version". Blocked does NOT mean raised — a Finding
comes back as a plain `dict`, so the first symptom is an AttributeError far from
the cause. This file pins both halves.
"""

from datetime import date
from decimal import Decimal

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from invoice_agent.domain import findings
from invoice_agent.extraction.models import InvoiceData, InvoiceLine
from invoice_agent.llm.cost import LLMCallRecord
from invoice_agent.serde import ALLOWED_MSGPACK_MODULES, build_serde


def sample_objects() -> dict[str, object]:
    return {
        "Finding": findings.make(
            findings.ARITHMETIC_MISMATCH, total_amount=Decimal("1290107.52")
        ),
        "InvoiceData": InvoiceData(
            template_no="1",
            invoice_series="C26TAA",
            invoice_number="00000123",
            issue_date=date(2026, 7, 1),
            total_amount=Decimal("1290107.52"),
            lines=[
                InvoiceLine(
                    line_no=1, item_kind=1, description="Dịch vụ tư vấn",
                    amount=Decimal("1194544"),
                )
            ],
        ),
        "LLMCallRecord": LLMCallRecord(
            model="gemini/gemini-3.5-flash", provider="gemini",
            prompt_tokens=900, completion_tokens=120, latency_ms=800,
            attempt=1, succeeded=True,
        ),
    }


@pytest.mark.parametrize("name", list(sample_objects()))
def test_allowlisted_types_round_trip_with_their_type_intact(name):
    obj = sample_objects()[name]
    serde = build_serde()
    back = serde.loads_typed(serde.dumps_typed(obj))
    assert type(back) is type(obj), f"{name} degraded to {type(back).__name__}"
    assert back == obj


def test_without_the_allowlist_a_finding_degrades_to_a_dict_silently():
    """The measurement that justifies this module. Strict mode does not raise —
    it substitutes a dict, and nothing downstream notices until an attribute
    access fails somewhere unrelated."""
    strict = JsonPlusSerializer(allowed_msgpack_modules=None)
    obj = findings.make(findings.NEW_VENDOR)
    back = strict.loads_typed(strict.dumps_typed(obj))
    assert isinstance(back, dict), "measurement changed — re-read spec §2.1"
    assert back != obj


def test_every_allowlist_entry_is_importable():
    """A typo here fails open: the type silently leaves the allowlist and
    degrades exactly as above."""
    import importlib

    for module_name, attr in ALLOWED_MSGPACK_MODULES:
        module = importlib.import_module(module_name)
        assert hasattr(module, attr), f"{module_name}.{attr} does not exist"


def test_checkpointer_carries_the_allowlist(requires_postgres):
    """`from_conn_string` takes no serde (spec §2.2), so this is assigned after
    the saver is yielded. If that assignment is dropped, checkpointed state
    degrades — and nothing else would notice."""
    from invoice_agent.checkpointer import build_checkpointer

    with build_checkpointer() as saver:
        obj = findings.make(findings.NEW_VENDOR)
        back = saver.serde.loads_typed(saver.serde.dumps_typed(obj))
        assert type(back) is findings.Finding
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_serde.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'invoice_agent.serde'`

- [ ] **Step 3: Write the implementation**

Create `src/invoice_agent/serde.py`:

```python
"""Which of our own types may cross a checkpoint boundary.

MEASURED 2026-07-29 (spec M3-C §2.1) on langgraph 1.2.9. Every type below
round-trips today, and each emits:

    Deserializing unregistered type ... from checkpoint.
    This will be blocked in a future version.

"Blocked" is the dangerous part, because it is not an exception:

    default (permissive)   -> Finding, equal=True, with a warning
    strict, no allowlist   -> dict,    equal=False          <- silent
    explicit allowlist     -> Finding, equal=True, no warning

A `Finding` arriving as a plain dict fails at the first `finding.code` far from
the cause — or worse, subscripting code survives and the flag vocabulary
degrades unnoticed. `langgraph==1.2.9` is pinned to protect HITL durability
(roadmap §0 rule 6), and this is the mechanism by which a future bump would
break it QUIETLY. Registering here turns that into a present, tested guarantee.

Add a type to `EnrichmentState` and you must add it here —
`tests/test_serde.py` enumerates from a populated state, so the omission fails
in CI rather than in a checkpoint.
"""

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

ALLOWED_MSGPACK_MODULES: list[tuple[str, str]] = [
    ("invoice_agent.domain.findings", "Finding"),
    ("invoice_agent.extraction.models", "InvoiceData"),
    # Belt and braces, NOT a requirement. An earlier draft of this plan claimed
    # "msgpack registers types individually — allowlisting the container is not
    # enough". That was WRONG, measured 2026-07-29: with this line removed a
    # populated EnrichmentState still round-trips and `extracted.lines[0]` still
    # arrives as InvoiceLine, because pydantic reconstructs nested models from
    # the PARENT's payload. Keep it (it costs nothing and would matter if a line
    # became a top-level state value), but no test can catch its removal — see
    # the correction to mutation 7 below.
    ("invoice_agent.extraction.models", "InvoiceLine"),
    ("invoice_agent.llm.cost", "LLMCallRecord"),
]


def build_serde() -> JsonPlusSerializer:
    """The serializer every checkpointer in this project must use."""
    return JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_MSGPACK_MODULES)
```

- [ ] **Step 4: Wire it into the checkpointer**

In `src/invoice_agent/checkpointer.py`, add `from invoice_agent.serde import build_serde` and change the body of `build_checkpointer`:

```python
    s = settings or get_settings()
    with PostgresSaver.from_conn_string(s.checkpointer_dsn) as saver:
        # MEASURED 2026-07-29 (spec M3-C §2.2): `from_conn_string` accepts no
        # `serde` — only `PostgresSaver.__init__` does. Constructing directly
        # would mean copying LangGraph's own connection kwargs
        # (autocommit=True, prepare_threshold=0, row_factory=dict_row) into
        # this repo, where they would rot silently against a future patch —
        # the same shape as the M0.2 DSN-options defect. Assigning afterwards
        # is honoured, and leaves those kwargs LangGraph's business.
        saver.serde = build_serde()
        yield saver
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_serde.py -v`
Expected: PASS (6 tests). If the last one SKIPs, run `make dev`.

- [ ] **Step 6: Commit**

```bash
git add src/invoice_agent/serde.py src/invoice_agent/checkpointer.py tests/test_serde.py
git commit -m "feat(serde): register state types before strict msgpack turns them into dicts"
```

---

### Task 2: State and the five nodes

**Files:**
- Create: `src/invoice_agent/graphs/__init__.py`, `src/invoice_agent/graphs/enrichment.py`
- Test: `tests/test_enrichment_nodes.py`

**Interfaces:**
- Consumes: `parse_invoice_xml` (M1), `XmlExtractionError` (`extraction.xml_schema`), `vision.extract` / `VisionExtractionError` (M3-B), `StructuredOutputError` (M3-B), `domain.validation.validate` (M2-B), `telemetry.record` (M3-A), `build_serde` (Task 1).
- Produces: `EnrichmentState`, `route_source`, `parse_xml_node`, `vision_extract_node`, `validate_node`, `manual_queue_node`, `MAX_VISION_ATTEMPTS`, `build_enrichment_graph(*, gateway=None, checkpointer=None)`. Task 3 adds `route_after_validate`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_enrichment_nodes.py`:

```python
"""Each node in isolation. The graph that wires them is Task 3's problem."""

import pathlib
from decimal import Decimal

import pytest

from invoice_agent.config import Settings
from invoice_agent.graphs import enrichment
from invoice_agent.llm.gateway import LLMGateway
from tests.helpers.fake_llm import ok_response

REAL_XML = pathlib.Path("tests/fixtures/real_samples/viettelpost_gtgt_2.1.0.xml")


def base_state(**overrides) -> dict:
    state = {
        "invoice_id": "INV-0001",
        "source_path": str(REAL_XML),
        "pdf_path": None,
        "source_type": "xml",
        "strategy": "xml",
        "extracted": None,
        "extraction_attempts": 0,
        "current_errors": [],
        "error_history": [],
        "findings": [],
        "llm_calls": [],
        "status": "received",
    }
    state.update(overrides)
    return state


class TestRouteSource:
    def test_xml_source_picks_the_xml_strategy(self):
        assert enrichment.route_source(base_state())["strategy"] == "xml"

    def test_pdf_source_picks_vision(self):
        out = enrichment.route_source(base_state(source_type="pdf"))
        assert out["strategy"] == "vision"


class TestParseXml:
    def test_a_real_invoice_parses(self):
        out = enrichment.parse_xml_node(base_state())
        assert out["extracted"] is not None
        assert out["extracted"].total_amount > Decimal(0)
        assert out["current_errors"] == []

    def test_malformed_xml_becomes_an_extraction_error_not_a_crash(self, tmp_path):
        broken = tmp_path / "broken.xml"
        broken.write_text("<HDon><oops", encoding="utf-8")
        out = enrichment.parse_xml_node(base_state(source_path=str(broken)))
        assert out["extracted"] is None
        assert out["current_errors"], "the reason must survive for the router"
        assert len(out["error_history"]) == 1, "a DELTA, never the accumulated list"


class TestValidateNode:
    def test_a_clean_real_invoice_produces_no_findings(self):
        """8/8 real invoices validate clean (M2-B). If this goes red, the
        false-positive rate the whole project is judged on has moved."""
        parsed = enrichment.parse_xml_node(base_state())
        out = enrichment.validate_node(base_state(extracted=parsed["extracted"]))
        assert out["findings"] == []
        assert out["error_history"] == []

    def test_findings_become_prompt_ready_error_strings(self):
        from invoice_agent.extraction.models import InvoiceData, InvoiceLine
        from datetime import date

        broken = InvoiceData(
            template_no="1", invoice_series="C26TAA", invoice_number="00000123",
            issue_date=date(2026, 7, 1), total_amount=Decimal("999999"),
            lines=[InvoiceLine(line_no=1, item_kind=1, description="X",
                               amount=Decimal("100"))],
        )
        out = enrichment.validate_node(base_state(extracted=broken))
        assert out["findings"], "an invoice whose total ignores its lines must flag"
        assert out["current_errors"], "the router and the retry prompt need text"
        assert len(out["error_history"]) == len(out["findings"])

    def test_no_extraction_means_nothing_to_validate(self):
        assert enrichment.validate_node(base_state(extracted=None)) == {}


class TestManualQueue:
    def test_sets_status_and_writes_nothing_else(self):
        """Spec §5.2: state only. `invoices.status` belongs to M4's batch."""
        assert enrichment.manual_queue_node(base_state()) == {"status": "manual_queue"}


class TestVisionNode:
    def test_a_broken_pdf_records_no_cost_and_no_extraction(self, tmp_path, monkeypatch):
        from invoice_agent.llm import gateway as gw_module

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kw: pytest.fail("a broken PDF must never reach a model"),
        )
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"%PDF-1.7\nnot really")
        gw = LLMGateway(Settings(_env_file=None, gemini_api_keys=["g"], llm_mode="live"))

        out = enrichment.vision_extract_node(
            base_state(source_path=str(broken), source_type="pdf", strategy="vision"),
            gateway=gw,
        )
        assert out["extracted"] is None
        assert out["llm_calls"] == []
        assert out["extraction_attempts"] == 1, "a failed attempt still counts"
        assert len(out["error_history"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_enrichment_nodes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'invoice_agent.graphs'`

- [ ] **Step 3: Create the package marker**

Create `src/invoice_agent/graphs/__init__.py`:

```python
"""LangGraph graphs. Three of them by M5: enrichment, batch, approval."""
```

- [ ] **Step 4: Write the state and the nodes**

Create `src/invoice_agent/graphs/enrichment.py`:

```python
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
from invoice_agent.domain import validation
from invoice_agent.domain.findings import Finding
from invoice_agent.extraction import vision
from invoice_agent.extraction.models import InvoiceData
from invoice_agent.extraction.xml_parser import parse_invoice_xml
from invoice_agent.extraction.xml_schema import XmlExtractionError
from invoice_agent.llm import telemetry
from invoice_agent.llm.cost import LLMCallRecord
from invoice_agent.llm.gateway import LLMGateway
from invoice_agent.llm.structured import StructuredOutputError

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
    builder.add_node("manual_queue", manual_queue_node)

    builder.add_edge(START, "route_source")
    builder.add_conditional_edges(
        "route_source",
        lambda s: "vision_extract" if s["strategy"] == "vision" else "parse_xml",
        {"parse_xml": "parse_xml", "vision_extract": "vision_extract"},
    )
    builder.add_edge("parse_xml", "validate")
    builder.add_edge("vision_extract", "validate")
    # Task 3 replaces this straight edge with the conditional that is the whole
    # point of the milestone.
    builder.add_edge("validate", END)
    builder.add_edge("manual_queue", END)

    return builder.compile(checkpointer=checkpointer)
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_enrichment_nodes.py -v`
Expected: PASS (10 tests).

- [ ] **Step 6: Add the state-level serde guard**

Append to `tests/test_serde.py`:

```python
def test_a_populated_enrichment_state_round_trips_with_every_type_intact():
    """The guard that actually matters: not "the list has four entries" but
    "every value in a real state survives". Add a state field of an
    unregistered type and this fails here, not in a checkpoint."""
    from datetime import date

    from invoice_agent.graphs.enrichment import EnrichmentState

    objects = sample_objects()
    state: EnrichmentState = {
        "invoice_id": "INV-0001",
        "source_path": "corpus/INV-0001.xml",
        "pdf_path": None,
        "source_type": "xml",
        "strategy": "xml",
        "extracted": objects["InvoiceData"],
        "extraction_attempts": 1,
        "current_errors": ["ARITHMETIC_MISMATCH: total_amount=1"],
        "error_history": ["ARITHMETIC_MISMATCH: total_amount=1"],
        "findings": [objects["Finding"]],
        "llm_calls": [objects["LLMCallRecord"]],
        "status": "received",
    }
    serde = build_serde()
    back = serde.loads_typed(serde.dumps_typed(state))
    assert type(back["extracted"]) is type(objects["InvoiceData"])
    assert type(back["extracted"].lines[0]) is type(objects["InvoiceData"].lines[0])
    assert type(back["findings"][0]) is type(objects["Finding"])
    assert type(back["llm_calls"][0]) is type(objects["LLMCallRecord"])
    assert back == state
```

Run: `.venv/bin/python -m pytest tests/test_serde.py -v` → PASS (7 tests).

- [ ] **Step 7: Commit**

```bash
git add src/invoice_agent/graphs tests/test_enrichment_nodes.py tests/test_serde.py
git commit -m "feat(graphs): enrichment state and five nodes, one of which calls an LLM"
```

---

### Task 3: `route_after_validate` — the milestone's whole point

Design §3.3 asserts the extraction-vs-data distinction without saying how to compute it. This is the computation, and it turns on one property: **the XML parser is deterministic and vision is not.**

**Files:**
- Modify: `src/invoice_agent/graphs/enrichment.py`
- Test: `tests/test_enrichment_routing.py`

**Interfaces:**
- Consumes: `EnrichmentState`, `MAX_VISION_ATTEMPTS` (Task 2).
- Produces: `route_after_validate(state) -> str` returning one of `"vision_extract"`, `"manual_queue"`, or `END`. Task 4 drives it through the compiled graph.

- [ ] **Step 1: Write the failing test**

Create `tests/test_enrichment_routing.py`:

```python
"""route_after_validate — extraction error vs data error.

Design §3.3: "With XML this is theatre. The XML parser is a pure function: same
input, same output, byte-identical. Retrying three times gives exactly three
identical results." The distinction IS the self-correction; the loop is not.
"""

from langgraph.graph import END

from invoice_agent.domain import findings
from invoice_agent.graphs.enrichment import MAX_VISION_ATTEMPTS, route_after_validate

A_FINDING = [findings.make(findings.ARITHMETIC_MISMATCH)]


def state(**overrides) -> dict:
    base = {
        "invoice_id": "INV-0001",
        "source_path": "corpus/INV-0001.xml",
        "pdf_path": None,
        "source_type": "xml",
        "strategy": "xml",
        "extracted": object(),
        "extraction_attempts": 0,
        "current_errors": [],
        "error_history": [],
        "findings": [],
        "llm_calls": [],
        "status": "received",
    }
    base.update(overrides)
    return base


class TestExtractionFailed:
    def test_xml_extraction_failure_climbs_to_vision_when_a_pdf_exists(self):
        assert route_after_validate(
            state(extracted=None, pdf_path="corpus/INV-0001.pdf")
        ) == "vision_extract"

    def test_xml_extraction_failure_without_a_pdf_gives_up(self):
        assert route_after_validate(state(extracted=None)) == "manual_queue"

    def test_vision_extraction_failure_retries_while_attempts_remain(self):
        assert route_after_validate(
            state(extracted=None, strategy="vision", source_type="pdf",
                  extraction_attempts=1)
        ) == "vision_extract"

    def test_vision_gives_up_after_three_attempts(self):
        assert route_after_validate(
            state(extracted=None, strategy="vision", source_type="pdf",
                  extraction_attempts=MAX_VISION_ATTEMPTS)
        ) == "manual_queue"


class TestExtractionSucceeded:
    def test_a_clean_invoice_ends(self):
        assert route_after_validate(state()) == END

    def test_xml_findings_never_retry(self):
        """The heart of it. A finding on an XML invoice is a fact ABOUT THE
        INVOICE — a DATA error. Retrying a pure function three times produces
        three identical results; the flag travels to the human instead."""
        assert route_after_validate(state(findings=A_FINDING)) == END

    def test_vision_findings_do_retry(self):
        """The extractor is LOSSY, so the same ARITHMETIC_MISMATCH may be
        misread digits — and design §3.3 says this is the retry that works."""
        assert route_after_validate(
            state(strategy="vision", source_type="pdf", findings=A_FINDING,
                  extraction_attempts=1)
        ) == "vision_extract"

    def test_vision_findings_stop_retrying_at_the_bound(self):
        assert route_after_validate(
            state(strategy="vision", source_type="pdf", findings=A_FINDING,
                  extraction_attempts=MAX_VISION_ATTEMPTS)
        ) == "manual_queue"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_enrichment_routing.py -v`
Expected: FAIL with `ImportError: cannot import name 'route_after_validate'`

- [ ] **Step 3: Write the router**

Add to `src/invoice_agent/graphs/enrichment.py`, after `validate_node`:

```python
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
```

- [ ] **Step 4: Wire it into the graph**

In `build_enrichment_graph`, replace `builder.add_edge("validate", END)` with:

```python
    builder.add_conditional_edges(
        "validate",
        route_after_validate,
        {"vision_extract": "vision_extract", "manual_queue": "manual_queue", END: END},
    )
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_enrichment_routing.py tests/test_enrichment_nodes.py -v`
Expected: PASS (18 tests).

- [ ] **Step 6: Commit**

```bash
git add src/invoice_agent/graphs/enrichment.py tests/test_enrichment_routing.py
git commit -m "feat(graphs): route_after_validate — extraction error vs data error"
```

---

### Task 4: The compiled graph, and the reducer trap

**Files:**
- Test: `tests/test_enrichment_graph.py`

**Interfaces:**
- Consumes: `build_enrichment_graph` (Task 2), `route_after_validate` (Task 3).
- Produces: no new source. This task is the M3 gate's second condition.

- [ ] **Step 1: Write the failing test**

Create `tests/test_enrichment_graph.py`:

```python
"""The compiled graph. Includes the M3 gate's reducer-trap condition."""

import json
import pathlib
from datetime import date
from decimal import Decimal

import pytest

from invoice_agent.config import Settings
from invoice_agent.graphs.enrichment import MAX_VISION_ATTEMPTS, build_enrichment_graph
from invoice_agent.llm.gateway import LLMGateway
from scripts.generator import pdf as pdf_gen
from tests.helpers.fake_llm import ok_response
from tests.helpers.invoice_specs import make_spec

REAL_XML = pathlib.Path("tests/fixtures/real_samples/viettelpost_gtgt_2.1.0.xml")

# Parses into InvoiceData, but the total ignores the line — so `validate` finds
# an ARITHMETIC_MISMATCH on every attempt and the loop can never converge.
ALWAYS_INVALID = json.dumps(
    {
        "template_no": "1", "invoice_series": "C26TAA", "invoice_number": "00000123",
        "issue_date": "2026-07-01", "total_amount": "999999",
        "lines": [{"line_no": 1, "item_kind": 1, "description": "Dịch vụ",
                   "amount": "100"}],
    }
)


def stub_gateway(monkeypatch, reply: str) -> LLMGateway:
    from invoice_agent.llm import gateway as gw_module

    monkeypatch.setattr(
        gw_module.litellm, "completion",
        lambda **kw: ok_response(reply, prompt=900, completion=120),
    )
    return LLMGateway(
        Settings(
            _env_file=None,
            gemini_api_keys=["gem1"],
            vision_model_chain=["gemini/gemini-3.5-flash"],
            llm_mode="live",
        )
    )


def initial(**overrides) -> dict:
    state = {
        "invoice_id": "INV-TEST",
        "source_path": str(REAL_XML),
        "pdf_path": None,
        "source_type": "xml",
        "strategy": "xml",
        "extracted": None,
        "extraction_attempts": 0,
        "current_errors": [],
        "error_history": [],
        "findings": [],
        "llm_calls": [],
        "status": "received",
    }
    state.update(overrides)
    return state


def test_a_real_xml_invoice_runs_clean_end_to_end():
    final = build_enrichment_graph().invoke(initial())
    assert final["extracted"] is not None
    assert final["findings"] == []
    assert final["status"] != "manual_queue"


def test_error_history_grows_LINEARLY_not_quadratically(tmp_path, monkeypatch):
    """THE M3 GATE CONDITION.

    Three vision attempts, each producing exactly one finding, must leave
    THREE entries. A node that returns the accumulated list instead of a delta
    gives 1, 3, 7 — which at three iterations reads as an off-by-one and only
    at scale reads as quadratic. That is why it is a trap and not a bug.
    """
    gw = stub_gateway(monkeypatch, ALWAYS_INVALID)
    pdf_path = tmp_path / "invoice.pdf"
    pdf_path.write_bytes(pdf_gen.render_pdf(make_spec()))

    final = build_enrichment_graph(gateway=gw).invoke(
        initial(source_path=str(pdf_path), source_type="pdf", strategy="vision")
    )

    assert final["extraction_attempts"] == MAX_VISION_ATTEMPTS
    assert len(final["error_history"]) == MAX_VISION_ATTEMPTS
    assert final["status"] == "manual_queue"


def test_a_truncated_pdf_reaches_manual_queue_without_spending_a_token(
    tmp_path, monkeypatch
):
    from invoice_agent.llm import gateway as gw_module

    monkeypatch.setattr(
        gw_module.litellm, "completion",
        lambda **kw: pytest.fail("a PDF that cannot be opened must not reach a model"),
    )
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(pdf_gen.render_truncated(make_spec()))
    gw = LLMGateway(Settings(_env_file=None, gemini_api_keys=["g"], llm_mode="live"))

    final = build_enrichment_graph(gateway=gw).invoke(
        initial(source_path=str(broken), source_type="pdf", strategy="vision")
    )
    assert final["status"] == "manual_queue"
    assert final["llm_calls"] == []


def test_an_unparseable_xml_with_a_pdf_twin_climbs_to_vision(tmp_path, monkeypatch):
    """The gate's escalation clause, on the compiled graph."""
    good = json.dumps(
        {
            "template_no": "1", "invoice_series": "C26TAA", "invoice_number": "00000123",
            "issue_date": "2026-07-01", "total_amount": "1194544",
            "lines": [{"line_no": 1, "item_kind": 1, "description": "Dịch vụ",
                       "amount": "1194544"}],
        }
    )
    gw = stub_gateway(monkeypatch, good)
    broken_xml = tmp_path / "broken.xml"
    broken_xml.write_text("<HDon><oops", encoding="utf-8")
    twin = tmp_path / "twin.pdf"
    twin.write_bytes(pdf_gen.render_pdf(make_spec()))

    final = build_enrichment_graph(gateway=gw).invoke(
        initial(source_path=str(broken_xml), pdf_path=str(twin))
    )
    assert final["strategy"] == "vision"
    assert final["extracted"] is not None
    assert final["extracted"].total_amount == Decimal("1194544")
```

The diagram drift test belongs to Task 5, which creates the file it compares against — adding it here would mean committing a red test.

- [ ] **Step 2: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_enrichment_graph.py -v`
Expected: PASS (4 tests).

If `test_error_history_grows_LINEARLY_not_quadratically` fails with a `GraphRecursionError`, the `MAX_VISION_ATTEMPTS` bound is not cutting the loop — fix the router, not the recursion limit. If it reports 1, 3 or 7 entries, a node is returning the accumulated list instead of a delta.

- [ ] **Step 3: Commit**

```bash
git add tests/test_enrichment_graph.py
git commit -m "test(graphs): the reducer trap, escalation, and a cheap give-up"
```

---

### Task 5: The generated diagram

**Files:**
- Create: `scripts/render_graph.py`, `docs/diagrams/enrichment_graph.mmd`

**Interfaces:**
- Consumes: `build_enrichment_graph` (Task 2).
- Produces: the committed `.mmd` that Task 4's drift test compares against.

- [ ] **Step 1: Write the generator**

Create `scripts/render_graph.py`:

```python
"""Regenerate the committed enrichment-graph diagram.

Mermaid TEXT, not a PNG. Measured 2026-07-29 (spec M3-C §2.3): `draw_mermaid()`
is the only renderer that works offline — `draw_mermaid_png()` needs a network
round-trip to mermaid.ink, `draw_png()` needs graphviz, and `draw_ascii()`
raises ImportError without grandalf. Text also makes the artefact a DRIFT
GUARD: `tests/test_enrichment_graph.py` regenerates and compares, so the
diagram cannot quietly stop describing the graph. GitHub renders it natively.
"""

import pathlib

from invoice_agent.graphs.enrichment import build_enrichment_graph

OUT = pathlib.Path("docs/diagrams/enrichment_graph.mmd")


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        build_enrichment_graph().get_graph().draw_mermaid(), encoding="utf-8"
    )
    print(f"✅ wrote {OUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Generate and inspect it**

```bash
.venv/bin/python -m scripts.render_graph
cat docs/diagrams/enrichment_graph.mmd
```

Read the output. It must show `validate` branching to `vision_extract`, `manual_queue` and `__end__`; if the conditional edge is missing, Task 3's wiring did not take.

- [ ] **Step 3: Add the drift test**

Append to `tests/test_enrichment_graph.py`:

```python
def test_the_committed_diagram_matches_the_graph():
    """The artefact is a drift guard, not decoration — which is the one thing a
    committed PNG could never be. Regenerate with:
        .venv/bin/python -m scripts.render_graph
    """
    committed = pathlib.Path("docs/diagrams/enrichment_graph.mmd")
    assert committed.exists(), "run `.venv/bin/python -m scripts.render_graph`"
    current = build_enrichment_graph().get_graph().draw_mermaid()
    assert committed.read_text(encoding="utf-8").strip() == current.strip()
```

- [ ] **Step 4: Run the drift test**

Run: `.venv/bin/python -m pytest tests/test_enrichment_graph.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/render_graph.py docs/diagrams/enrichment_graph.mmd tests/test_enrichment_graph.py
git commit -m "feat(graphs): generated mermaid diagram as a drift guard"
```

---

### Task 6: `scripts/run_one.py` — the M3 gate sentence

**Files:**
- Create: `scripts/run_one.py`
- Test: `tests/test_run_one.py`

**Interfaces:**
- Consumes: `build_enrichment_graph` (Task 2), `manifest.read_manifest`, `invoices.insert_with_lines`, `models.llm_calls`.
- Produces: `run_one(invoice_id: str, *, corpus: pathlib.Path, gateway=None) -> dict[str, Any]` returning the final state plus `db_invoice_id`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_run_one.py`:

```python
"""One invoice, end to end, with its cost linked to it — the M3 gate."""

import json
import pathlib

import pytest
from sqlalchemy import select

from invoice_agent.config import Settings
from invoice_agent.db import models
from invoice_agent.llm.gateway import LLMGateway
from scripts.run_one import run_one
from tests.helpers.fake_llm import ok_response

GOOD = json.dumps(
    {
        "template_no": "1", "invoice_series": "C26TAA", "invoice_number": "00099123",
        "issue_date": "2026-07-01", "total_amount": "1194544",
        "seller_tax_code": "0100000003",
        "lines": [{"line_no": 1, "item_kind": 1, "description": "Dịch vụ",
                   "amount": "1194544"}],
    }
)


@pytest.fixture
def pdf_corpus(tmp_path):
    """A one-entry corpus holding a single PDF-only invoice."""
    from scripts.generator import pdf as pdf_gen
    from tests.helpers.invoice_specs import make_spec

    (tmp_path / "INV-9001.pdf").write_bytes(pdf_gen.render_pdf(make_spec()))
    (tmp_path / "manifest.jsonl").write_text(
        json.dumps(
            {
                "invoice_id": "INV-9001",
                "path": "INV-9001.pdf",
                "pdf_path": None,
                "source_type": "pdf_vision",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def stub_gateway(monkeypatch) -> LLMGateway:
    from invoice_agent.llm import gateway as gw_module

    monkeypatch.setattr(
        gw_module.litellm, "completion",
        lambda **kw: ok_response(GOOD, prompt=900, completion=120),
    )
    return LLMGateway(
        Settings(
            _env_file=None, gemini_api_keys=["gem1"],
            vision_model_chain=["gemini/gemini-3.5-flash"], llm_mode="live",
        )
    )


def test_run_one_links_cost_to_the_invoice(runtime_conn, pdf_corpus, monkeypatch):
    """THE M3 GATE SENTENCE: one invoice end to end, cost recorded in
    llm_calls — and reachable FROM the invoice, which is what the column was
    added for."""
    result = run_one(
        "INV-9001", corpus=pdf_corpus, gateway=stub_gateway(monkeypatch)
    )
    invoice_id = result["db_invoice_id"]
    assert invoice_id is not None

    rows = runtime_conn.execute(
        select(models.llm_calls).where(models.llm_calls.c.invoice_id == invoice_id)
    ).mappings().all()
    assert rows, "cost was recorded but never linked to the invoice"
    assert rows[0]["node"] == "vision_extract"
    assert rows[0]["thread_id"] == "enrich:INV-9001"
    assert rows[0]["shadow_cost_usd"] > 0


def test_cost_row_exists_before_the_graph_finishes(runtime_conn, pdf_corpus, monkeypatch):
    """Spec §5.3: rows are written the moment the tokens are spent, so a run
    that dies afterwards cannot lose them. A node placed after vision raises;
    the row must still be there.
    """
    from invoice_agent.graphs import enrichment

    original = enrichment.validate_node

    def exploding_validate(state):
        raise RuntimeError("simulated crash after the tokens were spent")

    monkeypatch.setattr(enrichment, "validate_node", exploding_validate)
    with pytest.raises(RuntimeError):
        run_one("INV-9001", corpus=pdf_corpus, gateway=stub_gateway(monkeypatch))
    monkeypatch.setattr(enrichment, "validate_node", original)

    rows = runtime_conn.execute(
        select(models.llm_calls).where(
            models.llm_calls.c.thread_id == "enrich:INV-9001"
        )
    ).mappings().all()
    assert rows, "the call was billed and the record was lost"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_run_one.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.run_one'`

- [ ] **Step 3: Write the runner**

Create `scripts/run_one.py`:

```python
"""Run one invoice through `enrichment_graph` — the M3 gate demo.

Deliberately thin: M4's `batch_graph` replaces it. Its one non-obvious job is
the cost backfill, and the reason is in spec §5.3 — a PDF-only invoice has no
`invoices` row until vision has already run, so `vision_extract` writes its
`llm_calls` rows with `invoice_id` NULL and `thread_id` set, and this script
links them once the row exists.
"""

import argparse
import pathlib
from typing import Any

from sqlalchemy import update

from invoice_agent.db import models
from invoice_agent.db.engine import get_engine
from invoice_agent.db.repositories import invoices as invoice_repo
from invoice_agent.graphs.enrichment import build_enrichment_graph
from invoice_agent.llm.gateway import LLMGateway
from scripts.generator import manifest


def run_one(
    invoice_id: str,
    *,
    corpus: pathlib.Path = pathlib.Path("corpus"),
    gateway: LLMGateway | None = None,
) -> dict[str, Any]:
    entries = {
        row["invoice_id"]: row
        for row in manifest.read_manifest(corpus / "manifest.jsonl")
    }
    if invoice_id not in entries:
        raise SystemExit(f"{invoice_id} is not in {corpus / 'manifest.jsonl'}")
    entry = entries[invoice_id]

    source = corpus / pathlib.Path(entry["path"]).name
    twin = entry.get("pdf_path")
    state = {
        "invoice_id": invoice_id,
        "source_path": str(source),
        "pdf_path": str(corpus / pathlib.Path(twin).name) if twin else None,
        "source_type": "xml" if entry["source_type"] == "xml" else "pdf",
        "strategy": "xml",
        "extracted": None,
        "extraction_attempts": 0,
        "current_errors": [],
        "error_history": [],
        "findings": [],
        "llm_calls": [],
        "status": "received",
    }

    final = build_enrichment_graph(gateway=gateway).invoke(state)

    db_invoice_id = None
    if final.get("extracted") is not None:
        with get_engine().begin() as conn:
            db_invoice_id = invoice_repo.insert_with_lines(
                conn,
                final["extracted"],
                source_type="pdf_vision" if final["strategy"] == "vision" else "xml",
                source_path=str(source),
            )
            if db_invoice_id is not None:
                # The backfill. `thread_id` is the correlation key design §1.3
                # specifies; migration 10 added the column and migration 09
                # already granted UPDATE on this table.
                conn.execute(
                    update(models.llm_calls)
                    .where(models.llm_calls.c.thread_id == f"enrich:{invoice_id}")
                    .where(models.llm_calls.c.invoice_id.is_(None))
                    .values(invoice_id=db_invoice_id)
                )

    return {**final, "db_invoice_id": db_invoice_id}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoice", required=True)
    parser.add_argument("--corpus", type=pathlib.Path, default=pathlib.Path("corpus"))
    args = parser.parse_args()

    result = run_one(args.invoice, corpus=args.corpus)
    print(f"strategy   : {result['strategy']}")
    print(f"attempts   : {result['extraction_attempts']}")
    print(f"status     : {result['status']}")
    print(f"findings   : {[f.code for f in result['findings']]}")
    print(f"llm calls  : {len(result['llm_calls'])}")
    print(f"invoice row: {result['db_invoice_id']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_run_one.py -v`
Expected: PASS (2 tests). Needs `make dev`.

- [ ] **Step 5: Run the gate demo for real**

```bash
make generate
.venv/bin/python -c "
import json
rows=[json.loads(l) for l in open('corpus/manifest.jsonl')]
print([r['invoice_id'] for r in rows if r['source_type']=='pdf_vision'][:3])
"
```

Take the first id and run `.venv/bin/python -m scripts.run_one --invoice <id>`. With no API key it will fail at the vision call — that is expected and is exactly what §8.1 records as owed. The test above is the gate evidence.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_one.py tests/test_run_one.py
git commit -m "feat(scripts): run one invoice end to end and link its cost"
```

---

### Task 7: Mutation verification, the note, and the roadmap

M3-A and M3-B both closed with a defect in the closeout rather than the code. Read that history before writing anything here.

**Files:**
- Create: `docs/notes/2026-07-29-m3c-enrichment-graph.md`
- Modify: `docs/Plan_Project_F_2026-07-25.md`

- [ ] **Step 1: Run each mutation, one at a time**

Apply, run the named test, record the result, then `git checkout --` the file. **Aim the mutation at every call site**, not just the first — two M3-B review mutations came back green because they were patched in one place and overridden in another.

| # | Mutation | Command |
|---|---|---|
| 1 | `validate_node` returns `state["error_history"] + messages` instead of `messages` | `pytest tests/test_enrichment_graph.py::test_error_history_grows_LINEARLY_not_quadratically` |
| 2 | `route_after_validate` returns `"vision_extract"` for XML findings | `pytest tests/test_enrichment_routing.py::TestExtractionSucceeded::test_xml_findings_never_retry` |
| 3 | `route_after_validate` sends a failed XML extraction straight to `manual_queue` | `pytest tests/test_enrichment_routing.py::TestExtractionFailed::test_xml_extraction_failure_climbs_to_vision_when_a_pdf_exists` |
| 4 | Drop the `extraction_attempts < MAX_VISION_ATTEMPTS` bound | `pytest tests/test_enrichment_graph.py::test_error_history_grows_LINEARLY_not_quadratically` |
| 5 | `manual_queue_node` also writes `invoices.status` | `pytest tests/test_enrichment_nodes.py::TestManualQueue::test_sets_status_and_writes_nothing_else` |
| 6 | `build_checkpointer` stops assigning `build_serde()` | `pytest tests/test_serde.py::test_checkpointer_carries_the_allowlist` |
| 7 | ~~Remove `InvoiceLine` from the allowlist~~ → **remove `("invoice_agent.domain.findings", "Finding")` instead**. Corrected 2026-07-29: removing `InvoiceLine` has **no behavioural effect** (pydantic rebuilds nested models from the parent payload), so that mutation is un-catchable and any test that appears to catch it is asserting the list contains itself. `Finding` is a top-level state value and its removal really does degrade the round-trip | `pytest tests/test_serde.py::test_a_populated_enrichment_state_round_trips_with_every_type_intact` |
| 8 | `_persist_cost` becomes a no-op and `run_one` records from state instead | `pytest tests/test_run_one.py::test_cost_row_exists_before_the_graph_finishes` |
| 9 | `run_one` skips the backfill `UPDATE` | `pytest tests/test_run_one.py::test_run_one_links_cost_to_the_invoice` |
| 10 | Edit one character in `docs/diagrams/enrichment_graph.mmd` | `pytest tests/test_enrichment_graph.py::test_the_committed_diagram_matches_the_graph` |
| 11 | `vision_extract_node` calls the model before `rasterize` can fail | `pytest tests/test_enrichment_graph.py::test_a_truncated_pdf_reaches_manual_queue_without_spending_a_token` |
| 12 | `findings` given `Annotated[list[Finding], operator.add]` | `pytest tests/test_enrichment_graph.py::test_an_unparseable_xml_with_a_pdf_twin_climbs_to_vision` |

Mutation 12 is the spec bug this plan corrected — it must be seen red, because the spec as written would have shipped it.

- [ ] **Step 2: Confirm the tree is clean**

Run: `git status --porcelain src/ scripts/ docs/diagrams/`
Expected: empty.

- [ ] **Step 3: Verify both commands**

```bash
make check
env -u GROQ_API_KEY -u GROQ_API_KEYS -u GEMINI_API_KEY -u GEMINI_API_KEYS \
    -u OPENROUTER_API_KEY -u OPENROUTER_API_KEYS -u OPENAI_API_KEY \
    LLM_MODE=replay .venv/bin/python -m pytest tests/ -p no:cacheprovider
```
Expected: both green. **Do not commit until `make check` is green** — M2-C and M3-B both closed red on ruff with the fixes sitting uncommitted.

- [ ] **Step 4: Write the note**

Create `docs/notes/2026-07-29-m3c-enrichment-graph.md` following `docs/notes/2026-07-29-m3b-vision-surface.md` **as corrected**:

- What was built.
- Spec §2's four measurements with their numbers.
- The mutation table with measured results.
- **Spec §7's nine success criteria in the spec's own words and order.** Do not rename, reorder or drop any. Both previous milestones failed here.
- **Spec §8's five open questions still phrased as questions.** Do not answer them into descriptions.
- The two spec corrections this plan made (`findings` must replace; the state needs `pdf_path`), because the spec would otherwise have shipped a loop that never converges.

- [ ] **Step 5: Update the roadmap**

In `docs/Plan_Project_F_2026-07-25.md`: mark item 3.5 done; extend the §7 M3 row with M3-C; **close the M3 gate** with its four conditions and the evidence for each; note what M3 still owes — the vision fixture and first real accuracy number (spec §8.1), the git history purge, ADR `0001-three-graphs-not-one.md`. New prose in English.

- [ ] **Step 6: Commit**

```bash
git add docs/notes/2026-07-29-m3c-enrichment-graph.md docs/Plan_Project_F_2026-07-25.md
git commit -m "docs(m3c): close the enrichment graph and Milestone 3"
```

---

## Spec Coverage

| Spec section | Task |
|---|---|
| §2.1/§5.4 — msgpack allowlist, strict-mode guard | Task 1 |
| §2.2 — serde assigned post-`from_conn_string` | Task 1 |
| §4 — state (with the two corrections above) | Task 2 |
| §5.1 — the five nodes | Task 2 |
| §5.2 — `route_after_validate` | Task 3 |
| §5.3 — inline cost write + backfill | Tasks 2, 6 |
| §5.5 — `scripts/run_one.py` | Task 6 |
| §5.6 — generated diagram + drift guard | Tasks 4, 5 |
| §6.1 — the reducer-trap anchor | Task 4 |
| §6.2 — ten mutation guards | Task 7 (**twelve** — the truncated-PDF and `findings`-reducer cases were added while writing the plan) |
| §7 — success criteria 1–9 | Task 7, Steps 2–3 |
| §8 — open questions carried into the note | Task 7, Step 4 |
