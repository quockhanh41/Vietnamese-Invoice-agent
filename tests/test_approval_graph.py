"""The approval graph — design §5, spec 2026-08-01-m5b.

Every test here builds its invoice through `_persisted_invoice`, which commits.
Nodes open their own connections (they must: `apply_decision` commits so
`post_entry`'s guard can re-read), so an uncommitted fixture transaction is
invisible to them. `committed_rows` removes what the test committed — the same
arrangement `tests/test_batch_graph.py` uses, and for the same reason.
"""

import json
from decimal import Decimal

import pytest
from tests.helpers.approval_fixtures import _initial_state, _persisted_invoice

from invoice_agent.graphs import approval


def test_load_snapshot_carries_only_json_types(committed_rows):
    """Spec §0 constraint 3. An `appr:` thread lives DAYS — no other state in
    this system does — so its checkpoint must survive a deploy landing between
    the interrupt and the decision. A pydantic model that gained a required
    field will not deserialize; a dict will. `Decimal` and `date` are
    stringified here, at the boundary, not deep in a node.
    """
    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    out = approval.load_snapshot_node(_initial_state(db_id, approval_id))

    # The real assertion: the whole payload survives json.dumps with no encoder.
    json.dumps({"snapshot": out["snapshot"], "flags": out["flags"]})

    assert out["snapshot"]["invoice_number"]
    assert isinstance(out["snapshot"]["total_amount"], str)
    assert out["snapshot"]["lines"], "a reviewer needs the line items"
    assert [f["code"] for f in out["flags"]] == ["PRICE_OUTLIER"]


def test_load_snapshot_shows_only_open_flags(committed_rows):
    """A resolved flag is history, not a reason to hold the invoice."""
    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import flags

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    with get_owner_engine().begin() as conn:
        for row in flags.open_flags_for(conn, db_id):
            flags.resolve_flag(conn, row["id"])

    out = approval.load_snapshot_node(_initial_state(db_id, approval_id))
    assert out["flags"] == []


def test_suggest_is_empty_on_a_fresh_database(committed_rows):
    """Spec §4.2, reported rather than hidden. `episodic_decisions` is empty
    until reviewers decide things, so the first batch's suggestions are all
    empty lists. Seeding fixtures to make the feature look alive would measure
    the fixtures — the same reason item 4.4's tuning stays deferred.
    """
    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {**_initial_state(db_id, approval_id)}
    state.update(approval.load_snapshot_node(state))

    out = approval.suggest_node(state)
    assert out["suggestions"] == []


def test_suggest_recalls_an_earlier_decision_for_the_same_vendor(committed_rows):
    """The half that proves `suggest` is wired to memory at all, not just to an
    empty table."""
    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import episodic

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {**_initial_state(db_id, approval_id)}
    state.update(approval.load_snapshot_node(state))
    tax_code = state["snapshot"]["seller_tax_code"]

    with get_owner_engine().begin() as conn:
        episodic.record_episode(
            conn,
            invoice_id=db_id,
            vendor_tax_code=tax_code,
            amount=Decimal("100"),
            flags={},
            decision="rejected",
            reason="sai số học",
        )

    out = approval.suggest_node(state)
    assert len(out["suggestions"]) == 1
    assert out["suggestions"][0]["decision"] == "rejected"
    assert out["suggestions"][0]["reason"] == "sai số học"


def test_suggest_persists_the_context_it_built(committed_rows):
    """`approvals` carries `interrupt_payload`/`flags_snapshot`/`suggestions`
    (design §2.8). They are written HERE, before the gate, because the gate must
    contain `interrupt()` and nothing else — anything added to that node runs
    twice across a restart.
    """
    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.db.repositories import approvals as approvals_repo

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    state = {**_initial_state(db_id, approval_id)}
    state.update(approval.load_snapshot_node(state))
    approval.suggest_node(state)

    with get_owner_engine().begin() as conn:
        row = approvals_repo.get_approval(conn, approval_id)
    assert row["flags_snapshot"]["flags"][0]["code"] == "PRICE_OUTLIER"
    assert "invoice" in row["interrupt_payload"]


def test_the_graph_stops_at_the_interrupt_and_checkpoints(committed_rows):
    """The property design §5.2 calls the largest difference between this system
    and a confirm dialog: execution stops and the whole state lands in Postgres,
    so the process may die."""
    from invoice_agent.checkpointer import build_checkpointer

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    config = {"configurable": {"thread_id": f"appr:{db_id}"}}
    with build_checkpointer() as saver:
        saver.setup()
        app = approval.build_approval_graph(checkpointer=saver)
        result = app.invoke(_initial_state(db_id, approval_id), config)

    assert "__interrupt__" in result, "the graph ran past its own gate"
    payload = result["__interrupt__"][0].value
    assert payload["invoice"]["invoice_number"]
    assert payload["flags"][0]["code"] == "PRICE_OUTLIER"


def test_the_gate_node_has_no_side_effect_beyond_the_interrupt():
    """A STATIC guard, and deliberately so. The behavioural cost of a write in
    this node only appears across a `kill -9` (Task 9 measures it), which is
    slow and easy to skip. This one is instant and fails at the commit that
    introduces the write.

    M1.5 measured the consequence: the node containing `interrupt()` runs TWICE
    across resume, so anything here happens twice.
    """
    import inspect

    source = inspect.getsource(approval.approval_gate_node)
    for forbidden in ("conn", "insert", "update", "record_", "get_engine", "repositories"):
        assert forbidden not in source, (
            f"approval_gate_node touches {forbidden!r}; it must contain interrupt() and "
            "nothing else — M1.5 measured that this node runs twice across a restart"
        )


def test_apply_decision_commits_so_a_separate_connection_can_see_it(committed_rows):
    """Spec §2.3 — the boundary that can rot silently.

    `post_entry`'s guard re-reads `approvals` from Postgres. If the two nodes
    shared a transaction the guard would read its own caller's uncommitted
    write, verify nothing, and pass every ordinary test. So this asserts the
    decision is visible from a connection the node never touched.
    """
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import approvals as approvals_repo

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "approved",
        "decided_by": "ketoan",
        "reason": "ok",
    }
    approval.apply_decision_node(state)

    with get_engine().begin() as other:
        row = approvals_repo.get_approval(other, approval_id)
    assert row["decision"] == "approved"
    assert row["decided_by"] == "ketoan", "decided_by is stored BARE, no human: prefix"


def test_apply_decision_accepts_no_connection_parameter():
    """Static guard on the same property. A `conn=` parameter is the one change
    that would let a caller hand `apply_decision` the transaction `post_entry`
    later reads on — re-creating the defect above with the behavioural test
    still green, because the test above opens its own connection either way.
    """
    import inspect

    assert "conn" not in inspect.signature(approval.apply_decision_node).parameters


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_deciding_an_invoice_closes_its_flags(committed_rows, decision):
    """M5-A spec §8 question 1, answered. A human has adjudicated the flags
    either way. Left open, `invoices.status` says `flagged` forever,
    `route_outcomes` keeps treating the invoice as blocked, and M5-C's queue
    re-serves invoices somebody already decided.

    The rows are NOT deleted — `resolved_at` is set, so §10.4's flag
    false-positive metric can still count them.
    """
    from sqlalchemy import select

    from invoice_agent.db import models
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import flags

    db_id, approval_id = _persisted_invoice(
        committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly")
    )
    state = {
        **_initial_state(db_id, approval_id),
        "decision": decision,
        "decided_by": "ketoan",
    }
    approval.apply_decision_node(state)

    with get_engine().begin() as conn:
        assert flags.open_flags_for(conn, db_id) == []
        rows = list(
            conn.execute(
                select(models.invoice_flags).where(models.invoice_flags.c.invoice_id == db_id)
            ).mappings()
        )
    assert len(rows) == 1, "the flag row must survive; only resolved_at changes"
    assert rows[0]["resolved_at"] is not None


@pytest.mark.parametrize("decision", ["approved", "edited"])
def test_an_approved_invoice_posts_with_human_authority(committed_rows, decision):
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import invoices, journal

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": decision,
        "decided_by": "ketoan",
    }
    state.update(approval.load_snapshot_node(state))
    approval.apply_decision_node(state)
    out = approval.post_entry_node(state)

    with get_engine().begin() as conn:
        entry = journal.get_for_invoice(conn, db_id)
        assert invoices.get_by_id(conn, db_id)["status"] == "posted"
    assert entry["posting_authority"] == "human_approved"
    assert entry["approval_id"] == approval_id
    assert out["entry_id"] == str(entry["id"])


def test_the_audit_actor_is_prefixed_exactly_once(committed_rows):
    """Spec §4.5. `decided_by` is bare in `approvals`; `human:` is added only
    here. Prefixing an already-prefixed value yields `human:human:ketoan`, which
    no constraint rejects and no other test would notice.
    """
    from sqlalchemy import select

    from invoice_agent.db import models
    from invoice_agent.db.engine import get_engine

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "approved",
        "decided_by": "ketoan",
    }
    state.update(approval.load_snapshot_node(state))
    approval.apply_decision_node(state)
    approval.post_entry_node(state)

    with get_engine().begin() as conn:
        actors = {
            r["actor"]
            for r in conn.execute(
                select(models.audit_log).where(models.audit_log.c.target_id == str(db_id))
            ).mappings()
        }
    assert actors == {"human:ketoan"}


def test_a_rejected_invoice_is_marked_rejected_and_never_posted(committed_rows):
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import invoices, journal

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "rejected",
        "decided_by": "ketoan",
        "reason": "hóa đơn trùng",
    }
    state.update(approval.load_snapshot_node(state))
    approval.apply_decision_node(state)
    approval.record_rejection_node(state)

    with get_engine().begin() as conn:
        assert journal.get_for_invoice(conn, db_id) is None
        assert invoices.get_by_id(conn, db_id)["status"] == "rejected"


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("approved", "post_entry"),
        ("edited", "post_entry"),
        ("rejected", "record_rejection"),
    ],
)
def test_routing_after_the_decision(decision, expected):
    assert approval.route_after_decision({"decision": decision}) == expected


def test_a_guard_denial_in_post_entry_is_not_swallowed(committed_rows):
    """Spec §4.5. In `auto_post` a denial routes the invoice to a human; here a
    human has ALREADY decided, so a denial means the guard and the reviewer
    disagree. Absorbing it would hide exactly the disagreement the guard exists
    to surface.
    """
    from invoice_agent.tools import guards

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "approved",
        "decided_by": "ketoan",
    }
    state.update(approval.load_snapshot_node(state))
    # Deliberately NOT calling apply_decision — the approvals row stays
    # undecided, which is precisely what the guard refuses.
    with pytest.raises(guards.GuardDenied):
        approval.post_entry_node(state)


def test_a_rejection_is_remembered_with_its_reason(committed_rows):
    """Design §5.4. HITL and memory are one mechanism, not two features."""
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import episodic, vendors

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "rejected",
        "decided_by": "ketoan",
        "reason": "sai số học",
    }
    state.update(approval.load_snapshot_node(state))
    approval.learn_node(state)

    tax_code = state["snapshot"]["seller_tax_code"]
    with get_engine().begin() as conn:
        episodes = episodic.similar_episodes(conn, vendor_tax_code=tax_code)
        vendor = vendors.get_vendor(conn, tax_code)
    assert [e["reason"] for e in episodes] == ["sai số học"]
    assert vendor["rejection_count"] == 1


def test_an_approval_is_remembered_too(committed_rows):
    """Past design §5.4's literal wording, deliberately. `suggest` asks "what
    happened last time for this vendor"; a memory holding only refusals biases
    every suggestion it ever makes. Recorded as a decision, not a drift.
    """
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import episodic, vendors

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "approved",
        "decided_by": "ketoan",
    }
    state.update(approval.load_snapshot_node(state))
    approval.learn_node(state)

    tax_code = state["snapshot"]["seller_tax_code"]
    with get_engine().begin() as conn:
        episodes = episodic.similar_episodes(conn, vendor_tax_code=tax_code)
        vendor = vendors.get_vendor(conn, tax_code)
    assert [e["decision"] for e in episodes] == ["approved"]
    assert vendor["rejection_count"] == 0, "an approval is not a rejection"


def test_field_corrections_reach_the_field_classify_reads(committed_rows):
    """Design §5.4's second arrow: `field_corrections` → `vendors.reviewer_notes`
    → what `classify` reads next time. Without this the loop is decorative."""
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import vendors

    db_id, approval_id = _persisted_invoice(committed_rows)
    state = {
        **_initial_state(db_id, approval_id),
        "decision": "edited",
        "decided_by": "ketoan",
        "corrections": {"category": "IT_SERVICES"},
    }
    state.update(approval.load_snapshot_node(state))
    approval.learn_node(state)

    with get_engine().begin() as conn:
        vendor = vendors.get_vendor(conn, state["snapshot"]["seller_tax_code"])
    assert "IT_SERVICES" in vendor["reviewer_notes"]


def test_submit_decision_drives_a_pending_thread_to_completion(committed_rows):
    """The seam M5-C's route wraps. The API adds no logic — it parses a body and
    calls this — which is what makes M7's "interrupt is transport-agnostic"
    artefact a demonstration instead of a claim.
    """
    from invoice_agent.checkpointer import build_checkpointer
    from invoice_agent.db.engine import get_engine
    from invoice_agent.db.repositories import journal

    db_id, approval_id = _persisted_invoice(committed_rows)
    config = {"configurable": {"thread_id": f"appr:{db_id}"}}
    with build_checkpointer() as saver:
        saver.setup()
        app = approval.build_approval_graph(checkpointer=saver)
        first = app.invoke(_initial_state(db_id, approval_id), config)
    assert "__interrupt__" in first

    result = approval.submit_decision(
        invoice_id=str(db_id), decision="approved", decided_by="ketoan"
    )
    assert "__interrupt__" not in result
    assert result["status"] == "learned"

    with get_engine().begin() as conn:
        assert journal.get_for_invoice(conn, db_id) is not None


def test_an_unknown_decision_never_reaches_the_graph(committed_rows):
    """A typo must not resume a thread into a state `route_after_decision`
    cannot route — that would silently take the rejection branch and mark a
    perfectly good invoice rejected."""
    db_id, _approval_id = _persisted_invoice(committed_rows)
    with pytest.raises(ValueError, match="decision"):
        approval.submit_decision(
            invoice_id=str(db_id), decision="aproved", decided_by="ketoan"
        )


def test_the_counted_graph_matches_the_real_one():
    """`hitl_child.build_counted_graph` re-declares the topology in order to wrap
    two nodes. A copy of a graph is a copy that drifts — so this compares the
    rendered mermaid of both, and portfolio test 1 stops measuring the real
    graph the moment they differ."""
    from tests.helpers.hitl_child import build_counted_graph

    from invoice_agent.checkpointer import build_checkpointer

    with build_checkpointer() as saver:
        counted = build_counted_graph("appr:x", saver).get_graph().draw_mermaid()
        real = approval.build_approval_graph(checkpointer=saver).get_graph().draw_mermaid()
    assert counted == real


def test_the_committed_diagram_matches_the_graph():
    """The same drift guard the enrichment and batch graphs have had since M3-C.

    Added 2026-08-01 by the M5-B review, which found `approval_graph.mmd` had
    been hand-drawn from the design doc, never regenerated once
    `graphs/approval.py` existed, and was STALE — while the closeout note listed
    it as a delivered artefact. Two of three diagrams were guarded; the
    unguarded one is the one that rotted.

    Regenerate with `python -m scripts.render_graph`; never hand-edit the .mmd.
    """
    import pathlib

    committed = pathlib.Path("docs/diagrams/approval_graph.mmd")
    assert committed.exists()
    assert (
        committed.read_text(encoding="utf-8").strip()
        == approval.build_approval_graph().get_graph().draw_mermaid().strip()
    )






