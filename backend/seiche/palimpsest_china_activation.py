"""Root-controlled activation transaction for accepted China-economic bundles.

This module is installed into Seiche's immutable, release-addressed intake
runtime.  The small root launcher in ``ops/deploy`` imports it only from that
runtime.  Candidate verification is then repeated as the unprivileged
``seiche`` identity under an empty environment before any API configuration is
changed.

The activation receipt authorizes one immutable bundle; ``active.json`` is the
atomic marker selecting the receipt currently exposed to the API.  A failed
REST or MCP proof restores the previous environment, systemd drop-in, and
marker before returning an error.  Candidate bundles and receipts are retained
as inert audit evidence and are never silently rewritten.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import time
from typing import Any, Iterator, Mapping, NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from seiche.palimpsest_china_intake import (
    MAX_ACCEPTANCE_BYTES,
    MAX_AVAILABILITY_BYTES,
    MAX_ARTIFACT_BYTES,
    MAX_CHECKSUMS_BYTES,
    MAX_HANDOFF_BYTES,
    MAX_INPUT_LEDGER_BYTES,
    MAX_LINEAGE_CHAIN_BYTES,
    MAX_LINEAGE_EVIDENCE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_PRODUCER_COMMIT_EVIDENCE_BYTES,
    MAX_PRODUCER_MAIN_EVIDENCE_BYTES,
    PalimpsestChinaIntakeError,
    load_accepted_export,
)


ACTIVATION_RECEIPT_SCHEMA = "seiche.palimpsest-china-activation-receipt.v1"
ACTIVE_MARKER_SCHEMA = "seiche.palimpsest-china-active.v2"
LEGACY_ACTIVE_MARKER_SCHEMA = "seiche.palimpsest-china-active.v1"
CANDIDATE_SCHEMA = "seiche.palimpsest-china-activation-candidate.v1"
RUNTIME_PROOF_SCHEMA = "seiche.palimpsest-china-rest-mcp-proof.v1"
PENDING_SCHEMA = "seiche.palimpsest-china-activation-pending.v1"
BACKUP_STATE_SCHEMA = "seiche.palimpsest-china-activation-state.v1"
DURABILITY_RECEIPT_SCHEMA = "seiche.palimpsest-china-activation-durability.v1"
PRODUCTION_STATE_ROOT = Path("/var/lib/seiche-palimpsest-china")
PRODUCTION_ENV_FILE = Path("/etc/seiche/palimpsest-china.env")
PRODUCTION_DROPIN_FILE = Path(
    "/etc/systemd/system/seiche-api.service.d/palimpsest-china.conf"
)
PRODUCTION_DEPLOY_LOCK = Path("/run/seiche-deploy/deploy.lock")
PRODUCTION_ACTIVATION_LOCK = Path("/run/seiche-deploy/palimpsest-china.lock")
PRODUCTION_TRANSACTION_LOCK = Path(
    "/run/seiche-deploy/palimpsest-china-transaction.lock"
)
PRODUCTION_DURABILITY_ROOT = Path(
    "/var/lib/seiche-recovery-proof/palimpsest-china-durability"
)
PRODUCTION_API_URL = "http://127.0.0.1:8787"
PRODUCTION_SYSTEMCTL = Path("/usr/bin/systemctl")
PRODUCTION_RUNUSER = Path("/usr/sbin/runuser")
PRODUCTION_ENV = Path("/usr/bin/env")
PRODUCTION_RUNTIME_ROOT = Path("/opt/seiche-palimpsest-china")
PRODUCTION_PYTHON = Path("/usr/bin/python3")
PRODUCTION_REPOSITORY = Path("/home/seiche/app")
PRODUCTION_DEPLOYED_SHA = Path("/var/lib/seiche-deploy/deployed-sha")
PRODUCTION_ALLOWED_SIGNERS = Path("/etc/seiche-release.allowed-signers")
PRODUCTION_GIT = Path("/usr/bin/git")
PRODUCTION_SSH_KEYGEN = Path("/usr/bin/ssh-keygen")
PRODUCTION_API_USER = "seiche"
PRODUCTION_RELEASE_PRINCIPAL = "beepboop2025@users.noreply.github.com"
LOCK_TIMEOUT_SECONDS = 300.0
PROBE_TIMEOUT_SECONDS = 180.0
PROBE_INTERVAL_SECONDS = 2.0
MAX_ACTIVE_MARKER_BYTES = 16 * 1024

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_RECEIPT_KEYS = frozenset(
    {
        "schema",
        "activation_id",
        "bundle_id",
        "release_sha",
        "files",
        "producer_repository",
        "producer_sha",
        "producer_workflow_run_id",
        "signer_key_id",
        "accepted_at",
        "rights_expires_at",
        "previous_receipt_sha256",
        "runtime_proof",
        "recorded_at",
    }
)
_ACTIVE_KEYS = frozenset(
    {
        "schema",
        "activation_id",
        "bundle_id",
        "release_sha",
        "receipt_path",
        "receipt_sha256",
        "files",
        "activated_at",
        "publication_status",
        "legacy_active_marker",
        "legacy_active_marker_sha256",
    }
)
_LEGACY_ACTIVE_KEYS = _ACTIVE_KEYS - frozenset(
    {"publication_status", "legacy_active_marker", "legacy_active_marker_sha256"}
)
_DURABILITY_KEYS = frozenset(
    {
        "schema",
        "status",
        "activation_id",
        "bundle_id",
        "release_sha",
        "active_marker_sha256",
        "activation_receipt_sha256",
        "activation_state_audit_sha256",
        "activation_state_tree_sha256",
        "local_backup_snapshot",
        "local_backup_inventory_sha256",
        "local_restore_schema",
        "local_restore_activation_id",
        "local_restore_tree_sha256",
        "local_restore_checked_at",
        "local_restore_receipt",
        "local_restore_receipt_sha256",
        "offsite_status_schema",
        "offsite_snapshot",
        "offsite_activation_id",
        "offsite_tree_sha256",
        "offsite_attempt_id",
        "offsite_remote_receipt_key",
        "offsite_remote_receipt_sha256",
        "offsite_verified_at",
        "completed_at",
    }
)
_LOCAL_RESTORE_KEYS = frozenset(
    {
        "schema",
        "checked_at",
        "snapshot",
        "source_backup_schema",
        "deployed_sha",
        "critical_table_counts",
        "critical_table_count_floor",
        "nbs_full_store_audit_contract",
        "nbs_full_store_audit_result",
        "nbs_public_revision_store",
        "palimpsest_china_state_archive_restore",
        "palimpsest_china_state_audit_contract",
        "palimpsest_china_state_tree_sha256",
        "palimpsest_china_active_activation_id",
        "palimpsest_china_pending_candidate_activation_id",
        "palimpsest_china_bundle_count",
        "palimpsest_china_receipt_count",
        "database_restore",
        "state_archive_restore",
        "api_data_archive_restore",
        "research_only",
        "can_publish",
        "can_execute",
    }
)
_CANDIDATE_KEYS = frozenset(
    {
        "schema",
        "files",
        "signer_key_id",
        "accepted_at",
        "rights_expires_at",
        "producer_repository",
        "producer_sha",
        "producer_workflow_run_id",
    }
)
_PROOF_KEYS = frozenset(
    {
        "schema",
        "api_url",
        "rest_path",
        "mcp_path",
        "rest_files",
        "rest_signer_key_id",
        "mcp_files",
        "mcp_signer_key_id",
        "verified_at",
    }
)
_PENDING_KEYS = frozenset(
    {
        "schema",
        "candidate_activation_id",
        "candidate_bundle_id",
        "candidate_files",
        "previous_activation_id",
        "started_at",
    }
)


@dataclass(frozen=True, slots=True)
class _BundleFileSpec:
    source_field: str
    filename: str
    environment: str
    maximum: int


_BUNDLE_FILE_SPECS = (
    _BundleFileSpec(
        "manifest",
        "manifest.json",
        "SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH",
        MAX_MANIFEST_BYTES,
    ),
    _BundleFileSpec(
        "artifact",
        "artifact.jsonl",
        "SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH",
        MAX_ARTIFACT_BYTES,
    ),
    _BundleFileSpec(
        "input_ledger",
        "input-ledger.jsonl",
        "SEICHE_PALIMPSEST_CHINA_INPUT_LEDGER_PATH",
        MAX_INPUT_LEDGER_BYTES,
    ),
    _BundleFileSpec(
        "availability",
        "availability.json",
        "SEICHE_PALIMPSEST_CHINA_AVAILABILITY_PATH",
        MAX_AVAILABILITY_BYTES,
    ),
    _BundleFileSpec(
        "producer_commit_evidence",
        "github-commit.json",
        "SEICHE_PALIMPSEST_CHINA_PRODUCER_COMMIT_EVIDENCE_PATH",
        MAX_PRODUCER_COMMIT_EVIDENCE_BYTES,
    ),
    _BundleFileSpec(
        "producer_main_evidence",
        "github-main-branch.json",
        "SEICHE_PALIMPSEST_CHINA_PRODUCER_MAIN_EVIDENCE_PATH",
        MAX_PRODUCER_MAIN_EVIDENCE_BYTES,
    ),
    _BundleFileSpec(
        "handoff",
        "handoff-receipt.json",
        "SEICHE_PALIMPSEST_CHINA_HANDOFF_PATH",
        MAX_HANDOFF_BYTES,
    ),
    _BundleFileSpec(
        "checksums",
        "SHA256SUMS",
        "SEICHE_PALIMPSEST_CHINA_CHECKSUMS_PATH",
        MAX_CHECKSUMS_BYTES,
    ),
    _BundleFileSpec(
        "lineage_chain",
        "china-econ-wdi-lineage-chain.jsonl",
        "SEICHE_PALIMPSEST_CHINA_LINEAGE_CHAIN_PATH",
        MAX_LINEAGE_CHAIN_BYTES,
    ),
    _BundleFileSpec(
        "lineage_evidence",
        "github-commit-lineage-evidence.jsonl",
        "SEICHE_PALIMPSEST_CHINA_LINEAGE_EVIDENCE_PATH",
        MAX_LINEAGE_EVIDENCE_BYTES,
    ),
    _BundleFileSpec(
        "acceptance",
        "acceptance.json",
        "SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH",
        MAX_ACCEPTANCE_BYTES,
    ),
)
_BUNDLE_FILENAMES = frozenset(spec.filename for spec in _BUNDLE_FILE_SPECS)
_MAXIMUM_BY_FILENAME = {spec.filename: spec.maximum for spec in _BUNDLE_FILE_SPECS}


@dataclass(frozen=True, slots=True)
class BundleSources:
    """The complete immutable Palimpsest handoff selected by an operator."""

    manifest: Path
    artifact: Path
    input_ledger: Path
    availability: Path
    producer_commit_evidence: Path
    producer_main_evidence: Path
    handoff: Path
    checksums: Path
    lineage_chain: Path
    lineage_evidence: Path
    acceptance: Path

    def files(self) -> dict[str, Path]:
        return {
            spec.filename: Path(getattr(self, spec.source_field))
            for spec in _BUNDLE_FILE_SPECS
        }


class PalimpsestChinaActivationError(RuntimeError):
    """The privileged activation transaction could not be proven safe."""


def _fail(message: str) -> NoReturn:
    raise PalimpsestChinaActivationError(message)


@dataclass(frozen=True, slots=True)
class ActivationPaths:
    """Fixed host paths and identities used by one activation transaction."""

    state_root: Path
    env_file: Path
    dropin_file: Path
    deploy_lock: Path
    activation_lock: Path
    runtime_release: Path
    release_sha: str
    root_uid: int
    root_gid: int
    api_uid: int
    api_gid: int
    api_user: str = PRODUCTION_API_USER
    api_url: str = PRODUCTION_API_URL
    systemctl: Path = PRODUCTION_SYSTEMCTL
    runuser: Path = PRODUCTION_RUNUSER
    env_program: Path = PRODUCTION_ENV
    python: Path = PRODUCTION_PYTHON
    portable: bool = False
    attest_dir: Path | None = None
    durability_root: Path | None = None

    @property
    def receipts_dir(self) -> Path:
        return self.state_root / "receipts"

    @property
    def active_marker(self) -> Path:
        return self.state_root / "active.json"

    @property
    def pending_marker(self) -> Path:
        return self.state_root / "pending.json"

    @property
    def resolved_durability_root(self) -> Path:
        if self.durability_root is not None:
            return self.durability_root
        if self.portable:
            return self.state_root.parent / "palimpsest-china-durability"
        return PRODUCTION_DURABILITY_ROOT


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    path: Path
    body: bytes | None
    uid: int
    gid: int
    mode: int


def _canonical(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                dict(value),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise PalimpsestChinaActivationError(
            "activation document is not canonical JSON data"
        ) from exc


def _strict_json(body: bytes, *, label: str) -> dict[str, Any]:
    try:
        text = body.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise PalimpsestChinaActivationError(f"{label} is not strict UTF-8") from exc

    def reject_constant(value: str) -> None:
        raise PalimpsestChinaActivationError(
            f"{label} contains non-finite JSON number {value}"
        )

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise PalimpsestChinaActivationError(
                    f"{label} contains duplicate key {key!r}"
                )
            out[key] = value
        return out

    try:
        value = json.loads(
            text,
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise PalimpsestChinaActivationError(f"{label} is not valid JSON") from exc
    if type(value) is not dict:
        _fail(f"{label} must be an object")
    if _canonical(value) != body:
        _fail(f"{label} must use exact canonical JSON bytes")
    return value


def _exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], *, label: str
) -> None:
    if frozenset(value) != expected:
        _fail(f"{label} fields changed")


def _sha(value: object, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(f"{label} must be lowercase SHA-256")
    return value


def _git_sha(value: object, *, label: str) -> str:
    if type(value) is not str or _GIT_SHA_RE.fullmatch(value) is None:
        _fail(f"{label} must be a lowercase 40-hex Git SHA")
    return value


def _timestamp(value: object, *, label: str) -> datetime:
    if type(value) is not str or not value.endswith("Z") or len(value) > 64:
        _fail(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PalimpsestChinaActivationError(f"{label} must be canonical UTC") from exc
    if parsed.isoformat().replace("+00:00", "Z") != value:
        _fail(f"{label} must be canonical UTC")
    return parsed.astimezone(UTC)


def _now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _now_text_at_or_after(floor: datetime | None, *, label: str) -> str:
    """Render wall time only when it does not regress retained authority."""

    rendered = _now_text()
    now = _timestamp(rendered, label="current system clock")
    if floor is not None and now < floor:
        _fail(f"system clock regressed below {label}")
    return rendered


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _file_hashes(value: object, *, label: str) -> dict[str, str]:
    if type(value) is not dict or frozenset(value) != _BUNDLE_FILENAMES:
        _fail(f"{label} must bind the exact eleven runtime files")
    return {
        name: _sha(value[name], label=f"{label}.{name}")
        for name in sorted(_BUNDLE_FILENAMES)
    }


def _local_restore_fields(body: bytes) -> dict[str, str]:
    try:
        lines = body.decode("ascii", "strict").splitlines()
    except UnicodeDecodeError as exc:
        raise PalimpsestChinaActivationError(
            "activation durability local restore receipt is not ASCII"
        ) from exc
    if not body.endswith(b"\n") or not lines or len(body) > 16 * 1024:
        _fail("activation durability local restore receipt is invalid")
    fields: dict[str, str] = {}
    for line in lines:
        if line.count("=") != 1:
            _fail("activation durability local restore receipt has an invalid field")
        key, value = line.split("=", 1)
        if not key or key in fields:
            _fail("activation durability local restore receipt has duplicate fields")
        fields[key] = value
    if set(fields) != _LOCAL_RESTORE_KEYS:
        _fail("activation durability local restore receipt fields changed")
    return fields


def _bundle_id(hashes: Mapping[str, str]) -> str:
    files = _file_hashes(dict(hashes), label="bundle files")
    return _digest(b"seiche:palimpsest-china-bundle:v3\n" + _canonical(files))


def _activation_id(*, bundle_id: str, release_sha: str) -> str:
    _sha(bundle_id, label="bundle id")
    _git_sha(release_sha, label="activation release SHA")
    return _digest(
        f"seiche:palimpsest-china-activation:v1\n{bundle_id}\n{release_sha}\n".encode(
            "ascii"
        )
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_directory(
    path: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
    label: str,
    validate_ancestry: bool = False,
) -> None:
    if (
        not path.is_absolute()
        or path == Path("/")
        or Path(os.path.normpath(path)) != path
    ):
        _fail(f"{label} path is not canonical")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PalimpsestChinaActivationError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        _fail(f"{label} ownership or mode is unsafe")
    if not validate_ancestry:
        return
    current = Path("/")
    for component in path.parts[1:-1]:
        current /= component
        try:
            ancestor = current.lstat()
        except OSError as exc:
            raise PalimpsestChinaActivationError(
                f"{label} ancestry is unavailable"
            ) from exc
        if (
            not stat.S_ISDIR(ancestor.st_mode)
            or ancestor.st_uid != uid
            or stat.S_IMODE(ancestor.st_mode) & 0o022
        ):
            _fail(f"{label} ancestry is not root-owned and protected")


def _validate_protected_path(path: Path, *, uid: int, label: str) -> None:
    """Reject non-canonical paths, symlink traversal, and writable ancestry."""

    if (
        not path.is_absolute()
        or path == Path("/")
        or Path(os.path.normpath(path)) != path
    ):
        _fail(f"{label} path is not canonical")
    current = Path("/")
    for component in path.parts[1:-1]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise PalimpsestChinaActivationError(
                f"{label} ancestry is unavailable"
            ) from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != uid
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            _fail(f"{label} ancestry is not owner-controlled and protected")


def _stable_read(
    path: Path,
    *,
    label: str,
    maximum: int,
    uid: int,
    gid: int | None = None,
    modes: frozenset[int] | None = None,
    minimum: int = 1,
) -> bytes:
    if not path.is_absolute() or Path(os.path.normpath(path)) != path:
        _fail(f"{label} path is not canonical")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        visible = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != uid
            or (gid is not None and before.st_gid != gid)
            or (modes is not None and stat.S_IMODE(before.st_mode) not in modes)
            or (before.st_dev, before.st_ino) != (visible.st_dev, visible.st_ino)
            or before.st_size < minimum
            or before.st_size > maximum
        ):
            _fail(f"{label} metadata is unsafe")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        after = os.fstat(descriptor)

        def identity(item: os.stat_result) -> tuple[int, ...]:
            return (
                item.st_dev,
                item.st_ino,
                item.st_mode,
                item.st_nlink,
                item.st_uid,
                item.st_gid,
                item.st_size,
                item.st_mtime_ns,
                item.st_ctime_ns,
            )

        if len(body) != before.st_size or identity(before) != identity(after):
            _fail(f"{label} changed while being read")
        return body
    except OSError as exc:
        raise PalimpsestChinaActivationError(f"{label} cannot be read safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _atomic_write(
    path: Path,
    body: bytes,
    *,
    uid: int,
    gid: int,
    mode: int,
) -> None:
    stage = path.parent / f".{path.name}.{secrets.token_hex(16)}"
    descriptor = -1
    try:
        descriptor = os.open(
            stage,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        offset = 0
        while offset < len(body):
            written = os.write(descriptor, body[offset:])
            if written < 1:
                _fail(f"atomic write made no progress for {path}")
            offset += written
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(stage, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            f"could not atomically install {path}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            stage.unlink()
        except FileNotFoundError:
            pass


def _write_immutable(
    path: Path,
    body: bytes,
    *,
    uid: int,
    gid: int,
    mode: int,
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            mode,
        )
        offset = 0
        while offset < len(body):
            written = os.write(descriptor, body[offset:])
            if written < 1:
                _fail(f"immutable write made no progress for {path}")
            offset += written
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _fsync_directory(path.parent)
    except FileExistsError:
        existing = _stable_read(
            path,
            label=f"existing immutable {path.name}",
            maximum=max(len(body), 1),
            uid=uid,
            gid=gid,
            modes=frozenset({mode}),
        )
        if existing != body:
            _fail(f"immutable target {path} already contains different bytes")
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            f"could not install immutable {path}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _rename_noreplace(
    source: Path, destination: Path, *, portable: bool = False
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        if portable:
            try:
                destination.lstat()
            except FileNotFoundError:
                source.rename(destination)
                return
            raise FileExistsError(destination)
        _fail("activation requires Linux renameat2")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(destination)
    raise OSError(error, os.strerror(error), destination)


def _publish_immutable_atomic(
    path: Path,
    body: bytes,
    *,
    uid: int,
    gid: int,
    mode: int,
    portable: bool,
) -> None:
    stage = path.parent / (f".receipt-stage-{path.stem}-{secrets.token_hex(16)}")
    try:
        _write_immutable(stage, body, uid=uid, gid=gid, mode=mode)
        try:
            _rename_noreplace(stage, path, portable=portable)
        except FileExistsError:
            existing = _stable_read(
                path,
                label=f"existing immutable {path.name}",
                maximum=max(len(body), 1),
                uid=uid,
                gid=gid,
                modes=frozenset({mode}),
            )
            if existing != body:
                _fail(f"immutable target {path} already contains different bytes")
        _fsync_directory(path.parent)
    finally:
        try:
            stage.unlink()
            _fsync_directory(path.parent)
        except FileNotFoundError:
            pass


def _cleanup_activation_stages(paths: ActivationPaths) -> None:
    receipt_pattern = re.compile(r"\.receipt-stage-[0-9a-f]{64}-[0-9a-f]{32}")
    changed = False
    try:
        receipt_entries = list(paths.receipts_dir.iterdir())
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            "activation receipt stages cannot be enumerated"
        ) from exc
    for entry in receipt_entries:
        if receipt_pattern.fullmatch(entry.name) is None:
            continue
        metadata = entry.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != paths.root_uid
            or metadata.st_gid != paths.root_gid
            or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
            or metadata.st_size > 16 * 1024
        ):
            _fail("interrupted activation receipt stage is unsafe")
        entry.unlink()
        changed = True
    if changed:
        _fsync_directory(paths.receipts_dir)

    bundle_pattern = re.compile(r"\.bundle-stage-[0-9a-f]{32}")
    changed = False
    try:
        root_entries = list(paths.state_root.iterdir())
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            "activation bundle stages cannot be enumerated"
        ) from exc
    for entry in root_entries:
        if bundle_pattern.fullmatch(entry.name) is None:
            continue
        metadata = entry.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != paths.root_uid
            or metadata.st_gid != paths.api_gid
            or stat.S_IMODE(metadata.st_mode) not in {0o700, 0o750}
        ):
            _fail("interrupted activation bundle stage is unsafe")
        try:
            members = list(entry.iterdir())
        except OSError as exc:
            raise PalimpsestChinaActivationError(
                "interrupted activation bundle stage cannot be enumerated"
            ) from exc
        if any(member.name not in _BUNDLE_FILENAMES for member in members):
            _fail("interrupted activation bundle stage has unexpected members")
        for member in members:
            item = member.lstat()
            if (
                not stat.S_ISREG(item.st_mode)
                or item.st_nlink != 1
                or item.st_uid != paths.root_uid
                or item.st_gid != paths.api_gid
                or stat.S_IMODE(item.st_mode) not in {0o440, 0o600}
                or item.st_size > _MAXIMUM_BY_FILENAME[member.name]
            ):
                _fail("interrupted activation bundle stage member is unsafe")
            member.unlink()
        entry.rmdir()
        changed = True
    if changed:
        _fsync_directory(paths.state_root)


def _validate_bundle(
    bundle: Path,
    *,
    paths: ActivationPaths,
    expected: Mapping[str, bytes],
) -> None:
    _validate_directory(
        bundle,
        uid=paths.root_uid,
        gid=paths.api_gid,
        mode=0o750,
        label="Palimpsest China bundle",
    )
    try:
        names = {child.name for child in bundle.iterdir()}
    except OSError as exc:
        raise PalimpsestChinaActivationError("bundle cannot be listed") from exc
    if names != set(expected):
        _fail("bundle members changed")
    for name, body in expected.items():
        installed = _stable_read(
            bundle / name,
            label=f"installed bundle {name}",
            maximum=_MAXIMUM_BY_FILENAME[name],
            uid=paths.root_uid,
            gid=paths.api_gid,
            modes=frozenset({0o440}),
        )
        if installed != body:
            _fail(f"installed bundle {name} differs from reviewed input")


def _read_source_bundle(
    source_files: Mapping[str, Path], *, paths: ActivationPaths
) -> tuple[dict[str, bytes], dict[str, str]]:
    if frozenset(source_files) != _BUNDLE_FILENAMES:
        _fail("activation requires the exact eleven handoff files")
    bodies: dict[str, bytes] = {}
    for name, source in source_files.items():
        source = Path(source)
        if not paths.portable:
            _validate_protected_path(
                source,
                uid=paths.root_uid,
                label=f"operator source {name}",
            )
        bodies[name] = _stable_read(
            source,
            label=f"operator source {name}",
            maximum=_MAXIMUM_BY_FILENAME[name],
            uid=paths.root_uid,
            gid=paths.root_gid,
            modes=frozenset({0o400, 0o600}),
        )
    hashes = {name: _digest(body) for name, body in sorted(bodies.items())}
    return bodies, hashes


def _publish_bundle(
    bodies: Mapping[str, bytes],
    hashes: Mapping[str, str],
    *,
    paths: ActivationPaths,
) -> Path:
    identifier = _bundle_id(hashes)
    target = paths.state_root / identifier
    if target.exists() or target.is_symlink():
        _validate_bundle(target, paths=paths, expected=bodies)
        return target

    stage = paths.state_root / f".bundle-stage-{secrets.token_hex(16)}"
    try:
        os.mkdir(stage, 0o700)
        os.chown(stage, paths.root_uid, paths.api_gid)
        for name, body in bodies.items():
            _write_immutable(
                stage / name,
                body,
                uid=paths.root_uid,
                gid=paths.api_gid,
                mode=0o440,
            )
        os.chmod(stage, 0o750)
        _fsync_directory(stage)
        try:
            _rename_noreplace(stage, target, portable=paths.portable)
        except FileExistsError:
            _validate_bundle(target, paths=paths, expected=bodies)
        _fsync_directory(paths.state_root)
        _validate_bundle(target, paths=paths, expected=bodies)
        return target
    finally:
        if stage.exists() and not stage.is_symlink():
            for name in bodies:
                try:
                    (stage / name).unlink()
                except FileNotFoundError:
                    pass
            try:
                stage.rmdir()
            except FileNotFoundError:
                pass


def _install_bundle(
    source_files: Mapping[str, Path], *, paths: ActivationPaths
) -> tuple[Path, dict[str, str]]:
    bodies, hashes = _read_source_bundle(source_files, paths=paths)
    return _publish_bundle(bodies, hashes, paths=paths), hashes


def _validate_lock(descriptor: int, path: Path, *, paths: ActivationPaths) -> None:
    opened = os.fstat(descriptor)
    visible = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or opened.st_uid != paths.root_uid
        or opened.st_gid != paths.root_gid
        or stat.S_IMODE(opened.st_mode) != 0o600
        or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
    ):
        _fail(f"lock metadata is unsafe: {path}")


@contextmanager
def _exclusive_lock(
    path: Path, *, paths: ActivationPaths, create: bool
) -> Iterator[int]:
    flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        if create:
            try:
                descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                os.fchown(descriptor, paths.root_uid, paths.root_gid)
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
                _fsync_directory(path.parent)
            except FileExistsError:
                descriptor = os.open(path, flags)
        else:
            descriptor = os.open(path, flags)
        _validate_lock(descriptor, path, paths=paths)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail(f"lock remained busy for 300 seconds: {path}")
            time.sleep(min(0.1, remaining))
        _validate_lock(descriptor, path, paths=paths)
        yield descriptor
    except OSError as exc:
        raise PalimpsestChinaActivationError(f"cannot acquire lock {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@contextmanager
def _deployment_lock(
    *, paths: ActivationPaths, descriptor: int | None
) -> Iterator[None]:
    if descriptor is None:
        with _exclusive_lock(paths.deploy_lock, paths=paths, create=False):
            yield
        return
    if (
        isinstance(descriptor, bool)
        or not isinstance(descriptor, int)
        or descriptor < 0
    ):
        _fail("provided deploy lock descriptor is invalid")
    _validate_lock(descriptor, paths.deploy_lock, paths=paths)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            "provided deploy lock descriptor is not exclusively held"
        ) from exc
    _validate_lock(descriptor, paths.deploy_lock, paths=paths)
    yield


def _required_mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} is missing")
    return value


def _candidate_from_context(
    sources: BundleSources,
    *,
    attest_dir: Path | None = None,
) -> dict[str, Any]:
    context = load_accepted_export(
        sources.manifest,
        sources.artifact,
        sources.acceptance,
        input_ledger_path=sources.input_ledger,
        availability_path=sources.availability,
        producer_commit_evidence_path=sources.producer_commit_evidence,
        producer_main_evidence_path=sources.producer_main_evidence,
        handoff_path=sources.handoff,
        checksums_path=sources.checksums,
        lineage_chain_path=sources.lineage_chain,
        lineage_evidence_path=sources.lineage_evidence,
        attest_dir=attest_dir,
        now=datetime.now(UTC),
    )
    if not context.owner_attested or not isinstance(context.producer, Mapping):
        _fail("candidate lacks owner-attested serving authority")
    workflow = context.producer.get("workflow_run")
    if not isinstance(workflow, Mapping):
        _fail("candidate lacks an authoritative producer workflow")
    expiry = context.source_decision.get("expires_at")
    _timestamp(expiry, label="candidate rights expiry")
    commit = _required_mapping(
        context.producer_commit_evidence,
        label="candidate producer commit evidence",
    )
    main = _required_mapping(
        context.producer_main_evidence,
        label="candidate producer main evidence",
    )
    handoff = _required_mapping(
        context.handoff_receipt,
        label="candidate handoff receipt",
    )
    checksums = _required_mapping(
        context.checksum_subject,
        label="candidate checksum subject",
    )
    lineage = _required_mapping(
        context.governed_lineage,
        label="candidate governed lineage",
    )
    lineage_evidence = _required_mapping(
        lineage.get("evidence"),
        label="candidate governed lineage evidence",
    )
    if context.availability_receipt_sha256 is None:
        _fail("candidate lacks an availability receipt")
    if context.acceptance_sha256 is None or context.acceptance_signer_key_id is None:
        _fail("candidate lacks an acceptance authority")
    files = {
        "manifest.json": context.manifest_sha256,
        "artifact.jsonl": context.artifact_sha256,
        "input-ledger.jsonl": context.input_ledger_sha256,
        "availability.json": context.availability_receipt_sha256,
        "github-commit.json": commit.get("sha256"),
        "github-main-branch.json": main.get("sha256"),
        "handoff-receipt.json": handoff.get("sha256"),
        "SHA256SUMS": checksums.get("sha256"),
        "china-econ-wdi-lineage-chain.jsonl": lineage.get("sha256"),
        "github-commit-lineage-evidence.jsonl": lineage_evidence.get("sha256"),
        "acceptance.json": context.acceptance_sha256,
    }
    return {
        "schema": CANDIDATE_SCHEMA,
        "files": _file_hashes(files, label="candidate files"),
        "signer_key_id": context.acceptance_signer_key_id,
        "accepted_at": context.accepted_at,
        "rights_expires_at": expiry,
        "producer_repository": context.producer.get("repository"),
        "producer_sha": context.producer.get("commit_sha"),
        "producer_workflow_run_id": workflow.get("run_id"),
    }


def guarded_verify_main(arguments: list[str]) -> int:
    """Unprivileged exact-runtime child used by the installed launcher."""

    if len(arguments) != len(_BUNDLE_FILE_SPECS):
        _fail("guarded candidate verification requires exactly eleven paths")
    candidate = _candidate_from_context(
        BundleSources(*(Path(value) for value in arguments))
    )
    print(_canonical(candidate).decode("utf-8"), end="")
    return 0


_CHILD_CODE = """
import pathlib
import sys
release = pathlib.Path(sys.argv.pop(1))
sys.path.insert(0, str(release))
from seiche import palimpsest_china_activation as activation
expected = release / "seiche" / "palimpsest_china_activation.py"
if pathlib.Path(activation.__file__) != expected:
    raise SystemExit("activation module resolved outside the exact runtime")
raise SystemExit(activation.guarded_verify_main(sys.argv[1:]))
""".strip()


def _parse_candidate(body: bytes, *, expected: Mapping[str, str]) -> dict[str, Any]:
    candidate = _strict_json(body, label="candidate verification result")
    _exact_keys(candidate, _CANDIDATE_KEYS, label="candidate verification result")
    if candidate["schema"] != CANDIDATE_SCHEMA:
        _fail("candidate verification schema changed")
    files = _file_hashes(candidate["files"], label="candidate.files")
    expected_files = _file_hashes(dict(expected), label="installed files")
    if files != expected_files:
        _fail("candidate verifier returned mismatched runtime file hashes")
    _sha(candidate["signer_key_id"], label="candidate.signer_key_id")
    _timestamp(candidate["accepted_at"], label="candidate.accepted_at")
    expiry = _timestamp(
        candidate["rights_expires_at"], label="candidate.rights_expires_at"
    )
    if expiry <= datetime.now(UTC):
        _fail("candidate rights expired during activation")
    if candidate["producer_repository"] != "beepboop2025/palimpsest":
        _fail("candidate producer repository changed")
    _git_sha(candidate["producer_sha"], label="candidate.producer_sha")
    run_id = candidate["producer_workflow_run_id"]
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
        _fail("candidate producer run id is invalid")
    return candidate


def _verify_candidate(
    bundle: Path,
    *,
    hashes: Mapping[str, str],
    paths: ActivationPaths,
) -> dict[str, Any]:
    sources = BundleSources(*(bundle / spec.filename for spec in _BUNDLE_FILE_SPECS))
    if paths.portable:
        try:
            candidate = _candidate_from_context(
                sources,
                attest_dir=paths.attest_dir,
            )
        except (OSError, PalimpsestChinaIntakeError) as exc:
            raise PalimpsestChinaActivationError(
                "portable candidate verification failed"
            ) from exc
        return _parse_candidate(_canonical(candidate), expected=hashes)

    command = [
        str(paths.runuser),
        "-u",
        paths.api_user,
        "--",
        str(paths.env_program),
        "-i",
        "HOME=/var/lib/seiche",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        "PATH=/usr/bin:/bin",
        "PYTHONNOUSERSITE=1",
        str(paths.python),
        "-I",
        "-B",
        "-c",
        _CHILD_CODE,
        str(paths.runtime_release),
        *(str(path) for path in sources.files().values()),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "HOME": "/root",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
                "PYTHONNOUSERSITE": "1",
            },
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PalimpsestChinaActivationError(
            "unprivileged candidate verifier could not run"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[:1000]
        _fail(f"unprivileged candidate verifier rejected the bundle: {detail}")
    if len(result.stdout) > 64 * 1024:
        _fail("unprivileged candidate verifier returned excessive output")
    return _parse_candidate(result.stdout, expected=hashes)


def _validate_receipt(
    receipt: Mapping[str, Any], *, expected_path: Path | None = None
) -> None:
    _exact_keys(receipt, _RECEIPT_KEYS, label="activation receipt")
    if receipt["schema"] != ACTIVATION_RECEIPT_SCHEMA:
        _fail("activation receipt schema changed")
    for key in ("activation_id", "bundle_id", "signer_key_id"):
        _sha(receipt[key], label=f"activation receipt.{key}")
    files = _file_hashes(receipt["files"], label="activation receipt.files")
    if receipt["bundle_id"] != _bundle_id(files):
        _fail("activation receipt bundle identity changed")
    _git_sha(receipt["release_sha"], label="activation receipt.release_sha")
    _git_sha(receipt["producer_sha"], label="activation receipt.producer_sha")
    if receipt["producer_repository"] != "beepboop2025/palimpsest":
        _fail("activation receipt producer repository changed")
    run_id = receipt["producer_workflow_run_id"]
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
        _fail("activation receipt producer run id is invalid")
    accepted_at = _timestamp(
        receipt["accepted_at"], label="activation receipt.accepted_at"
    )
    expires_at = _timestamp(
        receipt["rights_expires_at"], label="activation receipt.rights_expires_at"
    )
    recorded_at = _timestamp(
        receipt["recorded_at"], label="activation receipt.recorded_at"
    )
    if (
        expires_at <= accepted_at
        or recorded_at < accepted_at
        or expires_at <= recorded_at
    ):
        _fail("activation receipt clocks are inconsistent")
    previous = receipt["previous_receipt_sha256"]
    if previous is not None:
        _sha(previous, label="activation receipt.previous_receipt_sha256")
    proof = receipt["runtime_proof"]
    if type(proof) is not dict:
        _fail("activation receipt runtime proof is malformed")
    _exact_keys(proof, _PROOF_KEYS, label="activation receipt.runtime_proof")
    if proof["schema"] != RUNTIME_PROOF_SCHEMA:
        _fail("activation receipt runtime proof schema changed")
    for key in ("api_url", "rest_path", "mcp_path"):
        value = proof[key]
        if type(value) is not str or not value or len(value) > 1024:
            _fail(f"activation receipt runtime_proof.{key} is invalid")
    if (
        not proof["api_url"].startswith("http://127.0.0.1:")
        or proof["api_url"].endswith("/")
        or proof["rest_path"] != "/api/v2/world-markets?section=china_macro"
        or proof["mcp_path"] != "/mcp"
    ):
        _fail("activation receipt runtime proof endpoints changed")
    for surface in ("rest", "mcp"):
        served_files = _file_hashes(
            proof[f"{surface}_files"],
            label=f"activation receipt.runtime_proof.{surface}_files",
        )
        if served_files != files:
            _fail(f"activation receipt {surface.upper()} proof files changed")
        served_signer = _sha(
            proof[f"{surface}_signer_key_id"],
            label=f"activation receipt.runtime_proof.{surface}_signer_key_id",
        )
        if served_signer != receipt["signer_key_id"]:
            _fail(f"activation receipt {surface.upper()} proof signer changed")
    verified_at = _timestamp(
        proof["verified_at"], label="activation receipt.runtime_proof.verified_at"
    )
    if verified_at < accepted_at or verified_at > recorded_at:
        _fail("activation receipt proof clock is inconsistent")
    if receipt["activation_id"] != _activation_id(
        bundle_id=receipt["bundle_id"],
        release_sha=receipt["release_sha"],
    ):
        _fail("activation receipt identity changed")
    if expected_path is not None and expected_path.name != (
        f"{receipt['activation_id']}.json"
    ):
        _fail("activation receipt filename does not bind its bundle")


def _read_receipt(
    path: Path, *, paths: ActivationPaths
) -> tuple[dict[str, Any], bytes]:
    body = _stable_read(
        path,
        label="activation receipt",
        maximum=16 * 1024,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o400}),
    )
    receipt = _strict_json(body, label="activation receipt")
    _validate_receipt(receipt, expected_path=path)
    return receipt, body


def _validate_active_semantics(
    value: Mapping[str, Any],
    *,
    paths: ActivationPaths,
    declared_receipts_dir: Path | None = None,
) -> None:
    for key in ("activation_id", "bundle_id", "receipt_sha256"):
        _sha(value[key], label=f"active marker.{key}")
    _git_sha(value["release_sha"], label="active marker.release_sha")
    files = _file_hashes(value["files"], label="active marker.files")
    if value["bundle_id"] != _bundle_id(files):
        _fail("active marker bundle identity changed")
    _timestamp(value["activated_at"], label="active marker.activated_at")
    if value["activation_id"] != _activation_id(
        bundle_id=value["bundle_id"], release_sha=value["release_sha"]
    ):
        _fail("active marker identity changed")
    expected = (declared_receipts_dir or paths.receipts_dir) / (
        f"{value['activation_id']}.json"
    )
    if value["receipt_path"] != str(expected):
        _fail("active marker receipt path changed")


def _validate_legacy_active(
    value: Mapping[str, Any],
    *,
    paths: ActivationPaths,
    declared_receipts_dir: Path | None = None,
    label: str = "legacy active marker",
) -> None:
    _exact_keys(value, _LEGACY_ACTIVE_KEYS, label=label)
    if value["schema"] != LEGACY_ACTIVE_MARKER_SCHEMA:
        _fail(f"{label} schema changed")
    _validate_active_semantics(
        value,
        paths=paths,
        declared_receipts_dir=declared_receipts_dir,
    )


def _validate_active(
    value: Mapping[str, Any],
    *,
    paths: ActivationPaths,
    declared_receipts_dir: Path | None = None,
) -> None:
    if value.get("schema") == LEGACY_ACTIVE_MARKER_SCHEMA:
        _validate_legacy_active(
            value,
            paths=paths,
            declared_receipts_dir=declared_receipts_dir,
        )
        return
    _exact_keys(value, _ACTIVE_KEYS, label="active marker")
    if value["schema"] != ACTIVE_MARKER_SCHEMA:
        _fail("active marker schema changed")
    _validate_active_semantics(
        value,
        paths=paths,
        declared_receipts_dir=declared_receipts_dir,
    )
    if value["publication_status"] != "provisional":
        _fail("active marker publication status must remain provisional")
    legacy_raw = value["legacy_active_marker"]
    legacy_digest = value["legacy_active_marker_sha256"]
    if legacy_raw is None and legacy_digest is None:
        return
    if not isinstance(legacy_raw, str):
        _fail("active marker legacy archive must be exact UTF-8 text")
    try:
        legacy_body = legacy_raw.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise PalimpsestChinaActivationError(
            "active marker legacy archive is not strict UTF-8"
        ) from exc
    _sha(legacy_digest, label="active marker.legacy_active_marker_sha256")
    if _digest(legacy_body) != legacy_digest:
        _fail("active marker legacy archive digest changed")
    legacy = _strict_json(legacy_body, label="active marker legacy archive")
    _validate_legacy_active(
        legacy,
        paths=paths,
        declared_receipts_dir=declared_receipts_dir,
        label="active marker legacy archive",
    )
    for key in _LEGACY_ACTIVE_KEYS - {"schema"}:
        if value[key] != legacy[key]:
            _fail("active marker legacy semantic projection changed")


def _read_active(
    paths: ActivationPaths,
    *,
    declared_receipts_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    marker = paths.active_marker
    if not marker.exists() and not marker.is_symlink():
        return None
    body = _stable_read(
        marker,
        label="active marker",
        maximum=MAX_ACTIVE_MARKER_BYTES,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o400}),
    )
    active = _strict_json(body, label="active marker")
    _validate_active(
        active,
        paths=paths,
        declared_receipts_dir=declared_receipts_dir,
    )
    receipt_path = paths.receipts_dir / f"{active['activation_id']}.json"
    receipt, receipt_body = _read_receipt(receipt_path, paths=paths)
    if (
        _digest(receipt_body) != active["receipt_sha256"]
        or receipt["activation_id"] != active["activation_id"]
        or receipt["bundle_id"] != active["bundle_id"]
        or receipt["release_sha"] != active["release_sha"]
        or receipt["files"] != active["files"]
    ):
        _fail("active marker does not bind its immutable receipt")
    activated_at = _timestamp(
        active["activated_at"], label="active marker.activated_at"
    )
    if activated_at < _timestamp(
        receipt["recorded_at"], label="activation receipt.recorded_at"
    ) or activated_at >= _timestamp(
        receipt["rights_expires_at"], label="activation receipt.rights_expires_at"
    ):
        _fail("active marker clock falls outside its proved rights interval")
    return active, receipt


def _durability_receipt_path(
    paths: ActivationPaths, *, activation_id: str, tree_sha256: str
) -> Path:
    _sha(activation_id, label="durability activation id")
    _sha(tree_sha256, label="durability state tree")
    return paths.resolved_durability_root / f"{activation_id}.{tree_sha256}.json"


def _validate_durability_receipt(
    value: Mapping[str, Any],
    *,
    active: Mapping[str, Any],
    activation_receipt: Mapping[str, Any],
    active_marker_sha256: str,
    activation_receipt_sha256: str,
    audit: Mapping[str, Any],
    expected_path: Path | None = None,
) -> None:
    _exact_keys(value, _DURABILITY_KEYS, label="activation durability receipt")
    if (
        value["schema"] != DURABILITY_RECEIPT_SCHEMA
        or value["status"] != "activated_durable"
    ):
        _fail("activation durability receipt status changed")
    for key in (
        "activation_id",
        "bundle_id",
        "active_marker_sha256",
        "activation_receipt_sha256",
        "activation_state_audit_sha256",
        "activation_state_tree_sha256",
        "local_backup_inventory_sha256",
        "local_restore_activation_id",
        "local_restore_tree_sha256",
        "local_restore_receipt_sha256",
        "offsite_activation_id",
        "offsite_tree_sha256",
        "offsite_remote_receipt_sha256",
    ):
        _sha(value[key], label=f"activation durability receipt.{key}")
    _git_sha(value["release_sha"], label="activation durability receipt.release_sha")
    if (
        value["activation_id"] != active["activation_id"]
        or value["bundle_id"] != active["bundle_id"]
        or value["active_marker_sha256"] != active_marker_sha256
        or value["activation_receipt_sha256"] != activation_receipt_sha256
        or value["activation_state_audit_sha256"] != _digest(_canonical(audit))
        or value["activation_state_tree_sha256"] != audit.get("tree_sha256")
        or value["local_restore_activation_id"] != active["activation_id"]
        or value["local_restore_tree_sha256"] != audit.get("tree_sha256")
        or value["offsite_activation_id"] != active["activation_id"]
        or value["offsite_tree_sha256"] != audit.get("tree_sha256")
    ):
        _fail("activation durability receipt does not bind the live activation")
    if value["local_restore_schema"] != "seiche.market-backup-restore-check.v5":
        _fail("activation durability local restore schema changed")
    if value["offsite_status_schema"] != "seiche.market-offsite-backup-status.v4":
        _fail("activation durability offsite status schema changed")
    snapshot = value["local_backup_snapshot"]
    if (
        type(snapshot) is not str
        or re.fullmatch(r"20[0-9]{6}T[0-9]{6}Z", snapshot) is None
        or value["offsite_snapshot"] != snapshot
    ):
        _fail("activation durability snapshot identity changed")
    attempt = value["offsite_attempt_id"]
    if (
        type(attempt) is not str
        or re.fullmatch(r"20[0-9]{6}T[0-9]{6}Z-[0-9]+", attempt) is None
    ):
        _fail("activation durability offsite attempt is invalid")
    remote_key = value["offsite_remote_receipt_key"]
    if (
        type(remote_key) is not str
        or not remote_key.endswith(
            f"/snapshots/{snapshot}/attempts/{attempt}/RECEIPT.json"
        )
        or "/canary/" in remote_key
    ):
        _fail("activation durability requires a scheduled offsite receipt")
    local_checked = _timestamp(
        value["local_restore_checked_at"],
        label="activation durability receipt.local_restore_checked_at",
    )
    embedded_restore = value["local_restore_receipt"]
    if type(embedded_restore) is not str:
        _fail("activation durability local restore receipt is malformed")
    embedded_restore_body = embedded_restore.encode("utf-8")
    restore = _local_restore_fields(embedded_restore_body)
    count_shape = r"[0-9]+\|[0-9]+\|[0-9]+\|[0-9]+"
    if (
        _digest(embedded_restore_body) != value["local_restore_receipt_sha256"]
        or restore["schema"] != value["local_restore_schema"]
        or restore["checked_at"] != value["local_restore_checked_at"]
        or restore["snapshot"] != snapshot
        or restore["source_backup_schema"] != "seiche.market-backup.v4"
        or restore["deployed_sha"] != value["release_sha"]
        or restore["palimpsest_china_state_archive_restore"] != "verified"
        or restore["palimpsest_china_state_audit_contract"]
        != "seiche.palimpsest-china-activation-state.v1"
        or restore["palimpsest_china_state_tree_sha256"] != audit.get("tree_sha256")
        or restore["palimpsest_china_active_activation_id"] != active["activation_id"]
        or restore["palimpsest_china_pending_candidate_activation_id"] != "none"
        or restore["database_restore"] != "pass"
        or restore["state_archive_restore"] != "pass"
        or restore["api_data_archive_restore"] != "pass"
        or restore["research_only"] != "true"
        or restore["can_publish"] != "false"
        or restore["can_execute"] != "false"
        or restore["nbs_full_store_audit_contract"] != "seiche.nbs-full-store-audit.v1"
        or restore["nbs_full_store_audit_result"]
        != restore["nbs_public_revision_store"]
        or restore["nbs_full_store_audit_result"]
        not in {"not_onboarded", "verified_head"}
        or re.fullmatch(count_shape, restore["critical_table_counts"]) is None
        or re.fullmatch(count_shape, restore["critical_table_count_floor"]) is None
        or re.fullmatch(r"[0-9]+", restore["palimpsest_china_bundle_count"]) is None
        or re.fullmatch(r"[0-9]+", restore["palimpsest_china_receipt_count"]) is None
    ):
        _fail("activation durability embedded local restore proof changed")
    offsite_verified = _timestamp(
        value["offsite_verified_at"],
        label="activation durability receipt.offsite_verified_at",
    )
    completed = _timestamp(
        value["completed_at"], label="activation durability receipt.completed_at"
    )
    recorded = _timestamp(
        activation_receipt["recorded_at"],
        label="activation receipt.recorded_at",
    )
    activated = _timestamp(active["activated_at"], label="active marker.activated_at")
    if not (recorded <= activated <= local_checked <= offsite_verified <= completed):
        _fail("activation durability proof chronology regressed")
    if completed >= _timestamp(
        activation_receipt["rights_expires_at"],
        label="activation receipt.rights_expires_at",
    ):
        _fail("activation durability completed after serving rights expired")
    if expected_path is not None and expected_path.name != (
        f"{active['activation_id']}.{audit['tree_sha256']}.json"
    ):
        _fail("activation durability receipt filename changed")


def _read_durability_receipt(
    paths: ActivationPaths,
    *,
    active: Mapping[str, Any],
    activation_receipt: Mapping[str, Any],
    audit: Mapping[str, Any],
) -> tuple[dict[str, Any], bytes, Path] | None:
    active_body = _stable_read(
        paths.active_marker,
        label="active marker",
        maximum=MAX_ACTIVE_MARKER_BYTES,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o400}),
    )
    receipt_path = paths.receipts_dir / f"{active['activation_id']}.json"
    receipt_body = _stable_read(
        receipt_path,
        label="activation receipt",
        maximum=16 * 1024,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o400}),
    )
    path = _durability_receipt_path(
        paths,
        activation_id=active["activation_id"],
        tree_sha256=audit["tree_sha256"],
    )
    if not path.exists() and not path.is_symlink():
        return None
    _validate_directory(
        paths.resolved_durability_root,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o700,
        label="Palimpsest China durability receipt root",
        validate_ancestry=not paths.portable,
    )
    body = _stable_read(
        path,
        label="activation durability receipt",
        maximum=64 * 1024,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o400}),
    )
    value = _strict_json(body, label="activation durability receipt")
    _validate_durability_receipt(
        value,
        active=active,
        activation_receipt=activation_receipt,
        active_marker_sha256=_digest(active_body),
        activation_receipt_sha256=_digest(receipt_body),
        audit=audit,
        expected_path=path,
    )
    return value, body, path


def _validate_pending(value: Mapping[str, Any]) -> None:
    _exact_keys(value, _PENDING_KEYS, label="pending activation marker")
    if value["schema"] != PENDING_SCHEMA:
        _fail("pending activation marker schema changed")
    _sha(
        value["candidate_activation_id"],
        label="pending activation candidate_activation_id",
    )
    _sha(value["candidate_bundle_id"], label="pending activation candidate_bundle_id")
    candidate_files = _file_hashes(
        value["candidate_files"],
        label="pending activation candidate_files",
    )
    if value["candidate_bundle_id"] != _bundle_id(candidate_files):
        _fail("pending activation bundle identity changed")
    previous = value["previous_activation_id"]
    if previous is not None:
        _sha(previous, label="pending activation previous_activation_id")
    _timestamp(value["started_at"], label="pending activation started_at")


def _read_pending(paths: ActivationPaths) -> dict[str, Any] | None:
    if not paths.pending_marker.exists() and not paths.pending_marker.is_symlink():
        return None
    body = _stable_read(
        paths.pending_marker,
        label="pending activation marker",
        maximum=8192,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o400}),
    )
    pending = _strict_json(body, label="pending activation marker")
    _validate_pending(pending)
    return pending


def _remove_controlled_file(
    path: Path,
    *,
    label: str,
    maximum: int,
    uid: int,
    gid: int,
    mode: int,
) -> None:
    if not path.exists() and not path.is_symlink():
        return
    _stable_read(
        path,
        label=label,
        maximum=maximum,
        uid=uid,
        gid=gid,
        modes=frozenset({mode}),
    )
    try:
        path.unlink()
        _fsync_directory(path.parent)
    except OSError as exc:
        raise PalimpsestChinaActivationError(f"could not remove {label}") from exc


def _render_legacy_env(bundle: Path) -> bytes:
    lines = [
        f"{spec.environment}={bundle / spec.filename}\n" for spec in _BUNDLE_FILE_SPECS
    ]
    try:
        return "".join(lines).encode("ascii")
    except UnicodeEncodeError as exc:
        raise PalimpsestChinaActivationError(
            "runtime bundle path must be ASCII"
        ) from exc


def _render_env(bundle: Path) -> bytes:
    return _render_legacy_env(bundle) + (
        b"SEICHE_PALIMPSEST_CHINA_PUBLICATION_STATUS=provisional\n"
    )


def _render_dropin(bundle: Path, *, env_file: Path) -> bytes:
    return (
        "[Unit]\n"
        f"RequiresMountsFor={bundle} {env_file}\n\n"
        "[Service]\n"
        f"EnvironmentFile={env_file}\n"
        f"ReadOnlyPaths={bundle} {env_file}\n"
    ).encode("ascii")


def _snapshot(
    path: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
    maximum: int,
) -> _FileSnapshot:
    if not path.exists() and not path.is_symlink():
        return _FileSnapshot(path, None, uid, gid, mode)
    body = _stable_read(
        path,
        label=f"pre-activation {path.name}",
        maximum=maximum,
        uid=uid,
        gid=gid,
        modes=frozenset({mode}),
    )
    return _FileSnapshot(path, body, uid, gid, mode)


def _restore(snapshot: _FileSnapshot) -> None:
    if snapshot.body is None:
        if snapshot.path.exists() or snapshot.path.is_symlink():
            metadata = snapshot.path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != snapshot.uid
                or metadata.st_gid != snapshot.gid
                or stat.S_IMODE(metadata.st_mode) != snapshot.mode
            ):
                _fail(f"refusing to remove unsafe rollback target {snapshot.path}")
            snapshot.path.unlink()
            _fsync_directory(snapshot.path.parent)
        return
    _atomic_write(
        snapshot.path,
        snapshot.body,
        uid=snapshot.uid,
        gid=snapshot.gid,
        mode=snapshot.mode,
    )


def _economic_context(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    china = payload.get("china_macro")
    if not isinstance(china, Mapping):
        _fail("China REST/MCP proof omitted china_macro")
    economic = china.get("economic_context")
    if economic is None:
        return None
    if not isinstance(economic, Mapping):
        _fail("China REST/MCP economic context is malformed")
    return economic


def _assert_projection(
    payload: Mapping[str, Any], expected: Mapping[str, Any] | None
) -> None:
    economic = _economic_context(payload)
    if expected is None:
        if economic is not None:
            _fail("rollback proof still exposes a China economic bundle")
        return
    if economic is None:
        _fail("activation proof did not expose the accepted China context")
    provenance = economic.get("provenance")
    if not isinstance(provenance, Mapping):
        _fail("activation proof returned malformed China provenance")
    files = _file_hashes(expected.get("files"), label="served expected files")
    commit = provenance.get("producer_commit_evidence")
    main = provenance.get("producer_main_evidence")
    handoff = provenance.get("handoff_receipt")
    checksums = provenance.get("checksum_subject")
    lineage = provenance.get("governed_lineage")
    lineage_evidence = lineage.get("evidence") if isinstance(lineage, Mapping) else None
    exact = {
        "manifest.json": provenance.get("manifest_sha256"),
        "artifact.jsonl": provenance.get("artifact_sha256"),
        "input-ledger.jsonl": provenance.get("input_ledger_sha256"),
        "availability.json": provenance.get("availability_receipt_sha256"),
        "github-commit.json": (
            commit.get("sha256") if isinstance(commit, Mapping) else None
        ),
        "github-main-branch.json": (
            main.get("sha256") if isinstance(main, Mapping) else None
        ),
        "handoff-receipt.json": (
            handoff.get("sha256") if isinstance(handoff, Mapping) else None
        ),
        "SHA256SUMS": (
            checksums.get("sha256") if isinstance(checksums, Mapping) else None
        ),
        "china-econ-wdi-lineage-chain.jsonl": (
            lineage.get("sha256") if isinstance(lineage, Mapping) else None
        ),
        "github-commit-lineage-evidence.jsonl": (
            lineage_evidence.get("sha256")
            if isinstance(lineage_evidence, Mapping)
            else None
        ),
        "acceptance.json": provenance.get("acceptance_sha256"),
    }
    if (
        economic.get("schema") != "seiche.palimpsest-china-economic-context.v1"
        or economic.get("context_only") is not True
        or economic.get("publication_status") != "provisional"
        or economic.get("scoring_eligible") is not False
        or economic.get("cn_cny_gauge_eligible") is not False
        or provenance.get("owner_attestation") != "ed25519"
        or provenance.get("acceptance_signer_key_id") != expected.get("signer_key_id")
        or exact != files
    ):
        _fail("activation proof returned the wrong China economic authority")


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, method=method, headers=headers)

    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(
            self,
            request: Request,
            file_pointer: Any,
            code: int,
            message: str,
            headers: Any,
            new_url: str,
        ) -> None:
            del request, file_pointer, code, message, headers, new_url
            return None

    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            if response.status != 200:
                _fail(f"proof endpoint returned HTTP {response.status}")
            payload = response.read(4 * 1024 * 1024 + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise PalimpsestChinaActivationError(
            f"proof endpoint is unavailable: {url}"
        ) from exc
    if len(payload) > 4 * 1024 * 1024:
        _fail("proof endpoint response is too large")
    try:
        text = payload.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise PalimpsestChinaActivationError(
            "proof response is not strict UTF-8"
        ) from exc

    def reject_constant(value: str) -> None:
        raise PalimpsestChinaActivationError(
            f"proof response contains non-finite JSON number {value}"
        )

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                _fail(f"proof response contains duplicate key {key!r}")
            out[key] = value
        return out

    try:
        value = json.loads(
            text,
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise PalimpsestChinaActivationError(
            "proof response is not valid JSON"
        ) from exc
    if type(value) is not dict:
        _fail("proof response must be an object")
    return value


def _probe_rest_and_mcp(
    *, paths: ActivationPaths, expected: Mapping[str, Any] | None
) -> None:
    rest = _http_json(f"{paths.api_url}/api/v2/world-markets?section=china_macro")
    _assert_projection(rest, expected)
    call = _canonical(
        {
            "jsonrpc": "2.0",
            "id": "palimpsest-china-activation-proof",
            "method": "tools/call",
            "params": {
                "name": "world_markets_context",
                "arguments": {"section": "china_macro"},
            },
        }
    )
    rpc = _http_json(f"{paths.api_url}/mcp", method="POST", body=call)
    if (
        rpc.get("jsonrpc") != "2.0"
        or rpc.get("id") != "palimpsest-china-activation-proof"
    ):
        _fail("MCP activation proof response identity changed")
    result = rpc.get("result")
    content = result.get("content") if isinstance(result, Mapping) else None
    if (
        not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], Mapping)
        or content[0].get("type") != "text"
        or not isinstance(content[0].get("text"), str)
    ):
        _fail("MCP activation proof response is malformed")
    try:
        mcp_payload = json.loads(content[0]["text"])
    except json.JSONDecodeError as exc:
        raise PalimpsestChinaActivationError(
            "MCP activation proof text is not JSON"
        ) from exc
    if not isinstance(mcp_payload, dict):
        _fail("MCP activation proof payload is not an object")
    _assert_projection(mcp_payload, expected)


def _systemctl(paths: ActivationPaths, *arguments: str) -> None:
    try:
        result = subprocess.run(
            [str(paths.systemctl), *arguments],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env={
                "HOME": "/root",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
            },
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PalimpsestChinaActivationError(
            f"systemctl {' '.join(arguments)} could not run"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[:1000]
        _fail(f"systemctl {' '.join(arguments)} failed: {detail}")


def _served_proof(
    *,
    paths: ActivationPaths,
    expected: Mapping[str, Any],
    clock_floor: datetime | None = None,
) -> dict[str, Any]:
    files = _file_hashes(expected.get("files"), label="served proof files")
    signer_key_id = _sha(
        expected.get("signer_key_id"),
        label="served proof signer_key_id",
    )
    return {
        "schema": RUNTIME_PROOF_SCHEMA,
        "api_url": paths.api_url,
        "rest_path": "/api/v2/world-markets?section=china_macro",
        "mcp_path": "/mcp",
        "rest_files": files,
        "rest_signer_key_id": signer_key_id,
        "mcp_files": files,
        "mcp_signer_key_id": signer_key_id,
        "verified_at": _now_text_at_or_after(
            clock_floor, label="retained activation proof clock"
        ),
    }


def _restart_and_probe(
    *,
    paths: ActivationPaths,
    expected: Mapping[str, Any] | None,
    clock_floor: datetime | None = None,
) -> dict[str, Any] | None:
    if paths.portable:
        return (
            None
            if expected is None
            else _served_proof(paths=paths, expected=expected, clock_floor=clock_floor)
        )
    _systemctl(paths, "daemon-reload")
    _systemctl(paths, "restart", "seiche-api.service")
    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
    last_error = ""
    while True:
        try:
            _probe_rest_and_mcp(paths=paths, expected=expected)
            return (
                None
                if expected is None
                else _served_proof(
                    paths=paths,
                    expected=expected,
                    clock_floor=clock_floor,
                )
            )
        except PalimpsestChinaActivationError as exc:
            last_error = str(exc)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _fail(f"REST/MCP activation proof timed out: {last_error}")
        time.sleep(min(PROBE_INTERVAL_SECONDS, remaining))


def _active_expected(
    active_receipt: tuple[dict[str, Any], dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if active_receipt is None:
        return None
    active, _receipt = active_receipt
    return {
        "files": dict(active["files"]),
        "signer_key_id": _receipt["signer_key_id"],
    }


def _validate_bundle_hashes(
    bundle: Path, *, expected: Mapping[str, str], paths: ActivationPaths
) -> None:
    files = _file_hashes(dict(expected), label="installed bundle files")
    _validate_directory(
        bundle,
        uid=paths.root_uid,
        gid=paths.api_gid,
        mode=0o750,
        label="installed Palimpsest China bundle",
    )
    try:
        names = {entry.name for entry in bundle.iterdir()}
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            "installed Palimpsest China bundle cannot be listed"
        ) from exc
    if names != _BUNDLE_FILENAMES:
        _fail("installed Palimpsest China bundle members changed")
    for spec in _BUNDLE_FILE_SPECS:
        body = _stable_read(
            bundle / spec.filename,
            label=f"installed bundle {spec.filename}",
            maximum=spec.maximum,
            uid=paths.root_uid,
            gid=paths.api_gid,
            modes=frozenset({0o440}),
        )
        if _digest(body) != files[spec.filename]:
            _fail(f"installed bundle {spec.filename} digest changed")


def _normalize_state_entry(
    path: Path,
    *,
    directory: bool,
    uid: int,
    gid: int,
    mode: int,
) -> None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    if directory:
        flags |= os.O_DIRECTORY
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        visible = os.stat(path, follow_symlinks=False)
        if (
            (
                not stat.S_ISDIR(metadata.st_mode)
                if directory
                else not stat.S_ISREG(metadata.st_mode)
            )
            or (not directory and metadata.st_nlink != 1)
            or (metadata.st_dev, metadata.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            _fail(f"restored activation-state entry is unsafe: {path}")
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            f"restored activation-state entry cannot be normalized: {path}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _activation_audit_paths(
    state_root: Path,
    *,
    root_uid: int,
    root_gid: int,
    api_uid: int,
    api_gid: int,
) -> ActivationPaths:
    return ActivationPaths(
        state_root=state_root,
        env_file=state_root / ".unused-env",
        dropin_file=state_root / ".unused-dropin",
        deploy_lock=state_root / ".unused-deploy-lock",
        activation_lock=state_root / ".unused-activation-lock",
        runtime_release=state_root / ".unused-runtime",
        release_sha="0" * 40,
        root_uid=root_uid,
        root_gid=root_gid,
        api_uid=api_uid,
        api_gid=api_gid,
        portable=True,
    )


def audit_activation_state(
    state_root: Path,
    *,
    root_uid: int,
    root_gid: int,
    api_uid: int,
    api_gid: int,
    normalize_restored: bool = False,
    declared_state_root: Path | None = None,
) -> dict[str, Any]:
    """Validate or normalize one isolated backup of the activation state tree."""

    state_root = Path(state_root)
    declared_state_root = (
        state_root if declared_state_root is None else Path(declared_state_root)
    )
    if (
        not state_root.is_absolute()
        or state_root == Path("/")
        or Path(os.path.normpath(state_root)) != state_root
        or not declared_state_root.is_absolute()
        or declared_state_root == Path("/")
        or Path(os.path.normpath(declared_state_root)) != declared_state_root
        or type(normalize_restored) is not bool
    ):
        _fail("activation-state audit path or mode is invalid")
    try:
        root_metadata = state_root.lstat()
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            "activation-state audit root is unavailable"
        ) from exc
    if not stat.S_ISDIR(root_metadata.st_mode):
        _fail("activation-state audit root is not a directory")
    try:
        root_entries = {entry.name: entry for entry in os.scandir(state_root)}
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            "activation-state audit root cannot be enumerated"
        ) from exc

    receipt_names: set[str] = set()
    bundle_names: set[str] = set()
    for name, entry in root_entries.items():
        metadata = entry.stat(follow_symlinks=False)
        if name == "receipts":
            if not stat.S_ISDIR(metadata.st_mode):
                _fail("activation-state receipts root is unsafe")
        elif name in {"active.json", "pending.json"}:
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                _fail(f"activation-state control file is unsafe: {name}")
        elif _SHA256_RE.fullmatch(name) is not None:
            if not stat.S_ISDIR(metadata.st_mode):
                _fail(f"activation-state bundle is unsafe: {name}")
            bundle_names.add(name)
        else:
            _fail(f"activation-state root contains unexpected member: {name}")
    if "receipts" not in root_entries:
        _fail("activation-state receipts root is missing")

    receipts_dir = state_root / "receipts"
    try:
        receipt_entries = {entry.name: entry for entry in os.scandir(receipts_dir)}
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            "activation-state receipts cannot be enumerated"
        ) from exc
    for name, entry in receipt_entries.items():
        metadata = entry.stat(follow_symlinks=False)
        if (
            re.fullmatch(r"[0-9a-f]{64}\.json", name) is None
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            _fail(f"activation-state receipt is unsafe: {name}")
        receipt_names.add(name)

    for bundle_name in bundle_names:
        bundle = state_root / bundle_name
        try:
            entries = {entry.name: entry for entry in os.scandir(bundle)}
        except OSError as exc:
            raise PalimpsestChinaActivationError(
                f"activation-state bundle cannot be enumerated: {bundle_name}"
            ) from exc
        if frozenset(entries) != _BUNDLE_FILENAMES:
            _fail(f"activation-state bundle members changed: {bundle_name}")
        for name, entry in entries.items():
            metadata = entry.stat(follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                _fail(f"activation-state bundle file is unsafe: {bundle_name}/{name}")

    if normalize_restored:
        _normalize_state_entry(
            state_root,
            directory=True,
            uid=root_uid,
            gid=api_gid,
            mode=0o750,
        )
        _normalize_state_entry(
            receipts_dir,
            directory=True,
            uid=root_uid,
            gid=root_gid,
            mode=0o700,
        )
        for name in receipt_names:
            _normalize_state_entry(
                receipts_dir / name,
                directory=False,
                uid=root_uid,
                gid=root_gid,
                mode=0o400,
            )
        for name in ("active.json", "pending.json"):
            if name in root_entries:
                _normalize_state_entry(
                    state_root / name,
                    directory=False,
                    uid=root_uid,
                    gid=root_gid,
                    mode=0o400,
                )
        for bundle_name in bundle_names:
            bundle = state_root / bundle_name
            _normalize_state_entry(
                bundle,
                directory=True,
                uid=root_uid,
                gid=api_gid,
                mode=0o750,
            )
            for spec in _BUNDLE_FILE_SPECS:
                _normalize_state_entry(
                    bundle / spec.filename,
                    directory=False,
                    uid=root_uid,
                    gid=api_gid,
                    mode=0o440,
                )
        _fsync_directory(receipts_dir)
        for bundle_name in bundle_names:
            _fsync_directory(state_root / bundle_name)
        _fsync_directory(state_root)

    paths = _activation_audit_paths(
        state_root,
        root_uid=root_uid,
        root_gid=root_gid,
        api_uid=api_uid,
        api_gid=api_gid,
    )
    _validate_directory(
        state_root,
        uid=root_uid,
        gid=api_gid,
        mode=0o750,
        label="activation-state root",
    )
    _validate_directory(
        receipts_dir,
        uid=root_uid,
        gid=root_gid,
        mode=0o700,
        label="activation-state receipts root",
    )

    receipts: list[dict[str, Any]] = []
    for name in sorted(receipt_names):
        receipt, _body = _read_receipt(receipts_dir / name, paths=paths)
        receipts.append(receipt)

    bundle_hashes: dict[str, dict[str, str]] = {}
    for bundle_name in sorted(bundle_names):
        hashes: dict[str, str] = {}
        for spec in _BUNDLE_FILE_SPECS:
            body = _stable_read(
                state_root / bundle_name / spec.filename,
                label=f"activation-state bundle {bundle_name}/{spec.filename}",
                maximum=spec.maximum,
                uid=root_uid,
                gid=api_gid,
                modes=frozenset({0o440}),
            )
            hashes[spec.filename] = _digest(body)
        if _bundle_id(hashes) != bundle_name:
            _fail(f"activation-state bundle identity changed: {bundle_name}")
        _validate_bundle_hashes(
            state_root / bundle_name,
            expected=hashes,
            paths=paths,
        )
        bundle_hashes[bundle_name] = hashes

    for receipt in receipts:
        if receipt["bundle_id"] not in bundle_hashes:
            _fail("activation-state receipt names a missing bundle")
        if receipt["files"] != bundle_hashes[receipt["bundle_id"]]:
            _fail("activation-state receipt bundle files changed")

    active_receipt = _read_active(
        paths,
        declared_receipts_dir=declared_state_root / "receipts",
    )
    active_id = active_receipt[0]["activation_id"] if active_receipt else None
    if active_receipt is not None:
        active = active_receipt[0]
        if active["bundle_id"] not in bundle_hashes:
            _fail("activation-state active marker names a missing bundle")
        _validate_bundle_hashes(
            state_root / active["bundle_id"],
            expected=active["files"],
            paths=paths,
        )

    pending = _read_pending(paths)
    pending_id = pending["candidate_activation_id"] if pending else None
    if pending is not None:
        candidate_bundle = pending["candidate_bundle_id"]
        if candidate_bundle not in bundle_hashes:
            _fail("activation-state pending marker names a missing bundle")
        if bundle_hashes[candidate_bundle] != pending["candidate_files"]:
            _fail("activation-state pending marker files changed")
        if active_id == pending_id:
            if (
                active_receipt is None
                or active_receipt[0]["files"] != pending["candidate_files"]
            ):
                _fail("activation-state completed pending marker changed")
        elif active_id != pending["previous_activation_id"]:
            _fail("activation-state pending marker does not bind active state")

    tree_entries: list[dict[str, Any]] = [
        {
            "group": "api",
            "kind": "directory",
            "mode": "0750",
            "owner": "root",
            "path": ".",
        },
        {
            "group": "root",
            "kind": "directory",
            "mode": "0700",
            "owner": "root",
            "path": "receipts",
        },
    ]
    for name in sorted(receipt_names):
        body = _stable_read(
            receipts_dir / name,
            label=f"activation-state receipt {name}",
            maximum=16 * 1024,
            uid=root_uid,
            gid=root_gid,
            modes=frozenset({0o400}),
        )
        tree_entries.append(
            {
                "bytes": len(body),
                "group": "root",
                "kind": "file",
                "mode": "0400",
                "owner": "root",
                "path": f"receipts/{name}",
                "sha256": _digest(body),
            }
        )
    for name in ("active.json", "pending.json"):
        if name not in root_entries:
            continue
        body = _stable_read(
            state_root / name,
            label=f"activation-state {name}",
            maximum=(MAX_ACTIVE_MARKER_BYTES if name == "active.json" else 8192),
            uid=root_uid,
            gid=root_gid,
            modes=frozenset({0o400}),
        )
        tree_entries.append(
            {
                "bytes": len(body),
                "group": "root",
                "kind": "file",
                "mode": "0400",
                "owner": "root",
                "path": name,
                "sha256": _digest(body),
            }
        )
    for bundle_name in sorted(bundle_names):
        tree_entries.append(
            {
                "group": "api",
                "kind": "directory",
                "mode": "0750",
                "owner": "root",
                "path": bundle_name,
            }
        )
        for spec in _BUNDLE_FILE_SPECS:
            path = state_root / bundle_name / spec.filename
            metadata = path.stat()
            tree_entries.append(
                {
                    "bytes": metadata.st_size,
                    "group": "api",
                    "kind": "file",
                    "mode": "0440",
                    "owner": "root",
                    "path": f"{bundle_name}/{spec.filename}",
                    "sha256": bundle_hashes[bundle_name][spec.filename],
                }
            )
    tree_body = _canonical({"entries": tree_entries})
    return {
        "schema": BACKUP_STATE_SCHEMA,
        "state_root": str(declared_state_root),
        "tree_sha256": _digest(tree_body),
        "bundles": sorted(bundle_names),
        "receipts": [receipt["activation_id"] for receipt in receipts],
        "active_activation_id": active_id,
        "pending_candidate_activation_id": pending_id,
    }


def activation_durability_status(paths: ActivationPaths) -> dict[str, Any]:
    """Return the exact live-tree durability overlay without mutating state.

    ``paths.release_sha`` identifies the trusted code performing this audit;
    the active marker and receipt retain the historical release that performed
    activation. A later signed Seiche release does not relabel unchanged data.
    """

    active_pair = _read_active(paths)
    if active_pair is None:
        return {
            "schema": "seiche.palimpsest-china-durability-status.v1",
            "status": "inactive",
            "activation_id": None,
            "tree_sha256": None,
            "durability_receipt_path": None,
            "durability_receipt_sha256": None,
        }
    active, activation_receipt = active_pair
    audit = audit_activation_state(
        paths.state_root,
        root_uid=paths.root_uid,
        root_gid=paths.root_gid,
        api_uid=paths.api_uid,
        api_gid=paths.api_gid,
        declared_state_root=(
            paths.state_root if paths.portable else PRODUCTION_STATE_ROOT
        ),
    )
    if audit["active_activation_id"] != active["activation_id"]:
        _fail("live activation and canonical state audit disagree")
    if audit["pending_candidate_activation_id"] is not None:
        _fail("pending activation cannot be declared durable")
    durable = None
    if active["schema"] == ACTIVE_MARKER_SCHEMA:
        durable = _read_durability_receipt(
            paths,
            active=active,
            activation_receipt=activation_receipt,
            audit=audit,
        )
    return {
        "schema": "seiche.palimpsest-china-durability-status.v1",
        "status": "activated_durable" if durable is not None else "provisional",
        "activation_id": active["activation_id"],
        "tree_sha256": audit["tree_sha256"],
        "durability_receipt_path": str(durable[2]) if durable is not None else None,
        "durability_receipt_sha256": (
            _digest(durable[1]) if durable is not None else None
        ),
    }


def seal_activation_durability(
    paths: ActivationPaths, evidence: Mapping[str, Any]
) -> tuple[dict[str, Any], bytes, Path]:
    """Publish one immutable exact-live-tree receipt outside the backed-up tree."""

    expected_evidence = _DURABILITY_KEYS - {
        "schema",
        "status",
        "activation_id",
        "bundle_id",
        "release_sha",
        "active_marker_sha256",
        "activation_receipt_sha256",
        "activation_state_audit_sha256",
        "activation_state_tree_sha256",
        "completed_at",
    }
    _exact_keys(evidence, expected_evidence, label="activation durability evidence")
    active_pair = _read_active(paths)
    if active_pair is None:
        _fail("inactive Palimpsest China state cannot be sealed")
    active, activation_receipt = active_pair
    if active["schema"] != ACTIVE_MARKER_SCHEMA:
        _fail(
            "legacy active marker must complete its one-way v2 migration before sealing"
        )
    audit = audit_activation_state(
        paths.state_root,
        root_uid=paths.root_uid,
        root_gid=paths.root_gid,
        api_uid=paths.api_uid,
        api_gid=paths.api_gid,
        declared_state_root=(
            paths.state_root if paths.portable else PRODUCTION_STATE_ROOT
        ),
    )
    if (
        audit["active_activation_id"] != active["activation_id"]
        or audit["pending_candidate_activation_id"] is not None
    ):
        _fail("live activation state is not sealable")
    _validate_directory(
        paths.resolved_durability_root,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o700,
        label="Palimpsest China durability receipt root",
        validate_ancestry=not paths.portable,
    )
    active_body = _stable_read(
        paths.active_marker,
        label="active marker",
        maximum=MAX_ACTIVE_MARKER_BYTES,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o400}),
    )
    activation_receipt_path = paths.receipts_dir / f"{active['activation_id']}.json"
    activation_receipt_body = _stable_read(
        activation_receipt_path,
        label="activation receipt",
        maximum=16 * 1024,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o400}),
    )
    floor = max(
        _timestamp(
            activation_receipt["recorded_at"],
            label="activation receipt.recorded_at",
        ),
        _timestamp(active["activated_at"], label="active marker.activated_at"),
        _timestamp(
            evidence["local_restore_checked_at"],
            label="activation durability evidence.local_restore_checked_at",
        ),
        _timestamp(
            evidence["offsite_verified_at"],
            label="activation durability evidence.offsite_verified_at",
        ),
    )
    payload = {
        "schema": DURABILITY_RECEIPT_SCHEMA,
        "status": "activated_durable",
        "activation_id": active["activation_id"],
        "bundle_id": active["bundle_id"],
        # The marker and immutable activation receipt retain the historical
        # release that first published these exact bytes. The durability proof
        # is produced by the currently deployed, independently verified
        # release and must bind that restore/offsite execution identity.
        "release_sha": paths.release_sha,
        "active_marker_sha256": _digest(active_body),
        "activation_receipt_sha256": _digest(activation_receipt_body),
        "activation_state_audit_sha256": _digest(_canonical(audit)),
        "activation_state_tree_sha256": audit["tree_sha256"],
        **dict(evidence),
        "completed_at": _now_text_at_or_after(
            floor, label="activation durability evidence"
        ),
    }
    path = _durability_receipt_path(
        paths,
        activation_id=active["activation_id"],
        tree_sha256=audit["tree_sha256"],
    )
    _validate_durability_receipt(
        payload,
        active=active,
        activation_receipt=activation_receipt,
        active_marker_sha256=_digest(active_body),
        activation_receipt_sha256=_digest(activation_receipt_body),
        audit=audit,
        expected_path=path,
    )
    body = _canonical(payload)
    _publish_immutable_atomic(
        path,
        body,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o400,
        portable=paths.portable,
    )
    loaded = _read_durability_receipt(
        paths,
        active=active,
        activation_receipt=activation_receipt,
        audit=audit,
    )
    if loaded is None or loaded[0] != payload or loaded[1] != body:
        _fail("activation durability receipt could not be read back exactly")
    return loaded


def _assert_config_consistency(
    active_receipt: tuple[dict[str, Any], dict[str, Any]] | None,
    *,
    paths: ActivationPaths,
) -> None:
    exists = [
        path.exists() or path.is_symlink()
        for path in (paths.env_file, paths.dropin_file, paths.active_marker)
    ]
    if active_receipt is None:
        if any(exists):
            _fail("partial Palimpsest China activation state requires operator review")
        return
    if not all(exists):
        _fail("active marker exists without complete API configuration")
    active, _receipt = active_receipt
    bundle = paths.state_root / active["bundle_id"]
    _validate_bundle_hashes(bundle, expected=active["files"], paths=paths)
    env = _stable_read(
        paths.env_file,
        label="active Palimpsest China environment",
        maximum=4096,
        uid=paths.root_uid,
        gid=paths.api_gid,
        modes=frozenset({0o640}),
    )
    dropin = _stable_read(
        paths.dropin_file,
        label="active Palimpsest China API drop-in",
        maximum=4096,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o644}),
    )
    expected_envs = {_render_env(bundle)}
    if active["schema"] == LEGACY_ACTIVE_MARKER_SCHEMA:
        # A hard stop after the migration's provisional environment commit but
        # before its atomic marker rename leaves this exact mixed state. It is
        # safe only as a one-way retry input for the same locked v1 marker.
        expected_envs.add(_render_legacy_env(bundle))
    if env not in expected_envs or dropin != _render_dropin(
        bundle, env_file=paths.env_file
    ):
        _fail("active marker and API configuration disagree")


def _migrate_legacy_active(
    paths: ActivationPaths,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Upgrade one exact v1 marker to provisional v2 under activation locks."""

    current = _read_active(paths)
    if current is None or current[0]["schema"] == ACTIVE_MARKER_SCHEMA:
        return current
    active, _receipt = current
    if active["schema"] != LEGACY_ACTIVE_MARKER_SCHEMA:
        _fail("unknown active marker cannot be migrated")
    legacy_body = _canonical(active)
    bundle = paths.state_root / active["bundle_id"]
    _validate_bundle_hashes(bundle, expected=active["files"], paths=paths)
    expected_dropin = _render_dropin(bundle, env_file=paths.env_file)
    current_env = _configured_body(
        paths.env_file,
        label="legacy active Palimpsest China environment",
        uid=paths.root_uid,
        gid=paths.api_gid,
        mode=0o640,
    )
    current_dropin = _configured_body(
        paths.dropin_file,
        label="legacy active Palimpsest China API drop-in",
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o644,
    )
    if current_env not in {_render_legacy_env(bundle), _render_env(bundle)}:
        _fail("legacy active marker and API environment disagree")
    if current_dropin != expected_dropin:
        _fail("legacy active marker and API drop-in disagree")
    pending = _read_pending(paths)
    if pending is not None and (
        pending["candidate_activation_id"] != active["activation_id"]
        or pending["candidate_bundle_id"] != active["bundle_id"]
        or pending["candidate_files"] != active["files"]
    ):
        _fail("legacy active migration found a different pending activation")
    migrated = {
        **active,
        "schema": ACTIVE_MARKER_SCHEMA,
        "publication_status": "provisional",
        "legacy_active_marker": legacy_body.decode("utf-8", "strict"),
        "legacy_active_marker_sha256": _digest(legacy_body),
    }
    _validate_active(migrated, paths=paths)
    try:
        _atomic_write(
            paths.env_file,
            _render_env(bundle),
            uid=paths.root_uid,
            gid=paths.api_gid,
            mode=0o640,
        )
        _atomic_write(
            paths.dropin_file,
            expected_dropin,
            uid=paths.root_uid,
            gid=paths.root_gid,
            mode=0o644,
        )
        _restart_and_probe(paths=paths, expected=_active_expected(current))
        _atomic_write(
            paths.active_marker,
            _canonical(migrated),
            uid=paths.root_uid,
            gid=paths.root_gid,
            mode=0o400,
        )
    except BaseException as migration_error:
        try:
            observed = _read_active(paths)
        except BaseException as audit_error:
            raise PalimpsestChinaActivationError(
                "legacy active migration left an unauditable marker"
            ) from audit_error
        if observed is not None and observed[0] == migrated:
            return observed
        if observed is not None and observed[0] == active:
            raise PalimpsestChinaActivationError(
                "legacy active marker remains provisional; retry its exact ID "
                "to complete the one-way v2 migration"
            ) from migration_error
        raise PalimpsestChinaActivationError(
            "legacy active marker changed unexpectedly during migration"
        ) from migration_error
    observed = _read_active(paths)
    if observed is None or observed[0] != migrated:
        _fail("migrated v2 active marker could not be read back exactly")
    return observed


def _configured_body(
    path: Path,
    *,
    label: str,
    uid: int,
    gid: int,
    mode: int,
) -> bytes | None:
    if not path.exists() and not path.is_symlink():
        return None
    return _stable_read(
        path,
        label=label,
        maximum=4096,
        uid=uid,
        gid=gid,
        modes=frozenset({mode}),
    )


def _recover_pending(paths: ActivationPaths) -> None:
    """Finish or roll back a transaction interrupted between atomic writes."""

    pending = _read_pending(paths)
    if pending is None:
        return
    candidate_bundle = paths.state_root / pending["candidate_bundle_id"]
    _validate_bundle_hashes(
        candidate_bundle,
        expected=pending["candidate_files"],
        paths=paths,
    )
    active = _read_active(paths)
    active_id = active[0]["activation_id"] if active is not None else None
    if active_id == pending["candidate_activation_id"]:
        if active is None or active[0]["files"] != pending["candidate_files"]:
            _fail("completed pending activation does not bind its candidate files")
        _assert_config_consistency(active, paths=paths)
        if active[0]["schema"] == LEGACY_ACTIVE_MARKER_SCHEMA:
            # The locked one-way migration below upgrades the marker and API
            # environment before this pending record can be cleared.
            return
        _restart_and_probe(paths=paths, expected=_active_expected(active))
        _remove_controlled_file(
            paths.pending_marker,
            label="completed pending activation marker",
            maximum=8192,
            uid=paths.root_uid,
            gid=paths.root_gid,
            mode=0o400,
        )
        return
    if active_id != pending["previous_activation_id"]:
        _fail("pending activation does not bind the current active marker")

    previous_bundle = (
        paths.state_root / active[0]["bundle_id"] if active is not None else None
    )
    candidate_env = _render_env(candidate_bundle)
    legacy_candidate_env = _render_legacy_env(candidate_bundle)
    candidate_dropin = _render_dropin(candidate_bundle, env_file=paths.env_file)
    previous_env = None
    if previous_bundle is not None:
        previous_env = (
            _render_legacy_env(previous_bundle)
            if active is not None and active[0]["schema"] == LEGACY_ACTIVE_MARKER_SCHEMA
            else _render_env(previous_bundle)
        )
    previous_dropin = (
        _render_dropin(previous_bundle, env_file=paths.env_file)
        if previous_bundle is not None
        else None
    )
    current_env = _configured_body(
        paths.env_file,
        label="interrupted activation environment",
        uid=paths.root_uid,
        gid=paths.api_gid,
        mode=0o640,
    )
    current_dropin = _configured_body(
        paths.dropin_file,
        label="interrupted activation drop-in",
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o644,
    )
    if current_env not in {
        None,
        candidate_env,
        legacy_candidate_env,
        previous_env,
    } or current_dropin not in {
        None,
        candidate_dropin,
        previous_dropin,
    }:
        _fail("interrupted activation configuration was modified unexpectedly")

    if previous_bundle is None:
        _remove_controlled_file(
            paths.env_file,
            label="interrupted activation environment",
            maximum=4096,
            uid=paths.root_uid,
            gid=paths.api_gid,
            mode=0o640,
        )
        _remove_controlled_file(
            paths.dropin_file,
            label="interrupted activation drop-in",
            maximum=4096,
            uid=paths.root_uid,
            gid=paths.root_gid,
            mode=0o644,
        )
    else:
        assert previous_env is not None and previous_dropin is not None
        _atomic_write(
            paths.env_file,
            previous_env,
            uid=paths.root_uid,
            gid=paths.api_gid,
            mode=0o640,
        )
        _atomic_write(
            paths.dropin_file,
            previous_dropin,
            uid=paths.root_uid,
            gid=paths.root_gid,
            mode=0o644,
        )
    _restart_and_probe(paths=paths, expected=_active_expected(active))
    _remove_controlled_file(
        paths.pending_marker,
        label="rolled-back pending activation marker",
        maximum=8192,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o400,
    )


def _receipt_floor(
    candidate: Mapping[str, Any],
    *,
    bundle_id: str,
    paths: ActivationPaths,
    active: tuple[dict[str, Any], dict[str, Any]] | None,
) -> datetime:
    """Reject a newly accepted bundle older than any retained activation."""

    try:
        entries = sorted(paths.receipts_dir.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            "activation receipts cannot be enumerated"
        ) from exc
    accepted_at = _timestamp(candidate["accepted_at"], label="candidate.accepted_at")
    clock_floor = accepted_at
    run_id = candidate["producer_workflow_run_id"]
    for entry in entries:
        if entry.name.startswith(".") or not entry.name.endswith(".json"):
            _fail("activation receipts directory contains an unexpected member")
        receipt, _body = _read_receipt(entry, paths=paths)
        clock_floor = max(
            clock_floor,
            _timestamp(receipt["recorded_at"], label="retained receipt.recorded_at"),
        )
        if receipt["bundle_id"] == bundle_id:
            continue
        if accepted_at <= _timestamp(
            receipt["accepted_at"], label="retained receipt.accepted_at"
        ):
            _fail("candidate acceptance clock would roll back retained authority")
        if run_id <= receipt["producer_workflow_run_id"]:
            _fail("candidate producer run id would roll back retained authority")
    if active is not None:
        clock_floor = max(
            clock_floor,
            _timestamp(active[0]["activated_at"], label="retained active.activated_at"),
        )
    return clock_floor


def _create_receipt(
    candidate: Mapping[str, Any],
    *,
    bundle_id: str,
    previous: tuple[dict[str, Any], dict[str, Any]] | None,
    runtime_proof: Mapping[str, Any],
    clock_floor: datetime,
    paths: ActivationPaths,
) -> tuple[dict[str, Any], bytes, Path]:
    previous_digest: str | None = None
    if previous is not None:
        previous_active, _previous_receipt = previous
        previous_path = Path(previous_active["receipt_path"])
        _loaded, previous_body = _read_receipt(previous_path, paths=paths)
        previous_digest = _digest(previous_body)

    activation_id = _activation_id(
        bundle_id=bundle_id,
        release_sha=paths.release_sha,
    )
    receipt_path = paths.receipts_dir / f"{activation_id}.json"
    expected = {
        "activation_id": activation_id,
        "bundle_id": bundle_id,
        "release_sha": paths.release_sha,
        "files": dict(candidate["files"]),
        "producer_repository": candidate["producer_repository"],
        "producer_sha": candidate["producer_sha"],
        "producer_workflow_run_id": candidate["producer_workflow_run_id"],
        "signer_key_id": candidate["signer_key_id"],
        "accepted_at": candidate["accepted_at"],
        "rights_expires_at": candidate["rights_expires_at"],
        "previous_receipt_sha256": previous_digest,
    }
    if receipt_path.exists() or receipt_path.is_symlink():
        receipt, body = _read_receipt(receipt_path, paths=paths)
        if any(receipt[key] != value for key, value in expected.items()):
            _fail("existing activation receipt differs from the candidate")
        return receipt, body, receipt_path

    verified_at = _timestamp(
        runtime_proof.get("verified_at"),
        label="runtime proof.verified_at",
    )
    recorded_at = _now_text_at_or_after(
        max(clock_floor, verified_at), label="retained activation receipt clock"
    )
    if verified_at > _timestamp(recorded_at, label="activation recorded_at"):
        _fail("runtime proof is later than its activation receipt")
    receipt = {
        "schema": ACTIVATION_RECEIPT_SCHEMA,
        **expected,
        "runtime_proof": dict(runtime_proof),
        "recorded_at": recorded_at,
    }
    _validate_receipt(receipt, expected_path=receipt_path)
    body = _canonical(receipt)
    _publish_immutable_atomic(
        receipt_path,
        body,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o400,
        portable=paths.portable,
    )
    return receipt, body, receipt_path


def _validate_activation_paths(paths: ActivationPaths) -> None:
    if _GIT_SHA_RE.fullmatch(paths.release_sha) is None:
        _fail("activation release SHA is invalid")
    if not paths.portable:
        fixed = (
            paths.state_root == PRODUCTION_STATE_ROOT
            and paths.env_file == PRODUCTION_ENV_FILE
            and paths.dropin_file == PRODUCTION_DROPIN_FILE
            and paths.deploy_lock == PRODUCTION_DEPLOY_LOCK
            and paths.activation_lock == PRODUCTION_ACTIVATION_LOCK
            and paths.resolved_durability_root == PRODUCTION_DURABILITY_ROOT
            and paths.runtime_release
            == PRODUCTION_RUNTIME_ROOT / "releases" / paths.release_sha
            and paths.python == PRODUCTION_PYTHON
            and paths.systemctl == PRODUCTION_SYSTEMCTL
            and paths.runuser == PRODUCTION_RUNUSER
            and paths.env_program == PRODUCTION_ENV
            and paths.api_url == PRODUCTION_API_URL
            and paths.api_user == PRODUCTION_API_USER
            and paths.root_uid == 0
            and paths.root_gid == 0
            and paths.attest_dir is None
        )
        if not fixed:
            _fail("production activation paths or identities changed")
        try:
            account = pwd.getpwnam(PRODUCTION_API_USER)
        except (KeyError, OSError) as exc:
            raise PalimpsestChinaActivationError(
                "production Seiche account is unavailable"
            ) from exc
        if (
            account.pw_uid <= 0
            or account.pw_gid <= 0
            or paths.api_uid != account.pw_uid
            or paths.api_gid != account.pw_gid
        ):
            _fail("production Seiche account identity changed")
    _validate_directory(
        paths.state_root,
        uid=paths.root_uid,
        gid=paths.api_gid,
        mode=0o750,
        label="Palimpsest China state root",
        validate_ancestry=not paths.portable,
    )
    _validate_directory(
        paths.receipts_dir,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o700,
        label="Palimpsest China receipts root",
    )
    durability_root_exists = (
        paths.resolved_durability_root.exists()
        or paths.resolved_durability_root.is_symlink()
    )
    if not paths.portable or durability_root_exists:
        _validate_directory(
            paths.resolved_durability_root,
            uid=paths.root_uid,
            gid=paths.root_gid,
            mode=0o700,
            label="Palimpsest China durability receipt root",
            validate_ancestry=not paths.portable,
        )
    _validate_directory(
        paths.env_file.parent,
        uid=paths.root_uid,
        gid=paths.api_gid,
        mode=0o750,
        label="Seiche environment root",
        validate_ancestry=not paths.portable,
    )
    _validate_directory(
        paths.dropin_file.parent,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o755,
        label="Seiche API drop-in root",
        validate_ancestry=not paths.portable,
    )
    _validate_directory(
        paths.deploy_lock.parent,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o700,
        label="Seiche deploy lock root",
        validate_ancestry=not paths.portable,
    )
    if paths.activation_lock.parent != paths.deploy_lock.parent:
        _fail("activation lock must share the protected deploy lock root")
    if not paths.api_url.startswith("http://127.0.0.1:") or paths.api_url.endswith("/"):
        _fail("activation API URL must be a fixed loopback origin")


def _runtime_command(*arguments: str, label: str) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            [
                str(PRODUCTION_GIT),
                "-c",
                f"safe.directory={PRODUCTION_REPOSITORY}",
                *arguments,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "HOME": "/root",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
            },
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PalimpsestChinaActivationError(f"{label} could not run") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[:1000]
        _fail(f"{label} failed: {detail}")
    return result


def _validate_runtime_release(paths: ActivationPaths) -> None:
    """Bind activation to the exact deployed, pinned-SSH-signed checkout."""

    if paths.portable:
        return
    for executable, label in (
        (paths.python, "activation Python runtime"),
        (paths.runuser, "runuser runtime"),
        (paths.env_program, "empty-environment runtime"),
        (paths.systemctl, "systemd control runtime"),
        (PRODUCTION_GIT, "Git runtime"),
        (PRODUCTION_SSH_KEYGEN, "SSH signature runtime"),
    ):
        if not executable.is_absolute() or not os.access(executable, os.X_OK):
            _fail(f"deployed {label} is unavailable")
        _validate_protected_path(
            executable,
            uid=paths.root_uid,
            label=f"deployed {label}",
        )
        try:
            resolved = executable.resolve(strict=True)
            metadata = resolved.stat()
        except OSError as exc:
            raise PalimpsestChinaActivationError(
                f"deployed {label} cannot be resolved"
            ) from exc
        _validate_protected_path(
            resolved,
            uid=paths.root_uid,
            label=f"resolved {label}",
        )
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != paths.root_uid
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or not stat.S_IMODE(metadata.st_mode) & 0o111
        ):
            _fail(f"deployed {label} metadata is unsafe")
    deployed = _stable_read(
        PRODUCTION_DEPLOYED_SHA,
        label="deployed release marker",
        maximum=64,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o600}),
    )
    if deployed != f"{paths.release_sha}\n".encode("ascii"):
        _fail("deployed release marker changed before activation")
    allowed_signers = _stable_read(
        PRODUCTION_ALLOWED_SIGNERS,
        label="release allowed-signers policy",
        maximum=4096,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o444}),
    )
    try:
        signer_line = allowed_signers.decode("ascii")
    except UnicodeDecodeError as exc:
        raise PalimpsestChinaActivationError(
            "release allowed-signers policy is not ASCII"
        ) from exc
    if (
        not signer_line.endswith("\n")
        or signer_line.count("\n") != 1
        or not signer_line.startswith(f"{PRODUCTION_RELEASE_PRINCIPAL} ssh-ed25519 ")
    ):
        _fail("release allowed-signers policy is not one pinned principal")
    head = _runtime_command(
        "-C",
        str(PRODUCTION_REPOSITORY),
        "rev-parse",
        "HEAD",
        label="deployed release resolution",
    ).stdout
    if head != f"{paths.release_sha}\n".encode("ascii"):
        _fail("application checkout does not match the deployed release marker")
    author = _runtime_command(
        "-C",
        str(PRODUCTION_REPOSITORY),
        "show",
        "-s",
        "--format=%ae",
        paths.release_sha,
        label="deployed release author verification",
    ).stdout
    if author != f"{PRODUCTION_RELEASE_PRINCIPAL}\n".encode("ascii"):
        _fail("deployed release author is not the pinned release principal")
    _runtime_command(
        "-C",
        str(PRODUCTION_REPOSITORY),
        "-c",
        "gpg.format=ssh",
        "-c",
        f"gpg.ssh.allowedSignersFile={PRODUCTION_ALLOWED_SIGNERS}",
        "-c",
        f"gpg.ssh.program={PRODUCTION_SSH_KEYGEN}",
        "verify-commit",
        paths.release_sha,
        label="deployed release signature verification",
    )
    _validate_directory(
        PRODUCTION_RUNTIME_ROOT,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o755,
        label="Palimpsest China trusted runtime root",
        validate_ancestry=True,
    )
    pointer = _stable_read(
        PRODUCTION_RUNTIME_ROOT / "current-sha",
        label="Palimpsest China trusted runtime pointer",
        maximum=64,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o444}),
    )
    if pointer != f"{paths.release_sha}\n".encode("ascii"):
        _fail("Palimpsest China trusted runtime pointer changed")
    releases = PRODUCTION_RUNTIME_ROOT / "releases"
    _validate_directory(
        releases,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o555,
        label="Palimpsest China trusted releases root",
    )
    _validate_directory(
        paths.runtime_release,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o555,
        label="Palimpsest China trusted release",
    )
    package = paths.runtime_release / "seiche"
    _validate_directory(
        package,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o555,
        label="Palimpsest China trusted package",
    )
    runtime_sources = {
        "__init__.py": "backend/seiche/__init__.py",
        "china_economic_focus.py": "backend/seiche/china_economic_focus.py",
        "nbs_trust.py": "backend/seiche/nbs_trust.py",
        "palimpsest_china_activation.py": (
            "backend/seiche/palimpsest_china_activation.py"
        ),
        "palimpsest_china_intake.py": "backend/seiche/palimpsest_china_intake.py",
    }
    try:
        package_names = {entry.name for entry in package.iterdir()}
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            "Palimpsest China trusted package cannot be listed"
        ) from exc
    if package_names != set(runtime_sources):
        _fail("Palimpsest China trusted package members changed")
    for name, repository_path in runtime_sources.items():
        installed = _stable_read(
            package / name,
            label=f"Palimpsest China trusted module {name}",
            maximum=2 * 1024 * 1024,
            uid=paths.root_uid,
            gid=paths.root_gid,
            modes=frozenset({0o444}),
            minimum=0 if name == "__init__.py" else 1,
        )
        committed = _runtime_command(
            "-C",
            str(PRODUCTION_REPOSITORY),
            "show",
            f"{paths.release_sha}:{repository_path}",
            label=f"Palimpsest China committed module {name}",
        ).stdout
        if installed != committed:
            _fail(f"trusted runtime module {name} differs from the signed release")


def activate_bundle(
    sources: BundleSources,
    *,
    paths: ActivationPaths,
    deploy_lock_descriptor: int | None = None,
) -> dict[str, Any]:
    """Install, verify, switch, prove, or roll back one exact bundle."""

    _validate_activation_paths(paths)

    with _deployment_lock(paths=paths, descriptor=deploy_lock_descriptor):
        _validate_runtime_release(paths)
        with _exclusive_lock(paths.activation_lock, paths=paths, create=True):
            _cleanup_activation_stages(paths)
            _recover_pending(paths)
            _migrate_legacy_active(paths)
            _recover_pending(paths)
            bodies, hashes = _read_source_bundle(sources.files(), paths=paths)
            identifier = _bundle_id(hashes)
            previous = _read_active(paths)
            _assert_config_consistency(previous, paths=paths)
            current_activation_id = _activation_id(
                bundle_id=identifier,
                release_sha=paths.release_sha,
            )
            same_live_bundle = (
                previous is not None
                and previous[0]["bundle_id"] == identifier
                and previous[0]["files"] == hashes
            )
            if (
                previous is not None
                and previous[0]["activation_id"] != current_activation_id
                and not same_live_bundle
                and activation_durability_status(paths)["status"] != "activated_durable"
            ):
                _fail(
                    "a different activation cannot replace the live provisional "
                    "activation; resume its exact ID first"
                )
            bundle = _publish_bundle(bodies, hashes, paths=paths)
            candidate = _verify_candidate(bundle, hashes=hashes, paths=paths)
            if bundle.name != identifier:
                _fail("bundle identity changed after verification")
            if same_live_bundle and previous is not None:
                previous_receipt = previous[1]
                for key in (
                    "files",
                    "producer_repository",
                    "producer_sha",
                    "producer_workflow_run_id",
                    "signer_key_id",
                    "accepted_at",
                    "rights_expires_at",
                ):
                    if candidate[key] != previous_receipt[key]:
                        _fail(
                            "same live bundle verification differs from its "
                            "immutable activation receipt"
                        )
            expected = {
                "files": dict(hashes),
                "signer_key_id": candidate["signer_key_id"],
            }
            clock_floor = _receipt_floor(
                candidate,
                bundle_id=identifier,
                paths=paths,
                active=previous,
            )
            if previous is not None and (
                previous[0]["activation_id"] == current_activation_id
                or same_live_bundle
            ):
                _restart_and_probe(
                    paths=paths,
                    expected=expected,
                    clock_floor=clock_floor,
                )
                durability = activation_durability_status(paths)
                return {
                    "schema": ACTIVE_MARKER_SCHEMA,
                    "status": (
                        "already_activated_durable"
                        if durability["status"] == "activated_durable"
                        else "already_active_provisional"
                    ),
                    "active": previous[0],
                    "receipt": previous[1],
                    "durability": durability,
                }

            snapshots = (
                _snapshot(
                    paths.env_file,
                    uid=paths.root_uid,
                    gid=paths.api_gid,
                    mode=0o640,
                    maximum=4096,
                ),
                _snapshot(
                    paths.dropin_file,
                    uid=paths.root_uid,
                    gid=paths.root_gid,
                    mode=0o644,
                    maximum=4096,
                ),
                _snapshot(
                    paths.active_marker,
                    uid=paths.root_uid,
                    gid=paths.root_gid,
                    mode=0o400,
                    maximum=MAX_ACTIVE_MARKER_BYTES,
                ),
            )
            pending = {
                "schema": PENDING_SCHEMA,
                "candidate_activation_id": current_activation_id,
                "candidate_bundle_id": identifier,
                "candidate_files": dict(hashes),
                "previous_activation_id": (
                    previous[0]["activation_id"] if previous is not None else None
                ),
                "started_at": _now_text_at_or_after(
                    clock_floor, label="retained activation clock"
                ),
            }
            _validate_pending(pending)
            _atomic_write(
                paths.pending_marker,
                _canonical(pending),
                uid=paths.root_uid,
                gid=paths.root_gid,
                mode=0o400,
            )
            changed = False
            marker_committed = False
            try:
                _atomic_write(
                    paths.env_file,
                    _render_env(bundle),
                    uid=paths.root_uid,
                    gid=paths.api_gid,
                    mode=0o640,
                )
                changed = True
                _atomic_write(
                    paths.dropin_file,
                    _render_dropin(bundle, env_file=paths.env_file),
                    uid=paths.root_uid,
                    gid=paths.root_gid,
                    mode=0o644,
                )
                runtime_proof = _restart_and_probe(
                    paths=paths,
                    expected=expected,
                    clock_floor=clock_floor,
                )
                if runtime_proof is None:
                    _fail("candidate activation did not produce a REST/MCP proof")
                receipt, receipt_body, receipt_path = _create_receipt(
                    candidate,
                    bundle_id=identifier,
                    previous=previous,
                    runtime_proof=runtime_proof,
                    clock_floor=clock_floor,
                    paths=paths,
                )
                activated_at = _now_text_at_or_after(
                    max(
                        clock_floor,
                        _timestamp(
                            receipt["recorded_at"],
                            label="activation receipt.recorded_at",
                        ),
                    ),
                    label="retained activation clock",
                )
                activated_at_value = _timestamp(
                    activated_at,
                    label="active marker.activated_at",
                )
                recorded_at_value = _timestamp(
                    receipt["recorded_at"],
                    label="activation receipt.recorded_at",
                )
                if activated_at_value < recorded_at_value:
                    _fail("system clock regressed before activation commit")
                if activated_at_value >= _timestamp(
                    receipt["rights_expires_at"],
                    label="activation receipt.rights_expires_at",
                ):
                    _fail("candidate rights expired before activation commit")
                marker = {
                    "schema": ACTIVE_MARKER_SCHEMA,
                    "activation_id": current_activation_id,
                    "bundle_id": identifier,
                    "release_sha": paths.release_sha,
                    "receipt_path": str(receipt_path),
                    "receipt_sha256": _digest(receipt_body),
                    "files": dict(hashes),
                    "activated_at": activated_at,
                    "publication_status": "provisional",
                    "legacy_active_marker": None,
                    "legacy_active_marker_sha256": None,
                }
                _validate_active(marker, paths=paths)
                _atomic_write(
                    paths.active_marker,
                    _canonical(marker),
                    uid=paths.root_uid,
                    gid=paths.root_gid,
                    mode=0o400,
                )
                marker_committed = True
                published = _read_active(paths)
                if (
                    published is None
                    or published[0] != marker
                    or published[1] != receipt
                ):
                    _fail("published active marker could not be read back exactly")
            except BaseException as activation_error:
                if marker_committed:
                    raise PalimpsestChinaActivationError(
                        "activation marker committed; live API remains provisional and "
                        f"must be resumed without rollback: {activation_error}"
                    ) from activation_error
                if not changed:
                    raise
                rollback_error: BaseException | None = None
                try:
                    for snapshot in snapshots:
                        _restore(snapshot)
                    _restart_and_probe(
                        paths=paths,
                        expected=_active_expected(previous),
                    )
                except BaseException as exc:
                    rollback_error = exc
                if rollback_error is not None:
                    raise PalimpsestChinaActivationError(
                        "activation failed and rollback could not be proven: "
                        f"activation={activation_error}; rollback={rollback_error}"
                    ) from rollback_error
                _remove_controlled_file(
                    paths.pending_marker,
                    label="rolled-back pending activation marker",
                    maximum=8192,
                    uid=paths.root_uid,
                    gid=paths.root_gid,
                    mode=0o400,
                )
                raise PalimpsestChinaActivationError(
                    f"activation failed; prior API configuration restored: {activation_error}"
                ) from activation_error

            try:
                _remove_controlled_file(
                    paths.pending_marker,
                    label="completed pending activation marker",
                    maximum=8192,
                    uid=paths.root_uid,
                    gid=paths.root_gid,
                    mode=0o400,
                )
            except PalimpsestChinaActivationError as exc:
                raise PalimpsestChinaActivationError(
                    "activation committed and proved, but pending-marker cleanup failed; "
                    "the active marker remains authoritative"
                ) from exc

            return {
                "schema": ACTIVE_MARKER_SCHEMA,
                "status": "activated_provisional",
                "active": marker,
                "receipt": receipt,
                "durability": activation_durability_status(paths),
            }


__all__ = [
    "ACTIVATION_RECEIPT_SCHEMA",
    "ACTIVE_MARKER_SCHEMA",
    "BACKUP_STATE_SCHEMA",
    "DURABILITY_RECEIPT_SCHEMA",
    "LEGACY_ACTIVE_MARKER_SCHEMA",
    "ActivationPaths",
    "BundleSources",
    "CANDIDATE_SCHEMA",
    "PENDING_SCHEMA",
    "PRODUCTION_ACTIVATION_LOCK",
    "PRODUCTION_API_URL",
    "PRODUCTION_DEPLOY_LOCK",
    "PRODUCTION_DURABILITY_ROOT",
    "PRODUCTION_DROPIN_FILE",
    "PRODUCTION_ENV_FILE",
    "PRODUCTION_PYTHON",
    "PRODUCTION_RUNTIME_ROOT",
    "PRODUCTION_STATE_ROOT",
    "PRODUCTION_TRANSACTION_LOCK",
    "RUNTIME_PROOF_SCHEMA",
    "PalimpsestChinaActivationError",
    "activate_bundle",
    "activation_durability_status",
    "audit_activation_state",
    "guarded_verify_main",
    "seal_activation_durability",
]
