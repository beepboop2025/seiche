"""Warehouse Engine — dealer balance-sheet saturation.

Primary dealers are the market's shock absorber of last resort: they take
down what auctions can't place and warehouse what forced sellers dump. A full
warehouse is an absorber that's already spent. We track net outright UST
positions by maturity bucket (NY Fed PD stats, weekly, T+9 by publication):
total saturation percentile, the 13-week build rate, and where on the curve
the inventory sits (long-end inventory is the expensive, hard-to-hedge kind).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import PD_POSITION_SERIES

LONG_END_BUCKETS = {"PDPOSGSC-G7L11", "PDPOSGSC-G11L21", "PDPOSGSC-G21"}


def analyze(positions: dict[str, pd.Series]) -> dict:
    if not positions:
        return {"ok": False, "reason": "no PD position data"}

    df = pd.concat(positions, axis=1).sort_index().ffill(limit=2)
    df = df.dropna(how="all")
    if len(df) < 30:
        return {"ok": False, "reason": f"insufficient PD history ({len(df)}w)"}

    total = df.sum(axis=1, min_count=max(1, df.shape[1] - 2))
    total = total.dropna()
    if total.empty:
        return {"ok": False, "reason": "PD total not computable"}

    now = float(total.iloc[-1])
    pctl = float((total <= now).mean() * 100.0)
    chg_13w = float(now - total.iloc[-14]) if len(total) > 14 else None

    long_cols = [c for c in df.columns if c in LONG_END_BUCKETS]
    long_share = (
        float(df[long_cols].iloc[-1].sum() / now * 100.0) if long_cols and now > 0 else None
    )

    buckets = []
    for keyid, label in PD_POSITION_SERIES.items():
        s = df.get(keyid)
        if s is None or s.dropna().empty:
            continue
        v = float(s.dropna().iloc[-1])
        b_pctl = float((s.dropna() <= v).mean() * 100.0)
        buckets.append({"bucket": label, "net_b": round(v, 1), "pctl": round(b_pctl, 0)})

    return {
        "ok": True,
        "asof": total.index[-1].date().isoformat(),
        "total_net_b": round(now, 1),
        "total_pctl": round(pctl, 0),
        "chg_13w_b": round(chg_13w, 1) if chg_13w is not None else None,
        "long_end_share_pct": round(long_share, 1) if long_share is not None else None,
        "buckets": buckets,
        "series": [
            [d.date().isoformat(), round(float(v), 1)] for d, v in total.tail(240).items()
        ],
        "method": (
            "NY Fed PD stats: net outright UST positions summed over bills + coupon "
            "buckets ($B, weekly, published T+9); saturation = percentile vs full "
            "spliced history; long-end = share in >7y buckets"
        ),
    }


def warehouse_score(result: dict) -> float:
    """0-100: saturation percentile, with a build-rate kicker."""
    if not result.get("ok"):
        return 0.0
    base = float(result.get("total_pctl") or 0.0) * 0.85
    if (result.get("chg_13w_b") or 0.0) > 40.0:  # +$40B in a quarter = fast fill
        base += 15.0
    return float(np.clip(base, 0.0, 100.0))
