"""Direct Board H.4.1 reserve balances: enabled by the declared rights record, cited everywhere."""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from fastapi import Request, Response

from seiche import api, store
from seiche.domain.observation import (
    CanonicalUnit,
    ConnectorClassification,
    Observation,
    QualityState,
    RedistributionStatus,
    SemanticRole,
    StalenessState,
    evidence_sha256,
)
from seiche.markets.atlas import build_global_money_market_atlas, market_source_reference
from seiche.markets.base import PublicationClockPrecision
from seiche.markets.registry import MarketRegistry, default_registry
from seiche.markets.us_usd.pack import PACK
from seiche.repository import SQLiteMarketRepository
from seiche.sources import fed_h41
from seiche.sources.canonical import FetchedDocument, PublicationTimePolicy
from seiche.sources.official import PRODUCTION_ADAPTER_KEYS, build_official_adapters

# Mirrors the Board package layout of 2026-11-02 (namespaces, series attributes,
# observation rows). The four values are the published week averages for the
# weeks ending 2026-10-07 through 2026-10-28, in millions of US dollars.
HEADER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
    '<message:MessageGroup xmlns:message="http://www.SDMX.org/resources/SDMXML/schemas/v1_0/message" '
    'xmlns:common="http://www.SDMX.org/resources/SDMXML/schemas/v1_0/common" '
    'xmlns:frb="http://www.federalreserve.gov/structure/compact/common">'
    '<frb:DataSet id="H41" xmlns:kf="http://www.federalreserve.gov/structure/compact/H41_H41">'
)
FOOTER = "</frb:DataSet></message:MessageGroup>"
TARGET = (
    '<kf:Series SERIES_NAME="RESH4R_XAW_N.WW" FREQ="19" CATEGORY="LIABCAP" SUBCATEGORY="OFDRB" '
    'COMPONENT="RBFRB" DISTRIBUTION="TOT" SERIESTYPE="A" UNIT="Currency" UNIT_MULT="1000000" CURRENCY="USD">'
    '<frb:Annotations><common:Annotation><common:AnnotationType>Short Description</common:AnnotationType>'
    '<common:AnnotationText>Liabilities and Capital: Other Factors Draining Reserve Balances: Reserve '
    'balances with Federal Reserve Banks: week average</common:AnnotationText></common:Annotation></frb:Annotations>'
    '<frb:Obs OBS_STATUS="A" OBS_VALUE="3029659" TIME_PERIOD="2026-10-07" />'
    '<frb:Obs OBS_STATUS="A" OBS_VALUE="3022140" TIME_PERIOD="2026-10-14" />'
    '<frb:Obs OBS_STATUS="NA" OBS_VALUE="" TIME_PERIOD="2026-10-21" />'
    '<frb:Obs OBS_STATUS="A" OBS_VALUE="2893770" TIME_PERIOD="2026-10-28" />'
    "</kf:Series>"
)
# A neighbouring series (Wednesday level) must never be read as the week average.
DECOY = (
    '<kf:Series SERIES_NAME="RESH4R_N.WW" FREQ="19" UNIT="Currency" UNIT_MULT="1000000" CURRENCY="USD">'
    '<frb:Obs OBS_STATUS="A" OBS_VALUE="2895236" TIME_PERIOD="2026-10-28" /></kf:Series>'
)


def document(xml: str | None = None, *, members: dict[str, str] | None = None) -> FetchedDocument:
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in (members or {fed_h41.MEMBER: xml if xml is not None else HEADER + DECOY + TARGET + FOOTER}).items():
            archive.writestr(name, body)
    return FetchedDocument(fed_h41.SOURCE_URL, "application/zip", raw.getvalue(), fed_h41.ADAPTER_ID)


def pack_with(status: RedistributionStatus | None):
    """The registered US pack with the fed_h41 record changed, or removed (None)."""
    if status is None:
        return replace(
            PACK,
            source_adapters=tuple(item for item in PACK.source_adapters
                                  if item.adapter_id != fed_h41.ADAPTER_ID),
            instruments=tuple(item for item in PACK.instruments
                              if item.source_adapter_id != fed_h41.ADAPTER_ID),
        )
    return replace(PACK, source_adapters=tuple(
        replace(item, redistribution_status=status) if item.adapter_id == fed_h41.ADAPTER_ID else item
        for item in PACK.source_adapters))


def registry_with(pack) -> MarketRegistry:
    return MarketRegistry(tuple(pack if item.market_id == "US-USD" else item
                                for item in default_registry().list()))


def test_the_us_pack_declares_the_allowed_board_record_and_registers_the_adapter():
    pack = default_registry().get("US-USD")
    adapter = pack.adapter_map[fed_h41.ADAPTER_ID]
    assert adapter.classification is ConnectorClassification.OFFICIAL_OPEN
    assert adapter.redistribution_status is RedistributionStatus.ALLOWED
    assert (adapter.expected_cadence, adapter.collection_cadence) == ("P1W", "P1D")
    clock = adapter.publication_clock
    assert (clock.timezone_name, clock.local_time.isoformat(), clock.business_day_lag) == (
        "America/New_York", "16:30:00", 1)
    assert clock.precision is PublicationClockPrecision.SCHEDULED
    assert clock.calendar_id == pack.settlement_calendar.calendar_id
    spec = pack.instrument_map[fed_h41.INSTRUMENT_ID]
    assert (spec.source_adapter_id, spec.semantic_role, spec.canonical_unit) == (
        fed_h41.ADAPTER_ID, SemanticRole.RESERVE_BALANCES, CanonicalUnit.LOCAL_CURRENCY_MILLIONS)
    assert spec.normalize(Decimal("2893770")) == Decimal("2893770")
    order = [item.instrument_id for item in pack.instruments]
    assert order.index(fed_h41.INSTRUMENT_ID) < order.index("US.FED.RESERVE_BALANCES")
    assert fed_h41.rights_gate_open(pack) is True
    adapters = build_official_adapters(repository=SQLiteMarketRepository())
    assert fed_h41.ADAPTER_ID in {item.adapter_id for item in adapters}
    assert ("US-USD", fed_h41.ADAPTER_ID) in PRODUCTION_ADAPTER_KEYS
    # The FRED copy keeps its own restriction; nothing here widens it.
    assert pack.adapter_map["fred_weekly"].redistribution_status is RedistributionStatus.METADATA_ONLY
    assert pack.instrument_map["US.FED.RESERVE_BALANCES"].source_adapter_id == "fred_weekly"


@pytest.mark.parametrize("status", [RedistributionStatus.METADATA_ONLY, RedistributionStatus.DERIVED_ONLY,
                                    RedistributionStatus.PROHIBITED, None])
def test_the_adapter_registers_only_with_the_declared_allowed_record(status):
    pack = pack_with(status)
    assert fed_h41.rights_gate_open(pack) is False
    adapters = build_official_adapters(registry=registry_with(pack), repository=SQLiteMarketRepository())
    assert fed_h41.ADAPTER_ID not in {item.adapter_id for item in adapters}
    # A record that is not an official open source never opens the gate either.
    tenant = replace(PACK, source_adapters=tuple(
        replace(item, classification=ConnectorClassification.TENANT_PROVIDED)
        if item.adapter_id == fed_h41.ADAPTER_ID else item for item in PACK.source_adapters))
    assert fed_h41.rights_gate_open(tenant) is False


def test_the_attribution_matches_the_registry_and_the_serving_maps():
    reference = market_source_reference(fed_h41.ADAPTER_ID)
    assert reference == {
        "publisher": "Board of Governors of the Federal Reserve System",
        "source_url": fed_h41.SOURCE_URL,
        "attribution": fed_h41.ATTRIBUTION,
        "terms_url": fed_h41.TERMS_URL,
    }
    assert fed_h41.ATTRIBUTION == "Source: Board of Governors of the Federal Reserve System, H.4.1"
    ledger = json.loads((Path(__file__).resolve().parents[2] / "docs" / "data-source-expansion.json").read_text())
    record = next(item for item in ledger["candidates"] if item["id"] == "frb_h41_release")
    assert record["access_url"] == fed_h41.SOURCE_URL
    assert record["rights_evidence_url"] == fed_h41.TERMS_URL
    assert record["rights_status"] == "explicit_public_domain_with_exclusions"
    assert record["implementation_status"] == "implemented"
    assert record["semantic_roles"] == ["RESERVE_BALANCES"]
    assert ("Unless otherwise indicated, information on Board's website is in the public domain and may be "
            "copied and distributed without permission. Please cite to the Board as the source of the "
            "information.") in record["blocker"]
    assert fed_h41.ATTRIBUTION in record["blocker"]


def test_parser_reads_only_the_week_average_series_in_millions():
    points = fed_h41.parse_document(document())
    assert [(p.instrument_id, p.event_time, p.raw_value) for p in points] == [
        (fed_h41.INSTRUMENT_ID, date(2026, 10, 7), Decimal("3029659")),
        (fed_h41.INSTRUMENT_ID, date(2026, 10, 14), Decimal("3022140")),
        # 2026-10-21 is NA in this fixture: withheld, never zero.
        (fed_h41.INSTRUMENT_ID, date(2026, 10, 28), Decimal("2893770")),
    ]
    assert all(p.source_publication_time is None for p in points)
    # The pack's declared schedule supplies the inferred publication time.
    assert all(p.publication_time_policy is PublicationTimePolicy.INFER for p in points)
    evidence = json.loads(points[-1].row_evidence)
    assert evidence["source_url"] == fed_h41.SOURCE_URL
    assert evidence["series"] == "RESH4R_XAW_N.WW" and evidence["field"] == "OBS_VALUE"
    assert evidence["declared"] == fed_h41.SERIES_ATTRIBUTES
    assert points[-1].revision_id.startswith("fed-h41:RESH4R_XAW_N.WW:2026-10-28:")


@pytest.mark.parametrize("mutation", [
    lambda s: s.replace('UNIT="Currency" UNIT_MULT="1000000" CURRENCY="USD">' "<frb:Annotations>",
                        'UNIT="Currency" UNIT_MULT="1000" CURRENCY="USD"><frb:Annotations>'),
    lambda s: s.replace('CURRENCY="USD"><frb:Annotations>', 'CURRENCY="EUR"><frb:Annotations>'),
    lambda s: s.replace('SERIES_NAME="RESH4R_XAW_N.WW" FREQ="19"', 'SERIES_NAME="RESH4R_XAW_N.WW" FREQ="9"'),
    lambda s: s.replace('SERIES_NAME="RESH4R_XAW_N.WW"', 'SERIES_NAME="OTHER"'),
    lambda s: s.replace(TARGET, TARGET + TARGET),
    lambda s: s.replace("2026-10-14", "2026-10-07"),
    lambda s: s.replace("2026-10-14", "2026-10-15"),
    lambda s: s.replace('OBS_VALUE="3022140"', 'OBS_VALUE="NaN"'),
    lambda s: s.replace('OBS_VALUE="3022140"', 'OBS_VALUE="-1"'),
    lambda s: s.replace('OBS_VALUE="3022140"', 'OBS_VALUE="3,022,140"'),
    lambda s: s.replace(HEADER, HEADER.replace("?>\n", '?>\n<!DOCTYPE m [<!ENTITY x "y">]>')),
    lambda s: s[:-40],
])
def test_changed_units_identity_dates_values_or_xml_fail_closed(mutation):
    xml = HEADER + DECOY + TARGET + FOOTER
    with pytest.raises(ValueError):
        fed_h41.parse_document(document(mutation(xml)))


def test_package_identity_member_and_size_bounds_fail_closed():
    good = document()
    with pytest.raises(ValueError):
        fed_h41.parse_document(FetchedDocument("https://example.invalid/FRB_H41_xml.zip",
                                               "application/zip", good.payload, fed_h41.ADAPTER_ID))
    with pytest.raises(ValueError):
        fed_h41.parse_document(document(members={"OTHER.xml": HEADER + TARGET + FOOTER}))
    with pytest.raises(ValueError):
        fed_h41.parse_document(document(members={f"extra{i}.xml": "x" for i in range(fed_h41.MAX_MEMBERS)}
                                        | {fed_h41.MEMBER: HEADER + TARGET + FOOTER}))


@pytest.mark.asyncio
async def test_bounded_fetch_uses_only_the_publisher_package_and_rejects_redirects():
    doc = document()
    requests = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield doc.payload

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=Stream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fed_h41.fetch_document(client) == (doc,)
    assert str(requests[0].url) == fed_h41.SOURCE_URL
    assert requests[0].headers["accept-encoding"] == "identity"
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"location": "https://example.invalid/"}))) as client:
        with pytest.raises(ValueError, match="HTTP 302"):
            await fed_h41.fetch_document(client)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"small", headers={
                "content-length": str(fed_h41.MAX_ARCHIVE_BYTES + 1)}))) as client:
        with pytest.raises(ValueError, match="bound"):
            await fed_h41.fetch_document(client)


def fred_reserves(day: str, value: str) -> Observation:
    spec = PACK.instrument_map["US.FED.RESERVE_BALANCES"]
    event = datetime.fromisoformat(day + "T00:00:00+00:00")
    published = event + timedelta(days=1, hours=21)
    return Observation(
        market_id="US-USD", monetary_area_id="US", jurisdiction_codes=("US",), currency="USD",
        instrument_id=spec.instrument_id, semantic_role=spec.semantic_role, value=Decimal(value),
        canonical_unit=spec.canonical_unit, rate_compounding=None, day_count=None,
        event_time=event, source_publication_time=published, knowledge_time=published,
        revision_id=f"fred-{day}", source="fred", evidence_hash=evidence_sha256(f"fred-{day}-{value}"),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.METADATA_ONLY,
        quality=QualityState.VERIFIED, staleness=StalenessState.FRESH,
    )


async def collected(captured: datetime):
    adapter = next(a for a in build_official_adapters(
        repository=SQLiteMarketRepository(), clock=lambda: captured)
        if a.adapter_id == fed_h41.ADAPTER_ID)

    async def fetcher(client):
        return (document(),)

    adapter.fetcher = fetcher
    return await adapter.collect()


@pytest.mark.asyncio
async def test_board_rows_serve_allowed_with_the_citation_and_fred_stays_withheld(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "fed-h41.sqlite")
    # Friday 2026-10-30: Thursday's 16:30 release for the week ending 10-28 is out.
    captured = datetime(2026, 10, 30, 12, tzinfo=UTC)
    batch = await collected(captured)
    assert [(row.event_time.date().isoformat(), row.value) for row in batch.observations] == [
        ("2026-10-07", Decimal("3029659")), ("2026-10-14", Decimal("3022140")),
        ("2026-10-28", Decimal("2893770")),
    ]
    assert all(row.source == fed_h41.ADAPTER_ID for row in batch.observations)
    assert all(row.redistribution_status is RedistributionStatus.ALLOWED for row in batch.observations)
    assert all(row.knowledge_time == captured for row in batch.observations)
    assert all(row.quality is QualityState.VERIFIED for row in batch.observations)
    # Thursday 2026-10-29 16:30 New York, the declared release time.
    assert batch.observations[-1].source_publication_time == datetime(2026, 10, 29, 20, 30, tzinfo=UTC)

    pack = default_registry().get("US-USD")
    rows = [*batch.observations, fred_reserves("2026-10-28", "2893770")]
    atlas = build_global_money_market_atlas([pack], {"US-USD": rows}, as_of=captured)
    metrics = {m["id"]: m for m in atlas["markets"][0]["metrics"]}
    direct = metrics[fed_h41.INSTRUMENT_ID]
    assert direct["availability"] == "AVAILABLE" and direct["redistribution_status"] == "allowed"
    assert direct["value"] == 2893770.0 and direct["unit"] == "local_currency_millions"
    assert direct["asof"] == "2026-10-28" and direct["status"] == "FRESH"
    assert direct["attribution"] == fed_h41.ATTRIBUTION
    assert direct["terms_url"] == fed_h41.TERMS_URL
    assert direct["source_url"] == fed_h41.SOURCE_URL
    # The FRED copy of the same number stays withheld, with no value or history.
    fred = metrics["US.FED.RESERVE_BALANCES"]
    assert fred["availability"] == "RESTRICTED" and fred["value"] is None and fred["history"] == []
    assert fred["redistribution_status"] == "metadata_only"
    assert "attribution" not in fred
    notice = next(item for item in atlas["legal_notices"]
                  if item.get("source", "").startswith("Board of Governors of the Federal Reserve System, H.4.1"))
    assert notice["terms_url"] == fed_h41.TERMS_URL
    assert notice["notice"].startswith(fed_h41.ATTRIBUTION + ".")


@pytest.mark.asyncio
async def test_series_page_cites_the_board_and_redacts_the_fred_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "fed-h41-series.sqlite")
    captured = datetime(2026, 10, 30, 12, tzinfo=UTC)
    batch = await collected(captured)
    store.save_observations([*batch.observations, fred_reserves("2026-10-28", "2893770")])

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return captured if tz is None else captured.astimezone(tz)

    monkeypatch.setattr(api, "datetime", Frozen)
    request = Request({"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
                       "path": "/api/v2/markets/US-USD/series", "raw_path": b"/api/v2/markets/US-USD/series",
                       "query_string": b"", "headers": [], "client": ("127.0.0.1", 1), "server": ("t", 80)})
    payload = api.market_series_v2("US-USD", request, Response(), n=50)
    instruments = {item["instrument_id"]: item for item in payload["instruments"]}
    direct = instruments[fed_h41.INSTRUMENT_ID]
    assert direct["availability"] == "READY"
    assert direct["redistribution_status"] == "allowed"
    assert direct["attribution"] == fed_h41.ATTRIBUTION and direct["terms_url"] == fed_h41.TERMS_URL
    assert direct["publisher"] == "Board of Governors of the Federal Reserve System"
    assert instruments["US.FED.RESERVE_BALANCES"]["availability"] == "RESTRICTED"
    assert "attribution" not in instruments["US.FED.RESERVE_BALANCES"]
    records = {(item["instrument_id"], item["event_time"][:10]): item for item in payload["observations"]}
    assert records[(fed_h41.INSTRUMENT_ID, "2026-10-28")]["value"] == "2893770"
    fred_record = records[("US.FED.RESERVE_BALANCES", "2026-10-28")]
    assert fred_record["value"] is None and fred_record["value_status"] == "REDACTED_BY_LICENCE"
