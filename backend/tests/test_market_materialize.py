from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncio

import pandas as pd
from seiche import store
from seiche import market_runtime
from seiche.collectors import CollectorRun, CollectorRunStatus
from seiche.domain.observation import (
    CanonicalUnit,
    ConnectorClassification,
    DayCountConvention,
    Observation,
    QualityState,
    RateCompounding,
    RedistributionStatus,
    SemanticRole,
    StalenessState,
    evidence_sha256,
)
from seiche.markets.materialize import materialize_global_tide, materialize_market
from seiche.kernel.engines import RoleSeries, cross_basin_coupling
from seiche.repository import SQLiteMarketRepository
from seiche.sources.base import ObservationBatch


def _rate(
    *,
    market_id: str,
    instrument_id: str,
    role: SemanticRole,
    value: float,
    event_time: datetime,
    source: str = "official-test",
    classification: ConnectorClassification = ConnectorClassification.OFFICIAL_OPEN,
    redistribution: RedistributionStatus = RedistributionStatus.ALLOWED,
) -> Observation:
    area, currency = market_id.split("-")
    jurisdiction = {"EA": "DE", "IN": "IN", "SG": "SG"}.get(area, area)
    return Observation(
        market_id=market_id,
        monetary_area_id=area,
        jurisdiction_codes=(jurisdiction,),
        currency=currency,
        instrument_id=instrument_id,
        semantic_role=role,
        value=value,
        canonical_unit=CanonicalUnit.BASIS_POINTS,
        rate_compounding=RateCompounding.SIMPLE,
        day_count=DayCountConvention.ACT_365,
        event_time=event_time,
        source_publication_time=event_time,
        knowledge_time=event_time,
        revision_id=f"test-{event_time.date().isoformat()}",
        source=source,
        evidence_hash=evidence_sha256(
            f"{market_id}:{instrument_id}:{event_time.isoformat()}:{value}"
        ),
        connector_classification=classification,
        redistribution_status=redistribution,
        quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )


def _save_success_run(market_id: str, adapter_id: str, cutoff: datetime) -> None:
    store.save_collector_run(
        {
            "market_id": market_id,
            "adapter_id": adapter_id,
            "status": "SUCCESS",
            "started_at": cutoff.isoformat(),
            "finished_at": cutoff.isoformat(),
            "observations_written": 1,
            "attempts": 1,
            "next_due": (cutoff + timedelta(days=1)).isoformat(),
            "fault": None,
        }
    )


def test_local_materializer_uses_policy_asof_alignment_and_is_idempotent(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "local.sqlite")
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [
        _rate(
            market_id="EA-EUR",
            instrument_id="EA.ECB.DFR",
            role=SemanticRole.POLICY_FLOOR,
            value=200,
            event_time=start,
        ),
        _rate(
            market_id="EA-EUR",
            instrument_id="EA.ECB.MRO",
            role=SemanticRole.POLICY_TARGET,
            value=215,
            event_time=start,
        ),
        _rate(
            market_id="EA-EUR",
            instrument_id="EA.ECB.MLF",
            role=SemanticRole.POLICY_CEILING,
            value=240,
            event_time=start,
        ),
    ]
    for offset in range(80):
        rows.append(
            _rate(
                market_id="EA-EUR",
                instrument_id="EA.ECB.ESTR",
                role=SemanticRole.UNSECURED_OVERNIGHT,
                value=206 + offset % 5,
                event_time=start + timedelta(days=offset),
            )
        )
    store.save_observations(rows)
    cutoff = start + timedelta(days=80)
    for adapter in ("ecb_benchmark", "ecb_policy", "ecb_liquidity"):
        _save_success_run("EA-EUR", adapter, cutoff)

    repository = SQLiteMarketRepository()
    first = materialize_market(
        "EA-EUR", repository=repository, knowledge_time=cutoff
    )
    second = materialize_market(
        "EA-EUR", repository=repository, knowledge_time=cutoff
    )
    gauge = store.load_latest_market_snapshot("EA-EUR", "gauge")["payload"]

    assert first == second
    assert store.forward_record_count("EA-EUR") == 2  # overview + gauge
    assert gauge["status"] == "READY"
    assert gauge["reading"]["index"] is not None
    assert gauge["event_cutoff"].startswith("2025-03-21")
    policy = next(
        item
        for item in gauge["components"]
        if item["component_id"] == "policy_relative_overnight"
    )
    assert policy["normalization"]["method"] == "point_in_time_own_history"
    assert policy["kernel"]["event_cutoff"].startswith("2025-03-21")


def test_missing_required_local_component_materializes_unavailable_never_zero(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "missing.sqlite")
    cutoff = datetime(2026, 8, 9, tzinfo=UTC)

    materialize_market(
        "SG-SGD",
        repository=SQLiteMarketRepository(),
        knowledge_time=cutoff,
    )
    gauge = store.load_latest_market_snapshot("SG-SGD", "gauge")["payload"]

    assert gauge["status"] == "UNAVAILABLE"
    assert gauge["reading"]["index"] is None
    assert gauge["reading"]["regime"] is None
    assert "corridor_pressure" in gauge["reading"]["publication_reason"]


def test_global_tide_is_sealed_unavailable_then_computes_only_from_fx_basis(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "tide.sqlite")
    repository = SQLiteMarketRepository()
    cutoff = datetime(2026, 4, 1, tzinfo=UTC)

    first_id = materialize_global_tide(
        repository=repository,
        knowledge_time=cutoff,
    )
    unavailable = store.load_latest_market_snapshot("GLOBAL", "tide")["payload"]
    assert first_id
    assert unavailable["status"] == "UNAVAILABLE"
    assert unavailable["reading"]["value"] is None

    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for offset in range(80):
        event = start + timedelta(days=offset)
        for market_id, instrument_id, multiplier in (
            ("IN-INR", "IN.MARKET.FX_FORWARD_BASIS", 1.0),
            ("SG-SGD", "SG.MARKET.FX_SWAP_BASIS", 0.7),
        ):
            rows.append(
                _rate(
                    market_id=market_id,
                    instrument_id=instrument_id,
                    role=SemanticRole.FX_SWAP_BASIS,
                    value=(-20 + offset * 0.2) * multiplier,
                    event_time=event,
                    source="licensed-test",
                    classification=ConnectorClassification.LICENSED,
                    redistribution=RedistributionStatus.DERIVED_ONLY,
                )
            )
    store.save_observations(rows)
    _save_success_run("IN-INR", "licensed_inr_market", cutoff)
    _save_success_run("SG-SGD", "licensed_sgd_market", cutoff)
    second_id = materialize_global_tide(
        repository=repository,
        knowledge_time=cutoff,
    )
    ready = store.load_latest_market_snapshot("GLOBAL", "tide")["payload"]

    assert second_id != first_id
    assert ready["status"] == "READY"
    assert ready["reading"]["value"] is not None
    assert ready["reading"]["value"] == ready["reading"]["synchronization_index"]
    assert ready["evidence_eligibility"]["eligible"] is False
    assert "global calibration is forward-only" in ready["evidence_eligibility"]["reasons"]
    assert ready["notes"] == "Local gauges are never averaged into the Global Tide."

    materialize_global_tide(
        repository=repository,
        knowledge_time=cutoff + timedelta(days=5),
    )
    stale = store.load_latest_market_snapshot("GLOBAL", "tide")["payload"]
    assert stale["status"] == "DEGRADED"
    assert {item["market_id"] for item in stale["stale_inputs"]} == {
        "IN-INR",
        "SG-SGD",
    }

    materialize_global_tide(
        repository=repository,
        knowledge_time=cutoff + timedelta(days=10),
    )
    dead = store.load_latest_market_snapshot("GLOBAL", "tide")["payload"]
    assert dead["status"] == "UNAVAILABLE"
    assert dead["reading"]["value"] is None


def test_global_tide_changes_span_the_same_common_business_dates() -> None:
    first = tuple(
        _rate(
            market_id="IN-INR",
            instrument_id="IN.MARKET.FX_FORWARD_BASIS",
            role=SemanticRole.FX_SWAP_BASIS,
            value=value,
            event_time=datetime(2026, 1, 5, tzinfo=UTC) + timedelta(days=offset),
        )
        for offset, value in zip((0, 1, 2, 3, 4), (0, 10, 30, 50, 90), strict=True)
    )
    second = tuple(
        _rate(
            market_id="SG-SGD",
            instrument_id="SG.MARKET.FX_SWAP_BASIS",
            role=SemanticRole.FX_SWAP_BASIS,
            value=value,
            event_time=datetime(2026, 1, 5, tzinfo=UTC) + timedelta(days=offset),
        )
        for offset, value in zip((0, 2, 3, 4), (0, 3, 5, 9), strict=True)
    )

    def role_series(observations: tuple[Observation, ...]) -> RoleSeries:
        return RoleSeries(
            SemanticRole.FX_SWAP_BASIS,
            observations[0].instrument_id,
            CanonicalUnit.BASIS_POINTS,
            pd.Series(
                [float(item.value) for item in observations],
                index=pd.DatetimeIndex([item.event_time for item in observations]),
                dtype=float,
            ),
            observations,
        )

    result = cross_basin_coupling(
        {"IN-INR": role_series(first), "SG-SGD": role_series(second)},
        minimum_aligned_changes=3,
    )

    assert result.value == 100.0
    assert result.event_cutoff == "2026-01-09T00:00:00+00:00"


def test_global_tide_payload_cutoff_is_the_last_shared_session(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "tide-cutoff.sqlite")
    repository = SQLiteMarketRepository()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for offset in range(61):
        event = start + timedelta(days=offset)
        for market_id, instrument_id, multiplier in (
            ("IN-INR", "IN.MARKET.FX_FORWARD_BASIS", 1.0),
            ("SG-SGD", "SG.MARKET.FX_SWAP_BASIS", 0.7),
        ):
            rows.append(
                _rate(
                    market_id=market_id,
                    instrument_id=instrument_id,
                    role=SemanticRole.FX_SWAP_BASIS,
                    value=(-20 + offset * 0.2) * multiplier,
                    event_time=event,
                )
            )
    rows.append(
        _rate(
            market_id="IN-INR",
            instrument_id="IN.MARKET.FX_FORWARD_BASIS",
            role=SemanticRole.FX_SWAP_BASIS,
            value=5,
            event_time=start + timedelta(days=70),
        )
    )
    store.save_observations(rows)
    cutoff = start + timedelta(days=80)
    _save_success_run("IN-INR", "licensed_inr_market", cutoff)
    _save_success_run("SG-SGD", "licensed_sgd_market", cutoff)

    materialize_global_tide(repository=repository, knowledge_time=cutoff)
    payload = store.load_latest_market_snapshot("GLOBAL", "tide")["payload"]

    expected = (start + timedelta(days=60)).isoformat()
    assert payload["status"] == "READY"
    assert payload["event_cutoff"] == expected
    assert payload["components"][0]["event_cutoff"] == expected


def test_collection_cycle_materializes_after_new_rows_become_knowable(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "cycle-cutoff.sqlite")
    repository = SQLiteMarketRepository()

    class _CrossSecondSupervisor:
        async def run_due(self, *, now, force):
            while datetime.now(UTC).replace(microsecond=0) <= now:
                await asyncio.sleep(0.02)
            capture = datetime.now(UTC).replace(microsecond=0)
            rows = [
                _rate(
                    market_id="IN-INR",
                    instrument_id=instrument,
                    role=role,
                    value=value,
                    event_time=capture,
                )
                for instrument, role, value in (
                    ("IN.RBI.SDF", SemanticRole.POLICY_FLOOR, 525),
                    ("IN.RBI.POLICY_REPO", SemanticRole.POLICY_TARGET, 550),
                    ("IN.RBI.MSF", SemanticRole.POLICY_CEILING, 575),
                    ("IN.MARKET.CALL_WAR", SemanticRole.UNSECURED_OVERNIGHT, 548),
                )
            ]
            repository.save_observations(rows)
            run = CollectorRun(
                "IN-INR",
                "rbi_official",
                CollectorRunStatus.SUCCESS,
                capture.isoformat(),
                capture.isoformat(),
                len(rows),
                1,
                (capture + timedelta(days=1)).isoformat(),
            )
            repository.save_collector_run(run.to_dict())
            return [run]

    monkeypatch.setattr(
        market_runtime,
        "build_supervisor",
        lambda **_kwargs: _CrossSecondSupervisor(),
    )
    payload = asyncio.run(
        market_runtime.collect_once(
            market_ids=frozenset({"IN-INR"}),
            repository=repository,
        )
    )
    gauge = store.load_latest_market_snapshot("IN-INR", "gauge")["payload"]

    assert payload["runs"][0]["status"] == "SUCCESS"
    assert gauge["status"] == "DEGRADED"
    assert gauge["reading"]["index"] is not None


def test_slow_foreign_collector_cannot_delay_completed_local_snapshot(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "isolated-publication.sqlite")
    repository = SQLiteMarketRepository()
    capture = datetime.now(UTC).replace(microsecond=0)
    japan_entered = asyncio.Event()
    release_japan = asyncio.Event()

    class _HealthyIndiaAdapter:
        market_id = "IN-INR"
        adapter_id = "rbi_official"

        async def collect(self) -> ObservationBatch:
            rows = tuple(
                _rate(
                    market_id="IN-INR",
                    instrument_id=instrument,
                    role=role,
                    value=value,
                    event_time=capture,
                )
                for instrument, role, value in (
                    ("IN.RBI.SDF", SemanticRole.POLICY_FLOOR, 525),
                    ("IN.RBI.POLICY_REPO", SemanticRole.POLICY_TARGET, 550),
                    ("IN.RBI.MSF", SemanticRole.POLICY_CEILING, 575),
                    ("IN.MARKET.CALL_WAR", SemanticRole.UNSECURED_OVERNIGHT, 548),
                )
            )
            return ObservationBatch(self.market_id, self.adapter_id, capture, rows)

    class _BlockedJapanAdapter:
        market_id = "JP-JPY"
        adapter_id = "boj_rates"

        async def collect(self) -> ObservationBatch:
            japan_entered.set()
            await release_japan.wait()
            raise RuntimeError("BOJ collector blocked for test")

    async def no_sleep(_: float) -> None:
        return None

    def supervisor_factory(**kwargs):
        supervisor = market_runtime.CollectorSupervisor(
            registry=kwargs["registry"],
            observation_writer=repository.save_observations,
            run_writer=kwargs["run_writer"],
            sleep=no_sleep,
        )
        supervisor.register(_BlockedJapanAdapter())
        supervisor.register(_HealthyIndiaAdapter())
        return supervisor

    monkeypatch.setattr(market_runtime, "build_supervisor", supervisor_factory)
    async def exercise() -> dict:
        cycle = asyncio.create_task(
            market_runtime.collect_once(repository=repository)
        )
        await asyncio.wait_for(japan_entered.wait(), timeout=15)
        try:
            india = None
            for _ in range(1500):
                india = await asyncio.to_thread(
                    repository.load_latest_market_snapshot,
                    "IN-INR",
                    "gauge",
                )
                if india is not None:
                    break
                await asyncio.sleep(0.01)
            assert india is not None
            assert india["payload"]["reading"]["index"] is not None
            assert not cycle.done()
        finally:
            release_japan.set()
        return await asyncio.wait_for(cycle, timeout=30)

    payload = asyncio.run(exercise())
    statuses = {(run["market_id"], run["status"]) for run in payload["runs"]}
    assert ("IN-INR", "SUCCESS") in statuses
    assert ("JP-JPY", "FAILED") in statuses


def test_early_materialization_fault_is_persisted_then_retried(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "materialization-retry.sqlite")
    repository = SQLiteMarketRepository()
    registry = market_runtime.default_registry()
    cutoff = datetime(2026, 8, 9, 10, tzinfo=UTC)
    run = CollectorRun(
        "IN-INR",
        "rbi_official",
        CollectorRunStatus.SUCCESS,
        cutoff.isoformat(),
        cutoff.isoformat(),
        1,
        1,
        (cutoff + timedelta(days=1)).isoformat(),
    )
    calls = 0

    def flaky_materialize(market_id, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient local seal failure")
        return {"overview": f"{market_id}-overview", "gauge": f"{market_id}-gauge"}

    monkeypatch.setattr(market_runtime, "materialize_market", flaky_materialize)
    published: dict[str, object] = {}
    handler = market_runtime._completed_run_handler(
        repository=repository,
        registry=registry,
        backfill=False,
        materialize=True,
        record_forward=True,
        published_snapshots=published,
    )

    handler(run.to_dict())
    assert published == {}
    assert repository.latest_collector_runs("IN-INR")[0]["status"] == "SUCCESS"

    snapshots = market_runtime._materialize_after_runs(
        [run],
        repository=repository,
        registry=registry,
        cutoff=cutoff,
        record_forward=True,
        existing=published,
    )
    assert calls == 2
    assert snapshots["IN-INR"]["gauge"] == "IN-INR-gauge"
    assert snapshots["GLOBAL"]


def test_later_same_market_failure_invalidates_the_earlier_snapshot(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "same-market-retry.sqlite")
    repository = SQLiteMarketRepository()
    registry = market_runtime.default_registry()
    cutoff = datetime(2026, 8, 9, 10, tzinfo=UTC)
    runs = [
        CollectorRun(
            "IN-INR",
            adapter_id,
            CollectorRunStatus.SUCCESS,
            cutoff.isoformat(),
            cutoff.isoformat(),
            1,
            1,
            (cutoff + timedelta(days=1)).isoformat(),
        )
        for adapter_id in ("rbi_official", "licensed_inr_market")
    ]
    outcomes = iter(
        (
            {"gauge": "first"},
            RuntimeError("second adapter seal failed"),
            {"gauge": "cycle-boundary retry"},
        )
    )

    def materialize_sequence(_market_id, **_kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    markers = []
    monkeypatch.setattr(market_runtime, "materialize_market", materialize_sequence)
    monkeypatch.setattr(
        market_runtime,
        "_mark_backfill_complete",
        lambda market_id, adapter_id: markers.append((market_id, adapter_id)),
    )
    published: dict[str, object] = {}
    handler = market_runtime._completed_run_handler(
        repository=repository,
        registry=registry,
        backfill=True,
        materialize=True,
        record_forward=True,
        published_snapshots=published,
    )

    handler(runs[0].to_dict())
    assert published["IN-INR"] == {"gauge": "first"}
    assert markers == [("IN-INR", "rbi_official")]

    handler(runs[1].to_dict())
    assert "IN-INR" not in published
    assert ("IN-INR", "licensed_inr_market") not in markers

    snapshots = market_runtime._materialize_after_runs(
        runs,
        repository=repository,
        registry=registry,
        cutoff=cutoff,
        record_forward=True,
        existing=published,
    )
    assert snapshots["IN-INR"] == {"gauge": "cycle-boundary retry"}
