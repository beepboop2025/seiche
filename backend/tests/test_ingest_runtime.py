from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from seiche import cli, ingest_runtime
from seiche.repository import LEGACY_SOURCE_WORKER_COMPONENT_ID


@pytest.mark.asyncio
async def test_collect_legacy_once_persists_only_a_safe_source_summary(
    monkeypatch,
) -> None:
    from seiche import assemble

    async def gather_sources():
        return (
            {"fred": object(), "nyfed_rates": object()},
            [
                {
                    "source": "fred",
                    "detail": (
                        "RuntimeError: https://user:private-password@example.test/"
                        "?access_token=private-token"
                    ),
                },
                {
                    "source": "private_feed",
                    "detail": "ConnectError: Authorization: Bearer private-token",
                },
            ],
        )

    monkeypatch.setattr(assemble, "_gather_sources", gather_sources)
    started_at = datetime(2026, 8, 22, 10, tzinfo=UTC)
    finished_at = started_at + timedelta(seconds=12)
    times = iter((started_at, finished_at))
    writes = []

    summary = await ingest_runtime.collect_legacy_once(
        clock=lambda: next(times),
        summary_writer=lambda key, value: writes.append((key, value)),
    )

    assert summary["status"] == "DEGRADED"
    assert summary["counts"] == {
        "source_groups": 3,
        "successful": 1,
        "degraded": 1,
        "failed": 1,
        "faults": 2,
    }
    assert summary["sources"] == [
        {"source": "fred", "status": "DEGRADED"},
        {"source": "nyfed_rates", "status": "SUCCESS"},
        {"source": "private_feed", "status": "FAILED"},
    ]
    assert summary["faults"] == [
        {
            "source": "fred",
            "status": "FAILED",
            "category": "INTERNAL_ERROR",
            "detail": "collector failed",
            "market_id": "GLOBAL",
        },
        {
            "source": "private_feed",
            "status": "FAILED",
            "category": "TRANSPORT_ERROR",
            "detail": "official source connection failed",
            "market_id": "GLOBAL",
        },
    ]
    assert writes == [(ingest_runtime.LEGACY_SOURCE_SUMMARY_BLOB_KEY, summary)]
    encoded = json.dumps(summary)
    assert "private-password" not in encoded
    assert "private-token" not in encoded
    assert "example.test" not in encoded


@pytest.mark.asyncio
async def test_collect_legacy_once_persists_a_safe_total_failure(monkeypatch) -> None:
    from seiche import assemble

    async def gather_sources():
        raise RuntimeError(
            "postgresql://private-user:private-password@db/seiche?token=private"
        )

    monkeypatch.setattr(assemble, "_gather_sources", gather_sources)
    started_at = datetime(2026, 8, 22, 10, tzinfo=UTC)
    finished_at = started_at + timedelta(seconds=3)
    times = iter((started_at, finished_at))
    writes = []

    with pytest.raises(ingest_runtime.LegacySourceSweepError) as caught:
        await ingest_runtime.collect_legacy_once(
            clock=lambda: next(times),
            summary_writer=lambda key, value: writes.append((key, value)),
        )

    summary = caught.value.summary
    assert str(caught.value) == "legacy source sweep failed"
    assert summary["status"] == "FAILED"
    assert summary["faults"] == [
        {
            "source": LEGACY_SOURCE_WORKER_COMPONENT_ID,
            "status": "FAILED",
            "category": "INTERNAL_ERROR",
            "detail": "collector failed",
            "market_id": "GLOBAL",
        }
    ]
    assert writes == [(ingest_runtime.LEGACY_SOURCE_SUMMARY_BLOB_KEY, summary)]
    assert "private-password" not in json.dumps(summary)


@pytest.mark.asyncio
async def test_collect_legacy_once_uses_the_durable_blob_store(
    tmp_path, monkeypatch
) -> None:
    from seiche import assemble, store

    async def gather_sources():
        return {"fred": object()}, []

    monkeypatch.setattr(assemble, "_gather_sources", gather_sources)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "legacy-source-summary.sqlite")
    started_at = datetime(2026, 8, 22, 10, tzinfo=UTC)
    times = iter((started_at, started_at + timedelta(seconds=1)))

    summary = await ingest_runtime.collect_legacy_once(clock=lambda: next(times))

    assert store.load_blob(ingest_runtime.LEGACY_SOURCE_SUMMARY_BLOB_KEY) == summary


@pytest.mark.asyncio
async def test_heartbeat_uses_distinct_component_and_aware_deadline() -> None:
    calls = []

    class _Repository:
        def save_worker_heartbeat(self, **kwargs):
            calls.append(kwargs)

    heartbeat_at = datetime(2026, 8, 22, 10, 30, tzinfo=UTC)
    await ingest_runtime._write_worker_heartbeat(
        _Repository(),
        grace_seconds=125,
        clock=lambda: heartbeat_at,
    )

    assert calls == [
        {
            "component_id": LEGACY_SOURCE_WORKER_COMPONENT_ID,
            "heartbeat_at": heartbeat_at,
            "expected_by": heartbeat_at + timedelta(seconds=125),
        }
    ]
    assert calls[0]["expected_by"].utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_heartbeat_rejects_a_naive_clock() -> None:
    class _Repository:
        def save_worker_heartbeat(self, **_kwargs):
            raise AssertionError("naive heartbeat must not be persisted")

    with pytest.raises(ValueError, match="timezone-aware"):
        await ingest_runtime._write_worker_heartbeat(
            _Repository(),
            grace_seconds=120,
            clock=lambda: datetime(2026, 8, 22, 10, 30),
        )


@pytest.mark.asyncio
async def test_watchdog_continues_when_heartbeat_persistence_fails(
    monkeypatch, caplog
) -> None:
    notifications = []

    async def failed_heartbeat(*_args, **_kwargs):
        raise RuntimeError("Bearer private-heartbeat-token")

    class _CycleComplete(Exception):
        pass

    async def complete_cycle(seconds):
        assert seconds == 30
        raise _CycleComplete

    monkeypatch.setattr(ingest_runtime, "_write_worker_heartbeat", failed_heartbeat)
    monkeypatch.setattr(ingest_runtime, "_systemd_notify", notifications.append)
    monkeypatch.setattr(ingest_runtime.asyncio, "sleep", complete_cycle)

    with pytest.raises(_CycleComplete):
        await ingest_runtime._heartbeat_loop(
            object(),
            interval_seconds=30,
            grace_seconds=120,
        )

    assert notifications == ["WATCHDOG=1"]
    assert "private-heartbeat-token" not in caplog.text


@pytest.mark.asyncio
async def test_watchdog_continues_but_durable_heartbeat_pauses_after_failure(
    monkeypatch,
) -> None:
    writes = []
    notifications = []
    heartbeat_enabled = asyncio.Event()

    async def write_heartbeat(*_args, **_kwargs):
        writes.append("heartbeat")

    class _CycleComplete(Exception):
        pass

    async def complete_cycle(_seconds):
        raise _CycleComplete

    monkeypatch.setattr(ingest_runtime, "_write_worker_heartbeat", write_heartbeat)
    monkeypatch.setattr(ingest_runtime, "_systemd_notify", notifications.append)
    monkeypatch.setattr(ingest_runtime.asyncio, "sleep", complete_cycle)

    with pytest.raises(_CycleComplete):
        await ingest_runtime._heartbeat_loop(
            object(),
            interval_seconds=30,
            grace_seconds=120,
            heartbeat_enabled=heartbeat_enabled,
        )

    assert writes == []
    assert notifications == ["WATCHDOG=1"]


@pytest.mark.asyncio
async def test_worker_retries_total_failure_before_ready(monkeypatch, caplog) -> None:
    events = []
    attempts = 0
    heartbeat_started = asyncio.Event()

    async def collect_once():
        nonlocal attempts
        attempts += 1
        events.append(f"collect-{attempts}")
        if attempts == 1:
            raise RuntimeError("Bearer private-source-token")
        return {"status": "SUCCESS"}

    async def write_heartbeat(*_args, **_kwargs):
        events.append("durable-heartbeat")

    async def parked_heartbeat(*_args, **_kwargs):
        heartbeat_started.set()
        await asyncio.Event().wait()

    def notify(message):
        events.append(message)

    class _StopWorker(Exception):
        pass

    sleep_calls = 0

    async def finish_after_retry(seconds):
        nonlocal sleep_calls
        assert seconds == 5
        sleep_calls += 1
        events.append(f"sleep-{sleep_calls}")
        if sleep_calls == 2:
            await heartbeat_started.wait()
            raise _StopWorker

    monkeypatch.setattr(ingest_runtime, "collect_legacy_once", collect_once)
    monkeypatch.setattr(ingest_runtime, "_write_worker_heartbeat", write_heartbeat)
    monkeypatch.setattr(ingest_runtime, "_heartbeat_loop", parked_heartbeat)
    monkeypatch.setattr(ingest_runtime, "_systemd_notify", notify)
    monkeypatch.setattr(ingest_runtime.asyncio, "sleep", finish_after_retry)

    with pytest.raises(_StopWorker):
        await ingest_runtime.run_legacy_worker(
            poll_seconds=5,
            repository=object(),
        )

    assert events.count("READY=1") == 1
    assert events.index("collect-2") < events.index("durable-heartbeat")
    assert events.index("durable-heartbeat") < events.index("READY=1")
    assert "READY=1" not in events[: events.index("sleep-1") + 1]
    assert events[-1] == "STOPPING=1"
    assert "private-source-token" not in caplog.text


def test_source_collect_cli_prints_safe_failure(monkeypatch, capsys) -> None:
    summary = {
        "status": "FAILED",
        "faults": [{"category": "INTERNAL_ERROR", "detail": "collector failed"}],
    }

    async def failed_collect():
        raise ingest_runtime.LegacySourceSweepError(summary)

    monkeypatch.setattr(ingest_runtime, "collect_legacy_once", failed_collect)

    assert cli.cmd_source_collect(SimpleNamespace()) == 1
    assert json.loads(capsys.readouterr().out) == summary


def test_source_worker_cli_defaults_to_five_minutes(monkeypatch) -> None:
    received = []

    def run_worker(args):
        received.append(args.poll_seconds)
        return 0

    monkeypatch.setattr(cli, "cmd_source_worker", run_worker)
    monkeypatch.setattr(cli.sys, "argv", ["seiche", "source-worker"])

    with pytest.raises(SystemExit) as stopped:
        cli.main()

    assert stopped.value.code == 0
    assert received == [300]
