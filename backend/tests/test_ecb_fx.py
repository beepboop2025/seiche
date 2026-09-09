"""ECB FX observations preserve orientation, dates, history and raw evidence."""

from __future__ import annotations

import hashlib
import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from seiche import store
from seiche.sources import ecb_fx
from seiche.sources.base import SourceFault

FETCHED = "2026-09-09T00:00:00+00:00"


def xml(days: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" '
        'xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">'
        '<gesmes:subject>Reference rates</gesmes:subject>'
        '<gesmes:Sender><gesmes:name>European Central Bank</gesmes:name></gesmes:Sender>'
        f'<Cube>{days}</Cube></gesmes:Envelope>'
    ).encode()


def day(when: str, values: str = '<Cube currency="USD" rate="1.1614"/>') -> str:
    return f'<Cube time="{when}">{values}</Cube>'


def parse(payload: bytes):
    return ecb_fx.parse_xml(payload, fetched_at=FETCHED, source_url=ecb_fx.SOURCE_URLS["history"])


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "ecb.sqlite")
    monkeypatch.setattr(ecb_fx, "utcnow_iso", lambda: FETCHED)
    monkeypatch.setattr(ecb_fx, "CURRENCIES", ("USD",))
    return tmp_path


def test_parse_preserves_eur_base_and_separate_currency_histories():
    payload = xml(
        day("2026-09-08", '<Cube currency="USD" rate="1.1614"/><Cube currency="CNY" rate="7.7936"/>')
        + day("2026-09-07", '<Cube currency="USD" rate="1.16"/>')
    )
    result = parse(payload)
    usd, cny = result["ECBFX_USD"], result["ECBFX_CNY"]
    assert usd.remote_id == "EXR/D.USD.EUR.SP00.A"
    assert usd.source == "ecb_fx"
    assert usd.unit == "USD/EUR"
    assert usd.points.tolist() == [1.16, 1.1614]
    assert usd.fetched_at == FETCHED
    assert cny.unit == "CNY/EUR"
    assert cny.points.tolist() == [7.7936]
    assert cny.asof == "2026-09-08"


@pytest.mark.parametrize("payload", [
    xml(day("2026-09-08") + day("2026-09-08")),
    xml(day("2026-09-08", '<Cube currency="USD" rate="1"/><Cube currency="USD" rate="2"/>')),
    xml(day("2026-09-10")),
    xml(day("2026-02-30")),
    xml(day("2026-9-8")),
    xml(day("1998-12-31")),
    xml(day("2026-09-08", '<Cube currency="EUR" rate="1"/>')),
    xml(day("2026-09-08", '<Cube currency="usd" rate="1"/>')),
    xml(day("2026-09-08", '<Cube currency="USD" rate="NaN"/>')),
    xml(day("2026-09-08", '<Cube currency="USD" rate="Infinity"/>')),
    xml(day("2026-09-08", '<Cube currency="USD" rate="0"/>')),
    xml(day("2026-09-08", '<Cube currency="USD" rate="-1"/>')),
    xml(day("2026-09-08", '<Cube currency="USD" rate="1e500"/>')),
    xml(day("2026-09-08", '<Cube currency="USD" rate="1" unexpected="x"/>')),
    xml(day("2026-09-08", "")),
    xml(""),
    b"<html>provider error</html>",
    xml(day("2026-09-08")).replace(b"European Central Bank", b"Another provider"),
    xml(day("2026-09-08")).replace(b"</Cube></gesmes:Envelope>", b"<Other/></Cube></gesmes:Envelope>"),
])
def test_rejects_invalid_complete_document(payload):
    with pytest.raises(ValueError):
        parse(payload)


@pytest.mark.parametrize("payload", [
    b'<!DOCTYPE root [<!ENTITY boom "boom">]>' + xml(day("2026-09-08")),
    b'<!ENTITY external SYSTEM "file:///etc/passwd">' + xml(day("2026-09-08")),
    xml(day("2026-09-08")).decode().encode("utf-16"),
    xml(day("2026-09-08")).replace(b"UTF-8", b"UTF-16"),
])
def test_rejects_entities_and_alternative_encodings(payload):
    with pytest.raises((ValueError, UnicodeError)):
        parse(payload)


@pytest.mark.asyncio
async def test_bootstrap_then_rolling_refresh_merges_history_and_retains_raw(isolated_store, monkeypatch):
    older = xml(day("1999-01-04", '<Cube currency="USD" rate="1.1789"/>') + day("2026-09-07"))
    newer = xml(day("2026-09-08", '<Cube currency="USD" rate="1.17"/>'))
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=older if len(requests) == 1 else newer)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await ecb_fx.fetch(client, force=True)
        original_manifest = store.load_blob(ecb_fx.LATEST_CAPTURE_KEY)
        monkeypatch.setattr(ecb_fx, "utcnow_iso", lambda: "2026-09-09T00:01:00+00:00")
        second = await ecb_fx.fetch(client, force=True)

    assert requests == [ecb_fx.SOURCE_URLS["history"], ecb_fx.SOURCE_URLS["history90d"]]
    assert len(first["ECBFX_USD"].points) == 2
    assert second["ECBFX_USD"].points.tolist() == [1.1789, 1.1614, 1.17]
    assert second["ECBFX_USD"].asof == "2026-09-08"
    assert original_manifest["evidence_sha256"] == hashlib.sha256(older).hexdigest()
    assert Path(original_manifest["raw_path"]).read_bytes() == older
    assert store.load_blob(ecb_fx.FULL_CAPTURE_KEY) == original_manifest
    latest = store.load_blob(ecb_fx.LATEST_CAPTURE_KEY)
    assert latest["source_url"] == ecb_fx.SOURCE_URLS["history90d"]
    assert latest["source_publication_time"] is None
    assert latest["base_currency"] == "EUR"
    assert latest["observation_count"] == 1


@pytest.mark.asyncio
async def test_fresh_cache_avoids_network(isolated_store, monkeypatch):
    monkeypatch.setattr(store, "is_fresh", lambda *_: True)
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, content=xml(day("2026-09-08")))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await ecb_fx.fetch(client)
        result = await ecb_fx.fetch(client)
    assert count == 1
    assert result["ECBFX_USD"].asof == "2026-09-08"


@pytest.mark.asyncio
async def test_invalid_later_row_cannot_persist_earlier_valid_rows(isolated_store):
    payload = xml(day("2026-09-08") + day("2026-09-07", '<Cube currency="CNY" rate="NaN"/>'))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload))) as client:
        with pytest.raises(SourceFault):
            await ecb_fx.fetch(client, force=True)
    assert store.load_series("ECBFX_USD") is None
    assert store.load_blob(ecb_fx.LATEST_CAPTURE_KEY) is None
    assert not (isolated_store / "raw").exists()


@pytest.mark.asyncio
async def test_refresh_failure_keeps_original_capture_clock_and_reports_fault(isolated_store):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, content=xml(day("2026-09-08"))) if count == 1 else httpx.Response(503)

    faults = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await ecb_fx.fetch(client)
        manifest = store.load_blob(ecb_fx.LATEST_CAPTURE_KEY)
        result = await ecb_fx.fetch(client, faults, force=True)
        with pytest.raises(SourceFault):
            await ecb_fx.fetch(client, force=True)
    assert result["ECBFX_USD"].fetched_at == FETCHED
    assert store.load_blob(ecb_fx.LATEST_CAPTURE_KEY) == manifest
    assert len(faults) == 1
    assert faults[0]["source"] == "ecb_fx"


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{"content-length": "10000"}, {}])
async def test_bounded_fetch_rejects_declared_and_streamed_oversize(isolated_store, monkeypatch, headers):
    monkeypatch.setattr(ecb_fx, "MAX_BODY_BYTES", 64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 65, headers=headers))) as client:
        with pytest.raises(SourceFault, match="bounds|byte limit"):
            await ecb_fx.fetch(client, force=True)
    assert store.load_blob(ecb_fx.LATEST_CAPTURE_KEY) is None


@pytest.mark.asyncio
async def test_redirect_is_not_followed(isolated_store):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://example.invalid/data"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(SourceFault):
            await ecb_fx.fetch(client, force=True)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_raw_archive_corruption_is_not_overwritten(isolated_store):
    payload = xml(day("2026-09-08"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload))) as client:
        await ecb_fx.fetch(client)
        manifest = store.load_blob(ecb_fx.LATEST_CAPTURE_KEY)
        path = Path(manifest["raw_path"])
        path.write_bytes(b"tampered")
        with pytest.raises(SourceFault, match="hash collision"):
            await ecb_fx.fetch(client, force=True)
    assert path.read_bytes() == b"tampered"
    assert store.load_blob(ecb_fx.LATEST_CAPTURE_KEY) == manifest


@pytest.mark.asyncio
async def test_failed_second_currency_rolls_back_series_vintages_and_manifest(isolated_store, monkeypatch):
    monkeypatch.setattr(ecb_fx, "CURRENCIES", ("USD", "CNY"))
    old = xml(day("2026-09-08", '<Cube currency="USD" rate="1.16"/><Cube currency="CNY" rate="7.79"/>'))
    new = xml(day("2026-09-09", '<Cube currency="USD" rate="1.20"/><Cube currency="CNY" rate="7.80"/>'))
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=old if calls == 1 else new)

    def database_generation():
        with sqlite3.connect(store.DB_PATH) as connection:
            return {
                table: connection.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
                for table in ("observations", "observation_vintages", "fetches", "blobs")
            }

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await ecb_fx.fetch(client, force=True)
        before = database_generation()
        original = store._save_series_in_transaction
        writes = 0

        def fail_second(connection, item):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise RuntimeError("injected second currency failure")
            original(connection, item)

        monkeypatch.setattr(store, "_save_series_in_transaction", fail_second)
        with pytest.raises(SourceFault, match="second currency failure"):
            await ecb_fx.fetch(client, force=True)
    assert writes == 2
    assert database_generation() == before
    assert store.load_series("ECBFX_USD").points.tolist() == [1.16]
    assert store.load_series("ECBFX_CNY").points.tolist() == [7.79]
    assert len(list((isolated_store / "raw").rglob("*.xml"))) == 2


@pytest.mark.asyncio
async def test_retired_currency_stays_in_archive_but_not_current_result(isolated_store):
    payload = xml(day("2026-09-08") + day("2022-03-01", '<Cube currency="RUB" rate="117.201"/>'))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload))) as client:
        result = await ecb_fx.fetch(client, force=True)
    assert set(result) == {"ECBFX_USD"}
    assert store.load_series("ECBFX_RUB").asof == "2022-03-01"
    assert "ECBFX_RUB" in store.load_blob(ecb_fx.LATEST_CAPTURE_KEY)["series"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    {"source": "another_provider"},
    {"remote_id": "EXR/D.EUR.USD.SP00.A"},
    {"unit": "EUR/USD"},
    {"freq": "M"},
    {"fetched_at": "2026-09-08T23:59:00+00:00"},
    {"fetched_at": "2026-09-09T00:00:00"},
    {"fetched_at": "2026-09-10T00:00:00+00:00"},
])
async def test_cache_rejects_wrong_identity_units_or_generation(isolated_store, monkeypatch, mutation):
    payload = xml(day("2026-09-08"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload))) as client:
        await ecb_fx.fetch(client, force=True)
    original = store.load_series("ECBFX_USD")
    monkeypatch.setattr(store, "load_series", lambda _: replace(original, **mutation))
    assert ecb_fx._load_cache() == {}


@pytest.mark.asyncio
async def test_mixed_generation_cannot_skip_a_refresh_even_with_fresh_ttl(isolated_store, monkeypatch):
    monkeypatch.setattr(ecb_fx, "CURRENCIES", ("USD", "CNY"))
    monkeypatch.setattr(store, "is_fresh", lambda *_: True)
    payload = xml(day("2026-09-08", '<Cube currency="USD" rate="1.16"/><Cube currency="CNY" rate="7.79"/>'))
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await ecb_fx.fetch(client)
        cny = store.load_series("ECBFX_CNY")
        store.save_series(replace(cny, fetched_at="2026-09-08T23:59:00+00:00"))
        assert ecb_fx._load_cache() == {}
        result = await ecb_fx.fetch(client)
    assert len(requests) == 2
    assert all(series.fetched_at == FETCHED for series in result.values())


@pytest.mark.asyncio
async def test_incomplete_registered_current_coverage_is_not_published(isolated_store, monkeypatch):
    monkeypatch.setattr(ecb_fx, "CURRENCIES", ("USD", "CNY"))
    payload = xml(day("2026-09-08") + day("2026-09-07", '<Cube currency="CNY" rate="7.79"/>'))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload))) as client:
        with pytest.raises(SourceFault, match="current currency coverage"):
            await ecb_fx.fetch(client, force=True)
    assert store.load_series("ECBFX_USD") is None
    assert store.load_blob(ecb_fx.LATEST_CAPTURE_KEY) is None


@pytest.mark.asyncio
async def test_whole_download_deadline_stops_slow_stream(isolated_store, monkeypatch):
    monkeypatch.setattr(ecb_fx, "DOWNLOAD_DEADLINE_SECONDS", 0.01)
    closed = False

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"<"
            await asyncio.sleep(1)
            yield b"body>"

        async def aclose(self):
            nonlocal closed
            closed = True

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=SlowBody()))) as client:
        with pytest.raises(SourceFault, match="TimeoutError"):
            await ecb_fx.fetch(client, force=True)
    assert closed
    assert store.load_blob(ecb_fx.LATEST_CAPTURE_KEY) is None
    assert store.load_series("ECBFX_USD") is None
