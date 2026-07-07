"""Rogue Wave — the tail law of the basin.

Extreme value theory is literally the mathematics of rogue waves: the
Pickands–Balkema–de Haan theorem says that excesses over a high threshold
converge to the Generalized Pareto Distribution no matter what the bulk of
the water is doing. Peaks-over-threshold on THE shared pop statistic
(SOFR−IORB minus its trailing 5bd median — imported from backtest.pop_bp,
never forked) is therefore the honest way to speak about pops LARGER than
any in the sample: return levels and P(pop ≥ x) beyond history, with the
uncertainty stated instead of hidden.

Placement among the siblings:
  - Swell's empirical exceedance curves stop dead at the largest pop in the
    sample — they price Thursday's 10bp risk, but have nothing to say about
    a wave nobody has seen. The GPD is the honest instrument for the wave
    that is NOT in the sample yet.
  - Bathymetry (engines/bathymetry.py) reconstructs the TYPICAL dynamics of
    the same pop statistic — the drift, the diffusion, the shape of the
    well; Rogue Wave models its EXTREMES. Two ends of the same
    distribution, one shared variable.

Method: threshold u at the ROGUE_THRESHOLD_PCTL-th percentile of the
positive pops over the full available history; runs declustering keeping
cluster MAXIMA — deliberately unlike PROOF's first-day convention (PROOF
wants lead time, so it keeps the first day of a run; EVT wants magnitudes,
so it keeps the biggest wave of the episode — same water, different
question); GPD fitted to the cluster excesses by probability-weighted
moments (Hosking & Wallis 1987 — numpy only, no scipy, no distributional
hand-waving); return levels and within-horizon exceedance probabilities
from the fitted tail plus the empirical cluster rate lambda_u.

Honesty notes:
  - expanding statistics only: the current fit uses all data up to now (it
    IS the expanding fit at T=now); the historical xi series refits at
    deterministic integer positions (ROGUE_MIN_HISTORY_D rows, then every
    252) so truncation equality holds exactly — the value at T never
    changes when future data arrives (unit-tested house invariant);
  - small-n honesty: n printed everywhere, bootstrap CIs on xi and on every
    return level (ROGUE_BOOT_N resamples, fixed seed), a threshold-
    sensitivity table, and a loud caveat when xi is unstable across
    thresholds;
  - the bootstrap resamples the excess vector with lambda_u held FIXED — a
    stated simplification that leaves the intervals, if anything, too
    narrow;
  - NO composite score: a tail law is context about the basin, not evidence
    of stress today (doctrine).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    ROGUE_BOOT_N,
    ROGUE_DECLUSTER_BD,
    ROGUE_HORIZONS_BD,
    ROGUE_MIN_EXCEED,
    ROGUE_MIN_HISTORY_D,
    ROGUE_RETURN_YEARS,
    ROGUE_SEED,
    ROGUE_SENS_PCTLS,
    ROGUE_SEVERITIES_BP,
    ROGUE_THRESHOLD_PCTL,
)
from seiche.engines.backtest import pop_bp

_BD_PER_YEAR = 252.0
_XI_LINEAR = 0.01  # |xi| below this: use the xi->0 (exponential) return-level limit


def _decluster(pop: pd.Series, u: float) -> tuple[list[pd.Timestamp], np.ndarray]:
    """Runs declustering, cluster MAXIMA: scan the days with pop > u in
    order (integer positions on the pop index); a new cluster starts when
    the previous exceedance sits more than ROGUE_DECLUSTER_BD rows back;
    within a cluster keep the maximum pop and its date."""
    vals = pop.to_numpy(dtype=float)
    locs = np.flatnonzero(vals > u)
    if locs.size == 0:
        return [], np.array([], dtype=float)
    dates: list[pd.Timestamp] = []
    maxima: list[float] = []
    best_v, best_d = vals[locs[0]], pop.index[locs[0]]
    prev = int(locs[0])
    for loc in locs[1:]:
        if loc - prev > ROGUE_DECLUSTER_BD:
            dates.append(best_d)
            maxima.append(float(best_v))
            best_v, best_d = vals[loc], pop.index[loc]
        elif vals[loc] > best_v:
            best_v, best_d = vals[loc], pop.index[loc]
        prev = int(loc)
    dates.append(best_d)
    maxima.append(float(best_v))
    return dates, np.asarray(maxima, dtype=float)


def _gpd_pwm(e: np.ndarray) -> tuple[float, float] | None:
    """GPD(xi, beta) by probability-weighted moments, Hosking & Wallis 1987.

    With e sorted ascending, the plotting-position PWM estimators are
      a0 = mean(e)                                          (= E[X])
      a1 = (1/n) sum_j ((n-1-j)/(n-1)) e_(j), j = 0..n-1    (= E[X (1-F(X))])
    and, in the heavy-tail-positive convention S(x) = (1 + xi x/beta)^(-1/xi),
      xi = 2 - a0/(a0 - 2 a1),   beta = 2 a0 a1 / (a0 - 2 a1).
    NB the weights DESCEND on the ascending sort: a1 is the SURVIVAL-weighted
    moment E[X(1-F)] — the F-weighted moment plugged into these same closed
    forms is inconsistent (checked against Hosking–Wallis and pinned by a
    Monte-Carlo unit test on 5000 iid GPD draws).
    Returns None on degenerate moments (a0 - 2 a1 <= 0)."""
    e = np.asarray(e, dtype=float)
    n = e.size
    if n < 5:
        return None
    es = np.sort(e)
    a0 = float(es.mean())
    w = (n - 1.0 - np.arange(n)) / (n - 1.0)
    a1 = float((es * w).mean())
    denom = a0 - 2.0 * a1
    if a0 <= 0.0 or denom <= 0.0:
        return None
    xi = 2.0 - a0 / denom
    beta = 2.0 * a0 * a1 / denom
    if beta <= 0.0:
        return None
    return float(xi), float(beta)


def _return_level(u: float, lam: float, m: float, xi, beta):
    """m-business-day return level; scalar or vectorized over bootstrap draws.
    x_m = u + (beta/xi)((m lam)^xi - 1) for |xi| > 0.01, else the xi->0
    limit u + beta ln(m lam)."""
    xi_a = np.asarray(xi, dtype=float)
    beta_a = np.asarray(beta, dtype=float)
    ml = m * lam
    small = np.abs(xi_a) <= _XI_LINEAR
    xi_safe = np.where(small, 1.0, xi_a)
    rl = np.where(
        small,
        u + beta_a * np.log(ml),
        u + beta_a / xi_safe * (np.power(ml, xi_a) - 1.0),
    )
    return float(rl) if np.ndim(xi) == 0 else rl


def _gpd_survival(x: float, u: float, xi: float, beta: float) -> float:
    """S(x) = P(cluster max > x | exceedance) for x >= u, with the support
    guard for xi < 0: S = 0 beyond the fitted endpoint u - beta/xi."""
    z = (x - u) / beta
    if z < 0.0:
        return 1.0
    if abs(xi) < 1e-9:
        return float(np.exp(-z))
    arg = 1.0 + xi * z
    if arg <= 0.0:
        return 0.0  # x beyond the bounded-tail endpoint
    return float(arg ** (-1.0 / xi))


def _bootstrap(e: np.ndarray, u: float, lam: float) -> dict | None:
    """Percentile-bootstrap CIs on xi and on every return level: resample
    the excess vector with replacement ROGUE_BOOT_N times (lambda_u held
    fixed — stated simplification), refit PWM per draw, 2.5/97.5 pctls.
    Degenerate-moment draws are dropped; returns None if too few survive."""
    rng = np.random.default_rng(ROGUE_SEED)
    n = e.size
    samp = np.sort(e[rng.integers(0, n, size=(ROGUE_BOOT_N, n))], axis=1)
    a0 = samp.mean(axis=1)
    w = (n - 1.0 - np.arange(n)) / (n - 1.0)
    a1 = (samp * w).mean(axis=1)
    denom = a0 - 2.0 * a1
    ok = (a0 > 0.0) & (denom > 0.0)
    safe = np.where(ok, denom, 1.0)
    xi_b = 2.0 - a0 / safe
    beta_b = 2.0 * a0 * a1 / safe
    ok &= beta_b > 0.0
    xi_b, beta_b = xi_b[ok], beta_b[ok]
    if xi_b.size < ROGUE_BOOT_N // 4:
        return None
    out = {
        "n_kept": int(xi_b.size),
        "xi_ci": [round(float(np.percentile(xi_b, q)), 3) for q in (2.5, 97.5)],
        "rl_ci": {},
    }
    for years in ROGUE_RETURN_YEARS:
        rls = _return_level(u, lam, years * _BD_PER_YEAR, xi_b, beta_b)
        out["rl_ci"][years] = [round(float(np.percentile(rls, q)), 1) for q in (2.5, 97.5)]
    return out


def _fit_prefix(pop: pd.Series) -> float | None:
    """One full refit (threshold + decluster + PWM) on a pop PREFIX — the
    historical xi series. Depends only on the prefix, so the value at a
    refit position never changes when future data arrives."""
    arr = pop.to_numpy(dtype=float)
    pos = arr[arr > 0.0]
    if pos.size < 10:
        return None
    uk = float(np.quantile(pos, ROGUE_THRESHOLD_PCTL / 100.0))
    _, cmax = _decluster(pop, uk)
    if cmax.size < ROGUE_MIN_EXCEED:
        return None
    fit = _gpd_pwm(cmax - uk)
    return None if fit is None else fit[0]


def analyze(spread_bp: pd.Series) -> dict:
    """Peaks-over-threshold GPD tail fit of the shared pop statistic.
    Input: SOFR−IORB spread in bp, daily DatetimeIndex. Publishes return
    levels and within-horizon exceedance probabilities beyond the sample
    maximum, with bootstrap CIs. No composite score — context, not evidence."""
    pop = pop_bp(spread_bp).dropna()
    n_days = int(len(pop))
    if n_days < ROGUE_MIN_HISTORY_D:
        return {"ok": False, "reason": f"insufficient pop history ({n_days}d < {ROGUE_MIN_HISTORY_D}d)"}

    arr = pop.to_numpy(dtype=float)
    pos = arr[arr > 0.0]
    if pos.size < 10:
        return {"ok": False, "reason": f"only {pos.size} positive pop days — no exceedances to fit a tail on"}

    u = float(np.quantile(pos, ROGUE_THRESHOLD_PCTL / 100.0))
    _, cl_max = _decluster(pop, u)
    n_cl = int(cl_max.size)
    if n_cl < ROGUE_MIN_EXCEED:
        return {
            "ok": False,
            "reason": (
                f"only {n_cl} declustered exceedances above u={u:.1f}bp — "
                f"need >= {ROGUE_MIN_EXCEED} before a tail fit prints"
            ),
        }

    e = cl_max - u
    fit = _gpd_pwm(e)
    if fit is None:
        return {"ok": False, "reason": "degenerate PWM moments (b0 - 2*b1 <= 0) — no valid GPD fit"}
    xi, beta = fit
    lam = n_cl / n_days  # clusters per business day over the pop sample
    boot = _bootstrap(e, u, lam)

    # ---- return levels with bootstrap CIs -----------------------------------
    return_levels = []
    for years in ROGUE_RETURN_YEARS:
        x_t = _return_level(u, lam, years * _BD_PER_YEAR, xi, beta)
        return_levels.append({
            "years": float(years),
            "bp": round(float(x_t), 1),
            "ci95": boot["rl_ci"][years] if boot else None,
        })

    # ---- P(pop >= x within h bd) ---------------------------------------------
    # Above u: Poisson thinning of the cluster rate, P = 1 - exp(-h lam S(x)).
    # Below u the GPD has nothing to say — the empirical per-bd exceedance rate
    # of that severity is used instead (basis marked 'empirical').
    p_exceed = []
    for x in ROGUE_SEVERITIES_BP:
        if x >= u:
            s = _gpd_survival(x, u, xi, beta)
            row = {f"h{h}": round(float(1.0 - np.exp(-h * lam * s)), 5) for h in ROGUE_HORIZONS_BD}
            basis = "gpd"
        else:
            r_emp = float((arr >= x).mean())
            row = {f"h{h}": round(float(1.0 - (1.0 - r_emp) ** h), 5) for h in ROGUE_HORIZONS_BD}
            basis = "empirical"
        p_exceed.append({"x_bp": float(x), **row, "basis": basis})

    # ---- threshold-sensitivity table (point fits only) -----------------------
    sensitivity, sens_xis = [], []
    for pctl in ROGUE_SENS_PCTLS:
        up = float(np.quantile(pos, pctl / 100.0))
        _, mx = _decluster(pop, up)
        fx = _gpd_pwm(mx - up) if mx.size >= 10 else None
        if fx is not None:
            sens_xis.append(fx[0])
        sensitivity.append({
            "pctl": float(pctl),
            "threshold_bp": round(up, 2),
            "n": int(mx.size),
            "xi": round(fx[0], 3) if fx is not None else None,
        })
    unstable = len(sens_xis) >= 2 and (max(sens_xis) - min(sens_xis)) > 0.4

    # ---- historical xi: expanding refits at deterministic positions ----------
    # (is the tail getting heavier as QT drains the basin?)
    xi_dates, xi_vals = [], []
    k = ROGUE_MIN_HISTORY_D
    while k <= n_days:
        xv = _fit_prefix(pop.iloc[:k])
        if xv is not None:
            xi_dates.append(pop.index[k - 1])
            xi_vals.append(float(xv))
        k += 252
    xi_rows = [[d.date().isoformat(), round(v, 3)] for d, v in zip(xi_dates, xi_vals)]
    xi_series = pd.Series(xi_vals, index=pd.DatetimeIndex(xi_dates), dtype=float)

    sample_max = float(np.max(arr))
    if xi > 0.05:
        tail_verdict = (
            f"heavy-tailed basin (xi={xi:.2f}): the largest wave in the sample "
            f"is NOT the largest the basin can make"
        )
    elif xi < -0.05:
        tail_verdict = f"bounded tail: fitted endpoint ~ {u - beta / xi:.0f} bp"
    else:
        tail_verdict = (
            f"exponential-class tail (xi={xi:.2f}): big waves possible, but the "
            f"tail is not scale-free"
        )

    caveats = [
        "declustering keeps cluster MAXIMA, deliberately unlike PROOF's first-day convention: "
        "PROOF wants lead time (the first day of the run), EVT wants magnitudes (the biggest "
        "wave of the episode) — same water, different question",
        f"bootstrap CIs resample the {n_cl} excesses with lambda_u held FIXED — threshold-choice "
        f"and exceedance-rate uncertainty are not propagated, so the intervals are if anything "
        f"too narrow",
        f"a tail fit on n={n_cl} cluster maxima is an extrapolation with stated error bars, not "
        f"a measurement — read the CIs before the points",
        "PWM is consistent only for xi < 0.5: a fit near that edge means 'very heavy', not a "
        "precise number",
        f"expanding statistics only: the current fit uses ALL history up to now; the xi history "
        f"refits at fixed integer positions ({ROGUE_MIN_HISTORY_D} rows, then every 252) so no "
        f"published value changes when future data arrives",
        "no composite score: the tail law is context about the basin, not evidence of stress "
        "today (doctrine)",
    ]
    if unstable:
        caveats.insert(
            0, "tail shape unstable across thresholds — treat return levels as order-of-magnitude"
        )
    if boot is None:
        caveats.append("bootstrap degenerate on too many resamples — CIs withheld (None)")

    method = (
        f"pop = SOFR−IORB − trailing 5bd median (imported from backtest.pop_bp, THE shared event "
        f"statistic; n={n_days}bd). Peaks-over-threshold: u = {ROGUE_THRESHOLD_PCTL:g}th pctl of "
        f"the positive pops = {u:.1f}bp; runs declustering (gap > {ROGUE_DECLUSTER_BD}bd starts a "
        f"new cluster, cluster maximum kept) -> n={n_cl} excesses at lambda_u = {n_cl}/{n_days} "
        f"clusters/bd. GPD(xi, beta) by probability-weighted moments (Hosking–Wallis 1987). "
        f"T-year return level = u + (beta/xi)((252·T·lambda_u)^xi − 1); P(pop ≥ x within h bd) = "
        f"1 − exp(−h·lambda_u·S(x)) above u, empirical per-bd rate below u. CIs: {ROGUE_BOOT_N} "
        f"bootstrap resamples of the excess vector, seed {ROGUE_SEED}, lambda_u fixed. Return "
        f"levels extend P(pop ≥ x) beyond the sample maximum ({sample_max:.1f}bp) with stated "
        f"uncertainty — the number Swell cannot produce."
    )

    return {
        "ok": True,
        "asof": pop.index[-1].date().isoformat(),
        "n_days": n_days,
        "n_clusters": n_cl,
        "threshold_bp": round(u, 2),
        "exceed_rate_per_bd": round(lam, 5),
        "fit": {
            "xi": round(xi, 3),
            "xi_ci95": boot["xi_ci"] if boot else None,
            "beta": round(beta, 2),
            "n_exceed": n_cl,
        },
        "return_levels": return_levels,
        "p_exceed": p_exceed,
        "sensitivity": sensitivity,
        "xi_rows": xi_rows,
        "tail_verdict": tail_verdict,
        "sample_max_bp": round(sample_max, 1),
        "caveats": caveats,
        "method": method,
        "_xi_series": xi_series,
    }
