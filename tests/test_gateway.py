"""LLMGateway provider/key routing — không gọi mạng.

Port từ Project A `tests/test_gateway.py`. Khác một điểm: gateway ở đây nhận
`Settings` thay vì tự đọc `os.getenv`, nên test dựng Settings tường minh —
hermetic thật sự, không còn phải `monkeypatch.delenv` để chống `.env` rò vào.
"""

import json

import pytest
from tests.helpers.fake_llm import ok_response as _ok_response

from invoice_agent.config import Settings
from invoice_agent.llm.gateway import LLMGateway


def make_gateway(**overrides) -> LLMGateway:
    defaults = {
        "groq_api_keys": ["gk1", "gk2"],
        "gemini_api_keys": ["gem1"],
        "openrouter_api_keys": ["or1"],
        "openai_api_key": "",
        "llm_mode": "live",
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


class TestReplayMode:
    """The M3 gate condition this spec owns: the suite runs with LLM_MODE=replay
    and NO API KEY."""

    def _record_fixture(self, tmp_path, *, model, prompt="xin chào", text="ok", error=None):
        from invoice_agent.llm import cache

        messages = [{"role": "user", "content": prompt}]
        key = cache.make_key(
            model=model, messages=messages, temperature=0.1, max_tokens=2048,
            json_mode=False, reasoning_effort=None,
        )
        cache.save(
            tmp_path, key, model=model, messages=messages,
            response=cache.CachedResponse(
                text=text, prompt_tokens=812, completion_tokens=97, error=error
            ),
        )

    def test_replay_runs_with_no_api_keys(self, tmp_path, monkeypatch):
        """Measured 2026-07-29: with zero keys _usable_models returns [] — not a
        shorter chain. Without the replay bypass NOTHING runs on a CI runner."""
        from invoice_agent.llm import gateway as gw_module

        def explode(**kwargs):
            raise AssertionError("replay must never reach the network")

        monkeypatch.setattr(gw_module.litellm, "completion", explode)
        model = "gemini/gemini-3.5-flash"
        self._record_fixture(tmp_path, model=model, text="DỊCH VỤ VẬN CHUYỂN")

        gw = make_gateway(
            groq_api_keys=[], gemini_api_keys=[], openrouter_api_keys=[], openai_api_key="",
            llm_mode="replay", llm_cache_dir=str(tmp_path), llm_model_chain=[model],
        )
        calls = []
        assert gw.generate("xin chào", calls=calls) == "DỊCH VỤ VẬN CHUYỂN"
        assert calls[0].prompt_tokens == 812, "replay must supply usage, or CI records no cost"

    def test_replay_walks_the_model_chain(self, tmp_path, monkeypatch):
        """A fixture recorded for the SECOND model must still be found — which
        only works if the seam sits inside the model loop."""
        from invoice_agent.llm import gateway as gw_module

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("no network in replay")),
        )
        second = "gemini/gemini-3.5-flash"
        self._record_fixture(tmp_path, model=second, text="từ model thứ hai")

        gw = make_gateway(
            groq_api_keys=[], gemini_api_keys=[], openrouter_api_keys=[], openai_api_key="",
            llm_mode="replay", llm_cache_dir=str(tmp_path),
            llm_model_chain=["groq/llama-3.3-70b-versatile", second],
        )
        text, used = gw.generate_with_model("xin chào")
        assert (text, used) == ("từ model thứ hai", second)

    def test_replay_miss_raises_and_never_calls_litellm(self, tmp_path, monkeypatch):
        from invoice_agent.llm import gateway as gw_module
        from invoice_agent.llm.cache import LLMReplayMiss

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("no network in replay")),
        )
        gw = make_gateway(
            groq_api_keys=[], llm_mode="replay", llm_cache_dir=str(tmp_path),
            llm_model_chain=["groq/llama-3.3-70b-versatile"],
        )
        with pytest.raises(LLMReplayMiss) as excinfo:
            gw.generate("một prompt chưa từng được ghi")
        assert "LLM_MODE=record" in str(excinfo.value), "the error must say how to fix it"

    def test_a_replay_miss_records_no_attempt(self, tmp_path):
        """Nothing happened — no tokens burned, so no llm_calls row."""
        from invoice_agent.llm.cache import LLMReplayMiss

        gw = make_gateway(
            groq_api_keys=[], llm_mode="replay", llm_cache_dir=str(tmp_path),
            llm_model_chain=["groq/llama-3.3-70b-versatile"],
        )
        calls = []
        with pytest.raises(LLMReplayMiss):
            gw.generate("chưa ghi bao giờ", calls=calls)
        assert calls == []

    def test_a_recorded_failure_replays_as_a_raise(self, tmp_path, monkeypatch):
        """M3-C replays the escalation path, where a failing call is the point."""
        from invoice_agent.llm import gateway as gw_module

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("no network in replay")),
        )
        model = "groq/llama-3.3-70b-versatile"
        self._record_fixture(tmp_path, model=model, text="", error="RateLimitError: 429 quota")

        gw = make_gateway(
            groq_api_keys=[], llm_mode="replay", llm_cache_dir=str(tmp_path),
            llm_model_chain=[model],
        )
        calls = []
        with pytest.raises(RuntimeError):
            gw.generate("xin chào", calls=calls)
        assert calls and calls[0].succeeded is False

    def test_record_mode_writes_a_fixture(self, tmp_path, monkeypatch):
        from invoice_agent.llm import gateway as gw_module

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: _ok_response("đã ghi", prompt=11, completion=3),
        )
        gw = make_gateway(
            llm_mode="record", llm_cache_dir=str(tmp_path),
            llm_model_chain=["groq/llama-3.3-70b-versatile"],
        )
        assert gw.generate("xin chào") == "đã ghi"
        assert list(tmp_path.glob("*.json")), "record mode wrote no fixture"

    def test_live_mode_ignores_the_cache(self, tmp_path, monkeypatch):
        from invoice_agent.llm import gateway as gw_module

        model = "groq/llama-3.3-70b-versatile"
        self._record_fixture(tmp_path, model=model, text="giá trị cũ trong cache")
        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: _ok_response("giá trị trực tiếp", prompt=1, completion=1),
        )
        gw = make_gateway(
            llm_mode="live", llm_cache_dir=str(tmp_path), llm_model_chain=[model]
        )
        assert gw.generate("xin chào") == "giá trị trực tiếp"

    def test_record_mode_writes_a_fixture_for_a_FAILED_call(self, tmp_path, monkeypatch):
        """The one thing CachedResponse.error exists for (spec §5.2): M3-C
        replays the escalation path, where a FAILING vision call is the point.
        Found 2026-07-29 by review — `cache.save` was reachable only from the
        success path, so those fixtures would have had to be hand-written JSON,
        i.e. the feature was half-built and its justification undeliverable.
        """
        from invoice_agent.llm import gateway as gw_module

        def always_429(**kwargs):
            raise RuntimeError("429 rate limit exceeded")

        monkeypatch.setattr(gw_module.litellm, "completion", always_429)
        gw = make_gateway(
            groq_api_keys=["gk1"], llm_mode="record", llm_cache_dir=str(tmp_path),
            llm_model_chain=["groq/llama-3.3-70b-versatile"],
        )
        with pytest.raises(RuntimeError):
            gw.generate("Hóa đơn giá trị gia tăng số 00000123")

        fixtures = list(tmp_path.glob("*.json"))
        assert fixtures, "record mode saved nothing for a failed call"
        doc = json.loads(fixtures[0].read_text(encoding="utf-8"))
        assert "429" in doc["error"]
        assert doc["usage"]["prompt_tokens"] > 0, "the failed attempt burned these"

    def test_a_failure_recorded_then_replayed_raises_the_same_way(self, tmp_path, monkeypatch):
        """Record and replay compose without a human editing JSON in between.
        This is the M3-C escalation path end to end."""
        from invoice_agent.llm import gateway as gw_module

        def always_429(**kwargs):
            raise RuntimeError("429 rate limit exceeded")

        monkeypatch.setattr(gw_module.litellm, "completion", always_429)
        recorder = make_gateway(
            groq_api_keys=["gk1"], llm_mode="record", llm_cache_dir=str(tmp_path),
            llm_model_chain=["groq/llama-3.3-70b-versatile"],
        )
        with pytest.raises(RuntimeError):
            recorder.generate("xin chào")

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("no network in replay")),
        )
        replayer = make_gateway(
            groq_api_keys=[], llm_mode="replay", llm_cache_dir=str(tmp_path),
            llm_model_chain=["groq/llama-3.3-70b-versatile"],
        )
        calls = []
        with pytest.raises(RuntimeError) as excinfo:
            replayer.generate("xin chào", calls=calls)
        assert "429" in str(excinfo.value)
        assert calls and calls[0].succeeded is False

    def test_generate_stream_refuses_replay_rather_than_calling_the_network(
        self, tmp_path, monkeypatch
    ):
        """`litellm.completion` has two call sites and only `_complete` sits
        behind the seam — found 2026-07-29 by review. Streaming has no fixture
        format yet (spec §8.1), so refusing keeps "replay never touches the
        network" true of the CLASS rather than only of `generate()`.
        """
        from invoice_agent.llm import gateway as gw_module
        from invoice_agent.llm.cache import LLMReplayMiss

        monkeypatch.setattr(
            gw_module.litellm, "completion",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("no network in replay")),
        )
        gw = make_gateway(
            groq_api_keys=[], llm_mode="replay", llm_cache_dir=str(tmp_path),
            llm_model_chain=["groq/llama-3.3-70b-versatile"],
        )
        with pytest.raises(LLMReplayMiss):
            list(gw.generate_stream("xin chào"))


class TestImages:
    """M3-A built `image_digests` into the cache key and never passed it. Only
    a direct unit test covered it, so nothing caught the gap."""

    def _fixture_for(self, tmp_path, *, model, images, text):
        import hashlib

        from invoice_agent.llm import cache

        messages = [{"role": "user", "content": "đọc hóa đơn"}]
        key = cache.make_key(
            model=model,
            messages=messages,
            temperature=0.1,
            max_tokens=2048,
            json_mode=False,
            reasoning_effort=None,
            image_digests=[hashlib.sha256(b).hexdigest() for b in images],
        )
        cache.save(
            tmp_path,
            key,
            model=model,
            messages=messages,
            response=cache.CachedResponse(
                text=text, prompt_tokens=900, completion_tokens=30
            ),
        )

    def test_two_invoices_do_not_share_one_vision_fixture(self, tmp_path, monkeypatch):
        """Without digests in the key, both PDFs hash to one entry and replay
        returns the FIRST invoice's answer for the second invoice."""
        from invoice_agent.llm import gateway as gw_module

        monkeypatch.setattr(
            gw_module.litellm,
            "completion",
            lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("no network in replay")
            ),
        )
        model = "gemini/gemini-3.5-flash"
        self._fixture_for(
            tmp_path, model=model, images=[b"page-of-invoice-A"], text="HÓA ĐƠN A"
        )
        self._fixture_for(
            tmp_path, model=model, images=[b"page-of-invoice-B"], text="HÓA ĐƠN B"
        )

        gw = make_gateway(
            groq_api_keys=[],
            gemini_api_keys=[],
            openrouter_api_keys=[],
            openai_api_key="",
            llm_mode="replay",
            llm_cache_dir=str(tmp_path),
            llm_model_chain=[model],
        )
        assert gw.generate("đọc hóa đơn", images=[b"page-of-invoice-A"]) == "HÓA ĐƠN A"
        assert gw.generate("đọc hóa đơn", images=[b"page-of-invoice-B"]) == "HÓA ĐƠN B"

    def test_live_call_sends_a_multimodal_content_block(self, monkeypatch):
        """MEASURED 2026-07-29 (spec §2.2): LiteLLM's real Gemini transformer
        turns `image_url` with a data: URI into inline_data/image/png."""
        from invoice_agent.llm import gateway as gw_module

        seen = {}

        def fake_completion(**kwargs):
            seen.update(kwargs)
            return _ok_response("ok", prompt=900, completion=30)

        monkeypatch.setattr(gw_module.litellm, "completion", fake_completion)
        gw = make_gateway(
            gemini_api_keys=["gem1"], llm_model_chain=["gemini/gemini-3.5-flash"]
        )
        gw.generate("đọc hóa đơn", images=[b"\x89PNG\r\n\x1a\n" + b"\x00" * 16])

        content = seen["messages"][-1]["content"]
        assert isinstance(
            content, list
        ), "images must produce a content BLOCK, not a string"
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_a_call_without_images_still_sends_a_plain_string(self, monkeypatch):
        """Every existing caller sends `content` as a string. Turning that into
        a one-element list for all calls would change every cache key in the
        repo and silently invalidate every fixture."""
        from invoice_agent.llm import gateway as gw_module

        seen = {}

        def fake_completion(**kwargs):
            seen.update(kwargs)
            return _ok_response("ok", prompt=10, completion=2)

        monkeypatch.setattr(gw_module.litellm, "completion", fake_completion)
        make_gateway(llm_model_chain=["groq/llama-3.3-70b-versatile"]).generate(
            "xin chào"
        )
        assert seen["messages"][-1]["content"] == "xin chào"



