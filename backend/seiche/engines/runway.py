"""Reserve Runway: 13-week reserve-path projection with a named kink date.

The kink engine says where scarcity starts; this engine says when we get
there. Weekly reserve identity, projected 13 weeks under three stated
scenarios:

    base:       trailing 13w drift + QT pace + settlement drains, with TGA
                mean-reverting to its trailing median
    fast_drain: same, but TGA rebuilds to its trailing p75 and any remaining
                ON RRP balance gives no buffer (floored at zero)
    slow:       same as base, but ON RRP absorbs half of each week's drain
                until the balance is spent

Each scenario gets a weekly path, the date it crosses the fitted kink (or
null), and a one-line verdict. Assumptions ship as data so the letter can
print them, and a vintage_record is emitted for an append-only ledger so the
paths can be scored later. This is arithmetic on stated assumptions, not a
forecast of policy: the trailing drift already embeds recent QT and fiscal
flows, so the explicit QT and settlement terms can double count, and we say
so rather than pretend otherwise.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HORIZON_WEEKS = 13
DRIFT_WINDOW_WEEKS = 13          # trailing change window for the base drift
TGA_WINDOW_OBS = 126             # ~6 months of daily TGA prints
SETTLEMENT_PASSTHROUGH = 0.25    # gross issuance mostly rolls maturities; assume 25 pct is net new cash
UNIT_THRESHOLD = 50_000.0        # RRP/TGA peaks sit near 2500 in $B and millions in $M; this separates cleanly


def _to_b(s: pd.Series) -> pd.Series:
    # Accept $B (native RRPONTSYD / DTS units) or $M; normalize by magnitude.
    s = s.dropna() if s is not None else pd.Series(dtype=float)
    if s.empty:
        return s
    return s / 1000.0 if float(s.abs().max()) > UNIT_THRESHOLD else s


def _settlements_by_week(calendar_settlements, start: pd.Timestamp) -> tuple[dict[int, float], float]:
    """Settlement pressure by projection week, as a DEVIATION from the
    calendar's own weekly average.

    The level effect of routine settlement is already inside the trailing
    drift (which is realized reserve change, settlements and all) and inside
    the TGA path (a settlement is largely a transfer into the TGA). Booking
    the raw drain on top of both counted the same dollars up to three times
    and pulled every kink crossing earlier than the arithmetic supports.
    Demeaning keeps what the calendar genuinely adds, which is SHAPE: a heavy
    settlement week dips below the path and a light week sits above it, with
    no net level bias over the horizon.
    """
    raw: dict[int, float] = {}
    gross = 0.0
    for item in calendar_settlements or []:
        try:
            d = pd.Timestamp(item["date"])
            amt = float(item["amount_b"])
        except (KeyError, TypeError, ValueError):
            continue
        days = (d - start).days
        if days <= 0 or days > 7 * HORIZON_WEEKS or not np.isfinite(amt):
            continue
        w = int(np.ceil(days / 7.0))
        raw[w] = raw.get(w, 0.0) + amt * SETTLEMENT_PASSTHROUGH
        gross += amt
    if not raw:
        return {}, 0.0
    # Spread the mean over every week in the covered span, not just the weeks
    # that happen to carry a settlement, so the demeaning is unbiased.
    span = max(raw)
    mean_w = sum(raw.values()) / float(span)
    by_week = {w: round(raw.get(w, 0.0) - mean_w, 4) for w in range(1, span + 1)}
    return by_week, gross


def _walk(
    start: pd.Timestamp,
    start_b: float,
    drift_w: float,
    qt_w: float,
    tga_w: float,
    settle_by_week: dict[int, float],
    rrp_buffer_b: float,
    buffer_frac: float,
) -> list[tuple[pd.Timestamp, float]]:
    level = start_b
    pts = [(start, level)]
    rrp_left = max(rrp_buffer_b, 0.0)
    for w in range(1, HORIZON_WEEKS + 1):
        delta = drift_w + qt_w + tga_w - settle_by_week.get(w, 0.0)
        if delta < 0 and buffer_frac > 0 and rrp_left > 0:
            buffered = min(-delta * buffer_frac, rrp_left)
            delta += buffered
            rrp_left -= buffered
        level += delta
        pts.append((start + pd.Timedelta(days=7 * w), level))
    return pts


def _crossing(pts: list[tuple[pd.Timestamp, float]], kink_b: float | None) -> tuple[str | None, int | None, str]:
    if kink_b is None:
        return None, None, "no kink estimate to cross; path published without a crossing date"
    end_gap = pts[-1][1] - kink_b
    if pts[0][1] <= kink_b:
        return pts[0][0].date().isoformat(), 0, "already below the estimated kink at the start of the window"
    for i in range(1, len(pts)):
        t0, l0 = pts[i - 1]
        t1, l1 = pts[i]
        if l1 <= kink_b:  # touching the kink counts; matches the start-of-window check
            # Linear interpolation inside the week for a named day.
            frac = (l0 - kink_b) / (l0 - l1) if l0 != l1 else 1.0
            day = t0 + pd.Timedelta(days=int(round(np.clip(frac, 0.0, 1.0) * 7)))
            return day.date().isoformat(), i, f"crosses the kink near {day.date().isoformat()}, week {i} of {HORIZON_WEEKS}"
    return None, None, f"no crossing inside {HORIZON_WEEKS} weeks; ends {end_gap:.0f} above the kink"


def project(
    reserves_weekly: pd.Series,        # WRESBAL, $M, weekly Wed
    rrp_daily: pd.Series,              # ON RRP take-up, $B native (RRPONTSYD); $M accepted and normalized
    tga_daily: pd.Series,              # Treasury General Account, $B native (DTS); $M accepted and normalized
    kink: dict,                        # engines.kink payload: kink_reserves_b, current_reserves_b, drain_per_bday_b
    calendar_settlements: list,        # [{date, amount_b}] like calendar.upcoming_settlements
    qt_pace_b_per_month: float,        # caller-stated runoff pace, $B per month, positive = drain
) -> dict:
    res_b = (reserves_weekly.dropna() / 1000.0).resample("W-WED").last().dropna() if reserves_weekly is not None and not reserves_weekly.dropna().empty else pd.Series(dtype=float)
    if res_b.empty:
        return {"ok": False, "reason": "no reserve history"}

    tga_b = _to_b(tga_daily)
    if tga_b.empty:
        return {"ok": False, "reason": "TGA history unavailable"}
    rrp_b = _to_b(rrp_daily)

    kink = kink if isinstance(kink, dict) else {}
    kink_b = kink.get("kink_reserves_b") if kink.get("ok", True) else None
    kink_b = float(kink_b) if kink_b is not None else None

    caveats = [
        "arithmetic on stated assumptions, not a forecast of policy",
        "the trailing drift already embeds recent QT, settlement and fiscal flows, so the settlement "
        "term enters as a deviation from the calendar's own weekly mean (shape, not level) and only "
        "the explicit QT pace can still double count against the drift",
        f"settlement drains counted at {SETTLEMENT_PASSTHROUGH:.0%} of gross (rollover assumption) "
        "before demeaning",
    ]

    # Base drift: trailing 13w reserve change per week; fall back to the kink
    # engine's per-bday drain when the weekly history is short.
    if len(res_b) >= DRIFT_WINDOW_WEEKS + 1:
        tail = res_b.tail(DRIFT_WINDOW_WEEKS + 1)
        drift_w = float((tail.iloc[-1] - tail.iloc[0]) / (len(tail) - 1))
    elif kink.get("drain_per_bday_b") is not None:
        drift_w = float(kink["drain_per_bday_b"]) * 5.0
        caveats.append(f"reserve history short ({len(res_b)} weeks); drift taken from the kink engine's per-bday drain")
    else:
        return {"ok": False, "reason": f"insufficient reserve history ({len(res_b)} weeks) and no kink drain fallback"}

    if qt_pace_b_per_month is None:
        qt_pace_b_per_month = 0.0
        caveats.append("QT pace not supplied; assumed 0")
    qt_w = -float(qt_pace_b_per_month) * 12.0 / 52.0

    tga_tail = tga_b.tail(TGA_WINDOW_OBS)
    tga_now = float(tga_tail.iloc[-1])
    tga_median = float(tga_tail.median())
    tga_p75 = float(tga_tail.quantile(0.75))
    # TGA rising by X drains reserves by X; spread the reversion over the horizon.
    tga_w_base = (tga_now - tga_median) / HORIZON_WEEKS
    tga_w_fast = (tga_now - tga_p75) / HORIZON_WEEKS

    if rrp_b.empty:
        rrp_now = 0.0
        caveats.append("RRP history unavailable; slow scenario has no buffer")
    else:
        rrp_now = float(rrp_b.iloc[-1])

    start = res_b.index[-1]
    start_b = float(res_b.iloc[-1])
    settle_by_week, settle_gross = _settlements_by_week(calendar_settlements, start)

    specs = {
        "base": dict(tga_w=tga_w_base, rrp_buffer_b=0.0, buffer_frac=0.0),
        "fast_drain": dict(tga_w=tga_w_fast, rrp_buffer_b=0.0, buffer_frac=0.0),
        "slow": dict(tga_w=tga_w_base, rrp_buffer_b=rrp_now, buffer_frac=0.5),
    }
    scenarios: dict = {}
    for name, sp in specs.items():
        pts = _walk(start, start_b, drift_w, qt_w, sp["tga_w"], settle_by_week, sp["rrp_buffer_b"], sp["buffer_frac"])
        cross_date, cross_week, verdict = _crossing(pts, kink_b)
        scenarios[name] = {
            "path": [[t.date().isoformat(), round(v, 1)] for t, v in pts],
            "crossing_date": cross_date,
            "crossing_week": cross_week,
            "end_reserves_b": round(pts[-1][1], 1),
            "verdict": verdict,
        }
    if kink_b is None:
        caveats.append("kink estimate unavailable; crossing dates are null")

    assumptions = {
        "horizon_weeks": HORIZON_WEEKS,
        "trailing_drift_b_per_week": round(drift_w, 1),
        "drift_window_weeks": DRIFT_WINDOW_WEEKS,
        "qt_pace_b_per_month": round(float(qt_pace_b_per_month), 1),
        "qt_b_per_week": round(qt_w, 1),
        "tga_now_b": round(tga_now, 1),
        "tga_median_b": round(tga_median, 1),
        "tga_p75_b": round(tga_p75, 1),
        "tga_window_obs": int(len(tga_tail)),
        "rrp_now_b": round(rrp_now, 1),
        "slow_rrp_buffer_frac": 0.5,
        "settlements_gross_b": round(settle_gross, 1),
        "settlement_passthrough": SETTLEMENT_PASSTHROUGH,
        "kink_reserves_b": round(kink_b, 1) if kink_b is not None else None,
        "start_reserves_b": round(start_b, 1),
        "start_date": start.date().isoformat(),
    }
    asof = start.date().isoformat()
    vintage_record = {
        "schema": 1,
        "asof": asof,
        "kink_reserves_b": assumptions["kink_reserves_b"],
        "start_reserves_b": assumptions["start_reserves_b"],
        "crossings": {name: s["crossing_date"] for name, s in scenarios.items()},
        "paths": {name: s["path"] for name, s in scenarios.items()},
        "assumptions": assumptions,
    }
    return {
        "ok": True,
        "asof": asof,
        "scenarios": scenarios,
        "assumptions": assumptions,
        "vintage_record": vintage_record,
        "caveats": caveats,
        "method": "13-week weekly reserve identity: trailing 13w drift + stated QT pace + TGA reversion (median / p75) + settlement drains at 25 pct passthrough; RRP buffers half the drain in the slow leg",
    }


def runway_score(result: dict) -> float:
    """0-100 sub-score off the base scenario: sooner crossing, higher score.

    Already below the kink saturates; a crossing inside the window ramps
    100 -> 30 with the crossing week; no crossing ramps 0 -> 30 as the end
    gap closes inside 600 $B (matching the kink score's approach band).
    """
    if not result.get("ok"):
        return 0.0
    base = (result.get("scenarios") or {}).get("base") or {}
    week = base.get("crossing_week")
    if week is not None:
        return float(np.clip(100.0 - (week / HORIZON_WEEKS) * 70.0, 30.0, 100.0))
    kink_b = (result.get("assumptions") or {}).get("kink_reserves_b")
    end_b = base.get("end_reserves_b")
    if kink_b is None or end_b is None:
        return 0.0
    gap = end_b - kink_b
    return float(np.clip(1.0 - gap / 600.0, 0.0, 1.0)) * 30.0
