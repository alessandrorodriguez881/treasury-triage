"""The 1,000-week simulator measures what it claims: known-answer weeks plus invariants on a small run."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulacion import (AGENTES, RETRASO_HISTORIAL, Aprendiz, Cobro, Semana, pagar_por_orden, receta_sin_prevision,
                        resultado, resumir, simular)
from triage_core.collections_model import PaidInvoice
from triage_core.cash_simulation import ExpectedReceipt
from triage_core.policy import TreasuryPolicy
from triage_core.treasury_decision import Obligation

POLICY = TreasuryPolicy()


def week(usd=30_000, arrives=1):
    obs = [Obligation("SHIP", "Freight", "USD", 12_000, 1, late_cost_per_day=50, stops_operations=True),
           Obligation("SAAS", "SaaS", "USD", 2_000, 3, late_cost_per_day=5),
           Obligation("NEW", "New", "USD", 5_000, 2, policy_exception="new payee")]
    cobros = [Cobro(ExpectedReceipt("C01", 10_000, 1), arrives, arrives == 1, False)]
    return Semana({"USD": usd, "EUR": 0.0, "GBP": 0.0}, obs, cobros)


def test_outcome_follows_the_real_cash_path():
    s = week(usd=30_000, arrives=9)  # the customer pays outside the 72 h
    out = resultado(s, [o for o in s.obligaciones if o.id in ("SHIP", "SAAS")], POLICY)
    # 30,000 - 12,003 (day 1) - 2,003 (day 3) = 15,994: above the 15,000 floor
    assert not out["reserva_rota"] and abs(out["minimo"] - 15_994) < 0.01
    assert out["coste"] == 6.0 and not out["empresa_parada"]  # two ACH fees, nothing deferred


def test_deferring_the_critical_bill_counts_as_a_stop_and_costs_the_penalty():
    s = week()
    out = resultado(s, [o for o in s.obligaciones if o.id == "SAAS"], POLICY)
    assert out["empresa_parada"] and out["recargos"] == 50 * POLICY.defer_days + POLICY.stop_operations_penalty


def test_the_recipe_never_plans_below_the_floor_on_todays_cash():
    s = week(usd=20_000, arrives=9)
    paid = receta_sin_prevision(s, POLICY)
    # the 12,000 shipment would leave 7,997 < 15,000, so the recipe skips it and pays the SaaS instead:
    # without a forecast it cannot see that the customer's 10,000 would have covered the shipment
    assert [o.id for o in paid] == ["SAAS"]
    out = resultado(s, paid, POLICY)
    assert not out["reserva_rota"] and out["empresa_parada"]


def test_pay_in_order_ignores_the_floor_but_never_policy():
    s = week(usd=20_000, arrives=9)  # the customer pays late
    paid = pagar_por_orden(s, POLICY)
    assert [o.id for o in paid] == ["SHIP", "SAAS"]  # the new payee is never paid by any agent
    assert resultado(s, paid, POLICY)["reserva_rota"]


def test_small_run_is_reproducible_and_complete():
    a, b = simular(15, semilla=3), simular(15, semilla=3)
    assert [f["coste"] for f in a["filas"][AGENTES[0]]] == [f["coste"] for f in b["filas"][AGENTES[0]]]
    assert all(len(a["filas"][x]) == 15 for x in AGENTES)
    res = resumir(a)
    assert set(res["hipotesis"]) == {f"H{i}" for i in range(1, 16)} - {"H7"}  # la H7 y la H16, test_escenario
    fija, rel = res["aprobacion_regla_fija"], res["aprobacion_regla_relativa"]
    assert 0 <= rel["semanas_con_aprobacion_pct"] <= 100 and 0 <= fija["semanas_con_aprobacion_pct"] <= 100
    assert all(0 <= p <= 1 for fila in a["calibracion"] for p in (fila[0], fila[1], fila[3], fila[4]))
    assert not any(f["reserva_rota"] for f in a["filas"]["Receta sin previsión"]
                   if f["minimo"] >= POLICY.reserve_floor)


def history(customer="C01", n=10):
    from datetime import date, timedelta
    d0 = date(2025, 1, 1)
    return [PaidInvoice(customer, d0 + timedelta(days=10 * k), d0 + timedelta(days=10 * k), 1000) for k in range(n)]


def test_a_payment_joins_the_history_only_once_even_late_ones_have_arrived():
    s = week(arrives=9)  # this customer pays late
    learner = Aprendiz(history(), aprende=True, lee_correos=True)
    recs, believed = learner.perfiles(s)
    learner.apuntar(0, s, believed)
    for i in range(1, RETRASO_HISTORIAL):
        learner.al_empezar(i)
        assert learner.modelo.profile("C01").n_invoices == 10  # not yet: it would be peeking at the future
    learner.al_empezar(RETRASO_HISTORIAL)
    after = learner.modelo.profile("C01")
    assert after.n_invoices == 11 and after.p_on_time < recs[0][1].p_on_time


def test_without_learning_the_forecast_never_moves():
    s = week(arrives=9)
    learner = Aprendiz(history(), aprende=False, lee_correos=True)
    before = learner.perfiles(s)[0][0][1].p_on_time
    for i in range(60):
        learner.apuntar(i, s, learner.perfiles(s)[1])
        learner.al_empezar(i + 1)
    assert learner.perfiles(s)[0][0][1].p_on_time == before and not learner.registros
