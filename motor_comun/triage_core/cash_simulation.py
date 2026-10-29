"""Piece 2: simulate many cash paths and measure the risk of breaking the reserve floor.

Amounts are in one base currency (the caller converts at current rates).
Time is in whole days from today (day 0).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .collections_model import CollectionsModel, CustomerProfile


@dataclass(frozen=True)
class ExpectedReceipt:
    customer: str
    amount: float
    due_day: int


@dataclass(frozen=True)
class CashRisk:
    p_breach: float          # probability the balance goes below the reserve at any day in the horizon
    expected_min: float      # average of the lowest balance per scenario
    min_p05: float           # bad-case lowest balance (5th percentile)


def _paths(start_cash: float, receipts: list[tuple[ExpectedReceipt, CustomerProfile]],
           outflows: list[tuple[int, float]], horizon_days: int, n_paths: int, seed: int) -> np.ndarray:
    """End-of-day balance of every simulated path: shape (n_paths, horizon_days + 1)."""
    rng = np.random.default_rng(seed)
    days = horizon_days + 1
    flows = np.zeros((n_paths, days))
    for day, amount in outflows:
        if 0 <= day < days:
            flows[:, day] -= amount
    for rec, profile in receipts:
        arrival = rec.due_day + CollectionsModel.sample_arrival_offsets(profile, n_paths, rng)
        inside = arrival < days
        flows[np.nonzero(inside)[0], arrival[inside].astype(int)] += rec.amount
    return start_cash + np.cumsum(flows, axis=1)


def simulate(start_cash: float, receipts: list[tuple[ExpectedReceipt, CustomerProfile]],
             outflows: list[tuple[int, float]], reserve: float, horizon_days: int,
             n_paths: int = 5000, seed: int = 7) -> CashRisk:
    """Monte Carlo over receipt timing. Outflows are fixed (day, amount) payments."""
    path_min = _paths(start_cash, receipts, outflows, horizon_days, n_paths, seed).min(axis=1)
    return CashRisk(float((path_min < reserve).mean()), float(path_min.mean()),
                    float(np.percentile(path_min, 5)))


def cash_bands(start_cash: float, receipts: list[tuple[ExpectedReceipt, CustomerProfile]],
               outflows: list[tuple[int, float]], horizon_days: int, quantiles: tuple[float, ...] = (5, 50, 95),
               n_paths: int = 5000, seed: int = 7) -> dict[float, list[float]]:
    """End-of-day balance at each percentile, over the same paths `simulate` uses (for a chart)."""
    balances = _paths(start_cash, receipts, outflows, horizon_days, n_paths, seed)
    return {q: [float(v) for v in np.percentile(balances, q, axis=0)] for q in quantiles}


def receipts_confidence(receipts: list[tuple[ExpectedReceipt, CustomerProfile]], horizon_days: int) -> float:
    """Amount-weighted probability that the receipts due inside the horizon arrive on time."""
    inside = [(r.amount, p.p_on_time) for r, p in receipts if r.due_day <= horizon_days]
    if not inside:
        return 1.0
    total = sum(a for a, _ in inside)
    return sum(a * p for a, p in inside) / total
