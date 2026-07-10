"""Sea State — the marine regime scale, estimated instead of asserted.

Mariners grade the water on a defined scale — sea state 2 is not an opinion,
it is a measurement convention. The terminal's regime words (CALM/EROSION/
STRAIN/STRESS) are editorial thresholds on the composite; this engine
estimates the regime the way a statistician would: a two-state Gaussian
hidden Markov model (Hamilton 1989) on the detrended spread residual, where
the two states are learned from the data — a low-variance "calm water" state
and a high-variance "rough water" state — and the published number is the
FILTERED P(rough): the probability, using only information available at each
close, that the basin is currently in its rough regime.

Filtered, never smoothed: the smoother uses the future and is forbidden
here (a smoothed regime probability is the single most common look-ahead in
published regime studies). The Hamilton filter is strictly causal. EM is
hand-rolled (numpy only, deterministic initialization, fixed tolerance) —
no new dependency, no RNG, and label switching is resolved by convention:
ROUGH is the state with the larger variance, always.

Placement among the siblings: the composite's regime words are editorial
(tunable opinion); Undertow measures damping loss; Sea State asks the
narrower statistical question "which variance regime is the water in RIGHT
NOW, and how persistent are these regimes historically?" Its transition
matrix is publishable knowledge by itself: the expected duration of rough
water is a number operators plan around.

Honesty notes: prefix refits at deterministic positions (truncation
equality); the filtered history uses only prefix-fitted parameters and a
causal filter; walk-forward validation against PIT climatology on the
shared PROOF label with a self-demoting verdict; context, never composite.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    BACKTEST_EVENT_FWD_D,
    BACKTEST_SPIKE_BP,
    SEASTATE_DETREND_D,
    SEASTATE_EM_ITERS,
    SEASTATE_MIN_HISTORY_D,
    SEASTATE_REFIT_EVERY_BD,
    SEASTATE_WARMUP_D,
)
from seiche.engines.backtest import pop_bp

_SIG_FLOOR_FRAC = 0.05   # emission sigma floor, as a fraction of overall std
_EM_TOL = 1e-7


def _detrend(spread_bp: pd.Series) -> pd.Series:
    s = spread_bp.dropna()
    return (s - s.rolling(SEASTATE_DETREND_D, min_periods=10).median().shift(1)).dropna()


def _emission(x: np.ndarray, mu: np.ndarray, sig: np.ndarray) -> np.ndarray:
    """N x 2 Gaussian densities."""
    z = (x[:, None] - mu[None, :]) / sig[None, :]
    return np.exp(-0.5 * z * z) / (np.sqrt(2.0 * np.pi) * sig[None, :])


def _em_fit(x: np.ndarray) -> dict | None:
    """Deterministic EM for a 2-state Gaussian HMM. Initialization is a fixed
    convention (calm = within 1.5 robust sigmas of the median), so the same
    prefix always yields the same fit — no RNG, no label ambiguity."""
    n = x.size
    if n < 200:
        return None
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med))) * 1.4826
    scale = max(mad, 1e-6)
    calm = np.abs(x - med) <= 1.5 * scale
    if calm.sum() < 50 or (~calm).sum() < 20:
        calm = np.abs(x - med) <= np.quantile(np.abs(x - med), 0.8)
    floor = max(_SIG_FLOOR_FRAC * float(np.std(x)), 1e-6)

    mu = np.array([float(np.mean(x[calm])), float(np.mean(x[~calm]))])
    sig = np.maximum(np.array([float(np.std(x[calm])), float(np.std(x[~calm]))]), floor)
    P = np.array([[0.95, 0.05], [0.10, 0.90]])
    pi = np.array([0.5, 0.5])

    prev_ll = -np.inf
    for _ in range(SEASTATE_EM_ITERS):
        B = np.maximum(_emission(x, mu, sig), 1e-300)
        # forward (scaled)
        a = np.empty((n, 2))
        c = np.empty(n)
        a0 = pi * B[0]
        c[0] = a0.sum()
        a[0] = a0 / c[0]
        for t in range(1, n):
            at = (a[t - 1] @ P) * B[t]
            c[t] = at.sum()
            a[t] = at / c[t]
        ll = float(np.log(c).sum())
        # backward (scaled)
        b = np.empty((n, 2))
        b[-1] = 1.0
        for t in range(n - 2, -1, -1):
            b[t] = (P @ (B[t + 1] * b[t + 1])) / c[t + 1]
        g = a * b
        g /= g.sum(axis=1, keepdims=True)
        # transitions
        xi = np.zeros((2, 2))
        for t in range(n - 1):
            m = (a[t][:, None] * P) * (B[t + 1] * b[t + 1])[None, :]
            xi += m / m.sum()
        P = xi / xi.sum(axis=1, keepdims=True)
        pi = g[0]
        w = g.sum(axis=0)
        mu = (g * x[:, None]).sum(axis=0) / w
        var = (g * (x[:, None] - mu[None, :]) ** 2).sum(axis=0) / w
        sig = np.maximum(np.sqrt(var), floor)
        if abs(ll - prev_ll) < _EM_TOL * max(1.0, abs(prev_ll)):
            break
        prev_ll = ll

    rough = int(np.argmax(sig))  # convention: ROUGH = larger variance, always
    order = [1 - rough, rough]
    return {
        "mu": mu[order], "sig": sig[order],
        "P": P[np.ix_(order, order)], "pi": pi[order],
        "loglik": ll,
    }


def _filter_p_rough(x: np.ndarray, fit: dict) -> np.ndarray:
    """Hamilton filter — strictly causal P(state=rough | x_0..x_t)."""
    B = np.maximum(_emission(x, fit["mu"], fit["sig"]), 1e-300)
    P = fit["P"]
    p = fit["pi"].copy()
    out = np.empty(x.size)
    for t in range(x.size):
        pred = p @ P if t > 0 else p
        post = pred * B[t]
        p = post / post.sum()
        out[t] = p[1]
    return out


def analyze(spread_bp: pd.Series) -> dict:
    resid = _detrend(spread_bp)
    n = int(len(resid))
    if n < SEASTATE_MIN_HISTORY_D:
        return {"ok": False, "reason": f"insufficient residual history ({n}d < {SEASTATE_MIN_HISTORY_D}d)"}
    x = resid.to_numpy(dtype=float)

    # ---- current fit: the expanding fit at T = now -------------------------
    fit = _em_fit(x)
    if fit is None:
        return {"ok": False, "reason": "EM failed on the full residual history"}
    p_now_series = _filter_p_rough(x, fit)
    p_rough_now = float(p_now_series[-1])
    p_cc, p_rr = float(fit["P"][0, 0]), float(fit["P"][1, 1])
    dur_calm = 1.0 / max(1.0 - p_cc, 1e-6)
    dur_rough = 1.0 / max(1.0 - p_rr, 1e-6)

    # ---- walk-forward filtered history (prefix fits, causal filter) --------
    refit_ks = list(range(SEASTATE_MIN_HISTORY_D, n, SEASTATE_REFIT_EVERY_BD))
    p_hist = pd.Series(np.nan, index=resid.index)
    for j, k in enumerate(refit_ks):
        fk = _em_fit(x[:k])
        if fk is None:
            continue
        seg_end = refit_ks[j + 1] if j + 1 < len(refit_ks) else n
        filt = _filter_p_rough(x[:seg_end], fk)  # causal: value at t uses x[0..t]
        p_hist.iloc[k:seg_end] = filt[k:seg_end]

    # ---- walk-forward validation vs PIT climatology ------------------------
    pop = pop_bp(spread_bp).reindex(resid.index)
    fwd_max = pd.concat(
        [pop.shift(-k) for k in range(1, BACKTEST_EVENT_FWD_D + 1)], axis=1
    ).max(axis=1)
    y = (fwd_max >= BACKTEST_SPIKE_BP).astype(float)
    y[fwd_max.isna()] = np.nan
    clim = y.expanding(min_periods=60).mean().shift(BACKTEST_EVENT_FWD_D)

    val: dict = {"ok": False, "reason": "insufficient scored history"}
    sc = pd.concat({"p": p_hist, "y": y, "c": clim}, axis=1).dropna()
    sc = sc[sc.index >= resid.index[min(SEASTATE_WARMUP_D, n - 1)]]
    if len(sc) >= 300 and 0 < sc["y"].sum() < len(sc):
        yv = sc["y"].to_numpy()
        pv, cv = sc["p"].to_numpy(), sc["c"].to_numpy()

        def _auroc(scores):
            pos = scores[yv > 0.5]
            neg = scores[yv <= 0.5]
            if pos.size == 0 or neg.size == 0:
                return None
            ranks = pd.Series(scores).rank(method="average").to_numpy()
            rp = ranks[yv > 0.5]
            return round(float((rp.sum() - pos.size * (pos.size + 1) / 2) / (pos.size * neg.size)), 3)

        # NB: P(rough) is a regime probability, not P(event) — Brier against
        # the event label is reported for the CLIMATOLOGY comparison shape
        # only; AUROC (pure ranking) is the fair score for a regime gauge.
        auroc_p, auroc_c = _auroc(pv), _auroc(cv)
        beats = auroc_p is not None and auroc_c is not None and auroc_p > auroc_c
        val = {
            "ok": True,
            "n_scored": int(len(sc)),
            "base_rate": round(float(yv.mean()), 3),
            "auroc_p_rough": auroc_p,
            "auroc_climatology": auroc_c,
            "beats_climatology": bool(beats),
            "verdict": (
                "filtered P(rough) ranks funding events better than climatology — the "
                "estimated regime carries forward information"
                if beats else
                "filtered P(rough) does NOT out-rank climatology on events — read it as a "
                "descriptive regime gauge, not a forecast"
            ),
        }

    state_now = "ROUGH" if p_rough_now >= 0.5 else "calm"
    reading = (
        f"the water is {state_now}: filtered P(rough) = {p_rough_now:.2f}; rough spells "
        f"historically persist ~{dur_rough:.0f}bd once entered (calm ~{dur_calm:.0f}bd)"
    )

    rows = [
        [d.date().isoformat(), round(float(v), 3)]
        for d, v in p_hist.dropna().iloc[::3].items()
    ]

    return {
        "ok": True,
        "asof": resid.index[-1].date().isoformat(),
        "n_days": n,
        "p_rough_now": round(p_rough_now, 3),
        "states": {
            "calm": {"mu_bp": round(float(fit["mu"][0]), 2), "sigma_bp": round(float(fit["sig"][0]), 2),
                     "expected_duration_bd": round(dur_calm, 0)},
            "rough": {"mu_bp": round(float(fit["mu"][1]), 2), "sigma_bp": round(float(fit["sig"][1]), 2),
                      "expected_duration_bd": round(dur_rough, 0)},
        },
        "transition": {"p_stay_calm": round(p_cc, 3), "p_stay_rough": round(p_rr, 3)},
        "rows": rows,
        "validation": val,
        "reading": reading,
        "caveats": [
            "FILTERED probabilities only — the smoother uses the future and is forbidden here "
            "(smoothed regime probabilities are the classic look-ahead of the regime literature)",
            "two states is an editorial choice (the marine scale has nine; the sample supports "
            "two); ROUGH = larger-variance state by fixed convention, so labels cannot switch",
            "prefix refits at deterministic positions + a causal filter: no published value "
            "changes when future data arrives (house invariant)",
            "the composite's regime words are editorial thresholds; this is the statistical "
            "counterpart — when they disagree, that disagreement is information",
            "context, never composite (doctrine)",
        ],
        "method": (
            f"2-state Gaussian HMM (Hamilton filter, hand-rolled EM: deterministic init, "
            f"tol {_EM_TOL:g}, ≤{SEASTATE_EM_ITERS} iters, sigma floor) on the spread residual "
            f"(x − trailing {SEASTATE_DETREND_D}bd median, shifted). History: prefix refits at "
            f"{SEASTATE_MIN_HISTORY_D} rows then every {SEASTATE_REFIT_EVERY_BD}bd, filtered "
            f"causally. Validation: AUROC of filtered P(rough) for the shared PROOF event "
            f"within {BACKTEST_EVENT_FWD_D}bd vs expanding PIT climatology, self-demoting."
        ),
        "_p_rough": p_hist,
    }
