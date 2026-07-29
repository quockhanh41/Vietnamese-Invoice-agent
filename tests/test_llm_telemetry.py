"""Cost reaching the database — the recording half of the M3 gate.

Runs as the RUNTIME role, like tests/test_repositories.py: running as the owner
would hide a missing GRANT, and migration 10 adds columns to a granted table.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from invoice_agent.db import models
from invoice_agent.db.repositories import invoices as invoice_repo
from invoice_agent.db.repositories import vendors
from invoice_agent.llm import telemetry
from invoice_agent.llm.cost import LLMCallRecord


def _make_invoice(conn) -> uuid.UUID:
    tax_code = f"ZZ{uuid.uuid4().hex[:8]}"
    vendors.ensure_vendor(conn, tax_code, name="Nhà cung cấp thử nghiệm")
    invoice_id = uuid.uuid4()
    invoice_repo.insert_row(
        conn,
        {
            "id": invoice_id,
            "template_no": "1",
            "invoice_series": "C26TAA",
            "invoice_number": uuid.uuid4().hex[:8],
            "seller_tax_code": tax_code,
            "issue_date": date(2026, 7, 1),
            "total_amount": Decimal("1000000"),
            "source_type": "xml",
            "status": "received",
        },
    )
    return invoice_id


def _rows_for(conn, invoice_id):
    return conn.execute(
        select(models.llm_calls).where(models.llm_calls.c.invoice_id == invoice_id)
    ).mappings().all()


def test_one_invoice_end_to_end_has_its_cost_recorded(runtime_conn):
    """The M3 gate sentence, in one assertion."""
    invoice_id = _make_invoice(runtime_conn)
    telemetry.record(
        runtime_conn,
        [
            LLMCallRecord(
                model="groq/llama-3.3-70b-versatile", provider="groq",
                prompt_tokens=1000, completion_tokens=200,
                latency_ms=430, attempt=1, succeeded=True,
            )
        ],
        node="vision_extract",
        invoice_id=invoice_id,
        thread_id=f"enrich:{invoice_id}",
        agent="VisionExtractor",
    )
    rows = _rows_for(runtime_conn, invoice_id)
    assert len(rows) == 1
    assert rows[0]["node"] == "vision_extract"
    assert rows[0]["agent"] == "VisionExtractor"
    assert rows[0]["thread_id"] == f"enrich:{invoice_id}"


def test_free_tier_records_zero_cost_and_a_non_zero_shadow(runtime_conn):
    """A bare $0 reads as 'unmeasured'. The pair reads as 'cheap'."""
    invoice_id = _make_invoice(runtime_conn)
    telemetry.record(
        runtime_conn,
        [
            LLMCallRecord(
                model="groq/llama-3.3-70b-versatile", provider="groq",
                prompt_tokens=1000, completion_tokens=200,
                latency_ms=430, attempt=1, succeeded=True,
            )
        ],
        node="classify",
        invoice_id=invoice_id,
    )
    row = _rows_for(runtime_conn, invoice_id)[0]
    assert row["cost_usd"] == Decimal(0)
    assert row["shadow_cost_usd"] == Decimal("0.00027")


def test_a_failed_attempt_is_written_too(runtime_conn):
    """§2.9's first easily-missed point: a 429 burned the input tokens."""
    invoice_id = _make_invoice(runtime_conn)
    telemetry.record(
        runtime_conn,
        [
            LLMCallRecord(
                model="groq/llama-3.3-70b-versatile", provider="groq",
                prompt_tokens=1000, completion_tokens=0, latency_ms=90,
                attempt=1, succeeded=False, error="RateLimitError: 429 quota",
            ),
            LLMCallRecord(
                model="gemini/gemini-3.5-flash", provider="gemini",
                prompt_tokens=1000, completion_tokens=200, latency_ms=380,
                attempt=2, succeeded=True,
            ),
        ],
        node="classify",
        invoice_id=invoice_id,
    )
    rows = sorted(_rows_for(runtime_conn, invoice_id), key=lambda r: r["attempt"])
    assert [r["succeeded"] for r in rows] == [False, True]
    assert rows[0]["prompt_tokens"] == 1000, "the failed attempt burned these"
    assert "429" in rows[0]["error"]


def test_context_columns_are_optional(runtime_conn):
    """A call from scripts/run_one.py has no invoice yet."""
    ids = telemetry.record(
        runtime_conn,
        [
            LLMCallRecord(
                model="openai/gpt-4o-mini", provider="openai",
                prompt_tokens=10, completion_tokens=2, latency_ms=50,
                attempt=1, succeeded=True,
            )
        ],
        node="eval_harness",
    )
    assert len(ids) == 1


def test_gateway_records_reach_the_database_unchanged(runtime_conn, monkeypatch):
    """Spec §7 criterion 3, which no single test covered until now.

    The gateway half asserted three RECORDS and the telemetry half asserted two
    ROWS; nothing drove one into the other. Found 2026-07-29 by review. A
    stubbed fallback — two rate-limited keys, then a success — must land as
    THREE rows, two of them failed with non-zero prompt_tokens.
    """
    from tests.helpers.fake_llm import ok_response

    from invoice_agent.config import Settings
    from invoice_agent.llm import gateway as gw_module
    from invoice_agent.llm.gateway import LLMGateway

    attempts: list[str] = []

    def fake_completion(**kwargs):
        attempts.append(kwargs["api_key"])
        if len(attempts) <= 2:
            raise RuntimeError("429 rate limit exceeded")
        return ok_response("VĂN PHÒNG PHẨM", prompt=1000, completion=200)

    monkeypatch.setattr(gw_module.litellm, "completion", fake_completion)
    gateway = LLMGateway(
        Settings(
            _env_file=None,
            groq_api_keys=["gk1", "gk2", "gk3"],
            llm_model_chain=["groq/llama-3.3-70b-versatile"],
            # Pinned, exactly as make_gateway() in tests/test_gateway.py pins it.
            # Without this the ambient LLM_MODE leaks in, and under the gate
            # command (`LLM_MODE=replay …`) this test would look for a fixture
            # instead of exercising the recording path it exists to test.
            llm_mode="live",
        )
    )
    calls: list[LLMCallRecord] = []
    gateway.generate("Hóa đơn giá trị gia tăng số 00000123", calls=calls)

    invoice_id = _make_invoice(runtime_conn)
    telemetry.record(runtime_conn, calls, node="classify", invoice_id=invoice_id)

    rows = sorted(_rows_for(runtime_conn, invoice_id), key=lambda r: r["attempt"])
    assert len(rows) == 3
    assert [r["succeeded"] for r in rows] == [False, False, True]
    assert all(r["prompt_tokens"] > 0 for r in rows), "failed attempts burned input tokens"
    assert rows[0]["completion_tokens"] == 0
    # Free tier: paid nothing, and the reference price says what that is worth.
    assert rows[2]["cost_usd"] == Decimal(0)
    assert rows[2]["shadow_cost_usd"] == Decimal("0.00027")
