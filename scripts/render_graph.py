"""Regenerate the committed enrichment-graph diagram.

Mermaid TEXT, not a PNG. Measured 2026-07-29 (spec M3-C §2.3): `draw_mermaid()`
is the only renderer that works offline — `draw_mermaid_png()` needs a network
round-trip to mermaid.ink, `draw_png()` needs graphviz, and `draw_ascii()`
raises ImportError without grandalf. Text also makes the artefact a DRIFT
GUARD: `tests/test_enrichment_graph.py` regenerates and compares, so the
diagram cannot quietly stop describing the graph. GitHub renders it natively.
"""

import pathlib

from invoice_agent.graphs.enrichment import build_enrichment_graph

OUT = pathlib.Path("docs/diagrams/enrichment_graph.mmd")


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        build_enrichment_graph().get_graph().draw_mermaid(), encoding="utf-8"
    )
    print(f"✅ wrote {OUT}")


if __name__ == "__main__":
    main()
