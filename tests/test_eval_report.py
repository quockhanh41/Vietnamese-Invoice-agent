"""The report must compare against targets, not restate them.

Design §10.5: a reported 0.52 beats a claimed 0.98. The failure mode worth
guarding is a generator that prints a target beside a number without deciding
whether the target was met — which reads as a pass to every skimming reader.

Steps 1-2 of the M6-C Task 6 brief. The remaining tests below extend coverage
to `deterministic_rows`/`agent_rows`/`main` per the milestone's own
requirement (M6-C context, not in the brief's literal text): `main()` must
handle a missing artifact by emitting `unmeasured` rows, tested here with the
artifact file absent, and `deterministic_rows` must be exercised against a
real generated corpus rather than only synthetic `Row` objects.
"""

import pytest
from scripts import eval as eval_mod
from scripts import generate_invoices
from scripts.eval import Row, agent_rows, deterministic_rows, render_report, verdict
from scripts.generator import manifest


def test_a_missed_target_is_rendered_as_missed():
    row = Row(stage="Auditor", target="override precision >= 0.60",
              value=0.125, n=24, method="live", met=False)
    out = render_report([row])
    assert "MISSED" in out
    assert "0.125" in out


def test_an_unmeasured_row_is_never_rendered_as_a_pass():
    row = Row(stage="Vision", target="F1 >= 0.90", value=None, n=0,
              method="unmeasured", met=None)
    out = render_report([row])
    assert "unmeasured" in out.lower()
    assert "MET" not in out.split("Vision")[1].split("\n")[0]


def test_verdict_is_computed_not_supplied():
    """A generator that trusted a hand-written `met` flag could publish a
    missed target as met. The comparison happens in code."""
    assert verdict(0.125, ">=", 0.60) is False
    assert verdict(0.954, ">=", 0.60) is True
    assert verdict(None, ">=", 0.60) is None


def test_agent_rows_are_unmeasured_when_the_artifact_is_absent():
    """The live pinned-rater run (docs/notes/2026-08-05-m6c-eval-raw.json) was
    still executing when this report was generated. `agent_rows(None)` is the
    exact path that fires, and it must never fabricate a number."""
    rows = agent_rows(None)
    assert {r.stage for r in rows} == {"Classification", "Auditor", "Cost"}
    for row in rows:
        assert row.method == "unmeasured"
        assert row.value is None
        assert row.met is None


def test_agent_rows_computes_the_verdict_from_a_fixture_not_from_a_flag():
    """A small hand-written fixture shaped like `evaluate()`'s real output
    (scripts/eval_classification.py). Accuracy is set BELOW the gate target on
    purpose, so a generator that trusted a stored `gate_passed`-style flag
    instead of comparing itself would be indistinguishable from this one
    unless the verdict is actually computed here."""
    fixture = {
        "total_invoices": 10,
        "baseline": {"accuracy": 0.60, "macro_f1": 0.55},
        "classifier": {
            "accuracy": 0.70,  # baseline + 0.15 == 0.75, so this MISSES the gate
            "macro_f1": 0.68,
            "gate_target": 0.75,
            "measured_n": 10,
        },
        "precisions": {"n": 4, "override_precision": 0.75, "dispute_precision": 0.25},
        "cost": {"total_shadow_cost_usd": "0.05"},
    }
    rows = agent_rows(fixture)
    by_stage = {r.stage: r for r in rows}

    classification = by_stage["Classification"]
    assert classification.method == "live"
    assert classification.value == 0.70
    assert classification.n == 10
    assert "60.0%" in classification.target  # baseline shown BESIDE the delta
    assert classification.met is False  # 0.70 < gate_target 0.75

    auditor = by_stage["Auditor"]
    assert auditor.value == 0.75
    assert auditor.n == 4
    assert auditor.met is True  # 0.75 >= 0.60

    cost = by_stage["Cost"]
    assert cost.value == pytest.approx(0.005)  # 0.05 / 10 invoices
    assert cost.met is True  # 0.005 <= 0.01


@pytest.fixture(scope="module")
def real_corpus(tmp_path_factory):
    """The same seed=42/count=300 corpus every other eval-adjacent test reads
    (test_near_dup_window.py, test_price_outlier_detection.py), regenerated
    into a temp dir because `corpus/` is gitignored and must not be assumed
    present in CI."""
    out = tmp_path_factory.mktemp("corpus_eval_report") / "corpus"
    generate_invoices.generate(count=300, seed=42, out=out)
    return out


def test_deterministic_rows_against_the_real_corpus(real_corpus):
    """Pins the measured 2026-08-05 numbers so a future corpus/detector change
    is caught rather than silently drifting the published report."""
    rows = deterministic_rows(real_corpus)
    by_stage = {r.stage: r for r in rows}
    assert set(by_stage) == {
        "Anomaly (headline)", "XML extraction", "Vision fallback", "Validation",
        "Security", "HITL durability", "Business / STP",
    }

    anomaly_row = by_stage["Anomaly (headline)"]
    assert anomaly_row.n == 300
    assert anomaly_row.value == pytest.approx(8 / 285)  # raw FP rate, measured 2.807%
    assert anomaly_row.met is True  # under the 5% ceiling even on the raw number

    xml_row = by_stage["XML extraction"]
    assert xml_row.n == 285
    assert xml_row.value == 1.0
    assert xml_row.met is True

    validation_row = by_stage["Validation"]
    assert validation_row.n == 285
    assert validation_row.value == 1.0
    assert validation_row.met is True

    # Design §10.4's negative control (M6-A, 22/24) is a MEASURED miss and
    # M6-C does not re-litigate it (spec §3) — the row must publish it as one.
    security_row = by_stage["Security"]
    assert security_row.value == pytest.approx(22 / 24)
    assert security_row.met is False

    hitl_row = by_stage["HITL durability"]
    assert hitl_row.value == 1.0
    assert hitl_row.met is True

    business_row = by_stage["Business / STP"]
    assert business_row.method == "pending"
    assert business_row.met is None


def test_the_anomaly_footnote_identifies_the_six_twins_and_both_rates(real_corpus):
    records = manifest.read_manifest(real_corpus / "manifest.jsonl")
    stats = eval_mod._score_anomaly(records)
    footnote = eval_mod._anomaly_footnote(stats)
    for twin in eval_mod.INJECTION_NEGATIVE_CONTROL_TWINS:
        assert twin in footnote
    assert "2.807%" in footnote or f"{stats['fp_raw_rate']:.3%}" in footnote
    assert f"{stats['fp_attributed_rate']:.3%}" in footnote


def test_main_emits_unmeasured_agent_rows_when_the_eval_json_is_missing(
    real_corpus, tmp_path, monkeypatch
):
    """The exact scenario this report ships in today: the pinned-rater run
    (docs/notes/2026-08-05-m6c-eval-raw.json) had not finished. `main()` must
    not wait for it and must not fabricate agent numbers in its absence."""
    missing_json = tmp_path / "does-not-exist.json"
    out_path = tmp_path / "eval-report.md"
    monkeypatch.setattr(
        "sys.argv",
        [
            "eval.py",
            "--eval-json", str(missing_json),
            "--corpus", str(real_corpus),
            "--out", str(out_path),
        ],
    )
    eval_mod.main()

    text = out_path.read_text(encoding="utf-8")
    assert "| Classification |" in text
    classification_line = next(ln for ln in text.splitlines() if ln.startswith("| Classification |"))
    assert "unmeasured" in classification_line
    assert "MET" not in classification_line.split("unmeasured")[-1]
    # The footnote is present and readable prose, not folded into a cell.
    assert "## Footnote" in text
    assert "flag false-positive rate" in text.lower()
