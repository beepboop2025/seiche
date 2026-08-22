#!/usr/bin/env bash
# Idempotently provision the canonical market data plane on the production box.
set -euo pipefail

APP_DIR="${SEICHE_APP_DIR:-/home/seiche/app}"
ASSET_ROOT="${SEICHE_PRIVILEGED_ASSET_ROOT:?signed privileged asset root is required}"
RELEASE_TARGET="${SEICHE_RELEASE_TARGET_SHA:?exact release target SHA is required}"
NBS_RUNTIME_ROOT="${SEICHE_NBS_RUNTIME_ROOT:?NBS runtime root is required}"
NBS_RUNTIME_PYTHON=/usr/bin/python3
NBS_RUNTIME_EXPECTED_UID=0
NBS_RUNTIME_EXPECTED_GID=0
NBS_RUNTIME_PORTABLE=0
NBS_EVIDENCE_PYTHON=/usr/bin/python3
NBS_EVIDENCE_EXPECTED_UID=0
NBS_EVIDENCE_EXPECTED_GID=0
NBS_EVIDENCE_PORTABLE=0
ENV_DIR="${SEICHE_ENV_DIR:-/etc/seiche}"
STATE_DIR="${SEICHE_MARKET_STATE_DIR:-/var/lib/seiche}"
API_DATA_DIR="${SEICHE_API_DATA_DIR:-$APP_DIR/backend/data}"
BACKUP_DIR="${SEICHE_MARKET_BACKUP_DIR:-/var/backups/seiche-market}"
NBS_STATE_DIR="${SEICHE_NBS_STATE_DIR:-/var/lib/seiche-nbs}"
NBS_RESTRICTED_DIR="$NBS_STATE_DIR/restricted"
NBS_PUBLIC_DIR="$NBS_STATE_DIR/public"
OFFSITE_ENV_FILE=/etc/seiche/offsite-backup.env
OFFSITE_PASSPHRASE_FILE=/etc/seiche/offsite-backup.passphrase
OFFSITE_CREDENTIAL_ENV_FILE=/root/.config/anchor/object-storage.env
OFFSITE_STATUS_PATH=/var/lib/seiche-offsite-backup/status.json
OFFSITE_SCRIPT_SOURCE="$ASSET_ROOT/ops/deploy/seiche-market-offsite-backup.sh"
OFFSITE_SCRIPT_INSTALLED=/etc/seiche/libexec/seiche-market-offsite-backup.sh
OFFSITE_SERVICE_SOURCE="$ASSET_ROOT/ops/deploy/seiche-market-offsite-backup.service"
OFFSITE_SERVICE_DESTINATION=/etc/systemd/system/seiche-market-offsite-backup.service
OFFSITE_TIMER_SOURCE="$ASSET_ROOT/ops/deploy/seiche-market-offsite-backup.timer"
OFFSITE_TIMER_DESTINATION=/etc/systemd/system/seiche-market-offsite-backup.timer
STORAGE_PREFLIGHT_SOURCE="$ASSET_ROOT/ops/deploy/seiche-storage-preflight.py"
STORAGE_PREFLIGHT_INSTALL_DIR=/etc/seiche/libexec
STORAGE_PREFLIGHT_INSTALLED="$STORAGE_PREFLIGHT_INSTALL_DIR/seiche-storage-preflight.py"
MARKET_BACKUP_SCRIPT_SOURCE="$ASSET_ROOT/ops/deploy/seiche-market-backup.sh"
MARKET_BACKUP_SCRIPT_INSTALLED="$STORAGE_PREFLIGHT_INSTALL_DIR/seiche-market-backup.sh"
MARKET_RESTORE_SCRIPT_SOURCE="$ASSET_ROOT/ops/deploy/seiche-market-restore-check.sh"
MARKET_RESTORE_SCRIPT_INSTALLED="$STORAGE_PREFLIGHT_INSTALL_DIR/seiche-market-restore-check.sh"
NBS_INTAKE_LAUNCHER_SOURCE="$ASSET_ROOT/ops/deploy/seiche-nbs-intake.py"
NBS_INTAKE_LAUNCHER_INSTALLED="$STORAGE_PREFLIGHT_INSTALL_DIR/seiche-nbs-intake.py"
STORAGE_PREFLIGHT_UNIT_SOURCE="$ASSET_ROOT/ops/deploy/seiche-storage-preflight.service"
STORAGE_PREFLIGHT_UNIT_DESTINATION=/etc/systemd/system/seiche-storage-preflight.service
RELEASE_POLL_STORAGE_DROPIN_DIR=/etc/systemd/system/seiche-release-poll.service.d
RELEASE_POLL_STORAGE_DROPIN=$RELEASE_POLL_STORAGE_DROPIN_DIR/storage-volume.conf
RECOVERY_PROOF_DIR=/var/lib/seiche-recovery-proof
RESTORE_STATUS_PATH=$RECOVERY_PROOF_DIR/backup-restore-check.status
EXPORT_READER_GROUP="${SEICHE_FUNDING_EXPORT_READER_GROUP:-seiche-world-model-readers}"
FUNDING_EXPORT_DIR="$STATE_DIR/exports/us-usd-funding-core-v1"
FUNDING_EXPORT_FILE="$FUNDING_EXPORT_DIR/us-usd-funding-core-v1.json"
DELIVERY_ENV_FILE="${SEICHE_WORLD_MODEL_DELIVERY_ENV_FILE:-$ENV_DIR/world-model-delivery.env}"
# Both market units load this exact root-controlled path. Keep validation and
# consumption inseparable instead of offering an override that systemd ignores.
RBNZ_ACCESS_ENV_FILE=/etc/seiche/rbnz-access.env
# CFETS collection is disabled unless these two root-controlled files form one
# content-bound approval object.  The env file names the fixed artifact and
# pins its digest; the artifact itself binds datasets, use, and expiry.
CFETS_ACCESS_ENV_FILE=/etc/seiche/cfets-access.env
CFETS_APPROVAL_FILE=/etc/seiche/cfets-approval.conf
CFETS_LICENCE_EVIDENCE_FILE=/etc/seiche/cfets-licence-evidence.pdf
# BOK ECOS embeds its individually issued key in the request path.  Provision
# it separately so the idempotent market.env rewrite can never erase or copy
# the credential into a broader service surface.
BOK_ECOS_ENV_FILE=/etc/seiche/bok-ecos.env
DELIVERY_PATH=/var/lib/liquilens-world-model/export/us-usd-funding-core-v2.json
DELIVERY_READER_GROUP=liquilens-world-model-readers
PROMOTION_REQUEST_DIR=/run/seiche-release
DEPLOY_STATE_DIR=/var/lib/seiche-deploy
PROMOTION_UNIT_SOURCE="$ASSET_ROOT/ops/deploy/seiche-snapshot-promote.service"
PROMOTION_UNIT_DESTINATION=/etc/systemd/system/seiche-snapshot-promote.service
WORKER_UNIT_SOURCE="$ASSET_ROOT/ops/deploy/seiche-market-worker.service"
WORKER_UNIT_DESTINATION=/etc/systemd/system/seiche-market-worker.service
SOURCE_WORKER_UNIT_SOURCE="$ASSET_ROOT/ops/deploy/seiche-source-worker.service"
SOURCE_WORKER_UNIT_DESTINATION=/etc/systemd/system/seiche-source-worker.service
READINESS_SERVICE_SOURCE="$ASSET_ROOT/ops/deploy/seiche-data-readiness.service"
READINESS_SERVICE_DESTINATION=/etc/systemd/system/seiche-data-readiness.service
READINESS_TIMER_SOURCE="$ASSET_ROOT/ops/deploy/seiche-data-readiness.timer"
READINESS_TIMER_DESTINATION=/etc/systemd/system/seiche-data-readiness.timer
READINESS_SCRIPT_SOURCE="$ASSET_ROOT/ops/deploy/seiche-data-readiness.sh"
READINESS_SCRIPT_INSTALLED=/etc/seiche/libexec/seiche-data-readiness.sh
LEGACY_UPDATE_RETIRER="$ASSET_ROOT/ops/deploy/retire-legacy-update-units.sh"

validate_signed_asset_root() {
    /usr/bin/python3 -I -B - "$ASSET_ROOT" "$RELEASE_TARGET" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


root_text, expected_target = sys.argv[1:]
sha_re = re.compile(r"[0-9a-f]{40}")
sha256_re = re.compile(r"[0-9a-f]{64}")
path_re = re.compile(r"[A-Za-z0-9._/-]{1,255}")
required_paths = {
    "backend/seiche/__init__.py",
    "backend/seiche/nbs_intake.py",
    "backend/seiche/nbs_trust.py",
    "ops/deploy/retire-legacy-update-units.sh",
    "ops/deploy/seiche-data-readiness.service",
    "ops/deploy/seiche-data-readiness.sh",
    "ops/deploy/seiche-data-readiness.timer",
    "ops/deploy/seiche-market-backfill.service",
    "ops/deploy/seiche-market-backup.service",
    "ops/deploy/seiche-market-backup.sh",
    "ops/deploy/seiche-market-backup.timer",
    "ops/deploy/seiche-market-offsite-backup.service",
    "ops/deploy/seiche-market-offsite-backup.sh",
    "ops/deploy/seiche-market-offsite-backup.timer",
    "ops/deploy/seiche-market-restore-check.service",
    "ops/deploy/seiche-market-restore-check.sh",
    "ops/deploy/seiche-market-restore-check.timer",
    "ops/deploy/seiche-market-validation.service",
    "ops/deploy/seiche-market-validation.timer",
    "ops/deploy/seiche-market-worker.service",
    "ops/deploy/seiche-nbs-intake.py",
    "ops/deploy/seiche-snapshot-promote.service",
    "ops/deploy/seiche-source-worker.service",
    "ops/deploy/seiche-storage-preflight.py",
    "ops/deploy/seiche-storage-preflight.service",
}


def fail(message: str) -> None:
    print(f"market platform: signed asset root {message}", file=sys.stderr)
    raise SystemExit(1)


if os.geteuid() != 0:
    fail("validation must run as root")
if (
    not root_text.startswith("/")
    or os.path.normpath(root_text) != root_text
    or root_text == "/"
    or sha_re.fullmatch(expected_target) is None
):
    fail("path or target is invalid")
root = Path(root_text)
flags = (
    os.O_RDONLY
    | os.O_DIRECTORY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
root_fd = os.open("/", flags)
try:
    for component in root.parts[1:]:
        child = os.open(component, flags, dir_fd=root_fd)
        visible = os.stat(component, dir_fd=root_fd, follow_symlinks=False)
        opened = os.fstat(child)
        if not stat.S_ISDIR(visible.st_mode) or (
            visible.st_dev,
            visible.st_ino,
        ) != (opened.st_dev, opened.st_ino):
            os.close(child)
            fail("has an unsafe path component")
        os.close(root_fd)
        root_fd = child
    root_metadata = os.fstat(root_fd)
    if (
        root_metadata.st_uid != 0
        or root_metadata.st_gid != 0
        or stat.S_IMODE(root_metadata.st_mode) != 0o700
    ):
        fail("metadata is unsafe")

    def read_root_file(name: str, maximum: int, mode: int) -> bytes:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            visible = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != 0
                or metadata.st_gid != 0
                or stat.S_IMODE(metadata.st_mode) != mode
                or (metadata.st_dev, metadata.st_ino)
                != (visible.st_dev, visible.st_ino)
                or metadata.st_size > maximum
            ):
                fail(f"metadata file is unsafe: {name}")
            body = bytearray()
            while len(body) <= maximum:
                chunk = os.read(descriptor, min(65536, maximum + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            if len(body) > maximum:
                fail(f"metadata file exceeds its bound: {name}")
            return bytes(body)
        finally:
            os.close(descriptor)

    marker = read_root_file(".target-sha", 41, 0o600)
    if marker != f"{expected_target}\n".encode("ascii"):
        fail("target marker does not match the requested release")
    try:
        manifest = json.loads(
            read_root_file(".seiche-release-assets.json", 2 * 1024 * 1024, 0o600)
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"manifest is invalid: {exc}")
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"entries", "git_object_format", "schema", "target_sha"}
        or manifest.get("schema") != "seiche.signed-privileged-assets.v1"
        or manifest.get("git_object_format") != "sha1"
        or manifest.get("target_sha") != expected_target
        or not isinstance(manifest.get("entries"), list)
        or not 1 <= len(manifest["entries"]) <= 256
    ):
        fail("manifest contract is invalid")

    expected_files = {".seiche-release-assets.json", ".target-sha"}
    seen: set[str] = set()
    for entry in manifest["entries"]:
        if not isinstance(entry, dict) or set(entry) != {
            "blob_oid",
            "git_mode",
            "path",
            "sha256",
            "size",
        }:
            fail("manifest entry shape is invalid")
        path = entry["path"]
        git_mode = entry["git_mode"]
        if (
            not isinstance(path, str)
            or path_re.fullmatch(path) is None
            or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or path in seen
            or git_mode not in {"100644", "100755"}
            or not isinstance(entry["blob_oid"], str)
            or sha_re.fullmatch(entry["blob_oid"]) is None
            or not isinstance(entry["sha256"], str)
            or sha256_re.fullmatch(entry["sha256"]) is None
            or type(entry["size"]) is not int
            or not 0 <= entry["size"] <= 16 * 1024 * 1024
        ):
            fail("manifest entry identity is invalid")
        seen.add(path)
        expected_files.add(path)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            mode = 0o755 if git_mode == "100755" else 0o644
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != 0
                or metadata.st_gid != 0
                or stat.S_IMODE(metadata.st_mode) != mode
                or metadata.st_size != entry["size"]
            ):
                fail(f"asset metadata does not match manifest: {path}")
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
            if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                fail(f"asset bytes do not match manifest: {path}")
        finally:
            os.close(descriptor)
    if not required_paths.issubset(seen):
        fail("is missing a required privileged source")

    actual_files: set[str] = set()
    for directory, directory_names, file_names in os.walk(root):
        relative_directory = Path(directory).relative_to(root)
        metadata = os.lstat(directory)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            fail("contains an unsafe directory")
        for name in directory_names:
            candidate = Path(directory) / name
            if candidate.is_symlink():
                fail("contains a symlinked directory")
        for name in file_names:
            relative = (relative_directory / name).as_posix()
            actual_files.add(relative)
    if actual_files != expected_files:
        fail("contains unmanifested or missing files")
finally:
    os.close(root_fd)
PY
}

install_nbs_runtime() {
    "$NBS_RUNTIME_PYTHON" -I -B - "$ASSET_ROOT" "$RELEASE_TARGET" \
        "$NBS_RUNTIME_ROOT" "$NBS_RUNTIME_EXPECTED_UID" \
        "$NBS_RUNTIME_EXPECTED_GID" "$NBS_RUNTIME_PORTABLE" <<'PY'
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys


(
    asset_text,
    target,
    runtime_text,
    expected_uid_text,
    expected_gid_text,
    portable_text,
) = sys.argv[1:]
expected_uid, expected_gid = int(expected_uid_text), int(expected_gid_text)
sha_re = re.compile(r"[0-9a-f]{40}")
rename_noreplace = 1
source_paths = {
    "__init__.py": Path(asset_text) / "backend/seiche/__init__.py",
    "nbs_intake.py": Path(asset_text) / "backend/seiche/nbs_intake.py",
    "nbs_trust.py": Path(asset_text) / "backend/seiche/nbs_trust.py",
}


def fail(message: str) -> None:
    print(f"market platform: NBS runtime {message}", file=sys.stderr)
    raise SystemExit(1)


def metadata_exact(
    metadata: os.stat_result, *, directory: bool, mode: int, links: int | None = None
) -> bool:
    return (
        (stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode))
        and metadata.st_uid == expected_uid
        and metadata.st_gid == expected_gid
        and stat.S_IMODE(metadata.st_mode) == mode
        and (links is None or metadata.st_nlink == links)
    )


def publish_noreplace(parent_fd: int, source: str, destination: str) -> None:
    if portable_text == "1" and not sys.platform.startswith("linux"):
        try:
            os.stat(destination, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            fail(f"destination appeared concurrently: {destination}")
        os.rename(
            source,
            destination,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        return
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        fail("requires Linux renameat2")
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
        source.encode("ascii"),
        parent_fd,
        destination.encode("ascii"),
        rename_noreplace,
    ) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            fail(f"destination appeared concurrently: {destination}")
        fail(f"atomic publication failed with errno {error}")


def open_dir_at(parent_fd: int, name: str) -> int:
    return os.open(
        name,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )


def validate_release(releases_fd: int) -> None:
    release_fd = open_dir_at(releases_fd, target)
    package_fd = -1
    try:
        if not metadata_exact(os.fstat(release_fd), directory=True, mode=0o555):
            fail("release directory metadata is unsafe")
        names = set(os.listdir(release_fd))
        if names != {"seiche"}:
            fail("release directory members are not exact")
        package_fd = open_dir_at(release_fd, "seiche")
        if not metadata_exact(os.fstat(package_fd), directory=True, mode=0o555):
            fail("package directory metadata is unsafe")
        if set(os.listdir(package_fd)) != set(source_paths):
            fail("package members are not exact")
        for name, source in source_paths.items():
            source_body = source.read_bytes()
            if len(source_body) > 512 * 1024:
                fail(f"module exceeds its runtime bound: {name}")
            descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=package_fd,
            )
            try:
                metadata = os.fstat(descriptor)
                if not metadata_exact(
                    metadata, directory=False, mode=0o444, links=1
                ):
                    fail(f"module metadata is unsafe: {name}")
                body = bytearray()
                while len(body) <= 512 * 1024:
                    chunk = os.read(descriptor, 65536)
                    if not chunk:
                        break
                    body.extend(chunk)
                if bytes(body) != source_body:
                    fail(f"module bytes differ from signed assets: {name}")
            finally:
                os.close(descriptor)
    finally:
        if package_fd >= 0:
            os.close(package_fd)
        os.close(release_fd)


if sha_re.fullmatch(target) is None or portable_text not in {"0", "1"}:
    fail("target or fixed root is invalid")
if portable_text == "0":
    if (
        os.geteuid() != 0
        or expected_uid != 0
        or expected_gid != 0
        or runtime_text != "/opt/seiche-nbs-intake"
    ):
        fail("production runtime policy is invalid")
elif (
    os.geteuid() == 0
    or os.geteuid() != expected_uid
    or os.getgid() != expected_gid
    or runtime_text == "/opt/seiche-nbs-intake"
):
    fail("portable runtime-test policy is invalid")
if (
    not runtime_text.startswith("/")
    or os.path.normpath(runtime_text) != runtime_text
    or runtime_text == "/"
    or not asset_text.startswith("/")
    or os.path.normpath(asset_text) != asset_text
    or asset_text == "/"
):
    fail("asset or runtime path is not canonical")
runtime = Path(runtime_text)
runtime_metadata = os.lstat(runtime)
if not metadata_exact(runtime_metadata, directory=True, mode=0o755):
    fail("anchor must be precreated root:root 0755")
runtime_fd = os.open(
    "/", os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
)
root_metadata = os.fstat(runtime_fd)
if (
    not stat.S_ISDIR(root_metadata.st_mode)
    or (root_metadata.st_uid, root_metadata.st_gid)
    not in {(0, 0), (expected_uid, expected_gid)}
    or stat.S_IMODE(root_metadata.st_mode) & 0o022
):
    fail("filesystem root ownership or mode is unsafe")
for component in runtime.parts[1:]:
    child = open_dir_at(runtime_fd, component)
    visible = os.stat(component, dir_fd=runtime_fd, follow_symlinks=False)
    opened = os.fstat(child)
    if (
        not stat.S_ISDIR(visible.st_mode)
        or (visible.st_dev, visible.st_ino) != (opened.st_dev, opened.st_ino)
        or (opened.st_uid, opened.st_gid)
        not in {(0, 0), (expected_uid, expected_gid)}
        or stat.S_IMODE(opened.st_mode) & 0o022
    ):
        os.close(child)
        fail("anchor ancestry ownership, mode, or identity is unsafe")
    os.close(runtime_fd)
    runtime_fd = child
if (
    not metadata_exact(os.fstat(runtime_fd), directory=True, mode=0o755)
    or (runtime_metadata.st_dev, runtime_metadata.st_ino)
    != (os.fstat(runtime_fd).st_dev, os.fstat(runtime_fd).st_ino)
):
    fail("final anchor descriptor does not match the validated path")

release_stage: str | None = None
releases_stage: str | None = None
pointer_stage: str | None = None
releases_fd = -1
try:
    for name in os.listdir(runtime_fd):
        if name.startswith((".release-stage-", ".releases-stage-", ".current-sha-")):
            fail(
                "has an interrupted stage; inspect it and remove it only if empty "
                "and root-owned"
            )
        if name not in {"current-sha", "releases"}:
            fail("anchor contains an unexpected member")

    try:
        releases_fd = open_dir_at(runtime_fd, "releases")
    except FileNotFoundError:
        releases_stage = f".releases-stage-{secrets.token_hex(16)}"
        os.mkdir(releases_stage, 0o700, dir_fd=runtime_fd)
        stage_fd = open_dir_at(runtime_fd, releases_stage)
        try:
            os.fchown(stage_fd, expected_uid, expected_gid)
            os.fchmod(stage_fd, 0o555)
            os.fsync(stage_fd)
        finally:
            os.close(stage_fd)
        os.fsync(runtime_fd)
        publish_noreplace(runtime_fd, releases_stage, "releases")
        releases_stage = None
        os.fsync(runtime_fd)
        releases_fd = open_dir_at(runtime_fd, "releases")
    if not metadata_exact(os.fstat(releases_fd), directory=True, mode=0o555):
        fail("releases directory metadata is unsafe")
    for name in os.listdir(releases_fd):
        if name.startswith(".release-stage-"):
            fail(
                "has an interrupted release stage; inspect it and remove it only "
                "if empty and root-owned"
            )
        if sha_re.fullmatch(name) is None:
            fail("releases directory contains an unexpected member")

    try:
        existing_fd = open_dir_at(releases_fd, target)
    except FileNotFoundError:
        existing_fd = -1
    if existing_fd >= 0:
        os.close(existing_fd)
        validate_release(releases_fd)
    else:
        release_stage = f".release-stage-{secrets.token_hex(16)}"
        if portable_text == "1":
            os.fchmod(releases_fd, 0o755)
        try:
            os.mkdir(release_stage, 0o700, dir_fd=releases_fd)
        finally:
            if portable_text == "1":
                os.fchmod(releases_fd, 0o555)
        stage_fd = open_dir_at(releases_fd, release_stage)
        package_fd = -1
        try:
            os.mkdir("seiche", 0o700, dir_fd=stage_fd)
            package_fd = open_dir_at(stage_fd, "seiche")
            for name, source in source_paths.items():
                body = source.read_bytes()
                if len(body) > 512 * 1024:
                    fail(f"module exceeds its runtime bound: {name}")
                descriptor = os.open(
                    name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o400,
                    dir_fd=package_fd,
                )
                try:
                    offset = 0
                    while offset < len(body):
                        written = os.write(descriptor, body[offset:])
                        if written < 1:
                            fail(f"module write made no progress: {name}")
                        offset += written
                    os.fchown(descriptor, expected_uid, expected_gid)
                    os.fchmod(descriptor, 0o444)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            os.fchown(package_fd, expected_uid, expected_gid)
            os.fchmod(package_fd, 0o555)
            os.fsync(package_fd)
            os.close(package_fd)
            package_fd = -1
            os.fchown(stage_fd, expected_uid, expected_gid)
            os.fchmod(stage_fd, 0o555)
            os.fsync(stage_fd)
        finally:
            if package_fd >= 0:
                os.close(package_fd)
            os.close(stage_fd)
        os.fsync(releases_fd)
        if portable_text == "1":
            os.fchmod(releases_fd, 0o755)
        try:
            publish_noreplace(releases_fd, release_stage, target)
        finally:
            if portable_text == "1":
                os.fchmod(releases_fd, 0o555)
        release_stage = None
        os.fsync(releases_fd)
        validate_release(releases_fd)

    release_path = runtime / "releases" / target
    if any(path.startswith("/home/") or path == "/home" for path in sys.path):
        fail("isolated system interpreter unexpectedly includes /home")
    sys.path.insert(0, str(release_path))
    try:
        import seiche.nbs_intake as nbs_intake
        import seiche.nbs_trust as nbs_trust
    except Exception as exc:
        fail(f"candidate package import failed: {exc}")
    expected_package = release_path / "seiche"
    if (
        Path(nbs_intake.__file__) != expected_package / "nbs_intake.py"
        or Path(nbs_trust.__file__) != expected_package / "nbs_trust.py"
        or not callable(nbs_trust.verify_trusted_ed25519_signature)
    ):
        fail("candidate package imported from an unexpected origin")

    if "current-sha" in os.listdir(runtime_fd):
        current_fd = os.open(
            "current-sha",
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=runtime_fd,
        )
        try:
            if not metadata_exact(
                os.fstat(current_fd), directory=False, mode=0o444, links=1
            ):
                fail("current-sha metadata is unsafe")
        finally:
            os.close(current_fd)
    pointer_stage = f".current-sha-{secrets.token_hex(16)}"
    pointer_fd = os.open(
        pointer_stage,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o400,
        dir_fd=runtime_fd,
    )
    try:
        body = f"{target}\n".encode("ascii")
        offset = 0
        while offset < len(body):
            written = os.write(pointer_fd, body[offset:])
            if written < 1:
                fail("current-sha write made no progress")
            offset += written
        os.fchown(pointer_fd, expected_uid, expected_gid)
        os.fchmod(pointer_fd, 0o444)
        os.fsync(pointer_fd)
    finally:
        os.close(pointer_fd)
    os.replace(pointer_stage, "current-sha", src_dir_fd=runtime_fd, dst_dir_fd=runtime_fd)
    pointer_stage = None
    os.fsync(runtime_fd)
finally:
    if release_stage is not None:
        if portable_text == "1" and releases_fd >= 0:
            os.fchmod(releases_fd, 0o755)
        try:
            shutil.rmtree(runtime / "releases" / release_stage)
        except FileNotFoundError:
            pass
        finally:
            if portable_text == "1" and releases_fd >= 0:
                os.fchmod(releases_fd, 0o555)
    if releases_stage is not None:
        try:
            shutil.rmtree(runtime / releases_stage)
        except FileNotFoundError:
            pass
    if pointer_stage is not None:
        try:
            os.unlink(pointer_stage, dir_fd=runtime_fd)
        except FileNotFoundError:
            pass
    if releases_fd >= 0:
        os.close(releases_fd)
    os.close(runtime_fd)
PY
}

ensure_nbs_evidence_tree() {
    "$NBS_EVIDENCE_PYTHON" -I -B - \
        "$NBS_STATE_DIR" "$SEICHE_NBS_GID" \
        "$NBS_EVIDENCE_EXPECTED_UID" "$NBS_EVIDENCE_EXPECTED_GID" \
        "$NBS_EVIDENCE_PORTABLE" <<'PY'
import ctypes
import errno
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys

root, seiche_gid_text, expected_uid_text, expected_gid_text, portable_text = sys.argv[1:]
seiche_gid = int(seiche_gid_text)
expected_uid = int(expected_uid_text)
expected_gid = int(expected_gid_text)
stage_prefix = ".seiche-nbs-stage-"
flags = (
    os.O_RDONLY
    | os.O_DIRECTORY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
RENAME_NOREPLACE = 1

if portable_text not in {"0", "1"}:
    raise SystemExit("market platform: NBS evidence portability flag is invalid")
if portable_text == "0":
    if (
        os.geteuid() != 0
        or expected_uid != 0
        or expected_gid != 0
        or root != "/var/lib/seiche-nbs"
    ):
        raise SystemExit("market platform: production NBS evidence policy is invalid")
elif (
    os.geteuid() == 0
    or os.geteuid() != expected_uid
    or os.getegid() != expected_gid
    or root == "/var/lib/seiche-nbs"
):
    raise SystemExit("market platform: portable NBS evidence policy is invalid")


def fail(message: str) -> None:
    print(f"market platform: {message}", file=sys.stderr)
    raise SystemExit(1)


def open_real_path(path: str) -> int:
    if not path.startswith("/") or os.path.normpath(path) != path or path == "/":
        fail("NBS evidence root path is not canonical")
    descriptor = os.open(
        "/", os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        for component in path.split("/")[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            visible = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            opened = os.fstat(child)
            if (
                not stat.S_ISDIR(visible.st_mode)
                or (visible.st_dev, visible.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                os.close(child)
                fail("NBS evidence root or one of its parents is unsafe")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError:
        os.close(descriptor)
        fail("NBS evidence root is absent or unsafe")


def validate(descriptor: int, uid: int, gid: int, mode: int, label: str) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        fail(f"{label} metadata is unsafe")


def publish_noreplace(parent: int, source: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        if portable_text != "1":
            fail("atomic no-replace NBS publication is unavailable")
        try:
            os.stat(destination, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            os.rename(
                source,
                destination,
                src_dir_fd=parent,
                dst_dir_fd=parent,
            )
            return
        fail(f"{destination} appeared concurrently")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if renameat2(
        parent,
        source.encode("ascii"),
        parent,
        destination.encode("ascii"),
        RENAME_NOREPLACE,
    ) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            fail(f"{destination} appeared concurrently")
        fail(f"atomic NBS publication failed with errno {error}")


def reject_orphans(parent: int, label: str) -> None:
    for name in os.listdir(parent):
        if name.startswith(stage_prefix):
            fail(
                f"{label} contains interrupted stage {name}; inspect it and "
                "remove it only when empty, root-owned, and not a mount"
            )


def existing_child(
    parent: int, name: str, uid: int, gid: int, mode: int, label: str
) -> int | None:
    try:
        child = os.open(name, flags, dir_fd=parent)
    except FileNotFoundError:
        return None
    except OSError:
        fail(f"{label} is unsafe")
    validate(child, uid, gid, mode, label)
    visible = os.stat(name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(child)
    if (visible.st_dev, visible.st_ino) != (opened.st_dev, opened.st_ino):
        os.close(child)
        fail(f"{label} changed during validation")
    return child


def stage_child(
    parent: int,
    parent_path: Path,
    name: str,
    uid: int,
    gid: int,
    mode: int,
    label: str,
    *,
    with_revisions: bool = False,
) -> int:
    reject_orphans(parent, label + " parent")
    stage = f"{stage_prefix}{name}-{secrets.token_hex(16)}"
    stage_path = parent_path / stage
    created = False
    stage_fd = -1
    revisions_fd = -1
    try:
        os.mkdir(stage, 0o700, dir_fd=parent)
        created = True
        stage_fd = os.open(stage, flags, dir_fd=parent)
        if with_revisions:
            os.mkdir("revisions", 0o700, dir_fd=stage_fd)
            revisions_fd = os.open("revisions", flags, dir_fd=stage_fd)
            os.fchown(revisions_fd, expected_uid, seiche_gid)
            os.fchmod(revisions_fd, 0o2750)
            validate(
                revisions_fd,
                expected_uid,
                seiche_gid,
                0o2750,
                "NBS public revisions root",
            )
            os.fsync(revisions_fd)
            os.close(revisions_fd)
            revisions_fd = -1
        os.fchown(stage_fd, uid, gid)
        os.fchmod(stage_fd, mode)
        validate(stage_fd, uid, gid, mode, label + " stage")
        os.fsync(stage_fd)
        os.close(stage_fd)
        stage_fd = -1
        os.fsync(parent)
        publish_noreplace(parent, stage, name)
        created = False
        os.fsync(parent)
    except OSError:
        fail(f"could not create {label} safely")
    finally:
        if revisions_fd >= 0:
            os.close(revisions_fd)
        if stage_fd >= 0:
            os.close(stage_fd)
        if created:
            try:
                shutil.rmtree(stage_path)
            except FileNotFoundError:
                pass
    child = existing_child(parent, name, uid, gid, mode, label)
    if child is None:
        fail(f"{label} was not published")
    return child


root_descriptor = open_real_path(root)
restricted_descriptor = -1
public_descriptor = -1
revisions_descriptor = -1
try:
    validate(
        root_descriptor,
        expected_uid,
        seiche_gid,
        0o750,
        "NBS evidence root",
    )
    reject_orphans(root_descriptor, "NBS evidence root")
    restricted_descriptor = existing_child(
        root_descriptor,
        "restricted",
        expected_uid,
        expected_gid,
        0o700,
        "NBS restricted root",
    )
    public_descriptor = existing_child(
        root_descriptor,
        "public",
        expected_uid,
        seiche_gid,
        0o750,
        "NBS public root",
    )
    if public_descriptor is not None:
        reject_orphans(public_descriptor, "NBS public root")
        revisions_descriptor = existing_child(
            public_descriptor,
            "revisions",
            expected_uid,
            seiche_gid,
            0o2750,
            "NBS public revisions root",
        )

    # Validate the complete pre-existing topology before publishing any missing
    # child. A malformed later child must not leave an otherwise safe partial
    # mutation behind.
    if restricted_descriptor is None:
        restricted_descriptor = stage_child(
            root_descriptor,
            Path(root),
            "restricted",
            expected_uid,
            expected_gid,
            0o700,
            "NBS restricted root",
        )
    if public_descriptor is None:
        public_descriptor = stage_child(
            root_descriptor,
            Path(root),
            "public",
            expected_uid,
            seiche_gid,
            0o750,
            "NBS public root",
            with_revisions=True,
        )
        revisions_descriptor = existing_child(
            public_descriptor,
            "revisions",
            expected_uid,
            seiche_gid,
            0o2750,
            "NBS public revisions root",
        )
        if revisions_descriptor is None:
            fail("NBS public revisions root was not published")
    if revisions_descriptor is None:
        revisions_descriptor = stage_child(
            public_descriptor,
            Path(root) / "public",
            "revisions",
            expected_uid,
            seiche_gid,
            0o2750,
            "NBS public revisions root",
        )
finally:
    for descriptor in (
        revisions_descriptor,
        public_descriptor,
        restricted_descriptor,
        root_descriptor,
    ):
        if descriptor >= 0:
            os.close(descriptor)
PY
}

case "${SEICHE_NBS_EVIDENCE_TREE_TEST_ONLY:-0}" in
0) ;;
1)
    if [ "$(/usr/bin/id -u)" -eq 0 ] \
            || [ "${SEICHE_ALLOW_NON_ROOT_INSTALL_TEST:-0}" != 1 ] \
            || [ -n "${SSH_ORIGINAL_COMMAND:-}" ] \
            || [ "${SEICHE_NBS_RUNTIME_TEST_ONLY:-0}" != 0 ]; then
        echo "market platform: evidence-tree-only mode is restricted to explicit non-root tests" >&2
        exit 1
    fi
    case "$ASSET_ROOT:$NBS_STATE_DIR" in
        /*:/*) ;;
        *)
            echo "market platform: evidence-tree-only paths must be absolute" >&2
            exit 1
            ;;
    esac
    if [ "$NBS_STATE_DIR" = /var/lib/seiche-nbs ]; then
        echo "market platform: evidence-tree-only mode must isolate its target" >&2
        exit 1
    fi
    NBS_EVIDENCE_PYTHON=${SEICHE_NBS_EVIDENCE_TEST_PYTHON:?evidence-tree test Python is required}
    case "$NBS_EVIDENCE_PYTHON" in
        /*) ;;
        *)
            echo "market platform: evidence-tree test Python must be absolute" >&2
            exit 1
            ;;
    esac
    [ -x "$NBS_EVIDENCE_PYTHON" ] || {
        echo "market platform: evidence-tree test Python is not executable" >&2
        exit 1
    }
    SEICHE_NBS_GID=${SEICHE_NBS_EVIDENCE_TEST_GID:?evidence-tree test group is required}
    case "$SEICHE_NBS_GID" in
        ''|*[!0-9]*)
            echo "market platform: evidence-tree test group must be numeric" >&2
            exit 1
            ;;
    esac
    NBS_EVIDENCE_EXPECTED_UID=$(/usr/bin/id -u)
    NBS_EVIDENCE_EXPECTED_GID=$(/usr/bin/id -g)
    if [ "$SEICHE_NBS_GID" -eq "$NBS_EVIDENCE_EXPECTED_GID" ]; then
        echo "market platform: evidence-tree test group must differ from the primary group" >&2
        exit 1
    fi
    case " $(/usr/bin/id -G) " in
        *" $SEICHE_NBS_GID "*) ;;
        *)
            echo "market platform: evidence-tree test group is not a supplementary group" >&2
            exit 1
            ;;
    esac
    NBS_EVIDENCE_PORTABLE=1
    ensure_nbs_evidence_tree
    exit 0
    ;;
*)
    echo "market platform: SEICHE_NBS_EVIDENCE_TREE_TEST_ONLY must be exactly 0 or 1" >&2
    exit 1
    ;;
esac

case "${SEICHE_NBS_RUNTIME_TEST_ONLY:-0}" in
0) ;;
1)
    if [ "$(/usr/bin/id -u)" -eq 0 ] \
            || [ "${SEICHE_ALLOW_NON_ROOT_INSTALL_TEST:-0}" != 1 ] \
            || [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
        echo "market platform: runtime-only mode is restricted to explicit non-root tests" >&2
        exit 1
    fi
    case "$ASSET_ROOT:$NBS_RUNTIME_ROOT" in
        /*:/*) ;;
        *)
            echo "market platform: runtime-only paths must be absolute" >&2
            exit 1
            ;;
    esac
    if [ "$NBS_RUNTIME_ROOT" = /opt/seiche-nbs-intake ]; then
        echo "market platform: runtime-only mode must isolate its target" >&2
        exit 1
    fi
    NBS_RUNTIME_PYTHON=${SEICHE_NBS_RUNTIME_TEST_PYTHON:?runtime test Python is required}
    case "$NBS_RUNTIME_PYTHON" in
        /*) ;;
        *)
            echo "market platform: runtime test Python must be absolute" >&2
            exit 1
            ;;
    esac
    [ -x "$NBS_RUNTIME_PYTHON" ] || {
        echo "market platform: runtime test Python is not executable" >&2
        exit 1
    }
    NBS_RUNTIME_EXPECTED_UID=$(/usr/bin/id -u)
    NBS_RUNTIME_EXPECTED_GID=$(/usr/bin/id -g)
    NBS_RUNTIME_PORTABLE=1
    install_nbs_runtime
    exit 0
    ;;
*)
    echo "market platform: SEICHE_NBS_RUNTIME_TEST_ONLY must be exactly 0 or 1" >&2
    exit 1
    ;;
esac

if [ "$(/usr/bin/id -u)" -ne 0 ]; then
    echo "market platform: installer must run as root" >&2
    exit 1
fi
if [ "$NBS_RUNTIME_ROOT" != /opt/seiche-nbs-intake ]; then
    echo "market platform: NBS runtime root is fixed at /opt/seiche-nbs-intake" >&2
    exit 1
fi
validate_signed_asset_root

if [ "$STATE_DIR" != /var/lib/seiche ] \
        || [ "$BACKUP_DIR" != /var/backups/seiche-market ] \
        || [ "$NBS_STATE_DIR" != /var/lib/seiche-nbs ]; then
    echo "market platform: guarded storage paths are fixed at /var/lib/seiche, /var/lib/seiche-nbs, and /var/backups/seiche-market" >&2
    exit 1
fi

# A missing volume otherwise leaves ordinary directories on the root disk, and
# the install -d calls below would silently begin a split-brain data plane.
# Check every binary needed to install the dedicated preflight before touching
# either guarded path.
[ -x /usr/bin/python3 ] || {
    echo "market platform: /usr/bin/python3 is required for storage preflight" >&2
    exit 1
}
[ -x /usr/bin/sync ] || {
    echo "market platform: /usr/bin/sync is required for storage preflight installation" >&2
    exit 1
}
[ -x /usr/bin/findmnt ] || {
    echo "market platform: /usr/bin/findmnt is required before storage cutover" >&2
    exit 1
}
[ -f "$STORAGE_PREFLIGHT_SOURCE" ] && [ ! -L "$STORAGE_PREFLIGHT_SOURCE" ] || {
    echo "market platform: storage preflight source is missing or unsafe" >&2
    exit 1
}
[ "$(sed -n '1p' "$NBS_INTAKE_LAUNCHER_SOURCE")" = '#!/usr/bin/python3 -I' ] || {
    echo "market platform: NBS intake launcher has the wrong interpreter" >&2
    exit 1
}
SEICHE_NBS_GID=$(/usr/bin/python3 -I -B - <<'PY'
import grp

try:
    gid = grp.getgrnam("seiche").gr_gid
except KeyError:
    raise SystemExit(1) from None
if gid <= 0:
    raise SystemExit(1)
print(gid)
PY
) || {
    echo "market platform: named seiche evidence group cannot be resolved" >&2
    exit 1
}
PACKAGES=()
if ! command -v psql >/dev/null 2>&1; then
    PACKAGES+=(postgresql)
fi
if ! command -v setfacl >/dev/null 2>&1; then
    PACKAGES+=(acl)
fi
if [ ! -x /usr/bin/setpriv ]; then
    PACKAGES+=(util-linux)
fi
if [ ! -x /usr/bin/sync ]; then
    PACKAGES+=(coreutils)
fi
if [ "${#PACKAGES[@]}" -gt 0 ]; then
    apt-get update -q
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${PACKAGES[@]}"
fi
[ -x /usr/bin/setpriv ] || {
    echo "market platform: /usr/bin/setpriv is required for sandboxed PostgreSQL backups" >&2
    exit 1
}
SYNC_VERSION=$(/usr/bin/sync --version | sed -n '1s/.* //p')
if [ ! -x /usr/bin/dpkg ] \
        || ! /usr/bin/dpkg --compare-versions "$SYNC_VERSION" ge 8.24; then
    echo "market platform: GNU coreutils sync 8.24 or newer is required" >&2
    exit 1
fi
install -d -o root -g root -m 0700 "$DEPLOY_STATE_DIR"
SEICHE_DEPLOY_STATE_DIR="$DEPLOY_STATE_DIR" \
    /usr/bin/bash "$LEGACY_UPDATE_RETIRER"

# The release poller executes this installer with PrivateDevices=true. Install
# the candidate helper and unit atomically, then ask PID 1 to run the proof in
# the guard's own device-visible sandbox. The deploy wrapper captured the prior
# bytes/absence before checkout mutation and owns restoration on any red gate.
STORAGE_PREFLIGHT_STAGE=""
STORAGE_PREFLIGHT_UNIT_STAGE_DIR=""
cleanup_early_storage_staging() {
    rm -f -- "$STORAGE_PREFLIGHT_STAGE"
    if [ -n "$STORAGE_PREFLIGHT_UNIT_STAGE_DIR" ]; then
        rm -f -- \
            "$STORAGE_PREFLIGHT_UNIT_STAGE_DIR/seiche-storage-preflight.service"
        rmdir "$STORAGE_PREFLIGHT_UNIT_STAGE_DIR" 2>/dev/null || true
    fi
}
trap cleanup_early_storage_staging EXIT
install -d -o root -g root -m 0755 "$STORAGE_PREFLIGHT_INSTALL_DIR"
STORAGE_PREFLIGHT_STAGE=$(mktemp \
    "$STORAGE_PREFLIGHT_INSTALL_DIR/.seiche-storage-preflight.XXXXXX")
install -o root -g root -m 0755 "$STORAGE_PREFLIGHT_SOURCE" \
    "$STORAGE_PREFLIGHT_STAGE"
/usr/bin/python3 -I -B "$STORAGE_PREFLIGHT_STAGE" --help >/dev/null
STORAGE_PREFLIGHT_UNIT_STAGE_DIR=$(mktemp -d \
    /etc/systemd/system/.seiche-storage-preflight-stage.XXXXXX)
install -m 0644 "$STORAGE_PREFLIGHT_UNIT_SOURCE" \
    "$STORAGE_PREFLIGHT_UNIT_STAGE_DIR/seiche-storage-preflight.service"
if ! systemd-analyze verify \
        "$STORAGE_PREFLIGHT_UNIT_STAGE_DIR/seiche-storage-preflight.service"; then
    echo "market platform: storage preflight unit failed verification" >&2
    exit 1
fi
/usr/bin/sync -f "$STORAGE_PREFLIGHT_STAGE"
/usr/bin/sync -f \
    "$STORAGE_PREFLIGHT_UNIT_STAGE_DIR/seiche-storage-preflight.service"
mv -f "$STORAGE_PREFLIGHT_STAGE" "$STORAGE_PREFLIGHT_INSTALLED"
STORAGE_PREFLIGHT_STAGE=""
/usr/bin/sync "$STORAGE_PREFLIGHT_INSTALL_DIR"
mv -f \
    "$STORAGE_PREFLIGHT_UNIT_STAGE_DIR/seiche-storage-preflight.service" \
    "$STORAGE_PREFLIGHT_UNIT_DESTINATION"
rmdir "$STORAGE_PREFLIGHT_UNIT_STAGE_DIR"
STORAGE_PREFLIGHT_UNIT_STAGE_DIR=""
/usr/bin/sync /etc/systemd/system
systemctl daemon-reload
systemctl start seiche-storage-preflight.service

# Only a proven v2 storage topology may expose a new root-owned intake runtime.
# Test the exact system interpreter API, publish/validate an inert version, then
# let install_nbs_runtime advance current-sha as its final atomic operation.
/usr/bin/env -i \
    HOME=/root LANG=C.UTF-8 PATH=/usr/bin:/bin PYTHONNOUSERSITE=1 \
    /usr/bin/python3 -I -B - <<'PY' || {
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

private_key = Ed25519PrivateKey.generate()
message = b"seiche-nbs-runtime-dependency-self-test"
private_key.public_key().verify(private_key.sign(message), message)
PY
    echo "market platform: isolated system Ed25519 runtime is unavailable" >&2
    exit 1
}
install_nbs_runtime

# The release wrapper uses umask 0077 while it checks out an exact candidate.
# Git therefore may materialize tracked executable files as seiche:seiche 0700.
# Capability-free root services cannot rely on root's DAC override to read
# those files, and the offsite service also cannot traverse /home/seiche as
# group root. Install the reviewed helpers into a root-owned runtime boundary
# instead of executing mutable checkout bytes directly from /home.
install_runtime_shell_helper() {
    local source=$1 destination=$2 label=$3 stage=""
    if [ ! -f "$source" ] || [ -L "$source" ] \
            || [ "$(stat -c '%h' "$source")" != 1 ]; then
        echo "market platform: $label source is missing or unsafe" >&2
        return 1
    fi
    stage=$(mktemp \
        "$STORAGE_PREFLIGHT_INSTALL_DIR/.${label}.XXXXXX") || return 1
    if ! install -o root -g root -m 0755 "$source" "$stage" \
            || ! /usr/bin/bash -n "$stage" \
            || ! /usr/bin/sync -f "$stage" \
            || ! mv -f -- "$stage" "$destination" \
            || ! /usr/bin/sync "$STORAGE_PREFLIGHT_INSTALL_DIR"; then
        rm -f -- "$stage"
        echo "market platform: $label installation failed" >&2
        return 1
    fi
}
install_runtime_python_helper() {
    local source=$1 destination=$2 label=$3 stage=""
    if [ ! -f "$source" ] || [ -L "$source" ] \
            || [ "$(stat -c '%h' "$source")" != 1 ]; then
        echo "market platform: $label source is missing or unsafe" >&2
        return 1
    fi
    stage=$(mktemp \
        "$STORAGE_PREFLIGHT_INSTALL_DIR/.${label}.XXXXXX") || return 1
    if ! install -o root -g root -m 0755 "$source" "$stage" \
            || ! /usr/bin/python3 -I "$stage" --help >/dev/null \
            || ! /usr/bin/sync -f "$stage" \
            || ! mv -f -- "$stage" "$destination" \
            || ! /usr/bin/sync "$STORAGE_PREFLIGHT_INSTALL_DIR"; then
        rm -f -- "$stage"
        echo "market platform: $label installation failed" >&2
        return 1
    fi
}
install_runtime_python_helper \
    "$NBS_INTAKE_LAUNCHER_SOURCE" "$NBS_INTAKE_LAUNCHER_INSTALLED" \
    seiche-nbs-intake
install_runtime_shell_helper \
    "$READINESS_SCRIPT_SOURCE" "$READINESS_SCRIPT_INSTALLED" \
    seiche-data-readiness
install_runtime_shell_helper \
    "$OFFSITE_SCRIPT_SOURCE" "$OFFSITE_SCRIPT_INSTALLED" \
    seiche-market-offsite-backup
install_runtime_shell_helper \
    "$MARKET_BACKUP_SCRIPT_SOURCE" "$MARKET_BACKUP_SCRIPT_INSTALLED" \
    seiche-market-backup
install_runtime_shell_helper \
    "$MARKET_RESTORE_SCRIPT_SOURCE" "$MARKET_RESTORE_SCRIPT_INSTALLED" \
    seiche-market-restore-check

systemctl enable --now postgresql

# Debian assigns the next free port when another local service already owns
# 5432. The production host has a Docker-published database on 5432, so the
# native cluster runs on 5433. Ask the cluster selected by pg_wrapper instead
# of assuming the default socket name.
POSTGRES_PORT=$(runuser -u postgres -- psql -tAc "SHOW port" | tr -d '[:space:]')
case "$POSTGRES_PORT" in
    ''|*[!0-9]*)
        echo "market platform: could not resolve the PostgreSQL cluster port" >&2
        exit 1
        ;;
esac
POSTGRES_VERSION_NUM=$(runuser -u postgres -- psql -tAc \
    "SHOW server_version_num" | tr -d '[:space:]')
case "$POSTGRES_VERSION_NUM" in
    ''|*[!0-9]*)
        echo "market platform: could not resolve PostgreSQL server_version_num" >&2
        exit 1
        ;;
esac
if [ "$POSTGRES_VERSION_NUM" -lt 110000 ]; then
    echo "market platform: PostgreSQL 11 or newer is required (found $POSTGRES_VERSION_NUM)" >&2
    exit 1
fi

if ! runuser -u postgres -- psql -tAc \
        "SELECT 1 FROM pg_roles WHERE rolname='seiche'" | grep -qx 1; then
    runuser -u postgres -- createuser --no-createdb --no-createrole --no-superuser seiche
fi
if ! runuser -u postgres -- psql -tAc \
        "SELECT 1 FROM pg_database WHERE datname='seiche'" | grep -qx 1; then
    runuser -u postgres -- createdb --owner=seiche seiche
fi

# The forward-chain invariant is an additive one-time migration. If any marker
# is missing, fail closed unless every process that can initialize the schema
# or append a record is already quiesced. The deploy wrapper stops the market
# daemons, but the API warmer is also a forward writer and must be stopped by
# the incident runbook before the first rollout.
FORWARD_TABLE_EXISTS=$(runuser -u postgres -- psql --no-psqlrc \
    --tuples-only --no-align --set ON_ERROR_STOP=1 --dbname=seiche \
    --command "SELECT to_regclass('public.forward_validation_records') IS NOT NULL" \
    | tr -d '[:space:]')
if [ "$FORWARD_TABLE_EXISTS" = "t" ]; then
    FORWARD_MIGRATION_MARKERS=$(runuser -u postgres -- psql --no-psqlrc \
        --tuples-only --no-align --set ON_ERROR_STOP=1 --dbname=seiche \
        --command "SELECT
          (SELECT count(*) FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name='forward_validation_records'
              AND column_name='chain_generation')::text || '|' ||
          (SELECT count(*) FROM pg_indexes
            WHERE schemaname='public'
              AND tablename='forward_validation_records'
              AND indexname='forward_records_generation')::text || '|' ||
          (SELECT count(*) FROM pg_indexes
            WHERE schemaname='public'
              AND tablename='forward_validation_records'
              AND indexname='forward_records_one_child')::text" \
        | tr -d '[:space:]')
    if [ "$FORWARD_MIGRATION_MARKERS" != "1|1|1" ]; then
        for unit in seiche-api.service seiche-market-worker.service \
                seiche-source-worker.service \
                seiche-market-backfill.service seiche-market-validation.service; do
            state=$(systemctl show "$unit" --property=ActiveState --value \
                2>/dev/null || true)
            case "$state" in
                ''|inactive|failed) ;;
                *)
                    echo "market platform: $unit must be inactive before the forward-chain migration" >&2
                    exit 1
                    ;;
            esac
        done
        if pgrep -f '/home/seiche/app/backend/.venv/bin/seiche (market-worker|market-backfill|market-collect|source-worker|source-collect)' \
                >/dev/null; then
            echo "market platform: an ad-hoc forward writer is still running" >&2
            exit 1
        fi
        V2_FORWARD_ROWS=$(runuser -u postgres -- psql --no-psqlrc \
            --tuples-only --no-align --set ON_ERROR_STOP=1 --dbname=seiche \
            --command "SELECT count(*) FROM forward_validation_records
                       WHERE calibration_id='nz-nzd-local-forward-v2'" \
            | tr -d '[:space:]')
        [ "$V2_FORWARD_ROWS" = "0" ] || {
            echo "market platform: NZ-NZD v2 rows predate the guarded migration; topology review required" >&2
            exit 1
        }
        DUPLICATE_FORWARD_CHILDREN=$(runuser -u postgres -- psql --no-psqlrc \
            --tuples-only --no-align --set ON_ERROR_STOP=1 --dbname=seiche \
            --command "SELECT count(*) FROM (
                         SELECT 1 FROM forward_validation_records
                          WHERE NOT (
                            market_id='NZ-NZD'
                            AND calibration_id='nz-nzd-local-forward-v1'
                          )
                          GROUP BY market_id, product, calibration_id,
                                   previous_record_hash
                         HAVING count(*) > 1
                       ) AS duplicate_children" | tr -d '[:space:]')
        [ "$DUPLICATE_FORWARD_CHILDREN" = "0" ] || {
            echo "market platform: duplicate forward children exist outside the NZ-NZD v1 quarantine" >&2
            exit 1
        }
    fi
fi

install -d -o seiche -g seiche -m 0750 \
    "$STATE_DIR" "$STATE_DIR/raw" "$STATE_DIR/normalized" "$STATE_DIR/backfill" \
    "$STATE_DIR/validation" "$STATE_DIR/exports" \
    "$FUNDING_EXPORT_DIR"
# Owner-supplied NBS browser exports are evidence, not market-pack inputs. Keep
# the signed raw envelope root-only and give the API read-only access solely to
# the separately materialized, metadata-only public projection. The root is an
# operator-provisioned, preflight-authenticated bind mount; routine releases
# may create missing children but never normalize an existing evidence tree.
ensure_nbs_evidence_tree
install -d -o root -g seiche -m 0750 "$ENV_DIR"
install -d -o root -g root -m 0700 "$BACKUP_DIR"
install -d -o root -g seiche -m 0750 "$RECOVERY_PROOF_DIR"
install -d -o root -g seiche -m 0750 "$PROMOTION_REQUEST_DIR"

# Give the future Lab runtime access to only the stable funding-core export.
# The group is provisioned independently of the consumer account so a Seiche
# deploy does not depend on another repository already being installed.  The
# Lab installer may add its dedicated account to this group later.  Execute-
# only ACLs on the ancestors prevent group members from listing or reading any
# other Seiche state or export.
case "$EXPORT_READER_GROUP" in
    ''|*[!a-zA-Z0-9_.-]*)
        echo "market platform: invalid funding export reader group" >&2
        exit 1
        ;;
esac
if ! getent group "$EXPORT_READER_GROUP" >/dev/null; then
    groupadd --system "$EXPORT_READER_GROUP"
fi
setfacl -m "g:$EXPORT_READER_GROUP:--x" "$STATE_DIR" "$STATE_DIR/exports"
chown seiche:"$EXPORT_READER_GROUP" "$FUNDING_EXPORT_DIR"
chmod 2750 "$FUNDING_EXPORT_DIR"
if [ -f "$FUNDING_EXPORT_FILE" ] && [ ! -L "$FUNDING_EXPORT_FILE" ]; then
    chown seiche:"$EXPORT_READER_GROUP" "$FUNDING_EXPORT_FILE"
    chmod 0640 "$FUNDING_EXPORT_FILE"
elif [ -e "$FUNDING_EXPORT_FILE" ] || [ -L "$FUNDING_EXPORT_FILE" ]; then
    echo "market platform: funding export target is not a regular file" >&2
    exit 1
fi

ENV_STAGE=$(mktemp "$ENV_DIR/.market.env.XXXXXX")
VALIDATION_STAGE=""
BACKUP_STAGE=""
RESTORE_STAGE=""
PROMOTION_UNIT_STAGE_DIR=""
WORKER_UNIT_STAGE_DIR=""
DATA_UNIT_STAGE_DIR=""
OFFSITE_CONFIGURED=0
OFFSITE_CANARY=1
STORAGE_PREFLIGHT_UNIT_STAGE_DIR=""
STORAGE_PREFLIGHT_STAGE=""
RELEASE_POLL_STORAGE_STAGE=""
cleanup() {
    rm -f -- "$ENV_STAGE" "$VALIDATION_STAGE" "$BACKUP_STAGE" "$RESTORE_STAGE" \
        "$STORAGE_PREFLIGHT_STAGE" "$RELEASE_POLL_STORAGE_STAGE"
    if [ -n "$DATA_UNIT_STAGE_DIR" ]; then
        rm -f -- \
            "$DATA_UNIT_STAGE_DIR/seiche-source-worker.service" \
            "$DATA_UNIT_STAGE_DIR/seiche-data-readiness.service" \
            "$DATA_UNIT_STAGE_DIR/seiche-data-readiness.timer" \
            "$DATA_UNIT_STAGE_DIR/seiche-market-backfill.service" \
            "$DATA_UNIT_STAGE_DIR/seiche-market-offsite-backup.service" \
            "$DATA_UNIT_STAGE_DIR/seiche-market-offsite-backup.timer"
        rmdir "$DATA_UNIT_STAGE_DIR" 2>/dev/null || true
    fi
    if [ -n "$WORKER_UNIT_STAGE_DIR" ]; then
        rm -f -- "$WORKER_UNIT_STAGE_DIR/seiche-market-worker.service"
        rmdir "$WORKER_UNIT_STAGE_DIR" 2>/dev/null || true
    fi
    if [ -n "$PROMOTION_UNIT_STAGE_DIR" ]; then
        rm -f -- "$PROMOTION_UNIT_STAGE_DIR/seiche-snapshot-promote.service"
        rmdir "$PROMOTION_UNIT_STAGE_DIR" 2>/dev/null || true
    fi
    if [ -n "$STORAGE_PREFLIGHT_UNIT_STAGE_DIR" ]; then
        rm -f -- \
            "$STORAGE_PREFLIGHT_UNIT_STAGE_DIR/seiche-storage-preflight.service"
        rmdir "$STORAGE_PREFLIGHT_UNIT_STAGE_DIR" 2>/dev/null || true
    fi
}
trap cleanup EXIT
cat >"$ENV_STAGE" <<EOF
SEICHE_DATABASE_URL=postgresql:///seiche?host=/var/run/postgresql&port=$POSTGRES_PORT
SEICHE_RAW_CAPTURE_DIR=$STATE_DIR/raw
SEICHE_NORMALIZED_DIR=$STATE_DIR/normalized
SEICHE_BACKFILL_STATE_DIR=$STATE_DIR/backfill
SEICHE_VALIDATION_DIR=$STATE_DIR/validation
SEICHE_USD_FUNDING_CORE_EXPORT_DIR=$FUNDING_EXPORT_DIR
SEICHE_CANONICAL_START=2000-01-01
EOF
chown root:seiche "$ENV_STAGE"
chmod 0640 "$ENV_STAGE"
mv -f "$ENV_STAGE" "$ENV_DIR/market.env"
ENV_STAGE=""

# RBNZ permits automated website access only after prior written approval.  A
# separately provisioned, root-controlled two-line file records the approval
# artifact hash and a bounded re-review date.  Its absence is intentional: the
# adapter then fails closed before making any RBNZ request.
if [ -e "$RBNZ_ACCESS_ENV_FILE" ] || [ -L "$RBNZ_ACCESS_ENV_FILE" ]; then
    [ -f "$RBNZ_ACCESS_ENV_FILE" ] && [ ! -L "$RBNZ_ACCESS_ENV_FILE" ] || {
        echo "market platform: RBNZ access env is not a regular file" >&2
        exit 1
    }
    [ "$(stat -c '%U:%G:%a' "$RBNZ_ACCESS_ENV_FILE")" = "root:seiche:640" ] || {
        echo "market platform: RBNZ access env ownership/mode is unsafe" >&2
        exit 1
    }
    if ! { [ "$(wc -l <"$RBNZ_ACCESS_ENV_FILE" | tr -d '[:space:]')" = "2" ] \
        && grep -Eq '^SEICHE_RBNZ_ACCESS_APPROVAL_SHA256=[0-9a-f]{64}$' \
            "$RBNZ_ACCESS_ENV_FILE" \
        && grep -Eq '^SEICHE_RBNZ_ACCESS_APPROVAL_VALID_UNTIL=[0-9]{4}-[0-9]{2}-[0-9]{2}$' \
            "$RBNZ_ACCESS_ENV_FILE"; }; then
        echo "market platform: RBNZ access env contract is invalid" >&2
        exit 1
    fi
fi

# CFETS values remain metadata-only and collection remains off by default.
# When legal approval exists, validate the exact environment/artifact pair
# before installing either writer unit.  An orphan artifact is rejected so an
# operator cannot mistake unused evidence for an enabled collection boundary.
if [ -e "$CFETS_ACCESS_ENV_FILE" ] || [ -L "$CFETS_ACCESS_ENV_FILE" ]; then
    [ -f "$CFETS_ACCESS_ENV_FILE" ] && [ ! -L "$CFETS_ACCESS_ENV_FILE" ] || {
        echo "market platform: CFETS access env is not a regular file" >&2
        exit 1
    }
    [ "$(stat -c '%U:%G:%a' "$CFETS_ACCESS_ENV_FILE")" = "root:seiche:640" ] || {
        echo "market platform: CFETS access env ownership/mode is unsafe" >&2
        exit 1
    }
    if ! { [ "$(wc -l <"$CFETS_ACCESS_ENV_FILE" | tr -d '[:space:]')" = "2" ] \
        && grep -Fqx \
            "SEICHE_CFETS_APPROVAL_PATH=$CFETS_APPROVAL_FILE" \
            "$CFETS_ACCESS_ENV_FILE" \
        && grep -Eq '^SEICHE_CFETS_APPROVAL_SHA256=[0-9a-f]{64}$' \
            "$CFETS_ACCESS_ENV_FILE"; }; then
        echo "market platform: CFETS access env contract is invalid" >&2
        exit 1
    fi
    [ -f "$CFETS_APPROVAL_FILE" ] && [ ! -L "$CFETS_APPROVAL_FILE" ] || {
        echo "market platform: CFETS approval artifact is not a regular file" >&2
        exit 1
    }
    [ "$(stat -c '%U:%G:%a:%h' "$CFETS_APPROVAL_FILE")" = "root:seiche:640:1" ] || {
        echo "market platform: CFETS approval artifact ownership/mode is unsafe" >&2
        exit 1
    }
    CFETS_APPROVAL_BYTES=$(wc -c <"$CFETS_APPROVAL_FILE" | tr -d '[:space:]')
    if [ "$CFETS_APPROVAL_BYTES" -lt 1 ] || [ "$CFETS_APPROVAL_BYTES" -gt 4096 ]; then
        echo "market platform: CFETS approval artifact size is unsafe" >&2
        exit 1
    fi
    if ! { [ "$(wc -l <"$CFETS_APPROVAL_FILE" | tr -d '[:space:]')" = "13" ] \
        && grep -Fqx 'schema=seiche.cfets-approval.v2' "$CFETS_APPROVAL_FILE" \
        && grep -Fqx 'publisher=China Foreign Exchange Trade System' \
            "$CFETS_APPROVAL_FILE" \
        && grep -Fqx \
            'endpoints=https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/fdr-settings.json,https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/fdr-chrt.csv,https://www.chinamoney.com.cn/ags/ms/cm-u-bk-shibor/ShiborHis' \
            "$CFETS_APPROVAL_FILE" \
        && grep -Fqx 'upstream_products=FDR007,SHIBOR_ON' \
            "$CFETS_APPROVAL_FILE" \
        && grep -Fqx 'canonical_outputs=CN.CFETS.FDR007,CN.CFETS.SHIBOR_ON' \
            "$CFETS_APPROVAL_FILE" \
        && grep -Fqx \
            'collection_scope=automated_bounded_fdr007_and_shibor_on_history' \
            "$CFETS_APPROVAL_FILE" \
        && grep -Fqx 'permitted_use=internal_research_only' \
            "$CFETS_APPROVAL_FILE" \
        && grep -Fqx 'publication=prohibited' "$CFETS_APPROVAL_FILE" \
        && grep -Fqx 'raw_response_retention=prohibited' \
            "$CFETS_APPROVAL_FILE" \
        && grep -Fqx 'retained_projection=event_date,value' \
            "$CFETS_APPROVAL_FILE" \
        && grep -Fqx \
            "licence_evidence_path=$CFETS_LICENCE_EVIDENCE_FILE" \
            "$CFETS_APPROVAL_FILE" \
        && grep -Eq '^licence_evidence_sha256=[0-9a-f]{64}$' \
            "$CFETS_APPROVAL_FILE" \
        && grep -Eq '^valid_until=[0-9]{4}-[0-9]{2}-[0-9]{2}$' \
            "$CFETS_APPROVAL_FILE"; }; then
        echo "market platform: CFETS approval artifact contract is invalid" >&2
        exit 1
    fi
    [ -f "$CFETS_LICENCE_EVIDENCE_FILE" ] \
        && [ ! -L "$CFETS_LICENCE_EVIDENCE_FILE" ] || {
        echo "market platform: CFETS licence evidence is not a regular file" >&2
        exit 1
    }
    [ "$(stat -c '%U:%G:%a:%h' "$CFETS_LICENCE_EVIDENCE_FILE")" = \
        "root:seiche:640:1" ] || {
        echo "market platform: CFETS licence evidence ownership/mode is unsafe" >&2
        exit 1
    }
    CFETS_EVIDENCE_BYTES=$(wc -c <"$CFETS_LICENCE_EVIDENCE_FILE" \
        | tr -d '[:space:]')
    if [ "$CFETS_EVIDENCE_BYTES" -lt 1 ] \
        || [ "$CFETS_EVIDENCE_BYTES" -gt 16777216 ]; then
        echo "market platform: CFETS licence evidence size is unsafe" >&2
        exit 1
    fi
    CFETS_EXPECTED_EVIDENCE_SHA=$(sed -n \
        's/^licence_evidence_sha256=//p' "$CFETS_APPROVAL_FILE")
    CFETS_ACTUAL_EVIDENCE_SHA=$(/usr/bin/sha256sum \
        "$CFETS_LICENCE_EVIDENCE_FILE" | cut -d ' ' -f 1)
    [ "$CFETS_ACTUAL_EVIDENCE_SHA" = "$CFETS_EXPECTED_EVIDENCE_SHA" ] || {
        echo "market platform: CFETS licence evidence digest mismatch" >&2
        exit 1
    }
    CFETS_EXPECTED_SHA=$(sed -n \
        's/^SEICHE_CFETS_APPROVAL_SHA256=//p' "$CFETS_ACCESS_ENV_FILE")
    CFETS_ACTUAL_SHA=$(/usr/bin/sha256sum "$CFETS_APPROVAL_FILE" | cut -d ' ' -f 1)
    [ "$CFETS_ACTUAL_SHA" = "$CFETS_EXPECTED_SHA" ] || {
        echo "market platform: CFETS approval artifact digest mismatch" >&2
        exit 1
    }
    CFETS_VALID_UNTIL=$(sed -n 's/^valid_until=//p' "$CFETS_APPROVAL_FILE")
    CFETS_CANONICAL_VALID_UNTIL=$(/usr/bin/date -u -d "$CFETS_VALID_UNTIL" +%F \
        2>/dev/null) || {
        echo "market platform: CFETS approval expiry is invalid" >&2
        exit 1
    }
    [ "$CFETS_CANONICAL_VALID_UNTIL" = "$CFETS_VALID_UNTIL" ] || {
        echo "market platform: CFETS approval expiry is not canonical" >&2
        exit 1
    }
    CFETS_TODAY_EPOCH=$(/usr/bin/date -u -d "$(/usr/bin/date -u +%F)" +%s)
    CFETS_VALID_UNTIL_EPOCH=$(/usr/bin/date -u -d "$CFETS_VALID_UNTIL" +%s)
    CFETS_REVIEW_DAYS=$((
        (CFETS_VALID_UNTIL_EPOCH - CFETS_TODAY_EPOCH) / 86400
    ))
    if [ "$CFETS_REVIEW_DAYS" -lt 0 ] || [ "$CFETS_REVIEW_DAYS" -gt 366 ]; then
        echo "market platform: CFETS approval review window is unsafe" >&2
        exit 1
    fi
elif [ -e "$CFETS_APPROVAL_FILE" ] || [ -L "$CFETS_APPROVAL_FILE" ] \
    || [ -e "$CFETS_LICENCE_EVIDENCE_FILE" ] \
    || [ -L "$CFETS_LICENCE_EVIDENCE_FILE" ]; then
    echo "market platform: CFETS approval artifacts have no access env pin" >&2
    exit 1
fi

# The BOK key is optional at the platform level: without it the two KR
# collectors fail closed before network access while every other market keeps
# running.  If provisioned, the file has one narrowly scoped secret and must
# already have the exact root-controlled ownership expected by systemd.
if [ -e "$BOK_ECOS_ENV_FILE" ] || [ -L "$BOK_ECOS_ENV_FILE" ]; then
    [ -f "$BOK_ECOS_ENV_FILE" ] && [ ! -L "$BOK_ECOS_ENV_FILE" ] || {
        echo "market platform: BOK ECOS env is not a regular file" >&2
        exit 1
    }
    [ "$(stat -c '%U:%G:%a' "$BOK_ECOS_ENV_FILE")" = "root:seiche:640" ] || {
        echo "market platform: BOK ECOS env ownership/mode is unsafe" >&2
        exit 1
    }
    if ! { [ "$(wc -l <"$BOK_ECOS_ENV_FILE" | tr -d '[:space:]')" = "1" ] \
        && grep -Eq '^SEICHE_BOK_ECOS_API_KEY=[A-Za-z0-9]{8,128}$' \
            "$BOK_ECOS_ENV_FILE"; }; then
        echo "market platform: BOK ECOS env contract is invalid" >&2
        exit 1
    fi
fi

# Off-node backup configuration is an all-or-none operator boundary. The
# shared Anchor credential already lives outside Git and is never copied or
# printed here; Seiche contributes only a dedicated bucket/prefix policy and a
# separately escrowed encryption passphrase. A configured canary remains
# manual, and scheduled mode requires the successful canary receipt.
if [ -e "$OFFSITE_ENV_FILE" ] || [ -L "$OFFSITE_ENV_FILE" ] \
        || [ -e "$OFFSITE_PASSPHRASE_FILE" ] \
        || [ -L "$OFFSITE_PASSPHRASE_FILE" ]; then
    [ -f "$OFFSITE_ENV_FILE" ] && [ ! -L "$OFFSITE_ENV_FILE" ] \
        && [ -f "$OFFSITE_PASSPHRASE_FILE" ] \
        && [ ! -L "$OFFSITE_PASSPHRASE_FILE" ] || {
        echo "market platform: offsite backup configuration is incomplete or unsafe" >&2
        exit 1
    }
    [ "$(stat -c '%U:%G:%a:%h' "$OFFSITE_ENV_FILE")" = root:root:600:1 ] \
        && [ "$(stat -c '%U:%G:%a:%h' "$OFFSITE_PASSPHRASE_FILE")" \
            = root:root:400:1 ] || {
        echo "market platform: offsite backup configuration ownership or mode is unsafe" >&2
        exit 1
    }
    [ -f "$OFFSITE_CREDENTIAL_ENV_FILE" ] \
        && [ ! -L "$OFFSITE_CREDENTIAL_ENV_FILE" ] \
        && [ "$(stat -c '%U:%G:%a:%h' "$OFFSITE_CREDENTIAL_ENV_FILE")" \
            = root:root:600:1 ] || {
        echo "market platform: shared Object Storage credential is missing or unsafe" >&2
        exit 1
    }
    OFFSITE_CANARY=$(/usr/bin/python3 -I -B - "$OFFSITE_ENV_FILE" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
values: dict[str, str] = {}
for line in path.read_text(encoding="utf-8").splitlines():
    if line.count("=") != 1:
        raise SystemExit(1)
    key, value = line.split("=", 1)
    if key in values:
        raise SystemExit(1)
    values[key] = value
expected = {
    "SEICHE_OFFSITE_BACKUP_BUCKET",
    "SEICHE_OFFSITE_BACKUP_PREFIX",
    "SEICHE_OFFSITE_BACKUP_RCLONE_REMOTE",
    "SEICHE_OFFSITE_BACKUP_WRITE_ENABLED",
    "SEICHE_OFFSITE_BACKUP_CANARY",
    "SEICHE_OFFSITE_BACKUP_KEY_ID",
    "SEICHE_OFFSITE_BACKUP_DESTINATION_ID",
    "SEICHE_OFFSITE_BACKUP_RETENTION_MODE",
    "SEICHE_OFFSITE_BACKUP_RETENTION_DAYS",
}
valid = (
    set(values) == expected
    and re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", values["SEICHE_OFFSITE_BACKUP_BUCKET"])
    and re.fullmatch(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*", values["SEICHE_OFFSITE_BACKUP_PREFIX"])
    and ".." not in values["SEICHE_OFFSITE_BACKUP_PREFIX"]
    and values["SEICHE_OFFSITE_BACKUP_RCLONE_REMOTE"] == "anchor"
    and values["SEICHE_OFFSITE_BACKUP_WRITE_ENABLED"] == "1"
    and values["SEICHE_OFFSITE_BACKUP_CANARY"] in {"0", "1"}
    and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}",
        values["SEICHE_OFFSITE_BACKUP_KEY_ID"],
    )
    and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}",
        values["SEICHE_OFFSITE_BACKUP_DESTINATION_ID"],
    )
    and values["SEICHE_OFFSITE_BACKUP_RETENTION_MODE"] == "COMPLIANCE"
    and values["SEICHE_OFFSITE_BACKUP_RETENTION_DAYS"] == "90"
)
if not valid:
    raise SystemExit(1)
print(values["SEICHE_OFFSITE_BACKUP_CANARY"])
PY
    ) || {
        echo "market platform: offsite backup environment contract is invalid" >&2
        exit 1
    }
    /usr/bin/python3 -I -B - "$OFFSITE_PASSPHRASE_FILE" <<'PY' || {
from pathlib import Path
import sys

body = Path(sys.argv[1]).read_bytes()
if not body.endswith(b"\n") or body.count(b"\n") != 1:
    raise SystemExit(1)
value = body[:-1]
if not 32 <= len(value) <= 4096 or b"\r" in value or b"\0" in value:
    raise SystemExit(1)
PY
        echo "market platform: offsite backup passphrase contract is invalid" >&2
        exit 1
    }
    if ! command -v gpg >/dev/null 2>&1; then
        echo "market platform: GPG with authenticated AEAD support is required" >&2
        exit 1
    fi
    GPG_OPTIONS=$(gpg --dump-options) || {
        echo "market platform: GPG runtime options cannot be inspected" >&2
        exit 1
    }
    if ! grep -Fxq -- --force-aead <<<"$GPG_OPTIONS" \
            || ! grep -Fxq -- --aead-algo <<<"$GPG_OPTIONS"; then
        echo "market platform: GPG with authenticated AEAD support is required" >&2
        exit 1
    fi
    command -v rclone >/dev/null 2>&1 || {
        echo "market platform: rclone is required for configured offsite backups" >&2
        exit 1
    }
    OFFSITE_CONFIGURED=1
fi
if systemctl is-active --quiet seiche-market-offsite-backup.service \
        2>/dev/null; then
    echo "market platform: offsite backup service must finish before unit installation" >&2
    exit 1
fi

# Fail before changing service units if the application user cannot reach the
# exact socket/port written above. pg_wrapper succeeding as postgres is not a
# substitute for validating the DSN the API and collectors will actually use.
runuser -u seiche -- /usr/bin/env -i \
    HOME=/home/seiche LANG=C.UTF-8 PATH=/usr/bin:/bin PYTHONNOUSERSITE=1 \
    SEICHE_DATABASE_URL="postgresql:///seiche?host=/var/run/postgresql&port=$POSTGRES_PORT" \
    "$APP_DIR/backend/.venv/bin/python" -I -B -c \
    'import os, psycopg; from seiche.repository import get_repository; connection = psycopg.connect(os.environ["SEICHE_DATABASE_URL"]); connection.execute("SELECT 1").fetchone(); connection.close(); get_repository().forward_record_count()'

# Readiness and watchdog semantics are a release boundary. Verify the exact
# candidate unit before replacing the running host's last-known-good template.
WORKER_UNIT_STAGE_DIR=$(mktemp -d \
    /etc/systemd/system/.seiche-market-worker-stage.XXXXXX)
install -m 0644 "$WORKER_UNIT_SOURCE" \
    "$WORKER_UNIT_STAGE_DIR/seiche-market-worker.service"
if ! systemd-analyze verify \
        "$WORKER_UNIT_STAGE_DIR/seiche-market-worker.service"; then
    echo "market platform: worker unit failed verification" >&2
    exit 1
fi
mv -f "$WORKER_UNIT_STAGE_DIR/seiche-market-worker.service" \
    "$WORKER_UNIT_DESTINATION"
rmdir "$WORKER_UNIT_STAGE_DIR"
WORKER_UNIT_STAGE_DIR=""

# The legacy source sweep and readiness monitor are one operational boundary:
# validate their exact canonical names together so timer ordering and every
# referenced service are checked before any host unit is replaced.
DATA_UNIT_STAGE_DIR=$(mktemp -d \
    /etc/systemd/system/.seiche-data-units-stage.XXXXXX)
install -m 0644 "$SOURCE_WORKER_UNIT_SOURCE" \
    "$DATA_UNIT_STAGE_DIR/seiche-source-worker.service"
install -m 0644 "$READINESS_SERVICE_SOURCE" \
    "$DATA_UNIT_STAGE_DIR/seiche-data-readiness.service"
install -m 0644 "$READINESS_TIMER_SOURCE" \
    "$DATA_UNIT_STAGE_DIR/seiche-data-readiness.timer"
install -m 0644 "$ASSET_ROOT/ops/deploy/seiche-market-backfill.service" \
    "$DATA_UNIT_STAGE_DIR/seiche-market-backfill.service"
install -m 0644 "$OFFSITE_SERVICE_SOURCE" \
    "$DATA_UNIT_STAGE_DIR/seiche-market-offsite-backup.service"
install -m 0644 "$OFFSITE_TIMER_SOURCE" \
    "$DATA_UNIT_STAGE_DIR/seiche-market-offsite-backup.timer"
if ! systemd-analyze verify \
        "$DATA_UNIT_STAGE_DIR/seiche-source-worker.service" \
        "$DATA_UNIT_STAGE_DIR/seiche-data-readiness.service" \
        "$DATA_UNIT_STAGE_DIR/seiche-data-readiness.timer" \
        "$DATA_UNIT_STAGE_DIR/seiche-market-backfill.service" \
        "$DATA_UNIT_STAGE_DIR/seiche-market-offsite-backup.service" \
        "$DATA_UNIT_STAGE_DIR/seiche-market-offsite-backup.timer"; then
    echo "market platform: data-plane units failed verification" >&2
    exit 1
fi
mv -f "$DATA_UNIT_STAGE_DIR/seiche-source-worker.service" \
    "$SOURCE_WORKER_UNIT_DESTINATION"
mv -f "$DATA_UNIT_STAGE_DIR/seiche-data-readiness.service" \
    "$READINESS_SERVICE_DESTINATION"
mv -f "$DATA_UNIT_STAGE_DIR/seiche-data-readiness.timer" \
    "$READINESS_TIMER_DESTINATION"
mv -f "$DATA_UNIT_STAGE_DIR/seiche-market-backfill.service" \
    /etc/systemd/system/seiche-market-backfill.service
mv -f "$DATA_UNIT_STAGE_DIR/seiche-market-offsite-backup.service" \
    "$OFFSITE_SERVICE_DESTINATION"
mv -f "$DATA_UNIT_STAGE_DIR/seiche-market-offsite-backup.timer" \
    "$OFFSITE_TIMER_DESTINATION"
rmdir "$DATA_UNIT_STAGE_DIR"
DATA_UNIT_STAGE_DIR=""

install -m 0644 "$ASSET_ROOT/ops/deploy/seiche-market-validation.service" \
    /etc/systemd/system/seiche-market-validation.service
install -m 0644 "$ASSET_ROOT/ops/deploy/seiche-market-validation.timer" \
    /etc/systemd/system/seiche-market-validation.timer
install -m 0644 "$ASSET_ROOT/ops/deploy/seiche-market-backup.service" \
    /etc/systemd/system/seiche-market-backup.service
install -m 0644 "$ASSET_ROOT/ops/deploy/seiche-market-backup.timer" \
    /etc/systemd/system/seiche-market-backup.timer
install -m 0644 "$ASSET_ROOT/ops/deploy/seiche-market-restore-check.service" \
    /etc/systemd/system/seiche-market-restore-check.service
install -m 0644 "$ASSET_ROOT/ops/deploy/seiche-market-restore-check.timer" \
    /etc/systemd/system/seiche-market-restore-check.timer

# Activation crosses a root-controller boundary. Verify the fixed unit under
# its canonical name before atomically installing it; it is started explicitly
# by the deploy wrapper and must never be enabled as a background job.
PROMOTION_UNIT_STAGE_DIR=$(mktemp -d \
    /etc/systemd/system/.seiche-snapshot-promote-stage.XXXXXX)
install -m 0644 "$PROMOTION_UNIT_SOURCE" \
    "$PROMOTION_UNIT_STAGE_DIR/seiche-snapshot-promote.service"
if ! systemd-analyze verify \
        "$PROMOTION_UNIT_STAGE_DIR/seiche-snapshot-promote.service"; then
    echo "market platform: snapshot promotion unit failed verification" >&2
    exit 1
fi
mv -f "$PROMOTION_UNIT_STAGE_DIR/seiche-snapshot-promote.service" \
    "$PROMOTION_UNIT_DESTINATION"
rmdir "$PROMOTION_UNIT_STAGE_DIR"
PROMOTION_UNIT_STAGE_DIR=""

# The base unit documents the default production path. A drop-in resets the
# writable sandbox to the configured state root, keeping ProtectSystem=strict
# compatible with SEICHE_MARKET_STATE_DIR overrides.
install -d -m 0755 /etc/systemd/system/seiche-market-validation.service.d
VALIDATION_STAGE=$(mktemp \
    /etc/systemd/system/seiche-market-validation.service.d/.state-path.XXXXXX)
cat >"$VALIDATION_STAGE" <<EOF
[Service]
ReadWritePaths=
ReadWritePaths=$STATE_DIR/validation
EOF
chmod 0644 "$VALIDATION_STAGE"
mv -f "$VALIDATION_STAGE" \
    /etc/systemd/system/seiche-market-validation.service.d/state-path.conf
VALIDATION_STAGE=""

# Backup units are repository templates with production defaults. Drop-ins
# keep their sandboxes exact when an operator uses supported path overrides.
install -d -m 0755 /etc/systemd/system/seiche-market-backup.service.d
BACKUP_STAGE=$(mktemp \
    /etc/systemd/system/seiche-market-backup.service.d/.paths.XXXXXX)
cat >"$BACKUP_STAGE" <<EOF
[Service]
Environment=SEICHE_APP_DIR=$APP_DIR
Environment=SEICHE_MARKET_STATE_DIR=$STATE_DIR
Environment=SEICHE_NBS_STATE_DIR=$NBS_STATE_DIR
Environment=SEICHE_API_DATA_DIR=$API_DATA_DIR
Environment=SEICHE_MARKET_BACKUP_DIR=$BACKUP_DIR
ReadOnlyPaths=
ReadOnlyPaths=$APP_DIR $STATE_DIR $NBS_STATE_DIR $API_DATA_DIR
ReadWritePaths=
ReadWritePaths=$BACKUP_DIR /run/lock
EOF
chmod 0644 "$BACKUP_STAGE"
mv -f "$BACKUP_STAGE" \
    /etc/systemd/system/seiche-market-backup.service.d/paths.conf
BACKUP_STAGE=""

install -d -m 0755 /etc/systemd/system/seiche-market-restore-check.service.d
RESTORE_STAGE=$(mktemp \
    /etc/systemd/system/seiche-market-restore-check.service.d/.paths.XXXXXX)
cat >"$RESTORE_STAGE" <<EOF
[Service]
Environment=SEICHE_APP_DIR=$APP_DIR
Environment=SEICHE_MARKET_STATE_DIR=$STATE_DIR
Environment=SEICHE_NBS_STATE_DIR=$NBS_STATE_DIR
Environment=SEICHE_MARKET_BACKUP_DIR=$BACKUP_DIR
Environment=SEICHE_RESTORE_STATUS_PATH=$RESTORE_STATUS_PATH
ReadOnlyPaths=
ReadOnlyPaths=$APP_DIR $BACKUP_DIR
ReadWritePaths=
ReadWritePaths=$RECOVERY_PROOF_DIR /run/lock
EOF
chmod 0644 "$RESTORE_STAGE"
mv -f "$RESTORE_STAGE" \
    /etc/systemd/system/seiche-market-restore-check.service.d/paths.conf
RESTORE_STAGE=""

# A relay is disabled unless the separately provisioned, root-controlled
# credential file exists.  When it does, validate its exact three-line shape
# without sourcing or printing the bearer secret, then converge only the API
# identity's membership in Lab's dedicated export-reader group.
if [ -e "$DELIVERY_ENV_FILE" ] || [ -L "$DELIVERY_ENV_FILE" ]; then
    [ -f "$DELIVERY_ENV_FILE" ] && [ ! -L "$DELIVERY_ENV_FILE" ] || {
        echo "market platform: world-model delivery env is not a regular file" >&2
        exit 1
    }
    [ "$(stat -c '%U:%G:%a' "$DELIVERY_ENV_FILE")" = "root:seiche:640" ] || {
        echo "market platform: world-model delivery env ownership/mode is unsafe" >&2
        exit 1
    }
    if ! { [ "$(wc -l <"$DELIVERY_ENV_FILE" | tr -d '[:space:]')" = "3" ] \
        && grep -Fxq "SEICHE_WORLD_MODEL_DELIVERY_PATH=$DELIVERY_PATH" \
            "$DELIVERY_ENV_FILE" \
        && grep -Eq '^SEICHE_WORLD_MODEL_DELIVERY_BEARER_TOKEN=[0-9a-f]{64}$' \
            "$DELIVERY_ENV_FILE" \
        && grep -Eq '^SEICHE_WORLD_MODEL_DELIVERY_MAX_BYTES=[0-9]+$' \
            "$DELIVERY_ENV_FILE"; }; then
        echo "market platform: world-model delivery env contract is invalid" >&2
        exit 1
    fi
    DELIVERY_MAX_BYTES=$(sed -n \
        's/^SEICHE_WORLD_MODEL_DELIVERY_MAX_BYTES=//p' "$DELIVERY_ENV_FILE")
    [ "$DELIVERY_MAX_BYTES" -ge 1 ] \
        && [ "$DELIVERY_MAX_BYTES" -le 5242880 ] || {
        echo "market platform: world-model delivery byte limit is unsafe" >&2
        exit 1
    }
    getent group "$DELIVERY_READER_GROUP" >/dev/null || {
        echo "market platform: Lab delivery reader group is not provisioned" >&2
        exit 1
    }
    if ! id -nG seiche | tr ' ' '\n' | grep -Fxq "$DELIVERY_READER_GROUP"; then
        usermod -a -G "$DELIVERY_READER_GROUP" seiche
    fi
    if [ ! -f "$DELIVERY_PATH" ] || [ -L "$DELIVERY_PATH" ]; then
        echo "market platform: exact signed Lab delivery is not readable" >&2
        exit 1
    fi
    [ "$(stat -c '%U:%G:%a' /var/lib/liquilens-world-model)" \
        = "liquilens-world-model:$DELIVERY_READER_GROUP:710" ] \
        && [ "$(stat -c '%U:%G:%a' /var/lib/liquilens-world-model/export)" \
        = "liquilens-world-model:$DELIVERY_READER_GROUP:2750" ] \
        && [ "$(stat -c '%U:%G:%a' "$DELIVERY_PATH")" \
        = "liquilens-world-model:$DELIVERY_READER_GROUP:440" ] || {
        echo "market platform: Lab delivery permission boundary is unsafe" >&2
        exit 1
    }
    if ! runuser -u seiche -- test -r "$DELIVERY_PATH"; then
        echo "market platform: exact signed Lab delivery is not readable" >&2
        exit 1
    fi
fi

# The production API unit predates this repository's unit template.  A drop-in
# adds only the shared repository environment and writable evidence root.
install -d -m 0755 /etc/systemd/system/seiche-api.service.d
DROPIN=$(mktemp /etc/systemd/system/seiche-api.service.d/.market-platform.XXXXXX)
cat >"$DROPIN" <<EOF
[Unit]
Requires=seiche-storage-preflight.service
After=seiche-storage-preflight.service
RequiresMountsFor=$STATE_DIR $NBS_STATE_DIR $BACKUP_DIR

[Service]
EnvironmentFile=-$ENV_DIR/market.env
EnvironmentFile=-$ENV_DIR/release.env
EnvironmentFile=-$DELIVERY_ENV_FILE
Environment=SEICHE_NBS_PUBLIC_DIR=$NBS_PUBLIC_DIR
# Caddy owns privacy-filtered edge request telemetry; Uvicorn's raw path logger
# includes the query string and would create a second, unredacted copy.
Environment=UVICORN_ACCESS_LOG=false
ReadWritePaths=$STATE_DIR
ReadOnlyPaths=$NBS_PUBLIC_DIR
InaccessiblePaths=$NBS_RESTRICTED_DIR
EOF
chmod 0644 "$DROPIN"
mv -f "$DROPIN" /etc/systemd/system/seiche-api.service.d/market-platform.conf

# install-release-poller.sh owns the canonical poller unit and is intentionally
# not rerun from an application release. Converge a narrow drop-in here so the
# already-installed controller joins the same mount/preflight transaction.
install -d -m 0755 "$RELEASE_POLL_STORAGE_DROPIN_DIR"
RELEASE_POLL_STORAGE_STAGE=$(mktemp \
    "$RELEASE_POLL_STORAGE_DROPIN_DIR/.storage-volume.XXXXXX")
cat >"$RELEASE_POLL_STORAGE_STAGE" <<EOF
[Unit]
Requires=seiche-storage-preflight.service
After=seiche-storage-preflight.service
RequiresMountsFor=$STATE_DIR $NBS_STATE_DIR $BACKUP_DIR
EOF
chmod 0644 "$RELEASE_POLL_STORAGE_STAGE"
mv -f "$RELEASE_POLL_STORAGE_STAGE" "$RELEASE_POLL_STORAGE_DROPIN"
RELEASE_POLL_STORAGE_STAGE=""

# Verify the complete installed candidate graph, including the generated API
# and release-poller drop-ins. Subset checks above provide an early parse gate;
# this canonical-name pass catches cross-unit ordering cycles before PID 1 sees
# any of the new dependency graph.
SYSTEMD_VERIFY_UNITS=(
    /etc/systemd/system/seiche-storage-preflight.service
    /etc/systemd/system/seiche-market-worker.service
    /etc/systemd/system/seiche-source-worker.service
    /etc/systemd/system/seiche-market-backfill.service
    /etc/systemd/system/seiche-market-validation.service
    /etc/systemd/system/seiche-market-validation.timer
    /etc/systemd/system/seiche-market-backup.service
    /etc/systemd/system/seiche-market-backup.timer
    /etc/systemd/system/seiche-market-offsite-backup.service
    /etc/systemd/system/seiche-market-offsite-backup.timer
    /etc/systemd/system/seiche-market-restore-check.service
    /etc/systemd/system/seiche-market-restore-check.timer
    /etc/systemd/system/seiche-data-readiness.service
    /etc/systemd/system/seiche-data-readiness.timer
    /etc/systemd/system/seiche-snapshot-promote.service
    /etc/systemd/system/seiche-api.service
    /etc/systemd/system/seiche-release-poll.service
    /etc/systemd/system/seiche-release-poll.timer
)
for unit in "${SYSTEMD_VERIFY_UNITS[@]}"; do
    [ -f "$unit" ] && [ ! -L "$unit" ] || {
        echo "market platform: required systemd unit is missing or unsafe: $unit" >&2
        exit 1
    }
done
if ! systemd-analyze verify "${SYSTEMD_VERIFY_UNITS[@]}"; then
    echo "market platform: combined candidate systemd graph failed verification" >&2
    exit 1
fi

systemctl daemon-reload
systemctl enable \
    seiche-market-worker.service seiche-source-worker.service
# Validation is an independent read/audit schedule. Starting the timer does not
# wait for a run and must not participate in the API/collector deploy gate.
systemctl enable --now seiche-market-validation.timer
# Backups and restore checks are independent of API deployment and never start
# a data collection. Enabling their timers is therefore safe while a candidate
# release remains behind the deploy health gate.
systemctl enable --now \
    seiche-market-backup.timer seiche-market-restore-check.timer

# Installing code is not authority for the first immutable write. A canary
# configuration keeps the timer disabled so an operator must start the service
# once and inspect its round-trip receipt. Scheduled mode is accepted only
# after that receipt exists for the configured destination. During a release,
# enablement is recorded but the timer is started only when the checkout and
# deployed receipt already reconcile; the deploy wrapper restores a
# previously-active timer after the new SHA has passed health.
offsite_canary_receipt_is_valid() {
    [ -f "$OFFSITE_STATUS_PATH" ] && [ ! -L "$OFFSITE_STATUS_PATH" ] \
        && [ "$(stat -c '%U:%G:%a:%h' "$OFFSITE_STATUS_PATH")" \
            = root:root:600:1 ] || return 1
    /usr/bin/python3 -I -B - \
        "$OFFSITE_ENV_FILE" "$OFFSITE_STATUS_PATH" <<'PY'
import json
import sys

env_path, status_path = sys.argv[1:]
settings = {}
for line in open(env_path, encoding="utf-8"):
    key, value = line.rstrip("\n").split("=", 1)
    settings[key] = value
try:
    status = json.load(open(status_path, encoding="utf-8"))
except (OSError, ValueError, TypeError):
    raise SystemExit(1)
success = status.get("last_success")
resolved_status = (
    status.get("status") == "success"
    or (
        status.get("status") == "failed"
        and status.get("remote_receipt_key") is None
        and status.get("remote_receipt_version_id") is None
    )
)
valid = (
    status.get("schema") == "seiche.market-offsite-backup-status.v2"
    and resolved_status
    and status.get("source_backup_schema") == "seiche.market-backup.v3"
    and status.get("nbs_state_root") == "/var/lib/seiche-nbs"
    and status.get("nbs_full_store_audit_contract")
        == "seiche.nbs-full-store-audit.v1"
    and status.get("nbs_full_store_audit_result") == "required_at_restore"
    and status.get("bucket") == settings["SEICHE_OFFSITE_BACKUP_BUCKET"]
    and status.get("prefix") == settings["SEICHE_OFFSITE_BACKUP_PREFIX"]
    and status.get("key_id") == settings["SEICHE_OFFSITE_BACKUP_KEY_ID"]
    and status.get("destination", {}).get("id")
        == settings["SEICHE_OFFSITE_BACKUP_DESTINATION_ID"]
    and status.get("object_lock") == {"days": 90, "mode": "COMPLIANCE"}
    and isinstance(success, dict)
    and success.get("restore_verified") is True
    and success.get("source_backup_schema") == "seiche.market-backup.v3"
    and success.get("nbs_state_root") == "/var/lib/seiche-nbs"
    and success.get("nbs_full_store_audit_contract")
        == "seiche.nbs-full-store-audit.v1"
    and success.get("nbs_full_store_audit_result") == "required_at_restore"
    and success.get("bucket") == settings["SEICHE_OFFSITE_BACKUP_BUCKET"]
    and success.get("prefix") == settings["SEICHE_OFFSITE_BACKUP_PREFIX"]
    and success.get("key_id") == settings["SEICHE_OFFSITE_BACKUP_KEY_ID"]
    and success.get("destination", {}).get("id")
        == settings["SEICHE_OFFSITE_BACKUP_DESTINATION_ID"]
    and success.get("object_lock") == {"days": 90, "mode": "COMPLIANCE"}
    and isinstance(success.get("remote_receipt_key"), str)
    and (
        success["remote_receipt_key"]
        == settings["SEICHE_OFFSITE_BACKUP_PREFIX"] + "/canary/v1/RECEIPT.json"
        or success["remote_receipt_key"].startswith(
            settings["SEICHE_OFFSITE_BACKUP_PREFIX"] + "/snapshots/"
        )
    )
    and isinstance(success.get("ciphertext_version_id"), str)
    and bool(success["ciphertext_version_id"])
    and isinstance(success.get("remote_receipt_version_id"), str)
    and bool(success["remote_receipt_version_id"])
)
raise SystemExit(0 if valid else 1)
PY
}
if [ "$OFFSITE_CONFIGURED" = 1 ] && [ "$OFFSITE_CANARY" = 0 ]; then
    offsite_canary_receipt_is_valid || {
        echo "market platform: scheduled offsite backup lacks a valid canary receipt" >&2
        exit 1
    }
    systemctl enable seiche-market-offsite-backup.timer
    OFFSITE_APP_SHA=$RELEASE_TARGET
    OFFSITE_DEPLOYED_SHA=$(tr -d '\n' <"$DEPLOY_STATE_DIR/deployed-sha" \
        2>/dev/null || true)
    if [ "$OFFSITE_APP_SHA" = "$OFFSITE_DEPLOYED_SHA" ] \
            && printf '%s' "$OFFSITE_APP_SHA" \
                | grep -Eq '^[0-9a-f]{40}$'; then
        systemctl start seiche-market-offsite-backup.timer
    fi
else
    systemctl disable --now seiche-market-offsite-backup.timer \
        >/dev/null 2>&1 || true
    if systemctl is-enabled --quiet seiche-market-offsite-backup.timer \
            2>/dev/null \
            || systemctl is-active --quiet seiche-market-offsite-backup.timer \
                2>/dev/null; then
        echo "market platform: unproven offsite backup timer is still active or enabled" >&2
        exit 1
    fi
fi
# The source worker is Type=notify and reports READY only after its first
# durable sweep. Keep the persistent readiness timer stopped until that gate
# succeeds and backup/restore readiness has been proven, so an overdue
# Persistent run cannot page during planned startup.
DATA_READINESS_PREFLIGHT_REQUIRED_UNITS="seiche-api.service seiche-market-worker.service seiche-source-worker.service seiche-market-backup.timer seiche-market-restore-check.timer seiche-market-validation.timer seiche-release-poll.timer"
DATA_READINESS_SCRIPT="$READINESS_SCRIPT_INSTALLED"
DATA_READINESS_CONVERGENCE_WAIT_SECONDS="${SEICHE_DATA_READINESS_CONVERGENCE_WAIT_SECONDS:-900}"
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
validate_data_readiness_convergence_wait() {
    case "$DATA_READINESS_CONVERGENCE_WAIT_SECONDS" in
        0|[1-9]|[1-9][0-9]|[1-9][0-9][0-9]) ;;
        *)
            echo "market platform: data-readiness convergence wait must be an integer from 0 to 900 seconds" >&2
            return 1
            ;;
    esac
    if [ "$DATA_READINESS_CONVERGENCE_WAIT_SECONDS" -gt 900 ]; then
        echo "market platform: data-readiness convergence wait must be an integer from 0 to 900 seconds" >&2
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
            printf 'market platform: operational readiness returned unexpected success output: %s\n' \
                "$readiness_output" >&2
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
        echo "market platform: seiche-api is not active before stale snapshot refresh" >&2
        return 1
    fi
    if ! /usr/bin/curl --fail --silent --show-error --proto '=http' \
            --connect-timeout 10 --max-time 10 --output /dev/null \
            'http://127.0.0.1:8787/api/gauge'; then
        echo "market platform: stale API snapshot refresh trigger failed" >&2
        return 1
    fi

    deadline=$((SECONDS + DATA_READINESS_CONVERGENCE_WAIT_SECONDS))
    while true; do
        if ! systemctl is-active --quiet seiche-api.service; then
            echo "market platform: seiche-api died during stale snapshot convergence" >&2
            return 1
        fi
        if readiness_output=$(run_data_readiness_preflight 2>&1); then
            readiness_status=0
        else
            readiness_status=$?
        fi
        if [ "$readiness_status" -eq 0 ]; then
            if [ "$readiness_output" != "seiche data readiness: ready" ]; then
                printf 'market platform: operational readiness returned unexpected success output: %s\n' \
                    "$readiness_output" >&2
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
            echo "market platform: API snapshot remained stale after ${DATA_READINESS_CONVERGENCE_WAIT_SECONDS}s" >&2
            return 1
        fi
        sleep 10
    done
}
activate_data_readiness_after_proof() {
    validate_data_readiness_convergence_wait || return 1
    if ! run_recovery_proof_preflight; then
        echo "data readiness: current v2 proof unavailable; bootstrapping backup and restore"
        if ! systemctl start seiche-market-backup.service; then
            echo "market platform: v2 readiness bootstrap backup failed; timer remains stopped" >&2
            return 1
        fi
        if ! systemctl start seiche-market-restore-check.service; then
            echo "market platform: v2 readiness bootstrap restore check failed; timer remains stopped" >&2
            return 1
        fi
        if ! run_recovery_proof_preflight; then
            echo "market platform: v2 readiness bootstrap failed; timer remains stopped" >&2
            return 1
        fi
    fi
    if ! converge_operational_data_readiness; then
        echo "market platform: operational readiness failed; timer remains stopped" >&2
        return 1
    fi
    if ! systemctl enable --now seiche-data-readiness.timer; then
        echo "market platform: proven readiness timer could not be activated" >&2
        return 1
    fi
}
if [ "${SEICHE_DEFER_MARKET_START:-0}" != "1" ]; then
    # Wait for the Type=notify market worker (and its ordered one-shot
    # backfill) before evaluating readiness. Otherwise a valid fresh recovery
    # receipt can trigger a redundant backup/restore drill during activation.
    systemctl start \
        seiche-market-backfill.service seiche-market-worker.service
    systemctl start seiche-source-worker.service
    activate_data_readiness_after_proof
fi

echo "market platform: PostgreSQL on socket port $POSTGRES_PORT, narrow funding export ACL, evidence directories, backups, source collection and readiness units ready"
