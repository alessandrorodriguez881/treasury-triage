"""Diagnóstico de la previsión con las semillas YA USADAS (2026-2028): ¿de dónde sale el error por tramo?

Solo calcula previsiones (sin planes, por eso es rápido) con las mismas semanas que la simulación y compara:
- ahora: el historial inicial, fijo durante las 1.000 semanas, y el correo con la razón de la política (0,35)
- crece: cada cobro entra en el historial de su cliente 4 semanas después (cuando ya han llegado hasta los tardíos)
- autocontrol: el historial fijo y, cada 20 semanas, la regresión del motor común sobre lo que predijo y lo que pasó
- crece + autocontrol
- la verdad oculta con el correo bien usado (también que NO escriba es una pista): el suelo de ruido de la medida
No cuenta como prueba: sirve para elegir el cambio. Las hipótesis se fijan después y se miden con semillas nuevas.

Uso:  python diagnostico_prevision.py
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import numpy as np

from simulacion import P_CORREO_SI_A_TIEMPO, P_CORREO_SI_TARDE, poblacion, semana
from triage_core.collections_calibration import Outcome, fit
from triage_core.collections_decision import WAIT, ReceivablesPolicy
from triage_core.collections_model import CollectionsModel, PaidInvoice
from triage_core.policy import SIGNAL_LIKELIHOOD

RETRASO_HISTORIAL, APRENDER_CADA = 4, 20
PEDIR = "asks_for_more_time"


def peor_tramo(pred: np.ndarray, real: np.ndarray, minimo: int = 50) -> float:
    peor = 0.0
    for lo in np.arange(0.0, 1.0, 0.1):
        en = (pred >= lo) & (pred < lo + 0.1 + (1e-9 if lo >= 0.9 else 0))
        if en.sum() >= minimo:
            peor = max(peor, abs(pred[en].mean() - real[en].mean()) * 100)
    return peor


def con_correo(p: float, correo: bool) -> float:
    if not correo:
        return p
    odds = p / (1 - p) * SIGNAL_LIKELIHOOD[PEDIR]
    return odds / (1 + odds)


def diagnostico(semilla: int, tipo: str, n: int = 1000) -> dict[str, tuple[float, float]]:
    rng = np.random.default_rng(semilla)
    clientes, historia = poblacion(rng, tipo=tipo)
    modelo = CollectionsModel(historia)
    p_real = {c.nombre: c.p_real for c in clientes}
    fijo, creciente, pendientes = modelo, modelo, []
    registros = {"autocontrol": [], "crece + autocontrol": []}
    calib = {k: fit([], ReceivablesPolicy()) for k in registros}
    pred = {k: [] for k in ("ahora", "crece", "autocontrol", "crece + autocontrol", "verdad (correo bien usado)")}
    real = []
    for i in range(n):
        s = semana(rng, clientes, i)
        if i % APRENDER_CADA == 0 and i:
            calib = {k: fit(v, ReceivablesPolicy()) for k, v in registros.items()}
        llegan = [h for semana_alta, h in pendientes if semana_alta <= i]
        if llegan:
            historia += llegan
            pendientes = [(w, h) for w, h in pendientes if w > i]
            creciente = CollectionsModel(historia)
        for c in s.cobros:
            cli = c.recibo.customer
            p_fijo = con_correo(fijo.profile(cli).p_on_time, c.correo)
            p_crece = con_correo(creciente.profile(cli).p_on_time, c.correo)
            resp = PEDIR if c.correo else None
            prof = lambda m, p: replace(m.profile(cli), p_on_time=p)
            pred["ahora"].append(p_fijo)
            pred["crece"].append(p_crece)
            pred["autocontrol"].append(calib["autocontrol"].profile(prof(fijo, p_fijo), resp).p_on_time)
            pred["crece + autocontrol"].append(calib["crece + autocontrol"].profile(prof(creciente, p_crece), resp).p_on_time)
            pe, pl = (P_CORREO_SI_A_TIEMPO, P_CORREO_SI_TARDE) if c.correo else (1 - P_CORREO_SI_A_TIEMPO, 1 - P_CORREO_SI_TARDE)
            o = p_real[cli] / (1 - p_real[cli]) * pe / pl
            pred["verdad (correo bien usado)"].append(o / (1 + o))
            real.append(c.a_tiempo)
            registros["autocontrol"].append(Outcome(p_fijo, False, WAIT, c.a_tiempo, resp))
            registros["crece + autocontrol"].append(Outcome(p_crece, False, WAIT, c.a_tiempo, resp))
            vence = date(2026, 1, 1) + timedelta(days=7 * i + c.recibo.due_day)
            pendientes.append((i + RETRASO_HISTORIAL,
                               PaidInvoice(cli, vence, vence + timedelta(days=c.llega - c.recibo.due_day), 1000)))
    y = np.array(real, dtype=float)
    return {k: (peor_tramo(np.array(v), y), float(np.mean((np.array(v) - y) ** 2))) for k, v in pred.items()}


def main() -> None:
    for semilla, tipo in ((2026, "normales"), (2027, "normales"), (2028, "normales"), (2027, "fiables"), (2028, "fiables")):
        print(f"\nSemilla {semilla}, clientes {tipo}  (peor tramo en puntos | Brier)")
        for k, (peor, brier) in diagnostico(semilla, tipo).items():
            print(f"  {k:<28} {peor:5.1f} | {brier:.4f}")


if __name__ == "__main__":
    main()
