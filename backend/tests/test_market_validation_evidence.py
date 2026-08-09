from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from seiche.markets.base import ValidationCheck
from seiche.markets.validation_evidence import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ValidationEvidenceArtifact,
    ValidationEvidenceStore,
    ValidationStatus,
    input_fingerprint_for,
)


GENERATED = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def _artifact(
    *,
    generated_at: datetime = GENERATED,
    check: ValidationCheck = ValidationCheck.SCHEMA_AND_UNITS,
    status: ValidationStatus = ValidationStatus.PASS,
    metrics: dict[str, object] | None = None,
    reasons: tuple[str, ...] = (),
    evidence_references: tuple[str, ...] = ("capture:sha256:abc123",),
) -> ValidationEvidenceArtifact:
    return ValidationEvidenceArtifact.create(
        market_id="IN-INR",
        calibration_id="in-inr-local-forward-v1",
        check=check,
        status=status,
        runner_id="market-validation",
        runner_version="1.2.0",
        generated_at=generated_at,
        event_cutoff=datetime(2026, 8, 8, tzinfo=UTC),
        knowledge_cutoff=datetime(2026, 8, 9, 11, 0, tzinfo=UTC),
        input_fingerprint=input_fingerprint_for({"rows": [1, 2, 3]}),
        metrics={"rows": 3, "nested": {"maximum_error": 0.0}}
        if metrics is None
        else metrics,
        reasons=reasons,
        evidence_references=evidence_references,
    )


def test_artifact_is_deeply_immutable_and_content_addressed() -> None:
    first = _artifact(metrics={"z": 2, "a": {"values": [1, 2]}})
    second = _artifact(metrics={"a": {"values": [1, 2]}, "z": 2})

    assert first.artifact_id == second.artifact_id
    assert len(first.artifact_id) == 64
    assert first.to_json() == second.to_json()
    unsigned = first.to_dict()
    unsigned.pop("artifact_id")
    assert (
        first.artifact_id
        == hashlib.sha256(
            json.dumps(
                unsigned,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
    )
    assert first.to_json() == json.dumps(
        first.to_dict(),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    with pytest.raises(FrozenInstanceError):
        first.market_id = "US-USD"  # type: ignore[misc]
    with pytest.raises(TypeError):
        first.metrics["new"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        first.metrics["a"]["new"] = 1  # type: ignore[index]


def test_input_fingerprint_is_canonical_and_rejects_non_json_values() -> None:
    assert input_fingerprint_for({"b": [2, 3], "a": 1}) == input_fingerprint_for(
        {"a": 1, "b": [2, 3]}
    )
    with pytest.raises(ValueError, match="NaN"):
        input_fingerprint_for({"bad": float("nan")})
    with pytest.raises(TypeError, match="JSON-compatible"):
        input_fingerprint_for({"bad": {1, 2}})


def test_artifact_round_trip_verifies_id_and_rejects_schema_drift() -> None:
    artifact = _artifact()

    assert ValidationEvidenceArtifact.from_json(artifact.to_json()) == artifact
    changed = artifact.to_dict()
    changed["metrics"] = {"rows": 4}
    with pytest.raises(ArtifactIntegrityError, match="artifact_id"):
        ValidationEvidenceArtifact.from_dict(changed)

    unknown = artifact.to_dict()
    unknown["unexpected"] = True
    with pytest.raises(ValueError, match="unknown"):
        ValidationEvidenceArtifact.from_dict(unknown)


def test_artifact_rejects_duplicate_keys_and_noncanonical_timestamps() -> None:
    artifact = _artifact()
    duplicate = artifact.to_json().replace(
        '"schema":',
        '"schema":"seiche.market-validation-evidence.v1","schema":',
        1,
    )
    with pytest.raises(ArtifactIntegrityError, match="duplicate JSON key"):
        ValidationEvidenceArtifact.from_json(duplicate)

    record = artifact.to_dict()
    record["generated_at"] = "2026-08-09T12:00:00+00:00"
    with pytest.raises(ValueError, match="Z notation"):
        ValidationEvidenceArtifact.from_dict(record)


def test_artifact_semantics_fail_closed() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _artifact(generated_at=datetime(2026, 8, 9, 12, 0))
    with pytest.raises(ValueError, match="require at least one reason"):
        _artifact(status=ValidationStatus.FAIL)
    with pytest.raises(ValueError, match="evidence reference"):
        _artifact(evidence_references=())
    with pytest.raises(ValueError, match="event_cutoff"):
        ValidationEvidenceArtifact.create(
            market_id="IN-INR",
            calibration_id="in-inr-local-forward-v1",
            check=ValidationCheck.CALENDAR_AND_TIMEZONE,
            status=ValidationStatus.PENDING,
            runner_id="market-validation",
            runner_version="1.0.0",
            generated_at=GENERATED,
            event_cutoff=GENERATED,
            knowledge_cutoff=GENERATED - timedelta(seconds=1),
            input_fingerprint="0" * 64,
            reasons=("awaiting official calendar",),
        )


def test_store_append_is_atomic_idempotent_and_refuses_conflict(tmp_path) -> None:
    store = ValidationEvidenceStore(tmp_path / "evidence")
    artifact = _artifact()

    first_path = store.append(artifact)
    second_path = store.append(artifact)

    assert first_path == second_path
    assert list((tmp_path / "evidence").rglob("*.json")) == [first_path]
    assert store.load(artifact.artifact_id) == artifact

    conflicting = _artifact(metrics={"rows": 99})
    with pytest.raises(ArtifactConflictError, match="different content"):
        store.append(conflicting)
    assert first_path.read_text() == artifact.to_json()


def test_store_verified_load_refuses_tampering(tmp_path) -> None:
    store = ValidationEvidenceStore(tmp_path)
    artifact = _artifact()
    path = store.append(artifact)
    tampered = artifact.to_dict()
    tampered["metrics"] = {"rows": 999}
    path.write_text(json.dumps(tampered, sort_keys=True, separators=(",", ":")))

    with pytest.raises(ArtifactIntegrityError, match="artifact_id"):
        store.load(artifact.artifact_id)
    with pytest.raises(ArtifactConflictError, match="refusing to replace"):
        store.append(artifact)


def test_store_latest_lookup_is_per_check_and_verified(tmp_path) -> None:
    store = ValidationEvidenceStore(tmp_path)
    old_schema = _artifact(generated_at=GENERATED - timedelta(minutes=1))
    new_schema = _artifact(generated_at=GENERATED, metrics={"rows": 4})
    calendar = _artifact(
        generated_at=GENERATED + timedelta(minutes=1),
        check=ValidationCheck.CALENDAR_AND_TIMEZONE,
    )
    for artifact in (new_schema, calendar, old_schema):
        store.append(artifact)

    assert (
        store.latest_for_check(
            "IN-INR",
            "in-inr-local-forward-v1",
            ValidationCheck.SCHEMA_AND_UNITS,
        )
        == new_schema
    )
    latest = store.latest_per_check("IN-INR", "in-inr-local-forward-v1")
    assert latest == {
        ValidationCheck.SCHEMA_AND_UNITS: new_schema,
        ValidationCheck.CALENDAR_AND_TIMEZONE: calendar,
    }
    assert (
        store.latest_for_check(
            "US-USD",
            "us-usd-legacy-parity-v1",
            ValidationCheck.SCHEMA_AND_UNITS,
        )
        is None
    )
    with pytest.raises(ArtifactNotFoundError):
        store.load("f" * 64)
