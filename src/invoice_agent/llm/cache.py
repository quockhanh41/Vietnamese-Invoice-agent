"""Record/replay fixture cache (design doc §11.4).

One mechanism, two problems: CI runs end-to-end tests with no API key, and a
live demo cannot be broken by somebody else's rate limit. Demos fail at the
worst possible moment; this removes the failure mode rather than hoping.

⚠️ Fixtures store the messages IN THE CLEAR, so a reviewer can read what was
actually sent to a model. **Only anonymised or synthetic invoices may ever be
recorded.** M3 opened by finding six real vendor PDFs committed under
tests/fixtures/ — a replay fixture is the same kind of quiet plaintext
container, and no automated layer in this repo looks at its contents.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class LLMReplayMiss(RuntimeError):
    """No fixture for this request, in a mode that must not touch the network.

    Raised rather than falling through to a live call. A fallthrough turns a
    missing fixture into one of two failures that look identical from the
    outside: a mysterious error on a keyless runner, or a test that quietly
    spent money.
    """


@dataclass(frozen=True)
class CachedResponse:
    """What one model returned — or the error it raised instead.

    `error` non-null means replay must RAISE rather than return text. M3-C
    needs it to replay the escalation path, where a failing vision call sends
    the graph to manual_queue.
    """

    text: str
    prompt_tokens: int
    completion_tokens: int
    error: str | None = None


def make_key(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    reasoning_effort: str | None,
    image_digests: list[str] | None = None,
) -> str:
    """sha256 over the whole request.

    `model` is IN the key on purpose (decided 2026-07-29, spec §5.2): a
    response recorded from Groq must not replay as though Gemini produced it,
    because llm_calls.model/provider feed the cost report and M4.1's
    exclude_model. The API key is deliberately NOT in the key — keys rotate
    within one model and a cached response does not depend on which one
    answered.

    `sort_keys=True, ensure_ascii=False` then utf-8: a key that moved with the
    JSON escaping style would invalidate every fixture on a library bump, and
    every message in this project contains Vietnamese.
    """
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "json_mode": json_mode,
        "reasoning_effort": reasoning_effort,
        "image_digests": image_digests or [],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _path_for(cache_dir: str | Path, key: str) -> Path:
    """Filenames are truncated for readability; `load` verifies the full key."""
    return Path(cache_dir) / f"{key[:16]}.json"


def load(cache_dir: str | Path, key: str) -> CachedResponse | None:
    path = _path_for(cache_dir, key)
    if not path.exists():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("key") != key:
        # 16 hex chars is 64 bits. A collision is vanishingly unlikely and
        # catastrophic if unchecked — it would replay an unrelated response as
        # though it were this request's. Treat it as a miss.
        return None
    return CachedResponse(
        text=doc["response"],
        prompt_tokens=doc["usage"]["prompt_tokens"],
        completion_tokens=doc["usage"]["completion_tokens"],
        error=doc.get("error"),
    )


def save(
    cache_dir: str | Path,
    key: str,
    *,
    model: str,
    messages: list[dict[str, Any]],
    response: CachedResponse,
) -> Path:
    path = _path_for(cache_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "key": key,
        "model": model,
        "messages": messages,
        "response": response.text,
        "usage": {
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        },
        "error": response.error,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
