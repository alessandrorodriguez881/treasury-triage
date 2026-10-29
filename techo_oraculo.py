"""El techo: ¿cuánto ganaría Treasury Triage si supiera la verdad? (semillas YA MEDIDAS; no cuenta como prueba)

El «oráculo» es el mismo motor con la previsión perfecta: la probabilidad verdadera de cada cliente, el correo
bien usado (que NO escriba también es una pista) y cómo se reparten de verdad sus retrasos. No sabe la suerte de la
semana. Decide con la misma política (la reserva paga el pago imprescindible, con una persona).
Si el oráculo apenas mejora a Treasury Triage, seguir afinando la previsión no vale la pena.

Uso:  python techo_oraculo.py [--semilla 2031]
"""
from __future__ import annotations

import argparse
import json

import numpy as np

from simulacion import RATES, RESULTADOS, poblacion, resultado, semana, verdad
from triage_core.collections_model import CustomerProfile
from triage_core.policy import TreasuryPolicy
from triage_core.treasury_decision import CONVERT_AND_FUND, FUND, plan_treasury


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--semilla", type=int, default=2031)
    ap.add_argument("--semanas", type=int, default=1000)
    args = ap.parse_args()
    policy = TreasuryPolicy()
    rng = np.random.default_rng(args.semilla)
    clientes, _ = poblacion(rng)
    p_real = {c.nombre: c.p_real for c in clientes}
    forma = np.random.default_rng(7)  # solo para la forma de los retrasos que conoce el oráculo
    retrasos = {c.nombre: 1 + forma.poisson(c.retraso_medio, 400) for c in clientes}
    filas = []
    for i in range(args.semanas):
        s = semana(rng, clientes, i)
        recs = [(c.recibo, CustomerProfile(c.recibo.customer, 0, verdad(p_real[c.recibo.customer], c.correo),
                                           retrasos[c.recibo.customer])) for c in s.cobros]
        plan = plan_treasury(s.obligaciones, s.saldos, RATES, recs, policy, seed=i)
        filas.append(resultado(s, [o for o in s.obligaciones if plan.action_of(o.id) in (FUND, CONVERT_AND_FUND)],
                               policy))
    out = {"reserva_rota_pct": 100 * float(np.mean([f["reserva_rota"] for f in filas])),
           "empresa_parada_pct": 100 * float(np.mean([f["empresa_parada"] for f in filas])),
           "caja_negativa_pct": 100 * float(np.mean([f["caja_negativa"] for f in filas])),
           "coste_medio": float(np.mean([f["coste"] for f in filas])),
           "pagado_medio_usd": float(np.mean([f["pagado_usd"] for f in filas]))}
    tt = json.loads((RESULTADOS / f"simulacion_{args.semanas}_normales_semilla{args.semilla}.json")
                    .read_text(encoding="utf-8"))["agentes"]["Treasury Triage"]
    out["Treasury Triage (misma semilla)"] = {k: tt[k] for k in out}
    RESULTADOS.mkdir(exist_ok=True)
    (RESULTADOS / f"techo_oraculo_{args.semanas}_semilla{args.semilla}.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
