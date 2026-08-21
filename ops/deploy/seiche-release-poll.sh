#!/usr/bin/env bash
# Poll trusted origin/main, gate one exact commit outside the live checkout, and
# hand only that tested identity to the existing rollback-owning deploy wrapper.
#
# This is installed as /usr/local/sbin/seiche-release-poll by
# install-release-poller.sh.  It deliberately has no write credential for the
# source repository: GitHub remains the source of truth and the box only reads.
set -euo pipefail

APP_DIR="${SEICHE_CONTROL_APP_DIR:-/home/seiche/app}"
SERVICE_USER="${SEICHE_CONTROL_USER:-seiche}"
STATE_DIR="${SEICHE_CONTROL_STATE_DIR:-/var/lib/seiche-control}"
RECEIPT_DIR="$STATE_DIR/receipts"
CANDIDATE_PARENT="$STATE_DIR/candidates"
CANDIDATE_DIR="$CANDIDATE_PARENT/main"
RUNTIME_DIR="${SEICHE_CONTROL_RUNTIME_DIR:-/run/seiche-control}"
CONTROL_LOCK="$RUNTIME_DIR/release.lock"
DEPLOY_STATE="${SEICHE_CONTROL_DEPLOY_STATE:-/var/lib/seiche-deploy/deployed-sha}"
DEPLOY_WRAPPER="${SEICHE_CONTROL_DEPLOY_WRAPPER:-/root/seiche-deploy-wrapper.sh}"
RUNUSER="${SEICHE_CONTROL_RUNUSER:-runuser}"
SYSTEMCTL="${SEICHE_CONTROL_SYSTEMCTL:-systemctl}"
CURL="${SEICHE_CONTROL_CURL:-curl}"
SYSTEM_PYTHON="${SEICHE_CONTROL_PYTHON:-python3}"
TIMEOUT="${SEICHE_CONTROL_TIMEOUT:-timeout}"
SLEEP="${SEICHE_CONTROL_SLEEP:-sleep}"
SYNC="${SEICHE_CONTROL_SYNC:-/usr/bin/sync}"
SHA256SUM="${SEICHE_CONTROL_SHA256SUM:-sha256sum}"
ALLOWED_SIGNERS="${SEICHE_CONTROL_ALLOWED_SIGNERS:-/etc/seiche-release.allowed-signers}"
SIGNING_PRINCIPAL="${SEICHE_CONTROL_SIGNING_PRINCIPAL:-beepboop2025@users.noreply.github.com}"
AUTOMATION_AUTHOR="${SEICHE_CONTROL_AUTOMATION_AUTHOR:-desk@seiche.info}"
SIGNER_UID="${SEICHE_CONTROL_SIGNER_UID:-0}"
SIGNER_GID="${SEICHE_CONTROL_SIGNER_GID:-0}"
SIGNER_MODE="${SEICHE_CONTROL_SIGNER_MODE:-444}"
SSH_KEYGEN="${SEICHE_CONTROL_SSH_KEYGEN:-/usr/bin/ssh-keygen}"
GATE_ONLY="${SEICHE_CONTROL_GATE_ONLY:-0}"
ADMISSION_WAIT_SECONDS="${SEICHE_CONTROL_ADMISSION_WAIT_SECONDS:-900}"
ADMISSION_RETRY_SECONDS="${SEICHE_CONTROL_ADMISSION_RETRY_SECONDS:-30}"
INSTALL_COMMAND="python -m pip install -q -e ./backend[dev,collectors]"
TEST_COMMAND="python -m pytest backend/tests -q --memray --pystack-threshold=300"
STARTED_AT=$(date -u +%FT%TZ)
CANDIDATE_ADDED=""
HEALTH_BODY=""

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

valid_sha() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]]
}

as_service() {
  "$RUNUSER" -u "$SERVICE_USER" -- "$@"
}

is_inert_automation_content_commit() {
  local target="$1" author="" subject="" parents="" changed_files="" changed_path=""
  author=$(as_service git -C "$APP_DIR" show -s --format=%ae "$target") \
    || return 1
  [ "$author" = "$AUTOMATION_AUTHOR" ] || return 1
  subject=$(as_service git -C "$APP_DIR" show -s --format=%s "$target") \
    || return 1
  case "$subject" in
    "dispatch: "*|"week ahead: "*) ;;
    *) return 1 ;;
  esac
  parents=$(as_service git -C "$APP_DIR" show -s --format=%P "$target") \
    || return 1
  # Generated desk commits must be ordinary one-parent commits. Merge commits
  # never qualify for the inert path even if their visible diff looks narrow.
  # The split below is an intentional word count over canonical SHA tokens.
  # shellcheck disable=SC2086
  set -- $parents
  [ "$#" -eq 1 ] || return 1
  changed_files=$(as_service git -C "$APP_DIR" -c core.quotePath=true diff-tree \
    --no-commit-id --no-renames --name-only -r "${target}^" "$target") \
    || return 1
  [ -n "$changed_files" ] || return 1
  while IFS= read -r changed_path; do
    [ -n "$changed_path" ] || return 1
    case "$changed_path" in
      frontend/public/dispatches/*|frontend/public/articles/*|backend/seiche/dispatches/*) ;;
      *) return 1 ;;
    esac
  done <<<"$changed_files"
  return 0
}

validate_allowed_signers() {
  "$SYSTEM_PYTHON" - "$ALLOWED_SIGNERS" "$SIGNING_PRINCIPAL" \
    "$SIGNER_UID" "$SIGNER_GID" "$SIGNER_MODE" <<'PY'
import base64
import os
import stat
import struct
import sys

path, expected_principal, uid, gid, mode = sys.argv[1:]
try:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
except OSError as exc:
    raise SystemExit(f"pinned release signer cannot be opened safely: {exc}")
try:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != int(uid)
        or info.st_gid != int(gid)
        or stat.S_IMODE(info.st_mode) != int(mode, 8)
    ):
        raise SystemExit("pinned release signer metadata is unsafe")
    with os.fdopen(descriptor, encoding="ascii") as handle:
        descriptor = -1
        content = handle.read()
finally:
    if descriptor >= 0:
        os.close(descriptor)

if "\r" in content or not content.endswith("\n") or content.count("\n") != 1:
    raise SystemExit("pinned release signer must contain exactly one canonical line")
line = content[:-1]
parts = line.split(" ")
if len(parts) != 3 or any(not part for part in parts):
    raise SystemExit("pinned release signer has an invalid allowed-signers shape")
principal, key_type, key_material = parts
if principal != expected_principal:
    raise SystemExit("pinned release signer principal does not match release policy")
if key_type not in {"ssh-ed25519", "sk-ssh-ed25519@openssh.com"}:
    raise SystemExit("pinned release signer key type is not allowed")
try:
    decoded = base64.b64decode(key_material, validate=True)
    name_length = struct.unpack(">I", decoded[:4])[0]
    encoded_key_type = decoded[4 : 4 + name_length].decode("ascii")
except (ValueError, UnicodeDecodeError, struct.error):
    raise SystemExit("pinned release signer key material is invalid") from None
if encoded_key_type != key_type:
    raise SystemExit("pinned release signer key material has the wrong type")
PY
}

verify_target_signature() {
  local target="$1" author_email=""
  validate_allowed_signers \
    || fail "pinned release signer failed its integrity check"
  [ -x "$SSH_KEYGEN" ] \
    || fail "trusted SSH signature verifier is missing: $SSH_KEYGEN"
  author_email=$(as_service git -C "$APP_DIR" show -s --format=%ae "$target") \
    || fail "target commit author could not be resolved"
  [ "$author_email" = "$SIGNING_PRINCIPAL" ] \
    || fail "target commit author is not the pinned release principal: ${author_email:-unknown}"
  if ! as_service git -C "$APP_DIR" \
      -c gpg.format=ssh \
      -c "gpg.ssh.allowedSignersFile=$ALLOWED_SIGNERS" \
      -c "gpg.ssh.program=$SSH_KEYGEN" \
      verify-commit "$target"; then
    fail "target commit does not carry a valid pinned SSH signature: $target"
  fi
}

wait_for_post_gate_admission() {
  local deadline=$((SECONDS + ADMISSION_WAIT_SECONDS)) status=0
  while true; do
    status=0
    SEICHE_DEPLOY_ADMISSION_ONLY=1 "$DEPLOY_WRAPPER" \
      || status=$?
    case "$status" in
      0) return 0 ;;
      75)
        if (( SECONDS >= deadline )); then
          return 75
        fi
        printf 'release poll: shared host still busy after the full gate; retrying admission in %ss\n' \
          "$ADMISSION_RETRY_SECONDS"
        "$SLEEP" "$ADMISSION_RETRY_SECONDS" || return 1
        ;;
      *) return "$status" ;;
    esac
  done
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [ -n "$HEALTH_BODY" ]; then
    rm -f -- "$HEALTH_BODY" || true
  fi
  if [ -n "$CANDIDATE_ADDED" ]; then
    if ! as_service git -C "$APP_DIR" worktree remove --force "$CANDIDATE_DIR"; then
      echo "FAIL: candidate worktree cleanup failed: $CANDIDATE_DIR" >&2
      [ "$status" -ne 0 ] || status=1
    fi
    as_service git -C "$APP_DIR" worktree prune || true
  fi
  exit "$status"
}

# Regression tests source only the signature boundary. This cannot authorize a
# release: the production unit never sets it, and the mode exits before any
# candidate, receipt, wrapper, or checkout mutation exists.
if [ "${SEICHE_CONTROL_LIBRARY_ONLY:-0}" = 1 ]; then
  [ "${BASH_SOURCE[0]}" != "$0" ] \
    || fail "library-only mode is valid only when the poller is sourced"
  return 0
fi
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$(id -u)" -ne 0 ]; then
  fail "release polling must run as root"
fi
case "$GATE_ONLY" in
  0|1) ;;
  *) fail "SEICHE_CONTROL_GATE_ONLY must be exactly 0 or 1" ;;
esac
if [[ ! "$ADMISSION_WAIT_SECONDS" =~ ^[0-9]+$ ]] \
    || (( ADMISSION_WAIT_SECONDS > 3600 )); then
  fail "SEICHE_CONTROL_ADMISSION_WAIT_SECONDS must be an integer from 0 to 3600"
fi
if [[ ! "$ADMISSION_RETRY_SECONDS" =~ ^[0-9]+$ ]] \
    || (( ADMISSION_RETRY_SECONDS < 1 || ADMISSION_RETRY_SECONDS > 300 )); then
  fail "SEICHE_CONTROL_ADMISSION_RETRY_SECONDS must be an integer from 1 to 300"
fi
for path in "$STATE_DIR" "$RECEIPT_DIR" "$CANDIDATE_PARENT" "$RUNTIME_DIR"; do
  if [ -L "$path" ] || { [ -e "$path" ] && [ ! -d "$path" ]; }; then
    fail "$path is not a real directory"
  fi
done
install -d -o root -g "$SERVICE_USER" -m 0750 "$STATE_DIR"
install -d -o root -g root -m 0700 "$RECEIPT_DIR" "$RUNTIME_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$CANDIDATE_PARENT"
if [ "$(stat -c '%U:%G:%a' "$RECEIPT_DIR")" != "root:root:700" ] \
    || [ "$(stat -c '%U:%G:%a' "$RUNTIME_DIR")" != "root:root:700" ] \
    || [ "$(stat -c '%U:%G:%a' "$CANDIDATE_PARENT")" \
      != "$SERVICE_USER:$SERVICE_USER:700" ]; then
  fail "control receipt/runtime/candidate directory permissions are unsafe"
fi
exec 8>"$CONTROL_LOCK"
chown root:root "$CONTROL_LOCK"
chmod 0600 "$CONTROL_LOCK"
if ! flock --nonblock 8; then
  echo "release poll coalesced: another candidate gate is active"
  exit 0
fi

[ -x "$DEPLOY_WRAPPER" ] || fail "deploy wrapper is missing or not executable: $DEPLOY_WRAPPER"
[ -d "$APP_DIR/.git" ] || fail "canonical checkout is missing: $APP_DIR"

if ! as_service git -C "$APP_DIR" fetch -q origin main; then
  fail "could not fetch trusted origin/main"
fi
TARGET=$(as_service git -C "$APP_DIR" rev-parse origin/main) \
  || fail "could not resolve origin/main"
if ! valid_sha "$TARGET" \
    || ! as_service git -C "$APP_DIR" rev-parse --verify --quiet \
      "$TARGET^{commit}" >/dev/null; then
  fail "origin/main is not a canonical local commit"
fi
if is_inert_automation_content_commit "$TARGET"; then
  echo "release poll: content-only desk commit is intentionally inert; production unchanged"
  exit 0
fi
verify_target_signature "$TARGET"

ADMISSION_STATUS=0
SEICHE_DEPLOY_ADMISSION_ONLY=1 "$DEPLOY_WRAPPER" \
  || ADMISSION_STATUS=$?
case "$ADMISSION_STATUS" in
  0) ;;
  75)
    echo "release poll: shared host busy; ${TARGET:0:7} deferred with production unchanged"
    exit 0
    ;;
  *) fail "shared-host admission preflight failed" ;;
esac

DEPLOYED=""
if [ -e "$DEPLOY_STATE" ] || [ -L "$DEPLOY_STATE" ]; then
  if [ -L "$DEPLOY_STATE" ] || [ ! -f "$DEPLOY_STATE" ] \
      || [ "$(stat -c '%U:%G:%a' "$DEPLOY_STATE")" != "root:root:600" ] \
      || ! IFS= read -r DEPLOYED <"$DEPLOY_STATE" \
      || ! valid_sha "$DEPLOYED"; then
    fail "deployed release state is unsafe or invalid"
  fi
fi

health_matches() {
  local expected="$1"
  HEALTH_BODY=$(mktemp "$RUNTIME_DIR/.release-health.XXXXXX") || return 1
  if ! "$SYSTEMCTL" is-active --quiet seiche-api \
      || ! "$CURL" -sf -m 20 \
        http://127.0.0.1:8787/api/internal/v1/release-health >"$HEALTH_BODY" \
      || ! "$SYSTEM_PYTHON" - "$HEALTH_BODY" "$expected" <<'PY'
import json
import re
import sys

try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    assert isinstance(payload, dict)
    candidate = payload.get("release_candidate")
    assert re.fullmatch(r"[0-9a-f]{40}", sys.argv[2])
    assert isinstance(candidate, dict)
    assert set(candidate) == {"producer_sha", "activation_token"}
    assert candidate.get("producer_sha") == sys.argv[2]
    assert re.fullmatch(r"[0-9a-f]{64}", candidate.get("activation_token", ""))
except (AssertionError, OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
    raise SystemExit(1)
PY
  then
    rm -f -- "$HEALTH_BODY"
    HEALTH_BODY=""
    return 1
  fi
  rm -f -- "$HEALTH_BODY"
  HEALTH_BODY=""
  return 0
}

if [ "$GATE_ONLY" != 1 ] \
    && [ "$DEPLOYED" = "$TARGET" ] \
    && health_matches "$TARGET"; then
  echo "release poll: ${TARGET:0:7} is already deployed and strictly healthy"
  exit 0
fi

# The candidate uses a detached worktree and its own venv, so ordinary relative
# writes cannot dirty the live checkout.  It receives no production
# EnvironmentFile.  It intentionally shares the checkout's Unix identity for
# read-only Git access, however, so this is process isolation, not a security
# sandbox: only commits authorized by the host-pinned release key may reach it.
if [ -L "$CANDIDATE_DIR" ]; then
  fail "candidate path is an unsafe symlink: $CANDIDATE_DIR"
fi
as_service git -C "$APP_DIR" worktree prune
if [ -e "$CANDIDATE_DIR" ]; then
  as_service git -C "$APP_DIR" worktree remove --force "$CANDIDATE_DIR" \
    || fail "could not remove the stale candidate worktree"
fi
if ! as_service git -C "$APP_DIR" worktree add --detach "$CANDIDATE_DIR" "$TARGET"; then
  fail "could not create the detached candidate worktree"
fi
CANDIDATE_ADDED=1
CANDIDATE_SHA=$(as_service git -C "$CANDIDATE_DIR" rev-parse HEAD) \
  || fail "candidate identity could not be resolved"
[ "$CANDIDATE_SHA" = "$TARGET" ] || fail "candidate does not match the selected target"
CANDIDATE_TREE=$(as_service git -C "$CANDIDATE_DIR" rev-parse "HEAD^{tree}") \
  || fail "candidate tree identity could not be resolved"
valid_sha "$CANDIDATE_TREE" || fail "candidate tree identity is invalid"
as_service git -C "$CANDIDATE_DIR" diff-index --quiet "$TARGET" -- \
  || fail "candidate worktree is dirty before the gate"

VENV="$CANDIDATE_DIR/.gate-venv"
as_service "$TIMEOUT" -k 30 300 "$SYSTEM_PYTHON" -m venv "$VENV" \
  || fail "candidate virtualenv creation failed or timed out"
as_service "$TIMEOUT" -k 30 600 "$VENV/bin/python" -m pip install -q -e \
  "$CANDIDATE_DIR/backend[dev,collectors]" \
  || fail "candidate dependency install failed or timed out"
(
  cd "$CANDIDATE_DIR"
  as_service env \
    PATH="$VENV/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "$TIMEOUT" -k 30 3600 "$VENV/bin/python" -m pytest backend/tests -q \
      --memray --pystack-threshold=300
) || fail "candidate full test gate failed or timed out"
as_service git -C "$CANDIDATE_DIR" diff-index --quiet "$TARGET" -- \
  || fail "candidate tests modified tracked release files"

# Re-fetch after the expensive gate.  A newer tip is not an error and is never
# deployed by accident: discard this candidate and let the next timer gate the
# new identity from the beginning.
if ! as_service git -C "$APP_DIR" fetch -q origin main; then
  fail "could not re-fetch origin/main after the candidate gate"
fi
LATEST=$(as_service git -C "$APP_DIR" rev-parse origin/main) \
  || fail "could not re-resolve origin/main after the candidate gate"
valid_sha "$LATEST" || fail "origin/main became invalid after the candidate gate"
if [ "$LATEST" != "$TARGET" ]; then
  echo "release poll: tested ${TARGET:0:7} was superseded by ${LATEST:0:7}; production unchanged"
  exit 0
fi

write_receipt() {
  local kind="$1" path="$2" gate_digest="${3:-}" stage=""
  if [ -e "$path" ] || [ -L "$path" ]; then
    if [ -L "$path" ] || [ ! -f "$path" ] \
        || [ "$(stat -c '%U:%G:%a' "$path")" != "root:root:400" ]; then
      fail "$kind receipt is unsafe: $path"
    fi
    "$SYSTEM_PYTHON" - "$path" "$kind" "$TARGET" "$CANDIDATE_TREE" \
      "$INSTALL_COMMAND" "$TEST_COMMAND" "$gate_digest" <<'PY' \
      || fail "existing receipt does not bind this exact candidate"
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
expected = {
    "schema": "seiche.release-receipt.v1",
    "kind": sys.argv[2],
    "commit": sys.argv[3],
    "tree": sys.argv[4],
    "conclusion": "success",
}
for key, value in expected.items():
    assert payload.get(key) == value
if sys.argv[2] == "gate":
    assert payload.get("install_command") == sys.argv[5]
    assert payload.get("test_command") == sys.argv[6]
else:
    assert payload.get("gate_receipt_sha256") == sys.argv[7]
PY
    return 0
  fi
  stage=$(mktemp "$RECEIPT_DIR/.${TARGET}.${kind}.XXXXXX") \
    || fail "could not stage the $kind receipt"
  if ! "$SYSTEM_PYTHON" - "$kind" "$TARGET" "$CANDIDATE_TREE" \
      "$STARTED_AT" "$(date -u +%FT%TZ)" "$INSTALL_COMMAND" \
      "$TEST_COMMAND" "$gate_digest" \
      >"$stage" <<'PY'
import json
import sys

(
    kind,
    commit,
    tree,
    started_at,
    completed_at,
    install_command,
    test_command,
    gate_digest,
) = sys.argv[1:]
payload = {
    "schema": "seiche.release-receipt.v1",
    "kind": kind,
    "commit": commit,
    "tree": tree,
    "started_at": started_at,
    "completed_at": completed_at,
    "conclusion": "success",
}
if kind == "gate":
    payload["install_command"] = install_command
    payload["test_command"] = test_command
else:
    payload["gate_receipt_sha256"] = gate_digest
print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
PY
  then
    rm -f -- "$stage"
    fail "could not render the $kind receipt"
  fi
  # A hard link is an atomic no-clobber install because both names are on the
  # same state filesystem.  Unlike `mv -n`, it fails when a receipt appears
  # unexpectedly instead of silently reporting success without installing it.
  if ! chown root:root "$stage" || ! chmod 0400 "$stage" \
      || ! "$SYNC" -f "$stage" || ! ln "$stage" "$path" \
      || ! "$SYNC" "$RECEIPT_DIR" || ! rm -f -- "$stage" \
      || ! "$SYNC" "$RECEIPT_DIR"; then
    rm -f -- "$stage"
    fail "could not atomically install the $kind receipt"
  fi
}

GATE_RECEIPT="$RECEIPT_DIR/$TARGET.gate.json"
write_receipt gate "$GATE_RECEIPT"
GATE_DIGEST=$("$SHA256SUM" "$GATE_RECEIPT" | awk '{print $1}') \
  || fail "could not digest the candidate gate receipt"
[[ "$GATE_DIGEST" =~ ^[0-9a-f]{64}$ ]] || fail "candidate gate receipt digest is invalid"
if [ "$GATE_ONLY" = 1 ]; then
  echo "release poll: gate-only success for ${TARGET:0:7}; production unchanged"
  exit 0
fi

# The full memory-instrumented suite is allowed to consume the host capacity
# that the deploy wrapper deliberately reserves during checkout mutation and
# snapshot assembly. Let its one- and five-minute load windows cool without
# weakening that boundary. A bounded wait remains a normal deferral, while an
# admission-probe error is a real controller failure.
POST_GATE_ADMISSION_STATUS=0
wait_for_post_gate_admission || POST_GATE_ADMISSION_STATUS=$?
case "$POST_GATE_ADMISSION_STATUS" in
  0) ;;
  75)
    echo "release poll: shared host remained busy after the bounded post-gate wait; ${TARGET:0:7} deferred with production unchanged"
    exit 0
    ;;
  *) fail "shared-host post-gate admission failed" ;;
esac

# Waiting widens the branch-movement window. Re-fetch immediately before the
# wrapper handoff and keep the original rule: never deploy a tested candidate
# after a newer main tip has superseded it.
if ! as_service git -C "$APP_DIR" fetch -q origin main; then
  fail "could not re-fetch origin/main after post-gate admission"
fi
LATEST=$(as_service git -C "$APP_DIR" rev-parse origin/main) \
  || fail "could not re-resolve origin/main after post-gate admission"
valid_sha "$LATEST" || fail "origin/main became invalid after post-gate admission"
if [ "$LATEST" != "$TARGET" ]; then
  echo "release poll: tested ${TARGET:0:7} was superseded by ${LATEST:0:7} during post-gate admission; production unchanged"
  exit 0
fi

# The wrapper owns checkout mutation, service quiescence, exact-candidate
# readiness, snapshot promotion, Caddy convergence, and automatic rollback.
# A non-zero result remains non-zero even when its rollback recovered service.
DEPLOY_STATUS=0
SEICHE_EXPECTED_TARGET_SHA="$TARGET" "$DEPLOY_WRAPPER" \
  || DEPLOY_STATUS=$?
case "$DEPLOY_STATUS" in
  0) ;;
  75)
    echo "release poll: shared host became busy; ${TARGET:0:7} deferred with production unchanged"
    exit 0
    ;;
  *) fail "deploy wrapper rejected ${TARGET:0:7}; its rollback path owns recovery" ;;
esac
if ! IFS= read -r DEPLOYED_AFTER <"$DEPLOY_STATE" \
    || [ "$DEPLOYED_AFTER" != "$TARGET" ] \
    || ! health_matches "$TARGET"; then
  fail "deploy wrapper returned without an exact healthy deployed target"
fi

RELEASE_RECEIPT="$RECEIPT_DIR/$TARGET.release.json"
write_receipt release "$RELEASE_RECEIPT" "$GATE_DIGEST"
echo "release poll: gated and deployed ${TARGET:0:7} (receipts: $GATE_RECEIPT, $RELEASE_RECEIPT)"
