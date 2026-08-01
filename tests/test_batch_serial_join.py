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
