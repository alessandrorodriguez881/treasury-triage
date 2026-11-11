"""The payee check on Metal, offline: a fake metal-agent CLI, a fake HTTP session and a fake Airwallex rail.

What matters: it never changes the plan, its fingerprint or the replayed Claude briefing; it never stops the demo;
each check is paid once per round and invoice; and the card says what was found and where to verify it."""
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import comprobacion_metal as m
from demo import NO_CUADRA, comprobar_beneficiarios, huella, leer_correos, resumen, situacion
from escenario import facturas_ensayo, fecha_ronda
from rail_metal import MetalError, MetalRail
from test_rail_metal import TX, FakeCli, FakeHttp, FakeResponse, offer, settlement
from triage_core.ledger import Ledger

RONDA = "r20261101-120000"
AGENT = "0x7393A826Fac730EaBA3cc58a8407236D9De4580f"
SERVICE = m.SERVICIO["pay_to"]
CHECK = {"check_id": f"{RONDA}-inv-205", "account": "LT121000011101001000", "name": "Q. Consulting",
         "result": "no_match", "name_on_record": None, "checked_at": "2026-11-01T12:00:05.000Z", "status": "paid",
         "transaction": TX, "payer": AGENT}


def factura_nueva():
    return next(f for f in facturas_ensayo(fecha_ronda(RONDA)) if f.obligation_id == "NEW-5")


class Airwallex:
    """The opening balances and the client rates the simulator uses."""

    def balances(self):
        return {"USD": {"available_amount": 38_000}, "EUR": {"available_amount": 1_200}, "GBP": {"available_amount": 0}}

    def current_rate(self, buy, sell, buy_amount=None):
        r = {"EUR": 1.1265, "GBP": 1.346}[buy]
        return {"rate_details": [{"level": "CLIENT", "buy_amount": buy_amount, "sell_amount": round(buy_amount * r, 2)}]}


@pytest.fixture
def grabacion(tmp_path, monkeypatch):
    path = tmp_path / "grabacion_metal.json"
    path.write_text(json.dumps({"recorded_on": "2026-11-01", "payee": "Q. Consulting", "account": CHECK["account"],
                                "result": "no_match", "name_on_record": None, "checked_at": CHECK["checked_at"],
                                "price": "0.25", "transaction": TX, "explorer": f"https://testnet.metalntwx.com/tx/{TX}",
                                "payer": AGENT, "service": m.SERVICIO["service"]}), encoding="utf-8")
    monkeypatch.setattr(m, "GRABACION_METAL", path)
    return path


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "agente.key.json"
    path.write_text(json.dumps({"address": AGENT, "privateKey": "0x" + "ab" * 32}), encoding="utf-8")
    return path


def test_only_an_account_that_is_on_no_file_is_checked():
    demo = facturas_ensayo(fecha_ronda(RONDA))
    assert [(f.obligation_id, payee) for f, payee in m.por_comprobar(demo)] == [("NEW-5", "Q. Consulting")]
    nueva_cuenta = [replace(f, pay_to="99990000") if f.obligation_id == "SHIP-1" else f for f in demo]
    assert [f.obligation_id for f, _ in m.por_comprobar(nueva_cuenta)] == ["SHIP-1", "NEW-5"]


def test_the_plan_its_fingerprint_and_the_claude_replay_do_not_change(tmp_path, monkeypatch, grabacion):
    monkeypatch.setenv("TT_METAL", "replay")
    rail, libro = Airwallex(), Ledger(tmp_path / "ledger.json")
    antes = leer_correos(rail, libro, RONDA, False, con_claude=False, repetir=True)
    plan_antes = resumen(situacion(rail, libro, RONDA, antes.senales, False, antes.facturas))
    hechas = comprobar_beneficiarios(RONDA, antes.facturas, [].append, carpeta=tmp_path)
    despues = leer_correos(rail, libro, RONDA, False, con_claude=False, repetir=True)
    plan_despues = resumen(situacion(rail, libro, RONDA, despues.senales, False, despues.facturas))
    assert [r["mode"] for r in hechas] == ["replay"]
    assert plan_antes == plan_despues and huella(plan_antes) == huella(plan_despues)
    assert despues.resumen == antes.resumen != NO_CUADRA  # Claude's recorded briefing still matches the plan
    assert libro.entries == [] and libro.verify().ok  # nothing in the Airwallex ledger or its audit trail


def test_the_replay_is_a_real_recorded_payment_and_says_so(tmp_path, monkeypatch, grabacion):
    monkeypatch.setenv("TT_METAL", "replay")
    mensajes = []
    [r] = comprobar_beneficiarios(RONDA, [factura_nueva()], mensajes.append, carpeta=tmp_path)
    assert (r["mode"], r["result"], r["transaction"]) == ("replay", "no_match", TX)
    assert "replay of a real Metal testnet payment" in mensajes[0] and "not registered to Q. Consulting" in mensajes[0]
    assert m.comprobaciones_de(RONDA, tmp_path)["NEW-5"] == r
    linea = m.texto_tarjeta(r)
    assert linea.startswith(":material/policy: Payee check on Metal (our demo service): no match. The account in this invoice is not "
                            "registered to Q. Consulting. Paid 0.25 \\*USDZ")
    assert "replay of a real Metal testnet payment (2026-11-01)" in linea
    assert linea.endswith(f"[view on Metal explorer](https://testnet.metalntwx.com/tx/{TX})")


def test_a_recording_of_another_account_is_not_shown_as_this_one(tmp_path, monkeypatch, grabacion):
    monkeypatch.setenv("TT_METAL", "replay")
    otra = replace(factura_nueva(), pay_to="LT99 0000 0000 0000 0000")
    [r] = comprobar_beneficiarios(RONDA, [otra], [].append, carpeta=tmp_path)
    assert r["mode"] == "unavailable" and "another account" in r["note"]


@pytest.mark.parametrize("fallo", [MetalError("Metal testnet did not answer"), ConnectionError("refused"),
                                   OSError("node not found")])
def test_if_metal_fails_the_demo_goes_on_with_a_calm_note(tmp_path, monkeypatch, fallo):
    monkeypatch.setenv("TT_METAL", "real")

    def roto(*a, **k):
        raise fallo
    monkeypatch.setattr(m, "comprobar_real", roto)
    mensajes = []
    [r] = comprobar_beneficiarios(RONDA, [factura_nueva()], mensajes.append, carpeta=tmp_path)
    assert r["mode"] == "unavailable"
    assert mensajes == [f"Q. Consulting's invoice (inv-205): Payee check on Metal unavailable ({r['note']}). "
                        "The bill still goes to a person."]
    assert m.texto_tarjeta(m.comprobaciones_de(RONDA, tmp_path)["NEW-5"]).startswith(
        ":material/policy: Payee check on Metal unavailable")


def test_off_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("TT_METAL", "off")
    assert comprobar_beneficiarios(RONDA, [factura_nueva()], [].append, carpeta=tmp_path) == []
    assert not (tmp_path / "comprobaciones.json").exists()


def test_by_default_it_pays_for_real_only_with_the_real_sandbox(tmp_path, monkeypatch):
    monkeypatch.delenv("TT_METAL", raising=False)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "agente.key.json").write_text("{}")
    monkeypatch.setattr(m, "RUNTIME", tmp_path)
    monkeypatch.setattr(m, "CLAVE_AGENTE", tmp_path / "agente.key.json")
    monkeypatch.setattr(m, "SIMULADO", False)
    assert m.modo_metal() == "real"
    monkeypatch.setattr(m, "SIMULADO", True)  # the keyless demo replays the recorded payment
    assert m.modo_metal() == "replay"
    monkeypatch.setenv("TT_METAL", "real")
    assert m.modo_metal() == "real"


def test_a_real_check_is_paid_once_per_round_and_invoice(tmp_path, key_file):
    pagado = (f"nonce 0x{'7d' * 32}\nstatus 200\npayer {AGENT}\npayee {SERVICE}\namount 0.25 *USDZ\n"
              f"network eip155:63825\ntransaction {TX}\nexplorer https://testnet.metalntwx.com/tx/{TX}\n"
              f"body {json.dumps(CHECK)}\n")
    cli = FakeCli((0, pagado, ""))
    ya_pagada = FakeResponse(200, CHECK, {"x-check-status": "already_paid"})
    http = FakeHttp([offer(pay_to=SERVICE, units="250000000000000000"), ya_pagada])
    rail = MetalRail(key_file, cli=["node", "metal-agent.js"], run=cli, http=http, sleep=lambda s: None)
    libro = Ledger(tmp_path / "libro_metal.json")
    primera = m.comprobar_real(RONDA, factura_nueva(), "Q. Consulting", rail=rail, libro=libro, base="http://svc")
    url = cli.calls[0]["argv"][cli.calls[0]["argv"].index("pay") + 1]
    assert url == f"http://svc/checks/{RONDA}-inv-205?account=LT121000011101001000&name=Q.%20Consulting"
    assert (primera["result"], primera["transaction"], primera["mode"]) == ("no_match", TX, "real")
    [entrada] = libro.attempts(f"check:{RONDA}:inv-205")
    assert (entrada.status, entrada.amount, entrada.currency) == ("PAID", 0.25, "*USDZ") and libro.verify().ok
    otra_vez = m.comprobar_real(RONDA, factura_nueva(), "Q. Consulting", rail=rail, libro=libro, base="http://svc")
    assert otra_vez["result"] == "no_match" and len(cli.calls) == 1  # the stored result, for free


def test_a_check_for_another_account_or_name_is_not_accepted(tmp_path, key_file):
    otro = {**CHECK, "account": "LT990000000000000000"}
    http = FakeHttp([offer(pay_to=SERVICE, units="250000000000000000"), FakeResponse(200, otro)],
                    settlements=[[settlement(payer=AGENT)]])
    rail = MetalRail(key_file, cli=["node", "metal-agent.js"], run=FakeCli(
        (0, f"status 200\npayer {AGENT}\npayee {SERVICE}\namount 0.25 *USDZ\ntransaction {TX}\n"
            f"body {json.dumps(otro)}\n", "")), http=http, sleep=lambda s: None)
    with pytest.raises(MetalError):
        m.comprobar_real(RONDA, factura_nueva(), "Q. Consulting", rail=rail, libro=Ledger(tmp_path / "l.json"),
                         base="http://svc")


def test_a_bad_file_of_checks_never_breaks_the_screen(tmp_path):
    (tmp_path / "comprobaciones.json").write_text("{not json", encoding="utf-8")
    assert m.comprobaciones_de(RONDA, tmp_path) == {}


def test_a_stored_check_that_makes_no_sense_shows_as_unavailable():
    for raro in ({}, {"mode": "real", "result": "maybe"}, {"mode": "replay"}):
        assert "unavailable" in m.texto_tarjeta(raro)
    otro_enlace = {"mode": "real", "result": "no_match", "payee": "Q. Consulting", "price": "0.25",
                   "explorer": "https://example.com/tx/1"}
    assert "view on Metal explorer" not in m.texto_tarjeta(otro_enlace)  # only links to Metal's explorer
