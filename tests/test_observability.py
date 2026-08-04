"""Tracing is decided at import time, so `_tracing_enabled` is tested as a
function rather than by reloading the module.

The ORDER of the two checks is the load-bearing part. `tests/conftest.py` keeps
the whole suite off the network by setting LANGFUSE_TRACING_ENABLED=false and
popping both keys; if the settings fallback were consulted before the
kill-switch, every test run would start firing traces at this developer's real
Langfuse project — the exact leak conftest was written to prevent.
"""

from invoice_agent import observability
from invoice_agent.config import Settings


def _settings_with_keys(**kw):
    return Settings(
        _env_file=None, langfuse_public_key="pk", langfuse_secret_key="sk", **kw
    )


def test_kill_switch_wins_over_keys_configured_in_dotenv(monkeypatch):
    """LANGFUSE_TRACING_ENABLED=false must win even when `.env` has a real pair."""
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(observability, "get_settings", _settings_with_keys)

    assert observability._tracing_enabled() is False


def test_keys_in_settings_alone_enable_tracing(monkeypatch):
    """The defect this task fixes: a pair in `.env` never reaches os.environ."""
    monkeypatch.delenv("LANGFUSE_TRACING_ENABLED", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(observability, "get_settings", _settings_with_keys)

    assert observability._tracing_enabled() is True


def test_environment_still_enables_tracing_without_settings(monkeypatch):
    monkeypatch.delenv("LANGFUSE_TRACING_ENABLED", raising=False)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr(
        observability, "get_settings", lambda: Settings(_env_file=None)
    )

    assert observability._tracing_enabled() is True


def test_no_keys_anywhere_means_no_tracing(monkeypatch):
    monkeypatch.delenv("LANGFUSE_TRACING_ENABLED", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(
        observability, "get_settings", lambda: Settings(_env_file=None)
    )

    assert observability._tracing_enabled() is False


def test_settings_failure_does_not_break_the_import(monkeypatch):
    """Telemetry that can kill a run is worse than telemetry that under-reports
    — the rule `gateway._count_tokens` already follows."""
    monkeypatch.delenv("LANGFUSE_TRACING_ENABLED", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    def _boom():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(observability, "get_settings", _boom)

    assert observability._tracing_enabled() is False
