# Manual baseline protocol — the one number an agent cannot measure

Status: **protocol only, no measurement taken yet**. The Business/STP row in the eval report
ships as `PENDING — awaiting operator measurement` until a human runs this protocol with an
actual stopwatch and fills in the numbers this document leaves as placeholders.

## 0. Why this exists

Design §10.4 is explicit about how the "time saved" business number must be produced:

> 20 phút bỏ ra biến một lời nói suông thành một phép đo. Đừng trích báo cáo thị trường cho con số
> của chính mình.
>
> ("20 minutes spent turns an empty claim into a measurement. Don't quote a market report for
> your own number.")

An LLM agent cannot pick up a phone, open a spreadsheet, and type invoice fields by hand while a
clock runs — the entire point is that a *human* does the keying, at human speed, so the number
reflects what this system actually replaces. That is why M6-C ships this row as PENDING rather
than a guess, an omission, or a citation of some AP-automation vendor's marketing page. This
document is the fixed protocol so that whoever later runs the 20 minutes doesn't have to
re-derive the method, and so the resulting number is reproducible and auditable rather than
whatever felt convenient on the day.

## 1. The ten invoices

Corpus: `corpus/manifest.jsonl`, 300 invoices total (285 `xml`, 15 `pdf_vision`), issue dates
`2026-07-01` to `2026-07-25`. The manifest's `defects` field records the seeded defect
distribution; the 18/12/12/9/9/6 counts quoted below were counted directly from the manifest,
not guessed.

| # | Invoice ID | Category | Defect class | Why it's in the sample |
|---|---|---|---|---|
| 1 | `INV-0124` | LOGISTICS | none (clean) | Baseline case: a bookkeeper transcribing an invoice with no anomaly, nothing to double back on. |
| 2 | `INV-0129` | OFFICE_SUPPLIES | none (clean) | Second clean baseline, different category, to check that "clean" timing isn't an artifact of one vendor's layout. |
| 3 | `INV-0016` | LOGISTICS | `arithmetic_error` | Largest single defect class in the corpus (18/285, ≈6.3%). Included twice (with #4) because a human who actually checks the math — `SLuong × DGia` against `ThTien`, and `TgTCThue + TgTThue` against `TgTTTBSo` — spends longer than one who doesn't, and the whole point of §10.4's instruction is to *not* skip that check. |
| 4 | `INV-0021` | OFFICE_SUPPLIES | `arithmetic_error` | Second arithmetic-error sample, different category, so the extra time isn't attributed to one vendor's formatting instead of the defect itself. |
| 5 | `INV-0040` | LOGISTICS | `bad_tax_code` | Requires the operator to actually look at the MST field and notice it's malformed rather than just copy it — a distinct kind of friction from an arithmetic check. |
| 6 | `INV-0061` | MARKETING | `budget_overrun` | Represents the second-largest defect class (12/285). Not detectable by inspecting the invoice alone (it needs the department's running budget), so this case measures pure transcription time with no extra on-page verification — a useful contrast with #3/#4/#5. |
| 7 | `INV-0079` | MARKETING | `near_duplicate` | A human doing manual entry over a run of invoices is the one realistic setting where near-duplicates get *caught* (déjà vu on the seller/amount), unlike a single-invoice LLM call. Included to see whether that recognition costs visible time even without a side-by-side compare tool. |
| 8 | `INV-0100` | OFFICE_SUPPLIES | `ambiguous_category` | Tests whether an ambiguous line-item description slows down transcription itself (not just downstream classification, which this protocol doesn't measure). |
| 9 | `INV-0088` | LOGISTICS | `price_outlier` | An unusually high unit price for its category — checks whether a human keying the row visibly hesitates on it the way a validation rule would flag it. |
| 10 | `INV-0004` | LOGISTICS | `prompt_injection` | Included deliberately: the line-item description (`THHDVu`) carries an embedded instruction string aimed at an LLM reader. A human retyping the same field is structurally immune to it — there is no "prompt" for a person copying text into a cell. Keeping it in the sample makes that asymmetry visible in the timing data instead of asserting it. |

**Mix rationale.** 2 clean / 2 arithmetic-error / 6 single-instance defect classes covering
`bad_tax_code`, `budget_overrun`, `near_duplicate`, `ambiguous_category`, `price_outlier`, and
`prompt_injection`. This is a representativeness sample, not a convenience sample — every ID was
selected by filtering the manifest for `defects == [class]` (a single defect, no compounding) and
`twin_of` / `duplicate_of` both null, so none of the ten is entangled with another corpus row in a
way that would make its timing depend on having already keyed a different invoice. `arithmetic_error`
gets two slots because it's both the largest class and the one design §10.4 explicitly calls out as
costing more human time; every other listed class gets one, to keep the sample at ten without
diluting it into twenty categories at one invoice each.

**Honest limitation found while selecting these:** every one of the 285 XML invoices in this
corpus has exactly **one** line item (`grep -c '<HHDVu>'` returns 1 for all 285 files). There is no
"multi-line invoice" variant to sample — the corpus's complexity axis is defect type, not line-item
count. If a future corpus revision adds multi-line invoices, this protocol's sample should add one,
but as of this corpus generation there is nothing to add.

## 2. Fields to key

Match `src/invoice_agent/extraction/models.py`'s `InvoiceData` / `InvoiceLine` — the fields the
system itself extracts, not an invented spreadsheet schema. `currency` (`DVTTe`) and
`exchange_rate` (`TGia`) are omitted from the timed columns: every one of the 285 XML invoices
carries `VND` / `1`, so there is nothing to key and nothing to time. Buyer fields
(`buyer_name`, `buyer_tax_code`, `buyer_address`) are also omitted — in a real AP workflow the
buyer is the operator's own company and is constant across the batch, so it isn't part of the
per-invoice keying cost.

| Spreadsheet column | `InvoiceData` field | XML tag | Notes |
|---|---|---|---|
| Mẫu số | `template_no` | `KHMSHDon` | `1` = GTGT, `2` = bán hàng |
| Ký hiệu | `invoice_series` | `KHHDon` | e.g. `C26TBB` |
| Số hoá đơn | `invoice_number` | `SHDon` | |
| Ngày lập | `issue_date` | `NLap` | |
| Tên người bán | `seller_name` | `NBan/Ten` | |
| MST người bán | `seller_tax_code` | `NBan/MST` | This is the field `bad_tax_code` (#5) targets — key it verbatim, don't auto-correct it |
| Mô tả hàng hoá/dịch vụ | `lines[0].description` | `THHDVu` | This is the field `prompt_injection` (#10) targets |
| Số lượng | `lines[0].quantity` | `SLuong` | |
| Đơn giá | `lines[0].unit_price` | `DGia` | |
| Thành tiền | `lines[0].amount` | `ThTien` | |
| Thuế suất | `lines[0].vat_rate` | `TSuat` | |
| Tiền thuế | `lines[0].vat_amount` | `TThue` | |
| Tổng tiền chưa thuế | `subtotal` | `TgTCThue` | |
| Tổng tiền thuế | `vat_amount` | `TgTThue` | |
| Tổng tiền thanh toán | `total_amount` | `TgTTTBSo` | |
| Tổng tiền bằng chữ | `total_in_words` | `TgTTTBChu` | Keyed so the words-vs-digits cross-check (§3 below) has something to check against |

16 columns per invoice, single line item, matching exactly what `InvoiceData` would hold after
extraction — so the manual-baseline timing is comparable to what the automated pipeline produces,
not to some richer or poorer spreadsheet of the operator's own design.

## 3. How to time it

- **Instrument:** a phone stopwatch with a lap/split function, or a spreadsheet formula that reads
  the current timestamp (`=NOW()`) into a cell when triggered — either is fine, but use the same
  method for all ten invoices in one session and record which method was used.
- **Start condition:** the stopwatch starts the moment the invoice file (XML rendered in a viewer,
  or the source PDF) is opened for that specific invoice — not before, not "whenever I remember to
  glance at the clock."
- **Stop condition:** the stopwatch stops when the spreadsheet row is fully keyed (all 16 columns
  in §2 filled) **and** the operator has done the correctness check below. The correctness check is
  *inside* the timed interval, not a separate untimed step — skipping it would erase exactly the
  extra seconds an `arithmetic_error` or `bad_tax_code` invoice costs a real bookkeeper, which is
  the effect this protocol exists to capture.
- **Correctness check, every invoice:** (a) does `SLuong × DGia` equal `ThTien`? (b) does
  `TgTCThue + TgTThue` equal `TgTTTBSo`? (c) does `TgTTTBChu`, read as a number, match
  `TgTTTBSo`? Do the check by eye/mental arithmetic, the way a bookkeeper actually would — not with
  a calculator macro that would itself save time this measurement is trying to capture.
- **Interruptions:** pause the stopwatch for genuine interruptions (a phone call, a colleague's
  question) — don't count wait time as keying time. If an interruption runs longer than about two
  minutes, discard that invoice's timing entirely and re-time it fresh later; don't try to
  reconstruct "where I was" from memory, since that guess would itself be unmeasured.
- **Session conditions:** key all ten in one sitting if possible, in the order listed in §1, with
  no long gaps between invoices — this keeps warm-up and fatigue effects comparable across the ten
  instead of comparing a fresh morning invoice to a post-lunch one. Record the session date and the
  operator's name/role (see §5).
- **Per-invoice, not aggregate:** log the elapsed time for *each* invoice separately. Do not record
  only a single total-elapsed-for-all-ten number — a mean over ten invoices with no visible spread
  is a weaker, less checkable number than a median plus a min–max range, and the range is exactly
  where the arithmetic-error cost (#3/#4) should show up relative to the clean baseline (#1/#2).

## 4. Recording template

One row per invoice, in addition to the 16 data columns from §2:

| Column | Purpose |
|---|---|
| `invoice_id` | e.g. `INV-0124` |
| `defect_class` | from §1's table, for later grouping |
| `start_timestamp` | wall-clock, to the second |
| `stop_timestamp` | wall-clock, to the second |
| `elapsed_seconds` | `stop − start`; the number that matters |
| `interrupted` | yes/no; if yes and discarded per §3, mark the row `DISCARDED — re-timed as row N` |
| `notes` | anything unusual — hesitation on a field, a typo caught late, etc. |

After the ten rows are filled, compute and record, as their own summary lines (not folded silently
into a single "average"):

- **N** (should be 10, or fewer if any were discarded per the interruption rule — record the actual N)
- **Mean** elapsed seconds
- **Median** elapsed seconds
- **Min** and **Max** elapsed seconds (the range)
- Operator name/role, session date, and which of §2's clean vs. defective rows are unusually far
  from the median (with the `notes` explaining why, if visible)

This document intentionally does **not** contain any of those numbers — filling them in without
having run the stopwatch would be exactly the fabrication this protocol exists to prevent.

## 5. Extrapolation, and its limits

Let `T_median` be the measured median seconds/invoice from §4 (a placeholder here — to be filled
in when the protocol is actually run).

**To the corpus.** `T_median × 285 / 3600` hours to manually key every XML invoice in the corpus
by hand (the 15 `pdf_vision` invoices are excluded from this figure — they involve reading a
rendered image rather than XML text, which is a different task with its own, unmeasured, timing;
do not fold them into the same average without a separate measurement).

**To a monthly volume.** The corpus's own issue dates span `2026-07-01` to `2026-07-25` — it was
constructed to represent roughly one month of company-wide invoice volume across several
departments (MKT, IT, LOGISTICS, TOOLS, OFFICE_SUPPLIES, UTILITIES). That makes 300 invoices/month
(285 XML + 15 vision) a defensible illustrative monthly figure *because it's what this corpus
already represents*, not an invented number — but a real deployment should substitute its own
observed monthly invoice count (check the AP ledger) rather than reuse the corpus size as if it
were a market claim. With that substitution: `T_median × monthly_volume / 3600` hours/month of
manual keying that the extraction stage removes.

**Bias, stated plainly.** The person running this protocol is, in practice, the system's own
author — someone who already knows the sixteen fields in §2, already knows where each one sits in
the XML, and isn't reading the schema for the first time. That makes the measured `T_median`
*faster* than a real bookkeeper unfamiliar with this invoice layout would be. The bias therefore
runs in one direction: the resulting time-saved estimate is a **conservative** (lower-bound)
number, understating the true saving rather than overstating it. This is the opposite failure mode
from a vendor's marketing number, which typically overstates savings — and is exactly why a
self-measured, author-run number, even a biased one, is more trustworthy than a quoted one: the
bias is disclosed and runs the safe direction, instead of being invisible and running the
self-serving one.

## 6. What number the report carries, and how it connects to STP ≥ 85%

The Business/STP row target in design §10.4 is **STP rate ≥ 85%** — the fraction of invoices that
reach a decision with **no** human touch (no `needs_approval` fallback, nothing routed to a
reviewer). STP rate itself is a deterministic pipeline measurement (computed from actual
department-resolution and flag outcomes over the corpus by `scripts/eval.py`), and is **not** what
this protocol measures — it needs no stopwatch.

What this protocol supplies is the other half of the business claim: **the value of one invoice
that reaches straight-through processing.** An invoice that clears STP is one a human never keys
by hand at all; an invoice that gets flagged still needs human review regardless of what the
extraction stage did, so this protocol conservatively credits time savings only to the STP
fraction, not to every invoice. Once both numbers exist, the report combines them as:

```
hours saved / month  =  STP_rate  ×  monthly_volume  ×  (T_median / 3600)
```

`STP_rate` comes from the deterministic eval measurement; `monthly_volume` and `T_median` come from
this protocol (§5). Until `T_median` exists, this formula cannot be evaluated — which is exactly
why the Business/STP row in `docs/eval-report.md` reads:

> **PENDING — awaiting operator measurement**

and not a number, a blank, or a citation to somebody else's AP-automation benchmark.
