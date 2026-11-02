"""BEA nominal GDP from BEA's own NIPA flat file: register pinned, bounded, cited.

Fixtures are trimmed copies of the files BEA posted on 2026-11-02 (see README).
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from seiche import store
from seiche.config import ALL_SERIES
from seiche.domain.observation import CanonicalUnit, ConnectorClassification, RedistributionStatus, SemanticRole
from seiche.markets.atlas import build_global_money_market_atlas, market_source_reference
from seiche.markets.base import PublicationClockPrecision
from seiche.markets.reading_rights import reading_restricted
from seiche.markets.registry import default_registry
from seiche.repository import SQLiteMarketRepository
from seiche.sources import bea_nipa
from seiche.sources.canonical import FetchedDocument, PublicationTimePolicy
from seiche.sources.official import PRODUCTION_ADAPTER_KEYS, build_official_adapters

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "bea"
DATA = (FIXTURES / "NipaDataQ.txt").read_bytes().decode()
REGISTER = (FIXTURES / "SeriesRegister.txt").read_bytes().decode()


def data(text: str = DATA, *, uri: str = bea_nipa.DATA_URL) -> FetchedDocument:
    return FetchedDocument(uri, "text/plain", text.encode(), bea_nipa.ADAPTER_ID)


def register(text: str = REGISTER, *, uri: str = bea_nipa.REGISTER_URL) -> FetchedDocument:
    return FetchedDocument(uri, "text/plain", text.encode(), bea_nipa.ADAPTER_ID)


def test_parser_reads_only_a191rc_quarters_in_usd_millions():
    points = bea_nipa.parse_document(data())
    assert [(p.event_time.isoformat(), p.raw_value) for p in points][:2] == [
        ("1947-03-31", Decimal("243164")), ("2024-12-31", Decimal("29929552"))]
    assert (points[-1].event_time, points[-1].raw_value) == (date(2026, 9, 30), Decimal("33010036"))
    assert len(points) == 9 and {p.instrument_id for p in points} == {bea_nipa.INSTRUMENT_ID}
    # Real GDP (A191RX) and its growth rate (A191RL) are never read as GDP.
    assert Decimal("24611077") not in {p.raw_value for p in points}
    assert all(p.publication_time_policy is PublicationTimePolicy.UNKNOWN for p in points)
    evidence = json.loads(points[-1].row_evidence)
    assert evidence == {"data_url": bea_nipa.DATA_URL, "series": "A191RC", "period": "2026Q3",
                        "value": "33,010,036", "unit": "USD millions, seasonally adjusted annual rate"}
    assert points[-1].revision_id.startswith("bea:A191RC:2026Q3:")
    # The register document checks the declaration and returns no points.
    assert bea_nipa.parse_document(register()) == ()


@pytest.mark.parametrize("old, new", [
    ('"Gross domestic product","Current Dollars"', '"Gross domestic income","Current Dollars"'),
    ('"Current Dollars","Level",-6,OECD', '"Chained Dollars","Level",-6,OECD'),
    ('"Current Dollars","Level",-6,OECD', '"Current Dollars","Percent change",-6,OECD'),
    ('"Current Dollars","Level",-6,OECD', '"Current Dollars","Level",-9,OECD'),
    ("|T10105:1|", "|T10105:2|"),
    ("%SeriesCode,SeriesLabel", "SeriesCode,SeriesLabel"),
])
def test_a_changed_register_declaration_fails_closed(old, new):
    assert REGISTER.count(old) == 1
    with pytest.raises(ValueError):
        bea_nipa.parse_document(register(REGISTER.replace(old, new)))


def test_a_missing_or_repeated_register_entry_fails_closed():
    line = next(item for item in REGISTER.splitlines(keepends=True) if item.startswith("A191RC,"))
    with pytest.raises(ValueError, match="missing, duplicated"):
        bea_nipa.check_register(register(REGISTER.replace(line, "")))
    with pytest.raises(ValueError, match="missing, duplicated"):
        bea_nipa.check_register(register(REGISTER + line))


@pytest.mark.parametrize("old, new", [
    ("%SeriesCode,Period,Value", "SeriesCode,Period,Value"),
    ('A191RC,2026Q3,"33,010,036"', 'A191RC,2026Q5,"33,010,036"'),
    ('A191RC,2026Q3,"33,010,036"', 'A191RC,2026M09,"33,010,036"'),
    ('A191RC,2026Q3,"33,010,036"', 'A191RC,2026Q2,"33,010,036"'),
    ('A191RC,2026Q3,"33,010,036"', 'A191RC,2026Q3,"33010036"'),
    ('A191RC,2026Q3,"33,010,036"', 'A191RC,2026Q3,"33,010.036"'),
    ('A191RC,2026Q3,"33,010,036"', 'A191RC,2026Q3,"---"'),
    ('A191RC,2026Q3,"33,010,036"', 'A191RC,2026Q3,"33,010"'),
    ('A191RC,2026Q3,"33,010,036"', 'A191RC,2026Q3,"33,010,036",x'),
], ids=["header", "quarter five", "a month", "repeated quarter", "no grouping", "decimal point",
        "unavailable marker", "rescaled to billions", "reshaped row"])
def test_a_changed_data_row_fails_closed(old, new):
    assert DATA.count(old) == 1
    with pytest.raises(ValueError):
        bea_nipa.parse_document(data(DATA.replace(old, new)))


def test_source_identity_size_and_encoding_bounds():
    with pytest.raises(ValueError, match="unrecognized or oversized"):
        bea_nipa.parse_data(data(uri="https://example.invalid/NipaDataQ.txt"))
    with pytest.raises(ValueError, match="unrecognized or oversized"):
        bea_nipa.check_register(register(uri=bea_nipa.DATA_URL))
    with pytest.raises(ValueError, match="no A191RC"):
        bea_nipa.parse_data(data("%SeriesCode,Period,Value\r\nA191RX,2026Q3,\"24,611,077\"\r\n"))
    with pytest.raises(ValueError, match="not UTF-8"):
        bea_nipa.parse_data(FetchedDocument(bea_nipa.DATA_URL, "text/plain", b"%SeriesCode,Period,Value\r\n\xff\r\n",
                                            bea_nipa.ADAPTER_ID))
    many = "%SeriesCode,Period,Value\r\n" + "".join(
        f'A191RC,{1000 + year}Q{quarter},"100,000"\r\n' for year in range(501) for quarter in range(1, 5))
    with pytest.raises(ValueError, match="row bound"):
        bea_nipa.parse_data(data(many))


@pytest.mark.asyncio
async def test_bounded_fetch_reads_the_register_then_the_data():
    bodies = {bea_nipa.REGISTER_URL: REGISTER.encode(), bea_nipa.DATA_URL: DATA.encode()}
    requests = []

    class Stream(httpx.AsyncByteStream):
        def __init__(self, body):
            self.body = body

        async def __aiter__(self):
            yield self.body

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, stream=Stream(bodies[str(request.url)]), headers={"content-type": "text/plain"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        documents = await bea_nipa.fetch_documents(client)
    assert requests == [bea_nipa.REGISTER_URL, bea_nipa.DATA_URL]
    assert [doc.source_uri for doc in documents] == requests
    for response, match in (
        (httpx.Response(301, headers={"location": "https://example.invalid/"}), "HTTP 301"),
        (httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}), "media type"),
        (httpx.Response(200, content=b"x", headers={"content-type": "text/plain",
                                                    "content-length": str(bea_nipa.MAX_DATA_BYTES + 1)}),
         "exceeds bound"),
    ):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request, r=response: r)) as client:
            with pytest.raises(ValueError, match=match):
                await bea_nipa.fetch_documents(client)


def test_the_us_pack_declares_gdp_as_context_and_registers_the_adapter():
    pack = default_registry().get("US-USD")
    adapter = pack.adapter_map[bea_nipa.ADAPTER_ID]
    assert adapter.classification is ConnectorClassification.OFFICIAL_OPEN
    assert adapter.redistribution_status is RedistributionStatus.ALLOWED
    assert (adapter.expected_cadence, adapter.collection_cadence) == ("P3M", "P1D")
    assert adapter.publication_clock.precision is PublicationClockPrecision.UNKNOWN
    spec = pack.instrument_map[bea_nipa.INSTRUMENT_ID]
    assert (spec.semantic_role, spec.canonical_unit) == (
        SemanticRole.GROSS_DOMESTIC_PRODUCT, CanonicalUnit.LOCAL_CURRENCY_MILLIONS)
    assert spec.normalize(Decimal("33010036")) == Decimal("33010036")
    assert spec.mnemonic not in ALL_SERIES and reading_restricted(pack) is True
    for each in default_registry().list():
        for capability in each.capabilities:
            assert SemanticRole.GROSS_DOMESTIC_PRODUCT not in capability.required_roles
    assert bea_nipa.rights_gate_open(pack) is True
    assert ("US-USD", bea_nipa.ADAPTER_ID) in PRODUCTION_ADAPTER_KEYS
    assert bea_nipa.ADAPTER_ID in {item.adapter_id for item in build_official_adapters(
        repository=SQLiteMarketRepository())}
    assert market_source_reference(bea_nipa.ADAPTER_ID) == {
        "publisher": "U.S. Bureau of Economic Analysis", "source_url": bea_nipa.DATA_URL,
        "attribution": bea_nipa.ATTRIBUTION, "terms_url": bea_nipa.RIGHTS_URL}


def test_the_registry_record_quotes_bea():
    ledger = json.loads((ROOT / "docs" / "data-source-expansion.json").read_text())
    record = next(item for item in ledger["candidates"] if item["id"] == bea_nipa.REGISTRY_RECORD)
    assert record["access_url"] == bea_nipa.DATA_URL and record["publisher_url"] == bea_nipa.RELEASE_URL
    assert record["rights_status"] == "explicit_public_domain_with_exclusions"
    assert record["rights_evidence_url"] == bea_nipa.RIGHTS_URL
    assert ("Unless stated otherwise, the information posted on this web site is in the public domain and may "
            "be used or reproduced without specific permission.") in record["blocker"]
    for text in (bea_nipa.ATTRIBUTION, bea_nipa.SERIES_CODE, bea_nipa.FRED_COPY, bea_nipa.REGISTER_URL,
                 bea_nipa.INSTRUMENT_ID):
        assert text in record["blocker"]


@pytest.mark.asyncio
async def test_collected_gdp_reaches_the_atlas_cited_with_an_unknown_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bea.sqlite")
    captured = datetime(2026, 11, 2, 18, tzinfo=UTC)
    adapter = next(item for item in build_official_adapters(repository=SQLiteMarketRepository(),
                                                            clock=lambda: captured)
                   if item.adapter_id == bea_nipa.ADAPTER_ID)

    async def fetcher(client):
        return (register(), data())

    adapter.fetcher = fetcher
    batch = await adapter.collect()
    # Routine runs keep 400 days: 2025Q3 to 2026Q3.
    assert [row.event_time.date().isoformat() for row in batch.observations] == [
        "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]
    assert all(row.source_publication_time is None and row.knowledge_time == captured
               for row in batch.observations)
    atlas = build_global_money_market_atlas([default_registry().get("US-USD")],
                                            {"US-USD": batch.observations}, as_of=captured)
    metric = next(item for item in atlas["markets"][0]["metrics"] if item["id"] == bea_nipa.INSTRUMENT_ID)
    assert (metric["value"], metric["unit"], metric["asof"]) == (33010036.0, "local_currency_millions", "2026-09-30")
    assert metric["status"] == "UNKNOWN" and metric["attribution"] == bea_nipa.ATTRIBUTION
    assert metric["semantic_role"] == "GROSS_DOMESTIC_PRODUCT" and metric["cadence"] == "P3M"
    notice = next(item for item in atlas["legal_notices"] if item.get("source", "").startswith("U.S. Bureau"))
    assert notice["notice"].startswith(bea_nipa.ATTRIBUTION + ".") and notice["terms_url"] == bea_nipa.RIGHTS_URL
