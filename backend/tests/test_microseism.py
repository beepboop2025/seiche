"""Microseism: parameter recovery, the calendar-null guard, truncation
equality and determinism.

The two tests that matter most:
  - a pure calendar-Poisson simulation must NOT print self-excitation
    (the Filimonov–Sornette false-positive guard — apparent clustering from
    deterministic forcing must be absorbed by the gated baseline);
  - a genuine Hawkes simulation must recover its branching ratio.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from seiche.engines import microseism as ms
from seiche.engines.swell import classify_days


def _grid(n_days: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2017-01-02", periods=n_days)


def _simulate_hawkes(
    rate_by_day: np.ndarray, n: float, beta: float, rng: np.random.Generator
) -> np.ndarray:
    """Cluster (immigrant/offspring) simulation on a daily time axis:
    immigrants ~ Poisson(rate per day); each event spawns Poisson(n) children
    at exponential(beta) forward lags. Returns sorted unique integer days."""
    T = len(rate_by_day)
    immigrants = [t for t in range(T) if rng.random() < rate_by_day[t]]
    events: list[int] = []
    queue = list(immigrants)
    while queue:
        t = queue.pop()
        events.append(t)
        for _ in range(rng.poisson(n)):
            child = t + rng.exponential(1.0 / beta)
            if child < T:
                queue.append(int(math.ceil(child)))
    return np.unique(np.array(sorted(events), dtype=int))


def _spread_with_shocks(idx: pd.DatetimeIndex, shock_days: np.ndarray) -> pd.Series:
    """A flat spread with +6bp single-day pops exactly on shock_days — the
    pop statistic (x − trailing 5bd median) then recovers those days."""
    s = pd.Series(0.0, index=idx)
    s.iloc[shock_days] = 6.0
    return s


# ---------------------------------------------------------------------------
# 1. Calendar-null guard: pure forcing, no excitation -> n stays small
# ---------------------------------------------------------------------------

def test_calendar_null_does_not_print_self_excitation():
    idx = _grid(2200)
    buckets = classify_days(idx).to_numpy()
    # strong deterministic forcing: turn/tax days shock 10x more often
    rate = np.where(np.isin(buckets, ("month_end", "quarter_turn", "year_turn", "tax_date")), 0.50, 0.05)
    rng = np.random.default_rng(11)
    days = np.flatnonzero(rng.random(len(idx)) < rate)
    hit = np.zeros(len(idx), dtype=bool)
    hit[days] = True
    rates = ms._bucket_rates(buckets, hit, len(idx))
    r = ms._rate_array(buckets, rates)
    fit = ms._fit(days, r, len(idx))
    assert fit is not None
    # forcing is fully explained by the gated baseline — excitation stays small
    assert fit["n"] < 0.15
    null = ms._null_fit(days, r, len(idx))
    lr = 2.0 * (fit["loglik"] - null["loglik"])
    assert lr < 15.0  # no decisive rejection of the calendar null


# ---------------------------------------------------------------------------
# 2. Genuine Hawkes -> branching recovered
# ---------------------------------------------------------------------------

def test_hawkes_recovery():
    idx = _grid(2500)
    buckets = classify_days(idx).to_numpy()
    rate = np.full(len(idx), 0.03)
    rng = np.random.default_rng(7)
    days = _simulate_hawkes(rate, n=0.55, beta=0.30, rng=rng)
    assert days.size >= ms.MICRO_MIN_EVENTS
    hit = np.zeros(len(idx), dtype=bool)
    hit[days] = True
    rates = ms._bucket_rates(buckets, hit, len(idx))
    r = ms._rate_array(buckets, rates)
    fit = ms._fit(days, r, len(idx))
    assert fit is not None
    # daily discretization + gated baseline absorb some excitation; the
    # recovered branching must land in the right regime, not at zero
    assert 0.30 < fit["n"] < 0.80
    null = ms._null_fit(days, r, len(idx))
    assert 2.0 * (fit["loglik"] - null["loglik"]) > 15.0  # decisively identified


# ---------------------------------------------------------------------------
# 3. Truncation equality: published branching history never changes
# ---------------------------------------------------------------------------

def test_branching_history_truncation_equality():
    idx = _grid(1400)
    rng = np.random.default_rng(3)
    days = _simulate_hawkes(np.full(len(idx), 0.05), n=0.4, beta=0.4, rng=rng)
    spread = _spread_with_shocks(idx, days)

    full = ms.analyze(spread)
    trunc = ms.analyze(spread.iloc[:1100])
    assert full["ok"] and trunc["ok"]
    common = min(len(full["branching_rows"]), len(trunc["branching_rows"]))
    assert common >= 3
    assert full["branching_rows"][:common] == trunc["branching_rows"][:common]


# ---------------------------------------------------------------------------
# 4. Determinism: two runs, identical output
# ---------------------------------------------------------------------------

def test_deterministic():
    idx = _grid(1200)
    rng = np.random.default_rng(5)
    days = _simulate_hawkes(np.full(len(idx), 0.06), n=0.3, beta=0.5, rng=rng)
    spread = _spread_with_shocks(idx, days)
    a = ms.analyze(spread)
    b = ms.analyze(spread)
    a.pop("_branching_series"), b.pop("_branching_series")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------------------
# 5. Refusals
# ---------------------------------------------------------------------------

def test_refuses_short_history():
    idx = _grid(300)
    out = ms.analyze(pd.Series(0.0, index=idx))
    assert not out["ok"]
    assert "insufficient" in out["reason"]


def test_refuses_too_few_shocks():
    idx = _grid(900)
    spread = _spread_with_shocks(idx, np.array([100, 400, 700]))
    out = ms.analyze(spread)
    assert not out["ok"]
    assert "micro-shocks" in out["reason"]
