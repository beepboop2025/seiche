"""Durable runtime for the broad legacy source sweep.

The legacy collectors remain the widest Seiche source surface while their
observations migrate into the canonical market repository.  This module gives
that acquisition pass an independent lifecycle without importing the heavy
board assembler merely to load the CLI.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from seiche.public_faults import project_public_fault, sanitize_fault_record
from seiche.repository import (
    LEGACY_SOURCE_WORKER_COMPONENT_ID,
    MarketRepository,
    get_repository,
)

LOGGER = logging.getLogger(__name__)

LEGACY_SOURCE_SUMMARY_BLOB_KEY = "ingest:legacy-source:last-run"
DEFAULT_POLL_SECONDS = 300
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0
DEFAULT_HEARTBEAT_GRACE_SECONDS = 120.0

_SAFE_STATUS_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class LegacySourceSweepError(RuntimeError):
    """A sweep failed with a summary safe to print or persist."""

    def __init__(self, summary: dict[str, Any]) -> None:
        super().__init__("legacy source sweep failed")
        self.summary = summary


def _positive_seconds(variable: str, fallback: float) -> float:
    raw = os.getenv(variable, "").strip()
    value = float(raw) if raw else fallback
    if value <= 0:
        raise ValueError(f"{variable} must be positive")
    return value


def _utc_now(clock: Callable[[], datetime] | None = None) -> datetime:
    value = (clock or (lambda: datetime.now(UTC)))()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("legacy source worker clock must be timezone-aware")
    return value.astimezone(UTC).replace(microsecond=0)


def _safe_source_name(value: object) -> str:
    record = sanitize_fault_record({"source": value, "status": "SUCCESS"})
    return str(record["source"])


def _safe_status(value: object) -> str:
    candidate = value if isinstance(value, str) else ""
    candidate = candidate.strip().upper()
    return candidate if _SAFE_STATUS_RE.fullmatch(candidate) else "FAILED"


def _failure_summary(
    error: BaseException,
    *,
    started_at: datetime,
    finished_at: datetime,
) -> dict[str, Any]:
    fault = project_public_fault(
        error,
        default_market_id="GLOBAL",
        default_source=LEGACY_SOURCE_WORKER_COMPONENT_ID,
    )
    return {
        "schema": "seiche.legacy-source-sweep.v1",
        "component_id": LEGACY_SOURCE_WORKER_COMPONENT_ID,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "status": "FAILED",
        "counts": {
            "source_groups": 0,
            "successful": 0,
            "degraded": 0,
            "failed": 1,
            "faults": 1,
        },
        "sources": [],
        "faults": [fault],
    }


def _completed_summary(
    sources: Mapping[object, object],
    faults: list[object],
    *,
    started_at: datetime,
    finished_at: datetime,
) -> dict[str, Any]:
    successful_sources = {_safe_source_name(source) for source in sources}
    safe_faults = []
    for fault in faults:
        if isinstance(fault, Mapping):
            # A legacy fault record may contain arbitrary extra fields or an
            # incorrect status.  Project only its source and diagnostic value,
            # and force fault-list entries to be failures.
            fault = {
                "source": fault.get("source", fault.get("adapter_id")),
                "status": "FAILED",
                "detail": fault.get("detail", fault.get("fault")),
            }
        safe_faults.append(
            project_public_fault(
                fault,
                default_market_id="GLOBAL",
                default_source="unknown_source",
            )
        )
    faulted_sources = {str(fault["source"]) for fault in safe_faults}
    source_names = sorted(successful_sources | faulted_sources)
    source_rows = []
    for source in source_names:
        if source in successful_sources and source in faulted_sources:
            status = "DEGRADED"
        elif source in faulted_sources:
            status = "FAILED"
        else:
            status = "SUCCESS"
        source_rows.append({"source": source, "status": status})

    status_counts = {
        status: sum(row["status"] == status for row in source_rows)
        for status in ("SUCCESS", "DEGRADED", "FAILED")
    }
    return {
        "schema": "seiche.legacy-source-sweep.v1",
        "component_id": LEGACY_SOURCE_WORKER_COMPONENT_ID,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "status": "DEGRADED" if safe_faults else "SUCCESS",
        "counts": {
            "source_groups": len(source_rows),
            "successful": status_counts["SUCCESS"],
            "degraded": status_counts["DEGRADED"],
            "failed": status_counts["FAILED"],
            "faults": len(safe_faults),
        },
        "sources": source_rows,
        "faults": safe_faults,
    }


async def _persist_summary(
    summary: dict[str, Any],
    *,
    summary_writer: Callable[[str, object], None] | None = None,
) -> None:
    if summary_writer is None:
        # Keep the store import lazy for CLI startup and isolated unit tests.
        from seiche import store

        summary_writer = store.save_blob
    await asyncio.to_thread(summary_writer, LEGACY_SOURCE_SUMMARY_BLOB_KEY, summary)


async def collect_legacy_once(
    *,
    clock: Callable[[], datetime] | None = None,
    summary_writer: Callable[[str, object], None] | None = None,
) -> dict[str, Any]:
    """Run and summarize the legacy source sweep without assembling the board."""

    started_at = _utc_now(clock)
    try:
        # ``assemble`` imports every engine.  Keep it out of module/CLI import
        # paths and pay that cost only when a source sweep is actually run.
        from seiche import assemble

        sources, faults = await assemble._gather_sources()
        if not isinstance(sources, Mapping) or not isinstance(faults, list):
            raise TypeError("legacy source sweep returned an invalid result")
        finished_at = _utc_now(clock)
        summary = _completed_summary(
            sources,
            faults,
            started_at=started_at,
            finished_at=finished_at,
        )
    except Exception as exc:  # noqa: BLE001 - total sweep isolation boundary
        finished_at = _utc_now(clock)
        summary = _failure_summary(
            exc,
            started_at=started_at,
            finished_at=finished_at,
        )
        try:
            await _persist_summary(summary, summary_writer=summary_writer)
        except Exception as persist_exc:  # noqa: BLE001 - emit only safe metadata
            raise LegacySourceSweepError(
                _failure_summary(
                    persist_exc,
                    started_at=started_at,
                    finished_at=finished_at,
                )
            ) from None
        raise LegacySourceSweepError(summary) from None

    try:
        await _persist_summary(summary, summary_writer=summary_writer)
    except Exception as exc:  # noqa: BLE001 - never expose persistence diagnostics
        raise LegacySourceSweepError(
            _failure_summary(
                exc,
                started_at=started_at,
                finished_at=finished_at,
            )
        ) from None
    return summary


def _systemd_notify(message: str) -> None:
    """Send readiness/watchdog state without a systemd Python dependency."""

    address = os.getenv("NOTIFY_SOCKET", "")
    if not address:
        return
    if address.startswith("@"):
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
    if grace_seconds <= 0:
        raise ValueError("legacy source heartbeat grace must be positive")
    heartbeat_at = _utc_now(clock)
    expected_by = heartbeat_at + timedelta(seconds=grace_seconds)
    await asyncio.to_thread(
        repository.save_worker_heartbeat,
        component_id=LEGACY_SOURCE_WORKER_COMPONENT_ID,
        heartbeat_at=heartbeat_at,
        expected_by=expected_by,
    )


async def _heartbeat_loop(
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
        except Exception as exc:  # noqa: BLE001 - liveness loop must survive
            LOGGER.error(
                "legacy source heartbeat persistence failed fault_type=%s",
                type(exc).__name__,
            )
        try:
            _systemd_notify("WATCHDOG=1")
        except Exception as exc:  # noqa: BLE001 - liveness loop must survive
            LOGGER.error(
                "legacy source watchdog notification failed fault_type=%s",
                type(exc).__name__,
            )
        await asyncio.sleep(interval_seconds)


def _notify_fixed_status(status: object) -> None:
    try:
        _systemd_notify(f"STATUS=legacy source sweep {_safe_status(status).lower()}")
    except Exception as exc:  # noqa: BLE001 - status is diagnostic, not readiness
        LOGGER.error(
            "legacy source status notification failed fault_type=%s",
            type(exc).__name__,
        )


async def run_legacy_worker(
    *,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
    repository: MarketRepository | None = None,
) -> None:
    """Collect legacy sources forever with durable and systemd liveness."""

    if poll_seconds < 5:
        raise ValueError("legacy source poll interval must be at least five seconds")
    repo = repository or get_repository()
    heartbeat_interval = _positive_seconds(
        "SEICHE_SOURCE_HEARTBEAT_INTERVAL_SECONDS",
        DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    )
    heartbeat_grace = max(
        heartbeat_interval * 2,
        _positive_seconds(
            "SEICHE_SOURCE_HEARTBEAT_GRACE_SECONDS",
            DEFAULT_HEARTBEAT_GRACE_SECONDS,
        ),
    )
    ready = False
    heartbeat_enabled = asyncio.Event()
    heartbeat_write_lock = asyncio.Lock()
    heartbeat_task: asyncio.Task[None] | None = None
    try:
        while True:
            try:
                summary = await collect_legacy_once()
                if not ready:
                    # A durable completed summary and heartbeat are the
                    # readiness boundary, not mere process startup.
                    await _write_worker_heartbeat(
                        repo,
                        grace_seconds=heartbeat_grace,
                    )
                    heartbeat_enabled.set()
                    candidate_heartbeat = asyncio.create_task(
                        _heartbeat_loop(
                            repo,
                            interval_seconds=heartbeat_interval,
                            grace_seconds=heartbeat_grace,
                            heartbeat_enabled=heartbeat_enabled,
                            heartbeat_write_lock=heartbeat_write_lock,
                        ),
                        name="seiche-legacy-source-heartbeat",
                    )
                    try:
                        _systemd_notify("READY=1")
                    except Exception:
                        candidate_heartbeat.cancel()
                        with suppress(asyncio.CancelledError):
                            await candidate_heartbeat
                        raise
                    heartbeat_task = candidate_heartbeat
                    ready = True
                elif not heartbeat_enabled.is_set():
                    # Recover the durable health signal only after a complete
                    # source sweep succeeds again. Serialize this transition
                    # with the periodic writer so no stale write can race it.
                    async with heartbeat_write_lock:
                        await _write_worker_heartbeat(
                            repo,
                            grace_seconds=heartbeat_grace,
                        )
                        heartbeat_enabled.set()
                _notify_fixed_status(summary["status"])
            except Exception as exc:  # noqa: BLE001 - retry the next full sweep
                if ready:
                    heartbeat_enabled.clear()
                    # Drain a heartbeat already inside its durable write. Any
                    # future iteration observes the closed acquisition gate.
                    async with heartbeat_write_lock:
                        pass
                LOGGER.error(
                    "legacy source sweep failed; retrying fault_type=%s",
                    type(exc).__name__,
                )
                _notify_fixed_status("FAILED")
            await asyncio.sleep(poll_seconds)
    finally:
        try:
            _systemd_notify("STOPPING=1")
        except Exception as exc:  # noqa: BLE001 - shutdown must still reap task
            LOGGER.error(
                "legacy source stopping notification failed fault_type=%s",
                type(exc).__name__,
            )
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
