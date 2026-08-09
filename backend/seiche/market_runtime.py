"""Operator runtime for independent canonical collection and materialization."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

from seiche.collectors import (
    CollectorRun,
    CollectorSupervisor,
    FileRawCaptureSink,
    ParquetPartitionSink,
)
from seiche.markets.materialize import materialize_global_tide, materialize_market
from seiche.markets.registry import MarketRegistry, default_registry
from seiche.repository import MarketRepository, get_repository
from seiche.sources.official import build_official_adapters


def _storage_root(variable: str, fallback: str) -> Path:
    return Path(os.getenv(variable, fallback)).expanduser().resolve()


def _backfill_marker(market_id: str, adapter_id: str) -> Path | None:
    root = os.getenv("SEICHE_BACKFILL_STATE_DIR", "").strip()
    if not root:
        return None
    return Path(root).expanduser().resolve() / f"{market_id}--{adapter_id}.done"


def build_supervisor(
    *,
    repository: MarketRepository | None = None,
    registry: MarketRegistry | None = None,
    backfill: bool = False,
    market_ids: frozenset[str] | None = None,
) -> CollectorSupervisor:
    repo = repository or get_repository()
    markets = registry or default_registry()
    supervisor = CollectorSupervisor(
        registry=markets,
        raw_sink=FileRawCaptureSink(
            _storage_root("SEICHE_RAW_CAPTURE_DIR", "/var/lib/seiche/raw")
        ),
        normalized_sink=ParquetPartitionSink(
            _storage_root("SEICHE_NORMALIZED_DIR", "/var/lib/seiche/normalized")
        ),
        observation_writer=repo.save_observations,
        run_writer=repo.save_collector_run,
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


def _materialize_after_runs(
    runs: list[CollectorRun],
    *,
    repository: MarketRepository,
    registry: MarketRegistry,
    cutoff: datetime,
    record_forward: bool,
) -> dict[str, object]:
    market_ids = sorted({item.market_id for item in runs})
    snapshots: dict[str, object] = {}
    for market_id in market_ids:
        # US v2 remains the pack-local compatibility materialization emitted
        # by assemble.py, preserving bit-identical v1 migration output.
        if market_id == "US-USD":
            continue
        snapshots[market_id] = materialize_market(
            market_id,
            repository=repository,
            registry=registry,
            knowledge_time=cutoff,
            record_forward=record_forward,
        )
    snapshots["GLOBAL"] = materialize_global_tide(
        repository=repository,
        registry=registry,
        knowledge_time=cutoff,
        record_forward=record_forward,
    )
    return snapshots


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
    supervisor = build_supervisor(
        repository=repo,
        registry=markets,
        backfill=backfill,
        market_ids=market_ids,
    )
    schedule_time = datetime.now(UTC).replace(microsecond=0)
    runs = await supervisor.run_due(now=schedule_time, force=True)
    if backfill:
        for run in runs:
            marker = _backfill_marker(run.market_id, run.adapter_id)
            if run.status.value == "SUCCESS" and marker is not None:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch(exist_ok=True)
    cutoff = datetime.now(UTC).replace(microsecond=0)
    snapshots = (
        _materialize_after_runs(
            runs,
            repository=repo,
            registry=markets,
            cutoff=cutoff,
            record_forward=record_forward,
        )
        if materialize
        else {}
    )
    return {
        "mode": "backfill" if backfill else "collect",
        "cutoff": cutoff.isoformat(),
        "runs": [item.to_dict() for item in runs],
        "snapshots": snapshots,
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
    supervisor = build_supervisor(repository=repo, registry=markets)
    while True:
        schedule_time = datetime.now(UTC).replace(microsecond=0)
        runs = await supervisor.run_due(now=schedule_time)
        if runs:
            cutoff = datetime.now(UTC).replace(microsecond=0)
            await asyncio.to_thread(
                _materialize_after_runs,
                runs,
                repository=repo,
                registry=markets,
                cutoff=cutoff,
                record_forward=True,
            )
        await asyncio.sleep(poll_seconds)
