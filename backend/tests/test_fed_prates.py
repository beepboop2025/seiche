"""Direct Board IORB acquisition must not become a FRED or historic-PIT alias."""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from seiche import store
from seiche.domain.observation import RedistributionStatus
from seiche.markets.atlas import build_global_money_market_atlas
from seiche.markets.registry import default_registry
from seiche.repository import SQLiteMarketRepository
from seiche.sources import fed_prates
from seiche.sources.canonical import FetchedDocument, PublicationTimePolicy
from seiche.sources.official import build_official_adapters


def document(xml=None):
    if xml is None:
        xml = ('<Message xmlns="urn:frb"><Series SERIES_NAME="RESBM_N.D" UNIT="Percent" UNIT_MULT="1">'
               '<Obs TIME_PERIOD="2026-08-10" OBS_STATUS="A" OBS_VALUE="3.65"/>'
               '<Obs TIME_PERIOD="2026-08-11" OBS_STATUS="A" OBS_VALUE="3.65"/>'
               '<Obs TIME_PERIOD="2026-08-12" OBS_STATUS="A" OBS_VALUE="3.70"/>'
               '</Series></Message>')
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("PRATES_data.xml", xml)
    return FetchedDocument(fed_prates.SOURCE_URL, "application/zip", raw.getvalue(), "fed_prates")


def test_parser_retains_distinct_identity_dates_precision_and_unknown_publication():
    doc = document()
    points = fed_prates.parse_document(doc)
    assert len(points) == 3
    assert all(p.instrument_id == "US.FED.IORB_DIRECT" for p in points)
    assert points[0].raw_value == Decimal("3.65")
    assert points[0].event_time.isoformat() == "2026-08-10"
    assert all(p.publication_time_policy is PublicationTimePolicy.UNKNOWN for p in points)
    assert all(p.source_publication_time is None for p in points)
    evidence = json.loads(points[0].row_evidence)
    assert evidence["source_url"] == fed_prates.SOURCE_URL
    assert evidence["series"] == "RESBM_N.D" and evidence["field"] == "OBS_VALUE"
    assert points[0].revision_id.startswith("fed-prates:RESBM_N.D:2026-08-10:")
    changed = document('<Message><Series SERIES_NAME="RESBM_N.D" UNIT="Percent" UNIT_MULT="1">'
                       '<Obs TIME_PERIOD="2026-08-10" OBS_STATUS="A" OBS_VALUE="3.66"/>'
                       '</Series></Message>')
    assert fed_prates.parse_document(changed)[0].revision_id != points[0].revision_id


@pytest.mark.parametrize("mutation", [
    lambda s: s.replace('UNIT="Percent"', 'UNIT="Basis points"'),
    lambda s: s.replace('UNIT_MULT="1"', 'UNIT_MULT="100"'),
    lambda s: s.replace('SERIES_NAME="RESBM_N.D"', 'SERIES_NAME="OTHER"'),
    lambda s: s.replace('OBS_VALUE="3.65"', 'OBS_VALUE="NaN"'),
    lambda s: s.replace('2026-08-11', '2026-08-10'),
    lambda s: '<!DOCTYPE Message [<!ENTITY unsafe "value">]>' + s,
])
def test_changed_units_duplicate_dates_and_invalid_xml_fail_closed(mutation):
    with zipfile.ZipFile(io.BytesIO(document().payload)) as archive:
        xml = archive.read("PRATES_data.xml").decode()
    with pytest.raises(ValueError):
        fed_prates.parse_document(document(mutation(xml)))


@pytest.mark.asyncio
async def test_bounded_fetch_uses_only_original_endpoint_and_rejects_redirect():
    doc = document()
    requests = []
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield doc.payload
    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=Stream())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        found = await fed_prates.fetch_document(client)
    assert found == (doc,)
    assert str(requests[0].url) == fed_prates.SOURCE_URL
    assert requests[0].headers["accept-encoding"] == "identity"
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"location": "https://example.invalid/"}))) as client:
        with pytest.raises(ValueError, match="HTTP 302"):
            await fed_prates.fetch_document(client)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"small",
                                           headers={"content-length": str(fed_prates.MAX_ARCHIVE_BYTES+1)}))) as client:
        with pytest.raises(ValueError, match="bound"):
            await fed_prates.fetch_document(client)


@pytest.mark.asyncio
async def test_canonical_history_binds_actual_capture_without_inventing_past_availability(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "fed-prates.sqlite")
    captured = datetime(2026, 8, 11, 18, tzinfo=UTC)
    doc = document()
    adapter = next(a for a in build_official_adapters(
        repository=SQLiteMarketRepository(), clock=lambda: captured)
        if a.adapter_id == "fed_prates")
    async def fetcher(client):
        return (doc,)
    adapter.fetcher = fetcher
    batch = await adapter.collect()
    assert batch.raw_capture.source_uri == fed_prates.SOURCE_URL
    assert batch.raw_capture.payload == doc.payload
    assert len(batch.observations) == 2  # future effective date is not admitted
    assert all(row.knowledge_time == captured for row in batch.observations)
    assert all(row.source_publication_time is None for row in batch.observations)
    assert all(row.source == "fed_prates" for row in batch.observations)
    assert all(row.value == Decimal("365") for row in batch.observations)
    pack = default_registry().get("US-USD")
    assert pack.adapter_map["fred_daily"].redistribution_status is RedistributionStatus.METADATA_ONLY
    out = build_global_money_market_atlas([pack], {"US-USD": batch.observations}, as_of=captured)
    metric = next(x for x in out["markets"][0]["metrics"] if x["id"] == fed_prates.INSTRUMENT_ID)
    assert metric["history"] == [["2026-08-10", 3.65], ["2026-08-11", 3.65]]
    assert metric["source_url"] == fed_prates.SOURCE_URL
    assert metric["published_at"] is None
    assert metric["status"] == "UNKNOWN"
    assert metric["freshness_basis"] == "source publication time unknown"
    assert metric["knowledge_time"] == captured.isoformat()
    prior = build_global_money_market_atlas([pack], {"US-USD": batch.observations},
                                            as_of=captured.replace(hour=17))
    assert all(m["value"] is None for m in prior["markets"][0]["metrics"])
