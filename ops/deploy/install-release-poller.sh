#!/usr/bin/env bash
# Install the direct main->Hetzner release controller.  Installation is inert by
# default: explicitly set SEICHE_ENABLE_RELEASE_POLLER=1 only after its shadow
# gate is green and the GitHub Actions deploy trigger has been disabled.
set -euo pipefail

APP_DIR="${SEICHE_APP_DIR:-/home/seiche/app}"
SYSTEMD_DIR="${SEICHE_SYSTEMD_DIR:-/etc/systemd/system}"
SCRIPT_DEST="${SEICHE_RELEASE_POLLER_DEST:-/usr/local/sbin/seiche-release-poll}"
DEPLOY_WRAPPER="${SEICHE_DEPLOY_WRAPPER:-/root/seiche-deploy-wrapper.sh}"
RUNTIME_DIR="${SEICHE_CONTROL_RUNTIME_DIR:-/run/seiche-control}"
CONTROL_LOCK="$RUNTIME_DIR/release.lock"
SYSTEMCTL="${SEICHE_SYSTEMCTL_BIN:-systemctl}"
SYSTEMD_ANALYZE="${SEICHE_SYSTEMD_ANALYZE_BIN:-systemd-analyze}"
SYNC="${SEICHE_SYNC_BIN:-/usr/bin/sync}"
FLOCK="${SEICHE_FLOCK_BIN:-flock}"
ENABLE="${SEICHE_ENABLE_RELEASE_POLLER:-0}"
SOURCE_DIR="$APP_DIR/ops/deploy"
SCRIPT_DIR=$(dirname -- "$SCRIPT_DEST")
STAGE_DIR=""
SCRIPT_NEW=""
INSTALL_STARTED=""
INSTALL_COMMITTED=""
WAS_ENABLED=""
WAS_ACTIVE=""
HAD_SCRIPT=""
HAD_SERVICE=""
HAD_TIMER=""

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

remove_staging() {
  [ -z "$SCRIPT_NEW" ] || rm -f -- "$SCRIPT_NEW"
  if [ -n "$STAGE_DIR" ]; then
    rm -f -- \
      "$STAGE_DIR/seiche-release-poll" \
      "$STAGE_DIR/seiche-release-poll.service" \
      "$STAGE_DIR/seiche-release-poll.timer" \
      "$STAGE_DIR/previous-script" \
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
  echo "install: restoring the previous release-poller files and timer state" >&2
  restore_file "$SYSTEMD_DIR/seiche-release-poll.timer" \
    "$STAGE_DIR/previous-timer" "$HAD_TIMER" || failed=1
  restore_file "$SYSTEMD_DIR/seiche-release-poll.service" \
    "$STAGE_DIR/previous-service" "$HAD_SERVICE" || failed=1
  restore_file "$SCRIPT_DEST" "$STAGE_DIR/previous-script" "$HAD_SCRIPT" \
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
    echo "FAIL: release-poller install rollback was incomplete; inspect the three installed files and timer state" >&2
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
[ -x "$DEPLOY_WRAPPER" ] \
  || fail "the rollback-owning deploy wrapper is missing: $DEPLOY_WRAPPER"
# The dollar expression is deliberately matched literally in the installed file.
# shellcheck disable=SC2016
grep -Fq 'EXPECTED_TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}' "$DEPLOY_WRAPPER" \
  || fail "installed deploy wrapper lacks the expected-target-SHA safety pin"
for source in seiche-release-poll.sh seiche-release-poll.service seiche-release-poll.timer; do
  [ -f "$SOURCE_DIR/$source" ] && [ ! -L "$SOURCE_DIR/$source" ] \
    || fail "missing or unsafe release-poller source: $SOURCE_DIR/$source"
done
for destination in \
    "$SCRIPT_DEST" \
    "$SYSTEMD_DIR/seiche-release-poll.service" \
    "$SYSTEMD_DIR/seiche-release-poll.timer"; do
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    [ -f "$destination" ] && [ ! -L "$destination" ] \
      || fail "unsafe installed release-poller path: $destination"
  fi
done
bash -n "$SOURCE_DIR/seiche-release-poll.sh"

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
install -m 0755 "$SOURCE_DIR/seiche-release-poll.sh" \
  "$STAGE_DIR/seiche-release-poll"
install -m 0644 "$SOURCE_DIR/seiche-release-poll.service" \
  "$STAGE_DIR/seiche-release-poll.service"
install -m 0644 "$SOURCE_DIR/seiche-release-poll.timer" \
  "$STAGE_DIR/seiche-release-poll.timer"
bash -n "$STAGE_DIR/seiche-release-poll"

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
# onward the EXIT trap restores all three prior files and the prior timer state
# on syntax, daemon-reload, activation, signal, or other failure.
SCRIPT_NEW=$(mktemp "$SCRIPT_DIR/.seiche-release-poll.XXXXXX")
install -m 0755 "$STAGE_DIR/seiche-release-poll" "$SCRIPT_NEW"
"$SYNC" -f "$SCRIPT_NEW"
INSTALL_STARTED=1
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
"$SYNC" "$SCRIPT_DIR" "$SYSTEMD_DIR"
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
