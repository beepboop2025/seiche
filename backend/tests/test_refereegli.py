"""Referee GLI tests: reconstruction arithmetic, honest degradation, the
planted-lead recovery check, and the no-dash rule on prose. Synthetic data
only, no network."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from seiche.engines import refereegli

pytestmark = pytest.mark.limit_memory("256 MB")

NO_DASH = ("—", "–")


@pytest.fixture(autouse=True)
def _cheap_bootstrap(monkeypatch):
    """The published intervals use 2000 draws; the tests need the plumbing,
    not the precision."""
    monkeypatch.setattr(refereegli, "REFEREE_BOOT_N", 100)


def _months(n: int, start: str = "2003-01-31") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="ME")


def _inputs(n_months: int = 264, eq_follows_liq_lag: int | None = None,
            seed: int = 11):
    """Synthetic G3 world: balance sheets growing with a planted cycle, flat
    FX, equity either pure noise or following liquidity growth at a lag."""
    rng = np.random.default_rng(seed)
    idx = _months(n_months)
    t = np.arange(n_months)
    cycle = 0.15 * np.sin(2 * np.pi * t / 60.0)
    level = np.exp(np.log(4_000_000.0) + 0.004 * t + cycle)

    fed = pd.Series(level, index=idx)
    ecb = pd.Series(level * 0.9, index=idx)
    boj = pd.Series(level * 6.0, index=idx)          # JPY 100M units
    eur = pd.Series(1.10, index=idx)
    jpy = pd.Series(120.0, index=idx)

    liq_yoy = np.log(pd.Series(level * (1.0 + 0.9 + 6.0 * 100 / 120 / 1.0),
                               index=idx)).diff(12)
    noise = rng.normal(0.0, 0.002, n_months)
    if eq_follows_liq_lag is None:
        ret = pd.Series(rng.normal(0.005, 0.03, n_months), index=idx)
    else:
        ret = liq_yoy.shift(eq_follows_liq_lag).fillna(0.0) * 0.5 + noise
    equity = pd.Series(100.0 * np.exp(ret.cumsum()), index=idx)

    ip = pd.Series(80.0 * np.exp(0.001 * t + 0.02 * np.sin(2 * np.pi * t / 80.0)),
                   index=pd.date_range("2003-01-01", periods=n_months, freq="MS"))
    return dict(fed_assets=fed, ecb_assets=ecb, boj_assets=boj,
                usd_per_eur=eur, jpy_per_usd=jpy, equity=equity, indpro=ip)


# ---------------------------------------------------------------------------
# Reconstruction arithmetic
# ---------------------------------------------------------------------------

def test_g3_sum_and_units():
    r = refereegli.analyze(**_inputs())
    assert r["ok"]
    c = r["latest"]["components_usd_tn"]
    # ECB leg is 0.9x the Fed leg at EURUSD 1.10; BoJ leg is 6.0 * 100 / 120
    assert c["ecb"] == pytest.approx(c["fed"] * 0.9 * 1.10, rel=0.01)
    assert c["boj"] == pytest.approx(c["fed"] * 6.0 * 100 / 120, rel=0.01)
    assert r["latest"]["g3_usd_tn"] == pytest.approx(
        c["fed"] + c["ecb"] + c["boj"], abs=0.02)
    assert r["n_months"] == 264
    assert len(r["series"]) == 264


def test_result_is_json_able_and_dash_free():
    r = refereegli.analyze(**_inputs())
    json.dumps(r)  # numpy leakage would raise here
    for ch in NO_DASH:
        assert ch not in r["method"]
        assert ch not in r["claim1"]["stated"]


# ---------------------------------------------------------------------------
# Honest degradation
# ---------------------------------------------------------------------------

def test_empty_input_fails_loud():
    kw = _inputs()
    kw["ecb_assets"] = pd.Series(dtype=float)
    r = refereegli.analyze(**kw)
    assert not r["ok"]
    assert "ecb" in r["reason"]


def test_short_overlap_fails_loud():
    r = refereegli.analyze(**_inputs(n_months=60))
    assert not r["ok"]
    assert "overlapping months" in r["reason"]


# ---------------------------------------------------------------------------
# The statistics find what is planted and refuse what is not
# ---------------------------------------------------------------------------

def test_planted_six_month_lead_is_recovered():
    r = refereegli.analyze(**_inputs(eq_follows_liq_lag=6))
    assert r["ok"]
    c6 = r["claim1"]["forward_return_corr"]["fwd_6m"]
    assert c6 is not None
    assert c6["corr"] > 0.3
    assert c6["ci95"][0] > 0


def test_pure_noise_equity_earns_no_lead():
    r = refereegli.analyze(**_inputs(eq_follows_liq_lag=None))
    assert r["ok"]
    c6 = r["claim1"]["forward_return_corr"]["fwd_6m"]
    assert c6 is not None
    assert abs(c6["corr"]) < 0.3


def test_walkforward_and_granger_present_on_long_sample():
    r = refereegli.analyze(**_inputs())
    oos = r["claim1"]["walkforward_oos"]
    assert oos is not None
    assert oos["months_high_liq"] + oos["months_low_liq"] > 100
    assert oos["spread_ci95"][0] <= oos["spread_6m_logret"] <= oos["spread_ci95"][1]
    g = r["claim1"]["granger"]["liq_to_returns"]
    assert g is not None and 0.0 <= g["p"] <= 1.0


def test_net_liquidity_reports_unavailable_without_drains():
    r = refereegli.analyze(**_inputs())
    nl = r["robustness_net_liquidity"]
    assert not nl["ok"]
    assert "unavailable" in nl["reason"]


def test_net_liquidity_subtracts_the_drains():
    kw = _inputs()
    idx = kw["fed_assets"].index
    r = refereegli.analyze(**kw, tga=pd.Series(500.0, index=idx),
                           rrp=pd.Series(100.0, index=idx))
    nl = r["robustness_net_liquidity"]
    assert nl["ok"]
    fed_tn = r["latest"]["components_usd_tn"]["fed"]
    assert nl["latest_usd_tn"] == pytest.approx(fed_tn - 0.6, abs=0.02)
    assert set(nl["forward_return_corr"]) == {"fwd_3m", "fwd_6m", "fwd_12m"}


def test_net_liquidity_normalizes_a_millions_tga_print():
    kw = _inputs()
    idx = kw["fed_assets"].index
    in_b = refereegli.analyze(**kw, tga=pd.Series(500.0, index=idx),
                              rrp=pd.Series(100.0, index=idx))
    in_m = refereegli.analyze(**kw, tga=pd.Series(500_000.0, index=idx),
                              rrp=pd.Series(100.0, index=idx))
    assert (in_b["robustness_net_liquidity"]["latest_usd_tn"]
            == in_m["robustness_net_liquidity"]["latest_usd_tn"])


def test_cycle_block_states_its_power_limit():
    r = refereegli.analyze(**_inputs())
    c3 = r["claim3"]
    assert c3["n_months"] == 264 - 12
    # resolution scales as 65^2 / n and must be reported, not hidden
    assert c3["spectral_resolution_at_65m_pm_months"] == pytest.approx(
        65.0 ** 2 / c3["n_months"], abs=0.1)
