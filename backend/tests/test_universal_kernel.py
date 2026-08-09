from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

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
from seiche.kernel.engines import (
    KernelStatus,
    MarketPanel,
    corridor_position,
    own_history_percentile,
    policy_relative_overnight_pressure,
    secured_unsecured_wedge,
    volume_dislocation,
)


def _rate(
    role: SemanticRole,
    value: float,
    offset: int,
    *,
    instrument: str | None = None,
) -> Observation:
    event_time = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=offset)
    return Observation(
        market_id="ZZ-ZZZ",
        monetary_area_id="ZZ",
        jurisdiction_codes=("ZZ",),
        currency="ZZZ",
        instrument_id=instrument or f"ZZ.TEST.{role.value}",
        semantic_role=role,
        value=value,
        canonical_unit=CanonicalUnit.BASIS_POINTS,
        rate_compounding=RateCompounding.SIMPLE,
        day_count=DayCountConvention.ACT_360,
        event_time=event_time,
        source_publication_time=event_time + timedelta(hours=8),
        knowledge_time=event_time + timedelta(hours=9),
        revision_id="initial",
        source="semantic-test",
        evidence_hash=evidence_sha256(f"{role.value}:{offset}:{value}"),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.ALLOWED,
        quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )


def _volume(value: float, offset: int) -> Observation:
    event_time = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=offset)
    return Observation(
        market_id="ZZ-ZZZ",
        monetary_area_id="ZZ",
        jurisdiction_codes=("ZZ",),
        currency="ZZZ",
        instrument_id="ZZ.TEST.REPO_VOLUME",
        semantic_role=SemanticRole.REPO_VOLUME,
        value=value,
        canonical_unit=CanonicalUnit.LOCAL_CURRENCY_MILLIONS,
        rate_compounding=None,
        day_count=None,
        event_time=event_time,
        source_publication_time=event_time + timedelta(hours=8),
        knowledge_time=event_time + timedelta(hours=9),
        revision_id="initial",
        source="semantic-test",
        evidence_hash=evidence_sha256(f"volume:{offset}:{value}"),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.ALLOWED,
        quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )


def test_role_engines_compute_local_spreads_without_mnemonics() -> None:
    observations = []
    for offset in range(70):
        observations.extend(
            (
                _rate(SemanticRole.POLICY_TARGET, 500, offset),
                _rate(SemanticRole.SECURED_OVERNIGHT, 505 + offset / 10, offset),
                _rate(SemanticRole.UNSECURED_OVERNIGHT, 503 + offset / 20, offset),
            )
        )
    panel = MarketPanel.from_observations(observations)

    pressure = policy_relative_overnight_pressure(
        panel,
        overnight_role=SemanticRole.SECURED_OVERNIGHT,
        anchor_role=SemanticRole.POLICY_TARGET,
    )
    wedge = secured_unsecured_wedge(panel)
    percentile = own_history_percentile(
        panel,
        role=SemanticRole.SECURED_OVERNIGHT,
        minimum_observations=60,
    )

    assert pressure.status is KernelStatus.READY
    assert pressure.value == 11.9
    assert wedge.status is KernelStatus.READY
    assert wedge.value == 5.45
    assert percentile.value == 100.0


def test_missing_corridor_is_unavailable_never_zero() -> None:
    panel = MarketPanel.from_observations(
        [
            _rate(SemanticRole.POLICY_TARGET, 500, 0),
            _rate(SemanticRole.SECURED_OVERNIGHT, 500, 0),
        ]
    )
    result = corridor_position(panel, overnight_role=SemanticRole.SECURED_OVERNIGHT)

    assert result.status is KernelStatus.UNAVAILABLE
    assert result.value is None
    assert "POLICY_FLOOR" in result.reason


def test_all_explicitly_unavailable_rows_remain_unavailable_never_zero() -> None:
    unavailable = replace(
        _rate(SemanticRole.SECURED_OVERNIGHT, 500, 0),
        value=None,
        quality=QualityState.UNAVAILABLE,
        staleness=StalenessState.UNAVAILABLE,
    )
    panel = MarketPanel.from_observations([unavailable])
    result = secured_unsecured_wedge(panel)

    assert result.status is KernelStatus.UNAVAILABLE
    assert result.value is None


def test_volume_dislocation_uses_own_same_weekday_history() -> None:
    history = [_volume(100, offset) for offset in range(56)]
    history.append(_volume(150, 56))

    result = volume_dislocation(MarketPanel.from_observations(history))

    assert result.status is KernelStatus.READY
    assert result.value == 50.0
    assert result.unit == "percent_vs_seasonal_median"


def test_truncation_does_not_change_an_earlier_point_in_time_result() -> None:
    history = [
        _rate(SemanticRole.SECURED_OVERNIGHT, 500 + offset, offset)
        for offset in range(80)
    ]
    at_sixty = own_history_percentile(
        MarketPanel.from_observations(history[:60]),
        role=SemanticRole.SECURED_OVERNIGHT,
        minimum_observations=30,
    )
    replayed_at_sixty = own_history_percentile(
        MarketPanel.from_observations(history[:60]),
        role=SemanticRole.SECURED_OVERNIGHT,
        minimum_observations=30,
    )

    assert at_sixty == replayed_at_sixty
    assert at_sixty.value == 100.0


def test_policy_event_series_is_carried_forward_but_never_backward() -> None:
    observations = [_rate(SemanticRole.POLICY_TARGET, 500, 0)]
    observations.extend(
        _rate(SemanticRole.SECURED_OVERNIGHT, 505 + offset, offset)
        for offset in range(10)
    )

    result = policy_relative_overnight_pressure(
        MarketPanel.from_observations(observations),
        overnight_role=SemanticRole.SECURED_OVERNIGHT,
        anchor_role=SemanticRole.POLICY_TARGET,
    )

    assert result.value == 14.0
    assert result.event_cutoff.startswith("2025-01-10")
