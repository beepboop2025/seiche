"""A broken series must preserve successful peers and honest cache clocks."""
import asyncio

import httpx
import pandas as pd
import pytest

from seiche.config import ALL_SERIES
from seiche.sources import fred, ofr
from seiche.sources.base import Series


def series(mnemonic):
    s = ALL_SERIES[mnemonic]
    return Series(s.mnemonic, s.source, s.remote_id, s.label, s.unit, s.freq,
                  "2026-10-23T15:00:00+00:00",
                  pd.Series([3.9], index=pd.to_datetime(["2026-10-22"])))


@pytest.mark.asyncio
@pytest.mark.parametrize("module,names", [(fred, ["SOFR", "IORB"]), (ofr, ["BGCR", "TGCR"])])
@pytest.mark.parametrize("operation", ["is_fresh", "load_series"])
async def test_cache_failure_is_per_series_and_keeps_original_clocks(monkeypatch, module, names, operation):
    values = {m: series(m) for m in names}
    writes = []
    def fresh(m, *_):
        if m == names[0] and operation == "is_fresh":
            raise RuntimeError("cache failed; token=private")
        return True
    def load(m):
        if m == names[0] and operation == "load_series":
            raise RuntimeError("cache failed; token=private")
        return values[m]
    monkeypatch.setattr(module.store, "is_fresh", fresh)
    monkeypatch.setattr(module.store, "load_series", load)
    monkeypatch.setattr(module.store, "save_series", writes.append)
    def network(_request):
        raise AssertionError("fresh peer and cached fallback need no network")
    faults = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(network)) as client:
        result = await module.fetch_many(client, names, faults)
    assert result[names[1]] is values[names[1]]
    assert len(faults) == 1
    assert faults[0]["series"] == names[0]
    assert faults[0]["cache_used"] is (operation == "is_fresh")
    assert "private" not in str(faults)
    assert writes == []
    if operation == "is_fresh":
        assert result[names[0]] is values[names[0]]
        assert result[names[0]].fetched_at == "2026-10-23T15:00:00+00:00"
    else:
        assert names[0] not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("module,names", [(fred, ["SOFR", "IORB"]), (ofr, ["BGCR", "TGCR"])])
async def test_unexpected_adapter_error_does_not_discard_successful_peer(monkeypatch, module, names):
    good = series(names[1])
    async def fetch(_client, spec, **_kwargs):
        if spec.mnemonic == names[0]:
            raise RuntimeError("private upstream response")
        return good
    monkeypatch.setattr(module, "fetch_series", fetch)
    faults = []
    result = await module.fetch_many(None, names, faults)
    assert result == {names[1]: good}
    assert len(faults) == 1
    assert faults[0]["series"] == names[0]
    assert faults[0]["cache_used"] is False
    assert "private" not in str(faults)


@pytest.mark.asyncio
@pytest.mark.parametrize("module,mnemonic", [(fred, "SOFR"), (ofr, "BGCR")])
async def test_task_cancellation_is_not_misreported_as_source_failure(monkeypatch, module, mnemonic):
    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(module, "fetch_series", cancelled)
    faults = []
    with pytest.raises(asyncio.CancelledError):
        await module.fetch_many(None, [mnemonic], faults)
    assert faults == []


@pytest.mark.asyncio
async def test_ofr_denial_retains_cache_but_reports_failed_refresh(monkeypatch):
    cached = series("BGCR")
    monkeypatch.setattr(ofr.store, "is_fresh", lambda *_: False)
    monkeypatch.setattr(ofr.store, "load_series", lambda *_: cached)
    calls = []
    def denied(request):
        calls.append(request)
        return httpx.Response(403, text="private upstream text")
    faults = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(denied)) as client:
        result = await ofr.fetch_many(client, ["BGCR"], faults)
    assert result["BGCR"] is cached
    assert cached.fetched_at == "2026-10-23T15:00:00+00:00"
    assert len(calls) == len(faults) == 1
    assert faults[0]["http_status"] == 403
    assert faults[0]["cache_used"] is True
    assert "private" not in str(faults)
