#!/bin/bash -p
# Fail closed when Seiche is serving stale data or its host safety rails drift.
set -uo pipefail
set -f
umask 0077
export LC_ALL=C

readonly DEFAULT_REQUIRED_UNITS="seiche-api.service seiche-market-worker.service seiche-source-worker.service seiche-market-backup.timer seiche-market-restore-check.timer seiche-market-validation.timer seiche-release-poll.timer seiche-data-readiness.timer"
readonly DEFAULT_DISK_PATHS="/ /var/lib/seiche /var/lib/seiche-nbs /var/backups/seiche-market"

HEALTH_URL="${SEICHE_DATA_READINESS_HEALTH_URL:-http://127.0.0.1:8787/api/health}"
BACKUP_DIR="${SEICHE_DATA_READINESS_BACKUP_DIR:-/var/backups/seiche-market}"
BACKUP_ARTIFACT="${SEICHE_DATA_READINESS_BACKUP_ARTIFACT:-}"
RESTORE_RECEIPT="${SEICHE_DATA_READINESS_RESTORE_RECEIPT:-/var/lib/seiche-recovery-proof/backup-restore-check.status}"
DEPLOYED_SHA_PATH="${SEICHE_DATA_READINESS_DEPLOYED_SHA_PATH:-/var/lib/seiche-deploy/deployed-sha}"
OFFSITE_ENV_FILE="${SEICHE_DATA_READINESS_OFFSITE_ENV_FILE:-/etc/seiche/offsite-backup.env}"
OFFSITE_STATUS_PATH="${SEICHE_DATA_READINESS_OFFSITE_STATUS_PATH:-/var/lib/seiche-offsite-backup/status.json}"
RECEIPT_UID="${SEICHE_DATA_READINESS_RECEIPT_UID:-0}"
RECEIPT_GROUP="${SEICHE_DATA_READINESS_RECEIPT_GROUP:-seiche}"
OFFSITE_UID="${SEICHE_DATA_READINESS_OFFSITE_UID:-0}"
OFFSITE_GID="${SEICHE_DATA_READINESS_OFFSITE_GID:-0}"
PROOF_ONLY="${SEICHE_DATA_READINESS_PROOF_ONLY:-0}"
SKIP_OFFSITE="${SEICHE_DATA_READINESS_SKIP_OFFSITE:-0}"
MAX_GENERATED_AGE="${SEICHE_DATA_READINESS_MAX_GENERATED_AGE_SECONDS:-900}"
MAX_BACKUP_AGE="${SEICHE_DATA_READINESS_BACKUP_MAX_AGE_SECONDS:-129600}"
MAX_RESTORE_AGE="${SEICHE_DATA_READINESS_RESTORE_MAX_AGE_SECONDS:-691200}"
MAX_OFFSITE_AGE="${SEICHE_DATA_READINESS_OFFSITE_MAX_AGE_SECONDS:-129600}"
MAX_FUTURE_SKEW="${SEICHE_DATA_READINESS_MAX_FUTURE_SKEW_SECONDS:-300}"
DISK_CRITICAL_PERCENT="${SEICHE_DATA_READINESS_DISK_CRITICAL_PERCENT:-90}"
CURL_TIMEOUT="${SEICHE_DATA_READINESS_CURL_TIMEOUT_SECONDS:-10}"
NOW_EPOCH="${SEICHE_DATA_READINESS_NOW_EPOCH:-}"
REQUIRED_UNITS="${SEICHE_DATA_READINESS_REQUIRED_UNITS-$DEFAULT_REQUIRED_UNITS}"
DISK_PATHS="${SEICHE_DATA_READINESS_DISK_PATHS-$DEFAULT_DISK_PATHS}"

configuration_invalid() {
    printf 'seiche data readiness: configuration invalid\n' >&2
    exit 1
}

# The installed service is a root trust boundary. Never let inherited or
# file-sourced environment turn its command paths into arbitrary root code.
# Host-free tests run unprivileged and must opt into their explicit fakes.
if [ "$EUID" -eq 0 ]; then
    if [ "${SEICHE_ALLOW_NON_ROOT_READINESS_TEST+x}" = x ] \
        || [ "${SEICHE_CURL_BIN+x}" = x ] \
        || [ "${SEICHE_PYTHON_BIN+x}" = x ] \
        || [ "${SEICHE_SYSTEMCTL_BIN+x}" = x ] \
        || [ "${SEICHE_DF_BIN+x}" = x ] \
        || [ "${SEICHE_MKTEMP_BIN+x}" = x ] \
        || [ "${SEICHE_RM_BIN+x}" = x ]; then
        configuration_invalid
    fi
    CURL_BIN=/usr/bin/curl
    PYTHON_BIN=/usr/bin/python3
    SYSTEMCTL_BIN=/usr/bin/systemctl
    DF_BIN=/usr/bin/df
    MKTEMP_BIN=/usr/bin/mktemp
    RM_BIN=/usr/bin/rm
else
    [ "${SEICHE_ALLOW_NON_ROOT_READINESS_TEST:-}" = 1 ] \
        || configuration_invalid
    CURL_BIN="${SEICHE_CURL_BIN:-}"
    PYTHON_BIN="${SEICHE_PYTHON_BIN:-}"
    SYSTEMCTL_BIN="${SEICHE_SYSTEMCTL_BIN:-}"
    DF_BIN="${SEICHE_DF_BIN:-}"
    MKTEMP_BIN="${SEICHE_MKTEMP_BIN:-}"
    RM_BIN="${SEICHE_RM_BIN:-}"
fi
readonly CURL_BIN PYTHON_BIN SYSTEMCTL_BIN DF_BIN MKTEMP_BIN RM_BIN

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
    "$MAX_OFFSITE_AGE" \
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
case "$SKIP_OFFSITE" in
    0|1) ;;
    *) add_failure "configuration invalid"; CONFIG_VALID=0 ;;
esac
case "$RECEIPT_UID" in
    ''|*[!0-9]*) add_failure "configuration invalid"; CONFIG_VALID=0 ;;
esac
case "$OFFSITE_UID" in
    ''|*[!0-9]*) add_failure "configuration invalid"; CONFIG_VALID=0 ;;
esac
case "$OFFSITE_GID" in
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
for path in "$OFFSITE_ENV_FILE" "$OFFSITE_STATUS_PATH"; do
    case "$path" in
        /) add_failure "configuration invalid"; CONFIG_VALID=0 ;;
        /*) ;;
        *) add_failure "configuration invalid"; CONFIG_VALID=0 ;;
    esac
done
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
        "$PYTHON_BIN" -I -B - "$HEALTH_FILE" "$HEALTH_AVAILABLE" "$BACKUP_DIR" \
            "$BACKUP_ARTIFACT" "$RESTORE_RECEIPT" "$DEPLOYED_SHA_PATH" \
            "$RECEIPT_UID" "$RECEIPT_GROUP" \
            "$MAX_GENERATED_AGE" "$MAX_BACKUP_AGE" "$MAX_RESTORE_AGE" \
            "$MAX_OFFSITE_AGE" "$MAX_FUTURE_SKEW" "$NOW_EPOCH" \
            "$OFFSITE_ENV_FILE" "$OFFSITE_STATUS_PATH" \
            "$OFFSITE_UID" "$OFFSITE_GID" "$SKIP_OFFSITE" 2>/dev/null <<'PY'
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
    max_offsite_raw,
    max_future_skew_raw,
    now_raw,
    offsite_env_raw,
    offsite_status_raw,
    offsite_uid_raw,
    offsite_gid_raw,
    skip_offsite_raw,
) = sys.argv[1:]
health_available = health_available_raw == "1"
max_generated = int(max_generated_raw)
max_backup = int(max_backup_raw)
max_restore = int(max_restore_raw)
max_offsite = int(max_offsite_raw)
max_future_skew = int(max_future_skew_raw)
now = float(now_raw) if now_raw else time.time()
receipt_uid = int(receipt_uid_raw)
offsite_uid = int(offsite_uid_raw)
offsite_gid = int(offsite_gid_raw)
skip_offsite = skip_offsite_raw == "1"
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
        required_fields = {
            "schema",
            "checked_at",
            "snapshot",
            "source_backup_schema",
            "deployed_sha",
            "critical_table_counts",
            "critical_table_count_floor",
            "nbs_full_store_audit_contract",
            "nbs_full_store_audit_result",
            "nbs_public_revision_store",
            "palimpsest_china_state_archive_restore",
            "palimpsest_china_state_audit_contract",
            "palimpsest_china_state_tree_sha256",
            "palimpsest_china_active_activation_id",
            "palimpsest_china_pending_candidate_activation_id",
            "palimpsest_china_bundle_count",
            "palimpsest_china_receipt_count",
            *required_passes,
        }
        if set(fields) != required_fields:
            raise ValueError
        if fields.get("schema") != "seiche.market-backup-restore-check.v5":
            raise ValueError
        if any(fields.get(key) != value for key, value in required_passes.items()):
            raise ValueError
        if (
            fields.get("nbs_full_store_audit_contract")
            != "seiche.nbs-full-store-audit.v1"
        ):
            raise ValueError
        nbs_audit_result = fields.get("nbs_full_store_audit_result")
        if nbs_audit_result not in {
            "not_onboarded",
            "verified_head",
        }:
            raise ValueError
        if fields.get("nbs_public_revision_store") != nbs_audit_result:
            raise ValueError
        if (
            fields.get("palimpsest_china_state_audit_contract")
            != "seiche.palimpsest-china-activation-state.v1"
        ):
            raise ValueError
        source_backup_schema = fields.get("source_backup_schema")
        palimpsest_result = fields.get("palimpsest_china_state_archive_restore")
        palimpsest_tree = fields.get("palimpsest_china_state_tree_sha256")
        active_id = fields.get("palimpsest_china_active_activation_id")
        pending_id = fields.get("palimpsest_china_pending_candidate_activation_id")
        if source_backup_schema == "seiche.market-backup.v3":
            if (
                palimpsest_result != "legacy_absent_inactive"
                or palimpsest_tree != "absent"
                or active_id != "none"
                or pending_id != "none"
                or fields.get("palimpsest_china_bundle_count") != "0"
                or fields.get("palimpsest_china_receipt_count") != "0"
            ):
                raise ValueError
        elif source_backup_schema == "seiche.market-backup.v4":
            if (
                palimpsest_result != "verified"
                or re.fullmatch(r"[0-9a-f]{64}", palimpsest_tree or "") is None
                or re.fullmatch(r"(?:none|[0-9a-f]{64})", active_id or "") is None
                or re.fullmatch(r"(?:none|[0-9a-f]{64})", pending_id or "")
                is None
                or re.fullmatch(
                    r"[0-9]+", fields.get("palimpsest_china_bundle_count", "")
                )
                is None
                or re.fullmatch(
                    r"[0-9]+", fields.get("palimpsest_china_receipt_count", "")
                )
                is None
            ):
                raise ValueError
        else:
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


def secure_private_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == offsite_uid
        and metadata.st_gid == offsite_gid
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1
    )


def secure_status_file(path: Path) -> bool:
    try:
        parent = path.parent.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(parent.st_mode)
        and parent.st_uid == offsite_uid
        and parent.st_gid == offsite_gid
        and stat.S_IMODE(parent.st_mode) == 0o700
        and secure_private_file(path)
    )


def bounded_version(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(
        r"[A-Za-z0-9._~+/=-]{1,1024}", value
    ) is not None


offsite_env = Path(offsite_env_raw)
offsite_status = Path(offsite_status_raw)
try:
    offsite_env_exists = offsite_env.exists() or offsite_env.is_symlink()
except OSError:
    offsite_env_exists = True

if not skip_offsite and offsite_env_exists:
    settings: dict[str, str] = {}
    expected_settings = {
        "SEICHE_OFFSITE_BACKUP_BUCKET",
        "SEICHE_OFFSITE_BACKUP_PREFIX",
        "SEICHE_OFFSITE_BACKUP_RCLONE_REMOTE",
        "SEICHE_OFFSITE_BACKUP_WRITE_ENABLED",
        "SEICHE_OFFSITE_BACKUP_CANARY",
        "SEICHE_OFFSITE_BACKUP_KEY_ID",
        "SEICHE_OFFSITE_BACKUP_DESTINATION_ID",
        "SEICHE_OFFSITE_BACKUP_RETENTION_MODE",
        "SEICHE_OFFSITE_BACKUP_RETENTION_DAYS",
    }
    try:
        if not secure_private_file(offsite_env) or offsite_env.stat().st_size > 8192:
            raise ValueError
        for line in offsite_env.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if not separator or not key or key in settings:
                raise ValueError
            settings[key] = value
        if (
            set(settings) != expected_settings
            or re.fullmatch(
                r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]",
                settings["SEICHE_OFFSITE_BACKUP_BUCKET"],
            )
            is None
            or re.fullmatch(
                r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*",
                settings["SEICHE_OFFSITE_BACKUP_PREFIX"],
            )
            is None
            or ".." in settings["SEICHE_OFFSITE_BACKUP_PREFIX"]
            or settings["SEICHE_OFFSITE_BACKUP_RCLONE_REMOTE"] != "anchor"
            or settings["SEICHE_OFFSITE_BACKUP_CANARY"] not in {"0", "1"}
            or settings["SEICHE_OFFSITE_BACKUP_WRITE_ENABLED"] != "1"
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}",
                settings["SEICHE_OFFSITE_BACKUP_KEY_ID"],
            )
            is None
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}",
                settings["SEICHE_OFFSITE_BACKUP_DESTINATION_ID"],
            )
            is None
            or settings["SEICHE_OFFSITE_BACKUP_RETENTION_MODE"] != "COMPLIANCE"
            or settings["SEICHE_OFFSITE_BACKUP_RETENTION_DAYS"] != "90"
        ):
            raise ValueError
    except (OSError, UnicodeError, ValueError):
        add("offsite backup configuration invalid")
    else:
        if settings["SEICHE_OFFSITE_BACKUP_CANARY"] == "0":
            verified_at: float | None = None
            try:
                if (
                    not secure_status_file(offsite_status)
                    or offsite_status.stat().st_size > 256 * 1024
                ):
                    raise ValueError
                status_document = json.loads(
                    offsite_status.read_text(encoding="utf-8")
                )
                if not isinstance(status_document, dict):
                    raise ValueError
                success = status_document.get("last_success")
                if not isinstance(success, dict):
                    raise ValueError
                destination = success.get("destination")
                current_destination = status_document.get("destination")
                if not isinstance(destination, dict) or not isinstance(
                    current_destination, dict
                ):
                    raise ValueError
                version_fields = (
                    "ciphertext_version_id",
                    "checksum_version_id",
                    "remote_receipt_version_id",
                )
                receipt_key = success.get("remote_receipt_key")
                prefix = settings["SEICHE_OFFSITE_BACKUP_PREFIX"]
                snapshot_id = success.get("snapshot_id")
                attempt_id = success.get("attempt_id")
                canary_receipt = f"{prefix}/canary/v1/RECEIPT.json"
                scheduled_receipt = (
                    f"{prefix}/snapshots/{snapshot_id}/attempts/"
                    f"{attempt_id}/RECEIPT.json"
                )
                etag_fields = (
                    "ciphertext_etag",
                    "checksum_etag",
                    "remote_receipt_etag",
                )
                legacy_source_backup_contract = {
                    "source_backup_schema": "seiche.market-backup.v3",
                    "nbs_state_root": "/var/lib/seiche-nbs",
                    "nbs_full_store_audit_contract": (
                        "seiche.nbs-full-store-audit.v1"
                    ),
                    "nbs_full_store_audit_result": "required_at_restore",
                }
                modern_source_backup_contract = {
                    **legacy_source_backup_contract,
                    "source_backup_schema": "seiche.market-backup.v4",
                    "palimpsest_china_state_root": (
                        "/var/lib/seiche-palimpsest-china"
                    ),
                    "palimpsest_china_state_audit_contract": (
                        "seiche.palimpsest-china-activation-state.v1"
                    ),
                }
                status_schema = status_document.get("schema")

                def source_contract_valid(record: dict[str, object]) -> bool:
                    if status_schema == "seiche.market-offsite-backup-status.v2":
                        return all(
                            record.get(field) == value
                            for field, value in legacy_source_backup_contract.items()
                        ) and not any(
                            field.startswith("palimpsest_china_") for field in record
                        )
                    if status_schema != "seiche.market-offsite-backup-status.v3":
                        return False
                    return (
                        all(
                            record.get(field) == value
                            for field, value in modern_source_backup_contract.items()
                        )
                        and re.fullmatch(
                            r"[0-9a-f]{64}",
                            str(record.get("palimpsest_china_state_tree_sha256", "")),
                        )
                        is not None
                        and record.get("palimpsest_china_state")
                        in {"active", "inactive"}
                    )

                current_state = status_document.get("status")
                valid = (
                    status_schema
                    in {
                        "seiche.market-offsite-backup-status.v2",
                        "seiche.market-offsite-backup-status.v3",
                    }
                    and current_state in {"running", "failed", "success"}
                    and status_document.get("provider")
                    == "hetzner-object-storage"
                    and status_document.get("bucket")
                    == settings["SEICHE_OFFSITE_BACKUP_BUCKET"]
                    and status_document.get("prefix") == prefix
                    and status_document.get("key_id")
                    == settings["SEICHE_OFFSITE_BACKUP_KEY_ID"]
                    and current_destination.get("id")
                    == settings["SEICHE_OFFSITE_BACKUP_DESTINATION_ID"]
                    and current_destination.get("bucket")
                    == settings["SEICHE_OFFSITE_BACKUP_BUCKET"]
                    and current_destination.get("prefix") == prefix
                    and re.fullmatch(
                        r"https://[a-z0-9.-]+",
                        current_destination.get("endpoint", ""),
                    )
                    is not None
                    and re.fullmatch(
                        r"[A-Za-z0-9_-]+",
                        current_destination.get("region", ""),
                    )
                    is not None
                    and status_document.get("object_lock")
                    == {"mode": "COMPLIANCE", "days": 90}
                    and source_contract_valid(status_document)
                    and status_document.get("restore_verified")
                    is (current_state == "success")
                    and success.get("restore_verified") is True
                    and success.get("bucket")
                    == settings["SEICHE_OFFSITE_BACKUP_BUCKET"]
                    and success.get("prefix")
                    == settings["SEICHE_OFFSITE_BACKUP_PREFIX"]
                    and success.get("key_id")
                    == settings["SEICHE_OFFSITE_BACKUP_KEY_ID"]
                    and destination.get("id")
                    == settings["SEICHE_OFFSITE_BACKUP_DESTINATION_ID"]
                    and destination.get("bucket")
                    == settings["SEICHE_OFFSITE_BACKUP_BUCKET"]
                    and destination.get("prefix") == prefix
                    and destination.get("endpoint")
                    == current_destination.get("endpoint")
                    and destination.get("region")
                    == current_destination.get("region")
                    and success.get("object_lock")
                    == {"mode": "COMPLIANCE", "days": 90}
                    and source_contract_valid(success)
                    and re.fullmatch(r"20[0-9]{6}T[0-9]{6}Z", snapshot_id or "")
                    is not None
                    and re.fullmatch(
                        r"20[0-9]{6}T[0-9]{6}Z-[0-9]+", attempt_id or ""
                    )
                    is not None
                    and re.fullmatch(
                        r"[0-9a-f]{40}", success.get("source_revision", "")
                    )
                    is not None
                    and re.fullmatch(
                        r"[0-9a-f]{64}", success.get("ciphertext_sha256", "")
                    )
                    is not None
                    and re.fullmatch(
                        r"[0-9a-f]{64}",
                        success.get("source_inventory_sha256", ""),
                    )
                    is not None
                    and re.fullmatch(
                        r"[0-9a-f]{64}",
                        success.get("source_content_set_sha256", ""),
                    )
                    is not None
                    and isinstance(success.get("ciphertext_bytes"), int)
                    and success["ciphertext_bytes"] > 0
                    and receipt_key in {canary_receipt, scheduled_receipt}
                    and all(bounded_version(success.get(field)) for field in version_fields)
                    and all(
                        isinstance(success.get(field), str)
                        and re.fullmatch(r'"[A-Fa-f0-9-]{16,128}"', success[field])
                        is not None
                        for field in etag_fields
                    )
                )
                verified_at = timestamp(success.get("verified_at"))
                if not valid or verified_at is None:
                    raise ValueError
            except (OSError, UnicodeError, ValueError):
                add("offsite backup proof missing or invalid")
            else:
                if verified_at > now + max_future_skew:
                    add("offsite backup proof timestamp is in the future")
                elif now - verified_at > max_offsite:
                    add("offsite backup proof stale")

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
                "restore receipt stale"|\
                "offsite backup configuration invalid"|\
                "offsite backup proof missing or invalid"|\
                "offsite backup proof timestamp is in the future"|\
                "offsite backup proof stale")
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
