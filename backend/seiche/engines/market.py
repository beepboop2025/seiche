"""The Tell — plumbing-versus-price divergence.

The whole thesis in one number. Every 2025/26 stress event was visible in the
plumbing while price screens looked calm; The Tell measures exactly that gap:

    Tell = plumbing percentile − market-priced-stress percentile   (−100..+100)

Positive: the basin is sloshing and the screens haven't noticed (hedges are
cheap relative to what the plumbing knows). Negative: price is panicking
about something the plumbing says is not a funding event (funding-driven
selloffs mean-revert differently than solvency ones).

Market-priced stress = weighted rolling percentiles of VIX, HY OAS, IG OAS
and realized rates vol — official FRED series only, same keyless contract as
everything else. Divergence is a trading signal, not evidence of stress —
reported alongside the Seiche Index, never weighted into it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import TELL_MARKET_WEIGHTS, TELL_PERCENTILE_WINDOW_D

MIN_PCTL_PERIODS = 250


def _rpctl(s: pd.Series) -> pd.Series:
    """Rolling percentile rank (0-100) of the latest value vs trailing window."""
    return s.rolling(TELL_PERCENTILE_WINDOW_D, min_periods=MIN_PCTL_PERIODS).rank(pct=True) * 100.0


def market_stress(
    vix: pd.Series, hy_oas: pd.Series, ig_oas: pd.Series, dgs10: pd.Series
) -> tuple[pd.Series, dict]:
    """Weighted percentile blend + per-component detail."""
    rates_vol = (dgs10.diff() * 100.0).rolling(10).std()  # bp/day realized

    parts = {
        "VIX": (vix, "CBOE VIX", "pts"),
        "HY_OAS": (hy_oas, "HY OAS", "%"),
        "IG_OAS": (ig_oas, "IG OAS", "%"),
        "RATES_VOL": (rates_vol, "10y realized vol (10d)", "bp/d"),
    }
    pctls, detail = {}, {}
    for name, (s, label, unit) in parts.items():
        sp = s.dropna()
        if sp.empty:
            continue
        p = _rpctl(sp)
        pctls[name] = p
        last_p = p.dropna()
        detail[name] = {
            "label": label,
            "unit": unit,
            "last": round(float(sp.iloc[-1]), 2),
            "pctl": round(float(last_p.iloc[-1]), 0) if not last_p.empty else None,
        }
    if not pctls:
        return pd.Series(dtype=float), {}

    df = pd.concat(pctls, axis=1)
    w = pd.Series({k: TELL_MARKET_WEIGHTS[k] for k in df.columns})
    avail = df.notna()
    eff = avail.mul(w, axis=1)
    blend = (df.fillna(0.0) * eff).sum(axis=1) / eff.sum(axis=1).replace(0, np.nan)
    return blend.dropna(), detail


def tell(
    plumbing_index: pd.Series,   # Seiche-lite daily index (history.build)
    vix: pd.Series,
    hy_oas: pd.Series,
    ig_oas: pd.Series,
    dgs10: pd.Series,
) -> dict:
    if plumbing_index.dropna().empty:
        return {"ok": False, "reason": "no plumbing index history"}

    mkt, detail = market_stress(vix, hy_oas, ig_oas, dgs10)
    if mkt.empty:
        return {"ok": False, "reason": "no market stress components"}

    plumb_pctl = _rpctl(plumbing_index.dropna())
    both = pd.concat({"plumb": plumb_pctl, "mkt": mkt}, axis=1).dropna()
    if both.empty:
        return {"ok": False, "reason": "no overlap between plumbing and market series"}

    tell_s = both["plumb"] - both["mkt"]
    cur = float(tell_s.iloc[-1])
    return {
        "ok": True,
        "asof": both.index[-1].date().isoformat(),
        "tell": round(cur, 1),
        "plumbing_pctl": round(float(both["plumb"].iloc[-1]), 0),
        "market_pctl": round(float(both["mkt"].iloc[-1]), 0),
        "reading": (
            "plumbing leads price" if cur >= 15
            else "price leads plumbing" if cur <= -15
            else "aligned"
        ),
        "components": detail,
        "series": [
            [d.date().isoformat(), round(float(v), 1)] for d, v in tell_s.tail(500).items()
        ],
        "method": (
            f"Tell = rolling-{TELL_PERCENTILE_WINDOW_D}d percentile of Seiche-lite minus "
            "weighted rolling percentiles of VIX/HY OAS/IG OAS/10y realized vol "
            f"(weights {TELL_MARKET_WEIGHTS}); divergence is a signal, not stress evidence"
        ),
    }
