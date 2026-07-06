"""Station-Keeping — maneuver detection for the reserve system.

Method transfer from satellite tracking (the SGP4/Skyfield workflow in every
orbit-determination repo): propagate the expected state from known dynamics,
compare against observations, and treat persistent innovation residuals as a
MANEUVER — the object fired thrusters your model doesn't know about.

The reserve system has known dynamics too: the fiscal calendar (Weather's
seasonal dTGA model), quarter-end RRP patterns, and the Fed's balance-sheet
drift. So: one-step-ahead innovations per channel, standardized by rolling
MAD, accumulated with a two-sided CUSUM. An alarm = an unmodeled burn —
debt-ceiling cash games, an RMP pace change, a QT restart — often visible in
the residuals days before it's narrated in public.

This is simultaneously a regime detector AND the Weather model's own health
monitor: if Station-Keeping alarms constantly, the seasonal model is stale.
Context engine — never weighted into the composite.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.engines.weather import _day_bucket, _seasonal_dtga

CUSUM_K = 0.5          # drift allowance (in MAD units)
CUSUM_H_DAILY = 6.0    # alarm threshold, daily channels
CUSUM_H_WEEKLY = 4.0   # alarm threshold, weekly channel
MAD_WINDOW = 250
MAD_FLOOR_B = 0.5      # $B — a dead-quiet channel must not alarm on noise


def _cusum(z: pd.Series, raw_b: pd.Series, h: float) -> tuple[list[dict], dict]:
    """Two-sided CUSUM over standardized innovations.

    Returns (alarms, state). Each alarm carries the run start (when the
    statistic left zero) and the cumulative raw drift ($B) over the run —
    the size of the detected burn.
    """
    sp = sn = 0.0
    sp_start = sn_start = None
    alarms: list[dict] = []
    for t, v in z.items():
        if not np.isfinite(v):
            continue
        prev_sp, prev_sn = sp, sn
        sp = max(0.0, sp + v - CUSUM_K)
        sn = max(0.0, sn - v - CUSUM_K)
        if prev_sp == 0.0 and sp > 0.0:
            sp_start = t
        if prev_sn == 0.0 and sn > 0.0:
            sn_start = t
        if sp > h:
            run = raw_b.loc[sp_start:t] if sp_start is not None else raw_b.loc[[t]]
            alarms.append({
                "date": t.date().isoformat(),
                "start": sp_start.date().isoformat() if sp_start is not None else None,
                "direction": "drain" if float(run.sum()) < 0 else "build",
                "cum_b": round(float(run.sum()), 1),
            })
            sp, sp_start = 0.0, None
        if sn > h:
            run = raw_b.loc[sn_start:t] if sn_start is not None else raw_b.loc[[t]]
            alarms.append({
                "date": t.date().isoformat(),
                "start": sn_start.date().isoformat() if sn_start is not None else None,
                "direction": "drain" if float(run.sum()) < 0 else "build",
                "cum_b": round(float(run.sum()), 1),
            })
            sn, sn_start = 0.0, None
    state = {
        "s_pos": round(sp, 2),
        "s_neg": round(sn, 2),
        "active": bool(max(sp, sn) > h / 2),
        "active_since": (
            (sp_start or sn_start).date().isoformat()
            if (sp if sp >= sn else sn) > h / 2 and (sp_start if sp >= sn else sn_start) is not None
            else None
        ),
    }
    return alarms, state


def _standardize(innov: pd.Series, floor_b: float = MAD_FLOOR_B) -> pd.Series:
    med = innov.rolling(MAD_WINDOW, min_periods=60).median()
    mad = (innov - med).abs().rolling(MAD_WINDOW, min_periods=60).median()
    scale = (1.4826 * mad).clip(lower=floor_b)
    return ((innov - med) / scale).dropna()


def analyze(
    tga_daily: pd.Series,      # $B
    rrp_daily: pd.Series,      # $B
    walcl_weekly: pd.Series,   # $M
) -> dict:
    channels: dict[str, dict] = {}
    all_alarms: list[dict] = []

    # --- TGA: innovations vs the Weather seasonal model --------------------
    tga = tga_daily.dropna()
    if len(tga) > 400:
        chg = tga.diff().dropna()
        seasonal = _seasonal_dtga(tga)
        expected = pd.Series([seasonal.get(_day_bucket(t), 0.0) for t in chg.index], index=chg.index)
        innov = chg - expected
        z = _standardize(innov)
        alarms, state = _cusum(z, innov, CUSUM_H_DAILY)
        channels["TGA"] = {**state, "n_alarms": len(alarms),
                           "note": "innovations vs the fiscal seasonal model — burns = unscheduled cash moves (debt-ceiling games, buyback shifts)"}
        for a in alarms:
            all_alarms.append({**a, "channel": "TGA"})

    # --- RRP: innovations vs its own calendar-bucket pattern ---------------
    rrp = rrp_daily.dropna()
    if len(rrp) > 400:
        chg = rrp.diff().dropna()
        buckets: dict[str, list[float]] = {}
        for t, v in chg.items():
            buckets.setdefault(_day_bucket(t), []).append(float(v))
        med = {k: float(np.median(v)) for k, v in buckets.items() if len(v) >= 3}
        expected = pd.Series([med.get(_day_bucket(t), 0.0) for t in chg.index], index=chg.index)
        innov = chg - expected
        # RRP has sat near zero since 2025 — with a small floor its rolling
        # MAD collapses and every $2B blip alarms (192 alarms on first run).
        # A $5B floor keeps only moves that matter at facility scale.
        z = _standardize(innov, floor_b=5.0)
        alarms, state = _cusum(z, innov, CUSUM_H_DAILY)
        channels["RRP"] = {**state, "n_alarms": len(alarms),
                           "note": "burns = the shock absorber refilling/draining outside its calendar rhythm"}
        for a in alarms:
            all_alarms.append({**a, "channel": "RRP"})

    # --- WALCL: weekly drift breaks ----------------------------------------
    w = (walcl_weekly.dropna() / 1000.0)
    if len(w) > 80:
        chg = w.diff().dropna()
        innov = (chg - chg.rolling(26, min_periods=13).median()).dropna()
        med = innov.rolling(52, min_periods=26).median()
        mad = (innov - med).abs().rolling(52, min_periods=26).median()
        z = ((innov - med) / (1.4826 * mad).clip(lower=1.0)).dropna()
        alarms, state = _cusum(z, innov, CUSUM_H_WEEKLY)
        channels["WALCL"] = {**state, "n_alarms": len(alarms),
                             "note": "burns = balance-sheet policy changes (QT stop, RMP pace) vs the trailing 26w drift"}
        for a in alarms:
            all_alarms.append({**a, "channel": "WALCL"})

    if not channels:
        return {"ok": False, "reason": "no channel has enough history"}

    all_alarms.sort(key=lambda a: a["date"], reverse=True)
    return {
        "ok": True,
        "asof": max(
            s.dropna().index[-1] for s in (tga_daily, rrp_daily, walcl_weekly) if not s.dropna().empty
        ).date().isoformat(),
        "channels": channels,
        "recent_maneuvers": all_alarms[:10],
        "any_active": any(c.get("active") for c in channels.values()),
        "method": (
            "orbit-determination transfer: propagate expected state (fiscal seasonal / "
            "calendar-bucket / trailing drift), standardize one-step innovations by "
            f"rolling MAD (floor ${MAD_FLOOR_B}B), two-sided CUSUM k={CUSUM_K}, "
            f"h={CUSUM_H_DAILY} daily / {CUSUM_H_WEEKLY} weekly; alarm = unmodeled burn. "
            "Doubles as the Weather model's health monitor. Context, not composite."
        ),
    }
