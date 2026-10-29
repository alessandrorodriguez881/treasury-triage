"""Policy lives in code, not in prompts: reserve, risk tolerance and how much the agent may do alone."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TreasuryPolicy:
    reserve_floor: float = 15_000.0      # base currency; never plan to go below it
    max_breach_prob: float = 0.05        # accepted probability of touching the floor in the horizon
    horizon_days: int = 3                # 72 hours
    defer_days: int = 7                  # a deferral pushes a payment one week
    stop_operations_penalty: float = 5_000.0  # cost of not paying something that halts the business
    fx_spread: float = 0.005             # cost of converting, as a share of the amount
    # Autonomy tied to forecast confidence
    full_fx_limit: float = 10_000.0      # base currency the agent may convert alone when confident
    reduced_fx_limit: float = 2_500.0
    high_confidence: float = 0.85        # fixed thresholds: used only when the portfolio's usual rate is unknown
    low_confidence: float = 0.60
    # Relative thresholds (27-oct): a person is asked when this week is worse than USUAL for this portfolio.
    # Fixed thresholds asked for approval in ~80 % of weeks when customers pay on time 75 % of the time.
    drop_for_reduced: float = 0.05       # confidence more than 5 points below usual -> reduced limit
    drop_for_none: float = 0.15          # more than 15 points below usual -> nothing without a person
    min_confidence: float = 0.50         # below a coin flip, always a person
    # If keeping the reserve means deferring the bills that stop the business, the agent may plan to pay just
    # those from the reserve (never below zero cash), and a person approves it. Without it, the business stopped
    # in ~10% of the simulated weeks and that was two thirds of the agent's cost (28-oct)
    reserve_covers_must_pay: bool = True

    def autonomous_fx_limit(self, confidence: float, usual: float | None = None) -> float:
        if usual is None:
            if confidence >= self.high_confidence:
                return self.full_fx_limit
            return self.reduced_fx_limit if confidence >= self.low_confidence else 0.0
        drop = usual - confidence
        if confidence < self.min_confidence or drop > self.drop_for_none:
            return 0.0
        return self.reduced_fx_limit if drop > self.drop_for_reduced else self.full_fx_limit


# Flat fee per payout, charged in the payout currency, keyed by (transfer_method, currency).
# Read from real Airwallex sandbox transfers on 27-oct (fee_amount); the guide's 12.85 EUR SWIFT was outdated.
# Airwallex keeps the fee even when a transfer fails, so the plan must reserve it up front.
# The fees move a few cents during the day (SWIFT EUR 13.45-13.49, Faster Payments 2.25-2.26 on 27-oct):
# these are the highest seen, and conversions buy FEE_CUSHION more on top.
PAYOUT_FEES = {
    ("LOCAL", "USD"): 3.00,   # ACH
    ("LOCAL", "EUR"): 2.69,   # SEPA
    ("LOCAL", "GBP"): 2.26,   # Faster Payments (CHAPS costs 11.26)
    ("SWIFT", "EUR"): 13.49,
}
# Buying a currency to pay a bill: the fee is sized 5% higher. Buying it exact failed on 27-oct, when the
# SWIFT fee rose 3 cents between the plan and the payment (balance_insufficient)
FEE_CUSHION = 0.05

# How much a text signal changes the odds of an on-time payment. Reviewed by a person, kept in code.
SIGNAL_LIKELIHOOD = {
    "confirms_payment": 2.0,
    "asks_for_more_time": 0.35,
    "disputes_invoice": 0.2,
    "no_reply": 0.8,
}
