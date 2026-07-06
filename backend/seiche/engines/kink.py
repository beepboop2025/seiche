"""Kink Engine — live reserve-demand-curve estimation.

The reserve demand curve is flat when reserves are abundant (the SOFR-IORB
spread ignores reserve changes) and turns steeply negative near scarcity.
The NY Fed estimates this ("Reserve Demand Elasticity") as periodic research;
we fit it continuously as a hockey-stick:

    spread = a + slope * max(0, kink_ratio - reserves/GDP) + eps

Grid-search the kink over observed reserves/GDP; report the kink translated
into today's dollars, the distance from current reserves, and days-to-kink at
the trailing drain rate. Fit quality (R^2 vs flat model) gates confidence.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def fit_kink(
    spread_daily: pd.Series,      # SOFR - IORB, %, daily
    reserves_weekly: pd.Series,   # WRESBAL, $M, weekly Wed
    gdp_quarterly: pd.Series,     # nominal GDP, $B SAAR, quarterly
) -> dict:
    # Weekly-align: average the spread over each reserve week, scale reserves by GDP.
    spread_w = spread_daily.resample("W-WED").mean()
    res_b = (reserves_weekly / 1000.0).resample("W-WED").last()  # $M -> $B
    df = pd.concat({"spread": spread_w, "res": res_b}, axis=1).dropna()
    # GDP publishes with a ~quarter lag — carry the latest print forward to
    # the present instead of truncating the sample at GDP's last obs date.
    gdp_w = gdp_quarterly.sort_index().reindex(df.index, method="ffill")
    df["gdp"] = gdp_w
    df = df.dropna()
    if len(df) < 60:
        return {"ok": False, "reason": f"insufficient overlap ({len(df)} weeks)"}

    x = (df["res"] / df["gdp"]).to_numpy()          # reserves / GDP ratio
    y = (df["spread"] * 100.0).to_numpy()           # % -> bp

    # Winsorize the spread tails so single squeeze days don't own the fit.
    lo, hi = np.percentile(y, [1, 99])
    y = np.clip(y, lo, hi)

    grid = np.linspace(np.percentile(x, 5), np.percentile(x, 95), 121)
    best = None
    for b in grid:
        z = np.maximum(0.0, b - x)                  # hinge below the kink
        X = np.column_stack([np.ones_like(x), z])
        beta, res_ss, *_ = np.linalg.lstsq(X, y, rcond=None)
        sse = float(res_ss[0]) if len(res_ss) else float(((X @ beta - y) ** 2).sum())
        if beta[1] > 0 and (best is None or sse < best["sse"]):
            best = {"sse": sse, "kink_ratio": float(b), "intercept": float(beta[0]), "slope": float(beta[1])}
    if best is None:
        return {"ok": False, "reason": "no upward-sloping hinge fit found"}

    sst = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - best["sse"] / sst if sst > 0 else 0.0

    gdp_now = float(df["gdp"].iloc[-1])
    res_now = float(df["res"].iloc[-1])
    kink_b = best["kink_ratio"] * gdp_now
    distance_b = res_now - kink_b

    # Trailing drain rate: 12-week reserve change, per business day.
    tail = df["res"].tail(13)
    drain_per_bd = float((tail.iloc[-1] - tail.iloc[0]) / (5 * (len(tail) - 1))) if len(tail) > 3 else 0.0
    days_to_kink = distance_b / -drain_per_bd if drain_per_bd < -1e-9 and distance_b > 0 else None

    # Model-vs-market consistency: what spread does the fit predict at today's
    # reserve level, and what is the market actually printing? Disagreement
    # discounts the sub-score (confidence-native, not silently trusted).
    x_now = x[-1]
    predicted_now_bp = best["intercept"] + best["slope"] * max(0.0, best["kink_ratio"] - x_now)
    observed_now_bp = float(np.mean(y[-4:]))
    consistency = float(np.clip(1.0 - abs(predicted_now_bp - observed_now_bp) / 12.0, 0.35, 1.0))

    curve = [
        [round(float(xi), 5), round(float(yi), 2)]
        for xi, yi in zip(x[-156:], y[-156:])  # last ~3y of weekly points for the scatter
    ]
    return {
        "ok": True,
        "predicted_spread_now_bp": round(predicted_now_bp, 1),
        "observed_spread_now_bp": round(observed_now_bp, 1),
        "consistency": round(consistency, 2),
        "kink_reserves_b": round(kink_b, 1),
        "current_reserves_b": round(res_now, 1),
        "distance_b": round(distance_b, 1),
        "drain_per_bday_b": round(drain_per_bd, 2),
        "days_to_kink": round(days_to_kink) if days_to_kink is not None else None,
        "kink_ratio": round(best["kink_ratio"], 5),
        "slope_bp_per_ratio": round(best["slope"], 1),
        "r2": round(r2, 3),
        "asof": df.index[-1].date().isoformat(),
        "scatter": curve,
        "method": "hockey-stick LSQ on weekly SOFR-IORB (bp, winsorized 1/99) vs reserves/GDP; grid-searched breakpoint",
    }


def kink_score(fit: dict) -> float:
    """0-100 sub-score: proximity to the kink, then depth into it.

    Crossing the breakpoint isn't binary doom — the fitted slope says how
    steep the scarcity region actually is. Approach ramps 0->60; beyond the
    kink, 60->100 scales with the model-implied spread (15bp+ = saturated).
    Fit quality and model-vs-market consistency discount the whole thing.
    """
    if not fit.get("ok"):
        return 0.0
    dist = fit["distance_b"]
    if dist > 0:
        raw = float(np.clip(1.0 - dist / 600.0, 0.0, 1.0)) * 60.0
    else:
        implied_bp = max(fit.get("predicted_spread_now_bp") or 0.0, 0.0)
        raw = 60.0 + 40.0 * float(np.clip(implied_bp / 15.0, 0.0, 1.0))
    conf = float(np.clip(fit.get("r2", 0.0) / 0.35, 0.3, 1.0))  # r2>=0.35 -> full weight
    return raw * conf * fit.get("consistency", 1.0)
