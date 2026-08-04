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


def test_vision_self_correction_records_every_attempt(runtime_conn, monkeypatch):
    """Spec M3-B §7 criterion 5, which no test covered until now.

    A vision extraction that fails schema validation twice and succeeds on the
    third attempt must leave THREE `llm_calls` rows. `test_three_attempts_
    record_three_calls` asserts an in-memory list against a toy model; nothing
    drove a real rasterize-and-extract into the database. Found 2026-07-29 by
    the M3-B exit review.
    """
    import json

    from scripts.generator import pdf as pdf_gen
    from tests.helpers.fake_llm import ok_response
    from tests.helpers.invoice_specs import make_spec

    from invoice_agent.config import Settings
    from invoice_agent.extraction import vision
    from invoice_agent.llm import gateway as gw_module
    from invoice_agent.llm.gateway import LLMGateway

    good = json.dumps(
        {
            "template_no": "1",
            "invoice_series": "C26TAA",
            "invoice_number": "00000123",
            "issue_date": "2026-07-01",
            "total_amount": "1290107.52",
            "lines": [],
        }
    )
    # Two failures the loop must distinguish: a schema violation, then
    # unparseable output. Both are retryable; neither is a transport failure.
    replies = ['{"total_amount": "không đọc được"}', "đây không phải JSON", good]

    seen: list[int] = []

    def counting_completion(**kwargs):
        response = ok_response(replies[len(seen)], prompt=900, completion=120)
        seen.append(1)
        return response

    monkeypatch.setattr(gw_module.litellm, "completion", counting_completion)
    gateway = LLMGateway(
        Settings(
            _env_file=None,
            gemini_api_keys=["gem1"],
            vision_model_chain=["gemini/gemini-3.5-flash"],
            # Pinned, as make_gateway() does: without it the ambient
            # LLM_MODE=replay leaks in under the gate command and this looks
            # for a fixture instead of exercising the recording path.
            llm_mode="live",
        )
    )

    calls: list[LLMCallRecord] = []
    data = vision.extract(
        pdf_gen.render_pdf(make_spec()), gateway=gateway, calls=calls
    )
    assert data.total_amount == Decimal("1290107.52")
    assert len(calls) == 3, "each self-correction attempt is a billed LLM call"

    invoice_id = _make_invoice(runtime_conn)
    telemetry.record(runtime_conn, calls, node="vision_extract", invoice_id=invoice_id)

    rows = sorted(_rows_for(runtime_conn, invoice_id), key=lambda r: r["attempt"])
    assert len(rows) == 3
    assert all(r["node"] == "vision_extract" for r in rows)
    assert all(r["prompt_tokens"] == 900 for r in rows), "every attempt sent the image"
    assert all(r["shadow_cost_usd"] > Decimal(0) for r in rows)


def test_current_trace_id_is_none_when_tracing_is_off(monkeypatch):
    from invoice_agent import observability

    monkeypatch.setattr(observability, "TRACING_ENABLED", False)
    assert observability.current_trace_id() is None


def test_current_trace_id_never_raises(monkeypatch):
    """Telemetry that can kill an invoice run is worse than telemetry that
    under-reports — the rule gateway._count_tokens already follows."""
    from invoice_agent import observability

    monkeypatch.setattr(observability, "TRACING_ENABLED", True)
    monkeypatch.setattr(
        observability, "_langfuse_client", lambda: (_ for _ in ()).throw(RuntimeError("no client"))
    )
    assert observability.current_trace_id() is None


def test_persist_cost_writes_the_active_trace_id(runtime_conn, monkeypatch):
    """The `trace_id` column gets its first writer. Monkeypatching the source
    keeps this runnable on a keyless CI while still proving the value is
    threaded from observability through telemetry into the row.

    `_persist_cost` opens its OWN `get_engine().begin()` and commits (spec
    §5.3's "write the moment the tokens are spent" — see its docstring), which
    bypasses `runtime_conn`'s rollback-on-teardown. The `finally` below deletes
    by this test's own `thread_id` so the row does not permanently pollute
    `app.llm_calls` — a later M6-B task counts rows in this table to prove
    model attribution, and an orphan row here would silently contaminate that
    count. Found by review 2026-08-04.

    Cleanup runs on `get_owner_engine()`, not `get_engine()`: migration
    `b25d8a939562_m2a_09_consumption_episodic_llm.py` grants the runtime role
    (`invoice_app`, what `_persist_cost` itself writes through) only SELECT,
    INSERT, UPDATE on `llm_calls` — no DELETE. That is a deliberate
    immutability property of the runtime role, the same one `owner_conn`
    exists to bypass for admin-only operations like `_reset_db_state`'s
    TRUNCATE. Confirmed empirically 2026-08-04: `get_engine()` here raises
    `psycopg.errors.InsufficientPrivilege`.
    """
    import uuid

    from sqlalchemy import text

    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.graphs import enrichment
    from invoice_agent.llm.cost import LLMCallRecord

    invoice_id = str(uuid.uuid4())
    thread_id = f"enrich:{invoice_id}"
    monkeypatch.setattr(enrichment, "current_trace_id", lambda: "trace-abc-123")

    try:
        enrichment._persist_cost(
            [
                LLMCallRecord(
                    model="groq/llama-3.3-70b-versatile",
                    provider="groq",
                    prompt_tokens=10,
                    completion_tokens=5,
                    latency_ms=1,
                    attempt=1,
                    succeeded=True,
                    error=None,
                )
            ],
            invoice_id=invoice_id,
            node="classify",
        )

        row = runtime_conn.execute(
            text("SELECT trace_id FROM app.llm_calls WHERE thread_id = :t"),
            {"t": thread_id},
        ).scalar_one()
        assert row == "trace-abc-123"
    finally:
        with get_owner_engine().begin() as cleanup_conn:
            cleanup_conn.execute(
                text("DELETE FROM app.llm_calls WHERE thread_id = :t"),
                {"t": thread_id},
            )


def test_persist_cost_reports_both_money_figures_to_the_trace(runtime_conn, monkeypatch):
    """$0 actual on free tier reads as "you did not measure" without the shadow
    figure beside it — design §2.9's whole reason for two columns.

    Same rollback-bypass as `test_persist_cost_writes_the_active_trace_id`
    above: `_persist_cost` commits on its own connection, so the row must be
    deleted explicitly in `finally` rather than relying on `runtime_conn`'s
    teardown rollback — and the delete must run as the owner role, because
    the runtime role has no DELETE grant on `llm_calls` (see the sibling
    test's docstring for the migration reference).
    """
    import uuid

    from sqlalchemy import text

    from invoice_agent.db.engine import get_owner_engine
    from invoice_agent.graphs import enrichment
    from invoice_agent.llm.cost import LLMCallRecord

    invoice_id = str(uuid.uuid4())
    thread_id = f"enrich:{invoice_id}"
    seen = []
    monkeypatch.setattr(enrichment, "tag_current_span", lambda **kw: seen.append(kw))
    monkeypatch.setattr(enrichment, "current_trace_id", lambda: None)

    try:
        enrichment._persist_cost(
            [
                LLMCallRecord(
                    model="groq/llama-3.3-70b-versatile", provider="groq",
                    prompt_tokens=1000, completion_tokens=200, latency_ms=1,
                    attempt=1, succeeded=True, error=None,
                )
            ],
            invoice_id=invoice_id,
            node="classify",
        )

        assert len(seen) == 1
        # actual_cost_usd's Decimal quantisation for the free tier prints as "0",
        # not "0.000000" — measured 2026-08-04 running this test directly.
        assert seen[0]["cost_usd"] == "0"               # free tier
        assert Decimal(seen[0]["shadow_cost_usd"]) > 0  # priced at gpt-4o-mini
    finally:
        with get_owner_engine().begin() as cleanup_conn:
            cleanup_conn.execute(
                text("DELETE FROM app.llm_calls WHERE thread_id = :t"),
                {"t": thread_id},
            )
