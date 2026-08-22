#!/usr/bin/env bash
# Restore the newest Seiche database snapshot into an isolated scratch DB.
set -euo pipefail
umask 0077

BACKUP_DIR="${SEICHE_MARKET_BACKUP_DIR:-/var/backups/seiche-market}"
NBS_STATE_DIR="${SEICHE_NBS_STATE_DIR:-/var/lib/seiche-nbs}"
NBS_RUNTIME_ROOT="${SEICHE_NBS_RUNTIME_ROOT:-/opt/seiche-nbs-intake}"
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
PYTHON_BIN="${SEICHE_PYTHON_BIN:-/usr/bin/python3}"
ALLOW_NON_ROOT_TEST="${SEICHE_ALLOW_NON_ROOT_BACKUP_TEST:-0}"

fail() {
    echo "seiche market restore check: $*" >&2
    exit 1
}

case "$BACKUP_DIR" in
    /*) ;;
    *) fail "backup directory must be absolute" ;;
esac
[ "$BACKUP_DIR" != "/" ] || fail "refusing a filesystem-root backup directory"
case "$ALLOW_NON_ROOT_TEST" in
    0|1) ;;
    *) fail "non-root backup test flag must be exactly 0 or 1" ;;
esac
CURRENT_EUID="${EUID:-$(id -u)}"
if [ "$ALLOW_NON_ROOT_TEST" = "1" ]; then
    [ "$CURRENT_EUID" -ne 0 ] \
        || fail "non-root restore test mode cannot run as root"
    EXPECTED_RUNTIME_UID=$(id -u)
    EXPECTED_RUNTIME_GID=$(id -g)
    [ "$NBS_RUNTIME_ROOT" != /opt/seiche-nbs-intake ] \
        || fail "non-root restore tests must isolate the NBS runtime"
else
    [ "$CURRENT_EUID" -eq 0 ] || fail "must run as root"
    EXPECTED_RUNTIME_UID=0
    EXPECTED_RUNTIME_GID=0
    [ "$NBS_STATE_DIR" = /var/lib/seiche-nbs ] \
        || fail "production NBS state root is fixed at /var/lib/seiche-nbs"
    [ "$NBS_RUNTIME_ROOT" = /opt/seiche-nbs-intake ] \
        || fail "production NBS runtime is fixed at /opt/seiche-nbs-intake"
    [ "$PYTHON_BIN" = /usr/bin/python3 ] \
        || fail "production Python runtime is fixed at /usr/bin/python3"
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
# Seiche research-only backup. Parse the closed v3 contract without sourcing
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
        schema|created_at|database|postgres_port|state_root|nbs_state_root|\
        nbs_full_store_audit_contract|nbs_full_store_audit_result|api_data_root|\
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

[ "${#MANIFEST_FIELDS[@]}" -eq 13 ] || MANIFEST_VALID=0
[ "${MANIFEST_FIELDS[schema]-}" = "seiche.market-backup.v3" ] \
    || MANIFEST_VALID=0
[ "${MANIFEST_FIELDS[created_at]-}" = "$SNAPSHOT_NAME" ] \
    || MANIFEST_VALID=0
[ "${MANIFEST_FIELDS[database]-}" = "$DATABASE_NAME" ] \
    || MANIFEST_VALID=0
case "${MANIFEST_FIELDS[postgres_port]-}" in
    ''|*[!0-9]*) MANIFEST_VALID=0 ;;
esac
safe_manifest_root "${MANIFEST_FIELDS[state_root]-}" || MANIFEST_VALID=0
safe_manifest_root "${MANIFEST_FIELDS[nbs_state_root]-}" || MANIFEST_VALID=0
[ "${MANIFEST_FIELDS[nbs_state_root]-}" = "$NBS_STATE_DIR" ] \
    || MANIFEST_VALID=0
[ "${MANIFEST_FIELDS[nbs_full_store_audit_contract]-}" = \
    "seiche.nbs-full-store-audit.v1" ] || MANIFEST_VALID=0
[ "${MANIFEST_FIELDS[nbs_full_store_audit_result]-}" = \
    "required_at_restore" ] || MANIFEST_VALID=0
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
NBS_RESTORED_ROOT="$STATE_STAGE/$NBS_STATE_NAME"
if ! NBS_FULL_STORE_AUDIT_RESULT=$(
    "$PYTHON_BIN" -I -B - "$NBS_RESTORED_ROOT" "$NBS_RUNTIME_ROOT" \
        "$EXPECTED_RUNTIME_UID" "$EXPECTED_RUNTIME_GID" \
        "$ALLOW_NON_ROOT_TEST" <<'PY'
from __future__ import annotations

import importlib
import os
import re
import stat
import sys
from pathlib import Path

(
    store_text,
    runtime_text,
    expected_uid_text,
    expected_gid_text,
    test_override,
) = sys.argv[1:]
store = Path(store_text)
public = store / "public"
runtime = Path(runtime_text)
expected_uid = int(expected_uid_text)
expected_gid = int(expected_gid_text)
revisions = public / "revisions"
sha_re = re.compile(r"[0-9a-f]{40}")
digest_re = re.compile(r"[0-9a-f]{64}")
export_re = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
expected_module_names = ("__init__.py", "nbs_intake.py", "nbs_trust.py")
maximum_module_bytes = 512 * 1024
directory_flags = (
    os.O_RDONLY
    | os.O_DIRECTORY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


def metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_uid,
        metadata.st_gid,
    )


def metadata_exact(
    metadata: os.stat_result, *, directory: bool, mode: int, links: int | None = None
) -> bool:
    return (
        (stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode))
        and metadata.st_uid == expected_uid
        and metadata.st_gid == expected_gid
        and stat.S_IMODE(metadata.st_mode) == mode
        and (links is None or metadata.st_nlink == links)
    )


def open_directory_at(
    parent_fd: int,
    name: str,
    *,
    mode: int,
    exact_entries: set[str] | None = None,
) -> tuple[int, os.stat_result]:
    descriptor = -1
    try:
        descriptor = os.open(name, directory_flags, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not metadata_exact(opened, directory=True, mode=mode)
            or not stat.S_ISDIR(visible.st_mode)
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            raise ValueError(f"sealed runtime directory is unsafe: {name}")
        if exact_entries is not None and set(os.listdir(descriptor)) != exact_entries:
            raise ValueError(f"sealed runtime directory members are not exact: {name}")
        return descriptor, opened
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def verify_directory_at(
    parent_fd: int,
    name: str,
    descriptor: int,
    original: os.stat_result,
) -> None:
    opened = os.fstat(descriptor)
    visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        metadata_identity(opened) != metadata_identity(original)
        or not stat.S_ISDIR(visible.st_mode)
        or (visible.st_dev, visible.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        raise ValueError(f"sealed runtime directory changed: {name}")


def read_regular_at(
    parent_fd: int,
    name: str,
    *,
    mode: int,
    minimum_bytes: int,
    maximum_bytes: int,
) -> tuple[bytes, int, os.stat_result]:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        before = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not metadata_exact(before, directory=False, mode=mode, links=1)
            or not minimum_bytes <= before.st_size <= maximum_bytes
            or not stat.S_ISREG(visible.st_mode)
            or (before.st_dev, before.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            raise ValueError(f"sealed runtime file metadata is unsafe: {name}")
        body = bytearray()
        while len(body) <= maximum_bytes:
            chunk = os.read(
                descriptor,
                min(65536, maximum_bytes + 1 - len(body)),
            )
            if not chunk:
                break
            body.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(body) > maximum_bytes
            or metadata_identity(before) != metadata_identity(after)
        ):
            raise ValueError(f"sealed runtime file changed while read: {name}")
        return bytes(body), descriptor, before
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def open_runtime_root() -> int:
    if (
        not runtime_text.startswith("/")
        or os.path.normpath(runtime_text) != runtime_text
        or runtime_text == "/"
    ):
        raise ValueError("sealed runtime path is not canonical")
    descriptor = -1
    try:
        descriptor = os.open("/", directory_flags)
        root_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or (
                test_override == "0"
                and (
                    root_metadata.st_uid != 0
                    or root_metadata.st_gid != 0
                    or stat.S_IMODE(root_metadata.st_mode) & 0o022
                )
            )
        ):
            raise ValueError("sealed runtime filesystem root is unsafe")
        for component in runtime.parts[1:]:
            child = os.open(component, directory_flags, dir_fd=descriptor)
            visible = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            opened = os.fstat(child)
            if (
                not stat.S_ISDIR(visible.st_mode)
                or (visible.st_dev, visible.st_ino)
                != (opened.st_dev, opened.st_ino)
                or (
                    test_override == "0"
                    and (
                        opened.st_uid != 0
                        or opened.st_gid != 0
                        or stat.S_IMODE(opened.st_mode) & 0o022
                    )
                )
            ):
                os.close(child)
                raise ValueError("sealed runtime ancestry is unsafe")
            os.close(descriptor)
            descriptor = child
        if (
            not metadata_exact(os.fstat(descriptor), directory=True, mode=0o755)
            or set(os.listdir(descriptor)) != {"current-sha", "releases"}
        ):
            raise ValueError("sealed runtime root metadata or members are unsafe")
        return descriptor
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def read_pointer(runtime_fd: int) -> str:
    body, pointer_fd, _pointer_metadata = read_regular_at(
        runtime_fd,
        "current-sha",
        mode=0o444,
        minimum_bytes=41,
        maximum_bytes=41,
    )
    os.close(pointer_fd)
    try:
        text = body.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("sealed runtime pointer is not ASCII") from exc
    if len(body) != 41 or not text.endswith("\n") or sha_re.fullmatch(text[:-1]) is None:
        raise ValueError("sealed runtime pointer is invalid")
    return text[:-1]


def inspect_directory(path: Path, label: str) -> set[str]:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(descriptor)
        visible = path.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(visible.st_mode)
            or opened.st_uid != expected_uid
            or opened.st_gid != expected_gid
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            raise ValueError(f"restored NBS directory is unsafe: {label}")
        return set(os.listdir(descriptor))
    finally:
        os.close(descriptor)


def inspect_regular_file(path: Path, label: str) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(descriptor)
        visible = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(visible.st_mode)
            or opened.st_uid != expected_uid
            or opened.st_gid != expected_gid
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            raise ValueError(f"restored NBS file is unsafe: {label}")
    finally:
        os.close(descriptor)


def normalize_directory(path: Path, mode: int) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def normalize_regular_file(path: Path, mode: int) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def normalize_restored_store() -> None:
    directories: list[tuple[Path, int]] = []
    files: list[tuple[Path, int]] = []

    root_names = inspect_directory(store, "evidence root")
    directories.append((store, 0o750))
    minimal_root = {"restricted", "public"}
    initialized_root = {
        ".nbs-intake.lock",
        ".staging",
        "restricted",
        "public",
    }
    if root_names == minimal_root:
        initialized = False
    elif root_names == initialized_root:
        initialized = True
    else:
        raise ValueError("restored NBS evidence-root members are not exact")

    restricted = store / "restricted"
    restricted_names = inspect_directory(restricted, "restricted root")
    directories.append((restricted, 0o700))
    public_names = inspect_directory(public, "public root")
    if public_names != {"revisions"}:
        raise ValueError("restored NBS public-root members are not exact")
    directories.append((public, 0o750))
    revision_names = inspect_directory(revisions, "public revisions")
    directories.append((revisions, 0o2750))

    if not initialized:
        if restricted_names or revision_names:
            raise ValueError("minimal restored NBS store is not safely empty")
    else:
        if restricted_names != {"objects", "exports"}:
            raise ValueError("restored NBS restricted-root members are not exact")
        staging = store / ".staging"
        if inspect_directory(staging, "atomic staging"):
            raise ValueError("restored NBS atomic staging is not empty")
        directories.append((staging, 0o700))
        lock = store / ".nbs-intake.lock"
        inspect_regular_file(lock, "intake lock")
        files.append((lock, 0o600))

        objects = restricted / "objects"
        if inspect_directory(objects, "restricted objects") != {"sha256"}:
            raise ValueError("restored NBS object-store members are not exact")
        directories.append((objects, 0o700))
        sha256_root = objects / "sha256"
        bucket_names = inspect_directory(sha256_root, "restricted SHA-256 root")
        directories.append((sha256_root, 0o700))
        for bucket in bucket_names:
            if re.fullmatch(r"[0-9a-f]{2}", bucket) is None:
                raise ValueError("restored NBS raw-object bucket name is invalid")
            bucket_path = sha256_root / bucket
            object_names = inspect_directory(bucket_path, "raw-object bucket")
            directories.append((bucket_path, 0o700))
            for object_name in object_names:
                if (
                    digest_re.fullmatch(object_name) is None
                    or not object_name.startswith(bucket)
                ):
                    raise ValueError("restored NBS raw-object name is invalid")
                object_path = bucket_path / object_name
                inspect_regular_file(object_path, "raw object")
                files.append((object_path, 0o600))

        exports = restricted / "exports"
        export_names = inspect_directory(exports, "restricted exports")
        directories.append((exports, 0o700))
        export_ids = export_names - {".head.json"}
        if any(export_re.fullmatch(export_id) is None for export_id in export_ids):
            raise ValueError("restored NBS export identity is invalid")
        if export_ids and ".head.json" not in export_names:
            raise ValueError("restored NBS restricted head is missing")
        if not export_ids and ".head.json" in export_names:
            raise ValueError("restored NBS restricted head has no exports")
        if ".head.json" in export_names:
            restricted_head = exports / ".head.json"
            inspect_regular_file(restricted_head, "restricted head")
            files.append((restricted_head, 0o600))
        for export_id in export_ids:
            export_path = exports / export_id
            if inspect_directory(export_path, "restricted export") != {
                "manifest.json",
                "signature.json",
            }:
                raise ValueError("restored NBS restricted export members are not exact")
            directories.append((export_path, 0o700))
            for name in ("manifest.json", "signature.json"):
                evidence_path = export_path / name
                inspect_regular_file(evidence_path, f"restricted {name}")
                files.append((evidence_path, 0o600))

        revision_ids: set[str] = set()
        for name in revision_names - {".head.json"}:
            if not name.endswith(".json") or export_re.fullmatch(name[:-5]) is None:
                raise ValueError("restored NBS public revision identity is invalid")
            revision_ids.add(name[:-5])
        if revision_ids != export_ids:
            raise ValueError("restored NBS restricted/public revision sets differ")
        if export_ids and ".head.json" not in revision_names:
            raise ValueError("restored NBS public head is missing")
        if not export_ids and ".head.json" in revision_names:
            raise ValueError("restored NBS public head has no revisions")
        for name in revision_names:
            revision_path = revisions / name
            inspect_regular_file(revision_path, "public revision")
            files.append((revision_path, 0o640))

    for path, mode in directories:
        normalize_directory(path, mode)
    for path, mode in files:
        normalize_regular_file(path, mode)


def verify_restored_full_store() -> str:
    runtime_fd = releases_fd = release_fd = package_fd = -1
    module_descriptors: dict[str, tuple[int, os.stat_result]] = {}
    try:
        runtime_fd = open_runtime_root()
        target = read_pointer(runtime_fd)
        releases_fd, releases_metadata = open_directory_at(
            runtime_fd,
            "releases",
            mode=0o555,
        )
        release_names = os.listdir(releases_fd)
        if not release_names or any(
            sha_re.fullmatch(name) is None for name in release_names
        ):
            raise ValueError("sealed releases root contains an unexpected member")
        release_fd, release_metadata = open_directory_at(
            releases_fd,
            target,
            mode=0o555,
            exact_entries={"seiche"},
        )
        package_fd, package_metadata = open_directory_at(
            release_fd,
            "seiche",
            mode=0o555,
            exact_entries=set(expected_module_names),
        )
        for module_name in expected_module_names:
            _body, module_fd, module_metadata = read_regular_at(
                package_fd,
                module_name,
                mode=0o444,
                minimum_bytes=0,
                maximum_bytes=maximum_module_bytes,
            )
            module_descriptors[module_name] = (module_fd, module_metadata)

        release_path = runtime / "releases" / target
        initial_path = tuple(sys.path)
        if any(
            not isinstance(path, str)
            or not path
            or path == "/home"
            or path.startswith("/home/")
            or (
                test_override == "0"
                and (not os.path.isabs(path) or os.path.normpath(path) != path)
            )
            for path in initial_path
        ):
            raise ValueError("isolated interpreter has an unsafe import path")
        if any(name == "seiche" or name.startswith("seiche.") for name in sys.modules):
            raise ValueError("sealed package was imported before origin selection")

        if test_override == "1":
            import_root = release_path
        else:
            import_root = Path(f"/proc/self/fd/{release_fd}")
            proc_metadata = os.stat(import_root)
            if (proc_metadata.st_dev, proc_metadata.st_ino) != (
                release_metadata.st_dev,
                release_metadata.st_ino,
            ):
                raise ValueError("sealed release descriptor path is unavailable")
        sys.path.insert(0, str(import_root))
        package = importlib.import_module("seiche")
        nbs_intake = importlib.import_module("seiche.nbs_intake")
        nbs_trust = importlib.import_module("seiche.nbs_trust")

        expected_origins = {
            "__init__.py": package,
            "nbs_intake.py": nbs_intake,
            "nbs_trust.py": nbs_trust,
        }
        expected_package = import_root / "seiche"
        if list(package.__path__) != [str(expected_package)]:
            raise ValueError("sealed package search path has the wrong origin")
        for module_name, module in expected_origins.items():
            expected_origin = expected_package / module_name
            if (
                not isinstance(module.__file__, str)
                or Path(module.__file__) != expected_origin
            ):
                raise ValueError(f"sealed module imported from the wrong origin: {module_name}")
            visible = os.stat(module.__file__)
            module_fd, original = module_descriptors[module_name]
            opened = os.fstat(module_fd)
            if (
                metadata_identity(opened) != metadata_identity(original)
                or (visible.st_dev, visible.st_ino)
                != (opened.st_dev, opened.st_ino)
            ):
                raise ValueError(f"sealed module changed during import: {module_name}")
        if (
            not callable(nbs_trust.verify_trusted_ed25519_signature)
            or nbs_intake.verify_trusted_ed25519_signature
            is not nbs_trust.verify_trusted_ed25519_signature
            or not callable(nbs_intake.NBSIntakeStore.audit_store_strict)
        ):
            raise ValueError("sealed NBS audit/trust origin is inconsistent")
        if read_pointer(runtime_fd) != target:
            raise ValueError("sealed runtime pointer changed during import")

        verify_directory_at(runtime_fd, "releases", releases_fd, releases_metadata)
        verify_directory_at(releases_fd, target, release_fd, release_metadata)
        verify_directory_at(release_fd, "seiche", package_fd, package_metadata)

        # Extraction intentionally discards archived ownership and permissions.
        # Reject the complete topology first, then restore only the code-owned
        # modes needed by the sealed read-only audit.  The audit itself performs
        # no reconciliation, layout creation, chmod, unlink, or lock mutation.
        normalize_restored_store()
        result = nbs_intake.NBSIntakeStore(store).audit_store_strict()
        if result not in {"not_onboarded", "verified_head"}:
            raise ValueError("sealed full-store audit returned an invalid state")

        if read_pointer(runtime_fd) != target:
            raise ValueError("sealed runtime pointer changed during validation")
        verify_directory_at(runtime_fd, "releases", releases_fd, releases_metadata)
        verify_directory_at(releases_fd, target, release_fd, release_metadata)
        verify_directory_at(release_fd, "seiche", package_fd, package_metadata)
        for module_name, (module_fd, original) in module_descriptors.items():
            opened = os.fstat(module_fd)
            visible = os.stat(module_name, dir_fd=package_fd, follow_symlinks=False)
            if (
                metadata_identity(opened) != metadata_identity(original)
                or not stat.S_ISREG(visible.st_mode)
                or (visible.st_dev, visible.st_ino)
                != (opened.st_dev, opened.st_ino)
            ):
                raise ValueError(f"sealed module changed during validation: {module_name}")
        return result
    finally:
        for descriptor, _metadata in module_descriptors.values():
            os.close(descriptor)
        for descriptor in (package_fd, release_fd, releases_fd, runtime_fd):
            if descriptor >= 0:
                os.close(descriptor)


try:
    print(verify_restored_full_store())
except Exception as exc:
    raise SystemExit(f"restored NBS evidence store is invalid: {exc}") from exc
PY
); then
    fail "restored NBS evidence store failed strict validation"
fi
case "$NBS_FULL_STORE_AUDIT_RESULT" in
    not_onboarded|verified_head) ;;
    *) fail "restored NBS evidence store returned an invalid audit state" ;;
esac
rm -rf -- "$STATE_STAGE"
STATE_STAGE=""

API_STAGE=$(mktemp -d "$STATUS_DIR/.backup-api-data-restore.XXXXXX")
"$TAR_BIN" --extract --gzip --file "$SNAPSHOT/api-data.tgz" \
    --directory "$API_STAGE" --no-same-owner --no-same-permissions
API_DATABASE="$API_STAGE/api-data/seiche.sqlite"
[ -f "$API_DATABASE" ] && [ ! -L "$API_DATABASE" ] \
    || fail "restored API SQLite database is missing or unsafe"
"$PYTHON_BIN" -I -B - "$API_DATABASE" <<'PY'
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
    "schema=seiche.market-backup-restore-check.v4" \
    "checked_at=$($DATE_BIN -u +%Y-%m-%dT%H:%M:%SZ)" \
    "snapshot=$SNAPSHOT_NAME" \
    "deployed_sha=$(tr -d '[:space:]' <"$SNAPSHOT/deployed-sha.txt")" \
    "critical_table_counts=$ACTUAL_COUNTS" \
    "critical_table_count_floor=$EXPECTED_COUNTS" \
    "database_restore=pass" \
    "state_archive_restore=pass" \
    "nbs_full_store_audit_contract=seiche.nbs-full-store-audit.v1" \
    "nbs_full_store_audit_result=$NBS_FULL_STORE_AUDIT_RESULT" \
    "nbs_public_revision_store=$NBS_FULL_STORE_AUDIT_RESULT" \
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
