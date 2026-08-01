"""Invoice persistence — the identity model (design doc §2.3).

Re-uploading the same file is IDEMPOTENT, not an error: `insert_row` returns
None on conflict rather than raising, and the caller routes that to duplicate
handling. Verified 2026-07-28 that ON CONFLICT DO NOTHING ... RETURNING yields
zero rows without raising.
"""

import uuid
from typing import Any

from sqlalchemy import Connection, func, select
from sqlalchemy.dialects.postgresql import insert

from invoice_agent.db import models
from invoice_agent.db.repositories import reference, vendors
from invoice_agent.extraction.models import InvoiceData


class MissingSellerTaxCode(ValueError):
    """An invoice with no seller MST cannot be given an identity.

    Measured 2026-07-28: seller MST is present in 8/8 real fixtures, so this is
    an exceptional document. Design doc §3.3 routes it to manual_queue; storing
    it with a null identity would put it outside the natural key and make
    duplicate detection meaningless for it.
    """


def insert_row(conn: Connection, values: dict[str, Any]) -> uuid.UUID | None:
    """Insert one invoice row. Returns None if the natural key already exists."""
    stmt = (
        insert(models.invoices)
        .values(**values)
        .on_conflict_do_nothing(constraint="uq_invoices_natural_key")
        .returning(models.invoices.c.id)
    )
    return conn.execute(stmt).scalar_one_or_none()


def insert_with_lines(
    conn: Connection,
    data: InvoiceData,
    *,
    batch_id: uuid.UUID | None = None,
    source_type: str = "xml",
    source_path: str | None = None,
    raw_xml: str | None = None,
    category: str | None = None,
    department: str | None = None,
) -> uuid.UUID | None:
    """Persist an InvoiceData plus its lines. Returns None if it is a duplicate."""
    if not data.seller_tax_code:
        raise MissingSellerTaxCode(
            f"{data.invoice_series}/{data.invoice_number}: no seller tax code; route to manual_queue"
        )

    # `category` and `department` are FKs to the seeded reference tables, so a
    # value the model invented cannot be written at all. The lookup exists so the
    # batch gets a routable invoice instead of an IntegrityError — measured
    # 2026-08-01: a live gemini-3.5-flash verdict proposed
    # OFFICE_REPAIR_MAINTENANCE, a category no seed row carries
    # (agents/models.py). NULL routes the invoice to a human; nothing is
    # coerced to a neighbouring category.
    if category is not None and reference.get_category(conn, category) is None:
        category = None
    if department is not None and reference.get_department(conn, department) is None:
        department = None

    vendors.ensure_vendor(conn, data.seller_tax_code, name=data.seller_name)

    invoice_id = insert_row(
        conn,
        {
            "id": uuid.uuid4(),
            "batch_id": batch_id,
            "template_no": data.template_no,
            "invoice_series": data.invoice_series,
            "invoice_number": data.invoice_number,
            "seller_tax_code": data.seller_tax_code,
            "buyer_tax_code": data.buyer_tax_code,
            "issue_date": data.issue_date,
            "currency": data.currency,
            "exchange_rate": data.exchange_rate,
            "subtotal": data.subtotal,
            "vat_amount": data.vat_amount,
            "total_amount": data.total_amount,
            "total_in_words": data.total_in_words,
            "ext": data.ext,
            "source_type": source_type,
            "source_path": source_path,
            "raw_xml": raw_xml,
            "category": category,
            "department": department,
            "status": "extracted",
        },
    )
    if invoice_id is None:
        return None

    if data.lines:
        conn.execute(
            models.invoice_line_items.insert(),
            [
                {
                    "invoice_id": invoice_id,
                    # Document position when STT is absent — see the column comment.
                    "line_no": line.line_no if line.line_no is not None else position,
                    "description": line.description,
                    "unit": line.unit,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "amount": line.amount,
                    "vat_rate": line.vat_rate,
                    "vat_amount": line.vat_amount,
                    "item_kind": line.item_kind,
                    "ext": line.ext,
                }
                for position, line in enumerate(data.lines, start=1)
            ],
        )
    return invoice_id


def get_by_id(conn: Connection, invoice_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        conn.execute(select(models.invoices).where(models.invoices.c.id == invoice_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def get_by_natural_key(
    conn: Connection,
    seller_tax_code: str,
    template_no: str,
    invoice_series: str,
    invoice_number: str,
) -> dict[str, Any] | None:
    row = (
        conn.execute(
            select(models.invoices).where(
                models.invoices.c.seller_tax_code == seller_tax_code,
                models.invoices.c.template_no == template_no,
                models.invoices.c.invoice_series == invoice_series,
                models.invoices.c.invoice_number == invoice_number,
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def get_lines(conn: Connection, invoice_id: uuid.UUID) -> list[dict[str, Any]]:
    stmt = (
        select(models.invoice_line_items)
        .where(models.invoice_line_items.c.invoice_id == invoice_id)
        .order_by(models.invoice_line_items.c.line_no)
    )
    return [dict(r) for r in conn.execute(stmt).mappings()]


def set_status(conn: Connection, invoice_id: uuid.UUID, status: str) -> None:
    conn.execute(
        models.invoices.update()
        .where(models.invoices.c.id == invoice_id)
        .values(status=status, updated_at=func.now())
    )
