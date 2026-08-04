#!/bin/bash
# Forced-command target for the GitHub Actions deploy key. The key in
# authorized_keys can run THIS script and nothing else (no pty, no forwarding).
# update.sh pulls main, pip-installs and runs the smoke gate with rollback;
# only a green tree gets restarted. After that restart is healthy, the same
# green checkout's Caddyfile is validated, backed up, installed and reloaded.
#
# Mirrored in the repo at ops/deploy/seiche-deploy-wrapper.sh. Edit the REPO
# copy: after update.sh pulls, this script installs the post-pull checkout's
# versions of itself and update.sh, so the box copies no longer drift from
# the repo until a human remembers to copy them. New copies take effect next
# deploy; a failed sync turns the run red at the end without blocking today's
# deploy.
set -euo pipefail
echo "== seiche auto-deploy $(date -u +%FT%TZ) =="

APP=/home/seiche/app

# The sha whose code is actually RUNNING, written only after a healthy
# restart. HEAD alone cannot answer that: a deploy killed between pull and
# restart leaves HEAD==origin/main with the old process still serving, and
# the old sha-compare then said "nothing to deploy" forever — even
# workflow_dispatch could not recover the box (2026-07-28). A missing file
# means unknown, and unknown means deploy.
STATE=/home/seiche/.seiche-deployed-sha
DEPLOYED=$(cat "$STATE" 2>/dev/null || true)

BEFORE=$(runuser -u seiche -- git -C "$APP" rev-parse HEAD)
runuser -u seiche -- bash /home/seiche/update.sh
AFTER=$(runuser -u seiche -- git -C "$APP" rev-parse HEAD)

# Self-sync the deploy chain from the POST-pull checkout. The manual root
# deploy synced these mirrors only when someone ran it, and from the pre-pull
# tree (one deploy behind); the auto chain never synced at all, so a repo fix
# to either script changed nothing on the box. Installed atomically
# (write-beside + rename) so the running copy keeps its inode and this run
# finishes on the code it started with — no re-exec, so no loop.
SYNC_FAIL=""
for pair in "seiche-deploy-wrapper.sh:/root/seiche-deploy-wrapper.sh" \
            "box-update.sh:/home/seiche/update.sh"; do
  src="$APP/ops/deploy/${pair%%:*}"
  dst="${pair##*:}"
  if [ ! -f "$src" ]; then
    echo "sync: $src missing from the checkout"; SYNC_FAIL=1; continue
  fi
  if cmp -s "$src" "$dst"; then
    continue
  fi
  if ! bash -n "$src"; then
    echo "sync: $src fails a syntax check; keeping the installed copy"; SYNC_FAIL=1; continue
  fi
  cp "$dst" "$dst.bak-$(date +%s)" 2>/dev/null || true
  if cp "$src" "$dst.new" && chmod +x "$dst.new" && mv -f "$dst.new" "$dst"; then
    echo "sync: installed $dst from the post-pull checkout (effective next deploy)"
  else
    echo "sync: could not install $dst"; SYNC_FAIL=1
  fi
done

sync_verdict() {  # loud drift check at exit: a red run, never a wedged box
  if [ -n "$SYNC_FAIL" ]; then
    echo "FAIL: deploy-script sync failed (see sync: lines above) — the box mirrors drift from the repo"
    exit 1
  fi
}

deploy_caddy() {
  local installer="$APP/ops/deploy/install-caddy.sh"
  if [ ! -f "$installer" ]; then
    echo "FAIL: Caddy installer missing from the post-pull checkout: $installer"
    return 1
  fi
  # Invoke with bash rather than trusting the executable bit: old checkouts can
  # carry the helper before its mode has been repaired on the box.
  bash "$installer"
}

if [ "$BEFORE" = "$AFTER" ] && [ "$DEPLOYED" = "$AFTER" ]; then
  echo "already running ${AFTER:0:7} — checking edge config"
  deploy_caddy || { echo "FAIL: application is healthy but the Caddy deploy failed and was rolled back"; exit 1; }
  sync_verdict
  echo "already deployed ${AFTER:0:7} — application and edge match the repo"
  exit 0
fi
if [ "$BEFORE" = "$AFTER" ]; then
  echo "HEAD already at ${AFTER:0:7} but the running service is ${DEPLOYED:-unknown} — recovering a wedged deploy"
fi

# The API rebuilds its board on start and can take minutes before it answers;
# poll instead of a single probe so a healthy warm-up is never reported red.
health_wait() {  # health_wait SECONDS -> 0 healthy, 1 dead or window exhausted
  local deadline=$((SECONDS + $1))
  until curl -sf -m 10 http://127.0.0.1:8787/api/public >/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "FAIL: api not answering after $(($1 / 60))min warm-up window"
      return 1
    fi
    systemctl is-active --quiet seiche-api || { echo "FAIL: seiche-api died during warm-up"; return 1; }
    sleep 10
  done
  return 0
}

systemctl restart seiche-api
sleep 3
# NOTE: health checks run inside `if` conditions on purpose — under set -e a
# bare failing check would abort the script here and skip the rollback below.
HEALTHY=""
if systemctl is-active --quiet seiche-api; then
  if health_wait 900; then
    HEALTHY=1
  fi
else
  echo "FAIL: seiche-api not active after restart"
fi

if [ -n "$HEALTHY" ]; then
  printf '%s\n' "$AFTER" > "$STATE"
  echo "application ${AFTER:0:7} active and healthy — deploying edge config"
  deploy_caddy || { echo "FAIL: application is healthy but the Caddy deploy failed and was rolled back"; exit 1; }
  sync_verdict
  echo "deployed ${AFTER:0:7} — service active, api healthy, edge config current"
  exit 0
fi

# A red warm-up used to leave the NEW code live with a dead API and nothing
# but a red CI run. Roll the service back to the last sha that passed health
# — once, with its own gate and its own on-box timeouts, and loud either
# way: this path always exits 1, because a deploy that needed the rollback
# needs a human even when the rollback lands. Never rely on cancellation.
echo "FAIL: ${AFTER:0:7} did not come healthy after restart"
if [ -z "$DEPLOYED" ] || [ "$DEPLOYED" = "$AFTER" ]; then
  echo "FAIL: no previously-deployed sha on record to roll back to — seiche-api needs a human NOW"
  exit 1
fi
if ! runuser -u seiche -- git -C "$APP" rev-parse --verify --quiet "$DEPLOYED^{commit}" >/dev/null; then
  echo "FAIL: recorded sha ${DEPLOYED:0:7} is not in the checkout — cannot roll back automatically"
  exit 1
fi
echo "rolling the service back to ${DEPLOYED:0:7} (last sha that passed health)"
runuser -u seiche -- git -C "$APP" reset -q --hard "$DEPLOYED"
runuser -u seiche -- bash -c "cd $APP && timeout -k 30 600 backend/.venv/bin/pip install -q -e './backend[notary]'" \
  || { echo "FAIL: rollback pip install failed or timed out — seiche-api needs a human NOW"; exit 1; }
runuser -u seiche -- bash -c "cd $APP && timeout -k 30 120 backend/.venv/bin/python -c 'import seiche.api, seiche.assemble, seiche.dispatch_daily'" \
  || { echo "FAIL: rollback tree does not import — seiche-api needs a human NOW"; exit 1; }
systemctl restart seiche-api
sleep 3
if systemctl is-active --quiet seiche-api && health_wait 480; then
  printf '%s\n' "$DEPLOYED" > "$STATE"
  echo "FAIL: rolled back to ${DEPLOYED:0:7}, healthy; the deploy of ${AFTER:0:7} FAILED health and needs a human"
  exit 1
fi
echo "FAIL: rollback to ${DEPLOYED:0:7} did not come healthy either — seiche-api is down and needs a human NOW"
exit 1
