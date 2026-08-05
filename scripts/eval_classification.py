"""Evaluation harness for M4-C ClassificationAgent and AuditorAgent.

Measures ClassificationAgent + AuditorAgent against the `keywords_vi` baseline over
all XML invoices in the corpus.

Usage:
  LLM_MODE=record .venv/bin/python -m scripts.eval_classification --out docs/notes/m4c-raw.json
  LLM_MODE=replay .venv/bin/python -m scripts.eval_classification
  LLM_MODE=live .venv/bin/python -m scripts.eval_classification \\
      --classifier-model groq/llama-3.3-70b-versatile \\
      --auditor-model gemini/gemini-3.5-flash --rpm 20 --retry-attempts 5 \\
      --out docs/notes/m6c-eval-raw.json
"""

import argparse
import functools
import json
import pathlib
import time
from collections import Counter
from typing import Any

from scripts.generator import manifest
from scripts.seed_reference import CATEGORIES

from invoice_agent.agents import audit as audit_mod
from invoice_agent.agents import classification
from invoice_agent.agents.families import family_of
from invoice_agent.config import DEFAULT_AUDITOR_CHAIN, DEFAULT_MODEL_CHAIN, Settings, get_settings
from invoice_agent.db.engine import get_engine
from invoice_agent.eval import baseline
from invoice_agent.extraction.xml_parser import parse_invoice_xml
from invoice_agent.llm import telemetry
from invoice_agent.llm.cost import LLMCallRecord, actual_cost_usd, shadow_cost_usd
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


def models_used(calls: list[LLMCallRecord]) -> list[str]:
    """Which model(s) actually produced an answer, from one agent's call list.

    SUCCESSFUL attempts only: a 429 leaves an LLMCallRecord too (design §2.9),
    and crediting it as the rater is precisely how a two-model mixture comes to
    be reported as a single rater.
    """
    return sorted({c.model for c in calls if c.succeeded})


def answering_model(calls: list[LLMCallRecord]) -> str | None:
    """Which model's output was actually used for the answer, per invoice.

    `call_structured` (`llm/structured.py`) returns the instant a reply
    validates against the response schema and appends nothing afterwards — so
    the LAST successful attempt in the call list is the one whose JSON became
    the answer, not merely one of the models that happened to respond. A model
    can transport-succeed (`succeeded=True`, tokens burned) and still have its
    JSON discarded by Pydantic before a later attempt actually wins;
    `models_used()` credits both, which is correct for "was this run a
    mixture at all" but overstates any one model's share when read as a
    per-invoice count. `answering_model` is the exact per-invoice figure.
    """
    for c in reversed(calls):
        if c.succeeded:
            return c.model
    return None


def pinned_gateways(
    settings: Settings, *, classifier_model: str, auditor_model: str
) -> tuple[LLMGateway, LLMGateway]:
    """Two gateways with single-entry chains — no fallback to demote to.

    Pinning by rebuilding Settings (`model_copy(update=...)`) and constructing
    a gateway from it, rather than adding a `model=` parameter to `classify()`
    — `classify()` never took one, and doesn't need to: it calls
    `gateway.generate()` with no override, so it falls straight to
    `gateway.model_chain`.

    Both gateways are built by overriding `llm_model_chain`, INCLUDING the
    auditor's. `LLMGateway.__init__` (llm/gateway.py) only ever reads
    `llm_model_chain` into `self.model_chain` — verified empirically
    2026-08-05, overriding `auditor_model_chain` instead leaves
    `LLMGateway.model_chain` at the two-entry default, which was a real bug in
    this task's original brief snippet (it would fail
    `test_a_pinned_gateway_has_no_fallback_at_all`).

    Pinning the Auditor's OWN call needs one more step this function does not
    do: `audit()` (agents/audit.py) takes an explicit `model` parameter that
    defaults to `get_settings().auditor_model_chain` rather than reading the
    gateway it is handed — a leftover from when Classifier and Auditor shared
    ONE gateway instance. `evaluate()` passes `model=auditor_gateway.model_chain`
    at the call site; review 2026-08-05 rejected an earlier version that
    monkeypatched `audit_mod.get_settings` instead — a parameter is
    caller-chosen and unreachable by the LLM (cannot self-authorize, same
    reasoning as `calls=`), while a monkeypatch mutates process-wide state for
    the run and (worse) is not type-checkable under this repo's mypy config.

    The two pinned models must not share a model FAMILY (design §6.3): a
    same-family Auditor shares the Classifier's failure mode and agrees with
    its mistake more confidently, regardless of whether the two model NAMES
    differ — `groq/llama-3.3-70b-versatile` and `groq/llama-3.1-8b-instant`
    are different strings but the same `meta-llama` family
    (agents/families.py), exactly the case a name-only check misses.
    """
    classifier_family = family_of(classifier_model)
    auditor_family = family_of(auditor_model)
    if classifier_family == auditor_family:
        raise ValueError(
            f"Classifier ({classifier_model}) and Auditor ({auditor_model}) pinned to "
            f"the same model family ('{classifier_family}'). Design §6.3 wants a "
            "different failure mode; a same-family run reports agreement as "
            "corroboration even under different model names."
        )
    classifier = LLMGateway(settings.model_copy(update={"llm_model_chain": [classifier_model]}))
    auditor = LLMGateway(settings.model_copy(update={"llm_model_chain": [auditor_model]}))
    return classifier, auditor


def call_with_retry(
    fn: Any, *, attempts: int, backoff_seconds: float
) -> tuple[Any, str | None]:
    """Retry the SAME model on failure. Returns (result, None) or (None, reason).

    A pinned chain has nothing to fall through to, so a rate limit raises out
    of `classify()`/`audit()` on the first failed attempt — `call_structured`'s
    own retry loop (llm/structured.py) only covers schema-validation failures,
    not transport errors. Waiting and retrying HERE is what turns a rate limit
    into a delay instead of a silent demotion — and when it still fails after
    `attempts` tries, `unmeasured` keeps the run honest about its own N rather
    than dropping the invoice (shrinks N) or scoring it as wrong (blames the
    model for the provider) — the `INV-0029` precedent.
    """
    last = ""
    for attempt in range(attempts):
        try:
            return fn(), None
        except Exception as exc:  # noqa: BLE001 - reason is recorded, not swallowed
            last = f"{type(exc).__name__}: {exc}"[:200]
            if attempt + 1 < attempts and backoff_seconds:
                time.sleep(backoff_seconds * (2**attempt))
    return None, f"unmeasured: {last}"


def evaluate(
    corpus_dir: pathlib.Path,
    *,
    classifier_gateway: LLMGateway,
    auditor_gateway: LLMGateway,
    rpm: float | None = None,
    retry_attempts: int = 3,
    persist_conn: Any = None,
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
    measured_auditor = 0

    total_calls: list[LLMCallRecord] = []
    persisted_ids: list[int] = []

    # `call_with_retry`'s `backoff_seconds` paces RETRIES after a failure; the
    # RPM figure ALSO seeds that starting backoff, so a rate-limited retry
    # begins no faster than the steady-state pace that likely caused it, then
    # doubles per attempt. Wall-clock at N invoices and no retries is exactly
    # `2 * N * (60 / rpm)` seconds — two throttled calls (classify, audit) per
    # invoice; retries add on top of that only when a call actually fails.
    rpm_delay = 60.0 / rpm if rpm else 0.0
    backoff_seconds = rpm_delay if rpm_delay else 1.0

    for record in xml_records:
        xml_file = corpus_dir.parent / record["path"]
        invoice = parse_invoice_xml(xml_file.read_bytes())
        truth_cat = record["category"]
        truth_dept = record.get("department", "")

        # Baseline prediction
        text = " ".join(ln.description or "" for ln in invoice.lines)
        base_pred = baseline.classify(text, KEYWORDS)
        baseline_pairs.append((truth_cat, base_pred))

        # Agent predictions. A pinned chain has nothing to fall through to
        # on failure, so each call is wrapped in `call_with_retry` rather
        # than left to raise — see that function's docstring.
        classify_calls: list[LLMCallRecord] = []
        audit_calls: list[LLMCallRecord] = []

        if rpm_delay and classifier_gateway.mode != "replay":
            time.sleep(rpm_delay)
        # `functools.partial` binds `invoice`/`classify_calls` at creation
        # time rather than by name lookup at call time — a plain lambda
        # would rebind to whatever the loop variables hold on a LATER
        # iteration if it ever escaped this one (ruff B023).
        class_res, class_unmeasured = call_with_retry(
            functools.partial(
                classification.classify,
                invoice, gateway=classifier_gateway, calls=classify_calls,
            ),
            attempts=retry_attempts,
            backoff_seconds=backoff_seconds,
        )

        # No proposal means nothing for the Auditor to critique — skip the
        # audit call rather than send it a non-answer, and record the row
        # unmeasured for the reason the Classifier failed. `model=` pins the
        # Auditor to its single chain entry directly — a plain, typed keyword
        # argument (agents/audit.py), not a monkeypatch of `get_settings()`.
        audit_res, audit_unmeasured = None, None
        if class_res is not None:
            if rpm_delay and auditor_gateway.mode != "replay":
                time.sleep(rpm_delay)
            audit_res, audit_unmeasured = call_with_retry(
                functools.partial(
                    audit_mod.audit,
                    invoice, class_res, gateway=auditor_gateway, calls=audit_calls,
                    model=auditor_gateway.model_chain,
                ),
                attempts=retry_attempts,
                backoff_seconds=backoff_seconds,
            )
            if audit_res is not None:
                measured_auditor += 1

        dispute_finding = (
            classification.dispute(class_res, audit_res)
            if class_res is not None and audit_res is not None
            else None
        )

        total_calls.extend(classify_calls)
        total_calls.extend(audit_calls)

        if persist_conn is not None:
            persisted_ids.extend(
                telemetry.record(
                    persist_conn, classify_calls, node="classify", agent="ClassificationAgent"
                )
            )
            persisted_ids.extend(
                telemetry.record(
                    persist_conn, audit_calls, node="audit", agent="AuditorAgent"
                )
            )

        if class_res is not None:
            agent_category_pairs.append((truth_cat, class_res.category))
            agent_department_pairs.append((truth_dept, class_res.department))

        eval_rows.append(
            {
                "id": record["invoice_id"],
                "truth": truth_cat,
                "truth_department": truth_dept,
                "baseline_category": base_pred,
                "classifier_category": class_res.category if class_res else None,
                "classifier_department": class_res.department if class_res else None,
                "classifier_confidence": class_res.confidence if class_res else None,
                "classifier_reason": class_res.reasoning if class_res else None,
                "auditor_agrees": audit_res.agrees if audit_res else None,
                "auditor_category": audit_res.proposed_category if audit_res else None,
                "auditor_department": audit_res.proposed_department if audit_res else None,
                "auditor_reason": audit_res.reason if audit_res else None,
                "disputed": dispute_finding is not None,
                # `ambiguous_category` is an entry in the `defects` LIST, not a
                # field. Reading it as a field returned None for every invoice,
                # so the group spec §4 requires scoring SEPARATELY was silently
                # empty — a wrong number, not a crash. Found 2026-08-01.
                "ambiguous": "ambiguous_category" in (record.get("defects") or []),
                "alternative_category": record.get("alternative_category"),
                "classifier_model": models_used(classify_calls),
                "auditor_model": models_used(audit_calls),
                "classifier_answering_model": answering_model(classify_calls),
                "auditor_answering_model": answering_model(audit_calls),
                # The INV-0029 precedent: a persistent failure is a
                # first-class outcome, not dropped and not scored wrong. Split
                # per agent (review 2026-08-05: a combined flag alone left no
                # auditor-specific measured_n) — `unmeasured` stays as the
                # combined at-a-glance flag Task 4's own inspection script reads.
                "classifier_unmeasured": class_unmeasured,
                "auditor_unmeasured": audit_unmeasured,
                "unmeasured": class_unmeasured or audit_unmeasured,
            }
        )

    total_xml = len(xml_records)
    base_acc = sum(1 for t, p in baseline_pairs if t == p) / total_xml if total_xml else 0.0
    base_f1 = macro_f1(baseline_pairs, ALL_CATEGORIES)

    # Denominator is invoices the Classifier actually ANSWERED
    # (`measured_classifier`), not `total_xml`: an unmeasured invoice must
    # neither shrink N by being dropped nor score as wrong by being counted
    # against a denominator it never reached the numerator of.
    measured_classifier = len(agent_category_pairs)
    agent_cat_acc = (
        sum(1 for t, p in agent_category_pairs if t == p) / measured_classifier
        if measured_classifier
        else 0.0
    )
    agent_cat_f1 = macro_f1(agent_category_pairs, ALL_CATEGORIES)

    dept_correct = sum(1 for t, p in agent_department_pairs if t == p)
    agent_dept_acc = dept_correct / measured_classifier if measured_classifier else 0.0

    prec_res = precisions(eval_rows)

    ambiguous_rows = [r for r in eval_rows if r["ambiguous"]]
    ambiguous_matched_alt = sum(
        1 for r in ambiguous_rows if r["classifier_category"] == r.get("alternative_category")
    )

    # `LLMCallRecord` carries token counts, not money — cost is DERIVED by
    # cost.actual_cost_usd / shadow_cost_usd, which is what keeps the pricing
    # table the single place a price is written (M3-A). Shadow cost is what
    # design §2.2's per-invoice target is quoted against; actual is ~0 on free
    # tier, and reporting only actual would make the number look free rather
    # than cheap.
    total_cost = sum(actual_cost_usd(c) for c in total_calls)
    total_shadow = sum(shadow_cost_usd(c) for c in total_calls)

    # Per-invoice mixture, from `answering_model` — the count each of these
    # sums to `total_invoices` (minus any invoice where the agent produced no
    # valid answer at all). `models_used()` above counts every SUCCESSFUL
    # ATTEMPT and over-credits a model that transport-succeeded but lost to
    # Pydantic validation before a later attempt actually won (review
    # 2026-08-05: 293 "successful" Auditor attempts against 285 invoices).
    classifier_mixture = Counter(
        r["classifier_answering_model"] for r in eval_rows if r["classifier_answering_model"]
    )
    auditor_mixture = Counter(
        r["auditor_answering_model"] for r in eval_rows if r["auditor_answering_model"]
    )

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
            # N behind `accuracy`/`macro_f1`/`department_accuracy` above —
            # smaller than `total_invoices` whenever a row's classify failed.
            "measured_n": measured_classifier,
        },
        # A separate `measured_n`: review 2026-08-05 — `classifier.measured_n`
        # only excludes classify failures, so it is NOT `total_invoices minus
        # every unmeasured row` (an audit-only failure still counts toward
        # it, correctly, since the Classifier DID answer). The Auditor needs
        # its own N for Tasks 5/6 to publish a dispute/override precision
        # against the invoices it actually reached a verdict on.
        "auditor": {
            "measured_n": measured_auditor,
        },
        "precisions": prec_res,
        "ambiguous_summary": {
            "count": len(ambiguous_rows),
            "matched_alternative": ambiguous_matched_alt,
        },
        "models": {
            "classifier": sorted({m for r in eval_rows for m in r["classifier_model"]}),
            "auditor": sorted({m for r in eval_rows for m in r["auditor_model"]}),
            # Exact per-invoice mixture — the number M6-C should quote, unlike
            # the ATTEMPT counts `classifier`/`auditor` above.
            "classifier_mixture": dict(sorted(classifier_mixture.items())),
            "auditor_mixture": dict(sorted(auditor_mixture.items())),
        },
        "cost": {
            "total_calls": len(total_calls),
            "total_cost_usd": total_cost,
            "total_shadow_cost_usd": total_shadow,
        },
        "persisted_row_range": {
            "count": len(persisted_ids),
            "min_id": min(persisted_ids) if persisted_ids else None,
            "max_id": max(persisted_ids) if persisted_ids else None,
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
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="skip writing app.llm_calls rows",
    )
    parser.add_argument(
        "--classifier-model",
        default=DEFAULT_MODEL_CHAIN[0],
        help="Single model to pin the Classifier to — no fallback chain "
             f"(default: {DEFAULT_MODEL_CHAIN[0]}).",
    )
    parser.add_argument(
        "--auditor-model",
        default=DEFAULT_AUDITOR_CHAIN[0],
        help="Single model to pin the Auditor to — must be a different family "
             f"from --classifier-model (default: {DEFAULT_AUDITOR_CHAIN[0]}).",
    )
    parser.add_argument(
        "--rpm",
        type=float,
        default=None,
        help="Requests-per-minute throttle, applied before each of the two "
             "per-invoice calls (classify, audit). REQUIRED outside "
             "LLM_MODE=replay — a pinned run has no fallback chain, so an "
             "unthrottled run turns rate limits into unmeasured rows. "
             "Wall-clock at N invoices, no retries: 2*N*(60/rpm)s.",
    )
    parser.add_argument(
        "--retry-attempts",
        type=int,
        default=3,
        help="Attempts per agent call, retrying the SAME pinned model, before "
             "the row is recorded 'unmeasured' (default: 3).",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not (settings.groq_api_keys or settings.gemini_api_keys) and settings.llm_mode != "replay":
        print("ERROR: Precondition failed — no GROQ or GEMINI API keys configured.")
        print("M4-C cannot measure itself without a provider key. Stopping Task 6 per plan.")
        return

    # A pinned chain has no fallback to demote to (that is the whole point),
    # so an unthrottled live/record run turns every rate limit straight into
    # `unmeasured` rows — silently shrinking N, the exact failure this task
    # exists to close. Refuse loudly rather than quietly picking a number
    # (review 2026-08-05): the old code this replaced defaulted to a fixed
    # 0.5s delay, which is not a value worth resurrecting as a silent default
    # for a mode that no longer has a fallback safety net.
    if settings.llm_mode != "replay" and args.rpm is None:
        print("ERROR: --rpm is required outside LLM_MODE=replay.")
        print(
            "A pinned run has no fallback chain, so an unthrottled run turns every "
            "rate limit into an unmeasured row instead of a delay. Pass --rpm "
            "explicitly (Task 4 uses --rpm 20)."
        )
        return

    corpus_dir = args.corpus.parent if args.corpus.is_file() else args.corpus
    classifier_gateway, auditor_gateway = pinned_gateways(
        settings, classifier_model=args.classifier_model, auditor_model=args.auditor_model
    )

    print(f"Evaluating {corpus_dir} (mode={settings.llm_mode})...")
    print(
        f"Pinned — classifier: {args.classifier_model} | auditor: {args.auditor_model} | "
        f"rpm: {args.rpm or 'unthrottled'} | retry-attempts: {args.retry_attempts}"
    )
    eval_kwargs: dict[str, Any] = dict(
        classifier_gateway=classifier_gateway,
        auditor_gateway=auditor_gateway,
        rpm=args.rpm,
        retry_attempts=args.retry_attempts,
    )
    if args.no_persist:
        res = evaluate(corpus_dir, **eval_kwargs)
    else:
        with get_engine().begin() as conn:
            res = evaluate(corpus_dir, persist_conn=conn, **eval_kwargs)

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

    # Per-invoice mixture, not the raw attempt counts — see `answering_model`.
    print(f"Classifier answering-model mixture (of {res['total_invoices']}): "
          f"{res['models']['classifier_mixture']}")
    print(f"Auditor answering-model mixture (of {res['total_invoices']}): "
          f"{res['models']['auditor_mixture']}")

    unmeasured_n = sum(1 for r in res["rows"] if r.get("unmeasured"))
    print(f"Unmeasured invoices: {unmeasured_n} of {res['total_invoices']} "
          f"(see rows[].unmeasured for reasons)")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        # `default=str` because cost is Decimal — money is never float in this
        # project (M1 measured amounts to 8 decimal places). Without it the whole
        # run is lost at the last line: TypeError, after 285 invoices and 570
        # calls. Found 2026-08-01 exactly that way.
        args.out.write_text(
            json.dumps(res, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        print(f"Raw results written to {args.out}")

    # So a run's own app.llm_calls rows are identifiable without hand-diffing
    # max(id) before and after — a concurrent `make check` run interleaving
    # its own rows into the same table is exactly what this was missing
    # (review 2026-08-05).
    row_range = res["persisted_row_range"]
    if row_range["count"]:
        print(
            f"Persisted {row_range['count']} app.llm_calls rows: "
            f"id {row_range['min_id']}–{row_range['max_id']}"
        )
    else:
        print("Persisted 0 app.llm_calls rows (--no-persist or nothing to write)")


if __name__ == "__main__":
    main()
