"""Long-lived adapters advance request windows without moving backfill cutoffs."""

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from seiche.sources import official


def _adapter(adapter_id, clock, *, backfill=False):
    return next(
        item
        for item in official.build_official_adapters(clock=clock, backfill=backfill)
        if item.adapter_id == adapter_id
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", ["nyfed_rates", "nyfed_unsecured_rates"])
@pytest.mark.parametrize("backfill", [False, True])
async def test_nyfed_same_adapter_advances_window_only_for_recent_collection(
    monkeypatch, adapter_id, backfill
):
    monkeypatch.setenv("SEICHE_CANONICAL_START", "2018-01-01")
    current = datetime(2026, 12, 31, 12, tzinfo=UTC)
    adapter = _adapter(adapter_id, lambda: current, backfill=backfill)
    requests = []

    def reply(request):
        requests.append(dict(request.url.params))
        return httpx.Response(200, json={"refRates": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        await adapter.fetcher(client)
        current = datetime(2027, 1, 2, 12, tzinfo=UTC)
        await adapter.fetcher(client)

    ends = [date(2026, 12, 31), date(2026, 12, 31) if backfill else date(2027, 1, 2)]
    assert requests == [
        {
            "startDate": (date(2018, 1, 1) if backfill else end - timedelta(days=45)).isoformat(),
            "endDate": end.isoformat(),
        }
        for end in ends
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter_id,parameter",
    [
        ("fred_daily", "cosd"),
        ("fred_weekly", "cosd"),
        ("ecb_benchmark", "startPeriod"),
        ("ecb_policy", "startPeriod"),
        ("ecb_liquidity", "startPeriod"),
        ("boe_sonia", "Datefrom"),
        ("boe_policy", "Datefrom"),
        ("fiscaldata", "filter"),
    ],
)
@pytest.mark.parametrize("backfill", [False, True])
async def test_request_batch_has_one_window_and_next_fetch_advances(
    monkeypatch, adapter_id, parameter, backfill
):
    monkeypatch.setenv("SEICHE_CANONICAL_START", "2018-01-01")
    current = datetime(2026, 12, 31, 12, tzinfo=UTC)
    adapter = _adapter(adapter_id, lambda: current, backfill=backfill)
    batches = [[], []]
    batch = 0

    def reply(request):
        nonlocal current
        batches[batch].append(dict(request.url.params))
        # A batch/paginated source crossing midnight must keep one interval.
        current = datetime(2027, 1, 2, 12, tzinfo=UTC)
        return httpx.Response(200, json={"meta": {"total-pages": 2}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        await adapter.fetcher(client)
        batch = 1
        await adapter.fetcher(client)

    for batch_index, requests in enumerate(batches):
        end = date(2026, 12, 31) if batch_index == 0 else date(2027, 1, 2)
        start = date(2018, 1, 1) if backfill else end - timedelta(days=45)
        assert requests
        if parameter == "filter":
            assert len(requests) == 2
            assert {row[parameter].split(",")[0] for row in requests} == {f"record_date:gte:{start}"}
        else:
            expected = start.strftime("%d/%b/%Y") if parameter == "Datefrom" else start.isoformat()
            assert {row[parameter] for row in requests} == {expected}


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", ["bok_ecos_policy", "bok_ecos_money_market"])
@pytest.mark.parametrize("backfill", [False, True])
async def test_bok_window_uses_current_seoul_day_and_pins_pagination(
    monkeypatch, adapter_id, backfill
):
    monkeypatch.setenv("SEICHE_CANONICAL_START", "2018-01-01")
    monkeypatch.setenv("SEICHE_BOK_ECOS_API_KEY", "A1234567890")
    monkeypatch.setattr(official, "_BOK_ECOS_PAGE_SIZE", 1)
    # Seoul crosses into January while UTC remains in December.
    current = datetime(2026, 12, 31, 14, 59, tzinfo=UTC)
    adapter = _adapter(adapter_id, lambda: current, backfill=backfill)
    batches = [[], []]
    batch = 0

    def reply(request):
        nonlocal current
        batches[batch].append(request.url.path.split("/")[-3:-1])
        current = datetime(2026, 12, 31, 15, 1, tzinfo=UTC)
        return httpx.Response(200, json={"StatisticSearch": {"list_total_count": 2, "row": [{}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        await adapter.fetcher(client)
        batch = 1
        await adapter.fetcher(client)

    for batch_index, windows in enumerate(batches):
        end = date(2026, 12, 31) if backfill or batch_index == 0 else date(2027, 1, 1)
        start = date(2018, 1, 1) if backfill else end - timedelta(days=45)
        assert windows == [[start.strftime("%Y%m%d"), end.strftime("%Y%m%d")]] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", ["mas_sora", "mas_rates"])
@pytest.mark.parametrize("backfill", [False, True])
async def test_mas_same_adapter_advances_requested_month_and_year(
    monkeypatch, adapter_id, backfill
):
    monkeypatch.setenv("SEICHE_CANONICAL_START", "2018-01-01")
    current = datetime(2026, 12, 31, 23, tzinfo=UTC)
    requests = []

    async def documents(client, **kwargs):
        requests.append((kwargs["start_year"], kwargs["end_year"], kwargs["end_month"]))
        return ()

    monkeypatch.setattr(official, "_mas_documents", documents)
    adapter = _adapter(adapter_id, lambda: current, backfill=backfill)
    await adapter.fetcher(None)
    current = datetime(2027, 1, 1, 1, tzinfo=UTC)
    await adapter.fetcher(None)
    assert requests == (
        [(2018, 2026, 12), (2018, 2026, 12)]
        if backfill
        else [(2026, 2026, 12), (2027, 2027, 1)]
    )
