from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seiche.domain.observation import (
    RATE_ROLES,
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
    publication_time_order_key,
)


def _rate_observation(**overrides) -> Observation:
    values = {
        "market_id": "us-usd",
        "monetary_area_id": "us",
        "jurisdiction_codes": ("us",),
        "currency": "usd",
        "instrument_id": "US.TEST.RATE",
        "semantic_role": SemanticRole.SECURED_OVERNIGHT,
        "value": "531.25",
        "canonical_unit": CanonicalUnit.BASIS_POINTS,
        "rate_compounding": RateCompounding.SIMPLE,
        "day_count": DayCountConvention.ACT_360,
        "event_time": datetime(2026, 8, 7, tzinfo=UTC),
        "source_publication_time": datetime(2026, 8, 8, 8, tzinfo=UTC),
        "knowledge_time": datetime(2026, 8, 8, 8, 1, tzinfo=UTC),
        "revision_id": "initial",
        "source": "official-test",
        "evidence_hash": evidence_sha256("source row"),
        "connector_classification": ConnectorClassification.OFFICIAL_OPEN,
        "redistribution_status": RedistributionStatus.ALLOWED,
        "quality": QualityState.VERIFIED,
        "staleness": StalenessState.FRESH,
    }
    values.update(overrides)
    return Observation(**values)


def test_observation_normalizes_identifiers_and_round_trips() -> None:
    observation = _rate_observation()

    assert observation.market_id == "US-USD"
    assert observation.currency == "USD"
    assert observation.jurisdiction_codes == ("US",)
    assert Observation.from_record(observation.to_record()) == observation


def test_observation_requires_per_row_aware_clocks() -> None:
    with pytest.raises(ValueError, match="event_time must be timezone-aware"):
        _rate_observation(event_time=datetime(2026, 8, 7))

    with pytest.raises(ValueError, match="cannot precede"):
        _rate_observation(
            knowledge_time=datetime(2026, 8, 8, 7, tzinfo=UTC),
        )


def test_unknown_publication_round_trips_without_inventing_a_clock() -> None:
    observation = _rate_observation(source_publication_time=None)
    assert observation.source_publication_time is None
    assert observation.to_record()["source_publication_time"] is None
    assert Observation.from_record(observation.to_record()) == observation
    assert observation.usable
    assert observation.knowledge_time == datetime(2026, 8, 8, 8, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="knowledge_time must be timezone-aware"):
        _rate_observation(source_publication_time=None, knowledge_time=datetime(2026, 8, 8))
    with pytest.raises(ValueError, match="source_publication_time must be timezone-aware"):
        _rate_observation(source_publication_time=datetime(2026, 8, 8))


def test_known_publication_keeps_original_record_hash_and_explicit_null_order() -> None:
    import hashlib
    import json

    known = _rate_observation()
    original_hash = "19e22a80109c39591a1474b0d25d5bdd6f870a00ebfce97eb30cf0b45ce5731f"
    encoded = json.dumps(known.to_record(), sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(encoded.encode()).hexdigest() == original_hash
    earlier = datetime(2026, 8, 8, 7, tzinfo=UTC)
    clocks = [known.source_publication_time, None, earlier, None]
    assert sorted(clocks, key=publication_time_order_key) == [
        None, None, earlier, known.source_publication_time,
    ]
    assert max(clocks, key=publication_time_order_key) == known.source_publication_time


def test_missing_observation_is_unavailable_not_zero() -> None:
    unavailable = _rate_observation(
        value=None,
        quality=QualityState.UNAVAILABLE,
        staleness=StalenessState.UNAVAILABLE,
        connector_classification=ConnectorClassification.UNAVAILABLE,
        redistribution_status=RedistributionStatus.METADATA_ONLY,
    )
    assert unavailable.value is None
    assert unavailable.usable is False

    with pytest.raises(ValueError, match="null value"):
        _rate_observation(value=None)


def test_rate_units_and_licensed_redistribution_are_enforced() -> None:
    with pytest.raises(ValueError, match="basis points"):
        _rate_observation(canonical_unit=CanonicalUnit.INDEX_POINTS)

    with pytest.raises(ValueError, match="licensed inputs"):
        _rate_observation(
            connector_classification=ConnectorClassification.LICENSED,
            redistribution_status=RedistributionStatus.ALLOWED,
        )


def test_compounded_overnight_averages_are_rates_but_index_is_not() -> None:
    average_roles = {
        SemanticRole.COMPOUNDED_OVERNIGHT_AVERAGE_30D,
        SemanticRole.COMPOUNDED_OVERNIGHT_AVERAGE_90D,
        SemanticRole.COMPOUNDED_OVERNIGHT_AVERAGE_180D,
    }

    assert average_roles <= RATE_ROLES
    assert SemanticRole.COMPOUNDED_OVERNIGHT_RATE_INDEX not in RATE_ROLES
    average = _rate_observation(
        semantic_role=SemanticRole.COMPOUNDED_OVERNIGHT_AVERAGE_30D,
        rate_compounding=RateCompounding.COMPOUNDED,
    )
    index = _rate_observation(
        instrument_id="US.TEST.RATE_INDEX",
        semantic_role=SemanticRole.COMPOUNDED_OVERNIGHT_RATE_INDEX,
        canonical_unit=CanonicalUnit.INDEX_POINTS,
        rate_compounding=None,
        day_count=None,
    )

    assert average.rate_compounding is RateCompounding.COMPOUNDED
    assert average.canonical_unit is CanonicalUnit.BASIS_POINTS
    assert index.canonical_unit is CanonicalUnit.INDEX_POINTS
    assert index.rate_compounding is None
    assert Observation.from_record(index.to_record()) == index

    with pytest.raises(ValueError, match="basis points"):
        _rate_observation(
            semantic_role=SemanticRole.COMPOUNDED_OVERNIGHT_AVERAGE_90D,
            canonical_unit=CanonicalUnit.INDEX_POINTS,
        )
    with pytest.raises(ValueError, match="non-rate observations"):
        _rate_observation(
            semantic_role=SemanticRole.COMPOUNDED_OVERNIGHT_RATE_INDEX,
            canonical_unit=CanonicalUnit.INDEX_POINTS,
        )
