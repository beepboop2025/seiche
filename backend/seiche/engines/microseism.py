"""Microseism — the shock catalog nobody kept (IDEAS.md #13 made real).

Seismology keeps a catalog of every tremor, not just the earthquakes, because
aftershock statistics — does one shock raise the hazard of the next? — are
the cleanest read on how close the crust is to failure. Funding markets get
the earthquake list (PROOF events) but nobody keeps the tremor catalog. This
engine does: every pop of the SHARED pop statistic (backtest.pop_bp, never
forked) >= MICRO_POP_BP is a micro-shock, and the question is whether shocks
CAUSE shocks (self-excitation) or merely share the calendar's forcing.

The instrument is a Hawkes self-exciting point process with a CALENDAR-GATED
baseline:

    lambda(t) = c * r_bucket(t) + n * beta * SUM_{t_i < t} exp(-beta (t - t_i))

where r_bucket is the expanding empirical shock rate of day t's forcing
bucket (Swell's classify_days — one source of truth for the calendar), c a
fitted scale, n the BRANCHING RATIO (expected direct aftershocks per shock)
and ln2/beta the aftershock half-life. The identification question this
answers is Filimonov–Sornette vs Hardiman–Bouchaud: apparent clustering can
be manufactured by a deterministic forcing schedule, so the null the Hawkes
must beat is an inhomogeneous CALENDAR-Poisson (same buckets, no excitation)
— by likelihood-ratio test in-sample and by walk-forward Brier/AUROC out of
sample. Network-Hawkes work on systemic risk (Zelvyte & Griffin 2026,
arXiv:2606.15755) reads the branching structure's spectral radius as the
system's distance to criticality and finds its own constant-baseline fit is
what breaks (their stated GOF failure: an un-modeled slow regime drift gets
absorbed into fake self-excitation) — the calendar gate here is exactly that
prescription, applied with the one calendar the basin actually has.

Placement among the siblings:
  - Resonance measures the amplitude of the FORCED response at calendar
    events; Microseism asks whether shocks echo BEYOND the forcing.
  - Undertow measures damping on ordinary days; Microseism measures the
    chain reaction between shock days. A basin can be well-damped and still
    near-critical (n -> 1): every shock breeds the next.
  - Rogue Wave owns shock MAGNITUDES (the tail law); Microseism owns shock
    TIMING (the clustering law). Same water, orthogonal questions.
  - Swell prices the calendar's known forcing; Microseism prices what the
    calendar CANNOT explain.

Honesty notes:
  - branching history refits at deterministic integer positions
    (MICRO_MIN_HISTORY_D rows, then every MICRO_REFIT_EVERY_BD) with bucket
    rates computed on the prefix only — truncation equality holds exactly
    (unit-tested house invariant);
  - the walk-forward hazard freezes parameters at each refit and must beat
    CALENDAR climatology (the honest comparator, not flat climatology) or
    the verdict says so and the gauge self-demotes to diagnostic;
  - the LR test's chi2(2) reference is conservative at the n=0 boundary
    (stated); no RNG anywhere — the board is deterministic;
  - NO composite score: near-criticality is context about the basin's state,
    not evidence of stress today (doctrine).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import chi2

from seiche.config import (
    MICRO_HAZARD_FWD_BD,
    MICRO_MIN_EVENTS,
    MICRO_MIN_HISTORY_D,
    MICRO_POP_BP,
    MICRO_REFIT_EVERY_BD,
    MICRO_SENS_BP,
    MICRO_SHRINK_K,
)
from seiche.engines.backtest import pop_bp
from seiche.engines.swell import classify_days

# multi-start grid for the 3-parameter MLE — fixed, deterministic
_STARTS = [(0.0, -1.5, -1.5), (0.0, 0.0, -0.5), (0.0, -3.0, 0.0), (0.0, 1.0, -2.5)]
_BOUNDS = [(-3.0, 3.0), (-8.0, 5.0), (-4.5, 2.0)]  # log c, logit-ish n, log beta
_N_CAP = 0.995  # branching stays inside the stationary region


def _shock_times(pop: pd.Series, thr: float) -> np.ndarray:
    """Micro-shock catalog: integer positions (business-day time axis) where
    the shared pop statistic >= thr. Deliberately NOT declustered — the
    clustering is the signal a Hawkes model measures, not noise to remove."""
    return np.flatnonzero(pop.to_numpy(dtype=float) >= thr)


def _bucket_rates(buckets: np.ndarray, hit: np.ndarray, upto: int) -> dict[str, float]:
    """Expanding per-bucket shock rates on the prefix [0, upto), shrunk to the
    pooled prefix rate with MICRO_SHRINK_K pseudo-days (thin buckets borrow
    strength instead of printing 0 or 1)."""
    b, h = buckets[:upto], hit[:upto]
    pooled = float(h.mean()) if upto > 0 else 0.0
    rates: dict[str, float] = {}
    for name in np.unique(b):
        m = b == name
        n_b, h_b = float(m.sum()), float(h[m].sum())
        rates[str(name)] = (h_b + MICRO_SHRINK_K * pooled) / (n_b + MICRO_SHRINK_K)
    rates["__pooled__"] = pooled
    return rates


def _rate_array(buckets: np.ndarray, rates: dict[str, float]) -> np.ndarray:
    pooled = rates.get("__pooled__", 0.0)
    return np.array([rates.get(str(b), pooled) for b in buckets], dtype=float)


def _unpack(theta: np.ndarray) -> tuple[float, float, float]:
    c = math.exp(float(theta[0]))
    n = _N_CAP / (1.0 + math.exp(-float(theta[1])))  # logistic onto (0, N_CAP)
    beta = math.exp(float(theta[2]))
    return c, n, beta


def _neg_loglik(theta: np.ndarray, times: np.ndarray, r: np.ndarray, T: int) -> float:
    """Exact continuous-time Hawkes log-likelihood on [0, T] with piecewise-
    constant (daily) baseline. O(N) via the exponential-kernel recursion
    S_i = e^{-beta dt} (S_{i-1} + 1)."""
    c, n, beta = _unpack(theta)
    ll = 0.0
    s = 0.0
    prev = None
    for t in times:
        if prev is not None:
            s = math.exp(-beta * (t - prev)) * (s + 1.0)
        lam = c * r[t] + n * beta * s
        if lam <= 0.0:
            return 1e12
        ll += math.log(lam)
        prev = t
    integral = c * float(r[:T].sum()) + n * float(
        (1.0 - np.exp(-beta * (T - times.astype(float)))).sum()
    )
    return integral - ll


def _fit(times: np.ndarray, r: np.ndarray, T: int) -> dict | None:
    """Multi-start L-BFGS-B MLE of (c, n, beta). Deterministic: fixed start
    grid, best likelihood wins."""
    if times.size < 5:
        return None
    best = None
    for start in _STARTS:
        res = minimize(
            _neg_loglik, np.array(start, dtype=float),
            args=(times, r, T), method="L-BFGS-B", bounds=_BOUNDS,
        )
        if best is None or res.fun < best.fun:
            best = res
    if best is None or not np.isfinite(best.fun):
        return None
    c, n, beta = _unpack(best.x)
    return {"c": c, "n": n, "beta": beta, "loglik": -float(best.fun)}


def _null_fit(times: np.ndarray, r: np.ndarray, T: int) -> dict:
    """Calendar-Poisson null (n = 0): the scale MLE is closed-form,
    c0 = N / integral(r)."""
    denom = float(r[:T].sum())
    c0 = times.size / denom if denom > 0 else 0.0
    lam = c0 * r[times]
    ll = float(np.log(np.maximum(lam, 1e-300)).sum()) - c0 * denom
    return {"c": c0, "loglik": ll}


def _excitation_state(times: np.ndarray, beta: float, t: int) -> float:
    """S(t) = sum over shocks at or before t of exp(-beta (t - t_i))."""
    past = times[times <= t].astype(float)
    if past.size == 0:
        return 0.0
    return float(np.exp(-beta * (t - past)).sum())


def _auroc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    n_pos = int(labels.sum())
    n_neg = int((~labels).sum())
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = pd.Series(scores).rank(method="average").to_numpy()
    return float((ranks[labels].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def analyze(spread_bp: pd.Series) -> dict:
    """Calendar-gated Hawkes on the micro-shock catalog. Input: SOFR−IORB
    spread in bp, daily DatetimeIndex. Publishes the branching ratio (distance
    to criticality), aftershock half-life, the LR identification test vs the
    calendar null, and a walk-forward hazard scored against calendar
    climatology. No composite score — context, not evidence."""
    pop = pop_bp(spread_bp).dropna()
    n_days = int(len(pop))
    if n_days < MICRO_MIN_HISTORY_D:
        return {"ok": False, "reason": f"insufficient pop history ({n_days}d < {MICRO_MIN_HISTORY_D}d)"}

    buckets = classify_days(pop.index).to_numpy()
    times = _shock_times(pop, MICRO_POP_BP)
    if times.size < MICRO_MIN_EVENTS:
        return {
            "ok": False,
            "reason": f"only {times.size} micro-shocks >= {MICRO_POP_BP}bp — need >= {MICRO_MIN_EVENTS}",
        }
    hit = np.zeros(n_days, dtype=bool)
    hit[times] = True

    # ---- current fit: the expanding fit at T = now -------------------------
    rates_full = _bucket_rates(buckets, hit, n_days)
    r_full = _rate_array(buckets, rates_full)
    fit = _fit(times, r_full, n_days)
    if fit is None:
        return {"ok": False, "reason": "MLE failed to converge on the full catalog"}
    null = _null_fit(times, r_full, n_days)
    lr_stat = max(0.0, 2.0 * (fit["loglik"] - null["loglik"]))
    lr_p = float(chi2.sf(lr_stat, df=2))

    half_life = math.log(2.0) / fit["beta"]
    s_now = _excitation_state(times, fit["beta"], n_days - 1)
    lam_now = fit["c"] * r_full[n_days - 1] + fit["n"] * fit["beta"] * s_now
    excitation_share = (fit["n"] * fit["beta"] * s_now) / lam_now if lam_now > 0 else 0.0

    # ---- branching history + walk-forward hazard ---------------------------
    # Refits at deterministic integer positions; bucket rates on the prefix
    # only. Between refits, parameters are FROZEN and the hazard uses only
    # shocks known at each scored day — no value changes when future arrives.
    refit_ks = list(range(MICRO_MIN_HISTORY_D, n_days, MICRO_REFIT_EVERY_BD))
    branching_rows: list[list] = []
    fwd = MICRO_HAZARD_FWD_BD
    p_h: list[float] = []
    p_c: list[float] = []
    labels: list[bool] = []

    for j, k in enumerate(refit_ks):
        rates_k = _bucket_rates(buckets, hit, k)
        r_k = _rate_array(buckets, rates_k)
        t_k = times[times < k]
        fit_k = _fit(t_k, r_k, k) if t_k.size >= MICRO_MIN_EVENTS else None
        null_k = _null_fit(t_k, r_k, k) if t_k.size >= 5 else None
        if fit_k is not None:
            branching_rows.append([pop.index[k - 1].date().isoformat(), round(fit_k["n"], 3)])
        seg_end = refit_ks[j + 1] if j + 1 < len(refit_ks) else n_days
        if fit_k is None or null_k is None:
            continue
        ck, nk, bk = fit_k["c"], fit_k["n"], fit_k["beta"]
        c0k = null_k["c"]
        decay_fwd = 1.0 - math.exp(-bk * fwd)
        s_t = _excitation_state(times, bk, k - 1)  # shocks known at segment start
        for t in range(k, min(seg_end, n_days - fwd)):
            s_t = s_t * math.exp(-bk) + (1.0 if hit[t] else 0.0)
            base_fwd = float(r_k[t + 1 : t + 1 + fwd].sum())
            lam_fwd = ck * base_fwd + nk * s_t * decay_fwd
            p_h.append(1.0 - math.exp(-lam_fwd))
            p_c.append(1.0 - math.exp(-c0k * base_fwd))
            labels.append(bool(hit[t + 1 : t + 1 + fwd].any()))

    walkforward: dict = {"ok": False, "reason": "insufficient scored history"}
    if len(labels) >= 200:
        y = np.array(labels, dtype=bool)
        ph = np.array(p_h, dtype=float)
        pc = np.array(p_c, dtype=float)
        brier_h = float(np.mean((ph - y) ** 2))
        brier_c = float(np.mean((pc - y) ** 2))
        auroc_h = _auroc(ph, y)
        auroc_c = _auroc(pc, y)
        beats = brier_h < brier_c and (auroc_h or 0.0) > (auroc_c or 0.0)
        walkforward = {
            "ok": True,
            "n_scored": int(len(y)),
            "base_rate": round(float(y.mean()), 3),
            "brier_hawkes": round(brier_h, 4),
            "brier_calendar": round(brier_c, 4),
            "auroc_hawkes": round(auroc_h, 3) if auroc_h is not None else None,
            "auroc_calendar": round(auroc_c, 3) if auroc_c is not None else None,
            "beats_calendar": bool(beats),
            "verdict": (
                "self-excitation adds forecast skill over calendar climatology"
                if beats else
                "does NOT beat calendar climatology out of sample — read the "
                "branching gauge as diagnostic, not predictive"
            ),
        }

    # ---- threshold sensitivity ---------------------------------------------
    sensitivity = []
    for thr in (MICRO_SENS_BP[0], MICRO_POP_BP, MICRO_SENS_BP[1]):
        tt = _shock_times(pop, thr)
        row: dict = {"thr_bp": float(thr), "n_shocks": int(tt.size)}
        if tt.size >= MICRO_MIN_EVENTS:
            hh = np.zeros(n_days, dtype=bool)
            hh[tt] = True
            rr = _rate_array(buckets, _bucket_rates(buckets, hh, n_days))
            fx = _fit(tt, rr, n_days)
            if fx is not None:
                nx = _null_fit(tt, rr, n_days)
                lrx = max(0.0, 2.0 * (fx["loglik"] - nx["loglik"]))
                row.update({
                    "branching": round(fx["n"], 3),
                    "half_life_bd": round(math.log(2.0) / fx["beta"], 1),
                    "lr_p": round(float(chi2.sf(lrx, df=2)), 4),
                })
        sensitivity.append(row)
    sens_ns = [r["branching"] for r in sensitivity if r.get("branching") is not None]
    unstable = len(sens_ns) >= 2 and (max(sens_ns) - min(sens_ns)) > 0.25

    identified = lr_p < 0.05
    if not identified:
        reading = (
            f"clustering is NOT distinguishable from calendar forcing alone "
            f"(LR p={lr_p:.3f}) — the calendar explains the tremors"
        )
    elif fit["n"] >= 0.7:
        reading = (
            f"NEAR-CRITICAL: each shock breeds ~{fit['n']:.2f} aftershocks — at "
            f"n=1 the chain reaction is self-sustaining; most tremors are now "
            f"echoes of tremors, not fresh forcing"
        )
    elif fit["n"] >= 0.4:
        reading = (
            f"genuine self-excitation (n={fit['n']:.2f}, LR p={lr_p:.4f}): shocks "
            f"echo beyond the calendar, half-life {half_life:.1f}bd"
        )
    else:
        reading = (
            f"mild self-excitation (n={fit['n']:.2f}): mostly calendar-forced, "
            f"with a real but small echo"
        )

    caveats = [
        "the catalog is deliberately NOT declustered — clustering is the signal a Hawkes "
        "measures, not noise to remove (PROOF declusters because it counts independent events; "
        "same water, different question)",
        "the LR test's chi2(2) reference is conservative at the n=0 boundary — a significant "
        "p is trustworthy, a marginal one overstates the evidence",
        "constant-baseline Hawkes fits absorb slow regime drift into fake self-excitation "
        "(the stated GOF failure of arXiv:2606.15755) — the calendar-gated baseline is the "
        "fix, but drift the calendar cannot see (e.g. a multi-year reserve drain) can still "
        "leak into n; read the branching HISTORY, not one number",
        "branching history refits at fixed integer positions with prefix-only bucket rates — "
        "no published value changes when future data arrives",
        "walk-forward hazard must beat CALENDAR climatology (the honest comparator); flat "
        "climatology is a strawman when the forcing schedule is public",
        "no composite score: near-criticality is context about the basin, not evidence of "
        "stress today (doctrine)",
    ]
    if unstable:
        caveats.insert(0, "branching ratio unstable across catalog thresholds — treat the "
                          "level as order-of-magnitude, trust the trend")

    method = (
        f"micro-shock catalog: pop = SOFR−IORB − trailing 5bd median (backtest.pop_bp, THE "
        f"shared statistic) >= {MICRO_POP_BP}bp, not declustered (n={times.size} shocks / "
        f"{n_days}bd). Hawkes lambda(t) = c*r_bucket(t) + n*beta*sum exp(-beta dt) with "
        f"calendar buckets from Swell's classify_days and expanding shrunk bucket rates "
        f"(K={MICRO_SHRINK_K:g}); MLE by multi-start L-BFGS-B (deterministic, no RNG). "
        f"Identification: LR test vs the calendar-Poisson null (chi2 df=2, conservative at "
        f"the boundary). Branching history: prefix refits at {MICRO_MIN_HISTORY_D} rows then "
        f"every {MICRO_REFIT_EVERY_BD}bd. Walk-forward: parameters frozen per segment, "
        f"P(shock within {fwd}bd) vs the null's calendar climatology, Brier + AUROC printed."
    )

    return {
        "ok": True,
        "asof": pop.index[-1].date().isoformat(),
        "n_days": n_days,
        "n_shocks": int(times.size),
        "threshold_bp": float(MICRO_POP_BP),
        "fit": {
            "branching": round(fit["n"], 3),
            "half_life_bd": round(half_life, 1),
            "baseline_scale_c": round(fit["c"], 3),
            "excitation_share_now": round(excitation_share, 3),
        },
        "lr_test": {
            "stat": round(lr_stat, 1),
            "p": round(lr_p, 5),
            "identified": bool(identified),
            "null": "inhomogeneous calendar-Poisson (same buckets, no excitation)",
        },
        "branching_rows": branching_rows,
        "walkforward": walkforward,
        "sensitivity": sensitivity,
        "reading": reading,
        "caveats": caveats,
        "method": method,
        "_branching_series": pd.Series(
            [v for _, v in branching_rows],
            index=pd.DatetimeIndex([d for d, _ in branching_rows]),
            dtype=float,
        ),
    }
