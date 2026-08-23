#!/bin/bash -p
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

# Every invocation crosses the same clean-shell boundary before parsing any
# release logic. authorized_keys supplies the public forced-entry marker; the
# poller/local bootstrap supplies no argument. Both become an internal mode
# argument after isolation. Test-only inputs remain explicit so host-free
# harnesses exercise the exact production entry path without ambient state.
SEICHE_DEPLOY_FORCED_MARKER=--seiche-forced-entry-v1
SEICHE_DEPLOY_ISOLATED_MARKER=--seiche-deploy-isolated-v1
if [ "$#" -eq 2 ] \
    && [ "$1" = "$SEICHE_DEPLOY_ISOLATED_MARKER" ] \
    && { [ "$2" = local ] || [ "$2" = forced ]; }; then
  SEICHE_DEPLOY_ENTRY_MODE=$2
  shift 2
else
  case "$#:${1-}" in
    0:)
      if [ -n "${SSH_CONNECTION-}" ] || [ -n "${SSH_CLIENT-}" ]; then
        echo "FAIL: SSH deployment entry is missing the forced-command marker" >&2
        exit 2
      fi
      SEICHE_DEPLOY_ENTRY_MODE=local
      ;;
    1:"$SEICHE_DEPLOY_FORCED_MARKER")
      SEICHE_DEPLOY_ENTRY_MODE=forced
      ;;
    *)
      echo "FAIL: deploy wrapper entry arguments are not authorized" >&2
      exit 2
      ;;
  esac
  if [ "$EUID" -eq 0 ]; then
    SEICHE_DEPLOY_ENTRY_HOME=/root
  else
    SEICHE_DEPLOY_ENTRY_HOME=/tmp
  fi
  SEICHE_DEPLOY_ENTRY_BASH=/usr/bin/bash
  if [ ! -x "$SEICHE_DEPLOY_ENTRY_BASH" ] \
      && [ "$EUID" -ne 0 ] && [ -x /bin/bash ]; then
    SEICHE_DEPLOY_ENTRY_BASH=/bin/bash
  fi
  [ -x "$SEICHE_DEPLOY_ENTRY_BASH" ] \
    || { echo "FAIL: trusted Bash is unavailable" >&2; exit 1; }
  exec /usr/bin/env -i \
    HOME="$SEICHE_DEPLOY_ENTRY_HOME" LANG=C LC_ALL=C PATH=/usr/bin:/bin \
    SSH_ORIGINAL_COMMAND="${SSH_ORIGINAL_COMMAND-}" \
    SEICHE_EXPECTED_TARGET_SHA="${SEICHE_EXPECTED_TARGET_SHA-}" \
    SEICHE_DEPLOY_ADMISSION_ONLY="${SEICHE_DEPLOY_ADMISSION_ONLY-}" \
    SEICHE_DEPLOY_BOOTSTRAP_ASSETS_ONLY="${SEICHE_DEPLOY_BOOTSTRAP_ASSETS_ONLY-}" \
    SEICHE_DATA_READINESS_CONVERGENCE_WAIT_SECONDS="${SEICHE_DATA_READINESS_CONVERGENCE_WAIT_SECONDS-}" \
    SEICHE_DEPLOY_ASSET_TEST_ONLY="${SEICHE_DEPLOY_ASSET_TEST_ONLY-}" \
    SEICHE_ALLOW_NON_ROOT_ASSET_TEST="${SEICHE_ALLOW_NON_ROOT_ASSET_TEST-}" \
    SEICHE_ASSET_TEST_REPO="${SEICHE_ASSET_TEST_REPO-}" \
    SEICHE_ASSET_TEST_TARGET="${SEICHE_ASSET_TEST_TARGET-}" \
    SEICHE_ASSET_TEST_PARENT="${SEICHE_ASSET_TEST_PARENT-}" \
    SEICHE_ASSET_TEST_DESTINATION="${SEICHE_ASSET_TEST_DESTINATION-}" \
    SEICHE_ASSET_TEST_PYTHON="${SEICHE_ASSET_TEST_PYTHON-}" \
    SEICHE_DEPLOY_BOOTSTRAP_TEST_ONLY="${SEICHE_DEPLOY_BOOTSTRAP_TEST_ONLY-}" \
    SEICHE_ALLOW_NON_ROOT_BOOTSTRAP_TEST="${SEICHE_ALLOW_NON_ROOT_BOOTSTRAP_TEST-}" \
    SEICHE_BOOTSTRAP_TEST_REPO="${SEICHE_BOOTSTRAP_TEST_REPO-}" \
    SEICHE_BOOTSTRAP_TEST_RUNTIME="${SEICHE_BOOTSTRAP_TEST_RUNTIME-}" \
    SEICHE_BOOTSTRAP_TEST_ALLOWED_SIGNERS="${SEICHE_BOOTSTRAP_TEST_ALLOWED_SIGNERS-}" \
    SEICHE_BOOTSTRAP_TEST_GIT_HOME="${SEICHE_BOOTSTRAP_TEST_GIT_HOME-}" \
    SEICHE_BOOTSTRAP_TEST_PYTHON="${SEICHE_BOOTSTRAP_TEST_PYTHON-}" \
    "$SEICHE_DEPLOY_ENTRY_BASH" -p "$0" \
      "$SEICHE_DEPLOY_ISOLATED_MARKER" "$SEICHE_DEPLOY_ENTRY_MODE"
fi
unset SEICHE_DEPLOY_ENTRY_BASH SEICHE_DEPLOY_ENTRY_HOME \
  SEICHE_DEPLOY_FORCED_MARKER SEICHE_DEPLOY_ISOLATED_MARKER
umask 077

RUNUSER=/usr/sbin/runuser
CANONICAL_DEPLOY_WRAPPER=/var/lib/seiche-deploy/bin/seiche-deploy-wrapper.sh
if [ "$SEICHE_DEPLOY_ENTRY_MODE" = forced ]; then
  CANONICAL_DEPLOY_PARENT=${CANONICAL_DEPLOY_WRAPPER%/*}
  if [ "$0" != "$CANONICAL_DEPLOY_WRAPPER" ] \
      || [ -L "$CANONICAL_DEPLOY_PARENT" ] \
      || [ -L "$CANONICAL_DEPLOY_WRAPPER" ] \
      || [ "$(/usr/bin/readlink -f -- "$0")" != "$CANONICAL_DEPLOY_WRAPPER" ] \
      || [ "$(/usr/bin/stat -c '%U:%G:%a:%F' "$CANONICAL_DEPLOY_PARENT")" \
        != root:root:700:directory ] \
      || [ "$(/usr/bin/stat -c '%U:%G:%a:%h:%F' "$CANONICAL_DEPLOY_WRAPPER")" \
        != 'root:root:700:1:regular file' ]; then
    echo "FAIL: forced deployment did not enter through the canonical root-owned wrapper" >&2
    exit 1
  fi
fi

materialize_privileged_release_assets() {
  local repo="$1" target="$2" parent="$3" destination="$4"
  local expected_uid="$5" expected_gid="$6" portable_test="${7:-0}"
  local materializer_python=/usr/bin/python3
  if [ "$portable_test" = 1 ]; then
    materializer_python="${8:?portable materializer Python is required}"
  fi
  "$materializer_python" -I -B - \
    "$repo" "$target" "$parent" "$destination" \
    "$expected_uid" "$expected_gid" "$portable_test" <<'PY'
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys


(
    repo_text,
    target,
    parent_text,
    destination_text,
    expected_uid_text,
    expected_gid_text,
    portable_test_text,
) = sys.argv[1:]

SHA_RE = re.compile(r"[0-9a-f]{40}")
PATH_RE = re.compile(r"[A-Za-z0-9._/-]{1,255}")
FINAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
RENAME_NOREPLACE = 1
REQUIRED_MODES = {
    "backend/seiche/__init__.py": "100644",
    "backend/seiche/nbs_intake.py": "100644",
    "backend/seiche/nbs_trust.py": "100644",
    "ops/Caddyfile": "100644",
    "ops/deploy/box-update.sh": "100644",
    "ops/deploy/install-caddy.sh": "100755",
    "ops/deploy/install-market-platform.sh": "100755",
    "ops/deploy/install-release-poller.sh": "100755",
    "ops/deploy/release-allowed-signers": "100644",
    "ops/deploy/retire-legacy-update-units.sh": "100644",
    "ops/deploy/seiche-data-readiness.service": "100644",
    "ops/deploy/seiche-data-readiness.sh": "100755",
    "ops/deploy/seiche-data-readiness.timer": "100644",
    "ops/deploy/seiche-deploy-wrapper.sh": "100644",
    "ops/deploy/seiche-market-backfill.service": "100644",
    "ops/deploy/seiche-market-backup.service": "100644",
    "ops/deploy/seiche-market-backup.sh": "100644",
    "ops/deploy/seiche-market-backup.timer": "100644",
    "ops/deploy/seiche-market-offsite-backup.service": "100644",
    "ops/deploy/seiche-market-offsite-backup.sh": "100755",
    "ops/deploy/seiche-market-offsite-backup.timer": "100644",
    "ops/deploy/seiche-market-restore-check.service": "100644",
    "ops/deploy/seiche-market-restore-check.sh": "100644",
    "ops/deploy/seiche-market-restore-check.timer": "100644",
    "ops/deploy/seiche-market-validation.service": "100644",
    "ops/deploy/seiche-market-validation.timer": "100644",
    "ops/deploy/seiche-market-worker.service": "100644",
    "ops/deploy/seiche-nbs-intake.py": "100644",
    "ops/deploy/seiche-pull.service": "100644",
    "ops/deploy/seiche-release-poll.service": "100644",
    "ops/deploy/seiche-release-poll.sh": "100755",
    "ops/deploy/seiche-release-poll.timer": "100644",
    "ops/deploy/seiche-remote-gate-verify.py": "100755",
    "ops/deploy/seiche-snapshot-promote.service": "100644",
    "ops/deploy/seiche-source-worker.service": "100644",
    "ops/deploy/seiche-storage-preflight.py": "100644",
    "ops/deploy/seiche-storage-preflight.service": "100644",
    "ops/deploy/storage-volume.env.example": "100644",
}


def fail(message: str) -> None:
    print(f"release assets: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical_absolute(text: str, label: str) -> Path:
    if (
        not text.startswith("/")
        or os.path.normpath(text) != text
        or text == "/"
    ):
        fail(f"{label} path is not absolute and canonical")
    return Path(text)


def open_directory_nofollow(path: Path) -> int:
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            visible = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            opened = os.fstat(child)
            if not stat.S_ISDIR(visible.st_mode) or (
                visible.st_dev,
                visible.st_ino,
            ) != (opened.st_dev, opened.st_ino):
                os.close(child)
                fail("asset parent has an unsafe path component")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


try:
    expected_uid = int(expected_uid_text)
    expected_gid = int(expected_gid_text)
except ValueError:
    fail("expected ownership is invalid")
if expected_uid < 0 or expected_gid < 0 or os.geteuid() != expected_uid:
    fail("materializer process ownership is invalid")
if portable_test_text not in {"0", "1"}:
    fail("portable-test policy is invalid")
if portable_test_text == "1" and expected_uid == 0:
    fail("portable-test publication is forbidden for root")
if SHA_RE.fullmatch(target) is None:
    fail("target is not one canonical commit SHA")

repo = canonical_absolute(repo_text, "repository")
parent = canonical_absolute(parent_text, "asset parent")
destination = canonical_absolute(destination_text, "asset destination")
if destination.parent != parent or FINAL_RE.fullmatch(destination.name) is None:
    fail("asset destination is outside its fixed parent")

parent_fd = -1
stage_name: str | None = None
published = False
try:
    parent_fd = open_directory_nofollow(parent)
    parent_metadata = os.fstat(parent_fd)
    if (
        parent_metadata.st_uid != expected_uid
        or parent_metadata.st_gid != expected_gid
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
    ):
        fail("asset parent must have exact protected ownership and mode")
    try:
        os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        fail("asset destination already exists; replacement is forbidden")

    for _ in range(32):
        candidate = f".release-assets.{secrets.token_hex(16)}"
        try:
            os.mkdir(candidate, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        stage_name = candidate
        break
    if stage_name is None:
        fail("could not allocate a private asset stage")
    stage = parent / stage_name
    os.chmod(stage, 0o700)
    stage_metadata = os.lstat(stage)
    if (
        not stat.S_ISDIR(stage_metadata.st_mode)
        or stage_metadata.st_uid != expected_uid
        or stage_metadata.st_gid != expected_gid
        or stat.S_IMODE(stage_metadata.st_mode) != 0o700
    ):
        fail("asset stage metadata is unsafe")

    git_environment = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }
    git_base = [
        "/usr/bin/git",
        "-c",
        f"safe.directory={repo}",
        "-C",
        str(repo),
    ]

    def git(*arguments: str, input_bytes: bytes | None = None) -> bytes:
        input_arguments: dict[str, object]
        if input_bytes is None:
            input_arguments = {"stdin": subprocess.DEVNULL}
        else:
            input_arguments = {"input": input_bytes}
        try:
            result = subprocess.run(
                [*git_base, *arguments],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=git_environment,
                cwd="/",
                **input_arguments,
            )
        except OSError as exc:
            fail(f"Git object inspection could not run: {exc}")
        if result.returncode != 0:
            fail("target Git object graph is missing or invalid")
        return result.stdout

    if git("rev-parse", "--show-object-format").strip() != b"sha1":
        fail("only canonical SHA-1 release repositories are supported")
    git("fsck", "--strict", "--no-reflogs", "--no-dangling", target)
    resolved_commit = git("rev-parse", "--verify", f"{target}^{{commit}}").strip()
    if resolved_commit != target.encode("ascii"):
        fail("target does not resolve to the exact requested commit")

    raw_tree = git(
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        target,
        "--",
        "ops/deploy",
        "ops/Caddyfile",
        "backend/seiche/__init__.py",
        "backend/seiche/nbs_intake.py",
        "backend/seiche/nbs_trust.py",
    )
    entries: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for record in raw_tree.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, raw_oid = metadata.split(b" ", 2)
            path = raw_path.decode("ascii")
            git_mode = mode.decode("ascii")
            oid = raw_oid.decode("ascii")
        except (UnicodeDecodeError, ValueError):
            fail("target tree contains a malformed asset entry")
        if (
            object_type != b"blob"
            or git_mode not in {"100644", "100755"}
            or SHA_RE.fullmatch(oid) is None
            or PATH_RE.fullmatch(path) is None
            or path.startswith("/")
            or "//" in path
            or any(component in {"", ".", ".."} for component in path.split("/"))
            or path in seen
            or not (
                path.startswith("ops/deploy/")
                or path == "ops/Caddyfile"
                or path
                in {
                    "backend/seiche/__init__.py",
                    "backend/seiche/nbs_intake.py",
                    "backend/seiche/nbs_trust.py",
                }
            )
        ):
            fail("target tree contains an unsafe asset entry")
        seen.add(path)
        entries.append((path, git_mode, oid))
    if not entries:
        fail("target contains no privileged assets")
    for required_path, required_mode in REQUIRED_MODES.items():
        matches = [entry for entry in entries if entry[0] == required_path]
        if len(matches) != 1 or matches[0][1] != required_mode:
            fail(f"required asset path or mode is invalid: {required_path}")

    manifest_entries: list[dict[str, object]] = []
    total_bytes = 0
    for path, git_mode, oid in sorted(entries):
        body = git("cat-file", "blob", oid)
        total_bytes += len(body)
        if len(body) > MAX_FILE_BYTES or total_bytes > MAX_TOTAL_BYTES:
            fail("privileged asset tree exceeds its release bound")
        calculated_oid = git("hash-object", "--stdin", input_bytes=body).strip().decode(
            "ascii", errors="strict"
        )
        if calculated_oid != oid:
            fail(f"asset blob content does not match its tree identity: {path}")

        output = stage / path
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        current = output.parent
        while current != stage:
            os.chmod(current, 0o700)
            current = current.parent
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(output, flags, 0o600)
        try:
            offset = 0
            while offset < len(body):
                written = os.write(descriptor, body[offset:])
                if written < 1:
                    fail(f"asset write made no progress: {path}")
                offset += written
            os.fchmod(descriptor, 0o755 if git_mode == "100755" else 0o644)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != expected_uid
                or metadata.st_gid != expected_gid
            ):
                fail(f"materialized asset metadata is unsafe: {path}")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        manifest_entries.append(
            {
                "blob_oid": oid,
                "git_mode": git_mode,
                "path": path,
                "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body),
            }
        )

    manifest = {
        "entries": manifest_entries,
        "git_object_format": "sha1",
        "schema": "seiche.signed-privileged-assets.v1",
        "target_sha": target,
    }
    metadata_files = {
        ".seiche-release-assets.json": (
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("ascii"),
        ".target-sha": f"{target}\n".encode("ascii"),
    }
    for name, body in metadata_files.items():
        descriptor = os.open(
            stage / name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(body):
                offset += os.write(descriptor, body[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    for root, directories, _files in os.walk(stage, topdown=False):
        for directory in directories:
            directory_path = Path(root) / directory
            metadata = os.lstat(directory_path)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                fail("materialized asset tree contains an unsafe directory")
            os.chmod(directory_path, 0o700)
            directory_fd = os.open(
                directory_path,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    stage_fd = os.open(
        stage,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(stage_fd)
    finally:
        os.close(stage_fd)

    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            fail("atomic no-replace publication is unavailable")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        if renameat2(
            parent_fd,
            stage_name.encode("ascii"),
            parent_fd,
            destination.name.encode("ascii"),
            RENAME_NOREPLACE,
        ) != 0:
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                fail("asset destination appeared concurrently; replacement refused")
            fail(f"atomic asset publication failed with errno {error}")
    elif portable_test_text == "1":
        try:
            os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            fail("asset destination appeared concurrently; replacement refused")
        os.rename(
            stage_name,
            destination.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
    else:
        fail("production asset publication requires Linux renameat2")
    published = True
    stage_name = None
    os.fsync(parent_fd)
finally:
    if stage_name is not None:
        stage_path = parent / stage_name
        try:
            shutil.rmtree(stage_path)
        except FileNotFoundError:
            pass
    if parent_fd >= 0:
        os.close(parent_fd)
    if not published:
        try:
            destination_metadata = os.lstat(destination)
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISDIR(destination_metadata.st_mode):
                fail("failed publication unexpectedly exposed an asset destination")
PY
}

# Host-free tests exercise the exact production materializer without touching
# /run or accepting this mode from a root/forced-command execution.
case "${SEICHE_DEPLOY_ASSET_TEST_ONLY:-0}" in
  0) ;;
  1)
    if [ "$(id -u)" -eq 0 ] \
        || [ "${SEICHE_ALLOW_NON_ROOT_ASSET_TEST:-0}" != 1 ] \
        || [ "$SEICHE_DEPLOY_ENTRY_MODE" = forced ]; then
      echo "FAIL: asset-only mode is restricted to explicit non-root tests" >&2
      exit 1
    fi
    TEST_REPO=${SEICHE_ASSET_TEST_REPO:?}
    TEST_TARGET=${SEICHE_ASSET_TEST_TARGET:?}
    TEST_PARENT=${SEICHE_ASSET_TEST_PARENT:?}
    TEST_DESTINATION=${SEICHE_ASSET_TEST_DESTINATION:?}
    MATERIALIZER_PYTHON=${SEICHE_ASSET_TEST_PYTHON:?}
    case "$MATERIALIZER_PYTHON" in
      /*) ;;
      *)
        echo "FAIL: asset-test Python must be an absolute path" >&2
        exit 1
        ;;
    esac
    [ -x "$MATERIALIZER_PYTHON" ] || {
      echo "FAIL: asset-test Python is not executable" >&2
      exit 1
    }
    materialize_privileged_release_assets \
      "$TEST_REPO" "$TEST_TARGET" "$TEST_PARENT" "$TEST_DESTINATION" \
      "$(id -u)" "$(id -g)" 1 "$MATERIALIZER_PYTHON"
    printf '%s\n' "$TEST_DESTINATION"
    exit 0
    ;;
  *)
    echo "FAIL: SEICHE_DEPLOY_ASSET_TEST_ONLY must be exactly 0 or 1" >&2
    exit 1
    ;;
esac

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
SIGNED_ASSET_ROOT=""
MARKET_MUTATION_LOCK=/run/lock/seiche-market-backup.lock
MARKET_MUTATION_LOCK_HELD=""
BOOTSTRAP_ASSETS_ONLY=${SEICHE_DEPLOY_BOOTSTRAP_ASSETS_ONLY:-0}
BOOTSTRAP_TEST_ONLY=${SEICHE_DEPLOY_BOOTSTRAP_TEST_ONLY:-0}
BOOTSTRAP_EXPECTED_UID=0
BOOTSTRAP_EXPECTED_GID=0
BOOTSTRAP_PORTABLE=0
BOOTSTRAP_PYTHON=/usr/bin/python3
BOOTSTRAP_GIT_HOME=/root
BOOTSTRAP_ALLOWED_SIGNERS=/etc/seiche-release.allowed-signers

case "$BOOTSTRAP_ASSETS_ONLY" in
  0|1) ;;
  *)
    echo "FAIL: SEICHE_DEPLOY_BOOTSTRAP_ASSETS_ONLY must be exactly 0 or 1" >&2
    exit 1
    ;;
esac
case "$BOOTSTRAP_TEST_ONLY" in
  0|1) ;;
  *)
    echo "FAIL: SEICHE_DEPLOY_BOOTSTRAP_TEST_ONLY must be exactly 0 or 1" >&2
    exit 1
    ;;
esac
if [ "$BOOTSTRAP_TEST_ONLY" = 1 ]; then
  if [ "$(/usr/bin/id -u)" -eq 0 ] \
      || [ "${SEICHE_ALLOW_NON_ROOT_BOOTSTRAP_TEST:-0}" != 1 ] \
      || [ "$SEICHE_DEPLOY_ENTRY_MODE" = forced ] \
      || [ "$BOOTSTRAP_ASSETS_ONLY" != 0 ]; then
    echo "FAIL: bootstrap test mode is restricted to explicit non-root local tests" >&2
    exit 1
  fi
  APP=${SEICHE_BOOTSTRAP_TEST_REPO:?}
  DEPLOY_RUNTIME_DIR=${SEICHE_BOOTSTRAP_TEST_RUNTIME:?}
  DEPLOY_LOCK=$DEPLOY_RUNTIME_DIR/deploy.lock
  BOOTSTRAP_ALLOWED_SIGNERS=${SEICHE_BOOTSTRAP_TEST_ALLOWED_SIGNERS:?}
  BOOTSTRAP_GIT_HOME=${SEICHE_BOOTSTRAP_TEST_GIT_HOME:?}
  BOOTSTRAP_PYTHON=${SEICHE_BOOTSTRAP_TEST_PYTHON:?}
  for bootstrap_path in \
      "$APP" "$DEPLOY_RUNTIME_DIR" "$BOOTSTRAP_ALLOWED_SIGNERS" \
      "$BOOTSTRAP_GIT_HOME" "$BOOTSTRAP_PYTHON"; do
    case "$bootstrap_path" in
      /*) ;;
      *)
        echo "FAIL: bootstrap test paths must be absolute" >&2
        exit 1
        ;;
    esac
  done
  [ -x "$BOOTSTRAP_PYTHON" ] || {
    echo "FAIL: bootstrap test Python is not executable" >&2
    exit 1
  }
  case "$APP:$DEPLOY_RUNTIME_DIR:$BOOTSTRAP_ALLOWED_SIGNERS" in
    /home/seiche/app:*|*:/run/seiche-deploy:*|*:/etc/seiche-release.allowed-signers)
      echo "FAIL: bootstrap tests must isolate every production path" >&2
      exit 1
      ;;
  esac
  BOOTSTRAP_EXPECTED_UID=$(/usr/bin/id -u)
  BOOTSTRAP_EXPECTED_GID=$(/usr/bin/id -g)
  BOOTSTRAP_PORTABLE=1
elif [ "$BOOTSTRAP_ASSETS_ONLY" = 1 ]; then
  if [ "$(/usr/bin/id -u)" -ne 0 ] \
      || [ "$SEICHE_DEPLOY_ENTRY_MODE" = forced ]; then
    echo "FAIL: bootstrap-assets mode is root-only and unavailable over SSH" >&2
    exit 1
  fi
fi

# Invoked directly and from the composed EXIT trap below.
# shellcheck disable=SC2329
cleanup_signed_release_assets() {
  local asset_name
  [ -n "$SIGNED_ASSET_ROOT" ] || return 0
  [ "$(dirname -- "$SIGNED_ASSET_ROOT")" = "$DEPLOY_RUNTIME_DIR" ] || {
    echo "FAIL: refusing to clean a signed-asset path outside the runtime" >&2
    return 1
  }
  asset_name=$(basename -- "$SIGNED_ASSET_ROOT")
  [[ "$asset_name" =~ ^release-assets-[0-9a-f]{40}-[0-9]+$ ]] || {
    echo "FAIL: refusing to clean an unsafe signed-asset path" >&2
    return 1
  }
  if [ -L "$SIGNED_ASSET_ROOT" ] \
      || { [ -e "$SIGNED_ASSET_ROOT" ] && [ ! -d "$SIGNED_ASSET_ROOT" ]; }; then
    echo "FAIL: signed-asset cleanup target is unsafe" >&2
    return 1
  fi
  /usr/bin/rm -rf --one-file-system -- "$SIGNED_ASSET_ROOT" || return 1
  SIGNED_ASSET_ROOT=""
}

release_market_mutation_lock() {
  [ -n "$MARKET_MUTATION_LOCK_HELD" ] || return 0
  flock --unlock 8 || return 1
  exec 8>&-
  MARKET_MUTATION_LOCK_HELD=""
}

acquire_market_mutation_lock() {
  local lock_identity fd_identity lock_created=""
  [ -z "$MARKET_MUTATION_LOCK_HELD" ] || return 0
  if systemctl is-active --quiet seiche-market-backup.service 2>/dev/null \
      || systemctl is-active --quiet seiche-market-offsite-backup.service \
        2>/dev/null \
      || systemctl is-active --quiet seiche-market-restore-check.service \
        2>/dev/null; then
    echo "FAIL: a backup/restore service remained active before runtime mutation"
    return 1
  fi
  if [ -L /run/lock ] || [ ! -d /run/lock ] \
      || [ "$(stat -c '%U:%G:%a' /run/lock)" != root:root:775 ]; then
    echo "FAIL: market mutation lock parent is unsafe"
    return 1
  fi
  if [ ! -e "$MARKET_MUTATION_LOCK" ] && [ ! -L "$MARKET_MUTATION_LOCK" ]; then
    if (umask 077; set -o noclobber; : >"$MARKET_MUTATION_LOCK") 2>/dev/null; then
      lock_created=1
    elif [ ! -e "$MARKET_MUTATION_LOCK" ]; then
      echo "FAIL: market mutation lock could not be created"
      return 1
    fi
  fi
  if [ "$(stat -c '%U:%G:%a:%h' "$MARKET_MUTATION_LOCK")" \
      != root:root:600:1 ]; then
    echo "FAIL: market mutation lock metadata is unsafe"
    if [ -n "$lock_created" ]; then
      rm -f -- "$MARKET_MUTATION_LOCK"
    fi
    return 1
  fi
  exec 8<>"$MARKET_MUTATION_LOCK"
  lock_identity=$(stat -c '%d:%i' "$MARKET_MUTATION_LOCK") || {
    exec 8>&-
    return 1
  }
  fd_identity=$(stat -Lc '%d:%i' "/proc/$$/fd/8") || {
    exec 8>&-
    return 1
  }
  if [ "$lock_identity" != "$fd_identity" ] \
      || ! flock --wait 300 8; then
    exec 8>&-
    echo "FAIL: market mutation lock remained busy or changed identity"
    return 1
  fi
  MARKET_MUTATION_LOCK_HELD=1
  echo "market runtime: acquired intake/backup serialization lock"
}

if [ -L "$DEPLOY_RUNTIME_DIR" ] \
    || { [ -e "$DEPLOY_RUNTIME_DIR" ] && [ ! -d "$DEPLOY_RUNTIME_DIR" ]; }; then
  echo "FAIL: deploy runtime directory is not a real directory"
  exit 1
fi
if [ "$BOOTSTRAP_PORTABLE" = 1 ]; then
  [ -d "$DEPLOY_RUNTIME_DIR" ] || {
    echo "FAIL: portable bootstrap runtime must already exist"
    exit 1
  }
else
  install -d -o root -g root -m 0700 "$DEPLOY_RUNTIME_DIR"
fi
if ! "$BOOTSTRAP_PYTHON" -I -B - \
    "$DEPLOY_RUNTIME_DIR" "$BOOTSTRAP_EXPECTED_UID" \
    "$BOOTSTRAP_EXPECTED_GID" <<'PY'
import os
import stat
import sys

path, uid, gid = sys.argv[1:]
metadata = os.lstat(path)
if (
    not stat.S_ISDIR(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != int(uid)
    or metadata.st_gid != int(gid)
    or stat.S_IMODE(metadata.st_mode) != 0o700
):
    raise SystemExit(1)
PY
then
  echo "FAIL: deploy runtime directory permissions are unsafe"
  exit 1
fi
exec 9>"$DEPLOY_LOCK"
[ "$BOOTSTRAP_PORTABLE" = 1 ] || chown root:root "$DEPLOY_LOCK"
chmod 0600 "$DEPLOY_LOCK"
if [ "$BOOTSTRAP_PORTABLE" != 1 ] && ! flock --nonblock 9; then
  echo "FAIL: another seiche deployment is still running"
  exit 1
fi
trap 'release_market_mutation_lock || true; cleanup_signed_release_assets || true' EXIT

valid_release_sha() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]]
}

valid_activation_token() {
  [[ "$1" =~ ^[0-9a-f]{64}$ ]]
}

verify_release_target_signature() {
  local target="$1" fetched_main="$2"
  local allowed_signers=$BOOTSTRAP_ALLOWED_SIGNERS
  local principal=beepboop2025@users.noreply.github.com
  local author="" signer_line=""
  if ! "$BOOTSTRAP_PYTHON" -I -B - \
      "$allowed_signers" "$BOOTSTRAP_EXPECTED_UID" \
      "$BOOTSTRAP_EXPECTED_GID" <<'PY'
import os
import stat
import sys

path, uid, gid = sys.argv[1:]
descriptor = os.open(
    path,
    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
)
try:
    metadata = os.fstat(descriptor)
    visible = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != int(uid)
        or metadata.st_gid != int(gid)
        or stat.S_IMODE(metadata.st_mode) != 0o444
        or (metadata.st_dev, metadata.st_ino) != (visible.st_dev, visible.st_ino)
    ):
        raise SystemExit(1)
finally:
    os.close(descriptor)
PY
  then
    echo "FAIL: root release signer pin is missing or unsafe"
    return 1
  fi
  if ! IFS= read -r signer_line <"$allowed_signers" \
      || ! printf '%s\n' "$signer_line" | cmp -s - "$allowed_signers" \
      || [[ "$signer_line" == *$'\r'* ]]; then
    echo "FAIL: root release signer pin is not one canonical line"
    return 1
  fi
  case "$signer_line" in
    "$principal ssh-ed25519 "*|"$principal sk-ssh-ed25519@openssh.com "*) ;;
    *)
      echo "FAIL: root release signer pin does not name the release principal"
      return 1
      ;;
  esac
  [ -x /usr/bin/ssh-keygen ] || {
    echo "FAIL: trusted SSH signature verifier is unavailable"
    return 1
  }
  author=$(/usr/bin/env -i \
    GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
    GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
    HOME="$BOOTSTRAP_GIT_HOME" LANG=C LC_ALL=C PATH=/usr/bin:/bin \
    /usr/bin/git -c "safe.directory=$APP" -C "$APP" \
      show -s --format=%ae "$target") || {
    echo "FAIL: signed target author cannot be resolved"
    return 1
  }
  [ "$author" = "$principal" ] || {
    echo "FAIL: signed target author is not the pinned release principal"
    return 1
  }
  if ! /usr/bin/env -i \
      GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
      GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
      HOME="$BOOTSTRAP_GIT_HOME" LANG=C LC_ALL=C PATH=/usr/bin:/bin \
      /usr/bin/git -c "safe.directory=$APP" -C "$APP" \
        -c gpg.format=ssh \
        -c "gpg.ssh.allowedSignersFile=$allowed_signers" \
        -c gpg.ssh.program=/usr/bin/ssh-keygen \
        verify-commit "$target"; then
    echo "FAIL: target commit lacks the pinned SSH signature"
    return 1
  fi
  if ! /usr/bin/env -i \
      GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
      GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
      HOME="$BOOTSTRAP_GIT_HOME" LANG=C LC_ALL=C PATH=/usr/bin:/bin \
      /usr/bin/git -c "safe.directory=$APP" -C "$APP" \
        merge-base --is-ancestor "$target" "$fetched_main"; then
    echo "FAIL: signed target is outside the fetched main range"
    return 1
  fi
}

verify_bootstrap_wrapper_blob() {
  local target="$1" tree_line="" tree_mode="" tree_type="" tree_oid=""
  local tree_path="" actual_oid="" self_path=""
  if [ "$BOOTSTRAP_PORTABLE" = 1 ]; then
    self_path=$("$BOOTSTRAP_PYTHON" -I -B - "$0" <<'PY'
import os
from pathlib import Path
import sys

text = sys.argv[1]
if not text.startswith("/") or os.path.normpath(text) != text:
    raise SystemExit(1)
print(Path(text))
PY
    )
  else
    self_path=$(/usr/bin/readlink -f -- "$0")
  fi || {
    echo "FAIL: bootstrap wrapper path cannot be resolved"
    return 1
  }
  if [ "$(dirname -- "$self_path")" != "$DEPLOY_RUNTIME_DIR" ]; then
    echo "FAIL: bootstrap wrapper is outside the fixed root runtime"
    return 1
  fi
  "$BOOTSTRAP_PYTHON" -I -B - \
      "$DEPLOY_RUNTIME_DIR" "$BOOTSTRAP_EXPECTED_UID" \
      "$BOOTSTRAP_EXPECTED_GID" "$BOOTSTRAP_PORTABLE" <<'PY' || {
import os
from pathlib import Path
import stat
import sys

path_text, uid_text, gid_text, portable_text = sys.argv[1:]
uid, gid = int(uid_text), int(gid_text)
path = Path(path_text)
if not path_text.startswith("/") or os.path.normpath(path_text) != path_text:
    raise SystemExit(1)
flags = (
    os.O_RDONLY
    | os.O_DIRECTORY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
descriptor = os.open("/", flags)
try:
    for component in path.parts[1:]:
        parent = os.fstat(descriptor)
        owner_is_safe = (parent.st_uid, parent.st_gid) == (0, 0) or (
            portable_text == "1" and (parent.st_uid, parent.st_gid) == (uid, gid)
        )
        if not owner_is_safe or stat.S_IMODE(parent.st_mode) & 0o022:
            raise SystemExit(1)
        child = os.open(component, flags, dir_fd=descriptor)
        visible = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
        opened = os.fstat(child)
        if not stat.S_ISDIR(visible.st_mode) or (
            visible.st_dev,
            visible.st_ino,
        ) != (opened.st_dev, opened.st_ino):
            os.close(child)
            raise SystemExit(1)
        os.close(descriptor)
        descriptor = child
    final = os.fstat(descriptor)
    if (
        final.st_uid != uid
        or final.st_gid != gid
        or stat.S_IMODE(final.st_mode) != 0o700
    ):
        raise SystemExit(1)
finally:
    os.close(descriptor)
PY
    echo "FAIL: bootstrap wrapper parent chain is unsafe"
    return 1
  }
  if [ -L "$0" ] || [ ! -f "$self_path" ] \
      || ! "$BOOTSTRAP_PYTHON" -I -B - \
        "$self_path" "$BOOTSTRAP_EXPECTED_UID" \
        "$BOOTSTRAP_EXPECTED_GID" <<'PY'
import os
import stat
import sys

path, uid, gid = sys.argv[1:]
metadata = os.lstat(path)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_nlink != 1
    or metadata.st_uid != int(uid)
    or metadata.st_gid != int(gid)
    or stat.S_IMODE(metadata.st_mode) != 0o500
):
    raise SystemExit(1)
PY
  then
    echo "FAIL: bootstrap wrapper must be one root-owned 0500 regular file"
    return 1
  fi
  tree_line=$(/usr/bin/env -i \
    GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
    GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
    HOME="$BOOTSTRAP_GIT_HOME" LANG=C LC_ALL=C PATH=/usr/bin:/bin \
    /usr/bin/git -c "safe.directory=$APP" -C "$APP" \
      ls-tree "$target" -- ops/deploy/seiche-deploy-wrapper.sh) || {
    echo "FAIL: bootstrap wrapper blob cannot be resolved"
    return 1
  }
  IFS=$' \t' read -r tree_mode tree_type tree_oid tree_path <<<"$tree_line"
  if [ "$tree_mode" != 100644 ] || [ "$tree_type" != blob ] \
      || [[ ! "$tree_oid" =~ ^[0-9a-f]{40}$ ]] \
      || [ "$tree_path" != ops/deploy/seiche-deploy-wrapper.sh ]; then
    echo "FAIL: signed target has no exact deploy-wrapper blob"
    return 1
  fi
  actual_oid=$(/usr/bin/env -i \
    GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
    GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
    HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
    /usr/bin/git hash-object --stdin <"$self_path") || {
    echo "FAIL: bootstrap wrapper bytes cannot be hashed"
    return 1
  }
  [ "$actual_oid" = "$tree_oid" ] || {
    echo "FAIL: executing bootstrap wrapper is not the exact target blob"
    return 1
  }
}

verify_release_object_graph() {
  local target="$1"
  /usr/bin/env -i \
    GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
    GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
    HOME="$BOOTSTRAP_GIT_HOME" LANG=C LC_ALL=C PATH=/usr/bin:/bin \
    /usr/bin/git -c "safe.directory=$APP" -C "$APP" \
      fsck --strict --no-reflogs --no-dangling "$target"
}

if [ "$BOOTSTRAP_ASSETS_ONLY" = 1 ] || [ "$BOOTSTRAP_TEST_ONLY" = 1 ]; then
  TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}
  BOOTSTRAP_MAIN=$(/usr/bin/env -i \
    GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
    GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
    HOME="$BOOTSTRAP_GIT_HOME" LANG=C LC_ALL=C PATH=/usr/bin:/bin \
    /usr/bin/git -c "safe.directory=$APP" -C "$APP" \
      rev-parse --verify 'refs/remotes/origin/main^{commit}') || {
    echo "FAIL: bootstrap origin/main cannot be resolved"
    exit 1
  }
  valid_release_sha "$TARGET" || {
    echo "FAIL: bootstrap-assets mode requires one exact target SHA"
    exit 1
  }
  valid_release_sha "$BOOTSTRAP_MAIN" && [ "$TARGET" = "$BOOTSTRAP_MAIN" ] || {
    echo "FAIL: bootstrap target must equal the fetched canonical origin/main"
    exit 1
  }
  verify_release_object_graph "$TARGET" || {
    echo "FAIL: bootstrap target object graph is invalid"
    exit 1
  }
  verify_bootstrap_wrapper_blob "$TARGET" || exit 1
  verify_release_target_signature "$TARGET" "$BOOTSTRAP_MAIN" || exit 1
  SIGNED_ASSET_ROOT="$DEPLOY_RUNTIME_DIR/release-assets-${TARGET}-$$"
  materializer_arguments=(
    "$APP" "$TARGET" "$DEPLOY_RUNTIME_DIR" "$SIGNED_ASSET_ROOT"
    "$BOOTSTRAP_EXPECTED_UID" "$BOOTSTRAP_EXPECTED_GID" "$BOOTSTRAP_PORTABLE"
  )
  if [ "$BOOTSTRAP_PORTABLE" = 1 ]; then
    materializer_arguments+=("$BOOTSTRAP_PYTHON")
  fi
  if ! materialize_privileged_release_assets "${materializer_arguments[@]}"; then
    echo "FAIL: exact signed bootstrap assets could not be materialized"
    exit 1
  fi
  [ "$SIGNED_ASSET_ROOT/ops/deploy/seiche-deploy-wrapper.sh" -ef "$0" ] \
    || cmp -s -- \
      "$SIGNED_ASSET_ROOT/ops/deploy/seiche-deploy-wrapper.sh" "$0" || {
      echo "FAIL: materialized deploy wrapper does not match the bootstrap blob"
      exit 1
    }
  retained_asset_root=$SIGNED_ASSET_ROOT
  SIGNED_ASSET_ROOT=""
  trap - EXIT
  printf 'release assets: retained exact signed target at %s\n' \
    "$retained_asset_root"
  exit 0
fi

# Snapshot assembly needs several CPU cores before strict health can turn green.
# Require a stable quiet window before any service is stopped; one low sample is
# not enough on this shared host because owner-controlled workloads start in
# phases. The fixed 75 percent ceiling leaves four cores free on the 16-core
# production host without trusting caller-controlled configuration.
admit_shared_host() {
  local cpu_count ceiling sample load_one load_five
  if ! cpu_count=$(/usr/bin/getconf _NPROCESSORS_ONLN 2>/dev/null) \
      || [[ ! "$cpu_count" =~ ^[0-9]+$ ]] \
      || (( cpu_count < 1 || cpu_count > 4096 )); then
    echo "DEFER: shared-host CPU capacity is unreadable; production unchanged"
    return 1
  fi
  if ! ceiling=$(
    /usr/bin/awk -v cpus="$cpu_count" \
      'BEGIN { printf "%.2f", cpus * 0.75 }'
  ) || [[ ! "$ceiling" =~ ^[0-9]+[.][0-9]{2}$ ]]; then
    echo "DEFER: shared-host load ceiling is invalid; production unchanged"
    return 1
  fi
  for (( sample = 1; sample <= 3; sample++ )); do
    if ! IFS=' ' read -r load_one load_five _ </proc/loadavg \
        || [[ ! "$load_one" =~ ^[0-9]+([.][0-9]+)?$ ]] \
        || [[ ! "$load_five" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
      echo "DEFER: shared-host load is unreadable; production unchanged"
      return 1
    fi
    if ! LC_ALL=C /usr/bin/awk -v observed="$load_one" -v limit="$ceiling" \
        'BEGIN { exit !(observed <= limit) }'; then
      printf 'DEFER: shared-host one-minute load %s exceeds %s; production unchanged\n' \
        "$load_one" "$ceiling"
      return 1
    fi
    if ! LC_ALL=C /usr/bin/awk -v observed="$load_five" -v limit="$ceiling" \
        'BEGIN { exit !(observed <= limit) }'; then
      printf 'DEFER: shared-host five-minute load %s exceeds %s; production unchanged\n' \
        "$load_five" "$ceiling"
      return 1
    fi
    if (( sample < 3 )); then
      sleep 10 || return 1
    fi
  done
  printf 'shared-host admission: three quiet one/five-minute samples at or below %s\n' \
    "$ceiling"
}

case "${SEICHE_DEPLOY_ADMISSION_ONLY:-0}" in
  0) ;;
  1)
    [ "$SEICHE_DEPLOY_ENTRY_MODE" != forced ] \
      || { echo "FAIL: forced deploy cannot request admission-only mode"; exit 1; }
    if admit_shared_host; then
      exit 0
    fi
    exit 75
    ;;
  *)
    echo "FAIL: SEICHE_DEPLOY_ADMISSION_ONLY must be exactly 0 or 1"
    exit 1
    ;;
esac

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

BEFORE=$("$RUNUSER" -u seiche -- git -C "$APP" rev-parse HEAD)
if ! "$RUNUSER" -u seiche -- git -C "$APP" fetch -q origin main; then
  echo "FAIL: could not fetch the candidate release"
  exit 1
fi
LATEST=$("$RUNUSER" -u seiche -- git -C "$APP" rev-parse origin/main)
if ! valid_release_sha "$LATEST" \
    || ! "$RUNUSER" -u seiche -- git -C "$APP" rev-parse --verify --quiet \
      "$LATEST^{commit}" >/dev/null; then
  echo "FAIL: origin/main did not resolve to a canonical local commit"
  exit 1
fi
# A local controller or the forced-command SSH request passes one reviewed
# identity here. Never let the wrapper silently replace it with a newer main
# tip. A direct root invocation without either constraint retains the explicit
# latest-main maintenance behavior.
EXPECTED_TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}
if [ "$SEICHE_DEPLOY_ENTRY_MODE" = forced ]; then
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
  if ! "$RUNUSER" -u seiche -- git -C "$APP" rev-parse --verify --quiet \
      "$EXPECTED_TARGET^{commit}" >/dev/null \
      || ! "$RUNUSER" -u seiche -- git -C "$APP" merge-base --is-ancestor \
        "$EXPECTED_TARGET" "$LATEST"; then
    echo "FAIL: reviewed target is not a fetched commit on main"
    exit 1
  fi
  TARGET=$EXPECTED_TARGET
fi
verify_release_target_signature "$TARGET" "$LATEST" || exit 1
if ! admit_shared_host; then
  exit 75
fi
SIGNED_ASSET_ROOT="$DEPLOY_RUNTIME_DIR/release-assets-${TARGET}-$$"
if ! materialize_privileged_release_assets \
    "$APP" "$TARGET" "$DEPLOY_RUNTIME_DIR" "$SIGNED_ASSET_ROOT" 0 0 0; then
  echo "FAIL: exact signed privileged assets could not be materialized"
  exit 1
fi
echo "release assets: materialized exact target ${TARGET:0:7}"
MARKET_WORKER_WAS_ACTIVE=""
MARKET_WORKER_WAS_ENABLED=""
MARKET_BACKFILL_WAS_ACTIVE=""
SOURCE_WORKER_WAS_ACTIVE=""
SOURCE_WORKER_WAS_ENABLED=""
READINESS_TIMER_WAS_ACTIVE=""
READINESS_TIMER_WAS_ENABLED=""
VALIDATION_TIMER_WAS_ACTIVE=""
VALIDATION_TIMER_WAS_ENABLED=""
BACKUP_TIMER_WAS_ACTIVE=""
BACKUP_TIMER_WAS_ENABLED=""
RESTORE_TIMER_WAS_ACTIVE=""
RESTORE_TIMER_WAS_ENABLED=""
OFFSITE_TIMER_WAS_ACTIVE=""
OFFSITE_TIMER_WAS_ENABLED=""
if systemctl is-active --quiet seiche-market-worker.service 2>/dev/null; then
  MARKET_WORKER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet seiche-market-worker.service 2>/dev/null; then
  MARKET_WORKER_WAS_ENABLED=1
fi
if systemctl is-active --quiet seiche-market-backfill.service 2>/dev/null; then
  MARKET_BACKFILL_WAS_ACTIVE=1
fi
if systemctl is-active --quiet seiche-source-worker.service 2>/dev/null; then
  SOURCE_WORKER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet seiche-source-worker.service 2>/dev/null; then
  SOURCE_WORKER_WAS_ENABLED=1
fi
if systemctl is-active --quiet seiche-data-readiness.timer 2>/dev/null; then
  READINESS_TIMER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet seiche-data-readiness.timer 2>/dev/null; then
  READINESS_TIMER_WAS_ENABLED=1
fi
if systemctl is-active --quiet seiche-market-validation.timer 2>/dev/null; then
  VALIDATION_TIMER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet seiche-market-validation.timer 2>/dev/null; then
  VALIDATION_TIMER_WAS_ENABLED=1
fi
if systemctl is-active --quiet seiche-market-backup.timer 2>/dev/null; then
  BACKUP_TIMER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet seiche-market-backup.timer 2>/dev/null; then
  BACKUP_TIMER_WAS_ENABLED=1
fi
if systemctl is-active --quiet seiche-market-restore-check.timer 2>/dev/null; then
  RESTORE_TIMER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet seiche-market-restore-check.timer 2>/dev/null; then
  RESTORE_TIMER_WAS_ENABLED=1
fi
if systemctl is-active --quiet seiche-market-offsite-backup.timer 2>/dev/null; then
  OFFSITE_TIMER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet seiche-market-offsite-backup.timer 2>/dev/null; then
  OFFSITE_TIMER_WAS_ENABLED=1
fi

# Capture every market-platform unit and generated storage-policy artifact this
# deploy can replace. A rollback must restore exact host bytes (or exact prior
# absence); resetting the application checkout alone cannot reproduce drop-ins
# or root-controlled helpers. The /run copy lives only for this locked deploy.
DATA_UNIT_NAMES=(
  seiche-storage-preflight.service
  seiche-market-worker.service
  seiche-pull.service
  seiche-market-backfill.service
  seiche-source-worker.service
  seiche-data-readiness.service
  seiche-data-readiness.timer
  seiche-market-validation.service
  seiche-market-validation.timer
  seiche-market-backup.service
  seiche-market-backup.timer
  seiche-market-offsite-backup.service
  seiche-market-offsite-backup.timer
  seiche-market-restore-check.service
  seiche-market-restore-check.timer
  seiche-snapshot-promote.service
)
DATA_ARTIFACT_NAMES=(
  storage-preflight-helper
  nbs-intake-launcher
  data-readiness-helper
  market-offsite-backup-helper
  market-backup-helper
  market-restore-check-helper
  api-market-platform-dropin
  release-poll-storage-dropin
  validation-state-dropin
  backup-paths-dropin
  restore-paths-dropin
  nbs-runtime-current-sha
)
DATA_ARTIFACT_PATHS=(
  /etc/seiche/libexec/seiche-storage-preflight.py
  /etc/seiche/libexec/seiche-nbs-intake.py
  /etc/seiche/libexec/seiche-data-readiness.sh
  /etc/seiche/libexec/seiche-market-offsite-backup.sh
  /etc/seiche/libexec/seiche-market-backup.sh
  /etc/seiche/libexec/seiche-market-restore-check.sh
  /etc/systemd/system/seiche-api.service.d/market-platform.conf
  /etc/systemd/system/seiche-release-poll.service.d/storage-volume.conf
  /etc/systemd/system/seiche-market-validation.service.d/state-path.conf
  /etc/systemd/system/seiche-market-backup.service.d/paths.conf
  /etc/systemd/system/seiche-market-restore-check.service.d/paths.conf
  /opt/seiche-nbs-intake/current-sha
)
DATA_UNIT_ROLLBACK_DIR=""
DATA_UNITS_MAY_HAVE_CHANGED=""
cleanup_preupdate_data_units() {
  local artifact index unit
  [ -n "$DATA_UNIT_ROLLBACK_DIR" ] || return 0
  case "$DATA_UNIT_ROLLBACK_DIR" in
    "$DEPLOY_RUNTIME_DIR"/.data-units.*) ;;
    *)
      echo "FAIL: refusing to clean an unsafe data-unit rollback path" >&2
      return 1
      ;;
  esac
  for unit in "${DATA_UNIT_NAMES[@]}"; do
    rm -f -- "$DATA_UNIT_ROLLBACK_DIR/$unit.present" \
      "$DATA_UNIT_ROLLBACK_DIR/$unit.absent"
  done
  for index in "${!DATA_ARTIFACT_NAMES[@]}"; do
    artifact=${DATA_ARTIFACT_NAMES[$index]}
    rm -f -- "$DATA_UNIT_ROLLBACK_DIR/$artifact.present" \
      "$DATA_UNIT_ROLLBACK_DIR/$artifact.absent"
  done
  rmdir "$DATA_UNIT_ROLLBACK_DIR" 2>/dev/null || return 1
  DATA_UNIT_ROLLBACK_DIR=""
}
capture_preupdate_data_units() {
  local artifact destination index unit
  DATA_UNIT_ROLLBACK_DIR=$(mktemp -d "$DEPLOY_RUNTIME_DIR/.data-units.XXXXXX") \
    || return 1
  if ! chown root:root "$DATA_UNIT_ROLLBACK_DIR" \
      || ! chmod 0700 "$DATA_UNIT_ROLLBACK_DIR"; then
    cleanup_preupdate_data_units || true
    return 1
  fi
  for unit in "${DATA_UNIT_NAMES[@]}"; do
    destination="/etc/systemd/system/$unit"
    if [ -e "$destination" ] || [ -L "$destination" ]; then
      if [ -L "$destination" ] || [ ! -f "$destination" ] \
          || ! cp -p -- "$destination" \
            "$DATA_UNIT_ROLLBACK_DIR/$unit.present"; then
        cleanup_preupdate_data_units || true
        echo "FAIL: pre-deploy data-unit state is unsafe or unreadable"
        return 1
      fi
    elif ! install -m 0600 /dev/null \
        "$DATA_UNIT_ROLLBACK_DIR/$unit.absent"; then
      cleanup_preupdate_data_units || true
      return 1
    fi
  done
  if [ "${#DATA_ARTIFACT_NAMES[@]}" -ne "${#DATA_ARTIFACT_PATHS[@]}" ]; then
    cleanup_preupdate_data_units || true
    echo "FAIL: data artifact rollback manifest is inconsistent"
    return 1
  fi
  for index in "${!DATA_ARTIFACT_NAMES[@]}"; do
    artifact=${DATA_ARTIFACT_NAMES[$index]}
    destination=${DATA_ARTIFACT_PATHS[$index]}
    if [ -e "$destination" ] || [ -L "$destination" ]; then
      if [ -L "$destination" ] || [ ! -f "$destination" ] \
          || ! cp -p -- "$destination" \
            "$DATA_UNIT_ROLLBACK_DIR/$artifact.present"; then
        cleanup_preupdate_data_units || true
        echo "FAIL: pre-deploy data artifact is unsafe or unreadable"
        return 1
      fi
    elif ! install -m 0600 /dev/null \
        "$DATA_UNIT_ROLLBACK_DIR/$artifact.absent"; then
      cleanup_preupdate_data_units || true
      return 1
    fi
  done
}
cleanup_data_unit_restore_stage() {
  local stage="$1" unit
  case "$stage" in
    /etc/systemd/system/.seiche-data-units-restore.*) ;;
    *) return 1 ;;
  esac
  for unit in "${DATA_UNIT_NAMES[@]}"; do
    rm -f -- "$stage/$unit"
  done
  rmdir "$stage" 2>/dev/null || true
}
restore_preupdate_data_units() {
  local artifact artifact_stage captured destination index stage unit
  local -a candidates=()
  [ -n "$DATA_UNITS_MAY_HAVE_CHANGED" ] || return 0
  case "$DATA_UNIT_ROLLBACK_DIR" in
    "$DEPLOY_RUNTIME_DIR"/.data-units.*) ;;
    *)
      echo "FAIL: no safe pre-deploy data-unit snapshot is available"
      return 1
      ;;
  esac
  # A failed installer may already have enabled persistent candidate timers.
  # Quiesce every reader/writer before restoring files or generated drop-ins.
  systemctl stop \
    seiche-data-readiness.timer seiche-data-readiness.service \
    seiche-market-validation.timer seiche-market-validation.service \
    seiche-market-backup.timer seiche-market-backup.service \
    seiche-market-offsite-backup.timer seiche-market-offsite-backup.service \
    seiche-market-restore-check.timer seiche-market-restore-check.service \
    2>/dev/null || true
  stage=$(mktemp -d /etc/systemd/system/.seiche-data-units-restore.XXXXXX) \
    || return 1
  chmod 0700 "$stage" || {
    cleanup_data_unit_restore_stage "$stage"
    return 1
  }
  for unit in "${DATA_UNIT_NAMES[@]}"; do
    captured="$DATA_UNIT_ROLLBACK_DIR/$unit.present"
    if [ -f "$captured" ] && [ ! -L "$captured" ] \
        && [ ! -e "$DATA_UNIT_ROLLBACK_DIR/$unit.absent" ]; then
      if ! cp -p -- "$captured" "$stage/$unit"; then
        cleanup_data_unit_restore_stage "$stage"
        return 1
      fi
      candidates+=("$stage/$unit")
    elif [ -f "$DATA_UNIT_ROLLBACK_DIR/$unit.absent" ] \
        && [ ! -L "$DATA_UNIT_ROLLBACK_DIR/$unit.absent" ] \
        && [ ! -e "$captured" ]; then
      :
    else
      cleanup_data_unit_restore_stage "$stage"
      echo "FAIL: pre-deploy data-unit snapshot is incomplete"
      return 1
    fi
  done
  for index in "${!DATA_ARTIFACT_NAMES[@]}"; do
    artifact=${DATA_ARTIFACT_NAMES[$index]}
    captured="$DATA_UNIT_ROLLBACK_DIR/$artifact.present"
    if [ -f "$captured" ] && [ ! -L "$captured" ] \
        && [ ! -e "$DATA_UNIT_ROLLBACK_DIR/$artifact.absent" ]; then
      :
    elif [ -f "$DATA_UNIT_ROLLBACK_DIR/$artifact.absent" ] \
        && [ ! -L "$DATA_UNIT_ROLLBACK_DIR/$artifact.absent" ] \
        && [ ! -e "$captured" ]; then
      :
    else
      cleanup_data_unit_restore_stage "$stage"
      echo "FAIL: pre-deploy data artifact snapshot is incomplete"
      return 1
    fi
  done
  if (( ${#candidates[@]} > 0 )) \
      && ! systemd-analyze verify "${candidates[@]}"; then
    cleanup_data_unit_restore_stage "$stage"
    echo "FAIL: pre-deploy data units no longer pass systemd verification"
    return 1
  fi

  # Remove any enablement created by the candidate while its unit is still
  # visible. A missing unit can make disable return nonzero, so the final
  # is-enabled check below is the authoritative fail-closed assertion.
  if [ -z "$MARKET_WORKER_WAS_ENABLED" ]; then
    systemctl disable seiche-market-worker.service >/dev/null 2>&1 || true
  fi
  if [ -z "$SOURCE_WORKER_WAS_ENABLED" ]; then
    systemctl disable seiche-source-worker.service >/dev/null 2>&1 || true
  fi
  if [ -z "$READINESS_TIMER_WAS_ENABLED" ]; then
    systemctl disable seiche-data-readiness.timer >/dev/null 2>&1 || true
  fi
  if [ -z "$VALIDATION_TIMER_WAS_ENABLED" ]; then
    systemctl disable seiche-market-validation.timer >/dev/null 2>&1 || true
  fi
  if [ -z "$BACKUP_TIMER_WAS_ENABLED" ]; then
    systemctl disable seiche-market-backup.timer >/dev/null 2>&1 || true
  fi
  if [ -z "$RESTORE_TIMER_WAS_ENABLED" ]; then
    systemctl disable seiche-market-restore-check.timer >/dev/null 2>&1 || true
  fi
  if [ -z "$OFFSITE_TIMER_WAS_ENABLED" ]; then
    systemctl disable seiche-market-offsite-backup.timer >/dev/null 2>&1 || true
  fi
  for index in "${!DATA_ARTIFACT_NAMES[@]}"; do
    artifact=${DATA_ARTIFACT_NAMES[$index]}
    destination=${DATA_ARTIFACT_PATHS[$index]}
    captured="$DATA_UNIT_ROLLBACK_DIR/$artifact.present"
    if [ -f "$captured" ]; then
      artifact_stage=$(mktemp \
        "$(dirname "$destination")/.seiche-data-artifact-restore.XXXXXX") \
        || {
          cleanup_data_unit_restore_stage "$stage"
          return 1
        }
      if ! cp -p -- "$captured" "$artifact_stage" \
          || ! mv -f -- "$artifact_stage" "$destination"; then
        rm -f -- "$artifact_stage"
        cleanup_data_unit_restore_stage "$stage"
        echo "FAIL: pre-deploy data artifacts could not be restored"
        return 1
      fi
    elif ! rm -f -- "$destination"; then
      cleanup_data_unit_restore_stage "$stage"
      echo "FAIL: candidate-only data artifacts could not be removed"
      return 1
    fi
  done
  for unit in "${DATA_UNIT_NAMES[@]}"; do
    destination="/etc/systemd/system/$unit"
    if [ -f "$DATA_UNIT_ROLLBACK_DIR/$unit.present" ]; then
      if ! mv -f "$stage/$unit" "$destination"; then
        cleanup_data_unit_restore_stage "$stage"
        echo "FAIL: pre-deploy data-unit files could not be restored"
        return 1
      fi
    elif ! rm -f -- "$destination"; then
      cleanup_data_unit_restore_stage "$stage"
      echo "FAIL: candidate-only data-unit files could not be removed"
      return 1
    fi
  done
  cleanup_data_unit_restore_stage "$stage"
  if ! systemctl daemon-reload; then
    echo "FAIL: systemd rejected restored pre-deploy data units"
    return 1
  fi
  if [ -n "$MARKET_WORKER_WAS_ENABLED" ]; then
    if ! systemctl enable seiche-market-worker.service >/dev/null \
        || ! systemctl is-enabled --quiet seiche-market-worker.service; then
      echo "FAIL: market worker enablement could not be restored"
      return 1
    fi
  else
    systemctl disable seiche-market-worker.service >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-market-worker.service 2>/dev/null; then
      echo "FAIL: candidate market worker remains enabled after rollback"
      return 1
    fi
  fi
  if [ -n "$SOURCE_WORKER_WAS_ENABLED" ]; then
    if ! systemctl enable seiche-source-worker.service >/dev/null \
        || ! systemctl is-enabled --quiet seiche-source-worker.service; then
      echo "FAIL: source worker enablement could not be restored"
      return 1
    fi
  else
    systemctl disable seiche-source-worker.service >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-source-worker.service 2>/dev/null; then
      echo "FAIL: candidate source worker remains enabled after rollback"
      return 1
    fi
  fi
  if [ -n "$READINESS_TIMER_WAS_ENABLED" ]; then
    if ! systemctl enable seiche-data-readiness.timer >/dev/null \
        || ! systemctl is-enabled --quiet seiche-data-readiness.timer; then
      echo "FAIL: readiness timer enablement could not be restored"
      return 1
    fi
  else
    systemctl disable seiche-data-readiness.timer >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-data-readiness.timer 2>/dev/null; then
      echo "FAIL: candidate readiness timer remains enabled after rollback"
      return 1
    fi
  fi
  if [ -n "$VALIDATION_TIMER_WAS_ENABLED" ]; then
    if ! systemctl enable seiche-market-validation.timer >/dev/null \
        || ! systemctl is-enabled --quiet seiche-market-validation.timer; then
      echo "FAIL: validation timer enablement could not be restored"
      return 1
    fi
  else
    systemctl disable seiche-market-validation.timer >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-market-validation.timer 2>/dev/null; then
      echo "FAIL: candidate validation timer remains enabled after rollback"
      return 1
    fi
  fi
  if [ -n "$BACKUP_TIMER_WAS_ENABLED" ]; then
    if ! systemctl enable seiche-market-backup.timer >/dev/null \
        || ! systemctl is-enabled --quiet seiche-market-backup.timer; then
      echo "FAIL: backup timer enablement could not be restored"
      return 1
    fi
  else
    systemctl disable seiche-market-backup.timer >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-market-backup.timer 2>/dev/null; then
      echo "FAIL: candidate backup timer remains enabled after rollback"
      return 1
    fi
  fi
  if [ -n "$RESTORE_TIMER_WAS_ENABLED" ]; then
    if ! systemctl enable seiche-market-restore-check.timer >/dev/null \
        || ! systemctl is-enabled --quiet seiche-market-restore-check.timer; then
      echo "FAIL: restore-check timer enablement could not be restored"
      return 1
    fi
  else
    systemctl disable seiche-market-restore-check.timer >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-market-restore-check.timer \
        2>/dev/null; then
      echo "FAIL: candidate restore-check timer remains enabled after rollback"
      return 1
    fi
  fi
  if [ -n "$OFFSITE_TIMER_WAS_ENABLED" ]; then
    if ! systemctl enable seiche-market-offsite-backup.timer >/dev/null \
        || ! systemctl is-enabled --quiet \
          seiche-market-offsite-backup.timer; then
      echo "FAIL: offsite backup timer enablement could not be restored"
      return 1
    fi
  else
    systemctl disable seiche-market-offsite-backup.timer \
      >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-market-offsite-backup.timer \
        2>/dev/null; then
      echo "FAIL: candidate offsite backup timer remains enabled after rollback"
      return 1
    fi
  fi
  DATA_UNITS_MAY_HAVE_CHANGED=""
  echo "data collection and readiness units restored to pre-deploy state"
}

trap 'release_market_mutation_lock || true; cleanup_preupdate_data_units || true; cleanup_signed_release_assets || true' EXIT
if ! capture_preupdate_data_units; then
  echo "FAIL: could not capture pre-deploy data-unit state"
  exit 1
fi
restore_market_services() {
  [ -z "$MARKET_BACKFILL_WAS_ACTIVE" ] \
    || systemctl start --no-block seiche-market-backfill.service 2>/dev/null \
    || true
  [ -z "$MARKET_WORKER_WAS_ACTIVE" ] \
    || systemctl start --no-block seiche-market-worker.service 2>/dev/null \
    || true
  if [ -n "$SOURCE_WORKER_WAS_ACTIVE" ]; then
    if ! systemctl start seiche-source-worker.service 2>/dev/null; then
      echo "FAIL: source worker did not become ready; readiness timer remains stopped"
      return 0
    fi
  fi
  if [ -n "$READINESS_TIMER_WAS_ACTIVE" ]; then
    if [ -z "$SOURCE_WORKER_WAS_ACTIVE" ]; then
      echo "FAIL: readiness timer remains stopped because source readiness is unknown"
    else
      systemctl start --no-block seiche-data-readiness.timer 2>/dev/null || true
    fi
  fi
  [ -z "$VALIDATION_TIMER_WAS_ACTIVE" ] \
    || systemctl start --no-block seiche-market-validation.timer 2>/dev/null \
    || true
  [ -z "$BACKUP_TIMER_WAS_ACTIVE" ] \
    || systemctl start --no-block seiche-market-backup.timer 2>/dev/null \
    || true
  [ -z "$RESTORE_TIMER_WAS_ACTIVE" ] \
    || systemctl start --no-block seiche-market-restore-check.timer 2>/dev/null \
    || true
  [ -z "$OFFSITE_TIMER_WAS_ACTIVE" ] \
    || ! systemctl is-enabled --quiet seiche-market-offsite-backup.timer \
      2>/dev/null \
    || systemctl start --no-block seiche-market-offsite-backup.timer \
      2>/dev/null || true
}
DATA_READINESS_PREFLIGHT_REQUIRED_UNITS="seiche-api.service seiche-market-worker.service seiche-source-worker.service seiche-market-backup.timer seiche-market-restore-check.timer seiche-market-validation.timer seiche-release-poll.timer"
DATA_READINESS_SCRIPT=/etc/seiche/libexec/seiche-data-readiness.sh
DATA_READINESS_CONVERGENCE_WAIT_SECONDS="${SEICHE_DATA_READINESS_CONVERGENCE_WAIT_SECONDS:-900}"
# Full board assembly can exceed the freshness horizon on the shared host.
# This is a fixed, reviewed controller budget rather than caller configuration.
API_FULL_REBUILD_WAIT_SECONDS=1800
run_recovery_proof_preflight() {
  /usr/bin/env -i \
    HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
    SEICHE_DATA_READINESS_PROOF_ONLY=1 \
    SEICHE_DATA_READINESS_SKIP_OFFSITE=1 \
    SEICHE_DATA_READINESS_REQUIRED_UNITS= \
    /usr/bin/bash -p "$DATA_READINESS_SCRIPT"
}
run_data_readiness_preflight() {
  /usr/bin/env -i \
    HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
    SEICHE_DATA_READINESS_SKIP_OFFSITE=1 \
    SEICHE_DATA_READINESS_REQUIRED_UNITS="$DATA_READINESS_PREFLIGHT_REQUIRED_UNITS" \
    /usr/bin/bash -p "$DATA_READINESS_SCRIPT"
}
ensure_candidate_fresh_for_readiness() {
  # A release-bound backup and restore can legitimately consume the API's
  # entire 15-minute freshness budget. Nudge the cache owner without taking
  # readers offline, then require a fresh exact-SHA handoff before acceptance.
  if ! curl -sf -m 20 http://127.0.0.1:8787/api/gauge >/dev/null; then
    echo "data readiness: cache refresh nudge failed; restarting the exact candidate"
    if ! systemctl restart seiche-api; then
      echo "FAIL: API refresh restart failed after recovery proof"
      return 1
    fi
    sleep 3
  fi
  if candidate_health_wait "$API_FULL_REBUILD_WAIT_SECONDS" "$AFTER" 900; then
    return 0
  fi
  echo "data readiness: background refresh missed its deadline; restarting the exact candidate"
  if ! systemctl restart seiche-api; then
    echo "FAIL: API fallback restart failed after recovery proof"
    return 1
  fi
  sleep 3
  if ! candidate_health_wait "$API_FULL_REBUILD_WAIT_SECONDS" "$AFTER" 900; then
    echo "FAIL: API did not produce a fresh exact candidate after recovery proof"
    return 1
  fi
}
validate_data_readiness_convergence_wait() {
  case "$DATA_READINESS_CONVERGENCE_WAIT_SECONDS" in
    0|[1-9]|[1-9][0-9]|[1-9][0-9][0-9]) ;;
    *)
      echo "FAIL: data-readiness convergence wait must be an integer from 0 to 900 seconds"
      return 1
      ;;
  esac
  if [ "$DATA_READINESS_CONVERGENCE_WAIT_SECONDS" -gt 900 ]; then
    echo "FAIL: data-readiness convergence wait must be an integer from 0 to 900 seconds"
    return 1
  fi
}
converge_operational_data_readiness() {
  local readiness_output readiness_status deadline

  if readiness_output=$(run_data_readiness_preflight 2>&1); then
    readiness_status=0
  else
    readiness_status=$?
  fi
  if [ "$readiness_status" -eq 0 ]; then
    if [ "$readiness_output" != "seiche data readiness: ready" ]; then
      printf 'FAIL: operational data readiness returned unexpected success output: %s\n' \
        "$readiness_output"
      return 1
    fi
    return 0
  fi
  if [ "$readiness_status" -ne 1 ] \
      || [ "$readiness_output" != "seiche data readiness: API snapshot stale" ]; then
    [ -z "$readiness_output" ] || printf '%s\n' "$readiness_output" >&2
    return 1
  fi

  if ! systemctl is-active --quiet seiche-api.service; then
    echo "FAIL: seiche-api is not active before stale snapshot refresh"
    return 1
  fi
  if ! /usr/bin/curl --fail --silent --show-error --proto '=http' \
      --connect-timeout 10 --max-time 10 --output /dev/null \
      'http://127.0.0.1:8787/api/gauge'; then
    echo "FAIL: stale API snapshot refresh trigger failed"
    return 1
  fi

  deadline=$((SECONDS + DATA_READINESS_CONVERGENCE_WAIT_SECONDS))
  while true; do
    if ! systemctl is-active --quiet seiche-api.service; then
      echo "FAIL: seiche-api died during stale snapshot convergence"
      return 1
    fi
    if readiness_output=$(run_data_readiness_preflight 2>&1); then
      readiness_status=0
    else
      readiness_status=$?
    fi
    if [ "$readiness_status" -eq 0 ]; then
      if [ "$readiness_output" != "seiche data readiness: ready" ]; then
        printf 'FAIL: operational data readiness returned unexpected success output: %s\n' \
          "$readiness_output"
        return 1
      fi
      return 0
    fi
    if [ "$readiness_status" -ne 1 ] \
        || [ "$readiness_output" != "seiche data readiness: API snapshot stale" ]; then
      [ -z "$readiness_output" ] || printf '%s\n' "$readiness_output" >&2
      return 1
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "FAIL: API snapshot remained stale after ${DATA_READINESS_CONVERGENCE_WAIT_SECONDS}s"
      return 1
    fi
    sleep 10
  done
}
activate_data_readiness_after_proof() {
  validate_data_readiness_convergence_wait || return 1
  # An already-current v2 backup and restore receipt avoid a redundant drill.
  # A first v2/fresh host fails this preflight and must create and restore one
  # real snapshot before the persistent timer is allowed to become active.
  if ! run_recovery_proof_preflight; then
    echo "data readiness: current v2 proof unavailable; bootstrapping backup and restore"
    if ! systemctl start seiche-market-backup.service; then
      echo "FAIL: v2 data-readiness bootstrap backup failed; readiness timer remains stopped"
      return 1
    fi
    if ! systemctl start seiche-market-restore-check.service; then
      echo "FAIL: v2 data-readiness bootstrap restore check failed; readiness timer remains stopped"
      return 1
    fi
    if ! run_recovery_proof_preflight; then
      echo "FAIL: v2 data-readiness bootstrap did not pass; readiness timer remains stopped"
      return 1
    fi
  fi
  if ! ensure_candidate_fresh_for_readiness; then
    echo "FAIL: exact candidate freshness could not be restored before readiness"
    return 1
  fi
  if ! converge_operational_data_readiness; then
    echo "FAIL: operational data readiness did not pass; readiness timer remains stopped"
    return 1
  fi
  # A background snapshot refresh publishes its in-memory board before the
  # exact release handoff finishes sealing. Readiness can therefore turn green
  # during that short interval; wait for the strict SHA-bound capability
  # instead of accepting ordinary API health without current release evidence.
  if ! candidate_health_wait 120 "$AFTER" 900; then
    echo "FAIL: exact candidate evidence did not reseal after data-readiness convergence"
    return 1
  fi
  if ! systemctl enable --now seiche-data-readiness.timer; then
    echo "FAIL: proven readiness timer could not be activated"
    return 1
  fi
}
ensure_source_worker_ready() {
  systemctl reset-failed seiche-source-worker.service 2>/dev/null || true
  if ! systemctl start seiche-source-worker.service; then
    echo "FAIL: source worker did not produce its initial durable heartbeat"
    return 1
  fi
}
start_market_services() {
  if [ -n "$MARKET_MUTATION_LOCK_HELD" ]; then
    echo "FAIL: refusing to start self-locking market services under deploy lock"
    return 1
  fi
  systemctl reset-failed \
    seiche-market-worker.service seiche-source-worker.service 2>/dev/null \
    || true
  # The worker is Type=notify and ordered after the one-shot backfill.  Wait
  # for both jobs here: a --no-block start races the readiness preflight, which
  # can mistake an activating worker for a stale recovery proof and repeat the
  # full backup/restore drill on the controller's same-SHA convergence pass.
  if ! systemctl start \
      seiche-market-backfill.service seiche-market-worker.service; then
    echo "FAIL: market backfill/worker did not become ready"
    return 1
  fi
  # Type=notify makes this block until the initial durable sweep has completed.
  # Only then submit the persistent readiness timer; its After= on the market
  # worker keeps a missed run queued until every collector startup is complete.
  ensure_source_worker_ready || return 1
  activate_data_readiness_after_proof || return 1
  if [ -n "$OFFSITE_TIMER_WAS_ACTIVE" ] \
      && systemctl is-enabled --quiet seiche-market-offsite-backup.timer; then
    systemctl start seiche-market-offsite-backup.timer || {
      echo "FAIL: offsite backup timer could not be restored after exact-SHA promotion"
      return 1
    }
  fi
}
MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=""
restore_preupdate_market_worker_unit() {
  [ -n "$MARKET_WORKER_UNIT_MAY_HAVE_CHANGED" ] || return 0
  # The worker is part of DATA_UNIT_NAMES. Its exact pre-deploy bytes and
  # enablement are restored by restore_preupdate_data_units as one bundle.
  MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=""
}
restore_preupdate_api() {
  local restore_sha="$DEPLOYED" deadline
  if ! valid_release_sha "$restore_sha"; then
    restore_sha="$BEFORE"
  fi
  if ! valid_release_sha "$restore_sha" \
      || ! "$RUNUSER" -u seiche -- git -C "$APP" rev-parse --verify --quiet \
        "$restore_sha^{commit}" >/dev/null; then
    echo "FAIL: no verified pre-update release is available to restart"
    return 1
  fi
  if ! "$RUNUSER" -u seiche -- git -C "$APP" reset -q --hard "$restore_sha" \
      || ! "$RUNUSER" -u seiche -- bash -c \
        "cd $APP && timeout -k 30 600 backend/.venv/bin/pip install -q -e './backend[notary]'" \
      || ! write_release_env "$restore_sha" \
      || ! systemctl restart seiche-api; then
    echo "FAIL: pre-update api could not be restored"
    return 1
  fi
  # Rebuilding the same board is not faster merely because this is recovery.
  # Match the candidate's reviewed full-rebuild budget so a CPU-bound but
  # progressing known-good API is not abandoned with every writer stopped.
  deadline=$((SECONDS + API_FULL_REBUILD_WAIT_SECONDS))
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
  if ! restore_preupdate_market_worker_unit; then
    echo "FAIL: market writers remain stopped because their unit recovery failed"
    return 1
  fi
  if ! restore_preupdate_data_units; then
    echo "FAIL: data workers remain stopped because their unit recovery failed"
    return 1
  fi
  if ! restore_quiesced_api; then
    echo "FAIL: market writers remain stopped because api recovery failed"
    return 1
  fi
  if ! release_market_mutation_lock; then
    echo "FAIL: market mutation lock could not be released after recovery"
    return 1
  fi
  restore_market_services
}
systemctl stop seiche-data-readiness.timer seiche-data-readiness.service \
  2>/dev/null || true
systemctl stop \
  seiche-market-validation.timer seiche-market-validation.service \
  seiche-market-backup.timer seiche-market-backup.service \
  seiche-market-offsite-backup.timer seiche-market-offsite-backup.service \
  seiche-market-restore-check.timer seiche-market-restore-check.service \
  2>/dev/null || true
systemctl stop seiche-market-worker.service seiche-market-backfill.service \
  seiche-source-worker.service \
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
if ! "$RUNUSER" -u seiche -- env SEICHE_DEPLOYED_SHA="$DEPLOYED" \
    SEICHE_UPDATE_TARGET_SHA="$TARGET" \
    bash /home/seiche/update.sh; then
  restore_pre_restart_services \
    || echo "FAIL: seiche-api needs a human after the update-gate failure"
  echo "FAIL: application update gate failed; recovery was attempted"
  exit 1
fi
AFTER=""
if ! AFTER=$("$RUNUSER" -u seiche -- git -C "$APP" rev-parse HEAD); then
  restore_pre_restart_services || true
  echo "FAIL: candidate checkout identity could not be resolved"
  exit 1
fi
if [ "$AFTER" != "$TARGET" ] \
    || ! valid_release_sha "$AFTER" \
    || ! "$RUNUSER" -u seiche -- git -C "$APP" diff-index --quiet "$AFTER" --; then
  restore_pre_restart_services || true
  echo "FAIL: candidate checkout does not exactly match its release SHA"
  exit 1
fi
UNTRACKED_IMPORTS=""
if ! UNTRACKED_IMPORTS=$(
  {
    "$RUNUSER" -u seiche -- git -C "$APP" ls-files \
      --others --exclude-standard -- backend
    "$RUNUSER" -u seiche -- git -C "$APP" ls-files \
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

# Self-sync the deploy chain from the exact target's root-owned signed asset
# tree. The running copy keeps its inode and this run never re-execs itself.
SYNC_FAIL=""
for pair in "seiche-deploy-wrapper.sh:/var/lib/seiche-deploy/bin/seiche-deploy-wrapper.sh" \
            "box-update.sh:/home/seiche/update.sh"; do
  src="$SIGNED_ASSET_ROOT/ops/deploy/${pair%%:*}"
  dst="${pair##*:}"
  dst_dir=$(dirname -- "$dst")
  dst_base=$(basename -- "$dst")
  sync_mode=0755
  if [ "$dst" = "$CANONICAL_DEPLOY_WRAPPER" ]; then
    sync_mode=0700
  fi
  sync_stage=""
  if [ ! -f "$src" ]; then
    echo "sync: signed source is missing: ${pair%%:*}"; SYNC_FAIL=1; continue
  fi
  if cmp -s "$src" "$dst"; then
    continue
  fi
  if ! bash -n "$src"; then
    echo "sync: $src fails a syntax check; keeping the installed copy"; SYNC_FAIL=1; continue
  fi
  cp "$dst" "$dst.bak-$(date +%s)" 2>/dev/null || true
  sync_stage=$(mktemp "$dst_dir/.${dst_base}.new.XXXXXX") || {
    echo "sync: could not stage $dst"; SYNC_FAIL=1; continue
  }
  if install -o root -g root -m "$sync_mode" "$src" "$sync_stage" \
      && /usr/bin/sync -f "$sync_stage" \
      && mv -f "$sync_stage" "$dst" \
      && /usr/bin/sync "$dst_dir"; then
    echo "sync: installed $dst from exact target assets (effective next deploy)"
  else
    rm -f -- "$sync_stage"
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
  local installer="$SIGNED_ASSET_ROOT/ops/deploy/install-caddy.sh"
  if [ ! -f "$installer" ]; then
    echo "FAIL: Caddy installer missing from exact target assets"
    return 1
  fi
  /usr/bin/env -i \
    HOME=/root LANG=C.UTF-8 \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    SEICHE_APP_DIR="$APP" \
    SEICHE_CADDY_SOURCE="$SIGNED_ASSET_ROOT/ops/Caddyfile" \
    /usr/bin/bash "$installer"
}

deploy_market_platform() {
  local installer="$SIGNED_ASSET_ROOT/ops/deploy/install-market-platform.sh"
  if [ ! -f "$installer" ]; then
    echo "FAIL: market-platform installer missing from exact target assets"
    return 1
  fi
  # Historical backfill can saturate the box. Install the units now, but keep
  # ingestion stopped until the candidate API and repository pass health.
  /usr/bin/env -i \
    HOME=/root LANG=C.UTF-8 \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    SEICHE_APP_DIR="$APP" \
    SEICHE_DEFER_MARKET_START=1 \
    SEICHE_NBS_RUNTIME_ROOT=/opt/seiche-nbs-intake \
    SEICHE_PRIVILEGED_ASSET_ROOT="$SIGNED_ASSET_ROOT" \
    SEICHE_RELEASE_TARGET_SHA="$TARGET" \
    /usr/bin/bash "$installer"
}

deploy_pull_unit() {
  local source="$SIGNED_ASSET_ROOT/ops/deploy/seiche-pull.service"
  local destination=/etc/systemd/system/seiche-pull.service
  local stage candidate previous had_previous=""
  if [ ! -f "$source" ]; then
    echo "FAIL: canonical pull unit missing from exact target assets"
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
  local body="$1" expected_sha="$2" max_generated_age="${3:-0}" now_epoch="${4:-}"
  /usr/bin/python3 -I -B -c '
from datetime import datetime, timezone
import json
import re
import sys
import time

try:
    expected_sha = sys.argv[2]
    max_generated_age = int(sys.argv[3])
    now_epoch = int(sys.argv[4]) if sys.argv[4] else int(time.time())
    if max_generated_age < 0 or now_epoch < 0:
        raise ValueError
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
    if max_generated_age:
        generated_at = payload.get("generated_at")
        if not isinstance(generated_at, str) or len(generated_at) > 64:
            raise ValueError
        candidate_time = datetime.fromisoformat(
            generated_at[:-1] + "+00:00"
            if generated_at.endswith(("Z", "z"))
            else generated_at
        )
        if candidate_time.tzinfo is None or candidate_time.utcoffset() is None:
            raise ValueError
        generated_epoch = candidate_time.astimezone(timezone.utc).timestamp()
        if generated_epoch > now_epoch + 300:
            raise ValueError
        if now_epoch - generated_epoch > max_generated_age:
            raise ValueError
except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
    raise SystemExit(1)
sys.stdout.write(candidate["activation_token"])
' "$body" "$expected_sha" "$max_generated_age" "$now_epoch"
}

ACTIVATION_TOKEN=""
candidate_health_once() {
  local expected_sha="$1" max_generated_age="${2:-0}" body token now_epoch
  body=$(mktemp) || return 2
  if ! curl -sf -m 10 \
      'http://127.0.0.1:8787/api/internal/v1/release-health' >"$body"; then
    rm -f -- "$body"
    # A transport error or non-2xx response is the normal not-ready state.
    return 1
  fi
  now_epoch=$(date +%s) || {
    rm -f -- "$body"
    return 2
  }
  if ! token=$(
      parse_candidate_health \
        "$body" "$expected_sha" "$max_generated_age" "$now_epoch"
  ); then
    rm -f -- "$body"
    # A successful HTTP response is an evidence claim. Wrong-SHA, stale,
    # malformed, or token-invalid claims cannot become valid by waiting.
    return 2
  fi
  rm -f -- "$body"
  ACTIVATION_TOKEN="$token"
}

candidate_health_wait() {  # candidate_health_wait SECONDS SHA [MAX_AGE] -> exact candidate
  local window="$1" expected_sha="$2" max_generated_age="${3:-0}" \
    deadline=$((SECONDS + $1)) health_status
  while true; do
    if candidate_health_once "$expected_sha" "$max_generated_age"; then
      return 0
    else
      health_status=$?
    fi
    if [ "$health_status" -eq 2 ]; then
      echo "FAIL: api returned invalid exact-release evidence during warm-up"
      return 1
    fi
    if [ "$health_status" -ne 1 ]; then
      echo "FAIL: candidate health check returned an unknown status"
      return 1
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "FAIL: api did not rebuild the exact release after $((window / 60))min warm-up window"
      return 1
    fi
    systemctl is-active --quiet seiche-api || { echo "FAIL: seiche-api died during warm-up"; return 1; }
    sleep 10
  done
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
  # The isolated wrapper runs with umask 077, so mktemp correctly creates a
  # root-only file. The validator deliberately imports candidate code as the
  # unprivileged service user; grant that user read-only group access to this
  # public API response without making the file writable or world-readable.
  if ! /usr/bin/chown root:seiche "$body" \
      || ! /usr/bin/chmod 0640 "$body"; then
    echo "FAIL: v2 coverage payload could not be prepared for unprivileged validation"
    rm -f -- "$body"
    return 1
  fi
  if ! "$RUNUSER" -u seiche -- /usr/bin/env -i \
      HOME=/home/seiche LANG=C.UTF-8 \
      PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
      PYTHONNOUSERSITE=1 \
      "$APP/backend/.venv/bin/python" -I -B -c \
      'import json,sys; from seiche.markets.registry import default_registry; p=json.load(open(sys.argv[1])); assert p["schema"] == "seiche.coverage.v2"; expected={pack.market_id for pack in default_registry().list()}; actual=[market["market_id"] for market in p["markets"]]; assert len(actual) == len(expected) and set(actual) == expected' \
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
DATA_UNITS_MAY_HAVE_CHANGED=1
acquire_market_mutation_lock || {
  restore_pre_restart_services || true
  echo "FAIL: production intake/backup serialization could not be acquired"
  exit 1
}
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
  ensure_source_worker_ready || {
    echo "FAIL: accepted release source worker did not become ready"
    exit 1
  }
  candidate_health_wait "$API_FULL_REBUILD_WAIT_SECONDS" "$AFTER" || {
    echo "FAIL: accepted release did not recover strict health; market writers remain stopped"
    exit 1
  }
  market_health || {
    release_market_mutation_lock || true
    restore_market_services
    echo "FAIL: running candidate cannot read the market repository"
    exit 1
  }
  deploy_pull_unit || {
    release_market_mutation_lock || true
    restore_market_services
    echo "FAIL: canonical pull unit could not be converged"
    exit 1
  }
  promote_snapshot_handoff || {
    release_market_mutation_lock || true
    restore_market_services
    echo "FAIL: healthy running candidate kept in place; snapshot activation needs a human"
    exit 1
  }
  release_market_mutation_lock || {
    echo "FAIL: market mutation lock could not be released"
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
  if ensure_source_worker_ready \
      && candidate_health_wait "$API_FULL_REBUILD_WAIT_SECONDS" "$AFTER"; then
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
  release_market_mutation_lock || {
    echo "FAIL: market mutation lock could not be released"
    exit 1
  }
  start_market_services || { echo "FAIL: market services could not be started"; exit 1; }
  echo "application ${AFTER:0:7} active and healthy — deploying edge config"
  deploy_caddy || { echo "FAIL: application is healthy but the Caddy deploy failed and was rolled back"; exit 1; }
  sync_verdict
  echo "deployed ${AFTER:0:7} — service active, api healthy, edge config current"
  exit 0
fi

if [ -n "$POINT_OF_NO_RETURN" ]; then
  release_market_mutation_lock || true
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
systemctl stop seiche-data-readiness.timer seiche-data-readiness.service \
  2>/dev/null || true
systemctl stop \
  seiche-market-validation.timer seiche-market-validation.service \
  seiche-market-backup.timer seiche-market-backup.service \
  seiche-market-offsite-backup.timer seiche-market-offsite-backup.service \
  seiche-market-restore-check.timer seiche-market-restore-check.service \
  2>/dev/null || true
systemctl stop seiche-market-worker.service seiche-market-backfill.service \
  seiche-source-worker.service 2>/dev/null || true
if [ -z "$DEPLOYED" ] || [ "$DEPLOYED" = "$AFTER" ]; then
  echo "FAIL: no previously-deployed sha on record to roll back to — seiche-api needs a human NOW"
  exit 1
fi
if ! valid_release_sha "$DEPLOYED"; then
  echo "FAIL: recorded deployment identity is not a canonical commit SHA — cannot roll back automatically"
  exit 1
fi
if ! "$RUNUSER" -u seiche -- git -C "$APP" rev-parse --verify --quiet "$DEPLOYED^{commit}" >/dev/null; then
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
"$RUNUSER" -u seiche -- git -C "$APP" reset -q --hard "$DEPLOYED"
"$RUNUSER" -u seiche -- bash -c "cd $APP && timeout -k 30 600 backend/.venv/bin/pip install -q -e './backend[notary]'" \
  || { echo "FAIL: rollback pip install failed or timed out — seiche-api needs a human NOW"; exit 1; }
"$RUNUSER" -u seiche -- bash -c "cd $APP && timeout -k 30 120 backend/.venv/bin/python -c 'import seiche.api, seiche.assemble, seiche.dispatch_daily'" \
  || { echo "FAIL: rollback tree does not import — seiche-api needs a human NOW"; exit 1; }
restore_preupdate_market_worker_unit \
  || { echo "FAIL: rollback worker unit could not be restored; market writers remain stopped"; exit 1; }
restore_preupdate_data_units \
  || { echo "FAIL: rollback data units could not be restored; data workers remain stopped"; exit 1; }
release_market_mutation_lock \
  || { echo "FAIL: rollback runtime lock could not be released"; exit 1; }
systemctl restart seiche-api
sleep 3
if systemctl is-active --quiet seiche-api \
    && rollback_health_wait "$API_FULL_REBUILD_WAIT_SECONDS"; then
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
