"""`explain_flags` narrates deterministic facts. No model is involved.

The flags are computed by Python from database state — that is the project's
central safety property. Narrating them with an LLM would put a model back into
the explanation path for a decision it did not make, and a reviewer reading the
narrative would have no way to tell which parts were computed and which were
generated.

The narrative is VIETNAMESE: a human reads it, so CLAUDE.md's product-output
exception applies and it must not be translated.
"""

import pathlib
import uuid

from invoice_agent.domain import findings
from invoice_agent.tools import explain


def test_every_registered_flag_code_has_a_narrative_template():
    """A code with no template would render as a bare enum to an accountant."""
    missing = [code for code in findings.CODE_REGISTRY if code not in explain.TEMPLATES_VI]
    assert missing == [], f"flag codes with no Vietnamese narrative: {missing}"


def test_the_narrative_is_vietnamese_not_english():
    text = explain.render(findings.BUDGET_BREACH, {"department": "MKT", "over_by": "5000000"})
    assert any(ch in text for ch in "ăâđêôơư"), f"narrative looks untranslated: {text!r}"


def test_explaining_an_invoice_with_no_flags_says_so_rather_than_returning_empty(runtime_conn):
    result = explain.explain_flags(runtime_conn, uuid.uuid4())
    assert result.flags == []
    assert result.summary_vi != ""


def test_no_gateway_call_happens(monkeypatch, runtime_conn):
    """Mutation-proof: if someone later routes this through a model, this fails.

    Patches all three of `LLMGateway`'s entry points -- `generate`,
    `generate_with_model` (gateway.py:359) and `generate_stream` (gateway.py:511)
    -- not just `generate`, and calls the real `explain_flags(conn, invoice_id)`
    under the patch, not just `render()`. `render()` alone is a pure `str.format`
    that could never reach a gateway call regardless of what `explain_flags`
    itself does; the plausible regression this guards against is an
    LLM-generated `summary_vi` inside `explain_flags()`, which only calling the
    real function can catch.
    """
    from invoice_agent.llm import gateway as gw

    def _boom(*a, **k):
        raise AssertionError("explain_flags must not call an LLM")

    monkeypatch.setattr(gw.LLMGateway, "generate", _boom)
    monkeypatch.setattr(gw.LLMGateway, "generate_with_model", _boom)
    monkeypatch.setattr(gw.LLMGateway, "generate_stream", _boom)

    result = explain.explain_flags(runtime_conn, uuid.uuid4())
    assert result.summary_vi != ""


def test_explain_module_imports_no_llm_gateway():
    """Structural guard, mirroring `tests/test_ui_boundary.py`'s shape: not
    satisfiable by behaviour alone, since a behavioural test only proves the
    gateway wasn't CALLED, not that the import doesn't exist waiting to be used.

    This note's own record (docs/notes/2026-08-05-m7-mcp-surface.md §3) already
    asserted "nothing in `tools/explain.py` imports `invoice_agent.llm.gateway`
    at all" -- true, but with no test behind it until now.
    """
    source = pathlib.Path("src/invoice_agent/tools/explain.py").read_text(encoding="utf-8")
    assert "invoice_agent.llm" not in source, "tools/explain.py must not import the LLM gateway"


def test_render_with_a_missing_detail_key_renders_a_placeholder_not_a_crash():
    """A flag's `detail` may be partial or absent entirely (TAX_CODE_MISSING and
    NEW_VENDOR carry none at all). A template built with plain `str.format`
    would raise KeyError on the first flag whose detail does not have every key
    the template names; this project substitutes a placeholder instead, because
    a narrative that crashes in front of an accountant is worse than one that
    omits a number."""
    text = explain.render(findings.LINE_AMOUNT_MISMATCH, {"line_no": 3})
    assert explain.MISSING_DETAIL_VI in text
    assert "3" in text
