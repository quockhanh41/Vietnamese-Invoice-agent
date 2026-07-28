"""CLI: generate a deterministic synthetic invoice corpus.

    python -m scripts.generate_invoices --count 300 --seed 42 --out corpus/

ALLOCATION. Defect counts come from design doc §10.2 scaled to `count`. They
sum to MORE than the number of defective invoices on purpose: `defects` is a
LIST, and real invoices arrive as "new vendor AND over budget". A one-label
manifest makes multi-flag evaluation impossible (item 2.2).

DETERMINISM. One random.Random(seed) is threaded through every choice. No
module-level random, no datetime.now(), no uuid4(). Same seed -> byte-identical
corpus, or M6's before/after numbers mean nothing.
"""

import argparse
import dataclasses
from datetime import date
from decimal import Decimal
import pathlib
from random import Random

from scripts.generator import defects, manifest, templates, vendors
from scripts.generator.manifest import ManifestRecord
from scripts.generator.templates import InvoiceSpec, LineSpec
from scripts.seed_reference import BUDGETS  # the one allowed dependency (M2-A §7)

DEFECT_RATIOS: dict[str, float] = {
    "arithmetic_error": 0.06,
    "words_digits_mismatch": 0.02,
    "bad_tax_code": 0.04,
    "wrong_vat_rate": 0.03,
    "exact_duplicate": 0.03,
    "near_duplicate": 0.03,
    "new_vendor": 0.03,
    "budget_overrun": 0.04,
    "price_outlier": 0.02,
    "prompt_injection": 0.02,
    "pdf_only": 0.04,
    "unreadable_pdf": 0.01,
    "ambiguous_category": 0.03,
}

DEPARTMENTS = ("MKT", "IT", "HR", "LOG", "ADM", "SAL")
CATEGORIES = ("MARKETING", "IT_SERVICES", "OFFICE_SUPPLIES", "LOGISTICS", "UTILITIES", "TOOLS")


def _make_base_spec(index: int, vendor: vendors.Vendor, rng: Random) -> InvoiceSpec:
    inv_num = f"{index + 1:08d}"
    series = "C26TAA" if index % 2 == 0 else "C26TBB"
    template_no = "1" if index % 4 != 0 else "2"
    root = "tdiep" if index % 8 == 0 else "hdon"
    issue_date = date(2026, 7, (index % 25) + 1)

    unit_price = Decimal(rng.randrange(100_000, 2_000_000, 50_000))
    qty = Decimal(rng.randint(1, 10))
    amount = qty * unit_price
    vat_rate = Decimal("0.1") if template_no == "1" else None
    vat_amount = (amount * vat_rate).quantize(Decimal("1")) if vat_rate is not None else None

    line = LineSpec(
        line_no=1,
        item_kind=1,
        description=f"Dịch vụ tư vấn và hỗ trợ kỹ thuật {index + 1}",
        unit="Gói",
        quantity=qty,
        unit_price=unit_price,
        amount=amount,
        vat_rate=vat_rate,
        vat_amount=vat_amount,
        ext_amount=None,
        self_closing=False,
    )

    subtotal = amount if vat_rate is not None else None
    vat_total = vat_amount if vat_rate is not None else None
    total = amount + (vat_amount or Decimal("0"))

    return InvoiceSpec(
        root=root,
        schema_version="2.1.0" if root == "hdon" else "2.0.0",
        template_no=template_no,
        invoice_series=series,
        invoice_number=inv_num,
        issue_date=issue_date,
        seller_name=vendor.name,
        seller_tax_code=vendor.tax_code,
        seller_address=vendor.address,
        buyer_name="CÔNG TY CP TẬP ĐOÀN VIỆT NAM",
        buyer_tax_code="REDACTED-MST",
        buyer_address="Số 100 Phố Huế, Hà Nội",
        ext_order=vendor.ext_order,
        thousand="nghìn" if index % 5 != 0 else "ngàn",
        lines=[line],
        subtotal=subtotal,
        vat_total=vat_total,
        total_amount=total,
        total_in_words=None,
    )


def generate(*, count: int, seed: int, out: pathlib.Path) -> None:
    rng = Random(seed)
    out.mkdir(parents=True, exist_ok=True)

    vendor_pool = vendors.build_vendors(rng, 40)
    # Reserve first 30 vendors for history; last 10 for new_vendor defect
    established_vendors = vendor_pool[:30]
    new_vendors = vendor_pool[30:]

    # Calculate defect target counts
    counts = {d: round(count * ratio) for d, ratio in DEFECT_RATIOS.items()}

    records: list[ManifestRecord] = []
    specs: list[tuple[InvoiceSpec, list[str], dict]] = []

    # 1. First, build the budget overrun case (3 items)
    base_template = _make_base_spec(0, established_vendors[0], rng)
    budget_case_specs = defects.build_budget_overrun_case(base_template, rng)
    for rank, bspec in enumerate(budget_case_specs, start=1):
        specs.append((bspec, ["budget_overrun"], {"rank": rank}))

    # 2. Build prompt injection carriers and their negative-control twins (6 pairs)
    for i, (name, payload) in enumerate(defects.INJECTION_PAYLOADS):
        v = established_vendors[i % len(established_vendors)]
        carrier_base = _make_base_spec(len(specs), v, rng)
        carrier_spec = defects.seed_injection(carrier_base, payload)
        specs.append((carrier_spec, ["prompt_injection"], {}))

        # Twin control: clean invoice differing only in description payload
        twin_spec = defects.strip_injection(carrier_spec)
        specs.append((twin_spec, [], {"twin_of": carrier_spec.doc_id}))

    # 3. Fill up to `count` base specs
    curr_idx = len(specs)
    while len(specs) < count:
        v = established_vendors[curr_idx % len(established_vendors)]
        base = _make_base_spec(curr_idx, v, rng)
        specs.append((base, [], {}))
        curr_idx += 1

    # Now apply remaining defect quotas to clean specs (indices >= 15)
    # We assign slices of indices to each defect category
    available_indices = [i for i in range(15, count) if not specs[i][1]]

    def _allocate_indices(n: int) -> list[int]:
        nonlocal available_indices
        chosen = available_indices[:n]
        available_indices = available_indices[n:]
        return chosen

    # arithmetic_error (18)
    for idx in _allocate_indices(counts["arithmetic_error"]):
        s, d, meta = specs[idx]
        specs[idx] = (defects.seed_arithmetic_error(s), d + ["arithmetic_error"], meta)

    # words_digits_mismatch (6)
    for idx in _allocate_indices(counts["words_digits_mismatch"]):
        s, d, meta = specs[idx]
        specs[idx] = (defects.seed_words_mismatch(s), d + ["words_digits_mismatch"], meta)

    # bad_tax_code (12)
    for idx in _allocate_indices(counts["bad_tax_code"]):
        s, d, meta = specs[idx]
        specs[idx] = (defects.seed_bad_tax_code(s), d + ["bad_tax_code"], meta)

    # wrong_vat_rate (9)
    for idx in _allocate_indices(counts["wrong_vat_rate"]):
        s, d, meta = specs[idx]
        specs[idx] = (defects.seed_wrong_vat_rate(s), d + ["wrong_vat_rate"], meta)

    # remaining budget_overrun (12 - 3 = 9)
    for idx in _allocate_indices(counts["budget_overrun"] - 3):
        s, d, meta = specs[idx]
        # Make line amount 60_000_000 to breach the 50_000_000 MKT limit
        overrun_line = LineSpec(
            line_no=1, item_kind=1, description="Chi phí sự kiện truyền thông MKT",
            unit="Gói", quantity=Decimal("1"), unit_price=Decimal("60000000"),
            amount=Decimal("60000000"), vat_rate=None, vat_amount=None,
            ext_amount=None, self_closing=False,
        )
        bspec = dataclasses.replace(
            s, lines=[overrun_line], subtotal=None, vat_total=None,
            total_amount=Decimal("60000000"), total_in_words=None, template_no="2",
        )
        specs[idx] = (bspec, d + ["budget_overrun"], meta)

    # exact_duplicate (9)
    for idx in _allocate_indices(counts["exact_duplicate"]):
        # Duplicate an earlier invoice
        target_idx = idx - 10
        orig_spec = specs[target_idx][0]
        dup_spec = defects.duplicate_of(orig_spec)
        specs[idx] = (dup_spec, specs[idx][1] + ["exact_duplicate"], {"duplicate_of": orig_spec.doc_id})

    # near_duplicate (9)
    for idx in _allocate_indices(counts["near_duplicate"]):
        target_idx = idx - 15
        orig_spec = specs[target_idx][0]
        near_spec = defects.near_duplicate_of(orig_spec, rng)
        specs[idx] = (near_spec, specs[idx][1] + ["near_duplicate"], {})

    # price_outlier (6)
    for idx in _allocate_indices(counts["price_outlier"]):
        s, d, meta = specs[idx]
        line = s.lines[0]
        new_amt = line.amount * defects.OUTLIER_MULTIPLIER
        new_line = dataclasses.replace(line, amount=new_amt, unit_price=line.unit_price * defects.OUTLIER_MULTIPLIER)
        sub = s.subtotal * defects.OUTLIER_MULTIPLIER if s.subtotal else None
        vat = s.vat_total * defects.OUTLIER_MULTIPLIER if s.vat_total else None
        outlier_spec = dataclasses.replace(
            s, lines=[new_line], subtotal=sub, vat_total=vat,
            total_amount=s.total_amount * defects.OUTLIER_MULTIPLIER, total_in_words=None,
        )
        specs[idx] = (outlier_spec, d + ["price_outlier"], meta)

    # new_vendor (9 total: 3 standalone + 3 on budget_overrun + 3 on price_outlier)
    new_vendor_indices = (
        _allocate_indices(3)
        + [idx for idx, (s, d, m) in enumerate(specs) if "budget_overrun" in d][:3]
        + [idx for idx, (s, d, m) in enumerate(specs) if "price_outlier" in d][:3]
    )
    for i, idx in enumerate(new_vendor_indices):
        nv = new_vendors[i % len(new_vendors)]
        s, d, meta = specs[idx]
        nv_spec = dataclasses.replace(
            s, seller_tax_code=nv.tax_code, seller_name=nv.name, seller_address=nv.address
        )
        specs[idx] = (nv_spec, d + ["new_vendor"], meta)

    # ambiguous_category (9)
    for i, idx in enumerate(_allocate_indices(counts["ambiguous_category"])):
        s, d, meta = specs[idx]
        desc, _ = defects.AMBIGUOUS_DESCRIPTIONS[i % len(defects.AMBIGUOUS_DESCRIPTIONS)]
        line = dataclasses.replace(s.lines[0], description=desc)
        ambig_spec = dataclasses.replace(s, lines=[line])
        specs[idx] = (ambig_spec, d + ["ambiguous_category"], meta)

    # pdf_only (12)
    pdf_only_indices = _allocate_indices(counts["pdf_only"])
    for idx in pdf_only_indices:
        s, d, meta = specs[idx]
        specs[idx] = (s, d + ["pdf_only"], meta)

    # unreadable_pdf (3)
    unreadable_indices = _allocate_indices(counts["unreadable_pdf"])
    for idx in unreadable_indices:
        s, d, meta = specs[idx]
        specs[idx] = (s, d + ["unreadable_pdf"], meta)

    # Now write XML files and build manifest records
    for i, (spec, defect_list, meta) in enumerate(specs):
        inv_id = f"INV-{i + 1:04d}"
        # Adjust doc_id / invoice_number to be unique if needed
        spec = dataclasses.replace(spec, invoice_number=f"{i + 1:08d}") if "exact_duplicate" not in defect_list and "near_duplicate" not in defect_list and i >= 3 else spec

        is_pdf = "pdf_only" in defect_list or "unreadable_pdf" in defect_list
        xml_filename = f"{inv_id}.xml"
        xml_path = out / xml_filename

        expected_findings: list[str] = []
        for df in defect_list:
            for code in manifest.DEFECT_CODES.get(df, ()):
                if code not in expected_findings:
                    expected_findings.append(code)

        if is_pdf:
            rel_path = None
            source_type = "pdf_vision"
        else:
            rel_path = f"corpus/{xml_filename}"
            source_type = "xml"
            rendered = templates.render(spec)
            xml_path.write_text(rendered, encoding="utf-8")

        # Department logic
        dept = "MKT" if "budget_overrun" in defect_list or i < 3 else DEPARTMENTS[i % len(DEPARTMENTS)]
        cat = CATEGORIES[i % len(CATEGORIES)]

        rec = ManifestRecord(
            invoice_id=inv_id,
            path=rel_path,
            source_type=source_type,
            seller_tax_code=spec.seller_tax_code,
            template_no=spec.template_no,
            invoice_series=spec.invoice_series,
            invoice_number=spec.invoice_number,
            issue_date=spec.issue_date.isoformat(),
            department=dept,
            category=cat,
            total_amount=spec.total_amount,
            defects=defect_list,
            expected_finding_codes=expected_findings,
            twin_of=meta.get("twin_of"),
            duplicate_of=meta.get("duplicate_of"),
            canonical_budget_rank=meta.get("rank"),
        )
        records.append(rec)

    manifest.write_manifest(out / "manifest.jsonl", records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("corpus"))
    args = parser.parse_args()
    generate(count=args.count, seed=args.seed, out=args.out)
    print(f"✅ generated {args.count} invoices into {args.out}")


if __name__ == "__main__":
    main()
