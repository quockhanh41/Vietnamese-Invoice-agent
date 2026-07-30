"""Ground-truth records for the generated corpus.

Finding codes are LITERAL STRINGS here, not imports. `domain.findings` must not
be imported by the generator (§4.1), so the two vocabularies are written
separately and `tests/test_generator_manifest.py` asserts they agree — the same
arrangement M2-B used for the flag vocabulary against the database CHECK.

JSONL rather than one JSON document: a 300-line corpus stays diffable and
greppable, and M6 can stream it.
"""

import json
import pathlib
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

# defect name -> the finding codes it should cause. Cross-checked against
# domain.findings.CODE_REGISTRY by test.
DEFECT_CODES: dict[str, tuple[str, ...]] = {
    "clean": (),
    "arithmetic_error": ("ARITHMETIC_MISMATCH",),
    "words_digits_mismatch": ("WORDS_DIGITS_MISMATCH",),
    "bad_tax_code": ("TAX_CODE_CHECKSUM_INVALID",),
    "wrong_vat_rate": ("LINE_VAT_MISMATCH",),
    "exact_duplicate": ("DUPLICATE_INVOICE",),
    "near_duplicate": ("NEAR_DUPLICATE",),
    "new_vendor": ("NEW_VENDOR",),
    "budget_overrun": ("BUDGET_BREACH",),
    "price_outlier": ("PRICE_OUTLIER",),
    # These four are not deterministic findings: injection and ambiguity are
    # judged by the classifier at M4/M6, and the PDF groups are routing
    # decisions at M3. They carry no expected finding code on purpose.
    "prompt_injection": (),
    "ambiguous_category": (),
    "pdf_only": (),
    "unreadable_pdf": (),
}


# Defects that live in the invoice BODY and are decidable from that body alone
# — exactly the ones `domain.validation.validate()` can raise without touching
# the database. A duplicate copies the body, so it copies these too, and its
# manifest row must SAY so.
#
# Found 2026-07-29 by the M3 exit audit: `INV-0070` is an exact duplicate of
# `INV-0060` (`wrong_vat_rate`), inherited the wrong line VAT exactly as a real
# duplicate would, and declared only `DUPLICATE_INVOICE`. `validate()` flagged
# it correctly and the manifest called that a false positive. At M6 the eval
# scores predictions against this file, so an undeclared true positive
# UNDERSTATES precision.
BODY_DEFECTS: tuple[str, ...] = (
    "arithmetic_error",
    "words_digits_mismatch",
    "bad_tax_code",
    "wrong_vat_rate",
)


@dataclass(frozen=True)
class ManifestRecord:
    invoice_id: str
    path: str | None            # None for PDF-only entries (rendered at M3.4)
    source_type: str            # xml | pdf_vision
    seller_tax_code: str
    template_no: str
    invoice_series: str
    invoice_number: str
    issue_date: str
    department: str
    category: str
    total_amount: Decimal
    # A PDF beside an XML invoice. Exactly three entries carry one, so M3-C's
    # escalation path ("XML extraction fails -> climb to vision") has real
    # input; no M2-C entry had both representations.
    pdf_path: str | None = None
    defects: list[str] = field(default_factory=list)
    expected_finding_codes: list[str] = field(default_factory=list)
    # ⚠️ THESE TWO REFERENCE FIELDS USE DIFFERENT IDENTIFIER SPACES. Measured
    # 2026-07-31 by the M4-A review: `twin_of` resolves 6/6 against
    # `invoice_id` and 0/6 against the doc_id; `duplicate_of` resolves 9/9
    # against the doc_id and 0/9 against `invoice_id`. Joining one the way you
    # joined the other yields an EMPTY join and no error, which is how this
    # hides in eval code. `tests/test_generator_labels.py` pins both.
    #
    # `twin_of` — the §9.3 negative-control partner, as an `invoice_id`
    # (`INV-0004`). The twin is a clean copy of an injection carrier.
    twin_of: str | None = None
    # `duplicate_of` — the invoice this one duplicates, as a doc_id
    # (`Id-C26TBB-00000060`), i.e. the NATURAL key. Deliberate: a duplicate is
    # defined by repeating that key, which is what design §2.3's UNIQUE index
    # is built on, so naming it by natural key is the honest reference.
    duplicate_of: str | None = None
    canonical_budget_rank: int | None = None
    # Set only for the `ambiguous_category` group: the OTHER defensible label.
    # M6 reports "chose the plausible alternative" apart from "was wrong".
    alternative_category: str | None = None


def _encode(value: Any) -> Any:
    # Decimal -> str, never float. M1 measured amounts to 8 decimal places
    # (21618409.09000000); float would silently round the corpus's own truth.
    return str(value) if isinstance(value, Decimal) else value


def write_manifest(path: pathlib.Path, records: list[ManifestRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for rec in records:
            row = {k: _encode(v) for k, v in asdict(rec).items()}
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_manifest(path: pathlib.Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
