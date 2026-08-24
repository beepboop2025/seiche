#!/usr/bin/python3 -I
"""Activate one owner-accepted Palimpsest China bundle on the Seiche host."""

from __future__ import annotations

import errno
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys
import time
from typing import NoReturn


LAUNCHER = Path("/etc/seiche/libexec/seiche-palimpsest-china-activate.py")
DEPLOY_LOCK = Path("/run/seiche-deploy/deploy.lock")
DEPLOYED_SHA = Path("/var/lib/seiche-deploy/deployed-sha")
RUNTIME_ROOT = Path("/opt/seiche-palimpsest-china")
LOCK_TIMEOUT_SECONDS = 300.0
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


def _open_deploy_lock() -> int:
    descriptor = -1
    try:
        descriptor = os.open(
            DEPLOY_LOCK,
            os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        visible = os.stat(DEPLOY_LOCK, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != 0
            or opened.st_gid != 0
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            _fail("deploy lock metadata is unsafe")
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail("deploy lock remained busy for 300 seconds")
            time.sleep(min(0.1, remaining))
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise LaunchError("deploy lock cannot be acquired safely") from exc
    except BaseException:
        if descriptor >= 0:
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


def run(arguments: list[str]) -> int:
    if os.geteuid() != 0:
        _fail("activation launcher must run as root")
    if len(arguments) != len(_SOURCE_LABELS):
        labels = " ".join(f"<{label.replace(' ', '-')}>" for label in _SOURCE_LABELS)
        _fail(f"usage: {LAUNCHER} {labels}")
    _validate_launcher()
    sources = [Path(value) for value in arguments]
    if any(
        not path.is_absolute()
        or path == Path("/")
        or Path(os.path.normpath(path)) != path
        for path in sources
    ):
        _fail("every handoff source path must be absolute and canonical")

    lock = _open_deploy_lock()
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
        api_uid, api_gid = _identity()
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
        )
        result = activation.activate_bundle(
            activation.BundleSources(*sources),
            paths=paths,
            deploy_lock_descriptor=lock,
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
        os.close(lock)


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
