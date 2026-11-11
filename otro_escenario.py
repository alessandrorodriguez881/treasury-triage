"""«Another scenario»: el mismo agente, otra semana (10-nov; mejora 3 del jurado semanal del cerebro: «un botón que
cambia la decisión en directo»).

La misma función que decide el plan de hoy (`plan_treasury`, del motor común) decide aquí otra semana: otros saldos,
otras facturas y otros correos de los clientes. No toca Airwallex, ni Claude, ni Metal: solo calcula, así que va
igual sin claves (la demo pública). Quien lo mira ve que el plan sale de los datos y no de un guion: cambia una cifra
y ve qué decisiones se mueven, y por qué. Las reglas no cambian: el proveedor nuevo siempre va a una persona, y las
conversiones por encima del límite del agente también.

Cada escenario sale de una semilla («Another scenario» pasa a la siguiente en la que el plan cambia) y luego se puede
tocar a mano. «This week» vuelve a la semana de la demo, que es contra la que se compara todo. Lo que se ve, en inglés
(lo ve el jurado).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

import numpy as np
import pandas as pd
import streamlit as st

from escenario import BASE, COBROS, DIVISAS, MODEL, OBLIGATIONS, SALDOS_INICIALES, receipts
from triage_core.policy import TreasuryPolicy
from triage_core.signals import Signal
from triage_core.treasury_decision import CONVERT_AND_FUND, Plan, diff_plans, plan_treasury

POLICY = TreasuryPolicy()
CLIENTES = ("Acme", "Globex")
# lo que dice el correo de cada cliente esta semana (como lo registraría Claude)
RESPUESTAS = {"none": "No email", "confirms_payment": "Confirms", "asks_for_more_time": "Asks for time",
              "disputes_invoice": "Disputes"}
# con qué probabilidad sale cada correo al azar: Globex (el cobro grande) se retrasa o discute más a menudo
AL_AZAR = {"Acme": (0.45, 0.25, 0.2, 0.1), "Globex": (0.3, 0.2, 0.3, 0.2)}


@dataclass(frozen=True)
class Semana:
    """Una semana para el agente: saldos, lo que vale cada factura (en el orden de `OBLIGATIONS`, en su divisa), en
    cuántos días vence, qué dice el correo de cada cliente y cuánto se mueven el euro y la libra frente al dólar."""
    saldos: tuple[float, float, float]         # USD, EUR, GBP (el orden de `DIVISAS`)
    importes: tuple[float, ...]
    dias: tuple[int, ...]
    correos: tuple[str, str] = ("none", "none")  # Acme, Globex (claves de `RESPUESTAS`)
    fx: float = 0.0                            # 0.05: el euro y la libra, un 5 % más caros en dólares
    semilla: int = 0                           # 0: la semana de la demo


def esta_semana() -> Semana:
    """La semana de la demo, tal como empieza (antes de leer la bandeja): contra ella se compara todo."""
    return Semana(tuple(float(SALDOS_INICIALES[d]) for d in DIVISAS), tuple(float(o.amount) for o in OBLIGATIONS),
                  tuple(o.due_day for o in OBLIGATIONS))


def al_azar(semilla: int) -> Semana:
    """Otra semana verosímil para la misma empresa: cada cifra sale de la semilla (la misma semilla, la misma semana).
    Las cifras van redondeadas como las escribiría una persona (saldos y facturas a cientos, el cambio a puntos)."""
    rng = np.random.default_rng(semilla)
    saldos = (float(round(rng.uniform(10_000, 55_000), -3)), float(round(rng.uniform(0, 5_000), -2)),
              float(round(rng.uniform(0, 3_000), -2)))
    importes = tuple(float(round(o.amount * rng.uniform(0.6, 1.6), -2)) for o in OBLIGATIONS)
    dias = tuple(int(rng.integers(1, 4)) for _ in OBLIGATIONS)
    correos = tuple(str(rng.choice(list(RESPUESTAS), p=AL_AZAR[c])) for c in CLIENTES)
    return Semana(saldos, importes, dias, correos, int(rng.integers(-6, 7)) / 100, semilla)


def obligaciones(s: Semana) -> list:
    return [replace(o, amount=a, due_day=d) for o, a, d in zip(OBLIGATIONS, s.importes, s.dias)]


def tipos(s: Semana, rates: dict[str, float]) -> dict[str, float]:
    return {c: r if c == BASE else r * (1 + s.fx) for c, r in rates.items()}


def senales(s: Semana) -> list[Signal]:
    return [Signal(f"what-if-{c.lower()}", c, r, "(what-if scenario)") for c, r in zip(CLIENTES, s.correos)
            if r != "none"]


def planificar(s: Semana, rates: dict[str, float]) -> Plan:
    """Lo mismo que hace `demo.situacion` con la semana de verdad: el mismo motor y la misma política."""
    return plan_treasury(obligaciones(s), dict(zip(DIVISAS, s.saldos)), tipos(s, rates), receipts(senales(s)), POLICY,
                         base=BASE, usual_confidence=MODEL.pool_rate)


def cambios(antes: Plan, ahora: Plan) -> list[tuple[str, str, str]]:
    """Qué cambió de un plan a otro: cada decisión distinta (id, antes, ahora) y cada conversión que pasa de poder
    hacerla el agente solo a necesitar a una persona, o al revés (`FX EUR`, …)."""
    out = diff_plans(antes, ahora)
    quien = lambda c: "NEEDS_PERSON" if c.needs_approval else "AGENT_ALONE"  # noqa: E731
    previas = {c.buy_currency: quien(c) for c in antes.conversions}
    for c in ahora.conversions:
        if c.buy_currency in previas and previas[c.buy_currency] != quien(c):
            out.append((f"FX {c.buy_currency}", previas[c.buy_currency], quien(c)))
    return out


# ---------- la pestaña «What if?» (Streamlit) ----------
QUIEN = {"AGENT_ALONE": "the agent alone", "NEEDS_PERSON": "a person approves"}


def poner(s: Semana) -> None:
    """Pone la semana `s` en los controles. Va en los callbacks de los botones: antes de pintar los controles."""
    st.session_state.update({"wi_usd": s.saldos[0], "wi_eur": s.saldos[1], "wi_gbp": s.saldos[2],
                             "wi_acme": s.correos[0], "wi_globex": s.correos[1], "wi_fx": round(s.fx * 100),
                             "wi_facturas": (s.importes, s.dias), "wi_semilla": s.semilla})
    st.session_state["wi_n"] = st.session_state.get("wi_n", 0) + 1  # la tabla de facturas se pinta de nuevo


def otra(rates: dict[str, float]) -> None:
    """La siguiente semana al azar en la que el plan no es el de la semana de la demo."""
    base = planificar(esta_semana(), rates)
    semilla = st.session_state.get("wi_semilla", 0)
    for semilla in range(semilla + 1, semilla + 100):
        s = al_azar(semilla)
        if cambios(base, planificar(s, rates)):
            break
    poner(s)


def _controles() -> Semana:
    """Los datos de la semana, a mano (lo que hay en los controles)."""
    st.markdown("**The week**")
    with st.container(horizontal=True):
        for clave, divisa in zip(("wi_usd", "wi_eur", "wi_gbp"), DIVISAS):
            st.number_input(f"{divisa} balance", min_value=0.0, step=1_000.0, format="%.0f", key=clave)
    with st.container(horizontal=True):
        for clave, cliente in zip(("wi_acme", "wi_globex"), CLIENTES):
            st.segmented_control(f"{cliente}'s email · owes {COBROS[cliente][1]:,.0f} USD", list(RESPUESTAS),
                                 format_func=RESPUESTAS.get, required=True, key=clave)
    st.slider("EUR and GBP against USD", -10, 10, format="%+d%%", key="wi_fx")
    importes, dias = st.session_state["wi_facturas"]
    tabla = pd.DataFrame({"Bill": [f"{o.id} · {o.payee}" for o in OBLIGATIONS],
                          "Currency": [o.currency for o in OBLIGATIONS], "Amount": list(importes),
                          "Due in (days)": list(dias)})
    editada = st.data_editor(
        tabla, key=f"wi_tabla_{st.session_state['wi_n']}", hide_index=True, disabled=("Bill", "Currency"),
        column_config={"Amount": st.column_config.NumberColumn(format="accounting", min_value=0, step=100,
                                                               required=True),
                       "Due in (days)": st.column_config.NumberColumn(min_value=0, max_value=7, step=1,
                                                                      required=True)},
        alt="The bills due this week: amount and days until due, editable")
    return Semana((float(st.session_state.wi_usd), float(st.session_state.wi_eur), float(st.session_state.wi_gbp)),
                  tuple(float(x) for x in editada["Amount"]), tuple(int(x) for x in editada["Due in (days)"]),
                  (st.session_state.wi_acme, st.session_state.wi_globex), st.session_state.wi_fx / 100,
                  st.session_state.wi_semilla)


def _plan(s: Semana, rates: dict[str, float], accion: dict, estilo: dict, dinero: Callable) -> None:
    """El plan del agente para la semana `s`, y qué cambia frente a la semana de la demo."""
    plan, base = planificar(s, rates), planificar(esta_semana(), rates)
    antes = {d.obligation_id: d.action for d in base.decisions}
    caja = sum(a * tipos(s, rates)[d] for d, a in zip(DIVISAS, s.saldos))
    usual = MODEL.pool_rate
    st.markdown("**The agent's plan**")
    m = st.columns(4)
    m[0].metric("Confidence", f"{plan.confidence:.0%}", f"{round(plan.confidence * 100) - round(usual * 100):+d} pts",
                delta_description=f"vs usual {usual:.0%}", border=True, height="stretch",
                help="Amount-weighted probability that the receipts due in 72 h arrive on time")
    m[1].metric("Agent FX limit", dinero(plan.fx_limit, decimales=0), border=True, height="stretch",
                help="The agent converts up to this alone; above it, a person approves")
    m[2].metric("Above reserve", dinero(caja - POLICY.reserve_floor, decimales=0), border=True, height="stretch",
                help=f"All balances in USD minus the {dinero(POLICY.reserve_floor, decimales=0)} reserve floor")
    m[3].metric("Reserve at risk", f"{plan.risk.p_breach:.1%}", border=True, height="stretch",
                help=f"Chance of breaking the reserve in the next 72 h (the policy allows "
                     f"{POLICY.max_breach_prob:.0%})")
    convs, primera = {c.buy_currency: c for c in plan.conversions}, {}
    for o, d in zip(obligaciones(s), plan.decisions):
        primera.setdefault(o.currency, o.id)
        c1, c2, c3 = st.columns([3, 3, 4], vertical_alignment="center")
        c1.markdown(f"**{o.id}** · {o.payee}  \n{dinero(o.amount, o.currency, 0)} · due in {o.due_day} "
                    f"day{'s' if o.due_day != 1 else ''}")
        with c2.container(horizontal=True, gap="small"):
            color, icono = estilo[d.action]
            st.badge(accion[d.action], icon=icono, color=color)
            if antes.get(o.id) != d.action:
                st.badge(f"was {accion[antes[o.id]].lower()}", icon=":material/swap_horiz:", color="violet")
        porque = d.reason[:1].upper() + d.reason[1:]
        conv = convs.get(o.currency)
        if d.action == CONVERT_AND_FUND and conv and primera[o.currency] == o.id:
            porque += (f". Buy {dinero(conv.buy_amount, conv.buy_currency, 0)} for about "
                       f"{dinero(conv.base_cost, decimales=0)}: "
                       + ("above the agent's limit, so a person approves" if conv.needs_approval
                          else "the agent can do it alone"))
        c3.caption(porque)
    if plan.uses_reserve:
        st.warning("Keeping the reserve would stop the business: the agent plans to pay only the bills that stop it, "
                   "from the reserve, and a person approves that.", icon=":material/front_hand:")
    lista = cambios(base, plan)
    quien = {o.id: o.payee for o in OBLIGATIONS}
    if s == esta_semana():
        st.caption(":material/info: This is the demo's week as it starts, before the inbox is read: the one every scenario is "
                   "compared with. Press "
                   "**Another scenario**, or change any number.")
    elif lista:
        lineas = [f"- **{i[3:]} conversion:** {QUIEN[a]} → {QUIEN[b]}" if i.startswith("FX ")
                  else f"- **{i}** {quien.get(i, '')}: {accion[a].lower()} → {accion[b].lower()}" for i, a, b in lista]
        st.info("\n".join(lineas), title="What changed against the demo's week", icon=":material/compare_arrows:")
    else:
        st.success("Same decisions as in the demo's week: this change is not enough to move the plan.",
                   icon=":material/check:")


@st.fragment
def pintar(rates: dict[str, float], accion: dict, estilo: dict, dinero: Callable) -> None:
    """La pestaña entera. Es un fragmento: tocar algo aquí solo recalcula esto, sin volver a preguntar a Airwallex."""
    if "wi_usd" not in st.session_state:
        poner(esta_semana())
    with st.container(horizontal=True, vertical_alignment="center"):
        st.button("Another scenario", icon=":material/casino:", type="primary", on_click=otra, args=(rates,))
        st.button("This week", icon=":material/undo:", on_click=poner, args=(esta_semana(),))
        rotulo = st.empty()  # qué semana es: se escribe al final, cuando ya se sabe si alguien la ha tocado
    st.caption("Same agent, same rules, another week. Nothing here touches Airwallex, Claude or Metal: the code that "
               "plans the Today tab plans this week too, at today's rates. Change any number and see which "
               "decisions move, and why.")
    entrada, salida = st.columns([2, 3], gap="large")
    with entrada:
        semana = _controles()
    with salida:
        _plan(semana, rates, accion, estilo, dinero)
    origen = esta_semana() if semana.semilla == 0 else al_azar(semana.semilla)
    rotulo.caption(("The demo's week, before the inbox" if semana.semilla == 0 else f"Scenario {semana.semilla}, drawn at random")
                   + (", changed by you" if semana != origen else ""))
