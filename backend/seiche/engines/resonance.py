"""Resonance Engine — the seiche made literal.

A seiche is a standing wave in an enclosed basin: its amplitude is set not by
the forcing but by the basin's damping. Funding markets are forced by the
same calendar every month — month-end window dressing, quarter-end
balance-sheet snapshots, mid-month coupon settlements, corporate tax dates,
year-end. When the SAME forcing starts producing a BIGGER slosh, the basin is
losing damping: intermediation capacity is thinning even while levels between
events look calm. That amplification trend — not the level — is the signal.

Per calendar mode we measure, for every historical event:
  slosh      = max(SOFR−IORB, event ±1bd) − median(spread over the 10bd
               baseline window ending 3bd before)          [bp]
  half-life  = business days until the spread falls back below
               baseline + slosh/2 (censored at RESONANCE_DECAY_D)

and report per mode: amplification (median slosh, last N events vs prior N),
decay trend, and a 0–100 mode score. The composite resonance score weights
quarter-end heaviest (the systemic snapshot date).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    CORPORATE_TAX_DAYS,
    RESONANCE_AMP_SATURATION,
    RESONANCE_DECAY_D,
    RESONANCE_MIN_EVENTS,
    RESONANCE_MODES,
    RESONANCE_PRE_BASELINE_D,
    RESONANCE_RECENT_N,
    RESONANCE_WINDOW_D,
)

MODE_WEIGHTS = {
    "quarter_end": 0.30,
    "month_end": 0.25,
    "year_end": 0.15,
    "mid_month": 0.15,
    "tax_date": 0.15,
}

MODE_LABELS = {
    "quarter_end": "Quarter-end (dealer balance-sheet snapshot)",
    "month_end": "Month-end (window dressing)",
    "year_end": "Year-end (G-SIB surcharge snapshot)",
    "mid_month": "Mid-month (coupon settlement pile)",
    "tax_date": "Corporate tax dates",
}


def _classify_events(index: pd.DatetimeIndex) -> dict[str, list[pd.Timestamp]]:
    """Enumerate disjoint calendar-forcing events inside the sample.

    Modes are kept disjoint so amplification trends aren't double-counted:
    year-end beats quarter-end beats month-end; tax dates beat mid-month.
    """
    if len(index) == 0:
        return {m: [] for m in RESONANCE_MODES}
    bdays = pd.bdate_range(index.min(), index.max())
    by_month: dict[tuple[int, int], list[pd.Timestamp]] = {}
    for d in bdays:
        by_month.setdefault((d.year, d.month), []).append(d)

    events: dict[str, list[pd.Timestamp]] = {m: [] for m in RESONANCE_MODES}
    for (y, m), days in sorted(by_month.items()):
        last_bd = days[-1]
        if m == 12:
            events["year_end"].append(last_bd)
        elif m in (3, 6, 9):
            events["quarter_end"].append(last_bd)
        else:
            events["month_end"].append(last_bd)
        # 15th (or next business day) — tax bucket in tax months, otherwise
        # the mid-month coupon-settlement mode.
        mid = next((d for d in days if d.day >= 15), None)
        if mid is not None:
            if (m, 15) in CORPORATE_TAX_DAYS:
                events["tax_date"].append(mid)
            else:
                events["mid_month"].append(mid)
    return events


def _event_response(spread: pd.Series, event: pd.Timestamp) -> dict | None:
    """Slosh amplitude and decay half-life for one event. None = no coverage."""
    idx = spread.index
    loc = idx.searchsorted(event)
    if loc >= len(idx) or loc < RESONANCE_PRE_BASELINE_D + 5:
        return None
    # nearest observed day at/after the calendar event
    ev_loc = min(loc, len(idx) - 1)

    base_end = ev_loc - 3
    base = spread.iloc[base_end - RESONANCE_PRE_BASELINE_D : base_end]
    if base.dropna().empty:
        return None
    baseline = float(base.median())

    w0 = max(ev_loc - RESONANCE_WINDOW_D, 0)
    w1 = min(ev_loc + RESONANCE_WINDOW_D, len(idx) - 1)
    window = spread.iloc[w0 : w1 + 1].dropna()
    if window.empty:
        return None
    slosh = float(window.max() - baseline)

    # Decay: first business day after the peak when the spread has given back
    # half the slosh. Censored (reported as the max) if it never does.
    half_level = baseline + slosh / 2.0
    peak_loc = w0 + int(np.argmax(spread.iloc[w0 : w1 + 1].fillna(-np.inf).to_numpy()))
    half_life = RESONANCE_DECAY_D  # censored default
    for k in range(1, RESONANCE_DECAY_D + 1):
        if peak_loc + k >= len(idx):
            break
        v = spread.iloc[peak_loc + k]
        if pd.notna(v) and float(v) <= half_level:
            half_life = k
            break
    return {
        "date": idx[ev_loc].date().isoformat(),
        "slosh_bp": round(slosh, 1),
        "half_life_d": int(half_life),
    }


def analyze(spread_bp: pd.Series) -> dict:
    """spread_bp: SOFR − IORB in basis points, daily, DatetimeIndex."""
    s = spread_bp.dropna()
    if len(s) < 300:
        return {"ok": False, "reason": f"insufficient spread history ({len(s)}d)"}

    events = _classify_events(s.index)
    modes: dict[str, dict] = {}
    scatter: list[list] = []  # [date, slosh_bp, mode] for the chart

    for mode in RESONANCE_MODES:
        resp = [r for r in (_event_response(s, e) for e in events[mode]) if r]
        if len(resp) < RESONANCE_MIN_EVENTS:
            modes[mode] = {"ok": False, "n": len(resp), "reason": "too few events"}
            continue
        for r in resp:
            scatter.append([r["date"], r["slosh_bp"], mode])

        sloshes = np.array([r["slosh_bp"] for r in resp])
        halves = np.array([r["half_life_d"] for r in resp])
        n_recent = min(RESONANCE_RECENT_N, len(resp) // 2)
        recent = sloshes[-n_recent:]
        recent_med = float(np.median(recent))
        prior_med = float(np.median(sloshes[:-n_recent]))
        # Amplification vs a 1bp floor: a basin that never sloshed before and
        # now moves 3bp is a real regime change, not a divide-by-zero artifact.
        amplification = recent_med / max(prior_med, 1.0)
        # Sensitivity check: recompute with the single largest recent slosh
        # removed. If amplification collapses, one event is doing the talking.
        amp_ex_max = None
        if len(recent) >= 3:
            ex = np.sort(recent)[:-1]
            amp_ex_max = float(np.median(ex)) / max(prior_med, 1.0)
        decay_recent = float(np.median(halves[-n_recent:]))
        decay_prior = float(np.median(halves[:-n_recent]))

        # Mode score: amplification carries 70, absolute recent slosh 30.
        amp_part = np.clip(
            (amplification - 1.0) / (RESONANCE_AMP_SATURATION - 1.0), 0.0, 1.0
        ) * 70.0
        level_part = np.clip(recent_med / 15.0, 0.0, 1.0) * 30.0
        modes[mode] = {
            "ok": True,
            "label": MODE_LABELS[mode],
            "n": len(resp),
            "n_recent": int(n_recent),
            "low_n": len(resp) < 10,
            "last": resp[-1],
            "recent_median_bp": round(recent_med, 1),
            "prior_median_bp": round(prior_med, 1),
            "amplification": round(amplification, 2),
            "amplification_ex_max": round(amp_ex_max, 2) if amp_ex_max is not None else None,
            "decay_recent_d": round(decay_recent, 1),
            "decay_prior_d": round(decay_prior, 1),
            "score": round(float(amp_part + level_part), 1),
        }

    live = {m: d for m, d in modes.items() if d.get("ok")}
    if not live:
        return {"ok": False, "reason": "no mode has enough events"}
    wsum = sum(MODE_WEIGHTS[m] for m in live)
    score = sum(live[m]["score"] * MODE_WEIGHTS[m] for m in live) / wsum

    # The one-line verdict: which mode is amplifying fastest?
    worst = max(live.items(), key=lambda kv: kv[1]["amplification"])
    scatter.sort(key=lambda r: r[0])
    return {
        "ok": True,
        "asof": s.index[-1].date().isoformat(),
        "score": round(float(score), 1),
        "modes": modes,
        "worst_mode": {
            "mode": worst[0],
            "label": worst[1]["label"],
            "amplification": worst[1]["amplification"],
        },
        "events_scatter": scatter[-120:],
        "method": (
            f"per calendar mode: slosh = max(SOFR−IORB, event ±{RESONANCE_WINDOW_D}bd) − "
            f"median({RESONANCE_PRE_BASELINE_D}bd baseline ending 3bd prior); amplification = "
            f"median slosh last {RESONANCE_RECENT_N} events / prior events; decay = days to give "
            f"back half the slosh (censored {RESONANCE_DECAY_D}d). Modes disjoint; weights: "
            + ", ".join(f"{m} {w}" for m, w in MODE_WEIGHTS.items())
            + ". ex-max = amplification with the largest recent slosh removed (one-event "
            "sensitivity); modes with n<10 carry a low-n flag"
        ),
    }


def resonance_score(result: dict) -> float:
    if not result.get("ok"):
        return 0.0
    return float(np.clip(result.get("score", 0.0), 0.0, 100.0))
