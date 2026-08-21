#!/usr/bin/env bash
# Commit one self-verifying local snapshot of the Seiche market data plane.
set -euo pipefail
umask 0077

APP_DIR="${SEICHE_APP_DIR:-/home/seiche/app}"
STATE_DIR="${SEICHE_MARKET_STATE_DIR:-/var/lib/seiche}"
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
PYTHON_BIN="${SEICHE_PYTHON_BIN:-$APP_DIR/backend/.venv/bin/python}"
DEPLOYED_SHA_PATH="${SEICHE_DEPLOYED_SHA_PATH:-/var/lib/seiche-deploy/deployed-sha}"

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
if [ "${EUID:-$(id -u)}" -ne 0 ] \
    && [ "${SEICHE_ALLOW_NON_ROOT_BACKUP_TEST:-0}" != "1" ]; then
    fail "must run as root"
fi
[ -d "$STATE_DIR" ] && [ ! -L "$STATE_DIR" ] \
    || fail "state directory must be a real directory"
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
if [ "${SEICHE_ALLOW_NON_ROOT_BACKUP_TEST:-0}" = "1" ]; then
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

STATE_PARENT=$(dirname "$STATE_DIR")
STATE_NAME=$(basename "$STATE_DIR")
"$TAR_BIN" --create --gzip --file "$STAGE/var-lib-seiche.tgz" \
    --acls --xattrs --numeric-owner --one-file-system \
    --directory "$STATE_PARENT" "$STATE_NAME"
"$TAR_BIN" --list --gzip --file "$STAGE/var-lib-seiche.tgz" >/dev/null

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
"$PYTHON_BIN" - "$API_DATA_DIR/seiche.sqlite" "$API_STAGE/seiche.sqlite" <<'PY'
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
    "schema=seiche.market-backup.v2" \
    "created_at=$STAMP" \
    "database=$DATABASE_NAME" \
    "postgres_port=$POSTGRES_PORT" \
    "state_root=$STATE_DIR" \
    "api_data_root=$API_DATA_DIR" \
    "critical_table_count_semantics=pre_dump_lower_bound" \
    "research_only=true" \
    "can_publish=false" \
    "can_execute=false" >"$STAGE/manifest.env"

(
    cd "$STAGE"
    "$SHA256SUM_BIN" seiche.dump var-lib-seiche.tgz api-data.tgz table-counts.txt \
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
