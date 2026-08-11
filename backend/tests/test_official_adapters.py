from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from seiche import store
from seiche.domain.observation import QualityState
from seiche.markets.registry import default_registry
from seiche.repository import SQLiteMarketRepository
from seiche.sources.canonical import (
    FetchedDocument,
    FunctionalCanonicalAdapter,
    ParsedPoint,
)
from seiche.sources import official
from seiche.sources.official import (
    PRODUCTION_ADAPTER_KEYS,
    RBNZSourceUnavailableError,
    bounded_date_windows,
    build_official_adapters,
    parse_nyfed_rates,
    parse_rbnz,
)


def test_bounded_date_windows_are_complete_non_overlapping_and_inclusive() -> None:
    windows = bounded_date_windows(
        date(2024, 1, 1),
        date(2026, 1, 5),
        maximum_days=360,
    )

    assert windows[0][0] == date(2024, 1, 1)
    assert windows[-1][1] == date(2026, 1, 5)
    assert all((end - start).days < 360 for start, end in windows)
    assert all(
        current[1].toordinal() + 1 == following[0].toordinal()
        for current, following in zip(windows, windows[1:])
    )


def test_nyfed_sofr_median_uses_percent_rate_and_binds_field_date_lineage() -> None:
    document = FetchedDocument(
        "https://markets.newyorkfed.org/api/rates/secured/all/search.json",
        "application/json",
        b'{"refRates":[{"type":"SOFR","effectiveDate":"2026-08-07",'
        b'"percentRate":5.31,"percentPercentile25":4.87,'
        b'"percentPercentile99":5.45,"volumeInBillions":2100}]}',
        "nyfed_secured_rates",
    )

    points = parse_nyfed_rates(document)
    by_instrument = {point.instrument_id: point for point in points}
    median = by_instrument["US.NYFED.SOFR_MEDIAN"]

    assert median.raw_value == Decimal("5.31")
    assert median.raw_value != Decimal("4.87")
    assert re.fullmatch(
        r"nyfed:percentRate:2026-08-07:unrevised-[0-9a-f]{16}",
        str(median.revision_id),
    )
    assert re.fullmatch(
        r"nyfed:percentPercentile99:2026-08-07:unrevised-[0-9a-f]{16}",
        str(by_instrument["US.NYFED.SOFR_P99"].revision_id),
    )
    assert re.fullmatch(
        r"nyfed:volumeInBillions:2026-08-07:unrevised-[0-9a-f]{16}",
        str(by_instrument["US.NYFED.SOFR_VOLUME"].revision_id),
    )


def test_nyfed_revision_indicator_cannot_replace_field_and_event_lineage() -> None:
    document = FetchedDocument(
        "https://markets.newyorkfed.org/api/rates/secured/all/search.json",
        "application/json",
        b'{"refRates":[{"type":"SOFR","effectiveDate":"2026-08-06",'
        b'"percentRate":5.3,"percentPercentile25":5.2,'
        b'"percentPercentile99":5.4,"volumeInBillions":2000,'
        b'"revisionIndicator":"R1"}]}',
        "nyfed_secured_rates",
    )

    points = parse_nyfed_rates(document)

    assert len({point.revision_id for point in points}) == 3
    assert all(
        re.fullmatch(
            r"nyfed:(?:percentRate|percentPercentile99|volumeInBillions):"
            r"2026-08-06:R1-[0-9a-f]{16}",
            str(point.revision_id),
        )
        for point in points
    )


def test_nyfed_changed_unflagged_row_gets_distinct_revision_lineage() -> None:
    first = FetchedDocument(
        "https://markets.newyorkfed.org/api/rates/secured/all/search.json",
        "application/json",
        b'{"refRates":[{"type":"SOFR","effectiveDate":"2026-08-06",'
        b'"percentRate":5.30,"percentPercentile99":5.40,'
        b'"volumeInBillions":2000}]}',
        "nyfed_secured_rates",
    )
    changed = FetchedDocument(
        first.source_uri,
        first.media_type,
        b'{"refRates":[{"type":"SOFR","effectiveDate":"2026-08-06",'
        b'"percentRate":5.31,"percentPercentile99":5.40,'
        b'"volumeInBillions":2000}]}',
        first.label,
    )

    first_median = next(
        point
        for point in parse_nyfed_rates(first)
        if point.instrument_id == "US.NYFED.SOFR_MEDIAN"
    )
    changed_median = next(
        point
        for point in parse_nyfed_rates(changed)
        if point.instrument_id == "US.NYFED.SOFR_MEDIAN"
    )

    assert first_median.revision_id != changed_median.revision_id
    assert str(first_median.revision_id).startswith(
        "nyfed:percentRate:2026-08-06:unrevised-"
    )


def test_every_production_adapter_is_pack_declared_without_network_io(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "adapters.sqlite")
    adapters = build_official_adapters(
        repository=SQLiteMarketRepository(),
        clock=lambda: datetime(2026, 8, 9, tzinfo=UTC),
    )
    keys = {(item.market_id, item.adapter_id) for item in adapters}

    assert keys == PRODUCTION_ADAPTER_KEYS
    registry = default_registry()
    assert all(
        adapter.adapter_id in registry.get(adapter.market_id).adapter_map
        for adapter in adapters
    )


def _official_adapter(
    adapter_id: str, *, backfill: bool = False
) -> FunctionalCanonicalAdapter:
    adapters = build_official_adapters(
        repository=SQLiteMarketRepository(),
        backfill=backfill,
        clock=lambda: datetime(2026, 8, 11, tzinfo=UTC),
    )
    return next(item for item in adapters if item.adapter_id == adapter_id)


def test_connector_owned_retry_policies_are_not_multiplied_by_supervisor() -> None:
    registry = default_registry()

    assert registry.get("HK-HKD").adapter_map["hkma_official"].retry_limit == 0
    assert registry.get("NZ-NZD").adapter_map["rbnz_policy"].retry_limit == 0
    assert registry.get("NZ-NZD").adapter_map["rbnz_wholesale"].retry_limit == 0


@pytest.mark.asyncio
async def test_hkma_retries_server_errors_with_bounded_backoff(
    monkeypatch,
) -> None:
    statuses = iter((502, 503, 200))
    requests: list[httpx.Request] = []
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status = next(statuses)
        return httpx.Response(
            status,
            headers={"content-type": "application/json; charset=utf-8"},
            json={"result": {"records": []}},
        )

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(official.asyncio, "sleep", record_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        documents = tuple(await _official_adapter("hkma_official").fetcher(client))

    assert [request.url.params["pagesize"] for request in requests] == [
        "1000",
        "1000",
        "1000",
    ]
    assert all(request.headers["accept"] == "application/json" for request in requests)
    assert delays == [1.0, 2.0]
    assert len(documents) == 1
    assert documents[0].label == "hkma_liquidity"
    assert documents[0].media_type == "application/json"
    assert documents[0].source_uri == str(requests[-1].url)


@pytest.mark.asyncio
async def test_hkma_raises_last_server_response_after_retry_exhaustion(
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(502, text="upstream temporarily unavailable")

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(official.asyncio, "sleep", record_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError) as raised:
            await _official_adapter("hkma_official").fetcher(client)

    assert raised.value.response.status_code == 502
    assert len(requests) == 3
    assert delays == [1.0, 2.0]


@pytest.mark.asyncio
async def test_hkma_does_not_retry_a_non_server_response(monkeypatch) -> None:
    requests: list[httpx.Request] = []
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(403, text="forbidden")

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(official.asyncio, "sleep", record_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await _official_adapter("hkma_official").fetcher(client)

    assert len(requests) == 1
    assert delays == []


@pytest.mark.asyncio
async def test_rbnz_uses_canonical_official_html_after_workbook_403() -> None:
    requests: list[httpx.Request] = []
    page = b"""
        <html><body><table>
          <tr><th>Cash rate (%pa)</th></tr>
          <tr>
            <th>Date</th>
            <th>Official Cash Rate (OCR)</th>
            <th>Overnight Deposit Rate</th>
            <th>Overnight Reverse Repurchase Facility Rate</th>
          </tr>
          <tr><td>08 Aug 2026</td><td>3.25</td><td>3.20</td><td>3.50</td></tr>
        </table></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith(".xlsx"):
            return httpx.Response(403, text="forbidden")
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=page,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        documents = tuple(await _official_adapter("rbnz_wholesale").fetcher(client))

    assert [str(request.url) for request in requests] == [
        "https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/statistics/"
        "series/b/b2/hb2-daily-close.xlsx",
        "https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates/"
        "wholesale-interest-rates",
    ]
    assert (
        requests[0]
        .headers["accept"]
        .startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    )
    assert requests[0].headers["referer"] == str(requests[1].url)
    assert all(
        request.headers["user-agent"] == official.USER_AGENT for request in requests
    )
    assert documents[0].source_uri == str(requests[1].url)
    assert documents[0].media_type == "text/html"
    assert {
        point.instrument_id: point.raw_value for point in parse_rbnz(documents[0])
    } == {
        "NZ.RBNZ.OVERNIGHT_DEPOSIT": Decimal("3.20"),
        "NZ.RBNZ.OVERNIGHT_REVERSE_REPO": Decimal("3.50"),
    }


@pytest.mark.asyncio
async def test_rbnz_uses_the_official_workbook_without_requesting_fallback() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={
                "content-type": (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            },
            content=b"PK\x03\x04representative workbook bytes",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        documents = tuple(await _official_adapter("rbnz_policy").fetcher(client))

    assert len(requests) == 1
    assert len(documents) == 1
    assert documents[0].source_uri == str(requests[0].url)
    assert documents[0].source_uri.endswith("hb2-daily-close.xlsx")
    assert documents[0].media_type.endswith("spreadsheetml.sheet")
    assert documents[0].payload.startswith(b"PK\x03\x04")


@pytest.mark.asyncio
async def test_rbnz_403_exhaustion_reports_both_official_endpoints() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(403, text="cloudflare challenge")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RBNZSourceUnavailableError) as raised:
            await _official_adapter("rbnz_policy").fetcher(client)

    detail = str(raised.value)
    assert len(requests) == 2
    assert detail.count("HTTP 403") == 2
    assert "hb2-daily-close.xlsx" in detail
    assert "/statistics/series/exchange-and-interest-rates/" in detail


@pytest.mark.asyncio
async def test_rbnz_rejects_html_without_the_expected_b2_table() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".xlsx"):
            return httpx.Response(403, text="forbidden")
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><table><tr><th>Date</th><th>Unrelated</th></tr>"
            "<tr><td>08 Aug 2026</td><td>9.99</td></tr></table></html>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RBNZSourceUnavailableError) as raised:
            await _official_adapter("rbnz_policy").fetcher(client)

    detail = str(raised.value)
    assert "HTTP 403" in detail
    assert "no expected B2 interest-rate table header" in detail


@pytest.mark.asyncio
async def test_rbnz_backfill_rejects_the_bounded_html_summary() -> None:
    page = b"""
        <table>
          <tr>
            <th>Date</th>
            <th>Official Cash Rate (OCR)</th>
            <th>Overnight Deposit Rate</th>
            <th>Overnight Reverse Repurchase Facility Rate</th>
          </tr>
          <tr><td>10 Aug 2026</td><td>2.50</td><td>2.50</td><td>3.00</td></tr>
        </table>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".xlsx"):
            return httpx.Response(403, text="forbidden")
        return httpx.Response(200, headers={"content-type": "text/html"}, content=page)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RBNZSourceUnavailableError) as raised:
            await _official_adapter("rbnz_policy", backfill=True).fetcher(client)

    detail = str(raised.value)
    assert "HTTP 403" in detail
    assert "recent summary" in detail
    assert "cannot satisfy a historical backfill" in detail


@pytest.mark.asyncio
async def test_historical_current_vintage_is_not_leaked_into_the_past(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "backfill.sqlite")
    capture = datetime(2026, 8, 9, 12, tzinfo=UTC)
    document = FetchedDocument(
        "https://example.invalid/official.csv",
        "text/csv",
        b"DATE,VALUE\n2020-01-02,1.50\n",
        "US.NYFED.SOFR",
    )

    async def fetcher(_client):
        return (document,)

    def parser(_document):
        return (
            ParsedPoint(
                "US.NYFED.SOFR",
                date(2020, 1, 2),
                "1.50",
                b"2020-01-02,1.50",
            ),
        )

    adapter = FunctionalCanonicalAdapter(
        pack=default_registry().get("US-USD"),
        adapter_id="fred_daily",
        source="official-test",
        fetcher=fetcher,
        parser=parser,
        repository=SQLiteMarketRepository(),
        clock=lambda: capture,
        historical_backfill=True,
    )
    batch = await adapter.collect()
    observation = batch.observations[0]

    assert observation.source_publication_time < capture
    assert observation.knowledge_time == capture
    assert observation.quality is QualityState.PROVISIONAL
    assert observation.value == 150


@pytest.mark.asyncio
async def test_changed_source_content_can_revert_without_revision_id_collision(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "revision-reversion.sqlite")
    repository = SQLiteMarketRepository()
    base = (
        b'{"refRates":[{"type":"SOFR","effectiveDate":"2026-08-06",'
        b'"percentRate":5.30,"percentPercentile99":5.40,'
        b'"volumeInBillions":2000}]}'
    )
    changed = base.replace(b'"percentRate":5.30', b'"percentRate":5.31')

    async def collect(payload: bytes, captured_at: datetime):
        document = FetchedDocument(
            "https://markets.newyorkfed.org/api/rates/secured/all/search.json",
            "application/json",
            payload,
            "nyfed_secured_rates",
        )

        async def fetcher(_client):
            return (document,)

        adapter = FunctionalCanonicalAdapter(
            pack=default_registry().get("US-USD"),
            adapter_id="nyfed_rates",
            source="nyfed_rates",
            fetcher=fetcher,
            parser=parse_nyfed_rates,
            repository=repository,
            clock=lambda: captured_at,
        )
        batch = await adapter.collect()
        repository.save_observations(batch.observations)
        return next(
            row
            for row in batch.observations
            if row.instrument_id == "US.NYFED.SOFR_MEDIAN"
        )

    first = await collect(base, datetime(2026, 8, 10, 10, tzinfo=UTC))
    second = await collect(changed, datetime(2026, 8, 10, 11, tzinfo=UTC))
    reverted = await collect(base, datetime(2026, 8, 10, 12, tzinfo=UTC))

    assert len({first.revision_id, second.revision_id, reverted.revision_id}) == 3
    assert "@capture-20260810T110000Z" in second.revision_id
    assert "@capture-20260810T120000Z" in reverted.revision_id


@pytest.mark.asyncio
async def test_identical_content_gains_explicit_field_lineage_once(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "lineage-upgrade.sqlite")
    repository = SQLiteMarketRepository()
    document = FetchedDocument(
        "https://markets.newyorkfed.org/api/rates/secured/all/search.json",
        "application/json",
        b"{}",
        "nyfed_secured_rates",
    )
    evidence = b"same canonical P99 source row"
    explicit_revision = "nyfed:percentPercentile99:2026-08-06:unrevised-content"

    async def collect(*, captured_at: datetime, revision_id: str | None):
        async def fetcher(_client):
            return (document,)

        def parser(_document):
            return (
                ParsedPoint(
                    "US.NYFED.SOFR_P99",
                    date(2026, 8, 6),
                    Decimal("5.40"),
                    evidence,
                    revision_id=revision_id,
                ),
            )

        adapter = FunctionalCanonicalAdapter(
            pack=default_registry().get("US-USD"),
            adapter_id="nyfed_rates",
            source="nyfed_rates",
            fetcher=fetcher,
            parser=parser,
            repository=repository,
            clock=lambda: captured_at,
        )
        batch = await adapter.collect()
        repository.save_observations(batch.observations)
        return batch.observations[0]

    legacy = await collect(
        captured_at=datetime(2026, 8, 10, 10, tzinfo=UTC),
        revision_id=None,
    )
    upgraded = await collect(
        captured_at=datetime(2026, 8, 10, 11, tzinfo=UTC),
        revision_id=explicit_revision,
    )
    repeated = await collect(
        captured_at=datetime(2026, 8, 10, 12, tzinfo=UTC),
        revision_id=explicit_revision,
    )

    assert legacy.revision_id.startswith("sha256:")
    assert upgraded.revision_id.startswith(f"{explicit_revision}@capture-")
    assert upgraded.evidence_hash == legacy.evidence_hash
    assert repeated.revision_id == upgraded.revision_id
    assert repeated.knowledge_time == upgraded.knowledge_time
