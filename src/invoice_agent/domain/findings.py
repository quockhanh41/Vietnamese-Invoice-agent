"""What a deterministic rule observed. Not a decision, and not a write.

`Finding` is a DESCRIPTION. M3/M4 pass it to `repositories.flags.raise_flag`,
which is the only thing that writes. Keeping the two apart is what lets every
rule in this package be a pure function.

The vocabularies below are duplicated from `db.models` ON PURPOSE. `domain/`
must not import `db/` (that would drag SQLAlchemy into the pure core), so they
are written independently and `tests/test_domain_findings.py` asserts they are
equal. Drift then fails in CI rather than as an IntegrityError from
`ck_flags_raised_by` in M3, on a real invoice, at runtime.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

# Must equal db.models.FLAG_SEVERITIES / FLAG_SOURCES — asserted by test.
SEVERITIES = ("info", "warn", "critical")
SOURCES = (
    "validation", "taxcode", "anomaly", "budget", "duplicate", "vendor_memory",
    "agent_disagreement",
)

ARITHMETIC_MISMATCH = "ARITHMETIC_MISMATCH"
SUBTOTAL_MISMATCH = "SUBTOTAL_MISMATCH"
VAT_TOTAL_MISMATCH = "VAT_TOTAL_MISMATCH"
LINE_AMOUNT_MISMATCH = "LINE_AMOUNT_MISMATCH"
LINE_VAT_MISMATCH = "LINE_VAT_MISMATCH"
WORDS_DIGITS_MISMATCH = "WORDS_DIGITS_MISMATCH"
ITEM_KIND_MISSING = "ITEM_KIND_MISSING"
TAX_CODE_CHECKSUM_INVALID = "TAX_CODE_CHECKSUM_INVALID"
TAX_CODE_SHAPE_UNRECOGNISED = "TAX_CODE_SHAPE_UNRECOGNISED"
TAX_CODE_MISSING = "TAX_CODE_MISSING"
PRICE_OUTLIER = "PRICE_OUTLIER"
NEAR_DUPLICATE = "NEAR_DUPLICATE"
NEW_VENDOR = "NEW_VENDOR"
BUDGET_WARNING = "BUDGET_WARNING"
BUDGET_BREACH = "BUDGET_BREACH"
DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
CLASSIFICATION_DISPUTED = "CLASSIFICATION_DISPUTED"

# code -> (severity, raised_by). `raised_by` is the PRODUCER as the reviewer
# understands it, not the module the function happens to live in: NEW_VENDOR is
# computed in anomaly.py but reads as entity memory, so its source is
# `vendor_memory`.
CODE_REGISTRY: dict[str, tuple[str, str]] = {
    ARITHMETIC_MISMATCH: ("critical", "validation"),
    SUBTOTAL_MISMATCH: ("warn", "validation"),
    VAT_TOTAL_MISMATCH: ("warn", "validation"),
    LINE_AMOUNT_MISMATCH: ("warn", "validation"),
    LINE_VAT_MISMATCH: ("warn", "validation"),
    WORDS_DIGITS_MISMATCH: ("warn", "validation"),
    ITEM_KIND_MISSING: ("info", "validation"),
    TAX_CODE_CHECKSUM_INVALID: ("warn", "taxcode"),
    TAX_CODE_SHAPE_UNRECOGNISED: ("warn", "taxcode"),
    TAX_CODE_MISSING: ("warn", "taxcode"),
    PRICE_OUTLIER: ("warn", "anomaly"),
    NEAR_DUPLICATE: ("warn", "anomaly"),
    NEW_VENDOR: ("info", "vendor_memory"),
    BUDGET_WARNING: ("warn", "budget"),
    BUDGET_BREACH: ("critical", "budget"),
    DUPLICATE_INVOICE: ("critical", "duplicate"),
    # `warn`, not `info`: it asserts something may be WRONG, so it counts toward
    # the flag false-positive metric — M4-B made severity the discriminator
    # (docs/notes/2026-07-31-m4b-vendor-memory.md). Raised by Python comparing
    # two structured outputs; no model can reach it.
    CLASSIFICATION_DISPUTED: ("warn", "agent_disagreement"),
}


@dataclass(frozen=True)
class Finding:
    """Frozen: a rule's output must not be editable after the fact."""

    code: str
    severity: str
    raised_by: str
    detail: dict[str, Any] = field(default_factory=dict)


def make(code: str, **detail: Any) -> Finding:
    """Build a Finding from the registry so severity/source can never disagree
    with it. Decimal values are stringified because `detail` lands in a JSONB
    column and Decimal is not JSON-serialisable."""
    severity, source = CODE_REGISTRY[code]
    clean = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in detail.items()}
    return Finding(code=code, severity=severity, raised_by=source, detail=clean)


def arithmetic_mismatch(**detail: Any) -> Finding:
    return make(ARITHMETIC_MISMATCH, **detail)
