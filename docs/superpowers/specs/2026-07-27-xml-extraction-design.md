# Spec — XML extraction: `xml_schema.py` + `xml_parser.py` (M1.3 + M1.4)

> **Date**: 2026-07-27 · **Scope**: M1.3, M1.4 · **Status**: awaiting review
> Sister spec: *HITL durability (M1.5 + M1.6)* — done, fully independent of this one.
> Authored in English per the 2026-07-27 language rule. The **product** stays Vietnamese:
> every element name, `TTKhac` key, and fixture value quoted here is domain data, not prose.

## 1. Why this, and why it is the last thing M1 owes

The M1 gate has four conditions. Three are closed. The fourth — *"2–3 real XML parse into
`InvoiceData`"* — is the whole of what remains, and nothing in M2 may open until it holds.

The purpose of the gate is narrow and worth restating, because it is the reason this work cannot be
done by writing a parser from memory of QĐ-1450. From plan §10.1: the M2 generator produces the
corpus, and we also write the parser. If both rest on the same wrong understanding of the format,
the parser scores 100% and that 100% proves only that the generator and the parser agree with each
other. The report would then say `false-positive rate 0%` and that number would be invented.

M1.2 already demonstrated this is not hypothetical. The structure table written from memory was
**wrong in four places**, and the naive validation rule `sum(ThTien) == TgTTTBSo` flags real, valid,
tax-authority-signed invoices as arithmetic errors. Plan §M1.2 records **4 of 5**, measured when the
corpus held 5 files; re-measured 2026-07-27 across all 8 it is **6 of 8** — five because `ThTien`
excludes VAT (`dienluc_`, `lienket_`, `newimage_`, `phongvu_`, `viettelpost_`) and one because VNPT
parks the amount in `TTKhac`. Only the two non-VAT invoices `hokinhdoanh_` and `ktx_` match. Every
one of those six is a false positive on a clean invoice — precisely the headline metric
`flag false-positive rate ≤5%`.

So the ordering rule stands: `xml_schema.py` is written against the files in
`tests/fixtures/real_samples/` **first**, and the Jinja generator at M2.1 is written **after and
independently**.

## 2. Measurements taken 2026-07-27 for this spec

Everything below was measured against all 8 fixtures on the day of writing. Where a decision in this
spec cites a number, this is where the number comes from.

### 2.1 The `TTKhac[Amount]` fallback is VNPT-specific, and near-miss keys exist

Plan §M1.2 and the fixtures README both record the validation rule as
`sum(ThTien | TChat==1, fallback TTKhac[Amount]) + TgTThue == TgTTTBSo`. Re-measuring found a trap
the wording hides: **only `vnpt_…` uses the literal key `Amount`.** Four other vendors carry a
line-level extension key that also looks like an amount:

| Vendor file | Line-level `TTKhac` key | Value | What it actually is |
|---|---|---:|---|
| `vnpt_…` | `Amount` | `3500000` | the line amount — `ThTien` is `0`, this is the real number |
| `lienket_…` (line 1) | `Thành tiền thanh toán của hàng hóa` | `1720144` | **gross**: `ThTien` 1592726 + `TThue` 127418 |
| `newimage_…` | same key | `702000` | **gross**: 650000 + 52000 |
| `viettelpost_…` | same key | `51599` | **gross**: 47777 + 3822 |

Matching loosely — "any amount-shaped `TTKhac` key" — would double-count tax on 4 of 8 files. The
fallback must match the key `Amount` **exactly**. Re-ran the full rule with an exact match:
**8/8 hold**, confirming the documented finding under the sharper reading.

### 2.2 The `TDiep` envelope makes `.//` searches actively wrong

`phongvu_gtgt_tdiep_2.0.0.xml` has root `TDiep`, and its `TTChung` envelope carries its **own**
`PBan`, its own `MST`, and its own `SLuong`. That last one is *the count of invoices in the
envelope*, not a line quantity:

```
TDiep/TTChung:  PBan=2.0.0   MST=0300000018-042   SLuong=1   MLTDiep=202
TDiep/DLieu/HDon/DLHDon/TTChung:  PBan=2.0.0
TDiep/DLieu/HDon/…/HHDVu[1]:      SLuong=1
```

So `.//SLuong` finds the envelope's invoice count before any line quantity, and `.//MST` finds the
transmitter's tax code before the seller's. Every path in `xml_schema.py` is therefore anchored at
`HDon` and relative to it. `.//` appears nowhere except in locating `HDon` itself.

### 2.3 The real fixture cannot catch that bug — a synthetic one is required

In this specific file all three collisions **coincide in value**: envelope `PBan` equals invoice
`PBan` (`2.0.0`), envelope `MST` equals the seller's `MST`, envelope `SLuong` equals the line
`SLuong` (`1`). A parser written entirely with `.//` passes every assertion that can be made against
this fixture.

This is the same failure mode the M1.5/M1.6 review caught twice: a test that stays green when you
break the thing it guards. The regression test therefore needs a small hand-built envelope whose
envelope-level values are deliberately distinct from its invoice-level values (§7).

### 2.4 `Decimal` survives checkpointing — risk closed, not deferred

`InvoiceData` lands in `EnrichmentState`, which `PostgresSaver` serializes, so `Decimal` fields are
only safe if the serializer round-trips them. Measured with the pinned `langgraph==1.2.9`:
`JsonPlusSerializer` takes the **msgpack** path and returns `Decimal("21618409.09000000")`
unchanged — trailing zeros intact, `type` still `Decimal`. No workaround, no wrapper, no `str`
smuggling.

### 2.5 stdlib `ElementTree` expands internal entities; `defusedxml` stops it

On this repo's Python 3.13.5:

| Payload | stdlib `xml.etree.ElementTree` | `defusedxml` 0.7.1 |
|---|---|---|
| external entity (XXE, `file:///etc/hostname`) | `ParseError: undefined entity` — safe | `EntitiesForbidden` |
| billion-laughs, 11 nested levels | **expands to 4096 chars** — live memory DoS | `EntitiesForbidden` |
| bare `<!DOCTYPE r>`, no entity declarations | parses | parses |

Vendor XML is untrusted input, so `defusedxml` is a dependency, not a nicety. Note the mechanism:
`defusedxml.ElementTree.fromstring` defaults are `forbid_dtd=False, forbid_entities=True,
forbid_external=True` — protection comes from **`EntitiesForbidden`**, not `DTDForbidden`, and a
harmless DOCTYPE is still accepted. Tests must assert the former.

`defusedxml` ships no `py.typed`, so dev dependencies gain `types-defusedxml`; verified that
`fromstring` then reveals `Element[str]` and mypy is clean. No `[tool.mypy.overrides]` entry needed.

### 2.6 Smaller facts that shape the model

| Fact | Measured |
|---|---|
| `STT` and `TChat` present | 15/15 lines, 8/8 files |
| Text nodes with leading/trailing whitespace | **0** |
| Distinct header-level `TTKhac` scopes | **6** (`DLHDon`, `TTChung`, `NBan`, `NMua`, `NDHDon`, `TToan`) plus one per line |
| Duplicate `TTruong` within one scope | **0** |
| Empty `TTKhac` (element present, no `TTin`) | 1 — `ktx_…/TToan` |
| `TToan` child order | differs per vendor; `THTTLTSuat` first at Viettel, last at Phong Vũ |
| `TTKhac/TTin` child order | `DLieu,KDLieu,TTruong` at Viettel vs `TTruong,KDLieu,DLieu` at VNPT |

## 3. Scope

**In scope:**

- `src/invoice_agent/extraction/xml_schema.py` — M1.3
- `src/invoice_agent/extraction/xml_parser.py` — M1.4
- `src/invoice_agent/extraction/models.py` — `InvoiceData`, `InvoiceLine`
- `tests/fixtures/synthetic/` — two hand-built XML files that the real corpus cannot provide
- `tests/test_xml_schema.py`, `tests/test_xml_parser.py`
- `pyproject.toml` — `defusedxml`, dev `types-defusedxml`
- plan doc: flip 1.3, 1.4 and gate row 4

**Deliberately NOT doing** (decisions, not omissions):

- **Validation** — `domain/validation.py` is M2.6. The parser never judges whether data is
  *plausible*; see §5.
- **Signature verification** — the `xmldsig#` subtree is skipped entirely. `raw_xml` is a DB column
  (§2.3), so nothing is unrecoverable.
- **Multi-invoice `TDiep` support** — an envelope with N>1 `HDon` **raises**. Supporting it is not M1.
- **Discount fields** — §2.4 of the design doc has no column, and `TLCKhau`/`STCKhau` are empty or
  absent in all 8 files. They exist in `xml_schema.py` only as empty-element coercion cases.
- **`KCT` / `KKKNT` non-percent `TSuat` codes** — known to exist in the wild, absent from the
  corpus. The parser raises loudly rather than guessing a semantic. See §9.
- **DB persistence** — repositories are M2.7.
- **The generator** — M2.1, and it must stay independent of `xml_schema.py`.

## 4. The safety property constrains the model

At M3.4, `InvoiceData` becomes the **structured-output schema for an LLM vision call**. Per
CLAUDE.md, structured output *is* the security boundary, so every field on this model is a field the
LLM can write.

`InvoiceData` therefore carries **no** `category`, `department`, `status`,
`classification_confidence`, and no flag or approval field — even though the `invoices` table
(§2.3) has those columns. It is the extraction *contract*, not the DB row. The test to apply before
adding any field later: could the LLM use this to self-authorize?

## 5. Error model

```python
def parse_invoice_xml(raw: bytes) -> InvoiceData:
    """Raises XmlExtractionError. Makes no claim about whether the data is correct."""
```

The parser is a pure function: structurally readable in → `InvoiceData` out, otherwise raise.
Nothing in between, and no soft-warning channel.

This puts design doc §3.3's EXTRACTION-vs-DATA distinction on the right side of the module boundary
by construction — **everything the parser raises is an extraction error**, and every data judgement
belongs to the `validate` node at M2.6. That distinction is the actual content of the
self-correction loop, so it must not be blurred here.

| Raises `XmlExtractionError` | Does **not** raise |
|---|---|
| malformed XML, or entity declarations present | `NMua/Ten` absent (real: 1/8 files) |
| no `HDon` locatable | `NMua/MST` is `'0'`, `''`, or absent (real: most files) |
| `TDiep` envelope with N ≠ 1 `HDon` | `TSuat` absent (real: all non-VAT invoices) |
| no `DSHHDVu/HHDVu` line | `THDon` is free-text garbage (real: 6 variants) |
| `KHMSHDon`, `KHHDon`, `SHDon`, `NLap`, `TgTTTBSo` absent | `TgTCThue`/`TgTThue` absent (real: 3/8) |
| a required numeric field present but unparseable | `sum(lines) != TgTTTBSo` — that is a **data** error |

## 6. Architecture

```
src/invoice_agent/extraction/
  __init__.py
  models.py       InvoiceData, InvoiceLine — the extraction contract.
                  Also the M3.4 LLM structured-output schema (§4).
  xml_schema.py   M1.3. ONE place holding element paths, TTKhac keys, coercers.
                  Written against real fixtures; the M2.1 generator must not read it.
                  Also DEFINES XmlExtractionError — see below.
  xml_parser.py   M1.4. locate HDon -> explicit findtext assembly -> InvoiceData.
```

`XmlExtractionError` is defined in `xml_schema.py`, not in `xml_parser.py`, because the coercers
raise it (`money` on unparseable input, `vat_rate` on an unknown code, `locate_hdon` on an
unrecognised root). `xml_parser` imports from `xml_schema`, so defining the exception in the parser
would make the import circular. `xml_parser` re-exports it so callers have one import site.

### 6.1 `xml_schema.py`

Path constants anchored at `HDon`, the `TTKhac` key constants, and the coercion primitives. No field
table and no generic walker: the irregularities that matter here — `TChat` filtering,
order-independent `TTKhac` lookup, `'8%'`, empty-versus-missing — do not fit a declarative table
without becoming a mini-DSL plus escape hatches. Explicit calls stay readable, and a reviewer can
diff this module against the Jinja template by eye, which is the point of the artefact.

```python
HDON_TAG = "HDon"
TTCHUNG  = "DLHDon/TTChung"
NDHDON   = "DLHDon/NDHDon"
SELLER   = f"{NDHDON}/NBan"
BUYER    = f"{NDHDON}/NMua"
LINES    = f"{NDHDON}/DSHHDVu/HHDVu"
TOTALS   = f"{NDHDON}/TToan"

# Six TTKhac scopes observed across the 8 fixtures (measured 2026-07-27,
# spec docs/superpowers/specs/2026-07-27-xml-extraction-design.md §2.6).
# Name -> path relative to HDon.
EXT_SCOPES = {
    "dlhdon":  "DLHDon/TTKhac",
    "ttchung": f"{TTCHUNG}/TTKhac",
    "seller":  f"{SELLER}/TTKhac",
    "buyer":   f"{BUYER}/TTKhac",
    "ndhdon":  f"{NDHDON}/TTKhac",
    "totals":  f"{TOTALS}/TTKhac",
}

# Measured 2026-07-27 on all 8 fixtures: only vnpt_… uses this exact key.
# Four vendors put the GROSS amount under 'Thành tiền thanh toán của hàng hóa'
# (lienket_ line 1: 1720144 == ThTien 1592726 + TThue 127418), so matching
# loosely double-counts tax on 4/8 files. Match this key exactly.
EXT_KEY_LINE_AMOUNT = "Amount"

# TChat — QĐ-1450 line character. 4 is a NOTE line: THHDVu only, no money.
ITEM_KIND_GOODS = 1
ITEM_KIND_NOTE = 4

# KHMSHDon is the ONLY trustworthy invoice-kind signal. THDon is free-text
# garbage: 6 variants observed including 'TT78 AC'.
TEMPLATE_NO_VAT = "1"
TEMPLATE_NO_SALES = "2"
```

Coercers, each rule traceable to a measurement:

| Coercer | Rule | Measurement behind it |
|---|---|---|
| `locate_hdon` | see §6.2 | two root shapes; envelope carries a count |
| `money` | `None`/`''` → `None`, **never** `Decimal(0)` | `<SLuong />` in `vnpt_…`; `0` and absent are different facts |
| `money` | `Decimal(str)`, `float` nowhere | `21618409.09000000` — 8 decimal places |
| `money` | present but unparseable → raise | garbage in a numeric field means we misread the document |
| `vat_rate` | `'8%'` → `Decimal('0.08')`; `None`/`''` → `None` | `TSuat` is a string with `%`, absent on non-VAT invoices |
| `vat_rate` | anything else non-empty → raise | `KCT`/`KKKNT` unobserved; §9 |
| `ext_bag` | read `TTruong`/`KDLieu`/`DLieu` **by tag, never position** | child order reverses between Viettel and VNPT |
| `ext_bag` | keys verbatim; empty `TTKhac` → `{}`; missing/empty `DLieu` → `''` | `ktx_…/TToan/TTKhac` is empty; absence is already encoded by the key being absent |
| `ext_bag` | duplicate `TTruong` → last-wins | 0 duplicates in the corpus, so this is a documented choice, not a measurement |
| `issue_date` | `date.fromisoformat` | `NLap` is `YYYY-MM-DD` in 8/8 |
| all text | **verbatim — no strip, no collapse** | `'Ba  triệu năm trăm ngàn đồng./.'`; M2.6's normalizer must face the real double space |

### 6.2 `locate_hdon`

```
root.tag == "HDon"   -> root
root.tag == "TDiep"  -> exactly one HDon under DLieu/, else raise
otherwise            -> raise XmlExtractionError
```

The uniqueness check is load-bearing, not defensive decoration: `TDiep/TTChung/SLuong` *is* the
invoice count in the envelope. Our fixture has `SLuong=1`, so N>1 is structurally real but
unobserved, and a parser that silently took the first `HDon` would drop the rest of a batch without
a sound. Raising is the correct behaviour for M1 — the enrichment graph escalates an extraction
error to vision or `manual_queue` (§3.3), so a new envelope shape surfaces as a routed failure
rather than as silent data loss.

### 6.3 `models.py`

```python
class InvoiceLine(BaseModel):
    line_no: int | None          # STT
    item_kind: int | None        # TChat — 1 goods, 2 promo, 3 discount, 4 NOTE
    description: str | None      # THHDVu  ⚠️ prompt-injection surface (design §2.4)
    unit: str | None             # DVTinh — absent on non-VAT invoices
    quantity: Decimal | None     # SLuong — <SLuong /> is None, not 0
    unit_price: Decimal | None   # DGia
    amount: Decimal | None       # ThTien — 0 at VNPT, real value in ext['Amount']
    vat_rate: Decimal | None     # TSuat  — '8%' -> Decimal('0.08')
    vat_amount: Decimal | None   # TThue
    ext: dict[str, str]          # whole TTKhac verbatim

class InvoiceData(BaseModel):
    schema_version: str | None       # PBan — 2.0.0 / 2.0.1 / 2.1.0 all in corpus
    template_no: str                 # KHMSHDon — required
    invoice_series: str              # KHHDon   — required
    invoice_number: str              # SHDon    — required
    issue_date: date                 # NLap     — required
    currency: str | None             # DVTTe
    exchange_rate: Decimal | None    # TGia — '1' and '1.00' both occur

    seller_name: str | None          # NBan/Ten
    seller_tax_code: str | None      # NBan/MST — 3 shapes: 10, 10-3, 12 digits
    seller_address: str | None
    buyer_name: str | None           # NMua/Ten — absent in 1/8 files
    buyer_tax_code: str | None       # '0' / '' / absent are three different facts
    buyer_address: str | None        # real value: 'Người mua không lấy hóa đơn'

    subtotal: Decimal | None         # TgTCThue — absent 3/8
    vat_amount: Decimal | None       # TgTThue  — absent 3/8
    total_amount: Decimal            # TgTTTBSo — required
    total_in_words: str | None       # TgTTTBChu — verbatim, double spaces intact

    lines: list[InvoiceLine]         # non-empty
    ext: dict[str, dict[str, str]]   # EXT_SCOPES name -> bag
```

The `ext` bags have no consumer yet. They are kept because `TTKhac` already proved load-bearing
once — VNPT hides the line amount there — so the corpus has demonstrated that this extension point
carries semantics we did not predict.

Three deliberate omissions:

- **No `invoice_kind`.** `template_no` *is* `KHMSHDon`, and `KHMSHDon` is the only trustworthy kind
  signal. A second field would duplicate it and invite the two to disagree.
- **No discount fields.** No column in §2.4, and empty or absent in 8/8.
- **No signature data.** `raw_xml` covers forensics.

`item_kind` is `int | None`, **not** `int = 1`. `TChat` is present on 15/15 lines, but whether
QĐ-1450 makes it optional with an implied default of `1` is *not verified*, and a wrong default
would silently fold a `TChat=4` note line into the money sum — the exact bug the measured validation
rule exists to prevent. That default waits on M1.2's outstanding NĐ 70/2025 check.

## 7. Tests

Two new fixture files under `tests/fixtures/synthetic/`, because the real corpus provably cannot
cover these cases (§2.3):

| File | Purpose |
|---|---|
| `tdiep_envelope_distinct.xml` | envelope `PBan=9.9.9`, `MST=9999999999`, `SLuong=99`; invoice `PBan=2.1.0`, seller `MST=0104093672`, line `SLuong=3` — every collision point differs, so a `.//` parser fails |
| `tdiep_two_invoices.xml` | one `TDiep`, two `HDon` — must raise |

Both are hand-written, minimal, and carry no PII. Verified that `.gitignore`'s `!tests/**/*.xml`
whitelist makes files under `tests/fixtures/synthetic/` committable.

### 7.1 `tests/test_xml_parser.py`

All 8 real fixtures parametrized against a **hand-written golden table** (`invoice_number`,
`template_no`, `total_amount`, line count, `item_kind` sequence), transcribed from the §2
measurements. It is never generated from the parser's own output — that would rebuild the circularity
this milestone exists to break.

| Test | Asserts |
|---|---|
| `test_all_real_fixtures_parse` | 8/8 parse, golden table matches |
| `test_empty_element_is_none_not_zero` | `vnpt_…` line `quantity is None` |
| `test_note_line_has_no_money` | `hokinhdoanh_…` line 2 → `item_kind == 4`, `amount is None` |
| `test_vnpt_amount_hidden_in_ext` | `amount == 0` **and** `ext["Amount"] == "3500000"` |
| `test_decimal_precision_preserved` | `str(total_amount) == "23780250.00000000"` |
| `test_words_amount_verbatim` | `'Ba  triệu năm trăm ngàn đồng./.'` — double space and `./.` intact |
| `test_ext_bag_is_order_independent` | Viettel (reversed child order) reads identically to VNPT |
| `test_missing_buyer_is_not_an_error` | `hokinhdoanh_…` → `buyer_name is None`, parses fine |
| `test_envelope_values_do_not_leak` | synthetic envelope → `schema_version == '2.1.0'` not `'9.9.9'`; `seller_tax_code == '0104093672'` not `'9999999999'`; `lines[0].quantity == 3` not `99` — all three, since any one alone could pass by luck |
| `test_multi_invoice_envelope_raises` | synthetic 2-`HDon` `TDiep` → `XmlExtractionError` |
| `test_entity_expansion_rejected` | billion-laughs → `XmlExtractionError` wrapping `EntitiesForbidden` |
| `test_external_entity_rejected` | XXE → `XmlExtractionError` |
| `test_invoice_data_is_sufficient_for_the_measured_rule` | see below |

The last one is the bridge to M2.6. It recomputes
`sum(amount | item_kind==1, fallback ext['Amount']) + vat_amount == total_amount` **from
`InvoiceData`**, not from the XML, and asserts 8/8. It does not implement the validator; it proves
the model exposes everything the validator will need. If it fails, `InvoiceData` is missing a field —
which is far cheaper to learn now than at M2.6.

### 7.2 `tests/test_xml_schema.py`

Coercer units: `money('')`/`money(None)` → `None`; `money('0')` → `Decimal('0')`;
`money('21618409.09000000')` keeps precision; `money('abc')` raises. `vat_rate('8%')` →
`Decimal('0.08')`; `vat_rate('10%')` → `Decimal('0.10')`; `vat_rate(None)` → `None`;
`vat_rate('KCT')` raises. `ext_bag` order independence, empty `TTKhac` → `{}`, duplicate key
last-wins.

### 7.3 Mutation verification — required, not optional

M1.5 and M1.6 each shipped a first cut that asserted less than it claimed: one tested a *copy* of
`include_object`, the other asserted a re-run count it never measured. Each guard below must be
confirmed **red** when the thing it guards is broken, and the confirmation recorded in the commit
message.

| # | Break this | Expect red |
|---|---|---|
| 1 | swap `defusedxml` for stdlib `ET` | `test_entity_expansion_rejected` |
| 2 | make `ext_bag` read `TTin` children by position | `test_ext_bag_is_order_independent` |
| 3 | make `money('')` return `Decimal(0)` | `test_empty_element_is_none_not_zero` |
| 4 | change any anchored path to `.//` | `test_envelope_values_do_not_leak` |
| 5 | drop the single-`HDon` check in `locate_hdon` | `test_multi_invoice_envelope_raises` |

Mutation 4 is the one that matters most, because §2.3 shows it is the mutation the real corpus
cannot detect.

## 8. Success criteria (= the last M1 gate condition)

| Criterion | Verified by |
|---|---|
| `make check` green — ruff, mypy, pytest | `make check` |
| 8/8 real fixtures parse, golden table matches | `test_all_real_fixtures_parse` |
| `InvoiceData` sufficient for the measured rule, 8/8 | `test_invoice_data_is_sufficient_for_the_measured_rule` |
| All five mutations confirmed red | manual, recorded in commit message |
| `defusedxml` + `types-defusedxml` pinned, `requirements.lock` regenerated | `make lock` |
| Plan gate row 4 flips ✅ | plan doc edit → **M1 gate 4/4** |

## 9. Open questions — recorded, not silently dropped

- **Non-percent `TSuat` codes.** `KCT` (không chịu thuế), `KKKNT`, and `\` are believed to occur in
  real invoices but appear in **none** of the 8 fixtures. The parser raises on them rather than
  guessing a rate, so the first such invoice fails loudly with a named reason instead of being
  assigned a wrong tax rate. Revisit when a fixture proves the shape — do not encode from memory,
  which is the mistake M1.2 already paid for once.
- **`TChat` default.** Present on 15/15 lines. Whether QĐ-1450 permits omission with an implied `1`
  is unverified; blocked on M1.2's NĐ 70/2025 check. Until then `None` means absent and M2.6 must
  decide explicitly.
- **12-digit household-business MST checksum** — already recorded for M2.6; unchanged by this spec.
- **Corpus gaps that will exercise this parser later**: non-zero discount, adjustment/replacement
  invoices (`HDCTTChinh` is `'0'` in the 3 files that have it), multiple `LTSuat` rates on one
  invoice, MISA meInvoice, BKAV. None block this gate.
