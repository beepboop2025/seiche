"""ML Lab — a learned event-probability model that has to earn its place.

Target: P(funding event within the next 5 business days), where "event" is
the same definition the PROOF lab uses (SOFR−IORB jumping ≥ 10bp over its
trailing 5d median). Features are strictly trailing-only (expanding z-scores,
rolling percentiles, calendar distances); labels use final-vintage prints.

Honesty contract, same as everything else in this codebase:
- Walk-forward evaluation ONLY (expanding train window, refits every
  ML_REFIT_EVERY_BD). No shuffled cross-validation — on time series that is
  leakage wearing a lab coat.
- The model is benchmarked against BOTH climatology (constant base rate) and
  the rule-based Seiche-lite percentile. If it can't beat them out-of-sample,
  the verdict says so in plain text and the rule-based signal stays primary.
- Reliability table published (predicted vs realized by probability bin),
  because a well-ranked but mis-calibrated probability is a trap.

Gradient-boosted trees (HistGradientBoosting) because the feature set is
small, tabular, NaN-riddled (histories start at different dates) and the
relationship is nonlinear (threshold effects everywhere in plumbing).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    BACKTEST_EVENT_FWD_D,
    BACKTEST_SPIKE_BP,
    ML_REFIT_EVERY_BD,
    ML_WARMUP_D,
)


def _ez(s: pd.Series, min_periods: int = 120) -> pd.Series:
    mu = s.expanding(min_periods).mean()
    sd = s.expanding(min_periods).std()
    return (s - mu) / sd.replace(0, np.nan)


def _rpctl(s: pd.Series, window: int = 750, min_periods: int = 250) -> pd.Series:
    return s.rolling(window, min_periods=min_periods).rank(pct=True) * 100.0


def _bdays_until(idx: pd.DatetimeIndex, targets: pd.DatetimeIndex) -> pd.Series:
    """Business days from each index date to the next target date."""
    out = np.full(len(idx), np.nan)
    t_sorted = targets.sort_values()
    for i, d in enumerate(idx):
        pos = t_sorted.searchsorted(d)
        if pos < len(t_sorted):
            out[i] = np.busday_count(d.date(), t_sorted[pos].date())
    return pd.Series(out, index=idx)


def build_features(
    spread_bp: pd.Series,
    tail_bp: pd.Series,
    srf: pd.Series,
    dw_b: pd.Series,
    rrp_b: pd.Series,
    res_gdp_pctl: pd.Series,
    pair_b: pd.Series,
    digestion: pd.Series,
    lite_index: pd.Series,
    lite_pctl: pd.Series,
    vix: pd.Series,
    hy_oas: pd.Series,
    dgs10: pd.Series,
    inr: pd.Series,
    usdt_peg_bp: pd.Series,
    stable_total_b: pd.Series,
) -> tuple[pd.DataFrame, pd.Series]:
    """Daily feature matrix + event labels. Trailing-only, NaN-tolerant."""
    s = spread_bp.dropna()
    idx = pd.bdate_range(s.index.min(), s.index.max())

    def f(x: pd.Series) -> pd.Series:
        # several auctions can share a date — keep the last print per day
        x = x[~x.index.duplicated(keep="last")].sort_index()
        return x.reindex(idx).ffill(limit=10)

    sp = s.reindex(idx)
    X = pd.DataFrame(index=idx)
    X["spread_lvl"] = sp
    X["spread_chg5"] = sp.diff(5)
    X["spread_ez"] = _ez(sp)
    tl = tail_bp.reindex(idx)
    X["tail_lvl"] = tl
    X["tail_ez"] = _ez(tl)
    X["srf_max20"] = f(srf).rolling(20, min_periods=1).max()
    X["dw_lvl"] = f(dw_b)
    X["rrp_lvl"] = f(rrp_b)
    X["res_gdp_pctl"] = f(res_gdp_pctl)
    X["pair_ez"] = _ez(f(pair_b), 60)
    X["digestion"] = f(digestion)
    X["lite"] = lite_index.reindex(idx)
    X["lite_pctl"] = lite_pctl.reindex(idx)
    X["vix_pctl"] = _rpctl(vix.dropna()).reindex(idx).ffill(limit=5)
    X["hy_pctl"] = _rpctl(hy_oas.dropna()).reindex(idx).ffill(limit=5)
    rv = (dgs10.dropna().diff() * 100.0).rolling(10).std()
    X["ratesvol_pctl"] = _rpctl(rv.dropna()).reindex(idx).ffill(limit=5)
    inr_vol = inr.dropna().pct_change().rolling(10).std()
    X["inr_vol_ez"] = _ez(inr_vol.dropna()).reindex(idx).ffill(limit=5)
    X["usdt_absdev_ez"] = _ez(usdt_peg_bp.dropna().abs()).reindex(idx).ffill(limit=5)
    st = stable_total_b.dropna()
    X["stable_chg30_pct"] = (st.pct_change(30) * 100.0).reindex(idx).ffill(limit=5)

    # Calendar distances — the forcing schedule is known in advance.
    months = pd.period_range(idx.min(), idx.max() + pd.offsets.QuarterEnd(1), freq="M")
    month_ends = pd.DatetimeIndex([pd.bdate_range(p.start_time, p.end_time)[-1] for p in months])
    qtr_ends = pd.DatetimeIndex([d for d in month_ends if d.month in (3, 6, 9, 12)])
    tax = pd.DatetimeIndex([
        pd.Timestamp(year=y, month=m, day=15)
        for y in range(idx.min().year, idx.max().year + 2)
        for m in (3, 4, 6, 9, 12)
    ])
    X["bd_to_mend"] = _bdays_until(idx, month_ends)
    X["bd_to_qend"] = _bdays_until(idx, qtr_ends)
    X["bd_to_tax"] = _bdays_until(idx, tax)

    # Label: same event the PROOF lab tests (spike vs trailing median).
    base = sp.rolling(5, min_periods=3).median()
    fwd_max = pd.concat(
        [sp.shift(-k) for k in range(1, BACKTEST_EVENT_FWD_D + 1)], axis=1
    ).max(axis=1)
    y = ((fwd_max - base) >= BACKTEST_SPIKE_BP).astype(float)
    y[fwd_max.isna()] = np.nan  # open windows at the end of the sample

    keep = X["spread_lvl"].notna() & y.notna()
    return X[keep], y[keep]


def walk_forward(X: pd.DataFrame, y: pd.Series) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import brier_score_loss, roc_auc_score

    if len(X) < ML_WARMUP_D + 100:
        return {"ok": False, "reason": f"insufficient history ({len(X)}d)"}
    if y.sum() < 8:
        return {"ok": False, "reason": f"too few events ({int(y.sum())}) to learn from"}

    def make_model():
        return HistGradientBoostingClassifier(
            max_depth=3, max_iter=150, learning_rate=0.08,
            min_samples_leaf=30, early_stopping=False, random_state=7,
        )

    preds = pd.Series(np.nan, index=X.index)
    for start in range(ML_WARMUP_D, len(X), ML_REFIT_EVERY_BD):
        end = min(start + ML_REFIT_EVERY_BD, len(X))
        Xt, yt = X.iloc[:start], y.iloc[:start]
        if yt.sum() < 5:
            continue
        # Histories start on different dates (crypto ~2021): a column that is
        # entirely NaN in this training slice crashes the HGB binner — drop it
        # for this fit; it re-enters once it has data.
        cols = Xt.columns[Xt.notna().any()]
        model = make_model()
        model.fit(Xt[cols], yt)
        preds.iloc[start:end] = model.predict_proba(X.iloc[start:end][cols])[:, 1]

    oos = pd.concat({"p": preds, "y": y, "rule": X["lite_pctl"]}, axis=1).dropna()
    if len(oos) < 200 or oos["y"].sum() < 5:
        return {"ok": False, "reason": "not enough out-of-sample coverage"}

    base_rate = float(oos["y"].mean())
    auroc = float(roc_auc_score(oos["y"], oos["p"]))
    brier = float(brier_score_loss(oos["y"], oos["p"]))
    brier_clim = float(brier_score_loss(oos["y"], np.full(len(oos), base_rate)))
    try:
        auroc_rule = float(roc_auc_score(oos["y"], oos["rule"]))
    except ValueError:
        auroc_rule = None

    # Reliability by predicted-probability bin.
    bins = [0.0, 0.05, 0.15, 0.30, 0.50, 1.01]
    reliability = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        grp = oos[(oos["p"] >= lo) & (oos["p"] < hi)]
        if len(grp):
            reliability.append({
                "bin": f"{lo:.2f}-{hi:.2f}",
                "mean_pred": round(float(grp["p"].mean()), 3),
                "realized": round(float(grp["y"].mean()), 3),
                "n": int(len(grp)),
            })

    # Final model on ALL history -> today's probability + importances.
    final_cols = X.columns[X.notna().any()]
    final = make_model()
    final.fit(X[final_cols], y)
    p_now = float(final.predict_proba(X[final_cols].iloc[[-1]])[0, 1])

    from sklearn.inspection import permutation_importance
    tail_n = min(300, len(X))
    imp = permutation_importance(
        final, X[final_cols].iloc[-tail_n:], y.iloc[-tail_n:], n_repeats=5, random_state=7,
        scoring="roc_auc" if y.iloc[-tail_n:].nunique() > 1 else None,
    )
    order = np.argsort(-imp.importances_mean)[:10]
    top_features = [
        {"feature": final_cols[i], "importance": round(float(imp.importances_mean[i]), 4)}
        for i in order
    ]

    beats_clim = brier < brier_clim
    beats_rule = auroc_rule is None or auroc > auroc_rule
    verdict = (
        "model beats climatology and the rule-based index out-of-sample"
        if (beats_clim and beats_rule)
        else "model beats climatology but NOT the rule-based index — rule stays primary"
        if beats_clim
        else "model does NOT beat climatology out-of-sample — treat as experimental"
    )

    return {
        "ok": True,
        "asof": X.index[-1].date().isoformat(),
        "p_event_5bd": round(p_now, 3),
        "verdict": verdict,
        "validation": {
            "oos_days": int(len(oos)),
            "oos_events": int(oos["y"].sum()),
            "base_rate": round(base_rate, 3),
            "auroc": round(auroc, 3),
            "auroc_rule_based": round(auroc_rule, 3) if auroc_rule is not None else None,
            "brier": round(brier, 4),
            "brier_climatology": round(brier_clim, 4),
            "refit_every_bd": ML_REFIT_EVERY_BD,
            "warmup_d": ML_WARMUP_D,
        },
        "reliability": reliability,
        "top_features": top_features,
        "p_series": [
            [d.date().isoformat(), round(float(v), 3)]
            for d, v in preds.dropna().iloc[::3].items()
        ],
        "n_features": int(X.shape[1]),
        "caveats": [
            "events are rare — AUROC on few positives deserves humility",
            "walk-forward only; no shuffled CV (leakage on time series)",
            "features trailing-only; labels final-vintage (same caveat as PROOF)",
            "probabilities are raw model outputs; check the reliability table before trusting a level",
        ],
        "method": (
            f"HistGradientBoosting (depth 3), expanding walk-forward refits every "
            f"{ML_REFIT_EVERY_BD}bd after {ML_WARMUP_D}d warmup; target = funding event "
            f"(+{BACKTEST_SPIKE_BP}bp vs 5d median) within {BACKTEST_EVENT_FWD_D}bd"
        ),
    }
