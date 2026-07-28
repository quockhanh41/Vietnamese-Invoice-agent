"""LiteLLM gateway — model-chain fallback × per-provider key rotation.

Port từ Project A `src/llm/gateway.py` (325 dòng). Logic routing/rotation giữ
nguyên vì nó đã được live-verify; **thay đổi duy nhất có chủ đích** là nguồn
cấu hình: chain và key giờ đến từ `Settings` chứ không phải `os.getenv` rải
rác trong `__init__` (đó chính là nợ kỹ thuật của Project A mà M0.2 sinh ra để
xoá). Gateway không đọc environment nữa — nó nhận cấu hình.

Hai phần mở rộng đã lên lịch, CHƯA làm ở M0:
- M3.6 `usage` out-param cho cost tracking (theo idiom `used_model` bên dưới)
- M4.1 `exclude_model` để Auditor không chấm bài của chính model đã sinh ra
"""

import re
from collections.abc import Generator
from typing import Any

import litellm

from invoice_agent.config import Settings, get_settings

# Configure LiteLLM
litellm.drop_params = True
litellm.telemetry = False


class LLMGateway:
    """Unified LLM access through LiteLLM with automatic fallback."""

    def __init__(self, settings: Settings | None = None):
        s = settings or get_settings()
        self.model_chain = list(s.llm_model_chain)
        self.groq_keys = list(s.groq_api_keys)
        self.gemini_keys = list(s.gemini_api_keys)
        self.openrouter_keys = list(s.openrouter_api_keys)
        self.openai_key = s.openai_api_key

    @staticmethod
    def _provider(model: str) -> str:
        """LiteLLM provider prefix — 'openrouter/openai/gpt-oss:free' is an
        openrouter model, so route on the prefix, never on substrings."""
        return model.split("/", 1)[0]

    def _is_thinking_model(self, model: str) -> bool:
        """Models that think by default and need reasoning_effort controlled on
        small token budgets: Groq Qwen3 emits <think> blocks; Gemini
        2.5+/3.x burns the whole budget thinking and returns EMPTY content
        (verified live on gemini-3.5-flash, max_tokens=100 → finish=length,
        content=''); Groq gpt-oss burns the budget the same way (verified
        live, max_tokens=150 → content='' with no reasoning_effort forwarded).
        NOT the OpenRouter Qwen3 in the chain — it's an instruct (non-thinking)
        variant that would 400 on the param."""
        provider = self._provider(model)
        if provider == "groq":
            return "qwen3" in model or "gpt-oss" in model
        return provider == "gemini"

    @staticmethod
    def _normalize_reasoning_effort(model: str, effort: str) -> str:
        """Groq gpt-oss rejects reasoning_effort='none' (only low/medium/high
        are valid) — map the "disable thinking" request to its weakest tier."""
        if "gpt-oss" in model and effort == "none":
            return "low"
        return effort

    def _usable_models(self, models_to_try: list[str]) -> list[str]:
        """Skip models whose provider has no API key configured."""
        return [m for m in models_to_try if any(self._keys_for(m))]

    def _keys_for(self, model: str) -> list[str]:
        """API keys to try for a model — the provider's full rotation list."""
        provider = self._provider(model)
        if provider == "openai":
            return [self.openai_key]
        if provider == "gemini":
            return self.gemini_keys
        if provider == "openrouter":
            return self.openrouter_keys
        return self.groq_keys or [""]

    @staticmethod
    def _is_rate_limit(err: Exception) -> bool:
        """Rate-limit / quota errors are worth retrying on ANOTHER KEY;
        other errors mean the model itself failed — move to the next model."""
        if isinstance(err, litellm.RateLimitError):
            return True
        msg = str(err).lower()
        return "rate limit" in msg or "rate_limit" in msg or "429" in msg or "quota" in msg

    def _resolve_chain(self, model: str | list[str] | None) -> list[str]:
        """User-specified model first, then the configured chain."""
        if isinstance(model, list):
            models_to_try = list(model)
        elif model:
            models_to_try = [model] + [m for m in self.model_chain if m != model]
        else:
            models_to_try = list(self.model_chain)
        return self._usable_models(models_to_try)

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | list[str] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> str:
        """Generate a response.

        Args:
            prompt: User prompt
            system_prompt: System prompt (optional)
            model: Override model (default: uses the configured chain)
            temperature: Generation temperature
            max_tokens: Max output tokens
            json_mode: Request JSON output format
            reasoning_effort: If set, forwarded as a provider-specific reasoning
                control (e.g. "none" hides Groq Qwen3 <think> blocks).

        Returns:
            Generated text
        """
        text, _ = self._generate_impl(
            prompt,
            system_prompt,
            model,
            temperature,
            max_tokens,
            json_mode,
            reasoning_effort,
        )
        return text

    def generate_with_model(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | list[str] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> tuple[str, str]:
        """Same as `generate()`, but also returns which model in the chain
        actually produced the answer — so a caller can tell which model to
        EXCLUDE from LLM self-eval, avoiding a model grading its own
        generation. This is the hook the Auditor agent needs (M4.1).

        Returns:
            (generated_text, model_name)
        """
        return self._generate_impl(
            prompt,
            system_prompt,
            model,
            temperature,
            max_tokens,
            json_mode,
            reasoning_effort,
        )

    def _generate_impl(
        self,
        prompt: str,
        system_prompt: str | None,
        model: str | list[str] | None,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        reasoning_effort: str | None,
    ) -> tuple[str, str]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        models_to_try = self._resolve_chain(model)

        # dict[str, Any]: kwargs này chứa cả dict (response_format) lẫn str
        # (reasoning_effort) — không annotate thì mypy khoá kiểu theo nhánh
        # json_mode và chặn dòng gán reasoning_effort bên dưới.
        extra_kwargs: dict[str, Any] = (
            {"response_format": {"type": "json_object"}} if json_mode else {}
        )

        # Try each model in chain; within a model, rotate API keys on rate-limit
        last_error = None
        for attempt_model in models_to_try:
            model_kwargs = dict(extra_kwargs)
            if reasoning_effort is not None and self._is_thinking_model(attempt_model):
                # reasoning_effort="none" disables Groq Qwen3 thinking entirely.
                # (reasoning_format="hidden" only hides the <think> block — the
                # model still burns the token budget thinking → empty output.)
                model_kwargs["reasoning_effort"] = self._normalize_reasoning_effort(
                    attempt_model, reasoning_effort
                )

            for api_key in self._keys_for(attempt_model):
                try:
                    response = litellm.completion(
                        model=attempt_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        api_key=api_key,
                        **model_kwargs,
                    )
                    return (
                        self._clean_thinking(response.choices[0].message.content),
                        attempt_model,
                    )
                except Exception as e:
                    last_error = e
                    if self._is_rate_limit(e):
                        continue  # rotate to the next key for the same model
                    break  # non-rate-limit failure — try the next model

        raise RuntimeError(f"All models failed. Last error: {last_error}")

    def _clean_thinking(self, text: str | None) -> str:
        """Strip Qwen 3 `<think>...</think>` tags from output.

        Qwen 3 models embed their reasoning in these tags — clean before returning.
        """
        if text is None:
            # Thinking models (Gemini, Qwen3) return content=None when they burn
            # the whole max_tokens budget on internal reasoning before answering.
            return ""
        # Remove complete <think>...</think> blocks
        text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
        # Remove trailing incomplete <think> tag (edge case)
        text = re.sub(r"<think>[\s\S]*$", "", text, flags=re.IGNORECASE)
        return text.strip()

    def generate_stream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | list[str] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        reasoning_effort: str | None = None,
        used_model: dict | None = None,
    ) -> Generator[str]:
        """Stream a response token by token.

        Args:
            reasoning_effort: If set, forwarded as a provider-specific reasoning
                control (e.g. "none" hides Groq Qwen3 <think> blocks).
            used_model: If a dict is passed, populated with {"model": name} once
                a chunk successfully streams — lets the caller learn which model
                in the fallback chain actually produced the answer. A plain
                out-param dict rather than a return value, since generators
                can't return a value AND be driven by a simple `for` loop.
                Fresh dict per call — safe with a single shared `LLMGateway`
                instance used across concurrent requests (no state on `self`).

        Yields:
            Text tokens one at a time
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        models_to_try = self._resolve_chain(model)

        # Try each model; within a model, rotate API keys on rate-limit
        for attempt_model in models_to_try:
            model_kwargs = {}
            if reasoning_effort is not None and self._is_thinking_model(attempt_model):
                model_kwargs["reasoning_effort"] = self._normalize_reasoning_effort(
                    attempt_model, reasoning_effort
                )

            for api_key in self._keys_for(attempt_model):
                started = False
                try:
                    response = litellm.completion(
                        model=attempt_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        api_key=api_key,
                        stream=True,
                        **model_kwargs,
                    )
                    for chunk in response:
                        if chunk.choices[0].delta.content:
                            started = True
                            if used_model is not None:
                                used_model["model"] = attempt_model
                            yield chunk.choices[0].delta.content
                    return
                except Exception as e:
                    if started:
                        # Mid-stream failure: tokens already reached the client —
                        # retrying would duplicate output. Surface and stop.
                        yield "\n\n[Lỗi] Kết nối bị gián đoạn giữa chừng. Vui lòng thử lại."
                        return
                    if self._is_rate_limit(e):
                        continue  # rotate to the next key for the same model
                    break  # non-rate-limit failure — try the next model

        yield "[Lỗi] Không thể kết nối đến LLM. Vui lòng thử lại sau."
