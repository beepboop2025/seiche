from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from seiche import store
from seiche.domain.observation import (
    CanonicalUnit,
    Observation,
    QualityState,
    SemanticRole,
    StalenessState,
    evidence_sha256,
)
from seiche.markets.base import (
    Capability,
    CapabilityStatus,
    MinimumHistory,
    ValidationCheck,
)
from seiche.markets.calibration import (
    ComponentCalibration,
    EngineKind,
    LocalCalibration,
)
from seiche.markets.materialize import build_local_products
from seiche.markets.registry import MarketRegistry
from seiche.markets.us_usd import PACK as US_PACK
from seiche.markets.validation import (
    _extra_reporting_lag,
    _missing_source_failure_injection,
    _revision_vintage_leakage,
    _schema_and_units,
    _truncation_invariance,
    promotion_report,
    validate_market,
)
from seiche.markets.validation_evidence import (
    ArtifactIntegrityError,
    ValidationEvidenceStore,
    ValidationStatus,
)
from seiche.repository import SQLiteMarketRepository


def _repository(tmp_path, monkeypatch, name: str = "validation.sqlite"):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / name)
    return SQLiteMarketRepository()


def _observation(
    instrument_id: str,
    *,
    event_time: datetime,
    knowledge_time: datetime,
    value: Decimal | int | str,
    revision_id: str = "initial",
) -> Observation:
    instrument = US_PACK.instrument_map[instrument_id]
    adapter = US_PACK.adapter_map[instrument.source_adapter_id]
    publication_time = knowledge_time - timedelta(minutes=1)
    return Observation(
        market_id=US_PACK.market_id,
        monetary_area_id=US_PACK.monetary_area_id,
        jurisdiction_codes=US_PACK.jurisdiction_codes,
        currency=US_PACK.currency,
        instrument_id=instrument.instrument_id,
        semantic_role=instrument.semantic_role,
        value=value,
        canonical_unit=instrument.canonical_unit,
        rate_compounding=instrument.rate_compounding,
        day_count=instrument.day_count,
        event_time=event_time,
        source_publication_time=publication_time,
        knowledge_time=knowledge_time,
        revision_id=revision_id,
        source=instrument.source_adapter_id,
        evidence_hash=evidence_sha256(
            ":".join(
                (
                    instrument_id,
                    event_time.isoformat(),
                    knowledge_time.isoformat(),
                    str(value),
                    revision_id,
                )
            )
        ),
        connector_classification=adapter.classification,
        redistribution_status=adapter.redistribution_status,
        quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )


def _compact_contract() -> tuple[object, LocalCalibration]:
    required_roles = frozenset(
        {SemanticRole.SECURED_OVERNIGHT, SemanticRole.POLICY_TARGET}
    )
    pack = replace(
        US_PACK,
        capabilities=(
            Capability(
                "policy_relative_overnight",
                CapabilityStatus.READY,
                required_roles,
                minimum_history=MinimumHistory(1, 1),
            ),
        ),
        minimum_history=MinimumHistory(2, 1),
    )
    calibration = LocalCalibration(
        calibration_id=pack.calibration_id,
        market_id=pack.market_id,
        components=(
            ComponentCalibration(
                component_id="policy_relative_overnight",
                kind=EngineKind.POLICY_RELATIVE,
                weight=1.0,
                required=True,
                stress_direction=1,
                center=0.0,
                scale=20.0,
                minimum_history=1,
                overnight_role=SemanticRole.SECURED_OVERNIGHT,
                anchor_role=SemanticRole.POLICY_TARGET,
            ),
        ),
    )
    return pack, calibration


def _compact_rows(
    *,
    event_time: datetime,
    knowledge_time: datetime,
) -> list[Observation]:
    return [
        _observation(
            "US.FED.IORB",
            event_time=event_time,
            knowledge_time=knowledge_time,
            value=500,
        ),
        _observation(
            "US.NYFED.SOFR",
            event_time=event_time,
            knowledge_time=knowledge_time,
            value=512,
        ),
    ]


def test_schema_passes_only_when_every_ready_role_has_canonical_evidence() -> None:
    pack, calibration = _compact_contract()
    event_time = datetime(2026, 8, 6, tzinfo=UTC)
    knowledge_time = event_time + timedelta(hours=1)
    rows = _compact_rows(event_time=event_time, knowledge_time=knowledge_time)

    complete = _schema_and_units(pack, calibration, rows)
    missing = _schema_and_units(pack, calibration, rows[:1])

    assert complete.status is ValidationStatus.PASS
    assert missing.status is ValidationStatus.PENDING
    assert missing.reasons == ("READY_CAPABILITY_OBSERVATIONS_MISSING",)
    assert missing.metrics["ready_roles_missing"] == ["SECURED_OVERNIGHT"]


def test_truncation_replay_uses_a_real_future_suffix(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path, monkeypatch)
    pack, calibration = _compact_contract()
    first = datetime(2026, 8, 5, tzinfo=UTC)
    second = first + timedelta(days=1)
    cutoff = second + timedelta(hours=2)
    rows = [
        *_compact_rows(event_time=first, knowledge_time=first + timedelta(hours=1)),
        *_compact_rows(event_time=second, knowledge_time=second + timedelta(hours=1)),
    ]

    result = _truncation_invariance(
        pack, calibration, rows, [], cutoff, repository
    )

    assert result.status is ValidationStatus.PASS
    assert result.metrics["prefix_rows"] == 2
    assert result.metrics["future_suffix_rows"] == 2
    assert (
        result.metrics["prefix_product_sha256"]
        == result.metrics["future-appended_product_sha256"]
    )


def test_extra_reporting_lag_withholds_rows_past_the_knowledge_cutoff(
    tmp_path, monkeypatch
) -> None:
    repository = _repository(tmp_path, monkeypatch)
    pack, calibration = _compact_contract()
    cutoff = datetime(2026, 8, 8, 12, tzinfo=UTC)
    rows = _compact_rows(
        event_time=cutoff - timedelta(days=2),
        knowledge_time=cutoff - timedelta(days=1),
    )

    result = _extra_reporting_lag(
        pack, calibration, rows, [], cutoff, repository
    )

    assert result.status is ValidationStatus.PASS
    assert result.metrics["rows_perturbed"] == 2
    assert result.metrics["rows_withheld"] == 2
    assert (
        result.metrics["lagged-input-product_sha256"]
        == result.metrics["explicit-visible-product_sha256"]
    )


def test_revision_gate_requires_and_checks_a_real_repository_vintage_pair(
    tmp_path, monkeypatch
) -> None:
    repository = _repository(tmp_path, monkeypatch)
    pack, _ = _compact_contract()
    event_time = datetime(2026, 8, 5, tzinfo=UTC)
    initial = _observation(
        "US.NYFED.SOFR",
        event_time=event_time,
        knowledge_time=event_time + timedelta(hours=1),
        value=510,
    )
    revised = _observation(
        "US.NYFED.SOFR",
        event_time=event_time,
        knowledge_time=event_time + timedelta(hours=3),
        value=511,
        revision_id="revision-1",
    )
    repository.save_observations((initial, revised))

    checked = _revision_vintage_leakage(pack, [revised], repository)
    no_pair = _revision_vintage_leakage(pack, [initial], repository)

    assert checked.status is ValidationStatus.PASS
    assert checked.metrics["real_revision_pairs_checked"] == 1
    assert no_pair.status is ValidationStatus.PENDING
    assert no_pair.reasons == ("REAL_REVISION_PAIR_NOT_YET_CAPTURED",)


def test_missing_required_role_never_fabricates_a_calm_or_numeric_reading(
    tmp_path, monkeypatch
) -> None:
    repository = _repository(tmp_path, monkeypatch)
    pack, calibration = _compact_contract()
    cutoff = datetime(2026, 8, 8, 12, tzinfo=UTC)
    rows = _compact_rows(
        event_time=cutoff - timedelta(hours=2),
        knowledge_time=cutoff - timedelta(hours=1),
    )

    result = _missing_source_failure_injection(
        pack, calibration, rows, [], cutoff, repository
    )
    _, injected = build_local_products(
        pack,
        calibration,
        [row for row in rows if row.semantic_role is not SemanticRole.SECURED_OVERNIGHT],
        [],
        cutoff,
        repository,
    )

    assert result.status is ValidationStatus.PASS
    assert result.metrics["adapter_injections_run"] == 1
    assert result.metrics["required_role_injections_run"] == 1
    assert result.metrics["required_source_losses"] == 1
    assert result.metrics["fabricated_numeric_results"] == 0
    assert result.metrics["unrelated_component_changes"] == 0
    assert injected["status"] == "UNAVAILABLE"
    assert injected["reading"] == {
        "index": None,
        "regime": None,
        "publication_reason": (
            "required components unavailable: policy_relative_overnight"
        ),
    }


_ROLE_VALUES = {
    SemanticRole.POLICY_TARGET: 500,
    SemanticRole.SECURED_OVERNIGHT: 510,
    SemanticRole.UNSECURED_OVERNIGHT: 505,
    SemanticRole.TBILL_3M: 500,
    SemanticRole.CP_3M: 540,
    SemanticRole.RESERVE_BALANCES: 3_000_000,
    SemanticRole.GOVERNMENT_CASH_BALANCE: 800_000,
    SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP: 1_000,
    SemanticRole.RATE_MEDIAN: 509,
    SemanticRole.RATE_P99: 525,
    SemanticRole.REPO_VOLUME: 1_000_000,
}


def _real_pack_rows(as_of: datetime) -> tuple[list[Observation], Observation]:
    ready_roles = frozenset(
        role
        for capability in US_PACK.capabilities
        if capability.status is CapabilityStatus.READY
        for role in capability.required_roles
    )
    instruments = {
        instrument.semantic_role: instrument
        for instrument in US_PACK.instruments
        if instrument.semantic_role in ready_roles
    }
    rows: list[Observation] = []
    for days_ago in (3, 1):
        event_time = as_of - timedelta(days=days_ago)
        knowledge_time = (
            as_of - timedelta(days=2)
            if days_ago == 3
            else as_of - timedelta(hours=1)
        )
        rows.extend(
            _observation(
                instrument.instrument_id,
                event_time=event_time,
                knowledge_time=knowledge_time,
                value=_ROLE_VALUES[role],
                revision_id=f"day-{days_ago}",
            )
            for role, instrument in instruments.items()
        )
    latest_sofr = next(
        row
        for row in rows
        if row.instrument_id == "US.NYFED.SOFR"
        and row.event_time == as_of - timedelta(days=1)
    )
    prior_sofr = _observation(
        latest_sofr.instrument_id,
        event_time=latest_sofr.event_time,
        knowledge_time=as_of - timedelta(hours=2),
        value=509,
        revision_id="day-1-prior",
    )
    return rows, prior_sofr


def test_validate_market_writes_all_gates_and_promotion_rejects_pending_or_stale_pack(
    tmp_path, monkeypatch
) -> None:
    repository = _repository(tmp_path, monkeypatch, "runner.sqlite")
    evidence = ValidationEvidenceStore(tmp_path / "evidence")
    as_of = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    rows, prior = _real_pack_rows(as_of)
    repository.save_observations((*rows, prior))

    report = validate_market(
        US_PACK.market_id,
        repository=repository,
        registry=MarketRegistry((US_PACK,)),
        evidence_store=evidence,
        as_of=as_of,
    )

    by_check = {artifact.check: artifact for artifact in report.artifacts}
    assert len(report.artifacts) == len(ValidationCheck) == 11
    assert set(by_check) == set(ValidationCheck)
    assert by_check[ValidationCheck.SCHEMA_AND_UNITS].status is ValidationStatus.PASS
    assert (
        by_check[ValidationCheck.TRUNCATION_INVARIANCE].status
        is ValidationStatus.PASS
    )
    assert by_check[ValidationCheck.EXTRA_REPORTING_LAG].status is ValidationStatus.PASS
    assert (
        by_check[ValidationCheck.REVISION_VINTAGE_LEAKAGE].status
        is ValidationStatus.PASS
    )
    assert {
        ValidationCheck.LABEL_SHUFFLE,
        ValidationCheck.LOCAL_TEMPORAL_HOLDOUT,
        ValidationCheck.LEAVE_ONE_MARKET_OUT,
        ValidationCheck.FORWARD_PAPER_RECORD,
        ValidationCheck.US_OUTPUT_PARITY,
    } <= {
        check
        for check, artifact in by_check.items()
        if artifact.status is ValidationStatus.PENDING
    }
    assert report.exit_code == 2
    assert all(
        evidence.latest_for_check(
            US_PACK.market_id,
            US_PACK.calibration_id,
            check,
        )
        == artifact
        for check, artifact in by_check.items()
    )

    pending = promotion_report(
        US_PACK.market_id,
        evidence_store=evidence,
        registry=MarketRegistry((US_PACK,)),
    )
    assert pending["eligible"] is False
    assert "label_shuffle:STATUS_PENDING" in pending["blockers"]

    changed_pack = replace(
        US_PACK,
        minimum_history=MinimumHistory(
            US_PACK.minimum_history.observations + 1,
            US_PACK.minimum_history.span_days,
        ),
    )
    stale = promotion_report(
        US_PACK.market_id,
        evidence_store=evidence,
        registry=MarketRegistry((changed_pack,)),
    )
    assert stale["eligible"] is False
    assert "schema_and_units:PACK_CONTRACT_CHANGED" in stale["blockers"]


def test_validate_market_rejects_a_naive_point_in_time_cutoff(
    tmp_path, monkeypatch
) -> None:
    repository = _repository(tmp_path, monkeypatch, "naive-cutoff.sqlite")
    with pytest.raises(ValueError, match="timezone-aware"):
        validate_market(
            US_PACK.market_id,
            repository=repository,
            registry=MarketRegistry((US_PACK,)),
            as_of=datetime(2026, 8, 9, 12),
        )


def test_promotion_report_refuses_tampered_evidence(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path, monkeypatch, "tamper.sqlite")
    evidence = ValidationEvidenceStore(tmp_path / "evidence")
    as_of = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    rows, prior = _real_pack_rows(as_of)
    repository.save_observations((*rows, prior))
    report = validate_market(
        US_PACK.market_id,
        repository=repository,
        registry=MarketRegistry((US_PACK,)),
        evidence_store=evidence,
        as_of=as_of,
    )
    artifact = next(
        item
        for item in report.artifacts
        if item.check is ValidationCheck.SCHEMA_AND_UNITS
    )
    path = evidence.path_for(artifact)
    path.write_bytes(path.read_bytes().replace(b'"PASS"', b'"FAIL"', 1))

    with pytest.raises(ArtifactIntegrityError):
        promotion_report(
            US_PACK.market_id,
            evidence_store=evidence,
            registry=MarketRegistry((US_PACK,)),
        )
