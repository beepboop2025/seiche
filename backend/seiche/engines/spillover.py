"""Spillover — directional funding-stress connectedness across the harbors.

Harbors reads each national money market's own water line; Basins measures how
COUPLED the dollar basins are (absorption ratio, lead-lag). Neither answers the
question a contagion desk actually asks: when one harbor's funding tightens,
WHERE does the stress go, and who is the source?

This engine answers it with the standard tool — the Diebold-Yilmaz connectedness
index (Diebold & Yilmaz 2012, 2014) built on the generalized (order-invariant)
forecast-error variance decomposition of Pesaran & Shin (1998). Fit a VAR on the
daily panel of harbor rate- and FX-changes; decompose each node's H-step forecast
error variance into the shares coming from every other node; the off-diagonal
shares ARE the directional spillovers:

  TOTAL     one number, 0-100: the share of system forecast-error variance that
            is cross-node rather than own — low = each harbor to its own calendar,
            high = one shock moves the whole system (a globally-fragile regime).
  TO(i)     how much of everyone else's variance i transmits — i as a SOURCE.
  FROM(i)   how much of i's own variance comes from others — i as a SINK.
  NET(i)    TO − FROM: a net transmitter (>0) leads the system; a net receiver
            (<0) is downstream. The largest net transmitter is this window's
            stress source.

This is the SRR (arXiv:2512.17185) / multilayer-network (arXiv:2602.10960) move
from correlation snapshots to a structural, directional contagion graph. Two
honesty rails carried on its face, both from that literature:

  - it measures CONNECTEDNESS, not causation: a VAR spillover is a statistical
    lead in forecast-error variance, not a proven transmission channel;
  - it is blind to EXOGENOUS shocks. SRR's own finding is that a correlation/
    VAR graph missed the COVID crash, which arrived through policy/liquidity, not
    through rising cross-market linkage. So this engine is the STRUCTURAL
    complement to the physics engines (critical-slowing, Bathymetry), which fire
    on exogenous shocks it cannot see — never a replacement.

Monthly-cadence harbors (India, Korea OECD mirrors) cannot join a daily VAR and
are excluded from the graph by construction, labeled as such — never interpolated
to a daily series to pad the panel.

Deterministic, numpy-only; the generalized FEVD is order-invariant, so unlike a
Cholesky decomposition there is no arbitrary node ordering to defend.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_OBS_PER_PARAM = 8   # VAR needs data: require this many rows per estimated coef
MAX_NODES = 8           # keep the VAR well-conditioned on keyless daily history
DEFAULT_LAG = 2
DEFAULT_HORIZON = 10


def _var_ols(Y: np.ndarray, p: int) -> tuple[list[np.ndarray], np.ndarray]:
    """Fit VAR(p) with a constant by OLS. Y is (T, K). Returns (A_1..A_p, Sigma)
    where each A_m is (K, K) and Sigma is the (K, K) residual covariance."""
    T, K = Y.shape
    rows = T - p
    # design: [1, Y_{t-1}, ..., Y_{t-p}] predicting Y_t
    X = np.ones((rows, 1 + K * p))
    for lag in range(1, p + 1):
        X[:, 1 + (lag - 1) * K : 1 + lag * K] = Y[p - lag : T - lag]
    Yt = Y[p:]
    # least squares (pinv for numerical safety on collinear panels)
    B, *_ = np.linalg.lstsq(X, Yt, rcond=None)   # (1+Kp, K)
    resid = Yt - X @ B
    dof = max(rows - (1 + K * p), 1)
    Sigma = (resid.T @ resid) / dof
    coefs = B[1:]                                 # drop the constant row
    A = [coefs[(m) * K:(m + 1) * K].T for m in range(p)]   # each (K, K)
    return A, Sigma


def _vma(A: list[np.ndarray], H: int) -> list[np.ndarray]:
    """Moving-average coefficients Theta_0..Theta_{H-1} from the VAR AR matrices:
    Theta_0 = I, Theta_l = sum_{m=1}^{min(l,p)} A_m Theta_{l-m}."""
    K = A[0].shape[0]
    p = len(A)
    theta = [np.eye(K)]
    for l in range(1, H):
        acc = np.zeros((K, K))
        for m in range(1, min(l, p) + 1):
            acc += A[m - 1] @ theta[l - m]
        theta.append(acc)
    return theta


def gfevd(A: list[np.ndarray], Sigma: np.ndarray, H: int) -> np.ndarray:
    """Normalized generalized FEVD (Pesaran-Shin 1998, Diebold-Yilmaz 2014).

    alpha_ij = sigma_ii^{-1} * sum_l (e_i' Theta_l Sigma e_j)^2
               / sum_l (e_i' Theta_l Sigma Theta_l' e_i)
    then row-normalized so each row sums to 1 (the DY-2014 fix for the fact that
    generalized shares are order-invariant but do not naturally sum to unity —
    Chan-Lau 2017). Returns the (K, K) table; row i is where i's variance comes
    from, so off-diagonal row-sum = FROM, off-diagonal col-sum = TO.
    """
    theta = _vma(A, H)
    K = Sigma.shape[0]
    sig_ii = np.diag(Sigma).copy()
    sig_ii[sig_ii <= 0] = np.finfo(float).eps
    num = np.zeros((K, K))
    den = np.zeros(K)
    for Th in theta:
        TS = Th @ Sigma                     # (K, K); row i · e_j = e_i' Th Sigma e_j
        num += (TS ** 2)                    # elementwise (e_i' Th Sigma e_j)^2
        den += np.einsum("ij,ij->i", TS, Th)  # e_i' Th Sigma Th' e_i
    den[den <= 0] = np.finfo(float).eps
    alpha = (num / sig_ii[None, :]) / den[:, None]
    return alpha / alpha.sum(axis=1, keepdims=True)


def connectedness(table: np.ndarray) -> dict:
    """Total, and per-node TO / FROM / NET from a normalized FEVD table."""
    K = table.shape[0]
    off = table - np.diag(np.diag(table))
    frm = off.sum(axis=1)          # from others into i (row off-diагonal)
    to = off.sum(axis=0)           # to others from i (column off-diagonal)
    total = 100.0 * off.sum() / K  # rows sum to 1, so this is mean cross-share
    return {"total": total, "to": to, "from": frm, "net": to - frm}


def analyze(series_map: dict[str, pd.Series], lag: int = DEFAULT_LAG,
            horizon: int = DEFAULT_HORIZON) -> dict:
    """series_map: node label -> a daily series (rate level or FX level). Nodes
    are differenced to changes, inner-joined on common dates, and fed to the
    VAR. Fewer than 2 nodes, or too little overlapping history for a stable
    VAR, degrades loudly rather than fitting noise."""
    live = {k: v.dropna() for k, v in series_map.items() if v is not None and not v.dropna().empty}
    if len(live) < 2:
        return {"ok": False, "reason": "need at least 2 daily nodes for a spillover graph"}
    if len(live) > MAX_NODES:
        # keep the longest-history nodes; a wide VAR on short keyless data is noise
        live = dict(sorted(live.items(), key=lambda kv: -len(kv[1]))[:MAX_NODES])

    panel = pd.concat({k: v for k, v in live.items()}, axis=1).sort_index()
    changes = panel.diff().dropna(how="any")
    nodes = list(changes.columns)
    K = len(nodes)
    T = len(changes)
    need = MIN_OBS_PER_PARAM * (1 + K * lag)
    if T < need:
        return {"ok": False,
                "reason": f"insufficient overlapping daily history for a VAR "
                          f"({T} rows, need ~{need} for {K} nodes at lag {lag})",
                "nodes": nodes}

    Y = changes.to_numpy(dtype=float)
    # standardize per column so a large-variance node (FX) doesn't dominate the
    # decomposition purely by scale — connectedness should be about co-movement.
    Y = (Y - Y.mean(axis=0)) / (Y.std(axis=0) + np.finfo(float).eps)

    A, Sigma = _var_ols(Y, lag)
    table = gfevd(A, Sigma, horizon)
    c = connectedness(table)

    order = np.argsort(-c["net"])
    ranked = [{
        "node": nodes[i],
        "to": round(float(c["to"][i]) * 100, 1),
        "from": round(float(c["from"][i]) * 100, 1),
        "net": round(float(c["net"][i]) * 100, 1),
        "role": "transmitter" if c["net"][i] > 0 else "receiver",
    } for i in order]

    transmitter = ranked[0] if ranked and ranked[0]["net"] > 0 else None
    receiver = ranked[-1] if ranked and ranked[-1]["net"] < 0 else None

    return {
        "ok": True,
        "asof": changes.index[-1].date().isoformat(),
        "nodes": nodes,
        "n_obs": T,
        "lag": lag,
        "horizon": horizon,
        "total_connectedness": round(float(c["total"]), 1),
        "directional": ranked,
        "source": transmitter["node"] if transmitter else None,
        "sink": receiver["node"] if receiver else None,
        "verdict": (
            f"system connectedness {c['total']:.0f}/100"
            + (f"; {transmitter['node']} is the net stress SOURCE this window "
               f"(net +{transmitter['net']:.0f})" if transmitter else
               "; no clear net transmitter — stress is diffuse")
        ),
        "caveats": [
            "connectedness is a statistical lead in forecast-error variance, NOT a "
            "proven transmission channel — read it as structure, not causation",
            "blind to EXOGENOUS shocks: a VAR/correlation graph missed the COVID "
            "crash (SRR, arXiv:2512.17185), which arrived through policy/liquidity "
            "not through rising linkage — the physics engines are its complement here",
            "generalized (order-invariant) FEVD, row-normalized (Diebold-Yilmaz 2014); "
            "monthly-cadence harbors are excluded from the daily VAR, never interpolated",
            f"a {K}-node VAR(p={lag}) on keyless daily history — a short or collinear "
            "panel widens the error bars the point estimates here do not show",
        ],
        "method": (
            f"Diebold-Yilmaz connectedness: VAR({lag}) by OLS on standardized daily "
            f"changes of {K} nodes, generalized FEVD (Pesaran-Shin 1998) at horizon "
            f"{horizon}, row-normalized; TOTAL = mean cross-node variance share, "
            f"NET(i) = TO(i) − FROM(i)."
        ),
    }
