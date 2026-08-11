from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

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
from seiche.domain.forward_record import market_snapshot_row_hash
from seiche.repository import PostgresMarketRepository


def _release_handoff(producer_sha: str, receipt: dict, payload: dict) -> dict:
    payload_json = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    body = {
        "schema": "seiche.snapshot-handoff.v1",
        "producer_sha": producer_sha,
        "payload_sha256": hashlib.sha256(payload_json.encode()).hexdigest(),
        "release_receipt": receipt,
        "payload": payload,
    }
    body_json = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return {
        **body,
        "handoff_id": hashlib.sha256(body_json.encode()).hexdigest(),
    }


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
    assert observation in repository.load_observation_revisions_as_of(
        "US-USD", knowledge
    )
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
    assert (
        repository.load_latest_market_snapshot("US-USD", "postgres-integration")[
            "payload"
        ]
        == payload
    )
    assert (
        repository.load_market_snapshot_as_of(
            "US-USD", "postgres-integration", knowledge
        )["snapshot_id"]
        == snapshot_id
    )

    staging_suffix = uuid4().hex
    staged_products = (
        f"postgres-staged-overview-{staging_suffix}",
        f"postgres-staged-gauge-{staging_suffix}",
    )
    staged_payloads = (
        {
            "schema": "seiche.postgres-staged.v1",
            "value": 43,
            "evidence_eligibility": {"eligible": True},
        },
        {
            "schema": "seiche.postgres-staged.v1",
            "value": 44,
            "evidence_eligibility": {"eligible": True},
        },
    )
    staged_ids = tuple(
        repository.seal_market_snapshot(
            market_id="US-USD",
            product=product,
            event_cutoff=event,
            knowledge_cutoff=knowledge,
            calibration_id="postgres-staging-integration-v1",
            evidence_eligible=True,
            payload=staged_payload,
            promoted=False,
        )
        for product, staged_payload in zip(staged_products, staged_payloads)
    )
    staged_forward_ids = tuple(
        repository.append_forward_record(
            snapshot_id=staged_id,
            market_id="US-USD",
            product=product,
            event_cutoff=event,
            knowledge_cutoff=knowledge,
            calibration_id="postgres-staging-integration-v1",
            payload=staged_payload,
        )
        for product, staged_payload, staged_id in zip(
            staged_products, staged_payloads, staged_ids, strict=True
        )
    )
    staged_row_hashes = tuple(
        market_snapshot_row_hash(
            repository.load_staged_market_snapshot(snapshot_id) or {}
        )
        for snapshot_id in staged_ids
    )
    bindings = tuple(
        zip(
            staged_products,
            staged_ids,
            staged_forward_ids,
            staged_row_hashes,
            strict=True,
        )
    )
    assert all(
        repository.load_latest_market_snapshot("US-USD", product) is None
        for product in staged_products
    )

    # The integration database is intentionally persistent across test runs.
    # Give each invocation its own release identity and monotonic receipt clock.
    producer_sha = hashlib.sha256(staging_suffix.encode()).hexdigest()[:40]
    run_generated_at = datetime.now(UTC).isoformat(timespec="microseconds")
    envelope = _release_handoff(
        producer_sha,
        {
            "generated_at": run_generated_at,
            "products": {
                product: {
                    "snapshot_id": staged_id,
                    "forward_record_id": forward_record_id,
                    "snapshot_row_sha256": snapshot_row_hash,
                }
                for product, staged_id, forward_record_id, snapshot_row_hash in zip(
                    staged_products,
                    staged_ids,
                    staged_forward_ids,
                    staged_row_hashes,
                    strict=True,
                )
            },
        },
        {
            "generated_at": run_generated_at,
            "products": list(staged_products),
        },
    )
    handoff_id = envelope["handoff_id"]
    active_before = repository.load_active_release_handoff()
    assert repository.load_release_handoff(handoff_id) is None
    repository.stage_release_handoff(handoff_id, producer_sha, envelope)
    repository.stage_release_handoff(handoff_id, producer_sha, envelope)
    assert repository.load_release_handoff(handoff_id) == envelope
    with pytest.raises(ValueError, match="different producer or envelope"):
        repository.stage_release_handoff(
            handoff_id,
            producer_sha,
            {**envelope, "payload": {"products": ["changed"]}},
        )

    missing_id = uuid4().hex + uuid4().hex
    with pytest.raises(ValueError, match="locked handoff"):
        repository.activate_release_handoff(
            handoff_id,
            producer_sha,
            (
                bindings[0],
                (staged_products[1], missing_id, "f" * 64, "e" * 64),
            ),
        )
    assert all(
        repository.load_latest_market_snapshot("US-USD", product) is None
        for product in staged_products
    )
    assert repository.load_active_release_handoff() == active_before

    staging_columns = (
        "snapshot_id,market_id,product,event_cutoff,knowledge_cutoff,"
        "sealed_at,calibration_id,evidence_eligible,payload_hash,payload"
    )
    with repository._connect() as connection:
        deleted_staging = connection.execute(
            """SELECT snapshot_id,market_id,product,event_cutoff,
                      knowledge_cutoff,sealed_at,calibration_id,
                      evidence_eligible,payload_hash,payload::text
                 FROM market_snapshot_staging WHERE snapshot_id=%s""",
            (staged_ids[1],),
        ).fetchone()
        connection.execute(
            "DELETE FROM market_snapshot_staging WHERE snapshot_id=%s",
            (staged_ids[1],),
        )
    with pytest.raises(ValueError, match="missing market snapshot"):
        repository.activate_release_handoff(handoff_id, producer_sha, bindings)
    with repository._connect() as connection:
        connection.execute(
            f"""INSERT INTO market_snapshot_staging ({staging_columns})
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
            deleted_staging,
        )
    assert (
        market_snapshot_row_hash(
            repository.load_staged_market_snapshot(staged_ids[1]) or {}
        )
        == staged_row_hashes[1]
    )

    with repository._connect() as connection:
        original_market_id = connection.execute(
            "SELECT market_id FROM market_snapshot_staging WHERE snapshot_id=%s",
            (staged_ids[0],),
        ).fetchone()[0]
        connection.execute(
            "UPDATE market_snapshot_staging SET market_id=%s WHERE snapshot_id=%s",
            (original_market_id.lower(), staged_ids[0]),
        )
    with pytest.raises(ValueError, match="market_id is not canonical uppercase"):
        repository.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert repository.load_active_release_handoff() == active_before
    with repository._connect() as connection:
        connection.execute(
            "UPDATE market_snapshot_staging SET market_id=%s WHERE snapshot_id=%s",
            (original_market_id, staged_ids[0]),
        )

    # TIMESTAMPTZ canonicalizes equivalent offsets on write, so PostgreSQL cannot
    # retain the raw-offset tamper exercised by SQLite. sealed_at remains a
    # representable non-identity row mutation and must still be receipt-bound.
    with repository._connect() as connection:
        original_sealed_at = connection.execute(
            "SELECT sealed_at FROM market_snapshot_staging WHERE snapshot_id=%s",
            (staged_ids[0],),
        ).fetchone()[0]
        connection.execute(
            "UPDATE market_snapshot_staging SET sealed_at=%s WHERE snapshot_id=%s",
            (datetime(2000, 1, 1, tzinfo=UTC), staged_ids[0]),
        )
    with pytest.raises(ValueError, match="row differs from the release receipt"):
        repository.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert repository.load_active_release_handoff() == active_before
    with repository._connect() as connection:
        connection.execute(
            "UPDATE market_snapshot_staging SET sealed_at=%s WHERE snapshot_id=%s",
            (original_sealed_at, staged_ids[0]),
        )

    with repository._connect() as connection:
        original_staging = connection.execute(
            """SELECT evidence_eligible, payload_hash, payload::text
                 FROM market_snapshot_staging WHERE snapshot_id=%s""",
            (staged_ids[0],),
        ).fetchone()
        connection.execute(
            """UPDATE market_snapshot_staging
                  SET payload_hash=%s, payload=%s::jsonb WHERE snapshot_id=%s""",
            ("0" * 64, '{"tampered":true}', staged_ids[0]),
        )
    with pytest.raises(ValueError, match="payload"):
        repository.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert repository.load_active_release_handoff() == active_before
    with repository._connect() as connection:
        connection.execute(
            """UPDATE market_snapshot_staging
                  SET evidence_eligible=%s, payload_hash=%s, payload=%s::jsonb
                WHERE snapshot_id=%s""",
            (*original_staging, staged_ids[0]),
        )

    with repository._connect() as connection:
        connection.execute(
            """UPDATE market_snapshot_staging SET evidence_eligible=FALSE
                WHERE snapshot_id=%s""",
            (staged_ids[0],),
        )
    with pytest.raises(ValueError, match="row differs from the release receipt"):
        repository.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert repository.load_active_release_handoff() == active_before
    with repository._connect() as connection:
        connection.execute(
            """UPDATE market_snapshot_staging SET evidence_eligible=TRUE
                WHERE snapshot_id=%s""",
            (staged_ids[0],),
        )

    with repository._connect() as connection:
        columns = (
            "snapshot_id,market_id,product,event_cutoff,knowledge_cutoff,"
            "sealed_at,calibration_id,evidence_eligible,payload_hash,payload"
        )
        connection.execute(
            f"""INSERT INTO market_snapshots ({columns})
                 SELECT {columns} FROM market_snapshot_staging
                  WHERE snapshot_id=%s ON CONFLICT DO NOTHING""",
            (staged_ids[0],),
        )
        connection.execute(
            """UPDATE market_snapshots SET payload_hash=%s, payload=%s::jsonb
                WHERE snapshot_id=%s""",
            ("0" * 64, '{"tampered":true}', staged_ids[0]),
        )
    with pytest.raises(RuntimeError, match="canonical rows differ"):
        repository.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert repository.load_active_release_handoff() == active_before
    with repository._connect() as connection:
        connection.execute(
            "DELETE FROM market_snapshots WHERE snapshot_id=%s",
            (staged_ids[0],),
        )

    with pytest.raises(ValueError, match="producer mismatch"):
        repository.activate_release_handoff(handoff_id, "e" * 40, bindings)
    assert all(
        repository.load_latest_market_snapshot("US-USD", product) is None
        for product in staged_products
    )
    assert repository.load_active_release_handoff() == active_before

    repository.activate_release_handoff(handoff_id, producer_sha, bindings)
    promoted = tuple(
        repository.load_latest_market_snapshot("US-USD", product)
        for product in staged_products
    )
    assert tuple(row["snapshot_id"] for row in promoted) == staged_ids
    assert tuple(row["payload"] for row in promoted) == staged_payloads
    assert repository.load_active_release_handoff() == envelope

    # Exact replay proves activation retained its staged source bundle.
    repository.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert repository.load_active_release_handoff() == envelope
    newer_receipt = json.loads(json.dumps(envelope["release_receipt"]))
    newer_generated_at = (
        datetime.fromisoformat(run_generated_at) + timedelta(minutes=1)
    ).isoformat(timespec="microseconds")
    newer_receipt["generated_at"] = newer_generated_at
    newer_envelope = _release_handoff(
        producer_sha,
        newer_receipt,
        {
            "generated_at": newer_generated_at,
            "products": list(staged_products),
        },
    )
    repository.stage_release_handoff(
        newer_envelope["handoff_id"], producer_sha, newer_envelope
    )
    repository.activate_release_handoff(
        newer_envelope["handoff_id"], producer_sha, bindings
    )
    assert repository.load_active_release_handoff() == newer_envelope
    with pytest.raises(ValueError, match="cannot regress"):
        repository.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert repository.load_active_release_handoff() == newer_envelope

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
    assert (
        repository.append_forward_record(
            snapshot_id=snapshot_id,
            market_id="US-USD",
            product="postgres-integration",
            event_cutoff=event,
            knowledge_cutoff=knowledge,
            calibration_id="postgres-integration-v1",
            payload=payload,
        )
        == record_id
    )
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
        "chain_generation",
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
