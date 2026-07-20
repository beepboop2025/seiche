#!/usr/bin/env bash
# Update a deployed Seiche to the latest main and restart — the one command
# that makes new engine work visible on the box. Run as root.
set -euo pipefail

APP_DIR=/opt/seiche
cd "$APP_DIR"

# git operations run as root (root owns the deploy key / remote credentials).
git fetch origin main
git checkout main
git pull --ff-only origin main

# HARDENING: hand the tree to the unprivileged service user BEFORE building, so
# the build/test steps run WITHOUT root. A compromised build/test dependency then
# can only touch seiche-owned files, never the rest of the box. Only the
# systemctl actions below stay root. `runuser` is part of util-linux (always
# present on a systemd host) and needs no sudo config.
#
# NOTE (needs a real on-box deploy to confirm): this reorders the chown to run
# before the build, and wraps pip/pytest/npm in `runuser -u seiche`. .venv and
# node_modules must therefore be writable by seiche (they are after this chown).
# If a future change makes the build need root, revert to running these as root.
chown -R seiche:seiche "$APP_DIR"

cd "$APP_DIR/backend"
runuser -u seiche -- .venv/bin/pip install -q -e ".[dev]"
runuser -u seiche -- .venv/bin/python -m pytest tests -q --memray --pystack-threshold=300   # same gate as CI: a red suite never deploys

cd "$APP_DIR/frontend"
runuser -u seiche -- npm ci --silent
runuser -u seiche -- npm run build

# pick up any unit changes shipped with the release
cp "$APP_DIR"/ops/deploy/seiche.service /etc/systemd/system/
cp "$APP_DIR"/ops/deploy/seiche-alert.service /etc/systemd/system/
cp "$APP_DIR"/ops/deploy/seiche-alert.timer /etc/systemd/system/
systemctl daemon-reload
systemctl restart seiche.service

# HARDENING: deploy the edge (Caddy) config for api.seiche.info — but only
# when it changed, and never at the cost of the engine deploy. Every caddy
# step is if-guarded so a failure warns and continues instead of tripping
# set -e. Sits after the pytest gate above, so a red suite also skips edge
# changes. Runs as root (writes /etc/caddy, talks to the caddy admin API).
if ! command -v caddy >/dev/null 2>&1; then
    echo "Caddy: caddy binary not found — skipping Caddyfile deploy."
elif [ ! -f /etc/caddy/Caddyfile ]; then
    echo "Caddy: /etc/caddy/Caddyfile absent — skipping Caddyfile deploy."
elif cmp -s "$APP_DIR/ops/Caddyfile" /etc/caddy/Caddyfile; then
    echo "Caddy: /etc/caddy/Caddyfile already matches the repo — nothing to do."
elif ! caddy validate --config "$APP_DIR/ops/Caddyfile" --adapter caddyfile; then
    echo "::warning ::Caddy: ops/Caddyfile FAILED 'caddy validate' — SKIPPING Caddyfile deploy (engine deploy unaffected)."
else
    CADDY_BAK="/etc/caddy/Caddyfile.bak-$(date +%s)"
    if ! cp /etc/caddy/Caddyfile "$CADDY_BAK"; then
        echo "::warning ::Caddy: could not back up /etc/caddy/Caddyfile — SKIPPING Caddyfile deploy (never overwrite without a backup)."
    elif ! cp "$APP_DIR/ops/Caddyfile" /etc/caddy/Caddyfile; then
        echo "::warning ::Caddy: could not install new Caddyfile — restoring backup."
        cp "$CADDY_BAK" /etc/caddy/Caddyfile || echo "::warning ::Caddy: restore copy failed — manual intervention required."
    elif caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile || systemctl reload caddy; then
        echo "Caddy: deployed new Caddyfile and reloaded (backup: $CADDY_BAK)."
    else
        echo "::warning ::Caddy: reload failed — restoring previous Caddyfile from $CADDY_BAK."
        cp "$CADDY_BAK" /etc/caddy/Caddyfile || echo "::warning ::Caddy: restore copy failed — /etc/caddy/Caddyfile may still be the rejected config."
        if caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile || systemctl reload caddy; then
            echo "::warning ::Caddy: previous Caddyfile restored and reloaded — investigate the new Caddyfile before retrying."
        else
            echo "::warning ::Caddy: restore reload ALSO failed — caddy may be down; manual intervention required."
        fi
    fi
fi

echo "Deployed $(git rev-parse --short HEAD) — $(git log -1 --format=%s)"
