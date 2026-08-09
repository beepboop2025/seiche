from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

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
    await asyncio.wait_for(entered.wait(), timeout=1)
    await asyncio.wait_for(healthy_published.wait(), timeout=1)

    assert not cycle.done()
    release.set()
    runs = await asyncio.wait_for(cycle, timeout=1)
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
    await asyncio.wait_for(entered.wait(), timeout=1)
    cycle.cancel()

    with pytest.raises(asyncio.CancelledError):
        await cycle
    await asyncio.wait_for(reaped.wait(), timeout=1)


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
