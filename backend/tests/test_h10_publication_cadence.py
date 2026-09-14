"""Daily FX observations must be judged against their weekly H.10 release."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from seiche.sources import base


def series(asof="2026-09-04", *, remote_id="DEXINUS", source="fred", freq="D"):
    points = pd.Series([94.49], index=pd.to_datetime([asof]))
    return base.Series(
        "INR",
        source,
        remote_id,
        "INR/USD",
        "INR",
        freq,
        "2026-09-14T07:00:00+00:00",
        points,
    )


def freeze(monkeypatch, now):
    instant = datetime.fromisoformat(now)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz or UTC)

    monkeypatch.setattr(base, "datetime", Clock)


@pytest.mark.parametrize(
    ("now", "expected", "due"),
    [
        ("2026-09-14T20:14:59+00:00", "fresh", "2026-09-08T16:15:00-04:00"),
        ("2026-09-14T20:15:00+00:00", "aging", "2026-09-14T16:15:00-04:00"),
        ("2026-09-14T20:15:01+00:00", "aging", "2026-09-14T16:15:00-04:00"),
        ("2026-09-21T20:15:00+00:00", "stale", "2026-09-21T16:15:00-04:00"),
        ("2026-10-19T20:15:00+00:00", "dead", "2026-10-19T16:15:00-04:00"),
    ],
)
def test_release_boundary_advances_without_new_fetch(monkeypatch, now, expected, due):
    freeze(monkeypatch, now)
    value = series()
    original = value.points.copy(deep=True)
    row = value.provenance()
    assert value.staleness == row["staleness"] == expected
    assert row["publication_frequency"] == "W"
    assert row["publication_schedule"]["latest_due_at"] == due
    assert row["publication_schedule"]["clock_precision"] == "scheduled"
    assert row["publication_schedule"]["actual_published_at"] is None
    assert row["freq"] == "D"
    assert row["asof"] == "2026-09-04"
    assert row["fetched_at"] == value.fetched_at
    assert row["freshness_grace_days"] is None
    pd.testing.assert_series_equal(value.points, original)


@pytest.mark.parametrize(
    ("now", "asof", "expected", "week"),
    [
        ("2026-09-07T21:00:00+00:00", "2026-08-28", "fresh", "2026-08-24"),
        ("2026-09-08T20:14:59+00:00", "2026-08-28", "fresh", "2026-08-24"),
        ("2026-09-08T20:15:00+00:00", "2026-08-28", "aging", "2026-08-31"),
        ("2026-09-08T20:15:01+00:00", "2026-09-04", "fresh", "2026-08-31"),
        # Friday is an observed federal holiday; a Thursday print is sufficient.
        ("2026-07-06T20:15:00+00:00", "2026-07-02", "fresh", "2026-06-29"),
        # DST starts March 8 and ends November 1; use New York, not a UTC constant.
        ("2026-03-09T20:14:59+00:00", "2026-02-27", "fresh", "2026-02-23"),
        ("2026-03-09T20:15:00+00:00", "2026-02-27", "aging", "2026-03-02"),
        ("2026-11-02T21:14:59+00:00", "2026-10-23", "fresh", "2026-10-19"),
        ("2026-11-02T21:15:00+00:00", "2026-10-23", "aging", "2026-10-26"),
        ("2024-01-02T21:14:59+00:00", "2023-12-22", "fresh", "2023-12-18"),
        ("2024-01-02T21:15:00+00:00", "2023-12-29", "fresh", "2023-12-25"),
    ],
)
def test_holidays_dst_and_year_boundary(monkeypatch, now, asof, expected, week):
    freeze(monkeypatch, now)
    row = series(asof).provenance()
    assert row["staleness"] == expected
    assert row["publication_schedule"]["expected_observation_week_start"] == week


@pytest.mark.parametrize("asof", ["2026-09-15", "NaT"])
def test_invalid_or_future_observations_never_become_fresh(monkeypatch, asof):
    freeze(monkeypatch, "2026-09-14T07:00:00+00:00")
    row = series(asof).provenance()
    assert row["staleness"] == "unknown"


def test_empty_series_stays_dead(monkeypatch):
    freeze(monkeypatch, "2026-09-14T07:00:00+00:00")
    value = series()
    value.points = value.points.iloc[:0]
    assert value.staleness == value.provenance()["staleness"] == "dead"


@pytest.mark.parametrize("remote_id", ["SOFR", "IOER", "TEDRATE", "DEXFAKE"])
def test_unrelated_daily_and_historical_series_keep_existing_policy(
    monkeypatch, remote_id
):
    freeze(monkeypatch, "2026-09-14T07:00:00+00:00")
    row = series(remote_id=remote_id).provenance()
    assert row["staleness"] == "stale"
    assert row["freshness_grace_days"] == 4
    assert "publication_schedule" not in row
    assert series("2021-01-01", remote_id=remote_id).staleness == "dead"


@pytest.mark.parametrize(("source", "freq"), [("ecb", "D"), ("fred", "M")])
def test_schedule_requires_exact_source_and_daily_frequency(monkeypatch, source, freq):
    freeze(monkeypatch, "2026-09-14T07:00:00+00:00")
    assert "publication_schedule" not in series(source=source, freq=freq).provenance()


def test_all_configured_h10_identities_and_aliases_share_cached_calendar(monkeypatch):
    from seiche import config
    from seiche.sources import publication

    freeze(monkeypatch, "2026-09-14T07:00:00+00:00")
    configured = [
        spec
        for group in vars(config).values()
        if isinstance(group, list)
        for spec in group
        if getattr(spec, "source", None) == "fred"
        and getattr(spec, "remote_id", None) in publication.H10_DAILY_REMOTE_IDS
    ]
    assert len(configured) == 27
    assert {spec.remote_id for spec in configured} == publication.H10_DAILY_REMOTE_IDS
    assert {spec.mnemonic for spec in configured} >= {"EURUSD_LONG", "JPY_LONG"}
    calls = []
    calendar = publication.USFederalHolidayCalendar

    def counted_calendar():
        calls.append(True)
        return calendar()

    publication._federal_holidays.cache_clear()
    publication._release_for_week.cache_clear()
    monkeypatch.setattr(publication, "USFederalHolidayCalendar", counted_calendar)
    try:
        for spec in configured * 2:
            value = series(remote_id=spec.remote_id)
            value.mnemonic = spec.mnemonic
            assert value.provenance()["staleness"] == "fresh"
        assert len(calls) == 1
    finally:
        publication._federal_holidays.cache_clear()
        publication._release_for_week.cache_clear()


def test_fetch_time_does_not_refresh_missing_weeks(monkeypatch):
    freeze(monkeypatch, "2026-09-14T20:15:00+00:00")
    value = series("2026-08-28")
    before = value.provenance()
    value.fetched_at = "2026-09-14T20:15:00+00:00"
    after = value.provenance()
    assert before["staleness"] == after["staleness"] == "stale"
    assert {k: v for k, v in before.items() if k != "fetched_at"} == {
        k: v for k, v in after.items() if k != "fetched_at"
    }
