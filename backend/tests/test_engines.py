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
