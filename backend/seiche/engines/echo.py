"""Echo Engine — historical stress-fingerprint matching.

Build a daily state vector of plumbing z-scores, take today's trailing
ECHO_WINDOW-day trajectory, and measure its distance to the trajectory that
*preceded* each labeled stress episode (windows ending 0..30 days before the
break). Output: "today resembles T-minus-N days before <episode>".

Resemblance is context, not evidence — reported alongside the Seiche Index
but deliberately not weighted into it (see config).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import ECHO_LEADS, ECHO_WINDOW, EPISODES


def build_state(components: dict[str, pd.Series]) -> pd.DataFrame:
    """Full-sample z-scored daily state matrix from named component series."""
    df = pd.concat(components, axis=1).sort_index()
    df = df.asfreq("B").ffill(limit=7)
    z = (df - df.mean()) / df.std().replace(0, np.nan)
    return z.dropna(how="all")


def _traj(z: pd.DataFrame, end_loc: int, window: int) -> np.ndarray | None:
    if end_loc - window + 1 < 0:
        return None
    block = z.iloc[end_loc - window + 1 : end_loc + 1]
    if block.isna().mean().mean() > 0.25:
        return None
    return block.fillna(0.0).to_numpy()


def match(z: pd.DataFrame) -> dict:
    if len(z) < ECHO_WINDOW + 40:
        return {"ok": False, "reason": "insufficient state history"}
    now = _traj(z, len(z) - 1, ECHO_WINDOW)
    if now is None:
        return {"ok": False, "reason": "current window too sparse"}

    results = []
    for ep_date, ep_label in EPISODES.items():
        ts = pd.Timestamp(ep_date)
        locs = z.index.searchsorted(ts)
        if locs >= len(z.index):
            continue
        best = None
        for lead in ECHO_LEADS:
            end_loc = locs - lead
            # Exclude self-matches: skip windows overlapping the live window.
            if end_loc >= len(z) - ECHO_WINDOW - 5:
                continue
            hist = _traj(z, end_loc, ECHO_WINDOW)
            if hist is None:
                continue
            rmse = float(np.sqrt(np.mean((now - hist) ** 2)))
            sim = 1.0 / (1.0 + rmse)
            if best is None or sim > best["similarity"]:
                best = {"lead_days": int(lead), "similarity": round(sim, 3)}
        if best:
            results.append({"episode": ep_label, "date": ep_date, **best})

    if not results:
        return {"ok": False, "reason": "no comparable episodes in sample"}
    results.sort(key=lambda r: -r["similarity"])
    return {
        "ok": True,
        "asof": z.index[-1].date().isoformat(),
        "components": list(z.columns),
        "matches": results,
        "top": results[0],
        "method": f"RMSE similarity of {ECHO_WINDOW}d z-trajectories vs pre-episode windows (leads 0-30d)",
    }
