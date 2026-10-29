"""Modo demo sin claves: la demo entera contra el simulador del sandbox, y la repetición de la lectura de Claude."""
import contextlib
from datetime import datetime, timedelta
import io
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ajustar_saldos_demo
import demo
import preparar_sandbox
from demo import conciliar, convertir, deposito_globex, leer_correos, pagar, situacion
from escenario import fecha_ronda
from rail_airwallex import AirwallexError, AirwallexRail
from simulador_airwallex import SandboxSimulado
from triage_core.approvals import ApprovalBook
from triage_core.ledger import Ledger
from triage_core.policy import PAYOUT_FEES, TreasuryPolicy

RONDA = (datetime.now() - timedelta(hours=1)).strftime("r%Y%m%d-%H%M%S")  # empezó hace una hora: los depósitos de ahora son suyos
nada = lambda *a, **k: None


def simulado(tmp_path) -> AirwallexRail:
    return AirwallexRail("simulated", "simulated", session=SandboxSimulado(tmp_path / "sim.json"), sleep=nada)


@pytest.fixture
def abierto(tmp_path, monkeypatch):
    """El simulador con los saldos de partida de la demo, preparados por los mismos scripts que el sandbox."""
    monkeypatch.setattr(ajustar_saldos_demo, "DATA", tmp_path)
    rail = simulado(tmp_path)
    with contextlib.redirect_stdout(io.StringIO()):
        preparar_sandbox.main(rail)
        ajustar_saldos_demo.main(rail)
    return rail


def test_the_whole_demo_runs_without_keys(abierto, tmp_path):
    rail, libro, book = abierto, Ledger(tmp_path / "ledger.json"), ApprovalBook.load(tmp_path / "aprobaciones.json")
    saldos = {d: float(b["available_amount"]) for d, b in rail.balances().items()}
    assert saldos == {"USD": 38_000, "EUR": 1_200}

    lectura = leer_correos(rail, libro, RONDA, False, con_claude=False)  # ensayo: el correo de Globex
    s = situacion(rail, libro, RONDA, lectura.senales, False, lectura.facturas)
    pagar(rail, libro, s, RONDA, convertir(rail, libro, book, s, RONDA, nada, nada), nada, book)
    assert situacion(rail, libro, RONDA, lectura.senales, False, lectura.facturas).pagadas == {"SHIP-1"}
    assert len(book.pending) == 2  # las dos conversiones esperan a una persona

    for fp in list(book.pending):
        book.approve(fp, "owner")
    deposito_globex(rail, libro, RONDA, nada, nada)
    assert "Globex" in conciliar(rail, libro, RONDA)  # el agente ve el depósito en la cuenta y lo cruza con la factura
    s = situacion(rail, libro, RONDA, lectura.senales, True, lectura.facturas)
    pagar(rail, libro, s, RONDA, convertir(rail, libro, book, s, RONDA, nada, nada), nada, book)

    final = situacion(rail, libro, RONDA, lectura.senales, True, lectura.facturas)
    assert final.pagadas == {"SHIP-1", "SUP-2", "SAAS-3", "UK-4"}  # Q. Consulting sigue esperando a una persona
    caja = sum(a * final.rates[d] for d, a in final.saldos.items())
    assert caja > TreasuryPolicy().reserve_floor  # la reserva, intacta
    assert final.saldos["EUR"] < 1 and final.saldos["GBP"] < 1  # se compró justo lo que hacía falta


def test_it_behaves_like_the_sandbox_where_it_matters(abierto):
    rail = abierto
    benef = rail.create_beneficiary({"company_name": "Test Co"}, "Test")["id"]
    t = rail.create_transfer("req-1", benef, "USD", "USD", 1_000, "test")
    assert t["status"] == "SCHEDULED" and t["fee_amount"] == PAYOUT_FEES[("LOCAL", "USD")]
    assert rail.create_transfer("req-1", benef, "USD", "USD", 1_000, "test")["id"] == t["id"]  # nunca dos veces
    rail.simulate_transfer_status(t["id"], "FAILED")
    usd = float(rail.balances()["USD"]["available_amount"])
    assert usd == pytest.approx(38_000 - PAYOUT_FEES[("LOCAL", "USD")])  # vuelve el importe, no la comisión
    with pytest.raises(AirwallexError) as e:
        rail.create_transfer("req-2", benef, "USD", "USD", 1_000_000, "too much")
    assert e.value.code == "balance_insufficient"
    q = rail.create_quote("USD", "EUR", 100)
    c = rail.create_conversion("conv-1", "USD", "EUR", 100, quote_id=q["quote_id"])
    assert c["status"] == "SETTLED" and c["sell_amount"] == q["sell_amount"]  # al coste que se aprobó
    assert rail.create_conversion("conv-1", "USD", "EUR", 100)["id"] == c["id"]  # un request_id repetido no convierte otra vez


def test_a_deposit_counts_only_when_it_matches_the_invoice_and_only_once(abierto, tmp_path):
    rail, libro = abierto, Ledger(tmp_path / "ledger.json")
    cuenta = next(c for c in rail.global_accounts() if c["currency"] == "USD")["id"]
    # el mismo importe, pero de otro pagador y sin la factura en la referencia: no es el cobro de Globex
    rail.simulate_deposit(cuenta, 18_000, reference="Consulting fees", payer_name="Initech")
    assert conciliar(rail, libro, RONDA) == {}
    rail.simulate_deposit(cuenta, 18_000, reference="Invoice GX-2207", payer_name="Globex Corp")
    hechos = conciliar(rail, libro, RONDA)
    assert list(hechos) == ["Globex"]
    assert conciliar(rail, libro, RONDA) == hechos  # mirarlo otra vez no lo cuenta dos veces
    assert sum(e.op_key.startswith("match:") for e in libro.entries) == 1


def _grabar(rail, libro, ronda, globex_pagado=False):
    """Una lectura como la de Claude (la del ensayo), con el plan que habría visto y un resumen suyo."""
    lectura = leer_correos(rail, libro, ronda, globex_pagado, con_claude=False)
    lectura.resumen = "Briefing written by Claude"
    plan = demo.resumen(demo.situacion(rail, libro, ronda, lectura.senales, globex_pagado, lectura.facturas))
    demo.guardar_grabacion(fecha_ronda(ronda), lectura, plan)


def test_a_real_claude_run_is_replayed_and_checked_again_by_code(abierto, tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "GRABACION", tmp_path / "grabacion.json")
    libro = Ledger(tmp_path / "ledger.json")
    _grabar(abierto, libro, RONDA)
    otra = "r20261105-090000"  # una semana después: los vencimientos de las facturas son otros
    repetida = leer_correos(abierto, libro, otra, False, con_claude=False, repetir=True)
    assert repetida.pasos[0].startswith("Replay of a real Claude run")
    assert [s.customer for s in repetida.senales] == ["Globex"]
    assert len(repetida.facturas) == 5
    assert repetida.facturas[0].due_date == (fecha_ronda(otra) + timedelta(days=1)).isoformat()
    assert repetida.resumen == "Briefing written by Claude"  # same plan as Claude saw: its briefing is shown
    # the screen keeps what the briefing talks about, without the live FX costs (they move by the second)
    ahora = demo.resumen(demo.situacion(abierto, libro, otra, repetida.senales, False, repetida.facturas))
    assert demo.huella(repetida.plan) == demo.huella(ahora)
    assert demo.huella({**ahora, "confidence": "93%"}) != demo.huella(ahora)
    costes = [{**c, "approx_cost": "1.00 USD"} for c in ahora["conversions"]]
    assert demo.huella({**ahora, "conversions": costes}) == demo.huella(ahora)


def test_claudes_recorded_briefing_is_hidden_when_the_numbers_changed(abierto, tmp_path, monkeypatch):
    """Globex already paid (step 4 before step 2): the briefing would say 75 % while the screen says 93 %."""
    monkeypatch.setattr(demo, "GRABACION", tmp_path / "grabacion.json")
    libro = Ledger(tmp_path / "ledger.json")
    _grabar(abierto, libro, RONDA)
    repetida = leer_correos(abierto, libro, RONDA, True, con_claude=False, repetir=True)
    assert repetida.resumen == demo.NO_CUADRA
