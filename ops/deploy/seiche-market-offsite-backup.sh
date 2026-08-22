#!/usr/bin/env bash
# shellcheck disable=SC2016  # Literal awk programs must not expand in the shell.
# Encrypt one completed local market snapshot, upload it append-only, and
# prove that the downloaded ciphertext restores to the source hashes.
set -Eeuo pipefail
umask 0077

BACKUP_ROOT="${SEICHE_MARKET_BACKUP_DIR:-/var/backups/seiche-market}"
WORK_ROOT="${SEICHE_OFFSITE_BACKUP_WORK_DIR:-/var/cache/seiche-market-offsite-backup}"
STATUS_PATH="${SEICHE_OFFSITE_BACKUP_STATUS_PATH:-/var/lib/seiche-offsite-backup/status.json}"
CONFIG_ENV_FILE="${SEICHE_OFFSITE_BACKUP_ENV_FILE:-/etc/seiche/offsite-backup.env}"
PASSPHRASE_FILE="${SEICHE_OFFSITE_BACKUP_PASSPHRASE_FILE:-/etc/seiche/offsite-backup.passphrase}"
CREDENTIAL_ENV_FILE="${SEICHE_OFFSITE_BACKUP_CREDENTIAL_ENV_FILE:-/root/.config/anchor/object-storage.env}"
DEPLOYED_SHA_PATH="${SEICHE_DEPLOYED_SHA_PATH:-/var/lib/seiche-deploy/deployed-sha}"
RELEASE_SHA="${SEICHE_RELEASE_SHA:-}"
LOCK_PATH="${SEICHE_OFFSITE_BACKUP_LOCK_PATH:-/run/lock/seiche-market-backup.lock}"
RUN_LOCK_PATH="${SEICHE_OFFSITE_BACKUP_RUN_LOCK_PATH:-/run/lock/seiche-market-offsite-backup.lock}"
BUCKET="${SEICHE_OFFSITE_BACKUP_BUCKET:-}"
PREFIX="${SEICHE_OFFSITE_BACKUP_PREFIX:-seiche/market-backups/v1}"
RCLONE_REMOTE="${SEICHE_OFFSITE_BACKUP_RCLONE_REMOTE:-anchor}"
WRITE_ENABLED="${SEICHE_OFFSITE_BACKUP_WRITE_ENABLED:-0}"
CANARY="${SEICHE_OFFSITE_BACKUP_CANARY:-1}"
KEY_ID="${SEICHE_OFFSITE_BACKUP_KEY_ID:-}"
DESTINATION_ID="${SEICHE_OFFSITE_BACKUP_DESTINATION_ID:-}"
RETENTION_MODE="${SEICHE_OFFSITE_BACKUP_RETENTION_MODE:-COMPLIANCE}"
RETENTION_DAYS="${SEICHE_OFFSITE_BACKUP_RETENTION_DAYS:-90}"
MIN_FREE_MB="${SEICHE_OFFSITE_BACKUP_MIN_FREE_MB:-1024}"
MAX_SNAPSHOT_BYTES="${SEICHE_OFFSITE_BACKUP_MAX_SNAPSHOT_BYTES:-26843545600}"
MAX_SNAPSHOT_AGE_SECONDS="${SEICHE_OFFSITE_BACKUP_MAX_SNAPSHOT_AGE_SECONDS:-129600}"
ALLOW_NON_ROOT_TEST="${SEICHE_OFFSITE_ALLOW_NON_ROOT_TEST:-0}"

AWK_BIN="${SEICHE_OFFSITE_AWK_BIN:-awk}"
CURL_BIN="${SEICHE_OFFSITE_CURL_BIN:-curl}"
DATE_BIN="${SEICHE_OFFSITE_DATE_BIN:-date}"
DF_BIN="${SEICHE_OFFSITE_DF_BIN:-df}"
FLOCK_BIN="${SEICHE_OFFSITE_FLOCK_BIN:-flock}"
GPG_BIN="${SEICHE_OFFSITE_GPG_BIN:-gpg}"
PYTHON_BIN="${SEICHE_OFFSITE_PYTHON_BIN:-python3}"
RCLONE_BIN="${SEICHE_OFFSITE_RCLONE_BIN:-rclone}"
SHA256SUM_BIN="${SEICHE_OFFSITE_SHA256SUM_BIN:-sha256sum}"
TAR_BIN="${SEICHE_OFFSITE_TAR_BIN:-tar}"

log() {
    printf 'seiche market offsite backup: %s\n' "$*" >&2
}

fail() {
    log "$*"
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 \
        || fail "required command is unavailable: $1"
}

require_absolute_nonroot_path() {
    local label="$1" value="$2"
    case "$value" in
        /*) [ "$value" != / ] || fail "$label cannot be the filesystem root" ;;
        *) fail "$label must be absolute" ;;
    esac
}

for command_name in basename chmod cmp dirname grep mkdir mktemp mountpoint mv \
        realpath rm stat tr "$AWK_BIN" "$CURL_BIN" "$DATE_BIN" "$DF_BIN" "$FLOCK_BIN" \
        "$GPG_BIN" "$PYTHON_BIN" "$RCLONE_BIN" \
        "$SHA256SUM_BIN" "$TAR_BIN"; do
    require_command "$command_name"
done
for specification in \
        "backup root:$BACKUP_ROOT" \
        "work root:$WORK_ROOT" \
        "status path:$STATUS_PATH" \
        "configuration path:$CONFIG_ENV_FILE" \
        "passphrase path:$PASSPHRASE_FILE" \
        "credential path:$CREDENTIAL_ENV_FILE" \
        "deployed SHA path:$DEPLOYED_SHA_PATH" \
        "backup lock path:$LOCK_PATH" \
        "offsite run lock path:$RUN_LOCK_PATH"; do
    label=${specification%%:*}
    value=${specification#*:}
    require_absolute_nonroot_path "$label" "$value"
done
[ "$LOCK_PATH" != "$RUN_LOCK_PATH" ] \
    || fail "local backup and offsite run locks must be distinct"
printf '%s' "$RELEASE_SHA" | grep -Eq '^[0-9a-f]{40}$' \
    || fail "controller release SHA is missing or malformed"

if [ "${EUID:-$($PYTHON_BIN -c 'import os; print(os.geteuid())')}" -ne 0 ] \
        && [ "$ALLOW_NON_ROOT_TEST" != 1 ]; then
    fail "must run as root"
fi
[ "$WRITE_ENABLED" = 1 ] || fail "offsite writes are not explicitly enabled"
case "$CANARY" in
    0|1) ;;
    *) fail "canary must be 0 or 1" ;;
esac
printf '%s' "$KEY_ID" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$' \
    || fail "encryption key ID is missing or malformed"
printf '%s' "$DESTINATION_ID" \
    | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$' \
    || fail "destination ID is missing or malformed"
[ "$RETENTION_MODE" = COMPLIANCE ] \
    || fail "retention mode must remain COMPLIANCE"
[ "$RETENTION_DAYS" = 90 ] \
    || fail "retention duration must remain exactly 90 days"
case "$MIN_FREE_MB" in
    ''|*[!0-9]*) fail "minimum free MB must be an integer" ;;
esac
[ "$MIN_FREE_MB" -ge 256 ] || fail "minimum free MB must be at least 256"
case "$MAX_SNAPSHOT_BYTES" in
    ''|*[!0-9]*) fail "maximum snapshot bytes must be an integer" ;;
esac
[ "$MAX_SNAPSHOT_BYTES" -ge 1048576 ] \
    || fail "maximum snapshot bytes is implausibly small"
case "$MAX_SNAPSHOT_AGE_SECONDS" in
    ''|*[!0-9]*) fail "maximum snapshot age must be an integer" ;;
esac
[ "$MAX_SNAPSHOT_AGE_SECONDS" -ge 21600 ] \
    || fail "maximum snapshot age must be at least six hours"
printf '%s' "$BUCKET" | grep -Eq '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$' \
    || fail "Object Storage bucket is missing or malformed"
printf '%s' "$PREFIX" \
    | grep -Eq '^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$' \
    || fail "Object Storage prefix is unsafe"
case "$PREFIX" in
    *..*) fail "Object Storage prefix cannot contain dot-dot" ;;
esac
[ "$RCLONE_REMOTE" = anchor ] \
    || fail "rclone remote must match the reviewed anchor credential"

for path in "$CONFIG_ENV_FILE" "$PASSPHRASE_FILE" \
        "$CREDENTIAL_ENV_FILE" "$DEPLOYED_SHA_PATH"; do
    [ -f "$path" ] && [ ! -L "$path" ] \
        || fail "required root-controlled file is missing or unsafe: $path"
done
if [ "$ALLOW_NON_ROOT_TEST" != 1 ]; then
    [ "$(stat -c '%u:%g:%a:%h' "$CONFIG_ENV_FILE")" = 0:0:600:1 ] \
        || fail "offsite environment file ownership or mode is unsafe"
    [ "$(stat -c '%u:%g:%a:%h' "$PASSPHRASE_FILE")" = 0:0:400:1 ] \
        || fail "offsite passphrase ownership or mode is unsafe"
    [ "$(stat -c '%u:%g:%a:%h' "$CREDENTIAL_ENV_FILE")" = 0:0:600:1 ] \
        || fail "shared Object Storage credential ownership or mode is unsafe"
fi
"$PYTHON_BIN" - "$PASSPHRASE_FILE" <<'PY' \
    || fail "passphrase must be one canonical 32-4096 byte line"
from pathlib import Path
import sys

body = Path(sys.argv[1]).read_bytes()
if not body.endswith(b"\n") or body.count(b"\n") != 1:
    raise SystemExit(1)
value = body[:-1]
if not 32 <= len(value) <= 4096 or b"\r" in value or b"\0" in value:
    raise SystemExit(1)
PY

for variable_name in \
        RCLONE_CONFIG_ANCHOR_ACCESS_KEY_ID \
        RCLONE_CONFIG_ANCHOR_SECRET_ACCESS_KEY \
        RCLONE_CONFIG_ANCHOR_ENDPOINT \
        RCLONE_CONFIG_ANCHOR_REGION \
        RCLONE_CONFIG_ANCHOR_TYPE \
        RCLONE_CONFIG_ANCHOR_PROVIDER; do
    [ -n "${!variable_name:-}" ] \
        || fail "required Object Storage setting is absent: $variable_name"
done
[ "$RCLONE_CONFIG_ANCHOR_TYPE" = s3 ] \
    || fail "shared Object Storage remote is not S3"
[ "$RCLONE_CONFIG_ANCHOR_PROVIDER" = Other ] \
    || fail "shared Object Storage provider is not explicit"
printf '%s' "$RCLONE_CONFIG_ANCHOR_ENDPOINT" \
    | grep -Eq '^https://[a-z0-9.-]+$' \
    || fail "Object Storage endpoint is unsafe"
printf '%s' "$RCLONE_CONFIG_ANCHOR_REGION" \
    | grep -Eq '^[A-Za-z0-9_-]+$' \
    || fail "Object Storage region is unsafe"
printf '%s' "$RCLONE_CONFIG_ANCHOR_ACCESS_KEY_ID" \
    | grep -Eq '^[A-Za-z0-9._/+=-]+$' \
    || fail "Object Storage access key contains unsupported characters"
printf '%s' "$RCLONE_CONFIG_ANCHOR_SECRET_ACCESS_KEY" \
    | grep -Eq '^[A-Za-z0-9._/+=-]+$' \
    || fail "Object Storage secret key contains unsupported characters"
if [ "${RCLONE_CONFIG_ANCHOR_ACL:-private}" != private ]; then
    fail "Object Storage credential does not require private objects"
fi
GPG_OPTIONS=$("$GPG_BIN" --dump-options) \
    || fail "GPG runtime options cannot be inspected"
grep -Fxq -- --force-aead <<<"$GPG_OPTIONS" \
    || fail "GPG runtime does not support authenticated AEAD encryption"
grep -Fxq -- --aead-algo <<<"$GPG_OPTIONS" \
    || fail "GPG runtime cannot select the reviewed AEAD algorithm"

[ -d "$BACKUP_ROOT" ] && [ ! -L "$BACKUP_ROOT" ] \
    || fail "local backup root is missing or symlinked"
BACKUP_ROOT=$(realpath -e -- "$BACKUP_ROOT")
STATUS_ROOT=$(dirname -- "$STATUS_PATH")
mkdir -p -- "$WORK_ROOT" "$STATUS_ROOT"
chmod 0700 "$WORK_ROOT" "$STATUS_ROOT"
[ -d "$WORK_ROOT" ] && [ ! -L "$WORK_ROOT" ] \
    || fail "work root is unsafe"
[ -d "$STATUS_ROOT" ] && [ ! -L "$STATUS_ROOT" ] \
    || fail "status root is unsafe"
WORK_ROOT=$(realpath -e -- "$WORK_ROOT")
STATUS_ROOT=$(realpath -e -- "$STATUS_ROOT")
case "$WORK_ROOT/" in
    "$BACKUP_ROOT/"*) fail "work root cannot be inside the backup root" ;;
esac
case "$BACKUP_ROOT/" in
    "$WORK_ROOT/"*) fail "backup root cannot be inside the work root" ;;
esac
if [ -e "$STATUS_PATH" ] || [ -L "$STATUS_PATH" ]; then
    [ -f "$STATUS_PATH" ] && [ ! -L "$STATUS_PATH" ] \
        || fail "status path is not a regular file"
    if [ "$ALLOW_NON_ROOT_TEST" != 1 ]; then
        [ "$(stat -c '%u:%g:%a:%h' "$STATUS_PATH")" = 0:0:600:1 ] \
            || fail "existing offsite status ownership or mode is unsafe"
    fi
fi

if [ -e "$RUN_LOCK_PATH" ] || [ -L "$RUN_LOCK_PATH" ]; then
    [ -f "$RUN_LOCK_PATH" ] && [ ! -L "$RUN_LOCK_PATH" ] \
        || fail "offsite run lock path is unsafe"
else
    : >"$RUN_LOCK_PATH"
    chmod 0600 "$RUN_LOCK_PATH"
fi
exec 8<"$RUN_LOCK_PATH"
if [ "$ALLOW_NON_ROOT_TEST" != 1 ]; then
    [ "$(stat -Lc '%u:%g:%a:%h' /proc/self/fd/8)" = 0:0:600:1 ] \
        || fail "offsite run lock ownership or mode is unsafe"
    RUN_LOCK_FD_ID=$(stat -Lc '%d:%i:%u:%g:%a:%F' /proc/self/fd/8)
    RUN_LOCK_PATH_ID=$(stat -Lc '%d:%i:%u:%g:%a:%F' "$RUN_LOCK_PATH")
    [ "$RUN_LOCK_FD_ID" = "$RUN_LOCK_PATH_ID" ] \
        || fail "opened offsite lock is not the reviewed lock inode"
fi
"$FLOCK_BIN" --exclusive --nonblock 8 \
    || fail "another offsite backup attempt is already active"

# A SIGKILL or host crash cannot run the EXIT trap. Once the exclusive run
# lock proves no attempt is live, remove only mktemp-shaped directories/files
# inside the two dedicated root-private roots. Refuse links and mountpoints.
for STALE_RUN in "$WORK_ROOT"/.run-*; do
    if [ ! -e "$STALE_RUN" ] && [ ! -L "$STALE_RUN" ]; then
        continue
    fi
    [ -d "$STALE_RUN" ] && [ ! -L "$STALE_RUN" ] \
        || fail "stale offsite run path is unsafe"
    [ "$(dirname -- "$(realpath -e -- "$STALE_RUN")")" = "$WORK_ROOT" ] \
        || fail "stale offsite run resolves outside the work root"
    basename -- "$STALE_RUN" \
        | grep -Eq '^\.run-20[0-9]{6}T[0-9]{6}Z-[0-9]+\.[A-Za-z0-9]+$' \
        || fail "stale offsite run name is unsafe"
    if mountpoint -q -- "$STALE_RUN"; then
        fail "refusing to clean a mounted stale offsite run"
    fi
    rm -rf -- "$STALE_RUN"
done
for STALE_STATUS in "$STATUS_ROOT"/.status.*; do
    if [ ! -e "$STALE_STATUS" ] && [ ! -L "$STALE_STATUS" ]; then
        continue
    fi
    [ -f "$STALE_STATUS" ] && [ ! -L "$STALE_STATUS" ] \
        || fail "stale offsite status path is unsafe"
    basename -- "$STALE_STATUS" \
        | grep -Eq '^\.status\.[A-Za-z0-9]+$' \
        || fail "stale offsite status name is unsafe"
    rm -f -- "$STALE_STATUS"
done

if [ -e "$LOCK_PATH" ] || [ -L "$LOCK_PATH" ]; then
    [ -f "$LOCK_PATH" ] && [ ! -L "$LOCK_PATH" ] \
        || fail "backup lock path is unsafe"
else
    : >"$LOCK_PATH"
    chmod 0600 "$LOCK_PATH"
fi
exec 9<"$LOCK_PATH"
if [ "$ALLOW_NON_ROOT_TEST" != 1 ]; then
    [ "$(stat -Lc '%u:%g:%a:%h' /proc/self/fd/9)" = 0:0:600:1 ] \
        || fail "backup lock ownership or mode is unsafe"
    LOCK_FD_ID=$(stat -Lc '%d:%i:%u:%g:%a:%F' /proc/self/fd/9)
    LOCK_PATH_ID=$(stat -Lc '%d:%i:%u:%g:%a:%F' "$LOCK_PATH")
    [ "$LOCK_FD_ID" = "$LOCK_PATH_ID" ] \
        || fail "opened backup lock is not the reviewed lock inode"
fi
"$FLOCK_BIN" --shared --timeout 300 9 \
    || fail "timed out waiting for the local backup lease"

SNAPSHOT_ID="${SEICHE_OFFSITE_BACKUP_SNAPSHOT_ID:-}"
if [ -n "$SNAPSHOT_ID" ]; then
    printf '%s' "$SNAPSHOT_ID" \
        | grep -Eq '^20[0-9]{6}T[0-9]{6}Z$' \
        || fail "requested snapshot ID is malformed"
else
    for candidate in "$BACKUP_ROOT"/20??????T??????Z; do
        [ -d "$candidate" ] && [ ! -L "$candidate" ] || continue
        candidate_name=$(basename -- "$candidate")
        printf '%s' "$candidate_name" \
            | grep -Eq '^20[0-9]{6}T[0-9]{6}Z$' || continue
        if [ -z "$SNAPSHOT_ID" ] || [ "$candidate_name" \> "$SNAPSHOT_ID" ]; then
            SNAPSHOT_ID="$candidate_name"
        fi
    done
fi
[ -n "$SNAPSHOT_ID" ] || fail "no completed local market snapshot exists"
SNAPSHOT_PATH="$BACKUP_ROOT/$SNAPSHOT_ID"
[ -d "$SNAPSHOT_PATH" ] && [ ! -L "$SNAPSHOT_PATH" ] \
    || fail "selected local snapshot is missing or unsafe"
[ "$(dirname -- "$(realpath -e -- "$SNAPSHOT_PATH")")" = "$BACKUP_ROOT" ] \
    || fail "selected snapshot resolves outside the backup root"

validate_snapshot() {
    "$PYTHON_BIN" - "$1" "$SNAPSHOT_ID" <<'PY'
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import sys

root = Path(sys.argv[1])
snapshot_id = sys.argv[2]
required = (
    "seiche.dump",
    "var-lib-seiche.tgz",
    "api-data.tgz",
    "table-counts.txt",
    "deployed-sha.txt",
    "manifest.env",
    "SHA256SUMS",
)
entries = sorted(path.name for path in root.iterdir())
if entries != sorted(required):
    raise SystemExit("snapshot file set is not the closed v2 contract")
for name in required:
    metadata = (root / name).lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise SystemExit(f"unsafe snapshot member: {name}")
metadata_limits = {
    "SHA256SUMS": 1024,
    "manifest.env": 4096,
    "deployed-sha.txt": 64,
    "table-counts.txt": 256,
}
for name, maximum in metadata_limits.items():
    if (root / name).stat().st_size > maximum:
        raise SystemExit(f"snapshot metadata is oversized: {name}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()

inventory_path = root / "SHA256SUMS"
inventory_bytes = inventory_path.read_bytes()
try:
    inventory_lines = inventory_bytes.decode("ascii").splitlines()
except UnicodeDecodeError as error:
    raise SystemExit("snapshot inventory is not ASCII") from error
hashed_names = required[:-1]
if len(inventory_lines) != len(hashed_names):
    raise SystemExit("snapshot inventory length is invalid")
digests: dict[str, str] = {}
for expected_name, line in zip(hashed_names, inventory_lines, strict=True):
    match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9.-]+)", line)
    if not match or match.group(2) != expected_name:
        raise SystemExit("snapshot inventory shape is invalid")
    digest, name = match.groups()
    observed = sha256_file(root / name)
    if observed != digest:
        raise SystemExit(f"snapshot hash mismatch: {name}")
    digests[name] = digest

manifest_lines = (root / "manifest.env").read_text(encoding="utf-8").splitlines()
manifest: dict[str, str] = {}
for line in manifest_lines:
    if line.count("=") != 1:
        raise SystemExit("snapshot manifest shape is invalid")
    key, value = line.split("=", 1)
    if key in manifest:
        raise SystemExit("snapshot manifest has a duplicate field")
    manifest[key] = value
expected_keys = {
    "schema", "created_at", "database", "postgres_port", "state_root",
    "api_data_root", "critical_table_count_semantics", "research_only",
    "can_publish", "can_execute",
}
if set(manifest) != expected_keys:
    raise SystemExit("snapshot manifest fields are invalid")
if (
    manifest["schema"] != "seiche.market-backup.v2"
    or manifest["created_at"] != snapshot_id
    or manifest["database"] != "seiche"
    or not re.fullmatch(r"[0-9]{1,5}", manifest["postgres_port"])
    or not manifest["state_root"].startswith("/")
    or manifest["state_root"] == "/"
    or not manifest["api_data_root"].startswith("/")
    or manifest["api_data_root"] == "/"
    or manifest["critical_table_count_semantics"] != "pre_dump_lower_bound"
    or manifest["research_only"] != "true"
    or manifest["can_publish"] != "false"
    or manifest["can_execute"] != "false"
):
    raise SystemExit("snapshot manifest contract is invalid")
revision = (root / "deployed-sha.txt").read_text(encoding="ascii")
if not re.fullmatch(r"[0-9a-f]{40}\n", revision):
    raise SystemExit("snapshot revision is invalid")
counts = (root / "table-counts.txt").read_text(encoding="ascii")
if not re.fullmatch(r"[0-9]+\|[0-9]+\|[0-9]+\|[0-9]+\n", counts):
    raise SystemExit("snapshot table counts are invalid")

content = hashlib.sha256()
total = 0
for name in hashed_names:
    size = (root / name).stat().st_size
    total += size
    content.update(name.encode("ascii") + b"\0")
    content.update(digests[name].encode("ascii") + b"\0")
    content.update(str(size).encode("ascii") + b"\n")
print(revision.strip())
print(hashlib.sha256(inventory_bytes).hexdigest())
print(content.hexdigest())
print(total)
PY
}

SOURCE_PROOF_TEXT=$(validate_snapshot "$SNAPSHOT_PATH") \
    || fail "selected snapshot failed its closed v2 contract"
mapfile -t SOURCE_PROOF <<<"$SOURCE_PROOF_TEXT"
[ "${#SOURCE_PROOF[@]}" -eq 4 ] \
    || fail "selected snapshot proof is incomplete"
SOURCE_REVISION=${SOURCE_PROOF[0]}
SOURCE_INVENTORY_SHA=${SOURCE_PROOF[1]}
SOURCE_CONTENT_SHA=${SOURCE_PROOF[2]}
SNAPSHOT_BYTES=${SOURCE_PROOF[3]}
case "$SNAPSHOT_BYTES" in
    ''|*[!0-9]*) fail "snapshot byte count is invalid" ;;
esac
[ "$SNAPSHOT_BYTES" -le "$MAX_SNAPSHOT_BYTES" ] \
    || fail "snapshot exceeds the configured work-space bound"
DEPLOYED_SHA=$(tr -d '\n' <"$DEPLOYED_SHA_PATH")
printf '%s' "$DEPLOYED_SHA" | grep -Eq '^[0-9a-f]{40}$' \
    || fail "deployed SHA receipt is malformed"
[ "$SOURCE_REVISION" = "$DEPLOYED_SHA" ] \
    && [ "$SOURCE_REVISION" = "$RELEASE_SHA" ] \
    || fail "snapshot, deployed receipt, and controller release SHAs differ"

SNAPSHOT_TIMESTAMP="${SNAPSHOT_ID:0:4}-${SNAPSHOT_ID:4:2}-${SNAPSHOT_ID:6:2}T${SNAPSHOT_ID:9:2}:${SNAPSHOT_ID:11:2}:${SNAPSHOT_ID:13:2}Z"
SNAPSHOT_EPOCH=$($DATE_BIN -u -d "$SNAPSHOT_TIMESTAMP" +%s 2>/dev/null || true)
CURRENT_EPOCH=$($DATE_BIN -u +%s)
case "$SNAPSHOT_EPOCH" in
    ''|*[!0-9]*) fail "snapshot timestamp cannot be converted to epoch time" ;;
esac
case "$CURRENT_EPOCH" in
    ''|*[!0-9]*) fail "current time cannot be converted to epoch time" ;;
esac
[ "$SNAPSHOT_EPOCH" -le $((CURRENT_EPOCH + 600)) ] \
    || fail "newest completed snapshot is implausibly in the future"
SNAPSHOT_AGE=$((CURRENT_EPOCH - SNAPSHOT_EPOCH))
[ "$SNAPSHOT_AGE" -le "$MAX_SNAPSHOT_AGE_SECONDS" ] \
    || fail "newest completed snapshot is stale"

prior_success=0
PRIOR_SUCCESS_SNAPSHOT=""
if [ -f "$STATUS_PATH" ] && [ ! -L "$STATUS_PATH" ]; then
    if "$PYTHON_BIN" - "$STATUS_PATH" "$BUCKET" "$PREFIX" "$KEY_ID" \
            "$DESTINATION_ID" "$RCLONE_CONFIG_ANCHOR_ENDPOINT" \
            "$RCLONE_CONFIG_ANCHOR_REGION" <<'PY'
import json
import sys

path, bucket, prefix, key_id, destination_id, endpoint, region = sys.argv[1:]
try:
    value = json.load(open(path, encoding="utf-8"))
except (OSError, ValueError, TypeError):
    raise SystemExit(1)
destination = {
    "id": destination_id,
    "endpoint": endpoint,
    "region": region,
    "bucket": bucket,
    "prefix": prefix,
}
same_destination = (
    value.get("schema") == "seiche.market-offsite-backup-status.v1"
    and value.get("key_id") == key_id
    and value.get("destination") == destination
)
unresolved = same_destination and (
    value.get("status") == "running"
    or (
        value.get("status") == "failed"
        and (
            isinstance(value.get("remote_receipt_key"), str)
            or isinstance(value.get("remote_receipt_version_id"), str)
        )
    )
)
raise SystemExit(0 if unresolved else 1)
PY
    then
        fail "prior offsite attempt is unresolved; operator reconciliation is required"
    fi
    if PRIOR_SUCCESS_SNAPSHOT=$("$PYTHON_BIN" - "$STATUS_PATH" "$BUCKET" \
            "$PREFIX" "$KEY_ID" "$DESTINATION_ID" \
            "$RCLONE_CONFIG_ANCHOR_ENDPOINT" \
            "$RCLONE_CONFIG_ANCHOR_REGION" <<'PY'
import json
import re
import sys

path, bucket, prefix, key_id, destination_id, endpoint, region = sys.argv[1:]
try:
    value = json.load(open(path, encoding="utf-8"))
    success = value.get("last_success")
except (OSError, ValueError, TypeError):
    raise SystemExit(1)
valid = (
    value.get("schema") == "seiche.market-offsite-backup-status.v1"
    and isinstance(success, dict)
    and success.get("restore_verified") is True
    and success.get("bucket") == bucket
    and success.get("prefix") == prefix
    and success.get("object_lock") == {"days": 90, "mode": "COMPLIANCE"}
    and success.get("key_id") == key_id
    and success.get("destination") == {
        "id": destination_id,
        "endpoint": endpoint,
        "region": region,
        "bucket": bucket,
        "prefix": prefix,
    }
    and value.get("bucket") == bucket
    and value.get("prefix") == prefix
    and value.get("object_lock") == {"days": 90, "mode": "COMPLIANCE"}
    and value.get("key_id") == key_id
    and value.get("destination") == success.get("destination")
    and isinstance(success.get("snapshot_id"), str)
    and re.fullmatch(r"20[0-9]{6}T[0-9]{6}Z", success["snapshot_id"])
    and re.fullmatch(
        r"[A-Za-z0-9._~+/=-]+", success.get("ciphertext_version_id", "")
    )
    and re.fullmatch(
        r"[A-Za-z0-9._~+/=-]+", success.get("remote_receipt_version_id", "")
    )
)
if not valid:
    raise SystemExit(1)
print(success["snapshot_id"])
PY
    ); then
        prior_success=1
    fi
fi
if [ "$CANARY" = 1 ] && [ "$prior_success" = 1 ]; then
    fail "canary already succeeded; set canary=0 before recurring writes"
fi
if [ "$CANARY" = 0 ] && [ "$prior_success" != 1 ]; then
    fail "scheduled mode requires a successful first-write canary"
fi
if [ "$CANARY" = 0 ] && [ "$SNAPSHOT_ID" = "$PRIOR_SUCCESS_SNAPSHOT" ]; then
    log "snapshot $SNAPSHOT_ID is already restore-verified off-node; no write needed"
    exit 0
fi
if [ "$CANARY" = 0 ] && [ "$SNAPSHOT_ID" \< "$PRIOR_SUCCESS_SNAPSHOT" ]; then
    fail "newest completed snapshot predates the last offsite success"
fi

AVAILABLE_KB=$($DF_BIN -Pk "$WORK_ROOT" | "$AWK_BIN" 'NR == 2 {print $4}')
case "$AVAILABLE_KB" in
    ''|*[!0-9]*) fail "cannot determine work-root free space" ;;
esac
REQUIRED_KB=$((MIN_FREE_MB * 1024 + (SNAPSHOT_BYTES * 4 + 1023) / 1024))
[ "$AVAILABLE_KB" -ge "$REQUIRED_KB" ] \
    || fail "work root lacks the bounded encryption and restore capacity"

ATTEMPT_STAMP=$($DATE_BIN -u +%Y%m%dT%H%M%SZ)
printf '%s' "$ATTEMPT_STAMP" | grep -Eq '^20[0-9]{6}T[0-9]{6}Z$' \
    || fail "attempt timestamp is malformed"
ATTEMPT_ID="$ATTEMPT_STAMP-$$"
RUN_ROOT=$(mktemp -d "$WORK_ROOT/.run-$ATTEMPT_ID.XXXXXX")
GNUPGHOME="$RUN_ROOT/gnupg"
export GNUPGHOME
mkdir -m 0700 -- "$GNUPGHOME"
ARCHIVE="$RUN_ROOT/seiche-market-backup.tar.gpg"
DOWNLOAD="$RUN_ROOT/downloaded.tar.gpg"
RESTORE_ROOT="$RUN_ROOT/restore"
CHECKSUM="$RUN_ROOT/CIPHERTEXT-SHA256SUMS"
RECEIPT="$RUN_ROOT/RECEIPT.json"
CURL_CONFIG="$RUN_ROOT/curl.conf"
LOCK_DOCUMENT="$RUN_ROOT/object-lock.xml"
VERSION_LIST="$RUN_ROOT/canary-versions.xml"
HEADERS="$RUN_ROOT/object.headers"
DOWNLOADED_RECEIPT="$RUN_ROOT/receipt.downloaded.json"
ARCHIVE_SHA=""
ARCHIVE_BYTES=0
REMOTE_RECEIPT_KEY=""
ARCHIVE_VERSION_ID=""
ARCHIVE_ETAG=""
CHECKSUM_VERSION_ID=""
CHECKSUM_ETAG=""
RECEIPT_VERSION_ID=""
RECEIPT_ETAG=""
COMPLETED=0

write_status() {
    local state="$1" failure_class="${2:-}" previous_success="" temporary
    if [ -f "$STATUS_PATH" ] && [ ! -L "$STATUS_PATH" ]; then
        previous_success=$(
            "$PYTHON_BIN" - "$STATUS_PATH" <<'PY' 2>/dev/null || true
import json
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
success = value.get("last_success")
if isinstance(success, dict):
    print(json.dumps(success, sort_keys=True, separators=(",", ":")))
PY
        )
    fi
    temporary=$(mktemp "$STATUS_ROOT/.status.XXXXXX")
    "$PYTHON_BIN" - "$temporary" "$state" "$ATTEMPT_ID" "$SNAPSHOT_ID" \
        "$SOURCE_REVISION" "$BUCKET" "$PREFIX" "$ARCHIVE_SHA" \
        "$ARCHIVE_BYTES" "$SOURCE_INVENTORY_SHA" "$SOURCE_CONTENT_SHA" \
        "$RETENTION_DAYS" "$REMOTE_RECEIPT_KEY" "$failure_class" \
        "$previous_success" "$KEY_ID" "$DESTINATION_ID" \
        "$RCLONE_CONFIG_ANCHOR_ENDPOINT" "$RCLONE_CONFIG_ANCHOR_REGION" \
        "$ARCHIVE_VERSION_ID" "$ARCHIVE_ETAG" "$CHECKSUM_VERSION_ID" \
        "$CHECKSUM_ETAG" "$RECEIPT_VERSION_ID" "$RECEIPT_ETAG" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

(
    path, state, attempt, snapshot, revision, bucket, prefix, archive_sha,
    archive_bytes, inventory_sha, content_sha, days, receipt_key,
    failure_class, previous_success, key_id, destination_id, endpoint, region,
    archive_version, archive_etag, checksum_version, checksum_etag,
    receipt_version, receipt_etag,
) = sys.argv[1:]
now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
destination = {
    "id": destination_id,
    "endpoint": endpoint,
    "region": region,
    "bucket": bucket,
    "prefix": prefix,
}
document = {
    "schema": "seiche.market-offsite-backup-status.v1",
    "status": state,
    "observed_at": now,
    "attempt_id": attempt,
    "snapshot_id": snapshot,
    "source_revision": revision,
    "provider": "hetzner-object-storage",
    "bucket": bucket,
    "prefix": prefix,
    "key_id": key_id,
    "destination": destination,
    "ciphertext_sha256": archive_sha or None,
    "ciphertext_bytes": int(archive_bytes),
    "ciphertext_version_id": archive_version or None,
    "ciphertext_etag": archive_etag or None,
    "checksum_version_id": checksum_version or None,
    "checksum_etag": checksum_etag or None,
    "source_inventory_sha256": inventory_sha,
    "source_content_set_sha256": content_sha,
    "object_lock": {"mode": "COMPLIANCE", "days": int(days)},
    "remote_receipt_key": receipt_key or None,
    "remote_receipt_version_id": receipt_version or None,
    "remote_receipt_etag": receipt_etag or None,
    "restore_verified": state == "success",
    "failure_class": failure_class or None,
    "last_success": json.loads(previous_success) if previous_success else None,
}
if state == "success":
    document["last_success"] = {
        "attempt_id": attempt,
        "snapshot_id": snapshot,
        "source_revision": revision,
        "bucket": bucket,
        "prefix": prefix,
        "key_id": key_id,
        "destination": destination,
        "ciphertext_sha256": archive_sha,
        "ciphertext_bytes": int(archive_bytes),
        "ciphertext_version_id": archive_version,
        "ciphertext_etag": archive_etag,
        "checksum_version_id": checksum_version,
        "checksum_etag": checksum_etag,
        "source_inventory_sha256": inventory_sha,
        "source_content_set_sha256": content_sha,
        "remote_receipt_key": receipt_key,
        "remote_receipt_version_id": receipt_version,
        "remote_receipt_etag": receipt_etag,
        "object_lock": {"mode": "COMPLIANCE", "days": int(days)},
        "restore_verified": True,
        "verified_at": now,
    }
with open(path, "w", encoding="utf-8") as handle:
    json.dump(document, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(path, 0o600)
PY
    mv -f -- "$temporary" "$STATUS_PATH"
    "$PYTHON_BIN" - "$STATUS_ROOT" <<'PY'
import os
import sys
descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}

status_commits_current_attempt() {
    [ -f "$STATUS_PATH" ] && [ ! -L "$STATUS_PATH" ] || return 1
    "$PYTHON_BIN" - "$STATUS_PATH" "$ATTEMPT_ID" "$SNAPSHOT_ID" \
        "$SOURCE_REVISION" "$ARCHIVE_SHA" "$REMOTE_RECEIPT_KEY" \
        "$RECEIPT_VERSION_ID" "$KEY_ID" "$DESTINATION_ID" <<'PY'
import json
import sys

(
    path, attempt, snapshot, revision, digest, receipt_key, receipt_version,
    key_id, destination_id,
) = sys.argv[1:]
try:
    value = json.load(open(path, encoding="utf-8"))
except (OSError, ValueError, TypeError):
    raise SystemExit(1)
valid = (
    value.get("schema") == "seiche.market-offsite-backup-status.v1"
    and value.get("status") == "success"
    and value.get("attempt_id") == attempt
    and value.get("snapshot_id") == snapshot
    and value.get("source_revision") == revision
    and value.get("ciphertext_sha256") == digest
    and value.get("remote_receipt_key") == receipt_key
    and value.get("remote_receipt_version_id") == receipt_version
    and value.get("key_id") == key_id
    and value.get("destination", {}).get("id") == destination_id
    and value.get("restore_verified") is True
)
raise SystemExit(0 if valid else 1)
PY
}

cleanup() {
    local result=$?
    trap - EXIT HUP INT TERM
    set +e
    if [ "$result" -ne 0 ] && [ "$COMPLETED" -eq 0 ] \
            && [ -n "${RUN_ROOT:-}" ] \
            && ! status_commits_current_attempt; then
        write_status failed operational_failure 2>/dev/null || true
    fi
    if [ -n "${RUN_ROOT:-}" ] && [ -d "$RUN_ROOT" ] \
            && [ ! -L "$RUN_ROOT" ] \
            && [ "$(dirname -- "$RUN_ROOT")" = "$WORK_ROOT" ]; then
        case "$(basename -- "$RUN_ROOT")" in
            .run-*) rm -rf -- "$RUN_ROOT" ;;
        esac
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

write_status running
log "encrypting completed snapshot $SNAPSHOT_ID with AES-256 OCB"
"$TAR_BIN" --create --format=posix --numeric-owner --one-file-system \
    --directory "$BACKUP_ROOT" "$SNAPSHOT_ID" \
    | "$GPG_BIN" --batch --yes --pinentry-mode loopback \
        --passphrase-file "$PASSPHRASE_FILE" --symmetric \
        --cipher-algo AES256 --force-aead --aead-algo OCB \
        --s2k-mode 3 --s2k-digest-algo SHA512 --s2k-count 65011712 \
        --compress-algo none --output "$ARCHIVE"
"$FLOCK_BIN" --unlock 9
exec 9<&-
[ -s "$ARCHIVE" ] || fail "encrypted archive is empty"
ARCHIVE_SHA=$($SHA256SUM_BIN "$ARCHIVE" | "$AWK_BIN" '{print $1}')
printf '%s' "$ARCHIVE_SHA" | grep -Eq '^[0-9a-f]{64}$' \
    || fail "ciphertext hash is malformed"
ARCHIVE_BYTES=$(stat -c '%s' "$ARCHIVE")
printf '%s  %s\n' "$ARCHIVE_SHA" seiche-market-backup.tar.gpg >"$CHECKSUM"

printf 'user = "%s:%s"\n' \
    "$RCLONE_CONFIG_ANCHOR_ACCESS_KEY_ID" \
    "$RCLONE_CONFIG_ANCHOR_SECRET_ACCESS_KEY" >"$CURL_CONFIG"
chmod 0600 "$CURL_CONFIG"
ENDPOINT_HOST=${RCLONE_CONFIG_ANCHOR_ENDPOINT#https://}

verify_bucket_lock() {
    local code
    code=$($CURL_BIN --config "$CURL_CONFIG" --silent --show-error \
        --aws-sigv4 "aws:amz:${RCLONE_CONFIG_ANCHOR_REGION}:s3" \
        --output "$LOCK_DOCUMENT" --write-out '%{http_code}' \
        "https://${BUCKET}.${ENDPOINT_HOST}/?object-lock")
    [ "$code" = 200 ] || fail "Object Lock probe returned HTTP $code"
    grep -q '<ObjectLockEnabled>Enabled</ObjectLockEnabled>' "$LOCK_DOCUMENT" \
        || fail "bucket Object Lock is not enabled"
    grep -q '<Mode>COMPLIANCE</Mode>' "$LOCK_DOCUMENT" \
        || fail "bucket default retention is not COMPLIANCE"
    grep -q '<Days>90</Days>' "$LOCK_DOCUMENT" \
        || fail "bucket default retention is not exactly 90 days"
}

verify_object_lock() {
    local key="$1" label="$2" code retain_until retain_epoch minimum_epoch
    code=$($CURL_BIN --config "$CURL_CONFIG" --silent --show-error --head \
        --aws-sigv4 "aws:amz:${RCLONE_CONFIG_ANCHOR_REGION}:s3" \
        --output "$HEADERS" --write-out '%{http_code}' \
        "https://${BUCKET}.${ENDPOINT_HOST}/${key}")
    [ "$code" = 200 ] || fail "$label retention probe returned HTTP $code"
    tr -d '\r' <"$HEADERS" \
        | grep -qi '^x-amz-object-lock-mode: COMPLIANCE$' \
        || fail "$label is not protected by COMPLIANCE retention"
    retain_until=$(tr -d '\r' <"$HEADERS" \
        | "$AWK_BIN" -F': ' \
            'tolower($1) == "x-amz-object-lock-retain-until-date" {print $2}')
    retain_epoch=$($DATE_BIN -u -d "$retain_until" +%s 2>/dev/null || true)
    minimum_epoch=$(($($DATE_BIN -u +%s) + 89 * 86400))
    case "$retain_epoch" in
        ''|*[!0-9]*) fail "$label retention deadline is invalid" ;;
    esac
    [ "$retain_epoch" -ge "$minimum_epoch" ] \
        || fail "$label retention deadline is shorter than policy"
    VERIFIED_VERSION_ID=$(tr -d '\r' <"$HEADERS" \
        | "$AWK_BIN" -F': ' \
            'tolower($1) == "x-amz-version-id" {print $2}')
    VERIFIED_ETAG=$(tr -d '\r' <"$HEADERS" \
        | "$AWK_BIN" -F': ' 'tolower($1) == "etag" {print $2}')
    printf '%s' "$VERIFIED_VERSION_ID" \
        | grep -Eq '^[A-Za-z0-9._~+/=-]+$' \
        || fail "$label version ID is missing or malformed"
    printf '%s' "$VERIFIED_ETAG" \
        | grep -Eq '^"[A-Fa-f0-9-]{16,128}"$' \
        || fail "$label ETag is missing or malformed"
}

require_private_object() {
    local key="$1" label="$2" code
    code=$($CURL_BIN --silent --show-error --head --output /dev/null \
        --write-out '%{http_code}' "https://${BUCKET}.${ENDPOINT_HOST}/${key}")
    case "$code" in
        401|403) ;;
        200) fail "$label is anonymously readable" ;;
        *) fail "$label anonymous-access probe returned HTTP $code" ;;
    esac
}

require_absent_canary_object() {
    local key="$1" label="$2" code
    code=$($CURL_BIN --config "$CURL_CONFIG" --silent --show-error --head \
        --aws-sigv4 "aws:amz:${RCLONE_CONFIG_ANCHOR_REGION}:s3" \
        --output "$HEADERS" --write-out '%{http_code}' \
        "https://${BUCKET}.${ENDPOINT_HOST}/${key}")
    case "$code" in
        404) ;;
        200) fail "remote canary $label exists; operator reconciliation is required" ;;
        *) fail "remote canary $label probe returned HTTP $code" ;;
    esac
}

require_empty_canary_version_history() {
    local prefix="$1" code
    code=$($CURL_BIN --config "$CURL_CONFIG" --silent --show-error \
        --aws-sigv4 "aws:amz:${RCLONE_CONFIG_ANCHOR_REGION}:s3" \
        --get --data-urlencode 'versions=' \
        --data-urlencode "prefix=$prefix/" \
        --output "$VERSION_LIST" --write-out '%{http_code}' \
        "https://${BUCKET}.${ENDPOINT_HOST}/")
    [ "$code" = 200 ] \
        || fail "remote canary version-history probe returned HTTP $code"
    "$PYTHON_BIN" - "$VERSION_LIST" <<'PY' \
        || fail "remote canary version history exists or is not safely enumerable; operator reconciliation is required"
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

root = ET.fromstring(Path(sys.argv[1]).read_bytes())
local = lambda tag: tag.rsplit("}", 1)[-1]
if local(root.tag) != "ListVersionsResult":
    raise SystemExit(1)
children = list(root)
truncated = [child.text for child in children if local(child.tag) == "IsTruncated"]
if truncated != ["false"]:
    raise SystemExit(1)
if any(local(child.tag) in {"Version", "DeleteMarker"} for child in children):
    raise SystemExit(1)
PY
}

download_exact_version() {
    local key="$1" version_id="$2" destination="$3" label="$4" code
    code=$($CURL_BIN --config "$CURL_CONFIG" --silent --show-error \
        --aws-sigv4 "aws:amz:${RCLONE_CONFIG_ANCHOR_REGION}:s3" \
        --get --data-urlencode "versionId=$version_id" \
        --output "$destination" --write-out '%{http_code}' \
        "https://${BUCKET}.${ENDPOINT_HOST}/${key}")
    [ "$code" = 200 ] || fail "$label exact-version download returned HTTP $code"
}

if [ "$CANARY" = 1 ]; then
    OBJECT_BASE="${PREFIX}/canary/v1"
else
    OBJECT_BASE="${PREFIX}/snapshots/${SNAPSHOT_ID}/attempts/${ATTEMPT_ID}"
fi
REMOTE_BASE="${RCLONE_REMOTE}:${BUCKET}/${OBJECT_BASE}"
verify_bucket_lock
if [ "$CANARY" = 1 ]; then
    require_empty_canary_version_history "$OBJECT_BASE"
    require_absent_canary_object \
        "$OBJECT_BASE/seiche-market-backup.tar.gpg" archive
    require_absent_canary_object \
        "$OBJECT_BASE/CIPHERTEXT-SHA256SUMS" checksum
    require_absent_canary_object "$OBJECT_BASE/RECEIPT.json" receipt
fi
RCLONE_FLAGS=(
    --config=/dev/null
    --s3-no-check-bucket
    --immutable
    --transfers=1
    --checkers=2
    --retries=5
    --low-level-retries=10
)
log "uploading ciphertext to a unique append-only Object Storage key"
"$RCLONE_BIN" copyto "$ARCHIVE" \
    "$REMOTE_BASE/seiche-market-backup.tar.gpg" "${RCLONE_FLAGS[@]}"
verify_object_lock "$OBJECT_BASE/seiche-market-backup.tar.gpg" archive
ARCHIVE_VERSION_ID=$VERIFIED_VERSION_ID
ARCHIVE_ETAG=$VERIFIED_ETAG
require_private_object "$OBJECT_BASE/seiche-market-backup.tar.gpg" archive
"$RCLONE_BIN" copyto "$CHECKSUM" \
    "$REMOTE_BASE/CIPHERTEXT-SHA256SUMS" "${RCLONE_FLAGS[@]}"
verify_object_lock "$OBJECT_BASE/CIPHERTEXT-SHA256SUMS" checksum
CHECKSUM_VERSION_ID=$VERIFIED_VERSION_ID
CHECKSUM_ETAG=$VERIFIED_ETAG

log "downloading, decrypting, and comparing the restored source hashes"
download_exact_version "$OBJECT_BASE/seiche-market-backup.tar.gpg" \
    "$ARCHIVE_VERSION_ID" "$DOWNLOAD" archive
[ "$($SHA256SUM_BIN "$DOWNLOAD" | "$AWK_BIN" '{print $1}')" = "$ARCHIVE_SHA" ] \
    || fail "downloaded ciphertext hash differs from the uploaded source"
mkdir -m 0700 -- "$RESTORE_ROOT"
"$GPG_BIN" --batch --yes --pinentry-mode loopback \
    --passphrase-file "$PASSPHRASE_FILE" --decrypt "$DOWNLOAD" \
    | "$TAR_BIN" --extract --no-same-owner --no-same-permissions \
        --directory "$RESTORE_ROOT" --file -
RESTORED_PROOF_TEXT=$(validate_snapshot "$RESTORE_ROOT/$SNAPSHOT_ID") \
    || fail "downloaded snapshot failed its authenticated restore contract"
mapfile -t RESTORED_PROOF <<<"$RESTORED_PROOF_TEXT"
[ "${#RESTORED_PROOF[@]}" -eq 4 ] \
    && [ "${RESTORED_PROOF[0]}" = "$SOURCE_REVISION" ] \
    && [ "${RESTORED_PROOF[1]}" = "$SOURCE_INVENTORY_SHA" ] \
    && [ "${RESTORED_PROOF[2]}" = "$SOURCE_CONTENT_SHA" ] \
    && [ "${RESTORED_PROOF[3]}" = "$SNAPSHOT_BYTES" ] \
    || fail "restored snapshot hashes differ from the completed source"

REMOTE_RECEIPT_KEY="$OBJECT_BASE/RECEIPT.json"
"$PYTHON_BIN" - "$RECEIPT" "$ATTEMPT_ID" "$SNAPSHOT_ID" \
    "$SOURCE_REVISION" "$BUCKET" "$PREFIX" "$ARCHIVE_SHA" \
    "$ARCHIVE_BYTES" "$SOURCE_INVENTORY_SHA" "$SOURCE_CONTENT_SHA" \
    "$RETENTION_DAYS" "$REMOTE_RECEIPT_KEY" "$KEY_ID" "$DESTINATION_ID" \
    "$RCLONE_CONFIG_ANCHOR_ENDPOINT" "$RCLONE_CONFIG_ANCHOR_REGION" \
    "$ARCHIVE_VERSION_ID" "$ARCHIVE_ETAG" "$CHECKSUM_VERSION_ID" \
    "$CHECKSUM_ETAG" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

(
    path, attempt, snapshot, revision, bucket, prefix, archive_sha,
    archive_bytes, inventory_sha, content_sha, days, receipt_key, key_id,
    destination_id, endpoint, region, archive_version, archive_etag,
    checksum_version, checksum_etag,
) = sys.argv[1:]
document = {
    "schema": "seiche.market-offsite-backup-receipt.v1",
    "status": "remote_restore_verified",
    "verified_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "attempt_id": attempt,
    "snapshot_id": snapshot,
    "source_revision": revision,
    "provider": "hetzner-object-storage",
    "bucket": bucket,
    "prefix": prefix,
    "key_id": key_id,
    "destination": {
        "id": destination_id,
        "endpoint": endpoint,
        "region": region,
        "bucket": bucket,
        "prefix": prefix,
    },
    "remote_receipt_key": receipt_key,
    "ciphertext": {
        "bytes": int(archive_bytes),
        "sha256": archive_sha,
        "version_id": archive_version,
        "etag": archive_etag,
    },
    "checksum": {"version_id": checksum_version, "etag": checksum_etag},
    "source_inventory_sha256": inventory_sha,
    "source_content_set_sha256": content_sha,
    "encryption": "openpgp-symmetric-aes256-ocb-aead-s2k-sha512",
    "verification": "download-ciphertext-sha256-aead-decrypt-and-closed-v2-source-hash-comparison",
    "object_lock": {"mode": "COMPLIANCE", "days": int(days)},
    "research_only": True,
    "can_publish": False,
    "can_execute": False,
}
with open(path, "x", encoding="utf-8") as handle:
    json.dump(document, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(path, 0o600)
PY

# Receipt-last publication is the only remote completion marker. Every rclone
# operation is copyto; this script intentionally has no delete or sync path.
"$RCLONE_BIN" copyto "$RECEIPT" "$REMOTE_BASE/RECEIPT.json" \
    "${RCLONE_FLAGS[@]}"
verify_object_lock "$REMOTE_RECEIPT_KEY" receipt
RECEIPT_VERSION_ID=$VERIFIED_VERSION_ID
RECEIPT_ETAG=$VERIFIED_ETAG
require_private_object "$REMOTE_RECEIPT_KEY" receipt
download_exact_version "$REMOTE_RECEIPT_KEY" "$RECEIPT_VERSION_ID" \
    "$DOWNLOADED_RECEIPT" receipt
cmp -s "$RECEIPT" "$DOWNLOADED_RECEIPT" \
    || fail "downloaded remote receipt differs from the verified commit marker"

write_status success
COMPLETED=1
log "remote restore verified for $SNAPSHOT_ID"
