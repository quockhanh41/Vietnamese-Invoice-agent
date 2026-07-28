"""LLMGateway provider/key routing — không gọi mạng.

Port từ Project A `tests/test_gateway.py`. Khác một điểm: gateway ở đây nhận
`Settings` thay vì tự đọc `os.getenv`, nên test dựng Settings tường minh —
hermetic thật sự, không còn phải `monkeypatch.delenv` để chống `.env` rò vào.
"""

import pytest

from invoice_agent.config import Settings
from invoice_agent.llm.gateway import LLMGateway


def make_gateway(**overrides) -> LLMGateway:
    defaults = {
        "groq_api_keys": ["gk1", "gk2"],
        "gemini_api_keys": ["gem1"],
        "openrouter_api_keys": ["or1"],
        "openai_api_key": "",
    }
    return LLMGateway(Settings(_env_file=None, **{**defaults, **overrides}))


@pytest.fixture
def gateway() -> LLMGateway:
    return make_gateway()


class TestKeysFor:
    def test_groq_models_rotate_all_groq_keys(self, gateway):
        assert gateway._keys_for("groq/qwen/qwen3-32b") == ["gk1", "gk2"]

    def test_gemini_model_uses_gemini_key(self, gateway):
        assert gateway._keys_for("gemini/gemini-3.5-flash") == ["gem1"]

    def test_openrouter_model_uses_openrouter_key(self, gateway):
        assert gateway._keys_for("openrouter/qwen/qwen3-next-80b-a3b-instruct:free") == ["or1"]

    def test_openai_model_uses_openai_key(self):
        gw = make_gateway(openai_api_key="oa1")
        assert gw._keys_for("openai/gpt-4o-mini") == ["oa1"]

    def test_openrouter_model_names_do_not_false_match_other_providers(self, gateway):
        # "openrouter/openai/gpt-oss-120b:free" là model OPENROUTER — substring
        # "openai" không được route nó sang key OpenAI (đang rỗng).
        assert gateway._keys_for("openrouter/openai/gpt-oss-120b:free") == ["or1"]


class TestUsableModels:
    def test_keyless_providers_are_skipped(self):
        gw = make_gateway(gemini_api_keys=[], openrouter_api_keys=[])
        chain = [
            "groq/qwen/qwen3-32b",
            "gemini/gemini-3.5-flash",
            "openrouter/meta-llama/llama-3.3-70b-instruct:free",
            "openai/gpt-4o-mini",
        ]
        assert gw._usable_models(chain) == ["groq/qwen/qwen3-32b"]

    def test_all_providers_keyed_keeps_full_chain(self, gateway):
        chain = list(gateway.model_chain)
        assert gateway._usable_models(chain) == [m for m in chain if not m.startswith("openai/")]


class TestResolveChain:
    def test_explicit_model_goes_first_and_is_not_duplicated(self, gateway):
        chain = gateway._resolve_chain("gemini/gemini-3.5-flash")
        assert chain[0] == "gemini/gemini-3.5-flash"
        assert chain.count("gemini/gemini-3.5-flash") == 1

    def test_explicit_list_is_used_verbatim(self, gateway):
        assert gateway._resolve_chain(["gemini/gemini-3.5-flash"]) == ["gemini/gemini-3.5-flash"]

    def test_chain_comes_from_settings_not_a_hardcoded_constant(self):
        # Điểm khác Project A: chain là cấu hình, không phải hằng số trong class.
        gw = make_gateway(llm_model_chain=["groq/only-this"])
        assert gw._resolve_chain(None) == ["groq/only-this"]


class TestThinkingModels:
    def test_groq_qwen3_is_thinking(self, gateway):
        assert gateway._is_thinking_model("groq/qwen/qwen3-32b") is True

    def test_groq_gpt_oss_is_thinking(self, gateway):
        assert gateway._is_thinking_model("groq/openai/gpt-oss-120b") is True

    def test_gemini_is_thinking(self, gateway):
        # gemini-3.5-flash think mặc định và trả content RỖNG khi max_tokens
        # nhỏ (classifier dùng 150) trừ khi gửi reasoning_effort="none"
        # — verify live 2026-07-12 ở Project A.
        assert gateway._is_thinking_model("gemini/gemini-3.5-flash") is True

    def test_openrouter_qwen3_instruct_is_not_thinking(self, gateway):
        # Bản instruct không có thinking mode; gửi reasoning_effort sẽ 400
        # mọi request fallback.
        assert (
            gateway._is_thinking_model("openrouter/qwen/qwen3-next-80b-a3b-instruct:free") is False
        )

    def test_gpt_oss_rejects_none_effort(self, gateway):
        # Groq gpt-oss chỉ nhận low/medium/high.
        assert gateway._normalize_reasoning_effort("groq/openai/gpt-oss-120b", "none") == "low"
        assert gateway._normalize_reasoning_effort("groq/qwen/qwen3-32b", "none") == "none"


class TestCleanThinking:
    def test_strips_complete_think_block(self, gateway):
        assert gateway._clean_thinking("<think>abc</think>Kết quả") == "Kết quả"

    def test_strips_unterminated_think_block(self, gateway):
        assert gateway._clean_thinking("Kết quả<think>bị cắt giữa chừng") == "Kết quả"

    def test_none_becomes_empty_string(self, gateway):
        # Model thinking trả content=None khi đốt hết budget vào reasoning.
        assert gateway._clean_thinking(None) == ""


class TestRateLimitDetection:
    @pytest.mark.parametrize(
        "message",
        ["Rate limit exceeded", "rate_limit_error", "HTTP 429 Too Many Requests", "quota exceeded"],
    )
    def test_rate_limit_messages_trigger_key_rotation(self, gateway, message):
        assert gateway._is_rate_limit(Exception(message)) is True

    def test_other_errors_do_not(self, gateway):
        # Model chết thì phải sang model KHÁC, không phải xoay key vô ích.
        assert gateway._is_rate_limit(Exception("404 model not found")) is False
