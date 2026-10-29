"""Deja el sandbox con los saldos de partida de la demo. Se puede ejecutar varias veces.

- Abre una cuenta global en USD (EE. UU.) si no existe
- Simula depósitos solo por lo que falte hasta el saldo objetivo
Nunca imprime claves ni tokens.

Uso:  python preparar_sandbox.py
"""
from pathlib import Path
import sys

from escenario import SALDOS_INICIALES
from modo import conectar
from rail_airwallex import AirwallexError, AirwallexRail

# Saldos de partida del escenario de la demo (las libras empiezan en 0: no hace falta depositar)
OBJETIVO = {d: s for d, s in SALDOS_INICIALES.items() if s > 0}
CUENTAS = {"USD": ("US", "Treasury Triage USD"), "EUR": ("NL", "Treasury Triage EUR")}


def divisas(cuenta: dict) -> set[str]:
    return {f.get("currency") for f in cuenta.get("supported_features", [])} | {cuenta.get("currency")}


def saldo(b: dict) -> float:
    return float(b.get("available_amount", 0)) + float(b.get("pending_amount", 0))


def main(rail: AirwallexRail | None = None) -> int:
    rail = rail or conectar()
    cuentas = rail.global_accounts()

    por_divisa = {}
    for divisa, (pais, nombre) in CUENTAS.items():
        cuenta = next((c for c in cuentas if divisa in divisas(c)), None)
        if cuenta is None:
            cuenta = rail.create_global_account(divisa, pais, nombre)
            print(f"Cuenta {divisa} creada ({pais}): {cuenta.get('status')}")
        else:
            print(f"Cuenta {divisa} ya existía ({cuenta.get('country_code')}): {cuenta.get('status')}")
        por_divisa[divisa] = cuenta

    saldos = rail.balances()
    for divisa, objetivo in OBJETIVO.items():
        falta = round(objetivo - saldo(saldos.get(divisa, {})), 2)
        if falta <= 0:
            print(f"{divisa}: ya hay {saldo(saldos[divisa]):,.2f}; no hace falta depósito")
            continue
        dep = rail.simulate_deposit(por_divisa[divisa]["id"], falta, reference=f"demo-opening-{divisa}")
        print(f"{divisa}: depósito simulado de {falta:,.2f} -> {dep.get('status')}")

    print("Saldos ahora:")
    for divisa, b in sorted(rail.balances().items()):
        if divisa in OBJETIVO or saldo(b):
            print(f"   {divisa}: disponible {b.get('available_amount')}, pendiente {b.get('pending_amount')}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AirwallexError as e:
        print(f"Error de Airwallex: {e}")
        sys.exit(1)
