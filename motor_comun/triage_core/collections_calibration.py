"""Piece 8: the agent checks its own forecasts against what really happened.

Every invoice the agent planned for leaves a record: the on-time probability it believed, whether it was
already overdue, what it did (wait, remind, discount) and whether the customer then paid on time. A small
logistic regression on those records corrects what the policy can only assume:
- how far off the base forecast is (intercept and slope on the log-odds),
- how much being overdue lowers the odds,
- how much a reminder and a discount really lift them,
- how far to trust each kind of customer reply (a confirmation, a request for more time, a dispute).

The fit starts from the policy's assumptions (a Gaussian prior around them), so with few records nothing
changes, and it moves only as far as the evidence pushes it. Disputed invoices are in too: a person handles
them, but the cash forecast still has to know how rarely they are paid in time. They were left out until 28-oct,
and in the 1,000-week simulation the agent then believed a disputed invoice arrived before payroll in 54-67% of
cases, against 36-40% in reality. The result is a new policy and a corrected profile; the decision rule itself
does not change.

A second, smaller check works on the whole week (`fit_risk`): the chance of being short on payday the agent
predicted for the plan it chose, against whether it really was short. Getting each invoice about right is not
enough: small errors on many invoices add up in the tail, and in the simulation the agent still expected 2-7
points less risk than it ran (28-oct). The correction is applied to every plan it compares.

Both agents use it. Receivables Copilot (PayPal) learns all of the above. Treasury Triage (Airwallex) only waits
for its customers' money (no reminders, no discounts, nothing overdue), so those columns stay at the policy's
values and only the base forecast and the replies are corrected.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

import numpy as np

from .collections_decision import DISCOUNT, REMIND, ReceivablesPolicy
from .collections_model import CustomerProfile

_EPS = 1e-4


@dataclass(frozen=True)
class Outcome:
    p_believed: float     # the agent's on-time probability before the overdue adjustment and the action
    overdue: bool
    action: str
    paid_on_time: bool
    reply: str | None = None  # confirms_payment / asks_for_more_time / disputes_invoice / None (no reply)


@dataclass(frozen=True)
class Calibration:
    intercept: float = 0.0
    slope: float = 1.0
    overdue_lr: float = 0.5
    reminder_lift: float = 1.5
    discount_lift: float = 3.0
    confirmed_lr: float = 1.0   # extra odds factor for a confirmed payment, on top of the policy's signal
    asked_lr: float = 1.0       # same for a request for more time
    disputed_lr: float = 1.0    # same for a dispute
    n: int = 0

    def policy(self, base: ReceivablesPolicy) -> ReceivablesPolicy:
        return replace(base, overdue_likelihood=self.overdue_lr, reminder_lift=self.reminder_lift,
                       discount_lift=self.discount_lift)

    def profile(self, prof: CustomerProfile, reply: str | None = None) -> CustomerProfile:
        if self.n == 0:
            return prof
        z = self.intercept + self.slope * _logit(prof.p_on_time)
        z += math.log({"confirms_payment": self.confirmed_lr, "asks_for_more_time": self.asked_lr,
                       "disputes_invoice": self.disputed_lr}.get(reply, 1.0))
        return replace(prof, p_on_time=float(1 / (1 + math.exp(-z))),
                       adjustments=prof.adjustments + [f"calibrated on {self.n} past invoices"])

    def describe(self) -> str:
        return (f"learned from {self.n} past invoices: overdue odds x{self.overdue_lr:.2f}, "
                f"reminder x{self.reminder_lift:.2f}, discount x{self.discount_lift:.2f}, "
                f"confirmation x{self.confirmed_lr:.2f}, more-time request x{self.asked_lr:.2f}, "
                f"dispute x{self.disputed_lr:.2f}")


def _logit(p: float) -> float:
    p = min(max(p, _EPS), 1 - _EPS)
    return math.log(p / (1 - p))


def _penalized_logit(X: np.ndarray, y: np.ndarray, prior: np.ndarray, prior_sd: float,
                     iterations: int = 25) -> np.ndarray:
    """Logistic regression with a Gaussian prior around `prior` (Newton steps)."""
    precision = np.eye(len(prior)) / prior_sd ** 2
    beta = prior.copy()
    for _ in range(iterations):
        p = 1 / (1 + np.exp(-(X @ beta)))
        grad = X.T @ (y - p) - precision @ (beta - prior)
        hess = (X * (p * (1 - p))[:, None]).T @ X + precision
        step = np.linalg.solve(hess, grad)
        beta += step
        if np.abs(step).max() < 1e-6:
            break
    return beta


def fit(outcomes: list[Outcome], base: ReceivablesPolicy = ReceivablesPolicy(), prior_sd: float = 0.5,
        iterations: int = 25) -> Calibration:
    """Penalized logistic regression around the policy's assumptions."""
    if not outcomes:
        return Calibration(overdue_lr=base.overdue_likelihood, reminder_lift=base.reminder_lift,
                           discount_lift=base.discount_lift)
    X = np.array([[1.0, _logit(o.p_believed), float(o.overdue), float(o.action == REMIND),
                   float(o.action == DISCOUNT), float(o.reply == "confirms_payment"),
                   float(o.reply == "asks_for_more_time"), float(o.reply == "disputes_invoice")] for o in outcomes])
    y = np.array([float(o.paid_on_time) for o in outcomes])
    prior = np.array([0.0, 1.0, math.log(base.overdue_likelihood), math.log(base.reminder_lift),
                      math.log(base.discount_lift), 0.0, 0.0, 0.0])
    beta = _penalized_logit(X, y, prior, prior_sd, iterations)
    b0, b1, b_over, b_rem, b_disc, b_conf, b_ask, b_disp = (float(v) for v in beta)
    return Calibration(b0, b1, math.exp(b_over), math.exp(b_rem), math.exp(b_disc), math.exp(b_conf),
                       math.exp(b_ask), math.exp(b_disp), len(outcomes))


@dataclass(frozen=True)
class RiskCalibration:
    """Correction of the weekly risk on the log-odds: identity until there is a track record."""
    intercept: float = 0.0
    slope: float = 1.0
    n: int = 0

    def __call__(self, p: float) -> float:
        if self.n == 0:
            return p
        return float(1 / (1 + math.exp(-(self.intercept + self.slope * _logit(p)))))

    def describe(self) -> str:
        return (f"weekly risk checked on {self.n} past weeks: a predicted 10% means "
                f"{self(0.10):.0%}, 30% means {self(0.30):.0%}")


def fit_risk(weeks: list[tuple[float, bool]], prior_sd: float = 0.5) -> RiskCalibration:
    """weeks: (chance of being short the agent predicted for the plan it chose, whether it really was short)."""
    if not weeks:
        return RiskCalibration()
    X = np.array([[1.0, _logit(p)] for p, _ in weeks])
    y = np.array([float(short) for _, short in weeks])
    b0, b1 = (float(v) for v in _penalized_logit(X, y, np.array([0.0, 1.0]), prior_sd))
    return RiskCalibration(b0, b1, len(weeks))
