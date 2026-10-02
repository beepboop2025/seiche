"""Daily AED references retain orientation, dated source evidence and vintages."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from seiche import store
from seiche.sources import cbuae_fx
from seiche.sources.base import SourceFault

FETCHED = "2026-10-02T19:00:00+00:00"
PUBLISHED = "Friday 02 October 2026 06:05:15 PM"
RATES = {"US Dollar": "3.6725", "Indian Rupee": "0.038126", "Euro": "4.140361",
         "GB Pound": "4.862757", "Japanese Yen": "0.023215", "Swiss Franc": "4.4",
         "Singapore Dollar": "2.8", "Iranian rial": "2E-06"}


def html(*, rates=None, updated=PUBLISHED):
    rows = "".join(f"<tr><td></td><td>{name}</td><td class='value'>{value}</td></tr>"
                   for name, value in (RATES if rates is None else rates).items())
    return (
        "<section><p>Exchange rates against UAE Dirham for VAT related obligations</p>"
        f"<p>Last updated:\n{updated}</p>"
        "<table><thead><tr><th></th><th>Currency</th><th>Rates</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></section>"
    ).encode()


def parse(payload):
    return cbuae_fx.parse_html(payload, fetched_at=FETCHED)


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "cbuae.sqlite")
    monkeypatch.setattr(cbuae_fx, "utcnow_iso", lambda: FETCHED)
    return tmp_path


def test_reference_orientation_and_observation_clock():
    result = parse(html())
    assert set(result) == {f"CBUAEFX_{currency}" for currency in cbuae_fx.CURRENCIES}
    inr = result["CBUAEFX_INR"]
    assert inr.unit == "AED/INR"
    assert inr.remote_id == "VAT/AED_PER_INR"
    assert inr.points.tolist() == [0.038126]
    assert 1 / inr.points.iloc[-1] == pytest.approx(26.22882022766616)
    assert result["CBUAEFX_USD"].points.iloc[-1] / inr.points.iloc[-1] == pytest.approx(96.325342285)
    assert inr.asof == "2026-10-02"
    assert inr.fetched_at == FETCHED
    result = cbuae_fx.parse_html(html(), fetched_at="2026-10-05T19:00:00+00:00")
    assert result["CBUAEFX_INR"].asof == "2026-10-02"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", "1e9999", "0x10", "1,200", "1%", ""])
def test_rejects_invalid_rate_in_unselected_currency(value):
    with pytest.raises(ValueError):
        parse(html(rates={**RATES, "Iranian rial": value}))


@pytest.mark.parametrize("updated", [
    "Monday 02 October 2026 06:05:15 PM", "Friday 31 February 2026 06:05:15 PM",
    "Saturday 03 October 2026 06:05:15 PM", "Friday 02 October 2026 11:59:59 PM",
    "02 October 2026", "Friday 02 October 2026", "", "unknown",
])
def test_rejects_missing_ambiguous_or_future_publisher_clock(updated):
    with pytest.raises(ValueError):
        parse(html(updated=updated))


@pytest.mark.parametrize("payload", [
    html().replace(b"<td>US Dollar</td>", b"<td>Indian Rupee</td>"),
    html().replace(b"</table>", b""),
    html().replace(b"<th>Rates</th>", b"<th>Other units</th>"),
    html().replace(b"VAT related obligations", b"executable market quotes"),
    html().replace(b"<td class='value'>3.6725</td>", b"<td>3.6725</td><td>extra</td>"),
    html(rates={"Euro": "4.14"}),
    b"<html>access denied</html>",
    b"\x00" + html(),
    html() + f"<p>Last updated: {PUBLISHED}</p>".encode(),
])
def test_rejects_wrong_or_incomplete_dataset(payload):
    with pytest.raises(ValueError):
        parse(payload)


def test_optional_currency_absence_does_not_manufacture_values():
    assert set(parse(html(rates={"US Dollar": "3.6725", "Indian Rupee": "0.038126"}))) == {
        "CBUAEFX_USD", "CBUAEFX_INR",
    }


def test_fetched_clock_must_be_zoned_and_url_must_be_exact():
    with pytest.raises(ValueError):
        cbuae_fx.parse_html(html(), fetched_at="2026-10-02T19:00:00")
    with pytest.raises(ValueError):
        cbuae_fx.parse_html(html(), fetched_at=FETCHED, source_url="https://example.org/clone")


@pytest.mark.asyncio
async def test_persisted_generation_retains_raw_hash_and_rights(isolated_store):
    payload = html()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload))) as client:
        result = await cbuae_fx.fetch(client, force=True)
    manifest = store.load_blob(cbuae_fx.LATEST_CAPTURE_KEY)
    assert manifest["source_publication_time"] is None
    assert manifest["publisher_updated_at"] == "2026-10-02T18:05:15+04:00"
    assert manifest["last_observation_date"] == "2026-10-02"
    assert manifest["evidence_sha256"] == hashlib.sha256(payload).hexdigest()
    assert Path(manifest["raw_path"]).read_bytes() == payload
    assert manifest["terms_url"] == cbuae_fx.TERMS_URL
    assert manifest["quote_currency"] == "AED"
    assert result["CBUAEFX_INR"].source == "cbuae_fx"


@pytest.mark.asyncio
async def test_cache_ttl_avoids_extra_network(isolated_store, monkeypatch):
    monkeypatch.setattr(store, "is_fresh", lambda *_: True)
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=html())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await cbuae_fx.fetch(client)
        cached = await cbuae_fx.fetch(client)
    assert calls == [cbuae_fx.SOURCE_URL]
    assert cached["CBUAEFX_USD"].asof == "2026-10-02"


@pytest.mark.asyncio
async def test_failed_or_regressed_refresh_preserves_original_clock(isolated_store, monkeypatch):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, content=html())
        if calls == 2:
            return httpx.Response(200, content=html(updated="Thursday 01 October 2026 06:05:15 PM"))
        return httpx.Response(503)

    faults = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await cbuae_fx.fetch(client, force=True)
        original = store.load_blob(cbuae_fx.LATEST_CAPTURE_KEY)
        monkeypatch.setattr(cbuae_fx, "utcnow_iso", lambda: "2026-10-03T19:00:00+00:00")
        retained = await cbuae_fx.fetch(client, faults, force=True)
        assert "regressed" in faults[0]["detail"]
        assert retained["CBUAEFX_USD"].fetched_at == FETCHED
        assert store.load_blob(cbuae_fx.LATEST_CAPTURE_KEY) == original
        with pytest.raises(SourceFault):
            await cbuae_fx.fetch(client, force=True)


@pytest.mark.asyncio
async def test_same_day_revision_is_retained_without_changing_observation_day(isolated_store, monkeypatch):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=html() if calls == 1 else html(
            rates={**RATES, "Indian Rupee": "0.038127"}, updated="Friday 02 October 2026 07:05:15 PM",
        ))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await cbuae_fx.fetch(client, force=True)
        monkeypatch.setattr(cbuae_fx, "utcnow_iso", lambda: "2026-10-02T20:00:00+00:00")
        revised = await cbuae_fx.fetch(client, force=True)
    assert revised["CBUAEFX_INR"].asof == "2026-10-02"
    assert revised["CBUAEFX_INR"].points.tolist() == [0.038127]
    assert len(list((isolated_store / "raw").rglob("*.*"))) >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [{"source": "wrong"}, {"unit": "INR/AED"},
    {"remote_id": "VAT/INR_PER_AED"}, {"fetched_at": "2026-10-01T19:00:00+00:00"}])
async def test_cache_validates_identity_orientation_and_generation(isolated_store, monkeypatch, mutation):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=html()))) as client:
        await cbuae_fx.fetch(client, force=True)
    original = store.load_series
    monkeypatch.setattr(store, "load_series", lambda name: replace(original(name), **mutation))
    assert cbuae_fx._load_cache() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [httpx.Response(302, headers={"location": "https://example.org"}),
    httpx.Response(206, content=html()), httpx.Response(200, content=b"x" * 65),
    httpx.Response(200, content=b"x", headers={"content-length": "10000"})])
async def test_incomplete_redirected_and_oversize_responses_do_not_persist(isolated_store, monkeypatch, response):
    monkeypatch.setattr(cbuae_fx, "MAX_BODY_BYTES", 64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response), follow_redirects=True) as client:
        with pytest.raises(SourceFault):
            await cbuae_fx.fetch(client, force=True)
    assert store.load_blob(cbuae_fx.LATEST_CAPTURE_KEY) is None


@pytest.mark.asyncio
async def test_transaction_failure_cannot_publish_partial_currency_generation(isolated_store, monkeypatch):
    save = store._save_series_in_transaction
    count = 0

    def fail_second(connection, item):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("injected write failure")
        save(connection, item)

    monkeypatch.setattr(store, "_save_series_in_transaction", fail_second)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=html()))) as client:
        with pytest.raises(SourceFault, match="injected write failure"):
            await cbuae_fx.fetch(client, force=True)
    assert store.load_blob(cbuae_fx.LATEST_CAPTURE_KEY) is None
    assert store.load_series("CBUAEFX_USD") is None


@pytest.mark.asyncio
async def test_slow_stream_has_whole_request_deadline(isolated_store, monkeypatch):
    monkeypatch.setattr(cbuae_fx, "DOWNLOAD_DEADLINE_SECONDS", 0.01)

    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"<"
            await asyncio.sleep(1)
            yield b"section>"

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Slow()))) as client:
        with pytest.raises(SourceFault, match="TimeoutError"):
            await cbuae_fx.fetch(client, force=True)
    assert store.load_blob(cbuae_fx.LATEST_CAPTURE_KEY) is None


@pytest.mark.asyncio
async def test_reader_preserves_provider_units_crosses_age_and_source_hash(isolated_store):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=html()))) as client:
        await cbuae_fx.fetch(client, force=True)
    result = cbuae_fx.read_references(now=datetime.fromisoformat(FETCHED))
    rows = {row["pair"]: row for row in result["rows"]}
    assert rows["USD/AED"]["value"] == 3.6725
    assert rows["USD/AED"]["evidence_status"] == "observed"
    assert rows["AED/INR"]["value"] == pytest.approx(1 / 0.038126)
    assert rows["USD/INR"]["value"] == pytest.approx(3.6725 / 0.038126)
    assert rows["USD/INR"]["unit"] == "INR per USD"
    assert rows["USD/INR"]["evidence_status"] == "derived"
    assert all(row["status"] == "fresh" and row["provider"] == "cbuae" for row in rows.values())
    assert "raw_path" not in result["capture"]
    assert len({leg["evidence_sha256"] for row in rows.values() for leg in row["sources"]}) == 1
    old = cbuae_fx.read_references(now=datetime.fromisoformat("2026-10-05T19:00:00+00:00"))
    assert all(row["status"] == "stale" and row["as_of"] == "2026-10-02" for row in old["rows"])


@pytest.mark.asyncio
async def test_reader_rejects_future_knowledge_and_mismatched_generation(isolated_store, monkeypatch):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=html()))) as client:
        await cbuae_fx.fetch(client, force=True)
    result = cbuae_fx.read_references(now=datetime.fromisoformat("2026-10-02T18:00:00+00:00"))
    assert result["capture"] is None
    assert all(row["value"] is None for row in result["rows"])
    load = store.load_series
    monkeypatch.setattr(store, "load_series", lambda name: replace(load(name), fetched_at="2026-10-02T18:00:00+00:00")
                        if name == "CBUAEFX_INR" else load(name))
    result = cbuae_fx.read_references(now=datetime.fromisoformat(FETCHED))
    assert result["capture"] is None
    assert all(row["status"] == "unavailable" for row in result["rows"])


def test_empty_store_reader_has_explicit_unavailable_rows(isolated_store):
    result = cbuae_fx.read_references(now=datetime.fromisoformat(FETCHED))
    assert result["capture"] is None
    assert len(result["rows"]) == 3
    assert all(row["value"] is None and row["reason"] for row in result["rows"])


@pytest.mark.asyncio
async def test_concurrent_older_capture_cannot_replace_newer_generation(isolated_store):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
            return httpx.Response(200, content=html(updated="Thursday 01 October 2026 06:05:15 PM"))
        return httpx.Response(200, content=html())

    faults = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        older = asyncio.create_task(cbuae_fx.fetch(client, faults, force=True))
        await asyncio.wait_for(started.wait(), timeout=5)
        newer = await cbuae_fx.fetch(client, force=True)
        manifest = store.load_blob(cbuae_fx.LATEST_CAPTURE_KEY)
        release.set()
        retained = await asyncio.wait_for(older, timeout=5)
    assert "SeriesBatchConflictError" in faults[0]["detail"]
    assert store.load_blob(cbuae_fx.LATEST_CAPTURE_KEY) == manifest
    assert retained["CBUAEFX_INR"].asof == newer["CBUAEFX_INR"].asof == "2026-10-02"


def test_reader_corrupt_database_fails_closed(isolated_store):
    store.DB_PATH.write_bytes(b"not a sqlite database")
    result = cbuae_fx.read_references(now=datetime.fromisoformat(FETCHED))
    assert result["capture"] is None
    assert all(row["status"] == "unavailable" for row in result["rows"])
