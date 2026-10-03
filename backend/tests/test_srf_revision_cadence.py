"""An unchanged funding rate can still have a new official observation date."""

from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd
import pytest

from seiche import store
from seiche.config import ALL_SERIES
from seiche.sources import base, fred


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "srf.sqlite")
    instant = [datetime(2026, 10, 3, 8, 9, 17, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant[0].astimezone(tz or UTC)

    for module in (base, fred, store):
        monkeypatch.setattr(module, "datetime", Clock)
    spec = ALL_SERIES["SRF_CEILING"]
    original = base.Series(
        spec.mnemonic,
        spec.source,
        spec.remote_id,
        spec.label,
        spec.unit,
        spec.freq,
        instant[0].isoformat(),
        pd.Series([4.0, 4.0], index=pd.to_datetime(["2026-10-01", "2026-10-02"])),
    )
    store.save_series(original)
    return instant, original, spec


@pytest.mark.asyncio
async def test_new_same_value_date_collected_at_hourly_boundary(cache):
    instant, original, spec = cache
    assert spec.ttl_minutes == 60
    start = instant[0]
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                "observation_date,DFEDTARU\n2026-10-01,4.0\n"
                "2026-10-02,4.0\n2026-10-03,4.0\n"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        instant[0] = start + timedelta(hours=1) - timedelta(seconds=1)
        cached = await fred.fetch_series(client, spec)
        assert not requests
        assert cached.asof == "2026-10-02"
        assert cached.fetched_at == original.fetched_at
        instant[0] = start + timedelta(hours=1)
        current = await fred.fetch_series(client, spec)
        assert len(requests) == 1
        assert requests[0].url.params["id"] == "DFEDTARU"
        assert current.asof == "2026-10-03"
        assert current.fetched_at == instant[0].isoformat()
        assert current.points.tolist() == [4.0, 4.0, 4.0]
        assert current.points.diff().dropna().eq(0).all()
        again = await fred.fetch_series(client, spec)
        assert len(requests) == 1
        assert again.fetched_at == current.fetched_at
    assert (
        store.load_series_as_of("SRF_CEILING", original.fetched_at).asof == "2026-10-02"
    )
    assert (
        store.load_series_as_of("SRF_CEILING", current.fetched_at).asof == "2026-10-03"
    )


@pytest.mark.asyncio
async def test_failed_hourly_check_cannot_invent_a_new_date(cache, monkeypatch):
    instant, original, spec = cache
    instant[0] += timedelta(hours=1)
    requests = []

    async def no_delay(*_args):
        pass

    monkeypatch.setattr(fred.asyncio, "sleep", no_delay)

    def unavailable(request):
        requests.append(request)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        current = await fred.fetch_series(client, spec)
    assert len(requests) == 4
    assert current.asof == original.asof
    assert current.fetched_at == original.fetched_at
    assert store.load_series("SRF_CEILING").points.equals(original.points)
