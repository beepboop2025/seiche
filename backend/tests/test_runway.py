"""Reserve Runway tests: synthetic data, honesty invariants.

What matters: crossing dates land where the arithmetic says, the scenario
ordering is coherent (fast crosses no later than base, base no later than
slow), assumptions ship as printable data, the vintage record is
self-contained for a ledger, and degradation is graceful rather than a
traceback.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from seiche.engines import runway


def _weekly_reserves_m(levels_b) -> pd.Series:
    # WRESBAL comes in $M on a Wednesday grid.
    idx = pd.date_range("2025-01-01", periods=len(levels_b), freq="W-WED")
    return pd.Series(np.asarray(levels_b, dtype=float) * 1000.0, index=idx)


def _daily(idx_len: int, value: float, end: str = "2026-07-22") -> pd.Series:
    idx = pd.bdate_range(end=end, periods=idx_len)
    return pd.Series(np.full(idx_len, value, dtype=float), index=idx)


def _kink(kink_b: float) -> dict:
    return {"ok": True, "kink_reserves_b": kink_b, "current_reserves_b": 3200.0, "drain_per_bday_b": -4.0}


def test_steady_drain_crosses_kink_at_the_arithmetic_week():
    # 40 $B/week drain from 3400; kink at 3200 => 200/40 = 5 weeks.
    levels = [3400.0 + 40.0 * (29 - i) for i in range(30)]  # ends at 3400, draining 40/wk
    res = _weekly_reserves_m(levels)
    r = runway.project(res, _daily(300, 0.0), _daily(300, 800.0), _kink(3200.0), [], 0.0)
    assert r["ok"]
    base = r["scenarios"]["base"]
    assert base["crossing_week"] == 5
    assert base["crossing_date"] is not None
    # Named day: start + 5 weeks exactly (constant TGA => pure drift).
    expected = (res.index[-1] + pd.Timedelta(weeks=5)).date().isoformat()
    assert base["crossing_date"] == expected
    assert len(base["path"]) == runway.HORIZON_WEEKS + 1


def test_abundant_reserves_never_cross():
    levels = [3400.0 - 0.5 * (29 - i) for i in range(30)]  # trickle drain
    r = runway.project(_weekly_reserves_m(levels), _daily(300, 0.0), _daily(300, 800.0), _kink(2500.0), [], 0.0)
    assert r["ok"]
    for name in ("base", "fast_drain", "slow"):
        s = r["scenarios"][name]
        assert s["crossing_date"] is None
        assert "no crossing" in s["verdict"]


def test_scenario_ordering_fast_before_base_before_slow():
    # TGA sits below its median and further below p75; RRP has real balance.
    levels = [3400.0 + 30.0 * (29 - i) for i in range(30)]
    tga_idx = pd.bdate_range(end="2026-07-22", periods=300)
    tga = pd.Series(np.linspace(900.0, 500.0, 300), index=tga_idx)  # now 500, median/p75 above
    rrp = _daily(300, 400.0)
    r = runway.project(_weekly_reserves_m(levels), rrp, tga, _kink(3150.0), [], 30.0)
    assert r["ok"]
    weeks = {n: r["scenarios"][n]["crossing_week"] for n in ("fast_drain", "base", "slow")}
    assert weeks["fast_drain"] is not None and weeks["base"] is not None
    assert weeks["fast_drain"] <= weeks["base"]
    assert weeks["slow"] is None or weeks["base"] <= weeks["slow"]
    # Slow leg ends higher than base: RRP absorbed part of the drain.
    assert r["scenarios"]["slow"]["end_reserves_b"] > r["scenarios"]["base"]["end_reserves_b"]


def test_already_below_kink_names_the_start_date():
    levels = [3100.0 + 2.0 * (29 - i) for i in range(30)]
    res = _weekly_reserves_m(levels)
    r = runway.project(res, _daily(300, 0.0), _daily(300, 800.0), _kink(3600.0), [], 0.0)
    assert r["ok"]
    base = r["scenarios"]["base"]
    assert base["crossing_week"] == 0
    assert base["crossing_date"] == res.index[-1].date().isoformat() == r["asof"]
    assert "already below" in base["verdict"]


def test_settlements_shape_the_path_without_shifting_its_level():
    """The trailing drift is realized reserve change and already contains
    routine settlement, and the TGA path contains it again. Booking the raw
    drain on top counted the same dollars up to three times and pulled every
    crossing early. Settlements now enter demeaned: they move the shape of
    the path, not its 13-week level."""
    levels = [3400.0 + 20.0 * (29 - i) for i in range(30)]
    res = _weekly_reserves_m(levels)
    start = res.index[-1]
    settlements = [
        {"date": (start + pd.Timedelta(days=3)).date().isoformat(), "amount_b": 95.0},
        {"date": (start + pd.Timedelta(days=10)).date().isoformat(), "amount_b": 74.0},
        {"date": (start - pd.Timedelta(days=5)).date().isoformat(), "amount_b": 999.0},  # past; ignored
    ]
    kw = dict(rrp_daily=_daily(300, 0.0), tga_daily=_daily(300, 800.0), kink=_kink(3150.0), qt_pace_b_per_month=0.0)
    without = runway.project(res, calendar_settlements=[], **kw)
    with_st = runway.project(res, calendar_settlements=settlements, **kw)

    # Only the two in-window settlements count, at the stated passthrough.
    assert with_st["assumptions"]["settlements_gross_b"] == 169.0
    # No level bias over the horizon: the end point is untouched.
    assert with_st["scenarios"]["base"]["end_reserves_b"] == pytest.approx(
        without["scenarios"]["base"]["end_reserves_b"], abs=0.2)
    # But the shape differs: the heavy settlement week sits below the flat path.
    a = [lvl for _, lvl in zip(range(3), with_st["scenarios"]["base"]["path"])]
    b = [lvl for _, lvl in zip(range(3), without["scenarios"]["base"]["path"])]
    assert a != b


def test_settlement_demeaning_is_unbiased_over_the_covered_span():
    from seiche.engines.runway import _settlements_by_week
    start = pd.Timestamp("2026-07-29")
    cal = [{"date": "2026-08-05", "amount_b": 100.0},
           {"date": "2026-08-19", "amount_b": 40.0}]
    by_week, gross = _settlements_by_week(cal, start)
    assert gross == 140.0
    assert sum(by_week.values()) == pytest.approx(0.0, abs=1e-3)   # 4dp rounding per week
    assert by_week[1] > 0            # the heavy week is a relative drain
    assert by_week[2] < 0            # the empty week is a relative reprieve


def test_unit_normalization_rrp_and_tga_in_millions():
    levels = [3400.0 + 20.0 * (29 - i) for i in range(30)]
    res = _weekly_reserves_m(levels)
    in_b = runway.project(res, _daily(300, 400.0), _daily(300, 800.0), _kink(3150.0), [], 10.0)
    in_m = runway.project(res, _daily(300, 400_000.0), _daily(300, 800_000.0), _kink(3150.0), [], 10.0)
    assert in_b["assumptions"]["rrp_now_b"] == in_m["assumptions"]["rrp_now_b"] == 400.0
    assert in_b["assumptions"]["tga_now_b"] == in_m["assumptions"]["tga_now_b"] == 800.0


def test_missing_kink_publishes_paths_without_crossings():
    levels = [3400.0 + 20.0 * (29 - i) for i in range(30)]
    r = runway.project(_weekly_reserves_m(levels), _daily(300, 0.0), _daily(300, 800.0),
                       {"ok": False, "reason": "no fit"}, [], 0.0)
    assert r["ok"]
    assert all(r["scenarios"][n]["crossing_date"] is None for n in r["scenarios"])
    assert any("kink" in c for c in r["caveats"])
    assert r["assumptions"]["kink_reserves_b"] is None


def test_graceful_degradation():
    empty = pd.Series(dtype=float)
    ok_tga = _daily(300, 800.0)
    assert runway.project(empty, empty, ok_tga, _kink(3200.0), [], 0.0)["ok"] is False
    # Short reserve history with no kink drain fallback refuses politely.
    short = _weekly_reserves_m([3400.0, 3390.0, 3380.0])
    r = runway.project(short, empty, ok_tga, {"ok": False}, [], 0.0)
    assert r["ok"] is False and "insufficient" in r["reason"]
    # Same short history but the kink drain rescues it, with a caveat.
    r2 = runway.project(short, empty, ok_tga, _kink(3200.0), [], 0.0)
    assert r2["ok"] and any("short" in c for c in r2["caveats"])
    # No TGA at all is a refusal: the scenarios are TGA arithmetic.
    assert runway.project(_weekly_reserves_m([3400.0] * 30), empty, empty, _kink(3200.0), [], 0.0)["ok"] is False


def test_vintage_record_is_self_contained():
    levels = [3400.0 + 40.0 * (29 - i) for i in range(30)]
    r = runway.project(_weekly_reserves_m(levels), _daily(300, 100.0), _daily(300, 800.0), _kink(3200.0), [], 25.0)
    v = r["vintage_record"]
    assert v["schema"] == 1
    assert v["asof"] == r["asof"]
    assert set(v["paths"]) == {"base", "fast_drain", "slow"}
    assert v["crossings"]["base"] == r["scenarios"]["base"]["crossing_date"]
    assert v["assumptions"]["qt_pace_b_per_month"] == 25.0
    # Ledger-safe: everything JSON-serializable.
    import json
    json.dumps(v)


def test_runway_score_orders_urgency():
    levels_fast = [3400.0 + 60.0 * (29 - i) for i in range(30)]
    levels_slow = [3400.0 + 5.0 * (29 - i) for i in range(30)]
    kw = dict(rrp_daily=_daily(300, 0.0), tga_daily=_daily(300, 800.0),
              kink=_kink(3200.0), calendar_settlements=[], qt_pace_b_per_month=0.0)
    fast = runway.project(_weekly_reserves_m(levels_fast), **kw)
    slow = runway.project(_weekly_reserves_m(levels_slow), **kw)
    assert runway.runway_score(fast) > runway.runway_score(slow)
    assert runway.runway_score({"ok": False}) == 0.0
