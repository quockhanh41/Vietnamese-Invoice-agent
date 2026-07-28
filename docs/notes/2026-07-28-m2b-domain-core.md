# Note: M2-B Domain Core Closeout

> **Date**: 2026-07-28
> **Milestone**: M2-B (`src/invoice_agent/domain/`)
> **Status**: Completed · 106 domain unit tests passing · 266 total project tests passing

---

## 1. Anchor Verification

The headline metric **flag false-positive rate** is verified against all 8 real XML invoice fixtures in `tests/fixtures/real_samples/`:

- **8/8 real invoices produce ZERO findings** from `validation.validate()`.
- **8/8 real `TgTTTBChu` strings parse to the exact total amount** via `money.parse_amount_words()`.
- **Fixture presence guard** `test_fixtures_are_present()` confirmed active (8 files).
- **Purity assertion** confirmed zero I/O imports (`sqlalchemy`, `psycopg`, `invoice_agent.db`, network).

---

## 2. Mutation Verification Results

All 10 guard mutation tests were executed from scratch and verified to turn red when broken:

| # | Mutation | Target Test | Result |
|---|---|---|---|
| 1 | `if invoice.subtotal:` → `is not None` | `test_subtotal_check_skipped_when_subtotal_is_zero` | **RED** |
| 2 | Delete `all(ln.vat_amount is not None ...)` | `test_vat_total_check_skipped_when_any_line_omits_line_vat` | **RED** |
| 3 | `goods_subtotal` includes `ITEM_KIND_NOTE` | `test_note_lines_are_not_money` | **RED** |
| 4 | `line_amount` drops `ext['Amount']` fallback | `test_vnpt_amount_comes_from_ext` | **RED** |
| 5 | `LINE_AMOUNT_TOLERANCE = 0` | `test_rounded_unit_price_is_tolerated` | **RED** |
| 6 | Remove `mốt` from `_UNIT_VARIANTS` | `test_parses_all_eight_real_amount_words` | **RED** |
| 7 | `taxcode` rejects 12-digit shape | `test_household_tax_code_accepted_without_checksum` | **RED** |
| 8 | Alter checksum weight | `test_real_tax_codes_pass_checksum` | **RED** |
| 9 | `budget.consume` uses caller order | `test_consume_is_order_independent` | **RED** |
| 10 | `build_journal_entry` uses `invoice.subtotal` | `test_journal_entry_balances_on_non_vat_invoice` | **RED** |

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

- `src/invoice_agent/domain/` package fully implemented with 6 pure modules:
  - `findings.py`
  - `money.py`
  - `taxcode.py`
  - `validation.py`
  - `accounting.py`
  - `budget.py`
  - `anomaly.py`
- M2-B gate condition cleared: 106 domain unit tests (gate requires ≥60).
- Ready for **M2-C** (Corpus Generator). Reminder: **M2-C must NOT import `domain/`**.
