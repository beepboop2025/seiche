from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
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
from seiche.kernel.engines import RoleSeries, cross_basin_coupling
from seiche.markets.base import MarketPack
from seiche.markets.china_cny import PACK as CHINA_PACK
from seiche.markets.india_inr import PACK as INDIA_PACK
from seiche.markets.materialize import (
    PUBLIC_SNAPSHOT_VISIBILITY,
    materialize_global_tide,
    materialize_market,
)
from seiche.markets.reference import rate_instrument
from seiche.markets.registry import MarketRegistry
from seiche.markets.singapore_sgd import PACK as SINGAPORE_PACK
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


def _registry_with_non_derivable_instruments() -> tuple[MarketRegistry, MarketPack]:
    tenant_call = rate_instrument(
        "IN.TENANT.CALL",
        "TENANT_CALL",
        SemanticRole.UNSECURED_OVERNIGHT,
        "tenant_market_data",
        DayCountConvention.ACT_365,
    )
    tenant_fx = rate_instrument(
        "IN.TENANT.FX_BASIS",
        "TENANT_FX_BASIS",
        SemanticRole.FX_SWAP_BASIS,
        "tenant_market_data",
        DayCountConvention.ACT_365,
    )
    tenant_tail = rate_instrument(
        "IN.TENANT.RATE_P99",
        "TENANT_RATE_P99",
        SemanticRole.RATE_P99,
        "tenant_market_data",
        DayCountConvention.ACT_365,
    )
    row_prohibited_fx = rate_instrument(
        "IN.ROW_PROHIBITED.FX_BASIS",
        "ROW_PROHIBITED_FX_BASIS",
        SemanticRole.FX_SWAP_BASIS,
        "licensed_inr_market",
        DayCountConvention.ACT_365,
    )
    metadata_call = rate_instrument(
        "IN.METADATA.CALL",
        "METADATA_CALL",
        SemanticRole.UNSECURED_OVERNIGHT,
        "cfets_rates",
        DayCountConvention.ACT_365,
    )
    metadata_fx = rate_instrument(
        "IN.METADATA.FX_BASIS",
        "METADATA_FX_BASIS",
        SemanticRole.FX_SWAP_BASIS,
        "cfets_rates",
        DayCountConvention.ACT_365,
    )
    india = replace(
        INDIA_PACK,
        source_adapters=(
            *INDIA_PACK.source_adapters,
            CHINA_PACK.adapter_map["cfets_rates"],
        ),
        instruments=(
            tenant_call,
            tenant_fx,
            tenant_tail,
            row_prohibited_fx,
            metadata_call,
            metadata_fx,
            *INDIA_PACK.instruments,
        ),
    )
    return MarketRegistry((india, SINGAPORE_PACK)), india


def _save_non_derivable_runs(cutoff: datetime) -> None:
    store.save_collector_run(
        {
            "market_id": "IN-INR",
            "adapter_id": "tenant_market_data",
            "status": "FAILED",
            "started_at": (cutoff - timedelta(minutes=2)).isoformat(),
            "finished_at": (cutoff - timedelta(minutes=1)).isoformat(),
            "observations_written": 2,
            "attempts": 1,
            "next_due": (cutoff + timedelta(days=1)).isoformat(),
            "fault": "tenant-run-secret",
        }
    )
    store.save_collector_run(
        {
            "market_id": "IN-INR",
            "adapter_id": "cfets_rates",
            "status": "FAILED",
            "started_at": (cutoff - timedelta(minutes=4)).isoformat(),
            "finished_at": (cutoff - timedelta(minutes=3)).isoformat(),
            "observations_written": 2,
            "attempts": 1,
            "next_due": (cutoff + timedelta(days=1)).isoformat(),
            "fault": "metadata-run-secret",
        }
    )


def _assert_non_derivable_metadata_absent(
    payload: dict, rows: list[Observation]
) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    forbidden = {
        "tenant_market_data",
        "tenant-run-secret",
        "cfets_rates",
        "metadata-run-secret",
        *(item.instrument_id for item in rows),
        *(item.source for item in rows),
        *(item.event_time.isoformat() for item in rows),
        *(item.knowledge_time.isoformat() for item in rows),
        *(item.evidence_hash for item in rows),
        *(str(float(item.value)) for item in rows if item.value is not None),
    }
    assert not [item for item in forbidden if item in serialized]


def test_local_snapshots_filter_non_derivable_rows_instruments_and_runs(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "local-public.sqlite")
    repository = SQLiteMarketRepository()
    registry, india = _registry_with_non_derivable_instruments()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    cutoff = start + timedelta(days=100)
    public_rows = [
        _rate(
            market_id="IN-INR",
            instrument_id=instrument_id,
            role=role,
            value=value,
            event_time=start,
        )
        for instrument_id, role, value in (
            ("IN.RBI.SDF", SemanticRole.POLICY_FLOOR, 525),
            ("IN.RBI.POLICY_REPO", SemanticRole.POLICY_TARGET, 550),
            ("IN.RBI.MSF", SemanticRole.POLICY_CEILING, 575),
        )
    ]
    public_rows.extend(
        _rate(
            market_id="IN-INR",
            instrument_id="IN.FBIL.MIBOR",
            role=SemanticRole.UNSECURED_OVERNIGHT,
            value=548 + offset % 4,
            event_time=start + timedelta(days=offset),
            source="licensed-public",
            classification=ConnectorClassification.LICENSED,
            redistribution=RedistributionStatus.DERIVED_ONLY,
        )
        for offset in range(80)
    )
    store.save_observations(public_rows)
    _save_success_run("IN-INR", "rbi_official", cutoff)
    _save_success_run("IN-INR", "licensed_inr_market", cutoff)

    baseline_ids = materialize_market(
        "IN-INR",
        repository=repository,
        registry=registry,
        knowledge_time=cutoff,
        record_forward=False,
    )
    baseline_overview = store.load_latest_market_snapshot("IN-INR", "overview")[
        "payload"
    ]
    baseline_gauge = store.load_latest_market_snapshot("IN-INR", "gauge")["payload"]
    assert baseline_gauge["reading"]["index"] is not None

    excluded_rows = [
        _rate(
            market_id="IN-INR",
            instrument_id="IN.TENANT.CALL",
            role=SemanticRole.UNSECURED_OVERNIGHT,
            value=987654.25,
            event_time=start + timedelta(days=90),
            source="tenant-instrument-secret",
            classification=ConnectorClassification.TENANT_PROVIDED,
            redistribution=RedistributionStatus.ALLOWED,
        ),
        _rate(
            market_id="IN-INR",
            instrument_id="IN.FBIL.MIBOR",
            role=SemanticRole.UNSECURED_OVERNIGHT,
            value=876543.25,
            event_time=start + timedelta(days=91),
            source="row-policy-secret",
            classification=ConnectorClassification.LICENSED,
            redistribution=RedistributionStatus.PROHIBITED,
        ),
        _rate(
            market_id="IN-INR",
            instrument_id="IN.TENANT.RATE_P99",
            role=SemanticRole.RATE_P99,
            value=765432.5,
            event_time=start + timedelta(days=92),
            source="tenant-role-secret",
            classification=ConnectorClassification.TENANT_PROVIDED,
            redistribution=RedistributionStatus.ALLOWED,
        ),
        _rate(
            market_id="IN-INR",
            instrument_id="IN.METADATA.CALL",
            role=SemanticRole.UNSECURED_OVERNIGHT,
            value=654320.5,
            event_time=start + timedelta(days=93),
            source="metadata-only-secret",
            classification=ConnectorClassification.OFFICIAL_OPEN,
            redistribution=RedistributionStatus.METADATA_ONLY,
        ),
    ]
    store.save_observations(excluded_rows)
    _save_non_derivable_runs(cutoff)

    filtered_ids = materialize_market(
        "IN-INR",
        repository=repository,
        registry=registry,
        knowledge_time=cutoff,
        record_forward=False,
    )
    filtered_overview = store.load_latest_market_snapshot("IN-INR", "overview")[
        "payload"
    ]
    filtered_gauge = store.load_latest_market_snapshot("IN-INR", "gauge")["payload"]

    assert filtered_ids == baseline_ids
    assert filtered_overview == baseline_overview
    assert filtered_gauge == baseline_gauge
    assert filtered_overview["visibility"] == PUBLIC_SNAPSHOT_VISIBILITY
    assert filtered_gauge["visibility"] == PUBLIC_SNAPSHOT_VISIBILITY
    unsecured_coverage = next(
        item
        for item in filtered_gauge["data_coverage"]["canonical_observations"]
        if item["semantic_role"] == SemanticRole.UNSECURED_OVERNIGHT.value
    )
    assert unsecured_coverage["observations"] == 80
    public_declared_roles = {
        item.semantic_role
        for item in india.instruments
        if india.adapter_map[item.source_adapter_id].redistribution_status
        in {RedistributionStatus.ALLOWED, RedistributionStatus.DERIVED_ONLY}
    }
    assert filtered_gauge["data_coverage"]["declared_roles"] == len(
        public_declared_roles
    )
    assert SemanticRole.RATE_P99.value not in {
        item["semantic_role"]
        for item in filtered_gauge["data_coverage"]["canonical_observations"]
    }
    assert {item["adapter_id"] for item in filtered_overview["collector_runs"]} == {
        "rbi_official",
        "licensed_inr_market",
    }
    for payload in (filtered_overview, filtered_gauge):
        _assert_non_derivable_metadata_absent(payload, excluded_rows)


def test_global_tide_filters_non_derivable_inputs_but_keeps_derived_only_aggregation(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "tide-public.sqlite")
    repository = SQLiteMarketRepository()
    registry, _ = _registry_with_non_derivable_instruments()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    cutoff = start + timedelta(days=100)
    public_rows = []
    for offset in range(80):
        event_time = start + timedelta(days=offset)
        for market_id, instrument_id, multiplier in (
            ("IN-INR", "IN.MARKET.FX_FORWARD_BASIS", 1.0),
            ("SG-SGD", "SG.MARKET.FX_SWAP_BASIS", 0.65),
        ):
            public_rows.append(
                _rate(
                    market_id=market_id,
                    instrument_id=instrument_id,
                    role=SemanticRole.FX_SWAP_BASIS,
                    value=(-25 + offset * 0.25 + (offset % 7) * 0.11) * multiplier,
                    event_time=event_time,
                    source="licensed-public",
                    classification=ConnectorClassification.LICENSED,
                    redistribution=RedistributionStatus.DERIVED_ONLY,
                )
            )
    store.save_observations(public_rows)
    _save_success_run("IN-INR", "licensed_inr_market", cutoff)
    _save_success_run("SG-SGD", "licensed_sgd_market", cutoff)

    baseline_id = materialize_global_tide(
        repository=repository,
        registry=registry,
        knowledge_time=cutoff,
        record_forward=False,
    )
    baseline = store.load_latest_market_snapshot("GLOBAL", "tide")["payload"]
    assert baseline["status"] == "READY"
    assert baseline["reading"]["value"] is not None

    excluded_rows = [
        _rate(
            market_id="IN-INR",
            instrument_id="IN.TENANT.FX_BASIS",
            role=SemanticRole.FX_SWAP_BASIS,
            value=765432.25,
            event_time=start + timedelta(days=90),
            source="tenant-fx-secret",
            classification=ConnectorClassification.TENANT_PROVIDED,
            redistribution=RedistributionStatus.ALLOWED,
        ),
        _rate(
            market_id="IN-INR",
            instrument_id="IN.ROW_PROHIBITED.FX_BASIS",
            role=SemanticRole.FX_SWAP_BASIS,
            value=654321.25,
            event_time=start + timedelta(days=91),
            source="row-fx-secret",
            classification=ConnectorClassification.LICENSED,
            redistribution=RedistributionStatus.PROHIBITED,
        ),
        _rate(
            market_id="IN-INR",
            instrument_id="IN.METADATA.FX_BASIS",
            role=SemanticRole.FX_SWAP_BASIS,
            value=543210.25,
            event_time=start + timedelta(days=92),
            source="metadata-fx-secret",
            classification=ConnectorClassification.OFFICIAL_OPEN,
            redistribution=RedistributionStatus.METADATA_ONLY,
        ),
    ]
    store.save_observations(excluded_rows)
    _save_non_derivable_runs(cutoff)

    filtered_id = materialize_global_tide(
        repository=repository,
        registry=registry,
        knowledge_time=cutoff,
        record_forward=False,
    )
    filtered = store.load_latest_market_snapshot("GLOBAL", "tide")["payload"]

    assert filtered_id == baseline_id
    assert filtered == baseline
    assert filtered["visibility"] == PUBLIC_SNAPSHOT_VISIBILITY
    assert {
        item["market_id"]: item["fx_swap_basis_observations"]
        for item in filtered["data_coverage"]
    } == {"IN-INR": 80, "SG-SGD": 80}
    _assert_non_derivable_metadata_absent(filtered, excluded_rows)


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


def test_worker_cycle_isolates_bad_market_from_sibling_and_global(
    monkeypatch, caplog
) -> None:
    cutoff = datetime(2026, 8, 14, 10, tzinfo=UTC)
    runs = [
        CollectorRun(
            market_id,
            adapter_id,
            CollectorRunStatus.SUCCESS,
            cutoff.isoformat(),
            cutoff.isoformat(),
            1,
            1,
            (cutoff + timedelta(days=1)).isoformat(),
        )
        for market_id, adapter_id in (
            ("IN-INR", "rbi_official"),
            ("JP-JPY", "boj_rates"),
        )
    ]
    calls = []

    def materialize_local(market_id, **_kwargs):
        calls.append(market_id)
        if market_id == "IN-INR":
            raise ValueError("postgresql://private-user:private-password@db/seiche")
        return {"gauge": "japan-healthy"}

    def materialize_global(**_kwargs):
        calls.append("GLOBAL")
        return {"tide": "global-healthy"}

    monkeypatch.setattr(market_runtime, "materialize_market", materialize_local)
    monkeypatch.setattr(market_runtime, "materialize_global_tide", materialize_global)
    faulted_markets: set[str] = set()

    snapshots = market_runtime._materialize_after_runs(
        runs,
        repository=object(),
        registry=market_runtime.default_registry(),
        cutoff=cutoff,
        record_forward=True,
        faulted_markets=faulted_markets,
    )

    assert calls == ["IN-INR", "JP-JPY", "GLOBAL"]
    assert snapshots == {
        "JP-JPY": {"gauge": "japan-healthy"},
        "GLOBAL": {"tide": "global-healthy"},
    }
    assert faulted_markets == {"IN-INR"}
    assert "market_id=IN-INR fault_type=ValueError" in caplog.text
    assert "private-password" not in caplog.text


def test_one_shot_cycle_boundary_materialization_remains_strict(
    monkeypatch,
) -> None:
    cutoff = datetime(2026, 8, 14, 10, tzinfo=UTC)
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
    global_calls = []
    monkeypatch.setattr(
        market_runtime,
        "materialize_market",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("strict forward-chain failure")
        ),
    )
    monkeypatch.setattr(
        market_runtime,
        "materialize_global_tide",
        lambda **_kwargs: global_calls.append("GLOBAL"),
    )

    with pytest.raises(ValueError, match="strict forward-chain failure"):
        market_runtime._materialize_after_runs(
            [run],
            repository=object(),
            registry=market_runtime.default_registry(),
            cutoff=cutoff,
            record_forward=True,
        )

    assert global_calls == []


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
