"""Liquidity Weather — forward reserve-path forecast with crunch windows.

Identity (with ON RRP ~ 0, every TGA move hits reserves ~1:1):

    reserves[t+1] = reserves[t] + Fed-balance-sheet drift - dTGA_forecast[t+1]

Components:
- Fed drift: trailing 8-week WALCL trend projected forward (data-driven; do
  NOT hard-code an RMP pace — a QT restart under a new chair would rot it).
- dTGA forecast: day-of-month seasonal median (fiscal flows repeat on a
  monthly rhythm: mid-month settlements, tax dates, month-end), estimated
  from ~3y of Daily Treasury Statement history, with corporate tax dates
  handled as their own bucket.
- Bands: empirical quantiles of historical cumulative seasonal-forecast
  errors at each horizon — honest bands, wide where the model is weak.
Crunch window: any forecast day where reserves dip within the cushion of the
kink, with a wider cushion at quarter-end (dealer window-dressing).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    CORPORATE_TAX_DAYS,
    CRUNCH_CUSHION_B,
    CRUNCH_CUSHION_QEND_B,
    WEATHER_HORIZON_BDAYS,
)


def _day_bucket(d: pd.Timestamp) -> str:
    if (d.month, d.day) in CORPORATE_TAX_DAYS or (d.day in (15, 16) and d.month in (3, 4, 6, 9, 12)):
        return "tax"
    if d.is_quarter_end:
        return "qend"
    if d.is_month_end:
        return "mend"
    return f"dom_{min(d.day, 28)}"


def _seasonal_dtga(tga: pd.Series) -> dict[str, float]:
    """Median daily TGA change ($B) per calendar bucket, from DTS history."""
    d = tga.dropna()
    chg = d.diff().dropna()
    buckets: dict[str, list[float]] = {}
    for ts, v in chg.items():
        buckets.setdefault(_day_bucket(ts), []).append(float(v))
    return {k: float(np.median(v)) for k, v in buckets.items() if len(v) >= 3}


def _backtest_band(tga: pd.Series, seasonal: dict[str, float], horizon: int) -> tuple[list[float], list[float]]:
    """20/80% quantiles of cumulative forecast error by horizon step."""
    d = tga.dropna()
    chg = d.diff().dropna()
    errs_by_h: list[list[float]] = [[] for _ in range(horizon)]
    dates = chg.index
    for start in range(len(dates) - horizon):
        cum_err = 0.0
        for h in range(horizon):
            ts = dates[start + h]
            pred = seasonal.get(_day_bucket(ts), 0.0)
            cum_err += float(chg.iloc[start + h]) - pred
            errs_by_h[h].append(cum_err)
    lo = [float(np.percentile(e, 20)) if e else 0.0 for e in errs_by_h]
    hi = [float(np.percentile(e, 80)) if e else 0.0 for e in errs_by_h]
    return lo, hi


def forecast(
    reserves_weekly: pd.Series,   # WRESBAL $M weekly
    walcl_weekly: pd.Series,      # WALCL $M weekly
    tga_daily: pd.Series,         # $B daily (DTS)
    kink_b: float | None,         # from Kink Engine ($B), may be None
) -> dict:
    res_b = (reserves_weekly.dropna() / 1000.0)
    if res_b.empty or tga_daily.dropna().empty:
        return {"ok": False, "reason": "missing reserves or TGA history"}
    res_now = float(res_b.iloc[-1])

    # Fed balance-sheet drift per business day from trailing 8 weeks.
    w = (walcl_weekly.dropna() / 1000.0).tail(9)
    fed_drift = float((w.iloc[-1] - w.iloc[0]) / (5 * (len(w) - 1))) if len(w) > 2 else 0.0

    seasonal = _seasonal_dtga(tga_daily)
    horizon = WEATHER_HORIZON_BDAYS
    future = pd.bdate_range(tga_daily.dropna().index[-1] + pd.Timedelta(days=1), periods=horizon)

    path, level = [], res_now
    for ts in future:
        dtga = seasonal.get(_day_bucket(ts), 0.0)
        level = level + fed_drift - dtga
        path.append(level)

    lo_err, hi_err = _backtest_band(tga_daily, seasonal, horizon)
    # TGA error flips sign into reserves (TGA build = reserve drain).
    lo_band = [p - h for p, h in zip(path, hi_err)]
    hi_band = [p - l for p, l in zip(path, lo_err)]

    crunches = []
    for i, ts in enumerate(future):
        cushion = CRUNCH_CUSHION_QEND_B if (ts.is_quarter_end or ts.is_month_end) else CRUNCH_CUSHION_B
        if kink_b is not None and lo_band[i] < kink_b + cushion:
            crunches.append(
                {
                    "date": ts.date().isoformat(),
                    "forecast_reserves_b": round(path[i], 1),
                    "worst_case_b": round(lo_band[i], 1),
                    "reason": "quarter/month-end + kink proximity" if cushion == CRUNCH_CUSHION_QEND_B else "kink proximity",
                }
            )

    min_i = int(np.argmin(path))
    return {
        "ok": True,
        "asof": tga_daily.dropna().index[-1].date().isoformat(),
        "current_reserves_b": round(res_now, 1),
        "fed_drift_per_bday_b": round(fed_drift, 2),
        "path": [
            [ts.date().isoformat(), round(p, 1), round(l, 1), round(h, 1)]
            for ts, p, l, h in zip(future, path, lo_band, hi_band)
        ],
        "min_forecast_b": round(path[min_i], 1),
        "min_forecast_date": future[min_i].date().isoformat(),
        "crunch_windows": crunches,
        "method": "reserves + trailing-8wk WALCL drift - seasonal dTGA (day-of-month median buckets, tax/qtr-end aware); bands = 20/80% backtested cumulative error",
    }


def weather_score(fc: dict, kink_b: float | None) -> float:
    """0-100: how threatening is the forward path."""
    if not fc.get("ok"):
        return 0.0
    n_crunch = len(fc.get("crunch_windows", []))
    base = min(n_crunch * 18.0, 70.0)
    if kink_b:
        headroom = fc["min_forecast_b"] - kink_b
        base += float(np.clip(1.0 - headroom / 500.0, 0.0, 1.0)) * 30.0
    return float(np.clip(base, 0.0, 100.0))
