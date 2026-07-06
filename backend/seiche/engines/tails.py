"""Tail Seismograph — distribution-tail pressure in secured funding rates.

The NY Fed publishes the full daily distribution (P1/P25/P75/P99) of SOFR,
TGCR and BGCR. Before the median ever moves, the 99th percentile detaches:
some desk somewhere is paying up. Tail pressure = P99 - P50, z-scored against
the trailing year, blended across rates and smoothed with a short EWMA.

Sep 15 2025 and Dec 31 2025 both showed tail detachment days before the
headline prints.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RATE_WEIGHTS = {"SOFR": 0.5, "TGCR": 0.3, "BGCR": 0.2}


def _zscore(s: pd.Series, window: int = 250) -> pd.Series:
    mu = s.rolling(window, min_periods=60).mean()
    sd = s.rolling(window, min_periods=60).std()
    return (s - mu) / sd.replace(0, np.nan)


def analyze(frames: dict[str, pd.DataFrame], iorb: pd.Series) -> dict:
    per_rate: dict[str, dict] = {}
    blended: list[pd.Series] = []

    for rate, w in RATE_WEIGHTS.items():
        df = frames.get(rate)
        if df is None or "percentPercentile99" not in df.columns:
            continue
        tail_bp = (df["percentPercentile99"] - df["percentRate"]) * 100.0
        z = _zscore(tail_bp)
        per_rate[rate] = {
            "tail_bp": round(float(tail_bp.dropna().iloc[-1]), 1) if not tail_bp.dropna().empty else None,
            "tail_z": round(float(z.dropna().iloc[-1]), 2) if not z.dropna().empty else None,
            "series": [
                [ts.date().isoformat(), round(float(v), 1)]
                for ts, v in tail_bp.dropna().tail(500).items()
            ],
        }
        blended.append(z * w)

    if not blended:
        return {"ok": False, "reason": "no percentile data"}

    tail_index = pd.concat(blended, axis=1).sum(axis=1, min_count=1).ewm(span=5).mean()

    # SOFR-IORB spread pressure (the median-level gauge), in bp.
    sofr = frames.get("SOFR", pd.DataFrame()).get("percentRate")
    spread_block = {}
    if sofr is not None:
        spread = (sofr - iorb.reindex(sofr.index).ffill()) * 100.0
        sz = _zscore(spread)
        spread_block = {
            "sofr_iorb_bp": round(float(spread.dropna().iloc[-1]), 1) if not spread.dropna().empty else None,
            "sofr_iorb_z": round(float(sz.dropna().iloc[-1]), 2) if not sz.dropna().empty else None,
            "series": [
                [ts.date().isoformat(), round(float(v), 1)]
                for ts, v in spread.dropna().tail(500).items()
            ],
        }

    ti_last = float(tail_index.dropna().iloc[-1]) if not tail_index.dropna().empty else 0.0
    return {
        "ok": True,
        "tail_index_z": round(ti_last, 2),
        "per_rate": per_rate,
        "spread": spread_block,
        "index_series": [
            [ts.date().isoformat(), round(float(v), 2)]
            for ts, v in tail_index.dropna().tail(500).items()
        ],
        "method": "P99-P50 per rate (bp), 250d z, blend SOFR .5/TGCR .3/BGCR .2, EWMA(5)",
    }


def tails_score(result: dict) -> float:
    """0-100 from the blended tail z and the SOFR-IORB spread z."""
    if not result.get("ok"):
        return 0.0
    tz = result.get("tail_index_z") or 0.0
    sz = (result.get("spread") or {}).get("sofr_iorb_z") or 0.0
    combo = 0.6 * tz + 0.4 * sz
    # z of 0 -> ~12, z of 2 -> ~65, z of 3.5+ -> ~95
    return float(np.clip(100.0 / (1.0 + np.exp(-(combo - 1.4) * 1.1)), 0.0, 100.0))
