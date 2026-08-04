#!/usr/bin/env bash
# Trigger the production key's forced command twice. Pass one deploy installs a
# newly pulled wrapper over the old on-box mirror; pass two executes that new
# wrapper on the same SHA, converging Caddy before external route verification.
# The requested remote command is always the literal log-friendly word
# "deploy"; authorized_keys still chooses the command that actually runs.
set -euo pipefail

HOST="${SEICHE_DEPLOY_HOST:?SEICHE_DEPLOY_HOST is required}"
KEY_FILE="${SEICHE_DEPLOY_KEY_FILE:?SEICHE_DEPLOY_KEY_FILE is required}"
KNOWN_HOSTS="${SEICHE_KNOWN_HOSTS_FILE:?SEICHE_KNOWN_HOSTS_FILE is required}"
SSH="${SEICHE_SSH_BIN:-ssh}"

trigger() {
    "$SSH" -i "$KEY_FILE" \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$KNOWN_HOSTS" \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=20 \
        -o TCPKeepAlive=yes \
        "root@$HOST" deploy
}

echo "forced deploy pass 1/2: application release + wrapper self-sync"
trigger
echo "forced deploy pass 2/2: same-SHA edge convergence"
trigger
