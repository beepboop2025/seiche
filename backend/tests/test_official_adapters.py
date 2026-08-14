from __future__ import annotations

import asyncio
import gzip
import io
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


def _rbnz_workbook_payload(*, include_b2: bool = True) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    if include_b2:
        worksheet.append(
            [
                "Date",
                "Official Cash Rate (OCR)",
                "Overnight Deposit Rate",
                "Overnight Reverse Repurchase Facility Rate",
            ]
        )
        worksheet.append([datetime(2026, 8, 10), 2.50, 2.50, 3.00])
    else:
        worksheet.append(["Date", "Unrelated series"])
        worksheet.append([datetime(2026, 8, 10), 9.99])
    payload = io.BytesIO()
    workbook.save(payload)
    return payload.getvalue()


def _rbnz_current_workbook_payload(
    *,
    reverse_series_id: str = "INM.DD2.N",
    duplicate_reverse: bool = False,
    series_marker: str = "Series Id",
    add_legacy_candidate: bool = False,
) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Data"
    worksheet.append([None, "Cash rate", "Cash rate", "Cash rate"])
    worksheet.append(
        [
            None,
            "Official Cash Rate (OCR)",
            "Overnight Deposit Rate",
            "Overnight Reverse Repurchase Facility Rate",
        ]
    )
    worksheet.append(["Notes", None, None, None])
    worksheet.append(["Unit", "%pa", "%pa", "%pa"])
    worksheet.append([series_marker, "INM.DP1.N", "INM.DD1.N", reverse_series_id])
    worksheet.append([datetime(2026, 8, 10), 2.50, 2.50, 3.00])
    if duplicate_reverse:
        worksheet.cell(
            row=2,
            column=5,
            value="Overnight Reverse Repurchase Facility Rate",
        )
        worksheet.cell(row=5, column=5, value="INM.DD2.N")
        worksheet.cell(row=6, column=5, value=3.00)
    workbook.create_sheet("Table Description").append(
        ["Table", "Daily wholesale interest rates (% pa) - B2"]
    )
    workbook.create_sheet("Series Definitions").append(
        ["Group", "Series", "Series Id", "Unit", "Note"]
    )
    if add_legacy_candidate:
        legacy = workbook.create_sheet("Legacy")
        legacy.append(
            [
                "Date",
                "Official Cash Rate (OCR)",
                "Overnight Deposit Rate",
                "Overnight Reverse Repurchase Facility Rate",
            ]
        )
        legacy.append([datetime(2026, 8, 10), 9.99, 9.99, 9.99])
    payload = io.BytesIO()
    workbook.save(payload)
    return payload.getvalue()


def _approve_rbnz_access(monkeypatch) -> None:
    monkeypatch.setattr(official, "_rbnz_access_today", lambda: date(2026, 8, 14))
    monkeypatch.setenv(official._RBNZ_ACCESS_APPROVAL_SHA256_ENV, "a" * 64)
    monkeypatch.setenv(
        official._RBNZ_ACCESS_APPROVAL_VALID_UNTIL_ENV,
        "2027-08-14",
    )


def _rbnz_mock_transport(handler) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response = handler(request)
        if response.is_stream_consumed:
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                stream=httpx.ByteStream(response.content),
            )
        return response

    return httpx.MockTransport(wrapped), requests


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

    monkeypatch.setattr(official, "_sleep", record_sleep)
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
async def test_hkma_retries_request_errors_within_the_same_budget(
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 3:
            raise httpx.ConnectError("temporary connect failure", request=request)
        return httpx.Response(200, json={"result": {"records": []}})

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(official, "_sleep", record_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        documents = tuple(await _official_adapter("hkma_official").fetcher(client))

    assert len(requests) == 3
    assert delays == [1.0, 2.0]
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

    monkeypatch.setattr(official, "_sleep", record_sleep)
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

    monkeypatch.setattr(official, "_sleep", record_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await _official_adapter("hkma_official").fetcher(client)

    assert len(requests) == 1
    assert delays == []


@pytest.mark.asyncio
async def test_rbnz_uses_canonical_official_html_after_workbook_403(
    monkeypatch,
) -> None:
    _approve_rbnz_access(monkeypatch)
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
        </table>
        <table>
          <tr><td>09 Aug 2026</td><td>9.25</td><td>9.20</td><td>9.50</td></tr>
        </table>
        </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".xlsx"):
            return httpx.Response(403, text="forbidden")
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=page,
        )

    transport, requests = _rbnz_mock_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        documents = tuple(await _official_adapter("rbnz_wholesale").fetcher(client))

    assert [str(request.url) for request in requests] == [
        "https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/statistics/"
        "series/b/b2/hb2-daily-close.xlsx",
        "https://www.rbnz.govt.nz/en/statistics/series/"
        "exchange-and-interest-rates/wholesale-interest-rates",
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
    assert all("sec-ch-ua" not in request.headers for request in requests)
    assert documents[0].source_uri == str(requests[1].url)
    assert documents[0].media_type == "text/html"
    assert {
        point.instrument_id: point.raw_value for point in parse_rbnz(documents[0])
    } == {
        "NZ.RBNZ.OVERNIGHT_DEPOSIT": Decimal("3.20"),
        "NZ.RBNZ.OVERNIGHT_REVERSE_REPO": Decimal("3.50"),
    }


@pytest.mark.asyncio
async def test_rbnz_uses_the_official_workbook_without_requesting_fallback(
    monkeypatch,
) -> None:
    _approve_rbnz_access(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            },
            content=_rbnz_workbook_payload(),
        )

    transport, requests = _rbnz_mock_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        documents = tuple(await _official_adapter("rbnz_policy").fetcher(client))

    assert len(requests) == 1
    assert len(documents) == 1
    assert documents[0].source_uri == str(requests[0].url)
    assert documents[0].source_uri.endswith("hb2-daily-close.xlsx")
    assert documents[0].media_type.endswith("spreadsheetml.sheet")
    assert documents[0].payload.startswith(b"PK\x03\x04")


@pytest.mark.asyncio
async def test_rbnz_never_follows_a_redirect(
    monkeypatch,
) -> None:
    _approve_rbnz_access(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://mirror.example.invalid/rbnz-b2.xlsx"},
        )

    transport, requests = _rbnz_mock_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RBNZSourceUnavailableError) as raised:
            await _official_adapter("rbnz_policy").fetcher(client)

    assert len(requests) == 2
    assert str(raised.value).count("HTTP 302") == 2
    assert all(request.url.host == "www.rbnz.govt.nz" for request in requests)


def test_rbnz_parses_the_current_series_id_workbook_contract() -> None:
    document = FetchedDocument(
        official._RBNZ_B2_XLSX_URI,
        official._RBNZ_XLSX_MEDIA_TYPE,
        _rbnz_current_workbook_payload(),
        "rbnz_wholesale",
    )

    points = {point.instrument_id: point for point in parse_rbnz(document)}

    assert points["NZ.RBNZ.OVERNIGHT_DEPOSIT"].raw_value == Decimal("2.5")
    assert points["NZ.RBNZ.OVERNIGHT_REVERSE_REPO"].raw_value == Decimal("3")


def test_rbnz_modern_workbook_cannot_downgrade_to_display_headings() -> None:
    document = FetchedDocument(
        official._RBNZ_B2_XLSX_URI,
        official._RBNZ_XLSX_MEDIA_TYPE,
        _rbnz_current_workbook_payload(reverse_series_id="CHANGED.UPSTREAM.ID"),
        "rbnz_wholesale",
    )

    with pytest.raises(ValueError, match="exactly one usable B2 data sheet"):
        parse_rbnz(document)


def test_rbnz_renamed_series_marker_cannot_downgrade_to_display_headings() -> None:
    document = FetchedDocument(
        official._RBNZ_B2_XLSX_URI,
        official._RBNZ_XLSX_MEDIA_TYPE,
        _rbnz_current_workbook_payload(series_marker="Series identifier"),
        "rbnz_wholesale",
    )

    with pytest.raises(ValueError, match="exactly one usable B2 data sheet"):
        parse_rbnz(document)


def test_rbnz_modern_signals_reject_a_separate_legacy_candidate() -> None:
    document = FetchedDocument(
        official._RBNZ_B2_XLSX_URI,
        official._RBNZ_XLSX_MEDIA_TYPE,
        _rbnz_current_workbook_payload(
            series_marker="Identifier",
            add_legacy_candidate=True,
        ),
        "rbnz_wholesale",
    )

    with pytest.raises(ValueError, match="exactly one usable B2 data sheet"):
        parse_rbnz(document)


def test_rbnz_modern_workbook_rejects_duplicate_series_columns() -> None:
    document = FetchedDocument(
        official._RBNZ_B2_XLSX_URI,
        official._RBNZ_XLSX_MEDIA_TYPE,
        _rbnz_current_workbook_payload(duplicate_reverse=True),
        "rbnz_wholesale",
    )

    with pytest.raises(ValueError, match="exactly one usable B2 data sheet"):
        parse_rbnz(document)


def test_rbnz_workbook_archive_row_and_cell_bounds_fail_closed(monkeypatch) -> None:
    document = FetchedDocument(
        official._RBNZ_B2_XLSX_URI,
        official._RBNZ_XLSX_MEDIA_TYPE,
        _rbnz_current_workbook_payload(),
        "rbnz_wholesale",
    )

    monkeypatch.setattr(official, "_RBNZ_MAX_XLSX_EXPANDED_BYTES", 1)
    with pytest.raises(ValueError, match="XLSX expansion limit"):
        parse_rbnz(document)
    monkeypatch.setattr(official, "_RBNZ_MAX_XLSX_EXPANDED_BYTES", 64 * 1024 * 1024)
    monkeypatch.setattr(official, "_RBNZ_MAX_WORKBOOK_ROWS", 1)
    with pytest.raises(ValueError, match="row limit"):
        parse_rbnz(document)
    monkeypatch.setattr(official, "_RBNZ_MAX_WORKBOOK_ROWS", 50_000)
    monkeypatch.setattr(official, "_RBNZ_MAX_WORKBOOK_CELLS", 1)
    with pytest.raises(ValueError, match="cell limit"):
        parse_rbnz(document)


@pytest.mark.asyncio
async def test_rbnz_access_defaults_off_without_written_approval(monkeypatch) -> None:
    monkeypatch.delenv(official._RBNZ_ACCESS_APPROVAL_SHA256_ENV, raising=False)
    monkeypatch.delenv(
        official._RBNZ_ACCESS_APPROVAL_VALID_UNTIL_ENV,
        raising=False,
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_rbnz_workbook_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            RBNZSourceUnavailableError, match="prior written permission"
        ):
            await _official_adapter("rbnz_policy").fetcher(client)

    assert requests == []


@pytest.mark.parametrize(
    ("valid_until", "message"),
    (
        ("2026-08-13", "review has expired"),
        ("2027-08-16", "reviewed within 366 days"),
    ),
)
def test_rbnz_access_approval_has_a_bounded_review_window(
    monkeypatch,
    valid_until: str,
    message: str,
) -> None:
    monkeypatch.setattr(official, "_rbnz_access_today", lambda: date(2026, 8, 14))
    monkeypatch.setenv(official._RBNZ_ACCESS_APPROVAL_SHA256_ENV, "a" * 64)
    monkeypatch.setenv(
        official._RBNZ_ACCESS_APPROVAL_VALID_UNTIL_ENV,
        valid_until,
    )

    with pytest.raises(RBNZSourceUnavailableError, match=message):
        official._require_rbnz_access_approval()


@pytest.mark.asyncio
async def test_rbnz_transport_bounds_decoded_response_body(monkeypatch) -> None:
    _approve_rbnz_access(monkeypatch)
    monkeypatch.setattr(official, "_RBNZ_MAX_BODY_BYTES", 4)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"12345")

    transport, requests = _rbnz_mock_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RBNZSourceUnavailableError) as raised:
            await _official_adapter("rbnz_policy").fetcher(client)

    assert len(requests) == 2
    assert str(raised.value).count("body limit") == 2


@pytest.mark.asyncio
async def test_rbnz_transport_rejects_compression_before_decoding(monkeypatch) -> None:
    _approve_rbnz_access(monkeypatch)
    compressed = gzip.compress(b"x" * (official._RBNZ_MAX_BODY_BYTES + 1))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=httpx.ByteStream(compressed),
        )

    transport, requests = _rbnz_mock_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RBNZSourceUnavailableError) as raised:
            await _official_adapter("rbnz_policy").fetcher(client)

    assert len(requests) == 2
    assert str(raised.value).count("unsupported transport content encoding") == 2


@pytest.mark.asyncio
async def test_rbnz_transport_enforces_a_total_response_deadline(monkeypatch) -> None:
    _approve_rbnz_access(monkeypatch)
    monkeypatch.setattr(official, "_RBNZ_TOTAL_RESPONSE_TIMEOUT_SECONDS", 0.01)

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.05)
            yield b"PK\x03\x04"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=SlowStream())

    transport, requests = _rbnz_mock_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RBNZSourceUnavailableError) as raised:
            await _official_adapter("rbnz_policy").fetcher(client)

    assert len(requests) == 2
    assert str(raised.value).count("total response deadline") == 2


@pytest.mark.asyncio
async def test_rbnz_unusable_pk_workbook_gets_one_canonical_html_attempt(
    monkeypatch,
) -> None:
    _approve_rbnz_access(monkeypatch)
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
            return httpx.Response(
                200,
                headers={"content-type": "application/octet-stream"},
                content=_rbnz_workbook_payload(include_b2=False),
            )
        return httpx.Response(200, headers={"content-type": "text/html"}, content=page)

    transport, requests = _rbnz_mock_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        documents = tuple(await _official_adapter("rbnz_policy").fetcher(client))

    assert len(requests) == 2
    assert requests[0].url.path.endswith("hb2-daily-close.xlsx")
    assert requests[1].url.path.endswith("wholesale-interest-rates")
    assert documents[0].source_uri == str(requests[1].url)
    assert documents[0].media_type == "text/html"


def test_rbnz_html_evidence_is_scoped_to_each_instrument_cell() -> None:
    def parse(*, ocr: str, deposit: str, reverse_repo: str):
        document = FetchedDocument(
            "https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates/"
            "wholesale-interest-rates",
            "text/html",
            f"""
                <table>
                  <tr><th>Date</th><th>Official Cash Rate (OCR)</th>
                    <th>Overnight Deposit Rate</th>
                    <th>Overnight Reverse Repurchase Facility Rate</th></tr>
                  <tr><td>10 Aug 2026</td><td>{ocr}</td><td>{deposit}</td>
                    <td>{reverse_repo}</td></tr>
                </table>
            """.encode(),
            "rbnz_wholesale",
        )
        return {point.instrument_id: point for point in parse_rbnz(document)}

    first = parse(ocr="2.50", deposit="2.50", reverse_repo="3.00")
    changed = parse(ocr="9.99", deposit="2.50", reverse_repo="3.10")

    assert (
        first["NZ.RBNZ.OVERNIGHT_DEPOSIT"].row_evidence
        == changed["NZ.RBNZ.OVERNIGHT_DEPOSIT"].row_evidence
    )
    assert (
        first["NZ.RBNZ.OVERNIGHT_REVERSE_REPO"].row_evidence
        != changed["NZ.RBNZ.OVERNIGHT_REVERSE_REPO"].row_evidence
    )


@pytest.mark.asyncio
async def test_rbnz_403_exhaustion_reports_both_official_endpoints(monkeypatch) -> None:
    _approve_rbnz_access(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="cloudflare challenge")

    transport, requests = _rbnz_mock_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RBNZSourceUnavailableError) as raised:
            await _official_adapter("rbnz_policy").fetcher(client)

    detail = str(raised.value)
    assert len(requests) == 2
    assert detail.count("HTTP 403") == 2
    assert "hb2-daily-close.xlsx" in detail
    assert "/statistics/series/exchange-and-interest-rates/" in detail


@pytest.mark.asyncio
async def test_rbnz_rejects_html_without_the_expected_b2_table(monkeypatch) -> None:
    _approve_rbnz_access(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".xlsx"):
            return httpx.Response(403, text="forbidden")
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><table><tr><th>Date</th><th>Unrelated</th></tr>"
            "<tr><td>08 Aug 2026</td><td>9.99</td></tr></table></html>",
        )

    transport, _ = _rbnz_mock_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RBNZSourceUnavailableError) as raised:
            await _official_adapter("rbnz_policy").fetcher(client)

    detail = str(raised.value)
    assert "HTTP 403" in detail
    assert "no expected B2 interest-rate table header" in detail


@pytest.mark.asyncio
async def test_rbnz_backfill_rejects_the_bounded_html_summary(monkeypatch) -> None:
    _approve_rbnz_access(monkeypatch)
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

    transport, _ = _rbnz_mock_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
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
