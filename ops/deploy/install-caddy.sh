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
CADDY_ENV_FILE="${SEICHE_CADDY_ENV_FILE:-/etc/seiche/railway-edge.env}"
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

load_railway_edge_environment() {
    local count=0 key line value
    if [ ! -e "$CADDY_ENV_FILE" ] && [ ! -L "$CADDY_ENV_FILE" ]; then
        return 0
    fi
    [ -f "$CADDY_ENV_FILE" ] && [ ! -L "$CADDY_ENV_FILE" ] \
        || { echo "FAIL: Railway edge environment is unsafe." >&2; return 1; }
    python3 -I -S - "$CADDY_ENV_FILE" <<'PY' \
        || { echo "FAIL: Railway edge environment permissions are unsafe." >&2; return 1; }
import os
from pathlib import Path
import stat
import sys

metadata = Path(sys.argv[1]).lstat()
if (
    not stat.S_ISREG(metadata.st_mode)
    or metadata.st_nlink != 1
    or metadata.st_mode & 0o077
    or (os.geteuid() == 0 and metadata.st_uid != 0)
):
    raise SystemExit(1)
PY
    unset SEICHE_API_UPSTREAM SEICHE_RAILWAY_EDGE_TOKEN
    while IFS= read -r line || [ -n "$line" ]; do
        key=${line%%=*}
        value=${line#*=}
        [ "$key" != "$line" ] || return 1
        case "$key" in
            SEICHE_API_UPSTREAM)
                [[ "$value" =~ ^https://[a-z0-9][a-z0-9.-]{1,251}\.up\.railway\.app$ ]] \
                    || return 1
                SEICHE_API_UPSTREAM=$value
                export SEICHE_API_UPSTREAM
                ;;
            SEICHE_RAILWAY_EDGE_TOKEN)
                [ "${#value}" -ge 32 ] && [ "${#value}" -le 512 ] \
                    && [[ "$value" =~ ^[A-Za-z0-9._~=-]+$ ]] || return 1
                SEICHE_RAILWAY_EDGE_TOKEN=$value
                export SEICHE_RAILWAY_EDGE_TOKEN
                ;;
            *) return 1 ;;
        esac
        count=$((count + 1))
    done <"$CADDY_ENV_FILE"
    [ "$count" -eq 2 ] \
        && [ -n "${SEICHE_API_UPSTREAM:-}" ] \
        && [ -n "${SEICHE_RAILWAY_EDGE_TOKEN:-}" ] \
        || { echo "FAIL: Railway edge environment is incomplete." >&2; return 1; }
}

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
load_railway_edge_environment \
    || { echo "FAIL: Railway edge environment is invalid." >&2; exit 1; }
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
