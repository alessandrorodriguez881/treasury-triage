"""Ejecuta en el sandbox el plan del motor para el escenario de la demo (versión de consola de `panel.py`).

1. Lee los saldos reales de Airwallex y el tipo de cambio que nos cobraría ahora
2. El motor decide qué pagar, qué convertir, qué aplazar y qué pasa a una persona
3. Hace las conversiones que el agente puede hacer solo. Las que necesitan una persona salen como tarjeta
   de aprobación (`aprobaciones.py`) y se hacen en la siguiente ejecución, si están aprobadas
4. Paga lo que el plan financia (SCHEDULED -> SENT -> PAID), todo apuntado en el libro de movimientos

Con --leer-correo, antes de decidir, Claude lee las facturas de los proveedores y la bandeja (`agente_ia.py`):
registra lo que dice cada factura y cada correo de cliente (el código lo comprueba todo; una factura que no
cuadra con la lista de pagos o con la ficha del proveedor pasa a una persona) y explica el plan. Lo aceptado
se guarda para toda la ronda. Sin clave de Claude, repite su última lectura de verdad (`grabacion_claude.json`),
como la pantalla. --correo-globex hace lo mismo sin Claude, para ensayar sin clave. Después, la
cuenta de una factura que no está en ninguna ficha se comprueba en Metal (`demo.comprobar_beneficiarios`).

Con --deposito-globex, antes de ejecutar llega el pago de Globex (depósito simulado de 18.000 $): el motor
vuelve a calcular el plan y enseña solo las decisiones que cambian.

En el sandbox el tiempo no corre: lo que el plan financia se paga ya, aunque venza en 1-3 días.
Cada operación lleva una clave por ronda (la de `reiniciar_demo.py`, o --ronda): si se repite la misma
ronda, lo ya pagado no se vuelve a pagar. Los mensajes van en inglés (son del producto). La lógica está en
`demo.py`. Nunca imprime claves ni tokens.

Uso:  python ejecutar_plan.py [--solo-plan] [--leer-correo | --correo-globex] [--deposito-globex] [--ronda R]
"""
import argparse
import sys

from aprobaciones import APROBACIONES
from demo import (Situacion, comprobar_beneficiarios, conciliar, convertir, deposito_globex, esperar_conciliacion,
                  leer_correos, pagar, situacion)
from escenario import (BASE, DATA, DIVISAS, MODEL, facturas_de, guardar_facturas, guardar_senales, ronda_actual,
                       senales_de)
from modo import SIMULADO, conectar
from triage_core.approvals import ApprovalBook
from triage_core.ledger import Ledger
from triage_core.treasury_decision import diff_plans


def mostrar(s: Situacion) -> None:
    print("Balances: " + ", ".join(f"{a:,.2f} {d}" for d, a in s.saldos.items()))
    if s.pagadas:
        print("Already paid in this round: " + ", ".join(sorted(s.pagadas)))
    if s.enviadas:
        print("Sent to Airwallex but not PAID yet (finished on this run): " + ", ".join(sorted(s.enviadas)))
    print("Rates (USD per unit): " + ", ".join(f"{d} {r:.4f}" for d, r in s.rates.items() if d != BASE))
    print(f"Forecast confidence {s.plan.confidence:.0%} (usual {MODEL.pool_rate:.0%}): the agent may convert "
          f"up to {s.plan.fx_limit:,.0f} USD on its own. Risk of breaking the reserve: {s.plan.risk.p_breach:.1%}")
    for d in s.plan.decisions:
        print(f"   {d.obligation_id:7} {d.action:17} {d.reason}")
    if s.comprobadas:
        bien = [i for i, problemas in s.comprobadas.items() if not problemas]
        print(f"Invoices checked by code: {len(bien)} of {len(s.comprobadas)} match the payables list and the "
              "bank details on file" + "".join(f"; {i} does not" for i, p in s.comprobadas.items() if p))
    for c in s.plan.conversions:
        print(f"   Conversion: buy {c.buy_amount:,.2f} {c.buy_currency} (~{c.base_cost:,.2f} USD)"
              + ("  -> NEEDS APPROVAL" if c.needs_approval else ""))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--solo-plan", action="store_true", help="muestra el plan sin mover dinero")
    correo = p.add_mutually_exclusive_group()
    correo.add_argument("--leer-correo", action="store_true", help="Claude lee la bandeja antes de decidir")
    correo.add_argument("--correo-globex", action="store_true", help="ensayo sin Claude: solo el correo de Globex")
    p.add_argument("--deposito-globex", action="store_true", help="llega el pago de Globex y se recalcula")
    p.add_argument("--ronda", default=ronda_actual())
    args = p.parse_args()

    rail = conectar()
    DATA.mkdir(exist_ok=True)
    libro = Ledger(DATA / "ledger.json")
    book = ApprovalBook.load(APROBACIONES)
    print(f"Round: {args.ronda}" + (" (demo mode: simulated Airwallex sandbox, no keys)" if SIMULADO else ""))

    globex_pagado = "Globex" in conciliar(rail, libro, args.ronda, print)  # lo que ya llegó a la cuenta
    senales, facturas = senales_de(args.ronda), facturas_de(args.ronda)
    if args.leer_correo or args.correo_globex:
        if senales is not None:
            print("This round's emails were already read: using the saved signals")
        else:
            from agente_ia import SinClave
            try:
                lectura = leer_correos(rail, libro, args.ronda, globex_pagado, con_claude=args.leer_correo)
            except SinClave:  # sin clave de Claude: su última lectura de verdad, como en la pantalla
                lectura = leer_correos(rail, libro, args.ronda, globex_pagado, con_claude=False, repetir=True)
            print("Inbox:")
            for paso in lectura.pasos:
                print(f"   {paso}")
            if lectura.resumen:
                print("Claude's briefing for the owner:\n" + "\n".join(f"   {l}" for l in lectura.resumen.splitlines()))
            senales, facturas = lectura.senales, lectura.facturas
            guardar_facturas(args.ronda, facturas)
            guardar_senales(args.ronda, senales)
            comprobar_beneficiarios(args.ronda, facturas, lambda m: print(f"   {m}"))  # Metal: prueba, nunca para
    senales = senales or []
    s = situacion(rail, libro, args.ronda, senales, globex_pagado, facturas)
    if args.deposito_globex and not globex_pagado and not args.solo_plan:
        print("Plan before Globex pays:")
        mostrar(s)
        deposito_globex(rail, libro, args.ronda)
        if not esperar_conciliacion(rail, libro, args.ronda, "Globex"):
            raise RuntimeError("Globex's deposit does not match its invoice: a person reviews it")
        antes, s = s, situacion(rail, libro, args.ronda, senales, True, facturas)
        print("Re-planned:")
        mostrar(s)
        cambios = diff_plans(antes.plan, s.plan)
        print("What changed: " + ("; ".join(f"{i} {a} -> {b}" for i, a, b in cambios) if cambios else "nothing"))
    else:
        mostrar(s)
    if args.solo_plan:
        return 0

    pagar(rail, libro, s, args.ronda, convertir(rail, libro, book, s, args.ronda), book=book)

    ahora = rail.balances()
    print("Balances now: " + ", ".join(
        f"{float(ahora.get(d, {}).get('available_amount', 0)):,.2f} {d}" for d in DIVISAS))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")  # una consola de Windows sin UTF-8 no se para por un emoji
    try:
        sys.exit(main())
    except RuntimeError as e:  # p. ej. falta la clave de Claude: se para antes de mover dinero
        print(f"⛔ {e}")
        sys.exit(1)
