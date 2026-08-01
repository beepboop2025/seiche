"""Phase Map: planted phase dependence recovered in the right bin, bands
cover, the vestigial clock is labeled, and the house copy rules hold.

The two tests that matter most:
  - autocorrelation planted ONLY at the month-end turn must surface in the
    month-end clock's event bins, attributed to the FIRST day of each pair
    (the phase-resolved construction), and nowhere else;
  - a structureless series must never let a clock print CONFIRMS: with no
    band separation the map may lean, it may not conclude.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from seiche.config import (
    COMPOSITE_WEIGHTS,
    FRED_SERIES,
    NYFED_RATES_START,
    PHASEMAP_MIN_HISTORY_D,
)
from seiche.engines import phasemap as pm
from seiche.engines.backtest import pop_bp


def _grid(n_days: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2018-04-02", periods=n_days)


def _noise(idx: pd.DatetimeIndex, seed: int, sigma: float = 0.3) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.standard_normal(len(idx)) * sigma, index=idx)


def _month_end_positions(idx: pd.DatetimeIndex, months=(3, 6, 9, 12)) -> list[int]:
    """Grid positions of the last business day of each NON-quarter month."""
    by_month: dict[tuple[int, int], list[int]] = {}
    for i, d in enumerate(idx):
        by_month.setdefault((d.year, d.month), []).append(i)
    return [locs[-1] for (_, m), locs in sorted(by_month.items()) if m not in months]


def _clocks(out: dict) -> dict[str, dict]:
    return {c["clock"]: c for c in out["clocks"]}


# ---------------------------------------------------------------------------
# 1. Planted month-end dependence recovered, in the right bin, right clock
# ---------------------------------------------------------------------------

def test_planted_month_end_dependence_recovered():
    idx = _grid(2100)
    s = _noise(idx, seed=11)
    # Two-day dependent runs at each non-quarter month-end turn: amplitude
    # varies across events so the (turn-1, turn) pair correlates.
    amps = (5.0, 8.0, 11.0, 14.0)
    for k, p in enumerate(_month_end_positions(idx)):
        if 5 <= p < len(idx) - 2:
            a = amps[k % len(amps)]
            s.iloc[p - 1] = a
            s.iloc[p] = 0.85 * a
    out = pm.analyze(s)
    assert out["ok"]
    me = _clocks(out)["month_end"]
    rows = {r["bin"]: r for r in me["bins"]}
    # the pair (turn-1, turn) is attributed to the FIRST day's bin: the
    # correlation lives in "-1", not "0"
    assert rows["-1"]["ac1"] is not None and rows["-1"]["ac1"] > 0.8
    assert rows["-1"]["ac1_ci"][0] > 0.5
    assert rows["0"]["ac1"] is None or abs(rows["0"]["ac1"]) < 0.5
    assert me["audit"]["verdict"] == "CONFIRMS"
    assert "autocorr" in me["audit"]["detail"]  # numbers ride inline
    # nothing was planted on the quarter clock: it must not conclude
    assert _clocks(out)["quarter_end"]["audit"]["verdict"] != "CONFIRMS"


# ---------------------------------------------------------------------------
# 2. Structureless series: no clock may print CONFIRMS
# ---------------------------------------------------------------------------

def test_white_noise_never_confirms():
    out = pm.analyze(_noise(_grid(2000), seed=5, sigma=1.0))
    assert out["ok"]
    for c in out["clocks"]:
        if c["ok"] and not c["vestigial"]:
            assert c["audit"]["verdict"] != "CONFIRMS", c["clock"]


# ---------------------------------------------------------------------------
# 3. Bands: mandatory wherever a statistic prints, and they cover
# ---------------------------------------------------------------------------

def test_bands_present_and_cover_the_pooled_law():
    s = _noise(_grid(2400), seed=3, sigma=1.0)
    out = pm.analyze(s)
    assert out["ok"]
    x = pop_bp(s).dropna().to_numpy(dtype=float)
    pooled = pm._stats(x, np.ones(len(x), dtype=bool))
    ac_total = ac_cover = rate_total = rate_cover = 0
    for c in out["clocks"]:
        if not c["ok"]:
            continue
        for r in c["bins"]:
            if r["ac1"] is not None:
                assert r["ac1_ci"] is not None  # bands are mandatory
                ac_total += 1
                ac_cover += int(r["ac1_ci"][0] <= pooled["ac1"] <= r["ac1_ci"][1])
            if r["n"] > 0:
                assert r["shock_rate_ci"] is not None
                rate_total += 1
                rate_cover += int(
                    r["shock_rate_ci"][0] <= pooled["shock_rate"] <= r["shock_rate_ci"][1]
                )
    # every bin is a subsample of the same stationary law: the pooled value
    # must sit inside almost every bin's 95% band
    assert ac_total >= 20 and ac_cover / ac_total >= 0.8
    assert rate_total >= 20 and rate_cover / rate_total >= 0.8


# ---------------------------------------------------------------------------
# 4. The vestigial clock is labeled as such, and the reading says which
#    clocks are live
# ---------------------------------------------------------------------------

def test_vestigial_labeling_present():
    out = pm.analyze(_noise(_grid(900), seed=7))
    assert out["ok"]
    rmp = next(c for c in out["clocks"] if c["vestigial"])
    assert rmp["clock"] == "rmp"
    assert "VESTIGIAL" in rmp["label"] and "March 2020" in rmp["label"]
    assert "no engine on this board keys on the maintenance period" in rmp["note"]
    assert [r["bin"] for r in rmp["bins"]] == [f"d{k:02d}" for k in range(1, 11)]
    assert rmp["audit"]["verdict"] in ("CONFIRMS", "PARTIALLY CONFIRMS", "EMBARRASSES")
    assert "vestigial" in out["reading"]
    assert "month/quarter-end" in out["reading"]
    for c in out["clocks"]:
        if c["ok"] and not c["vestigial"]:
            assert c["audit"]["verdict"] in ("CONFIRMS", "PARTIALLY CONFIRMS", "EMBARRASSES")


# ---------------------------------------------------------------------------
# 5. Settlement clock: realized auction calendar when it exists, the
#    conventional grid when it does not
# ---------------------------------------------------------------------------

def _auction_frame(dates: list[str], amt: float, security_type: str = "Note") -> pd.DataFrame:
    return pd.DataFrame({
        "security_type": [security_type] * len(dates),
        "issue_date": dates,
        "total_accepted": [amt] * len(dates),
    })


def test_settlement_clock_prefers_the_realized_auction_calendar():
    idx = _grid(1200)
    big_dates = [d.date().isoformat() for d in pd.bdate_range("2018-06-15", periods=10, freq="BME")]
    small = _auction_frame(["2018-07-02", "2018-08-01"], 50e9)          # below the flag
    bills = _auction_frame(["2018-09-04"], 200e9, security_type="Bill")  # coupon-only parser drops
    au = pd.concat([_auction_frame(big_dates, 150e9), small, bills], ignore_index=True)
    out = pm.analyze(_noise(idx, seed=9), auctions=au)
    settle = _clocks(out)["settlement"]
    assert "auction" in settle["events_source"]
    assert settle["n_events"] == 10  # sub-flag piles and bills never count


def test_settlement_clock_falls_back_to_convention():
    out = pm.analyze(_noise(_grid(900), seed=9))
    settle = _clocks(out)["settlement"]
    assert "conventional" in settle["events_source"]
    assert settle["ok"]


# ---------------------------------------------------------------------------
# 6. Refusals, determinism, house copy rule
# ---------------------------------------------------------------------------

def test_refuses_short_history():
    out = pm.analyze(_noise(_grid(300), seed=1))
    assert not out["ok"]
    assert "insufficient" in out["reason"]


def test_deterministic():
    s = _noise(_grid(1100), seed=13)
    a, b = pm.analyze(s), pm.analyze(s)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_no_dashes_anywhere_in_the_payload():
    out = pm.analyze(_noise(_grid(900), seed=2))
    blob = json.dumps(out)
    assert "—" not in blob and "–" not in blob


# ---------------------------------------------------------------------------
# 7. Registry and doctrine pins
# ---------------------------------------------------------------------------

def test_registry_start_covers_the_history_floor():
    """The *_LONG lesson, inverted: Phase Map needs no LONG twin because the
    board's own SOFR/IORB fetch windows open BEFORE the SOFR era does. Pin
    both facts so a registry retune cannot silently starve the engine."""
    for mnemonic in ("SOFR", "IORB"):
        spec = next(s for s in FRED_SERIES if s.mnemonic == mnemonic)
        assert pd.Timestamp(spec.start) <= pd.Timestamp(NYFED_RATES_START), mnemonic
    # the era holds roughly 2000 business days; the floor must sit well
    # inside it so the engine can never be starved by its own registry
    era_days = len(pd.bdate_range(NYFED_RATES_START, "2026-07-01"))
    assert PHASEMAP_MIN_HISTORY_D <= era_days // 2


def test_display_only_doctrine():
    """No composite weight: an EMBARRASSES changes the siblings only through
    a pre-registered methodology change, never automatically."""
    assert "phasemap" not in COMPOSITE_WEIGHTS
