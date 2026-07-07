"""Fleet of Forecasts — four independent views, one blend, and the
disagreement meter.

The terminal now emits four genuinely different P(funding event within 5bd)
views, each with its own published walk-forward record:

  rule     the Seiche-lite percentile, mapped to a probability through its
           own expanding bucket event-rates (computed here, same honesty
           rules: predict first, update after);
  ml       ML Lab's walk-forward gradient-boosted probability;
  analogs  Tide Tables' k-nearest-neighbor event odds;
  swell    the Swell forward curve integrated over the next 5bd.

Two outputs, both of which matter more than any single view:

  BLEND — skill-weighted average, where skill = each view's own published
  Brier improvement over climatology (a view that can't beat the base rate
  gets weight ZERO — it already self-demoted; averaging it back in would
  smuggle it past its own verdict). All views skill-less -> the blend IS
  climatology and says so.

  DISAGREEMENT — max−min across live views. Ensemble dispersion spikes when
  the regime is genuinely ambiguous, which is exactly when an operator
  should distrust point estimates. Disagreement is published as a signal,
  never averaged away silently.

Reported alongside the index, not weighted into it (same rule as every
forecast layer: a forecast is not evidence of stress).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    BACKTEST_EVENT_FWD_D,
    BACKTEST_SPIKE_BP,
    FLEET_DISAGREE_WARN,
    FLEET_MIN_SKILL,
    SWELL_WARMUP_D,
)
from seiche.engines.backtest import PCTL_BUCKETS, pop_bp

# same edges PROOF publishes its outcome tables under — shared, not re-declared
_BUCKET_EDGES = [hi for _, hi, _ in PCTL_BUCKETS[:-1]]   # [60, 80, 90]


def _labels_5bd(spread_bp: pd.Series, grid: pd.DatetimeIndex) -> np.ndarray:
    """y[i] = 1 if a pop >= BACKTEST_SPIKE_BP lands within the next 5bd (the
    shared event definition via backtest.pop_bp). NaN where the forward
    window is open (runs past the sample) or entirely unobserved."""
    pop = pop_bp(spread_bp, grid)
    fwd = BACKTEST_EVENT_FWD_D
    # max over the next `fwd` pops = reversed trailing rolling max, shifted
    fwd_max = pop[::-1].rolling(fwd, min_periods=1).max()[::-1].shift(-1)
    y = (fwd_max >= BACKTEST_SPIKE_BP).astype(float).to_numpy().copy()
    y[fwd_max.isna().to_numpy()] = np.nan
    n = len(grid)
    if fwd > 0:
        y[max(n - fwd, 0):] = np.nan   # open windows: no verdict yet
    return y


def rule_probability(lite_pctl: pd.Series, spread_bp: pd.Series) -> dict:
    """Map the rule index percentile to P(event within 5bd) through expanding
    per-bucket event rates. Honesty detail that costs real skill points: the
    label for day j is only OBSERVABLE at close of day j+5, so the tables at
    prediction time i contain labels through day i−5 only (the same boundary
    embargo the ML Lab applies). Updating with y[i−1..i−4] would leak pops on
    days i..i+4 into the very forecast they score."""
    pct = lite_pctl.dropna()
    if len(pct) < SWELL_WARMUP_D:
        return {"ok": False, "reason": "insufficient rule history"}
    grid = pd.DatetimeIndex(pct.index)
    y = _labels_5bd(spread_bp, grid)
    v = pct.to_numpy(dtype=float)
    bidx = np.digitize(v, _BUCKET_EDGES)   # 0..3, ties match lo <= v < hi

    fwd = BACKTEST_EVENT_FWD_D
    hits = np.zeros(len(PCTL_BUCKETS))
    ns = np.zeros(len(PCTL_BUCKETS))
    all_hits = all_n = 0.0
    ps, ys, scored_pos = [], [], []
    p_now = None
    for i in range(len(grid)):
        j = i - fwd   # newest label whose window has closed by day i
        if j >= 0 and not np.isnan(y[j]):
            b = bidx[j]
            hits[b] += y[j]
            ns[b] += 1
            all_hits += y[j]
            all_n += 1
        if i >= SWELL_WARMUP_D and all_n > 0:
            b = bidx[i]
            p = (hits[b] + 0.5) / (ns[b] + 1.0) if ns[b] >= 30 else (all_hits + 0.5) / (all_n + 1.0)
            p_now = p
            if not np.isnan(y[i]):
                ps.append(p)
                ys.append(y[i])
                scored_pos.append(i)
    if p_now is None or len(ps) < 100:
        return {"ok": False, "reason": "not enough scored rule history"}
    pa, ya = np.array(ps), np.array(ys)
    base = float(ya.mean())
    brier = float(np.mean((pa - ya) ** 2))
    brier_clim = float(np.mean((base - ya) ** 2))
    return {
        "ok": True,
        "p": round(float(p_now), 3),
        "brier": round(brier, 4),
        "brier_climatology": round(brier_clim, 4),
        "n_scored": len(ps),
        "base_rate_5bd": round(base, 3),
        "embargo_bd": fwd,
        # walk-forward p per scored day — no-look-ahead test hook (never
        # serialized: rule_probability's dict stays internal to analyze())
        "_p_series": pd.Series(pa, index=grid[scored_pos]),
    }


def _skill(brier: float | None, brier_clim: float | None) -> float | None:
    if brier is None or brier_clim is None or brier_clim <= 0:
        return None
    return round(1.0 - brier / brier_clim, 3)


def analyze(
    lite_pctl: pd.Series,
    spread_bp: pd.Series,
    ml: dict | None,
    tide: dict | None,
    swell: dict | None,
) -> dict:
    views: list[dict] = []

    rule = rule_probability(lite_pctl, spread_bp)
    if rule.get("ok"):
        views.append({
            "name": "rule",
            "label": "Seiche-lite percentile (bucket-mapped)",
            "p": rule["p"],
            "skill": _skill(rule["brier"], rule["brier_climatology"]),
            "brier": rule["brier"],
        })

    if ml and ml.get("ok"):
        v = ml.get("validation", {})
        views.append({
            "name": "ml",
            "label": "ML Lab (gradient boosting, walk-forward)",
            "p": ml.get("p_event_5bd"),
            "skill": _skill(v.get("brier"), v.get("brier_climatology")),
            "brier": v.get("brier"),
        })

    if tide and tide.get("ok"):
        sk = tide.get("skill", {})
        views.append({
            "name": "analogs",
            "label": "Tide Tables (nearest analogs)",
            "p": (tide.get("event_odds") or {}).get("p"),
            "skill": _skill(sk.get("brier"), sk.get("brier_climatology")) if sk.get("ok") else None,
            "brier": sk.get("brier") if sk.get("ok") else None,
        })

    if swell and swell.get("ok"):
        v = swell.get("validation", {})
        views.append({
            "name": "swell",
            "label": "Swell curve (calendar hazard, 5bd integral)",
            "p": swell.get("p_event_5bd"),
            "skill": _skill(v.get("brier"), v.get("brier_climatology")) if v.get("ok") else None,
            "brier": v.get("brier") if v.get("ok") else None,
        })

    live = [v for v in views if v.get("p") is not None]
    if len(live) < 2:
        return {"ok": False, "reason": f"only {len(live)} live forecast view(s) — fleet needs 2+"}

    clim = rule.get("base_rate_5bd") if rule.get("ok") else None

    # Skill-proportional weights; a view that never beat its own climatology
    # carries zero weight (it self-demoted — honor that verdict). The deadband
    # keeps a view oscillating around zero skill from flipping the blend's
    # identity day to day on sampling noise.
    for v in live:
        s = v.get("skill")
        v["weight"] = round(float(s), 3) if s is not None and s >= FLEET_MIN_SKILL else 0.0
    wsum = sum(v["weight"] for v in live)
    if wsum > 0:
        blend = sum(v["p"] * v["weight"] for v in live) / wsum
        for v in live:
            v["weight"] = round(v["weight"] / wsum, 3)
        blend_src = "skill-weighted blend"
    else:
        blend = clim
        blend_src = "climatology (no view beats its own base rate out-of-sample)"

    p_vals = [v["p"] for v in live]
    disagreement = round(float(max(p_vals) - min(p_vals)), 3)
    spread_note = (
        "the fleet DISAGREES — regime ambiguity is itself information; trust ranges, not points"
        if disagreement >= FLEET_DISAGREE_WARN
        else "the fleet broadly agrees"
    )

    return {
        "ok": True,
        "views": views,
        "blend_p_5bd": round(float(blend), 3) if blend is not None else None,
        "blend_source": blend_src,
        "climatology_p_5bd": clim,
        "disagreement": disagreement,
        "disagree_warn": FLEET_DISAGREE_WARN,
        "verdict": spread_note,
        "caveats": [
            "each view's Brier is its own published walk-forward score on its own scored sample — comparable in spirit, not on identical days",
            "views share input series (the plumbing) — disagreement understates true independence, agreement overstates it",
            "the blend inherits every component's final-vintage caveat",
        ],
        "method": (
            "views: rule (expanding bucket event-rates over lite pctl, labels embargoed "
            f"{BACKTEST_EVENT_FWD_D}bd — a forward label enters the tables only once its window "
            "closes), ML Lab, Tide Tables analogs, Swell 5bd integral — all targeting P(pop ≥ "
            f"{BACKTEST_SPIKE_BP:g}bp within {BACKTEST_EVENT_FWD_D}bd). Blend weights ∝ Brier "
            f"skill from each view's own walk-forward record, zeroed below {FLEET_MIN_SKILL} "
            "(deadband against identity churn); all-zero skills → climatology published. "
            "Disagreement = max − min across live views"
        ),
    }
