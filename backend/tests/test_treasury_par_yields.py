"""Treasury's own par yield curve: enabled by the declared rights record, cited everywhere."""
from __future__ import annotations

import json
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
    DayCountConvention,
    Observation,
    QualityState,
    RateCompounding,
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
from seiche.sources import treasury_par_yields as ust
from seiche.sources.canonical import FetchedDocument, PublicationTimePolicy
from seiche.sources.official import PRODUCTION_ADAPTER_KEYS, build_official_adapters

OCTOBER = ust.month_url(2026, 10)
FIELDS = ("BC_1MONTH", "BC_3MONTH", "BC_6MONTH", "BC_1YEAR", "BC_2YEAR", "BC_5YEAR", "BC_10YEAR", "BC_30YEAR")
# Published Daily Treasury Par Yield Curve Rates, read from the October 2026
# feed document on 2026-11-02.
ROWS = {
    "2026-10-28": ("4.07", "4.18", "4.32", "4.47", "4.67", "4.85", "5.05", "5.37"),
    "2026-10-29": ("4.06", "4.18", "4.28", "4.42", "4.61", "4.78", "4.98", "5.32"),
    "2026-10-30": ("4.06", "4.16", "4.25", "4.36", "4.56", "4.71", "4.94", "5.31"),
}


def entry(day: str, values: dict[str, str], *, extra: str = "") -> str:
    fields = "".join(f'<d:{field} m:type="Edm.Double">{value}</d:{field}>' for field, value in values.items())
    return (
        "<entry><id>x</id><title type=\"text\"></title><updated>2026-11-02T01:01:54Z</updated>"
        "<content type=\"application/xml\"><m:properties>"
        f'<d:Id m:type="Edm.Int32">1</d:Id><d:NEW_DATE m:type="Edm.DateTime">{day}T00:00:00</d:NEW_DATE>'
        f"{fields}{extra}</m:properties></content></entry>"
    )


def feed(*entries: str, title: str = "DailyTreasuryYieldCurveRateData", prolog: str = "") -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8" standalone="yes" ?>\n' + prolog
        + '<feed xml:base="https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml" '
        'xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" '
        'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" '
        'xmlns="http://www.w3.org/2005/Atom">'
        f'<title type="text">{title}</title><id>x</id><updated>2026-11-02T01:01:54Z</updated>'
        + "".join(entries) + "</feed>"
    ).encode()


def october(**overrides) -> FetchedDocument:
    entries = [entry(day, dict(zip(FIELDS, values, strict=True))) for day, values in ROWS.items()]
    return FetchedDocument(overrides.get("url", OCTOBER), "text/xml",
                           overrides.get("payload", feed(*entries)), ust.ADAPTER_ID)


def pack_with(status: RedistributionStatus | None):
    """The registered US pack with the Treasury record changed, or removed (None)."""
    if status is None:
        return replace(
            PACK,
            source_adapters=tuple(item for item in PACK.source_adapters if item.adapter_id != ust.ADAPTER_ID),
            instruments=tuple(item for item in PACK.instruments if item.source_adapter_id != ust.ADAPTER_ID),
        )
    return replace(PACK, source_adapters=tuple(
        replace(item, redistribution_status=status) if item.adapter_id == ust.ADAPTER_ID else item
        for item in PACK.source_adapters))


def registry_with(pack) -> MarketRegistry:
    return MarketRegistry(tuple(pack if item.market_id == "US-USD" else item
                                for item in default_registry().list()))


def test_the_us_pack_declares_the_allowed_treasury_record_and_tenors():
    pack = default_registry().get("US-USD")
    adapter = pack.adapter_map[ust.ADAPTER_ID]
    assert adapter.classification is ConnectorClassification.OFFICIAL_OPEN
    assert adapter.redistribution_status is RedistributionStatus.ALLOWED
    assert (adapter.expected_cadence, adapter.collection_cadence) == ("P1D", "PT1H")
    clock = adapter.publication_clock
    assert (clock.timezone_name, clock.local_time.isoformat(), clock.business_day_lag) == (
        "America/New_York", "18:00:00", 0)
    assert clock.precision is PublicationClockPrecision.SCHEDULED
    assert clock.calendar_id == pack.settlement_calendar.calendar_id
    declared = [item for item in pack.instruments if item.source_adapter_id == ust.ADAPTER_ID]
    # The pack's tenors are exactly the feed fields the source module maps.
    assert [item.instrument_id for item in declared] == [instrument for _, instrument in ust.TENORS]
    for spec in declared:
        assert spec.semantic_role is SemanticRole.SOVEREIGN_YIELD
        assert spec.canonical_unit is CanonicalUnit.BASIS_POINTS
        assert (spec.rate_compounding, spec.day_count) == (
            RateCompounding.SOURCE_NATIVE, DayCountConvention.SOURCE_NATIVE)
        assert spec.normalize(Decimal("4.16")) == Decimal("416")
    # No existing role binding moves: the 3M bill role keeps the DTB3 FRED copy.
    assert [item.instrument_id for item in pack.instruments if item.semantic_role is SemanticRole.TBILL_3M] == [
        "US.TREASURY.TBILL_3M"]
    assert pack.instrument_map["US.TREASURY.TBILL_3M"].source_adapter_id == "fred_daily"
    assert pack.adapter_map["fred_daily"].redistribution_status is RedistributionStatus.METADATA_ONLY
    assert ust.declared_instruments(pack) == set(ust.FIELDS.values())
    assert ust.rights_gate_open(pack) is True
    adapters = build_official_adapters(repository=SQLiteMarketRepository())
    assert ust.ADAPTER_ID in {item.adapter_id for item in adapters}
    assert ("US-USD", ust.ADAPTER_ID) in PRODUCTION_ADAPTER_KEYS


@pytest.mark.parametrize("status", [RedistributionStatus.METADATA_ONLY, RedistributionStatus.DERIVED_ONLY,
                                    RedistributionStatus.PROHIBITED, None])
def test_the_adapter_registers_only_with_the_declared_allowed_record(status):
    pack = pack_with(status)
    assert ust.rights_gate_open(pack) is False
    adapters = build_official_adapters(registry=registry_with(pack), repository=SQLiteMarketRepository())
    assert ust.ADAPTER_ID not in {item.adapter_id for item in adapters}
    tenant = replace(PACK, source_adapters=tuple(
        replace(item, classification=ConnectorClassification.TENANT_PROVIDED)
        if item.adapter_id == ust.ADAPTER_ID else item for item in PACK.source_adapters))
    assert ust.rights_gate_open(tenant) is False


def test_the_attribution_matches_the_registry_and_the_serving_maps():
    assert market_source_reference(ust.ADAPTER_ID) == {
        "publisher": "U.S. Department of the Treasury",
        "source_url": ust.SOURCE_URL,
        "attribution": ust.ATTRIBUTION,
        "terms_url": ust.TERMS_URL,
    }
    assert ust.ATTRIBUTION == "Source: U.S. Department of the Treasury, Daily Treasury Par Yield Curve Rates"
    ledger = json.loads((Path(__file__).resolve().parents[2] / "docs" / "data-source-expansion.json").read_text())
    record = next(item for item in ledger["candidates"] if item["id"] == "treasury_par_yield_curve")
    assert record["access_url"] == f"{ust.FEED_URL}?data={ust.DATASET}"
    assert record["rights_status"] == "explicit_public_domain_with_exclusions"
    assert record["rights_evidence_url"] == "https://www.copyright.gov/title17/92chap1.html"
    assert record["implementation_status"] == "implemented"
    assert record["semantic_roles"] == ["SOVEREIGN_YIELD"]
    # The verified wording is recorded, including that Treasury's terms page is silent.
    assert ("Copyright protection under this title is not available for any work of the United States "
            "Government") in record["blocker"]
    assert "The Treasury's official yield curve is a par yield curve derived using a monotone convex method." in record["blocker"]
    assert ust.TERMS_URL in record["blocker"] and "no copyright statement and no reuse restriction" in record["blocker"]
    assert ust.ATTRIBUTION in record["blocker"]


def test_document_urls_cover_the_window_by_month_or_by_year():
    assert ust.document_urls(date(2026, 9, 16), date(2026, 10, 31), by_year=False) == (
        ust.month_url(2026, 9), ust.month_url(2026, 10))
    assert ust.document_urls(date(2025, 12, 1), date(2026, 1, 15), by_year=False) == (
        ust.month_url(2025, 12), ust.month_url(2026, 1))
    assert ust.document_urls(date(2018, 1, 1), date(2026, 11, 2), by_year=True) == tuple(
        ust.year_url(year) for year in range(2018, 2027))
    assert ust.month_url(2026, 10).endswith("?data=daily_treasury_yield_curve&field_tdr_date_value_month=202610")
    assert ust.year_url(2026).endswith("?data=daily_treasury_yield_curve&field_tdr_date_value=2026")
    with pytest.raises(ValueError):
        ust.document_urls(date(2026, 2, 1), date(2026, 1, 1), by_year=False)
    with pytest.raises(ValueError):
        ust.document_urls(date(2020, 1, 1), date(2026, 1, 1), by_year=False)
    with pytest.raises(ValueError):
        ust.document_urls(date(1950, 1, 1), date(2026, 1, 1), by_year=True)


def test_parser_reads_each_mapped_tenor_in_percent():
    points = ust.parse_document(october())
    by_key = {(p.instrument_id, p.event_time): p.raw_value for p in points}
    assert len(points) == len(ROWS) * len(FIELDS)
    assert by_key[("US.TREASURY.PAR_YIELD_3M", date(2026, 10, 30))] == Decimal("4.16")
    assert by_key[("US.TREASURY.PAR_YIELD_2Y", date(2026, 10, 30))] == Decimal("4.56")
    assert by_key[("US.TREASURY.PAR_YIELD_5Y", date(2026, 10, 30))] == Decimal("4.71")
    assert by_key[("US.TREASURY.PAR_YIELD_10Y", date(2026, 10, 30))] == Decimal("4.94")
    assert by_key[("US.TREASURY.PAR_YIELD_30Y", date(2026, 10, 30))] == Decimal("5.31")
    assert all(p.source_publication_time is None for p in points)
    assert all(p.publication_time_policy is PublicationTimePolicy.INFER for p in points)
    last = next(p for p in points if p.instrument_id == "US.TREASURY.PAR_YIELD_10Y" and p.event_time == date(2026, 10, 30))
    evidence = json.loads(last.row_evidence)
    assert evidence == {"feed_url": ust.FEED_URL, "dataset": ust.DATASET, "field": "BC_10YEAR",
                        "type": "Edm.Double", "new_date": "2026-10-30T00:00:00", "value": "4.94"}
    assert last.revision_id.startswith("ust-par:BC_10YEAR:2026-10-30:")


def test_a_backfill_year_document_yields_the_same_revisions_as_the_month():
    month = ust.parse_document(october())
    year = ust.parse_document(october(url=ust.year_url(2026)))
    assert [(p.instrument_id, p.event_time, p.raw_value, p.revision_id, p.row_evidence) for p in month] == [
        (p.instrument_id, p.event_time, p.raw_value, p.revision_id, p.row_evidence) for p in year]


def test_absent_null_and_unmapped_fields_stay_absent():
    values = {"BC_1MONTH": "4.06", "BC_1_5MONTH": "4.09", "BC_30YEARDISPLAY": "5.31"}
    doc = october(payload=feed(entry("2026-10-30", values, extra='<d:BC_3MONTH m:type="Edm.Double" m:null="true" />')))
    points = ust.parse_document(doc)
    assert [(p.instrument_id, p.raw_value) for p in points] == [("US.TREASURY.PAR_YIELD_1M", Decimal("4.06"))]
    # A month with no trading day yet is an empty document, not an error.
    assert ust.parse_document(october(payload=feed())) == ()


def _good_entry(day="2026-10-30", **values):
    base = dict(zip(FIELDS, ROWS["2026-10-30"], strict=True))
    base.update(values)
    return entry(day, base)


@pytest.mark.parametrize("payload", [
    feed(_good_entry(), title="DailyTreasuryBillRateData"),
    feed(_good_entry(), prolog='<!DOCTYPE feed [<!ENTITY x "y">]>'),
    feed(_good_entry(BC_10YEAR="N/A")),
    feed(_good_entry(BC_10YEAR="4,94")),
    feed(_good_entry(BC_10YEAR="NaN")),
    feed(_good_entry(BC_10YEAR="75.00")),
    feed(_good_entry(day="2026-09-30")),
    feed(_good_entry(day="2026-10-31")),
    feed(_good_entry(), _good_entry()),
    feed(entry("2026-10-30", {"BC_10YEAR": "4.94"}, extra='<d:BC_10YEAR m:type="Edm.Double">4.95</d:BC_10YEAR>')),
    feed(entry("2026-10-30", {}, extra='<d:BC_10YEAR m:type="Edm.String">4.94</d:BC_10YEAR>')),
    feed(_good_entry()).replace(b'm:type="Edm.DateTime"', b'm:type="Edm.String"'),
    feed(_good_entry()).replace(b"2026-10-30T00:00:00", b"2026-10-30T12:00:00"),
    feed(_good_entry())[:-30],
])
def test_changed_identity_types_dates_or_values_fail_closed(payload):
    with pytest.raises(ValueError):
        ust.parse_document(october(payload=payload))


def test_unrecognized_or_oversized_documents_fail_closed():
    with pytest.raises(ValueError):
        ust.parse_document(october(url="https://example.invalid/xml?data=daily_treasury_yield_curve"))
    with pytest.raises(ValueError):
        ust.parse_document(october(url=f"{ust.FEED_URL}?data=daily_treasury_bill_rates&field_tdr_date_value=2026"))
    with pytest.raises(ValueError):
        ust.parse_document(october(url=ust.month_url(2026, 13)))
    with pytest.raises(ValueError):
        ust.parse_document(october(payload=b" " * (ust.MAX_DOCUMENT_BYTES + 1)))


@pytest.mark.asyncio
async def test_bounded_fetch_reads_only_the_named_documents_and_rejects_changes():
    good = october().payload
    requests = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield good

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=Stream(), headers={"content-type": "text/xml; charset=UTF-8"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        documents = await ust.fetch_documents(client, (OCTOBER,))
    assert [(item.source_uri, item.payload) for item in documents] == [(OCTOBER, good)]
    assert str(requests[0].url) == OCTOBER
    assert requests[0].headers["accept-encoding"] == "identity"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="unrecognized"):
            await ust.fetch_documents(client, ("https://example.invalid/feed",))
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"location": "https://example.invalid/"}))) as client:
        with pytest.raises(ValueError, match="HTTP 302"):
            await ust.fetch_documents(client, (OCTOBER,))
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"}))) as client:
        with pytest.raises(ValueError, match="media type"):
            await ust.fetch_documents(client, (OCTOBER,))
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"small", headers={
                "content-type": "text/xml", "content-length": str(ust.MAX_DOCUMENT_BYTES + 1)}))) as client:
        with pytest.raises(ValueError, match="bound"):
            await ust.fetch_documents(client, (OCTOBER,))


async def collected(captured: datetime, monkeypatch):
    requested: list[tuple[str, ...]] = []

    async def fetch(client, urls):
        requested.append(tuple(urls))
        return tuple(october(url=url) if url == OCTOBER else october(url=url, payload=feed()) for url in urls)

    monkeypatch.setattr(ust, "fetch_documents", fetch)
    adapter = next(a for a in build_official_adapters(
        repository=SQLiteMarketRepository(), clock=lambda: captured)
        if a.adapter_id == ust.ADAPTER_ID)
    return await adapter.collect(), requested


def fred_bill(day: str, value: str) -> Observation:
    spec = PACK.instrument_map["US.TREASURY.TBILL_3M"]
    event = datetime.fromisoformat(day + "T00:00:00+00:00")
    published = event + timedelta(hours=22)
    return Observation(
        market_id="US-USD", monetary_area_id="US", jurisdiction_codes=("US",), currency="USD",
        instrument_id=spec.instrument_id, semantic_role=spec.semantic_role, value=Decimal(value),
        canonical_unit=spec.canonical_unit, rate_compounding=spec.rate_compounding, day_count=spec.day_count,
        event_time=event, source_publication_time=published, knowledge_time=published,
        revision_id=f"fred-{day}", source="fred", evidence_hash=evidence_sha256(f"fred-{day}-{value}"),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.METADATA_ONLY,
        quality=QualityState.VERIFIED, staleness=StalenessState.FRESH,
    )


@pytest.mark.asyncio
async def test_treasury_rows_serve_allowed_with_the_citation_and_fred_stays_withheld(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "treasury-par.sqlite")
    # Monday 2026-11-02 12:00 UTC: Friday's 18:00 New York posting is out.
    captured = datetime(2026, 11, 2, 12, tzinfo=UTC)
    batch, requested = await collected(captured, monkeypatch)
    # A routine run asks for the month documents of its 45-day window only.
    assert requested == [(ust.month_url(2026, 9), ust.month_url(2026, 10), ust.month_url(2026, 11))]
    rows = [row for row in batch.observations if row.instrument_id == "US.TREASURY.PAR_YIELD_10Y"]
    assert [(row.event_time.date().isoformat(), row.value) for row in rows] == [
        ("2026-10-28", Decimal("505")), ("2026-10-29", Decimal("498")), ("2026-10-30", Decimal("494"))]
    assert len(batch.observations) == len(ROWS) * len(FIELDS)
    assert all(row.source == ust.ADAPTER_ID for row in batch.observations)
    assert all(row.redistribution_status is RedistributionStatus.ALLOWED for row in batch.observations)
    assert all(row.quality is QualityState.VERIFIED for row in batch.observations)
    # Friday 2026-10-30 18:00 New York (EDT), the declared posting time.
    assert rows[-1].source_publication_time == datetime(2026, 10, 30, 22, 0, tzinfo=UTC)

    pack = default_registry().get("US-USD")
    atlas = build_global_money_market_atlas(
        [pack], {"US-USD": [*batch.observations, fred_bill("2026-10-30", "395")]}, as_of=captured)
    metrics = {m["id"]: m for m in atlas["markets"][0]["metrics"]}
    for instrument_id, value in (("US.TREASURY.PAR_YIELD_3M", 4.16), ("US.TREASURY.PAR_YIELD_2Y", 4.56),
                                 ("US.TREASURY.PAR_YIELD_10Y", 4.94), ("US.TREASURY.PAR_YIELD_30Y", 5.31)):
        metric = metrics[instrument_id]
        assert metric["availability"] == "AVAILABLE" and metric["redistribution_status"] == "allowed"
        assert (metric["value"], metric["unit"], metric["asof"]) == (value, "%", "2026-10-30")
        assert metric["status"] == "FRESH"
        assert metric["semantic_role"] == "SOVEREIGN_YIELD"
        assert metric["attribution"] == ust.ATTRIBUTION and metric["terms_url"] == ust.TERMS_URL
        assert metric["source_url"] == ust.SOURCE_URL
    assert [point[1] for point in metrics["US.TREASURY.PAR_YIELD_3M"]["history"]] == [4.18, 4.18, 4.16]
    fred = metrics["US.TREASURY.TBILL_3M"]
    assert fred["availability"] == "RESTRICTED" and fred["value"] is None and fred["history"] == []
    assert "attribution" not in fred
    notice = next(item for item in atlas["legal_notices"]
                  if item.get("source") == "U.S. Department of the Treasury, Daily Treasury Par Yield Curve Rates")
    assert notice["terms_url"] == ust.TERMS_URL
    assert notice["notice"].startswith(ust.ATTRIBUTION + ".")


@pytest.mark.asyncio
async def test_a_same_day_row_waits_for_the_declared_posting_time(monkeypatch):
    # Friday 2026-10-30 at 17:00 New York: the feed may already show Friday's
    # row, but the declared 18:00 posting has not passed, so it is not knowable.
    captured = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
    batch, _ = await collected(captured, monkeypatch)
    assert {row.event_time.date().isoformat() for row in batch.observations} == {"2026-10-28", "2026-10-29"}


@pytest.mark.asyncio
async def test_series_page_cites_treasury(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "treasury-par-series.sqlite")
    captured = datetime(2026, 11, 2, 12, tzinfo=UTC)
    batch, _ = await collected(captured, monkeypatch)
    store.save_observations([*batch.observations, fred_bill("2026-10-30", "395")])

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
    direct = instruments["US.TREASURY.PAR_YIELD_10Y"]
    assert direct["availability"] == "READY" and direct["redistribution_status"] == "allowed"
    assert direct["attribution"] == ust.ATTRIBUTION and direct["terms_url"] == ust.TERMS_URL
    assert direct["publisher"] == "U.S. Department of the Treasury"
    assert instruments["US.TREASURY.TBILL_3M"]["availability"] == "RESTRICTED"
    records = {(item["instrument_id"], item["event_time"][:10]): item for item in payload["observations"]}
    assert records[("US.TREASURY.PAR_YIELD_10Y", "2026-10-30")]["value"] == "494.00"
    assert records[("US.TREASURY.TBILL_3M", "2026-10-30")]["value"] is None
