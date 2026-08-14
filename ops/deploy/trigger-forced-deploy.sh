#!/usr/bin/env bash
# Trigger the production key's forced command twice. Pass one deploy installs a
# newly pulled wrapper over the old on-box mirror; pass two executes that new
# wrapper on the same SHA, converging Caddy before external route verification.
# The requested command carries only the exact reviewed commit identity;
# authorized_keys still chooses the wrapper that actually runs. The wrapper
# rejects every other command shape and deploys this commit even if main moves.
set -euo pipefail

HOST="${SEICHE_DEPLOY_HOST:?SEICHE_DEPLOY_HOST is required}"
KEY_FILE="${SEICHE_DEPLOY_KEY_FILE:?SEICHE_DEPLOY_KEY_FILE is required}"
KNOWN_HOSTS="${SEICHE_KNOWN_HOSTS_FILE:?SEICHE_KNOWN_HOSTS_FILE is required}"
SSH="${SEICHE_SSH_BIN:-ssh}"
TARGET_SHA="${SEICHE_EXPECTED_TARGET_SHA:?SEICHE_EXPECTED_TARGET_SHA is required}"
if [[ ! "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
    echo "deployment target is not a canonical commit SHA" >&2
    exit 2
fi

trigger() {
    "$SSH" -i "$KEY_FILE" \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$KNOWN_HOSTS" \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=20 \
        -o TCPKeepAlive=yes \
        "root@$HOST" "deploy $TARGET_SHA"
}

echo "forced deploy pass 1/2: application release + wrapper self-sync"
trigger
echo "forced deploy pass 2/2: same-SHA edge convergence"
set +e
trigger
second_pass_status=$?
set -e
case "$second_pass_status" in
    0) ;;
    75)
        echo "DEFER: pass 2/2 shared-host admission was busy after successful pass 1; continuing to external route verification" >&2
        ;;
    *)
        exit "$second_pass_status"
        ;;
esac
