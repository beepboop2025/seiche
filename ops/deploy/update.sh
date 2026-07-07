#!/usr/bin/env bash
# Update a deployed Seiche to the latest main and restart — the one command
# that makes new engine work visible on the box. Run as root.
set -euo pipefail

APP_DIR=/opt/seiche
cd "$APP_DIR"

git fetch origin main
git checkout main
git pull --ff-only origin main

cd "$APP_DIR/backend"
.venv/bin/pip install -q -e ".[dev]"
.venv/bin/python -m pytest tests -q   # same gate as CI: a red suite never deploys

cd "$APP_DIR/frontend"
npm ci --silent
npm run build

chown -R seiche:seiche "$APP_DIR"

# pick up any unit changes shipped with the release
cp "$APP_DIR"/ops/deploy/seiche.service /etc/systemd/system/
cp "$APP_DIR"/ops/deploy/seiche-alert.service /etc/systemd/system/
cp "$APP_DIR"/ops/deploy/seiche-alert.timer /etc/systemd/system/
systemctl daemon-reload
systemctl restart seiche.service

echo "Deployed $(git rev-parse --short HEAD) — $(git log -1 --format=%s)"
