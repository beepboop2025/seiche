"""Playbook — what happened the last N times the board looked like this?

State = (Seiche-lite regime bucket) x (Tell reading). For every historical
day in the same state we tabulate what liquid markets did over the next 5 and
20 business days — in native units (S&P return %, VIX pts, OAS bp, yield bp),
with n and hit rates printed. Decision support, not advice: the table shows
distributions, the operator owns the trade.

Overlap caveat printed with every table: consecutive days share forward
windows, so n_days >> n_independent (~ n_days / horizon). Both are shown.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    PLAYBOOK_HORIZONS_BD,
    PLAYBOOK_MIN_N,
    PLAYBOOK_OUTCOMES,
    REGIMES,
)

def _regime_of(v: float) -> str:
    return next(name for cutoff, name in REGIMES if v < cutoff)


def _tell_bucket(v: float) -> str:
    if v >= 15.0:
        return "plumbing leads price"
    if v <= -15.0:
        return "price leads plumbing"
    return "aligned"


def _fwd(s: pd.Series, h: int, kind: str) -> pd.Series:
    if kind == "pct":
        return (s.shift(-h) / s - 1.0) * 100.0
    if kind == "diff_bp":
        return (s.shift(-h) - s) * 100.0
    return s.shift(-h) - s  # "diff"


def analyze(
    lite_index: pd.Series,          # Seiche-lite daily index
    tell_series: pd.Series,         # daily Tell values
    outcomes: dict[str, pd.Series], # mnemonic -> level series (FRED market set)
) -> dict:
    idx = lite_index.dropna()
    tl = tell_series.dropna()
    if idx.empty or tl.empty:
        return {"ok": False, "reason": "missing index or tell history"}

    state = pd.concat({"idx": idx, "tell": tl}, axis=1).dropna()
    if len(state) < 300:
        return {"ok": False, "reason": "insufficient state overlap"}

    state["regime"] = state["idx"].map(_regime_of)
    state["bucket"] = state["tell"].map(_tell_bucket)

    cur_regime = str(state["regime"].iloc[-1])
    cur_bucket = str(state["bucket"].iloc[-1])
    mask = (state["regime"] == cur_regime) & (state["bucket"] == cur_bucket)
    # Exclude the trailing 20bd: their forward windows are still open.
    mask.iloc[-20:] = False
    match_days = state.index[mask]

    tables = []
    for mnem, (label, kind) in PLAYBOOK_OUTCOMES.items():
        s = outcomes.get(mnem)
        if s is None or s.dropna().empty:
            continue
        s = s.dropna()
        row = {"outcome": label, "mnemonic": mnem, "horizons": {}}
        for h in PLAYBOOK_HORIZONS_BD:
            fwd = _fwd(s, h, kind).reindex(match_days).dropna()
            n = int(len(fwd))
            if n < PLAYBOOK_MIN_N:
                row["horizons"][f"{h}d"] = {"n_days": n, "insufficient": True}
                continue
            n_indep = max(1, n // h)
            row["horizons"][f"{h}d"] = {
                "median": round(float(fwd.median()), 2),
                "p25": round(float(fwd.quantile(0.25)), 2),
                "p75": round(float(fwd.quantile(0.75)), 2),
                "pct_positive": round(float((fwd > 0).mean() * 100.0), 0),
                "n_days": n,
                "n_independent": n_indep,
                # fewer than 8 non-overlapping windows = an anecdote, not a table
                "low_confidence": n_indep < 8,
            }
        tables.append(row)

    return {
        "ok": True,
        "asof": state.index[-1].date().isoformat(),
        "state": {
            "regime": cur_regime,
            "tell_bucket": cur_bucket,
            "n_matching_days": int(mask.sum()),
        },
        "tables": tables,
        "caveat": (
            "historical distributions conditioned on the current state; overlapping "
            "forward windows mean n_days overstates independent samples (n_independent "
            "≈ n_days/horizon shown); native units; not investment advice"
        ),
        "method": (
            "state = Seiche-lite regime × Tell bucket; forward outcomes over "
            f"{PLAYBOOK_HORIZONS_BD} bd on matching historical days"
        ),
    }
