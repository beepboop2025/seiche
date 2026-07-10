"""Historical index reconstruction — the backtestable core of the Seiche Index.

Rebuilds a daily "Seiche-lite" index over the full sample using EXPANDING-
window standardization only: the value on any date uses only data available
on that date (no look-ahead in any z-score or percentile). This is the series
the PROOF lab, The Tell and the Playbook all run on.

Honest scope: only components whose live engines are point-in-time
reconstructable are included — tails, kink (reserves/GDP percentile proxy),
confession (SRF + discount window), rvxray (pair-proxy expanding z),
auctions (digestion index — already trailing by construction), buffers (RRP).
Weather, resonance, hydrophone and warehouse are live-only; their composite
weights are renormalized away here and that exclusion is printed, not hidden.

Vintage caveat (stated on the PROOF page): daily market prints (SOFR, RRP,
TGA, percentile tails) are effectively unrevised; weekly H.4.1 aggregates are
lightly revised. We use final vintage — the honest reading is "as good as a
point-in-time backtest can be on free data, minus small H.4.1 revisions".
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import COMPOSITE_WEIGHTS, LEAKAUDIT_TEMP_CENTER_W, REGIMES

LITE_COMPONENTS = ["tails", "kink", "confession", "rvxray", "auctions", "buffers"]
MIN_Z_PERIODS = 120
MIN_PCTL_PERIODS = 250


def _ez(s: pd.Series, min_periods: int = MIN_Z_PERIODS) -> pd.Series:
    """Expanding z-score: no look-ahead."""
    mu = s.expanding(min_periods).mean()
    sd = s.expanding(min_periods).std()
    return (s - mu) / sd.replace(0, np.nan)


def _epctl(s: pd.Series, min_periods: int = MIN_PCTL_PERIODS) -> pd.Series:
    """Expanding percentile rank (0-1) of each value vs all history to date."""
    return s.expanding(min_periods).rank(pct=True)


def _sigmoid100(x: pd.Series, center: float, slope: float) -> pd.Series:
    return 100.0 / (1.0 + np.exp(-(x - center) * slope))


def build(
    spread_bp: pd.Series,        # SOFR - IORB, bp, daily
    tail_bp: pd.Series,          # SOFR P99 - P50, bp, daily (may be empty)
    srf_accepted: pd.Series,     # $B daily (zeros count)
    dw_b: pd.Series,             # discount window primary credit, $B, weekly
    rrp_b: pd.Series,            # ON RRP, $B, daily
    res_gdp: pd.Series,          # reserves/GDP ratio, weekly
    pair_b: pd.Series,           # RV pair proxy, $B, weekly (CFTC)
    digestion: pd.Series,        # auction digestion index, per-auction dates
    exclude: tuple[str, ...] = (),   # components to leave out (orthogonal tests)
    leak: str = "none",          # LEAK AUDIT ONLY — deliberately broken variants
) -> dict:
    """`leak` exists solely for the Leak Audit engine (one-switch protocol,
    arXiv:2605.23959): "norm_global" swaps every expanding z/percentile for
    its full-sample (look-ahead) twin; "temp_center" swaps the trailing tails
    smoother for a centered window that peeks forward. Neither variant is
    ever published as a signal — they exist to MEASURE what cheating would
    buy. Publishing code must always call with leak="none"."""
    if leak not in ("none", "norm_global", "temp_center"):
        raise ValueError(f"unknown leak mode: {leak!r}")
    idx = pd.bdate_range(spread_bp.dropna().index.min(), spread_bp.dropna().index.max())
    f = lambda s: s.reindex(idx).ffill(limit=10) if not s.dropna().empty else pd.Series(index=idx, dtype=float)

    if leak == "norm_global":
        _z = lambda s, mp=MIN_Z_PERIODS: (s - s.mean()) / s.std()
        _p = lambda s, mp=MIN_PCTL_PERIODS: s.rank(pct=True)
    else:
        _z, _p = _ez, _epctl

    spread_d = spread_bp.reindex(idx)
    tail_d = tail_bp.reindex(idx) if not tail_bp.dropna().empty else pd.Series(index=idx, dtype=float)

    comps = pd.DataFrame(index=idx)

    # tails — mirror tails_score: 0.6 tail z + 0.4 spread z through the sigmoid
    combo = 0.6 * _z(tail_d).fillna(0.0) + 0.4 * _z(spread_d).fillna(0.0)
    smoothed = (
        combo.rolling(LEAKAUDIT_TEMP_CENTER_W, center=True, min_periods=1).mean()
        if leak == "temp_center" else combo.ewm(span=5).mean()
    )
    comps["tails"] = _sigmoid100(smoothed, 1.4, 1.1)

    # kink proxy — low reserves/GDP percentile = closer to scarcity. Capped at
    # 70: a percentile is a cruder instrument than the fitted kink engine.
    p = _p(f(res_gdp))
    comps["kink"] = ((1.0 - p) * 70.0).clip(0, 70)

    # confession — SRF trailing-20d max through the live srf_score curve,
    # blended max() with the discount-window curve ($2B stigma floor).
    srf20 = f(srf_accepted).rolling(20, min_periods=1).max()
    srf_sc = (100.0 * (1.0 - np.exp(-srf20 / 22.0))).clip(0, 100)
    dw = f(dw_b)
    dw_sc = (100.0 * (1.0 - np.exp(-(dw - 2.0).clip(lower=0.0) / 12.0))).clip(0, 100)
    comps["confession"] = pd.concat([srf_sc, dw_sc], axis=1).max(axis=1)

    # rvxray — expanding z of the pair proxy through the live sigmoid
    comps["rvxray"] = _sigmoid100(_z(f(pair_b), 60), 0.8, 1.3)

    # auctions — digestion index is trailing-window by construction
    dig = digestion.sort_index()
    dig = dig[~dig.index.duplicated(keep="last")]
    comps["auctions"] = _sigmoid100(f(dig), 0.5, 2.2)

    # buffers — same closed form as the live engine
    comps["buffers"] = ((1.0 - f(rrp_b) / 400.0).clip(0, 1) * 100.0)

    active = [k for k in LITE_COMPONENTS if k not in exclude]
    if not active:
        raise ValueError("exclude removed every component")
    w = {k: COMPOSITE_WEIGHTS[k] for k in active}
    wsum = sum(w.values())
    weights = pd.Series({k: v / wsum for k, v in w.items()})

    used = comps[active]
    avail = used.notna()
    eff_w = avail.mul(weights, axis=1)
    index = (used.fillna(0.0) * eff_w).sum(axis=1) / eff_w.sum(axis=1).replace(0, np.nan)
    index = index.dropna()

    pctl = _p(index) * 100.0

    def regime_of(v: float) -> str:
        return next(name for cutoff, name in REGIMES if v < cutoff)

    return {
        "index": index,
        "pctl": pctl.reindex(index.index),
        "components": comps.reindex(index.index),
        "weights": {k: round(v / wsum, 3) for k, v in w.items()},
        "excluded": [k for k in COMPOSITE_WEIGHTS if k not in active],
        "regime_series": index.map(regime_of),
        "method": (
            "Seiche-lite: expanding-window standardization only (no look-ahead); "
            "components tails/kink-proxy/confession/rvxray/auctions/buffers with live "
            "composite weights renormalized; weather/resonance/hydrophone/warehouse are "
            "live-only and excluded (stated, not hidden); final-vintage data"
        ),
    }
