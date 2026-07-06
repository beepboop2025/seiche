"""Engine tests — synthetic data, focused on the honesty invariants.

The tests that matter most here aren't "does it run" but "does it refuse to
cheat": no look-ahead in the historical reconstruction, disjoint resonance
modes, small-value swap ops excluded, composite renormalization published.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from seiche.engines import (
    backtest,
    basins,
    composite,
    history,
    hydrophone,
    playbook,
    resonance,
    sonar,
    turn,
)
from seiche.engines import rvxray, weather


def _bdays(n: int, start: str = "2019-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


@pytest.fixture()
def rng():
    return np.random.default_rng(7)


# --------------------------------------------------------------------------
# Resonance
# --------------------------------------------------------------------------

def test_resonance_detects_amplifying_quarter_ends(rng):
    idx = _bdays(1600)
    spread = pd.Series(rng.normal(0, 0.4, len(idx)), index=idx)
    for i, ts in enumerate(idx):
        if ts.is_quarter_end or (ts.month in (3, 6, 9, 12) and ts == max(d for d in idx if d.month == ts.month and d.year == ts.year)):
            pass
    # add growing spikes on the last bday of Mar/Jun/Sep (quarter_end mode)
    events = resonance._classify_events(idx)
    for k, ev in enumerate(events["quarter_end"]):
        loc = idx.searchsorted(ev)
        if loc < len(idx):
            spread.iloc[loc] += 2.0 + 0.8 * k  # amplitude grows over time
    r = resonance.analyze(spread)
    assert r["ok"]
    q = r["modes"]["quarter_end"]
    assert q["ok"]
    assert q["amplification"] > 1.2, "growing spikes must read as amplification"
    # month_end got no injected spikes: must NOT be amplifying comparably
    m = r["modes"]["month_end"]
    if m.get("ok"):
        assert m["amplification"] < q["amplification"]


def test_resonance_modes_are_disjoint():
    idx = _bdays(900)
    events = resonance._classify_events(idx)
    all_dates = [d for lst in events.values() for d in lst]
    assert len(all_dates) == len(set(all_dates)), "one date must belong to exactly one mode"


# --------------------------------------------------------------------------
# Hydrophone
# --------------------------------------------------------------------------

def test_absorption_higher_for_coupled_panel(rng):
    idx = _bdays(400)
    common = rng.normal(0, 1, len(idx))
    coupled = {f"s{i}": pd.Series(np.cumsum(common + rng.normal(0, 0.3, len(idx))), index=idx) for i in range(6)}
    indep = {f"s{i}": pd.Series(np.cumsum(rng.normal(0, 1, len(idx))), index=idx) for i in range(6)}
    a_coupled = hydrophone.analyze(coupled)
    a_indep = hydrophone.analyze(indep)
    assert a_coupled["ok"] and a_indep["ok"]
    assert a_coupled["absorption"] > a_indep["absorption"] + 0.15


# --------------------------------------------------------------------------
# SONAR
# --------------------------------------------------------------------------

def test_sonar_flags_injected_outlier(rng):
    idx = _bdays(300)
    quiet = pd.Series(rng.normal(10, 0.1, len(idx)), index=idx)
    loud = quiet.copy()
    loud.iloc[-1] = 20.0  # ~100 MAD units away
    res = sonar.sweep({"quiet": ("q", "u", quiet), "loud": ("l", "u", loud)})
    movers = {m["name"]: m for m in res["movers"]}
    assert movers["loud"]["flag"]
    assert not movers["quiet"]["flag"]


# --------------------------------------------------------------------------
# History: THE no-look-ahead invariant
# --------------------------------------------------------------------------

def _hist_inputs(n, rng):
    idx = _bdays(n)
    widx = pd.date_range(idx[0], idx[-1], freq="W-WED")
    return dict(
        spread_bp=pd.Series(rng.normal(0, 2, len(idx)), index=idx),
        tail_bp=pd.Series(np.abs(rng.normal(4, 2, len(idx))), index=idx),
        srf_accepted=pd.Series(0.0, index=idx),
        dw_b=pd.Series(2.0, index=widx),
        rrp_b=pd.Series(np.linspace(2000, 5, len(idx)), index=idx),
        res_gdp=pd.Series(np.linspace(0.13, 0.10, len(widx)), index=widx),
        pair_b=pd.Series(np.linspace(300, 900, len(widx)), index=widx),
        digestion=pd.Series(rng.normal(0, 0.5, len(widx)), index=widx),
    )


def test_history_has_no_look_ahead(rng):
    full = _hist_inputs(1000, rng)
    h_full = history.build(**full)
    truncated = {
        k: (v.iloc[:-120] if k in ("spread_bp", "tail_bp", "srf_accepted", "rrp_b") else v)
        for k, v in full.items()
    }
    h_trunc = history.build(**truncated)
    t = h_trunc["index"].index[-1]
    # the same date must have the same index value whether or not the future exists
    assert abs(float(h_full["index"].loc[t]) - float(h_trunc["index"].loc[t])) < 1e-6, \
        "index value at T changed when future data was appended — look-ahead leak"


def test_history_publishes_exclusions(rng):
    h = history.build(**_hist_inputs(800, rng))
    assert "weather" in h["excluded"] and "resonance" in h["excluded"]
    assert abs(sum(h["weights"].values()) - 1.0) < 0.01


# --------------------------------------------------------------------------
# Composite
# --------------------------------------------------------------------------

def test_composite_renormalizes_and_reports_dead():
    subs = {"tails": 50.0, "kink": None, "weather": 20.0, "confession": 0.0,
            "rvxray": None, "resonance": 40.0, "hydrophone": 10.0,
            "auctions": 30.0, "warehouse": 60.0, "buffers": 90.0}
    out = composite.compose(subs)
    assert out["ok"]
    assert "kink" in out["dead_inputs"] and "rvxray" in out["dead_inputs"]
    assert out["coverage_pct"] < 100.0
    assert 0.0 <= out["value"] <= 100.0


def test_confession_takes_the_louder_channel():
    srf = pd.DataFrame({"accepted": [0.0] * 20}, index=_bdays(20))
    dw_hot = pd.Series([30.0], index=[pd.Timestamp("2026-01-01")])
    assert composite.confession_score(srf, dw_hot) > 80.0
    dw_quiet = pd.Series([2.0], index=[pd.Timestamp("2026-01-01")])
    assert composite.confession_score(srf, dw_quiet) == 0.0


# --------------------------------------------------------------------------
# Turn barometer
# --------------------------------------------------------------------------

def test_turn_runs_and_reports_validation(rng):
    idx = _bdays(1500)
    spread = pd.Series(rng.normal(0, 1, len(idx)), index=idx)
    events = resonance._classify_events(idx)
    for m in ("month_end", "quarter_end", "year_end"):
        for ev in events[m]:
            loc = idx.searchsorted(ev)
            if loc < len(idx):
                spread.iloc[loc] += 6.0
    rrp = pd.Series(np.linspace(2400, 0, len(idx)), index=idx)
    tail = pd.Series(np.abs(rng.normal(4, 1, len(idx))), index=idx)
    res_pctl = pd.Series(np.linspace(1.0, 0.0, len(idx)), index=idx)
    r = turn.analyze(spread, rrp, tail, res_pctl)
    assert r["ok"]
    v = r["validation"]
    assert v["n_turns"] >= 12
    assert v["loo_mae_bp"] > 0 and v["naive_mae_bp"] > 0
    assert r["next_turn"]["severity"] in (1, 2, 3, 4, 5)


# --------------------------------------------------------------------------
# Backtest
# --------------------------------------------------------------------------

def test_backtest_perfect_signal_gets_high_recall(rng):
    idx = _bdays(1200)
    spread = pd.Series(rng.normal(0, 1, len(idx)), index=idx)
    spike_locs = list(range(400, 1100, 150))
    for loc in spike_locs:
        spread.iloc[loc] += 25.0
    # a signal that ramps to 100 exactly before each spike
    pctl = pd.Series(10.0, index=idx)
    for loc in spike_locs:
        pctl.iloc[loc - 4 : loc + 1] = 95.0
    r = backtest.run(pctl, spread, outcomes={})
    assert r["ok"]
    assert r["event_capture"]["recall"] >= 0.9
    assert r["event_capture"]["precision"] > r["event_capture"]["base_rate"]


# --------------------------------------------------------------------------
# Playbook
# --------------------------------------------------------------------------

def test_playbook_excludes_open_windows(rng):
    idx = _bdays(900)
    lite = pd.Series(30.0, index=idx)  # constant EROSION regime
    tell = pd.Series(30.0, index=idx)  # constant "plumbing leads"
    out = {"SP500": pd.Series(np.linspace(4000, 5000, len(idx)), index=idx)}
    r = playbook.analyze(lite, tell, out)
    assert r["ok"]
    assert r["state"]["regime"] == "EROSION"
    cell = r["tables"][0]["horizons"]["20d"]
    # matching days exclude the trailing 20bd whose windows are open
    assert cell["n_days"] <= len(idx) - 20


# --------------------------------------------------------------------------
# Basins
# --------------------------------------------------------------------------

def test_basins_excludes_small_value_ops(rng):
    idx = _bdays(700)
    widx = pd.date_range(idx[0], idx[-1], freq="W-WED")
    ops = [
        {"trade_date": idx[-3].date().isoformat(), "counterparty": "ECB", "amount_m": 5000.0,
         "term_days": 7, "rate": 4.0, "is_small_value": False},
        {"trade_date": idx[-2].date().isoformat(), "counterparty": "BoJ", "amount_m": 90000.0,
         "term_days": 7, "rate": 4.0, "is_small_value": True},  # test op — must not count
    ]
    r = basins.analyze(
        spread_us_bp=pd.Series(rng.normal(0, 2, len(idx)), index=idx),
        estr=pd.Series(2.0, index=idx),
        ecb_dfr=pd.Series(2.25, index=idx),
        sonia=pd.Series(3.7, index=idx),
        dxy=pd.Series(rng.normal(120, 1, len(idx)), index=idx),
        swap_lines_m=pd.Series(250.0, index=widx),
        foreign_rrp_m=pd.Series(300000.0, index=widx),
        fx_ops=ops,
    )
    assert r["ok"]
    assert r["swap_lines"]["ops_30d_total_m"] == 5000.0
    assert r["swap_lines"]["small_value_ops_excluded"] == 1


# --------------------------------------------------------------------------
# Weather settlement calendar + crowding guard
# --------------------------------------------------------------------------

def test_settlement_calendar_parses_amounts():
    up = pd.DataFrame({
        "issue_date": ["2026-07-09", "2026-07-09", "2026-07-15"],
        "offering_amt": ["90,000,000,000", "52,000,000,000", "119000000000"],
    })
    cal = weather.settlement_calendar(up)
    assert round(float(cal.loc[pd.Timestamp("2026-07-09")]), 0) == 142.0


def test_rvxray_ignores_non_ust_contracts(rng):
    df = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-06")] * 2,
        "contract": ["UST 2Y NOTE", "FED FUNDS"],
        "open_interest_all": [100_000.0, 50_000.0],
        "lev_money_positions_long_all": [10_000.0, 5_000.0],
        "lev_money_positions_short_all": [40_000.0, 9_000.0],
        "asset_mgr_positions_long_all": [35_000.0, 8_000.0],
        "asset_mgr_positions_short_all": [5_000.0, 1_000.0],
    })
    hist = rvxray.position_history(df)
    # only the UST contract contributes: min(40k, 35k) * $200k face = $7.0B
    assert round(float(hist["pair_b"].iloc[0]), 1) == 7.0


# --------------------------------------------------------------------------
# Moorings
# --------------------------------------------------------------------------

def test_moorings_flags_depeg_and_drain(rng):
    from seiche.engines import moorings
    idx = _bdays(400)
    cal = pd.date_range(idx[0], idx[-1], freq="D")
    usdt = pd.Series(1.0 + rng.normal(0, 0.0003, len(cal)), index=cal)
    usdt.iloc[-1] = 0.99  # -100bp depeg today
    total = pd.Series(300.0, index=cal)
    total.iloc[-40:] = np.linspace(300, 270, 40)  # -10% in ~40 days = real redemptions
    btc = pd.Series(60000 * np.exp(np.cumsum(rng.normal(0, 0.02, len(cal)))), index=cal)
    board = [{"symbol": "USDT", "circulating_b": 180.0, "price": 0.99}]
    r = moorings.analyze(board, usdt, total, btc)
    assert r["ok"]
    assert r["pegs"][0]["flag"], "-100bp must flag"
    assert r["demand"]["draining"]
    assert r["score"] > 30


# --------------------------------------------------------------------------
# ML Lab
# --------------------------------------------------------------------------

def _ml_inputs(rng, n=1400):
    idx = _bdays(n)
    cal_w = pd.date_range(idx[0], idx[-1], freq="W-WED")
    spread = pd.Series(rng.normal(0, 1.5, len(idx)), index=idx)
    return dict(
        spread_bp=spread,
        tail_bp=pd.Series(np.abs(rng.normal(4, 2, len(idx))), index=idx),
        srf=pd.Series(0.0, index=idx),
        dw_b=pd.Series(2.0, index=cal_w),
        rrp_b=pd.Series(np.linspace(2000, 0, len(idx)), index=idx),
        res_gdp_pctl=pd.Series(np.linspace(1, 0, len(cal_w)), index=cal_w),
        pair_b=pd.Series(np.linspace(300, 900, len(cal_w)), index=cal_w),
        digestion=pd.Series(rng.normal(0, 0.5, len(cal_w)), index=cal_w),
        lite_index=pd.Series(30.0, index=idx),
        lite_pctl=pd.Series(rng.uniform(0, 100, len(idx)), index=idx),
        vix=pd.Series(np.abs(rng.normal(18, 4, len(idx))), index=idx),
        hy_oas=pd.Series(np.abs(rng.normal(3.5, 0.5, len(idx))), index=idx),
        dgs10=pd.Series(4 + np.cumsum(rng.normal(0, 0.02, len(idx))), index=idx),
        inr=pd.Series(90 + np.cumsum(rng.normal(0, 0.1, len(idx))), index=idx),
        usdt_peg_bp=pd.Series(rng.normal(0, 3, len(idx)), index=idx),
        stable_total_b=pd.Series(np.linspace(150, 310, len(idx)), index=idx),
    )


def test_ml_features_label_matches_event_definition(rng):
    from seiche.engines import mlpred
    inputs = _ml_inputs(rng)
    # inject a known spike far from the sample edges
    inputs["spread_bp"].iloc[700] += 30.0
    X, y = mlpred.build_features(**inputs)
    spike_date = inputs["spread_bp"].index[700]
    # the 5 business days BEFORE the spike must be labeled 1
    prior = y.loc[: spike_date].iloc[-6:-1]
    assert prior.sum() >= 4, "days ahead of an injected spike must carry positive labels"


def test_ml_walkforward_survives_late_starting_features(rng):
    from seiche.engines import mlpred
    inputs = _ml_inputs(rng)
    # crypto-style column: entirely NaN for the first 800 days
    inputs["usdt_peg_bp"].iloc[:800] = np.nan
    for loc in range(600, 1300, 60):
        inputs["spread_bp"].iloc[loc] += 25.0
    X, y = mlpred.build_features(**inputs)
    r = mlpred.walk_forward(X, y)
    assert r["ok"], r.get("reason")
    assert 0.0 <= r["p_event_5bd"] <= 1.0
    assert r["validation"]["oos_events"] >= 5
    assert "verdict" in r


# --------------------------------------------------------------------------
# AI context pack
# --------------------------------------------------------------------------

def test_context_pack_is_compact_and_json_safe():
    import json as _json
    from seiche import ai
    snap = {
        "generated_at": "2026-07-07T00:00:00+00:00", "version": "test",
        "engines": {"composite": {"value": 47.7, "regime": "STRAIN", "coverage_pct": 100.0,
                                  "dead_inputs": [], "decomposition": []},
                    "sonar": {"movers": []}, "weather": {}, "kink": {}, "resonance": {},
                    "warehouse": {}, "echo": {}, "basins": {}, "moorings": {}},
        "deep": {"tell": {}, "turn": {}, "ml": {}, "playbook": {}, "backtest": {}},
        "headline": {}, "calendar": {}, "faults": [], "provenance": [{"staleness": "fresh"}],
    }
    pack = ai.context_pack(snap)
    blob = _json.dumps(pack, default=str)
    assert len(blob) < 60_000
    assert pack["composite"]["regime"] == "STRAIN"
    assert pack["provenance_staleness"] == {"fresh": 1}
