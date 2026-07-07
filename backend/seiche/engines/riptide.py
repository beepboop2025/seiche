"""Riptide — the pop prognosis: chop or current?

The morning the spread pops is the only morning the whole desk asks the same
question, and no tool answers it: is this a one-day slosh (chop) or the
start of a squeeze (a current that will carry you out)? Three discriminators
make the question answerable at the pop-day close:

  1. the RRP co-sign: a benign quarter-end pop co-occurs with the mechanical
     ON-RRP sawtooth (dealers refuse balance sheet, MMF cash parks at the
     Fed). The malign 2025 pops printed with RRP ~$0 — a pop WITHOUT its
     expected co-move is genuine cash scarcity, not regulatory choreography;
  2. the calendar: a pop ON a turn date is scheduled forcing; a pop on a
     plain Tuesday is news;
  3. the basin state: pops arriving while Undertow reads hot (damping
     already thinning) re-anchor instead of mean-reverting — the diagnostic
     transition of the scarcity ladder.

Unit of analysis = the POP, not the day (declustered ≥ RIPTIDE_POP_BP via
the shared PROOF statistic — ~independent trials). Two targets per pop, from
data strictly after it: STICKY (the spread has not given back half the pop
after RIPTIDE_STICKY_MIN_BD) and ESCALATES (a full ≥10bp PROOF event lands
within RIPTIDE_ESCALATE_BD). Tiny walk-forward logistic (4 features against
a few hundred pops), validated pop-by-pop against the base rate; verdicts
self-demote. The engine SPEAKS only when there is a live pop — exactly when
the operator is staring at the screen — and otherwise shows flat water plus
the receipts of the last pops.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    BACKTEST_SPIKE_BP,
    RIPTIDE_ESCALATE_BD,
    RIPTIDE_LIVE_BD,
    RIPTIDE_MIN_POPS,
    RIPTIDE_POP_BP,
    RIPTIDE_STICKY_MIN_BD,
    RIPTIDE_WINDOW_BD,
)
from seiche.engines.backtest import _wilson, pop_bp
from seiche.engines.swell import classify_days
from seiche.engines.tidetables import _auroc

_TURN_BUCKETS = ("year_turn", "quarter_turn", "month_end")
FEATURES = ["pop_bp", "rrp_co_z", "is_turn", "damping_pctl"]


def extract_pops(
    spread_bp: pd.Series,
    rrp_b: pd.Series,
    damping_pctl: pd.Series | None,
) -> pd.DataFrame:
    """One row per declustered pop with features as-of the pop-day close and
    targets from strictly-after data (NaN while a window is still open)."""
    s = spread_bp.dropna()
    if s.empty:
        return pd.DataFrame()
    grid = pd.bdate_range(s.index.min(), s.index.max())
    sp = s.reindex(grid)
    pop = pop_bp(s, grid)
    buckets = classify_days(grid).to_numpy()

    rrp = rrp_b.dropna().reindex(grid).ffill(limit=5)
    rrp_chg = rrp.diff()
    # expanding robust z of the RRP daily change — is today's move unusual?
    med = rrp_chg.expanding(120).median().shift(1)
    mad = (rrp_chg - med).abs().expanding(120).median().shift(1) * 1.4826
    rrp_co_z = ((rrp_chg - med) / mad.replace(0.0, np.nan)).fillna(0.0)

    damp = (
        damping_pctl.reindex(grid).ffill(limit=10)
        if damping_pctl is not None and not damping_pctl.dropna().empty
        else pd.Series(np.nan, index=grid)
    )

    vals = pop.to_numpy()
    spv = sp.to_numpy()
    rows = []
    last_kept: pd.Timestamp | None = None
    for i in np.where(vals >= RIPTIDE_POP_BP)[0]:
        d = grid[i]
        if last_kept is not None and (d - last_kept).days <= 7:
            continue  # same episode — one trial (backtest's decluster rule)
        last_kept = d

        baseline = float(np.nanmedian(spv[max(i - 5, 0):i])) if i > 0 else np.nan
        peak = spv[i]
        half_level = baseline + (peak - baseline) / 2.0 if np.isfinite(baseline) else np.nan

        # STICKY: still above half-give-back after RIPTIDE_STICKY_MIN_BD
        sticky: float | None = np.nan
        if np.isfinite(half_level):
            horizon = min(RIPTIDE_WINDOW_BD, len(grid) - 1 - i)
            gave_back_at = None
            for k in range(1, horizon + 1):
                v = spv[i + k]
                if np.isfinite(v) and v <= half_level:
                    gave_back_at = k
                    break
            if gave_back_at is not None:
                sticky = float(gave_back_at >= RIPTIDE_STICKY_MIN_BD)
            elif horizon >= RIPTIDE_WINDOW_BD:
                sticky = 1.0  # never gave it back inside the full window
            # else: window still open at sample end -> NaN (no verdict yet)

        # ESCALATES: a full PROOF event strictly after the pop, within horizon
        esc: float | None = np.nan
        if i + RIPTIDE_ESCALATE_BD < len(grid):
            fwd = vals[i + 1 : i + 1 + RIPTIDE_ESCALATE_BD]
            if not np.all(np.isnan(fwd)):
                esc = float(np.nanmax(fwd) >= BACKTEST_SPIKE_BP)

        rows.append({
            "date": d,
            "pop_bp": round(float(vals[i]), 1),
            "rrp_co_z": round(float(rrp_co_z.iloc[i]), 2),
            "is_turn": float(buckets[i] in _TURN_BUCKETS),
            "bucket": buckets[i],
            "damping_pctl": round(float(damp.iloc[i]), 1) if np.isfinite(damp.iloc[i]) else np.nan,
            "sticky": sticky,
            "escalates": esc,
        })
    return pd.DataFrame(rows)


def _walk_forward_probs(P: pd.DataFrame, target: str) -> pd.Series:
    """Expanding logistic across pops in time order; damping NaNs imputed
    with the expanding mean of PRIOR pops only (no look-ahead)."""
    from sklearn.linear_model import LogisticRegression

    probs = pd.Series(np.nan, index=P.index)
    y_all = P[target]
    for i in range(RIPTIDE_MIN_POPS, len(P)):
        train = P.iloc[:i]
        yt = y_all.iloc[:i]
        ok = yt.notna()
        if ok.sum() < RIPTIDE_MIN_POPS or yt[ok].nunique() < 2:
            continue
        # a feature with no data yet (damping starts late / absent) drops for
        # this fit and re-enters once it has history — same rule as ML Lab
        cols = [c for c in FEATURES if train.loc[ok, c].notna().any()]
        if not cols:
            continue
        fill = train.loc[ok, cols].mean(numeric_only=True)
        Xt = train.loc[ok, cols].fillna(fill)
        xq = P.iloc[[i]][cols].fillna(fill)
        try:
            m = LogisticRegression(C=1.0, max_iter=500)
            m.fit(Xt, yt[ok])
            probs.iloc[i] = float(m.predict_proba(xq)[0, 1])
        except ValueError:
            continue
    return probs


def analyze(
    spread_bp: pd.Series,
    rrp_b: pd.Series,
    damping_pctl: pd.Series | None = None,
) -> dict:
    P = extract_pops(spread_bp, rrp_b, damping_pctl)
    if len(P) < RIPTIDE_MIN_POPS + 10:
        return {"ok": False, "reason": f"too few pops to learn the grammar ({len(P)})"}

    validation: dict = {}
    probs: dict[str, pd.Series] = {}
    for target in ("sticky", "escalates"):
        p = _walk_forward_probs(P, target)
        probs[target] = p
        scored = pd.concat({"p": p, "y": P[target]}, axis=1).dropna()
        if len(scored) >= 30:
            base = float(scored["y"].mean())
            brier = float(np.mean((scored["p"] - scored["y"]) ** 2))
            brier_base = float(np.mean((base - scored["y"]) ** 2))
            validation[target] = {
                "n_scored": int(len(scored)),
                "base_rate": round(base, 3),
                "auroc": (lambda a: round(a, 3) if a is not None else None)(
                    _auroc(scored["y"].to_numpy(), scored["p"].to_numpy())),
                "brier": round(brier, 4),
                "brier_base": round(brier_base, 4),
                "skill": round(1.0 - brier / brier_base, 3) if brier_base > 0 else None,
            }
        else:
            validation[target] = {"n_scored": int(len(scored)), "verdict": "too few resolved pops"}

    # --- the live question -------------------------------------------------
    s = spread_bp.dropna()
    last_pop = P.iloc[-1]
    age_bd = int(np.busday_count(last_pop["date"].date(), s.index[-1].date()))
    live = age_bd <= RIPTIDE_LIVE_BD
    live_block = None
    if live:
        # predict with everything known (final walk-forward style: all closed pops)
        p_sticky = float(probs["sticky"].iloc[-1]) if np.isfinite(probs["sticky"].iloc[-1]) else None
        p_esc = float(probs["escalates"].iloc[-1]) if np.isfinite(probs["escalates"].iloc[-1]) else None
        verdict = "?"
        if p_sticky is not None:
            verdict = "CURRENT — this pop reads as genuine scarcity" if p_sticky >= 0.5 else \
                      "CHOP — this pop reads as calendar mechanics"
        live_block = {
            "date": last_pop["date"].date().isoformat(),
            "age_bd": age_bd,
            "pop_bp": last_pop["pop_bp"],
            "bucket": last_pop["bucket"],
            "rrp_co_z": last_pop["rrp_co_z"],
            "rrp_cosigned": bool(last_pop["rrp_co_z"] >= 1.0),
            "damping_pctl": None if pd.isna(last_pop["damping_pctl"]) else last_pop["damping_pctl"],
            "p_sticky": round(p_sticky, 3) if p_sticky is not None else None,
            "p_escalates": round(p_esc, 3) if p_esc is not None else None,
            "verdict": verdict,
        }

    # receipts: the most recent resolved pops with their outcomes
    resolved = P.dropna(subset=["sticky"]).tail(8)
    receipts = [{
        "date": r["date"].date().isoformat(),
        "pop_bp": r["pop_bp"],
        "bucket": r["bucket"],
        "rrp_co_z": r["rrp_co_z"],
        "stuck": bool(r["sticky"]),
        "escalated": None if pd.isna(r["escalates"]) else bool(r["escalates"]),
    } for _, r in resolved.iterrows()]

    n_sticky = int(P["sticky"].sum(skipna=True))
    n_resolved = int(P["sticky"].notna().sum())
    return {
        "ok": True,
        "asof": s.index[-1].date().isoformat(),
        "n_pops": int(len(P)),
        "n_resolved": n_resolved,
        "sticky_base": {
            "rate": round(n_sticky / n_resolved, 3) if n_resolved else None,
            "ci95": _wilson(n_sticky, n_resolved),
        },
        "live": live_block,          # None = flat water, and that IS the reading
        "flat_water": not live,
        "receipts": receipts,
        "validation": validation,
        "caveats": [
            "pops are declustered (>7 calendar days apart) so trials are ~independent — but regimes still overlap",
            "the model is a 4-feature logistic on purpose: a few hundred pops cannot discipline more",
            "a pop with its expected RRP co-move is choreography; without it, scarcity — the discriminator the 2025 squeezes proved",
        ],
        "method": (
            f"pop = shared PROOF statistic ≥ {RIPTIDE_POP_BP:g}bp, declustered; targets: STICKY = "
            f"half-give-back time ≥ {RIPTIDE_STICKY_MIN_BD}bd (window {RIPTIDE_WINDOW_BD}bd), "
            f"ESCALATES = ≥{BACKTEST_SPIKE_BP:g}bp event within {RIPTIDE_ESCALATE_BD}bd; features "
            f"as-of pop-day close ({', '.join(FEATURES)}); expanding walk-forward logistic across "
            f"pops after {RIPTIDE_MIN_POPS} warmup; validated pop-by-pop vs the base rate"
        ),
    }
