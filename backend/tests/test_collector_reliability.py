from __future__ import annotations

import asyncio
import socket
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import Response

from seiche import api, assemble, market_runtime, store
from seiche.collectors import CollectorRun, CollectorRunStatus, CollectorSupervisor
from seiche.markets.registry import default_registry
from seiche.repository import (
    COLLECTOR_WORKER_COMPONENT_ID,
    _POSTGRES_SCHEMA,
    SQLiteMarketRepository,
)
from seiche.sources.base import ObservationBatch
from seiche.sources.canonical import (
    FetchedDocument,
    FunctionalCanonicalAdapter,
    ParsedPoint,
    get_documents,
)


async def _no_sleep(_seconds: float) -> None:
    return None


@dataclass
class _Adapter:
    failure: Exception | None = None
    calls: int = 0
    market_id: str = "US-USD"
    adapter_id: str = "fred_daily"

    async def collect(self) -> ObservationBatch:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        now = datetime(2026, 8, 14, 10, tzinfo=UTC)
        return ObservationBatch(self.market_id, self.adapter_id, now, ())


def _run_state(
    now: datetime,
    *,
    next_due: datetime,
    consecutive_failures: int = 0,
    circuit_open_until: datetime | None = None,
) -> dict:
    return {
        "market_id": "US-USD",
        "adapter_id": "fred_daily",
        "status": "FAILED" if consecutive_failures else "SUCCESS",
        "started_at": now.isoformat(),
        "finished_at": now.isoformat(),
        "observations_written": 0,
        "attempts": 1,
        "next_due": next_due.isoformat(),
        "fault": "source unavailable" if consecutive_failures else None,
        "consecutive_failures": consecutive_failures,
        "circuit_open_until": (
            circuit_open_until.isoformat()
            if circuit_open_until is not None
            else None
        ),
    }


@pytest.mark.asyncio
async def test_latest_run_restores_schedule_and_circuit_across_supervisors(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "collector-state.sqlite")
    repository = SQLiteMarketRepository()
    now = datetime(2026, 8, 14, 10, tzinfo=UTC)
    repository.save_collector_run(
        _run_state(
            now,
            next_due=now,
            consecutive_failures=4,
        )
    )
    broken = _Adapter(RuntimeError("upstream remains unavailable"))
    first = CollectorSupervisor(
        observation_writer=repository.save_observations,
        run_writer=repository.save_collector_run,
        restored_runs=repository.load_collector_states(),
        sleep=_no_sleep,
    )
    first.register(broken)

    failed = (await first.run_due(now=now, force=True))[0]

    assert failed.status is CollectorRunStatus.FAILED
    assert failed.consecutive_failures == 5
    assert failed.circuit_open_until == (now + timedelta(minutes=15)).isoformat()
    assert failed.next_due == (now + timedelta(days=1)).isoformat()
    restarted_adapter = _Adapter(RuntimeError("must not be called"))
    restarted = CollectorSupervisor(
        observation_writer=repository.save_observations,
        restored_runs=repository.load_collector_states(),
        sleep=_no_sleep,
    )
    restarted.register(restarted_adapter)

    circuit = (await restarted.run_due(now=now + timedelta(seconds=1), force=True))[0]

    assert circuit.status is CollectorRunStatus.CIRCUIT_OPEN
    assert circuit.consecutive_failures == 5
    assert restarted_adapter.calls == 0


def test_legacy_run_history_fills_only_missing_durable_scheduler_state() -> None:
    now = datetime(2026, 8, 14, 10, tzinfo=UTC)
    legacy = _run_state(now, next_due=now + timedelta(days=1))
    durable = {
        **legacy,
        "next_due": (now + timedelta(days=2)).isoformat(),
        "consecutive_failures": 3,
    }

    class _Repository:
        def latest_collector_runs(self):
            return [legacy, {**legacy, "adapter_id": "fred_weekly"}]

        def load_collector_states(self):
            return [durable]

    restored = market_runtime._load_restored_collector_states(_Repository())

    assert [(item["adapter_id"], item["next_due"]) for item in restored] == [
        ("fred_daily", durable["next_due"]),
        ("fred_weekly", legacy["next_due"]),
    ]


@pytest.mark.asyncio
async def test_restored_next_due_prevents_restart_request_burst() -> None:
    now = datetime(2026, 8, 14, 10, tzinfo=UTC)
    adapter = _Adapter()
    supervisor = CollectorSupervisor(
        restored_runs=[_run_state(now, next_due=now + timedelta(days=1))]
    )
    supervisor.register(adapter)

    assert await supervisor.run_due(now=now) == []
    assert adapter.calls == 0
    assert len(await supervisor.run_due(now=now + timedelta(days=1))) == 1
    assert adapter.calls == 1


def test_sqlite_additively_migrates_the_previous_collector_run_schema(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "legacy-collector.sqlite"
    monkeypatch.setattr(store, "DB_PATH", database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE collector_runs (
                 run_id TEXT PRIMARY KEY,
                 market_id TEXT NOT NULL,
                 adapter_id TEXT NOT NULL,
                 status TEXT NOT NULL,
                 started_at TEXT NOT NULL,
                 finished_at TEXT NOT NULL,
                 observations_written INTEGER NOT NULL,
                 attempts INTEGER NOT NULL,
                 next_due TEXT NOT NULL,
                 fault TEXT)"""
        )
    repository = SQLiteMarketRepository()

    assert repository.latest_collector_runs() == []
    with sqlite3.connect(database) as connection:
        state_table = connection.execute(
            """SELECT name FROM sqlite_master
                 WHERE type='table' AND name='collector_states'"""
        ).fetchone()
    assert state_table == ("collector_states",)


def test_scheduler_state_does_not_change_the_append_only_run_identity(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "run-identity.sqlite"
    monkeypatch.setattr(store, "DB_PATH", database)
    repository = SQLiteMarketRepository()
    now = datetime(2026, 8, 14, 10, tzinfo=UTC)
    stateful = _run_state(
        now,
        next_due=now + timedelta(minutes=15),
        consecutive_failures=5,
        circuit_open_until=now + timedelta(minutes=15),
    )
    legacy = {
        key: value
        for key, value in stateful.items()
        if key not in {"consecutive_failures", "circuit_open_until"}
    }

    assert repository.save_collector_run(legacy) == repository.save_collector_run(
        stateful
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM collector_runs").fetchone() == (
            1,
        )
    assert repository.load_collector_states()[0]["consecutive_failures"] == 5


def test_replayed_older_run_cannot_regress_durable_scheduler_state(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "monotonic-state.sqlite")
    repository = SQLiteMarketRepository()
    older = datetime(2026, 8, 14, 10, tzinfo=UTC)
    newer = older + timedelta(minutes=5)
    open_until = newer + timedelta(minutes=15)
    repository.save_collector_run(
        _run_state(
            newer,
            next_due=open_until,
            consecutive_failures=5,
            circuit_open_until=open_until,
        )
    )

    repository.save_collector_run(
        _run_state(
            older,
            next_due=older + timedelta(days=1),
            consecutive_failures=1,
        )
    )

    assert repository.load_collector_states() == [
        {
            "market_id": "US-USD",
            "adapter_id": "fred_daily",
            "next_due": open_until.isoformat(),
            "consecutive_failures": 5,
            "circuit_open_until": open_until.isoformat(),
            "updated_at": newer.isoformat(),
        }
    ]


def test_postgres_schema_has_durable_scheduler_and_heartbeat_state() -> None:
    assert "CREATE TABLE IF NOT EXISTS collector_states" in _POSTGRES_SCHEMA
    assert "CREATE TABLE IF NOT EXISTS worker_heartbeats" in _POSTGRES_SCHEMA


def test_worker_heartbeat_is_monotonic_and_public_fault_is_sanitized(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "heartbeat.sqlite")
    repository = SQLiteMarketRepository()
    newest = datetime(2026, 8, 14, 10, tzinfo=UTC)
    repository.save_worker_heartbeat(
        component_id=COLLECTOR_WORKER_COMPONENT_ID,
        heartbeat_at=newest,
        expected_by=newest + timedelta(minutes=2),
    )
    repository.save_worker_heartbeat(
        component_id=COLLECTOR_WORKER_COMPONENT_ID,
        heartbeat_at=newest - timedelta(minutes=1),
        expected_by=newest,
    )

    stored = repository.load_worker_heartbeat(COLLECTOR_WORKER_COMPONENT_ID)
    assert stored == {
        "component_id": COLLECTOR_WORKER_COMPONENT_ID,
        "heartbeat_at": newest.isoformat(),
        "expected_by": (newest + timedelta(minutes=2)).isoformat(),
    }
    assert api._collector_worker_fault(now=newest, repository=repository) is None
    fault = api._collector_worker_fault(
        now=newest + timedelta(minutes=3), repository=repository
    )
    assert fault == {
        "source": COLLECTOR_WORKER_COMPONENT_ID,
        "status": "OVERDUE",
        "detail": "official collector worker heartbeat is overdue",
        "heartbeat_at": newest.isoformat(),
        "expected_by": (newest + timedelta(minutes=2)).isoformat(),
    }

    class _PrivateFailure:
        def load_worker_heartbeat(self, _component_id):
            raise RuntimeError("postgresql://private-user:private-password@db/seiche")

    redacted = api._collector_worker_fault(now=newest, repository=_PrivateFailure())
    assert redacted["status"] == "UNKNOWN"
    assert "private" not in str(redacted)


def test_public_health_adds_overdue_worker_without_mutating_snapshot(
    monkeypatch,
) -> None:
    snapshot = {
        "generated_at": "2026-08-14T10:00:00+00:00",
        "version": "test",
        "faults": [],
        "provenance": {},
    }
    monkeypatch.setenv("SEICHE_COLLECTOR_HEARTBEAT_REQUIRED", "1")
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: snapshot)
    monkeypatch.setattr(
        api,
        "_collector_worker_fault",
        lambda: {
            "source": COLLECTOR_WORKER_COMPONENT_ID,
            "status": "OVERDUE",
            "detail": "official collector worker heartbeat is overdue",
        },
    )

    payload = api._health_response(
        Response(),
        require_rebuilt=False,
        include_release_candidate=False,
    )

    assert payload["faults"][0]["status"] == "OVERDUE"
    assert snapshot["faults"] == []


@pytest.mark.asyncio
async def test_nonempty_official_document_with_zero_parsed_rows_fails() -> None:
    document = FetchedDocument(
        "https://example.invalid/official.csv",
        "text/csv",
        b"DATE,VALUE\n",
    )

    async def fetcher(_client):
        return (document,)

    adapter = FunctionalCanonicalAdapter(
        pack=default_registry().get("US-USD"),
        adapter_id="fred_daily",
        source="official-test",
        fetcher=fetcher,
        parser=lambda _document: (),
    )

    with pytest.raises(ValueError, match="parsed zero declared observations"):
        await adapter.collect()


@pytest.mark.asyncio
async def test_rows_filtered_by_point_in_time_rules_cannot_report_success(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "future-row.sqlite")
    capture = datetime(2026, 8, 14, 10, tzinfo=UTC)
    document = FetchedDocument(
        "https://example.invalid/official.csv",
        "text/csv",
        b"DATE,VALUE\n2026-08-15,5.31\n",
    )

    async def fetcher(_client):
        return (document,)

    def parser(_document):
        return (
            ParsedPoint(
                "US.NYFED.SOFR",
                date(2026, 8, 15),
                "5.31",
                b"2026-08-15,5.31",
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
    )

    with pytest.raises(ValueError, match="zero usable observations"):
        await adapter.collect()


@pytest.mark.asyncio
async def test_adapter_deadline_cancels_a_hung_collector() -> None:
    cancelled = asyncio.Event()

    class _HungAdapter:
        market_id = "US-USD"
        adapter_id = "fred_daily"

        async def collect(self) -> ObservationBatch:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    supervisor = CollectorSupervisor(
        adapter_deadline_seconds=0.02,
        sleep=_no_sleep,
    )
    supervisor.register(_HungAdapter())

    run = (
        await supervisor.run_due(now=datetime(2026, 8, 14, 10, tzinfo=UTC))
    )[0]

    assert run.status is CollectorRunStatus.FAILED
    assert run.attempts == 1
    assert "AdapterDeadlineExceeded" in str(run.fault)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_document_fetches_are_bounded_and_return_in_declared_order() -> None:
    class _Response:
        def __init__(self, uri: str) -> None:
            self.url = uri
            self.headers = {"content-type": "application/json; charset=utf-8"}
            self.content = uri.rsplit("/", 1)[-1].encode()

        def raise_for_status(self) -> None:
            return None

    class _Client:
        active = 0
        max_active = 0

        async def get(self, uri: str, *, params=None):
            del params
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                index = int(uri.rsplit("/", 1)[-1])
                await asyncio.sleep(0.01 if index % 2 == 0 else 0)
                return _Response(uri)
            finally:
                self.active -= 1

    client = _Client()
    declarations = tuple(
        (f"document-{index}", f"https://example.invalid/{index}", None)
        for index in range(6)
    )

    documents = await get_documents(client, declarations, max_concurrency=2)

    assert client.max_active == 2
    assert [document.label for document in documents] == [
        f"document-{index}" for index in range(6)
    ]
    assert all(document.media_type == "application/json" for document in documents)


def test_market_worker_unit_enables_systemd_watchdog() -> None:
    unit = (
        Path(__file__).parents[2]
        / "ops"
        / "deploy"
        / "seiche-market-worker.service"
    ).read_text()

    assert "Type=notify" in unit
    assert "NotifyAccess=main" in unit
    assert "WatchdogSec=180" in unit


def test_worker_can_notify_systemd_over_its_unix_datagram_socket(monkeypatch) -> None:
    # Darwin's sockaddr_un path limit is shorter than pytest's nested tmp path.
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        notify_socket = Path(directory) / "notify.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as listener:
            listener.bind(str(notify_socket))
            listener.settimeout(1)
            monkeypatch.setenv("NOTIFY_SOCKET", str(notify_socket))

            market_runtime._systemd_notify("READY=1")

            assert listener.recv(64) == b"READY=1"


@pytest.mark.asyncio
async def test_degraded_worker_heartbeat_keeps_systemd_watchdog_alive(
    monkeypatch,
) -> None:
    heartbeat_enabled = asyncio.Event()
    heartbeat_write_lock = asyncio.Lock()
    writes = []
    notifications = []

    async def write_heartbeat(*_args, **_kwargs):
        writes.append("heartbeat")

    class _CycleComplete(Exception):
        pass

    async def complete_cycle(_seconds):
        raise _CycleComplete

    monkeypatch.setattr(market_runtime, "_write_worker_heartbeat", write_heartbeat)
    monkeypatch.setattr(market_runtime, "_systemd_notify", notifications.append)
    monkeypatch.setattr(market_runtime.asyncio, "sleep", complete_cycle)

    with pytest.raises(_CycleComplete):
        await market_runtime._worker_heartbeat_loop(
            object(),
            interval_seconds=30,
            grace_seconds=120,
            heartbeat_enabled=heartbeat_enabled,
            heartbeat_write_lock=heartbeat_write_lock,
        )

    assert writes == []
    assert notifications == ["WATCHDOG=1"]

    heartbeat_enabled.set()
    with pytest.raises(_CycleComplete):
        await market_runtime._worker_heartbeat_loop(
            object(),
            interval_seconds=30,
            grace_seconds=120,
            heartbeat_enabled=heartbeat_enabled,
            heartbeat_write_lock=heartbeat_write_lock,
        )

    assert writes == ["heartbeat"]
    assert notifications == ["WATCHDOG=1", "WATCHDOG=1"]


@pytest.mark.asyncio
async def test_worker_carries_materialization_fault_until_retry_recovers(
    tmp_path, monkeypatch, caplog
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "worker-degradation.sqlite")
    monkeypatch.setenv("SEICHE_COLLECTOR_HEARTBEAT_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("SEICHE_COLLECTOR_HEARTBEAT_GRACE_SECONDS", "10")
    repository = SQLiteMarketRepository()
    started_at = datetime(2026, 8, 14, 10, tzinfo=UTC)

    def completed_run(market_id: str, adapter_id: str) -> CollectorRun:
        return CollectorRun(
            market_id,
            adapter_id,
            CollectorRunStatus.SUCCESS,
            started_at.isoformat(),
            started_at.isoformat(),
            1,
            1,
            (started_at + timedelta(days=1)).isoformat(),
        )

    cycles = [
        [
            completed_run("IN-INR", "rbi_official"),
            completed_run("JP-JPY", "boj_rates"),
        ],
        [],
        [completed_run("US-USD", "fred_daily")],
        [completed_run("US-USD", "fred_daily")],
    ]

    class _Supervisor:
        async def run_due(self, *, now):
            del now
            return cycles.pop(0)

    market_attempts = []
    global_attempts = []
    india_attempts = 0

    def materialize_market(market_id, **_kwargs):
        nonlocal india_attempts
        market_attempts.append(market_id)
        if market_id == "IN-INR":
            india_attempts += 1
            if india_attempts < 3:
                raise ValueError(
                    "forward chain has no single valid head; "
                    "postgresql://private-user:private-password@db/seiche"
                )
        return {"gauge": f"{market_id}-healthy"}

    def materialize_global(**_kwargs):
        global_attempts.append("GLOBAL")
        return {"tide": "healthy"}

    heartbeat_state: dict[str, asyncio.Event] = {}
    heartbeat_loop_started = asyncio.Event()

    async def parked_heartbeat_loop(
        _repository,
        *,
        interval_seconds,
        grace_seconds,
        heartbeat_enabled: asyncio.Event,
        heartbeat_write_lock: asyncio.Lock,
    ):
        del interval_seconds, grace_seconds, heartbeat_write_lock
        heartbeat_state["enabled"] = heartbeat_enabled
        heartbeat_loop_started.set()
        await asyncio.Event().wait()

    heartbeat_times = iter((started_at, started_at + timedelta(minutes=5)))
    written_heartbeats = []
    original_write_heartbeat = market_runtime._write_worker_heartbeat

    async def write_heartbeat(_repository, *, grace_seconds, clock=None):
        del clock
        heartbeat_at = next(heartbeat_times)
        written_heartbeats.append(heartbeat_at)
        await original_write_heartbeat(
            repository,
            grace_seconds=grace_seconds,
            clock=lambda: heartbeat_at,
        )

    notifications = []
    sleep_count = 0

    class _StopWorker(Exception):
        pass

    async def finish_cycle(_seconds):
        nonlocal sleep_count
        sleep_count += 1
        await heartbeat_loop_started.wait()
        heartbeat_enabled = heartbeat_state["enabled"]
        if sleep_count == 1:
            assert not heartbeat_enabled.is_set()
            assert written_heartbeats == [started_at]
            assert market_attempts == ["IN-INR", "JP-JPY"]
            assert global_attempts == ["GLOBAL"]
            stored = repository.load_worker_heartbeat(COLLECTOR_WORKER_COMPONENT_ID)
            assert stored["heartbeat_at"] == started_at.isoformat()
            fault = api._collector_worker_fault(
                now=started_at + timedelta(seconds=11),
                repository=repository,
            )
            assert fault["status"] == "OVERDUE"
        elif sleep_count == 2:
            assert not heartbeat_enabled.is_set()
            assert market_attempts == ["IN-INR", "JP-JPY"]
            assert written_heartbeats == [started_at]
        elif sleep_count == 3:
            assert not heartbeat_enabled.is_set()
            assert market_attempts.count("IN-INR") == 2
            assert written_heartbeats == [started_at]
            assert "STATUS=collector materialization healthy" not in notifications
        elif sleep_count == 4:
            assert heartbeat_enabled.is_set()
            assert market_attempts.count("IN-INR") == 3
            assert written_heartbeats == [
                started_at,
                started_at + timedelta(minutes=5),
            ]
            raise _StopWorker

    monkeypatch.setattr(
        market_runtime, "build_supervisor", lambda **_kwargs: _Supervisor()
    )
    monkeypatch.setattr(market_runtime, "materialize_market", materialize_market)
    monkeypatch.setattr(market_runtime, "materialize_global_tide", materialize_global)
    monkeypatch.setattr(
        market_runtime,
        "_export_usd_funding_core_after_runs",
        lambda *_args, **_kwargs: {"status": "DISABLED"},
    )
    monkeypatch.setattr(market_runtime, "_worker_heartbeat_loop", parked_heartbeat_loop)
    monkeypatch.setattr(market_runtime, "_write_worker_heartbeat", write_heartbeat)
    monkeypatch.setattr(market_runtime, "_systemd_notify", notifications.append)
    monkeypatch.setattr(market_runtime.asyncio, "sleep", finish_cycle)

    with pytest.raises(_StopWorker):
        await market_runtime.run_worker(
            poll_seconds=5,
            repository=repository,
        )

    assert cycles == []
    assert global_attempts == ["GLOBAL", "GLOBAL", "GLOBAL"]
    assert notifications == [
        "READY=1",
        "STATUS=collector materialization degraded; pending=IN-INR",
        "STATUS=collector materialization healthy",
        "STOPPING=1",
    ]
    assert "private-password" not in caplog.text
    assert "private-password" not in str(notifications)
