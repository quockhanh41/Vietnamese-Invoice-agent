"""Evaluation script for M8-A ReportingAgent.

Measures pre-guard numeric fidelity and coverage for both LLM commentary and the LLM-free
template baseline across N >= 20 payloads.
"""

import argparse
import datetime
import json
import random
from typing import Any

from invoice_agent.agents import reporting
from invoice_agent.config import get_settings
from invoice_agent.llm.gateway import LLMGateway


def _generate_synthetic_payload(index: int, rng: random.Random) -> dict[str, Any]:
    year = 2026
    month = (index % 12) + 1
    period = f"{year}-{month:02d}"

    departments = ["MKT", "OPS", "ADMIN", "IT", "SALES"]
    budgets = []
    for dept in departments:
        limit = rng.randint(10, 50) * 1000000
        # Perturbations: over budget, warning, or normal
        r = rng.random()
        if r < 0.35:
            # Over budget
            consumed = limit + rng.randint(1, 10) * 1000000
        elif r < 0.65:
            # Warning (80% - 100%)
            consumed = int(limit * rng.uniform(0.81, 0.99))
        else:
            # Normal
            consumed = int(limit * rng.uniform(0.1, 0.79))
        budgets.append({
            "department_code": dept,
            "period": period,
            "category": None,
            "amount_limit": str(limit),
            "warn_threshold_pct": "0.8",
            "consumed": str(consumed),
            "remaining": str(limit - consumed),
        })

    flag_codes = ["BUDGET_BREACH", "PRICE_OUTLIER", "DUPLICATE_INVOICE", "TAX_CODE_MISMATCH"]
    top_code = rng.choice(flag_codes)
    total_flags = rng.randint(0, 15)
    flags = [{"code": top_code, "severity": "warn", "total": total_flags, "open_count": rng.randint(0, total_flags)}] if total_flags > 0 else []

    auto_cnt = rng.randint(10, 50)
    human_cnt = rng.randint(0, 20)
    posting = [
        {"posting_authority": "auto", "entries": auto_cnt},
        {"posting_authority": "human", "entries": human_cnt},
    ]

    cost = {"calls": rng.randint(10, 100), "succeeded": rng.randint(10, 100), "cost_usd": "0", "shadow_cost_usd": f"{rng.uniform(0.01, 0.5):.2f}"}

    spend = [
        {"group_key": dept, "invoice_count": rng.randint(1, 10), "total_amount": str(rng.randint(5, 40) * 1000000)}
        for dept in departments[:3]
    ]

    return {
        "period": period,
        "budgets": budgets,
        "flags": flags,
        "posting": posting,
        "cost": cost,
        "spend": spend,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate M8-A ReportingAgent LLM vs template.")
    parser.add_argument("--n", type=int, default=24, help="Number of payloads to evaluate.")
    parser.add_argument("--out", type=str, default="docs/notes/2026-08-06-m8a-reporting-raw.json", help="Path to write raw JSON artefact.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    gateway = LLMGateway()

    template_fidelity_hits = 0
    template_coverage_hits = 0
    llm_fidelity_hits = 0
    llm_coverage_hits = 0

    results = []

    for i in range(args.n):
        payload_raw = _generate_synthetic_payload(i, rng)
        facts = reporting.build_facts(**payload_raw)

        # Template evaluation
        template_commentary = reporting.render_template(facts)
        t_unsupported = reporting.unsupported_numbers(template_commentary, facts)
        t_faithful = len(t_unsupported) == 0
        if t_faithful:
            template_fidelity_hits += 1

        t_text = " ".join([template_commentary.summary, *template_commentary.observations])
        t_covered = all(row["department"] in t_text for row in facts.over_budget)
        if t_covered or not facts.over_budget:
            template_coverage_hits += 1

        # LLM evaluation (pre-guard)
        narration = reporting.narrate(facts, gateway=gateway)
        l_faithful = len(narration.rejected) == 0 and narration.source == "llm"
        if l_faithful:
            llm_fidelity_hits += 1

        l_text = " ".join([narration.commentary.summary, *narration.commentary.observations])
        l_covered = all(row["department"] in l_text for row in facts.over_budget)
        if l_covered or not facts.over_budget:
            llm_coverage_hits += 1

        results.append({
            "index": i,
            "period": facts.period,
            "over_budget_count": len(facts.over_budget),
            "template": {"faithful": t_faithful, "covered": t_covered, "unsupported": [str(x) for x in t_unsupported]},
            "llm": {"source": narration.source, "faithful": l_faithful, "covered": l_covered, "rejected": [str(x) for x in narration.rejected]},
        })

    n = args.n
    eval_data = {
        "n": n,
        "seed": args.seed,
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "model": get_settings().llm_model_chain[0],
        "llm_mode": get_settings().llm_mode,
        "llm": {
            "fidelity": llm_fidelity_hits / n,
            "coverage": llm_coverage_hits / n,
        },
        "template": {
            "fidelity": template_fidelity_hits / n,
            "coverage": template_coverage_hits / n,
        },
        "details": results,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(eval_data, f, indent=2, ensure_ascii=False)

    print("=== M8-A REPORTING AGENT EVALUATION ===")
    print(f"N: {n} | Model: {eval_data['model']} | Mode: {eval_data['llm_mode']}")
    print(f"Template - Fidelity: {eval_data['template']['fidelity']:.4f} | Coverage: {eval_data['template']['coverage']:.4f}")
    print(f"LLM      - Fidelity: {eval_data['llm']['fidelity']:.4f} | Coverage: {eval_data['llm']['coverage']:.4f}")


if __name__ == "__main__":
    main()
