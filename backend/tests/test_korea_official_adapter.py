from __future__ import annotations

import json
import traceback
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from fastapi import Response

from seiche import api, store
from seiche.collectors import CollectorRunStatus, CollectorSupervisor
from seiche.domain.observation import (
    ConnectorClassification,
    DayCountConvention,
    QualityState,
    RedistributionStatus,
)
from seiche.markets.registry import default_registry
from seiche.repository import SQLiteMarketRepository, reset_repository_cache
from seiche.sources import official
from seiche.sources.base import SourcePolicyUnavailableError
from seiche.sources.canonical import FetchedDocument, FunctionalCanonicalAdapter
from seiche.sources.official import build_official_adapters, parse_bok_ecos

_ISSUED_TEST_KEY = "A1234567890"


def _ecos_payload(
    instrument_id: str,
    *,
    event_day: str = "20260820",
    value: str = "2.789",
    total: int = 1,
    overrides: dict[str, object] | None = None,
) -> bytes:
    stat_code, item_code = official._BOK_ECOS_SERIES[instrument_id]
    row: dict[str, object] = {
        "STAT_CODE": stat_code,
        "STAT_NAME": "official test fixture",
        "ITEM_CODE1": item_code,
        "ITEM_NAME1": "official test fixture",
        "ITEM_CODE2": None,
        "ITEM_NAME2": None,
        "ITEM_CODE3": None,
        "ITEM_NAME3": None,
        "ITEM_CODE4": None,
        "ITEM_NAME4": None,
        "UNIT_NAME": "Percent Per Annum",
        "WGT": None,
        "TIME": event_day,
        "DATA_VALUE": value,
    }
    row.update(overrides or {})
    return json.dumps(
        {"StatisticSearch": {"list_total_count": total, "row": [row]}},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _document(
    instrument_id: str,
    **payload_kwargs,
) -> FetchedDocument:
    return FetchedDocument(
        "https://ecos.bok.or.kr/api/StatisticSearch/REDACTED/json/en/1/1",
        "application/json",
        _ecos_payload(instrument_id, **payload_kwargs),
        instrument_id,
    )


def _adapter(
    adapter_id: str,
    tmp_path,
    monkeypatch,
    *,
    capture: datetime = datetime(2026, 8, 21, tzinfo=UTC),
) -> FunctionalCanonicalAdapter:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / f"{adapter_id}.sqlite")
    return next(
        adapter
        for adapter in build_official_adapters(
            repository=SQLiteMarketRepository(),
            clock=lambda: capture,
        )
        if adapter.market_id == "KR-KRW" and adapter.adapter_id == adapter_id
    )


@pytest.mark.parametrize(
    ("instrument_id", "event_day", "value"),
    (
        ("KR.BOK.BASE_RATE", date(2026, 8, 20), Decimal("2.75")),
        (
            "KR.BOK.CALL_OVERNIGHT_ALL",
            date(2026, 8, 20),
            Decimal("2.789"),
        ),
    ),
)
def test_bok_ecos_parser_accepts_only_declared_daily_series(
    instrument_id: str,
    event_day: date,
    value: Decimal,
) -> None:
    point = parse_bok_ecos(
        _document(
            instrument_id, event_day=event_day.strftime("%Y%m%d"), value=str(value)
        )
    )[0]

    assert point.instrument_id == instrument_id
    assert point.event_time == event_day
    assert point.raw_value == value
    assert point.source_publication_time is None
    evidence = json.loads(point.row_evidence)
    assert evidence["row"]["STAT_CODE"] == official._BOK_ECOS_SERIES[instrument_id][0]
    assert evidence["row"]["ITEM_CODE1"] == official._BOK_ECOS_SERIES[instrument_id][1]


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    (
        ("STAT_CODE", "wrong", "STAT_CODE"),
        ("ITEM_CODE1", "wrong", "ITEM_CODE1"),
        ("UNIT_NAME", "Index", "UNIT_NAME"),
        ("TIME", "2026-08-20", "malformed"),
        ("DATA_VALUE", "not-a-number", "malformed"),
    ),
)
def test_bok_ecos_parser_fails_closed_on_series_contract_drift(
    field: str,
    replacement: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_bok_ecos(_document("KR.BOK.BASE_RATE", overrides={field: replacement}))


def test_bok_ecos_parser_surfaces_source_errors_without_credentials() -> None:
    document = FetchedDocument(
        "https://ecos.bok.or.kr/api/StatisticSearch/REDACTED/json/en/1/10",
        "application/json",
        b'{"RESULT":{"CODE":"ERROR-301","MESSAGE":"maximum 10 rows"}}',
        "KR.BOK.BASE_RATE",
    )

    with pytest.raises(ValueError, match="ERROR-301.*maximum 10 rows"):
        parse_bok_ecos(document)


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_key", (None, "sample", "contains-a-hyphen"))
async def test_bok_ecos_fails_before_network_without_an_issued_key(
    configured_key: str | None,
    tmp_path,
    monkeypatch,
) -> None:
    if configured_key is None:
        monkeypatch.delenv(official._BOK_ECOS_API_KEY_ENV, raising=False)
    else:
        monkeypatch.setenv(official._BOK_ECOS_API_KEY_ENV, configured_key)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    adapter = _adapter("bok_ecos_policy", tmp_path, monkeypatch)
    with pytest.raises(SourcePolicyUnavailableError, match="ECOS API key|sample key"):
        adapter.check_availability()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourcePolicyUnavailableError):
            await adapter.fetcher(client)

    assert requests == []


@pytest.mark.asyncio
async def test_bok_ecos_fetcher_uses_daily_contract_and_redacts_key(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(official._BOK_ECOS_API_KEY_ENV, _ISSUED_TEST_KEY)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=_ecos_payload("KR.BOK.BASE_RATE", value="2.75"),
            headers={"content-type": "application/json"},
        )

    adapter = _adapter("bok_ecos_policy", tmp_path, monkeypatch)
    adapter.check_availability()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        documents = tuple(await adapter.fetcher(client))

    assert len(requests) == 1
    assert requests[0].url.path == (
        "/api/StatisticSearch/A1234567890/json/en/1/10000/"
        "722Y001/D/20260707/20260821/0101000"
    )
    assert len(documents) == 1
    assert documents[0].label == "KR.BOK.BASE_RATE"
    assert _ISSUED_TEST_KEY not in documents[0].source_uri
    assert "/REDACTED/" in documents[0].source_uri


@pytest.mark.asyncio
async def test_bok_ecos_fetcher_paginates_without_truncating(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(official._BOK_ECOS_API_KEY_ENV, _ISSUED_TEST_KEY)
    monkeypatch.setattr(official, "_BOK_ECOS_PAGE_SIZE", 1)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        second_page = "/2/2/" in request.url.path
        return httpx.Response(
            200,
            content=_ecos_payload(
                "KR.BOK.CALL_OVERNIGHT_ALL",
                event_day="20260820" if second_page else "20260819",
                total=2,
            ),
        )

    adapter = _adapter("bok_ecos_money_market", tmp_path, monkeypatch)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        documents = tuple(await adapter.fetcher(client))

    assert len(requests) == 2
    assert len(documents) == 2
    assert [
        point.event_time for document in documents for point in parse_bok_ecos(document)
    ] == [
        date(2026, 8, 19),
        date(2026, 8, 20),
    ]


@pytest.mark.asyncio
async def test_bok_call_collection_preserves_kst_publication_bound_and_knowledge_time(
    tmp_path,
    monkeypatch,
) -> None:
    capture = datetime(2026, 8, 21, 16, tzinfo=UTC)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "canonical-korea.sqlite")
    document = _document("KR.BOK.CALL_OVERNIGHT_ALL")

    async def fetcher(_client):
        return (document,)

    adapter = FunctionalCanonicalAdapter(
        pack=default_registry().get("KR-KRW"),
        adapter_id="bok_ecos_money_market",
        source="bok_ecos",
        fetcher=fetcher,
        parser=parse_bok_ecos,
        repository=SQLiteMarketRepository(),
        clock=lambda: capture,
    )
    observation = (await adapter.collect()).observations[0]

    assert observation.event_time == datetime(2026, 8, 20, tzinfo=UTC)
    # ECOS has no per-row timestamp. The upstream-native contract therefore
    # uses the conservative end of the declared D+1 KST publication day.
    assert observation.source_publication_time == datetime(
        2026, 8, 21, 14, 59, 59, tzinfo=UTC
    )
    assert observation.knowledge_time == capture
    assert observation.value == Decimal("278.900")
    assert observation.day_count is DayCountConvention.ACT_365
    assert observation.connector_classification is ConnectorClassification.OFFICIAL_OPEN
    assert observation.redistribution_status is RedistributionStatus.ALLOWED
    assert observation.quality is QualityState.ESTIMATED


def test_kofr_has_no_production_adapter_without_redistribution_permission() -> None:
    keys = {
        (adapter.market_id, adapter.adapter_id)
        for adapter in build_official_adapters(
            registry=default_registry(),
            repository=SQLiteMarketRepository(),
            clock=lambda: datetime(2026, 8, 21, tzinfo=UTC),
        )
    }

    assert ("KR-KRW", "ksd_kofr") not in keys


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_kind", ("http_status", "transport", "echo", "escaped_echo")
)
async def test_bok_ecos_failure_has_no_credential_in_exception_chain_or_logs(
    failure_kind: str,
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setenv(official._BOK_ECOS_API_KEY_ENV, _ISSUED_TEST_KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        if failure_kind == "transport":
            raise httpx.ConnectError(
                f"connection failed for credential-bearing URL {request.url}",
                request=request,
            )
        if failure_kind == "echo":
            return httpx.Response(200, content=_ISSUED_TEST_KEY.encode())
        if failure_kind == "escaped_echo":
            escaped_key = "".join(
                f"\\u{ord(character):04x}" for character in _ISSUED_TEST_KEY
            )
            payload = _ecos_payload("KR.BOK.BASE_RATE").replace(
                b"official test fixture",
                escaped_key.encode(),
                1,
            )
            assert _ISSUED_TEST_KEY.encode() not in payload
            return httpx.Response(200, content=payload)
        return httpx.Response(503)

    caplog.set_level("INFO", logger="httpx")
    adapter = _adapter("bok_ecos_policy", tmp_path, monkeypatch)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(official.BOKECOSSourceError) as captured:
            await adapter.fetcher(client)

    fault = captured.value
    assert fault.__cause__ is None
    assert fault.__context__ is None
    exposed = (
        str(fault),
        repr(fault),
        repr(vars(fault)),
        "".join(traceback.format_exception(fault)),
        caplog.text,
        *(repr(record.__dict__) for record in caplog.records),
    )
    assert all(_ISSUED_TEST_KEY not in item for item in exposed)
    if failure_kind != "transport":
        assert "REDACTED" in caplog.text


@pytest.mark.asyncio
async def test_bok_ecos_failure_stays_redacted_in_persistence_and_public_faults(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bok-public-fault.sqlite")
    monkeypatch.delenv("SEICHE_DATABASE_URL", raising=False)
    monkeypatch.setenv(official._BOK_ECOS_API_KEY_ENV, _ISSUED_TEST_KEY)
    reset_repository_cache()
    repository = SQLiteMarketRepository()
    delegate = _adapter("bok_ecos_policy", tmp_path, monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    class FailingBOKAdapter:
        market_id = delegate.market_id
        adapter_id = delegate.adapter_id

        def check_availability(self) -> None:
            delegate.check_availability()

        async def collect(self):
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                await delegate.fetcher(client)
            raise AssertionError("BOK fetcher unexpectedly returned")

    async def no_sleep(_seconds: float) -> None:
        return None

    supervisor = CollectorSupervisor(
        observation_writer=repository.save_observations,
        run_writer=repository.save_collector_run,
        sleep=no_sleep,
    )
    supervisor.register(FailingBOKAdapter())
    run = (
        await supervisor.run_due(
            now=datetime(2026, 8, 21, 16, tzinfo=UTC),
            force=True,
        )
    )[0]

    assert run.status is CollectorRunStatus.FAILED
    stored = repository.latest_collector_runs("KR-KRW")
    coverage = api.coverage_v2(Response())
    atlas = api.global_money_markets_v2(Response())
    assert _ISSUED_TEST_KEY not in repr(run.to_dict())
    assert _ISSUED_TEST_KEY not in repr(stored)
    assert _ISSUED_TEST_KEY.encode() not in store.DB_PATH.read_bytes()
    assert _ISSUED_TEST_KEY not in repr(coverage)
    assert _ISSUED_TEST_KEY not in repr(atlas)
    korea = next(row for row in coverage["markets"] if row["market_id"] == "KR-KRW")
    assert korea["faults"][0]["category"] == "SOURCE_ERROR"
    assert korea["faults"][0]["detail"] == "official source collection failed"
