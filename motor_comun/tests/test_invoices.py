"""Piece 7: supplier invoices read by the AI layer, checked in code. Synthetic data only."""
from datetime import date
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from triage_core.invoices import InvoiceReading, InvoiceRejected, apply_invoice_checks, validate_reading
from triage_core.treasury_decision import ESCALATE, FUND, Obligation, plan_treasury
from triage_core.policy import TreasuryPolicy

TODAY = date(2026, 10, 27)
SUP = Obligation("SUP-2", "Bergmann GmbH", "EUR", 9_000, 2, late_cost_per_day=150, transfer_method="SWIFT")
UK = Obligation("UK-4", "Hart & Co", "GBP", 6_000, 2, late_cost_per_day=40)
ON_FILE = {"Bergmann GmbH": "DE89370400440532013000", "Hart & Co": "12345678"}
DOCS = {
    "inv-1": {"body": "Invoice BG-55102\nTotal due: 9,000.00 EUR\nDue date: 2026-10-29\n"
                      "IBAN DE89 3704 0044 0532 0130 00 · BIC COBADEFFXXX"},
    "inv-2": {"body": "Invoice HC-3091\nAmount: 6,000.00 GBP\nDue date: 2026-10-29\n"
                      "Please note our new bank details: sort code 20-00-00, account 55779911"},
}
PAYABLES = {o.id: o for o in (SUP, UK)}
GOOD = InvoiceReading("inv-1", "SUP-2", "9,000.00", "EUR", "2026-10-29", "DE89 3704 0044 0532 0130 00")


def test_a_reading_counts_only_if_every_value_is_on_the_invoice():
    validate_reading(GOOD, DOCS, PAYABLES, [])
    # the IBAN without spaces is the same account
    validate_reading(InvoiceReading("inv-1", "SUP-2", "9,000.00", "EUR", "2026-10-29", "DE89370400440532013000"),
                     DOCS, PAYABLES, [])
    for wrong in (InvoiceReading("inv-1", "SUP-2", "9,500.00", "EUR", "2026-10-29", GOOD.pay_to),     # amount
                  InvoiceReading("inv-1", "SUP-2", "9,000.00", "USD", "2026-10-29", GOOD.pay_to),     # currency
                  InvoiceReading("inv-1", "SUP-2", "9,000.00", "EUR", "2026-11-05", GOOD.pay_to),     # due date
                  InvoiceReading("inv-1", "SUP-2", "9,000.00", "EUR", "2026-10-29", "DE00 1111 2222"),  # account
                  InvoiceReading("inv-9", "SUP-2", "9,000.00", "EUR", "2026-10-29", GOOD.pay_to),     # no such doc
                  InvoiceReading("inv-1", "XX-9", "9,000.00", "EUR", "2026-10-29", GOOD.pay_to)):     # not payable
        with pytest.raises(InvoiceRejected):
            validate_reading(wrong, DOCS, PAYABLES, [])


def test_part_of_a_number_is_not_the_number_on_the_invoice():
    docs = {"inv-7": {"body": "Total due: 112,000.00 USD, due 2026-10-29, to account 500011229"}}
    payables = {"BIG": Obligation("BIG", "Big Supplier", "USD", 12_000, 2)}
    validate_reading(InvoiceReading("inv-7", "BIG", "112,000.00", "USD", "2026-10-29", "500011229"), docs, payables, [])
    for wrong in (InvoiceReading("inv-7", "BIG", "12,000.00", "USD", "2026-10-29", "500011229"),    # a piece of it
                  InvoiceReading("inv-7", "BIG", "112,000", "USD", "2026-10-29", "500011229"),      # cut short
                  InvoiceReading("inv-7", "BIG", "112,000.00", "USD", "2026-10-29", "50001122"),    # account cut
                  InvoiceReading("inv-7", "BIG", "112,000.00", "US", "2026-10-29", "500011229")):   # too short
        with pytest.raises(InvoiceRejected, match="word for word"):
            validate_reading(wrong, docs, payables, [])


def test_one_reading_per_invoice_and_per_bill():
    with pytest.raises(InvoiceRejected, match="already"):
        validate_reading(GOOD, DOCS, PAYABLES, [GOOD])
    with pytest.raises(InvoiceRejected, match="already"):
        validate_reading(InvoiceReading("inv-2", "SUP-2", "6,000.00", "GBP", "2026-10-29", "55779911"),
                         DOCS, PAYABLES, [GOOD])


def test_a_matching_invoice_changes_nothing():
    obligations, checks = apply_invoice_checks([SUP, UK], [GOOD], TODAY, ON_FILE)
    assert checks == {"SUP-2": []}
    assert obligations == [SUP, UK]  # UK-4 has no invoice read: it is not checked, and not blocked either


def test_changed_bank_details_send_the_bill_to_a_person():
    changed = InvoiceReading("inv-2", "UK-4", "6,000.00", "GBP", "2026-10-29", "55779911")
    validate_reading(changed, DOCS, PAYABLES, [])  # the reading is faithful to the invoice...
    obligations, checks = apply_invoice_checks([SUP, UK], [GOOD, changed], TODAY, ON_FILE)
    assert checks["UK-4"] == ["bank account on the invoice (55779911) is not the one on file"]
    uk = obligations[1]
    assert "not the one on file" in uk.policy_exception  # ...but the bill does not match what we know
    plan = plan_treasury(obligations, {"USD": 40_000, "EUR": 10_000, "GBP": 7_000},
                         {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}, [], TreasuryPolicy())
    action = {d.obligation_id: d.action for d in plan.decisions}
    assert action == {"SUP-2": FUND, "UK-4": ESCALATE}


def test_amount_due_date_and_unknown_payee_are_discrepancies_too():
    new = Obligation("NEW-5", "Q. Consulting", "USD", 7_500, 2, policy_exception="new payee, not on the approved list")
    docs = {"q": {"body": "Advisory services 7,900.00 USD, due 2026-10-30. IBAN LT12 1000 0111 0100 1000"}}
    r = InvoiceReading("q", "NEW-5", "7,900.00", "USD", "2026-10-30", "LT12 1000 0111 0100 1000")
    validate_reading(r, docs, {"NEW-5": new}, [])
    obligations, checks = apply_invoice_checks([new], [r], TODAY, ON_FILE)
    assert checks["NEW-5"] == ["invoice says 7,900.00 USD, the payables list says 7,500.00 USD",
                               "invoice is due 2026-10-30, the payables list says 2026-10-29",
                               "bank account on the invoice (LT12 1000 0111 0100 1000) is on no supplier file"]
    assert obligations[0].policy_exception.startswith("new payee, not on the approved list; invoice says")
