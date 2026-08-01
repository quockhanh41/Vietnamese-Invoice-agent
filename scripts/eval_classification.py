"""Evaluation harness for M4-C ClassificationAgent and AuditorAgent.

Measures ClassificationAgent + AuditorAgent against the `keywords_vi` baseline over
all XML invoices in the corpus.

Usage:
  LLM_MODE=record .venv/bin/python -m scripts.eval_classification --out docs/notes/m4c-raw.json
  LLM_MODE=replay .venv/bin/python -m scripts.eval_classification
"""

import argparse
import json
import pathlib
import time
from typing import Any

from scripts.generator import manifest
from scripts.seed_reference import CATEGORIES

from invoice_agent.agents import audit as audit_mod
from invoice_agent.agents import classification
from invoice_agent.config import get_settings
from invoice_agent.eval import baseline
from invoice_agent.extraction.xml_parser import parse_invoice_xml
from invoice_agent.llm.cost import LLMCallRecord
from invoice_agent.llm.gateway import LLMGateway

KEYWORDS = {code: list(keywords) for code, _n, _d, _v, _c, keywords in CATEGORIES}
ALL_CATEGORIES = list(KEYWORDS.keys())


def macro_f1(pairs: list[tuple[str, str | None]], labels: list[str]) -> float:
    """Unweighted mean of per-class F1. NOT accuracy: the corpus is skewed
    (MARKETING 70 vs OFFICE_SUPPLIES 42, measured 2026-07-31), and accuracy on a
    skew rewards over-predicting the majority class — the behaviour §9.1's
    `category_laundering` payload is designed to induce."""
    total = 0.0
    for label in labels:
        tp = sum(1 for truth, pred in pairs if truth == label and pred == label)
        fp = sum(1 for truth, pred in pairs if truth != label and pred == label)
        fn = sum(1 for truth, pred in pairs if truth == label and pred != label)
        denom = 2 * tp + fp + fn
        total += (2 * tp / denom) if denom else 0.0
    return total / len(labels)


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval. Reported ALWAYS for the two precisions, because a point
    estimate on a handful of disputes reads as though it meant something —
    §6.4's 'below 50% is net harmful' threshold is noise at n=6."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def precisions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """DISPUTE precision: when the Auditor objected, was the Classifier wrong?
    OVERRIDE precision: was the Auditor's own proposal right?

    Both are computed over the SAME denominator — the disputes — so they are
    directly comparable. They diverge when the Auditor spots real errors and
    proposes bad corrections, which is the case worth knowing about and the one
    a single number hides.
    """
    disputes = [r for r in rows if r["disputed"]]
    n = len(disputes)
    classifier_wrong = sum(1 for r in disputes if r["classifier_category"] != r["truth"])
    auditor_right = sum(1 for r in disputes if r["auditor_category"] == r["truth"])
    return {
        "n": n,
        "dispute_precision": (classifier_wrong / n) if n else None,
        "dispute_ci": wilson(classifier_wrong, n),
        "override_precision": (auditor_right / n) if n else None,
        "override_ci": wilson(auditor_right, n),
        "underpowered": n < 20,
    }


def evaluate(
    corpus_dir: pathlib.Path,
    *,
    gateway: LLMGateway,
    delay_seconds: float = 0.5,
) -> dict[str, Any]:
    manifest_path = corpus_dir / "manifest.jsonl"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found at {manifest_path}")

    records = manifest.read_manifest(manifest_path)
    xml_records = [r for r in records if r["source_type"] == "xml"]

    eval_rows: list[dict[str, Any]] = []
    baseline_pairs: list[tuple[str, str | None]] = []
    agent_category_pairs: list[tuple[str, str | None]] = []
    agent_department_pairs: list[tuple[str, str | None]] = []

    total_calls: list[LLMCallRecord] = []

    for record in xml_records:
        xml_file = corpus_dir.parent / record["path"]
        invoice = parse_invoice_xml(xml_file.read_bytes())
        truth_cat = record["category"]
        truth_dept = record.get("department", "")

        # Baseline prediction
        text = " ".join(ln.description or "" for ln in invoice.lines)
        base_pred = baseline.classify(text, KEYWORDS)
        baseline_pairs.append((truth_cat, base_pred))

        # Agent predictions
        calls: list[LLMCallRecord] = []
        class_res = classification.classify(invoice, gateway=gateway, calls=calls)
        audit_res = audit_mod.audit(invoice, class_res, gateway=gateway, calls=calls)
        dispute_finding = classification.dispute(class_res, audit_res)

        total_calls.extend(calls)

        agent_category_pairs.append((truth_cat, class_res.category))
        agent_department_pairs.append((truth_dept, class_res.department))

        eval_rows.append(
            {
                "id": record["id"],
                "truth": truth_cat,
                "truth_department": truth_dept,
                "baseline_category": base_pred,
                "classifier_category": class_res.category,
                "classifier_department": class_res.department,
                "classifier_confidence": class_res.confidence,
                "classifier_reason": class_res.reasoning,
                "auditor_agrees": audit_res.agrees,
                "auditor_category": audit_res.proposed_category,
                "auditor_department": audit_res.proposed_department,
                "auditor_reason": audit_res.reason,
                "disputed": dispute_finding is not None,
                "ambiguous": bool(record.get("ambiguous_category")),
                "alternative_category": record.get("alternative_category"),
            }
        )

        if delay_seconds > 0 and gateway.mode != "replay":
            time.sleep(delay_seconds)

    total_xml = len(xml_records)
    base_acc = sum(1 for t, p in baseline_pairs if t == p) / total_xml if total_xml else 0.0
    base_f1 = macro_f1(baseline_pairs, ALL_CATEGORIES)

    agent_cat_acc = (
        sum(1 for t, p in agent_category_pairs if t == p) / total_xml if total_xml else 0.0
    )
    agent_cat_f1 = macro_f1(agent_category_pairs, ALL_CATEGORIES)

    dept_correct = sum(1 for t, p in agent_department_pairs if t == p)
    agent_dept_acc = dept_correct / total_xml if total_xml else 0.0

    prec_res = precisions(eval_rows)

    ambiguous_rows = [r for r in eval_rows if r["ambiguous"]]
    ambiguous_matched_alt = sum(
        1 for r in ambiguous_rows if r["classifier_category"] == r.get("alternative_category")
    )

    total_cost = sum(c.cost for c in total_calls)

    results = {
        "total_invoices": total_xml,
        "baseline": {
            "accuracy": base_acc,
            "macro_f1": base_f1,
        },
        "classifier": {
            "accuracy": agent_cat_acc,
            "macro_f1": agent_cat_f1,
            "department_accuracy": agent_dept_acc,
            "department_ceiling": 0.840,
            "gate_target": base_acc + 0.15,
            "gate_passed": agent_cat_acc >= (base_acc + 0.15),
        },
        "precisions": prec_res,
        "ambiguous_summary": {
            "count": len(ambiguous_rows),
            "matched_alternative": ambiguous_matched_alt,
        },
        "cost": {
            "total_calls": len(total_calls),
            "total_cost_usd": total_cost,
        },
        "rows": eval_rows,
    }

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Classification & Audit agents")
    parser.add_argument(
        "--corpus",
        type=pathlib.Path,
        default=pathlib.Path("corpus/manifest.jsonl"),
        help="Path to manifest.jsonl",
    )
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=None,
        help="Optional path to output raw JSON results",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not (settings.groq_api_keys or settings.gemini_api_keys) and settings.llm_mode != "replay":
        print("ERROR: Precondition failed — no GROQ or GEMINI API keys configured.")
        print("M4-C cannot measure itself without a provider key. Stopping Task 6 per plan.")
        return

    corpus_dir = args.corpus.parent if args.corpus.is_file() else args.corpus
    gateway = LLMGateway(settings)

    print(f"Evaluating {corpus_dir} (mode={settings.llm_mode})...")
    res = evaluate(corpus_dir, gateway=gateway)

    print("\n--- M4-C Evaluation Summary ---")
    print(f"Baseline Accuracy: {res['baseline']['accuracy']:.1%} | Macro-F1: {res['baseline']['macro_f1']:.3f}")
    print(f"Classifier Accuracy: {res['classifier']['accuracy']:.1%} (Gate target: {res['classifier']['gate_target']:.1%})")
    print(f"Classifier Macro-F1: {res['classifier']['macro_f1']:.3f}")
    print(f"Department Accuracy: {res['classifier']['department_accuracy']:.1%} (Ceiling: 84.0%)")
    print(f"Disputes Count (n): {res['precisions']['n']}")

    if res["precisions"]["underpowered"]:
        print("Dispute metrics: UNDERPOWERED (n < 20)")
    else:
        print(f"Dispute Precision: {res['precisions']['dispute_precision']:.1%} (CI: {res['precisions']['dispute_ci']})")
        print(f"Override Precision: {res['precisions']['override_precision']:.1%} (CI: {res['precisions']['override_ci']})")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Raw results written to {args.out}")


if __name__ == "__main__":
    main()
