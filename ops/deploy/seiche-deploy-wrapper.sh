#!/bin/bash
# Forced-command target for the GitHub Actions deploy key. The key in
# authorized_keys can run THIS script and nothing else (no pty, no forwarding).
# update.sh pulls main, pip-installs and runs the smoke gate with rollback;
# only a green tree gets restarted. After that restart is healthy, the same
# green checkout's Caddyfile is validated, backed up, installed and reloaded.
#
# Mirrored in the repo at ops/deploy/seiche-deploy-wrapper.sh. Edit the REPO
# copy: after update.sh pulls, this script installs the post-pull checkout's
# versions of itself and update.sh, so the box copies no longer drift from
# the repo until a human remembers to copy them. New copies take effect next
# deploy; a failed sync turns the run red at the end without blocking today's
# deploy.
set -euo pipefail
echo "== seiche auto-deploy $(date -u +%FT%TZ) =="

APP=/home/seiche/app
DEPLOY_STATE_DIR=/var/lib/seiche-deploy
STATE=$DEPLOY_STATE_DIR/deployed-sha
RELEASE_ENV=/etc/seiche/release.env
PROMOTION_REQUEST_DIR=/run/seiche-release
PROMOTION_REQUEST=$PROMOTION_REQUEST_DIR/promotion-request.json
PROMOTION_UNIT=seiche-snapshot-promote.service
DEPLOY_RUNTIME_DIR=/run/seiche-deploy
DEPLOY_LOCK=$DEPLOY_RUNTIME_DIR/deploy.lock

if [ -L "$DEPLOY_RUNTIME_DIR" ] \
    || { [ -e "$DEPLOY_RUNTIME_DIR" ] && [ ! -d "$DEPLOY_RUNTIME_DIR" ]; }; then
  echo "FAIL: deploy runtime directory is not a real directory"
  exit 1
fi
install -d -o root -g root -m 0700 "$DEPLOY_RUNTIME_DIR"
if [ "$(stat -c '%U:%G:%a' "$DEPLOY_RUNTIME_DIR")" != "root:root:700" ]; then
  echo "FAIL: deploy runtime directory permissions are unsafe"
  exit 1
fi
exec 9>"$DEPLOY_LOCK"
chown root:root "$DEPLOY_LOCK"
chmod 0600 "$DEPLOY_LOCK"
if ! flock --nonblock 9; then
  echo "FAIL: another seiche deployment is still running"
  exit 1
fi

valid_release_sha() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]]
}

valid_activation_token() {
  [[ "$1" =~ ^[0-9a-f]{64}$ ]]
}

# Snapshot assembly needs several CPU cores before strict health can turn green.
# Require a stable quiet window before any service is stopped; one low sample is
# not enough on this shared host because owner-controlled workloads start in
# phases. The fixed 75 percent ceiling leaves four cores free on the 16-core
# production host without trusting caller-controlled configuration.
admit_shared_host() {
  local cpu_count ceiling sample load_one load_five
  if ! cpu_count=$(/usr/bin/getconf _NPROCESSORS_ONLN 2>/dev/null) \
      || [[ ! "$cpu_count" =~ ^[0-9]+$ ]] \
      || (( cpu_count < 1 || cpu_count > 4096 )); then
    echo "DEFER: shared-host CPU capacity is unreadable; production unchanged"
    return 1
  fi
  if ! ceiling=$(
    /usr/bin/awk -v cpus="$cpu_count" \
      'BEGIN { printf "%.2f", cpus * 0.75 }'
  ) || [[ ! "$ceiling" =~ ^[0-9]+[.][0-9]{2}$ ]]; then
    echo "DEFER: shared-host load ceiling is invalid; production unchanged"
    return 1
  fi
  for (( sample = 1; sample <= 3; sample++ )); do
    if ! IFS=' ' read -r load_one load_five _ </proc/loadavg \
        || [[ ! "$load_one" =~ ^[0-9]+([.][0-9]+)?$ ]] \
        || [[ ! "$load_five" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
      echo "DEFER: shared-host load is unreadable; production unchanged"
      return 1
    fi
    if ! LC_ALL=C /usr/bin/awk -v observed="$load_one" -v limit="$ceiling" \
        'BEGIN { exit !(observed <= limit) }'; then
      printf 'DEFER: shared-host one-minute load %s exceeds %s; production unchanged\n' \
        "$load_one" "$ceiling"
      return 1
    fi
    if ! LC_ALL=C /usr/bin/awk -v observed="$load_five" -v limit="$ceiling" \
        'BEGIN { exit !(observed <= limit) }'; then
      printf 'DEFER: shared-host five-minute load %s exceeds %s; production unchanged\n' \
        "$load_five" "$ceiling"
      return 1
    fi
    if (( sample < 3 )); then
      sleep 10 || return 1
    fi
  done
  printf 'shared-host admission: three quiet one/five-minute samples at or below %s\n' \
    "$ceiling"
}

case "${SEICHE_DEPLOY_ADMISSION_ONLY:-0}" in
  0) ;;
  1)
    [ -z "${SSH_ORIGINAL_COMMAND:-}" ] \
      || { echo "FAIL: forced deploy cannot request admission-only mode"; exit 1; }
    if admit_shared_host; then
      exit 0
    fi
    exit 75
    ;;
  *)
    echo "FAIL: SEICHE_DEPLOY_ADMISSION_ONLY must be exactly 0 or 1"
    exit 1
    ;;
esac

DEPLOYED_STATE_RENAMED=""
write_deployed_state() {
  local release_sha="$1" stage=""
  DEPLOYED_STATE_RENAMED=""
  if ! valid_release_sha "$release_sha"; then
    echo "FAIL: refusing to record a non-canonical deployed SHA"
    return 1
  fi
  stage=$(mktemp "$DEPLOY_STATE_DIR/.deployed-sha.XXXXXX") || return 1
  if ! printf '%s\n' "$release_sha" >"$stage" \
      || ! chown root:root "$stage" \
      || ! chmod 0600 "$stage" \
      || ! /usr/bin/sync -f "$stage"; then
    rm -f -- "$stage"
    echo "FAIL: could not atomically record the deployed release"
    return 1
  fi
  if ! mv -f "$stage" "$STATE"; then
    rm -f -- "$stage"
    echo "FAIL: could not atomically record the deployed release"
    return 1
  fi
  # The visible state now names the candidate. Even if its directory flush
  # fails, rolling old code back would contradict the state we just installed.
  DEPLOYED_STATE_RENAMED=1
  if ! /usr/bin/sync "$DEPLOY_STATE_DIR"; then
    echo "FAIL: could not durably record the deployed release"
    return 1
  fi
}

write_release_env() {
  local release_sha="$1" stage=""
  if ! valid_release_sha "$release_sha"; then
    echo "FAIL: refusing to install a non-canonical release SHA"
    return 1
  fi
  if [ ! -d /etc/seiche ] || [ -L /etc/seiche ]; then
    echo "FAIL: /etc/seiche is not a safe release environment directory"
    return 1
  fi
  stage=$(mktemp /etc/seiche/.release.env.XXXXXX) || return 1
  if ! printf 'SEICHE_RELEASE_SHA=%s\n' "$release_sha" >"$stage" \
      || ! chown root:seiche "$stage" \
      || ! chmod 0640 "$stage" \
      || ! mv -f "$stage" "$RELEASE_ENV"; then
    rm -f -- "$stage"
    echo "FAIL: could not atomically install the release environment"
    return 1
  fi
}

write_promotion_request() {
  local expected_sha="$1" activation_token="$2" stage=""
  if ! valid_release_sha "$expected_sha" \
      || ! valid_activation_token "$activation_token"; then
    echo "FAIL: refusing to write an invalid snapshot promotion request"
    return 1
  fi
  if [ ! -d "$PROMOTION_REQUEST_DIR" ] \
      || [ -L "$PROMOTION_REQUEST_DIR" ] \
      || [ "$(stat -c '%U:%G:%a' "$PROMOTION_REQUEST_DIR")" != "root:seiche:750" ]; then
    echo "FAIL: snapshot promotion request directory is unsafe"
    return 1
  fi
  stage=$(mktemp "$PROMOTION_REQUEST_DIR/.promotion-request.json.XXXXXX") \
    || return 1
  if ! printf '{"expected_sha":"%s","activation_token":"%s"}\n' \
      "$expected_sha" "$activation_token" >"$stage" \
      || ! chown root:seiche "$stage" \
      || ! chmod 0640 "$stage" \
      || ! mv -f "$stage" "$PROMOTION_REQUEST"; then
    rm -f -- "$stage"
    echo "FAIL: could not atomically install the snapshot promotion request"
    return 1
  fi
}

# The sha whose code is actually RUNNING, written only after a healthy
# restart. HEAD alone cannot answer that: a deploy killed between pull and
# restart leaves HEAD==origin/main with the old process still serving, and
# the old sha-compare then said "nothing to deploy" forever — even
# workflow_dispatch could not recover the box (2026-07-28). A missing file
# means unknown, and unknown means deploy.
if [ -L "$DEPLOY_STATE_DIR" ] \
    || { [ -e "$DEPLOY_STATE_DIR" ] && [ ! -d "$DEPLOY_STATE_DIR" ]; }; then
  echo "FAIL: deploy state directory is not a real directory"
  exit 1
fi
install -d -o root -g root -m 0700 "$DEPLOY_STATE_DIR"
if [ "$(stat -c '%U:%G:%a' "$DEPLOY_STATE_DIR")" != "root:root:700" ]; then
  echo "FAIL: deploy state directory permissions are unsafe"
  exit 1
fi
DEPLOYED=""
if [ -e "$STATE" ] || [ -L "$STATE" ]; then
  if [ -L "$STATE" ] || [ ! -f "$STATE" ] \
      || [ "$(stat -c '%U:%G:%a' "$STATE")" != "root:root:600" ] \
      || ! IFS= read -r DEPLOYED <"$STATE" \
      || ! valid_release_sha "$DEPLOYED"; then
    echo "FAIL: deployed release state is unsafe or invalid"
    exit 1
  fi
fi

BEFORE=$(runuser -u seiche -- git -C "$APP" rev-parse HEAD)
if ! runuser -u seiche -- git -C "$APP" fetch -q origin main; then
  echo "FAIL: could not fetch the candidate release"
  exit 1
fi
LATEST=$(runuser -u seiche -- git -C "$APP" rev-parse origin/main)
if ! valid_release_sha "$LATEST" \
    || ! runuser -u seiche -- git -C "$APP" rev-parse --verify --quiet \
      "$LATEST^{commit}" >/dev/null; then
  echo "FAIL: origin/main did not resolve to a canonical local commit"
  exit 1
fi
# A local controller or the forced-command SSH request passes one reviewed
# identity here. Never let the wrapper silently replace it with a newer main
# tip. A direct root invocation without either constraint retains the explicit
# latest-main maintenance behavior.
EXPECTED_TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}
if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
  if [[ "$SSH_ORIGINAL_COMMAND" =~ ^deploy\ ([0-9a-f]{40})$ ]]; then
    REQUESTED_TARGET=${BASH_REMATCH[1]}
  else
    echo "FAIL: forced deployment command must be deploy plus one commit SHA"
    exit 1
  fi
  if [ -n "$EXPECTED_TARGET" ] && [ "$EXPECTED_TARGET" != "$REQUESTED_TARGET" ]; then
    echo "FAIL: environment and forced-command deployment targets disagree"
    exit 1
  fi
  EXPECTED_TARGET=$REQUESTED_TARGET
fi
TARGET=$LATEST
if [ -n "$EXPECTED_TARGET" ]; then
  if ! valid_release_sha "$EXPECTED_TARGET"; then
    echo "FAIL: expected target is not a canonical commit SHA"
    exit 1
  fi
  if ! runuser -u seiche -- git -C "$APP" rev-parse --verify --quiet \
      "$EXPECTED_TARGET^{commit}" >/dev/null \
      || ! runuser -u seiche -- git -C "$APP" merge-base --is-ancestor \
        "$EXPECTED_TARGET" "$LATEST"; then
    echo "FAIL: reviewed target is not a fetched commit on main"
    exit 1
  fi
  TARGET=$EXPECTED_TARGET
fi
if ! admit_shared_host; then
  exit 75
fi
MARKET_WORKER_WAS_ACTIVE=""
MARKET_WORKER_WAS_ENABLED=""
MARKET_BACKFILL_WAS_ACTIVE=""
SOURCE_WORKER_WAS_ACTIVE=""
SOURCE_WORKER_WAS_ENABLED=""
READINESS_TIMER_WAS_ACTIVE=""
READINESS_TIMER_WAS_ENABLED=""
VALIDATION_TIMER_WAS_ACTIVE=""
VALIDATION_TIMER_WAS_ENABLED=""
BACKUP_TIMER_WAS_ACTIVE=""
BACKUP_TIMER_WAS_ENABLED=""
RESTORE_TIMER_WAS_ACTIVE=""
RESTORE_TIMER_WAS_ENABLED=""
OFFSITE_TIMER_WAS_ACTIVE=""
OFFSITE_TIMER_WAS_ENABLED=""
if systemctl is-active --quiet seiche-market-worker.service 2>/dev/null; then
  MARKET_WORKER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet seiche-market-worker.service 2>/dev/null; then
  MARKET_WORKER_WAS_ENABLED=1
fi
if systemctl is-active --quiet seiche-market-backfill.service 2>/dev/null; then
  MARKET_BACKFILL_WAS_ACTIVE=1
fi
if systemctl is-active --quiet seiche-source-worker.service 2>/dev/null; then
  SOURCE_WORKER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet seiche-source-worker.service 2>/dev/null; then
  SOURCE_WORKER_WAS_ENABLED=1
fi
if systemctl is-active --quiet seiche-data-readiness.timer 2>/dev/null; then
  READINESS_TIMER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet seiche-data-readiness.timer 2>/dev/null; then
  READINESS_TIMER_WAS_ENABLED=1
fi
if systemctl is-active --quiet seiche-market-validation.timer 2>/dev/null; then
  VALIDATION_TIMER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet seiche-market-validation.timer 2>/dev/null; then
  VALIDATION_TIMER_WAS_ENABLED=1
fi
if systemctl is-active --quiet seiche-market-backup.timer 2>/dev/null; then
  BACKUP_TIMER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet seiche-market-backup.timer 2>/dev/null; then
  BACKUP_TIMER_WAS_ENABLED=1
fi
if systemctl is-active --quiet seiche-market-restore-check.timer 2>/dev/null; then
  RESTORE_TIMER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet seiche-market-restore-check.timer 2>/dev/null; then
  RESTORE_TIMER_WAS_ENABLED=1
fi
if systemctl is-active --quiet seiche-market-offsite-backup.timer 2>/dev/null; then
  OFFSITE_TIMER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet seiche-market-offsite-backup.timer 2>/dev/null; then
  OFFSITE_TIMER_WAS_ENABLED=1
fi

# Capture every market-platform unit and generated storage-policy artifact this
# deploy can replace. A rollback must restore exact host bytes (or exact prior
# absence); resetting the application checkout alone cannot reproduce drop-ins
# or root-controlled helpers. The /run copy lives only for this locked deploy.
DATA_UNIT_NAMES=(
  seiche-storage-preflight.service
  seiche-market-backfill.service
  seiche-source-worker.service
  seiche-data-readiness.service
  seiche-data-readiness.timer
  seiche-market-validation.service
  seiche-market-validation.timer
  seiche-market-backup.service
  seiche-market-backup.timer
  seiche-market-offsite-backup.service
  seiche-market-offsite-backup.timer
  seiche-market-restore-check.service
  seiche-market-restore-check.timer
  seiche-snapshot-promote.service
)
DATA_ARTIFACT_NAMES=(
  storage-preflight-helper
  data-readiness-helper
  market-offsite-backup-helper
  api-market-platform-dropin
  release-poll-storage-dropin
  validation-state-dropin
  backup-paths-dropin
  restore-paths-dropin
)
DATA_ARTIFACT_PATHS=(
  /etc/seiche/libexec/seiche-storage-preflight.py
  /etc/seiche/libexec/seiche-data-readiness.sh
  /etc/seiche/libexec/seiche-market-offsite-backup.sh
  /etc/systemd/system/seiche-api.service.d/market-platform.conf
  /etc/systemd/system/seiche-release-poll.service.d/storage-volume.conf
  /etc/systemd/system/seiche-market-validation.service.d/state-path.conf
  /etc/systemd/system/seiche-market-backup.service.d/paths.conf
  /etc/systemd/system/seiche-market-restore-check.service.d/paths.conf
)
DATA_UNIT_ROLLBACK_DIR=""
DATA_UNITS_MAY_HAVE_CHANGED=""
cleanup_preupdate_data_units() {
  local artifact index unit
  [ -n "$DATA_UNIT_ROLLBACK_DIR" ] || return 0
  case "$DATA_UNIT_ROLLBACK_DIR" in
    "$DEPLOY_RUNTIME_DIR"/.data-units.*) ;;
    *)
      echo "FAIL: refusing to clean an unsafe data-unit rollback path" >&2
      return 1
      ;;
  esac
  for unit in "${DATA_UNIT_NAMES[@]}"; do
    rm -f -- "$DATA_UNIT_ROLLBACK_DIR/$unit.present" \
      "$DATA_UNIT_ROLLBACK_DIR/$unit.absent"
  done
  for index in "${!DATA_ARTIFACT_NAMES[@]}"; do
    artifact=${DATA_ARTIFACT_NAMES[$index]}
    rm -f -- "$DATA_UNIT_ROLLBACK_DIR/$artifact.present" \
      "$DATA_UNIT_ROLLBACK_DIR/$artifact.absent"
  done
  rmdir "$DATA_UNIT_ROLLBACK_DIR" 2>/dev/null || return 1
  DATA_UNIT_ROLLBACK_DIR=""
}
capture_preupdate_data_units() {
  local artifact destination index unit
  DATA_UNIT_ROLLBACK_DIR=$(mktemp -d "$DEPLOY_RUNTIME_DIR/.data-units.XXXXXX") \
    || return 1
  if ! chown root:root "$DATA_UNIT_ROLLBACK_DIR" \
      || ! chmod 0700 "$DATA_UNIT_ROLLBACK_DIR"; then
    cleanup_preupdate_data_units || true
    return 1
  fi
  for unit in "${DATA_UNIT_NAMES[@]}"; do
    destination="/etc/systemd/system/$unit"
    if [ -e "$destination" ] || [ -L "$destination" ]; then
      if [ -L "$destination" ] || [ ! -f "$destination" ] \
          || ! cp -p -- "$destination" \
            "$DATA_UNIT_ROLLBACK_DIR/$unit.present"; then
        cleanup_preupdate_data_units || true
        echo "FAIL: pre-deploy data-unit state is unsafe or unreadable"
        return 1
      fi
    elif ! install -m 0600 /dev/null \
        "$DATA_UNIT_ROLLBACK_DIR/$unit.absent"; then
      cleanup_preupdate_data_units || true
      return 1
    fi
  done
  if [ "${#DATA_ARTIFACT_NAMES[@]}" -ne "${#DATA_ARTIFACT_PATHS[@]}" ]; then
    cleanup_preupdate_data_units || true
    echo "FAIL: data artifact rollback manifest is inconsistent"
    return 1
  fi
  for index in "${!DATA_ARTIFACT_NAMES[@]}"; do
    artifact=${DATA_ARTIFACT_NAMES[$index]}
    destination=${DATA_ARTIFACT_PATHS[$index]}
    if [ -e "$destination" ] || [ -L "$destination" ]; then
      if [ -L "$destination" ] || [ ! -f "$destination" ] \
          || ! cp -p -- "$destination" \
            "$DATA_UNIT_ROLLBACK_DIR/$artifact.present"; then
        cleanup_preupdate_data_units || true
        echo "FAIL: pre-deploy data artifact is unsafe or unreadable"
        return 1
      fi
    elif ! install -m 0600 /dev/null \
        "$DATA_UNIT_ROLLBACK_DIR/$artifact.absent"; then
      cleanup_preupdate_data_units || true
      return 1
    fi
  done
}
cleanup_data_unit_restore_stage() {
  local stage="$1" unit
  case "$stage" in
    /etc/systemd/system/.seiche-data-units-restore.*) ;;
    *) return 1 ;;
  esac
  for unit in "${DATA_UNIT_NAMES[@]}"; do
    rm -f -- "$stage/$unit"
  done
  rmdir "$stage" 2>/dev/null || true
}
restore_preupdate_data_units() {
  local artifact artifact_stage captured destination index stage unit
  local -a candidates=()
  [ -n "$DATA_UNITS_MAY_HAVE_CHANGED" ] || return 0
  case "$DATA_UNIT_ROLLBACK_DIR" in
    "$DEPLOY_RUNTIME_DIR"/.data-units.*) ;;
    *)
      echo "FAIL: no safe pre-deploy data-unit snapshot is available"
      return 1
      ;;
  esac
  # A failed installer may already have enabled persistent candidate timers.
  # Quiesce every reader/writer before restoring files or generated drop-ins.
  systemctl stop \
    seiche-data-readiness.timer seiche-data-readiness.service \
    seiche-market-validation.timer seiche-market-validation.service \
    seiche-market-backup.timer seiche-market-backup.service \
    seiche-market-offsite-backup.timer seiche-market-offsite-backup.service \
    seiche-market-restore-check.timer seiche-market-restore-check.service \
    2>/dev/null || true
  stage=$(mktemp -d /etc/systemd/system/.seiche-data-units-restore.XXXXXX) \
    || return 1
  chmod 0700 "$stage" || {
    cleanup_data_unit_restore_stage "$stage"
    return 1
  }
  for unit in "${DATA_UNIT_NAMES[@]}"; do
    captured="$DATA_UNIT_ROLLBACK_DIR/$unit.present"
    if [ -f "$captured" ] && [ ! -L "$captured" ] \
        && [ ! -e "$DATA_UNIT_ROLLBACK_DIR/$unit.absent" ]; then
      if ! cp -p -- "$captured" "$stage/$unit"; then
        cleanup_data_unit_restore_stage "$stage"
        return 1
      fi
      candidates+=("$stage/$unit")
    elif [ -f "$DATA_UNIT_ROLLBACK_DIR/$unit.absent" ] \
        && [ ! -L "$DATA_UNIT_ROLLBACK_DIR/$unit.absent" ] \
        && [ ! -e "$captured" ]; then
      :
    else
      cleanup_data_unit_restore_stage "$stage"
      echo "FAIL: pre-deploy data-unit snapshot is incomplete"
      return 1
    fi
  done
  for index in "${!DATA_ARTIFACT_NAMES[@]}"; do
    artifact=${DATA_ARTIFACT_NAMES[$index]}
    captured="$DATA_UNIT_ROLLBACK_DIR/$artifact.present"
    if [ -f "$captured" ] && [ ! -L "$captured" ] \
        && [ ! -e "$DATA_UNIT_ROLLBACK_DIR/$artifact.absent" ]; then
      :
    elif [ -f "$DATA_UNIT_ROLLBACK_DIR/$artifact.absent" ] \
        && [ ! -L "$DATA_UNIT_ROLLBACK_DIR/$artifact.absent" ] \
        && [ ! -e "$captured" ]; then
      :
    else
      cleanup_data_unit_restore_stage "$stage"
      echo "FAIL: pre-deploy data artifact snapshot is incomplete"
      return 1
    fi
  done
  if (( ${#candidates[@]} > 0 )) \
      && ! systemd-analyze verify "${candidates[@]}"; then
    cleanup_data_unit_restore_stage "$stage"
    echo "FAIL: pre-deploy data units no longer pass systemd verification"
    return 1
  fi

  # Remove any enablement created by the candidate while its unit is still
  # visible. A missing unit can make disable return nonzero, so the final
  # is-enabled check below is the authoritative fail-closed assertion.
  if [ -z "$SOURCE_WORKER_WAS_ENABLED" ]; then
    systemctl disable seiche-source-worker.service >/dev/null 2>&1 || true
  fi
  if [ -z "$READINESS_TIMER_WAS_ENABLED" ]; then
    systemctl disable seiche-data-readiness.timer >/dev/null 2>&1 || true
  fi
  if [ -z "$VALIDATION_TIMER_WAS_ENABLED" ]; then
    systemctl disable seiche-market-validation.timer >/dev/null 2>&1 || true
  fi
  if [ -z "$BACKUP_TIMER_WAS_ENABLED" ]; then
    systemctl disable seiche-market-backup.timer >/dev/null 2>&1 || true
  fi
  if [ -z "$RESTORE_TIMER_WAS_ENABLED" ]; then
    systemctl disable seiche-market-restore-check.timer >/dev/null 2>&1 || true
  fi
  if [ -z "$OFFSITE_TIMER_WAS_ENABLED" ]; then
    systemctl disable seiche-market-offsite-backup.timer >/dev/null 2>&1 || true
  fi
  for index in "${!DATA_ARTIFACT_NAMES[@]}"; do
    artifact=${DATA_ARTIFACT_NAMES[$index]}
    destination=${DATA_ARTIFACT_PATHS[$index]}
    captured="$DATA_UNIT_ROLLBACK_DIR/$artifact.present"
    if [ -f "$captured" ]; then
      artifact_stage=$(mktemp \
        "$(dirname "$destination")/.seiche-data-artifact-restore.XXXXXX") \
        || {
          cleanup_data_unit_restore_stage "$stage"
          return 1
        }
      if ! cp -p -- "$captured" "$artifact_stage" \
          || ! mv -f -- "$artifact_stage" "$destination"; then
        rm -f -- "$artifact_stage"
        cleanup_data_unit_restore_stage "$stage"
        echo "FAIL: pre-deploy data artifacts could not be restored"
        return 1
      fi
    elif ! rm -f -- "$destination"; then
      cleanup_data_unit_restore_stage "$stage"
      echo "FAIL: candidate-only data artifacts could not be removed"
      return 1
    fi
  done
  for unit in "${DATA_UNIT_NAMES[@]}"; do
    destination="/etc/systemd/system/$unit"
    if [ -f "$DATA_UNIT_ROLLBACK_DIR/$unit.present" ]; then
      if ! mv -f "$stage/$unit" "$destination"; then
        cleanup_data_unit_restore_stage "$stage"
        echo "FAIL: pre-deploy data-unit files could not be restored"
        return 1
      fi
    elif ! rm -f -- "$destination"; then
      cleanup_data_unit_restore_stage "$stage"
      echo "FAIL: candidate-only data-unit files could not be removed"
      return 1
    fi
  done
  cleanup_data_unit_restore_stage "$stage"
  if ! systemctl daemon-reload; then
    echo "FAIL: systemd rejected restored pre-deploy data units"
    return 1
  fi
  if [ -n "$SOURCE_WORKER_WAS_ENABLED" ]; then
    if ! systemctl enable seiche-source-worker.service >/dev/null \
        || ! systemctl is-enabled --quiet seiche-source-worker.service; then
      echo "FAIL: source worker enablement could not be restored"
      return 1
    fi
  else
    systemctl disable seiche-source-worker.service >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-source-worker.service 2>/dev/null; then
      echo "FAIL: candidate source worker remains enabled after rollback"
      return 1
    fi
  fi
  if [ -n "$READINESS_TIMER_WAS_ENABLED" ]; then
    if ! systemctl enable seiche-data-readiness.timer >/dev/null \
        || ! systemctl is-enabled --quiet seiche-data-readiness.timer; then
      echo "FAIL: readiness timer enablement could not be restored"
      return 1
    fi
  else
    systemctl disable seiche-data-readiness.timer >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-data-readiness.timer 2>/dev/null; then
      echo "FAIL: candidate readiness timer remains enabled after rollback"
      return 1
    fi
  fi
  if [ -n "$VALIDATION_TIMER_WAS_ENABLED" ]; then
    if ! systemctl enable seiche-market-validation.timer >/dev/null \
        || ! systemctl is-enabled --quiet seiche-market-validation.timer; then
      echo "FAIL: validation timer enablement could not be restored"
      return 1
    fi
  else
    systemctl disable seiche-market-validation.timer >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-market-validation.timer 2>/dev/null; then
      echo "FAIL: candidate validation timer remains enabled after rollback"
      return 1
    fi
  fi
  if [ -n "$BACKUP_TIMER_WAS_ENABLED" ]; then
    if ! systemctl enable seiche-market-backup.timer >/dev/null \
        || ! systemctl is-enabled --quiet seiche-market-backup.timer; then
      echo "FAIL: backup timer enablement could not be restored"
      return 1
    fi
  else
    systemctl disable seiche-market-backup.timer >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-market-backup.timer 2>/dev/null; then
      echo "FAIL: candidate backup timer remains enabled after rollback"
      return 1
    fi
  fi
  if [ -n "$RESTORE_TIMER_WAS_ENABLED" ]; then
    if ! systemctl enable seiche-market-restore-check.timer >/dev/null \
        || ! systemctl is-enabled --quiet seiche-market-restore-check.timer; then
      echo "FAIL: restore-check timer enablement could not be restored"
      return 1
    fi
  else
    systemctl disable seiche-market-restore-check.timer >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-market-restore-check.timer \
        2>/dev/null; then
      echo "FAIL: candidate restore-check timer remains enabled after rollback"
      return 1
    fi
  fi
  if [ -n "$OFFSITE_TIMER_WAS_ENABLED" ]; then
    if ! systemctl enable seiche-market-offsite-backup.timer >/dev/null \
        || ! systemctl is-enabled --quiet \
          seiche-market-offsite-backup.timer; then
      echo "FAIL: offsite backup timer enablement could not be restored"
      return 1
    fi
  else
    systemctl disable seiche-market-offsite-backup.timer \
      >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-market-offsite-backup.timer \
        2>/dev/null; then
      echo "FAIL: candidate offsite backup timer remains enabled after rollback"
      return 1
    fi
  fi
  DATA_UNITS_MAY_HAVE_CHANGED=""
  echo "data collection and readiness units restored to pre-deploy state"
}

trap 'cleanup_preupdate_data_units || true' EXIT
if ! capture_preupdate_data_units; then
  echo "FAIL: could not capture pre-deploy data-unit state"
  exit 1
fi
restore_market_services() {
  [ -z "$MARKET_BACKFILL_WAS_ACTIVE" ] \
    || systemctl start --no-block seiche-market-backfill.service 2>/dev/null \
    || true
  [ -z "$MARKET_WORKER_WAS_ACTIVE" ] \
    || systemctl start --no-block seiche-market-worker.service 2>/dev/null \
    || true
  if [ -n "$SOURCE_WORKER_WAS_ACTIVE" ]; then
    if ! systemctl start seiche-source-worker.service 2>/dev/null; then
      echo "FAIL: source worker did not become ready; readiness timer remains stopped"
      return 0
    fi
  fi
  if [ -n "$READINESS_TIMER_WAS_ACTIVE" ]; then
    if [ -z "$SOURCE_WORKER_WAS_ACTIVE" ]; then
      echo "FAIL: readiness timer remains stopped because source readiness is unknown"
    else
      systemctl start --no-block seiche-data-readiness.timer 2>/dev/null || true
    fi
  fi
  [ -z "$VALIDATION_TIMER_WAS_ACTIVE" ] \
    || systemctl start --no-block seiche-market-validation.timer 2>/dev/null \
    || true
  [ -z "$BACKUP_TIMER_WAS_ACTIVE" ] \
    || systemctl start --no-block seiche-market-backup.timer 2>/dev/null \
    || true
  [ -z "$RESTORE_TIMER_WAS_ACTIVE" ] \
    || systemctl start --no-block seiche-market-restore-check.timer 2>/dev/null \
    || true
  [ -z "$OFFSITE_TIMER_WAS_ACTIVE" ] \
    || ! systemctl is-enabled --quiet seiche-market-offsite-backup.timer \
      2>/dev/null \
    || systemctl start --no-block seiche-market-offsite-backup.timer \
      2>/dev/null || true
}
DATA_READINESS_PREFLIGHT_REQUIRED_UNITS="seiche-api.service seiche-market-worker.service seiche-source-worker.service seiche-market-backup.timer seiche-market-restore-check.timer seiche-market-validation.timer seiche-release-poll.timer"
DATA_READINESS_SCRIPT=/etc/seiche/libexec/seiche-data-readiness.sh
DATA_READINESS_CONVERGENCE_WAIT_SECONDS="${SEICHE_DATA_READINESS_CONVERGENCE_WAIT_SECONDS:-900}"
run_recovery_proof_preflight() {
  SEICHE_DATA_READINESS_PROOF_ONLY=1 \
    SEICHE_DATA_READINESS_SKIP_OFFSITE=1 \
    SEICHE_DATA_READINESS_REQUIRED_UNITS='' \
    /usr/bin/bash "$DATA_READINESS_SCRIPT"
}
run_data_readiness_preflight() {
  SEICHE_DATA_READINESS_SKIP_OFFSITE=1 \
    SEICHE_DATA_READINESS_REQUIRED_UNITS="$DATA_READINESS_PREFLIGHT_REQUIRED_UNITS" \
    /usr/bin/bash "$DATA_READINESS_SCRIPT"
}
ensure_candidate_fresh_for_readiness() {
  # A release-bound backup and restore can legitimately consume the API's
  # entire 15-minute freshness budget. Nudge the cache owner without taking
  # readers offline, then require a fresh exact-SHA handoff before acceptance.
  if ! curl -sf -m 20 http://127.0.0.1:8787/api/gauge >/dev/null; then
    echo "data readiness: cache refresh nudge failed; restarting the exact candidate"
    if ! systemctl restart seiche-api; then
      echo "FAIL: API refresh restart failed after recovery proof"
      return 1
    fi
    sleep 3
  fi
  if candidate_health_wait 900 "$AFTER" 900; then
    return 0
  fi
  echo "data readiness: background refresh missed its deadline; restarting the exact candidate"
  if ! systemctl restart seiche-api; then
    echo "FAIL: API fallback restart failed after recovery proof"
    return 1
  fi
  sleep 3
  if ! candidate_health_wait 900 "$AFTER" 900; then
    echo "FAIL: API did not produce a fresh exact candidate after recovery proof"
    return 1
  fi
}
validate_data_readiness_convergence_wait() {
  case "$DATA_READINESS_CONVERGENCE_WAIT_SECONDS" in
    0|[1-9]|[1-9][0-9]|[1-9][0-9][0-9]) ;;
    *)
      echo "FAIL: data-readiness convergence wait must be an integer from 0 to 900 seconds"
      return 1
      ;;
  esac
  if [ "$DATA_READINESS_CONVERGENCE_WAIT_SECONDS" -gt 900 ]; then
    echo "FAIL: data-readiness convergence wait must be an integer from 0 to 900 seconds"
    return 1
  fi
}
converge_operational_data_readiness() {
  local readiness_output readiness_status deadline

  if readiness_output=$(run_data_readiness_preflight 2>&1); then
    readiness_status=0
  else
    readiness_status=$?
  fi
  if [ "$readiness_status" -eq 0 ]; then
    if [ "$readiness_output" != "seiche data readiness: ready" ]; then
      printf 'FAIL: operational data readiness returned unexpected success output: %s\n' \
        "$readiness_output"
      return 1
    fi
    return 0
  fi
  if [ "$readiness_status" -ne 1 ] \
      || [ "$readiness_output" != "seiche data readiness: API snapshot stale" ]; then
    [ -z "$readiness_output" ] || printf '%s\n' "$readiness_output" >&2
    return 1
  fi

  if ! systemctl is-active --quiet seiche-api.service; then
    echo "FAIL: seiche-api is not active before stale snapshot refresh"
    return 1
  fi
  if ! /usr/bin/curl --fail --silent --show-error --proto '=http' \
      --connect-timeout 10 --max-time 10 --output /dev/null \
      'http://127.0.0.1:8787/api/gauge'; then
    echo "FAIL: stale API snapshot refresh trigger failed"
    return 1
  fi

  deadline=$((SECONDS + DATA_READINESS_CONVERGENCE_WAIT_SECONDS))
  while true; do
    if ! systemctl is-active --quiet seiche-api.service; then
      echo "FAIL: seiche-api died during stale snapshot convergence"
      return 1
    fi
    if readiness_output=$(run_data_readiness_preflight 2>&1); then
      readiness_status=0
    else
      readiness_status=$?
    fi
    if [ "$readiness_status" -eq 0 ]; then
      if [ "$readiness_output" != "seiche data readiness: ready" ]; then
        printf 'FAIL: operational data readiness returned unexpected success output: %s\n' \
          "$readiness_output"
        return 1
      fi
      return 0
    fi
    if [ "$readiness_status" -ne 1 ] \
        || [ "$readiness_output" != "seiche data readiness: API snapshot stale" ]; then
      [ -z "$readiness_output" ] || printf '%s\n' "$readiness_output" >&2
      return 1
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "FAIL: API snapshot remained stale after ${DATA_READINESS_CONVERGENCE_WAIT_SECONDS}s"
      return 1
    fi
    sleep 10
  done
}
activate_data_readiness_after_proof() {
  validate_data_readiness_convergence_wait || return 1
  # An already-current v2 backup and restore receipt avoid a redundant drill.
  # A first v2/fresh host fails this preflight and must create and restore one
  # real snapshot before the persistent timer is allowed to become active.
  if ! run_recovery_proof_preflight; then
    echo "data readiness: current v2 proof unavailable; bootstrapping backup and restore"
    if ! systemctl start seiche-market-backup.service; then
      echo "FAIL: v2 data-readiness bootstrap backup failed; readiness timer remains stopped"
      return 1
    fi
    if ! systemctl start seiche-market-restore-check.service; then
      echo "FAIL: v2 data-readiness bootstrap restore check failed; readiness timer remains stopped"
      return 1
    fi
    if ! run_recovery_proof_preflight; then
      echo "FAIL: v2 data-readiness bootstrap did not pass; readiness timer remains stopped"
      return 1
    fi
  fi
  if ! ensure_candidate_fresh_for_readiness; then
    echo "FAIL: exact candidate freshness could not be restored before readiness"
    return 1
  fi
  if ! converge_operational_data_readiness; then
    echo "FAIL: operational data readiness did not pass; readiness timer remains stopped"
    return 1
  fi
  # A background snapshot refresh publishes its in-memory board before the
  # exact release handoff finishes sealing. Readiness can therefore turn green
  # during that short interval; wait for the strict SHA-bound capability
  # instead of accepting ordinary API health without current release evidence.
  if ! candidate_health_wait 120 "$AFTER" 900; then
    echo "FAIL: exact candidate evidence did not reseal after data-readiness convergence"
    return 1
  fi
  if ! systemctl enable --now seiche-data-readiness.timer; then
    echo "FAIL: proven readiness timer could not be activated"
    return 1
  fi
}
ensure_source_worker_ready() {
  systemctl reset-failed seiche-source-worker.service 2>/dev/null || true
  if ! systemctl start seiche-source-worker.service; then
    echo "FAIL: source worker did not produce its initial durable heartbeat"
    return 1
  fi
}
start_market_services() {
  systemctl reset-failed \
    seiche-market-worker.service seiche-source-worker.service 2>/dev/null \
    || true
  # The worker is Type=notify and ordered after the one-shot backfill.  Wait
  # for both jobs here: a --no-block start races the readiness preflight, which
  # can mistake an activating worker for a stale recovery proof and repeat the
  # full backup/restore drill on the controller's same-SHA convergence pass.
  if ! systemctl start \
      seiche-market-backfill.service seiche-market-worker.service; then
    echo "FAIL: market backfill/worker did not become ready"
    return 1
  fi
  # Type=notify makes this block until the initial durable sweep has completed.
  # Only then submit the persistent readiness timer; its After= on the market
  # worker keeps a missed run queued until every collector startup is complete.
  ensure_source_worker_ready || return 1
  activate_data_readiness_after_proof || return 1
  if [ -n "$OFFSITE_TIMER_WAS_ACTIVE" ] \
      && systemctl is-enabled --quiet seiche-market-offsite-backup.timer; then
    systemctl start seiche-market-offsite-backup.timer || {
      echo "FAIL: offsite backup timer could not be restored after exact-SHA promotion"
      return 1
    }
  fi
}
MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=""
restore_preupdate_market_worker_unit() {
  local restore_sha="$DEPLOYED" stage candidate destination
  [ -n "$MARKET_WORKER_UNIT_MAY_HAVE_CHANGED" ] || return 0
  if ! valid_release_sha "$restore_sha"; then
    restore_sha="$BEFORE"
  fi
  if ! valid_release_sha "$restore_sha" \
      || ! runuser -u seiche -- git -C "$APP" rev-parse --verify --quiet \
        "$restore_sha^{commit}" >/dev/null; then
    echo "FAIL: no verified pre-update worker unit is available"
    return 1
  fi
  stage=$(mktemp -d /etc/systemd/system/.seiche-market-worker-restore.XXXXXX) \
    || return 1
  candidate="$stage/seiche-market-worker.service"
  destination=/etc/systemd/system/seiche-market-worker.service
  if ! runuser -u seiche -- git -C "$APP" show \
      "${restore_sha}:ops/deploy/seiche-market-worker.service" >"$candidate" \
      || ! chmod 0644 "$candidate" \
      || ! systemd-analyze verify "$candidate" \
      || ! mv -f "$candidate" "$destination" \
      || ! systemctl daemon-reload; then
    rm -f -- "$candidate"
    rmdir "$stage" 2>/dev/null || true
    echo "FAIL: pre-update market worker unit could not be restored"
    return 1
  fi
  rmdir "$stage"
  if [ -n "$MARKET_WORKER_WAS_ENABLED" ]; then
    if ! systemctl enable seiche-market-worker.service >/dev/null \
        || ! systemctl is-enabled --quiet seiche-market-worker.service; then
      echo "FAIL: market worker enablement could not be restored"
      return 1
    fi
  else
    systemctl disable seiche-market-worker.service >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-market-worker.service 2>/dev/null; then
      echo "FAIL: candidate market worker remains enabled after rollback"
      return 1
    fi
  fi
  MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=""
  echo "market worker unit restored from ${restore_sha:0:7}"
}
restore_preupdate_api() {
  local restore_sha="$DEPLOYED" deadline
  if ! valid_release_sha "$restore_sha"; then
    restore_sha="$BEFORE"
  fi
  if ! valid_release_sha "$restore_sha" \
      || ! runuser -u seiche -- git -C "$APP" rev-parse --verify --quiet \
        "$restore_sha^{commit}" >/dev/null; then
    echo "FAIL: no verified pre-update release is available to restart"
    return 1
  fi
  if ! runuser -u seiche -- git -C "$APP" reset -q --hard "$restore_sha" \
      || ! runuser -u seiche -- bash -c \
        "cd $APP && timeout -k 30 600 backend/.venv/bin/pip install -q -e './backend[notary]'" \
      || ! write_release_env "$restore_sha" \
      || ! systemctl restart seiche-api; then
    echo "FAIL: pre-update api could not be restored"
    return 1
  fi
  # Rebuilding the same board is not faster merely because this is recovery.
  # Match the candidate's 15-minute strict-health budget so a CPU-bound but
  # progressing known-good API is not abandoned with every writer stopped.
  deadline=$((SECONDS + 900))
  until curl -sf -m 10 \
      'http://127.0.0.1:8787/api/health?require_rebuilt=true' >/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ] \
        || ! systemctl is-active --quiet seiche-api; then
      echo "FAIL: restored pre-update api did not become healthy"
      return 1
    fi
    sleep 10
  done
  echo "pre-update api restored at ${restore_sha:0:7}"
}
restore_quiesced_api() {
  if [ -n "$API_QUIESCED" ]; then
    restore_preupdate_api || {
      echo "FAIL: seiche-api needs a human after a pre-restart failure"
      return 1
    }
  fi
}
restore_pre_restart_services() {
  if ! restore_preupdate_market_worker_unit; then
    echo "FAIL: market writers remain stopped because their unit recovery failed"
    return 1
  fi
  if ! restore_preupdate_data_units; then
    echo "FAIL: data workers remain stopped because their unit recovery failed"
    return 1
  fi
  if ! restore_quiesced_api; then
    echo "FAIL: market writers remain stopped because api recovery failed"
    return 1
  fi
  restore_market_services
}
systemctl stop seiche-data-readiness.timer seiche-data-readiness.service \
  2>/dev/null || true
systemctl stop \
  seiche-market-validation.timer seiche-market-validation.service \
  seiche-market-backup.timer seiche-market-backup.service \
  seiche-market-offsite-backup.timer seiche-market-offsite-backup.service \
  seiche-market-restore-check.timer seiche-market-restore-check.service \
  2>/dev/null || true
systemctl stop seiche-market-worker.service seiche-market-backfill.service \
  seiche-source-worker.service \
  2>/dev/null || true
API_QUIESCED=""
if [ "$BEFORE" != "$TARGET" ] || [ "$DEPLOYED" != "$TARGET" ]; then
  if ! systemctl stop seiche-api; then
    restore_market_services
    echo "FAIL: seiche-api could not be quiesced before checkout mutation"
    exit 1
  fi
  API_QUIESCED=1
fi
if ! runuser -u seiche -- env SEICHE_DEPLOYED_SHA="$DEPLOYED" \
    SEICHE_UPDATE_TARGET_SHA="$TARGET" \
    bash /home/seiche/update.sh; then
  restore_pre_restart_services \
    || echo "FAIL: seiche-api needs a human after the update-gate failure"
  echo "FAIL: application update gate failed; recovery was attempted"
  exit 1
fi
AFTER=""
if ! AFTER=$(runuser -u seiche -- git -C "$APP" rev-parse HEAD); then
  restore_pre_restart_services || true
  echo "FAIL: candidate checkout identity could not be resolved"
  exit 1
fi
if [ "$AFTER" != "$TARGET" ] \
    || ! valid_release_sha "$AFTER" \
    || ! runuser -u seiche -- git -C "$APP" diff-index --quiet "$AFTER" --; then
  restore_pre_restart_services || true
  echo "FAIL: candidate checkout does not exactly match its release SHA"
  exit 1
fi
UNTRACKED_IMPORTS=""
if ! UNTRACKED_IMPORTS=$(
  {
    runuser -u seiche -- git -C "$APP" ls-files \
      --others --exclude-standard -- backend
    runuser -u seiche -- git -C "$APP" ls-files \
      --others --ignored --exclude-standard -- backend
  } | awk '
    /\.(py|pyc|so)$/ \
      && $0 !~ /^backend\/\.venv\// \
      && $0 !~ /\/__pycache__\// { print }
  '
); then
  restore_pre_restart_services || true
  echo "FAIL: candidate checkout import-surface audit failed"
  exit 1
fi
if [ -n "$UNTRACKED_IMPORTS" ]; then
  restore_pre_restart_services || true
  echo "FAIL: candidate checkout has untracked importable backend files"
  exit 1
fi

# Self-sync the deploy chain from the POST-pull checkout. The manual root
# deploy synced these mirrors only when someone ran it, and from the pre-pull
# tree (one deploy behind); the auto chain never synced at all, so a repo fix
# to either script changed nothing on the box. Installed atomically
# (write-beside + rename) so the running copy keeps its inode and this run
# finishes on the code it started with — no re-exec, so no loop.
SYNC_FAIL=""
for pair in "seiche-deploy-wrapper.sh:/var/lib/seiche-deploy/bin/seiche-deploy-wrapper.sh" \
            "box-update.sh:/home/seiche/update.sh"; do
  src="$APP/ops/deploy/${pair%%:*}"
  dst="${pair##*:}"
  if [ ! -f "$src" ]; then
    echo "sync: $src missing from the checkout"; SYNC_FAIL=1; continue
  fi
  if cmp -s "$src" "$dst"; then
    continue
  fi
  if ! bash -n "$src"; then
    echo "sync: $src fails a syntax check; keeping the installed copy"; SYNC_FAIL=1; continue
  fi
  cp "$dst" "$dst.bak-$(date +%s)" 2>/dev/null || true
  if cp "$src" "$dst.new" && chmod +x "$dst.new" && mv -f "$dst.new" "$dst"; then
    echo "sync: installed $dst from the post-pull checkout (effective next deploy)"
  else
    echo "sync: could not install $dst"; SYNC_FAIL=1
  fi
done

sync_verdict() {  # loud drift check at exit: a red run, never a wedged box
  if [ -n "$SYNC_FAIL" ]; then
    echo "FAIL: deploy-script sync failed (see sync: lines above) — the box mirrors drift from the repo"
    exit 1
  fi
}

deploy_caddy() {
  local installer="$APP/ops/deploy/install-caddy.sh"
  if [ ! -f "$installer" ]; then
    echo "FAIL: Caddy installer missing from the post-pull checkout: $installer"
    return 1
  fi
  # Invoke with bash rather than trusting the executable bit: old checkouts can
  # carry the helper before its mode has been repaired on the box.
  bash "$installer"
}

deploy_market_platform() {
  local installer="$APP/ops/deploy/install-market-platform.sh"
  if [ ! -f "$installer" ]; then
    echo "FAIL: market-platform installer missing: $installer"
    return 1
  fi
  # Historical backfill can saturate the box. Install the units now, but keep
  # ingestion stopped until the candidate API and repository pass health.
  SEICHE_DEFER_MARKET_START=1 bash "$installer"
}

deploy_pull_unit() {
  local source="$APP/ops/deploy/seiche-pull.service"
  local destination=/etc/systemd/system/seiche-pull.service
  local stage candidate previous had_previous=""
  if [ ! -f "$source" ]; then
    echo "FAIL: canonical pull unit missing: $source"
    return 1
  fi
  stage=$(mktemp -d /etc/systemd/system/.seiche-pull-stage.XXXXXX) || return 1
  candidate="$stage/seiche-pull.service"
  previous="$stage/previous.service"
  if ! install -m 0644 "$source" "$candidate"; then
    rmdir "$stage" 2>/dev/null || true
    return 1
  fi
  if ! systemd-analyze verify "$candidate"; then
    rm -f -- "$candidate"
    rmdir "$stage" 2>/dev/null || true
    echo "FAIL: canonical pull unit did not pass systemd verification"
    return 1
  fi
  if [ -e "$destination" ]; then
    cp -p "$destination" "$previous" || {
      rm -f -- "$candidate"
      rmdir "$stage" 2>/dev/null || true
      return 1
    }
    had_previous=1
  fi
  if ! mv -f "$candidate" "$destination"; then
    rm -f -- "$previous" "$candidate"
    rmdir "$stage" 2>/dev/null || true
    return 1
  fi
  if systemctl daemon-reload; then
    rm -f -- "$previous"
    rmdir "$stage" 2>/dev/null || true
    echo "pull unit: installed cached localhost alert evaluator"
    return 0
  fi

  echo "FAIL: daemon-reload rejected the pull unit; restoring the previous unit"
  if [ -n "$had_previous" ]; then
    mv -f "$previous" "$destination" || {
      echo "FAIL: could not restore $destination"
      rm -f -- "$candidate"
      rmdir "$stage" 2>/dev/null || true
      return 1
    }
  else
    rm -f -- "$destination"
  fi
  systemctl daemon-reload || echo "FAIL: daemon-reload also failed after pull-unit rollback"
  rm -f -- "$candidate" "$previous"
  rmdir "$stage" 2>/dev/null || true
  return 1
}

# A restored last-known-good snapshot makes public reads available immediately,
# but it is not proof that this candidate can assemble a board.  The query flag
# keeps the release gate waiting for a build completed by the current process;
# every poll remains cache-only and cheap.
parse_candidate_health() {
  local body="$1" expected_sha="$2" max_generated_age="${3:-0}" now_epoch="${4:-}"
  "$APP/backend/.venv/bin/python" -c '
from datetime import datetime, timezone
import json
import re
import sys
import time

try:
    expected_sha = sys.argv[2]
    max_generated_age = int(sys.argv[3])
    now_epoch = int(sys.argv[4]) if sys.argv[4] else int(time.time())
    if max_generated_age < 0 or now_epoch < 0:
        raise ValueError
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError
    candidate = payload.get("release_candidate")
    if (
        re.fullmatch(r"[0-9a-f]{40}", expected_sha) is None
        or not isinstance(candidate, dict)
        or set(candidate) != {"producer_sha", "activation_token"}
        or candidate.get("producer_sha") != expected_sha
        or not isinstance(candidate.get("activation_token"), str)
        or re.fullmatch(r"[0-9a-f]{64}", candidate["activation_token"]) is None
    ):
        raise ValueError
    if max_generated_age:
        generated_at = payload.get("generated_at")
        if not isinstance(generated_at, str) or len(generated_at) > 64:
            raise ValueError
        candidate_time = datetime.fromisoformat(
            generated_at[:-1] + "+00:00"
            if generated_at.endswith(("Z", "z"))
            else generated_at
        )
        if candidate_time.tzinfo is None or candidate_time.utcoffset() is None:
            raise ValueError
        generated_epoch = candidate_time.astimezone(timezone.utc).timestamp()
        if generated_epoch > now_epoch + 300:
            raise ValueError
        if now_epoch - generated_epoch > max_generated_age:
            raise ValueError
except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
    raise SystemExit(1)
sys.stdout.write(candidate["activation_token"])
' "$body" "$expected_sha" "$max_generated_age" "$now_epoch"
}

ACTIVATION_TOKEN=""
candidate_health_once() {
  local expected_sha="$1" max_generated_age="${2:-0}" body token now_epoch
  body=$(mktemp) || return 1
  if ! curl -sf -m 10 \
      'http://127.0.0.1:8787/api/internal/v1/release-health' >"$body"; then
    rm -f -- "$body"
    return 1
  fi
  now_epoch=$(date +%s) || {
    rm -f -- "$body"
    return 1
  }
  if ! token=$(
      parse_candidate_health \
        "$body" "$expected_sha" "$max_generated_age" "$now_epoch"
  ); then
    rm -f -- "$body"
    return 1
  fi
  rm -f -- "$body"
  ACTIVATION_TOKEN="$token"
}

candidate_health_wait() {  # candidate_health_wait SECONDS SHA [MAX_AGE] -> exact candidate
  local window="$1" expected_sha="$2" max_generated_age="${3:-0}" deadline=$((SECONDS + $1))
  until candidate_health_once "$expected_sha" "$max_generated_age"; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "FAIL: api did not rebuild the exact release after $((window / 60))min warm-up window"
      return 1
    fi
    systemctl is-active --quiet seiche-api || { echo "FAIL: seiche-api died during warm-up"; return 1; }
    sleep 10
  done
  return 0
}

# A rollback target can predate the controller token contract. It still has to
# complete its own rebuild, but a legacy healthy response need not advertise a
# promotion capability that only the candidate gate consumes.
rollback_health_wait() {
  local window="$1" deadline=$((SECONDS + $1))
  until curl -sf -m 10 \
      'http://127.0.0.1:8787/api/health?require_rebuilt=true' >/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "FAIL: rollback api did not rebuild after $((window / 60))min warm-up window"
      return 1
    fi
    systemctl is-active --quiet seiche-api || {
      echo "FAIL: seiche-api died during rollback warm-up"
      return 1
    }
    sleep 10
  done
}

market_health() {
  local body
  body=$(mktemp)
  if ! curl -sf -m 20 http://127.0.0.1:8787/api/v2/coverage >"$body"; then
    echo "FAIL: v2 coverage cannot read the configured market repository"
    rm -f -- "$body"
    return 1
  fi
  if ! "$APP/backend/.venv/bin/python" -c \
      'import json,sys; from seiche.markets.registry import default_registry; p=json.load(open(sys.argv[1])); assert p["schema"] == "seiche.coverage.v2"; expected={pack.market_id for pack in default_registry().list()}; actual=[market["market_id"] for market in p["markets"]]; assert len(actual) == len(expected) and set(actual) == expected' \
      "$body"; then
    echo "FAIL: v2 coverage returned an invalid market-platform contract"
    rm -f -- "$body"
    return 1
  fi
  rm -f -- "$body"
  systemctl is-active --quiet postgresql || {
    echo "FAIL: PostgreSQL is not active after market-platform provisioning"
    return 1
  }
  return 0
}

POINT_OF_NO_RETURN=""
promote_snapshot_handoff() {
  local attempt
  for attempt in 1 2 3; do
    [ "$attempt" -eq 1 ] || sleep 15
    ACTIVATION_TOKEN=""
    # Refresh immediately before each request so the unit can activate only the
    # exact handoff generation the healthy candidate is serving right now.
    if ! candidate_health_once "$AFTER"; then
      echo "FAIL: promotion attempt $attempt could not refresh exact candidate health"
    elif ! write_promotion_request "$AFTER" "$ACTIVATION_TOKEN"; then
      echo "FAIL: promotion attempt $attempt could not install its exact request"
    elif ! write_deployed_state "$AFTER"; then
      # The candidate is healthy, but without durable acceptance a later
      # forced-deploy could mistake the old release for the rollback target.
      if [ -n "$DEPLOYED_STATE_RENAMED" ]; then
        POINT_OF_NO_RETURN=1
      fi
      echo "FAIL: promotion attempt $attempt could not durably accept the candidate"
    else
      # The unit may commit and then lose its response. Never move the checkout
      # underneath the healthy candidate once an activation has been submitted.
      # deployed-sha already names this healthy candidate, so the boundary also
      # survives this shell process exiting before systemctl returns.
      POINT_OF_NO_RETURN=1
      if systemctl start "$PROMOTION_UNIT"; then
        if ! rm -f -- "$PROMOTION_REQUEST"; then
          echo "FAIL: activated request could not be cleared"
          return 1
        fi
        if ! candidate_health_wait 120 "$AFTER"; then
          echo "FAIL: candidate lost strict health after snapshot activation"
          return 1
        fi
        echo "snapshot handoff: activated controller-approved candidate"
        return 0
      fi
      echo "FAIL: promotion attempt $attempt did not complete"
    fi
  done
  rm -f -- "$PROMOTION_REQUEST" \
    || echo "FAIL: stale snapshot promotion request could not be cleared"
  echo "FAIL: verified candidate snapshot could not be activated after 3 attempts"
  return 1
}

MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=1
DATA_UNITS_MAY_HAVE_CHANGED=1
deploy_market_platform || {
  restore_pre_restart_services || true
  echo "FAIL: application checkout is intact but market-platform provisioning failed"
  exit 1
}

# The API captures this root-controlled identity at process start. The same
# file is required by the unprivileged promotion unit on both a normal deploy
# and the second pass of the first controller rollout.
if ! write_release_env "$AFTER"; then
  restore_pre_restart_services || true
  echo "FAIL: candidate release identity could not be installed"
  exit 1
fi

if [ "$BEFORE" = "$AFTER" ] && [ "$DEPLOYED" = "$AFTER" ]; then
  echo "already running ${AFTER:0:7} — checking candidate rebuild and edge config"
  if ! systemctl is-active --quiet seiche-api; then
    echo "accepted release api is inactive — restarting it without moving the checkout"
    if ! systemctl restart seiche-api; then
      echo "FAIL: accepted release api could not be restarted; market writers remain stopped"
      exit 1
    fi
    sleep 3
  fi
  ensure_source_worker_ready || {
    echo "FAIL: accepted release source worker did not become ready"
    exit 1
  }
  candidate_health_wait 900 "$AFTER" || {
    echo "FAIL: accepted release did not recover strict health; market writers remain stopped"
    exit 1
  }
  market_health || {
    restore_market_services
    echo "FAIL: running candidate cannot read the market repository"
    exit 1
  }
  deploy_pull_unit || {
    restore_market_services
    echo "FAIL: canonical pull unit could not be converged"
    exit 1
  }
  promote_snapshot_handoff || {
    restore_market_services
    echo "FAIL: healthy running candidate kept in place; snapshot activation needs a human"
    exit 1
  }
  start_market_services || { echo "FAIL: market services could not be started"; exit 1; }
  deploy_caddy || { echo "FAIL: application is healthy but the Caddy deploy failed and was rolled back"; exit 1; }
  sync_verdict
  echo "already deployed ${AFTER:0:7} — application and edge match the repo"
  exit 0
fi
if [ "$BEFORE" = "$AFTER" ]; then
  echo "HEAD already at ${AFTER:0:7} but the running service is ${DEPLOYED:-unknown} — recovering a wedged deploy"
fi

HEALTHY=""
RESTARTED=""
# Every fallible pre-activation step stays inside a conditional. Under set -e,
# a bare restart failure would otherwise abort before the rollback state machine.
if systemctl restart seiche-api; then
  RESTARTED=1
  sleep 3
else
  echo "FAIL: seiche-api could not be restarted onto the candidate"
fi
if [ -n "$RESTARTED" ] && systemctl is-active --quiet seiche-api; then
  if ensure_source_worker_ready && candidate_health_wait 900 "$AFTER"; then
    if market_health; then
      if deploy_pull_unit; then
        if promote_snapshot_handoff; then
          HEALTHY=1
        fi
      fi
    fi
  fi
elif [ -n "$RESTARTED" ]; then
  echo "FAIL: seiche-api not active after restart"
fi

if [ -n "$HEALTHY" ]; then
  start_market_services || { echo "FAIL: market services could not be started"; exit 1; }
  echo "application ${AFTER:0:7} active and healthy — deploying edge config"
  deploy_caddy || { echo "FAIL: application is healthy but the Caddy deploy failed and was rolled back"; exit 1; }
  sync_verdict
  echo "deployed ${AFTER:0:7} — service active, api healthy, edge config current"
  exit 0
fi

if [ -n "$POINT_OF_NO_RETURN" ]; then
  restore_market_services
  echo "FAIL: snapshot activation failed; healthy candidate code remains running and no rollback was attempted"
  exit 1
fi

# A red warm-up used to leave the NEW code live with a dead API and nothing
# but a red CI run. Roll the service back to the last sha that passed health
# — once, with its own gate and its own on-box timeouts, and loud either
# way: this path always exits 1, because a deploy that needed the rollback
# needs a human even when the rollback lands. Never rely on cancellation.
echo "FAIL: ${AFTER:0:7} did not come healthy after restart"
systemctl stop seiche-data-readiness.timer seiche-data-readiness.service \
  2>/dev/null || true
systemctl stop \
  seiche-market-validation.timer seiche-market-validation.service \
  seiche-market-backup.timer seiche-market-backup.service \
  seiche-market-offsite-backup.timer seiche-market-offsite-backup.service \
  seiche-market-restore-check.timer seiche-market-restore-check.service \
  2>/dev/null || true
systemctl stop seiche-market-worker.service seiche-market-backfill.service \
  seiche-source-worker.service 2>/dev/null || true
if [ -z "$DEPLOYED" ] || [ "$DEPLOYED" = "$AFTER" ]; then
  echo "FAIL: no previously-deployed sha on record to roll back to — seiche-api needs a human NOW"
  exit 1
fi
if ! valid_release_sha "$DEPLOYED"; then
  echo "FAIL: recorded deployment identity is not a canonical commit SHA — cannot roll back automatically"
  exit 1
fi
if ! runuser -u seiche -- git -C "$APP" rev-parse --verify --quiet "$DEPLOYED^{commit}" >/dev/null; then
  echo "FAIL: recorded sha ${DEPLOYED:0:7} is not in the checkout — cannot roll back automatically"
  exit 1
fi
if ! systemctl stop seiche-api; then
  echo "FAIL: seiche-api could not be stopped cleanly — refusing to mutate its checkout"
  exit 1
fi
if ! write_release_env "$DEPLOYED"; then
  echo "FAIL: rollback release identity could not be installed — checkout remains unchanged"
  exit 1
fi
echo "rolling the service back to ${DEPLOYED:0:7} (last sha that passed health)"
runuser -u seiche -- git -C "$APP" reset -q --hard "$DEPLOYED"
runuser -u seiche -- bash -c "cd $APP && timeout -k 30 600 backend/.venv/bin/pip install -q -e './backend[notary]'" \
  || { echo "FAIL: rollback pip install failed or timed out — seiche-api needs a human NOW"; exit 1; }
runuser -u seiche -- bash -c "cd $APP && timeout -k 30 120 backend/.venv/bin/python -c 'import seiche.api, seiche.assemble, seiche.dispatch_daily'" \
  || { echo "FAIL: rollback tree does not import — seiche-api needs a human NOW"; exit 1; }
restore_preupdate_market_worker_unit \
  || { echo "FAIL: rollback worker unit could not be restored; market writers remain stopped"; exit 1; }
restore_preupdate_data_units \
  || { echo "FAIL: rollback data units could not be restored; data workers remain stopped"; exit 1; }
systemctl restart seiche-api
sleep 3
if systemctl is-active --quiet seiche-api && rollback_health_wait 900; then
  write_deployed_state "$DEPLOYED" || {
    echo "FAIL: rollback is healthy but deployed state could not be recorded"
    exit 1
  }
  restore_market_services
  echo "FAIL: rolled back to ${DEPLOYED:0:7}, healthy; the deploy of ${AFTER:0:7} FAILED health and needs a human"
  exit 1
fi
echo "FAIL: rollback to ${DEPLOYED:0:7} did not come healthy either — seiche-api is down and needs a human NOW"
exit 1
