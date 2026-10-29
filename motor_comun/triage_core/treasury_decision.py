"""Piece 3 (Treasury Triage): decide what to fund, convert, defer or escalate when cash is short.

The rule: among all combinations of obligations to pay today, pick the one with the lowest
expected cost whose probability of breaking the reserve floor stays under the policy limit.
One exception: if keeping the reserve means deferring the bills that stop the business, the plan may pay just
those from the reserve, as long as cash stays above zero, and a person approves it (`Plan.uses_reserve`).
Policy exceptions are never decided by the agent: they go to a person.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
import math

from .cash_simulation import CashRisk, ExpectedReceipt, receipts_confidence, simulate
from .collections_model import CustomerProfile
from .policy import FEE_CUSHION, PAYOUT_FEES, TreasuryPolicy

FUND, CONVERT_AND_FUND, DEFER, ESCALATE = "FUND", "CONVERT_AND_FUND", "DEFER", "ESCALATE"


@dataclass(frozen=True)
class Obligation:
    id: str
    payee: str
    currency: str
    amount: float
    due_day: int
    late_cost_per_day: float = 0.0      # base currency
    stops_operations: bool = False
    policy_exception: str | None = None  # e.g. "new payee, not on the approved list"
    transfer_method: str = "LOCAL"       # LOCAL or SWIFT


@dataclass(frozen=True)
class Conversion:
    sell_currency: str
    buy_currency: str
    buy_amount: float
    base_cost: float
    needs_approval: bool


@dataclass
class Decision:
    obligation_id: str
    action: str
    reason: str


@dataclass
class Plan:
    decisions: list[Decision]
    conversions: list[Conversion]
    risk: CashRisk
    confidence: float
    fx_limit: float
    expected_cost: float
    notes: list[str] = field(default_factory=list)
    uses_reserve: bool = False  # pays the must-pay bills from the reserve: a person approves those payments
    outflows: list[tuple[int, float]] = field(default_factory=list)  # (day, base amount) the plan pays out

    def action_of(self, obligation_id: str) -> str:
        return next(d.action for d in self.decisions if d.obligation_id == obligation_id)


def _fee(ob: Obligation) -> float:
    return PAYOUT_FEES.get((ob.transfer_method, ob.currency), 0.0)


def _defer_cost(ob: Obligation, policy: TreasuryPolicy) -> float:
    return ob.late_cost_per_day * policy.defer_days + (policy.stop_operations_penalty if ob.stops_operations else 0.0)


def _conversions(paid: list[Obligation], balances: dict[str, float], rates: dict[str, float],
                 base: str) -> list[tuple[str, float]]:
    """Minimum amount to buy per currency so every paid obligation (plus its fee, with a cushion) is covered."""
    need: dict[str, float] = {}
    for ob in paid:
        need[ob.currency] = need.get(ob.currency, 0.0) + ob.amount + _fee(ob) * (1 + FEE_CUSHION)
    out = []
    for cur, amt in sorted(need.items()):
        short = amt - balances.get(cur, 0.0)
        if cur != base and short > 0:
            out.append((cur, math.ceil(short * 100) / 100))
    return out


def plan_treasury(obligations: list[Obligation], balances: dict[str, float], rates: dict[str, float],
                  receipts: list[tuple[ExpectedReceipt, CustomerProfile]], policy: TreasuryPolicy,
                  base: str = "USD", seed: int = 7, usual_confidence: float | None = None) -> Plan:
    """rates[c] = units of base currency per 1 unit of c (rates[base] == 1).

    usual_confidence: the portfolio's usual on-time rate (CollectionsModel.pool_rate). With it, autonomy
    depends on how much worse than usual this week's forecast is; without it, on fixed thresholds."""
    to_base = lambda cur, amt: amt * rates[cur]
    start_cash = sum(to_base(c, a) for c, a in balances.items())
    escalated = [ob for ob in obligations if ob.policy_exception]
    candidates = [ob for ob in obligations if not ob.policy_exception]

    best = None
    for k in range(len(candidates), -1, -1):
        for paid in combinations(candidates, k):
            paid = list(paid)
            convs = _conversions(paid, balances, rates, base)
            fx_cost = sum(policy.fx_spread * to_base(c, a) for c, a in convs)
            # Conversions are booked today with base currency; each payment leaves on its due day.
            base_today = sum(to_base(c, a) for c, a in convs) + fx_cost \
                + sum(ob.amount + _fee(ob) for ob in paid if ob.currency == base and ob.due_day == 0)
            if base_today > balances.get(base, 0.0):
                continue
            outflows = [(0, fx_cost)] + [(ob.due_day, to_base(ob.currency, ob.amount + _fee(ob))) for ob in paid]
            deferred = [ob for ob in candidates if ob not in paid]
            cost = fx_cost + sum(to_base(ob.currency, _fee(ob)) for ob in paid) \
                + sum(_defer_cost(ob, policy) for ob in deferred)
            risk = simulate(start_cash, receipts, outflows, policy.reserve_floor,
                            policy.horizon_days, seed=seed)
            dips = False
            if risk.p_breach > policy.max_breach_prob:
                # only the bills that stop the business may use the reserve, and never below zero cash
                must_pay_only = paid and all(ob.stops_operations for ob in paid) \
                    and len(paid) == sum(ob.stops_operations for ob in candidates)
                if not (policy.reserve_covers_must_pay and must_pay_only and simulate(
                        start_cash, receipts, outflows, 0.0, policy.horizon_days, seed=seed).p_breach
                        <= policy.max_breach_prob):
                    continue
                dips = True
            key = (round(cost, 2), -sum(to_base(o.currency, o.amount) for o in paid))
            if best is None or key < best[0]:
                best = (key, paid, convs, risk, cost, dips, outflows)

    confidence = receipts_confidence(receipts, policy.horizon_days)
    fx_limit = policy.autonomous_fx_limit(confidence, usual_confidence)
    if best is None:  # even paying nothing breaks the floor: a person must decide
        risk = simulate(start_cash, receipts, [], policy.reserve_floor, policy.horizon_days, seed=seed)
        decisions = [Decision(ob.id, ESCALATE, "cash is already at risk of breaking the reserve floor")
                     for ob in obligations]
        return Plan(decisions, [], risk, confidence, fx_limit, float("nan"))

    _, paid, convs, risk, cost, dips, outflows = best
    converted = {c for c, _ in convs}
    decisions = []
    for ob in obligations:
        if ob.policy_exception:
            decisions.append(Decision(ob.id, ESCALATE, f"policy exception: {ob.policy_exception}"))
        elif ob in paid:
            act = CONVERT_AND_FUND if ob.currency in converted else FUND
            why = "halts operations if unpaid" if ob.stops_operations else "affordable within the reserve"
            if dips:
                why += "; paid from the reserve, so a person approves"
            decisions.append(Decision(ob.id, act, why))
        else:
            decisions.append(Decision(ob.id, DEFER, f"deferring costs ~{_defer_cost(ob, policy):,.0f} {base} "
                                                    f"and frees {to_base(ob.currency, ob.amount):,.0f} {base}"))
    conversions = [Conversion(base, c, a, round(to_base(c, a), 2), to_base(c, a) > fx_limit) for c, a in convs]
    notes = [f"{len(escalated)} obligation(s) need a person"] if escalated else []
    if dips:
        notes.append("keeping the reserve would stop the business: the plan pays only the must-pay bills, from the "
                     "reserve, and a person approves it")
    return Plan(decisions, conversions, risk, confidence, fx_limit, round(cost, 2), notes, dips, outflows)


def diff_plans(old: Plan, new: Plan) -> list[tuple[str, str, str]]:
    """Decisions that changed between two plans: (obligation_id, old_action, new_action)."""
    before = {d.obligation_id: d.action for d in old.decisions}
    return [(d.obligation_id, before.get(d.obligation_id, "-"), d.action)
            for d in new.decisions if before.get(d.obligation_id) != d.action]
