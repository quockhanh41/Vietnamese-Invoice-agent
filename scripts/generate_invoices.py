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
import pathlib
from datetime import date
from decimal import Decimal
from random import Random

from scripts.generator import (
    defects, descriptions, gate_batch, manifest, pdf, templates, vendors,
)
from scripts.generator.manifest import ManifestRecord
from scripts.generator.templates import InvoiceSpec, LineSpec
from scripts.seed_reference import BUDGETS, DEPARTMENTS as _SEEDED_DEPARTMENTS

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

# Derived from the seed, not written twice. Measured 2026-07-30: the two lists
# had drifted to ("MKT","IT","HR","LOG","ADM","SAL") here against
# ("MKT","ADMIN","OPS","IT","SALES") in the seed, so 194 of 300 invoices could
# not satisfy the invoices.department foreign key and four departments had no
# budget to reconcile against. Deriving makes that class of drift
# unrepresentable rather than merely fixed. seed_reference is the ONE allowed
# dependency (M2-A §7) and already supplies BUDGETS.
DEPARTMENTS = tuple(code for code, _name in _SEEDED_DEPARTMENTS)
CATEGORIES = ("MARKETING", "IT_SERVICES", "OFFICE_SUPPLIES", "LOGISTICS", "UTILITIES", "TOOLS")

CATEGORY_TO_DEPARTMENT: dict[str, str] = {
    "MARKETING": "MKT",
    "IT_SERVICES": "IT",
    "OFFICE_SUPPLIES": "ADMIN",
    "UTILITIES": "ADMIN",
    "LOGISTICS": "OPS",
    "TOOLS": "OPS",
}

SECONDARY_DEPARTMENT: dict[str, str] = {
    "MARKETING": "SALES",
    "IT_SERVICES": "ADMIN",
    "OFFICE_SUPPLIES": "SALES",
    "UTILITIES": "OPS",
    "LOGISTICS": "SALES",
    "TOOLS": "IT",
}

DEPARTMENT_NOISE_RATE = 0.15


def _pick_department(category: str, rng: Random) -> str:
    if rng.random() < DEPARTMENT_NOISE_RATE:
        return SECONDARY_DEPARTMENT[category]
    return CATEGORY_TO_DEPARTMENT[category]


def _pick_category(index: int) -> str:
    return CATEGORIES[index % len(CATEGORIES)]


def _inherited_body_defects(
    source_defects: list[str], source_meta: dict, target_defects: list[str]
) -> list[str]:
    """Body defects a duplicate copies along with the invoice it duplicates.

    `duplicate_of` / `near_duplicate_of` copy the source SPEC, so every defect
    that lives in the body travels with it — but the manifest row was being
    built from the target slot's own defect list, so the inherited ones went
    undeclared. Found 2026-07-29 by the M3 exit audit; see
    `manifest.BODY_DEFECTS`.

    Relational defects (the duplicate markers themselves, budget overrun, new
    vendor, PDF-only) are deliberately NOT inherited: they describe how the
    invoice relates to the rest of the corpus, not what is inside it.

    TRANSITIVE, because the chains are real: measured 2026-07-29, `INV-0085` is
    a near-duplicate of an invoice that is itself an exact duplicate of a
    `wrong_vat_rate` invoice. Reading only the source's own `defects` list stops
    at the middle link, because that link records its inheritance in `meta`
    rather than in `defects` — so the source's inherited set must be read too.
    """
    source_all = [*source_defects, *source_meta.get("inherited_defects", [])]
    return [
        d for d in manifest.BODY_DEFECTS if d in source_all and d not in target_defects
    ]


def _make_base_spec(
    index: int, vendor: vendors.Vendor, rng: Random, category: str
) -> InvoiceSpec:
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
        description=descriptions.draw(category, rng),
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
    base_template = _make_base_spec(0, established_vendors[0], rng, "MARKETING")
    budget_case_specs = defects.build_budget_overrun_case(base_template, rng)

    # The seeded limit is read from scripts.seed_reference — the ONE allowed
    # dependency (M2-A §7) — and checked here rather than assumed. If someone
    # raises the MKT budget, these three invoices quietly stop overrunning and
    # the 4% budget_overrun group becomes 12 invoices that breach nothing. That
    # failure is silent in the corpus and only shows up as a bad recall number
    # in M6, so `make generate` refuses to produce it.
    seeded_limit = {(dept, period): amount for dept, period, _cat, amount in BUDGETS}[
        (defects.BUDGET_CASE_DEPARTMENT, defects.BUDGET_CASE_PERIOD)
    ]
    case_total = sum((s.total_amount for s in budget_case_specs), Decimal("0"))
    if case_total <= seeded_limit:
        raise SystemExit(
            f"budget-overrun case totals {case_total} but the seeded "
            f"{defects.BUDGET_CASE_DEPARTMENT} limit is {seeded_limit} — the case would "
            f"never breach. Re-tune defects._BUDGET_CASE_AMOUNTS against seed_reference.BUDGETS."
        )

    # Rank by CANONICAL order (issue_date, series, number), not by construction
    # order. defects.py:163 deliberately makes invoice-number order the reverse
    # of canonical order so that shuffling arrival order can distinguish a
    # sorted implementation from an unsorted one; ranking by construction order
    # therefore stored precisely the reverse of what the field name promises.
    canonical_case = sorted(
        budget_case_specs,
        key=lambda s: (s.issue_date, s.invoice_series, s.invoice_number),
    )
    ranks = {id(spec): rank for rank, spec in enumerate(canonical_case, start=1)}
    for bspec in budget_case_specs:
        specs.append(
            (bspec, ["budget_overrun"], {"rank": ranks[id(bspec)], "category": "MARKETING"})
        )

    # 2. Build prompt injection carriers and their negative-control twins (6 pairs)
    for i, (_name, payload) in enumerate(defects.INJECTION_PAYLOADS):
        v = established_vendors[i % len(established_vendors)]
        category = _pick_category(len(specs))
        carrier_base = _make_base_spec(len(specs), v, rng, category)
        carrier_inv_id = f"INV-{len(specs) + 1:04d}"
        carrier_spec = defects.seed_injection(carrier_base, payload)
        specs.append((carrier_spec, ["prompt_injection"], {"category": category}))

        # Twin control: clean invoice differing only in description payload
        twin_spec = defects.strip_injection(
            carrier_spec, clean_description=carrier_base.lines[0].description
        )
        specs.append(
            (twin_spec, [], {"twin_of": carrier_inv_id, "category": category})
        )

    # 3. Fill up to `count` base specs
    curr_idx = len(specs)
    while len(specs) < count:
        v = established_vendors[curr_idx % len(established_vendors)]
        category = _pick_category(curr_idx)
        base = _make_base_spec(curr_idx, v, rng, category)
        specs.append((base, [], {"category": category}))
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

    # remaining budget_overrun (12 - 3 = 9 at count=300)
    for idx in _allocate_indices(max(0, counts["budget_overrun"] - 3)):
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
        specs[idx] = (bspec, d + ["budget_overrun"], {**meta, "category": "MARKETING"})

    # exact_duplicate (9)
    for idx in _allocate_indices(counts["exact_duplicate"]):
        # Duplicate an earlier invoice
        target_idx = idx - 10
        orig_spec = specs[target_idx][0]
        dup_spec = defects.duplicate_of(orig_spec)
        specs[idx] = (
            dup_spec,
            specs[idx][1] + ["exact_duplicate"],
            {
                "duplicate_of": orig_spec.doc_id,
                "category": specs[target_idx][2]["category"],
                "inherited_defects": _inherited_body_defects(
                    specs[target_idx][1], specs[target_idx][2], specs[idx][1]
                ),
            },
        )

    # near_duplicate (9)
    for idx in _allocate_indices(counts["near_duplicate"]):
        target_idx = idx - 15
        orig_spec = specs[target_idx][0]
        near_spec = defects.near_duplicate_of(orig_spec, rng)
        specs[idx] = (
            near_spec,
            specs[idx][1] + ["near_duplicate"],
            {
                "category": specs[target_idx][2]["category"],
                "inherited_defects": _inherited_body_defects(
                    specs[target_idx][1], specs[target_idx][2], specs[idx][1]
                ),
            },
        )

    # price_outlier (6)
    for idx in _allocate_indices(counts["price_outlier"]):
        s, d, meta = specs[idx]
        line = s.lines[0]
        new_amt = line.amount * defects.OUTLIER_MULTIPLIER
        new_line = dataclasses.replace(
            line,
            amount=new_amt,
            unit_price=line.unit_price * defects.OUTLIER_MULTIPLIER,
            # Scale the LINE's VAT too. The header below is scaled in full
            # (subtotal / vat_total / total_amount), so leaving TThue at its
            # pre-outlier value keeps the header consistent while making the
            # LINE arithmetic wrong — a *price* outlier silently becomes a VAT
            # error as well. Measured 2026-07-29 (M3 exit audit): 4 invoices,
            # deviations up to 5,040,000 đồng, e.g. 140,000 stated against
            # 14,000,000 at 10%. `defects` is a LIST so each one stays
            # attributable; a defect that drags an undeclared second defect
            # along makes M6's eval unattributable.
            vat_amount=(
                line.vat_amount * defects.OUTLIER_MULTIPLIER
                if line.vat_amount is not None
                else None
            ),
        )
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
        desc, (primary, alternative) = defects.AMBIGUOUS_DESCRIPTIONS[
            i % len(defects.AMBIGUOUS_DESCRIPTIONS)
        ]
        line = dataclasses.replace(s.lines[0], description=desc)
        ambig_spec = dataclasses.replace(s, lines=[line])
        specs[idx] = (
            ambig_spec,
            d + ["ambiguous_category"],
            {**meta, "category": primary, "alternative_category": alternative},
        )

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

    # Three XML invoices also get a PDF, for M3-C's escalation path. Drawn from
    # the same rng as every other choice so `--seed 42` stays reproducible;
    # hardcoding invoice ids would drift the moment --count changes.
    xml_indices = [
        i for i, (_, defect_list, _) in enumerate(specs)
        if "pdf_only" not in defect_list and "unreadable_pdf" not in defect_list
    ]
    twin_indices = set(rng.sample(xml_indices, min(3, len(xml_indices))))

    # Now write XML files and build manifest records
    for i, (spec, defect_list, meta) in enumerate(specs):
        inv_id = f"INV-{i + 1:04d}"
        # Adjust doc_id / invoice_number to be unique if needed
        spec = dataclasses.replace(spec, invoice_number=f"{i + 1:08d}") if "exact_duplicate" not in defect_list and "near_duplicate" not in defect_list and i >= 3 else spec

        is_pdf = "pdf_only" in defect_list or "unreadable_pdf" in defect_list
        xml_filename = f"{inv_id}.xml"
        xml_path = out / xml_filename

        # `defects` records what was deliberately SEEDED — that list carries the
        # §10.2 ratios and must not grow. `expected_finding_codes` records what a
        # detector should FIND, which also includes defects inherited from a
        # duplicated invoice's body. Two different questions; conflating them
        # inflates the ratio counts (measured 2026-07-29: wrong_vat_rate went
        # 9 -> 11) while leaving the real problem unfixed.
        expected_findings: list[str] = []
        for df in [*defect_list, *meta.get("inherited_defects", [])]:
            for code in manifest.DEFECT_CODES.get(df, ()):
                if code not in expected_findings:
                    expected_findings.append(code)

        pdf_rel_path: str | None = None
        if is_pdf:
            pdf_filename = f"{inv_id}.pdf"
            if "unreadable_pdf" in defect_list:
                # Three different failures on purpose (spec §5.1): truncated
                # never reaches a model, blank and degraded do and fail on
                # content. `i % 3` distributes them deterministically.
                variant = i % 3
                if variant == 0:
                    blob = pdf.render_truncated(spec)
                elif variant == 1:
                    blob = pdf.render_blank()
                else:
                    blob = pdf.render_degraded(spec)
            else:
                blob = pdf.render_pdf(spec)
            (out / pdf_filename).write_bytes(blob)
            rel_path = f"corpus/{pdf_filename}"
            source_type = "pdf_vision"
        else:
            rel_path = f"corpus/{xml_filename}"
            source_type = "xml"
            rendered = templates.render(spec)
            xml_path.write_text(rendered, encoding="utf-8")
            if i in twin_indices:
                twin_filename = f"{inv_id}.pdf"
                (out / twin_filename).write_bytes(pdf.render_pdf(spec))
                pdf_rel_path = f"corpus/{twin_filename}"

        # Department logic. `category` was decided when the spec was built and
        # the description was drawn FROM it — reading it back here is what makes
        # the label a function of the invoice (spec 2026-07-31-m4a §1.1 D3).
        cat = meta["category"]
        dept = "MKT" if "budget_overrun" in defect_list else _pick_department(cat, rng)

        rec = ManifestRecord(
            invoice_id=inv_id,
            path=rel_path,
            pdf_path=pdf_rel_path,
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
            alternative_category=meta.get("alternative_category"),
        )
        records.append(rec)

    manifest.write_manifest(out / "manifest.jsonl", records)
    # Read back rather than passing `records` directly: the manifest encodes
    # Decimal as str, and the gate batch must be computed from exactly the bytes
    # on disk so the two artifacts cannot disagree about an amount.
    gate_batch.write_gate_batch(
        out / "gate_batch.json", manifest.read_manifest(out / "manifest.jsonl")
    )


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
