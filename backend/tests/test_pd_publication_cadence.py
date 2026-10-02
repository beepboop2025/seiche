"""PD observations and cache clocks survive weekly publication boundaries."""
from datetime import UTC, datetime

import httpx
import pytest

from seiche import store
from seiche.config import PD_POSITION_SERIES
from seiche.sources import base, nyfed, publication


def clock(monkeypatch, value):
    instant = datetime.fromisoformat(value)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz or UTC)

    monkeypatch.setattr(nyfed, "datetime", Clock)
    monkeypatch.setattr(base, "datetime", Clock)


@pytest.mark.parametrize("now,asof,fetched,due", [
    ("2026-10-01T20:14:59+00:00", "2026-09-16", "2026-10-01T20:00:04+00:00", False),
    ("2026-10-01T20:15:00+00:00", "2026-09-16", "2026-10-01T20:00:04+00:00", True),
    ("2026-10-01T21:14:59+00:00", "2026-09-16", "2026-10-01T20:15:00+00:00", False),
    ("2026-10-01T21:15:00+00:00", "2026-09-16", "2026-10-01T20:15:00+00:00", True),
    ("2026-10-02T07:00:00+00:00", "2026-09-23", "2026-10-01T20:15:00+00:00", False),
    # Winter and summer use New York wall time, not a fixed UTC hour.
    ("2026-02-05T21:14:59+00:00", "2026-01-21", "2026-02-05T21:00:00+00:00", False),
    ("2026-02-05T21:15:00+00:00", "2026-01-21", "2026-02-05T21:00:00+00:00", True),
    ("2026-03-12T20:15:00+00:00", "2026-02-25", "2026-03-12T20:00:00+00:00", True),
    # The nominal deadline is a polling opportunity, not a claimed holiday
    # release. Old source data remains old until the publisher supplies it.
    ("2026-11-26T21:15:00+00:00", "2026-11-11", "2026-11-26T21:00:00+00:00", True),
    ("2027-01-01T12:00:00+00:00", "2026-12-23", "2026-12-31T21:15:00+00:00", False),
])
def test_nominal_boundary_and_bounded_successful_checks(now, asof, fetched, due):
    assert publication.pd_positions_refresh_due([asof] * 8, fetched, now=datetime.fromisoformat(now)) is due


@pytest.mark.parametrize("bad", [None, "invalid", "2026-10-02T08:00:00", "2026-10-03T00:00:00+00:00"])
def test_bad_fetch_clock_cannot_hide_a_release(bad):
    assert publication.pd_positions_refresh_due(["2026-09-23"] * 8, bad, now=datetime(2026, 10, 2, tzinfo=UTC))


@pytest.mark.parametrize("bad", [None, "NaT", "2026-09-16", "2026-10-07"])
def test_every_bucket_must_have_a_valid_current_observation(bad):
    assert publication.pd_positions_refresh_due(["2026-09-23"] * 7 + [bad], "2026-10-01T20:00:00+00:00", now=datetime(2026, 10, 2, tzinfo=UTC))


def test_naive_evaluation_time_rejected():
    with pytest.raises(ValueError):
        publication.pd_positions_refresh_due(["2026-09-23"], "2026-10-01T20:00:00+00:00", now=datetime(2026, 10, 2))


def install_cache(monkeypatch):
    cached = [{"fetched_at": "2026-10-01T20:00:04+00:00", "series": {
        identifier: [["2026-09-16", "1000"]] for identifier in PD_POSITION_SERIES
    }}]
    saved = []
    monkeypatch.setattr(store, "load_blob", lambda *args: cached[0])

    def save(key, value):
        assert key == "nyfed_pd_positions"
        saved.append(value)
        cached[0] = value

    monkeypatch.setattr(store, "save_blob", save)
    return cached, saved


@pytest.mark.asyncio
async def test_pre_release_cache_refreshes_all_buckets_then_reuses_current_data(monkeypatch):
    clock(monkeypatch, "2026-10-01T20:16:00+00:00")
    cached, saved = install_cache(monkeypatch)
    requested = []

    def reply(request):
        requested.append(request.url.path)
        return httpx.Response(200, json={"pd": {"timeseries": [
            {"asofdate": "2026-09-16", "value": "1000"},
            {"asofdate": "2026-09-23", "value": "2000"},
        ]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        result = await nyfed.fetch_pd_positions(client)
        await nyfed.fetch_pd_positions(client)
    assert set(requested) == {f"/api/pd/get/{identifier}.json" for identifier in PD_POSITION_SERIES}
    assert len(requested) == 8 and len(saved) == 1
    assert result["fetched_at"] == "2026-10-01T20:16:00+00:00"
    assert all(s.index.max().date().isoformat() == "2026-09-23" and s.iloc[-1] == 2.0 for s in result["positions"].values())


@pytest.mark.asyncio
async def test_delayed_holiday_release_preserves_dates_and_rechecks_hourly(monkeypatch):
    clock(monkeypatch, "2026-10-01T20:16:00+00:00")
    cached, saved = install_cache(monkeypatch)
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(200, json={"pd": {"timeseries": [{"asofdate": "2026-09-16", "value": "1000"}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        result = await nyfed.fetch_pd_positions(client)
        await nyfed.fetch_pd_positions(client)
        assert len(calls) == 8
        clock(monkeypatch, "2026-10-01T21:16:00+00:00")
        await nyfed.fetch_pd_positions(client)
    assert len(calls) == 16
    assert all(s.index.max().date().isoformat() == "2026-09-16" for s in result["positions"].values())
    assert cached[0]["fetched_at"] == "2026-10-01T21:16:00+00:00"


@pytest.mark.asyncio
async def test_network_failure_preserves_cache_and_original_fetch_clock(monkeypatch):
    clock(monkeypatch, "2026-10-01T20:16:00+00:00")
    cached, saved = install_cache(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        result = await nyfed.fetch_pd_positions(client)
    assert not saved
    assert result["fetched_at"] == "2026-10-01T20:00:04+00:00"
    assert all(s.index.max().date().isoformat() == "2026-09-16" for s in result["positions"].values())
