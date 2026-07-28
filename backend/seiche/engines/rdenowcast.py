"""RDE Nowcast: our live kink fit graded against the NY Fed's official print.

The NY Fed publishes Reserve Demand Elasticity monthly (Afonso, Giannone,
La Spada, Williams): the effect, in basis points, of a 1 percent change in
total reserves on the federal funds-IORB spread, with confidence bands. Our
kink engine fits the same demand curve continuously from public data. This
engine converts our hinge fit into their units and puts the two side by
side: if we track their print between releases, we carry a multi-week
nowcast lead over the official number; where we diverge, one of us is wrong
and the scorecard keeps the receipts so PROOF can grade it.

Unit mapping, stated honestly: our fit is
    spread_bp = a + slope * max(0, kink_ratio - x),  x = reserves/GDP
so d(spread)/dx = -slope below the kink and 0 above. A 1 percent reserve
change moves x by 0.01x, hence our implied RDE = -slope * x / 100 below the
kink and exactly 0 above it. Residual differences the mapping cannot remove:
they regress the fed funds-IORB spread, we fit SOFR-IORB; theirs is a smooth
time-varying coefficient, ours a hard hinge, so we print exactly 0 on the
flat side where they can print small negatives.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.engines import kink as eng_kink

SIGN_DEADZONE_BP = 0.05  # below this magnitude an elasticity reads as zero


def implied_rde(fit: dict, x: float | None = None) -> float | None:
    """Our fit's elasticity in NY Fed units: bp of spread per 1 pct reserve change.

    x defaults to the ratio embedded in the fit dict itself (current reserves
    over the GDP implied by kink_reserves_b / kink_ratio), so a bare overview
    payload is enough for the latest print."""
    if not fit.get("ok"):
        return None
    kink_ratio = fit.get("kink_ratio")
    slope = fit.get("slope_bp_per_ratio")
    if not kink_ratio or slope is None:
        return None
    if x is None:
        kink_b, res_b = fit.get("kink_reserves_b"), fit.get("current_reserves_b")
        if not kink_b or res_b is None:
            return None
        x = res_b / (kink_b / kink_ratio)
    if x >= kink_ratio:
        return 0.0
    return float(-slope * x / 100.0)


def _sign(v: float) -> int:
    return 0 if abs(v) < SIGN_DEADZONE_BP else (1 if v > 0 else -1)


def _release_cutoffs(median: pd.Series, n: int) -> list[pd.Timestamp]:
    """Last observation date per calendar month, most recent n, oldest first.

    Each RDE release extends the series by roughly a month; the last obs date
    in a month is a lower bound on that release's information set, and our
    inputs through that date were public well before the release landed."""
    last_per_month = median.groupby(median.index.to_period("M")).apply(lambda s: s.index[-1])
    return list(last_per_month.sort_values().iloc[-n:])


def _scorecard(
    rde: pd.DataFrame,
    spread_daily: pd.Series,
    reserves_weekly: pd.Series,
    gdp_quarterly: pd.Series,
    n: int,
) -> list[dict]:
    rows = []
    for cutoff in _release_cutoffs(rde["median"].dropna(), n):
        # Walk-forward refit: only inputs dated on or before their last obs.
        fit_t = eng_kink.fit_kink(
            spread_daily[spread_daily.index <= cutoff],
            reserves_weekly[reserves_weekly.index <= cutoff],
            gdp_quarterly[gdp_quarterly.index <= cutoff],
        )
        ours = implied_rde(fit_t)
        their = rde.loc[cutoff]
        theirs = float(their["median"])
        p16 = float(their["p16"]) if pd.notna(their.get("p16")) else None
        p84 = float(their["p84"]) if pd.notna(their.get("p84")) else None
        row = {
            "cutoff": cutoff.date().isoformat(),
            "nyfed_bp_per_1pct": round(theirs, 3),
            "nyfed_band_68": [round(p16, 3), round(p84, 3)] if p16 is not None and p84 is not None else None,
            "ours_bp_per_1pct": round(ours, 3) if ours is not None else None,
        }
        if ours is None:
            row.update({"gradable": False, "reason": fit_t.get("reason", "kink fit failed")})
        else:
            row.update({
                "gradable": True,
                "diff_bp": round(ours - theirs, 3),
                "within_band": bool(p16 <= ours <= p84) if p16 is not None and p84 is not None else None,
                "direction_agree": _sign(ours) == _sign(theirs),
            })
        rows.append(row)
    return rows


def nowcast(
    kink_fit: dict,
    rde: pd.DataFrame,
    spread_daily: pd.Series | None = None,     # SOFR - IORB, %, daily
    reserves_weekly: pd.Series | None = None,  # WRESBAL, $M, weekly Wed
    gdp_quarterly: pd.Series | None = None,    # nominal GDP, $B SAAR, quarterly
    n_scorecard: int = 18,
) -> dict:
    """Side-by-side of our continuous kink fit vs the NY Fed's monthly RDE.

    The three raw series are optional: without them the month-by-month
    scorecard degrades to empty (the latest comparison needs only the fit
    dict); with them each scorecard row is a walk-forward refit truncated to
    that release period's data."""
    if rde is None or rde.empty or rde["median"].dropna().empty:
        return {"ok": False, "reason": "no NY Fed RDE data"}
    if not kink_fit or not kink_fit.get("ok"):
        why = (kink_fit or {}).get("reason", "unavailable")
        return {"ok": False, "reason": f"kink fit not usable: {why}"}

    ours = implied_rde(kink_fit)
    if ours is None:
        return {"ok": False, "reason": "kink fit lacks kink_ratio/slope/reserve fields"}

    med = rde["median"].dropna()
    nyfed_asof = med.index[-1]
    latest = rde.loc[nyfed_asof]
    theirs = float(latest["median"])
    p16 = float(latest["p16"]) if pd.notna(latest.get("p16")) else None
    p84 = float(latest["p84"]) if pd.notna(latest.get("p84")) else None
    p2_5 = float(latest["p2_5"]) if pd.notna(latest.get("p2_5")) else None
    p97_5 = float(latest["p97_5"]) if pd.notna(latest.get("p97_5")) else None

    have_series = all(
        s is not None and not s.dropna().empty
        for s in (spread_daily, reserves_weekly, gdp_quarterly)
    )
    scorecard = (
        _scorecard(rde, spread_daily, reserves_weekly, gdp_quarterly, n_scorecard)
        if have_series else []
    )
    graded = [r for r in scorecard if r.get("gradable")]
    banded = [r for r in graded if r.get("within_band") is not None]
    summary = None
    if graded:
        summary = {
            "n": len(graded),
            "within_band": sum(1 for r in banded if r["within_band"]),
            "direction_agree": sum(1 for r in graded if r["direction_agree"]),
            "mean_abs_diff_bp": round(float(np.mean([abs(r["diff_bp"]) for r in graded])), 3),
        }

    kink_asof = pd.Timestamp(kink_fit["asof"])
    lead_days = int((kink_asof - nyfed_asof).days)

    caveats = [
        "official series: NY Fed Reserve Demand Elasticity, methodology by "
        "Afonso, Giannone, La Spada and Williams; monthly release, "
        "68/95 pct bands as published",
        "units differ under the hood: they regress fed funds-IORB, we fit "
        "SOFR-IORB; the mapping -slope*x/100 assumes GDP fixed over the move",
        "our hinge prints exactly 0 above the kink where their smooth "
        "coefficient can print small negatives; disagreement near the kink "
        "is expected, disagreement deep in either region is signal",
    ]
    if have_series:
        caveats.append(
            "scorecard cutoffs use their last obs date per month, which our "
            "public inputs predate by weeks at each release; GDP truncation "
            "by obs date is roughly one quarter optimistic on availability"
        )
    else:
        caveats.append("raw series not supplied: month-by-month scorecard skipped")

    return {
        "ok": True,
        "ours_bp_per_1pct": round(ours, 3),
        "nyfed_bp_per_1pct": round(theirs, 3),
        "nyfed_band_68": [round(p16, 3), round(p84, 3)] if p16 is not None and p84 is not None else None,
        "nyfed_band_95": [round(p2_5, 3), round(p97_5, 3)] if p2_5 is not None and p97_5 is not None else None,
        "divergence_bp": round(ours - theirs, 3),
        "within_68_band": bool(p16 <= ours <= p84) if p16 is not None and p84 is not None else None,
        "direction_agree": _sign(ours) == _sign(theirs),
        "nyfed_zero_in_68_band": bool(p84 >= 0.0) if p84 is not None else None,
        "our_side_of_kink": "below" if (kink_fit.get("distance_b") or 0) < 0 else "above",
        "nyfed_asof": nyfed_asof.date().isoformat(),
        "kink_asof": kink_asof.date().isoformat(),
        "nowcast_lead_days": lead_days,
        "scorecard": scorecard,
        "scorecard_summary": summary,
        "asof": max(kink_asof, nyfed_asof).date().isoformat(),
        "method": (
            "our hinge fit mapped to NY Fed RDE units: implied bp per 1 pct "
            "reserve change = -slope*x/100 below the kink, 0 above (x = "
            "reserves/GDP); latest print compared against their published "
            "median and bands; scorecard = walk-forward refits truncated to "
            "each monthly release period"
        ),
        "caveats": caveats,
    }
