"""Synthetic corpus generation — deliberately independent of the parser.

THE ORDERING RULE (design doc §10.1). Nothing in this package may import
`xml_schema`, `xml_parser` or `invoice_agent.domain`. The templates are written
from `tests/fixtures/real_samples/`; the parser is written from its own path
map; and only because the two were built separately does a 300/300 round-trip
mean anything.

`scripts.seed_reference.BUDGETS` is the one allowed import, by design (M2-A §7).
"""
