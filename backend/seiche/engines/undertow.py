"""Undertow — the damping gauge: critical slowing down, measured continuously.

A damped basin forgets a perturbation fast: the surface flattens, the
autocorrelation of its state dies quickly, its variance stays bounded. A basin
LOSING damping forgets slowly — lag-1 autocorrelation and variance of the
detrended state rise, and every little pop takes longer to bleed off. That
signature (critical slowing down) precedes regime shifts across ecology,
climate and finance (Scheffer et al., Nature 2009) — and it needs no event to
fire: it is read off the response to everyday noise.

This completes the seiche physics pair: Resonance measures the FORCED response
(how loud the basin rings to the known calendar bell), Undertow measures the
FREE decay (how fast the ring dies out on ordinary days). Both can deteriorate
while levels look calm; either alone is evidence of thinning damping.

Per series (SOFR−IORB spread, SOFR P99−P50 tail) on rolling-median-detrended
residuals:
  AC1        rolling lag-1 autocorrelation  -> phi
  tau        implied relaxation time = -1/ln(phi) business days
  variance   rolling variance of the residual
  recovery   median half-life of decay after every pop above the expanding
             90th percentile (the unconditional version of Resonance's
             calendar-event half-life), recent year vs prior history

Honesty notes, learned the hard way in the early-warning literature:
  - the indicator series are heavily serially correlated, so trend
    "significance" tests (Kendall tau p-values) are anti-conservative junk —
    we publish expanding PERCENTILES vs the series' own past instead;
  - expanding statistics only: the value at T never changes when future data
    arrives (Time Machine safe, enforced by a unit test);
  - the spread belongs to the same variable family as the PROOF event
    definition — Undertow is evidence of structural fragility for the live
    composite, not an independent orthogonal predictor, and says so.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    UNDERTOW_DETREND_D,
    UNDERTOW_MIN_HISTORY_D,
    UNDERTOW_RECOVERY_CENSOR_D,
    UNDERTOW_RECOVERY_MIN_POPS,
    UNDERTOW_TREND_D,
    UNDERTOW_WINDOW_D,
)

SERIES_WEIGHTS = {"spread": 0.6, "tail": 0.4}


def _roll_ac1(resid: pd.Series, window: int) -> pd.Series:
    """Rolling lag-1 autocorrelation, vectorized (rolling corr of x with its
    own lag — same estimator as Series.autocorr per window, without the
    per-window Python loop)."""
    return resid.rolling(window, min_periods=window // 2).corr(resid.shift(1))


def _expanding_pctl(s: pd.Series, min_periods: int = 120) -> pd.Series:
    """Percentile of each value within its OWN past (inclusive) — trailing
    only, so appending future data never changes a historical value."""
    return s.expanding(min_periods).rank(pct=True) * 100.0


def _indicators(series: pd.Series) -> dict | None:
    s = series.dropna()
    if len(s) < UNDERTOW_MIN_HISTORY_D:
        return None
    resid = (s - s.rolling(UNDERTOW_DETREND_D, min_periods=10).median()).dropna()
    if len(resid) < UNDERTOW_MIN_HISTORY_D // 2:
        return None

    ac1 = _roll_ac1(resid, UNDERTOW_WINDOW_D)
    var = resid.rolling(UNDERTOW_WINDOW_D, min_periods=UNDERTOW_WINDOW_D // 2).var()
    ac1_pctl = _expanding_pctl(ac1.dropna())
    var_pctl = _expanding_pctl(var.dropna())

    phi = float(ac1.dropna().iloc[-1]) if not ac1.dropna().empty else None
    tau = None
    if phi is not None and 0.0 < phi < 0.999:
        tau = float(-1.0 / np.log(phi))

    # Perturbation recovery: every pop of the residual above its EXPANDING
    # 90th percentile is a natural experiment; half-life = business days until
    # the residual gives back half the pop (censored). Recent year vs prior.
    thr = resid.expanding(120).quantile(0.90).shift(1)  # yesterday's yardstick
    pops = resid.index[(resid >= thr) & thr.notna()]
    halves: list[tuple[pd.Timestamp, int]] = []
    vals = resid.to_numpy()
    locs = resid.index.get_indexer(pops)
    for loc in locs:
        peak = vals[loc]
        if peak <= 0:
            continue
        hl = UNDERTOW_RECOVERY_CENSOR_D  # censored default
        for k in range(1, UNDERTOW_RECOVERY_CENSOR_D + 1):
            if loc + k >= len(vals):
                break
            if vals[loc + k] <= peak / 2.0:
                hl = k
                break
        halves.append((resid.index[loc], hl))
    cut = resid.index[-1] - pd.Timedelta(days=365)
    recent = [h for d, h in halves if d >= cut]
    prior = [h for d, h in halves if d < cut]
    recovery = {
        "n_recent": len(recent),
        "n_prior": len(prior),
        "low_n": len(recent) < UNDERTOW_RECOVERY_MIN_POPS or len(prior) < UNDERTOW_RECOVERY_MIN_POPS,
        "halflife_recent_d": round(float(np.median(recent)), 1) if recent else None,
        "halflife_prior_d": round(float(np.median(prior)), 1) if prior else None,
        "stretch": (
            round(float(np.median(recent)) / max(float(np.median(prior)), 0.5), 2)
            if recent and prior
            else None
        ),
    }

    ac1_now = float(ac1_pctl.iloc[-1]) if not ac1_pctl.empty else None
    var_now = float(var_pctl.iloc[-1]) if not var_pctl.empty else None
    ac1_chg = (
        float(ac1.dropna().iloc[-1] - ac1.dropna().iloc[-UNDERTOW_TREND_D])
        if len(ac1.dropna()) > UNDERTOW_TREND_D
        else None
    )
    return {
        "ac1": round(phi, 3) if phi is not None else None,
        "ac1_pctl": round(ac1_now, 0) if ac1_now is not None else None,
        "ac1_chg_60d": round(ac1_chg, 3) if ac1_chg is not None else None,
        "tau_bd": round(tau, 1) if tau is not None else None,
        "var_pctl": round(var_now, 0) if var_now is not None else None,
        "recovery": recovery,
        "_ac1_series": ac1.dropna(),
        "_ac1_pctl_series": ac1_pctl,
        "asof": s.index[-1].date().isoformat(),
    }


def _series_score(ind: dict) -> float:
    """0-100 per series: AC1 percentile 45, variance percentile 30,
    recovery stretch 25 (renormalized away below the pop-count floor)."""
    parts: list[tuple[float, float]] = []
    if ind.get("ac1_pctl") is not None:
        parts.append((ind["ac1_pctl"], 0.45))
    if ind.get("var_pctl") is not None:
        parts.append((ind["var_pctl"], 0.30))
    rec = ind.get("recovery", {})
    if rec.get("stretch") is not None and not rec.get("low_n"):
        # stretch 1.0 (no change) -> 0; 2.5x slower recovery -> 100
        parts.append((float(np.clip((rec["stretch"] - 1.0) / 1.5, 0.0, 1.0)) * 100.0, 0.25))
    if not parts:
        return 0.0
    wsum = sum(w for _, w in parts)
    return float(sum(v * w for v, w in parts) / wsum)


def analyze(spread_bp: pd.Series, tail_bp: pd.Series) -> dict:
    """Both inputs daily with DatetimeIndex; either may be short/empty —
    the blend renormalizes over what qualifies (published, fail-loud)."""
    inds = {}
    for name, s in (("spread", spread_bp), ("tail", tail_bp)):
        ind = _indicators(s)
        if ind is not None:
            inds[name] = ind
    if not inds:
        return {"ok": False, "reason": f"no series with >= {UNDERTOW_MIN_HISTORY_D}d of history"}

    wsum = sum(SERIES_WEIGHTS[k] for k in inds)
    score = sum(_series_score(v) * SERIES_WEIGHTS[k] for k, v in inds.items()) / wsum

    # Chart: AC1 of both series (thinned) — the slowing-down is visible.
    base = inds.get("spread") or next(iter(inds.values()))
    grid = base["_ac1_series"].index
    rows = []
    for d in grid[::2]:
        row = [d.date().isoformat()]
        for k in ("spread", "tail"):
            v = inds.get(k, {}).get("_ac1_series")
            row.append(round(float(v.loc[d]), 3) if v is not None and d in v.index else None)
        rows.append(row)

    # The damping-state series Swell conditions on: blended AC1 expanding
    # percentile (trailing-only by construction).
    pctl_frames = {
        k: v["_ac1_pctl_series"] for k, v in inds.items() if v.get("_ac1_pctl_series") is not None
    }
    blend = pd.concat(pctl_frames, axis=1)
    damping_pctl = (
        blend.mul(pd.Series({k: SERIES_WEIGHTS[k] for k in blend.columns}), axis=1).sum(axis=1)
        / sum(SERIES_WEIGHTS[k] for k in blend.columns)
    ).dropna()

    out = {
        "ok": True,
        "asof": max(v["asof"] for v in inds.values()),
        "score": round(float(score), 1),
        "series_used": list(inds.keys()),
        "per_series": {
            k: {kk: vv for kk, vv in v.items() if not str(kk).startswith("_")}
            for k, v in inds.items()
        },
        "ac1_rows": rows[-500:],
        "_damping_pctl": damping_pctl,
        "caveats": [
            "indicator series are serially correlated — trends are read as percentiles vs own history, never as p-values",
            "spread/tail belong to the PROOF event's variable family: Undertow is live structural evidence, not an orthogonal predictor",
            "detrended with a rolling median: slow level regimes don't count as damping loss, only the noise dynamics do",
        ],
        "method": (
            f"per series: residual = x − rolling {UNDERTOW_DETREND_D}bd median; AC1 = rolling "
            f"{UNDERTOW_WINDOW_D}bd lag-1 autocorr; tau = −1/ln(AC1) bd; variance over the same "
            f"window; both scored as EXPANDING percentiles vs own past. Recovery = median "
            f"half-life after pops above the expanding 90th pctl (censored {UNDERTOW_RECOVERY_CENSOR_D}bd), "
            f"trailing year vs prior. Score: AC1 pctl 45 / var pctl 30 / recovery stretch 25; "
            f"series blend spread {SERIES_WEIGHTS['spread']} / tail {SERIES_WEIGHTS['tail']}"
        ),
    }
    return out


def undertow_score(result: dict) -> float:
    if not result.get("ok"):
        return 0.0
    return float(np.clip(result.get("score", 0.0), 0.0, 100.0))
