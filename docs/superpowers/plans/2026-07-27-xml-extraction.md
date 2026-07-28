# XML Extraction (M1.3 + M1.4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse real Vietnamese e-invoice XML (QĐ-1450) into a typed `InvoiceData` deterministically, closing the last open condition of the M1 gate.

**Architecture:** Three modules under `src/invoice_agent/extraction/`. `xml_schema.py` holds every element path (anchored at `HDon`, never `.//`), the `TTKhac` extension keys, the coercion primitives, and `XmlExtractionError`. `models.py` holds the `InvoiceData`/`InvoiceLine` contract. `xml_parser.py` locates `HDon` and assembles the model with explicit `findtext` calls. The parser raises on anything it cannot *read*; it never judges whether the data is *correct* — that is M2.6's `validate` node.

**Tech Stack:** Python 3.13 · `defusedxml` (untrusted input) · `pydantic` v2 · `Decimal` throughout · pytest. **No Postgres needed** — every test here is a pure unit test, so `make dev` is not required.

**Spec:** `docs/superpowers/specs/2026-07-27-xml-extraction-design.md`. Read it before starting; it contains the measurements every decision below rests on.

## Global Constraints

- **Language**: all code comments, docstrings, commit messages in **English**. Element names, `TTKhac` keys, and fixture values stay Vietnamese — they are domain data, not prose.
- **Never `float` for money.** `Decimal` only. Real value in the corpus: `21618409.09000000` (8 decimal places).
- **Never `.//` for a field path.** Only for locating `HDon`. `TDiep/TTChung` carries its own `PBan`, its own `MST` (the transmitter's), and its own `SLuong` (the invoice count in the envelope).
- **Empty is not zero.** `<SLuong />` → `None`, never `Decimal(0)`. Three distinct "missing" manifestations exist: value `'0'`, empty element `''`, absent element `None`.
- **Text is verbatim.** No `.strip()`, no whitespace collapsing. `'Ba  triệu năm trăm ngàn đồng./.'` has a double space and a `./.` suffix that M2.6's normalizer must face.
- **`InvoiceData` carries no `category`, `department`, `status`, `classification_confidence`, or any flag/approval field.** At M3.4 it becomes an LLM structured-output schema, and structured output *is* the security boundary (CLAUDE.md).
- **Do not modify anything in `tests/fixtures/real_samples/`.** Fix the parser to match the files, never the reverse.
- **Every non-obvious constant needs a comment naming the date + measurement + source** (CLAUDE.md).
- `line-length = 100`, ruff `E501` ignored. mypy runs over `packages = ["invoice_agent"]` — `src/` only, not `tests/`.
- Run `make check` (ruff + mypy + pytest) before declaring any task complete.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/invoice_agent/extraction/__init__.py` | Package marker. Re-exports the public surface. |
| `src/invoice_agent/extraction/xml_schema.py` | **M1.3.** All element paths, `TTKhac` keys, coercers, `XmlExtractionError`. The anti-circularity artefact — a reviewer must be able to diff it against the M2.1 Jinja template by reading. |
| `src/invoice_agent/extraction/models.py` | `InvoiceData`, `InvoiceLine`. The extraction contract; also the M3.4 vision structured-output schema. |
| `src/invoice_agent/extraction/xml_parser.py` | **M1.4.** `parse_invoice_xml(raw: bytes) -> InvoiceData`. |
| `tests/fixtures/synthetic/tdiep_envelope_distinct.xml` | Envelope values deliberately ≠ invoice values. The real corpus **cannot** catch the `.//` bug (spec §2.3). |
| `tests/fixtures/synthetic/tdiep_two_invoices.xml` | Two `HDon` in one envelope → must raise. |
| `tests/test_xml_schema.py` | Coercer unit tests. |
| `tests/test_xml_parser.py` | 8 real fixtures + golden table + security + sufficiency. |

`XmlExtractionError` lives in `xml_schema.py`, **not** in `xml_parser.py`: the coercers raise it, and `xml_parser` imports `xml_schema`, so defining it in the parser would be a circular import. `xml_parser` re-exports it.

---

### Task 1: Package scaffolding + `defusedxml` dependency

Untrusted vendor XML is the input. Measured 2026-07-27 on Python 3.13.5: stdlib `ElementTree` blocks external entities but **expands internal ones** — 11 nested levels → 4096 chars, a live memory DoS.

**Files:**
- Create: `src/invoice_agent/extraction/__init__.py`
- Modify: `pyproject.toml`
- Modify: `requirements.lock` (regenerated)

**Interfaces:**
- Consumes: nothing.
- Produces: the `invoice_agent.extraction` package; `defusedxml` importable; `types-defusedxml` installed so mypy resolves `defusedxml.ElementTree.fromstring` to return `Element[str]`.

- [ ] **Step 1: Create the package marker**

```python
# src/invoice_agent/extraction/__init__.py
"""Extraction layer — XML now (M1.4), PDF/vision later (M3.4).

Both produce the same contract, `InvoiceData`. That is deliberate: the
enrichment graph's strategy-escalation loop (design doc §3.3) swaps XML for
vision without the downstream nodes noticing.
"""
```

- [ ] **Step 2: Add the dependency**

In `pyproject.toml`, add to `dependencies` (after `langfuse`, in the "floor" group):

```toml
    # Vendor XML is UNTRUSTED input. Measured 2026-07-27 on Python 3.13.5:
    # stdlib xml.etree blocks external entities (XXE -> ParseError) but DOES
    # expand internal ones — a billion-laughs payload at 11 nested levels
    # expanded to 4096 chars, so the real thing is a memory DoS. defusedxml
    # 0.7.1 defaults are forbid_dtd=False/forbid_entities=True/
    # forbid_external=True: both payloads raise EntitiesForbidden while a
    # harmless bare <!DOCTYPE> still parses.
    "defusedxml>=0.7.1",
```

And to `[project.optional-dependencies] dev`:

```toml
    # defusedxml ships no py.typed. With this stub package mypy resolves
    # fromstring() to Element[str]; without it, every call is Any.
    "types-defusedxml>=0.7",
```

- [ ] **Step 3: Install and regenerate the lock**

```bash
uv pip install -e ".[dev]"
make lock
```

- [ ] **Step 4: Verify mypy actually sees the types**

```bash
printf 'from defusedxml.ElementTree import fromstring\nreveal_type(fromstring(b"<a/>"))\n' \
  > /tmp/probe_defused.py
.venv/bin/mypy /tmp/probe_defused.py
```

Expected: `Revealed type is "xml.etree.ElementTree.Element[str]"` and `Success: no issues found`. If it says `Any`, `types-defusedxml` did not install — fix before continuing, because every later mypy check would silently pass.

- [ ] **Step 5: Confirm nothing regressed, then commit**

```bash
make check
git add pyproject.toml requirements.lock src/invoice_agent/extraction/__init__.py
git commit -m "build: add defusedxml — stdlib ET expands internal entities

Measured 2026-07-27 on Python 3.13.5: a billion-laughs payload at 11 nested
levels expands to 4096 chars under xml.etree.ElementTree. Vendor XML is
untrusted input. defusedxml raises EntitiesForbidden for both that and XXE
while still accepting a harmless bare DOCTYPE. types-defusedxml added so mypy
resolves fromstring() to Element[str] instead of Any."
```

---

### Task 2: `xml_schema.py` — paths, keys, and `XmlExtractionError`

**Files:**
- Create: `src/invoice_agent/extraction/xml_schema.py`
- Create: `tests/test_xml_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `XmlExtractionError(Exception)`; path constants `HDON_TAG`, `TDIEP_TAG`, `TDIEP_HDON`, `TTCHUNG`, `NDHDON`, `SELLER`, `BUYER`, `LINES`, `TOTALS`; field-name constants; `EXT_SCOPES: dict[str, str]`; `EXT_NODE`, `EXT_ENTRY`, `EXT_KEY`, `EXT_VALUE`, `EXT_KEY_LINE_AMOUNT`; `ITEM_KIND_GOODS`, `ITEM_KIND_NOTE`, `TEMPLATE_NO_VAT`, `TEMPLATE_NO_SALES`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_xml_schema.py
"""Unit tests for the QĐ-1450 path map and coercers (M1.3).

No Postgres, no fixtures loaded from disk except where a test says so.
"""

from invoice_agent.extraction import xml_schema as sch


def test_no_field_path_uses_descendant_search():
    """`.//` anywhere in a FIELD path is a bug, not a style choice.

    Measured 2026-07-27: `phongvu_gtgt_tdiep_2.0.0.xml` has root `TDiep`, and
    `TDiep/TTChung` carries its own `PBan`, its own `MST` (the transmitter's,
    not the seller's) and its own `SLuong` — which is the COUNT OF INVOICES in
    the envelope, not a line quantity. `.//SLuong` finds that count first.

    This test is the cheap guard. `test_envelope_values_do_not_leak` in
    tests/test_xml_parser.py is the behavioural one.
    """
    paths = {
        name: value
        for name, value in vars(sch).items()
        if name.isupper() and isinstance(value, str)
    }
    offenders = {n: v for n, v in paths.items() if ".//" in v}
    assert offenders == {}, f"field paths must be anchored at HDon: {offenders}"

    scope_offenders = {n: v for n, v in sch.EXT_SCOPES.items() if ".//" in v}
    assert scope_offenders == {}


def test_ext_scopes_covers_the_six_observed_scopes():
    """Measured 2026-07-27: six distinct header-level TTKhac scopes across the
    8 real fixtures, plus one per line (handled separately on InvoiceLine).
    """
    assert set(sch.EXT_SCOPES) == {
        "dlhdon",
        "ttchung",
        "seller",
        "buyer",
        "ndhdon",
        "totals",
    }
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
.venv/bin/python -m pytest tests/test_xml_schema.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'invoice_agent.extraction.xml_schema'`.

- [ ] **Step 3: Write the module header, paths, and error**

```python
# src/invoice_agent/extraction/xml_schema.py
"""Single source of truth for QĐ-1450 element paths — written against real files.

THE ORDERING RULE (design doc §10.1, plan M1.3): this module is written against
`tests/fixtures/real_samples/` FIRST. The Jinja generator at M2.1 is written
AFTER and must NOT import or read this module. If both rested on one
understanding of the format, a 100% parser score would prove only that the
generator and the parser agree with each other, and the eval report's
`false-positive rate 0%` would be an invented number.

That risk is not hypothetical. The structure table written from memory at M1.2
was wrong in four places, and the naive rule `sum(ThTien) == TgTTTBSo` flags
4 of 5 real, valid, tax-authority-signed invoices as arithmetic errors.

EVERY PATH HERE IS RELATIVE TO `HDon` AND CONTAINS NO `.//`. Measured
2026-07-27: `phongvu_gtgt_tdiep_2.0.0.xml` has root `TDiep`, whose `TTChung`
envelope carries its own `PBan`, its own `MST` (the transmitter's, not the
seller's) and its own `SLuong` — the COUNT OF INVOICES in the envelope. So
`.//SLuong` finds that count before any line quantity, and `.//MST` finds the
transmitter before the seller. `.//` is used only to locate `HDon` itself.
"""

from __future__ import annotations


class XmlExtractionError(Exception):
    """We could not READ the document.

    Deliberately says nothing about whether the data is CORRECT. Arithmetic,
    MST checksums and VAT rates belong to M2.6's `validate` node. Design doc
    §3.3 routes on exactly this distinction: an EXTRACTION error escalates the
    strategy (XML -> vision -> manual_queue), a DATA error travels onward as a
    flag for a human to judge. Keeping the parser incapable of producing the
    second kind puts that split on the right side of the module boundary.

    Lives here rather than in xml_parser.py because the coercers below raise
    it, and xml_parser imports this module — the other direction would be a
    circular import.
    """


# --- Root shapes --------------------------------------------------------------
# Both are real. 7/8 fixtures have root `HDon`; `phongvu_…` is wrapped in a
# `TDiep` transport envelope. A parser hard-coded to one shape fails half the
# corpus outright.
HDON_TAG = "HDon"
TDIEP_TAG = "TDiep"
TDIEP_HDON = "DLieu/HDon"

# --- Scopes, relative to HDon -------------------------------------------------
TTCHUNG = "DLHDon/TTChung"
NDHDON = "DLHDon/NDHDon"
SELLER = f"{NDHDON}/NBan"
BUYER = f"{NDHDON}/NMua"
LINES = f"{NDHDON}/DSHHDVu/HHDVu"
TOTALS = f"{NDHDON}/TToan"

# --- TTChung (header) ---------------------------------------------------------
SCHEMA_VERSION = "PBan"  # 2.0.0, 2.0.1 and 2.1.0 all present in the corpus
# KHMSHDon is `mẫu số` and doubles as the ONLY trustworthy invoice-kind signal.
# THDon is free-text garbage: 6 variants observed, including 'TT78 AC'.
TEMPLATE_NO = "KHMSHDon"
INVOICE_SERIES = "KHHDon"
INVOICE_NUMBER = "SHDon"
ISSUE_DATE = "NLap"
CURRENCY = "DVTTe"
EXCHANGE_RATE = "TGia"  # '1' and '1.00' both occur

# --- Party --------------------------------------------------------------------
PARTY_NAME = "Ten"
PARTY_TAX_CODE = "MST"
PARTY_ADDRESS = "DChi"

# --- Line ---------------------------------------------------------------------
LINE_NO = "STT"
LINE_KIND = "TChat"
LINE_DESC = "THHDVu"
LINE_UNIT = "DVTinh"
LINE_QTY = "SLuong"
LINE_PRICE = "DGia"
LINE_AMOUNT = "ThTien"
LINE_VAT_RATE = "TSuat"
LINE_VAT_AMOUNT = "TThue"

# --- Totals -------------------------------------------------------------------
SUBTOTAL = "TgTCThue"
VAT_TOTAL = "TgTThue"
GRAND_TOTAL = "TgTTTBSo"
TOTAL_IN_WORDS = "TgTTTBChu"

# --- TChat / KHMSHDon values --------------------------------------------------
# TChat 4 is a NOTE line: THHDVu only, no money. Summing it is wrong, and
# requiring ThTien on it crashes. Found in `hokinhdoanh_…`.
ITEM_KIND_GOODS = 1
ITEM_KIND_NOTE = 4
TEMPLATE_NO_VAT = "1"
TEMPLATE_NO_SALES = "2"

# --- TTKhac (vendor extensions) ----------------------------------------------
EXT_NODE = "TTKhac"
EXT_ENTRY = "TTin"
EXT_KEY = "TTruong"
EXT_VALUE = "DLieu"

# Six header-level TTKhac scopes observed across the 8 fixtures (measured
# 2026-07-27, spec §2.6). Name -> path relative to HDon.
EXT_SCOPES: dict[str, str] = {
    "dlhdon": f"DLHDon/{EXT_NODE}",
    "ttchung": f"{TTCHUNG}/{EXT_NODE}",
    "seller": f"{SELLER}/{EXT_NODE}",
    "buyer": f"{BUYER}/{EXT_NODE}",
    "ndhdon": f"{NDHDON}/{EXT_NODE}",
    "totals": f"{TOTALS}/{EXT_NODE}",
}

# Measured 2026-07-27 on all 8 fixtures: ONLY `vnpt_…` uses this exact key, and
# there it holds the real line amount because `ThTien` is 0. Four other vendors
# carry a similar-looking key, `'Thành tiền thanh toán của hàng hóa'`, whose
# value is the GROSS (lienket_ line 1: 1720144 == ThTien 1592726 + TThue
# 127418; newimage_: 702000 == 650000 + 52000; viettelpost_: 51599 ==
# 47777 + 3822). Matching loosely — "any amount-shaped TTKhac key" — would
# double-count tax on 4/8 files. MATCH THIS KEY EXACTLY.
EXT_KEY_LINE_AMOUNT = "Amount"
```

- [ ] **Step 4: Run the tests**

```bash
.venv/bin/python -m pytest tests/test_xml_schema.py -v
```

Expected: both PASS.

- [ ] **Step 5: Verify the guard actually guards (mutation #4, cheap half)**

Temporarily change `LINE_QTY = "SLuong"` to `LINE_QTY = ".//SLuong"`, re-run, confirm `test_no_field_path_uses_descendant_search` goes **RED**, then revert.

- [ ] **Step 6: Commit**

```bash
make check
git add src/invoice_agent/extraction/xml_schema.py tests/test_xml_schema.py
git commit -m "feat(extraction): QĐ-1450 path map, anchored at HDon

Every path is relative to HDon and contains no './/'. Measured 2026-07-27: the
TDiep envelope in phongvu_ carries its own PBan, its own MST (the
transmitter's) and its own SLuong — the invoice COUNT in the envelope — so
'.//SLuong' finds that count before any line quantity.

EXT_KEY_LINE_AMOUNT documents the sharper reading of the M1.2 finding: only
vnpt_ uses the key 'Amount'. Four vendors carry 'Thành tiền thanh toán của
hàng hóa', which is the GROSS, so a loose match double-counts tax on 4/8 files.

Guard mutation-verified: LINE_QTY = './/SLuong' turns the test red."
```

---

### Task 3: Coercers

Each rule below traces to a measurement. This is where "empty is not zero" and "`'8%'` is a string" are enforced once, for every caller.

**Files:**
- Modify: `src/invoice_agent/extraction/xml_schema.py`
- Modify: `tests/test_xml_schema.py`

**Interfaces:**
- Consumes: `XmlExtractionError`, `EXT_*` constants from Task 2.
- Produces:
  - `required_text(node: Element, path: str) -> str`
  - `money(raw: str | None, *, where: str) -> Decimal | None`
  - `integer(raw: str | None, *, where: str) -> int | None`
  - `vat_rate(raw: str | None, *, where: str) -> Decimal | None`
  - `issue_date(raw: str | None, *, where: str) -> date`
  - `ext_bag(scope: Element | None) -> dict[str, str]`

  Every coercer takes `where: str` — a human-readable path used only in error messages, so a failure names the offending element instead of saying "invalid input".

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_xml_schema.py`:

```python
from datetime import date
from decimal import Decimal

import pytest
from defusedxml.ElementTree import fromstring

from invoice_agent.extraction.xml_schema import XmlExtractionError


# --- money -------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, ""])
def test_money_absent_or_empty_is_none_never_zero(raw):
    """`<SLuong />` in vnpt_ parses to '' — that is NOT the number zero.

    Three distinct 'missing' manifestations exist in the corpus: value '0',
    empty element '', absent element None. Collapsing empty to Decimal(0) would
    make a missing quantity indistinguishable from a genuine zero quantity.
    """
    assert sch.money(raw, where="probe") is None


def test_money_zero_is_zero():
    assert sch.money("0", where="probe") == Decimal("0")


def test_money_preserves_eight_decimal_places():
    """Real value from phongvu_. float would lose the last đồng."""
    assert str(sch.money("21618409.09000000", where="probe")) == "21618409.09000000"


@pytest.mark.parametrize("raw", ["abc", "1,5", "NaN", "Infinity"])
def test_money_rejects_non_finite_and_garbage(raw):
    """NaN and Infinity are the trap: Decimal('NaN') SUCCEEDS, so a plain
    try/except InvalidOperation would let them through into a money field.
    """
    with pytest.raises(XmlExtractionError):
        sch.money(raw, where="probe")


# --- vat_rate ----------------------------------------------------------------


def test_vat_rate_parses_percent_string():
    """TSuat is a STRING with a '%': '8%', '10%'. Not 0.08, not 8."""
    assert sch.vat_rate("8%", where="probe") == Decimal("0.08")
    assert sch.vat_rate("10%", where="probe") == Decimal("0.10")


@pytest.mark.parametrize("raw", [None, ""])
def test_vat_rate_absent_is_none(raw):
    """Absent on every non-VAT invoice (KHMSHDon=2). Not an error."""
    assert sch.vat_rate(raw, where="probe") is None


@pytest.mark.parametrize("raw", ["KCT", "KKKNT", "\\", "8"])
def test_vat_rate_rejects_unknown_codes_loudly(raw):
    """KCT / KKKNT are believed real but appear in NONE of the 8 fixtures.

    Raising means the first such invoice fails with a named reason and routes
    to vision/manual_queue, instead of being silently assigned a wrong tax
    rate. Guessing a semantic from memory is the mistake M1.2 already paid for.
    Revisit when a fixture proves the shape — see spec §9.
    """
    with pytest.raises(XmlExtractionError):
        sch.vat_rate(raw, where="probe")


# --- integer / issue_date / required_text -------------------------------------


def test_integer_and_date():
    assert sch.integer("7", where="probe") == 7
    assert sch.integer(None, where="probe") is None
    assert sch.integer("", where="probe") is None
    assert sch.issue_date("2026-06-30", where="probe") == date(2026, 6, 30)
    with pytest.raises(XmlExtractionError):
        sch.issue_date("30/06/2026", where="probe")
    with pytest.raises(XmlExtractionError):
        sch.issue_date(None, where="probe")


def test_required_text_rejects_absent_and_empty():
    node = fromstring(b"<a><present>x</present><empty/></a>")
    assert sch.required_text(node, "present") == "x"
    with pytest.raises(XmlExtractionError):
        sch.required_text(node, "empty")
    with pytest.raises(XmlExtractionError):
        sch.required_text(node, "absent")


# --- ext_bag -----------------------------------------------------------------


def test_ext_bag_reads_by_tag_not_position():
    """Child order REVERSES between vendors: Viettel emits
    `DLieu, KDLieu, TTruong`; VNPT emits `TTruong, KDLieu, DLieu`.
    Reading by position is broken on half the corpus.
    """
    viettel = fromstring(
        b"<TTKhac><TTin><DLieu>51599</DLieu><KDLieu>string</KDLieu>"
        b"<TTruong>Amount</TTruong></TTin></TTKhac>"
    )
    vnpt = fromstring(
        b"<TTKhac><TTin><TTruong>Amount</TTruong><KDLieu>string</KDLieu>"
        b"<DLieu>51599</DLieu></TTin></TTKhac>"
    )
    assert sch.ext_bag(viettel) == sch.ext_bag(vnpt) == {"Amount": "51599"}


def test_ext_bag_handles_absent_and_empty_scopes():
    """`ktx_…/TToan/TTKhac` is present with zero TTin children."""
    assert sch.ext_bag(None) == {}
    assert sch.ext_bag(fromstring(b"<TTKhac/>")) == {}


def test_ext_bag_keeps_keys_verbatim_and_empty_values_as_empty_string():
    """Keys carry real double spaces ('Loại giấy tờ  người mua'). Normalizing
    them would collide keys across vendors.
    """
    node = fromstring(
        b"<TTKhac>"
        b"<TTin><TTruong>Loai giay to  nguoi mua</TTruong><DLieu/></TTin>"
        b"<TTin><TTruong>Zero</TTruong><DLieu>0</DLieu></TTin>"
        b"<TTin><KDLieu>string</KDLieu></TTin>"
        b"</TTKhac>"
    )
    assert sch.ext_bag(node) == {"Loai giay to  nguoi mua": "", "Zero": "0"}


def test_ext_bag_duplicate_key_last_wins():
    """Zero duplicates in the corpus, so this is a documented CHOICE rather
    than a measurement. Pinned by a test so it cannot drift silently.
    """
    node = fromstring(
        b"<TTKhac>"
        b"<TTin><TTruong>K</TTruong><DLieu>first</DLieu></TTin>"
        b"<TTin><TTruong>K</TTruong><DLieu>second</DLieu></TTin>"
        b"</TTKhac>"
    )
    assert sch.ext_bag(node) == {"K": "second"}
```

- [ ] **Step 2: Run them to confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_xml_schema.py -v
```

Expected: the new tests FAIL with `AttributeError: module ... has no attribute 'money'`.

- [ ] **Step 3: Implement the coercers**

Add these imports at the top of `xml_schema.py`:

```python
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import Element
```

Append to `xml_schema.py`:

```python
# --- Coercers ----------------------------------------------------------------
# Every one takes `where` purely so a failure names the offending element.
# "TSuat: unsupported value 'KCT'" is actionable; "invalid input" is not.

# `TSuat` is a percent STRING. Anchored and fully explicit: a bare '8' must NOT
# parse, because we cannot tell whether a vendor meant 8% or 800%.
_PERCENT = re.compile(r"^(\d+(?:\.\d+)?)%$")


def required_text(node: Element, path: str) -> str:
    """Text that must be present AND non-empty, else the document is unreadable."""
    value = node.findtext(path)
    if value is None or value == "":
        raise XmlExtractionError(f"{path}: required element missing or empty")
    return value


def money(raw: str | None, *, where: str) -> Decimal | None:
    """Any monetary or quantity value. `Decimal` only — never `float`.

    Empty and absent both return None, NEVER Decimal(0): `<SLuong />` in
    `vnpt_…` means "not stated", which is a different fact from a quantity of
    zero, and M2.6 needs to tell them apart.
    """
    if raw is None or raw == "":
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise XmlExtractionError(f"{where}: not a number: {raw!r}") from exc
    # Decimal('NaN') and Decimal('Infinity') SUCCEED. Without this check they
    # would flow into a money field and poison every downstream comparison.
    if not value.is_finite():
        raise XmlExtractionError(f"{where}: not a finite number: {raw!r}")
    return value


def integer(raw: str | None, *, where: str) -> int | None:
    """`STT` and `TChat`. Absent stays absent — see InvoiceLine.item_kind."""
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise XmlExtractionError(f"{where}: not an integer: {raw!r}") from exc


def vat_rate(raw: str | None, *, where: str) -> Decimal | None:
    """`'8%'` -> `Decimal('0.08')`. Absent -> None.

    Absent is normal, not an error: `TSuat` is missing from every non-VAT
    invoice (KHMSHDon=2).

    Anything else raises. `KCT` (không chịu thuế) and `KKKNT` are believed to
    occur in real invoices but appear in NONE of the 8 fixtures, so we refuse
    to invent a rate for them: the first such invoice fails loudly and routes
    to vision/manual_queue instead of silently carrying a wrong tax rate.
    Revisit when a fixture proves the shape (spec §9).
    """
    if raw is None or raw == "":
        return None
    matched = _PERCENT.match(raw)
    if matched is None:
        raise XmlExtractionError(
            f"{where}: unsupported TSuat {raw!r}; only 'N%' is observed in the corpus"
        )
    # scaleb(-2) keeps the two-decimal form: '10' -> 0.10, not 0.1.
    return Decimal(matched.group(1)).scaleb(-2)


def issue_date(raw: str | None, *, where: str) -> date:
    """`NLap`. ISO `YYYY-MM-DD` in 8/8 fixtures. Required."""
    if raw is None or raw == "":
        raise XmlExtractionError(f"{where}: required element missing or empty")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise XmlExtractionError(f"{where}: not an ISO date: {raw!r}") from exc


def ext_bag(scope: Element | None) -> dict[str, str]:
    """One `TTKhac` block -> `{TTruong: DLieu}`.

    Reads children BY TAG NAME. Never by position: measured 2026-07-27, Viettel
    emits `DLieu, KDLieu, TTruong` while VNPT emits `TTruong, KDLieu, DLieu`,
    so positional reads are wrong on half the corpus.

    Keys are verbatim — several carry real double spaces
    ('Loại giấy tờ  người mua'), and normalizing would collide keys between
    vendors. A missing or empty `DLieu` becomes '': within a bag, absence is
    already expressed by the key not being there. Duplicate `TTruong` is
    last-wins; the corpus has none, so that is a documented choice.
    """
    if scope is None:
        return {}
    bag: dict[str, str] = {}
    for entry in scope.findall(EXT_ENTRY):
        key = entry.findtext(EXT_KEY)
        if not key:
            continue
        bag[key] = entry.findtext(EXT_VALUE) or ""
    return bag
```

- [ ] **Step 4: Run the tests**

```bash
.venv/bin/python -m pytest tests/test_xml_schema.py -v
```

Expected: all PASS.

- [ ] **Step 5: Verify mutations #2 and #3 turn tests red**

Mutation #3 — in `money`, change `return None` to `return Decimal(0)`. Run: `test_money_absent_or_empty_is_none_never_zero` must go **RED**. Revert.

Mutation #2 — in `ext_bag`, replace the body of the loop with a positional read:

```python
        children = list(entry)
        bag[children[0].text or ""] = children[2].text or ""
```

Run: `test_ext_bag_reads_by_tag_not_position` must go **RED**. Revert.

- [ ] **Step 6: Commit**

```bash
make check
git add src/invoice_agent/extraction/xml_schema.py tests/test_xml_schema.py
git commit -m "feat(extraction): coercers — empty is not zero, '8%' is a string

money() returns None for both absent and empty, never Decimal(0): <SLuong /> in
vnpt_ means 'not stated', a different fact from a quantity of zero. Also
rejects non-finite values — Decimal('NaN') SUCCEEDS, so a plain
except InvalidOperation would let NaN into a money field.

vat_rate() parses 'N%' only and raises on KCT/KKKNT rather than inventing a
rate for a code no fixture contains.

ext_bag() reads TTin children by tag name; child order reverses between
Viettel and VNPT, so positional reads break half the corpus.

Mutation-verified: money('') -> Decimal(0) and a positional ext_bag each turn
their guard red."
```

---

### Task 4: `locate_hdon` + synthetic envelope fixtures

**Files:**
- Modify: `src/invoice_agent/extraction/xml_schema.py`
- Create: `tests/fixtures/synthetic/tdiep_envelope_distinct.xml`
- Create: `tests/fixtures/synthetic/tdiep_two_invoices.xml`
- Modify: `tests/test_xml_schema.py`

**Interfaces:**
- Consumes: `HDON_TAG`, `TDIEP_TAG`, `TDIEP_HDON`, `XmlExtractionError`.
- Produces: `locate_hdon(root: Element) -> Element`.

The synthetic fixtures exist because the real corpus **cannot** catch the `.//` bug. In `phongvu_…` all three envelope/invoice collisions coincide in value — envelope `PBan` `2.0.0` equals the invoice's, envelope `MST` equals the seller's, envelope `SLuong` `1` equals the line's. A parser written entirely with `.//` passes every assertion that file can make.

- [ ] **Step 1: Create the two synthetic fixtures**

`tests/fixtures/synthetic/tdiep_envelope_distinct.xml` — hand-written, no PII, every envelope value deliberately different from its invoice counterpart:

```xml
<?xml version="1.0" encoding="utf-8"?>
<!-- Hand-written for tests. NOT a real invoice, NOT anonymised vendor data.
     Purpose: the real TDiep fixture (phongvu_) cannot catch a './/' parser,
     because its envelope PBan/MST/SLuong all happen to EQUAL the invoice's.
     Here every one of the three differs, so './/' fails loudly. -->
<TDiep>
  <TTChung>
    <PBan>9.9.9</PBan>
    <MST>9999999999</MST>
    <SLuong>99</SLuong>
    <MLTDiep>202</MLTDiep>
  </TTChung>
  <DLieu>
    <HDon>
      <DLHDon>
        <TTChung>
          <PBan>2.1.0</PBan>
          <THDon>Hóa đơn giá trị gia tăng</THDon>
          <KHMSHDon>1</KHMSHDon>
          <KHHDon>C26TSY</KHHDon>
          <SHDon>4242</SHDon>
          <NLap>2026-05-20</NLap>
          <DVTTe>VND</DVTTe>
          <TGia>1</TGia>
        </TTChung>
        <NDHDon>
          <NBan>
            <Ten>CÔNG TY TNHH TỔNG HỢP</Ten>
            <MST>0100000003</MST>
            <DChi>1 Đường Mẫu, Hà Nội</DChi>
          </NBan>
          <NMua>
            <Ten>NGUYỄN VĂN B</Ten>
            <DChi>2 Đường Mẫu, Hà Nội</DChi>
          </NMua>
          <DSHHDVu>
            <HHDVu>
              <TChat>1</TChat>
              <STT>1</STT>
              <THHDVu>Hàng mẫu</THHDVu>
              <DVTinh>Cái</DVTinh>
              <SLuong>3</SLuong>
              <DGia>100000</DGia>
              <ThTien>300000</ThTien>
              <TSuat>8%</TSuat>
              <TThue>24000</TThue>
            </HHDVu>
          </DSHHDVu>
          <TToan>
            <TgTCThue>300000</TgTCThue>
            <TgTThue>24000</TgTThue>
            <TgTTTBSo>324000</TgTTTBSo>
            <TgTTTBChu>Ba trăm hai mươi bốn nghìn đồng</TgTTTBChu>
          </TToan>
        </NDHDon>
      </DLHDon>
    </HDon>
  </DLieu>
</TDiep>
```

`tests/fixtures/synthetic/tdiep_two_invoices.xml` — the guard case. `TDiep/TTChung/SLuong` is literally the invoice count, so N>1 is structurally real:

```xml
<?xml version="1.0" encoding="utf-8"?>
<!-- Hand-written for tests. Two HDon in one envelope. A parser that silently
     took the first would drop the second without a sound. Must raise. -->
<TDiep>
  <TTChung>
    <PBan>2.1.0</PBan>
    <MST>0100000003</MST>
    <SLuong>2</SLuong>
  </TTChung>
  <DLieu>
    <HDon>
      <DLHDon>
        <TTChung><KHMSHDon>1</KHMSHDon><KHHDon>C26TSY</KHHDon><SHDon>1</SHDon><NLap>2026-05-20</NLap></TTChung>
      </DLHDon>
    </HDon>
    <HDon>
      <DLHDon>
        <TTChung><KHMSHDon>1</KHMSHDon><KHHDon>C26TSY</KHHDon><SHDon>2</SHDon><NLap>2026-05-20</NLap></TTChung>
      </DLHDon>
    </HDon>
  </DLieu>
</TDiep>
```

Confirm both are committable (`.gitignore` blocks `*.xml` at every level but whitelists `!tests/**/*.xml`):

```bash
git add --dry-run tests/fixtures/synthetic/tdiep_envelope_distinct.xml
```

Expected: `add 'tests/fixtures/synthetic/tdiep_envelope_distinct.xml'`, exit 0.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_xml_schema.py`:

```python
from pathlib import Path

SYNTHETIC = Path(__file__).parent / "fixtures" / "synthetic"
REAL = Path(__file__).parent / "fixtures" / "real_samples"


def test_locate_hdon_when_root_is_hdon():
    root = fromstring((REAL / "vnpt_hoadon_banhang_2.1.0.xml").read_bytes())
    assert sch.locate_hdon(root) is root


def test_locate_hdon_inside_tdiep_envelope():
    """7/8 fixtures have root HDon; phongvu_ is wrapped in a TDiep envelope.
    Both shapes are real, so the parser must LOCATE HDon, not assume it.
    """
    root = fromstring((REAL / "phongvu_gtgt_tdiep_2.0.0.xml").read_bytes())
    hdon = sch.locate_hdon(root)
    assert hdon.tag == "HDon"
    assert hdon is not root


def test_locate_hdon_raises_on_multi_invoice_envelope():
    """`TDiep/TTChung/SLuong` IS the invoice count in the envelope. Taking the
    first HDon would drop the rest of a batch silently. M1 raises instead;
    supporting envelopes is out of scope (spec §3).
    """
    root = fromstring((SYNTHETIC / "tdiep_two_invoices.xml").read_bytes())
    with pytest.raises(XmlExtractionError, match="2 HDon"):
        sch.locate_hdon(root)


def test_locate_hdon_raises_on_unrecognised_root():
    with pytest.raises(XmlExtractionError, match="root"):
        sch.locate_hdon(fromstring(b"<SomethingElse/>"))
```

- [ ] **Step 3: Run them to confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_xml_schema.py -k locate -v
```

Expected: FAIL — `module ... has no attribute 'locate_hdon'`.

- [ ] **Step 4: Implement `locate_hdon`**

Append to `xml_schema.py`:

```python
def locate_hdon(root: Element) -> Element:
    """Find the invoice element. Both root shapes are real.

    Measured 2026-07-27: 7/8 fixtures have root `HDon`; `phongvu_…` wraps it in
    a `TDiep` transport envelope. A parser hard-coded to either shape fails
    outright on the other — which is why the gate says "2–3 files", not "1".

    The count check is load-bearing, not decoration: `TDiep/TTChung/SLuong` IS
    the number of invoices in the envelope. Our fixture has SLuong=1, so N>1 is
    unobserved but structurally real, and silently taking the first `HDon`
    would drop the rest of a batch without a sound. Raising routes the document
    to vision/manual_queue via §3.3 instead of losing data quietly.
    """
    if root.tag == HDON_TAG:
        return root
    if root.tag == TDIEP_TAG:
        found = root.findall(TDIEP_HDON)
        if len(found) != 1:
            raise XmlExtractionError(
                f"TDiep envelope holds {len(found)} HDon; exactly 1 is supported"
            )
        return found[0]
    raise XmlExtractionError(f"unrecognised root element {root.tag!r}")
```

- [ ] **Step 5: Run the tests**

```bash
.venv/bin/python -m pytest tests/test_xml_schema.py -v
```

Expected: all PASS.

- [ ] **Step 6: Verify mutation #5**

Change `if len(found) != 1:` to `if len(found) < 1:`. Run: `test_locate_hdon_raises_on_multi_invoice_envelope` must go **RED**. Revert.

- [ ] **Step 7: Commit**

```bash
make check
git add src/invoice_agent/extraction/xml_schema.py tests/test_xml_schema.py tests/fixtures/synthetic/
git commit -m "feat(extraction): locate_hdon — both root shapes, and reject N>1

7/8 fixtures have root HDon; phongvu_ wraps it in a TDiep envelope. A parser
hard-coded to either shape fails outright on the other.

The count check is load-bearing: TDiep/TTChung/SLuong IS the invoice count in
the envelope, so silently taking the first HDon would drop the rest of a batch.

Two hand-written fixtures added, because the real corpus cannot catch the './/'
bug: in phongvu_ the envelope PBan/MST/SLuong all coincide in value with the
invoice's, so a './/'-only parser passes every assertion that file can make.

Mutation-verified: len(found) != 1 -> < 1 turns the multi-invoice test red."
```

---

### Task 5: `models.py` — the extraction contract

**Files:**
- Create: `src/invoice_agent/extraction/models.py`
- Create: `tests/test_extraction_models.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `InvoiceLine` and `InvoiceData`, both `pydantic.BaseModel` with `model_config = ConfigDict(extra="forbid", frozen=True)`. Field names and types exactly as written in Step 3 — Task 6 constructs both by keyword.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extraction_models.py
"""The extraction contract, and the safety property it must satisfy."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from invoice_agent.extraction.models import InvoiceData, InvoiceLine

# Fields that would let an LLM authorize its own work. At M3.4 InvoiceData
# becomes the structured-output schema for a vision call, and per CLAUDE.md
# structured output IS the security boundary — so a field here is a field the
# model can write. Flags are computed by Python from DB state; nothing else.
FORBIDDEN_FIELDS = {
    "category",
    "department",
    "classification_confidence",
    "status",
    "approved",
    "approval",
    "flags",
    "auto_post",
    "is_valid",
}


def test_invoice_data_has_no_self_authorizing_field():
    assert set(InvoiceData.model_fields) & FORBIDDEN_FIELDS == set()
    assert set(InvoiceLine.model_fields) & FORBIDDEN_FIELDS == set()


def test_invoice_data_rejects_unknown_fields():
    """extra='forbid' is what makes the test above meaningful: without it, an
    LLM could smuggle a field the schema never declared.
    """
    with pytest.raises(ValidationError):
        InvoiceData(
            template_no="1",
            invoice_series="C26TSY",
            invoice_number="4242",
            issue_date=date(2026, 5, 20),
            total_amount=Decimal("324000"),
            lines=[],
            category="office_supplies",
        )


def test_optional_fields_default_to_none_and_ext_to_empty_dict():
    """A minimal real invoice supplies only the required fields. Absent seller
    MST, absent buyer name and absent TgTThue are all normal in the corpus.
    """
    invoice = InvoiceData(
        template_no="2",
        invoice_series="C26TLN",
        invoice_number="23900",
        issue_date=date(2026, 6, 10),
        total_amount=Decimal("90000"),
        lines=[InvoiceLine(line_no=1, item_kind=1, description="Hàng mẫu")],
    )
    assert invoice.seller_tax_code is None
    assert invoice.buyer_name is None
    assert invoice.vat_amount is None
    assert invoice.ext == {}
    assert invoice.lines[0].quantity is None
    assert invoice.lines[0].ext == {}


def test_models_are_frozen():
    line = InvoiceLine(line_no=1, item_kind=1)
    with pytest.raises(ValidationError):
        line.amount = Decimal("1")
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
.venv/bin/python -m pytest tests/test_extraction_models.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'invoice_agent.extraction.models'`.

- [ ] **Step 3: Write the models**

```python
# src/invoice_agent/extraction/models.py
"""The extraction contract: what we read off an invoice, and nothing else.

⚠️ SAFETY PROPERTY (CLAUDE.md). At M3.4 `InvoiceData` becomes the
structured-output schema for an LLM vision call, and structured output IS the
security boundary. Every field here is therefore a field the model can write.

So this is NOT the `invoices` DB row. It deliberately omits `category`,
`department`, `classification_confidence`, `status`, and every flag or approval
field, even though design doc §2.3 has those columns. Before adding a field,
ask: could the LLM use this to self-authorize? `extra="forbid"` is what makes
the omission enforceable rather than aspirational.

Fidelity notes, all measured 2026-07-27 on `tests/fixtures/real_samples/`:
`Decimal` everywhere because amounts reach 8 decimal places
(`21618409.09000000`); almost everything is Optional because real invoices omit
far more than the regulation implies; and text is stored verbatim, since
`TgTTTBChu` carries double spaces and a `./.` suffix that M2.6 must normalize
itself.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class InvoiceLine(BaseModel):
    """One `HHDVu` element."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    line_no: int | None = None  # STT
    # TChat: 1 goods, 2 promotion, 3 discount, 4 NOTE.
    #
    # `int | None`, NOT `int = 1`. TChat is present on 15/15 lines across 8/8
    # fixtures, but whether QĐ-1450 permits omission with an implied default of
    # 1 is UNVERIFIED — it waits on M1.2's outstanding NĐ 70/2025 check. A wrong
    # default would silently fold a TChat=4 note line into the money sum, which
    # is the exact bug the measured validation rule exists to catch.
    item_kind: int | None = None
    # THHDVu. ⚠️ PROMPT-INJECTION SURFACE (design doc §2.4, §9.1): free text
    # written by whoever issued the invoice. Never concatenate into a system
    # prompt — M3.3 wraps it in an escaped <invoice_data> block.
    description: str | None = None
    unit: str | None = None  # DVTinh — absent on non-VAT invoices
    quantity: Decimal | None = None  # SLuong — `<SLuong />` is None, not 0
    unit_price: Decimal | None = None  # DGia
    # ThTien. 0 at VNPT, where the real amount hides in ext['Amount'].
    amount: Decimal | None = None
    vat_rate: Decimal | None = None  # TSuat — '8%' parsed to Decimal('0.08')
    vat_amount: Decimal | None = None  # TThue
    # The line's whole TTKhac block, verbatim. Kept because TTKhac already
    # proved load-bearing once: VNPT sets ThTien=0 and puts the real amount in
    # ext['Amount'], which no amount of reading the regulation would predict.
    ext: dict[str, str] = Field(default_factory=dict)


class InvoiceData(BaseModel):
    """One invoice, as read from XML (M1.4) or vision (M3.4)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str | None = None  # PBan — 2.0.0, 2.0.1 and 2.1.0 all real

    # --- Identity. §2.3's UNIQUE key is
    # (seller_tax_code, template_no, invoice_series, invoice_number) — an
    # invoice_number alone is NOT unique across sellers.
    #
    # template_no IS KHMSHDon, and it is the only trustworthy invoice-kind
    # signal (1 = GTGT, 2 = bán hàng). There is deliberately no separate
    # `invoice_kind` field to disagree with it: THDon looks like it names the
    # kind but is free-text garbage — 6 variants observed, including 'TT78 AC'.
    template_no: str
    invoice_series: str  # KHHDon
    invoice_number: str  # SHDon
    issue_date: date  # NLap

    currency: str | None = None  # DVTTe
    exchange_rate: Decimal | None = None  # TGia — '1' and '1.00' both occur

    seller_name: str | None = None
    # MST has three real shapes: '0100000003' (10), '0300000018-042' (branch,
    # with a dash) and '050000000000' (12, household business). M2.6's
    # validator must accept all three.
    seller_tax_code: str | None = None
    seller_address: str | None = None
    # Buyer fields are Optional because real invoices omit them: one fixture has
    # no NMua/Ten at all, and buyer MST is missing in three forms — '0', '' and
    # absent. Real garbage also lives here: DChi = 'Người mua không lấy hóa đơn'.
    buyer_name: str | None = None
    buyer_tax_code: str | None = None
    buyer_address: str | None = None

    subtotal: Decimal | None = None  # TgTCThue — absent in 3/8 fixtures
    vat_amount: Decimal | None = None  # TgTThue — absent in 3/8 fixtures
    total_amount: Decimal  # TgTTTBSo — the one total present in 8/8
    # TgTTTBChu, verbatim. §10.3 uses it as a second, independent encoding of
    # the total, so M2.6 compares words against digits. Real value:
    # 'Ba  triệu năm trăm ngàn đồng./.' — two spaces, 'ngàn' not 'nghìn', './.'
    # suffix. Normalizing here would hide the very cases M2.6 must handle.
    total_in_words: str | None = None

    lines: list[InvoiceLine]
    # Header-level TTKhac bags, keyed by xml_schema.EXT_SCOPES name.
    ext: dict[str, dict[str, str]] = Field(default_factory=dict)
```

- [ ] **Step 4: Run the tests**

```bash
.venv/bin/python -m pytest tests/test_extraction_models.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
make check
git add src/invoice_agent/extraction/models.py tests/test_extraction_models.py
git commit -m "feat(extraction): InvoiceData/InvoiceLine — the extraction contract

Not the invoices DB row. At M3.4 this becomes the LLM vision structured-output
schema, and per CLAUDE.md structured output IS the security boundary, so every
field here is a field the model can write. It therefore omits category,
department, classification_confidence, status and every flag/approval field,
and sets extra='forbid' so the omission is enforceable. A test asserts the
field set never intersects the forbidden names.

item_kind is int | None rather than int = 1: TChat is present on 15/15 lines,
but whether QĐ-1450 permits omission with an implied 1 is unverified, and a
wrong default would fold a TChat=4 note line into the money sum."
```

---

### Task 6: `parse_invoice_xml`

**Files:**
- Create: `src/invoice_agent/extraction/xml_parser.py`
- Modify: `src/invoice_agent/extraction/__init__.py`
- Create: `tests/test_xml_parser.py`

**Interfaces:**
- Consumes: everything from Tasks 2–5.
- Produces: `parse_invoice_xml(raw: bytes) -> InvoiceData`, and `XmlExtractionError` re-exported from `xml_parser` and from the package `__init__`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_xml_parser.py
"""M1.4 — parse the 8 real invoices, and the cases the real corpus cannot cover.

The golden table below is TRANSCRIBED BY HAND from measurements taken
2026-07-27, never generated from the parser's own output. Generating it would
rebuild exactly the circularity this milestone exists to break (design §10.1).
"""

from decimal import Decimal
from pathlib import Path

import pytest

from invoice_agent.extraction import XmlExtractionError, parse_invoice_xml

REAL = Path(__file__).parent / "fixtures" / "real_samples"
SYNTHETIC = Path(__file__).parent / "fixtures" / "synthetic"

# file, template_no, invoice_series, invoice_number, total_amount, item_kinds
GOLDEN = [
    ("dienluc_gtgt_cccd_2.1.0.xml", "1", "K26TAG", "3260056", "763044", [1]),
    ("hokinhdoanh_banhang_qrcode_2.1.0.xml", "2", "C26TLN", "23900", "90000", [1, 4]),
    ("ktx_banhang_cccd_2.1.0.xml", "2", "C25TCC", "2779", "3410000", [1]),
    ("lienket_gtgt_7dong_2.1.0.xml", "1", "C25TAC", "15547", "10994397", [1] * 7),
    ("newimage_gtgt_pban2.0.1.xml", "1", "C23TNI", "103049", "702000", [1]),
    ("phongvu_gtgt_tdiep_2.0.0.xml", "1", "C22TDN", "5227", "23780250.00000000", [1]),
    ("viettelpost_gtgt_2.1.0.xml", "1", "K26TAN", "2219110", "51599", [1]),
    ("vnpt_hoadon_banhang_2.1.0.xml", "2", "C25TKT", "78", "3500000", [1]),
]


def _parse(directory: Path, name: str):
    return parse_invoice_xml((directory / name).read_bytes())


@pytest.mark.parametrize(
    "name,template_no,series,number,total,item_kinds",
    GOLDEN,
    ids=[row[0] for row in GOLDEN],
)
def test_all_real_fixtures_parse(name, template_no, series, number, total, item_kinds):
    invoice = _parse(REAL, name)
    assert invoice.template_no == template_no
    assert invoice.invoice_series == series
    assert invoice.invoice_number == number
    assert invoice.total_amount == Decimal(total)
    assert [line.item_kind for line in invoice.lines] == item_kinds


def test_empty_element_is_none_not_zero():
    """`<SLuong />` in vnpt_ — 'not stated', not a quantity of zero."""
    invoice = _parse(REAL, "vnpt_hoadon_banhang_2.1.0.xml")
    assert invoice.lines[0].quantity is None


def test_note_line_has_no_money():
    """TChat=4 is a NOTE line: THHDVu only. Summing it is wrong, and requiring
    ThTien on it would crash. Found in hokinhdoanh_.
    """
    invoice = _parse(REAL, "hokinhdoanh_banhang_qrcode_2.1.0.xml")
    note = invoice.lines[1]
    assert note.item_kind == 4
    assert note.amount is None
    assert note.description is not None


def test_vnpt_amount_hidden_in_ext():
    """VNPT sets ThTien=0 and puts the real amount in TTKhac['Amount'].

    This is why ext bags are preserved verbatim: nothing in the regulation
    predicts it, and the naive rule sum(ThTien) misses 3.5M đồng here.
    """
    invoice = _parse(REAL, "vnpt_hoadon_banhang_2.1.0.xml")
    assert invoice.lines[0].amount == Decimal("0")
    assert invoice.lines[0].ext["Amount"] == "3500000"


def test_decimal_precision_preserved():
    """phongvu_ carries 8 decimal places. float would lose the last đồng."""
    invoice = _parse(REAL, "phongvu_gtgt_tdiep_2.0.0.xml")
    assert str(invoice.total_amount) == "23780250.00000000"


def test_words_amount_is_verbatim():
    """Two spaces after 'Ba', 'ngàn' not 'nghìn', './.' suffix. §10.3 uses this
    as a second independent encoding of the total, so M2.6's normalizer must
    face the real string — stripping it here would hide the test case.
    """
    invoice = _parse(REAL, "vnpt_hoadon_banhang_2.1.0.xml")
    assert invoice.total_in_words == "Ba  triệu năm trăm ngàn đồng./."


def test_ext_bag_is_order_independent():
    """Viettel emits DLieu,KDLieu,TTruong; VNPT emits TTruong,KDLieu,DLieu."""
    viettel = _parse(REAL, "viettelpost_gtgt_2.1.0.xml")
    vnpt = _parse(REAL, "vnpt_hoadon_banhang_2.1.0.xml")
    assert viettel.lines[0].ext["Tiền thuế dòng (Tiền thuế GTGT)"] == "3822"
    assert vnpt.lines[0].ext["VATAmount"] == "0"


def test_missing_buyer_is_not_an_error():
    """hokinhdoanh_ has no NMua/Ten at all. A validator demanding one would
    flag a real, valid, tax-authority-signed invoice.
    """
    invoice = _parse(REAL, "hokinhdoanh_banhang_qrcode_2.1.0.xml")
    assert invoice.buyer_name is None


def test_envelope_values_do_not_leak():
    """The './/' regression test. All three collision points at once, because
    any one alone could pass by luck — and in the REAL TDiep fixture all three
    coincide in value, so it cannot catch this bug at all (spec §2.3).
    """
    invoice = _parse(SYNTHETIC, "tdiep_envelope_distinct.xml")
    assert invoice.schema_version == "2.1.0"  # envelope says 9.9.9
    assert invoice.seller_tax_code == "0100000003"  # envelope says 9999999999
    assert invoice.lines[0].quantity == Decimal("3")  # envelope SLuong says 99


def test_multi_invoice_envelope_raises():
    with pytest.raises(XmlExtractionError, match="2 HDon"):
        _parse(SYNTHETIC, "tdiep_two_invoices.xml")


BILLION_LAUGHS = (
    b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY e0 "AAAA">'
    b'<!ENTITY e1 "&e0;&e0;"><!ENTITY e2 "&e1;&e1;">]><r>&e2;</r>'
)
XXE = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/hostname">]><r>&x;</r>'


@pytest.mark.parametrize("payload", [BILLION_LAUGHS, XXE], ids=["billion-laughs", "xxe"])
def test_entity_declarations_rejected(payload):
    """Vendor XML is untrusted. Measured 2026-07-27 on Python 3.13.5: stdlib
    ElementTree blocks external entities but EXPANDS internal ones — 11 nested
    levels produced 4096 chars, so the real payload is a memory DoS. defusedxml
    raises EntitiesForbidden for both; we wrap it as XmlExtractionError so the
    graph routes it like any other unreadable document.
    """
    with pytest.raises(XmlExtractionError):
        parse_invoice_xml(payload)


def test_malformed_xml_raises_extraction_error():
    with pytest.raises(XmlExtractionError):
        parse_invoice_xml(b"<HDon><unclosed></HDon>")


def test_missing_required_field_raises():
    with pytest.raises(XmlExtractionError):
        parse_invoice_xml(b"<HDon><DLHDon><TTChung/></DLHDon></HDon>")
```

- [ ] **Step 2: Run them to confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_xml_parser.py -v
```

Expected: FAIL — `ImportError: cannot import name 'parse_invoice_xml'`.

- [ ] **Step 3: Write the parser**

```python
# src/invoice_agent/extraction/xml_parser.py
"""Deterministic QĐ-1450 XML -> InvoiceData (M1.4).

A pure function: same bytes in, byte-identical model out. That property is why
design doc §3.3 does NOT retry this node — retrying a deterministic parser three
times produces three identical results. Escalation goes XML -> vision instead.

The parser raises when it cannot READ the document, and never judges whether
the data is CORRECT. Arithmetic, MST checksums, VAT-rate consistency and
words-vs-digits all belong to M2.6's `validate` node. That boundary is what
makes §3.3's EXTRACTION-vs-DATA routing decidable: by construction, everything
raised here is an extraction error.
"""

from decimal import Decimal
from xml.etree.ElementTree import Element, ParseError

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException

from invoice_agent.extraction import xml_schema as sch
from invoice_agent.extraction.models import InvoiceData, InvoiceLine
from invoice_agent.extraction.xml_schema import XmlExtractionError

__all__ = ["XmlExtractionError", "parse_invoice_xml"]


def parse_invoice_xml(raw: bytes) -> InvoiceData:
    """Read one invoice. Raises `XmlExtractionError` and nothing else."""
    root = _read(raw)
    hdon = sch.locate_hdon(root)
    header = _require_node(hdon, sch.TTCHUNG)
    totals = _require_node(hdon, sch.TOTALS)
    seller = hdon.find(sch.SELLER)
    buyer = hdon.find(sch.BUYER)

    lines = [
        _line(element, index) for index, element in enumerate(hdon.findall(sch.LINES), start=1)
    ]
    if not lines:
        raise XmlExtractionError(f"{sch.LINES}: no invoice lines found")

    return InvoiceData(
        schema_version=header.findtext(sch.SCHEMA_VERSION),
        template_no=sch.required_text(header, sch.TEMPLATE_NO),
        invoice_series=sch.required_text(header, sch.INVOICE_SERIES),
        invoice_number=sch.required_text(header, sch.INVOICE_NUMBER),
        issue_date=sch.issue_date(
            header.findtext(sch.ISSUE_DATE), where=f"{sch.TTCHUNG}/{sch.ISSUE_DATE}"
        ),
        currency=header.findtext(sch.CURRENCY),
        exchange_rate=sch.money(
            header.findtext(sch.EXCHANGE_RATE), where=f"{sch.TTCHUNG}/{sch.EXCHANGE_RATE}"
        ),
        seller_name=_party(seller, sch.PARTY_NAME),
        seller_tax_code=_party(seller, sch.PARTY_TAX_CODE),
        seller_address=_party(seller, sch.PARTY_ADDRESS),
        buyer_name=_party(buyer, sch.PARTY_NAME),
        buyer_tax_code=_party(buyer, sch.PARTY_TAX_CODE),
        buyer_address=_party(buyer, sch.PARTY_ADDRESS),
        subtotal=sch.money(totals.findtext(sch.SUBTOTAL), where=f"{sch.TOTALS}/{sch.SUBTOTAL}"),
        vat_amount=sch.money(totals.findtext(sch.VAT_TOTAL), where=f"{sch.TOTALS}/{sch.VAT_TOTAL}"),
        total_amount=_grand_total(totals),
        total_in_words=totals.findtext(sch.TOTAL_IN_WORDS),
        lines=lines,
        ext={
            name: bag
            for name, path in sch.EXT_SCOPES.items()
            if (bag := sch.ext_bag(hdon.find(path)))
        },
    )


def _read(raw: bytes) -> Element:
    """Parse untrusted bytes. Every failure becomes XmlExtractionError.

    `DefusedXmlException` is the base of EntitiesForbidden / DTDForbidden /
    ExternalReferenceForbidden, so catching it covers all three defences at
    once. Verified 2026-07-27: both a billion-laughs payload and an XXE payload
    arrive here as EntitiesForbidden.
    """
    try:
        return fromstring(raw)
    except DefusedXmlException as exc:
        raise XmlExtractionError(f"unsafe XML rejected: {exc}") from exc
    except ParseError as exc:
        raise XmlExtractionError(f"malformed XML: {exc}") from exc


def _require_node(hdon: Element, path: str) -> Element:
    node = hdon.find(path)
    if node is None:
        raise XmlExtractionError(f"{path}: required element missing")
    return node


def _party(node: Element | None, field: str) -> str | None:
    """Seller/buyer field. Absent party is not an error — one fixture has no
    NMua/Ten at all, and buyer MST is missing in three forms: '0', '', absent.
    """
    return None if node is None else node.findtext(field)


def _grand_total(totals: Element) -> Decimal:
    """TgTTTBSo — the one total present in 8/8 fixtures, hence required.

    TgTCThue and TgTThue are absent from 3/8, so they stay Optional.
    """
    value = sch.money(totals.findtext(sch.GRAND_TOTAL), where=f"{sch.TOTALS}/{sch.GRAND_TOTAL}")
    if value is None:
        raise XmlExtractionError(f"{sch.TOTALS}/{sch.GRAND_TOTAL}: required")
    return value


def _line(element: Element, index: int) -> InvoiceLine:
    where = f"{sch.LINES}[{index}]"
    return InvoiceLine(
        line_no=sch.integer(element.findtext(sch.LINE_NO), where=f"{where}/{sch.LINE_NO}"),
        item_kind=sch.integer(element.findtext(sch.LINE_KIND), where=f"{where}/{sch.LINE_KIND}"),
        description=element.findtext(sch.LINE_DESC),
        unit=element.findtext(sch.LINE_UNIT),
        quantity=sch.money(element.findtext(sch.LINE_QTY), where=f"{where}/{sch.LINE_QTY}"),
        unit_price=sch.money(element.findtext(sch.LINE_PRICE), where=f"{where}/{sch.LINE_PRICE}"),
        amount=sch.money(element.findtext(sch.LINE_AMOUNT), where=f"{where}/{sch.LINE_AMOUNT}"),
        vat_rate=sch.vat_rate(
            element.findtext(sch.LINE_VAT_RATE), where=f"{where}/{sch.LINE_VAT_RATE}"
        ),
        vat_amount=sch.money(
            element.findtext(sch.LINE_VAT_AMOUNT), where=f"{where}/{sch.LINE_VAT_AMOUNT}"
        ),
        ext=sch.ext_bag(element.find(sch.EXT_NODE)),
    )
```

- [ ] **Step 4: Export from the package**

Append to `src/invoice_agent/extraction/__init__.py`:

```python
from invoice_agent.extraction.models import InvoiceData, InvoiceLine
from invoice_agent.extraction.xml_parser import XmlExtractionError, parse_invoice_xml

__all__ = ["InvoiceData", "InvoiceLine", "XmlExtractionError", "parse_invoice_xml"]
```

- [ ] **Step 5: Run the full suite**

```bash
.venv/bin/python -m pytest tests/test_xml_parser.py -v
```

Expected: all PASS, including the 8 parametrized golden rows.

If `test_all_real_fixtures_parse` fails on a value, **fix the parser, never the fixture** — the files are the external anchor and are the only thing in the repo nobody here authored.

- [ ] **Step 6: Verify mutations #1 and #4 (behavioural half)**

Mutation #1 — in `xml_parser.py`, replace `from defusedxml.ElementTree import fromstring` with `from xml.etree.ElementTree import fromstring`. Run: `test_entity_declarations_rejected[billion-laughs]` must go **RED**. Revert.

Mutation #4 — in `xml_schema.py`, set `SCHEMA_VERSION = ".//PBan"` and `LINE_QTY = ".//SLuong"`. Run `tests/test_xml_parser.py`: `test_envelope_values_do_not_leak` must go **RED**. Confirm it is red on the *synthetic* fixture while all 8 real fixtures still pass — that is the demonstration that the real corpus cannot catch this bug. Revert.

- [ ] **Step 7: Commit**

```bash
make check
git add src/invoice_agent/extraction/ tests/test_xml_parser.py
git commit -m "feat(extraction): parse_invoice_xml — 8/8 real invoices parse

Pure function, deterministic, raises XmlExtractionError and nothing else. It
never judges whether data is correct: arithmetic, MST checksums and VAT
consistency are M2.6's validate node. That boundary is what makes §3.3's
EXTRACTION-vs-DATA routing decidable — by construction everything raised here
is an extraction error.

Golden table transcribed by hand from 2026-07-27 measurements, never generated
from the parser's own output, which would rebuild the §10.1 circularity.

Mutation-verified: swapping defusedxml for stdlib ET turns the entity test red;
'.//PBan' + './/SLuong' turns test_envelope_values_do_not_leak red while all 8
REAL fixtures still pass — which is precisely why the synthetic fixture exists."
```

---

### Task 7: Sufficiency test — the bridge to M2.6

The M1.2 finding is that `sum(ThTien | TChat==1, fallback TTKhac['Amount']) + TgTThue == TgTTTBSo` holds 8/8 while the naive `sum(ThTien) == TgTTTBSo` flags 4 of 5 real invoices. This task does **not** implement that validator — that is M2.6. It proves `InvoiceData` exposes everything the validator will need, which is far cheaper to learn now than in week 2.

**Files:**
- Modify: `tests/test_xml_parser.py`

**Interfaces:**
- Consumes: `parse_invoice_xml`, `InvoiceData`, `sch.ITEM_KIND_GOODS`, `sch.EXT_KEY_LINE_AMOUNT`.
- Produces: nothing importable.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_xml_parser.py`:

```python
from invoice_agent.extraction import xml_schema as sch


def _total_from_model(invoice) -> Decimal:
    """The M1.2 validation rule, computed FROM InvoiceData rather than from XML.

    Not the validator — that is M2.6's domain/validation.py. This exists only to
    prove the model carries every input the rule needs: item_kind, the line
    amount, the ext['Amount'] fallback, and vat_amount.
    """
    total = Decimal("0")
    for line in invoice.lines:
        # TChat=4 is a NOTE line with no money. Summing it is wrong.
        if line.item_kind != sch.ITEM_KIND_GOODS:
            continue
        amount = line.amount
        if not amount:
            # VNPT sets ThTien=0 and hides the real amount here. Match the key
            # EXACTLY: four other vendors carry
            # 'Thành tiền thanh toán của hàng hóa', which is the GROSS, so a
            # loose match would double-count tax on 4/8 files.
            raw = line.ext.get(sch.EXT_KEY_LINE_AMOUNT)
            amount = Decimal(raw) if raw else Decimal("0")
        total += amount
    # On a VAT invoice ThTien is the amount BEFORE tax; absent means no VAT.
    return total + (invoice.vat_amount or Decimal("0"))


@pytest.mark.parametrize("name", [row[0] for row in GOLDEN])
def test_invoice_data_is_sufficient_for_the_measured_rule(name):
    """8/8 — the same result measured on raw XML on 2026-07-27.

    If this fails, InvoiceData is missing a field that M2.6 will need.
    """
    invoice = _parse(REAL, name)
    assert _total_from_model(invoice) == invoice.total_amount


def test_the_naive_rule_would_be_wrong():
    """The negative control. Without it, the test above could pass for the
    wrong reason and we would never learn that the naive rule is the trap.

    Measured 2026-07-27: sum(ThTien) alone mismatches the stated total on 6 of
    8 real, valid, tax-authority-signed invoices — 5 because ThTien excludes VAT
    (dienluc_, lienket_, newimage_, phongvu_, viettelpost_) and 1 because VNPT
    parks the amount in TTKhac. Only hokinhdoanh_ and ktx_ match, both non-VAT.
    Every mismatch would be a false positive on a clean invoice, i.e. the
    headline metric `flag false-positive rate <= 5%`.

    The older figure "4/5" in plan §M1.2 predates the corpus reaching 8 files;
    it is not a contradiction, just a smaller sample.
    """
    mismatching = 0
    for name, *_ in GOLDEN:
        invoice = _parse(REAL, name)
        naive = sum((line.amount or Decimal("0") for line in invoice.lines), Decimal("0"))
        if naive != invoice.total_amount:
            mismatching += 1
    assert mismatching == 6
```

- [ ] **Step 2: Run the tests**

```bash
.venv/bin/python -m pytest tests/test_xml_parser.py -k "sufficient or naive" -v
```

Expected: the 8 sufficiency rows PASS and `test_the_naive_rule_would_be_wrong` PASSES.

If the sufficiency test fails, `InvoiceData` is missing a field — add it to `models.py` and to the parser, and say which one in the commit message. If `mismatching` is not 5, **do not adjust the number to match**: re-derive it from the fixtures and record what actually happened, because the number is a measurement, not a target.

- [ ] **Step 3: Commit**

```bash
make check
git add tests/test_xml_parser.py
git commit -m "test(extraction): InvoiceData carries what the M1.2 rule needs, 8/8

Recomputes sum(amount | item_kind==1, fallback ext['Amount']) + vat_amount ==
total_amount FROM InvoiceData rather than from XML. Not the validator — that is
M2.6 — but proof the model exposes every input it will need, which is much
cheaper to learn now than in week 2.

Plus the negative control: the naive sum(ThTien) mismatches on 5 of 8 real,
valid, tax-authority-signed invoices. Without that assertion the sufficiency
test could pass for the wrong reason and the trap would stay invisible."
```

---

### Task 8: Close the M1 gate in the plan document

**Files:**
- Modify: `docs/Plan_Project_F_2026-07-25.md`

**Interfaces:**
- Consumes: passing tests from Tasks 1–7.
- Produces: nothing importable.

- [ ] **Step 1: Confirm the whole suite is green before touching the doc**

```bash
make check
```

Expected: ruff clean, mypy clean, every test passing. Record the test count — the M0 baseline was 55, so state the new number.

- [ ] **Step 2: Update the two M1 work items**

In the M1 table (around `docs/Plan_Project_F_2026-07-25.md:253`), replace the `🔲 **Not started**` markers for 1.3 and 1.4 with `✅ **Done 2026-07-27**` plus the evidence, matching the style the M1.5/1.6 rows already use — every claim naming its measurement. Mention specifically:

- the `TTKhac['Amount']` sharpening (only `vnpt_` uses that key; 4 vendors carry a gross-amount key, so a loose match double-counts tax on 4/8)
- the `.//` finding and why the real `TDiep` fixture cannot catch it
- the five mutation verifications
- `defusedxml`, and the measured stdlib entity-expansion behaviour

- [ ] **Step 3: Flip the gate table**

In the "Gate status" table (around line 347), change the fourth row to ✅ with the evidence, and update the heading from `2/3, blocked only on the XML half` to record **4/4**.

- [ ] **Step 4: Update §1 "Vị trí hiện tại"**

Lines 101–102 and 126–127 say 1.3 and 1.4 remain and that they need a brainstorming pass. Replace with the closed state, the spec and plan filenames, and the outstanding items that are genuinely still open: the terminal recording (deferred to M8), ADR `0001-three-graphs-not-one.md`, the NĐ 70/2025 check, and the `TSuat` `KCT`/`KKKNT` question.

Per CLAUDE.md, rewrite **only** these passages. Do not translate surrounding Vietnamese prose — a translation diff mixed into this change would make the functional part unreviewable.

- [ ] **Step 5: Commit**

```bash
git add docs/Plan_Project_F_2026-07-25.md
git commit -m "docs(plan): M1.3 + M1.4 done — M1 gate 4/4

The fourth gate condition ('2-3 real XML parse into InvoiceData') is closed:
8/8 real fixtures parse against a hand-transcribed golden table, and a
sufficiency test proves InvoiceData carries every input the measured M1.2
validation rule needs.

Still open, and named rather than quietly dropped: the terminal recording
(deferred to M8), ADR 0001-three-graphs-not-one.md, the NĐ 70/2025 field check,
and whether TSuat can carry KCT/KKKNT (the parser raises on them today)."
```

---

## Self-Review

**Spec coverage** — every spec section maps to a task:

| Spec section | Task |
|---|---|
| §2.5 defusedxml, types-defusedxml | 1 |
| §2.2 anchored paths, §6.1 constants, §6.1 `EXT_KEY_LINE_AMOUNT` | 2 |
| §6.1 coercer table (all 11 rules) | 3 |
| §6.2 `locate_hdon`, §2.3 synthetic fixtures, §7 fixture table | 4 |
| §4 safety property, §6.3 models | 5 |
| §5 error model, §6 assembly, §7.1 golden table + 12 named tests | 6 |
| §7.1 sufficiency test | 7 |
| §8 success criteria | 8 |
| §7.3 mutation table (all 5) | 2 (#4 cheap half), 3 (#2, #3), 4 (#5), 6 (#1, #4 behavioural) |
| §9 open questions | 3 (`vat_rate` raises), 5 (`item_kind` comment), 8 (recorded in plan doc) |

Two additions beyond the spec, both justified inline: `test_no_field_path_uses_descendant_search` (a cheap static guard alongside the behavioural one) and `test_the_naive_rule_would_be_wrong` (a negative control, without which the sufficiency test could pass for the wrong reason — §10.1 asks for exactly this discipline).

**Type consistency** — checked: `where` is keyword-only on every coercer; `ext_bag` takes `Element | None` and Task 6 passes `hdon.find(path)`, which is `Element | None`; `InvoiceData.ext` is `dict[str, dict[str, str]]` and Task 6's comprehension produces exactly that; `sch.money` is used for `quantity`/`exchange_rate` as well as money, which the docstring states.

**Values verified computationally while writing this plan**, so a failing step means the implementation is wrong, not the plan: the golden table's 8 rows; the naive-rule mismatch count of **6/8** (an earlier draft of this plan said 5 — corrected); `viettelpost_` line 1 `ext['Tiền thuế dòng (Tiền thuế GTGT)'] == '3822'` and `vnpt_` line 1 `ext['VATAmount'] == '0'`; `hokinhdoanh_` line 2 has children `[TChat, STT, THHDVu]` only, so `amount is None` while `description` is present; `hokinhdoanh_` has no `NMua/Ten`. Also verified against the installed libraries: `defusedxml.ElementTree.fromstring` accepts `bytes`; `DefusedXmlException` catches both the billion-laughs and XXE payloads; malformed XML raises `ParseError`; `Decimal('8').scaleb(-2) == Decimal('0.08')` and `Decimal('10').scaleb(-2) == Decimal('0.10')`; `Decimal('NaN')` **succeeds**, which is why `money()` needs the `is_finite()` guard.
