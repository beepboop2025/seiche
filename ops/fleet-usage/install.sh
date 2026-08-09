#!/usr/bin/env bash
# Atomic installer for the fleet digest. Run only after activation hooks are live.
set -euo pipefail

SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEST_DIR=/opt/fleet-usage
DEST="$DEST_DIR/usage_digest.py"
STATE_DIR=/var/lib/fleet-usage
STATE="$STATE_DIR/activation-armed.json"
SYSTEMD_DIR=/etc/systemd/system
PRODUCTS=(seiche undertow palimpsest groundcheck breach noisefloor)

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root." >&2
    exit 1
fi

python3 - "$SOURCE_DIR/usage_digest.py" <<'PY'
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    compile(source.read(), "usage_digest.py", "exec")
PY
install -d -m 0755 "$DEST_DIR" "$STATE_DIR"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=""
dest_existed=false
if [ -f "$DEST" ]; then
    dest_existed=true
    backup="$DEST.before-$stamp"
    cp -p -- "$DEST" "$backup"
fi

staged=$(mktemp "$DEST_DIR/.usage_digest.py.new.XXXXXX")
service_stage=""
timer_stage=""
trap '[ -z "$staged" ] || rm -f -- "$staged"; [ -z "$service_stage" ] || rm -f -- "$service_stage"; [ -z "$timer_stage" ] || rm -f -- "$timer_stage"' EXIT
install -m 0755 "$SOURCE_DIR/usage_digest.py" "$staged"
mv -f -- "$staged" "$DEST"
staged=""

# A bad script must never get a chance to install or arm a persistent timer.
if ! "$DEST" --print-only >/dev/null; then
    if $dest_existed; then
        cp -p -- "$backup" "$DEST"
    else
        rm -f -- "$DEST"
    fi
    echo "Digest smoke test failed; script restored and units untouched." >&2
    exit 1
fi

service="$SYSTEMD_DIR/fleet-usage-digest.service"
timer="$SYSTEMD_DIR/fleet-usage-digest.timer"
service_backup=""
timer_backup=""
service_existed=false
timer_existed=false
if [ -f "$service" ]; then
    service_existed=true
    service_backup="$service.before-$stamp"
    cp -p -- "$service" "$service_backup"
fi
if [ -f "$timer" ]; then
    timer_existed=true
    timer_backup="$timer.before-$stamp"
    cp -p -- "$timer" "$timer_backup"
fi
timer_was_enabled=false
timer_was_active=false
if systemctl is-enabled --quiet fleet-usage-digest.timer; then
    timer_was_enabled=true
fi
if systemctl is-active --quiet fleet-usage-digest.timer; then
    timer_was_active=true
fi

state_backup=""
state_existed=false
if [ -f "$STATE" ]; then
    state_existed=true
    state_backup="$STATE.before-$stamp"
    cp -p -- "$STATE" "$state_backup"
fi

restore_previous() {
    set +e
    if $dest_existed; then cp -p -- "$backup" "$DEST"; else rm -f -- "$DEST"; fi
    if $service_existed; then cp -p -- "$service_backup" "$service"; else rm -f -- "$service"; fi
    if $timer_existed; then cp -p -- "$timer_backup" "$timer"; else rm -f -- "$timer"; fi
    if $state_existed; then cp -p -- "$state_backup" "$STATE"; else rm -f -- "$STATE"; fi
    systemctl daemon-reload
    if $timer_was_enabled; then
        systemctl enable fleet-usage-digest.timer
    else
        systemctl disable fleet-usage-digest.timer
    fi
    if $timer_was_active; then
        systemctl start fleet-usage-digest.timer
    else
        systemctl stop fleet-usage-digest.timer
    fi
    set -e
}

service_stage=$(mktemp "$SYSTEMD_DIR/.fleet-usage-digest.service.new.XXXXXX")
timer_stage=$(mktemp "$SYSTEMD_DIR/.fleet-usage-digest.timer.new.XXXXXX")
install -m 0644 "$SOURCE_DIR/fleet-usage-digest.service" "$service_stage"
install -m 0644 "$SOURCE_DIR/fleet-usage-digest.timer" "$timer_stage"

# Preserve the first arm time on repeat installs. The digest uses this to avoid
# presenting a partial observation window as a trustworthy 24-hour zero.
if ! python3 - "$STATE" "${PRODUCTS[@]}" <<'PY'
import json
import os
import sys
import tempfile
import time

path = sys.argv[1]
products = sys.argv[2:]
try:
    with open(path, encoding="utf-8") as fh:
        state = json.load(fh)
except (OSError, json.JSONDecodeError):
    state = {}
now = time.time()
for product in products:
    if not isinstance(state.get(product), (int, float)):
        state[product] = now
fd, staged = tempfile.mkstemp(prefix=".activation-armed.", dir=os.path.dirname(path))
try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, sort_keys=True)
        fh.write("\n")
    os.chmod(staged, 0o600)
    os.replace(staged, path)
except Exception:
    try:
        os.unlink(staged)
    except OSError:
        pass
    raise
PY
then
    restore_previous
    echo "Could not arm telemetry; previous installation restored." >&2
    exit 1
fi

if ! mv -f -- "$service_stage" "$service"; then
    restore_previous
    echo "Could not install service unit; previous installation restored." >&2
    exit 1
fi
service_stage=""
if ! mv -f -- "$timer_stage" "$timer"; then
    restore_previous
    echo "Could not install timer unit; previous installation restored." >&2
    exit 1
fi
timer_stage=""
if ! systemctl daemon-reload \
        || ! systemctl enable --now fleet-usage-digest.timer; then
    restore_previous
    echo "Could not activate timer; previous installation restored." >&2
    exit 1
fi

echo "Fleet digest installed; previous script: ${backup:-none}."
