"""CAESar — tomorrow's tail, estimated from the tail's own dynamics.

Rogue Wave (engines/roguewave.py) fits the STATIC law of the biggest waves the
basin can make; CAESar answers the operational question: given how the last
few days went, how bad could TOMORROW's pop be? It runs the CAViaR recursion
(Engle & Manganelli 2004) on THE shared pop statistic (SOFR−IORB minus its
trailing 5bd median — imported from backtest.pop_bp, never forked) and extends
it to a joint (VaR, ES) estimator following Gatta, Lillo & Mazzarisi,
"Conditional Autoregressive Expected Shortfall" (arXiv:2407.06619):

  [q_t; e_t] = [b0; g0] + [b1 b2; g1 g2]·[(y+); (y−)]_{t−1}
             + [b3 b4; g3 g4]·[q_{t−1}; e_{t−1}]

the paper's AS-(1,1) specification, estimated in its three steps: (1) CAViaR
quantile regression, here solved as an EXACT LP (the Koenker–Bassett pinball
program, scipy linprog/HiGHS) inside a fixed-point iteration over the
recursion's own lagged state; (2) the ES−VaR gap as an autoregression under
the BCGNS squared-excess loss with a soft monotonicity penalty; (3) joint
re-estimation of both coefficient vectors under the Fissler–Ziegel /
Patton–Ziegel–Chen loss with soft penalties for e > q (crossing) and q > 0.
Published pairs are monotonicity-repaired (e ← min(e, q)) — the soft
constraint made hard at print time, stated here rather than hidden.

The series is modeled in NEGATED space (y = −pop) so the left tail is the
stress tail, which is the paper's sign convention (VaR, ES ≤ 0, ES ≤ VaR);
published numbers are flipped back to bp of pop, where ES ≥ VaR.

Honesty notes:
  - expanding windows only: the walk-forward fits on prefixes, refits every
    5th observation at deterministic integer positions with coefficients
    warm-carried between refits, so no published value changes when future
    data arrives (unit-tested house invariant);
  - nothing prints before the expanding fit has 500 observations: thinner
    fits were found systematically miscalibrated in this engine's own
    walk-forward (q95 exceedance rates ~2x nominal below ~500 obs, much
    reduced above), so early forecasts are withheld — refusal beats false
    precision;
  - skill is stated, not asserted: out-of-sample one-step-ahead forecasts are
    scored against rolling-250bd climatology (order-statistic quantile,
    tail-mean ES) under the same FZ loss used for fitting, and the verdict
    SELF-DEMOTES to 'use climatology' when the loss ratio is >= 1;
  - the exceedance-rate table carries Wilson CIs; at the 99% level a few
    hundred origins hold only a handful of expected exceedances — wide
    intervals are the honest answer there;
  - NO composite score: tomorrow's VaR/ES bands are context for the basin,
    not evidence of stress today (doctrine).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import linprog, minimize

from seiche.engines.backtest import pop_bp

CAESAR_MIN_HISTORY_D = 550  # pop observations required before anything prints
CAESAR_T0 = 499             # first walk-forward origin: fit on a 500-obs prefix
CAESAR_REFIT_EVERY = 5      # weekly refits; coefficients carried between refits
CAESAR_LEVELS = (0.95, 0.99)
CAESAR_LAMBDA = 10.0        # soft-constraint penalties (paper's value)
CAESAR_CLIM_WINDOW = 250    # rolling window of the climatology benchmark
CAESAR_BURN_IN = 60         # prefix slice that seeds the recursions
_EPS = 1e-6                 # floor keeping log(-e) finite in the FZ loss
_NM_MAXITER = 400


# ---------------------------------------------------------------------------
# Small shared pieces
# ---------------------------------------------------------------------------

def _order_quantile(w: np.ndarray, theta: float) -> float:
    """Unconditional theta-quantile as an order statistic (k = ceil(theta*n))."""
    k = max(1, int(np.ceil(theta * len(w))))
    return float(np.sort(w)[k - 1])


def _clim_qe(w: np.ndarray, theta: float) -> tuple[float, float]:
    """Climatology pair: order-statistic quantile and the mean of the k
    smallest — ES <= VaR by construction, always defined."""
    k = max(1, int(np.ceil(theta * len(w))))
    s = np.sort(w)
    return float(s[k - 1]), float(s[:k].mean())


def _burnin_qe(y: np.ndarray, theta: float) -> tuple[float, float]:
    return _clim_qe(y[:CAESAR_BURN_IN], theta)


def _pinball(y: np.ndarray, q: np.ndarray, theta: float) -> float:
    return float(np.mean((y - q) * (theta - (y < q))))


def _rolling_quantile_path(y: np.ndarray, theta: float) -> np.ndarray:
    """Deterministic cold-start path for the CAViaR fixed point: the rolling
    (expanding at the left edge) unconditional theta-quantile."""
    n = len(y)
    out = np.empty(n)
    for t in range(n):
        lo = max(0, t + 1 - CAESAR_CLIM_WINDOW)
        out[t] = _order_quantile(y[lo:t + 1], theta)
    return out


# ---------------------------------------------------------------------------
# Step 1 — CAViaR via exact LP inside a recursion fixed point
# ---------------------------------------------------------------------------

def _caviar_recurse(beta: np.ndarray, y: np.ndarray, q0: float) -> np.ndarray:
    """q_t = b0 + b1*(y+){t-1} + b2*(y-){t-1} + b3*q_{t-1}, q_0 = q0."""
    n = len(y)
    q = np.empty(n)
    q[0] = q0
    yp = np.maximum(y, 0.0)
    ym = np.maximum(-y, 0.0)
    b0, b1, b2, b3 = beta
    for t in range(1, n):
        q[t] = b0 + b1 * yp[t - 1] + b2 * ym[t - 1] + b3 * q[t - 1]
    return q


def _qr_lp(X: np.ndarray, y: np.ndarray, theta: float) -> np.ndarray:
    """Koenker–Bassett quantile regression as an LP: with residuals split
    r = u − v, u,v >= 0, the pinball loss min sum theta*u + (1−theta)*v is
    linear. Solved by HiGHS on a sparse constraint matrix. The persistence
    column (last) is bounded to [0, 0.999]: a CAViaR recursion is meant to be
    a stable, persistent filter — an unbounded LP otherwise spends thin-tail
    fits on oscillatory negative-persistence optima that die out-of-sample."""
    n, p = X.shape
    c = np.concatenate([np.zeros(p), theta * np.ones(n), (1.0 - theta) * np.ones(n)])
    A = sparse.hstack(
        [sparse.csr_matrix(X), sparse.identity(n), -sparse.identity(n)], format="csc"
    )
    res = linprog(
        c, A_eq=A, b_eq=y,
        bounds=[(None, None)] * (p - 1) + [(0.0, 0.999)] + [(0.0, None)] * (2 * n),
        method="highs",
    )
    if res.status != 0:
        raise RuntimeError(f"quantile LP failed (status {res.status})")
    return np.asarray(res.x[:p], dtype=float)


def _fit_caviar_once(y: np.ndarray, theta: float, q0: float,
                     start: np.ndarray | None) -> tuple[np.ndarray | None, np.ndarray, float]:
    """One fixed-point run: iterate (rebuild design from current path -> exact
    LP -> regenerate path), keeping the best TRUE recursive pinball loss seen.
    `start` None means the rolling-quantile path init; otherwise a coefficient
    vector whose recursion seeds the first design."""
    n = len(y)
    yp = np.maximum(y, 0.0)
    ym = np.maximum(-y, 0.0)
    if start is not None:
        qpath = _caviar_recurse(start, y, q0)
        beta = start.copy()
    else:
        qpath = _rolling_quantile_path(y, theta)
        qpath[0] = q0
        beta = None
    best_beta: np.ndarray | None = None
    best_q = qpath
    best_loss = np.inf
    for _ in range(8):
        X = np.column_stack([np.ones(n - 1), yp[:-1], ym[:-1], qpath[:-1]])
        try:
            beta_new = _qr_lp(X, y[1:], theta)
        except RuntimeError:
            break
        q_new = _caviar_recurse(beta_new, y, q0)
        loss = _pinball(y[1:], q_new[1:], theta)
        if loss < best_loss:
            best_loss, best_beta, best_q = loss, beta_new, q_new
        if beta is not None and np.max(np.abs(beta_new - beta)) < 1e-4:
            break
        beta, qpath = beta_new, q_new
    return best_beta, best_q, best_loss


def _fit_caviar(y: np.ndarray, theta: float, q0: float,
                warm: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Fixed-point CAViaR: the lagged q in the design makes the problem
    nonlinear and the pinball landscape multi-modal (Engle–Manganelli's
    warning), so cold fits run the fixed point from three deterministic
    starts — rolling-quantile path, and persistent anchors at beta3 = 0.7/0.9
    around the unconditional quantile — and keep the best true loss. Warm
    fits (between weekly refits) run once from the carried coefficients."""
    starts: list[np.ndarray | None]
    if warm is not None:
        starts = [warm]
    else:
        qbar = _order_quantile(y, theta)
        starts = [None] + [np.array([(1.0 - b3) * qbar, 0.0, 0.0, b3]) for b3 in (0.7, 0.9)]
    best_beta: np.ndarray | None = None
    best_q: np.ndarray | None = None
    best_loss = np.inf
    for s in starts:
        b, q, loss = _fit_caviar_once(y, theta, q0, s)
        if b is not None and loss < best_loss:
            best_loss, best_beta, best_q = loss, b, q
    if best_beta is None:  # every LP refused — degrade to a constant quantile
        best_beta = np.array([_order_quantile(y, theta), 0.0, 0.0, 0.0])
        best_q = _caviar_recurse(best_beta, y, q0)
    return best_beta, best_q


# ---------------------------------------------------------------------------
# Step 2 — the ES−VaR gap as an autoregression (BCGNS loss + soft penalty)
# ---------------------------------------------------------------------------

def _gap_recurse(g: np.ndarray, y: np.ndarray, qhat: np.ndarray, r0: float) -> np.ndarray:
    """r_t = g0 + g1*(y+){t-1} + g2*(y-){t-1} + g3*q_{t-1} + g4*r_{t-1}."""
    n = len(y)
    r = np.empty(n)
    r[0] = r0
    yp = np.maximum(y, 0.0)
    ym = np.maximum(-y, 0.0)
    g0, g1, g2, g3, g4 = g
    for t in range(1, n):
        r[t] = g0 + g1 * yp[t - 1] + g2 * ym[t - 1] + g3 * qhat[t - 1] + g4 * r[t - 1]
    return r


def _gap_design(y: np.ndarray, qhat: np.ndarray, rpath: np.ndarray) -> np.ndarray:
    n = len(y)
    return np.column_stack([
        np.ones(n - 1), np.maximum(y[:-1], 0.0), np.maximum(-y[:-1], 0.0),
        qhat[:-1], rpath[:-1],
    ])


def _gap_loss(r: np.ndarray, c: np.ndarray) -> float:
    """True recursive step-2 loss: mean (r + c)^2 + lambda * sum (r)+, with
    c_t = (VaR_t − y_t)+ / theta the scaled excess loss (paper eq. 15)."""
    with np.errstate(over="ignore", invalid="ignore"):
        return float(np.mean((r + c) ** 2) + CAESAR_LAMBDA * np.sum(np.maximum(r, 0.0)))


def _fit_es_gap(y: np.ndarray, qhat: np.ndarray, theta: float, r0: float,
                warm: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Same fixed-point pattern as step 1: with the lagged r in the design
    held fixed the objective is a convex QP in g (solved by L-BFGS-B with the
    analytic gradient; the hinge penalty contributes its subgradient)."""
    c = np.maximum(qhat - y, 0.0) / theta
    if warm is not None:
        g = warm.copy()
        rpath = _gap_recurse(g, y, qhat, r0)
    else:
        g = np.array([-float(np.mean(c)), 0.0, 0.0, 0.0, 0.5])
        rpath = _gap_recurse(g, y, qhat, r0)
    best_g, best_r = g.copy(), rpath
    best_loss = _gap_loss(rpath[1:], c[1:])
    # |g4| < 1 keeps the recursion's fixed point stable; without the bound
    # L-BFGS-B's line search walks into explosive paths and overflows.
    bounds = [(None, None)] * 4 + [(-0.999, 0.999)]
    for _ in range(6):
        W = _gap_design(y, qhat, rpath)
        cw = c[1:]

        def fg(gv: np.ndarray) -> tuple[float, np.ndarray]:
            r = W @ gv
            with np.errstate(over="ignore", invalid="ignore"):
                f = float(np.mean((r + cw) ** 2) + CAESAR_LAMBDA * np.sum(np.maximum(r, 0.0)))
            if not np.isfinite(f):
                return 1e12, np.zeros_like(gv)
            grad = (2.0 / len(cw)) * W.T @ (r + cw) + CAESAR_LAMBDA * W.T @ (r > 0.0)
            return f, grad

        res = minimize(fg, g, jac=True, method="L-BFGS-B", bounds=bounds,
                       options={"maxiter": 200})
        g_new = res.x if np.all(np.isfinite(res.x)) else g
        r_new = _gap_recurse(g_new, y, qhat, r0)
        loss = _gap_loss(r_new[1:], c[1:])
        if loss < best_loss:
            best_loss, best_g, best_r = loss, g_new, r_new
        if np.max(np.abs(g_new - g)) < 1e-4:
            break
        g, rpath = g_new, r_new
    return best_g, best_r


# ---------------------------------------------------------------------------
# Step 3 — joint re-estimation under the Fissler–Ziegel / PZC loss
# ---------------------------------------------------------------------------

def _joint_recurse(p: np.ndarray, y: np.ndarray, q0: float, e0: float) -> tuple[np.ndarray, np.ndarray]:
    """The paper's AS-(1,1) joint recursion (eq. 17), cross terms included."""
    n = len(y)
    q = np.empty(n)
    e = np.empty(n)
    q[0], e[0] = q0, e0
    yp = np.maximum(y, 0.0)
    ym = np.maximum(-y, 0.0)
    b0, b1, b2, b3, b4, g0, g1, g2, g3, g4 = p
    for t in range(1, n):
        q[t] = b0 + b1 * yp[t - 1] + b2 * ym[t - 1] + b3 * q[t - 1] + b4 * e[t - 1]
        e[t] = g0 + g1 * yp[t - 1] + g2 * ym[t - 1] + g3 * q[t - 1] + g4 * e[t - 1]
    return q, e


def _fz_path_loss(q: np.ndarray, e: np.ndarray, y: np.ndarray, theta: float) -> float:
    """Fissler–Ziegel / Patton–Ziegel–Chen joint (VaR, ES) loss (paper eq. 6):
    mean[ q/e − (q−y)/(theta·e)·1{y<=q} + log(−e) ], with the e-floor the only
    guard — callers pass fitted paths already known to be negative."""
    ef = np.minimum(e, -_EPS)
    hit = y <= q
    return float(np.mean(q / ef - (q - y) * hit / (theta * ef) + np.log(-ef)))


def _joint_objective(p: np.ndarray, y: np.ndarray, theta: float,
                     q0: float, e0: float) -> float:
    q, e = _joint_recurse(p, y, q0, e0)
    if (
        not np.all(np.isfinite(q)) or not np.all(np.isfinite(e))
        or np.max(np.abs(q)) > 1e7 or np.max(np.abs(e)) > 1e7
        or np.any(e >= -_EPS)
    ):
        over = np.sum(np.maximum(e + _EPS, 0.0)) if np.all(np.isfinite(e)) else 1e6
        return 1e12 + 1e6 * float(over)
    qs, es, ys = q[1:], e[1:], y[1:]
    loss = _fz_path_loss(qs, es, ys, theta)
    # paper eq. 16: soft monotonicity (e <= q) and non-positivity (q <= 0)
    loss += CAESAR_LAMBDA * float(
        np.sum(np.maximum(es - qs, 0.0)) + np.sum(np.maximum(qs, 0.0))
    )
    return loss


def _fit_joint(p0: np.ndarray, y: np.ndarray, theta: float,
               q0: float, e0: float) -> np.ndarray:
    """Nelder–Mead on the penalized FZ objective, warm-started from the
    step-1/2 estimates (or the previous refit's optimum). Never returns
    something worse than its start point."""
    f0 = _joint_objective(p0, y, theta, q0, e0)
    res = minimize(
        _joint_objective, p0, args=(y, theta, q0, e0), method="Nelder-Mead",
        options={"maxiter": _NM_MAXITER, "maxfev": _NM_MAXITER + 50,
                 "xatol": 1e-4, "fatol": 1e-6},
    )
    if np.all(np.isfinite(res.x)) and res.fun < f0:
        return res.x
    return p0


def _fit_level(y: np.ndarray, theta: float, q0: float, e0: float,
               warm: dict | None) -> tuple[np.ndarray, dict]:
    """One full three-step CAESar fit on a prefix. warm carries the previous
    refit's coefficients: beta for step 1, the inverted-map gap coefficients
    for step 2, and the joint vector for step 3."""
    beta, qhat = _fit_caviar(y, theta, q0, warm=None if warm is None else warm["beta"])
    gt, _ = _fit_es_gap(y, qhat, theta, e0 - q0, warm=None if warm is None else warm["gt"])
    # paper's coefficient map (eq. following 15): fold the gap fit onto the
    # CAViaR vector to initialize the joint (q, e) system; beta's e-term
    # coefficient starts at zero.
    p0 = np.array([
        beta[0], beta[1], beta[2], beta[3], 0.0,
        gt[0] + beta[0], gt[1] + beta[1], gt[2] + beta[2],
        gt[3] + beta[3] - gt[4], gt[4],
    ])
    if warm is not None:
        # the previous refit's joint optimum is the better start
        p0 = warm["p"] if _joint_objective(warm["p"], y, theta, q0, e0) < _joint_objective(p0, y, theta, q0, e0) else p0
    p = _fit_joint(p0, y, theta, q0, e0)
    new_warm = {
        "beta": p[0:4].copy(),
        "gt": np.array([p[5] - p[0], p[6] - p[1], p[7] - p[2], p[8] - p[3] + p[9], p[9]]),
        "p": p.copy(),
    }
    return p, new_warm


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------

def _walk_level(y: np.ndarray, theta: float) -> dict:
    """Expanding-window walk-forward at one level. Origin t fits (only on
    scheduled refit positions) on the prefix y[:t+1] and forecasts day t+1.
    Returns the per-origin forecasts (negated space), the climatology pair,
    and the carried-coefficient chain is internal and deterministic."""
    n = len(y)
    q0, e0 = _burnin_qe(y, theta)
    q_fc = np.full(n, np.nan)
    e_fc = np.full(n, np.nan)
    qc_fc = np.full(n, np.nan)
    ec_fc = np.full(n, np.nan)
    warm = None
    p = None
    for t in range(CAESAR_T0, n):
        if p is None or (t - CAESAR_T0) % CAESAR_REFIT_EVERY == 0:
            p, warm = _fit_level(y[:t + 1], theta, q0, e0, warm)
        q_path, e_path = _joint_recurse(p, y[:t + 1], q0, e0)
        yp, ym = max(y[t], 0.0), max(-y[t], 0.0)
        q_next = p[0] + p[1] * yp + p[2] * ym + p[3] * q_path[t] + p[4] * e_path[t]
        e_next = p[5] + p[6] * yp + p[7] * ym + p[8] * q_path[t] + p[9] * e_path[t]
        e_next = min(e_next, q_next)  # monotonicity repair, stated in method
        q_fc[t], e_fc[t] = q_next, e_next
        w = y[max(0, t + 1 - CAESAR_CLIM_WINDOW):t + 1]
        qc_fc[t], ec_fc[t] = _clim_qe(w, theta)
    return {"q": q_fc, "e": e_fc, "qc": qc_fc, "ec": ec_fc}


def _wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1.0 + z * z / n
    ctr = p + z * z / (2.0 * n)
    half = z * np.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return (float((ctr - half) / den), float((ctr + half) / den))


def _fz_point(y: float, q: float, e: float, theta: float) -> float:
    ef = min(e, -_EPS)
    hit = 1.0 if y <= q else 0.0
    return q / ef - (q - y) * hit / (theta * ef) + float(np.log(-ef))


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def analyze(spread_bp: pd.Series) -> dict:
    """CAESar (VaR, ES) bands for the NEXT business day's pop, at the 95% and
    99% levels, with walk-forward skill vs climatology stated. Input:
    SOFR−IORB spread in bp, daily DatetimeIndex. No composite score — context,
    not evidence."""
    pop = pop_bp(spread_bp).dropna()
    n = int(len(pop))
    if n < CAESAR_MIN_HISTORY_D:
        return {"ok": False, "reason": f"insufficient pop history ({n}d < {CAESAR_MIN_HISTORY_D}d)"}

    pop_arr = pop.to_numpy(dtype=float)
    y = -pop_arr  # negated space: left tail = stress tail (paper's convention)
    origins = np.arange(CAESAR_T0, n)
    scored = origins[:-1]  # the last origin forecasts tomorrow: nothing to score

    levels: dict[str, dict] = {}
    wf_frames: dict[str, pd.DataFrame] = {}
    ratios: dict[str, float | None] = {}
    reliability: list[dict] = []
    for q_level in CAESAR_LEVELS:
        lab = f"q{int(round(q_level * 100))}"
        theta = 1.0 - q_level
        wf = _walk_level(y, theta)

        # --- out-of-sample scoring vs climatology (FZ loss, same as fitting) ---
        l_model = np.array([_fz_point(y[t + 1], wf["q"][t], wf["e"][t], theta) for t in scored])
        l_clim = np.array([_fz_point(y[t + 1], wf["qc"][t], wf["ec"][t], theta) for t in scored])
        m_model, m_clim = float(np.mean(l_model)), float(np.mean(l_clim))
        ratio = (m_model / m_clim) if m_clim > 1e-9 and m_model > 0.0 else None
        ratios[lab] = None if ratio is None else round(float(ratio), 4)

        hits = np.array([y[t + 1] <= wf["q"][t] for t in scored])
        lo, hi = _wilson(int(hits.sum()), int(hits.size))
        reliability.append({
            "level": lab,
            "nominal": round(theta, 2),
            "exceedance_rate": round(float(hits.mean()), 4),
            "n_origins": int(hits.size),
            "wilson95": [round(lo, 4), round(hi, 4)],
        })

        levels[lab] = {
            "theta": round(theta, 2),
            "var_bp": round(float(-wf["q"][n - 1]), 3),
            "es_bp": round(float(-wf["e"][n - 1]), 3),
            "loss_ratio_vs_climatology": ratios[lab],
            "mean_fz_loss": {"caesar": round(m_model, 5), "climatology": round(m_clim, 5)},
        }
        realized = np.full(origins.size, np.nan)
        has = origins + 1 < n
        realized[has] = pop_arr[origins[has] + 1]
        wf_frames[lab] = pd.DataFrame(
            {
                "pos": origins,
                "var_bp": -wf["q"][origins],
                "es_bp": -wf["e"][origins],
                "clim_var_bp": -wf["qc"][origins],
                "clim_es_bp": -wf["ec"][origins],
                "realized_bp": realized,
            },
            index=pop.index[origins],
        )

    both = [ratios[lab] for lab in ("q95", "q99")]
    if all(r is not None and r < 1.0 for r in both):
        verdict = "use caesar"
        verdict_detail = (
            f"walk-forward FZ loss beats climatology at both levels "
            f"(q95 {both[0]:.3f}, q99 {both[1]:.3f} — below 1 is better)"
        )
    else:
        verdict = "use climatology"
        shown = ", ".join(f"{lab} {r:.3f}" if r is not None else f"{lab} n/a" for lab, r in zip(("q95", "q99"), both))
        verdict_detail = (
            f"SELF-DEMOTION: walk-forward loss ratio vs climatology >= 1 at "
            f"at least one level ({shown}) — the unconditional band is the "
            f"honest forecast here"
        )

    crossing = not (
        levels["q99"]["var_bp"] >= levels["q95"]["var_bp"] - 1e-9
        and levels["q99"]["es_bp"] >= levels["q95"]["es_bp"] - 1e-9
    )

    caveats = [
        "modeled in negated space (y = −pop) so the left tail is the stress tail — the paper's "
        "sign convention; published bands are flipped back to bp of pop, where ES >= VaR",
        "monotonicity is a SOFT constraint in estimation (penalty lambda=10, paper's value); the "
        "published pairs are repaired hard (ES never below VaR) — stated, not hidden",
        "estimation detail: the CAViaR step solves exact pinball LPs (scipy linprog/HiGHS) inside "
        "a fixed-point iteration over the recursion's own lag — a local scheme, not the global "
        "nonlinear optimum; weekly refits are warm-started from the previous refit",
        f"the 99% level expects ~{0.01 * len(scored):.0f} exceedances over {len(scored)} scored "
        f"origins — the q99 reliability interval is wide by construction, read the Wilson CI",
        f"no forecast is published before the expanding fit has {CAESAR_T0 + 1} observations: "
        f"thinner fits measured systematically miscalibrated in this engine's own walk-forward, "
        f"so early origins are withheld rather than scored — refusal beats false precision",
        "the FZ loss floors ES at −1e-6 bp to stay finite; a forecaster (usually climatology) "
        "whose ES is not strictly negative eats a large penalty — directionally honest, "
        "magnitude arbitrary",
        "no refit-uncertainty CIs on the VaR/ES bands themselves (a bootstrap over the three-step "
        "fit is not cheap); the walk-forward skill ratio and the reliability table are the "
        "uncertainty statement offered here",
        "no composite score: tomorrow's tail bands are context for the basin, not evidence of "
        "stress today (doctrine)",
    ]
    if verdict == "use climatology":
        caveats.insert(0, verdict_detail)
    if crossing:
        caveats.append(
            "cross-level quantile crossing at the final fit (q99 band inside q95) — the two "
            "levels are separate fits and the paper constrains only ES<=VaR within a level"
        )

    method = (
        f"pop = SOFR−IORB − trailing 5bd median (imported from backtest.pop_bp, THE shared event "
        f"statistic; n={n}bd), modeled in negated space y = −pop. CAESar AS-(1,1) per "
        f"arXiv:2407.06619 (Gatta–Lillo–Mazzarisi) — CAViaR (Engle–Manganelli 2004) extended to "
        f"a joint (VaR, ES) estimator: [q; e]_t = [b0; g0] + [b1 b2; g1 g2]·[(y+); (y−)]"
        f"{{t−1}} + [b3 b4; g3 g4]·[q; e]{{t−1}}. Three steps: (1) CAViaR quantile regression as "
        f"an exact Koenker–Bassett LP (linprog/HiGHS, persistence bounded to [0, 0.999]) in a "
        f"fixed point over the recursion's lag, multi-started from three deterministic anchors "
        f"on cold refits; "
        f"(2) ES−VaR gap autoregression under the BCGNS squared-excess loss with soft "
        f"monotonicity penalty; (3) joint Nelder–Mead re-estimation of the Fissler–Ziegel / "
        f"Patton–Ziegel–Chen loss with penalties for e > q and q > 0 (lambda={CAESAR_LAMBDA:g}); "
        f"published pairs monotonicity-repaired. Expanding-window walk-forward from a "
        f"{CAESAR_T0 + 1}bd prefix, refit every {CAESAR_REFIT_EVERY}bd with coefficients carried "
        f"between refits; one-step-ahead forecasts scored under the same FZ loss against "
        f"rolling-{CAESAR_CLIM_WINDOW}bd climatology (order-statistic quantile, tail-mean ES). "
        f"Verdict self-demotes to climatology when the loss ratio >= 1."
    )

    return {
        "ok": True,
        "asof": pop.index[-1].date().isoformat(),
        "n_days": n,
        "var95_bp": levels["q95"]["var_bp"],
        "es95_bp": levels["q95"]["es_bp"],
        "var99_bp": levels["q99"]["var_bp"],
        "es99_bp": levels["q99"]["es_bp"],
        "levels": levels,
        "loss_ratio_vs_climatology": ratios,
        "reliability": reliability,
        "verdict": verdict,
        "verdict_detail": verdict_detail,
        "n_origins": int(len(scored)),
        "caveats": caveats,
        "method": method,
        "_wf": wf_frames,
    }
