"""The Book — the signal made accountable.

Every engine upstream of this file produces warnings. This one produces
POSITIONS — the only output that can be wrong in a way that costs something,
which is exactly why it exists: a forecaster that never books a P&L is a
pundit. The Book maps the ensemble state to explicit daily weights on a small
liquid universe, runs the walk-forward P&L with costs, and publishes the
verdict either way.

What it deliberately is NOT: an execution system. Free daily data buys
close-to-close paper P&L on proxy returns — the duration sleeves are
constant-maturity par-yield approximations (carry − D·Δy + ½C·Δy²), not
tradeable futures; BTC folds weekends into Monday; costs are fixed haircuts.
All of that is printed on the page. The claim being tested is narrow and
falsifiable: does the plumbing signal, mapped through a FROZEN rulebook,
survive contact with returns it has never seen?

Honesty architecture:
  - the only fitted object anywhere is the stacker's calibration (itself
    walk-forward); the stance map, thresholds and sizing are fixed config —
    a rulebook, not a model, because ~6 episodes cannot discipline more;
  - signal at t earns returns at t+1 (BOOK_SIGNAL_LAG_BD), enforced in ONE
    place (pnl) and unit-tested;
  - hysteresis bands + a dispersion gate control turnover, and when the
    fleet disagrees the honest position is smaller, not braver;
  - every benchmark runs through the IDENTICAL pipeline (same sizing, same
    lag, same costs): all-cash, a static mix, and each member driving the
    same rulebook alone;
  - Sharpe ships with a stationary-block-bootstrap CI (persistent positions
    make naive Sharpe errors a lie), plus a doubled-cost rerun flag;
  - the live section only ever reads the as-published pit record — the
    backtest cannot touch it, and the hash chain makes it tamper-evident.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    BOOK_BENCH_STATIC,
    BOOK_BOOT_BLOCK_D,
    BOOK_BOOT_N,
    BOOK_DISPERSION_GATE,
    BOOK_LIVE_MIN_D,
    BOOK_MAX_GROSS,
    BOOK_MAX_WEIGHT,
    BOOK_P_ENTER_RISKOFF,
    BOOK_P_EXIT_RISKOFF,
    BOOK_P_RISKON,
    BOOK_SIGNAL_LAG_BD,
    BOOK_SLEEVES,
    BOOK_STANCE_MAP,
    BOOK_TELL_RISKON,
    BOOK_VOL_LOOKBACK_D,
    BOOK_VOL_MIN_PERIODS,
    BOOK_VOL_TARGET_ANN,
    EPISODES,
)

SLEEVES = list(BOOK_SLEEVES)

DURATION_NOTE = (
    "duration sleeves are PAPER PROXIES: r ≈ y/252 − D·Δy + ½C·Δy² on constant-maturity "
    "par yields (D/C fixed in config), not tradeable futures — no roll-down, no CTD "
    "switches, no financing spread; validated for sign and rough magnitude only"
)


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------

def build_returns(
    dgs2: pd.Series, dgs10: pd.Series, sp500: pd.Series, btc: pd.Series, tb3m: pd.Series
) -> pd.DataFrame:
    """Daily sleeve returns + cash on a business-day grid. Trailing-only."""
    anchor = dgs10.dropna()
    if anchor.empty:
        return pd.DataFrame()
    idx = pd.bdate_range(anchor.index.min(), anchor.index.max())

    def dur_ret(y: pd.Series, mod_dur: float, convexity: float) -> pd.Series:
        yy = y.reindex(idx).ffill(limit=5)
        dy = yy.diff() / 100.0
        carry = yy.shift(1) / 100.0 / 252.0
        return carry - mod_dur * dy + 0.5 * convexity * dy ** 2

    out = pd.DataFrame(index=idx)
    out["ust2y"] = dur_ret(dgs2, BOOK_SLEEVES["ust2y"]["mod_dur"], BOOK_SLEEVES["ust2y"]["convexity"])
    out["ust10y"] = dur_ret(dgs10, BOOK_SLEEVES["ust10y"]["mod_dur"], BOOK_SLEEVES["ust10y"]["convexity"])
    out["spx"] = sp500.reindex(idx).ffill(limit=5).pct_change()
    # BTC trades 24/7; on a bday grid the weekend folds into Monday (stated).
    out["btc"] = btc.reindex(idx).ffill(limit=3).pct_change()
    out["cash"] = tb3m.reindex(idx).ffill(limit=7).shift(1) / 100.0 / 252.0
    return out


# ---------------------------------------------------------------------------
# Stance and sizing (the frozen rulebook)
# ---------------------------------------------------------------------------

def stance_series(p: pd.Series, dispersion: pd.Series, tell: pd.Series) -> pd.Series:
    """Hysteresis state machine + disagreement gate. Iterative by design —
    the state at t depends only on values at ≤ t."""
    idx = p.dropna().index
    d = dispersion.reindex(idx)
    t = tell.reindex(idx).ffill(limit=5)
    state = "neutral"
    out = []
    for day in idx:
        pv = float(p.loc[day])
        if state == "risk_off":
            if pv < BOOK_P_EXIT_RISKOFF:
                state = "neutral"
        elif state == "risk_on":
            tv = t.loc[day]
            if pv > 2 * BOOK_P_RISKON or pd.isna(tv) or tv > 0:
                state = "neutral"
        if state == "neutral":
            tv = t.loc[day]
            if pv >= BOOK_P_ENTER_RISKOFF:
                state = "risk_off"
            elif pv <= BOOK_P_RISKON and pd.notna(tv) and tv <= BOOK_TELL_RISKON:
                state = "risk_on"
        dv = d.loc[day]
        gated = "neutral" if (pd.notna(dv) and float(dv) > BOOK_DISPERSION_GATE) else state
        out.append(gated)
    return pd.Series(out, index=idx)


def size_positions(stance: pd.Series, returns: pd.DataFrame) -> pd.DataFrame:
    """Directions from the stance map, vol-targeted per sleeve, capped."""
    daily_target = BOOK_VOL_TARGET_ANN / np.sqrt(252.0)
    w = pd.DataFrame(0.0, index=stance.index, columns=SLEEVES)
    for s in SLEEVES:
        vol = returns[s].rolling(BOOK_VOL_LOOKBACK_D, min_periods=BOOK_VOL_MIN_PERIODS).std()
        scale = (daily_target / vol).clip(upper=BOOK_MAX_WEIGHT).reindex(stance.index)
        dirs = stance.map(lambda st: BOOK_STANCE_MAP[st][s])
        w[s] = (dirs * scale).fillna(0.0)
    gross = w.abs().sum(axis=1)
    over = gross > BOOK_MAX_GROSS
    if over.any():
        w.loc[over] = w.loc[over].div(gross[over], axis=0) * BOOK_MAX_GROSS
    return w


# ---------------------------------------------------------------------------
# P&L
# ---------------------------------------------------------------------------

def pnl(weights: pd.DataFrame, returns: pd.DataFrame, tcost_bp: dict[str, float] | None = None) -> dict:
    """THE lag lives here, once: weights formed at t earn returns at t+1.
    Positions are financed at cash: r_book = cash + Σ w·(r − cash) − costs."""
    costs_bp = tcost_bp or {s: BOOK_SLEEVES[s]["tcost_bp"] for s in SLEEVES}
    idx = returns.index
    w = weights.reindex(idx).fillna(0.0)
    w_used = w.shift(BOOK_SIGNAL_LAG_BD).fillna(0.0)
    cash = returns["cash"].fillna(0.0)
    excess = returns[SLEEVES].fillna(0.0).sub(cash, axis=0)
    gross = cash + (w_used * excess).sum(axis=1)
    turnover = (w_used - w_used.shift(1).fillna(0.0)).abs()
    costs = sum(turnover[s] * costs_bp[s] / 1e4 for s in SLEEVES)
    net = gross - costs
    return {
        "net": net,
        "gross": gross,
        "costs": costs,
        "turnover_ann": float(turnover.sum(axis=1).mean() * 252.0),
        "cost_drag_bp_ann": float(costs.mean() * 252.0 * 1e4),
        "w_used": w_used,
    }


def _max_drawdown(net: pd.Series) -> float:
    eq = (1.0 + net.fillna(0.0)).cumprod()
    return float((eq / eq.cummax() - 1.0).min())


def sharpe_ci(excess: pd.Series, block: int = BOOK_BOOT_BLOCK_D, n: int = BOOK_BOOT_N,
              seed: int = 7) -> dict:
    """Stationary block bootstrap (Politis–Romano) CI + Newey–West t-stat.
    Daily book returns are serially dependent; naive Sharpe SEs flatter."""
    x = excess.dropna().to_numpy()
    if len(x) < 100:
        return {"sharpe": None, "ci95": None, "nw_tstat": None}
    sharpe = float(np.mean(x) / np.std(x) * np.sqrt(252.0)) if np.std(x) > 0 else 0.0
    rng = np.random.default_rng(seed)
    N = len(x)
    boots = np.empty(n)
    for b in range(n):
        out = np.empty(N)
        i = 0
        while i < N:
            start = rng.integers(0, N)
            length = min(int(rng.geometric(1.0 / block)), N - i)
            take = np.arange(start, start + length) % N
            out[i : i + length] = x[take]
            i += length
        sd = np.std(out)
        boots[b] = np.mean(out) / sd * np.sqrt(252.0) if sd > 0 else 0.0
    lo, hi = np.percentile(boots, [2.5, 97.5])
    # Newey–West t-stat of the mean daily excess return
    mu = np.mean(x)
    xc = x - mu
    gamma0 = float(np.mean(xc * xc))
    var = gamma0
    for lag in range(1, block + 1):
        cov = float(np.mean(xc[lag:] * xc[:-lag]))
        var += 2.0 * (1.0 - lag / (block + 1.0)) * cov
    se = np.sqrt(max(var, 1e-18) / N)
    return {
        "sharpe": round(sharpe, 2),
        "ci95": [round(float(lo), 2), round(float(hi), 2)],
        "nw_tstat": round(float(mu / se), 2) if se > 0 else None,
    }


def _metrics(net: pd.Series, cash: pd.Series, with_ci: bool = False) -> dict:
    net = net.dropna()
    if net.empty:
        return {}
    excess = (net - cash.reindex(net.index).fillna(0.0)).dropna()
    monthly = (1.0 + net).groupby(net.index.to_period("M")).prod() - 1.0
    out = {
        "ann_return_pct": round(float((1.0 + net).prod() ** (252.0 / len(net)) - 1.0) * 100.0, 2),
        "ann_vol_pct": round(float(net.std() * np.sqrt(252.0)) * 100.0, 2),
        "max_dd_pct": round(_max_drawdown(net) * 100.0, 2),
        "hit_rate_daily": round(float((net > 0).mean()), 3),
        "hit_rate_monthly": round(float((monthly > 0).mean()), 3) if len(monthly) >= 6 else None,
        "n_days": int(len(net)),
    }
    sc = sharpe_ci(excess) if with_ci else {
        "sharpe": round(float(excess.mean() / excess.std() * np.sqrt(252.0)), 2)
        if excess.std() > 0 else 0.0,
        "ci95": None, "nw_tstat": None,
    }
    out.update(sc)
    return out


def episode_attribution(net: pd.Series, bench: pd.Series) -> list[dict]:
    rows = []
    for ep_date, label in EPISODES.items():
        ts = pd.Timestamp(ep_date)
        if net.empty or ts < net.index[0] or ts > net.index[-1]:
            rows.append({"episode": label, "date": ep_date, "in_sample": False})
            continue
        loc = net.index.searchsorted(ts)
        w0, w1 = max(loc - 30, 0), min(loc + 10, len(net) - 1)
        seg, bseg = net.iloc[w0 : w1 + 1], bench.reindex(net.index).iloc[w0 : w1 + 1]
        rows.append({
            "episode": label,
            "date": ep_date,
            "in_sample": True,
            "book_pct": round(float((1 + seg.fillna(0)).prod() - 1) * 100.0, 2),
            "static_pct": round(float((1 + bseg.fillna(0)).prod() - 1) * 100.0, 2),
        })
    return rows


# ---------------------------------------------------------------------------
# Benchmarks — every one through the identical pipeline
# ---------------------------------------------------------------------------

def _static_mix_weights(returns: pd.DataFrame) -> pd.DataFrame:
    """BOOK_BENCH_STATIC, monthly-rebalanced (weights constant within month —
    drift ignored, stated; costs still charged on the monthly reset)."""
    w = pd.DataFrame(0.0, index=returns.index, columns=SLEEVES)
    for s, v in BOOK_BENCH_STATIC.items():
        w[s] = v
    return w


def _member_book(p_member: pd.Series, dispersion_zero: pd.Series, tell: pd.Series,
                 returns: pd.DataFrame) -> pd.Series:
    st = stance_series(p_member, dispersion_zero, tell)
    w = size_positions(st, returns)
    return pnl(w, returns)["net"]


# ---------------------------------------------------------------------------
# Live track record — reads ONLY the as-published pit records
# ---------------------------------------------------------------------------

def live_track(pit_records: list[dict], returns: pd.DataFrame) -> dict:
    rows = []
    for r in pit_records:
        bk = r.get("book")
        if not bk or not r.get("date"):
            continue
        w = {s: 0.0 for s in SLEEVES}
        for sleeve, weight in bk.get("positions", []):
            if sleeve in w and weight is not None:
                w[sleeve] = float(weight)
        rows.append({"date": r["date"], **w})
    if not rows:
        return {"n_days": 0, "note": "live record starts accruing with the first published book"}
    W = pd.DataFrame(rows).drop_duplicates("date", keep="last")
    W = W.set_index(pd.DatetimeIndex(W.pop("date"))).sort_index()
    res = pnl(W, returns.reindex(returns.index.union(W.index)).sort_index())
    net = res["net"].loc[W.index.min():]
    net = net[net.index > W.index.min()]  # first day has no lagged position yet
    out = {
        "n_days": int(len(net)),
        "since": W.index.min().date().isoformat(),
        "cum_return_pct": round(float((1 + net.fillna(0)).prod() - 1) * 100.0, 2),
        "note": (
            "as-published positions only (pit record + hash chain) — this number is "
            "immune to backtest criticism and is the one that matters"
        ),
    }
    if len(net) >= BOOK_LIVE_MIN_D:
        out.update(sharpe_ci((net - returns["cash"].reindex(net.index).fillna(0.0)).dropna()))
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    p: pd.Series,                    # published ensemble probability (stacker)
    member_probs: pd.DataFrame,      # calibrated member probs (for member books)
    dispersion: pd.Series,
    tell: pd.Series,
    returns: pd.DataFrame,
    pit_records: list[dict] | None = None,
) -> dict:
    if returns.empty or p.dropna().empty:
        return {"ok": False, "reason": "no returns or no ensemble signal"}

    stance = stance_series(p, dispersion, tell)
    weights = size_positions(stance, returns)
    res = pnl(weights, returns)
    net, cash = res["net"].reindex(stance.index).dropna(), returns["cash"]
    if len(net) < 300:
        return {"ok": False, "reason": f"insufficient overlap ({len(net)}d)"}

    strat = _metrics(net, cash, with_ci=True)
    strat["turnover_ann"] = round(res["turnover_ann"], 2)
    strat["cost_drag_bp_ann"] = round(res["cost_drag_bp_ann"], 1)
    in_market = weights.abs().sum(axis=1) > 0
    strat["time_in_market_pct"] = round(float(in_market.mean() * 100.0), 0)

    # Benchmarks through the identical pipeline.
    zero_disp = pd.Series(0.0, index=p.index)
    static_net = pnl(_static_mix_weights(returns), returns)["net"].reindex(net.index)
    cash_net = cash.reindex(net.index).fillna(0.0)
    benchmarks = {
        "all_cash": _metrics(cash_net, cash),
        "static_mix": _metrics(static_net.dropna(), cash),
    }
    for m in member_probs.columns:
        pm = member_probs[m].dropna()
        if len(pm) >= 300:
            mnet = _member_book(pm, zero_disp, tell, returns).reindex(net.index).dropna()
            if len(mnet) >= 300:
                benchmarks[f"{m}_only"] = _metrics(mnet, cash)

    # Doubled-cost robustness rerun.
    res2 = pnl(weights, returns, {s: 2.0 * BOOK_SLEEVES[s]["tcost_bp"] for s in SLEEVES})
    m2 = _metrics(res2["net"].reindex(stance.index).dropna(), cash)
    static_sharpe = benchmarks["static_mix"].get("sharpe")
    robust_2x = (
        m2.get("sharpe") is not None and static_sharpe is not None
        and m2["sharpe"] > static_sharpe
    )

    beats_static = (
        strat.get("sharpe") is not None and static_sharpe is not None
        and strat["sharpe"] > static_sharpe
    )
    beats_cash = strat.get("ann_return_pct", 0) > benchmarks["all_cash"].get("ann_return_pct", 0)
    ci = strat.get("ci95") or ["?", "?"]
    verdict = (
        f"the Book {'BEATS' if beats_static else 'does NOT beat'} the static mix after costs "
        f"(net Sharpe {strat.get('sharpe')} CI [{ci[0]}, {ci[1]}] vs {static_sharpe}); "
        f"{'beats' if beats_cash else 'does NOT beat'} all-cash "
        f"({strat.get('ann_return_pct')}% vs {benchmarks['all_cash'].get('ann_return_pct')}%/yr); "
        f"{'survives' if robust_2x else 'does NOT survive'} doubled transaction costs"
    )

    # Today's book.
    today_idx = stance.index[-1]
    positions = []
    for s in SLEEVES:
        wv = float(weights[s].iloc[-1])
        vol = returns[s].rolling(BOOK_VOL_LOOKBACK_D, min_periods=BOOK_VOL_MIN_PERIODS).std()
        positions.append({
            "sleeve": s,
            "label": BOOK_SLEEVES[s]["label"],
            "weight": round(wv, 3),
            "direction": "long" if wv > 0 else "short" if wv < 0 else "flat",
            "vol_ann_pct": round(float(vol.iloc[-1] * np.sqrt(252) * 100.0), 1)
            if pd.notna(vol.iloc[-1]) else None,
            "tcost_bp": BOOK_SLEEVES[s]["tcost_bp"],
        })
    p_now = float(p.dropna().iloc[-1])
    d_now = dispersion.reindex(stance.index).iloc[-1]
    tell_now = tell.dropna().iloc[-1] if not tell.dropna().empty else None
    gate_on = pd.notna(d_now) and float(d_now) > BOOK_DISPERSION_GATE
    rationale = (
        f"P(event,5bd)={p_now:.2f} vs enter≥{BOOK_P_ENTER_RISKOFF}/exit<{BOOK_P_EXIT_RISKOFF}; "
        f"fleet dispersion {d_now:.2f}" + (" — GATE ON, forced neutral" if gate_on else "")
        + (f"; Tell {tell_now:+.0f}" if tell_now is not None else "")
    ) if pd.notna(d_now) else f"P(event,5bd)={p_now:.2f}"
    prior_stance = str(stance.iloc[-2]) if len(stance) > 1 else None

    eq = (1.0 + net.fillna(0)).cumprod()
    eq_static = (1.0 + static_net.reindex(net.index).fillna(0)).cumprod()
    eq_cash = (1.0 + cash_net).cumprod()

    return {
        "ok": True,
        "asof": today_idx.date().isoformat(),
        "today": {
            "stance": str(stance.iloc[-1]),
            "prior_stance": prior_stance,
            "changed_vs_prior": prior_stance is not None and str(stance.iloc[-1]) != prior_stance,
            "p_ensemble": round(p_now, 3),
            "dispersion": round(float(d_now), 3) if pd.notna(d_now) else None,
            "dispersion_gate_on": bool(gate_on),
            "tell": round(float(tell_now), 1) if tell_now is not None else None,
            "positions": positions,
            "rationale": rationale,
        },
        "backtest": {
            "sample": {
                "start": net.index[0].date().isoformat(),
                "end": net.index[-1].date().isoformat(),
                "n_days": int(len(net)),
                "n_stance_runs": int((stance != stance.shift(1)).sum()),
            },
            **strat,
            "robust_to_2x_costs": bool(robust_2x),
            "benchmarks": benchmarks,
            "episodes": episode_attribution(net, static_net),
            "equity": [
                [d.date().isoformat(), round(float(eq.loc[d]), 4),
                 round(float(eq_static.loc[d]), 4) if d in eq_static.index and pd.notna(eq_static.loc[d]) else None,
                 round(float(eq_cash.loc[d]), 4) if d in eq_cash.index else None]
                for d in net.index[::3]
            ],
            "verdict": verdict,
        },
        "live": live_track(pit_records or [], returns),
        "duration_note": DURATION_NOTE,
        "caveats": [
            "PAPER P&L on proxy returns — see duration_note; no borrow/financing beyond the cash leg; close-to-close only",
            "the stance map was frozen knowing the history this backtest reuses — the live hash-chained record is the only arbiter free of that critique",
            "persistent positions: Sharpe CI is stationary-block-bootstrap (21bd) and the Newey–West t-stat is printed; n_stance_runs is the independent-ish n",
            "BTC weekend moves fold into Monday on the business-day grid",
            "not investment advice",
        ],
        "method": (
            f"stance = hysteresis on ensemble P (enter {BOOK_P_ENTER_RISKOFF}/exit {BOOK_P_EXIT_RISKOFF}; "
            f"risk_on P≤{BOOK_P_RISKON} & Tell≤{BOOK_TELL_RISKON}) + dispersion gate {BOOK_DISPERSION_GATE}; "
            f"per-sleeve vol targeting {BOOK_VOL_TARGET_ANN:.0%} ann (lookback {BOOK_VOL_LOOKBACK_D}d), "
            f"|w|≤{BOOK_MAX_WEIGHT}, gross≤{BOOK_MAX_GROSS}; signal t → returns t+{BOOK_SIGNAL_LAG_BD}; "
            "costs per config; benchmarks through the identical pipeline"
        ),
    }
