#!/usr/bin/env bash
# Fail closed when Seiche is serving stale data or its host safety rails drift.
set -uo pipefail
set -f
umask 0077
export LC_ALL=C

readonly DEFAULT_REQUIRED_UNITS="seiche-api.service seiche-market-worker.service seiche-source-worker.service seiche-market-backup.timer seiche-market-restore-check.timer seiche-market-validation.timer seiche-release-poll.timer seiche-data-readiness.timer"
readonly DEFAULT_DISK_PATHS="/ /var/lib/seiche /var/backups/seiche-market"

HEALTH_URL="${SEICHE_DATA_READINESS_HEALTH_URL:-http://127.0.0.1:8787/api/health}"
BACKUP_DIR="${SEICHE_DATA_READINESS_BACKUP_DIR:-/var/backups/seiche-market}"
BACKUP_ARTIFACT="${SEICHE_DATA_READINESS_BACKUP_ARTIFACT:-}"
RESTORE_RECEIPT="${SEICHE_DATA_READINESS_RESTORE_RECEIPT:-/var/lib/seiche-recovery-proof/backup-restore-check.status}"
DEPLOYED_SHA_PATH="${SEICHE_DATA_READINESS_DEPLOYED_SHA_PATH:-/var/lib/seiche-deploy/deployed-sha}"
RECEIPT_UID="${SEICHE_DATA_READINESS_RECEIPT_UID:-0}"
RECEIPT_GROUP="${SEICHE_DATA_READINESS_RECEIPT_GROUP:-seiche}"
PROOF_ONLY="${SEICHE_DATA_READINESS_PROOF_ONLY:-0}"
MAX_GENERATED_AGE="${SEICHE_DATA_READINESS_MAX_GENERATED_AGE_SECONDS:-900}"
MAX_BACKUP_AGE="${SEICHE_DATA_READINESS_BACKUP_MAX_AGE_SECONDS:-129600}"
MAX_RESTORE_AGE="${SEICHE_DATA_READINESS_RESTORE_MAX_AGE_SECONDS:-691200}"
MAX_FUTURE_SKEW="${SEICHE_DATA_READINESS_MAX_FUTURE_SKEW_SECONDS:-300}"
DISK_CRITICAL_PERCENT="${SEICHE_DATA_READINESS_DISK_CRITICAL_PERCENT:-90}"
CURL_TIMEOUT="${SEICHE_DATA_READINESS_CURL_TIMEOUT_SECONDS:-10}"
NOW_EPOCH="${SEICHE_DATA_READINESS_NOW_EPOCH:-}"
REQUIRED_UNITS="${SEICHE_DATA_READINESS_REQUIRED_UNITS-$DEFAULT_REQUIRED_UNITS}"
DISK_PATHS="${SEICHE_DATA_READINESS_DISK_PATHS-$DEFAULT_DISK_PATHS}"

CURL_BIN="${SEICHE_CURL_BIN:-/usr/bin/curl}"
PYTHON_BIN="${SEICHE_PYTHON_BIN:-/home/seiche/app/backend/.venv/bin/python}"
SYSTEMCTL_BIN="${SEICHE_SYSTEMCTL_BIN:-/usr/bin/systemctl}"
DF_BIN="${SEICHE_DF_BIN:-/usr/bin/df}"
MKTEMP_BIN="${SEICHE_MKTEMP_BIN:-/usr/bin/mktemp}"
RM_BIN="${SEICHE_RM_BIN:-/usr/bin/rm}"

FAILURES=()
HEALTH_FILE=""

add_failure() {
    local existing
    for existing in "${FAILURES[@]}"; do
        [ "$existing" != "$1" ] || return 0
    done
    FAILURES+=("$1")
}

finish() {
    if [ -n "$HEALTH_FILE" ]; then
        "$RM_BIN" -f -- "$HEALTH_FILE" >/dev/null 2>&1 || true
    fi
}
trap finish EXIT

valid_positive_integer() {
    case "$1" in
        ''|*[!0-9]*) add_failure "configuration invalid"; return 1 ;;
    esac
    if [ "$1" -le 0 ]; then
        add_failure "configuration invalid"
        return 1
    fi
    return 0
}

CONFIG_VALID=1
for value in \
    "$MAX_GENERATED_AGE" \
    "$MAX_BACKUP_AGE" \
    "$MAX_RESTORE_AGE" \
    "$MAX_FUTURE_SKEW" \
    "$CURL_TIMEOUT" \
    "$DISK_CRITICAL_PERCENT"; do
    valid_positive_integer "$value" || CONFIG_VALID=0
done
if [ "$DISK_CRITICAL_PERCENT" -gt 100 ] 2>/dev/null; then
    add_failure "configuration invalid"
    CONFIG_VALID=0
fi
if [ -n "$NOW_EPOCH" ]; then
    case "$NOW_EPOCH" in
        *[!0-9]*) add_failure "configuration invalid"; CONFIG_VALID=0 ;;
    esac
fi
case "$PROOF_ONLY" in
    0|1) ;;
    *) add_failure "configuration invalid"; CONFIG_VALID=0 ;;
esac
case "$RECEIPT_UID" in
    ''|*[!0-9]*) add_failure "configuration invalid"; CONFIG_VALID=0 ;;
esac
case "$RECEIPT_GROUP" in
    ''|*[!A-Za-z0-9_.-]*) add_failure "configuration invalid"; CONFIG_VALID=0 ;;
esac
case "$BACKUP_DIR" in
    /*) ;;
    *) add_failure "configuration invalid"; CONFIG_VALID=0 ;;
esac
case "$RESTORE_RECEIPT" in
    /*) ;;
    *) add_failure "configuration invalid"; CONFIG_VALID=0 ;;
esac
case "$DEPLOYED_SHA_PATH" in
    /) add_failure "configuration invalid"; CONFIG_VALID=0 ;;
    /*) ;;
    *) add_failure "configuration invalid"; CONFIG_VALID=0 ;;
esac
if [ -n "$BACKUP_ARTIFACT" ]; then
    case "$BACKUP_ARTIFACT" in
        /*) ;;
        *) add_failure "configuration invalid"; CONFIG_VALID=0 ;;
    esac
fi

if [ ! -x "$CURL_BIN" ] || [ ! -x "$PYTHON_BIN" ] \
    || [ ! -x "$SYSTEMCTL_BIN" ] || [ ! -x "$DF_BIN" ] \
    || [ ! -x "$MKTEMP_BIN" ] || [ ! -x "$RM_BIN" ]; then
    add_failure "required command unavailable"
    CONFIG_VALID=0
fi

HEALTH_AVAILABLE=0
if [ "$CONFIG_VALID" -eq 1 ]; then
    HEALTH_FILE=$("$MKTEMP_BIN" "${TMPDIR:-/tmp}/seiche-data-readiness.XXXXXX" 2>/dev/null) \
        || HEALTH_FILE=""
    if [ -z "$HEALTH_FILE" ]; then
        add_failure "temporary health check unavailable"
    elif [ "$PROOF_ONLY" -eq 1 ]; then
        :
    elif ! "$CURL_BIN" --fail --silent --show-error \
        --proto '=http,https' --connect-timeout "$CURL_TIMEOUT" \
        --max-time "$CURL_TIMEOUT" --output "$HEALTH_FILE" \
        "$HEALTH_URL" 2>/dev/null; then
        add_failure "API health fetch failed"
    else
        HEALTH_AVAILABLE=1
    fi

    VALIDATION_OUTPUT=$(
        "$PYTHON_BIN" - "$HEALTH_FILE" "$HEALTH_AVAILABLE" "$BACKUP_DIR" \
            "$BACKUP_ARTIFACT" "$RESTORE_RECEIPT" "$DEPLOYED_SHA_PATH" \
            "$RECEIPT_UID" "$RECEIPT_GROUP" \
            "$MAX_GENERATED_AGE" "$MAX_BACKUP_AGE" "$MAX_RESTORE_AGE" \
            "$MAX_FUTURE_SKEW" "$NOW_EPOCH" 2>/dev/null <<'PY'
from __future__ import annotations

from datetime import UTC, datetime
import grp
import json
from pathlib import Path
import re
import stat
import sys
import time


(
    health_path,
    health_available_raw,
    backup_dir_raw,
    backup_artifact_raw,
    restore_receipt_raw,
    deployed_sha_path_raw,
    receipt_uid_raw,
    receipt_group,
    max_generated_raw,
    max_backup_raw,
    max_restore_raw,
    max_future_skew_raw,
    now_raw,
) = sys.argv[1:]
health_available = health_available_raw == "1"
max_generated = int(max_generated_raw)
max_backup = int(max_backup_raw)
max_restore = int(max_restore_raw)
max_future_skew = int(max_future_skew_raw)
now = float(now_raw) if now_raw else time.time()
receipt_uid = int(receipt_uid_raw)
reasons: list[str] = []


def add(reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def timestamp(value: object) -> float | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    candidate = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    try:
        return parsed.astimezone(UTC).timestamp()
    except (OverflowError, OSError, ValueError):
        return None


if health_available:
    try:
        health_file = Path(health_path)
        if health_file.stat().st_size > 8 * 1024 * 1024:
            raise ValueError
        payload = json.loads(health_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        faults = payload.get("faults")
        if not isinstance(faults, list):
            raise ValueError
    except (OSError, UnicodeError, ValueError):
        add("API health JSON invalid")
    else:
        generated_at = timestamp(payload.get("generated_at"))
        if generated_at is None:
            add("API health generated_at invalid")
        elif generated_at > now + max_future_skew:
            add("API health generated_at is in the future")
        elif now - generated_at > max_generated:
            add("API snapshot stale")

        heartbeat_faults: list[object] = []
        critical_faults: list[object] = []
        for fault in faults:
            if isinstance(fault, dict):
                status_name = str(fault.get("status", "")).strip().upper()
                category_name = str(fault.get("category", "")).strip().upper()
                source_name = str(fault.get("source", "")).strip().casefold()
                is_heartbeat = (
                    status_name in {"OVERDUE", "MISSING", "UNKNOWN"}
                    and (
                        category_name == "WORKER_HEALTH"
                        or "collector" in source_name
                        or "worker" in source_name
                    )
                )
            else:
                is_heartbeat = False
            if is_heartbeat:
                heartbeat_faults.append(fault)
            else:
                critical_faults.append(fault)
        if heartbeat_faults:
            add("collector heartbeat unhealthy")
        if critical_faults:
            add("API health reports critical faults")


def safe_regular(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode)


def secure_restore_receipt(path: Path) -> bool:
    try:
        expected_gid = grp.getgrnam(receipt_group).gr_gid
        parent = path.parent.lstat()
        receipt = path.lstat()
    except (KeyError, OSError):
        return False
    return (
        stat.S_ISDIR(parent.st_mode)
        and parent.st_uid == receipt_uid
        and parent.st_gid == expected_gid
        and stat.S_IMODE(parent.st_mode) == 0o750
        and stat.S_ISREG(receipt.st_mode)
        and receipt.st_uid == receipt_uid
        and receipt.st_gid == expected_gid
        and stat.S_IMODE(receipt.st_mode) == 0o640
        and receipt.st_nlink == 1
    )


current_deployed_sha: str | None = None
deployed_sha_path = Path(deployed_sha_path_raw)
if safe_regular(deployed_sha_path):
    try:
        if deployed_sha_path.stat().st_size > 64:
            raise ValueError
        deployed_sha_candidate = deployed_sha_path.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{40}", deployed_sha_candidate) is None:
            raise ValueError
        current_deployed_sha = deployed_sha_candidate
    except (OSError, UnicodeError, ValueError):
        current_deployed_sha = None
if current_deployed_sha is None:
    add("deployed release SHA missing or invalid")


backup_epoch: float | None = None
if backup_artifact_raw:
    backup_artifact = Path(backup_artifact_raw)
    try:
        artifact_stat = backup_artifact.lstat()
    except OSError:
        artifact_stat = None
    if artifact_stat is not None and (
        stat.S_ISREG(artifact_stat.st_mode) or stat.S_ISDIR(artifact_stat.st_mode)
    ):
        backup_epoch = artifact_stat.st_mtime
else:
    backup_dir = Path(backup_dir_raw)
    try:
        backup_mode = backup_dir.lstat().st_mode
        if not stat.S_ISDIR(backup_mode):
            raise OSError
        candidates = []
        for candidate in backup_dir.iterdir():
            if not re.fullmatch(r"20[0-9]{6}T[0-9]{6}Z", candidate.name):
                continue
            try:
                candidate_mode = candidate.lstat().st_mode
            except OSError:
                continue
            if not stat.S_ISDIR(candidate_mode) or not safe_regular(
                candidate / "SHA256SUMS"
            ):
                continue
            try:
                created = datetime.strptime(
                    candidate.name, "%Y%m%dT%H%M%SZ"
                ).replace(tzinfo=UTC)
            except ValueError:
                continue
            candidates.append(created.timestamp())
        if candidates:
            backup_epoch = max(candidates)
    except OSError:
        backup_epoch = None
if backup_epoch is None:
    add("backup artifact missing")
elif backup_epoch > now + max_future_skew:
    add("backup artifact timestamp is in the future")
elif now - backup_epoch > max_backup:
    add("backup artifact stale")


restore_receipt = Path(restore_receipt_raw)
checked_at: float | None = None
if secure_restore_receipt(restore_receipt):
    try:
        if restore_receipt.stat().st_size > 64 * 1024:
            raise ValueError
        fields: dict[str, str] = {}
        for line in restore_receipt.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if not separator or not key or key in fields:
                raise ValueError
            fields[key] = value
        required_passes = {
            "database_restore": "pass",
            "state_archive_restore": "pass",
            "api_data_archive_restore": "pass",
            "research_only": "true",
            "can_publish": "false",
            "can_execute": "false",
        }
        if fields.get("schema") != "seiche.market-backup-restore-check.v2":
            raise ValueError
        if any(fields.get(key) != value for key, value in required_passes.items()):
            raise ValueError
        if re.fullmatch(r"20[0-9]{6}T[0-9]{6}Z", fields.get("snapshot", "")) is None:
            raise ValueError
        if re.fullmatch(r"[0-9a-f]{40}", fields.get("deployed_sha", "")) is None:
            raise ValueError
        count_shape = r"[0-9]+\|[0-9]+\|[0-9]+\|[0-9]+"
        if re.fullmatch(count_shape, fields.get("critical_table_counts", "")) is None:
            raise ValueError
        if re.fullmatch(count_shape, fields.get("critical_table_count_floor", "")) is None:
            raise ValueError
        checked_at = timestamp(fields.get("checked_at"))
        if (
            current_deployed_sha is not None
            and fields["deployed_sha"] != current_deployed_sha
        ):
            add("restore receipt belongs to a different release")
    except (OSError, UnicodeError, ValueError):
        checked_at = None
if checked_at is None:
    add("restore receipt missing or invalid")
elif checked_at > now + max_future_skew:
    add("restore receipt timestamp is in the future")
elif now - checked_at > max_restore:
    add("restore receipt stale")

for reason in reasons:
    print(reason)
raise SystemExit(1 if reasons else 0)
PY
    )
    VALIDATION_STATUS=$?
    if [ "$VALIDATION_STATUS" -ne 0 ]; then
        RECOGNIZED_REASON=0
        while IFS= read -r reason; do
            case "$reason" in
                "API health JSON invalid"|\
                "API health generated_at invalid"|\
                "API health generated_at is in the future"|\
                "API snapshot stale"|\
                "collector heartbeat unhealthy"|\
                "API health reports critical faults"|\
                "backup artifact missing"|\
                "backup artifact timestamp is in the future"|\
                "backup artifact stale"|\
                "restore receipt missing or invalid"|\
                "deployed release SHA missing or invalid"|\
                "restore receipt belongs to a different release"|\
                "restore receipt timestamp is in the future"|\
                "restore receipt stale")
                    add_failure "$reason"
                    RECOGNIZED_REASON=1
                    ;;
                '') ;;
                *) add_failure "data readiness validation failed" ;;
            esac
        done <<<"$VALIDATION_OUTPUT"
        if [ "$RECOGNIZED_REASON" -eq 0 ] && [ -z "$VALIDATION_OUTPUT" ]; then
            add_failure "data readiness validation failed"
        fi
    fi

    if [ "$PROOF_ONLY" -eq 0 ]; then
    for unit in $REQUIRED_UNITS; do
        case "$unit" in
            *[!A-Za-z0-9_.@:-]*|'')
                add_failure "configuration invalid"
                continue
                ;;
        esac
        if [ "${#unit}" -gt 128 ]; then
            add_failure "configuration invalid"
            continue
        fi
        if ! "$SYSTEMCTL_BIN" is-active --quiet "$unit" >/dev/null 2>&1; then
            add_failure "required unit inactive: $unit"
        fi
        case "$unit" in
            *.timer)
                if ! "$SYSTEMCTL_BIN" is-enabled --quiet "$unit" \
                        >/dev/null 2>&1; then
                    add_failure "required timer disabled: $unit"
                fi
                ;;
        esac
    done

    for disk_path in $DISK_PATHS; do
        case "$disk_path" in
            /*) ;;
            *) add_failure "configuration invalid"; continue ;;
        esac
        for df_mode in blocks inodes; do
            if [ "$df_mode" = "blocks" ]; then
                DF_OUTPUT=$("$DF_BIN" -P -- "$disk_path" 2>/dev/null) || DF_OUTPUT=""
            else
                DF_OUTPUT=$("$DF_BIN" -Pi -- "$disk_path" 2>/dev/null) || DF_OUTPUT=""
            fi
            if [ -z "$DF_OUTPUT" ]; then
                add_failure "filesystem usage unavailable"
                continue
            fi
            DF_LINE=${DF_OUTPUT##*$'\n'}
            read -r _filesystem _total _used _available DF_PERCENT _mount \
                <<<"$DF_LINE"
            DF_PERCENT=${DF_PERCENT%%%}
            case "$DF_PERCENT" in
                ''|*[!0-9]*)
                    add_failure "filesystem usage unavailable"
                    continue
                    ;;
            esac
            if [ "$DF_PERCENT" -ge "$DISK_CRITICAL_PERCENT" ]; then
                if [ "$df_mode" = "blocks" ]; then
                    add_failure "disk usage critical"
                else
                    add_failure "inode usage critical"
                fi
            fi
        done
    done
    fi
fi

if [ "${#FAILURES[@]}" -gt 0 ]; then
    for reason in "${FAILURES[@]}"; do
        printf 'seiche data readiness: %s\n' "$reason" >&2
    done
    exit 1
fi

printf 'seiche data readiness: ready\n'
