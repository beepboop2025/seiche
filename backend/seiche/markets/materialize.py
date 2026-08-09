"""Seal local gauges and the cross-basin tide from canonical observations.

This is a batch/runtime module, never a request-time dependency.  It selects
pack-declared instruments, invokes only universal semantic engines, applies a
versioned local calibration, and writes immutable products plus hash-chained
forward-validation records.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from seiche.collectors import cadence_delta
from seiche.domain.observation import (
    Observation,
    QualityState,
    SemanticRole,
    StalenessState,
)
from seiche.kernel.engines import (
    KernelResult,
    KernelStatus,
    MarketPanel,
    RoleSeries,
    corridor_position,
    cross_basin_coupling,
    facility_usage_pressure,
    funding_bill_wedge,
    liquidity_buffer_drain,
    policy_relative_overnight_pressure,
    secured_unsecured_wedge,
    tail_dislocation,
    term_funding_slope,
    volume_dislocation,
)
from seiche.markets.base import CapabilityStatus, MarketPack, PackSupportStatus
from seiche.markets.calibration import (
    ComponentCalibration,
    EngineKind,
    LocalCalibration,
    get_local_calibration,
)
from seiche.markets.publication import (
    PublicationStatus,
    decide_local_gauge_publication,
)
from seiche.markets.registry import MarketRegistry, default_registry
from seiche.repository import MarketRepository, get_repository


GLOBAL_TIDE_CALIBRATION_ID = "global-tide-coupling-forward-v1"
GLOBAL_TIDE_CALIBRATION_MATURITY = "FORWARD_ONLY"


def _utc(value: datetime | None) -> datetime:
    parsed = value or datetime.now(UTC)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("materialization cutoff must be timezone-aware")
    return parsed.astimezone(UTC).replace(microsecond=0)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _latest_runs(repository: MarketRepository, market_id: str) -> list[dict]:
    return repository.latest_collector_runs(market_id)


def _source_state(
    pack: MarketPack,
    instrument_id: str,
    runs: dict[str, dict],
    cutoff: datetime,
    fallback_knowledge: datetime,
) -> StalenessState:
    instrument = pack.instrument_map[instrument_id]
    adapter = pack.adapter_map[instrument.source_adapter_id]
    run = runs.get(adapter.adapter_id)
    if run is not None and run["status"] != "SUCCESS":
        return (
            StalenessState.DEAD
            if run["status"] == "CIRCUIT_OPEN"
            else StalenessState.STALE
        )
    reference = _parse_time(run["finished_at"]) if run is not None else fallback_knowledge
    age = max((cutoff - reference).total_seconds(), 0.0)
    cadence = cadence_delta(adapter.expected_cadence).total_seconds()
    if age <= cadence * 2:
        return StalenessState.FRESH
    if age <= cadence * 4:
        return StalenessState.AGING
    if age <= cadence * 8:
        return StalenessState.STALE
    return StalenessState.DEAD


def _age_observations(
    pack: MarketPack,
    observations: list[Observation],
    runs: list[dict],
    cutoff: datetime,
) -> list[Observation]:
    run_map = {item["adapter_id"]: item for item in runs}
    latest_knowledge: dict[str, datetime] = {}
    for observation in observations:
        latest_knowledge[observation.instrument_id] = max(
            observation.knowledge_time,
            latest_knowledge.get(observation.instrument_id, observation.knowledge_time),
        )
    return [
        replace(
            observation,
            staleness=_source_state(
                pack,
                observation.instrument_id,
                run_map,
                cutoff,
                latest_knowledge[observation.instrument_id],
            ),
        )
        for observation in observations
    ]


def _selected_panel(
    pack: MarketPack,
    observations: list[Observation],
) -> MarketPanel | None:
    """Select the first pack-declared populated instrument for each role.

    Pack order is the explicit priority mechanism.  It resolves cases such as
    official call money followed by a licensed fallback without teaching the
    universal kernel either instrument name.
    """

    populated = {item.instrument_id for item in observations if item.usable}
    chosen: dict[SemanticRole, str] = {}
    for instrument in pack.instruments:
        if instrument.instrument_id in populated:
            chosen.setdefault(instrument.semantic_role, instrument.instrument_id)
    selected = [
        item
        for item in observations
        if chosen.get(item.semantic_role) == item.instrument_id
    ]
    return MarketPanel.from_observations(selected) if selected else None


def _run_component(
    panel: MarketPanel,
    component: ComponentCalibration,
) -> KernelResult:
    if component.kind is EngineKind.POLICY_RELATIVE:
        return policy_relative_overnight_pressure(
            panel,
            overnight_role=component.overnight_role,  # type: ignore[arg-type]
            anchor_role=component.anchor_role,  # type: ignore[arg-type]
        )
    if component.kind is EngineKind.CORRIDOR:
        return corridor_position(
            panel,
            overnight_role=component.overnight_role,  # type: ignore[arg-type]
        )
    if component.kind is EngineKind.SECURED_UNSECURED:
        return secured_unsecured_wedge(panel)
    if component.kind is EngineKind.TERM_SLOPE:
        return term_funding_slope(
            panel,
            term_role=component.term_role,  # type: ignore[arg-type]
            overnight_role=component.overnight_role,  # type: ignore[arg-type]
        )
    if component.kind is EngineKind.FUNDING_BILL:
        return funding_bill_wedge(
            panel,
            funding_role=component.funding_role,  # type: ignore[arg-type]
        )
    if component.kind is EngineKind.LIQUIDITY_DRAIN:
        return liquidity_buffer_drain(
            panel,
            buffer_role=component.buffer_role,  # type: ignore[arg-type]
        )
    if component.kind is EngineKind.FACILITY_USAGE:
        return facility_usage_pressure(panel)
    if component.kind is EngineKind.TAIL_DISLOCATION:
        return tail_dislocation(panel)
    if component.kind is EngineKind.VOLUME_DISLOCATION:
        return volume_dislocation(panel)
    raise ValueError(f"unknown engine kind {component.kind!r}")


def _series(panel: MarketPanel, role: SemanticRole | None) -> pd.Series | None:
    if role is None:
        return None
    lookup = panel.lookup(role)
    return lookup.series.points if lookup.series is not None else None


def _aligned_series(*series: pd.Series | None) -> pd.DataFrame:
    if any(item is None for item in series):
        return pd.DataFrame()
    return pd.concat(series, axis=1, join="outer").sort_index().ffill().dropna()


def _component_history(
    panel: MarketPanel,
    component: ComponentCalibration,
) -> pd.Series:
    kind = component.kind
    if kind is EngineKind.POLICY_RELATIVE:
        frame = _aligned_series(
            _series(panel, component.overnight_role),
            _series(panel, component.anchor_role),
        )
        return frame.iloc[:, 0] - frame.iloc[:, 1] if not frame.empty else pd.Series(dtype=float)
    if kind is EngineKind.CORRIDOR:
        frame = _aligned_series(
            _series(panel, component.overnight_role),
            _series(panel, SemanticRole.POLICY_FLOOR),
            _series(panel, SemanticRole.POLICY_CEILING),
        )
        if frame.empty:
            return pd.Series(dtype=float)
        width = frame.iloc[:, 2] - frame.iloc[:, 1]
        return ((frame.iloc[:, 0] - frame.iloc[:, 1]) / width.replace(0, np.nan) * 100).dropna()
    if kind is EngineKind.SECURED_UNSECURED:
        frame = _aligned_series(
            _series(panel, SemanticRole.SECURED_OVERNIGHT),
            _series(panel, SemanticRole.UNSECURED_OVERNIGHT),
        )
        return frame.iloc[:, 0] - frame.iloc[:, 1] if not frame.empty else pd.Series(dtype=float)
    if kind is EngineKind.TERM_SLOPE:
        frame = _aligned_series(
            _series(panel, component.term_role),
            _series(panel, component.overnight_role),
        )
        return frame.iloc[:, 0] - frame.iloc[:, 1] if not frame.empty else pd.Series(dtype=float)
    if kind is EngineKind.FUNDING_BILL:
        frame = _aligned_series(
            _series(panel, component.funding_role),
            _series(panel, SemanticRole.TBILL_3M),
        )
        return frame.iloc[:, 0] - frame.iloc[:, 1] if not frame.empty else pd.Series(dtype=float)
    if kind is EngineKind.LIQUIDITY_DRAIN:
        points = _series(panel, component.buffer_role)
        return (-(points / points.shift(4) - 1) * 100).dropna() if points is not None else pd.Series(dtype=float)
    if kind is EngineKind.FACILITY_USAGE:
        frame = _aligned_series(
            _series(panel, SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP),
            _series(panel, SemanticRole.RESERVE_BALANCES),
        )
        if frame.empty:
            return pd.Series(dtype=float)
        return (frame.iloc[:, 0] / frame.iloc[:, 1].replace(0, np.nan) * 100).dropna()
    if kind is EngineKind.TAIL_DISLOCATION:
        frame = _aligned_series(
            _series(panel, SemanticRole.RATE_P99),
            _series(panel, SemanticRole.RATE_MEDIAN),
        )
        return frame.iloc[:, 0] - frame.iloc[:, 1] if not frame.empty else pd.Series(dtype=float)
    if kind is EngineKind.VOLUME_DISLOCATION:
        points = _series(panel, SemanticRole.REPO_VOLUME)
        if points is None:
            return pd.Series(dtype=float)
        values: list[float] = []
        indexes: list[pd.Timestamp] = []
        ordered = points.dropna().sort_index()
        for index, value in ordered.items():
            prior = ordered.loc[ordered.index < index]
            seasonal = prior[prior.index.weekday == index.weekday()]
            if len(seasonal) < 8 or float(seasonal.median()) <= 0:
                continue
            values.append((float(value) / float(seasonal.median()) - 1) * 100)
            indexes.append(index)
        return pd.Series(values, index=pd.DatetimeIndex(indexes), dtype=float)
    return pd.Series(dtype=float)


def _normalization(
    result: KernelResult,
    history: pd.Series,
    component: ComponentCalibration,
) -> dict[str, Any]:
    if result.value is None:
        return {
            "score": None,
            "method": "not_computed",
            "history_observations": len(history),
        }
    raw = float(result.value)
    directed_z = component.stress_direction * (raw - component.center) / component.scale
    declared_score = float(np.clip(50 + directed_z * 15, 0, 100))
    finite = history.replace([np.inf, -np.inf], np.nan).dropna().astype(float)
    if len(finite) < component.minimum_history:
        return {
            "score": round(declared_score, 4),
            "method": "declared_local_forward_calibration",
            "history_observations": len(finite),
            "minimum_history": component.minimum_history,
            "own_history_percentile": None,
            "robust_z": None,
            "center": component.center,
            "scale": component.scale,
            "stress_direction": component.stress_direction,
        }
    oriented = finite * component.stress_direction
    current = raw * component.stress_direction
    percentile = float((oriented <= current).sum()) / len(oriented) * 100
    center = float(oriented.median())
    mad = float((oriented - center).abs().median())
    robust_z = (current - center) / (1.4826 * mad) if mad > 0 else directed_z
    history_score = float(np.clip(50 + robust_z * 15, 0, 100))
    score = percentile * 0.6 + history_score * 0.4
    return {
        "score": round(score, 4),
        "method": "point_in_time_own_history",
        "history_observations": len(finite),
        "minimum_history": component.minimum_history,
        "own_history_percentile": round(percentile, 4),
        "robust_z": round(float(robust_z), 4),
        "center": component.center,
        "scale": component.scale,
        "stress_direction": component.stress_direction,
    }


def _faults(runs: list[dict], market_id: str) -> list[dict]:
    return [
        {
            "market_id": market_id,
            "source": item["adapter_id"],
            "status": item["status"],
            "detail": item.get("fault"),
            "finished_at": item["finished_at"],
            "next_due": item["next_due"],
        }
        for item in runs
        if item["status"] != "SUCCESS"
    ]


def _effective_knowledge(
    observations: list[Observation],
    runs: list[dict],
    fallback: datetime,
) -> datetime:
    candidates = [item.knowledge_time for item in observations]
    candidates.extend(_parse_time(item["finished_at"]) for item in runs)
    return min(max(candidates, default=fallback), fallback)


def _capabilities(
    pack: MarketPack,
    results: dict[str, KernelResult],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    matrix = {
        item.capability_id: item.status.value
        for item in pack.capabilities
    }
    reasons = {
        item.capability_id: item.reason
        for item in pack.capabilities
    }
    for component_id, result in results.items():
        matrix[component_id] = result.status.value
        reasons[component_id] = result.reason
    missing = [
        {
            "capability": name,
            "status": status,
            "reason": reasons.get(name),
        }
        for name, status in sorted(matrix.items())
        if status != CapabilityStatus.READY.value
    ]
    return matrix, missing


def _stale_inputs(panel: MarketPanel | None) -> list[dict]:
    if panel is None:
        return []
    return [
        {
            "instrument_id": item.instrument_id,
            "semantic_role": item.role.value,
            "event_time": item.latest.event_time.isoformat(),
            "knowledge_time": item.latest.knowledge_time.isoformat(),
            "staleness": item.latest.staleness.value,
        }
        for item in panel.series
        if item.latest.staleness not in {StalenessState.FRESH, StalenessState.AGING}
    ]


def _index_regime(value: float | None) -> str | None:
    if value is None:
        return None
    if value < 35:
        return "CALM"
    if value < 50:
        return "WATCH"
    if value < 65:
        return "STRAIN"
    if value < 80:
        return "STRESS"
    return "SEVERE"


def build_local_products(
    pack: MarketPack,
    calibration: LocalCalibration,
    observations: list[Observation],
    runs: list[dict],
    knowledge_limit: datetime,
    repository: MarketRepository,
) -> tuple[dict, dict]:
    aged = _age_observations(pack, observations, runs, knowledge_limit)
    panel = _selected_panel(pack, aged)
    results: dict[str, KernelResult] = {}
    components: list[dict[str, Any]] = []
    if panel is not None:
        for definition in calibration.components:
            result = _run_component(panel, definition)
            results[definition.component_id] = result
            normalized = _normalization(
                result,
                _component_history(panel, definition),
                definition,
            )
            components.append(
                {
                    "component_id": definition.component_id,
                    "required": definition.required,
                    "weight": definition.weight,
                    "kernel": result.to_dict(),
                    "normalization": normalized,
                }
            )
    decision = decide_local_gauge_publication(
        results,
        calibration.required_components,
    )
    mature_required = all(
        item["normalization"].get("method") == "point_in_time_own_history"
        for item in components
        if item["required"]
    )
    weighted = [
        (
            float(item["normalization"]["score"]),
            float(item["weight"]),
        )
        for item in components
        if item["normalization"].get("score") is not None
        and item["kernel"]["status"] in {KernelStatus.READY.value, KernelStatus.STALE.value}
    ]
    index = (
        sum(value * weight for value, weight in weighted)
        / sum(weight for _, weight in weighted)
        if decision.publish_value and weighted
        else None
    )
    status = decision.status
    reason = decision.reason
    if status is PublicationStatus.READY and not mature_required:
        status = PublicationStatus.DEGRADED
        reason = "required engines are ready; own-history calibration is still accruing"

    effective_knowledge = _effective_knowledge(aged, runs, knowledge_limit)
    event_candidates = [item.event_time for item in aged]
    event_cutoff = max(event_candidates, default=None)
    capabilities, missing = _capabilities(pack, results)
    faults = _faults(runs, pack.market_id)
    stale = _stale_inputs(panel)
    eligibility_reasons = []
    if pack.support_status is not PackSupportStatus.SUPPORTED:
        eligibility_reasons.append("pack validation status is not SUPPORTED")
    if calibration.maturity != "VALIDATED":
        eligibility_reasons.append("calibration is forward-only")
    if not mature_required:
        eligibility_reasons.append("minimum own-history calibration has not accrued")
    if faults:
        eligibility_reasons.append("one or more pack collectors are faulted")
    if stale:
        eligibility_reasons.append("one or more selected inputs are stale")
    if any(item.quality is QualityState.ESTIMATED for item in aged):
        eligibility_reasons.append("one or more publication clocks are estimated")
    eligible = not eligibility_reasons and index is not None
    coverage = repository.canonical_coverage(pack.market_id)
    common = {
        "market_id": pack.market_id,
        "monetary_area_id": pack.monetary_area_id,
        "jurisdiction_codes": list(pack.jurisdiction_codes),
        "currency": pack.currency,
        "policy_regime": pack.policy_regime.value,
        "support_status": pack.support_status.value,
        "data_coverage": {
            "canonical_observations": coverage,
            "selected_roles": len(panel.series) if panel is not None else 0,
            "declared_roles": len({item.semantic_role for item in pack.instruments}),
        },
        "capabilities": capabilities,
        "missing_capabilities": missing,
        "calibration_id": calibration.calibration_id,
        "calibration_maturity": calibration.maturity,
        "evidence_eligibility": {
            "eligible": eligible,
            "reasons": eligibility_reasons,
        },
        "event_cutoff": event_cutoff.isoformat() if event_cutoff else None,
        "knowledge_cutoff": effective_knowledge.isoformat(),
        "faults": faults,
        "stale_inputs": stale,
    }
    gauge = {
        "schema": "seiche.local-gauge.v2",
        "product": "LOCAL_SEICHE_GAUGE",
        "status": status.value,
        **common,
        "reading": {
            "index": round(index, 4) if index is not None and math.isfinite(index) else None,
            "regime": _index_regime(index),
            "publication_reason": reason,
        },
        "components": components,
        "calendar": pack.settlement_calendar.summary()
        if hasattr(pack.settlement_calendar, "summary")
        else {
            "calendar_id": pack.settlement_calendar.calendar_id,
            "valid_from_year": pack.settlement_calendar.valid_from_year,
            "valid_to_year": pack.settlement_calendar.valid_to_year,
            "source": pack.settlement_calendar.source_uri,
        },
        "notes": "Raw levels are never compared across markets; every score is locally oriented.",
    }
    overview = {
        "schema": "seiche.market-overview.v2",
        "product": "LOCAL_MARKET_OVERVIEW",
        "status": status.value,
        **common,
        "gauge": gauge["reading"],
        "component_summary": [
            {
                "component_id": item["component_id"],
                "status": item["kernel"]["status"],
                "score": item["normalization"].get("score"),
            }
            for item in components
        ],
        "collector_runs": runs,
        "calendar": gauge["calendar"],
    }
    return overview, gauge


def materialize_market(
    market_id: str,
    *,
    repository: MarketRepository | None = None,
    registry: MarketRegistry | None = None,
    knowledge_time: datetime | None = None,
    record_forward: bool = True,
) -> dict[str, str]:
    repo = repository or get_repository()
    markets = registry or default_registry()
    pack = markets.get(market_id)
    calibration = get_local_calibration(pack.market_id)
    cutoff = _utc(knowledge_time)
    observations = repo.load_observations_as_of(
        pack.market_id,
        cutoff,
        event_time=cutoff,
    )
    runs = _latest_runs(repo, pack.market_id)
    overview, gauge = build_local_products(
        pack,
        calibration,
        observations,
        runs,
        cutoff,
        repo,
    )
    seal_event = overview["event_cutoff"] or overview["knowledge_cutoff"]
    ids = {
        "overview": repo.seal_market_snapshot(
            market_id=pack.market_id,
            product="overview",
            event_cutoff=seal_event,
            knowledge_cutoff=overview["knowledge_cutoff"],
            calibration_id=calibration.calibration_id,
            evidence_eligible=overview["evidence_eligibility"]["eligible"],
            payload=overview,
        ),
        "gauge": repo.seal_market_snapshot(
            market_id=pack.market_id,
            product="gauge",
            event_cutoff=seal_event,
            knowledge_cutoff=gauge["knowledge_cutoff"],
            calibration_id=calibration.calibration_id,
            evidence_eligible=gauge["evidence_eligibility"]["eligible"],
            payload=gauge,
        ),
    }
    if record_forward:
        for product, snapshot_id in ids.items():
            payload = overview if product == "overview" else gauge
            repo.append_forward_record(
                snapshot_id=snapshot_id,
                market_id=pack.market_id,
                product=product,
                event_cutoff=seal_event,
                knowledge_cutoff=payload["knowledge_cutoff"],
                calibration_id=calibration.calibration_id,
                payload=payload,
            )
    return ids


def _fx_series(
    pack: MarketPack,
    observations: list[Observation],
) -> RoleSeries | None:
    panel = _selected_panel(pack, observations)
    if panel is None:
        return None
    return panel.lookup(SemanticRole.FX_SWAP_BASIS).series


def materialize_global_tide(
    *,
    repository: MarketRepository | None = None,
    registry: MarketRegistry | None = None,
    knowledge_time: datetime | None = None,
    record_forward: bool = True,
) -> str:
    repo = repository or get_repository()
    markets = registry or default_registry()
    cutoff = _utc(knowledge_time)
    series_by_market: dict[str, RoleSeries] = {}
    coverage: list[dict[str, Any]] = []
    all_observations: list[Observation] = []
    faults: list[dict[str, Any]] = []
    for pack in markets.list():
        observations = repo.load_observations_as_of(
            pack.market_id,
            cutoff,
            event_time=cutoff,
            roles=(SemanticRole.FX_SWAP_BASIS,),
        )
        runs = _latest_runs(repo, pack.market_id)
        aged = _age_observations(pack, observations, runs, cutoff)
        all_observations.extend(aged)
        series = _fx_series(pack, aged) if aged else None
        if series is not None:
            series_by_market[pack.market_id] = series
        fx_adapter_ids = {
            instrument.source_adapter_id
            for instrument in pack.instruments
            if instrument.semantic_role is SemanticRole.FX_SWAP_BASIS
        }
        faults.extend(
            _faults(
                [run for run in runs if run["adapter_id"] in fx_adapter_ids],
                pack.market_id,
            )
        )
        coverage.append(
            {
                "market_id": pack.market_id,
                "currency": pack.currency,
                "fx_swap_basis_observations": len(series.points) if series else 0,
                "status": (
                    "STALE" if series is not None and series.stale
                    else "READY" if series is not None
                    else "UNAVAILABLE"
                ),
            }
        )
    result = cross_basin_coupling(series_by_market)
    status = (
        "READY"
        if result.status is KernelStatus.READY
        else "DEGRADED"
        if result.status is KernelStatus.STALE
        else "UNAVAILABLE"
    )
    # The tide is a newly materialized cross-market claim. Its knowledge clock
    # is the materialization cutoff, not merely the newest constituent row;
    # otherwise a later unavailable seal could permanently sort ahead of a
    # newly computable tide built from older-but-now-ingested history.
    knowledge_cutoff = cutoff
    # A READY tide is calculated only through its last shared business-date
    # session. A newer constituent observation from one market was not used
    # and must not advance the product's published event cutoff.
    event_cutoff = (
        datetime.fromisoformat(result.event_cutoff)
        if result.event_cutoff is not None
        else max((item.event_time for item in all_observations), default=None)
    )
    unvalidated_markets = sorted(
        market_id
        for market_id in series_by_market
        if markets.get(market_id).support_status is not PackSupportStatus.SUPPORTED
    )
    eligibility_reasons = []
    if result.status is not KernelStatus.READY:
        eligibility_reasons.append("cross-basin coupling is not READY")
    if unvalidated_markets:
        eligibility_reasons.append(
            "constituent packs are not SUPPORTED: " + ", ".join(unvalidated_markets)
        )
    if GLOBAL_TIDE_CALIBRATION_MATURITY != "VALIDATED":
        eligibility_reasons.append("global calibration is forward-only")
    if faults:
        eligibility_reasons.append("one or more FX-basis collectors are faulted")
    eligible = not eligibility_reasons
    missing = []
    if result.status is not KernelStatus.READY:
        missing.append(
            {
                "capability": "cross_basin_coupling",
                "status": result.status.value,
                "reason": result.reason,
            }
        )
    payload = {
        "schema": "seiche.global-tide.v2",
        "product": "GLOBAL_SEICHE_TIDE",
        "status": status,
        "market_id": "GLOBAL",
        "monetary_area_id": None,
        "jurisdiction_codes": [],
        "currency": None,
        "policy_regime": None,
        "data_coverage": coverage,
        "capabilities": {"cross_basin_coupling": result.status.value},
        "missing_capabilities": missing,
        "calibration_id": GLOBAL_TIDE_CALIBRATION_ID,
        "calibration_maturity": GLOBAL_TIDE_CALIBRATION_MATURITY,
        "evidence_eligibility": {
            "eligible": eligible,
            "reasons": eligibility_reasons,
        },
        "event_cutoff": event_cutoff.isoformat() if event_cutoff else None,
        "knowledge_cutoff": knowledge_cutoff.isoformat(),
        "faults": faults,
        "stale_inputs": [
            {
                "market_id": market_id,
                "instrument_id": series.instrument_id,
                "event_time": series.latest.event_time.isoformat(),
                "knowledge_time": series.latest.knowledge_time.isoformat(),
                "staleness": series.latest.staleness.value,
            }
            for market_id, series in sorted(series_by_market.items())
            if series.stale
        ],
        "reading": {
            "value": result.value,
            "synchronization_index": result.value,
            "unit": result.unit,
        },
        "components": [result.to_dict()],
        "notes": "Local gauges are never averaged into the Global Tide.",
    }
    seal_event = payload["event_cutoff"] or payload["knowledge_cutoff"]
    snapshot_id = repo.seal_market_snapshot(
        market_id="GLOBAL",
        product="tide",
        event_cutoff=seal_event,
        knowledge_cutoff=payload["knowledge_cutoff"],
        calibration_id=GLOBAL_TIDE_CALIBRATION_ID,
        evidence_eligible=eligible,
        payload=payload,
    )
    if record_forward:
        repo.append_forward_record(
            snapshot_id=snapshot_id,
            market_id="GLOBAL",
            product="tide",
            event_cutoff=seal_event,
            knowledge_cutoff=payload["knowledge_cutoff"],
            calibration_id=GLOBAL_TIDE_CALIBRATION_ID,
            payload=payload,
        )
    return snapshot_id
