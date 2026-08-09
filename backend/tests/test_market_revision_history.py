from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

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
from seiche.repository import SQLiteMarketRepository


def _observation(
    *,
    instrument_id: str,
    event_time: datetime,
    knowledge_time: datetime,
    revision_id: str,
    value: str,
    source: str = "official-test",
    publication_time: datetime | None = None,
) -> Observation:
    return Observation(
        market_id="US-USD",
        monetary_area_id="US",
        jurisdiction_codes=("US",),
        currency="USD",
        instrument_id=instrument_id,
        semantic_role=SemanticRole.SECURED_OVERNIGHT,
        value=value,
        canonical_unit=CanonicalUnit.BASIS_POINTS,
        rate_compounding=RateCompounding.SIMPLE,
        day_count=DayCountConvention.ACT_360,
        event_time=event_time,
        source_publication_time=publication_time or knowledge_time - timedelta(hours=1),
        knowledge_time=knowledge_time,
        revision_id=revision_id,
        source=source,
        evidence_hash=evidence_sha256(
            f"{instrument_id}:{event_time}:{knowledge_time}:{revision_id}:{source}:{value}"
        ),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.ALLOWED,
        quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )


def test_revision_history_retains_all_vintages_in_contract_order(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "revision-history.sqlite")
    event = datetime(2026, 1, 2, tzinfo=UTC)
    first = _observation(
        instrument_id="US.TEST.A",
        event_time=event,
        knowledge_time=event + timedelta(days=1),
        revision_id="v1",
        value="500",
    )
    revised = replace(
        first,
        value="525",
        knowledge_time=event + timedelta(days=3),
        source_publication_time=event + timedelta(days=3, hours=-1),
        revision_id="v2",
        evidence_hash=evidence_sha256("US.TEST.A revised"),
        quality=QualityState.REVISED,
    )
    alternate_source = replace(
        revised,
        source="secondary-test",
        evidence_hash=evidence_sha256("US.TEST.A revised secondary"),
    )
    other_instrument = _observation(
        instrument_id="US.TEST.B",
        event_time=event,
        knowledge_time=event + timedelta(days=2),
        revision_id="v1",
        value="510",
    )
    later_event = _observation(
        instrument_id="US.TEST.A",
        event_time=event + timedelta(days=1),
        knowledge_time=event + timedelta(days=2),
        revision_id="v1",
        value="515",
    )
    repository = SQLiteMarketRepository()
    repository.save_observations(
        [later_event, alternate_source, revised, other_instrument, first]
    )

    history = repository.load_observation_revisions(
        "us-usd", event + timedelta(days=4)
    )

    assert history == [first, revised, alternate_source, other_instrument, later_event]
    latest = repository.load_observations_as_of(
        "US-USD", event + timedelta(days=4)
    )
    assert len(latest) == 3
    assert latest[0] in {revised, alternate_source}
    assert latest[1:] == [other_instrument, later_event]
    assert first not in latest


def test_revision_history_applies_inclusive_cutoffs_and_instrument_bounds(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "revision-bounds.sqlite")
    start = datetime(2026, 2, 1, tzinfo=UTC)
    rows = [
        _observation(
            instrument_id="US.TEST.A",
            event_time=start + timedelta(days=event_offset),
            knowledge_time=start + timedelta(days=knowledge_offset),
            revision_id=revision_id,
            value=value,
        )
        for event_offset, knowledge_offset, revision_id, value in (
            (0, 1, "a-v1", "500"),
            (0, 4, "a-v2", "520"),
            (1, 2, "a-next", "510"),
        )
    ]
    other = _observation(
        instrument_id="US.TEST.B",
        event_time=start + timedelta(days=1),
        knowledge_time=start + timedelta(days=2),
        revision_id="b-v1",
        value="600",
    )
    store.save_observations([*rows, other])

    bounded = store.load_observation_revisions(
        "us-usd",
        start + timedelta(days=2),
        instrument_ids=("US.TEST.A",),
        event_time_from=start,
        event_time=start + timedelta(days=1),
    )

    assert bounded == [rows[0], rows[2]]
    assert store.load_observation_revisions(
        "US-USD", start + timedelta(days=10), instrument_ids=[]
    ) == []
    with pytest.raises(ValueError, match="timezone-aware"):
        store.load_observation_revisions("US-USD", datetime(2026, 2, 3))
    with pytest.raises(ValueError, match="timezone-aware"):
        store.load_observation_revisions(
            "US-USD",
            start + timedelta(days=10),
            event_time=datetime(2026, 2, 3),
        )
