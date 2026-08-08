"""Oil × Funding: identities, payload honesty, and no-look-ahead diagnostics."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from seiche.config import (
    ALL_SERIES,
    OIL_FUNDING_EIA_SERIES,
    OIL_FUNDING_FRED_SERIES,
)
from seiche.engines import oilfunding


def _constant_world(n: int = 800) -> dict[str, pd.Series]:
    index = pd.bdate_range("2021-01-04", periods=n)
    cushing_n = n // 5
    cushing_index = pd.date_range(index[0], periods=cushing_n, freq="W-FRI")
    bill = pd.Series(4.0, index=index)
    return {
        "wti": pd.Series(80.0, index=index),
        "brent": pd.Series(84.0, index=index),
        "sofr": pd.Series(5.0, index=index),
        "iorb": pd.Series(4.98, index=index),
        "cp_nonfinancial_3m": bill + 0.20,
        "cp_financial_3m": bill + 0.30,
        "treasury_3m": bill,
        "inr_per_usd": pd.Series(84.0, index=index),
        "energy_cpi": pd.Series(np.linspace(250.0, 280.0, n), index=index),
        "core_cpi": pd.Series(np.linspace(290.0, 320.0, n), index=index),
        "foreign_treasury_custody": pd.Series(
            np.linspace(3_000_000.0, 3_200_000.0, n), index=index
        ),
        "foreign_official_rrp": pd.Series(
            np.linspace(200_000.0, 240_000.0, n), index=index
        ),
        "cushing_stocks": pd.Series(
            np.linspace(32_000.0, 21_000.0, cushing_n), index=cushing_index
        ),
    }


def _varying_world(n: int = 1200) -> dict[str, pd.Series]:
    index = pd.bdate_range("2019-01-02", periods=n)
    cushing_n = n // 5
    cushing_index = pd.date_range(index[0], periods=cushing_n, freq="W-FRI")
    rng = np.random.default_rng(17)
    oil_change = rng.normal(0.02, 1.2, n)
    wti = pd.Series(65.0 + np.cumsum(oil_change), index=index)
    bill = pd.Series(2.0 + np.linspace(0.0, 2.0, n), index=index)
    # Plant a relationship between five-day oil moves and five-day changes in
    # the CP spread level, which is exactly what the public scatter estimates.
    cp_spread_bp = (
        22.0 + 0.45 * (wti.to_numpy() - wti.iloc[0]) + rng.normal(0.0, 0.7, n)
    )
    sofr_spread_bp = -2.0 + 0.25 * oil_change + rng.normal(0.0, 0.7, n)
    iorb = pd.Series(2.25 + np.linspace(0.0, 2.0, n), index=index)
    return {
        "wti": wti,
        "brent": wti + 4.5 + rng.normal(0.0, 0.3, n),
        "sofr": iorb + pd.Series(sofr_spread_bp / 100.0, index=index),
        "iorb": iorb,
        "cp_nonfinancial_3m": bill + pd.Series(cp_spread_bp / 100.0, index=index),
        "cp_financial_3m": bill + pd.Series((cp_spread_bp + 8.0) / 100.0, index=index),
        "treasury_3m": bill,
        "inr_per_usd": pd.Series(
            72.0 + np.cumsum(0.002 * oil_change + rng.normal(0.0, 0.03, n)),
            index=index,
        ),
        "energy_cpi": pd.Series(
            240.0 + np.cumsum(rng.normal(0.08, 0.2, n)), index=index
        ),
        "core_cpi": pd.Series(
            270.0 + np.cumsum(rng.normal(0.07, 0.05, n)), index=index
        ),
        "foreign_treasury_custody": pd.Series(
            3_000_000.0 + np.cumsum(rng.normal(120.0, 900.0, n)), index=index
        ),
        "foreign_official_rrp": pd.Series(
            220_000.0 + np.cumsum(rng.normal(20.0, 180.0, n)), index=index
        ),
        "cushing_stocks": pd.Series(
            35_000.0 + np.cumsum(rng.normal(-15.0, 310.0, cushing_n)),
            index=cushing_index,
        ),
    }


def test_public_oil_series_are_keyless_fred_contracts() -> None:
    by_name = {spec.mnemonic: spec for spec in OIL_FUNDING_FRED_SERIES}
    assert set(by_name) == {
        "WTI_SPOT", "BRENT_SPOT", "HENRY_HUB_SPOT", "ENERGY_CPI", "CORE_CPI"
    }
    assert by_name["WTI_SPOT"].remote_id == "DCOILWTICO"
    assert by_name["BRENT_SPOT"].remote_id == "DCOILBRENTEU"
    assert by_name["HENRY_HUB_SPOT"].remote_id == "DHHNGSP"
    assert by_name["ENERGY_CPI"].remote_id == "CPIENGSL"
    assert by_name["CORE_CPI"].remote_id == "CPILFESL"
    assert all(spec.source == "fred" for spec in by_name.values())
    assert set(by_name) <= set(ALL_SERIES)

    assert len(OIL_FUNDING_EIA_SERIES) == 1
    cushing = OIL_FUNDING_EIA_SERIES[0]
    assert cushing.mnemonic == "CUSHING_STOCKS"
    assert cushing.remote_id == "W_EPC0_SAX_YCUOK_MBBL"
    assert cushing.source == "eia" and cushing.freq == "W"
    assert cushing.mnemonic in ALL_SERIES


def test_scenario_reproduces_the_channel_identities() -> None:
    out = oilfunding.analyze(**_constant_world())
    assert out["ok"]
    scenario = out["scenario"]["outputs"]

    expected_carry = 80.0 * 0.055 * 90.0 / 365.0 + 0.03 * 90.0
    assert scenario["carry"]["required_contango_usd_per_bbl"] == pytest.approx(
        expected_carry, abs=0.001
    )
    assert scenario["trade_finance"]["cargo_credit_usd"] == 160_000_000
    assert (
        scenario["trade_finance"]["incremental_voyage_working_capital_usd"]
        == 480_000_000
    )
    assert scenario["margin"]["same_day_liquidity_demand_usd"] == 12_000_000
    assert scenario["india"]["annual_import_bill_change_usd"] == 18_250_000_000
    assert (
        scenario["india"]["rbi_unreplenished_liquidity_absorption_inr"]
        == 126_000_000_000
    )
    assert scenario["india"]["omc_cp_funding_demand_inr"] == 120_000_000_000


def test_live_spreads_keep_units_and_signs_separate() -> None:
    out = oilfunding.analyze(**_constant_world())
    live = out["live"]
    assert live["cp_nonfinancial"]["spread_bp"] == pytest.approx(20.0)
    assert live["cp_financial"]["spread_bp"] == pytest.approx(30.0)
    assert live["sofr_iorb"]["spread_bp"] == pytest.approx(2.0)
    assert live["wti"]["price_usd_per_bbl"] == 80.0
    assert live["inr"]["per_usd"] == 84.0
    assert live["brent_wti_spread"]["brent_minus_wti_usd_per_bbl"] == 4.0


def test_market_structure_keeps_live_reference_and_interpretive_data_separate() -> None:
    world = _constant_world()
    out = oilfunding.analyze(**world)
    live = out["live"]["cushing"]
    structure = out["market_structure"]

    assert live["stocks_m_bbl"] == 21.0
    assert live["fill_of_last_working_capacity_pct"] == pytest.approx(26.8)
    assert live["buffer_to_20m_reference_m_bbl"] == 1.0
    assert live["change_1w_m_bbl"] == pytest.approx(
        round(float(world["cushing_stocks"].diff().iloc[-1]) / 1000.0, 3)
    )
    assert structure["cushing"]["working_capacity_m_bbl"] == 78.410
    assert structure["cushing"]["capacity_asof"] == "2024-03-31"
    assert "not a universal" in structure["cushing"]["stress_reference_status"]
    assert structure["chokepoints"]["latest_period"] == "1Q26"
    assert structure["chokepoints"]["live_status"].startswith("not asserted")
    assert structure["india"]["non_hormuz_crude_routing_pct"] == 70.0
    assert out["charts"]["cushing_inventory"]["rows"]
    assert out["charts"]["brent_wti_spread"]["rows"]


def test_coupling_uses_changes_and_recovers_planted_association() -> None:
    out = oilfunding.analyze(**_varying_world())
    fit = out["charts"]["scatter"]["fit"]
    assert fit["n"] >= 100
    assert fit["correlation"] > 0.4
    assert fit["slope_bp_per_usd"] > 0
    assert out["charts"]["coupling"]["rows"]


def test_truncation_does_not_change_closed_rolling_correlation_rows() -> None:
    world = _varying_world()
    cutoff = world["wti"].index[-151]
    full = oilfunding.analyze(**world)
    truncated = oilfunding.analyze(
        **{name: series[series.index <= cutoff] for name, series in world.items()}
    )
    cutoff_text = cutoff.date().isoformat()
    full_closed = {
        row[0]: row[1:]
        for row in full["charts"]["coupling"]["rows"]
        if row[0] <= cutoff_text
    }
    truncated_closed = {
        row[0]: row[1:] for row in truncated["charts"]["coupling"]["rows"]
    }
    # Adaptive chart sampling deliberately changes which old dates are shown
    # as the replay endpoint moves. Values on every shared closed date must be
    # invariant; that is the actual no-look-ahead contract.
    shared_dates = sorted(set(full_closed) & set(truncated_closed))
    assert len(shared_dates) >= 20
    assert {date: full_closed[date] for date in shared_dates} == {
        date: truncated_closed[date] for date in shared_dates
    }


def test_payload_is_json_safe_context_not_a_composite_score() -> None:
    out = oilfunding.analyze(**_varying_world())
    assert json.dumps(out)
    assert out["ok"] and out["asof"]
    assert set(out["channel_directions"]) == {
        "cost_of_carry",
        "trade_finance",
        "margin",
        "india_external",
        "india_fx_liquidity",
        "india_omc",
        "petrodollar_recycling",
        "inflation_policy",
    }
    assert out["charts"]["inflation_policy"]["rows"]
    assert out["charts"]["official_dollar_parking"]["rows"]

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value)) if value else set()
        return set()

    assert "score" not in keys(out)
    assert any("scenario" in caveat.lower() for caveat in out["caveats"])


def test_refuses_to_speak_without_oil_or_funding_history() -> None:
    world = _constant_world()
    world["wti"] = world["wti"].iloc[:20]
    out = oilfunding.analyze(**world)
    assert not out["ok"] and "WTI" in out["reason"]

    world = _constant_world()
    empty = pd.Series(dtype=float)
    world["sofr"] = empty
    world["iorb"] = empty
    world["cp_nonfinancial_3m"] = empty
    world["cp_financial_3m"] = empty
    out = oilfunding.analyze(**world)
    assert not out["ok"] and "funding spread" in out["reason"]
