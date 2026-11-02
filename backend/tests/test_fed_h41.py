"""Direct Board H.4.1 reserve balances: complete, tested, and off behind the rights gate."""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal

import httpx
import pytest

from seiche import store
from seiche.domain.observation import (
    CanonicalUnit,
    ConnectorClassification,
    RedistributionStatus,
    SemanticRole,
)
from seiche.markets.atlas import build_global_money_market_atlas
from seiche.markets.base import (
    InstrumentSpec,
    PublicationClock,
    PublicationClockPrecision,
    SourceAdapterSpec,
)
from seiche.markets.registry import MarketRegistry, default_registry
from seiche.markets.us_usd.pack import PACK, US_SETTLEMENT_CALENDAR
from seiche.repository import SQLiteMarketRepository
from seiche.sources import fed_h41
from seiche.sources.canonical import FetchedDocument, PublicationTimePolicy
from seiche.sources.official import build_official_adapters

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


def opened_pack(status: RedistributionStatus = RedistributionStatus.ALLOWED):
    """A test-only pack variant: what the owner's rights record would add."""
    clock = PublicationClock("America/New_York", time(16, 30), 1,
                             PublicationClockPrecision.SCHEDULED, US_SETTLEMENT_CALENDAR.calendar_id)
    adapter = SourceAdapterSpec(fed_h41.ADAPTER_ID, ConnectorClassification.OFFICIAL_OPEN, "P1W", clock, status)
    instrument = InstrumentSpec(fed_h41.INSTRUMENT_ID, "RESERVE_BALANCES_DIRECT", SemanticRole.RESERVE_BALANCES,
                                fed_h41.ADAPTER_ID, "USD millions", CanonicalUnit.LOCAL_CURRENCY_MILLIONS)
    return replace(PACK, source_adapters=(*PACK.source_adapters, adapter),
                   instruments=(*PACK.instruments, instrument))


def test_gate_is_closed_for_the_registered_pack_and_no_request_is_built():
    pack = default_registry().get("US-USD")
    assert fed_h41.ADAPTER_ID not in pack.adapter_map
    assert fed_h41.INSTRUMENT_ID not in pack.instrument_map
    assert fed_h41.rights_gate_open(pack) is False
    adapters = build_official_adapters(repository=SQLiteMarketRepository())
    assert fed_h41.ADAPTER_ID not in {adapter.adapter_id for adapter in adapters}
    # The FRED copy keeps its own restriction; nothing here widens it.
    assert pack.adapter_map["fred_weekly"].redistribution_status is RedistributionStatus.METADATA_ONLY


@pytest.mark.parametrize("status", [RedistributionStatus.METADATA_ONLY, RedistributionStatus.DERIVED_ONLY,
                                    RedistributionStatus.PROHIBITED])
def test_only_an_allowed_rights_record_opens_the_gate(status):
    assert fed_h41.rights_gate_open(opened_pack(status)) is False
    assert fed_h41.rights_gate_open(opened_pack()) is True


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


@pytest.mark.asyncio
async def test_once_a_rights_record_exists_rows_keep_the_board_clock_and_rights(tmp_path, monkeypatch):
    """What activation would do, proved on a test-only pack; production stays off."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "fed-h41.sqlite")
    pack = opened_pack()
    registry = MarketRegistry(tuple(pack if item.market_id == "US-USD" else item
                                    for item in default_registry().list()))
    # Friday 2026-10-30: Thursday's 16:30 release for the week ending 10-28 is out.
    captured = datetime(2026, 10, 30, 12, tzinfo=UTC)
    adapter = next(a for a in build_official_adapters(
        registry=registry, repository=SQLiteMarketRepository(), clock=lambda: captured)
        if a.adapter_id == fed_h41.ADAPTER_ID)

    async def fetcher(client):
        return (document(),)

    adapter.fetcher = fetcher
    batch = await adapter.collect()
    assert [(row.event_time.date().isoformat(), row.value) for row in batch.observations] == [
        ("2026-10-07", Decimal("3029659")), ("2026-10-14", Decimal("3022140")),
        ("2026-10-28", Decimal("2893770")),
    ]
    assert all(row.redistribution_status is RedistributionStatus.ALLOWED for row in batch.observations)
    assert all(row.knowledge_time == captured for row in batch.observations)
    # Thursday 2026-10-29 16:30 New York, the declared release time.
    assert batch.observations[-1].source_publication_time == datetime(2026, 10, 29, 20, 30, tzinfo=UTC)
    atlas = build_global_money_market_atlas([pack], {"US-USD": batch.observations}, as_of=captured)
    metric = next(m for m in atlas["markets"][0]["metrics"] if m["id"] == fed_h41.INSTRUMENT_ID)
    assert metric["availability"] == "AVAILABLE"
    assert metric["value"] == 2893770.0 and metric["asof"] == "2026-10-28"
    assert metric["status"] == "FRESH"
