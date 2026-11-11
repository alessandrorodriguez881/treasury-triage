"""The owner above the agent (10-nov): pausing stops reading and moving money in code, not only on screen; a rejected
bill leaves the plan with its reason; both go into the same hash-chained audit trail."""
import contextlib
from datetime import datetime, timedelta
import io
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ajustar_saldos_demo
import control
import preparar_sandbox
from demo import convertir, leer_correos, pagar, paso_siguiente, situacion
from escenario import OBLIGATIONS
from rail_airwallex import AirwallexRail
from simulador_airwallex import SandboxSimulado
from triage_core.approvals import ApprovalBook
from triage_core.ledger import Ledger
from triage_core.treasury_decision import ESCALATE

RONDA = (datetime.now() - timedelta(hours=1)).strftime("r%Y%m%d-%H%M%S")
nada = lambda *a, **k: None  # noqa: E731
NEW5 = next(o for o in OBLIGATIONS if o.id == "NEW-5")


@pytest.fixture
def abierto(tmp_path, monkeypatch):
    monkeypatch.setattr(ajustar_saldos_demo, "DATA", tmp_path)
    rail = AirwallexRail("simulated", "simulated", session=SandboxSimulado(tmp_path / "sim.json"), sleep=nada)
    with contextlib.redirect_stdout(io.StringIO()):
        preparar_sandbox.main(rail)
        ajustar_saldos_demo.main(rail)
    return rail


def test_a_paused_agent_moves_no_money_and_reads_no_email(abierto, tmp_path):
    rail, libro = abierto, Ledger(tmp_path / "ledger.json")
    book = ApprovalBook.load(tmp_path / "aprobaciones.json")
    s = situacion(rail, libro, RONDA, [], False)
    antes = rail.balances()
    assert control.pausar(libro, RONDA) and not control.pausar(libro, RONDA)  # twice is still one pause
    with pytest.raises(control.AgentePausado):
        convertir(rail, libro, book, s, RONDA, nada)
    with pytest.raises(control.AgentePausado):
        pagar(rail, libro, s, RONDA, set(), nada, book)
    with pytest.raises(control.AgentePausado):
        leer_correos(rail, libro, RONDA, False, con_claude=False)
    assert rail.balances() == antes and not any(e.op_key.startswith(("pay:", "fx:")) for e in libro.entries)
    assert paso_siguiente(libro, RONDA, None, [], False, 0) == "reanudar"
    assert control.reanudar(libro, RONDA) and not control.en_pausa(libro, RONDA)
    pagar(rail, libro, s, RONDA, convertir(rail, libro, book, s, RONDA, nada), nada, book)
    assert situacion(rail, libro, RONDA, [], False).pagadas  # resumed: the plan runs as before
    check = libro.verify()
    assert check.ok and check.events >= 4  # pause and resume are in the chained trail, with the payments


def test_a_rejected_bill_leaves_the_plan_with_its_reason(abierto, tmp_path):
    rail, libro = abierto, Ledger(tmp_path / "ledger.json")
    antes = situacion(rail, libro, RONDA, [], False)
    assert {d.obligation_id: d.action for d in antes.plan.decisions}["NEW-5"] == ESCALATE
    motivo = "payee check on Metal: the account is not registered to Q. Consulting"
    assert control.rechazar(libro, RONDA, NEW5, motivo) and not control.rechazar(libro, RONDA, NEW5, "again")
    despues = situacion(rail, libro, RONDA, [], False)
    assert "NEW-5" not in {o.id for o in despues.pendientes} and despues.rechazadas == {"NEW-5": motivo}
    assert "NEW-5" not in {d.obligation_id for d in despues.plan.decisions}
    # the rest of the plan does not change: the new supplier was never going to be paid by the agent
    resto = lambda p: {d.obligation_id: d.action for d in p.decisions if d.obligation_id != "NEW-5"}  # noqa: E731
    assert resto(despues.plan) == resto(antes.plan)
    assert libro.verify().ok
    assert control.rechazados(libro, "r20990101-000000") == {}  # a new round starts with nothing rejected


def test_controls_are_never_mistaken_for_money(abierto, tmp_path):
    rail, libro = abierto, Ledger(tmp_path / "ledger.json")
    control.pausar(libro, RONDA)
    control.reanudar(libro, RONDA)
    control.rechazar(libro, RONDA, NEW5, "not ordered")
    s = situacion(rail, libro, RONDA, [], False)
    assert not s.pagadas and not s.enviadas  # no control entry counts as a payment or a transfer in flight
    assert all(e.op_key.startswith(f"control:{RONDA}:") for e in libro.entries)


def test_a_decision_on_a_card_goes_to_the_audit_trail(tmp_path):
    """Revisión de ejecución (11-nov): el sí o el no a una tarjeta también va en la auditoría encadenada."""
    from triage_core.ledger import Ledger
    libro = Ledger(tmp_path / "ledger.json")
    assert control.decision_en_tarjeta(libro, "r1", "ab" * 16, "Buy 7,814.17 EUR for 8,828.37 USD", 8828.37, "USD", True)
    assert not control.decision_en_tarjeta(libro, "r1", "ab" * 16, "Buy 7,814.17 EUR", 8828.37, "USD", True)  # una vez
    assert control.decision_en_tarjeta(libro, "r1", "cd" * 16, "Buy 6,002.24 GBP for 8,114.61 USD", 8114.61, "USD", False)
    assert [(e.kind, e.status) for e in libro.entries] == [("approve", "APPROVED"), ("decline", "REJECTED")]
    assert libro.verify().ok
    assert control.rechazados(libro, "r1") == {} and not control.en_pausa(libro, "r1")  # no es un rechazo de un pago
