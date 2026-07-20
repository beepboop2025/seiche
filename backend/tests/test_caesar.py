"""CAESar tests — a planted GARCH tail and the honesty invariants.

Same philosophy as test_roguewave.py: not "does it run" but "does it refuse to
cheat". The engine is given a world whose conditional tail is KNOWN —
GARCH(1,1) vol with fast mean-reversion times a standardized Student-t —
folded into a spread whose pop_bp reproduces the planted series exactly
(s_t = g_t + median(s_{t-5..t-1}), verified against THE shared statistic
before any engine assertion). Vol is planted to move FAST (alpha > beta by
GARCH standards): an unconditional rolling benchmark cannot keep up with a
tail that re-prices every day, which is exactly the structure CAESar exists
to extract (arXiv:2407.06619's stated edge over models that only update on
quantile breaks). In a slow-vol world rolling climatology is nearly
unbeatable at these sample sizes — that regime is not what is tested here.

The invariants under test:
  (a) the fitted VaR tracks the planted one-step-ahead conditional quantile
      better than climatology on average (MAE over walk-forward origins);
  (b) ES >= VaR on EVERY walk-forward origin at both levels — the paper only
      softly constrains monotonicity, the published bands must never cross;
  (c) no look-ahead: appending future data never changes a published band
      (truncation equality on shared walk-forward origins);
  (d) thin history is refused before any fit is attempted;
  (e) the payload is JSON-safe, carries the house keys, and no score.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from scipy import stats

# Leak canary (Bloomberg pytest-memray), same as test_engines.py: inert unless
# pytest runs with --memray (CI does; local dev may not).
pytestmark = pytest.mark.limit_memory("256 MB")

from seiche.engines import backtest, caesar

_GARCH = dict(n=850, df=6.0, omega=0.2, alpha=0.25, beta=0.70)
_SKIP_WARMUP = 40  # origins dropped before skill is judged (first fits are thin)


def _bdays(n: int, start: str = "2019-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


def _garch_world(rng, n: int, df: float, omega: float, alpha: float, beta: float):
    """Planted pops g_t = sigma_t * z_t (z a standardized Student-t) with
    sigma^2_t = omega + alpha*g^2_{t-1} + beta*sigma^2_{t-1}, folded into a
    spread s_t = g_t + median(s_{t-5..t-1}) so pop_bp(s)_t == g_t exactly
    (t >= 3). Returns (spread, sigma, g)."""
    z = rng.standard_t(df, n) / np.sqrt(df / (df - 2.0))
    g = np.zeros(n)
    sig = np.empty(n)
    s2 = omega / (1.0 - alpha - beta)
    sig[0] = np.sqrt(s2)
    for t in range(1, n):
        s2 = omega + alpha * g[t - 1] ** 2 + beta * s2
        sig[t] = np.sqrt(s2)
        g[t] = sig[t] * z[t]
    g[:3] = 0.0
    s = np.zeros(n)
    for t in range(3, n):
        s[t] = g[t] + np.median(s[max(0, t - 5):t])
    return pd.Series(s, index=_bdays(n)), sig, g


def _planted_quantile(q: float) -> float:
    """q-quantile of the standardized Student-t innovation."""
    df = _GARCH["df"]
    return float(stats.t.ppf(q, df) / np.sqrt(df / (df - 2.0)))


@pytest.fixture(scope="module")
def world():
    rng = np.random.default_rng(7)
    spread, sig, g = _garch_world(rng, **_GARCH)
    pop = backtest.pop_bp(spread).dropna()
    assert np.max(np.abs(pop.to_numpy() - g[3:])) < 1e-9, \
        "pop_bp must reproduce the planted series before any engine assertion"
    res = caesar.analyze(spread)
    assert res["ok"], res.get("reason")
    return {"res": res, "sig": sig, "spread": spread}


# ---------------------------------------------------------------------------
# (a) Planted truth: fitted VaR tracks the known conditional quantile
# ---------------------------------------------------------------------------

def test_caesar_var_tracks_planted_conditional_quantile(world):
    res, sig = world["res"], world["sig"]
    for lab, q in (("q95", 0.95), ("q99", 0.99)):
        wf = res["_wf"][lab]
        pos = wf["pos"].to_numpy()
        mask = pos >= caesar.CAESAR_T0 + _SKIP_WARMUP
        planted = sig[pos[mask] + 1] * _planted_quantile(q)  # one-step-ahead truth
        mae_model = float(np.mean(np.abs(wf["var_bp"].to_numpy()[mask] - planted)))
        mae_clim = float(np.mean(np.abs(wf["clim_var_bp"].to_numpy()[mask] - planted)))
        assert mae_model < mae_clim, (
            f"{lab}: fitted VaR must track the planted conditional quantile better "
            f"than climatology (MAE {mae_model:.3f} vs {mae_clim:.3f})"
        )


# ---------------------------------------------------------------------------
# (b) Monotonicity: ES >= VaR on every walk-forward origin
# ---------------------------------------------------------------------------

def test_caesar_es_never_below_var(world):
    for lab in ("q95", "q99"):
        wf = world["res"]["_wf"][lab]
        assert bool((wf["es_bp"] >= wf["var_bp"] - 1e-9).all()), \
            f"{lab}: ES below VaR on some origin — published bands crossed"
    # and the cross-level order at print time: the 99% band is wider
    r = world["res"]
    assert r["es99_bp"] >= r["var99_bp"] - 1e-9
    assert r["es95_bp"] >= r["var95_bp"] - 1e-9


# ---------------------------------------------------------------------------
# (c) No look-ahead: truncation equality of the published bands
# ---------------------------------------------------------------------------

def test_caesar_no_look_ahead(world):
    spread = world["spread"]
    trunc = caesar.analyze(spread.iloc[:-120])
    assert trunc["ok"], trunc.get("reason")
    full = world["res"]
    for lab in ("q95", "q99"):
        wf_f, wf_t = full["_wf"][lab], trunc["_wf"][lab]
        shared = wf_f.index.get_indexer(wf_t.index)
        assert (shared >= 0).all(), "truncated origins must be a subset of full origins"
        ff = wf_f.iloc[shared]
        for col in ("var_bp", "es_bp"):
            assert np.allclose(ff[col].to_numpy(), wf_t[col].to_numpy(), atol=1e-8), \
                f"{lab}.{col} changed when future data was appended — look-ahead leak"


# ---------------------------------------------------------------------------
# (d) Thin history refusal
# ---------------------------------------------------------------------------

def test_caesar_refuses_thin_history():
    rng = np.random.default_rng(3)
    short = pd.Series(rng.normal(0.0, 2.0, 300), index=_bdays(300))
    r = caesar.analyze(short)
    assert not r["ok"]
    assert str(caesar.CAESAR_MIN_HISTORY_D) in r["reason"]


# ---------------------------------------------------------------------------
# (e) Payload: JSON-safe, house keys, no score
# ---------------------------------------------------------------------------

def test_caesar_payload_json_safe(world):
    r = world["res"]
    payload = {k: v for k, v in r.items() if not str(k).startswith("_")}
    for key in (
        "ok", "asof", "n_days", "var95_bp", "es95_bp", "var99_bp", "es99_bp",
        "levels", "loss_ratio_vs_climatology", "reliability", "verdict",
        "verdict_detail", "n_origins", "caveats", "method",
    ):
        assert key in payload, f"missing output key {key}"
    json.dumps(payload)  # must not raise — no numpy/pandas leakage
    assert "arXiv:2407.06619" in payload["method"]
    assert payload["verdict"] in ("use caesar", "use climatology")
    assert set(payload["loss_ratio_vs_climatology"]) == {"q95", "q99"}
    for row in payload["reliability"]:
        for key in ("level", "nominal", "exceedance_rate", "n_origins", "wilson95"):
            assert key in row, f"reliability row missing {key}"
        assert row["wilson95"][0] <= row["exceedance_rate"] <= row["wilson95"][1]
    assert "score" not in payload, "tail bands are context, not evidence — no composite score"
