"""Upload, background execution, SSE progress, and the DB as the source of truth."""

import json
import pathlib
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from invoice_agent.api.app import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODE", "replay")
    for key in ("GROQ_API_KEYS", "GEMINI_API_KEYS", "OPENROUTER_API_KEYS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    # WITHOUT THIS THE MONKEYPATCH DOES NOTHING — get_settings is lru_cached, so
    # a Settings built earlier keeps llm_mode="live" with real keys loaded.
    # Measured 2026-08-01 at M4-D: a gate test made LIVE calls and passed only
    # because a key happened to be configured.
    from invoice_agent.config import get_settings

    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _one_real_invoice() -> tuple[str, bytes]:
    p = pathlib.Path("tests/fixtures/real_samples/viettelpost_gtgt_2.1.0.xml")
    return p.name, p.read_bytes()


def _wait_for_completion(client, batch_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/batches/{batch_id}").json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.2)
    pytest.fail(f"batch {batch_id} did not finish within {timeout}s")


def test_upload_starts_a_batch_and_returns_immediately(client):
    name, data = _one_real_invoice()
    response = client.post("/batches", files={"files": (name, data, "application/xml")})
    assert response.status_code == 202
    batch_id = response.json()["batch_id"]
    uuid.UUID(batch_id)
    _wait_for_completion(client, batch_id)


def test_the_event_stream_emits_one_event_per_node(client):
    """Spec §6 criterion 3: one event per node, node sequence includes
    serial_join, route_outcomes, auto_post, open_approval, finalize."""
    name, data = _one_real_invoice()
    batch_id = client.post(
        "/batches", files={"files": (name, data, "application/xml")}
    ).json()["batch_id"]

    nodes = []
    with client.stream("GET", f"/batches/{batch_id}/events") as stream:
        for line in stream.iter_lines():
            if line.startswith("data: "):
                event = json.loads(line[len("data: "):])
                if event.get("event") == "end":
                    break
                if "node" in event:
                    nodes.append(event["node"])

    # Every node after the fan-out runs exactly once for a one-invoice batch.
    for node in ("serial_join", "route_outcomes", "auto_post", "open_approval", "finalize"):
        assert node in nodes, f"{node} never reported progress (nodes seen: {nodes})"


def test_a_batch_is_queryable_after_the_registry_is_cleared(client):
    """Spec §2.4 — progress is EPHEMERAL, the database is truth.

    Design §5.5: the API never assumes an in-RAM registry survived a restart.
    Clearing the registry stands in for the restart; `GET /batches/{id}` must
    still answer, because it counts rows rather than reading memory.
    """
    from invoice_agent.api import progress

    name, data = _one_real_invoice()
    batch_id = client.post(
        "/batches", files={"files": (name, data, "application/xml")}
    ).json()["batch_id"]
    before = _wait_for_completion(client, batch_id)

    progress.clear()

    after = client.get(f"/batches/{batch_id}")
    assert after.status_code == 200
    assert after.json()["persisted"] == before["persisted"]
    assert after.json()["status"] == "completed"


def test_an_unknown_batch_is_404(client):
    assert client.get(f"/batches/{uuid.uuid4()}").status_code == 404
