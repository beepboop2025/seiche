"""Dated missing-value placeholders are not usable source observations."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from seiche import assemble, config
from seiche.sources import base
from seiche.sources.base import Series


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 14, 9, tzinfo=UTC).astimezone(tz)


@pytest.fixture(autouse=True)
def isolated_sources(monkeypatch):
    monkeypatch.setattr(base, "datetime", FixedClock)
    monkeypatch.setattr(assemble.store, "load_series", lambda _: None)


def series(mnemonic, dates, values):
    spec = config.ALL_SERIES[mnemonic]
    return Series(
        mnemonic, spec.source, spec.remote_id, spec.label, spec.unit, spec.freq,
        "2026-10-14T08:00:00+00:00",
        pd.Series(values, index=pd.to_datetime(dates), dtype=float),
    )


def test_today_placeholder_cannot_freshen_an_old_daily_observation():
    captured = series("SOFR", ["2026-09-28", "2026-10-14"], [3.65, float("nan")])
    original = captured.points.copy()
    provenance = captured.provenance()

    assert captured.asof == "2026-09-28"
    assert captured.staleness == "stale"
    assert provenance["asof"] == "2026-09-28"
    assert provenance["age_days"] == 16
    assert provenance["n_obs"] == 1
    assert provenance["fetched_at"] == "2026-10-14T08:00:00+00:00"
    assert captured.tail_records() == [["2026-09-28", 3.65]]
    pd.testing.assert_series_equal(captured.points, original)


def test_weekly_placeholder_cannot_satisfy_a_due_h41_release():
    captured = series("WALCL", ["2026-09-30", "2026-10-07"], [6500000, None])
    provenance = captured.provenance()

    assert provenance["asof"] == "2026-09-30"
    assert provenance["staleness"] == "aging"
    assert provenance["publication_schedule"]["expected_observation_date"] == "2026-10-07"
    assert provenance["publication_schedule"]["missed_release_weeks"] == 1


@pytest.mark.parametrize("mnemonic", ["SOFR", "WALCL"])
def test_all_null_capture_retains_retrieval_clock_but_has_no_observations(mnemonic):
    captured = series(mnemonic, ["2026-10-13", "2026-10-14"], [None, None])
    provenance = captured.provenance()
    row = next(
        row for row in assemble._provenance({"fred": {mnemonic: captured}})
        if row["mnemonic"] == mnemonic
    )

    assert captured.asof is None
    assert captured.staleness == "dead"
    assert provenance["asof"] is None
    assert provenance["age_days"] is None
    assert provenance["n_obs"] == 0
    assert row["availability"] == "empty"
    assert row["fetched_at"] == captured.fetched_at
    assert captured.tail_records() == []


def test_zero_is_an_observation_and_internal_gaps_do_not_remove_latest_value():
    captured = series("SOFR", ["2026-10-09", "2026-10-12", "2026-10-13"], [3.65, None, 0])

    assert captured.asof == "2026-10-13"
    assert captured.provenance()["n_obs"] == 2
    assert captured.provenance()["staleness"] == "fresh"
    assert captured.tail_records()[-1] == ["2026-10-13", 0.0]


def test_announced_iorb_effective_date_keeps_its_native_date():
    captured = series("IORB", ["2026-10-14", "2026-10-15", "2026-10-16"], [3.65, 3.65, None])

    assert captured.asof == "2026-10-15"
    assert captured.provenance()["asof"] == "2026-10-15"
    assert captured.provenance()["n_obs"] == 2
