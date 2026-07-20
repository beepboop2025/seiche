#!/bin/bash
# Forced-command target for the GitHub Actions deploy key. The key in
# authorized_keys can run THIS script and nothing else (no pty, no forwarding).
# update.sh pulls main, pip-installs and runs the test suite with rollback;
# only a green tree gets restarted.
# Mirrored in the repo at ops/deploy/seiche-deploy-wrapper.sh — edit both.
set -euo pipefail
echo "== seiche auto-deploy $(date -u +%FT%TZ) =="
BEFORE=$(runuser -u seiche -- git -C /home/seiche/app rev-parse HEAD)
runuser -u seiche -- bash /home/seiche/update.sh
AFTER=$(runuser -u seiche -- git -C /home/seiche/app rev-parse HEAD)
if [ "$BEFORE" = "$AFTER" ]; then
  echo "already at ${AFTER:0:7} — nothing to deploy"
  exit 0
fi
systemctl restart seiche-api
sleep 3
systemctl is-active --quiet seiche-api || { echo "FAIL: seiche-api not active after restart"; exit 1; }
# The API rebuilds its board on start and can take minutes before it answers;
# poll instead of a single probe so a healthy warm-up is never reported red.
WINDOW=900
DEADLINE=$((SECONDS + WINDOW))
until curl -sf -m 10 http://127.0.0.1:8787/api/public >/dev/null; do
  if [ "$SECONDS" -ge "$DEADLINE" ]; then
    echo "FAIL: api not answering after $((WINDOW / 60))min warm-up window"
    exit 1
  fi
  systemctl is-active --quiet seiche-api || { echo "FAIL: seiche-api died during warm-up"; exit 1; }
  sleep 10
done
echo "deployed ${AFTER:0:7} — service active, api healthy"
