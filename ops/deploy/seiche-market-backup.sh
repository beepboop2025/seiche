#!/usr/bin/env bash
# Commit one self-verifying local snapshot of the Seiche market data plane.
set -euo pipefail
umask 0077

APP_DIR="${SEICHE_APP_DIR:-/home/seiche/app}"
STATE_DIR="${SEICHE_MARKET_STATE_DIR:-/var/lib/seiche}"
NBS_STATE_DIR="${SEICHE_NBS_STATE_DIR:-/var/lib/seiche-nbs}"
PALIMPSEST_CHINA_STATE_DIR="${SEICHE_PALIMPSEST_CHINA_STATE_DIR:-/var/lib/seiche-palimpsest-china}"
PALIMPSEST_CHINA_AUDIT_BIN="${SEICHE_PALIMPSEST_CHINA_AUDIT_BIN:-/etc/seiche/libexec/seiche-palimpsest-china-activate.py}"
API_DATA_DIR="${SEICHE_API_DATA_DIR:-$APP_DIR/backend/data}"
BACKUP_DIR="${SEICHE_MARKET_BACKUP_DIR:-/var/backups/seiche-market}"
DATABASE_NAME="${SEICHE_MARKET_DATABASE_NAME:-seiche}"
RETENTION_DAYS="${SEICHE_BACKUP_RETENTION_DAYS:-21}"
MIN_DUMP_BYTES="${SEICHE_BACKUP_MIN_DUMP_BYTES:-10240}"
POSTGRES_USER="${SEICHE_POSTGRES_OS_USER:-postgres}"
POSTGRES_GROUP="${SEICHE_POSTGRES_OS_GROUP:-}"
ID_BIN="${SEICHE_ID_BIN:-id}"
SETPRIV_BIN="${SEICHE_SETPRIV_BIN:-/usr/bin/setpriv}"
PSQL_BIN="${SEICHE_PSQL_BIN:-psql}"
PG_DUMP_BIN="${SEICHE_PG_DUMP_BIN:-pg_dump}"
PG_RESTORE_BIN="${SEICHE_PG_RESTORE_BIN:-pg_restore}"
TAR_BIN="${SEICHE_TAR_BIN:-tar}"
CP_BIN="${SEICHE_CP_BIN:-cp}"
SHA256SUM_BIN="${SEICHE_SHA256SUM_BIN:-sha256sum}"
SYNC_BIN="${SEICHE_SYNC_BIN:-sync}"
DATE_BIN="${SEICHE_DATE_BIN:-date}"
GIT_BIN="${SEICHE_GIT_BIN:-git}"
PYTHON_BIN="${SEICHE_PYTHON_BIN:-/usr/bin/python3}"
CMP_BIN="${SEICHE_CMP_BIN:-/usr/bin/cmp}"
DEPLOYED_SHA_PATH="${SEICHE_DEPLOYED_SHA_PATH:-/var/lib/seiche-deploy/deployed-sha}"
ALLOW_NON_ROOT_TEST="${SEICHE_ALLOW_NON_ROOT_BACKUP_TEST:-0}"

fail() {
    echo "seiche market backup: $*" >&2
    exit 1
}

case "$BACKUP_DIR" in
    /*) ;;
    *) fail "backup directory must be absolute" ;;
esac
[ "$BACKUP_DIR" != "/" ] || fail "refusing a filesystem-root backup directory"
case "$RETENTION_DAYS" in
    ''|*[!0-9]*) fail "retention days must be a non-negative integer" ;;
esac
case "$MIN_DUMP_BYTES" in
    ''|*[!0-9]*) fail "minimum dump size must be a non-negative integer" ;;
esac
case "$ALLOW_NON_ROOT_TEST" in
    0|1) ;;
    *) fail "non-root backup test flag must be exactly 0 or 1" ;;
esac
CURRENT_EUID="${EUID:-$(id -u)}"
if [ "$ALLOW_NON_ROOT_TEST" = "1" ]; then
    [ "$CURRENT_EUID" -ne 0 ] \
        || fail "non-root backup test mode cannot run as root"
elif [ "$CURRENT_EUID" -ne 0 ]; then
    fail "must run as root"
else
    [ "$PYTHON_BIN" = /usr/bin/python3 ] \
        || fail "production Python runtime is fixed at /usr/bin/python3"
    [ "$NBS_STATE_DIR" = /var/lib/seiche-nbs ] \
        || fail "production NBS state root is fixed at /var/lib/seiche-nbs"
    [ "$PALIMPSEST_CHINA_STATE_DIR" = /var/lib/seiche-palimpsest-china ] \
        || fail "production Palimpsest China state root is fixed"
    [ "$PALIMPSEST_CHINA_AUDIT_BIN" = \
        /etc/seiche/libexec/seiche-palimpsest-china-activate.py ] \
        || fail "production Palimpsest China audit launcher is fixed"
fi
[ -d "$STATE_DIR" ] && [ ! -L "$STATE_DIR" ] \
    || fail "state directory must be a real directory"
case "$NBS_STATE_DIR" in
    /*) ;;
    *) fail "NBS state directory must be absolute" ;;
esac
[ "$NBS_STATE_DIR" != "/" ] || fail "refusing a filesystem-root NBS state directory"
[ -d "$NBS_STATE_DIR" ] && [ ! -L "$NBS_STATE_DIR" ] \
    || fail "NBS state directory must be a real directory"
if find "$NBS_STATE_DIR" -type l -print -quit | grep -q .; then
    fail "NBS state directory cannot contain symlinks"
fi
case "$PALIMPSEST_CHINA_STATE_DIR" in
    /*) ;;
    *) fail "Palimpsest China state directory must be absolute" ;;
esac
[ "$PALIMPSEST_CHINA_STATE_DIR" != "/" ] \
    || fail "refusing a filesystem-root Palimpsest China state directory"
[ -d "$PALIMPSEST_CHINA_STATE_DIR" ] && [ ! -L "$PALIMPSEST_CHINA_STATE_DIR" ] \
    || fail "Palimpsest China state directory must be a real directory"
[ -x "$PALIMPSEST_CHINA_AUDIT_BIN" ] && [ ! -L "$PALIMPSEST_CHINA_AUDIT_BIN" ] \
    || fail "Palimpsest China audit launcher is missing or unsafe"
[ -x "$CMP_BIN" ] || fail "cmp is unavailable"
case "$API_DATA_DIR" in
    /*) ;;
    *) fail "API data directory must be absolute" ;;
esac
[ "$API_DATA_DIR" != "/" ] || fail "refusing a filesystem-root API data directory"
[ -d "$API_DATA_DIR" ] && [ ! -L "$API_DATA_DIR" ] \
    || fail "API data directory must be a real directory"
if find "$API_DATA_DIR" -type l -print -quit | grep -q .; then
    fail "API data directory cannot contain symlinks"
fi
[ -x "$PYTHON_BIN" ] || fail "Python runtime is unavailable"
[ ! -L "$BACKUP_DIR" ] || fail "backup directory cannot be a symlink"
if [ "$ALLOW_NON_ROOT_TEST" = "1" ]; then
    mkdir -p "$BACKUP_DIR"
    chmod 0700 "$BACKUP_DIR"
else
    install -d -o root -g root -m 0700 "$BACKUP_DIR"
fi

run_as_postgres() {
    local postgres_group="$POSTGRES_GROUP"
    if [ -z "$postgres_group" ]; then
        postgres_group=$("$ID_BIN" -g "$POSTGRES_USER") \
            || fail "cannot resolve primary group for PostgreSQL OS user $POSTGRES_USER"
    fi
    "$SETPRIV_BIN" --reuid="$POSTGRES_USER" --regid="$postgres_group" \
        --init-groups --inh-caps=-all -- "$@"
}

POSTGRES_PORT=$(run_as_postgres "$PSQL_BIN" --no-psqlrc -tAc "SHOW port" \
    | tr -d '[:space:]')
case "$POSTGRES_PORT" in
    ''|*[!0-9]*) fail "could not resolve the PostgreSQL cluster port" ;;
esac

COUNTS_SQL="SELECT (SELECT count(*) FROM canonical_observations)::text || '|' || (SELECT count(*) FROM collector_runs)::text || '|' || (SELECT count(*) FROM forward_validation_records)::text || '|' || (SELECT count(*) FROM market_snapshots)::text"
query_counts() {
    run_as_postgres "$PSQL_BIN" --no-psqlrc --tuples-only --no-align \
        --set ON_ERROR_STOP=1 --host=/var/run/postgresql \
        --port="$POSTGRES_PORT" --dbname="$DATABASE_NAME" \
        --command "$COUNTS_SQL" | tr -d '[:space:]'
}

STAMP="${SEICHE_BACKUP_STAMP:-$($DATE_BIN -u +%Y%m%dT%H%M%SZ)}"
case "$STAMP" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
    *) fail "snapshot stamp must be UTC basic format" ;;
esac
FINAL="$BACKUP_DIR/$STAMP"
[ ! -e "$FINAL" ] || fail "refusing to replace existing snapshot $STAMP"
STAGE=$(mktemp -d "$BACKUP_DIR/.stage-$STAMP.XXXXXX")
cleanup() {
    [ -z "${STAGE:-}" ] || rm -rf -- "$STAGE"
}
trap cleanup EXIT

validate_palimpsest_audit() {
    local audit_path="$1"
    "$PYTHON_BIN" -I -B - "$audit_path" "$PALIMPSEST_CHINA_STATE_DIR" <<'PY'
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
expected_root = sys.argv[2]
body = path.read_bytes()
if not 1 <= len(body) <= 512 * 1024 or not body.endswith(b"\n"):
    raise SystemExit("Palimpsest China state audit is empty, oversized, or unterminated")
try:
    value = json.loads(body)
except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SystemExit("Palimpsest China state audit is not strict JSON") from exc
keys = {
    "schema",
    "state_root",
    "tree_sha256",
    "bundles",
    "receipts",
    "active_activation_id",
    "pending_candidate_activation_id",
}
sha_re = re.compile(r"[0-9a-f]{64}")
if type(value) is not dict or set(value) != keys:
    raise SystemExit("Palimpsest China state audit fields changed")
if (
    value["schema"] != "seiche.palimpsest-china-activation-state.v1"
    or value["state_root"] != expected_root
    or sha_re.fullmatch(value["tree_sha256"] or "") is None
    or type(value["bundles"]) is not list
    or type(value["receipts"]) is not list
    or value["bundles"] != sorted(set(value["bundles"]))
    or value["receipts"] != sorted(set(value["receipts"]))
    or any(sha_re.fullmatch(item or "") is None for item in value["bundles"])
    or any(sha_re.fullmatch(item or "") is None for item in value["receipts"])
    or any(
        item is not None and sha_re.fullmatch(item or "") is None
        for item in (
            value["active_activation_id"],
            value["pending_candidate_activation_id"],
        )
    )
):
    raise SystemExit("Palimpsest China state audit contract changed")
canonical = (
    json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    + b"\n"
)
if body != canonical:
    raise SystemExit("Palimpsest China state audit is not canonical JSON")
PY
}

COUNTS_BEFORE=$(query_counts)
printf '%s' "$COUNTS_BEFORE" \
    | grep -Eq '^[0-9]+\|[0-9]+\|[0-9]+\|[0-9]+$' \
    || fail "critical table counts have an invalid shape"
run_as_postgres "$PG_DUMP_BIN" \
    --format=custom --compress=9 --no-owner --no-privileges \
    --host=/var/run/postgresql --port="$POSTGRES_PORT" \
    --dbname="$DATABASE_NAME" >"$STAGE/seiche.dump"
DUMP_BYTES=$(wc -c <"$STAGE/seiche.dump" | tr -d '[:space:]')
[ "$DUMP_BYTES" -ge "$MIN_DUMP_BYTES" ] \
    || fail "database dump is implausibly small ($DUMP_BYTES bytes)"
"$PG_RESTORE_BIN" --list <"$STAGE/seiche.dump" >/dev/null

# The activation tree is a separate root-owned trust domain. Audit it through
# the exact release-addressed launcher before and after archiving, then audit a
# normalized scratch extraction. Three equal canonical receipts make a racing
# activation fail closed without widening ownership of /var/lib/seiche.
"$PALIMPSEST_CHINA_AUDIT_BIN" \
    --audit-state "$PALIMPSEST_CHINA_STATE_DIR" 0 \
    >"$STAGE/palimpsest-china-state.json"
validate_palimpsest_audit "$STAGE/palimpsest-china-state.json"

STATE_PARENT=$(dirname "$STATE_DIR")
STATE_NAME=$(basename "$STATE_DIR")
NBS_STATE_PARENT=$(dirname "$NBS_STATE_DIR")
NBS_STATE_NAME=$(basename "$NBS_STATE_DIR")
[ "$STATE_NAME" != "$NBS_STATE_NAME" ] \
    || fail "market and NBS state roots must have distinct names"
"$TAR_BIN" --create --gzip --file "$STAGE/var-lib-seiche.tgz" \
    --acls --xattrs --numeric-owner --one-file-system \
    --directory "$STATE_PARENT" "$STATE_NAME" \
    --directory "$NBS_STATE_PARENT" "$NBS_STATE_NAME"
"$TAR_BIN" --list --gzip --file "$STAGE/var-lib-seiche.tgz" >/dev/null

PALIMPSEST_CHINA_STATE_PARENT=$(dirname "$PALIMPSEST_CHINA_STATE_DIR")
PALIMPSEST_CHINA_STATE_NAME=$(basename "$PALIMPSEST_CHINA_STATE_DIR")
"$TAR_BIN" --create --gzip --file "$STAGE/palimpsest-china.tgz" \
    --acls --xattrs --numeric-owner --one-file-system \
    --directory "$PALIMPSEST_CHINA_STATE_PARENT" \
    "$PALIMPSEST_CHINA_STATE_NAME"
"$TAR_BIN" --list --gzip --file "$STAGE/palimpsest-china.tgz" >/dev/null
PALIMPSEST_VERIFY_ROOT="$STAGE/palimpsest-verify"
mkdir -m 0700 "$PALIMPSEST_VERIFY_ROOT"
"$TAR_BIN" --extract --gzip --file "$STAGE/palimpsest-china.tgz" \
    --directory "$PALIMPSEST_VERIFY_ROOT" \
    --no-same-owner --no-same-permissions
PALIMPSEST_RESTORED_ROOT="$PALIMPSEST_VERIFY_ROOT/$PALIMPSEST_CHINA_STATE_NAME"
[ -d "$PALIMPSEST_RESTORED_ROOT" ] && [ ! -L "$PALIMPSEST_RESTORED_ROOT" ] \
    || fail "Palimpsest China archive omitted its state root"
"$PALIMPSEST_CHINA_AUDIT_BIN" \
    --audit-state "$PALIMPSEST_RESTORED_ROOT" 1 \
    >"$STAGE/palimpsest-china-restored-state.json"
validate_palimpsest_audit "$STAGE/palimpsest-china-restored-state.json"
"$CMP_BIN" -s "$STAGE/palimpsest-china-state.json" \
    "$STAGE/palimpsest-china-restored-state.json" \
    || fail "Palimpsest China archive changed immutable state"
rm -rf -- "$PALIMPSEST_VERIFY_ROOT"
rm -f -- "$STAGE/palimpsest-china-restored-state.json"
"$PALIMPSEST_CHINA_AUDIT_BIN" \
    --audit-state "$PALIMPSEST_CHINA_STATE_DIR" 0 \
    >"$STAGE/palimpsest-china-live-after.json"
validate_palimpsest_audit "$STAGE/palimpsest-china-live-after.json"
"$CMP_BIN" -s "$STAGE/palimpsest-china-state.json" \
    "$STAGE/palimpsest-china-live-after.json" \
    || fail "Palimpsest China state changed during the snapshot"
rm -f -- "$STAGE/palimpsest-china-live-after.json"

# Copy the compatibility data directory, then replace the live SQLite files
# with a transactionally consistent online backup.  Copying a database and its
# WAL independently can produce a snapshot which only fails during a disaster.
API_STAGE="$STAGE/api-data"
mkdir -m 0700 "$API_STAGE"
# This is a content snapshot, not an ownership migration. The hardened backup
# service intentionally has no CAP_CHOWN, so archive-preserving copies fail
# when the live tree contains files owned by more than one service user. The
# restore path already extracts with --no-same-owner/--no-same-permissions.
"$CP_BIN" -R -- "$API_DATA_DIR/." "$API_STAGE/"
rm -f -- "$API_STAGE/seiche.sqlite" \
    "$API_STAGE/seiche.sqlite-wal" "$API_STAGE/seiche.sqlite-shm"
[ -f "$API_DATA_DIR/seiche.sqlite" ] && [ ! -L "$API_DATA_DIR/seiche.sqlite" ] \
    || fail "API SQLite database is missing or unsafe"
"$PYTHON_BIN" -I -B - \
    "$API_DATA_DIR/seiche.sqlite" "$API_STAGE/seiche.sqlite" <<'PY'
import sqlite3
import sys

source_path, backup_path = sys.argv[1:]
with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source:
    with sqlite3.connect(backup_path) as backup:
        source.backup(backup)
        result = backup.execute("PRAGMA quick_check").fetchone()
        if result != ("ok",):
            raise SystemExit("SQLite online backup failed PRAGMA quick_check")
PY
"$TAR_BIN" --create --gzip --file "$STAGE/api-data.tgz" \
    --acls --xattrs --numeric-owner --one-file-system \
    --directory "$STAGE" api-data
"$TAR_BIN" --list --gzip --file "$STAGE/api-data.tgz" >/dev/null
rm -rf -- "$API_STAGE"
API_STAGE=""

# These four tables are append-only/upsert-only during ordinary collection.
# Record the pre-dump values as a lower-bound receipt: pg_dump takes its own
# consistent MVCC snapshot after this query, so concurrent ingestion may make
# the dump larger but must never make a valid restore smaller.
printf '%s\n' "$COUNTS_BEFORE" >"$STAGE/table-counts.txt"

DEPLOYED_SHA=$(cat "$DEPLOYED_SHA_PATH" 2>/dev/null || true)
if ! printf '%s' "$DEPLOYED_SHA" | grep -Eq '^[0-9a-f]{40}$'; then
    DEPLOYED_SHA=$($GIT_BIN -C "$APP_DIR" rev-parse HEAD 2>/dev/null || true)
fi
printf '%s' "$DEPLOYED_SHA" | grep -Eq '^[0-9a-f]{40}$' \
    || fail "cannot bind the snapshot to a deployed commit"
printf '%s\n' "$DEPLOYED_SHA" >"$STAGE/deployed-sha.txt"
printf '%s\n' \
    "schema=seiche.market-backup.v4" \
    "created_at=$STAMP" \
    "database=$DATABASE_NAME" \
    "postgres_port=$POSTGRES_PORT" \
    "state_root=$STATE_DIR" \
    "nbs_state_root=$NBS_STATE_DIR" \
    "nbs_full_store_audit_contract=seiche.nbs-full-store-audit.v1" \
    "nbs_full_store_audit_result=required_at_restore" \
    "palimpsest_china_state_root=$PALIMPSEST_CHINA_STATE_DIR" \
    "palimpsest_china_state_audit_contract=seiche.palimpsest-china-activation-state.v1" \
    "palimpsest_china_state_audit_result=required_at_restore" \
    "api_data_root=$API_DATA_DIR" \
    "critical_table_count_semantics=pre_dump_lower_bound" \
    "research_only=true" \
    "can_publish=false" \
    "can_execute=false" >"$STAGE/manifest.env"

(
    cd "$STAGE"
    "$SHA256SUM_BIN" seiche.dump var-lib-seiche.tgz palimpsest-china.tgz \
        palimpsest-china-state.json api-data.tgz table-counts.txt \
        deployed-sha.txt manifest.env >SHA256SUMS
    "$SHA256SUM_BIN" --check --strict SHA256SUMS >/dev/null
)
chmod 0600 "$STAGE"/*
for member in "$STAGE"/*; do
    "$SYNC_BIN" -f "$member"
done
"$SYNC_BIN" -f "$STAGE"
mv -- "$STAGE" "$FINAL"
STAGE=""
"$SYNC_BIN" -f "$BACKUP_DIR"

# Retention runs only after a new, verified snapshot is committed. Candidate
# names and parents are validated before recursive removal, so an operator
# typo cannot turn the backup root or an unrelated directory into a target.
while IFS= read -r -d '' CANDIDATE; do
    NAME=$(basename "$CANDIDATE")
    case "$NAME" in
        [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
        *) continue ;;
    esac
    [ "$(dirname "$CANDIDATE")" = "$BACKUP_DIR" ] || continue
    if command -v mountpoint >/dev/null 2>&1 \
        && mountpoint -q -- "$CANDIDATE"; then
        echo "seiche market backup: refusing retention across mount $CANDIDATE" >&2
        continue
    fi
    rm -rf -- "$CANDIDATE"
done < <(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d \
    -name '20??????T??????Z' -mtime "+$RETENTION_DAYS" -print0)

echo "seiche market backup: committed $FINAL ($DUMP_BYTES database bytes)"
