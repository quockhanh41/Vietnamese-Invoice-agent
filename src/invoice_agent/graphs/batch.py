"""batch_graph — fan out over invoices, then join serially (design doc §4).

THE SHAPE IS THE ARGUMENT. Every fan-out task is READ-ONLY; every write happens
in one serial node after the join. That single decision buys three properties at
once, and they are the milestone:

  * budget reconciliation is order-invariant, because one walker in canonical
    order draws down the budget;
  * duplicate detection is order-invariant, because the same walker decides
    which member of a pair is the original;
  * `signals` never sees the invoice it is scoring in that invoice's own vendor
    statistics, because `apply_invoice` runs here rather than in the task.

That third one is silent when broken. M4-B measured the self-inclusion ceiling:
a single extreme point's z-score against a sample containing itself is bounded
by (n-1)/sqrt(n) — 1.79 at n=5 — so persisting before `signals` runs does not
fail, it just stops PRICE_OUTLIER ever firing.
"""

import operator
import uuid
from decimal import Decimal
from typing import Annotated, Any, TypedDict

from langgraph.types import Send

from invoice_agent.extraction.models import InvoiceData
from invoice_agent.observability import traced


class EnrichedInvoice(TypedDict):
    """What a fan-out task hands the serial join. Deliberately small: the join
    re-derives everything else from the database."""

    invoice_id: str
    source_path: str
    source_type: str
    department: str
    extracted: InvoiceData | None
    findings: list[Any]
    signals: list[Any]
    # Read by `_join` to persist `invoices.category`/`department`, and by
    # `auto_post` to pick TT133 accounts. Carried rather than re-derived: the
    # classification is an LLM output and re-running it in the serial join would
    # both cost tokens and risk a different answer than the one that was scored.
    classification: Any
    status: str


class BatchState(TypedDict):
    batch_id: str
    inputs: list[dict[str, Any]]
    # Accumulate across Sends. Nodes return DELTAS — returning the accumulated
    # list makes each superstep double the last (design §3.1's reducer trap).
    enriched: Annotated[list[EnrichedInvoice], operator.add]
    failed: Annotated[list[dict[str, str]], operator.add]
    persisted: list[dict[str, Any]]
    consumption: list[dict[str, Any]]
    raised: list[dict[str, Any]]
    unroutable: list[str]
    auto: list[str]
    needs_approval: list[str]
    posted: list[dict[str, Any]]
    opened: list[dict[str, Any]]
    unopened: list[dict[str, str]]
    status: str



def thread_id_for(batch_id: uuid.UUID | str, invoice_id: str) -> str:
    """Design §1.3's correlation key, with the per-run component M3-C §8.5 owes.

    `enrich:{invoice_id}` alone is stable across re-runs, so a second batch over
    the same invoice resumes the first batch's checkpoints rather than starting
    clean — and the interrupt/resume semantics M1 proved would then apply to the
    wrong run.
    """
    return f"enrich:{batch_id}:{invoice_id}"


def enrich_one(task: dict[str, Any], *, graph: Any) -> dict[str, Any]:
    invoice_id = task["invoice_id"]
    state = {
        "invoice_id": invoice_id,
        "source_path": task["source_path"],
        "pdf_path": task.get("pdf_path"),
        "source_type": task["source_type"],
        "strategy": "xml" if task["source_type"] == "xml" else "vision",
        "extracted": None,
        "extraction_attempts": 0,
        "current_errors": [],
        "error_history": [],
        "findings": [],
        "signals": [],
        "llm_calls": [],
        "classification": None,
        "audit_verdict": None,
        "status": "",
    }
    config = {"configurable": {"thread_id": thread_id_for(task["batch_id"], invoice_id)}}

    try:
        result = graph.invoke(state, config)
    except Exception as exc:  # noqa: BLE001 - a bad file must not end the batch
        return {"enriched": [], "failed": [{"invoice_id": invoice_id, "error": str(exc)}]}

    extracted = result.get("extracted")
    # No `or "MKT"` default. A department this wrapper cannot determine is a
    # fact to carry, not a value to invent — the serial join skips an invoice it
    # cannot route rather than reconciling it against somebody else's budget.
    dept = task.get("department") or getattr(extracted, "department", None)
    return {
        "enriched": [
            {
                "invoice_id": task["invoice_id"],
                "source_path": task["source_path"],
                "source_type": task["source_type"],
                "department": dept,
                "extracted": extracted,
                "findings": result.get("findings", []),
                "signals": result.get("signals", []),
                "classification": result.get("classification"),
                # PROPAGATED, not overwritten. `manual_queue_node` returns
                # `{"status": "manual_queue"}` (enrichment.py:196) — a CONTROLLED
                # give-up, which is a different outcome from a successful
                # enrichment. Hardcoding "enriched" here made the two
                # indistinguishable, and M5's reviewer queue is exactly what
                # needs to tell them apart.
                "status": result.get("status") or "enriched",
            }
        ],
        "failed": [],
    }


@traced(node="serial_join")
def serial_join(state: BatchState, *, conn: Any = None) -> dict[str, Any]:
    """Persist, profile, de-duplicate and reconcile — ONE transaction.

    Constraint 2: the batch must never end with invoices recorded and budgets
    not drawn against them, so this is the batch's single commit point. A
    failure here loses persistence but not the expensive work: `llm_calls` rows
    were written inline during enrichment and survive independently.
    """
    if conn is not None:
        return _join(conn, state)
    from invoice_agent.db.engine import get_engine

    with get_engine().begin() as owned:
        return _join(owned, state)


def _join(conn: Any, state: BatchState) -> dict[str, Any]:
    from typing import cast

    from sqlalchemy import select

    from invoice_agent.db import models
    from invoice_agent.db.repositories import (
        batches,
        budget_consumption,
        flags,
        invoices,
        vendors,
    )
    from invoice_agent.domain import anomaly, findings
    from invoice_agent.domain import budget as domain_budget
    from invoice_agent.extraction.models import InvoiceData
    from invoice_agent.memory import vendor_profile
    from invoice_agent.tools import guards

    survivors = [e for e in state["enriched"] if e.get("extracted") is not None]

    def _canonical_key(e: EnrichedInvoice) -> tuple[Any, str, str]:
        data = cast(InvoiceData, e["extracted"])
        return (data.issue_date, data.invoice_series, data.invoice_number)

    ordered = sorted(survivors, key=_canonical_key)

    batch_uuid = (
        uuid.UUID(state["batch_id"])
        if isinstance(state["batch_id"], str)
        else state["batch_id"]
    )
    batches.ensure_batch(conn, batch_uuid)
    persisted: list[dict[str, Any]] = []
    raised: list[dict[str, Any]] = []
    db_id_map: dict[str, uuid.UUID] = {}
    priors: list[anomaly.InvoiceKey] = []

    for e in ordered:
        inv = cast(InvoiceData, e["extracted"])
        if inv.seller_tax_code is None:
            # RECORDED AND SKIPPED, never asserted. This was a bare
            # `assert inv.seller_tax_code is not None`, and the first LIVE run
            # of this batch — 2026-08-01, the max_concurrency measurement in
            # docs/notes/2026-08-01-m5b-approval-graph.md — died on it: a vision
            # extraction returned no seller tax code and the AssertionError took
            # the WHOLE batch with it, all 30 invoices.
            #
            # Replay could never surface this. The recorded fixture always
            # carries a tax code, so the assert never fires; only a live model
            # produces the omission.
            #
            # Two things were wrong with it. It broke M4-D's constraint that one
            # bad file must not end the batch — the reason `enrich_one` wraps
            # every task in try/except. And `assert` is stripped under
            # `python -O`, so the "protection" evaporates in exactly the
            # deployment that needs it most.
            #
            # An invoice with no seller MST cannot be given an identity (the
            # `invoices.seller_tax_code` NOT NULL column comment), so there is
            # nothing to persist. It is surfaced with the vocabulary that
            # already exists for it and the walk continues.
            raised.append({"invoice_id": e["invoice_id"], "code": findings.TAX_CODE_MISSING})
            continue
        vendors.ensure_vendor(conn, inv.seller_tax_code, inv.seller_name)
        cls = e.get("classification")
        db_id = invoices.insert_with_lines(
            conn,
            inv,
            batch_id=batch_uuid,
            source_type=e["source_type"],
            source_path=e["source_path"],
            category=getattr(cls, "category", None),
            department=e.get("department") or getattr(cls, "department", None),
        )

        if db_id is None:
            raised.append({"invoice_id": e["invoice_id"], "code": findings.DUPLICATE_INVOICE})
            continue

        db_id_map[e["invoice_id"]] = db_id
        persisted.append({"invoice_id": e["invoice_id"], "db_id": str(db_id)})

        # Gap 1 (spec §1.1). Every finding and signal the enrichment produced
        # becomes a row, because the trigger and the reviewer queue both read
        # this table and neither can see graph state. `severity`/`raised_by`
        # come from CODE_REGISTRY via `findings.make`, so a caller cannot
        # declare its own — and every source it can produce is already in
        # FLAG_SOURCES, so no CHECK needs widening.
        for finding in (*e.get("findings", []), *e.get("signals", [])):
            flags.raise_flag(
                conn,
                db_id,
                code=finding.code,
                severity=finding.severity,
                raised_by=finding.raised_by,
                detail=finding.detail,
            )

        vendor_profile.apply_invoice(
            conn,
            tax_code=inv.seller_tax_code,
            amount=Decimal(inv.total_amount),
            category=getattr(inv, "category", None),
            department=getattr(inv, "department", None),
        )

        cand = anomaly.InvoiceKey(
            seller_tax_code=inv.seller_tax_code,
            invoice_number=inv.invoice_number,
            issue_date=inv.issue_date,
            total_amount=Decimal(inv.total_amount),
        )
        near_dup = anomaly.near_duplicate(cand, priors)
        priors.append(cand)

        if near_dup:
            flags.raise_flag(
                conn,
                db_id,
                code=near_dup.code,
                severity=near_dup.severity,
                raised_by=near_dup.raised_by,
                detail=near_dup.detail,
            )
            raised.append({"invoice_id": e["invoice_id"], "code": near_dup.code})

    # Group survivors by (department, period). BOTH matter: `budgets` is keyed
    # UNIQUE(department_code, period, category), so grouping by department alone
    # and taking one invoice's period would draw a July invoice against an
    # August budget the moment a batch spans a month boundary.
    by_dept_period: dict[tuple[str, str], list[tuple[EnrichedInvoice, InvoiceData]]] = {}
    unroutable: list[str] = []
    undeclared_department: list[str] = []
    for e in ordered:
        data_obj = cast(InvoiceData, e["extracted"])
        dept = e.get("department") or getattr(data_obj, "department", None)
        if not dept:
            # NOT defaulted to a department. Reconciling an invoice against an
            # arbitrary budget spends somebody else's money on paper and emits a
            # BUDGET_BREACH nobody can trace. Recorded and skipped instead.
            unroutable.append(e["invoice_id"])
            # Tracked SEPARATELY from the unbudgeted-period case below, because
            # only this one gets a flag. An invoice with no department was never
            # attributable to anyone; an invoice whose department simply has no
            # budget row for that month is a configuration gap, and flagging
            # every invoice in an unbudgeted month is precisely the noise
            # §10.4's false-positive rate measures.
            undeclared_department.append(e["invoice_id"])
            continue
        period = data_obj.issue_date.strftime("%Y-%m")
        by_dept_period.setdefault((dept, period), []).append((e, data_obj))

    consumption_out: list[dict[str, Any]] = []
    for (dept, period) in sorted(by_dept_period.keys()):
        dept_items = by_dept_period[(dept, period)]

        stmt = select(models.budgets).where(
            models.budgets.c.department_code == dept,
            models.budgets.c.period == period,
        ).with_for_update()
        budget_row = conn.execute(stmt).mappings().first()

        if not budget_row:
            # NO FABRICATED FALLBACK. This read `Decimal("50000000")` when the
            # row was missing, so a department with no seeded budget was
            # reconciled against an INVENTED limit and could emit a
            # BUDGET_BREACH derived from a number with no provenance — against
            # CLAUDE.md's rule that every non-obvious constant carries date +
            # measurement + report filename, and against the premise that flags
            # come from the source of truth. An unbudgeted department is
            # recorded and skipped; §10.4's false-positive rate is the metric
            # this protects.
            unroutable.extend(e["invoice_id"] for e, _inv in dept_items)
            continue

        limit = Decimal(budget_row["amount_limit"])
        warn_pct = Decimal(budget_row["warn_threshold_pct"])

        inputs = [
            domain_budget.BudgetInput(
                invoice_id=e["invoice_id"],
                issue_date=inv.issue_date,
                invoice_series=inv.invoice_series,
                invoice_number=inv.invoice_number,
                amount=inv.total_amount,
            )
            for e, inv in dept_items
        ]

        consumptions = domain_budget.consume(limit, warn_pct, inputs)
        for c in consumptions:
            consumption_out.append(
                {
                    "invoice_id": c.invoice_id,
                    "amount": str(c.amount),
                    "balance_before": str(c.balance_before),
                    "balance_after": str(c.balance_after),
                    "sequence_no": c.sequence_no,
                }
            )
            if budget_row and c.invoice_id in db_id_map:
                budget_consumption.record_consumption(
                    conn,
                    budget_id=budget_row["id"],
                    invoice_id=db_id_map[c.invoice_id],
                    amount=c.amount,
                    balance_before=c.balance_before,
                    balance_after=c.balance_after,
                    sequence_no=c.sequence_no,
                )
            for f in c.findings:
                raised.append({"invoice_id": c.invoice_id, "code": f.code})
                if c.invoice_id in db_id_map:
                    flags.raise_flag(
                        conn,
                        db_id_map[c.invoice_id],
                        code=f.code,
                        severity=f.severity,
                        raised_by=f.raised_by,
                        detail=f.detail,
                    )

    # An unroutable invoice RAISES A FLAG, not just a list entry. Returning it in
    # the node's result surfaced it only to whoever reads that result;
    # `route_outcomes` reads `invoice_flags`, so an invoice that was never drawn
    # against any budget still reached the auto path. Measured 2026-08-02: a
    # clean 51,000,000 đồng invoice with no declared department acquired no
    # budget flag in either direction (docs/notes/2026-08-02-m6a-injection.md
    # §6.3). `warn` blocks, so raising it is what sends the invoice to a human.
    #
    # MUST run before the status loop below, which reads the table back.
    for invoice_key in undeclared_department:
        db_id = db_id_map.get(invoice_key)
        if db_id is None:
            # Never persisted — `route_outcomes` already sends it to a human,
            # and there is no row to attach a flag to.
            continue
        unreconciled = findings.make(
            findings.BUDGET_UNRECONCILED,
            department=next(
                (
                    e.get("department")
                    for e in (state.get("enriched") or [])
                    if e["invoice_id"] == invoice_key
                ),
                None,
            ),
        )
        raised.append({"invoice_id": invoice_key, "code": unreconciled.code})
        flags.raise_flag(
            conn,
            db_id,
            code=unreconciled.code,
            severity=unreconciled.severity,
            raised_by=unreconciled.raised_by,
            detail=unreconciled.detail,
        )

    # Status is set LAST and read back from the table, not accumulated in
    # Python. Budget flags are raised after the per-invoice loop, so an invoice
    # that is clean at insert time can acquire a BUDGET_BREACH before this node
    # returns — computing status earlier would record it as `validated` while
    # `invoice_flags` says otherwise, and the guard reads the table.
    for _invoice_key, db_id in db_id_map.items():
        blocking = [
            f
            for f in flags.open_flags_for(conn, db_id)
            if f["severity"] in guards.BLOCKING_SEVERITIES
        ]
        invoices.set_status(conn, db_id, "flagged" if blocking else "validated")

    return {
        "persisted": persisted,
        "consumption": consumption_out,
        "raised": raised,
        # Invoices no budget could be found for — no department, or no budgets
        # row for (department, period). Surfaced rather than silently dropped:
        # an invoice that was persisted but never drawn against a budget is a
        # real state a reviewer has to see, and the previous code hid it behind
        # an invented 50,000,000 limit.
        "unroutable": unroutable,
    }


@traced(node="ingest")
def ingest_node(state: BatchState) -> dict[str, Any]:
    batch_id = state.get("batch_id") or str(uuid.uuid4())
    return {"batch_id": batch_id, "status": "ingested"}


@traced(node="cost_gate")
def cost_gate_node(state: BatchState) -> dict[str, Any]:
    """Check budget/file-count gate.

    M6.6 owns the real token budget logic and cost cap. Ships as a threshold
    check here to establish graph topology per design §4.
    """
    return {"status": "cost_gate_passed"}


def cost_gate_condition(state: BatchState) -> str:
    inputs = state.get("inputs") or []
    if len(inputs) > 10000:
        return "rejected"
    return "ok"


@traced(node="fan_out")
def fan_out_node(state: BatchState) -> dict[str, Any]:
    return {"status": "fan_out"}


def fan_out_router(state: BatchState) -> list[Send]:
    batch_id = state["batch_id"]
    inputs = state.get("inputs") or []
    return [
        Send("enrich_one", {**inp, "batch_id": batch_id})
        for inp in inputs
    ]


@traced(node="enrich_one")
def enrich_one_task_node(task: Any) -> dict[str, Any]:
    from invoice_agent.graphs.enrichment import build_enrichment_graph

    enrichment_graph = build_enrichment_graph()
    return enrich_one(task, graph=enrichment_graph)


@traced(node="route_outcomes")
def route_outcomes_node(state: BatchState, *, conn: Any = None) -> dict[str, Any]:
    """Split into auto and needs_approval, reading `invoice_flags`.

    READS THE TABLE, NOT GRAPH STATE — a correction, not a preference. Measured
    2026-08-01 on the 30-invoice gate batch: routing on the in-memory
    `findings`/`signals` a fan-out task returned sent **8 invoices to the auto
    path that the guard then refused, every one of them for `BUDGET_BREACH`**.
    Budget findings are computed in `serial_join`, AFTER the fan-out, so they can
    never appear in a task's own result — this node was structurally blind to the
    entire budget family.

    The system stayed safe, because the guard re-reads Postgres and denied all
    eight. But every batch then wrote eight `audit_log` denial rows, which read
    as eight safety refusals when they were one routing bug. Roadmap principle 5
    — do not trust graph state, trust the database — governs routing exactly as
    it governs the guard, and running the same predicate over the same rows is
    what makes the two agree by construction rather than by coincidence.

    SEVERITY decides, not the mere existence of a flag. Measured 2026-08-01:
    routing on "any finding" sent 28 of 28 enriched invoices to a reviewer and
    left `auto` empty, because `NEW_VENDOR` fires on every vendor's FIRST
    sighting — 51 across the corpus (docs/notes/2026-07-31-m4b-vendor-memory.md).
    `info` asserts something is NEW, true every time it fires, not that something
    is WRONG. `warn` and `critical` assert a defect and do require a person.
    """
    if conn is not None:
        return _route(conn, state)
    from invoice_agent.db.engine import get_engine

    with get_engine().begin() as owned:
        return _route(owned, state)


def _route(conn: Any, state: BatchState) -> dict[str, Any]:
    from invoice_agent.db.repositories import flags
    from invoice_agent.tools import guards

    db_ids = {p["invoice_id"]: uuid.UUID(p["db_id"]) for p in state.get("persisted") or []}

    auto: list[str] = []
    needs_approval: list[str] = []

    for e in state.get("enriched") or []:
        db_id = db_ids.get(e["invoice_id"])
        if db_id is None:
            # Enriched but never persisted — a duplicate the natural key
            # rejected, or an invoice the join could not store. There is nothing
            # to post and a person has to see it. These previously reached
            # `auto_post` and were bounced there; deciding it here keeps `auto`
            # meaning "postable" rather than "not yet known to be unpostable".
            needs_approval.append(e["invoice_id"])
            continue
        blocking = [
            f
            for f in flags.open_flags_for(conn, db_id)
            if f["severity"] in guards.BLOCKING_SEVERITIES
        ]
        if blocking:
            needs_approval.append(e["invoice_id"])
        else:
            auto.append(e["invoice_id"])

    for f in state.get("failed") or []:
        needs_approval.append(f["invoice_id"])

    return {"auto": auto, "needs_approval": needs_approval, "status": "routed"}


@traced(node="finalize")
def finalize_node(state: BatchState) -> dict[str, Any]:
    batch_id_str = state.get("batch_id")
    if batch_id_str:
        try:
            batch_uuid = uuid.UUID(batch_id_str)
            from invoice_agent.db.engine import get_engine
            from invoice_agent.db.repositories import batches

            with get_engine().begin() as conn:
                count = len(state.get("enriched") or []) + len(state.get("failed") or [])
                batches.complete_batch(conn, batch_uuid, status="completed", invoice_count=count)
        except Exception:
            pass
    return {"status": "completed"}


@traced(node="auto_post")
def auto_post_node(state: BatchState) -> dict[str, Any]:
    """Post every clean invoice through the guard — design §4's auto_post arm.

    WHAT THIS IS ALLOWED TO BOOK, stated because it is M5-A's one real risk.
    The account codes come from the invoice's category, which the
    ClassificationAgent chose (95.4% accurate, M4-C). The LLM proposes; the
    category -> account map is deterministic seed data; zero open warn/critical
    flags is enforced by the database. A MIS-CLASSIFIED CLEAN INVOICE THEREFORE
    BOOKS TO 6421 INSTEAD OF 6422 WITH NOBODY LOOKING. That is an accuracy cost,
    not a breach of the safety property — the LLM still never toggles a flag and
    never decides that posting is permitted.

    ONE TRANSACTION PER INVOICE. A single bad entry must not roll back every
    other entry in the batch; the same argument `enrich_one`'s try/except makes
    for the fan-out, applied to the write path.
    """
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import invoices as invoices_repo
    from invoice_agent.db.repositories import reference
    from invoice_agent.domain import accounting
    from invoice_agent.tools import guards

    db_ids = {p["invoice_id"]: uuid.UUID(p["db_id"]) for p in state.get("persisted") or []}
    by_id = {e["invoice_id"]: e for e in state.get("enriched") or []}

    posted: list[dict[str, Any]] = []
    still_auto: list[str] = []
    to_human: list[str] = list(state.get("needs_approval") or [])

    for invoice_key in state.get("auto") or []:
        db_id = db_ids.get(invoice_key)
        entry = by_id.get(invoice_key)
        extracted = entry.get("extracted") if entry else None
        if db_id is None or entry is None or extracted is None:
            # Routed rather than dropped: an invoice in `auto` that never
            # persisted is a state a reviewer has to see. M4-D added
            # `unroutable` for the same reason.
            to_human.append(invoice_key)
            continue

        with get_engine().begin() as conn:
            row = invoices_repo.get_by_id(conn, db_id)
            category = row["category"] if row else None
            cat_row = reference.get_category(conn, category) if category else None
            if cat_row is None:
                to_human.append(invoice_key)
                continue

            accounts = accounting.CategoryAccounts(
                debit_account=cat_row["debit_account"],
                debit_name=cat_row["name_vi"],
                vat_account=cat_row["vat_account"],
                vat_name="Thuế GTGT được khấu trừ" if cat_row["vat_account"] else None,
                credit_account=cat_row["credit_account"],
                credit_name="Phải trả cho người bán",
            )
            lines = accounting.build_journal_entry(extracted, accounts)
            try:
                entry_id = guards.create_journal_entry(
                    conn,
                    invoice_id=db_id,
                    posting_authority="auto_clean",
                    entry_date=extracted.issue_date,
                    lines=lines,
                    actor="system",
                )
            except guards.GuardDenied:
                # The guard already wrote the audit row, on its own connection,
                # so it survives this transaction being abandoned.
                to_human.append(invoice_key)
                continue

        posted.append(
            {"invoice_id": invoice_key, "db_id": str(db_id), "entry_id": str(entry_id)}
        )
        still_auto.append(invoice_key)

    return {"posted": posted, "auto": still_auto, "needs_approval": to_human}


@traced(node="open_approval")
def open_approval_node(state: BatchState) -> dict[str, Any]:
    """Design §5.2's arm: one approval thread per flagged invoice, then return.

    The batch does NOT wait. Each thread runs to `interrupt()` and stops, its
    whole state checkpointed to Postgres, so the reviewer may decide hours later
    from a different process. That is the entire justification for three graphs
    rather than one (design §1.1), and `test_batch_not_blocked_by_pending.py`
    is the measurement of it.

    ONE checkpointer for the whole node, not one per invoice: a PostgresSaver
    per invoice would open a connection pool per invoice to accomplish nothing.
    """
    from invoice_agent.checkpointer import build_checkpointer
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import approvals as approvals_repo
    from invoice_agent.graphs.approval import build_approval_graph

    db_ids = {p["invoice_id"]: uuid.UUID(p["db_id"]) for p in state.get("persisted") or []}
    opened: list[dict[str, Any]] = []
    unopened: list[dict[str, str]] = []

    pending = [k for k in (state.get("needs_approval") or []) if k in db_ids]
    unopened.extend(
        {"invoice_id": k, "reason": "not persisted"}
        for k in (state.get("needs_approval") or [])
        if k not in db_ids
    )
    if not pending:
        return {"opened": opened, "unopened": unopened, "status": "approvals_opened"}

    with build_checkpointer() as saver:
        saver.setup()
        graph = build_approval_graph(checkpointer=saver)
        for invoice_key in pending:
            db_id = db_ids[invoice_key]
            thread_id = f"appr:{db_id}"
            with get_engine().begin() as conn:
                approval_id = approvals_repo.open_approval(
                    conn,
                    invoice_id=db_id,
                    thread_id=thread_id,
                    interrupt_payload={},
                    flags_snapshot={},
                    suggestions={},
                )
            initial: dict[str, Any] = {

                "invoice_id": str(db_id),
                "approval_id": str(approval_id),
                "snapshot": {},
                "flags": [],
                "suggestions": [],
                "decision": "",
                "decided_by": "",
                "reason": None,
                "corrections": None,
                "entry_id": None,
                "status": "",
            }
            result = graph.invoke(initial, {"configurable": {"thread_id": thread_id}})
            if "__interrupt__" not in result:
                # The graph ran past its own gate. Reported rather than
                # asserted: a batch node must not die because one thread
                # misbehaved, and a silent success here would mean an invoice
                # nobody is ever asked about.
                unopened.append({"invoice_id": invoice_key, "reason": "no interrupt raised"})
                continue
            opened.append(
                {
                    "invoice_id": invoice_key,
                    "db_id": str(db_id),
                    "approval_id": str(approval_id),
                    "thread_id": thread_id,
                }
            )

    return {"opened": opened, "unopened": unopened, "status": "approvals_opened"}


def build_batch_graph(*, max_concurrency: int = 5) -> Any:
    """Compile the batch workflow graph.

    max_concurrency defaults to 5 (design §4.2's 4-6). Ships unexercised
    against real rate limits because the gate replays hermetically — M4-C
    measured 177 Gemini rate-limits in a serial run.
    """
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(BatchState)

    builder.add_node("ingest", ingest_node)
    builder.add_node("cost_gate", cost_gate_node)
    builder.add_node("fan_out", fan_out_node)
    builder.add_node("enrich_one", enrich_one_task_node)  # type: ignore[arg-type]
    builder.add_node("serial_join", serial_join)
    builder.add_node("route_outcomes", route_outcomes_node)
    builder.add_node("auto_post", auto_post_node)
    builder.add_node("open_approval", open_approval_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "cost_gate")
    builder.add_conditional_edges("cost_gate", cost_gate_condition, {"ok": "fan_out", "rejected": END})
    builder.add_conditional_edges("fan_out", fan_out_router, ["enrich_one"])
    builder.add_edge("enrich_one", "serial_join")
    builder.add_edge("serial_join", "route_outcomes")
    builder.add_edge("route_outcomes", "auto_post")
    builder.add_edge("auto_post", "open_approval")
    builder.add_edge("open_approval", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile()



