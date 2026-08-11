"""Read-only, evidence-producing validation for monetary-area packs.

The runner deliberately never mutates a pack, seals a snapshot, or promotes a
market.  It evaluates gates against one point-in-time repository view and
commits one content-addressed artifact per check.  Checks which need evidence
that does not yet exist are ``PENDING`` rather than being omitted or treated as
successful.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from seiche.domain.observation import Observation, SemanticRole
from seiche.markets.base import (
    CapabilityStatus,
    MarketPack,
    REQUIRED_VALIDATION_CHECKS,
    ValidationCheck,
    ValidationOutcome,
    ValidationResult,
)
from seiche.markets.calibration import (
    ComponentCalibration,
    EngineKind,
    LocalCalibration,
    get_local_calibration,
)
from seiche.markets.materialize import build_local_products
from seiche.markets.registry import MarketRegistry, default_registry
from seiche.markets.validation_calendar import assess_calendar_and_timezone
from seiche.markets.validation_evidence import (
    ValidationEvidenceArtifact,
    ValidationEvidenceStore,
    ValidationStatus,
    input_fingerprint_for,
)
from seiche.markets.validation_forward import verify_repository_forward_chain
from seiche.repository import MarketRepository, get_repository


VALIDATION_RUNNER_ID = "market-validate"
VALIDATION_RUNNER_VERSION = "market-validation-policy-v2"


@dataclass(frozen=True, slots=True)
class GateAssessment:
    check: ValidationCheck
    status: ValidationStatus
    metrics: Mapping[str, object]
    reasons: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status is not ValidationStatus.PASS and not self.reasons:
            raise ValueError("non-passing validation assessments require a reason")


@dataclass(frozen=True, slots=True)
class MarketValidationReport:
    market_id: str
    calibration_id: str
    generated_at: datetime
    artifacts: tuple[ValidationEvidenceArtifact, ...]

    @property
    def exit_code(self) -> int:
        if any(item.status is ValidationStatus.FAIL for item in self.artifacts):
            return 1
        if any(item.status is ValidationStatus.PENDING for item in self.artifacts):
            return 2
        return 0

    def to_dict(self) -> dict[str, object]:
        statuses = {
            status.value: [
                item.check.value for item in self.artifacts if item.status is status
            ]
            for status in ValidationStatus
        }
        return {
            "schema": "seiche.market-validation-report.v1",
            "market_id": self.market_id,
            "calibration_id": self.calibration_id,
            "generated_at": self.generated_at.isoformat(),
            "checks": [item.to_dict() for item in self.artifacts],
            "summary": {
                "passed": statuses[ValidationStatus.PASS.value],
                "failed": statuses[ValidationStatus.FAIL.value],
                "pending": statuses[ValidationStatus.PENDING.value],
                "promotion_evidence_complete": self.exit_code == 0,
            },
        }


def _pack_contract_payload(
    pack: MarketPack,
    calibration: LocalCalibration,
) -> dict[str, object]:
    """Return the stable, JSON-only part of a pack used by the engines."""

    def calendar_payload(calendar) -> dict[str, object]:
        return {
            "calendar_id": calendar.calendar_id,
            "timezone_name": calendar.timezone_name,
            "weekend_days": sorted(calendar.weekend_days),
            "valid_from_year": calendar.valid_from_year,
            "valid_to_year": calendar.valid_to_year,
            "source_uri": calendar.source_uri,
            "holiday_provider": (
                f"{calendar.holiday_provider.__module__}."
                f"{calendar.holiday_provider.__qualname__}"
                if calendar.holiday_provider is not None
                else None
            ),
            "working_day_provider": (
                f"{calendar.working_day_provider.__module__}."
                f"{calendar.working_day_provider.__qualname__}"
                if calendar.working_day_provider is not None
                else None
            ),
        }

    return {
        "market_id": pack.market_id,
        "monetary_area_id": pack.monetary_area_id,
        "jurisdiction_codes": list(pack.jurisdiction_codes),
        "currency": pack.currency,
        "local_timezone": pack.local_timezone,
        "policy_regime": pack.policy_regime.value,
        "calibration_id": pack.calibration_id,
        "minimum_history": {
            "observations": pack.minimum_history.observations,
            "span_days": pack.minimum_history.span_days,
        },
        "holiday_calendar": calendar_payload(pack.holiday_calendar),
        "settlement_calendar": calendar_payload(pack.settlement_calendar),
        "instruments": [
            {
                "instrument_id": item.instrument_id,
                "semantic_role": item.semantic_role.value,
                "source_adapter_id": item.source_adapter_id,
                "source_unit": item.source_unit,
                "canonical_unit": item.canonical_unit.value,
                "value_multiplier": str(item.value_multiplier),
                "rate_compounding": (
                    item.rate_compounding.value if item.rate_compounding else None
                ),
                "day_count": item.day_count.value if item.day_count else None,
            }
            for item in pack.instruments
        ],
        "adapters": [
            {
                "adapter_id": item.adapter_id,
                "classification": item.classification.value,
                "expected_cadence": item.expected_cadence,
                "redistribution_status": item.redistribution_status.value,
                "publication_clock": {
                    "timezone_name": item.publication_clock.timezone_name,
                    "local_time": (
                        item.publication_clock.local_time.isoformat()
                        if item.publication_clock.local_time
                        else None
                    ),
                    "business_day_lag": item.publication_clock.business_day_lag,
                    "precision": item.publication_clock.precision.value,
                    "calendar_id": item.publication_clock.calendar_id,
                },
            }
            for item in pack.source_adapters
        ],
        "capabilities": [
            {
                "capability_id": item.capability_id,
                "status": item.status.value,
                "required_roles": sorted(role.value for role in item.required_roles),
                "minimum_history": (
                    {
                        "observations": item.minimum_history.observations,
                        "span_days": item.minimum_history.span_days,
                    }
                    if item.minimum_history
                    else None
                ),
            }
            for item in pack.capabilities
        ],
        "calibration": {
            "calibration_id": calibration.calibration_id,
            "market_id": calibration.market_id,
            "maturity": calibration.maturity,
            "components": [
                {
                    "component_id": item.component_id,
                    "kind": item.kind.value,
                    "weight": item.weight,
                    "required": item.required,
                    "stress_direction": item.stress_direction,
                    "center": item.center,
                    "scale": item.scale,
                    "minimum_history": item.minimum_history,
                    "overnight_role": (
                        item.overnight_role.value if item.overnight_role else None
                    ),
                    "anchor_role": item.anchor_role.value if item.anchor_role else None,
                    "term_role": item.term_role.value if item.term_role else None,
                    "funding_role": (
                        item.funding_role.value if item.funding_role else None
                    ),
                    "buffer_role": item.buffer_role.value if item.buffer_role else None,
                }
                for item in calibration.components
            ],
        },
    }


def pack_fingerprint(pack: MarketPack, calibration: LocalCalibration) -> str:
    return input_fingerprint_for(_pack_contract_payload(pack, calibration))


def _validation_input_payload(
    pack: MarketPack,
    calibration: LocalCalibration,
    observations: Iterable[Observation],
    runs: Iterable[dict],
) -> dict[str, object]:
    return {
        "pack": _pack_contract_payload(pack, calibration),
        "observations": [
            item.to_record()
            for item in sorted(
                observations,
                key=lambda row: (
                    row.event_time,
                    row.instrument_id,
                    row.knowledge_time,
                    row.source,
                    row.revision_id,
                ),
            )
        ],
        "collector_runs": sorted(
            (
                {
                    "market_id": str(item.get("market_id", "")),
                    "adapter_id": str(item.get("adapter_id", "")),
                    "status": str(item.get("status", "")),
                    "started_at": str(item.get("started_at", "")),
                    "finished_at": str(item.get("finished_at", "")),
                    "observations_written": int(item.get("observations_written", 0)),
                    "attempts": int(item.get("attempts", 0)),
                }
                for item in runs
            ),
            key=lambda item: (
                item["market_id"],
                item["adapter_id"],
                item["finished_at"],
            ),
        ),
    }


def _required_ready_roles(pack: MarketPack) -> frozenset[SemanticRole]:
    return frozenset(
        role
        for capability in pack.capabilities
        if capability.status is CapabilityStatus.READY
        for role in capability.required_roles
    )


def _schema_and_units(
    pack: MarketPack,
    calibration: LocalCalibration,
    observations: list[Observation],
) -> GateAssessment:
    mismatches: set[str] = set()
    conversions_checked = 0
    for instrument in pack.instruments:
        try:
            normalized = instrument.normalize(Decimal("1"))
        except Exception:
            mismatches.add("DECLARED_UNIT_CONVERSION_FAILED")
        else:
            conversions_checked += 1
            if not normalized.is_finite():
                mismatches.add("DECLARED_UNIT_CONVERSION_NONFINITE")

    for observation in observations:
        instrument = pack.instrument_map.get(observation.instrument_id)
        if instrument is None:
            mismatches.add("OBSERVATION_INSTRUMENT_UNDECLARED")
            continue
        adapter = pack.adapter_map[instrument.source_adapter_id]
        if observation.market_id != pack.market_id:
            mismatches.add("OBSERVATION_MARKET_MISMATCH")
        if observation.monetary_area_id != pack.monetary_area_id:
            mismatches.add("OBSERVATION_MONETARY_AREA_MISMATCH")
        if observation.jurisdiction_codes != pack.jurisdiction_codes:
            mismatches.add("OBSERVATION_JURISDICTION_MISMATCH")
        if observation.currency != pack.currency:
            mismatches.add("OBSERVATION_CURRENCY_MISMATCH")
        if observation.semantic_role is not instrument.semantic_role:
            mismatches.add("OBSERVATION_ROLE_MISMATCH")
        if observation.canonical_unit is not instrument.canonical_unit:
            mismatches.add("OBSERVATION_UNIT_MISMATCH")
        if observation.rate_compounding is not instrument.rate_compounding:
            mismatches.add("OBSERVATION_COMPOUNDING_MISMATCH")
        if observation.day_count is not instrument.day_count:
            mismatches.add("OBSERVATION_DAY_COUNT_MISMATCH")
        if observation.connector_classification is not adapter.classification:
            mismatches.add("OBSERVATION_CONNECTOR_CLASSIFICATION_MISMATCH")
        if observation.redistribution_status is not adapter.redistribution_status:
            mismatches.add("OBSERVATION_REDISTRIBUTION_POLICY_MISMATCH")

    present_roles = {
        item.semantic_role for item in observations if item.usable
    }
    missing_roles = sorted(
        role.value for role in _required_ready_roles(pack) - present_roles
    )
    metrics: dict[str, object] = {
        "implementation": "schema-and-units-v1",
        "declared_instruments": len(pack.instruments),
        "declared_adapters": len(pack.source_adapters),
        "unit_conversions_checked": conversions_checked,
        "canonical_rows_checked": len(observations),
        "ready_roles_required": len(_required_ready_roles(pack)),
        "ready_roles_missing": missing_roles,
        "mismatch_codes": sorted(mismatches),
        "calibration_matches_pack": (
            calibration.market_id == pack.market_id
            and calibration.calibration_id == pack.calibration_id
        ),
    }
    if calibration.market_id != pack.market_id or calibration.calibration_id != pack.calibration_id:
        mismatches.add("CALIBRATION_PACK_MISMATCH")
        metrics["mismatch_codes"] = sorted(mismatches)
    if mismatches:
        return GateAssessment(
            ValidationCheck.SCHEMA_AND_UNITS,
            ValidationStatus.FAIL,
            metrics,
            tuple(sorted(mismatches)),
        )
    if missing_roles:
        return GateAssessment(
            ValidationCheck.SCHEMA_AND_UNITS,
            ValidationStatus.PENDING,
            metrics,
            ("READY_CAPABILITY_OBSERVATIONS_MISSING",),
        )
    return GateAssessment(
        ValidationCheck.SCHEMA_AND_UNITS,
        ValidationStatus.PASS,
        metrics,
    )


def _products_at_cutoffs(
    pack: MarketPack,
    calibration: LocalCalibration,
    observations: Iterable[Observation],
    runs: list[dict],
    *,
    event_cutoff: datetime,
    knowledge_cutoff: datetime,
    repository: MarketRepository,
) -> tuple[dict, dict]:
    visible = [
        item
        for item in observations
        if item.event_time <= event_cutoff and item.knowledge_time <= knowledge_cutoff
    ]
    return build_local_products(
        pack,
        calibration,
        visible,
        runs,
        knowledge_cutoff,
        repository,
    )


def _truncation_invariance(
    pack: MarketPack,
    calibration: LocalCalibration,
    observations: list[Observation],
    runs: list[dict],
    as_of: datetime,
    repository: MarketRepository,
) -> GateAssessment:
    event_times = sorted({item.event_time for item in observations})
    if len(event_times) < 2:
        return GateAssessment(
            ValidationCheck.TRUNCATION_INVARIANCE,
            ValidationStatus.PENDING,
            {
                "implementation": "truncation-replay-v1",
                "event_times_available": len(event_times),
            },
            ("INSUFFICIENT_DISTINCT_EVENT_TIMES",),
        )
    event_cutoff = event_times[(len(event_times) - 1) // 2]
    prefix = [item for item in observations if item.event_time <= event_cutoff]
    suffix = [item for item in observations if item.event_time > event_cutoff]
    expected = _products_at_cutoffs(
        pack,
        calibration,
        prefix,
        runs,
        event_cutoff=event_cutoff,
        knowledge_cutoff=as_of,
        repository=repository,
    )
    replayed = _products_at_cutoffs(
        pack,
        calibration,
        observations,
        runs,
        event_cutoff=event_cutoff,
        knowledge_cutoff=as_of,
        repository=repository,
    )
    expected_hash = input_fingerprint_for(expected)
    replayed_hash = input_fingerprint_for(replayed)
    metrics = {
        "implementation": "truncation-replay-v1",
        "prefix_rows": len(prefix),
        "future_suffix_rows": len(suffix),
        "event_times_available": len(event_times),
        "event_cutoff": event_cutoff.isoformat(),
        "prefix_product_sha256": expected_hash,
        "future-appended_product_sha256": replayed_hash,
    }
    if not suffix:
        return GateAssessment(
            ValidationCheck.TRUNCATION_INVARIANCE,
            ValidationStatus.PENDING,
            metrics,
            ("FUTURE_SUFFIX_UNAVAILABLE",),
        )
    if expected_hash != replayed_hash:
        return GateAssessment(
            ValidationCheck.TRUNCATION_INVARIANCE,
            ValidationStatus.FAIL,
            metrics,
            ("FUTURE_OBSERVATIONS_CHANGED_TRUNCATED_REPLAY",),
        )
    return GateAssessment(
        ValidationCheck.TRUNCATION_INVARIANCE,
        ValidationStatus.PASS,
        metrics,
    )


def _extra_reporting_lag(
    pack: MarketPack,
    calibration: LocalCalibration,
    observations: list[Observation],
    runs: list[dict],
    as_of: datetime,
    repository: MarketRepository,
) -> GateAssessment:
    usable = [item for item in observations if item.usable]
    if not usable:
        return GateAssessment(
            ValidationCheck.EXTRA_REPORTING_LAG,
            ValidationStatus.PENDING,
            {"implementation": "knowledge-lag-perturbation-v1", "rows_available": 0},
            ("OBSERVATIONS_UNAVAILABLE_FOR_LAG_PERTURBATION",),
        )
    selected_source = max(usable, key=lambda item: item.knowledge_time).source
    lag = timedelta(days=2)
    delayed = [
        replace(
            item,
            source_publication_time=item.source_publication_time + lag,
            knowledge_time=item.knowledge_time + lag,
        )
        if item.source == selected_source
        else item
        for item in observations
    ]
    delayed_rows = [
        item for item in delayed if item.source == selected_source and item.knowledge_time > as_of
    ]
    visible = [item for item in delayed if item.knowledge_time <= as_of]
    full_result = _products_at_cutoffs(
        pack,
        calibration,
        delayed,
        runs,
        event_cutoff=as_of,
        knowledge_cutoff=as_of,
        repository=repository,
    )
    visible_result = build_local_products(
        pack,
        calibration,
        visible,
        runs,
        as_of,
        repository,
    )
    full_hash = input_fingerprint_for(full_result)
    visible_hash = input_fingerprint_for(visible_result)
    metrics = {
        "implementation": "knowledge-lag-perturbation-v1",
        "lag_days": lag.days,
        "rows_perturbed": sum(item.source == selected_source for item in observations),
        "rows_withheld": len(delayed_rows),
        "lagged-input-product_sha256": full_hash,
        "explicit-visible-product_sha256": visible_hash,
    }
    if not delayed_rows:
        return GateAssessment(
            ValidationCheck.EXTRA_REPORTING_LAG,
            ValidationStatus.PENDING,
            metrics,
            ("PERTURBATION_DID_NOT_CROSS_KNOWLEDGE_CUTOFF",),
        )
    if full_hash != visible_hash:
        return GateAssessment(
            ValidationCheck.EXTRA_REPORTING_LAG,
            ValidationStatus.FAIL,
            metrics,
            ("LAGGED_OBSERVATIONS_ENTERED_EARLY",),
        )
    return GateAssessment(
        ValidationCheck.EXTRA_REPORTING_LAG,
        ValidationStatus.PASS,
        metrics,
    )


def _revision_vintage_leakage(
    pack: MarketPack,
    observations: list[Observation],
    repository: MarketRepository,
) -> GateAssessment:
    pairs_checked = 0
    failures: set[str] = set()
    if not observations:
        history: list[Observation] = []
    else:
        history = repository.load_observation_revisions(
            pack.market_id,
            max(item.knowledge_time for item in observations),
            instrument_ids=tuple(pack.instrument_map),
            event_time=max(item.event_time for item in observations),
            event_time_from=min(item.event_time for item in observations),
        )
    grouped: dict[tuple[str, datetime], list[Observation]] = {}
    for item in history:
        grouped.setdefault((item.instrument_id, item.event_time), []).append(item)
    for (instrument_id, event_time), vintages in grouped.items():
        ordered = sorted(
            vintages,
            key=lambda item: (
                item.knowledge_time,
                item.source_publication_time,
                item.revision_id,
                item.source,
            ),
        )
        if len({item.revision_id for item in ordered}) < 2:
            continue
        current = ordered[-1]
        # Canonical observation clocks have second precision in both stores.
        prior_cutoff = current.knowledge_time - timedelta(seconds=1)
        prior_rows = repository.load_observations_as_of(
            pack.market_id,
            prior_cutoff,
            event_time=event_time,
            event_time_from=event_time,
            instrument_ids=(instrument_id,),
        )
        prior = next(
            (
                item
                for item in prior_rows
                if item.event_time == event_time
                and item.instrument_id == instrument_id
            ),
            None,
        )
        pairs_checked += 1
        if prior is None:
            failures.add("PRIOR_REVISION_NOT_SELECTED_BEFORE_LATER_KNOWLEDGE_TIME")
        elif prior.knowledge_time > prior_cutoff:
            failures.add("FUTURE_REVISION_VISIBLE_BEFORE_KNOWLEDGE_TIME")
        after_rows = repository.load_observations_as_of(
            pack.market_id,
            current.knowledge_time,
            event_time=event_time,
            event_time_from=event_time,
            instrument_ids=(instrument_id,),
        )
        after = next(
            (
                item
                for item in after_rows
                if item.event_time == event_time
                and item.instrument_id == instrument_id
            ),
            None,
        )
        if after is None or after.revision_id != current.revision_id:
            failures.add("LATEST_REVISION_NOT_SELECTED_AT_KNOWLEDGE_TIME")
    metrics = {
        "implementation": "bitemporal-revision-pair-v1",
        "latest_rows_examined": len(observations),
        "stored_vintages_examined": len(history),
        "real_revision_pairs_checked": pairs_checked,
        "failure_codes": sorted(failures),
    }
    if failures:
        return GateAssessment(
            ValidationCheck.REVISION_VINTAGE_LEAKAGE,
            ValidationStatus.FAIL,
            metrics,
            tuple(sorted(failures)),
        )
    if not pairs_checked:
        return GateAssessment(
            ValidationCheck.REVISION_VINTAGE_LEAKAGE,
            ValidationStatus.PENDING,
            metrics,
            ("REAL_REVISION_PAIR_NOT_YET_CAPTURED",),
        )
    return GateAssessment(
        ValidationCheck.REVISION_VINTAGE_LEAKAGE,
        ValidationStatus.PASS,
        metrics,
    )


def _component_roles(component: ComponentCalibration) -> frozenset[SemanticRole]:
    roles = {
        item
        for item in (
            component.overnight_role,
            component.anchor_role,
            component.term_role,
            component.funding_role,
            component.buffer_role,
        )
        if item is not None
    }
    if component.kind is EngineKind.CORRIDOR:
        roles.update({SemanticRole.POLICY_FLOOR, SemanticRole.POLICY_CEILING})
    elif component.kind is EngineKind.SECURED_UNSECURED:
        roles.update(
            {SemanticRole.SECURED_OVERNIGHT, SemanticRole.UNSECURED_OVERNIGHT}
        )
    elif component.kind is EngineKind.FUNDING_BILL:
        roles.add(SemanticRole.TBILL_3M)
    elif component.kind is EngineKind.FACILITY_USAGE:
        roles.update(
            {
                SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP,
                SemanticRole.RESERVE_BALANCES,
            }
        )
    elif component.kind is EngineKind.TAIL_DISLOCATION:
        roles.update({SemanticRole.RATE_P99, SemanticRole.RATE_MEDIAN})
    elif component.kind is EngineKind.VOLUME_DISLOCATION:
        roles.add(SemanticRole.REPO_VOLUME)
    return frozenset(roles)


def _missing_source_failure_injection(
    pack: MarketPack,
    calibration: LocalCalibration,
    observations: list[Observation],
    runs: list[dict],
    as_of: datetime,
    repository: MarketRepository,
) -> GateAssessment:
    _, baseline = build_local_products(
        pack,
        calibration,
        observations,
        runs,
        as_of,
        repository,
    )
    baseline_index = baseline["reading"]["index"]
    required = [item for item in calibration.components if item.required]
    metrics: dict[str, object] = {
        "implementation": "adapter-failure-and-required-role-removal-v1",
        "required_components": len(required),
        "adapter_injections_run": 0,
        "required_role_injections_run": 0,
        "required_source_losses": 0,
        "baseline_numeric": baseline_index is not None,
        "fabricated_numeric_results": 0,
        "unrelated_component_changes": 0,
    }
    if baseline_index is None:
        return GateAssessment(
            ValidationCheck.MISSING_SOURCE_FAILURE_INJECTION,
            ValidationStatus.PENDING,
            metrics,
            ("COMPUTABLE_BASELINE_UNAVAILABLE",),
        )
    fabricated = 0
    unrelated_changes = 0
    baseline_components = {
        item["component_id"]: item for item in baseline["components"]
    }
    for adapter in pack.source_adapters:
        adapter_instrument_ids = {
            item.instrument_id
            for item in pack.instruments
            if item.source_adapter_id == adapter.adapter_id
        }
        removed_rows = [
            item for item in observations if item.instrument_id in adapter_instrument_ids
        ]
        if not removed_rows:
            continue
        remaining = [
            item for item in observations if item.instrument_id not in adapter_instrument_ids
        ]
        failed_run = {
            "market_id": pack.market_id,
            "adapter_id": adapter.adapter_id,
            "status": "FAILED",
            "started_at": as_of.isoformat(),
            "finished_at": as_of.isoformat(),
            "observations_written": 0,
            "attempts": 1,
            "next_due": (as_of + timedelta(days=1)).isoformat(),
            "fault": "validation failure injection",
        }
        injected_runs = [
            item for item in runs if item.get("adapter_id") != adapter.adapter_id
        ] + [failed_run]
        _, injected = build_local_products(
            pack,
            calibration,
            remaining,
            injected_runs,
            as_of,
            repository,
        )
        metrics["adapter_injections_run"] = int(
            metrics["adapter_injections_run"]
        ) + 1
        remaining_roles = {item.semantic_role for item in remaining if item.usable}
        required_lost = any(
            not _component_roles(component).issubset(remaining_roles)
            for component in required
        )
        if required_lost:
            metrics["required_source_losses"] = int(
                metrics["required_source_losses"]
            ) + 1
            if (
                injected["reading"]["index"] is not None
                or injected["reading"]["regime"] is not None
            ):
                fabricated += 1

        removed_roles = {item.semantic_role for item in removed_rows}
        injected_components = {
            item["component_id"]: item for item in injected["components"]
        }
        for component in calibration.components:
            if _component_roles(component) & removed_roles:
                continue
            before = baseline_components.get(component.component_id)
            after = injected_components.get(component.component_id)
            if before is not None and after is not None and before["kernel"] != after["kernel"]:
                unrelated_changes += 1

    for component in required:
        roles = _component_roles(component)
        injected_rows = [
            item for item in observations if item.semantic_role not in roles
        ]
        _, injected = build_local_products(
            pack,
            calibration,
            injected_rows,
            runs,
            as_of,
            repository,
        )
        metrics["required_role_injections_run"] = int(
            metrics["required_role_injections_run"]
        ) + 1
        reading = injected["reading"]
        if reading["index"] is not None or reading["regime"] is not None:
            fabricated += 1
    metrics["fabricated_numeric_results"] = fabricated
    metrics["unrelated_component_changes"] = unrelated_changes
    failures = []
    if fabricated:
        failures.append("REQUIRED_SOURCE_LOSS_FABRICATED_READING")
    if unrelated_changes:
        failures.append("UNRELATED_COMPONENT_CHANGED_DURING_SOURCE_FAILURE")
    if failures:
        return GateAssessment(
            ValidationCheck.MISSING_SOURCE_FAILURE_INJECTION,
            ValidationStatus.FAIL,
            metrics,
            tuple(failures),
        )
    return GateAssessment(
        ValidationCheck.MISSING_SOURCE_FAILURE_INJECTION,
        ValidationStatus.PASS,
        metrics,
    )


def _pending(check: ValidationCheck, reason: str, implementation: str) -> GateAssessment:
    return GateAssessment(
        check,
        ValidationStatus.PENDING,
        {"implementation": implementation},
        (reason,),
    )


def _forward_paper_record(
    pack: MarketPack,
    repository: MarketRepository,
    *,
    minimum_records: int | None,
    minimum_span_days: int | None,
) -> GateAssessment:
    policy_frozen = minimum_records is not None and minimum_span_days is not None
    result = verify_repository_forward_chain(
        repository,
        market_id=pack.market_id,
        calibration_id=pack.calibration_id,
        required_products=("gauge", "overview"),
        minimum_records=minimum_records or 0,
        minimum_span_days=minimum_span_days or 0,
    )
    metrics = {
        "implementation": "forward-topology-and-generation-v2",
        "chain_integrity_status": result["status"],
        "maturity_policy_frozen": policy_frozen,
        **result["metrics"],
    }
    if result["status"] == "FAIL":
        return GateAssessment(
            ValidationCheck.FORWARD_PAPER_RECORD,
            ValidationStatus.FAIL,
            metrics,
            tuple(result["reason_codes"]),
        )
    reasons = list(result["reason_codes"])
    if not policy_frozen:
        reasons.append("FORWARD_MATURITY_POLICY_NOT_FROZEN")
    # Integrity and elapsed time are necessary but not sufficient: the record
    # still needs outcomes and an independent calibration review.
    reasons.append("FORWARD_OUTCOME_REVIEW_NOT_RECORDED")
    return GateAssessment(
        ValidationCheck.FORWARD_PAPER_RECORD,
        ValidationStatus.PENDING,
        metrics,
        tuple(sorted(set(reasons))),
    )


def _assess(
    check: ValidationCheck,
    *,
    pack: MarketPack,
    calibration: LocalCalibration,
    observations: list[Observation],
    runs: list[dict],
    as_of: datetime,
    repository: MarketRepository,
    minimum_forward_records: int | None,
    minimum_forward_span_days: int | None,
) -> GateAssessment:
    if check is ValidationCheck.SCHEMA_AND_UNITS:
        return _schema_and_units(pack, calibration, observations)
    if check is ValidationCheck.CALENDAR_AND_TIMEZONE:
        result = assess_calendar_and_timezone(pack, as_of=as_of)
        references = tuple(
            dict.fromkeys(
                item
                for item in (
                    pack.holiday_calendar.source_uri,
                    pack.settlement_calendar.source_uri,
                    f"fixture-set:{result['metrics']['fixture_set_version']}",  # type: ignore[index]
                )
                if item
            )
        )
        return GateAssessment(
            check,
            ValidationStatus(str(result["status"])),
            result["metrics"],  # type: ignore[arg-type]
            tuple(result["reasons"]),  # type: ignore[arg-type]
            references,
        )
    if check is ValidationCheck.TRUNCATION_INVARIANCE:
        return _truncation_invariance(
            pack, calibration, observations, runs, as_of, repository
        )
    if check is ValidationCheck.EXTRA_REPORTING_LAG:
        return _extra_reporting_lag(
            pack, calibration, observations, runs, as_of, repository
        )
    if check is ValidationCheck.REVISION_VINTAGE_LEAKAGE:
        return _revision_vintage_leakage(pack, observations, repository)
    if check is ValidationCheck.LABEL_SHUFFLE:
        return _pending(
            check,
            "OUTCOME_LABEL_CORPUS_NOT_RECORDED",
            "label-shuffle-v1",
        )
    if check is ValidationCheck.MISSING_SOURCE_FAILURE_INJECTION:
        return _missing_source_failure_injection(
            pack, calibration, observations, runs, as_of, repository
        )
    if check is ValidationCheck.LOCAL_TEMPORAL_HOLDOUT:
        return _pending(
            check,
            "LOCAL_TEMPORAL_HOLDOUT_NOT_RECORDED",
            "local-temporal-holdout-v1",
        )
    if check is ValidationCheck.LEAVE_ONE_MARKET_OUT:
        return _pending(
            check,
            "LEAVE_ONE_MARKET_OUT_REVIEW_NOT_RECORDED",
            "leave-one-market-out-v1",
        )
    if check is ValidationCheck.FORWARD_PAPER_RECORD:
        return _forward_paper_record(
            pack,
            repository,
            minimum_records=minimum_forward_records,
            minimum_span_days=minimum_forward_span_days,
        )
    return _pending(
        check,
        (
            "US_CANONICAL_PARITY_CORPUS_NOT_RECORDED"
            if pack.market_id == "US-USD"
            else "PLATFORM_US_PARITY_EVIDENCE_NOT_RECORDED"
        ),
        "us-output-parity-v1",
    )


def validate_market(
    market_id: str,
    *,
    repository: MarketRepository | None = None,
    registry: MarketRegistry | None = None,
    evidence_store: ValidationEvidenceStore | None = None,
    checks: Iterable[ValidationCheck | str] | None = None,
    as_of: datetime | None = None,
    minimum_forward_records: int | None = None,
    minimum_forward_span_days: int | None = None,
) -> MarketValidationReport:
    """Run selected checks without writing product or repository state."""

    generated_at = datetime.now(UTC)
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise ValueError("validation as_of must be timezone-aware")
    cutoff = (as_of or generated_at).astimezone(UTC).replace(microsecond=0)
    if cutoff > generated_at:
        raise ValueError("validation as_of cannot be in the future")
    repo = repository or get_repository()
    markets = registry or default_registry()
    pack = markets.get(market_id)
    calibration = get_local_calibration(pack.market_id)
    selected = (
        tuple(ValidationCheck(item) for item in checks)
        if checks is not None
        else tuple(ValidationCheck)
    )
    if len(selected) != len(set(selected)):
        raise ValueError("validation checks must be unique")
    observations = repo.load_observations_as_of(
        pack.market_id,
        cutoff,
        event_time=cutoff,
        instrument_ids=tuple(pack.instrument_map),
    )
    runs = repo.latest_collector_runs(pack.market_id)
    input_fingerprint = input_fingerprint_for(
        _validation_input_payload(pack, calibration, observations, runs)
    )
    contract_fingerprint = pack_fingerprint(pack, calibration)
    event_cutoff = max(
        (item.event_time for item in observations),
        default=cutoff,
    )
    artifacts: list[ValidationEvidenceArtifact] = []
    for check in selected:
        assessment = _assess(
            check,
            pack=pack,
            calibration=calibration,
            observations=observations,
            runs=runs,
            as_of=cutoff,
            repository=repo,
            minimum_forward_records=minimum_forward_records,
            minimum_forward_span_days=minimum_forward_span_days,
        )
        metrics = {"pack_fingerprint": contract_fingerprint, **assessment.metrics}
        references = assessment.evidence_references
        if assessment.status is not ValidationStatus.PENDING and not references:
            references = (f"sha256:{input_fingerprint}",)
        artifact = ValidationEvidenceArtifact.create(
            market_id=pack.market_id,
            calibration_id=calibration.calibration_id,
            check=check,
            status=assessment.status,
            runner_id=VALIDATION_RUNNER_ID,
            runner_version=VALIDATION_RUNNER_VERSION,
            generated_at=generated_at,
            event_cutoff=min(event_cutoff, cutoff),
            knowledge_cutoff=cutoff,
            input_fingerprint=input_fingerprint,
            metrics=metrics,
            reasons=assessment.reasons,
            evidence_references=references,
        )
        if evidence_store is not None:
            evidence_store.append(artifact)
        artifacts.append(artifact)
    return MarketValidationReport(
        market_id=pack.market_id,
        calibration_id=calibration.calibration_id,
        generated_at=generated_at,
        artifacts=tuple(artifacts),
    )


def promotion_report(
    market_id: str,
    *,
    evidence_store: ValidationEvidenceStore,
    registry: MarketRegistry | None = None,
) -> dict[str, object]:
    """Verify latest artifacts and report eligibility without mutating a pack."""

    markets = registry or default_registry()
    pack = markets.get(market_id)
    calibration = get_local_calibration(pack.market_id)
    expected_pack_fingerprint = pack_fingerprint(pack, calibration)
    latest = evidence_store.latest_per_check(
        pack.market_id,
        calibration.calibration_id,
    )
    checks: dict[str, object] = {}
    blockers: list[str] = []
    results: list[ValidationResult] = []
    for check in ValidationCheck:
        artifact = latest.get(check)
        if artifact is None:
            blockers.append(f"{check.value}:MISSING_ARTIFACT")
            checks[check.value] = {"status": "MISSING", "artifact_id": None}
            continue
        reasons: list[str] = []
        if artifact.runner_id != VALIDATION_RUNNER_ID:
            reasons.append("UNTRUSTED_RUNNER_ID")
        if artifact.runner_version != VALIDATION_RUNNER_VERSION:
            reasons.append("UNSUPPORTED_RUNNER_VERSION")
        if artifact.metrics.get("pack_fingerprint") != expected_pack_fingerprint:
            reasons.append("PACK_CONTRACT_CHANGED")
        if artifact.status is not ValidationStatus.PASS:
            reasons.append(f"STATUS_{artifact.status.value}")
        if reasons:
            blockers.extend(f"{check.value}:{reason}" for reason in reasons)
        else:
            results.append(
                ValidationResult(
                    check=check,
                    outcome=ValidationOutcome.PASS,
                    evidence=f"sha256:{artifact.artifact_id}",
                )
            )
        checks[check.value] = {
            "status": artifact.status.value,
            "artifact_id": artifact.artifact_id,
            "generated_at": artifact.generated_at.isoformat(),
            "reasons": reasons,
        }
    eligible = not blockers and set(latest) == REQUIRED_VALIDATION_CHECKS
    return {
        "schema": "seiche.market-promotion-report.v1",
        "market_id": pack.market_id,
        "calibration_id": calibration.calibration_id,
        "pack_fingerprint": expected_pack_fingerprint,
        "eligible": eligible,
        "checks": checks,
        "blockers": sorted(blockers),
        "validation_results": [
            {
                "check": item.check.value,
                "outcome": item.outcome.value,
                "evidence": item.evidence,
            }
            for item in results
        ],
        "note": "This verifier never mutates the registered pack.",
    }


__all__ = [
    "GateAssessment",
    "MarketValidationReport",
    "VALIDATION_RUNNER_ID",
    "VALIDATION_RUNNER_VERSION",
    "pack_fingerprint",
    "promotion_report",
    "validate_market",
]
