from __future__ import annotations

import asyncio
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from seiche import collectors as collectors_module
from seiche.collectors import (
    CollectorRunStatus,
    CollectorSupervisor,
    FileRawCaptureSink,
    ParquetPartitionSink,
)
from seiche.domain.observation import evidence_sha256
from seiche.sources.base import (
    ObservationBatch,
    RawCapture,
    SourcePolicyUnavailableError,
)


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
async def test_supervisor_revalidates_availability_inside_every_sink_write() -> None:
    now = datetime(2026, 8, 11, 10, tzinfo=UTC)
    events: list[str] = []
    payload = b'{"selected":true}'

    class CheckedAdapter:
        market_id = "HK-HKD"
        adapter_id = "hkma_official"

        def check_availability(self) -> None:
            events.append("availability")

        async def collect(self) -> ObservationBatch:
            events.append("collect")
            return ObservationBatch(
                market_id=self.market_id,
                adapter_id=self.adapter_id,
                captured_at=now,
                observations=(),
                raw_capture=RawCapture(
                    market_id=self.market_id,
                    adapter_id=self.adapter_id,
                    captured_at=now,
                    source_uri="https://source.example/selected",
                    media_type="application/json",
                    payload=payload,
                    evidence_hash=evidence_sha256(payload),
                ),
            )

    class OrderedSink:
        def __init__(self, label: str) -> None:
            self.label = label

        def write(self, _value) -> list[str]:
            events.append(self.label)
            return []

    def write_observations(_observations: tuple) -> int:
        events.append("observations")
        return 0

    supervisor = CollectorSupervisor(
        raw_sink=OrderedSink("raw"),
        normalized_sink=OrderedSink("normalized"),
        observation_writer=write_observations,
        sleep=_no_sleep,
    )
    supervisor.register(CheckedAdapter())

    runs = await supervisor.run_due(now=now)

    assert runs[0].status is CollectorRunStatus.SUCCESS
    assert events == [
        "availability",
        "collect",
        "availability",
        "raw",
        "availability",
        "normalized",
        "availability",
        "observations",
    ]


@pytest.mark.asyncio
async def test_supervisor_revocation_before_raw_sink_is_unavailable_and_writes_nothing() -> (
    None
):
    now = datetime(2026, 8, 11, 10, tzinfo=UTC)
    payload = b'{"selected":true}'
    checks = 0
    raw_sink = _RecordingSink()
    normalized_sink = _RecordingSink()
    observation_writes: list[tuple] = []

    class RevokedAdapter:
        market_id = "HK-HKD"
        adapter_id = "hkma_official"

        def check_availability(self) -> None:
            nonlocal checks
            checks += 1
            if checks > 1:
                raise SourcePolicyUnavailableError("approval revoked")

        async def collect(self) -> ObservationBatch:
            return ObservationBatch(
                market_id=self.market_id,
                adapter_id=self.adapter_id,
                captured_at=now,
                observations=(),
                raw_capture=RawCapture(
                    market_id=self.market_id,
                    adapter_id=self.adapter_id,
                    captured_at=now,
                    source_uri="https://source.example/selected",
                    media_type="application/json",
                    payload=payload,
                    evidence_hash=evidence_sha256(payload),
                ),
            )

    supervisor = CollectorSupervisor(
        raw_sink=raw_sink,
        normalized_sink=normalized_sink,
        observation_writer=lambda rows: observation_writes.append(rows) or len(rows),
        sleep=_no_sleep,
    )
    supervisor.register(RevokedAdapter())

    runs = await supervisor.run_due(now=now)

    assert runs[0].status is CollectorRunStatus.UNAVAILABLE
    assert runs[0].attempts == 1
    assert checks == 2
    assert raw_sink.calls == 0
    assert normalized_sink.calls == 0
    assert observation_writes == []


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


def test_parquet_parts_remain_group_readable_under_restrictive_umask(
    tmp_path,
) -> None:
    now = datetime(2026, 8, 31, 20, tzinfo=UTC)

    class _Observation:
        market_id = "IN-INR"
        knowledge_time = now
        event_time = now

        @staticmethod
        def to_record() -> dict[str, object]:
            return {"jurisdiction_codes": ("IN",), "value": "1"}

    batch = ObservationBatch(
        market_id="IN-INR",
        adapter_id="rbi_official",
        captured_at=now,
        observations=(_Observation(),),
    )
    previous_umask = os.umask(0o077)
    try:
        [output] = ParquetPartitionSink(tmp_path).write(batch)
    finally:
        os.umask(previous_umask)

    mode = stat.S_IMODE(Path(output).stat().st_mode)
    assert mode == 0o640
    assert mode & stat.S_IRGRP
    assert collectors_module.pd.read_parquet(output).iloc[0]["value"] == "1"


def _nyfed_startup_observations(adapter_id, now):
    from seiche import market_runtime
    from seiche.domain.observation import (
        ConnectorClassification,
        Observation,
        QualityState,
        RedistributionStatus,
        StalenessState,
    )
    from seiche.markets.registry import default_registry

    pack = default_registry().get("US-USD")
    observations = []
    for instrument_id in market_runtime._STARTUP_NYFED_INSTRUMENTS[
        ("US-USD", adapter_id)
    ]:
        spec = pack.instrument_map[instrument_id]
        observations.append(
            Observation(
                market_id="US-USD",
                monetary_area_id="US",
                jurisdiction_codes=("US",),
                currency="USD",
                instrument_id=instrument_id,
                semantic_role=spec.semantic_role,
                value=100,
                canonical_unit=spec.canonical_unit,
                rate_compounding=spec.rate_compounding,
                day_count=spec.day_count,
                event_time=now - timedelta(days=1),
                knowledge_time=now,
                source_publication_time=now,
                revision_id="startup-fixture-v1",
                source=adapter_id,
                evidence_hash=evidence_sha256(instrument_id),
                connector_classification=ConnectorClassification.OFFICIAL_OPEN,
                redistribution_status=RedistributionStatus.ALLOWED,
                quality=QualityState.VERIFIED,
                staleness=StalenessState.FRESH,
            )
        )
    return tuple(observations)


def _nyfed_restored_state(adapter_id, now, *, failures=0, open_until=None):
    return {
        "market_id": "US-USD",
        "adapter_id": adapter_id,
        "next_due": (now + timedelta(hours=14)).isoformat(),
        "consecutive_failures": failures,
        "circuit_open_until": open_until.isoformat() if open_until else None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_adapter", ["nyfed_rates", "nyfed_unsecured_rates"])
async def test_nyfed_startup_initializes_only_missing_group_and_keeps_durable_due(
    tmp_path, monkeypatch, missing_adapter
):
    from seiche import market_runtime, store
    from seiche.markets.registry import default_registry
    from seiche.repository import SQLiteMarketRepository

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "startup.sqlite")
    repo = SQLiteMarketRepository()
    now = datetime.now(UTC).replace(microsecond=0)
    other = (
        "nyfed_unsecured_rates" if missing_adapter == "nyfed_rates" else "nyfed_rates"
    )
    repo.save_observations(_nyfed_startup_observations(other, now))
    selected = market_runtime._startup_nyfed_refresh_keys(repo, now=now)
    assert selected == frozenset({("US-USD", missing_adapter)})
    observations = _nyfed_startup_observations(missing_adapter, now)

    class _ExpandedAdapter(_FakeAdapter):
        async def collect(self):
            self.calls += 1
            return ObservationBatch(self.market_id, self.adapter_id, now, observations)

    added = _ExpandedAdapter("US-USD", missing_adapter, now)
    untouched = _FakeAdapter("US-USD", other, now)
    normal = _FakeAdapter("US-USD", "fred_daily", now)
    completions = []
    original_export = market_runtime._export_completed_funding_run

    def record_export(run, *, repository):
        completions.append((run["adapter_id"], run["next_due"]))
        # Keep the original PR236 callback path; disable only its filesystem
        # destination for this synthetic observation fixture.
        return original_export(run, repository=repository)

    monkeypatch.delenv(market_runtime.EXPORT_DIRECTORY_ENV, raising=False)
    monkeypatch.setattr(market_runtime, "_export_completed_funding_run", record_export)
    supervisor = CollectorSupervisor(
        observation_writer=repo.save_observations,
        run_writer=market_runtime._completed_run_handler(
            repository=repo,
            registry=default_registry(),
            backfill=False,
            materialize=False,
            record_forward=False,
            published_snapshots={},
        ),
        restored_runs=[
            _nyfed_restored_state(adapter, now) for adapter in (missing_adapter, other)
        ],
    )
    for adapter in (added, untouched, normal):
        supervisor.register(adapter)
    runs = await supervisor.run_due(now=now, startup_due=selected)
    assert {run.adapter_id for run in runs} == {missing_adapter, "fred_daily"}
    assert added.calls == normal.calls == 1 and untouched.calls == 0
    expected_due = (now + timedelta(hours=14)).isoformat()
    run = next(run for run in runs if run.adapter_id == missing_adapter)
    assert run.status is CollectorRunStatus.SUCCESS and run.next_due == expected_due
    assert (missing_adapter, expected_due) in completions
    stored = {row["adapter_id"]: row for row in repo.load_collector_states("US-USD")}
    assert stored[missing_adapter]["next_due"] == expected_due
    assert await supervisor.run_due(now=now + timedelta(seconds=30)) == []
    # Reopen durable state: completed coverage survives process restarts.
    restarted = SQLiteMarketRepository()
    assert (
        market_runtime._startup_nyfed_refresh_keys(
            restarted, now=now + timedelta(days=30)
        )
        == frozenset()
    )


def test_nyfed_startup_coverage_queries_exactly_23_new_ids_with_one_row_pages():
    from types import SimpleNamespace
    from seiche import market_runtime
    from seiche.markets.registry import default_registry

    now = datetime.now(UTC)
    rows = {
        item.instrument_id: item
        for adapter in ("nyfed_rates", "nyfed_unsecured_rates")
        for item in _nyfed_startup_observations(adapter, now)
    }
    queries = []

    def page(market_id, knowledge_time, **kwargs):
        queries.append((market_id, knowledge_time, kwargs))
        assert market_id == "US-USD" and knowledge_time == now
        assert (
            kwargs["limit"] == 1
            and len(kwargs["instrument_ids"]) == len(kwargs["sources"]) == 1
        )
        assert kwargs["event_time"] == now
        assert kwargs["event_time_from"] == now - timedelta(days=45)
        row = rows[kwargs["instrument_ids"][0]]
        assert row.source == kwargs["sources"][0]
        return [row], None

    assert (
        market_runtime._startup_nyfed_refresh_keys(
            SimpleNamespace(load_observation_page=page), now=now
        )
        == frozenset()
    )
    assert len(queries) == 23
    queried = {query[2]["instrument_ids"][0] for query in queries}
    assert len(queried) == 23 and "US.NYFED.EFFR_MEDIAN" not in queried
    assert not any(item.startswith("US.NYFED.SOFR") for item in queried)
    pack = default_registry().get("US-USD")
    for (
        _,
        adapter_id,
    ), instrument_ids in market_runtime._STARTUP_NYFED_INSTRUMENTS.items():
        assert all(
            pack.instrument_map[instrument].source_adapter_id == adapter_id
            for instrument in instrument_ids
        )


def test_nyfed_unknown_coverage_never_requests_early_collection(caplog):
    from types import SimpleNamespace
    from seiche import market_runtime

    def broken(*args, **kwargs):
        raise RuntimeError("postgresql://private-user:private-password@host/database")

    assert (
        market_runtime._startup_nyfed_refresh_keys(
            SimpleNamespace(load_observation_page=broken), now=datetime.now(UTC)
        )
        == frozenset()
    )
    assert "private-password" not in caplog.text
    assert caplog.text.count("NY Fed startup coverage unavailable") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("open_circuit", [False, True])
async def test_nyfed_startup_preserves_failure_history_and_circuit(open_circuit):
    now = datetime.now(UTC).replace(microsecond=0)
    open_until = now + timedelta(hours=20) if open_circuit else None
    adapter = _FakeAdapter("US-USD", "nyfed_rates", now, RuntimeError("source offline"))
    published = []
    supervisor = CollectorSupervisor(
        restored_runs=[
            _nyfed_restored_state("nyfed_rates", now, failures=3, open_until=open_until)
        ],
        run_writer=lambda run: published.append(run),
        sleep=_no_sleep,
    )
    supervisor.register(adapter)
    [run] = await supervisor.run_due(
        now=now, startup_due=frozenset({("US-USD", "nyfed_rates")})
    )
    assert run.status is (
        CollectorRunStatus.CIRCUIT_OPEN if open_circuit else CollectorRunStatus.FAILED
    )
    assert run.consecutive_failures == (3 if open_circuit else 4)
    assert (adapter.calls == 0) is open_circuit
    assert run.next_due == max(now + timedelta(hours=14), open_until or now).isoformat()
    assert published[0]["next_due"] == run.next_due
    assert published[0]["consecutive_failures"] == run.consecutive_failures


@pytest.mark.asyncio
async def test_nyfed_startup_does_not_bypass_policy_or_duplicate_a_normally_due_run():
    now = datetime.now(UTC).replace(microsecond=0)

    class _DeniedAdapter(_FakeAdapter):
        def check_availability(self):
            raise SourcePolicyUnavailableError("no current rights")

    denied = _DeniedAdapter("US-USD", "nyfed_rates", now)
    normal = _FakeAdapter("US-USD", "nyfed_unsecured_rates", now)
    supervisor = CollectorSupervisor(observation_writer=lambda rows: len(rows))
    supervisor.register(denied)
    supervisor.register(normal)
    runs = await supervisor.run_due(
        now=now,
        startup_due=frozenset(
            {("US-USD", "nyfed_rates"), ("US-USD", "nyfed_unsecured_rates")}
        ),
    )
    assert len(runs) == 2 and denied.calls == 0 and normal.calls == 1
    assert (
        next(run for run in runs if run.adapter_id == "nyfed_rates").status
        is CollectorRunStatus.UNAVAILABLE
    )
    assert (
        next(run for run in runs if run.adapter_id == "nyfed_unsecured_rates").next_due
        == (now + timedelta(days=1)).isoformat()
    )


@pytest.mark.asyncio
async def test_worker_consumes_startup_selection_only_on_first_poll(monkeypatch):
    from types import SimpleNamespace
    from seiche import market_runtime

    selected = frozenset({("US-USD", "nyfed_rates")})
    calls = []
    coverage_checks = []

    class _Supervisor:
        async def run_due(self, *, now, startup_due=frozenset()):
            calls.append(startup_due)
            return []  # Failed/incomplete initialization cannot force every poll.

    def coverage(repo, *, now):
        coverage_checks.append(now)
        return selected

    class _StopWorker(Exception):
        pass

    async def sleep(seconds):
        if len(calls) == 3:
            raise _StopWorker

    monkeypatch.setattr(
        market_runtime, "build_supervisor", lambda **kwargs: _Supervisor()
    )
    monkeypatch.setattr(
        market_runtime, "_recover_completed_funding_export", lambda repo: {}
    )
    monkeypatch.setattr(market_runtime, "_startup_nyfed_refresh_keys", coverage)
    monkeypatch.setattr(market_runtime, "_systemd_notify", lambda message: None)
    monkeypatch.setattr(market_runtime.asyncio, "sleep", sleep)
    with pytest.raises(_StopWorker):
        await market_runtime.run_worker(repository=SimpleNamespace(), poll_seconds=5)
    assert calls == [selected, frozenset(), frozenset()]
    assert len(coverage_checks) == 1
