#!/usr/bin/env bash
# Install Seiche's edge allow-list without ever leaving a rejected Caddyfile
# active. Production uses the defaults; tests override the paths and binaries
# below and therefore never touch /etc or a real Caddy process.
set -u

APP_DIR="${SEICHE_APP_DIR:-/home/seiche/app}"
SOURCE="${SEICHE_CADDY_SOURCE:-$APP_DIR/ops/Caddyfile}"
DEST="${SEICHE_CADDY_DEST:-/etc/caddy/Caddyfile}"
CADDY="${SEICHE_CADDY_BIN:-caddy}"
SYSTEMCTL="${SEICHE_SYSTEMCTL_BIN:-systemctl}"

reload_caddy() {
    "$CADDY" reload --config "$DEST" --adapter caddyfile \
        || "$SYSTEMCTL" reload caddy
}

restore_previous() {
    local backup="$1"
    echo "Caddy: restoring previous Caddyfile from $backup."
    if ! cp "$backup" "$DEST"; then
        echo "FAIL: Caddy restore copy failed — $DEST may contain the rejected config." >&2
        return 1
    fi
    if reload_caddy; then
        echo "Caddy: previous Caddyfile restored and reloaded."
        return 0
    fi
    echo "FAIL: restored Caddyfile could not be reloaded — caddy needs a human NOW." >&2
    return 1
}

if [ ! -f "$SOURCE" ]; then
    echo "FAIL: Caddy source is missing: $SOURCE" >&2
    exit 1
fi
if ! command -v "$CADDY" >/dev/null 2>&1; then
    echo "FAIL: caddy binary not found: $CADDY" >&2
    exit 1
fi
if ! command -v "$SYSTEMCTL" >/dev/null 2>&1; then
    echo "FAIL: systemctl binary not found: $SYSTEMCTL" >&2
    exit 1
fi
if [ ! -f "$DEST" ]; then
    echo "FAIL: installed Caddyfile is missing: $DEST" >&2
    exit 1
fi
if cmp -s "$SOURCE" "$DEST"; then
    echo "Caddy: installed Caddyfile already matches the repo."
    exit 0
fi
if ! "$CADDY" validate --config "$SOURCE" --adapter caddyfile; then
    echo "FAIL: repo ops/Caddyfile did not pass caddy validate; installed config unchanged." >&2
    exit 1
fi

BACKUP="${DEST}.bak-$(date +%s)-$$"
if ! cp "$DEST" "$BACKUP"; then
    echo "FAIL: could not back up $DEST; refusing to overwrite it." >&2
    exit 1
fi
if ! cp "$SOURCE" "$DEST"; then
    echo "FAIL: could not install the repo Caddyfile; restoring the backup." >&2
    restore_previous "$BACKUP" || true
    exit 1
fi
if reload_caddy; then
    echo "Caddy: deployed repo Caddyfile and reloaded (backup: $BACKUP)."
    exit 0
fi

echo "FAIL: new Caddyfile could not be reloaded; rolling it back." >&2
restore_previous "$BACKUP" || true
# A release that needed rollback stays red even when the old edge recovered.
exit 1
