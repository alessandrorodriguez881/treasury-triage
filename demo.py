"""Los pasos de la demo de Treasury Triage, compartidos por la consola (`ejecutar_plan.py`) y la pantalla (`panel.py`).

Cada paso que mueve dinero recibe `log`, una función que recibe cada mensaje (en inglés: lo ve el jurado).
La consola usa print; la pantalla guarda los mensajes y los enseña. Nunca se escriben claves ni tokens.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from typing import Callable

from aprobaciones import (APROBADA, RECHAZADA, aprobacion_de_conversion, aprobacion_de_reserva, cerrar_sobrantes,
                          cotizacion_aprobada, rechazo_de_una_persona)
import comprobacion_metal
from escenario import (BASE, COBROS, DIVISAS, EN_FICHA, MODEL, OBLIGATIONS, PAGO_GLOBEX, PROVEEDORES, SENAL_GLOBEX,
                       facturas_ensayo, facturas_proveedor, fecha_ronda, receipts)
from preparar_sandbox import divisas
from rail_airwallex import AirwallexError, AirwallexRail
from triage_core.approvals import ApprovalBook
from triage_core.invoices import apply_invoice_checks
from triage_core.ledger import IN_FLIGHT, SUCCESS, DuplicatePaymentError, Ledger
from triage_core.policy import TreasuryPolicy
from triage_core.treasury_decision import CONVERT_AND_FUND, FUND, Plan, plan_treasury

Log = Callable[[str], None]
ENV = Path(__file__).with_name(".env")
# La última lectura de Claude de verdad: sin clave (p. ej. en la demo pública), la pantalla repite sus llamadas
GRABACION = Path(__file__).with_name("grabacion_claude.json")
GRABAR = os.environ.get("TT_GRABAR") == "1"  # la grabación del repositorio solo se cambia a propósito
NO_CUADRA = ("Not shown: Claude wrote this briefing for the numbers at the start of a round, and they have "
             "changed here (for example, a customer has already paid). Everything else on this screen is "
             "recomputed now by code.")


@dataclass
class Situacion:
    saldos: dict[str, float]
    rates: dict[str, float]
    pagadas: set[str]        # PAID en Airwallex
    pendientes: list
    senales: tuple[str, ...]
    plan: Plan
    enviadas: set[str] = field(default_factory=set)  # creadas en Airwallex pero aún no PAID: el dinero ya está comprometido
    obligaciones: list = field(default_factory=list)  # la lista de pagos, con lo que digan las facturas comprobadas
    comprobadas: dict[str, list[str]] = field(default_factory=dict)  # factura leída -> discrepancias ([] = cuadra)


@dataclass
class Lectura:
    """Lo que salió de leer la bandeja: las señales y facturas aceptadas, los pasos del agente y su resumen."""
    senales: list
    pasos: list[str] = field(default_factory=list)
    resumen: str = ""
    facturas: list = field(default_factory=list)
    plan: dict | None = None  # el último plan que vio Claude: sobre él escribió su resumen


def tipos(rail: AirwallexRail) -> dict[str, float]:
    """USD por unidad de cada divisa, al tipo que nos cobraría Airwallex (nivel CLIENT)."""
    out = {BASE: 1.0}
    for divisa in DIVISAS:
        if divisa != BASE:
            r = rail.current_rate(divisa, BASE, 1000)
            client = next(d for d in r["rate_details"] if d["level"] == "CLIENT")
            out[divisa] = client["sell_amount"] / client["buy_amount"]
    return out


def ya_hecho(libro: Ledger, clave: str) -> bool:
    return any(e.status in SUCCESS for e in libro.attempts(clave))


def en_camino(libro: Ledger, clave: str):
    """El último intento si ya se mandó a Airwallex y aún no ha acabado (ni PAID ni fallado), o None."""
    intentos = libro.attempts(clave)
    ultimo = intentos[-1] if intentos else None
    return ultimo if ultimo and ultimo.provider_id and ultimo.status in IN_FLIGHT else None


def situacion(rail: AirwallexRail, libro: Ledger, ronda: str, senales_correo: list,
              globex_pagado: bool, facturas: list = ()) -> Situacion:
    actuales = rail.balances()
    saldos = {d: float(actuales.get(d, {}).get("available_amount", 0)) for d in DIVISAS}
    rates = tipos(rail)
    # Una factura que no cuadra con la lista de pagos o con la ficha del proveedor pasa a una persona
    obligaciones, comprobadas = apply_invoice_checks(OBLIGATIONS, list(facturas), fecha_ronda(ronda), EN_FICHA)
    # Lo ya pagado, o ya mandado a Airwallex, sale del plan: su dinero ya no está libre en el saldo
    pagadas = {o.id for o in obligaciones if ya_hecho(libro, f"pay:{ronda}:{o.id}")}
    enviadas = {o.id for o in obligaciones if en_camino(libro, f"pay:{ronda}:{o.id}")}
    pendientes = [o for o in obligaciones if o.id not in pagadas | enviadas]
    recs = receipts(senales_correo, globex_pagado)
    senales = tuple(a for _, perfil in recs for a in perfil.adjustments)
    # La autonomía depende de cuánto peor que lo normal en esta cartera es la previsión de esta semana
    plan = plan_treasury(pendientes, saldos, rates, recs, TreasuryPolicy(), base=BASE, usual_confidence=MODEL.pool_rate)
    return Situacion(saldos, rates, pagadas, pendientes, senales, plan, enviadas, obligaciones, comprobadas)


def paso_siguiente(libro: Ledger, ronda: str, senales_correo: list | None, facturas: list, globex_pagado: bool,
                   por_aprobar: int) -> str | None:
    """Lo que toca ahora en la demo: «2» (leer la bandeja), «aprobar», «3», «4» (cobra Globex), «3 otra vez», o None
    cuando ya está todo. Sale del libro de la ronda, sin preguntar a Airwallex: la pantalla lo necesita antes de pedir
    los saldos, para resaltar el botón que toca."""
    if senales_correo is None:
        return "2"
    if por_aprobar:
        return "aprobar"
    obligaciones, _ = apply_invoice_checks(OBLIGATIONS, list(facturas), fecha_ronda(ronda), EN_FICHA)
    pagadas = {o.id for o in obligaciones if ya_hecho(libro, f"pay:{ronda}:{o.id}")}
    if not pagadas:
        return "3"
    if not globex_pagado:
        return "4"
    return "3 otra vez" if any(o.id not in pagadas for o in obligaciones if not o.policy_exception) else None


def huella(plan: dict | None) -> tuple | None:
    """Lo que cuenta el resumen de Claude y no se mueve con el tipo de cambio del momento: la confianza, lo pagado,
    las decisiones y las divisas que hay que comprar (y si piden permiso). Si cambia, su resumen ya no está al día."""
    if plan is None:
        return None
    return (plan["confidence"], tuple(plan["already_paid"]), tuple((d["id"], d["action"]) for d in plan["decisions"]),
            tuple((c["buy"], c["needs_owner_approval"]) for c in plan["conversions"]))


def resumen(s: Situacion) -> dict:
    """Lo que ve Claude del plan: decisiones y cifras del motor, sin claves ni identificadores de Airwallex."""
    por_id = {o.id: o for o in s.pendientes}
    # Importes ya escritos: así Claude los copia tal cual en el resumen (sin esto escribía «12000 USD»)
    importe = lambda cantidad, divisa, decimales=2: f"{cantidad:,.{decimales}f} {divisa}"
    return {
        "balances": {d: importe(a, d) for d, a in s.saldos.items()},
        "already_paid": sorted(s.pagadas),
        "confidence": f"{s.plan.confidence:.0%}",
        "usual_confidence": f"{MODEL.pool_rate:.0%}",
        "agent_fx_limit": importe(s.plan.fx_limit, BASE, 0),
        "reserve_floor": importe(TreasuryPolicy().reserve_floor, BASE, 0),
        "risk_of_breaking_reserve": f"{s.plan.risk.p_breach:.1%}",
        "decisions": [{"id": d.obligation_id, "payee": por_id[d.obligation_id].payee,
                       "amount": importe(por_id[d.obligation_id].amount, por_id[d.obligation_id].currency),
                       "action": d.action, "reason": d.reason} for d in s.plan.decisions],
        "conversions": [{"buy": importe(c.buy_amount, c.buy_currency), "approx_cost": importe(c.base_cost, BASE),
                         "needs_owner_approval": c.needs_approval}
                        for c in s.plan.conversions],
        "invoice_checks": {o.id: ("matches the payables list and the bank details on file"
                                  if not s.comprobadas[o.id] else s.comprobadas[o.id])
                           if o.id in s.comprobadas else "invoice not read" for o in s.obligaciones},
    }


def guardar_grabacion(hoy: date, lectura: Lectura, plan: dict | None) -> None:
    """Guarda lo que registró Claude (con los vencimientos en días desde la ronda), el último plan que vio y su
    resumen para el dueño."""
    facturas = []
    for f in lectura.facturas:
        try:
            d = asdict(f)
            d["due_in_days"] = (date.fromisoformat(d.pop("due_date")) - hoy).days
            facturas.append(d)
        except ValueError:  # una fecha que no es ISO no se puede mover a otro día: esa lectura no se graba
            continue
    GRABACION.write_text(json.dumps({"date": hoy.isoformat(), "invoices": facturas,
                                     "signals": [asdict(s) for s in lectura.senales], "plan": plan,
                                     "briefing": lectura.resumen},
                                    indent=2, ensure_ascii=False), encoding="utf-8")


def hay_grabacion() -> bool:
    return GRABACION.exists()


def repetir_grabacion(hoy: date, recalcular: Callable[[list, list], dict]) -> Lectura:
    """Repite las llamadas de la última lectura de Claude de verdad. El código las vuelve a comprobar todas, igual
    que en directo (con los vencimientos movidos al día de esta ronda). El resumen es el que escribió Claude, solo
    si el plan de ahora es el mismo que vio él: si no, sus cifras no serían las de la pantalla."""
    from agente_ia import Bandeja
    g = json.loads(GRABACION.read_text(encoding="utf-8"))
    bandeja = Bandeja(recalcular, facturas=facturas_proveedor(hoy), hoy=hoy)
    for f in g["invoices"]:
        bandeja.record_invoice(f["document_id"], f["obligation_id"], f["amount"], f["currency"],
                               (hoy + timedelta(days=f["due_in_days"])).isoformat(), f["pay_to"])
    for s in g["signals"]:
        bandeja.record_signal(s["message_id"], s["customer"], s["signal"], s["quote"])
    bandeja.recompute_plan()
    return Lectura(bandeja.senales, [f"Replay of a real Claude run on {g['date']} (no Claude key here): the same "
                                     "tool calls, checked again by code"] + bandeja.registro,
                   g["briefing"] if bandeja.plan == g.get("plan") else NO_CUADRA, bandeja.facturas, bandeja.plan)


def leer_correos(rail: AirwallexRail, libro: Ledger, ronda: str, globex_pagado: bool, con_claude: bool,
                 repetir: bool = False) -> Lectura:
    """Lee las facturas y la bandeja (una vez por ronda; quien llama guarda lo aceptado).

    Sin Claude: con `repetir`, la última lectura de Claude de verdad; si no, lo que registraría Claude (ensayo).
    Las dos pasan por las mismas comprobaciones del código."""
    hoy = fecha_ronda(ronda)
    plan_de = lambda senales, facturas: resumen(situacion(rail, libro, ronda, senales, globex_pagado, facturas))
    if not con_claude and repetir and hay_grabacion():
        return repetir_grabacion(hoy, plan_de)
    if not con_claude:
        from agente_ia import Bandeja
        bandeja = Bandeja(lambda senales, facturas: {}, facturas=facturas_proveedor(hoy), hoy=hoy)
        for f in facturas_ensayo(hoy):
            bandeja.record_invoice(f.document_id, f.obligation_id, f.amount, f.currency, f.due_date, f.pay_to)
        bandeja.record_signal(SENAL_GLOBEX.message_id, SENAL_GLOBEX.customer, SENAL_GLOBEX.signal,
                              SENAL_GLOBEX.quote)
        return Lectura(bandeja.senales, ["Rehearsal without Claude: the readings Claude would record"]
                       + bandeja.registro, facturas=bandeja.facturas)
    import anthropic
    from agente_ia import Bandeja, cliente, leer_bandeja  # solo hace falta el paquete anthropic aquí
    bandeja = Bandeja(plan_de, facturas=facturas_proveedor(hoy), hoy=hoy)
    try:
        explicacion = leer_bandeja(cliente(ENV), bandeja)
    except anthropic.APIError as e:
        raise RuntimeError(f"Claude could not read the inbox ({type(e).__name__}). No money was moved") from e
    lectura = Lectura(bandeja.senales, bandeja.registro, explicacion, bandeja.facturas, bandeja.plan)
    if GRABAR:
        guardar_grabacion(hoy, lectura, bandeja.plan)
    return lectura


def comprobar_beneficiarios(ronda: str, facturas, log: Log = print, carpeta=None) -> list[dict]:
    """Paso 2, después de leer las facturas: cada factura que pide pagar a una cuenta que no está en la ficha de su
    proveedor (en la demo, la de Q. Consulting) se comprueba en Metal, como la «Verification of Payee» de la UE.
    El agente paga 0,25 *USDZ por x402 y el resultado queda como prueba para la persona que decide
    (`comprobacion_metal.py`). No cambia ninguna decisión ni ninguna cifra del plan.

    Nunca para la demo: si Metal, el servicio local o la red fallan, queda una nota «unavailable» y se sigue.
    Devuelve las comprobaciones de esta ronda (también se guardan por ronda en `carpeta`, por defecto la de datos)."""
    m = comprobacion_metal
    modo = m.modo_metal()
    if modo == "off":
        return []
    hechas = m.comprobaciones_de(ronda, carpeta)
    resultados = []
    for f, payee in m.por_comprobar(facturas):
        r = hechas.get(f.obligation_id)
        if r is None or r.get("mode") not in ("real", "replay"):
            try:
                r = m.comprobar_real(ronda, f, payee) if modo == "real" else m.comprobar_grabada(f, payee)
                if r["mode"] == "real" and m.GRABAR_METAL:
                    m.grabar(r)
            except Exception as e:  # Metal, el servicio local o la red: una nota, nunca un error en pantalla
                motivo = str(e) if isinstance(e, m.MetalError) else type(e).__name__
                r = {**m.resultado_base(f, payee, "unavailable"), "note": motivo[:160]}
        resultados.append(r)
        extra = (f" ({m.REPLAY} of {r['recorded_on']})" if r["mode"] == "replay" else
                 f" · transaction {r['transaction']}" if r["mode"] == "real" and r.get("transaction") else "")
        log(f"{payee}'s invoice ({f.document_id}): {m.texto(r)}{extra}")
    if resultados:
        m.guardar_comprobaciones(ronda, resultados, carpeta)
    return resultados


def disponible(rail: AirwallexRail, divisa: str) -> float:
    return float(rail.balances().get(divisa, {}).get("available_amount", 0))


def esperar_saldo(rail: AirwallexRail, divisa: str, minimo: float, log: Log = print, espera=time.sleep,
                  intentos: int = 30) -> bool:
    """Espera a que el saldo disponible llegue a `minimo`. Devuelve False si no llega en ~1 minuto.

    El sandbox tarda unos segundos en sumar al saldo lo convertido o depositado (visto el 27-oct: un pago
    lanzado un segundo después de su conversión falló por saldo insuficiente, y un plan recalculado justo
    después del depósito de Globex no lo veía)."""
    for intento in range(intentos):
        if disponible(rail, divisa) >= minimo - 0.005:
            return True
        if intento == 0:
            log(f"   waiting for Airwallex to credit the {divisa} balance...")
        espera(2)
    return False


def deposito_globex(rail: AirwallexRail, libro: Ledger, ronda: str, log: Log = print, espera=time.sleep) -> bool:
    """Simula que Globex paga su factura. Una sola vez por ronda (un depósito simulado no se reintenta).

    Vuelve cuando el dinero ya está en el saldo, para que el plan recalculado lo vea."""
    try:
        entrada = libro.begin(f"deposit:{ronda}:Globex", "deposit", PAGO_GLOBEX, BASE, "Globex")
    except DuplicatePaymentError:
        log("Globex's payment already arrived in this round")
        return False
    cuenta = next(c for c in rail.global_accounts() if BASE in divisas(c))
    antes = disponible(rail, BASE)
    try:
        dep = rail.simulate_deposit(cuenta["id"], PAGO_GLOBEX, reference=f"Invoice {COBROS['Globex'][0]}",
                                    payer_name="Globex Corp")
    except AirwallexError:
        libro.update(entrada, "FAILED")
        raise
    libro.update(entrada, "SETTLED", dep.get("id"))
    if not esperar_saldo(rail, BASE, antes + PAGO_GLOBEX, log, espera):
        raise RuntimeError("Globex's deposit is not in the USD balance yet. Wait a minute and reload the plan")
    log(f"Globex paid {PAGO_GLOBEX:,.2f} {BASE} into the USD global account")
    return True


def inicio_ronda(ronda: str) -> str:
    """Cuándo empezó la ronda, en UTC: los depósitos de antes son de otras rondas."""
    try:
        local = datetime.strptime(ronda, "r%Y%m%d-%H%M%S")
    except ValueError:
        local = datetime.combine(fecha_ronda(ronda), datetime.min.time())
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def ya_conciliados(libro: Ledger, ronda: str) -> dict[str, str]:
    """Cliente -> id del depósito de Airwallex con el que se concilió su factura en esta ronda."""
    return {e.op_key.split(":")[2]: e.provider_id for e in libro.entries
            if e.op_key.startswith(f"match:{ronda}:") and e.status in SUCCESS}


def conciliar(rail: AirwallexRail, libro: Ledger, ronda: str, log: Log | None = None) -> dict[str, str]:
    """Cruza los depósitos que han llegado a Airwallex en esta ronda con lo que nos deben (`COBROS`).

    Un depósito concilia una factura si tiene el mismo importe y divisa, y el nombre del cliente está en el pagador
    o el número de la factura en la referencia. Cada depósito cuenta una sola vez y el cruce queda en el libro.
    Así el agente no da un cobro por hecho porque alguien lo diga: lo ve en la cuenta. Devuelve cliente -> depósito."""
    hechos = ya_conciliados(libro, ronda)
    if len(hechos) == len(COBROS):
        return hechos
    usados = {e.provider_id for e in libro.entries if e.op_key.startswith("match:") and e.status in SUCCESS}
    for d in rail.deposits(inicio_ronda(ronda)):
        if d["id"] in usados or d.get("status") != "SETTLED" or d.get("currency") != BASE:
            continue
        pagador, ref = (d.get("payer") or {}).get("name") or "", d.get("reference") or ""
        for cliente, (factura, importe) in COBROS.items():
            if cliente in hechos or abs(float(d["amount"]) - importe) > 0.005:
                continue
            if cliente.lower() in pagador.lower() or factura.lower() in ref.lower():
                entrada = libro.begin(f"match:{ronda}:{cliente}", "receipt_match", importe, BASE, cliente)
                libro.update(entrada, "COMPLETED", d["id"])
                hechos[cliente] = d["id"]
                usados.add(d["id"])
                if log:
                    log(f"Airwallex deposit {d['id'][:8]}: {float(d['amount']):,.2f} {BASE} from "
                        f"{pagador or 'an unknown payer'} (ref '{ref}') matched to {cliente}'s invoice {factura}")
                break
    return hechos


def esperar_conciliacion(rail: AirwallexRail, libro: Ledger, ronda: str, cliente: str, log: Log = print,
                         espera=time.sleep, intentos: int = 30) -> str | None:
    """Espera a ver el cobro de `cliente` conciliado. El sandbox tarda unos segundos en dar el depósito por SETTLED
    en su lista, aunque el dinero ya esté en el saldo (visto el 29-oct). Devuelve el id del depósito, o None."""
    for intento in range(intentos):
        deposito = conciliar(rail, libro, ronda, log).get(cliente)
        if deposito:
            return deposito
        if intento == 0:
            log(f"   waiting for Airwallex to list {cliente}'s deposit as settled...")
        espera(2)
    return None


def beneficiario(rail: AirwallexRail, existentes: list[dict], apodo: str, log: Log = print) -> str:
    for b in existentes:
        if b.get("nickname") == apodo:
            return b.get("id") or b["beneficiary_id"]
    metodos, datos = PROVEEDORES[apodo]
    creado = rail.create_beneficiary(datos, apodo, metodos)
    log(f"Beneficiary {apodo} created")
    existentes.append({"nickname": apodo, **creado})
    return creado.get("id") or creado["beneficiary_id"]


def motivo_aprobacion(s: Situacion) -> str:
    return (f"forecast confidence {s.plan.confidence:.0%}, below the usual {MODEL.pool_rate:.0%}: "
            f"the agent may only convert {s.plan.fx_limit:,.0f} {BASE} on its own")


def convertir(rail: AirwallexRail, libro: Ledger, book: ApprovalBook, s: Situacion, ronda: str,
              log: Log = print, espera=time.sleep) -> set[str]:
    """Hace las conversiones del plan. Devuelve las divisas cuyo pago espera a una conversión que no se hizo.

    Después de cada conversión espera a ver la divisa comprada en el saldo, antes de pagar con ella."""
    libro.require_intact()  # con el libro o su registro tocados a mano, no se mueve dinero
    paradas = set()
    for c in s.plan.conversions:
        # El importe va en la clave: repetir el mismo plan no convierte dos veces, pero si un pago falló
        # y el plan pide comprar lo que falta, eso es otra operación
        clave = f"fx:{ronda}:{c.buy_currency}:{c.buy_amount:.2f}"
        if ya_hecho(libro, clave):
            log(f"{c.buy_currency} conversion already done in this round: not repeated")
            continue
        # Si una persona ya aprobó esta misma conversión, se hace al coste que aprobó, aunque ahora
        # el agente pudiera hacerla solo (p. ej. tras el pago de Globex sube la confianza)
        quote_id = cotizacion_aprobada(rail, book, c.buy_currency, c.buy_amount, ronda)
        rechazo = None if quote_id else rechazo_de_una_persona(book, c.buy_currency, ronda)
        if rechazo:  # el «no» de una persona vale para toda la ronda, aunque ahora el agente pudiera hacerla solo
            log(f"{c.buy_currency} conversion rejected by a person in this round [{rechazo[:8]}]: not done")
            paradas.add(c.buy_currency)
            continue
        if not quote_id and c.needs_approval:
            estado, ref = aprobacion_de_conversion(rail, book, c.sell_currency, c.buy_currency, c.buy_amount,
                                                   motivo_aprobacion(s), ronda, s.senales, log=log)
            if estado != APROBADA:
                log(f"{c.buy_currency} conversion "
                    + ("rejected by a person: not done" if estado == RECHAZADA else f"waits for approval [{ref[:8]}]"))
                paradas.add(c.buy_currency)
                continue
            quote_id = ref
        if quote_id:
            log(f"{c.buy_currency} conversion approved: booked at the approved quote")
        try:
            entrada = libro.begin(clave, "conversion", c.buy_amount, c.buy_currency, "Airwallex FX")
        except DuplicatePaymentError:
            log(f"{c.buy_currency} conversion already done in this round: not repeated")
            continue
        try:
            conv = rail.create_conversion(entrada.request_id, c.sell_currency, c.buy_currency, c.buy_amount,
                                          quote_id=quote_id)
        except AirwallexError as e:
            libro.update(entrada, "FAILED")
            log(f"{c.buy_currency} conversion failed at Airwallex: {e}")
            paradas.add(c.buy_currency)
            continue
        libro.update(entrada, conv.get("status", "SUBMITTED"), conv.get("conversion_id") or conv.get("id"))
        log(f"Converted: bought {conv.get('buy_amount')} {c.buy_currency} for {conv.get('sell_amount')} USD "
            f"at {conv.get('client_rate')} ({conv.get('status')})")
        for fp in cerrar_sobrantes(book, c.buy_currency, ronda):
            log(f"   approval card [{fp[:8]}] closed: no longer needed")
        if not esperar_saldo(rail, c.buy_currency, s.saldos.get(c.buy_currency, 0) + c.buy_amount, log, espera):
            log(f"{c.buy_currency} is not in the balance yet: its payments wait for the next run")
            paradas.add(c.buy_currency)
    return paradas


ORDEN = {"CREATED": 0, "SUBMITTED": 0, "SCHEDULED": 1, "PROCESSING": 2, "SENT": 3, "PAID": 4}


def avanzar(rail: AirwallexRail, libro: Ledger, entrada, log: Log = print, espera=time.sleep) -> str:
    """Lleva una transferencia del sandbox hasta PAID y devuelve su estado final.

    La simulación del sandbox a veces responde 500 (visto el 27-oct). Antes de cada paso se mira el estado
    real, y un paso que falla se reintenta una vez: mover el estado simulado no crea ni mueve dinero."""
    estado = rail.get_transfer(entrada.provider_id).get("status", entrada.status)
    for siguiente in ("SENT", "PAID"):
        for intento in (1, 2):
            if ORDEN.get(estado, -1) >= ORDEN[siguiente]:
                break
            try:
                estado = rail.simulate_transfer_status(entrada.provider_id, siguiente).get("status", siguiente)
            except AirwallexError as e:
                estado = rail.get_transfer(entrada.provider_id).get("status", estado)
                if intento == 2 and ORDEN.get(estado, -1) < ORDEN[siguiente]:
                    log(f"   the sandbox could not move it to {siguiente} ({e.code}); press Execute again to finish")
                espera(1)
        if ORDEN.get(estado, -1) < ORDEN[siguiente]:
            break
    libro.update(entrada, estado)
    return estado


def motivo_reserva(s: Situacion, ob) -> str:
    floor = TreasuryPolicy().reserve_floor
    return (f"keeping the {floor:,.0f} {BASE} reserve would mean deferring {ob.id}, and without it the business "
            f"stops. Paying it leaves a {s.plan.risk.p_breach:.0%} chance of dipping below the reserve in the next "
            f"72 h, never below zero")


def pagar(rail: AirwallexRail, libro: Ledger, s: Situacion, ronda: str, paradas: set[str],
          log: Log = print, book: ApprovalBook | None = None) -> None:
    libro.require_intact()
    # Primero, terminar lo que se quedó a medias en Airwallex (sin crear transferencias nuevas)
    for oid in sorted(s.enviadas):
        entrada = en_camino(libro, f"pay:{ronda}:{oid}")
        estado = avanzar(rail, libro, entrada, log)
        log(f"{oid}: finished the transfer that was waiting at Airwallex ({estado})")
    existentes = rail.beneficiaries()
    for d in s.plan.decisions:
        if d.action not in (FUND, CONVERT_AND_FUND):
            continue
        ob = next(o for o in s.pendientes if o.id == d.obligation_id)
        if ob.currency in paradas:
            log(f"{ob.id} ({ob.payee}) waits for the {ob.currency} conversion")
            continue
        if s.plan.uses_reserve and ob.stops_operations:  # sale de la reserva: nunca sin una persona
            estado, fp = (aprobacion_de_reserva(book, ob.payee, ob.amount, ob.currency, motivo_reserva(s, ob), ronda,
                                                s.senales, log=log) if book else (RECHAZADA, ""))
            if estado != APROBADA:
                log(f"{ob.id} would be paid from the reserve: "
                    + ("not paid (rejected, or no approval book)" if estado == RECHAZADA else f"waits for approval [{fp[:8]}]"))
                continue
            log(f"{ob.id}: paying from the reserve, approved by a person")
        try:
            entrada = libro.begin(f"pay:{ronda}:{ob.id}", "transfer", ob.amount, ob.currency, ob.payee)
        except DuplicatePaymentError:
            log(f"{ob.id} already paid in this round: never paid twice")
            continue
        try:
            t = rail.create_transfer(entrada.request_id, beneficiario(rail, existentes, ob.payee, log), ob.currency,
                                     ob.currency, ob.amount, reference=f"Invoice {ob.id}",
                                     transfer_method=ob.transfer_method)
        except AirwallexError as e:
            libro.update(entrada, "FAILED")
            log(f"{ob.id} payment failed at Airwallex: {e}")
            continue
        libro.update(entrada, t.get("status", "SUBMITTED"), t.get("id"))
        estado = avanzar(rail, libro, entrada, log)
        if estado == "PAID":
            log(f"Paid {ob.id}: {ob.amount:,.2f} {ob.currency} to {ob.payee} by {ob.transfer_method} (PAID)")
        else:
            log(f"{ob.id}: {ob.amount:,.2f} {ob.currency} to {ob.payee} sent to Airwallex, still {estado}")
