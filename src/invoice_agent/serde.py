"""Which of our own types may cross a checkpoint boundary.

MEASURED 2026-07-29 (spec M3-C §2.1) on langgraph 1.2.9. Every type below
round-trips today, and each emits:

    Deserializing unregistered type ... from checkpoint.
    This will be blocked in a future version.

"Blocked" is the dangerous part, because it is not an exception:

    default (permissive)   -> Finding, equal=True, with a warning
    strict, no allowlist   -> dict,    equal=False          <- silent
    explicit allowlist     -> Finding, equal=True, no warning

A `Finding` arriving as a plain dict fails at the first `finding.code` far from
the cause — or worse, subscripting code survives and the flag vocabulary
degrades unnoticed. `langgraph==1.2.9` is pinned to protect HITL durability
(roadmap §0 rule 6), and this is the mechanism by which a future bump would
break it QUIETLY. Registering here turns that into a present, tested guarantee.

Add a type to `EnrichmentState` and you must add it here —
`tests/test_serde.py` enumerates from a populated state, so the omission fails
in CI rather than in a checkpoint.
"""

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

ALLOWED_MSGPACK_MODULES: list[tuple[str, str]] = [
    ("invoice_agent.domain.findings", "Finding"),
    ("invoice_agent.extraction.models", "InvoiceData"),
    # Nested inside InvoiceData, and msgpack registers types individually —
    # allowlisting the container is not enough.
    ("invoice_agent.extraction.models", "InvoiceLine"),
    ("invoice_agent.llm.cost", "LLMCallRecord"),
]


def build_serde() -> JsonPlusSerializer:
    """The serializer every checkpointer in this project must use."""
    return JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_MSGPACK_MODULES)
