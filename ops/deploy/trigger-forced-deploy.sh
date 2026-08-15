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
SLEEP_BIN="${SEICHE_DEPLOY_SLEEP_BIN:-sleep}"
TARGET_SHA="${SEICHE_EXPECTED_TARGET_SHA:?SEICHE_EXPECTED_TARGET_SHA is required}"
DEFER_WAIT_SECONDS="${SEICHE_DEPLOY_DEFER_WAIT_SECONDS:-600}"
DEFER_RETRY_SECONDS="${SEICHE_DEPLOY_DEFER_RETRY_SECONDS:-30}"
if [[ ! "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
    echo "deployment target is not a canonical commit SHA" >&2
    exit 2
fi
if [[ ! "$DEFER_WAIT_SECONDS" =~ ^(0|[1-9][0-9]*)$ ]] \
    || (( DEFER_WAIT_SECONDS > 1200 )); then
    echo "SEICHE_DEPLOY_DEFER_WAIT_SECONDS must be an integer from 0 to 1200" >&2
    exit 2
fi
if [[ ! "$DEFER_RETRY_SECONDS" =~ ^[1-9][0-9]*$ ]] \
    || (( DEFER_RETRY_SECONDS > 300 )); then
    echo "SEICHE_DEPLOY_DEFER_RETRY_SECONDS must be an integer from 1 to 300" >&2
    exit 2
fi
DEFER_DEADLINE=$((SECONDS + DEFER_WAIT_SECONDS))

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

trigger_with_defer_retry() {
    local pass_label="$1" status=0 remaining=0 delay=0
    while true; do
        status=0
        trigger || status=$?
        if (( status == 0 )); then
            return 0
        fi
        if (( status != 75 )); then
            return "$status"
        fi
        remaining=$((DEFER_DEADLINE - SECONDS))
        if (( remaining <= 0 )); then
            printf 'forced deploy pass %s remained safely deferred after %ss\n' \
                "$pass_label" "$DEFER_WAIT_SECONDS" >&2
            return 75
        fi
        delay="$DEFER_RETRY_SECONDS"
        if (( delay > remaining )); then
            delay="$remaining"
        fi
        printf 'forced deploy pass %s safely deferred; retrying in %ss (%ss remain)\n' \
            "$pass_label" "$delay" "$remaining"
        "$SLEEP_BIN" "$delay"
    done
}

echo "forced deploy pass 1/2: application release + wrapper self-sync"
trigger_with_defer_retry "1/2"
echo "forced deploy pass 2/2: same-SHA edge convergence"
trigger_with_defer_retry "2/2"
