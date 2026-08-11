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
PRESTATE_STAGE=""
ARCHIVE_STAGE=""
SHA_STAGE=""
STAT_STAGE=""

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
        || return 1
    if [ "$ALLOW_NON_ROOT_TEST" != "1" ]; then
        [ "$("$STAT_BIN" -c '%U:%G' "$path")" = "root:root" ] \
            || return 1
    fi
}

cleanup_active_stages() {
    local stage_path=""
    for stage_path in \
            "$PRESTATE_STAGE" "$ARCHIVE_STAGE" "$SHA_STAGE" "$STAT_STAGE"; do
        [ -z "$stage_path" ] || rm -f -- "$stage_path"
    done
}
trap cleanup_active_stages EXIT

cleanup_stale_stages() {
    local pattern="" candidate=""
    for pattern in \
            '.pre-retirement-state.*' \
            '.SHA256SUMS.*' \
            '.STAT.*' \
            ".$SERVICE_NAME.archive.*" \
            ".$TIMER_NAME.archive.*" \
            ".$SERVICE_NAME.absent.archive.*" \
            ".$TIMER_NAME.absent.archive.*"; do
        while IFS= read -r -d '' candidate; do
            [ "$(dirname "$candidate")" = "$ARCHIVE_DIR" ] \
                || fail "stale stage escaped the archive directory"
            rm -f -- "$candidate"
        done < <(
            find "$ARCHIVE_DIR" -mindepth 1 -maxdepth 1 -type f \
                -name "$pattern" -print0
        )
    done
    "$SYNC_BIN" "$ARCHIVE_DIR"
}
cleanup_stale_stages

write_prestate_once() {
    local timer_enabled="" timer_active="" service_active=""
    if [ -e "$PRESTATE_PATH" ] || [ -L "$PRESTATE_PATH" ]; then
        safe_archive_file "$PRESTATE_PATH" \
            || fail "pre-retirement state archive is unsafe"
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
    PRESTATE_STAGE=$(mktemp "$ARCHIVE_DIR/.pre-retirement-state.XXXXXX")
    if ! printf '%s\n' \
            "schema=seiche.retired-update-units.v1" \
            "timer_enabled=${timer_enabled:-unknown}" \
            "timer_active=${timer_active:-unknown}" \
            "service_active=${service_active:-unknown}" >"$PRESTATE_STAGE" \
        || ! chmod 0600 "$PRESTATE_STAGE" \
        || ! "$SYNC_BIN" -f "$PRESTATE_STAGE" \
        || ! mv -f "$PRESTATE_STAGE" "$PRESTATE_PATH"; then
        fail "could not record the pre-retirement unit state"
    fi
    PRESTATE_STAGE=""
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

valid_absence_marker() {
    local path="$1" unit_name="$2" expected=""
    safe_archive_file "$path" || return 1
    expected=$(printf '%s\n%s' \
        'schema=seiche.retired-update-unit-absence.v1' \
        "unit=$unit_name")
    [ "$(cat "$path")" = "$expected" ]
}

write_absence_marker_once() {
    local marker="$1" unit_name="$2"
    if [ -e "$marker" ] || [ -L "$marker" ]; then
        valid_absence_marker "$marker" "$unit_name" \
            || fail "retired unit absence marker is unsafe: $unit_name"
        return
    fi
    ARCHIVE_STAGE=$(mktemp "$ARCHIVE_DIR/.$(basename "$marker").archive.XXXXXX")
    if ! printf '%s\n%s\n' \
            'schema=seiche.retired-update-unit-absence.v1' \
            "unit=$unit_name" >"$ARCHIVE_STAGE" \
        || ! chmod 0600 "$ARCHIVE_STAGE" \
        || ! safe_archive_file "$ARCHIVE_STAGE" \
        || ! "$SYNC_BIN" -f "$ARCHIVE_STAGE" \
        || ! mv -f "$ARCHIVE_STAGE" "$marker"; then
        fail "could not record absent legacy unit: $unit_name"
    fi
    ARCHIVE_STAGE=""
    "$SYNC_BIN" "$ARCHIVE_DIR"
}

archive_unit() {
    local source="$1" archive="$2" source_meta="" archive_meta=""
    local unit_name="" absence_marker=""
    unit_name=$(basename "$source")
    absence_marker="$archive.absent"
    if [ -L "$source" ]; then
        [ "$(readlink "$source")" = /dev/null ] \
            || fail "legacy unit is an unexpected symlink: $unit_name"
        if safe_archive_file "$archive"; then
            [ ! -e "$absence_marker" ] && [ ! -L "$absence_marker" ] \
                || fail "retired unit has conflicting archive evidence: $unit_name"
        elif valid_absence_marker "$absence_marker" "$unit_name"; then
            [ ! -e "$archive" ] && [ ! -L "$archive" ] \
                || fail "retired unit archive member is unsafe: $unit_name"
        else
            fail "masked legacy unit has no verified retirement evidence: $unit_name"
        fi
        return
    fi
    if [ ! -e "$source" ]; then
        if [ -e "$archive" ] || [ -L "$archive" ]; then
            safe_archive_file "$archive" \
                || fail "retired unit archive member is unsafe: $unit_name"
            [ ! -e "$absence_marker" ] && [ ! -L "$absence_marker" ] \
                || fail "retired unit has conflicting archive evidence: $unit_name"
        else
            write_absence_marker_once "$absence_marker" "$unit_name"
        fi
        return
    fi
    [ -f "$source" ] \
        || fail "legacy unit is not a regular file: $unit_name"
    [ ! -e "$absence_marker" ] && [ ! -L "$absence_marker" ] \
        || fail "retired unit has conflicting absence evidence: $unit_name"
    if [ "$ALLOW_NON_ROOT_TEST" != "1" ]; then
        [ "$("$STAT_BIN" -c '%U:%G' "$source")" = "root:root" ] \
            || fail "legacy unit ownership is unsafe: $unit_name"
    fi
    source_meta=$("$STAT_BIN" -c '%u:%g:%a:%Y' "$source")
    if [ -e "$archive" ] || [ -L "$archive" ]; then
        safe_archive_file "$archive" \
            || fail "retired unit archive member is unsafe"
        cmp -s "$source" "$archive" \
            || fail "retired unit archive differs from the original"
        archive_meta=$("$STAT_BIN" -c '%u:%g:%a:%Y' "$archive")
        [ "$archive_meta" = "$source_meta" ] \
            || fail "retired unit archive metadata differs from the original"
        return
    fi
    ARCHIVE_STAGE=$(mktemp "$ARCHIVE_DIR/.$(basename "$archive").archive.XXXXXX")
    if ! "$CP_BIN" -a "$source" "$ARCHIVE_STAGE" \
        || ! safe_archive_file "$ARCHIVE_STAGE" \
        || ! cmp -s "$source" "$ARCHIVE_STAGE"; then
        fail "retired unit archive copy failed verification"
    fi
    archive_meta=$("$STAT_BIN" -c '%u:%g:%a:%Y' "$ARCHIVE_STAGE")
    if [ "$archive_meta" != "$source_meta" ] \
        || ! "$SYNC_BIN" -f "$ARCHIVE_STAGE" \
        || ! mv -f "$ARCHIVE_STAGE" "$archive"; then
        fail "retired unit archive metadata copy failed verification"
    fi
    ARCHIVE_STAGE=""
    "$SYNC_BIN" "$ARCHIVE_DIR"
}

write_inventory_once() {
    local expected_stat="" unit_name="" archive="" absence_marker=""
    local member=""
    local -a inventory_members=()
    for unit_name in "$SERVICE_NAME" "$TIMER_NAME"; do
        archive="$ARCHIVE_DIR/$unit_name"
        absence_marker="$archive.absent"
        if safe_archive_file "$archive"; then
            [ ! -e "$absence_marker" ] && [ ! -L "$absence_marker" ] \
                || fail "retired unit has conflicting archive evidence: $unit_name"
            inventory_members+=("$unit_name")
        elif valid_absence_marker "$absence_marker" "$unit_name"; then
            [ ! -e "$archive" ] && [ ! -L "$archive" ] \
                || fail "retired unit archive member is unsafe: $unit_name"
            inventory_members+=("$unit_name.absent")
        else
            fail "retired unit archive is incomplete: $unit_name"
        fi
    done
    if [ -e "$SHA_PATH" ] || [ -L "$SHA_PATH" ]; then
        safe_archive_file "$SHA_PATH" \
            || fail "retired unit checksum inventory is unsafe"
        (cd "$ARCHIVE_DIR" && "$SHA256SUM_BIN" --check --strict SHA256SUMS) \
            >/dev/null || fail "retired unit checksum inventory is invalid"
    else
        SHA_STAGE=$(mktemp "$ARCHIVE_DIR/.SHA256SUMS.XXXXXX")
        (
            cd "$ARCHIVE_DIR"
            "$SHA256SUM_BIN" "${inventory_members[@]}"
        ) >"$SHA_STAGE"
        chmod 0600 "$SHA_STAGE"
        "$SYNC_BIN" -f "$SHA_STAGE"
        mv -f "$SHA_STAGE" "$SHA_PATH"
        SHA_STAGE=""
    fi
    expected_stat=$(
        for member in "${inventory_members[@]}"; do
            printf '%s|%s\n' \
                "$member" \
                "$("$STAT_BIN" -c '%u:%g:%a:%Y' "$ARCHIVE_DIR/$member")"
        done
    )
    if [ -e "$STAT_PATH" ] || [ -L "$STAT_PATH" ]; then
        safe_archive_file "$STAT_PATH" \
            || fail "retired unit metadata inventory is unsafe"
        [ "$(cat "$STAT_PATH")" = "$expected_stat" ] \
            || fail "retired unit metadata inventory is invalid"
    else
        STAT_STAGE=$(mktemp "$ARCHIVE_DIR/.STAT.XXXXXX")
        printf '%s\n' "$expected_stat" >"$STAT_STAGE"
        chmod 0600 "$STAT_STAGE"
        "$SYNC_BIN" -f "$STAT_STAGE"
        mv -f "$STAT_STAGE" "$STAT_PATH"
        STAT_STAGE=""
    fi
    "$SYNC_BIN" "$ARCHIVE_DIR"
}

write_prestate_once

unit_path_is_masked() {
    [ -L "$1" ] && [ "$(readlink "$1")" = /dev/null ]
}

for unit_path in "$SERVICE_PATH" "$TIMER_PATH"; do
    if [ -L "$unit_path" ] && ! unit_path_is_masked "$unit_path"; then
        fail "legacy unit is an unexpected symlink: $(basename "$unit_path")"
    fi
done

if ! unit_path_is_masked "$TIMER_PATH" \
    && ! "$SYSTEMCTL_BIN" disable --now "$TIMER_NAME" >/dev/null 2>&1; then
    if [ -e "$TIMER_PATH" ] || [ -L "$TIMER_PATH" ]; then
        fail "could not disable the legacy update timer"
    fi
fi
if ! unit_path_is_masked "$SERVICE_PATH" \
    && ! "$SYSTEMCTL_BIN" disable "$SERVICE_NAME" >/dev/null 2>&1; then
    if [ -e "$SERVICE_PATH" ] || [ -L "$SERVICE_PATH" ]; then
        fail "could not disable the legacy update service"
    fi
fi
if unit_is_running "$SERVICE_NAME"; then
    "$SYSTEMCTL_BIN" stop "$SERVICE_NAME" >/dev/null \
        || fail "could not stop the legacy update service"
fi
if unit_is_running "$TIMER_NAME"; then
    fail "legacy update timer is still active"
fi
if unit_is_running "$SERVICE_NAME"; then
    fail "legacy update service is still active"
fi

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
    if unit_is_running "$unit_name"; then
        fail "$unit_name became active after masking"
    fi
done
wants_path="$SYSTEMD_DIR/timers.target.wants/$TIMER_NAME"
[ ! -e "$wants_path" ] && [ ! -L "$wants_path" ] \
    || fail "legacy timer enablement link still exists"
if find "$SYSTEMD_DIR" -mindepth 2 -maxdepth 2 -type l \
        \( -path "$SYSTEMD_DIR/*.wants/$SERVICE_NAME" \
        -o -path "$SYSTEMD_DIR/*.wants/$TIMER_NAME" \) \
        -print -quit | grep -q .; then
    fail "legacy unit enablement link still exists"
fi
"$SYNC_BIN" "$SYSTEMD_DIR"

echo "legacy updater retirement: service and timer archived and masked"
