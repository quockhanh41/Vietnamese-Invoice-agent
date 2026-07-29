# Spec — M3-C: the enrichment graph (plan item 3.5)

> **Date**: 2026-07-29 · **Milestone**: M3, third of three specs
> **Covers**: plan item **3.5** — `graphs/enrichment.py`, `route_after_validate`, the reducer trap,
> `scripts/run_one.py`, and the checkpoint-serialization registration the graph's state requires.
> **Does not cover**: 3.6/3.7 (M3-A) and 3.2/3.3/3.4 (M3-B), both done 2026-07-29. Item **3.1** moved
> to M7 (M3-A §3.3). `classify` / `audit` / `signals` are M4.
> **Predecessor**: `docs/superpowers/specs/2026-07-29-m3b-vision-surface-design.md`

---

## 1. Why this, and what it owes

### 1.1 The gate

M3's gate has four conditions. This spec owns the two that are still open and closes the milestone:

| M3 gate condition | This spec |
|---|---|
| One invoice end-to-end with cost recorded in `llm_calls` | **owns it** — `scripts/run_one.py` |
| 3-iteration loop asserts `len(error_history) == 3` | **owns it** — the reducer trap |
| XML extraction error escalates to vision; broken vision falls to manual queue | **owns the routing**; M3-B supplied the extractor and the PDFs |
| Suite runs under `LLM_MODE=replay` without an API key | must keep it true — graph tests stub the gateway |

### 1.2 Why it is last

It consumes everything: `parse_invoice_xml` (M1), `domain.validate` (M2-B), the corpus and its PDF
twins (M3-B), `vision.extract` (M3-B), and `telemetry.record` (M3-A). Built earlier it would have
been mocks all the way down.

### 1.3 What it is really for

Design §3.3 is blunt that the loop is not the point:

> *"With XML this is theatre. The XML parser is a pure function: same input, same output,
> byte-identical. Retrying three times gives exactly three identical results."*

**The distinction between an extraction error and a data error is the content of self-correction.**
That distinction is one function, `route_after_validate`, and it is what this milestone exists to
get right.

---

## 2. Measurements taken 2026-07-29 for this spec

### 2.1 Strict msgpack turns a `Finding` into a `dict` — silently

Every state type survives LangGraph's checkpoint serializer *today*, and each emits a deprecation
warning naming itself:

```
Deserializing unregistered type invoice_agent.domain.findings.Finding from checkpoint.
This will be blocked in a future version.
```

What "blocked" means is the finding:

| Serializer configuration | `Finding` round-trips as | Equal? |
|---|---|:---:|
| default (permissive) | `Finding` | ✅ (with warning) |
| **strict** (`allowed_msgpack_modules=None`) | **`dict`** | ❌ |
| explicit allowlist | `Finding` | ✅ (no warning) |

Strict mode **does not raise**. It returns a plain `dict`, so the first symptom is an
`AttributeError` on `finding.code` somewhere far from the cause — or worse, code that happens to
subscript survives and the flag vocabulary silently degrades. The `langgraph==1.2.9` pin contains
this today, but the pin's entire justification is HITL durability, and this is the mechanism by
which a future bump breaks it *quietly*. Registering the allowlist now converts a future silent
corruption into a present, tested guarantee.

### 2.2 `from_conn_string` cannot take a `serde`; post-assignment can

```python
PostgresSaver.__init__(self, conn, pipe=None, serde=None)          # accepts serde
PostgresSaver.from_conn_string(conn_string, *, pipeline=False)     # does NOT
```

`build_checkpointer` (M1.5, `src/invoice_agent/checkpointer.py`) uses `from_conn_string`, whose body
is:

```python
with Connection.connect(conn_string, autocommit=True, prepare_threshold=0, row_factory=dict_row) as conn:
    yield cls(conn)
```

Constructing `PostgresSaver` directly would mean copying those three keyword arguments into this
repo, where they would rot silently against a future LangGraph patch — the same class of bug as the
M0.2 DSN-options defect, where a config detail diverged and only one of two libraries noticed.

Measured instead: **assigning `.serde` on the yielded saver is honoured** — the object round-trips
as `Finding` with zero warnings. One line, and LangGraph keeps owning its connection arguments.

### 2.3 Only mermaid text renders offline

| Method | Requirement |
|---|---|
| `draw_mermaid()` | none — **works offline** |
| `draw_mermaid_png()` | network (mermaid.ink) or a headless browser |
| `draw_png()` | graphviz / pygraphviz |
| `draw_ascii()` | `grandalf` — raises `ImportError` today |

The plan's artefact ("ảnh render `enrichment_graph`") therefore ships as **generated mermaid source**,
committed and rendered by GitHub natively. That also makes it a drift guard rather than decoration
(§6.2), which a PNG could never be.

### 2.4 No vision-capable API key is configured

`gemini_api_keys: 0`, `openai_api_key: False`. So the recorded vision fixture — the one that would
give this project its first real vision measurement (M3-B §8.1 records that it has none) — cannot be
produced in the current environment. It is scoped as a **separate, optional step** (§5.5), and the
milestone closes without it.

---

## 3. Scope

### 3.1 In

| # | Deliverable |
|---|---|
| 1 | `graphs/enrichment.py` — state, five nodes, `route_after_validate` |
| 2 | `serde.py` + `build_checkpointer` registration, and a strict-mode round-trip guard |
| 3 | `scripts/run_one.py` — the end-to-end runner |
| 4 | `docs/diagrams/enrichment_graph.mmd`, generated, with a drift test |
| 5 | *(optional, needs a key — §2.4)* one recorded vision fixture + the first real accuracy note. It has no §5 subsection because it is not code: it is a `LLM_MODE=record` run against a generated PDF, plus the resulting numbers written into the closeout note. Blocked today; carried as §8.1 |

### 3.2 Out

`classify`, `audit`, `signals`, `vendor_profile` (M4). `batch_graph` and `Send` (M4.6). Writing
`invoices.status` (M4's `ingest` / `route_outcomes` own it — §5.2). Streaming replay (still refused,
M3-A §8.1).

---

## 4. State

Design §3.1's shape, restricted to what M3's nodes use:

```python
class EnrichmentState(TypedDict):
    invoice_id: str
    source_path: str
    source_type: Literal["xml", "pdf"]
    strategy: Literal["xml", "vision", "manual"]        # replace — drives the loop
    extracted: InvoiceData | None
    extraction_attempts: int
    current_errors: list[str]                            # replace — feeds the retry prompt
    error_history: Annotated[list[str], operator.add]    # SHORT-TERM MEMORY
    findings: Annotated[list[Finding], operator.add]
    llm_calls: Annotated[list[LLMCallRecord], operator.add]
    status: str
```

**A correction to design §3.1.** It types `current_errors` as `list[ValidationError]`. No such type
exists: `domain.validate()` returns `list[Finding]`, and `vision.extract(current_errors=…)` takes
`list[str]`. Since the field's only purpose is to enter a prompt, `list[str]` is the honest type,
rendered from `Finding` at the boundary. `findings` keeps the structured objects for M4 to flag from.

**The reducer contract, restated because it is the gate.** `error_history`, `findings` and
`llm_calls` all use `operator.add`. A node returns a **delta** — never the accumulated list. Return
the whole list and each pass doubles the previous one: quadratic growth that looks like a slow leak
rather than a bug.

---

## 5. Components

### 5.1 The five nodes

| Node | LLM | Behaviour |
|---|:---:|---|
| `route_source` | ✗ | `source_type == "xml"` → `parse_xml`; else `vision_extract`. Sets `strategy`. |
| `parse_xml` | ✗ | `parse_invoice_xml`. `XmlExtractionError` → `extracted=None`, error into the delta. |
| `vision_extract` | ✓ | `vision.extract(..., current_errors=…)`. Increments `extraction_attempts`, appends `llm_calls`. `VisionExtractionError` / `StructuredOutputError` → `extracted=None`. |
| `validate` | ✗ | `domain.validate(extracted)` → `findings` delta. Skipped by routing when `extracted is None`. |
| `manual_queue` | ✗ | Sets `status="manual_queue"` and ends. **State only** — §5.2. |

Five nodes, **one** of which calls an LLM. Design §3.2's ratio is a design claim, not thrift, and it
should survive into the README.

### 5.2 `route_after_validate` — the whole point

```
extracted is None                     → EXTRACTION failed
    strategy == "xml" and a PDF exists    → vision_extract
    otherwise                             → manual_queue

no findings                           → END

strategy == "xml" and findings        → END, carrying the findings
    The parser is a PURE FUNCTION. Three retries yield three identical
    results. A finding here is a fact ABOUT THE INVOICE — a DATA error —
    and the human approver decides, not the loop.

strategy == "vision" and findings
    and extraction_attempts < 3       → vision_extract, with current_errors in the prompt
    The extractor is LOSSY. The same ARITHMETIC_MISMATCH may be misread
    digits, and design §3.3 says this retry is the one that actually works.

otherwise                             → manual_queue
```

The split is **per-strategy, not per-finding-code**. Design §3.3 asserts the extraction/data
distinction without saying how to compute it; this is the computation, and it falls out of one
property — the XML parser is deterministic and vision is not.

`manual_queue` sets `status` in state and stops. It does **not** write `invoices.status`: design §4
gives batch's `ingest` / `route_outcomes` that job, and M3 agreed the graph's only DB surface is
`llm_calls`.

### 5.3 Where `llm_calls` are written

Design §3.1 accumulates them in state; M3-A's spec has nodes writing rows immediately because *"a
crash mid-run loses the cost record of calls already billed"*. Those collide here, because **a
PDF-only invoice has no `invoices` row until vision has already run** — so the node that spends the
token cannot name the invoice.

Decided 2026-07-29: **`vision_extract` writes its rows immediately with `thread_id` set and
`invoice_id` NULL; the runner backfills `invoice_id` by `thread_id` once the row exists.**

- Cost is recorded the moment it is incurred, so a crash cannot lose it.
- `thread_id` is exactly the correlation key design §1.3 specifies (`enrich:{invoice_id}`).
- Migration 09 already grants `UPDATE` on `llm_calls` to `invoice_app`; no schema change.
- The state field stays too — M4's batch graph reads it, and it costs nothing.

Rejected: persisting only from state (loses billed calls on a crash — the failure §2.9 exists to
prevent), and inserting a stub `invoices` row from the corpus manifest (feeds generator truth into
the production path, and would not exist for a real upload).

### 5.4 `serde.py` and the checkpointer

```python
ALLOWED_MSGPACK_MODULES = [
    ("invoice_agent.domain.findings", "Finding"),
    ("invoice_agent.extraction.models", "InvoiceData"),
    ("invoice_agent.extraction.models", "InvoiceLine"),
    ("invoice_agent.llm.cost", "LLMCallRecord"),
]
```

`build_checkpointer` assigns `saver.serde = JsonPlusSerializer(allowed_msgpack_modules=…)` on the
object `from_conn_string` yields (§2.2). The graph compiles with that checkpointer, so the
serialization path is exercised by the M3 tests rather than discovered at M4.

The guard is not "the list has four entries" — it is a **fully populated `EnrichmentState`
round-tripping under strict mode with every value's type preserved**. That way adding a state field
of an unregistered type fails here, not in production.

### 5.5 `scripts/run_one.py`

```
run_one.py --invoice INV-0106 [--corpus corpus]
```

1. Read `corpus/manifest.jsonl`, find the entry, take `path` (and `pdf_path` if present).
2. Invoke the graph with a `thread_id` of `enrich:{invoice_id}`.
3. On success, `invoices.insert_with_lines(extracted)` — the first moment an invoice row *can* exist.
4. Backfill `llm_calls.invoice_id` where `thread_id` matches.
5. Print the outcome: strategy, attempts, findings, and the cost actually recorded.

This is the M3 gate demo, and deliberately thin — M4's `batch_graph` replaces it.

### 5.6 The diagram

`docs/diagrams/enrichment_graph.mmd`, generated by `app.get_graph().draw_mermaid()` and committed.
A test regenerates and compares, so the diagram cannot drift from the graph — and where it disagrees
with design doc §3.3's hand-drawn version, the generated one is right.

---

## 6. Tests

### 6.1 The anchor

**`test_error_history_grows_linearly`**: drive three vision iterations against a stub whose output
always fails validation, then assert `len(state["error_history"]) == 3`.

Not 6, not 7. Returning the accumulated list instead of a delta gives 1, 3, 7 — and the shape of
that sequence is why the plan calls it a trap rather than a bug: at three iterations it looks like
an off-by-one, and only at scale does it read as quadratic.

### 6.2 Mutation verification

| Mutation applied to source | Guard that must go red |
|---|---|
| `vision_extract` returns the full `error_history` instead of a delta | `test_error_history_grows_linearly` |
| `route_after_validate` retries on findings under `strategy == "xml"` | `test_xml_findings_never_retry` |
| `route_after_validate` sends a failed XML extraction straight to `manual_queue` | `test_xml_extraction_failure_climbs_to_vision` |
| `route_after_validate` drops the `extraction_attempts < 3` bound | `test_vision_gives_up_after_three_attempts` |
| `manual_queue` writes `invoices.status` | `test_graph_touches_only_llm_calls` |
| `build_checkpointer` stops assigning the allowlist serde | `test_state_round_trips_under_strict_msgpack` |
| A state field of an unregistered type is added | same test (it enumerates from a populated state) |
| `vision_extract` returns `llm_calls` in state only, leaving the runner to persist them | `test_cost_row_exists_before_the_graph_finishes` — a node placed after `vision_extract` raises, the graph aborts, and the row must still be in `llm_calls`. That is the crash §2.9 cares about, made deterministic instead of simulated |
| The runner skips the `invoice_id` backfill | `test_run_one_links_cost_to_the_invoice` |
| `enrichment_graph.mmd` edited by hand | `test_committed_diagram_matches_the_graph` |

### 6.3 Count

≈22 tests. As in M3-A and M3-B, the count is not the claim; the ten red mutations are.

---

## 7. Success criteria

1. `make check` green, and green again under `LLM_MODE=replay` with **no API keys**.
2. `len(error_history) == 3` after three forced vision iterations.
3. An XML invoice whose extraction fails, **with a `pdf_path` twin**, reaches `vision_extract`; the
   same invoice without a twin reaches `manual_queue`.
4. A truncated PDF reaches `manual_queue` with **zero** `llm_calls` rows.
5. `scripts/run_one.py --invoice <a pdf_vision entry>` produces at least one `llm_calls` row whose
   `invoice_id` matches the invoice the run created — the M3 gate sentence, executed.
6. A fully populated `EnrichmentState` round-trips under **strict** msgpack with every type
   preserved (no `dict` substitutions, §2.1).
7. The committed `.mmd` matches the compiled graph.
8. Ten mutations from §6.2 verified red, one at a time.
9. `ruff` and `mypy` clean over `src/`, `tests/`, `scripts/`, `migrations/`.

---

## 8. Open questions — recorded, not silently dropped

1. **The vision branch still has no accuracy measurement**, synthetic or real (M3-B §8.1). §2.4
   measured that no vision-capable key is configured, so the recorded fixture and the first real
   data point stay **owed**. M6 cannot report a vision number until this is paid.
2. **The recursion bound is ours, not the framework's.** Termination comes from
   `extraction_attempts < 3`, not from LangGraph's recursion limit, so the framework limit is never
   approached and never tested. If M4's batch fan-out changes that, it needs revisiting.
3. **Nothing yet proves the graph survives being wrapped as a function node.** M4.6 makes
   `enrich_one` a wrapper with explicit state mapping; this spec only keeps the signature plain-dict
   in, plain-dict out so that stays possible. Unverified until M4.
4. **`invoices.status` is never written in M3.** A run that ends in `manual_queue` leaves no DB trace
   beyond `llm_calls`. That is deliberate (§5.2) but means the M3 demo shows the give-up only in
   stdout — worth remembering when M8 records the terminal session.
5. **Whether `thread_id` collides across re-runs.** `enrich:{invoice_id}` is stable, so re-running
   the same invoice appends to the same checkpoint thread and backfills the same rows twice. Harmless
   at M3 with one invoice; M4's batch needs a per-run component, and the decision is deferred there.
