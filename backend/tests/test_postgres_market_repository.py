from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

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
from seiche.repository import PostgresMarketRepository


pytestmark = pytest.mark.skipif(
    not os.getenv("SEICHE_TEST_POSTGRES_URL"),
    reason="SEICHE_TEST_POSTGRES_URL is not configured",
)


def test_postgres_round_trip_covers_the_complete_market_repository() -> None:
    repository = PostgresMarketRepository(os.environ["SEICHE_TEST_POSTGRES_URL"])
    event = datetime(2026, 8, 8, tzinfo=UTC)
    knowledge = event + timedelta(hours=9)
    observation = Observation(
        market_id="US-USD",
        monetary_area_id="US",
        jurisdiction_codes=("US",),
        currency="USD",
        instrument_id="US.TEST.POSTGRES.SOFR",
        semantic_role=SemanticRole.SECURED_OVERNIGHT,
        value="531",
        canonical_unit=CanonicalUnit.BASIS_POINTS,
        rate_compounding=RateCompounding.SIMPLE,
        day_count=DayCountConvention.ACT_360,
        event_time=event,
        source_publication_time=knowledge - timedelta(hours=1),
        knowledge_time=knowledge,
        revision_id="postgres-integration-v1",
        source="postgres-integration",
        evidence_hash=evidence_sha256("postgres integration observation"),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.ALLOWED,
        quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )

    assert repository.save_observations([observation]) in {0, 1}
    loaded = repository.load_observations_as_of("US-USD", knowledge)
    assert observation in loaded
    assert any(
        row["semantic_role"] == "SECURED_OVERNIGHT"
        for row in repository.canonical_coverage("US-USD")
    )

    later_event = event + timedelta(days=1)
    later_knowledge = later_event + timedelta(hours=9)
    later = replace(
        observation,
        instrument_id="US.TEST.POSTGRES.LATER",
        event_time=later_event,
        source_publication_time=later_knowledge - timedelta(hours=1),
        knowledge_time=later_knowledge,
        revision_id="postgres-integration-later-v1",
        evidence_hash=evidence_sha256("postgres integration later observation"),
    )
    other_source = replace(
        later,
        instrument_id="US.TEST.POSTGRES.OTHER_SOURCE",
        source="postgres-other-source",
        revision_id="postgres-integration-other-source-v1",
        evidence_hash=evidence_sha256("postgres integration other source"),
    )
    assert repository.save_observations([later, other_source]) in {0, 1, 2}
    filtered = repository.load_observations_as_of(
        "US-USD",
        later_knowledge,
        event_time_from=later_event,
        instrument_ids=(later.instrument_id, other_source.instrument_id),
        sources=(observation.source,),
    )
    assert filtered == [later]
    assert repository.latest_observation_hashes(
        "US-USD",
        later_knowledge,
        event_time_from=later_event,
        instrument_ids=(later.instrument_id, other_source.instrument_id),
        sources=(observation.source,),
    ) == {(later.instrument_id, later.event_time): later.evidence_hash}

    page, cursor = repository.load_observation_page(
        "US-USD",
        later_knowledge,
        limit=1,
        instrument_ids=(observation.instrument_id, later.instrument_id),
        redistribution_statuses=(RedistributionStatus.ALLOWED,),
    )
    assert page == [later]
    assert cursor == (later.event_time, later.instrument_id)
    older, end_cursor = repository.load_observation_page(
        "US-USD",
        later_knowledge,
        limit=1,
        instrument_ids=(observation.instrument_id, later.instrument_id),
        redistribution_statuses=(RedistributionStatus.ALLOWED,),
        before=cursor,
    )
    assert older == [observation]
    assert end_cursor is None

    revised = replace(
        observation,
        value="532",
        source_publication_time=knowledge + timedelta(days=1, hours=-1),
        knowledge_time=knowledge + timedelta(days=1),
        revision_id="postgres-integration-v2",
        evidence_hash=evidence_sha256("postgres integration observation v2"),
        quality=QualityState.REVISED,
    )
    assert repository.save_observations([revised]) in {0, 1}
    assert repository.load_observation_revisions(
        "us-usd",
        revised.knowledge_time,
        instrument_ids=(observation.instrument_id,),
        event_time_from=event,
        event_time=event,
    ) == [observation, revised]
    assert repository.load_observation_revisions(
        "US-USD", revised.knowledge_time, instrument_ids=()
    ) == []

    pagination_instrument = "US.TEST.POSTGRES.VISIBLE_PAGE"
    pagination_start = event + timedelta(days=10)

    def pagination_observation(
        event_offset: int,
        *,
        knowledge_offset: int,
        revision_id: str,
        redistribution_status: RedistributionStatus,
    ) -> Observation:
        row_event = pagination_start + timedelta(days=event_offset)
        row_knowledge = pagination_start + timedelta(days=knowledge_offset, hours=9)
        return replace(
            observation,
            instrument_id=pagination_instrument,
            value=str(600 + event_offset),
            event_time=row_event,
            source_publication_time=row_knowledge - timedelta(hours=1),
            knowledge_time=row_knowledge,
            revision_id=revision_id,
            evidence_hash=evidence_sha256(
                f"postgres visible page {event_offset} {revision_id}"
            ),
            redistribution_status=redistribution_status,
        )

    older_allowed = [
        pagination_observation(
            offset,
            knowledge_offset=offset,
            revision_id=f"allowed-{offset}",
            redistribution_status=RedistributionStatus.ALLOWED,
        )
        for offset in (0, 1)
    ]
    newest_prohibited = [
        pagination_observation(
            offset,
            knowledge_offset=offset,
            revision_id=f"prohibited-{offset}",
            redistribution_status=RedistributionStatus.PROHIBITED,
        )
        for offset in (2, 3)
    ]
    newest_old_allowed = pagination_observation(
        4,
        knowledge_offset=4,
        revision_id="allowed-4",
        redistribution_status=RedistributionStatus.ALLOWED,
    )
    newest_revised_prohibited = pagination_observation(
        4,
        knowledge_offset=5,
        revision_id="prohibited-4",
        redistribution_status=RedistributionStatus.PROHIBITED,
    )
    repository.save_observations(
        [
            *older_allowed,
            *newest_prohibited,
            newest_old_allowed,
            newest_revised_prohibited,
        ]
    )

    visible_page, visible_cursor = repository.load_observation_page(
        "US-USD",
        pagination_start + timedelta(days=6),
        limit=2,
        instrument_ids=(pagination_instrument,),
        redistribution_statuses=(RedistributionStatus.ALLOWED,),
    )
    assert visible_page == list(reversed(older_allowed))
    assert visible_cursor is None
    assert newest_old_allowed not in visible_page

    run = {
        "market_id": "US-USD",
        "adapter_id": "postgres_integration",
        "status": "SUCCESS",
        "started_at": knowledge.isoformat(),
        "finished_at": knowledge.isoformat(),
        "observations_written": 1,
        "attempts": 1,
        "next_due": (knowledge + timedelta(days=1)).isoformat(),
        "fault": None,
    }
    repository.save_collector_run(run)
    assert any(
        item["adapter_id"] == "postgres_integration"
        for item in repository.latest_collector_runs("US-USD")
    )

    payload = {"schema": "seiche.postgres-integration.v1", "value": 42}
    snapshot_id = repository.seal_market_snapshot(
        market_id="US-USD",
        product="postgres-integration",
        event_cutoff=event,
        knowledge_cutoff=knowledge,
        calibration_id="postgres-integration-v1",
        evidence_eligible=True,
        payload=payload,
    )
    assert repository.load_latest_market_snapshot(
        "US-USD", "postgres-integration"
    )["payload"] == payload
    assert repository.load_market_snapshot_as_of(
        "US-USD", "postgres-integration", knowledge
    )["snapshot_id"] == snapshot_id

    record_id = repository.append_forward_record(
        snapshot_id=snapshot_id,
        market_id="US-USD",
        product="postgres-integration",
        event_cutoff=event,
        knowledge_cutoff=knowledge,
        calibration_id="postgres-integration-v1",
        payload=payload,
    )
    assert record_id
    assert repository.append_forward_record(
        snapshot_id=snapshot_id,
        market_id="US-USD",
        product="postgres-integration",
        event_cutoff=event,
        knowledge_cutoff=knowledge,
        calibration_id="postgres-integration-v1",
        payload=payload,
    ) == record_id
    assert repository.forward_record_count("US-USD") >= 1

    forward_records = repository.load_forward_records(
        "us-usd", "postgres-integration"
    )
    record = next(item for item in forward_records if item["record_id"] == record_id)
    assert list(record) == [
        "record_id",
        "snapshot_id",
        "market_id",
        "product",
        "event_cutoff",
        "knowledge_cutoff",
        "calibration_id",
        "created_at",
        "payload_hash",
        "previous_record_hash",
        "record_hash",
        "payload",
    ]
    assert record["snapshot_id"] == snapshot_id
    assert record["payload"] == payload
    assert record["record_hash"] == record_id
    assert [
        (item["created_at"], item["record_id"]) for item in forward_records
    ] == sorted(
        (item["created_at"], item["record_id"]) for item in forward_records
    )
    assert record_id in {
        item["record_id"]
        for item in repository.load_forward_records(product="postgres-integration")
    }
