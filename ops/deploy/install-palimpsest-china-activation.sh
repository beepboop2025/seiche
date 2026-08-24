#!/usr/bin/env bash
# Install the inert, root-owned Palimpsest China activation boundary.
set -euo pipefail
umask 0077

ASSET_ROOT=${SEICHE_PRIVILEGED_ASSET_ROOT:?signed privileged asset root is required}
RELEASE_TARGET=${SEICHE_RELEASE_TARGET_SHA:?exact release target SHA is required}
RUNTIME_ROOT=/opt/seiche-palimpsest-china
STATE_ROOT=/var/lib/seiche-palimpsest-china
RECEIPTS_ROOT=$STATE_ROOT/receipts
LAUNCHER_SOURCE=$ASSET_ROOT/ops/deploy/seiche-palimpsest-china-activate.py
LAUNCHER_DESTINATION=/etc/seiche/libexec/seiche-palimpsest-china-activate.py
DEPLOY_RUNTIME=/run/seiche-deploy
ACTIVATION_LOCK=$DEPLOY_RUNTIME/palimpsest-china.lock

fail() {
    echo "Palimpsest China activation installer: $*" >&2
    exit 1
}

[ "${EUID:-$(id -u)}" -eq 0 ] || fail "must run as root"

validate_root_traversal() {
    local path=$1 label=$2
    /usr/bin/python3 -I -B - "$path" "$label" <<'PY'
from pathlib import Path
import os
import stat
import sys

text, label = sys.argv[1:]
path = Path(text)
if not path.is_absolute() or path == Path("/") or Path(os.path.normpath(path)) != path:
    raise SystemExit(f"{label} path is not absolute and canonical")
current = Path("/")
for component in path.parts[1:]:
    current /= component
    try:
        metadata = current.lstat()
    except OSError as exc:
        raise SystemExit(f"{label} traversal is unavailable: {current}: {exc}")
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SystemExit(f"{label} traversal is unsafe: {current}")
PY
}

[[ "$RELEASE_TARGET" =~ ^[0-9a-f]{40}$ ]] \
    || fail "release target must be an exact lowercase Git SHA"
[ "$ASSET_ROOT" != / ] && [[ "$ASSET_ROOT" = /* ]] \
    || fail "signed asset root is invalid"
validate_root_traversal "$ASSET_ROOT" "signed asset root" \
    || fail "signed asset root traversal is unsafe"
[ ! -L "$ASSET_ROOT" ] && [ -d "$ASSET_ROOT" ] \
    || fail "signed asset root is unavailable or a symlink"
[ "$(stat -c '%U:%G:%a' "$ASSET_ROOT")" = root:root:700 ] \
    || fail "signed asset root ownership or mode is unsafe"
if ! { [ -f "$ASSET_ROOT/.target-sha" ] \
    && [ ! -L "$ASSET_ROOT/.target-sha" ] \
    && [ "$(stat -c '%U:%G:%a:%h' "$ASSET_ROOT/.target-sha")" = root:root:600:1 ] \
    && printf '%s\n' "$RELEASE_TARGET" \
        | cmp -s - "$ASSET_ROOT/.target-sha"; }; then
    fail "signed asset target receipt does not match the release"
fi
[ -f "$LAUNCHER_SOURCE" ] && [ ! -L "$LAUNCHER_SOURCE" ] \
    || fail "activation launcher source is missing or unsafe"
[ "$(stat -c '%U:%G:%a:%h' "$LAUNCHER_SOURCE")" = root:root:644:1 ] \
    || fail "activation launcher source metadata is unsafe"
[ "$(sed -n '1p' "$LAUNCHER_SOURCE")" = '#!/usr/bin/python3 -I' ] \
    || fail "activation launcher has the wrong interpreter"
/usr/bin/python3 -I -B - "$LAUNCHER_SOURCE" <<'PY' \
    || fail "activation launcher does not compile"
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(encoding="utf-8")
compile(source, sys.argv[1], "exec")
PY

id -u seiche >/dev/null 2>&1 || fail "seiche user is unavailable"
getent group seiche >/dev/null || fail "seiche group is unavailable"

validate_root_traversal "$(dirname "$STATE_ROOT")" "state parent" \
    || fail "state parent traversal is unsafe"
validate_root_traversal /etc/seiche/libexec "Seiche libexec root" \
    || fail "Seiche libexec traversal is unsafe"
validate_root_traversal "$DEPLOY_RUNTIME" "deploy lock root" \
    || fail "deploy lock traversal is unsafe"
validate_root_traversal "$(dirname "$RUNTIME_ROOT")" "trusted runtime parent" \
    || fail "trusted runtime parent traversal is unsafe"

validate_directory() {
    local path=$1 owner=$2 group=$3 mode=$4 label=$5
    [ -d "$path" ] && [ ! -L "$path" ] \
        || fail "$label is not a regular directory"
    [ "$(stat -c '%U:%G:%a' "$path")" = "$owner:$group:$mode" ] \
        || fail "$label ownership or mode is unsafe"
}

if [ -e "$STATE_ROOT" ] || [ -L "$STATE_ROOT" ]; then
    validate_directory "$STATE_ROOT" root seiche 750 "state root"
else
    install -d -o root -g seiche -m 0750 "$STATE_ROOT"
    /usr/bin/sync "$(dirname "$STATE_ROOT")"
fi
validate_root_traversal "$STATE_ROOT" "state root" \
    || fail "state root traversal is unsafe"
if [ -e "$RECEIPTS_ROOT" ] || [ -L "$RECEIPTS_ROOT" ]; then
    validate_directory "$RECEIPTS_ROOT" root root 700 "receipts root"
else
    install -d -o root -g root -m 0700 "$RECEIPTS_ROOT"
    /usr/bin/sync "$STATE_ROOT"
fi
validate_directory "$DEPLOY_RUNTIME" root root 700 "deploy lock root"
if [ -e "$ACTIVATION_LOCK" ] || [ -L "$ACTIVATION_LOCK" ]; then
    [ -f "$ACTIVATION_LOCK" ] && [ ! -L "$ACTIVATION_LOCK" ] \
        && [ "$(stat -c '%U:%G:%a:%h' "$ACTIVATION_LOCK")" = root:root:600:1 ] \
        || fail "activation lock metadata is unsafe"
else
    install -o root -g root -m 0600 /dev/null "$ACTIVATION_LOCK"
    /usr/bin/sync -f "$ACTIVATION_LOCK"
    /usr/bin/sync "$DEPLOY_RUNTIME"
fi

validate_directory /etc/seiche/libexec root root 755 "Seiche libexec root"
if [ -e "$LAUNCHER_DESTINATION" ] || [ -L "$LAUNCHER_DESTINATION" ]; then
    [ -f "$LAUNCHER_DESTINATION" ] && [ ! -L "$LAUNCHER_DESTINATION" ] \
        && [ "$(stat -c '%U:%G:%a:%h' "$LAUNCHER_DESTINATION")" = root:root:500:1 ] \
        || fail "installed activation launcher metadata is unsafe"
fi
LAUNCHER_STAGE=$(mktemp /etc/seiche/libexec/.palimpsest-china-activate.XXXXXX)
cleanup() {
    rm -f -- "${LAUNCHER_STAGE:-}"
}
trap cleanup EXIT
install -o root -g root -m 0500 "$LAUNCHER_SOURCE" "$LAUNCHER_STAGE"
/usr/bin/python3 -I -B "$LAUNCHER_STAGE" 2>/dev/null && \
    fail "activation launcher unexpectedly accepted an empty invocation"
/usr/bin/sync -f "$LAUNCHER_STAGE"
mv -f -- "$LAUNCHER_STAGE" "$LAUNCHER_DESTINATION"
LAUNCHER_STAGE=""
/usr/bin/sync /etc/seiche/libexec

if [ -e "$RUNTIME_ROOT" ] || [ -L "$RUNTIME_ROOT" ]; then
    validate_directory "$RUNTIME_ROOT" root root 755 "trusted runtime root"
else
    install -d -o root -g root -m 0755 "$RUNTIME_ROOT"
    /usr/bin/sync "$(dirname "$RUNTIME_ROOT")"
fi
/usr/bin/python3 -I -B - \
    "$ASSET_ROOT" "$RELEASE_TARGET" "$RUNTIME_ROOT" <<'PY'
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import re
import secrets
import stat
import sys


asset_text, release_sha, runtime_text = sys.argv[1:]
sha_re = re.compile(r"[0-9a-f]{40}")
asset = Path(asset_text)
runtime = Path(runtime_text)
sources = {
    "__init__.py": asset / "backend/seiche/__init__.py",
    "china_economic_focus.py": asset / "backend/seiche/china_economic_focus.py",
    "nbs_trust.py": asset / "backend/seiche/nbs_trust.py",
    "palimpsest_china_activation.py": asset
    / "backend/seiche/palimpsest_china_activation.py",
    "palimpsest_china_intake.py": asset / "backend/seiche/palimpsest_china_intake.py",
}


def fail(message: str) -> None:
    raise SystemExit(f"Palimpsest China trusted runtime: {message}")


def directory(path: Path, mode: int) -> os.stat_result:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        fail(f"directory metadata is unsafe: {path}")
    return metadata


def file_body(
    path: Path,
    *,
    mode: int,
    maximum: int,
    uid: int = 0,
    gid: int = 0,
) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        visible = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != uid
            or metadata.st_gid != gid
            or stat.S_IMODE(metadata.st_mode) != mode
            or metadata.st_size > maximum
            or (metadata.st_dev, metadata.st_ino)
            != (visible.st_dev, visible.st_ino)
        ):
            fail(f"file metadata is unsafe: {path}")
        body = bytearray()
        while len(body) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        after = os.fstat(descriptor)
        identity = lambda value: (
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
            or len(body) != metadata.st_size
            or identity(metadata) != identity(after)
        ):
            fail(f"file size changed while reading: {path}")
        return bytes(body)
    finally:
        os.close(descriptor)


def publish_noreplace(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        fail("Linux renameat2 is required")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(destination)
    raise OSError(error, os.strerror(error), destination)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_release(path: Path) -> None:
    directory(path, 0o555)
    if {entry.name for entry in path.iterdir()} != {"seiche"}:
        fail("release members changed")
    package = path / "seiche"
    directory(package, 0o555)
    if {entry.name for entry in package.iterdir()} != set(sources):
        fail("package members changed")
    for name, source in sources.items():
        installed = file_body(package / name, mode=0o444, maximum=2 * 1024 * 1024)
        expected = file_body(source, mode=0o644, maximum=2 * 1024 * 1024)
        if installed != expected:
            fail(f"installed module differs from signed asset: {name}")


if (
    os.geteuid() != 0
    or sha_re.fullmatch(release_sha) is None
    or runtime != Path("/opt/seiche-palimpsest-china")
):
    fail("production identity, target, or root is invalid")
directory(runtime, 0o755)
if any(name.startswith(".") for name in os.listdir(runtime)):
    fail("interrupted runtime staging requires operator review")
if any(name not in {"current-sha", "releases"} for name in os.listdir(runtime)):
    fail("runtime root contains an unexpected member")
for source in sources.values():
    metadata = source.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o644
    ):
        fail(f"signed source is missing or unsafe: {source}")

releases = runtime / "releases"
if releases.exists() or releases.is_symlink():
    directory(releases, 0o555)
else:
    os.mkdir(releases, 0o555)
    os.chown(releases, 0, 0)
    os.chmod(releases, 0o555)
    fsync_directory(runtime)
if any(name.startswith(".") for name in os.listdir(releases)):
    fail("interrupted release staging requires operator review")
if any(sha_re.fullmatch(name) is None for name in os.listdir(releases)):
    fail("releases root contains an unexpected member")

target = releases / release_sha
if target.exists() or target.is_symlink():
    validate_release(target)
else:
    stage = releases / f".release-stage-{secrets.token_hex(16)}"
    os.chmod(releases, 0o755)
    try:
        os.mkdir(stage, 0o700)
    finally:
        os.chmod(releases, 0o555)
    try:
        package = stage / "seiche"
        os.mkdir(package, 0o700)
        for name, source in sources.items():
            body = file_body(
                source,
                mode=0o644,
                maximum=2 * 1024 * 1024,
            )
            destination = package / name
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
            )
            try:
                offset = 0
                while offset < len(body):
                    written = os.write(descriptor, body[offset:])
                    if written < 1:
                        fail(f"runtime module write made no progress: {name}")
                    offset += written
                os.fchown(descriptor, 0, 0)
                os.fchmod(descriptor, 0o444)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        os.chown(package, 0, 0)
        os.chmod(package, 0o555)
        fsync_directory(package)
        os.chown(stage, 0, 0)
        os.chmod(stage, 0o555)
        fsync_directory(stage)
        try:
            publish_noreplace(stage, target)
        except FileExistsError:
            validate_release(target)
        fsync_directory(releases)
    finally:
        if stage.exists() and not stage.is_symlink():
            for name in sources:
                try:
                    (stage / "seiche" / name).unlink()
                except FileNotFoundError:
                    pass
            try:
                (stage / "seiche").rmdir()
            except FileNotFoundError:
                pass
            try:
                stage.rmdir()
            except FileNotFoundError:
                pass
    validate_release(target)

pointer = runtime / "current-sha"
if pointer.exists() or pointer.is_symlink():
    existing_pointer = file_body(pointer, mode=0o444, maximum=64)
    try:
        pointer_text = existing_pointer.decode("ascii")
    except UnicodeDecodeError:
        fail("existing runtime pointer is not ASCII")
    if not pointer_text.endswith("\n") or sha_re.fullmatch(pointer_text[:-1]) is None:
        fail("existing runtime pointer is malformed")
pointer_stage = runtime / f".current-sha-{secrets.token_hex(16)}"
descriptor = os.open(
    pointer_stage,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
    0o400,
)
try:
    body = f"{release_sha}\n".encode("ascii")
    offset = 0
    while offset < len(body):
        written = os.write(descriptor, body[offset:])
        if written < 1:
            fail("runtime pointer write made no progress")
        offset += written
    os.fchown(descriptor, 0, 0)
    os.fchmod(descriptor, 0o444)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
os.replace(pointer_stage, pointer)
fsync_directory(runtime)
PY

trap - EXIT
echo "Palimpsest China activation installer: inert trusted runtime installed for $RELEASE_TARGET"
