"""Import one host-verified Railway snapshot into the local release ledger."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

from seiche import assemble

SCHEMA = "seiche.railway-snapshot-result.v1"
REPOSITORY = "beepboop2025/seiche"
WORKFLOW = "beepboop2025/seiche/.github/workflows/railway-snapshot-prebuild.yml"
SOURCE_REF = "refs/heads/main"
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024 + 128 * 1024
MAX_SNAPSHOT_AGE = timedelta(hours=2)
MAX_FUTURE_SKEW = timedelta(minutes=5)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
ARTIFACT_KEYS = {
    "schema",
    "repository",
    "workflow",
    "source_ref",
    "commit",
    "tree",
    "source_archive_sha256",
    "request_id",
    "runner_provider",
    "runner_image",
    "railway_deployment_id",
    "railway_project_id",
    "railway_environment_id",
    "railway_service_id",
    "railway_replica_region",
    "started_at",
    "completed_at",
    "conclusion",
    "install_command",
    "build_command",
    "python_version",
    "dependency_snapshot_sha256",
    "payload_sha256",
    "payload_size_bytes",
    "generated_at",
    "provenance_sha256",
    "provenance_count",
    "faults_sha256",
    "fault_count",
    "payload",
}
SYSTEMD_ARTIFACT_PATH = Path("/run/seiche-release/prebuilt-snapshot.json")
SYSTEMD_RESULT_PATH = Path("/run/seiche-release/prebuilt-result.json")


def canonical_value(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def load_artifact(stream: BinaryIO) -> dict[str, object]:
    body = stream.read(MAX_ARTIFACT_BYTES + 1)
    if not body or len(body) > MAX_ARTIFACT_BYTES:
        raise ValueError("verified snapshot artifact is empty or oversized")
    try:
        artifact = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("verified snapshot artifact is invalid JSON") from exc
    if not isinstance(artifact, dict) or body != canonical_value(artifact) + b"\n":
        raise ValueError("verified snapshot artifact is not canonical JSON")
    return artifact


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"snapshot {label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"snapshot {label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"snapshot {label} is not timezone-aware")
    return parsed.astimezone(UTC)


def validate_artifact(
    artifact: Mapping[str, object],
    *,
    expected_release_sha: str,
    now: datetime | None = None,
) -> dict:
    if set(artifact) != ARTIFACT_KEYS:
        raise ValueError("snapshot artifact shape is invalid")
    expected = {
        "schema": SCHEMA,
        "repository": REPOSITORY,
        "workflow": WORKFLOW,
        "source_ref": SOURCE_REF,
        "commit": expected_release_sha,
        "runner_provider": "railway",
        "conclusion": "success",
    }
    for key, value in expected.items():
        if artifact.get(key) != value:
            raise ValueError(f"snapshot artifact {key} is invalid")
    payload = artifact.get("payload")
    if not isinstance(payload, dict):
        raise TypeError("snapshot artifact payload is invalid")
    payload_bytes = canonical_value(payload)
    if (
        artifact.get("payload_size_bytes") != len(payload_bytes)
        or not isinstance(artifact.get("payload_sha256"), str)
        or not hashlib.sha256(payload_bytes).hexdigest()
        == artifact["payload_sha256"]
    ):
        raise ValueError("snapshot artifact payload digest is invalid")
    if payload.get("generated_at") != artifact.get("generated_at"):
        raise ValueError("snapshot artifact generated_at binding is invalid")

    provenance = payload.get("provenance")
    faults = payload.get("faults")
    if not isinstance(provenance, (dict, list)) or not isinstance(faults, list):
        raise TypeError("snapshot artifact evidence shape is invalid")
    if (
        artifact.get("provenance_count") != len(provenance)
        or artifact.get("fault_count") != len(faults)
        or artifact.get("provenance_sha256")
        != hashlib.sha256(canonical_value(provenance)).hexdigest()
        or artifact.get("faults_sha256")
        != hashlib.sha256(canonical_value(faults)).hexdigest()
    ):
        raise ValueError("snapshot artifact evidence digest is invalid")
    for key in (
        "tree",
        "source_archive_sha256",
        "request_id",
        "dependency_snapshot_sha256",
        "payload_sha256",
        "provenance_sha256",
        "faults_sha256",
    ):
        value = artifact.get(key)
        expected_length = 40 if key == "tree" else 64
        pattern = r"[0-9a-f]{40}" if expected_length == 40 else r"[0-9a-f]{64}"
        if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
            raise ValueError(f"snapshot artifact {key} is invalid")

    generated_at = _timestamp(artifact.get("generated_at"), "generated_at")
    started_at = _timestamp(artifact.get("started_at"), "started_at")
    completed_at = _timestamp(artifact.get("completed_at"), "completed_at")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if not started_at <= generated_at <= completed_at:
        raise ValueError("snapshot artifact timestamps are out of order")
    if generated_at < current - MAX_SNAPSHOT_AGE:
        raise ValueError("snapshot artifact is too old for fast cutover")
    if generated_at > current + MAX_FUTURE_SKEW:
        raise ValueError("snapshot artifact is implausibly future-dated")

    assemble._assert_snapshot_rights(payload)
    if not assemble._servable_snapshot(payload):
        raise ValueError("snapshot artifact payload is not safely servable")
    return payload


def stage_artifact(
    artifact: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> str:
    release_sha = assemble.capture_process_release_sha()
    payload = validate_artifact(
        artifact,
        expected_release_sha=release_sha,
        now=now,
    )
    receipt = assemble._seal_release_evidence(payload)
    if receipt is None:
        raise RuntimeError("prebuilt snapshot could not be sealed locally")
    handoff_id = assemble._persist_pending_snapshot(payload, receipt)
    if handoff_id is None or SHA256_RE.fullmatch(handoff_id) is None:
        raise RuntimeError("prebuilt snapshot could not be staged locally")
    if not assemble.verify_pending_snapshot(release_sha, handoff_id):
        raise RuntimeError("prebuilt snapshot handoff failed local verification")
    # Seed only the already-verified public deep layer. This prevents the
    # canonical host from repeating Railway's expensive walk-forward work on
    # the same data-day; a missing/degraded SOFR boundary safely falls back to
    # the normal local build.
    assemble.seed_prebuilt_deep_cache(payload)
    return handoff_id


def _read_systemd_artifact() -> dict[str, object]:
    descriptor = os.open(
        SYSTEMD_ARTIFACT_PATH,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != 0
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != 0o640
        ):
            raise ValueError("systemd snapshot artifact metadata is unsafe")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            artifact = load_artifact(handle)
        visible = os.stat(SYSTEMD_ARTIFACT_PATH, follow_symlinks=False)
        if (visible.st_dev, visible.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("systemd snapshot artifact changed identity")
        return artifact
    finally:
        os.close(descriptor)


def _write_systemd_result(payload: Mapping[str, object]) -> None:
    body = canonical_value(payload) + b"\n"
    descriptor = os.open(
        SYSTEMD_RESULT_PATH,
        os.O_WRONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("systemd snapshot result metadata is unsafe")
        visible = os.stat(SYSTEMD_RESULT_PATH, follow_symlinks=False)
        if (visible.st_dev, visible.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("systemd snapshot result changed identity")
        os.ftruncate(descriptor, 0)
        os.write(descriptor, body)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    try:
        systemd_mode = sys.argv[1:] == ["--systemd"]
        if sys.argv[1:] not in ([], ["--systemd"]):
            raise ValueError("unsupported importer arguments")
        artifact = _read_systemd_artifact() if systemd_mode else load_artifact(
            sys.stdin.buffer
        )
        handoff_id = stage_artifact(artifact)
        result = {
            "handoff_id": handoff_id,
            "payload_sha256": artifact["payload_sha256"],
        }
        if systemd_mode:
            _write_systemd_result(result)
        else:
            print(handoff_id)
    except Exception as exc:  # noqa: BLE001 - root controller gets status only
        print(f"remote snapshot import rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
