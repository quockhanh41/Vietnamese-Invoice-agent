"""In-process progress channels for running batches.

EXPECTED TO BE LOST. This is memory, not storage: a restart empties it, and
design §5.5 says the API never assumes an in-RAM registry survived one. That is
why `GET /batches/{id}` counts rows in Postgres instead of reading this, and why
`tests/test_api_batches.py` clears the registry and asserts the batch is still
queryable.

Losing progress costs a client its live view. It never costs a batch.
"""

import queue
import threading
from collections.abc import Iterator
from typing import Any

_CHANNELS: dict[str, queue.Queue[dict[str, Any] | None]] = {}
_LOCK = threading.Lock()

# Sentinel pushed by `finish` so a streaming client terminates instead of
# blocking forever on a batch that already ended.
_END = None


def _channel(batch_id: str) -> queue.Queue[dict[str, Any] | None]:
    with _LOCK:
        return _CHANNELS.setdefault(batch_id, queue.Queue())


def publish(batch_id: str, event: dict[str, Any]) -> None:
    _channel(batch_id).put(event)


def finish(batch_id: str) -> None:
    _channel(batch_id).put(_END)


def stream(batch_id: str, *, timeout: float = 120.0) -> Iterator[dict[str, Any]]:
    """Yield events until the batch finishes or `timeout` elapses.

    The timeout is a liveness guard, not a deadline for the batch: a client that
    attaches to a batch whose producer already died would otherwise hang for as
    long as the connection stayed open.
    """
    channel = _channel(batch_id)
    while True:
        try:
            event = channel.get(timeout=timeout)
        except queue.Empty:
            return
        if event is _END:
            return
        yield event


def clear() -> None:
    """Drop every channel. Stands in for a process restart in tests."""
    with _LOCK:
        _CHANNELS.clear()
