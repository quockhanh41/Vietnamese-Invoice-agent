# M2-B Domain Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `domain/` — six pure modules that turn an `InvoiceData` into findings, journal lines and budget consumption, with zero I/O.

**Architecture:** Every function takes data and reference values as arguments and returns values. Findings are *described*, never written — M3/M4 hand them to `repositories.flags.raise_flag`. The eight real fixtures are the anchor: they must produce **zero** findings, because this milestone's real metric is flag false-positive rate, not test count.

**Tech Stack:** Python 3.13 · `decimal.Decimal` · stdlib only (no new dependencies) · pytest

**Spec:** `docs/superpowers/specs/2026-07-28-m2b-domain-core-design.md`

## Global Constraints

- **Zero I/O.** No `sqlalchemy`, no `psycopg`, no imports from `invoice_agent.db`, no clock, no randomness, no network. Task 7 asserts this with a test.
- **`Decimal` only, never `float`.** M1 measured amounts to 8 decimal places (`21618409.09000000`) and the header rule is an exact comparison.
- **Language:** English for all code, comments, docstrings, commits (CLAUDE.md). Vietnamese stays only in data the system ingests — the `TgTTTBChu` word tables are Vietnamese *data*, not prose.
- **`domain/` must not import `db/`.** The flag vocabulary is defined independently and a test asserts it equals `db.models.FLAG_SOURCES` / `FLAG_SEVERITIES`. Resolves a spec ambiguity: §5.1 says "re-exported", §7.8 forbids the import. The test gets both.
- **M2-C must not import `domain/`** — recorded here because the generator is written next.
- **Unmeasured constants must say so.** `NEAR_DUP_WINDOW_DAYS`, `PRICE_OUTLIER_Z`, `PRICE_OUTLIER_MIN_INVOICES` have no corpus to justify them yet. Each carries a comment naming M6 as where the number gets set. Do not invent a measurement.
- Verify with `make check` (ruff + mypy + pytest). Baseline at plan time: **160 tests passing**.
- `line-length = 100`, ruff `E,F,I,UP,B,SIM`, `E501` ignored.

## Existing constants to reuse — do not retype

| From | Names |
|---|---|
| `invoice_agent.extraction.xml_schema` | `ITEM_KIND_GOODS = 1`, `ITEM_KIND_NOTE = 4`, `TEMPLATE_NO_VAT = "1"`, `TEMPLATE_NO_SALES = "2"`, `EXT_KEY_LINE_AMOUNT = "Amount"` |
| `invoice_agent.extraction.models` | `InvoiceData`, `InvoiceLine` |
| `invoice_agent.db.models` (tests only) | `FLAG_SOURCES`, `FLAG_SEVERITIES` |

## File Structure

| File | Responsibility |
|---|---|
| `src/invoice_agent/domain/__init__.py` | package marker |
| `src/invoice_agent/domain/findings.py` | `Finding`, severities, sources, code vocabulary |
| `src/invoice_agent/domain/money.py` | `line_amount`, `goods_subtotal`, `parse_amount_words` |
| `src/invoice_agent/domain/taxcode.py` | MST normalisation + checksum, three shapes |
| `src/invoice_agent/domain/validation.py` | `validate()` — the eight checks |
| `src/invoice_agent/domain/accounting.py` | `JournalLine`, `build_journal_entry` |
| `src/invoice_agent/domain/budget.py` | `Consumption`, `consume` |
| `src/invoice_agent/domain/anomaly.py` | `new_vendor`, `price_outlier`, `near_duplicate` |
| `tests/test_domain_findings.py` … `tests/test_domain_anomaly.py` | one per module |
| `tests/test_domain_real_invoices.py` | the anchor + the purity test |

---

### Task 1: `findings.py` — the shared vocabulary

**Files:**
- Create: `src/invoice_agent/domain/__init__.py`, `src/invoice_agent/domain/findings.py`, `tests/test_domain_findings.py`

**Interfaces:**
- Produces: `Finding(code, severity, raised_by, detail)`; `SEVERITIES`, `SOURCES`; code constants `ARITHMETIC_MISMATCH`, `SUBTOTAL_MISMATCH`, `VAT_TOTAL_MISMATCH`, `LINE_AMOUNT_MISMATCH`, `LINE_VAT_MISMATCH`, `WORDS_DIGITS_MISMATCH`, `ITEM_KIND_MISSING`, `TAX_CODE_CHECKSUM_INVALID`, `TAX_CODE_SHAPE_UNRECOGNISED`, `TAX_CODE_MISSING`, `PRICE_OUTLIER`, `NEAR_DUPLICATE`, `NEW_VENDOR`, `BUDGET_WARNING`, `BUDGET_BREACH`, `DUPLICATE_INVOICE`

- [ ] **Step 1: Write the failing test**

`tests/test_domain_findings.py`:

```python
"""The flag vocabulary is shared with the database, and must not drift from it."""

from invoice_agent.db import models
from invoice_agent.domain import findings


def test_sources_match_the_database_check_constraint():
    """`domain/` deliberately does NOT import `db/` (purity), so the two
    vocabularies are written independently. This test is what stops them
    drifting: a mismatch would surface in M3 as an IntegrityError from
    `ck_flags_raised_by` at runtime, on a real invoice, instead of here."""
    assert findings.SOURCES == models.FLAG_SOURCES


def test_severities_match_the_database_check_constraint():
    assert findings.SEVERITIES == models.FLAG_SEVERITIES


def test_every_declared_code_maps_to_a_legal_source_and_severity():
    for code, (severity, source) in findings.CODE_REGISTRY.items():
        assert severity in findings.SEVERITIES, f"{code}: bad severity {severity}"
        assert source in findings.SOURCES, f"{code}: bad source {source}"


def test_finding_is_frozen():
    """Findings travel from domain -> node -> repository. A mutable one could be
    edited after the deterministic rule produced it, which is exactly the hole
    the safety property closes."""
    import dataclasses

    import pytest

    f = findings.arithmetic_mismatch(expected="1", actual="2")
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.code = "SOMETHING_ELSE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_domain_findings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'invoice_agent.domain'`

- [ ] **Step 3: Write minimal implementation**

`src/invoice_agent/domain/__init__.py`:

```python
"""Deterministic domain rules — pure functions, zero I/O.

This package is where flags come from. CLAUDE.md's safety property says the LLM
never raises a flag; these modules are the other half of that sentence. Nothing
here touches a database, a clock, the network or an LLM: everything arrives as
an argument and leaves as a return value.
"""
```

`src/invoice_agent/domain/findings.py`:

```python
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
SOURCES = ("validation", "taxcode", "anomaly", "budget", "duplicate", "vendor_memory")

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_domain_findings.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/domain/ tests/test_domain_findings.py
git commit -m "feat(domain): Finding + flag vocabulary, kept in step with the DB CHECK

domain/ must not import db/, so the vocabulary is written independently
and a test asserts equality. Drift fails in CI instead of as an
IntegrityError on a real invoice in M3."
```

---

### Task 2: `money.py` — the measured subtotal rule

The rule that is wrong on 6/8 invoices if you write the obvious thing. Read spec §2.1.

**Files:**
- Create: `src/invoice_agent/domain/money.py`, `tests/test_domain_money.py`

**Interfaces:**
- Consumes: `findings` (Task 1); `xml_schema.ITEM_KIND_NOTE`, `xml_schema.EXT_KEY_LINE_AMOUNT`
- Produces: `line_amount(line) -> Decimal`, `goods_subtotal(lines) -> Decimal`

- [ ] **Step 1: Write the failing test**

`tests/test_domain_money.py`:

```python
"""Money aggregation. The rule here is measured, not derived from the spec."""

from decimal import Decimal

from invoice_agent.domain import money
from invoice_agent.extraction.models import InvoiceLine


def line(**kw) -> InvoiceLine:
    return InvoiceLine(**kw)


def test_note_lines_are_not_money():
    """MUTATION GUARD. TChat=4 is a NOTE line — description only, no money.
    Summing it is wrong, and it is the first of the three facts that make the
    measured rule work (found in `hokinhdoanh_…`)."""
    lines = [
        line(item_kind=1, amount=Decimal("1000")),
        line(item_kind=4, description="Ghi chú: giao hàng trong 3 ngày"),
    ]
    assert money.goods_subtotal(lines) == Decimal("1000")


def test_vnpt_amount_comes_from_ext():
    """MUTATION GUARD. VNPT writes ThTien=0 and puts the real amount in
    TTKhac['Amount']. No amount of reading QĐ-1450 predicts this; it was
    measured. Drop the fallback and the invoice silently totals to zero."""
    lines = [line(item_kind=1, amount=Decimal("0"), ext={"Amount": "3500000"})]
    assert money.goods_subtotal(lines) == Decimal("3500000")


def test_real_amount_wins_over_the_ext_fallback():
    """The fallback is for ThTien=0 ONLY. If both are present the element wins —
    four vendors carry a similar-looking TTKhac key holding the GROSS amount,
    and preferring ext would double-count tax on half the corpus."""
    lines = [line(item_kind=1, amount=Decimal("1592726"), ext={"Amount": "1720144"})]
    assert money.goods_subtotal(lines) == Decimal("1592726")


def test_missing_item_kind_counts_as_goods():
    """Spec §5.4. Under-counting drops real money and produces a FALSE
    arithmetic mismatch; over-counting a note line is at least visible."""
    lines = [line(item_kind=None, amount=Decimal("500"))]
    assert money.goods_subtotal(lines) == Decimal("500")


def test_empty_lines_total_zero():
    assert money.goods_subtotal([]) == Decimal("0")


def test_line_amount_of_a_note_line_is_zero_not_none():
    assert money.line_amount(line(item_kind=4, description="ghi chú")) == Decimal("0")


def test_unparseable_ext_amount_is_ignored_not_crashed():
    """`ext` is verbatim vendor text. A non-numeric value there must not take
    down the whole invoice — that would be an EXTRACTION failure raised from
    the DATA layer, the wrong side of the §3.3 split."""
    lines = [line(item_kind=1, amount=Decimal("0"), ext={"Amount": "n/a"})]
    assert money.goods_subtotal(lines) == Decimal("0")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_domain_money.py -v`
Expected: FAIL — `ImportError: cannot import name 'money'`

- [ ] **Step 3: Write minimal implementation**

`src/invoice_agent/domain/money.py`:

```python
"""Money aggregation over invoice lines, and Vietnamese amount-words parsing.

THE SUBTOTAL RULE IS MEASURED, NOT DERIVED. Measured 2026-07-28 on
tests/fixtures/real_samples/ (spec §2.1):

    sum(ThTien) == TgTTTBSo                                   -> WRONG on 6/8
    sum(ThTien | TChat==1, fallback ext['Amount']) + TgTThue  -> holds  8/8

Three independent facts make it work, each found in a different real file and
none of them guessable from the regulation:

  TChat == 1            excludes note lines            (hokinhdoanh_…)
  ext['Amount']         recovers VNPT's amount, ThTien=0 (vnpt_…)
  + TgTThue             ThTien is PRE-TAX on VAT invoices (viettelpost_, phongvu_)

`goods_subtotal` is the single place the first two live. validation.py and
accounting.py both call it; neither reimplements it.
"""

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from invoice_agent.extraction import xml_schema as sch
from invoice_agent.extraction.models import InvoiceLine


def line_amount(line: InvoiceLine) -> Decimal:
    """One line's money, with the VNPT fallback. Note lines are worth zero.

    The fallback applies ONLY when ThTien is absent or zero. Measured
    2026-07-28: four vendors carry a similar-looking TTKhac key holding the
    GROSS amount (lienket_ line 1: 1720144 == ThTien 1592726 + TThue 127418),
    so preferring `ext` whenever present would double-count tax on 4/8 files.
    """
    if line.item_kind == sch.ITEM_KIND_NOTE:
        return Decimal("0")
    if line.amount:
        return line.amount
    raw = line.ext.get(sch.EXT_KEY_LINE_AMOUNT)
    if raw:
        try:
            return Decimal(raw)
        except InvalidOperation:
            # Vendor extension text is untrusted and free-form. A bad value here
            # is not an extraction failure — §3.3 keeps those in the parser — so
            # fall through to zero and let the arithmetic check report the gap.
            return Decimal("0")
    return Decimal("0")


def goods_subtotal(lines: Sequence[InvoiceLine]) -> Decimal:
    """Pre-tax total of the money-bearing lines.

    `item_kind is None` counts as GOODS (spec §5.4): TChat is present on 15/15
    lines across 8/8 fixtures, so absence is unobserved, and whether QĐ-1450
    permits omitting it waits on the NĐ 70/2025 check M1.2 still owes. Dropping
    such a line would under-count and raise a FALSE ARITHMETIC_MISMATCH on a
    valid invoice — the exact false positive the headline metric measures.
    validation.py additionally emits ITEM_KIND_MISSING so the assumption is
    visible to a reviewer.
    """
    return sum((line_amount(line) for line in lines), Decimal("0"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_domain_money.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/domain/money.py tests/test_domain_money.py
git commit -m "feat(domain): measured goods_subtotal — note lines, ext fallback"
```

---

### Task 3: `money.py` — `parse_amount_words`

Read spec §2.5 first. All eight real values and the trap each contributes are listed there.

**Files:**
- Modify: `src/invoice_agent/domain/money.py`, `tests/test_domain_money.py`

**Interfaces:**
- Produces: `parse_amount_words(text: str | None) -> Decimal | None`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_domain_money.py`:

```python
import pytest

# Every value is REAL, read from tests/fixtures/real_samples/ on 2026-07-28.
REAL_AMOUNT_WORDS = [
    ("Bảy trăm sáu mươi ba nghìn không trăm bốn mươi bốn đồng", "763044"),
    ("Chín mươi nghìn đồng", "90000"),
    ("Ba triệu bốn trăm mười nghìn  đồng", "3410000"),
    ("Mười triệu chín trăm chín mươi bốn nghìn ba trăm chín mươi bảy đồng", "10994397"),
    ("Bảy trăm lẻ hai nghìn đồng", "702000"),
    ("Hai mươi ba triệu bảy trăm tám mươi nghìn hai trăm năm mươi đồng", "23780250"),
    ("Năm mươi mốt nghìn năm trăm chín mươi chín đồng", "51599"),
    ("Ba  triệu năm trăm ngàn đồng./.", "3500000"),
]


@pytest.mark.parametrize("text,expected", REAL_AMOUNT_WORDS)
def test_parses_all_eight_real_amount_words(text, expected):
    """MUTATION GUARD. §10.3 uses TgTTTBChu as a SECOND INDEPENDENT ENCODING of
    the total, so the parser has to survive real data: 'ngàn' as well as
    'nghìn', 'lẻ' and 'không trăm' as zero-fillers, 'mốt' for 1 after a tens
    word, double spaces, and a './.' suffix."""
    assert money.parse_amount_words(text) == Decimal(expected)


@pytest.mark.parametrize(
    "text,expected",
    [
        # Unobserved in the corpus but structurally real and unambiguous, so
        # supported rather than rejected (spec §2.5, open question 3).
        ("Hai mươi lăm nghìn đồng", "25000"),
        ("Hai mươi tư nghìn đồng", "24000"),
        ("Hai mươi nhăm nghìn đồng", "25000"),
    ],
)
def test_supports_lam_tu_nham_though_no_fixture_has_them(text, expected):
    assert money.parse_amount_words(text) == Decimal(expected)


@pytest.mark.parametrize("text", [None, "", "   ", "không đọc được", "abc xyz"])
def test_unparseable_returns_none_never_zero(text):
    """`None` means 'could not read', `Decimal(0)` means 'the amount is zero'.
    Conflating them makes validation compare 0 against a real total and raise
    WORDS_DIGITS_MISMATCH on every invoice it cannot parse — the same
    None-vs-zero distinction xml_schema.money() already makes."""
    assert money.parse_amount_words(text) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_domain_money.py -k amount_words -v`
Expected: FAIL — `AttributeError: module 'money' has no attribute 'parse_amount_words'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/invoice_agent/domain/money.py`:

```python
import re

# Vietnamese numerals. This is DATA the system ingests, not authoring style, so
# it stays Vietnamese (CLAUDE.md language rule).
_UNITS = {
    "không": 0, "một": 1, "hai": 2, "ba": 3, "bốn": 4,
    "năm": 5, "sáu": 6, "bảy": 7, "tám": 8, "chín": 9,
}
# Units-position variants after a tens word. 51 is 'năm mươi MỐT', never
# 'năm mươi một' — measured in viettelpost_. 'lăm'/'nhăm' (5) and 'tư' (4)
# appear in NO fixture but are unambiguous standard Vietnamese, so they are
# supported and marked unobserved (spec §2.5).
_UNIT_VARIANTS = {"mốt": 1, "lăm": 5, "nhăm": 5, "tư": 4}
_SCALES = {"nghìn": 1000, "triệu": 1_000_000, "tỷ": 1_000_000_000}
# Zero-fillers. Both forms are real and mean the same thing: 'lẻ' in newimage_
# (702000), 'không trăm' in dienluc_ (763044).
_FILLERS = {"lẻ", "linh"}

_TRAILING = re.compile(r"\s*(đồng)?\s*(chẵn)?\s*(\./\.)?\s*$")


def parse_amount_words(text: str | None) -> Decimal | None:
    """`'Ba  triệu năm trăm ngàn đồng./.'` -> `Decimal('3500000')`.

    Returns None when the text cannot be read — NEVER Decimal(0). 'unreadable'
    and 'zero' are different facts and only one of them is a finding.

    Normalisation, in order, each step justified by a real value measured
    2026-07-28: collapse whitespace runs ('Ba  triệu' has two spaces, so does
    'mười nghìn  đồng'); strip the './.' suffix and a trailing 'đồng' (vnpt_);
    lowercase; and treat 'ngàn' as 'nghìn' (vnpt_ says ngàn, the other seven
    say nghìn).
    """
    if not text:
        return None
    cleaned = _TRAILING.sub("", " ".join(text.split()).lower())
    cleaned = cleaned.replace("ngàn", "nghìn")
    if not cleaned:
        return None

    total = Decimal(0)
    group = Decimal(0)
    pending: int | None = None
    saw_number = False

    for token in cleaned.split():
        if token in _UNITS:
            pending = _UNITS[token]
            saw_number = True
        elif token in _UNIT_VARIANTS:
            group += _UNIT_VARIANTS[token]
            pending = None
            saw_number = True
        elif token == "trăm":
            group += (pending or 0) * 100
            pending = None
        elif token == "mươi":
            group += (pending or 0) * 10
            pending = None
        elif token == "mười":
            # 'mười' is a standalone ten ('mười nghìn' = 10 000), unlike 'mươi'
            # which multiplies the digit before it.
            group += 10
            pending = None
            saw_number = True
        elif token in _FILLERS:
            pending = None
        elif token in _SCALES:
            if pending is not None:
                group += pending
                pending = None
            total += group * _SCALES[token]
            group = Decimal(0)
        else:
            # An unknown token means we did not understand the sentence. Return
            # None rather than a partial sum: a confidently wrong number here
            # would silently defeat the cross-check TgTTTBChu exists to provide.
            return None

    if pending is not None:
        group += pending
    total += group
    return total if saw_number else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_domain_money.py -v`
Expected: PASS (all 8 real values + 3 variants + 5 unparseable + Task 2's 7)

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/domain/money.py tests/test_domain_money.py
git commit -m "feat(domain): parse Vietnamese amount words, 8/8 real values

Handles ngàn/nghìn, lẻ and không trăm, mốt, mười vs mươi, double spaces
and the './.' suffix. Unparseable returns None, never Decimal(0)."
```

---

### Task 4: `taxcode.py`

**Files:**
- Create: `src/invoice_agent/domain/taxcode.py`, `tests/test_domain_taxcode.py`

**Interfaces:**
- Consumes: `findings` (Task 1)
- Produces: `normalise(mst) -> str | None`, `is_valid_checksum(mst10) -> bool`, `check_tax_code(mst, *, required=False) -> Finding | None`

- [ ] **Step 1: Write the failing test**

`tests/test_domain_taxcode.py`:

```python
"""MST validation. Three real shapes; only one of them has a public checksum."""

import pytest

from invoice_agent.domain import findings, taxcode

# Read from tests/fixtures/real_samples/ on 2026-07-28. Already anonymised —
# and the anonymiser preserved valid check digits, which is why they can be
# used as ground truth here (spec §2.4).
REAL_TAX_CODES = [
    "0400100009-006", "0300000018", "0102000009-006",
    "0311110005", "0300000018-042", "0100000003",
]


@pytest.mark.parametrize("mst", REAL_TAX_CODES)
def test_real_tax_codes_pass_checksum(mst):
    """MUTATION GUARD. 7/7 checksummable fixture codes validate under the
    General Dept. of Taxation algorithm (weights 31,29,23,19,17,13,7,5,3).
    Alter a weight and this goes red."""
    assert taxcode.check_tax_code(mst) is None


def test_household_tax_code_accepted_without_checksum():
    """MUTATION GUARD, and the point of open question 2. The 12-digit household
    MST is derived from CCCD and has NO KNOWN PUBLIC CHECKSUM RULE. A validator
    that rejects it flags real invoices — the headline metric."""
    assert taxcode.check_tax_code("050000000000") is None


def test_bad_check_digit_is_flagged():
    bad = "0100000004"  # 0100000003 is valid; the last digit is the check digit
    result = taxcode.check_tax_code(bad)
    assert result is not None
    assert result.code == findings.TAX_CODE_CHECKSUM_INVALID


def test_branch_suffix_is_checked_on_the_base_only():
    """The 3-digit branch suffix has no checksum of its own; the 10-digit base
    carries it. '-999' is a legal branch number."""
    assert taxcode.check_tax_code("0100000003-999") is None


@pytest.mark.parametrize("mst", ["123", "01000000031234567", "abcdefghij", "0100-000003"])
def test_unrecognised_shapes_are_flagged(mst):
    result = taxcode.check_tax_code(mst)
    assert result is not None
    assert result.code == findings.TAX_CODE_SHAPE_UNRECOGNISED


@pytest.mark.parametrize("mst", [None, "", "   ", "0"])
def test_absent_buyer_tax_code_is_not_a_finding(mst):
    """Measured 8/8: buyer MST is absent, '' or '0' on every real invoice, and
    one file has no NMua/Ten at all. Flagging that would flag the whole corpus.
    Absence is only a finding when the caller says the field is required."""
    assert taxcode.check_tax_code(mst) is None


def test_absent_tax_code_is_flagged_when_required():
    result = taxcode.check_tax_code(None, required=True)
    assert result is not None
    assert result.code == findings.TAX_CODE_MISSING


def test_whitespace_is_tolerated():
    assert taxcode.check_tax_code("  0100000003  ") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_domain_taxcode.py -v`
Expected: FAIL — `ImportError: cannot import name 'taxcode'`

- [ ] **Step 3: Write minimal implementation**

`src/invoice_agent/domain/taxcode.py`:

```python
"""Vietnamese tax code (MST) validation.

THREE SHAPES ARE REAL, measured 2026-07-28 across the 8 fixtures:

    0100000003        10 digits, ordinary enterprise      -> checksum applies
    0300000018-042    10 digits + 3-digit branch suffix   -> checksum on the base
    050000000000      12 digits, household business       -> NO PUBLIC RULE

A CHAR(10) assumption rejects two of the three. Plan §M1.2 opened the question
of whether the 12-digit household form (derived from CCCD) has a checksum; no
public rule was found, so it is ACCEPTED UNCONDITIONALLY. A validator that
rejects it flags real, valid invoices, which is the metric §10.4 headlines.
"""

import re

from invoice_agent.domain import findings

# General Department of Taxation algorithm for the 10-digit form: weight the
# first nine digits, check digit = 10 - (sum mod 11). Verified 2026-07-28
# against all 7 checksummable fixture codes — 7/7 valid, which also confirms
# the anonymiser preserved real check digits.
_WEIGHTS = (31, 29, 23, 19, 17, 13, 7, 5, 3)

_TEN = re.compile(r"^\d{10}$")
_TEN_BRANCH = re.compile(r"^(\d{10})-\d{3}$")
_TWELVE = re.compile(r"^\d{12}$")


def normalise(mst: str | None) -> str | None:
    """Trim surrounding whitespace. The dash is significant and is kept."""
    if mst is None:
        return None
    cleaned = mst.strip()
    return cleaned or None


def is_valid_checksum(mst10: str) -> bool:
    total = sum(int(d) * w for d, w in zip(mst10[:9], _WEIGHTS, strict=True))
    return (10 - (total % 11)) == int(mst10[9])


def check_tax_code(mst: str | None, *, required: bool = False) -> findings.Finding | None:
    """None when the code is acceptable, a Finding when it is not.

    Absence is NOT a finding by default. Measured 8/8: buyer MST is missing in
    three forms — absent, '' and '0' — on every real invoice, and real garbage
    lives in these fields ('.', 'Người mua không lấy hóa đơn'). Only the seller
    code is passed with required=True.
    """
    cleaned = normalise(mst)
    if cleaned is None or cleaned == "0":
        return findings.make(findings.TAX_CODE_MISSING) if required else None

    if _TWELVE.match(cleaned):
        return None  # household business — no public checksum rule, see module docstring

    branch = _TEN_BRANCH.match(cleaned)
    base = branch.group(1) if branch else (cleaned if _TEN.match(cleaned) else None)
    if base is None:
        return findings.make(findings.TAX_CODE_SHAPE_UNRECOGNISED, tax_code=cleaned)
    if not is_valid_checksum(base):
        return findings.make(findings.TAX_CODE_CHECKSUM_INVALID, tax_code=cleaned)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_domain_taxcode.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/domain/taxcode.py tests/test_domain_taxcode.py
git commit -m "feat(domain): MST checksum, three real shapes

7/7 checksummable fixture codes validate. The 12-digit household form is
accepted unconditionally — no public checksum rule exists, and rejecting
it would flag real invoices."
```

---

### Task 5: `validation.py` — header arithmetic and the conditional guards

**The most important task in this milestone.** Read spec §2.3 before writing anything: it corrects a measurement recorded in the plan doc, and getting it wrong flags 3 of 8 real invoices.

**Files:**
- Create: `src/invoice_agent/domain/validation.py`, `tests/test_domain_validation.py`

**Interfaces:**
- Consumes: `money.goods_subtotal` (Task 2), `findings` (Task 1)
- Produces: `validate(invoice: InvoiceData) -> list[Finding]`

- [ ] **Step 1: Write the failing test**

`tests/test_domain_validation.py`:

```python
"""Validation rules. Every threshold and guard here is measured, not assumed."""

from datetime import date
from decimal import Decimal

from invoice_agent.domain import findings, validation
from invoice_agent.extraction.models import InvoiceData, InvoiceLine


def invoice(**kw) -> InvoiceData:
    base = dict(
        template_no="1", invoice_series="C26TAA", invoice_number="00001234",
        issue_date=date(2026, 7, 1), seller_tax_code="0100000003",
        total_amount=Decimal("1100"), lines=[
            InvoiceLine(item_kind=1, amount=Decimal("1000")),
        ],
        vat_amount=Decimal("100"),
    )
    base.update(kw)
    return InvoiceData(**base)


def codes(inv) -> set[str]:
    return {f.code for f in validation.validate(inv)}


def test_clean_invoice_produces_no_findings():
    assert validation.validate(invoice()) == []


def test_header_arithmetic_mismatch_is_flagged():
    assert findings.ARITHMETIC_MISMATCH in codes(invoice(total_amount=Decimal("9999")))


def test_subtotal_check_skipped_when_subtotal_is_zero(monkeypatch):
    """MUTATION GUARD — spec §2.3, the correction to M1's record.

    `ktx_banhang_cccd` is KHMSHDon=2, a non-VAT sales invoice: TgTCThue and
    TgTThue are BOTH 0 while the single line carries the full 3,410,000. M1
    recorded `Σ ThTien == TgTCThue` as unconditionally exact. Applied that way
    this invoice is flagged — a false positive on a real, valid,
    tax-authority-signed document. Drop the non-zero guard and this goes red.
    """
    non_vat = invoice(
        template_no="2", subtotal=Decimal("0"), vat_amount=Decimal("0"),
        total_amount=Decimal("3410000"),
        lines=[InvoiceLine(item_kind=1, amount=Decimal("3410000"))],
    )
    assert validation.validate(non_vat) == []


def test_vat_total_check_skipped_when_any_line_omits_line_vat():
    """MUTATION GUARD — spec §2.3, second half.

    `dienluc_gtgt` and `phongvu_gtgt` state TSuat per line but NOT TThue; only
    the header carries VAT. Comparing Σ TThue (= 0) against the header flags
    both. Drop the all-lines-present guard and this goes red.
    """
    header_only_vat = invoice(
        subtotal=Decimal("706522"), vat_amount=Decimal("56522"),
        total_amount=Decimal("763044"),
        lines=[InvoiceLine(item_kind=1, amount=Decimal("706522"),
                           vat_rate=Decimal("0.08"), vat_amount=None)],
    )
    assert findings.VAT_TOTAL_MISMATCH not in codes(header_only_vat)


def test_subtotal_mismatch_is_flagged_when_the_check_does_apply():
    """The guards must not disable the check entirely — that would pass the two
    tests above while validating nothing."""
    wrong = invoice(
        subtotal=Decimal("999"), vat_amount=Decimal("100"), total_amount=Decimal("1100"),
        lines=[InvoiceLine(item_kind=1, amount=Decimal("1000"))],
    )
    assert findings.SUBTOTAL_MISMATCH in codes(wrong)


def test_vat_total_mismatch_is_flagged_when_every_line_states_vat():
    wrong = invoice(
        vat_amount=Decimal("999"), total_amount=Decimal("1999"),
        lines=[InvoiceLine(item_kind=1, amount=Decimal("1000"),
                           vat_rate=Decimal("0.1"), vat_amount=Decimal("100"))],
    )
    assert findings.VAT_TOTAL_MISMATCH in codes(wrong)


def test_validate_never_raises_on_a_sparse_invoice():
    """§3.3: DATA errors travel as findings; EXTRACTION errors belong to the
    parser. validate() must never raise, however little the invoice carries."""
    sparse = InvoiceData(
        template_no="2", invoice_series="X", invoice_number="1",
        issue_date=date(2026, 7, 1), total_amount=Decimal("0"), lines=[],
    )
    assert isinstance(validation.validate(sparse), list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_domain_validation.py -v`
Expected: FAIL — `ImportError: cannot import name 'validation'`

- [ ] **Step 3: Write minimal implementation**

`src/invoice_agent/domain/validation.py`:

```python
"""Deterministic invoice validation — DATA errors only.

WHAT THIS NEVER DOES: raise. Design doc §3.3 splits EXTRACTION errors ("we
misread the document" -> escalate XML -> vision -> manual_queue) from DATA
errors ("the invoice itself is wrong" -> carry a flag to a human).
XmlExtractionError belongs to the parser. Keeping this module incapable of
producing one puts the split on the right side of the module boundary.

EVERY CHECK IS INDEPENDENTLY SKIPPABLE. One absent field must never suppress
the others, and — more importantly — must never invent a finding. See the two
guards in §2.3 of the spec, which exist because the obvious version of these
rules flags 3 of 8 real, valid, tax-authority-signed invoices.
"""

from collections.abc import Sequence
from decimal import Decimal

from invoice_agent.domain import findings, money, taxcode
from invoice_agent.extraction import xml_schema as sch
from invoice_agent.extraction.models import InvoiceData, InvoiceLine

# Measured 2026-07-28 on the 8 real fixtures (spec §2.2):
#   max |SLuong x DGia - ThTien| = 0.01   (3 x 398181.33 = 1194543.99, ThTien = 1194544
#                                          — the seller divided the unit price back out
#                                          of a rounded line total)
#   max |ThTien x TSuat - TThue| = 0.48   (tax rounded to the đồng, deviating BOTH ways)
# Rounded up to a round number; the measurement is the justification, the
# rounding is headroom.
LINE_AMOUNT_TOLERANCE = Decimal("0.01")
LINE_VAT_TOLERANCE = Decimal("0.50")


def _goods_lines(lines: Sequence[InvoiceLine]) -> list[InvoiceLine]:
    return [ln for ln in lines if ln.item_kind != sch.ITEM_KIND_NOTE]


def validate(invoice: InvoiceData) -> list[findings.Finding]:
    out: list[findings.Finding] = []
    goods = _goods_lines(invoice.lines)
    subtotal = money.goods_subtotal(invoice.lines)
    vat_total = invoice.vat_amount or Decimal("0")

    # 1. Header arithmetic — the measured 8/8 rule (spec §2.1). Exact.
    if subtotal + vat_total != invoice.total_amount:
        out.append(findings.make(
            findings.ARITHMETIC_MISMATCH,
            goods_subtotal=subtotal, vat_total=vat_total, total_amount=invoice.total_amount,
        ))

    # 2. Subtotal — ONLY when TgTCThue is stated AND non-zero.
    #    A non-VAT invoice (KHMSHDon=2) reports TgTCThue = 0 while its lines
    #    carry the full amount; comparing them flags a valid invoice.
    if invoice.subtotal:
        if subtotal != invoice.subtotal:
            out.append(findings.make(
                findings.SUBTOTAL_MISMATCH,
                computed=subtotal, declared=invoice.subtotal,
            ))

    # 3. VAT total — ONLY when EVERY goods line states TThue.
    #    dienluc_ and phongvu_ state TSuat per line but not TThue; only the
    #    header has VAT, so Σ TThue is 0 and the comparison is meaningless.
    if invoice.vat_amount is not None and goods and all(
        ln.vat_amount is not None for ln in goods
    ):
        line_vat = sum((ln.vat_amount or Decimal("0") for ln in goods), Decimal("0"))
        if line_vat != invoice.vat_amount:
            out.append(findings.make(
                findings.VAT_TOTAL_MISMATCH,
                computed=line_vat, declared=invoice.vat_amount,
            ))

    out.extend(_check_lines(goods))
    out.extend(_check_words(invoice))
    out.extend(_check_tax_codes(invoice))
    if any(ln.item_kind is None for ln in invoice.lines):
        out.append(findings.make(findings.ITEM_KIND_MISSING))
    return out
```

Add the three helpers below `validate` in the same file:

```python
def _check_lines(goods: Sequence[InvoiceLine]) -> list[findings.Finding]:
    out: list[findings.Finding] = []
    for index, ln in enumerate(goods, start=1):
        if ln.quantity is not None and ln.unit_price is not None and ln.amount is not None:
            if abs(ln.quantity * ln.unit_price - ln.amount) > LINE_AMOUNT_TOLERANCE:
                out.append(findings.make(
                    findings.LINE_AMOUNT_MISMATCH, line_no=index,
                    quantity=ln.quantity, unit_price=ln.unit_price, amount=ln.amount,
                ))
        if ln.amount is not None and ln.vat_rate is not None and ln.vat_amount is not None:
            if abs(ln.amount * ln.vat_rate - ln.vat_amount) > LINE_VAT_TOLERANCE:
                out.append(findings.make(
                    findings.LINE_VAT_MISMATCH, line_no=index,
                    amount=ln.amount, vat_rate=ln.vat_rate, vat_amount=ln.vat_amount,
                ))
    return out


def _check_words(invoice: InvoiceData) -> list[findings.Finding]:
    """TgTTTBChu is a second, independent encoding of the total (§10.3), so a
    disagreement is a real finding. An UNPARSEABLE value is not: returning None
    means we could not read it, and flagging that would punish vendors for
    phrasing rather than for arithmetic."""
    parsed = money.parse_amount_words(invoice.total_in_words)
    if parsed is not None and parsed != invoice.total_amount:
        return [findings.make(
            findings.WORDS_DIGITS_MISMATCH,
            words=invoice.total_in_words, parsed=parsed, digits=invoice.total_amount,
        )]
    return []


def _check_tax_codes(invoice: InvoiceData) -> list[findings.Finding]:
    """Seller MST is required — measured present 8/8, and M2-A makes the column
    NOT NULL. Buyer MST is never a real value in the corpus, so absence there
    is not a finding."""
    out = []
    seller = taxcode.check_tax_code(invoice.seller_tax_code, required=True)
    if seller is not None:
        out.append(seller)
    buyer = taxcode.check_tax_code(invoice.buyer_tax_code)
    if buyer is not None:
        out.append(buyer)
    return out
```

Note the helpers are referenced by `validate` before they appear in the file — that is fine at module
scope, since the names resolve at call time, not definition time.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_domain_validation.py -v`
Expected: PASS (7 tests)

Then verify both §2.3 guards are load-bearing. Temporarily change `if invoice.subtotal:` to `if invoice.subtotal is not None:` → `test_subtotal_check_skipped_when_subtotal_is_zero` must go **red**. Restore. Then delete the `all(ln.vat_amount is not None ...)` clause → `test_vat_total_check_skipped_when_any_line_omits_line_vat` must go **red**. Restore.

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/domain/validation.py tests/test_domain_validation.py
git commit -m "feat(domain): validate() with the two conditional guards

Corrects M1's record: Σ ThTien == TgTCThue and Σ TThue == TgTThue are
exact only WHERE THEY APPLY. Unconditional, they flag 3 of 8 real, valid,
tax-authority-signed invoices. Both guards mutation-verified."
```

---

### Task 6: `validation.py` — line tolerances, words, `item_kind`

**Files:**
- Modify: `tests/test_domain_validation.py`

**Interfaces:** consumes Task 5's `validate`; adds no new functions.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_domain_validation.py`:

```python
def test_rounded_unit_price_is_tolerated():
    """MUTATION GUARD. Real case measured 2026-07-28 in lienket_gtgt_7dong:
    3 x 398181.33 = 1194543.99 but ThTien = 1194544. The seller divided the
    unit price back out of a rounded line total. Set the tolerance to 0 and
    this valid invoice is flagged."""
    inv = invoice(
        total_amount=Decimal("1194544"), vat_amount=Decimal("0"),
        lines=[InvoiceLine(item_kind=1, quantity=Decimal("3"),
                           unit_price=Decimal("398181.33"), amount=Decimal("1194544"))],
    )
    assert findings.LINE_AMOUNT_MISMATCH not in codes(inv)


def test_line_amount_error_beyond_tolerance_is_flagged():
    inv = invoice(
        total_amount=Decimal("500"), vat_amount=Decimal("0"),
        lines=[InvoiceLine(item_kind=1, quantity=Decimal("2"),
                           unit_price=Decimal("100"), amount=Decimal("500"))],
    )
    assert findings.LINE_AMOUNT_MISMATCH in codes(inv)


def test_line_vat_rounding_is_tolerated():
    """Measured max deviation 0.48, in BOTH directions — tax is rounded to the
    đồng. 1000 x 0.10 = 100, and a stated 100.4 is the same invoice."""
    inv = invoice(
        vat_amount=Decimal("100.4"), total_amount=Decimal("1100.4"),
        lines=[InvoiceLine(item_kind=1, amount=Decimal("1000"),
                           vat_rate=Decimal("0.10"), vat_amount=Decimal("100.4"))],
    )
    assert findings.LINE_VAT_MISMATCH not in codes(inv)


def test_wrong_vat_rate_is_flagged():
    """The 3% defect group 'ghi 8% nhưng tính 10%'."""
    inv = invoice(
        vat_amount=Decimal("100"), total_amount=Decimal("1100"),
        lines=[InvoiceLine(item_kind=1, amount=Decimal("1000"),
                           vat_rate=Decimal("0.08"), vat_amount=Decimal("100"))],
    )
    assert findings.LINE_VAT_MISMATCH in codes(inv)


def test_words_disagreeing_with_digits_is_flagged():
    """The 2% defect group 'chữ-vs-số lệch'."""
    inv = invoice(total_in_words="Chín mươi nghìn đồng")  # 90000 vs total 1100
    assert findings.WORDS_DIGITS_MISMATCH in codes(inv)


def test_words_matching_digits_produces_nothing():
    inv = invoice(total_amount=Decimal("90000"), vat_amount=Decimal("0"),
                  lines=[InvoiceLine(item_kind=1, amount=Decimal("90000"))],
                  total_in_words="Chín mươi nghìn đồng")
    assert validation.validate(inv) == []


def test_unparseable_words_are_not_a_finding():
    """None means 'could not read', which is not the invoice's fault."""
    inv = invoice(total_in_words="viết tay không đọc được")
    assert findings.WORDS_DIGITS_MISMATCH not in codes(inv)


def test_missing_item_kind_raises_an_info_flag_but_still_sums():
    """Spec §5.4: include as goods so the arithmetic stays right, and record the
    assumption so a reviewer can see it."""
    inv = invoice(lines=[InvoiceLine(item_kind=None, amount=Decimal("1000"))])
    result = codes(inv)
    assert findings.ITEM_KIND_MISSING in result
    assert findings.ARITHMETIC_MISMATCH not in result


def test_bad_seller_tax_code_is_flagged():
    assert findings.TAX_CODE_CHECKSUM_INVALID in codes(invoice(seller_tax_code="0100000004"))


def test_missing_seller_tax_code_is_flagged():
    assert findings.TAX_CODE_MISSING in codes(invoice(seller_tax_code=None))


def test_missing_buyer_tax_code_is_not_flagged():
    assert validation.validate(invoice(buyer_tax_code=None)) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_domain_validation.py -v`
Expected: some PASS (Task 5 already implemented the checks), any FAIL points at a real gap — fix `validation.py` until green.

- [ ] **Step 3: Adjust the implementation if needed**

No new code is expected. If `test_line_vat_rounding_is_tolerated` fails, confirm `LINE_VAT_TOLERANCE` is `Decimal("0.50")` and the comparison uses `>` not `>=`.

- [ ] **Step 4: Run the full domain suite**

Run: `.venv/bin/python -m pytest tests/test_domain_*.py -v`
Expected: PASS

Mutation: set `LINE_AMOUNT_TOLERANCE = Decimal("0")` → `test_rounded_unit_price_is_tolerated` red. Restore.

- [ ] **Step 5: Commit**

```bash
git add tests/test_domain_validation.py src/invoice_agent/domain/validation.py
git commit -m "test(domain): line tolerances, words-vs-digits, item_kind flag"
```

---

### Task 7: The anchor — 8 real invoices, zero findings — and the purity guard

The single most valuable test in this milestone.

**Files:**
- Create: `tests/test_domain_real_invoices.py`

**Interfaces:** consumes `validation.validate`, `money.parse_amount_words`, `taxcode.check_tax_code`

- [ ] **Step 1: Write the failing test**

```python
"""The anchor: real invoices must produce NO findings.

Design doc §10.4 makes flag false-positive rate the HEADLINE metric, because it
measures the thing that decides whether the system gets used — reviewer fatigue.
These 8 files are real, valid, tax-authority-signed invoices. Every finding here
is a false positive, and this test measures that today, before a single
synthetic invoice exists.

M1 measured that the NAIVE rule flags 6 of 8, and spec §2.3 measured that M1's
own subtotal/VAT rules flag 3 of 8. Both were caught by exactly this test.
"""

import pathlib

import pytest

from invoice_agent.domain import money, validation
from invoice_agent.extraction.xml_parser import parse_invoice_xml

FIXTURES = sorted(pathlib.Path("tests/fixtures/real_samples").glob("*.xml"))


def test_fixtures_are_present():
    """Without this, the parametrized tests below would silently collect zero
    cases and pass — a green suite proving nothing."""
    assert len(FIXTURES) == 8


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_real_invoice_produces_no_findings(path):
    invoice = parse_invoice_xml(path.read_bytes())
    result = validation.validate(invoice)
    assert result == [], (
        f"{path.name} is a real, valid invoice but produced "
        f"{[f.code for f in result]} — every one is a false positive"
    )


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_real_amount_words_agree_with_the_digits(path):
    """TgTTTBChu as a second independent encoding (§10.3), on real data."""
    invoice = parse_invoice_xml(path.read_bytes())
    parsed = money.parse_amount_words(invoice.total_in_words)
    assert parsed is not None, f"{path.name}: could not parse {invoice.total_in_words!r}"
    assert parsed == invoice.total_amount


def test_domain_imports_nothing_with_io():
    """Spec §7 criterion 8. Purity is the interface: these functions run inside
    M4's `SELECT ... FOR UPDATE`, and an accidental query there is a deadlock.
    Asserted rather than intended."""
    import pathlib as pl

    banned = ("sqlalchemy", "psycopg", "invoice_agent.db", "requests", "httpx", "litellm")
    offenders = []
    for module in pl.Path("src/invoice_agent/domain").glob("*.py"):
        source = module.read_text(encoding="utf-8")
        for name in banned:
            if f"import {name}" in source or f"from {name}" in source:
                offenders.append(f"{module.name} imports {name}")
    assert not offenders, offenders
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_domain_real_invoices.py -v`
Expected: this is the moment of truth. If any fixture produces findings, **the rule is wrong, not the fixture** — every one of these invoices is genuinely valid. Read the finding's `detail`, fix `validation.py`, and record what was learned in the commit message.

- [ ] **Step 3: Fix whatever the anchor caught**

Do not weaken a check to make this pass. Narrow its *guard* — the two in §2.3 are exactly that pattern. If a genuinely new condition is discovered, add it with a comment naming the fixture that proved it.

- [ ] **Step 4: Run the full suite**

Run: `make check`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add tests/test_domain_real_invoices.py
git commit -m "test(domain): 8/8 real invoices produce zero findings

The headline false-positive metric, measurable before any corpus exists.
Plus a test asserting domain/ imports nothing with I/O."
```

---

### Task 8: `accounting.py`

**Files:**
- Create: `src/invoice_agent/domain/accounting.py`, `tests/test_domain_accounting.py`

**Interfaces:**
- Consumes: `money.goods_subtotal` (Task 2)
- Produces: `CategoryAccounts(debit_account, debit_name, vat_account, vat_name, credit_account, credit_name)`, `JournalLine(account_code, account_name, debit, credit, description)`, `build_journal_entry(invoice, accounts) -> list[JournalLine]`

- [ ] **Step 1: Write the failing test**

```python
"""Journal entry construction (design doc §2.7)."""

from datetime import date
from decimal import Decimal

from invoice_agent.domain.accounting import CategoryAccounts, build_journal_entry
from invoice_agent.extraction.models import InvoiceData, InvoiceLine

# Real TT133 numbers, seeded by scripts/seed_reference.py.
OFFICE = CategoryAccounts(
    debit_account="6422", debit_name="Chi phí quản lý doanh nghiệp",
    vat_account="1331", vat_name="Thuế GTGT được khấu trừ",
    credit_account="331", credit_name="Phải trả cho người bán",
)


def inv(**kw) -> InvoiceData:
    base = dict(
        template_no="1", invoice_series="C26TAA", invoice_number="1",
        issue_date=date(2026, 7, 1), seller_tax_code="0100000003",
        lines=[InvoiceLine(item_kind=1, amount=Decimal("3250000"))],
        vat_amount=Decimal("260000"), total_amount=Decimal("3510000"),
    )
    base.update(kw)
    return InvoiceData(**base)


def test_builds_the_textbook_office_supplies_entry():
    """Design doc §2.7 gives this exact entry:
    Nợ 6422 3.250.000 / Nợ 1331 260.000 / Có 331 3.510.000"""
    lines = build_journal_entry(inv(), OFFICE)
    assert [(x.account_code, x.debit, x.credit) for x in lines] == [
        ("6422", Decimal("3250000"), Decimal("0")),
        ("1331", Decimal("260000"), Decimal("0")),
        ("331", Decimal("0"), Decimal("3510000")),
    ]


def test_entry_balances():
    lines = build_journal_entry(inv(), OFFICE)
    assert sum(x.debit for x in lines) == sum(x.credit for x in lines)


def test_journal_entry_balances_on_non_vat_invoice():
    """MUTATION GUARD. Measured spec §2.3: a non-VAT invoice reports
    TgTCThue = 0 while its line carries the full amount. Building the debit
    from `invoice.subtotal` emits `Nợ 6422 0 / Có 331 3.410.000` — unbalanced,
    and refused by ck_journal_balanced. Must use money.goods_subtotal."""
    non_vat = inv(
        template_no="2", subtotal=Decimal("0"), vat_amount=Decimal("0"),
        total_amount=Decimal("3410000"),
        lines=[InvoiceLine(item_kind=1, amount=Decimal("3410000"))],
    )
    lines = build_journal_entry(non_vat, OFFICE)
    assert sum(x.debit for x in lines) == sum(x.credit for x in lines) == Decimal("3410000")


def test_no_vat_line_when_there_is_no_vat():
    non_vat = inv(vat_amount=Decimal("0"), total_amount=Decimal("3250000"))
    assert [x.account_code for x in build_journal_entry(non_vat, OFFICE)] == ["6422", "331"]


def test_every_line_is_one_sided():
    """journal_entry_lines has CHECK (debit = 0 OR credit = 0)."""
    for x in build_journal_entry(inv(), OFFICE):
        assert x.debit == 0 or x.credit == 0


def test_no_negative_amounts():
    """journal_entry_lines has CHECK (debit >= 0 AND credit >= 0)."""
    for x in build_journal_entry(inv(), OFFICE):
        assert x.debit >= 0 and x.credit >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_domain_accounting.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
"""Journal-entry construction (design doc §2.7). Pure — nothing is posted here.

A real office-supplies entry:

    Nợ 6422   3.250.000        expense, pre-tax
    Nợ 1331     260.000        deductible input VAT
        Có 331          3.510.000   payable to the supplier

Account numbers are real TT133/2016/TT-BTC codes and arrive as an argument from
the seeded `expense_categories` row — this module reads no database.
"""

from dataclasses import dataclass
from decimal import Decimal

from invoice_agent.domain import money
from invoice_agent.extraction.models import InvoiceData

ZERO = Decimal("0")


@dataclass(frozen=True)
class CategoryAccounts:
    debit_account: str
    debit_name: str
    vat_account: str | None
    vat_name: str | None
    credit_account: str
    credit_name: str


@dataclass(frozen=True)
class JournalLine:
    account_code: str
    account_name: str
    debit: Decimal
    credit: Decimal
    description: str | None = None


def build_journal_entry(invoice: InvoiceData, accounts: CategoryAccounts) -> list[JournalLine]:
    """Debit expense (+ VAT), credit payable. Always balances by construction.

    The expense debit comes from `money.goods_subtotal`, NOT `invoice.subtotal`.
    Measured 2026-07-28 (spec §2.3): a non-VAT invoice reports TgTCThue = 0
    while its lines carry the full amount, so building from the header would
    produce a zero debit against a real credit — unbalanced, and rejected by
    `ck_journal_balanced`. That constraint would catch it; this comment is so it
    never gets that far.
    """
    subtotal = money.goods_subtotal(invoice.lines)
    vat = invoice.vat_amount or ZERO

    lines = [JournalLine(accounts.debit_account, accounts.debit_name, subtotal, ZERO)]
    if vat > ZERO and accounts.vat_account is not None:
        lines.append(
            JournalLine(accounts.vat_account, accounts.vat_name or "", vat, ZERO)
        )
    lines.append(
        JournalLine(accounts.credit_account, accounts.credit_name, ZERO, subtotal + vat)
    )
    return lines
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_domain_accounting.py -v`
Expected: PASS (6 tests). Mutation: change `subtotal = money.goods_subtotal(...)` to `invoice.subtotal or ZERO` → `test_journal_entry_balances_on_non_vat_invoice` red. Restore.

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/domain/accounting.py tests/test_domain_accounting.py
git commit -m "feat(domain): journal entry builder, balanced by construction"
```

---

### Task 9: `budget.py`

**Files:**
- Create: `src/invoice_agent/domain/budget.py`, `tests/test_domain_budget.py`

**Interfaces:**
- Consumes: `findings` (Task 1)
- Produces: `BudgetInput(invoice_id, issue_date, invoice_series, invoice_number, amount)`, `Consumption(invoice_id, amount, balance_before, balance_after, sequence_no, findings)`, `consume(limit, warn_pct, invoices) -> list[Consumption]`

- [ ] **Step 1: Write the failing test**

```python
"""Serial budget reconciliation arithmetic (design doc §4.3, §2.6)."""

import random
from datetime import date
from decimal import Decimal

from invoice_agent.domain import findings
from invoice_agent.domain.budget import BudgetInput, consume

LIMIT = Decimal("1000")
WARN = Decimal("0.8")


def item(n, amount, day=1):
    return BudgetInput(
        invoice_id=n, issue_date=date(2026, 7, day),
        invoice_series="C26TAA", invoice_number=f"{n:08d}", amount=Decimal(amount),
    )


def test_balances_chain_correctly():
    result = consume(LIMIT, WARN, [item(1, "300"), item(2, "400")])
    assert [(c.balance_before, c.balance_after) for c in result] == [
        (Decimal("1000"), Decimal("700")),
        (Decimal("700"), Decimal("300")),
    ]
    assert [c.sequence_no for c in result] == [1, 2]


def test_consume_is_order_independent():
    """MUTATION GUARD, and the whole reason this function sorts internally.

    M4's test_budget_serialization.py shuffles arrival order and asserts the
    balance_after chain is byte-identical and the SAME invoice breaches. A
    consume() that trusted the caller's order would make that unprovable.
    Canonical order is (issue_date, invoice_series, invoice_number) — NOT
    arrival order (design doc §2.6).
    """
    items = [item(1, "300", day=1), item(2, "400", day=2), item(3, "500", day=3)]
    expected = [c.balance_after for c in consume(LIMIT, WARN, items)]
    breached = [c.invoice_id for c in consume(LIMIT, WARN, items)
                if any(f.code == findings.BUDGET_BREACH for f in c.findings)]
    rng = random.Random(42)
    for _ in range(10):
        shuffled = items[:]
        rng.shuffle(shuffled)
        got = consume(LIMIT, WARN, shuffled)
        assert [c.balance_after for c in got] == expected
        assert [c.invoice_id for c in got
                if any(f.code == findings.BUDGET_BREACH for f in c.findings)] == breached


def test_breach_is_flagged_when_balance_goes_negative():
    result = consume(LIMIT, WARN, [item(1, "600"), item(2, "600")])
    assert not result[0].findings
    assert [f.code for f in result[1].findings] == [findings.BUDGET_BREACH]


def test_warning_fires_at_the_threshold_but_not_before():
    """warn_pct=0.8 means warn once 80% of the limit is gone, i.e. when the
    remaining balance falls to 200 or below."""
    result = consume(LIMIT, WARN, [item(1, "700"), item(2, "100")])
    assert not result[0].findings                       # balance 300, 30% left
    assert [f.code for f in result[1].findings] == [findings.BUDGET_WARNING]  # balance 200


def test_breach_and_warning_are_mutually_exclusive():
    """A breaching invoice gets BUDGET_BREACH only — a reviewer should not see
    two findings saying the same thing."""
    result = consume(LIMIT, WARN, [item(1, "1500")])
    assert [f.code for f in result[0].findings] == [findings.BUDGET_BREACH]


def test_empty_input_returns_empty():
    assert consume(LIMIT, WARN, []) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_domain_budget.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
"""Budget consumption arithmetic (design doc §4.3, §2.6). Pure.

`consume` SORTS ITS INPUT. That is not a convenience — it is the property M4's
test_budget_serialization.py exists to prove: shuffle the arrival order of a
batch and the same invoice must still breach, with a byte-identical
balance_after chain. Canonical order is (issue_date, invoice_series,
invoice_number), NOT the order files happened to arrive in.

Locking (`SELECT ... FOR UPDATE`) and writing `budget_consumption` rows are
M4.5's job. This function only does the arithmetic.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from invoice_agent.domain import findings

ZERO = Decimal("0")


@dataclass(frozen=True)
class BudgetInput:
    invoice_id: Any
    issue_date: date
    invoice_series: str
    invoice_number: str
    amount: Decimal


@dataclass(frozen=True)
class Consumption:
    invoice_id: Any
    amount: Decimal
    balance_before: Decimal
    balance_after: Decimal
    sequence_no: int
    findings: list[findings.Finding]


def consume(
    limit: Decimal, warn_pct: Decimal, invoices: Sequence[BudgetInput]
) -> list[Consumption]:
    """Walk the invoices in canonical order, draining the budget."""
    ordered = sorted(
        invoices, key=lambda i: (i.issue_date, i.invoice_series, i.invoice_number)
    )
    # Warn once cumulative spend reaches warn_pct of the limit — i.e. once the
    # remaining balance falls to limit x (1 - warn_pct). With the seeded default
    # of 0.8 that is "80% or more of the budget is gone".
    warn_at = limit * (Decimal("1") - warn_pct)

    out: list[Consumption] = []
    balance = limit
    for sequence_no, inv in enumerate(ordered, start=1):
        before = balance
        balance = before - inv.amount
        found: list[findings.Finding] = []
        if balance < ZERO:
            found.append(findings.make(
                findings.BUDGET_BREACH,
                limit=limit, balance_before=before, balance_after=balance,
            ))
        elif balance <= warn_at:
            found.append(findings.make(
                findings.BUDGET_WARNING,
                limit=limit, balance_after=balance, warn_threshold_pct=warn_pct,
            ))
        out.append(Consumption(inv.invoice_id, inv.amount, before, balance, sequence_no, found))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_domain_budget.py -v`
Expected: PASS (6 tests). Mutation: replace `ordered = sorted(...)` with `ordered = list(invoices)` → `test_consume_is_order_independent` red. Restore.

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/domain/budget.py tests/test_domain_budget.py
git commit -m "feat(domain): serial budget consumption, order-independent

consume() sorts by (issue_date, invoice_series, invoice_number) itself.
Trusting the caller's order would make M4's serialization test unprovable."
```

---

### Task 10: `anomaly.py`

**Files:**
- Create: `src/invoice_agent/domain/anomaly.py`, `tests/test_domain_anomaly.py`

**Interfaces:**
- Consumes: `findings` (Task 1)
- Produces: `VendorStats(invoice_count, avg_amount, stddev_amount)`, `InvoiceKey(seller_tax_code, invoice_number, issue_date, total_amount)`, `new_vendor(stats)`, `price_outlier(amount, stats)`, `near_duplicate(candidate, priors)`

- [ ] **Step 1: Write the failing test**

```python
"""Structural anomaly signals. No fuzzy matching — see the module docstring."""

from datetime import date
from decimal import Decimal

from invoice_agent.domain import findings
from invoice_agent.domain.anomaly import (
    InvoiceKey, VendorStats, near_duplicate, new_vendor, price_outlier,
)

KNOWN = VendorStats(invoice_count=50, avg_amount=Decimal("1000"), stddev_amount=Decimal("100"))


def key(number="00001234", day=1, amount="1000", seller="0100000003"):
    return InvoiceKey(seller_tax_code=seller, invoice_number=number,
                      issue_date=date(2026, 7, day), total_amount=Decimal(amount))


def test_unknown_vendor_is_flagged():
    assert new_vendor(None).code == findings.NEW_VENDOR
    assert new_vendor(VendorStats(0, None, None)).code == findings.NEW_VENDOR


def test_known_vendor_is_not_flagged():
    assert new_vendor(KNOWN) is None


def test_price_far_from_the_mean_is_flagged():
    """The 2% defect group: 10x the usual price."""
    assert price_outlier(Decimal("10000"), KNOWN).code == findings.PRICE_OUTLIER


def test_ordinary_price_is_not_flagged():
    assert price_outlier(Decimal("1050"), KNOWN) is None


def test_no_outlier_verdict_without_enough_history():
    """A vendor with two invoices has no meaningful distribution. Computing a
    z-score from one is how a clean invoice gets flagged."""
    thin = VendorStats(invoice_count=2, avg_amount=Decimal("1000"), stddev_amount=Decimal("10"))
    assert price_outlier(Decimal("99999"), thin) is None


def test_no_outlier_verdict_when_stddev_is_zero():
    """Every prior invoice identical -> stddev 0 -> division by zero, and any
    different amount would look infinitely anomalous."""
    flat = VendorStats(invoice_count=50, avg_amount=Decimal("1000"), stddev_amount=Decimal("0"))
    assert price_outlier(Decimal("1001"), flat) is None


def test_near_duplicate_same_seller_same_total_different_number():
    """The 'wow' case: a vendor re-issues an almost identical invoice under a
    new number to be paid twice."""
    result = near_duplicate(key(number="00009999", day=2), [key(number="00001234", day=1)])
    assert result.code == findings.NEAR_DUPLICATE


def test_not_a_duplicate_when_the_seller_differs():
    other = key(seller="0311110005", number="00001234")
    assert near_duplicate(key(number="00009999"), [other]) is None


def test_not_a_duplicate_when_the_total_differs():
    assert near_duplicate(key(number="00009999", amount="2000"), [key()]) is None


def test_same_invoice_number_is_not_a_NEAR_duplicate():
    """An identical natural key is an EXACT duplicate — the UNIQUE index and
    DUPLICATE_INVOICE own that case, not this one."""
    assert near_duplicate(key(), [key()]) is None


def test_outside_the_window_is_not_flagged():
    from invoice_agent.domain import anomaly

    far = key(number="00009999", day=1 + anomaly.NEAR_DUP_WINDOW_DAYS + 1)
    assert near_duplicate(far, [key(day=1)]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_domain_anomaly.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
"""Structural anomaly signals (design doc §3.2 `signals`, §7 entity memory).

STRUCTURAL ONLY — no fuzzy string matching, no similarity library. Decision
taken 2026-07-28: the corpus that would justify a text-similarity threshold does
not exist until M2-C, and the headline metric is flag FALSE-POSITIVE rate, so an
untuned similarity channel costs more than it earns. If M6's eval shows fraud
recall < 0.90 with structural signals alone, a description channel is added THEN,
with before/after numbers (principle 2). If it is dropped, the reason is
recorded — that is portfolio content, not a failure.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from invoice_agent.domain import findings

# ⚠️ PROVISIONAL AND UNMEASURED. CLAUDE.md requires every non-obvious constant
# to carry date + measurement + report file name. These three cannot: the corpus
# that would justify them is M2-C, and no measurement on the 8 real fixtures can
# produce them. They are set at M6 from docs/eval-report.md. Saying so is the
# honest option; inventing a justification is not.
NEAR_DUP_WINDOW_DAYS = 7
PRICE_OUTLIER_Z = Decimal("3")
PRICE_OUTLIER_MIN_INVOICES = 5


@dataclass(frozen=True)
class VendorStats:
    invoice_count: int
    avg_amount: Decimal | None
    stddev_amount: Decimal | None


@dataclass(frozen=True)
class InvoiceKey:
    seller_tax_code: str
    invoice_number: str
    issue_date: date
    total_amount: Decimal


def new_vendor(stats: VendorStats | None) -> findings.Finding | None:
    """Never transacted before. Carries `raised_by='vendor_memory'` — the
    producer as a reviewer understands it, not the module it lives in."""
    if stats is None or stats.invoice_count == 0:
        return findings.make(findings.NEW_VENDOR)
    return None


def price_outlier(amount: Decimal, stats: VendorStats) -> findings.Finding | None:
    """Z-score against the vendor's own history.

    Returns None when the history is too thin or has no spread. Both guards
    exist to protect the false-positive rate: a vendor with two invoices has no
    distribution, and a zero stddev makes every different amount look infinitely
    anomalous (and divides by zero).
    """
    if (
        stats.invoice_count < PRICE_OUTLIER_MIN_INVOICES
        or not stats.avg_amount
        or not stats.stddev_amount
    ):
        return None
    z = abs(amount - stats.avg_amount) / stats.stddev_amount
    if z >= PRICE_OUTLIER_Z:
        return findings.make(
            findings.PRICE_OUTLIER, amount=amount, avg=stats.avg_amount, z_score=z
        )
    return None


def near_duplicate(
    candidate: InvoiceKey, priors: Sequence[InvoiceKey]
) -> findings.Finding | None:
    """Same seller, same total, close in time, DIFFERENT number.

    An identical natural key is an EXACT duplicate, owned by the UNIQUE index
    and DUPLICATE_INVOICE. This is the softer case: a vendor re-issuing an
    almost identical invoice under a new number to be paid twice.
    """
    for prior in priors:
        if (
            prior.seller_tax_code == candidate.seller_tax_code
            and prior.total_amount == candidate.total_amount
            and prior.invoice_number != candidate.invoice_number
            and abs((candidate.issue_date - prior.issue_date).days) <= NEAR_DUP_WINDOW_DAYS
        ):
            return findings.make(
                findings.NEAR_DUPLICATE,
                matched_invoice_number=prior.invoice_number,
                total_amount=candidate.total_amount,
            )
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_domain_anomaly.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/invoice_agent/domain/anomaly.py tests/test_domain_anomaly.py
git commit -m "feat(domain): structural anomaly signals

Three thresholds are provisional and say so in code — the corpus that
would justify them is M2-C, and they get set at M6 from the eval report."
```

---

### Task 11: Gate verification

**Files:**
- Modify: `docs/Plan_Project_F_2026-07-25.md` (§7 M2 row)
- Create: `docs/notes/2026-07-28-m2b-domain-core.md`

- [ ] **Step 1: Confirm the test count clears the gate**

```bash
.venv/bin/python -m pytest tests/test_domain_*.py -q | tail -2
```
Expected: **≥60** domain tests. If short, the gap is real coverage, not padding — add cases for shapes named in spec §2 that have no test yet.

- [ ] **Step 2: Re-run every mutation from scratch**

M1 closed on the *claim* that guards were mutation-verified and the exit review found four that were not. Re-break each and record the true result:

| # | Mutation | Guard |
|---|---|---|
| 1 | `if invoice.subtotal:` → `is not None` | `test_subtotal_check_skipped_when_subtotal_is_zero` |
| 2 | delete the `all(ln.vat_amount is not None ...)` clause | `test_vat_total_check_skipped_when_any_line_omits_line_vat` |
| 3 | `goods_subtotal` stops excluding `ITEM_KIND_NOTE` | `test_note_lines_are_not_money` |
| 4 | `line_amount` drops the `ext['Amount']` fallback | `test_vnpt_amount_comes_from_ext` |
| 5 | `LINE_AMOUNT_TOLERANCE = 0` | `test_rounded_unit_price_is_tolerated` |
| 6 | remove `mốt` from `_UNIT_VARIANTS` | `test_parses_all_eight_real_amount_words` |
| 7 | `taxcode` rejects the 12-digit shape | `test_household_tax_code_accepted_without_checksum` |
| 8 | alter a checksum weight | `test_real_tax_codes_pass_checksum` |
| 9 | `budget.consume` uses caller order | `test_consume_is_order_independent` |
| 10 | `build_journal_entry` uses `invoice.subtotal` | `test_journal_entry_balances_on_non_vat_invoice` |

Every one must go red. **If one does not, that is the finding — fix the guard, do not fix the record.**

- [ ] **Step 3: Full verification**

```bash
make check
```
Expected: ruff clean, mypy clean, 160 baseline + domain tests, all green.

- [ ] **Step 4: Write the note**

`docs/notes/2026-07-28-m2b-domain-core.md` records: the 8/8 zero-findings anchor result; the 10 mutation results as actually observed; the §2.3 correction to M1's record; and the three provisional constants with the explicit statement that they are unmeasured and set at M6.

- [ ] **Step 5: Update the plan and commit**

In §7, update the M2 row: M2-A and M2-B done, M2-C open. Record the domain test count.

```bash
git add docs/ && git commit -m "docs(m2b): close domain core, 10 guards mutation-verified

8/8 real invoices produce zero findings — the headline false-positive
metric, measured before the corpus exists."
```

---

## Self-Review

**Spec coverage.** §2.1 measured rule → Task 2. §2.2 tolerances → Tasks 5, 6. §2.3 the correction → Task 5 (both guards, both mutation-verified). §2.4 checksum → Task 4. §2.5 amount words → Task 3. §4 safety property → Task 1 (`Finding` is frozen and carries no approval field) + Task 7 (purity test). §5.1 findings → Task 1. §5.2 money → Tasks 2, 3. §5.3 taxcode → Task 4. §5.4 validation → Tasks 5, 6. §5.5 accounting → Task 8. §5.6 budget → Task 9. §5.7 anomaly → Task 10. §6.1 anchor → Task 7. §6.2 all 10 mutations → distributed, re-run in Task 11. §6.3 count → Task 11 Step 1. §7 criteria 1–8 → Tasks 7, 11. §8 open questions → carried in code comments (Tasks 2, 4, 10).

**Ambiguity resolved:** spec §5.1 says the vocabulary is "re-exported" from `db.models` while §7.8 forbids `domain/` importing `db/`. The plan defines it independently in `findings.py` and has `tests/test_domain_findings.py` assert equality — purity preserved, drift still caught. Noted in Global Constraints.

**Type consistency.** `findings.make(code, **detail) -> Finding` used in Tasks 4, 5, 8, 9, 10. `money.goods_subtotal(lines) -> Decimal` defined Task 2, consumed Tasks 5 and 8. `money.parse_amount_words(text) -> Decimal | None` defined Task 3, consumed Task 5. `taxcode.check_tax_code(mst, *, required=False)` defined Task 4, consumed Task 5. `CategoryAccounts` / `JournalLine` are Task 8's only. `BudgetInput` / `Consumption` are Task 9's only. `VendorStats` / `InvoiceKey` are Task 10's only.

**Verified before shipping, not assumed:** Task 3's `parse_amount_words` algorithm was run against all 8 real `TgTTTBChu` values, the 2 unobserved `lăm`/`tư` variants, and 5 unparseable inputs — 15/15 correct. The plan contains working code, not plausible code.

**One thing the implementer will hit:** Task 5 shows `validate` calling `_check_lines`, `_check_words` and `_check_tax_codes`, which are defined below it in the same file. That resolves fine at call time. Ruff's import sorting will also want `findings`, `money`, `taxcode` grouped as first-party — run `.venv/bin/ruff check --fix` if it complains.
