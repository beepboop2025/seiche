#!/usr/bin/env bash
# Atomically move only Seiche REST/MCP edge handlers between local and Railway.
set -euo pipefail
set -f
umask 0077
export LC_ALL=C

readonly CADDYFILE="${SEICHE_EDGE_CADDYFILE:-/etc/caddy/Caddyfile}"
readonly EDGE_ENV="${SEICHE_EDGE_ENV_FILE:-/etc/seiche/railway-edge.env}"
readonly DROPIN="${SEICHE_EDGE_DROPIN:-/etc/systemd/system/caddy.service.d/railway-edge.conf}"
readonly STATE_DIR="${SEICHE_EDGE_STATE_DIR:-/var/lib/seiche-railway-cutover}"
readonly SYSTEMCTL="${SEICHE_EDGE_SYSTEMCTL:-/usr/bin/systemctl}"
readonly CADDY="${SEICHE_EDGE_CADDY:-/usr/bin/caddy}"
readonly CURL="${SEICHE_EDGE_CURL:-/usr/bin/curl}"
readonly PYTHON="${SEICHE_EDGE_PYTHON:-/usr/bin/python3}"
readonly FLOCK="${SEICHE_EDGE_FLOCK:-/usr/bin/flock}"
readonly TEST_MODE="${SEICHE_EDGE_TEST_MODE:-0}"
readonly FENCE="$STATE_DIR/AUTHORITY-FENCE.json"
readonly ACTIVATION_ACK="$STATE_DIR/activation-ack.json"
readonly EDGE_RECEIPT="$STATE_DIR/edge-railway.json"
readonly LOCK_PATH="${SEICHE_EDGE_LOCK_PATH:-/run/lock/seiche-railway-edge.lock}"

fail() {
  printf 'seiche Railway edge: %s\n' "$*" >&2
  exit 1
}

validate_configuration() {
  local command_path
  case "$TEST_MODE" in 0|1) ;; *) fail "test mode must be exactly 0 or 1" ;; esac
  if [ "$TEST_MODE" = 0 ]; then
    [ "${EUID:-$($PYTHON -I -S -c 'import os; print(os.geteuid())')}" -eq 0 ] \
      || fail "must run as root"
    [ "$CADDYFILE" = /etc/caddy/Caddyfile ] \
      && [ "$EDGE_ENV" = /etc/seiche/railway-edge.env ] \
      && [ "$DROPIN" = /etc/systemd/system/caddy.service.d/railway-edge.conf ] \
      && [ "$STATE_DIR" = /var/lib/seiche-railway-cutover ] \
      && [ "$LOCK_PATH" = /run/lock/seiche-railway-edge.lock ] \
      || fail "production paths are fixed"
    [ "$SYSTEMCTL" = /usr/bin/systemctl ] \
      && [ "$CADDY" = /usr/bin/caddy ] \
      && [ "$CURL" = /usr/bin/curl ] \
      && [ "$PYTHON" = /usr/bin/python3 ] \
      && [ "$FLOCK" = /usr/bin/flock ] \
      || fail "production commands are fixed"
  fi
  for command_path in "$SYSTEMCTL" "$CADDY" "$CURL" "$PYTHON" "$FLOCK"; do
    [ -x "$command_path" ] || fail "required command is unavailable"
  done
  [ -f "$CADDYFILE" ] && [ ! -L "$CADDYFILE" ] \
    || fail "Caddyfile is unavailable or unsafe"
  [ -d "$STATE_DIR" ] && [ ! -L "$STATE_DIR" ] \
    || fail "cutover state is unavailable or unsafe"
  mkdir -p "$(dirname "$EDGE_ENV")" "$(dirname "$DROPIN")"
}

validate_fence() {
  local expected_sha="$1"
  [ -f "$FENCE" ] && [ ! -L "$FENCE" ] \
    || fail "authority fence is unavailable"
  "$PYTHON" -I -B - "$FENCE" "$expected_sha" <<'PY'
import json
from datetime import UTC, datetime
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
commit = sys.argv[2]
body = path.read_bytes()
value = json.loads(body)
if (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode() != body:
    raise SystemExit("authority fence is not canonical")
authority = value.get("authority", {})
if (
    re.fullmatch(r"[0-9a-f]{40}", commit) is None
    or value.get("schema") != "seiche.railway-authority-fence.v1"
    or value.get("commit") != commit
    or authority.get("source") != "hetzner"
    or authority.get("state") != "frozen"
    or authority.get("writers_frozen") is not True
    or authority.get("api_stopped") is not True
    or value.get("can_activate_railway") is not True
):
    raise SystemExit("authority fence cannot authorize the edge")
expires = datetime.fromisoformat(authority["expires_at"].replace("Z", "+00:00"))
if datetime.now(UTC).replace(microsecond=0) > expires:
    raise SystemExit("authority fence expired before edge activation")
PY
}

probe() {
  local expected_authority="$1" expected_deployment="$2" expected_sha="$3"
  local url="$4" token="${5:-}" body headers status
  body=$(mktemp "$STATE_DIR/.edge-body.XXXXXX")
  headers=$(mktemp "$STATE_DIR/.edge-headers.XXXXXX")
  trap 'rm -f -- "$body" "$headers"' RETURN
  if [ -n "$token" ]; then
    status=$(
      "$CURL" --silent --show-error --proto '=https' --tlsv1.2 \
        --connect-timeout 10 --max-time 30 --output "$body" \
        --dump-header "$headers" --write-out '%{http_code}' \
        --header "X-Seiche-Edge-Token: $token" "$url"
    )
  else
    status=$(
      "$CURL" --silent --show-error --proto '=https' --tlsv1.2 \
        --connect-timeout 10 --max-time 30 --output "$body" \
        --dump-header "$headers" --write-out '%{http_code}' "$url"
    )
  fi
  if [ "$status" != 200 ]; then
    printf 'seiche Railway edge: edge probe returned HTTP %s\n' "$status" >&2
    return 1
  fi
  EXPECTED_AUTHORITY="$expected_authority" \
    EXPECTED_DEPLOYMENT="$expected_deployment" EXPECTED_SHA="$expected_sha" \
    HEADERS="$headers" BODY="$body" "$PYTHON" -I -B - <<'PY'
import json
import os
from pathlib import Path

headers = Path(os.environ["HEADERS"]).read_text(encoding="iso-8859-1")
observed = {}
for line in headers.splitlines():
    if ":" in line:
        name, value = line.split(":", 1)
        observed[name.strip().lower()] = value.strip()
expected = {
    "x-seiche-railway-authority": os.environ["EXPECTED_AUTHORITY"],
    "x-seiche-railway-deployment": os.environ["EXPECTED_DEPLOYMENT"],
    "x-seiche-release-sha": os.environ["EXPECTED_SHA"],
}
if any(observed.get(name) != value for name, value in expected.items()):
    raise SystemExit("edge response identity is invalid")
body = json.loads(Path(os.environ["BODY"]).read_bytes())
if not isinstance(body, dict) or not isinstance(body.get("version"), str):
    raise SystemExit("edge response body is invalid")
PY
  rm -f -- "$body" "$headers"
  trap - RETURN
}

restart_caddy() {
  "$SYSTEMCTL" daemon-reload
  "$SYSTEMCTL" restart caddy
  "$SYSTEMCTL" is-active --quiet caddy
}

activate_railway() {
  local expected_sha="$1" expected_deployment="$2"
  local origin="${SEICHE_RAILWAY_ORIGIN:-}" token="${SEICHE_RAILWAY_EDGE_TOKEN:-}"
  local env_stage dropin_stage receipt_stage switched=0
  [ "${SEICHE_EDGE_CONFIRM:-}" = RAILWAY_CANDIDATE_RECEIPTED_READ_ONLY ] \
    || fail "Railway candidate confirmation is absent"
  [ ! -e "$ACTIVATION_ACK" ] && [ ! -L "$ACTIVATION_ACK" ] \
    || fail "Railway is already acknowledged as writer"
  [[ "$origin" =~ ^https://[a-z0-9][a-z0-9.-]{1,251}\.up\.railway\.app$ ]] \
    || fail "Railway origin is invalid"
  [ "${#token}" -ge 32 ] && [ "${#token}" -le 512 ] \
    && [[ "$token" =~ ^[A-Za-z0-9._~=-]+$ ]] \
    || fail "Railway edge token is invalid"
  [[ "$expected_deployment" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] \
    || fail "Railway deployment ID is invalid"
  validate_fence "$expected_sha"
  [ ! -e "$EDGE_RECEIPT" ] && [ ! -L "$EDGE_RECEIPT" ] \
    || fail "Railway edge receipt already exists"
  [ ! -e "$EDGE_ENV" ] && [ ! -L "$EDGE_ENV" ] \
    && [ ! -e "$DROPIN" ] && [ ! -L "$DROPIN" ] \
    || fail "existing Railway edge configuration needs reconciliation"
  probe candidate "$expected_deployment" "$expected_sha" \
    "$origin/api/health" "$token"
  SEICHE_API_UPSTREAM="$origin" SEICHE_RAILWAY_EDGE_TOKEN="$token" \
    "$CADDY" validate --config "$CADDYFILE" --adapter caddyfile
  env_stage=$(mktemp "$(dirname "$EDGE_ENV")/.railway-edge.env.XXXXXX")
  dropin_stage=$(mktemp "$(dirname "$DROPIN")/.railway-edge.conf.XXXXXX")
  printf 'SEICHE_API_UPSTREAM=%s\nSEICHE_RAILWAY_EDGE_TOKEN=%s\n' \
    "$origin" "$token" >"$env_stage"
  printf '[Service]\nEnvironmentFile=%s\n' "$EDGE_ENV" >"$dropin_stage"
  chmod 0600 "$env_stage"
  chmod 0644 "$dropin_stage"
  mv "$env_stage" "$EDGE_ENV"
  mv "$dropin_stage" "$DROPIN"
  if restart_caddy; then
    switched=1
  fi
  if [ "$switched" != 1 ] || ! probe candidate "$expected_deployment" \
      "$expected_sha" 'https://api.seiche.info/api/health'; then
    rm -f -- "$EDGE_ENV" "$DROPIN"
    restart_caddy || true
    fail "Railway edge activation failed and loopback was restored"
  fi
  receipt_stage=$(mktemp "$STATE_DIR/.edge-railway.XXXXXX")
  ORIGIN="$origin" DEPLOYMENT="$expected_deployment" COMMIT="$expected_sha" \
    DESTINATION="$receipt_stage" "$PYTHON" -I -B - <<'PY'
import hashlib
import json
import os
from pathlib import Path
from datetime import UTC, datetime

value = {
    "schema": "seiche.railway-edge-receipt.v1",
    "authority": "candidate",
    "public_base_url": "https://api.seiche.info",
    "origin": os.environ["ORIGIN"],
    "deployment_id": os.environ["DEPLOYMENT"],
    "commit": os.environ["COMMIT"],
    "edge_token_sha256": hashlib.sha256(
        os.environ["SEICHE_RAILWAY_EDGE_TOKEN"].encode()
    ).hexdigest(),
    "switched_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
}
Path(os.environ["DESTINATION"]).write_text(
    json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY
  chmod 0400 "$receipt_stage"
  mv "$receipt_stage" "$EDGE_RECEIPT"
  printf 'seiche Railway edge: public traffic reaches exact read-only candidate\n'
}

restore_local() {
  local suffix
  [ "${SEICHE_EDGE_CONFIRM:-}" = RAILWAY_CANDIDATE_STOPPED_NO_WRITERS ] \
    || fail "local rollback confirmation is absent"
  [ ! -e "$ACTIVATION_ACK" ] && [ ! -L "$ACTIVATION_ACK" ] \
    || fail "local rollback is forbidden after Railway activation"
  suffix=$($PYTHON -I -S -c 'from time import time_ns; print(time_ns())')
  [ ! -e "$EDGE_ENV" ] || mv "$EDGE_ENV" "$STATE_DIR/railway-edge.env.rolled-back-$suffix"
  [ ! -e "$DROPIN" ] || mv "$DROPIN" "$STATE_DIR/railway-edge.conf.rolled-back-$suffix"
  [ ! -e "$EDGE_RECEIPT" ] \
    || mv "$EDGE_RECEIPT" "$STATE_DIR/edge-railway.json.rolled-back-$suffix"
  restart_caddy || fail "Caddy did not recover its loopback configuration"
  printf 'seiche Railway edge: loopback configuration restored; restart Hetzner authority\n'
}

validate_configuration
[ "$#" -ge 1 ] || fail "usage: $0 railway EXPECTED_SHA DEPLOYMENT_ID | local | status"
exec 9>"$LOCK_PATH"
"$FLOCK" --exclusive --nonblock 9 || fail "another edge controller holds the lock"
case "$1" in
  railway)
    [ "$#" -eq 3 ] || fail "railway mode requires SHA and deployment ID"
    activate_railway "$2" "$3"
    ;;
  local)
    [ "$#" -eq 1 ] || fail "local mode accepts no identity arguments"
    restore_local
    ;;
  status)
    [ "$#" -eq 1 ] || fail "status accepts no identity arguments"
    if [ -f "$EDGE_ENV" ] && [ -f "$DROPIN" ]; then
      printf 'railway\n'
    else
      printf 'local\n'
    fi
    ;;
  *) fail "mode must be railway, local, or status" ;;
esac
