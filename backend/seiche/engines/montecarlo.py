"""Monte Carlo path fan — thousands of simulated forward paths from today.

Takes the OU + jump process fit to the reconstructed index (shared with the
oujump engine) and simulates N paths H business days forward, then reports the
fan (per-horizon percentiles) and the PATH-MAX / PATH-MIN probabilities:
P(touch STRESS within Nd) and P(fall back to CALM within Nd).

Path-max is the honest question a risk desk asks ("do we touch the line at any
point", not "where do we end up"), which is exactly what oujump's analytic
endpoint marginal cannot give. The RNG is seeded from a FIXED constant so the
published reading is deterministic and notarisable — the same input always
produces the same fan, never random noise in the record.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import REGIMES
from seiche.engines.oujump import fit_params

_STRESS_FLOOR = REGIMES[-2][0]      # index >= this => STRESS  (70)
_CALM_CEIL = REGIMES[0][0]          # index <  this => CALM     (25)
_SEED = 20260710                    # fixed: reproducible paths, notarisable output


def analyze(index: pd.Series, horizons: tuple[int, ...] = (5, 10, 21),
            n_paths: int = 5000, current_value: float | None = None) -> dict:
    x = index.dropna().to_numpy(dtype=float)
    if len(x) < 120:
        return {"ok": False, "reason": f"insufficient history ({len(x)}d)"}

    p = fit_params(x)
    # start the paths from the LIVE board level when given, so the fan agrees
    # with the published board; the fitted dynamics come from the history.
    x0 = float(current_value) if current_value is not None and np.isfinite(current_value) else float(x[-1])
    hmax = max(horizons)
    rng = np.random.default_rng(_SEED)

    paths = np.empty((n_paths, hmax + 1))
    paths[:, 0] = x0
    jstd = max(p["jstd"], 1e-6)
    for t in range(1, hmax + 1):
        prev = paths[:, t - 1]
        drift = p["k"] * (p["theta"] - prev)
        diffusion = p["sigma"] * rng.standard_normal(n_paths)
        jumped = rng.random(n_paths) < p["lam"]
        jmag = np.where(jumped, rng.normal(p["jmean"], jstd, n_paths), 0.0)
        paths[:, t] = np.clip(prev + drift + diffusion + jmag, 0.0, 100.0)

    def _pct(col: np.ndarray, q: float) -> float:
        return round(float(np.percentile(col, q)), 1)

    fan = [{
        "h": h,
        "p10": _pct(paths[:, h], 10), "p25": _pct(paths[:, h], 25),
        "median": _pct(paths[:, h], 50),
        "p75": _pct(paths[:, h], 75), "p90": _pct(paths[:, h], 90),
    } for h in horizons]

    reach_stress, back_calm = {}, {}
    for h in horizons:
        seg = paths[:, 1:h + 1]
        reach_stress[f"h{h}"] = round(float((seg.max(axis=1) >= _STRESS_FLOOR).mean()), 4)
        back_calm[f"h{h}"] = round(float((seg.min(axis=1) <= _CALM_CEIL).mean()), 4)

    return {
        "ok": True,
        "level_now": round(x0, 1),
        "n_paths": n_paths,
        "stress_line": _STRESS_FLOOR,
        "calm_line": _CALM_CEIL,
        "fan": fan,
        "p_touch_stress": reach_stress,   # path-max crosses the STRESS line
        "p_back_to_calm": back_calm,       # path-min falls to CALM
        "reading": (
            "5,000 simulated paths from today under the fitted OU+jump process. "
            "the fan is the spread of where the index could be at +Nd; "
            "p_touch_stress is the chance a path crosses the STRESS line at ANY "
            "point within Nd (path-max, higher than the endpoint odds). Seeded "
            "fixed, so the same board always simulates the same fan."
        ),
    }
