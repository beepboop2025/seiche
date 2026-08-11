from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from seiche import store
from seiche.domain.forward_record import forward_record_hash
from seiche.markets.validation_forward import (
    GENESIS_HASH,
    verify_forward_chain,
    verify_repository_forward_chain,
)
from seiche.markets.validation import _forward_paper_record
from seiche.markets.new_zealand_nzd import PACK as NZ_PACK
from seiche.repository import SQLiteMarketRepository


def _repository(monkeypatch, tmp_path) -> SQLiteMarketRepository:
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "forward-validation.sqlite")
    return SQLiteMarketRepository()


def _append(
    repository: SQLiteMarketRepository,
    ordinal: int,
    *,
    market_id: str = "US-USD",
    product: str = "gauge",
) -> str:
    event = datetime(2026, 8, 1, tzinfo=UTC) + timedelta(days=ordinal)
    return repository.append_forward_record(
        snapshot_id=f"{market_id}-{product}-snapshot-{ordinal}",
        market_id=market_id,
        product=product,
        event_cutoff=event,
        knowledge_cutoff=event + timedelta(hours=12),
        calibration_id=f"{market_id.lower()}-forward-test-v1",
        payload={"ordinal": ordinal, "nested": {"market": market_id}},
    )


def test_sqlite_forward_records_round_trip_and_valid_chain(monkeypatch, tmp_path) -> None:
    repository = _repository(monkeypatch, tmp_path)
    _append(repository, 0)
    _append(repository, 9, product="overview")
    _append(repository, 1)
    _append(repository, 4, market_id="EA-EUR")
    _append(repository, 2)

    records = repository.load_forward_records("us-usd", "gauge")

    assert len(records) == 3
    assert list(records[0]) == [
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
    assert [record["payload"]["ordinal"] for record in records] == [0, 1, 2]
    assert [record["created_at"] for record in records] == sorted(
        record["created_at"] for record in records
    )
    assert len(repository.load_forward_records(product="overview")) == 1
    assert len(repository.load_forward_records(market_id="EA-EUR")) == 1

    result = verify_repository_forward_chain(
        repository,
        market_id="US-USD",
        product="gauge",
        minimum_records=3,
        minimum_span_days=2,
    )

    assert result["status"] == "PASS"
    assert result["reason_codes"] == []
    assert result["metrics"]["record_count"] == 3
    assert result["metrics"]["chain_count"] == 1
    assert result["metrics"]["minimum_chain_span_days"] == 2


def test_forward_chain_fails_when_payload_is_tampered(monkeypatch, tmp_path) -> None:
    repository = _repository(monkeypatch, tmp_path)
    _append(repository, 0)
    tampered_id = _append(repository, 1)

    with store._conn() as connection:
        connection.execute(
            "UPDATE forward_validation_records SET payload=? WHERE record_id=?",
            (json.dumps({"ordinal": 999}), tampered_id),
        )

    result = verify_repository_forward_chain(
        repository,
        market_id="US-USD",
        product="gauge",
        minimum_records=2,
        minimum_span_days=1,
    )

    assert result["status"] == "FAIL"
    assert "PAYLOAD_HASH_MISMATCH" in result["reason_codes"]
    assert "RECORD_HASH_MISMATCH" in result["reason_codes"]
    assert result["metrics"]["payload_hash_mismatches"] == 1


def test_forward_chain_fails_when_an_internal_link_is_missing(monkeypatch, tmp_path) -> None:
    repository = _repository(monkeypatch, tmp_path)
    _append(repository, 0)
    missing_id = _append(repository, 1)
    _append(repository, 2)

    with store._conn() as connection:
        connection.execute(
            "DELETE FROM forward_validation_records WHERE record_id=?",
            (missing_id,),
        )

    result = verify_repository_forward_chain(
        repository,
        market_id="US-USD",
        product="gauge",
        minimum_records=1,
        minimum_span_days=0,
    )

    assert result["status"] == "FAIL"
    assert "MISSING_PREDECESSOR" in result["reason_codes"]
    assert "ORPHANED_FORWARD_RECORD" in result["reason_codes"]
    assert result["metrics"]["link_mismatches"] == 1


def test_empty_forward_chain_is_pending(monkeypatch, tmp_path) -> None:
    repository = _repository(monkeypatch, tmp_path)

    result = verify_forward_chain(
        repository.load_forward_records("US-USD", "gauge"),
        minimum_records=10,
        minimum_span_days=30,
    )

    assert result["status"] == "PENDING"
    assert result["reason_codes"] == ["NO_FORWARD_RECORDS"]
    assert result["metrics"]["record_count"] == 0


def test_intact_but_insufficient_forward_history_is_pending(monkeypatch, tmp_path) -> None:
    repository = _repository(monkeypatch, tmp_path)
    _append(repository, 0)
    _append(repository, 1)

    result = verify_repository_forward_chain(
        repository,
        market_id="US-USD",
        product="gauge",
        minimum_records=3,
        minimum_span_days=5,
    )

    assert result["status"] == "PENDING"
    assert result["reason_codes"] == [
        "INSUFFICIENT_FORWARD_RECORDS",
        "INSUFFICIENT_FORWARD_SPAN",
    ]
    assert result["metrics"]["minimum_chain_record_count"] == 2
    assert result["metrics"]["minimum_chain_span_days"] == 1


def test_forward_identity_rejects_reserved_delimiters(monkeypatch, tmp_path) -> None:
    repository = _repository(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="reserved delimiter"):
        _append(repository, 0, product="gauge|forged")

    _append(repository, 0)
    forged = repository.load_forward_records("US-USD", "gauge")[0] | {
        "product": "gauge|forged"
    }
    result = verify_forward_chain(
        (forged,),
        minimum_records=1,
        minimum_span_days=0,
    )
    assert result["status"] == "FAIL"
    assert "UNSAFE_FORWARD_IDENTITY_FIELD" in result["reason_codes"]
    assert result["metrics"]["malformed_record_defects"] == 1


def test_safe_forward_identity_preserves_the_deployed_v1_hash() -> None:
    fields = (
        "a" * 64,
        "US-USD",
        "gauge",
        "2026-08-09T00:00:00+00:00",
        "2026-08-09T12:00:00+00:00",
        "us-usd-forward-v1",
        "b" * 64,
        "0" * 64,
    )
    legacy = hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()
    assert forward_record_hash(
        snapshot_id=fields[0],
        market_id=fields[1],
        product=fields[2],
        event_cutoff=fields[3],
        knowledge_cutoff=fields[4],
        calibration_id=fields[5],
        payload_hash=fields[6],
        previous_record_hash=fields[7],
    ) == legacy


def _synthetic_record(
    name: str,
    previous_record_hash: str,
    *,
    calibration_id: str = "nz-nzd-local-forward-v2",
    created_at: str = "2026-08-11T00:00:00+00:00",
    product: str = "gauge",
    market_id: str = "NZ-NZD",
) -> dict:
    payload = {"name": name}
    payload_json = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    snapshot_id = hashlib.sha256(f"snapshot:{name}".encode()).hexdigest()
    event_cutoff = "2026-08-10T00:00:00+00:00"
    knowledge_cutoff = "2026-08-11T00:00:00+00:00"
    record_hash = forward_record_hash(
        snapshot_id=snapshot_id,
        market_id=market_id,
        product=product,
        event_cutoff=event_cutoff,
        knowledge_cutoff=knowledge_cutoff,
        calibration_id=calibration_id,
        payload_hash=payload_hash,
        previous_record_hash=previous_record_hash,
    )
    return {
        "record_id": record_hash,
        "snapshot_id": snapshot_id,
        "market_id": market_id,
        "product": product,
        "event_cutoff": event_cutoff,
        "knowledge_cutoff": knowledge_cutoff,
        "calibration_id": calibration_id,
        "chain_generation": int(calibration_id.rsplit("-v", 1)[1]),
        "created_at": created_at,
        "payload_hash": payload_hash,
        "previous_record_hash": previous_record_hash,
        "record_hash": record_hash,
        "payload": payload,
    }


def test_topology_not_hash_sorting_orders_same_timestamp_links() -> None:
    root = _synthetic_record("same-time-root", GENESIS_HASH)
    # Find a valid child whose hash sorts before its parent. A timestamp+hash
    # sort would reject this chain even though its authenticated edge is valid.
    for ordinal in range(10_000):
        child = _synthetic_record(f"same-time-child-{ordinal}", root["record_hash"])
        if child["record_hash"] < root["record_hash"]:
            break
    else:  # pragma: no cover - SHA-256 ordering makes this astronomically unlikely
        raise AssertionError("could not construct reverse-sorting child")

    result = verify_forward_chain(
        sorted((root, child), key=lambda row: row["record_hash"]),
        minimum_records=2,
        minimum_span_days=0,
    )

    assert result["status"] == "PASS"
    assert result["metrics"]["chains"][0]["head_record_hash"] == child["record_hash"]


def test_topology_reports_fork_and_multiple_heads() -> None:
    root = _synthetic_record("fork-root", GENESIS_HASH)
    left = _synthetic_record("fork-left", root["record_hash"])
    right = _synthetic_record("fork-right", root["record_hash"])

    result = verify_forward_chain(
        (right, root, left), minimum_records=0, minimum_span_days=0
    )

    assert result["status"] == "FAIL"
    assert {"CHAIN_FORK", "MULTIPLE_CHAIN_HEADS"} <= set(result["reason_codes"])
    assert result["metrics"]["fork_defects"] == 1
    assert result["metrics"]["chains"][0]["head_count"] == 2


def test_topology_reports_orphan_and_missing_predecessor() -> None:
    orphan = _synthetic_record("orphan", "f" * 64)

    result = verify_forward_chain((orphan,), minimum_records=0, minimum_span_days=0)

    assert result["status"] == "FAIL"
    assert {
        "MISSING_CHAIN_ROOT",
        "MISSING_PREDECESSOR",
        "ORPHANED_FORWARD_RECORD",
    } <= set(result["reason_codes"])


def test_topology_reports_cycle_even_when_identity_hashes_are_also_invalid() -> None:
    first = _synthetic_record("cycle-first", GENESIS_HASH)
    second = _synthetic_record("cycle-second", first["record_hash"])
    first = first | {"previous_record_hash": second["record_hash"]}

    result = verify_forward_chain(
        (first, second), minimum_records=0, minimum_span_days=0
    )

    assert result["status"] == "FAIL"
    assert {"CHAIN_CYCLE", "MISSING_CHAIN_ROOT", "MISSING_CHAIN_HEAD"} <= set(
        result["reason_codes"]
    )


def test_topology_reports_multiple_roots() -> None:
    first = _synthetic_record("root-one", GENESIS_HASH)
    second = _synthetic_record("root-two", GENESIS_HASH)

    result = verify_forward_chain(
        (first, second), minimum_records=0, minimum_span_days=0
    )

    assert result["status"] == "FAIL"
    assert {"MULTIPLE_CHAIN_ROOTS", "MULTIPLE_CHAIN_HEADS"} <= set(
        result["reason_codes"]
    )


def test_concurrent_v2_append_keeps_one_linear_chain(monkeypatch, tmp_path) -> None:
    repository = _repository(monkeypatch, tmp_path)
    event = datetime(2026, 8, 11, tzinfo=UTC)

    def append(ordinal: int) -> str:
        return repository.append_forward_record(
            snapshot_id=f"nz-v2-concurrent-{ordinal}",
            market_id="NZ-NZD",
            product="gauge",
            event_cutoff=event,
            knowledge_cutoff=event,
            calibration_id="nz-nzd-local-forward-v2",
            payload={"ordinal": ordinal},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        record_ids = list(pool.map(append, range(16)))

    records = repository.load_forward_records(
        "NZ-NZD", "gauge", "nz-nzd-local-forward-v2"
    )
    result = verify_forward_chain(records, minimum_records=16, minimum_span_days=0)
    assert len(set(record_ids)) == 16
    assert result["status"] == "PASS"
    assert result["metrics"]["chains"][0]["root_count"] == 1
    assert result["metrics"]["chains"][0]["head_count"] == 1


def test_idempotent_retry_verifies_chain_and_request_identity(
    monkeypatch, tmp_path
) -> None:
    repository = _repository(monkeypatch, tmp_path)
    event = "2026-08-11T00:00:00+00:00"
    record_id = repository.append_forward_record(
        snapshot_id="v2-idempotent-snapshot",
        market_id="NZ-NZD",
        product="gauge",
        event_cutoff=event,
        knowledge_cutoff=event,
        calibration_id="nz-nzd-local-forward-v2",
        payload={"value": 1},
    )
    assert (
        repository.append_forward_record(
            snapshot_id="v2-idempotent-snapshot",
            market_id="NZ-NZD",
            product="gauge",
            event_cutoff=event,
            knowledge_cutoff=event,
            calibration_id="nz-nzd-local-forward-v2",
            payload={"value": 1},
        )
        == record_id
    )
    with pytest.raises(ValueError, match="does not match its stored identity"):
        repository.append_forward_record(
            snapshot_id="v2-idempotent-snapshot",
            market_id="NZ-NZD",
            product="gauge",
            event_cutoff=event,
            knowledge_cutoff=event,
            calibration_id="nz-nzd-local-forward-v2",
            payload={"value": 2},
        )


def test_promotion_gate_requires_both_active_product_chains(
    monkeypatch, tmp_path
) -> None:
    repository = _repository(monkeypatch, tmp_path)
    event = "2026-08-11T00:00:00+00:00"
    repository.append_forward_record(
        snapshot_id="v2-gauge-only",
        market_id="NZ-NZD",
        product="gauge",
        event_cutoff=event,
        knowledge_cutoff=event,
        calibration_id=NZ_PACK.calibration_id,
        payload={"product": "gauge"},
    )

    assessment = _forward_paper_record(
        NZ_PACK, repository, minimum_records=1, minimum_span_days=0
    )

    assert assessment.metrics["chain_integrity_status"] == "PENDING"
    assert assessment.metrics["missing_product_chains"] == ["overview"]
    assert "MISSING_FORWARD_PRODUCT_CHAIN" in assessment.reasons


def _insert_record(connection: sqlite3.Connection, record: dict) -> None:
    connection.execute(
        """INSERT INTO forward_validation_records
             (record_id, snapshot_id, market_id, product, event_cutoff,
              knowledge_cutoff, calibration_id, chain_generation, created_at,
              payload_hash, previous_record_hash, record_hash, payload)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            record["record_id"],
            record["snapshot_id"],
            record["market_id"],
            record["product"],
            record["event_cutoff"],
            record["knowledge_cutoff"],
            record["calibration_id"],
            record["chain_generation"],
            record["created_at"],
            record["payload_hash"],
            record["previous_record_hash"],
            record["record_hash"],
            json.dumps(record["payload"], sort_keys=True, separators=(",", ":")),
        ),
    )


def test_v1_incident_is_preserved_and_v2_restarts_at_genesis(
    monkeypatch, tmp_path
) -> None:
    repository = _repository(monkeypatch, tmp_path)
    v1_root = _synthetic_record(
        "v1-root", GENESIS_HASH, calibration_id="nz-nzd-local-forward-v1"
    )
    v1_left = _synthetic_record(
        "v1-left",
        v1_root["record_hash"],
        calibration_id="nz-nzd-local-forward-v1",
    )
    v1_right = _synthetic_record(
        "v1-right",
        v1_root["record_hash"],
        calibration_id="nz-nzd-local-forward-v1",
    )
    v1_overview_root = _synthetic_record(
        "v1-overview-root",
        GENESIS_HASH,
        calibration_id="nz-nzd-local-forward-v1",
        product="overview",
    )
    v1_overview_children = [
        _synthetic_record(
            f"v1-overview-{branch}",
            v1_overview_root["record_hash"],
            calibration_id="nz-nzd-local-forward-v1",
            product="overview",
        )
        for branch in ("left", "middle", "right")
    ]
    with store._conn() as connection:
        for record in (
            v1_root,
            v1_left,
            v1_right,
            v1_overview_root,
            *v1_overview_children,
        ):
            _insert_record(connection, record)
        before = connection.execute(
            """SELECT record_id, snapshot_id, previous_record_hash, record_hash,
                      payload FROM forward_validation_records
                 WHERE calibration_id='nz-nzd-local-forward-v1'
                 ORDER BY record_id"""
        ).fetchall()

    with pytest.raises(ValueError, match="no single valid head"):
        repository.append_forward_record(
            snapshot_id="must-not-extend-v1-fork",
            market_id="NZ-NZD",
            product="gauge",
            event_cutoff="2026-08-11T00:00:00+00:00",
            knowledge_cutoff="2026-08-11T00:00:00+00:00",
            calibration_id="nz-nzd-local-forward-v1",
            payload={"must": "fail closed"},
        )
    with pytest.raises(ValueError, match="no single valid head"):
        repository.append_forward_record(
            snapshot_id=v1_root["snapshot_id"],
            market_id="NZ-NZD",
            product="gauge",
            event_cutoff=v1_root["event_cutoff"],
            knowledge_cutoff=v1_root["knowledge_cutoff"],
            calibration_id="nz-nzd-local-forward-v1",
            payload=v1_root["payload"],
        )

    v2_id = repository.append_forward_record(
        snapshot_id="honest-v2-genesis",
        market_id="NZ-NZD",
        product="gauge",
        event_cutoff="2026-08-11T00:00:00+00:00",
        knowledge_cutoff="2026-08-11T00:00:00+00:00",
        calibration_id="nz-nzd-local-forward-v2",
        payload={"generation": 2},
    )
    repository.append_forward_record(
        snapshot_id="honest-v2-overview-genesis",
        market_id="NZ-NZD",
        product="overview",
        event_cutoff="2026-08-11T00:00:00+00:00",
        knowledge_cutoff="2026-08-11T00:00:00+00:00",
        calibration_id="nz-nzd-local-forward-v2",
        payload={"generation": 2, "product": "overview"},
    )
    with store._conn() as connection:
        after = connection.execute(
            """SELECT record_id, snapshot_id, previous_record_hash, record_hash,
                      payload FROM forward_validation_records
                 WHERE calibration_id='nz-nzd-local-forward-v1'
                 ORDER BY record_id"""
        ).fetchall()
    assert after == before
    v2 = repository.load_forward_records("NZ-NZD", "gauge", "nz-nzd-local-forward-v2")
    assert v2 == [v2[0]]
    assert v2[0]["record_id"] == v2_id
    assert v2[0]["previous_record_hash"] == GENESIS_HASH
    assert v2[0]["chain_generation"] == 2

    active = verify_repository_forward_chain(
        repository,
        market_id="NZ-NZD",
        product="gauge",
        calibration_id="nz-nzd-local-forward-v2",
        minimum_records=1,
        minimum_span_days=0,
    )
    assert active["status"] == "PASS"
    assert active["metrics"]["historical_quarantine_status"] == (
        "INCIDENT_EVIDENCE_QUARANTINED"
    )
    assert active["metrics"]["quarantined_generations"] == [
        {
            "market_id": "NZ-NZD",
            "calibration_id": "nz-nzd-local-forward-v1",
            "chain_generation": 1,
            "product": "gauge",
            "record_count": 3,
            "integrity_status": "FAIL",
            "reason_codes": ["CHAIN_FORK", "MULTIPLE_CHAIN_HEADS"],
            "topology": {
                "root_count": 1,
                "head_count": 2,
                "fork_count": 1,
                "fork_parent_count": 1,
                "orphan_count": 0,
                "cycle_count": 0,
                "missing_predecessor_count": 0,
            },
            "disposition": "QUARANTINED_INTEGRITY_INCIDENT",
        }
    ]

    assessment = _forward_paper_record(
        NZ_PACK, repository, minimum_records=1, minimum_span_days=0
    )
    assert assessment.metrics["chain_integrity_status"] == "PASS"
    assert assessment.metrics["active_calibration_id"] == NZ_PACK.calibration_id
    assert assessment.metrics["historical_quarantine_status"] == (
        "INCIDENT_EVIDENCE_QUARANTINED"
    )
    assert assessment.metrics["chain_count"] == 2
    quarantine_by_product = {
        row["product"]: row for row in assessment.metrics["quarantined_generations"]
    }
    assert quarantine_by_product["gauge"]["reason_codes"] == [
        "CHAIN_FORK",
        "MULTIPLE_CHAIN_HEADS",
    ]
    assert quarantine_by_product["overview"]["record_count"] == 4
    assert quarantine_by_product["overview"]["topology"] == {
        "root_count": 1,
        "head_count": 3,
        "fork_count": 2,
        "fork_parent_count": 1,
        "orphan_count": 0,
        "cycle_count": 0,
        "missing_predecessor_count": 0,
    }
    assert quarantine_by_product["overview"]["reason_codes"] == [
        "CHAIN_FORK",
        "MULTIPLE_CHAIN_HEADS",
    ]


def test_historical_audit_keeps_shared_calibrations_separate_by_market() -> None:
    class SharedCalibrationReader:
        def load_forward_records(
            self,
            market_id: str | None = None,
            product: str | None = None,
            calibration_id: str | None = None,
        ) -> list[dict]:
            del market_id, product, calibration_id
            return [
                _synthetic_record(
                    "shared-us-root",
                    GENESIS_HASH,
                    calibration_id="shared-local-forward-v1",
                    market_id="US-USD",
                ),
                _synthetic_record(
                    "shared-ea-root",
                    GENESIS_HASH,
                    calibration_id="shared-local-forward-v1",
                    market_id="EA-EUR",
                ),
            ]

    result = verify_repository_forward_chain(
        SharedCalibrationReader(),
        calibration_id="active-local-forward-v2",
        minimum_records=0,
        minimum_span_days=0,
    )

    quarantined = result["metrics"]["quarantined_generations"]
    assert result["metrics"]["historical_generation_count"] == 2
    assert [row["market_id"] for row in quarantined] == ["EA-EUR", "US-USD"]
    assert all(row["integrity_status"] == "PASS" for row in quarantined)
    assert all(row["topology"]["root_count"] == 1 for row in quarantined)
    assert all(row["topology"]["head_count"] == 1 for row in quarantined)


def test_invalid_active_calibration_still_returns_an_audit_report() -> None:
    class EmptyReader:
        def load_forward_records(
            self,
            market_id: str | None = None,
            product: str | None = None,
            calibration_id: str | None = None,
        ) -> list[dict]:
            del market_id, product, calibration_id
            return []

    result = verify_repository_forward_chain(
        EmptyReader(),
        calibration_id="invalid-calibration",
        minimum_records=1,
        minimum_span_days=1,
    )

    assert result["status"] == "PENDING"
    assert result["metrics"]["active_calibration_id"] == "invalid-calibration"
    assert result["metrics"]["active_chain_generation"] is None


def test_v2_database_invariant_rejects_duplicate_child(monkeypatch, tmp_path) -> None:
    repository = _repository(monkeypatch, tmp_path)
    root_id = repository.append_forward_record(
        snapshot_id="v2-unique-root",
        market_id="NZ-NZD",
        product="gauge",
        event_cutoff="2026-08-11T00:00:00+00:00",
        knowledge_cutoff="2026-08-11T00:00:00+00:00",
        calibration_id="nz-nzd-local-forward-v2",
        payload={"root": True},
    )
    first = _synthetic_record("direct-first-child", root_id)
    second = _synthetic_record("direct-second-child", root_id)
    with store._conn() as connection:
        _insert_record(connection, first)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            _insert_record(connection, second)


def test_database_invariant_still_protects_other_active_v1_chains(
    monkeypatch, tmp_path
) -> None:
    repository = _repository(monkeypatch, tmp_path)
    root_id = repository.append_forward_record(
        snapshot_id="ea-v1-unique-root",
        market_id="EA-EUR",
        product="gauge",
        event_cutoff="2026-08-11T00:00:00+00:00",
        knowledge_cutoff="2026-08-11T00:00:00+00:00",
        calibration_id="ea-eur-local-forward-v1",
        payload={"root": True},
    )
    first = _synthetic_record(
        "ea-direct-first-child",
        root_id,
        calibration_id="ea-eur-local-forward-v1",
        market_id="EA-EUR",
    )
    second = _synthetic_record(
        "ea-direct-second-child",
        root_id,
        calibration_id="ea-eur-local-forward-v1",
        market_id="EA-EUR",
    )
    with store._conn() as connection:
        _insert_record(connection, first)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            _insert_record(connection, second)


def test_additive_sqlite_migration_preserves_legacy_v1_fork(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    database = tmp_path / "legacy-forward.sqlite"
    monkeypatch.setattr(store, "DB_PATH", database)
    legacy = sqlite3.connect(database)
    legacy.execute(
        """CREATE TABLE forward_validation_records (
             record_id TEXT PRIMARY KEY,
             snapshot_id TEXT NOT NULL UNIQUE,
             market_id TEXT NOT NULL,
             product TEXT NOT NULL,
             event_cutoff TEXT NOT NULL,
             knowledge_cutoff TEXT NOT NULL,
             calibration_id TEXT NOT NULL,
             created_at TEXT NOT NULL,
             payload_hash TEXT NOT NULL,
             previous_record_hash TEXT NOT NULL,
             record_hash TEXT NOT NULL UNIQUE,
             payload TEXT NOT NULL)"""
    )
    root = _synthetic_record(
        "legacy-root", GENESIS_HASH, calibration_id="nz-nzd-local-forward-v1"
    )
    children = [
        _synthetic_record(
            name,
            root["record_hash"],
            calibration_id="nz-nzd-local-forward-v1",
        )
        for name in ("legacy-left", "legacy-right")
    ]
    legacy_columns = (
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
    )
    for record in (root, *children):
        legacy.execute(
            f"""INSERT INTO forward_validation_records
                 ({",".join(legacy_columns)}) VALUES ({",".join("?" for _ in legacy_columns)})""",
            tuple(
                json.dumps(record[key], sort_keys=True, separators=(",", ":"))
                if key == "payload"
                else record[key]
                for key in legacy_columns
            ),
        )
    before = legacy.execute(
        f"""SELECT {",".join(legacy_columns)} FROM forward_validation_records
             ORDER BY record_id"""
    ).fetchall()
    legacy.commit()
    legacy.close()

    repository = SQLiteMarketRepository()
    loaded = repository.load_forward_records(
        "NZ-NZD", "gauge", "nz-nzd-local-forward-v1"
    )

    assert len(loaded) == 3
    assert {row["chain_generation"] for row in loaded} == {1}
    migrated = sqlite3.connect(database)
    after = migrated.execute(
        f"""SELECT {",".join(legacy_columns)} FROM forward_validation_records
             ORDER BY record_id"""
    ).fetchall()
    indexes = {
        row[1]
        for row in migrated.execute("PRAGMA index_list(forward_validation_records)")
    }
    migrated.close()
    assert after == before
    assert "forward_records_one_child" in indexes


def test_sqlite_legacy_fork_blocks_forward_appends_but_preserves_reads(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    database = tmp_path / "unexpected-legacy-fork.sqlite"
    monkeypatch.setattr(store, "DB_PATH", database)
    legacy = sqlite3.connect(database)
    legacy.execute(
        """CREATE TABLE forward_validation_records (
             record_id TEXT PRIMARY KEY,
             snapshot_id TEXT NOT NULL UNIQUE,
             market_id TEXT NOT NULL,
             product TEXT NOT NULL,
             event_cutoff TEXT NOT NULL,
             knowledge_cutoff TEXT NOT NULL,
             calibration_id TEXT NOT NULL,
             created_at TEXT NOT NULL,
             payload_hash TEXT NOT NULL,
             previous_record_hash TEXT NOT NULL,
             record_hash TEXT NOT NULL UNIQUE,
             payload TEXT NOT NULL)"""
    )
    root = _synthetic_record(
        "unexpected-root",
        GENESIS_HASH,
        calibration_id="ea-eur-local-forward-v1",
        market_id="EA-EUR",
    )
    children = [
        _synthetic_record(
            name,
            root["record_hash"],
            calibration_id="ea-eur-local-forward-v1",
            market_id="EA-EUR",
        )
        for name in ("unexpected-left", "unexpected-right")
    ]
    legacy_columns = (
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
    )
    for record in (root, *children):
        legacy.execute(
            f"""INSERT INTO forward_validation_records
                 ({",".join(legacy_columns)})
                 VALUES ({",".join("?" for _ in legacy_columns)})""",
            tuple(
                json.dumps(record[key], sort_keys=True, separators=(",", ":"))
                if key == "payload"
                else record[key]
                for key in legacy_columns
            ),
        )
    legacy.commit()
    legacy.close()

    repository = SQLiteMarketRepository()
    loaded = repository.load_forward_records(
        "EA-EUR", "gauge", "ea-eur-local-forward-v1"
    )

    assert len(loaded) == 3
    with store._conn() as connection:
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(forward_validation_records)"
            )
        }
        connection.execute(
            "INSERT INTO blobs (key, fetched_at, payload) VALUES (?,?,?)",
            ("still-readable", "2026-08-11T00:00:00+00:00", "{}"),
        )
    assert "forward_records_one_child" not in indexes
    with pytest.raises(RuntimeError, match="forward appends disabled"):
        repository.append_forward_record(
            snapshot_id="must-not-write-without-index",
            market_id="US-USD",
            product="gauge",
            event_cutoff="2026-08-11T00:00:00+00:00",
            knowledge_cutoff="2026-08-11T00:00:00+00:00",
            calibration_id="us-usd-local-forward-v1",
            payload={"blocked": True},
        )
    with store._conn() as connection:
        assert connection.execute(
            "SELECT payload FROM blobs WHERE key='still-readable'"
        ).fetchone() == ("{}",)
