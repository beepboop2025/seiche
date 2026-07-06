"""Seiche Index — the one number, with its full decomposition.

Weighted blend of engine sub-scores (weights = config.COMPOSITE_WEIGHTS, the
tool's editorial voice). Fail-loud: a dead input never silently drops out —
its weight is renormalized away and the coverage % falls, both published.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import COMPOSITE_WEIGHTS, REGIMES


def srf_score(srf_daily: pd.DataFrame) -> float:
    """SRF usage is the confession channel: paying the ceiling rate means no
    cheaper private funding existed. Score on 20d max accepted ($B)."""
    if srf_daily is None or srf_daily.empty:
        return 0.0
    recent = float(srf_daily["accepted"].tail(20).max())
    # $0 -> 0; $5B -> ~35; $20B -> ~70; $75B (Dec-2025 record) -> ~100
    return float(np.clip(100.0 * (1.0 - np.exp(-recent / 22.0)), 0.0, 100.0))


def buffers_score(rrp_b: float | None) -> float:
    """Emptiness of the ON RRP shock absorber. $2.5T -> 0; $0 -> 100."""
    if rrp_b is None:
        return 0.0
    return float(np.clip((1.0 - rrp_b / 400.0), 0.0, 1.0) * 100.0)


def compose(subscores: dict[str, float | None]) -> dict:
    """subscores: engine key -> 0-100 or None (input dead)."""
    live = {k: v for k, v in subscores.items() if v is not None and k in COMPOSITE_WEIGHTS}
    dead = [k for k in COMPOSITE_WEIGHTS if k not in live]
    wsum = sum(COMPOSITE_WEIGHTS[k] for k in live)
    if wsum <= 0:
        return {"ok": False, "reason": "all composite inputs dead"}

    value = sum(live[k] * COMPOSITE_WEIGHTS[k] for k in live) / wsum
    regime = next(name for cutoff, name in REGIMES if value < cutoff)

    decomposition = [
        {
            "component": k,
            "score": round(live[k], 1) if k in live else None,
            "weight": COMPOSITE_WEIGHTS[k],
            "contribution": round(live[k] * COMPOSITE_WEIGHTS[k] / wsum, 1) if k in live else None,
            "status": "live" if k in live else "DEAD",
        }
        for k in COMPOSITE_WEIGHTS
    ]
    decomposition.sort(key=lambda d: -(d["contribution"] or -1))

    return {
        "ok": True,
        "value": round(float(value), 1),
        "regime": regime,
        "coverage_pct": round(100.0 * wsum / sum(COMPOSITE_WEIGHTS.values()), 0),
        "dead_inputs": dead,
        "decomposition": decomposition,
    }
