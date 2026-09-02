#!/usr/bin/python3 -I
"""Activate one owner-accepted Palimpsest China bundle on the Seiche host."""

from __future__ import annotations

import errno
import fcntl
import grp
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys
import time
from typing import NoReturn

LAUNCHER = Path("/etc/seiche/libexec/seiche-palimpsest-china-activate.py")
DEPLOY_LOCK = Path("/run/seiche-deploy/deploy.lock")
TRANSACTION_LOCK = Path("/run/seiche-deploy/palimpsest-china-transaction.lock")
MARKET_LOCK = Path("/run/lock/seiche-market-backup.lock")
DEPLOYED_SHA = Path("/var/lib/seiche-deploy/deployed-sha")
RUNTIME_ROOT = Path("/opt/seiche-palimpsest-china")
LIVE_STATE_ROOT = Path("/var/lib/seiche-palimpsest-china")
DURABILITY_ROOT = Path("/var/lib/seiche-recovery-proof/palimpsest-china-durability")
DURABILITY_REQUEST = Path("/run/seiche-deploy/palimpsest-china-durability-request.json")
BACKUP_ROOT = Path("/var/backups/seiche-market")
RESTORE_STATUS = Path("/var/lib/seiche-recovery-proof/backup-restore-check.status")
OFFSITE_STATUS = Path("/var/lib/seiche-offsite-backup/status.json")
SYSTEMCTL = Path("/usr/bin/systemctl")
LOCK_TIMEOUT_SECONDS = 300.0
MARKET_LOCK_FD_ENV = "SEICHE_PALIMPSEST_CHINA_MARKET_LOCK_FD"
TRANSACTION_LOCK_FD_ENV = "SEICHE_PALIMPSEST_CHINA_TRANSACTION_LOCK_FD"
LOCKED_RUNNERS = {
    "backup": Path("/etc/seiche/libexec/seiche-market-backup.sh"),
    "restore": Path("/etc/seiche/libexec/seiche-market-restore-check.sh"),
}
DURABILITY_UNITS = (
    "seiche-market-backup.service",
    "seiche-market-restore-check.service",
    "seiche-market-offsite-backup.service",
)
_RESTORE_STATUS_KEYS = frozenset(
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
        "agent_room_restore_audit",
        "agent_room_audit_schema",
        "agent_room_server_key_id",
        "agent_room_participant_count",
        "agent_room_room_count",
        "agent_room_event_count",
        "agent_room_state_sha256",
        "agent_room_non_executable",
        "agent_room_execution_authority",
        "research_only",
        "can_publish",
        "can_execute",
    }
)


def _valid_agent_room_restore(fields: dict[str, str]) -> bool:
    count_names = (
        "agent_room_participant_count",
        "agent_room_room_count",
        "agent_room_event_count",
    )
    raw_counts = tuple(fields.get(name, "") for name in count_names)
    if any(
        len(value) > 7 or re.fullmatch(r"(?:0|[1-9][0-9]*)", value) is None
        for value in raw_counts
    ):
        return False
    counts = tuple(int(value) for value in raw_counts)
    participants, rooms, events = counts
    if (
        any(count > 2_000_000 for count in counts)
        or events > rooms * 4096
        or (rooms > 0 and participants == 0)
        or fields.get("agent_room_audit_schema") != "seiche.agent-room.restore-audit.v1"
        or fields.get("agent_room_non_executable") != "true"
        or fields.get("agent_room_execution_authority") != "none"
    ):
        return False
    result = fields.get("agent_room_restore_audit")
    server_key_id = fields.get("agent_room_server_key_id", "")
    state_sha256 = fields.get("agent_room_state_sha256", "")
    if result == "verified":
        return (
            re.fullmatch(r"[0-9a-f]{64}", server_key_id) is not None
            and re.fullmatch(r"[0-9a-f]{64}", state_sha256) is not None
        )
    return (
        result == "absent_uninitialized"
        and server_key_id == "none"
        and state_sha256 == "none"
        and counts == (0, 0, 0)
    )


_OFFSITE_STATUS_KEYS = frozenset(
    {
        "schema",
        "status",
        "mode",
        "observed_at",
        "attempt_id",
        "snapshot_id",
        "source_revision",
        "provider",
        "bucket",
        "prefix",
        "key_id",
        "destination",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "ciphertext_version_id",
        "ciphertext_etag",
        "checksum_version_id",
        "checksum_etag",
        "source_inventory_sha256",
        "source_content_set_sha256",
        "source_backup_schema",
        "nbs_state_root",
        "nbs_full_store_audit_contract",
        "nbs_full_store_audit_result",
        "palimpsest_china_state_root",
        "palimpsest_china_state_audit_contract",
        "palimpsest_china_state_tree_sha256",
        "palimpsest_china_state",
        "palimpsest_china_active_activation_id",
        "palimpsest_china_pending_candidate_activation_id",
        "object_lock",
        "remote_receipt_key",
        "remote_receipt_sha256",
        "remote_receipt_version_id",
        "remote_receipt_etag",
        "restore_verified",
        "failure_class",
        "last_success",
    }
)
_OFFSITE_SUCCESS_KEYS = _OFFSITE_STATUS_KEYS - {
    "schema",
    "status",
    "observed_at",
    "provider",
    "failure_class",
    "last_success",
} | {"verified_at"}
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_SOURCE_LABELS = (
    "manifest",
    "artifact",
    "input ledger",
    "availability receipt",
    "producer commit evidence",
    "producer main evidence",
    "handoff receipt",
    "checksum subject",
    "lineage chain",
    "lineage evidence",
    "acceptance receipt",
)
_RUNTIME_FILES = frozenset(
    {
        "__init__.py",
        "china_economic_focus.py",
        "nbs_trust.py",
        "palimpsest_china_activation.py",
        "palimpsest_china_intake.py",
    }
)


class LaunchError(RuntimeError):
    """The fixed privileged activation launcher rejected its environment."""


def _fail(message: str) -> NoReturn:
    raise LaunchError(message)


def _stable_read(
    path: Path,
    *,
    label: str,
    maximum: int,
    uid: int,
    gid: int,
    mode: int,
    minimum: int = 1,
) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
        visible = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != uid
            or before.st_gid != gid
            or stat.S_IMODE(before.st_mode) != mode
            or not minimum <= before.st_size <= maximum
            or (before.st_dev, before.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            _fail(f"{label} metadata is unsafe")
        body = bytearray()
        while len(body) <= maximum:
            chunk = os.read(descriptor, min(4096, maximum + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        after = os.fstat(descriptor)

        def identity(value: os.stat_result) -> tuple[int, ...]:
            return (
                value.st_dev,
                value.st_ino,
                value.st_mode,
                value.st_nlink,
                value.st_uid,
                value.st_gid,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )

        if (
            len(body) > maximum
            or len(body) != before.st_size
            or identity(before) != identity(after)
        ):
            _fail(f"{label} changed while being read")
        return bytes(body)
    except OSError as exc:
        raise LaunchError(f"{label} cannot be read safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _open_lock(
    path: Path,
    *,
    label: str,
    parent_mode: int,
    expected_uid: int = 0,
    expected_gid: int = 0,
    timeout_seconds: float = LOCK_TIMEOUT_SECONDS,
) -> int:
    descriptor = -1
    parent_descriptor = -1
    try:
        directory_flags = (
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        )
        parent_descriptor = os.open(path.parent, directory_flags)
        opened_parent = os.fstat(parent_descriptor)
        visible_parent = os.stat(path.parent, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened_parent.st_mode)
            or opened_parent.st_uid != expected_uid
            or opened_parent.st_gid != expected_gid
            or stat.S_IMODE(opened_parent.st_mode) != parent_mode
            or (opened_parent.st_dev, opened_parent.st_ino)
            != (visible_parent.st_dev, visible_parent.st_ino)
        ):
            _fail(f"{label} lock root metadata is unsafe")

        lock_flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        created = False
        try:
            descriptor = os.open(
                path.name,
                lock_flags,
                dir_fd=parent_descriptor,
            )
        except FileNotFoundError:
            try:
                descriptor = os.open(
                    path.name,
                    lock_flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent_descriptor,
                )
                created = True
            except FileExistsError:
                descriptor = os.open(
                    path.name,
                    lock_flags,
                    dir_fd=parent_descriptor,
                )
        if created:
            os.fchown(descriptor, expected_uid, expected_gid)
            os.fchmod(descriptor, 0o600)
        opened = os.fstat(descriptor)
        visible = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != expected_uid
            or opened.st_gid != expected_gid
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            _fail(f"{label} lock metadata is unsafe")
        os.fsync(descriptor)
        os.fsync(parent_descriptor)
        os.close(parent_descriptor)
        parent_descriptor = -1
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail(f"{label} lock remained busy")
            time.sleep(min(0.1, remaining))
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        raise LaunchError(f"{label} lock cannot be acquired safely") from exc
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        raise


def _open_deploy_lock(*, expected_uid: int = 0, expected_gid: int = 0) -> int:
    return _open_lock(
        DEPLOY_LOCK,
        label="deploy",
        parent_mode=0o700,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )


def _open_transaction_lock(
    *,
    expected_uid: int = 0,
    expected_gid: int = 0,
    timeout_seconds: float = LOCK_TIMEOUT_SECONDS,
) -> int:
    return _open_lock(
        TRANSACTION_LOCK,
        label="Palimpsest China transaction",
        parent_mode=0o700,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        timeout_seconds=timeout_seconds,
    )


def _open_market_lock(*, expected_uid: int = 0, expected_gid: int = 0) -> int:
    return _open_lock(
        MARKET_LOCK,
        label="market mutation",
        parent_mode=0o775,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )


def _validate_inherited_lock(
    descriptor: int,
    *,
    path: Path,
    label: str,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> None:
    """Prove that descriptor is the already-held exact controller lock."""

    contender = -1
    try:
        if descriptor < 3:
            _fail(f"inherited {label} lock descriptor is invalid")
        opened = os.fstat(descriptor)
        visible = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != expected_uid
            or opened.st_gid != expected_gid
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            _fail(f"inherited {label} lock metadata is unsafe")

        contender = os.open(
            path,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            fcntl.flock(contender, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
        else:
            fcntl.flock(contender, fcntl.LOCK_UN)
            _fail(f"inherited {label} lock is not already exclusive")

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                _fail(f"inherited descriptor does not own the {label} lock")
            raise
        after = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
        if (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino):
            _fail(f"inherited {label} lock changed identity")
    except OSError as exc:
        raise LaunchError(f"inherited {label} lock cannot be validated safely") from exc
    finally:
        if contender >= 0:
            os.close(contender)


def _validate_inherited_market_lock(
    descriptor: int,
    *,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> None:
    _validate_inherited_lock(
        descriptor,
        path=MARKET_LOCK,
        label="market mutation",
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )


def _inherited_market_lock() -> int:
    value = os.environ.get(MARKET_LOCK_FD_ENV, "")
    if re.fullmatch(r"[3-9]|[1-9][0-9]{1,2}", value) is None:
        _fail("market-locked audit descriptor is missing or malformed")
    descriptor = int(value)
    _validate_inherited_market_lock(descriptor)
    return descriptor


def _inherited_transaction_lock(*, expected_uid: int = 0, expected_gid: int = 0) -> int:
    value = os.environ.get(TRANSACTION_LOCK_FD_ENV, "")
    if re.fullmatch(r"[3-9]|[1-9][0-9]{1,2}", value) is None:
        _fail("activation transaction descriptor is missing or malformed")
    descriptor = int(value)
    _validate_inherited_lock(
        descriptor,
        path=TRANSACTION_LOCK,
        label="Palimpsest China transaction",
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    return descriptor


def _validate_locked_runner(path: Path) -> None:
    _stable_read(
        path,
        label="market-locked runner",
        maximum=512 * 1024,
        uid=0,
        gid=0,
        mode=0o755,
    )


def _run_market_locked(name: str) -> NoReturn:
    runner = LOCKED_RUNNERS[name]
    _validate_locked_runner(runner)
    descriptor = _open_market_lock()
    try:
        os.set_inheritable(descriptor, True)
        environment = dict(os.environ)
        environment[MARKET_LOCK_FD_ENV] = str(descriptor)
        os.execve(
            "/usr/bin/bash",
            ["/usr/bin/bash", str(runner)],
            environment,
        )
    except BaseException:
        os.close(descriptor)
        raise


def _release_sha() -> str:
    body = _stable_read(
        DEPLOYED_SHA,
        label="deployed release marker",
        maximum=64,
        uid=0,
        gid=0,
        mode=0o600,
    )
    try:
        value = body.decode("ascii")
    except UnicodeDecodeError as exc:
        raise LaunchError("deployed release marker is not ASCII") from exc
    if not value.endswith("\n") or _SHA_RE.fullmatch(value[:-1]) is None:
        _fail("deployed release marker is malformed")
    return value[:-1]


def _identity() -> tuple[int, int]:
    try:
        account = pwd.getpwnam("seiche")
        group = grp.getgrnam("seiche")
    except (KeyError, OSError) as exc:
        raise LaunchError("the production seiche identity is unavailable") from exc
    if account.pw_uid <= 0 or group.gr_gid <= 0 or account.pw_gid != group.gr_gid:
        _fail("the production seiche identity is inconsistent")
    return account.pw_uid, group.gr_gid


def _validate_launcher() -> None:
    if Path(os.path.abspath(__file__)) != LAUNCHER:
        _fail("activation launcher must run from its fixed installed path")
    _stable_read(
        LAUNCHER,
        label="activation launcher",
        maximum=512 * 1024,
        uid=0,
        gid=0,
        mode=0o500,
    )


def _directory(
    path: Path, *, mode: int, entries: set[str] | frozenset[str] | None = None
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise LaunchError(f"trusted runtime directory is unavailable: {path}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        _fail(f"trusted runtime directory metadata is unsafe: {path}")
    if entries is not None:
        try:
            actual = {entry.name for entry in path.iterdir()}
        except OSError as exc:
            raise LaunchError(
                f"trusted runtime directory cannot be listed: {path}"
            ) from exc
        if actual != set(entries):
            _fail(f"trusted runtime directory members changed: {path}")


def _validate_runtime(release_sha: str) -> Path:
    try:
        anchor = RUNTIME_ROOT.parent.lstat()
    except OSError as exc:
        raise LaunchError("trusted runtime anchor is unavailable") from exc
    if (
        not stat.S_ISDIR(anchor.st_mode)
        or anchor.st_uid != 0
        or stat.S_IMODE(anchor.st_mode) & 0o022
    ):
        _fail("trusted runtime anchor is unsafe")
    _directory(RUNTIME_ROOT, mode=0o755, entries={"current-sha", "releases"})
    pointer = _stable_read(
        RUNTIME_ROOT / "current-sha",
        label="trusted runtime pointer",
        maximum=64,
        uid=0,
        gid=0,
        mode=0o444,
    )
    if pointer != f"{release_sha}\n".encode("ascii"):
        _fail("trusted runtime pointer does not match the deployed release")
    releases = RUNTIME_ROOT / "releases"
    _directory(releases, mode=0o555)
    runtime = releases / release_sha
    _directory(runtime, mode=0o555, entries={"seiche"})
    package = runtime / "seiche"
    _directory(package, mode=0o555, entries=_RUNTIME_FILES)
    for name in _RUNTIME_FILES:
        _stable_read(
            package / name,
            label=f"trusted runtime module {name}",
            maximum=2 * 1024 * 1024,
            uid=0,
            gid=0,
            mode=0o444,
            minimum=0 if name == "__init__.py" else 1,
        )
    return runtime


def _audit_target(path: Path, *, normalize_restored: bool) -> None:
    if (
        not path.is_absolute()
        or path == Path("/")
        or Path(os.path.normpath(path)) != path
        or path.name != LIVE_STATE_ROOT.name
    ):
        _fail("activation-state audit path is invalid")
    if not normalize_restored:
        if path != LIVE_STATE_ROOT:
            _fail("live activation-state audit path changed")
        return
    backup_parent = Path("/var/backups/seiche-market")
    recovery_parent = Path("/var/lib/seiche-recovery-proof")
    allowed = (
        path.parent.name == "palimpsest-verify"
        and path.parent.parent.name.startswith(".stage-")
        and path.parent.parent.parent == backup_parent
    ) or (
        path.parent.name.startswith(".backup-palimpsest-restore.")
        and path.parent.parent == recovery_parent
    )
    if not allowed:
        _fail("restored activation-state audit path is outside a fixed scratch root")
    try:
        parent = path.parent.lstat()
    except OSError as exc:
        raise LaunchError(
            "restored activation-state audit parent is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        _fail("restored activation-state audit parent is unsafe")


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _strict_json(body: bytes, *, label: str) -> dict[str, object]:
    try:
        text = body.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise LaunchError(f"{label} is not strict UTF-8") from exc

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                _fail(f"{label} contains a duplicate field")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=unique)
    except (json.JSONDecodeError, ValueError) as exc:
        raise LaunchError(f"{label} is not strict JSON") from exc
    if type(value) is not dict or body != _canonical(value):
        _fail(f"{label} is not canonical JSON")
    return value


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _require_sha(value: object, *, label: str, length: int = 64) -> str:
    if type(value) is not str or re.fullmatch(f"[0-9a-f]{{{length}}}", value) is None:
        _fail(f"{label} is malformed")
    return value


def _status_fields(body: bytes, *, label: str) -> dict[str, str]:
    try:
        lines = body.decode("ascii", "strict").splitlines()
    except UnicodeDecodeError as exc:
        raise LaunchError(f"{label} is not ASCII") from exc
    if not body.endswith(b"\n") or not lines:
        _fail(f"{label} is empty or unterminated")
    fields: dict[str, str] = {}
    for line in lines:
        if line.count("=") != 1:
            _fail(f"{label} has an invalid field")
        key, value = line.split("=", 1)
        if not key or key in fields:
            _fail(f"{label} has duplicate or empty fields")
        fields[key] = value
    return fields


def _safe_fixed_directory(path: Path, *, mode: int, label: str) -> None:
    try:
        opened = path.lstat()
    except OSError as exc:
        raise LaunchError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(opened.st_mode)
        or opened.st_uid != 0
        or opened.st_gid != 0
        or stat.S_IMODE(opened.st_mode) != mode
    ):
        _fail(f"{label} metadata is unsafe")


def _remove_durability_request() -> None:
    if not DURABILITY_REQUEST.exists() and not DURABILITY_REQUEST.is_symlink():
        return
    _stable_read(
        DURABILITY_REQUEST,
        label="activation durability request",
        maximum=4096,
        uid=0,
        gid=0,
        mode=0o400,
    )
    DURABILITY_REQUEST.unlink()
    descriptor = os.open(
        DURABILITY_REQUEST.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_durability_request(document: dict[str, object]) -> None:
    _safe_fixed_directory(
        DURABILITY_REQUEST.parent,
        mode=0o700,
        label="activation durability request root",
    )
    _remove_durability_request()
    body = _canonical(document)
    temporary = DURABILITY_REQUEST.parent / (
        f".palimpsest-china-durability-request.{secrets.token_hex(12)}"
    )
    descriptor = -1
    parent_descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            0o400,
        )
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o400)
        written = 0
        while written < len(body):
            written += os.write(descriptor, body[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, DURABILITY_REQUEST)
        parent_descriptor = os.open(
            DURABILITY_REQUEST.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        os.fsync(parent_descriptor)
    except OSError as exc:
        raise LaunchError("activation durability request cannot be published") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
    observed = _stable_read(
        DURABILITY_REQUEST,
        label="activation durability request",
        maximum=4096,
        uid=0,
        gid=0,
        mode=0o400,
    )
    if observed != body:
        _fail("activation durability request changed during publication")


def _snapshot_id() -> str:
    _safe_fixed_directory(BACKUP_ROOT, mode=0o700, label="market backup root")
    candidate = datetime.now(UTC).replace(microsecond=0)
    for _index in range(121):
        name = candidate.strftime("%Y%m%dT%H%M%SZ")
        path = BACKUP_ROOT / name
        if not path.exists() and not path.is_symlink():
            return name
        candidate += timedelta(seconds=1)
    _fail("could not allocate a unique activation durability snapshot")


def _start_unit(unit: str) -> None:
    if unit not in DURABILITY_UNITS:
        _fail("activation durability unit is outside the fixed allowlist")
    try:
        completed = subprocess.run(
            [str(SYSTEMCTL), "start", unit],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"HOME": "/root", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            timeout=1800,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LaunchError(f"activation durability unit could not run: {unit}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()[:1000]
        _fail(f"activation durability unit failed: {unit}: {detail}")


def _read_snapshot_audit(snapshot: str) -> tuple[dict[str, object], str]:
    snapshot_root = BACKUP_ROOT / snapshot
    _safe_fixed_directory(snapshot_root, mode=0o700, label="activation snapshot")
    audit_body = _stable_read(
        snapshot_root / "palimpsest-china-state.json",
        label="activation snapshot state audit",
        maximum=512 * 1024,
        uid=0,
        gid=0,
        mode=0o600,
    )
    audit = _strict_json(audit_body, label="activation snapshot state audit")
    inventory = _stable_read(
        snapshot_root / "SHA256SUMS",
        label="activation snapshot inventory",
        maximum=4096,
        uid=0,
        gid=0,
        mode=0o600,
    )
    return audit, _sha256(inventory)


def _run_activation_durability(activation: object, paths: object) -> dict[str, object]:
    status = activation.activation_durability_status(paths)
    if status["status"] == "activated_durable":
        # A hard stop can occur after the immutable seal but before the normal
        # request cleanup. The exact durable overlay makes that request stale;
        # remove it before returning so ordinary backup jobs cannot inherit it.
        _remove_durability_request()
        return status
    if status["status"] != "provisional":
        _fail("activation durability requires an exact live provisional activation")
    activation_id = _require_sha(
        status.get("activation_id"), label="live activation ID"
    )
    tree_sha = _require_sha(status.get("tree_sha256"), label="live activation tree")
    release_sha = _release_sha()
    initial_audit = activation.audit_activation_state(
        LIVE_STATE_ROOT,
        root_uid=paths.root_uid,
        root_gid=paths.root_gid,
        api_uid=paths.api_uid,
        api_gid=paths.api_gid,
        declared_state_root=LIVE_STATE_ROOT,
    )
    if (
        initial_audit.get("active_activation_id") != activation_id
        or initial_audit.get("tree_sha256") != tree_sha
        or initial_audit.get("pending_candidate_activation_id") is not None
    ):
        _fail("live provisional activation audit changed before durability")
    snapshot = _snapshot_id()
    request = {
        "schema": "seiche.palimpsest-china-durability-request.v1",
        "activation_id": activation_id,
        "tree_sha256": tree_sha,
        "release_sha": release_sha,
        "snapshot_id": snapshot,
        "requested_at": datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    _write_durability_request(request)
    try:
        _start_unit(DURABILITY_UNITS[0])
        snapshot_audit, inventory_sha = _read_snapshot_audit(snapshot)
        if snapshot_audit != initial_audit:
            _fail("activation snapshot does not equal the exact live activation tree")

        _start_unit(DURABILITY_UNITS[1])
        restore_body = _stable_read(
            RESTORE_STATUS,
            label="activation restore receipt",
            maximum=16 * 1024,
            uid=0,
            gid=paths.api_gid,
            mode=0o640,
        )
        restore = _status_fields(restore_body, label="activation restore receipt")
        if (
            set(restore) != _RESTORE_STATUS_KEYS
            or restore.get("schema") != "seiche.market-backup-restore-check.v6"
            or restore.get("snapshot") != snapshot
            or restore.get("source_backup_schema") != "seiche.market-backup.v4"
            or restore.get("deployed_sha") != release_sha
            or restore.get("palimpsest_china_state_archive_restore") != "verified"
            or restore.get("palimpsest_china_state_tree_sha256") != tree_sha
            or restore.get("palimpsest_china_active_activation_id") != activation_id
            or restore.get("palimpsest_china_pending_candidate_activation_id") != "none"
            or restore.get("database_restore") != "pass"
            or restore.get("state_archive_restore") != "pass"
            or restore.get("api_data_archive_restore") != "pass"
            or restore.get("research_only") != "true"
            or restore.get("can_publish") != "false"
            or restore.get("can_execute") != "false"
            or restore.get("nbs_full_store_audit_contract")
            != "seiche.nbs-full-store-audit.v1"
            or restore.get("nbs_full_store_audit_result")
            != restore.get("nbs_public_revision_store")
            or restore.get("nbs_full_store_audit_result")
            not in {"not_onboarded", "verified_head"}
            or restore.get("palimpsest_china_state_audit_contract")
            != "seiche.palimpsest-china-activation-state.v1"
            or re.fullmatch(r"[0-9]+", restore.get("palimpsest_china_bundle_count", ""))
            is None
            or re.fullmatch(
                r"[0-9]+", restore.get("palimpsest_china_receipt_count", "")
            )
            is None
            or re.fullmatch(
                r"[0-9]+\|[0-9]+\|[0-9]+\|[0-9]+",
                restore.get("critical_table_counts", ""),
            )
            is None
            or re.fullmatch(
                r"[0-9]+\|[0-9]+\|[0-9]+\|[0-9]+",
                restore.get("critical_table_count_floor", ""),
            )
            is None
            or not _valid_agent_room_restore(restore)
        ):
            _fail("local restore receipt does not prove the exact live activation")

        _start_unit(DURABILITY_UNITS[2])
        offsite_body = _stable_read(
            OFFSITE_STATUS,
            label="activation offsite status",
            maximum=128 * 1024,
            uid=0,
            gid=0,
            mode=0o600,
        )
        offsite = _strict_json(offsite_body, label="activation offsite status")
        success = offsite.get("last_success")
        if type(success) is not dict:
            _fail("activation offsite status has no completed proof")
        shared_fields = _OFFSITE_SUCCESS_KEYS - {"verified_at"}
        if (
            set(offsite) != _OFFSITE_STATUS_KEYS
            or set(success) != _OFFSITE_SUCCESS_KEYS
            or any(offsite.get(key) != success.get(key) for key in shared_fields)
            or offsite.get("schema") != "seiche.market-offsite-backup-status.v4"
            or offsite.get("status") != "success"
            or offsite.get("mode") != "scheduled"
            or offsite.get("provider") != "hetzner-object-storage"
            or offsite.get("failure_class") is not None
            or success.get("snapshot_id") != snapshot
            or success.get("source_revision") != release_sha
            or success.get("source_backup_schema") != "seiche.market-backup.v4"
            or success.get("palimpsest_china_active_activation_id") != activation_id
            or success.get("palimpsest_china_pending_candidate_activation_id")
            is not None
            or success.get("palimpsest_china_state_tree_sha256") != tree_sha
            or success.get("source_inventory_sha256") != inventory_sha
            or success.get("nbs_full_store_audit_contract")
            != "seiche.nbs-full-store-audit.v1"
            or success.get("nbs_full_store_audit_result") != "required_at_restore"
            or success.get("palimpsest_china_state_audit_contract")
            != "seiche.palimpsest-china-activation-state.v1"
            or success.get("palimpsest_china_state") != "active"
            or success.get("object_lock") != {"days": 90, "mode": "COMPLIANCE"}
            or success.get("restore_verified") is not True
        ):
            _fail("offsite receipt does not prove the exact live activation")
        attempt_id = success.get("attempt_id")
        remote_key = success.get("remote_receipt_key")
        remote_sha = _require_sha(
            success.get("remote_receipt_sha256"), label="remote receipt digest"
        )
        if (
            type(attempt_id) is not str
            or re.fullmatch(r"20[0-9]{6}T[0-9]{6}Z-[0-9]+", attempt_id) is None
            or type(remote_key) is not str
            or remote_key
            != f"{offsite.get('prefix')}/snapshots/{snapshot}/attempts/{attempt_id}/RECEIPT.json"
        ):
            _fail("offsite scheduled receipt identity changed")

        final_audit = activation.audit_activation_state(
            LIVE_STATE_ROOT,
            root_uid=paths.root_uid,
            root_gid=paths.root_gid,
            api_uid=paths.api_uid,
            api_gid=paths.api_gid,
            declared_state_root=LIVE_STATE_ROOT,
        )
        if final_audit != initial_audit:
            _fail("live activation tree changed during durability proof")
        evidence = {
            "local_backup_snapshot": snapshot,
            "local_backup_inventory_sha256": inventory_sha,
            "local_restore_schema": restore["schema"],
            "local_restore_activation_id": activation_id,
            "local_restore_tree_sha256": tree_sha,
            "local_restore_checked_at": restore.get("checked_at"),
            "local_restore_receipt": restore_body.decode("ascii", "strict"),
            "local_restore_receipt_sha256": _sha256(restore_body),
            "offsite_status_schema": offsite["schema"],
            "offsite_snapshot": snapshot,
            "offsite_activation_id": activation_id,
            "offsite_tree_sha256": tree_sha,
            "offsite_attempt_id": attempt_id,
            "offsite_remote_receipt_key": remote_key,
            "offsite_remote_receipt_sha256": remote_sha,
            "offsite_verified_at": success.get("verified_at"),
        }
        receipt, receipt_body, receipt_path = activation.seal_activation_durability(
            paths, evidence
        )
        final_status = activation.activation_durability_status(paths)
        if (
            final_status.get("status") != "activated_durable"
            or final_status.get("activation_id") != activation_id
            or final_status.get("tree_sha256") != tree_sha
            or final_status.get("durability_receipt_path") != str(receipt_path)
            or final_status.get("durability_receipt_sha256") != _sha256(receipt_body)
            or receipt.get("activation_id") != activation_id
        ):
            _fail("final live durability audit did not bind the sealed receipt")
        return final_status
    except Exception as exc:
        raise LaunchError(
            "live activation remains provisional; exact durability proof did not complete: "
            f"{exc}"
        ) from exc
    finally:
        _remove_durability_request()


def run(arguments: list[str]) -> int:
    if os.geteuid() != 0:
        _fail("activation launcher must run as root")
    locked_runner_mode = (
        len(arguments) == 2
        and arguments[0] == "--run-market-locked"
        and arguments[1] in LOCKED_RUNNERS
    )
    audit_mode = len(arguments) == 3 and arguments[0] == "--audit-state"
    durability_status_mode = arguments == ["--durability-status"]
    if (
        not locked_runner_mode
        and not audit_mode
        and not durability_status_mode
        and len(arguments) != len(_SOURCE_LABELS)
    ):
        labels = " ".join(f"<{label.replace(' ', '-')}>" for label in _SOURCE_LABELS)
        _fail(
            f"usage: {LAUNCHER} {labels}; or "
            f"{LAUNCHER} --durability-status; or "
            f"{LAUNCHER} --audit-state <state-root> <normalize-restored-0-or-1>; "
            f"or {LAUNCHER} --run-market-locked <backup-or-restore>"
        )
    _validate_launcher()
    if locked_runner_mode:
        _run_market_locked(arguments[1])
    sources: list[Path] = []
    audit_path: Path | None = None
    normalize_restored = False
    if audit_mode:
        audit_path = Path(arguments[1])
        if arguments[2] not in {"0", "1"}:
            _fail("normalize-restored flag must be exactly 0 or 1")
        normalize_restored = arguments[2] == "1"
        _audit_target(audit_path, normalize_restored=normalize_restored)
    elif not durability_status_mode:
        sources = [Path(value) for value in arguments]
        if any(
            not path.is_absolute()
            or path == Path("/")
            or Path(os.path.normpath(path)) != path
            for path in sources
        ):
            _fail("every handoff source path must be absolute and canonical")

    transaction_lock = -1
    deploy_lock = -1
    market_lock = -1
    if audit_mode:
        market_lock = _inherited_market_lock()
    elif durability_status_mode:
        transaction_lock = (
            _inherited_transaction_lock()
            if os.environ.get(TRANSACTION_LOCK_FD_ENV)
            else _open_transaction_lock(timeout_seconds=0)
        )
    else:
        # The exact activation transaction encloses provisional publication,
        # local backup/restore, offsite verification, and final sealing. Inner
        # deploy and market locks are released before the backup services run.
        transaction_lock = _open_transaction_lock()
        try:
            deploy_lock = _open_deploy_lock()
            try:
                market_lock = _open_market_lock()
            except BaseException:
                os.close(deploy_lock)
                deploy_lock = -1
                raise
        except BaseException:
            os.close(transaction_lock)
            transaction_lock = -1
            raise
    try:
        release_sha = _release_sha()
        runtime = _validate_runtime(release_sha)
        if any(path and str(path).startswith("/home/") for path in sys.path):
            _fail("activation launcher inherited an application import path")
        sys.path.insert(0, str(runtime))
        try:
            from seiche import palimpsest_china_activation as activation
        except (ImportError, OSError) as exc:
            raise LaunchError("trusted activation runtime cannot be imported") from exc
        expected_module = runtime / "seiche" / "palimpsest_china_activation.py"
        if Path(activation.__file__) != expected_module:
            _fail("activation module resolved outside the trusted runtime")
        if activation.PRODUCTION_STATE_ROOT != LIVE_STATE_ROOT:
            _fail("trusted activation runtime state root changed")
        api_uid, api_gid = _identity()
        if audit_path is not None:
            result = activation.audit_activation_state(
                audit_path,
                root_uid=0,
                root_gid=0,
                api_uid=api_uid,
                api_gid=api_gid,
                normalize_restored=normalize_restored,
                declared_state_root=LIVE_STATE_ROOT,
            )
            print(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0
        paths = activation.ActivationPaths(
            state_root=activation.PRODUCTION_STATE_ROOT,
            env_file=activation.PRODUCTION_ENV_FILE,
            dropin_file=activation.PRODUCTION_DROPIN_FILE,
            deploy_lock=activation.PRODUCTION_DEPLOY_LOCK,
            activation_lock=activation.PRODUCTION_ACTIVATION_LOCK,
            runtime_release=runtime,
            release_sha=release_sha,
            root_uid=0,
            root_gid=0,
            api_uid=api_uid,
            api_gid=api_gid,
            durability_root=DURABILITY_ROOT,
        )
        if durability_status_mode:
            print(
                json.dumps(
                    activation.activation_durability_status(paths),
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0
        result = activation.activate_bundle(
            activation.BundleSources(*sources),
            paths=paths,
            deploy_lock_descriptor=deploy_lock,
        )
        # Activation's inner lock is already gone. Release the remaining inner
        # controller locks before systemd backup/restore/offsite units acquire
        # their own market leases, while retaining the outer transaction lock.
        os.close(market_lock)
        market_lock = -1
        os.close(deploy_lock)
        deploy_lock = -1
        durability = _run_activation_durability(activation, paths)
        result["durability"] = durability
        result["status"] = (
            "already_activated_durable"
            if result.get("status") == "already_activated_durable"
            else "activated_durable"
        )
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    finally:
        if not audit_mode and market_lock >= 0:
            os.close(market_lock)
        if deploy_lock >= 0:
            os.close(deploy_lock)
        if transaction_lock >= 0:
            os.close(transaction_lock)


def main() -> int:
    os.umask(0o077)
    try:
        return run(sys.argv[1:])
    except LaunchError as exc:
        print(f"Palimpsest China activation refused: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Palimpsest China activation failed closed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
