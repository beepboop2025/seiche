"""Swell Forecast — the funding-stress forward curve.

Marine forecasting publishes swell height BY DATE: not "the sea is rough"
but "Thursday brings 8-foot swell." Funding stress deserves the same product,
and the physics cooperates: the basin's forcing schedule is KNOWN IN ADVANCE
— month/quarter/year turns, corporate tax dates, coupon-settlement piles all
sit on the public calendar. Every incumbent tool scores today; nobody
publishes the term structure of funding stress. This engine does:

  for each of the next 42 business days, P(spread pop >= x bp), x in
  {2, 5, 10, 20}, where pop = SOFR−IORB minus its trailing 5bd median —
  the exact statistic whose >= 10bp exceedance is the PROOF/ML/Tide event.

How the probabilities are built (all expanding-window, no fitted model):
  1. Each calendar day belongs to ONE forcing bucket (year/quarter/month
     turn, tax date, mid-month, plain — disjoint, turn buckets include the
     first business day across the boundary). Each bucket keeps the full
     sample of its historical pops; P(pop >= x | bucket) reads off the
     empirical exceedance with a Laplace floor. Small severities lend the
     bucket statistical mass that rare 10bp+ events alone can't provide.
  2. A damping-state lift: exceedances have been more frequent when
     Undertow's damping percentile ran hot; the multiplicative lift is
     estimated pooled (capped, published, disabled below a sample floor).
  3. A coupon-settlement lift on mid-month/plain days that carry >= $90B of
     note/bond settlement (estimated within those buckets only, so the
     calendar isn't double-counted; future settlement dates come from the
     announced auction schedule).

The curve compounds to P(event by horizon) and is VALIDATED the house way:
walk-forward P(event within 5bd) from expanding tables only, AUROC/Brier vs
climatology, reliability table published, verdict self-demotes when the
levels add nothing over the base rate. Reported alongside the index, never
weighted into it (a forecast is not evidence of stress).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    BACKTEST_EVENT_FWD_D,
    BACKTEST_SPIKE_BP,
    CORPORATE_TAX_DAYS,
    SETTLEMENT_FLAG_B,
    SWELL_HORIZON_BD,
    SWELL_LIFT_CAP,
    SWELL_MIN_BUCKET_N,
    SWELL_P_CAP,
    SWELL_SEVERITIES_BP,
    SWELL_STATE_MIN_N,
    SWELL_STATE_PCTL,
    SWELL_WARMUP_D,
)
from seiche.engines import weather as eng_weather
from seiche.engines.backtest import _wilson, pop_bp
from seiche.engines.tidetables import _auroc

BUCKET_LABELS = {
    "year_turn": "Year turn (G-SIB snapshot)",
    "quarter_turn": "Quarter turn (balance-sheet snapshot)",
    "month_end": "Month turn (window dressing)",
    "tax_date": "Corporate tax date",
    "mid_month": "Mid-month (settlement pile)",
    "plain": "Plain day",
}
_SETTLE_BUCKETS = ("mid_month", "plain")  # where the settlement lift applies


_TAX_MONTHS = sorted({m for m, _ in CORPORATE_TAX_DAYS})   # one source of truth
_QTR_MONTHS = (3, 6, 9)


def classify_days(grid: pd.DatetimeIndex) -> pd.Series:
    """One disjoint forcing bucket per business day. Turn buckets span the
    boundary (last bd of the month + first bd of the next); tax/mid-month is
    the first bd on/after the 15th. Vectorized — this runs on every snapshot."""
    m = grid.month.to_numpy()
    dd = grid.day.to_numpy()
    nxt_m = np.roll(m, -1)
    nxt_m[-1] = (grid[-1] + pd.offsets.BDay(1)).month
    prv_m = np.roll(m, 1)
    prv_m[0] = (grid[0] - pd.offsets.BDay(1)).month
    prv_d = np.roll(dd, 1)
    prv_d[0] = (grid[0] - pd.offsets.BDay(1)).day

    last_bd = nxt_m != m           # last business day of month m
    first_bd = prv_m != m          # first business day after a turn

    out = np.full(len(grid), "plain", dtype=object)
    mid = ~last_bd & ~first_bd & (dd >= 15) & (prv_d < 15)
    out[mid] = np.where(np.isin(m[mid], _TAX_MONTHS), "tax_date", "mid_month")
    turn_first = np.where(
        prv_m == 12, "year_turn", np.where(np.isin(prv_m, _QTR_MONTHS), "quarter_turn", "month_end")
    )
    out[first_bd] = turn_first[first_bd]
    turn_last = np.where(
        m == 12, "year_turn", np.where(np.isin(m, _QTR_MONTHS), "quarter_turn", "month_end")
    )
    out[last_bd] = turn_last[last_bd]
    return pd.Series(out, index=grid)


def coupon_settlements(auctions: pd.DataFrame) -> pd.Series:
    """$B of note/bond settlement by issue date — the shared Weather parser,
    coupon-only (bills roll weekly and mostly net out), preferring realized
    total_accepted over the offering amount."""
    return eng_weather.settlement_calendar(
        auctions, exclude_bills=True, amount_cols=("total_accepted", "offering_amt")
    )


class _Tables:
    """Expanding exceedance counters: per (bucket, severity), plus pooled and
    conditional (damping-state, heavy-settlement) channels."""

    def __init__(self) -> None:
        z = lambda: {x: 0 for x in SWELL_SEVERITIES_BP}  # noqa: E731
        self.bucket_hits: dict[str, dict[float, int]] = {b: z() for b in BUCKET_LABELS}
        self.bucket_n: dict[str, int] = {b: 0 for b in BUCKET_LABELS}
        self.all_hits, self.all_n = z(), 0
        self.state_hits, self.state_n = z(), 0
        self.settle_hits, self.settle_n = z(), 0          # heavy-settle mid/plain days
        self.settle_pool_hits, self.settle_pool_n = z(), 0  # all mid/plain days
        # rates are constant between update() calls; the walk-forward loop
        # asks for the same handful per day — memoize per counter version
        self._ver = 0
        self._cache: dict = {}

    def update(self, bucket: str, pop: float, state_hot: bool, settle_heavy: bool) -> None:
        if np.isnan(pop):
            return
        for x in SWELL_SEVERITIES_BP:
            hit = int(pop >= x)
            self.bucket_hits[bucket][x] += hit
            self.all_hits[x] += hit
            if state_hot:
                self.state_hits[x] += hit
            if bucket in _SETTLE_BUCKETS:
                self.settle_pool_hits[x] += hit
                if settle_heavy:
                    self.settle_hits[x] += hit
        self.bucket_n[bucket] += 1
        self.all_n += 1
        if state_hot:
            self.state_n += 1
        if bucket in _SETTLE_BUCKETS:
            self.settle_pool_n += 1
            if settle_heavy:
                self.settle_n += 1
        self._ver += 1
        self._cache.clear()

    # Shrinkage parents: a year turn IS a turn day (2/year can never fill its
    # own bucket — without this, Dec 31, the riskiest known day of the year,
    # would dilute to a plain day). Empirical-Bayes: each bucket's rate is
    # shrunk toward its parent's, weighted by its own n vs SWELL_MIN_BUCKET_N.
    _PARENT = {
        "year_turn": "quarter_turn",
        "quarter_turn": "month_end",
        "tax_date": "mid_month",
    }

    def base_rate(self, bucket: str | None, x: float) -> float:
        if bucket is None:  # root: all days
            return (self.all_hits[x] + 0.5) / (self.all_n + 1.0)
        parent = self.base_rate(self._PARENT.get(bucket), x)
        n, h = self.bucket_n[bucket], self.bucket_hits[bucket][x]
        k = float(SWELL_MIN_BUCKET_N)
        return (h + k * parent) / (n + k)

    def state_lift(self, x: float) -> float:
        if self.state_n < SWELL_STATE_MIN_N or self.all_n < 200:
            return 1.0
        r_state = self.state_hits[x] / self.state_n
        r_all = self.all_hits[x] / max(self.all_n, 1)
        if r_all <= 0:
            return 1.0
        return min(max(r_state / r_all, SWELL_LIFT_CAP[0]), SWELL_LIFT_CAP[1])

    def settle_lift(self, x: float) -> float:
        if self.settle_n < 30 or self.settle_pool_n < 200:
            return 1.0
        r_settle = self.settle_hits[x] / self.settle_n
        r_pool = self.settle_pool_hits[x] / max(self.settle_pool_n, 1)
        if r_pool <= 0:
            return 1.0
        return min(max(r_settle / r_pool, SWELL_LIFT_CAP[0]), SWELL_LIFT_CAP[1])

    def p(self, bucket: str, x: float, state_hot: bool, settle_heavy: bool) -> float:
        key = (bucket, x, state_hot, settle_heavy)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        p = self.base_rate(bucket, x)
        if state_hot:
            p *= self.state_lift(x)
        if settle_heavy and bucket in _SETTLE_BUCKETS:
            p *= self.settle_lift(x)
        p = float(min(p, SWELL_P_CAP))
        self._cache[key] = p
        return p


def analyze(
    spread_bp: pd.Series,
    damping_pctl: pd.Series | None = None,
    auctions: pd.DataFrame | None = None,
    upcoming: pd.DataFrame | None = None,
    horizon: int = SWELL_HORIZON_BD,
) -> dict:
    s = spread_bp.dropna()
    if len(s) < SWELL_WARMUP_D + 100:
        return {"ok": False, "reason": f"insufficient spread history ({len(s)}d)"}

    grid = pd.bdate_range(s.index.min(), s.index.max() + pd.offsets.BDay(horizon))
    today_pos = int(grid.searchsorted(s.index.max()))
    pop = pop_bp(s, grid).to_numpy()   # THE shared event statistic (backtest.pop_bp)

    buckets = classify_days(grid)
    b_arr = buckets.to_numpy()

    if damping_pctl is not None and not damping_pctl.dropna().empty:
        state_hot = (damping_pctl.reindex(grid).ffill(limit=10) >= SWELL_STATE_PCTL).fillna(False).to_numpy()
        state_available = True
    else:
        state_hot = np.zeros(len(grid), dtype=bool)
        state_available = False

    settle_hist = coupon_settlements(auctions if auctions is not None else pd.DataFrame())
    settle_b = settle_hist.reindex(grid).fillna(0.0)
    # future settlement dates come from the announced schedule
    if upcoming is not None and not upcoming.empty:
        up = coupon_settlements(upcoming)
        for d, v in up.items():
            if d in settle_b.index and d > grid[today_pos]:
                settle_b.loc[d] = max(float(settle_b.loc[d]), float(v))
    settle_heavy = (settle_b >= SETTLEMENT_FLAG_B).to_numpy()

    ev_x = BACKTEST_SPIKE_BP
    fwd = BACKTEST_EVENT_FWD_D

    # ---- walk-forward validation: expanding tables only --------------------
    tables = _Tables()
    ps, ys, cs, scored_pos = [], [], [], []
    for i in range(today_pos + 1):
        if i >= SWELL_WARMUP_D and i + fwd <= today_pos:
            fpops = pop[i + 1 : i + 1 + fwd]
            if not np.all(np.isnan(fpops)):
                q = 1.0
                for h in range(1, fwd + 1):
                    q *= 1.0 - tables.p(b_arr[i + h], ev_x, bool(state_hot[i]), bool(settle_heavy[i + h]))
                p5 = 1.0 - q
                clim_day = (tables.all_hits[ev_x] + 0.5) / (tables.all_n + 1.0)
                ps.append(p5)
                ys.append(float(np.nanmax(fpops) >= ev_x))
                cs.append(1.0 - (1.0 - clim_day) ** fwd)
                scored_pos.append(i)
        tables.update(b_arr[i], pop[i], bool(state_hot[i]), bool(settle_heavy[i]))

    validation: dict = {"ok": False, "reason": "insufficient scored history"}
    if len(ps) >= 200:
        pa, ya, ca = np.array(ps), np.array(ys), np.array(cs)
        brier = float(np.mean((pa - ya) ** 2))
        brier_clim = float(np.mean((ca - ya) ** 2))
        auroc = _auroc(ya, pa)   # shared estimator (tidetables) — one AUROC per repo
        reliability = []
        for lo, hi in ((0.0, 0.10), (0.10, 0.20), (0.20, 0.35), (0.35, 1.01)):
            g = (pa >= lo) & (pa < hi)
            if g.sum() >= 5:
                reliability.append({
                    "bin": f"{lo:.2f}-{hi:.2f}",
                    "mean_pred": round(float(pa[g].mean()), 3),
                    "realized": round(float(ya[g].mean()), 3),
                    "n": int(g.sum()),
                    "realized_ci95": _wilson(int(ya[g].sum()), int(g.sum())),
                })
        beats = brier < brier_clim and (auroc or 0.0) > 0.55
        validation = {
            "ok": True,
            "n_scored": len(ps),
            "n_events": int(ya.sum()),
            "auroc": round(auroc, 3) if auroc is not None else None,
            "brier": round(brier, 4),
            "brier_climatology": round(brier_clim, 4),
            "brier_skill": round(1.0 - brier / brier_clim, 3) if brier_clim > 0 else None,
            "reliability": reliability,
            "verdict": (
                "curve beats climatology out-of-sample — the dates AND the levels carry information"
                if beats else
                "curve does NOT beat climatology on levels — trust the calendar shape (which days are "
                "riskier), not the absolute probabilities"
            ),
        }

    # ---- live forward curve: full-history tables ---------------------------
    state_now = bool(state_hot[today_pos])
    curve = []
    surv10 = 1.0
    for j in range(today_pos + 1, min(today_pos + 1 + horizon, len(grid))):
        row: dict = {"date": grid[j].date().isoformat(), "bucket": b_arr[j]}
        prev = 1.0  # per-severity lifts can cross; exceedance must not rise in x
        for x in sorted(SWELL_SEVERITIES_BP):
            pj = min(tables.p(b_arr[j], x, state_now, bool(settle_heavy[j])), prev)
            prev = pj
            row[f"p{x:g}"] = round(pj, 3)
            if x == BACKTEST_SPIKE_BP:
                surv10 *= 1.0 - pj
        row["cum10"] = round(1.0 - surv10, 3)
        if settle_b.iloc[j] > 0:
            row["settle_b"] = round(float(settle_b.iloc[j]), 1)
        curve.append(row)

    # Horizons read off the SAME clamped curve the tab shows — one code path
    # for one quantity (the alert p and the displayed cum10 must never differ).
    horizons = {
        f"h{h}": (curve[min(h, len(curve)) - 1]["cum10"] if curve else None)
        for h in (5, 10, 21, horizon)
    }

    peak = max(curve, key=lambda r: r["p10"]) if curve else None

    bucket_rows = []
    for b in BUCKET_LABELS:
        n = tables.bucket_n[b]
        h10 = tables.bucket_hits[b][ev_x]
        bucket_rows.append({
            "bucket": b,
            "label": BUCKET_LABELS[b],
            "n": n,
            **{f"p{x:g}": round(tables.base_rate(b, x), 3) for x in SWELL_SEVERITIES_BP},
            "ci95_10bp": _wilson(h10, n) if n else None,
            "low_n": n < SWELL_MIN_BUCKET_N,
        })

    upcoming_settle = [
        {"date": grid[j].date().isoformat(), "amount_b": round(float(settle_b.iloc[j]), 1)}
        for j in range(today_pos + 1, min(today_pos + 1 + horizon, len(grid)))
        if settle_b.iloc[j] >= 20.0
    ]

    return {
        "ok": True,
        "asof": s.index.max().date().isoformat(),
        "horizon_bd": horizon,
        # walk-forward p5 per scored day — test hook for the no-look-ahead
        # invariant (value at T must not change when future data arrives);
        # popped before serialization like tidetables' _hindcast.
        "_p5_series": pd.Series(ps, index=grid[scored_pos]) if scored_pos else pd.Series(dtype=float),
        "curve": curve,
        "event_by_horizon": horizons,   # P(pop >= 10bp within h bd)
        "p_event_5bd": horizons.get("h5"),
        "peak": peak,
        "buckets": bucket_rows,
        "state": {
            "available": state_available,
            "hot": state_now,
            "pctl_threshold": SWELL_STATE_PCTL,
            "lift_10bp": round(tables.state_lift(ev_x), 2),
            "n_hot_days": tables.state_n,
        },
        "settlement": {
            "lift_10bp": round(tables.settle_lift(ev_x), 2),
            "n_heavy_days": tables.settle_n,
            "flag_b": SETTLEMENT_FLAG_B,
            "upcoming": upcoming_settle,
        },
        "validation": validation,
        "caveats": [
            "forward days condition on TODAY'S damping state held constant — state drift over the horizon is not modeled (stated, not hidden)",
            "multi-week horizons compound daily rates as if pop-days were independent; pops CLUSTER, so h≥10 cumulative numbers are upper bounds — only the 5bd integral is walk-forward validated",
            "year turns see ~2 obs/year by construction — their rates borrow strength from quarter-turn evidence via shrinkage (low-n flagged), Wilson CIs printed on the raw counts",
            "check the reliability table before reading any probability as literal odds; the verdict self-demotes on levels",
            "replay note: the announced-settlement overlay has no historical vintage — Time Machine replays run calendar-only",
        ],
        "method": (
            f"pop = SOFR−IORB − trailing 5bd median (the PROOF event statistic; event = pop ≥ "
            f"{ev_x:g}bp). Each bd belongs to one forcing bucket; P(pop ≥ x | bucket) = expanding "
            f"empirical exceedance with empirical-Bayes shrinkage toward the parent bucket "
            f"(year→quarter→month turn, tax→mid-month, all→pooled; prior weight {SWELL_MIN_BUCKET_N}) "
            f"— a 2-obs-per-year bucket borrows strength instead of diluting to a plain day. "
            f"Severity monotonicity enforced after lifts. "
            f"Damping-state lift (Undertow pctl ≥ {SWELL_STATE_PCTL:g}) and coupon-settlement lift "
            f"(≥ ${SETTLEMENT_FLAG_B:g}B on mid-month/plain days) estimated expanding, capped "
            f"{SWELL_LIFT_CAP}. Curve compounds day probabilities; validation = walk-forward "
            f"P(event within {fwd}bd) vs climatology (AUROC/Brier/reliability)"
        ),
    }
