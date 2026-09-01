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
DEPLOY_WRAPPER="${SEICHE_CONTROL_DEPLOY_WRAPPER:-/var/lib/seiche-deploy/bin/seiche-deploy-wrapper.sh}"
REMOTE_GATE_VERIFIER="${SEICHE_CONTROL_REMOTE_GATE_VERIFIER:-/var/lib/seiche-deploy/bin/seiche-remote-gate-verify.py}"
REMOTE_SNAPSHOT_VERIFIER="${SEICHE_CONTROL_REMOTE_SNAPSHOT_VERIFIER:-/var/lib/seiche-deploy/bin/seiche-remote-snapshot-verify.py}"
RUNUSER=/usr/sbin/runuser
SYSTEMCTL="${SEICHE_CONTROL_SYSTEMCTL:-systemctl}"
CURL="${SEICHE_CONTROL_CURL:-curl}"
SYSTEM_PYTHON="${SEICHE_CONTROL_PYTHON:-python3}"
TIMEOUT="${SEICHE_CONTROL_TIMEOUT:-timeout}"
SLEEP="${SEICHE_CONTROL_SLEEP:-sleep}"
SYNC="${SEICHE_CONTROL_SYNC:-/usr/bin/sync}"
SHA256SUM="${SEICHE_CONTROL_SHA256SUM:-sha256sum}"
PS="${SEICHE_CONTROL_PS:-/bin/ps}"
KILL="${SEICHE_CONTROL_KILL:-/bin/kill}"
RECEIPT_UID="${SEICHE_CONTROL_RECEIPT_UID:-0}"
RECEIPT_GID="${SEICHE_CONTROL_RECEIPT_GID:-0}"
RECEIPT_MODE="${SEICHE_CONTROL_RECEIPT_MODE:-400}"
ALLOWED_SIGNERS="${SEICHE_CONTROL_ALLOWED_SIGNERS:-/etc/seiche-release.allowed-signers}"
SIGNING_PRINCIPAL="${SEICHE_CONTROL_SIGNING_PRINCIPAL:-beepboop2025@users.noreply.github.com}"
AUTOMATION_AUTHOR="${SEICHE_CONTROL_AUTOMATION_AUTHOR:-desk@seiche.info}"
SIGNER_UID="${SEICHE_CONTROL_SIGNER_UID:-0}"
SIGNER_GID="${SEICHE_CONTROL_SIGNER_GID:-0}"
SIGNER_MODE="${SEICHE_CONTROL_SIGNER_MODE:-444}"
SSH_KEYGEN="${SEICHE_CONTROL_SSH_KEYGEN:-/usr/bin/ssh-keygen}"
GATE_ONLY="${SEICHE_CONTROL_GATE_ONLY:-0}"
LOCAL_GATE_BREAK_GLASS="${SEICHE_CONTROL_LOCAL_GATE_BREAK_GLASS:-0}"
REMOTE_GATE_PENDING_MAX_SECONDS="${SEICHE_CONTROL_REMOTE_GATE_PENDING_MAX_SECONDS:-3600}"
ADMISSION_WAIT_SECONDS="${SEICHE_CONTROL_ADMISSION_WAIT_SECONDS:-900}"
ADMISSION_RETRY_SECONDS="${SEICHE_CONTROL_ADMISSION_RETRY_SECONDS:-30}"
SUPERSESSION_POLL_SECONDS="${SEICHE_CONTROL_SUPERSESSION_POLL_SECONDS:-15}"
SUPERSESSION_CHECK_TIMEOUT_SECONDS="${SEICHE_CONTROL_SUPERSESSION_CHECK_TIMEOUT_SECONDS:-30}"
RELEASE_TIMER_UNIT="${SEICHE_CONTROL_RELEASE_TIMER_UNIT:-seiche-release-poll.timer}"
INSTALL_COMMAND="python -m pip install -q -e ./backend[dev,collectors] && python -m pip install --disable-pip-version-check --only-binary=:all: --require-hashes -r ops/requirements-social-cards.txt"
REMOTE_GATE_INSTALL_COMMAND="python -m pip install -q ./backend[dev,collectors] && python -m pip install --disable-pip-version-check --only-binary=:all: --require-hashes -r ops/requirements-social-cards.txt"
TEST_COMMAND="python -m pytest backend/tests -q --memray -o faulthandler_timeout=300"
REMOTE_GATE_TEST_COMMAND="PYTHONPATH=/workspace/backend SEICHE_RUNTIME_DATA_DIR=/tmp/seiche-railway-gate-runtime/data SEICHE_VALIDATION_DIR=/tmp/seiche-railway-gate-runtime/data/market-validation python -P -m pytest backend/tests -q --memray -o faulthandler_timeout=300 -o cache_dir=/tmp/seiche-railway-gate-runtime/pytest-cache"
REMOTE_GATE_REPOSITORY="beepboop2025/seiche"
REMOTE_GATE_WORKFLOW="beepboop2025/seiche/.github/workflows/railway-release-gate.yml"
REMOTE_GATE_ARTIFACT_REPOSITORY="ghcr.io/beepboop2025/seiche-release-gates"
REMOTE_GATE_RUNNER_IMAGE="docker.io/library/python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7"
REMOTE_SNAPSHOT_REPOSITORY="beepboop2025/seiche"
REMOTE_SNAPSHOT_WORKFLOW="beepboop2025/seiche/.github/workflows/railway-snapshot-prebuild.yml"
REMOTE_SNAPSHOT_ARTIFACT_REPOSITORY="ghcr.io/beepboop2025/seiche-release-snapshots"
REMOTE_SNAPSHOT_RUNNER_IMAGE="docker.io/library/python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7"
STARTED_AT=$(date -u +%FT%TZ)
CANDIDATE_ADDED=""
HEALTH_BODY=""
RELEASE_TIMER_STATE_CAPTURED=0
RELEASE_TIMER_WAS_ENABLED=0
RELEASE_TIMER_WAS_ACTIVE=0
RELEASE_TIMER_RESTORE_REQUIRED=0
TARGET_DURABLY_DEPLOYED=0
DEPLOY_WRAPPER_HANDOFF_STARTED=0
GATE_PROCESS_PID=""
GATE_PROCESS_GROUP_READY=0
GATE_TICK_PID=""
GATE_SUPERSEDED_SHA=""
SNAPSHOT_ARTIFACT=""

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# A test-only source harness may replace runuser with a local executable. The
# production controller always uses the fixed Ubuntu util-linux path and fails
# closed if an ambient process attempts to override it.
if [ -n "${SEICHE_CONTROL_RUNUSER:-}" ]; then
  [ "${SEICHE_CONTROL_LIBRARY_ONLY:-0}" = 1 ] \
    || fail "SEICHE_CONTROL_RUNUSER is unavailable in production"
  case "$SEICHE_CONTROL_RUNUSER" in
    /*) ;;
    *) fail "test runuser override must be absolute" ;;
  esac
  [ -x "$SEICHE_CONTROL_RUNUSER" ] \
    || fail "test runuser override is not executable"
  RUNUSER=$SEICHE_CONTROL_RUNUSER
fi

valid_sha() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]]
}

as_service() {
  "$RUNUSER" -u "$SERVICE_USER" -- "$@"
}

resolve_advertised_main() {
  local advertised="" sha="" reference=""
  REMOTE_MAIN_SHA=""
  advertised=$(as_service "$TIMEOUT" -k 5 \
    "$SUPERSESSION_CHECK_TIMEOUT_SECONDS" \
    git -C "$APP_DIR" ls-remote --exit-code --refs \
    origin refs/heads/main) || return 1
  [[ "$advertised" != *$'\n'* ]] || return 1
  sha=${advertised%%$'\t'*}
  reference=${advertised#*$'\t'}
  valid_sha "$sha" || return 1
  [ "$reference" = refs/heads/main ] || return 1
  REMOTE_MAIN_SHA="$sha"
}

gate_process_group_is_ready() {
  local pid="$1" pgid=""
  pgid=$("$PS" -o pgid= -p "$pid" 2>/dev/null) || return 1
  pgid=${pgid//[[:space:]]/}
  [[ "$pgid" =~ ^[0-9]+$ ]] && [ "$pgid" = "$pid" ]
}

gate_process_is_running() {
  local pid="$1" state=""
  state=$("$PS" -o stat= -p "$pid" 2>/dev/null) || return 1
  state=${state//[[:space:]]/}
  [ -n "$state" ] && [[ "$state" != Z* ]]
}

start_candidate_gate_process() {
  local attempt=0
  GATE_PROCESS_PID=""
  GATE_PROCESS_GROUP_READY=0
  "$SYSTEM_PYTHON" -c '
import os
import sys

runner, service_user, *command = sys.argv[1:]
os.setsid()
os.execv(runner, [runner, "-u", service_user, "--", *command])
' "$RUNUSER" "$SERVICE_USER" "$@" &
  GATE_PROCESS_PID=$!

  # Never use a negative-PID signal until the controller has observed that the
  # exact child it started is the leader of its own process group.
  while (( attempt < 50 )); do
    if gate_process_group_is_ready "$GATE_PROCESS_PID"; then
      GATE_PROCESS_GROUP_READY=1
      return 0
    fi
    if ! "$KILL" -0 "$GATE_PROCESS_PID" 2>/dev/null; then
      return 0
    fi
    "$SLEEP" 0.1 || break
    attempt=$((attempt + 1))
  done
  "$KILL" -TERM "$GATE_PROCESS_PID" 2>/dev/null || true
  wait "$GATE_PROCESS_PID" 2>/dev/null || true
  GATE_PROCESS_PID=""
  return 1
}

stop_gate_tick() {
  [ -n "$GATE_TICK_PID" ] || return 0
  "$KILL" -TERM "$GATE_TICK_PID" 2>/dev/null || true
  wait "$GATE_TICK_PID" 2>/dev/null || true
  GATE_TICK_PID=""
}

terminate_candidate_gate_group() {
  local pid="$1" attempt=0
  "$KILL" -0 "$pid" 2>/dev/null || return 0
  if [ "$GATE_PROCESS_GROUP_READY" != 1 ] \
      || ! gate_process_group_is_ready "$pid"; then
    # A group identity mismatch must never widen the signal target. Signal only
    # the controller-owned leader and report failure instead.
    "$KILL" -TERM "$pid" 2>/dev/null || true
    return 1
  fi
  "$KILL" -TERM -- "-$pid" 2>/dev/null || {
    "$KILL" -0 "$pid" 2>/dev/null || return 0
    return 1
  }
  while (( attempt < 10 )); do
    gate_process_is_running "$pid" || break
    "$SLEEP" 1 || return 1
    attempt=$((attempt + 1))
  done
  if gate_process_is_running "$pid"; then
    "$KILL" -KILL -- "-$pid" 2>/dev/null || return 1
  fi
  # Kill any TERM-resistant child before reaping the leader, while its zombie
  # (or live PID) still anchors this exact controller-created process group.
  if "$KILL" -0 -- "-$pid" 2>/dev/null; then
    "$KILL" -KILL -- "-$pid" 2>/dev/null || return 1
  fi
  wait "$pid" 2>/dev/null || true
}

clear_candidate_gate_process() {
  GATE_PROCESS_PID=""
  GATE_PROCESS_GROUP_READY=0
}

run_monitored_candidate_step() {
  local gate_status=0 tick_status=0 monitor_status=0
  GATE_SUPERSEDED_SHA=""
  if ! resolve_advertised_main; then
    return 76
  fi
  if [ "$REMOTE_MAIN_SHA" != "$TARGET" ]; then
    GATE_SUPERSEDED_SHA="$REMOTE_MAIN_SHA"
    return 75
  fi
  if ! start_candidate_gate_process "$@"; then
    return 76
  fi
  if [ "$GATE_PROCESS_GROUP_READY" != 1 ]; then
    wait "$GATE_PROCESS_PID" || gate_status=$?
    clear_candidate_gate_process
    return "$gate_status"
  fi

  while "$KILL" -0 "$GATE_PROCESS_PID" 2>/dev/null; do
    "$SLEEP" "$SUPERSESSION_POLL_SECONDS" &
    GATE_TICK_PID=$!
    wait -n "$GATE_PROCESS_PID" "$GATE_TICK_PID" 2>/dev/null || true
    if ! "$KILL" -0 "$GATE_PROCESS_PID" 2>/dev/null; then
      stop_gate_tick
      break
    fi
    wait "$GATE_TICK_PID" 2>/dev/null || tick_status=$?
    GATE_TICK_PID=""
    if [ "$tick_status" -ne 0 ]; then
      monitor_status=76
      break
    fi
    if ! resolve_advertised_main; then
      monitor_status=76
      break
    fi
    if [ "$REMOTE_MAIN_SHA" != "$TARGET" ]; then
      GATE_SUPERSEDED_SHA="$REMOTE_MAIN_SHA"
      monitor_status=75
      break
    fi
  done

  if [ "$monitor_status" -ne 0 ]; then
    if ! terminate_candidate_gate_group "$GATE_PROCESS_PID"; then
      monitor_status=76
    fi
  fi
  wait "$GATE_PROCESS_PID" 2>/dev/null || gate_status=$?
  clear_candidate_gate_process
  [ "$monitor_status" -eq 0 ] || return "$monitor_status"
  return "$gate_status"
}

run_candidate_gate_stage() {
  local stage="$1" failure="$2" status=0
  shift 2
  run_monitored_candidate_step "$@" || status=$?
  case "$status" in
    0) return 0 ;;
    75)
      echo "release poll: ${TARGET:0:7} was superseded by ${GATE_SUPERSEDED_SHA:0:7} during $stage; production unchanged"
      exit 0
      ;;
    76) fail "origin/main supersession monitoring failed during $stage; production unchanged" ;;
    *) fail "$failure" ;;
  esac
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

run_deploy_wrapper() {
  local mode="$1" target="${2:-}"
  case "$mode" in
    admission)
      /usr/bin/env -i \
        HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
        SEICHE_DEPLOY_ADMISSION_ONLY=1 \
        /usr/bin/bash -p "$DEPLOY_WRAPPER"
      ;;
    deploy)
      valid_sha "$target" || fail "deploy-wrapper handoff target is invalid"
      /usr/bin/env -i \
        HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
        SEICHE_EXPECTED_TARGET_SHA="$target" \
        SEICHE_PREBUILT_SNAPSHOT_ARTIFACT="$SNAPSHOT_ARTIFACT" \
        /usr/bin/bash -p "$DEPLOY_WRAPPER"
      ;;
    *) fail "deploy-wrapper handoff mode is invalid" ;;
  esac
}

wait_for_post_gate_admission() {
  local deadline=$((SECONDS + ADMISSION_WAIT_SECONDS)) status=0
  while true; do
    status=0
    run_deploy_wrapper admission || status=$?
    case "$status" in
      0) return 0 ;;
      75)
        if (( SECONDS >= deadline )); then
          return 75
        fi
        printf 'release poll: shared host still busy after gate evidence acceptance; retrying admission in %ss\n' \
          "$ADMISSION_RETRY_SECONDS"
        "$SLEEP" "$ADMISSION_RETRY_SECONDS" || return 1
        ;;
      *) return "$status" ;;
    esac
  done
}

receipt_path_exists() {
  [ -e "$1" ] || [ -L "$1" ]
}

validate_receipt() {
  local path="$1" kind="$2" commit="$3" tree="$4" gate_digest="${5:-}"
  local snapshot_digest="${6:-}"
  "$SYSTEM_PYTHON" - "$path" "$kind" "$commit" "$tree" \
    "$INSTALL_COMMAND" "$REMOTE_GATE_INSTALL_COMMAND" "$TEST_COMMAND" \
    "$REMOTE_GATE_TEST_COMMAND" \
    "$gate_digest" "$snapshot_digest" \
    "$RECEIPT_UID" "$RECEIPT_GID" "$RECEIPT_MODE" \
    "$REMOTE_GATE_REPOSITORY" "$REMOTE_GATE_WORKFLOW" \
    "$REMOTE_GATE_ARTIFACT_REPOSITORY" "$REMOTE_GATE_RUNNER_IMAGE" <<'PY'
import json
import os
import re
import stat
import sys

(
    path,
    kind,
    commit,
    tree,
    install_command,
    remote_install_command,
    test_command,
    remote_test_command,
    gate_digest,
    snapshot_digest,
    expected_uid,
    expected_gid,
    expected_mode,
    remote_repository,
    remote_workflow,
    remote_artifact_repository,
    remote_runner_image,
) = sys.argv[1:]

descriptor = -1
try:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    info = os.fstat(descriptor)
    assert stat.S_ISREG(info.st_mode)
    assert info.st_nlink == 1
    assert info.st_uid == int(expected_uid)
    assert info.st_gid == int(expected_gid)
    assert stat.S_IMODE(info.st_mode) == int(expected_mode, 8)
    with os.fdopen(descriptor, encoding="utf-8") as handle:
        descriptor = -1
        payload = json.load(handle)

    assert kind in {"gate", "release"}
    assert re.fullmatch(r"[0-9a-f]{40}", commit)
    assert re.fullmatch(r"[0-9a-f]{40}", tree)
    common = {
        "kind": kind,
        "commit": commit,
        "tree": tree,
        "conclusion": "success",
    }
    for key, value in common.items():
        assert payload.get(key) == value
    timestamp = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
    assert timestamp.fullmatch(payload.get("started_at", ""))
    assert timestamp.fullmatch(payload.get("completed_at", ""))
    assert payload["started_at"] <= payload["completed_at"]
    schema = payload.get("schema")
    if schema == "seiche.release-receipt.v1":
        if kind == "gate":
            assert set(payload) == {
                "schema",
                "kind",
                "commit",
                "tree",
                "started_at",
                "completed_at",
                "conclusion",
                "install_command",
                "test_command",
            }
            assert payload["install_command"] == install_command
            assert payload["test_command"] == test_command
        else:
            assert set(payload) == {
                "schema",
                "kind",
                "commit",
                "tree",
                "started_at",
                "completed_at",
                "conclusion",
                "gate_receipt_sha256",
            }
            assert re.fullmatch(r"[0-9a-f]{64}", gate_digest)
            assert payload["gate_receipt_sha256"] == gate_digest
            assert not snapshot_digest
    elif schema == "seiche.release-receipt.v2" and kind == "release":
        assert set(payload) == {
            "schema",
            "kind",
            "commit",
            "tree",
            "started_at",
            "completed_at",
            "conclusion",
            "gate_receipt_sha256",
        }
        assert re.fullmatch(r"[0-9a-f]{64}", gate_digest)
        assert payload["gate_receipt_sha256"] == gate_digest
        assert not snapshot_digest
    elif schema == "seiche.release-receipt.v3" and kind == "release":
        assert set(payload) == {
            "schema",
            "kind",
            "commit",
            "tree",
            "started_at",
            "completed_at",
            "conclusion",
            "gate_receipt_sha256",
            "snapshot_receipt_sha256",
        }
        assert re.fullmatch(r"[0-9a-f]{64}", gate_digest)
        assert re.fullmatch(r"[0-9a-f]{64}", snapshot_digest)
        assert payload["gate_receipt_sha256"] == gate_digest
        assert payload["snapshot_receipt_sha256"] == snapshot_digest
    elif schema == "seiche.release-receipt.v2" and kind == "gate":
        provider = payload.get("gate_provider")
        assert provider in {"railway", "local-break-glass"}
        expected_keys = {
            "schema",
            "kind",
            "commit",
            "tree",
            "started_at",
            "completed_at",
            "conclusion",
            "gate_provider",
            "install_command",
            "test_command",
            "remote" if provider == "railway" else "break_glass",
        }
        assert set(payload) == expected_keys
        assert payload["install_command"] == (
            remote_install_command if provider == "railway" else install_command
        )
        assert payload["test_command"] == (
            remote_test_command if provider == "railway" else test_command
        )
        if provider == "local-break-glass":
            assert payload["break_glass"] == {
                "acknowledgement": "SEICHE_CONTROL_LOCAL_GATE_BREAK_GLASS=1"
            }
        else:
            remote = payload["remote"]
            assert isinstance(remote, dict)
            assert set(remote) == {
                "repository",
                "workflow",
                "source_ref",
                "artifact_repository",
                "artifact_digest",
                "artifact_receipt_sha256",
                "source_archive_sha256",
                "request_id",
                "runner_image",
                "python_version",
                "dependency_snapshot_sha256",
                "railway_deployment_id",
                "railway_project_id",
                "railway_environment_id",
                "railway_service_id",
                "railway_replica_region",
                "tests",
            }
            assert remote["repository"] == remote_repository
            assert remote["workflow"] == remote_workflow
            assert remote["source_ref"] == "refs/heads/main"
            assert remote["artifact_repository"] == remote_artifact_repository
            assert remote["runner_image"] == remote_runner_image
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", remote["artifact_digest"])
            for key in (
                "artifact_receipt_sha256",
                "source_archive_sha256",
                "request_id",
                "dependency_snapshot_sha256",
            ):
                assert re.fullmatch(r"[0-9a-f]{64}", remote[key])
            assert re.fullmatch(r"3\.12\.[0-9]+", remote["python_version"])
            uuid = re.compile(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}"
            )
            for key in (
                "railway_deployment_id",
                "railway_project_id",
                "railway_environment_id",
                "railway_service_id",
            ):
                assert uuid.fullmatch(remote[key])
            assert re.fullmatch(
                r"[a-z0-9][a-z0-9-]{0,63}", remote["railway_replica_region"]
            )
            tests = remote["tests"]
            assert isinstance(tests, dict)
            assert set(tests) == {
                "passed",
                "skipped",
                "subtests",
                "duration_seconds",
            }
            for key in ("passed", "skipped", "subtests"):
                assert type(tests[key]) is int and tests[key] >= 0
            assert tests["passed"] > 0
            assert type(tests["duration_seconds"]) in {int, float}
            assert 0 < tests["duration_seconds"] <= 3600
    else:
        raise AssertionError("unsupported receipt schema")
except (
    AssertionError,
    KeyError,
    OSError,
    UnicodeError,
    ValueError,
    TypeError,
    json.JSONDecodeError,
):
    raise SystemExit(1) from None
finally:
    if descriptor >= 0:
        os.close(descriptor)
PY
}

validate_gate_provider() {
  local path="$1" expected="$2"
  "$SYSTEM_PYTHON" - "$path" "$expected" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload.get("schema") == "seiche.release-receipt.v2"
    assert payload.get("kind") == "gate"
    assert payload.get("gate_provider") == sys.argv[2]
except (AssertionError, OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
    raise SystemExit(1) from None
PY
}

validate_snapshot_receipt() {
  local path="$1" commit="$2" tree="$3"
  "$SYSTEM_PYTHON" -I -B - "$path" "$commit" "$tree" \
    "$RECEIPT_UID" "$RECEIPT_GID" "$RECEIPT_MODE" \
    "$REMOTE_SNAPSHOT_REPOSITORY" "$REMOTE_SNAPSHOT_WORKFLOW" \
    "$REMOTE_SNAPSHOT_ARTIFACT_REPOSITORY" "$REMOTE_SNAPSHOT_RUNNER_IMAGE" <<'PY'
from datetime import datetime
import json
import os
import re
import stat
import sys

(
    path,
    commit,
    tree,
    expected_uid,
    expected_gid,
    expected_mode,
    repository,
    workflow,
    artifact_repository,
    runner_image,
) = sys.argv[1:]
descriptor = -1
try:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    info = os.fstat(descriptor)
    assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    assert info.st_uid == int(expected_uid) and info.st_gid == int(expected_gid)
    assert stat.S_IMODE(info.st_mode) == int(expected_mode, 8)
    with os.fdopen(descriptor, encoding="utf-8") as handle:
        descriptor = -1
        payload = json.load(handle)
    assert set(payload) == {
        "schema", "kind", "commit", "tree", "generated_at", "started_at",
        "completed_at", "conclusion", "snapshot_provider", "payload_sha256",
        "payload_size_bytes", "remote",
    }
    assert payload["schema"] == "seiche.remote-snapshot-receipt.v1"
    assert payload["kind"] == "snapshot-prebuild"
    assert payload["commit"] == commit and payload["tree"] == tree
    assert payload["conclusion"] == "success"
    assert payload["snapshot_provider"] == "railway"
    assert re.fullmatch(r"[0-9a-f]{64}", payload["payload_sha256"])
    assert type(payload["payload_size_bytes"]) is int
    assert 1 <= payload["payload_size_bytes"] <= 64 * 1024 * 1024
    generated = datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))
    started = datetime.fromisoformat(payload["started_at"].replace("Z", "+00:00"))
    completed = datetime.fromisoformat(payload["completed_at"].replace("Z", "+00:00"))
    assert generated.tzinfo and started.tzinfo and completed.tzinfo
    assert started <= generated <= completed
    remote = payload["remote"]
    assert set(remote) == {
        "repository", "workflow", "source_ref", "artifact_repository",
        "artifact_digest", "artifact_snapshot_sha256",
        "source_archive_sha256", "request_id", "runner_image",
        "python_version", "dependency_snapshot_sha256",
        "railway_deployment_id", "railway_project_id",
        "railway_environment_id", "railway_service_id",
        "railway_replica_region", "provenance_sha256", "provenance_count",
        "faults_sha256", "fault_count",
    }
    assert remote["repository"] == repository
    assert remote["workflow"] == workflow
    assert remote["source_ref"] == "refs/heads/main"
    assert remote["artifact_repository"] == artifact_repository
    assert remote["runner_image"] == runner_image
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", remote["artifact_digest"])
    for key in (
        "artifact_snapshot_sha256", "source_archive_sha256", "request_id",
        "dependency_snapshot_sha256", "provenance_sha256", "faults_sha256",
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", remote[key])
    assert re.fullmatch(r"3\.12\.[0-9]+", remote["python_version"])
    uuid = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
        r"[0-9a-f]{4}-[0-9a-f]{12}"
    )
    for key in (
        "railway_deployment_id", "railway_project_id",
        "railway_environment_id", "railway_service_id",
    ):
        assert uuid.fullmatch(remote[key])
    assert re.fullmatch(
        r"[a-z0-9][a-z0-9-]{0,63}", remote["railway_replica_region"]
    )
    assert type(remote["provenance_count"]) is int and remote["provenance_count"] >= 0
    assert type(remote["fault_count"]) is int and remote["fault_count"] >= 0
except (
    AssertionError, KeyError, OSError, UnicodeError, ValueError, TypeError,
    json.JSONDecodeError,
):
    raise SystemExit(1) from None
finally:
    if descriptor >= 0:
        os.close(descriptor)
PY
}

# Return 0 only for a complete, exact gate+snapshot+release evidence chain. A missing
# member returns 1 so the caller converges by running the full gate. Any
# existing but invalid member returns 2 and must fail closed.
receipt_pair_status() {
  local commit="$1" tree="$2" gate_receipt="$3" snapshot_receipt=""
  local release_receipt="" legacy_pair=0 gate_present=0 snapshot_present=0
  local release_present=0
  local gate_digest="" snapshot_digest=""
  if [ "$#" -eq 4 ]; then
    release_receipt="$4"
    legacy_pair=1
  else
    snapshot_receipt="$4"
    release_receipt="$5"
  fi
  receipt_path_exists "$gate_receipt" && gate_present=1
  if [ -n "$snapshot_receipt" ] && receipt_path_exists "$snapshot_receipt"; then
    snapshot_present=1
  fi
  receipt_path_exists "$release_receipt" && release_present=1

  if [ "$gate_present" = 0 ]; then
    [ "$snapshot_present" = 0 ] && [ "$release_present" = 0 ] && return 1
    return 2
  fi
  validate_receipt "$gate_receipt" gate "$commit" "$tree" || return 2
  if [ "$snapshot_present" = 0 ]; then
    [ "$release_present" = 0 ] && return 1
    if [ "$legacy_pair" != 1 ]; then
      validate_gate_provider "$gate_receipt" local-break-glass || return 2
    fi
    gate_digest=$("$SHA256SUM" "$gate_receipt" | awk '{print $1}') || return 2
    [[ "$gate_digest" =~ ^[0-9a-f]{64}$ ]] || return 2
    validate_receipt \
      "$release_receipt" release "$commit" "$tree" "$gate_digest" || return 2
    return 0
  fi
  validate_snapshot_receipt "$snapshot_receipt" "$commit" "$tree" || return 2
  [ "$release_present" = 1 ] || return 1
  gate_digest=$("$SHA256SUM" "$gate_receipt" | awk '{print $1}') || return 2
  snapshot_digest=$("$SHA256SUM" "$snapshot_receipt" | awk '{print $1}') || return 2
  [[ "$gate_digest" =~ ^[0-9a-f]{64}$ ]] || return 2
  [[ "$snapshot_digest" =~ ^[0-9a-f]{64}$ ]] || return 2
  validate_receipt \
    "$release_receipt" release "$commit" "$tree" \
    "$gate_digest" "$snapshot_digest" || return 2
  return 0
}

validate_recovery_receipt() {
  local path="$1" release_receipt="$2" commit="$3" tree="$4"
  "$SYSTEM_PYTHON" -I -B - \
    "$path" "$release_receipt" "$commit" "$tree" \
    "$RECEIPT_UID" "$RECEIPT_GID" "$RECEIPT_MODE" <<'PY'
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
import re
import stat
import sys


(
    path,
    release_path,
    commit,
    tree,
    expected_uid_raw,
    expected_gid_raw,
    expected_mode_raw,
) = sys.argv[1:]
expected_uid = int(expected_uid_raw)
expected_gid = int(expected_gid_raw)
expected_mode = int(expected_mode_raw, 8)
sha_re = re.compile(r"[0-9a-f]{40}")
digest_re = re.compile(r"[0-9a-f]{64}")
timestamp_re = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def read_exact(candidate: str, maximum: int) -> bytes:
    descriptor = os.open(
        candidate,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        visible = os.stat(candidate, follow_symlinks=False)
        assert stat.S_ISREG(before.st_mode)
        assert before.st_nlink == 1
        assert before.st_uid == expected_uid
        assert before.st_gid == expected_gid
        assert stat.S_IMODE(before.st_mode) == expected_mode
        assert 0 < before.st_size <= maximum
        assert stat.S_ISREG(visible.st_mode)
        assert (before.st_dev, before.st_ino) == (visible.st_dev, visible.st_ino)
        body = os.read(descriptor, maximum + 1)
        after = os.fstat(descriptor)
        assert len(body) <= maximum
        assert (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) == (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        return body
    finally:
        os.close(descriptor)


try:
    assert sha_re.fullmatch(commit)
    assert sha_re.fullmatch(tree)
    release_body = read_exact(release_path, 64 * 1024)
    release = json.loads(release_body)
    assert release_body == (
        json.dumps(release, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    assert release.get("kind") == "release"
    assert release.get("commit") == commit
    assert release.get("tree") == tree
    assert release.get("conclusion") == "success"
    assert release.get("schema") in {
        "seiche.release-receipt.v2",
        "seiche.release-receipt.v3",
    }
    release_digest = hashlib.sha256(release_body).hexdigest()

    body = read_exact(path, 64 * 1024)
    payload = json.loads(body)
    assert body == (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    assert set(payload) == {
        "schema",
        "kind",
        "commit",
        "tree",
        "release_receipt_sha256",
        "backup_snapshot",
        "backup_inventory_sha256",
        "restore_checked_at",
        "restore_receipt_sha256",
        "worker_startup",
        "data_readiness",
        "offsite_schedule",
        "completed_at",
        "conclusion",
    }
    assert payload["schema"] == "seiche.release-recovery-receipt.v1"
    assert payload["kind"] == "recovery"
    assert payload["commit"] == commit
    assert payload["tree"] == tree
    assert payload["release_receipt_sha256"] == release_digest
    assert re.fullmatch(r"20[0-9]{6}T[0-9]{6}Z", payload["backup_snapshot"])
    assert digest_re.fullmatch(payload["backup_inventory_sha256"])
    assert digest_re.fullmatch(payload["restore_receipt_sha256"])
    assert timestamp_re.fullmatch(payload["restore_checked_at"])
    assert timestamp_re.fullmatch(payload["completed_at"])
    assert payload["worker_startup"] == "ready"
    assert payload["data_readiness"] == "ready"
    assert payload["offsite_schedule"] in {"active", "disabled"}
    assert payload["conclusion"] == "success"
    release_completed = datetime.fromisoformat(
        release["completed_at"].replace("Z", "+00:00")
    ).astimezone(UTC)
    backup_created = datetime.strptime(
        payload["backup_snapshot"], "%Y%m%dT%H%M%SZ"
    ).replace(tzinfo=UTC)
    restore_checked = datetime.fromisoformat(
        payload["restore_checked_at"].replace("Z", "+00:00")
    ).astimezone(UTC)
    recovery_completed = datetime.fromisoformat(
        payload["completed_at"].replace("Z", "+00:00")
    ).astimezone(UTC)
    assert release_completed <= recovery_completed
    assert backup_created <= restore_checked <= recovery_completed
except (
    AssertionError,
    KeyError,
    OSError,
    TypeError,
    ValueError,
    UnicodeError,
    json.JSONDecodeError,
):
    raise SystemExit(1) from None
PY
}

recovery_receipt_status() {
  local path="$1" release_receipt="$2" commit="$3" tree="$4"
  if ! receipt_path_exists "$path"; then
    return 1
  fi
  validate_recovery_receipt "$path" "$release_receipt" "$commit" "$tree" \
    || return 2
  return 0
}

queue_recovery_seal() {
  "$SYSTEMCTL" reset-failed seiche-release-recovery-seal.service \
    2>/dev/null || true
  "$SYSTEMCTL" start --no-block seiche-release-recovery-seal.service
}

install_remote_gate_receipt() {
  local path="$1" stage="" verifier_status=0
  if receipt_path_exists "$path"; then
    validate_receipt "$path" gate "$TARGET" "$CANDIDATE_TREE" \
      || fail "existing gate receipt does not bind this exact candidate safely"
    validate_gate_provider "$path" railway \
      || fail "existing gate receipt is not attested Railway evidence"
    return 0
  fi
  stage=$(mktemp "$RECEIPT_DIR/.${TARGET}.remote-gate.XXXXXX") \
    || fail "could not stage the remote gate receipt"
  "$SYSTEM_PYTHON" -I -B "$REMOTE_GATE_VERIFIER" \
      --app "$APP_DIR" \
      --service-user "$SERVICE_USER" \
      --target "$TARGET" \
      --tree "$CANDIDATE_TREE" >"$stage" || verifier_status=$?
  if [ "$verifier_status" = 75 ]; then
    rm -f -- "$stage"
    return 75
  elif [ "$verifier_status" != 0 ]; then
    rm -f -- "$stage"
    fail "attested Railway gate verification failed; local gate was not run automatically"
  fi
  if ! chown root:root "$stage" || ! chmod 0400 "$stage" \
      || ! validate_receipt "$stage" gate "$TARGET" "$CANDIDATE_TREE" \
      || ! validate_gate_provider "$stage" railway \
      || ! "$SYNC" -f "$stage" || ! ln "$stage" "$path" \
      || ! "$SYNC" "$RECEIPT_DIR" || ! rm -f -- "$stage" \
      || ! "$SYNC" "$RECEIPT_DIR"; then
    rm -f -- "$stage"
    fail "could not atomically install the verified Railway gate receipt"
  fi
}

snapshot_artifact_matches_receipt() {
  local artifact="$1" receipt="$2"
  [ -f "$artifact" ] && [ ! -L "$artifact" ] \
    && [ "$(stat -c '%U:%G:%a:%h' "$artifact")" = root:root:600:1 ] \
    || return 1
  "$SYSTEM_PYTHON" -I -B - "$artifact" "$receipt" <<'PY'
import hashlib
import json
import os
import sys

artifact, receipt = sys.argv[1:]
digest = hashlib.sha256()
with open(artifact, "rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
with open(receipt, encoding="utf-8") as handle:
    expected = json.load(handle)["remote"]["artifact_snapshot_sha256"]
if digest.hexdigest() != expected:
    raise SystemExit(1)
PY
}

install_remote_snapshot_receipt() {
  local path="$1" artifact="$2" stage="" verifier_status=0
  if receipt_path_exists "$artifact"; then
    if [ -L "$artifact" ] || [ ! -f "$artifact" ] \
        || [ "$(stat -c '%U:%G:%a:%h' "$artifact")" != root:root:600:1 ]; then
      fail "stale snapshot artifact path is unsafe"
    fi
    rm -f -- "$artifact" || fail "stale snapshot artifact could not be cleared"
  fi
  stage=$(mktemp "$RECEIPT_DIR/.${TARGET}.remote-snapshot.XXXXXX") \
    || fail "could not stage the remote snapshot receipt"
  "$SYSTEM_PYTHON" -I -B "$REMOTE_SNAPSHOT_VERIFIER" \
      --app "$APP_DIR" \
      --service-user "$SERVICE_USER" \
      --target "$TARGET" \
      --tree "$CANDIDATE_TREE" \
      --artifact-output "$artifact" >"$stage" || verifier_status=$?
  if [ "$verifier_status" = 75 ]; then
    rm -f -- "$stage" "$artifact"
    return 75
  elif [ "$verifier_status" != 0 ]; then
    rm -f -- "$stage" "$artifact"
    fail "attested Railway snapshot verification failed; slow rebuild was not selected automatically"
  fi
  if ! chown root:root "$stage" || ! chmod 0400 "$stage" \
      || ! validate_snapshot_receipt "$stage" "$TARGET" "$CANDIDATE_TREE" \
      || ! snapshot_artifact_matches_receipt "$artifact" "$stage"; then
    rm -f -- "$stage" "$artifact"
    fail "verified Railway snapshot evidence is unsafe"
  fi
  if receipt_path_exists "$path"; then
    if ! validate_snapshot_receipt "$path" "$TARGET" "$CANDIDATE_TREE" \
        || ! cmp -s "$stage" "$path" \
        || ! snapshot_artifact_matches_receipt "$artifact" "$path"; then
      rm -f -- "$stage" "$artifact"
      fail "existing snapshot receipt differs from the current exact-SHA artifact"
    fi
    rm -f -- "$stage"
    return 0
  fi
  if ! "$SYNC" -f "$stage" || ! ln "$stage" "$path" \
      || ! "$SYNC" "$RECEIPT_DIR" || ! rm -f -- "$stage" \
      || ! "$SYNC" "$RECEIPT_DIR"; then
    rm -f -- "$stage" "$artifact"
    fail "could not atomically install the verified Railway snapshot receipt"
  fi
}

record_remote_gate_pending() {
  local path="$RECEIPT_DIR/$TARGET.remote-pending" stage="" now="" started="" age=""
  now=$(date -u +%s) || fail "could not read the remote gate pending clock"
  [[ "$now" =~ ^[0-9]+$ ]] || fail "remote gate pending clock is invalid"
  if receipt_path_exists "$path"; then
    started=$("$SYSTEM_PYTHON" -I -B - "$path" <<'PY'
import os
import re
import stat
import sys

descriptor = -1
try:
    descriptor = os.open(
        sys.argv[1],
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    info = os.fstat(descriptor)
    assert stat.S_ISREG(info.st_mode)
    assert info.st_nlink == 1
    assert info.st_uid == 0 and info.st_gid == 0
    assert stat.S_IMODE(info.st_mode) == 0o400
    with os.fdopen(descriptor, encoding="ascii") as handle:
        descriptor = -1
        value = handle.read()
    assert re.fullmatch(r"[0-9]+\n", value)
    print(value.strip())
except (AssertionError, OSError, UnicodeError, ValueError, TypeError):
    raise SystemExit(1) from None
finally:
    if descriptor >= 0:
        os.close(descriptor)
PY
    ) || fail "remote gate pending evidence is unsafe"
  else
    stage=$(mktemp "$RECEIPT_DIR/.${TARGET}.remote-pending.XXXXXX") \
      || fail "could not stage remote gate pending evidence"
    if ! printf '%s\n' "$now" >"$stage" \
        || ! chown root:root "$stage" || ! chmod 0400 "$stage" \
        || ! "$SYNC" -f "$stage" || ! ln "$stage" "$path" \
        || ! "$SYNC" "$RECEIPT_DIR" || ! rm -f -- "$stage" \
        || ! "$SYNC" "$RECEIPT_DIR"; then
      rm -f -- "$stage"
      fail "could not atomically record remote gate pending evidence"
    fi
    started="$now"
  fi
  [[ "$started" =~ ^[0-9]+$ ]] \
    || fail "remote gate pending start is invalid"
  (( started <= now )) || fail "remote gate pending start is in the future"
  age=$((now - started))
  if (( age >= REMOTE_GATE_PENDING_MAX_SECONDS )); then
    fail "attested Railway gate remained unpublished for ${age}s; local gate was not run automatically"
  fi
  printf 'release poll: Railway evidence pending for %ss (hard failure after %ss)\n' \
    "$age" "$REMOTE_GATE_PENDING_MAX_SECONDS"
}

record_remote_snapshot_pending() {
  local path="$RECEIPT_DIR/$TARGET.snapshot-pending" stage="" now="" started="" age=""
  now=$(date -u +%s) || fail "could not read the remote snapshot pending clock"
  [[ "$now" =~ ^[0-9]+$ ]] || fail "remote snapshot pending clock is invalid"
  if receipt_path_exists "$path"; then
    started=$("$SYSTEM_PYTHON" -I -B - "$path" <<'PY'
import os
import re
import stat
import sys

descriptor = os.open(
    sys.argv[1], os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
)
try:
    info = os.fstat(descriptor)
    assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    assert info.st_uid == 0 and info.st_gid == 0
    assert stat.S_IMODE(info.st_mode) == 0o400
    value = os.read(descriptor, 64).decode("ascii")
    assert re.fullmatch(r"[0-9]+\n", value)
    print(value.strip())
except (AssertionError, OSError, UnicodeError, ValueError):
    raise SystemExit(1) from None
finally:
    os.close(descriptor)
PY
    ) || fail "remote snapshot pending evidence is unsafe"
  else
    stage=$(mktemp "$RECEIPT_DIR/.${TARGET}.snapshot-pending.XXXXXX") \
      || fail "could not stage remote snapshot pending evidence"
    if ! printf '%s\n' "$now" >"$stage" \
        || ! chown root:root "$stage" || ! chmod 0400 "$stage" \
        || ! "$SYNC" -f "$stage" || ! ln "$stage" "$path" \
        || ! "$SYNC" "$RECEIPT_DIR" || ! rm -f -- "$stage" \
        || ! "$SYNC" "$RECEIPT_DIR"; then
      rm -f -- "$stage"
      fail "could not atomically record remote snapshot pending evidence"
    fi
    started="$now"
  fi
  [[ "$started" =~ ^[0-9]+$ ]] || fail "remote snapshot pending start is invalid"
  (( started <= now )) || fail "remote snapshot pending start is in the future"
  age=$((now - started))
  if (( age >= REMOTE_GATE_PENDING_MAX_SECONDS )); then
    fail "attested Railway snapshot remained unpublished for ${age}s; slow rebuild was not selected automatically"
  fi
  printf 'release poll: Railway snapshot pending for %ss (hard failure after %ss)\n' \
    "$age" "$REMOTE_GATE_PENDING_MAX_SECONDS"
}

capture_release_timer_state() {
  local enabled_state="" active_state=""
  [ "$RELEASE_TIMER_STATE_CAPTURED" = 0 ] || return 1
  enabled_state=$("$SYSTEMCTL" show \
    --property=UnitFileState --value "$RELEASE_TIMER_UNIT") || return 1
  case "$enabled_state" in
    enabled|enabled-runtime) RELEASE_TIMER_WAS_ENABLED=1 ;;
    disabled|disabled-runtime) RELEASE_TIMER_WAS_ENABLED=0 ;;
    *) return 1 ;;
  esac
  active_state=$("$SYSTEMCTL" show \
    --property=ActiveState --value "$RELEASE_TIMER_UNIT") || return 1
  case "$active_state" in
    active) RELEASE_TIMER_WAS_ACTIVE=1 ;;
    inactive) RELEASE_TIMER_WAS_ACTIVE=0 ;;
    *) return 1 ;;
  esac
  RELEASE_TIMER_STATE_CAPTURED=1
  RELEASE_TIMER_RESTORE_REQUIRED=1
}

release_timer_is_ready() {
  "$SYSTEMCTL" is-enabled --quiet "$RELEASE_TIMER_UNIT" \
    && "$SYSTEMCTL" is-active --quiet "$RELEASE_TIMER_UNIT"
}

ensure_release_timer_ready() {
  "$SYSTEMCTL" enable "$RELEASE_TIMER_UNIT" || return 1
  "$SYSTEMCTL" start "$RELEASE_TIMER_UNIT" || return 1
  release_timer_is_ready
}

activate_release_timer_for_deploy() {
  capture_release_timer_state || return 1
  ensure_release_timer_ready
}

restore_release_timer_state() {
  local failed=0
  [ "$RELEASE_TIMER_STATE_CAPTURED" = 1 ] || return 0
  [ "$RELEASE_TIMER_RESTORE_REQUIRED" = 1 ] || return 0
  if [ "$TARGET_DURABLY_DEPLOYED" = 1 ]; then
    ensure_release_timer_ready || return 1
    RELEASE_TIMER_RESTORE_REQUIRED=0
    return 0
  fi

  if [ "$RELEASE_TIMER_WAS_ACTIVE" = 1 ]; then
    "$SYSTEMCTL" start "$RELEASE_TIMER_UNIT" || failed=1
  else
    "$SYSTEMCTL" stop "$RELEASE_TIMER_UNIT" || failed=1
  fi
  if [ "$RELEASE_TIMER_WAS_ENABLED" = 1 ]; then
    "$SYSTEMCTL" enable "$RELEASE_TIMER_UNIT" || failed=1
  else
    "$SYSTEMCTL" disable "$RELEASE_TIMER_UNIT" || failed=1
  fi
  [ "$failed" = 0 ] || return 1
  RELEASE_TIMER_RESTORE_REQUIRED=0
}

load_deployed_state() {
  DEPLOYED_STATE_VALUE=""
  if [ ! -e "$DEPLOY_STATE" ] && [ ! -L "$DEPLOY_STATE" ]; then
    return 1
  fi
  if [ -L "$DEPLOY_STATE" ] || [ ! -f "$DEPLOY_STATE" ] \
      || [ "$(stat -c '%U:%G:%a:%h' "$DEPLOY_STATE")" != "root:root:600:1" ] \
      || ! IFS= read -r DEPLOYED_STATE_VALUE <"$DEPLOY_STATE" \
      || ! valid_sha "$DEPLOYED_STATE_VALUE"; then
    return 2
  fi
  return 0
}

cleanup() {
  local status=$? cleanup_deploy_state_status=0
  trap - EXIT INT TERM
  stop_gate_tick
  if [ -n "$GATE_PROCESS_PID" ]; then
    if ! terminate_candidate_gate_group "$GATE_PROCESS_PID"; then
      echo "FAIL: candidate gate process group cleanup failed" >&2
      [ "$status" -ne 0 ] || status=1
    fi
    wait "$GATE_PROCESS_PID" 2>/dev/null || true
    clear_candidate_gate_process
  fi
  if [ -n "$HEALTH_BODY" ]; then
    rm -f -- "$HEALTH_BODY" || true
  fi
  if [ -n "$SNAPSHOT_ARTIFACT" ]; then
    rm -f -- "$SNAPSHOT_ARTIFACT" || true
    SNAPSHOT_ARTIFACT=""
  fi
  if [ -n "$CANDIDATE_ADDED" ]; then
    if ! as_service git -C "$APP_DIR" worktree remove --force "$CANDIDATE_DIR"; then
      echo "FAIL: candidate worktree cleanup failed: $CANDIDATE_DIR" >&2
      [ "$status" -ne 0 ] || status=1
    fi
    as_service git -C "$APP_DIR" worktree prune || true
  fi
  # TERM/INT can skip the normal post-wrapper branch. Re-read the wrapper's
  # synced acceptance marker before rolling its timer dependency back; once
  # the exact target is durable, the active timer belongs to recovery.
  if [ "$RELEASE_TIMER_STATE_CAPTURED" = 1 ] \
      && [ "$RELEASE_TIMER_RESTORE_REQUIRED" = 1 ] \
      && [ "$DEPLOY_WRAPPER_HANDOFF_STARTED" = 1 ] \
      && [ "${DEPLOYED:-}" != "${TARGET:-}" ] \
      && valid_sha "${TARGET:-}"; then
    load_deployed_state || cleanup_deploy_state_status=$?
    if [ "$cleanup_deploy_state_status" = 0 ] \
        && [ "$DEPLOYED_STATE_VALUE" = "$TARGET" ]; then
      TARGET_DURABLY_DEPLOYED=1
    fi
  fi
  if ! restore_release_timer_state; then
    echo "FAIL: prior release timer state could not be restored" >&2
    [ "$status" -ne 0 ] || status=1
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
case "$LOCAL_GATE_BREAK_GLASS" in
  0|1) ;;
  *) fail "SEICHE_CONTROL_LOCAL_GATE_BREAK_GLASS must be exactly 0 or 1" ;;
esac
if [ "$LOCAL_GATE_BREAK_GLASS" = 0 ]; then
  [ -x "$REMOTE_GATE_VERIFIER" ] \
    || fail "remote Railway gate verifier is missing or not executable: $REMOTE_GATE_VERIFIER"
  [ -x "$REMOTE_SNAPSHOT_VERIFIER" ] \
    || fail "remote Railway snapshot verifier is missing or not executable: $REMOTE_SNAPSHOT_VERIFIER"
fi
if [[ ! "$ADMISSION_WAIT_SECONDS" =~ ^[0-9]+$ ]] \
    || (( ADMISSION_WAIT_SECONDS > 3600 )); then
  fail "SEICHE_CONTROL_ADMISSION_WAIT_SECONDS must be an integer from 0 to 3600"
fi
if [[ ! "$REMOTE_GATE_PENDING_MAX_SECONDS" =~ ^[0-9]+$ ]] \
    || (( REMOTE_GATE_PENDING_MAX_SECONDS < 300 \
      || REMOTE_GATE_PENDING_MAX_SECONDS > 86400 )); then
  fail "SEICHE_CONTROL_REMOTE_GATE_PENDING_MAX_SECONDS must be an integer from 300 to 86400"
fi
if [[ ! "$ADMISSION_RETRY_SECONDS" =~ ^[0-9]+$ ]] \
    || (( ADMISSION_RETRY_SECONDS < 1 || ADMISSION_RETRY_SECONDS > 300 )); then
  fail "SEICHE_CONTROL_ADMISSION_RETRY_SECONDS must be an integer from 1 to 300"
fi
if [[ ! "$SUPERSESSION_POLL_SECONDS" =~ ^[0-9]+$ ]] \
    || (( SUPERSESSION_POLL_SECONDS < 1 || SUPERSESSION_POLL_SECONDS > 300 )); then
  fail "SEICHE_CONTROL_SUPERSESSION_POLL_SECONDS must be an integer from 1 to 300"
fi
if [[ ! "$SUPERSESSION_CHECK_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] \
    || (( SUPERSESSION_CHECK_TIMEOUT_SECONDS < 1 \
      || SUPERSESSION_CHECK_TIMEOUT_SECONDS > 120 )); then
  fail "SEICHE_CONTROL_SUPERSESSION_CHECK_TIMEOUT_SECONDS must be an integer from 1 to 120"
fi
if [[ ! "$RECEIPT_UID" =~ ^[0-9]+$ ]] \
    || [[ ! "$RECEIPT_GID" =~ ^[0-9]+$ ]] \
    || [[ ! "$RECEIPT_MODE" =~ ^[0-7]{3,4}$ ]]; then
  fail "release receipt ownership policy is invalid"
fi
[ -x "$PS" ] || fail "trusted process-group inspector is missing: $PS"
[ -x "$KILL" ] || fail "trusted process-group signaler is missing: $KILL"
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
TARGET_TREE=$(as_service git -C "$APP_DIR" rev-parse "$TARGET^{tree}") \
  || fail "target tree identity could not be resolved"
valid_sha "$TARGET_TREE" || fail "target tree identity is invalid"
GATE_RECEIPT="$RECEIPT_DIR/$TARGET.gate.json"
SNAPSHOT_RECEIPT="$RECEIPT_DIR/$TARGET.snapshot.json"
RELEASE_RECEIPT="$RECEIPT_DIR/$TARGET.release.json"
RECOVERY_RECEIPT="$RECEIPT_DIR/$TARGET.recovery.json"
RECEIPT_PAIR_STATUS=0
receipt_pair_status \
  "$TARGET" "$TARGET_TREE" "$GATE_RECEIPT" "$SNAPSHOT_RECEIPT" \
  "$RELEASE_RECEIPT" \
  || RECEIPT_PAIR_STATUS=$?
case "$RECEIPT_PAIR_STATUS" in
  0|1) ;;
  *) fail "existing release receipt evidence is invalid for $TARGET" ;;
esac

# The explicit local break-glass gate still needs a quiet shared host before it
# consumes CPU. Remote evidence verification is lightweight and may proceed
# while the host is busy; the rollback-owning wrapper retains its own admission
# check before any production mutation.
if [ "$LOCAL_GATE_BREAK_GLASS" = 1 ]; then
  ADMISSION_STATUS=0
  run_deploy_wrapper admission || ADMISSION_STATUS=$?
  case "$ADMISSION_STATUS" in
    0) ;;
    75)
      echo "release poll: shared host busy; ${TARGET:0:7} local break-glass gate deferred with production unchanged"
      exit 0
      ;;
    *) fail "shared-host admission preflight failed" ;;
  esac
fi

DEPLOYED=""
DEPLOY_STATE_STATUS=0
load_deployed_state || DEPLOY_STATE_STATUS=$?
case "$DEPLOY_STATE_STATUS" in
  0) DEPLOYED="$DEPLOYED_STATE_VALUE" ;;
  1) ;;
  *) fail "deployed release state is unsafe or invalid" ;;
esac

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
    && [ "$RECEIPT_PAIR_STATUS" = 0 ] \
    && [ "$DEPLOYED" = "$TARGET" ] \
    && health_matches "$TARGET"; then
  RECOVERY_RECEIPT_STATUS=0
  recovery_receipt_status \
    "$RECOVERY_RECEIPT" "$RELEASE_RECEIPT" "$TARGET" "$TARGET_TREE" \
    || RECOVERY_RECEIPT_STATUS=$?
  case "$RECOVERY_RECEIPT_STATUS" in
    0)
      echo "release poll: ${TARGET:0:7} is live, strictly healthy, and recovery sealed"
      ;;
    1)
      queue_recovery_seal \
        || fail "live release recovery sealing could not be queued"
      echo "release poll: ${TARGET:0:7} live cutover is complete; recovery sealing continues asynchronously"
      ;;
    *) fail "existing recovery receipt evidence is invalid for $TARGET" ;;
  esac
  exit 0
fi

CANDIDATE_TREE="$TARGET_TREE"
if [ "$LOCAL_GATE_BREAK_GLASS" = 1 ]; then
  echo "release poll: explicit local gate break-glass selected for ${TARGET:0:7}"
  # The break-glass candidate uses a detached worktree and its own venv, so
  # ordinary relative writes cannot dirty the live checkout. It receives no
  # production EnvironmentFile. This path is never an automatic fallback for
  # absent, invalid, or unavailable Railway evidence.
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
  [ "$CANDIDATE_SHA" = "$TARGET" ] \
    || fail "candidate does not match the selected target"
  CANDIDATE_TREE=$(as_service git -C "$CANDIDATE_DIR" rev-parse "HEAD^{tree}") \
    || fail "candidate tree identity could not be resolved"
  valid_sha "$CANDIDATE_TREE" || fail "candidate tree identity is invalid"
  [ "$CANDIDATE_TREE" = "$TARGET_TREE" ] \
    || fail "candidate tree does not match the selected target tree"
  as_service git -C "$CANDIDATE_DIR" diff-index --quiet "$TARGET" -- \
    || fail "candidate worktree is dirty before the gate"

  VENV="$CANDIDATE_DIR/.gate-venv"
  run_candidate_gate_stage \
    "candidate virtualenv creation" \
    "candidate virtualenv creation failed or timed out" \
    "$TIMEOUT" -k 30 300 "$SYSTEM_PYTHON" -m venv "$VENV"
  run_candidate_gate_stage \
    "candidate dependency installation" \
    "candidate dependency install failed or timed out" \
    "$TIMEOUT" -k 30 600 "$VENV/bin/python" -m pip install -q -e \
    "$CANDIDATE_DIR/backend[dev,collectors]"
  run_candidate_gate_stage \
    "candidate social-card test dependency installation" \
    "candidate social-card dependency install failed or timed out" \
    "$TIMEOUT" -k 30 300 "$VENV/bin/python" -m pip install \
    --disable-pip-version-check --only-binary=:all: --require-hashes \
    -r "$CANDIDATE_DIR/ops/requirements-social-cards.txt"
  # The candidate shell receives its values only through positional arguments.
  # shellcheck disable=SC2016
  run_candidate_gate_stage \
    "candidate full test gate" \
    "candidate full test gate failed or timed out" \
    /bin/bash -c '
candidate=$1
gate_path=$2
shift 2
cd "$candidate" || exit 125
PATH=$gate_path exec "$@"
' seiche-candidate-gate \
    "$CANDIDATE_DIR" \
    "$VENV/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "$TIMEOUT" -k 30 3600 "$VENV/bin/python" -m pytest backend/tests -q \
    --memray -o faulthandler_timeout=300
  as_service git -C "$CANDIDATE_DIR" diff-index --quiet "$TARGET" -- \
    || fail "candidate tests modified tracked release files"
else
  REMOTE_GATE_STATUS=0
  install_remote_gate_receipt "$GATE_RECEIPT" || REMOTE_GATE_STATUS=$?
  if [ "$REMOTE_GATE_STATUS" = 75 ]; then
    record_remote_gate_pending
    echo "release poll: attested Railway gate for ${TARGET:0:7} is still pending; production unchanged"
    exit 0
  elif [ "$REMOTE_GATE_STATUS" != 0 ]; then
    fail "unexpected remote gate verifier status: $REMOTE_GATE_STATUS"
  fi
  echo "release poll: verified attested Railway gate for ${TARGET:0:7}"
fi

# Re-fetch after either gate path. A newer tip is not an error and is never
# deployed by accident: discard this candidate and let the next timer verify
# the new identity from the beginning.
if ! as_service git -C "$APP_DIR" fetch -q origin main; then
  fail "could not re-fetch origin/main after gate verification"
fi
LATEST=$(as_service git -C "$APP_DIR" rev-parse origin/main) \
  || fail "could not re-resolve origin/main after gate verification"
valid_sha "$LATEST" || fail "origin/main became invalid after gate verification"
if [ "$LATEST" != "$TARGET" ]; then
  echo "release poll: tested ${TARGET:0:7} was superseded by ${LATEST:0:7}; production unchanged"
  exit 0
fi

write_receipt() {
  local kind="$1" path="$2" gate_digest="${3:-}" snapshot_digest="${4:-}" stage=""
  if receipt_path_exists "$path"; then
    validate_receipt "$path" "$kind" "$TARGET" "$CANDIDATE_TREE" \
      "$gate_digest" "$snapshot_digest" \
      || fail "existing $kind receipt does not bind this exact candidate safely"
    if [ "$kind" = gate ]; then
      validate_gate_provider "$path" local-break-glass \
        || fail "existing gate receipt is not the selected local break-glass evidence"
    fi
    return 0
  fi
  stage=$(mktemp "$RECEIPT_DIR/.${TARGET}.${kind}.XXXXXX") \
    || fail "could not stage the $kind receipt"
  if ! "$SYSTEM_PYTHON" - "$kind" "$TARGET" "$CANDIDATE_TREE" \
      "$STARTED_AT" "$(date -u +%FT%TZ)" "$INSTALL_COMMAND" \
      "$TEST_COMMAND" "$gate_digest" "$snapshot_digest" \
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
    snapshot_digest,
) = sys.argv[1:]
payload = {
    "schema": (
        "seiche.release-receipt.v3"
        if kind == "release" and snapshot_digest
        else "seiche.release-receipt.v2"
    ),
    "kind": kind,
    "commit": commit,
    "tree": tree,
    "started_at": started_at,
    "completed_at": completed_at,
    "conclusion": "success",
}
if kind == "gate":
    payload["gate_provider"] = "local-break-glass"
    payload["install_command"] = install_command
    payload["test_command"] = test_command
    payload["break_glass"] = {
        "acknowledgement": "SEICHE_CONTROL_LOCAL_GATE_BREAK_GLASS=1"
    }
else:
    payload["gate_receipt_sha256"] = gate_digest
    if snapshot_digest:
        payload["snapshot_receipt_sha256"] = snapshot_digest
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
      || ! validate_receipt "$stage" "$kind" "$TARGET" "$CANDIDATE_TREE" \
        "$gate_digest" "$snapshot_digest" \
      || ! "$SYNC" -f "$stage" || ! ln "$stage" "$path" \
      || ! "$SYNC" "$RECEIPT_DIR" || ! rm -f -- "$stage" \
      || ! "$SYNC" "$RECEIPT_DIR"; then
    rm -f -- "$stage"
    fail "could not atomically install the $kind receipt"
  fi
}

if [ "$LOCAL_GATE_BREAK_GLASS" = 1 ]; then
  write_receipt gate "$GATE_RECEIPT"
else
  validate_receipt "$GATE_RECEIPT" gate "$TARGET" "$CANDIDATE_TREE" \
    || fail "verified Railway gate receipt changed before deployment"
  validate_gate_provider "$GATE_RECEIPT" railway \
    || fail "verified gate receipt lost its Railway provider identity"
fi
GATE_DIGEST=$("$SHA256SUM" "$GATE_RECEIPT" | awk '{print $1}') \
  || fail "could not digest the candidate gate receipt"
[[ "$GATE_DIGEST" =~ ^[0-9a-f]{64}$ ]] || fail "candidate gate receipt digest is invalid"
if [ "$GATE_ONLY" = 1 ]; then
  if [ "$LOCAL_GATE_BREAK_GLASS" = 1 ]; then
    echo "release poll: local break-glass gate-only success for ${TARGET:0:7}; production unchanged"
  else
    echo "release poll: attested Railway gate-only success for ${TARGET:0:7}; production unchanged"
  fi
  exit 0
fi

SNAPSHOT_DIGEST=""
if [ "$LOCAL_GATE_BREAK_GLASS" = 0 ]; then
  SNAPSHOT_ARTIFACT="$RUNTIME_DIR/$TARGET.snapshot.json"
  REMOTE_SNAPSHOT_STATUS=0
  install_remote_snapshot_receipt \
    "$SNAPSHOT_RECEIPT" "$SNAPSHOT_ARTIFACT" || REMOTE_SNAPSHOT_STATUS=$?
  if [ "$REMOTE_SNAPSHOT_STATUS" = 75 ]; then
    record_remote_snapshot_pending
    echo "release poll: attested Railway snapshot for ${TARGET:0:7} is still pending; production unchanged"
    exit 0
  elif [ "$REMOTE_SNAPSHOT_STATUS" != 0 ]; then
    fail "unexpected remote snapshot verifier status: $REMOTE_SNAPSHOT_STATUS"
  fi
  validate_snapshot_receipt \
    "$SNAPSHOT_RECEIPT" "$TARGET" "$CANDIDATE_TREE" \
    || fail "verified Railway snapshot receipt changed before deployment"
  snapshot_artifact_matches_receipt \
    "$SNAPSHOT_ARTIFACT" "$SNAPSHOT_RECEIPT" \
    || fail "verified Railway snapshot artifact changed before deployment"
  SNAPSHOT_DIGEST=$("$SHA256SUM" "$SNAPSHOT_RECEIPT" | awk '{print $1}') \
    || fail "could not digest the candidate snapshot receipt"
  [[ "$SNAPSHOT_DIGEST" =~ ^[0-9a-f]{64}$ ]] \
    || fail "candidate snapshot receipt digest is invalid"
  echo "release poll: verified attested Railway prebuild for ${TARGET:0:7}"
else
  echo "release poll: local break-glass will use the bounded on-host rebuild path"
fi

# The deploy wrapper still requires a quiet host before checkout mutation and
# snapshot assembly. Remote evidence normally reaches this check immediately;
# an explicit local break-glass gate may need its one- and five-minute load
# windows to cool. A bounded wait remains a normal deferral, while an admission
# probe error is a real controller failure.
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
activate_release_timer_for_deploy \
  || fail "release timer could not be made enabled and active before deployment"
DEPLOY_STATUS=0
DEPLOY_WRAPPER_HANDOFF_STARTED=1
run_deploy_wrapper deploy "$TARGET" || DEPLOY_STATUS=$?
DEPLOY_STATE_STATUS=0
load_deployed_state || DEPLOY_STATE_STATUS=$?
case "$DEPLOY_STATE_STATUS" in
  0)
    DEPLOYED_AFTER="$DEPLOYED_STATE_VALUE"
    if [ "$DEPLOYED_AFTER" = "$TARGET" ]; then
      TARGET_DURABLY_DEPLOYED=1
    fi
    ;;
  1) DEPLOYED_AFTER="" ;;
  *) fail "deploy wrapper left an unsafe deployed release state" ;;
esac
case "$DEPLOY_STATUS" in
  0) ;;
  75)
    echo "release poll: shared host became busy; ${TARGET:0:7} deferred with production unchanged"
    exit 0
    ;;
  *) fail "deploy wrapper rejected ${TARGET:0:7}; its rollback path owns recovery" ;;
esac
if [ "$TARGET_DURABLY_DEPLOYED" != 1 ] \
    || ! health_matches "$TARGET"; then
  fail "deploy wrapper returned without an exact healthy deployed target"
fi
release_timer_is_ready \
  || fail "release timer is not enabled and active after deployment"

write_receipt release "$RELEASE_RECEIPT" "$GATE_DIGEST" "$SNAPSHOT_DIGEST"
RELEASE_TIMER_RESTORE_REQUIRED=0
queue_recovery_seal \
  || fail "live release recovery sealing could not be queued"
if [ "$LOCAL_GATE_BREAK_GLASS" = 1 ]; then
  echo "release poll: live cutover ${TARGET:0:7} complete; recovery sealing queued (receipts: $GATE_RECEIPT, break-glass, $RELEASE_RECEIPT)"
else
  echo "release poll: live cutover ${TARGET:0:7} complete; recovery sealing queued (receipts: $GATE_RECEIPT, $SNAPSHOT_RECEIPT, $RELEASE_RECEIPT)"
fi
