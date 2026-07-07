#!/usr/bin/env bash
# First-time Seiche install on a Debian/Ubuntu VPS (Hetzner-friendly).
# Run as root:  bash ops/deploy/install.sh [git-url]
# Idempotent: safe to re-run.
set -euo pipefail

REPO_URL="${1:-https://github.com/beepboop2025/seiche.git}"
APP_DIR=/opt/seiche

apt-get update -q
apt-get install -y -q python3 python3-venv git curl nodejs npm

id -u seiche &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin seiche

if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
git fetch origin main && git checkout main && git pull --ff-only origin main

# backend
cd "$APP_DIR/backend"
python3 -m venv .venv
.venv/bin/pip install -q -e ".[dev]"
.venv/bin/python -m pytest tests -q   # the same gate CI uses: no green, no serve

# frontend (built once; uvicorn serves dist/)
cd "$APP_DIR/frontend"
npm ci --silent
npm run build

chown -R seiche:seiche "$APP_DIR"

# systemd units
cp "$APP_DIR"/ops/deploy/seiche.service /etc/systemd/system/
cp "$APP_DIR"/ops/deploy/seiche-alert.service /etc/systemd/system/
cp "$APP_DIR"/ops/deploy/seiche-alert.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now seiche.service seiche-alert.timer

echo
echo "Seiche is up on 127.0.0.1:8787 (put a reverse proxy with TLS in front:"
echo "  caddy:  reverse_proxy 127.0.0.1:8787  — or nginx proxy_pass)."
echo "First load fetches several years of history and is slow; then cached."
echo "Update later with: bash $APP_DIR/ops/deploy/update.sh"
