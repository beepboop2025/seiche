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
DEPLOY_STATE_DIR=/var/lib/seiche-deploy
STATE=$DEPLOY_STATE_DIR/deployed-sha
RELEASE_ENV=/etc/seiche/release.env
PROMOTION_REQUEST_DIR=/run/seiche-release
PROMOTION_REQUEST=$PROMOTION_REQUEST_DIR/promotion-request.json
PROMOTION_UNIT=seiche-snapshot-promote.service
DEPLOY_RUNTIME_DIR=/run/seiche-deploy
DEPLOY_LOCK=$DEPLOY_RUNTIME_DIR/deploy.lock

if [ -L "$DEPLOY_RUNTIME_DIR" ] \
    || { [ -e "$DEPLOY_RUNTIME_DIR" ] && [ ! -d "$DEPLOY_RUNTIME_DIR" ]; }; then
  echo "FAIL: deploy runtime directory is not a real directory"
  exit 1
fi
install -d -o root -g root -m 0700 "$DEPLOY_RUNTIME_DIR"
if [ "$(stat -c '%U:%G:%a' "$DEPLOY_RUNTIME_DIR")" != "root:root:700" ]; then
  echo "FAIL: deploy runtime directory permissions are unsafe"
  exit 1
fi
exec 9>"$DEPLOY_LOCK"
chown root:root "$DEPLOY_LOCK"
chmod 0600 "$DEPLOY_LOCK"
if ! flock --nonblock 9; then
  echo "FAIL: another seiche deployment is still running"
  exit 1
fi

valid_release_sha() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]]
}

valid_activation_token() {
  [[ "$1" =~ ^[0-9a-f]{64}$ ]]
}

DEPLOYED_STATE_RENAMED=""
write_deployed_state() {
  local release_sha="$1" stage=""
  DEPLOYED_STATE_RENAMED=""
  if ! valid_release_sha "$release_sha"; then
    echo "FAIL: refusing to record a non-canonical deployed SHA"
    return 1
  fi
  stage=$(mktemp "$DEPLOY_STATE_DIR/.deployed-sha.XXXXXX") || return 1
  if ! printf '%s\n' "$release_sha" >"$stage" \
      || ! chown root:root "$stage" \
      || ! chmod 0600 "$stage" \
      || ! /usr/bin/sync -f "$stage"; then
    rm -f -- "$stage"
    echo "FAIL: could not atomically record the deployed release"
    return 1
  fi
  if ! mv -f "$stage" "$STATE"; then
    rm -f -- "$stage"
    echo "FAIL: could not atomically record the deployed release"
    return 1
  fi
  # The visible state now names the candidate. Even if its directory flush
  # fails, rolling old code back would contradict the state we just installed.
  DEPLOYED_STATE_RENAMED=1
  if ! /usr/bin/sync "$DEPLOY_STATE_DIR"; then
    echo "FAIL: could not durably record the deployed release"
    return 1
  fi
}

write_release_env() {
  local release_sha="$1" stage=""
  if ! valid_release_sha "$release_sha"; then
    echo "FAIL: refusing to install a non-canonical release SHA"
    return 1
  fi
  if [ ! -d /etc/seiche ] || [ -L /etc/seiche ]; then
    echo "FAIL: /etc/seiche is not a safe release environment directory"
    return 1
  fi
  stage=$(mktemp /etc/seiche/.release.env.XXXXXX) || return 1
  if ! printf 'SEICHE_RELEASE_SHA=%s\n' "$release_sha" >"$stage" \
      || ! chown root:seiche "$stage" \
      || ! chmod 0640 "$stage" \
      || ! mv -f "$stage" "$RELEASE_ENV"; then
    rm -f -- "$stage"
    echo "FAIL: could not atomically install the release environment"
    return 1
  fi
}

write_promotion_request() {
  local expected_sha="$1" activation_token="$2" stage=""
  if ! valid_release_sha "$expected_sha" \
      || ! valid_activation_token "$activation_token"; then
    echo "FAIL: refusing to write an invalid snapshot promotion request"
    return 1
  fi
  if [ ! -d "$PROMOTION_REQUEST_DIR" ] \
      || [ -L "$PROMOTION_REQUEST_DIR" ] \
      || [ "$(stat -c '%U:%G:%a' "$PROMOTION_REQUEST_DIR")" != "root:seiche:750" ]; then
    echo "FAIL: snapshot promotion request directory is unsafe"
    return 1
  fi
  stage=$(mktemp "$PROMOTION_REQUEST_DIR/.promotion-request.json.XXXXXX") \
    || return 1
  if ! printf '{"expected_sha":"%s","activation_token":"%s"}\n' \
      "$expected_sha" "$activation_token" >"$stage" \
      || ! chown root:seiche "$stage" \
      || ! chmod 0640 "$stage" \
      || ! mv -f "$stage" "$PROMOTION_REQUEST"; then
    rm -f -- "$stage"
    echo "FAIL: could not atomically install the snapshot promotion request"
    return 1
  fi
}

# The sha whose code is actually RUNNING, written only after a healthy
# restart. HEAD alone cannot answer that: a deploy killed between pull and
# restart leaves HEAD==origin/main with the old process still serving, and
# the old sha-compare then said "nothing to deploy" forever — even
# workflow_dispatch could not recover the box (2026-07-28). A missing file
# means unknown, and unknown means deploy.
if [ -L "$DEPLOY_STATE_DIR" ] \
    || { [ -e "$DEPLOY_STATE_DIR" ] && [ ! -d "$DEPLOY_STATE_DIR" ]; }; then
  echo "FAIL: deploy state directory is not a real directory"
  exit 1
fi
install -d -o root -g root -m 0700 "$DEPLOY_STATE_DIR"
if [ "$(stat -c '%U:%G:%a' "$DEPLOY_STATE_DIR")" != "root:root:700" ]; then
  echo "FAIL: deploy state directory permissions are unsafe"
  exit 1
fi
DEPLOYED=""
if [ -e "$STATE" ] || [ -L "$STATE" ]; then
  if [ -L "$STATE" ] || [ ! -f "$STATE" ] \
      || [ "$(stat -c '%U:%G:%a' "$STATE")" != "root:root:600" ] \
      || ! IFS= read -r DEPLOYED <"$STATE" \
      || ! valid_release_sha "$DEPLOYED"; then
    echo "FAIL: deployed release state is unsafe or invalid"
    exit 1
  fi
fi

BEFORE=$(runuser -u seiche -- git -C "$APP" rev-parse HEAD)
if ! runuser -u seiche -- git -C "$APP" fetch -q origin main; then
  echo "FAIL: could not fetch the candidate release"
  exit 1
fi
LATEST=$(runuser -u seiche -- git -C "$APP" rev-parse origin/main)
if ! valid_release_sha "$LATEST" \
    || ! runuser -u seiche -- git -C "$APP" rev-parse --verify --quiet \
      "$LATEST^{commit}" >/dev/null; then
  echo "FAIL: origin/main did not resolve to a canonical local commit"
  exit 1
fi
# A local controller or the forced-command SSH request passes one reviewed
# identity here. Never let the wrapper silently replace it with a newer main
# tip. A direct root invocation without either constraint retains the explicit
# latest-main maintenance behavior.
EXPECTED_TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}
if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
  if [[ "$SSH_ORIGINAL_COMMAND" =~ ^deploy\ ([0-9a-f]{40})$ ]]; then
    REQUESTED_TARGET=${BASH_REMATCH[1]}
  else
    echo "FAIL: forced deployment command must be deploy plus one commit SHA"
    exit 1
  fi
  if [ -n "$EXPECTED_TARGET" ] && [ "$EXPECTED_TARGET" != "$REQUESTED_TARGET" ]; then
    echo "FAIL: environment and forced-command deployment targets disagree"
    exit 1
  fi
  EXPECTED_TARGET=$REQUESTED_TARGET
fi
TARGET=$LATEST
if [ -n "$EXPECTED_TARGET" ]; then
  if ! valid_release_sha "$EXPECTED_TARGET"; then
    echo "FAIL: expected target is not a canonical commit SHA"
    exit 1
  fi
  if ! runuser -u seiche -- git -C "$APP" rev-parse --verify --quiet \
      "$EXPECTED_TARGET^{commit}" >/dev/null \
      || ! runuser -u seiche -- git -C "$APP" merge-base --is-ancestor \
        "$EXPECTED_TARGET" "$LATEST"; then
    echo "FAIL: reviewed target is not a fetched commit on main"
    exit 1
  fi
  TARGET=$EXPECTED_TARGET
fi
MARKET_WORKER_WAS_ACTIVE=""
MARKET_BACKFILL_WAS_ACTIVE=""
if systemctl is-active --quiet seiche-market-worker.service 2>/dev/null; then
  MARKET_WORKER_WAS_ACTIVE=1
fi
if systemctl is-active --quiet seiche-market-backfill.service 2>/dev/null; then
  MARKET_BACKFILL_WAS_ACTIVE=1
fi
restore_market_services() {
  [ -z "$MARKET_BACKFILL_WAS_ACTIVE" ] \
    || systemctl start --no-block seiche-market-backfill.service 2>/dev/null \
    || true
  [ -z "$MARKET_WORKER_WAS_ACTIVE" ] \
    || systemctl start --no-block seiche-market-worker.service 2>/dev/null \
    || true
}
start_market_services() {
  systemctl start --no-block \
    seiche-market-backfill.service seiche-market-worker.service
}
MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=""
restore_preupdate_market_worker_unit() {
  local restore_sha="$DEPLOYED" stage candidate destination
  [ -n "$MARKET_WORKER_UNIT_MAY_HAVE_CHANGED" ] || return 0
  if ! valid_release_sha "$restore_sha"; then
    restore_sha="$BEFORE"
  fi
  if ! valid_release_sha "$restore_sha" \
      || ! runuser -u seiche -- git -C "$APP" rev-parse --verify --quiet \
        "$restore_sha^{commit}" >/dev/null; then
    echo "FAIL: no verified pre-update worker unit is available"
    return 1
  fi
  stage=$(mktemp -d /etc/systemd/system/.seiche-market-worker-restore.XXXXXX) \
    || return 1
  candidate="$stage/seiche-market-worker.service"
  destination=/etc/systemd/system/seiche-market-worker.service
  if ! runuser -u seiche -- git -C "$APP" show \
      "${restore_sha}:ops/deploy/seiche-market-worker.service" >"$candidate" \
      || ! chmod 0644 "$candidate" \
      || ! systemd-analyze verify "$candidate" \
      || ! mv -f "$candidate" "$destination" \
      || ! systemctl daemon-reload; then
    rm -f -- "$candidate"
    rmdir "$stage" 2>/dev/null || true
    echo "FAIL: pre-update market worker unit could not be restored"
    return 1
  fi
  rmdir "$stage"
  MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=""
  echo "market worker unit restored from ${restore_sha:0:7}"
}
restore_preupdate_api() {
  local restore_sha="$DEPLOYED" deadline
  if ! valid_release_sha "$restore_sha"; then
    restore_sha="$BEFORE"
  fi
  if ! valid_release_sha "$restore_sha" \
      || ! runuser -u seiche -- git -C "$APP" rev-parse --verify --quiet \
        "$restore_sha^{commit}" >/dev/null; then
    echo "FAIL: no verified pre-update release is available to restart"
    return 1
  fi
  if ! runuser -u seiche -- git -C "$APP" reset -q --hard "$restore_sha" \
      || ! runuser -u seiche -- bash -c \
        "cd $APP && timeout -k 30 600 backend/.venv/bin/pip install -q -e './backend[notary]'" \
      || ! write_release_env "$restore_sha" \
      || ! systemctl restart seiche-api; then
    echo "FAIL: pre-update api could not be restored"
    return 1
  fi
  deadline=$((SECONDS + 480))
  until curl -sf -m 10 \
      'http://127.0.0.1:8787/api/health?require_rebuilt=true' >/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ] \
        || ! systemctl is-active --quiet seiche-api; then
      echo "FAIL: restored pre-update api did not become healthy"
      return 1
    fi
    sleep 10
  done
  echo "pre-update api restored at ${restore_sha:0:7}"
}
restore_quiesced_api() {
  if [ -n "$API_QUIESCED" ]; then
    restore_preupdate_api || {
      echo "FAIL: seiche-api needs a human after a pre-restart failure"
      return 1
    }
  fi
}
restore_pre_restart_services() {
  if ! restore_quiesced_api; then
    echo "FAIL: market writers remain stopped because api recovery failed"
    return 1
  fi
  if ! restore_preupdate_market_worker_unit; then
    echo "FAIL: market writers remain stopped because their unit recovery failed"
    return 1
  fi
  restore_market_services
}
systemctl stop seiche-market-worker.service seiche-market-backfill.service \
  2>/dev/null || true
API_QUIESCED=""
if [ "$BEFORE" != "$TARGET" ] || [ "$DEPLOYED" != "$TARGET" ]; then
  if ! systemctl stop seiche-api; then
    restore_market_services
    echo "FAIL: seiche-api could not be quiesced before checkout mutation"
    exit 1
  fi
  API_QUIESCED=1
fi
if ! runuser -u seiche -- env SEICHE_DEPLOYED_SHA="$DEPLOYED" \
    SEICHE_UPDATE_TARGET_SHA="$TARGET" \
    bash /home/seiche/update.sh; then
  restore_pre_restart_services \
    || echo "FAIL: seiche-api needs a human after the update-gate failure"
  echo "FAIL: application update gate failed; recovery was attempted"
  exit 1
fi
AFTER=""
if ! AFTER=$(runuser -u seiche -- git -C "$APP" rev-parse HEAD); then
  restore_pre_restart_services || true
  echo "FAIL: candidate checkout identity could not be resolved"
  exit 1
fi
if [ "$AFTER" != "$TARGET" ] \
    || ! valid_release_sha "$AFTER" \
    || ! runuser -u seiche -- git -C "$APP" diff-index --quiet "$AFTER" --; then
  restore_pre_restart_services || true
  echo "FAIL: candidate checkout does not exactly match its release SHA"
  exit 1
fi
UNTRACKED_IMPORTS=""
if ! UNTRACKED_IMPORTS=$(
  {
    runuser -u seiche -- git -C "$APP" ls-files \
      --others --exclude-standard -- backend
    runuser -u seiche -- git -C "$APP" ls-files \
      --others --ignored --exclude-standard -- backend
  } | awk '
    /\.(py|pyc|so)$/ \
      && $0 !~ /^backend\/\.venv\// \
      && $0 !~ /\/__pycache__\// { print }
  '
); then
  restore_pre_restart_services || true
  echo "FAIL: candidate checkout import-surface audit failed"
  exit 1
fi
if [ -n "$UNTRACKED_IMPORTS" ]; then
  restore_pre_restart_services || true
  echo "FAIL: candidate checkout has untracked importable backend files"
  exit 1
fi

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

deploy_market_platform() {
  local installer="$APP/ops/deploy/install-market-platform.sh"
  if [ ! -f "$installer" ]; then
    echo "FAIL: market-platform installer missing: $installer"
    return 1
  fi
  # Historical backfill can saturate the box. Install the units now, but keep
  # ingestion stopped until the candidate API and repository pass health.
  SEICHE_DEFER_MARKET_START=1 bash "$installer"
}

deploy_pull_unit() {
  local source="$APP/ops/deploy/seiche-pull.service"
  local destination=/etc/systemd/system/seiche-pull.service
  local stage candidate previous had_previous=""
  if [ ! -f "$source" ]; then
    echo "FAIL: canonical pull unit missing: $source"
    return 1
  fi
  stage=$(mktemp -d /etc/systemd/system/.seiche-pull-stage.XXXXXX) || return 1
  candidate="$stage/seiche-pull.service"
  previous="$stage/previous.service"
  if ! install -m 0644 "$source" "$candidate"; then
    rmdir "$stage" 2>/dev/null || true
    return 1
  fi
  if ! systemd-analyze verify "$candidate"; then
    rm -f -- "$candidate"
    rmdir "$stage" 2>/dev/null || true
    echo "FAIL: canonical pull unit did not pass systemd verification"
    return 1
  fi
  if [ -e "$destination" ]; then
    cp -p "$destination" "$previous" || {
      rm -f -- "$candidate"
      rmdir "$stage" 2>/dev/null || true
      return 1
    }
    had_previous=1
  fi
  if ! mv -f "$candidate" "$destination"; then
    rm -f -- "$previous" "$candidate"
    rmdir "$stage" 2>/dev/null || true
    return 1
  fi
  if systemctl daemon-reload; then
    rm -f -- "$previous"
    rmdir "$stage" 2>/dev/null || true
    echo "pull unit: installed cached localhost alert evaluator"
    return 0
  fi

  echo "FAIL: daemon-reload rejected the pull unit; restoring the previous unit"
  if [ -n "$had_previous" ]; then
    mv -f "$previous" "$destination" || {
      echo "FAIL: could not restore $destination"
      rm -f -- "$candidate"
      rmdir "$stage" 2>/dev/null || true
      return 1
    }
  else
    rm -f -- "$destination"
  fi
  systemctl daemon-reload || echo "FAIL: daemon-reload also failed after pull-unit rollback"
  rm -f -- "$candidate" "$previous"
  rmdir "$stage" 2>/dev/null || true
  return 1
}

# A restored last-known-good snapshot makes public reads available immediately,
# but it is not proof that this candidate can assemble a board.  The query flag
# keeps the release gate waiting for a build completed by the current process;
# every poll remains cache-only and cheap.
parse_candidate_health() {
  local body="$1" expected_sha="$2"
  "$APP/backend/.venv/bin/python" -c '
import json
import re
import sys

try:
    expected_sha = sys.argv[2]
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError
    candidate = payload.get("release_candidate")
    if (
        re.fullmatch(r"[0-9a-f]{40}", expected_sha) is None
        or not isinstance(candidate, dict)
        or set(candidate) != {"producer_sha", "activation_token"}
        or candidate.get("producer_sha") != expected_sha
        or not isinstance(candidate.get("activation_token"), str)
        or re.fullmatch(r"[0-9a-f]{64}", candidate["activation_token"]) is None
    ):
        raise ValueError
except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
    raise SystemExit(1)
sys.stdout.write(candidate["activation_token"])
' "$body" "$expected_sha"
}

ACTIVATION_TOKEN=""
candidate_health_once() {
  local expected_sha="$1" body token
  body=$(mktemp) || return 1
  if ! curl -sf -m 10 \
      'http://127.0.0.1:8787/api/internal/v1/release-health' >"$body"; then
    rm -f -- "$body"
    return 1
  fi
  if ! token=$(parse_candidate_health "$body" "$expected_sha"); then
    rm -f -- "$body"
    return 1
  fi
  rm -f -- "$body"
  ACTIVATION_TOKEN="$token"
}

candidate_health_wait() {  # candidate_health_wait SECONDS SHA -> exact candidate
  local window="$1" expected_sha="$2" deadline=$((SECONDS + $1))
  until candidate_health_once "$expected_sha"; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "FAIL: api did not rebuild the exact release after $((window / 60))min warm-up window"
      return 1
    fi
    systemctl is-active --quiet seiche-api || { echo "FAIL: seiche-api died during warm-up"; return 1; }
    sleep 10
  done
  return 0
}

# A rollback target can predate the controller token contract. It still has to
# complete its own rebuild, but a legacy healthy response need not advertise a
# promotion capability that only the candidate gate consumes.
rollback_health_wait() {
  local window="$1" deadline=$((SECONDS + $1))
  until curl -sf -m 10 \
      'http://127.0.0.1:8787/api/health?require_rebuilt=true' >/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "FAIL: rollback api did not rebuild after $((window / 60))min warm-up window"
      return 1
    fi
    systemctl is-active --quiet seiche-api || {
      echo "FAIL: seiche-api died during rollback warm-up"
      return 1
    }
    sleep 10
  done
}

market_health() {
  local body
  body=$(mktemp)
  if ! curl -sf -m 20 http://127.0.0.1:8787/api/v2/coverage >"$body"; then
    echo "FAIL: v2 coverage cannot read the configured market repository"
    rm -f -- "$body"
    return 1
  fi
  if ! "$APP/backend/.venv/bin/python" -c \
      'import json,sys; p=json.load(open(sys.argv[1])); assert p["schema"] == "seiche.coverage.v2"; assert len(p["markets"]) == 10' \
      "$body"; then
    echo "FAIL: v2 coverage returned an invalid market-platform contract"
    rm -f -- "$body"
    return 1
  fi
  rm -f -- "$body"
  systemctl is-active --quiet postgresql || {
    echo "FAIL: PostgreSQL is not active after market-platform provisioning"
    return 1
  }
  return 0
}

POINT_OF_NO_RETURN=""
promote_snapshot_handoff() {
  local attempt
  for attempt in 1 2 3; do
    [ "$attempt" -eq 1 ] || sleep 15
    ACTIVATION_TOKEN=""
    # Refresh immediately before each request so the unit can activate only the
    # exact handoff generation the healthy candidate is serving right now.
    if ! candidate_health_once "$AFTER"; then
      echo "FAIL: promotion attempt $attempt could not refresh exact candidate health"
    elif ! write_promotion_request "$AFTER" "$ACTIVATION_TOKEN"; then
      echo "FAIL: promotion attempt $attempt could not install its exact request"
    elif ! write_deployed_state "$AFTER"; then
      # The candidate is healthy, but without durable acceptance a later
      # forced-deploy could mistake the old release for the rollback target.
      if [ -n "$DEPLOYED_STATE_RENAMED" ]; then
        POINT_OF_NO_RETURN=1
      fi
      echo "FAIL: promotion attempt $attempt could not durably accept the candidate"
    else
      # The unit may commit and then lose its response. Never move the checkout
      # underneath the healthy candidate once an activation has been submitted.
      # deployed-sha already names this healthy candidate, so the boundary also
      # survives this shell process exiting before systemctl returns.
      POINT_OF_NO_RETURN=1
      if systemctl start "$PROMOTION_UNIT"; then
        if ! rm -f -- "$PROMOTION_REQUEST"; then
          echo "FAIL: activated request could not be cleared"
          return 1
        fi
        if ! candidate_health_wait 120 "$AFTER"; then
          echo "FAIL: candidate lost strict health after snapshot activation"
          return 1
        fi
        echo "snapshot handoff: activated controller-approved candidate"
        return 0
      fi
      echo "FAIL: promotion attempt $attempt did not complete"
    fi
  done
  rm -f -- "$PROMOTION_REQUEST" \
    || echo "FAIL: stale snapshot promotion request could not be cleared"
  echo "FAIL: verified candidate snapshot could not be activated after 3 attempts"
  return 1
}

MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=1
deploy_market_platform || {
  restore_pre_restart_services || true
  echo "FAIL: application checkout is intact but market-platform provisioning failed"
  exit 1
}

# The API captures this root-controlled identity at process start. The same
# file is required by the unprivileged promotion unit on both a normal deploy
# and the second pass of the first controller rollout.
if ! write_release_env "$AFTER"; then
  restore_pre_restart_services || true
  echo "FAIL: candidate release identity could not be installed"
  exit 1
fi

if [ "$BEFORE" = "$AFTER" ] && [ "$DEPLOYED" = "$AFTER" ]; then
  echo "already running ${AFTER:0:7} — checking candidate rebuild and edge config"
  if ! systemctl is-active --quiet seiche-api; then
    echo "accepted release api is inactive — restarting it without moving the checkout"
    if ! systemctl restart seiche-api; then
      echo "FAIL: accepted release api could not be restarted; market writers remain stopped"
      exit 1
    fi
    sleep 3
  fi
  candidate_health_wait 900 "$AFTER" || {
    echo "FAIL: accepted release did not recover strict health; market writers remain stopped"
    exit 1
  }
  market_health || {
    restore_market_services
    echo "FAIL: running candidate cannot read the market repository"
    exit 1
  }
  deploy_pull_unit || {
    restore_market_services
    echo "FAIL: canonical pull unit could not be converged"
    exit 1
  }
  promote_snapshot_handoff || {
    restore_market_services
    echo "FAIL: healthy running candidate kept in place; snapshot activation needs a human"
    exit 1
  }
  start_market_services || { echo "FAIL: market services could not be started"; exit 1; }
  deploy_caddy || { echo "FAIL: application is healthy but the Caddy deploy failed and was rolled back"; exit 1; }
  sync_verdict
  echo "already deployed ${AFTER:0:7} — application and edge match the repo"
  exit 0
fi
if [ "$BEFORE" = "$AFTER" ]; then
  echo "HEAD already at ${AFTER:0:7} but the running service is ${DEPLOYED:-unknown} — recovering a wedged deploy"
fi

HEALTHY=""
RESTARTED=""
# Every fallible pre-activation step stays inside a conditional. Under set -e,
# a bare restart failure would otherwise abort before the rollback state machine.
if systemctl restart seiche-api; then
  RESTARTED=1
  sleep 3
else
  echo "FAIL: seiche-api could not be restarted onto the candidate"
fi
if [ -n "$RESTARTED" ] && systemctl is-active --quiet seiche-api; then
  if candidate_health_wait 900 "$AFTER"; then
    if market_health; then
      if deploy_pull_unit; then
        if promote_snapshot_handoff; then
          HEALTHY=1
        fi
      fi
    fi
  fi
elif [ -n "$RESTARTED" ]; then
  echo "FAIL: seiche-api not active after restart"
fi

if [ -n "$HEALTHY" ]; then
  start_market_services || { echo "FAIL: market services could not be started"; exit 1; }
  echo "application ${AFTER:0:7} active and healthy — deploying edge config"
  deploy_caddy || { echo "FAIL: application is healthy but the Caddy deploy failed and was rolled back"; exit 1; }
  sync_verdict
  echo "deployed ${AFTER:0:7} — service active, api healthy, edge config current"
  exit 0
fi

if [ -n "$POINT_OF_NO_RETURN" ]; then
  restore_market_services
  echo "FAIL: snapshot activation failed; healthy candidate code remains running and no rollback was attempted"
  exit 1
fi

# A red warm-up used to leave the NEW code live with a dead API and nothing
# but a red CI run. Roll the service back to the last sha that passed health
# — once, with its own gate and its own on-box timeouts, and loud either
# way: this path always exits 1, because a deploy that needed the rollback
# needs a human even when the rollback lands. Never rely on cancellation.
echo "FAIL: ${AFTER:0:7} did not come healthy after restart"
systemctl stop seiche-market-worker.service seiche-market-backfill.service 2>/dev/null || true
if [ -z "$DEPLOYED" ] || [ "$DEPLOYED" = "$AFTER" ]; then
  echo "FAIL: no previously-deployed sha on record to roll back to — seiche-api needs a human NOW"
  exit 1
fi
if ! valid_release_sha "$DEPLOYED"; then
  echo "FAIL: recorded deployment identity is not a canonical commit SHA — cannot roll back automatically"
  exit 1
fi
if ! runuser -u seiche -- git -C "$APP" rev-parse --verify --quiet "$DEPLOYED^{commit}" >/dev/null; then
  echo "FAIL: recorded sha ${DEPLOYED:0:7} is not in the checkout — cannot roll back automatically"
  exit 1
fi
if ! systemctl stop seiche-api; then
  echo "FAIL: seiche-api could not be stopped cleanly — refusing to mutate its checkout"
  exit 1
fi
if ! write_release_env "$DEPLOYED"; then
  echo "FAIL: rollback release identity could not be installed — checkout remains unchanged"
  exit 1
fi
echo "rolling the service back to ${DEPLOYED:0:7} (last sha that passed health)"
runuser -u seiche -- git -C "$APP" reset -q --hard "$DEPLOYED"
runuser -u seiche -- bash -c "cd $APP && timeout -k 30 600 backend/.venv/bin/pip install -q -e './backend[notary]'" \
  || { echo "FAIL: rollback pip install failed or timed out — seiche-api needs a human NOW"; exit 1; }
runuser -u seiche -- bash -c "cd $APP && timeout -k 30 120 backend/.venv/bin/python -c 'import seiche.api, seiche.assemble, seiche.dispatch_daily'" \
  || { echo "FAIL: rollback tree does not import — seiche-api needs a human NOW"; exit 1; }
restore_preupdate_market_worker_unit \
  || { echo "FAIL: rollback worker unit could not be restored; market writers remain stopped"; exit 1; }
systemctl restart seiche-api
sleep 3
if systemctl is-active --quiet seiche-api && rollback_health_wait 480; then
  write_deployed_state "$DEPLOYED" || {
    echo "FAIL: rollback is healthy but deployed state could not be recorded"
    exit 1
  }
  restore_market_services
  echo "FAIL: rolled back to ${DEPLOYED:0:7}, healthy; the deploy of ${AFTER:0:7} FAILED health and needs a human"
  exit 1
fi
echo "FAIL: rollback to ${DEPLOYED:0:7} did not come healthy either — seiche-api is down and needs a human NOW"
exit 1
