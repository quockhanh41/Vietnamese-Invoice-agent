"""The fixture cache — key identity and round-trip. No gateway, no network."""

import json
from pathlib import Path

from invoice_agent.llm import cache

BASE_REQUEST = {
    "model": "groq/llama-3.3-70b-versatile",
    "messages": [{"role": "user", "content": "Hóa đơn số 00000123, tổng 1.500.000 đồng"}],
    "temperature": 0.1,
    "max_tokens": 2048,
    "json_mode": False,
    "reasoning_effort": None,
}


class TestKeyIdentity:
    def test_identical_requests_share_a_key(self):
        assert cache.make_key(**BASE_REQUEST) == cache.make_key(**BASE_REQUEST)

    def test_same_messages_different_model_are_distinct_keys(self):
        """Decided 2026-07-29 (spec §5.2): a response recorded from Groq must
        not replay as though Gemini produced it. llm_calls.model/provider feed
        the cost report and M4.1's exclude_model."""
        other = {**BASE_REQUEST, "model": "gemini/gemini-3.5-flash"}
        assert cache.make_key(**BASE_REQUEST) != cache.make_key(**other)

    def test_image_bytes_change_the_key(self):
        """[] for every text call; carries M3-B's vision images."""
        with_image = {**BASE_REQUEST, "image_digests": ["abc123"]}
        assert cache.make_key(**BASE_REQUEST) != cache.make_key(**with_image)

    def test_temperature_changes_the_key(self):
        hotter = {**BASE_REQUEST, "temperature": 0.9}
        assert cache.make_key(**BASE_REQUEST) != cache.make_key(**hotter)

    def test_key_is_stable_across_vietnamese_text(self):
        """ensure_ascii=False plus utf-8 encoding — a key that changed with the
        JSON escaping style would invalidate every fixture on a library bump."""
        key = cache.make_key(**BASE_REQUEST)
        assert key == cache.make_key(**json.loads(json.dumps(BASE_REQUEST, ensure_ascii=False)))


class TestRoundTrip:
    def test_save_then_load_returns_the_response(self, tmp_path: Path):
        key = cache.make_key(**BASE_REQUEST)
        cache.save(
            tmp_path, key,
            model=BASE_REQUEST["model"], messages=BASE_REQUEST["messages"],
            response=cache.CachedResponse(text="DỊCH VỤ", prompt_tokens=812, completion_tokens=97),
        )
        hit = cache.load(tmp_path, key)
        assert hit is not None
        assert hit.text == "DỊCH VỤ"
        assert (hit.prompt_tokens, hit.completion_tokens) == (812, 97)

    def test_miss_returns_none(self, tmp_path: Path):
        assert cache.load(tmp_path, "0" * 64) is None

    def test_a_truncated_filename_collision_is_a_miss_not_a_wrong_answer(self, tmp_path: Path):
        """Filenames use key[:16]; the file stores the FULL key and load()
        checks it. Without that check a 64-bit collision would silently replay
        someone else's response."""
        key = cache.make_key(**BASE_REQUEST)
        cache.save(
            tmp_path, key,
            model=BASE_REQUEST["model"], messages=BASE_REQUEST["messages"],
            response=cache.CachedResponse(text="x", prompt_tokens=1, completion_tokens=1),
        )
        path = tmp_path / f"{key[:16]}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["key"] = "f" * 64
        path.write_text(json.dumps(doc), encoding="utf-8")
        assert cache.load(tmp_path, key) is None

    def test_a_recorded_failure_round_trips(self, tmp_path: Path):
        """M3-C replays the escalation path: a vision call that FAILS, sending
        the graph to manual_queue. That needs a recorded failure."""
        key = cache.make_key(**BASE_REQUEST)
        cache.save(
            tmp_path, key,
            model=BASE_REQUEST["model"], messages=BASE_REQUEST["messages"],
            response=cache.CachedResponse(
                text="", prompt_tokens=812, completion_tokens=0,
                error="RateLimitError: 429 quota exceeded",
            ),
        )
        hit = cache.load(tmp_path, key)
        assert hit is not None and hit.error is not None
        assert "429" in hit.error

    def test_fixture_is_human_readable(self, tmp_path: Path):
        """Messages stored in the clear so a reviewer can read what was sent.
        ⚠️ Only anonymised or synthetic invoices may ever be recorded."""
        key = cache.make_key(**BASE_REQUEST)
        path = cache.save(
            tmp_path, key,
            model=BASE_REQUEST["model"], messages=BASE_REQUEST["messages"],
            response=cache.CachedResponse(text="ok", prompt_tokens=1, completion_tokens=1),
        )
        assert "Hóa đơn số 00000123" in path.read_text(encoding="utf-8")
