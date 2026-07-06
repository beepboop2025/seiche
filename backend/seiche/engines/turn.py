"""Turn Barometer — forecast the severity of the NEXT calendar turn.

Month-, quarter- and year-end funding turns are the most predictable stress
events in finance: the date is known, only the amplitude is uncertain. We
learn the amplitude from history: features frozen 5 business days before each
past turn (buffer state, spread level, tail pressure, reserve percentile,
prior slosh of the same mode), target = that turn's slosh in bp.

Honesty contract: leave-one-out cross-validation, always benchmarked against
the naive forecast (trailing median of the same mode). If the model can't
beat naive, we SAY SO and publish the naive number instead. n is printed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import (
    TURN_FEATURE_LAG_D,
    TURN_MIN_HISTORY,
    TURN_SEVERITY_BINS,
)
from seiche.engines.resonance import _classify_events, _event_response

TURN_MODES = ["month_end", "quarter_end", "year_end"]


def _severity(bp: float) -> int:
    sev = 1
    for cut in TURN_SEVERITY_BINS:
        if bp >= cut:
            sev += 1
    return sev


def _features_at(
    loc: int,
    spread: pd.Series,
    rrp: pd.Series,
    tail_bp: pd.Series,
    res_gdp_pctl: pd.Series,
    prior_slosh: float,
    is_q: int,
    is_y: int,
) -> list[float] | None:
    t = spread.index[loc]
    def last_at(s: pd.Series) -> float | None:
        ss = s.dropna()
        ss = ss[ss.index <= t]
        return float(ss.iloc[-1]) if not ss.empty else None

    spread_med5 = spread.iloc[max(loc - 4, 0) : loc + 1].median()
    vals = [
        last_at(rrp),
        float(spread_med5) if pd.notna(spread_med5) else None,
        last_at(tail_bp),
        last_at(res_gdp_pctl),
        prior_slosh,
        float(is_q),
        float(is_y),
    ]
    if any(v is None or not np.isfinite(v) for v in vals):
        return None
    return vals


FEATURE_NAMES = [
    "rrp_b", "spread_med5_bp", "tail_bp", "res_gdp_pctl", "prior_slosh_bp",
    "is_quarter_end", "is_year_end",
]


def analyze(
    spread_bp: pd.Series,
    rrp_b: pd.Series,
    tail_bp: pd.Series,
    res_gdp_pctl: pd.Series,   # expanding percentile 0-1 (history layer)
) -> dict:
    s = spread_bp.dropna()
    if len(s) < 400:
        return {"ok": False, "reason": "insufficient spread history"}

    events = _classify_events(s.index)
    rows = []  # (date, mode, features, target_slosh)
    last_slosh: dict[str, float] = {}
    all_turns = sorted(
        [(e, m) for m in TURN_MODES for e in events[m]], key=lambda x: x[0]
    )
    for ev, mode in all_turns:
        resp = _event_response(s, ev)
        loc = s.index.searchsorted(ev) - TURN_FEATURE_LAG_D
        if resp is None or loc < 30:
            if resp is not None:
                last_slosh[mode] = resp["slosh_bp"]
            continue
        prior = last_slosh.get(mode)
        if prior is not None:
            feats = _features_at(
                loc, s, rrp_b, tail_bp, res_gdp_pctl, prior,
                int(mode == "quarter_end"), int(mode == "year_end"),
            )
            if feats is not None:
                rows.append((ev, mode, feats, resp["slosh_bp"]))
        last_slosh[mode] = resp["slosh_bp"]

    if len(rows) < TURN_MIN_HISTORY:
        return {"ok": False, "reason": f"only {len(rows)} usable historical turns"}

    X = np.array([r[2] for r in rows])
    y = np.array([r[3] for r in rows])
    modes = [r[1] for r in rows]

    # Naive baseline: trailing median of the same mode's last 4 sloshes.
    naive_pred = []
    hist: dict[str, list[float]] = {m: [] for m in TURN_MODES}
    for i, (ev, mode, _, target) in enumerate(rows):
        h = hist[mode]
        if h:
            naive_pred.append(float(np.median(h[-4:])))
        elif i > 0:
            naive_pred.append(float(np.median(y[:i])))
        else:
            naive_pred.append(0.0)
        h.append(target)
    naive_pred = np.array(naive_pred)

    # Leave-one-out OLS with per-fold standardization (no leakage).
    loo_pred = np.zeros(len(y))
    for i in range(len(y)):
        mask = np.ones(len(y), bool)
        mask[i] = False
        Xt, yt = X[mask], y[mask]
        mu, sd = Xt.mean(axis=0), Xt.std(axis=0)
        sd[sd == 0] = 1.0
        Xn = np.column_stack([np.ones(mask.sum()), (Xt - mu) / sd])
        beta, *_ = np.linalg.lstsq(Xn, yt, rcond=None)
        xi = np.concatenate([[1.0], (X[i] - mu) / sd])
        loo_pred[i] = float(xi @ beta)

    mae_model = float(np.mean(np.abs(loo_pred - y)))
    mae_naive = float(np.mean(np.abs(naive_pred - y)))
    skill = 1.0 - mae_model / mae_naive if mae_naive > 0 else 0.0
    residuals = loo_pred - y

    # Final fit on ALL history for the forward forecast.
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    Xn = np.column_stack([np.ones(len(y)), (X - mu) / sd])
    beta, *_ = np.linalg.lstsq(Xn, y, rcond=None)

    # Next turn: first month/quarter/year-end after the last observation.
    today = s.index[-1]
    horizon = pd.bdate_range(today, today + pd.Timedelta(days=70))
    nxt, nxt_mode = None, None
    future_events = _classify_events(horizon)
    cand = sorted(
        (e, m) for m in TURN_MODES for e in future_events[m] if e > today
    )
    if cand:
        nxt, nxt_mode = cand[0]
    if nxt is None:
        return {"ok": False, "reason": "no upcoming turn inside 70d (calendar bug?)"}

    feats_now = _features_at(
        len(s) - 1, s, rrp_b, tail_bp, res_gdp_pctl,
        last_slosh.get(nxt_mode, float(np.median(y))),
        int(nxt_mode == "quarter_end"), int(nxt_mode == "year_end"),
    )
    if feats_now is None:
        return {"ok": False, "reason": "current features unavailable"}

    # Both forecasts are always computed and published; the model only gets
    # the headline when its LOO skill is MEANINGFULLY positive (>0.05), not
    # merely nonzero — skill of 0.02 is naive wearing a lab coat.
    xn = np.concatenate([[1.0], (np.array(feats_now) - mu) / sd])
    model_point = float(xn @ beta)
    mode_hist = [t for (_, m, _, t) in rows if m == nxt_mode]
    naive_point = float(np.median(mode_hist[-4:])) if mode_hist else float(np.median(y))

    use_model = skill > 0.05
    point = model_point if use_model else naive_point

    lo, hi = np.percentile(residuals, [20, 80])
    if use_model:
        note = None
    elif skill > 0.0:
        note = (f"model skill vs naive is only {skill:.3f} on LOO-CV — statistically "
                "indistinguishable from naive, so the naive same-mode median is published")
    else:
        note = ("model shows NO skill vs naive on LOO-CV — publishing the naive "
                "same-mode median instead (that honesty is the feature)")
    return {
        "ok": True,
        "asof": today.date().isoformat(),
        "next_turn": {
            "date": nxt.date().isoformat(),
            "mode": nxt_mode,
            "forecast_bp": round(point, 1),
            "forecast_model_bp": round(model_point, 1),
            "forecast_naive_bp": round(naive_point, 1),
            "published": "model" if use_model else "naive",
            "band_bp": [round(point + lo, 1), round(point + hi, 1)],
            "severity": _severity(point),
            "severity_scale": "1 calm .. 5 extreme "
                              f"(cutoffs {TURN_SEVERITY_BINS} bp)",
        },
        "validation": {
            "n_turns": len(rows),
            "loo_mae_bp": round(mae_model, 2),
            "naive_mae_bp": round(mae_naive, 2),
            "skill_vs_naive": round(skill, 3),
            "model_used": use_model,
            "note": note,
        },
        "features": dict(zip(FEATURE_NAMES, [round(float(v), 2) for v in feats_now])),
        "recent_turns": [
            {"date": ev.date().isoformat(), "mode": m, "slosh_bp": t}
            for ev, m, _, t in rows[-8:]
        ],
        "method": (
            f"OLS on {len(FEATURE_NAMES)} features frozen T-{TURN_FEATURE_LAG_D}bd before each "
            "historical turn; LOO-CV vs naive same-mode trailing median; band = LOO "
            "residual 20/80%"
        ),
    }
