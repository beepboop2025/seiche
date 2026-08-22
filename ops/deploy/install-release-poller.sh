#!/usr/bin/env bash
# Install the direct main->Hetzner release controller.  Installation is inert by
# default: explicitly set SEICHE_ENABLE_RELEASE_POLLER=1 only after its shadow
# gate is green and the GitHub Actions deploy trigger has been disabled.
set -euo pipefail

APP_DIR="${SEICHE_APP_DIR:-/home/seiche/app}"
SYSTEMD_DIR="${SEICHE_SYSTEMD_DIR:-/etc/systemd/system}"
SCRIPT_DEST="${SEICHE_RELEASE_POLLER_DEST:-/usr/local/sbin/seiche-release-poll}"
DEPLOY_WRAPPER="${SEICHE_DEPLOY_WRAPPER:-/var/lib/seiche-deploy/bin/seiche-deploy-wrapper.sh}"
RUNTIME_DIR="${SEICHE_CONTROL_RUNTIME_DIR:-/run/seiche-control}"
CONTROL_LOCK="$RUNTIME_DIR/release.lock"
SYSTEMCTL="${SEICHE_SYSTEMCTL_BIN:-systemctl}"
SYSTEMD_ANALYZE="${SEICHE_SYSTEMD_ANALYZE_BIN:-systemd-analyze}"
SYNC="${SEICHE_SYNC_BIN:-/usr/bin/sync}"
FLOCK="${SEICHE_FLOCK_BIN:-flock}"
SYSTEM_PYTHON="${SEICHE_CONTROL_PYTHON:-python3}"
ENABLE="${SEICHE_ENABLE_RELEASE_POLLER:-0}"
SOURCE_DIR="$APP_DIR/ops/deploy"
SOURCE_WRAPPER="$SOURCE_DIR/seiche-deploy-wrapper.sh"
SOURCE_SIGNER="$SOURCE_DIR/release-allowed-signers"
ALLOWED_SIGNERS="${SEICHE_RELEASE_ALLOWED_SIGNERS_DEST:-/etc/seiche-release.allowed-signers}"
SIGNING_PRINCIPAL=beepboop2025@users.noreply.github.com
SCRIPT_DIR=$(dirname -- "$SCRIPT_DEST")
WRAPPER_DIR=$(dirname -- "$DEPLOY_WRAPPER")
STAGE_DIR=""
SCRIPT_NEW=""
WRAPPER_NEW=""
SIGNER_STAGE=""
INSTALL_STARTED=""
INSTALL_COMMITTED=""
WAS_ENABLED=""
WAS_ACTIVE=""
HAD_SCRIPT=""
HAD_WRAPPER=""
HAD_SERVICE=""
HAD_TIMER=""

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

remove_staging() {
  [ -z "$SCRIPT_NEW" ] || rm -f -- "$SCRIPT_NEW"
  [ -z "$WRAPPER_NEW" ] || rm -f -- "$WRAPPER_NEW"
  [ -z "$SIGNER_STAGE" ] || rm -f -- "$SIGNER_STAGE"
  if [ -n "$STAGE_DIR" ]; then
    rm -f -- \
      "$STAGE_DIR/seiche-release-poll" \
      "$STAGE_DIR/seiche-deploy-wrapper.sh" \
      "$STAGE_DIR/seiche-release-poll.service" \
      "$STAGE_DIR/seiche-release-poll.timer" \
      "$STAGE_DIR/previous-script" \
      "$STAGE_DIR/previous-wrapper" \
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
    echo "FAIL: release-controller install rollback was incomplete; inspect the four installed files and timer state" >&2
    return 1
  }
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [ -n "$INSTALL_STARTED" ] && [ -z "$INSTALL_COMMITTED" ]; then
    rollback_install || status=1
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
else
  EXPECTED_SIGNER_UID=$(id -u)
  EXPECTED_SIGNER_GID=$(id -g)
fi
case "$ENABLE" in
  0|1) ;;
  *) fail "SEICHE_ENABLE_RELEASE_POLLER must be exactly 0 or 1" ;;
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
"$SYSTEM_PYTHON" - "$WRAPPER_DIR" "$EXPECTED_SIGNER_UID" \
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
    release-allowed-signers; do
  [ -f "$SOURCE_DIR/$source" ] && [ ! -L "$SOURCE_DIR/$source" ] \
    || fail "missing or unsafe release-poller source: $SOURCE_DIR/$source"
done
for destination in \
    "$DEPLOY_WRAPPER" \
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
# The dollar expression is deliberately matched literally in the reviewed source.
# shellcheck disable=SC2016
grep -Fq 'EXPECTED_TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}' "$SOURCE_WRAPPER" \
  || fail "reviewed deploy wrapper lacks the expected-target-SHA safety pin"

# Validate the reviewed repository copy without following symlinks or accepting
# comments/options/multiple principals. The resulting canonical line is the
# one durable host trust anchor this installer may create or confirm.
EXPECTED_SIGNER=$("$SYSTEM_PYTHON" - "$SOURCE_SIGNER" "$SIGNING_PRINCIPAL" <<'PY'
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
  "$SYSTEM_PYTHON" - "$ALLOWED_SIGNERS" "$EXPECTED_SIGNER" \
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
"$SYSTEM_PYTHON" - "$SIGNER_DIR" "$EXPECTED_SIGNER_UID" \
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
# onward the EXIT trap restores all four prior files and the prior timer state
# on syntax, daemon-reload, activation, signal, or other failure.
WRAPPER_NEW=$(mktemp "$WRAPPER_DIR/.seiche-deploy-wrapper.XXXXXX")
install -m 0700 "$STAGE_DIR/seiche-deploy-wrapper.sh" "$WRAPPER_NEW"
"$SYNC" -f "$WRAPPER_NEW"
INSTALL_STARTED=1
mv -f -- "$WRAPPER_NEW" "$DEPLOY_WRAPPER"
WRAPPER_NEW=""

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
