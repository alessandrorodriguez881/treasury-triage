"""Piece 7 (Receivables Copilot): what to do with each open invoice so the money arrives when it is needed.

For each open invoice the agent picks WAIT, REMIND, DISCOUNT (an early-payment discount), PLAN (a payment plan: part
now, the rest later, only for a customer who asked for more time) or ESCALATE (a person).
Among all combinations it takes the one with the lowest expected cost whose probability of being short of cash
on the day the money is needed (payroll, rent) stays under the policy limit. If none is safe enough, it takes
the safest one and says so. How much each action moves the odds is policy, never the model's free text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Callable

from .cash_simulation import ExpectedReceipt, simulate
from .collections_model import CollectionsModel, CustomerProfile

WAIT, REMIND, DISCOUNT, PLAN, ESCALATE = "WAIT", "REMIND", "DISCOUNT", "PLAN", "ESCALATE"
RULED_OUT = {DISCOUNT: "early-payment discount", PLAN: "payment plan"}  # what a person can say no to


@dataclass(frozen=True)
class Invoice:
    id: str
    customer: str
    amount: float
    due_day: int                          # days from today; negative = already overdue
    disputed: bool = False
    reminded_days_ago: int | None = None  # None = never reminded
    promised: bool = False                # the customer confirmed when they will pay: no chasing
    asked_for_time: bool = False          # the customer asked for more time: a payment plan, never a discount
    ruled_out: frozenset[str] = frozenset()  # DISCOUNT or PLAN a person rejected for this invoice: plan without it


@dataclass(frozen=True)
class CashNeed:
    what: str
    amount: float
    day: int


@dataclass(frozen=True)
class ReceivablesPolicy:
    max_short_prob: float = 0.05          # accepted probability of not having the money on the day it is needed
    shortfall_penalty: float = 1_500.0    # cost of missing a need (late payroll, overdraft fees)
    reminder_cost: float = 3.0            # goodwill cost of one more reminder
    reminder_lift: float = 1.5            # odds multiplier of paying on time after a personal reminder
    min_days_between_reminders: int = 5
    remind_from_days_before_due: int = 3
    discount_pct: float = 0.02
    discount_lift: float = 3.0            # odds multiplier with "2% off if you pay within 5 days"
    discount_window_days: int = 5
    autonomous_discount_limit: float = 150.0  # USD the agent may give away alone; above it, a person approves
    overdue_likelihood: float = 0.5       # being already late lowers the odds of paying now
    escalate_overdue_days: int = 45
    # When no combination is safe enough: "cheapest" takes the lowest expected cost (the shortfall penalty is
    # in it); "safest" takes the lowest risk at any price. "safest" handed out discounts that did not save the
    # payroll in the 1,000-week simulation (27-oct), so "cheapest" is the default
    if_unsafe: str = "cheapest"
    # Payment plans (29-oct): to a customer who asked for more time, a discount for paying early makes no sense (they
    # just said they cannot pay now); the agent can offer part now and the rest later. Changing the terms of an
    # invoice is the owner's call, so every plan needs approval. OFF by default: in the 1,000-week simulation (seeds
    # 2042 and 2043, hypotheses written first) half the money arrived after payday, the agent collected $300-$470 a
    # week less from those customers and was no safer on payday. An owner who values the relationship can turn it on
    payment_plans: bool = False
    plan_first_pct: float = 0.5           # share of the invoice due now
    plan_first_days: int = 3              # ...within these days
    plan_lift: float = 3.0                # odds multiplier of paying that first part on time
    plan_rest_days: int = 30              # the rest, this many days after the original due date
    plan_cost: float = 3.0                # goodwill/admin cost of changing the terms


@dataclass
class InvoiceDecision:
    invoice_id: str
    action: str
    reason: str
    p_before: float
    p_after: float
    expected_cost: float
    needs_approval: bool = False


@dataclass
class CollectionsPlan:
    decisions: list[InvoiceDecision]
    p_short: float            # with the chosen actions
    p_short_if_wait: float    # if the agent did nothing
    expected_cost: float
    safe: bool                # p_short within the policy limit
    notes: list[str] = field(default_factory=list)
    p_short_model: float | None = None  # before the weekly risk check (what the simulation of scenarios said)
    # what the simulation saw (receipt, profile) with the chosen actions and if the agent did nothing: for a chart
    receipts: list = field(default_factory=list)
    receipts_if_wait: list = field(default_factory=list)

    def action_of(self, invoice_id: str) -> str:
        return next(d.action for d in self.decisions if d.invoice_id == invoice_id)


def _options(inv: Invoice, policy: ReceivablesPolicy) -> list[str]:
    if inv.disputed or -inv.due_day > policy.escalate_overdue_days:
        return [ESCALATE]
    if inv.promised:
        return [WAIT]
    if inv.asked_for_time and policy.payment_plans:
        out = [WAIT, PLAN]  # who just asked for time is not pressed: no reminder, no money off for paying early
    else:
        out = [WAIT, DISCOUNT]
        spaced = inv.reminded_days_ago is None or inv.reminded_days_ago >= policy.min_days_between_reminders
        if spaced and inv.due_day <= policy.remind_from_days_before_due:
            out.insert(1, REMIND)
    return [a for a in out if a == WAIT or a not in inv.ruled_out]  # a person said no: the agent plans without it


def _receipt(inv: Invoice, profile: CustomerProfile, action: str,
             policy: ReceivablesPolicy) -> tuple[list[tuple[ExpectedReceipt, CustomerProfile]], CustomerProfile, float]:
    """The receipts as the simulation sees them under this action (a payment plan splits the invoice in two), the
    profile of the first one, and the expected cost of the action itself."""
    due, amount = max(inv.due_day, 0), inv.amount
    if inv.due_day < 0:
        profile = CollectionsModel.apply_signal(profile, policy.overdue_likelihood, f"{-inv.due_day} days overdue")
    if action == PLAN:
        first = CollectionsModel.apply_signal(profile, policy.plan_lift, "payment plan: part now")
        now = round(inv.amount * policy.plan_first_pct, 2)
        return ([(ExpectedReceipt(inv.customer, now, min(due, policy.plan_first_days)), first),
                 (ExpectedReceipt(inv.customer, round(inv.amount - now, 2), due + policy.plan_rest_days), profile)],
                first, policy.plan_cost)
    cost = 0.0
    if action == REMIND:
        profile = CollectionsModel.apply_signal(profile, policy.reminder_lift, "personal reminder")
        cost = policy.reminder_cost
    elif action == DISCOUNT:
        profile = CollectionsModel.apply_signal(profile, policy.discount_lift, "early-payment discount")
        due = min(due, policy.discount_window_days)
        cost = policy.discount_pct * inv.amount * profile.p_on_time  # only paid if they pay in the window
        amount = inv.amount * (1 - policy.discount_pct)  # the cash that arrives is the discounted amount
    return [(ExpectedReceipt(inv.customer, amount, due), profile)], profile, cost


def plan_collections(invoices: list[Invoice], profiles: dict[str, CustomerProfile], start_cash: float,
                     needs: list[CashNeed], policy: ReceivablesPolicy = ReceivablesPolicy(),
                     seed: int = 7, risk_check: Callable[[float], float] | None = None) -> CollectionsPlan:
    """risk_check: the agent's correction of its weekly risk (`collections_calibration.fit_risk`), if any."""
    horizon = max(n.day for n in needs)
    outflows = [(n.day, n.amount) for n in needs]
    menu = [_options(inv, policy) for inv in invoices]

    def evaluate(actions: tuple[str, ...]):
        parts = [_receipt(inv, profiles[inv.customer], a, policy) for inv, a in zip(invoices, actions)]
        risk = simulate(start_cash, [x for rs, _, _ in parts for x in rs], outflows, reserve=0.0,
                        horizon_days=horizon, seed=seed)
        own = sum(c for _, _, c in parts)
        p = risk_check(risk.p_breach) if risk_check else risk.p_breach
        return p, own + policy.shortfall_penalty * p, parts, risk.p_breach

    results = []
    for actions in product(*menu):
        p_short, cost, parts, raw = evaluate(actions)
        interventions = sum(a not in (WAIT, ESCALATE) for a in actions)
        results.append((actions, p_short, cost, parts, interventions, raw))
    wait = tuple(m[0] if m == [ESCALATE] else WAIT for m in menu)
    p_wait, wait_parts = next((r[1], r[3]) for r in results if r[0] == wait)
    safe = [r for r in results if r[1] <= policy.max_short_prob]
    pool = safe or results
    by_cost = lambda r: (round(r[2], 2), r[4])
    key = by_cost if safe or policy.if_unsafe == "cheapest" else (lambda r: (round(r[1], 4), round(r[2], 2)))
    actions, p_short, cost, parts, _, raw = min(pool, key=key)

    decisions = []
    for inv, a, (_, prof, c) in zip(invoices, actions, parts):
        before = _receipt(inv, profiles[inv.customer], WAIT, policy)[1].p_on_time
        if a == ESCALATE:
            why = "disputed: a person decides" if inv.disputed else f"{-inv.due_day} days overdue: a person decides"
        elif a == REMIND:
            why = f"a personal reminder lifts on-time odds {before:.0%} -> {prof.p_on_time:.0%}"
        elif a == PLAN:
            why = (f"asked for more time: {policy.plan_first_pct:.0%} now (~${policy.plan_first_pct * inv.amount:,.0f}) "
                   f"and the rest {policy.plan_rest_days} days later; odds of the first part {before:.0%} -> "
                   f"{prof.p_on_time:.0%}")
        elif a == DISCOUNT:
            why = (f"{policy.discount_pct:.0%} off (~${policy.discount_pct * inv.amount:,.0f}) if paid within "
                   f"{policy.discount_window_days} days: odds {before:.0%} -> {prof.p_on_time:.0%}")
        elif inv.ruled_out:
            why = f"on-time odds {before:.0%}: the agent waits"
        elif inv.promised:
            why = f"the customer confirmed the payment (odds {before:.0%}): no chasing"
        elif safe:
            why = f"on-time odds {before:.0%}: the cash need is covered without pushing this one"
        else:
            why = f"on-time odds {before:.0%}: pushing it would not change the cash risk enough"
        if inv.ruled_out and a != ESCALATE:
            why = f"{' and '.join(RULED_OUT[x] for x in sorted(inv.ruled_out))} rejected by a person; {why}"
        approval =a == PLAN or (a == DISCOUNT and policy.discount_pct * inv.amount > policy.autonomous_discount_limit)
        decisions.append(InvoiceDecision(inv.id, a, why, before, prof.p_on_time, round(c, 2), approval))
    notes = [] if safe else [f"no combination keeps the shortfall risk under {policy.max_short_prob:.0%}: the agent "
                             "takes the cheapest plan counting the risk, and a person should look at other cash "
                             "(credit line, delaying a payment)"]
    return CollectionsPlan(decisions, p_short, p_wait, round(cost, 2), bool(safe), notes, raw,
                           [x for rs, _, _ in parts for x in rs], [x for rs, _, _ in wait_parts for x in rs])
