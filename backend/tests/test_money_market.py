"""Synthetic contract tests for the institutional USD money-market desk.

No network or collector cache is involved.  The tests pin the identities,
exact-date doctrine, graceful partial coverage, finite JSON contract, compact
charts, deterministic output, and point-in-time replay behavior.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from seiche.engines import money_market

# pandas 3 + the collectors extra initializes PyArrow's 1 GiB string arena on
# the first DataFrame construction. Memray counts that allocator reservation
# even though this suite's measured process peak is ~72 MiB. Keep a bounded
# canary above the one-time arena so genuine growth still fails production CI.
pytestmark = pytest.mark.limit_memory("1280 MB")


def _world(n: int = 900) -> dict:
    index = pd.bdate_range("2021-01-04", periods=n)
    step = pd.Series(np.linspace(0.0, 0.45, n), index=index)
    sofr = 4.95 + step
    iorb = sofr - 0.02
    effr = sofr - 0.01

    def distribution(rate: pd.Series, volume_start: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "percentRate": rate,
                "percentPercentile1": rate - 0.04,
                "percentPercentile25": rate - 0.01,
                "percentPercentile75": rate + 0.02,
                "percentPercentile99": rate + 0.08,
                "volumeInBillions": np.linspace(volume_start, volume_start + 90.0, n),
            },
            index=index,
        )

    bill_3m = 4.50 + step
    bill_4w = bill_3m - 0.10
    treasury_3m = 4.55 + step
    monthly_index = pd.date_range(index[0], index[-1], freq="ME")
    m = np.arange(len(monthly_index), dtype=float)
    weekly_index = pd.date_range(index[0], index[-1], freq="W-FRI")
    w = np.arange(len(weekly_index), dtype=float)

    return {
        "sofr": sofr,
        "effr": effr,
        "iorb": iorb,
        "nyfed_sofr": distribution(sofr, 1_000.0),
        "nyfed_tgcr": distribution(sofr - 0.03, 500.0),
        "nyfed_bgcr": distribution(sofr - 0.02, 550.0),
        "bgcr": sofr - 0.02,
        "tgcr": sofr - 0.03,
        "dvp_rate": sofr + 0.03,
        "dvp_volume": pd.Series(700.0 + np.linspace(0, 80, n), index=index),
        "tri_rate": sofr - 0.01,
        "tri_volume": pd.Series(600.0 + np.linspace(0, 60, n), index=index),
        "gcf_rate": sofr + 0.01,
        "gcf_volume": pd.Series(100.0 + np.linspace(0, 10, n), index=index),
        "mmf_total": pd.Series(6_000.0 + 10.0 * m, index=monthly_index),
        "mmf_repo_ficc": pd.Series(300.0 + 2.0 * m, index=monthly_index),
        "mmf_repo_fed": pd.Series(200.0 - 1.0 * m, index=monthly_index),
        "mmf_repo_total": pd.Series(800.0 + 4.0 * m, index=monthly_index),
        "cp_nonfinancial_3m": treasury_3m + 0.20,
        "cp_financial_3m": treasury_3m + 0.35,
        "treasury_3m": treasury_3m,
        "bill_4w": bill_4w,
        "bill_3m": bill_3m,
        "reserves": pd.Series(3_300.0 - 0.5 * w, index=weekly_index),
        "tga": pd.Series(650.0 + np.sin(np.arange(n) / 17.0) * 50.0, index=index),
        "on_rrp": pd.Series(np.maximum(0.0, 1_500.0 - np.arange(n) * 1.2), index=index),
        "srf": pd.Series(0.0, index=index),
        "discount_window": pd.Series(4.0 + np.cos(w / 11.0), index=weekly_index),
    }


def _card(result: dict, metric_id: str) -> dict:
    return next(
        metric
        for section in result["sections"]
        for metric in section["metrics"]
        if metric["id"] == metric_id
    )


def _truncate(world: dict, cutoff: pd.Timestamp) -> dict:
    out = {}
    for name, value in world.items():
        out[name] = value[value.index <= cutoff]
    return out


def test_exact_date_formulas_and_units() -> None:
    result = money_market.analyze(**_world())
    assert result["ok"]
    assert result["schema"] == "seiche.money-market-desk.v1"
    assert _card(result, "policy.sofr_minus_iorb")["value"] == pytest.approx(2.0)
    assert _card(result, "policy.sofr_minus_effr")["value"] == pytest.approx(1.0)
    assert _card(result, "distribution.sofr.p99_minus_rate")["value"] == pytest.approx(
        8.0
    )
    assert _card(result, "distribution.sofr.iqr")["value"] == pytest.approx(3.0)
    assert _card(result, "distribution.sofr.tail_skew")["value"] == pytest.approx(2.0)
    assert _card(result, "repo.dvp_minus_sofr")["value"] == pytest.approx(3.0)
    assert _card(result, "repo.dvp_minus_tri")["value"] == pytest.approx(4.0)
    assert _card(result, "unsecured.nonfinancial_cp_minus_treasury")[
        "value"
    ] == pytest.approx(20.0)
    assert _card(result, "unsecured.financial_cp_minus_treasury")[
        "value"
    ] == pytest.approx(35.0)
    assert _card(result, "unsecured.financial_minus_nonfinancial_cp")[
        "value"
    ] == pytest.approx(15.0)
    assert _card(result, "bills.three_month_minus_four_week")["value"] == pytest.approx(
        10.0
    )

    displayed = _card(result, "repo.reported_segment_volume")
    expected_volume = (
        result["charts"]["repo_volumes"]["rows"][-1][1]
        + result["charts"]["repo_volumes"]["rows"][-1][2]
        + result["charts"]["repo_volumes"]["rows"][-1][3]
    )
    assert displayed["value"] == pytest.approx(expected_volume)
    assert displayed["unit"] == "$B"

    world = _world()
    used_month = world["mmf_repo_total"][
        world["mmf_repo_total"].index <= pd.Timestamp(result["asof"])
    ].index[-1]
    expected_ficc_share = (
        world["mmf_repo_ficc"].loc[used_month]
        / world["mmf_repo_total"].loc[used_month]
        * 100.0
    )
    assert _card(result, "mmf.ficc_share_of_repo")["value"] == pytest.approx(
        expected_ficc_share, abs=0.001
    )


def test_every_metric_is_self_describing_and_statistics_are_empirical() -> None:
    result = money_market.analyze(**_world())
    required = {
        "id",
        "label",
        "value",
        "unit",
        "asof",
        "cadence",
        "source",
        "explanation",
    }
    metrics = [
        metric for section in result["sections"] for metric in section["metrics"]
    ]
    assert metrics
    assert all(required <= set(metric) for metric in metrics)
    core = _card(result, "policy.sofr")
    assert core["change_1d"] is not None
    assert core["change_5d"] is not None
    assert core["change_20d"] is not None
    assert core["robust_z_1y"] is not None
    assert core["robust_z_1y_n"] >= 200
    assert core["percentile_3y"] is not None
    assert core["percentile_3y_n"] >= 700
    assert (
        result["regime"]["status"]
        == "descriptive_context_only_not_forecast_probability_or_trade_signal"
    )
    assert result["regime"]["state"] in {
        "NORMAL",
        "WATCH",
        "STRAIN",
        "STRESS",
        "CANNOT_ASSESS",
    }
    assert "no averaging" in result["regime"]["rule"]
    assert "not causal" in result["strongest_signal"]["use"]
    assert result["legal_notices"][0]["terms_url"].startswith(
        "https://www.newyorkfed.org/"
    )
    assert "not endorsed" in result["legal_notices"][0]["non_affiliation"]


def test_headline_uses_dependence_robust_familywise_adjustment() -> None:
    result = money_market.analyze(**_world())
    regime = result["regime"]
    indicators = regime["indicators"]
    family_size = len(indicators)

    assert family_size > 1
    assert regime["familywise_adjustment"] == {
        "method": "bonferroni_empirical_upper_tail",
        "eligible_hypotheses": family_size,
        "formula": "adjusted tail probability = min(1, m x (1 - raw stress percentile / 100)); adjusted stress percentile = 100 x (1 - adjusted tail probability)",
        "dependence_assumption": "valid under arbitrary cross-channel dependence",
        "headline_uses": "bonferroni_adjusted_stress_percentile",
    }
    for indicator in indicators:
        raw = indicator["raw_stress_percentile"]
        adjusted = indicator["bonferroni_adjusted_stress_percentile"]
        expected = max(0.0, 100.0 * (1.0 - family_size * (1.0 - raw / 100.0)))
        expected_at_contract_precision = np.floor((expected + 1e-12) * 10.0) / 10.0
        assert adjusted == pytest.approx(expected_at_contract_precision)
        assert indicator["stress_percentile"] == adjusted
        assert adjusted <= raw
        assert indicator["familywise_hypotheses"] == family_size

    worst = regime["worst_indicator"]
    assert regime["raw_worst_stress_percentile"] == worst[
        "raw_stress_percentile"
    ]
    assert regime["worst_stress_percentile"] == worst[
        "bonferroni_adjusted_stress_percentile"
    ]
    assert regime["bonferroni_adjusted_worst_stress_percentile"] == regime[
        "worst_stress_percentile"
    ]

    contextual = {
        row["metric_id"]: row["reason"]
        for row in regime["excluded_indicators"]
        if row["reason"] == "contextual_stock_level_not_headline_anomaly"
    }
    assert contextual == {
        "liquidity.reserves": "contextual_stock_level_not_headline_anomaly",
        "liquidity.on_rrp": "contextual_stock_level_not_headline_anomaly",
    }
    assert _card(result, "liquidity.reserves")["status"] == "available"
    assert _card(result, "liquidity.on_rrp")["status"] == "available"
    assert not {"liquidity.reserves", "liquidity.on_rrp"} & {
        row["metric_id"] for row in indicators
    }


def test_bonferroni_adjustment_preserves_nulls_and_is_monotone() -> None:
    assert money_market._bonferroni_stress_percentile(None, 4) is None
    assert money_market._bonferroni_stress_percentile(np.nan, 4) is None
    assert money_market._bonferroni_stress_percentile(99.0, 0) is None

    raw_ranks = [50.0, 75.0, 90.0, 99.0, 100.0]
    adjusted = [
        money_market._bonferroni_stress_percentile(raw, 3) for raw in raw_ranks
    ]
    assert adjusted == sorted(adjusted)
    assert all(value is not None for value in adjusted)
    assert all(value <= raw for value, raw in zip(adjusted, raw_ranks, strict=True))
    assert money_market._bonferroni_stress_percentile(
        99.0, 8
    ) <= money_market._bonferroni_stress_percentile(99.0, 2)


def test_bonferroni_controls_false_headlines_in_deterministic_null_panels() -> None:
    rng = np.random.default_rng(20260821)
    family_size = 9
    raw_panel = rng.uniform(0.0, 100.0, size=(50_000, family_size))
    raw_headlines = raw_panel.max(axis=1)
    adjusted_headlines = np.fromiter(
        (
            money_market._bonferroni_stress_percentile(raw, family_size)
            for raw in raw_headlines
        ),
        dtype=float,
        count=len(raw_headlines),
    )

    # Under a true uniform null, taking the unadjusted maximum across nine
    # channels produces a nominal p97.5 "STRESS" headline far too often.
    raw_false_positive_rate = float(np.mean(raw_headlines >= 97.5))
    adjusted_false_positive_rate = float(np.mean(adjusted_headlines >= 97.5))

    assert raw_false_positive_rate > 0.15
    assert adjusted_false_positive_rate <= 0.03
    assert adjusted_false_positive_rate < raw_false_positive_rate / 5.0
    assert np.all(adjusted_headlines <= raw_headlines)


def test_declining_reserves_and_on_rrp_cannot_create_a_stress_headline() -> None:
    world = _world()
    daily_index = world["sofr"].index
    weekly_index = world["reserves"].index

    # Keep every eligible price/spread anomaly exactly flat at its historical
    # midpoint while the two context-only liquidity stocks drain monotonically.
    world["sofr"] = pd.Series(5.0, index=daily_index)
    world["iorb"] = pd.Series(4.984375, index=daily_index)
    world["effr"] = pd.Series(4.9921875, index=daily_index)
    world["dvp_rate"] = pd.Series(5.03125, index=daily_index)
    world["gcf_rate"] = pd.Series(5.015625, index=daily_index)
    world["treasury_3m"] = pd.Series(4.5, index=daily_index)
    world["cp_nonfinancial_3m"] = pd.Series(4.75, index=daily_index)
    world["cp_financial_3m"] = pd.Series(4.875, index=daily_index)
    world["srf"] = pd.Series(0.0, index=daily_index)
    world["discount_window"] = pd.Series(4.0, index=weekly_index)

    sofr_distribution = world["nyfed_sofr"].copy()
    sofr_distribution["percentRate"] = 5.0
    sofr_distribution["percentPercentile1"] = 4.9375
    sofr_distribution["percentPercentile25"] = 4.984375
    sofr_distribution["percentPercentile75"] = 5.03125
    sofr_distribution["percentPercentile99"] = 5.0625
    world["nyfed_sofr"] = sofr_distribution

    world["reserves"] = pd.Series(
        np.linspace(4_000.0, 2_000.0, len(weekly_index)),
        index=weekly_index,
    )
    world["on_rrp"] = pd.Series(
        np.linspace(2_000.0, 0.0, len(daily_index)),
        index=daily_index,
    )

    result = money_market.analyze(**world)
    reserves = _card(result, "liquidity.reserves")
    on_rrp = _card(result, "liquidity.on_rrp")

    assert world["reserves"].is_monotonic_decreasing
    assert world["on_rrp"].is_monotonic_decreasing
    assert reserves["percentile_3y"] <= 1.0
    assert on_rrp["percentile_3y"] <= 1.0
    assert reserves["status"] == on_rrp["status"] == "available"
    assert result["regime"]["state"] == "NORMAL"
    assert result["regime"]["raw_worst_stress_percentile"] == 50.0
    assert result["regime"]["worst_stress_percentile"] == 0.0
    assert {
        row["raw_stress_percentile"] for row in result["regime"]["indicators"]
    } == {50.0}
    assert result["strongest_signal"]["metric_id"] not in {
        "liquidity.reserves",
        "liquidity.on_rrp",
    }


def test_partial_inputs_remain_visible_and_exact_alignment_never_fills() -> None:
    world = _world()
    # Deliberately put CP and its Treasury reference on disjoint dates.  A
    # forward fill would manufacture a spread; the exact-date engine must not.
    world["cp_nonfinancial_3m"] = world["cp_nonfinancial_3m"].iloc[::2]
    world["treasury_3m"] = world["treasury_3m"].iloc[1::2]
    for name in (
        "nyfed_tgcr",
        "nyfed_bgcr",
        "gcf_rate",
        "gcf_volume",
        "mmf_repo_ficc",
        "discount_window",
    ):
        value = world[name]
        world[name] = value.iloc[:0]

    result = money_market.analyze(**world)
    assert result["ok"]
    spread = _card(result, "unsecured.nonfinancial_cp_minus_treasury")
    assert spread["status"] == "unavailable"
    assert spread["value"] is None
    assert spread["alignment"]["method"] == "exact_date_inner_join"
    assert spread["alignment"]["no_forward_fill"] is True
    assert spread["alignment"]["overlap_observations"] == 0
    assert _card(result, "repo.dvp_rate")["status"] == "available"
    assert _card(result, "repo.gcf_rate")["status"] == "unavailable"
    available_total = _card(result, "repo.reported_segment_volume")
    assert available_total["status"] == "available"
    assert available_total["alignment"]["latest_available_components"] == ["DVP", "TRI"]
    assert available_total["alignment"]["missing_component_treatment"].endswith(
        "never imputed as zero"
    )
    assert _card(result, "mmf.total_assets")["status"] == "available"
    assert _card(result, "mmf.ficc_share_of_repo")["status"] == "unavailable"
    assert any(section["status"] == "partial" for section in result["sections"])
    assert spread["id"] in result["coverage"]["unavailable_metrics"]


def test_repo_aggregate_never_blends_instrument_compositions() -> None:
    world = _world()
    intermittent_gcf = world["gcf_volume"].copy()
    intermittent_gcf.iloc[1::2] = np.nan
    world["gcf_volume"] = intermittent_gcf

    result = money_market.analyze(**world)
    aggregate = _card(result, "repo.reported_segment_volume")
    alignment = aggregate["alignment"]

    assert alignment["method"] == "same_date_fixed_composition_sum"
    assert alignment["comparison_components"] == ["DVP", "TRI"]
    assert alignment["comparison_component_mask"] == "DVP+TRI"
    assert alignment["excluded_mixed_composition_observations"] > 0
    assert set(alignment["observed_component_masks"]) == {
        "DVP+TRI",
        "DVP+TRI+GCF",
    }

    for _, dvp, tri, gcf, displayed_total in result["charts"]["repo_volumes"][
        "rows"
    ]:
        if gcf is None:
            assert displayed_total == pytest.approx(dvp + tri, abs=0.02)
        else:
            assert displayed_total is None
    assert _card(result, "repo.gcf_volume_share")["status"] == "unavailable"


def test_only_missing_core_makes_the_engine_fail() -> None:
    world = _world()
    world["effr"] = world["effr"].iloc[:0]
    world["nyfed_sofr"] = world["nyfed_sofr"].iloc[:0]
    world["mmf_total"] = world["mmf_total"].iloc[:0]
    result = money_market.analyze(**world)
    assert result["ok"]

    world["sofr"] = world["sofr"].iloc[:0]
    result = money_market.analyze(**world)
    assert not result["ok"]
    assert "SOFR and IORB" in result["reason"]
    assert result["freshness"]["evaluation_asof"] is None

    world = _world()
    world["iorb"] = world["iorb"].iloc[:0]
    result = money_market.analyze(**world)
    assert not result["ok"]
    assert result["coverage"]["status"] == "core_unavailable"
    assert {
        "schema",
        "asof",
        "regime",
        "plain_language",
        "quant_read",
        "strongest_signal",
        "countercase",
        "coverage",
        "freshness",
        "sections",
        "charts",
        "methodology",
        "formulas",
        "caveats",
        "source_metadata",
    } <= set(result)

    # Both core legs exist, but on disjoint clocks. This is partial evidence,
    # not a hard failure: raw cards remain and the spread is explicitly null.
    world = _world()
    world["sofr"] = world["sofr"].iloc[::2]
    world["iorb"] = world["iorb"].iloc[1::2]
    world["nyfed_sofr"] = world["nyfed_sofr"].iloc[:0]
    result = money_market.analyze(**world)
    assert result["ok"]
    assert _card(result, "policy.sofr")["status"] == "available"
    assert _card(result, "policy.iorb")["status"] == "available"
    core_spread = _card(result, "policy.sofr_minus_iorb")
    assert core_spread["status"] == "unavailable"
    assert core_spread["alignment"]["overlap_observations"] == 0


def test_unavailable_desk_preserves_the_requested_evaluation_clock() -> None:
    world = _world()
    world["iorb"] = world["iorb"].iloc[:0]

    result = money_market.analyze(
        **world,
        evaluation_asof="2026-08-21T17:30:00+05:30",
    )

    assert not result["ok"]
    assert result["freshness"]["desk_asof"] is None
    assert result["freshness"]["evaluation_asof"] == "2026-08-21"


def test_json_safety_determinism_and_chart_budgets() -> None:
    world = _world()
    world["gcf_volume"].iloc[-1] = np.inf
    world["tga"].iloc[-2] = np.nan
    first = money_market.analyze(**world)
    second = money_market.analyze(**world)
    assert first == second
    assert json.dumps(first, allow_nan=False)
    assert all(
        len(chart["rows"])
        <= (
            money_market.MONTHLY_CHART_ROWS
            if chart["cadence"] == "monthly"
            else money_market.DAILY_CHART_ROWS
        )
        for chart in first["charts"].values()
    )
    assert len(first["charts"]["mmf"]["rows"]) <= 36
    assert len(first["charts"]["policy"]["rows"]) <= 180


def test_truncation_has_no_lookahead() -> None:
    world = _world()
    cutoff = world["sofr"].index[-121]
    full = money_market.analyze(**world)
    replay = money_market.analyze(**_truncate(world, cutoff))
    cutoff_text = cutoff.date().isoformat()

    assert replay["asof"] == cutoff_text
    for section in replay["sections"]:
        for metric in section["metrics"]:
            assert metric["asof"] is None or metric["asof"] <= cutoff_text
    for chart in replay["charts"].values():
        assert all(row[0] <= cutoff_text for row in chart["rows"])
    assert all(
        row["asof"] is None or row["asof"] <= cutoff_text
        for row in replay["source_metadata"]
    )

    trailing_1y = world["sofr"][
        (world["sofr"].index <= cutoff)
        & (world["sofr"].index >= cutoff - pd.DateOffset(years=1))
    ]
    median = float(trailing_1y.median())
    scale = 1.4826 * float((trailing_1y - median).abs().median())
    expected_z = round((float(trailing_1y.iloc[-1]) - median) / scale, 2)
    assert _card(replay, "policy.sofr")["robust_z_1y"] == pytest.approx(expected_z)

    trailing_3y = world["sofr"][
        (world["sofr"].index <= cutoff)
        & (world["sofr"].index >= cutoff - pd.DateOffset(years=3))
    ]
    latest = float(trailing_3y.iloc[-1])
    expected_percentile = round(
        100.0
        * (
            int((trailing_3y < latest).sum())
            + 0.5 * int(np.isclose(trailing_3y.to_numpy(), latest).sum())
        )
        / len(trailing_3y),
        1,
    )
    assert _card(replay, "policy.sofr")["percentile_3y"] == pytest.approx(
        expected_percentile
    )

    # The adaptive tail changes which dates are retained, but any closed date
    # appearing in both snapshots must have byte-for-byte identical values.
    for chart_id in ("policy", "sofr_distribution", "repo_rates", "unsecured", "bills"):
        full_closed = {
            row[0]: row[1:]
            for row in full["charts"][chart_id]["rows"]
            if row[0] <= cutoff_text
        }
        replay_rows = {row[0]: row[1:] for row in replay["charts"][chart_id]["rows"]}
        common = set(full_closed) & set(replay_rows)
        assert common
        assert {date: full_closed[date] for date in common} == {
            date: replay_rows[date] for date in common
        }


def test_frames_can_supply_the_official_sofr_core_fallback() -> None:
    world = _world()
    world["sofr"] = world["sofr"].iloc[:0]
    result = money_market.analyze(**world)
    assert result["ok"]
    assert _card(result, "policy.sofr")["value"] == pytest.approx(
        world["nyfed_sofr"]["percentRate"].iloc[-1]
    )
    assert (
        _card(result, "policy.sofr")["source"]
        == "Federal Reserve Bank of New York direct reference-rates feed"
    )
    source_ids = {row["id"] for row in result["source_metadata"]}
    assert "nyfed_sofr_rate" in source_ids
    assert "fred_sofr" not in source_ids
    direct = next(
        row for row in result["source_metadata"] if row["id"] == "nyfed_sofr_rate"
    )
    assert direct["publisher"] == "Federal Reserve Bank of New York"
    assert direct["series"] == "SOFR percentRate (direct reference-rates feed)"


def test_frozen_snapshot_is_stale_and_cannot_set_a_current_regime() -> None:
    world = _world()
    desk_asof = world["sofr"].index[-1]
    evaluation_asof = desk_asof + pd.Timedelta(days=120)

    result = money_market.analyze(
        **world,
        evaluation_asof=evaluation_asof,
    )

    assert result["ok"]
    assert result["freshness"]["desk_asof"] == desk_asof.date().isoformat()
    assert result["freshness"]["evaluation_asof"] == (
        evaluation_asof.date().isoformat()
    )
    assert result["freshness"]["status_counts"]["fresh"] == 0
    assert result["regime"]["state"] == "CANNOT_ASSESS"
    assert result["regime"]["raw_worst_stress_percentile"] is None
    assert result["regime"]["worst_stress_percentile"] is None
    assert result["regime"]["bonferroni_adjusted_worst_stress_percentile"] is None
    assert result["regime"]["familywise_adjustment"]["eligible_hypotheses"] == 0
    assert result["regime"]["indicators"] == []
    assert any(
        row["reason"] == "stale_at_evaluation_asof"
        for row in result["regime"]["excluded_indicators"]
    )
    assert "not treated as calm" in result["plain_language"]

    historical_sofr = _card(result, "policy.sofr")
    assert historical_sofr["status"] == "available"
    assert historical_sofr["freshness"] == "stale"
    assert historical_sofr["age_days_vs_evaluation_asof"] == 120
    assert all(section["status"] == "stale" for section in result["sections"])
    assert result["coverage"]["available_metrics"] == 0
    assert result["coverage"]["historical_available_metrics"] > 0
    sofr_source = next(
        row for row in result["source_metadata"] if row["id"] == "fred_sofr"
    )
    assert sofr_source["age_days_vs_desk_asof"] == 0
    assert sofr_source["age_days_vs_evaluation_asof"] == 120
    assert sofr_source["freshness"] == "stale"


def test_explicit_replay_date_is_deterministic_and_cannot_precede_evidence() -> None:
    world = _world()
    cutoff = world["sofr"].index[-121]
    replay_world = _truncate(world, cutoff)

    first = money_market.analyze(**replay_world, evaluation_asof=cutoff)
    second = money_market.analyze(**replay_world, evaluation_asof=cutoff)

    assert first == second
    assert first["freshness"]["evaluation_asof"] == cutoff.date().isoformat()
    assert first["regime"]["state"] != "CANNOT_ASSESS"
    with pytest.raises(ValueError, match="cannot precede"):
        money_market.analyze(
            **replay_world,
            evaluation_asof=cutoff - pd.Timedelta(days=1),
        )


def test_cached_desk_ages_at_read_time_without_mutating_observations() -> None:
    world = _world()
    built = money_market.analyze(**world)
    desk_asof = pd.Timestamp(built["asof"])
    original_value = _card(built, "policy.sofr")["value"]
    original_evaluation = built["freshness"]["evaluation_asof"]

    served = money_market.refresh_for_evaluation(
        built,
        evaluation_asof=desk_asof + pd.Timedelta(days=120),
    )

    assert built["freshness"]["evaluation_asof"] == original_evaluation
    assert _card(built, "policy.sofr")["freshness"] == "fresh"
    assert _card(served, "policy.sofr")["value"] == original_value
    assert _card(served, "policy.sofr")["freshness"] == "stale"
    assert served["regime"]["state"] == "CANNOT_ASSESS"
    assert served["regime"]["indicators"] == []
    assert all(section["status"] == "stale" for section in served["sections"])
    assert served["coverage"]["available_metrics"] == 0
    assert served["coverage"]["historical_available_metrics"] > 0
    assert "stale" in served["quant_read"].lower()
    assert all(
        row[0] <= built["asof"]
        for chart in served["charts"].values()
        for row in chart["rows"]
    )
