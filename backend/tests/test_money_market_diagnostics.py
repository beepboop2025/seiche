"""Observed-print diagnostics preserve gaps, source identity and replay clocks."""

import copy
import json

import pandas as pd
import pytest

from seiche.engines import money_market


def _desk(dates, spreads, *, effr=None, evaluation_asof=None):
    index = pd.DatetimeIndex(dates)
    iorb = pd.Series(5.0, index=index)
    return money_market.analyze(
        sofr=iorb + pd.Series(spreads, index=index) / 100.0,
        iorb=iorb,
        effr=effr,
        evaluation_asof=evaluation_asof or index[-1],
    )


def test_sparse_prints_do_not_become_consecutive_days_or_missing_zeros():
    desk = _desk(
        ["2026-08-24", "2026-08-25", "2026-08-27", "2026-09-01"],
        [0, 1, 2, 3],
    )
    diagnostic = desk["diagnostics"]
    window = diagnostic["persistence"]["windows"][0]
    assert window["observed_n"] == 4
    assert window["status"] == "partial"
    assert window["above_iorb_n"] == 3
    assert window["above_iorb_share_pct"] == 75.0
    assert window["unobserved_calendar_weekdays"] == 3
    assert window["median_bp"] == 1.5
    assert window["p95_bp"] is None
    run = diagnostic["persistence"]["current_above_iorb_run"]
    assert run["observed_prints"] == 3
    assert run["calendar_span_days"] == 8
    assert run["left_censored"] is False
    assert (
        diagnostic["calendar_context"]["quarter_end_minus_other_dates_median_bp"]
        is None
    )
    json.dumps(desk, allow_nan=False)


@pytest.mark.parametrize(
    "spreads, count, censored",
    [([1, 2], 2, True), ([1, 0], 0, False), ([-1, 2], 1, False)],
)
def test_positive_run_ends_at_zero_and_discloses_left_censoring(
    spreads, count, censored
):
    run = _desk(["2026-09-01", "2026-09-02"], spreads)["diagnostics"]["persistence"][
        "current_above_iorb_run"
    ]
    assert run["observed_prints"] == count
    assert run["left_censored"] is censored


def test_transmission_uses_older_common_print_and_ages_it_independently():
    effr = pd.Series([5.02, 9.0], index=pd.to_datetime(["2026-08-25", "2026-09-03"]))
    desk = _desk(
        ["2026-08-25", "2026-09-01"], [1, 3], effr=effr, evaluation_asof="2026-09-04"
    )
    diagnostic = desk["diagnostics"]
    transmission = diagnostic["overnight_transmission"]
    assert diagnostic["asof"] == "2026-09-01"
    assert diagnostic["freshness"] == "fresh"
    assert transmission["asof"] == "2026-08-25"
    assert transmission["sofr_minus_iorb_bp"] == 1.0
    assert transmission["effr_minus_iorb_bp"] == 2.0
    assert transmission["sofr_minus_effr_bp"] == -1.0
    assert transmission["pattern"] == "both_benchmarks_above_iorb"
    assert transmission["freshness"] == "stale"
    assert transmission["use"] == "historical_context"


def test_no_overlap_is_explicit_and_missing_effr_never_becomes_zero():
    desk = money_market.analyze(
        sofr=pd.Series([5.0], index=pd.to_datetime(["2026-09-01"])),
        iorb=pd.Series([5.0], index=pd.to_datetime(["2026-09-02"])),
    )
    assert desk["diagnostics"]["status"] == "unavailable"
    assert desk["diagnostics"]["use"] == "no_inference"
    diagnostic = _desk(["2026-09-01"], [2])["diagnostics"]
    assert diagnostic["overnight_transmission"]["status"] == "unavailable"
    assert "effr_minus_iorb_bp" not in diagnostic["overnight_transmission"]


@pytest.mark.parametrize(
    "end, expected_status, expected_difference",
    [("2026-06-30", "available", 5.0), ("2026-09-01", "partial", None)],
)
def test_calendar_medians_count_real_cohorts_and_cap_history(
    end, expected_status, expected_difference
):
    dates = pd.bdate_range("2022-01-03", end)
    month_end = dates.days_in_month - dates.day < 3
    quarter_end = month_end & (dates.month % 3 == 0)
    spreads = pd.Series(1.0, index=dates)
    spreads.loc[month_end] = 3.0
    spreads.loc[quarter_end] = 6.0
    # An older extreme must not change the three-year cohort comparison.
    spreads.iloc[:50] = 10_000
    diagnostic = _desk(dates, spreads)["diagnostics"]
    calendar = diagnostic["calendar_context"]
    assert calendar["status"] == expected_status
    assert calendar["quarter_end_minus_other_dates_median_bp"] == expected_difference
    cohort_rows = {row["cohort"]: row for row in calendar["cohorts"]}
    retained = dates >= dates[-1] - pd.DateOffset(years=3)
    assert cohort_rows["quarter_end"]["observed_n"] == int(
        (quarter_end & retained).sum()
    )
    assert cohort_rows["other_month_end"]["median_bp"] == 3.0
    assert diagnostic["persistence"]["windows"][1]["p95_bp"] is not None


def test_response_time_aging_changes_metadata_without_mutating_snapshot():
    desk = _desk(["2026-09-01", "2026-09-02"], [1, 2])
    original = copy.deepcopy(desk)
    aged = money_market.refresh_for_evaluation(desk, evaluation_asof="2026-09-20")
    assert desk == original
    assert aged["diagnostics"]["freshness"] == "stale"
    assert aged["diagnostics"]["use"] == "historical_context"
    assert (
        aged["diagnostics"]["persistence"]["windows"]
        == desk["diagnostics"]["persistence"]["windows"]
    )
    assert aged["diagnostics"]["used_in_regime"] is False


def test_direct_sofr_fallback_preserves_publisher_identity():
    dates = pd.bdate_range("2026-08-24", periods=5)
    desk = money_market.analyze(
        nyfed_sofr=pd.DataFrame({"percentRate": [5.01] * 5}, index=dates),
        iorb=pd.Series(5.0, index=dates),
    )
    assert "nyfed_sofr_rate" in desk["diagnostics"]["source_ids"]
    assert "fred_sofr" not in desk["diagnostics"]["source_ids"]
    assert set(desk["diagnostics"]["source_ids"]) <= {
        row["id"] for row in desk["source_metadata"]
    }


def test_diagnostics_do_not_change_regime_or_existing_metric_cards(monkeypatch):
    dates = pd.bdate_range("2024-01-02", periods=600)
    spreads = [2.0] * 599 + [20.0]
    with_diagnostics = _desk(dates, spreads)
    monkeypatch.setattr(
        money_market, "_funding_diagnostics", lambda *_args, **_kwargs: {}
    )
    without_diagnostics = _desk(dates, spreads)
    with_diagnostics.pop("diagnostics")
    without_diagnostics.pop("diagnostics")
    assert with_diagnostics == without_diagnostics
