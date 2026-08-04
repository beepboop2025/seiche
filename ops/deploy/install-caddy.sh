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
DEST_DIR=$(dirname -- "$DEST")
DEST_BASE=$(basename -- "$DEST")
STAGED=""
RESTORE_STAGED=""

# Invoked indirectly by the EXIT trap below.
# shellcheck disable=SC2329
cleanup() {
    [ -z "$STAGED" ] || rm -f -- "$STAGED"
    [ -z "$RESTORE_STAGED" ] || rm -f -- "$RESTORE_STAGED"
}
trap cleanup EXIT

reload_caddy() {
    "$CADDY" reload --config "$DEST" --adapter caddyfile \
        || "$SYSTEMCTL" reload caddy
}

restore_previous() {
    local backup="$1"
    echo "Caddy: restoring previous Caddyfile from $backup."
    RESTORE_STAGED=$(mktemp "$DEST_DIR/.${DEST_BASE}.restore.XXXXXX") \
        || { echo "FAIL: could not create a restore stage beside $DEST." >&2; return 1; }
    if ! cp -p "$backup" "$RESTORE_STAGED"; then
        echo "FAIL: Caddy restore staging failed — $DEST still contains the rejected config." >&2
        return 1
    fi
    if ! mv -f "$RESTORE_STAGED" "$DEST"; then
        echo "FAIL: atomic Caddy restore rename failed — $DEST may contain the rejected config." >&2
        return 1
    fi
    RESTORE_STAGED=""
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
    echo "Caddy: installed Caddyfile matches the repo; validating and reloading runtime."
    if ! "$CADDY" validate --config "$DEST" --adapter caddyfile; then
        echo "FAIL: matching on-disk Caddyfile did not validate; runtime left unchanged." >&2
        exit 1
    fi
    if reload_caddy; then
        echo "Caddy: matching on-disk config accepted by the runtime."
        exit 0
    fi
    echo "FAIL: matching on-disk Caddyfile could not be applied to the runtime." >&2
    exit 1
fi

# Stage in the destination directory so the final rename is atomic and Caddy
# resolves the candidate in the same path context as the installed file. Seed
# the stage from DEST to retain its owner/mode, then replace only its contents.
STAGED=$(mktemp "$DEST_DIR/.${DEST_BASE}.new.XXXXXX") \
    || { echo "FAIL: could not create an install stage beside $DEST." >&2; exit 1; }
if ! cp -p "$DEST" "$STAGED" || ! cp "$SOURCE" "$STAGED"; then
    echo "FAIL: could not stage the repo Caddyfile; installed config unchanged." >&2
    exit 1
fi
if ! "$CADDY" validate --config "$STAGED" --adapter caddyfile; then
    echo "FAIL: staged repo Caddyfile did not pass caddy validate; installed config unchanged." >&2
    exit 1
fi

BACKUP="${DEST}.bak-$(date +%s)-$$"
if ! cp -p "$DEST" "$BACKUP"; then
    echo "FAIL: could not back up $DEST; refusing to overwrite it." >&2
    exit 1
fi
if ! mv -f "$STAGED" "$DEST"; then
    echo "FAIL: atomic Caddy install rename failed; restoring the backup." >&2
    restore_previous "$BACKUP" || true
    exit 1
fi
STAGED=""
if reload_caddy; then
    echo "Caddy: deployed repo Caddyfile and reloaded (backup: $BACKUP)."
    exit 0
fi

echo "FAIL: new Caddyfile could not be reloaded; rolling it back." >&2
restore_previous "$BACKUP" || true
# A release that needed rollback stays red even when the old edge recovered.
exit 1
