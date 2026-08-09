"""Immutable, content-addressed evidence for market-pack validation checks.

The module intentionally uses only the Python standard library.  Validation
artifacts are canonical JSON records whose identity is the SHA-256 digest of
every field except ``artifact_id`` itself.  The filesystem store gives each
logical run (market, calibration, check, generated time) one append-only slot:
an exact retry is harmless, while different content for that slot is refused.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from seiche.markets.base import ValidationCheck


VALIDATION_EVIDENCE_SCHEMA = "seiche.market-validation-evidence.v1"

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MARKET_ID_RE = re.compile(r"[A-Z0-9]+-[A-Z]{3}")
_PATH_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_RUNNER_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+:-]{0,127}")


class ValidationStatus(StrEnum):
    """Outcome of a validation check, including work not yet completed."""

    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"


class ValidationEvidenceError(ValueError):
    """Base class for malformed or unverifiable validation evidence."""


class ArtifactIntegrityError(ValidationEvidenceError):
    """An artifact does not match its content identity or canonical record."""


class ArtifactConflictError(ValidationEvidenceError):
    """A different artifact already occupies an immutable store identity."""


class ArtifactNotFoundError(FileNotFoundError):
    """No artifact with the requested content identity exists in the store."""


def _freeze_json(value: object, *, field: str) -> object:
    """Validate and recursively freeze a JSON-compatible value."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} must not contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{field} object keys must be strings")
            if not key or key != key.strip():
                raise ValueError(f"{field} object keys must be non-blank and trimmed")
            frozen[key] = _freeze_json(item, field=f"{field}.{key}")
        return MappingProxyType(dict(sorted(frozen.items())))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(
            _freeze_json(item, field=f"{field}[{index}]")
            for index, item in enumerate(value)
        )
    raise TypeError(
        f"{field} contains unsupported value {type(value).__name__}; "
        "only JSON-compatible values are allowed"
    )


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _canonical_json_bytes(value: object) -> bytes:
    frozen = _freeze_json(value, field="payload")
    return json.dumps(
        _thaw_json(frozen),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def input_fingerprint_for(payload: object) -> str:
    """Return a deterministic SHA-256 fingerprint for JSON-compatible input."""

    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    timespec = "microseconds" if value.microsecond else "seconds"
    return value.isoformat(timespec=timespec).replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a canonical UTC timestamp string")
    if not value.endswith("Z"):
        raise ValueError(f"{field} must use canonical UTC Z notation")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid ISO-8601 timestamp") from exc
    parsed = _utc(parsed, field=field)
    if _timestamp(parsed) != value:
        raise ValueError(f"{field} is not in canonical UTC form")
    return parsed


def _require_string(value: object, *, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"invalid {field}: {value!r}")
    return value


def _text_tuple(value: Iterable[str], *, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{field} must be an iterable of strings, not one string")
    try:
        items = tuple(value)
    except TypeError as exc:
        raise TypeError(f"{field} must be an iterable of strings") from exc
    for item in items:
        if not isinstance(item, str):
            raise TypeError(f"{field} entries must be strings")
        if not item or item != item.strip():
            raise ValueError(f"{field} entries must be non-blank and trimmed")
    if len(items) != len(set(items)):
        raise ValueError(f"{field} entries must be unique")
    return items


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactIntegrityError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class ValidationEvidenceArtifact:
    """One immutable result for one market-pack validation check."""

    market_id: str
    calibration_id: str
    check: ValidationCheck
    status: ValidationStatus
    runner_id: str
    runner_version: str
    generated_at: datetime
    event_cutoff: datetime
    knowledge_cutoff: datetime
    input_fingerprint: str
    metrics: Mapping[str, object]
    reasons: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()
    schema: str = VALIDATION_EVIDENCE_SCHEMA
    artifact_id: str = ""

    def __post_init__(self) -> None:
        if self.schema != VALIDATION_EVIDENCE_SCHEMA:
            raise ValueError(f"unsupported validation evidence schema {self.schema!r}")
        _require_string(self.market_id, field="market_id", pattern=_MARKET_ID_RE)
        _require_string(
            self.calibration_id,
            field="calibration_id",
            pattern=_PATH_ID_RE,
        )
        if not isinstance(self.check, ValidationCheck):
            raise TypeError("check must be a ValidationCheck")
        if not isinstance(self.status, ValidationStatus):
            raise TypeError("status must be a ValidationStatus")
        _require_string(self.runner_id, field="runner_id", pattern=_PATH_ID_RE)
        _require_string(
            self.runner_version,
            field="runner_version",
            pattern=_RUNNER_VERSION_RE,
        )

        generated = _utc(self.generated_at, field="generated_at")
        event = _utc(self.event_cutoff, field="event_cutoff")
        knowledge = _utc(self.knowledge_cutoff, field="knowledge_cutoff")
        if event > knowledge:
            raise ValueError("event_cutoff cannot follow knowledge_cutoff")
        if knowledge > generated:
            raise ValueError("knowledge_cutoff cannot follow generated_at")
        object.__setattr__(self, "generated_at", generated)
        object.__setattr__(self, "event_cutoff", event)
        object.__setattr__(self, "knowledge_cutoff", knowledge)

        _require_string(
            self.input_fingerprint,
            field="input_fingerprint",
            pattern=_SHA256_RE,
        )
        if not isinstance(self.metrics, Mapping):
            raise TypeError("metrics must be a mapping")
        frozen_metrics = _freeze_json(self.metrics, field="metrics")
        if not isinstance(frozen_metrics, Mapping):  # defensive type narrowing
            raise TypeError("metrics must be a mapping")
        object.__setattr__(self, "metrics", frozen_metrics)

        reasons = _text_tuple(self.reasons, field="reasons")
        references = _text_tuple(
            self.evidence_references,
            field="evidence_references",
        )
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "evidence_references", references)
        if (
            self.status in {ValidationStatus.FAIL, ValidationStatus.PENDING}
            and not reasons
        ):
            raise ValueError(
                f"{self.status.value} artifacts require at least one reason"
            )
        if self.status is not ValidationStatus.PENDING and not references:
            raise ValueError(
                "completed artifacts require at least one evidence reference"
            )

        expected = self._computed_artifact_id()
        supplied = self.artifact_id
        if supplied:
            _require_string(supplied, field="artifact_id", pattern=_SHA256_RE)
            if not hmac.compare_digest(supplied, expected):
                raise ArtifactIntegrityError(
                    "artifact_id does not match canonical artifact content"
                )
        else:
            object.__setattr__(self, "artifact_id", expected)

    @classmethod
    def create(
        cls,
        *,
        market_id: str,
        calibration_id: str,
        check: ValidationCheck | str,
        status: ValidationStatus | str,
        runner_id: str,
        runner_version: str,
        generated_at: datetime,
        event_cutoff: datetime,
        knowledge_cutoff: datetime,
        input_fingerprint: str,
        metrics: Mapping[str, object] | None = None,
        reasons: Iterable[str] = (),
        evidence_references: Iterable[str] = (),
        schema: str = VALIDATION_EVIDENCE_SCHEMA,
    ) -> ValidationEvidenceArtifact:
        """Create an artifact and derive its content identity."""

        return cls(
            market_id=market_id,
            calibration_id=calibration_id,
            check=ValidationCheck(check),
            status=ValidationStatus(status),
            runner_id=runner_id,
            runner_version=runner_version,
            generated_at=generated_at,
            event_cutoff=event_cutoff,
            knowledge_cutoff=knowledge_cutoff,
            input_fingerprint=input_fingerprint,
            metrics={} if metrics is None else metrics,
            reasons=tuple(reasons),
            evidence_references=tuple(evidence_references),
            schema=schema,
        )

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "market_id": self.market_id,
            "calibration_id": self.calibration_id,
            "check": self.check.value,
            "status": self.status.value,
            "runner_id": self.runner_id,
            "runner_version": self.runner_version,
            "generated_at": _timestamp(self.generated_at),
            "event_cutoff": _timestamp(self.event_cutoff),
            "knowledge_cutoff": _timestamp(self.knowledge_cutoff),
            "input_fingerprint": self.input_fingerprint,
            "metrics": _thaw_json(self.metrics),
            "reasons": list(self.reasons),
            "evidence_references": list(self.evidence_references),
        }

    def _computed_artifact_id(self) -> str:
        return hashlib.sha256(_canonical_json_bytes(self._content_dict())).hexdigest()

    def verify_artifact_id(self) -> None:
        """Raise if this instance no longer matches its content identity."""

        expected = self._computed_artifact_id()
        if not hmac.compare_digest(self.artifact_id, expected):
            raise ArtifactIntegrityError(
                "artifact_id does not match canonical artifact content"
            )

    def to_dict(self) -> dict[str, object]:
        record = self._content_dict()
        record["artifact_id"] = self.artifact_id
        return record

    def to_json(self) -> str:
        """Return the byte-stable canonical JSON store representation."""

        return _canonical_json_bytes(self.to_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, record: Mapping[str, object]) -> ValidationEvidenceArtifact:
        if not isinstance(record, Mapping):
            raise TypeError("validation evidence record must be a mapping")
        if any(not isinstance(key, str) for key in record):
            raise TypeError("validation evidence field names must be strings")
        required = {
            "schema",
            "artifact_id",
            "market_id",
            "calibration_id",
            "check",
            "status",
            "runner_id",
            "runner_version",
            "generated_at",
            "event_cutoff",
            "knowledge_cutoff",
            "input_fingerprint",
            "metrics",
            "reasons",
            "evidence_references",
        }
        keys = set(record)
        if keys != required:
            missing = sorted(required - keys)
            unknown = sorted(keys - required)
            raise ValueError(
                f"validation evidence fields do not match schema; "
                f"missing={missing}, unknown={unknown}"
            )
        if not isinstance(record["schema"], str):
            raise TypeError("schema must be a string")
        if not isinstance(record["artifact_id"], str):
            raise TypeError("artifact_id must be a string")
        if not isinstance(record["market_id"], str):
            raise TypeError("market_id must be a string")
        if not isinstance(record["calibration_id"], str):
            raise TypeError("calibration_id must be a string")
        if not isinstance(record["runner_id"], str):
            raise TypeError("runner_id must be a string")
        if not isinstance(record["runner_version"], str):
            raise TypeError("runner_version must be a string")
        if not isinstance(record["input_fingerprint"], str):
            raise TypeError("input_fingerprint must be a string")
        if not isinstance(record["check"], str):
            raise TypeError("check must be a string")
        if not isinstance(record["status"], str):
            raise TypeError("status must be a string")
        metrics = record["metrics"]
        if not isinstance(metrics, Mapping):
            raise TypeError("metrics must be an object")
        reasons = record["reasons"]
        references = record["evidence_references"]
        if not isinstance(reasons, list):
            raise TypeError("reasons must be a JSON array")
        if not isinstance(references, list):
            raise TypeError("evidence_references must be a JSON array")
        return cls(
            schema=record["schema"],
            artifact_id=record["artifact_id"],
            market_id=record["market_id"],
            calibration_id=record["calibration_id"],
            check=ValidationCheck(record["check"]),
            status=ValidationStatus(record["status"]),
            runner_id=record["runner_id"],
            runner_version=record["runner_version"],
            generated_at=_parse_timestamp(record["generated_at"], field="generated_at"),
            event_cutoff=_parse_timestamp(record["event_cutoff"], field="event_cutoff"),
            knowledge_cutoff=_parse_timestamp(
                record["knowledge_cutoff"],
                field="knowledge_cutoff",
            ),
            input_fingerprint=record["input_fingerprint"],
            metrics=metrics,
            reasons=tuple(reasons),
            evidence_references=tuple(references),
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> ValidationEvidenceArtifact:
        if isinstance(payload, bytes):
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ArtifactIntegrityError("artifact is not valid UTF-8") from exc
        elif isinstance(payload, str):
            text = payload
        else:
            raise TypeError("artifact JSON must be str or bytes")

        def reject_constant(value: str) -> object:
            raise ArtifactIntegrityError(f"non-finite JSON number {value!r}")

        try:
            decoded = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=reject_constant,
            )
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ArtifactIntegrityError("artifact is not valid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise ArtifactIntegrityError("artifact JSON root must be an object")
        try:
            return cls.from_dict(decoded)
        except ArtifactIntegrityError:
            raise
        except (TypeError, ValueError) as exc:
            raise ArtifactIntegrityError(f"invalid artifact record: {exc}") from exc


class ValidationEvidenceStore:
    """Atomic append-only filesystem storage for validation artifacts."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        if self.root.exists() and not self.root.is_dir():
            raise ValueError("validation evidence store root must be a directory")

    @staticmethod
    def _filename_stamp(value: datetime) -> str:
        return value.strftime("%Y%m%dT%H%M%S.%fZ")

    def path_for(self, artifact: ValidationEvidenceArtifact) -> Path:
        if not isinstance(artifact, ValidationEvidenceArtifact):
            raise TypeError("artifact must be a ValidationEvidenceArtifact")
        return (
            self.root
            / f"market={artifact.market_id}"
            / f"calibration={artifact.calibration_id}"
            / f"check={artifact.check.value}"
            / f"{self._filename_stamp(artifact.generated_at)}.json"
        )

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        try:
            descriptor = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            # Directory fsync is not supported by every filesystem/platform.
            pass
        finally:
            os.close(descriptor)

    def _load_path(self, path: Path) -> ValidationEvidenceArtifact:
        if path.is_symlink() or not path.is_file():
            raise ArtifactIntegrityError(f"artifact path is not a regular file: {path}")
        try:
            raw = path.read_bytes()
            artifact = ValidationEvidenceArtifact.from_json(raw)
        except ArtifactIntegrityError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise ArtifactIntegrityError(f"invalid artifact at {path}: {exc}") from exc
        canonical = artifact.to_json().encode("utf-8")
        if not hmac.compare_digest(raw, canonical):
            raise ArtifactIntegrityError(f"artifact is not canonical JSON: {path}")

        expected = self.path_for(artifact)
        if path != expected:
            raise ArtifactIntegrityError(
                f"artifact content does not match its store path: {path}"
            )
        return artifact

    def _assert_idempotent(
        self,
        path: Path,
        artifact: ValidationEvidenceArtifact,
    ) -> None:
        try:
            existing = self._load_path(path)
        except (ArtifactIntegrityError, OSError) as exc:
            raise ArtifactConflictError(
                f"refusing to replace an invalid committed artifact at {path}"
            ) from exc
        if existing != artifact:
            raise ArtifactConflictError(
                "validation evidence identity is already committed with different content"
            )

    def append(self, artifact: ValidationEvidenceArtifact) -> Path:
        """Atomically commit an artifact, allowing only byte-identical retries."""

        if not isinstance(artifact, ValidationEvidenceArtifact):
            raise TypeError("artifact must be a ValidationEvidenceArtifact")
        artifact.verify_artifact_id()
        target = self.path_for(artifact)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            self._assert_idempotent(target, artifact)
            return target

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".validation-evidence-",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(artifact.to_json().encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o644)
            try:
                # A hard link publishes a fully written file atomically and,
                # unlike os.replace(), can never overwrite an existing record.
                os.link(temporary, target)
            except FileExistsError:
                self._assert_idempotent(target, artifact)
            else:
                self._fsync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    # ``put`` is a familiar content-store spelling and retains append semantics.
    put = append

    def _all_paths(self) -> tuple[Path, ...]:
        if not self.root.exists():
            return ()
        return tuple(
            sorted(
                self.root.glob("market=*/calibration=*/check=*/*.json"),
                key=lambda path: str(path),
            )
        )

    def load(self, artifact_id: str) -> ValidationEvidenceArtifact:
        """Load by content ID and verify identity, canonical bytes, and path."""

        _require_string(artifact_id, field="artifact_id", pattern=_SHA256_RE)
        match: ValidationEvidenceArtifact | None = None
        for path in self._all_paths():
            artifact = self._load_path(path)
            if artifact.artifact_id != artifact_id:
                continue
            if match is not None:
                raise ArtifactIntegrityError(
                    f"artifact_id {artifact_id} is committed more than once"
                )
            match = artifact
        if match is None:
            raise ArtifactNotFoundError(
                f"validation artifact {artifact_id} was not found"
            )
        return match

    @staticmethod
    def _coerce_check(check: ValidationCheck | str) -> ValidationCheck:
        try:
            return ValidationCheck(check)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown validation check {check!r}") from exc

    def latest_for_check(
        self,
        market_id: str,
        calibration_id: str,
        check: ValidationCheck | str,
    ) -> ValidationEvidenceArtifact | None:
        """Return the newest verified artifact for one exact check identity."""

        _require_string(market_id, field="market_id", pattern=_MARKET_ID_RE)
        _require_string(calibration_id, field="calibration_id", pattern=_PATH_ID_RE)
        selected_check = self._coerce_check(check)
        directory = (
            self.root
            / f"market={market_id}"
            / f"calibration={calibration_id}"
            / f"check={selected_check.value}"
        )
        if not directory.exists():
            return None
        if directory.is_symlink() or not directory.is_dir():
            raise ArtifactIntegrityError(
                f"validation evidence partition is not a regular directory: {directory}"
            )
        candidates: list[ValidationEvidenceArtifact] = []
        for path in sorted(directory.glob("*.json")):
            artifact = self._load_path(path)
            if (
                artifact.market_id != market_id
                or artifact.calibration_id != calibration_id
                or artifact.check is not selected_check
            ):
                raise ArtifactIntegrityError(
                    f"artifact content does not match lookup partition: {path}"
                )
            candidates.append(artifact)
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.generated_at, item.artifact_id))

    # Concise spelling for callers querying one check.
    latest = latest_for_check

    def latest_per_check(
        self,
        market_id: str,
        calibration_id: str,
    ) -> Mapping[ValidationCheck, ValidationEvidenceArtifact]:
        """Return a read-only map containing each check's newest artifact."""

        latest: dict[ValidationCheck, ValidationEvidenceArtifact] = {}
        for check in ValidationCheck:
            artifact = self.latest_for_check(market_id, calibration_id, check)
            if artifact is not None:
                latest[check] = artifact
        return MappingProxyType(latest)


# Compatibility-friendly short spellings for callers that do not need the
# longer domain-specific names.  They are aliases, not parallel contracts.
ValidationEvidence = ValidationEvidenceArtifact
ValidationArtifact = ValidationEvidenceArtifact
ValidationEvidenceStatus = ValidationStatus
ValidationEvidenceIntegrityError = ArtifactIntegrityError
ValidationEvidenceConflictError = ArtifactConflictError


__all__ = [
    "ArtifactConflictError",
    "ArtifactIntegrityError",
    "ArtifactNotFoundError",
    "VALIDATION_EVIDENCE_SCHEMA",
    "ValidationArtifact",
    "ValidationEvidence",
    "ValidationEvidenceArtifact",
    "ValidationEvidenceConflictError",
    "ValidationEvidenceError",
    "ValidationEvidenceIntegrityError",
    "ValidationEvidenceStatus",
    "ValidationEvidenceStore",
    "ValidationStatus",
    "input_fingerprint_for",
]
