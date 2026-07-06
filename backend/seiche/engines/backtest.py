"""PROOF — the backtest lab. The page that earns the right to be believed.

Three questions, answered with numbers a skeptic can recompute:

1. EVENT CAPTURE — does the (expanding-window, no-look-ahead) Seiche-lite
   index actually lead funding events? Funding event = SOFR−IORB jumping
   ≥ BACKTEST_SPIKE_BP over its trailing 5d median. We report recall (events
   preceded by an alert), precision (alerts followed by an event), lead-time
   distribution, and — critically — the base rate a coin-flipper would get.

2. EPISODE LEAD TABLE — for each labeled historical episode: the index
   percentile in the run-up and how many days before the break it first
   crossed the alert line.

3. MARKET OUTCOMES BY SIGNAL BUCKET — forward 5/20d moves of S&P, VIX,
   HY OAS, 10y by index-percentile bucket. This is The Tell's evidence base.

Anti-rigging rules: expanding-window standardization only, alert threshold
fixed in config (not fitted), overlapping-window counts disclosed, and the
vintage caveat printed. If the numbers are unimpressive, they publish anyway.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    BACKTEST_ALERT_PCTL,
    BACKTEST_EVENT_FWD_D,
    BACKTEST_MIN_WARMUP_D,
    BACKTEST_SPIKE_BP,
    EPISODES,
    PLAYBOOK_OUTCOMES,
)

PCTL_BUCKETS = [(0, 60, "0-60"), (60, 80, "60-80"), (80, 90, "80-90"), (90, 101, "90-100")]


def _funding_events(spread_bp: pd.Series) -> pd.DatetimeIndex:
    s = spread_bp.dropna()
    jump = s - s.rolling(5, min_periods=3).median().shift(1)
    ev_days = s.index[jump >= BACKTEST_SPIKE_BP]
    # Collapse clusters: keep the first day of any run within 5bd.
    kept: list[pd.Timestamp] = []
    for d in ev_days:
        if not kept or (d - kept[-1]).days > 7:
            kept.append(d)
    return pd.DatetimeIndex(kept)


def run(
    lite_pctl: pd.Series,           # expanding percentile of Seiche-lite (0-100)
    spread_bp: pd.Series,           # SOFR - IORB bp
    outcomes: dict[str, pd.Series], # market level series
) -> dict:
    pct = lite_pctl.dropna()
    if len(pct) < BACKTEST_MIN_WARMUP_D + 100:
        return {"ok": False, "reason": "insufficient scored history"}
    pct = pct.iloc[BACKTEST_MIN_WARMUP_D:]

    events = _funding_events(spread_bp)
    events = events[(events >= pct.index[0]) & (events <= pct.index[-1])]

    alert = pct >= BACKTEST_ALERT_PCTL

    # --- 1. Event capture -------------------------------------------------
    # Lead time = start of the continuous alert run the event landed in (not
    # just the capture-window boundary), capped at 60 calendar days.
    lead_times, captured = [], 0
    for ev in events:
        loc = pct.index.searchsorted(ev)
        window = alert.iloc[max(loc - BACKTEST_EVENT_FWD_D, 0) : loc]
        if window.any():
            captured += 1
            j = loc - 1
            while j > 0 and bool(alert.iloc[j - 1]):
                j -= 1
            lead = int((ev - pct.index[j]).days)
            lead_times.append(min(lead, 60))
    recall = captured / len(events) if len(events) else None

    # Precision: alert days followed by an event within the forward window.
    # Same 5bd (~9 calendar day) window is used for the all-days base rate so
    # precision and base rate are directly comparable.
    def _event_within(d: pd.Timestamp) -> bool:
        return bool(((events > d) & (events <= d + pd.Timedelta(days=9))).any())

    alert_days = pct.index[alert]
    precision = (
        sum(1 for d in alert_days if _event_within(d)) / len(alert_days)
        if len(alert_days)
        else None
    )
    base_rate = (
        sum(1 for d in pct.index if _event_within(d)) / len(pct) if len(pct) else None
    )

    # --- 2. Episode lead table --------------------------------------------
    episode_rows = []
    for ep_date, label in EPISODES.items():
        ts = pd.Timestamp(ep_date)
        if ts < pct.index[0] or ts > pct.index[-1]:
            episode_rows.append({"episode": label, "date": ep_date, "in_sample": False})
            continue
        loc = pct.index.searchsorted(ts)
        runup = pct.iloc[max(loc - 30, 0) : loc]
        crossed = runup[runup >= BACKTEST_ALERT_PCTL]
        episode_rows.append(
            {
                "episode": label,
                "date": ep_date,
                "in_sample": True,
                "max_pctl_30d_before": round(float(runup.max()), 0) if not runup.empty else None,
                "first_alert_lead_d": int((ts - crossed.index[0]).days) if not crossed.empty else None,
            }
        )

    # --- 3. Market outcomes by signal bucket -------------------------------
    outcome_tables = []
    for mnem, (label, kind) in PLAYBOOK_OUTCOMES.items():
        s = outcomes.get(mnem)
        if s is None or s.dropna().empty:
            continue
        s = s.dropna()
        for h in (5, 20):
            if kind == "pct":
                fwd = (s.shift(-h) / s - 1.0) * 100.0
            elif kind == "diff_bp":
                fwd = (s.shift(-h) - s) * 100.0
            else:
                fwd = s.shift(-h) - s
            joined = pd.concat({"pctl": pct, "fwd": fwd}, axis=1).dropna()
            buckets = []
            for lo, hi, name in PCTL_BUCKETS:
                grp = joined[(joined["pctl"] >= lo) & (joined["pctl"] < hi)]["fwd"]
                buckets.append(
                    {
                        "bucket": name,
                        "median": round(float(grp.median()), 2) if len(grp) else None,
                        "pct_positive": round(float((grp > 0).mean() * 100), 0) if len(grp) else None,
                        "n_days": int(len(grp)),
                        "n_independent": max(1, len(grp) // h),
                    }
                )
            outcome_tables.append({"outcome": label, "horizon_bd": h, "buckets": buckets})

    return {
        "ok": True,
        "asof": pct.index[-1].date().isoformat(),
        "sample": {
            "start": pct.index[0].date().isoformat(),
            "end": pct.index[-1].date().isoformat(),
            "n_days": int(len(pct)),
            "n_events": int(len(events)),
            "event_dates": [d.date().isoformat() for d in events],
        },
        "event_capture": {
            "spike_def_bp": BACKTEST_SPIKE_BP,
            "alert_pctl": BACKTEST_ALERT_PCTL,
            "recall": round(recall, 3) if recall is not None else None,
            "precision": round(precision, 3) if precision is not None else None,
            "base_rate": round(base_rate, 3) if base_rate is not None else None,
            "lead_times_d": lead_times,
            "median_lead_d": float(np.median(lead_times)) if lead_times else None,
        },
        "episodes": episode_rows,
        "outcome_tables": outcome_tables,
        "signal_series": [
            [d.date().isoformat(), round(float(v), 1)] for d, v in pct.iloc[::3].items()
        ],
        "caveats": [
            "expanding-window standardization only — no look-ahead in the signal",
            "alert threshold fixed in config, not fitted to outcomes",
            "final-vintage data (weekly H.4.1 aggregates are lightly revised; daily market prints effectively are not)",
            "overlapping forward windows: n_independent ≈ n_days / horizon is shown",
            "Seiche-lite excludes live-only engines (weather/resonance/hydrophone/warehouse) — the live index has MORE information than this backtest",
        ],
        "method": (
            f"event = SOFR−IORB ≥ +{BACKTEST_SPIKE_BP}bp vs trailing 5d median (clusters "
            f"collapsed); alert = Seiche-lite expanding pctl ≥ {BACKTEST_ALERT_PCTL}; capture "
            f"window {BACKTEST_EVENT_FWD_D}bd"
        ),
    }
