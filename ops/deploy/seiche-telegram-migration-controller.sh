#!/usr/bin/env bash
# Forced-command controller for the Phase-7 Telegram authority transfer.

set -euo pipefail
set -f
umask 0077

readonly CONTROL_ROOT=/var/lib/seiche-telegram-transfer
readonly STATE_ROOT=/var/lib/seiche-bot
readonly LOCK_PATH=/run/lock/seiche-telegram-migration.lock
readonly POLLER_SETTLE_SECONDS=65
readonly -a UNITS=(
  seiche-bot.service
  seiche-bot-alert.service
  seiche-bot-alert.timer
  seiche-bot-letter.service
  seiche-bot-letter.timer
  seiche-bot-tandem.service
  seiche-bot-tandem.timer
)

FREEZE_ROLLBACK_ROOT=
FREEZE_COMMITTED=0

fail() {
  printf 'seiche Telegram migration controller: %s\n' "$*" >&2
  exit 1
}

require_root() {
  [ "$(id -u)" -eq 0 ] || fail "root identity is required"
  [ "$(id -g)" -eq 0 ] || fail "root group is required"
}

valid_request_id() {
  [[ "$1" =~ ^[0-9a-f]{64}$ ]]
}

valid_sha() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]]
}

request_root() {
  valid_request_id "$1" || fail "request id is invalid"
  printf '%s/%s\n' "$CONTROL_ROOT" "$1"
}

canonical_digest() {
  sha256sum "$1" | awk '{print $1}'
}

assert_state_tree() {
  [ -d "$STATE_ROOT" ] || fail "Telegram state root is absent"
  [ ! -L "$STATE_ROOT" ] || fail "Telegram state root is a symlink"
  [ -f "$STATE_ROOT/offset.json" ] || fail "Telegram offset is absent"
  [ ! -L "$STATE_ROOT/offset.json" ] || fail "Telegram offset is unsafe"
  [ -f "$STATE_ROOT/subscribers.json" ] || fail "Telegram subscribers are absent"
  [ ! -L "$STATE_ROOT/subscribers.json" ] || fail "Telegram subscribers are unsafe"
  if find "$STATE_ROOT" -mindepth 1 \( -type l -o -type d -o ! -type f \) \
      -print -quit | grep -q .; then
    fail "Telegram state contains an unsupported member"
  fi
  if find "$STATE_ROOT" -mindepth 1 -maxdepth 1 -type f \
      ! -name '*.json' ! -name '*.jsonl' -print -quit | grep -q .; then
    fail "Telegram state contains an unclosed filename"
  fi
  if find "$STATE_ROOT" -mindepth 1 -maxdepth 1 -type f -links +1 \
      -print -quit | grep -q .; then
    fail "Telegram state contains a hard-linked member"
  fi
  if ! STATE_ROOT="$STATE_ROOT" python3 -I -S - <<'PY'
import os
from pathlib import Path
import re
import stat

root = Path(os.environ["STATE_ROOT"])
entries = list(root.iterdir())
if not 2 <= len(entries) <= 256:
    raise SystemExit("Telegram state file count is invalid")
if not {"offset.json", "subscribers.json"}.issubset(
    {entry.name for entry in entries}
):
    raise SystemExit("Telegram critical state files are absent")
total = 0
for entry in entries:
    metadata = entry.lstat()
    if (
        re.fullmatch(r"[a-z][a-z0-9_-]{0,63}\.(?:json|jsonl)", entry.name)
        is None
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > 32 * 1024**2
    ):
        raise SystemExit("Telegram state member is unsafe")
    total += metadata.st_size
if not 0 < total <= 512 * 1024**2:
    raise SystemExit("Telegram state byte total is invalid")
PY
  then
    fail "Telegram state exceeds its closed migration contract"
  fi
}

assert_frozen() {
  for unit in "${UNITS[@]}"; do
    [ "$(systemctl is-enabled "$unit" 2>/dev/null || true)" = masked ] ||
      fail "$unit is not masked"
    [ "$(systemctl is-active "$unit" 2>/dev/null || true)" != active ] ||
      fail "$unit is still active"
  done
  if pgrep -af '/opt/seiche-bot/seiche_bot.py' >/dev/null; then
    fail "a Seiche Telegram process remains"
  fi
}

assert_source_authoritative() {
  [ "$(systemctl is-active seiche-bot.service 2>/dev/null || true)" = active ] ||
    fail "Seiche Telegram source service is not active"
  for unit in "${UNITS[@]}"; do
    [ "$(systemctl is-enabled "$unit" 2>/dev/null || true)" != masked ] ||
      fail "$unit is already masked"
  done
  pgrep -af '/opt/seiche-bot/seiche_bot.py' >/dev/null ||
    fail "the Seiche Telegram source process is absent"
}

source_lab_channel() {
  local main_pid
  main_pid=$(systemctl show seiche-bot.service --property=MainPID --value)
  [[ "$main_pid" =~ ^[1-9][0-9]*$ ]] ||
    fail "Seiche Telegram main process identity is unavailable"
  PROC_ENV="/proc/$main_pid/environ" python3 -I -S - <<'PY'
import os
from pathlib import Path
import re

entries = {}
for item in Path(os.environ["PROC_ENV"]).read_bytes().split(b"\0"):
    if b"=" in item:
        name, value = item.split(b"=", 1)
        entries[name] = value
try:
    channel = entries[b"LAB_CHANNEL_ID"].decode("ascii")
except (KeyError, UnicodeDecodeError):
    raise SystemExit("Telegram Lab channel identity is unavailable") from None
if re.fullmatch(r"-100[0-9]{6,16}", channel) is None:
    raise SystemExit("Telegram Lab channel identity is invalid")
print(channel)
PY
}

capture_unit_state() {
  local output=$1
  : >"$output"
  for unit in "${UNITS[@]}"; do
    printf '%s\t%s\t%s\n' \
      "$unit" \
      "$(systemctl is-enabled "$unit" 2>/dev/null || true)" \
      "$(systemctl is-active "$unit" 2>/dev/null || true)" \
      >>"$output"
  done
  chmod 0400 "$output"
}

restore_prestate() {
  local root=$1
  [ -f "$root/units-before.tsv" ] || return 1
  systemctl unmask "${UNITS[@]}" || return 1
  while IFS=$'\t' read -r unit enabled active; do
    case "$enabled" in
      enabled) systemctl enable "$unit" || return 1 ;;
      disabled|static|indirect|generated|transient|alias|not-found|"") ;;
      *) return 1 ;;
    esac
    if [ "$active" = active ]; then
      systemctl start "$unit" || return 1
    elif [ "$active" != inactive ] && [ "$active" != failed ] &&
        [ "$active" != unknown ]; then
      return 1
    fi
  done <"$root/units-before.tsv"
  systemctl start seiche-bot.service || return 1
}

restore_failed_freeze() {
  local status=$?
  trap - EXIT
  if [ "$FREEZE_COMMITTED" -ne 1 ] && [ -n "$FREEZE_ROLLBACK_ROOT" ]; then
    set +e
    if ! restore_prestate "$FREEZE_ROLLBACK_ROOT"; then
      printf '%s\n' \
        'seiche Telegram migration controller: automatic prestate restore failed' \
        >&2
    fi
  fi
  exit "$status"
}

freeze() {
  local commit=$1
  local request_id=$2
  valid_sha "$commit" || fail "commit is invalid"
  valid_request_id "$request_id" || fail "request id is invalid"
  local root
  root=$(request_root "$request_id")
  [ ! -e "$root" ] || fail "request root already exists"
  assert_source_authoritative
  local lab_channel_id
  lab_channel_id=$(source_lab_channel)
  install -d -m 0700 "$CONTROL_ROOT" "$root"
  assert_state_tree
  capture_unit_state "$root/units-before.tsv"
  FREEZE_ROLLBACK_ROOT=$root
  FREEZE_COMMITTED=0
  trap restore_failed_freeze EXIT
  local frozen_at
  frozen_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  systemctl disable --now \
    seiche-bot-alert.timer seiche-bot-letter.timer seiche-bot-tandem.timer
  systemctl stop \
    seiche-bot-alert.service seiche-bot-letter.service \
    seiche-bot-tandem.service seiche-bot.service
  systemctl mask "${UNITS[@]}"
  assert_frozen
  sleep "$POLLER_SETTLE_SECONDS"
  assert_frozen
  local snapshot_id
  snapshot_id=$(date -u +%Y%m%dT%H%M%SZ)
  assert_state_tree
  tar --create --gzip --file "$root/seiche-bot.tgz" \
    --directory /var/lib seiche-bot
  chmod 0400 "$root/seiche-bot.tgz"
  local settled_at expires_at
  settled_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  expires_at=$(date -u -d '+4 hours' +%Y-%m-%dT%H:%M:%SZ)
  COMMIT="$commit" REQUEST_ID="$request_id" SNAPSHOT_ID="$snapshot_id" \
    FROZEN_AT="$frozen_at" SETTLED_AT="$settled_at" EXPIRES_AT="$expires_at" \
    POLLER_SETTLE_SECONDS="$POLLER_SETTLE_SECONDS" \
    LAB_CHANNEL_ID="$lab_channel_id" \
    python3 -I -S - <<'PY' >"$root/fence.json"
import json
import os

units = [
    "seiche-bot.service",
    "seiche-bot-alert.service",
    "seiche-bot-alert.timer",
    "seiche-bot-letter.service",
    "seiche-bot-letter.timer",
    "seiche-bot-tandem.service",
    "seiche-bot-tandem.timer",
]
value = {
    "source": "hetzner",
    "state": "frozen",
    "state_root": "/var/lib/seiche-bot",
    "units": units,
    "poller_stopped": True,
    "timers_stopped": True,
    "timers_disabled": True,
    "active_processes": [],
    "lab_channel_id": os.environ["LAB_CHANNEL_ID"],
    "frozen_at": os.environ["FROZEN_AT"],
    "settled_at": os.environ["SETTLED_AT"],
    "expires_at": os.environ["EXPIRES_AT"],
    "poller_settle_seconds": int(os.environ["POLLER_SETTLE_SECONDS"]),
}
print(json.dumps(value, sort_keys=True, separators=(",", ":")))
PY
  chmod 0400 "$root/fence.json"
  local archive_sha fence_sha
  archive_sha=$(canonical_digest "$root/seiche-bot.tgz")
  fence_sha=$(canonical_digest "$root/fence.json")
  COMMIT="$commit" REQUEST_ID="$request_id" SNAPSHOT_ID="$snapshot_id" \
    ARCHIVE_SHA="$archive_sha" FENCE_SHA="$fence_sha" \
    python3 -I -S - <<'PY' >"$root/metadata.json"
import json
import os

value = {
    "schema": "seiche.hetzner-telegram-freeze-metadata.v1",
    "commit": os.environ["COMMIT"],
    "request_id": os.environ["REQUEST_ID"],
    "snapshot_id": os.environ["SNAPSHOT_ID"],
    "archive_sha256": os.environ["ARCHIVE_SHA"],
    "fence_sha256": os.environ["FENCE_SHA"],
}
print(json.dumps(value, sort_keys=True, separators=(",", ":")))
PY
  chmod 0400 "$root/metadata.json"
  FREEZE_COMMITTED=1
  trap - EXIT
  FREEZE_ROLLBACK_ROOT=
  cat "$root/metadata.json"
}

status() {
  local request_id=$1
  local root
  root=$(request_root "$request_id")
  [ -f "$root/metadata.json" ] || fail "request metadata is absent"
  assert_frozen
  REQUEST_ID="$request_id" ROOT="$root" python3 -I -S - <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import stat

root = Path(os.environ["ROOT"])

def read_control(name):
    path = root / name
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != 0
        or before.st_gid != 0
        or stat.S_IMODE(before.st_mode) != 0o400
    ):
        raise SystemExit(f"Telegram {name} ownership is unsafe")
    body = path.read_bytes()
    after = path.lstat()
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise SystemExit(f"Telegram {name} changed while read")
    return body

metadata_body = read_control("metadata.json")
metadata = json.loads(metadata_body)
canonical_metadata = (
    json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n"
).encode()
if (
    metadata_body != canonical_metadata
    or set(metadata)
    != {
        "schema", "commit", "request_id", "snapshot_id",
        "archive_sha256", "fence_sha256",
    }
    or metadata.get("schema") != "seiche.hetzner-telegram-freeze-metadata.v1"
    or metadata.get("request_id") != os.environ["REQUEST_ID"]
    or re.fullmatch(r"[0-9a-f]{40}", str(metadata.get("commit"))) is None
    or re.fullmatch(r"20[0-9]{6}T[0-9]{6}Z", str(metadata.get("snapshot_id")))
    is None
    or re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("archive_sha256"))) is None
    or re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("fence_sha256"))) is None
):
    raise SystemExit("Telegram freeze metadata is not canonical")
fence_body = read_control("fence.json")
fence = json.loads(fence_body)
canonical_fence = (
    json.dumps(fence, sort_keys=True, separators=(",", ":")) + "\n"
).encode()
if (
    fence_body != canonical_fence
    or hashlib.sha256(fence_body).hexdigest() != metadata["fence_sha256"]
    or fence.get("source") != "hetzner"
    or fence.get("state") != "frozen"
    or fence.get("state_root") != "/var/lib/seiche-bot"
):
    raise SystemExit("Telegram persisted fence is invalid")
activation_path = root / "activation.json"
activation_sha = None
if activation_path.exists():
    activation_body = read_control("activation.json")
    activation = json.loads(activation_body)
    canonical_activation = (
        json.dumps(activation, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    if (
        activation_body != canonical_activation
        or activation.get("request_id") != os.environ["REQUEST_ID"]
    ):
        raise SystemExit("Telegram activation acknowledgement is invalid")
    activation_sha = hashlib.sha256(activation_body).hexdigest()
value = {
    "schema": "seiche.hetzner-telegram-status.v1",
    "request_id": os.environ["REQUEST_ID"],
    "commit": metadata["commit"],
    "snapshot_id": metadata["snapshot_id"],
    "archive_sha256": metadata["archive_sha256"],
    "fence_sha256": metadata["fence_sha256"],
    "metadata_sha256": hashlib.sha256(metadata_body).hexdigest(),
    "activation_sha256": activation_sha,
    "source_frozen": True,
    "authority": "railway" if activation_sha is not None else "hetzner-frozen",
}
print(json.dumps(value, sort_keys=True, separators=(",", ":")))
PY
}

fetch_artifact() {
  local request_id=$1
  local name=$2
  local root
  root=$(request_root "$request_id")
  case "$name" in
    archive) cat "$root/seiche-bot.tgz" ;;
    fence) cat "$root/fence.json" ;;
    metadata) cat "$root/metadata.json" ;;
    *) fail "artifact name is invalid" ;;
  esac
}

rollback() {
  local request_id=$1
  local confirmation=$2
  [ "$confirmation" = RESTORE_HETZNER_TELEGRAM_BEFORE_GRANT ] ||
    fail "rollback confirmation is invalid"
  local root
  root=$(request_root "$request_id")
  [ -f "$root/metadata.json" ] || fail "request metadata is absent"
  [ ! -e "$root/activation.json" ] || fail "activation is already acknowledged"
  restore_prestate "$root" || fail "saved Telegram prestate could not be restored"
  printf '%s\n' '{"authority":"hetzner","rolled_back":true}'
}

acknowledge() {
  local request_id=$1
  local expected_sha=$2
  valid_request_id "$request_id" || fail "request id is invalid"
  [[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || fail "activation digest is invalid"
  local root temporary
  root=$(request_root "$request_id")
  [ -f "$root/metadata.json" ] || fail "request metadata is absent"
  assert_frozen
  temporary="$root/.activation.$$.tmp"
  trap 'rm -f -- "$temporary"' EXIT
  dd of="$temporary" bs=65536 count=8 status=none
  [ -s "$temporary" ] || fail "activation receipt is empty"
  [ "$(canonical_digest "$temporary")" = "$expected_sha" ] ||
    fail "activation receipt digest differs"
  REQUEST_ID="$request_id" python3 -I -S - "$temporary" <<'PY'
import json
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
body = path.read_bytes()
value = json.loads(body)
canonical = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
if (
    body != canonical
    or value.get("schema") != "seiche.railway-telegram-activation-receipt.v1"
    or value.get("request_id") != os.environ["REQUEST_ID"]
    or value.get("authority", {}).get("source") != "railway"
    or value.get("authority", {}).get("sole_get_updates_consumer") is not True
):
    raise SystemExit("activation receipt is invalid")
PY
  chmod 0400 "$temporary"
  if [ -e "$root/activation.json" ]; then
    [ -f "$root/activation.json" ] || fail "saved activation is not a file"
    [ ! -L "$root/activation.json" ] || fail "saved activation is a symlink"
    cmp --silent "$temporary" "$root/activation.json" ||
      fail "saved activation differs from the retry"
    rm -f -- "$temporary"
  else
    mv "$temporary" "$root/activation.json"
  fi
  sync -f "$root/activation.json"
  trap - EXIT
  printf '%s\n' '{"authority":"railway","acknowledged":true}'
}

main() {
  require_root
  install -d -m 0700 "$(dirname "$LOCK_PATH")" "$CONTROL_ROOT"
  exec 9>"$LOCK_PATH"
  flock -n 9 || fail "another Telegram migration operation is active"
  local command_text=${SSH_ORIGINAL_COMMAND:-}
  if [ -n "$command_text" ]; then
    read -r action first second extra <<<"$command_text"
    [ -z "${extra:-}" ] || fail "forced command has too many arguments"
  else
    local action=${1:-}
    local first=${2:-}
    local second=${3:-}
    [ "$#" -le 3 ] || fail "command has too many arguments"
  fi
  case "$action" in
    freeze) freeze "$first" "$second" ;;
    status) [ -z "${second:-}" ] || fail "status arguments are invalid"; status "$first" ;;
    fetch) fetch_artifact "$first" "$second" ;;
    rollback) rollback "$first" "$second" ;;
    acknowledge) acknowledge "$first" "$second" ;;
    *) fail "command is not allowed" ;;
  esac
}

main "$@"
