"""Explicit source denials must survive retries, caches, and batch isolation."""

from datetime import UTC, datetime
from email.utils import format_datetime

import httpx
import pandas as pd
import pytest

from seiche.config import ALL_SERIES
from seiche.sources import fred
from seiche.sources.base import Series, SourceFault


@pytest.fixture
def refresh(monkeypatch):
    spec = ALL_SERIES["SOFR"]
    cached = Series(
        spec.mnemonic, spec.source, spec.remote_id, spec.label, spec.unit,
        spec.freq, "2026-10-16T14:00:00+00:00",
        pd.Series([4.5], index=pd.to_datetime(["2026-10-15"])),
    )
    state = {"cached": cached, "saved": [], "delays": [], "requests": []}
    monkeypatch.setattr(fred.store, "is_fresh", lambda *_args: False)
    monkeypatch.setattr(fred.store, "load_series", lambda *_args: state["cached"])
    monkeypatch.setattr(fred.store, "save_series", state["saved"].append)
    monkeypatch.setattr(fred.random, "random", lambda: 0.0)

    async def sleep(delay):
        state["delays"].append(delay)

    monkeypatch.setattr(fred.asyncio, "sleep", sleep)
    return state, spec


def client_for(state, responses):
    sequence = iter(responses)

    def handler(request):
        state["requests"].append(request)
        response = next(sequence)
        if isinstance(response, Exception):
            raise response
        return response

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 451])
@pytest.mark.parametrize("has_cache", [False, True])
async def test_denial_is_attempted_once_and_reported_with_or_without_cache(
    refresh, status, has_cache,
):
    state, spec = refresh
    original = state["cached"]
    if not has_cache:
        state["cached"] = None
    faults = []
    async with client_for(state, [
        httpx.Response(status, headers={"Retry-After": "1"}, text="private upstream text"),
    ]) as client:
        result = await fred.fetch_many(client, [spec.mnemonic], faults)
    assert len(state["requests"]) == 1
    assert state["delays"] == state["saved"] == []
    assert len(faults) == 1
    fault = faults[0]
    assert fault["reason"] == "upstream_access_denied"
    assert fault["http_status"] == status
    assert fault["refresh_status"] == "failed"
    assert fault["cache_used"] is has_cache
    assert fault["retry_deferred"] is False
    assert "private upstream text" not in fault["detail"]
    if has_cache:
        assert result[spec.mnemonic] is original
        assert original.fetched_at == "2026-10-16T14:00:00+00:00"
        assert original.asof == "2026-10-15"
        assert original.points.iloc[0] == 4.5
    else:
        assert result == {}


@pytest.mark.asyncio
async def test_direct_call_without_fault_sink_keeps_sourcefault_contract(refresh):
    state, spec = refresh
    state["cached"] = None
    async with client_for(state, [httpx.Response(403)]) as client:
        with pytest.raises(SourceFault, match="upstream_access_denied") as raised:
            await fred.fetch_series(client, spec)
    assert raised.value.source == "fred"
    assert isinstance(raised.value.__cause__, httpx.HTTPStatusError)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 422])
async def test_other_permanent_http_failures_are_not_retried(refresh, status):
    state, spec = refresh
    faults = []
    async with client_for(state, [httpx.Response(status)]) as client:
        result = await fred.fetch_series(client, spec, faults=faults)
    assert result is state["cached"]
    assert len(state["requests"]) == 1
    assert state["delays"] == []
    assert faults[0]["http_status"] == status
    assert faults[0]["reason"] == "source_refresh_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    httpx.Response(503),
    httpx.Response(429),
    httpx.ReadTimeout("transient failure"),
])
async def test_transient_failure_retries_then_saves_success(refresh, failure):
    state, spec = refresh
    faults = []
    async with client_for(state, [
        failure, httpx.Response(200, text="DATE,SOFR\n2026-10-19,4.6\n"),
    ]) as client:
        result = await fred.fetch_series(client, spec, faults=faults)
    assert len(state["requests"]) == 2
    assert state["delays"] == [1.5]
    assert state["saved"] == [result]
    assert result.asof == "2026-10-19"
    assert faults == []


@pytest.mark.asyncio
async def test_exhausted_retries_do_not_sleep_after_final_attempt(refresh):
    state, spec = refresh
    faults = []
    async with client_for(state, [httpx.Response(503) for _ in range(4)]) as client:
        result = await fred.fetch_series(client, spec, faults=faults)
    assert result is state["cached"]
    assert len(state["requests"]) == 4
    assert state["delays"] == [1.5, 3.0, 6.0]
    assert faults[0]["reason"] == "upstream_unavailable"
    assert faults[0]["cache_used"] is True
    assert state["saved"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", ["7", "Tue, 20 Oct 2026 12:00:07 GMT"])
async def test_retry_after_seconds_and_http_date_are_honored(refresh, monkeypatch, retry_after):
    state, spec = refresh

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 20, 12, tzinfo=UTC).astimezone(tz or UTC)

    monkeypatch.setattr(fred, "datetime", Clock)
    async with client_for(state, [
        httpx.Response(429, headers={"Retry-After": retry_after}),
        httpx.Response(200, text="DATE,SOFR\n2026-10-19,4.6\n"),
    ]) as client:
        await fred.fetch_series(client, spec)
    assert state["delays"] == [7.0]
    assert len(state["requests"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", [
    "86400", "9" * 200, format_datetime(datetime(2099, 1, 1, tzinfo=UTC)),
])
async def test_long_retry_after_defers_without_shortening_deadline(refresh, retry_after):
    state, spec = refresh
    faults = []
    async with client_for(state, [
        httpx.Response(429, headers={"Retry-After": retry_after}),
    ]) as client:
        result = await fred.fetch_series(client, spec, faults=faults)
    assert result is state["cached"]
    assert len(state["requests"]) == 1
    assert state["delays"] == state["saved"] == []
    assert faults[0]["reason"] == "upstream_rate_limited"
    assert faults[0]["retry_deferred"] is True


@pytest.mark.asyncio
async def test_retry_wait_budget_is_cumulative(refresh):
    state, spec = refresh
    faults = []
    async with client_for(state, [
        httpx.Response(429, headers={"Retry-After": "20"}),
        httpx.Response(429, headers={"Retry-After": "20"}),
    ]) as client:
        await fred.fetch_series(client, spec, faults=faults)
    assert state["delays"] == [20.0]
    assert len(state["requests"]) == 2
    assert faults[0]["retry_deferred"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", ["not-a-date", "-5", "NaN", "Infinity", "1.5"])
async def test_invalid_retry_after_uses_normal_bounded_backoff(refresh, retry_after):
    state, spec = refresh
    async with client_for(state, [
        httpx.Response(429, headers={"Retry-After": retry_after}),
        httpx.Response(200, text="DATE,SOFR\n2026-10-19,4.6\n"),
    ]) as client:
        await fred.fetch_series(client, spec)
    assert state["delays"] == [1.5]


@pytest.mark.asyncio
async def test_parse_failure_is_reported_without_network_retry(refresh):
    state, spec = refresh
    faults = []
    async with client_for(state, [httpx.Response(200, text="bad,csv,shape\n1,2,3\n")]) as client:
        result = await fred.fetch_series(client, spec, faults=faults)
    assert result is state["cached"]
    assert len(state["requests"]) == 1
    assert state["delays"] == state["saved"] == []
    assert faults[0]["reason"] == "source_refresh_failed"


@pytest.mark.asyncio
async def test_local_protocol_error_is_not_treated_as_transient(refresh):
    state, spec = refresh
    faults = []
    async with client_for(state, [httpx.LocalProtocolError("invalid request")]) as client:
        await fred.fetch_series(client, spec, faults=faults)
    assert len(state["requests"]) == 1
    assert state["delays"] == []
    assert faults[0]["reason"] == "source_refresh_failed"


@pytest.mark.asyncio
async def test_valid_cache_hit_does_not_create_refresh_fault(refresh, monkeypatch):
    state, spec = refresh
    monkeypatch.setattr(fred.store, "is_fresh", lambda *_args: True)
    faults = []
    async with client_for(state, []) as client:
        result = await fred.fetch_many(client, [spec.mnemonic], faults)
    assert result[spec.mnemonic] is state["cached"]
    assert state["requests"] == state["saved"] == []
    assert faults == []
