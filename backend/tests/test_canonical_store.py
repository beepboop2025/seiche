from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from seiche import store
from seiche.domain.forward_record import market_snapshot_row_hash
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


def _create_legacy_canonical_database(path):
    # The preceding schema had exactly this NOT NULL constraint. Include extra
    # schema objects to ensure the rebuild does not silently discard them.
    with store._conn() as template:
        schema = template.execute(
            "SELECT sql FROM sqlite_master WHERE name='canonical_observations'"
        ).fetchone()[0]
    schema = schema.replace("source_publication_time TEXT,", "source_publication_time TEXT NOT NULL,")
    conn = sqlite3.connect(path)
    conn.execute(schema)
    conn.execute("CREATE UNIQUE INDEX legacy_hash_lookup ON canonical_observations(record_hash)")
    conn.execute("CREATE TABLE insert_audit (revision_id TEXT)")
    conn.execute(
        "CREATE TRIGGER legacy_insert_audit AFTER INSERT ON canonical_observations "
        "BEGIN INSERT INTO insert_audit VALUES (NEW.revision_id); END"
    )
    conn.execute("CREATE VIEW legacy_observation_view AS SELECT * FROM canonical_observations")
    conn.execute(
        "CREATE TABLE evidence_reference (record_hash TEXT REFERENCES "
        "canonical_observations(record_hash))"
    )
    row = _observation(event_day=2, knowledge_day=3, value="500.00", revision="original")
    values = store._observation_row(row)
    conn.execute(
        f"INSERT INTO canonical_observations (rowid,{','.join((*store._CANONICAL_COLUMNS, 'record_hash'))}) "
        f"VALUES (97,{','.join('?' for _ in values)})", values,
    )
    conn.execute("INSERT INTO evidence_reference VALUES (?)", (values[-1],))
    conn.commit()
    return conn, row


def test_existing_sqlite_nullable_upgrade_preserves_all_rows_hashes_and_schema(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "template.sqlite")
    database = tmp_path / "legacy.sqlite"
    conn, original = _create_legacy_canonical_database(database)
    conn.execute("PRAGMA foreign_keys=ON")
    before = conn.execute("SELECT rowid,* FROM canonical_observations").fetchall()
    objects = conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE type IN ('index','trigger','view')"
    ).fetchall()
    store._converge_nullable_publication_time(conn)
    assert conn.execute("SELECT rowid,* FROM canonical_observations").fetchall() == before
    assert conn.execute("SELECT * FROM legacy_observation_view").fetchall() == [before[0][1:]]
    assert conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE type IN ('index','trigger','view')"
    ).fetchall() != []
    assert set(conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE type IN ('index','trigger','view')"
    )) == set(objects)
    assert conn.execute("SELECT * FROM insert_audit").fetchall() == [("original",)]
    assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert conn.execute("PRAGMA legacy_alter_table").fetchone() == (0,)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("SELECT * FROM evidence_reference").fetchall() == [(before[0][-1],)]
    assert not next(row for row in conn.execute("PRAGMA table_info(canonical_observations)")
                    if row[1] == "source_publication_time")[3]
    store._converge_nullable_publication_time(conn)
    assert conn.execute("SELECT rowid,* FROM canonical_observations").fetchall() == before
    conn.close()
    monkeypatch.setattr(store, "DB_PATH", database)
    unknown = replace(original, source_publication_time=None, revision_id="unknown")
    assert store.save_observations([original, unknown]) == 1
    assert store.load_observation_revisions("US-USD", original.knowledge_time) == [unknown, original]
    with sqlite3.connect(database) as checked:
        assert checked.execute("SELECT * FROM insert_audit").fetchall() == [("original",), ("unknown",)]
        assert checked.execute("SELECT rowid,* FROM canonical_observations WHERE revision_id='original'").fetchall() == before
    with pytest.raises(ValueError, match="identity collision"):
        store.save_observations([replace(original, source_publication_time=None)])


def test_existing_sqlite_upgrade_is_automatic_and_rolls_back_after_copy_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "template.sqlite")
    database = tmp_path / "legacy.sqlite"
    conn, original = _create_legacy_canonical_database(database)
    conn.execute("PRAGMA foreign_keys=ON")
    before = conn.execute("SELECT type,name,sql FROM sqlite_master").fetchall()
    rows = conn.execute("SELECT rowid,* FROM canonical_observations").fetchall()

    def deny_rename(action, first, second, database_name, trigger):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_ALTER_TABLE else sqlite3.SQLITE_OK

    conn.set_authorizer(deny_rename)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        store._converge_nullable_publication_time(conn)
    conn.set_authorizer(None)
    assert conn.execute("SELECT type,name,sql FROM sqlite_master").fetchall() == before
    assert conn.execute("SELECT rowid,* FROM canonical_observations").fetchall() == rows
    assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert conn.execute("PRAGMA legacy_alter_table").fetchone() == (0,)
    conn.execute("INSERT INTO insert_audit VALUES ('caller-uncommitted')")
    with pytest.raises(RuntimeError, match="clean transaction"):
        store._converge_nullable_publication_time(conn)
    assert conn.in_transaction
    conn.rollback()
    conn.close()
    monkeypatch.setattr(store, "DB_PATH", database)
    assert store.save_observations([replace(original, source_publication_time=None, revision_id="after-retry")]) == 1


def test_mixed_publication_clocks_rank_identically_in_all_sqlite_queries(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "mixed.sqlite")
    known = _observation(event_day=2, knowledge_day=3, value="500", revision="a-known")
    unknown = replace(known, source_publication_time=None, revision_id="z-unknown", value="900",
                      evidence_hash=evidence_sha256("unknown-publication"))
    source_tie = replace(known, source="z-official-test", value="501",
                         evidence_hash=evidence_sha256("known-publication-source-tie"))
    assert store.save_observations([source_tie, unknown, known]) == 3
    cutoff = known.knowledge_time
    assert store.load_observation_revisions("US-USD", cutoff) == [unknown, known, source_tie]
    assert store.load_observation_revisions_as_of("US-USD", cutoff) == [unknown, known, source_tie]
    assert store.load_observations_as_of("US-USD", cutoff) == [source_tie]
    assert store.load_observation_page("US-USD", cutoff, limit=1)[0] == [source_tie]
    assert store.latest_observation_hashes("US-USD", cutoff) == {
        (known.instrument_id, known.event_time): source_tie.evidence_hash,
    }
    later_unknown = replace(unknown, knowledge_time=datetime(2026, 1, 4, tzinfo=UTC))
    store.save_observations([later_unknown])
    assert store.load_observations_as_of("US-USD", later_unknown.knowledge_time) == [later_unknown]
    assert store.load_observation_page("US-USD", later_unknown.knowledge_time, limit=1)[0] == [later_unknown]
    assert store.load_observations_as_of("US-USD", datetime(2026, 1, 3, 8, tzinfo=UTC)) == []


def test_missing_history_append_is_atomic_idempotent_and_preserves_existing_vintages(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "missing.sqlite")
    existing = _observation(event_day=2, knowledge_day=3, value="500", revision="live")
    store.save_observations([existing])
    archive = replace(existing, source_publication_time=None, revision_id="archive",
                      knowledge_time=datetime(2026, 1, 8, tzinfo=UTC), value="900")
    missing = replace(archive, event_time=datetime(2026, 1, 1, tzinfo=UTC))
    other_source = replace(archive, source="other-official-test")
    assert store.save_missing_observations([archive, missing, other_source]) == 2
    assert store.save_missing_observations([archive, missing, other_source]) == 0
    assert store.load_observation_revisions("US-USD", archive.knowledge_time) == [missing, existing, other_source]
    new_row = replace(missing, instrument_id="US.TEST.ROLLBACK")
    with pytest.raises(ValueError, match="identity collision"):
        store.save_missing_observations([new_row, replace(missing, value="123")])
    assert store.load_observation_revisions("US-USD", archive.knowledge_time,
                                             instrument_ids=(new_row.instrument_id,)) == []
    with pytest.raises(ValueError, match="5000"):
        store.save_missing_observations([missing] * 5001)


def test_missing_history_waits_for_concurrent_sqlite_collector(tmp_path, monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "concurrent.sqlite")
    with store._conn():
        pass
    existing = _observation(event_day=2, knowledge_day=3, value="500", revision="live")
    archive = replace(existing, source_publication_time=None, revision_id="archive",
                      knowledge_time=datetime(2026, 1, 8, tzinfo=UTC))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with sqlite3.connect(store.DB_PATH) as collector:
            collector.execute("BEGIN IMMEDIATE")
            values = store._observation_row(existing)
            collector.execute(
                f"INSERT INTO canonical_observations VALUES ({','.join('?' for _ in values)})", values,
            )
            future = pool.submit(store.save_missing_observations, [archive])
            try:
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.2)
            finally:
                collector.commit()
        assert future.result(timeout=10) == 0
    assert store.load_observation_revisions("US-USD", archive.knowledge_time) == [existing]


def test_upgraded_sqlite_schema_only_reads_column_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "upgraded.sqlite")
    with store._conn() as connection:
        queries = []
        connection.set_trace_callback(queries.append)
        store._converge_nullable_publication_time(connection)
        assert queries == ["PRAGMA table_info(canonical_observations)"]


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


def test_canonical_query_filters_are_inclusive_and_hash_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "canonical-filters.sqlite")
    second = _observation(
        event_day=2, knowledge_day=3, value="500", revision="second"
    )
    third = _observation(
        event_day=3, knowledge_day=4, value="510", revision="third"
    )
    fourth = _observation(
        event_day=4, knowledge_day=5, value="520", revision="fourth"
    )
    other_source = replace(
        fourth,
        source="other-source",
        revision_id="other-source",
        evidence_hash=evidence_sha256("other source"),
    )
    store.save_observations([second, third, fourth, other_source])
    cutoff = datetime(2026, 1, 6, tzinfo=UTC)

    filtered = store.load_observations_as_of(
        "US-USD",
        cutoff,
        event_time_from=datetime(2026, 1, 3, tzinfo=UTC),
        instrument_ids=(third.instrument_id,),
        sources=(third.source,),
    )
    hashes = store.latest_observation_hashes(
        "US-USD",
        cutoff,
        event_time_from=datetime(2026, 1, 3, tzinfo=UTC),
        instrument_ids=(third.instrument_id,),
        sources=(third.source,),
    )

    assert filtered == [third, fourth]
    assert hashes == {
        (third.instrument_id, third.event_time): third.evidence_hash,
        (fourth.instrument_id, fourth.event_time): fourth.evidence_hash,
    }
    assert store.load_observations_as_of(
        "US-USD", cutoff, instrument_ids=()
    ) == []


def test_sql_page_limits_visible_latest_vintages(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bounded-page.sqlite")
    observations = [
        _observation(
            event_day=day,
            knowledge_day=day,
            value=str(500 + day),
            revision=f"day-{day}",
        )
        for day in (2, 3, 4)
    ]
    store.save_observations(observations)
    traced: list[str] = []
    original_conn = store._conn
    with original_conn() as connection:
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list('canonical_observations')"
            ).fetchall()
        }
    assert "canonical_observations_series_page" in indexes

    def traced_conn():
        connection = original_conn()
        connection.set_trace_callback(traced.append)
        return connection

    monkeypatch.setattr(store, "_conn", traced_conn)
    page, cursor = store.load_observation_page(
        "US-USD",
        datetime(2026, 1, 6, tzinfo=UTC),
        limit=1,
        instrument_ids=(observations[0].instrument_id,),
        redistribution_statuses=(RedistributionStatus.ALLOWED,),
    )

    query = next(statement for statement in traced if "WITH ranked AS" in statement)
    compact = " ".join(query.split())
    assert "ROW_NUMBER() OVER" in compact
    assert "WHERE vintage_rank=1 AND redistribution_status IN ('allowed')" in compact
    assert "LIMIT 2" in compact
    assert compact.index("WHERE vintage_rank=1") < compact.index("LIMIT 2")
    assert page == [observations[-1]]
    assert cursor == (observations[-1].event_time, observations[-1].instrument_id)


def test_page_scans_past_prohibited_keys_without_resurrecting_old_revision(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "visible-page.sqlite")
    older_allowed = [
        _observation(
            event_day=day,
            knowledge_day=day,
            value=str(500 + day),
            revision=f"allowed-{day}",
        )
        for day in (2, 3)
    ]
    newest_prohibited = [
        replace(
            _observation(
                event_day=day,
                knowledge_day=day,
                value=str(500 + day),
                revision=f"prohibited-{day}",
            ),
            redistribution_status=RedistributionStatus.PROHIBITED,
        )
        for day in (4, 5)
    ]
    sixth_allowed = _observation(
        event_day=6,
        knowledge_day=6,
        value="506",
        revision="allowed-6",
    )
    sixth_prohibited = replace(
        sixth_allowed,
        value="606",
        source_publication_time=datetime(2026, 1, 7, 8, tzinfo=UTC),
        knowledge_time=datetime(2026, 1, 7, 9, tzinfo=UTC),
        revision_id="prohibited-6",
        evidence_hash=evidence_sha256("newest prohibited revision"),
        redistribution_status=RedistributionStatus.PROHIBITED,
    )
    store.save_observations(
        [*older_allowed, *newest_prohibited, sixth_allowed, sixth_prohibited]
    )

    page, cursor = store.load_observation_page(
        "US-USD",
        datetime(2026, 1, 10, tzinfo=UTC),
        limit=2,
        redistribution_statuses=(RedistributionStatus.ALLOWED,),
    )

    assert page == list(reversed(older_allowed))
    assert cursor is None
    assert sixth_allowed not in page


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


def test_release_handoff_activation_is_atomic_and_retains_staging(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "staged-snapshots.sqlite")
    prior_sha = "a" * 40
    prior_snapshot_id = store.seal_market_snapshot(
        market_id="US-USD",
        product="prior-overview",
        event_cutoff="2026-01-01T00:00:00+00:00",
        knowledge_cutoff="2026-01-02T00:00:00+00:00",
        calibration_id="test-staging-v1",
        evidence_eligible=True,
        payload={"value": 5, "evidence_eligibility": {"eligible": True}},
        promoted=False,
    )
    prior_forward_id = store.append_forward_record(
        snapshot_id=prior_snapshot_id,
        market_id="US-USD",
        product="prior-overview",
        event_cutoff="2026-01-01T00:00:00+00:00",
        knowledge_cutoff="2026-01-02T00:00:00+00:00",
        calibration_id="test-staging-v1",
        payload={"value": 5, "evidence_eligibility": {"eligible": True}},
    )
    prior_snapshot_row_hash = market_snapshot_row_hash(
        store.load_staged_market_snapshot(prior_snapshot_id) or {}
    )
    prior_envelope = _release_handoff(
        prior_sha,
        {
            "products": {
                "prior-overview": {
                    "snapshot_id": prior_snapshot_id,
                    "forward_record_id": prior_forward_id,
                    "snapshot_row_sha256": prior_snapshot_row_hash,
                }
            }
        },
        {"value": 5},
    )
    prior_handoff_id = prior_envelope["handoff_id"]
    store.stage_release_handoff(prior_handoff_id, prior_sha, prior_envelope)
    store.activate_release_handoff(
        prior_handoff_id,
        prior_sha,
        (
            (
                "prior-overview",
                prior_snapshot_id,
                prior_forward_id,
                prior_snapshot_row_hash,
            ),
        ),
    )
    assert store.load_active_release_handoff() == prior_envelope

    staged_ids = tuple(
        store.seal_market_snapshot(
            market_id="US-USD",
            product=product,
            event_cutoff="2026-01-02T00:00:00+00:00",
            knowledge_cutoff="2026-01-03T00:00:00+00:00",
            calibration_id="test-staging-v1",
            evidence_eligible=True,
            payload={
                "value": value,
                "evidence_eligibility": {"eligible": True},
            },
            promoted=False,
        )
        for product, value in (("overview", 10), ("gauge", 20))
    )
    staged_forward_ids = tuple(
        store.append_forward_record(
            snapshot_id=snapshot_id,
            market_id="US-USD",
            product=product,
            event_cutoff="2026-01-02T00:00:00+00:00",
            knowledge_cutoff="2026-01-03T00:00:00+00:00",
            calibration_id="test-staging-v1",
            payload={
                "value": value,
                "evidence_eligibility": {"eligible": True},
            },
        )
        for product, value, snapshot_id in zip(
            ("overview", "gauge"), (10, 20), staged_ids, strict=True
        )
    )
    staged_row_hashes = tuple(
        market_snapshot_row_hash(store.load_staged_market_snapshot(snapshot_id) or {})
        for snapshot_id in staged_ids
    )
    bindings = tuple(
        zip(
            ("overview", "gauge"),
            staged_ids,
            staged_forward_ids,
            staged_row_hashes,
            strict=True,
        )
    )
    producer_sha = "b" * 40
    envelope = _release_handoff(
        producer_sha,
        {
            "generated_at": "2026-01-03T00:01:00+00:00",
            "products": {
                product: {
                    "snapshot_id": snapshot_id,
                    "forward_record_id": forward_record_id,
                    "snapshot_row_sha256": snapshot_row_hash,
                }
                for product, snapshot_id, forward_record_id, snapshot_row_hash in zip(
                    ("overview", "gauge"),
                    staged_ids,
                    staged_forward_ids,
                    staged_row_hashes,
                    strict=True,
                )
            },
        },
        {"generated_at": "2026-01-03T00:01:00+00:00", "value": 20},
    )
    handoff_id = envelope["handoff_id"]
    assert store.load_release_handoff(handoff_id) is None
    store.stage_release_handoff(handoff_id, producer_sha, envelope)
    store.stage_release_handoff(handoff_id, producer_sha, envelope)
    assert store.load_release_handoff(handoff_id) == envelope
    with pytest.raises(ValueError, match="different producer or envelope"):
        store.stage_release_handoff(handoff_id, "c" * 40, envelope)
    with pytest.raises(ValueError, match="different producer or envelope"):
        store.stage_release_handoff(
            handoff_id,
            producer_sha,
            {**envelope, "payload": {"value": "changed"}},
        )

    assert store.load_latest_market_snapshot("US-USD", "overview") is None
    assert store.load_latest_market_snapshot("US-USD", "gauge") is None

    with pytest.raises(ValueError, match="locked handoff"):
        store.activate_release_handoff(
            handoff_id,
            producer_sha,
            (bindings[0], ("gauge", "f" * 64, "e" * 64, "d" * 64)),
        )
    assert store.load_latest_market_snapshot("US-USD", "overview") is None
    assert store.load_latest_market_snapshot("US-USD", "gauge") is None
    assert store.load_active_release_handoff() == prior_envelope

    staging_columns = (
        "snapshot_id,market_id,product,event_cutoff,knowledge_cutoff,"
        "sealed_at,calibration_id,evidence_eligible,payload_hash,payload"
    )
    with sqlite3.connect(store.DB_PATH) as connection:
        deleted_staging = connection.execute(
            f"SELECT {staging_columns} FROM market_snapshot_staging "
            "WHERE snapshot_id=?",
            (staged_ids[1],),
        ).fetchone()
        connection.execute(
            "DELETE FROM market_snapshot_staging WHERE snapshot_id=?",
            (staged_ids[1],),
        )
    with pytest.raises(ValueError, match="missing market snapshot"):
        store.activate_release_handoff(handoff_id, producer_sha, bindings)
    with sqlite3.connect(store.DB_PATH) as connection:
        connection.execute(
            f"INSERT INTO market_snapshot_staging ({staging_columns}) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            deleted_staging,
        )
    assert (
        market_snapshot_row_hash(store.load_staged_market_snapshot(staged_ids[1]) or {})
        == staged_row_hashes[1]
    )

    with sqlite3.connect(store.DB_PATH) as connection:
        original_market_id = connection.execute(
            "SELECT market_id FROM market_snapshot_staging WHERE snapshot_id=?",
            (staged_ids[0],),
        ).fetchone()[0]
        connection.execute(
            "UPDATE market_snapshot_staging SET market_id=? WHERE snapshot_id=?",
            (original_market_id.lower(), staged_ids[0]),
        )
    with pytest.raises(ValueError, match="market_id is not canonical uppercase"):
        store.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert store.load_active_release_handoff() == prior_envelope
    with sqlite3.connect(store.DB_PATH) as connection:
        connection.execute(
            "UPDATE market_snapshot_staging SET market_id=? WHERE snapshot_id=?",
            (original_market_id, staged_ids[0]),
        )

    # This is the same instant as the sealed UTC cutoff. SQLite preserves the
    # raw offset text, so activation must reject the representation change.
    with sqlite3.connect(store.DB_PATH) as connection:
        original_event_cutoff = connection.execute(
            "SELECT event_cutoff FROM market_snapshot_staging WHERE snapshot_id=?",
            (staged_ids[0],),
        ).fetchone()[0]
        connection.execute(
            "UPDATE market_snapshot_staging SET event_cutoff=? WHERE snapshot_id=?",
            ("2026-01-01T19:00:00-05:00", staged_ids[0]),
        )
    with pytest.raises(ValueError, match="event_cutoff is not canonical UTC"):
        store.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert store.load_active_release_handoff() == prior_envelope
    with sqlite3.connect(store.DB_PATH) as connection:
        connection.execute(
            "UPDATE market_snapshot_staging SET event_cutoff=? WHERE snapshot_id=?",
            (original_event_cutoff, staged_ids[0]),
        )

    with sqlite3.connect(store.DB_PATH) as connection:
        original_sealed_at = connection.execute(
            "SELECT sealed_at FROM market_snapshot_staging WHERE snapshot_id=?",
            (staged_ids[0],),
        ).fetchone()[0]
        connection.execute(
            "UPDATE market_snapshot_staging SET sealed_at=? WHERE snapshot_id=?",
            ("2000-01-01T00:00:00.000000+00:00", staged_ids[0]),
        )
    with pytest.raises(ValueError, match="row differs from the release receipt"):
        store.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert store.load_active_release_handoff() == prior_envelope
    with sqlite3.connect(store.DB_PATH) as connection:
        connection.execute(
            "UPDATE market_snapshot_staging SET sealed_at=? WHERE snapshot_id=?",
            (original_sealed_at, staged_ids[0]),
        )

    with sqlite3.connect(store.DB_PATH) as connection:
        original_staging = connection.execute(
            """SELECT payload_hash, payload FROM market_snapshot_staging
                WHERE snapshot_id=?""",
            (staged_ids[0],),
        ).fetchone()
        connection.execute(
            """UPDATE market_snapshot_staging
                  SET payload_hash=?, payload=? WHERE snapshot_id=?""",
            ("0" * 64, '{"tampered":true}', staged_ids[0]),
        )
    with pytest.raises(ValueError, match="payload"):
        store.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert store.load_latest_market_snapshot("US-USD", "overview") is None
    assert store.load_active_release_handoff() == prior_envelope
    with sqlite3.connect(store.DB_PATH) as connection:
        connection.execute(
            """UPDATE market_snapshot_staging
                  SET payload_hash=?, payload=? WHERE snapshot_id=?""",
            (*original_staging, staged_ids[0]),
        )

    with sqlite3.connect(store.DB_PATH) as connection:
        connection.execute(
            """UPDATE market_snapshot_staging SET evidence_eligible=0
                WHERE snapshot_id=?""",
            (staged_ids[0],),
        )
    with pytest.raises(ValueError, match="row differs from the release receipt"):
        store.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert store.load_active_release_handoff() == prior_envelope
    with sqlite3.connect(store.DB_PATH) as connection:
        connection.execute(
            """UPDATE market_snapshot_staging SET evidence_eligible=1
                WHERE snapshot_id=?""",
            (staged_ids[0],),
        )

    with sqlite3.connect(store.DB_PATH) as connection:
        connection.execute(
            f"""INSERT INTO market_snapshots ({staging_columns})
                 SELECT {staging_columns} FROM market_snapshot_staging
                  WHERE snapshot_id=?""",
            (staged_ids[0],),
        )
        connection.execute(
            """UPDATE market_snapshots SET payload_hash=?, payload=?
                WHERE snapshot_id=?""",
            ("0" * 64, '{"tampered":true}', staged_ids[0]),
        )
    with pytest.raises(RuntimeError, match="canonical rows differ"):
        store.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert store.load_active_release_handoff() == prior_envelope
    with sqlite3.connect(store.DB_PATH) as connection:
        connection.execute(
            "DELETE FROM market_snapshots WHERE snapshot_id=?",
            (staged_ids[0],),
        )

    # The trigger fails on the second deterministic insert. RAISE(FAIL) leaves
    # the first insert pending, so the connection transaction must roll it back.
    failing_snapshot_id = max(staged_ids)
    with sqlite3.connect(store.DB_PATH) as connection:
        connection.execute(
            f"""CREATE TRIGGER inject_release_snapshot_copy_failure
                BEFORE INSERT ON market_snapshots
                WHEN NEW.snapshot_id = '{failing_snapshot_id}'
                BEGIN
                  SELECT RAISE(FAIL, 'injected canonical insert failure');
                END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="canonical insert failure"):
        store.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert store.load_latest_market_snapshot("US-USD", "overview") is None
    assert store.load_latest_market_snapshot("US-USD", "gauge") is None
    assert store.load_active_release_handoff() == prior_envelope

    with sqlite3.connect(store.DB_PATH) as connection:
        connection.execute("DROP TRIGGER inject_release_snapshot_copy_failure")
    store.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert (
        store.load_latest_market_snapshot("US-USD", "overview")["snapshot_id"]
        == staged_ids[0]
    )
    assert (
        store.load_latest_market_snapshot("US-USD", "gauge")["snapshot_id"]
        == staged_ids[1]
    )
    assert store.load_active_release_handoff() == envelope

    # Re-activation depends on retained staging and remains deterministic.
    store.activate_release_handoff(handoff_id, producer_sha, bindings)
    newer_receipt = json.loads(json.dumps(envelope["release_receipt"]))
    newer_receipt["generated_at"] = "2026-01-03T00:02:00+00:00"
    newer_envelope = _release_handoff(
        producer_sha,
        newer_receipt,
        {"generated_at": "2026-01-03T00:02:00+00:00", "value": 21},
    )
    store.stage_release_handoff(
        newer_envelope["handoff_id"], producer_sha, newer_envelope
    )
    store.activate_release_handoff(
        newer_envelope["handoff_id"], producer_sha, bindings
    )
    assert store.load_active_release_handoff() == newer_envelope
    with pytest.raises(ValueError, match="cannot regress"):
        store.activate_release_handoff(handoff_id, producer_sha, bindings)
    assert store.load_active_release_handoff() == newer_envelope
    with sqlite3.connect(store.DB_PATH) as connection:
        retained = connection.execute(
            """SELECT snapshot_id FROM market_snapshot_staging
                WHERE snapshot_id IN (?,?) ORDER BY snapshot_id""",
            staged_ids,
        ).fetchall()
    assert tuple(row[0] for row in retained) == tuple(sorted(staged_ids))
