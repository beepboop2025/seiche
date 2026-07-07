"""Rogue Wave tests — planted tails and the honesty invariants.

Same philosophy as test_engines.py: not "does it run" but "does it refuse to
cheat" — the engine must recover a tail law it was given (GPD world reads
xi ~ 0.3, exponential world reads xi ~ 0), keep return levels ordered with
CIs that contain their points and widen with horizon, keep exceedance
probabilities monotone, never look ahead in the historical xi series, refuse
thin history and thin exceedance counts, and publish a JSON-safe payload.

Synthetic construction: spreads whose pop_bp reproduces planted magnitudes.
A gently DOWN-drifting noisy baseline keeps ordinary-day pops almost surely
negative (pop is level-free but not drift-free: pop ~ N(-3*drift, sigma')),
so the engine's positive-pop threshold quantile lands INSIDE the planted
spike distribution rather than in the noise — an isolated one-day spike of
size v (drift-compensated) then yields pop ~ v on the spike day, which each
test verifies with backtest.pop_bp before asserting on the engine. By GPD
threshold stability the excesses above the engine's own threshold are still
GPD with the planted xi.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from seiche.config import (
    ROGUE_HORIZONS_BD,
    ROGUE_MIN_EXCEED,
    ROGUE_MIN_HISTORY_D,
)
from seiche.engines import backtest, roguewave


def _bdays(n: int, start: str = "2015-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


@pytest.fixture()
def rng():
    return np.random.default_rng(7)


def _inv_gpd(u01: np.ndarray, xi: float, beta: float) -> np.ndarray:
    """GPD inverse CDF: e = beta/xi * ((1-U)^(-xi) - 1)."""
    return beta / xi * ((1.0 - u01) ** (-xi) - 1.0)


def _spiked_spread(
    rng,
    sizes: np.ndarray,
    spacing: int = 12,
    n_days: int = 4000,
    first: int = 20,
    sigma: float = 0.3,
    drift: float = 0.10,
) -> tuple[pd.Series, np.ndarray]:
    """Near-zero-noise baseline (sigma ~ 0.3bp) with a gentle downward drift,
    plus isolated one-day spikes spaced >= 8bd apart. pop on a spike day is
    v_planted - 3*drift + noise, so sizes are drift-compensated at planting;
    ordinary days pop ~ N(-3*drift, ~sigma) and stay out of the tail."""
    sizes = np.asarray(sizes, dtype=float)
    base = -drift * np.arange(n_days, dtype=float) + rng.normal(0.0, sigma, n_days)
    locs = first + spacing * np.arange(len(sizes))
    assert spacing >= 8 and locs[-1] < n_days
    base[locs] += sizes + 3.0 * drift  # compensate the drift bias in pop
    return pd.Series(base, index=_bdays(n_days)), locs


def _verify_planting(spread: pd.Series, locs: np.ndarray, sizes: np.ndarray) -> None:
    """An isolated spike of size v must yield pop ~ v on the spike day —
    checked with THE shared statistic before any engine assertion."""
    pop = backtest.pop_bp(spread).dropna()
    planted = pop.reindex(spread.index[locs]).to_numpy()
    assert np.max(np.abs(planted - sizes)) < 1.5, "pop_bp does not reproduce the planted magnitudes"


def _gpd_world(rng, xi: float = 0.3, beta: float = 3.0, u0: float = 2.0):
    sizes = u0 + _inv_gpd(rng.random(300), xi, beta)
    spread, locs = _spiked_spread(rng, sizes)
    _verify_planting(spread, locs, sizes)
    return spread


# ---------------------------------------------------------------------------
# 1. Recovers a planted GPD tail (and the PWM estimator itself is pinned)
# ---------------------------------------------------------------------------

def test_roguewave_recovers_planted_gpd(rng):
    # PWM Monte-Carlo sanity check: the estimator alone, no pipeline —
    # 5000 iid GPD(0.3, 3) draws must come back within ±0.05 on xi.
    fit_mc = roguewave._gpd_pwm(_inv_gpd(rng.random(5000), 0.3, 3.0))
    assert fit_mc is not None
    xi_mc, beta_mc = fit_mc
    assert abs(xi_mc - 0.3) < 0.05, f"PWM estimator itself is off: xi={xi_mc:.3f}"
    assert 2.5 < beta_mc < 3.5

    r = roguewave.analyze(_gpd_world(rng))
    assert r["ok"], r.get("reason")
    assert r["fit"]["n_exceed"] >= ROGUE_MIN_EXCEED
    assert abs(r["fit"]["xi"] - 0.3) < 0.15, f"planted xi=0.3 not recovered: {r['fit']}"
    # threshold sits above u0, so threshold stability gives scale
    # beta + xi*(u - u0); a factor-2 window on the planted 3 covers it
    assert 1.5 < r["fit"]["beta"] < 6.0, f"planted beta=3 not recovered within 2x: {r['fit']}"


# ---------------------------------------------------------------------------
# 2. An exponential world must read as a light (xi ~ 0) tail
# ---------------------------------------------------------------------------

def test_roguewave_exponential_world_reads_light_tail(rng):
    sizes = 2.0 + rng.exponential(3.0, 300)  # u0 + Exp(beta=3)
    spread, locs = _spiked_spread(rng, sizes)
    _verify_planting(spread, locs, sizes)
    r = roguewave.analyze(spread)
    assert r["ok"], r.get("reason")
    assert abs(r["fit"]["xi"]) < 0.15, f"exponential spikes must read xi ~ 0, got {r['fit']['xi']}"


# ---------------------------------------------------------------------------
# 3. Return levels: ordered, CIs contain the point, CIs widen with T
# ---------------------------------------------------------------------------

def test_roguewave_return_levels_ordered(rng):
    r = roguewave.analyze(_gpd_world(rng))
    assert r["ok"], r.get("reason")
    rls = sorted(r["return_levels"], key=lambda d: d["years"])
    assert [d["years"] for d in rls] == [1.0, 5.0, 10.0]
    assert rls[0]["bp"] > r["threshold_bp"], "1y return level must exceed the threshold"
    assert rls[0]["bp"] < rls[1]["bp"] < rls[2]["bp"], "return levels must increase with T"
    prev_width = -np.inf
    for d in rls:
        assert d["ci95"] is not None
        lo, hi = d["ci95"]
        assert lo - 1e-9 <= d["bp"] <= hi + 1e-9, f"CI {d['ci95']} must contain point {d['bp']}"
        width = hi - lo
        assert width >= prev_width - 1e-9, "return-level CIs must widen with T"
        prev_width = width


# ---------------------------------------------------------------------------
# 4. P(pop >= x within h): monotone in h and in x
# ---------------------------------------------------------------------------

def test_roguewave_p_exceed_monotone(rng):
    r = roguewave.analyze(_gpd_world(rng))
    assert r["ok"], r.get("reason")
    hkeys = [f"h{h}" for h in ROGUE_HORIZONS_BD]
    rows = sorted(r["p_exceed"], key=lambda d: d["x_bp"])
    for row in rows:
        assert row["basis"] in ("gpd", "empirical")
        vals = [row[h] for h in hkeys]
        assert all(0.0 <= v <= 1.0 for v in vals)
        assert all(b > a for a, b in zip(vals, vals[1:])), \
            f"P must increase with horizon at x={row['x_bp']}: {vals}"
    for h in hkeys:
        col = [row[h] for row in rows]
        assert all(b < a for a, b in zip(col, col[1:])), \
            f"P must decrease with severity at {h}: {col}"


# ---------------------------------------------------------------------------
# 5. No look-ahead: xi series truncation equality
# ---------------------------------------------------------------------------

def test_roguewave_no_look_ahead(rng):
    spread = _gpd_world(rng)
    full = roguewave.analyze(spread)
    trunc = roguewave.analyze(spread.iloc[:-300])
    assert full["ok"] and trunc["ok"]
    ser_f, ser_t = full["_xi_series"], trunc["_xi_series"]
    assert len(ser_t) >= 2, "need at least two shared refit dates for the invariant to bite"
    missing = ser_t.index.difference(ser_f.index)
    assert len(missing) == 0, f"refit dates changed when future data was appended: {missing}"
    for t in ser_t.index:
        assert abs(float(ser_f.loc[t]) - float(ser_t.loc[t])) < 1e-9, \
            f"xi at {t} changed when future data was appended — look-ahead leak"


# ---------------------------------------------------------------------------
# 6. Refusals: thin history, thin exceedance count
# ---------------------------------------------------------------------------

def test_roguewave_refuses_thin_history(rng):
    short = pd.Series(rng.normal(0.0, 0.3, 300), index=_bdays(300))
    r = roguewave.analyze(short)
    assert not r["ok"]
    assert str(ROGUE_MIN_HISTORY_D) in r["reason"]

    # long enough series, but almost no waves: too few exceedances to fit
    sizes = np.full(10, 5.0)
    spread, _ = _spiked_spread(rng, sizes, spacing=100, n_days=1200, drift=0.25)
    r2 = roguewave.analyze(spread)
    assert not r2["ok"]
    assert "exceed" in r2["reason"].lower(), f"reason must mention exceedances: {r2['reason']}"


# ---------------------------------------------------------------------------
# 7. Payload: JSON-safe, private keys underscored
# ---------------------------------------------------------------------------

def test_roguewave_payload_json_safe(rng):
    r = roguewave.analyze(_gpd_world(rng))
    assert r["ok"], r.get("reason")
    payload = {k: v for k, v in r.items() if not str(k).startswith("_")}
    for key in (
        "ok", "asof", "n_days", "n_clusters", "threshold_bp", "exceed_rate_per_bd",
        "fit", "return_levels", "p_exceed", "sensitivity", "xi_rows", "tail_verdict",
        "sample_max_bp", "caveats", "method",
    ):
        assert key in payload, f"missing output key {key}"
    for key in ("xi", "xi_ci95", "beta", "n_exceed"):
        assert key in payload["fit"], f"missing fit key {key}"
    json.dumps(payload)  # must not raise — no numpy/pandas leakage
    assert isinstance(r["_xi_series"], pd.Series)
    assert len(payload["xi_rows"]) == len(r["_xi_series"])
    assert "score" not in payload, "tail law is context, not evidence — no composite score"
