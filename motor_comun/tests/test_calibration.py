"""The agent's self-check: with no track record nothing changes; with a long one it finds the real effects."""
import math
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from triage_core.collections_calibration import Outcome, fit, fit_risk
from triage_core.collections_decision import DISCOUNT, REMIND, WAIT, ReceivablesPolicy
from triage_core.collections_model import CustomerProfile

POLICY = ReceivablesPolicy()


def track_record(n, rng, overdue_lr, reminder_lift, discount_lift, bias=0.0):
    out = []
    for _ in range(n):
        p = float(rng.uniform(0.3, 0.95))
        overdue, action = bool(rng.random() < 0.5), str(rng.choice([WAIT, REMIND, DISCOUNT]))
        z = math.log(p / (1 - p)) + bias + overdue * math.log(overdue_lr)
        z += {REMIND: math.log(reminder_lift), DISCOUNT: math.log(discount_lift)}.get(action, 0.0)
        out.append(Outcome(p, overdue, action, bool(rng.random() < 1 / (1 + math.exp(-z)))))
    return out


def test_with_no_track_record_the_policy_and_profile_stay_as_they_are():
    cal = fit([], POLICY)
    assert cal.policy(POLICY) == POLICY
    prof = CustomerProfile("C", 10, 0.8, np.array([3]))
    assert cal.profile(prof).p_on_time == 0.8


def test_a_long_track_record_finds_the_real_effects():
    rng = np.random.default_rng(0)
    cal = fit(track_record(20_000, rng, overdue_lr=0.3, reminder_lift=1.2, discount_lift=2.0, bias=-0.4), POLICY)
    assert cal.overdue_lr == pytest.approx(0.3, rel=0.15)
    assert cal.reminder_lift == pytest.approx(1.2, rel=0.15)
    assert cal.discount_lift == pytest.approx(2.0, rel=0.15)
    assert cal.intercept == pytest.approx(-0.4, abs=0.1) and cal.slope == pytest.approx(1.0, abs=0.1)


def test_a_short_track_record_barely_moves_the_assumptions():
    rng = np.random.default_rng(1)
    cal = fit(track_record(15, rng, overdue_lr=0.3, reminder_lift=1.0, discount_lift=1.0), POLICY)
    assert abs(math.log(cal.discount_lift) - math.log(POLICY.discount_lift)) < 0.5  # the prior holds it


def test_disputes_are_learned_too():
    # the policy says a dispute cuts the odds x0.2; here a disputed invoice is almost never paid in time
    rng = np.random.default_rng(3)
    record = track_record(3_000, rng, 0.5, 1.5, 3.0)
    for _ in range(300):
        p = float(rng.uniform(0.1, 0.4))  # what the agent believed after the policy's x0.2
        record.append(Outcome(p, False, WAIT, bool(rng.random() < 0.05), "disputes_invoice"))
    cal = fit(record, POLICY)
    prof = CustomerProfile("C", 10, 0.3, np.array([3]))
    assert cal.disputed_lr < 0.4 and cal.profile(prof, "disputes_invoice").p_on_time < 0.15
    assert cal.profile(prof).p_on_time == pytest.approx(0.3, abs=0.05)  # without a dispute, barely moves


def test_the_weekly_risk_check_finds_an_agent_that_expects_too_little_risk():
    rng = np.random.default_rng(4)
    weeks = []
    for _ in range(1_000):
        p = float(rng.uniform(0.02, 0.4))
        weeks.append((p, bool(rng.random() < min(1.0, p + 0.07))))  # it really runs ~7 points more risk
    check = fit_risk(weeks)
    assert check(0.15) == pytest.approx(0.22, abs=0.03)
    assert fit_risk([])(0.15) == 0.15  # no track record: no change


def test_the_corrected_profile_follows_the_learned_bias():
    rng = np.random.default_rng(2)
    cal = fit(track_record(5_000, rng, 0.5, 1.5, 3.0, bias=-0.8), POLICY)
    prof = CustomerProfile("C", 10, 0.8, np.array([3]))
    assert cal.profile(prof).p_on_time < 0.7 and "calibrated" in cal.profile(prof).adjustments[-1]
