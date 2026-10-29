"""Simulación de 1.000 semanas: ¿decide mejor un agente que sabe cuánto fiarse de su previsión?

Cada semana sintética trae saldos en 3 divisas, 3-5 pagos que vencen en 72 h y 2-3 cobros esperados.
Cada cliente tiene una probabilidad VERDADERA de pagar a tiempo que ningún agente ve: el motor solo ve
su historial de pagos. A veces el cliente escribe un correo («pagamos la semana que viene»), y es más
probable si de verdad va a pagar tarde: P = 0,40 si llega tarde y 0,14 si no (razón 0,35, la de la política).

Cuatro agentes deciden el día 0 con la misma información y se mide lo que pasa de verdad:
1. Treasury Triage: el motor (previsión con probabilidades, reserva y autonomía según la confianza)
2. Treasury Triage sin leer correos: igual, pero ignora los correos (lo que aporta la capa de IA)
3. Receta sin previsión: prioriza por urgencia y coste de retraso, y solo paga si el saldo de HOY no baja
   de la reserva (no cuenta con ningún cobro). Es lo que sale de la receta oficial «Treasury agent»
4. Pagar por orden: paga por fecha de vencimiento mientras haya dinero (no mira la reserva)
Los cuatro pasan igual a una persona las excepciones de política (proveedor nuevo): eso no se compara.
Las cuentas son las mismas para todos (las del motor): recargos por aplazar, cambio de divisa y comisiones.

Hipótesis fijadas ANTES de ejecutar (27-oct-2026). Si alguna no se cumple, se cuenta tal cual:
H1. Treasury Triage rompe la reserva menos semanas que «pagar por orden»
H2. Treasury Triage cuesta menos por semana que la «receta sin previsión»
H3. Leer los correos reduce las semanas con la reserva rota frente a no leerlos
H4. La previsión está calibrada: en cada tramo de probabilidad prevista, la proporción de cobros a tiempo
    queda a menos de 10 puntos de lo previsto (en los tramos con al menos 50 cobros)

Segunda tanda, fijada ANTES de medir (27-oct-2026, noche), con una semilla nueva (2028) que no se ha usado:
la regla de aprobación pasa a ser RELATIVA a lo normal de la cartera. El agente pide una persona cuando la
confianza de esta semana cae más de 5 puntos por debajo de la tasa habitual de cobro a tiempo: con una caída de
5 a 15 puntos, límite reducido; con más de 15, o con una confianza por debajo del 50 %, nada sin una persona.
En la simulación, la aprobación no cambia el resultado (se supone que la persona aprueba): solo se mide
cuántas veces se pide y si se pide cuando hay más riesgo.
H5. Con clientes normales, la regla relativa pide aprobación como mucho en el 40 % de las semanas
    (la regla fija la pedía en ~80 %)
H6. Con clientes normales y la regla relativa, las semanas en que pide aprobación tienen más cobros tardíos
    que las demás (diferencia > 0, con el intervalo del 95 % por encima de 0)
H7. La historia de la demo no cambia: antes del correo de Globex el agente convierte solo y después necesita
    aprobación para las dos conversiones (lo comprueba una prueba automática)

Tercera tanda, fijada ANTES de medir (28-oct-2026), con dos semillas nuevas (2029 y 2030) que no se han usado:
el agente APRENDE de cada cobro. Antes, la previsión se quedaba con el historial inicial las 1.000 semanas. Ahora
(clase `Aprendiz`): cada cobro entra en el historial de su cliente 4 semanas después (cuando ya han llegado hasta
los tardíos) y, cada 20 semanas, el autocontrol del motor común (el mismo que usa PayPal) compara lo que creyó con
lo que pasó y corrige la previsión base y lo que vale un correo. Se compara con el mismo agente sin aprender
(«Treasury Triage sin aprender», el de las dos primeras tandas). Lo eligió `diagnostico_prevision.py` entre cuatro
opciones, con las semillas ya usadas. Ese diagnóstico enseñó también que la H4 es ruidosa: hasta la probabilidad
verdadera la falla en 1 de 5 semillas (10,5 puntos), porque hay tramos con solo 50-100 cobros. Por eso se añade
la H9, que no depende de tramos. La H4 se vuelve a medir con el agente que aprende.
H8. La previsión mejora: el error cuadrático (Brier) del agente que aprende es menor que el del que no aprende
    (diferencia emparejada por cobro, IC 95 % por debajo de 0)
H9. Cierra al menos la mitad de la distancia (en Brier) entre la previsión sin aprender y la verdad oculta
H10. No es más arriesgado: frente al agente sin aprender, la diferencia de semanas con la reserva rota tiene el
     extremo superior del IC 95 % en +0,5 puntos o menos
H11. Cuesta menos por semana que el agente sin aprender (IC 95 % por debajo de 0)
Cada una tiene que cumplirse en las dos semillas, con clientes normales.

Cuarta tanda, fijada ANTES de medir (28-oct-2026, noche), con dos semillas nuevas (2031 y 2032): con el sí del
candidato, el pago que para la empresa puede salir de la reserva cuando no tocarla obligaría a aplazarlo. Solo ese
pago, nunca por debajo de cero (probabilidad de caja negativa dentro del límite de la política) y siempre con una
persona (en la simulación se supone que aprueba). Antes, dos tercios del coste del agente eran semanas con la
empresa parada. «Treasury Triage sin tocar la reserva» es el agente de la tercera tanda, para comparar.
H12. La empresa se para en menos del 1 % de las semanas (antes, en el 9-12 %)
H13. Cuesta menos por semana que el agente que nunca toca la reserva (IC 95 % por debajo de 0)
H14. La reserva se rompe en menos del 10 % de las semanas, y menos que pagando por orden (la H1)
H15. La caja baja de cero en menos del 1 % de las semanas
H16. La historia de la demo no cambia (pruebas de test_escenario.py)
Cada una tiene que cumplirse en las dos semillas, con clientes normales.

Clientes de la simulación principal: P(a tiempo) ~ Beta(6, 2), una media del 75 %. Semilla 2026.
Aparte, sin hipótesis: clientes «fiables», Beta(18, 2), una media del 90 % (como los de la demo).

Uso:  python simulacion.py [--semanas 1000] [--semilla 2026] [--clientes normales|fiables] [--prefijo simulacion]
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date, timedelta
import json
import math
from pathlib import Path
import sys
import time

import numpy as np

# el motor común: junto a la app (repositorio publicado) o dos carpetas más arriba (carpeta de trabajo)
sys.path.insert(0, str(next(p / "motor_comun" for p in Path(__file__).resolve().parents
                            if (p / "motor_comun" / "triage_core").exists())))

from triage_core.cash_simulation import ExpectedReceipt
from triage_core.collections_calibration import Outcome, fit
from triage_core.collections_decision import WAIT
from triage_core.collections_model import CollectionsModel, PaidInvoice
from triage_core.policy import SIGNAL_LIKELIHOOD, TreasuryPolicy
from triage_core.treasury_decision import (CONVERT_AND_FUND, FUND, Obligation, _defer_cost, _fee,
                                           plan_treasury)

RATES = {"USD": 1.0, "EUR": 1.126, "GBP": 1.346}  # tipos del sandbox de Airwallex el 27-oct
P_CORREO_SI_TARDE, P_CORREO_SI_A_TIEMPO = 0.40, 0.14
AGENTES = ("Treasury Triage", "Treasury Triage sin correos", "Receta sin previsión", "Pagar por orden",
           "Treasury Triage sin aprender", "Treasury Triage sin tocar la reserva")
# agente -> (aprende, lee los correos, cambios en la política)
MOTORES = {"Treasury Triage": (True, True, {}), "Treasury Triage sin correos": (True, False, {}),
           "Treasury Triage sin aprender": (False, True, {}),
           "Treasury Triage sin tocar la reserva": (True, True, {"reserve_covers_must_pay": False})}
RETRASO_HISTORIAL, APRENDER_CADA = 4, 20  # semanas hasta que un cobro entra en el historial; cada cuánto se recalibra
PEDIR = "asks_for_more_time"
INICIO = date(2026, 1, 1)  # fecha de la semana 0 (solo para fechar los cobros que entran en el historial)
RESULTADOS = Path(__file__).with_name("resultados")


@dataclass(frozen=True)
class Cliente:
    nombre: str
    p_real: float
    retraso_medio: float


@dataclass
class Cobro:
    recibo: ExpectedReceipt
    llega: int            # día en que llega de verdad (puede caer fuera de las 72 h)
    a_tiempo: bool
    correo: bool          # el cliente escribió pidiendo más tiempo


@dataclass
class Semana:
    saldos: dict[str, float]
    obligaciones: list[Obligation]
    cobros: list[Cobro]


def _base(divisa: str, importe: float) -> float:
    return importe * RATES[divisa]


# Poblaciones de clientes: P(pagar a tiempo) ~ Beta(a, b). «normales» es la principal (fijada antes de ejecutar);
# «fiables» se añadió tras la prueba corta, porque con clientes normales el agente pide aprobación casi siempre
POBLACIONES = {"normales": (6, 2), "fiables": (18, 2)}


def poblacion(rng: np.random.Generator, n: int = 30, tipo: str = "normales") -> tuple[list[Cliente], list[PaidInvoice]]:
    """Clientes con una verdad oculta y el historial que el motor sí puede ver."""
    a, b = POBLACIONES[tipo]
    clientes = [Cliente(f"C{i:02d}", float(rng.beta(a, b)), float(rng.uniform(1, 8))) for i in range(n)]
    historia, d0 = [], date(2025, 1, 1)
    for c in clientes:
        for k in range(int(rng.integers(3, 25))):
            vence = d0 + timedelta(days=10 * k)
            tarde = 0 if rng.random() < c.p_real else 1 + int(rng.poisson(c.retraso_medio))
            historia.append(PaidInvoice(c.nombre, vence, vence + timedelta(days=tarde), 1000))
    return clientes, historia


def semana(rng: np.random.Generator, clientes: list[Cliente], i: int) -> Semana:
    u = lambda a, b: round(float(rng.uniform(a, b)), 2)
    saldos = {"USD": u(20_000, 45_000), "EUR": u(0, 2_000), "GBP": u(0, 800)}
    obs = [
        Obligation(f"{i}-SHIP", "Freight", "USD", u(6_000, 15_000), 1, late_cost_per_day=u(30, 80),
                   stops_operations=True),
        Obligation(f"{i}-SUP", "EU supplier", "EUR", u(3_000, 10_000), int(rng.integers(1, 4)),
                   late_cost_per_day=u(50, 200), transfer_method="SWIFT" if rng.random() < 0.5 else "LOCAL"),
        Obligation(f"{i}-SAAS", "SaaS", "USD", u(500, 3_000), 3, late_cost_per_day=u(2, 10)),
    ]
    if rng.random() < 0.6:
        obs.append(Obligation(f"{i}-UK", "UK contractor", "GBP", u(2_000, 7_000), 2, late_cost_per_day=u(20, 60)))
    if rng.random() < 0.3:
        obs.append(Obligation(f"{i}-NEW", "New payee", "USD", u(2_000, 8_000), 2,
                              policy_exception="new payee, not on the approved list"))
    cobros = []
    for j in rng.choice(len(clientes), size=int(rng.integers(2, 4)), replace=False):
        c = clientes[int(j)]
        vence = int(rng.integers(1, 3))
        a_tiempo = bool(rng.random() < c.p_real)
        llega = vence if a_tiempo else vence + 1 + int(rng.poisson(c.retraso_medio))
        correo = bool(rng.random() < (P_CORREO_SI_A_TIEMPO if a_tiempo else P_CORREO_SI_TARDE))
        cobros.append(Cobro(ExpectedReceipt(c.nombre, u(4_000, 20_000), vence), llega, a_tiempo, correo))
    return Semana(saldos, obs, cobros)


class Aprendiz:
    """La previsión de un agente Treasury Triage y lo que aprende semana a semana (tercera tanda).

    - Cada cobro entra en el historial de su cliente RETRASO_HISTORIAL semanas después, cuando ya han llegado hasta
      los tardíos: si entrara al llegar, el historial reciente tendría de más los que llegan a tiempo
    - Cada APRENDER_CADA semanas, el autocontrol del motor común (`collections_calibration.fit`) compara lo que creyó
      con lo que pasó (si llegó a tiempo se sabe el día que vence) y corrige la previsión base y lo que vale un correo
    Sin aprender, se queda con el historial inicial las 1.000 semanas, como en la primera y la segunda tanda.
    """

    def __init__(self, historia: list[PaidInvoice], aprende: bool, lee_correos: bool):
        self.historia, self.aprende, self.lee_correos = list(historia), aprende, lee_correos
        self.modelo = CollectionsModel(self.historia)
        self.autocontrol = fit([])
        self.pendientes: list[tuple[int, PaidInvoice]] = []
        self.registros: list[Outcome] = []

    def al_empezar(self, i: int) -> None:
        """Al empezar la semana i entra lo que ya se sabe."""
        if not self.aprende:
            return
        llegan = [h for semana_alta, h in self.pendientes if semana_alta <= i]
        if llegan:
            self.historia += llegan
            self.pendientes = [(w, h) for w, h in self.pendientes if w > i]
            self.modelo = CollectionsModel(self.historia)
        if i and i % APRENDER_CADA == 0:
            self.autocontrol = fit(self.registros)

    def _respuesta(self, c: Cobro) -> str | None:
        return PEDIR if self.lee_correos and c.correo else None

    def perfiles(self, s: Semana):
        """Los cobros con su perfil, y lo que el agente creía antes del autocontrol (para apuntarlo después)."""
        recs, creia = [], []
        for c in s.cobros:
            p = self.modelo.profile(c.recibo.customer)
            if self._respuesta(c):
                p = CollectionsModel.apply_signal(p, SIGNAL_LIKELIHOOD[PEDIR], "email")
            creia.append(p.p_on_time)
            recs.append((c.recibo, self.autocontrol.profile(p, self._respuesta(c))))
        return recs, creia

    def apuntar(self, i: int, s: Semana, creia: list[float]) -> None:
        """Lo que pasó con los cobros de la semana i: va al autocontrol ya y al historial más tarde."""
        if not self.aprende:
            return
        for c, p in zip(s.cobros, creia):
            self.registros.append(Outcome(p, False, WAIT, c.a_tiempo, self._respuesta(c)))
            vence = INICIO + timedelta(days=7 * i + c.recibo.due_day)
            pagado = vence + timedelta(days=c.llega - c.recibo.due_day)
            self.pendientes.append((i + RETRASO_HISTORIAL, PaidInvoice(c.recibo.customer, vence, pagado, 1000)))


def verdad(p_real: float, correo: bool) -> float:
    """La previsión perfecta, que solo la simulación conoce: la probabilidad oculta y el correo bien usado (que el
    cliente NO escriba también es una pista). Ninguna previsión puede hacerlo mejor de media: sirve de suelo."""
    pe, pl = ((P_CORREO_SI_A_TIEMPO, P_CORREO_SI_TARDE) if correo
              else (1 - P_CORREO_SI_A_TIEMPO, 1 - P_CORREO_SI_TARDE))
    odds = p_real / (1 - p_real) * pe / pl
    return odds / (1 + odds)


def _coste_de_pagar(ob: Obligation, s: Semana, comprometido: dict[str, float], policy: TreasuryPolicy) -> float:
    """Lo que cuesta en USD pagar ob sumado a lo ya comprometido: importe, comisión y cambio de lo que falte."""
    falta = max(0.0, comprometido.get(ob.currency, 0.0) + ob.amount + _fee(ob) - s.saldos.get(ob.currency, 0.0))
    ya_faltaba = max(0.0, comprometido.get(ob.currency, 0.0) - s.saldos.get(ob.currency, 0.0))
    cambio = 0.0 if ob.currency == "USD" else policy.fx_spread * _base(ob.currency, falta - ya_faltaba)
    return _base(ob.currency, ob.amount + _fee(ob)) + cambio


def receta_sin_prevision(s: Semana, policy: TreasuryPolicy) -> list[Obligation]:
    """Lo urgente primero y después lo más caro de retrasar por dólar; paga solo si HOY sigue por encima de la reserva."""
    caja = sum(_base(d, a) for d, a in s.saldos.items())
    orden = sorted((o for o in s.obligaciones if not o.policy_exception),
                   key=lambda o: (not o.stops_operations, -o.late_cost_per_day / _base(o.currency, o.amount)))
    pagadas, comprometido = [], {}
    for ob in orden:
        coste = _coste_de_pagar(ob, s, comprometido, policy)
        if caja - coste >= policy.reserve_floor:
            caja -= coste
            comprometido[ob.currency] = comprometido.get(ob.currency, 0.0) + ob.amount + _fee(ob)
            pagadas.append(ob)
    return pagadas


def pagar_por_orden(s: Semana, policy: TreasuryPolicy) -> list[Obligation]:
    """Por fecha de vencimiento, mientras haya dinero. No mira la reserva ni los cobros."""
    caja = sum(_base(d, a) for d, a in s.saldos.items())
    pagadas, comprometido = [], {}
    for ob in sorted((o for o in s.obligaciones if not o.policy_exception), key=lambda o: (o.due_day, o.id)):
        coste = _coste_de_pagar(ob, s, comprometido, policy)
        if caja - coste >= 0:
            caja -= coste
            comprometido[ob.currency] = comprometido.get(ob.currency, 0.0) + ob.amount + _fee(ob)
            pagadas.append(ob)
    return pagadas


def resultado(s: Semana, pagadas: list[Obligation], policy: TreasuryPolicy) -> dict:
    """Lo que pasa DE VERDAD con esas decisiones: la caja día a día con los cobros reales."""
    necesita: dict[str, float] = {}
    for ob in pagadas:
        necesita[ob.currency] = necesita.get(ob.currency, 0.0) + ob.amount + _fee(ob)
    cambio = sum(policy.fx_spread * _base(d, max(0.0, n - s.saldos.get(d, 0.0)))
                 for d, n in necesita.items() if d != "USD")
    flujos = np.zeros(policy.horizon_days + 1)
    flujos[0] -= cambio
    for ob in pagadas:
        flujos[ob.due_day] -= _base(ob.currency, ob.amount + _fee(ob))
    for c in s.cobros:
        if c.llega <= policy.horizon_days:
            flujos[c.llega] += c.recibo.amount
    camino = sum(_base(d, a) for d, a in s.saldos.items()) + np.cumsum(flujos)
    ids = {o.id for o in pagadas}
    aplazadas = [o for o in s.obligaciones if not o.policy_exception and o.id not in ids]
    comisiones = sum(_base(o.currency, _fee(o)) for o in pagadas)
    recargos = sum(_defer_cost(o, policy) for o in aplazadas)
    return {"reserva_rota": bool(camino.min() < policy.reserve_floor), "minimo": float(camino.min()),
            "caja_negativa": bool(camino.min() < 0),
            "empresa_parada": any(o.stops_operations for o in aplazadas),
            "coste": round(recargos + cambio + comisiones, 2), "recargos": round(recargos, 2),
            "pagado_usd": round(sum(_base(o.currency, o.amount) for o in pagadas), 2)}


def simular(n: int, semilla: int, policy: TreasuryPolicy = TreasuryPolicy(), clientes_tipo: str = "normales") -> dict:
    rng = np.random.default_rng(semilla)
    clientes, historia = poblacion(rng, tipo=clientes_tipo)
    p_real = {c.nombre: c.p_real for c in clientes}
    motores = {a: Aprendiz(historia, aprende, lee) for a, (aprende, lee, _) in MOTORES.items()}
    filas = {a: [] for a in AGENTES}
    calibracion, aprobaciones, todo_persona = [], [], []
    for i in range(n):
        s = semana(rng, clientes, i)
        planes, previsto = {}, {}
        for agente, motor in motores.items():
            motor.al_empezar(i)
            recs, creia = motor.perfiles(s)
            plan = plan_treasury(s.obligaciones, s.saldos, RATES, recs, replace(policy, **MOTORES[agente][2]), seed=i)
            planes[agente], previsto[agente] = plan, [p.p_on_time for _, p in recs]
            filas[agente].append(resultado(s, [o for o in s.obligaciones
                                               if plan.action_of(o.id) in (FUND, CONVERT_AND_FUND)], policy))
            filas[agente][-1]["riesgo_previsto"] = plan.risk.p_breach  # lo que creía que arriesgaba ese plan
            filas[agente][-1]["usa_reserva"] = plan.uses_reserve  # paga con la reserva (una persona lo aprueba)
            motor.apuntar(i, s, creia)
        # por cobro: lo previsto con correos, sin correos, si llegó a tiempo, sin aprender y la verdad oculta
        calibracion += [(t, sc, c.a_tiempo, sa, verdad(p_real[c.recibo.customer], c.correo))
                        for t, sc, sa, c in zip(previsto["Treasury Triage"], previsto["Treasury Triage sin correos"],
                                                previsto["Treasury Triage sin aprender"], s.cobros)]
        filas["Receta sin previsión"].append(resultado(s, receta_sin_prevision(s, policy), policy))
        filas["Pagar por orden"].append(resultado(s, pagar_por_orden(s, policy), policy))
        plan = planes["Treasury Triage"]
        pidio = any(c.needs_approval for c in plan.conversions)  # regla fija (85 % / 60 %)
        limite_rel = policy.autonomous_fx_limit(plan.confidence, motores["Treasury Triage"].modelo.pool_rate)  # relativa
        pidio_rel = any(c.base_cost > limite_rel for c in plan.conversions)
        aprobaciones.append((pidio, any(not c.a_tiempo for c in s.cobros), any(c.correo for c in s.cobros), pidio_rel))
        # Si hasta no pagar nada rompe la reserva, el motor no decide: todo pasa a una persona (se cuenta aparte)
        todo_persona.append(math.isnan(planes["Treasury Triage"].expected_cost))
    return {"filas": filas, "calibracion": calibracion, "aprobaciones": aprobaciones, "todo_persona": todo_persona}


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centro, margen = (p + z * z / (2 * n)) / (1 + z * z / n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (centro - margen, centro + margen)


def _media_ic(x: np.ndarray) -> tuple[float, float, float]:
    m, e = float(x.mean()), float(x.std(ddof=1) / math.sqrt(len(x))) if len(x) > 1 else 0.0
    return m, m - 1.96 * e, m + 1.96 * e


def resumir(sim: dict) -> dict:
    filas, n = sim["filas"], len(sim["filas"][AGENTES[0]])
    agentes = {}
    for a in AGENTES:
        rotas = sum(f["reserva_rota"] for f in filas[a])
        paradas = sum(f["empresa_parada"] for f in filas[a])
        coste = np.array([f["coste"] for f in filas[a]])
        negativas = sum(f["caja_negativa"] for f in filas[a])
        agentes[a] = {"reserva_rota_pct": 100 * rotas / n, "reserva_rota_ic": [100 * v for v in _wilson(rotas, n)],
                      "empresa_parada_pct": 100 * paradas / n, "caja_negativa_pct": 100 * negativas / n,
                      "coste_medio": _media_ic(coste)[0], "coste_ic": list(_media_ic(coste)[1:]),
                      "pagado_medio_usd": float(np.mean([f["pagado_usd"] for f in filas[a]]))}
        if "riesgo_previsto" in filas[a][0]:  # ¿sabe cuánto arriesga? lo previsto frente a lo que pasó
            agentes[a]["riesgo_previsto_pct"] = 100 * float(np.mean([f["riesgo_previsto"] for f in filas[a]]))
            agentes[a]["usa_reserva_pct"] = 100 * float(np.mean([f["usa_reserva"] for f in filas[a]]))

    def diferencia(a: str, b: str, campo: str) -> list[float]:
        d = np.array([float(x[campo]) - float(y[campo]) for x, y in zip(filas[a], filas[b])])
        return list(_media_ic(d))

    cal = np.array(sim["calibracion"], dtype=float)

    def por_tramos(col: int) -> list[dict]:
        out = []
        for lo in np.arange(0.0, 1.0, 0.1):
            en = (cal[:, col] >= lo) & (cal[:, col] < lo + 0.1 + (1e-9 if lo >= 0.9 else 0))
            if en.sum():
                out.append({"tramo": f"{lo:.0%}-{lo + 0.1:.0%}", "n": int(en.sum()),
                            "previsto": float(cal[en, col].mean()), "observado": float(cal[en, 2].mean())})
        return out

    def peor(tramos: list[dict]) -> float | None:  # el criterio de la H4: tramos con al menos 50 cobros
        return max((abs(t["previsto"] - t["observado"]) * 100 for t in tramos if t["n"] >= 50), default=None)

    tramos = por_tramos(0)
    brier = lambda p: float(np.mean((p - cal[:, 2]) ** 2))
    b_triage, b_sin_aprender, b_verdad = brier(cal[:, 0]), brier(cal[:, 3]), brier(cal[:, 4])
    # aprox.: trata cada cobro como independiente, aunque los del mismo cliente se parecen
    brier_dif = _media_ic((cal[:, 0] - cal[:, 2]) ** 2 - (cal[:, 3] - cal[:, 2]) ** 2)
    hueco = b_sin_aprender - b_verdad
    apr = np.array(sim["aprobaciones"], dtype=bool)
    con, sin = apr[apr[:, 0]], apr[~apr[:, 0]]

    def regla(col: int) -> dict:
        """Cuántas semanas pide una persona y si esas semanas son más arriesgadas (algún cobro llega tarde)."""
        si, no = apr[apr[:, col], 1], apr[~apr[:, col], 1]
        p1, p2 = (float(si.mean()) if len(si) else 0.0), (float(no.mean()) if len(no) else 0.0)
        e = math.sqrt(p1 * (1 - p1) / max(len(si), 1) + p2 * (1 - p2) / max(len(no), 1))
        return {"semanas_con_aprobacion_pct": 100 * len(si) / n, "cobro_tarde_si_pide_pct": 100 * p1,
                "cobro_tarde_si_no_pide_pct": 100 * p2,
                "diferencia_puntos": [100 * (p1 - p2), 100 * (p1 - p2 - 1.96 * e), 100 * (p1 - p2 + 1.96 * e)]}
    out = {
        "semanas": n,
        "agentes": agentes,
        "diferencias": {
            "H1 reserva rota: Triage - por orden (puntos)": [100 * v for v in diferencia(AGENTES[0], AGENTES[3], "reserva_rota")],
            "H2 coste: Triage - receta (USD/semana)": diferencia(AGENTES[0], AGENTES[2], "coste"),
            "H3 reserva rota: Triage - sin correos (puntos)": [100 * v for v in diferencia(AGENTES[0], AGENTES[1], "reserva_rota")],
            "H10 reserva rota: Triage - sin aprender (puntos)": [100 * v for v in diferencia(AGENTES[0], AGENTES[4], "reserva_rota")],
            "H11 coste: Triage - sin aprender (USD/semana)": diferencia(AGENTES[0], AGENTES[4], "coste"),
            "H13 coste: Triage - sin tocar la reserva (USD/semana)": diferencia(AGENTES[0], AGENTES[5], "coste"),
            "reserva rota: Triage - sin tocar la reserva (puntos)":
                [100 * v for v in diferencia(AGENTES[0], AGENTES[5], "reserva_rota")],
            "empresa parada: Triage - sin tocar la reserva (puntos)":
                [100 * v for v in diferencia(AGENTES[0], AGENTES[5], "empresa_parada")],
        },
        "calibracion": {"tramos": tramos, "brier_con_correos": b_triage,
                        "brier_sin_correos": brier(cal[:, 1]), "brier_media_fija": brier(np.full(len(cal), cal[:, 2].mean())),
                        "h4_peor_desvio_puntos": peor(tramos),
                        "tramos_sin_aprender": por_tramos(3), "peor_desvio_sin_aprender": peor(por_tramos(3)),
                        "tramos_verdad": por_tramos(4), "peor_desvio_verdad": peor(por_tramos(4)),
                        "brier_sin_aprender": b_sin_aprender, "brier_verdad": b_verdad,
                        "H8 brier: Triage - sin aprender": list(brier_dif),
                        "H9 distancia a la verdad cerrada": (b_sin_aprender - b_triage) / hueco if hueco > 0 else None},
        "semanas_todo_a_una_persona_pct": 100 * float(np.mean(sim["todo_persona"])),
        "aprobaciones": {"semanas_con_aprobacion_pct": 100 * len(con) / n,
                         "algun_cobro_tarde_si_pidio_pct": 100 * float(con[:, 1].mean()) if len(con) else None,
                         "algun_cobro_tarde_si_no_pidio_pct": 100 * float(sin[:, 1].mean()) if len(sin) else None},
        "aprobacion_regla_fija": regla(0),
        "aprobacion_regla_relativa": regla(3),
    }
    d = out["diferencias"]
    out["hipotesis"] = {
        "H1": d["H1 reserva rota: Triage - por orden (puntos)"][2] < 0,
        "H2": d["H2 coste: Triage - receta (USD/semana)"][2] < 0,
        "H3": d["H3 reserva rota: Triage - sin correos (puntos)"][2] < 0,
        "H4": out["calibracion"]["h4_peor_desvio_puntos"] is not None and out["calibracion"]["h4_peor_desvio_puntos"] < 10,
        "H5": out["aprobacion_regla_relativa"]["semanas_con_aprobacion_pct"] <= 40,
        "H6": out["aprobacion_regla_relativa"]["diferencia_puntos"][1] > 0,
        "H8": out["calibracion"]["H8 brier: Triage - sin aprender"][2] < 0,
        "H9": (out["calibracion"]["H9 distancia a la verdad cerrada"] or 0) >= 0.5,
        "H10": d["H10 reserva rota: Triage - sin aprender (puntos)"][2] <= 0.5,
        "H11": d["H11 coste: Triage - sin aprender (USD/semana)"][2] < 0,
        "H12": agentes[AGENTES[0]]["empresa_parada_pct"] < 1,
        "H13": d["H13 coste: Triage - sin tocar la reserva (USD/semana)"][2] < 0,
        "H14": agentes[AGENTES[0]]["reserva_rota_pct"] < 10 and d["H1 reserva rota: Triage - por orden (puntos)"][2] < 0,
        "H15": agentes[AGENTES[0]]["caja_negativa_pct"] < 1,
    }
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--semanas", type=int, default=1000)
    p.add_argument("--semilla", type=int, default=2026)
    p.add_argument("--clientes", choices=sorted(POBLACIONES), default="normales")
    p.add_argument("--prefijo", default="simulacion", help="otro nombre para no pisar resultados guardados")
    args = p.parse_args()
    t0 = time.time()
    sim = simular(args.semanas, args.semilla, clientes_tipo=args.clientes)
    res = resumir(sim)
    res.update(clientes=args.clientes, semilla=args.semilla, segundos=round(time.time() - t0, 1))
    RESULTADOS.mkdir(exist_ok=True)
    (RESULTADOS / f"{args.prefijo}_{args.semanas}_{args.clientes}_semilla{args.semilla}.json").write_text(
        json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
