"""Piece 7: what a supplier invoice says, checked in code against the payables list and the bank details on file.

The AI layer reads the invoice text and proposes a reading. Code accepts the reading only if every value in it
(amount, currency, due date, bank account) appears word for word in that invoice. Then code compares it with
what we already know:
- the amount, currency and due date on the payables list
- the bank account of the beneficiary on file
Any difference is a discrepancy, and the agent never pays an obligation with a discrepancy: it goes to a person.
Invoice fraud usually changes only the bank details, so those are compared too. Payments always go to the
beneficiary on file, never to an account taken from an invoice.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
import re

from .treasury_decision import Obligation


class InvoiceRejected(ValueError):
    pass


@dataclass(frozen=True)
class InvoiceReading:
    document_id: str
    obligation_id: str
    amount: str     # as written on the invoice, without the currency, e.g. "9,000.00"
    currency: str
    due_date: str   # as written, YYYY-MM-DD
    pay_to: str     # the IBAN, or else the account number, as written

    @property
    def evidence(self) -> str:
        return (f"invoice {self.document_id}: {self.amount} {self.currency}, due {self.due_date}, "
                f"pay to {self.pay_to}")


def _compact(text: str) -> str:
    """Spaces and dashes do not count: «DE89 3704…» and «DE893704…» are the same account."""
    return re.sub(r"[\s\-]", "", text).upper()


def _edge(char: str, ahead: bool) -> str:
    """What may not touch the first (or last) character of a value: a digit cannot continue a number (nor «,5» or
    «.5» after it), a letter cannot continue a word."""
    if char.isdigit():
        return r"(?![0-9]|[.,][0-9])" if ahead else r"(?<![0-9])(?<![0-9][.,])"
    if char.isalpha():
        return r"(?![A-Z])" if ahead else r"(?<![A-Z])"
    return ""


def _written_in(value: str, text: str) -> bool:
    """`value` is in `text` whole. Spaces and dashes inside it do not count, but «12,000.00» is not in
    «112,000.00 USD», nor account «50001122» in «500011229»."""
    chars = _compact(value)
    if len(chars) < 3:
        return False
    pattern = r"[\s\-]*".join(map(re.escape, chars))
    return re.search(_edge(chars[0], False) + pattern + _edge(chars[-1], True), text, re.IGNORECASE) is not None


def _amount(text: str) -> float:
    try:
        return float(text.replace(",", ""))
    except ValueError:
        raise InvoiceRejected(f"amount must be the number as written on the invoice, got {text!r}") from None


def validate_reading(reading: InvoiceReading, documents: dict[str, dict], payables: dict[str, Obligation],
                     recorded: list[InvoiceReading]) -> None:
    """Raise InvoiceRejected with the reason if the reading is not what the invoice says."""
    doc = documents.get(reading.document_id)
    if doc is None:
        raise InvoiceRejected(f"unknown document {reading.document_id}")
    if reading.obligation_id not in payables:
        raise InvoiceRejected(f"{reading.obligation_id} is not on the payables list")
    if any(r.document_id == reading.document_id for r in recorded):
        raise InvoiceRejected(f"{reading.document_id} has already been recorded")
    if any(r.obligation_id == reading.obligation_id for r in recorded):
        raise InvoiceRejected(f"{reading.obligation_id} already has an invoice recorded")
    for label, value in (("amount", reading.amount), ("currency", reading.currency),
                         ("due date", reading.due_date), ("bank account", reading.pay_to)):
        if not _written_in(value, doc["body"]):
            raise InvoiceRejected(f"the {label} must be copied word for word from the invoice")
    _amount(reading.amount)


def discrepancies(reading: InvoiceReading, payable: Obligation, due: date, on_file: str | None,
                  known_accounts: frozenset[str] = frozenset()) -> list[str]:
    """What the invoice says that differs from what we already know. Empty list: it matches.

    known_accounts: every account on any supplier's file (compacted), to say where an unknown payee's account is."""
    out = []
    if round(_amount(reading.amount), 2) != round(payable.amount, 2) or reading.currency.upper() != payable.currency:
        out.append(f"invoice says {reading.amount} {reading.currency.upper()}, "
                   f"the payables list says {payable.amount:,.2f} {payable.currency}")
    if reading.due_date != due.isoformat():
        out.append(f"invoice is due {reading.due_date}, the payables list says {due.isoformat()}")
    if on_file is None:
        where = "belongs to another supplier" if _compact(reading.pay_to) in known_accounts else "is on no supplier file"
        out.append(f"bank account on the invoice ({reading.pay_to}) {where}")
    elif _compact(reading.pay_to) != _compact(on_file):
        out.append(f"bank account on the invoice ({reading.pay_to}) is not the one on file")
    return out


def apply_invoice_checks(obligations: list[Obligation], readings: list[InvoiceReading], today: date,
                         on_file: dict[str, str]) -> tuple[list[Obligation], dict[str, list[str]]]:
    """Obligations with a discrepancy get a policy exception (a person decides).

    Returns the obligations and, for each one with a recorded invoice, its discrepancies ([] = matches).
    """
    by_obligation = {r.obligation_id: r for r in readings}
    known = frozenset(_compact(a) for a in on_file.values())
    out, checks = [], {}
    for ob in obligations:
        r = by_obligation.get(ob.id)
        if r is not None:
            checks[ob.id] = issues = discrepancies(r, ob, today + timedelta(days=ob.due_day), on_file.get(ob.payee),
                                                   known)
            if issues:
                why = "; ".join(issues)
                ob = replace(ob, policy_exception=f"{ob.policy_exception}; {why}" if ob.policy_exception
                             else f"invoice check: {why}")
        out.append(ob)
    return out, checks
