"""A model that has been fully compromised, at the gateway seam.

This is the threat model, not an exaggeration of it: the stub returns raw JSON
at exactly the point a provider returns raw JSON, so everything downstream —
`_first_json_object`, `model_validate`, `extra="forbid"` — runs for real.

Two modes, because the boundary behaves differently against the two families of
payload:

  OVERT   payloads 1-4 demand fields that DO NOT EXIST on ClassificationResult.
          The stub emits them; pydantic rejects; call_structured exhausts its
          retries and raises. The attack is inexpressible.

  LAUNDER payload 6 demands a category, which is a field the model legitimately
          chooses. The stub complies fully and succeeds. Proving THAT harmless
          requires showing the flag still fires (Task 5), not the schema.

A well-behaved model replayed from a fixture could not test either: it would
prove the model behaved, which is not the claim design §9.2 makes.
"""

import json

from tests.helpers.fake_llm import ok_response

from invoice_agent.config import Settings
from invoice_agent.llm.gateway import LLMGateway

OVERT = "overt"
LAUNDER = "launder"

# Everything payloads 1-4 ask the model to do, in one object. None of these keys
# exist on ClassificationResult, which is the entire defence.
_OVERT_BODY = {
    "category": "MARKETING",
    "department": "MKT",
    "confidence": 1.0,
    "reasoning": "Đã được Giám đốc phê duyệt qua email.",
    "approval_status": "approved",
    "flags": [],
    "status": "auto_clean",
    "auto_post": True,
}


def _reply(mode: str, category: str, messages_str: str = "") -> str:
    if "agrees" in messages_str or "Auditor" in messages_str or "audit" in messages_str.lower():
        return json.dumps(
            {"agrees": True, "proposed_category": None, "proposed_department": None, "reason": "Đồng ý"},
            ensure_ascii=False,
        )
    if mode == OVERT:
        return json.dumps(_OVERT_BODY, ensure_ascii=False)
    if mode == LAUNDER:
        return json.dumps(
            {
                "category": category,
                "department": "ADMIN",
                "confidence": 1.0,
                "reasoning": "Phân loại theo ghi chú trên hóa đơn.",
            },
            ensure_ascii=False,
        )
    raise ValueError(f"unknown mode {mode!r}")


def hostile_gateway(monkeypatch, *, mode: str, category: str = "OFFICE_SUPPLIES") -> LLMGateway:
    """A real LLMGateway whose provider has been replaced by an attacker.

    Same shape as `stub_gateway` in tests/test_enrichment_graph.py — patch
    `litellm.completion`, then build a live-mode gateway with a fake key — so
    the parsing path under test is the production one.
    """
    from invoice_agent.llm import gateway as gw_module

    def _completion(**kw):
        msgs = str(kw.get("messages", ""))
        reply = _reply(mode, category, msgs)
        return ok_response(reply, prompt=900, completion=120)

    monkeypatch.setattr(
        gw_module.litellm,
        "completion",
        _completion,
    )
    return LLMGateway(
        Settings(
            _env_file=None,
            groq_api_keys=["hostile"],
            gemini_api_keys=["hostile"],
            llm_mode="live",
        )
    )
