"""Fail-closed Railway shadow restore for Seiche backup-v4 snapshots.

Phase 4 deliberately restores an immutable filesystem generation and a fresh
PostgreSQL database while Hetzner remains the sole writer and public origin.
The module contains no Railway control-plane client: a protected workflow owns
deployment, while this runtime owns only validation, restore, and evidence.
"""

from __future__ import annotations

import argparse
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
RECEIPT_SCHEMA = "seiche.railway-stateful-shadow-receipt.v2"
BACKUP_SCHEMA = "seiche.market-backup.v4"
LEGACY_BACKUP_SCHEMA = "seiche.market-backup.v3"
REPOSITORY = "beepboop2025/seiche"
WORKFLOW = "beepboop2025/seiche/.github/workflows/railway-stateful-shadow.yml"
SOURCE_REF = "refs/heads/main"
PLATFORM_ROOT = Path("/var/lib/seiche-platform")
SOURCE_ARCHIVE = Path("/migration/source.tar")
SOURCE_BUNDLE = Path("/migration/source.bundle")
REQUEST_PATH = Path("/migration/request.json")
RUNTIME_UID = 10001
RUNTIME_GID = 10001

_SHA40_RE = re.compile(r"[0-9a-f]{40}")
_SHA64_RE = re.compile(r"[0-9a-f]{64}")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_SNAPSHOT_RE = re.compile(r"20[0-9]{6}T[0-9]{6}Z")
_REGION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}")
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
    if (
        value["schema"] != "seiche.palimpsest-china-activation-state.v1"
        or value["state_root"] != "/var/lib/seiche-palimpsest-china"
        or _SHA64_RE.fullmatch(value["tree_sha256"] or "") is None
        or type(bundles) is not list
        or type(receipts) is not list
        or bundles != sorted(set(bundles))
        or receipts != sorted(set(receipts))
        or any(_SHA64_RE.fullmatch(item or "") is None for item in bundles)
        or any(_SHA64_RE.fullmatch(item or "") is None for item in receipts)
        or any(
            item is not None and _SHA64_RE.fullmatch(item or "") is None
            for item in (
                value["active_activation_id"],
                value["pending_candidate_activation_id"],
            )
        )
    ):
        raise MigrationContractError("Palimpsest China state audit is invalid")
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
                api_uid=os.geteuid(),
                api_gid=os.getegid(),
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
    market = state_stage / "seiche"
    nbs = state_stage / "seiche-nbs"
    api_data = api_stage / "api-data"
    _validate_sqlite(api_data / "seiche.sqlite")
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
    railway: Mapping[str, str],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
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
    expected_members = (
        _BACKUP_MEMBERS if bundle_schema == BACKUP_SCHEMA else _LEGACY_BACKUP_MEMBERS
    )
    if (
        not isinstance(bundle, dict)
        or bundle_schema not in {BACKUP_SCHEMA, LEGACY_BACKUP_SCHEMA}
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
        or filesystem.get("palimpsest_china_state_audit_result")
        != ("verified" if bundle_schema == BACKUP_SCHEMA else "legacy_absent_inactive")
        or not isinstance(filesystem.get("tree_sha256"), dict)
        or set(filesystem["tree_sha256"])
        != {"market", "nbs", "api", "palimpsest-china"}
        or any(
            _SHA64_RE.fullmatch(item) is None
            for item in filesystem["tree_sha256"].values()
        )
    ):
        raise MigrationContractError("shadow receipt filesystem is invalid")
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
            api_uid=os.geteuid(),
            api_gid=os.getegid(),
            declared_state_root=Path("/var/lib/seiche-palimpsest-china"),
        )
    except Exception as exc:
        raise MigrationContractError(
            "accepted shadow Palimpsest China audit failed"
        ) from exc
    audit_result = receipt["filesystem"]["palimpsest_china_state_audit_result"]
    if audit_result == "legacy_absent_inactive" and any(
        (
            palimpsest_audit["bundles"],
            palimpsest_audit["receipts"],
            palimpsest_audit["active_activation_id"],
            palimpsest_audit["pending_candidate_activation_id"],
        )
    ):
        raise MigrationContractError(
            "legacy shadow Palimpsest China state is not empty and inactive"
        )


def palimpsest_runtime_environment(state_root: Path) -> dict[str, str]:
    """Render runtime paths only from one fully audited restored state tree."""

    try:
        from seiche import palimpsest_china_activation as activation

        audit = activation.audit_activation_state(
            state_root,
            root_uid=os.geteuid(),
            root_gid=os.getegid(),
            api_uid=os.geteuid(),
            api_gid=os.getegid(),
            declared_state_root=Path("/var/lib/seiche-palimpsest-china"),
        )
        paths = activation._activation_audit_paths(
            state_root,
            root_uid=os.geteuid(),
            root_gid=os.getegid(),
            api_uid=os.geteuid(),
            api_gid=os.getegid(),
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
) -> tuple[dict[str, Any], str]:
    started_at = _iso_now()
    for path in (platform_root, bundle.root):
        if not path.is_absolute() or path == Path("/") or path.is_symlink():
            raise MigrationContractError("stateful migration path is unsafe")
    platform_root.mkdir(mode=0o750, parents=True, exist_ok=True)
    generations = platform_root / "generations"
    receipts = platform_root / "receipts"
    generations.mkdir(mode=0o750, exist_ok=True)
    receipts.mkdir(mode=0o750, exist_ok=True)
    os.chown(platform_root, os.geteuid(), runtime_gid)
    os.chown(generations, os.geteuid(), runtime_gid)
    os.chown(receipts, os.geteuid(), runtime_gid)
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
        validate_receipted_generation(generation_path, receipt)
        target_dsn = _target_dsn(base_dsn, receipt["database"]["name"])
        if inspect_postgres_counts(target_dsn) != tuple(
            receipt["database"]["critical_table_counts"]
        ):
            raise MigrationContractError("accepted shadow PostgreSQL counts changed")
        return receipt, target_dsn
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
        nbs_result, generation_digests = restore_filesystem_generation(
            bundle,
            staging,
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
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
            railway=railway,
            started_at=started_at,
            completed_at=_iso_now(),
        )
        validate_receipt_document(receipt, request=request, railway=railway)
        _write_receipt(receipt_path, receipt, gid=runtime_gid)
        return receipt, database.dsn
    finally:
        if staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)


def runtime_environment(
    base: Mapping[str, str],
    receipt: Mapping[str, Any],
    *,
    database_dsn: str,
    receipt_path: Path,
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
            }
            | palimpsest_environment_names
        )
    }
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
            "SEICHE_RUNTIME_DATA_DIR": str(root / "api"),
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
    environment.update(palimpsest_runtime_environment(root / "palimpsest-china"))
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
    receipt, database_dsn = restore_shadow(
        request,
        bundle,
        platform_root=platform_root,
        base_dsn=base_dsn,
        railway=railway,
    )
    receipt_path = platform_root / "receipts" / f"{request['request_id']}.json"
    environment = runtime_environment(
        os.environ,
        receipt,
        database_dsn=database_dsn,
        receipt_path=receipt_path,
    )
    validate_runtime_receipt(environment)
    print(
        "seiche Railway shadow: restore verified; Hetzner remains sole authority",
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
