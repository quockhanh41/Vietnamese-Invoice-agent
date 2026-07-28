"""Journal-entry construction (design doc §2.7). Pure — nothing is posted here.

A real office-supplies entry:

    Nợ 6422   3.250.000        expense, pre-tax
    Nợ 1331     260.000        deductible input VAT
        Có 331          3.510.000   payable to the supplier

Account numbers are real TT133/2016/TT-BTC codes and arrive as an argument from
the seeded `expense_categories` row — this module reads no database.
"""

from dataclasses import dataclass
from decimal import Decimal

from invoice_agent.domain import money
from invoice_agent.extraction.models import InvoiceData

ZERO = Decimal("0")


@dataclass(frozen=True)
class CategoryAccounts:
    debit_account: str
    debit_name: str
    vat_account: str | None
    vat_name: str | None
    credit_account: str
    credit_name: str


@dataclass(frozen=True)
class JournalLine:
    account_code: str
    account_name: str
    debit: Decimal
    credit: Decimal
    description: str | None = None


def build_journal_entry(invoice: InvoiceData, accounts: CategoryAccounts) -> list[JournalLine]:
    """Debit expense (+ VAT), credit payable. Always balances by construction.

    The expense debit comes from `money.goods_subtotal`, NOT `invoice.subtotal`.
    The payable credit is `subtotal + vat`, NOT `invoice.total_amount`.
    Measured 2026-07-28 (spec §2.3): a non-VAT invoice reports TgTCThue = 0
    while its lines carry the full amount. Moreover, an invoice with an arithmetic
    defect (total_amount disagreeing with lines) must still produce a balanced
    journal entry when approved by a human — crediting `subtotal + vat` makes it
    balance by construction and prevents `ck_journal_balanced` from rejecting it.
    """
    subtotal = money.goods_subtotal(invoice.lines)
    vat = invoice.vat_amount or ZERO

    lines = [JournalLine(accounts.debit_account, accounts.debit_name, subtotal, ZERO)]
    if vat > ZERO and accounts.vat_account is not None:
        lines.append(
            JournalLine(accounts.vat_account, accounts.vat_name or "", vat, ZERO)
        )
    lines.append(
        JournalLine(accounts.credit_account, accounts.credit_name, ZERO, subtotal + vat)
    )
    return lines
