"""Sandbox quirks seen on 27-oct, offline with fake rails: the simulation answering 500, and balances that are
credited a few seconds after a conversion or a deposit."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from demo import avanzar, en_camino, esperar_saldo, paso_siguiente
from rail_airwallex import AirwallexError
from triage_core.ledger import Ledger


class FakeRail:
    """failures: one item per simulate call: None = works, "lost" = 500 before applying, "applied" = 500 after."""

    def __init__(self, failures=()):
        self.status, self.failures, self.calls = "SCHEDULED", list(failures), []

    def get_transfer(self, transfer_id):
        return {"status": self.status}

    def simulate_transfer_status(self, transfer_id, next_status):
        self.calls.append(next_status)
        failure = self.failures.pop(0) if self.failures else None
        if failure == "applied":
            self.status = next_status
        if failure:
            raise AirwallexError(500, "operation_failed", "Request could not be processed", "/simulation")
        self.status = next_status
        return {"status": next_status}


def entry(tmp_path):
    led = Ledger(tmp_path / "ledger.json")
    e = led.begin("pay:r1:SHIP-1", "transfer", 12_000, "USD", "FreightCo")
    led.update(e, "SCHEDULED", "tfr_1")
    return led, e


def test_one_500_is_retried_and_the_transfer_reaches_paid(tmp_path):
    led, e = entry(tmp_path)
    rail = FakeRail(["lost"])
    assert avanzar(rail, led, e, log=lambda m: None, espera=lambda s: None) == "PAID"
    assert rail.calls == ["SENT", "SENT", "PAID"] and e.status == "PAID"


def test_a_500_that_still_moved_the_status_is_not_repeated(tmp_path):
    led, e = entry(tmp_path)
    rail = FakeRail(["applied"])
    assert avanzar(rail, led, e, log=lambda m: None, espera=lambda s: None) == "PAID"
    assert rail.calls == ["SENT", "PAID"]  # it looked at the real status instead of asking for SENT again


def test_two_500s_leave_it_in_flight_and_the_next_run_finishes_it(tmp_path):
    led, e = entry(tmp_path)
    said = []
    assert avanzar(FakeRail(["lost", "lost"]), led, e, log=said.append, espera=lambda s: None) == "SCHEDULED"
    assert any("press Execute again" in m for m in said)
    again = Ledger(tmp_path / "ledger.json")
    pending = en_camino(again, "pay:r1:SHIP-1")
    assert pending is not None and pending.provider_id == "tfr_1"  # counted as committed, not as unpaid
    assert avanzar(FakeRail(), again, pending, log=lambda m: None, espera=lambda s: None) == "PAID"
    assert en_camino(Ledger(tmp_path / "ledger.json"), "pay:r1:SHIP-1") is None


class SlowBalances:
    """The sandbox credits a conversion or a deposit a few seconds later (seen on 27-oct)."""

    def __init__(self, amounts):
        self.amounts = list(amounts)

    def balances(self):
        amount = self.amounts.pop(0) if len(self.amounts) > 1 else self.amounts[0]
        return {"EUR": {"available_amount": amount}}


def test_it_waits_for_the_bought_currency_before_paying_with_it():
    said, waits = [], []
    rail = SlowBalances([1_200.00, 1_200.00, 9_013.46])
    assert esperar_saldo(rail, "EUR", 9_013.46, log=said.append, espera=waits.append)
    assert len(waits) == 2 and said == ["   waiting for Airwallex to credit the EUR balance..."]


def test_if_the_money_never_shows_up_it_says_so_instead_of_paying():
    assert not esperar_saldo(SlowBalances([1_200.00]), "EUR", 9_013.46, log=lambda m: None, espera=lambda s: None,
                             intentos=3)


def test_failed_or_never_sent_attempts_are_not_in_flight(tmp_path):
    led = Ledger(tmp_path / "ledger.json")
    e = led.begin("pay:r1:UK-4", "transfer", 6_000, "GBP", "Hart & Co")
    assert en_camino(led, "pay:r1:UK-4") is None  # created in our ledger, never reached Airwallex
    led.update(e, "FAILED")
    assert en_camino(led, "pay:r1:UK-4") is None


class PayRail:
    """Just enough of Airwallex to pay one bill."""

    def __init__(self):
        self.transfers = []

    def beneficiaries(self):
        return [{"nickname": "FreightCo", "id": "ben_1"}]

    def create_transfer(self, request_id, beneficiary_id, source_currency, currency, amount, **kw):
        self.transfers.append((beneficiary_id, amount, currency))
        return {"id": f"tfr_{len(self.transfers)}", "status": "PAID"}

    def get_transfer(self, transfer_id):
        return {"status": "PAID"}


def from_the_reserve():
    from demo import Situacion
    from triage_core.cash_simulation import CashRisk
    from triage_core.treasury_decision import FUND, Decision, Obligation, Plan
    ship = Obligation("SHIP-1", "FreightCo", "USD", 12_000, 1, late_cost_per_day=50, stops_operations=True)
    plan = Plan([Decision("SHIP-1", FUND, "halts operations if unpaid")], [], CashRisk(0.3, 14_000, 9_000), 0.8,
                10_000, 0.0, [], uses_reserve=True)
    return Situacion({"USD": 24_000}, {"USD": 1.0}, set(), [ship], (), plan)


def test_a_bill_paid_from_the_reserve_never_leaves_without_a_person(tmp_path):
    from demo import pagar
    from triage_core.approvals import ApprovalBook
    rail, book, said = PayRail(), ApprovalBook(), []
    pagar(rail, Ledger(tmp_path / "ledger.json"), from_the_reserve(), "r1", set(), log=said.append, book=book)
    assert rail.transfers == [] and len(book.pending) == 1  # a card, no money moved
    assert any("from the cash reserve" in m for m in said)
    book.approve(next(iter(book.pending)), "owner")
    pagar(rail, Ledger(tmp_path / "ledger.json"), from_the_reserve(), "r1", set(), log=said.append, book=book)
    assert rail.transfers == [("ben_1", 12_000, "USD")]


def test_without_an_approval_book_it_does_not_pay_from_the_reserve(tmp_path):
    from demo import pagar
    rail = PayRail()
    pagar(rail, Ledger(tmp_path / "ledger.json"), from_the_reserve(), "r1", set(), log=lambda m: None)
    assert rail.transfers == []


def test_the_next_step_follows_the_order_of_the_demo(tmp_path):
    led, ronda = Ledger(tmp_path / "ledger.json"), "r20261106-120000"

    def paid(oid):
        led.update(led.begin(f"pay:{ronda}:{oid}", "transfer", 1, "USD", oid), "PAID", f"tr_{oid}")

    assert paso_siguiente(led, ronda, None, [], False, 0) == "2"
    assert paso_siguiente(led, ronda, [], [], False, 2) == "aprobar"
    assert paso_siguiente(led, ronda, [], [], False, 0) == "3"
    paid("SHIP-1")
    assert paso_siguiente(led, ronda, [], [], False, 0) == "4"
    assert paso_siguiente(led, ronda, [], [], True, 0) == "3 otra vez"
    for oid in ("SUP-2", "UK-4", "SAAS-3"):
        paid(oid)
    assert paso_siguiente(led, ronda, [], [], True, 0) is None  # NEW-5 waits for a person, not for the agent
