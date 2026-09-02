"""Phase-6 portable recovery exports for Railway stateful production.

The production supervisor briefly pauses the two writer children, calls this
module to commit one backup-v4 generation, restarts the writers, and only then
publishes the recovery receipt.  Public API reads remain available throughout.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, NamedTuple

from seiche import stateful_cutover as cutover
from seiche import stateful_migration as migration

REQUEST_SCHEMA = "seiche.railway-recovery-export-request.v1"
RECEIPT_SCHEMA = "seiche.railway-recovery-export-receipt.v4"
OFFSITE_RECEIPT_SCHEMA = "seiche.railway-offsite-recovery-receipt.v3"
WORKFLOW = "beepboop2025/seiche/.github/workflows/railway-stateful-recovery.yml"
CONFIRMATION = "EXPORT_WITHOUT_AUTHORITY_CHANGE"
REQUEST_MAX_AGE = timedelta(minutes=30)
REQUEST_FUTURE_SKEW = timedelta(minutes=5)
MAX_SHADOW_RECEIPTS = 1024
MAX_SHADOW_RECEIPT_BYTES = 64 * 1024 * 1024

_SNAPSHOT_RE = re.compile(r"20[0-9]{6}T[0-9]{6}Z")
_SHA40_RE = re.compile(r"[0-9a-f]{40}")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-" r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_REQUEST_KEYS = frozenset(
    {
        "schema",
        "repository",
        "workflow",
        "commit",
        "deployment_id",
        "activation_receipt_sha256",
        "request_id",
        "snapshot_id",
        "requested_at",
        "confirmation",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        "schema",
        "repository",
        "workflow",
        "commit",
        "request_id",
        "request_sha256",
        "activation_receipt_sha256",
        "candidate_receipt_sha256",
        "shadow_receipt_sha256",
        "railway",
        "authority",
        "snapshot",
        "filesystem",
        "palimpsest_china_state",
        "timing",
        "workers",
        "research_only",
        "can_publish",
        "can_execute",
    }
)
_OFFSITE_RECEIPT_KEYS = frozenset(
    {
        "schema",
        "repository",
        "workflow",
        "commit",
        "request_id",
        "snapshot_id",
        "recovery_receipt_sha256",
        "reverse_restore_proof_sha256",
        "palimpsest_china_state",
        "bucket",
        "prefix",
        "object_lock_mode",
        "retain_until",
        "objects",
        "sealed_at",
        "authority_changed",
        "research_only",
        "can_publish",
        "can_execute",
    }
)
_OFFSITE_OBJECTS = frozenset(
    {
        "activation-receipt.json",
        "candidate-receipt.json",
        "shadow-receipt.json",
        "request.json",
        "recovery-receipt.json",
        *migration._ALL_BACKUP_MEMBERS,
        "proof/reverse-restore.json",
    }
)


class RecoveryContractError(RuntimeError):
    """Raised when recovery state is ambiguous or not content-bound."""


class RecoveryExport(NamedTuple):
    bundle: migration.BackupBundle
    nbs_audit_result: str
    agent_room_audit: Mapping[str, Any]
    tree_sha256: Mapping[str, str]
    palimpsest_china_state: Mapping[str, Any]
    started_at: str
    completed_at: str


def _canonical(value: Mapping[str, Any]) -> bytes:
    return migration.canonical_document(dict(value))


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _validate_digest(value: object, *, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise RecoveryContractError(f"{label} is invalid")
    return value


def _palimpsest_china_state(value: object) -> dict[str, Any]:
    try:
        return migration.validate_palimpsest_china_state(value)
    except migration.MigrationContractError as exc:
        raise RecoveryContractError(str(exc)) from exc


def _palimpsest_china_state_from_bundle(
    bundle: migration.BackupBundle,
) -> dict[str, Any]:
    try:
        return migration.palimpsest_china_state_from_audit(
            bundle.palimpsest_china_state_audit
        )
    except migration.MigrationContractError as exc:
        raise RecoveryContractError(str(exc)) from exc


def _utc(value: object, *, label: str) -> datetime:
    try:
        return migration._utc_timestamp(value, label=label)
    except migration.MigrationContractError as exc:
        raise RecoveryContractError(str(exc)) from exc


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_canonical(
    path: Path, *, label: str, maximum_bytes: int
) -> tuple[bytes, dict[str, Any]]:
    try:
        body = migration._stable_read(path, maximum_bytes=maximum_bytes)
        return body, migration._decode_canonical_json(body, label=label)
    except migration.MigrationContractError as exc:
        raise RecoveryContractError(str(exc)) from exc


def validate_request(
    value: object,
    *,
    activation_receipt: Mapping[str, Any],
    now: datetime | None = None,
    require_fresh: bool = True,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _REQUEST_KEYS:
        raise RecoveryContractError("recovery export request fields are invalid")
    if (
        value.get("schema") != REQUEST_SCHEMA
        or value.get("repository") != migration.REPOSITORY
        or value.get("workflow") != WORKFLOW
        or value.get("confirmation") != CONFIRMATION
        or value.get("commit") != activation_receipt.get("commit")
        or value.get("deployment_id")
        != activation_receipt.get("railway", {}).get("deployment_id")
        or value.get("activation_receipt_sha256")
        != _digest(_canonical(activation_receipt))
    ):
        raise RecoveryContractError("recovery export request binding is invalid")
    _validate_digest(value.get("request_id"), label="recovery request id")
    _validate_digest(
        value.get("activation_receipt_sha256"),
        label="activation receipt digest",
    )
    if (
        not isinstance(value.get("commit"), str)
        or _SHA40_RE.fullmatch(value["commit"]) is None
        or not isinstance(value.get("deployment_id"), str)
        or _UUID_RE.fullmatch(value["deployment_id"]) is None
    ):
        raise RecoveryContractError("recovery export identity is invalid")
    snapshot_id = value.get("snapshot_id")
    if not isinstance(snapshot_id, str) or _SNAPSHOT_RE.fullmatch(snapshot_id) is None:
        raise RecoveryContractError("recovery snapshot identity is invalid")
    requested_at = _utc(value.get("requested_at"), label="recovery requested_at")
    snapshot_at = datetime.strptime(snapshot_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    observed = now or datetime.now(UTC)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise RecoveryContractError("recovery request clock is not timezone-aware")
    observed = observed.astimezone(UTC).replace(microsecond=0)
    if abs(requested_at - snapshot_at) > timedelta(minutes=5):
        raise RecoveryContractError("recovery request is not bound to its snapshot")
    if requested_at > observed + REQUEST_FUTURE_SKEW or (
        require_fresh and requested_at < observed - REQUEST_MAX_AGE
    ):
        raise RecoveryContractError("recovery export request is not fresh")
    return dict(value)


def activation_context(environment: Mapping[str, str]) -> tuple[bytes, dict[str, Any]]:
    try:
        activation = cutover.validate_activation_runtime(environment)
    except cutover.CutoverContractError as exc:
        raise RecoveryContractError(str(exc)) from exc
    path = Path(environment.get("SEICHE_RAILWAY_ACTIVATION_RECEIPT_PATH", ""))
    body, value = _load_canonical(
        path,
        label="activation receipt",
        maximum_bytes=256 * 1024,
    )
    if value != activation or _digest(body) != environment.get(
        "SEICHE_RAILWAY_ACTIVATION_RECEIPT_SHA256"
    ):
        raise RecoveryContractError("recovery activation receipt differs from runtime")
    return body, value


def validate_candidate_chain(
    value: object,
    *,
    activation_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the activation-bound v3 candidate identity used by recovery."""

    expected_keys = {
        "schema",
        "request",
        "authority",
        "fence",
        "bundle",
        "database",
        "filesystem",
        "palimpsest_china_state",
        "railway",
        "timing",
        "research_only",
        "can_publish",
        "can_execute",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise RecoveryContractError("recovery candidate receipt fields are invalid")
    candidate_request = value.get("request")
    if (
        value.get("schema") != cutover.CANDIDATE_RECEIPT_SCHEMA
        or activation_receipt.get("candidate_receipt_sha256")
        != _digest(_canonical(value))
        or not isinstance(candidate_request, dict)
        or set(candidate_request)
        != {
            "id",
            "sha256",
            "commit",
            "tree",
            "source_shadow_receipt_sha256",
        }
        or candidate_request.get("id") != activation_receipt.get("request_id")
        or candidate_request.get("commit") != activation_receipt.get("commit")
        or value.get("railway") != activation_receipt.get("railway")
        or value.get("authority")
        != {
            "mode": "cutover_candidate",
            "source": "none",
            "hetzner_writers_frozen": True,
            "railway_writers_started": False,
            "public_traffic_enabled": False,
        }
        or value.get("research_only") is not True
        or value.get("can_publish") is not False
        or value.get("can_execute") is not False
    ):
        raise RecoveryContractError("recovery candidate receipt binding is invalid")
    _validate_digest(
        candidate_request.get("source_shadow_receipt_sha256"),
        label="candidate source shadow receipt",
    )
    for name in ("id", "sha256"):
        _validate_digest(candidate_request.get(name), label=f"candidate request {name}")
    for name in ("commit", "tree"):
        if (
            not isinstance(candidate_request.get(name), str)
            or _SHA40_RE.fullmatch(candidate_request[name]) is None
        ):
            raise RecoveryContractError(f"candidate request {name} is invalid")
    try:
        migration.validate_agent_room_audit(
            value.get("filesystem", {}).get("agent_room_audit")
        )
    except migration.MigrationContractError as exc:
        raise RecoveryContractError(str(exc)) from exc
    _palimpsest_china_state(value.get("palimpsest_china_state"))
    return dict(value)


def candidate_context(
    environment: Mapping[str, str],
    *,
    activation_receipt: Mapping[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    path = Path(environment.get("SEICHE_RAILWAY_CANDIDATE_RECEIPT_PATH", ""))
    if (
        not path.is_absolute()
        or path.parent != migration.PLATFORM_ROOT / "cutover-receipts"
        or not path.name.endswith(".candidate.json")
    ):
        raise RecoveryContractError("recovery candidate receipt path is invalid")
    body, value = _load_canonical(
        path,
        label="candidate receipt",
        maximum_bytes=256 * 1024,
    )
    if _digest(body) != activation_receipt.get("candidate_receipt_sha256") or _digest(
        body
    ) != environment.get("SEICHE_RAILWAY_CANDIDATE_RECEIPT_SHA256"):
        raise RecoveryContractError("recovery candidate receipt digest differs")
    return body, validate_candidate_chain(
        value,
        activation_receipt=activation_receipt,
    )


def validate_shadow_chain(
    value: object,
    *,
    candidate_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    expected_keys = {
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
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise RecoveryContractError("recovery shadow receipt fields are invalid")
    candidate_request = candidate_receipt.get("request", {})
    expected_digest = candidate_request.get("source_shadow_receipt_sha256")
    shadow_request = value.get("request")
    if (
        value.get("schema") != migration.RECEIPT_SCHEMA
        or _digest(_canonical(value)) != expected_digest
        or not isinstance(shadow_request, dict)
        or set(shadow_request)
        != {
            "id",
            "sha256",
            "commit",
            "tree",
            "source_archive_sha256",
            "source_bundle_sha256",
            "source_release_receipt_sha256",
            "source_recovery_receipt_sha256",
        }
        or shadow_request.get("commit") != candidate_request.get("commit")
        or value.get("authority")
        != {
            "mode": "shadow",
            "source": "hetzner",
            "source_writers_frozen": False,
            "public_traffic_enabled": False,
            "workers_started": False,
        }
        or value.get("research_only") is not True
        or value.get("can_publish") is not False
        or value.get("can_execute") is not False
    ):
        raise RecoveryContractError("recovery shadow receipt binding is invalid")
    for name in (
        "id",
        "sha256",
        "source_archive_sha256",
        "source_bundle_sha256",
        "source_release_receipt_sha256",
        "source_recovery_receipt_sha256",
    ):
        _validate_digest(shadow_request.get(name), label=f"shadow request {name}")
    for name in ("commit", "tree"):
        if (
            not isinstance(shadow_request.get(name), str)
            or _SHA40_RE.fullmatch(shadow_request[name]) is None
        ):
            raise RecoveryContractError(f"shadow request {name} is invalid")
    shadow_state = _palimpsest_china_state(value.get("palimpsest_china_state"))
    candidate_state = _palimpsest_china_state(
        candidate_receipt.get("palimpsest_china_state")
    )
    if shadow_state != candidate_state:
        raise RecoveryContractError(
            "cutover Palimpsest China state differs from shadow"
        )
    try:
        shadow_agent_room_audit = migration.validate_agent_room_audit(
            value.get("filesystem", {}).get("agent_room_audit")
        )
        candidate_agent_room_audit = migration.validate_agent_room_audit(
            candidate_receipt.get("filesystem", {}).get("agent_room_audit")
        )
    except migration.MigrationContractError as exc:
        raise RecoveryContractError(str(exc)) from exc
    if shadow_agent_room_audit != candidate_agent_room_audit:
        raise RecoveryContractError("cutover Agent Room state differs from shadow")
    return dict(value)


def shadow_context(
    *,
    candidate_receipt: Mapping[str, Any],
    platform_root: Path | None = None,
) -> tuple[bytes, dict[str, Any]]:
    receipts = (platform_root or migration.PLATFORM_ROOT) / "receipts"
    if not receipts.is_dir() or receipts.is_symlink():
        raise RecoveryContractError("shadow receipt directory is unsafe")
    entries = sorted(receipts.iterdir(), key=lambda path: path.name)
    if len(entries) > MAX_SHADOW_RECEIPTS:
        raise RecoveryContractError("shadow receipt directory is unbounded")
    expected_digest = candidate_receipt.get("request", {}).get(
        "source_shadow_receipt_sha256"
    )
    _validate_digest(expected_digest, label="candidate source shadow receipt")
    matches: list[tuple[bytes, dict[str, Any]]] = []
    total_bytes = 0
    for path in entries:
        if re.fullmatch(r"[0-9a-f]{64}\.json", path.name) is None:
            raise RecoveryContractError("shadow receipt directory is not closed")
        body, value = _load_canonical(
            path,
            label="shadow receipt",
            maximum_bytes=256 * 1024,
        )
        total_bytes += len(body)
        if total_bytes > MAX_SHADOW_RECEIPT_BYTES:
            raise RecoveryContractError("shadow receipt directory exceeds capacity")
        if _digest(body) == expected_digest:
            matches.append((body, value))
    if len(matches) != 1:
        raise RecoveryContractError("activation-bound shadow receipt is not unique")
    body, value = matches[0]
    return body, validate_shadow_chain(
        value,
        candidate_receipt=candidate_receipt,
    )


def publish_request(
    body: bytes,
    environment: Mapping[str, str],
    *,
    platform_root: Path | None = None,
    now: datetime | None = None,
    runtime_gid: int = migration.RUNTIME_GID,
) -> dict[str, Any]:
    try:
        value = migration._decode_canonical_json(body, label="recovery request")
    except migration.MigrationContractError as exc:
        raise RecoveryContractError(str(exc)) from exc
    _activation_body, activation = activation_context(environment)
    request = validate_request(value, activation_receipt=activation, now=now)
    root = platform_root or migration.PLATFORM_ROOT
    requests = root / "recovery-requests"
    cutover._prepare_authority_directory(requests, runtime_gid=runtime_gid)
    cutover._publish_immutable_authority_file(
        requests,
        f"{request['request_id']}.json",
        body,
        runtime_gid=runtime_gid,
    )
    return request


def next_pending_request(
    environment: Mapping[str, str],
    *,
    platform_root: Path | None = None,
    now: datetime | None = None,
    runtime_gid: int = migration.RUNTIME_GID,
) -> dict[str, Any] | None:
    root = platform_root or migration.PLATFORM_ROOT
    requests = root / "recovery-requests"
    receipts = root / "recovery-receipts"
    cutover._prepare_authority_directory(requests, runtime_gid=runtime_gid)
    cutover._prepare_authority_directory(receipts, runtime_gid=runtime_gid)
    _activation_body, activation = activation_context(environment)
    for path in sorted(requests.iterdir(), key=lambda item: item.name):
        if re.fullmatch(r"[0-9a-f]{64}\.json", path.name) is None:
            raise RecoveryContractError("recovery request directory is not closed")
        request_id = path.stem
        _body, value = _load_canonical(
            path,
            label="recovery request",
            maximum_bytes=32 * 1024,
        )
        request = validate_request(
            value,
            activation_receipt=activation,
            now=now,
            require_fresh=False,
        )
        receipt_path = receipts / (f"{request['snapshot_id']}-{request_id}.json")
        if receipt_path.exists() or receipt_path.is_symlink():
            continue
        observed = now or datetime.now(UTC)
        if _utc(request["requested_at"], label="recovery requested_at") < (
            observed.astimezone(UTC).replace(microsecond=0) - REQUEST_MAX_AGE
        ):
            continue
        return request
    return None


def generation_root(environment: Mapping[str, str]) -> Path:
    api = Path(environment.get("SEICHE_RUNTIME_DATA_DIR", ""))
    market_raw = Path(environment.get("SEICHE_RAW_CAPTURE_DIR", ""))
    nbs = Path(environment.get("SEICHE_NBS_ROOT", ""))
    if not api.is_absolute() or api.name != "api":
        raise RecoveryContractError("recovery API generation path is invalid")
    root = api.parent
    if (
        root.parent.name != "generations"
        or market_raw != root / "market" / "raw"
        or nbs != root / "nbs"
        or root.is_symlink()
        or not root.is_dir()
    ):
        raise RecoveryContractError("recovery generation paths do not agree")
    return root


def _write_all(path: Path, body: bytes, *, mode: int = 0o600) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        offset = 0
        while offset < len(body):
            count = os.write(descriptor, body[offset:])
            if count <= 0:
                raise OSError("recovery file write made no progress")
            offset += count
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(descriptor)


def _archive_roots(destination: Path, roots: Mapping[str, Path]) -> None:
    for root in roots.values():
        try:
            migration._walk_real_tree(root)
        except migration.MigrationContractError as exc:
            raise RecoveryContractError(str(exc)) from exc
    try:
        with tarfile.open(destination, mode="w:gz", compresslevel=9) as archive:
            for arcname, root in roots.items():
                archive.add(root, arcname=arcname, recursive=True)
    except (OSError, tarfile.TarError) as exc:
        raise RecoveryContractError("recovery archive creation failed") from exc


def _snapshot_api(
    source: Path,
    destination: Path,
    *,
    source_owner_uid: int | None = None,
) -> Mapping[str, Any]:
    expected_source_uid = os.geteuid() if source_owner_uid is None else source_owner_uid
    if destination.exists() or destination.is_symlink():
        raise RecoveryContractError("recovery API staging path already exists")
    shutil.copytree(source, destination, symlinks=True)
    for suffix in ("", "-wal", "-shm"):
        path = destination / f"seiche.sqlite{suffix}"
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    source_database = source / "seiche.sqlite"
    if source_database.is_symlink() or not source_database.is_file():
        raise RecoveryContractError("recovery API SQLite source is unsafe")
    target_database = destination / "seiche.sqlite"
    try:
        with sqlite3.connect(f"file:{source_database}?mode=ro", uri=True) as live:
            with sqlite3.connect(target_database) as snapshot:
                live.backup(snapshot)
                if snapshot.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise RecoveryContractError("recovery SQLite backup is corrupt")
        source_room_root = source / "_agent_room"
        source_room_database = source_room_root / "agent-room.sqlite"
        destination_room_root = destination / "_agent_room"
        if destination_room_root.is_dir() and not destination_room_root.is_symlink():
            shutil.rmtree(destination_room_root)
        elif destination_room_root.exists() or destination_room_root.is_symlink():
            raise RecoveryContractError("recovery Agent Room staging path is unsafe")
        if source_room_database.exists() or source_room_database.is_symlink():
            root_metadata = source_room_root.lstat()
            database_metadata = source_room_database.lstat()
            room_members = {entry.name for entry in source_room_root.iterdir()}
            if (
                source_room_root.is_symlink()
                or not source_room_root.is_dir()
                or root_metadata.st_uid != expected_source_uid
                or stat.S_IMODE(root_metadata.st_mode) & 0o022
                or source_room_database.is_symlink()
                or not stat.S_ISREG(database_metadata.st_mode)
                or database_metadata.st_nlink != 1
                or database_metadata.st_uid != expected_source_uid
                or stat.S_IMODE(database_metadata.st_mode) & 0o077
                or not {"agent-room.sqlite"} <= room_members
                or not room_members
                <= {"agent-room.sqlite", "agent-room.sqlite-journal"}
            ):
                raise RecoveryContractError("recovery Agent Room source is unsafe")
            destination_room_root.mkdir(mode=0o700)
            destination_room_root.chmod(0o700)
            target_room_database = destination_room_root / "agent-room.sqlite"
            with sqlite3.connect(
                f"file:{source_room_database}?mode=ro", uri=True
            ) as live_room:
                with sqlite3.connect(target_room_database) as snapshot_room:
                    live_room.backup(snapshot_room)
                    if snapshot_room.execute("PRAGMA quick_check").fetchone() != (
                        "ok",
                    ):
                        raise RecoveryContractError(
                            "recovery Agent Room SQLite backup is corrupt"
                        )
            target_room_database.chmod(0o600)
            after = source_room_database.lstat()
            if (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_nlink,
                after.st_uid,
            ) != (
                database_metadata.st_dev,
                database_metadata.st_ino,
                database_metadata.st_mode,
                database_metadata.st_nlink,
                database_metadata.st_uid,
            ):
                raise RecoveryContractError(
                    "recovery Agent Room source changed during snapshot"
                )
        elif source_room_root.exists() or source_room_root.is_symlink():
            root_metadata = source_room_root.lstat()
            if (
                source_room_root.is_symlink()
                or not source_room_root.is_dir()
                or root_metadata.st_uid != expected_source_uid
                or stat.S_IMODE(root_metadata.st_mode) & 0o022
                or any(source_room_root.iterdir())
            ):
                raise RecoveryContractError(
                    "recovery Agent Room source contains partial state"
                )
        migration._walk_real_tree(destination)
    except RecoveryContractError:
        raise
    except (sqlite3.Error, migration.MigrationContractError) as exc:
        raise RecoveryContractError("recovery SQLite backup failed") from exc
    return migration.audit_agent_room_state(destination)


def _dump_postgres(destination: Path, database_dsn: str) -> None:
    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")
    if not pg_dump or not pg_restore or not database_dsn:
        raise RecoveryContractError("PostgreSQL recovery tools are unavailable")
    completed = subprocess.run(
        [
            pg_dump,
            "--format=custom",
            "--compress=9",
            "--no-owner",
            "--no-privileges",
            f"--file={destination}",
            f"--dbname={database_dsn}",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=7200,
        check=False,
    )
    if completed.returncode != 0:
        raise RecoveryContractError("PostgreSQL recovery dump failed")
    metadata = destination.lstat()
    if not destination.is_file() or destination.is_symlink() or metadata.st_size < 1024:
        raise RecoveryContractError("PostgreSQL recovery dump is implausibly small")
    listed = subprocess.run(
        [pg_restore, "--list", str(destination)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=300,
        check=False,
    )
    if listed.returncode != 0:
        raise RecoveryContractError("PostgreSQL recovery dump cannot be listed")


def _bundle_identity(
    root: Path, *, snapshot_id: str, commit: str
) -> migration.BackupBundle:
    try:
        inventory = migration._stable_read(root / "SHA256SUMS", maximum_bytes=4096)
    except migration.MigrationContractError as exc:
        raise RecoveryContractError(str(exc)) from exc
    inventory_sha256 = _digest(inventory)
    digests: dict[str, str] = {}
    content = hashlib.sha256()
    content_bytes = 0
    for name in migration._BACKUP_MEMBERS:
        path = root / name
        try:
            digest = migration.sha256_file(path)
            size = path.stat().st_size
        except (OSError, migration.MigrationContractError) as exc:
            raise RecoveryContractError(
                "recovery bundle member is unavailable"
            ) from exc
        digests[name] = digest
        content_bytes += size
        content.update(name.encode("ascii") + b"\0")
        content.update(digest.encode("ascii") + b"\0")
        content.update(str(size).encode("ascii") + b"\n")
    request = {
        "snapshot_id": snapshot_id,
        "source_revision": commit,
        "source_inventory_sha256": inventory_sha256,
        "source_content_set_sha256": content.hexdigest(),
    }
    try:
        bundle = migration.validate_bundle(root, request)
        migration.validate_tar_contract(
            root / "var-lib-seiche.tgz",
            expected_roots=frozenset({"seiche", "seiche-nbs"}),
        )
        migration.validate_tar_contract(
            root / "api-data.tgz",
            expected_roots=frozenset({"api-data"}),
        )
        migration.validate_tar_contract(
            root / "palimpsest-china.tgz",
            expected_roots=frozenset({"seiche-palimpsest-china"}),
        )
    except migration.MigrationContractError as exc:
        raise RecoveryContractError(str(exc)) from exc
    if bundle.total_bytes != content_bytes or bundle.member_sha256 != digests:
        raise RecoveryContractError("recovery bundle identity is unstable")
    return bundle


def _restored_filesystem_identity(
    bundle: migration.BackupBundle,
    *,
    scratch_parent: Path,
    runtime_uid: int = migration.RUNTIME_UID,
    runtime_gid: int = migration.RUNTIME_GID,
) -> tuple[str, Mapping[str, str], Mapping[str, Any]]:
    scratch = Path(tempfile.mkdtemp(prefix=".recovery-inspect.", dir=scratch_parent))
    try:
        try:
            agent_room_audit: dict[str, Any] = {}
            nbs_result, tree_digests = migration.restore_filesystem_generation(
                bundle,
                scratch,
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
                agent_room_audit_out=agent_room_audit,
            )
            return nbs_result, tree_digests, agent_room_audit
        except migration.MigrationContractError as exc:
            raise RecoveryContractError(str(exc)) from exc
    finally:
        if scratch.is_dir() and not scratch.is_symlink():
            shutil.rmtree(scratch)


def export_snapshot(
    environment: Mapping[str, str],
    request: Mapping[str, Any],
    *,
    platform_root: Path | None = None,
    runtime_uid: int = migration.RUNTIME_UID,
    runtime_gid: int = migration.RUNTIME_GID,
) -> RecoveryExport:
    root = platform_root or migration.PLATFORM_ROOT
    _activation_body, activation = activation_context(environment)
    _candidate_body, candidate = candidate_context(
        environment,
        activation_receipt=activation,
    )
    _shadow_body, _shadow = shadow_context(
        candidate_receipt=candidate,
        platform_root=root,
    )
    expected_palimpsest_state = _palimpsest_china_state(
        candidate.get("palimpsest_china_state")
    )
    validated = validate_request(request, activation_receipt=activation)
    snapshot_id = str(validated["snapshot_id"])
    snapshots = root / "recovery-snapshots"
    snapshots.mkdir(mode=0o750, parents=True, exist_ok=True)
    if snapshots.is_symlink() or not snapshots.is_dir():
        raise RecoveryContractError("recovery snapshot root is unsafe")
    final = snapshots / snapshot_id
    if final.exists() or final.is_symlink():
        started_at = _iso_now()
        bundle = _bundle_identity(
            final,
            snapshot_id=snapshot_id,
            commit=str(validated["commit"]),
        )
        nbs_result, tree_digests, agent_room_audit = _restored_filesystem_identity(
            bundle,
            scratch_parent=snapshots,
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
        )
        observed_palimpsest_state = _palimpsest_china_state_from_bundle(bundle)
        if observed_palimpsest_state != expected_palimpsest_state:
            raise RecoveryContractError(
                "recovery snapshot Palimpsest China state differs from candidate"
            )
        return RecoveryExport(
            bundle,
            nbs_result,
            agent_room_audit,
            tree_digests,
            observed_palimpsest_state,
            started_at,
            _iso_now(),
        )
    stage: Path | None = Path(
        tempfile.mkdtemp(prefix=f".stage-{snapshot_id}.", dir=snapshots)
    )
    started_at = _iso_now()
    api_stage = stage / ".api-stage"
    try:
        generation = generation_root(environment)
        market = generation / "market"
        nbs = generation / "nbs"
        api = generation / "api"
        palimpsest = generation / "palimpsest-china"
        try:
            nbs_result = migration._audit_nbs(nbs)
            from seiche.palimpsest_china_activation import audit_activation_state

            palimpsest_audit = audit_activation_state(
                palimpsest,
                root_uid=os.geteuid(),
                root_gid=os.getegid(),
                api_uid=runtime_uid,
                api_gid=runtime_gid,
                declared_state_root=Path("/var/lib/seiche-palimpsest-china"),
            )
            tree_digests = {
                "market": migration.hash_tree(market),
                "nbs": migration.hash_tree(nbs),
                "palimpsest-china": migration.hash_tree(palimpsest),
            }
        except Exception as exc:
            raise RecoveryContractError(str(exc)) from exc
        observed_palimpsest_state = _palimpsest_china_state(
            {
                "audit_schema": palimpsest_audit.get("schema"),
                "tree_sha256": palimpsest_audit.get("tree_sha256"),
                "active_activation_id": palimpsest_audit.get("active_activation_id"),
                "pending_candidate_activation_id": palimpsest_audit.get(
                    "pending_candidate_activation_id"
                ),
            }
        )
        if observed_palimpsest_state != expected_palimpsest_state:
            raise RecoveryContractError(
                "live Palimpsest China state differs from cutover candidate"
            )
        counts_before = migration.inspect_postgres_counts(
            environment.get("SEICHE_DATABASE_URL", "")
        )
        _dump_postgres(
            stage / "seiche.dump", environment.get("SEICHE_DATABASE_URL", "")
        )
        counts_after = migration.inspect_postgres_counts(
            environment.get("SEICHE_DATABASE_URL", "")
        )
        if counts_after != counts_before:
            raise RecoveryContractError(
                "critical PostgreSQL counts changed while writers paused"
            )
        _archive_roots(
            stage / "var-lib-seiche.tgz",
            {"seiche": market, "seiche-nbs": nbs},
        )
        _archive_roots(
            stage / "palimpsest-china.tgz",
            {"seiche-palimpsest-china": palimpsest},
        )
        _write_all(
            stage / "palimpsest-china-state.json",
            migration.canonical_document(palimpsest_audit),
        )
        staged_agent_room_audit = _snapshot_api(
            api,
            api_stage,
            source_owner_uid=runtime_uid,
        )
        tree_digests["api"] = migration.hash_tree(api_stage)
        _archive_roots(stage / "api-data.tgz", {"api-data": api_stage})
        shutil.rmtree(api_stage)
        _write_all(
            stage / "table-counts.txt",
            ("|".join(str(item) for item in counts_before) + "\n").encode("ascii"),
        )
        _write_all(
            stage / "deployed-sha.txt",
            (str(validated["commit"]) + "\n").encode("ascii"),
        )
        manifest = (
            "\n".join(
                (
                    f"schema={migration.BACKUP_SCHEMA}",
                    f"created_at={snapshot_id}",
                    "database=seiche",
                    "postgres_port=5432",
                    "state_root=/var/lib/seiche",
                    "nbs_state_root=/var/lib/seiche-nbs",
                    "nbs_full_store_audit_contract=seiche.nbs-full-store-audit.v1",
                    "nbs_full_store_audit_result=required_at_restore",
                    "api_data_root=/home/seiche/app/backend/data",
                    "critical_table_count_semantics=pre_dump_lower_bound",
                    "palimpsest_china_state_root=/var/lib/seiche-palimpsest-china",
                    "palimpsest_china_state_audit_contract=seiche.palimpsest-china-activation-state.v1",
                    "palimpsest_china_state_audit_result=required_at_restore",
                    "research_only=true",
                    "can_publish=false",
                    "can_execute=false",
                )
            )
            + "\n"
        )
        _write_all(stage / "manifest.env", manifest.encode("utf-8"))
        inventory = b"".join(
            f"{migration.sha256_file(stage / name)}  {name}\n".encode("ascii")
            for name in migration._BACKUP_MEMBERS
        )
        _write_all(stage / "SHA256SUMS", inventory)
        for path in stage.iterdir():
            if path.is_file() and not path.is_symlink():
                os.chmod(path, 0o440)
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
        migration._fsync_directory(stage)
        bundle = _bundle_identity(
            stage,
            snapshot_id=snapshot_id,
            commit=str(validated["commit"]),
        )
        restored_nbs_result, restored_tree_digests, restored_agent_room_audit = (
            _restored_filesystem_identity(
                bundle,
                scratch_parent=snapshots,
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
            )
        )
        if (
            restored_nbs_result != nbs_result
            or restored_agent_room_audit != staged_agent_room_audit
        ):
            raise RecoveryContractError(
                "recovery archive restore proof differs from staged state"
            )
        stage.rename(final)
        migration._fsync_directory(snapshots)
        stage = None
        bundle = _bundle_identity(
            final,
            snapshot_id=snapshot_id,
            commit=str(validated["commit"]),
        )
        return RecoveryExport(
            bundle,
            restored_nbs_result,
            restored_agent_room_audit,
            restored_tree_digests,
            observed_palimpsest_state,
            started_at,
            _iso_now(),
        )
    finally:
        if stage is not None and stage.is_dir() and not stage.is_symlink():
            shutil.rmtree(stage)


def render_receipt(
    request: Mapping[str, Any],
    activation_receipt: Mapping[str, Any],
    candidate_receipt: Mapping[str, Any],
    shadow_receipt: Mapping[str, Any],
    export: RecoveryExport,
    *,
    railway: Mapping[str, str],
    writers_stopped_at: str,
    writers_restarted_at: str,
    worker_commands: Mapping[str, list[str]],
) -> dict[str, Any]:
    bundle = export.bundle
    candidate = validate_candidate_chain(
        candidate_receipt,
        activation_receipt=activation_receipt,
    )
    validate_shadow_chain(
        shadow_receipt,
        candidate_receipt=candidate,
    )
    palimpsest_china_state = _palimpsest_china_state(export.palimpsest_china_state)
    if palimpsest_china_state != candidate["palimpsest_china_state"]:
        raise RecoveryContractError(
            "recovery export Palimpsest China state differs from candidate"
        )
    return {
        "schema": RECEIPT_SCHEMA,
        "repository": migration.REPOSITORY,
        "workflow": WORKFLOW,
        "commit": request["commit"],
        "request_id": request["request_id"],
        "request_sha256": _digest(_canonical(request)),
        "activation_receipt_sha256": _digest(_canonical(activation_receipt)),
        "candidate_receipt_sha256": _digest(_canonical(candidate)),
        "shadow_receipt_sha256": _digest(_canonical(shadow_receipt)),
        "railway": dict(railway),
        "authority": {
            "source": "railway",
            "authority_changed": False,
            "public_api_remained_online": True,
            "writers_paused_for_export": True,
            "writers_restarted": True,
        },
        "snapshot": {
            "id": bundle.snapshot_id,
            "relative_path": f"recovery-snapshots/{bundle.snapshot_id}",
            "backup_schema": migration.BACKUP_SCHEMA,
            "source_revision": bundle.source_revision,
            "inventory_sha256": bundle.inventory_sha256,
            "content_set_sha256": bundle.content_set_sha256,
            "member_sha256": dict(bundle.member_sha256),
            "critical_table_count_floor": list(bundle.counts_floor),
            "total_bytes": bundle.total_bytes,
        },
        "filesystem": {
            "tree_sha256": dict(export.tree_sha256),
            "nbs_full_store_audit_result": export.nbs_audit_result,
            "agent_room_audit": migration.validate_agent_room_audit(
                export.agent_room_audit
            ),
            "palimpsest_china_state_audit_contract": (
                "seiche.palimpsest-china-activation-state.v1"
            ),
            "palimpsest_china_state_audit_result": "verified",
        },
        "palimpsest_china_state": palimpsest_china_state,
        "timing": {
            "requested_at": request["requested_at"],
            "writers_stopped_at": writers_stopped_at,
            "export_started_at": export.started_at,
            "export_completed_at": export.completed_at,
            "writers_restarted_at": writers_restarted_at,
        },
        "workers": {
            name: {"command": command, "restarted": True}
            for name, command in worker_commands.items()
        },
        "research_only": True,
        "can_publish": False,
        "can_execute": False,
    }


def validate_receipt(
    value: object,
    *,
    request: Mapping[str, Any],
    activation_receipt: Mapping[str, Any],
    candidate_receipt: Mapping[str, Any],
    shadow_receipt: Mapping[str, Any],
    railway: Mapping[str, str] | None = None,
    bundle_root: Path | None = None,
    runtime_uid: int | None = None,
    runtime_gid: int | None = None,
) -> dict[str, Any]:
    validate_request(
        request,
        activation_receipt=activation_receipt,
        require_fresh=False,
    )
    if not isinstance(value, dict) or set(value) != _RECEIPT_KEYS:
        raise RecoveryContractError("recovery receipt fields are invalid")
    expected_authority = {
        "source": "railway",
        "authority_changed": False,
        "public_api_remained_online": True,
        "writers_paused_for_export": True,
        "writers_restarted": True,
    }
    if (
        value.get("schema") != RECEIPT_SCHEMA
        or value.get("repository") != migration.REPOSITORY
        or value.get("workflow") != WORKFLOW
        or value.get("commit") != request["commit"]
        or value.get("request_id") != request["request_id"]
        or value.get("request_sha256") != _digest(_canonical(request))
        or value.get("activation_receipt_sha256")
        != _digest(_canonical(activation_receipt))
        or value.get("candidate_receipt_sha256")
        != _digest(_canonical(candidate_receipt))
        or value.get("shadow_receipt_sha256") != _digest(_canonical(shadow_receipt))
        or value.get("authority") != expected_authority
        or value.get("research_only") is not True
        or value.get("can_publish") is not False
        or value.get("can_execute") is not False
    ):
        raise RecoveryContractError("recovery receipt binding is invalid")
    candidate = validate_candidate_chain(
        candidate_receipt,
        activation_receipt=activation_receipt,
    )
    validate_shadow_chain(
        shadow_receipt,
        candidate_receipt=candidate,
    )
    observed_railway = value.get("railway")
    if observed_railway != activation_receipt.get("railway") or (
        railway is not None and observed_railway != dict(railway)
    ):
        raise RecoveryContractError("recovery receipt Railway identity is invalid")
    snapshot = value.get("snapshot")
    required_snapshot = {
        "id",
        "relative_path",
        "backup_schema",
        "source_revision",
        "inventory_sha256",
        "content_set_sha256",
        "member_sha256",
        "critical_table_count_floor",
        "total_bytes",
    }
    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != required_snapshot
        or snapshot.get("id") != request["snapshot_id"]
        or snapshot.get("relative_path")
        != f"recovery-snapshots/{request['snapshot_id']}"
        or snapshot.get("backup_schema") != migration.BACKUP_SCHEMA
        or snapshot.get("source_revision") != request["commit"]
        or not isinstance(snapshot.get("member_sha256"), dict)
        or set(snapshot["member_sha256"]) != set(migration._BACKUP_MEMBERS)
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(item)) is None
            for item in snapshot["member_sha256"].values()
        )
        or not isinstance(snapshot.get("critical_table_count_floor"), list)
        or len(snapshot["critical_table_count_floor"]) != 4
        or any(
            not isinstance(item, int) or item < 0
            for item in snapshot["critical_table_count_floor"]
        )
        or not isinstance(snapshot.get("total_bytes"), int)
        or snapshot["total_bytes"] <= 0
    ):
        raise RecoveryContractError("recovery receipt snapshot is invalid")
    _validate_digest(snapshot.get("inventory_sha256"), label="recovery inventory")
    _validate_digest(snapshot.get("content_set_sha256"), label="recovery content set")
    filesystem = value.get("filesystem")
    if (
        not isinstance(filesystem, dict)
        or set(filesystem)
        != {
            "tree_sha256",
            "nbs_full_store_audit_result",
            "agent_room_audit",
            "palimpsest_china_state_audit_contract",
            "palimpsest_china_state_audit_result",
        }
        or not isinstance(filesystem.get("tree_sha256"), dict)
        or set(filesystem["tree_sha256"])
        != {"market", "nbs", "api", "palimpsest-china"}
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(item)) is None
            for item in filesystem["tree_sha256"].values()
        )
        or filesystem.get("nbs_full_store_audit_result")
        not in {"not_onboarded", "verified_head"}
        or filesystem.get("palimpsest_china_state_audit_contract")
        != "seiche.palimpsest-china-activation-state.v1"
        or filesystem.get("palimpsest_china_state_audit_result") != "verified"
    ):
        raise RecoveryContractError("recovery receipt filesystem proof is invalid")
    try:
        migration.validate_agent_room_audit(filesystem.get("agent_room_audit"))
    except migration.MigrationContractError as exc:
        raise RecoveryContractError(str(exc)) from exc
    palimpsest_china_state = _palimpsest_china_state(
        value.get("palimpsest_china_state")
    )
    if palimpsest_china_state != candidate["palimpsest_china_state"]:
        raise RecoveryContractError(
            "recovery Palimpsest China state differs from candidate"
        )
    timing = value.get("timing")
    if not isinstance(timing, dict) or set(timing) != {
        "requested_at",
        "writers_stopped_at",
        "export_started_at",
        "export_completed_at",
        "writers_restarted_at",
    }:
        raise RecoveryContractError("recovery receipt timing fields are invalid")
    if timing["requested_at"] != request["requested_at"]:
        raise RecoveryContractError("recovery receipt request timing differs")
    moments = [
        _utc(timing[name], label=name)
        for name in (
            "requested_at",
            "writers_stopped_at",
            "export_started_at",
            "export_completed_at",
            "writers_restarted_at",
        )
    ]
    if moments != sorted(moments):
        raise RecoveryContractError("recovery receipt timing order is invalid")
    workers = value.get("workers")
    expected_commands = cutover.worker_commands()
    if not isinstance(workers, dict) or set(workers) != set(expected_commands):
        raise RecoveryContractError("recovery receipt worker set is invalid")
    for name, command in expected_commands.items():
        observed = workers[name]
        observed_command = (
            observed.get("command") if isinstance(observed, dict) else None
        )
        if (
            not isinstance(observed, dict)
            or set(observed) != {"command", "restarted"}
            or not isinstance(observed_command, list)
            or len(observed_command) != len(command)
            or not isinstance(observed_command[0], str)
            or not Path(observed_command[0]).is_absolute()
            or not Path(observed_command[0]).name.startswith("python")
            or observed_command[1:] != command[1:]
            or observed.get("restarted") is not True
        ):
            raise RecoveryContractError("recovery receipt worker proof is invalid")
    if bundle_root is not None:
        bundle = _bundle_identity(
            bundle_root,
            snapshot_id=str(request["snapshot_id"]),
            commit=str(request["commit"]),
        )
        if (
            bundle.inventory_sha256 != snapshot["inventory_sha256"]
            or bundle.content_set_sha256 != snapshot["content_set_sha256"]
            or dict(bundle.member_sha256) != snapshot["member_sha256"]
            or list(bundle.counts_floor) != snapshot["critical_table_count_floor"]
            or bundle.total_bytes != snapshot["total_bytes"]
        ):
            raise RecoveryContractError("recovery receipt differs from bundle")
        bundle_palimpsest_state = _palimpsest_china_state_from_bundle(bundle)
        if bundle_palimpsest_state != palimpsest_china_state:
            raise RecoveryContractError(
                "recovery receipt Palimpsest China state differs from bundle"
            )
        restored_nbs, restored_trees, restored_agent_room = (
            _restored_filesystem_identity(
                bundle,
                scratch_parent=bundle_root.parent,
                runtime_uid=(os.geteuid() if runtime_uid is None else runtime_uid),
                runtime_gid=(os.getegid() if runtime_gid is None else runtime_gid),
            )
        )
        if (
            restored_nbs != filesystem["nbs_full_store_audit_result"]
            or dict(restored_trees) != filesystem["tree_sha256"]
            or dict(restored_agent_room) != filesystem["agent_room_audit"]
        ):
            raise RecoveryContractError("recovery filesystem audit differs from bundle")
    return dict(value)


def validate_offsite_receipt(
    value: object,
    *,
    recovery_receipt: Mapping[str, Any],
    now: datetime | None = None,
    require_fresh: bool = True,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _OFFSITE_RECEIPT_KEYS:
        raise RecoveryContractError("off-site recovery receipt fields are invalid")
    if (
        value.get("schema") != OFFSITE_RECEIPT_SCHEMA
        or recovery_receipt.get("schema") != RECEIPT_SCHEMA
        or value.get("repository") != migration.REPOSITORY
        or value.get("workflow") != WORKFLOW
        or value.get("commit") != recovery_receipt.get("commit")
        or value.get("request_id") != recovery_receipt.get("request_id")
        or value.get("snapshot_id") != recovery_receipt.get("snapshot", {}).get("id")
        or value.get("recovery_receipt_sha256") != _digest(_canonical(recovery_receipt))
        or value.get("object_lock_mode") != "COMPLIANCE"
        or value.get("authority_changed") is not False
        or value.get("research_only") is not True
        or value.get("can_publish") is not False
        or value.get("can_execute") is not False
    ):
        raise RecoveryContractError("off-site recovery receipt binding is invalid")
    _validate_digest(
        value.get("reverse_restore_proof_sha256"),
        label="reverse restore proof",
    )
    offsite_palimpsest_state = _palimpsest_china_state(
        value.get("palimpsest_china_state")
    )
    recovery_palimpsest_state = _palimpsest_china_state(
        recovery_receipt.get("palimpsest_china_state")
    )
    if offsite_palimpsest_state != recovery_palimpsest_state:
        raise RecoveryContractError(
            "off-site Palimpsest China state differs from recovery"
        )
    bucket = value.get("bucket")
    prefix = value.get("prefix")
    if (
        not isinstance(bucket, str)
        or re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,62}", bucket) is None
        or not isinstance(prefix, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,200}", prefix) is None
        or prefix.startswith("/")
        or prefix.endswith("/")
        or ".." in prefix.split("/")
    ):
        raise RecoveryContractError("off-site recovery location is invalid")
    sealed_at = _utc(value.get("sealed_at"), label="off-site sealed_at")
    retain_until = _utc(value.get("retain_until"), label="off-site retain_until")
    writers_restarted_at = _utc(
        recovery_receipt.get("timing", {}).get("writers_restarted_at"),
        label="writers_restarted_at",
    )
    observed = now or datetime.now(UTC)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise RecoveryContractError("off-site receipt clock is not timezone-aware")
    observed = observed.astimezone(UTC).replace(microsecond=0)
    if (
        sealed_at < writers_restarted_at
        or sealed_at > observed + REQUEST_FUTURE_SKEW
        or retain_until < sealed_at + timedelta(days=29)
        or (require_fresh and sealed_at < observed - timedelta(hours=26))
    ):
        raise RecoveryContractError("off-site recovery timing is invalid")
    objects = value.get("objects")
    if not isinstance(objects, dict) or set(objects) != _OFFSITE_OBJECTS:
        raise RecoveryContractError("off-site recovery object set is not closed")
    expected_digests = {
        "activation-receipt.json": recovery_receipt["activation_receipt_sha256"],
        "candidate-receipt.json": recovery_receipt["candidate_receipt_sha256"],
        "shadow-receipt.json": recovery_receipt["shadow_receipt_sha256"],
        "request.json": recovery_receipt["request_sha256"],
        "recovery-receipt.json": _digest(_canonical(recovery_receipt)),
        "SHA256SUMS": recovery_receipt["snapshot"]["inventory_sha256"],
        "proof/reverse-restore.json": value["reverse_restore_proof_sha256"],
        **recovery_receipt["snapshot"]["member_sha256"],
    }
    key_root = f"{prefix}/{value['snapshot_id']}/{value['request_id']}"
    for name in sorted(_OFFSITE_OBJECTS):
        item = objects[name]
        if (
            not isinstance(item, dict)
            or set(item) != {"key", "sha256", "size", "version_id"}
            or item.get("key") != f"{key_root}/{name}"
            or item.get("sha256") != expected_digests[name]
            or not isinstance(item.get("size"), int)
            or item["size"] <= 0
            or item["size"] > 5 * 1024**3
            or not isinstance(item.get("version_id"), str)
            or not item["version_id"]
        ):
            raise RecoveryContractError("off-site recovery object proof is invalid")
    return dict(value)


def finalize_receipt(
    environment: Mapping[str, str],
    request: Mapping[str, Any],
    export: RecoveryExport,
    *,
    writers_stopped_at: str,
    writers_restarted_at: str,
    worker_commands: Mapping[str, list[str]],
    platform_root: Path | None = None,
    runtime_gid: int = migration.RUNTIME_GID,
) -> tuple[Path, dict[str, Any]]:
    root = platform_root or migration.PLATFORM_ROOT
    _activation_body, activation = activation_context(environment)
    _candidate_body, candidate = candidate_context(
        environment,
        activation_receipt=activation,
    )
    _shadow_body, shadow = shadow_context(
        candidate_receipt=candidate,
        platform_root=root,
    )
    railway = migration.railway_identity(environment)
    receipt = render_receipt(
        request,
        activation,
        candidate,
        shadow,
        export,
        railway=railway,
        writers_stopped_at=writers_stopped_at,
        writers_restarted_at=writers_restarted_at,
        worker_commands=worker_commands,
    )
    validate_receipt(
        receipt,
        request=request,
        activation_receipt=activation,
        candidate_receipt=candidate,
        shadow_receipt=shadow,
        railway=railway,
        bundle_root=export.bundle.root,
    )
    receipts = root / "recovery-receipts"
    cutover._prepare_authority_directory(receipts, runtime_gid=runtime_gid)
    path = receipts / (f"{request['snapshot_id']}-{request['request_id']}.json")
    if path.exists() or path.is_symlink():
        _body, existing = _load_canonical(
            path,
            label="recovery receipt",
            maximum_bytes=256 * 1024,
        )
        validate_receipt(
            existing,
            request=request,
            activation_receipt=activation,
            candidate_receipt=candidate,
            shadow_receipt=shadow,
            railway=railway,
            bundle_root=export.bundle.root,
        )
        return path, existing
    migration._write_receipt(path, receipt, gid=runtime_gid)
    return path, receipt


def _decode_base64(value: str) -> bytes:
    if not 1 <= len(value) <= 128 * 1024:
        raise RecoveryContractError("recovery request encoding is invalid")
    try:
        body = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RecoveryContractError("recovery request encoding is invalid") from exc
    if base64.b64encode(body).decode("ascii") != value:
        raise RecoveryContractError("recovery request encoding is not canonical")
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    request = subparsers.add_parser("request")
    request.add_argument("document_base64")
    validate = subparsers.add_parser("validate-receipt")
    validate.add_argument("receipt")
    validate.add_argument("--request", required=True)
    validate.add_argument("--activation", required=True)
    validate.add_argument("--candidate", required=True)
    validate.add_argument("--shadow", required=True)
    validate.add_argument("--bundle-root", required=True)
    monitor = subparsers.add_parser("validate-monitor")
    monitor.add_argument("recovery_receipt")
    monitor.add_argument("offsite_receipt")
    monitor.add_argument("--request", required=True)
    monitor.add_argument("--activation", required=True)
    monitor.add_argument("--candidate", required=True)
    monitor.add_argument("--shadow", required=True)
    monitor.add_argument("--deployment-id", required=True)
    monitor.add_argument("--release-sha", required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "request":
        published = publish_request(
            _decode_base64(arguments.document_base64), os.environ
        )
        print(published["request_id"])
        return 0
    receipt_path = (
        Path(arguments.receipt)
        if arguments.command == "validate-receipt"
        else Path(arguments.recovery_receipt)
    )
    _receipt_body, receipt = _load_canonical(
        receipt_path,
        label="recovery receipt",
        maximum_bytes=256 * 1024,
    )
    _request_body, request_value = _load_canonical(
        Path(arguments.request),
        label="recovery request",
        maximum_bytes=32 * 1024,
    )
    activation_body, activation = _load_canonical(
        Path(arguments.activation),
        label="activation receipt",
        maximum_bytes=256 * 1024,
    )
    _candidate_body, candidate = _load_canonical(
        Path(arguments.candidate),
        label="candidate receipt",
        maximum_bytes=256 * 1024,
    )
    _shadow_body, shadow = _load_canonical(
        Path(arguments.shadow),
        label="shadow receipt",
        maximum_bytes=256 * 1024,
    )
    validate_request(
        request_value,
        activation_receipt=activation,
        require_fresh=False,
    )
    validate_receipt(
        receipt,
        request=request_value,
        activation_receipt=activation,
        candidate_receipt=candidate,
        shadow_receipt=shadow,
        bundle_root=(
            Path(arguments.bundle_root)
            if arguments.command == "validate-receipt"
            else None
        ),
    )
    if _digest(activation_body) != receipt["activation_receipt_sha256"]:
        raise RecoveryContractError("recovery activation file digest differs")
    if arguments.command == "validate-monitor":
        if (
            receipt.get("commit") != arguments.release_sha
            or receipt.get("railway", {}).get("deployment_id")
            != arguments.deployment_id
        ):
            raise RecoveryContractError(
                "monitored recovery receipt is not current production"
            )
        _offsite_body, offsite = _load_canonical(
            Path(arguments.offsite_receipt),
            label="off-site recovery receipt",
            maximum_bytes=512 * 1024,
        )
        validate_offsite_receipt(
            offsite,
            recovery_receipt=receipt,
        )
        print(f"{_digest(_receipt_body)} {_digest(_offsite_body)}")
        return 0
    print(_digest(_receipt_body))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RecoveryContractError as error:
        print(f"seiche Railway recovery: {error}", file=sys.stderr)
        time.sleep(1)
        raise SystemExit(1) from None
