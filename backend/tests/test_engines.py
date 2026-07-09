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
    tidetables,
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
# Tide Tables — analog forecasting
# --------------------------------------------------------------------------

def _planted_motif_spread(rng, n=1200, ramp_d=15, period=70, first=250):
    """Noise with a recurring precursor motif: a ramp that is always followed
    by a spike the next day. The LAST motif ends on the final day — ramp
    complete, spike not yet happened. An analog engine must see it coming."""
    idx = _bdays(n)
    spread = pd.Series(rng.normal(0, 0.3, n), index=idx)
    starts = list(range(first, n - ramp_d - 6, period)) + [n - ramp_d]
    for s in starts:
        spread.iloc[s : s + ramp_d] += np.linspace(0, 10, ramp_d)
        if s + ramp_d < n - 3:  # historical motifs get their spike
            spread.iloc[s + ramp_d] += 25.0
            spread.iloc[s + ramp_d + 1 : s + ramp_d + 4] += [12.0, 6.0, 3.0]
    return spread


def test_tidetables_predicts_planted_pattern(rng):
    spread = _planted_motif_spread(rng)
    r = tidetables.analyze({"x": spread}, spread, warmup=350, k=8)
    assert r["ok"]
    odds = r["event_odds"]
    assert odds["p"] >= 0.7, f"analogs of the planted precursor must flag the spike (got {odds})"
    assert odds["base_rate"] < 0.3
    assert odds["lift"] is not None and odds["lift"] >= 2.0
    # the fan must lean sharply upward: the analogs' next days contain spikes
    assert r["fan"], "forward fan missing"
    assert r["fan"][0]["p75"] > r["spread_now_bp"] + 5.0
    # a repeating motif is well-charted water, not novelty
    assert r["novelty"]["verdict"] != "uncharted"
    # and the hindcast must beat climatology on a sample this rigged
    assert r["skill"]["ok"] and r["skill"]["brier"] < r["skill"]["brier_climatology"]


def test_tidetables_no_look_ahead(rng):
    idx = _bdays(1150)
    spread = pd.Series(rng.normal(0, 2.0, len(idx)), index=idx)
    for s in range(200, 1100, 55):  # real events so the odds vary
        spread.iloc[s] += 18.0
    full = tidetables.analyze({"x": spread}, spread, warmup=300)
    assert full["ok"] and "_hindcast" in full
    hind = full["_hindcast"]
    t = hind.index[-10]
    trunc = spread[spread.index <= t]
    part = tidetables.analyze({"x": trunc}, trunc, warmup=300, with_hindcast=False)
    assert part["ok"]
    assert abs(part["event_odds"]["p"] - float(hind.loc[t])) < 1e-6, \
        "hindcast probability at T differs from the live forecast computed with data up to T only"


def test_tidetables_flags_uncharted_water(rng):
    idx = _bdays(900)
    spread = pd.Series(rng.normal(0, 1.0, len(idx)), index=idx)
    spread.iloc[-20:] = 40.0  # a state the sample has never seen
    r = tidetables.analyze({"x": spread}, spread, warmup=300, with_hindcast=False)
    assert r["ok"]
    assert r["novelty"]["pctl"] is not None and r["novelty"]["pctl"] >= 90
    assert r["novelty"]["verdict"] == "uncharted"


def test_tidetables_refuses_short_history(rng):
    idx = _bdays(200)
    spread = pd.Series(rng.normal(0, 1.0, len(idx)), index=idx)
    r = tidetables.analyze({"x": spread}, spread)
    assert not r["ok"]


# --------------------------------------------------------------------------
# Composite
# --------------------------------------------------------------------------

def test_composite_renormalizes_and_reports_dead():
    subs = {"tails": 50.0, "kink": None, "weather": 20.0, "confession": 0.0,
            "rvxray": None, "resonance": 40.0, "hydrophone": 10.0,
            "undertow": 30.0, "auctions": 30.0, "warehouse": 60.0, "buffers": 90.0}
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


# --------------------------------------------------------------------------
# Gap fixes: Wilson CIs, orthogonal support, embargo, station-keeping
# --------------------------------------------------------------------------

def test_wilson_ci_sane():
    from seiche.engines.backtest import _wilson
    lo, hi = _wilson(8, 13)
    assert 0.0 <= lo <= 8 / 13 <= hi <= 1.0
    assert _wilson(13, 13)[1] == 1.0
    assert _wilson(0, 13)[0] == 0.0
    assert _wilson(1, 0) is None


def test_history_exclude_builds_orthogonal_index(rng):
    h = history.build(**_hist_inputs(800, rng), exclude=("tails",))
    assert "tails" not in h["weights"]
    assert "tails" in h["excluded"]
    assert abs(sum(h["weights"].values()) - 1.0) < 0.01
    assert len(h["index"]) > 300


def test_backtest_capture_and_run_share_stats(rng):
    idx = _bdays(1200)
    spread = pd.Series(rng.normal(0, 1, len(idx)), index=idx)
    for loc in range(400, 1100, 150):
        spread.iloc[loc] += 25.0
    pctl = pd.Series(10.0, index=idx)
    for loc in range(400, 1100, 150):
        pctl.iloc[loc - 4 : loc + 1] = 95.0
    cap = backtest.capture(pctl, spread)
    assert cap["ok"]
    ec = cap["event_capture"]
    assert ec["recall_ci95"] is not None and ec["precision_runs_ci95"] is not None
    assert ec["n_alert_runs"] >= 1
    assert ec["precision_runs"] >= ec["base_rate"]


def test_ml_orthogonal_runs_without_rule_column(rng):
    from seiche.engines import mlpred
    inputs = _ml_inputs(rng)
    for loc in range(600, 1300, 60):
        inputs["spread_bp"].iloc[loc] += 25.0
    X, y = mlpred.build_features(**inputs)
    keep = [c for c in X.columns if c not in mlpred.ORTHOGONAL_DROP]
    r = mlpred.walk_forward(X[keep], y, full_report=False)
    assert r["ok"], r.get("reason")
    assert r["validation"]["auroc_rule_based"] is None
    assert r["top_features"] == [] and r["p_series"] == []
    assert r["utility"]["rule_at_80pctl"] is None
    assert r["validation"]["embargo_bd"] == mlpred.EMBARGO_BD


def test_turn_publishes_both_forecasts(rng):
    idx = _bdays(1500)
    spread = pd.Series(rng.normal(0, 1, len(idx)), index=idx)
    events = resonance._classify_events(idx)
    for m in ("month_end", "quarter_end", "year_end"):
        for ev in events[m]:
            loc = idx.searchsorted(ev)
            if loc < len(idx):
                spread.iloc[loc] += 6.0
    r = turn.analyze(
        spread,
        pd.Series(np.linspace(2400, 0, len(idx)), index=idx),
        pd.Series(np.abs(rng.normal(4, 1, len(idx))), index=idx),
        pd.Series(np.linspace(1.0, 0.0, len(idx)), index=idx),
    )
    nt = r["next_turn"]
    assert "forecast_model_bp" in nt and "forecast_naive_bp" in nt
    assert nt["published"] in ("model", "naive")
    expected = nt["forecast_model_bp"] if nt["published"] == "model" else nt["forecast_naive_bp"]
    assert nt["forecast_bp"] == expected


def test_resonance_reports_ex_max_sensitivity(rng):
    idx = _bdays(1600)
    spread = pd.Series(rng.normal(0, 0.4, len(idx)), index=idx)
    events = resonance._classify_events(idx)
    for k, ev in enumerate(events["quarter_end"]):
        loc = idx.searchsorted(ev)
        if loc < len(idx):
            spread.iloc[loc] += 2.0 + 0.8 * k
    r = resonance.analyze(spread)
    q = r["modes"]["quarter_end"]
    assert q.get("amplification_ex_max") is not None
    assert q["amplification_ex_max"] <= q["amplification"] + 0.5
    assert "low_n" in q


def test_stationkeeping_flags_injected_burn(rng):
    from seiche.engines import stationkeeping
    idx = _bdays(1200)
    widx = pd.date_range(idx[0], idx[-1], freq="W-WED")
    tga = pd.Series(500.0 + np.cumsum(rng.normal(0, 2, len(idx))), index=idx)
    tga.iloc[800:812] += np.cumsum(np.full(12, 25.0))  # +$300B unscheduled build
    rrp = pd.Series(np.abs(rng.normal(5, 1, len(idx))), index=idx)
    walcl = pd.Series(7_000_000 - np.arange(len(widx)) * 5000.0, index=widx)
    r = stationkeeping.analyze(tga, rrp, walcl)
    assert r["ok"]
    tga_alarms = [m for m in r["recent_maneuvers"] if m["channel"] == "TGA"]
    assert tga_alarms, "a $300B unscheduled build must alarm"


# --------------------------------------------------------------------------
# Undertow: critical slowing down must fire on a basin losing damping and
# stay quiet on a stationary one; expanding percentiles must not look ahead
# --------------------------------------------------------------------------

def _ar1_series(rng, n, phi):
    """AR(1) with per-step phi array."""
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = phi[t] * x[t - 1] + rng.normal(0, 1.0)
    return pd.Series(x, index=_bdays(n))


def test_undertow_detects_losing_damping(rng):
    from seiche.engines import undertow
    n = 1500
    phi_flat = np.full(n, 0.5)
    phi_crit = np.full(n, 0.5)
    phi_crit[-300:] = np.linspace(0.5, 0.95, 300)
    tail = pd.Series(np.abs(rng.normal(4, 2, n)), index=_bdays(n))
    calm = undertow.analyze(_ar1_series(rng, n, phi_flat), tail)
    hot = undertow.analyze(_ar1_series(rng, n, phi_crit), tail)
    assert calm["ok"] and hot["ok"]
    assert hot["per_series"]["spread"]["ac1_pctl"] >= 90, "phi->0.95 must read as top-decile AC1"
    assert hot["score"] > calm["score"] + 15


def test_undertow_mechanism_split_names_the_right_cause(rng):
    """Fluctuation-dissipation: rising phi (constant kicks) must read as
    damping loss, rising kick size (constant phi) as louder forcing."""
    from seiche.engines import undertow
    n = 1500
    tail = pd.Series(np.abs(rng.normal(4, 2, n)), index=_bdays(n))

    phi_crit = np.full(n, 0.5)
    phi_crit[-300:] = np.linspace(0.5, 0.95, 300)
    weak_basin = undertow.analyze(_ar1_series(rng, n, phi_crit), tail)
    assert weak_basin["per_series"]["spread"]["mechanism"].startswith(
        ("absorbers weakening", "both")
    )

    phi_flat = np.full(n, 0.5)
    sigma = np.ones(n)
    sigma[-300:] = np.linspace(1.0, 3.0, 300)
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = phi_flat[t] * x[t - 1] + rng.normal(0, sigma[t])
    loud_shocks = undertow.analyze(pd.Series(x, index=_bdays(n)), tail)
    ind = loud_shocks["per_series"]["spread"]
    assert ind["noise_pctl"] >= 80, "3x kick size must read as top-quintile noise power"
    assert ind["mechanism"].startswith(("louder shocks", "both"))


def test_undertow_no_look_ahead(rng):
    from seiche.engines import undertow
    n = 1500
    phi = np.full(n, 0.6)
    spread = _ar1_series(rng, n, phi)
    tail = pd.Series(np.abs(rng.normal(4, 2, n)), index=_bdays(n))
    full = undertow.analyze(spread, tail)
    trunc = undertow.analyze(spread.iloc[:-120], tail.iloc[:-120])
    t = trunc["_damping_pctl"].index[-1]
    assert abs(float(full["_damping_pctl"].loc[t]) - float(trunc["_damping_pctl"].loc[t])) < 1e-9, \
        "damping percentile at T changed when future data was appended — look-ahead leak"


def test_undertow_refuses_short_history(rng):
    from seiche.engines import undertow
    short = pd.Series(rng.normal(0, 1, 100), index=_bdays(100))
    r = undertow.analyze(short, short)
    assert not r["ok"]


# --------------------------------------------------------------------------
# Swell: the forward curve must find the calendar, validate honestly, and
# never look ahead
# --------------------------------------------------------------------------

def _calendar_spiked_spread(rng, n=1600, spike=14.0):
    from seiche.engines import swell
    idx = _bdays(n)
    s = pd.Series(rng.normal(0, 1.2, n), index=idx)
    buckets = swell.classify_days(idx)
    hot = buckets.isin(["quarter_turn", "year_turn"]).to_numpy()
    s[hot] += spike
    return s


def test_swell_buckets_are_disjoint_and_complete():
    from seiche.engines import swell
    idx = _bdays(900)
    b = swell.classify_days(idx)
    assert len(b) == len(idx)
    assert set(b.unique()) <= set(swell.BUCKET_LABELS)
    # spot checks: 2019-12-31 is the year turn; 2019-09-30 the quarter turn
    assert b.loc[pd.Timestamp("2019-12-31")] == "year_turn"
    assert b.loc[pd.Timestamp("2020-01-01")] == "year_turn"  # first bd after
    assert b.loc[pd.Timestamp("2019-09-30")] == "quarter_turn"


def test_swell_finds_the_calendar(rng):
    from seiche.engines import swell
    r = swell.analyze(_calendar_spiked_spread(rng))
    assert r["ok"]
    by = {b["bucket"]: b for b in r["buckets"]}
    assert by["quarter_turn"]["p10"] > by["plain"]["p10"] + 0.2, \
        "quarter turns carry the injected spikes — the curve must know"
    # year turns have ~2 obs/year — shrinkage toward quarter-turn evidence
    # must keep them hot instead of diluting them to plain days
    assert by["year_turn"]["p10"] > by["plain"]["p10"] + 0.15, \
        "year-turn risk must survive its tiny sample via hierarchical shrinkage"
    v = r["validation"]
    assert v["ok"] and v["auroc"] > 0.7 and v["brier"] < v["brier_climatology"]
    assert r["peak"] is not None and r["peak"]["bucket"] in ("quarter_turn", "year_turn")


def test_swell_no_look_ahead(rng):
    from seiche.engines import swell
    s = _calendar_spiked_spread(rng)
    full = swell.analyze(s)
    trunc = swell.analyze(s.iloc[:-90])
    t = trunc["_p5_series"].index[-1]
    assert abs(float(full["_p5_series"].loc[t]) - float(trunc["_p5_series"].loc[t])) < 1e-9, \
        "walk-forward p5 at T changed when future data was appended — look-ahead leak"


def test_swell_state_lift_capped_and_directional(rng):
    from seiche.engines import swell
    n = 1600
    idx = _bdays(n)
    s = pd.Series(rng.normal(0, 1.0, n), index=idx)
    # hot regime in the middle third carries extra pops
    damping = pd.Series(0.0, index=idx)
    damping.iloc[n // 3 : 2 * n // 3] = 90.0
    hot = damping >= 67.0
    s[hot.to_numpy() & (rng.uniform(size=n) < 0.10)] += 12.0
    r = swell.analyze(s, damping_pctl=damping)
    assert r["ok"]
    lift = r["state"]["lift_10bp"]
    assert 0.5 <= lift <= 3.0
    assert lift > 1.2, "exceedances concentrated in the hot state must lift the rate"


def test_swell_refuses_short_history(rng):
    from seiche.engines import swell
    r = swell.analyze(pd.Series(rng.normal(0, 1, 200), index=_bdays(200)))
    assert not r["ok"]


# --------------------------------------------------------------------------
# Review-pass invariants: fleet rule embargo, undertow unresolved pops
# --------------------------------------------------------------------------

def test_undertow_unresolved_pops_do_not_inflate_recovery(rng):
    from seiche.engines import undertow
    n = 1200
    idx = _bdays(n)
    quiet = pd.Series(rng.normal(0, 1.0, n), index=idx)
    tail = pd.Series(np.abs(rng.normal(4, 2, n)), index=idx)
    loud = quiet.copy()
    loud.iloc[-2] += 60.0  # giant pop 2bd before asof — window still open
    a = undertow.analyze(quiet, tail)
    b = undertow.analyze(loud, tail)
    ra = a["per_series"]["spread"]["recovery"]
    rb = b["per_series"]["spread"]["recovery"]
    assert ra["n_recent"] == rb["n_recent"], \
        "an unresolved end-of-sample pop must be excluded, not counted as censored-slow"
    assert rb["halflife_recent_d"] == ra["halflife_recent_d"]


# --------------------------------------------------------------------------
# The Navigator: commitment discipline + forward-only scoring
# --------------------------------------------------------------------------

def test_navigator_parses_and_bounds_commitments():
    from seiche.engines import navigator
    good = navigator.parse_commitment('{"p_event_5bd": 0.07, "rationale": "tails calm (2026-07-07); kink runway wide"}')
    assert good == {"p_event_5bd": 0.07, "rationale": "tails calm (2026-07-07); kink runway wide"}
    fenced = navigator.parse_commitment('```json\n{"p_event_5bd": 0.5, "rationale": "x"}\n```')
    assert fenced is not None and fenced["p_event_5bd"] == 0.5
    assert navigator.parse_commitment('{"p_event_5bd": 1.7, "rationale": "overconfident"}') is None
    assert navigator.parse_commitment("the vibes feel risky, maybe 40%?") is None
    assert navigator.parse_commitment(None) is None


def test_navigator_commits_once_per_day(rng, tmp_path, monkeypatch):
    import asyncio
    from seiche import store
    from seiche.engines import navigator
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "nav.sqlite")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    calls = {"n": 0}

    async def llm(messages):
        calls["n"] += 1
        return '{"p_event_5bd": 0.12, "rationale": "test"}'

    first = asyncio.run(navigator.commit({"composite": {}}, "2026-07-07", llm=llm))
    second = asyncio.run(navigator.commit({"composite": {}}, "2026-07-07", llm=llm))
    assert first["ok"] and second["ok"]
    assert calls["n"] == 1, "the model must not be consulted twice for one data-day"
    assert second.get("cached") is True
    assert second["p_event_5bd"] == first["p_event_5bd"]


def test_navigator_fails_loud_without_endpoint(tmp_path, monkeypatch):
    import asyncio
    from seiche import store
    from seiche.engines import navigator
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "nav.sqlite")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)

    async def llm(messages):
        return None

    r = asyncio.run(navigator.commit({}, "2026-07-08", llm=llm))
    assert not r["ok"] and "ashore" in r["reason"]

    async def garbage(messages):
        return "I think markets will be fine."

    r = asyncio.run(navigator.commit({}, "2026-07-09", llm=garbage))
    assert not r["ok"] and "malformed" in r["reason"]


def test_navigator_scores_forward_record_only(rng):
    from seiche.engines import navigator
    n = 400
    idx = _bdays(n, "2024-01-01")
    spread = pd.Series(rng.normal(0, 1, n), index=idx)
    spread.iloc[150] += 25.0  # one real event
    # 30 as-published forecasts: high p right before the event, low elsewhere
    recs = []
    for i in range(100, 160, 2):
        p = 0.8 if 144 <= i < 150 else 0.05
        recs.append({"date": idx[i].date().isoformat(),
                     "forecasts": {"views": {"navigator": p}}})
    # plus one too fresh to resolve (window still open)
    recs.append({"date": idx[-2].date().isoformat(),
                 "forecasts": {"views": {"navigator": 0.5}}})
    out = navigator.score_record(recs, spread)
    assert out["ok"]
    assert out["n_resolved"] == 30
    assert out["n_pending"] == 1
    assert out["brier"] < out["brier_climatology"], "prescient synthetic forecasts must beat climatology"


# --------------------------------------------------------------------------
# Communiqué: deterministic lexicon scoring on vintage-stamped statements
# --------------------------------------------------------------------------

def test_communique_scores_and_flags_stress_language():
    from seiche.engines import communique
    calm = ("The Committee decided to maintain the target range. Inflation has cooled and "
            "longer-term expectations remain well anchored. Economic activity expanded at a "
            "moderate pace. " * 12)
    stressy = ("The Committee decided to maintain the target range. Money market conditions "
               "warrant attention: funding pressures emerged in repurchase agreement markets "
               "and the standing repo facility supported smooth market functioning amid "
               "liquidity strains. " * 12)
    texts = {f"2024-0{m}-01": calm for m in range(1, 8)}
    texts["2024-09-01"] = stressy
    r = communique.analyze(texts)
    assert r["ok"] and r["n_statements"] == 8
    assert r["latest"]["stress_score"] > 0
    assert r["latest"]["stress_score_chg"] > 0
    assert any("funding-stress vocabulary" in f for f in r["flags"]), \
        "a statement suddenly full of repo vocabulary must flag"
    assert not communique.analyze({})["ok"]


def test_communique_is_deterministic():
    from seiche.engines import communique
    text = "The Committee remains vigilant; further tightening and balance sheet reduction continue."
    assert communique.score_text(text) == communique.score_text(text)
    s = communique.score_text(text)
    assert s["hawk_score"] > 0 and s["bs_tighten"] > 0


# --------------------------------------------------------------------------
# Transfer learning: TED-era pretraining mechanics
# --------------------------------------------------------------------------

def test_pretrain_rows_share_feature_slots(rng):
    from seiche.engines import mlpred
    idx = pd.bdate_range("1995-01-01", periods=4000)
    ted = pd.Series(np.abs(rng.normal(0.4, 0.15, len(idx))), index=idx)
    ted.iloc[2000] += 1.2  # a 120bp TED pop
    pre = mlpred.build_pretrain_rows(ted)
    assert pre is not None
    X_pre, y_pre = pre
    assert {"spread_lvl", "spread_chg5", "spread_ez", "bd_to_mend", "bd_to_qend", "bd_to_tax"} \
        <= set(X_pre.columns)
    assert y_pre.sum() >= 1, "the injected TED pop must label"
    assert X_pre.index.max() < pd.Timestamp("2018-04-01"), "no overlap with the SOFR era"
    # too little history refuses
    assert mlpred.build_pretrain_rows(ted.iloc[:100]) is None


def test_walk_forward_with_pretrain_scores_same_days(rng):
    from seiche.engines import mlpred
    inputs = _ml_inputs(rng)
    for loc in range(600, 1300, 60):
        inputs["spread_bp"].iloc[loc] += 25.0
    X, y = mlpred.build_features(**inputs)
    pre_idx = pd.bdate_range("2012-01-01", periods=1500)
    X_pre = pd.DataFrame({
        "spread_lvl": np.abs(rng.normal(20, 8, len(pre_idx))),
        "spread_chg5": rng.normal(0, 4, len(pre_idx)),
    }, index=pre_idx)
    y_pre = pd.Series((rng.uniform(size=len(pre_idx)) < 0.03).astype(float), index=pre_idx)
    solo = mlpred.walk_forward(X, y, full_report=False)
    pooled = mlpred.walk_forward(X, y, full_report=False, pre=(X_pre, y_pre))
    assert solo["ok"] and pooled["ok"]
    assert pooled["validation"]["oos_days"] == solo["validation"]["oos_days"], \
        "pretraining adds training mass, never scored days"
    assert pooled["validation"]["pretrain_rows"] == len(X_pre)
    assert 0.0 <= pooled["p_event_5bd"] <= 1.0


# --------------------------------------------------------------------------
# Riptide: pop grammar, discriminators, walk-forward honesty
# --------------------------------------------------------------------------

def _riptide_world(rng, n=3200):
    """Turn pops co-signed by RRP mean-revert; plain-day pops without the
    co-sign stick and escalate — the grammar Riptide must learn."""
    from seiche.engines import swell as sw
    idx = _bdays(n)
    s = pd.Series(rng.normal(0, 0.8, n), index=idx)
    rrp = pd.Series(np.abs(rng.normal(50, 5, n)), index=idx)
    buckets = sw.classify_days(idx).to_numpy()
    for i in range(60, n - 25):
        if buckets[i] in ("quarter_turn", "year_turn"):
            s.iloc[i] += 8.0                    # scheduled pop...
            rrp.iloc[i] += 60.0                 # ...with its RRP co-sign; fades next day
        elif rng.uniform() < 0.02:
            s.iloc[i : i + 6] += 9.0            # scarcity pop: sticks, no co-sign
            if rng.uniform() < 0.5:
                s.iloc[i + 4] += 6.0            # and sometimes escalates
    return s, rrp


def test_riptide_learns_the_cosign_grammar(rng):
    from seiche.engines import riptide
    s, rrp = _riptide_world(rng)
    r = riptide.analyze(s, rrp)
    assert r["ok"], r.get("reason")
    v = r["validation"]["sticky"]
    assert v.get("auroc") is not None and v["auroc"] > 0.65, \
        f"co-sign grammar must be learnable (AUROC {v.get('auroc')})"
    P = riptide.extract_pops(s, rrp, None)
    # turn pops carry the co-sign; plain scarcity pops don't
    turn = P[P["is_turn"] == 1.0]
    plain = P[P["is_turn"] == 0.0]
    if len(turn) > 5 and len(plain) > 5:
        assert turn["rrp_co_z"].median() > plain["rrp_co_z"].median()
        assert plain["sticky"].mean(skipna=True) > turn["sticky"].mean(skipna=True)


def test_riptide_open_windows_get_no_verdict(rng):
    from seiche.engines import riptide
    s, rrp = _riptide_world(rng)
    # a pop 2bd before the sample end that has NOT given back — undecidable
    s.iloc[-2] += 9.0
    s.iloc[-1] = s.iloc[-2] + 0.2   # still riding high at the sample edge
    P = riptide.extract_pops(s, rrp, None)
    last = P.iloc[-1]
    assert last["date"] == s.index[-2]
    assert pd.isna(last["sticky"]), "an undecided open window must carry NO verdict"
    assert pd.isna(last["escalates"])
    # but early resolution IS a verdict: a pop that gives back half by day 1
    # is decidedly chop, and appending future data can never change that
    s2, rrp2 = _riptide_world(rng)
    s2.iloc[-2] += 9.0              # noise next day = immediate give-back
    P2 = riptide.extract_pops(s2, rrp2, None)
    assert P2.iloc[-1]["sticky"] == 0.0


def test_riptide_refuses_thin_history(rng):
    from seiche.engines import riptide
    idx = _bdays(300)
    r = riptide.analyze(pd.Series(rng.normal(0, 0.5, 300), index=idx),
                        pd.Series(50.0, index=idx))
    assert not r["ok"]


# --------------------------------------------------------------------------
# Breakwater: the revealed reaction function
# --------------------------------------------------------------------------

def test_breakwater_reveals_the_pain_threshold(rng):
    from seiche.engines import breakwater
    n = 2000
    idx = _bdays(n)
    s = pd.Series(rng.normal(2, 1.0, n), index=idx)
    interventions = []
    for loc in (600, 1100, 1600):
        s.iloc[loc - 15 : loc] += np.linspace(2, 18, 15)   # stress builds...
        interventions.append({"date": idx[loc].date().isoformat(),
                              "label": f"rescue {loc}", "kind": "test"})
        s.iloc[loc : loc + 10] -= np.linspace(0, 12, 10)   # ...rescue fades it
    srf = pd.Series(0.0, index=idx)
    r = breakwater.analyze(s, srf, interventions=interventions)
    assert r["ok"], r.get("reason")
    assert r["revealed_threshold"]["n"] == 3
    assert r["revealed_threshold"]["median_pctl"] > 90, \
        "rescues that arrive at stress peaks must reveal a high threshold"
    assert r["current"]["spread_pctl"] < r["revealed_threshold"]["median_pctl"]
    assert 0 <= r["rescue_proximity"] <= 100


def test_breakwater_refuses_thin_catalog(rng):
    from seiche.engines import breakwater
    idx = _bdays(900)
    s = pd.Series(rng.normal(2, 1, 900), index=idx)
    r = breakwater.analyze(s, pd.Series(0.0, index=idx),
                           interventions=[{"date": "2010-01-01", "label": "x", "kind": "y"}])
    assert not r["ok"]


# --------------------------------------------------------------------------
# Venn-Abers: the calibrated band's finite-sample sanity
# --------------------------------------------------------------------------

def test_venn_abers_band_is_ordered_and_bounded(rng):
    from seiche.engines.stacker import _venn_abers
    p = rng.uniform(0, 1, 400)
    y = (rng.uniform(size=400) < p).astype(float)   # perfectly calibrated world
    band = _venn_abers(p, y, 0.3)
    assert band is not None
    assert 0.0 <= band["p0"] <= band["p1"] <= 1.0
    assert band["p1"] - band["p0"] < 0.3, "dense calibrated data must give a tight band"
    # miscalibrated world: model says 0.8, reality is 0.2 — band must drag down
    p2 = np.full(400, 0.8) + rng.normal(0, 0.02, 400)
    y2 = (rng.uniform(size=400) < 0.2).astype(float)
    band2 = _venn_abers(p2, y2, 0.8)
    assert band2 is not None and band2["p1"] < 0.5, \
        "Venn-Abers must override a miscalibrated point forecast"


# --------------------------------------------------------------------------
# Bathymetry: the fitted dynamics must recover a known potential, slow down
# spectrally near criticality, point the arrow at driven systems, escape
# faster from a flat well — and never look ahead
# --------------------------------------------------------------------------

def _ou_series(rng, n, phi, sigma, start="2018-01-01"):
    """Discrete OU / AR(1): a single quadratic well of stiffness (1 - phi)."""
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + rng.normal(0, sigma)
    return pd.Series(x, index=_bdays(n, start))


def _series_with_pop(x: np.ndarray, start="2018-01-01") -> pd.Series:
    """Construct a spread whose pop statistic (s minus its trailing 5bd
    median, backtest.pop_bp) is EXACTLY the given process x: s_t =
    median(s_{t-5..t-1}) + x_t. This lets the tests control the dynamics the
    engine is supposed to recover."""
    s = list(x[:3])
    for t in range(3, len(x)):
        s.append(float(np.median(s[-5:])) + float(x[t]))
    return pd.Series(s, index=_bdays(len(x), start))


def _pop_like(rng, n, phi, sigma):
    return _series_with_pop(_ou_series(rng, n, phi, sigma).to_numpy())


def test_bathymetry_recovers_single_well(rng):
    from seiche.engines import bathymetry
    r = bathymetry.analyze(_pop_like(rng, 2000, 0.6, 1.5))
    assert r["ok"], r.get("reason")
    fl = r["floor"]
    assert fl["ok"]
    assert abs(fl["well_bp"]) <= 2.0, "an OU well centered at 0 must be found near 0"
    assert fl["stiffness"] > 0, "mean reversion must read as positive restoring stiffness"
    # drift must point back to the well: negative on the right flank
    right = [row for row in fl["curve"] if row[0] >= 3.0 and row[4] >= 5]
    assert right and all(row[2] < 0 for row in right), \
        "D1 must be negative above the well (restoring drift)"


def test_bathymetry_spectral_gap_closes_near_criticality(rng):
    from seiche.engines import bathymetry
    calm = bathymetry.analyze(_pop_like(rng, 2000, 0.35, 1.2))
    crit = bathymetry.analyze(_pop_like(rng, 2000, 0.93, 1.2))
    assert calm["ok"] and crit["ok"]
    assert crit["spectrum"]["tau_bd"] > calm["spectrum"]["tau_bd"] * 1.5, \
        "phi -> 1 must read as a longer relaxation time (smaller spectral gap)"
    assert crit["spectrum"]["gap"] < calm["spectrum"]["gap"]


def test_bathymetry_arrow_points_at_driven_systems(rng):
    from seiche.engines import bathymetry
    n = 1800
    # reversible world: OU noise around a well
    ou = bathymetry.analyze(_pop_like(rng, n, 0.5, 1.5))
    # driven world: a deterministic cycle 0 -> 3 -> 6 -> 0 plus small noise —
    # probability current flows around a loop, never balancing pairwise
    cyc = np.tile([0.0, 3.0, 6.0], n // 3 + 1)[:n] + rng.normal(0, 0.3, n)
    drv = bathymetry.analyze(_series_with_pop(cyc))
    assert ou["ok"] and drv["ok"]
    assert ou["arrow"]["sigma_nats_bd"] >= 0 and drv["arrow"]["sigma_nats_bd"] >= 0, \
        "entropy production is non-negative by construction"
    assert drv["arrow"]["sigma_nats_bd"] > 3 * ou["arrow"]["sigma_nats_bd"], \
        "a cyclically driven system must produce far more entropy than a reversible one"


def test_bathymetry_flat_well_escapes_faster(rng):
    from seiche.engines import bathymetry
    deep = bathymetry.analyze(_pop_like(rng, 2000, 0.4, 1.0))
    flat = bathymetry.analyze(_pop_like(rng, 2000, 0.9, 3.0))
    assert deep["ok"] and flat["ok"]
    assert (flat["p_event_5bd"] or 0.0) > (deep["p_event_5bd"] or 0.0), \
        "a hot, weakly-damped basin must show higher first-passage probability"
    if deep["mfpt_bd"] is not None and flat["mfpt_bd"] is not None:
        assert flat["mfpt_bd"] < deep["mfpt_bd"]
    # in the hot world events actually happen — the walk-forward must rank
    v = flat["validation"]
    assert v["ok"] and v["n_events"] > 10
    assert v["auroc"] is not None and v["auroc"] > 0.5


def test_bathymetry_no_look_ahead(rng):
    from seiche.engines import bathymetry
    s = _pop_like(rng, 1800, 0.8, 2.5)
    full = bathymetry.analyze(s)
    trunc = bathymetry.analyze(s.iloc[:-90])
    t = trunc["_p5_series"].index[-1]
    assert abs(float(full["_p5_series"].loc[t]) - float(trunc["_p5_series"].loc[t])) < 1e-9, \
        "walk-forward first-passage p at T changed when future data was appended — look-ahead leak"


def test_bathymetry_refuses_short_history(rng):
    from seiche.engines import bathymetry
    r = bathymetry.analyze(pd.Series(rng.normal(0, 1, 300), index=_bdays(300)))
    assert not r["ok"]
