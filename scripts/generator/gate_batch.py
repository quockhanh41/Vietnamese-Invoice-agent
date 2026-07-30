"""The 30-invoice batch M4's gate is measured against.

WHY DECLARED RATHER THAN SAMPLED. BUDGET_BREACH is a property of the batch, not
of the invoice: measured 2026-07-30, the corpus holds 59 MKT invoices totalling
993,010,000 against MKT's seeded limit of 50,000,000, so any batch containing MKT
spend beyond the three-invoice case breaches far more than the manifest declares.
A per-invoice expectation is only meaningful relative to a named batch.

The roadmap's original phrase — "batch 30 invoices -> ~26 auto, ~4 flagged" —
matches neither this nor the corpus's 28% finding rate (84/300 carry at least one
expected finding, so a random 30 flags ~8). The gate criterion is therefore EXACT
AGREEMENT between what the batch flags and what this file declares; the auto/flag
split is reported as whatever it is.

`corpus/` is gitignored, so this module is the committed source of truth and
`corpus/gate_batch.json` is a generated artifact — the same relationship
`manifest.jsonl` already has.

COMPOSITION, chosen for coverage and asserted by tests/test_gate_batch.py:
  * INV-0001/0002/0003 - the budget case, whose canonical order is the reverse
    of their file order, and the batch's ONLY MKT members;
  * one exact duplicate and its original;
  * one near-duplicate;
  * one NEW_VENDOR, one PRICE_OUTLIER;
  * the remainder clean and non-MKT.
"""

import json
import pathlib
from decimal import Decimal
from typing import Any

# ⚠️ FROZEN LIST — ids discovered from the corpus regenerated at
# count=300, seed=42 by plan 2026-07-31-m4a Task 7 step 1. If regeneration moves
# a defect off one of these, tests/test_gate_batch.py goes red rather than the
# batch quietly losing the case it was chosen to exercise.
GATE_BATCH_IDS: tuple[str, ...] = (
    "INV-0001",  # MKT / MARKETING — budget_overrun, new_vendor
    "INV-0002",  # MKT / MARKETING — budget_overrun, new_vendor
    "INV-0003",  # MKT / MARKETING — budget_overrun, new_vendor
    "INV-0060",  # OPS / TOOLS — wrong_vat_rate
    "INV-0070",  # OPS / TOOLS — exact_duplicate
    "INV-0080",  # SALES / MARKETING — near_duplicate
    "INV-0088",  # OPS / LOGISTICS — price_outlier, new_vendor
    "INV-0089",  # ADMIN / UTILITIES — price_outlier, new_vendor
    "INV-0005",  # OPS / LOGISTICS — clean
    "INV-0007",  # OPS / TOOLS — clean
    "INV-0009",  # IT / IT_SERVICES — clean
    "INV-0011",  # SALES / LOGISTICS — clean
    "INV-0013",  # OPS / TOOLS — clean
    "INV-0015",  # IT / IT_SERVICES — clean
    "INV-0122",  # IT / IT_SERVICES — clean
    "INV-0123",  # ADMIN / OFFICE_SUPPLIES — clean
    "INV-0124",  # OPS / LOGISTICS — clean
    "INV-0125",  # ADMIN / UTILITIES — clean
    "INV-0126",  # OPS / TOOLS — clean
    "INV-0128",  # ADMIN / IT_SERVICES — clean
    "INV-0129",  # ADMIN / OFFICE_SUPPLIES — clean
    "INV-0130",  # OPS / LOGISTICS — clean
    "INV-0131",  # ADMIN / UTILITIES — clean
    "INV-0132",  # OPS / TOOLS — clean
    "INV-0134",  # ADMIN / IT_SERVICES — clean
    "INV-0135",  # SALES / OFFICE_SUPPLIES — clean
    "INV-0136",  # OPS / LOGISTICS — clean
    "INV-0137",  # ADMIN / UTILITIES — clean
    "INV-0138",  # OPS / TOOLS — clean
    "INV-0140",  # ADMIN / IT_SERVICES — clean
)

# Third in canonical order (issue_date 2026-07-20), first in file order — the
# distinction canonical_budget_rank exists to record. See spec §1.1 D2.
EXPECTED_BREACH_ID = "INV-0001"

MKT_LIMIT = Decimal("50000000")


def write_gate_batch(path: pathlib.Path, records: list[dict[str, Any]]) -> None:
    """Emit the resolved batch beside the manifest.

    The balance chain is computed from the manifest's own amounts in canonical
    order, so the JSON cannot drift from the corpus it describes.
    """
    chosen = {r["invoice_id"]: r for r in records if r["invoice_id"] in set(GATE_BATCH_IDS)}
    mkt = sorted(
        (r for r in chosen.values() if r["department"] == "MKT"),
        key=lambda r: (r["issue_date"], r["invoice_series"], r["invoice_number"]),
    )
    chain: list[dict[str, str]] = []
    balance = MKT_LIMIT
    for record in mkt:
        before = balance
        balance = before - Decimal(record["total_amount"])
        chain.append(
            {
                "invoice_id": record["invoice_id"],
                "balance_before": str(before),
                "balance_after": str(balance),
            }
        )

    payload = {
        "invoice_ids": list(GATE_BATCH_IDS),
        "expected_breach_id": EXPECTED_BREACH_ID,
        "mkt_limit": str(MKT_LIMIT),
        "balance_after_chain": chain,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
