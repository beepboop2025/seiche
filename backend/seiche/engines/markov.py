"""Regime-transition Markov — the funding-stress regime as a Markov chain.

Maps the reconstructed index history into the four regimes (CALM / EROSION /
STRAIN / STRESS), estimates the daily transition-probability matrix by counting,
and reads off the forward odds of reaching STRESS from where we are now, the
expected dwell time in the current regime, and the long-run mix.

Empirical and interpretable: there is no fit beyond counting, so it cannot
overclaim. It answers the question the regime framing begs — "from STRAIN, how
often does the next stop turn out to be STRESS, and how soon."
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import REGIMES

_ORDER = [name for _cut, name in REGIMES]          # CALM, EROSION, STRAIN, STRESS
_STRESS = _ORDER[-1]


def _regime_of(v: float) -> str:
    for cut, name in REGIMES:
        if v < cut:
            return name
    return _ORDER[-1]


def _stationary(P: np.ndarray, iters: int = 500) -> np.ndarray:
    v = np.ones(P.shape[0]) / P.shape[0]
    for _ in range(iters):
        v = v @ P
        s = v.sum()
        if s > 0:
            v = v / s
    return v


def analyze(index: pd.Series, regime_series: pd.Series | None = None,
            horizons: tuple[int, ...] = (5, 10, 21),
            current_regime: str | None = None) -> dict:
    idx = index.dropna()
    if len(idx) < 120:
        return {"ok": False, "reason": f"insufficient history ({len(idx)}d)"}

    if regime_series is not None and not regime_series.dropna().empty:
        labels = regime_series.dropna().astype(str).tolist()
    else:
        labels = [_regime_of(float(v)) for v in idx]

    n = len(_ORDER)
    pos = {name: i for i, name in enumerate(_ORDER)}
    C = np.zeros((n, n))
    for a, b in zip(labels[:-1], labels[1:]):
        if a in pos and b in pos:
            C[pos[a], pos[b]] += 1
    rowsum = C.sum(axis=1, keepdims=True)
    P = np.divide(C, rowsum, out=np.zeros_like(C), where=rowsum > 0)
    for i in range(n):                                  # unvisited regime: assume it stays
        if rowsum[i] == 0:
            P[i, i] = 1.0

    # Start from the LIVE board regime when given, so the reading agrees with the
    # published board rather than the reconstructed index's last label.
    cur = current_regime if current_regime in pos else (
        labels[-1] if labels[-1] in pos else _ORDER[0])
    ci, si = pos[cur], pos[_STRESS]

    # P(reach STRESS within h): make STRESS absorbing, propagate today's regime.
    Pabs = P.copy()
    Pabs[si, :] = 0.0
    Pabs[si, si] = 1.0
    dist = np.zeros(n)
    dist[ci] = 1.0
    reach: dict[str, float] = {}
    for step in range(1, max(horizons) + 1):
        dist = dist @ Pabs
        if step in horizons:
            reach[f"h{step}"] = round(float(dist[si]), 4)

    p_stay = float(P[ci, ci])
    dwell = round(1.0 / (1.0 - p_stay), 1) if p_stay < 1.0 else None
    pi = _stationary(P)

    return {
        "ok": True,
        "current_regime": cur,
        "regimes": _ORDER,
        "transition_matrix": [[round(float(P[i, j]), 3) for j in range(n)] for i in range(n)],
        "p_reach_stress": reach,
        "expected_dwell_bd": dwell,
        "stationary": {name: round(float(pi[i]), 3) for i, name in enumerate(_ORDER)},
        "n_days": int(len(idx)),
        "reading": (
            "empirical daily regime transitions. p_reach_stress makes STRESS "
            "absorbing and propagates today's regime forward N business days; "
            "dwell is the expected days before leaving the current regime. "
            "Counting only, no fit, so it can only be as representative as the "
            "sample."
        ),
    }
