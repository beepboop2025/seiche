#!/usr/bin/env bash
# Install the direct main->Hetzner release controller.  Installation is inert by
# default: explicitly set SEICHE_ENABLE_RELEASE_POLLER=1 only after its shadow
# gate is green and the GitHub Actions deploy trigger has been disabled.
set -euo pipefail

ASSET_ROOT="${SEICHE_PRIVILEGED_ASSET_ROOT:?signed privileged asset root is required}"
RELEASE_TARGET="${SEICHE_RELEASE_TARGET_SHA:?exact release target SHA is required}"
SYSTEMD_DIR="${SEICHE_SYSTEMD_DIR:-/etc/systemd/system}"
SCRIPT_DEST="${SEICHE_RELEASE_POLLER_DEST:-/usr/local/sbin/seiche-release-poll}"
DEPLOY_WRAPPER="${SEICHE_DEPLOY_WRAPPER:-/var/lib/seiche-deploy/bin/seiche-deploy-wrapper.sh}"
WRAPPER_DIR=$(dirname -- "$DEPLOY_WRAPPER")
REMOTE_GATE_VERIFIER="${SEICHE_REMOTE_GATE_VERIFIER_DEST:-$WRAPPER_DIR/seiche-remote-gate-verify.py}"
RUNTIME_DIR="${SEICHE_CONTROL_RUNTIME_DIR:-/run/seiche-control}"
NBS_STATE_DIR="${SEICHE_NBS_STATE_DIR:-/var/lib/seiche-nbs}"
NBS_RUNTIME_ROOT="${SEICHE_NBS_RUNTIME_ROOT:-/opt/seiche-nbs-intake}"
CONTROL_LOCK="$RUNTIME_DIR/release.lock"
SYSTEMCTL="${SEICHE_SYSTEMCTL_BIN:-systemctl}"
SYSTEMD_ANALYZE="${SEICHE_SYSTEMD_ANALYZE_BIN:-systemd-analyze}"
SYNC="${SEICHE_SYNC_BIN:-/usr/bin/sync}"
FLOCK="${SEICHE_FLOCK_BIN:-flock}"
SYSTEM_PYTHON="${SEICHE_CONTROL_PYTHON:-/usr/bin/python3}"
ENABLE="${SEICHE_ENABLE_RELEASE_POLLER:-0}"
SOURCE_DIR="$ASSET_ROOT/ops/deploy"
SOURCE_WRAPPER="$SOURCE_DIR/seiche-deploy-wrapper.sh"
SOURCE_REMOTE_GATE_VERIFIER="$SOURCE_DIR/seiche-remote-gate-verify.py"
SOURCE_SIGNER="$SOURCE_DIR/release-allowed-signers"
ALLOWED_SIGNERS="${SEICHE_RELEASE_ALLOWED_SIGNERS_DEST:-/etc/seiche-release.allowed-signers}"
SIGNING_PRINCIPAL=beepboop2025@users.noreply.github.com
SCRIPT_DIR=$(dirname -- "$SCRIPT_DEST")
STAGE_DIR=""
SCRIPT_NEW=""
WRAPPER_NEW=""
REMOTE_GATE_VERIFIER_NEW=""
SIGNER_STAGE=""
INSTALL_STARTED=""
INSTALL_COMMITTED=""
NBS_RUNTIME_ANCHOR_CREATED=""
NBS_RUNTIME_ANCHOR_IDENTITY=""
WAS_ENABLED=""
WAS_ACTIVE=""
HAD_SCRIPT=""
HAD_WRAPPER=""
HAD_REMOTE_GATE_VERIFIER=""
HAD_SERVICE=""
HAD_TIMER=""

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

validate_signed_controller_assets() {
  "$SYSTEM_PYTHON" -I -B - "$ASSET_ROOT" "$RELEASE_TARGET" \
    "$EXPECTED_SIGNER_UID" "$EXPECTED_SIGNER_GID" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

root_text, target, uid_text, gid_text = sys.argv[1:]
uid, gid = int(uid_text), int(gid_text)
sha_re = re.compile(r"[0-9a-f]{40}")
required = {
    "ops/deploy/release-allowed-signers": "100644",
    "ops/deploy/seiche-deploy-wrapper.sh": "100644",
    "ops/deploy/seiche-release-poll.service": "100644",
    "ops/deploy/seiche-release-poll.sh": "100755",
    "ops/deploy/seiche-release-poll.timer": "100644",
    "ops/deploy/seiche-remote-gate-verify.py": "100755",
}


def reject(message: str) -> None:
    raise SystemExit(f"signed controller assets {message}")


if (
    not root_text.startswith("/")
    or os.path.normpath(root_text) != root_text
    or root_text == "/"
    or sha_re.fullmatch(target) is None
):
    reject("path or target is invalid")
root = Path(root_text)
flags = (
    os.O_RDONLY
    | os.O_DIRECTORY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
root_fd = os.open("/", flags)
try:
    for component in root.parts[1:]:
        child = os.open(component, flags, dir_fd=root_fd)
        visible = os.stat(component, dir_fd=root_fd, follow_symlinks=False)
        opened = os.fstat(child)
        if not stat.S_ISDIR(visible.st_mode) or (
            visible.st_dev,
            visible.st_ino,
        ) != (opened.st_dev, opened.st_ino):
            os.close(child)
            reject("has an unsafe path component")
        os.close(root_fd)
        root_fd = child
    metadata = os.fstat(root_fd)
    if (
        metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        reject("root metadata is unsafe")

    def read_file(path: str, mode: int, maximum: int) -> bytes:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != uid
                or info.st_gid != gid
                or stat.S_IMODE(info.st_mode) != mode
                or info.st_size > maximum
            ):
                reject(f"file metadata is unsafe: {path}")
            body = bytearray()
            while len(body) <= maximum:
                chunk = os.read(descriptor, min(65536, maximum + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            if len(body) > maximum:
                reject(f"file is too large: {path}")
            return bytes(body)
        finally:
            os.close(descriptor)

    if read_file(".target-sha", 0o600, 41) != f"{target}\n".encode("ascii"):
        reject("target marker mismatch")
    try:
        manifest = json.loads(
            read_file(".seiche-release-assets.json", 0o600, 2 * 1024 * 1024)
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        reject(f"manifest is invalid: {exc}")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "seiche.signed-privileged-assets.v1"
        or manifest.get("target_sha") != target
        or manifest.get("git_object_format") != "sha1"
        or not isinstance(manifest.get("entries"), list)
    ):
        reject("manifest contract is invalid")
    entries = {}
    for entry in manifest["entries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            reject("manifest has a malformed entry")
        path = entry["path"]
        if path in entries:
            reject(f"manifest repeats path: {path}")
        entries[path] = entry
    for path, git_mode in required.items():
        entry = entries.get(path)
        if (
            not isinstance(entry, dict)
            or entry.get("git_mode") != git_mode
            or type(entry.get("size")) is not int
            or not isinstance(entry.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
        ):
            reject(f"manifest lacks required path/mode: {path}")
        body = read_file(path, 0o755 if git_mode == "100755" else 0o644, 16 * 1024 * 1024)
        if len(body) != entry["size"] or hashlib.sha256(body).hexdigest() != entry["sha256"]:
            reject(f"bytes do not match manifest: {path}")
finally:
    os.close(root_fd)
PY
}

ensure_nbs_runtime_anchor() {
  "$SYSTEM_PYTHON" -I -B - "$NBS_RUNTIME_ROOT" \
    "$EXPECTED_SIGNER_UID" "$EXPECTED_SIGNER_GID" \
    "${SEICHE_ALLOW_NON_ROOT_INSTALL_TEST:-0}" <<'PY'
import ctypes
import errno
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys

path_text, uid_text, gid_text, portable_text = sys.argv[1:]
uid, gid = int(uid_text), int(gid_text)
path = Path(path_text)
if (
    not path_text.startswith("/")
    or os.path.normpath(path_text) != path_text
    or path_text == "/"
    or portable_text not in {"0", "1"}
):
    raise SystemExit("NBS runtime anchor path is invalid")
flags = (
    os.O_RDONLY
    | os.O_DIRECTORY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
parent_fd = os.open("/", flags)
created_anchor = False
try:
    parent_metadata = os.fstat(parent_fd)
    for component in path.parent.parts[1:]:
        if (
            (parent_metadata.st_uid, parent_metadata.st_gid)
            not in {(0, 0), (uid, gid)}
            or stat.S_IMODE(parent_metadata.st_mode) & 0o022
        ):
            raise SystemExit("NBS runtime anchor ancestry is unsafe")
        child = os.open(component, flags, dir_fd=parent_fd)
        visible = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(child)
        if not stat.S_ISDIR(visible.st_mode) or (
            visible.st_dev,
            visible.st_ino,
        ) != (opened.st_dev, opened.st_ino):
            os.close(child)
            raise SystemExit("NBS runtime anchor ancestry changed identity")
        os.close(parent_fd)
        parent_fd = child
        parent_metadata = opened
    if (
        (parent_metadata.st_uid, parent_metadata.st_gid)
        not in {(0, 0), (uid, gid)}
        or stat.S_IMODE(parent_metadata.st_mode) & 0o022
    ):
        raise SystemExit("NBS runtime anchor parent is unsafe")
    for name in os.listdir(parent_fd):
        if name.startswith(".seiche-nbs-intake-anchor-"):
            raise SystemExit(
                "interrupted NBS runtime anchor stage requires empty root-owned inspection"
            )
    try:
        anchor_fd = os.open(path.name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        stage = f".seiche-nbs-intake-anchor-{secrets.token_hex(16)}"
        stage_path = path.parent / stage
        created = False
        try:
            os.mkdir(stage, 0o700, dir_fd=parent_fd)
            created = True
            stage_fd = os.open(stage, flags, dir_fd=parent_fd)
            try:
                if (os.fstat(stage_fd).st_uid, os.fstat(stage_fd).st_gid) != (uid, gid):
                    os.fchown(stage_fd, uid, gid)
                os.fchmod(stage_fd, 0o755)
                os.fsync(stage_fd)
            finally:
                os.close(stage_fd)
            os.fsync(parent_fd)
            if sys.platform.startswith("linux"):
                libc = ctypes.CDLL(None, use_errno=True)
                renameat2 = getattr(libc, "renameat2", None)
                if renameat2 is None:
                    raise SystemExit("Linux renameat2 is required")
                renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
                renameat2.restype = ctypes.c_int
                if renameat2(parent_fd, stage.encode(), parent_fd, path.name.encode(), 1) != 0:
                    error = ctypes.get_errno()
                    if error != errno.EEXIST:
                        raise SystemExit(f"NBS runtime anchor publication failed: {error}")
                    raise SystemExit("NBS runtime anchor appeared concurrently")
            elif portable_text == "1" and uid != 0:
                os.rename(stage, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            else:
                raise SystemExit("production anchor publication requires Linux")
            created = False
            created_anchor = True
            os.fsync(parent_fd)
        finally:
            if created:
                try:
                    shutil.rmtree(stage_path)
                except FileNotFoundError:
                    pass
        anchor_fd = os.open(path.name, flags, dir_fd=parent_fd)
    try:
        opened = os.fstat(anchor_fd)
        visible = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != uid
            or opened.st_gid != gid
            or stat.S_IMODE(opened.st_mode) != 0o755
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            raise SystemExit("NBS runtime anchor metadata is unsafe")
        print(
            f"{'created' if created_anchor else 'existing'}:"
            f"{opened.st_dev}:{opened.st_ino}"
        )
    finally:
        os.close(anchor_fd)
finally:
    os.close(parent_fd)
PY
}

remove_created_nbs_runtime_anchor() {
  [ -n "$NBS_RUNTIME_ANCHOR_CREATED" ] || return 0
  "$SYSTEM_PYTHON" -I -B - "$NBS_RUNTIME_ROOT" \
    "$EXPECTED_SIGNER_UID" "$EXPECTED_SIGNER_GID" \
    "$NBS_RUNTIME_ANCHOR_IDENTITY" <<'PY'
import os
from pathlib import Path
import stat
import sys

path_text, uid_text, gid_text, identity = sys.argv[1:]
uid, gid = int(uid_text), int(gid_text)
path = Path(path_text)
if not path_text.startswith("/") or os.path.normpath(path_text) != path_text:
    raise SystemExit(1)
flags = (
    os.O_RDONLY
    | os.O_DIRECTORY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
parent_fd = os.open("/", flags)
try:
    for component in path.parent.parts[1:]:
        child = os.open(component, flags, dir_fd=parent_fd)
        visible = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(child)
        if not stat.S_ISDIR(visible.st_mode) or (
            visible.st_dev,
            visible.st_ino,
        ) != (opened.st_dev, opened.st_ino):
            os.close(child)
            raise SystemExit(1)
        os.close(parent_fd)
        parent_fd = child
    anchor_fd = os.open(path.name, flags, dir_fd=parent_fd)
    try:
        opened = os.fstat(anchor_fd)
        if (
            opened.st_uid != uid
            or opened.st_gid != gid
            or stat.S_IMODE(opened.st_mode) != 0o755
            or f"{opened.st_dev}:{opened.st_ino}" != identity
            or os.listdir(anchor_fd)
        ):
            raise SystemExit(1)
    finally:
        os.close(anchor_fd)
    os.rmdir(path.name, dir_fd=parent_fd)
    os.fsync(parent_fd)
finally:
    os.close(parent_fd)
PY
}

verify_system_ed25519() {
  "$SYSTEM_PYTHON" -I -B - <<'PY'
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

private_key = Ed25519PrivateKey.generate()
public_key = private_key.public_key()
message = b"seiche-controller-ed25519-self-test"
signature = private_key.sign(message)
public_key.verify(signature, message)
PY
}

validate_nbs_state_root() {
  "$SYSTEM_PYTHON" -I -B - "$NBS_STATE_DIR" "$EXPECTED_NBS_UID" \
    "$EXPECTED_NBS_GID" <<'PY'
import os
import stat
import sys

path, uid, gid = sys.argv[1:]
if not path.startswith("/") or os.path.normpath(path) != path or path == "/":
    raise SystemExit(1)


def snapshot() -> tuple[tuple[int, int], ...]:
    descriptor = os.open(
        "/", os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    )
    identities: list[tuple[int, int]] = []
    try:
        for component in path.split("/")[1:]:
            child = os.open(
                component,
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            visible = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            opened = os.fstat(child)
            if (
                not stat.S_ISDIR(visible.st_mode)
                or (visible.st_dev, visible.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                os.close(child)
                raise SystemExit(1)
            identities.append((opened.st_dev, opened.st_ino))
            os.close(descriptor)
            descriptor = child
        final = os.fstat(descriptor)
        if (
            final.st_uid != int(uid)
            or final.st_gid != int(gid)
            or stat.S_IMODE(final.st_mode) != 0o750
        ):
            raise SystemExit(1)
        return tuple(identities)
    finally:
        os.close(descriptor)


try:
    before = snapshot()
    after = snapshot()
except OSError:
    raise SystemExit(1) from None
if not before or before != after:
    raise SystemExit(1)
PY
}

remove_staging() {
  [ -z "$SCRIPT_NEW" ] || rm -f -- "$SCRIPT_NEW"
  [ -z "$WRAPPER_NEW" ] || rm -f -- "$WRAPPER_NEW"
  [ -z "$REMOTE_GATE_VERIFIER_NEW" ] || rm -f -- "$REMOTE_GATE_VERIFIER_NEW"
  [ -z "$SIGNER_STAGE" ] || rm -f -- "$SIGNER_STAGE"
  if [ -n "$STAGE_DIR" ]; then
    rm -f -- \
      "$STAGE_DIR/seiche-release-poll" \
      "$STAGE_DIR/seiche-deploy-wrapper.sh" \
      "$STAGE_DIR/seiche-release-poll.service" \
      "$STAGE_DIR/seiche-release-poll.timer" \
      "$STAGE_DIR/seiche-remote-gate-verify.py" \
      "$STAGE_DIR/previous-script" \
      "$STAGE_DIR/previous-wrapper" \
      "$STAGE_DIR/previous-remote-gate-verifier" \
      "$STAGE_DIR/previous-service" \
      "$STAGE_DIR/previous-timer"
    rmdir "$STAGE_DIR" 2>/dev/null || true
  fi
}

restore_file() {
  local destination="$1" backup="$2" had_previous="$3" restore=""
  if [ -n "$had_previous" ]; then
    restore=$(mktemp "$(dirname -- "$destination")/.seiche-release-restore.XXXXXX") \
      || return 1
    if ! cp -p -- "$backup" "$restore" || ! mv -f -- "$restore" "$destination"; then
      rm -f -- "$restore"
      return 1
    fi
  else
    rm -f -- "$destination" || return 1
  fi
}

rollback_install() {
  local failed=""
  echo "install: restoring the previous release-controller files and timer state" >&2
  restore_file "$SYSTEMD_DIR/seiche-release-poll.timer" \
    "$STAGE_DIR/previous-timer" "$HAD_TIMER" || failed=1
  restore_file "$SYSTEMD_DIR/seiche-release-poll.service" \
    "$STAGE_DIR/previous-service" "$HAD_SERVICE" || failed=1
  restore_file "$SCRIPT_DEST" "$STAGE_DIR/previous-script" "$HAD_SCRIPT" \
    || failed=1
  restore_file "$REMOTE_GATE_VERIFIER" \
    "$STAGE_DIR/previous-remote-gate-verifier" "$HAD_REMOTE_GATE_VERIFIER" \
    || failed=1
  restore_file "$DEPLOY_WRAPPER" "$STAGE_DIR/previous-wrapper" "$HAD_WRAPPER" \
    || failed=1
  "$SYSTEMCTL" daemon-reload || failed=1
  if [ -n "$WAS_ENABLED" ]; then
    "$SYSTEMCTL" enable seiche-release-poll.timer || failed=1
  else
    "$SYSTEMCTL" disable seiche-release-poll.timer 2>/dev/null || true
  fi
  if [ -n "$WAS_ACTIVE" ]; then
    "$SYSTEMCTL" start seiche-release-poll.timer || failed=1
  else
    "$SYSTEMCTL" stop seiche-release-poll.timer 2>/dev/null || true
  fi
  [ -z "$failed" ] || {
    echo "FAIL: release-controller install rollback was incomplete; inspect the five installed files and timer state" >&2
    return 1
  }
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [ -n "$INSTALL_STARTED" ] && [ -z "$INSTALL_COMMITTED" ]; then
    rollback_install || status=1
  fi
  if [ -z "$INSTALL_COMMITTED" ] && [ -n "$NBS_RUNTIME_ANCHOR_CREATED" ]; then
    if ! remove_created_nbs_runtime_anchor; then
      echo "WARN: retaining changed/nonempty NBS runtime anchor for root inspection" >&2
      status=1
    fi
  fi
  remove_staging
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$(id -u)" -ne 0 ] && [ "${SEICHE_ALLOW_NON_ROOT_INSTALL_TEST:-0}" != 1 ]; then
  fail "install-release-poller must run as root"
fi
if [ "$(id -u)" -eq 0 ]; then
  EXPECTED_SIGNER_UID=0
  EXPECTED_SIGNER_GID=0
  [ "$SYSTEM_PYTHON" = /usr/bin/python3 ] \
    || fail "production controller installation requires /usr/bin/python3"
  [ "$NBS_STATE_DIR" = /var/lib/seiche-nbs ] \
    || fail "NBS evidence root is fixed at /var/lib/seiche-nbs"
  [ "$NBS_RUNTIME_ROOT" = /opt/seiche-nbs-intake ] \
    || fail "NBS runtime root is fixed at /opt/seiche-nbs-intake"
  EXPECTED_NBS_UID=0
  EXPECTED_NBS_GID=$("$SYSTEM_PYTHON" -I -B - <<'PY'
import grp

try:
    gid = grp.getgrnam("seiche").gr_gid
except KeyError:
    raise SystemExit(1) from None
if gid <= 0:
    raise SystemExit(1)
print(gid)
PY
  ) \
    || fail "the seiche group is required for the NBS evidence root"
else
  EXPECTED_SIGNER_UID=$(id -u)
  EXPECTED_SIGNER_GID=$(id -g)
  [ "$NBS_STATE_DIR" != /var/lib/seiche-nbs ] \
    || fail "non-root install tests must isolate the NBS evidence root"
  [ "$NBS_RUNTIME_ROOT" != /opt/seiche-nbs-intake ] \
    || fail "non-root install tests must isolate the NBS runtime root"
  case "$NBS_STATE_DIR" in
    /*) ;;
    *) fail "NBS evidence root must be an absolute path" ;;
  esac
  case "$NBS_RUNTIME_ROOT" in
    /*) ;;
    *) fail "NBS runtime root must be an absolute path" ;;
  esac
  EXPECTED_NBS_UID=$EXPECTED_SIGNER_UID
  EXPECTED_NBS_GID=$EXPECTED_SIGNER_GID
fi
case "$ENABLE" in
  0|1) ;;
  *) fail "SEICHE_ENABLE_RELEASE_POLLER must be exactly 0 or 1" ;;
esac
validate_signed_controller_assets \
  || fail "signed privileged controller assets are invalid"
validate_nbs_state_root \
  || fail "NBS evidence root is absent or has unsafe metadata"
verify_system_ed25519 \
  || fail "isolated system Python Ed25519 support is unavailable"
NBS_RUNTIME_ANCHOR_RESULT=$(ensure_nbs_runtime_anchor) \
  || fail "NBS runtime anchor is absent or unsafe"
case "$NBS_RUNTIME_ANCHOR_RESULT" in
  created:*)
    NBS_RUNTIME_ANCHOR_CREATED=1
    NBS_RUNTIME_ANCHOR_IDENTITY=${NBS_RUNTIME_ANCHOR_RESULT#created:}
    ;;
  existing:*) ;;
  *) fail "NBS runtime anchor result is invalid" ;;
esac
[ -d "$SYSTEMD_DIR" ] && [ ! -L "$SYSTEMD_DIR" ] \
  || fail "unsafe systemd directory: $SYSTEMD_DIR"
if [ -e "$SCRIPT_DIR" ] || [ -L "$SCRIPT_DIR" ]; then
  [ -d "$SCRIPT_DIR" ] && [ ! -L "$SCRIPT_DIR" ] \
    || fail "unsafe poller binary directory: $SCRIPT_DIR"
else
  install -d -o root -g root -m 0755 "$SCRIPT_DIR"
fi
if [ -e "$WRAPPER_DIR" ] || [ -L "$WRAPPER_DIR" ]; then
  [ -d "$WRAPPER_DIR" ] && [ ! -L "$WRAPPER_DIR" ] \
    || fail "unsafe deploy-wrapper directory: $WRAPPER_DIR"
else
  if [ "$(id -u)" -eq 0 ]; then
    install -d -o root -g root -m 0700 "$WRAPPER_DIR"
  else
    install -d -m 0700 "$WRAPPER_DIR"
  fi
fi
"$SYSTEM_PYTHON" -I -B - "$WRAPPER_DIR" "$EXPECTED_SIGNER_UID" \
  "$EXPECTED_SIGNER_GID" <<'PY' \
  || fail "deploy-wrapper directory metadata is unsafe"
import os
import stat
import sys

path, uid, gid = sys.argv[1:]
info = os.lstat(path)
if (
    not stat.S_ISDIR(info.st_mode)
    or info.st_uid != int(uid)
    or info.st_gid != int(gid)
    or stat.S_IMODE(info.st_mode) != 0o700
):
    raise SystemExit(1)
PY
for source in \
    seiche-deploy-wrapper.sh \
    seiche-release-poll.sh \
    seiche-release-poll.service \
    seiche-release-poll.timer \
    seiche-remote-gate-verify.py \
    release-allowed-signers; do
  [ -f "$SOURCE_DIR/$source" ] && [ ! -L "$SOURCE_DIR/$source" ] \
    || fail "missing or unsafe release-poller source: $SOURCE_DIR/$source"
done
for destination in \
    "$DEPLOY_WRAPPER" \
    "$REMOTE_GATE_VERIFIER" \
    "$SCRIPT_DEST" \
    "$SYSTEMD_DIR/seiche-release-poll.service" \
    "$SYSTEMD_DIR/seiche-release-poll.timer"; do
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    [ -f "$destination" ] && [ ! -L "$destination" ] \
      || fail "unsafe installed release-poller path: $destination"
  fi
done
bash -n "$SOURCE_WRAPPER"
bash -n "$SOURCE_DIR/seiche-release-poll.sh"
"$SYSTEM_PYTHON" -I -B - "$SOURCE_REMOTE_GATE_VERIFIER" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
compile(source, sys.argv[1], "exec")
PY
# The dollar expression is deliberately matched literally in the reviewed source.
# shellcheck disable=SC2016
grep -Fq 'EXPECTED_TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}' "$SOURCE_WRAPPER" \
  || fail "reviewed deploy wrapper lacks the expected-target-SHA safety pin"

# Validate the exact signed-asset copy without following symlinks or accepting
# comments/options/multiple principals. Production may only confirm the durable
# host trust anchor provisioned out of band before asset authentication; the
# creation path below exists solely for the isolated non-root installer harness.
EXPECTED_SIGNER=$("$SYSTEM_PYTHON" -I -B - \
  "$SOURCE_SIGNER" "$SIGNING_PRINCIPAL" <<'PY'
import base64
import os
import stat
import struct
import sys

path, expected_principal = sys.argv[1:]
descriptor = os.open(
    path,
    os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
)
try:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SystemExit("release signer source metadata is unsafe")
    with os.fdopen(descriptor, encoding="ascii") as handle:
        descriptor = -1
        content = handle.read()
finally:
    if descriptor >= 0:
        os.close(descriptor)

if "\r" in content or not content.endswith("\n") or content.count("\n") != 1:
    raise SystemExit("release signer source must contain one canonical line")
line = content[:-1]
parts = line.split(" ")
if len(parts) != 3 or any(not part for part in parts):
    raise SystemExit("release signer source has an invalid allowed-signers shape")
principal, key_type, key_material = parts
if principal != expected_principal:
    raise SystemExit("release signer source principal does not match release policy")
if key_type not in {"ssh-ed25519", "sk-ssh-ed25519@openssh.com"}:
    raise SystemExit("release signer source key type is not allowed")
try:
    decoded = base64.b64decode(key_material, validate=True)
    name_length = struct.unpack(">I", decoded[:4])[0]
    encoded_key_type = decoded[4 : 4 + name_length].decode("ascii")
except (ValueError, UnicodeDecodeError, struct.error):
    raise SystemExit("release signer source key material is invalid") from None
if encoded_key_type != key_type:
    raise SystemExit("release signer source key material has the wrong type")
print(line)
PY
) || fail "reviewed release signer source is invalid"

validate_installed_signer() {
  "$SYSTEM_PYTHON" -I -B - "$ALLOWED_SIGNERS" "$EXPECTED_SIGNER" \
    "$EXPECTED_SIGNER_UID" "$EXPECTED_SIGNER_GID" <<'PY'
import os
import stat
import sys

path, expected, uid, gid = sys.argv[1:]
try:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
except OSError as exc:
    raise SystemExit(f"installed release signer cannot be opened safely: {exc}")
try:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != int(uid)
        or info.st_gid != int(gid)
        or stat.S_IMODE(info.st_mode) != 0o444
    ):
        raise SystemExit("installed release signer metadata is unsafe")
    with os.fdopen(descriptor, encoding="ascii") as handle:
        descriptor = -1
        content = handle.read()
finally:
    if descriptor >= 0:
        os.close(descriptor)
if content != expected + "\n":
    raise SystemExit("installed release signer does not match the pinned identity")
PY
}

SIGNER_DIR=$(dirname -- "$ALLOWED_SIGNERS")
[ -d "$SIGNER_DIR" ] && [ ! -L "$SIGNER_DIR" ] \
  || fail "unsafe release signer directory: $SIGNER_DIR"
"$SYSTEM_PYTHON" -I -B - "$SIGNER_DIR" "$EXPECTED_SIGNER_UID" \
  "$EXPECTED_SIGNER_GID" <<'PY' \
  || fail "release signer directory metadata is unsafe"
import os
import stat
import sys

path, uid, gid = sys.argv[1:]
info = os.lstat(path)
if (
    not stat.S_ISDIR(info.st_mode)
    or info.st_uid != int(uid)
    or info.st_gid != int(gid)
    or stat.S_IMODE(info.st_mode) & 0o022
):
    raise SystemExit(1)
PY
if [ -e "$ALLOWED_SIGNERS" ] || [ -L "$ALLOWED_SIGNERS" ]; then
  validate_installed_signer \
    || fail "refusing to replace the pinned Seiche release signer"
else
  if [ "$(id -u)" -eq 0 ]; then
    fail "production requires the out-of-band Seiche release signer trust anchor"
  fi
  SIGNER_STAGE=$(mktemp "$SIGNER_DIR/.seiche-release.allowed-signers.XXXXXX") \
    || fail "could not stage the pinned Seiche release signer"
  printf '%s\n' "$EXPECTED_SIGNER" >"$SIGNER_STAGE"
  if [ "$(id -u)" -eq 0 ]; then
    chown root:root "$SIGNER_STAGE"
  fi
  chmod 0444 "$SIGNER_STAGE"
  "$SYNC" -f "$SIGNER_STAGE"
  # Hard-link installation is an atomic no-clobber operation. If another
  # process establishes the path first, fail instead of replacing its key.
  ln "$SIGNER_STAGE" "$ALLOWED_SIGNERS" \
    || fail "refusing to replace a concurrently pinned Seiche release signer"
  rm -f -- "$SIGNER_STAGE"
  SIGNER_STAGE=""
  "$SYNC" "$SIGNER_DIR"
  validate_installed_signer \
    || fail "new Seiche release signer pin failed its metadata check"
fi

if [ -L "$RUNTIME_DIR" ] || { [ -e "$RUNTIME_DIR" ] && [ ! -d "$RUNTIME_DIR" ]; }; then
  fail "unsafe release-controller runtime directory: $RUNTIME_DIR"
fi
if [ "$(id -u)" -eq 0 ]; then
  install -d -o root -g root -m 0700 "$RUNTIME_DIR"
else
  install -d -m 0700 "$RUNTIME_DIR"
fi
exec 9>"$CONTROL_LOCK"
if [ "$(id -u)" -eq 0 ]; then
  chown root:root "$CONTROL_LOCK"
fi
chmod 0600 "$CONTROL_LOCK"
"$FLOCK" --nonblock 9 \
  || fail "a release poll or another poller installation is already active"

if "$SYSTEMCTL" is-enabled --quiet seiche-release-poll.timer 2>/dev/null; then
  WAS_ENABLED=1
fi
if "$SYSTEMCTL" is-active --quiet seiche-release-poll.timer 2>/dev/null; then
  WAS_ACTIVE=1
fi

STAGE_DIR=$(mktemp -d "$SYSTEMD_DIR/.seiche-release-poll.XXXXXX")
install -m 0700 "$SOURCE_WRAPPER" \
  "$STAGE_DIR/seiche-deploy-wrapper.sh"
install -m 0755 "$SOURCE_DIR/seiche-release-poll.sh" \
  "$STAGE_DIR/seiche-release-poll"
install -m 0700 "$SOURCE_REMOTE_GATE_VERIFIER" \
  "$STAGE_DIR/seiche-remote-gate-verify.py"
install -m 0644 "$SOURCE_DIR/seiche-release-poll.service" \
  "$STAGE_DIR/seiche-release-poll.service"
install -m 0644 "$SOURCE_DIR/seiche-release-poll.timer" \
  "$STAGE_DIR/seiche-release-poll.timer"
bash -n "$STAGE_DIR/seiche-release-poll"
bash -n "$STAGE_DIR/seiche-deploy-wrapper.sh"

if [ -e "$DEPLOY_WRAPPER" ]; then
  cp -p -- "$DEPLOY_WRAPPER" "$STAGE_DIR/previous-wrapper"
  HAD_WRAPPER=1
fi
if [ -e "$REMOTE_GATE_VERIFIER" ]; then
  cp -p -- "$REMOTE_GATE_VERIFIER" \
    "$STAGE_DIR/previous-remote-gate-verifier"
  HAD_REMOTE_GATE_VERIFIER=1
fi
if [ -e "$SCRIPT_DEST" ]; then
  cp -p -- "$SCRIPT_DEST" "$STAGE_DIR/previous-script"
  HAD_SCRIPT=1
fi
if [ -e "$SYSTEMD_DIR/seiche-release-poll.service" ]; then
  cp -p -- "$SYSTEMD_DIR/seiche-release-poll.service" \
    "$STAGE_DIR/previous-service"
  HAD_SERVICE=1
fi
if [ -e "$SYSTEMD_DIR/seiche-release-poll.timer" ]; then
  cp -p -- "$SYSTEMD_DIR/seiche-release-poll.timer" \
    "$STAGE_DIR/previous-timer"
  HAD_TIMER=1
fi

# Keep every rename on its destination filesystem.  From the first rename
# onward the EXIT trap restores all five prior files and the prior timer state
# on syntax, daemon-reload, activation, signal, or other failure.
WRAPPER_NEW=$(mktemp "$WRAPPER_DIR/.seiche-deploy-wrapper.XXXXXX")
install -m 0700 "$STAGE_DIR/seiche-deploy-wrapper.sh" "$WRAPPER_NEW"
"$SYNC" -f "$WRAPPER_NEW"
INSTALL_STARTED=1
mv -f -- "$WRAPPER_NEW" "$DEPLOY_WRAPPER"
WRAPPER_NEW=""

REMOTE_GATE_VERIFIER_NEW=$(mktemp "$WRAPPER_DIR/.seiche-remote-gate-verify.XXXXXX")
install -m 0700 "$STAGE_DIR/seiche-remote-gate-verify.py" \
  "$REMOTE_GATE_VERIFIER_NEW"
"$SYNC" -f "$REMOTE_GATE_VERIFIER_NEW"
mv -f -- "$REMOTE_GATE_VERIFIER_NEW" "$REMOTE_GATE_VERIFIER"
REMOTE_GATE_VERIFIER_NEW=""

SCRIPT_NEW=$(mktemp "$SCRIPT_DIR/.seiche-release-poll.XXXXXX")
install -m 0755 "$STAGE_DIR/seiche-release-poll" "$SCRIPT_NEW"
"$SYNC" -f "$SCRIPT_NEW"
mv -f -- "$SCRIPT_NEW" "$SCRIPT_DEST"
SCRIPT_NEW=""

# Verification happens only after the candidate ExecStart exists.  This avoids
# first-install false failures while remaining inside the rollback transaction.
"$SYSTEMD_ANALYZE" verify "$STAGE_DIR/seiche-release-poll.service" \
  "$STAGE_DIR/seiche-release-poll.timer"
mv -f -- "$STAGE_DIR/seiche-release-poll.service" \
  "$SYSTEMD_DIR/seiche-release-poll.service"
mv -f -- "$STAGE_DIR/seiche-release-poll.timer" \
  "$SYSTEMD_DIR/seiche-release-poll.timer"
"$SYNC" "$WRAPPER_DIR" "$SCRIPT_DIR" "$SYSTEMD_DIR"
"$SYSTEMCTL" daemon-reload

if [ "$ENABLE" = 1 ]; then
  "$SYSTEMCTL" enable --now seiche-release-poll.timer
else
  "$SYSTEMCTL" disable --now seiche-release-poll.timer
fi
INSTALL_COMMITTED=1

if [ "$ENABLE" = 1 ]; then
  echo "release poller installed and enabled"
else
  echo "release poller installed but DISABLED and inactive"
  echo "gate without deploying: SEICHE_CONTROL_GATE_ONLY=1 $SCRIPT_DEST"
  echo "after Actions deploy is disabled: SEICHE_ENABLE_RELEASE_POLLER=1 $0"
fi
