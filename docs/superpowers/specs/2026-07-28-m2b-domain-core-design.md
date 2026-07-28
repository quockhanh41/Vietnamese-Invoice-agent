# Spec — M2-B: `domain/`, the deterministic core (plan item 2.6)

> **Date**: 2026-07-28 · **Milestone**: M2, second of three specs
> **Covers**: plan item **2.6** — `validation.py`, `taxcode.py`, `money.py`, `accounting.py`,
> `budget.py`, `anomaly.py`. Pure functions, zero I/O.
> **Does not cover**: 2.4/2.5/2.7 (M2-A, done 2026-07-28) and 2.1–2.3 (M2-C, the corpus generator).
> **Predecessor**: `docs/superpowers/specs/2026-07-28-m2a-schema-seed-repositories-design.md`

---

## 1. Why this, and what it owes

### 1.1 The gate

M2's gate has five conditions. This spec owns one outright — **≥60 `domain/` unit tests green** — and
supplies the rules that M2-C's corpus is built to exercise.

But the number that matters is not the test count. It is design doc §10.4's headline metric,
**flag false-positive rate ≤5%**, chosen because it measures the thing that decides whether the
system is used at all: reviewer fatigue. Every rule in this milestone is a chance to flag a clean
invoice, so the anchor test is not "60 tests pass" — it is **all 8 real invoices validate with zero
findings**.

### 1.2 Why it is the middle spec

`domain/` is pure and depends on neither the schema nor the corpus. It sits second because M2-C's
generator needs the seeded budget limits from M2-A, and because these rules define what M2-C must be
able to produce a *violation* of.

### 1.3 The circularity rule, restated for rules rather than paths

M1 established that the generator must not read `xml_schema.py`. The same trap exists one level up:
if M2-C seeds defects by inverting `validation.py`'s rules, then "validation recall 100%" proves only
that the generator and the validator share one understanding of what is wrong. **M2-C must not
import `domain/`.** The eight real fixtures are the independent anchor — they are what makes a rule
measurable before any synthetic data exists.

---

## 2. Measurements taken 2026-07-28 for this spec

Every design decision below traces to one of these. All were run against
`tests/fixtures/real_samples/` on the current parser.

### 2.1 The naive arithmetic rule is wrong on 6 of 8 real invoices

```
sum(ThTien) == TgTTTBSo                                    -> WRONG on 6/8
sum(ThTien | TChat==1, fallback ext['Amount']) + TgTThue   -> holds  8/8
```

Unchanged from M1, re-measured here. Three independent facts make the correct rule work, none of
them guessable: `TChat==1` excludes note lines; the `ext['Amount']` fallback recovers VNPT's amount
where `ThTien = 0`; `+ TgTThue` accounts for `ThTien` being pre-tax on VAT invoices.

### 2.2 Line-level comparisons need tolerance; header-level ones do not

| Comparison | Max deviation | Rule |
|---|---:|---|
| `SLuong × DGia` vs `ThTien` | **0.01** | tolerance |
| `ThTien × TSuat` vs `TThue` | **0.48** | tolerance, deviates **both** directions |
| `Σ ThTien` vs `TgTCThue` | 0 | exact — **but see §2.3** |
| `Σ TThue` vs `TgTThue` | 0 | exact — **but see §2.3** |

The 0.01 case is real: `3 × 398181.33 = 1194543.99` but `ThTien = 1194544`. The seller divided the
unit price back out of a rounded line total. Only a multi-line invoice with an odd unit price exposes
it, which is why a generator that always multiplies forward would never produce one.

### 2.3 CORRECTION to the M1 record — the two "exact" rules are conditional

Plan §M1.2 records `Σ ThTien` vs `TgTCThue` and `Σ TThue` vs `TgTThue` as deviation **0**, annotated
"so `==` được" — safe to compare exactly, unconditionally. **Measured 2026-07-28: 5/6 and 4/6.**
Applied as written, `validation.py` would flag **3 of 8 real, valid, tax-authority-signed invoices.**

The three failures, and why:

| Fixture | Observed | Cause |
|---|---|---|
| `ktx_banhang_cccd` | `TgTCThue = 0`, `TgTThue = 0`, line amount = 3,410,000 = total | `KHMSHDon = 2`, a **non-VAT sales invoice**. Its subtotal header is zero, not the sum of lines |
| `dienluc_gtgt` | header `TgTThue = 56,522`, `Σ TThue = 0` | lines state `TSuat = 0.08` but **`TThue` is absent per line** |
| `phongvu_gtgt` | header `TgTThue = 2,161,840.91`, `Σ TThue = 0` | same — per-line VAT not stated, only the header |

Both rules are exact **where they apply**. The guards that make that true:

- Compare `Σ ThTien` against `TgTCThue` **only when `TgTCThue` is present and non-zero.**
- Compare `Σ TThue` against `TgTThue` **only when every goods line states `TThue`.**

Re-measured with the guards: subtotal check runs on 5/8 and is exact **5/5**; VAT check runs on 3/8
and is exact **3/3**.

The M1 claim was not wrong so much as unconditioned — and an unconditioned rule in a validator is a
false-positive generator. This is the third time in this project a recorded measurement has turned
out to be narrower than its wording; it belongs in the record, not in a silent fix.

### 2.4 The MST checksum works, and the fixtures preserve it

The General Department of Taxation 10-digit algorithm — weights `31,29,23,19,17,13,7,5,3` over the
first nine digits, check digit `10 − (Σ mod 11)`:

| Shape | Count | Checksum |
|---|---:|---|
| 10-digit (`0100000003`) | 4 | **valid 4/4** |
| 10-digit + 3-digit branch (`0300000018-042`) | 3 | **valid 3/3** on the 10-digit base |
| 12-digit household (`050000000000`) | 1 | no public rule |

**7/7 checksummable codes pass.** Passing by chance is ~1 in 2×10⁷, so two things are established:
the algorithm is correct, and whoever anonymised the fixtures deliberately preserved valid checksums.
The fixtures are therefore usable as checksum ground truth — which is not something that could be
assumed of anonymised data.

The 12-digit household form is derived from CCCD and has **no known public checksum rule**
(plan §M1.2 open question). It must be accepted unconditionally. A validator that rejects it flags
real invoices, which is the metric this milestone is judged on.

### 2.5 `TgTTTBChu` is a parser, not a regex

All eight real values, with the trap each contributes:

| Value | Amount | Trap |
|---|---:|---|
| `'Bảy trăm sáu mươi ba nghìn không trăm bốn mươi bốn đồng'` | 763,044 | **`không trăm`** zero-filler |
| `'Bảy trăm lẻ hai nghìn đồng'` | 702,000 | **`lẻ`** zero-filler — different word, same job |
| `'Năm mươi mốt nghìn năm trăm chín mươi chín đồng'` | 51,599 | **`mốt`** = 1 in units after a tens word |
| `'Ba triệu bốn trăm mười nghìn  đồng'` | 3,410,000 | `mười` vs `mươi`; **double space** |
| `'Ba  triệu năm trăm ngàn đồng./.'` | 3,500,000 | **`ngàn`** not `nghìn`; double space; **`./.`** suffix |
| `'Mười triệu chín trăm chín mươi bốn nghìn ba trăm chín mươi bảy đồng'` | 10,994,397 | `Mười triệu` leading tens |
| `'Hai mươi ba triệu bảy trăm tám mươi nghìn hai trăm năm mươi đồng'` | 23,780,250 | — |
| `'Chín mươi nghìn đồng'` | 90,000 | — |

**Absent from all eight**: `lăm`/`nhăm` (5 in units after a tens word) and `tư` (4 in the tens
position). Structurally real, unobserved here — the same situation as `KCT`/`KKKNT` in the parser.
Unlike `KCT`, their values are unambiguous, so they are supported and marked as unobserved.

---

## 3. Scope

**In:**

```
src/invoice_agent/domain/__init__.py
src/invoice_agent/domain/findings.py       Finding + flag-code vocabulary (shared by all)
src/invoice_agent/domain/taxcode.py        MST checksum, three shapes
src/invoice_agent/domain/money.py          line/goods totals + parse_amount_words
src/invoice_agent/domain/validation.py     arithmetic · MST · VAT · words-vs-digits
src/invoice_agent/domain/accounting.py     journal-entry builder + balance check
src/invoice_agent/domain/budget.py         serial consumption arithmetic
src/invoice_agent/domain/anomaly.py        new vendor · price outlier · near-duplicate
tests/test_domain_*.py                     one file per module
```

**Out:** anything touching a database, a clock, the network, or an LLM. No repository calls — M3 and
M4 wire these into nodes. No corpus — M2-C.

**Purity is the interface.** Every function takes data and reference values as arguments and returns
values. That is what makes ≥60 tests run in milliseconds, and what lets M4 call them inside a
`SELECT … FOR UPDATE` without surprises.

---

## 4. The safety property constrains what these return

Per CLAUDE.md, the LLM never sets a flag and never posts an entry. `domain/` is the other half of
that sentence: **it is where flags actually come from.**

So these functions return `Finding` values — descriptions of what was observed. They do **not** write
to `invoice_flags`, do not set `invoice.status`, and do not decide approval. M3/M4 pass the results
to `repositories.flags.raise_flag`, whose `raised_by` is constrained by the DB CHECK M2-A created.

`validate()` **never raises.** Design doc §3.3 splits EXTRACTION errors (we misread the document →
escalate strategy) from DATA errors (the invoice itself is wrong → carry a flag to a human).
`XmlExtractionError` belongs to the parser. Keeping `domain/` incapable of producing one puts that
split on the right side of the module boundary.

---

## 5. Modules

### 5.1 `findings.py`

```python
@dataclass(frozen=True)
class Finding:
    code: str          # from the vocabulary below
    severity: str      # info | warn | critical  -> ck_flags_severity
    raised_by: str     # -> ck_flags_raised_by
    detail: dict[str, Any]
```

`raised_by` values must be exactly the six the M2-A CHECK permits — `validation`, `taxcode`,
`anomaly`, `budget`, `duplicate`, `vendor_memory`. They are re-exported from here rather than
retyped, and a test asserts the vocabulary matches `db.models.FLAG_SOURCES`. Otherwise the mismatch
surfaces as an `IntegrityError` at runtime in M3.

Vocabulary:

| `raised_by` | codes | severity |
|---|---|---|
| `validation` | `ARITHMETIC_MISMATCH`, `SUBTOTAL_MISMATCH`, `VAT_TOTAL_MISMATCH`, `LINE_AMOUNT_MISMATCH`, `LINE_VAT_MISMATCH`, `WORDS_DIGITS_MISMATCH` | warn/critical |
| `validation` | `ITEM_KIND_MISSING` | info |
| `taxcode` | `TAX_CODE_CHECKSUM_INVALID`, `TAX_CODE_SHAPE_UNRECOGNISED`, `TAX_CODE_MISSING` | warn |
| `anomaly` | `PRICE_OUTLIER`, `NEAR_DUPLICATE` | warn |
| `vendor_memory` | `NEW_VENDOR` | info |
| `budget` | `BUDGET_WARNING`, `BUDGET_BREACH` | warn/critical |
| `duplicate` | `DUPLICATE_INVOICE` | critical |

### 5.2 `money.py`

```python
def line_amount(line: InvoiceLine) -> Decimal          # ThTien, falling back to ext['Amount']
def goods_subtotal(lines: Sequence[InvoiceLine]) -> Decimal
def parse_amount_words(text: str | None) -> Decimal | None
```

`goods_subtotal` is the one place §2.1's rule lives — exclude `TChat == 4`, apply the `ext['Amount']`
fallback, include `item_kind is None` (see §5.4). Both `validation.py` and `accounting.py` call it;
neither reimplements it.

`parse_amount_words` returns `None` for text it cannot parse, **never `Decimal(0)`** — the same
distinction `xml_schema.money()` already makes, and for the same reason: "unparseable" and "zero" are
different facts, and only one of them is a finding.

Normalisation, in order: collapse whitespace runs, strip the `./.` suffix, lowercase, drop a trailing
`đồng`, then treat `ngàn` as `nghìn`.

### 5.3 `taxcode.py`

```python
def normalise(mst: str | None) -> str | None            # strip spaces; keep the dash
def check_tax_code(mst: str | None) -> Finding | None
```

Three shapes, per §2.4. 10-digit → checksum. 10-digit + `-NNN` → checksum the base, accept any
3-digit branch. 12-digit → **accept, no checksum**, because no public rule exists. Anything else →
`TAX_CODE_SHAPE_UNRECOGNISED`.

Real garbage lives in these fields (`NMua/Ten = '.'`, `DChi = 'Người mua không lấy hóa đơn'`), and
buyer MST is absent or `''` in 8/8 — so a missing buyer MST is **not** a finding. Only a *present but
malformed* code is.

### 5.4 `validation.py`

```python
def validate(invoice: InvoiceData) -> list[Finding]
```

Checks, each independently skippable so one absent field never suppresses the others:

1. **Header arithmetic** — `goods_subtotal + (TgTThue or 0) == TgTTTBSo`, exact →
   `ARITHMETIC_MISMATCH` (critical).
2. **Subtotal** — only when `TgTCThue` is present **and non-zero** (§2.3) → `SUBTOTAL_MISMATCH`.
3. **VAT total** — only when **every** goods line states `TThue` (§2.3) → `VAT_TOTAL_MISMATCH`.
4. **Line amount** — `|SLuong × DGia − ThTien| ≤ 0.01`, only when all three present →
   `LINE_AMOUNT_MISMATCH`.
5. **Line VAT** — `|ThTien × TSuat − TThue| ≤ 0.50`, only when all three present →
   `LINE_VAT_MISMATCH`. Threshold is the measured 0.48 rounded up to a round number; the comment
   records both.
6. **Words vs digits** — `parse_amount_words(TgTTTBChu) == TgTTTBSo`, skipped when the parse returns
   `None` → `WORDS_DIGITS_MISMATCH`.
7. **Tax codes** — delegate to `taxcode.check_tax_code` for seller and buyer.
8. **`item_kind is None`** — include the line in `goods_subtotal` as goods, and emit
   `ITEM_KIND_MISSING` (info). Under-counting would drop real money and produce a false
   `ARITHMETIC_MISMATCH`; over-counting a note line is at least visible. Revisit when NĐ 70/2025
   answers whether `TChat` may be omitted with an implied default of 1.

### 5.5 `accounting.py`

```python
@dataclass(frozen=True)
class JournalLine:
    account_code: str; account_name: str
    debit: Decimal; credit: Decimal; description: str | None

def build_journal_entry(invoice, accounts: CategoryAccounts) -> list[JournalLine]
```

Produces the shape design doc §2.7 gives:

```
Nợ 6422   3.250.000      debit  = goods_subtotal
Nợ 1331     260.000      debit  = vat_amount            (omitted when zero/absent)
    Có 331          3.510.000    credit = goods_subtotal + vat_amount
```

> **Corrected 2026-07-28.** This block first read `credit = total_amount`, which is wrong on exactly
> the invoices that matter. On a clean invoice the two are identical, but the 6% "sai số học" defect
> group is defined by `total_amount` disagreeing with the lines — and §3.3 sends those to a **human**,
> who may approve them, so M5 will still try to post one. Measured: `subtotal=1000, vat=100,
> declared total=1200` gives `debit=1100 / credit=1200`, which `ck_journal_balanced` refuses at
> INSERT. Crediting the computed sum makes the entry balance **by construction**, so the constraint
> is a backstop rather than the thing that discovers the bug. M2-A's
> `test_unbalanced_journal_entry_rejected` already proves the database refuses the other version.

**It must use `money.goods_subtotal`, not `invoice.subtotal`.** Measured §2.3: on the non-VAT fixture
`TgTCThue = 0` while the real amount is 3,410,000 — building from the raw header would emit
`Nợ 6422 0 / Có 331 3.410.000`, which is unbalanced and would be refused by
`ck_journal_balanced`. That constraint would catch it; this note is so it never gets there.

`accounts` is passed in, sourced from the seeded `expense_categories` row. No database access here.

### 5.6 `budget.py`

```python
@dataclass(frozen=True)
class Consumption:
    invoice_id: Any; amount: Decimal
    balance_before: Decimal; balance_after: Decimal
    sequence_no: int; findings: list[Finding]

def consume(limit: Decimal, warn_pct: Decimal, invoices: Sequence[BudgetInput]) -> list[Consumption]
```

**`consume` sorts internally** by the canonical key `(issue_date, invoice_series, invoice_number)`.
It does not trust the caller's order — that is the entire point. M4's
`test_budget_serialization.py` shuffles arrival order and asserts the `balance_after` chain is
byte-identical and the *same* invoice breaches; a function that consumed in the given order would
make that test unprovable.

Findings, evaluated per invoice on the balance **after** that invoice is consumed:

- `BUDGET_BREACH` (critical) when `balance_after < 0`.
- `BUDGET_WARNING` (warn) when `balance_after >= 0` **and** cumulative spend has reached
  `warn_pct` of the limit — i.e. `balance_after <= limit × (1 − warn_pct)`. With the seeded default
  `warn_threshold_pct = 0.8`, that is "80% or more of the budget is gone".

The two are mutually exclusive: a breaching invoice gets `BUDGET_BREACH` only, so a reviewer sees one
finding per invoice rather than two saying the same thing. Locking, row writes and
`SELECT … FOR UPDATE` are M4.5.

### 5.7 `anomaly.py`

```python
def new_vendor(stats: VendorStats | None) -> Finding | None
def price_outlier(amount: Decimal, stats: VendorStats) -> Finding | None
def near_duplicate(candidate: InvoiceKey, priors: Sequence[InvoiceKey]) -> Finding | None
```

> **Module name ≠ `raised_by`.** `new_vendor` lives in `anomaly.py` but its finding carries
> `raised_by="vendor_memory"`, because that is the producer the M2-A CHECK names and the reviewer-
> facing meaning is "entity memory says this vendor is new". The mapping is data in `findings.py`,
> not a function of which file the code sits in.

Structural signals only (design decision, 2026-07-28). `near_duplicate` fires on: same
`seller_tax_code` **and** same `total_amount` **and** `|issue_date − prior.issue_date| ≤
NEAR_DUP_WINDOW_DAYS` **and** different `invoice_number`. No fuzzy string matching and no new
dependency.

`price_outlier` is a z-score against `VendorStats.avg_amount` / `stddev_amount`, skipped when
`invoice_count` is below a minimum or `stddev` is zero — a vendor with two invoices has no meaningful
distribution, and computing one is how a clean invoice gets flagged.

> **Honesty requirement for the two thresholds.** `NEAR_DUP_WINDOW_DAYS`, `PRICE_OUTLIER_Z` and
> `PRICE_OUTLIER_MIN_INVOICES` are **provisional and unmeasured** — the corpus that would justify
> them does not exist until M2-C, and no real-fixture measurement can produce them. CLAUDE.md
> requires every non-obvious constant to carry date + measurement + report file. These cannot yet, so
> each must carry a comment saying exactly that, naming M6 as where the number gets set from
> `docs/eval-report.md`. A fabricated justification is worse than an admitted gap.
>
> If M6 shows fraud recall < 0.90 with structural signals alone, a description-similarity channel is
> added **then**, with before/after numbers (principle 2). If it is dropped, the reason is recorded —
> that is portfolio content, not a failure.

---

## 6. Tests

One file per module, plus a cross-cutting anchor.

### 6.1 The anchor

`test_domain_no_false_positives_on_real_invoices` — **all 8 real fixtures produce zero findings**
from `validate()`. This is design doc §10.4's headline metric in miniature, and it is measurable
today, before any synthetic invoice exists. It is the single test most likely to catch a rule written
one notch too strict.

### 6.2 Mutation verification

The M1 lesson, applied up front. A validator test is unusually easy to write so that it passes while
guarding nothing, because feeding it a *valid* invoice passes whether or not the rule runs.

| Mutation | Guard that must go red |
|---|---|
| Drop the "non-zero" guard on the subtotal check (§2.3) | `test_..._no_false_positives_on_real_invoices` |
| Drop the "every line states TThue" guard (§2.3) | `test_..._no_false_positives_on_real_invoices` |
| `goods_subtotal` stops excluding `TChat == 4` | `test_note_lines_are_not_money` |
| `goods_subtotal` drops the `ext['Amount']` fallback | `test_vnpt_amount_comes_from_ext` |
| Line tolerance `0.01 → 0` (exact compare) | `test_rounded_unit_price_is_tolerated` |
| `parse_amount_words` treats `mốt` as unknown | `test_parses_all_eight_real_amount_words` |
| `taxcode` rejects the 12-digit household shape | `test_household_tax_code_accepted_without_checksum` |
| `taxcode` checksum weights altered | `test_real_tax_codes_pass_checksum` |
| `budget.consume` uses caller order instead of sorting | `test_consume_is_order_independent` |
| `build_journal_entry` uses `invoice.subtotal` | `test_journal_entry_balances_on_non_vat_invoice` |

### 6.3 Count

≥60 tests (M2 gate). Roughly: money 14, taxcode 10, validation 18, accounting 8, budget 8, anomaly 8,
plus the anchor and the vocabulary-consistency test.

---

## 7. Success criteria

1. All 8 real fixtures → `validate()` returns **zero findings**.
2. Every seeded-defect *shape* named in §2 produces exactly the expected finding, tested on
   hand-built `InvoiceData` values (not on corpus data, which does not exist).
3. `parse_amount_words` returns the correct `Decimal` for **8/8** real `TgTTTBChu` values.
4. All 7 checksummable real MSTs validate; the 12-digit household code is accepted.
5. `budget.consume` is order-independent — shuffled input, identical `balance_after` chain.
6. `build_journal_entry` balances on both a VAT and a non-VAT invoice.
7. ≥60 domain tests; all 10 mutations in §6.2 verified red.
8. `make check` green. No module in `domain/` imports `sqlalchemy`, `psycopg`, or anything from
   `db/` — asserted by a test, not by intention.

---

## 8. Open questions — recorded, not silently dropped

1. **NĐ 70/2025 / `item_kind`** — inherited from M1.2. Resolved *provisionally* here (§5.4: include
   as goods + info flag); the regulation still has the final say.
2. **12-digit household MST checksum** — no public rule found. Accepted unconditionally.
3. **`lăm` / `tư`** — unobserved in the corpus, supported anyway because their values are
   unambiguous. Flagged in code as unobserved.
4. **`KCT` / `KKKNT`** — still rejected by `xml_schema.vat_rate()`. Unchanged by this spec; M2-C must
   not generate them until the parser is extended.
5. **`NEAR_DUP_WINDOW_DAYS`, `PRICE_OUTLIER_Z`, `PRICE_OUTLIER_MIN_INVOICES`** — provisional, set at
   M6 from the eval report. Must say so in their comments.
