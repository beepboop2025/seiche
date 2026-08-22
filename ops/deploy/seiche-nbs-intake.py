#!/usr/bin/python3 -I
"""Run one production NBS intake behind storage and backup serialization."""

from __future__ import annotations

import argparse
import errno
import fcntl
import grp
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from typing import NoReturn


PRODUCTION_NBS_ROOT = Path("/var/lib/seiche-nbs")
BACKUP_LOCK = Path("/run/lock/seiche-market-backup.lock")
STORAGE_PREFLIGHT = Path("/etc/seiche/libexec/seiche-storage-preflight.py")
STORAGE_CONFIG = Path("/etc/seiche/storage-volume.env")
LAUNCHER_PATH = Path(os.path.abspath(__file__))
TRUSTED_RUNTIME_ANCHOR = Path("/opt")
TRUSTED_RUNTIME_ROOT = Path("/opt/seiche-nbs-intake")
TRUSTED_RUNTIME_POINTER = TRUSTED_RUNTIME_ROOT / "current-sha"
TRUSTED_RUNTIME_RELEASES = TRUSTED_RUNTIME_ROOT / "releases"
SYSTEM_PYTHON = Path("/usr/bin/python3")
STATE_PATH = Path("/var/lib/seiche")
BACKUP_PATH = Path("/var/backups/seiche-market")
LOCK_TIMEOUT_SECONDS = 300.0
LOCK_POLL_SECONDS = 0.1
PREFLIGHT_TIMEOUT_SECONDS = 60.0
EXPECTED_ROOT_UID = 0
EXPECTED_ROOT_GID = 0
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_RUNTIME_PACKAGE_FILES = frozenset({"__init__.py", "nbs_intake.py", "nbs_trust.py"})

GUARD_ROOT_FD_ENV = "SEICHE_NBS_INTAKE_GUARD_ROOT_FD"
GUARD_TOKEN_FD_ENV = "SEICHE_NBS_INTAKE_GUARD_TOKEN_FD"
GUARD_TOKEN_ENV = "SEICHE_NBS_INTAKE_GUARD_TOKEN"


class IntakeLaunchError(RuntimeError):
    """The production operator boundary could not be proven."""


def _fail(message: str) -> NoReturn:
    raise IntakeLaunchError(message)


def _open_directory_nofollow(path: Path, *, kind: str) -> int:
    if (
        not path.is_absolute()
        or path == Path("/")
        or Path(os.path.normpath(path)) != path
    ):
        _fail(f"{kind} path is not canonical")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(path.anchor, flags)
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            visible = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            opened = os.fstat(child)
            if not stat.S_ISDIR(visible.st_mode) or (
                visible.st_dev,
                visible.st_ino,
            ) != (opened.st_dev, opened.st_ino):
                os.close(child)
                _fail(f"{kind} has an unsafe path component")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise IntakeLaunchError(f"{kind} cannot be opened safely") from exc
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _validate_visible_descriptor(
    path: Path,
    descriptor: int,
    *,
    kind: str,
    required_uid: int | None = None,
    required_gid: int | None = None,
    required_mode: int | None = None,
) -> os.stat_result:
    try:
        opened = os.fstat(descriptor)
        visible = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise IntakeLaunchError(f"{kind} cannot be validated") from exc
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(visible.st_mode)
        or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        or (required_uid is not None and opened.st_uid != required_uid)
        or (required_gid is not None and opened.st_gid != required_gid)
        or (required_mode is not None and stat.S_IMODE(opened.st_mode) != required_mode)
    ):
        _fail(f"{kind} metadata or path identity is unsafe")
    return opened


def _validate_lock(lock_parent: int, lock_descriptor: int) -> None:
    try:
        opened = os.fstat(lock_descriptor)
        visible = os.stat(
            BACKUP_LOCK.name,
            dir_fd=lock_parent,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise IntakeLaunchError("backup lock cannot be validated") from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or opened.st_uid != EXPECTED_ROOT_UID
        or opened.st_gid != EXPECTED_ROOT_GID
        or stat.S_IMODE(opened.st_mode) != 0o600
        or (visible.st_dev, visible.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        _fail("backup lock must be root:root 0600, regular, and single-link")


def _open_backup_lock() -> tuple[int, int]:
    lock_parent = _open_directory_nofollow(
        BACKUP_LOCK.parent, kind="backup lock parent"
    )
    parent_metadata = os.fstat(lock_parent)
    if (
        parent_metadata.st_uid != EXPECTED_ROOT_UID
        or stat.S_IMODE(parent_metadata.st_mode) & 0o002
        or (
            stat.S_IMODE(parent_metadata.st_mode) & 0o020
            and parent_metadata.st_gid != EXPECTED_ROOT_GID
        )
    ):
        os.close(lock_parent)
        _fail("backup lock parent is not root-owned, root-grouped, and protected")
    flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    lock_descriptor = -1
    try:
        try:
            lock_descriptor = os.open(
                BACKUP_LOCK.name,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=lock_parent,
            )
        except FileExistsError:
            lock_descriptor = os.open(
                BACKUP_LOCK.name,
                flags,
                dir_fd=lock_parent,
            )
        _validate_lock(lock_parent, lock_descriptor)
    except OSError as exc:
        if lock_descriptor >= 0:
            os.close(lock_descriptor)
        os.close(lock_parent)
        raise IntakeLaunchError("backup lock cannot be opened safely") from exc
    except BaseException:
        if lock_descriptor >= 0:
            os.close(lock_descriptor)
        os.close(lock_parent)
        raise
    return lock_parent, lock_descriptor


def _acquire_backup_lock(lock_descriptor: int) -> None:
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise IntakeLaunchError("backup lock acquisition failed") from exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _fail("backup lock remained busy for 300 seconds")
        time.sleep(min(LOCK_POLL_SECONDS, remaining))


def _validate_program(path: Path, *, kind: str, root_owned: bool) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise IntakeLaunchError(f"{kind} is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not stat.S_IMODE(metadata.st_mode) & 0o100
        or (root_owned and metadata.st_uid != EXPECTED_ROOT_UID)
    ):
        _fail(f"{kind} has unsafe metadata")


def _read_protected_file(
    parent_descriptor: int,
    name: str,
    *,
    kind: str,
    required_mode: int,
    maximum_bytes: int,
    minimum_bytes: int = 1,
) -> bytes:
    def identity(value: os.stat_result) -> tuple[int, ...]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
            value.st_uid,
            value.st_gid,
        )

    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
        before = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != EXPECTED_ROOT_UID
            or before.st_gid != EXPECTED_ROOT_GID
            or stat.S_IMODE(before.st_mode) != required_mode
            or not minimum_bytes <= before.st_size <= maximum_bytes
            or (visible.st_dev, visible.st_ino) != (before.st_dev, before.st_ino)
        ):
            _fail(f"{kind} has unsafe metadata")
        body = bytearray()
        while len(body) <= maximum_bytes:
            chunk = os.read(descriptor, min(4096, maximum_bytes + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        after = os.fstat(descriptor)
        if len(body) > maximum_bytes or identity(before) != identity(after):
            _fail(f"{kind} changed while being read")
        return bytes(body)
    except OSError as exc:
        raise IntakeLaunchError(f"{kind} cannot be opened safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_runtime_directory(
    path: Path,
    *,
    kind: str,
    mode: int,
    exact_entries: set[str] | frozenset[str] | None = None,
) -> int:
    descriptor = _open_directory_nofollow(path, kind=kind)
    try:
        _validate_visible_descriptor(
            path,
            descriptor,
            kind=kind,
            required_uid=EXPECTED_ROOT_UID,
            required_gid=EXPECTED_ROOT_GID,
            required_mode=mode,
        )
        if exact_entries is not None and set(os.listdir(descriptor)) != set(
            exact_entries
        ):
            _fail(f"{kind} has unexpected entries")
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise IntakeLaunchError(f"{kind} cannot be inspected safely") from exc
    except BaseException:
        os.close(descriptor)
        raise


def _validate_trusted_runtime_anchor() -> None:
    if (
        TRUSTED_RUNTIME_ROOT.parent != TRUSTED_RUNTIME_ANCHOR
        or TRUSTED_RUNTIME_POINTER.parent != TRUSTED_RUNTIME_ROOT
        or TRUSTED_RUNTIME_RELEASES.parent != TRUSTED_RUNTIME_ROOT
    ):
        _fail("trusted NBS runtime paths do not share the fixed trust anchor")

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    root_descriptor = -1
    try:
        root_descriptor = os.open("/", flags)
        root_metadata = os.fstat(root_descriptor)
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or root_metadata.st_uid != 0
            or stat.S_IMODE(root_metadata.st_mode) & 0o022
        ):
            _fail("trusted NBS system root is not protected")
    except OSError as exc:
        raise IntakeLaunchError(
            "trusted NBS system root cannot be opened safely"
        ) from exc
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)

    anchor_descriptor = _open_directory_nofollow(
        TRUSTED_RUNTIME_ANCHOR,
        kind="trusted NBS runtime anchor",
    )
    try:
        anchor_metadata = _validate_visible_descriptor(
            TRUSTED_RUNTIME_ANCHOR,
            anchor_descriptor,
            kind="trusted NBS runtime anchor",
            required_uid=EXPECTED_ROOT_UID,
        )
        if stat.S_IMODE(anchor_metadata.st_mode) & 0o022:
            _fail("trusted NBS runtime anchor is writable by an unsafe principal")
    finally:
        os.close(anchor_descriptor)


def _trusted_runtime_path(*, expected_sha: str | None = None) -> tuple[str, Path]:
    _validate_trusted_runtime_anchor()
    root_descriptor = _validate_runtime_directory(
        TRUSTED_RUNTIME_ROOT,
        kind="trusted NBS runtime root",
        mode=0o755,
        exact_entries={"current-sha", "releases"},
    )
    try:
        pointer = _read_protected_file(
            root_descriptor,
            TRUSTED_RUNTIME_POINTER.name,
            kind="trusted NBS runtime pointer",
            required_mode=0o444,
            maximum_bytes=41,
        )
    finally:
        os.close(root_descriptor)
    try:
        pointer_text = pointer.decode("ascii")
    except UnicodeDecodeError as exc:
        raise IntakeLaunchError("trusted NBS runtime pointer is not ASCII") from exc
    if (
        len(pointer_text) != 41
        or not pointer_text.endswith("\n")
        or _SHA_RE.fullmatch(pointer_text[:-1]) is None
    ):
        _fail("trusted NBS runtime pointer is malformed")
    revision = pointer_text[:-1]
    if expected_sha is not None and revision != expected_sha:
        _fail("trusted NBS runtime changed during intake")

    releases_descriptor = _validate_runtime_directory(
        TRUSTED_RUNTIME_RELEASES,
        kind="trusted NBS releases root",
        mode=0o555,
    )
    os.close(releases_descriptor)
    release = TRUSTED_RUNTIME_RELEASES / revision
    release_descriptor = _validate_runtime_directory(
        release,
        kind="trusted NBS release",
        mode=0o555,
        exact_entries={"seiche"},
    )
    os.close(release_descriptor)
    package = release / "seiche"
    package_descriptor = _validate_runtime_directory(
        package,
        kind="trusted NBS package",
        mode=0o555,
        exact_entries=_RUNTIME_PACKAGE_FILES,
    )
    try:
        for name in sorted(_RUNTIME_PACKAGE_FILES):
            _read_protected_file(
                package_descriptor,
                name,
                kind=f"trusted NBS package file {name}",
                required_mode=0o444,
                maximum_bytes=512 * 1024,
                minimum_bytes=0,
            )
    finally:
        os.close(package_descriptor)
    return revision, release


def _preflight_command() -> list[str]:
    return [
        str(SYSTEM_PYTHON),
        "-I",
        str(STORAGE_PREFLIGHT),
        "--config",
        str(STORAGE_CONFIG),
        "--state-path",
        str(STATE_PATH),
        "--nbs-path",
        str(PRODUCTION_NBS_ROOT),
        "--backup-path",
        str(BACKUP_PATH),
    ]


def _minimal_environment() -> dict[str, str]:
    return {
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONNOUSERSITE": "1",
    }


def _run_preflight(*, phase: str) -> None:
    _validate_program(STORAGE_PREFLIGHT, kind="storage preflight", root_owned=True)
    try:
        result = subprocess.run(
            _preflight_command(),
            check=False,
            stdin=subprocess.DEVNULL,
            env=_minimal_environment(),
            timeout=PREFLIGHT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise IntakeLaunchError(f"{phase} storage preflight could not run") from exc
    if result.returncode != 0:
        _fail(f"{phase} storage preflight rejected the production volume")


def _write_pipe(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written < 1:
            _fail("could not create the child intake capability")
        offset += written


def _run_trusted_runtime(
    manifest: Path,
    signature: Path,
    raw: Path,
    *,
    root_descriptor: int,
) -> int:
    runtime_sha, _runtime_path = _trusted_runtime_path()
    _validate_program(LAUNCHER_PATH, kind="NBS intake launcher", root_owned=True)
    token = secrets.token_bytes(32)
    if hasattr(os, "pipe2"):
        token_read, token_write = os.pipe2(os.O_CLOEXEC)
    else:  # pragma: no cover - production is Linux
        token_read, token_write = os.pipe()
        os.set_inheritable(token_read, False)
        os.set_inheritable(token_write, False)
    try:
        try:
            _write_pipe(token_write, token)
        finally:
            os.close(token_write)
        command = [
            str(SYSTEM_PYTHON),
            "-I",
            "-B",
            str(LAUNCHER_PATH),
            "--guarded-child",
            runtime_sha,
            str(manifest),
            str(signature),
            str(raw),
        ]
        environment = _minimal_environment()
        environment.update(
            {
                GUARD_ROOT_FD_ENV: str(root_descriptor),
                GUARD_TOKEN_FD_ENV: str(token_read),
                GUARD_TOKEN_ENV: token.hex(),
            }
        )
        try:
            result = subprocess.run(
                command,
                check=False,
                env=environment,
                pass_fds=(root_descriptor, token_read),
            )
        finally:
            _trusted_runtime_path(expected_sha=runtime_sha)
        return result.returncode
    except OSError as exc:
        raise IntakeLaunchError("trusted NBS intake runtime could not run") from exc
    finally:
        os.close(token_read)


def _production_nbs_gid() -> int:
    try:
        group = grp.getgrnam("seiche")
    except (KeyError, OSError) as exc:
        raise IntakeLaunchError("the production seiche group is unavailable") from exc
    if not isinstance(group.gr_gid, int) or group.gr_gid <= 0:
        _fail("the production seiche group has an invalid gid")
    return group.gr_gid


def run_intake(
    manifest: Path,
    signature: Path,
    raw: Path,
) -> int:
    if os.geteuid() != EXPECTED_ROOT_UID:
        _fail("production NBS intake launcher must run as root")
    production_gid = _production_nbs_gid()
    lock_parent = -1
    lock_descriptor = -1
    root_descriptor = -1
    try:
        lock_parent, lock_descriptor = _open_backup_lock()
        _acquire_backup_lock(lock_descriptor)
        _validate_lock(lock_parent, lock_descriptor)

        root_descriptor = _open_directory_nofollow(
            PRODUCTION_NBS_ROOT,
            kind="production NBS root",
        )
        _validate_visible_descriptor(
            PRODUCTION_NBS_ROOT,
            root_descriptor,
            kind="production NBS root",
            required_uid=EXPECTED_ROOT_UID,
            required_gid=production_gid,
            required_mode=0o750,
        )
        _run_preflight(phase="pre-intake")
        _validate_visible_descriptor(
            PRODUCTION_NBS_ROOT,
            root_descriptor,
            kind="production NBS root",
            required_uid=EXPECTED_ROOT_UID,
            required_gid=production_gid,
            required_mode=0o750,
        )

        child_status = 1
        child_started = False
        try:
            child_started = True
            child_status = _run_trusted_runtime(
                manifest,
                signature,
                raw,
                root_descriptor=root_descriptor,
            )
        finally:
            if child_started:
                _run_preflight(phase="post-intake")
                _validate_visible_descriptor(
                    PRODUCTION_NBS_ROOT,
                    root_descriptor,
                    kind="production NBS root",
                    required_uid=EXPECTED_ROOT_UID,
                    required_gid=production_gid,
                    required_mode=0o750,
                )
                _validate_lock(lock_parent, lock_descriptor)
        return child_status
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)
        if lock_descriptor >= 0:
            os.close(lock_descriptor)
        if lock_parent >= 0:
            os.close(lock_parent)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("signature", type=Path)
    parser.add_argument("raw", type=Path)
    return parser


def _child_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("runtime_sha")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("signature", type=Path)
    parser.add_argument("raw", type=Path)
    return parser


def _run_guarded_child(options: argparse.Namespace) -> int:
    if os.geteuid() != EXPECTED_ROOT_UID:
        _fail("trusted NBS intake child must run as root")
    _runtime_sha, runtime_path = _trusted_runtime_path(expected_sha=options.runtime_sha)
    if any(path and str(path).startswith("/home/") for path in sys.path):
        _fail("trusted NBS intake child inherited an application import path")
    sys.path.insert(0, str(runtime_path))
    try:
        from seiche import nbs_intake, nbs_trust
    except (ImportError, OSError) as exc:
        raise IntakeLaunchError("trusted NBS package could not be imported") from exc
    loaded_paths = {
        "nbs_intake.py": Path(nbs_intake.__file__).resolve(),
        "nbs_trust.py": Path(nbs_trust.__file__).resolve(),
    }
    if any(
        loaded != runtime_path / "seiche" / name
        for name, loaded in loaded_paths.items()
    ):
        _fail("trusted NBS package resolved outside the selected runtime")
    try:
        context = nbs_intake.ingest_signed_export(
            options.manifest,
            options.signature,
            options.raw,
            root=PRODUCTION_NBS_ROOT,
        )
    except (nbs_intake.NBSIntakeError, OSError) as exc:
        print(
            json.dumps(
                {
                    "schema": "seiche.nbs-intake-error.v1",
                    "status": "rejected",
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(context.to_dict(), indent=2, sort_keys=True))
    return 0


def main(arguments: list[str] | None = None) -> int:
    selected_arguments = list(sys.argv[1:] if arguments is None else arguments)
    try:
        if selected_arguments[:1] == ["--guarded-child"]:
            return _run_guarded_child(
                _child_parser().parse_args(selected_arguments[1:])
            )
        options = _parser().parse_args(selected_arguments)
        return run_intake(
            options.manifest,
            options.signature,
            options.raw,
        )
    except IntakeLaunchError as exc:
        print(f"seiche NBS intake: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
