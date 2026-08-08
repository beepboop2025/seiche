"""The Estuary: normalized FX, cadence honesty, and earned Passage links."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from seiche.config import ALL_SERIES, ESTUARY_FRED_SERIES
from seiche.engines import estuary


def _world(n: int = 1100) -> dict:
    index = pd.bdate_range("2020-01-02", periods=n)
    month = pd.date_range("2015-01-01", periods=132, freq="MS")
    week = pd.date_range("2020-01-03", periods=230, freq="W-FRI")
    quarter = pd.date_range("2000-03-31", periods=90, freq="QE")
    bill = pd.Series(4.0, index=index)
    effr = pd.Series(5.0, index=index)
    fx = {
        "EUR": {
            "label": "Euro",
            "bucket": "AFE",
            "series": pd.Series(1.20, index=index),
            "quote": "usd_per_local",
            "rate": pd.Series(4.0, index=index),
            "rate_label": "€STR",
            "rate_cadence": "daily",
            "source_id": "DEXUSEU",
        },
        "JPY": {
            "label": "Japanese yen",
            "bucket": "AFE",
            "series": pd.Series(150.0, index=index),
            "quote": "local_per_usd",
            "source_id": "DEXJPUS",
        },
        "CNY": {
            "label": "Chinese yuan",
            "bucket": "EM",
            "series": pd.Series(7.0, index=index),
            "quote": "local_per_usd",
            "source_id": "DEXCHUS",
        },
        "INR": {
            "label": "Indian rupee",
            "bucket": "EM",
            "series": pd.Series(84.0, index=index),
            "quote": "local_per_usd",
            "source_id": "DEXINUS",
        },
        "KRW": {
            "label": "Korean won",
            "bucket": "EM",
            "series": pd.Series(1400.0, index=index),
            "quote": "local_per_usd",
            "source_id": "DEXKOUS",
        },
        "BRL": {
            "label": "Brazilian real",
            "bucket": "EM",
            "series": pd.Series(5.2, index=index),
            "quote": "local_per_usd",
            "source_id": "DEXBZUS",
        },
    }
    commodities = {
        "WTI": {
            "label": "WTI crude",
            "category": "energy",
            "series": pd.Series(80.0, index=index),
            "cadence": "D",
            "change_kind": "diff",
            "unit": "$/bbl",
        },
        "BRENT": {
            "label": "Brent crude",
            "category": "energy",
            "series": pd.Series(84.0, index=index),
            "cadence": "D",
            "change_kind": "diff",
            "unit": "$/bbl",
        },
        "NATGAS": {
            "label": "Henry Hub gas",
            "category": "energy",
            "series": pd.Series(3.0, index=index),
            "cadence": "D",
            "unit": "$/MMBtu",
        },
        "ALL": {
            "label": "All commodities",
            "category": "broad",
            "series": pd.Series(160.0, index=month),
            "cadence": "M",
            "unit": "2016=100",
        },
        "COPPER": {
            "label": "Copper",
            "category": "industrial",
            "series": pd.Series(9000.0, index=month),
            "cadence": "M",
            "unit": "$/metric ton",
        },
        "WHEAT": {
            "label": "Wheat",
            "category": "agriculture",
            "series": pd.Series(220.0, index=month),
            "cadence": "M",
            "unit": "$/metric ton",
        },
    }
    return {
        "fx": fx,
        "broad_dollar": pd.Series(120.0, index=index),
        "afe_dollar": pd.Series(112.0, index=index),
        "eme_dollar": pd.Series(130.0, index=index),
        "commodities": commodities,
        "sofr": pd.Series(5.02, index=index),
        "iorb": pd.Series(5.0, index=index),
        "effr": effr,
        "cp_nonfinancial_3m": bill + 0.20,
        "cp_financial_3m": bill + 0.30,
        "treasury_3m": bill,
        "swap_lines_m": pd.Series(500.0, index=week),
        "foreign_rrp_m": pd.Series(200_000.0, index=week),
        "fima_repo_m": pd.Series(0.0, index=week),
        "offshore_usd_credit_m": pd.Series(14_000_000.0, index=quarter),
    }


def test_estuary_registry_is_keyless_and_covers_fx_and_material_categories() -> None:
    by_name = {spec.mnemonic: spec for spec in ESTUARY_FRED_SERIES}
    assert {"GBP", "AUD", "CAD", "CHF", "MXN", "BRL", "ZAR"} <= set(by_name)
    assert {"DXY_AFE", "DXY_EME", "NATGAS_SPOT", "COMMODITY_ALL"} <= set(by_name)
    assert {"COPPER", "ALUMINUM", "NICKEL", "COAL", "WHEAT", "CORN"} <= set(by_name)
    assert all(spec.source == "fred" for spec in by_name.values())
    assert set(by_name) <= set(ALL_SERIES)


def test_flat_world_is_neutral_and_normalizes_every_fx_quote_direction() -> None:
    out = estuary.analyze(**_world())
    assert out["ok"]
    headline = out["headline"]
    assert headline["upstream_pressure"] == pytest.approx(50.0)
    assert headline["funding_priced"] == pytest.approx(50.0)
    assert headline["transmission_gap"] == pytest.approx(0.0)
    assert headline["context_only"] is True

    currencies = {row["key"]: row for row in out["fx"]["currencies"]}
    assert currencies["EUR"]["last_local_per_usd"] == pytest.approx(1 / 1.2, abs=1e-5)
    assert currencies["JPY"]["last_local_per_usd"] == 150.0
    assert currencies["EUR"]["policy_diff_vs_effr_bp"] == -100.0


def test_passage_only_earns_a_link_that_survives_the_holdout() -> None:
    index = pd.bdate_range("2017-01-02", periods=1800)
    rng = np.random.default_rng(91)
    source = pd.Series(rng.normal(0.0, 1.0, len(index)), index=index)
    target = source.shift(3) + pd.Series(rng.normal(0.0, 0.12, len(index)), index=index)
    decoy = pd.Series(rng.normal(0.0, 1.0, len(index)), index=index)
    edge = estuary._passage_edge("upstream", source, {"funding": target, "decoy": decoy})
    assert edge is not None
    assert edge["target"] == "funding"
    assert edge["lag_bd"] == 3
    assert edge["status"] == "earned"
    assert edge["corr_holdout"] > 0.9
    assert edge["n_holdout"] > 500


def test_monthly_materials_are_context_not_daily_passage_evidence() -> None:
    out = estuary.analyze(**_world())
    assert out["materials"]["instruments"]
    assert out["charts"]["materials"]["rows"]
    assert "monthly IMF materials are excluded" in out["charts"]["daily_gap"]["note"]
    sources = {edge["source"] for edge in out["passage"]["edges"]}
    assert "Copper" not in sources
    assert "Wheat" not in sources


def test_settlement_and_inventory_scenario_keeps_principal_separate_from_cost() -> None:
    out = estuary.analyze(**_world())
    scenario = out["scenario"]["outputs"]
    assert scenario["fx"]["gross_obligations_usd"] == 5_000_000_000
    assert scenario["fx"]["principal_without_pvp_usd"] == 500_000_000
    assert scenario["fx"]["replacement_cost_shock_usd"] == 10_000_000
    assert scenario["materials"]["inventory_value_change_usd"] == 50_000_000
    assert scenario["materials"]["hedge_margin_call_usd"] == 35_000_000
    assert scenario["materials"]["haircut_cash_call_usd"] == 15_000_000


def test_payload_is_json_safe_and_carries_scope_boundaries() -> None:
    out = estuary.analyze(**_world())
    assert json.dumps(out)
    assert out["settlement_structure"]["survey_asof"] == "2025-04"
    assert any(row["status"] == "out_of_scope" for row in out["coverage_matrix"])
    assert any("cross-currency basis" in caveat for caveat in out["caveats"])
    assert "composite" not in out
