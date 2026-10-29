"""Baja los saldos del sandbox (que viene con 10 millones) a los de la demo.

Manda el sobrante de cada divisa a un beneficiario «Owner reserve» con transferencias de verdad
del sandbox, apuntadas en el libro de movimientos (no se pueden repetir) y llevadas hasta PAID.
Se puede ejecutar varias veces: si no sobra nada, no hace nada. Nunca imprime claves ni tokens.

Uso:  python ajustar_saldos_demo.py
"""
from pathlib import Path
import sys

from escenario import DATA, SALDOS_INICIALES, ronda_actual  # añade motor_comun al path
from modo import conectar
from rail_airwallex import AirwallexError, AirwallexRail
from triage_core.ledger import DuplicatePaymentError, Ledger

OBJETIVO = SALDOS_INICIALES
TITULAR = "Sandbox Business Owner Reserve"
# Faster Payments tiene un tope por transferencia; para vaciar 10 M de libras hace falta CHAPS
APODOS = {"GBP": "Owner reserve GBP (CHAPS)"}

# Datos bancarios de prueba (formatos públicos de ejemplo), solo para el sandbox
BENEFICIARIOS = {
    "USD": {
        "entity_type": "COMPANY",
        "company_name": TITULAR,
        "address": {"city": "Seattle", "country_code": "US", "postcode": "98104",
                    "state": "WA", "street_address": "412 5th Avenue"},
        "bank_details": {"account_currency": "USD", "account_name": TITULAR, "account_number": "50001121",
                         "account_routing_type1": "aba", "account_routing_value1": "021000021",
                         "bank_country_code": "US", "local_clearing_system": "ACH",
                         "bank_account_category": "Checking"},
    },
    "EUR": {  # SEPA a Alemania da «beneficiary_type_unsupported» en el sandbox; a Países Bajos sí
        "entity_type": "COMPANY",
        "company_name": TITULAR,
        "address": {"city": "Amsterdam", "country_code": "NL", "postcode": "1012 AB",
                    "street_address": "Damrak 1"},
        "bank_details": {"account_currency": "EUR", "account_name": TITULAR,
                         "iban": "NL91ABNA0417164300", "bank_country_code": "NL"},
    },
    "GBP": {
        "entity_type": "COMPANY",
        "company_name": TITULAR,
        "address": {"city": "London", "country_code": "GB", "postcode": "EC1A 1BB",
                    "street_address": "1 King Street"},
        "bank_details": {"account_currency": "GBP", "account_name": TITULAR, "account_number": "12345678",
                         "account_routing_type1": "sort_code", "account_routing_value1": "040004",
                         "bank_country_code": "GB", "local_clearing_system": "CHAPS"},
    },
}


def beneficiario(rail: AirwallexRail, divisa: str, existentes: list[dict]) -> str:
    apodo = APODOS.get(divisa, f"Owner reserve {divisa}")
    for b in existentes:
        if b.get("nickname") == apodo:
            return b["id"] if "id" in b else b["beneficiary_id"]
    creado = rail.create_beneficiary(BENEFICIARIOS[divisa], apodo)
    print(f"   Beneficiario {apodo} creado")
    return creado.get("id") or creado["beneficiary_id"]


def main(rail: AirwallexRail | None = None) -> int:
    rail = rail or conectar()
    libro = Ledger(DATA / "ledger.json")
    libro.path.parent.mkdir(exist_ok=True)
    existentes = rail.beneficiaries()

    saldos = rail.balances()
    for divisa, objetivo in OBJETIVO.items():
        sobra = round(float(saldos.get(divisa, {}).get("available_amount", 0)) - objetivo, 2)
        if sobra <= 0:
            print(f"{divisa}: nada que ajustar")
            continue
        print(f"{divisa}: sobran {sobra:,.2f}")
        try:
            entrada = libro.begin(f"sweep:{ronda_actual()}:{divisa}", "transfer", sobra, divisa, TITULAR)
        except DuplicatePaymentError:
            print("   Ya se hizo en esta ronda un ajuste pagado de esta divisa; no se repite")
            continue
        try:
            t = rail.create_transfer(entrada.request_id, beneficiario(rail, divisa, existentes), divisa, divisa,
                                     sobra, reference="Demo opening balance sweep",
                                     reason="transfer_to_own_account", fee_paid_by="BENEFICIARY")
        except AirwallexError as e:
            libro.update(entrada, "FAILED")
            print(f"   Error de Airwallex: {e}")
            continue
        libro.update(entrada, t.get("status", "SUBMITTED"), t.get("id"))
        print(f"   Transferencia creada: {t.get('status')}")
        for siguiente in ("SENT", "PAID"):
            try:
                t = rail.simulate_transfer_status(t["id"], siguiente)
                libro.update(entrada, t.get("status", siguiente))
                print(f"   -> {t.get('status', siguiente)}")
            except AirwallexError as e:
                print(f"   No se pudo pasar a {siguiente}: {e}")
                break

    print("Saldos ahora:")
    for divisa, b in sorted(rail.balances().items()):
        if divisa in OBJETIVO or float(b.get("available_amount", 0)):
            print(f"   {divisa}: disponible {b.get('available_amount')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
