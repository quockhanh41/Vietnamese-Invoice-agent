# Note: M2-B Domain Core Closeout

> **Date**: 2026-07-28
> **Milestone**: M2-B (`src/invoice_agent/domain/`)
> **Status**: Completed, then reviewed 2026-07-28 · 107 domain unit tests · 267 total project tests

---

## 1. Anchor Verification

The headline metric **flag false-positive rate** is verified against all 8 real XML invoice fixtures in `tests/fixtures/real_samples/`:

- **8/8 real invoices produce ZERO findings** from `validation.validate()`.
- **8/8 real `TgTTTBChu` strings parse to the exact total amount** via `money.parse_amount_words()`.
- **Fixture presence guard** `test_fixtures_are_present()` confirmed active (8 files).
- **Purity assertion** confirmed zero I/O imports (`sqlalchemy`, `psycopg`, `invoice_agent.db`, network).

---

## 2. Mutation Verification Results

Re-run independently during review on 2026-07-28. **Row 3 did not hold the first time** — see the
correction below the table.

| # | Mutation | Target Test | Result |
|---|---|---|---|
| 1 | `if invoice.subtotal:` → `is not None` | `test_subtotal_check_skipped_when_subtotal_is_zero` | **RED** |
| 2 | Delete `all(ln.vat_amount is not None ...)` | `test_vat_total_check_skipped_when_any_line_omits_line_vat` | **RED** |
| 3 | `goods_subtotal` includes `ITEM_KIND_NOTE` | `test_note_lines_are_not_money` | **RED** *(after fix — see below)* |
| 4 | `line_amount` drops `ext['Amount']` fallback | `test_vnpt_amount_comes_from_ext` | **RED** |
| 5 | `LINE_AMOUNT_TOLERANCE = 0` | `test_rounded_unit_price_is_tolerated` | **RED** |
| 6 | Remove `mốt` from `_UNIT_VARIANTS` | `test_parses_all_eight_real_amount_words` | **RED** |
| 7 | `taxcode` rejects 12-digit shape | `test_household_tax_code_accepted_without_checksum` | **RED** |
| 8 | Alter checksum weight | `test_real_tax_codes_pass_checksum` | **RED** |
| 9 | `budget.consume` uses caller order | `test_consume_is_order_independent` | **RED** |
| 10 | `build_journal_entry` uses `invoice.subtotal` | `test_journal_entry_balances_on_non_vat_invoice` | **RED** |

### Correction — row 3 was reported RED before it was true

This note's first version claimed all ten were verified. Re-measured during review: replacing the
`ITEM_KIND_NOTE` branch in `line_amount` with `if False:` left `test_note_lines_are_not_money`
**GREEN**.

The cause was in the test, not the rule. Its note line was built with `amount=None` and no `ext`, and
`line_amount` returns `Decimal("0")` for an empty line regardless — so the assertion held whether or
not the guard existed. A test labelled "MUTATION GUARD" that guards nothing is the exact pattern M1
produced four times.

Fixed by giving the note line `amount=Decimal("500")`, plus a second case
(`test_note_line_with_money_is_still_excluded_via_the_ext_fallback`) covering the other route by
which a note line can carry money. Both now go red under the mutation. The defect originated in the
implementation plan's test code, not in `money.py` — `line_amount` was always correct.

**Method note.** Byte-length-preserving mutations — `(…, 5, 3)` → `(…, 5, 5)` — restore within the
same mtime granularity, so CPython reuses the *mutated* `.pyc`. Measured 2026-07-28: that made a
clean `taxcode.py` fail its own checksum test after the run. Any mutation harness for this repo must
clear `__pycache__` around each case, or its results cannot be trusted.

---

## 3. Spec Correction & Journal Entry Balancing

- **Journal Credit Balancing**: Crediting payable as `goods_subtotal + vat` (instead of `invoice.total_amount`) guarantees journal entries balance by construction even on invoices containing header arithmetic defects.

---

## 4. Provisional Constants

The following constants are unmeasured at M2-B time and explicitly commented as provisional until M6 eval:

- `NEAR_DUP_WINDOW_DAYS = 7`
- `PRICE_OUTLIER_Z = Decimal("3")`
- `PRICE_OUTLIER_MIN_INVOICES = 5`

---

## 5. Summary & Next Steps

- `src/invoice_agent/domain/` package fully implemented with 7 pure modules:
  - `findings.py`
  - `money.py`
  - `taxcode.py`
  - `validation.py`
  - `accounting.py`
  - `budget.py`
  - `anomaly.py`
- M2-B gate condition cleared: 106 domain unit tests (gate requires ≥60).
- Ready for **M2-C** (Corpus Generator). Reminder: **M2-C must NOT import `domain/`**.
