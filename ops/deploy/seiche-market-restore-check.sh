#!/usr/bin/env bash
# Restore the newest Seiche database snapshot into an isolated scratch DB.
set -euo pipefail
umask 0077

STATE_DIR="${SEICHE_MARKET_STATE_DIR:-/var/lib/seiche}"
BACKUP_DIR="${SEICHE_MARKET_BACKUP_DIR:-/var/backups/seiche-market}"
STATUS_PATH="${SEICHE_RESTORE_STATUS_PATH:-$STATE_DIR/validation/backup-restore-check.status}"
POSTGRES_USER="${SEICHE_POSTGRES_OS_USER:-postgres}"
POSTGRES_GROUP="${SEICHE_POSTGRES_OS_GROUP:-}"
ID_BIN="${SEICHE_ID_BIN:-id}"
SETPRIV_BIN="${SEICHE_SETPRIV_BIN:-/usr/bin/setpriv}"
PSQL_BIN="${SEICHE_PSQL_BIN:-psql}"
PG_RESTORE_BIN="${SEICHE_PG_RESTORE_BIN:-pg_restore}"
CREATEDB_BIN="${SEICHE_CREATEDB_BIN:-createdb}"
DROPDB_BIN="${SEICHE_DROPDB_BIN:-dropdb}"
TAR_BIN="${SEICHE_TAR_BIN:-tar}"
SHA256SUM_BIN="${SEICHE_SHA256SUM_BIN:-sha256sum}"
SYNC_BIN="${SEICHE_SYNC_BIN:-sync}"
DATE_BIN="${SEICHE_DATE_BIN:-date}"

fail() {
    echo "seiche market restore check: $*" >&2
    exit 1
}

case "$BACKUP_DIR" in
    /*) ;;
    *) fail "backup directory must be absolute" ;;
esac
[ "$BACKUP_DIR" != "/" ] || fail "refusing a filesystem-root backup directory"
if [ "${EUID:-$(id -u)}" -ne 0 ] \
    && [ "${SEICHE_ALLOW_NON_ROOT_BACKUP_TEST:-0}" != "1" ]; then
    fail "must run as root"
fi
[ -d "$BACKUP_DIR" ] && [ ! -L "$BACKUP_DIR" ] \
    || fail "backup directory must be a real directory"

run_as_postgres() {
    local postgres_group="$POSTGRES_GROUP"
    if [ -z "$postgres_group" ]; then
        postgres_group=$("$ID_BIN" -g "$POSTGRES_USER") \
            || fail "cannot resolve primary group for PostgreSQL OS user $POSTGRES_USER"
    fi
    "$SETPRIV_BIN" --reuid="$POSTGRES_USER" --regid="$postgres_group" \
        --init-groups --inh-caps=-all -- "$@"
}

SNAPSHOT="${SEICHE_RESTORE_SNAPSHOT:-}"
if [ -z "$SNAPSHOT" ]; then
    while IFS= read -r CANDIDATE; do
        SNAPSHOT="$CANDIDATE"
    done < <(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d \
        -name '20??????T??????Z' -print | LC_ALL=C sort)
fi
[ -n "$SNAPSHOT" ] || fail "no committed snapshot exists"
[ -d "$SNAPSHOT" ] && [ ! -L "$SNAPSHOT" ] \
    || fail "snapshot must be a real directory"
[ "$(dirname "$SNAPSHOT")" = "$BACKUP_DIR" ] \
    || fail "snapshot is outside the configured backup directory"
SNAPSHOT_NAME=$(basename "$SNAPSHOT")
case "$SNAPSHOT_NAME" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
    *) fail "snapshot name is invalid" ;;
esac
for MEMBER in SHA256SUMS seiche.dump var-lib-seiche.tgz table-counts.txt \
    deployed-sha.txt manifest.env; do
    [ -f "$SNAPSHOT/$MEMBER" ] && [ ! -L "$SNAPSHOT/$MEMBER" ] \
        || fail "snapshot member $MEMBER is missing or unsafe"
done
(
    cd "$SNAPSHOT"
    "$SHA256SUM_BIN" --check --strict SHA256SUMS >/dev/null
)
"$TAR_BIN" --list --gzip --file "$SNAPSHOT/var-lib-seiche.tgz" >/dev/null
"$PG_RESTORE_BIN" --list <"$SNAPSHOT/seiche.dump" >/dev/null

POSTGRES_PORT=$(run_as_postgres "$PSQL_BIN" --no-psqlrc -tAc "SHOW port" \
    | tr -d '[:space:]')
case "$POSTGRES_PORT" in
    ''|*[!0-9]*) fail "could not resolve the PostgreSQL cluster port" ;;
esac
SCRATCH="seiche_restore_${SNAPSHOT_NAME//[TZ]/_}_$$"
case "$SCRATCH" in
    *[!a-zA-Z0-9_]*) fail "scratch database identity is unsafe" ;;
esac
CREATED=""
cleanup() {
    if [ -n "$CREATED" ]; then
        run_as_postgres "$DROPDB_BIN" --if-exists \
            --host=/var/run/postgresql --port="$POSTGRES_PORT" "$SCRATCH" \
            >/dev/null 2>&1 || true
    fi
    [ -z "${STATE_STAGE:-}" ] || rm -rf -- "$STATE_STAGE"
    [ -z "${STATUS_STAGE:-}" ] || rm -f -- "$STATUS_STAGE"
}
trap cleanup EXIT

STATUS_DIR=$(dirname "$STATUS_PATH")
[ -d "$STATUS_DIR" ] && [ ! -L "$STATUS_DIR" ] \
    || fail "restore status directory must be a real directory"
STATE_STAGE=$(mktemp -d "$STATUS_DIR/.backup-state-restore.XXXXXX")
"$TAR_BIN" --extract --gzip --file "$SNAPSHOT/var-lib-seiche.tgz" \
    --directory "$STATE_STAGE" --no-same-owner --no-same-permissions
find "$STATE_STAGE" -mindepth 1 -print -quit | grep -q . \
    || fail "restored state archive is empty"
rm -rf -- "$STATE_STAGE"
STATE_STAGE=""

run_as_postgres "$CREATEDB_BIN" --template=template0 \
    --host=/var/run/postgresql --port="$POSTGRES_PORT" "$SCRATCH"
CREATED=1
run_as_postgres "$PG_RESTORE_BIN" --exit-on-error --no-owner --no-privileges \
    --host=/var/run/postgresql --port="$POSTGRES_PORT" --dbname="$SCRATCH" \
    <"$SNAPSHOT/seiche.dump"

COUNTS_SQL="SELECT (SELECT count(*) FROM canonical_observations)::text || '|' || (SELECT count(*) FROM collector_runs)::text || '|' || (SELECT count(*) FROM forward_validation_records)::text || '|' || (SELECT count(*) FROM market_snapshots)::text"
ACTUAL_COUNTS=$(run_as_postgres "$PSQL_BIN" --no-psqlrc \
    --tuples-only --no-align --set ON_ERROR_STOP=1 \
    --host=/var/run/postgresql --port="$POSTGRES_PORT" \
    --dbname="$SCRATCH" --command "$COUNTS_SQL" | tr -d '[:space:]')
EXPECTED_COUNTS=$(tr -d '[:space:]' <"$SNAPSHOT/table-counts.txt")
[ "$ACTUAL_COUNTS" = "$EXPECTED_COUNTS" ] \
    || fail "restored critical table counts do not match the snapshot"
run_as_postgres "$DROPDB_BIN" --if-exists \
    --host=/var/run/postgresql --port="$POSTGRES_PORT" "$SCRATCH"
CREATED=""

STATUS_STAGE=$(mktemp "$STATUS_DIR/.backup-restore-check.XXXXXX")
printf '%s\n' \
    "schema=seiche.market-backup-restore-check.v1" \
    "checked_at=$($DATE_BIN -u +%Y-%m-%dT%H:%M:%SZ)" \
    "snapshot=$SNAPSHOT_NAME" \
    "deployed_sha=$(tr -d '[:space:]' <"$SNAPSHOT/deployed-sha.txt")" \
    "critical_table_counts=$ACTUAL_COUNTS" \
    "database_restore=pass" \
    "state_archive_restore=pass" \
    "research_only=true" \
    "can_publish=false" \
    "can_execute=false" >"$STATUS_STAGE"
chmod 0640 "$STATUS_STAGE"
if [ "${SEICHE_ALLOW_NON_ROOT_BACKUP_TEST:-0}" != "1" ]; then
    chown root:seiche "$STATUS_STAGE"
fi
"$SYNC_BIN" -f "$STATUS_STAGE"
mv -f -- "$STATUS_STAGE" "$STATUS_PATH"
STATUS_STAGE=""
"$SYNC_BIN" -f "$STATUS_DIR"

echo "seiche market restore check: $SNAPSHOT_NAME restored and verified"
