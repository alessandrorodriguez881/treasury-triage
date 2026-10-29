"""Approval cards bound to an Airwallex quote: offline, with a fake rail that hands out quotes."""
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aprobaciones import APROBADA, CADUCADA, ESPERA, RECHAZADA, aprobacion_de_conversion, cotizacion_aprobada
from triage_core.approvals import ApprovalBook

NOW = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)


class FakeRail:
    def __init__(self):
        self.quotes = {}

    def create_quote(self, sell_currency, buy_currency, buy_amount, validity):
        qid = f"q{len(self.quotes) + 1}"
        self.quotes[qid] = {"quote_id": qid, "sell_amount": round(buy_amount * 1.126, 2), "client_rate": 1.126,
                            "valid_to_at": (NOW + timedelta(hours=1)).isoformat()}
        return self.quotes[qid]

    def get_quote(self, quote_id):
        return self.quotes[quote_id]


def ask(rail, book, amount=7812.85, signals=("Globex email",), now=NOW, ronda="r1"):
    return aprobacion_de_conversion(rail, book, "USD", "EUR", amount, "low confidence", ronda, signals, now)


def test_first_run_creates_one_card_and_waits():
    rail, book = FakeRail(), ApprovalBook()
    state, fp = ask(rail, book)
    assert state == ESPERA and ask(rail, book) == (ESPERA, fp)  # running again does not spam cards
    assert len(rail.quotes) == 1 and list(book.pending) == [fp]
    assert book.pending[fp].amount == round(7812.85 * 1.126, 2)  # the exact USD cost of the quote


def test_approved_card_converts_at_its_quote():
    rail, book = FakeRail(), ApprovalBook()
    _, fp = ask(rail, book)
    book.approve(fp, "owner")
    assert ask(rail, book) == (APROBADA, "q1")


def test_other_amount_or_a_new_signal_needs_a_new_approval():
    rail, book = FakeRail(), ApprovalBook()
    book.approve(ask(rail, book)[1], "owner")
    assert ask(rail, book, amount=7900.00)[0] == ESPERA
    assert ask(rail, book, signals=("Globex email", "Globex disputes the invoice"))[0] == ESPERA
    assert len(book.pending) == 2


def test_expired_quote_needs_a_new_approval():
    rail, book = FakeRail(), ApprovalBook()
    book.approve(ask(rail, book)[1], "owner")
    state, _ = ask(rail, book, now=NOW + timedelta(hours=2))
    assert state == ESPERA and len(rail.quotes) == 2  # new quote, new card


def test_rejection_holds_for_the_round_but_not_the_next():
    rail, book = FakeRail(), ApprovalBook()
    _, fp = ask(rail, book)
    book.reject(fp, "owner")
    assert ask(rail, book) == (RECHAZADA, fp)
    assert ask(rail, book, ronda="r2")[0] == ESPERA


def test_approved_cost_is_kept_when_the_agent_no_longer_needs_approval():
    """Globex pays, its email stops mattering, the agent could convert alone: it still honours the approved quote."""
    rail, book = FakeRail(), ApprovalBook()
    book.approve(ask(rail, book)[1], "owner")
    assert cotizacion_aprobada(rail, book, "EUR", 7812.85, "r1", NOW) == "q1"
    assert cotizacion_aprobada(rail, book, "EUR", 7900.00, "r1", NOW) is None              # other amount
    assert cotizacion_aprobada(rail, book, "EUR", 7812.85, "r2", NOW) is None              # other round
    assert cotizacion_aprobada(rail, book, "EUR", 7812.85, "r1", NOW + timedelta(hours=2)) is None  # expired


def test_a_pending_card_is_never_used_as_an_approval():
    rail, book = FakeRail(), ApprovalBook()
    ask(rail, book)
    assert cotizacion_aprobada(rail, book, "EUR", 7812.85, "r1", NOW) is None


def test_an_expired_pending_card_is_not_a_no():
    rail, book = FakeRail(), ApprovalBook()
    _, old = ask(rail, book)
    state, new = ask(rail, book, now=NOW + timedelta(hours=2))
    assert state == ESPERA and new != old
    assert book.rejected[old]["approver"] == CADUCADA
