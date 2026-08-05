"""`explain_flags` narrates deterministic facts. No model is involved.

The flags are computed by Python from database state — that is the project's
central safety property. Narrating them with an LLM would put a model back into
the explanation path for a decision it did not make, and a reviewer reading the
narrative would have no way to tell which parts were computed and which were
generated.

The narrative is VIETNAMESE: a human reads it, so CLAUDE.md's product-output
exception applies and it must not be translated.
"""

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


def test_no_gateway_call_happens(monkeypatch):
    """Mutation-proof: if someone later routes this through a model, this fails."""
    from invoice_agent.llm import gateway as gw

    def _boom(*a, **k):
        raise AssertionError("explain_flags must not call an LLM")

    monkeypatch.setattr(gw.LLMGateway, "generate", _boom)
    text = explain.render(findings.NEW_VENDOR, {})
    assert text


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
