from __future__ import annotations

from datetime import UTC, datetime

from seiche import store
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


def _observation(
    *,
    event_day: int,
    knowledge_day: int,
    value: str,
    revision: str,
    market_id: str = "US-USD",
) -> Observation:
    area = market_id.split("-")[0]
    currency = market_id.split("-")[1]
    jurisdiction = "US" if market_id == "US-USD" else "IN"
    return Observation(
        market_id=market_id,
        monetary_area_id=area,
        jurisdiction_codes=(jurisdiction,),
        currency=currency,
        instrument_id=f"{area}.TEST.OVERNIGHT",
        semantic_role=SemanticRole.SECURED_OVERNIGHT,
        value=value,
        canonical_unit=CanonicalUnit.BASIS_POINTS,
        rate_compounding=RateCompounding.SIMPLE,
        day_count=DayCountConvention.ACT_360,
        event_time=datetime(2026, 1, event_day, tzinfo=UTC),
        source_publication_time=datetime(2026, 1, knowledge_day, 8, tzinfo=UTC),
        knowledge_time=datetime(2026, 1, knowledge_day, 9, tzinfo=UTC),
        revision_id=revision,
        source="official-test",
        evidence_hash=evidence_sha256(f"{market_id}:{event_day}:{revision}:{value}"),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.ALLOWED,
        quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )


def test_each_row_keeps_its_own_knowledge_time(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "canonical.sqlite")
    early = _observation(event_day=2, knowledge_day=3, value="500", revision="initial")
    late = _observation(event_day=3, knowledge_day=5, value="510", revision="initial")
    revised = _observation(event_day=2, knowledge_day=6, value="525", revision="revised")
    assert store.save_observations([early, late, revised]) == 3

    as_of_fourth = store.load_observations_as_of(
        "US-USD", datetime(2026, 1, 4, tzinfo=UTC)
    )
    as_of_fifth = store.load_observations_as_of(
        "US-USD", datetime(2026, 1, 5, 12, tzinfo=UTC)
    )
    as_of_sixth = store.load_observations_as_of(
        "US-USD", datetime(2026, 1, 6, 12, tzinfo=UTC)
    )

    assert [str(item.value) for item in as_of_fourth] == ["500"]
    assert [str(item.value) for item in as_of_fifth] == ["500", "510"]
    assert [str(item.value) for item in as_of_sixth] == ["525", "510"]
    assert store.save_observations([early]) == 0


def test_market_identity_prevents_cross_market_collision(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "markets.sqlite")
    usd = _observation(event_day=2, knowledge_day=3, value="500", revision="initial")
    inr = _observation(
        event_day=2,
        knowledge_day=3,
        value="600",
        revision="initial",
        market_id="IN-INR",
    )
    store.save_observations([usd, inr])

    assert store.load_observations_as_of("US-USD", usd.knowledge_time) == [usd]
    assert store.load_observations_as_of("IN-INR", inr.knowledge_time) == [inr]


def test_sealed_snapshots_are_immutable_and_knowledge_queryable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "snapshots.sqlite")
    first_id = store.seal_market_snapshot(
        market_id="US-USD",
        product="gauge",
        event_cutoff="2026-01-02T00:00:00+00:00",
        knowledge_cutoff="2026-01-03T00:00:00+00:00",
        calibration_id="test-v1",
        evidence_eligible=True,
        payload={"value": 10},
    )
    second_id = store.seal_market_snapshot(
        market_id="US-USD",
        product="gauge",
        event_cutoff="2026-01-03T00:00:00+00:00",
        knowledge_cutoff="2026-01-05T00:00:00+00:00",
        calibration_id="test-v1",
        evidence_eligible=True,
        payload={"value": 20},
    )

    assert first_id != second_id
    assert store.load_latest_market_snapshot("US-USD", "gauge")["payload"]["value"] == 20
    historical = store.load_market_snapshot_as_of(
        "US-USD", "gauge", "2026-01-04T00:00:00+00:00"
    )
    assert historical["snapshot_id"] == first_id
