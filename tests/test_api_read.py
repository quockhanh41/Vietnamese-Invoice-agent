"""Read-only queries and the routes that expose them.

Uses `committed_rows` + `_persisted_invoice` from tests/helpers/approval_fixtures.py
(registered as a pytest plugin in conftest.py), because these queries read
COMMITTED state — a fixture transaction the query's own connection cannot see
would make every count zero.
"""

import uuid
from decimal import Decimal

from tests.helpers.approval_fixtures import _persisted_invoice

from invoice_agent.db import queries
from invoice_agent.db.engine import get_engine


def test_list_invoices_filters_by_status_and_department(committed_rows):
    db_id, _ = _persisted_invoice(committed_rows, category="OFFICE_SUPPLIES")
    with get_engine().begin() as conn:
        all_rows = queries.list_invoices(conn)
        admin = queries.list_invoices(conn, department="ADMIN")
        nobody = queries.list_invoices(conn, department="MKT")

    assert db_id in [r["id"] for r in all_rows]
    assert db_id in [r["id"] for r in admin]
    assert db_id not in [r["id"] for r in nobody]


def test_list_invoices_respects_its_limit(committed_rows):
    for _ in range(3):
        _persisted_invoice(committed_rows)
    with get_engine().begin() as conn:
        assert len(queries.list_invoices(conn, limit=2)) == 2


def test_batch_summary_counts_from_the_database(committed_rows):
    """`GET /batches/{id}` answers from here, not from the in-process progress
    registry — spec §2.4. Every number is a COUNT against a table."""
    from invoice_agent.db.repositories import batches

    batch_id = uuid.uuid4()
    with get_engine().begin() as conn:
        batches.ensure_batch(conn, batch_id)
    db_id, _ = _persisted_invoice(committed_rows, batch_id=batch_id)

    with get_engine().begin() as conn:
        summary = queries.batch_summary(conn, batch_id)
    assert summary["persisted"] == 1
    assert summary["posted"] == 0
    assert summary["pending_approval"] == 1  # _persisted_invoice opens an approval
    assert summary["status"] == "running"


def test_batch_summary_is_none_for_an_unknown_batch():
    with get_engine().begin() as conn:
        assert queries.batch_summary(conn, uuid.uuid4()) is None


def test_budget_report_shows_consumed_against_limit(committed_rows):
    """Seeded MKT limit is 50,000,000 (scripts/seed_reference.BUDGETS)."""
    with get_engine().begin() as conn:
        rows = queries.budget_report(conn, period="2026-07")
    mkt = next(r for r in rows if r["department_code"] == "MKT")
    assert Decimal(mkt["amount_limit"]) == Decimal("50000000")
    assert Decimal(mkt["consumed"]) >= Decimal("0")


def test_flag_report_counts_by_code_and_severity(committed_rows):
    _persisted_invoice(committed_rows, flag=("PRICE_OUTLIER", "warn", "anomaly"))
    with get_engine().begin() as conn:
        rows = queries.flag_report(conn)
    outlier = next(r for r in rows if r["code"] == "PRICE_OUTLIER")
    assert outlier["severity"] == "warn"
    assert outlier["open_count"] >= 1


def test_cost_report_returns_both_real_and_shadow(committed_rows):
    """Shadow cost exists because cost_usd is $0 on free tier, which reads as
    'you did not measure' rather than 'it is cheap' (design §2.9)."""
    with get_engine().begin() as conn:
        report = queries.cost_report(conn)
    assert set(report) >= {"calls", "succeeded", "cost_usd", "shadow_cost_usd"}


# ---------------------------------------------------------------------------
# Task 2: create_app, /health (both branches), GuardDenied handler
# ---------------------------------------------------------------------------


def test_health_reports_ok_when_postgres_answers(requires_postgres):
    from fastapi.testclient import TestClient

    from invoice_agent.api.app import create_app

    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_unhealthy_when_postgres_is_gone(monkeypatch, requires_postgres):
    """A health check that does not touch the database reports healthy while the
    database is gone, which is worse than having no health check.
    Spec §6 criterion 5."""
    from fastapi.testclient import TestClient

    from invoice_agent.api import app as app_module
    from invoice_agent.api.routes import health as health_module

    def _explode():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(health_module, "get_engine", _explode)
    with TestClient(app_module.create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["database"] == "unreachable"


def test_a_guard_denial_is_409_not_500(requires_postgres):
    """The guard refusing is CORRECT behaviour. A 500 would read as a bug and
    bury a working safety control in the error logs."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from invoice_agent.api.app import install_error_handlers
    from invoice_agent.tools.guards import GuardDenied

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise GuardDenied("invoice has an open flag")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 409
    assert "open flag" in response.json()["detail"]

