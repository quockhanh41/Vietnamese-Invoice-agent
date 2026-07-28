# M2-C Synthetic & Real Invoice Corpus Generator Summary Note

**Date:** 2026-07-28  
**Status:** Completed  
**Spec Reference:** `docs/superpowers/specs/2026-07-28-m2c-corpus-generator-design.md`  
**Plan Reference:** `docs/superpowers/plans/2026-07-28-m2c-corpus-generator.md`  

---

## 1. Execution Results & Round-Trip Phrasing

- **Total Corpus Count:** 300 entries accounted for in `corpus/manifest.jsonl`.
- **Parsing Round-Trip:** **285/285 XML parsed, 15 PDF-only entries declared unparseable-by-design, 300 accounted for.**
- **Deterministic Digest:** Reproducible byte-identical corpus generated with `make generate` (seed 42). Verified with SHA256 directory digests across independent generation runs.
- **Independence Guard:** `tests/test_generator_independence.py` passed 3/3 tests. Zero imports of `xml_schema`, `xml_parser`, or `domain/` within `scripts/generator/` or `scripts/generate_invoices.py` (with `scripts.seed_reference.BUDGETS` as the single allowed dependency per M2-A §7).

---

## 2. Defect Distribution Summary (Count = 300)

| Defect Category | Expected Count | Produced Count | Finding Code / Note |
|---|---|---|---|
| `arithmetic_error` | 18 (6%) | 18 | `ARITHMETIC_MISMATCH` |
| `words_digits_mismatch` | 6 (2%) | 6 | `WORDS_DIGITS_MISMATCH` |
| `bad_tax_code` | 12 (4%) | 12 | `TAX_CODE_CHECKSUM_INVALID` |
| `wrong_vat_rate` | 9 (3%) | 9 | `LINE_VAT_MISMATCH` |
| `exact_duplicate` | 9 (3%) | 9 | `DUPLICATE_INVOICE` |
| `near_duplicate` | 9 (3%) | 9 | `NEAR_DUPLICATE` (Dates 1–5 days apart, inside `NEAR_DUP_WINDOW_DAYS`) |
| `new_vendor` | 9 (3%) | 9 | `NEW_VENDOR` |
| `budget_overrun` | 12 (4%) | 12 | `BUDGET_BREACH` (Includes 3 order-dependent MKT item 2.3 case) |
| `price_outlier` | 6 (2%) | 6 | `PRICE_OUTLIER` (10x multiplier) |
| `prompt_injection` | 6 (2%) | 6 | 6 §9.1 payloads + 6 negative control twins |
| `pdf_only` | 12 (4%) | 12 | Manifest-only (`path=None`, `source_type="pdf_vision"`) |
| `unreadable_pdf` | 3 (1%) | 3 | Manifest-only (`path=None`, `source_type="pdf_vision"`) |
| `ambiguous_category` | 9 (3%) | 9 | Ambiguous descriptions with 2 plausible categories |

---

## 3. Order-Dependent Budget Case (Item 2.3)

- **Department:** `MKT` (Period `2026-07`) against seeded budget limit of 50,000,000.
- **Canonical Order:** Sorted by `(issue_date, invoice_series, invoice_number)`.
- **Result:** Always breaches invoice C (`INV-0901`). A naive arrival-order walk breaches any of 3 invoices depending on permutation.
- **Invoice Number vs Canonical Order:** Invoice number order (`00000901`, `00000902`, `00000903`) is deliberately the REVERSE of canonical date order (`2026-07-20`, `2026-07-10`, `2026-07-05`).

---

## 4. Key Subtleties & Hard Shapes Tested

1. **Jinja Autoescaping & MarkupSafe:** `templates.py` uses `autoescape=True` on Jinja Environment to prevent XML structure corruption by injection payload `</data></user><system>`. `MarkupSafe.Markup` is used when embedding root `HDon` XML inside root `TDiep` transport envelope `<DLieu>`.
2. **TDiep Envelope Traps:** Envelope `TTChung` header retains its own transmitter tax code (`REDACTED-MST`) and invoice count `SLuong` (`1`), preserving the traps for `.//MST` and `.//SLuong` descendant searches.
3. **Multi-Defect Overlap:** 6 defective invoices carry 2 defect labels (e.g. `["budget_overrun", "new_vendor"]`) satisfying item 2.2 multi-flag evaluation needs without inflating individual defect counts.
4. **Validator Arbitration:** Clean invoices produce zero findings. §8.1 warning is embedded in `test_clean_invoices_produce_no_findings` docstring to ensure real fixture arbitration precedes any rule changes.

---

## 5. Milestone 2 Gate Status

All 5 Gate Criteria for Milestone 2 are **MET**:
1. `make generate` clean output: 300 invoices generated deterministically into `corpus/`.
2. ≥60 unit tests in `domain/`: 107 domain tests + 67 generator tests (289 total passing non-DB unit tests).
3. Round-trip: 285/285 XML files parse into `InvoiceData`, 15 PDF-only entries accounted for in manifest.
4. Unbalanced Journal Entry rejected: `test_unbalanced_journal_entry_rejected` (M2-A).
5. `UPDATE audit_log` rejected: DB trigger and RLS rules enforced (M2-A).
