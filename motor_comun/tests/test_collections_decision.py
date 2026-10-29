"""Known-answer tests for piece 7 (Receivables Copilot): which invoice to push, and how. Synthetic data only."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from triage_core.collections_decision import (DISCOUNT, ESCALATE, REMIND, WAIT, CashNeed, Invoice,
                                              ReceivablesPolicy, _options, plan_collections)
from triage_core.cash_simulation import cash_bands, simulate
from triage_core.collections_model import CollectionsModel, CustomerProfile
from triage_core.policy import SIGNAL_LIKELIHOOD

PROFILES = {
    "Northwind": CustomerProfile("Northwind", 20, 0.92, np.array([4])),   # reliable
    "Contoso": CustomerProfile("Contoso", 12, 0.70, np.array([6, 9])),    # usually fine
    "Fabrikam": CustomerProfile("Fabrikam", 10, 0.45, np.array([12])),    # slow: late means after payroll
}
PAYROLL = [CashNeed("payroll", 9_000, 7)]
INVOICES = [
    Invoice("INV-101", "Northwind", 2_500, 3),
    Invoice("INV-102", "Contoso", 4_000, 2),
    Invoice("INV-103", "Fabrikam", 6_000, 4),
    Invoice("INV-104", "Contoso", 1_800, -12),
    Invoice("INV-105", "Fabrikam", 3_200, 5, disputed=True),
]


def test_plenty_of_cash_means_no_needless_reminders_or_discounts():
    plan = plan_collections(INVOICES, PROFILES, start_cash=20_000, needs=PAYROLL)
    assert plan.p_short == 0.0 and plan.safe
    assert [d.action for d in plan.decisions] == [WAIT, WAIT, WAIT, WAIT, ESCALATE]


def test_tight_cash_pushes_only_what_moves_the_payroll_risk():
    plan = plan_collections(INVOICES, PROFILES, start_cash=3_000, needs=PAYROLL)
    assert plan.p_short_if_wait > ReceivablesPolicy().max_short_prob  # doing nothing is not safe
    assert plan.safe and plan.p_short <= ReceivablesPolicy().max_short_prob
    assert any(d.action in (REMIND, DISCOUNT) for d in plan.decisions)
    assert plan.action_of("INV-105") == ESCALATE  # money never overrides a dispute
    for d in plan.decisions:
        if d.action in (REMIND, DISCOUNT):
            assert d.p_after > d.p_before


def test_the_payday_chart_draws_the_same_scenarios_as_the_risk():
    """The plan keeps what it simulated: its own risk can be recomputed from it, and its bad case on payday is
    better than doing nothing (the median can be a bit lower: a discount gives away 2% of an invoice)."""
    plan = plan_collections(INVOICES, PROFILES, start_cash=3_000, needs=PAYROLL)
    outflows = [(n.day, n.amount) for n in PAYROLL]
    assert len(plan.receipts) == len(plan.receipts_if_wait) == len(INVOICES)
    again = simulate(3_000, plan.receipts, outflows, reserve=0.0, horizon_days=7)
    assert again.p_breach == plan.p_short_model
    payday = lambda receipts: cash_bands(3_000, receipts, outflows, 7)[5][-1]
    assert payday(plan.receipts) > payday(plan.receipts_if_wait)


def test_disputed_or_very_late_invoices_go_to_a_person():
    policy = ReceivablesPolicy()
    assert _options(Invoice("X", "Contoso", 1_000, 2, disputed=True), policy) == [ESCALATE]
    assert _options(Invoice("Y", "Contoso", 1_000, -60), policy) == [ESCALATE]


def test_reminders_are_spaced_and_not_sent_long_before_the_due_date():
    policy = ReceivablesPolicy()
    assert REMIND not in _options(Invoice("A", "Contoso", 1_000, 1, reminded_days_ago=2), policy)
    assert REMIND not in _options(Invoice("B", "Contoso", 1_000, 10), policy)
    assert REMIND in _options(Invoice("C", "Contoso", 1_000, -3, reminded_days_ago=9), policy)
    assert _options(Invoice("D", "Contoso", 1_000, 1, promised=True), policy) == [WAIT]  # they confirmed: no chasing


def test_a_big_discount_needs_a_person():
    big = [Invoice("INV-900", "Fabrikam", 12_000, 4)]
    plan = plan_collections(big, PROFILES, start_cash=0, needs=[CashNeed("payroll", 9_000, 7)])
    d = plan.decisions[0]
    assert d.action == DISCOUNT and d.needs_approval  # 2% of 12,000 = 240 > 150 the agent may give alone


def test_a_customer_reply_changes_the_plan():
    worried = dict(PROFILES)
    worried["Contoso"] = CollectionsModel.apply_signal(PROFILES["Contoso"], SIGNAL_LIKELIHOOD["asks_for_more_time"],
                                                      "Contoso email: 'we need two more weeks'")
    before = plan_collections(INVOICES, PROFILES, start_cash=3_000, needs=PAYROLL)
    after = plan_collections(INVOICES, worried, start_cash=3_000, needs=PAYROLL)
    assert after.p_short_if_wait > before.p_short_if_wait
    # the reply makes payday unsafe: the agent says so and hands it to a person instead of hiding it
    assert before.safe and not after.safe and after.notes
    assert after.expected_cost > before.expected_cost


def test_when_nothing_is_safe_it_takes_the_cheapest_plan_not_discounts_at_any_price():
    worried = dict(PROFILES)
    worried["Contoso"] = CollectionsModel.apply_signal(PROFILES["Contoso"], SIGNAL_LIKELIHOOD["asks_for_more_time"], "x")
    cheapest = plan_collections(INVOICES, worried, start_cash=3_000, needs=PAYROLL)
    safest = plan_collections(INVOICES, worried, start_cash=3_000, needs=PAYROLL,
                              policy=ReceivablesPolicy(if_unsafe="safest"))
    assert not cheapest.safe and not safest.safe
    assert cheapest.expected_cost <= safest.expected_cost  # the shortfall penalty is already in the cost
    discounts = lambda p: sum(d.action == DISCOUNT for d in p.decisions)
    assert discounts(cheapest) < discounts(safest)


def test_when_nothing_is_safe_the_agent_says_so():
    plan = plan_collections(INVOICES, PROFILES, start_cash=0, needs=[CashNeed("payroll", 50_000, 7)])
    assert not plan.safe and plan.notes
