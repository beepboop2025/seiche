"""Fail-closed Railway shadow restore for Seiche backup-v4 snapshots.

Phase 4 deliberately restores an immutable filesystem generation and a fresh
PostgreSQL database while Hetzner remains the sole writer and public origin.
The module contains no Railway control-plane client: a protected workflow owns
deployment, while this runtime owns only validation, restore, and evidence.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, NamedTuple

REQUEST_SCHEMA = "seiche.railway-stateful-shadow-request.v1"
RECEIPT_SCHEMA = "seiche.railway-stateful-shadow-receipt.v4"
LOG_RESULT_SCHEMA = "seiche.railway-stateful-shadow-log-result.v1"
LOG_RESULT_MARKER = "SEICHE_RAILWAY_STATEFUL_SHADOW_RESULT_V1="
BACKUP_SCHEMA = "seiche.market-backup.v4"
LEGACY_BACKUP_SCHEMA = "seiche.market-backup.v3"
AGENT_ROOM_RESTORE_AUDIT_SCHEMA = "seiche.agent-room.restore-audit.v1"
AGENT_ROOM_UNPROVISIONED_KEY = "unprovisioned"
REPOSITORY = "beepboop2025/seiche"
WORKFLOW = "beepboop2025/seiche/.github/workflows/railway-stateful-shadow.yml"
SOURCE_REF = "refs/heads/main"
PLATFORM_ROOT = Path("/var/lib/seiche-platform")
SOURCE_ARCHIVE = Path("/migration/source.tar")
SOURCE_BUNDLE = Path("/migration/source.bundle")
REQUEST_PATH = Path("/migration/request.json")
RUNTIME_UID = 10001
RUNTIME_GID = 10001
MAX_LOG_RECEIPT_BYTES = 16 * 1024
MAX_LOG_RESULT_BYTES = 24 * 1024
MAX_DEPLOYMENT_LOG_BYTES = 8 * 1024 * 1024
PALIMPSEST_CHINA_STATE_AUDIT_SCHEMA = "seiche.palimpsest-china-activation-state.v1"

_SHA40_RE = re.compile(r"[0-9a-f]{40}")
_SHA64_RE = re.compile(r"[0-9a-f]{64}")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-" r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_SNAPSHOT_RE = re.compile(r"20[0-9]{6}T[0-9]{6}Z")
_REGION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}")
_RFC3339_UTC_RE = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})"
    r"(?:\.([0-9]{1,9}))?Z"
)
_PALIMPSEST_CHINA_STATE_KEYS = frozenset(
    {
        "audit_schema",
        "tree_sha256",
        "active_activation_id",
        "pending_candidate_activation_id",
    }
)
_REQUEST_KEYS = frozenset(
    {
        "schema",
        "repository",
        "workflow",
        "source_ref",
        "commit",
        "tree",
        "source_archive_sha256",
        "source_bundle_sha256",
        "request_id",
        "operation",
        "snapshot_id",
        "source_revision",
        "source_inventory_sha256",
        "source_content_set_sha256",
        "source_release_receipt_sha256",
        "source_recovery_receipt_sha256",
        "source_writers_frozen",
        "public_traffic_enabled",
        "requested_at",
    }
)
_LEGACY_BACKUP_MEMBERS = (
    "seiche.dump",
    "var-lib-seiche.tgz",
    "api-data.tgz",
    "table-counts.txt",
    "deployed-sha.txt",
    "manifest.env",
)
_BACKUP_MEMBERS = (
    "seiche.dump",
    "var-lib-seiche.tgz",
    "palimpsest-china.tgz",
    "palimpsest-china-state.json",
    "api-data.tgz",
    "table-counts.txt",
    "deployed-sha.txt",
    "manifest.env",
)
_ALL_BACKUP_MEMBERS = frozenset((*_BACKUP_MEMBERS, "SHA256SUMS"))
_MANIFEST_KEYS = frozenset(
    {
        "schema",
        "created_at",
        "database",
        "postgres_port",
        "state_root",
        "nbs_state_root",
        "nbs_full_store_audit_contract",
        "nbs_full_store_audit_result",
        "api_data_root",
        "critical_table_count_semantics",
        "research_only",
        "can_publish",
        "can_execute",
        "palimpsest_china_state_root",
        "palimpsest_china_state_audit_contract",
        "palimpsest_china_state_audit_result",
    }
)
_LEGACY_MANIFEST_KEYS = _MANIFEST_KEYS - {
    "palimpsest_china_state_root",
    "palimpsest_china_state_audit_contract",
    "palimpsest_china_state_audit_result",
}
_COUNTS_SQL = (
    "SELECT (SELECT count(*) FROM canonical_observations)::text || '|' || "
    "(SELECT count(*) FROM collector_runs)::text || '|' || "
    "(SELECT count(*) FROM forward_validation_records)::text || '|' || "
    "(SELECT count(*) FROM market_snapshots)::text"
)


class MigrationContractError(ValueError):
    """One closed migration contract failed validation."""


class BackupBundle(NamedTuple):
    root: Path
    snapshot_id: str
    source_revision: str
    inventory_sha256: str
    content_set_sha256: str
    member_sha256: Mapping[str, str]
    counts_floor: tuple[int, int, int, int]
    total_bytes: int
    schema: str
    palimpsest_china_state_audit: Mapping[str, Any] | None


class RestoredDatabase(NamedTuple):
    name: str
    dsn: str
    counts: tuple[int, int, int, int]


class ShadowLogResult(NamedTuple):
    lifecycle: str
    request_id: str
    deployment_id: str
    replica_id: str
    logged_at: str
    logged_at_unix_ns: int
    runtime_started_at: str
    receipt_sha256: str
    receipt_body: bytes


def canonical_document(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def rfc3339_utc_nanoseconds(value: object, *, label: str) -> int:
    """Parse a Railway RFC3339 UTC timestamp without losing nanoseconds."""

    if not isinstance(value, str):
        raise MigrationContractError(f"{label} is not a timestamp")
    match = _RFC3339_UTC_RE.fullmatch(value)
    if match is None:
        raise MigrationContractError(f"{label} is not canonical RFC3339 UTC")
    try:
        seconds = int(
            datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
            .replace(tzinfo=UTC)
            .timestamp()
        )
    except ValueError as exc:
        raise MigrationContractError(f"{label} is invalid") from exc
    fraction = (match.group(2) or "").ljust(9, "0")
    return seconds * 1_000_000_000 + int(fraction or "0")


def render_log_result(
    receipt: Mapping[str, Any],
    *,
    lifecycle: str,
    environment: Mapping[str, str],
    runtime_started_at: str,
) -> str:
    """Render one bounded, opaque result line for project-token log retrieval."""

    if lifecycle not in {"created", "reused"}:
        raise MigrationContractError("shadow log lifecycle is invalid")
    deployment_id = environment.get("RAILWAY_DEPLOYMENT_ID", "")
    replica_id = environment.get("RAILWAY_REPLICA_ID", "")
    if (
        _UUID_RE.fullmatch(deployment_id) is None
        or _UUID_RE.fullmatch(replica_id) is None
    ):
        raise MigrationContractError("shadow log Railway identity is invalid")
    canonical_started_at = (
        _utc_timestamp(runtime_started_at, label="shadow runtime started_at")
        .isoformat()
        .replace("+00:00", "Z")
    )
    if canonical_started_at != runtime_started_at:
        raise MigrationContractError("shadow runtime started_at is not canonical")

    request = receipt.get("request")
    railway = receipt.get("railway")
    if (
        not isinstance(request, dict)
        or _SHA64_RE.fullmatch(str(request.get("id", ""))) is None
        or not isinstance(railway, dict)
        or railway.get("deployment_id") != deployment_id
    ):
        raise MigrationContractError("shadow log receipt binding is invalid")
    receipt_body = canonical_document(dict(receipt))
    if not receipt_body or len(receipt_body) > MAX_LOG_RECEIPT_BYTES:
        raise MigrationContractError("shadow receipt exceeds the log transport bound")
    receipt_sha256 = _sha256_bytes(receipt_body)
    envelope = {
        "schema": LOG_RESULT_SCHEMA,
        "lifecycle": lifecycle,
        "request_id": request["id"],
        "deployment_id": deployment_id,
        "replica_id": replica_id,
        "runtime_started_at": runtime_started_at,
        "receipt_sha256": receipt_sha256,
        "receipt": dict(receipt),
    }
    encoded = base64.b64encode(canonical_document(envelope)).decode("ascii")
    marker = LOG_RESULT_MARKER + encoded
    if len(marker.encode("ascii")) > MAX_LOG_RESULT_BYTES:
        raise MigrationContractError("shadow log result exceeds the transport bound")
    return marker


def extract_log_results(
    body: bytes,
    *,
    expected_request_id: str,
    expected_deployment_id: str,
    expected_replicas: Mapping[str, str],
) -> dict[str, ShadowLogResult]:
    """Extract an exact lifecycle set from Railway CLI JSON deployment logs."""

    if not body or len(body) > MAX_DEPLOYMENT_LOG_BYTES:
        raise MigrationContractError("Railway deployment log size is invalid")
    if (
        _SHA64_RE.fullmatch(expected_request_id) is None
        or _UUID_RE.fullmatch(expected_deployment_id) is None
        or not expected_replicas
        or set(expected_replicas) not in ({"created"}, {"created", "reused"})
        or any(
            _UUID_RE.fullmatch(value) is None for value in expected_replicas.values()
        )
    ):
        raise MigrationContractError("expected shadow log identity is invalid")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationContractError("Railway deployment logs are not UTF-8") from exc

    results: dict[str, ShadowLogResult] = {}
    for line in text.splitlines():
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MigrationContractError(
                "Railway deployment log line is not JSON"
            ) from exc
        if not isinstance(record, dict) or not isinstance(record.get("message"), str):
            raise MigrationContractError("Railway deployment log record is invalid")
        message = record["message"]
        if LOG_RESULT_MARKER not in message:
            continue
        if not message.startswith(LOG_RESULT_MARKER):
            raise MigrationContractError("shadow log result framing is invalid")
        logged_at = record.get("timestamp")
        if not isinstance(logged_at, str):
            raise MigrationContractError("Railway shadow log timestamp is absent")
        logged_at_unix_ns = rfc3339_utc_nanoseconds(
            logged_at,
            label="Railway shadow log timestamp",
        )
        encoded = message.removeprefix(LOG_RESULT_MARKER)
        if (
            not encoded
            or len(message.encode("utf-8")) > MAX_LOG_RESULT_BYTES
            or re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", encoded) is None
        ):
            raise MigrationContractError("shadow log result encoding is invalid")
        try:
            envelope_body = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise MigrationContractError(
                "shadow log result is truncated or malformed"
            ) from exc
        if base64.b64encode(envelope_body).decode("ascii") != encoded:
            raise MigrationContractError("shadow log result encoding is not canonical")
        envelope = _decode_canonical_json(envelope_body, label="shadow log result")
        if (
            set(envelope)
            != {
                "schema",
                "lifecycle",
                "request_id",
                "deployment_id",
                "replica_id",
                "runtime_started_at",
                "receipt_sha256",
                "receipt",
            }
            or envelope.get("schema") != LOG_RESULT_SCHEMA
        ):
            raise MigrationContractError("shadow log result fields are invalid")
        lifecycle = envelope.get("lifecycle")
        if lifecycle not in expected_replicas:
            raise MigrationContractError(
                "shadow log result lifecycle is stale or unexpected"
            )
        if lifecycle in results:
            raise MigrationContractError("shadow log result lifecycle is duplicated")
        if (
            envelope.get("request_id") != expected_request_id
            or envelope.get("deployment_id") != expected_deployment_id
            or envelope.get("replica_id") != expected_replicas[lifecycle]
        ):
            raise MigrationContractError(
                "shadow log result identity is stale or unexpected"
            )
        canonical_started_at = (
            _utc_timestamp(
                envelope.get("runtime_started_at"),
                label="shadow log runtime started_at",
            )
            .isoformat()
            .replace("+00:00", "Z")
        )
        if canonical_started_at != envelope.get("runtime_started_at"):
            raise MigrationContractError(
                "shadow log runtime started_at is not canonical"
            )
        receipt = envelope.get("receipt")
        if not isinstance(receipt, dict):
            raise MigrationContractError("shadow log receipt is invalid")
        receipt_request = receipt.get("request")
        receipt_railway = receipt.get("railway")
        if not isinstance(receipt_request, dict) or not isinstance(
            receipt_railway, dict
        ):
            raise MigrationContractError("shadow log receipt identity is invalid")
        receipt_body = canonical_document(receipt)
        if not receipt_body or len(receipt_body) > MAX_LOG_RECEIPT_BYTES:
            raise MigrationContractError("shadow log receipt size is invalid")
        receipt_sha256 = _sha256_bytes(receipt_body)
        if (
            envelope.get("receipt_sha256") != receipt_sha256
            or receipt_request.get("id") != expected_request_id
            or receipt_railway.get("deployment_id") != expected_deployment_id
        ):
            raise MigrationContractError(
                "shadow log receipt digest or identity is invalid"
            )
        results[lifecycle] = ShadowLogResult(
            lifecycle=lifecycle,
            request_id=expected_request_id,
            deployment_id=expected_deployment_id,
            replica_id=expected_replicas[lifecycle],
            logged_at=logged_at,
            logged_at_unix_ns=logged_at_unix_ns,
            runtime_started_at=canonical_started_at,
            receipt_sha256=receipt_sha256,
            receipt_body=receipt_body,
        )
    if set(results) != set(expected_replicas):
        raise MigrationContractError("shadow log result lifecycle set is incomplete")
    return results


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file_metadata(path: Path, *, maximum_bytes: int) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise MigrationContractError("required migration file is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > maximum_bytes
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise MigrationContractError("migration file metadata is unsafe")
    return metadata


def _stable_read(path: Path, *, maximum_bytes: int) -> bytes:
    before = _regular_file_metadata(path, maximum_bytes=maximum_bytes)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise MigrationContractError("migration file changed before read")
        body = bytearray()
        while len(body) <= maximum_bytes:
            chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        after = os.fstat(descriptor)

        def identity(item: os.stat_result) -> tuple[int, ...]:
            return (
                item.st_dev,
                item.st_ino,
                item.st_mode,
                item.st_nlink,
                item.st_size,
                item.st_mtime_ns,
                item.st_ctime_ns,
            )

        if len(body) > maximum_bytes or identity(opened) != identity(after):
            raise MigrationContractError("migration file changed during read")
        return bytes(body)
    finally:
        os.close(descriptor)


def _decode_canonical_json(body: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationContractError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict) or canonical_document(value) != body:
        raise MigrationContractError(f"{label} is not canonical JSON")
    return value


def _utc_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise MigrationContractError(f"{label} is not canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise MigrationContractError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise MigrationContractError(f"{label} is not UTC")
    return parsed.astimezone(UTC).replace(microsecond=0)


def _snapshot_timestamp(snapshot_id: str) -> datetime:
    try:
        return datetime.strptime(snapshot_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise MigrationContractError("snapshot identity is invalid") from exc


def validate_request(
    value: object,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _REQUEST_KEYS:
        raise MigrationContractError("migration request fields are invalid")
    if (
        value.get("schema") != REQUEST_SCHEMA
        or value.get("repository") != REPOSITORY
        or value.get("workflow") != WORKFLOW
        or value.get("source_ref") != SOURCE_REF
        or value.get("operation") != "shadow"
        or value.get("source_writers_frozen") is not False
        or value.get("public_traffic_enabled") is not False
    ):
        raise MigrationContractError("migration request policy is invalid")
    for name in ("commit", "tree", "source_revision"):
        if (
            not isinstance(value.get(name), str)
            or _SHA40_RE.fullmatch(value[name]) is None
        ):
            raise MigrationContractError(f"migration request {name} is invalid")
    if value["source_revision"] != value["commit"]:
        raise MigrationContractError("shadow snapshot does not match candidate commit")
    for name in (
        "source_archive_sha256",
        "source_bundle_sha256",
        "request_id",
        "source_inventory_sha256",
        "source_content_set_sha256",
        "source_release_receipt_sha256",
        "source_recovery_receipt_sha256",
    ):
        if (
            not isinstance(value.get(name), str)
            or _SHA64_RE.fullmatch(value[name]) is None
        ):
            raise MigrationContractError(f"migration request {name} is invalid")
    snapshot_id = value.get("snapshot_id")
    if not isinstance(snapshot_id, str) or _SNAPSHOT_RE.fullmatch(snapshot_id) is None:
        raise MigrationContractError("migration request snapshot identity is invalid")
    requested_at = _utc_timestamp(value.get("requested_at"), label="requested_at")
    snapshot_at = _snapshot_timestamp(snapshot_id)
    observed = now or datetime.now(UTC)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise MigrationContractError("migration request clock is not timezone-aware")
    observed = observed.astimezone(UTC).replace(microsecond=0)
    if not snapshot_at <= requested_at <= snapshot_at + timedelta(hours=36):
        raise MigrationContractError(
            "migration request is not bound to a fresh snapshot"
        )
    if requested_at > observed + timedelta(minutes=5):
        raise MigrationContractError("migration request is implausibly in the future")
    if requested_at < observed - timedelta(days=7):
        raise MigrationContractError("migration request is stale")
    return dict(value)


def load_request(
    request_path: Path,
    source_archive: Path,
    source_bundle: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    request = validate_request(
        _decode_canonical_json(
            _stable_read(request_path, maximum_bytes=32 * 1024),
            label="migration request",
        ),
        now=now,
    )
    _regular_file_metadata(source_archive, maximum_bytes=1024**3)
    _regular_file_metadata(source_bundle, maximum_bytes=2 * 1024**3)
    if sha256_file(source_archive) != request["source_archive_sha256"]:
        raise MigrationContractError("source archive digest differs from request")
    if sha256_file(source_bundle) != request["source_bundle_sha256"]:
        raise MigrationContractError("source bundle digest differs from request")
    return request


def _parse_manifest(body: bytes, *, snapshot_id: str) -> dict[str, str]:
    try:
        lines = body.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise MigrationContractError("backup manifest is not UTF-8") from exc
    manifest: dict[str, str] = {}
    for line in lines:
        if line.count("=") != 1:
            raise MigrationContractError("backup manifest shape is invalid")
        key, value = line.split("=", 1)
        if key in manifest:
            raise MigrationContractError("backup manifest has duplicate fields")
        manifest[key] = value
    schema = manifest.get("schema")
    expected_keys = _MANIFEST_KEYS if schema == BACKUP_SCHEMA else _LEGACY_MANIFEST_KEYS
    if (
        schema not in {BACKUP_SCHEMA, LEGACY_BACKUP_SCHEMA}
        or set(manifest) != expected_keys
    ):
        raise MigrationContractError("backup manifest fields are invalid")
    if (
        manifest["created_at"] != snapshot_id
        or manifest["database"] != "seiche"
        or re.fullmatch(r"[0-9]{1,5}", manifest["postgres_port"]) is None
        or manifest["state_root"] != "/var/lib/seiche"
        or manifest["nbs_state_root"] != "/var/lib/seiche-nbs"
        or manifest["api_data_root"] != "/home/seiche/app/backend/data"
        or manifest["critical_table_count_semantics"] != "pre_dump_lower_bound"
        or manifest["nbs_full_store_audit_contract"] != "seiche.nbs-full-store-audit.v1"
        or manifest["nbs_full_store_audit_result"] != "required_at_restore"
        or manifest["research_only"] != "true"
        or manifest["can_publish"] != "false"
        or manifest["can_execute"] != "false"
    ):
        raise MigrationContractError("backup manifest contract is invalid")
    if schema == BACKUP_SCHEMA and (
        manifest["palimpsest_china_state_root"] != "/var/lib/seiche-palimpsest-china"
        or manifest["palimpsest_china_state_audit_contract"]
        != "seiche.palimpsest-china-activation-state.v1"
        or manifest["palimpsest_china_state_audit_result"] != "required_at_restore"
    ):
        raise MigrationContractError(
            "backup Palimpsest China state contract is invalid"
        )
    return manifest


def _parse_counts(body: bytes) -> tuple[int, int, int, int]:
    try:
        value = body.decode("ascii")
    except UnicodeDecodeError as exc:
        raise MigrationContractError("backup table counts are not ASCII") from exc
    if re.fullmatch(r"[0-9]+\|[0-9]+\|[0-9]+\|[0-9]+\n", value) is None:
        raise MigrationContractError("backup table-count floor is invalid")
    return tuple(int(item) for item in value.strip().split("|"))  # type: ignore[return-value]


def _parse_palimpsest_state_audit(body: bytes) -> dict[str, Any]:
    value = _decode_canonical_json(body, label="Palimpsest China state audit")
    keys = {
        "schema",
        "state_root",
        "tree_sha256",
        "bundles",
        "receipts",
        "active_activation_id",
        "pending_candidate_activation_id",
    }
    if type(value) is not dict or set(value) != keys:
        raise MigrationContractError("Palimpsest China state audit fields changed")
    bundles = value["bundles"]
    receipts = value["receipts"]
    active = value["active_activation_id"]
    pending = value["pending_candidate_activation_id"]
    if (
        value["schema"] != PALIMPSEST_CHINA_STATE_AUDIT_SCHEMA
        or value["state_root"] != "/var/lib/seiche-palimpsest-china"
        or not isinstance(value["tree_sha256"], str)
        or _SHA64_RE.fullmatch(value["tree_sha256"]) is None
        or type(bundles) is not list
        or type(receipts) is not list
        or any(
            not isinstance(item, str) or _SHA64_RE.fullmatch(item) is None
            for item in bundles
        )
        or any(
            not isinstance(item, str) or _SHA64_RE.fullmatch(item) is None
            for item in receipts
        )
        or bundles != sorted(set(bundles))
        or receipts != sorted(set(receipts))
        or any(
            item is not None
            and (not isinstance(item, str) or _SHA64_RE.fullmatch(item) is None)
            for item in (active, pending)
        )
    ):
        raise MigrationContractError("Palimpsest China state audit is invalid")
    if pending is not None:
        raise MigrationContractError(
            "Palimpsest China state has an unfinished activation transaction"
        )
    return dict(value)


def palimpsest_china_state_from_audit(
    audit: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project the closed, portable identity from one verified state audit."""

    if not isinstance(audit, Mapping):
        raise MigrationContractError("Palimpsest China state audit is unavailable")
    state = {
        "audit_schema": audit.get("schema"),
        "tree_sha256": audit.get("tree_sha256"),
        "active_activation_id": audit.get("active_activation_id"),
        "pending_candidate_activation_id": audit.get("pending_candidate_activation_id"),
    }
    return validate_palimpsest_china_state(state)


def validate_palimpsest_china_state(value: object) -> dict[str, Any]:
    """Validate the exact cross-receipt Palimpsest China state identity."""

    if not isinstance(value, dict) or set(value) != _PALIMPSEST_CHINA_STATE_KEYS:
        raise MigrationContractError("Palimpsest China state identity fields changed")
    tree = value.get("tree_sha256")
    active = value.get("active_activation_id")
    if (
        value.get("audit_schema") != PALIMPSEST_CHINA_STATE_AUDIT_SCHEMA
        or not isinstance(tree, str)
        or _SHA64_RE.fullmatch(tree) is None
        or (
            active is not None
            and (not isinstance(active, str) or _SHA64_RE.fullmatch(active) is None)
        )
        or value.get("pending_candidate_activation_id") is not None
    ):
        raise MigrationContractError("Palimpsest China state identity is invalid")
    return dict(value)


def validate_bundle(
    root: Path,
    request: Mapping[str, Any],
    *,
    maximum_total_bytes: int = 30 * 1024**3,
) -> BackupBundle:
    try:
        root_metadata = root.lstat()
        entries = {item.name for item in root.iterdir()}
    except OSError as exc:
        raise MigrationContractError("backup bundle is unavailable") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or root.is_symlink():
        raise MigrationContractError("backup bundle root is unsafe")
    snapshot_id = str(request["snapshot_id"])
    manifest_path = root / "manifest.env"
    _regular_file_metadata(manifest_path, maximum_bytes=4096)
    manifest = _parse_manifest(
        _stable_read(manifest_path, maximum_bytes=4096),
        snapshot_id=snapshot_id,
    )
    backup_members = (
        _BACKUP_MEMBERS
        if manifest["schema"] == BACKUP_SCHEMA
        else _LEGACY_BACKUP_MEMBERS
    )
    all_backup_members = frozenset((*backup_members, "SHA256SUMS"))
    if entries != all_backup_members:
        raise MigrationContractError("backup bundle file set is not closed")
    members: dict[str, Path] = {}
    total_bytes = 0
    for name in (*backup_members, "SHA256SUMS"):
        path = root / name
        metadata = _regular_file_metadata(path, maximum_bytes=maximum_total_bytes)
        total_bytes += metadata.st_size
        members[name] = path
    if total_bytes > maximum_total_bytes:
        raise MigrationContractError("backup bundle exceeds the restore capacity")

    inventory = _stable_read(members["SHA256SUMS"], maximum_bytes=4096)
    try:
        inventory_lines = inventory.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise MigrationContractError("backup inventory is not ASCII") from exc
    if len(inventory_lines) != len(backup_members):
        raise MigrationContractError("backup inventory length is invalid")
    digests: dict[str, str] = {}
    for expected_name, line in zip(backup_members, inventory_lines, strict=True):
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9.-]+)", line)
        if match is None or match.group(2) != expected_name:
            raise MigrationContractError("backup inventory order is invalid")
        digest = sha256_file(members[expected_name])
        if digest != match.group(1):
            raise MigrationContractError("backup member digest mismatch")
        digests[expected_name] = digest

    palimpsest_audit = None
    if manifest["schema"] == BACKUP_SCHEMA:
        palimpsest_audit = _parse_palimpsest_state_audit(
            _stable_read(
                members["palimpsest-china-state.json"],
                maximum_bytes=512 * 1024,
            )
        )
    revision_body = _stable_read(members["deployed-sha.txt"], maximum_bytes=64)
    try:
        revision = revision_body.decode("ascii")
    except UnicodeDecodeError as exc:
        raise MigrationContractError("backup revision is not ASCII") from exc
    if re.fullmatch(r"[0-9a-f]{40}\n", revision) is None:
        raise MigrationContractError("backup revision is invalid")
    source_revision = revision.strip()
    counts = _parse_counts(_stable_read(members["table-counts.txt"], maximum_bytes=256))
    inventory_sha256 = _sha256_bytes(inventory)
    content = hashlib.sha256()
    content_bytes = 0
    for name in backup_members:
        size = members[name].stat().st_size
        content_bytes += size
        content.update(name.encode("ascii") + b"\0")
        content.update(digests[name].encode("ascii") + b"\0")
        content.update(str(size).encode("ascii") + b"\n")
    content_set_sha256 = content.hexdigest()
    if (
        source_revision != request["source_revision"]
        or inventory_sha256 != request["source_inventory_sha256"]
        or content_set_sha256 != request["source_content_set_sha256"]
    ):
        raise MigrationContractError("backup bundle differs from migration request")
    if content_bytes > maximum_total_bytes:
        raise MigrationContractError("backup content set exceeds the restore capacity")
    return BackupBundle(
        root=root,
        snapshot_id=snapshot_id,
        source_revision=source_revision,
        inventory_sha256=inventory_sha256,
        content_set_sha256=content_set_sha256,
        member_sha256=digests,
        counts_floor=counts,
        total_bytes=content_bytes,
        schema=manifest["schema"],
        palimpsest_china_state_audit=palimpsest_audit,
    )


def validate_tar_contract(
    path: Path,
    *,
    expected_roots: frozenset[str],
    maximum_members: int = 2_000_000,
    maximum_expanded_bytes: int = 100 * 1024**3,
) -> tuple[tarfile.TarInfo, ...]:
    try:
        archive = tarfile.open(path, mode="r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise MigrationContractError("backup archive cannot be opened") from exc
    with archive:
        members = archive.getmembers()
    if not members or len(members) > maximum_members:
        raise MigrationContractError("backup archive member count is invalid")
    names: set[str] = set()
    roots: set[str] = set()
    expanded = 0
    for member in members:
        if "\x00" in member.name or member.name.startswith("/"):
            raise MigrationContractError("backup archive member path is unsafe")
        canonical_name = member.name.rstrip("/")
        path_parts = PurePosixPath(canonical_name).parts
        if (
            not path_parts
            or any(part in {"", ".", ".."} for part in path_parts)
            or PurePosixPath(canonical_name).as_posix() != canonical_name
            or (member.isfile() and canonical_name != member.name)
        ):
            raise MigrationContractError("backup archive member path is not canonical")
        if canonical_name in names:
            raise MigrationContractError("backup archive has duplicate members")
        names.add(canonical_name)
        roots.add(path_parts[0])
        if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
            raise MigrationContractError(
                "backup archive contains an unsafe member type"
            )
        if member.isfile():
            expanded += member.size
            if expanded > maximum_expanded_bytes:
                raise MigrationContractError("backup archive expands beyond its bound")
    if roots != set(expected_roots):
        raise MigrationContractError("backup archive top-level roots are invalid")
    for root in expected_roots:
        root_member = next(
            (member for member in members if member.name.rstrip("/") == root),
            None,
        )
        if root_member is None or not root_member.isdir():
            raise MigrationContractError("backup archive root directory is missing")
    return tuple(members)


def _root_owned_tar_filter(
    member: tarfile.TarInfo,
    destination: str,
) -> tarfile.TarInfo:
    del destination
    selected = copy.copy(member)
    selected.uid = os.geteuid()
    selected.gid = os.getegid()
    selected.uname = ""
    selected.gname = ""
    if selected.isfile():
        selected.mode &= 0o0777
    else:
        selected.mode &= 0o2777
    return selected


def extract_validated_tar(
    path: Path,
    destination: Path,
    *,
    expected_roots: frozenset[str],
) -> None:
    validate_tar_contract(path, expected_roots=expected_roots)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            archive.extractall(
                path=destination,
                filter=_root_owned_tar_filter,
            )
    except (OSError, tarfile.TarError) as exc:
        raise MigrationContractError(
            "validated backup archive extraction failed"
        ) from exc


def _walk_real_tree(root: Path) -> list[Path]:
    paths = [root]
    for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
        names.sort()
        filenames.sort()
        parent = Path(directory)
        for name in names:
            path = parent / name
            if path.is_symlink() or not path.is_dir():
                raise MigrationContractError(
                    "restored tree contains an unsafe directory"
                )
            paths.append(path)
        for name in filenames:
            path = parent / name
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise MigrationContractError("restored tree contains an unsafe file")
            paths.append(path)
    return paths


def hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _walk_real_tree(root):
        relative = "." if path == root else path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            digest.update(
                f"d\0{relative}\0{stat.S_IMODE(metadata.st_mode):04o}\n".encode()
            )
            continue
        digest.update(
            (
                f"f\0{relative}\0{stat.S_IMODE(metadata.st_mode):04o}\0"
                f"{metadata.st_size}\0"
            ).encode()
        )
        digest.update(sha256_file(path).encode("ascii") + b"\n")
    return digest.hexdigest()


def _chown_tree(root: Path, *, uid: int, gid: int) -> None:
    for path in reversed(_walk_real_tree(root)):
        os.chown(path, uid, gid, follow_symlinks=False)


def _prepare_nbs_reader_group(root: Path, *, gid: int) -> None:
    public = root / "public"
    revisions = public / "revisions"
    os.chown(root, os.geteuid(), gid, follow_symlinks=False)
    for path in _walk_real_tree(public):
        os.chown(path, os.geteuid(), gid, follow_symlinks=False)
    os.chmod(root, 0o750)
    os.chmod(public, 0o750)
    os.chmod(revisions, 0o2750)


def _validate_sqlite(path: Path) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise MigrationContractError("restored API SQLite database is unsafe")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as database:
        if database.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise MigrationContractError("restored API SQLite database is corrupt")


def absent_agent_room_audit(
    *, server_key_id: str | None = None
) -> dict[str, Any]:
    """Return the explicit receipt for a never-initialized Agent Room.

    An already-existing operator key is retained as the independent identity
    under which a later production bootstrap may occur.  ``None`` means no
    key was present at the receipted boundary and therefore no automatic
    Agent Room bootstrap is authorized.
    """

    if server_key_id is not None and _SHA64_RE.fullmatch(server_key_id) is None:
        raise MigrationContractError("absent Agent Room key identity is invalid")

    return {
        "schema": AGENT_ROOM_RESTORE_AUDIT_SCHEMA,
        "result": "absent_uninitialized",
        "server_key_id": server_key_id,
        "participant_count": 0,
        "room_count": 0,
        "event_count": 0,
        "state_sha256": None,
        "non_executable": True,
        "execution_authority": "none",
    }


def validate_agent_room_audit(value: object) -> dict[str, Any]:
    """Validate the bounded recovery projection of the full Agent Room audit."""

    expected = {
        "schema",
        "result",
        "server_key_id",
        "participant_count",
        "room_count",
        "event_count",
        "state_sha256",
        "non_executable",
        "execution_authority",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise MigrationContractError("Agent Room recovery audit fields are invalid")
    if (
        value.get("schema") != AGENT_ROOM_RESTORE_AUDIT_SCHEMA
        or value.get("result") not in {"verified", "absent_uninitialized"}
        or value.get("non_executable") is not True
        or value.get("execution_authority") != "none"
    ):
        raise MigrationContractError("Agent Room recovery audit policy is invalid")
    counts = tuple(
        value.get(name) for name in ("participant_count", "room_count", "event_count")
    )
    if any(
        isinstance(item, bool)
        or not isinstance(item, int)
        or not 0 <= item <= 2_000_000
        for item in counts
    ):
        raise MigrationContractError("Agent Room recovery audit counts are invalid")
    participant_count, room_count, event_count = counts
    if event_count > room_count * 4_096 or (room_count and not participant_count):
        raise MigrationContractError(
            "Agent Room recovery audit counts are inconsistent"
        )
    if value["result"] == "absent_uninitialized":
        if (
            counts != (0, 0, 0)
            or value.get("state_sha256") is not None
            or (
                value.get("server_key_id") is not None
                and (
                    not isinstance(value.get("server_key_id"), str)
                    or _SHA64_RE.fullmatch(value["server_key_id"]) is None
                )
            )
        ):
            raise MigrationContractError(
                "absent Agent Room recovery audit contains durable state"
            )
    elif (
        not isinstance(value.get("server_key_id"), str)
        or _SHA64_RE.fullmatch(value["server_key_id"]) is None
        or not isinstance(value.get("state_sha256"), str)
        or _SHA64_RE.fullmatch(value["state_sha256"]) is None
    ):
        raise MigrationContractError("verified Agent Room audit identity is invalid")
    return dict(value)


def agent_room_expected_key_binding(audit: object) -> str:
    """Project a receipt audit into the closed runtime provisioning gate."""

    validated = validate_agent_room_audit(audit)
    key_id = validated["server_key_id"]
    return key_id if isinstance(key_id, str) else AGENT_ROOM_UNPROVISIONED_KEY


def audit_agent_room_state(
    api_data: Path,
    *,
    expected_owner_uid: int | None = None,
) -> dict[str, Any]:
    """Cryptographically audit an extracted Agent Room under its restored key."""

    from seiche import agent_room, attest

    room_root = api_data / "_agent_room"
    database_path = room_root / "agent-room.sqlite"
    attest_root = api_data / "_attest"
    seal_path = attest_root / agent_room.AGENT_ROOM_INITIALIZATION_SEAL_FILENAME
    key_path = attest_root / "operator_key.pem"
    seal_present = seal_path.exists() or seal_path.is_symlink()
    if not database_path.exists() and not database_path.is_symlink():
        if room_root.exists() or room_root.is_symlink():
            if room_root.is_symlink() or not room_root.is_dir():
                raise MigrationContractError("restored Agent Room path is unsafe")
            try:
                entries = tuple(room_root.iterdir())
            except OSError as exc:
                raise MigrationContractError(
                    "restored Agent Room path is unavailable"
                ) from exc
            if entries:
                raise MigrationContractError(
                    "uninitialized Agent Room contains partial state"
                )
        if seal_present:
            try:
                private_key, _public_key = attest.load_existing_keypair(
                    str(api_data / "_attest"),
                    expected_owner_uid=expected_owner_uid,
                )
                agent_room.verify_initialization_seal(
                    seal_path,
                    server_private_key=private_key,
                    expected_owner_uid=expected_owner_uid,
                )
            except Exception as exc:
                raise MigrationContractError(
                    "restored Agent Room initialization seal failed"
                ) from exc
            raise MigrationContractError(
                "initialized Agent Room database is unavailable"
            )
        server_key_id = None
        if attest_root.exists() or attest_root.is_symlink():
            if attest_root.is_symlink() or not attest_root.is_dir():
                raise MigrationContractError(
                    "restored Agent Room key path is unsafe"
                )
            if key_path.exists() or key_path.is_symlink():
                try:
                    _private_key, public_key = attest.load_existing_keypair(
                        str(attest_root),
                        expected_owner_uid=expected_owner_uid,
                    )
                    server_key_id = agent_room.ed25519_key_id(public_key)
                except Exception as exc:
                    raise MigrationContractError(
                        "restored Agent Room bootstrap key failed"
                    ) from exc
        return absent_agent_room_audit(server_key_id=server_key_id)
    try:
        if room_root.is_symlink() or not room_root.is_dir():
            raise MigrationContractError("restored Agent Room path is unsafe")
        if {entry.name for entry in room_root.iterdir()} != {"agent-room.sqlite"}:
            raise MigrationContractError("restored Agent Room members are not closed")
        if not seal_present:
            raise MigrationContractError("initialized Agent Room seal is unavailable")
        private_key, _public_key = attest.load_existing_keypair(
            str(api_data / "_attest"),
            expected_owner_uid=expected_owner_uid,
        )
        agent_room.verify_initialization_seal(
            seal_path,
            server_private_key=private_key,
            expected_owner_uid=expected_owner_uid,
        )
        store = agent_room.AgentRoomStore.open_existing(
            database_path,
            server_private_key=private_key,
            expected_owner_uid=expected_owner_uid,
        )
        raw = store.audit_all_rooms()
    except MigrationContractError:
        raise
    except Exception as exc:
        raise MigrationContractError(
            "restored Agent Room cryptographic audit failed"
        ) from exc
    if (
        not isinstance(raw, dict)
        or set(raw)
        != {
            "ok",
            "schema",
            "server_key_id",
            "participant_count",
            "room_count",
            "event_count",
            "state_sha256",
            "non_executable",
            "execution_authority",
        }
        or raw.get("ok") is not True
        or raw.get("schema") != agent_room.AGENT_ROOM_AUDIT_SCHEMA
    ):
        raise MigrationContractError("restored Agent Room audit result is invalid")
    return validate_agent_room_audit(
        {
            "schema": AGENT_ROOM_RESTORE_AUDIT_SCHEMA,
            "result": "verified",
            "server_key_id": raw["server_key_id"],
            "participant_count": raw["participant_count"],
            "room_count": raw["room_count"],
            "event_count": raw["event_count"],
            "state_sha256": raw["state_sha256"],
            "non_executable": raw["non_executable"],
            "execution_authority": raw["execution_authority"],
        }
    )


def _audit_nbs(root: Path) -> str:
    from seiche.nbs_intake import NBSIntakeStore

    try:
        result = NBSIntakeStore(root).audit_store_strict()
    except Exception as exc:
        raise MigrationContractError("restored NBS evidence audit failed") from exc
    if result not in {"not_onboarded", "verified_head"}:
        raise MigrationContractError("restored NBS evidence audit result is invalid")
    return result


def restore_filesystem_generation(
    bundle: BackupBundle,
    staging: Path,
    *,
    runtime_uid: int,
    runtime_gid: int,
    agent_room_audit_out: dict[str, Any] | None = None,
) -> tuple[str, Mapping[str, str]]:
    state_stage = staging / "state-archive"
    api_stage = staging / "api-archive"
    palimpsest_stage = staging / "palimpsest-china-archive"
    extract_validated_tar(
        bundle.root / "var-lib-seiche.tgz",
        state_stage,
        expected_roots=frozenset({"seiche", "seiche-nbs"}),
    )
    extract_validated_tar(
        bundle.root / "api-data.tgz",
        api_stage,
        expected_roots=frozenset({"api-data"}),
    )
    if bundle.schema == BACKUP_SCHEMA:
        extract_validated_tar(
            bundle.root / "palimpsest-china.tgz",
            palimpsest_stage,
            expected_roots=frozenset({"seiche-palimpsest-china"}),
        )
        palimpsest = palimpsest_stage / "seiche-palimpsest-china"
        try:
            from seiche.palimpsest_china_activation import audit_activation_state

            palimpsest_audit = audit_activation_state(
                palimpsest,
                root_uid=os.geteuid(),
                root_gid=os.getegid(),
                api_uid=runtime_uid,
                api_gid=runtime_gid,
                normalize_restored=True,
                declared_state_root=Path("/var/lib/seiche-palimpsest-china"),
            )
        except Exception as exc:
            raise MigrationContractError(
                "restored Palimpsest China activation-state audit failed"
            ) from exc
        if palimpsest_audit != bundle.palimpsest_china_state_audit:
            raise MigrationContractError(
                "restored Palimpsest China activation state changed"
            )
    else:
        palimpsest_stage.mkdir(mode=0o750)
        palimpsest = palimpsest_stage / "seiche-palimpsest-china"
        palimpsest.mkdir(mode=0o750)
        (palimpsest / "receipts").mkdir(mode=0o700)
        try:
            from seiche.palimpsest_china_activation import audit_activation_state

            palimpsest_audit = audit_activation_state(
                palimpsest,
                root_uid=os.geteuid(),
                root_gid=os.getegid(),
                api_uid=runtime_uid,
                api_gid=runtime_gid,
                normalize_restored=True,
                declared_state_root=Path("/var/lib/seiche-palimpsest-china"),
            )
        except Exception as exc:
            raise MigrationContractError(
                "legacy Palimpsest China activation-state normalization failed"
            ) from exc
        if any(
            (
                palimpsest_audit["bundles"],
                palimpsest_audit["receipts"],
                palimpsest_audit["active_activation_id"],
                palimpsest_audit["pending_candidate_activation_id"],
            )
        ):
            raise MigrationContractError(
                "legacy Palimpsest China activation state is not empty"
            )
    market = state_stage / "seiche"
    nbs = state_stage / "seiche-nbs"
    api_data = api_stage / "api-data"
    _validate_sqlite(api_data / "seiche.sqlite")
    agent_room_audit = audit_agent_room_state(api_data)
    if agent_room_audit_out is not None:
        agent_room_audit_out.clear()
        agent_room_audit_out.update(agent_room_audit)
    _prepare_nbs_reader_group(nbs, gid=runtime_gid)
    nbs_result = _audit_nbs(nbs)
    _chown_tree(market, uid=runtime_uid, gid=runtime_gid)
    _chown_tree(api_data, uid=runtime_uid, gid=runtime_gid)
    generation = staging / "generation"
    generation.mkdir(mode=0o750)
    os.chown(generation, os.geteuid(), runtime_gid)
    market.rename(generation / "market")
    nbs.rename(generation / "nbs")
    api_data.rename(generation / "api")
    palimpsest.rename(generation / "palimpsest-china")
    os.chmod(generation, 0o750)
    shutil.rmtree(state_stage)
    shutil.rmtree(api_stage)
    shutil.rmtree(palimpsest_stage)
    digests = {
        name: hash_tree(generation / name)
        for name in ("market", "nbs", "api", "palimpsest-china")
    }
    return nbs_result, digests


def derive_database_name(snapshot_id: str, content_set_sha256: str) -> str:
    if (
        _SNAPSHOT_RE.fullmatch(snapshot_id) is None
        or _SHA64_RE.fullmatch(content_set_sha256) is None
    ):
        raise MigrationContractError("database generation inputs are invalid")
    return f"seiche_s_{snapshot_id.lower()}_{content_set_sha256[:12]}"


def _target_dsn(base_dsn: str, database_name: str) -> str:
    try:
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        values = conninfo_to_dict(base_dsn)
        values["dbname"] = database_name
        return make_conninfo(**values)
    except Exception as exc:
        raise MigrationContractError("Railway PostgreSQL URL is invalid") from exc


def restore_postgres(
    bundle: BackupBundle,
    base_dsn: str,
    *,
    timeout_seconds: int = 7200,
) -> RestoredDatabase:
    try:
        import psycopg
        from psycopg import sql
    except ImportError as exc:
        raise MigrationContractError(
            "PostgreSQL migration dependency is unavailable"
        ) from exc
    database_name = derive_database_name(
        bundle.snapshot_id,
        bundle.content_set_sha256,
    )
    target_dsn = _target_dsn(base_dsn, database_name)
    try:
        with psycopg.connect(base_dsn, autocommit=True) as connection:
            if connection.info.dbname == database_name:
                raise MigrationContractError("migration control database is the target")
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname = %s)",
                    (database_name,),
                )
                exists = bool(cursor.fetchone()[0])
                if exists:
                    cursor.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()",
                        (database_name,),
                    )
                    cursor.execute(
                        sql.SQL("DROP DATABASE {}").format(
                            sql.Identifier(database_name)
                        )
                    )
                cursor.execute(
                    sql.SQL(
                        "CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'"
                    ).format(sql.Identifier(database_name))
                )
    except MigrationContractError:
        raise
    except Exception as exc:
        raise MigrationContractError(
            "Railway PostgreSQL target cannot be prepared"
        ) from exc

    check = subprocess.run(
        ["pg_restore", "--list", str(bundle.root / "seiche.dump")],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    if check.returncode != 0:
        raise MigrationContractError("PostgreSQL archive list is invalid")
    restored = subprocess.run(
        [
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            f"--dbname={target_dsn}",
            str(bundle.root / "seiche.dump"),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout_seconds,
        check=False,
    )
    if restored.returncode != 0:
        raise MigrationContractError("PostgreSQL archive restore failed")
    counts = inspect_postgres_counts(target_dsn)
    if any(actual < floor for actual, floor in zip(counts, bundle.counts_floor)):
        raise MigrationContractError("restored PostgreSQL counts are below the floor")
    return RestoredDatabase(
        name=database_name,
        dsn=target_dsn,
        counts=counts,
    )


def inspect_postgres_counts(dsn: str) -> tuple[int, int, int, int]:
    try:
        import psycopg

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(_COUNTS_SQL)
                counts_text = str(cursor.fetchone()[0])
    except Exception as exc:
        raise MigrationContractError(
            "restored PostgreSQL counts are unavailable"
        ) from exc
    if re.fullmatch(r"[0-9]+\|[0-9]+\|[0-9]+\|[0-9]+", counts_text) is None:
        raise MigrationContractError("restored PostgreSQL counts are malformed")
    counts = tuple(int(item) for item in counts_text.split("|"))
    return counts  # type: ignore[return-value]


def railway_identity(environment: Mapping[str, str]) -> dict[str, str]:
    mapping = {
        "deployment_id": environment.get("RAILWAY_DEPLOYMENT_ID", ""),
        "project_id": environment.get("RAILWAY_PROJECT_ID", ""),
        "environment_id": environment.get("RAILWAY_ENVIRONMENT_ID", ""),
        "service_id": environment.get("RAILWAY_SERVICE_ID", ""),
        "volume_id": environment.get("SEICHE_RAILWAY_VOLUME_ID", ""),
        "volume_name": environment.get("RAILWAY_VOLUME_NAME", ""),
        "volume_mount_path": environment.get("RAILWAY_VOLUME_MOUNT_PATH", ""),
        "region": environment.get("RAILWAY_REPLICA_REGION", ""),
    }
    for name in ("deployment_id", "project_id", "environment_id", "service_id"):
        if _UUID_RE.fullmatch(mapping[name]) is None:
            raise MigrationContractError(f"Railway {name} is invalid")
    if (
        _UUID_RE.fullmatch(mapping["volume_id"]) is None
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", mapping["volume_name"])
        is None
        or mapping["volume_mount_path"] != str(PLATFORM_ROOT)
        or _REGION_RE.fullmatch(mapping["region"]) is None
    ):
        raise MigrationContractError("Railway volume or region identity is invalid")
    return mapping


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def render_receipt(
    request: Mapping[str, Any],
    bundle: BackupBundle,
    database: RestoredDatabase,
    *,
    generation_name: str,
    generation_digests: Mapping[str, str],
    nbs_audit_result: str,
    agent_room_audit: Mapping[str, Any],
    railway: Mapping[str, str],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    palimpsest_china_state = palimpsest_china_state_from_audit(
        bundle.palimpsest_china_state_audit
    )
    return {
        "schema": RECEIPT_SCHEMA,
        "request": {
            "id": request["request_id"],
            "sha256": _sha256_bytes(canonical_document(dict(request))),
            "commit": request["commit"],
            "tree": request["tree"],
            "source_archive_sha256": request["source_archive_sha256"],
            "source_bundle_sha256": request["source_bundle_sha256"],
            "source_release_receipt_sha256": request["source_release_receipt_sha256"],
            "source_recovery_receipt_sha256": request["source_recovery_receipt_sha256"],
        },
        "authority": {
            "mode": "shadow",
            "source": "hetzner",
            "source_writers_frozen": False,
            "public_traffic_enabled": False,
            "workers_started": False,
        },
        "bundle": {
            "schema": bundle.schema,
            "snapshot_id": bundle.snapshot_id,
            "source_revision": bundle.source_revision,
            "source_inventory_sha256": bundle.inventory_sha256,
            "source_content_set_sha256": bundle.content_set_sha256,
            "member_sha256": dict(bundle.member_sha256),
            "total_bytes": bundle.total_bytes,
        },
        "database": {
            "name": database.name,
            "critical_table_counts": list(database.counts),
            "critical_table_count_floor": list(bundle.counts_floor),
            "restore": "pass",
        },
        "filesystem": {
            "generation": generation_name,
            "tree_sha256": dict(generation_digests),
            "api_sqlite_quick_check": "pass",
            "agent_room_audit": validate_agent_room_audit(agent_room_audit),
            "nbs_full_store_audit_contract": "seiche.nbs-full-store-audit.v1",
            "nbs_full_store_audit_result": nbs_audit_result,
            "palimpsest_china_state_audit_contract": (
                "seiche.palimpsest-china-activation-state.v1"
            ),
            "palimpsest_china_state_audit_result": (
                "verified"
                if bundle.schema == BACKUP_SCHEMA
                else "legacy_absent_inactive"
            ),
        },
        "palimpsest_china_state": palimpsest_china_state,
        "railway": dict(railway),
        "timing": {
            "started_at": started_at,
            "completed_at": completed_at,
        },
        "research_only": True,
        "can_publish": False,
        "can_execute": False,
    }


def validate_receipt_document(
    value: object,
    *,
    request: Mapping[str, Any],
    railway: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "request",
        "authority",
        "bundle",
        "database",
        "filesystem",
        "palimpsest_china_state",
        "railway",
        "timing",
        "research_only",
        "can_publish",
        "can_execute",
    }:
        raise MigrationContractError("shadow receipt fields are invalid")
    if (
        value.get("schema") != RECEIPT_SCHEMA
        or value.get("research_only") is not True
        or value.get("can_publish") is not False
        or value.get("can_execute") is not False
    ):
        raise MigrationContractError("shadow receipt policy is invalid")
    expected_request = {
        "id": request["request_id"],
        "sha256": _sha256_bytes(canonical_document(dict(request))),
        "commit": request["commit"],
        "tree": request["tree"],
        "source_archive_sha256": request["source_archive_sha256"],
        "source_bundle_sha256": request["source_bundle_sha256"],
        "source_release_receipt_sha256": request["source_release_receipt_sha256"],
        "source_recovery_receipt_sha256": request["source_recovery_receipt_sha256"],
    }
    if value.get("request") != expected_request:
        raise MigrationContractError("shadow receipt request binding is invalid")
    if value.get("authority") != {
        "mode": "shadow",
        "source": "hetzner",
        "source_writers_frozen": False,
        "public_traffic_enabled": False,
        "workers_started": False,
    }:
        raise MigrationContractError("shadow receipt authority is invalid")
    bundle = value.get("bundle")
    bundle_schema = bundle.get("schema") if isinstance(bundle, dict) else None
    expected_members = _BACKUP_MEMBERS
    if (
        not isinstance(bundle, dict)
        or bundle_schema != BACKUP_SCHEMA
        or bundle.get("snapshot_id") != request["snapshot_id"]
        or bundle.get("source_revision") != request["source_revision"]
        or bundle.get("source_inventory_sha256") != request["source_inventory_sha256"]
        or bundle.get("source_content_set_sha256")
        != request["source_content_set_sha256"]
        or not isinstance(bundle.get("total_bytes"), int)
        or bundle["total_bytes"] <= 0
        or not isinstance(bundle.get("member_sha256"), dict)
        or set(bundle["member_sha256"]) != set(expected_members)
        or any(
            _SHA64_RE.fullmatch(item) is None
            for item in bundle["member_sha256"].values()
        )
    ):
        raise MigrationContractError("shadow receipt bundle is invalid")
    database = value.get("database")
    expected_database = derive_database_name(
        str(request["snapshot_id"]),
        str(request["source_content_set_sha256"]),
    )
    if (
        not isinstance(database, dict)
        or set(database)
        != {
            "name",
            "critical_table_counts",
            "critical_table_count_floor",
            "restore",
        }
        or database.get("name") != expected_database
        or database.get("restore") != "pass"
    ):
        raise MigrationContractError("shadow receipt database is invalid")
    for name in ("critical_table_counts", "critical_table_count_floor"):
        counts = database.get(name)
        if (
            not isinstance(counts, list)
            or len(counts) != 4
            or any(not isinstance(item, int) or item < 0 for item in counts)
        ):
            raise MigrationContractError("shadow receipt table counts are invalid")
    if any(
        actual < floor
        for actual, floor in zip(
            database["critical_table_counts"],
            database["critical_table_count_floor"],
        )
    ):
        raise MigrationContractError("shadow receipt table counts are below floor")
    filesystem = value.get("filesystem")
    generation = (
        f"{request['snapshot_id']}-{str(request['source_content_set_sha256'])[:16]}"
    )
    if (
        not isinstance(filesystem, dict)
        or set(filesystem)
        != {
            "generation",
            "tree_sha256",
            "api_sqlite_quick_check",
            "agent_room_audit",
            "nbs_full_store_audit_contract",
            "nbs_full_store_audit_result",
            "palimpsest_china_state_audit_contract",
            "palimpsest_china_state_audit_result",
        }
        or filesystem.get("generation") != generation
        or filesystem.get("api_sqlite_quick_check") != "pass"
        or filesystem.get("nbs_full_store_audit_contract")
        != "seiche.nbs-full-store-audit.v1"
        or filesystem.get("nbs_full_store_audit_result")
        not in {"not_onboarded", "verified_head"}
        or filesystem.get("palimpsest_china_state_audit_contract")
        != "seiche.palimpsest-china-activation-state.v1"
        or filesystem.get("palimpsest_china_state_audit_result") != "verified"
        or not isinstance(filesystem.get("tree_sha256"), dict)
        or set(filesystem["tree_sha256"])
        != {"market", "nbs", "api", "palimpsest-china"}
        or any(
            _SHA64_RE.fullmatch(item) is None
            for item in filesystem["tree_sha256"].values()
        )
    ):
        raise MigrationContractError("shadow receipt filesystem is invalid")
    validate_agent_room_audit(filesystem.get("agent_room_audit"))
    validate_palimpsest_china_state(value.get("palimpsest_china_state"))
    observed_railway = value.get("railway")
    if not isinstance(observed_railway, dict) or set(observed_railway) != {
        "deployment_id",
        "project_id",
        "environment_id",
        "service_id",
        "volume_id",
        "volume_name",
        "volume_mount_path",
        "region",
    }:
        raise MigrationContractError("shadow receipt Railway identity is invalid")
    if railway is not None and observed_railway != dict(railway):
        raise MigrationContractError(
            "shadow receipt belongs to another Railway runtime"
        )
    for name in ("deployment_id", "project_id", "environment_id", "service_id"):
        if _UUID_RE.fullmatch(str(observed_railway[name])) is None:
            raise MigrationContractError("shadow receipt Railway UUID is invalid")
    if _UUID_RE.fullmatch(str(observed_railway["volume_id"])) is None:
        raise MigrationContractError("shadow receipt Railway volume UUID is invalid")
    if (
        re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",
            str(observed_railway.get("volume_name")),
        )
        is None
        or observed_railway.get("volume_mount_path") != str(PLATFORM_ROOT)
        or _REGION_RE.fullmatch(str(observed_railway.get("region"))) is None
    ):
        raise MigrationContractError("shadow receipt volume mount is invalid")
    timing = value.get("timing")
    if not isinstance(timing, dict) or set(timing) != {"started_at", "completed_at"}:
        raise MigrationContractError("shadow receipt timing is invalid")
    started = _utc_timestamp(timing["started_at"], label="restore started_at")
    completed = _utc_timestamp(timing["completed_at"], label="restore completed_at")
    if completed < started:
        raise MigrationContractError("shadow receipt timing order is invalid")
    return dict(value)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _prepare_shared_directory(
    path: Path,
    *,
    gid: int,
    parents: bool = False,
) -> None:
    """Create or normalize one root-owned, group-traversable directory safely."""
    try:
        path.mkdir(mode=0o750, parents=parents, exist_ok=True)
        before = path.lstat()
    except OSError as exc:
        raise MigrationContractError(
            f"shared directory is unavailable: {path}"
        ) from exc
    if not stat.S_ISDIR(before.st_mode):
        raise MigrationContractError(f"shared directory is unsafe: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MigrationContractError(f"shared directory is unsafe: {path}") from exc
    expected_uid = os.geteuid()
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise MigrationContractError(f"shared directory changed: {path}")
        os.fchown(descriptor, expected_uid, gid)
        os.fchmod(descriptor, 0o750)
        os.fsync(descriptor)
        final = os.fstat(descriptor)
        if (
            final.st_uid != expected_uid
            or final.st_gid != gid
            or stat.S_IMODE(final.st_mode) != 0o750
        ):
            raise MigrationContractError(f"shared directory mode is invalid: {path}")
        after = path.lstat()
        if (
            (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or after.st_uid != expected_uid
            or after.st_gid != gid
            or stat.S_IMODE(after.st_mode) != 0o750
        ):
            raise MigrationContractError(f"shared directory changed: {path}")
    except MigrationContractError:
        raise
    except OSError as exc:
        raise MigrationContractError(
            f"shared directory mutation failed: {path}"
        ) from exc
    finally:
        os.close(descriptor)


def _write_receipt(path: Path, document: Mapping[str, Any], *, gid: int) -> None:
    body = canonical_document(dict(document))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, 0o440)
    try:
        written = 0
        while written < len(body):
            count = os.write(descriptor, body[written:])
            if count <= 0:
                raise OSError("receipt write made no progress")
            written += count
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o440)
        os.fchown(descriptor, os.geteuid(), gid)
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def validate_receipted_generation(
    generation_path: Path,
    receipt: Mapping[str, Any],
    *,
    runtime_uid: int = RUNTIME_UID,
    runtime_gid: int = RUNTIME_GID,
) -> None:
    if not generation_path.is_dir() or generation_path.is_symlink():
        raise MigrationContractError("accepted shadow generation is missing")
    expected = receipt.get("filesystem", {}).get("tree_sha256")
    names = ("market", "nbs", "api", "palimpsest-china")
    if not isinstance(expected, dict) or set(expected) != set(names):
        raise MigrationContractError("accepted shadow generation receipt is invalid")
    observed = {name: hash_tree(generation_path / name) for name in names}
    if observed != expected:
        raise MigrationContractError("accepted shadow generation digest changed")
    _validate_sqlite(generation_path / "api" / "seiche.sqlite")
    expected_agent_room_audit = validate_agent_room_audit(
        receipt["filesystem"].get("agent_room_audit")
    )
    observed_agent_room_audit = audit_agent_room_state(
        generation_path / "api",
        expected_owner_uid=runtime_uid,
    )
    if observed_agent_room_audit != expected_agent_room_audit:
        raise MigrationContractError("accepted shadow Agent Room audit result changed")
    if (
        _audit_nbs(generation_path / "nbs")
        != receipt["filesystem"]["nbs_full_store_audit_result"]
    ):
        raise MigrationContractError("accepted shadow NBS audit result changed")
    try:
        from seiche.palimpsest_china_activation import audit_activation_state

        palimpsest_audit = audit_activation_state(
            generation_path / "palimpsest-china",
            root_uid=os.geteuid(),
            root_gid=os.getegid(),
            api_uid=runtime_uid,
            api_gid=runtime_gid,
            declared_state_root=Path("/var/lib/seiche-palimpsest-china"),
        )
    except Exception as exc:
        raise MigrationContractError(
            "accepted shadow Palimpsest China audit failed"
        ) from exc
    audit_result = receipt["filesystem"]["palimpsest_china_state_audit_result"]
    if audit_result != "verified":
        raise MigrationContractError(
            "accepted shadow Palimpsest China audit result is invalid"
        )
    observed_state = palimpsest_china_state_from_audit(palimpsest_audit)
    expected_state = validate_palimpsest_china_state(
        receipt.get("palimpsest_china_state")
    )
    if observed_state != expected_state:
        raise MigrationContractError(
            "accepted shadow Palimpsest China state identity changed"
        )


def validate_active_generation(
    generation_path: Path,
    receipt: Mapping[str, Any],
    *,
    runtime_uid: int = RUNTIME_UID,
    runtime_gid: int = RUNTIME_GID,
) -> dict[str, Any]:
    """Validate a previously activated, intentionally mutable generation.

    Candidate receipts remain exact point-in-time proofs.  Once writer
    authority has moved, the API and market trees legitimately advance, so a
    production restart verifies their safe layout and semantic state instead
    of comparing them to stale byte hashes.  NBS and Palimpsest China remain
    immutable in this runtime and retain exact receipt-digest equality.
    """

    filesystem = receipt.get("filesystem")
    expected_generation = (
        filesystem.get("generation") if isinstance(filesystem, Mapping) else None
    )
    if (
        not isinstance(expected_generation, str)
        or generation_path.name != expected_generation
        or generation_path.parent.name != "generations"
        or not generation_path.is_dir()
        or generation_path.is_symlink()
    ):
        raise MigrationContractError("active generation path is invalid")
    names = {"market", "nbs", "api", "palimpsest-china"}
    try:
        entries = tuple(generation_path.iterdir())
    except OSError as exc:
        raise MigrationContractError("active generation is unavailable") from exc
    if {entry.name for entry in entries} != names or any(
        entry.is_symlink() or not entry.is_dir() for entry in entries
    ):
        raise MigrationContractError("active generation members are not closed")

    expected_trees = filesystem.get("tree_sha256")
    if not isinstance(expected_trees, Mapping) or set(expected_trees) != names:
        raise MigrationContractError("active generation receipt is invalid")
    # These two trees have no Railway runtime writer. Keep their original
    # byte-and-mode identity exact, while the market/API trees are checked by
    # their live semantic contracts below.
    immutable_names = ("nbs", "palimpsest-china")
    observed_immutable = {
        name: hash_tree(generation_path / name) for name in immutable_names
    }
    if any(observed_immutable[name] != expected_trees[name] for name in immutable_names):
        raise MigrationContractError("active immutable generation digest changed")

    # Walk both writable trees before opening their databases. This rejects
    # symlink, device, socket, FIFO, and hard-link substitutions without
    # pretending that legitimate new market observations have their old hash.
    writable_paths = {
        name: _walk_real_tree(generation_path / name)
        for name in ("market", "api")
    }
    for paths in writable_paths.values():
        for path in paths:
            metadata = path.lstat()
            if (
                metadata.st_uid != runtime_uid
                or metadata.st_gid != runtime_gid
                or stat.S_IMODE(metadata.st_mode) & 0o002
            ):
                raise MigrationContractError(
                    "active writable generation metadata is unsafe"
                )
    _validate_sqlite(generation_path / "api" / "seiche.sqlite")

    expected_agent_room = validate_agent_room_audit(
        filesystem.get("agent_room_audit")
    )
    observed_agent_room = audit_agent_room_state(
        generation_path / "api",
        expected_owner_uid=runtime_uid,
    )
    if expected_agent_room["result"] == "verified":
        expected_counts = tuple(
            int(expected_agent_room[name])
            for name in ("participant_count", "room_count", "event_count")
        )
        observed_counts = tuple(
            int(observed_agent_room[name])
            for name in ("participant_count", "room_count", "event_count")
        )
        if (
            observed_agent_room["result"] != "verified"
            or observed_agent_room["server_key_id"]
            != expected_agent_room["server_key_id"]
            or any(
                observed < expected
                for observed, expected in zip(
                    observed_counts,
                    expected_counts,
                    strict=True,
                )
            )
            or (
                observed_counts == expected_counts
                and observed_agent_room["state_sha256"]
                != expected_agent_room["state_sha256"]
            )
        ):
            raise MigrationContractError(
                "active Agent Room state does not extend its candidate state"
            )
    else:
        expected_key_id = expected_agent_room["server_key_id"]
        if observed_agent_room["result"] == "verified":
            if (
                expected_key_id is None
                or observed_agent_room["server_key_id"] != expected_key_id
            ):
                raise MigrationContractError(
                    "active Agent Room bootstrap identity is not receipt-bound"
                )
        elif expected_key_id is not None and observed_agent_room != expected_agent_room:
            raise MigrationContractError(
                "active Agent Room bootstrap key state changed"
            )

    if (
        _audit_nbs(generation_path / "nbs")
        != filesystem["nbs_full_store_audit_result"]
    ):
        raise MigrationContractError("active NBS audit result changed")
    try:
        from seiche.palimpsest_china_activation import audit_activation_state

        palimpsest_audit = audit_activation_state(
            generation_path / "palimpsest-china",
            root_uid=os.geteuid(),
            root_gid=os.getegid(),
            api_uid=runtime_uid,
            api_gid=runtime_gid,
            declared_state_root=Path("/var/lib/seiche-palimpsest-china"),
        )
    except Exception as exc:
        raise MigrationContractError(
            "active Palimpsest China audit failed"
        ) from exc
    if (
        filesystem.get("palimpsest_china_state_audit_result") != "verified"
        or palimpsest_china_state_from_audit(palimpsest_audit)
        != validate_palimpsest_china_state(receipt.get("palimpsest_china_state"))
    ):
        raise MigrationContractError(
            "active Palimpsest China state identity changed"
        )
    return observed_agent_room


def palimpsest_runtime_environment(
    state_root: Path,
    *,
    runtime_uid: int = RUNTIME_UID,
    runtime_gid: int = RUNTIME_GID,
) -> dict[str, str]:
    """Render runtime paths only from one fully audited restored state tree."""

    try:
        from seiche import palimpsest_china_activation as activation

        audit = activation.audit_activation_state(
            state_root,
            root_uid=os.geteuid(),
            root_gid=os.getegid(),
            api_uid=runtime_uid,
            api_gid=runtime_gid,
            declared_state_root=Path("/var/lib/seiche-palimpsest-china"),
        )
        paths = activation._activation_audit_paths(
            state_root,
            root_uid=os.geteuid(),
            root_gid=os.getegid(),
            api_uid=runtime_uid,
            api_gid=runtime_gid,
        )
        loaded = activation._read_active(
            paths,
            declared_receipts_dir=Path("/var/lib/seiche-palimpsest-china/receipts"),
        )
    except Exception as exc:
        raise MigrationContractError(
            "restored Palimpsest China runtime state is invalid"
        ) from exc
    if loaded is None:
        if audit["active_activation_id"] is not None:
            raise MigrationContractError(
                "restored Palimpsest China active state is inconsistent"
            )
        return {}
    active, _receipt = loaded
    if audit["active_activation_id"] != active["activation_id"]:
        raise MigrationContractError(
            "restored Palimpsest China active identity is inconsistent"
        )
    bundle = state_root / active["bundle_id"]
    return {
        spec.environment: str(bundle / spec.filename)
        for spec in activation._BUNDLE_FILE_SPECS
    }


def restore_shadow(
    request: Mapping[str, Any],
    bundle: BackupBundle,
    *,
    platform_root: Path,
    base_dsn: str,
    railway: Mapping[str, str],
    runtime_uid: int = RUNTIME_UID,
    runtime_gid: int = RUNTIME_GID,
) -> tuple[dict[str, Any], str, str]:
    started_at = _iso_now()
    if bundle.schema != BACKUP_SCHEMA or bundle.palimpsest_china_state_audit is None:
        raise MigrationContractError(
            "shadow receipt v4 requires the current Palimpsest-state backup contract"
        )
    for path in (platform_root, bundle.root):
        if not path.is_absolute() or path == Path("/") or path.is_symlink():
            raise MigrationContractError("stateful migration path is unsafe")
    _prepare_shared_directory(platform_root, gid=runtime_gid, parents=True)
    generations = platform_root / "generations"
    receipts = platform_root / "receipts"
    _prepare_shared_directory(generations, gid=runtime_gid)
    _prepare_shared_directory(receipts, gid=runtime_gid)
    generation_name = (
        f"{request['snapshot_id']}-{str(request['source_content_set_sha256'])[:16]}"
    )
    generation_path = generations / generation_name
    receipt_path = receipts / f"{request['request_id']}.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        body = _stable_read(receipt_path, maximum_bytes=256 * 1024)
        receipt = validate_receipt_document(
            _decode_canonical_json(body, label="shadow receipt"),
            request=request,
            railway=railway,
        )
        validate_receipted_generation(
            generation_path,
            receipt,
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
        )
        target_dsn = _target_dsn(base_dsn, receipt["database"]["name"])
        if inspect_postgres_counts(target_dsn) != tuple(
            receipt["database"]["critical_table_counts"]
        ):
            raise MigrationContractError("accepted shadow PostgreSQL counts changed")
        return receipt, target_dsn, "reused"
    if generation_path.exists() or generation_path.is_symlink():
        raise MigrationContractError(
            "unreceipted shadow generation needs reconciliation"
        )

    staging = platform_root / f".staging-{request['request_id']}"
    if staging.exists() or staging.is_symlink():
        if staging.is_symlink() or not staging.is_dir():
            raise MigrationContractError("stale migration staging path is unsafe")
        shutil.rmtree(staging)
    staging.mkdir(mode=0o700)
    try:
        agent_room_audit: dict[str, Any] = {}
        nbs_result, generation_digests = restore_filesystem_generation(
            bundle,
            staging,
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
            agent_room_audit_out=agent_room_audit,
        )
        database = restore_postgres(bundle, base_dsn)
        (staging / "generation").rename(generation_path)
        _fsync_directory(generations)
        receipt = render_receipt(
            request,
            bundle,
            database,
            generation_name=generation_name,
            generation_digests=generation_digests,
            nbs_audit_result=nbs_result,
            agent_room_audit=agent_room_audit,
            railway=railway,
            started_at=started_at,
            completed_at=_iso_now(),
        )
        validate_receipt_document(receipt, request=request, railway=railway)
        _write_receipt(receipt_path, receipt, gid=runtime_gid)
        return receipt, database.dsn, "created"
    finally:
        if staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)


def runtime_environment(
    base: Mapping[str, str],
    receipt: Mapping[str, Any],
    *,
    database_dsn: str,
    receipt_path: Path,
    runtime_uid: int = RUNTIME_UID,
    runtime_gid: int = RUNTIME_GID,
) -> dict[str, str]:
    generation = str(receipt["filesystem"]["generation"])
    root = PLATFORM_ROOT / "generations" / generation
    from seiche import palimpsest_china_activation as activation

    palimpsest_environment_names = {
        spec.environment for spec in activation._BUNDLE_FILE_SPECS
    }
    environment = {
        key: value
        for key, value in base.items()
        if key
        not in (
            {
                "DATABASE_URL",
                "RAILWAY_TOKEN",
                "RAILWAY_API_TOKEN",
                "PYTHONHOME",
                "PYTHONPATH",
                "SEICHE_RUNTIME_DATA_DIR",
                "SEICHE_AGENT_ROOM_DB_PATH",
                "SEICHE_ATTEST_DIR",
                "SEICHE_AGENT_ROOM_EXPECTED_KEY_ID",
            }
            | palimpsest_environment_names
        )
    }
    runtime_data = root / "api"
    environment.update(
        {
            "HOME": "/tmp/seiche-home",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": "/workspace/backend",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "SEICHE_ENV": "production",
            "SEICHE_RELEASE_SHA": str(receipt["request"]["commit"]),
            "SEICHE_DATABASE_URL": database_dsn,
            "SEICHE_RUNTIME_DATA_DIR": str(runtime_data),
            "SEICHE_AGENT_ROOM_DB_PATH": str(
                runtime_data / "_agent_room" / "agent-room.sqlite"
            ),
            "SEICHE_ATTEST_DIR": str(runtime_data / "_attest"),
            "SEICHE_AGENT_ROOM_EXPECTED_KEY_ID": agent_room_expected_key_binding(
                receipt["filesystem"].get("agent_room_audit")
            ),
            "SEICHE_RAW_CAPTURE_DIR": str(root / "market" / "raw"),
            "SEICHE_NORMALIZED_DIR": str(root / "market" / "normalized"),
            "SEICHE_BACKFILL_STATE_DIR": str(root / "market" / "backfill"),
            "SEICHE_VALIDATION_DIR": str(root / "market" / "validation"),
            "SEICHE_USD_FUNDING_CORE_EXPORT_DIR": str(
                root / "market" / "exports" / "us-usd-funding-core-v1"
            ),
            "SEICHE_NBS_ROOT": str(root / "nbs"),
            "SEICHE_NBS_PUBLIC_DIR": str(root / "nbs" / "public"),
            "SEICHE_RAILWAY_STATEFUL_MODE": "shadow",
            "SEICHE_RAILWAY_MIGRATION_RECEIPT_PATH": str(receipt_path),
            "SEICHE_RAILWAY_MIGRATION_REQUEST_ID": str(receipt["request"]["id"]),
            "SEICHE_RAILWAY_MIGRATION_RECEIPT_SHA256": _sha256_bytes(
                canonical_document(dict(receipt))
            ),
            "SEICHE_COLLECTOR_HEARTBEAT_REQUIRED": "0",
            "SEICHE_SOURCE_HEARTBEAT_REQUIRED": "0",
        }
    )
    environment.update(
        palimpsest_runtime_environment(
            root / "palimpsest-china",
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
        )
    )
    return environment


def validate_runtime_receipt(environment: Mapping[str, str]) -> dict[str, Any]:
    if environment.get("SEICHE_RAILWAY_STATEFUL_MODE") != "shadow":
        raise MigrationContractError("Railway stateful authority mode is invalid")
    path_text = environment.get("SEICHE_RAILWAY_MIGRATION_RECEIPT_PATH", "")
    path = Path(path_text)
    if (
        not path.is_absolute()
        or path.parent != PLATFORM_ROOT / "receipts"
        or path.suffix != ".json"
    ):
        raise MigrationContractError("Railway migration receipt path is invalid")
    body = _stable_read(path, maximum_bytes=256 * 1024)
    if _sha256_bytes(body) != environment.get(
        "SEICHE_RAILWAY_MIGRATION_RECEIPT_SHA256"
    ):
        raise MigrationContractError("Railway migration receipt digest changed")
    value = _decode_canonical_json(body, label="Railway migration receipt")
    if (
        value.get("schema") != RECEIPT_SCHEMA
        or value.get("request", {}).get("id")
        != environment.get("SEICHE_RAILWAY_MIGRATION_REQUEST_ID")
        or value.get("request", {}).get("commit")
        != environment.get("SEICHE_RELEASE_SHA")
        or value.get("authority", {}).get("mode") != "shadow"
        or value.get("authority", {}).get("public_traffic_enabled") is not False
        or value.get("authority", {}).get("workers_started") is not False
    ):
        raise MigrationContractError("Railway runtime receipt binding is invalid")
    validate_palimpsest_china_state(value.get("palimpsest_china_state"))
    agent_room_audit = validate_agent_room_audit(
        value.get("filesystem", {}).get("agent_room_audit")
    )
    if environment.get("SEICHE_AGENT_ROOM_EXPECTED_KEY_ID") != (
        agent_room_expected_key_binding(agent_room_audit)
    ):
        raise MigrationContractError("Railway Agent Room key binding is invalid")
    return value


def supervise_shadow(environment: Mapping[str, str]) -> int:
    port_text = environment.get("PORT", "")
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise MigrationContractError("Railway PORT is invalid")
    Path("/tmp/seiche-home").mkdir(mode=0o700, exist_ok=True)
    os.chown("/tmp/seiche-home", RUNTIME_UID, RUNTIME_GID)
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "seiche.api:app",
        "--host",
        "0.0.0.0",
        "--port",
        port_text,
        "--no-access-log",
    ]
    child = subprocess.Popen(
        command,
        cwd="/workspace",
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        user=RUNTIME_UID,
        group=RUNTIME_GID,
        extra_groups=[RUNTIME_GID],
    )
    stopping = False

    def stop_child(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGTERM, stop_child)
    signal.signal(signal.SIGINT, stop_child)
    try:
        returncode = child.wait()
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
    if stopping and returncode in {0, -signal.SIGTERM, -signal.SIGINT}:
        return 0
    return returncode or 1


def run_shadow() -> int:
    runtime_started_at = _iso_now()
    if os.geteuid() != 0 or os.getegid() != 0:
        raise MigrationContractError("stateful migration supervisor must start as root")
    request = load_request(REQUEST_PATH, SOURCE_ARCHIVE, SOURCE_BUNDLE)
    railway = railway_identity(os.environ)
    platform_root = Path(railway["volume_mount_path"])
    inbox = platform_root / "inbox" / str(request["snapshot_id"])
    maximum = int(os.getenv("SEICHE_RAILWAY_MIGRATION_MAX_BYTES", str(30 * 1024**3)))
    if not 1024**2 <= maximum <= 1024**4:
        raise MigrationContractError("migration byte bound is invalid")
    bundle = validate_bundle(inbox, request, maximum_total_bytes=maximum)
    base_dsn = os.getenv("DATABASE_URL", "").strip()
    if not base_dsn:
        raise MigrationContractError("Railway PostgreSQL URL is absent")
    receipt, database_dsn, lifecycle = restore_shadow(
        request,
        bundle,
        platform_root=platform_root,
        base_dsn=base_dsn,
        railway=railway,
        runtime_uid=RUNTIME_UID,
        runtime_gid=RUNTIME_GID,
    )
    receipt_path = platform_root / "receipts" / f"{request['request_id']}.json"
    environment = runtime_environment(
        os.environ,
        receipt,
        database_dsn=database_dsn,
        receipt_path=receipt_path,
        runtime_uid=RUNTIME_UID,
        runtime_gid=RUNTIME_GID,
    )
    validated_receipt = validate_runtime_receipt(environment)
    print(
        render_log_result(
            validated_receipt,
            lifecycle=lifecycle,
            environment=os.environ,
            runtime_started_at=runtime_started_at,
        ),
        flush=True,
    )
    return supervise_shadow(environment)


def _verify_cli(arguments: argparse.Namespace) -> int:
    request = load_request(
        Path(arguments.request),
        Path(arguments.source_archive),
        Path(arguments.source_bundle),
        now=datetime.now(UTC),
    )
    body = Path(arguments.receipt).read_bytes()
    receipt = _decode_canonical_json(body, label="downloaded shadow receipt")
    railway = {
        "deployment_id": os.environ["RAILWAY_DEPLOYMENT_ID"],
        "project_id": os.environ["RAILWAY_PROJECT_ID"],
        "environment_id": os.environ["RAILWAY_ENVIRONMENT_ID"],
        "service_id": os.environ["RAILWAY_SERVICE_ID"],
        "volume_id": os.environ["RAILWAY_VOLUME_ID"],
        "volume_name": os.environ["RAILWAY_VOLUME_NAME"],
        "volume_mount_path": str(PLATFORM_ROOT),
        "region": os.environ["RAILWAY_REPLICA_REGION"],
    }
    validate_receipt_document(receipt, request=request, railway=railway)
    print(_sha256_bytes(body))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    verify = subparsers.add_parser("verify-receipt")
    verify.add_argument("--request", required=True)
    verify.add_argument("--source-archive", required=True)
    verify.add_argument("--source-bundle", required=True)
    verify.add_argument("--receipt", required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "verify-receipt":
        return _verify_cli(arguments)
    return run_shadow()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationContractError as error:
        print(f"seiche Railway shadow: {error}", file=sys.stderr)
        time.sleep(1)
        raise SystemExit(1) from None
