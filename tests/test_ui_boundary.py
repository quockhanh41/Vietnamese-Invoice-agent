"""Structural guards. Neither is satisfiable by behaviour.

A UI that imported `submit_decision` directly would produce identical
screenshots and identical database state — only an import check can see that
M5-C's API was bypassed.
"""

import pathlib

UI = pathlib.Path("src/invoice_agent/ui")

# Everything the UI must reach over HTTP instead. `invoice_agent.config` is
# absent on purpose: it is the ONE allowed import (spec §2.1), because reading an
# env var directly would reintroduce the scattered-os.getenv debt M0.2 paid off.
FORBIDDEN = (
    "invoice_agent.db",
    "invoice_agent.graphs",
    "invoice_agent.tools",
    "invoice_agent.domain",
    "invoice_agent.llm",
    "invoice_agent.memory",
    "invoice_agent.extraction",
)


def test_ui_talks_only_over_http():
    """THE POINT OF THIS MILESTONE. Streamlit is Python, so importing
    `submit_decision` directly would work and would be shorter — and M5-C's only
    consumer would bypass the API, leaving it dead code with a green suite."""
    offenders = []
    for path in UI.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for module in FORBIDDEN:
            if module in source:
                offenders.append(f"{path.name}: imports {module}")
    assert not offenders, (
        "the UI must reach the system over HTTP only: " + "; ".join(offenders)
    )


def test_app_makes_no_http_call_of_its_own():
    """Streamlit reruns this script on every interaction, so anything in it is
    unreachable by a unit test. Keeping HTTP in client.py is what makes every
    request path testable; without this guard the logic leaks back one call at a
    time and the split becomes decorative."""
    source = (UI / "app.py").read_text(encoding="utf-8")
    for forbidden in ("httpx.", "requests.", "urllib"):
        assert forbidden not in source, f"app.py makes its own HTTP call: {forbidden!r}"


def test_the_ui_imports_config_and_nothing_else_from_the_package():
    """The allowlist stated positively, so a NEW forbidden module added to
    `invoice_agent/` later is caught even though FORBIDDEN above does not list
    it."""
    import re

    allowed = {"invoice_agent.config", "invoice_agent.ui"}
    found: set[str] = set()
    for path in UI.rglob("*.py"):
        for match in re.finditer(
            r"(?:from|import)\s+(invoice_agent(?:\.[a-z_]+)*)", path.read_text(encoding="utf-8")
        ):
            found.add(match.group(1))
    extra = {m for m in found if not any(m.startswith(a) for a in allowed)}
    assert not extra, f"the UI may import only {sorted(allowed)}; found {sorted(extra)}"
