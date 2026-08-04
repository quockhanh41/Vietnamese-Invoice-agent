"""Env-gated Langfuse tracing (M4.3, wired for real at M6-B).

Single import point for observability. When LANGFUSE keys are available — from
the environment **or** from `Settings`, i.e. `.env` — and the official
kill-switch (LANGFUSE_TRACING_ENABLED=false) is not set, re-exports the real
langfuse @observe decorator and registers LiteLLM's langfuse_otel callback
(OTel-based integration recommended for SDK v3 — generations auto-nest under
the active @observe span via the shared OpenTelemetry context, including failed
429/fallback attempts). Otherwise @observe is a zero-overhead identity
decorator and langfuse is never imported.

⚠️ This file is NO LONGER the verbatim Project A copy M0.4 recorded, and the
reason is in `_tracing_enabled` below: verbatim is precisely what kept it
reading `os.environ` only, which meant whether tracing was on was decided by
import order, not configuration — measured 2026-08-04 with a valid key pair
in `.env`: `api.app`, `graphs.batch`, and `graphs.approval` all measured
False, `graphs.enrichment` measured True. Reading `Settings` makes it
order-independent.

Related env vars (documented, not read here): LANGFUSE_HOST,
LANGFUSE_TRACING_ENVIRONMENT (tagging only — never a kill-switch).
"""

import functools
import os

from invoice_agent.config import get_settings


def _tracing_enabled() -> bool:
    # The kill-switch is checked FIRST and unconditionally. tests/conftest.py
    # relies on it to keep the whole suite off the network; consulting settings
    # before it would fire every test run's traces at the developer's real
    # Langfuse project — the exact leak that file was written to prevent.
    if os.getenv("LANGFUSE_TRACING_ENABLED", "true").strip().lower() == "false":
        return False
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        return True
    # A key configured in `.env` reaches os.environ only by accident: nothing
    # here calls load_dotenv(), but `litellm` does on import, so the two checks
    # above were decided by IMPORT ORDER. Measured 2026-08-04 with a valid pair
    # in `.env` — TRACING_ENABLED via `api.app` False, via `graphs.batch` False,
    # via `graphs.approval` False, via `graphs.enrichment` True. Reading
    # Settings makes it order-independent; all four are True now. Full
    # measurement in
    # docs/superpowers/specs/2026-08-04-m6b-observability-cost-cap-design.md §1.1.
    #
    # Same defect class as gateway.py, which M0.4 already ported to take
    # Settings instead of calling os.getenv itself.
    try:
        settings = get_settings()
    except Exception:
        # Telemetry must never be the thing that kills a run — the rule
        # gateway._count_tokens already follows.
        return False
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


TRACING_ENABLED = _tracing_enabled()

if TRACING_ENABLED:
    import litellm
    from langfuse import observe  # noqa: F401  (re-export)

    if "langfuse_otel" not in litellm.callbacks:
        litellm.callbacks.append("langfuse_otel")
else:

    def observe(*args, **kwargs):
        """Identity no-op decorator: supports @observe and @observe(...)."""
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]

        def _identity(fn):
            return fn

        return _identity


TRACED_ATTR = "__invoice_agent_traced__"


def _langfuse_client():
    from langfuse import get_client

    return get_client()


def current_trace_id() -> str | None:
    """The active Langfuse trace, for `app.llm_calls.trace_id`.

    Returns None rather than raising: a cost row is written on the hot path of
    every LLM call, and telemetry must never be able to kill an invoice run.
    """
    if not TRACING_ENABLED:
        return None
    try:
        return _langfuse_client().get_current_trace_id()
    except Exception:
        return None


def tag_current_span(**fields) -> None:
    """Attach per-call identifiers to the active span.

    No-ops when tracing is off, and never raises: this runs on the hot path of
    every node, and observability must not be able to fail an invoice run.
    """
    if not TRACING_ENABLED:
        return
    known = {k: v for k, v in fields.items() if v is not None}
    if not known:
        return
    try:
        _langfuse_client().update_current_span(metadata=known)
    except Exception:
        return


def traced(*, node: str, agent: str | None = None):
    """Mark a graph node as traced, and trace it.

    The marker is set whether or not tracing is enabled. CI has no Langfuse
    keys, so `observe` is the identity decorator there, and a structural test
    that could only see the real decorator would pass on an unwired codebase.
    """

    def decorate(fn):
        @functools.wraps(fn)
        def tag_then_run(*args, **kwargs):
            state = args[0] if args else None
            if isinstance(state, dict):
                tag_current_span(
                    node=node,
                    agent=agent,
                    invoice_id=state.get("invoice_id"),
                    batch_id=state.get("batch_id"),
                )
            return fn(*args, **kwargs)

        wrapped = observe(name=node)(tag_then_run)
        setattr(wrapped, TRACED_ATTR, {"node": node, "agent": agent})
        return wrapped

    return decorate


def tracing_marker(fn) -> dict | None:
    """The marker `traced` left, or None. Used by tests/test_node_tracing.py."""
    return getattr(fn, TRACED_ATTR, None)
