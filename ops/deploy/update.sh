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
runuser -u seiche -- .venv/bin/python -m pytest tests -q   # same gate as CI: a red suite never deploys

cd "$APP_DIR/frontend"
runuser -u seiche -- npm ci --silent
runuser -u seiche -- npm run build

# pick up any unit changes shipped with the release
cp "$APP_DIR"/ops/deploy/seiche.service /etc/systemd/system/
cp "$APP_DIR"/ops/deploy/seiche-alert.service /etc/systemd/system/
cp "$APP_DIR"/ops/deploy/seiche-alert.timer /etc/systemd/system/
systemctl daemon-reload
systemctl restart seiche.service

echo "Deployed $(git rev-parse --short HEAD) — $(git log -1 --format=%s)"
