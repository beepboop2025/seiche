#!/usr/bin/env bash
# Freeze Hetzner writers and commit the final Phase-5 Railway handoff snapshot.
set -euo pipefail
set -f
umask 0077
export LC_ALL=C

readonly APP_DIR="${SEICHE_CUTOVER_APP_DIR:-/home/seiche/app}"
readonly DEPLOYED_STATE="${SEICHE_CUTOVER_DEPLOYED_STATE:-/var/lib/seiche-deploy/deployed-sha}"
readonly CONTROL_RECEIPTS="${SEICHE_CUTOVER_CONTROL_RECEIPTS:-/var/lib/seiche-control/receipts}"
readonly SHADOW_RECEIPT="${SEICHE_CUTOVER_SHADOW_RECEIPT:-/var/lib/seiche-control/railway-shadow/latest.json}"
readonly BACKUP_DIR="${SEICHE_CUTOVER_BACKUP_DIR:-/var/backups/seiche-market}"
readonly RESTORE_STATUS="${SEICHE_CUTOVER_RESTORE_STATUS:-/var/lib/seiche-recovery-proof/backup-restore-check.status}"
readonly STATE_DIR="${SEICHE_CUTOVER_STATE_DIR:-/var/lib/seiche-railway-cutover}"
readonly LOCK_PATH="${SEICHE_CUTOVER_LOCK_PATH:-/run/lock/seiche-railway-cutover.lock}"
readonly SYSTEMCTL="${SEICHE_CUTOVER_SYSTEMCTL:-/usr/bin/systemctl}"
readonly PYTHON="${SEICHE_CUTOVER_PYTHON:-/usr/bin/python3}"
readonly FLOCK="${SEICHE_CUTOVER_FLOCK:-/usr/bin/flock}"
readonly GIT="${SEICHE_CUTOVER_GIT:-/usr/bin/git}"
readonly DATE="${SEICHE_CUTOVER_DATE:-/usr/bin/date}"
readonly TEST_MODE="${SEICHE_CUTOVER_TEST_MODE:-0}"
readonly INTENT="$STATE_DIR/intent.json"
readonly PRESTATE="$STATE_DIR/prestate.tsv"
readonly FENCE="$STATE_DIR/AUTHORITY-FENCE.json"
readonly ROLLBACK_RECEIPT="$STATE_DIR/rollback.json"
readonly ACTIVATION_ACK="$STATE_DIR/activation-ack.json"

readonly -a FENCED_UNITS=(
  seiche-release-poll.timer
  seiche-release-poll.service
  seiche-release-recovery-seal.service
  seiche-data-readiness.timer
  seiche-data-readiness.service
  seiche-market-validation.timer
  seiche-market-validation.service
  seiche-market-backup.timer
  seiche-market-restore-check.timer
  seiche-market-offsite-backup.timer
  seiche-market-offsite-backup.service
  seiche-snapshot-promote.service
  seiche-snapshot-import.service
  seiche-market-backfill.service
  seiche-market-worker.service
  seiche-source-worker.service
  seiche-api.service
  seiche-pull.service
  seiche-alert.timer
  seiche-alert.service
  seiche.service
  seiche-update.timer
  seiche-update.service
)

fail() {
  printf 'seiche Railway cutover fence: %s\n' "$*" >&2
  exit 1
}

canonical_path() {
  "$PYTHON" -I -B - "$1" <<'PY'
import os
import sys
print(os.path.realpath(sys.argv[1]))
PY
}

validate_configuration() {
  local command_path path
  case "$TEST_MODE" in
    0|1) ;;
    *) fail "test mode must be exactly 0 or 1" ;;
  esac
  if [ "$TEST_MODE" = 0 ]; then
    [ "${EUID:-$($PYTHON -I -S -c 'import os; print(os.geteuid())')}" -eq 0 ] \
      || fail "must run as root"
    [ "$APP_DIR" = /home/seiche/app ] \
      && [ "$DEPLOYED_STATE" = /var/lib/seiche-deploy/deployed-sha ] \
      && [ "$CONTROL_RECEIPTS" = /var/lib/seiche-control/receipts ] \
      && [ "$BACKUP_DIR" = /var/backups/seiche-market ] \
      && [ "$STATE_DIR" = /var/lib/seiche-railway-cutover ] \
      && [ "$LOCK_PATH" = /run/lock/seiche-railway-cutover.lock ] \
      || fail "production paths are fixed"
    [ "$SYSTEMCTL" = /usr/bin/systemctl ] \
      && [ "$PYTHON" = /usr/bin/python3 ] \
      && [ "$FLOCK" = /usr/bin/flock ] \
      && [ "$GIT" = /usr/bin/git ] \
      && [ "$DATE" = /usr/bin/date ] \
      || fail "production commands are fixed"
  fi
  for command_path in "$SYSTEMCTL" "$PYTHON" "$FLOCK" "$GIT" "$DATE"; do
    [ -x "$command_path" ] || fail "required command is unavailable"
  done
  for path in "$APP_DIR" "$CONTROL_RECEIPTS" "$BACKUP_DIR"; do
    [ -d "$path" ] && [ ! -L "$path" ] \
      || fail "required cutover directory is unsafe"
  done
  [ "$(canonical_path "$APP_DIR")" = "$APP_DIR" ] \
    || fail "application directory is not canonical"
  [ "$(canonical_path "$BACKUP_DIR")" = "$BACKUP_DIR" ] \
    || fail "backup directory is not canonical"
  if [ -e "$STATE_DIR" ] || [ -L "$STATE_DIR" ]; then
    [ -d "$STATE_DIR" ] && [ ! -L "$STATE_DIR" ] \
      || fail "cutover state directory is unsafe"
  else
    install -d -m 0700 "$STATE_DIR"
  fi
  chmod 0700 "$STATE_DIR"
}

unit_active() {
  "$SYSTEMCTL" is-active --quiet "$1" 2>/dev/null
}

unit_enabled() {
  "$SYSTEMCTL" is-enabled --quiet "$1" 2>/dev/null
}

unit_masked() {
  [ "$("$SYSTEMCTL" is-enabled "$1" 2>/dev/null || true)" = masked-runtime ] \
    || [ "$("$SYSTEMCTL" is-enabled "$1" 2>/dev/null || true)" = masked ]
}

write_intent_and_prestate() {
  local active enabled stage unit
  [ ! -e "$INTENT" ] && [ ! -L "$INTENT" ] \
    || fail "an unresolved cutover intent already exists"
  [ ! -e "$PRESTATE" ] && [ ! -L "$PRESTATE" ] \
    || fail "an unresolved unit prestate already exists"
  stage=$(mktemp "$STATE_DIR/.prestate.XXXXXX")
  for unit in "${FENCED_UNITS[@]}"; do
    active=0
    enabled=0
    unit_active "$unit" && active=1
    unit_enabled "$unit" && enabled=1
    printf '%s\t%s\t%s\n' "$unit" "$active" "$enabled" >>"$stage"
  done
  [ "$(wc -l <"$stage" | tr -d '[:space:]')" -eq "${#FENCED_UNITS[@]}" ] \
    || fail "unit prestate is incomplete"
  chmod 0600 "$stage"
  mv "$stage" "$PRESTATE"
  "$PYTHON" -I -B - "$INTENT" "$EXPECTED_SHA" "$STARTED_AT" <<'PY'
import json
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
value = {
    "schema": "seiche.railway-cutover-intent.v1",
    "commit": sys.argv[2],
    "started_at": sys.argv[3],
    "state": "freezing_hetzner",
}
body = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
try:
    written = 0
    while written < len(body):
        count = os.write(descriptor, body[written:])
        if count <= 0:
            raise OSError("intent write made no progress")
        written += count
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}

freeze_units() {
  local unit
  for unit in "${FENCED_UNITS[@]}"; do
    if ! "$SYSTEMCTL" stop "$unit"; then
      unit_active "$unit" \
        && fail "unit could not be stopped during the fence: $unit"
    fi
    if unit_enabled "$unit"; then
      "$SYSTEMCTL" disable "$unit"
    fi
    "$SYSTEMCTL" mask --runtime "$unit"
    unit_active "$unit" && fail "unit remained active after fence: $unit"
    unit_masked "$unit" || fail "unit is not runtime-masked: $unit"
  done
}

commit_final_snapshot() {
  local after before new_snapshot snapshot_name
  before=$(mktemp "$STATE_DIR/.snapshots-before.XXXXXX")
  after=$(mktemp "$STATE_DIR/.snapshots-after.XXXXXX")
  find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20??????T??????Z' \
    -exec basename {} \; | sort >"$before"
  "$SYSTEMCTL" start seiche-market-backup.service
  "$SYSTEMCTL" is-failed --quiet seiche-market-backup.service \
    && fail "final backup service failed"
  find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20??????T??????Z' \
    -exec basename {} \; | sort >"$after"
  new_snapshot=$(comm -13 "$before" "$after")
  [ -n "$new_snapshot" ] && [[ "$new_snapshot" != *$'\n'* ]] \
    || fail "final backup did not create exactly one committed snapshot"
  snapshot_name=$new_snapshot
  FINAL_SNAPSHOT="$BACKUP_DIR/$snapshot_name"
  [ -d "$FINAL_SNAPSHOT" ] && [ ! -L "$FINAL_SNAPSHOT" ] \
    || fail "final snapshot is unsafe"
  [ "$(tr -d '[:space:]' <"$FINAL_SNAPSHOT/deployed-sha.txt")" = "$EXPECTED_SHA" ] \
    || fail "final snapshot belongs to another release"
  # Every scheduler and writer is masked, so the just-created timestamped
  # directory is necessarily the newest snapshot selected by the oneshot.
  "$SYSTEMCTL" start seiche-market-restore-check.service
  "$SYSTEMCTL" is-failed --quiet seiche-market-restore-check.service \
    && fail "final isolated restore service failed"
  [ -f "$RESTORE_STATUS" ] && [ ! -L "$RESTORE_STATUS" ] \
    || fail "final restore receipt is unavailable"
  grep -Fx "snapshot=$snapshot_name" "$RESTORE_STATUS" >/dev/null \
    || fail "restore receipt does not bind the final snapshot"
  grep -Fx "deployed_sha=$EXPECTED_SHA" "$RESTORE_STATUS" >/dev/null \
    || fail "restore receipt does not bind the final release"
  grep -Fx 'schema=seiche.market-backup-restore-check.v5' "$RESTORE_STATUS" >/dev/null \
    || fail "restore receipt schema is invalid"
  grep -Fx 'source_backup_schema=seiche.market-backup.v4' \
    "$RESTORE_STATUS" >/dev/null \
    || fail "restore receipt did not bind the current backup schema"
  grep -Fx 'palimpsest_china_state_archive_restore=verified' \
    "$RESTORE_STATUS" >/dev/null \
    || fail "restore receipt did not prove the Palimpsest China archive"
  grep -Fx \
    'palimpsest_china_state_audit_contract=seiche.palimpsest-china-activation-state.v1' \
    "$RESTORE_STATUS" >/dev/null \
    || fail "restore receipt did not bind the Palimpsest China audit contract"
  grep -Fx 'database_restore=pass' "$RESTORE_STATUS" >/dev/null \
    || fail "restore receipt did not prove the database"
  grep -Fx 'state_archive_restore=pass' "$RESTORE_STATUS" >/dev/null \
    || fail "restore receipt did not prove the state archive"
  grep -Fx 'api_data_archive_restore=pass' "$RESTORE_STATUS" >/dev/null \
    || fail "restore receipt did not prove the API data archive"
}

write_fence() {
  local recovery_receipt release_receipt tree
  release_receipt="$CONTROL_RECEIPTS/$EXPECTED_SHA.release.json"
  recovery_receipt="$CONTROL_RECEIPTS/$EXPECTED_SHA.recovery.json"
  for path in "$release_receipt" "$recovery_receipt" "$SHADOW_RECEIPT"; do
    [ -f "$path" ] && [ ! -L "$path" ] \
      || fail "required authority receipt is unavailable"
  done
  tree=$("$GIT" -C "$APP_DIR" rev-parse "$EXPECTED_SHA^{tree}")
  [[ "$tree" =~ ^[0-9a-f]{40}$ ]] || fail "release tree is invalid"
  "$PYTHON" -I -B - \
    "$FENCE" "$FINAL_SNAPSHOT" "$RESTORE_STATUS" \
    "$release_receipt" "$recovery_receipt" "$SHADOW_RECEIPT" \
    "$EXPECTED_SHA" "$tree" "$FROZEN_AT" "$EXPIRES_AT" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

(
    destination_raw,
    snapshot_raw,
    restore_raw,
    release_raw,
    recovery_raw,
    shadow_raw,
    commit,
    tree,
    frozen_at,
    expires_at,
) = sys.argv[1:]
destination = Path(destination_raw)
snapshot = Path(snapshot_raw)
members = (
    "seiche.dump",
    "var-lib-seiche.tgz",
    "palimpsest-china.tgz",
    "palimpsest-china-state.json",
    "api-data.tgz",
    "table-counts.txt",
    "deployed-sha.txt",
    "manifest.env",
)


def read_regular(path: Path, maximum: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= maximum
        ):
            raise ValueError(f"unsafe receipt or snapshot member: {path}")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(body) > maximum or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError(f"file changed during fence creation: {path}")
        return body
    finally:
        os.close(descriptor)


def sha(path: Path, maximum: int = 4 * 1024 * 1024) -> str:
    return hashlib.sha256(read_regular(path, maximum)).hexdigest()


def hash_regular(path: Path, maximum: int) -> tuple[str, int]:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= maximum
        ):
            raise ValueError(f"unsafe snapshot member: {path}")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError(f"snapshot member changed during hashing: {path}")
        return digest.hexdigest(), before.st_size
    finally:
        os.close(descriptor)


inventory = read_regular(snapshot / "SHA256SUMS", 4096)
entries = {item.name for item in snapshot.iterdir()}
if entries != {*members, "SHA256SUMS"}:
    raise SystemExit("final snapshot file set is not closed")
lines = inventory.decode("ascii").splitlines()
if len(lines) != len(members):
    raise SystemExit("final snapshot inventory length is invalid")
digests: dict[str, str] = {}
content = hashlib.sha256()
for expected, line in zip(members, lines, strict=True):
    match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9.-]+)", line)
    if match is None or match.group(2) != expected:
        raise SystemExit("final snapshot inventory is invalid")
    path = snapshot / expected
    observed, size = hash_regular(path, 30 * 1024**3)
    if observed != match.group(1):
        raise SystemExit("final snapshot member digest changed")
    digests[expected] = observed
    content.update(expected.encode("ascii") + b"\0")
    content.update(observed.encode("ascii") + b"\0")
    content.update(str(size).encode("ascii") + b"\n")

restore_digest = sha(Path(restore_raw), 64 * 1024)
unit_names = (
    "seiche-api.service",
    "seiche-market-worker.service",
    "seiche-source-worker.service",
    "seiche-market-backfill.service",
    "seiche-snapshot-promote.service",
    "seiche-snapshot-import.service",
    "seiche-release-poll.service",
    "seiche-release-poll.timer",
    "seiche-release-recovery-seal.service",
    "seiche-data-readiness.service",
    "seiche-data-readiness.timer",
    "seiche-market-validation.service",
    "seiche-market-validation.timer",
    "seiche-market-backup.timer",
    "seiche-market-restore-check.timer",
    "seiche-market-offsite-backup.service",
    "seiche-market-offsite-backup.timer",
    "seiche-pull.service",
    "seiche-alert.service",
    "seiche-alert.timer",
    "seiche.service",
    "seiche-update.service",
    "seiche-update.timer",
)
value = {
    "schema": "seiche.railway-authority-fence.v1",
    "repository": "beepboop2025/seiche",
    "commit": commit,
    "tree": tree,
    "authority": {
        "source": "hetzner",
        "state": "frozen",
        "writers_frozen": True,
        "api_stopped": True,
        "frozen_at": frozen_at,
        "expires_at": expires_at,
    },
    "snapshot": {
        "id": snapshot.name,
        "source_revision": commit,
        "inventory_sha256": hashlib.sha256(inventory).hexdigest(),
        "content_set_sha256": content.hexdigest(),
        "restore_receipt_sha256": restore_digest,
    },
    "receipts": {
        "release_sha256": sha(Path(release_raw), 256 * 1024),
        "recovery_sha256": sha(Path(recovery_raw), 256 * 1024),
        "latest_shadow_sha256": sha(Path(shadow_raw), 256 * 1024),
    },
    "units": {
        name: {"active": False, "enabled": False, "runtime_masked": True}
        for name in unit_names
    },
    "can_activate_railway": True,
    "can_resume_hetzner_before_activation": True,
}
body = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
descriptor = os.open(
    destination,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
    0o400,
)
try:
    written = 0
    while written < len(body):
        count = os.write(descriptor, body[written:])
        if count <= 0:
            raise OSError("fence write made no progress")
        written += count
    os.fsync(descriptor)
finally:
    os.close(descriptor)
print(hashlib.sha256(body).hexdigest())
PY
}

prepare() {
  local head
  [ ! -e "$FENCE" ] && [ ! -L "$FENCE" ] \
    || fail "an authority fence already exists"
  [ ! -e "$ACTIVATION_ACK" ] && [ ! -L "$ACTIVATION_ACK" ] \
    || fail "Railway activation is already acknowledged"
  head=$("$GIT" -C "$APP_DIR" rev-parse HEAD)
  [ "$head" = "$EXPECTED_SHA" ] \
    && [ "$(tr -d '[:space:]' <"$DEPLOYED_STATE")" = "$EXPECTED_SHA" ] \
    || fail "application and deployed marker do not match the reviewed SHA"
  write_intent_and_prestate
  freeze_units
  FROZEN_AT=$($DATE -u +%Y-%m-%dT%H:%M:%SZ)
  EXPIRES_AT=$($DATE -u -d '+4 hours' +%Y-%m-%dT%H:%M:%SZ)
  if [ "$TEST_MODE" = 0 ]; then
    [ "$FROZEN_AT" != "$EXPIRES_AT" ] || fail "fence clock is invalid"
  fi
  commit_final_snapshot
  write_fence
  printf 'seiche Railway cutover fence: Hetzner frozen at %s; final snapshot %s\n' \
    "$EXPECTED_SHA" "$(basename "$FINAL_SNAPSHOT")"
}

rollback() {
  local active enabled unit
  [ "${SEICHE_CUTOVER_ROLLBACK_CONFIRM:-}" = \
      RAILWAY_CANDIDATE_STOPPED_NO_WRITERS ] \
    || fail "rollback confirmation is absent"
  [ ! -e "$ACTIVATION_ACK" ] && [ ! -L "$ACTIVATION_ACK" ] \
    || fail "stale-state rollback is forbidden after Railway activation"
  [ -f "$PRESTATE" ] && [ ! -L "$PRESTATE" ] \
    || fail "unit prestate is unavailable"
  "$PYTHON" -I -B - "$PRESTATE" "${FENCED_UNITS[@]}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
expected = set(sys.argv[2:])
rows = path.read_text(encoding="utf-8").splitlines()
parsed = [row.split("\t") for row in rows]
if (
    len(parsed) != len(expected)
    or any(len(row) != 3 or row[1] not in {"0", "1"} or row[2] not in {"0", "1"} for row in parsed)
    or {row[0] for row in parsed} != expected
):
    raise SystemExit("unit prestate is not a closed inventory")
PY
  while IFS=$'\t' read -r unit active enabled; do
    [[ " ${FENCED_UNITS[*]} " == *" $unit "* ]] \
      || fail "unit prestate contains an unknown unit"
    [[ "$active" =~ ^[01]$ ]] && [[ "$enabled" =~ ^[01]$ ]] \
      || fail "unit prestate contains an invalid state"
    "$SYSTEMCTL" unmask --runtime "$unit"
    if [ "$enabled" = 1 ]; then
      "$SYSTEMCTL" enable "$unit"
    fi
  done <"$PRESTATE"
  while IFS=$'\t' read -r unit active enabled; do
    if [ "$active" = 1 ]; then
      "$SYSTEMCTL" start "$unit"
    fi
  done <"$PRESTATE"
  "$PYTHON" -I -B - "$ROLLBACK_RECEIPT" "$EXPECTED_SHA" <<'PY'
import json
import os
from pathlib import Path
import sys
from datetime import UTC, datetime

path = Path(sys.argv[1])
value = {
    "schema": "seiche.railway-cutover-rollback.v1",
    "commit": sys.argv[2],
    "authority": "hetzner",
    "railway_writers_started": False,
    "rolled_back_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
}
body = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o400)
try:
    written = 0
    while written < len(body):
        count = os.write(descriptor, body[written:])
        if count <= 0:
            raise OSError("rollback receipt write made no progress")
        written += count
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
  printf 'seiche Railway cutover fence: Hetzner pre-activation state restored\n'
}

finalize() {
  local activation_path="$1" expected_digest="$2"
  [ -f "$FENCE" ] && [ ! -L "$FENCE" ] \
    || fail "authority fence is unavailable"
  [ ! -e "$ACTIVATION_ACK" ] && [ ! -L "$ACTIVATION_ACK" ] \
    || fail "activation acknowledgement already exists"
  "$PYTHON" -I -B - \
    "$activation_path" "$expected_digest" "$ACTIVATION_ACK" "$EXPECTED_SHA" \
    "$FENCE" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

source = Path(sys.argv[1])
expected, destination_raw, commit, fence_raw = sys.argv[2:]


def read_regular(path: Path, maximum: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= maximum
        ):
            raise SystemExit(f"unsafe cutover receipt: {path}")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(body) > maximum or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise SystemExit(f"cutover receipt changed while reading: {path}")
        return body
    finally:
        os.close(descriptor)


body = read_regular(source, 256 * 1024)
if (
    re.fullmatch(r"[0-9a-f]{64}", expected) is None
    or hashlib.sha256(body).hexdigest() != expected
):
    raise SystemExit("activation receipt digest is invalid")
value = json.loads(body)
if (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode() != body:
    raise SystemExit("activation receipt is not canonical")
required = {
    "schema",
    "commit",
    "request_id",
    "candidate_receipt_sha256",
    "grant_sha256",
    "fence_sha256",
    "railway",
    "authority",
    "workers",
    "public",
    "activated_at",
    "workers_started_at",
    "research_only",
    "can_publish",
    "can_execute",
}
fence_digest = hashlib.sha256(read_regular(Path(fence_raw), 128 * 1024)).hexdigest()
if (
    set(value) != required
    or value.get("schema") != "seiche.railway-activation-receipt.v1"
    or value.get("commit") != commit
    or value.get("fence_sha256") != fence_digest
    or re.fullmatch(r"[0-9a-f]{64}", str(value.get("request_id"))) is None
    or re.fullmatch(
        r"[0-9a-f]{64}", str(value.get("candidate_receipt_sha256"))
    )
    is None
    or re.fullmatch(r"[0-9a-f]{64}", str(value.get("grant_sha256"))) is None
    or value.get("authority") != {
        "mode": "production",
        "source": "railway",
        "hetzner_writers_frozen": True,
        "railway_writers_started": True,
        "public_traffic_enabled": True,
    }
    or set(value.get("workers", {})) != {"market", "source"}
    or any(
        not isinstance(worker, dict)
        or set(worker) != {"command", "process_started"}
        or not isinstance(worker["command"], list)
        or not worker["command"]
        or any(not isinstance(argument, str) or not argument for argument in worker["command"])
        or worker["process_started"] is not True
        for worker in value.get("workers", {}).values()
    )
    or value.get("public", {}).get("base_url") != "https://api.seiche.info"
    or re.fullmatch(
        r"[0-9a-f]{64}", str(value.get("public", {}).get("probe_sha256"))
    )
    is None
    or not isinstance(value.get("railway"), dict)
    or value.get("research_only") is not True
    or value.get("can_publish") is not False
    or value.get("can_execute") is not False
):
    raise SystemExit("activation receipt authority is invalid")
ack = {
    "schema": "seiche.railway-activation-ack.v1",
    "commit": commit,
    "activation_receipt_sha256": expected,
    "authority": "railway",
    "hetzner_resume_requires_reverse_restore": True,
}
ack_body = (json.dumps(ack, sort_keys=True, separators=(",", ":")) + "\n").encode()
destination = Path(destination_raw)
descriptor = os.open(
    destination,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
    0o400,
)
try:
    written = 0
    while written < len(ack_body):
        count = os.write(descriptor, ack_body[written:])
        if count <= 0:
            raise OSError("activation acknowledgement write made no progress")
        written += count
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
  printf 'seiche Railway cutover fence: Railway is the sole acknowledged writer\n'
}

validate_configuration
[ "$#" -ge 2 ] || fail "usage: $0 prepare|rollback|finalize EXPECTED_SHA [activation receipt digest]"
readonly OPERATION="$1"
readonly EXPECTED_SHA="$2"
[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "expected SHA is invalid"
STARTED_AT=$($DATE -u +%Y-%m-%dT%H:%M:%SZ)
readonly STARTED_AT
exec 9>"$LOCK_PATH"
"$FLOCK" --exclusive --nonblock 9 || fail "another cutover controller holds the lock"

case "$OPERATION" in
  prepare)
    [ "$#" -eq 2 ] || fail "prepare accepts only the expected SHA"
    prepare
    ;;
  rollback)
    [ "$#" -eq 2 ] || fail "rollback accepts only the expected SHA"
    rollback
    ;;
  finalize)
    [ "$#" -eq 4 ] || fail "finalize requires activation receipt and digest"
    finalize "$3" "$4"
    ;;
  *) fail "operation must be prepare, rollback, or finalize" ;;
esac
