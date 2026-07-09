"""OU + jump-diffusion — the index as a mean-reverting process with jumps.

Fits a discrete Ornstein-Uhlenbeck process to the reconstructed index
(dX = k(theta - X)dt + sigma dW, business-day steps), separates the fat
residuals into a compound-Poisson jump term, and reports the ANALYTIC marginal
distribution at each horizon: P(above the STRESS line at +Nd), split into the
diffusion tail and the jump tail.

This is the endpoint marginal, deliberately different from the Monte Carlo
engine (which gives path-max) and from Bathymetry (which is on the spread, not
the index). ``fit_params`` is shared with the Monte Carlo engine so both speak
the same fitted process.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from seiche.config import REGIMES

_STRESS_FLOOR = REGIMES[-2][0]      # index >= this => STRESS  (70)
_JUMP_Z = 3.5                        # residual sd multiple that counts as a jump


def fit_params(x: np.ndarray) -> dict:
    """OU + jump parameters from a level series (business-day steps, dt=1)."""
    x = np.asarray(x, dtype=float)
    dx = np.diff(x)
    x0 = x[:-1]
    # dx = a + b*x0 + eps ;  b = -k*dt, a = k*theta*dt, dt=1
    b, a = np.polyfit(x0, dx, 1)
    k = float(-b)
    theta = float(a / k) if abs(k) > 1e-9 else float(np.mean(x))
    resid = dx - (a + b * x0)
    sd = float(np.std(resid))
    jump = np.abs(resid) > (_JUMP_Z * sd) if sd > 0 else np.zeros_like(resid, dtype=bool)
    lam = float(jump.mean()) if resid.size else 0.0
    jmean = float(np.mean(resid[jump])) if jump.any() else 0.0
    jstd = float(np.std(resid[jump])) if jump.sum() > 1 else 0.0
    cont = resid[~jump] if (~jump).any() else resid
    sigma = float(np.std(cont, ddof=1)) if cont.size > 1 else sd
    half_life = float(math.log(2) / k) if k > 1e-6 else None
    return {"k": k, "theta": theta, "sigma": sigma, "lam": lam,
            "jmean": jmean, "jstd": jstd, "half_life": half_life}


def _ou_moments(x0: float, p: dict, h: int) -> tuple[float, float]:
    """Mean and variance of the diffusion part at horizon h."""
    k, theta, sigma = p["k"], p["theta"], p["sigma"]
    if k > 1e-6:
        e = math.exp(-k * h)
        mean = theta + (x0 - theta) * e
        var = (sigma ** 2) * (1 - math.exp(-2 * k * h)) / (2 * k)
    else:                                # no mean reversion -> random walk
        mean = x0
        var = (sigma ** 2) * h
    return mean, max(var, 1e-9)


def _exceed(level: float, mean: float, var: float) -> float:
    return 0.5 * math.erfc((level - mean) / math.sqrt(2 * var))


def analyze(index: pd.Series, horizons: tuple[int, ...] = (5, 10, 21),
            current_value: float | None = None) -> dict:
    x = index.dropna().to_numpy(dtype=float)
    if len(x) < 120:
        return {"ok": False, "reason": f"insufficient history ({len(x)}d)"}

    p = fit_params(x)
    # start from the LIVE board level when given, so the reading agrees with the
    # published board; the dynamics are still fit on the reconstructed history.
    x0 = float(current_value) if current_value is not None and np.isfinite(current_value) else float(x[-1])
    out_h = []
    for h in horizons:
        mean_d, var_d = _ou_moments(x0, p, h)
        p_diff = _exceed(_STRESS_FLOOR, mean_d, var_d)
        # add the compound-Poisson jump term over h days
        mean_j = mean_d + p["lam"] * h * p["jmean"]
        var_j = var_d + p["lam"] * h * (p["jstd"] ** 2 + p["jmean"] ** 2)
        p_tot = _exceed(_STRESS_FLOOR, mean_j, var_j)
        share = round(float((p_tot - p_diff) / p_tot), 3) if p_tot > 1e-6 else 0.0
        out_h.append({
            "h": h,
            "p_above_stress": round(p_tot, 4),
            "p_diffusion_only": round(p_diff, 4),
            "jump_share_of_tail": share,
        })

    return {
        "ok": True,
        "level_now": round(x0, 1),
        "stress_line": _STRESS_FLOOR,
        "fit": {
            "k_per_bd": round(p["k"], 4),
            "half_life_bd": round(p["half_life"], 1) if p["half_life"] else None,
            "theta": round(p["theta"], 1),
            "sigma": round(p["sigma"], 3),
            "jump_intensity_per_bd": round(p["lam"], 4),
            "jump_mean": round(p["jmean"], 2),
        },
        "horizons": out_h,
        "reading": (
            "mean-reverting fit with a jump term. p_above_stress is the analytic "
            "probability the index sits above the STRESS line at +Nd (endpoint, "
            "not path-max), split into the diffusion tail and the jump tail. "
            "half_life is how fast shocks decay; a low k means the level drifts "
            "rather than snaps back."
        ),
    }
