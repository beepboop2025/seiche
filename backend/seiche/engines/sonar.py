"""SONAR — the daily anomaly sweep.

Every series the collectors hold, pinged every day with the same question:
"is your latest print unusual, on level or on change?" Robust statistics only
(median/MAD — a squeeze day must not inflate its own yardstick). Output is a
ranked movers board: the terminal's answer to "what actually moved today?"

Context pane, not a composite input: an anomaly is a question, not a verdict.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import SONAR_LOOKBACK_D, SONAR_TOP_N, SONAR_Z_FLAG


def _robust_z(s: pd.Series) -> float | None:
    x = s.dropna().tail(SONAR_LOOKBACK_D)
    if len(x) < 60:
        return None
    med = float(x.median())
    mad = float((x - med).abs().median())
    scale = 1.4826 * mad
    if scale <= 0:
        return None
    return float((float(x.iloc[-1]) - med) / scale)


def sweep(series_map: dict[str, tuple[str, str, pd.Series]]) -> dict:
    """series_map: name -> (label, unit, daily/weekly level series)."""
    movers = []
    for name, (label, unit, s) in series_map.items():
        pts = s.dropna()
        if len(pts) < 60:
            continue
        level_z = _robust_z(pts)
        change_z = _robust_z(pts.diff())
        zs = [abs(z) for z in (level_z, change_z) if z is not None]
        if not zs:
            continue
        worst = max(zs)
        movers.append(
            {
                "name": name,
                "label": label,
                "unit": unit,
                "last": round(float(pts.iloc[-1]), 3),
                "chg_1d": round(float(pts.diff().iloc[-1]), 3) if len(pts) > 1 else None,
                "level_z": round(level_z, 2) if level_z is not None else None,
                "change_z": round(change_z, 2) if change_z is not None else None,
                "max_abs_z": round(worst, 2),
                "flag": worst >= SONAR_Z_FLAG,
                "asof": pts.index[-1].date().isoformat(),
            }
        )
    movers.sort(key=lambda m: -m["max_abs_z"])
    return {
        "ok": bool(movers),
        "n_scanned": len(movers),
        "n_flagged": sum(1 for m in movers if m["flag"]),
        "movers": movers[:SONAR_TOP_N],
        "method": (
            f"robust z = (last − median) / (1.4826·MAD) over trailing {SONAR_LOOKBACK_D} obs, "
            f"on level and 1d change; flag |z| ≥ {SONAR_Z_FLAG}"
        ),
    }
