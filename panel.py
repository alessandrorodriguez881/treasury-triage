"""Pantalla de la demo de Treasury Triage (Streamlit). En inglés: es lo que ve el jurado en el vídeo.

Los botones de la barra lateral siguen la historia de la demo; cada uno llama a los mismos pasos que la
consola (`demo.py`) y mueve dinero de verdad en el sandbox de Airwallex. Sin claves, corre contra el simulador
(`modo.py`), y sin clave de Claude repite la última lectura de Claude de verdad. Nunca enseña claves ni tokens.

Uso (desde la carpeta app/):  streamlit run panel.py
"""
import contextlib
import io
import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import ajustar_saldos_demo
import preparar_sandbox
from aprobaciones import APROBACIONES, CON_RESERVA, cotizacion, rechazo_de_una_persona, titulo
from comprobacion_metal import comprobaciones_de, precalentar, texto_tarjeta
from demo import (ENV, comprobar_beneficiarios, conciliar, convertir, deposito_globex, esperar_conciliacion,
                  hay_grabacion, huella, leer_correos, pagar, paso_siguiente, situacion, ya_conciliados, ya_hecho)
from demo import resumen as plan_para_claude
from escenario import (BANDEJA, BASE, COBROS, CONCEPTOS, DATA, DIVISAS, MODEL, PAGO_GLOBEX, facturas_de,
                       facturas_proveedor, fecha_ronda, guardar_facturas, guardar_senales, nueva_ronda, receipts,
                       ronda_actual, senales_de)
from modo import SIMULADO, conectar
from rail_airwallex import AirwallexError, AirwallexRail, read_env
from triage_core.approvals import ApprovalBook
from triage_core.cash_simulation import cash_bands
from triage_core.ledger import Ledger
from triage_core.policy import FEE_CUSHION, PAYOUT_FEES, SIGNAL_LIKELIHOOD, TreasuryPolicy
from triage_core.treasury_decision import CONVERT_AND_FUND, DEFER, ESCALATE, FUND, diff_plans

st.set_page_config(page_title="Treasury Triage", page_icon=":material/account_balance:", layout="wide")

POLICY = TreasuryPolicy()
RESULTADOS = Path(__file__).with_name("resultados")
ACCION = {FUND: "Pay", CONVERT_AND_FUND: "Convert + pay", DEFER: "Defer", ESCALATE: "A person decides"}
ESTILO = {FUND: ("green", ":material/check_circle:"), CONVERT_AND_FUND: ("blue", ":material/currency_exchange:"),
          DEFER: ("orange", ":material/schedule:"), ESCALATE: ("red", ":material/front_hand:")}
SIMBOLO = {"USD": "$", "EUR": "€", "GBP": "£"}
RED = {("LOCAL", "USD"): "ACH", ("LOCAL", "EUR"): "SEPA", ("LOCAL", "GBP"): "Faster Payments", ("SWIFT", "EUR"): "SWIFT"}
ACENTO, ROJO = "#4338CA", "#DC2626"  # los de .streamlit/config.toml
DONDE = "the simulated sandbox" if SIMULADO else "Airwallex"


@st.cache_resource
def get_rail() -> AirwallexRail:
    return conectar()


def claude_disponible() -> bool:
    if os.environ.get("TT_NO_CLAUDE") == "1":  # para ensayar la demo pública (sin clave) en un equipo que sí la tiene
        return False
    return bool(read_env(ENV).get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


def dinero(cantidad: float, divisa: str = BASE, decimales: int = 2) -> str:
    cantidad = 0.0 if abs(cantidad) < 0.005 else cantidad  # sin «-0» por los céntimos de redondeo
    return f"{SIMBOLO.get(divisa, '')}{cantidad:,.{decimales}f}"


def comprobar_auditoria() -> str:
    """La orden que comprueba este mismo libro desde la consola (en el modo demo, cada visitante tiene su carpeta)."""
    carpeta = Path(os.path.relpath(DATA, Path(__file__).parent)).as_posix() if SIMULADO else ""
    return f"python verificar_auditoria.py {carpeta}".rstrip()


def md(texto: str) -> str:
    """En Markdown de Streamlit, dos «$» seguidos se leen como una fórmula: se escapan."""
    return texto.replace("$", "\\$")


for clave, valor in (("log", []), ("lectura", None), ("leido", None), ("cambios", None), ("error", None)):
    st.session_state.setdefault(clave, valor)

rail = get_rail()
if SIMULADO and not (DATA / "sandbox_simulado.json").exists():  # primera visita: saldos de partida de la demo
    with contextlib.redirect_stdout(io.StringIO()):
        nueva_ronda()
        preparar_sandbox.main(rail)
        ajustar_saldos_demo.main(rail)
ronda = ronda_actual()
DATA.mkdir(exist_ok=True)
libro = Ledger(DATA / "ledger.json")
book = ApprovalBook.load(APROBACIONES)
senales = senales_de(ronda)
facturas = facturas_de(ronda)
comprobaciones = comprobaciones_de(ronda)  # la comprobación en Metal de las cuentas que no están en ninguna ficha
if senales is None:
    precalentar()  # con Metal de verdad, el servicio de comprobación arranca ya, en segundo plano
try:  # el agente mira en Airwallex si ha llegado algún cobro y lo concilia con su factura
    conciliados = conciliar(rail, libro, ronda)
except AirwallexError:
    conciliados = ya_conciliados(libro, ronda)
globex_pagado = "Globex" in conciliados


def _usd(x: float) -> str:
    return f"{x:,.0f} USD"  # sin «$»: en una tabla, dos «$» se leerían como una fórmula


def _puntos(x: float) -> str:
    return f"{x * 100:.0f} points"


# Las reglas del código, con un número que cita cada decisión de la pestaña Today
REGLAS = {
    "R1": ("Reserve floor", f"{_usd(POLICY.reserve_floor)}, all balances counted in USD"),
    "R2": ("Max chance of breaking it in the next 72 h", f"{POLICY.max_breach_prob:.0%}, over 5,000 simulated paths "
           "of when customers pay"),
    "R3": ("Cheapest plan within the risk limit", "among the plans that meet R2, the cheapest. Its cost counts FX spread, payout fees, each deferred bill's late "
           f"fees, and {_usd(POLICY.stop_operations_penalty)} if a bill that stops the business is not paid"),
    "R4": ("If keeping the reserve would stop the business", "pay only that bill, from the reserve, never below zero "
           "cash, and only after a person approves"),
    "R5": ("FX the agent may convert on its own", f"{_usd(POLICY.full_fx_limit)} in a normal week; "
           f"{_usd(POLICY.reduced_fx_limit)} when forecast confidence is more than {_puntos(POLICY.drop_for_reduced)} "
           f"below usual; nothing without a person when more than {_puntos(POLICY.drop_for_none)} below usual or "
           f"under {POLICY.min_confidence:.0%}. Above the limit, a person approves the exact cost of a locked quote"),
    "R6": ("Deferring a bill", f"pushes it {POLICY.defer_days} days and costs its late fees"),
    "R7": ("New payees, and invoices that do not match", "a person decides"),
    "R8": ("Where payments go", "the beneficiary on file, never an account read from an invoice"),
    "R9": ("What a customer's email can change", "only that customer's odds of paying on time (asks for more time "
           f"×{SIGNAL_LIKELIHOOD['asks_for_more_time']}, confirms ×{SIGNAL_LIKELIHOOD['confirms_payment']:.0f}, "
           f"disputes ×{SIGNAL_LIKELIHOOD['disputes_invoice']}), and only with a sentence found word for word in an "
           "email from the customer's own domain"),
    "R10": ("Every money movement", "one request_id; a retry reuses it, and a paid bill can never be paid again. "
            "Each change is also chained into a tamper-evident audit trail"),
    "R11": ("Payout fees (read from real sandbox transfers)", " · ".join(f"{RED[k]} {v:.2f} {k[1]}" for k, v in
                                                                         PAYOUT_FEES.items())
            + f"; conversions buy {FEE_CUSHION:.0%} more fee as a cushion"),
}


def regla(rid: str) -> str:
    return f":material/rule: {rid} · {REGLAS[rid][0]}"


def de_ronda(req) -> bool:
    return req.evidence[1:2] == (f"ronda:{ronda}",)


# ---------- acciones (callbacks: se ejecutan antes de volver a pintar la pantalla) ----------
def _accion(nombre: str, fn) -> None:
    st.session_state.error = None
    mensajes: list[str] = []
    try:
        fn(mensajes.append)
    except (AirwallexError, RuntimeError) as e:
        st.session_state.error = f"{nombre}: {e}"
    st.session_state.log = [f"**{nombre}**"] + mensajes + st.session_state.log


def reiniciar(log) -> None:
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):  # los scripts de preparación imprimen en español: no se enseña
        nueva = nueva_ronda()
        preparar_sandbox.main()
        ajustar_saldos_demo.main()
    st.session_state.lectura = st.session_state.leido = st.session_state.cambios = None
    log(f"Sandbox back to the opening balances. New round {nueva}")


def leer(log, con_claude: bool) -> None:
    antes = situacion(rail, libro, ronda, [], globex_pagado, ())
    lectura = leer_correos(rail, libro, ronda, globex_pagado, con_claude, repetir=True)
    guardar_facturas(ronda, lectura.facturas)
    guardar_senales(ronda, lectura.senales)
    despues = situacion(rail, libro, ronda, lectura.senales, globex_pagado, lectura.facturas)
    st.session_state.lectura = {"pasos": lectura.pasos, "resumen": lectura.resumen, "con_claude": con_claude,
                                "plan": huella(lectura.plan)}
    # lo que cambió la lectura, para el aviso de arriba (como el del cobro de Globex)
    st.session_state.leido = {"antes": antes.plan.confidence, "despues": despues.plan.confidence,
                              "fx_antes": antes.plan.fx_limit, "fx_despues": despues.plan.fx_limit,
                              "senales": [(sig.customer, sig.signal) for sig in lectura.senales],
                              "facturas": len(lectura.facturas),
                              "no_cuadran": [o.id for o in despues.obligaciones if despues.comprobadas.get(o.id)],
                              "por_aprobar": sum(c.needs_approval for c in despues.plan.conversions)}
    st.session_state.cambios = None  # si Globex ya pagó, su aviso describía el plan de antes de leer la bandeja
    log(f"Inbox read: {len(lectura.facturas)} invoice(s) and {len(lectura.senales)} signal(s) accepted")
    comprobar_beneficiarios(ronda, lectura.facturas, log)  # Metal: prueba para quien decide; nunca para la demo


def ejecutar(log) -> None:
    s = situacion(rail, libro, ronda, senales or [], globex_pagado, facturas)
    pagar(rail, libro, s, ronda, convertir(rail, libro, book, s, ronda, log), log, book)


def cobra_globex(log) -> None:
    antes = situacion(rail, libro, ronda, senales or [], False, facturas)
    deposito_globex(rail, libro, ronda, log)
    deposito = esperar_conciliacion(rail, libro, ronda, "Globex", log)
    if not deposito:
        raise RuntimeError("Globex's deposit is in Airwallex but does not match its invoice: a person reviews it")
    despues = situacion(rail, libro, ronda, senales or [], True, facturas)
    st.session_state.cambios = {"lineas": [(i, ACCION.get(a, a), ACCION.get(b, b)) for i, a, b in
                                           diff_plans(antes.plan, despues.plan)],
                                "antes": antes.plan.confidence, "despues": despues.plan.confidence,
                                "deposito": deposito}
    log("Plan re-computed with the new cash")


def decidir(log, fp: str, aprobar: bool) -> None:
    if fp not in book.pending:  # un doble clic: la tarjeta ya se decidió
        return
    req = book.pending[fp]
    (book.approve if aprobar else book.reject)(fp, "owner")
    log(f"{'Approved' if aprobar else 'Rejected'} by the owner: {titulo(req)}")


# ---------- barra lateral: los pasos de la demo ----------
pulsado = None  # (nombre, función) del paso que hay que ejecutar en esta pasada
con_claude = claude_disponible()
lee = ("Claude reads the inbox" if con_claude else "Claude reads the inbox (replay of a real run)"
       if hay_grabacion() else "Read the inbox (rehearsal, no Claude key)")
por_aprobar = sum(de_ronda(r) for r in book.pending.values())
rechazadas = frozenset(d for d in DIVISAS if rechazo_de_una_persona(book, d, ronda))  # el «no» vale para la ronda
toca = paso_siguiente(libro, ronda, senales, facturas, globex_pagado, por_aprobar, rechazadas)  # botón resaltado


def resaltar(*pasos) -> str:
    return "primary" if toca in pasos else "secondary"


with st.sidebar:
    st.header("Run the demo")
    st.caption(f"Round `{ronda}` · " + ("simulated Airwallex sandbox · no keys" if SIMULADO
                                         else "Airwallex sandbox · no real money"))
    if st.button("1 · Reset sandbox", icon=":material/restart_alt:", type=resaltar(None), width="stretch"):
        pulsado = ("Resetting the sandbox", reiniciar)
    if st.button(f"2 · {lee}", icon=":material/mail:", type=resaltar("2"), width="stretch",
                 disabled=senales is not None):
        pulsado = (lee, lambda log: leer(log, con_claude))
    if st.button("3 · Execute the plan", icon=":material/play_arrow:", type=resaltar("3", "3 otra vez"),
                 width="stretch"):
        pulsado = ("Executing the plan on Airwallex", ejecutar)
    if st.button(f"4 · Globex pays {dinero(PAGO_GLOBEX, decimales=0)}", icon=":material/payments:",
                 type=resaltar("4"), width="stretch",
                 disabled=globex_pagado or ya_hecho(libro, f"deposit:{ronda}:Globex")):
        pulsado = ("Globex pays", cobra_globex)
    # Lo que toca, debajo de los botones: si fuera encima, los botones se moverían cada vez que la frase cambia de largo
    if not pulsado:
        siguiente = {"2": f"2 · {lee}",
                     "aprobar": f"approve or reject the {f'{por_aprobar} cards' if por_aprobar != 1 else 'card'} "
                                "waiting for you on the Today tab",
                     "3": "3 · Execute the plan",
                     "4": f"4 · Globex pays {dinero(PAGO_GLOBEX, decimales=0)}",
                     "3 otra vez": "3 · Execute the plan again, with the new cash"}.get(toca)
        st.markdown(f":material/arrow_forward: **Next:** {md(siguiente)}" if siguiente else
                    ":material/check_circle: **Done.** Step 1 starts the demo again.")
    with st.expander("Agent log", icon=":material/receipt_long:"):
        for linea in st.session_state.log[:40]:
            if linea.startswith("**"):
                st.markdown(linea)
            else:
                st.text(linea)

# ---------- cabecera y cifras clave ----------
st.title("Treasury Triage")
st.caption("A treasury agent that knows how much to trust its own cash forecast · "
           + ("demo mode" if SIMULADO else "live on the Airwallex sandbox"))
if SIMULADO:
    st.caption(":material/science: **Demo mode, no keys:** a built-in simulator answers the same Airwallex API calls, "
               "with the rates and fees measured on the real sandbox. With sandbox keys in `.env`, the same buttons "
               "move money in the Airwallex sandbox.")
if pulsado:  # el paso corre aquí, a la vista: cada mensaje aparece mientras pasa
    nombre, fn = pulsado
    st.session_state.error = None
    mensajes: list[str] = []
    with st.status(f":shimmer[{nombre}]", expanded=True) as progreso:
        def log(m: str) -> None:
            mensajes.append(m)
            st.text(m)
        try:
            fn(log)
            progreso.update(label=f"{nombre}: done", state="complete", expanded=False)
        except (AirwallexError, RuntimeError) as e:
            st.session_state.error = f"{nombre}: {e}"
            progreso.update(label=f"{nombre}: stopped", state="error")
    st.session_state.log = [f"**{nombre}**"] + mensajes + st.session_state.log
    st.rerun()
if st.session_state.error:
    st.error(st.session_state.error, icon=":material/error:")

s = situacion(rail, libro, ronda, senales or [], globex_pagado, facturas)
caja = sum(a * s.rates[d] for d, a in s.saldos.items())
usual = MODEL.pool_rate

cols = st.columns(6)
for col, divisa in zip(cols, ("USD", "EUR", "GBP")):
    col.metric(f"{divisa} balance", dinero(s.saldos[divisa], divisa, 0), border=True, height="stretch",
               help=f"Live from {DONDE}: {dinero(s.saldos[divisa], divisa)}")
# la diferencia, entre las cifras ya redondeadas: 75 % frente a 81 % se lee «−6», no «−5»
cols[3].metric("Confidence", f"{s.plan.confidence:.0%}", f"{round(s.plan.confidence * 100) - round(usual * 100):+d} pts",
               delta_description=f"vs usual {usual:.0%}", border=True, height="stretch",
               help="Amount-weighted probability that the receipts due in 72 h arrive on time")
cols[4].metric("Agent FX limit", dinero(s.plan.fx_limit, decimales=0), border=True, height="stretch",
               help="The agent converts up to this alone; above it, a person approves. "
                    "It drops when this week looks worse than usual.")
cols[5].metric("Above reserve", dinero(caja - POLICY.reserve_floor, decimales=0), border=True, height="stretch",
               help=f"All balances in USD minus the {dinero(POLICY.reserve_floor, decimales=0)} reserve floor")

QUE_DICE = {"confirms_payment": "{} confirmed its payment", "asks_for_more_time": "{} will pay late",
            "disputes_invoice": "{} disputes its invoice"}
if st.session_state.leido and not st.session_state.cambios:  # lo que cambió al leer la bandeja, hasta que paga Globex
    l = st.session_state.leido
    quien = {o.id: o.payee for o in s.obligaciones}
    dichos = [QUE_DICE.get(tipo, "{}: " + tipo).format(cliente) for cliente, tipo in l["senales"]]
    texto = (f"{', '.join(dichos[:-1]) + ' and ' + dichos[-1] if len(dichos) > 1 else ''.join(dichos) or 'Nothing new'}"
             f", so confidence goes {l['antes']:.0%} → {l['despues']:.0%} (usual {usual:.0%}).")
    if l["fx_despues"] != l["fx_antes"]:
        texto += (f" The agent may now convert only {dinero(l['fx_despues'], decimales=0)} on its own (it was "
                  f"{dinero(l['fx_antes'], decimales=0)})"
                  + (f": {l['por_aprobar']} conversion{'s' if l['por_aprobar'] != 1 else ''} will need your approval."
                     if l["por_aprobar"] else "."))
    for oid in l["no_cuadran"]:
        texto += f" **{oid}** ({quien.get(oid, '')}): the invoice does not match, so a person decides."
    ignorados = len(BANDEJA) - len(l["senales"])
    if ignorados > 0:
        texto += (f" {ignorados} email{'s' if ignorados != 1 else ''} changed nothing: an email can only change a "
                  "customer's odds, never authorise a payment.")
    st.info(md(texto), title=f"Claude read {len(BANDEJA)} emails and {l['facturas']} supplier invoices",
            icon=":material/mark_email_read:")
if st.session_state.cambios:
    cambios = st.session_state.cambios
    lineas = "; ".join(f"**{i}** {a.lower()} → {b.lower()}" for i, a, b in cambios["lineas"]) or "nothing"
    st.success(md(f"Globex paid {dinero(PAGO_GLOBEX, decimales=0)}: the agent found the deposit in Airwallex "
                  f"(`{cambios.get('deposito', '')[:8]}`) and matched it to invoice {COBROS['Globex'][0]}. Confidence "
                  f"{cambios['antes']:.0%} → {cambios['despues']:.0%}. It only reopened what the new cash changes: "
                  f"{lineas}."), icon=":material/savings:")
if s.pagadas and globex_pagado and all(o.id in s.pagadas for o in s.obligaciones if not o.policy_exception):
    st.success(md(f"Reserve intact: {dinero(caja - POLICY.reserve_floor, decimales=0)} above the "
                  f"{dinero(POLICY.reserve_floor, decimales=0)} floor. The new supplier still waits for a person."),
               title="All bills the agent may pay are paid", icon=":material/verified:")

hoy, pruebas, auditoria, reglas = st.tabs(["Today", "Evidence: 1,000 weeks", "Audit trail", "How it decides"])


# ---------- pestaña Today: una tarjeta por pago, con sus aprobaciones dentro ----------
def tarjeta_aprobacion(fp: str, r) -> None:
    with st.container(border=True):
        st.markdown(md(f":material/approval: **{titulo(r)}**"))
        st.caption(f"Rate locked by {'simulated ' if SIMULADO else ''}Airwallex quote `{cotizacion(r)[:12]}…` · card `{fp[:8]}`"
                   if r.action != CON_RESERVA else f"Uses the cash reserve · card `{fp[:8]}`")
        st.caption(md(f"Why: {r.reason}"))
        for e in r.evidence[2:]:
            st.caption(md(f":material/mail: {e}"))
        with st.container(horizontal=True):
            st.button("Approve", key=f"ok-{fp}", type="primary", icon=":material/check:",
                      on_click=_accion, args=("Approval", lambda log, fp=fp: decidir(log, fp, True)))
            st.button("Reject", key=f"no-{fp}", icon=":material/close:",
                      on_click=_accion, args=("Approval", lambda log, fp=fp: decidir(log, fp, False)))


def de_este_pago(o, req, primera: dict) -> bool:
    """La tarjeta va en el pago al que sirve: la de reserva, en el suyo; la de una divisa, en el primero de esa divisa."""
    if req.action == CON_RESERVA:
        return req.counterparty == o.payee
    return req.counterparty.rsplit(" ", 1)[-1] == o.currency and primera.get(o.currency) == o.id


def grafica_caja() -> None:
    """La previsión de caja de las próximas 72 h con este plan: la banda del 90 % de los caminos simulados."""
    bandas = cash_bands(caja, receipts(senales or [], globex_pagado), s.plan.outflows, POLICY.horizon_days)
    dias = ["Today"] + [f"Day {i}" for i in range(1, POLICY.horizon_days + 1)]
    df = pd.DataFrame({"day": dias, "low": bandas[5], "mid": bandas[50], "high": bandas[95]})
    piso = POLICY.reserve_floor
    bajo, alto = min(min(bandas[5]), piso), max(max(bandas[95]), piso)
    margen = (alto - bajo) * 0.2 or 1_000
    escala = alt.Scale(domain=[bajo - margen, alto + margen])
    eje_y = alt.Axis(format="$,.0f", title=None, tickCount=4)
    x = alt.X("day:N", sort=dias, title=None, axis=alt.Axis(labelAngle=0))
    banda = alt.Chart(df).mark_area(opacity=0.15, color=ACENTO).encode(
        x=x, y=alt.Y("low:Q", scale=escala, axis=eje_y), y2="high:Q")
    linea = alt.Chart(df).mark_line(point=True, color=ACENTO, strokeWidth=2).encode(
        x=x, y=alt.Y("mid:Q", scale=escala),
        tooltip=[alt.Tooltip("day:N", title="Day"), alt.Tooltip("mid:Q", title="Median", format="$,.0f"),
                 alt.Tooltip("low:Q", title="Bad case (5%)", format="$,.0f"),
                 alt.Tooltip("high:Q", title="Good case (95%)", format="$,.0f")])
    df_piso = pd.DataFrame({"y": [piso], "label": [f"Reserve floor {dinero(piso, decimales=0)}"]})
    regla = alt.Chart(df_piso).mark_rule(color=ROJO, strokeDash=[5, 4]).encode(y=alt.Y("y:Q", scale=escala))
    texto = alt.Chart(df_piso).mark_text(align="left", dx=4, dy=-8, color=ROJO, fontSize=11).encode(
        y=alt.Y("y:Q", scale=escala), x=alt.value(0), text="label:N")
    st.altair_chart((banda + linea + regla + texto).properties(height=230), width="stretch",
                    alt="Cash forecast for the next 72 hours with this plan, against the reserve floor")


with hoy:
    decision = {d.obligation_id: d for d in s.plan.decisions}
    pendientes = [(fp, r) for fp, r in book.pending.items() if de_ronda(r)]
    hechas = [(g, "approved") for g in book.granted.values() if de_ronda(g["request"])] + \
             [(g, "rejected") for g in book.rejected.values() if de_ronda(g["request"]) and g["approver"] == "owner"]
    primera: dict[str, str] = {}
    for o in s.obligaciones:
        primera.setdefault(o.currency, o.id)
    mostradas: set[str] = set()

    # El plan de un vistazo: cuántos pagos van con cada decisión, lo que espera a una persona y el riesgo
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown("**Today's plan**")
        if s.pagadas:
            st.badge(f"Paid · {len(s.pagadas)}", icon=":material/verified:", color="green")
        for accion in (FUND, CONVERT_AND_FUND, DEFER, ESCALATE):
            n = sum(d.action == accion for d in s.plan.decisions)
            if n:
                color, icono = ESTILO[accion]
                st.badge(f"{ACCION[accion]} · {n}", icon=icono, color=color)
        if pendientes:
            st.badge(f"Waiting for your approval · {len(pendientes)}", icon=":material/approval:", color="orange")
        st.caption(f"Chance of breaking the reserve in the next 72 h: {s.plan.risk.p_breach:.1%} "
                   f"(the policy allows {POLICY.max_breach_prob:.0%})")

    # Por vencimiento, en filas de tres: las tarjetas de una fila miden lo mismo y la gráfica cierra la última
    por_vencer = sorted(s.obligaciones, key=lambda o: (o.due_day, s.obligaciones.index(o)))
    celdas, total = [], len(por_vencer) + 1
    for fila in range(0, total, 3):
        cols = st.columns(3)
        celdas += [cols[i].container(border=True, height="stretch") for i in range(min(3, total - fila))]
    for o, celda in zip(por_vencer, celdas):
        with celda:
            d = decision.get(o.id)
            cab = st.container(horizontal=True, vertical_alignment="center")
            cab.markdown(f"**{o.id}** · {o.payee}")
            if o.id in s.pagadas:
                cab.badge("Paid", icon=":material/check_circle:", color="green")
            elif o.id in s.enviadas:
                cab.badge("Sent", icon=":material/send:", color="gray")
            else:
                cab.badge(f"Due in {o.due_day} day{'s' if o.due_day != 1 else ''}", color="gray")
            st.caption(CONCEPTOS.get(o.id, ""))
            st.markdown(md(f"### {dinero(o.amount, o.currency)}"))
            comision = PAYOUT_FEES.get((o.transfer_method, o.currency), 0.0)
            st.caption(md(f"{RED.get((o.transfer_method, o.currency), o.transfer_method)} · fee "
                          f"{dinero(comision, o.currency)}"
                          + (f" · about {dinero(o.amount * s.rates[o.currency], decimales=0)}" if o.currency != BASE
                             else "")))
            if o.id in s.pagadas:
                st.caption(f":material/verified: Paid on {DONDE} (PAID)")
            elif o.id in s.enviadas:
                st.caption("At Airwallex, not PAID yet: Execute finishes it")
            elif d:
                color, icono = ESTILO[d.action]
                st.badge(ACCION[d.action], icon=icono, color=color)
                st.caption(md(d.reason[:1].upper() + d.reason[1:]))
                citada = ("R7" if d.action == ESCALATE else "R6" if d.action == DEFER
                          else "R4" if s.plan.uses_reserve and o.stops_operations else "R3")
                st.caption(regla(citada))
                conv = next((c for c in s.plan.conversions if c.buy_currency == o.currency), None)
                if d.action == CONVERT_AND_FUND and conv and primera.get(o.currency) == o.id:
                    st.markdown(md(f":material/currency_exchange: Buy **{dinero(conv.buy_amount, conv.buy_currency)}**"
                                   f" for about {dinero(conv.base_cost, decimales=0)} · ")
                                + (":red-badge[rejected by a person: not done this round]"
                                   if o.currency in rechazadas
                                   else ":orange-badge[needs a person · R5]" if conv.needs_approval
                                   else ":blue-badge[agent can do it alone · R5]"))
            if o.id in s.comprobadas:
                if s.comprobadas[o.id]:
                    st.markdown(md(":red[:material/gpp_bad: Invoice does not match: "
                                   + "; ".join(s.comprobadas[o.id]) + "]"))
                else:
                    st.caption(":material/fact_check: Invoice matches the payables list and the bank details on file")
            if o.id in comprobaciones:
                st.caption(texto_tarjeta(comprobaciones[o.id]))
            for fp, r in pendientes:
                if de_este_pago(o, r, primera):
                    mostradas.add(fp)
                    tarjeta_aprobacion(fp, r)
            for g, estado in hechas:
                if de_este_pago(o, g["request"], primera):
                    st.caption(md(f":material/{'check_circle' if estado == 'approved' else 'cancel'}: "
                                  f"{titulo(g['request'])} · {estado} by {g['approver']}"))
    with celdas[len(por_vencer)]:
        st.markdown("**Cash over the next 72 hours** · with this plan")
        grafica_caja()
        st.caption(md(f"The band holds 90% of 5,000 simulated paths of when customers pay. Chance of breaking "
                      f"the reserve: {s.plan.risk.p_breach:.1%} (the policy allows {POLICY.max_breach_prob:.0%})."))

    resto = [(fp, r) for fp, r in pendientes if fp not in mostradas]
    if resto:
        with st.container(border=True):
            st.subheader("Other approvals")
            for fp, r in resto:
                tarjeta_aprobacion(fp, r)

    with st.container(border=True):
        st.subheader("What the agent read")
        izq, der = st.columns([3, 2], gap="medium")
        lectura = st.session_state.lectura
        with izq:
            if lectura:
                for paso in lectura["pasos"]:
                    llamada, _, resultado = paso.partition(" -> ")
                    aviso = "REJECTED" in paso or "DOES NOT MATCH" in paso
                    with st.status(llamada, type="step", state="error" if aviso else "complete", expanded=True):
                        st.caption(resultado or llamada)
            elif senales is not None:
                for f in facturas:
                    st.markdown(md(f":material/description: {f.evidence}"))
                for sig in senales:
                    st.markdown(md(f":material/mail: {sig.evidence}"))
            else:
                st.caption("The inbox has not been read in this round yet. Amounts, limits and approvals are "
                           "decided in code; Claude reads emails and invoices and explains.")
        with der:
            if lectura and lectura["resumen"] and lectura.get("plan") not in (None, huella(plan_para_claude(s))):
                st.info("Claude wrote its briefing before the latest changes (payments made or received since), so "
                        "its numbers are out of date. Everything else on this screen is current.",
                        title="Claude's briefing for the owner", icon=":material/smart_toy:")
                with st.expander("The briefing as Claude wrote it (out of date)", icon=":material/history:"):
                    st.markdown(md(lectura["resumen"]))
            elif lectura and lectura["resumen"]:
                st.info(md(lectura["resumen"]), title="Claude's briefing for the owner", icon=":material/smart_toy:")
            with st.expander(f"Supplier invoices ({len(facturas_proveedor(fecha_ronda(ronda)))})",
                             icon=":material/description:"):
                st.caption("Payments always go to the beneficiary on file, never to an account read from an invoice.")
                for f in facturas_proveedor(fecha_ronda(ronda)):
                    st.markdown(f"**{f['subject']}** · {f['from']}")
                    st.text(f["body"])
            with st.expander(f"Inbox ({len(BANDEJA)} emails)", icon=":material/inbox:"):
                for m in BANDEJA:
                    st.markdown(f"**{m['subject']}** · {m['from']}")
                    st.text(m["body"])

with pruebas:
    st.subheader("Does it decide better? 1,000 simulated weeks")
    st.markdown("Every agent faced the same synthetic weeks. Customers' true on-time odds were hidden from all "
                "of them. Hypotheses were written in the code before running; the misses are reported too.")
    # cada gráfica, de la primera semilla nueva de la tanda que probó ese cambio
    resumen = RESULTADOS / "resumen_1000_semanas.png"  # la de video/hacer_imagenes.py: tres cifras que se leen de un vistazo
    if resumen.exists():
        st.image(str(resumen), alt="Weeks the business stopped, weeks below the reserve and cost per week: Treasury "
                                    "Triage and the same three simpler agents in every column")
    for semilla, nombre, texto in ((2031, "agentes", "Treasury Triage against simpler agents on the same 1,000 weeks"),
                                   (2029, "calibracion", "Calibration of the receivables forecast")):
        ruta = RESULTADOS / f"simulacion_1000_normales_semilla{semilla}_{nombre}.png"
        if ruta.exists():
            st.image(str(ruta), alt=texto)
    st.markdown("- The business never stopped (2 fresh seeds). With a reserve that is never touched, it stopped in "
                "12-13% of weeks\n"
                "- About \\$940 a week cheaper than a rule-based agent with no forecast, and \\$530-\\$570 cheaper "
                "than never touching the reserve. Paying bills in order costs less (\\$105-\\$112), but only because "
                "going below the reserve has no price in this cost\n"
                "- Cash never went below zero. It went below the reserve in 5-7% of weeks, against 14% when paying "
                "bills in order. The agent only plans to use the reserve with a person's approval\n"
                "- It learns from every payment: the forecast stays within 6 points of what really happened\n"
                "- A perfect forecast would not do better: the same engine knowing every customer's true odds cost "
                "the same (\\$322-\\$360 a week against \\$321-\\$351)\n"
                "- It asks a person for FX in 1 week in 4 or 5, and those are the weeks when payments really run late "
                "(63-74% against 47-48%)\n"
                "- Honest misses: the reserve is used more often than before (a choice: better than stopping the "
                "business); reading emails helps only a little")

with auditoria:
    st.subheader("Every money movement in this round")
    chequeo = libro.verify()
    if chequeo.ok:
        st.success(f"Audit trail verified: {chequeo.events} events, each chained to the one before by its hash, "
                   "and the ledger matches them"
                   + (f" ({chequeo.before_trail} older entries predate the trail)" if chequeo.before_trail else "")
                   + f". Check it yourself: {comprobar_auditoria()}", icon=":material/verified_user:")
    else:
        st.error(f"Audit trail check failed: {chequeo.problem}", icon=":material/gpp_bad:")
    movimientos = [{"Operation": e.op_key.split(":", 1)[0], "Key": e.op_key, "Attempt": e.attempt,
                    "Amount": dinero(e.amount, e.currency), "Counterparty": e.counterparty, "Status": e.status,
                    "Airwallex id": e.provider_id or "", "request_id": e.request_id, "Updated": e.updated_at[:19]}
                   for e in libro.entries if f":{ronda}:" in e.op_key]
    if movimientos:
        st.dataframe(movimientos, hide_index=True, alt="Ledger entries for this demo round")
    else:
        st.caption("Nothing has moved in this round yet.")
    st.caption("One request_id per operation; a retry reuses it, a new attempt after a confirmed failure gets a new "
               "one, and a bill that is already paid cannot be paid again.")

    # Lo pagado en Metal va en su propio libro (data/metal/): así no se mezcla con las cifras de Airwallex de arriba
    st.subheader("Paid on Metal in this round")
    en_metal = [{"Bill": oid, "Paid for": f"Payee check, {r.get('payee')}",
                 "Amount": f"{r.get('price')} *USDZ", "Result": str(r.get("result", "")).replace("_", " "),
                 "Transaction": r.get("explorer") or "",
                 "Run": (f"replay of a real testnet payment ({r.get('recorded_on', '')})".replace(" ()", "")
                         if r.get("mode") == "replay" else "live on testnet"),
                 "Paid by": (lambda p: f"agent wallet {p[:6]}…{p[-4:]}" if p else "")(r.get("payer") or "")}
                for oid, r in comprobaciones.items() if r.get("mode") in ("real", "replay") and r.get("transaction")]
    if en_metal:
        st.dataframe(en_metal, hide_index=True, alt="Payments on Metal testnet in this demo round",
                     column_config={"Transaction": st.column_config.LinkColumn(display_text="view on Metal explorer")})
    else:
        st.caption("Nothing paid on Metal in this round yet. Step 2 pays a payee check when a bill asks to be paid "
                   "into an account that is on no supplier file.")
    st.caption("Digital dollars (*USDZ) on Metal testnet, paid over x402 to a payee-check service (a demo service "
               "that runs locally). Kept in their own ledger with the same pay-once rule, so the Airwallex figures "
               "above are not mixed with them.")

with reglas:
    st.markdown("Among all combinations of bills it could pay today, the agent takes the cheapest one whose chance "
                "of breaking the reserve stays under the limit. Amounts, limits and approvals live in code; Claude "
                "reads emails and invoices and explains. Every decision on the Today tab cites the rule it follows.")
    st.table({"Rule": [f"{rid} · {nombre}" for rid, (nombre, _) in REGLAS.items()],
              "Value": [valor for _, valor in REGLAS.values()]})
