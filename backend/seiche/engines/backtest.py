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

import math

import numpy as np
import pandas as pd

from seiche.config import (
    BACKTEST_ALERT_PCTL,
    BACKTEST_EVENT_FWD_D,
    BACKTEST_MIN_WARMUP_D,
    BACKTEST_SPIKE_BP,
    EPISODE_CLASS,
    EPISODES,
    PLAYBOOK_OUTCOMES,
)


def _wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """Wilson score interval for a proportion — honest small-n error bars."""
    if n <= 0:
        return None
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, center - half), 3), round(min(1.0, center + half), 3)]

PCTL_BUCKETS = [(0, 60, "0-60"), (60, 80, "60-80"), (80, 90, "80-90"), (90, 101, "90-100")]


def pop_bp(spread_bp: pd.Series, grid: pd.DatetimeIndex | None = None) -> pd.Series:
    """THE event statistic, defined once: spread minus its trailing 5bd median
    (yesterday's yardstick). _funding_events thresholds it at BACKTEST_SPIKE_BP;
    Swell's hazard tables and the Fleet's labels read it raw. Tune it here and
    every layer moves together — a fork of this definition is a methodology bug."""
    s = spread_bp.dropna()
    if grid is not None:
        s = s.reindex(grid)
    return s - s.rolling(5, min_periods=3).median().shift(1)


def _funding_events(spread_bp: pd.Series) -> pd.DatetimeIndex:
    s = spread_bp.dropna()
    jump = pop_bp(spread_bp)
    ev_days = s.index[jump >= BACKTEST_SPIKE_BP]
    # Collapse clusters: keep the first day of any run within 5bd.
    kept: list[pd.Timestamp] = []
    for d in ev_days:
        if not kept or (d - kept[-1]).days > 7:
            kept.append(d)
    return pd.DatetimeIndex(kept)


def _capture_stats(pct: pd.Series, events: pd.DatetimeIndex) -> dict:
    """Event capture with honest error bars.

    - recall CI: Wilson over the (declustered, ~independent) events.
    - day-level precision is kept for continuity but alert DAYS are serially
      correlated, so the load-bearing number is RUN-level precision: contiguous
      alert runs are the independent-ish trials, Wilson over those.
    """
    alert = pct >= BACKTEST_ALERT_PCTL

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

    # Alert runs: contiguous stretches of alert==True.
    runs: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    in_run = False
    for i, flag in enumerate(alert):
        if flag and not in_run:
            start = pct.index[i]
            in_run = True
        elif not flag and in_run:
            runs.append((start, pct.index[i - 1]))
            in_run = False
    if in_run:
        runs.append((start, pct.index[-1]))
    runs_hit = sum(
        1 for s, e in runs
        if bool(((events >= s) & (events <= e + pd.Timedelta(days=9))).any())
    )
    precision_runs = runs_hit / len(runs) if runs else None

    return {
        "spike_def_bp": BACKTEST_SPIKE_BP,
        "alert_pctl": BACKTEST_ALERT_PCTL,
        "n_events": int(len(events)),
        "recall": round(recall, 3) if recall is not None else None,
        "recall_ci95": _wilson(captured, len(events)),
        "precision": round(precision, 3) if precision is not None else None,
        "precision_note": "day-level; alert days are serially correlated — use run-level",
        "base_rate": round(base_rate, 3) if base_rate is not None else None,
        "n_alert_runs": len(runs),
        "runs_hit": runs_hit,
        "precision_runs": round(precision_runs, 3) if precision_runs is not None else None,
        "precision_runs_ci95": _wilson(runs_hit, len(runs)),
        "lead_times_d": lead_times,
        "median_lead_d": float(np.median(lead_times)) if lead_times else None,
    }


def _episode_rows(pct: pd.Series) -> list[dict]:
    rows = []
    for ep_date, label in EPISODES.items():
        klass = EPISODE_CLASS.get(ep_date, "unclassified")
        ts = pd.Timestamp(ep_date)
        if ts < pct.index[0] or ts > pct.index[-1]:
            rows.append({"episode": label, "date": ep_date, "class": klass, "in_sample": False})
            continue
        loc = pct.index.searchsorted(ts)
        runup = pct.iloc[max(loc - 30, 0) : loc]
        crossed = runup[runup >= BACKTEST_ALERT_PCTL]
        rows.append(
            {
                "episode": label,
                "date": ep_date,
                "class": klass,
                "in_sample": True,
                "max_pctl_30d_before": round(float(runup.max()), 0) if not runup.empty else None,
                "first_alert_lead_d": int((ts - crossed.index[0]).days) if not crossed.empty else None,
            }
        )
    return rows


def _class_split(rows: list[dict]) -> dict:
    """Recall split by competence class. ENDOGENOUS events build up in the
    plumbing and should be caught; EXOGENOUS ones arrive from outside it and are
    expected misses. Stating both keeps the tool from claiming skill it lacks."""
    out: dict[str, dict] = {}
    for klass in ("endogenous", "exogenous"):
        ins = [r for r in rows if r.get("class") == klass and r.get("in_sample")]
        caught = [r for r in ins if r.get("first_alert_lead_d") is not None]
        leads = [r["first_alert_lead_d"] for r in caught]
        out[klass] = {
            "n": len(ins),
            "caught": len(caught),
            "recall": round(len(caught) / len(ins), 3) if ins else None,
            "median_lead_d": int(pd.Series(leads).median()) if leads else None,
            "episodes": [r["date"] for r in ins],
        }
    out["reading"] = (
        "endogenous events (reserve/calendar build-ups) are the ones Seiche is "
        "built to see; exogenous shocks (pandemic, single-bank run, policy) are "
        "not in the plumbing beforehand and are expected misses. Judge the tool "
        "on the endogenous row."
    )
    return out


def _event_auroc(pct: pd.Series, events: pd.DatetimeIndex) -> float | None:
    """Threshold-FREE skill: AUROC of the percentile as a score for 'a funding
    event lands within the next horizon'. 0.5 = no skill, 1.0 = perfect. Removes
    the 'you cherry-picked the alert threshold' objection — it scores every day
    at every operating point at once."""
    labels = np.zeros(len(pct), dtype=bool)
    for ev in events:
        loc = pct.index.searchsorted(ev)
        labels[max(loc - BACKTEST_EVENT_FWD_D, 0):loc] = True
    n_pos, n_neg = int(labels.sum()), int((~labels).sum())
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = pct.rank(method="average").to_numpy()          # ties -> average rank
    return round(float((ranks[labels].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)), 3)


def _significance(pct: pd.Series, events: pd.DatetimeIndex, n_perm: int = 2000) -> dict:
    """Block-permutation null: relocate the SAME alert runs (same count, same
    durations, same total alert budget) to random start times and measure how
    often chance placement matches this recall. p = P(random recall >= actual).
    Directly answers 'could this happen by luck?' — a low p means the TIMING of
    the alerts carries information, not just their quantity."""
    n = len(pct)
    alert = (pct >= BACKTEST_ALERT_PCTL).to_numpy()
    ev_locs = [pct.index.searchsorted(ev) for ev in events]
    fwd = BACKTEST_EVENT_FWD_D
    if not ev_locs or not alert.any():
        return {"ok": False, "reason": "no events or no alerts in sample"}

    def _recall(a: np.ndarray) -> float:
        return sum(1 for loc in ev_locs if a[max(loc - fwd, 0):loc].any()) / len(ev_locs)

    actual = _recall(alert)
    runs, i = [], 0
    while i < n:
        if alert[i]:
            j = i
            while j < n and alert[j]:
                j += 1
            runs.append(j - i)
            i = j
        else:
            i += 1
    rng = np.random.default_rng(20260710)                  # fixed: reproducible, notarisable
    null = np.empty(n_perm)
    for k in range(n_perm):
        a = np.zeros(n, dtype=bool)
        for length in runs:
            start = int(rng.integers(0, max(n - length, 1)))
            a[start:start + length] = True
        null[k] = _recall(a)
    pval = float((np.sum(null >= actual) + 1) / (n_perm + 1))
    return {
        "ok": True,
        "actual_recall": round(actual, 3),
        "null_mean_recall": round(float(null.mean()), 3),
        "null_p95_recall": round(float(np.percentile(null, 95)), 3),
        "p_value": round(pval, 4),
        "n_alert_runs": len(runs),
        "n_permutations": n_perm,
        "verdict": ("beats chance placement (p<0.05)" if pval < 0.05
                    else "NOT distinguishable from chance placement of the same alerts"),
    }


def capture(lite_pctl: pd.Series, spread_bp: pd.Series) -> dict:
    """Event capture + episode leads only — the orthogonal-test entry point."""
    pct = lite_pctl.dropna()
    if len(pct) < BACKTEST_MIN_WARMUP_D + 100:
        return {"ok": False, "reason": "insufficient scored history"}
    pct = pct.iloc[BACKTEST_MIN_WARMUP_D:]
    events = _funding_events(spread_bp)
    events = events[(events >= pct.index[0]) & (events <= pct.index[-1])]
    rows = _episode_rows(pct)
    return {
        "ok": True,
        "event_capture": _capture_stats(pct, events),
        "episodes": rows,
        "class_split": _class_split(rows),
    }


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

    capture_stats = _capture_stats(pct, events)
    episode_rows = _episode_rows(pct)
    class_split = _class_split(episode_rows)
    rigor = {
        "event_auroc": _event_auroc(pct, events),
        "significance": _significance(pct, events),
    }

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
        "event_capture": capture_stats,
        "episodes": episode_rows,
        "class_split": class_split,
        "rigor": rigor,
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
            "event count is small — Wilson 95% intervals are printed next to every rate; run-level precision is the load-bearing number (alert days are serially correlated)",
            "the lite index contains spread/tail terms and the event is a spread spike — see the ORTHOGONAL test for the same claim with the target's own variables removed",
        ],
        "method": (
            f"event = SOFR−IORB ≥ +{BACKTEST_SPIKE_BP}bp vs trailing 5d median (clusters "
            f"collapsed); alert = Seiche-lite expanding pctl ≥ {BACKTEST_ALERT_PCTL}; capture "
            f"window {BACKTEST_EVENT_FWD_D}bd"
        ),
    }
