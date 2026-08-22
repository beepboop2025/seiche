"""Foreign Official Bid tests: stated rules, honest degradation, and the net
positioning additions to RV X-Ray. Synthetic data only, no network."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from seiche.engines import officialbid, rvxray

pytestmark = pytest.mark.limit_memory("256 MB")

NO_DASH = ("—", "–", "-")


def _weeks(n: int, start: str = "2024-01-03") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="W-WED")


def _flat(n: int, level: float) -> np.ndarray:
    return np.full(n, level, dtype=float)


def _ramp_tail(base: np.ndarray, per_week: float, weeks: int = 13) -> np.ndarray:
    """Add a linear per-week drift over the last `weeks` observations."""
    out = base.copy()
    out[-weeks:] += per_week * np.arange(1, weeks + 1)
    return out


# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------


def test_rotation_custody_down_rrp_takes_it_all():
    idx = _weeks(60)
    # custody sheds $3B/wk for 13 weeks (=39B), foreign RRP absorbs $2.9B/wk,
    # so the combined footprint barely moves: the money parked, it did not leave
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), -3_000.0), index=idx)
    rrp = pd.Series(_ramp_tail(_flat(60, 350_000.0), +2_900.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    assert r["ok"]
    assert r["classification"] == "rotation"
    assert r["custody_chg_13w_b"] == -39.0
    assert r["foreign_rrp_chg_13w_b"] == 37.7
    assert r["custody_b"] == 2900.0 - 39.0
    assert r["footprint_b"] == round(
        (2_900_000 - 39_000 + 350_000 + 37_700) / 1000.0, 1
    )
    assert r["footprint_chg_13w_b"] == -1.3
    assert r["footprint_drain_share"] == 0.03
    assert "rotation" in r["letter_line"] and "partial" not in r["letter_line"]


def test_partial_rotation_when_the_footprint_fell():
    """Custody down 39B with only 19.5B landing in the foreign RRP pool: half
    the money left the official sector, which is not the same finding as a
    parking shift and must not print as one."""
    idx = _weeks(60)
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), -3_000.0), index=idx)
    rrp = pd.Series(_ramp_tail(_flat(60, 350_000.0), +1_500.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    assert r["ok"] and r["classification"] == "partial_rotation"
    assert r["footprint_chg_13w_b"] == -19.5
    assert r["footprint_drain_share"] == 0.5
    line = r["letter_line"]
    assert "partial rotation" in line
    assert "19.5B" in line  # the footprint fall is named, not implied
    assert "50%" in line
    assert "3,230B" in line  # and the footprint level it fell to


def test_partial_rotation_on_the_live_shape():
    """The 2026-07-22 board: custody down ~105B, foreign RRP up ~28B, footprint
    down ~78B. That was asserted as a clean parking rotation."""
    idx = _weeks(60)
    cust = pd.Series(_ramp_tail(_flat(60, 2_722_000.0), -8_000.0), index=idx)
    rrp = pd.Series(_ramp_tail(_flat(60, 326_000.0), +2_000.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    assert r["classification"] == "partial_rotation"
    assert r["custody_chg_13w_b"] == -104.0 and r["foreign_rrp_chg_13w_b"] == 26.0
    assert r["footprint_chg_13w_b"] == -78.0
    assert r["footprint_drain_share"] == 0.75


def test_rotation_custody_down_rrp_flat_is_partial():
    # Nothing was absorbed at all: the whole custody drop left the footprint.
    idx = _weeks(60)
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), -3_000.0), index=idx)
    rrp = pd.Series(_flat(60, 350_000.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    assert r["ok"] and r["classification"] == "partial_rotation"
    assert r["footprint_drain_share"] == 1.0
    assert "held flat" in r["letter_line"] and "100%" in r["letter_line"]


def test_footprint_drain_below_the_threshold_stays_a_clean_rotation():
    # 9.1B of 39B leaves = 23%, under the 25% mark: still a parking rotation.
    idx = _weeks(60)
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), -3_000.0), index=idx)
    rrp = pd.Series(_ramp_tail(_flat(60, 350_000.0), +2_300.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    assert r["classification"] == "rotation"
    assert r["footprint_chg_13w_b"] == -9.1
    assert r["footprint_drain_share"] == 0.23


def test_footprint_fall_inside_the_flat_band_is_not_material():
    # Two thirds of a 6B custody drop left, but 4B is inside the 5B band:
    # noise on these pools, so the label stays clean.
    idx = _weeks(60)
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), -6_000.0 / 13), index=idx)
    rrp = pd.Series(_ramp_tail(_flat(60, 350_000.0), +2_000.0 / 13), index=idx)
    r = officialbid.analyze(cust, rrp)
    assert r["custody_chg_13w_b"] == -6.0 and r["footprint_chg_13w_b"] == -4.0
    assert r["footprint_drain_share"] == 0.67
    assert r["classification"] == "rotation"


def test_threshold_is_documented_in_the_method():
    idx = _weeks(60)
    cust = pd.Series(_flat(60, 2_900_000.0), index=idx)
    rrp = pd.Series(_flat(60, 350_000.0), index=idx)
    m = officialbid.analyze(cust, rrp)["method"]
    assert "partial rotation" in m and "25%" in m and "footprint_drain_share" in m


def test_retreat_both_down():
    idx = _weeks(60)
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), -3_000.0), index=idx)
    rrp = pd.Series(_ramp_tail(_flat(60, 350_000.0), -1_000.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    assert r["ok"] and r["classification"] == "retreat"
    assert "retreat" in r["letter_line"] or "leaving" in r["letter_line"]
    # both pools shrank, so more left than the custody book alone gave up
    assert r["footprint_drain_share"] > 1.0


def test_build_both_up():
    idx = _weeks(60)
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), +2_000.0), index=idx)
    rrp = pd.Series(_ramp_tail(_flat(60, 350_000.0), +1_000.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    assert r["ok"] and r["classification"] == "build"
    assert "build" in r["letter_line"]


def test_steady_inside_flat_band():
    idx = _weeks(60)
    # 13w drifts of +-1B sit inside the 5B band
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), -70.0), index=idx)
    rrp = pd.Series(_ramp_tail(_flat(60, 350_000.0), +70.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    assert r["ok"] and r["classification"] == "steady"


# ---------------------------------------------------------------------------
# Letter line and payload contract
# ---------------------------------------------------------------------------


def test_letter_line_is_one_clean_sentence():
    idx = _weeks(60)
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), -3_000.0), index=idx)
    # every branch, the partial one included, prints one clean sentence
    for per_week in (+2_900.0, +1_500.0, -1_000.0, 0.0):
        rrp = pd.Series(_ramp_tail(_flat(60, 350_000.0), per_week), index=idx)
        line = officialbid.analyze(cust, rrp)["letter_line"]
        assert isinstance(line, str) and line.endswith(".")
        assert ". " not in line  # one sentence; decimals in numbers allowed
        for ch in NO_DASH:
            assert ch not in line
        assert "39.0B" in line  # numbers inline, plain


def test_payload_keys_and_series_shape():
    idx = _weeks(200)
    cust = pd.Series(_flat(200, 2_900_000.0), index=idx)
    rrp = pd.Series(_flat(200, 350_000.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    for k in (
        "ok",
        "method",
        "caveats",
        "asof",
        "classification",
        "letter_line",
        "custody_chg_4w_b",
        "footprint_chg_13w_b",
        "footprint_drain_share",
        "series",
    ):
        assert k in r
    assert len(r["series"]) == 156  # capped history for the chart
    assert all(len(row) == 4 for row in r["series"])
    assert r["asof"] == idx[-1].date().isoformat()


def test_fima_repo_optional_and_drawn_flag():
    idx = _weeks(60)
    cust = pd.Series(_flat(60, 2_900_000.0), index=idx)
    rrp = pd.Series(_flat(60, 350_000.0), index=idx)
    # absent: honest caveat, no fields invented
    r0 = officialbid.analyze(cust, rrp)
    assert r0["fima_repo_b"] is None and not r0["fima_drawn"]
    assert any("FIMA" in c for c in r0["caveats"])
    # an empty Series (the assemble _pts default) counts as absent, not a crash
    re = officialbid.analyze(cust, rrp, fima_repo_weekly=pd.Series(dtype=float))
    assert re["ok"] and re["fima_repo_b"] is None
    # drawn: 5B outstanding flags the stress tell
    fima = pd.Series(_flat(60, 0.0), index=idx)
    fima.iloc[-3:] = 5_000.0
    r1 = officialbid.analyze(cust, rrp, fima_repo_weekly=fima)
    assert r1["fima_repo_b"] == 5.0 and r1["fima_drawn"]
    assert any("borrowing" in c for c in r1["caveats"])


def test_degrades_without_history_or_inputs():
    idx = _weeks(6)
    short = pd.Series(_flat(6, 2_900_000.0), index=idx)
    r = officialbid.analyze(short, pd.Series(_flat(6, 350_000.0), index=idx))
    assert not r["ok"] and "insufficient" in r["reason"]
    r2 = officialbid.analyze(
        pd.Series(dtype=float), pd.Series(_flat(6, 1.0), index=idx)
    )
    assert not r2["ok"]


# ---------------------------------------------------------------------------
# RV X-Ray: net rides alongside gross, additive only
# ---------------------------------------------------------------------------


def _tff_two_weeks() -> pd.DataFrame:
    rows = []
    for date in (pd.Timestamp("2026-01-06"), pd.Timestamp("2026-01-13")):
        rows += [
            {
                "date": date,
                "contract": "UST 2Y NOTE",
                "open_interest_all": 100_000.0,
                "lev_money_positions_long_all": 10_000.0,
                "lev_money_positions_short_all": 40_000.0,
                "asset_mgr_positions_long_all": 35_000.0,
                "asset_mgr_positions_short_all": 5_000.0,
            },
            {
                "date": date,
                "contract": "UST 5Y NOTE",
                "open_interest_all": 80_000.0,
                "lev_money_positions_long_all": 20_000.0,
                "lev_money_positions_short_all": 5_000.0,
                "asset_mgr_positions_long_all": 8_000.0,
                "asset_mgr_positions_short_all": 1_000.0,
            },
            {
                "date": date,
                "contract": "FED FUNDS",
                "open_interest_all": 50_000.0,
                "lev_money_positions_long_all": 5_000.0,
                "lev_money_positions_short_all": 9_000.0,
                "asset_mgr_positions_long_all": 8_000.0,
                "asset_mgr_positions_short_all": 1_000.0,
            },
        ]
    return pd.DataFrame(rows)


def test_rvxray_net_total_and_per_contract():
    df = _tff_two_weeks()
    dvp = pd.Series([4e12, 4e12], index=pd.to_datetime(["2026-01-06", "2026-01-13"]))
    r = rvxray.analyze(df, dvp)
    assert r["ok"]
    # existing keys untouched
    assert r["gross_short_b"] == 8.5  # 40k x 200k + 5k x 100k
    assert r["pair_proxy_b"] == 7.5  # min(40k,35k) x 200k + min(5k,8k) x 100k
    # net total: 2Y (10k-40k) x 200k = -6.0B; 5Y (20k-5k) x 100k = +1.5B
    assert r["net_b"] == -4.5
    by = {row["contract"]: row for row in r["by_contract"]}
    assert set(by) == {"UST 2Y NOTE", "UST 5Y NOTE"}  # FED FUNDS is not an RV leg
    assert (
        by["UST 2Y NOTE"]["net_b"] == -6.0 and by["UST 2Y NOTE"]["gross_short_b"] == 8.0
    )
    assert (
        by["UST 5Y NOTE"]["net_b"] == 1.5 and by["UST 5Y NOTE"]["gross_short_b"] == 0.5
    )
    # series rows carry net as a 4th column, prior columns unchanged
    assert all(len(row) == 4 for row in r["series"])
    assert r["series"][-1][2] == 8.5 and r["series"][-1][3] == -4.5
    # headline and table are dated the same week, and the date is published
    assert r["asof"] == "2026-01-13" and r["by_contract_asof"] == "2026-01-13"


def _panel_row(date: pd.Timestamp, contract: str) -> dict:
    return {
        "date": date,
        "contract": contract,
        "open_interest_all": 50_000.0,
        "lev_money_positions_long_all": 5_000.0,
        "lev_money_positions_short_all": 9_000.0,
        "asset_mgr_positions_long_all": 8_000.0,
        "asset_mgr_positions_short_all": 1_000.0,
    }


def test_rvxray_partial_last_report_does_not_split_the_asof():
    """A last TFF row carrying only crowding-panel contracts dated the headline
    a week ahead of the by_contract table, and zeroed the totals with it: the
    history grouped every contract while the table looked at UST rows only."""
    df = pd.concat(
        [
            _tff_two_weeks(),
            pd.DataFrame([_panel_row(pd.Timestamp("2026-01-20"), "FED FUNDS")]),
        ],
        ignore_index=True,
    )
    dvp = pd.Series([4e12], index=pd.to_datetime(["2026-01-20"]))
    r = rvxray.analyze(df, dvp)
    assert r["ok"]
    assert r["asof"] == "2026-01-13"  # the last week with UST rows
    assert r["by_contract_asof"] == r["asof"]
    assert r["gross_short_b"] == 8.5  # not zeroed by the panel-only row
    assert r["net_b"] == -4.5
    assert r["series"][-1][0] == "2026-01-13"
    assert sum(row["gross_short_b"] for row in r["by_contract"]) == r["gross_short_b"]


def test_rvxray_thin_ust_week_still_reads_one_date():
    """When the last week reports some UST contracts and not others, both reads
    move to that week together; the table always adds up to the headline."""
    thin = pd.DataFrame(
        [
            _panel_row(pd.Timestamp("2026-01-20"), "FED FUNDS"),
            {
                "date": pd.Timestamp("2026-01-20"),
                "contract": "UST 2Y NOTE",
                "open_interest_all": 100_000.0,
                "lev_money_positions_long_all": 10_000.0,
                "lev_money_positions_short_all": 40_000.0,
                "asset_mgr_positions_long_all": 35_000.0,
                "asset_mgr_positions_short_all": 5_000.0,
            },
        ]
    )
    df = pd.concat([_tff_two_weeks(), thin], ignore_index=True)
    dvp = pd.Series([4e12], index=pd.to_datetime(["2026-01-20"]))
    r = rvxray.analyze(df, dvp)
    assert r["asof"] == "2026-01-20" and r["by_contract_asof"] == "2026-01-20"
    assert [row["contract"] for row in r["by_contract"]] == ["UST 2Y NOTE"]
    assert r["gross_short_b"] == 8.0
    assert sum(row["gross_short_b"] for row in r["by_contract"]) == r["gross_short_b"]
    for metric in ("pair_proxy_b", "gross_short_b", "net_b", "dv01_m_per_bp"):
        assert r["metric_coverage"][metric]["status"] == "partial"
        assert r["metric_coverage"][metric]["missing_contracts"] == ["UST 5Y NOTE"]
    assert r["series"][-1] == ["2026-01-20", None, None, None]
    assert r["score_eligible"] is False
    assert rvxray.rvxray_score(r) is None
    assert all(
        value is None
        for scenario in r["scenarios"]
        for value in (
            scenario["mtm_loss_b"],
            scenario["assumed_unwind_b"],
            scenario["unwind_days_of_dvp"],
        )
    )


def test_rvxray_preserves_metric_specific_exposure_and_marks_partial_pair():
    df = _tff_two_weeks()
    latest = df["date"].max()
    missing = (df["date"] == latest) & (df["contract"] == "UST 2Y NOTE")
    df.loc[missing, "asset_mgr_positions_long_all"] = np.nan
    dvp = pd.Series([4e12], index=pd.to_datetime(["2026-01-13"]))

    r = rvxray.analyze(df, dvp)

    assert r["ok"] and r["asof"] == "2026-01-13"
    assert [row["contract"] for row in r["by_contract"]] == [
        "UST 2Y NOTE",
        "UST 5Y NOTE",
    ]
    assert r["pair_proxy_b"] == 0.5
    assert r["gross_short_b"] == 8.5
    assert r["net_b"] == -4.5
    assert r["dv01_m_per_bp"] == 1.7
    assert r["score_eligible"] is False
    assert rvxray.rvxray_score(r) is None
    by_contract = {row["contract"]: row for row in r["by_contract"]}
    assert by_contract["UST 2Y NOTE"] == {
        "contract": "UST 2Y NOTE",
        "pair_proxy_b": None,
        "gross_short_b": 8.0,
        "net_b": -6.0,
        "missing_fields": ["asset_mgr_positions_long_all"],
    }
    assert r["metric_coverage"]["pair_proxy_b"] == {
        "status": "partial",
        "usable_rows": 1,
        "total_rows": 2,
        "coverage_pct": 50.0,
        "coverage_unit": "expected_contracts",
        "expected_contracts": ["UST 2Y NOTE", "UST 5Y NOTE"],
        "usable_contracts": ["UST 5Y NOTE"],
        "missing_contracts": [],
        "unusable_contracts": ["UST 2Y NOTE"],
        "duplicate_contracts": [],
        "required_fields": [
            "lev_money_positions_short_all",
            "asset_mgr_positions_long_all",
        ],
    }
    assert r["metric_coverage"]["gross_short_b"]["status"] == "complete"
    assert r["metric_coverage"]["net_b"]["status"] == "complete"
    assert "pair proxy 1/2 contracts partial" in r["method"]
    assert r["series"][-1] == ["2026-01-13", None, 8.5, -4.5]
    assert r["series_quality"]["incomplete_points_nulled"]["pair_proxy_b"] == 1
    assert json.dumps(
        {key: value for key, value in r.items() if key != "_pair_full"},
        allow_nan=False,
    )
    assert r["input_quality"] == {
        "ust_rows_received": 4,
        "valid_identity_rows": 4,
        "invalid_identity_rows_excluded": 0,
        "complete_ust_rows": 3,
        "partial_ust_rows_used": 1,
        "incomplete_ust_rows_excluded": 0,
        "complete_pair_history_dates": 1,
        "headline_asof": "2026-01-13",
        "headline_metrics": r["metric_coverage"],
    }


def test_rvxray_keeps_current_gross_when_pair_is_entirely_unavailable():
    df = _tff_two_weeks()
    latest = df["date"].max()
    df.loc[df["date"] == latest, "asset_mgr_positions_long_all"] = np.nan

    r = rvxray.analyze(df, pd.Series(dtype=float))

    assert r["ok"] and r["asof"] == "2026-01-13"
    assert r["pair_proxy_b"] is None
    assert r["gross_short_b"] == 8.5
    assert r["net_b"] == -4.5
    assert r["metric_coverage"]["pair_proxy_b"]["status"] == "unavailable"
    assert r["metric_coverage"]["gross_short_b"]["status"] == "complete"
    assert r["series"][-1] == ["2026-01-13", None, 8.5, -4.5]
    assert r["score_eligible"] is False


def test_rvxray_missing_long_keeps_pair_gross_and_dv01():
    df = _tff_two_weeks()
    latest = df["date"].max()
    missing = (df["date"] == latest) & (df["contract"] == "UST 2Y NOTE")
    df.loc[missing, "lev_money_positions_long_all"] = np.nan

    r = rvxray.analyze(df, pd.Series(dtype=float))

    assert r["pair_proxy_b"] == 7.5
    assert r["gross_short_b"] == 8.5
    assert r["dv01_m_per_bp"] == 1.7
    assert r["net_b"] == 1.5
    assert r["metric_coverage"]["pair_proxy_b"]["status"] == "complete"
    assert r["metric_coverage"]["net_b"]["status"] == "partial"
    assert r["series"][-1] == ["2026-01-13", 7.5, 8.5, None]


def test_rvxray_13_week_change_requires_the_exact_complete_report_date():
    dates = _weeks(15)
    rows = []
    for offset, date in enumerate(dates):
        row = _panel_row(date, "UST 2Y NOTE")
        row["lev_money_positions_short_all"] = 10_000.0 + offset * 1_000.0
        row["lev_money_positions_long_all"] = 20_000.0
        row["asset_mgr_positions_long_all"] = 50_000.0
        rows.append(row)
    complete = rvxray.analyze(pd.DataFrame(rows), pd.Series(dtype=float))

    assert complete["pair_change_13w_b"] == 2.6
    assert complete["score_eligible"] is True
    assert rvxray.rvxray_score(complete) is not None
    assert complete["pair_change_13w_quality"] == {
        "status": "complete",
        "current_asof": dates[-1].date().isoformat(),
        "required_prior_asof": dates[1].date().isoformat(),
        "prior_observation_asof": dates[1].date().isoformat(),
        "current_expected_contracts": ["UST 2Y NOTE"],
        "prior_expected_contracts": ["UST 2Y NOTE"],
        "reason": None,
    }

    rows[1]["asset_mgr_positions_long_all"] = np.nan
    gap = rvxray.analyze(pd.DataFrame(rows), pd.Series(dtype=float))

    assert gap["pair_change_13w_b"] is None
    assert gap["pair_change_13w_quality"]["status"] == "unavailable"
    assert (
        gap["pair_change_13w_quality"]["required_prior_asof"]
        == dates[1].date().isoformat()
    )
    assert (
        gap["pair_change_13w_quality"]["reason"] == "exact_13_week_report_unavailable"
    )
    assert gap["series"][1][1] is None
    assert gap["score_eligible"] is False
    assert rvxray.rvxray_score(gap) is None


def test_rvxray_13_week_change_uses_raw_values_before_publication_rounding():
    dates = _weeks(15)
    rows = []
    for date in dates:
        row = _panel_row(date, "UST 2Y NOTE")
        row["lev_money_positions_short_all"] = 1_000.0
        row["asset_mgr_positions_long_all"] = 10_000.0
        rows.append(row)
    rows[1]["lev_money_positions_short_all"] = 200.0  # $0.04B
    rows[-1]["lev_money_positions_short_all"] = 5_250.0  # $1.05B

    result = rvxray.analyze(pd.DataFrame(rows), pd.Series(dtype=float))

    assert result["pair_proxy_b"] == 1.1
    assert result["pair_change_13w_b"] == 1.0


def test_rvxray_withholds_growth_when_contract_universe_changes():
    dates = _weeks(15)
    rows = [_panel_row(date, "UST 2Y NOTE") for date in dates]
    new_contract = _panel_row(dates[-1], "UST 5Y NOTE")
    new_contract["lev_money_positions_short_all"] = 100_000.0
    new_contract["asset_mgr_positions_long_all"] = 100_000.0
    rows.append(new_contract)

    result = rvxray.analyze(pd.DataFrame(rows), pd.Series(dtype=float))

    assert result["pair_change_13w_b"] is None
    assert result["pair_change_13w_quality"]["reason"] == "contract_universe_changed"
    assert result["pair_change_13w_quality"]["current_expected_contracts"] == [
        "UST 2Y NOTE",
        "UST 5Y NOTE",
    ]
    assert result["pair_change_13w_quality"]["prior_expected_contracts"] == [
        "UST 2Y NOTE"
    ]
    assert result["score_eligible"] is False
    assert rvxray.rvxray_score(result) is None
    assert result["series"][-2][1:] == [None, None, None]
    assert result["series"][-1][1:] == [11.6, 11.8, -10.3]
    assert (
        result["series_quality"]["incomparable_universe_points_nulled"]["pair_proxy_b"]
        == 14
    )


def test_rvxray_scenarios_use_raw_aggregates_before_rounding():
    row = _panel_row(pd.Timestamp("2026-01-20"), "UST 2Y NOTE")
    row["lev_money_positions_short_all"] = 42_450.0  # raw gross = $8.49B
    row["asset_mgr_positions_long_all"] = 50_000.0

    result = rvxray.analyze(pd.DataFrame([row]), pd.Series([4e12]))

    assert result["gross_short_b"] == 8.5
    assert result["scenarios"][0]["assumed_unwind_b"] == 0.8


def test_rvxray_rejects_non_finite_dvp_and_remains_strict_json():
    result = rvxray.analyze(_tff_two_weeks(), pd.Series([np.inf]))

    assert result["dvp_volume_b"] is None
    assert all(row["unwind_days_of_dvp"] is None for row in result["scenarios"])
    assert result["scenario_quality"]["unwind_days_of_dvp"] == {
        "status": "unavailable",
        "required_inputs": ["gross_short_b", "dvp_volume_b"],
        "reason": "dvp_volume_unavailable",
    }
    assert json.dumps(
        {key: value for key, value in result.items() if key != "_pair_full"},
        allow_nan=False,
    )


def test_rvxray_marks_all_null_current_report_unavailable():
    latest_date = pd.Timestamp("2026-01-20")
    current = pd.DataFrame(
        [
            _panel_row(latest_date, "UST 2Y NOTE"),
            _panel_row(latest_date, "UST 5Y NOTE"),
        ]
    )
    current[
        [
            "lev_money_positions_short_all",
            "lev_money_positions_long_all",
            "asset_mgr_positions_long_all",
        ]
    ] = np.nan

    result = rvxray.analyze(
        pd.concat([_tff_two_weeks(), current], ignore_index=True),
        pd.Series(dtype=float),
    )

    assert result["ok"] is True
    assert result["asof"] == "2026-01-20"
    assert result["current_available"] is False
    assert result["current_reason"] == "no usable current UST position metrics"
    assert all(
        result[field] is None
        for field in ("pair_proxy_b", "gross_short_b", "net_b", "dv01_m_per_bp")
    )
    assert all(
        record["status"] == "unavailable"
        for record in result["metric_coverage"].values()
    )


@pytest.mark.parametrize("bad_value", [None, np.nan, np.inf, -np.inf])
def test_rvxray_rejects_report_with_no_complete_exposure(bad_value):
    df = _tff_two_weeks().iloc[[0]].copy()
    df["lev_money_positions_short_all"] = pd.Series(
        [bad_value], index=df.index, dtype=object
    )

    hist = rvxray.position_history(df)
    r = rvxray.analyze(df, pd.Series(dtype=float))

    assert hist.empty
    assert not r["ok"] and "usable UST position metrics" in r["reason"]


def test_crowding_drops_missing_exposure_instead_of_treating_it_as_zero():
    dates = _weeks(61)
    rows = [_panel_row(date, "FED FUNDS") for date in dates]
    rows[-1]["lev_money_positions_long_all"] = None

    r = rvxray.crowding(pd.DataFrame(rows))

    assert r["ok"]
    assert r["rows"][0]["asof"] == dates[-2].date().isoformat()
    assert r["rows"][0]["lev_net_share_oi"] == -0.08
    assert "never zero-filled" in r["method"]


def test_crowding_missing_required_column_degrades_cleanly():
    df = pd.DataFrame([_panel_row(date, "FED FUNDS") for date in _weeks(61)])
    df = df.drop(columns=["lev_money_positions_long_all"])

    r = rvxray.crowding(df)

    assert not r["ok"] and "complete CFTC positioning" in r["reason"]
