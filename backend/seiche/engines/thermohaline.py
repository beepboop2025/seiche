"""Thermohaline — the deep circulation under the daily plumbing.

The ocean has two circulations: wind-driven surface currents that change in
days, and the thermohaline conveyor — slow, planet-scale, and the thing that
actually sets the climate. The funding basin is the same: SOFR prints and
RRP balances are surface weather; underneath sits the OFFSHORE DOLLAR STOCK
— USD credit owed by non-banks outside the United States (BIS global
liquidity indicators, ~$14T) — and the credit-to-GDP gaps that measure how
far national credit systems have stretched above trend. When the conveyor
accelerates, the world has borrowed more dollars it must eventually roll,
hedge, or repay through the very plumbing this terminal watches; every
squeeze in the daily data is ultimately a rationing of THIS stock.

Data: BIS Data Portal (keyless, quarterly, published ~2 quarters after the
reference period BY DESIGN). That lag sets the doctrine: a two-quarter-old
number cannot be evidence of stress today, so this engine is context ONLY —
it locates the current squeeze inside the credit cycle, it never joins the
composite. Expanding percentiles against the series' own quarter-century
history; the publication lag is printed, not hidden.

Placement among the siblings: Basins couples the world's short-rate surfaces
day by day; Far Basin reads policy fear; Thermohaline is the stock those
flows service — the slowest and largest number on the board.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import THERMO_HOT_PCTL, THERMO_MIN_OBS


def _yoy(pts: pd.Series) -> pd.Series:
    """Year-over-year growth of a quarterly series, %."""
    s = pts.dropna()
    return (s / s.shift(4) - 1.0) * 100.0


def _epctl_last(s: pd.Series) -> float | None:
    """Expanding percentile of the LAST value vs all history to date."""
    v = s.dropna()
    if len(v) < THERMO_MIN_OBS:
        return None
    return round(float((v.iloc[:-1] <= v.iloc[-1]).mean() * 100.0), 0)


def analyze(bis: dict) -> dict:
    """Input: {mnemonic: Series} from the BIS collector. Publishes the
    offshore-dollar stock and growth, the loans/securities split, the EME
    slice, and the credit-to-GDP gaps — all with expanding percentiles and
    the publication lag stated. Context only, never composite."""
    def pts(m: str) -> pd.Series:
        s = bis.get(m)
        return s.points.dropna() if s is not None else pd.Series(dtype=float)

    stock = pts("GLI_OFFSHORE_USD")
    if stock.empty:
        return {"ok": False, "reason": "BIS offshore-credit series unavailable"}
    if len(stock) < THERMO_MIN_OBS:
        return {"ok": False, "reason": f"only {len(stock)} quarterly obs (< {THERMO_MIN_OBS})"}

    asof_q = stock.index[-1]
    lag_days = int((pd.Timestamp.now(tz=None).normalize() - asof_q).days)
    stock_t = float(stock.iloc[-1]) / 1e6  # $M -> $T

    growth = _yoy(stock)
    g_now = float(growth.dropna().iloc[-1]) if not growth.dropna().empty else None
    g_pctl = _epctl_last(growth)

    # composition: is the growth coming from bank loans or bond markets?
    comp = {}
    for key, mnem in (("loans", "GLI_OFFSHORE_LOANS"), ("debt_securities", "GLI_OFFSHORE_DEBT")):
        g = _yoy(pts(mnem))
        if not g.dropna().empty:
            comp[key] = {
                "yoy_pct": round(float(g.dropna().iloc[-1]), 1),
                "pctl": _epctl_last(g),
            }

    eme_g = _yoy(pts("GLI_EME_USD"))
    eme = (
        {"yoy_pct": round(float(eme_g.dropna().iloc[-1]), 1), "pctl": _epctl_last(eme_g)}
        if not eme_g.dropna().empty else None
    )

    gaps = []
    for label, mnem in (("United States", "CREDIT_GAP_US"), ("China", "CREDIT_GAP_CN")):
        g = pts(mnem)
        if g.empty:
            continue
        gaps.append({
            "economy": label,
            "gap_pp": round(float(g.iloc[-1]), 1),
            "pctl": _epctl_last(g),
            "asof": g.index[-1].date().isoformat(),
            "reading": "credit above trend" if float(g.iloc[-1]) > 0 else "credit below trend",
        })

    hot = g_pctl is not None and g_pctl >= THERMO_HOT_PCTL
    cold = g_pctl is not None and g_pctl <= (100.0 - THERMO_HOT_PCTL)
    if g_now is None:
        reading = "offshore stock printed but growth history is too short to place"
    elif hot:
        reading = (
            f"the conveyor is ACCELERATING: offshore dollar credit growing {g_now:+.1f}% yoy "
            f"({g_pctl:.0f}th pctl of its own history) — the world is adding dollar "
            f"liabilities it must roll through this plumbing"
        )
    elif cold:
        reading = (
            f"the conveyor is decelerating ({g_now:+.1f}% yoy, {g_pctl:.0f}th pctl) — "
            f"offshore dollar credit is being rationed; squeezes land on a shrinking base"
        )
    else:
        reading = f"mid-cycle: offshore dollar credit {g_now:+.1f}% yoy ({g_pctl:.0f}th pctl)"

    yoy_rows = [
        [d.date().isoformat(), round(float(v), 2)]
        for d, v in growth.dropna().items()
    ]

    return {
        "ok": True,
        "asof": asof_q.date().isoformat(),
        "publication_lag_days": lag_days,
        "stock": {
            "usd_trillions": round(stock_t, 2),
            "yoy_pct": round(g_now, 1) if g_now is not None else None,
            "yoy_pctl": g_pctl,
        },
        "composition": comp,
        "eme": eme,
        "credit_gaps": gaps,
        "yoy_rows": yoy_rows,
        "reading": reading,
        "caveats": [
            f"BIS publishes ~2 quarters after the reference period — this print is "
            f"{lag_days} days old BY DESIGN, stated not hidden",
            "quarterly data cannot evidence stress today: this engine locates the squeeze "
            "inside the credit cycle — context ONLY, never composite (doctrine)",
            "expanding percentiles vs the series' own history since 2000 — no cross-sample "
            "reference, no look-ahead",
            "credit-to-GDP gaps use the BIS one-sided HP filter; the level is famously "
            "sensitive to the trend estimate at the sample edge — read the sign and the "
            "trend, not the decimal",
        ],
        "method": (
            "BIS Data Portal (keyless SDMX-CSV): offshore stock = USD credit (bank loans + "
            "debt securities) to non-banks outside the US (WS_GLI); growth = yoy of the "
            "quarterly stock; percentiles expanding vs own history since 2000 "
            f"(min {THERMO_MIN_OBS} obs); credit-to-GDP gaps = BIS actual − one-sided HP "
            "trend (WS_CREDIT_GAP). Quarterly, lagged, context only."
        ),
    }
