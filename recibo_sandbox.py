"""Recibo de una ronda de la demo en el sandbox de Airwallex: cada movimiento con su id de Airwallex, vuelto a pedir.

Lee del libro los movimientos de la ronda, vuelve a preguntar a Airwallex el estado de cada conversión y
transferencia (solo lectura) y escribe `evidencia/recibo_<ronda>.md`, en inglés (es para el jurado). Así se ve que
la demo movió dinero de verdad en el sandbox, con ids que Airwallex puede confirmar. Solo con el sandbox de verdad.

Uso:  python recibo_sandbox.py [--ronda R]
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

from escenario import DATA, ronda_actual
from modo import SIMULADO, conectar
from rail_airwallex import AirwallexError
from triage_core.ledger import Ledger

EVIDENCIA = Path(__file__).with_name("evidencia")
QUE = {"fx": "FX conversion", "pay": "Supplier payment", "deposit": "Customer deposit (sandbox simulation)",
       "sweep": "Opening-balance sweep", "match": "Deposit matched to the customer's invoice"}


def fila(rail, e) -> str:
    tipo = e.op_key.split(":", 1)[0]
    if tipo == "control":  # una decisión del dueño (10-nov): pausa, reanudación o rechazo; no pasa por Airwallex
        objeto = e.op_key.split(":")[-1] if e.kind == "reject" else ""
        importe = f"{e.amount:,.2f} {e.currency}" if e.currency else "—"
        return (f"| Owner {e.kind} {objeto} | {importe} | {e.counterparty} | `—` | `{e.request_id}` | {e.status} | — | "
                "in the hash-chained audit trail, not an Airwallex call |")
    ahora, detalle = "—", ""
    try:
        if tipo == "fx" and e.provider_id:
            c = rail.get_conversion(e.provider_id)
            ahora = c.get("status", "?")
            detalle = f"sold {float(c['sell_amount']):,.2f} {c['sell_currency']} at {c.get('client_rate')}"
        elif tipo in ("pay", "sweep") and e.provider_id:
            t = rail.get_transfer(e.provider_id)
            ahora = t.get("status", "?")
            detalle = f"{t.get('transfer_method', '')} fee {t.get('fee_amount', '?')} {t.get('fee_currency', '')}".strip()
    except AirwallexError as err:
        ahora = f"lookup failed ({err.status})"
    objeto = e.op_key.split(":")[-1] if tipo != "fx" else e.currency
    return (f"| {QUE.get(tipo, tipo)} {objeto} | {e.amount:,.2f} {e.currency} | {e.counterparty} | "
            f"`{e.provider_id or '—'}` | `{e.request_id}` | {e.status} | {ahora} | {detalle} |")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ronda", default=ronda_actual())
    args = ap.parse_args()
    if SIMULADO:
        print("Receipts are for the real Airwallex sandbox: add the sandbox keys to .env")
        return 1
    rail, libro = conectar(), Ledger(DATA / "ledger.json")
    entradas = [e for e in libro.entries if f":{args.ronda}:" in e.op_key]
    if not entradas:
        print(f"Nothing moved in round {args.ronda}")
        return 1
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lineas = [f"# Airwallex sandbox receipt: demo round `{args.ronda}`", "",
              f"Every money movement of this demo round, from the agent's ledger, with the id Airwallex gave it, "
              f"and the owner's decisions (pause, reject), which live in the same ledger. "
              f"*Ledger status* is what our ledger recorded; *Airwallex now* is what Airwallex answers when asked "
              f"again (read-only, on {ahora}, by `recibo_sandbox.py`). Failed attempts stay in the ledger: a retry "
              "reuses the request_id, and a new attempt after a confirmed failure gets a new one.", "",
              "| Operation | Amount | Counterparty | Airwallex id | request_id | Ledger status | Airwallex now | Detail |",
              "|---|---|---|---|---|---|---|---|"]
    lineas += [fila(rail, e) for e in entradas]
    chequeo = libro.verify()
    if chequeo.events or not chequeo.ok:
        lineas += ["", f"Audit trail: {'verified' if chequeo.ok else 'PROBLEM: ' + chequeo.problem} "
                       f"({chequeo.events} hash-chained events; check it with `python verificar_auditoria.py`)."]
    EVIDENCIA.mkdir(exist_ok=True)
    salida = EVIDENCIA / f"recibo_{args.ronda}.md"
    salida.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print(f"Written {salida.name}: {len(entradas)} movements")
    return 0


if __name__ == "__main__":
    sys.exit(main())
