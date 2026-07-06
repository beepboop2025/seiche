"""Hydrophone Array — how connected is the plumbing right now?

In a calm basin the funding segments (tri-party, DVP, fed funds, SRF, TGA,
RRP...) move on their own idiosyncratic noise: shocks are absorbed locally.
As stress builds the segments start moving TOGETHER — a shock anywhere
transmits everywhere. We measure that with the absorption ratio (Kritzman et
al.): the share of panel variance explained by the top principal components
of rolling standardized daily changes. Rising absorption = a densifying
network = the system has stopped absorbing and started transmitting.

Second output: the lead-lag map. For every ordered pair of series we find the
lag-k cross-correlation (k = 1..3 business days) and report the strongest
edges — which pipe is upstream of which right now. That map reorganizing is
itself a regime signal (and tells you which screen to watch tomorrow).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    HYDROPHONE_EDGE_MIN_ABS,
    HYDROPHONE_MAX_LAG_D,
    HYDROPHONE_TOP_PCS,
    HYDROPHONE_WINDOW_D,
)


def _absorption(window: pd.DataFrame) -> float | None:
    """Top-K eigenvalue share of the correlation matrix of standardized changes."""
    x = window.dropna(axis=1, thresh=int(len(window) * 0.7))
    x = x.dropna(how="any")
    if x.shape[1] < 4 or len(x) < 40:
        return None
    z = (x - x.mean()) / x.std().replace(0, np.nan)
    z = z.dropna(axis=1, how="any")
    if z.shape[1] < 4:
        return None
    corr = np.corrcoef(z.to_numpy().T)
    eig = np.linalg.eigvalsh(corr)
    eig = np.clip(eig, 0, None)
    total = float(eig.sum())
    if total <= 0:
        return None
    top = float(np.sort(eig)[-HYDROPHONE_TOP_PCS:].sum())
    return top / total


def analyze(panel: dict[str, pd.Series]) -> dict:
    """panel: name -> daily level series. Changes/z-scoring handled here."""
    df = pd.concat(panel, axis=1).sort_index().asfreq("B").ffill(limit=3)
    chg = df.diff()
    chg = chg.dropna(how="all")
    if len(chg) < HYDROPHONE_WINDOW_D + 60:
        return {"ok": False, "reason": f"insufficient panel history ({len(chg)}d)"}

    # Rolling absorption every 5 business days (daily would be wasteful).
    dates, values = [], []
    for end in range(HYDROPHONE_WINDOW_D, len(chg), 5):
        a = _absorption(chg.iloc[end - HYDROPHONE_WINDOW_D : end])
        if a is not None:
            dates.append(chg.index[end - 1])
            values.append(a)
    if not values:
        return {"ok": False, "reason": "absorption not computable (sparse panel)"}
    absorption = pd.Series(values, index=pd.DatetimeIndex(dates))

    current = float(absorption.iloc[-1])
    pctl = float((absorption <= current).mean() * 100.0)
    trend_60d = (
        float(current - absorption.iloc[-13]) if len(absorption) > 13 else 0.0
    )  # 13 samples x 5bd ~ 60bd

    # Lead-lag edges over the live window.
    win = chg.iloc[-HYDROPHONE_WINDOW_D:]
    win = win.dropna(axis=1, thresh=int(len(win) * 0.7))
    z = (win - win.mean()) / win.std().replace(0, np.nan)
    cols = list(z.columns)
    edges = []
    for i, a in enumerate(cols):
        for j, b in enumerate(cols):
            if i == j:
                continue
            best_r, best_lag = 0.0, 0
            for lag in range(1, HYDROPHONE_MAX_LAG_D + 1):
                x = z[a].shift(lag)
                pair = pd.concat([x, z[b]], axis=1).dropna()
                if len(pair) < 40:
                    continue
                r = float(pair.corr().iloc[0, 1])
                if abs(r) > abs(best_r):
                    best_r, best_lag = r, lag
            if abs(best_r) >= HYDROPHONE_EDGE_MIN_ABS:
                edges.append(
                    {"lead": a, "follows": b, "lag_d": best_lag, "corr": round(best_r, 2)}
                )
    edges.sort(key=lambda e: -abs(e["corr"]))
    # Keep the strongest edge per (lead, follows) direction pair only.
    seen, top_edges = set(), []
    for e in edges:
        k = (e["lead"], e["follows"])
        if k in seen:
            continue
        seen.add(k)
        top_edges.append(e)
        if len(top_edges) >= 12:
            break

    return {
        "ok": True,
        "asof": chg.index[-1].date().isoformat(),
        "absorption": round(current, 3),
        "absorption_pctl": round(pctl, 0),
        "trend_60d": round(trend_60d, 3),
        "n_series": int(z.shape[1]),
        "series": [
            [d.date().isoformat(), round(float(v), 3)] for d, v in absorption.items()
        ],
        "edges": top_edges,
        "method": (
            f"absorption ratio = top-{HYDROPHONE_TOP_PCS} PC variance share of "
            f"{HYDROPHONE_WINDOW_D}bd standardized daily changes (Kritzman), sampled 5bd; "
            f"edges = max |lag-k xcorr| k=1..{HYDROPHONE_MAX_LAG_D}, |r|>={HYDROPHONE_EDGE_MIN_ABS}"
        ),
    }


def hydrophone_score(result: dict) -> float:
    """0-100: percentile of current absorption + trend kicker."""
    if not result.get("ok"):
        return 0.0
    base = float(result.get("absorption_pctl") or 0.0) * 0.85
    if (result.get("trend_60d") or 0.0) > 0.05:
        base += 15.0
    return float(np.clip(base, 0.0, 100.0))
