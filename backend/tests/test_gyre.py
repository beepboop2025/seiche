"""The Gyre tests — planted determinism and the honesty invariants.

Same philosophy as test_engines.py: the tests that matter are not "does it
run" but "does it refuse to cheat" — the engine must find the chaos it was
given (skill that DECAYS with horizon, state-dependent dynamics), read white
noise as noise, read a linear AR(1) as linear (the surrogates preserve AR
structure, so linear memory must never masquerade as determinism), never
look ahead, refuse short history, keep the live fan ordered and finite, and
publish a JSON-safe payload.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from seiche.engines import gyre


def _bdays(n: int, start: str = "2019-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


@pytest.fixture()
def rng():
    return np.random.default_rng(7)


def _logistic_series(n: int = 1500) -> pd.Series:
    """Fully deterministic chaos: x_{n+1} = 3.9 x_n (1 − x_n), scaled to a
    bp-like series. The engine's rolling-median detrend leaves the dynamics
    intact (an affine transform of the observable is still an observable)."""
    x = np.empty(n)
    x[0] = 0.234
    for t in range(1, n):
        x[t] = 3.9 * x[t - 1] * (1.0 - x[t - 1])
    return pd.Series((x - 0.5) * 20.0, index=_bdays(n))


def _ar1_series(n: int = 1500, phi: float = 0.7, seed: int = 7) -> pd.Series:
    """Linearly-filtered noise: all its predictability is autocorrelation."""
    g = np.random.default_rng(seed)
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + g.normal(0.0, 1.0)
    return pd.Series(x, index=_bdays(n))


@pytest.fixture(scope="module")
def chaos_result():
    return gyre.analyze(_logistic_series())


@pytest.fixture(scope="module")
def ar1_result():
    return gyre.analyze(_ar1_series())


def test_gyre_finds_chaos(chaos_result):
    r = chaos_result
    assert r["ok"]
    assert r["determinism"]["verdict"].startswith("deterministic"), \
        "a pure logistic map must clear the surrogate gate"
    d = {row["h"]: row for row in r["decay"]}
    assert d[1]["rho"] > 0.85, "near neighbors on the attractor must have near futures at h=1"
    assert d[1]["rho"] > d[8]["rho"] + 0.2, \
        "chaos amplifies small differences — skill must DECAY with horizon"
    assert r["nonlinearity"]["delta_rho"] > 0.03, \
        "the logistic map is state-dependent — localizing the S-map must beat the global linear fit"


def test_gyre_rejects_noise(rng):
    # white noise — whiteness is a property of the spectrum, not the marginal
    s = pd.Series(rng.uniform(-5.0, 5.0, 1500), index=_bdays(1500))
    r = gyre.analyze(s)
    assert r["ok"]
    assert r["determinism"]["verdict"].startswith("indistinguishable"), \
        "white noise must not clear the surrogate gate"


def test_gyre_linear_ar1_reads_linear(ar1_result):
    r = ar1_result
    assert r["ok"]
    # phase-randomized surrogates preserve the AR structure exactly, so the
    # AR(1)'s (real) forecastability must NOT read as determinism...
    assert r["determinism"]["verdict"].startswith("indistinguishable"), \
        "linear memory must never masquerade as deterministic structure"
    # ...and a global linear map must be (near) the best S-map.
    assert r["nonlinearity"]["delta_rho"] < 0.1, \
        "an AR(1) obeys the same rule at every state — theta must not buy real skill"


def test_gyre_no_look_ahead(ar1_result):
    full = ar1_result
    trunc = gyre.analyze(_ar1_series().iloc[:-120])
    assert full["ok"] and trunc["ok"]
    t = trunc["_stability_pctl_series"].index[-1]
    assert abs(
        float(full["_stability_pctl_series"].loc[t])
        - float(trunc["_stability_pctl_series"].loc[t])
    ) < 1e-9, "stability percentile at T changed when future data was appended — look-ahead leak"


def test_gyre_refuses_short_history(rng):
    r = gyre.analyze(pd.Series(rng.normal(0.0, 1.0, 300), index=_bdays(300)))
    assert not r["ok"]


def test_gyre_forecast_bounds(chaos_result):
    fc = chaos_result["forecast"]
    for key in ("point_bp", "p25_bp", "p75_bp"):
        assert fc[key] is not None and np.isfinite(fc[key]), f"{key} must be finite"
    assert fc["p25_bp"] <= fc["point_bp"] <= fc["p75_bp"], \
        "the point forecast must sit inside its own neighbor fan"


def test_gyre_payload_json_safe(ar1_result):
    r = ar1_result
    payload = {k: v for k, v in r.items() if not str(k).startswith("_")}
    for key in (
        "ok", "asof", "n", "embedding", "decay", "determinism", "nonlinearity",
        "stability", "stability_rows", "forecast", "caveats", "method",
    ):
        assert key in payload, f"missing output key {key}"
    json.dumps(payload)  # must not raise
    assert len(r["stability_rows"]) <= 500
    assert r["embedding"]["chosen_on"] == "warmup segment only, frozen"
    assert isinstance(r["_stability_pctl_series"], pd.Series)
