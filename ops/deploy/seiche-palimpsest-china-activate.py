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
MARKET_LOCK = Path("/run/lock/seiche-market-backup.lock")
DEPLOYED_SHA = Path("/var/lib/seiche-deploy/deployed-sha")
RUNTIME_ROOT = Path("/opt/seiche-palimpsest-china")
LIVE_STATE_ROOT = Path("/var/lib/seiche-palimpsest-china")
LOCK_TIMEOUT_SECONDS = 300.0
MARKET_LOCK_FD_ENV = "SEICHE_PALIMPSEST_CHINA_MARKET_LOCK_FD"
LOCKED_RUNNERS = {
    "backup": Path("/etc/seiche/libexec/seiche-market-backup.sh"),
    "restore": Path("/etc/seiche/libexec/seiche-market-restore-check.sh"),
}
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
                _fail(f"{label} lock remained busy for 300 seconds")
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


def _open_market_lock(*, expected_uid: int = 0, expected_gid: int = 0) -> int:
    return _open_lock(
        MARKET_LOCK,
        label="market mutation",
        parent_mode=0o775,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )


def _validate_inherited_market_lock(
    descriptor: int,
    *,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> None:
    """Prove that descriptor is the already-held exact market lock."""

    contender = -1
    try:
        if descriptor < 3:
            _fail("inherited market mutation lock descriptor is invalid")
        opened = os.fstat(descriptor)
        visible = os.stat(MARKET_LOCK, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != expected_uid
            or opened.st_gid != expected_gid
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            _fail("inherited market mutation lock metadata is unsafe")

        contender = os.open(
            MARKET_LOCK,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            fcntl.flock(contender, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
        else:
            fcntl.flock(contender, fcntl.LOCK_UN)
            _fail("inherited market mutation lock is not already exclusive")

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                _fail("inherited descriptor does not own the market mutation lock")
            raise
        after = os.fstat(descriptor)
        current = os.stat(MARKET_LOCK, follow_symlinks=False)
        if (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino):
            _fail("inherited market mutation lock changed identity")
    except OSError as exc:
        raise LaunchError(
            "inherited market mutation lock cannot be validated safely"
        ) from exc
    finally:
        if contender >= 0:
            os.close(contender)


def _inherited_market_lock() -> int:
    value = os.environ.get(MARKET_LOCK_FD_ENV, "")
    if re.fullmatch(r"[3-9]|[1-9][0-9]{1,2}", value) is None:
        _fail("market-locked audit descriptor is missing or malformed")
    descriptor = int(value)
    _validate_inherited_market_lock(descriptor)
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


def run(arguments: list[str]) -> int:
    if os.geteuid() != 0:
        _fail("activation launcher must run as root")
    locked_runner_mode = (
        len(arguments) == 2
        and arguments[0] == "--run-market-locked"
        and arguments[1] in LOCKED_RUNNERS
    )
    audit_mode = len(arguments) == 3 and arguments[0] == "--audit-state"
    if (
        not locked_runner_mode
        and not audit_mode
        and len(arguments) != len(_SOURCE_LABELS)
    ):
        labels = " ".join(f"<{label.replace(' ', '-')}>" for label in _SOURCE_LABELS)
        _fail(
            f"usage: {LAUNCHER} {labels}; or "
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
    else:
        sources = [Path(value) for value in arguments]
        if any(
            not path.is_absolute()
            or path == Path("/")
            or Path(os.path.normpath(path)) != path
            for path in sources
        ):
            _fail("every handoff source path must be absolute and canonical")

    deploy_lock = -1
    market_lock = -1
    if audit_mode:
        market_lock = _inherited_market_lock()
    else:
        # Below any outer activation transaction, controller order is deploy ->
        # market -> activation. Backup, restore, and offsite never take the
        # outer/deploy locks; backup and restore hold only market and pass that
        # exact FD to audit mode.
        deploy_lock = _open_deploy_lock()
        try:
            market_lock = _open_market_lock()
        except BaseException:
            os.close(deploy_lock)
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
        )
        result = activation.activate_bundle(
            activation.BundleSources(*sources),
            paths=paths,
            deploy_lock_descriptor=deploy_lock,
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
