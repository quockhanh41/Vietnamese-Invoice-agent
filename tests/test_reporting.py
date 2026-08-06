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
