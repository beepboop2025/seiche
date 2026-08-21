"""Operator runtime for independent canonical collection and materialization."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from seiche.collectors import (
    CollectorRun,
    CollectorSupervisor,
    FileRawCaptureSink,
    ParquetPartitionSink,
)
from seiche.markets.materialize import materialize_global_tide, materialize_market
from seiche.markets.registry import MarketRegistry, default_registry
from seiche.markets.us_usd.funding_core import (
    EXPORT_DIRECTORY_ENV,
    FUNDING_CORE_PROFILE_ID,
    export_funding_core_input_pack,
)
from seiche.repository import (
    COLLECTOR_WORKER_COMPONENT_ID,
    MarketRepository,
    get_repository,
)
from seiche.sources.official import build_official_adapters

LOGGER = logging.getLogger(__name__)

DEFAULT_ADAPTER_DEADLINE_SECONDS = 300.0
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0
DEFAULT_HEARTBEAT_GRACE_SECONDS = 120.0

# Backfill markers certify an adapter's exact historical collection contract.
# Production may already hold the v3 marker written after the SOFR distribution
# lineage repair, so the SOFRAI 30/90/180-day averages and index require a new
# generation. After that expanded history imports successfully, v4 restores
# normal idempotency without deleting or reinterpreting older markers.
_BACKFILL_MARKER_GENERATIONS = {
    ("US-USD", "nyfed_rates"): "nyfed-sofrai-averages-index-v4",
}


def _storage_root(variable: str, fallback: str) -> Path:
    return Path(os.getenv(variable, fallback)).expanduser().resolve()


def _positive_seconds(variable: str, fallback: float) -> float:
    raw = os.getenv(variable, "").strip()
    value = float(raw) if raw else fallback
    if value <= 0:
        raise ValueError(f"{variable} must be positive")
    return value


def _systemd_notify(message: str) -> None:
    """Send readiness/watchdog state without adding a systemd dependency."""

    address = os.getenv("NOTIFY_SOCKET", "")
    if not address:
        return
    if address.startswith("@"):  # Linux abstract namespace notation.
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.connect(address)
        client.sendall(message.encode("utf-8"))


async def _write_worker_heartbeat(
    repository: MarketRepository,
    *,
    grace_seconds: float,
    clock: Callable[[], datetime] | None = None,
) -> None:
    heartbeat_at = (clock or (lambda: datetime.now(UTC)))()
    if heartbeat_at.tzinfo is None or heartbeat_at.utcoffset() is None:
        raise ValueError("worker heartbeat clock must be timezone-aware")
    heartbeat_at = heartbeat_at.astimezone(UTC).replace(microsecond=0)
    await asyncio.to_thread(
        repository.save_worker_heartbeat,
        component_id=COLLECTOR_WORKER_COMPONENT_ID,
        heartbeat_at=heartbeat_at,
        expected_by=heartbeat_at + timedelta(seconds=grace_seconds),
    )


async def _worker_heartbeat_loop(
    repository: MarketRepository,
    *,
    interval_seconds: float,
    grace_seconds: float,
    heartbeat_enabled: asyncio.Event | None = None,
    heartbeat_write_lock: asyncio.Lock | None = None,
) -> None:
    async def persist_if_enabled() -> None:
        if heartbeat_enabled is None or heartbeat_enabled.is_set():
            await _write_worker_heartbeat(
                repository,
                grace_seconds=grace_seconds,
            )

    while True:
        try:
            if heartbeat_write_lock is None:
                await persist_if_enabled()
            else:
                async with heartbeat_write_lock:
                    await persist_if_enabled()
        except Exception as exc:  # noqa: BLE001 - report, then retry
            LOGGER.error(
                "collector worker heartbeat persistence failed fault_type=%s",
                type(exc).__name__,
            )
        try:
            _systemd_notify("WATCHDOG=1")
        except Exception as exc:  # noqa: BLE001 - watchdog loop must survive
            LOGGER.error(
                "collector worker watchdog notification failed fault_type=%s",
                type(exc).__name__,
            )
        await asyncio.sleep(interval_seconds)


def _backfill_marker(market_id: str, adapter_id: str) -> Path | None:
    root = os.getenv("SEICHE_BACKFILL_STATE_DIR", "").strip()
    if not root:
        return None
    normalized_market = market_id.upper()
    generation = _BACKFILL_MARKER_GENERATIONS.get((normalized_market, adapter_id))
    suffix = f"--{generation}" if generation is not None else ""
    return (
        Path(root).expanduser().resolve()
        / f"{normalized_market}--{adapter_id}{suffix}.done"
    )


def _mark_backfill_complete(market_id: str, adapter_id: str) -> None:
    marker = _backfill_marker(market_id, adapter_id)
    if marker is not None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)


def _marker_requires_funding_core_export(market_id: str, adapter_id: str) -> bool:
    return (market_id.upper(), adapter_id) in _BACKFILL_MARKER_GENERATIONS


def _load_restored_collector_states(
    repository: MarketRepository,
) -> tuple[dict, ...]:
    """Bridge legacy run history into the durable scheduler-state table."""

    restored: dict[tuple[str, str], dict] = {}
    legacy_loader = getattr(repository, "latest_collector_runs", None)
    if callable(legacy_loader):
        for run in legacy_loader():
            key = (str(run["market_id"]).upper(), str(run["adapter_id"]))
            restored[key] = run
    state_loader = getattr(repository, "load_collector_states", None)
    if callable(state_loader):
        for state in state_loader():
            key = (str(state["market_id"]).upper(), str(state["adapter_id"]))
            # Explicit scheduler state wins. Run history fills only adapters
            # whose last outcome predates the additive scheduler-state table.
            restored[key] = state
    return tuple(restored[key] for key in sorted(restored))


def build_supervisor(
    *,
    repository: MarketRepository | None = None,
    registry: MarketRegistry | None = None,
    backfill: bool = False,
    market_ids: frozenset[str] | None = None,
    run_writer: Callable[[dict], object] | None = None,
) -> CollectorSupervisor:
    repo = repository or get_repository()
    markets = registry or default_registry()
    restored_states = _load_restored_collector_states(repo)
    supervisor = CollectorSupervisor(
        registry=markets,
        raw_sink=FileRawCaptureSink(
            _storage_root("SEICHE_RAW_CAPTURE_DIR", "/var/lib/seiche/raw")
        ),
        normalized_sink=ParquetPartitionSink(
            _storage_root("SEICHE_NORMALIZED_DIR", "/var/lib/seiche/normalized")
        ),
        observation_writer=repo.save_observations,
        run_writer=run_writer or repo.save_collector_run,
        adapter_deadline_seconds=_positive_seconds(
            "SEICHE_COLLECTOR_ADAPTER_DEADLINE_SECONDS",
            DEFAULT_ADAPTER_DEADLINE_SECONDS,
        ),
        restored_runs=restored_states,
    )
    selected = {item.upper() for item in market_ids} if market_ids else None
    for adapter in build_official_adapters(
        registry=markets,
        repository=repo,
        backfill=backfill,
    ):
        if selected is not None and adapter.market_id not in selected:
            continue
        marker = _backfill_marker(adapter.market_id, adapter.adapter_id)
        if backfill and marker is not None and marker.exists():
            continue
        supervisor.register(adapter)
    return supervisor


def _completed_run_handler(
    *,
    repository: MarketRepository,
    registry: MarketRegistry,
    backfill: bool,
    materialize: bool,
    record_forward: bool,
    published_snapshots: dict[str, object],
) -> Callable[[dict], object]:
    """Persist and publish one completed source without waiting for siblings."""

    def handle(run: dict) -> object:
        run_id = repository.save_collector_run(run)
        market_id = str(run["market_id"]).upper()
        publication_complete = not materialize or market_id == "US-USD"
        if materialize and market_id != "US-USD":
            # A later adapter for the same market makes the earlier per-cycle
            # snapshot stale. Remove it before attempting publication so a
            # failure cannot suppress the cycle-boundary retry or mark this
            # adapter's backfill complete against an older snapshot.
            published_snapshots.pop(market_id, None)
            try:
                published_snapshots[market_id] = materialize_market(
                    market_id,
                    repository=repository,
                    registry=registry,
                    knowledge_time=datetime.now(UTC).replace(microsecond=0),
                    record_forward=record_forward,
                )
                publication_complete = True
            except Exception as exc:  # noqa: BLE001 — retry at cycle boundary
                LOGGER.error(
                    "early materialization failed market_id=%s fault_type=%s",
                    market_id,
                    type(exc).__name__,
                )
        if (
            backfill
            and run["status"] == "SUCCESS"
            and publication_complete
            and not _marker_requires_funding_core_export(
                market_id, str(run["adapter_id"])
            )
        ):
            _mark_backfill_complete(market_id, str(run["adapter_id"]))
        return run_id

    return handle


def _materialize_after_runs(
    runs: list[CollectorRun],
    *,
    repository: MarketRepository,
    registry: MarketRegistry,
    cutoff: datetime,
    record_forward: bool,
    existing: dict[str, object] | None = None,
    faulted_markets: set[str] | None = None,
) -> dict[str, object]:
    """Materialize a completed cycle, optionally isolating worker faults.

    One-shot collection and backfill omit ``faulted_markets`` and retain their
    strict all-or-error contract.  The long-lived worker supplies its carried
    fault set so each market and the global tide can fail independently.  A
    carried market is retried only at a later nonempty collection boundary.
    """

    market_ids = {item.market_id for item in runs}
    if faulted_markets is not None:
        market_ids.update(faulted_markets - {"GLOBAL"})
    snapshots: dict[str, object] = dict(existing or {})
    for market_id in sorted(market_ids):
        # US v2 remains the pack-local compatibility materialization emitted
        # by assemble.py, preserving bit-identical v1 migration output.
        if market_id == "US-USD":
            continue
        if market_id in snapshots:
            if faulted_markets is not None:
                faulted_markets.discard(market_id)
            continue
        if faulted_markets is None:
            snapshots[market_id] = materialize_market(
                market_id,
                repository=repository,
                registry=registry,
                knowledge_time=cutoff,
                record_forward=record_forward,
            )
            continue
        try:
            snapshots[market_id] = materialize_market(
                market_id,
                repository=repository,
                registry=registry,
                knowledge_time=cutoff,
                record_forward=record_forward,
            )
        except Exception as exc:  # noqa: BLE001 - isolate long-lived worker
            faulted_markets.add(market_id)
            LOGGER.error(
                "cycle materialization failed market_id=%s fault_type=%s",
                market_id,
                type(exc).__name__,
            )
        else:
            faulted_markets.discard(market_id)
    if faulted_markets is None:
        snapshots["GLOBAL"] = materialize_global_tide(
            repository=repository,
            registry=registry,
            knowledge_time=cutoff,
            record_forward=record_forward,
        )
        return snapshots
    try:
        snapshots["GLOBAL"] = materialize_global_tide(
            repository=repository,
            registry=registry,
            knowledge_time=cutoff,
            record_forward=record_forward,
        )
    except Exception as exc:  # noqa: BLE001 - isolate long-lived worker
        faulted_markets.add("GLOBAL")
        LOGGER.error(
            "cycle materialization failed market_id=GLOBAL fault_type=%s",
            type(exc).__name__,
        )
    else:
        faulted_markets.discard("GLOBAL")
    return snapshots


def _notify_materialization_status(faulted_markets: frozenset[str]) -> None:
    if faulted_markets:
        pending = ",".join(sorted(faulted_markets))
        message = f"STATUS=collector materialization degraded; pending={pending}"
    else:
        message = "STATUS=collector materialization healthy"
    try:
        _systemd_notify(message)
    except Exception as exc:  # noqa: BLE001 - status must not stop collection
        LOGGER.error(
            "collector worker status notification failed fault_type=%s",
            type(exc).__name__,
        )


def _export_usd_funding_core_after_runs(
    runs: list[CollectorRun],
    *,
    repository: MarketRepository,
    cutoff: datetime,
) -> dict[str, object]:
    """Attempt one research export at a completed US cycle boundary.

    Profile readiness is deliberately independent from collector health.  An
    insufficient/corrected-lineage failure is logged and returned to the
    operator, but it never changes a sibling collector's completed outcome.
    """

    if not any(item.market_id == "US-USD" for item in runs):
        return {"status": "SKIPPED", "reason": "cycle had no US-USD collector"}
    directory = os.getenv(EXPORT_DIRECTORY_ENV, "").strip()
    if not directory:
        return {
            "status": "DISABLED",
            "reason": f"{EXPORT_DIRECTORY_ENV} is not configured",
        }
    try:
        target = export_funding_core_input_pack(
            repository,
            as_of=cutoff,
            directory=directory,
        )
    except Exception as exc:  # noqa: BLE001 — non-fatal research export boundary
        LOGGER.exception(
            "USD funding-core export failed after completed collector cycle"
        )
        return {
            "status": "FAILED",
            "fault": f"{type(exc).__name__}: {exc}",
        }
    LOGGER.info("USD funding-core research input exported to %s", target)
    return {"status": "SUCCESS", "path": str(target)}


async def collect_once(
    *,
    backfill: bool = False,
    market_ids: frozenset[str] | None = None,
    repository: MarketRepository | None = None,
    registry: MarketRegistry | None = None,
    materialize: bool = True,
    record_forward: bool = True,
) -> dict[str, object]:
    repo = repository or get_repository()
    markets = registry or default_registry()
    published_snapshots: dict[str, object] = {}
    supervisor = build_supervisor(
        repository=repo,
        registry=markets,
        backfill=backfill,
        market_ids=market_ids,
        run_writer=_completed_run_handler(
            repository=repo,
            registry=markets,
            backfill=backfill,
            materialize=materialize,
            record_forward=record_forward,
            published_snapshots=published_snapshots,
        ),
    )
    schedule_time = datetime.now(UTC).replace(microsecond=0)
    runs = await supervisor.run_due(now=schedule_time, force=True)
    cutoff = datetime.now(UTC).replace(microsecond=0)
    snapshots = (
        _materialize_after_runs(
            runs,
            repository=repo,
            registry=markets,
            cutoff=cutoff,
            record_forward=record_forward,
            existing=published_snapshots,
        )
        if materialize
        else {}
    )
    exports: dict[str, object] = {}
    if any(item.market_id == "US-USD" for item in runs):
        exports[FUNDING_CORE_PROFILE_ID] = await asyncio.to_thread(
            _export_usd_funding_core_after_runs,
            runs,
            repository=repo,
            cutoff=cutoff,
        )
    if backfill:
        for run in runs:
            if run.status.value != "SUCCESS":
                continue
            if _marker_requires_funding_core_export(run.market_id, run.adapter_id):
                funding_export = exports.get(FUNDING_CORE_PROFILE_ID)
                if (
                    not isinstance(funding_export, dict)
                    or funding_export.get("status") != "SUCCESS"
                ):
                    continue
            if (
                not materialize
                or run.market_id == "US-USD"
                or run.market_id in snapshots
            ):
                _mark_backfill_complete(run.market_id, run.adapter_id)
    return {
        "mode": "backfill" if backfill else "collect",
        "cutoff": cutoff.isoformat(),
        "runs": [item.to_dict() for item in runs],
        "snapshots": snapshots,
        "exports": exports,
    }


async def run_worker(
    *,
    poll_seconds: int = 30,
    repository: MarketRepository | None = None,
    registry: MarketRegistry | None = None,
) -> None:
    if poll_seconds < 5:
        raise ValueError("collector poll interval must be at least five seconds")
    repo = repository or get_repository()
    markets = registry or default_registry()
    published_snapshots: dict[str, object] = {}
    faulted_markets: set[str] = set()
    heartbeat_enabled = asyncio.Event()
    heartbeat_enabled.set()
    heartbeat_write_lock = asyncio.Lock()
    supervisor = build_supervisor(
        repository=repo,
        registry=markets,
        run_writer=_completed_run_handler(
            repository=repo,
            registry=markets,
            backfill=False,
            materialize=True,
            record_forward=True,
            published_snapshots=published_snapshots,
        ),
    )
    heartbeat_task: asyncio.Task[None] | None = None
    heartbeat_grace: float | None = None
    if callable(getattr(repo, "save_worker_heartbeat", None)):
        heartbeat_interval = _positive_seconds(
            "SEICHE_COLLECTOR_HEARTBEAT_INTERVAL_SECONDS",
            DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        )
        heartbeat_grace = max(
            heartbeat_interval * 2,
            _positive_seconds(
                "SEICHE_COLLECTOR_HEARTBEAT_GRACE_SECONDS",
                DEFAULT_HEARTBEAT_GRACE_SECONDS,
            ),
        )
        # Readiness means the worker can reach its durable liveness store.
        await _write_worker_heartbeat(repo, grace_seconds=heartbeat_grace)
        heartbeat_task = asyncio.create_task(
            _worker_heartbeat_loop(
                repo,
                interval_seconds=heartbeat_interval,
                grace_seconds=heartbeat_grace,
                heartbeat_enabled=heartbeat_enabled,
                heartbeat_write_lock=heartbeat_write_lock,
            ),
            name="seiche-collector-heartbeat",
        )
    _systemd_notify("READY=1")
    try:
        while True:
            published_snapshots.clear()
            schedule_time = datetime.now(UTC).replace(microsecond=0)
            runs = await supervisor.run_due(now=schedule_time)
            if runs:
                cutoff = datetime.now(UTC).replace(microsecond=0)
                previous_faults = frozenset(faulted_markets)
                await asyncio.to_thread(
                    _materialize_after_runs,
                    runs,
                    repository=repo,
                    registry=markets,
                    cutoff=cutoff,
                    record_forward=True,
                    existing=published_snapshots,
                    faulted_markets=faulted_markets,
                )
                current_faults = frozenset(faulted_markets)
                if current_faults:
                    heartbeat_enabled.clear()
                    # Drain a heartbeat already inside its durable write. Any
                    # waiter observes the closed gate after acquiring the lock.
                    async with heartbeat_write_lock:
                        pass
                elif previous_faults:
                    async with heartbeat_write_lock:
                        heartbeat_enabled.set()
                        if heartbeat_grace is not None:
                            try:
                                await _write_worker_heartbeat(
                                    repo,
                                    grace_seconds=heartbeat_grace,
                                )
                            except Exception as exc:  # noqa: BLE001 - loop retries
                                LOGGER.error(
                                    "collector worker heartbeat recovery failed "
                                    "fault_type=%s",
                                    type(exc).__name__,
                                )
                if current_faults != previous_faults:
                    _notify_materialization_status(current_faults)
                if any(item.market_id == "US-USD" for item in runs):
                    await asyncio.to_thread(
                        _export_usd_funding_core_after_runs,
                        runs,
                        repository=repo,
                        cutoff=cutoff,
                    )
            await asyncio.sleep(poll_seconds)
    finally:
        _systemd_notify("STOPPING=1")
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
