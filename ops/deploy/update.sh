#!/usr/bin/env bash
# Manual full deploy on the PRODUCTION box (run as root ON the box).
# Rewritten 2026-07-20: the previous version assumed /opt/seiche +
# seiche.service — a layout that never existed on the box (/home/seiche/app,
# seiche-api.service) — and could never have worked there.
#
# The engine deploy is exactly what GitHub Actions (deploy-hetzner) triggers:
#
#   /root/seiche-deploy-wrapper.sh          (mirror: seiche-deploy-wrapper.sh)
#     └─ /home/seiche/update.sh             (mirror: box-update.sh)
#        pull main → pip install → full test suite, rollback on red
#     └─ systemctl restart seiche-api + poll /api/public through warm-up
#
# This script adds the one thing the auto chain does not do: deploying
# ops/Caddyfile to the edge, test-gated with backup and rollback.
# Frontend is NOT built here — seiche.info ships via the publish workflow
# (Cloudflare Pages), the box serves only api.seiche.info.
set -uo pipefail

APP_DIR=/home/seiche/app

if [ ! -x /root/seiche-deploy-wrapper.sh ]; then
    echo "FATAL: /root/seiche-deploy-wrapper.sh missing — this is not the production box (or the wrapper was removed). See header." >&2
    exit 1
fi

# SYNC THE MIRRORS FIRST. The two scripts the auto chain runs live ON the box,
# so "mirrored in the repo, edit both" was a promise nothing enforced, and both
# copies drifted: the wrapper lost the Caddyfile step, and the update script
# kept a full-suite test gate that outgrew every deploy timeout we gave it.
# Editing the repo did nothing until someone remembered to copy by hand, which
# is the same as not fixing it. This installs the repo versions, with a backup
# and a syntax check, BEFORE the wrapper runs, so from here the repo is the
# source of truth and a stale box is a one-command fix rather than a mystery.
for pair in "seiche-deploy-wrapper.sh:/root/seiche-deploy-wrapper.sh" \
            "box-update.sh:/home/seiche/update.sh"; do
    src="$APP_DIR/ops/deploy/${pair%%:*}"
    dst="${pair##*:}"
    [ -f "$src" ] || { echo "::warning ::sync: $src missing, leaving $dst as is."; continue; }
    if cmp -s "$src" "$dst"; then
        echo "sync: $dst already matches the repo."
    elif ! bash -n "$src"; then
        echo "::warning ::sync: $src FAILED a syntax check, leaving $dst as is."
    else
        cp "$dst" "$dst.bak-$(date +%s)" 2>/dev/null || true
        if cp "$src" "$dst" && chmod +x "$dst"; then
            echo "sync: installed $dst from the repo (previous copy kept as $dst.bak-*)."
        else
            echo "::warning ::sync: could not install $dst — the box keeps its old copy."
        fi
    fi
done

/root/seiche-deploy-wrapper.sh || exit 1

# HARDENING: deploy the edge (Caddy) config for api.seiche.info — but only
# when it changed, and never at the cost of the engine deploy. Every caddy
# step is if-guarded so a failure warns and continues instead of tripping
# set -e. Sits after the wrapper's test gate above, so a red suite also
# skips edge changes. Runs as root (writes /etc/caddy, talks to the caddy
# admin API).
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

echo "Deployed $(git -C "$APP_DIR" rev-parse --short HEAD) — $(git -C "$APP_DIR" log -1 --format=%s)"
