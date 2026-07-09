"""The three stochastic scenario engines: Markov, OU+jump, Monte Carlo.

Driven by a synthetic mean-reverting index (no network). Checks structure, that
every probability is a probability, that horizons behave monotonically, and that
the Monte Carlo fan is deterministic (so it can go into the notarised record).
"""

import numpy as np
import pandas as pd
import pytest

from seiche.engines import markov, montecarlo, oujump


@pytest.fixture()
def index():
    # OU around 45 (STRAIN/EROSION border) that wanders across regimes.
    rng = np.random.default_rng(7)
    n, x, theta, k, sig = 600, 42.0, 45.0, 0.05, 7.0
    out = []
    for _ in range(n):
        x = x + k * (theta - x) + sig * rng.standard_normal()
        x = min(100.0, max(0.0, x))
        out.append(x)
    return pd.Series(out)


def _is_prob(v):
    return v is None or (0.0 <= v <= 1.0)


# ---- Markov ------------------------------------------------------------------

def test_markov_matrix_and_reach(index):
    r = markov.analyze(index)
    assert r["ok"]
    assert r["current_regime"] in r["regimes"]
    for row in r["transition_matrix"]:
        assert abs(sum(row) - 1.0) < 0.01           # rows are distributions (3-dp rounded)
    reach = r["p_reach_stress"]
    assert all(_is_prob(v) for v in reach.values())
    assert reach["h5"] <= reach["h10"] <= reach["h21"]   # more time, more chance


def test_markov_needs_history():
    assert markov.analyze(pd.Series([40.0] * 50))["ok"] is False


# ---- OU + jump ---------------------------------------------------------------

def test_oujump_fit_and_exceedance(index):
    r = oujump.analyze(index)
    assert r["ok"]
    assert r["fit"]["k_per_bd"] > 0                 # mean-reverting
    for h in r["horizons"]:
        assert _is_prob(h["p_above_stress"])
        assert _is_prob(h["p_diffusion_only"])
        assert _is_prob(h["jump_share_of_tail"])
        # total tail is never below the diffusion-only tail
        assert h["p_above_stress"] >= h["p_diffusion_only"] - 1e-9


def test_fit_params_shared_shape(index):
    p = oujump.fit_params(index.to_numpy(float))
    assert set(p) == {"k", "theta", "sigma", "lam", "jmean", "jstd", "half_life"}


# ---- Monte Carlo -------------------------------------------------------------

def test_montecarlo_fan_and_probs(index):
    r = montecarlo.analyze(index)
    assert r["ok"]
    assert [f["h"] for f in r["fan"]] == [5, 10, 21]
    for f in r["fan"]:
        assert f["p10"] <= f["median"] <= f["p90"]  # ordered fan
    assert all(_is_prob(v) for v in r["p_touch_stress"].values())
    assert all(_is_prob(v) for v in r["p_back_to_calm"].values())
    # path-max touch prob rises with horizon
    tp = r["p_touch_stress"]
    assert tp["h5"] <= tp["h10"] <= tp["h21"]


def test_montecarlo_is_deterministic(index):
    a = montecarlo.analyze(index)
    b = montecarlo.analyze(index)
    assert a == b        # same input -> identical fan (notarisable, not noise)


def test_montecarlo_needs_history():
    assert montecarlo.analyze(pd.Series([40.0] * 50))["ok"] is False


# ---- reconciliation: start from the live board level -------------------------

def test_current_value_overrides_starting_point(index):
    base = montecarlo.analyze(index)
    hot = montecarlo.analyze(index, current_value=75.0)     # near STRESS
    assert hot["level_now"] == 75.0                          # headline matches the board
    assert hot["p_touch_stress"]["h21"] > base["p_touch_stress"]["h21"]


def test_oujump_current_value_overrides(index):
    assert oujump.analyze(index, current_value=68.0)["level_now"] == 68.0


def test_markov_current_regime_overrides(index):
    assert markov.analyze(index, current_regime="STRAIN")["current_regime"] == "STRAIN"
