#!/bin/bash -p
# Finish release-bound backup, restore, and readiness evidence after cutover.
set -euo pipefail
set -f
umask 0077
export LC_ALL=C

readonly RELEASE_ENV=/etc/seiche/release.env
readonly DEPLOYED_STATE=/var/lib/seiche-deploy/deployed-sha
readonly RECEIPT_DIR=/var/lib/seiche-control/receipts
readonly RESTORE_RECEIPT=/var/lib/seiche-recovery-proof/backup-restore-check.status
readonly BACKUP_DIR=/var/backups/seiche-market
readonly READINESS_SCRIPT=/etc/seiche/libexec/seiche-data-readiness.sh
readonly HEALTH_URL=http://127.0.0.1:8787/api/internal/v1/release-health
readonly REFRESH_URL=http://127.0.0.1:8787/api/gauge
readonly SYSTEMCTL=/usr/bin/systemctl
readonly CURL=/usr/bin/curl
readonly PYTHON=/usr/bin/python3
readonly MKTEMP=/usr/bin/mktemp
readonly RM=/usr/bin/rm
readonly SYNC=/usr/bin/sync
readonly MAX_FRESH_WAIT_SECONDS=900
readonly MAX_GENERATED_AGE_SECONDS=900
readonly READINESS_REQUIRED_UNITS="seiche-api.service seiche-market-worker.service seiche-source-worker.service seiche-market-backup.timer seiche-market-restore-check.timer seiche-market-validation.timer seiche-release-poll.timer"

fail() {
    printf 'seiche release recovery: %s\n' "$*" >&2
    exit 1
}

[ "${EUID:-$(id -u)}" -eq 0 ] || fail "must run as root"
for command_path in "$SYSTEMCTL" "$CURL" "$PYTHON" "$MKTEMP" "$RM" "$SYNC"; do
    [ -x "$command_path" ] || fail "required command is unavailable: $command_path"
done
[ -x "$READINESS_SCRIPT" ] || fail "data-readiness helper is unavailable"

# Validate all root-selected identities before starting any writer. The output
# contains only fixed-format hashes and one state word, so shell parsing cannot
# turn file content into a command or path.
load_release_identity() {
    "$PYTHON" -I -B - \
        "$RELEASE_ENV" "$DEPLOYED_STATE" "$RECEIPT_DIR" <<'PY'
from __future__ import annotations

from datetime import UTC, datetime
import grp
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path


release_env_raw, deployed_state_raw, receipt_dir_raw = sys.argv[1:]
release_env = Path(release_env_raw)
deployed_state = Path(deployed_state_raw)
receipt_dir = Path(receipt_dir_raw)
sha_re = re.compile(r"[0-9a-f]{40}")
digest_re = re.compile(r"[0-9a-f]{64}")
timestamp_re = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
seiche_gid = grp.getgrnam("seiche").gr_gid


def read_exact(path: Path, *, uid: int, gid: int, mode: int, maximum: int) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        visible = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != uid
            or before.st_gid != gid
            or stat.S_IMODE(before.st_mode) != mode
            or not 0 < before.st_size <= maximum
            or not stat.S_ISREG(visible.st_mode)
            or (before.st_dev, before.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            raise ValueError(f"unsafe metadata: {path}")
        body = os.read(descriptor, maximum + 1)
        after = os.fstat(descriptor)
        if len(body) > maximum or (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError(f"changed while reading: {path}")
        return body
    finally:
        os.close(descriptor)


directory = receipt_dir.lstat()
if (
    not stat.S_ISDIR(directory.st_mode)
    or directory.st_uid != 0
    or directory.st_gid != 0
    or stat.S_IMODE(directory.st_mode) != 0o700
):
    raise SystemExit("recovery receipt directory metadata is unsafe")

env_body = read_exact(
    release_env, uid=0, gid=seiche_gid, mode=0o640, maximum=4096
).decode("utf-8")
settings: dict[str, str] = {}
for line in env_body.splitlines():
    key, separator, value = line.partition("=")
    if not separator or not key or key in settings:
        raise SystemExit("release environment is malformed")
    settings[key] = value
allowed = {
    "SEICHE_RELEASE_SHA",
    "SEICHE_PREBUILT_HANDOFF_ID",
    "SEICHE_PREBUILT_PAYLOAD_SHA256",
}
if not set(settings) <= allowed or "SEICHE_RELEASE_SHA" not in settings:
    raise SystemExit("release environment shape is invalid")
if set(settings) != {"SEICHE_RELEASE_SHA"} and set(settings) != allowed:
    raise SystemExit("release prebuild binding is incomplete")
target = settings["SEICHE_RELEASE_SHA"]
if sha_re.fullmatch(target) is None:
    raise SystemExit("release target is invalid")
for key in allowed - {"SEICHE_RELEASE_SHA"}:
    if key in settings and digest_re.fullmatch(settings[key]) is None:
        raise SystemExit("release prebuild binding is invalid")

deployed = read_exact(
    deployed_state, uid=0, gid=0, mode=0o600, maximum=64
).decode("ascii").strip()
if deployed != target:
    raise SystemExit("deployed state changed before recovery sealing")

release_path = receipt_dir / f"{target}.release.json"
if not release_path.exists() and not release_path.is_symlink():
    print(target, "-", "-", "awaiting-receipt")
    raise SystemExit(0)
release_body = read_exact(
    release_path, uid=0, gid=0, mode=0o400, maximum=64 * 1024
)
release = json.loads(release_body)
canonical_release = (
    json.dumps(release, sort_keys=True, separators=(",", ":")) + "\n"
).encode()
if release_body != canonical_release or not isinstance(release, dict):
    raise SystemExit("release receipt is not canonical")
common = {
    "kind": "release",
    "commit": target,
    "conclusion": "success",
}
if any(release.get(key) != value for key, value in common.items()):
    raise SystemExit("release receipt identity is invalid")
tree = release.get("tree")
if sha_re.fullmatch(tree if isinstance(tree, str) else "") is None:
    raise SystemExit("release receipt tree is invalid")
schema = release.get("schema")
v2_keys = {
    "schema", "kind", "commit", "tree", "started_at", "completed_at",
    "conclusion", "gate_receipt_sha256",
}
v3_keys = v2_keys | {"snapshot_receipt_sha256"}
if schema == "seiche.release-receipt.v2":
    if set(release) != v2_keys:
        raise SystemExit("v2 release receipt shape is invalid")
elif schema == "seiche.release-receipt.v3":
    if set(release) != v3_keys:
        raise SystemExit("v3 release receipt shape is invalid")
else:
    raise SystemExit("release receipt schema is unsupported")
for key in ("started_at", "completed_at"):
    if timestamp_re.fullmatch(release.get(key, "")) is None:
        raise SystemExit("release receipt timestamp is invalid")
release_started = datetime.fromisoformat(
    release["started_at"].replace("Z", "+00:00")
).astimezone(UTC)
release_completed = datetime.fromisoformat(
    release["completed_at"].replace("Z", "+00:00")
).astimezone(UTC)
if release_started > release_completed:
    raise SystemExit("release receipt timestamp order is invalid")
for key in set(release) & {"gate_receipt_sha256", "snapshot_receipt_sha256"}:
    if digest_re.fullmatch(release[key]) is None:
        raise SystemExit("release receipt digest is invalid")
release_digest = hashlib.sha256(release_body).hexdigest()

recovery_path = receipt_dir / f"{target}.recovery.json"
state = "pending"
if recovery_path.exists() or recovery_path.is_symlink():
    recovery_body = read_exact(
        recovery_path, uid=0, gid=0, mode=0o400, maximum=64 * 1024
    )
    recovery = json.loads(recovery_body)
    canonical_recovery = (
        json.dumps(recovery, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    expected_keys = {
        "schema", "kind", "commit", "tree", "release_receipt_sha256",
        "backup_snapshot", "backup_inventory_sha256", "restore_checked_at",
        "restore_receipt_sha256", "worker_startup", "data_readiness",
        "offsite_schedule", "completed_at", "conclusion",
    }
    valid = (
        recovery_body == canonical_recovery
        and isinstance(recovery, dict)
        and set(recovery) == expected_keys
        and recovery.get("schema") == "seiche.release-recovery-receipt.v1"
        and recovery.get("kind") == "recovery"
        and recovery.get("commit") == target
        and recovery.get("tree") == tree
        and recovery.get("release_receipt_sha256") == release_digest
        and re.fullmatch(r"20[0-9]{6}T[0-9]{6}Z", recovery.get("backup_snapshot", ""))
        is not None
        and digest_re.fullmatch(recovery.get("backup_inventory_sha256", ""))
        is not None
        and digest_re.fullmatch(recovery.get("restore_receipt_sha256", ""))
        is not None
        and timestamp_re.fullmatch(recovery.get("restore_checked_at", ""))
        is not None
        and timestamp_re.fullmatch(recovery.get("completed_at", "")) is not None
        and recovery.get("worker_startup") == "ready"
        and recovery.get("data_readiness") == "ready"
        and recovery.get("offsite_schedule") in {"active", "disabled"}
        and recovery.get("conclusion") == "success"
    )
    if not valid:
        raise SystemExit("existing recovery receipt is invalid")
    restore_checked = datetime.fromisoformat(
        recovery["restore_checked_at"].replace("Z", "+00:00")
    ).astimezone(UTC)
    backup_created = datetime.strptime(
        recovery["backup_snapshot"], "%Y%m%dT%H%M%SZ"
    ).replace(tzinfo=UTC)
    recovery_completed = datetime.fromisoformat(
        recovery["completed_at"].replace("Z", "+00:00")
    ).astimezone(UTC)
    if (
        release_completed > recovery_completed
        or backup_created > restore_checked
        or restore_checked > recovery_completed
    ):
        raise SystemExit("existing recovery receipt timestamp order is invalid")
    state = "complete"

print(target, tree, release_digest, state)
PY
}

IDENTITY=$(load_release_identity) || fail "release identity is not sealable"
read -r TARGET TREE RELEASE_RECEIPT_SHA256 RECOVERY_STATE <<<"$IDENTITY"
[[ "$TARGET" =~ ^[0-9a-f]{40}$ ]] \
    || fail "validated release identity output is malformed"
case "$RECOVERY_STATE" in
    complete)
        [[ "$TREE" =~ ^[0-9a-f]{40}$ ]] \
            && [[ "$RELEASE_RECEIPT_SHA256" =~ ^[0-9a-f]{64}$ ]] \
            || fail "validated complete recovery identity is malformed"
        printf 'seiche release recovery: %s already sealed\n' "${TARGET:0:7}"
        exit 0
        ;;
    pending)
        [[ "$TREE" =~ ^[0-9a-f]{40}$ ]] \
            && [[ "$RELEASE_RECEIPT_SHA256" =~ ^[0-9a-f]{64}$ ]] \
            || fail "validated pending recovery identity is malformed"
        ;;
    awaiting-receipt)
        [ "$TREE" = - ] && [ "$RELEASE_RECEIPT_SHA256" = - ] \
            || fail "validated receipt-pending identity is malformed"
        TREE=""
        RELEASE_RECEIPT_SHA256=""
        ;;
    *) fail "validated recovery state is malformed" ;;
esac

assert_release_identity() {
    local current="" current_target="" current_tree="" current_digest="" _state=""
    current=$(load_release_identity) || return 1
    read -r current_target current_tree current_digest _state <<<"$current"
    [ "$current_target" = "$TARGET" ] || return 1
    case "$_state" in
        awaiting-receipt)
            [ -z "$TREE" ] && [ "$current_tree" = - ] \
                && [ "$current_digest" = - ]
            ;;
        pending|complete)
            [[ "$current_tree" =~ ^[0-9a-f]{40}$ ]] \
                && [[ "$current_digest" =~ ^[0-9a-f]{64}$ ]] \
                && { [ -z "$TREE" ] \
                    || { [ "$current_tree" = "$TREE" ] \
                        && [ "$current_digest" = "$RELEASE_RECEIPT_SHA256" ]; }; }
            ;;
        *) return 1 ;;
    esac
}

run_recovery_proof_preflight() {
    /usr/bin/env -i \
        HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
        SEICHE_DATA_READINESS_PROOF_ONLY=1 \
        SEICHE_DATA_READINESS_SKIP_OFFSITE=1 \
        SEICHE_DATA_READINESS_REQUIRED_UNITS= \
        /usr/bin/bash -p "$READINESS_SCRIPT"
}

run_operational_readiness_preflight() {
    /usr/bin/env -i \
        HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
        SEICHE_DATA_READINESS_SKIP_OFFSITE=1 \
        SEICHE_DATA_READINESS_REQUIRED_UNITS="$READINESS_REQUIRED_UNITS" \
        /usr/bin/bash -p "$READINESS_SCRIPT"
}

candidate_health_once() {
    local body="" status=0
    body=$("$MKTEMP" /tmp/seiche-recovery-health.XXXXXX) || return 1
    if ! "$CURL" --fail --silent --show-error --proto '=http' \
            --connect-timeout 10 --max-time 20 --output "$body" \
            "$HEALTH_URL" \
        || ! "$PYTHON" -I -B - "$body" "$TARGET" \
            "$MAX_GENERATED_AGE_SECONDS" <<'PY'
from datetime import UTC, datetime
import json
import re
import sys
import time

path, expected, max_age_raw = sys.argv[1:]
try:
    payload = json.load(open(path, encoding="utf-8"))
    candidate = payload["release_candidate"]
    if (
        not isinstance(payload, dict)
        or set(candidate) != {"producer_sha", "activation_token"}
        or candidate["producer_sha"] != expected
        or re.fullmatch(r"[0-9a-f]{64}", candidate["activation_token"]) is None
    ):
        raise ValueError
    generated = payload["generated_at"]
    parsed = datetime.fromisoformat(
        generated[:-1] + "+00:00" if generated.endswith("Z") else generated
    ).astimezone(UTC)
    age = time.time() - parsed.timestamp()
    if age < -300 or age > int(max_age_raw):
        raise ValueError
except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1) from None
PY
    then
        status=1
    fi
    "$RM" -f -- "$body"
    return "$status"
}

wait_for_fresh_candidate() {
    local deadline=$((SECONDS + MAX_FRESH_WAIT_SECONDS))
    while ! candidate_health_once; do
        assert_release_identity || return 1
        [ "$SECONDS" -lt "$deadline" ] || return 1
        sleep 10
    done
}

converge_operational_readiness() {
    local output="" status=0 deadline=$((SECONDS + MAX_FRESH_WAIT_SECONDS))
    while true; do
        if output=$(run_operational_readiness_preflight 2>&1); then
            [ "$output" = "seiche data readiness: ready" ] || return 1
            return 0
        fi
        status=$?
        if [ "$status" -ne 1 ] \
                || [ "$output" != "seiche data readiness: API snapshot stale" ]; then
            [ -z "$output" ] || printf '%s\n' "$output" >&2
            return 1
        fi
        "$SYSTEMCTL" is-active --quiet seiche-api.service || return 1
        "$CURL" --fail --silent --show-error --proto '=http' \
            --connect-timeout 10 --max-time 20 --output /dev/null \
            "$REFRESH_URL" || return 1
        assert_release_identity || return 1
        [ "$SECONDS" -lt "$deadline" ] || return 1
        sleep 10
    done
}

"$SYSTEMCTL" reset-failed \
    seiche-market-worker.service seiche-source-worker.service \
    seiche-market-backfill.service 2>/dev/null || true
"$SYSTEMCTL" start \
    seiche-market-backfill.service seiche-market-worker.service \
    || fail "market backfill/worker did not become ready"
"$SYSTEMCTL" start seiche-source-worker.service \
    || fail "source worker did not complete its durable startup sweep"
assert_release_identity || fail "release identity changed during worker startup"

if ! run_recovery_proof_preflight; then
    printf 'seiche release recovery: creating release-bound backup and restore proof\n'
    "$SYSTEMCTL" start seiche-market-backup.service \
        || fail "release-bound backup failed"
    assert_release_identity || fail "release identity changed during backup"
    "$SYSTEMCTL" start seiche-market-restore-check.service \
        || fail "release-bound isolated restore failed"
    assert_release_identity || fail "release identity changed during restore"
    run_recovery_proof_preflight \
        || fail "backup/restore proof did not bind the deployed release"
fi

# The prebuilt board should normally still be fresh. If a long restore crossed
# the freshness horizon, request a background refresh and retry without
# restarting the already-live API.
"$CURL" --fail --silent --show-error --proto '=http' \
    --connect-timeout 10 --max-time 20 --output /dev/null "$REFRESH_URL" \
    || fail "candidate refresh nudge failed"
wait_for_fresh_candidate \
    || fail "exact candidate did not become fresh without an API restart"
converge_operational_readiness \
    || fail "operational data readiness did not converge"
candidate_health_once \
    || fail "exact candidate lost strict health before recovery sealing"

"$SYSTEMCTL" enable --now seiche-data-readiness.timer \
    || fail "proven data-readiness timer could not be activated"
OFFSITE_SCHEDULE=disabled
if "$SYSTEMCTL" is-enabled --quiet seiche-market-offsite-backup.timer; then
    "$SYSTEMCTL" start seiche-market-offsite-backup.timer \
        || fail "offsite backup timer could not be restored"
    "$SYSTEMCTL" is-active --quiet seiche-market-offsite-backup.timer \
        || fail "offsite backup timer did not become active"
    OFFSITE_SCHEDULE=active
fi
assert_release_identity || fail "release identity changed before receipt sealing"

# A direct SSH recovery deploy can complete while Railway/GitHub evidence is
# temporarily unavailable. Recovery work and recurring timers are still made
# safe above; only the immutable seal waits for the controller's release
# receipt. systemd retries this oneshot until that receipt exists.
FINAL_IDENTITY=$(load_release_identity) \
    || fail "release identity could not be finalized for recovery sealing"
read -r FINAL_TARGET FINAL_TREE FINAL_DIGEST FINAL_STATE <<<"$FINAL_IDENTITY"
[ "$FINAL_TARGET" = "$TARGET" ] \
    || fail "release identity changed before recovery receipt creation"
case "$FINAL_STATE" in
    complete)
        printf 'seiche release recovery: %s was sealed concurrently\n' "${TARGET:0:7}"
        exit 0
        ;;
    pending)
        [[ "$FINAL_TREE" =~ ^[0-9a-f]{40}$ ]] \
            && [[ "$FINAL_DIGEST" =~ ^[0-9a-f]{64}$ ]] \
            || fail "final release receipt identity is malformed"
        TREE=$FINAL_TREE
        RELEASE_RECEIPT_SHA256=$FINAL_DIGEST
        ;;
    awaiting-receipt)
        fail "recovery proof is ready but the immutable release receipt is pending"
        ;;
    *) fail "final recovery state is malformed" ;;
esac
assert_release_identity || fail "release receipt changed before recovery sealing"

"$PYTHON" -I -B - \
    "$TARGET" "$TREE" "$RELEASE_RECEIPT_SHA256" "$OFFSITE_SCHEDULE" \
    "$RECEIPT_DIR" "$RESTORE_RECEIPT" "$BACKUP_DIR" <<'PY'
from __future__ import annotations

from datetime import UTC, datetime
import grp
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path


(
    target,
    tree,
    release_digest,
    offsite_schedule,
    receipt_dir_raw,
    restore_receipt_raw,
    backup_dir_raw,
) = sys.argv[1:]
receipt_dir = Path(receipt_dir_raw)
restore_receipt = Path(restore_receipt_raw)
backup_dir = Path(backup_dir_raw)
digest_re = re.compile(r"[0-9a-f]{64}")
timestamp_re = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
if (
    re.fullmatch(r"[0-9a-f]{40}", target) is None
    or re.fullmatch(r"[0-9a-f]{40}", tree) is None
    or digest_re.fullmatch(release_digest) is None
    or offsite_schedule not in {"active", "disabled"}
):
    raise SystemExit("recovery receipt inputs are invalid")

def read_exact_file(
    path: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
    maximum: int,
) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        visible = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != uid
            or before.st_gid != gid
            or stat.S_IMODE(before.st_mode) != mode
            or not 0 < before.st_size <= maximum
            or not stat.S_ISREG(visible.st_mode)
            or (before.st_dev, before.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            raise ValueError(f"unsafe metadata: {path}")
        body = os.read(descriptor, maximum + 1)
        after = os.fstat(descriptor)
        if len(body) > maximum or (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError(f"changed while reading: {path}")
        return body
    finally:
        os.close(descriptor)


seiche_gid = grp.getgrnam("seiche").gr_gid
restore_body = read_exact_file(
    restore_receipt,
    uid=0,
    gid=seiche_gid,
    mode=0o640,
    maximum=64 * 1024,
)
fields: dict[str, str] = {}
for line in restore_body.decode("utf-8").splitlines():
    key, separator, value = line.partition("=")
    if not separator or not key or key in fields:
        raise SystemExit("restore receipt is malformed")
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
    "schema", "checked_at", "snapshot", "source_backup_schema", "deployed_sha",
    "critical_table_counts", "critical_table_count_floor",
    "nbs_full_store_audit_contract", "nbs_full_store_audit_result",
    "nbs_public_revision_store", "palimpsest_china_state_archive_restore",
    "palimpsest_china_state_audit_contract",
    "palimpsest_china_state_tree_sha256",
    "palimpsest_china_active_activation_id",
    "palimpsest_china_pending_candidate_activation_id",
    "palimpsest_china_bundle_count", "palimpsest_china_receipt_count",
    *required_passes,
}
if (
    set(fields) != required_fields
    or fields.get("schema") != "seiche.market-backup-restore-check.v5"
    or fields.get("source_backup_schema") != "seiche.market-backup.v4"
    or fields.get("deployed_sha") != target
    or any(fields.get(key) != value for key, value in required_passes.items())
    or timestamp_re.fullmatch(fields.get("checked_at", "")) is None
    or re.fullmatch(r"20[0-9]{6}T[0-9]{6}Z", fields.get("snapshot", ""))
    is None
    or fields.get("palimpsest_china_state_archive_restore") != "verified"
    or fields.get("palimpsest_china_state_audit_contract")
    != "seiche.palimpsest-china-activation-state.v1"
    or re.fullmatch(
        r"[0-9a-f]{64}", fields.get("palimpsest_china_state_tree_sha256", "")
    )
    is None
    or re.fullmatch(
        r"(?:none|[0-9a-f]{64})",
        fields.get("palimpsest_china_active_activation_id", ""),
    )
    is None
    or re.fullmatch(
        r"(?:none|[0-9a-f]{64})",
        fields.get("palimpsest_china_pending_candidate_activation_id", ""),
    )
    is None
    or re.fullmatch(r"[0-9]+", fields.get("palimpsest_china_bundle_count", ""))
    is None
    or re.fullmatch(r"[0-9]+", fields.get("palimpsest_china_receipt_count", ""))
    is None
):
    raise SystemExit("restore receipt does not prove this release")

snapshot = backup_dir / fields["snapshot"]
inventory = snapshot / "SHA256SUMS"
snapshot_info = snapshot.lstat()
if (
    not stat.S_ISDIR(snapshot_info.st_mode)
    or snapshot_info.st_uid != 0
    or snapshot_info.st_gid != 0
    or stat.S_IMODE(snapshot_info.st_mode) != 0o700
):
    raise SystemExit("backup snapshot metadata is unsafe")
inventory_body = read_exact_file(
    inventory,
    uid=0,
    gid=0,
    mode=0o600,
    maximum=64 * 1024,
)

now = datetime.now(UTC).replace(microsecond=0)
checked_at = datetime.fromisoformat(fields["checked_at"].replace("Z", "+00:00"))
snapshot_created = datetime.strptime(
    fields["snapshot"], "%Y%m%dT%H%M%SZ"
).replace(tzinfo=UTC)
if snapshot_created > checked_at:
    raise SystemExit("restore receipt predates its backup snapshot")
if checked_at > now.replace(microsecond=0):
    raise SystemExit("restore receipt timestamp is in the future")
completed_at = now.isoformat().replace("+00:00", "Z")
payload = {
    "schema": "seiche.release-recovery-receipt.v1",
    "kind": "recovery",
    "commit": target,
    "tree": tree,
    "release_receipt_sha256": release_digest,
    "backup_snapshot": fields["snapshot"],
    "backup_inventory_sha256": hashlib.sha256(inventory_body).hexdigest(),
    "restore_checked_at": fields["checked_at"],
    "restore_receipt_sha256": hashlib.sha256(restore_body).hexdigest(),
    "worker_startup": "ready",
    "data_readiness": "ready",
    "offsite_schedule": offsite_schedule,
    "completed_at": completed_at,
    "conclusion": "success",
}
body = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
destination = receipt_dir / f"{target}.recovery.json"
if destination.exists() or destination.is_symlink():
    raise SystemExit("recovery receipt appeared unexpectedly")
stage = receipt_dir / f".{target}.recovery.{secrets.token_hex(8)}"
descriptor = os.open(
    stage,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
    0o400,
)
try:
    os.write(descriptor, body)
    os.fchmod(descriptor, 0o400)
    os.fchown(descriptor, 0, 0)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
try:
    os.link(stage, destination, follow_symlinks=False)
    directory_fd = os.open(receipt_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    stage.unlink(missing_ok=True)
directory_fd = os.open(receipt_dir, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY

"$SYNC" "$RECEIPT_DIR"
printf 'seiche release recovery: sealed %s after backup, restore, and readiness proof\n' \
    "${TARGET:0:7}"
