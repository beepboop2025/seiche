"""Immutable prospective outcomes for investigative and forecasting evaluation.

Forecasts, eligibility decisions, observations, and resolutions are distinct
append-only facts.  A forecast is prospective only when its prediction,
knowledge, and append clocks are identical.  It commits to an immutable
evidence cut and to the exact market-local dates selected by a versioned
calendar artifact; this module deliberately does not pretend it can infer or
revalidate local business days without that artifact.

The module is storage-neutral.  It verifies canonical records and trusted
chain heads, but production still needs an atomic single-head append store and
signed or externally anchored head receipts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

OUTCOME_LEDGER_SCHEMA = "seiche.investigative-outcome-ledger.v1"
OUTCOME_EXPORT_SCHEMA = "seiche.investigative-outcome-export.v1"
OUTCOME_GENESIS_HASH = "0" * 64
MAX_OUTCOME_RECORD_BYTES = 64 * 1024
MAX_EVIDENCE_REFERENCES = 64
MAX_OBSERVATION_DATES = 252
MAX_OUTCOME_CHAIN_ENTRIES = 50_000
MAX_OUTCOME_EXPORT_ROWS = 50_000
MAX_OUTCOME_EXPORT_BYTES = 256 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 4_096
MAX_EXPORT_JSON_NODES = 20_000_000

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MARKET_ID_RE = re.compile(r"[A-Z0-9]+-[A-Z]{3}")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}")
_IANA_TIMEZONE_RE = re.compile(
    r"(?:UTC|[A-Za-z][A-Za-z0-9._+-]*/[A-Za-z0-9][A-Za-z0-9._+/-]{0,126})"
)


class OutcomeRecordKind(StrEnum):
    FORECAST = "forecast"
    ELIGIBILITY_DECISION = "eligibility_decision"
    OBSERVATION = "observation"
    RESOLUTION = "resolution"


class DatasetEligibility(StrEnum):
    TRAINING_ELIGIBLE = "training_eligible"
    EVALUATION_ONLY = "evaluation_only"
    PROHIBITED = "prohibited"


class OutcomeExportPurpose(StrEnum):
    TRAINING = "training"
    EVALUATION = "evaluation"


class ResolutionDisposition(StrEnum):
    RESOLVED = "resolved"
    CENSORED = "censored"


class OutcomeRowStatus(StrEnum):
    PENDING = "pending"
    MATURED_UNRESOLVED = "matured_unresolved"
    CENSORED = "censored"
    RESOLVED = "resolved"


class OutcomeLedgerError(ValueError):
    """A record, chain, or export violates the outcome contract."""


class OutcomeIntegrityError(OutcomeLedgerError):
    """A content identity, chain link, or trusted head does not verify."""


def _freeze_json(
    value: object,
    *,
    field: str,
    _depth: int = 0,
    _budget: list[int] | None = None,
    max_nodes: int = MAX_JSON_NODES,
) -> object:
    if _depth > MAX_JSON_DEPTH:
        raise OutcomeLedgerError(f"{field} exceeds JSON depth {MAX_JSON_DEPTH}")
    budget = [0] if _budget is None else _budget
    budget[0] += 1
    if budget[0] > max_nodes:
        raise OutcomeLedgerError(f"{field} exceeds JSON node limit {max_nodes}")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise OutcomeLedgerError(f"{field} must not contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or key != key.strip():
                raise OutcomeLedgerError(
                    f"{field} object keys must be non-blank trimmed strings"
                )
            frozen[key] = _freeze_json(
                item,
                field=f"{field}.{key}",
                _depth=_depth + 1,
                _budget=budget,
                max_nodes=max_nodes,
            )
        return MappingProxyType(dict(sorted(frozen.items())))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(
            _freeze_json(
                item,
                field=f"{field}[{index}]",
                _depth=_depth + 1,
                _budget=budget,
                max_nodes=max_nodes,
            )
            for index, item in enumerate(value)
        )
    raise OutcomeLedgerError(
        f"{field} contains unsupported value {type(value).__name__}"
    )


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _canonical_json_bytes(value: object, *, max_nodes: int = MAX_JSON_NODES) -> bytes:
    return json.dumps(
        _thaw_json(
            _freeze_json(
                value,
                field="canonical payload",
                max_nodes=max_nodes,
            )
        ),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: object, *, max_nodes: int = MAX_JSON_NODES) -> str:
    return hashlib.sha256(_canonical_json_bytes(value, max_nodes=max_nodes)).hexdigest()


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise OutcomeLedgerError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    normalized = _utc(value, field="timestamp")
    timespec = "microseconds" if normalized.microsecond else "seconds"
    return normalized.isoformat(timespec=timespec).replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 27 or not value.endswith("Z"):
        raise OutcomeLedgerError(f"{field} must be a canonical UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise OutcomeLedgerError(f"{field} is not an ISO-8601 timestamp") from exc
    parsed = _utc(parsed, field=field)
    if _timestamp(parsed) != value:
        raise OutcomeLedgerError(f"{field} is not in canonical UTC form")
    return parsed


def _local_date(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 10:
        raise OutcomeLedgerError(f"{field} must be an ISO local date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise OutcomeLedgerError(f"{field} must be an ISO local date") from exc
    if parsed.isoformat() != value:
        raise OutcomeLedgerError(f"{field} must use canonical YYYY-MM-DD form")
    return value


def _identifier(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 128
        or _IDENTIFIER_RE.fullmatch(value) is None
    ):
        raise OutcomeLedgerError(f"invalid {field}: {value!r}")
    return value


def _digest(value: object, *, field: str, genesis: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or _SHA256_RE.fullmatch(value) is None
    ):
        raise OutcomeLedgerError(f"invalid {field}: {value!r}")
    if not genesis and value == OUTCOME_GENESIS_HASH:
        raise OutcomeLedgerError(f"{field} cannot be the genesis hash")
    return value


def _digests(
    values: object,
    *,
    field: str,
    maximum: int,
    nonempty: bool = True,
    preserve_order: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise OutcomeLedgerError(f"{field} must be an array")
    if len(values) > maximum:
        raise OutcomeLedgerError(f"{field} exceeds {maximum} values")
    result = tuple(_digest(item, field=f"{field} item") for item in values)
    if nonempty and not result:
        raise OutcomeLedgerError(f"{field} must be non-empty")
    if len(result) != len(set(result)):
        raise OutcomeLedgerError(f"{field} values must be unique")
    return result if preserve_order else tuple(sorted(result))


def _local_dates(values: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise OutcomeLedgerError(f"{field} must be an array")
    if len(values) > MAX_OBSERVATION_DATES:
        raise OutcomeLedgerError(f"{field} exceeds {MAX_OBSERVATION_DATES} dates")
    result = tuple(
        _local_date(item, field=f"{field}[{index}]")
        for index, item in enumerate(values)
    )
    if not result:
        raise OutcomeLedgerError(f"{field} must be non-empty")
    if tuple(sorted(set(result))) != result:
        raise OutcomeLedgerError(f"{field} must be unique and strictly increasing")
    return result


def _iana_timezone(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 128
        or _IANA_TIMEZONE_RE.fullmatch(value) is None
    ):
        raise OutcomeLedgerError(f"{field} must be a canonical IANA timezone key")
    try:
        timezone = ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise OutcomeLedgerError(
            f"{field} must be a canonical IANA timezone key"
        ) from exc
    if timezone.key != value:
        raise OutcomeLedgerError(f"{field} must be a canonical IANA timezone key")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise OutcomeIntegrityError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _exact_payload(
    payload: Mapping[str, object], *, expected: set[str], kind: OutcomeRecordKind
) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise OutcomeLedgerError(f"{kind.value} payload must be an object")
    actual = set(payload)
    if actual != expected:
        raise OutcomeLedgerError(
            f"{kind.value} payload fields mismatch; "
            f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
        )
    return dict(payload)


def _validate_forecast(
    payload: Mapping[str, object], knowledge_time: datetime, recorded_at: datetime
) -> dict[str, object]:
    row = _exact_payload(
        payload,
        expected={
            "model_id",
            "forecast_run_id",
            "target_rule_id",
            "prediction_time",
            "probability_ppm",
            "evidence_ids",
            "evidence_cut_digest",
            "window_opened_at",
            "window_closed_at",
            "observation_dates",
            "calendar_id",
            "calendar_version",
            "calendar_digest",
            "calendar_timezone",
        },
        kind=OutcomeRecordKind.FORECAST,
    )
    model_id = _identifier(row["model_id"], field="forecast model_id")
    forecast_run_id = _identifier(
        row["forecast_run_id"], field="forecast forecast_run_id"
    )
    rule_id = _identifier(row["target_rule_id"], field="forecast target_rule_id")
    prediction_time = _parse_timestamp(
        row["prediction_time"], field="forecast prediction_time"
    )
    if prediction_time != knowledge_time or knowledge_time != recorded_at:
        raise OutcomeLedgerError(
            "forecast prediction_time, knowledge_time, and recorded_at must be equal"
        )
    probability = row["probability_ppm"]
    if (
        isinstance(probability, bool)
        or not isinstance(probability, int)
        or not 0 <= probability <= 1_000_000
    ):
        raise OutcomeLedgerError(
            "forecast probability_ppm must be an integer from 0 to 1000000"
        )
    evidence_ids = _digests(
        row["evidence_ids"],
        field="forecast evidence_ids",
        maximum=MAX_EVIDENCE_REFERENCES,
    )
    evidence_cut_digest = _digest(
        row["evidence_cut_digest"], field="forecast evidence_cut_digest"
    )
    opened = _parse_timestamp(
        row["window_opened_at"], field="forecast window_opened_at"
    )
    closed = _parse_timestamp(
        row["window_closed_at"], field="forecast window_closed_at"
    )
    if opened != prediction_time:
        raise OutcomeLedgerError("forecast target window must open at prediction_time")
    if opened >= closed:
        raise OutcomeLedgerError("forecast target window must close after it opens")
    observation_dates = _local_dates(
        row["observation_dates"], field="forecast observation_dates"
    )
    calendar_id = _identifier(row["calendar_id"], field="forecast calendar_id")
    calendar_version = _identifier(
        row["calendar_version"], field="forecast calendar_version"
    )
    calendar_digest = _digest(row["calendar_digest"], field="forecast calendar_digest")
    calendar_timezone = _iana_timezone(
        row["calendar_timezone"], field="forecast calendar_timezone"
    )
    return {
        "model_id": model_id,
        "forecast_run_id": forecast_run_id,
        "target_rule_id": rule_id,
        "prediction_time": _timestamp(prediction_time),
        "probability_ppm": probability,
        "evidence_ids": list(evidence_ids),
        "evidence_cut_digest": evidence_cut_digest,
        "window_opened_at": _timestamp(opened),
        "window_closed_at": _timestamp(closed),
        "observation_dates": list(observation_dates),
        "calendar_id": calendar_id,
        "calendar_version": calendar_version,
        "calendar_digest": calendar_digest,
        "calendar_timezone": calendar_timezone,
    }


def _validate_eligibility_decision(
    payload: Mapping[str, object], _knowledge_time: datetime, _recorded_at: datetime
) -> dict[str, object]:
    row = _exact_payload(
        payload,
        expected={
            "forecast_record_hash",
            "dataset_eligibility",
            "rights_decision_hash",
            "reviewer_id",
            "policy_id",
            "supersedes_decision_record_hash",
        },
        kind=OutcomeRecordKind.ELIGIBILITY_DECISION,
    )
    forecast_hash = _digest(
        row["forecast_record_hash"], field="eligibility forecast_record_hash"
    )
    raw_eligibility = row["dataset_eligibility"]
    if not isinstance(raw_eligibility, str):
        raise OutcomeLedgerError("eligibility dataset_eligibility is invalid")
    try:
        eligibility = DatasetEligibility(raw_eligibility)
    except (TypeError, ValueError) as exc:
        raise OutcomeLedgerError("eligibility dataset_eligibility is invalid") from exc
    rights_hash = _digest(
        row["rights_decision_hash"], field="eligibility rights_decision_hash"
    )
    reviewer_id = _identifier(row["reviewer_id"], field="eligibility reviewer_id")
    policy_id = _identifier(row["policy_id"], field="eligibility policy_id")
    raw_supersedes = row["supersedes_decision_record_hash"]
    supersedes = (
        None
        if raw_supersedes is None
        else _digest(
            raw_supersedes,
            field="eligibility supersedes_decision_record_hash",
        )
    )
    return {
        "forecast_record_hash": forecast_hash,
        "dataset_eligibility": eligibility.value,
        "rights_decision_hash": rights_hash,
        "reviewer_id": reviewer_id,
        "policy_id": policy_id,
        "supersedes_decision_record_hash": supersedes,
    }


def _validate_observation(
    payload: Mapping[str, object], knowledge_time: datetime, _recorded_at: datetime
) -> dict[str, object]:
    row = _exact_payload(
        payload,
        expected={
            "target_rule_id",
            "observation_date",
            "event_time",
            "event_occurred",
            "source_record_hash",
            "evidence_ids",
        },
        kind=OutcomeRecordKind.OBSERVATION,
    )
    rule_id = _identifier(row["target_rule_id"], field="observation target_rule_id")
    observation_date = _local_date(
        row["observation_date"], field="observation observation_date"
    )
    event_time = _parse_timestamp(row["event_time"], field="observation event_time")
    if event_time > knowledge_time:
        raise OutcomeLedgerError("observation event_time cannot follow knowledge_time")
    occurred = row["event_occurred"]
    if occurred is not None and not isinstance(occurred, bool):
        raise OutcomeLedgerError("observation event_occurred must be boolean or null")
    source_record_hash = _digest(
        row["source_record_hash"], field="observation source_record_hash"
    )
    evidence_ids = _digests(
        row["evidence_ids"],
        field="observation evidence_ids",
        maximum=MAX_EVIDENCE_REFERENCES,
    )
    return {
        "target_rule_id": rule_id,
        "observation_date": observation_date,
        "event_time": _timestamp(event_time),
        "event_occurred": occurred,
        "source_record_hash": source_record_hash,
        "evidence_ids": list(evidence_ids),
    }


def _validate_resolution(
    payload: Mapping[str, object], _knowledge_time: datetime, _recorded_at: datetime
) -> dict[str, object]:
    row = _exact_payload(
        payload,
        expected={
            "forecast_record_hash",
            "observation_record_hashes",
            "disposition",
            "outcome",
            "censor_reason",
        },
        kind=OutcomeRecordKind.RESOLUTION,
    )
    forecast_hash = _digest(
        row["forecast_record_hash"], field="resolution forecast_record_hash"
    )
    observation_hashes = _digests(
        row["observation_record_hashes"],
        field="resolution observation_record_hashes",
        maximum=MAX_OBSERVATION_DATES,
        preserve_order=True,
    )
    raw_disposition = row["disposition"]
    if not isinstance(raw_disposition, str):
        raise OutcomeLedgerError("resolution disposition is invalid")
    try:
        disposition = ResolutionDisposition(raw_disposition)
    except (TypeError, ValueError) as exc:
        raise OutcomeLedgerError("resolution disposition is invalid") from exc
    outcome = row["outcome"]
    censor_reason = row["censor_reason"]
    if disposition is ResolutionDisposition.RESOLVED:
        if not isinstance(outcome, bool):
            raise OutcomeLedgerError("resolved resolution outcome must be boolean")
        if censor_reason is not None:
            raise OutcomeLedgerError("resolved resolution cannot carry censor_reason")
    else:
        if outcome is not None:
            raise OutcomeLedgerError("censored resolution outcome must be null")
        censor_reason = _identifier(censor_reason, field="resolution censor_reason")
    return {
        "forecast_record_hash": forecast_hash,
        "observation_record_hashes": list(observation_hashes),
        "disposition": disposition.value,
        "outcome": outcome,
        "censor_reason": censor_reason,
    }


_VALIDATORS = {
    OutcomeRecordKind.FORECAST: _validate_forecast,
    OutcomeRecordKind.ELIGIBILITY_DECISION: _validate_eligibility_decision,
    OutcomeRecordKind.OBSERVATION: _validate_observation,
    OutcomeRecordKind.RESOLUTION: _validate_resolution,
}


@dataclass(frozen=True, slots=True)
class OutcomeLedgerEntry:
    sequence: int
    previous_record_hash: str
    kind: OutcomeRecordKind
    recorded_at: datetime
    knowledge_time: datetime
    market_id: str
    entity_group_id: str
    payload: Mapping[str, object]
    schema: str = OUTCOME_LEDGER_SCHEMA
    record_hash: str = ""

    def __post_init__(self) -> None:
        if self.schema != OUTCOME_LEDGER_SCHEMA:
            raise OutcomeLedgerError(f"unsupported outcome schema {self.schema!r}")
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or not 0 <= self.sequence < MAX_OUTCOME_CHAIN_ENTRIES
        ):
            raise OutcomeLedgerError(
                f"sequence must be an integer from 0 to {MAX_OUTCOME_CHAIN_ENTRIES - 1}"
            )
        previous = _digest(
            self.previous_record_hash,
            field="previous_record_hash",
            genesis=True,
        )
        if self.sequence == 0 and previous != OUTCOME_GENESIS_HASH:
            raise OutcomeLedgerError("sequence zero must link to the genesis hash")
        if self.sequence > 0 and previous == OUTCOME_GENESIS_HASH:
            raise OutcomeLedgerError("nonzero sequence cannot link to genesis")
        if not isinstance(self.kind, OutcomeRecordKind):
            raise TypeError("kind must be an OutcomeRecordKind")
        recorded_at = _utc(self.recorded_at, field="recorded_at")
        knowledge_time = _utc(self.knowledge_time, field="knowledge_time")
        if knowledge_time > recorded_at:
            raise OutcomeLedgerError("knowledge_time cannot follow recorded_at")
        if (
            not isinstance(self.market_id, str)
            or _MARKET_ID_RE.fullmatch(self.market_id) is None
        ):
            raise OutcomeLedgerError(f"invalid market_id: {self.market_id!r}")
        entity_group_id = _identifier(self.entity_group_id, field="entity_group_id")
        payload = _VALIDATORS[self.kind](self.payload, knowledge_time, recorded_at)
        object.__setattr__(self, "recorded_at", recorded_at)
        object.__setattr__(self, "knowledge_time", knowledge_time)
        object.__setattr__(self, "entity_group_id", entity_group_id)
        object.__setattr__(self, "payload", _freeze_json(payload, field="payload"))

        if len(_canonical_json_bytes(self._content_dict())) > MAX_OUTCOME_RECORD_BYTES:
            raise OutcomeLedgerError(
                f"canonical outcome record exceeds {MAX_OUTCOME_RECORD_BYTES} bytes"
            )

        expected = self._computed_hash()
        if self.record_hash:
            supplied = _digest(self.record_hash, field="record_hash")
            if not hmac.compare_digest(supplied, expected):
                raise OutcomeIntegrityError(
                    "record_hash does not match canonical outcome content"
                )
        else:
            object.__setattr__(self, "record_hash", expected)

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "sequence": self.sequence,
            "previous_record_hash": self.previous_record_hash,
            "kind": self.kind.value,
            "recorded_at": _timestamp(self.recorded_at),
            "knowledge_time": _timestamp(self.knowledge_time),
            "market_id": self.market_id,
            "entity_group_id": self.entity_group_id,
            "payload": _thaw_json(self.payload),
        }

    def _computed_hash(self) -> str:
        return _sha256(self._content_dict())

    def to_dict(self) -> dict[str, object]:
        record = self._content_dict()
        record["record_hash"] = self.record_hash
        return record

    def to_json(self) -> str:
        return _canonical_json_bytes(self.to_dict()).decode("utf-8")

    def verify(self) -> None:
        if not hmac.compare_digest(self.record_hash, self._computed_hash()):
            raise OutcomeIntegrityError("outcome record content hash changed")

    @classmethod
    def from_dict(cls, record: Mapping[str, object]) -> OutcomeLedgerEntry:
        required = {
            "schema",
            "sequence",
            "previous_record_hash",
            "kind",
            "recorded_at",
            "knowledge_time",
            "market_id",
            "entity_group_id",
            "payload",
            "record_hash",
        }
        if not isinstance(record, Mapping):
            raise OutcomeLedgerError("outcome record must be an object")
        if any(not isinstance(key, str) for key in record):
            raise OutcomeLedgerError("outcome record field names must be strings")
        if set(record) != required:
            actual = set(record)
            raise OutcomeLedgerError(
                "outcome record fields mismatch; "
                f"missing={sorted(required - actual)}, unknown={sorted(actual - required)}"
            )
        raw_kind = record["kind"]
        if not isinstance(raw_kind, str):
            raise OutcomeLedgerError("outcome record kind is invalid")
        try:
            kind = OutcomeRecordKind(raw_kind)
        except (TypeError, ValueError) as exc:
            raise OutcomeLedgerError("outcome record kind is invalid") from exc
        payload = record["payload"]
        if not isinstance(payload, Mapping):
            raise OutcomeLedgerError("outcome record payload must be an object")
        return cls(
            schema=record["schema"],  # type: ignore[arg-type]
            sequence=record["sequence"],  # type: ignore[arg-type]
            previous_record_hash=record["previous_record_hash"],  # type: ignore[arg-type]
            kind=kind,
            recorded_at=_parse_timestamp(record["recorded_at"], field="recorded_at"),
            knowledge_time=_parse_timestamp(
                record["knowledge_time"], field="knowledge_time"
            ),
            market_id=record["market_id"],  # type: ignore[arg-type]
            entity_group_id=record["entity_group_id"],  # type: ignore[arg-type]
            payload=payload,
            record_hash=record["record_hash"],  # type: ignore[arg-type]
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> OutcomeLedgerEntry:
        if isinstance(payload, bytes):
            raw = payload
        elif isinstance(payload, str):
            try:
                raw = payload.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise OutcomeIntegrityError(
                    "outcome record is not Unicode scalar text"
                ) from exc
        else:
            raise TypeError("outcome record JSON must be text or bytes")
        if len(raw) > MAX_OUTCOME_RECORD_BYTES:
            raise OutcomeIntegrityError(
                f"outcome record exceeds {MAX_OUTCOME_RECORD_BYTES} bytes"
            )

        def reject_constant(value: str) -> object:
            raise OutcomeIntegrityError(f"non-finite JSON number {value!r}")

        def bounded_integer(value: str) -> int:
            digits = value[1:] if value.startswith("-") else value
            if len(digits) > 20:
                raise OutcomeIntegrityError("JSON integer exceeds 20 digits")
            return int(value)

        try:
            decoded = json.loads(
                raw,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=reject_constant,
                parse_int=bounded_integer,
            )
            _freeze_json(decoded, field="outcome record JSON")
        except OutcomeIntegrityError:
            raise
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValueError,
            RecursionError,
        ) as exc:
            raise OutcomeIntegrityError(
                "outcome record is not valid bounded JSON"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise OutcomeIntegrityError("outcome record JSON root must be an object")
        try:
            record = cls.from_dict(decoded)
        except OutcomeIntegrityError:
            raise
        except (TypeError, ValueError, RecursionError) as exc:
            raise OutcomeIntegrityError(f"invalid outcome record: {exc}") from exc
        if not hmac.compare_digest(record.to_json().encode("utf-8"), raw):
            raise OutcomeIntegrityError("outcome record JSON is not canonical")
        return record


def _bounded_entries(
    entries: Iterable[OutcomeLedgerEntry],
) -> tuple[OutcomeLedgerEntry, ...]:
    rows: list[OutcomeLedgerEntry] = []
    for row in entries:
        if len(rows) >= MAX_OUTCOME_CHAIN_ENTRIES:
            raise OutcomeLedgerError(
                f"outcome chain exceeds {MAX_OUTCOME_CHAIN_ENTRIES} entries"
            )
        rows.append(row)
    return tuple(rows)


def _verify_chain_structure(
    entries: Iterable[OutcomeLedgerEntry], *, expected_head_hash: str
) -> tuple[OutcomeLedgerEntry, ...]:
    expected_head = _digest(
        expected_head_hash, field="expected_head_hash", genesis=True
    )
    rows = _bounded_entries(entries)
    by_hash: set[str] = set()
    previous_hash = OUTCOME_GENESIS_HASH
    previous_recorded_at: datetime | None = None
    previous_knowledge_time: datetime | None = None
    for expected_sequence, row in enumerate(rows):
        if not isinstance(row, OutcomeLedgerEntry):
            raise TypeError("outcome chain entries must be OutcomeLedgerEntry")
        row.verify()
        if row.sequence != expected_sequence:
            raise OutcomeIntegrityError("outcome chain sequence is not contiguous")
        if row.previous_record_hash != previous_hash:
            raise OutcomeIntegrityError("outcome chain previous hash does not match")
        if row.record_hash in by_hash:
            raise OutcomeIntegrityError(
                "outcome chain contains a duplicate record hash"
            )
        if previous_recorded_at is not None and row.recorded_at < previous_recorded_at:
            raise OutcomeIntegrityError("outcome chain recorded_at moved backwards")
        if (
            previous_knowledge_time is not None
            and row.knowledge_time < previous_knowledge_time
        ):
            raise OutcomeIntegrityError("outcome chain knowledge_time moved backwards")
        by_hash.add(row.record_hash)
        previous_hash = row.record_hash
        previous_recorded_at = row.recorded_at
        previous_knowledge_time = row.knowledge_time
    actual_head = rows[-1].record_hash if rows else OUTCOME_GENESIS_HASH
    if not hmac.compare_digest(actual_head, expected_head):
        raise OutcomeIntegrityError(
            "outcome chain does not match the caller-supplied trusted head"
        )
    return rows


@dataclass(slots=True)
class _SemanticState:
    forecasts: dict[str, OutcomeLedgerEntry]
    observations: dict[str, OutcomeLedgerEntry]
    eligibility_decisions: dict[str, OutcomeLedgerEntry]
    resolutions: dict[str, OutcomeLedgerEntry]


def _same_identity(first: OutcomeLedgerEntry, second: OutcomeLedgerEntry) -> bool:
    return (
        first.market_id == second.market_id
        and first.entity_group_id == second.entity_group_id
    )


def _case_group_digest_values(
    *,
    market_id: str,
    entity_group_id: str,
    target_rule_id: str,
    prediction_time: str,
    window_opened_at: str,
    window_closed_at: str,
    observation_dates: Sequence[str],
) -> str:
    """Hash the semantic target schedule, excluding replaceable provenance."""

    return _sha256(
        {
            "market_id": market_id,
            "entity_group_id": entity_group_id,
            "target_rule_id": target_rule_id,
            "prediction_time": prediction_time,
            "window_opened_at": window_opened_at,
            "window_closed_at": window_closed_at,
            "observation_dates": list(observation_dates),
        }
    )


def _case_group_digest(forecast: OutcomeLedgerEntry) -> str:
    """Model-neutral target identity used to keep dataset splits disjoint."""

    payload = forecast.payload
    return _case_group_digest_values(
        market_id=forecast.market_id,
        entity_group_id=forecast.entity_group_id,
        target_rule_id=cast(str, payload["target_rule_id"]),
        prediction_time=cast(str, payload["prediction_time"]),
        window_opened_at=cast(str, payload["window_opened_at"]),
        window_closed_at=cast(str, payload["window_closed_at"]),
        observation_dates=cast(tuple[str, ...], payload["observation_dates"]),
    )


def _forecast_identity_digest_values(
    *, case_group_digest: str, model_id: str, forecast_run_id: str
) -> str:
    return _sha256(
        {
            "case_group_digest": case_group_digest,
            "model_id": model_id,
            "forecast_run_id": forecast_run_id,
        }
    )


def _forecast_identity_digest(forecast: OutcomeLedgerEntry) -> str:
    """One model/run prediction for a case, excluding value and evidence choices."""

    return _forecast_identity_digest_values(
        case_group_digest=_case_group_digest(forecast),
        model_id=cast(str, forecast.payload["model_id"]),
        forecast_run_id=cast(str, forecast.payload["forecast_run_id"]),
    )


def _replay_semantics(rows: Sequence[OutcomeLedgerEntry]) -> _SemanticState:
    forecasts: dict[str, OutcomeLedgerEntry] = {}
    observations: dict[str, OutcomeLedgerEntry] = {}
    eligibility_decisions: dict[str, OutcomeLedgerEntry] = {}
    resolutions: dict[str, OutcomeLedgerEntry] = {}
    forecast_identity_digests: set[str] = set()
    case_group_by_forecast: dict[str, str] = {}
    case_group_split_history: dict[str, DatasetEligibility] = {}

    for row in rows:
        payload = row.payload
        if row.kind is OutcomeRecordKind.FORECAST:
            forecast_identity = _forecast_identity_digest(row)
            if forecast_identity in forecast_identity_digests:
                raise OutcomeIntegrityError(
                    "outcome chain contains a semantic duplicate forecast"
                )
            forecast_identity_digests.add(forecast_identity)
            forecasts[row.record_hash] = row
            case_group_by_forecast[row.record_hash] = _case_group_digest(row)
            continue

        if row.kind is OutcomeRecordKind.OBSERVATION:
            observations[row.record_hash] = row
            continue

        forecast_hash = str(payload["forecast_record_hash"])
        forecast = forecasts.get(forecast_hash)
        if forecast is None:
            raise OutcomeIntegrityError(
                f"{row.kind.value} does not reference an earlier forecast"
            )
        if not _same_identity(forecast, row):
            raise OutcomeIntegrityError(
                f"{row.kind.value} identity differs from its forecast"
            )

        if row.kind is OutcomeRecordKind.ELIGIBILITY_DECISION:
            current = eligibility_decisions.get(forecast_hash)
            supersedes = payload["supersedes_decision_record_hash"]
            selected = DatasetEligibility(str(payload["dataset_eligibility"]))
            if current is None:
                if supersedes is not None:
                    raise OutcomeIntegrityError(
                        "first eligibility decision cannot supersede another decision"
                    )
                if (
                    row.knowledge_time != forecast.knowledge_time
                    or row.recorded_at != forecast.recorded_at
                ):
                    raise OutcomeIntegrityError(
                        "first eligibility decision must be reviewed at forecast issuance"
                    )
            else:
                if supersedes != current.record_hash:
                    raise OutcomeIntegrityError(
                        "eligibility decision must supersede the latest decision"
                    )
                previous = DatasetEligibility(
                    str(current.payload["dataset_eligibility"])
                )
                if previous is DatasetEligibility.PROHIBITED:
                    raise OutcomeIntegrityError(
                        "prohibited eligibility is terminal and cannot be superseded"
                    )
                if (
                    selected is not previous
                    and selected is not DatasetEligibility.PROHIBITED
                ):
                    raise OutcomeIntegrityError(
                        "eligibility cannot move between evaluation and training"
                    )
            group = case_group_by_forecast[forecast_hash]
            if selected is not DatasetEligibility.PROHIBITED:
                prior_group_split = case_group_split_history.get(group)
                if prior_group_split is not None and selected is not prior_group_split:
                    raise OutcomeIntegrityError(
                        "one case group cannot cross evaluation and training splits"
                    )
                case_group_split_history[group] = selected
            eligibility_decisions[forecast_hash] = row
            continue

        if forecast_hash in resolutions:
            raise OutcomeIntegrityError("one forecast cannot have multiple resolutions")
        observation_hashes = cast(tuple[str, ...], payload["observation_record_hashes"])
        cited: list[OutcomeLedgerEntry] = []
        for observation_hash in observation_hashes:
            observation = observations.get(str(observation_hash))
            if observation is None:
                raise OutcomeIntegrityError(
                    "resolution does not reference an earlier observation"
                )
            if not _same_identity(forecast, observation):
                raise OutcomeIntegrityError(
                    "resolution observation identity is incompatible"
                )
            if (
                observation.payload["target_rule_id"]
                != forecast.payload["target_rule_id"]
            ):
                raise OutcomeIntegrityError(
                    "resolution observation target rule is incompatible"
                )
            if observation.knowledge_time > row.knowledge_time:
                raise OutcomeIntegrityError(
                    "resolution uses an observation not yet knowable"
                )
            cited.append(observation)

        expected_dates = cast(tuple[str, ...], forecast.payload["observation_dates"])
        actual_dates = tuple(
            str(observation.payload["observation_date"]) for observation in cited
        )
        if actual_dates != expected_dates:
            raise OutcomeIntegrityError(
                "resolution observations must match the forecast's exact local dates"
            )
        opened = _parse_timestamp(
            forecast.payload["window_opened_at"], field="forecast window_opened_at"
        )
        closed = _parse_timestamp(
            forecast.payload["window_closed_at"], field="forecast window_closed_at"
        )
        if row.knowledge_time < closed:
            raise OutcomeIntegrityError(
                "resolution cannot be known before the forecast window closes"
            )
        event_times = [
            _parse_timestamp(
                observation.payload["event_time"], field="observation event_time"
            )
            for observation in cited
        ]
        if any(
            event_time <= opened or event_time > closed for event_time in event_times
        ):
            raise OutcomeIntegrityError(
                "resolution observation falls outside the forecast target window"
            )
        if event_times != sorted(event_times) or len(event_times) != len(
            set(event_times)
        ):
            raise OutcomeIntegrityError(
                "resolution observation event times must be strictly increasing"
            )
        calendar_timezone = ZoneInfo(cast(str, forecast.payload["calendar_timezone"]))
        event_local_dates = tuple(
            event_time.astimezone(calendar_timezone).date().isoformat()
            for event_time in event_times
        )
        if event_local_dates != actual_dates:
            raise OutcomeIntegrityError(
                "resolution observation event_time does not match its claimed "
                "market-local date"
            )

        values = [observation.payload["event_occurred"] for observation in cited]
        if any(value is True for value in values):
            expected_disposition = ResolutionDisposition.RESOLVED
            expected_outcome: bool | None = True
        elif all(value is False for value in values):
            expected_disposition = ResolutionDisposition.RESOLVED
            expected_outcome = False
        else:
            expected_disposition = ResolutionDisposition.CENSORED
            expected_outcome = None
        if (
            payload["disposition"] != expected_disposition.value
            or payload["outcome"] is not expected_outcome
        ):
            raise OutcomeIntegrityError(
                "resolution disposition/outcome differs from its cited observations"
            )
        resolutions[forecast_hash] = row

    return _SemanticState(
        forecasts=forecasts,
        observations=observations,
        eligibility_decisions=eligibility_decisions,
        resolutions=resolutions,
    )


def verify_outcome_chain(
    entries: Iterable[OutcomeLedgerEntry], *, expected_head_hash: str
) -> tuple[OutcomeLedgerEntry, ...]:
    """Verify the complete chain against a trusted head and all semantics."""

    rows = _verify_chain_structure(entries, expected_head_hash=expected_head_hash)
    _replay_semantics(rows)
    return rows


_OUTCOME_EXPORT_ROW_FIELDS = frozenset(
    {
        "forecast_record_hash",
        "eligibility_decision_record_hash",
        "rights_decision_hash",
        "reviewer_id",
        "eligibility_policy_id",
        "dataset_eligibility",
        "resolution_record_hash",
        "market_id",
        "entity_group_id",
        "forecast_identity_digest",
        "case_group_digest",
        "model_id",
        "forecast_run_id",
        "target_rule_id",
        "prediction_time",
        "forecast_knowledge_time",
        "forecast_recorded_at",
        "window_opened_at",
        "window_closed_at",
        "observation_dates",
        "calendar_id",
        "calendar_version",
        "calendar_digest",
        "calendar_timezone",
        "probability_ppm",
        "status",
        "label_eligible",
        "outcome",
        "censor_reason",
        "resolution_knowledge_time",
        "observation_record_hashes",
        "evidence_ids",
        "evidence_cut_digest",
    }
)


def _expected_export_policy(purpose: OutcomeExportPurpose) -> dict[str, object]:
    eligibility = {
        OutcomeExportPurpose.TRAINING: DatasetEligibility.TRAINING_ELIGIBLE.value,
        OutcomeExportPurpose.EVALUATION: DatasetEligibility.EVALUATION_ONLY.value,
    }[purpose]
    return {
        "policy_id": "seiche-investigative-outcome-cut/v1",
        "eligibility_required": eligibility,
        "reviewed_eligibility_decision_required": True,
        "trusted_full_chain_head_required": True,
        "semantic_validation_scope": "visible_prefix",
        "requires_recorded_at_lte_as_of": True,
        "requires_knowledge_time_lte_as_of": True,
        "includes_pending_denominator_rows": True,
        "includes_matured_unresolved_denominator_rows": True,
        "includes_censored_denominator_rows": True,
        "null_outcome_is_negative": False,
        "label_eligible_requires_resolved_status": True,
        "calendar_business_day_status_requires_external_evidence_join": True,
        "copies_observation_values": False,
    }


def _validate_export_policy(
    policy: Mapping[str, object], *, purpose: OutcomeExportPurpose
) -> dict[str, object]:
    if not isinstance(policy, Mapping):
        raise OutcomeLedgerError("outcome export policy must be an object")
    if any(not isinstance(key, str) for key in policy):
        raise OutcomeLedgerError("outcome export policy field names must be strings")
    expected = _expected_export_policy(purpose)
    if set(policy) != set(expected):
        raise OutcomeLedgerError("outcome export policy fields do not match v1")
    for key, expected_value in expected.items():
        actual_value = policy[key]
        if (
            type(actual_value) is not type(expected_value)
            or actual_value != expected_value
        ):
            raise OutcomeLedgerError(
                f"outcome export policy field {key!r} does not match v1"
            )
    return expected


def _validate_export_row(
    row: Mapping[str, object],
    *,
    index: int,
    purpose: OutcomeExportPurpose,
    as_of: datetime,
) -> dict[str, object]:
    field = f"outcome export row {index}"
    if not isinstance(row, Mapping):
        raise OutcomeLedgerError(f"{field} must be an object")
    if any(not isinstance(key, str) for key in row):
        raise OutcomeLedgerError(f"{field} field names must be strings")
    if set(row) != _OUTCOME_EXPORT_ROW_FIELDS:
        raise OutcomeLedgerError(f"{field} fields do not match v1")

    forecast_hash = _digest(
        row["forecast_record_hash"], field=f"{field} forecast_record_hash"
    )
    eligibility_hash = _digest(
        row["eligibility_decision_record_hash"],
        field=f"{field} eligibility_decision_record_hash",
    )
    rights_hash = _digest(
        row["rights_decision_hash"], field=f"{field} rights_decision_hash"
    )
    reviewer_id = _identifier(row["reviewer_id"], field=f"{field} reviewer_id")
    eligibility_policy_id = _identifier(
        row["eligibility_policy_id"], field=f"{field} eligibility_policy_id"
    )
    required_eligibility = {
        OutcomeExportPurpose.TRAINING: DatasetEligibility.TRAINING_ELIGIBLE,
        OutcomeExportPurpose.EVALUATION: DatasetEligibility.EVALUATION_ONLY,
    }[purpose]
    raw_eligibility = row["dataset_eligibility"]
    if not isinstance(raw_eligibility, str):
        raise OutcomeLedgerError(f"{field} dataset_eligibility is invalid")
    try:
        eligibility = DatasetEligibility(raw_eligibility)
    except ValueError as exc:
        raise OutcomeLedgerError(f"{field} dataset_eligibility is invalid") from exc
    if eligibility is not required_eligibility:
        raise OutcomeLedgerError(
            f"{field} dataset_eligibility does not match export purpose"
        )

    raw_resolution_hash = row["resolution_record_hash"]
    resolution_hash = (
        None
        if raw_resolution_hash is None
        else _digest(raw_resolution_hash, field=f"{field} resolution_record_hash")
    )
    market_id = row["market_id"]
    if (
        not isinstance(market_id, str)
        or len(market_id) > 32
        or _MARKET_ID_RE.fullmatch(market_id) is None
    ):
        raise OutcomeLedgerError(f"invalid {field} market_id: {market_id!r}")
    entity_group_id = _identifier(
        row["entity_group_id"], field=f"{field} entity_group_id"
    )
    model_id = _identifier(row["model_id"], field=f"{field} model_id")
    forecast_run_id = _identifier(
        row["forecast_run_id"], field=f"{field} forecast_run_id"
    )
    target_rule_id = _identifier(row["target_rule_id"], field=f"{field} target_rule_id")

    prediction_time = _parse_timestamp(
        row["prediction_time"], field=f"{field} prediction_time"
    )
    forecast_knowledge_time = _parse_timestamp(
        row["forecast_knowledge_time"], field=f"{field} forecast_knowledge_time"
    )
    forecast_recorded_at = _parse_timestamp(
        row["forecast_recorded_at"], field=f"{field} forecast_recorded_at"
    )
    window_opened_at = _parse_timestamp(
        row["window_opened_at"], field=f"{field} window_opened_at"
    )
    window_closed_at = _parse_timestamp(
        row["window_closed_at"], field=f"{field} window_closed_at"
    )
    if not (
        prediction_time
        == forecast_knowledge_time
        == forecast_recorded_at
        == window_opened_at
    ):
        raise OutcomeLedgerError(
            f"{field} forecast issuance and window-open clocks must be equal"
        )
    if window_closed_at <= window_opened_at:
        raise OutcomeLedgerError(f"{field} target window must close after it opens")
    if forecast_knowledge_time > as_of or forecast_recorded_at > as_of:
        raise OutcomeLedgerError(f"{field} forecast is later than export as_of")

    observation_dates = _local_dates(
        row["observation_dates"], field=f"{field} observation_dates"
    )
    calendar_id = _identifier(row["calendar_id"], field=f"{field} calendar_id")
    calendar_version = _identifier(
        row["calendar_version"], field=f"{field} calendar_version"
    )
    calendar_digest = _digest(row["calendar_digest"], field=f"{field} calendar_digest")
    calendar_timezone = _iana_timezone(
        row["calendar_timezone"], field=f"{field} calendar_timezone"
    )
    probability = row["probability_ppm"]
    if (
        isinstance(probability, bool)
        or not isinstance(probability, int)
        or not 0 <= probability <= 1_000_000
    ):
        raise OutcomeLedgerError(
            f"{field} probability_ppm must be an integer from 0 to 1000000"
        )

    case_group_digest = _digest(
        row["case_group_digest"], field=f"{field} case_group_digest"
    )
    expected_case_group = _case_group_digest_values(
        market_id=market_id,
        entity_group_id=entity_group_id,
        target_rule_id=target_rule_id,
        prediction_time=_timestamp(prediction_time),
        window_opened_at=_timestamp(window_opened_at),
        window_closed_at=_timestamp(window_closed_at),
        observation_dates=observation_dates,
    )
    if not hmac.compare_digest(case_group_digest, expected_case_group):
        raise OutcomeIntegrityError(f"{field} case_group_digest does not verify")
    forecast_identity_digest = _digest(
        row["forecast_identity_digest"], field=f"{field} forecast_identity_digest"
    )
    expected_forecast_identity = _forecast_identity_digest_values(
        case_group_digest=case_group_digest,
        model_id=model_id,
        forecast_run_id=forecast_run_id,
    )
    if not hmac.compare_digest(forecast_identity_digest, expected_forecast_identity):
        raise OutcomeIntegrityError(f"{field} forecast_identity_digest does not verify")

    raw_status = row["status"]
    if not isinstance(raw_status, str):
        raise OutcomeLedgerError(f"{field} status is invalid")
    try:
        status = OutcomeRowStatus(raw_status)
    except ValueError as exc:
        raise OutcomeLedgerError(f"{field} status is invalid") from exc
    label_eligible = row["label_eligible"]
    if not isinstance(label_eligible, bool):
        raise OutcomeLedgerError(f"{field} label_eligible must be boolean")
    outcome = row["outcome"]
    if outcome is not None and not isinstance(outcome, bool):
        raise OutcomeLedgerError(f"{field} outcome must be boolean or null")
    censor_reason = row["censor_reason"]
    raw_resolution_knowledge_time = row["resolution_knowledge_time"]
    resolution_knowledge_time = (
        None
        if raw_resolution_knowledge_time is None
        else _parse_timestamp(
            raw_resolution_knowledge_time,
            field=f"{field} resolution_knowledge_time",
        )
    )
    observation_hashes = _digests(
        row["observation_record_hashes"],
        field=f"{field} observation_record_hashes",
        maximum=MAX_OBSERVATION_DATES,
        nonempty=False,
        preserve_order=True,
    )

    if status is OutcomeRowStatus.PENDING:
        if as_of >= window_closed_at:
            raise OutcomeLedgerError(f"{field} pending status is past target close")
        if (
            any(
                value is not None
                for value in (
                    resolution_hash,
                    resolution_knowledge_time,
                    outcome,
                    censor_reason,
                )
            )
            or observation_hashes
        ):
            raise OutcomeLedgerError(f"{field} pending status fields are inconsistent")
        if label_eligible:
            raise OutcomeLedgerError(f"{field} pending status cannot be label eligible")
    elif status is OutcomeRowStatus.MATURED_UNRESOLVED:
        if as_of < window_closed_at:
            raise OutcomeLedgerError(
                f"{field} matured_unresolved status precedes target close"
            )
        if (
            resolution_hash is not None
            or resolution_knowledge_time is not None
            or outcome is not None
            or censor_reason != "missing_resolution_record"
            or observation_hashes
            or label_eligible
        ):
            raise OutcomeLedgerError(
                f"{field} matured_unresolved status fields are inconsistent"
            )
    else:
        if resolution_hash is None or resolution_knowledge_time is None:
            raise OutcomeLedgerError(f"{field} terminal status requires a resolution")
        if not observation_hashes or len(observation_hashes) != len(observation_dates):
            raise OutcomeLedgerError(
                f"{field} terminal status requires one observation per local date"
            )
        if not window_closed_at <= resolution_knowledge_time <= as_of:
            raise OutcomeLedgerError(
                f"{field} resolution clock is outside the export interval"
            )
        if status is OutcomeRowStatus.RESOLVED:
            if not isinstance(outcome, bool) or censor_reason is not None:
                raise OutcomeLedgerError(
                    f"{field} resolved status fields are inconsistent"
                )
            if not label_eligible:
                raise OutcomeLedgerError(
                    f"{field} resolved status must be label eligible"
                )
        else:
            if outcome is not None or label_eligible:
                raise OutcomeLedgerError(
                    f"{field} censored status fields are inconsistent"
                )
            censor_reason = _identifier(censor_reason, field=f"{field} censor_reason")

    evidence_ids = _digests(
        row["evidence_ids"],
        field=f"{field} evidence_ids",
        maximum=MAX_EVIDENCE_REFERENCES,
    )
    evidence_cut_digest = _digest(
        row["evidence_cut_digest"], field=f"{field} evidence_cut_digest"
    )
    return {
        "forecast_record_hash": forecast_hash,
        "eligibility_decision_record_hash": eligibility_hash,
        "rights_decision_hash": rights_hash,
        "reviewer_id": reviewer_id,
        "eligibility_policy_id": eligibility_policy_id,
        "dataset_eligibility": eligibility.value,
        "resolution_record_hash": resolution_hash,
        "market_id": market_id,
        "entity_group_id": entity_group_id,
        "forecast_identity_digest": forecast_identity_digest,
        "case_group_digest": case_group_digest,
        "model_id": model_id,
        "forecast_run_id": forecast_run_id,
        "target_rule_id": target_rule_id,
        "prediction_time": _timestamp(prediction_time),
        "forecast_knowledge_time": _timestamp(forecast_knowledge_time),
        "forecast_recorded_at": _timestamp(forecast_recorded_at),
        "window_opened_at": _timestamp(window_opened_at),
        "window_closed_at": _timestamp(window_closed_at),
        "observation_dates": list(observation_dates),
        "calendar_id": calendar_id,
        "calendar_version": calendar_version,
        "calendar_digest": calendar_digest,
        "calendar_timezone": calendar_timezone,
        "probability_ppm": probability,
        "status": status.value,
        "label_eligible": label_eligible,
        "outcome": outcome,
        "censor_reason": censor_reason,
        "resolution_knowledge_time": (
            None
            if resolution_knowledge_time is None
            else _timestamp(resolution_knowledge_time)
        ),
        "observation_record_hashes": list(observation_hashes),
        "evidence_ids": list(evidence_ids),
        "evidence_cut_digest": evidence_cut_digest,
    }


def _bounded_export_json_bytes(value: object) -> bytes:
    encoded = _canonical_json_bytes(value, max_nodes=MAX_EXPORT_JSON_NODES)
    if len(encoded) > MAX_OUTCOME_EXPORT_BYTES:
        raise OutcomeLedgerError(
            f"canonical outcome export exceeds {MAX_OUTCOME_EXPORT_BYTES} bytes"
        )
    return encoded


@dataclass(frozen=True, slots=True)
class InvestigativeOutcomeExport:
    as_of: datetime
    purpose: OutcomeExportPurpose
    source_head_hash: str
    rows: tuple[Mapping[str, object], ...]
    policy: Mapping[str, object]
    export_hash: str
    schema: str = OUTCOME_EXPORT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != OUTCOME_EXPORT_SCHEMA:
            raise OutcomeLedgerError("unsupported outcome export schema")
        as_of = _utc(self.as_of, field="as_of")
        if not isinstance(self.purpose, OutcomeExportPurpose):
            raise TypeError("purpose must be an OutcomeExportPurpose")
        source_head_hash = _digest(
            self.source_head_hash, field="source_head_hash", genesis=True
        )
        if not isinstance(self.rows, (tuple, list)):
            raise OutcomeLedgerError("outcome export rows must be an array")
        if len(self.rows) > MAX_OUTCOME_EXPORT_ROWS:
            raise OutcomeLedgerError(
                f"outcome export exceeds {MAX_OUTCOME_EXPORT_ROWS} rows"
            )
        validated_rows: list[dict[str, object]] = []
        for index, row in enumerate(self.rows):
            validated_rows.append(
                _validate_export_row(
                    row,
                    index=index,
                    purpose=self.purpose,
                    as_of=as_of,
                )
            )
        validated_rows.sort(
            key=lambda row: (
                cast(str, row["prediction_time"]),
                cast(str, row["market_id"]),
                cast(str, row["model_id"]),
                cast(str, row["forecast_record_hash"]),
            )
        )
        forecast_hashes = [
            cast(str, row["forecast_record_hash"]) for row in validated_rows
        ]
        if len(forecast_hashes) != len(set(forecast_hashes)):
            raise OutcomeIntegrityError(
                "outcome export contains a duplicate forecast_record_hash"
            )
        forecast_identities = [
            cast(str, row["forecast_identity_digest"]) for row in validated_rows
        ]
        if len(forecast_identities) != len(set(forecast_identities)):
            raise OutcomeIntegrityError(
                "outcome export contains a duplicate forecast_identity_digest"
            )
        if validated_rows and source_head_hash == OUTCOME_GENESIS_HASH:
            raise OutcomeIntegrityError(
                "non-empty outcome export cannot use the genesis source head"
            )

        frozen_rows: list[Mapping[str, object]] = []
        for index, row in enumerate(validated_rows):
            frozen = _freeze_json(row, field=f"rows[{index}]")
            if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
                raise OutcomeLedgerError(
                    f"outcome export row {index} must be an object"
                )
            frozen_rows.append(frozen)
        validated_policy = _validate_export_policy(self.policy, purpose=self.purpose)
        frozen_policy = _freeze_json(validated_policy, field="policy")
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "source_head_hash", source_head_hash)
        object.__setattr__(self, "rows", tuple(frozen_rows))
        object.__setattr__(self, "policy", frozen_policy)
        supplied_hash = _digest(self.export_hash, field="export_hash")
        content_bytes = _bounded_export_json_bytes(self._content_dict())
        expected_hash = hashlib.sha256(content_bytes).hexdigest()
        if not hmac.compare_digest(supplied_hash, expected_hash):
            raise OutcomeIntegrityError("outcome export hash does not verify")
        _bounded_export_json_bytes(self.to_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "as_of": _timestamp(self.as_of),
            "purpose": self.purpose.value,
            "source_head_hash": self.source_head_hash,
            "policy": _thaw_json(self.policy),
            "rows": _thaw_json(self.rows),
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "export_hash": self.export_hash}

    def to_json(self) -> str:
        return _bounded_export_json_bytes(self.to_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, export: Mapping[str, object]) -> InvestigativeOutcomeExport:
        required = {
            "schema",
            "as_of",
            "purpose",
            "source_head_hash",
            "policy",
            "rows",
            "export_hash",
        }
        if not isinstance(export, Mapping):
            raise OutcomeLedgerError("outcome export must be an object")
        if any(not isinstance(key, str) for key in export):
            raise OutcomeLedgerError("outcome export field names must be strings")
        if set(export) != required:
            raise OutcomeLedgerError("outcome export fields do not match v1")
        raw_purpose = export["purpose"]
        if not isinstance(raw_purpose, str):
            raise OutcomeLedgerError("outcome export purpose is invalid")
        try:
            purpose = OutcomeExportPurpose(raw_purpose)
        except ValueError as exc:
            raise OutcomeLedgerError("outcome export purpose is invalid") from exc
        rows = export["rows"]
        if not isinstance(rows, (tuple, list)):
            raise OutcomeLedgerError("outcome export rows must be an array")
        policy = export["policy"]
        if not isinstance(policy, Mapping):
            raise OutcomeLedgerError("outcome export policy must be an object")
        return cls(
            schema=export["schema"],  # type: ignore[arg-type]
            as_of=_parse_timestamp(export["as_of"], field="outcome export as_of"),
            purpose=purpose,
            source_head_hash=export["source_head_hash"],  # type: ignore[arg-type]
            rows=tuple(rows),  # type: ignore[arg-type]
            policy=policy,
            export_hash=export["export_hash"],  # type: ignore[arg-type]
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> InvestigativeOutcomeExport:
        if isinstance(payload, bytes):
            raw = payload
        elif isinstance(payload, str):
            try:
                raw = payload.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise OutcomeIntegrityError(
                    "outcome export is not Unicode scalar text"
                ) from exc
        else:
            raise TypeError("outcome export JSON must be text or bytes")
        if len(raw) > MAX_OUTCOME_EXPORT_BYTES:
            raise OutcomeIntegrityError(
                f"outcome export exceeds {MAX_OUTCOME_EXPORT_BYTES} bytes"
            )

        def reject_constant(value: str) -> object:
            raise OutcomeIntegrityError(f"non-finite JSON number {value!r}")

        def bounded_integer(value: str) -> int:
            digits = value[1:] if value.startswith("-") else value
            if len(digits) > 20:
                raise OutcomeIntegrityError("JSON integer exceeds 20 digits")
            return int(value)

        try:
            decoded = json.loads(
                raw,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=reject_constant,
                parse_int=bounded_integer,
            )
            _freeze_json(
                decoded,
                field="outcome export JSON",
                max_nodes=MAX_EXPORT_JSON_NODES,
            )
        except OutcomeIntegrityError:
            raise
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValueError,
            RecursionError,
        ) as exc:
            raise OutcomeIntegrityError(
                "outcome export is not valid bounded JSON"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise OutcomeIntegrityError("outcome export JSON root must be an object")
        try:
            result = cls.from_dict(decoded)
        except OutcomeIntegrityError:
            raise
        except (TypeError, ValueError, RecursionError) as exc:
            raise OutcomeIntegrityError(f"invalid outcome export: {exc}") from exc
        if not hmac.compare_digest(result.to_json().encode("utf-8"), raw):
            raise OutcomeIntegrityError("outcome export JSON is not canonical")
        return result


def _visible_prefix(
    chain: Sequence[OutcomeLedgerEntry], cutoff: datetime
) -> tuple[OutcomeLedgerEntry, ...]:
    count = 0
    for row in chain:
        if row.knowledge_time > cutoff or row.recorded_at > cutoff:
            break
        count += 1
    return tuple(chain[:count])


def build_investigative_outcome_export(
    entries: Iterable[OutcomeLedgerEntry],
    *,
    as_of: datetime,
    purpose: OutcomeExportPurpose | str,
    trusted_head_hash: str,
) -> InvestigativeOutcomeExport:
    """Seal the cutoff prefix after verifying the complete structural chain."""

    chain = _verify_chain_structure(entries, expected_head_hash=trusted_head_hash)
    cutoff = _utc(as_of, field="as_of")
    try:
        selected_purpose = OutcomeExportPurpose(purpose)
    except (TypeError, ValueError) as exc:
        raise OutcomeLedgerError(f"unsupported export purpose {purpose!r}") from exc
    prefix = _visible_prefix(chain, cutoff)
    state = _replay_semantics(prefix)

    eligible_value = {
        OutcomeExportPurpose.TRAINING: DatasetEligibility.TRAINING_ELIGIBLE.value,
        OutcomeExportPurpose.EVALUATION: DatasetEligibility.EVALUATION_ONLY.value,
    }[selected_purpose]
    export_rows: list[dict[str, object]] = []
    for forecast_hash, forecast in state.forecasts.items():
        decision = state.eligibility_decisions.get(forecast_hash)
        if (
            decision is None
            or decision.payload["dataset_eligibility"] != eligible_value
        ):
            continue
        resolution = state.resolutions.get(forecast_hash)
        if resolution is None:
            window_closed_at = _parse_timestamp(
                forecast.payload["window_closed_at"],
                field="forecast window_closed_at",
            )
            if cutoff < window_closed_at:
                status = OutcomeRowStatus.PENDING
                censor_reason: str | None = None
            else:
                status = OutcomeRowStatus.MATURED_UNRESOLVED
                censor_reason = "missing_resolution_record"
            outcome: bool | None = None
            label_eligible = False
            resolution_hash: str | None = None
            resolution_knowledge_time: str | None = None
            observation_hashes: list[str] = []
        else:
            disposition = ResolutionDisposition(str(resolution.payload["disposition"]))
            status = (
                OutcomeRowStatus.RESOLVED
                if disposition is ResolutionDisposition.RESOLVED
                else OutcomeRowStatus.CENSORED
            )
            outcome = resolution.payload["outcome"]  # type: ignore[assignment]
            label_eligible = status is OutcomeRowStatus.RESOLVED
            resolution_hash = resolution.record_hash
            resolution_knowledge_time = _timestamp(resolution.knowledge_time)
            observation_hashes = list(
                cast(
                    tuple[str, ...],
                    resolution.payload["observation_record_hashes"],
                )
            )
            censor_reason = resolution.payload["censor_reason"]  # type: ignore[assignment]
        export_rows.append(
            {
                "forecast_record_hash": forecast_hash,
                "eligibility_decision_record_hash": decision.record_hash,
                "rights_decision_hash": decision.payload["rights_decision_hash"],
                "reviewer_id": decision.payload["reviewer_id"],
                "eligibility_policy_id": decision.payload["policy_id"],
                "dataset_eligibility": decision.payload["dataset_eligibility"],
                "resolution_record_hash": resolution_hash,
                "market_id": forecast.market_id,
                "entity_group_id": forecast.entity_group_id,
                "forecast_identity_digest": _forecast_identity_digest(forecast),
                "case_group_digest": _case_group_digest(forecast),
                "model_id": forecast.payload["model_id"],
                "forecast_run_id": forecast.payload["forecast_run_id"],
                "target_rule_id": forecast.payload["target_rule_id"],
                "prediction_time": forecast.payload["prediction_time"],
                "forecast_knowledge_time": _timestamp(forecast.knowledge_time),
                "forecast_recorded_at": _timestamp(forecast.recorded_at),
                "window_opened_at": forecast.payload["window_opened_at"],
                "window_closed_at": forecast.payload["window_closed_at"],
                "observation_dates": list(
                    cast(tuple[str, ...], forecast.payload["observation_dates"])
                ),
                "calendar_id": forecast.payload["calendar_id"],
                "calendar_version": forecast.payload["calendar_version"],
                "calendar_digest": forecast.payload["calendar_digest"],
                "calendar_timezone": forecast.payload["calendar_timezone"],
                "probability_ppm": forecast.payload["probability_ppm"],
                "status": status.value,
                "label_eligible": label_eligible,
                "outcome": outcome,
                "censor_reason": censor_reason,
                "resolution_knowledge_time": resolution_knowledge_time,
                "observation_record_hashes": observation_hashes,
                "evidence_ids": list(
                    cast(tuple[str, ...], forecast.payload["evidence_ids"])
                ),
                "evidence_cut_digest": forecast.payload["evidence_cut_digest"],
            }
        )
    if len(export_rows) > MAX_OUTCOME_EXPORT_ROWS:
        raise OutcomeLedgerError(
            f"outcome export exceeds {MAX_OUTCOME_EXPORT_ROWS} rows"
        )
    export_rows.sort(
        key=lambda row: (
            str(row["prediction_time"]),
            str(row["market_id"]),
            str(row["model_id"]),
            str(row["forecast_record_hash"]),
        )
    )
    source_head = prefix[-1].record_hash if prefix else OUTCOME_GENESIS_HASH
    policy = _expected_export_policy(selected_purpose)
    content = {
        "schema": OUTCOME_EXPORT_SCHEMA,
        "as_of": _timestamp(cutoff),
        "purpose": selected_purpose.value,
        "source_head_hash": source_head,
        "policy": policy,
        "rows": export_rows,
    }
    return InvestigativeOutcomeExport(
        as_of=cutoff,
        purpose=selected_purpose,
        source_head_hash=source_head,
        rows=tuple(export_rows),
        policy=policy,
        export_hash=hashlib.sha256(_bounded_export_json_bytes(content)).hexdigest(),
    )


__all__ = [
    "DatasetEligibility",
    "InvestigativeOutcomeExport",
    "MAX_EVIDENCE_REFERENCES",
    "MAX_JSON_DEPTH",
    "MAX_JSON_NODES",
    "MAX_OBSERVATION_DATES",
    "MAX_OUTCOME_CHAIN_ENTRIES",
    "MAX_OUTCOME_EXPORT_BYTES",
    "MAX_OUTCOME_EXPORT_ROWS",
    "MAX_OUTCOME_RECORD_BYTES",
    "OUTCOME_EXPORT_SCHEMA",
    "OUTCOME_GENESIS_HASH",
    "OUTCOME_LEDGER_SCHEMA",
    "OutcomeExportPurpose",
    "OutcomeIntegrityError",
    "OutcomeLedgerEntry",
    "OutcomeLedgerError",
    "OutcomeRecordKind",
    "OutcomeRowStatus",
    "ResolutionDisposition",
    "build_investigative_outcome_export",
    "verify_outcome_chain",
]
