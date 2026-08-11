#!/usr/bin/env bash
# Retire the pre-controller updater without losing its exact host provenance.
set -euo pipefail
umask 0077

SYSTEMD_DIR="${SEICHE_SYSTEMD_DIR:-/etc/systemd/system}"
DEPLOY_STATE_DIR="${SEICHE_DEPLOY_STATE_DIR:-/var/lib/seiche-deploy}"
SYSTEMCTL_BIN="${SEICHE_SYSTEMCTL_BIN:-/usr/bin/systemctl}"
SYNC_BIN="${SEICHE_SYNC_BIN:-/usr/bin/sync}"
CP_BIN="${SEICHE_CP_BIN:-/bin/cp}"
STAT_BIN="${SEICHE_STAT_BIN:-/usr/bin/stat}"
SHA256SUM_BIN="${SEICHE_SHA256SUM_BIN:-/usr/bin/sha256sum}"
ALLOW_NON_ROOT_TEST="${SEICHE_ALLOW_NON_ROOT_RETIRE_TEST:-0}"
SERVICE_NAME=seiche-update.service
TIMER_NAME=seiche-update.timer
SERVICE_PATH="$SYSTEMD_DIR/$SERVICE_NAME"
TIMER_PATH="$SYSTEMD_DIR/$TIMER_NAME"
ARCHIVE_DIR="$DEPLOY_STATE_DIR/retired-units/seiche-update-v1"
PRESTATE_PATH="$ARCHIVE_DIR/pre-retirement-state.env"
SHA_PATH="$ARCHIVE_DIR/SHA256SUMS"
STAT_PATH="$ARCHIVE_DIR/STAT"

fail() {
    echo "legacy updater retirement: $*" >&2
    exit 1
}

case "$SYSTEMD_DIR" in
    /*) ;;
    *) fail "systemd directory must be absolute" ;;
esac
case "$DEPLOY_STATE_DIR" in
    /*) ;;
    *) fail "deploy state directory must be absolute" ;;
esac
[ "$SYSTEMD_DIR" != "/" ] && [ "$DEPLOY_STATE_DIR" != "/" ] \
    || fail "refusing a filesystem-root working directory"
[ -x "$SYSTEMCTL_BIN" ] && [ -x "$SYNC_BIN" ] \
    && [ -x "$CP_BIN" ] && [ -x "$STAT_BIN" ] \
    && [ -x "$SHA256SUM_BIN" ] \
    || fail "required retirement command is unavailable"
if [ "${EUID:-$(id -u)}" -ne 0 ] && [ "$ALLOW_NON_ROOT_TEST" != "1" ]; then
    fail "must run as root"
fi
[ -d "$SYSTEMD_DIR" ] && [ ! -L "$SYSTEMD_DIR" ] \
    || fail "systemd directory must be a real directory"

if [ "$ALLOW_NON_ROOT_TEST" = "1" ]; then
    install -d -m 0700 "$DEPLOY_STATE_DIR" "$ARCHIVE_DIR"
else
    install -d -o root -g root -m 0700 "$DEPLOY_STATE_DIR" "$ARCHIVE_DIR"
fi
[ -d "$ARCHIVE_DIR" ] && [ ! -L "$ARCHIVE_DIR" ] \
    || fail "archive directory must be a real directory"

safe_archive_file() {
    local path="$1"
    [ -f "$path" ] && [ ! -L "$path" ] \
        || fail "archive member is not a regular file: $(basename "$path")"
    if [ "$ALLOW_NON_ROOT_TEST" != "1" ]; then
        [ "$("$STAT_BIN" -c '%U:%G' "$path")" = "root:root" ] \
            || fail "archive member ownership is unsafe: $(basename "$path")"
    fi
}

write_prestate_once() {
    local stage="" timer_enabled="" timer_active="" service_active=""
    if [ -e "$PRESTATE_PATH" ] || [ -L "$PRESTATE_PATH" ]; then
        safe_archive_file "$PRESTATE_PATH"
        return
    fi
    timer_enabled=$("$SYSTEMCTL_BIN" is-enabled "$TIMER_NAME" 2>/dev/null || true)
    timer_active=$("$SYSTEMCTL_BIN" is-active "$TIMER_NAME" 2>/dev/null || true)
    service_active=$("$SYSTEMCTL_BIN" is-active "$SERVICE_NAME" 2>/dev/null || true)
    case "$timer_enabled" in
        enabled|enabled-runtime|linked|linked-runtime|alias|masked|\
        masked-runtime|static|indirect|disabled|generated|transient|\
        bad|not-found|'') ;;
        *)
        fail "unexpected timer enablement state"
    esac
    for active_state in "$timer_active" "$service_active"; do
        case "$active_state" in
            active|reloading|inactive|failed|activating|deactivating|\
            maintenance|refreshing|unknown|'') ;;
            *) fail "unexpected legacy unit activity state" ;;
        esac
    done
    stage=$(mktemp "$ARCHIVE_DIR/.pre-retirement-state.XXXXXX")
    if ! printf '%s\n' \
            "schema=seiche.retired-update-units.v1" \
            "timer_enabled=${timer_enabled:-unknown}" \
            "timer_active=${timer_active:-unknown}" \
            "service_active=${service_active:-unknown}" >"$stage" \
        || ! chmod 0600 "$stage" \
        || ! "$SYNC_BIN" -f "$stage" \
        || ! mv -f "$stage" "$PRESTATE_PATH"; then
        rm -f -- "$stage"
        fail "could not record the pre-retirement unit state"
    fi
    "$SYNC_BIN" "$ARCHIVE_DIR"
}

unit_is_running() {
    local state=""
    state=$("$SYSTEMCTL_BIN" is-active "$1" 2>/dev/null || true)
    case "$state" in
        active|activating|reloading|deactivating|maintenance|refreshing) return 0 ;;
        *) return 1 ;;
    esac
}

archive_unit() {
    local source="$1" archive="$2" source_meta="" archive_meta="" stage=""
    if [ -L "$source" ]; then
        [ "$(readlink "$source")" = /dev/null ] \
            || fail "legacy unit is an unexpected symlink: $(basename "$source")"
        return
    fi
    [ -e "$source" ] || return
    [ -f "$source" ] \
        || fail "legacy unit is not a regular file: $(basename "$source")"
    if [ "$ALLOW_NON_ROOT_TEST" != "1" ]; then
        [ "$("$STAT_BIN" -c '%U:%G' "$source")" = "root:root" ] \
            || fail "legacy unit ownership is unsafe: $(basename "$source")"
    fi
    source_meta=$("$STAT_BIN" -c '%u:%g:%a:%Y' "$source")
    if [ -e "$archive" ] || [ -L "$archive" ]; then
        safe_archive_file "$archive"
        cmp -s "$source" "$archive" \
            || fail "retired unit archive differs from the original"
        archive_meta=$("$STAT_BIN" -c '%u:%g:%a:%Y' "$archive")
        [ "$archive_meta" = "$source_meta" ] \
            || fail "retired unit archive metadata differs from the original"
        return
    fi
    stage=$(mktemp "$ARCHIVE_DIR/.$(basename "$archive").archive.XXXXXX")
    if ! "$CP_BIN" -a "$source" "$stage" \
        || ! safe_archive_file "$stage" \
        || ! cmp -s "$source" "$stage"; then
        rm -f -- "$stage"
        fail "retired unit archive copy failed verification"
    fi
    archive_meta=$("$STAT_BIN" -c '%u:%g:%a:%Y' "$stage")
    if [ "$archive_meta" != "$source_meta" ] \
        || ! "$SYNC_BIN" -f "$stage" \
        || ! mv -f "$stage" "$archive" \
        || ! "$SYNC_BIN" "$ARCHIVE_DIR"; then
        rm -f -- "$stage"
        fail "retired unit archive metadata copy failed verification"
    fi
}

write_inventory_once() {
    local sha_stage="" stat_stage="" expected_stat=""
    if [ ! -f "$ARCHIVE_DIR/$SERVICE_NAME" ] \
        && [ ! -f "$ARCHIVE_DIR/$TIMER_NAME" ]; then
        return
    fi
    [ -f "$ARCHIVE_DIR/$SERVICE_NAME" ] \
        && [ -f "$ARCHIVE_DIR/$TIMER_NAME" ] \
        || fail "retired unit archive is incomplete"
    if [ -e "$SHA_PATH" ] || [ -L "$SHA_PATH" ]; then
        safe_archive_file "$SHA_PATH"
        (cd "$ARCHIVE_DIR" && "$SHA256SUM_BIN" --check --strict SHA256SUMS) \
            >/dev/null || fail "retired unit checksum inventory is invalid"
    else
        sha_stage=$(mktemp "$ARCHIVE_DIR/.SHA256SUMS.XXXXXX")
        (
            cd "$ARCHIVE_DIR"
            "$SHA256SUM_BIN" "$SERVICE_NAME" "$TIMER_NAME"
        ) >"$sha_stage"
        chmod 0600 "$sha_stage"
        "$SYNC_BIN" -f "$sha_stage"
        mv -f "$sha_stage" "$SHA_PATH"
    fi
    expected_stat=$(printf '%s|%s\n%s|%s\n' \
        "$SERVICE_NAME" \
        "$("$STAT_BIN" -c '%u:%g:%a:%Y' "$ARCHIVE_DIR/$SERVICE_NAME")" \
        "$TIMER_NAME" \
        "$("$STAT_BIN" -c '%u:%g:%a:%Y' "$ARCHIVE_DIR/$TIMER_NAME")")
    if [ -e "$STAT_PATH" ] || [ -L "$STAT_PATH" ]; then
        safe_archive_file "$STAT_PATH"
        [ "$(cat "$STAT_PATH")" = "$expected_stat" ] \
            || fail "retired unit metadata inventory is invalid"
    else
        stat_stage=$(mktemp "$ARCHIVE_DIR/.STAT.XXXXXX")
        printf '%s\n' "$expected_stat" >"$stat_stage"
        chmod 0600 "$stat_stage"
        "$SYNC_BIN" -f "$stat_stage"
        mv -f "$stat_stage" "$STAT_PATH"
    fi
    "$SYNC_BIN" "$ARCHIVE_DIR"
}

write_prestate_once

if ! "$SYSTEMCTL_BIN" disable --now "$TIMER_NAME" >/dev/null 2>&1; then
    if [ -e "$TIMER_PATH" ] || [ -L "$TIMER_PATH" ]; then
        fail "could not disable the legacy update timer"
    fi
fi
if unit_is_running "$SERVICE_NAME"; then
    "$SYSTEMCTL_BIN" stop "$SERVICE_NAME" >/dev/null \
        || fail "could not stop the legacy update service"
fi
unit_is_running "$TIMER_NAME" && fail "legacy update timer is still active"
unit_is_running "$SERVICE_NAME" && fail "legacy update service is still active"

archive_unit "$SERVICE_PATH" "$ARCHIVE_DIR/$SERVICE_NAME"
archive_unit "$TIMER_PATH" "$ARCHIVE_DIR/$TIMER_NAME"
write_inventory_once

for unit_path in "$SERVICE_PATH" "$TIMER_PATH"; do
    if [ -L "$unit_path" ]; then
        [ "$(readlink "$unit_path")" = /dev/null ] \
            || fail "refusing to remove an unexpected unit symlink"
    elif [ -e "$unit_path" ]; then
        rm -f -- "$unit_path"
    fi
done
"$SYSTEMCTL_BIN" daemon-reload
"$SYSTEMCTL_BIN" mask --now "$SERVICE_NAME" "$TIMER_NAME" >/dev/null
"$SYSTEMCTL_BIN" daemon-reload

for unit_name in "$SERVICE_NAME" "$TIMER_NAME"; do
    unit_path="$SYSTEMD_DIR/$unit_name"
    [ -L "$unit_path" ] && [ "$(readlink "$unit_path")" = /dev/null ] \
        || fail "$unit_name was not masked exactly"
    enabled_state=$("$SYSTEMCTL_BIN" is-enabled "$unit_name" 2>/dev/null || true)
    [ "$enabled_state" = "masked" ] || fail "$unit_name is not reported masked"
    unit_is_running "$unit_name" && fail "$unit_name became active after masking"
done
wants_path="$SYSTEMD_DIR/timers.target.wants/$TIMER_NAME"
[ ! -e "$wants_path" ] && [ ! -L "$wants_path" ] \
    || fail "legacy timer enablement link still exists"
"$SYNC_BIN" "$SYSTEMD_DIR"

echo "legacy updater retirement: service and timer archived and masked"
