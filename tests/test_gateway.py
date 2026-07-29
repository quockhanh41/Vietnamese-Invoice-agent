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


class _FakeUsage:
    def __init__(self, prompt: int, completion: int):
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    """Just enough of a litellm ModelResponse for the gateway's two accesses:
    `.choices[0].message.content` and `.usage`."""

    def __init__(self, content: str, prompt: int, completion: int):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage(prompt, completion)


def _ok_response(content: str, *, prompt: int, completion: int) -> _FakeResponse:
    return _FakeResponse(content, prompt, completion)


class TestCallRecording:
    """Design doc §2.9: record the FAILED calls too. A 429 still burned the
    input tokens, and omitting it makes the cost number wrong.

    Stubbed rather than replayed, deliberately (spec §5.2): API keys are not in
    the cache key, so 'key 1 rate-limits, key 2 succeeds' is unrepresentable as
    fixtures. Recording and replay are different behaviours, tested separately.
    """

    def test_success_records_one_attempt_with_provider_usage(self, monkeypatch):
        from invoice_agent.llm import gateway as gw_module

        def fake_completion(**kwargs):
            return _ok_response("phân loại: VĂN PHÒNG PHẨM", prompt=812, completion=97)

        monkeypatch.setattr(gw_module.litellm, "completion", fake_completion)
        calls = []
        gw = make_gateway(llm_model_chain=["groq/llama-3.3-70b-versatile"])
        text = gw.generate("xin chào", calls=calls)

        assert text == "phân loại: VĂN PHÒNG PHẨM"
        assert len(calls) == 1
        assert calls[0].succeeded is True
        assert calls[0].provider == "groq"
        assert (calls[0].prompt_tokens, calls[0].completion_tokens) == (812, 97)
        assert calls[0].attempt == 1

    def test_fallback_records_every_attempt(self, monkeypatch):
        """Two rate-limited keys then a success = THREE records, not one."""
        from invoice_agent.llm import gateway as gw_module

        seen = []

        def fake_completion(**kwargs):
            seen.append(kwargs["api_key"])
            if len(seen) <= 2:
                raise RuntimeError("429 rate limit exceeded")
            return _ok_response("ok", prompt=100, completion=10)

        monkeypatch.setattr(gw_module.litellm, "completion", fake_completion)
        calls = []
        gw = make_gateway(
            groq_api_keys=["gk1", "gk2", "gk3"],
            llm_model_chain=["groq/llama-3.3-70b-versatile"],
        )
        gw.generate("xin chào", calls=calls)

        assert [c.succeeded for c in calls] == [False, False, True]
        assert [c.attempt for c in calls] == [1, 2, 3]

    def test_failed_call_still_counts_input_tokens(self, monkeypatch):
        """The provider sends no usage payload with an error, but the tokens
        were burned. token_counter is offline for all six chain models
        (measured 2026-07-29, spec §2.4)."""
        from invoice_agent.llm import gateway as gw_module

        def always_429(**kwargs):
            raise RuntimeError("429 rate limit exceeded")

        monkeypatch.setattr(gw_module.litellm, "completion", always_429)
        calls = []
        gw = make_gateway(
            groq_api_keys=["gk1"], llm_model_chain=["groq/llama-3.3-70b-versatile"]
        )
        with pytest.raises(RuntimeError):
            gw.generate("Hóa đơn giá trị gia tăng số 00000123", calls=calls)

        assert len(calls) == 1
        assert calls[0].succeeded is False
        assert calls[0].prompt_tokens > 0, "input tokens were burned but not counted"
        assert calls[0].completion_tokens == 0
        assert "429" in (calls[0].error or "")

    def test_calls_out_param_is_optional(self, monkeypatch):
        """Every existing caller passes nothing and must keep working."""
        from invoice_agent.llm import gateway as gw_module

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: _ok_response("ok", prompt=1, completion=1),
        )
        gw = make_gateway(llm_model_chain=["groq/llama-3.3-70b-versatile"])
        assert gw.generate("xin chào") == "ok"

    def test_latency_is_measured_per_attempt(self, monkeypatch):
        from invoice_agent.llm import gateway as gw_module

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: _ok_response("ok", prompt=1, completion=1),
        )
        calls = []
        gw = make_gateway(llm_model_chain=["groq/llama-3.3-70b-versatile"])
        gw.generate("xin chào", calls=calls)
        assert calls[0].latency_ms >= 0

