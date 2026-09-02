#!/usr/bin/env bash
# Transactional installer for the private Hetzner fleet watchdog.
set -euo pipefail
umask 077

SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SOURCE_SCRIPT="$SOURCE_DIR/watchdog.py"
LIVE_SCRIPT=/opt/fleet-watchdog/watchdog.py
LIVE_CONFIG=/etc/fleet-watchdog.json
RELEASE_ROOT=/var/lib/fleet-watchdog/releases
WATCHDOG_SERVICE=fleet-watchdog.service
WATCHDOG_TIMER=fleet-watchdog.timer

MODE=""
SOURCE_SHA=""

usage() {
    echo "usage: $0 (--check|--install) --source-sha <40-hex>" >&2
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --check|--install)
            if [ -n "$MODE" ]; then
                usage
                exit 2
            fi
            MODE=${1#--}
            shift
            ;;
        --source-sha)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            SOURCE_SHA=$2
            shift 2
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

if [ -z "$MODE" ] || ! [[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]]; then
    usage
    exit 2
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "run as root" >&2
    exit 1
fi

REPO_ROOT=$(git -C "$SOURCE_DIR" rev-parse --show-toplevel 2>/dev/null) || {
    echo "installer must run from the canonical Seiche Git worktree" >&2
    exit 1
}
if [ "$SOURCE_SCRIPT" != "$REPO_ROOT/ops/fleet-watchdog/watchdog.py" ]; then
    echo "installer source path is outside the canonical Seiche layout" >&2
    exit 1
fi
case "$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null)" in
    https://github.com/beepboop2025/seiche.git|git@github.com:beepboop2025/seiche.git)
        ;;
    *)
        echo "installer source repository is not canonical Seiche" >&2
        exit 1
        ;;
esac
if [ "$(git -C "$REPO_ROOT" rev-parse --verify HEAD)" != "$SOURCE_SHA" ] \
        || [ -n "$(git -C "$REPO_ROOT" status --porcelain=v1 --untracked-files=all)" ]; then
    echo "Seiche worktree is not clean at the requested source commit" >&2
    exit 1
fi
if ! GIT_NO_REPLACE_OBJECTS=1 git -C "$REPO_ROOT" cat-file -e \
        "$SOURCE_SHA:ops/fleet-watchdog/watchdog.py" \
        || ! GIT_NO_REPLACE_OBJECTS=1 git -C "$REPO_ROOT" cat-file -e \
        "$SOURCE_SHA:ops/fleet-watchdog/install.sh"; then
    echo "requested commit does not contain the watchdog release files" >&2
    exit 1
fi
exec 9>/run/lock/fleet-watchdog-install.lock
if ! flock -n 9; then
    echo "another fleet-watchdog installer holds the transaction lock" >&2
    exit 1
fi

for required in "$SOURCE_SCRIPT" "$LIVE_SCRIPT" "$LIVE_CONFIG"; do
    if [ ! -f "$required" ] || [ -L "$required" ]; then
        echo "required watchdog input is missing or unsafe" >&2
        exit 1
    fi
done

assert_live_metadata() {
    python3 - "$LIVE_SCRIPT" "$LIVE_CONFIG" <<'PY'
import os
import stat
import sys

for path, mode in ((sys.argv[1], 0o750), (sys.argv[2], 0o600)):
    info = os.lstat(path)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) != mode
        or info.st_nlink != 1
    ):
        raise SystemExit("live watchdog input ownership, mode or type is unsafe")
PY
}

assert_live_metadata

WORK_DIR=$(mktemp -d /run/fleet-watchdog-install.XXXXXX)
chmod 0700 "$WORK_DIR"
CANDIDATE_CONFIG="$WORK_DIR/fleet-watchdog.json"
SOURCE_CANDIDATE="$WORK_DIR/watchdog.py"
GIT_NO_REPLACE_OBJECTS=1 git -C "$REPO_ROOT" show \
    "$SOURCE_SHA:ops/fleet-watchdog/watchdog.py" >"$SOURCE_CANDIDATE"
chown root:root "$SOURCE_CANDIDATE"
chmod 0750 "$SOURCE_CANDIDATE"
if ! cmp -s -- "$SOURCE_SCRIPT" "$SOURCE_CANDIDATE"; then
    echo "worktree watchdog differs from the requested commit" >&2
    exit 1
fi
python3 - "$SOURCE_CANDIDATE" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
compile(source, sys.argv[1], "exec")
PY
SCRIPT_STAGE=""
CONFIG_STAGE=""
TIMER_WAS_ACTIVE=""
TIMER_WAS_ENABLED=""
TIMER_QUIESCED=false
MUTATED=false
COMMITTED=false
RELEASE_DIR=""
RELEASE_STAGE=""
PRESERVE_RELEASE=false

cleanup_files() {
    [ -z "$SCRIPT_STAGE" ] || rm -f -- "$SCRIPT_STAGE"
    [ -z "$CONFIG_STAGE" ] || rm -f -- "$CONFIG_STAGE"
    rm -f -- "$CANDIDATE_CONFIG"
    rm -f -- "$SOURCE_CANDIDATE"
    if [ -n "$RELEASE_STAGE" ] && [ "$PRESERVE_RELEASE" != true ]; then
        rm -f -- \
            "$RELEASE_STAGE/previous-watchdog.py" \
            "$RELEASE_STAGE/previous-config.json" \
            "$RELEASE_STAGE/release-receipt.json" \
            "$RELEASE_STAGE/release-receipt.json.tmp"
        rmdir -- "$RELEASE_STAGE" 2>/dev/null || true
    fi
    rmdir -- "$WORK_DIR" 2>/dev/null || true
}

restore_timer() {
    local failed=0
    [ "$TIMER_QUIESCED" = true ] || return 0
    if [ "$TIMER_WAS_ENABLED" = enabled ]; then
        systemctl enable "$WATCHDOG_TIMER" >/dev/null || failed=1
    else
        systemctl disable "$WATCHDOG_TIMER" >/dev/null || failed=1
    fi
    if [ "$TIMER_WAS_ACTIVE" = active ]; then
        systemctl start "$WATCHDOG_TIMER" || failed=1
    else
        systemctl stop "$WATCHDOG_TIMER" || failed=1
    fi
    if [ "$failed" -ne 0 ]; then
        return 1
    fi
    TIMER_QUIESCED=false
}

atomic_restore() {
    local source=$1
    local destination=$2
    local mode=$3
    local directory
    local staged
    directory=$(dirname -- "$destination") || return 1
    staged=$(mktemp "$directory/.fleet-watchdog-rollback.XXXXXX") || return 1
    if ! install -o root -g root -m "$mode" "$source" "$staged"; then
        rm -f -- "$staged"
        return 1
    fi
    if ! mv -fT -- "$staged" "$destination"; then
        rm -f -- "$staged"
        return 1
    fi
}

rollback() {
    local failed=0
    [ "$MUTATED" = true ] || return 0
    set +e
    atomic_restore "$RELEASE_STAGE/previous-watchdog.py" \
        "$LIVE_SCRIPT" 0750 || failed=1
    atomic_restore "$RELEASE_STAGE/previous-config.json" \
        "$LIVE_CONFIG" 0600 || failed=1
    if [ "$failed" -eq 0 ]; then
        systemctl reset-failed "$WATCHDOG_SERVICE" || failed=1
        systemctl start "$WATCHDOG_SERVICE" || failed=1
    fi
    set -e
    if [ "$failed" -ne 0 ]; then
        PRESERVE_RELEASE=true
        echo "watchdog rollback incomplete; timer remains quiesced" >&2
        echo "recovery bytes retained at $RELEASE_STAGE" >&2
        return 1
    fi
    MUTATED=false
}

finish() {
    local status=$?
    local rollback_ok=true
    trap - EXIT
    if [ "$status" -ne 0 ] && [ "$COMMITTED" != true ]; then
        if ! rollback; then
            rollback_ok=false
            status=1
        fi
    fi
    if [ "$rollback_ok" = true ]; then
        if ! restore_timer; then
            echo "watchdog timer state restoration failed" >&2
            PRESERVE_RELEASE=true
            status=1
        fi
    else
        echo "watchdog timer deliberately left quiesced after rollback failure" >&2
    fi
    cleanup_files
    exit "$status"
}
trap finish EXIT

# Build the candidate from the exact current private object. The helper emits
# only hashes and an action word; it never prints values from the configuration.
CONFIG_BEFORE_SHA=$(sha256sum "$LIVE_CONFIG" | awk '{print $1}')
CONFIG_ACTION=$(python3 - \
    "$LIVE_CONFIG" "$CANDIDATE_CONFIG" "$SOURCE_CANDIDATE" \
    "$CONFIG_BEFORE_SHA" <<'PY'
import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import stat
import sys

sys.dont_write_bytecode = True

live_path, candidate_path, watchdog_path, expected_sha = sys.argv[1:]
info = os.lstat(live_path)
if (
    not stat.S_ISREG(info.st_mode)
    or info.st_uid != 0
    or stat.S_IMODE(info.st_mode) != 0o600
    or info.st_nlink != 1
):
    raise SystemExit("live watchdog config ownership, mode or type is unsafe")
with open(live_path, "rb") as source:
    original_bytes = source.read(1024 * 1024 + 1)
if len(original_bytes) > 1024 * 1024:
    raise SystemExit("live watchdog config exceeds installer size limit")
if hashlib.sha256(original_bytes).hexdigest() != expected_sha:
    raise SystemExit("live watchdog config changed during candidate derivation")
try:
    original = json.loads(original_bytes.decode("utf-8"))
except (UnicodeError, ValueError) as exc:
    raise SystemExit("live watchdog config is not valid UTF-8 JSON") from exc
if not isinstance(original, dict):
    raise SystemExit("live watchdog config is not an object")

desired = {
    "name": "liquilens-runner-restart-debt",
    "status_file": "/var/lib/liquilens-runner-maintenance/status.json",
    "debt_file": "/var/lib/liquilens-runner-maintenance/restart-debt.json",
    "service_unit": "liquilens-runner-restart-debt.service",
    "timer_unit": "liquilens-runner-restart-debt.timer",
    "monitored_unit": (
        "actions.runner.beepboop2025-LiquiLens.hetzner-cpx32.service"
    ),
    "max_age_seconds": 1200,
}
has_existing = "maintenance_status" in original
existing = original.get("maintenance_status")
if has_existing and existing != desired:
    raise SystemExit("existing maintenance_status differs; refusing replacement")

candidate = copy.deepcopy(original)
candidate["maintenance_status"] = desired
without_probe = copy.deepcopy(candidate)
without_probe.pop("maintenance_status")
original_without_probe = copy.deepcopy(original)
original_without_probe.pop("maintenance_status", None)
if without_probe != original_without_probe:
    raise SystemExit("candidate changed pre-existing watchdog configuration")

if has_existing:
    candidate_bytes = original_bytes
    action = "unchanged"
else:
    candidate_bytes = (
        json.dumps(candidate, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    action = "added"
fd = os.open(candidate_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with os.fdopen(fd, "wb") as destination:
        destination.write(candidate_bytes)
        destination.flush()
        os.fsync(destination.fileno())
except Exception:
    try:
        os.unlink(candidate_path)
    except OSError:
        pass
    raise

spec = importlib.util.spec_from_file_location("fleet_watchdog_candidate", watchdog_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
diagnostics = io.StringIO()
with contextlib.redirect_stdout(diagnostics):
    config = module.load_config(candidate_path)
if diagnostics.getvalue():
    raise SystemExit("candidate watchdog configuration emitted diagnostics")
if config.maintenance is None or config.config_problems:
    raise SystemExit("candidate watchdog rejected maintenance_status")
status, read_problem = module._read_safe_json(
    config.maintenance.status_file, "maintenance status",
)
if read_problem:
    raise SystemExit("maintenance producer preflight failed")
_, status_problems = module._validate_maintenance_status(
    status, config.maintenance, __import__("time").time(),
)
if status_problems:
    raise SystemExit("maintenance producer status failed validation")
debt, debt_read_problem = module._read_safe_json(
    config.maintenance.debt_file, "maintenance debt marker", missing_ok=True,
)
if debt_read_problem:
    raise SystemExit("maintenance debt marker preflight failed")
if debt is not None:
    _, debt_problems = module._validate_debt_marker(
        debt, config.maintenance, __import__("time").time(),
    )
    if debt_problems:
        raise SystemExit("maintenance debt marker failed validation")
if status["result"] == "debt" and debt is None:
    raise SystemExit("maintenance debt status is missing its marker")
if status["result"] == "clean" and debt is not None:
    raise SystemExit("clean maintenance status conflicts with debt marker")
print(action)
PY
)

case "$CONFIG_ACTION" in added|unchanged) ;; *)
    echo "candidate watchdog returned an invalid config action" >&2
    exit 1;;
esac

if [ "$(sha256sum "$LIVE_CONFIG" | awk '{print $1}')" != "$CONFIG_BEFORE_SHA" ]; then
    echo "live watchdog config changed during candidate derivation" >&2
    exit 1
fi
CONFIG_CANDIDATE_SHA=$(sha256sum "$CANDIDATE_CONFIG" | awk '{print $1}')
SCRIPT_BEFORE_SHA=$(sha256sum "$LIVE_SCRIPT" | awk '{print $1}')
SCRIPT_CANDIDATE_SHA=$(sha256sum "$SOURCE_CANDIDATE" | awk '{print $1}')

if [ "$MODE" = check ]; then
    echo "watchdog preflight passed"
    echo "source_commit=$SOURCE_SHA"
    echo "script_candidate_sha256=$SCRIPT_CANDIDATE_SHA"
    echo "config_action=$CONFIG_ACTION"
    echo "config_before_sha256=$CONFIG_BEFORE_SHA"
    echo "config_candidate_sha256=$CONFIG_CANDIDATE_SHA"
    exit 0
fi

RELEASE_DIR="$RELEASE_ROOT/$SOURCE_SHA"
if [ -e "$RELEASE_DIR" ]; then
    echo "release directory already exists; refusing to overwrite history" >&2
    exit 1
fi

TIMER_WAS_ENABLED=$(systemctl is-enabled "$WATCHDOG_TIMER" 2>/dev/null || true)
TIMER_WAS_ACTIVE=$(systemctl is-active "$WATCHDOG_TIMER" 2>/dev/null || true)
case "$TIMER_WAS_ENABLED" in enabled|disabled) ;; *)
    echo "unsupported watchdog timer enabled state" >&2; exit 1;;
esac
case "$TIMER_WAS_ACTIVE" in active|inactive) ;; *)
    echo "unsupported watchdog timer active state" >&2; exit 1;;
esac

if [ "$TIMER_WAS_ACTIVE" = active ]; then
    systemctl stop "$WATCHDOG_TIMER"
fi
TIMER_QUIESCED=true
for _ in $(seq 1 190); do
    service_state=$(systemctl is-active "$WATCHDOG_SERVICE" 2>/dev/null || true)
    [ "$service_state" = inactive ] && break
    [ "$service_state" = failed ] && {
        echo "watchdog service is failed; refusing unattended replacement" >&2
        exit 1
    }
    sleep 1
done
if [ "${service_state:-unknown}" != inactive ]; then
    echo "watchdog service did not quiesce" >&2
    exit 1
fi

# Compare-and-swap both live inputs after quiescing the reader. Another
# operator changing either object aborts the transaction before any mutation.
if [ "$(sha256sum "$LIVE_CONFIG" | awk '{print $1}')" != "$CONFIG_BEFORE_SHA" ]; then
    echo "live watchdog config changed during preflight" >&2
    exit 1
fi
if [ "$(sha256sum "$LIVE_SCRIPT" | awk '{print $1}')" != "$SCRIPT_BEFORE_SHA" ]; then
    echo "live watchdog script changed during preflight" >&2
    exit 1
fi
assert_live_metadata

install -d -o root -g root -m 0755 "$RELEASE_ROOT"
RELEASE_STAGE=$(mktemp -d "$RELEASE_ROOT/.release-$SOURCE_SHA.XXXXXX")
chown root:root "$RELEASE_STAGE"
chmod 0700 "$RELEASE_STAGE"
install -o root -g root -m 0750 "$LIVE_SCRIPT" \
    "$RELEASE_STAGE/previous-watchdog.py"
install -o root -g root -m 0600 "$LIVE_CONFIG" \
    "$RELEASE_STAGE/previous-config.json"

SCRIPT_STAGE=$(mktemp /opt/fleet-watchdog/.watchdog.py.new.XXXXXX)
CONFIG_STAGE=$(mktemp /etc/.fleet-watchdog.json.new.XXXXXX)
install -o root -g root -m 0750 "$SOURCE_CANDIDATE" "$SCRIPT_STAGE"
install -o root -g root -m 0600 "$CANDIDATE_CONFIG" "$CONFIG_STAGE"
MUTATED=true
mv -fT -- "$SCRIPT_STAGE" "$LIVE_SCRIPT"
SCRIPT_STAGE=""
mv -fT -- "$CONFIG_STAGE" "$LIVE_CONFIG"
CONFIG_STAGE=""

if [ "$(sha256sum "$LIVE_SCRIPT" | awk '{print $1}')" != "$SCRIPT_CANDIDATE_SHA" ] \
        || [ "$(sha256sum "$LIVE_CONFIG" | awk '{print $1}')" != "$CONFIG_CANDIDATE_SHA" ]; then
    echo "installed watchdog bytes do not match candidates" >&2
    exit 1
fi

systemctl start "$WATCHDOG_SERVICE"
service_result=$(systemctl show "$WATCHDOG_SERVICE" \
    --property=Result --value --no-pager)
service_exit=$(systemctl show "$WATCHDOG_SERVICE" \
    --property=ExecMainStatus --value --no-pager)
if [ "$service_result" != success ] || [ "$service_exit" != 0 ]; then
    echo "installed watchdog one-shot verification failed" >&2
    exit 1
fi

installed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
python3 - "$RELEASE_STAGE/release-receipt.json" <<PY
import json
import os
import sys

receipt = {
    "schema": "fleet-watchdog-release.v2",
    "installed_at": "$installed_at",
    "source_repository": "beepboop2025/seiche",
    "source_commit": "$SOURCE_SHA",
    "transaction_state": "prepared_timer_restore_pending",
    "script": {
        "path": "$LIVE_SCRIPT",
        "previous_sha256": "$SCRIPT_BEFORE_SHA",
        "installed_sha256": "$SCRIPT_CANDIDATE_SHA",
        "owner": "root:root",
        "mode": "0750",
    },
    "config": {
        "path": "$LIVE_CONFIG",
        "action": "$CONFIG_ACTION",
        "previous_sha256": "$CONFIG_BEFORE_SHA",
        "installed_sha256": "$CONFIG_CANDIDATE_SHA",
        "preservation": "deep_equal_addition_only",
        "owner": "root:root",
        "mode": "0600",
    },
    "timer_before": {
        "enabled": "$TIMER_WAS_ENABLED",
        "active": "$TIMER_WAS_ACTIVE",
    },
    "verification": "candidate_status_validated_and_oneshot_succeeded",
}
path = sys.argv[1]
temporary = path + ".tmp"
fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as destination:
    json.dump(receipt, destination, indent=2, sort_keys=True)
    destination.write("\n")
    destination.flush()
    os.fsync(destination.fileno())
os.replace(temporary, path)
PY

mv -T -- "$RELEASE_STAGE" "$RELEASE_DIR"
# Until the timer is restored, keep the final directory as the rollback source
# and as cleanup-owned evidence. A failed restore must not leave a success
# receipt behind or race an enabled reader against partially sealed history.
RELEASE_STAGE="$RELEASE_DIR"
if ! restore_timer; then
    echo "watchdog timer state restoration failed" >&2
    exit 1
fi
# Live bytes and timer state are now verified. From this point a failure must
# not race an active reader by rolling back; instead the absence of the commit
# marker leaves the otherwise recoverable release visibly uncommitted.
COMMITTED=true
MUTATED=false
PRESERVE_RELEASE=true
RELEASE_STAGE=""
if [ "$(sha256sum "$LIVE_SCRIPT" | awk '{print $1}')" != "$SCRIPT_CANDIDATE_SHA" ] \
        || [ "$(sha256sum "$LIVE_CONFIG" | awk '{print $1}')" != "$CONFIG_CANDIDATE_SHA" ]; then
    echo "live watchdog bytes changed before release commit" >&2
    exit 1
fi
assert_live_metadata
committed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
python3 - \
    "$RELEASE_DIR/release-receipt.json" \
    "$RELEASE_DIR/release-commit.json" <<PY
import hashlib
import json
import os
import sys

receipt_path, marker_path = sys.argv[1:]
with open(receipt_path, "rb") as source:
    receipt = source.read(1024 * 1024 + 1)
if len(receipt) > 1024 * 1024:
    raise SystemExit("fleet watchdog release receipt exceeds size limit")
marker = {
    "schema": "fleet-watchdog-release-commit.v1",
    "committed_at": "$committed_at",
    "source_commit": "$SOURCE_SHA",
    "receipt_sha256": hashlib.sha256(receipt).hexdigest(),
    "verification": "live_bytes_verified_and_timer_state_restored",
}
fd = os.open(marker_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as destination:
    json.dump(marker, destination, indent=2, sort_keys=True)
    destination.write("\n")
    destination.flush()
    os.fsync(destination.fileno())
PY
PRESERVE_RELEASE=false
echo "fleet watchdog installed from $SOURCE_SHA; committed receipt: $RELEASE_DIR"
