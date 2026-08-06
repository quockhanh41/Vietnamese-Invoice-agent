"""ReportingAgent — design §6.2's fifth agent, and the only one whose output is
prose rather than a label.

The safety story is different from the other four and worth stating: this agent
cannot be constrained by its schema alone, because free text is the deliverable.
So the constraint is arithmetic instead — Python computes every number the
narrative may contain (`build_facts`), and `narrate` rejects any commentary
containing a number that is not one of them.

Vietnamese output, per CLAUDE.md's product-output exception, which names this
agent's commentary explicitly.
"""

import re
from decimal import Decimal, InvalidOperation

# A run of digits with optional `.`/`,` groupers. Percent signs, currency words
# and punctuation around it are not part of the token.
_NUMBER_TOKEN = re.compile(r"\d[\d.,]*")
_VN_THOUSANDS = re.compile(r"^[1-9]\d{0,2}(\.\d{3})+$")
_EN_THOUSANDS = re.compile(r"^\d{1,3}(,\d{3})+$")
_VN_DECIMAL = re.compile(r"^\d+,\d+$")


def _to_decimal(token: str) -> Decimal | None:
    """One token to one number, reading Vietnamese conventions.

    `6.525.000` is 6525000 (dot groups thousands) but `0.083` is 0.083 (dot is a
    decimal point, the form `db/queries` stringifies Decimals into). The grouping
    patterns disambiguate: thousands groups are exactly three digits.
    """
    token = token.rstrip(".,")
    if _VN_THOUSANDS.match(token):
        token = token.replace(".", "")
    elif _EN_THOUSANDS.match(token):
        token = token.replace(",", "")
    elif _VN_DECIMAL.match(token):
        token = token.replace(",", ".")
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def numbers_in(text: str) -> set[Decimal]:
    """Every number a reader would see in `text`.

    Used on BOTH sides of the guard — the narrative and the facts — so the two
    cannot disagree about what a number is. `Decimal` equality is numeric, so
    `35000000.00` and `35.000.000` are the same member of the set.
    """
    found = set()
    for match in _NUMBER_TOKEN.finditer(text):
        value = _to_decimal(match.group())
        if value is not None:
            found.add(value)
    return found
