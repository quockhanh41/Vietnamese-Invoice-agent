# Spec — M2-C: the synthetic corpus generator (plan items 2.1, 2.2, 2.3)

> **Date**: 2026-07-28 · **Milestone**: M2, third and last spec
> **Covers**: **2.1** (`scripts/generate_invoices.py --count 300 --seed 42`, Jinja, written
> independently of `xml_schema.py`), **2.2** (14 defect groups per §10.2, `defects` as a **list**),
> **2.3** (order-dependent budget-overrun case).
> **Predecessors**: `2026-07-28-m2a-schema-seed-repositories-design.md` (done),
> `2026-07-28-m2b-domain-core-design.md` (done).

---

## 1. Why this, and what it owes

### 1.1 The gate

M2's gate has five conditions. M2-A closed two, M2-B closed one. This spec closes the remaining two:

- `make generate && make seed` runs clean — the `generate` half.
- **Parser round-trip: 300/300 synthetic invoices parse, plus the 8 real files.**

### 1.2 This milestone exists to make the eval report honest

Design doc §10.1 states the problem plainly: you generate the XML, you generate the ground truth,
**and** you write the parser. 100% F1 then means nothing, and an interviewer will find that first.

Four mechanisms break the loop; three of them are this spec's:

1. Generator from **Jinja templates**, parser from a **path map written independently**.
2. `tests/fixtures/real_samples/` — 8 real invoices that must parse, already green.
3. Every LLM metric reported beside the `keywords_vi` baseline (M6).
4. Negative controls for injection (§9.3) — the twins generated here.

**The risk is not hypothetical.** M1 measured that a structure table written from memory was wrong in
four places, and that the naive arithmetic rule flags 6 of 8 real invoices. A generator written from
the same memory would have produced 300 invoices that agree with the wrong rule, scored 300/300, and
printed `false-positive rate 0%` — an invented number.

---

## 2. Measurements taken 2026-07-28 for this spec

### 2.1 A generator cannot invent tax codes

10,000 random 10-digit strings, checked against the General Department of Taxation algorithm:

```
valid: 955 / 10000  =  9.6%
```

A generator that makes up MSTs would trip `TAX_CODE_CHECKSUM_INVALID` on roughly **90% of its own
clean corpus**, destroying the false-positive measurement before it is taken.

The inverse is also measured: for a valid code, **all 9 alternative check digits are invalid**. So the
4% "MST sai checksum" group needs no algorithm — perturb the last digit.

**Consequence**: a frozen table of ~40 pre-computed valid MSTs, provenance in a comment. The
generator contains **zero** checksum logic, which is stronger independence than a second
implementation could give.

### 2.2 The order-dependent budget case works against the real seeded limit

Against `scripts/seed_reference.BUDGETS` → MKT 2026-07 = **50,000,000**, three invoices whose
invoice-number order is deliberately the reverse of their canonical order:

| id | issue_date | number | amount |
|---|---|---:|---:|
| MKT-A | 2026-07-20 | 00000901 | 20,000,000 |
| MKT-B | 2026-07-10 | 00000902 | 20,000,000 |
| MKT-C | 2026-07-05 | 00000903 | 15,000,000 |

```
canonical order (issue_date, series, number)  ->  C, B, A  ->  A breaches, always
naive arrival-order walk                      ->  ANY of A, B, C can breach
```

That is exactly item 2.3: the same invoice must breach under every shuffle, and a naive
implementation would pick a different one. Note the deliberate detail — **filename/number order is
not canonical order**, or shuffling the input would prove nothing.

### 2.3 Jinja2 is already available; no PDF library is

`jinja2==3.1.6` is in `requirements.lock`. Nothing can render a PDF, which is one input to the
decision in §5.4.

---

## 3. Scope

**In:**

```
scripts/generate_invoices.py       CLI: --count 300 --seed 42 --out corpus/
scripts/generator/__init__.py
scripts/generator/vendors.py       frozen MST pool + vendor profiles
scripts/generator/amounts.py       money helpers + number -> words (independent)
scripts/generator/templates.py     Jinja environment + rendering
scripts/generator/defects.py       the 14 injectors
scripts/generator/manifest.py      ground-truth records
scripts/generator/templates/hdon.xml.j2
scripts/generator/templates/tdiep.xml.j2
tests/test_generator_*.py
Makefile                           + `make generate`
```

**Out:** PDF bytes (§5.4) · injection variants 7–26 and the injection *measurement* (M6.1) · anything
that reads the corpus (M3+).

**Location.** `scripts/`, not `src/`. The independence boundary should be visible without reading
imports: production code in `src/`, corpus tooling outside it.

---

## 4. Independence — the mechanism, not the intention

### 4.1 What is banned

`test_generator_imports_neither_parser_nor_domain` asserts that nothing under `scripts/generator/` or
`scripts/generate_invoices.py` imports:

- `invoice_agent.extraction.xml_schema` — the path map
- `invoice_agent.extraction.xml_parser`
- `invoice_agent.domain` — any of it

### 4.2 What is deliberately allowed

`scripts.seed_reference` for `BUDGETS`. M2-A §7 made that a designed dependency: the budget limits
must exist in one place, or item 2.3's overrun case is tuned against a second copy and the test
starts passing for the wrong reason.

### 4.3 What is therefore written twice, on purpose

| Written twice | Generator side | Production side |
|---|---|---|
| QĐ-1450 element paths | Jinja templates | `xml_schema.py` |
| Vietnamese number → words | `amounts.py` | `money.parse_amount_words` (opposite direction) |

And written **zero** times in the generator: the MST checksum (§2.1).

Duplication is the price of a meaningful score. It is also bounded — two small modules — and the
alternative is an eval report whose headline number is invented.

### 4.4 Finding codes cross the boundary as strings

The manifest names expected findings as literal strings (`"ARITHMETIC_MISMATCH"`). A test asserts
every string appears in `domain.findings.CODE_REGISTRY`. The **test** imports both sides; the
generator imports neither. Same pattern M2-B used for the flag vocabulary against the DB CHECK.

---

## 5. What the corpus must contain

### 5.1 The hard shapes

If the corpus is easier than reality, every number measured on it is optimistic. Each row below was
measured on a real file during M1/M2-B, and at least one generated invoice must carry it — asserted
by test (§8).

| Shape | Taught by |
|---|---|
| Root `HDon` **and** root `TDiep` envelope (~7:1) | 7/8 vs `phongvu_` |
| `TChat=4` note line, description only, no money | `hokinhdoanh_` |
| `ThTien=0` with the real amount in `TTKhac['Amount']` | `vnpt_` |
| `TSuat` as the string `'8%'` / `'10%'` | GTGT files |
| Odd unit price: `SLuong × DGia ≠ ThTien`, within 0.01 | `lienket_` (3 × 398181.33 = 1194543.99 vs 1194544) |
| `TTKhac` child order differing between vendors | Viettel `DLieu,KDLieu,TTruong` vs VNPT `TTruong,KDLieu,DLieu` |
| Self-closing `<SLuong />` → `None`, not `0` | `vnpt_` |
| `TgTTTBChu` using `ngàn`, double spaces, `./.` suffix | `vnpt_` |
| All three MST shapes | 10-digit, dashed branch, 12-digit household |
| Non-VAT invoice: `KHMSHDon=2`, `TgTCThue=0`, `TgTThue=0` | `ktx_banhang_` |
| VAT stated only at header, `TThue` absent per line | `dienluc_`, `phongvu_` |

The last two exist because M2-B §2.3 measured that they are what make the subtotal and VAT checks
conditional. A corpus without them cannot exercise those guards.

### 5.2 What is deliberately NOT generated

`TSuat` codes `KCT` and `KKKNT`. `xml_schema.vat_rate()` rejects them loudly because no fixture
proves their shape, and generating data the parser cannot read would manufacture a failure that
teaches nothing. Recorded as an open question, not silently skipped.

### 5.3 The 14 defect groups, and why `defects` is a list

Counts at `--count 300`, from §10.2:

| Group | % | n | Group | % | n |
|---|--:|--:|---|--:|--:|
| Clean, auto-postable | 65 | 195 | Near-duplicate / fraud | 3 | 9 |
| Arithmetic error | 6 | 18 | New vendor + large amount | 3 | 9 |
| Words vs digits | 2 | 6 | Budget overrun | 4 | 12 |
| MST checksum | 4 | 12 | Price outlier | 2 | 6 |
| Wrong VAT rate | 3 | 9 | Prompt injection | 2 | 6 |
| Exact duplicate | 3 | 9 | PDF-only | 4 | 12 |
| Ambiguous category | 3 | 9 | Unreadable PDF | 1 | 3 |

Defect counts sum to 120 across ~105 distinct invoices, because **some invoices carry more than
one**. That is the point: real invoices arrive as "new vendor *and* over budget", and a one-label
manifest makes multi-flag evaluation impossible (§10.2). The total remains 300 distinct files.

Injection is 6 rather than §10.2's 1.5% (≈5) because §9.1 specifies exactly six payloads. Each gets a
payload-free **twin**, and the twins count toward the clean group.

Exact duplicates are 9 **files** whose natural key repeats an earlier invoice — the corpus exercises
`ON CONFLICT DO NOTHING`, so duplicate files are the point, not an accident.

### 5.4 PDF groups are manifest-only

The ~15 PDF entries get full manifest records — identity, defect list, expected routing — but no
bytes. M3.4 renders them when vision exists to consume them.

Nothing in the M2 gate reads a PDF: "round-trip 300/300" is `xml_parser`, which cannot read one. The
alternative is adding a rendering dependency, plus layout and Vietnamese-diacritic font decisions,
five weeks before anything consumes the output.

### 5.5 Injection payloads and their twins

The six §9.1 payloads verbatim, in the line `description` field. Payloads 5 (social engineering,
*"Đã được Giám đốc phê duyệt qua email"*) and 6 (category laundering) matter most, because they carry
no injection markers at all and defeat every keyword detector.

Each payload invoice has a twin that is **byte-identical except the `description`**. §9.3 calls the
negative control the thing everyone forgets: without it, a classification difference measures noise,
not injection. The pairing is recorded in the manifest because it is generation-time truth —
reconstructing it at M6 against a frozen corpus invites drift.

The payload text stays Vietnamese/English as written. It is data the system ingests, and translating
it changes what is being tested (CLAUDE.md).

---

## 6. Manifest

`corpus/manifest.jsonl`, one JSON object per line, one line per invoice:

```
invoice_id, path (null for PDF-only), source_type,
seller_tax_code, template_no, invoice_series, invoice_number, issue_date,
department, category, total_amount,
defects: [str],                  # LIST, may be empty
expected_finding_codes: [str],   # cross-checked against domain.findings
twin_of: str | null,             # injection negative control pairing
duplicate_of: str | null,        # for the exact-duplicate group
canonical_budget_rank: int | null
```

JSONL rather than one JSON document so a 300-line corpus stays diffable and greppable, and so M6 can
stream it.

---

## 7. Determinism

`--seed 42` produces **byte-identical** output, including manifest ordering. A single
`random.Random(seed)` is threaded through; no module-level `random`, no `datetime.now()`, no
`uuid4()`. Invoice ids are derived from the seed and index.

Without this the eval is not reproducible and no before/after comparison in M6 means anything.

---

## 8. Tests

| Test | Asserts |
|---|---|
| `test_generator_imports_neither_parser_nor_domain` | the §4.1 ban, by source scan |
| `test_same_seed_produces_identical_bytes` | determinism, files **and** manifest |
| `test_every_generated_xml_parses` | **M2 gate** — round-trip through `xml_parser`; 285 files at count=300 |
| `test_manifest_accounts_for_every_entry` | 285 XML + 15 PDF-only = 300; nothing silently missing |
| `test_defect_ratios_match_the_design_doc` | §5.3 counts, exact at count=300 |
| `test_every_hard_shape_is_present` | one assertion per §5.1 row |
| `test_clean_invoices_produce_no_findings` | cross-check against `domain.validate` |
| `test_seeded_defects_produce_their_expected_findings` | manifest `expected_finding_codes` are actually raised |
| `test_budget_case_is_order_dependent` | item 2.3, shuffled arrival order |
| `test_injection_twin_differs_only_in_description` | §9.3 negative control is genuinely controlled |
| `test_manifest_codes_exist_in_domain_findings` | §4.4 |

### 8.1 One test that is double-edged, and how to read it

`test_clean_invoices_produce_no_findings` is a genuine cross-check: the generator is written from the
regulation and the real files, not from `domain/`, so agreement is evidence.

**But when it fails, the reflex will be to fix the generator.** Sometimes the validator is what is
wrong — that is exactly what happened twice already, at M1 (naive rule, 6/8) and M2-B (unconditioned
subtotal/VAT rules, 3/8). **The 8 real fixtures arbitrate.** If a generated invoice and a real
invoice disagree about a rule, the real one wins.

---

## 9. Success criteria

1. `make generate && make seed` runs clean from a fresh checkout.
2. **Every generated XML invoice parses into `InvoiceData`**, and the 8 real fixtures still parse.

   > **What "round-trip 300/300" means here.** The M2 gate says "parser round-trip 300/300 hóa đơn
   > synthetic". At `--count 300` the corpus is **300 manifest entries**, of which **285 carry XML**
   > and 15 are PDF-only (§5.4). The measurable claim is therefore **285/285 XML files parse, and 15
   > entries are declared unparseable-by-design in the manifest** — 300 accounted for, none silently
   > missing. Writing "300/300 parsed" would be false the moment anyone counted the files, and this
   > project has already had two claims that were narrower than their wording.
3. Defect counts match §5.3 exactly at `--count 300 --seed 42`.
4. Same seed → byte-identical corpus and manifest.
5. Every §5.1 hard shape appears in at least one invoice.
6. Clean invoices produce zero findings from `domain.validate`.
7. Every seeded defect produces the finding its manifest entry predicts.
8. The item 2.3 budget case: same invoice breaches under all 10 shuffles.
9. No file under `scripts/generator/` imports the parser or `domain/`.
10. `make check` green.

---

## 10. Open questions — recorded, not silently dropped

1. **`KCT` / `KKKNT`** — not generated (§5.2). Revisit if a real fixture ever proves the shape; the
   parser must be extended first.
2. **PDF rendering** — deferred to M3.4. The manifest reserves the entries.
3. **Injection variants 7–26** — M6.1. The six base payloads and their twins are generated here.
4. **NĐ 70/2025 / `item_kind`** — inherited. The generator emits `TChat` on every line, so the
   corpus does not depend on the answer; M2-B's `ITEM_KIND_MISSING` path stays exercised only by
   unit tests until it does.
5. **Corpus is not committed to git.** 300 XML files carrying synthetic PII-shaped data would bloat
   the repo and blur the line with `tests/fixtures/real_samples/`. `corpus/` is gitignored and
   regenerated from the seed; the manifest schema and generator are what get committed. Revisit if
   CI needs a pre-built corpus.
