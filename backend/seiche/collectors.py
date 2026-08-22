"""Independent canonical collector schedules and failure isolation.

The REST layer never invokes this module. A scheduler process registers one
adapter per market/source and runs due tasks independently; successful batches
append raw evidence, normalized partitions, and canonical observations.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import uuid
from builtins import ExceptionGroup
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypeVar

import pandas as pd

from seiche.markets.base import SourceAdapterSpec
from seiche.markets.registry import MarketRegistry, default_registry
from seiche.public_faults import sanitize_fault
from seiche.repository import get_repository
from seiche.sources.base import (
    CanonicalSourceAdapter,
    ObservationBatch,
    RawCapture,
    SourcePolicyUnavailableError,
)


_T = TypeVar("_T")


class CollectorRunStatus(StrEnum):
    SUCCESS = "SUCCESS"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"


class PersistenceStageError(RuntimeError):
    """A named persistence stage exhausted its local retry budget."""


class AdapterDeadlineExceeded(TimeoutError):
    """A collector exhausted its bounded acquisition/retry budget."""


@dataclass(frozen=True, slots=True)
class CollectorRun:
    market_id: str
    adapter_id: str
    status: CollectorRunStatus
    started_at: str
    finished_at: str
    observations_written: int
    attempts: int
    next_due: str
    fault: str | None = None
    consecutive_failures: int = 0
    circuit_open_until: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "market_id": self.market_id,
            "adapter_id": self.adapter_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "observations_written": self.observations_written,
            "attempts": self.attempts,
            "next_due": self.next_due,
            # Collector runs are a durable/publicly projected boundary.  Never
            # persist arbitrary exception text, even when a caller manually
            # constructs a CollectorRun instead of using this supervisor.
            "fault": sanitize_fault(self.fault, status=self.status.value),
            "consecutive_failures": self.consecutive_failures,
            "circuit_open_until": self.circuit_open_until,
        }


@dataclass(slots=True)
class _CollectorState:
    next_due: datetime
    consecutive_failures: int = 0
    open_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class _CollectorTask:
    adapter: CanonicalSourceAdapter
    spec: SourceAdapterSpec


class RawCaptureSink(Protocol):
    def write(self, capture: RawCapture) -> str: ...


class NormalizedBatchSink(Protocol):
    def write(self, batch: ObservationBatch) -> list[str]: ...


def cadence_delta(value: str) -> timedelta:
    if value.startswith("PT"):
        amount = int(value[2:-1])
        suffix = value[-1]
        return {
            "H": timedelta(hours=amount),
            "M": timedelta(minutes=amount),
            "S": timedelta(seconds=amount),
        }[suffix]
    amount = int(value[1:-1])
    suffix = value[-1]
    return {"D": timedelta(days=amount), "W": timedelta(weeks=amount)}[suffix]


def _state_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, (str, datetime)):
        raise ValueError(f"persisted collector {field} must be an ISO-8601 timestamp")
    parsed = (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, str)
        else value
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"persisted collector {field} must be timezone-aware")
    return parsed.astimezone(UTC).replace(microsecond=0)


class FileRawCaptureSink:
    """Content-addressed immutable captures under market/source/date paths."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, capture: RawCapture) -> str:
        day = capture.captured_at.astimezone(UTC).date()
        directory = (
            self.root
            / f"market={capture.market_id}"
            / f"source={capture.adapter_id}"
            / f"date={day.isoformat()}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        suffix = {
            "application/json": ".json",
            "text/csv": ".csv",
            "application/xml": ".xml",
        }.get(capture.media_type, ".bin")
        target = directory / f"{capture.evidence_hash}{suffix}"
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != capture.evidence_hash:
                raise ValueError(f"raw capture hash collision at {target}")
            return str(target)
        temporary = directory / f".seiche-raw-{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                written = handle.write(capture.payload)
                if written != len(capture.payload):
                    raise OSError(
                        f"short raw-capture write: {written}/{len(capture.payload)} bytes"
                    )
            try:
                os.link(temporary, target)
            except FileExistsError:
                if (
                    hashlib.sha256(target.read_bytes()).hexdigest()
                    != capture.evidence_hash
                ):
                    raise ValueError(f"raw capture hash collision at {target}")
        finally:
            temporary.unlink(missing_ok=True)
        return str(target)


class ParquetPartitionSink:
    """Immutable normalized Parquet parts by market/source/event date.

    Install the ``collectors`` optional dependency to provide a Parquet engine.
    Each content hash gets its own part file, so a later revision appends rather
    than rewriting a prior partition.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, batch: ObservationBatch) -> list[str]:
        grouped: dict[str, list[dict]] = {}
        for observation in batch.observations:
            record = observation.to_record()
            record["jurisdiction_codes"] = ",".join(record["jurisdiction_codes"])
            grouped.setdefault(observation.event_time.date().isoformat(), []).append(
                record
            )
        outputs = []
        for event_date, records in grouped.items():
            canonical = json.dumps(
                records,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            directory = (
                self.root
                / f"market={batch.market_id}"
                / f"source={batch.adapter_id}"
                / f"date={event_date}"
            )
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"part-{digest}.parquet"
            if target.exists():
                outputs.append(str(target))
                continue
            frame = pd.DataFrame.from_records(records)
            descriptor, temporary = tempfile.mkstemp(
                prefix=".seiche-parquet-",
                suffix=".tmp",
                dir=directory,
            )
            os.close(descriptor)
            try:
                frame.to_parquet(temporary, index=False)
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    pass
            finally:
                Path(temporary).unlink(missing_ok=True)
            outputs.append(str(target))
        return outputs


class CollectorSupervisor:
    def __init__(
        self,
        *,
        registry: MarketRegistry | None = None,
        raw_sink: RawCaptureSink | None = None,
        normalized_sink: NormalizedBatchSink | None = None,
        observation_writer: Callable[[tuple], int] | None = None,
        run_writer: Callable[[dict], str] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        persistence_retry_limit: int = 4,
        persistence_backoff_seconds: float = 1.5,
        adapter_deadline_seconds: float = 300.0,
        restored_runs: Iterable[dict] = (),
    ) -> None:
        if persistence_retry_limit < 0 or persistence_backoff_seconds < 0:
            raise ValueError("persistence retry settings cannot be negative")
        if adapter_deadline_seconds <= 0:
            raise ValueError("adapter deadline must be positive")
        self.registry = registry or default_registry()
        self.raw_sink = raw_sink
        self.normalized_sink = normalized_sink
        self.observation_writer = (
            observation_writer or get_repository().save_observations
        )
        self.run_writer = run_writer
        self.sleep = sleep
        self.persistence_retry_limit = persistence_retry_limit
        self.persistence_backoff_seconds = persistence_backoff_seconds
        self.adapter_deadline_seconds = adapter_deadline_seconds
        self._tasks: dict[tuple[str, str], _CollectorTask] = {}
        self._states: dict[tuple[str, str], _CollectorState] = {}
        self._restored_states: dict[tuple[str, str], _CollectorState] = {}
        for run in restored_runs:
            key = (str(run["market_id"]).upper(), str(run["adapter_id"]))
            failures = int(run.get("consecutive_failures", 0))
            if failures < 0:
                raise ValueError("persisted collector failures cannot be negative")
            open_value = run.get("circuit_open_until")
            self._restored_states[key] = _CollectorState(
                next_due=_state_timestamp(run["next_due"], "next_due"),
                consecutive_failures=failures,
                open_until=(
                    _state_timestamp(open_value, "circuit_open_until")
                    if open_value is not None
                    else None
                ),
            )

    async def _persist_with_retry(
        self,
        operation: Callable[[], _T],
        *,
        stage: str,
    ) -> _T:
        """Retry one persistence stage without recollecting its source batch."""

        for attempt in range(self.persistence_retry_limit + 1):
            try:
                return await asyncio.to_thread(operation)
            except SourcePolicyUnavailableError:
                # Policy revocation is deterministic and must remain an
                # UNAVAILABLE abstention, not be retried or disguised as a
                # persistence outage.
                raise
            except Exception as exc:  # noqa: BLE001 - bounded persistence boundary
                deterministic = isinstance(exc, (ImportError, TypeError, ValueError))
                if deterministic or attempt == self.persistence_retry_limit:
                    raise PersistenceStageError(
                        f"{stage} persistence failed after {attempt + 1} attempt(s): "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                await self.sleep(self.persistence_backoff_seconds * (2**attempt))
        raise RuntimeError("persistence retry loop exhausted")  # pragma: no cover

    def register(self, adapter: CanonicalSourceAdapter) -> None:
        market_id = adapter.market_id.upper()
        pack = self.registry.get(market_id)
        try:
            spec = pack.adapter_map[adapter.adapter_id]
        except KeyError as exc:
            raise ValueError(
                f"adapter {adapter.adapter_id!r} is not declared by {market_id}"
            ) from exc
        key = (market_id, adapter.adapter_id)
        if key in self._tasks:
            raise ValueError(f"collector {key!r} is already registered")
        self._tasks[key] = _CollectorTask(adapter, spec)
        self._states[key] = self._restored_states.pop(
            key,
            _CollectorState(next_due=datetime.min.replace(tzinfo=UTC)),
        )

    async def run_due(
        self,
        *,
        now: datetime | None = None,
        force: bool = False,
    ) -> list[CollectorRun]:
        current = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
        due = [
            (key, task)
            for key, task in self._tasks.items()
            if force or self._states[key].next_due <= current
        ]
        pending = [
            asyncio.create_task(self._run_one(key, task, current)) for key, task in due
        ]
        runs: list[CollectorRun] = []
        writer_errors: list[Exception] = []
        # Persist in completion order. A slow or retrying source therefore
        # cannot hold already-finished markets behind the cycle boundary. Run
        # writers execute off-loop, so every sibling collector keeps moving;
        # writer failures are reported only after all active tasks are reaped.
        try:
            for completed in asyncio.as_completed(pending):
                run = await completed
                runs.append(run)
                if self.run_writer is None:
                    continue
                try:
                    await asyncio.to_thread(self.run_writer, run.to_dict())
                except Exception as exc:  # noqa: BLE001 — persistence boundary
                    exc.add_note(
                        f"while publishing {run.market_id}/{run.adapter_id} collector run"
                    )
                    writer_errors.append(exc)
        finally:
            # Outer cancellation and unexpected internal exceptions must not
            # strand collector tasks. Normal adapter failures still complete
            # as scoped FAILED runs and therefore never enter this path early.
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        ordered = sorted(runs, key=lambda item: (item.market_id, item.adapter_id))
        if writer_errors:
            raise ExceptionGroup(
                "one or more collector runs could not be published", writer_errors
            )
        return ordered

    async def _run_one(
        self,
        key: tuple[str, str],
        task: _CollectorTask,
        now: datetime,
    ) -> CollectorRun:
        state = self._states[key]
        started = datetime.now(UTC).replace(microsecond=0)
        cadence = cadence_delta(task.spec.expected_cadence)

        def unavailable_run(
            fault: SourcePolicyUnavailableError,
            *,
            attempts: int,
        ) -> CollectorRun:
            # A deterministic access-policy abstention is not source
            # instability. It clears any legacy circuit state while remaining
            # visible to materialization and public fault reporting.
            state.consecutive_failures = 0
            state.open_until = None
            state.next_due = now + cadence
            finished = datetime.now(UTC).replace(microsecond=0)
            detail = sanitize_fault(fault, status=CollectorRunStatus.UNAVAILABLE)
            return CollectorRun(
                key[0],
                key[1],
                CollectorRunStatus.UNAVAILABLE,
                started.isoformat(),
                finished.isoformat(),
                0,
                attempts,
                state.next_due.isoformat(),
                detail,
                0,
                None,
            )

        availability_fault: Exception | None = None
        availability_check = getattr(task.adapter, "check_availability", None)
        if callable(availability_check):
            try:
                availability_check()
            except SourcePolicyUnavailableError as exc:
                return unavailable_run(exc, attempts=0)
            except Exception as exc:  # noqa: BLE001 - isolate adapter preflight
                availability_fault = exc
        if state.open_until is not None and state.open_until > now:
            return CollectorRun(
                key[0],
                key[1],
                CollectorRunStatus.CIRCUIT_OPEN,
                started.isoformat(),
                started.isoformat(),
                0,
                0,
                state.open_until.isoformat(),
                sanitize_fault(None, status=CollectorRunStatus.CIRCUIT_OPEN),
                state.consecutive_failures,
                state.open_until.isoformat(),
            )

        fault: Exception | None = availability_fault
        batch: ObservationBatch | None = None
        attempts = 0
        try:
            # The timeout is one total acquisition budget: source calls,
            # connector-owned retries, supervisor retries, and their backoff.
            async with asyncio.timeout(self.adapter_deadline_seconds):
                for attempt in (
                    range(task.spec.retry_limit + 1)
                    if availability_fault is None
                    else range(0)
                ):
                    attempts = attempt + 1
                    try:
                        batch = await task.adapter.collect()
                        if batch.market_id != key[0] or batch.adapter_id != key[1]:
                            raise ValueError(
                                "collector returned a batch outside its registered scope"
                            )
                    except SourcePolicyUnavailableError as exc:
                        return unavailable_run(exc, attempts=attempts)
                    except Exception as exc:  # noqa: BLE001 — isolation boundary
                        fault = exc
                        batch = None
                        if attempt < task.spec.retry_limit:
                            await self.sleep(task.spec.backoff_seconds * (2**attempt))
                    else:
                        break
        except TimeoutError:
            fault = AdapterDeadlineExceeded(
                f"collector exceeded {self.adapter_deadline_seconds:g}s acquisition deadline"
            )
            batch = None

        if batch is not None:
            def persist_if_available(operation: Callable[[], _T]) -> _T:
                # Run this in the same worker invocation as the write so an
                # approval cannot be revoked between an outer preflight and
                # entry into a raw, normalized, or observation sink. Retries
                # repeat the check before making another write attempt.
                if callable(availability_check):
                    availability_check()
                return operation()

            try:
                if self.raw_sink is not None and batch.raw_capture is not None:
                    raw_sink = self.raw_sink
                    raw_capture = batch.raw_capture
                    await self._persist_with_retry(
                        lambda: persist_if_available(
                            lambda: raw_sink.write(raw_capture)
                        ),
                        stage="raw capture",
                    )
                if self.normalized_sink is not None:
                    normalized_sink = self.normalized_sink
                    await self._persist_with_retry(
                        lambda: persist_if_available(
                            lambda: normalized_sink.write(batch)
                        ),
                        stage="normalized batch",
                    )
                written = await self._persist_with_retry(
                    lambda: persist_if_available(
                        lambda: self.observation_writer(batch.observations)
                    ),
                    stage="observation writer",
                )
            except SourcePolicyUnavailableError as exc:
                return unavailable_run(exc, attempts=attempts)
            except Exception as exc:  # noqa: BLE001 — isolation boundary
                fault = exc
            else:
                state.consecutive_failures = 0
                state.open_until = None
                state.next_due = now + cadence
                finished = datetime.now(UTC).replace(microsecond=0)
                return CollectorRun(
                    key[0],
                    key[1],
                    CollectorRunStatus.SUCCESS,
                    started.isoformat(),
                    finished.isoformat(),
                    written,
                    attempts,
                    state.next_due.isoformat(),
                    None,
                    0,
                    None,
                )

        state.consecutive_failures += 1
        state.next_due = now + cadence
        if state.consecutive_failures >= task.spec.circuit_breaker_failures:
            state.open_until = now + timedelta(
                seconds=task.spec.circuit_breaker_cooldown_seconds
            )
            # A circuit breaker may delay a high-frequency source, but it must
            # never make a slower source run more often than its declared
            # cadence. Daily official feeds often have a shorter cooldown.
            state.next_due = max(state.next_due, state.open_until)
        finished = datetime.now(UTC).replace(microsecond=0)
        detail = sanitize_fault(fault, status=CollectorRunStatus.FAILED)
        return CollectorRun(
            key[0],
            key[1],
            CollectorRunStatus.FAILED,
            started.isoformat(),
            finished.isoformat(),
            0,
            attempts,
            state.next_due.isoformat(),
            detail,
            state.consecutive_failures,
            state.open_until.isoformat() if state.open_until is not None else None,
        )
