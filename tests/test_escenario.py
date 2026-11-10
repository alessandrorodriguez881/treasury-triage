"""The demo's story, offline, with the scenario data and the rates seen in the sandbox on 27-oct."""
from datetime import date
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from escenario import (EN_FICHA, MODEL, OBLIGATIONS, PAGO_GLOBEX, SALDOS_INICIALES, SENAL_GLOBEX, facturas_ensayo,
                       receipts)
from triage_core.invoices import apply_invoice_checks
from triage_core.signals import Signal
from triage_core.policy import TreasuryPolicy
from triage_core.treasury_decision import CONVERT_AND_FUND, DEFER, ESCALATE, FUND, diff_plans, plan_treasury

RATES = {"USD": 1.0, "EUR": 1.1265, "GBP": 1.3466}
# What Claude should record from the demo inbox: Globex asks for time, Acme confirms; the fake CFO email, nothing
INBOX_SIGNALS = [SENAL_GLOBEX, Signal("msg-102", "Acme", "confirms_payment",
                                      "invoice AC-1184 for 6,000.00 USD is scheduled in tomorrow's payment run")]


def plan(pending_ids, balances, senales=(SENAL_GLOBEX,), usual=MODEL.pool_rate, **kw):
    """Same call as the app: autonomy relative to the portfolio's usual on-time rate."""
    pending = [o for o in OBLIGATIONS if o.id in pending_ids]
    return plan_treasury(pending, balances, RATES, receipts(list(senales), **kw), TreasuryPolicy(),
                         usual_confidence=usual)


def test_h7_the_demo_story_holds_with_the_relative_rule():
    everything = {o.id for o in OBLIGATIONS}
    calm = plan(everything, SALDOS_INICIALES, senales=())
    assert calm.fx_limit == TreasuryPolicy().full_fx_limit and not any(c.needs_approval for c in calm.conversions)
    for senales in ([SENAL_GLOBEX], INBOX_SIGNALS):  # the stand-in flag, and what Claude records from the inbox
        worried = plan(everything, SALDOS_INICIALES, senales=senales)
        assert worried.confidence < MODEL.pool_rate - TreasuryPolicy().drop_for_reduced
        assert [c.needs_approval for c in worried.conversions] == [True, True]


def test_without_emails_the_agent_converts_on_its_own():
    p = plan({o.id for o in OBLIGATIONS}, SALDOS_INICIALES, senales=())
    assert p.conversions and not any(c.needs_approval for c in p.conversions)


def test_the_whole_inbox_still_tells_the_same_story():
    p = plan({o.id for o in OBLIGATIONS}, SALDOS_INICIALES, senales=INBOX_SIGNALS)
    assert all(c.needs_approval for c in p.conversions)
    assert p.action_of("SAAS-3") == DEFER and p.action_of("NEW-5") == ESCALATE


def test_email_makes_both_conversions_wait_for_a_person():
    p = plan({o.id for o in OBLIGATIONS}, SALDOS_INICIALES)
    assert p.action_of("SHIP-1") == FUND and p.action_of("SAAS-3") == DEFER and p.action_of("NEW-5") == ESCALATE
    assert [c.buy_currency for c in p.conversions] == ["EUR", "GBP"]
    assert all(c.needs_approval for c in p.conversions)


def test_reading_the_demo_invoices_keeps_the_same_story():
    """The four known invoices match, so the plan is the same one; Q. Consulting was escalated already."""
    hoy = date(2026, 10, 27)
    checked, checks = apply_invoice_checks(OBLIGATIONS, facturas_ensayo(hoy), hoy, EN_FICHA)
    assert {i for i, problems in checks.items() if problems} == {"NEW-5"}
    with_invoices = plan_treasury(checked, SALDOS_INICIALES, RATES, receipts([SENAL_GLOBEX]), TreasuryPolicy(),
                                  usual_confidence=MODEL.pool_rate)
    without = plan({o.id for o in OBLIGATIONS}, SALDOS_INICIALES)
    assert [(d.obligation_id, d.action) for d in with_invoices.decisions] == \
           [(d.obligation_id, d.action) for d in without.decisions]
    assert with_invoices.conversions == without.conversions


def test_globex_payment_only_changes_the_saas_and_keeps_the_reserve():
    """After the transport is paid (12,003 USD with the ACH fee), Globex pays and the plan is recomputed."""
    pending = {"SUP-2", "SAAS-3", "UK-4", "NEW-5"}
    usd = SALDOS_INICIALES["USD"] - 12_003.0
    before = plan(pending, {"USD": usd, "EUR": 1_200.0, "GBP": 0.0})
    after = plan(pending, {"USD": usd + PAGO_GLOBEX, "EUR": 1_200.0, "GBP": 0.0}, globex_pagado=True)
    assert before.action_of("SUP-2") == CONVERT_AND_FUND and before.action_of("UK-4") == CONVERT_AND_FUND
    assert diff_plans(before, after) == [("SAAS-3", DEFER, FUND)]  # the only decision the new cash changes
    assert after.action_of("NEW-5") == ESCALATE                      # money never overrides policy
    assert after.risk.p_breach <= TreasuryPolicy().max_breach_prob
    # USD left after both conversions and the three payments stays above the 15,000 floor
    spent = sum(c.base_cost for c in after.conversions) + 1_800 + 3
    assert usd + PAGO_GLOBEX - spent > TreasuryPolicy().reserve_floor


def test_the_demo_story_survives_fx_moves():
    """10-nov: with 38,000 USD the story hung on a 27 USD margin, and EUR and GBP 0.8% dearer than on 27-oct changed
    the plan from the start. From the starting balances, the whole story must hold with EUR and GBP from 4% cheaper
    to 6% dearer than on 27-oct, together or in opposite directions."""
    start = ["FUND", "CONVERT_AND_FUND", "DEFER", "CONVERT_AND_FUND", "ESCALATE"]
    acme = Signal("msg-102", "Acme", "confirms_payment", "scheduled in tomorrow's payment run")
    for eur, gbp in [(m, m) for m in (0.96, 0.98, 1.0, 1.02, 1.04, 1.06)] + [(0.97, 1.03), (1.03, 0.97)]:
        rates = {"USD": 1.0, "EUR": RATES["EUR"] * eur, "GBP": RATES["GBP"] * gbp}
        run = lambda pending, balances, senales, **kw: plan_treasury(  # noqa: E731
            [o for o in OBLIGATIONS if o.id in pending], balances, rates, receipts(senales, **kw), TreasuryPolicy(),
            usual_confidence=MODEL.pool_rate)
        everything = {o.id for o in OBLIGATIONS}
        calm = run(everything, SALDOS_INICIALES, [])
        assert [d.action for d in calm.decisions] == start and not any(c.needs_approval for c in calm.conversions)
        for senales in ([SENAL_GLOBEX], [SENAL_GLOBEX, acme]):
            worried = run(everything, SALDOS_INICIALES, senales)
            assert [d.action for d in worried.decisions] == start, (eur, gbp, senales)
            assert [c.needs_approval for c in worried.conversions] == [True, True]
        usd = SALDOS_INICIALES["USD"] - 12_003.0  # after the freight
        pending = everything - {"SHIP-1"}
        before = run(pending, {**SALDOS_INICIALES, "USD": usd}, [SENAL_GLOBEX, acme])
        after = run(pending, {**SALDOS_INICIALES, "USD": usd + PAGO_GLOBEX}, [SENAL_GLOBEX, acme], globex_pagado=True)
        assert diff_plans(before, after) == [("SAAS-3", DEFER, FUND)], (eur, gbp)
