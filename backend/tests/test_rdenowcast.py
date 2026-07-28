"""RDE Nowcast tests: synthetic data only, no network.

The honesty invariants that matter: the unit mapping is exact arithmetic,
the scorecard is walk-forward (data after a cutoff cannot move that row),
and everything degrades to {"ok": False, ...} instead of raising.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from seiche.engines import rdenowcast
from seiche.sources import nyfed_rde


# --------------------------------------------------------------------------
# Fixtures: a kink-shaped world with a known breakpoint
# --------------------------------------------------------------------------

KINK_RATIO = 0.115
SLOPE = 400.0     # bp per unit of reserves/GDP below the kink
BASE_BP = -5.0
GDP_B = 28000.0   # constant nominal GDP, $B SAAR


def _fit_dict(current_reserves_b: float) -> dict:
    """Overview-shaped kink payload with known parameters."""
    kink_b = KINK_RATIO * GDP_B
    return {
        "ok": True,
        "kink_ratio": KINK_RATIO,
        "slope_bp_per_ratio": SLOPE,
        "kink_reserves_b": kink_b,
        "current_reserves_b": current_reserves_b,
        "distance_b": current_reserves_b - kink_b,
        "r2": 0.7,
        "asof": "2026-06-24",
    }


def _world(n_weeks: int = 220, end: str = "2026-06-24"):
    """Daily spread (%), weekly reserves ($M), quarterly GDP ($B) obeying
    the hinge exactly, plus small seeded noise."""
    rng = np.random.default_rng(11)
    weeks = pd.date_range(end=end, periods=n_weeks, freq="W-WED")
    res_b = np.linspace(3900.0, 2600.0, n_weeks)          # drain through the kink
    reserves_weekly = pd.Series(res_b * 1000.0, index=weeks)  # $B -> $M
    x = res_b / GDP_B
    spread_bp = BASE_BP + SLOPE * np.maximum(0.0, KINK_RATIO - x)
    days = pd.bdate_range(weeks[0] - pd.Timedelta(days=6), weeks[-1])
    wk_of_day = pd.Series(spread_bp, index=weeks).reindex(days, method="bfill")
    spread_daily = (wk_of_day + rng.normal(0, 0.3, len(days))) / 100.0  # bp -> %
    q_idx = pd.date_range(weeks[0] - pd.offsets.QuarterBegin(2), weeks[-1], freq="QS")
    gdp_quarterly = pd.Series(GDP_B, index=q_idx)
    return spread_daily, reserves_weekly, gdp_quarterly


def _rde_frame(end: str = "2026-06-10", n_months: int = 24) -> pd.DataFrame:
    """Monthly NY Fed-shaped prints drifting from near zero to -0.5."""
    idx = pd.date_range(end=end, periods=n_months, freq="MS") + pd.Timedelta(days=9)
    med = np.linspace(-0.05, -0.5, n_months)
    return pd.DataFrame(
        {
            "median": med,
            "p2_5": med - 0.6,
            "p16": med - 0.3,
            "p84": med + 0.3,
            "p97_5": med + 0.6,
        },
        index=idx,
    )


# --------------------------------------------------------------------------
# Unit mapping
# --------------------------------------------------------------------------

def test_implied_rde_exact_below_kink():
    # x = 3000/28000 = 0.10714 < kink: implied = -400 * x / 100
    fit = _fit_dict(3000.0)
    x = 3000.0 / GDP_B
    assert rdenowcast.implied_rde(fit) == pytest.approx(-SLOPE * x / 100.0)


def test_implied_rde_zero_above_kink():
    fit = _fit_dict(3900.0)  # x = 0.1393 > 0.115
    assert rdenowcast.implied_rde(fit) == 0.0


def test_implied_rde_refuses_bad_fit():
    assert rdenowcast.implied_rde({"ok": False}) is None
    assert rdenowcast.implied_rde({"ok": True, "kink_ratio": 0.1}) is None


# --------------------------------------------------------------------------
# Latest comparison
# --------------------------------------------------------------------------

def test_nowcast_latest_side_by_side():
    fit = _fit_dict(3000.0)
    rde = _rde_frame()
    out = rdenowcast.nowcast(fit, rde)
    assert out["ok"]
    ours = -SLOPE * (3000.0 / GDP_B) / 100.0
    assert out["ours_bp_per_1pct"] == pytest.approx(ours, abs=1e-3)
    assert out["nyfed_bp_per_1pct"] == pytest.approx(-0.5, abs=1e-6)
    assert out["divergence_bp"] == pytest.approx(ours - (-0.5), abs=1e-3)
    # ours = -0.4286, their 68 band = [-0.8, -0.2] -> inside
    assert out["within_68_band"] is True
    assert out["direction_agree"] is True
    assert out["our_side_of_kink"] == "below"
    assert out["nyfed_asof"] == rde.index[-1].date().isoformat()
    # without raw series: no scorecard, and the caveat says so
    assert out["scorecard"] == []
    assert out["scorecard_summary"] is None
    assert any("scorecard skipped" in c for c in out["caveats"])


def test_caveats_name_the_source_lineage():
    out = rdenowcast.nowcast(_fit_dict(3000.0), _rde_frame())
    blob = " ".join(out["caveats"])
    for name in ("Afonso", "Giannone", "La Spada", "Williams"):
        assert name in blob


def test_nowcast_degrades_gracefully():
    empty = pd.DataFrame(columns=["median", "p16", "p84"])
    assert rdenowcast.nowcast(_fit_dict(3000.0), empty)["ok"] is False
    assert rdenowcast.nowcast(_fit_dict(3000.0), None)["ok"] is False
    bad = rdenowcast.nowcast({"ok": False, "reason": "no data"}, _rde_frame())
    assert bad["ok"] is False and "no data" in bad["reason"]
    assert rdenowcast.nowcast(None, _rde_frame())["ok"] is False


# --------------------------------------------------------------------------
# Walk-forward scorecard
# --------------------------------------------------------------------------

def test_scorecard_walkforward_grades_months():
    spread, res, gdp = _world()
    fit = _fit_dict(res.iloc[-1] / 1000.0)
    rde = _rde_frame()
    out = rdenowcast.nowcast(fit, rde, spread, res, gdp, n_scorecard=8)
    assert out["ok"]
    rows = out["scorecard"]
    assert 0 < len(rows) <= 8
    cutoffs = [r["cutoff"] for r in rows]
    assert cutoffs == sorted(cutoffs), "oldest first"
    graded = [r for r in rows if r["gradable"]]
    assert graded, "the synthetic world must produce at least one graded month"
    for r in graded:
        assert isinstance(r["within_band"], bool)
        assert isinstance(r["direction_agree"], bool)
        assert r["diff_bp"] == pytest.approx(
            r["ours_bp_per_1pct"] - r["nyfed_bp_per_1pct"], abs=2e-3
        )
    summ = out["scorecard_summary"]
    assert summ["n"] == len(graded)
    assert 0 <= summ["within_band"] <= summ["n"]


def test_scorecard_has_no_lookahead():
    """Corrupting data strictly after a cutoff must not move that row."""
    spread, res, gdp = _world()
    fit = _fit_dict(res.iloc[-1] / 1000.0)
    rde = _rde_frame()
    base = rdenowcast.nowcast(fit, rde, spread, res, gdp, n_scorecard=6)
    rows = [r for r in base["scorecard"] if r["gradable"]]
    assert len(rows) >= 2
    target = rows[-2]
    cutoff = pd.Timestamp(target["cutoff"])
    poisoned = spread.copy()
    poisoned[poisoned.index > cutoff] = 5.0  # 500bp squeeze, post-cutoff only
    again = rdenowcast.nowcast(fit, rde, poisoned, res, gdp, n_scorecard=6)
    match = [r for r in again["scorecard"] if r["cutoff"] == target["cutoff"]]
    assert match and match[0]["ours_bp_per_1pct"] == target["ours_bp_per_1pct"]


# --------------------------------------------------------------------------
# Source parser (pure function; bytes in, frame out)
# --------------------------------------------------------------------------

REAL_HEADER = (
    "Date,Elasticity - 50th percentile (main),Elasticity - 2.5th percentile,"
    "Elasticity - 97.5th percentile,Elasticity - 16th percentile,"
    "Elasticity - 84th percentile\n"
)


def test_parse_rde_real_layout():
    csv = REAL_HEADER + (
        "1/20/2010,-3.244024,-5.656335,-0.646839,-4.369168,-2.172346\n"
        "7/6/2026,-0.267904,-0.592405,0.084762,-0.439382,-0.093415\n"
    )
    df = nyfed_rde.parse_rde(csv.encode())
    assert list(df.columns) == ["median", "p2_5", "p16", "p84", "p97_5"]
    assert df.index[-1] == pd.Timestamp("2026-07-06")
    last = df.iloc[-1]
    assert last["median"] == pytest.approx(-0.267904)
    assert last["p2_5"] == pytest.approx(-0.592405)   # not swapped with 97.5
    assert last["p97_5"] == pytest.approx(0.084762)
    assert last["p16"] == pytest.approx(-0.439382)
    assert last["p84"] == pytest.approx(-0.093415)


def test_parse_rde_rejects_unknown_layout():
    with pytest.raises(ValueError):
        nyfed_rde.parse_rde(b"foo,bar\n1,2\n")


def test_parse_rde_feeds_nowcast_end_to_end():
    csv = REAL_HEADER + "7/6/2026,-0.267904,-0.592405,0.084762,-0.439382,-0.093415\n"
    df = nyfed_rde.parse_rde(csv.encode())
    out = rdenowcast.nowcast(_fit_dict(3000.0), df)
    assert out["ok"]
    assert out["nyfed_bp_per_1pct"] == pytest.approx(-0.268, abs=1e-3)
    assert out["nyfed_band_68"] == [-0.439, -0.093]
    assert out["nyfed_zero_in_68_band"] is False
