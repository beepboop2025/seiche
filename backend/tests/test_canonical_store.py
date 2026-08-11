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
