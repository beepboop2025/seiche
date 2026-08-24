"""Strict offline intake for Palimpsest's China-economic export.

The intake deliberately does not fetch Palimpsest or any upstream publisher.
An operator supplies an exact manifest, its exact JSONL artifact, and a small
Seiche acceptance receipt which binds a trusted ``accepted_at`` clock to those
bytes.  Only the reviewed ``world_bank_wdi`` source may contribute values.

This is a context contract, not a market-observation adapter.  Nothing in this
module creates :class:`seiche.domain.observation.Observation`, changes the
``CN-CNY`` pack, or makes a row eligible for a gauge or score.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from functools import lru_cache
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from seiche.china_economic_focus import featured_series
from seiche.nbs_trust import verify_trusted_palimpsest_china_signature

EXPORT_SCHEMA = "palimpsest.china-economic-export.v1"
LEGACY_MANIFEST_SCHEMA = "palimpsest.china-economic-export-manifest.v1"
REVIEW_MANIFEST_SCHEMA = "palimpsest.china-economic-export-manifest.v2"
MANIFEST_SCHEMA = "palimpsest.china-economic-export-manifest.v3"
PRODUCER_SCHEMA = "palimpsest.producer-receipt.v1"
POLICY_SCHEMA = "palimpsest.china-economic-source-policy.v1"
SERIES_REGISTRY_SCHEMA = "palimpsest-china-econ-wdi-series.v1"
AVAILABILITY_RECEIPT_SCHEMA = "palimpsest-china-econ-wdi-run.v3"
AVAILABILITY_SCHEMA = "palimpsest-china-econ-wdi-availability.v1"
ACCEPTANCE_SCHEMA = "seiche.palimpsest-china-economic-acceptance.v1"
ACCEPTANCE_DOMAIN = "seiche:palimpsest-china-economic-acceptance:v1"
CONTEXT_SCHEMA = "seiche.palimpsest-china-economic-context.v1"

ALLOWED_SOURCE_IDS = frozenset({"world_bank_wdi"})
MARKET_CHANNELS = ("capital_market", "money_market")
WDI_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
WDI_RIGHTS_EVIDENCE_URL = (
    "https://datacatalog.worldbank.org/search/dataset/0037712/"
    "world-development-indicators"
)
WDI_ATTRIBUTION = "World Bank, World Development Indicators"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_INPUT_LEDGER_BYTES = 128 * 1024 * 1024
MAX_AVAILABILITY_BYTES = 64 * 1024 * 1024
MAX_ACCEPTANCE_BYTES = 4096
MAX_SERIES_REGISTRY_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_SERIES = 60

# This process-local capability is deliberately not serializable and the
# dataclass field which carries it is not accepted by ``__init__``.  Parsing a
# valid Palimpsest manifest proves export integrity; only the signed Seiche
# acceptance path below may confer public-serving authority.
_OWNER_ATTESTED_AUTHORITY = object()

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_ED25519_SIGNATURE_RE = re.compile(r"[0-9a-f]{128}")
_SOURCE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_]{1,79}")
_SERIES_ID_RE = re.compile(r"cn\.wdi\.[a-z0-9][a-z0-9_]{1,119}")
_SOURCE_SERIES_ID_RE = re.compile(r"[A-Z0-9][A-Z0-9._-]{1,79}")
_FORBIDDEN_VALUE_SOURCE_MARKERS = ("cfets", "chinamoney", "china_money")

_MANIFEST_V1_KEYS = frozenset(
    {
        "schema_version",
        "generated_at",
        "context_only",
        "scoring_allowed",
        "artifact",
        "input_ledger",
        "policy",
        "series_registry",
        "source_decisions",
        "market_channel_mapping",
    }
)
_MANIFEST_V2_KEYS = frozenset({*_MANIFEST_V1_KEYS, "producer"})
_MANIFEST_KEYS = frozenset({*_MANIFEST_V2_KEYS, "availability_receipt"})
_PRODUCER_KEYS = frozenset(
    {"schema_version", "repository", "commit_sha", "workflow_run"}
)
_PRODUCER_WORKFLOW_KEYS = frozenset(
    {
        "provider",
        "workflow_file",
        "run_id",
        "run_attempt",
        "head_sha",
        "event",
        "conclusion",
        "url",
    }
)
_PRODUCER_REPOSITORY = "beepboop2025/palimpsest"
_PRODUCER_WORKFLOW_FILE = ".github/workflows/tests.yml"
_ARTIFACT_RECEIPT_KEYS = frozenset(
    {"schema_version", "path", "media_type", "sha256", "bytes", "records"}
)
_INPUT_LEDGER_KEYS = frozenset({"path", "sha256", "bytes", "records"})
_POLICY_RECEIPT_KEYS = frozenset({"path", "sha256", "schema_version", "evaluated_at"})
_SERIES_REGISTRY_RECEIPT_KEYS = frozenset({"path", "sha256", "bytes", "schema_version"})
_AVAILABILITY_RECEIPT_KEYS = frozenset(
    {
        "path",
        "sha256",
        "bytes",
        "schema_version",
        "generated_at",
        "batch_raw_sha256",
        "availability_schema_version",
        "current_numeric_identities_sha256",
        "current_numeric_identities_records",
        "current_projectable_series_sha256",
        "current_projectable_series_records",
        "current_projectable_source_indicators_sha256",
        "current_projectable_source_indicators_records",
        "withdrawn_numeric_identities_sha256",
        "withdrawn_numeric_identities_records",
    }
)
_RUN_RECEIPT_KEYS = frozenset(
    {
        "appended_observations",
        "availability",
        "batch_raw_sha256",
        "collector_artifact",
        "context_only",
        "dataset",
        "dataset_last_updated",
        "generated_at",
        "indicator_provenance",
        "ledger_after",
        "ledger_before",
        "ledger_coverage",
        "license",
        "license_url",
        "limitations",
        "publication_state",
        "redistribution_status",
        "response_coverage",
        "revision_lineage",
        "rights_evidence_url",
        "schema_version",
        "scoring_allowed",
        "source_id",
    }
)
_AVAILABILITY_KEYS = frozenset(
    {
        "coverage_semantics",
        "entries",
        "null_records",
        "records",
        "schema_version",
        "withdrawal_limitation",
        "withdrawal_state",
    }
)
_AVAILABILITY_ENTRY_KEYS = frozenset({"available", "footnote", "indicator_id", "year"})
_SOURCE_DECISION_KEYS = frozenset(
    {
        "source_id",
        "decision",
        "decision_sha256",
        "values_allowed",
        "seiche_export_allowed",
        "license",
        "license_url",
        "rights_evidence_url",
        "attribution",
        "reviewed_at",
        "expires_at",
        "reason",
        "input_records",
        "exported_records",
    }
)
_WRAPPER_KEYS = frozenset(
    {
        "schema_version",
        "context_only",
        "scoring_allowed",
        "market_channels",
        "observation",
    }
)
_OBSERVATION_KEYS = frozenset(
    {
        "series_id",
        "value",
        "unit",
        "frequency",
        "period_start",
        "period_end",
        "released_at",
        "collected_at",
        "source_id",
        "evidence_url",
        "revision",
        "status",
        "geography",
        "sector",
        "firm_size",
        "ownership",
        "quality",
        "raw_sha256",
        "metadata",
        "observation_id",
    }
)
_WDI_METADATA_KEYS = frozenset(
    {
        "family",
        "source_series_id",
        "source_document_version",
        "parser_version",
        "release_time_semantics",
        "aggregation_window",
    }
)
_ACCEPTANCE_CLAIM_KEYS = frozenset(
    {
        "schema_version",
        "algorithm",
        "domain",
        "accepted_at",
        "manifest_sha256",
        "artifact_sha256",
        "signer_key_id",
    }
)
_ACCEPTANCE_KEYS = frozenset({*_ACCEPTANCE_CLAIM_KEYS, "signature"})


class PalimpsestChinaIntakeError(ValueError):
    """The offline export or its Seiche acceptance receipt failed closed."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _freeze_json(value: Any) -> Any:
    """Recursively freeze validated JSON before placing it in the process cache."""

    if type(value) is dict:
        return MappingProxyType(
            {key: _freeze_json(child) for key, child in value.items()}
        )
    if type(value) is list:
        return tuple(_freeze_json(child) for child in value)
    return value


def _copy_json(value: Any) -> Any:
    """Return ordinary JSON containers from a recursively frozen value."""

    if isinstance(value, Mapping):
        return {key: _copy_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_copy_json(child) for child in value]
    return value


def _canonical_json_line(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PalimpsestChinaIntakeError("document is not canonical JSON data") from exc
    return encoded + b"\n"


def _strict_json(raw: bytes, *, label: str) -> Any:
    if type(raw) is not bytes or not raw:
        raise PalimpsestChinaIntakeError(f"{label} is empty")
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise PalimpsestChinaIntakeError(f"{label} is not strict UTF-8") from exc

    def reject_constant(value: str) -> None:
        raise PalimpsestChinaIntakeError(
            f"{label} contains non-finite JSON number {value}"
        )

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise PalimpsestChinaIntakeError(
                    f"{label} contains duplicate JSON key {key!r}"
                )
            out[key] = value
        return out

    try:
        return json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise PalimpsestChinaIntakeError(f"{label} is not valid JSON") from exc


def _exact_keys(
    value: object, expected: frozenset[str], *, label: str
) -> dict[str, Any]:
    if type(value) is not dict:
        raise PalimpsestChinaIntakeError(f"{label} must be an object")
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise PalimpsestChinaIntakeError(
            f"{label} keys changed (missing={missing}, unknown={unknown})"
        )
    return value


def _required_string(value: object, *, label: str, maximum: int = 8192) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise PalimpsestChinaIntakeError(f"{label} must be a bounded non-empty string")
    return value


def _sha(value: object, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise PalimpsestChinaIntakeError(f"{label} must be lowercase SHA-256")
    return value


def _count(value: object, *, label: str, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PalimpsestChinaIntakeError(f"{label} must be an integer >= {minimum}")
    return value


def _git_sha(value: object, *, label: str) -> str:
    if type(value) is not str or _GIT_SHA_RE.fullmatch(value) is None:
        raise PalimpsestChinaIntakeError(f"{label} must be a lowercase 40-hex Git SHA")
    return value


def _validate_producer(value: object) -> dict[str, Any]:
    """Validate the closed shape of Palimpsest's producer declaration.

    A null workflow run is deliberately valid only as offline review metadata.
    The signed acceptance boundary applies the stronger push-run requirement.
    These self-declared fields are not independent GitHub attestation; the
    offline owner must verify the run and bundle hashes before signing.
    """

    producer = _exact_keys(value, _PRODUCER_KEYS, label="manifest.producer")
    if producer["schema_version"] != PRODUCER_SCHEMA:
        raise PalimpsestChinaIntakeError(
            f"manifest.producer must use {PRODUCER_SCHEMA}"
        )
    if producer["repository"] != _PRODUCER_REPOSITORY:
        raise PalimpsestChinaIntakeError(
            "manifest.producer repository is not release-reviewed"
        )
    commit_sha = _git_sha(producer["commit_sha"], label="manifest.producer.commit_sha")
    workflow_value = producer["workflow_run"]
    if workflow_value is None:
        return dict(producer)

    workflow = _exact_keys(
        workflow_value,
        _PRODUCER_WORKFLOW_KEYS,
        label="manifest.producer.workflow_run",
    )
    if workflow["provider"] != "github_actions":
        raise PalimpsestChinaIntakeError(
            "manifest.producer.workflow_run provider must be github_actions"
        )
    if workflow["workflow_file"] != _PRODUCER_WORKFLOW_FILE:
        raise PalimpsestChinaIntakeError(
            "manifest.producer.workflow_run workflow is not release-reviewed"
        )
    run_id = _count(
        workflow["run_id"],
        label="manifest.producer.workflow_run.run_id",
        positive=True,
    )
    _count(
        workflow["run_attempt"],
        label="manifest.producer.workflow_run.run_attempt",
        positive=True,
    )
    head_sha = _git_sha(
        workflow["head_sha"], label="manifest.producer.workflow_run.head_sha"
    )
    if head_sha != commit_sha:
        raise PalimpsestChinaIntakeError(
            "manifest.producer workflow head SHA does not match producer commit"
        )
    if workflow["event"] not in {"pull_request", "push"}:
        raise PalimpsestChinaIntakeError(
            "manifest.producer.workflow_run event is not reviewed"
        )
    if workflow["conclusion"] != "success":
        raise PalimpsestChinaIntakeError(
            "manifest.producer.workflow_run conclusion must be success"
        )
    expected_url = f"https://github.com/{_PRODUCER_REPOSITORY}/actions/runs/{run_id}"
    if workflow["url"] != expected_url:
        raise PalimpsestChinaIntakeError(
            "manifest.producer.workflow_run URL is not canonical"
        )
    return {**producer, "workflow_run": dict(workflow)}


def _timestamp(value: object, *, label: str) -> datetime:
    text = _required_string(value, label=label, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PalimpsestChinaIntakeError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PalimpsestChinaIntakeError(f"{label} must be timezone-aware")
    if parsed.utcoffset().total_seconds() != 0:
        raise PalimpsestChinaIntakeError(f"{label} must use UTC")
    return parsed.astimezone(UTC)


def _canonical_timestamp(value: object, *, label: str) -> tuple[str, datetime]:
    parsed = _timestamp(value, label=label)
    normalized = parsed.isoformat().replace("+00:00", "Z")
    if value != normalized:
        raise PalimpsestChinaIntakeError(f"{label} must be canonical UTC")
    return normalized, parsed


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _date(value: object, *, label: str) -> date:
    text = _required_string(value, label=label, maximum=10)
    if len(text) != 10:
        raise PalimpsestChinaIntakeError(f"{label} must be an ISO date")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise PalimpsestChinaIntakeError(f"{label} must be an ISO date") from exc


def _https(value: object, *, label: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    text = _required_string(value, label=label)
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise PalimpsestChinaIntakeError(f"{label} must be an HTTPS URL") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise PalimpsestChinaIntakeError(f"{label} must be a credential-free HTTPS URL")
    return text


def _safe_relative_path(value: object, *, label: str, suffix: str | None = None) -> str:
    text = _required_string(value, label=label, maximum=512)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or path.as_posix() != text
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PalimpsestChinaIntakeError(f"{label} must be a safe relative path")
    if suffix is not None and not text.endswith(suffix):
        raise PalimpsestChinaIntakeError(f"{label} must end in {suffix}")
    return text


def _stat_fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _stable_read(
    path: str | Path,
    *,
    label: str,
    maximum: int,
    expected_identity: tuple[str, int, int, int, int, int] | None = None,
) -> bytes:
    selected = Path(path)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(selected, flags)
    except OSError as exc:
        raise PalimpsestChinaIntakeError(f"cannot safely open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PalimpsestChinaIntakeError(
                f"{label} must be a single-link regular file"
            )
        before_fingerprint = _stat_fingerprint(before)
        if (
            expected_identity is not None
            and before_fingerprint != expected_identity[1:]
        ):
            raise PalimpsestChinaIntakeError(
                f"{label} identity changed before it was read"
            )
        if before.st_size <= 0 or before.st_size > maximum:
            raise PalimpsestChinaIntakeError(
                f"{label} is empty or exceeds {maximum} bytes"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(payload) != before.st_size
            or len(payload) > maximum
            or before_fingerprint != _stat_fingerprint(after)
        ):
            raise PalimpsestChinaIntakeError(f"{label} changed while being read")
        return payload
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class PalimpsestChinaObservation:
    """One validated WDI context row plus its reviewed channel mapping."""

    record: Mapping[str, Any]
    market_channels: tuple[str, ...]

    @property
    def series_id(self) -> str:
        return str(self.record["series_id"])

    @property
    def released_at(self) -> datetime:
        return _timestamp(self.record["released_at"], label="observation.released_at")

    @property
    def collected_at(self) -> datetime:
        return _timestamp(self.record["collected_at"], label="observation.collected_at")

    @property
    def period_end(self) -> date:
        return _date(self.record["period_end"], label="observation.period_end")

    def public_record(self, *, accepted_at: str) -> dict[str, Any]:
        """Return the exact upstream row with Seiche's clock kept separate."""

        return {
            **_copy_json(self.record),
            "market_channels": list(self.market_channels),
            "accepted_at": accepted_at,
            "freshness": {
                "native_cadence": "annual",
                "classification": "structural",
                "state": "annual_structural",
                "is_live_market_data": False,
                "clock": "collected_at",
            },
        }


@dataclass(frozen=True, slots=True)
class PalimpsestChinaEconomicContext:
    """Verified context projection for one exact Palimpsest export."""

    accepted_at: str
    manifest_schema_version: str
    manifest_sha256: str
    producer: Mapping[str, Any] | None
    artifact_sha256: str
    artifact_bytes: int
    input_ledger_sha256: str
    availability_receipt_sha256: str | None
    availability_batch_raw_sha256: str | None
    current_numeric_identities_sha256: str | None
    current_projectable_series_sha256: str | None
    current_projectable_source_indicators_sha256: str | None
    withdrawn_numeric_identities_sha256: str | None
    policy_sha256: str
    series_registry_sha256: str
    acceptance_sha256: str | None
    acceptance_signer_key_id: str | None
    source_decision: Mapping[str, Any]
    observations: tuple[PalimpsestChinaObservation, ...]
    current_observations: tuple[PalimpsestChinaObservation, ...]
    _acceptance_authority: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    @property
    def owner_attested(self) -> bool:
        """Whether the trusted signed-acceptance loader authorized serving."""

        return self._acceptance_authority is _OWNER_ATTESTED_AUTHORITY

    def to_dict(self) -> dict[str, Any]:
        current = list(self.current_observations)
        channel_families: dict[str, Any] = {}
        for channel in MARKET_CHANNELS:
            available = [row for row in current if channel in row.market_channels]
            featured_ids = featured_series(
                channel, (row.series_id for row in available)
            )
            by_series = {row.series_id: row for row in available}
            rows = [
                by_series[series_id].public_record(accepted_at=self.accepted_at)
                for series_id in featured_ids
            ]
            channel_families[channel] = {
                "family": channel,
                "context_only": True,
                "scoring_eligible": False,
                "gauge_eligible": False,
                "native_cadence": "annual",
                "interpretation": "structural",
                "series_count": len(available),
                "returned_observation_count": len(rows),
                "observations_truncated": len(rows) < len(available),
                "featured_series": list(featured_ids),
                "observations": rows,
            }

        latest_release = max(row.released_at for row in self.observations)
        latest_collection = max(row.collected_at for row in self.observations)
        latest_period = max(row.period_end for row in self.observations)
        decision = self.source_decision
        return {
            "schema": CONTEXT_SCHEMA,
            "status": "structural",
            "evidence_status": "observed",
            "available": True,
            "context_only": True,
            "scoring_eligible": False,
            "cn_cny_gauge_eligible": False,
            "market_observation_eligible": False,
            "source_id": "world_bank_wdi",
            "source_registry_ids": ["world_bank_wdi"],
            "observation_count": len(self.observations),
            "current_series_count": len(current),
            "clocks": {
                "latest_observation_period_end": latest_period.isoformat(),
                "latest_source_released_at": latest_release.isoformat().replace(
                    "+00:00", "Z"
                ),
                "latest_palimpsest_collected_at": latest_collection.isoformat().replace(
                    "+00:00", "Z"
                ),
                "seiche_accepted_at": self.accepted_at,
            },
            "freshness": {
                "native_cadence": "annual",
                "classification": "structural",
                "state": "annual_structural",
                "is_live_market_data": False,
                "advances_world_observation_clocks": False,
                "advances_cn_cny_freshness": False,
            },
            "rights": {
                "decision": "allowed",
                "decision_sha256": decision["decision_sha256"],
                "license": decision["license"],
                "license_url": decision["license_url"],
                "rights_evidence_url": decision["rights_evidence_url"],
                "attribution": decision["attribution"],
                "reviewed_at": decision["reviewed_at"],
                "expires_at": decision["expires_at"],
            },
            "provenance": {
                "producer": (
                    _copy_json(self.producer) if self.producer is not None else None
                ),
                "manifest_sha256": self.manifest_sha256,
                "artifact_sha256": self.artifact_sha256,
                "artifact_bytes": self.artifact_bytes,
                "input_ledger_sha256": self.input_ledger_sha256,
                "availability_receipt_sha256": self.availability_receipt_sha256,
                "availability_batch_raw_sha256": self.availability_batch_raw_sha256,
                "current_numeric_identities_sha256": (
                    self.current_numeric_identities_sha256
                ),
                "current_projectable_series_sha256": (
                    self.current_projectable_series_sha256
                ),
                "current_projectable_source_indicators_sha256": (
                    self.current_projectable_source_indicators_sha256
                ),
                "withdrawn_numeric_identities_sha256": (
                    self.withdrawn_numeric_identities_sha256
                ),
                "policy_sha256": self.policy_sha256,
                "series_registry_sha256": self.series_registry_sha256,
                "acceptance_sha256": self.acceptance_sha256,
                "acceptance_signer_key_id": self.acceptance_signer_key_id,
                "owner_attestation": ("ed25519" if self.owner_attested else None),
            },
            "channel_families": channel_families,
            "boundaries": [
                "Annual WDI observations are structural context, not live China money-market data.",
                "Palimpsest release and collection clocks remain distinct from Seiche accepted_at.",
                "The export cannot enter a Seiche score, market pack, CN-CNY gauge, forecast, or trading signal.",
                "World Bank transport is not independent confirmation of every upstream statistical source.",
            ],
        }


def _validate_wdi_url(value: object, *, source_series_id: str) -> str:
    text = _https(value, label="observation.evidence_url")
    assert text is not None
    parsed = urlsplit(text)
    if parsed.hostname != "api.worldbank.org" or parsed.port not in {None, 443}:
        raise PalimpsestChinaIntakeError(
            "world_bank_wdi evidence_url must use the reviewed API host"
        )
    prefix = "/v2/country/CHN/indicator/"
    if not parsed.path.startswith(prefix):
        raise PalimpsestChinaIntakeError("world_bank_wdi evidence_url path changed")
    indicators = parsed.path[len(prefix) :].split(";")
    if source_series_id not in indicators:
        raise PalimpsestChinaIntakeError(
            "world_bank_wdi evidence_url does not bind the source series"
        )
    query = parse_qs(parsed.query, strict_parsing=True)
    if set(query) != {"source", "date", "format", "per_page", "footnote"}:
        raise PalimpsestChinaIntakeError("world_bank_wdi evidence_url query changed")
    if (
        query["source"] != ["2"]
        or query["format"] != ["json"]
        or query["per_page"] != ["20000"]
        or query["footnote"] != ["y"]
        or len(query["date"]) != 1
        or re.fullmatch(r"\d{4}:\d{4}", query["date"][0]) is None
    ):
        raise PalimpsestChinaIntakeError("world_bank_wdi evidence_url query changed")
    return text


def _validate_observation(
    value: object,
    *,
    channels: tuple[str, ...],
    accepted_at: datetime,
    position: int,
) -> PalimpsestChinaObservation:
    row = _exact_keys(value, _OBSERVATION_KEYS, label=f"observation {position}")
    series_id = row["series_id"]
    if type(series_id) is not str or _SERIES_ID_RE.fullmatch(series_id) is None:
        raise PalimpsestChinaIntakeError(
            f"observation {position} has a non-WDI series_id"
        )
    source_id = row["source_id"]
    if source_id not in ALLOWED_SOURCE_IDS:
        raise PalimpsestChinaIntakeError(
            f"observation {position} source {source_id!r} is not allowlisted"
        )
    if any(
        marker in str(source_id).lower() for marker in _FORBIDDEN_VALUE_SOURCE_MARKERS
    ):
        raise PalimpsestChinaIntakeError(
            f"observation {position} contains prohibited CFETS/ChinaMoney values"
        )

    if row["frequency"] != "A":
        raise PalimpsestChinaIntakeError("world_bank_wdi observations must be annual")
    if row["geography"] != "CN" or any(
        row[name] != "all" for name in ("sector", "firm_size", "ownership")
    ):
        raise PalimpsestChinaIntakeError(
            "world_bank_wdi observations must be aggregate China rows"
        )
    if row["status"] != "estimate":
        raise PalimpsestChinaIntakeError(
            "world_bank_wdi observations must retain estimate status"
        )
    for name in ("unit",):
        _required_string(row[name], label=f"observation.{name}", maximum=256)
    if isinstance(row["value"], bool) or not isinstance(row["value"], (int, float)):
        raise PalimpsestChinaIntakeError("observation.value must be numeric")
    if not math.isfinite(float(row["value"])):
        raise PalimpsestChinaIntakeError("observation.value must be finite")
    if isinstance(row["quality"], bool) or not isinstance(row["quality"], (int, float)):
        raise PalimpsestChinaIntakeError("observation.quality must be numeric")
    quality = float(row["quality"])
    if not math.isfinite(quality) or not 0 <= quality <= 1:
        raise PalimpsestChinaIntakeError("observation.quality must lie in [0, 1]")
    revision = _count(row["revision"], label="observation.revision")
    raw_sha256 = _sha(row["raw_sha256"], label="observation.raw_sha256")

    period_start = _date(row["period_start"], label="observation.period_start")
    period_end = _date(row["period_end"], label="observation.period_end")
    if period_start != date(period_start.year, 1, 1) or period_end != date(
        period_start.year, 12, 31
    ):
        raise PalimpsestChinaIntakeError(
            "world_bank_wdi periods must be complete calendar years"
        )
    released_at = _timestamp(row["released_at"], label="observation.released_at")
    collected_at = _timestamp(row["collected_at"], label="observation.collected_at")
    if released_at > collected_at:
        raise PalimpsestChinaIntakeError(
            "observation released_at cannot follow collected_at"
        )
    if collected_at > accepted_at:
        raise PalimpsestChinaIntakeError(
            "observation collected_at cannot follow Seiche accepted_at"
        )

    metadata = _exact_keys(
        row["metadata"], _WDI_METADATA_KEYS, label=f"observation {position}.metadata"
    )
    source_series_id = metadata["source_series_id"]
    if (
        type(source_series_id) is not str
        or _SOURCE_SERIES_ID_RE.fullmatch(source_series_id) is None
    ):
        raise PalimpsestChinaIntakeError("invalid WDI source_series_id")
    if metadata != {
        "family": "wdi_officially_recognized_sources",
        "source_series_id": source_series_id,
        "source_document_version": metadata["source_document_version"],
        "parser_version": "world-bank-wdi-json.v1",
        "release_time_semantics": "dataset_lastupdated_upper_bound",
        "aggregation_window": "calendar_year",
    }:
        raise PalimpsestChinaIntakeError("world_bank_wdi metadata contract changed")
    _date(
        metadata["source_document_version"],
        label="observation.metadata.source_document_version",
    )
    _validate_wdi_url(row["evidence_url"], source_series_id=source_series_id)

    supplied_id = _sha(row["observation_id"], label="observation.observation_id")
    canonical_record = dict(row)
    canonical_record.pop("observation_id")
    # Palimpsest's EconomicObservation normalizes all real values to float and
    # integral revisions to int before hashing its canonical payload.
    canonical_record["value"] = float(row["value"])
    canonical_record["quality"] = quality
    canonical_record["revision"] = revision
    canonical_record["raw_sha256"] = raw_sha256
    computed_id = hashlib.sha256(
        json.dumps(
            canonical_record,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if supplied_id != computed_id:
        raise PalimpsestChinaIntakeError(
            f"observation {position} observation_id does not match record contents"
        )
    normalized = dict(canonical_record)
    normalized["observation_id"] = supplied_id
    return PalimpsestChinaObservation(
        record=_freeze_json(normalized), market_channels=channels
    )


def _source_policy_decision_digest(row: Mapping[str, Any]) -> str:
    """Recompute Palimpsest's digest over the configured policy decision."""

    effective = row["decision"]
    if effective not in {"allowed", "denied"}:
        raise PalimpsestChinaIntakeError(
            "only currently effective policy decisions may be accepted"
        )
    payload = {
        "source_id": row["source_id"],
        "decision": "allow" if effective == "allowed" else "deny",
        "values_allowed": row["values_allowed"],
        "seiche_export_allowed": row["seiche_export_allowed"],
        "license": row["license"],
        "license_url": row["license_url"],
        "rights_evidence_url": row["rights_evidence_url"],
        "attribution": row["attribution"],
        "reviewed_at": row["reviewed_at"],
        "expires_at": row["expires_at"],
        "reason": row["reason"],
    }
    return _sha256(_canonical_json_line(payload))


def _validate_source_decisions(
    value: object,
    *,
    artifact_records: int,
    input_ledger_records: int,
    generated_at: datetime,
) -> Mapping[str, Any]:
    if type(value) is not list or not value or len(value) > 256:
        raise PalimpsestChinaIntakeError("source_decisions must be a bounded list")
    seen: set[str] = set()
    source_order: list[str] = []
    allowed: Mapping[str, Any] | None = None
    input_total = 0
    exported_total = 0
    for position, candidate in enumerate(value, 1):
        row = _exact_keys(
            candidate, _SOURCE_DECISION_KEYS, label=f"source decision {position}"
        )
        source_id = _required_string(
            row["source_id"], label=f"source decision {position}.source_id", maximum=128
        )
        if _SOURCE_ID_RE.fullmatch(source_id) is None:
            raise PalimpsestChinaIntakeError(
                f"source decision {position}.source_id is invalid"
            )
        if source_id in seen:
            raise PalimpsestChinaIntakeError(f"duplicate source decision {source_id}")
        seen.add(source_id)
        source_order.append(source_id)
        decision = row["decision"]
        if decision not in {"allowed", "denied", "expired", "unknown"}:
            raise PalimpsestChinaIntakeError(f"invalid source decision for {source_id}")
        if (
            type(row["values_allowed"]) is not bool
            or type(row["seiche_export_allowed"]) is not bool
        ):
            raise PalimpsestChinaIntakeError(
                f"source decision booleans are invalid for {source_id}"
            )
        inputs = _count(row["input_records"], label=f"{source_id}.input_records")
        exported = _count(
            row["exported_records"], label=f"{source_id}.exported_records"
        )
        if exported > inputs:
            raise PalimpsestChinaIntakeError(
                f"source decision {source_id} exports more records than it received"
            )
        input_total += inputs
        exported_total += exported
        _required_string(row["reason"], label=f"source decision {source_id}.reason")

        reviewed_at: datetime | None = None
        expires_at: datetime | None = None
        if decision == "unknown":
            if any(
                row[key] is not None
                for key in (
                    "decision_sha256",
                    "license",
                    "license_url",
                    "rights_evidence_url",
                    "attribution",
                    "reviewed_at",
                    "expires_at",
                )
            ):
                raise PalimpsestChinaIntakeError(
                    f"unknown source decision {source_id} invents policy evidence"
                )
        else:
            supplied_decision_sha = _sha(
                row["decision_sha256"], label=f"{source_id}.decision_sha256"
            )
            reviewed_at = _timestamp(
                row["reviewed_at"], label=f"source decision {source_id}.reviewed_at"
            )
            expires_at = _timestamp(
                row["expires_at"], label=f"source decision {source_id}.expires_at"
            )
            if reviewed_at > generated_at:
                raise PalimpsestChinaIntakeError(
                    f"source decision {source_id} was reviewed after export"
                )
            if decision in {"allowed", "denied"} and expires_at <= generated_at:
                raise PalimpsestChinaIntakeError(
                    f"source decision {source_id} has an incorrect effective state"
                )
            if decision == "expired" and expires_at > generated_at:
                raise PalimpsestChinaIntakeError(
                    f"source decision {source_id} has an incorrect effective state"
                )
            for key in ("license", "attribution"):
                if row[key] is not None:
                    _required_string(row[key], label=f"{source_id}.{key}")
            for key in ("license_url", "rights_evidence_url"):
                _https(row[key], label=f"{source_id}.{key}", nullable=True)
            if decision == "expired":
                raise PalimpsestChinaIntakeError(
                    f"source decision {source_id} is expired and must be re-reviewed"
                )
            if supplied_decision_sha != _source_policy_decision_digest(row):
                raise PalimpsestChinaIntakeError(
                    f"source decision {source_id} digest does not match its fields"
                )

        is_code_allowed = source_id in ALLOWED_SOURCE_IDS
        if is_code_allowed:
            if (
                decision != "allowed"
                or row["values_allowed"] is not True
                or row["seiche_export_allowed"] is not True
                or expires_at is None
                or expires_at <= generated_at
                or row["decision_sha256"] is None
                or row["license"] != "CC-BY-4.0"
                or row["license_url"] != WDI_LICENSE_URL
                or row["rights_evidence_url"] != WDI_RIGHTS_EVIDENCE_URL
                or row["attribution"] != WDI_ATTRIBUTION
                or exported != artifact_records
            ):
                raise PalimpsestChinaIntakeError(
                    "world_bank_wdi is not covered by the reviewed allow decision"
                )
            allowed = dict(row)
        elif (
            decision == "allowed"
            or row["values_allowed"] is not False
            or row["seiche_export_allowed"] is not False
            or exported != 0
        ):
            marker = (
                "CFETS/ChinaMoney"
                if any(
                    token in source_id.lower()
                    for token in _FORBIDDEN_VALUE_SOURCE_MARKERS
                )
                else "unknown source"
            )
            raise PalimpsestChinaIntakeError(f"{marker} is not export-allowlisted")
    if source_order != sorted(source_order):
        raise PalimpsestChinaIntakeError("source decisions must be uniquely sorted")
    required_boundaries = {"world_bank_wdi", "cfets_benchmarks", "chinamoney"}
    if not required_boundaries.issubset(seen):
        raise PalimpsestChinaIntakeError(
            "source decisions omit WDI or the explicit CFETS/ChinaMoney boundary"
        )
    if allowed is None:
        raise PalimpsestChinaIntakeError("world_bank_wdi allow decision is missing")
    if input_total != input_ledger_records:
        raise PalimpsestChinaIntakeError(
            "source decision input_records do not match input ledger records"
        )
    if exported_total != artifact_records:
        raise PalimpsestChinaIntakeError(
            "source decision exported_records do not match artifact records"
        )
    return allowed


@dataclass(frozen=True, slots=True)
class _LedgerState:
    identities: frozenset[tuple[str, int]]
    source_to_series: Mapping[str, str]
    record_sha256_by_observation_id: Mapping[str, str]
    latest_observation_id_by_identity: Mapping[tuple[str, int], str]
    latest_collected_at: datetime
    records: int


@dataclass(frozen=True, slots=True)
class _AvailabilityState:
    receipt_sha256: str
    batch_raw_sha256: str
    current_identities: frozenset[tuple[str, int]]
    projectable_source_indicators: frozenset[str]
    projectable_series: frozenset[str]
    expected_artifact_identities: frozenset[tuple[str, int]]
    current_numeric_identities_sha256: str
    current_projectable_series_sha256: str
    current_projectable_source_indicators_sha256: str
    withdrawn_numeric_identities_sha256: str


def _identity_digest(identities: set[tuple[str, int]]) -> str:
    body = b"".join(
        _canonical_json_line({"indicator_id": indicator_id, "year": year})
        for indicator_id, year in sorted(identities)
    )
    return _sha256(body)


def _source_indicator_digest(indicators: set[str]) -> str:
    body = b"".join(
        _canonical_json_line({"indicator_id": indicator_id})
        for indicator_id in sorted(indicators)
    )
    return _sha256(body)


def _series_digest(series_ids: set[str]) -> str:
    body = b"".join(
        _canonical_json_line({"series_id": series_id})
        for series_id in sorted(series_ids)
    )
    return _sha256(body)


def _validate_input_ledger(
    ledger_bytes: bytes,
    *,
    receipt: Mapping[str, Any],
    accepted_at: datetime,
) -> _LedgerState:
    if not ledger_bytes or len(ledger_bytes) > MAX_INPUT_LEDGER_BYTES:
        raise PalimpsestChinaIntakeError("input ledger is empty or too large")
    expected_sha = _sha(receipt["sha256"], label="input_ledger.sha256")
    expected_bytes = _count(receipt["bytes"], label="input_ledger.bytes", positive=True)
    expected_records = _count(
        receipt["records"], label="input_ledger.records", positive=True
    )
    if expected_sha != _sha256(ledger_bytes) or expected_bytes != len(ledger_bytes):
        raise PalimpsestChinaIntakeError(
            "input ledger hash/bytes commitment does not match"
        )
    if not ledger_bytes.endswith(b"\n") or b"\r" in ledger_bytes:
        raise PalimpsestChinaIntakeError(
            "input ledger must be canonical LF-terminated JSONL"
        )
    lines = ledger_bytes.splitlines(keepends=True)
    if len(lines) != expected_records or len(lines) > MAX_RECORDS:
        raise PalimpsestChinaIntakeError(
            "input ledger records commitment does not match"
        )

    identities: set[tuple[str, int]] = set()
    observation_ids: set[str] = set()
    source_to_series: dict[str, str] = {}
    series_to_source: dict[str, str] = {}
    record_sha256_by_observation_id: dict[str, str] = {}
    latest_by_identity: dict[
        tuple[str, int], tuple[tuple[int, datetime, datetime, str], str]
    ] = {}
    previous_global_collection: datetime | None = None
    previous_by_identity: dict[tuple[str, int], tuple[float, int, datetime]] = {}
    for position, line in enumerate(lines, 1):
        if line == b"\n" or not line.endswith(b"\n"):
            raise PalimpsestChinaIntakeError(
                f"input ledger line {position} is blank or unterminated"
            )
        value = _strict_json(line[:-1], label=f"input ledger line {position}")
        if _canonical_json_line(value) != line:
            raise PalimpsestChinaIntakeError(
                f"input ledger line {position} is not canonical JSON"
            )
        observation = _validate_observation(
            value,
            channels=(),
            accepted_at=accepted_at,
            position=position,
        )
        row = observation.record
        observation_id = str(row["observation_id"])
        if observation_id in observation_ids:
            raise PalimpsestChinaIntakeError(
                "input ledger observation_id values must be unique"
            )
        observation_ids.add(observation_id)
        collected_at = observation.collected_at
        if (
            previous_global_collection is not None
            and collected_at < previous_global_collection
        ):
            raise PalimpsestChinaIntakeError(
                "input ledger collected_at order moved backward"
            )
        previous_global_collection = collected_at

        source_series_id = str(row["metadata"]["source_series_id"])
        series_id = observation.series_id
        existing_series = source_to_series.setdefault(source_series_id, series_id)
        existing_source = series_to_source.setdefault(series_id, source_series_id)
        if existing_series != series_id or existing_source != source_series_id:
            raise PalimpsestChinaIntakeError(
                "input ledger source/internal series mapping is not one-to-one"
            )
        identity = (source_series_id, observation.period_end.year)
        identities.add(identity)
        numeric_value = float(row["value"])
        revision = int(row["revision"])
        released_at = observation.released_at
        previous = previous_by_identity.get(identity)
        if previous is None:
            if revision != 0:
                raise PalimpsestChinaIntakeError(
                    "input ledger first identity revision must be zero"
                )
        else:
            previous_value, previous_revision, previous_release = previous
            if released_at < previous_release:
                raise PalimpsestChinaIntakeError(
                    "input ledger release clock moved backward within an identity"
                )
            if numeric_value != previous_value:
                if revision != previous_revision + 1:
                    raise PalimpsestChinaIntakeError(
                        "input ledger value change did not increment revision"
                    )
            elif revision != previous_revision:
                raise PalimpsestChinaIntakeError(
                    "input ledger same-value provenance changed revision"
                )
        previous_by_identity[identity] = (numeric_value, revision, released_at)
        record_sha256_by_observation_id[observation_id] = _sha256(line)
        selection = (revision, released_at, collected_at, observation_id)
        selected = latest_by_identity.get(identity)
        if selected is None or selection > selected[0]:
            latest_by_identity[identity] = (selection, observation_id)

    assert previous_global_collection is not None
    return _LedgerState(
        identities=frozenset(identities),
        source_to_series=MappingProxyType(dict(source_to_series)),
        record_sha256_by_observation_id=MappingProxyType(
            record_sha256_by_observation_id
        ),
        latest_observation_id_by_identity=MappingProxyType(
            {identity: selected[1] for identity, selected in latest_by_identity.items()}
        ),
        latest_collected_at=previous_global_collection,
        records=len(lines),
    )


def _validate_availability(
    availability_bytes: bytes,
    *,
    receipt_value: object,
    manifest_generated_at: datetime,
    ledger: _LedgerState,
) -> _AvailabilityState:
    if not availability_bytes or len(availability_bytes) > MAX_AVAILABILITY_BYTES:
        raise PalimpsestChinaIntakeError("availability receipt is empty or too large")
    receipt = _exact_keys(
        receipt_value,
        _AVAILABILITY_RECEIPT_KEYS,
        label="manifest.availability_receipt",
    )
    path = _safe_relative_path(
        receipt["path"], label="availability_receipt.path", suffix=".json"
    )
    if PurePosixPath(path).name != "china-econ-wdi-latest.json":
        raise PalimpsestChinaIntakeError(
            "availability receipt path is not release-reviewed"
        )
    expected_sha = _sha(receipt["sha256"], label="availability_receipt.sha256")
    expected_bytes = _count(
        receipt["bytes"], label="availability_receipt.bytes", positive=True
    )
    if expected_sha != _sha256(availability_bytes) or expected_bytes != len(
        availability_bytes
    ):
        raise PalimpsestChinaIntakeError(
            "availability receipt hash/bytes commitment does not match"
        )
    if receipt["schema_version"] != AVAILABILITY_RECEIPT_SCHEMA:
        raise PalimpsestChinaIntakeError(
            f"availability receipt must use {AVAILABILITY_RECEIPT_SCHEMA}"
        )
    receipt_generated_text, receipt_generated_at = _canonical_timestamp(
        receipt["generated_at"], label="availability_receipt.generated_at"
    )
    if receipt_generated_at > manifest_generated_at:
        raise PalimpsestChinaIntakeError(
            "availability receipt was generated after the export manifest"
        )
    if ledger.latest_collected_at > receipt_generated_at:
        raise PalimpsestChinaIntakeError(
            "input ledger collection clock follows the availability receipt"
        )
    batch_raw_sha256 = _sha(
        receipt["batch_raw_sha256"], label="availability_receipt.batch_raw_sha256"
    )
    if receipt["availability_schema_version"] != AVAILABILITY_SCHEMA:
        raise PalimpsestChinaIntakeError(
            f"availability receipt must bind {AVAILABILITY_SCHEMA}"
        )

    run = _strict_json(availability_bytes, label="availability receipt")
    if _canonical_json_line(run) != availability_bytes:
        raise PalimpsestChinaIntakeError(
            "availability receipt must use exact canonical JSON bytes"
        )
    run = _exact_keys(run, _RUN_RECEIPT_KEYS, label="availability receipt")
    if (
        run["schema_version"] != AVAILABILITY_RECEIPT_SCHEMA
        or run["generated_at"] != receipt_generated_text
        or run["batch_raw_sha256"] != batch_raw_sha256
        or run["source_id"] != "world_bank_wdi"
        or run["context_only"] is not True
        or run["scoring_allowed"] is not False
        or run["license"] != "CC-BY-4.0"
        or run["license_url"] != WDI_LICENSE_URL
        or run["rights_evidence_url"] != WDI_RIGHTS_EVIDENCE_URL
    ):
        raise PalimpsestChinaIntakeError(
            "availability receipt source, rights, clocks, or safety fields changed"
        )
    _required_string(run["dataset"], label="availability receipt.dataset")
    _date(
        run["dataset_last_updated"],
        label="availability receipt.dataset_last_updated",
    )
    _count(
        run["appended_observations"],
        label="availability receipt.appended_observations",
    )
    ledger_before = _count(
        run["ledger_before"], label="availability receipt.ledger_before"
    )
    ledger_after = _count(
        run["ledger_after"], label="availability receipt.ledger_after"
    )
    if (
        ledger_after != ledger.records
        or ledger_after < ledger_before
        or run["appended_observations"] != ledger_after - ledger_before
    ):
        raise PalimpsestChinaIntakeError(
            "availability receipt ledger counts do not match the exact input ledger"
        )

    availability = _exact_keys(
        run["availability"], _AVAILABILITY_KEYS, label="availability"
    )
    if availability["schema_version"] != AVAILABILITY_SCHEMA:
        raise PalimpsestChinaIntakeError(f"availability must use {AVAILABILITY_SCHEMA}")
    for key in ("coverage_semantics", "withdrawal_limitation", "withdrawal_state"):
        _required_string(availability[key], label=f"availability.{key}")
    entries = availability["entries"]
    if type(entries) is not list or len(entries) > MAX_RECORDS:
        raise PalimpsestChinaIntakeError("availability entries must be a bounded list")
    if _count(availability["records"], label="availability.records") != len(entries):
        raise PalimpsestChinaIntakeError("availability records count does not match")

    current_identities: set[tuple[str, int]] = set()
    ordered_identities: list[tuple[str, int]] = []
    null_records = 0
    for position, candidate in enumerate(entries, 1):
        entry = _exact_keys(
            candidate,
            _AVAILABILITY_ENTRY_KEYS,
            label=f"availability entry {position}",
        )
        indicator_id = entry["indicator_id"]
        if (
            type(indicator_id) is not str
            or _SOURCE_SERIES_ID_RE.fullmatch(indicator_id) is None
        ):
            raise PalimpsestChinaIntakeError(
                f"availability entry {position} indicator_id is invalid"
            )
        year = entry["year"]
        if (
            isinstance(year, bool)
            or not isinstance(year, int)
            or not 1800 <= year <= 2200
        ):
            raise PalimpsestChinaIntakeError(
                f"availability entry {position} year is invalid"
            )
        if type(entry["available"]) is not bool:
            raise PalimpsestChinaIntakeError(
                f"availability entry {position} available must be boolean"
            )
        footnote = entry["footnote"]
        if footnote is not None:
            _required_string(
                footnote,
                label=f"availability entry {position}.footnote",
                maximum=8192,
            )
        identity = (indicator_id, year)
        if ordered_identities and identity <= ordered_identities[-1]:
            raise PalimpsestChinaIntakeError(
                "availability entries must be uniquely sorted"
            )
        ordered_identities.append(identity)
        if entry["available"]:
            current_identities.add(identity)
        else:
            null_records += 1
    if (
        _count(availability["null_records"], label="availability.null_records")
        != null_records
    ):
        raise PalimpsestChinaIntakeError(
            "availability null_records count does not match"
        )
    if not current_identities.issubset(ledger.identities):
        raise PalimpsestChinaIntakeError(
            "current numeric availability contains an identity absent from the ledger"
        )

    withdrawn = set(ledger.identities) - current_identities
    ledger_sources = set(ledger.source_to_series)
    withdrawn_sources = {indicator_id for indicator_id, _year in withdrawn}
    projectable_sources = ledger_sources - withdrawn_sources
    projectable_series = {
        ledger.source_to_series[source] for source in projectable_sources
    }
    expected_artifact_identities = {
        identity
        for identity in current_identities
        if identity[0] in projectable_sources
    }

    commitments: tuple[tuple[str, str, int, str], ...] = (
        (
            "current_numeric_identities",
            _identity_digest(current_identities),
            len(current_identities),
            "current numeric identities",
        ),
        (
            "current_projectable_series",
            _series_digest(projectable_series),
            len(projectable_series),
            "current projectable series",
        ),
        (
            "current_projectable_source_indicators",
            _source_indicator_digest(projectable_sources),
            len(projectable_sources),
            "current projectable source indicators",
        ),
        (
            "withdrawn_numeric_identities",
            _identity_digest(withdrawn),
            len(withdrawn),
            "withdrawn numeric identities",
        ),
    )
    computed: dict[str, str] = {}
    for prefix, digest, records, label in commitments:
        supplied_digest = _sha(
            receipt[f"{prefix}_sha256"],
            label=f"availability_receipt.{prefix}_sha256",
        )
        supplied_records = _count(
            receipt[f"{prefix}_records"],
            label=f"availability_receipt.{prefix}_records",
        )
        if supplied_digest != digest or supplied_records != records:
            raise PalimpsestChinaIntakeError(f"{label} commitment does not match")
        computed[prefix] = digest

    return _AvailabilityState(
        receipt_sha256=expected_sha,
        batch_raw_sha256=batch_raw_sha256,
        current_identities=frozenset(current_identities),
        projectable_source_indicators=frozenset(projectable_sources),
        projectable_series=frozenset(projectable_series),
        expected_artifact_identities=frozenset(expected_artifact_identities),
        current_numeric_identities_sha256=computed["current_numeric_identities"],
        current_projectable_series_sha256=computed["current_projectable_series"],
        current_projectable_source_indicators_sha256=computed[
            "current_projectable_source_indicators"
        ],
        withdrawn_numeric_identities_sha256=computed["withdrawn_numeric_identities"],
    )


def verify_export(
    manifest_bytes: bytes,
    artifact_bytes: bytes,
    *,
    input_ledger_bytes: bytes | None = None,
    availability_bytes: bytes | None = None,
    accepted_at: datetime,
) -> PalimpsestChinaEconomicContext:
    """Verify exact offline bytes and return a bounded context projection."""

    if (
        type(accepted_at) is not datetime
        or accepted_at.tzinfo is None
        or accepted_at.utcoffset() is None
    ):
        raise PalimpsestChinaIntakeError(
            "accepted_at must be a timezone-aware datetime"
        )
    accepted_at = accepted_at.astimezone(UTC)
    if not manifest_bytes or len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise PalimpsestChinaIntakeError("manifest is empty or too large")
    if not artifact_bytes or len(artifact_bytes) > MAX_ARTIFACT_BYTES:
        raise PalimpsestChinaIntakeError("artifact is empty or too large")

    manifest = _strict_json(manifest_bytes, label="manifest")
    if _canonical_json_line(manifest) != manifest_bytes:
        raise PalimpsestChinaIntakeError("manifest must use exact canonical JSON bytes")
    if type(manifest) is not dict:
        raise PalimpsestChinaIntakeError("manifest must be an object")
    manifest_schema = manifest.get("schema_version")
    if manifest_schema == MANIFEST_SCHEMA:
        manifest = _exact_keys(manifest, _MANIFEST_KEYS, label="manifest")
        producer = _validate_producer(manifest["producer"])
        if input_ledger_bytes is None or availability_bytes is None:
            raise PalimpsestChinaIntakeError(
                "manifest v3 requires exact input ledger and availability bytes"
            )
    elif manifest_schema == REVIEW_MANIFEST_SCHEMA:
        manifest = _exact_keys(manifest, _MANIFEST_V2_KEYS, label="manifest")
        producer = _validate_producer(manifest["producer"])
        if input_ledger_bytes is not None or availability_bytes is not None:
            raise PalimpsestChinaIntakeError(
                "review manifest v2 cannot accept v3 supplemental inputs"
            )
    elif manifest_schema == LEGACY_MANIFEST_SCHEMA:
        manifest = _exact_keys(manifest, _MANIFEST_V1_KEYS, label="manifest")
        producer = None
        if input_ledger_bytes is not None or availability_bytes is not None:
            raise PalimpsestChinaIntakeError(
                "legacy manifest v1 cannot accept v3 supplemental inputs"
            )
    else:
        raise PalimpsestChinaIntakeError(
            "manifest must use "
            f"{LEGACY_MANIFEST_SCHEMA}, {REVIEW_MANIFEST_SCHEMA}, or {MANIFEST_SCHEMA}"
        )
    if manifest["context_only"] is not True or manifest["scoring_allowed"] is not False:
        raise PalimpsestChinaIntakeError(
            "manifest must remain context-only and unscored"
        )
    if manifest_schema == MANIFEST_SCHEMA:
        _generated_text, generated_at = _canonical_timestamp(
            manifest["generated_at"], label="manifest.generated_at"
        )
    else:
        generated_at = _timestamp(
            manifest["generated_at"], label="manifest.generated_at"
        )
    if generated_at > accepted_at:
        raise PalimpsestChinaIntakeError(
            "manifest.generated_at follows Seiche accepted_at"
        )

    artifact = _exact_keys(
        manifest["artifact"], _ARTIFACT_RECEIPT_KEYS, label="manifest.artifact"
    )
    if artifact["schema_version"] != EXPORT_SCHEMA:
        raise PalimpsestChinaIntakeError(f"artifact must use {EXPORT_SCHEMA}")
    _safe_relative_path(artifact["path"], label="artifact.path", suffix=".jsonl")
    if artifact["media_type"] != "application/x-ndjson":
        raise PalimpsestChinaIntakeError(
            "artifact media_type must be application/x-ndjson"
        )
    artifact_sha256 = _sha(artifact["sha256"], label="artifact.sha256")
    artifact_size = _count(artifact["bytes"], label="artifact.bytes", positive=True)
    artifact_records = _count(
        artifact["records"], label="artifact.records", positive=True
    )
    if artifact_size != len(artifact_bytes) or artifact_sha256 != _sha256(
        artifact_bytes
    ):
        raise PalimpsestChinaIntakeError(
            "artifact hash/bytes commitment does not match"
        )
    if artifact_records > MAX_RECORDS:
        raise PalimpsestChinaIntakeError(f"artifact exceeds {MAX_RECORDS} records")

    input_ledger = _exact_keys(
        manifest["input_ledger"], _INPUT_LEDGER_KEYS, label="manifest.input_ledger"
    )
    _safe_relative_path(
        input_ledger["path"], label="input_ledger.path", suffix=".jsonl"
    )
    input_ledger_sha256 = _sha(input_ledger["sha256"], label="input_ledger.sha256")
    _count(input_ledger["bytes"], label="input_ledger.bytes", positive=True)
    input_records = _count(
        input_ledger["records"], label="input_ledger.records", positive=True
    )
    if input_records < artifact_records:
        raise PalimpsestChinaIntakeError(
            "input ledger cannot contain fewer records than the export"
        )
    ledger_state = (
        _validate_input_ledger(
            input_ledger_bytes,
            receipt=input_ledger,
            accepted_at=accepted_at,
        )
        if input_ledger_bytes is not None
        else None
    )

    policy = _exact_keys(
        manifest["policy"], _POLICY_RECEIPT_KEYS, label="manifest.policy"
    )
    if policy["schema_version"] != POLICY_SCHEMA:
        raise PalimpsestChinaIntakeError(f"policy must use {POLICY_SCHEMA}")
    if policy["path"] != "china_econ_source_policy.json":
        raise PalimpsestChinaIntakeError("policy path is not release-reviewed")
    policy_sha256 = _sha(policy["sha256"], label="policy.sha256")
    evaluated_at = _timestamp(policy["evaluated_at"], label="policy.evaluated_at")
    if evaluated_at != generated_at:
        raise PalimpsestChinaIntakeError(
            "policy evaluation clock must equal manifest generation"
        )

    series_registry = _exact_keys(
        manifest["series_registry"],
        _SERIES_REGISTRY_RECEIPT_KEYS,
        label="manifest.series_registry",
    )
    if series_registry["schema_version"] != SERIES_REGISTRY_SCHEMA:
        raise PalimpsestChinaIntakeError(
            f"series registry must use {SERIES_REGISTRY_SCHEMA}"
        )
    if series_registry["path"] != "china_econ_wdi_series.json":
        raise PalimpsestChinaIntakeError("series registry path is not release-reviewed")
    series_registry_sha256 = _sha(
        series_registry["sha256"], label="series_registry.sha256"
    )
    series_registry_size = _count(
        series_registry["bytes"], label="series_registry.bytes", positive=True
    )
    if series_registry_size > MAX_SERIES_REGISTRY_BYTES:
        raise PalimpsestChinaIntakeError("series registry receipt is too large")

    availability_state = (
        _validate_availability(
            availability_bytes,
            receipt_value=manifest["availability_receipt"],
            manifest_generated_at=generated_at,
            ledger=ledger_state,
        )
        if availability_bytes is not None and ledger_state is not None
        else None
    )

    source_decision = _validate_source_decisions(
        manifest["source_decisions"],
        artifact_records=artifact_records,
        input_ledger_records=input_records,
        generated_at=generated_at,
    )
    decision_expiry = _timestamp(
        source_decision["expires_at"], label="world_bank_wdi.expires_at"
    )
    if decision_expiry <= accepted_at:
        raise PalimpsestChinaIntakeError(
            "world_bank_wdi rights decision is expired at Seiche acceptance"
        )

    mapping = _exact_keys(
        manifest["market_channel_mapping"],
        frozenset(MARKET_CHANNELS),
        label="manifest.market_channel_mapping",
    )
    declared_mapping: dict[str, list[str]] = {}
    for channel in MARKET_CHANNELS:
        values = mapping[channel]
        if (
            type(values) is not list
            or not values
            or len(values) > MAX_SERIES
            or any(
                type(item) is not str or _SERIES_ID_RE.fullmatch(item) is None
                for item in values
            )
            or values != sorted(set(values))
        ):
            raise PalimpsestChinaIntakeError(
                f"market_channel_mapping.{channel} must be sorted unique WDI series"
            )
        declared_mapping[channel] = values

    if not artifact_bytes.endswith(b"\n") or b"\r" in artifact_bytes:
        raise PalimpsestChinaIntakeError(
            "artifact must be canonical LF-terminated JSONL"
        )
    lines = artifact_bytes.splitlines(keepends=True)
    if len(lines) != artifact_records:
        raise PalimpsestChinaIntakeError("artifact records commitment does not match")

    observations: list[PalimpsestChinaObservation] = []
    seen_ids: set[str] = set()
    derived_mapping = {channel: set() for channel in MARKET_CHANNELS}
    series_contracts: dict[str, tuple[object, ...]] = {}
    artifact_identities: set[tuple[str, int]] = set()
    artifact_source_indicators: set[str] = set()
    artifact_series_ids: set[str] = set()
    artifact_source_to_series: dict[str, str] = {}
    artifact_series_to_source: dict[str, str] = {}
    seen_artifact_identities: set[tuple[str, int]] = set()
    for position, line in enumerate(lines, 1):
        if not line.endswith(b"\n") or line == b"\n":
            raise PalimpsestChinaIntakeError(
                f"artifact line {position} is blank or unterminated"
            )
        wrapper = _strict_json(line[:-1], label=f"artifact line {position}")
        if _canonical_json_line(wrapper) != line:
            raise PalimpsestChinaIntakeError(
                f"artifact line {position} is not canonical JSON"
            )
        wrapper = _exact_keys(wrapper, _WRAPPER_KEYS, label=f"artifact line {position}")
        if wrapper["schema_version"] != EXPORT_SCHEMA:
            raise PalimpsestChinaIntakeError(
                f"artifact line {position} must use {EXPORT_SCHEMA}"
            )
        if (
            wrapper["context_only"] is not True
            or wrapper["scoring_allowed"] is not False
        ):
            raise PalimpsestChinaIntakeError(
                f"artifact line {position} attempts scoring or non-context use"
            )
        channel_value = wrapper["market_channels"]
        if (
            type(channel_value) is not list
            or not channel_value
            or channel_value != sorted(set(channel_value))
            or any(channel not in MARKET_CHANNELS for channel in channel_value)
        ):
            raise PalimpsestChinaIntakeError(
                f"artifact line {position} has invalid market_channels"
            )
        channels = tuple(channel_value)
        observation = _validate_observation(
            wrapper["observation"],
            channels=channels,
            accepted_at=accepted_at,
            position=position,
        )
        observation_id = str(observation.record["observation_id"])
        if observation_id in seen_ids:
            raise PalimpsestChinaIntakeError(
                f"artifact duplicates observation_id {observation_id}"
            )
        seen_ids.add(observation_id)
        source_series_id = str(observation.record["metadata"]["source_series_id"])
        identity = (source_series_id, observation.period_end.year)
        if identity in seen_artifact_identities:
            raise PalimpsestChinaIntakeError(
                "artifact must contain exactly one latest row per identity"
            )
        seen_artifact_identities.add(identity)
        artifact_identities.add(identity)
        artifact_source_indicators.add(source_series_id)
        artifact_series_ids.add(observation.series_id)
        mapped_series = artifact_source_to_series.setdefault(
            source_series_id, observation.series_id
        )
        mapped_source = artifact_series_to_source.setdefault(
            observation.series_id, source_series_id
        )
        if mapped_series != observation.series_id or mapped_source != source_series_id:
            raise PalimpsestChinaIntakeError(
                "artifact source/internal series mapping is not one-to-one"
            )
        if ledger_state is not None:
            ledger_record_sha256 = ledger_state.record_sha256_by_observation_id.get(
                observation_id
            )
            if (
                ledger_record_sha256
                != _sha256(_canonical_json_line(wrapper["observation"]))
                or ledger_state.source_to_series.get(source_series_id)
                != observation.series_id
            ):
                raise PalimpsestChinaIntakeError(
                    "artifact observation is not an exact input-ledger row"
                )
            if (
                ledger_state.latest_observation_id_by_identity.get(identity)
                != observation_id
            ):
                raise PalimpsestChinaIntakeError(
                    "artifact observation is not the latest input-ledger row"
                )
        for channel in channels:
            derived_mapping[channel].add(observation.series_id)
        contract = (
            observation.record["unit"],
            observation.record["frequency"],
            observation.record["geography"],
            observation.record["sector"],
            observation.record["firm_size"],
            observation.record["ownership"],
            observation.record["metadata"]["source_series_id"],
            channels,
        )
        prior_contract = series_contracts.setdefault(observation.series_id, contract)
        if prior_contract != contract:
            raise PalimpsestChinaIntakeError(
                f"series contract drift for {observation.series_id}"
            )
        observations.append(observation)

    for channel in MARKET_CHANNELS:
        if declared_mapping[channel] != sorted(derived_mapping[channel]):
            raise PalimpsestChinaIntakeError(
                f"market_channel_mapping.{channel} does not match artifact rows"
            )
    if len(series_contracts) > MAX_SERIES:
        raise PalimpsestChinaIntakeError(f"artifact exceeds {MAX_SERIES} series")

    if availability_state is not None:
        if artifact_identities != set(availability_state.expected_artifact_identities):
            raise PalimpsestChinaIntakeError(
                "artifact identities do not match current projectable availability"
            )
        if artifact_source_indicators != set(
            availability_state.projectable_source_indicators
        ):
            raise PalimpsestChinaIntakeError(
                "artifact source indicators do not match current projectable availability"
            )
        if artifact_series_ids != set(availability_state.projectable_series):
            raise PalimpsestChinaIntakeError(
                "artifact series do not match current projectable availability"
            )

    latest_identity: dict[tuple[str, int], PalimpsestChinaObservation] = {}
    for row in observations:
        identity = (
            str(row.record["metadata"]["source_series_id"]),
            row.period_end.year,
        )
        prior = latest_identity.get(identity)
        if prior is None or (
            int(row.record["revision"]),
            row.released_at,
            row.collected_at,
            str(row.record["observation_id"]),
        ) > (
            int(prior.record["revision"]),
            prior.released_at,
            prior.collected_at,
            str(prior.record["observation_id"]),
        ):
            latest_identity[identity] = row

    latest: dict[str, PalimpsestChinaObservation] = {}
    for row in latest_identity.values():
        prior = latest.get(row.series_id)
        if prior is None or (
            row.period_end,
            int(row.record["revision"]),
            row.released_at,
            row.collected_at,
            str(row.record["observation_id"]),
        ) > (
            prior.period_end,
            int(prior.record["revision"]),
            prior.released_at,
            prior.collected_at,
            str(prior.record["observation_id"]),
        ):
            latest[row.series_id] = row

    accepted_text = accepted_at.isoformat().replace("+00:00", "Z")
    return PalimpsestChinaEconomicContext(
        accepted_at=accepted_text,
        manifest_schema_version=manifest_schema,
        manifest_sha256=_sha256(manifest_bytes),
        producer=_freeze_json(producer) if producer is not None else None,
        artifact_sha256=artifact_sha256,
        artifact_bytes=artifact_size,
        input_ledger_sha256=input_ledger_sha256,
        availability_receipt_sha256=(
            availability_state.receipt_sha256
            if availability_state is not None
            else None
        ),
        availability_batch_raw_sha256=(
            availability_state.batch_raw_sha256
            if availability_state is not None
            else None
        ),
        current_numeric_identities_sha256=(
            availability_state.current_numeric_identities_sha256
            if availability_state is not None
            else None
        ),
        current_projectable_series_sha256=(
            availability_state.current_projectable_series_sha256
            if availability_state is not None
            else None
        ),
        current_projectable_source_indicators_sha256=(
            availability_state.current_projectable_source_indicators_sha256
            if availability_state is not None
            else None
        ),
        withdrawn_numeric_identities_sha256=(
            availability_state.withdrawn_numeric_identities_sha256
            if availability_state is not None
            else None
        ),
        policy_sha256=policy_sha256,
        series_registry_sha256=series_registry_sha256,
        acceptance_sha256=None,
        acceptance_signer_key_id=None,
        source_decision=_freeze_json(dict(source_decision)),
        observations=tuple(observations),
        current_observations=tuple(latest[key] for key in sorted(latest)),
    )


def _require_authoritative_producer(
    context: PalimpsestChinaEconomicContext,
) -> None:
    """Require a final-main producer declaration before owner acceptance."""

    producer = context.producer
    workflow = producer.get("workflow_run") if isinstance(producer, Mapping) else None
    if (
        context.manifest_schema_version != MANIFEST_SCHEMA
        or not isinstance(workflow, Mapping)
        or workflow.get("event") != "push"
        or workflow.get("conclusion") != "success"
        or workflow.get("head_sha") != producer.get("commit_sha")
    ):
        raise PalimpsestChinaIntakeError(
            "Palimpsest producer declaration must identify a successful "
            "exact-commit push run"
        )


def build_acceptance_claim(
    manifest_bytes: bytes,
    artifact_bytes: bytes,
    *,
    input_ledger_bytes: bytes | None = None,
    availability_bytes: bytes | None = None,
    accepted_at: datetime,
    signer_key_id: str,
) -> dict[str, Any]:
    """Build the domain-separated claim a Seiche operator signs offline."""

    if (
        type(accepted_at) is not datetime
        or accepted_at.tzinfo is None
        or accepted_at.utcoffset() is None
    ):
        raise PalimpsestChinaIntakeError(
            "accepted_at must be a timezone-aware datetime"
        )
    if accepted_at.astimezone(UTC) > _utc_now():
        raise PalimpsestChinaIntakeError("accepted_at cannot be in the future")
    context = verify_export(
        manifest_bytes,
        artifact_bytes,
        input_ledger_bytes=input_ledger_bytes,
        availability_bytes=availability_bytes,
        accepted_at=accepted_at,
    )
    _require_authoritative_producer(context)
    signer = _sha(signer_key_id, label="acceptance.signer_key_id")
    return {
        "schema_version": ACCEPTANCE_SCHEMA,
        "algorithm": "ed25519",
        "domain": ACCEPTANCE_DOMAIN,
        "accepted_at": context.accepted_at,
        "manifest_sha256": context.manifest_sha256,
        "artifact_sha256": context.artifact_sha256,
        "signer_key_id": signer,
    }


def build_acceptance_claim_from_files(
    manifest_path: str | Path,
    artifact_path: str | Path,
    *,
    input_ledger_path: str | Path | None = None,
    availability_path: str | Path | None = None,
    accepted_at: datetime,
    signer_key_id: str,
) -> dict[str, Any]:
    """Safely read exact export bytes and build their offline signing claim."""

    manifest_bytes = _stable_read(
        manifest_path, label="Palimpsest China manifest", maximum=MAX_MANIFEST_BYTES
    )
    artifact_bytes = _stable_read(
        artifact_path, label="Palimpsest China artifact", maximum=MAX_ARTIFACT_BYTES
    )
    input_ledger_bytes = (
        _stable_read(
            input_ledger_path,
            label="Palimpsest China input ledger",
            maximum=MAX_INPUT_LEDGER_BYTES,
        )
        if input_ledger_path is not None
        else None
    )
    availability_bytes = (
        _stable_read(
            availability_path,
            label="Palimpsest China availability receipt",
            maximum=MAX_AVAILABILITY_BYTES,
        )
        if availability_path is not None
        else None
    )
    return build_acceptance_claim(
        manifest_bytes,
        artifact_bytes,
        input_ledger_bytes=input_ledger_bytes,
        availability_bytes=availability_bytes,
        accepted_at=accepted_at,
        signer_key_id=signer_key_id,
    )


def encode_acceptance_claim(claim: Mapping[str, Any]) -> bytes:
    """Return the exact canonical bytes covered by the detached signature."""

    row = _exact_keys(claim, _ACCEPTANCE_CLAIM_KEYS, label="acceptance claim")
    if (
        row["schema_version"] != ACCEPTANCE_SCHEMA
        or row["algorithm"] != "ed25519"
        or row["domain"] != ACCEPTANCE_DOMAIN
    ):
        raise PalimpsestChinaIntakeError("acceptance claim domain changed")
    _canonical_timestamp(row["accepted_at"], label="acceptance.accepted_at")
    _sha(row["manifest_sha256"], label="acceptance.manifest_sha256")
    _sha(row["artifact_sha256"], label="acceptance.artifact_sha256")
    _sha(row["signer_key_id"], label="acceptance.signer_key_id")
    return _canonical_json_line(dict(row))


def build_acceptance_receipt(
    manifest_bytes: bytes,
    artifact_bytes: bytes,
    *,
    input_ledger_bytes: bytes | None = None,
    availability_bytes: bytes | None = None,
    accepted_at: datetime,
    signer_key_id: str,
    signature: str,
    attest_dir: str | Path | None = None,
) -> bytes:
    """Assemble a trusted canonical receipt around an offline signature."""

    claim = build_acceptance_claim(
        manifest_bytes,
        artifact_bytes,
        input_ledger_bytes=input_ledger_bytes,
        availability_bytes=availability_bytes,
        accepted_at=accepted_at,
        signer_key_id=signer_key_id,
    )
    if type(signature) is not str or _ED25519_SIGNATURE_RE.fullmatch(signature) is None:
        raise PalimpsestChinaIntakeError("acceptance signature is malformed")
    try:
        verify_trusted_palimpsest_china_signature(
            encode_acceptance_claim(claim),
            signature,
            signer_key_id,
            attest_dir=attest_dir,
        )
    except ValueError as exc:
        raise PalimpsestChinaIntakeError(
            "acceptance signature is not trusted and valid"
        ) from exc
    return _canonical_json_line({**claim, "signature": signature})


def build_acceptance_receipt_from_files(
    manifest_path: str | Path,
    artifact_path: str | Path,
    *,
    input_ledger_path: str | Path | None = None,
    availability_path: str | Path | None = None,
    accepted_at: datetime,
    signer_key_id: str,
    signature: str,
    attest_dir: str | Path | None = None,
) -> bytes:
    """Safely re-read the signed inputs and assemble their trusted receipt."""

    manifest_bytes = _stable_read(
        manifest_path, label="Palimpsest China manifest", maximum=MAX_MANIFEST_BYTES
    )
    artifact_bytes = _stable_read(
        artifact_path, label="Palimpsest China artifact", maximum=MAX_ARTIFACT_BYTES
    )
    input_ledger_bytes = (
        _stable_read(
            input_ledger_path,
            label="Palimpsest China input ledger",
            maximum=MAX_INPUT_LEDGER_BYTES,
        )
        if input_ledger_path is not None
        else None
    )
    availability_bytes = (
        _stable_read(
            availability_path,
            label="Palimpsest China availability receipt",
            maximum=MAX_AVAILABILITY_BYTES,
        )
        if availability_path is not None
        else None
    )
    return build_acceptance_receipt(
        manifest_bytes,
        artifact_bytes,
        input_ledger_bytes=input_ledger_bytes,
        availability_bytes=availability_bytes,
        accepted_at=accepted_at,
        signer_key_id=signer_key_id,
        signature=signature,
        attest_dir=attest_dir,
    )


def _file_identity(
    path: str | Path, *, label: str
) -> tuple[str, int, int, int, int, int]:
    selected = os.path.abspath(os.fspath(path))
    try:
        metadata = os.stat(selected, follow_symlinks=False)
    except OSError as exc:
        raise PalimpsestChinaIntakeError(f"cannot inspect {label}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise PalimpsestChinaIntakeError(f"{label} must be a single-link regular file")
    return (selected, *_stat_fingerprint(metadata))


@lru_cache(maxsize=8)
def _load_accepted_export_cached(
    manifest_identity: tuple[str, int, int, int, int, int],
    artifact_identity: tuple[str, int, int, int, int, int],
    input_ledger_identity: tuple[str, int, int, int, int, int] | None,
    availability_identity: tuple[str, int, int, int, int, int] | None,
    acceptance_identity: tuple[str, int, int, int, int, int],
    attest_dir: str | None,
    trust_identity: tuple[str, int, int, int, int, int] | None,
) -> PalimpsestChinaEconomicContext:
    """Verify one immutable input set once; identities form the cache key."""

    del trust_identity  # cache-key commitment; signature verification reads it below
    manifest_path, artifact_path, acceptance_path = (
        manifest_identity[0],
        artifact_identity[0],
        acceptance_identity[0],
    )
    manifest_bytes = _stable_read(
        manifest_path,
        label="Palimpsest China manifest",
        maximum=MAX_MANIFEST_BYTES,
        expected_identity=manifest_identity,
    )
    artifact_bytes = _stable_read(
        artifact_path,
        label="Palimpsest China artifact",
        maximum=MAX_ARTIFACT_BYTES,
        expected_identity=artifact_identity,
    )
    input_ledger_bytes = (
        _stable_read(
            input_ledger_identity[0],
            label="Palimpsest China input ledger",
            maximum=MAX_INPUT_LEDGER_BYTES,
            expected_identity=input_ledger_identity,
        )
        if input_ledger_identity is not None
        else None
    )
    availability_bytes = (
        _stable_read(
            availability_identity[0],
            label="Palimpsest China availability receipt",
            maximum=MAX_AVAILABILITY_BYTES,
            expected_identity=availability_identity,
        )
        if availability_identity is not None
        else None
    )
    acceptance_bytes = _stable_read(
        acceptance_path,
        label="Palimpsest China acceptance receipt",
        maximum=MAX_ACCEPTANCE_BYTES,
        expected_identity=acceptance_identity,
    )
    receipt = _strict_json(acceptance_bytes, label="acceptance receipt")
    if _canonical_json_line(receipt) != acceptance_bytes:
        raise PalimpsestChinaIntakeError(
            "acceptance receipt must use exact canonical JSON bytes"
        )
    receipt = _exact_keys(receipt, _ACCEPTANCE_KEYS, label="acceptance receipt")
    signature = receipt["signature"]
    if type(signature) is not str or _ED25519_SIGNATURE_RE.fullmatch(signature) is None:
        raise PalimpsestChinaIntakeError("acceptance signature is malformed")
    claim = {key: receipt[key] for key in _ACCEPTANCE_CLAIM_KEYS}
    claim_bytes = encode_acceptance_claim(claim)
    if claim["manifest_sha256"] != _sha256(manifest_bytes):
        raise PalimpsestChinaIntakeError(
            "acceptance receipt manifest hash does not match"
        )
    if claim["artifact_sha256"] != _sha256(artifact_bytes):
        raise PalimpsestChinaIntakeError(
            "acceptance receipt artifact hash does not match"
        )
    try:
        verify_trusted_palimpsest_china_signature(
            claim_bytes,
            signature,
            claim["signer_key_id"],
            attest_dir=attest_dir,
        )
    except ValueError as exc:
        raise PalimpsestChinaIntakeError(
            "acceptance signature is not trusted and valid"
        ) from exc
    accepted_at_text, accepted_at = _canonical_timestamp(
        claim["accepted_at"], label="acceptance.accepted_at"
    )
    context = verify_export(
        manifest_bytes,
        artifact_bytes,
        input_ledger_bytes=input_ledger_bytes,
        availability_bytes=availability_bytes,
        accepted_at=accepted_at,
    )
    _require_authoritative_producer(context)
    if context.accepted_at != accepted_at_text:
        raise PalimpsestChinaIntakeError("acceptance clock normalization changed")
    context = replace(
        context,
        acceptance_sha256=_sha256(acceptance_bytes),
        acceptance_signer_key_id=claim["signer_key_id"],
    )
    object.__setattr__(
        context,
        "_acceptance_authority",
        _OWNER_ATTESTED_AUTHORITY,
    )
    return context


def clear_accepted_export_cache() -> None:
    """Clear the bounded process cache after an operator-controlled rotation."""

    _load_accepted_export_cached.cache_clear()


def load_accepted_export(
    manifest_path: str | Path,
    artifact_path: str | Path,
    acceptance_path: str | Path,
    *,
    input_ledger_path: str | Path | None = None,
    availability_path: str | Path | None = None,
    attest_dir: str | Path | None = None,
    now: datetime | None = None,
) -> PalimpsestChinaEconomicContext:
    """Load a signed local export and re-evaluate rights on every invocation."""

    selected_attest_dir = (
        os.path.abspath(os.fspath(attest_dir)) if attest_dir is not None else None
    )
    trust_identity = (
        _file_identity(
            Path(selected_attest_dir) / "trusted_operator_keys",
            label="Palimpsest China operator trust policy",
        )
        if selected_attest_dir is not None
        else None
    )
    context = _load_accepted_export_cached(
        _file_identity(manifest_path, label="Palimpsest China manifest"),
        _file_identity(artifact_path, label="Palimpsest China artifact"),
        (
            _file_identity(input_ledger_path, label="Palimpsest China input ledger")
            if input_ledger_path is not None
            else None
        ),
        (
            _file_identity(
                availability_path,
                label="Palimpsest China availability receipt",
            )
            if availability_path is not None
            else None
        ),
        _file_identity(acceptance_path, label="Palimpsest China acceptance receipt"),
        selected_attest_dir,
        trust_identity,
    )
    evaluation = now or _utc_now()
    if (
        type(evaluation) is not datetime
        or evaluation.tzinfo is None
        or evaluation.utcoffset() is None
    ):
        raise PalimpsestChinaIntakeError("now must be a timezone-aware datetime")
    evaluation = evaluation.astimezone(UTC)
    accepted_at = _timestamp(context.accepted_at, label="acceptance.accepted_at")
    if accepted_at > evaluation:
        raise PalimpsestChinaIntakeError("Seiche acceptance clock is in the future")
    expiry = _timestamp(
        context.source_decision["expires_at"], label="world_bank_wdi.expires_at"
    )
    if expiry <= evaluation:
        raise PalimpsestChinaIntakeError(
            "world_bank_wdi rights decision has expired at serve time"
        )
    return context


__all__ = [
    "ACCEPTANCE_DOMAIN",
    "ACCEPTANCE_SCHEMA",
    "ALLOWED_SOURCE_IDS",
    "AVAILABILITY_RECEIPT_SCHEMA",
    "AVAILABILITY_SCHEMA",
    "CONTEXT_SCHEMA",
    "EXPORT_SCHEMA",
    "LEGACY_MANIFEST_SCHEMA",
    "MANIFEST_SCHEMA",
    "MARKET_CHANNELS",
    "POLICY_SCHEMA",
    "PRODUCER_SCHEMA",
    "REVIEW_MANIFEST_SCHEMA",
    "SERIES_REGISTRY_SCHEMA",
    "PalimpsestChinaEconomicContext",
    "PalimpsestChinaIntakeError",
    "PalimpsestChinaObservation",
    "build_acceptance_claim",
    "build_acceptance_claim_from_files",
    "build_acceptance_receipt",
    "build_acceptance_receipt_from_files",
    "clear_accepted_export_cache",
    "encode_acceptance_claim",
    "load_accepted_export",
    "verify_export",
]
