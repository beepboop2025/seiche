"""ECB failures retain safe categories and never manufacture a new fetch clock."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pandas as pd
import pytest

from seiche import ingest_runtime, store
from seiche.config import ALL_SERIES
from seiche.public_faults import (
    project_public_fault,
    sanitize_fault_record,
    sanitize_public_fault_payload,
)
from seiche.sources import ecb
from seiche.sources.base import Series, SourceFault, utcnow_iso


PRIVATE = "https://operator:issued-secret@example.invalid/?token=issued-secret"
CACHED_AT = "2000-01-03T00:00:00+00:00"


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "ecb.sqlite")


@pytest.mark.asyncio
@pytest.mark.parametrize(("failure", "category", "detail"), [
    ("timeout", "TIMEOUT", "source collection timed out"),
    ("http", "HTTP_ERROR", "official source returned an HTTP error"),
    ("validation", "VALIDATION_ERROR", "source response failed validation"),
    ("transport", "TRANSPORT_ERROR", "official source connection failed"),
    ("unknown", "INTERNAL_ERROR", "collector failed"),
])
async def test_cold_failure_survives_snapshot_and_public_projections(
    isolated_store, failure, category, detail,
):
    def handler(request):
        if failure == "http":
            return httpx.Response(503, text=PRIVATE)
        if failure == "validation":
            return httpx.Response(200, text=f"{PRIVATE},unexpected\n1,2\n")
        error = {
            "timeout": httpx.ReadTimeout,
            "transport": httpx.ConnectError,
            "unknown": RuntimeError,
        }[failure]
        raise error(PRIVATE)

    faults = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await ecb.fetch_many(client, ["ESTR"], faults) == {}

    assert faults == [{"source": "ecb", "detail": f"{category}: {detail}"}]
    stored = json.loads(json.dumps([sanitize_fault_record(row) for row in faults]))
    projected = [project_public_fault(row) for row in stored]
    expected = {"source": "ecb", "status": "FAILED", "category": category, "detail": detail}
    assert projected == [expected]
    assert sanitize_public_fault_payload({"faults": projected}) == {"faults": [expected]}
    summary = ingest_runtime._completed_summary(
        {}, faults, started_at=datetime(2026, 10, 3, tzinfo=UTC),
        finished_at=datetime(2026, 10, 3, 0, 1, tzinfo=UTC),
    )
    assert summary["faults"] == [{**expected, "market_id": "GLOBAL"}]
    serialized = json.dumps([faults, stored, projected, summary])
    assert "issued-secret" not in serialized
    assert "https://" not in serialized
    assert ALL_SERIES["ESTR"].remote_id not in serialized
    assert store.load_series("ESTR") is None


@pytest.mark.asyncio
async def test_boundary_does_not_format_exception_or_its_cause(isolated_store):
    class HostileTimeout(httpx.ReadTimeout):
        def __str__(self):
            raise AssertionError("exception diagnostics must not be formatted")

    def handler(request):
        raise HostileTimeout(PRIVATE) from ValueError(PRIVATE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceFault) as caught:
            await ecb.fetch_series(client, ALL_SERIES["ESTR"])

    assert caught.value.detail == "TIMEOUT: source collection timed out"
    assert str(caught.value) == "ecb: TIMEOUT: source collection timed out"


def cached_series(fetched_at):
    spec = ALL_SERIES["ESTR"]
    return Series(spec.mnemonic, "ecb", spec.remote_id, spec.label, spec.unit,
                  spec.freq, fetched_at,
                  pd.Series([2.442], index=pd.to_datetime(["2000-01-01"])))


@pytest.mark.asyncio
async def test_failed_refresh_preserves_cached_values_and_fetch_clock(isolated_store):
    original = cached_series(CACHED_AT)
    store.save_series(original)
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout(PRIVATE)

    faults = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = (await ecb.fetch_many(client, ["ESTR"], faults))["ESTR"]

    assert len(requests) == 1
    assert faults == []  # Preserve the existing cached fallback policy.
    for value in (result, store.load_series("ESTR")):
        assert value.fetched_at == CACHED_AT
        assert value.asof == original.asof
        pd.testing.assert_series_equal(value.points, original.points)


@pytest.mark.asyncio
async def test_fresh_cache_performs_no_request(isolated_store):
    original = cached_series(utcnow_iso())
    store.save_series(original)
    requests = []

    def handler(request):
        requests.append(request)
        raise AssertionError("fresh cache must avoid the upstream")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ecb.fetch_series(client, ALL_SERIES["ESTR"])

    assert requests == []
    assert result.fetched_at == original.fetched_at
    pd.testing.assert_series_equal(result.points, original.points)
