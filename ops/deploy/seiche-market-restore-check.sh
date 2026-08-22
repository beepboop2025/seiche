#!/usr/bin/env bash
# Restore the newest Seiche database snapshot into an isolated scratch DB.
set -euo pipefail
umask 0077

BACKUP_DIR="${SEICHE_MARKET_BACKUP_DIR:-/var/backups/seiche-market}"
NBS_STATE_DIR="${SEICHE_NBS_STATE_DIR:-/var/lib/seiche-nbs}"
STATUS_PATH="${SEICHE_RESTORE_STATUS_PATH:-/var/lib/seiche-recovery-proof/backup-restore-check.status}"
DATABASE_NAME="${SEICHE_MARKET_DATABASE_NAME:-seiche}"
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
PYTHON_BIN="${SEICHE_PYTHON_BIN:-/home/seiche/app/backend/.venv/bin/python}"

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
case "$NBS_STATE_DIR" in
    /*) ;;
    *) fail "NBS state directory must be absolute" ;;
esac
[ "$NBS_STATE_DIR" != "/" ] || fail "refusing a filesystem-root NBS state directory"
NBS_STATE_NAME=$(basename "$NBS_STATE_DIR")

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
for MEMBER in SHA256SUMS seiche.dump var-lib-seiche.tgz api-data.tgz table-counts.txt \
    deployed-sha.txt manifest.env; do
    [ -f "$SNAPSHOT/$MEMBER" ] && [ ! -L "$SNAPSHOT/$MEMBER" ] \
        || fail "snapshot member $MEMBER is missing or unsafe"
done
(
    cd "$SNAPSHOT"
    "$SHA256SUM_BIN" --check --strict SHA256SUMS >/dev/null
)

# A valid checksum only binds bytes; it does not make an arbitrary manifest a
# Seiche research-only backup. Parse the closed v2 contract without sourcing
# attacker-controlled shell text before extracting files or creating a scratch
# database.
declare -A MANIFEST_FIELDS=()
MANIFEST_VALID=1
while IFS= read -r MANIFEST_LINE || [ -n "$MANIFEST_LINE" ]; do
    case "$MANIFEST_LINE" in
        *=*) ;;
        *) MANIFEST_VALID=0; continue ;;
    esac
    MANIFEST_KEY=${MANIFEST_LINE%%=*}
    MANIFEST_VALUE=${MANIFEST_LINE#*=}
    case "$MANIFEST_KEY" in
        schema|created_at|database|postgres_port|state_root|api_data_root|\
        critical_table_count_semantics|research_only|can_publish|can_execute) ;;
        *) MANIFEST_VALID=0; continue ;;
    esac
    if [ -n "${MANIFEST_FIELDS[$MANIFEST_KEY]+present}" ]; then
        MANIFEST_VALID=0
        continue
    fi
    MANIFEST_FIELDS[$MANIFEST_KEY]=$MANIFEST_VALUE
done <"$SNAPSHOT/manifest.env"

safe_manifest_root() {
    local candidate="$1"
    case "$candidate" in
        /*) ;;
        *) return 1 ;;
    esac
    [ "$candidate" != "/" ] || return 1
    case "$candidate/" in
        *'//'*) return 1 ;;
        *'/./'*|*'/../'*) return 1 ;;
    esac
    return 0
}

[ "${#MANIFEST_FIELDS[@]}" -eq 10 ] || MANIFEST_VALID=0
[ "${MANIFEST_FIELDS[schema]-}" = "seiche.market-backup.v2" ] \
    || MANIFEST_VALID=0
[ "${MANIFEST_FIELDS[created_at]-}" = "$SNAPSHOT_NAME" ] \
    || MANIFEST_VALID=0
[ "${MANIFEST_FIELDS[database]-}" = "$DATABASE_NAME" ] \
    || MANIFEST_VALID=0
case "${MANIFEST_FIELDS[postgres_port]-}" in
    ''|*[!0-9]*) MANIFEST_VALID=0 ;;
esac
safe_manifest_root "${MANIFEST_FIELDS[state_root]-}" || MANIFEST_VALID=0
safe_manifest_root "${MANIFEST_FIELDS[api_data_root]-}" || MANIFEST_VALID=0
[ "${MANIFEST_FIELDS[critical_table_count_semantics]-}" = \
    "pre_dump_lower_bound" ] || MANIFEST_VALID=0
[ "${MANIFEST_FIELDS[research_only]-}" = "true" ] || MANIFEST_VALID=0
[ "${MANIFEST_FIELDS[can_publish]-}" = "false" ] || MANIFEST_VALID=0
[ "${MANIFEST_FIELDS[can_execute]-}" = "false" ] || MANIFEST_VALID=0
[ "$MANIFEST_VALID" -eq 1 ] || fail "snapshot manifest contract is invalid"

"$TAR_BIN" --list --gzip --file "$SNAPSHOT/var-lib-seiche.tgz" >/dev/null
"$TAR_BIN" --list --gzip --file "$SNAPSHOT/api-data.tgz" >/dev/null
"$PG_RESTORE_BIN" --list <"$SNAPSHOT/seiche.dump" >/dev/null
[ -x "$PYTHON_BIN" ] || fail "Python runtime is unavailable"

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
    [ -z "${API_STAGE:-}" ] || rm -rf -- "$API_STAGE"
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
[ -d "$STATE_STAGE/$NBS_STATE_NAME" ] && [ ! -L "$STATE_STAGE/$NBS_STATE_NAME" ] \
    || fail "restored state archive has no safe NBS evidence root"
NBS_PUBLIC_DIR="$STATE_STAGE/$NBS_STATE_NAME/public"
[ -d "$NBS_PUBLIC_DIR" ] && [ ! -L "$NBS_PUBLIC_DIR" ] \
    || fail "restored state archive has no safe NBS public store"
if ! NBS_PUBLIC_REVISION_STORE=$(
    "$PYTHON_BIN" - "$NBS_PUBLIC_DIR" <<'PY'
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

from seiche.nbs_intake import (
    NBSIntakeError,
    NBSNotOnboardedError,
    load_public_context_strict_from_public_dir,
)


public = Path(sys.argv[1])
revisions = public / "revisions"


def normalize_directory(path: Path, mode: int) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("public store path is not a directory")
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def normalize_regular_file(path: Path, metadata: os.stat_result) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise ValueError("public revision changed during normalization")
        os.fchmod(descriptor, 0o640)
    finally:
        os.close(descriptor)


try:
    public_metadata = public.lstat()
    revisions_metadata = revisions.lstat()
    if not stat.S_ISDIR(public_metadata.st_mode) or not stat.S_ISDIR(
        revisions_metadata.st_mode
    ):
        raise ValueError("public store paths are not directories")

    # Extraction intentionally discards archived ownership and permissions.
    # Restore only the code-owned public-store modes before asking the strict
    # loader to validate entry types, link counts, signatures, and the chain.
    normalize_directory(public, 0o750)
    normalize_directory(revisions, 0o2750)
    for entry in revisions.iterdir():
        metadata = entry.lstat()
        if stat.S_ISREG(metadata.st_mode):
            normalize_regular_file(entry, metadata)

    try:
        context = load_public_context_strict_from_public_dir(public)
    except NBSNotOnboardedError:
        print("not_onboarded")
    else:
        if not context.available:
            raise ValueError("strict loader returned an unavailable context")
        print("verified_head")
except (NBSIntakeError, OSError, TypeError, ValueError) as exc:
    raise SystemExit(f"restored NBS public revision store is invalid: {exc}") from exc
PY
); then
    fail "restored NBS public revision store failed strict validation"
fi
case "$NBS_PUBLIC_REVISION_STORE" in
    not_onboarded|verified_head) ;;
    *) fail "restored NBS public revision store returned an invalid state" ;;
esac
rm -rf -- "$STATE_STAGE"
STATE_STAGE=""

API_STAGE=$(mktemp -d "$STATUS_DIR/.backup-api-data-restore.XXXXXX")
"$TAR_BIN" --extract --gzip --file "$SNAPSHOT/api-data.tgz" \
    --directory "$API_STAGE" --no-same-owner --no-same-permissions
API_DATABASE="$API_STAGE/api-data/seiche.sqlite"
[ -f "$API_DATABASE" ] && [ ! -L "$API_DATABASE" ] \
    || fail "restored API SQLite database is missing or unsafe"
"$PYTHON_BIN" - "$API_DATABASE" <<'PY'
import sqlite3
import sys

with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as database:
    result = database.execute("PRAGMA quick_check").fetchone()
    if result != ("ok",):
        raise SystemExit("restored API SQLite database failed PRAGMA quick_check")
PY
rm -rf -- "$API_STAGE"
API_STAGE=""

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
printf '%s' "$EXPECTED_COUNTS" \
    | grep -Eq '^[0-9]+\|[0-9]+\|[0-9]+\|[0-9]+$' \
    || fail "snapshot critical table-count floor has an invalid shape"
IFS='|' read -r ACTUAL_OBSERVATIONS ACTUAL_RUNS ACTUAL_FORWARD ACTUAL_SNAPSHOTS \
    <<<"$ACTUAL_COUNTS"
IFS='|' read -r FLOOR_OBSERVATIONS FLOOR_RUNS FLOOR_FORWARD FLOOR_SNAPSHOTS \
    <<<"$EXPECTED_COUNTS"
[ "$ACTUAL_OBSERVATIONS" -ge "$FLOOR_OBSERVATIONS" ] \
    && [ "$ACTUAL_RUNS" -ge "$FLOOR_RUNS" ] \
    && [ "$ACTUAL_FORWARD" -ge "$FLOOR_FORWARD" ] \
    && [ "$ACTUAL_SNAPSHOTS" -ge "$FLOOR_SNAPSHOTS" ] \
    || fail "restored critical table counts fall below the snapshot floor"
run_as_postgres "$DROPDB_BIN" --if-exists \
    --host=/var/run/postgresql --port="$POSTGRES_PORT" "$SCRATCH"
CREATED=""

STATUS_STAGE=$(mktemp "$STATUS_DIR/.backup-restore-check.XXXXXX")
printf '%s\n' \
    "schema=seiche.market-backup-restore-check.v3" \
    "checked_at=$($DATE_BIN -u +%Y-%m-%dT%H:%M:%SZ)" \
    "snapshot=$SNAPSHOT_NAME" \
    "deployed_sha=$(tr -d '[:space:]' <"$SNAPSHOT/deployed-sha.txt")" \
    "critical_table_counts=$ACTUAL_COUNTS" \
    "critical_table_count_floor=$EXPECTED_COUNTS" \
    "database_restore=pass" \
    "state_archive_restore=pass" \
    "nbs_public_revision_store=$NBS_PUBLIC_REVISION_STORE" \
    "api_data_archive_restore=pass" \
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
