from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from seiche import store
from seiche.domain.forward_record import forward_record_hash
from seiche.markets.validation_forward import (
    verify_forward_chain,
    verify_repository_forward_chain,
)
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
    assert result["reason_codes"] == ["CHAIN_LINK_MISMATCH"]
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
