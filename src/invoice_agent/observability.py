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
reading `os.environ` only, which meant tracing was off in every process this
project ever ran.

Related env vars (documented, not read here): LANGFUSE_HOST,
LANGFUSE_TRACING_ENVIRONMENT (tagging only — never a kill-switch).
"""

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
