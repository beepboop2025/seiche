"""Ballast commodity-to-cash identities, boundaries, and replay behavior."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from seiche import assemble
from seiche.engines import ballast
from seiche.sources.base import Series


def _positions(dates: pd.DatetimeIndex) -> pd.DataFrame:
    rows: list[dict] = []
    for contract in ("WTI", "HENRY_HUB"):
        for date in dates:
            rows.append({
                "date": date,
                "contract": contract,
                "open_interest_all": 1_000_000,
                "prod_merc_positions_long": 200_000,
                "prod_merc_positions_short": 300_000,
                "swap_positions_long_all": 150_000,
                "swap__positions_short_all": 180_000,
                "m_money_positions_long_all": 350_000,
                "m_money_positions_short_all": 250_000,
                "other_rept_positions_long": 100_000,
                "other_rept_positions_short": 80_000,
                "conc_gross_le_4_tdr_long": 11.0,
                "conc_gross_le_4_tdr_short": 22.0,
                "conc_gross_le_8_tdr_long": 18.0,
                "conc_gross_le_8_tdr_short": 31.0,
            })
    return pd.DataFrame(rows)


def _world(periods: int = 80) -> dict:
    dates = pd.date_range("2022-01-04", periods=periods, freq="W-TUE")
    wti_values = 60.0 + np.arange(periods, dtype=float)
    wti_values[-1] += 9.0  # final weekly move is +$10/bbl
    gas_values = 3.0 + np.arange(periods, dtype=float) * 0.05
    gas_values[-1] += 0.45  # final weekly move is +$0.50/MMBtu

    days = pd.bdate_range(dates[0], dates[-1])
    weeks = pd.date_range(dates[0] + pd.Timedelta(days=3), periods=periods, freq="W-FRI")
    return {
        "commodity_positions": _positions(dates),
        "prices": {
            "WTI_SPOT": pd.Series(wti_values, index=dates),
            "HENRY_HUB_SPOT": pd.Series(gas_values, index=dates),
        },
        "crude_stocks_ex_spr": pd.Series(
            410_000.0 + np.sin(np.arange(periods)) * 2_000.0,
            index=weeks,
        ),
        "sofr": pd.Series(5.02, index=days),
        "iorb": pd.Series(5.00, index=days),
        "cp_nonfinancial_3m": pd.Series(5.20, index=days),
        "treasury_3m": pd.Series(5.00, index=days),
    }


def test_wti_gross_displacement_identity_and_paying_side() -> None:
    out = ballast.analyze(**_world())

    assert out["ok"] is True
    assert out["context_only"] is True
    assert "score" not in out and "probability" not in out
    assert out["headline"]["state"] == "ACUTE"
    assert out["headline"]["composite_status"] == "never_enters_seiche_composite"

    wti = next(row for row in out["contracts"] if row["key"] == "WTI")
    cash = wti["cash_transfer_scale"]
    assert wti["price_proxy"]["change_since_prior_report"] == 10.0
    assert pd.Timestamp(wti["available_asof"]) == (
        pd.Timestamp(wti["report_asof"]) + pd.Timedelta(days=3)
    )
    assert cash["gross_mark_displacement_usd"] == pytest.approx(10_000_000_000)
    assert cash["category_proxies_usd"]["producer_merchant"] == pytest.approx(
        3_000_000_000
    )
    assert cash["direction"] == "shorts_pay_if_proxy_tracks_settlement"
    assert wti["positioning"]["top4_paying_side_pct"] == 22.0
    assert cash["status"] == "derived_gross_scale_not_observed_margin_call"
    assert cash["gross_displacement_percentile_5y"] > 95.0


def test_falling_proxy_selects_reported_longs_as_paying_side() -> None:
    world = _world()
    wti = world["prices"]["WTI_SPOT"].copy()
    wti.iloc[-1] = wti.iloc[-2] - 7.0
    world["prices"]["WTI_SPOT"] = wti

    out = ballast.analyze(**world)
    contract = next(row for row in out["contracts"] if row["key"] == "WTI")

    assert contract["cash_transfer_scale"]["direction"] == (
        "longs_pay_if_proxy_tracks_settlement"
    )
    assert contract["cash_transfer_scale"]["category_proxies_usd"][
        "producer_merchant"
    ] == pytest.approx(1_400_000_000)
    assert contract["positioning"]["top4_paying_side_pct"] == 11.0


def test_flat_channels_are_midrank_not_false_tail_events() -> None:
    out = ballast.analyze(**_world())

    assert out["funding"]["sofr_iorb"]["percentile_3y"] == 50.0
    assert out["funding"]["cp_nonfinancial"]["percentile_3y"] == 50.0


def test_funding_is_an_amplifier_not_a_commodity_state_trigger() -> None:
    world = _world()
    dates = world["prices"]["WTI_SPOT"].index
    world["prices"]["WTI_SPOT"] = pd.Series(
        60.0 + np.arange(len(dates), dtype=float), index=dates
    )
    world["prices"]["HENRY_HUB_SPOT"] = pd.Series(
        3.0 + np.arange(len(dates), dtype=float) * 0.05, index=dates
    )
    world["crude_stocks_ex_spr"] = pd.Series(
        410_000.0, index=world["crude_stocks_ex_spr"].index
    )
    sofr = world["sofr"].copy()
    sofr.iloc[-1] = 6.0
    world["sofr"] = sofr

    out = ballast.analyze(**world)

    assert out["headline"]["state"] == "CALM"
    assert out["headline"]["worst_channel_percentile"] == 50.0
    overlay = out["headline"]["funding_overlay"]
    assert overlay["status"] == "TAIL_RELATIVE"
    assert overlay["dominant_channel"]["channel"] == "dollar_funding"
    assert overlay["role"] == "amplifier_not_commodity_state_trigger"


def test_payload_is_json_safe_and_carries_dark_data_boundaries() -> None:
    out = ballast.analyze(**_world())

    json.dumps(out, allow_nan=False)
    statuses = {row["status"] for row in out["coverage"]["boundaries"]}
    assert "dark" in statuses
    assert "undertow_handoff_requires_licensed_depth" in statuses
    assert "named institution" in out["handoffs"]["liquilens"]["boundary"]
    assert any("not an observed variation-margin call" in c for c in out["caveats"])


def test_insufficient_cot_history_fails_loud() -> None:
    world = _world(periods=30)
    out = ballast.analyze(**world)

    assert out == {
        "ok": False,
        "reason": "insufficient aligned CFTC positioning and public benchmark history",
    }


def _series(mnemonic: str, points: pd.Series) -> Series:
    return Series(
        mnemonic=mnemonic,
        source="test",
        remote_id=mnemonic,
        label=mnemonic,
        unit="test",
        freq="W",
        fetched_at="2026-01-01T00:00:00+00:00",
        points=points,
    )


def test_time_machine_truncates_new_weekly_sources_without_mutating_live_data() -> None:
    world = _world()
    cut = world["prices"]["WTI_SPOT"].index[-10]
    positions = world["commodity_positions"]
    stocks = world["crude_stocks_ex_spr"]
    src = {
        "eia_inventory": {
            "CRUDE_STOCKS_EX_SPR": _series("CRUDE_STOCKS_EX_SPR", stocks)
        },
        "commodity_cot": {
            "fetched_at": "2026-01-01T00:00:00+00:00",
            "positions": positions,
        },
    }

    replay = assemble._truncate_sources(src, cut)

    replay_stocks = replay["eia_inventory"]["CRUDE_STOCKS_EX_SPR"].points
    replay_positions = replay["commodity_cot"]["positions"]
    assert replay_stocks.index.max() <= cut
    assert replay_positions["date"].max() <= cut
    assert (replay_stocks.index + pd.Timedelta(days=5) <= cut).all()
    assert (replay_positions["date"] + pd.Timedelta(days=3) <= cut).all()
    assert len(stocks) > len(replay_stocks)
    assert len(positions) > len(replay_positions)
    assert src["commodity_cot"]["positions"]["date"].max() > cut
