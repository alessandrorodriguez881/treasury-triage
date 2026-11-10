"""«Another scenario»: the same planner on another week. The rules never move; the decisions do."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from escenario import MODEL, OBLIGATIONS, SALDOS_INICIALES, receipts
from otro_escenario import Semana, al_azar, cambios, esta_semana, obligaciones, planificar
from triage_core.policy import TreasuryPolicy
from triage_core.treasury_decision import CONVERT_AND_FUND, DEFER, ESCALATE, FUND, plan_treasury

RATES = {"USD": 1.0, "EUR": 1.1265, "GBP": 1.3466}  # the sandbox's rates on 27-oct, as in test_escenario
SEEDS = range(1, 201)


def test_this_week_is_the_week_the_demo_starts_with():
    p = planificar(esta_semana(), RATES)
    demo = plan_treasury(list(OBLIGATIONS), SALDOS_INICIALES, RATES, receipts([]), TreasuryPolicy(),
                         usual_confidence=MODEL.pool_rate)
    assert [(d.obligation_id, d.action) for d in p.decisions] == [(d.obligation_id, d.action) for d in demo.decisions]
    assert [(d.obligation_id, d.action) for d in p.decisions] == [
        ("SHIP-1", FUND), ("SUP-2", CONVERT_AND_FUND), ("SAAS-3", DEFER), ("UK-4", CONVERT_AND_FUND),
        ("NEW-5", ESCALATE)]
    assert p.fx_limit == TreasuryPolicy().full_fx_limit and not any(c.needs_approval for c in p.conversions)
    assert cambios(p, p) == []


def test_the_same_seed_is_the_same_week():
    assert al_azar(7) == al_azar(7) and al_azar(7) != al_azar(8)
    s = al_azar(7)
    assert len(s.importes) == len(s.dias) == len(OBLIGATIONS) and all(1 <= d <= 3 for d in s.dias)


def test_the_rules_never_move_whatever_the_week():
    policy = TreasuryPolicy()
    for seed in SEEDS:
        s = al_azar(seed)
        p = planificar(s, RATES)
        acts = {d.obligation_id: d.action for d in p.decisions}
        assert acts["NEW-5"] == ESCALATE  # the new supplier always goes to a person
        for c in p.conversions:  # above the agent's own limit, a person approves
            assert c.needs_approval == (c.base_cost > p.fx_limit)
        if p.uses_reserve:  # only the bill that stops the business may dip into the reserve
            paid = {o.id for o in obligaciones(s) if acts[o.id] in (FUND, CONVERT_AND_FUND)}
            assert paid <= {o.id for o in OBLIGATIONS if o.stops_operations}
        assert p.fx_limit in (policy.full_fx_limit, policy.reduced_fx_limit, 0.0)


def test_another_scenario_shows_every_kind_of_change():
    base = planificar(esta_semana(), RATES)
    seen = {"decision": 0, "approval": 0, "reduced_limit": 0, "no_limit": 0, "reserve": 0}
    for seed in range(1, 41):
        p = planificar(al_azar(seed), RATES)
        lista = cambios(base, p)
        seen["decision"] += any(not i.startswith("FX ") for i, _, _ in lista)
        seen["approval"] += any(i.startswith("FX ") for i, _, _ in lista)
        seen["reduced_limit"] += p.fx_limit == TreasuryPolicy().reduced_fx_limit
        seen["no_limit"] += p.fx_limit == 0.0
        seen["reserve"] += p.uses_reserve
    assert all(seen.values()), seen  # in 40 weeks, every kind of decision change shows up at least once


def test_a_customer_email_moves_the_agent_autonomy():
    week = esta_semana()
    calm = planificar(week, RATES)
    disputes = planificar(Semana(week.saldos, week.importes, week.dias, ("none", "disputes_invoice")), RATES)
    confirms = planificar(Semana(week.saldos, week.importes, week.dias, ("confirms_payment", "confirms_payment")),
                          RATES)
    assert disputes.confidence < calm.confidence < confirms.confidence
    assert disputes.fx_limit < calm.fx_limit


def test_less_cash_defers_more_and_never_pays_what_a_person_must_decide():
    week = esta_semana()
    tight = Semana((20_000.0, 0.0, 0.0), week.importes, week.dias)
    p = planificar(tight, RATES)
    assert sum(d.action == DEFER for d in p.decisions) > sum(d.action == DEFER for d in planificar(week, RATES).decisions)
    assert {d.obligation_id: d.action for d in p.decisions}["NEW-5"] == ESCALATE


def test_what_the_video_says_when_globex_disputes_holds_across_fx_moves():
    """The video (part 6): in What if, Globex disputes its invoice → confidence 64%, the agent defers the pound bill
    and converts nothing without a person. It must hold with EUR and GBP 4% cheaper to 6% dearer than on 27-oct."""
    week = esta_semana()
    disputes = Semana(week.saldos, week.importes, week.dias, ("none", "disputes_invoice"))
    for m in (0.96, 0.98, 1.0, 1.02, 1.04, 1.06):
        rates = {"USD": 1.0, "EUR": RATES["EUR"] * m, "GBP": RATES["GBP"] * m}
        p = planificar(disputes, rates)
        acts = {d.obligation_id: d.action for d in p.decisions}
        assert round(p.confidence * 100) == 64 and p.fx_limit == 0.0, m
        assert acts["UK-4"] == DEFER and planificar(week, rates).decisions[3].action == CONVERT_AND_FUND, m
        assert p.conversions and all(c.needs_approval for c in p.conversions), m
