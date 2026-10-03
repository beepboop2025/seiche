"""Daily GDP retrieval must capture revisions to unchanged quarterly dates."""

from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd
import pytest

from seiche import store
from seiche.config import ALL_SERIES
from seiche.sources import base, fred


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "gdp.sqlite")
    instant = [datetime(2026, 9, 29, 12, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant[0].astimezone(tz or UTC)

    for module in (base, fred, store):
        monkeypatch.setattr(module, "datetime", Clock)
    spec = ALL_SERIES["GDP"]
    first = base.Series(
        spec.mnemonic,
        spec.source,
        spec.remote_id,
        spec.label,
        spec.unit,
        spec.freq,
        instant[0].isoformat(),
        pd.Series(
            [31865.721, 32486.066], index=pd.to_datetime(["2026-01-01", "2026-04-01"])
        ),
    )
    store.save_series(first)
    return instant, first, spec


@pytest.mark.asyncio
async def test_same_quarter_revision_is_fetched_at_daily_boundary_and_keeps_old_vintage(
    cache,
):
    instant, original, spec = cache
    start = instant[0]
    requests = []

    def revised(request):
        requests.append(request)
        return httpx.Response(
            200,
            text="observation_date,GDP\n2026-01-01,31906.274\n2026-04-01,32563.030\n",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(revised)) as client:
        instant[0] = start + timedelta(hours=24) - timedelta(seconds=1)
        cached = await fred.fetch_series(client, spec)
        assert not requests
        assert cached.fetched_at == original.fetched_at
        instant[0] = start + timedelta(hours=24)
        current = await fred.fetch_series(client, spec)
        assert len(requests) == 1
        assert requests[0].url.params["id"] == "GDP"
        assert requests[0].url.params["cosd"] == spec.start
        assert current.asof == original.asof == "2026-04-01"
        assert current.freq == spec.freq == "Q"
        assert current.fetched_at == instant[0].isoformat()
        assert current.points.loc["2026-04-01"] == 32563.030
        assert current.points.loc["2026-01-01"] == 31906.274
        unchanged = await fred.fetch_series(client, spec)
        assert len(requests) == 1
        assert unchanged.fetched_at == current.fetched_at
    old_vintage = store.load_series_as_of("GDP", original.fetched_at)
    new_vintage = store.load_series_as_of("GDP", current.fetched_at)
    assert old_vintage.points.loc["2026-04-01"] == 32486.066
    assert new_vintage.points.loc["2026-04-01"] == 32563.030
    assert old_vintage.points.loc["2026-01-01"] == 31865.721
    assert new_vintage.points.loc["2026-01-01"] == 31906.274


@pytest.mark.asyncio
async def test_failed_daily_check_cannot_advance_fetch_or_vintage_clock(
    cache, monkeypatch
):
    instant, original, spec = cache
    instant[0] += timedelta(days=1)
    requests = []

    async def no_delay(*_args):
        pass

    monkeypatch.setattr(fred.asyncio, "sleep", no_delay)

    def unavailable(request):
        requests.append(request)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        result = await fred.fetch_series(client, spec)
    assert len(requests) == 4  # Original bounded transport retries remain unchanged.
    assert result.fetched_at == original.fetched_at
    assert result.points.equals(original.points)
    stored = store.load_series("GDP")
    assert stored.fetched_at == original.fetched_at
    assert stored.points.equals(original.points)


@pytest.mark.asyncio
async def test_unchanged_daily_publication_keeps_quarterly_observation_date(cache):
    instant, original, spec = cache
    instant[0] += timedelta(days=1)
    text = "observation_date,GDP\n2026-01-01,31865.721\n2026-04-01,32486.066\n"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=text))
    ) as client:
        result = await fred.fetch_series(client, spec)
    assert result.asof == original.asof
    assert result.freq == "Q"
    assert result.points.equals(original.points)
    assert result.fetched_at != original.fetched_at
