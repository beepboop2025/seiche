"""A weekly cache must not conceal the next scheduled Fed balance sheet."""

from datetime import UTC, datetime

import httpx
import pandas as pd
import pytest

from seiche.sources import base, fred, publication


def clock(monkeypatch, now):
    instant = datetime.fromisoformat(now)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz or UTC)

    monkeypatch.setattr(base, "datetime", Clock)
    monkeypatch.setattr(fred, "datetime", Clock)
    return instant


def series(asof="2026-09-23", fetched_at="2026-10-01T20:00:00+00:00", remote_id="WALCL"):
    return base.Series("WALCL", "fred", remote_id, "Fed assets", "$M", "W",
                       fetched_at, pd.Series([100.0], index=pd.to_datetime([asof])))


@pytest.mark.parametrize("now,asof,status,due,expected", [
    ("2026-10-01T20:29:59+00:00", "2026-09-23", "fresh", "2026-09-24T16:30:00-04:00", "2026-09-23"),
    ("2026-10-01T20:30:00+00:00", "2026-09-23", "aging", "2026-10-01T16:30:00-04:00", "2026-09-30"),
    ("2026-10-02T05:30:00+00:00", "2026-09-30", "fresh", "2026-10-01T16:30:00-04:00", "2026-09-30"),
    # Thanksgiving postpones Thursday publication, but not the observation date.
    ("2026-11-26T22:00:00+00:00", "2026-11-18", "fresh", "2026-11-19T16:30:00-05:00", "2026-11-18"),
    ("2026-11-27T21:30:00+00:00", "2026-11-18", "aging", "2026-11-27T16:30:00-05:00", "2026-11-25"),
    # New Year and the spring DST transition use New York wall time.
    ("2026-01-02T21:30:00+00:00", "2025-12-31", "fresh", "2026-01-02T16:30:00-05:00", "2025-12-31"),
    ("2026-03-12T20:30:00+00:00", "2026-03-04", "aging", "2026-03-12T16:30:00-04:00", "2026-03-11"),
    ("2026-10-08T20:30:00+00:00", "2026-09-23", "stale", "2026-10-08T16:30:00-04:00", "2026-10-07"),
])
def test_source_clock_preserves_observation_and_fetch_dates(monkeypatch, now, asof, status, due, expected):
    clock(monkeypatch, now)
    value = series(asof)
    row = value.provenance()
    assert value.staleness == row["staleness"] == status
    assert row["asof"] == asof
    assert row["fetched_at"] == value.fetched_at
    assert row["publication_schedule"]["latest_due_at"] == due
    assert row["publication_schedule"]["expected_observation_date"] == expected
    assert row["publication_schedule"]["actual_published_at"] is None
    assert row["freshness_grace_days"] is None


@pytest.mark.parametrize("remote_id", sorted(publication.H41_WEEKLY_REMOTE_IDS))
def test_every_h41_identity_and_alias_uses_same_clock(monkeypatch, remote_id):
    clock(monkeypatch, "2026-10-02T05:30:00+00:00")
    value = series(remote_id=remote_id)
    value.mnemonic = "SOME_HISTORY_ALIAS"
    assert value.provenance()["staleness"] == "aging"
    assert value.provenance()["freshness_policy"] == "fred-h41-weekly-v1"


@pytest.mark.parametrize("asof,expected", [("2026-10-07", "unknown"), ("NaT", "unknown"), (None, "dead")])
def test_invalid_or_future_dates_never_become_fresh(asof, expected):
    result = publication.publication_freshness("fred", "WALCL", "W", asof, now=datetime(2026, 10, 2, tzinfo=UTC))
    assert result["staleness"] == expected


@pytest.mark.parametrize("source,remote_id,freq", [("other", "WALCL", "W"), ("fred", "GDP", "Q"), ("fred", "WALCL", "D"), ("fred", "NFCI", "W")])
def test_unrelated_series_keep_original_policy(source, remote_id, freq):
    now = datetime(2026, 10, 2, tzinfo=UTC)
    assert publication.publication_freshness(source, remote_id, freq, "2026-09-23", now=now) is None
    assert not publication.publication_refresh_due(source, remote_id, freq, "2026-09-23", "2026-10-01T20:00:00+00:00", now=now)


@pytest.mark.parametrize("now,asof,fetched,due", [
    ("2026-10-01T20:29:59+00:00", "2026-09-23", "2026-10-01T20:00:00+00:00", False),
    ("2026-10-01T20:30:00+00:00", "2026-09-23", "2026-10-01T20:00:00+00:00", True),
    ("2026-10-01T21:29:59+00:00", "2026-09-23", "2026-10-01T20:30:00+00:00", False),
    ("2026-10-01T21:30:00+00:00", "2026-09-23", "2026-10-01T20:30:00+00:00", True),
    ("2026-10-02T05:30:00+00:00", "2026-09-30", "2026-10-01T20:30:00+00:00", False),
])
def test_cache_override_is_release_aware_and_rate_bounded(now, asof, fetched, due):
    assert publication.publication_refresh_due("fred", "WALCL", "W", asof, fetched, now=datetime.fromisoformat(now)) is due


@pytest.mark.parametrize("asof", ["2026-09-23", "2026-09-30"])
@pytest.mark.parametrize("fetched", ["invalid", "2026-10-01T20:31:00", "2026-10-03T00:00:00+00:00", None])
def test_invalid_or_future_fetch_clock_is_refetched(asof, fetched):
    assert publication.publication_refresh_due(
        "fred", "WALCL", "W", asof, fetched,
        now=datetime(2026, 10, 2, tzinfo=UTC),
    )


def test_empty_h41_series_stays_dead(monkeypatch):
    clock(monkeypatch, "2026-10-02T05:30:00+00:00")
    value = series()
    value.points = value.points.iloc[:0]
    assert value.staleness == value.provenance()["staleness"] == "dead"


@pytest.mark.asyncio
async def test_fred_refreshes_pre_release_cache_and_then_reuses_new_data(monkeypatch):
    from seiche import store
    from seiche.config import ALL_SERIES

    clock(monkeypatch, "2026-10-01T20:31:00+00:00")
    cached = [series()]
    monkeypatch.setattr(store, "is_fresh", lambda *args: True)
    monkeypatch.setattr(store, "load_series", lambda *args: cached[0])
    monkeypatch.setattr(store, "save_series", lambda value: cached.__setitem__(0, value))
    requests = []

    def publish(request):
        requests.append(request)
        return httpx.Response(200, text="observation_date,WALCL\n2026-09-23,100\n2026-09-30,101\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(publish)) as client:
        value = await fred.fetch_series(client, ALL_SERIES["WALCL"])
        next_value = await fred.fetch_series(client, ALL_SERIES["WALCL"])
    assert len(requests) == 1
    assert value.asof == next_value.asof == "2026-09-30"
    assert value.points.loc["2026-09-23"] == 100
    assert value.points.loc["2026-09-30"] == 101


@pytest.mark.asyncio
async def test_delayed_mirror_keeps_old_observation_visible_and_retries_hourly(monkeypatch):
    from seiche import store
    from seiche.config import ALL_SERIES

    clock(monkeypatch, "2026-10-01T20:31:00+00:00")
    cached = [series()]
    monkeypatch.setattr(store, "is_fresh", lambda *args: True)
    monkeypatch.setattr(store, "load_series", lambda *args: cached[0])
    monkeypatch.setattr(store, "save_series", lambda value: cached.__setitem__(0, value))
    requests = []

    def publish(request):
        requests.append(request)
        return httpx.Response(200, text="observation_date,WALCL\n2026-09-23,100\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(publish)) as client:
        value = await fred.fetch_series(client, ALL_SERIES["WALCL"])
        assert value.asof == "2026-09-23" and value.staleness == "aging"
        await fred.fetch_series(client, ALL_SERIES["WALCL"])
        assert len(requests) == 1
        clock(monkeypatch, "2026-10-01T21:31:00+00:00")
        await fred.fetch_series(client, ALL_SERIES["WALCL"])
        assert len(requests) == 2
        assert cached[0].asof == "2026-09-23" and cached[0].staleness == "aging"


@pytest.mark.asyncio
async def test_failed_release_fetch_preserves_cached_observation_and_clock(monkeypatch):
    from seiche import store
    from seiche.config import ALL_SERIES

    clock(monkeypatch, "2026-10-01T20:31:00+00:00")
    cached = series()
    saved = []
    monkeypatch.setattr(store, "is_fresh", lambda *args: True)
    monkeypatch.setattr(store, "load_series", lambda *args: cached)
    monkeypatch.setattr(store, "save_series", saved.append)

    async def no_delay(*args):
        pass

    monkeypatch.setattr(fred.asyncio, "sleep", no_delay)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        value = await fred.fetch_series(client, ALL_SERIES["WALCL"])
    assert value is cached and not saved
    assert value.asof == "2026-09-23"
    assert value.fetched_at == "2026-10-01T20:00:00+00:00"
    assert value.staleness == "aging"


def test_catalog_and_csv_tell_daily_fx_observations_from_weekly_release(monkeypatch):
    from seiche import methodology

    monkeypatch.setattr(methodology, "_fetched_mnemonics", lambda: {"INR", "WALCL", "SOFR"})
    catalog = {row["mnemonic"]: row for row in methodology.series_index()["series"]}
    assert catalog["INR"]["cadence"] == "daily observations, weekly publication"
    assert "H.10" in catalog["INR"]["native_lag"]
    assert "H.4.1" in catalog["WALCL"]["native_lag"]
    assert catalog["SOFR"]["cadence"] == "daily"
    value = series(remote_id="DEXINUS")
    value.mnemonic, value.freq = "INR", "D"
    exported = methodology.render_series_csv(value)
    assert "daily observations, weekly publication" in exported
    assert "H.10" in exported
    assert "2026-09-23,100" in exported
