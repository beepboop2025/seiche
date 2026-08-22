#!/usr/bin/env bash
# Manual full deploy on the PRODUCTION box (run as root ON the box).
# Rewritten 2026-07-20: the previous version assumed /opt/seiche +
# seiche.service — a layout that never existed on the box (/home/seiche/app,
# seiche-api.service) — and could never have worked there.
#
# The engine deploy is the same rollback-owning chain used by the host release
# poller and by the disabled/manual deploy-hetzner recovery workflow:
#
#   /var/lib/seiche-deploy/bin/seiche-deploy-wrapper.sh
#                                           (mirror: seiche-deploy-wrapper.sh)
#     └─ /home/seiche/update.sh             (mirror: box-update.sh)
#        pull main → pip install → focused smoke suite, rollback on red
#     └─ systemctl restart seiche-api + poll cache-only /api/health
#
# The wrapper also deploys ops/Caddyfile to the edge, test-gated with backup
# and rollback. Keeping that operation in the forced-command path means manual,
# recovery, and poller-driven deploys exercise the same release boundary.
# Frontend is NOT built here — seiche.info ships via the publish workflow
# (Cloudflare Pages), the box serves only api.seiche.info.
set -uo pipefail

APP_DIR=/home/seiche/app

DEPLOY_WRAPPER=/var/lib/seiche-deploy/bin/seiche-deploy-wrapper.sh

if [ ! -x "$DEPLOY_WRAPPER" ]; then
    echo "FATAL: $DEPLOY_WRAPPER missing — this is not the production box (or the wrapper was removed). See header." >&2
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
#
# Note this copies from whatever is CHECKED OUT (pre-pull) — it exists to
# bootstrap or un-wedge a box by hand. The auto chain no longer depends on
# it: the wrapper re-syncs both mirrors from the POST-pull checkout on every
# deploy, effective the next run, and fails the run loud if the sync drifts.
for pair in "seiche-deploy-wrapper.sh:$DEPLOY_WRAPPER" \
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

"$DEPLOY_WRAPPER" || exit 1

echo "Deployed $(git -C "$APP_DIR" rev-parse --short HEAD) — $(git -C "$APP_DIR" log -1 --format=%s)"
