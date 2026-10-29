"""Piece 1: probability that a customer pays on time, and how late they pay.

Each customer's estimate is shrunk towards the portfolio average, so a customer
with two invoices is not judged as "always on time" or "always late".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import math

import numpy as np


@dataclass(frozen=True)
class PaidInvoice:
    customer: str
    due: date
    paid: date | None  # None = still unpaid
    amount: float

    @property
    def days_late(self) -> int | None:
        return None if self.paid is None else max(0, (self.paid - self.due).days)


@dataclass
class CustomerProfile:
    customer: str
    n_invoices: int
    p_on_time: float           # shrunk probability of paying by the due date
    late_days: np.ndarray      # sample of days late for late payments (pooled if few)
    adjustments: list[str] = field(default_factory=list)


class CollectionsModel:
    """Beta-binomial shrinkage for P(on time) + empirical delay distribution.

    How hard to shrink is estimated from the portfolio (empirical Bayes) unless a number is given: a fixed
    strength of 4 was too weak and left the forecast badly calibrated in the 1,000-week simulation (27-oct).
    """

    def __init__(self, history: list[PaidInvoice], prior_strength: float | str = "auto", min_late_obs: int = 3):
        settled = [h for h in history if h.paid is not None]
        if not settled:
            raise ValueError("need at least one settled invoice")
        on_time = np.array([h.days_late == 0 for h in settled])
        self.history = settled  # to rebuild the model with more invoices
        self.pool_rate = float(on_time.mean())
        self.min_late_obs = min_late_obs
        self.pool_late = np.array([h.days_late for h in settled if h.days_late > 0] or [7])
        self._by_customer: dict[str, list[PaidInvoice]] = {}
        for h in settled:
            self._by_customer.setdefault(h.customer, []).append(h)
        self.prior_strength = self._fit_prior_strength() if prior_strength == "auto" else float(prior_strength)

    def _fit_prior_strength(self) -> float:
        """Prior strength that best explains how much customers really differ: beta-binomial marginal
        likelihood on a grid. Customers that look alike get pooled hard; very different ones keep their own rate."""
        mu = min(max(self.pool_rate, 1e-3), 1 - 1e-3)
        counts = [(sum(r.days_late == 0 for r in rows), len(rows)) for rows in self._by_customer.values()]
        betaln = lambda x, y: math.lgamma(x) + math.lgamma(y) - math.lgamma(x + y)

        def loglik(s: float) -> float:
            a, b = mu * s, (1 - mu) * s
            return sum(betaln(k + a, n - k + b) - betaln(a, b) for k, n in counts)

        return float(max(np.geomspace(1, 500, 60), key=loglik))

    def profile(self, customer: str) -> CustomerProfile:
        rows = self._by_customer.get(customer, [])
        k = sum(r.days_late == 0 for r in rows)
        a = self.pool_rate * self.prior_strength
        b = (1 - self.pool_rate) * self.prior_strength
        p = (k + a) / (len(rows) + a + b)
        late = np.array([r.days_late for r in rows if r.days_late > 0])
        if len(late) < self.min_late_obs:
            late = np.concatenate([late, self.pool_late])
        return CustomerProfile(customer, len(rows), float(p), late)

    @staticmethod
    def apply_signal(profile: CustomerProfile, likelihood_ratio: float, reason: str) -> CustomerProfile:
        """Update P(on time) with evidence from text, e.g. an email that contradicts the forecast.

        likelihood_ratio < 1 lowers confidence, > 1 raises it. The ratios live in policy code,
        never in the model's free text.
        """
        odds = profile.p_on_time / (1 - profile.p_on_time)
        odds *= likelihood_ratio
        p = odds / (1 + odds)
        return CustomerProfile(profile.customer, profile.n_invoices, float(p), profile.late_days,
                               profile.adjustments + [reason])

    @staticmethod
    def sample_arrival_offsets(profile: CustomerProfile, n: int, rng: np.random.Generator) -> np.ndarray:
        """Days after the due date at which the payment arrives, one draw per scenario."""
        on_time = rng.random(n) < profile.p_on_time
        late = rng.choice(profile.late_days, size=n, replace=True)
        return np.where(on_time, 0, late)
