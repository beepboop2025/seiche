from __future__ import annotations

import hashlib
import json
import math
import os
import threading
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


@pytest.fixture
def isolated_legacy_postgres():
    """Use an owned schema so migration tests cannot rewrite another test's rows."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from seiche.repository import _POSTGRES_SCHEMA

    base_dsn = os.environ["SEICHE_TEST_POSTGRES_URL"]
    schema = "nullable_publication_" + uuid4().hex
    with psycopg.connect(base_dsn) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    repository = PostgresMarketRepository(make_conninfo(base_dsn, options=f"-csearch_path={schema}"))
    try:
        with repository._connect() as connection:
            for statement in _POSTGRES_SCHEMA.split(";"):
                statement = statement.strip()
                if statement.startswith("CREATE TABLE IF NOT EXISTS canonical_observations"):
                    connection.execute(statement.replace(
                        "source_publication_time TIMESTAMPTZ,",
                        "source_publication_time TIMESTAMPTZ NOT NULL,",
                    ))
                elif statement.startswith("CREATE INDEX IF NOT EXISTS canonical_observations"):
                    connection.execute(statement)
        yield repository
    finally:
        with psycopg.connect(base_dsn) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _nullable_test_observation() -> Observation:
    event = datetime(2026, 1, 2, tzinfo=UTC)
    return Observation(
        market_id="US-USD", monetary_area_id="US", jurisdiction_codes=("US",), currency="USD",
        instrument_id="US.TEST.NULLABLE", semantic_role=SemanticRole.SECURED_OVERNIGHT,
        value="500.00", canonical_unit=CanonicalUnit.BASIS_POINTS,
        rate_compounding=RateCompounding.SIMPLE, day_count=DayCountConvention.ACT_360,
        event_time=event, knowledge_time=event + timedelta(days=1, hours=9),
        source_publication_time=event + timedelta(days=1, hours=8), revision_id="a-known",
        source="a-official-test", evidence_hash=evidence_sha256("nullable-publication-original"),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.ALLOWED, quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )


def _insert_legacy_postgres(connection, observation):
    from seiche.repository import (
        _OBSERVATION_INSERT_COLUMNS, _OBSERVATION_INSERT_PLACEHOLDERS, _observation_values,
    )

    connection.execute(
        f"INSERT INTO canonical_observations ({','.join(_OBSERVATION_INSERT_COLUMNS)}) "
        f"VALUES ({','.join(_OBSERVATION_INSERT_PLACEHOLDERS)})", _observation_values(observation),
    )


def test_postgres_existing_nullable_migration_preserves_hashes_rows_and_indexes(isolated_legacy_postgres):
    repository = isolated_legacy_postgres
    known = _nullable_test_observation()
    with repository._connect() as connection:
        _insert_legacy_postgres(connection, known)
        before = connection.execute("SELECT ctid,* FROM canonical_observations").fetchall()
        indexes = connection.execute(
            "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname=current_schema() ORDER BY indexname"
        ).fetchall()
    # Exercise the ordinary writer entrypoint, not a test-only migration call.
    unknown = replace(known, source_publication_time=None, revision_id="z-unknown", value="900",
                      evidence_hash=evidence_sha256("unknown-publication"))
    assert repository.save_observations([known, unknown]) == 1
    assert repository.save_observations([known, unknown]) == 0
    with repository._connect() as connection:
        assert connection.execute(
            "SELECT ctid,* FROM canonical_observations WHERE revision_id='a-known'"
        ).fetchall() == before
        assert connection.execute(
            "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname=current_schema() "
            "AND tablename='canonical_observations' ORDER BY indexname"
        ).fetchall() == indexes
        assert connection.execute(
            "SELECT attnotnull FROM pg_attribute WHERE attrelid='canonical_observations'::regclass "
            "AND attname='source_publication_time'"
        ).fetchone() == (False,)
    assert repository.load_observation_revisions_as_of("US-USD", known.knowledge_time) == [unknown, known]
    with pytest.raises(ValueError, match="identity collision"):
        repository.save_observations([replace(known, source_publication_time=None)])
    # A new process/repository repeats schema convergence without modifying rows.
    restarted = PostgresMarketRepository(repository.dsn)
    assert restarted.load_observation_revisions_as_of("US-USD", known.knowledge_time) == [unknown, known]


def test_postgres_nullable_migration_rolls_back_with_later_schema_failure(isolated_legacy_postgres, monkeypatch):
    import psycopg
    import seiche.repository as module

    repository = isolated_legacy_postgres
    known = _nullable_test_observation()
    with repository._connect() as connection:
        _insert_legacy_postgres(connection, known)
        before = connection.execute("SELECT ctid,* FROM canonical_observations").fetchall()
    schema = module._POSTGRES_SCHEMA
    monkeypatch.setattr(module, "_POSTGRES_SCHEMA", schema + ";SELECT * FROM missing_migration_failure_fixture;")
    with pytest.raises(psycopg.errors.UndefinedTable):
        repository._ensure_schema()
    assert not repository._initialized
    with repository._connect() as connection:
        assert connection.execute("SELECT ctid,* FROM canonical_observations").fetchall() == before
        assert connection.execute(
            "SELECT attnotnull FROM pg_attribute WHERE attrelid='canonical_observations'::regclass "
            "AND attname='source_publication_time'"
        ).fetchone() == (True,)
    monkeypatch.setattr(module, "_POSTGRES_SCHEMA", schema)
    assert repository.save_observations([replace(known, source_publication_time=None, revision_id="retry")]) == 1


def test_mixed_publication_clocks_rank_identically_in_all_postgres_queries(isolated_legacy_postgres):
    repository = isolated_legacy_postgres
    known = _nullable_test_observation()
    unknown = replace(known, source_publication_time=None, revision_id="z-unknown", value="900",
                      evidence_hash=evidence_sha256("unknown-publication"))
    source_tie = replace(known, source="z-official-test", value="501",
                         evidence_hash=evidence_sha256("known-publication-source-tie"))
    assert repository.save_observations([source_tie, unknown, known]) == 3
    cutoff = known.knowledge_time
    assert repository.load_observation_revisions("US-USD", cutoff) == [unknown, known, source_tie]
    assert repository.load_observation_revisions_as_of("US-USD", cutoff) == [unknown, known, source_tie]
    assert repository.load_observations_as_of("US-USD", cutoff) == [source_tie]
    assert repository.load_observation_page("US-USD", cutoff, limit=1)[0] == [source_tie]
    assert repository.latest_observation_hashes("US-USD", cutoff) == {
        (known.instrument_id, known.event_time): source_tie.evidence_hash,
    }
    assert repository.load_observations_batch_as_of(
        {"US-USD": (known.instrument_id,)}, cutoff, event_time=cutoff,
        event_time_from=known.event_time,
    ) == {"US-USD": [source_tie]}
    assert repository.load_latest_observations_by_instrument(
        "US-USD", cutoff, event_time=cutoff, instrument_ids=(known.instrument_id,),
        redistribution_statuses=(RedistributionStatus.ALLOWED,),
    ) == {known.instrument_id: source_tie}
    later_unknown = replace(unknown, knowledge_time=cutoff + timedelta(days=1))
    repository.save_observations([later_unknown])
    assert repository.load_observations_as_of("US-USD", later_unknown.knowledge_time) == [later_unknown]
    assert repository.load_observation_page("US-USD", later_unknown.knowledge_time, limit=1)[0] == [later_unknown]
    assert repository.load_observations_as_of("US-USD", cutoff - timedelta(seconds=1)) == []


def test_postgres_missing_history_append_is_atomic_and_preserves_live_vintages(isolated_legacy_postgres):
    repository = isolated_legacy_postgres
    existing = _nullable_test_observation()
    repository.save_observations([existing])
    archive = replace(existing, source_publication_time=None, revision_id="archive", value="900",
                      knowledge_time=existing.knowledge_time + timedelta(days=5))
    missing = replace(archive, event_time=existing.event_time - timedelta(days=1))
    other_source = replace(archive, source="other-official-test")
    assert repository.save_missing_observations([archive, missing, other_source]) == 2
    assert repository.save_missing_observations([archive, missing, other_source]) == 0
    assert repository.load_observation_revisions("US-USD", archive.knowledge_time) == [missing, existing, other_source]
    new_row = replace(missing, instrument_id="US.TEST.ROLLBACK")
    with pytest.raises(ValueError, match="identity collision"):
        repository.save_missing_observations([new_row, replace(missing, value="123")])
    assert repository.load_observation_revisions("US-USD", archive.knowledge_time,
                                                 instrument_ids=(new_row.instrument_id,)) == []
    with pytest.raises(ValueError, match="5000"):
        repository.save_missing_observations([missing] * 5001)


def test_postgres_missing_history_waits_for_concurrent_collector(isolated_legacy_postgres):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    repository = isolated_legacy_postgres
    repository._ensure_schema()
    existing = _nullable_test_observation()
    archive = replace(existing, source_publication_time=None, revision_id="archive",
                      knowledge_time=existing.knowledge_time + timedelta(days=5))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with repository._connect() as collector:
            _insert_legacy_postgres(collector, existing)
            future = pool.submit(repository.save_missing_observations, [archive])
            try:
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.2)
            finally:
                collector.commit()
        assert future.result(timeout=10) == 0
    assert repository.load_observation_revisions("US-USD", archive.knowledge_time) == [existing]


def test_postgres_migration_serializes_initializers_and_nullable_readers_need_no_ddl(isolated_legacy_postgres):
    from concurrent.futures import ThreadPoolExecutor

    repository = isolated_legacy_postgres
    known = _nullable_test_observation()
    with repository._connect() as connection:
        _insert_legacy_postgres(connection, known)
    barrier = threading.Barrier(2)

    def migrate():
        with repository._connect() as connection:
            connection.execute("SET LOCAL lock_timeout='5000ms'")
            barrier.wait(timeout=5)
            repository._converge_nullable_publication_time(connection)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(migrate) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    with repository._connect() as connection:
        calls = []

        class TracedConnection:
            def execute(self, query):
                calls.append(query)
                return connection.execute(query)

        repository._converge_nullable_publication_time(TracedConnection())
        assert len(calls) == 1
        assert calls[0].startswith("SELECT attnotnull FROM pg_attribute")
    unknown = replace(known, source_publication_time=None, revision_id="unknown")
    assert repository.save_observations([unknown]) == 1


def test_postgres_heartbeat_wait_is_bounded_by_statement_timeout() -> None:
    import psycopg

    repository = PostgresMarketRepository(os.environ["SEICHE_TEST_POSTGRES_URL"])
    repository._ensure_schema()
    released = threading.Event()
    with repository._connect() as blocker:
        blocker.execute("SET LOCAL lock_timeout = '5000ms'")
        blocker.execute("LOCK TABLE worker_heartbeats IN ACCESS EXCLUSIVE MODE")

        def watchdog():
            # A missing query deadline must fail the assertion, not hang CI.
            if not released.wait(6):
                blocker.rollback()

        worker = threading.Thread(target=watchdog)
        worker.start()
        try:
            with pytest.raises(psycopg.errors.QueryCanceled):
                repository.load_worker_heartbeat("health-deadline-fixture")
        finally:
            released.set()
            worker.join(timeout=7)
        assert not worker.is_alive()
    assert repository.load_worker_heartbeat("health-deadline-fixture") is None


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

    repository.save_collector_run(
        {
            **run,
            "status": "FAILED",
            "finished_at": (knowledge + timedelta(hours=1)).isoformat(),
            "observations_written": 0,
            "fault": "later source failure",
        }
    )
    latest = next(
        item
        for item in repository.latest_collector_runs("US-USD")
        if item["adapter_id"] == "postgres_integration"
    )
    completed = next(
        item
        for item in repository.latest_collector_runs("US-USD", successful_only=True)
        if item["adapter_id"] == "postgres_integration"
    )
    assert latest["status"] == "FAILED"
    assert completed["status"] == "SUCCESS"
    assert completed["finished_at"] == knowledge.isoformat()

    payload = {
        "schema": "seiche.postgres-integration.v1",
        "value": 42,
        "signed_zero": -0.0,
    }
    snapshot_id = repository.seal_market_snapshot(
        market_id="US-USD",
        product="postgres-integration",
        event_cutoff=event,
        knowledge_cutoff=knowledge,
        calibration_id="postgres-integration-v1",
        evidence_eligible=True,
        payload=payload,
    )
    loaded_snapshot = repository.load_latest_market_snapshot(
        "US-USD", "postgres-integration"
    )
    assert loaded_snapshot["payload"] == payload
    assert math.copysign(1.0, loaded_snapshot["payload"]["signed_zero"]) == 1.0
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
    assert math.copysign(1.0, record["payload"]["signed_zero"]) == 1.0
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


def test_postgres_atlas_batch_matches_individual_histories(monkeypatch) -> None:
    """Exercise actual PostgreSQL parameter binding, window ranking and rows."""

    repository = PostgresMarketRepository(os.environ["SEICHE_TEST_POSTGRES_URL"])
    token = uuid4().hex
    event = datetime(2026, 8, 8, tzinfo=UTC)
    cutoff = event + timedelta(days=2)
    floor = event - timedelta(days=1)
    shared_id = f"TEST.BATCH.{token}.SHARED"
    euro_id = f"TEST.BATCH.{token}.EURO"
    original = Observation(
        market_id="US-USD",
        monetary_area_id="US",
        jurisdiction_codes=("US",),
        currency="USD",
        instrument_id=shared_id,
        semantic_role=SemanticRole.SECURED_OVERNIGHT,
        value="531",
        canonical_unit=CanonicalUnit.BASIS_POINTS,
        rate_compounding=RateCompounding.SIMPLE,
        day_count=DayCountConvention.ACT_360,
        event_time=event,
        source_publication_time=event,
        knowledge_time=event,
        revision_id="v1",
        source="postgres-batch-test",
        evidence_hash=evidence_sha256(f"synthetic postgres batch {token}"),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.ALLOWED,
        quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )
    revised = replace(
        original, value="532", knowledge_time=event + timedelta(hours=1),
        source_publication_time=event + timedelta(hours=1), revision_id="v2",
        redistribution_status=RedistributionStatus.PROHIBITED,
    )
    euro = replace(
        original, market_id="EA-EUR", monetary_area_id="EA", currency="EUR",
        jurisdiction_codes=("DE",), instrument_id=euro_id, value="301",
    )
    earlier = replace(original, event_time=floor)
    later = replace(original, event_time=cutoff)
    repository.save_observations([
        original, revised, euro, earlier, later,
        replace(revised, source_publication_time=event, revision_id="z9"),
        replace(revised, revision_id="v1", value="533"),
        replace(revised, knowledge_time=cutoff + timedelta(seconds=1), revision_id="v3"),
        replace(original, event_time=floor - timedelta(seconds=1)),
        replace(original, event_time=cutoff + timedelta(seconds=1)),
        replace(euro, instrument_id=shared_id),
        replace(original, instrument_id=euro_id),
    ])
    opened = []
    connect = repository._connect

    def counted_connect():
        opened.append(True)
        return connect()

    monkeypatch.setattr(repository, "_connect", counted_connect)
    selections = {"us-usd": (shared_id,), "EA-EUR": (euro_id,), "UK-GBP": ()}
    batch = repository.load_observations_batch_as_of(
        selections, cutoff.isoformat(), event_time=cutoff, event_time_from=floor,
    )
    assert len(opened) == 1
    assert batch == {"US-USD": [earlier, revised, later], "EA-EUR": [euro], "UK-GBP": []}
    assert batch == {
        market.upper(): repository.load_observations_as_of(
            market, cutoff, event_time=cutoff, event_time_from=floor, instrument_ids=instruments,
        )
        for market, instruments in selections.items()
    }
    before_empty = len(opened)
    assert repository.load_observations_batch_as_of(
        {"US-USD": ()}, cutoff, event_time=cutoff, event_time_from=floor,
    ) == {"US-USD": []}
    assert len(opened) == before_empty


def test_postgres_latest_instrument_batch_matches_visible_pages(monkeypatch) -> None:
    """A later rights restriction hides its old vintage before status selection."""

    repository = PostgresMarketRepository(os.environ["SEICHE_TEST_POSTGRES_URL"])
    token = uuid4().hex
    event = datetime(2026, 8, 8, tzinfo=UTC)
    cutoff = event + timedelta(days=2)
    first = Observation(
        market_id="US-USD", monetary_area_id="US", jurisdiction_codes=("US",),
        currency="USD", instrument_id=f"TEST.LATEST.{token}.A",
        semantic_role=SemanticRole.SECURED_OVERNIGHT, value="531",
        canonical_unit=CanonicalUnit.BASIS_POINTS, rate_compounding=RateCompounding.SIMPLE,
        day_count=DayCountConvention.ACT_360, event_time=event,
        source_publication_time=event, knowledge_time=event, revision_id="v1",
        source="a-test", evidence_hash=evidence_sha256(token),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.ALLOWED, quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )
    older = replace(first, event_time=event - timedelta(days=1))
    restricted = replace(first, knowledge_time=event + timedelta(hours=1), revision_id="v2",
                         redistribution_status=RedistributionStatus.PROHIBITED)
    other = replace(first, instrument_id=f"TEST.LATEST.{token}.B")
    source_tie = replace(other, source="z-test", value="532")
    derived = replace(first, instrument_id=f"TEST.LATEST.{token}.C",
                      redistribution_status=RedistributionStatus.DERIVED_ONLY)
    metadata = replace(first, instrument_id=f"TEST.LATEST.{token}.D",
                       redistribution_status=RedistributionStatus.METADATA_ONLY)
    prohibited = replace(first, instrument_id=f"TEST.LATEST.{token}.E",
                         redistribution_status=RedistributionStatus.PROHIBITED)
    repository.save_observations([
        older, first, restricted, other, source_tie, derived, metadata, prohibited,
        replace(first, knowledge_time=cutoff + timedelta(seconds=1), revision_id="future-knowledge"),
        replace(first, event_time=cutoff + timedelta(seconds=1), revision_id="future-event"),
        replace(first, market_id="EA-EUR", currency="EUR", monetary_area_id="EA"),
        replace(first, instrument_id=f"TEST.LATEST.{token}.UNSELECTED"),
    ])
    instruments = (first.instrument_id, other.instrument_id, derived.instrument_id,
                   metadata.instrument_id, prohibited.instrument_id, f"TEST.LATEST.{token}.MISSING")
    opened = []
    connect = repository._connect

    def counted_connect():
        opened.append(True)
        return connect()

    monkeypatch.setattr(repository, "_connect", counted_connect)
    statuses = (RedistributionStatus.ALLOWED, RedistributionStatus.DERIVED_ONLY,
                RedistributionStatus.METADATA_ONLY)
    actual = repository.load_latest_observations_by_instrument(
        "us-usd", cutoff.isoformat(), event_time=cutoff,
        instrument_ids=(*instruments, instruments[0]), redistribution_statuses=statuses,
    )
    assert len(opened) == 1
    assert actual == {row.instrument_id: row for row in (older, source_tie, derived, metadata)}
    expected = {}
    for instrument in instruments:
        page, _ = repository.load_observation_page(
            "US-USD", cutoff, event_time=cutoff, limit=1,
            instrument_ids=(instrument,), redistribution_statuses=statuses,
        )
        if page:
            expected[instrument] = page[0]
    assert actual == expected
    connections = len(opened)
    for selected, allowed in (((), statuses), (instruments, ())):
        assert repository.load_latest_observations_by_instrument(
            "US-USD", cutoff, event_time=cutoff,
            instrument_ids=selected, redistribution_statuses=allowed,
        ) == {}
    assert len(opened) == connections


def test_postgres_catalog_snapshot_batch_matches_individual_reads(monkeypatch) -> None:
    repository = PostgresMarketRepository(os.environ["SEICHE_TEST_POSTGRES_URL"])
    token = uuid4().hex
    product = f"batch-catalog-{token}"
    event = datetime(2026, 8, 8, tzinfo=UTC)
    for market in ("US-USD", "EA-EUR"):
        for offset in (0, 1):
            cutoff = event + timedelta(days=offset)
            repository.seal_market_snapshot(
                market_id=market, product=product, event_cutoff=cutoff,
                knowledge_cutoff=cutoff, calibration_id=token, evidence_eligible=False,
                payload={"market_id": market, "generation": offset},
            )
    opened = []
    connect = repository._connect

    def counted_connect():
        opened.append(True)
        return connect()

    monkeypatch.setattr(repository, "_connect", counted_connect)
    actual = repository.load_latest_market_snapshots(("us-usd", "EA-EUR", "US-USD", "UK-GBP"), product)
    assert len(opened) == 1
    assert actual == {market: repository.load_latest_market_snapshot(market, product)
                      for market in ("US-USD", "EA-EUR", "UK-GBP")}
    assert actual["US-USD"]["payload"]["generation"] == 1
    assert actual["UK-GBP"] is None
    connections = len(opened)
    assert repository.load_latest_market_snapshots((), product) == {}
    assert len(opened) == connections
