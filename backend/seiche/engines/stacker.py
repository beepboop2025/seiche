"""The Stack — walk-forward ensemble of every forecast Seiche makes.

Seiche now emits three independent views of the same target — P(funding event
within 5bd): the rule-based Seiche-lite expanding percentile, the ML Lab
walk-forward model, and the Tide Tables analog odds — plus The Tell (the
plumbing-vs-price divergence, a different axis entirely). A fleet beats a
flagship, but only if the admiral is honest about it:

  - each member is CALIBRATED individually (1-D logistic, walk-forward) so a
    percentile and a raw model output speak the same probability language;
  - the stack is a deliberately tiny logistic (a handful of members + regime
    dummies, ~10 parameters against ~6 historical episodes — anything bigger
    is a memorization engine);
  - the fitted stack must beat the ZERO-PARAMETER equal-weight mean of its
    calibrated members out-of-sample, or the mean is published instead (the
    same publish-naive rule the Turn Barometer lives by);
  - member DISPERSION is an output, not noise: when the fleet disagrees, the
    honest statement is lower conviction — the Book reads it as a gate.

Same honesty contract as the ML Lab: expanding walk-forward with a boundary
embargo, no shuffled CV, validation against climatology and every member,
negative verdicts published in plain text.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.engines.backtest import pop_bp
from seiche.config import (
    BACKTEST_EVENT_FWD_D,
    BACKTEST_SPIKE_BP,
    STACK_L2_C,
    STACK_MIN_MEMBERS,
    STACK_REFIT_EVERY_BD,
    STACK_WARMUP_D,
)

EMBARGO_BD = BACKTEST_EVENT_FWD_D  # labels look 5bd ahead

REGIME_DUMMIES = ["EROSION", "STRAIN", "STRESS"]  # CALM is the base level


def build_member_matrix(
    rule_pctl: pd.Series,
    ml_p: pd.Series | None,
    tide_p: pd.Series | None,
    tell: pd.Series | None,
    swell_p: pd.Series | None = None,
    bathy_p: pd.Series | None = None,
) -> pd.DataFrame:
    """Daily member panel on the rule signal's business-day grid.

    Members arrive in different units (percentile, probability, divergence);
    they are only ever compared AFTER per-member calibration — this matrix
    stores them raw.
    """
    base = rule_pctl.dropna()
    idx = pd.bdate_range(base.index.min(), base.index.max())
    M = pd.DataFrame(index=idx)
    M["rule"] = (base / 100.0).reindex(idx).ffill(limit=3)
    if ml_p is not None and not ml_p.dropna().empty:
        M["ml"] = ml_p.reindex(idx).ffill(limit=3)
    if tide_p is not None and not tide_p.dropna().empty:
        M["tide"] = tide_p.reindex(idx).ffill(limit=3)
    if swell_p is not None and not swell_p.dropna().empty:
        M["swell"] = swell_p.reindex(idx).ffill(limit=3)
    if bathy_p is not None and not bathy_p.dropna().empty:
        # Bathymetry's first-passage probability (its walk-forward has gaps on
        # days already inside the event bin — ffill bridges them like the rest)
        M["bathy"] = bathy_p.reindex(idx).ffill(limit=3)
    if tell is not None and not tell.dropna().empty:
        M["tell"] = ((tell + 100.0) / 200.0).reindex(idx).ffill(limit=3)
    return M


def event_labels(spread_bp: pd.Series, idx: pd.DatetimeIndex) -> pd.Series:
    """The PROOF label via the ONE shared event statistic (backtest.pop_bp —
    trailing 5bd median, shift(1)): pop >= BACKTEST_SPIKE_BP within the next
    BACKTEST_EVENT_FWD_D bd. NaN where the window is open."""
    pop = pop_bp(spread_bp, idx)
    fwd_max = pd.concat(
        [pop.shift(-k) for k in range(1, BACKTEST_EVENT_FWD_D + 1)], axis=1
    ).max(axis=1)
    y = (fwd_max >= BACKTEST_SPIKE_BP).astype(float)
    y[fwd_max.isna()] = np.nan
    return y


def _fit_1d(x: np.ndarray, y: np.ndarray):
    from sklearn.linear_model import LogisticRegression

    m = LogisticRegression(C=STACK_L2_C, max_iter=500)
    m.fit(x.reshape(-1, 1), y)
    return m


def _venn_abers(p_hist: np.ndarray, y_hist: np.ndarray, p_now: float) -> dict | None:
    """Inductive Venn-Abers band for one point: isotonic calibration fitted
    twice, once with (p_now, 0) appended and once with (p_now, 1); the two
    fits' predictions at p_now bracket the calibrated probability with
    finite-sample validity (no distributional assumptions)."""
    from sklearn.isotonic import IsotonicRegression

    try:
        lo_hi = []
        for forced in (0.0, 1.0):
            iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
            iso.fit(np.append(p_hist, p_now), np.append(y_hist, forced))
            lo_hi.append(float(iso.predict([p_now])[0]))
        p0, p1 = sorted(lo_hi)
        return {"p0": round(p0, 3), "p1": round(p1, 3), "method": "Venn-Abers (finite-sample validity)"}
    except Exception:  # noqa: BLE001 — a band is an upgrade, never a blocker
        return None


def walk_forward_stack(
    M: pd.DataFrame,
    y: pd.Series,
    regime: pd.Series | None = None,
) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import brier_score_loss, roc_auc_score

    members = list(M.columns)
    if not members:
        return {"ok": False, "reason": "no members"}
    y = y.reindex(M.index)
    if len(M) < STACK_WARMUP_D + 100:
        return {"ok": False, "reason": f"insufficient member history ({len(M)}d)"}
    if float(y.dropna().sum()) < 8:
        return {"ok": False, "reason": "too few events to learn from"}

    reg = None
    if regime is not None and not regime.dropna().empty:
        reg = regime.reindex(M.index).ffill(limit=5)

    cal = pd.DataFrame(np.nan, index=M.index, columns=members)  # calibrated OOS probs
    p_stack = pd.Series(np.nan, index=M.index)

    for start in range(STACK_WARMUP_D, len(M), STACK_REFIT_EVERY_BD):
        end = min(start + STACK_REFIT_EVERY_BD, len(M))
        train_end = max(start - EMBARGO_BD, 0)
        yt = y.iloc[:train_end]

        # 1. per-member calibration on the train slice
        models: dict[str, object] = {}
        train_cal = pd.DataFrame(np.nan, index=M.index[:train_end], columns=members)
        for m in members:
            xt = M[m].iloc[:train_end]
            ok = xt.notna() & yt.notna()
            if ok.sum() < 60 or yt[ok].sum() < 3 or yt[ok].nunique() < 2:
                continue
            try:
                mod = _fit_1d(xt[ok].to_numpy(), yt[ok].to_numpy())
            except ValueError:
                continue
            models[m] = mod
            train_cal.loc[ok[ok].index, m] = mod.predict_proba(
                xt[ok].to_numpy().reshape(-1, 1))[:, 1]
            blk = M[m].iloc[start:end]
            has = blk.notna()
            if has.any():
                cal.loc[blk.index[has], m] = mod.predict_proba(
                    blk[has].to_numpy().reshape(-1, 1))[:, 1]
        if not models:
            continue

        # 2. the stack: logistic on calibrated members (+ regime dummies),
        # missing members imputed with the train-window mean of that member.
        feats = list(models)
        col_mu = train_cal[feats].mean()
        Xt = train_cal[feats].fillna(col_mu)
        rows_ok = (train_cal[feats].notna().sum(axis=1) >= min(STACK_MIN_MEMBERS, len(feats)))
        rows_ok &= yt.reindex(Xt.index).notna()
        if reg is not None:
            for rname in REGIME_DUMMIES:
                Xt[f"regime_{rname}"] = (reg.iloc[:train_end] == rname).astype(float)
        if rows_ok.sum() < 60 or yt[rows_ok].sum() < 3:
            continue
        stack = LogisticRegression(C=STACK_L2_C, max_iter=500)
        stack.fit(Xt[rows_ok], yt[rows_ok])

        Xb = cal[feats].iloc[start:end].fillna(col_mu)
        if reg is not None:
            for rname in REGIME_DUMMIES:
                Xb[f"regime_{rname}"] = (reg.iloc[start:end] == rname).astype(float)
        score_ok = cal[feats].iloc[start:end].notna().sum(axis=1) >= min(
            STACK_MIN_MEMBERS, len(feats))
        if score_ok.any():
            p_stack.loc[Xb.index[score_ok]] = stack.predict_proba(Xb[score_ok])[:, 1]

    p_mean = cal.mean(axis=1)  # zero-parameter comparator (equal-weight fleet)
    dispersion = cal.std(axis=1)

    oos = pd.concat({"stack": p_stack, "mean": p_mean, "y": y}, axis=1).dropna()
    if len(oos) < 200 or oos["y"].sum() < 5:
        return {"ok": False, "reason": "not enough out-of-sample coverage"}

    base_rate = float(oos["y"].mean())
    val: dict = {
        "oos_days": int(len(oos)),
        "oos_events": int(oos["y"].sum()),
        "base_rate": round(base_rate, 3),
        "brier_stack": round(float(brier_score_loss(oos["y"], oos["stack"])), 4),
        "brier_mean": round(float(brier_score_loss(oos["y"], oos["mean"])), 4),
        "brier_climatology": round(
            float(brier_score_loss(oos["y"], np.full(len(oos), base_rate))), 4),
        "auroc_stack": round(float(roc_auc_score(oos["y"], oos["stack"])), 3),
        "auroc_mean": round(float(roc_auc_score(oos["y"], oos["mean"])), 3),
        "brier_members": {},
        "auroc_members": {},
        "member_coverage_pct": {
            m: round(float(cal[m].notna().mean() * 100.0), 0) for m in members
        },
        "warmup_d": STACK_WARMUP_D,
        "refit_every_bd": STACK_REFIT_EVERY_BD,
        "embargo_bd": EMBARGO_BD,
    }
    for m in members:
        mm = pd.concat({"p": cal[m], "y": y}, axis=1).dropna()
        if len(mm) >= 100 and 0 < mm["y"].sum() < len(mm):
            val["brier_members"][m] = round(float(brier_score_loss(mm["y"], mm["p"])), 4)
            val["auroc_members"][m] = round(float(roc_auc_score(mm["y"], mm["p"])), 3)

    # Publish-naive rule: the fitted stack earns its place or the mean prints.
    published = "stack" if val["brier_stack"] < val["brier_mean"] else "mean"
    p_pub = p_stack if published == "stack" else p_mean
    best_member = min(val["brier_members"], key=val["brier_members"].get) if val["brier_members"] else None
    beats_best = (
        best_member is not None
        and val[f"brier_{published}"] < val["brier_members"][best_member]
    )
    verdict = (
        f"published signal = {published} "
        f"(Brier {val[f'brier_{published}']} vs mean {val['brier_mean']}, "
        f"climatology {val['brier_climatology']}); "
        + (
            f"beats the best single member ({best_member})"
            if beats_best
            else f"does NOT beat the best single member ({best_member}) — the ensemble adds robustness, not skill"
            if best_member
            else "no member had enough coverage to compare"
        )
    )

    p_now = float(p_pub.dropna().iloc[-1]) if not p_pub.dropna().empty else None

    # Venn-Abers calibrated band: isotonic fits with today's point forced to
    # label 0 and to label 1 give [p0, p1] — an interval with FINITE-SAMPLE
    # validity guarantees (Vovk), not an asymptotic hope. Wide band = the
    # OOS record has little to say about probabilities in this region.
    band = None
    if p_now is not None and len(oos) >= 100:
        band = _venn_abers(oos[published].to_numpy(), oos["y"].to_numpy(), p_now)
    disp_now = float(dispersion.dropna().iloc[-1]) if not dispersion.dropna().empty else None
    members_now = {
        m: (round(float(cal[m].dropna().iloc[-1]), 3) if not cal[m].dropna().empty else None)
        for m in members
    }

    both = pd.concat({"p": p_pub, "d": dispersion}, axis=1).dropna(subset=["p"])
    return {
        "ok": True,
        # private (stripped before blob/API): downstream honesty engines race
        # the fleet (Regatta/MCS) and wrap the published probability in
        # conformal sets (Sea Room) — same OOS streams, no recompute.
        "_cal": cal,
        "_p_pub": p_pub,
        "_y": y,
        "asof": M.index[-1].date().isoformat(),
        "p_now": round(p_now, 3) if p_now is not None else None,
        "calibrated_band": band,
        "published": published,
        "members_now": members_now,
        "dispersion_now": round(disp_now, 3) if disp_now is not None else None,
        "validation": val,
        "verdict": verdict,
        "series": [
            [d.date().isoformat(), round(float(r["p"]), 3),
             round(float(r["d"]), 3) if pd.notna(r["d"]) else None]
            for d, r in both.iloc[::3].iterrows()
        ],
        "caveats": [
            "members share upstream data — dispersion understates true model disagreement",
            "~6 historical episodes: the stack is capped at ~10 parameters on purpose",
            "walk-forward with boundary embargo; members are themselves walk-forward outputs (no peeking anywhere in the chain)",
            "missing members imputed with train-window means; per-member coverage published",
        ],
        "method": (
            f"per-member 1-D logistic calibration + logistic stack over calibrated members "
            f"and regime dummies; expanding walk-forward (warmup {STACK_WARMUP_D}d, refit "
            f"{STACK_REFIT_EVERY_BD}bd, embargo {EMBARGO_BD}bd); published = stack only if it "
            "beats the equal-weight mean OOS; target = PROOF funding event within "
            f"{BACKTEST_EVENT_FWD_D}bd"
        ),
        "_p": p_pub.dropna(),
        "_member_probs": cal,
        "_dispersion": dispersion,
    }
