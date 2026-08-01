"""One node, one transaction, canonical order (spec 2026-08-01-m4d §3.3)."""

import uuid
from decimal import Decimal

from invoice_agent.domain import findings
from invoice_agent.graphs import batch
from invoice_agent.memory import vendor_profile


def _enriched(invoice, invoice_id, department="MKT"):
    """`department` is EXPLICIT, because nothing can derive it.

    `InvoiceData` has no department field — the department comes from the
    manifest (or, later, from `classify`), and the batch graph passes it in via
    the task dict. These helpers previously omitted it and `_join` supplied a
    hardcoded "MKT" default, so every test invoice routed to MKT whatever it
    actually was: `test_the_budget_chain_matches_domain_budget` was passing
    because everything fell into one department, not because routing worked.
    Found 2026-08-01 when the fabricated default was removed.
    """
    return {
        "invoice_id": invoice_id,
        "source_path": f"corpus/{invoice_id}.xml",
        "source_type": "xml",
        "department": department,
        "extracted": invoice,
        "findings": [],
        "signals": [],
        "status": "",
    }


def test_invoices_are_persisted_in_canonical_order(runtime_conn, three_mkt_invoices):
    """Arrival order is shuffled; `sequence_no` must follow issue_date."""
    state = {
        "batch_id": str(uuid.uuid4()),
        "enriched": [_enriched(inv, iid) for iid, inv in three_mkt_invoices],
        "failed": [],
    }
    out = batch.serial_join(state, conn=runtime_conn)
    persisted_ids = [p["invoice_id"] for p in out["persisted"]]
    assert persisted_ids == ["INV-0003", "INV-0002", "INV-0001"]
    seqs = [(c["invoice_id"], c["sequence_no"]) for c in out["consumption"]]
    assert [s for _, s in seqs] == sorted(s for _, s in seqs)
    # canonical order is by issue_date, so the EARLIEST-dated invoice is #1
    assert seqs[0][0].endswith("0003")


def test_the_budget_chain_matches_domain_budget(runtime_conn, three_mkt_invoices):
    state = {
        "batch_id": str(uuid.uuid4()),
        "enriched": [_enriched(inv, iid) for iid, inv in three_mkt_invoices],
        "failed": [],
    }
    out = batch.serial_join(state, conn=runtime_conn)
    chain = [Decimal(c["balance_after"]) for c in out["consumption"]]
    assert chain == [Decimal("35000000"), Decimal("15000000"), Decimal("-5000000")]


def test_a_repeated_natural_key_is_a_duplicate_not_a_second_row(
    runtime_conn, three_mkt_invoices
):
    """`insert_with_lines` returns None on conflict — design §2.3's UNIQUE index
    IS the exact-duplicate detector, with no second implementation to disagree
    with it."""
    iid, inv = three_mkt_invoices[0]
    state = {
        "batch_id": str(uuid.uuid4()),
        "enriched": [_enriched(inv, iid), _enriched(inv, iid + "-copy")],
        "failed": [],
    }
    out = batch.serial_join(state, conn=runtime_conn)
    codes = [f["code"] for f in out.get("raised", [])]
    assert findings.DUPLICATE_INVOICE in codes


def test_vendor_profile_is_updated_here_not_in_the_fan_out(
    runtime_conn, three_mkt_invoices
):
    """Constraint 3. If this moves into the task, `signals` starts seeing the
    invoice it is scoring and PRICE_OUTLIER silently stops firing."""
    iid, inv = three_mkt_invoices[0]
    before = vendor_profile.stats_for(runtime_conn, inv.seller_tax_code)
    assert before is None or before.invoice_count == 0
    batch.serial_join(
        {"batch_id": str(uuid.uuid4()), "enriched": [_enriched(inv, iid)], "failed": []},
        conn=runtime_conn,
    )
    after = vendor_profile.stats_for(runtime_conn, inv.seller_tax_code)
    assert after.invoice_count == 1


def test_failed_invoices_are_not_persisted(runtime_conn):
    """A file that never parsed has no InvoiceData; there is nothing to persist
    beyond the batch counter (spec §8 open question 1)."""
    out = batch.serial_join(
        {
            "batch_id": str(uuid.uuid4()),
            "enriched": [],
            "failed": [{"invoice_id": "INV-BAD", "error": "boom"}],
        },
        conn=runtime_conn,
    )
    assert out["persisted"] == []
    assert out["consumption"] == []


def _state_with_one_invoice(
    conn, *, findings=None, signals=None, category=None, department="ADMIN"
) -> dict:
    """A minimal BatchState carrying exactly one enriched invoice."""
    import pathlib

    from invoice_agent.extraction.xml_parser import parse_invoice_xml

    data = parse_invoice_xml(
        pathlib.Path("tests/fixtures/real_samples/viettelpost_gtgt_2.1.0.xml").read_bytes()
    ).model_copy(update={"invoice_number": uuid.uuid4().hex[:8]})
    return {
        "batch_id": str(uuid.uuid4()),
        "inputs": [],
        "enriched": [
            {
                "invoice_id": "INV-TEST",
                "source_path": "x.xml",
                "source_type": "xml",
                "department": department,
                "extracted": data,
                "findings": findings or [],
                "signals": signals or [],
                "classification": None if category is None else _fake_classification(category, department),
                "status": "enriched",
            }
        ],
        "failed": [],
        "persisted": [],
        "consumption": [],
        "raised": [],
        "unroutable": [],
        "auto": [],
        "needs_approval": [],
        "status": "",
    }


def _fake_classification(category: str, department: str):
    """A stand-in for ClassificationResult that carries only the two fields
    `_join` reads. The real model is a frozen Literal-constrained pydantic
    model, and `OFFICE_REPAIR_MAINTENANCE` is deliberately NOT in its Literal —
    so the out-of-seed test could not construct one."""
    from types import SimpleNamespace

    return SimpleNamespace(category=category, department=department)


def test_validation_findings_reach_the_invoice_flags_table(owner_conn):
    """Gap 1. Before M5-A, batch.py raised flags ONLY for duplicates and budgets
    (batch.py:214 and :303 were the only callers), so every validation finding
    and every signal was discarded when the batch ended — and the
    forbid_auto_clean_with_open_flags trigger, the DATABASE layer of the safety
    property, fired on an almost-empty table."""
    from invoice_agent.db.repositories import flags
    from invoice_agent.domain import findings

    state = _state_with_one_invoice(
        owner_conn,
        findings=[findings.make(findings.ARITHMETIC_MISMATCH, expected="100", actual="110")],
        signals=[findings.make(findings.NEW_VENDOR, tax_code="0100000003")],
    )
    result = batch.serial_join(state, conn=owner_conn)

    db_id = uuid.UUID(result["persisted"][0]["db_id"])
    codes = {f["code"] for f in flags.open_flags_for(owner_conn, db_id)}
    assert findings.ARITHMETIC_MISMATCH in codes
    assert findings.NEW_VENDOR in codes


def test_the_classified_category_and_department_are_persisted(owner_conn):
    """Gap 2. insert_with_lines omitted both columns, so the classification
    result was discarded and auto_post had no category from which to pick TT133
    accounts."""
    from invoice_agent.db.repositories import invoices as invoices_repo

    state = _state_with_one_invoice(
        owner_conn, category="OFFICE_SUPPLIES", department="ADMIN"
    )
    result = batch.serial_join(state, conn=owner_conn)

    row = invoices_repo.get_by_id(owner_conn, uuid.UUID(result["persisted"][0]["db_id"]))
    assert row["category"] == "OFFICE_SUPPLIES"
    assert row["department"] == "ADMIN"


def test_a_category_outside_the_seed_lands_as_NULL_not_invented(owner_conn):
    """Spec §0 constraint 4. A model proposed OFFICE_REPAIR_MAINTENANCE live on
    2026-08-01 (agents/models.py). A category the seed does not know becomes
    NULL, which routes the invoice to a human — it is never coerced to a
    neighbouring category and never written raw, which would raise IntegrityError
    against the FK and kill the batch."""
    from invoice_agent.db.repositories import invoices as invoices_repo

    state = _state_with_one_invoice(owner_conn, category="OFFICE_REPAIR_MAINTENANCE")
    result = batch.serial_join(state, conn=owner_conn)

    row = invoices_repo.get_by_id(owner_conn, uuid.UUID(result["persisted"][0]["db_id"]))
    assert row["category"] is None


def test_status_reflects_whether_a_blocking_flag_persisted(owner_conn):
    """Gap 3. `set_status` had zero callers, so every invoice sat at 'extracted'
    forever and nothing downstream could tell a clean invoice from a flagged
    one."""
    from invoice_agent.db.repositories import invoices as invoices_repo
    from invoice_agent.domain import findings

    clean = _state_with_one_invoice(owner_conn)
    clean_result = batch.serial_join(clean, conn=owner_conn)
    clean_row = invoices_repo.get_by_id(
        owner_conn, uuid.UUID(clean_result["persisted"][0]["db_id"])
    )
    assert clean_row["status"] == "validated"

    dirty = _state_with_one_invoice(
        owner_conn,
        findings=[findings.make(findings.ARITHMETIC_MISMATCH, expected="1", actual="2")],
    )
    dirty_result = batch.serial_join(dirty, conn=owner_conn)
    dirty_row = invoices_repo.get_by_id(
        owner_conn, uuid.UUID(dirty_result["persisted"][0]["db_id"])
    )
    assert dirty_row["status"] == "flagged"


def test_an_info_only_invoice_is_validated_not_flagged(owner_conn):
    """NEW_VENDOR alone must not read as a defect — the same rule as the trigger
    and the guard, in a third place, so all three can be shown to agree."""
    from invoice_agent.db.repositories import invoices as invoices_repo
    from invoice_agent.domain import findings

    state = _state_with_one_invoice(
        owner_conn, signals=[findings.make(findings.NEW_VENDOR, tax_code="0100000003")]
    )
    result = batch.serial_join(state, conn=owner_conn)
    row = invoices_repo.get_by_id(owner_conn, uuid.UUID(result["persisted"][0]["db_id"]))
    assert row["status"] == "validated"

