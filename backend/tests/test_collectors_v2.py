from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from seiche import collectors as collectors_module
from seiche.collectors import (
    CollectorRunStatus,
    CollectorSupervisor,
    FileRawCaptureSink,
)
from seiche.domain.observation import evidence_sha256
from seiche.sources.base import ObservationBatch, RawCapture


@dataclass
class _FakeAdapter:
    market_id: str
    adapter_id: str
    captured_at: datetime
    failure: Exception | None = None
    calls: int = 0

    async def collect(self) -> ObservationBatch:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return ObservationBatch(
            market_id=self.market_id,
            adapter_id=self.adapter_id,
            captured_at=self.captured_at,
            observations=(),
        )


async def _no_sleep(_: float) -> None:
    return None


class _HTTPAdapter:
    market_id = "HK-HKD"
    adapter_id = "hkma_official"

    def __init__(self, client: httpx.AsyncClient, captured_at: datetime) -> None:
        self.client = client
        self.captured_at = captured_at

    async def collect(self) -> ObservationBatch:
        response = await self.client.get("https://source.example/hkma")
        response.raise_for_status()
        payload = response.content
        return ObservationBatch(
            market_id=self.market_id,
            adapter_id=self.adapter_id,
            captured_at=self.captured_at,
            observations=(),
            raw_capture=RawCapture(
                market_id=self.market_id,
                adapter_id=self.adapter_id,
                captured_at=self.captured_at,
                source_uri=str(response.url),
                media_type="application/json",
                payload=payload,
                evidence_hash=evidence_sha256(payload),
            ),
        )


class _RecordingSink:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls = 0

    def write(self, _value) -> list[str]:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise OSError("temporary sink failure")
        return []


@pytest.mark.asyncio
async def test_collector_failure_is_isolated_by_market_and_source() -> None:
    now = datetime(2026, 8, 9, 10, tzinfo=UTC)
    healthy = _FakeAdapter("US-USD", "fred_daily", now)
    broken = _FakeAdapter("JP-JPY", "boj_rates", now, RuntimeError("upstream down"))
    written = []
    recorded = []

    def writer(observations: tuple) -> int:
        written.append(observations)
        return len(observations)

    supervisor = CollectorSupervisor(
        observation_writer=writer,
        run_writer=lambda run: recorded.append(run) or "run-id",
        sleep=_no_sleep,
    )
    supervisor.register(healthy)
    supervisor.register(broken)
    runs = await supervisor.run_due(now=now)
    statuses = {(run.market_id, run.adapter_id): run.status for run in runs}

    assert statuses[("US-USD", "fred_daily")] is CollectorRunStatus.SUCCESS
    assert statuses[("JP-JPY", "boj_rates")] is CollectorRunStatus.FAILED
    assert healthy.calls == 1
    assert broken.calls == 5  # initial attempt plus the pack-declared retries
    assert written == [()]
    assert {(item["market_id"], item["status"]) for item in recorded} == {
        ("US-USD", "SUCCESS"),
        ("JP-JPY", "FAILED"),
    }


@pytest.mark.parametrize("failure_stage", ("raw", "normalized", "writer"))
@pytest.mark.asyncio
async def test_persistence_retry_reuses_batch_without_another_source_http_request(
    failure_stage: str,
) -> None:
    now = datetime(2026, 8, 11, 10, tzinfo=UTC)
    requests: list[httpx.Request] = []
    delays: list[float] = []
    writer_calls = 0
    raw_sink = _RecordingSink(fail_first=failure_stage == "raw")
    normalized_sink = _RecordingSink(fail_first=failure_stage == "normalized")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"result": {"records": []}})

    def writer(_observations: tuple) -> int:
        nonlocal writer_calls
        writer_calls += 1
        if failure_stage == "writer" and writer_calls == 1:
            raise OSError("temporary observation writer failure")
        return 0

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        supervisor = CollectorSupervisor(
            raw_sink=raw_sink,
            normalized_sink=normalized_sink,
            observation_writer=writer,
            sleep=record_sleep,
            persistence_retry_limit=1,
            persistence_backoff_seconds=0.25,
        )
        supervisor.register(_HTTPAdapter(client, now))
        runs = await supervisor.run_due(now=now)

    assert len(requests) == 1
    assert raw_sink.calls == (2 if failure_stage == "raw" else 1)
    assert normalized_sink.calls == (2 if failure_stage == "normalized" else 1)
    assert writer_calls == (2 if failure_stage == "writer" else 1)
    assert delays == [0.25]
    assert runs[0].status is CollectorRunStatus.SUCCESS
    assert runs[0].attempts == 1


@pytest.mark.asyncio
async def test_persistence_retry_exhaustion_does_not_refetch_source() -> None:
    now = datetime(2026, 8, 11, 10, tzinfo=UTC)
    requests: list[httpx.Request] = []
    writer_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"result": {"records": []}})

    def broken_writer(_observations: tuple) -> int:
        nonlocal writer_calls
        writer_calls += 1
        raise OSError("observation store remains unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        supervisor = CollectorSupervisor(
            observation_writer=broken_writer,
            sleep=_no_sleep,
            persistence_retry_limit=2,
        )
        supervisor.register(_HTTPAdapter(client, now))
        runs = await supervisor.run_due(now=now)

    assert len(requests) == 1
    assert writer_calls == 3
    assert runs[0].status is CollectorRunStatus.FAILED
    assert runs[0].attempts == 1
    assert runs[0].fault == "PERSISTENCE_ERROR: collector persistence failed"


@pytest.mark.asyncio
async def test_deterministic_persistence_error_is_not_retried() -> None:
    now = datetime(2026, 8, 11, 10, tzinfo=UTC)
    requests: list[httpx.Request] = []
    delays: list[float] = []
    writer_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"result": {"records": []}})

    def invalid_writer(_observations: tuple) -> int:
        nonlocal writer_calls
        writer_calls += 1
        raise ValueError("observation payload violates schema")

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        supervisor = CollectorSupervisor(
            observation_writer=invalid_writer,
            sleep=record_sleep,
        )
        supervisor.register(_HTTPAdapter(client, now))
        runs = await supervisor.run_due(now=now)

    assert len(requests) == 1
    assert writer_calls == 1
    assert delays == []
    assert runs[0].status is CollectorRunStatus.FAILED
    assert runs[0].fault == "PERSISTENCE_ERROR: collector persistence failed"


@pytest.mark.asyncio
async def test_completed_run_is_published_before_slow_sibling_finishes() -> None:
    now = datetime(2026, 8, 9, 10, tzinfo=UTC)
    entered = asyncio.Event()
    release = asyncio.Event()
    healthy_published = asyncio.Event()
    loop = asyncio.get_running_loop()

    class _BlockedJapaneseAdapter:
        market_id = "JP-JPY"
        adapter_id = "boj_rates"

        async def collect(self) -> ObservationBatch:
            entered.set()
            await release.wait()
            raise RuntimeError("upstream remains down")

    def publish(run: dict) -> str:
        if run["market_id"] == "US-USD":
            loop.call_soon_threadsafe(healthy_published.set)
        return "run-id"

    supervisor = CollectorSupervisor(run_writer=publish, sleep=_no_sleep)
    supervisor.register(_BlockedJapaneseAdapter())
    supervisor.register(_FakeAdapter("US-USD", "fred_daily", now))
    cycle = asyncio.create_task(supervisor.run_due(now=now))
    await asyncio.wait_for(entered.wait(), timeout=15)
    await asyncio.wait_for(healthy_published.wait(), timeout=15)

    assert not cycle.done()
    release.set()
    runs = await asyncio.wait_for(cycle, timeout=15)
    assert {run.status for run in runs} == {
        CollectorRunStatus.SUCCESS,
        CollectorRunStatus.FAILED,
    }


@pytest.mark.asyncio
async def test_cancelled_cycle_reaps_pending_collector_tasks() -> None:
    now = datetime(2026, 8, 9, 10, tzinfo=UTC)
    entered = asyncio.Event()
    reaped = asyncio.Event()

    class _PendingAdapter:
        market_id = "JP-JPY"
        adapter_id = "boj_rates"

        async def collect(self) -> ObservationBatch:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                reaped.set()

    supervisor = CollectorSupervisor(sleep=_no_sleep)
    supervisor.register(_PendingAdapter())
    cycle = asyncio.create_task(supervisor.run_due(now=now))
    await asyncio.wait_for(entered.wait(), timeout=15)
    cycle.cancel()

    with pytest.raises(asyncio.CancelledError):
        await cycle
    await asyncio.wait_for(reaped.wait(), timeout=15)


@pytest.mark.asyncio
async def test_source_schedule_does_not_run_before_its_own_cadence() -> None:
    now = datetime(2026, 8, 9, 10, tzinfo=UTC)
    adapter = _FakeAdapter("US-USD", "fred_daily", now)
    supervisor = CollectorSupervisor(observation_writer=lambda rows: len(rows))
    supervisor.register(adapter)

    assert len(await supervisor.run_due(now=now)) == 1
    assert await supervisor.run_due(now=now + timedelta(hours=23)) == []
    assert len(await supervisor.run_due(now=now + timedelta(days=1))) == 1


@pytest.mark.asyncio
async def test_repeated_failure_opens_only_that_source_circuit() -> None:
    now = datetime(2026, 8, 9, 10, tzinfo=UTC)
    broken = _FakeAdapter("JP-JPY", "boj_rates", now, RuntimeError("upstream down"))
    healthy = _FakeAdapter("US-USD", "fred_daily", now)
    supervisor = CollectorSupervisor(
        observation_writer=lambda rows: len(rows),
        sleep=_no_sleep,
    )
    supervisor.register(broken)
    supervisor.register(healthy)

    for _ in range(5):
        runs = await supervisor.run_due(now=now, force=True)
        assert any(run.status is CollectorRunStatus.SUCCESS for run in runs)
    final = await supervisor.run_due(now=now, force=True)
    by_market = {run.market_id: run for run in final}

    assert by_market["JP-JPY"].status is CollectorRunStatus.CIRCUIT_OPEN
    assert by_market["US-USD"].status is CollectorRunStatus.SUCCESS


def test_raw_captures_are_content_addressed_and_immutable(tmp_path) -> None:
    payload = b'{"value": 1}'
    capture = RawCapture(
        market_id="US-USD",
        adapter_id="fred_daily",
        captured_at=datetime(2026, 8, 9, 10, tzinfo=UTC),
        source_uri="https://example.invalid/source",
        media_type="application/json",
        payload=payload,
        evidence_hash=evidence_sha256(payload),
    )
    sink = FileRawCaptureSink(tmp_path)
    first = sink.write(capture)
    second = sink.write(capture)

    assert first == second
    assert open(first, "rb").read() == payload
    assert "market=US-USD/source=fred_daily/date=2026-08-09" in first


def test_raw_capture_publication_is_atomic_and_concurrency_safe(
    tmp_path,
    monkeypatch,
) -> None:
    payload = b'{"value": "complete evidence"}'
    capture = RawCapture(
        market_id="HK-HKD",
        adapter_id="hkma_official",
        captured_at=datetime(2026, 8, 11, 10, tzinfo=UTC),
        source_uri="https://api.hkma.gov.hk/example",
        media_type="application/json",
        payload=payload,
        evidence_hash=evidence_sha256(payload),
    )
    sink = FileRawCaptureSink(tmp_path)
    real_link = collectors_module.os.link
    link_calls = 0

    def fail_first_link(source, target) -> None:
        nonlocal link_calls
        link_calls += 1
        if link_calls == 1:
            raise OSError("temporary filesystem publication failure")
        real_link(source, target)

    monkeypatch.setattr(collectors_module.os, "link", fail_first_link)
    with pytest.raises(OSError, match="temporary filesystem"):
        sink.write(capture)

    assert list(tmp_path.rglob("*.tmp")) == []
    assert list(tmp_path.rglob(f"{capture.evidence_hash}.json")) == []

    monkeypatch.setattr(collectors_module.os, "link", real_link)
    with ThreadPoolExecutor(max_workers=2) as executor:
        paths = list(executor.map(lambda _: sink.write(capture), range(2)))

    assert paths[0] == paths[1]
    assert open(paths[0], "rb").read() == payload
    assert list(tmp_path.rglob("*.tmp")) == []
