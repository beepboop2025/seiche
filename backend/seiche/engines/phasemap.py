"""Phase Map: the calendar clock the siblings hard-code, audited in public.

Kim and Hansen (arXiv 2607.09426) show that phase-RESOLVED statistics uncover
serial dependence that phase-AVERAGED measures hide: condition on where you
are in the cycle and correlations appear that the pooled estimate washes out.
Their clock is the intraday 15-minute grid; the funding basin's clock is the
US calendar. This engine makes the transfer: a calendar-phase-resolved map of
lag-1 serial dependence in the daily funding micro-shock, published with
bands, so the calendar assumptions the sibling engines HARD-CODE get graded
where readers can see it. Resonance scores sloshes at event +/- 1bd; Swell's
hazard tables and Microseism's Hawkes baseline gate on one-bucket-per-day
calendar buckets (turn = last bd + first bd, tax and mid-month = one day).
Nobody has checked, phase by phase, whether serial dependence actually
concentrates on those days. Now the board checks itself.

Five clocks, each an independent phase coordinate:
  - month-end turn (non-quarter months): signed bd distance to the last bd;
  - quarter-end turn (Mar/Jun/Sep/Dec, the December year-end included);
  - Treasury settlement cycle: coupon settlement days from the realized
    auction calendar (the same parser Weather and Swell share), falling back
    to the conventional mid-month + month-end settlement grid;
  - corporate tax dates (config CORPORATE_TAX_DAYS, the set Resonance and
    Swell key on);
  - reserve-maintenance-period day, VESTIGIAL: reserve requirements have
    been zero since March 2020, no engine on this board keys on it, and the
    map is published as a historical audit of a clock the era retired.

Per phase bin: n, mean |shock|, the shock exceedance rate with a Wilson 95%
CI, and the phase-resolved lag-1 autocorrelation (each observation paired
with the NEXT observation in calendar order, the pair attributed to the bin
of the FIRST day, the kernel paper's construction) with a Fisher-z 95% CI.
Bins are thin (roughly 100-200 obs in the SOFR era): an unbanded map invites
noise-reading, so the bands are mandatory.

Honesty notes:
  - the shock is THE shared pop statistic (backtest.pop_bp), never forked;
  - the audit comparator is the shared plain set: days more than
    PHASEMAP_NEAR_BD business days from EVERY live clock's events, so one
    clock's forcing cannot contaminate another clock's control group;
  - verdicts are display-only self-audit: no composite weight, no alert; a
    verdict here changes the siblings only through a pre-registered
    methodology change, never automatically;
  - no RNG anywhere; the map is deterministic.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from seiche.config import (
    CORPORATE_TAX_DAYS,
    MICRO_POP_BP,
    PHASEMAP_MIN_EVENTS,
    PHASEMAP_MIN_HISTORY_D,
    PHASEMAP_MIN_PAIRS,
    PHASEMAP_NEAR_BD,
    PHASEMAP_RMP_ANCHOR,
    PHASEMAP_RMP_ZERO_FROM,
    SETTLEMENT_FLAG_B,
)
from seiche.engines.backtest import _wilson, pop_bp
from seiche.engines.swell import coupon_settlements

_QTR_MONTHS = (3, 6, 9, 12)                              # year-end included
_TAX_MONTHS = tuple(sorted({m for m, _ in CORPORATE_TAX_DAYS}))
_EVENT_BINS = ("-1", "0", "+1")                          # where the siblings put the forcing
# residue of (date - anchor).days mod 14 -> business day 1..10 of the period
# (10 = the period-end settlement Wednesday); weekend residues never occur
# for business days because mod 14 preserves the weekday.
_RMP_RESIDUE_TO_DAY = {1: 1, 2: 2, 5: 3, 6: 4, 7: 5, 8: 6, 9: 7, 12: 8, 13: 9, 0: 10}


# ---------------------------------------------------------------------------
# Phase coordinates
# ---------------------------------------------------------------------------

def _bin_order(near: int = PHASEMAP_NEAR_BD) -> list[str]:
    return [f"{-d:+d}" for d in range(near, 0, -1)] + ["0"] \
        + [f"{+d:+d}" for d in range(1, near + 1)] + ["interior"]


def _signed_bins(n: int, ev_pos: np.ndarray, near: int = PHASEMAP_NEAR_BD) -> np.ndarray:
    """Label every observation by signed distance (in observation steps) to
    the NEAREST event: negative approaching, 0 on the event, positive after.
    Beyond +/- near it is 'interior'. Ties go to the upcoming event."""
    labels = np.full(n, "interior", dtype=object)
    if ev_pos.size == 0:
        return labels
    i = np.arange(n)
    j = np.searchsorted(ev_pos, i)
    big = 10 ** 9
    d_next = np.where(j < ev_pos.size, ev_pos[np.minimum(j, ev_pos.size - 1)] - i, big)
    d_prev = np.where(j > 0, i - ev_pos[np.maximum(j - 1, 0)], big)
    signed = np.where(d_next <= d_prev, -d_next, d_prev)
    for d in range(-near, near + 1):
        labels[signed == d] = "0" if d == 0 else f"{d:+d}"
    return labels


def _positions(idx: pd.DatetimeIndex, dates: list[pd.Timestamp]) -> np.ndarray:
    """Event dates snapped onto the observed series: the first observation at
    or after each date (an event on a data gap snaps forward, stated in the
    method). Out-of-sample dates drop."""
    pos = idx.searchsorted(pd.DatetimeIndex(dates)) if dates else np.array([], dtype=int)
    return np.unique(pos[pos < len(idx)])


def _by_month(idx: pd.DatetimeIndex) -> dict[tuple[int, int], list[pd.Timestamp]]:
    bdays = pd.bdate_range(idx.min(), idx.max())
    out: dict[tuple[int, int], list[pd.Timestamp]] = {}
    for d in bdays:
        out.setdefault((d.year, d.month), []).append(d)
    return out


def _turn_events(idx: pd.DatetimeIndex) -> tuple[list[pd.Timestamp], list[pd.Timestamp]]:
    """(month-end events for non-quarter months, quarter-end events)."""
    month_ends: list[pd.Timestamp] = []
    quarter_ends: list[pd.Timestamp] = []
    for (_, m), days in sorted(_by_month(idx).items()):
        (quarter_ends if m in _QTR_MONTHS else month_ends).append(days[-1])
    return month_ends, quarter_ends


def _mid_month(days: list[pd.Timestamp]) -> pd.Timestamp | None:
    return next((d for d in days if d.day >= 15), None)


def _tax_events(idx: pd.DatetimeIndex) -> list[pd.Timestamp]:
    out = []
    for (_, m), days in sorted(_by_month(idx).items()):
        if m in _TAX_MONTHS:
            mid = _mid_month(days)
            if mid is not None:
                out.append(mid)
    return out


def _conventional_settlements(idx: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """The convention when no auction frame is on hand: Treasury coupons
    settle mid-month (first bd on/after the 15th) and at month-end."""
    out = []
    for _, days in sorted(_by_month(idx).items()):
        mid = _mid_month(days)
        if mid is not None:
            out.append(mid)
        out.append(days[-1])
    return out


def _settlement_events(idx: pd.DatetimeIndex, auctions: pd.DataFrame | None) -> tuple[list[pd.Timestamp], str]:
    """Realized coupon-settlement piles >= the Swell/Weather flag size when
    the auction frame supports it; the conventional grid otherwise."""
    settle = coupon_settlements(auctions if auctions is not None else pd.DataFrame())
    if not settle.empty:
        big = settle[settle >= SETTLEMENT_FLAG_B]
        dates = [d for d in big.index if idx.min() <= d <= idx.max()]
        if len(dates) >= PHASEMAP_MIN_EVENTS:
            return dates, (
                f"realized auction calendar (coupon-only, settlement days >= "
                f"${SETTLEMENT_FLAG_B:.0f}B, the same parser Weather and Swell share)"
            )
    return _conventional_settlements(idx), (
        "conventional mid-month + month-end settlement grid (no usable auction frame)"
    )


def _rmp_bins(idx: pd.DatetimeIndex) -> np.ndarray:
    anchor = pd.Timestamp(PHASEMAP_RMP_ANCHOR)
    residues = (idx - anchor).days % 14
    return np.array(
        [f"d{_RMP_RESIDUE_TO_DAY[int(r)]:02d}" if int(r) in _RMP_RESIDUE_TO_DAY else "offcycle"
         for r in residues],
        dtype=object,
    )


# ---------------------------------------------------------------------------
# Per-bin statistics (bands mandatory)
# ---------------------------------------------------------------------------

def _fisher_ci(r: float, n_pairs: int) -> list[float]:
    z = math.atanh(max(-0.999999, min(0.999999, r)))
    se = 1.0 / math.sqrt(n_pairs - 3)
    return [round(math.tanh(z - 1.96 * se), 3), round(math.tanh(z + 1.96 * se), 3)]


def _stats(x: np.ndarray, mask: np.ndarray) -> dict:
    """n, mean |shock| (normal-approx CI), exceedance rate (Wilson CI), and
    the phase-resolved lag-1 autocorrelation (Fisher-z CI): each observation
    paired with the next observation in calendar order, the pair attributed
    to the bin of the first."""
    n = int(mask.sum())
    out: dict = {
        "n": n, "mean_abs_bp": None, "mean_abs_ci_bp": None,
        "shock_rate": None, "shock_rate_ci": None,
        "ac1": None, "ac1_ci": None, "n_pairs": 0,
    }
    if n == 0:
        return out
    a = np.abs(x[mask])
    out["mean_abs_bp"] = round(float(a.mean()), 2)
    if n >= 3:
        se = float(a.std(ddof=1)) / math.sqrt(n)
        out["mean_abs_ci_bp"] = [round(float(a.mean()) - 1.96 * se, 2),
                                 round(float(a.mean()) + 1.96 * se, 2)]
    k = int((x[mask] >= MICRO_POP_BP).sum())
    out["shock_rate"] = round(k / n, 3)
    out["shock_rate_ci"] = _wilson(k, n)
    first = mask[:-1]
    xf, xn = x[:-1][first], x[1:][first]
    n_pairs = int(first.sum())
    out["n_pairs"] = n_pairs
    if n_pairs >= PHASEMAP_MIN_PAIRS and float(xf.std()) > 0.0 and float(xn.std()) > 0.0:
        r = float(np.corrcoef(xf, xn)[0, 1])
        out["ac1"] = round(r, 3)
        out["ac1_ci"] = _fisher_ci(r, n_pairs)
    return out


def _fmt(s: dict, what: str) -> str:
    if what == "ac1":
        if s["ac1"] is None:
            return "n/a"
        lo, hi = s["ac1_ci"]
        return f"{s['ac1']} [{lo}, {hi}]"
    if s["shock_rate_ci"] is None:
        return "n/a"
    lo, hi = s["shock_rate_ci"]
    return f"{s['shock_rate']} [{lo}, {hi}]"


def _separated(a: dict, b: dict, key_ci: str, key: str) -> bool:
    return a[key] is not None and b[key] is not None and a[key_ci][0] > b[key_ci][1]


def _elevated(a: dict, b: dict, key: str) -> bool:
    return a[key] is not None and b[key] is not None and a[key] > b[key]


def _grade(event: dict, comparator: dict, assumption: str, hardcoded_by: list[str],
           comparator_name: str) -> dict:
    """CONFIRMS: bands separate (autocorrelation or rate). PARTIALLY
    CONFIRMS: point estimates lean the assumed way but bands overlap.
    EMBARRASSES: no concentration at the assumed dates."""
    sep = _separated(event, comparator, "ac1_ci", "ac1") \
        or _separated(event, comparator, "shock_rate_ci", "shock_rate")
    up = _elevated(event, comparator, "ac1") or _elevated(event, comparator, "shock_rate")
    if sep:
        verdict, gloss = "CONFIRMS", "serial dependence concentrates where the siblings put it, bands separate"
    elif up:
        verdict, gloss = "PARTIALLY CONFIRMS", "point estimates lean the assumed way but the bands overlap, thin-bin territory"
    else:
        verdict, gloss = "EMBARRASSES", "no concentration at the assumed dates, the hard-coded calendar is doing no work here"
    detail = (
        f"{verdict}: {gloss}. Event bins (+/-1bd): lag-1 autocorr {_fmt(event, 'ac1')}, "
        f"shock rate {_fmt(event, 'rate')}, n={event['n']}; {comparator_name}: "
        f"autocorr {_fmt(comparator, 'ac1')}, rate {_fmt(comparator, 'rate')}, n={comparator['n']}"
    )
    return {"assumption": assumption, "hardcoded_by": hardcoded_by,
            "verdict": verdict, "detail": detail}


# ---------------------------------------------------------------------------
# Clocks
# ---------------------------------------------------------------------------

def _event_clock(key: str, label: str, x: np.ndarray, ev_pos: np.ndarray, plain: dict,
                 assumption: str, hardcoded_by: list[str], source: str | None = None) -> dict:
    if ev_pos.size < PHASEMAP_MIN_EVENTS:
        return {"clock": key, "label": label, "ok": False, "vestigial": False,
                "n_events": int(ev_pos.size),
                "reason": f"only {ev_pos.size} events in sample, need >= {PHASEMAP_MIN_EVENTS}"}
    bins = _signed_bins(len(x), ev_pos)
    rows = [{"bin": b, **_stats(x, bins == b)} for b in _bin_order()]
    event = _stats(x, np.isin(bins, _EVENT_BINS))
    out = {
        "clock": key, "label": label, "ok": True, "vestigial": False,
        "n_events": int(ev_pos.size), "bins": rows, "event_pool": event,
        "audit": _grade(event, plain, assumption, hardcoded_by, "plain days"),
    }
    if source is not None:
        out["events_source"] = source
    return out


def _rmp_clock(idx: pd.DatetimeIndex, x: np.ndarray) -> dict:
    """The retired clock, kept as a historical audit. Grading is INVERTED:
    the assumption being tested is that the clock is dead, so flat CONFIRMS,
    residual structure EMBARRASSES. Audited on the post-March-2020 subsample
    only (the era in which the requirement is zero)."""
    bins = _rmp_bins(idx)
    order = [f"d{k:02d}" for k in range(1, 11)]
    rows = [{"bin": b, **_stats(x, bins == b)} for b in order]

    sel = np.asarray(idx >= pd.Timestamp(PHASEMAP_RMP_ZERO_FROM))
    x2, bins2 = x[sel], bins[sel]
    end_wed = _stats(x2, bins2 == "d10")
    rest = _stats(x2, bins2 != "d10")
    sep = _separated(end_wed, rest, "ac1_ci", "ac1") \
        or _separated(end_wed, rest, "shock_rate_ci", "shock_rate")
    up = _elevated(end_wed, rest, "ac1") or _elevated(end_wed, rest, "shock_rate")
    if sep:
        verdict, gloss = "EMBARRASSES", "residual phase structure survives on a clock the era retired"
    elif up:
        verdict, gloss = "PARTIALLY CONFIRMS", "the retirement holds inside the bands, with a mild point-estimate lean on the old settlement Wednesday"
    else:
        verdict, gloss = "CONFIRMS", "the retired clock is flat, as a dead clock should be"
    detail = (
        f"{verdict}: {gloss}. Post-March-2020 subsample; period-end Wednesday (d10): "
        f"lag-1 autocorr {_fmt(end_wed, 'ac1')}, shock rate {_fmt(end_wed, 'rate')}, "
        f"n={end_wed['n']}; other maintenance-period days: autocorr {_fmt(rest, 'ac1')}, "
        f"rate {_fmt(rest, 'rate')}, n={rest['n']}"
    )
    return {
        "clock": "rmp",
        "label": "Reserve maintenance period day (VESTIGIAL: reserve requirements zero since March 2020)",
        "ok": True, "vestigial": True,
        "note": (
            "kept as a historical audit only: reserve requirements went to zero in "
            "March 2020, no engine on this board keys on the maintenance period, and "
            "the live clocks are the month/quarter-end turns, Treasury settlement "
            "days and corporate tax dates"
        ),
        "bins": rows,
        "audit": {
            "assumption": (
                "vestigial clock: reserve requirements have been zero since March 2020 "
                "and the retired maintenance-period clock should show no residual "
                "phase structure"
            ),
            "hardcoded_by": ["nothing on this board (that is the point)"],
            "verdict": verdict, "detail": detail,
        },
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def analyze(spread_bp: pd.Series, auctions: pd.DataFrame | None = None) -> dict:
    """Calendar-phase-resolved map of the daily funding micro-shock. Input:
    SOFR-IORB spread in bp, daily DatetimeIndex; optionally the realized
    auction frame for the settlement clock. Display-only: audits the calendar
    assumptions the siblings hard-code, never scores, never alerts."""
    pop = pop_bp(spread_bp).dropna()
    n_days = int(len(pop))
    if n_days < PHASEMAP_MIN_HISTORY_D:
        return {"ok": False,
                "reason": f"insufficient pop history ({n_days}d < {PHASEMAP_MIN_HISTORY_D}d)"}
    idx = pop.index
    x = pop.to_numpy(dtype=float)

    month_ends, quarter_ends = _turn_events(idx)
    settle_dates, settle_source = _settlement_events(idx, auctions)
    tax_dates = _tax_events(idx)
    ev = {
        "month_end": _positions(idx, month_ends),
        "quarter_end": _positions(idx, quarter_ends),
        "settlement": _positions(idx, settle_dates),
        "tax": _positions(idx, tax_dates),
    }

    # The shared plain comparator: farther than PHASEMAP_NEAR_BD from EVERY
    # live clock's events, so no clock's forcing sits in another's control.
    all_live = np.unique(np.concatenate([p for p in ev.values() if p.size] or
                                        [np.array([], dtype=int)]))
    plain_mask = _signed_bins(n_days, all_live) == "interior"
    plain = _stats(x, plain_mask)

    clocks = [
        _event_clock(
            "month_end", "Month-end turn (non-quarter months)", x, ev["month_end"], plain,
            "calendar forcing lives at the month-end turn (event +/-1bd)",
            ["Resonance month_end mode (slosh window +/-1bd)",
             "Swell/Microseism month_end bucket (turn = last bd + first bd)"],
        ),
        _event_clock(
            "quarter_end", "Quarter-end turn (Mar/Jun/Sep/Dec, year-end included)", x,
            ev["quarter_end"], plain,
            "the quarter-end balance-sheet snapshot is the heaviest forcing date",
            ["Resonance quarter_end mode (weighted heaviest, 0.30)",
             "Swell/Microseism quarter_turn and year_turn buckets"],
        ),
        _event_clock(
            "settlement", "Treasury coupon settlement cycle", x, ev["settlement"], plain,
            "coupon settlement days carry the reserve-drain pile",
            ["Resonance mid_month mode (coupon settlement pile)",
             f"Swell settlement lift on >= ${SETTLEMENT_FLAG_B:.0f}B days",
             "Weather crunch flags on settlement days"],
            source=settle_source,
        ),
        _event_clock(
            "tax", "Corporate tax dates", x, ev["tax"], plain,
            "corporate tax dates pull deposits and force the basin",
            ["Resonance tax_date mode", "Swell/Microseism tax_date bucket"],
        ),
        _rmp_clock(idx, x),
    ]

    live = [c for c in clocks if c["ok"] and not c["vestigial"]]
    verdict_bits = [f"{c['clock']} {c['audit']['verdict']}" for c in live]
    rmp = next(c for c in clocks if c["vestigial"])
    reading = (
        "phase-resolved audit of the board's calendar assumptions: "
        + "; ".join(verdict_bits)
        + ". The live clocks on this board are the month/quarter-end turns, Treasury "
          "settlement days and corporate tax dates; the reserve-maintenance clock is "
          "vestigial (reserve requirements zero since March 2020) and published as a "
          f"historical audit only ({rmp['audit']['verdict']} on the post-2020 subsample)."
    )

    caveats = [
        "bins are thin (roughly 100-200 obs in the SOFR era): read the bands, not the "
        "points; an unbanded phase map invites noise-reading",
        "pairs are consecutive observations in calendar order, attributed to the FIRST "
        "day's bin (the kernel paper's phase-resolved construction); a data gap pairs "
        "across the gap rather than inventing a day",
        "the pop statistic carries a small built-in negative lag-1 autocorrelation on "
        "quiet data (consecutive pops share 4 of 5 trailing-median days): compare bins "
        "to the plain-day comparator, never to zero",
        f"the plain comparator excludes every day within {PHASEMAP_NEAR_BD}bd of any "
        "live clock's events, so one clock's forcing cannot pad another's control group",
        "display-only self-audit: no composite weight, no alert, no score (doctrine); "
        "an EMBARRASSES here changes the siblings only through a pre-registered "
        "methodology change, never automatically",
    ]

    method = (
        f"shock = pop = SOFR-IORB minus its trailing 5bd median (backtest.pop_bp, THE "
        f"shared statistic; exceedance threshold {MICRO_POP_BP:g}bp, Microseism's "
        f"catalog threshold). Per clock: signed bd distance to the nearest event, bins "
        f"-{PHASEMAP_NEAR_BD}..+{PHASEMAP_NEAR_BD} plus interior; per bin: n, mean "
        f"|pop|, exceedance rate with Wilson 95% CI, phase-resolved lag-1 "
        f"autocorrelation with Fisher-z 95% CI (>= {PHASEMAP_MIN_PAIRS} pairs before "
        f"it prints). Settlement events: {settle_source}. Tax dates: config "
        f"CORPORATE_TAX_DAYS ({'/'.join(str(m) for m in _TAX_MONTHS)} month 15, "
        f"rolled to the next bd). Events on data gaps snap to the next observation. "
        f"Audit: event bins (+/-1bd) vs the shared plain comparator; CONFIRMS needs "
        f"band separation, PARTIALLY CONFIRMS a point-estimate lean, EMBARRASSES "
        f"neither. Kernel: arXiv 2607.09426 (Kim and Hansen), intraday phase clock "
        f"replaced by the US funding calendar."
    )

    return {
        "ok": True,
        "asof": idx[-1].date().isoformat(),
        "n_days": n_days,
        "threshold_bp": float(MICRO_POP_BP),
        "plain": plain,
        "clocks": clocks,
        "reading": reading,
        "caveats": caveats,
        "method": method,
    }
