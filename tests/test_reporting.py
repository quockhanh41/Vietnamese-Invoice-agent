"""The fifth agent — and the guard that lets it ship.

Python computes every number; the model may speak no number it was not given.
The guard rejects rather than repairs: a retry asking the model to fix its own
arithmetic would put the model back in the arithmetic path through the back door.
"""

from decimal import Decimal

from invoice_agent.agents import reporting
from invoice_agent.agents.models import ReportCommentary


def test_plain_integers_and_decimals_are_read_as_written():
    assert reporting.numbers_in("có 9 cảnh báo, chi phí 0.083 USD") == {
        Decimal("9"),
        Decimal("0.083"),
    }


def test_vietnamese_thousand_separators_collapse_to_one_number():
    """`6.525.000` is six and a half million to a Vietnamese reader, not 6.525
    followed by 000. A guard that read it the American way would reject every
    correctly-formatted narrative."""
    assert reporting.numbers_in("vượt 6.525.000 đồng") == {Decimal("6525000")}


def test_vietnamese_decimal_comma_is_a_decimal_point():
    assert reporting.numbers_in("đã dùng 118,6% hạn mức") == {Decimal("118.6")}


def test_trailing_zeros_do_not_make_two_different_numbers():
    """`db/queries` stringifies Decimals straight from Postgres Numeric, so a
    fact arrives as '35000000.00' while a narrative says '35.000.000'."""
    assert reporting.numbers_in("35.000.000") == reporting.numbers_in("35000000.00")


def test_report_commentary_carries_text_and_nothing_else():
    c = ReportCommentary(summary="Kỳ này ổn.", observations=["MKT vượt ngân sách."])
    assert set(ReportCommentary.model_fields) == {"summary", "observations"}
    assert c.summary


def _budget_rows():
    return [
        {
            "department_code": "MKT",
            "period": "2026-07",
            "category": None,
            "amount_limit": "35000000.00",
            "warn_threshold_pct": "0.8",
            "consumed": "41525000.00",
            "remaining": "-6525000.00",
        },
        {
            "department_code": "OPS",
            "period": "2026-07",
            "category": None,
            "amount_limit": "20000000.00",
            "warn_threshold_pct": "0.8",
            "consumed": "17000000.00",
            "remaining": "3000000.00",
        },
    ]


def _facts():
    return reporting.build_facts(
        period="2026-07",
        budgets=_budget_rows(),
        flags=[{"code": "BUDGET_BREACH", "severity": "critical", "total": 9, "open_count": 9}],
        posting=[
            {"posting_authority": "auto", "entries": 23},
            {"posting_authority": "human", "entries": 7},
        ],
        cost={"calls": 60, "succeeded": 60, "cost_usd": "0", "shadow_cost_usd": "0.15"},
        spend=[{"group_key": "MKT", "invoice_count": 12, "total_amount": "41525000.00"}],
    )


def test_overspend_is_computed_by_python_not_read_from_a_string():
    facts = _facts()
    assert [o["department"] for o in facts.over_budget] == ["MKT"]
    assert facts.over_budget[0]["overspend"] == Decimal("6525000.00")


def test_a_department_at_the_warning_threshold_is_warned_not_breached():
    """OPS is at 85% of a limit whose warn threshold is 0.8 — a warning, and it
    must not appear as over budget."""
    facts = _facts()
    assert [w["department"] for w in facts.warning] == ["OPS"]
    assert facts.warning[0]["pct_used"] == Decimal("85.0")


def test_posting_split_is_a_percentage_python_computed():
    facts = _facts()
    assert facts.auto_pct == Decimal("76.7")  # 23 of 30, one decimal place
    assert facts.human_pct == Decimal("23.3")


def test_fact_numbers_include_numbers_hiding_inside_string_values():
    """The period is stored as '2026-07', so `tháng 7/2026` is sayable. A guard
    reading only numeric-typed fields would reject the month."""
    numbers = reporting.fact_numbers(_facts())
    assert Decimal("2026") in numbers
    assert Decimal("7") in numbers
    assert Decimal("6525000") in numbers


def test_a_number_nobody_computed_is_not_a_fact():
    assert Decimal("8000000") not in reporting.fact_numbers(_facts())

