#!/usr/bin/env python3
"""Verify an attested Railway snapshot and emit its root-local receipt."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn

REMOTE_SCHEMA = "seiche.railway-snapshot-result.v1"
LOCAL_SCHEMA = "seiche.remote-snapshot-receipt.v1"
REPOSITORY = "beepboop2025/seiche"
WORKFLOW = "beepboop2025/seiche/.github/workflows/railway-snapshot-prebuild.yml"
SOURCE_REF = "refs/heads/main"
ARTIFACT_REPOSITORY = "ghcr.io/beepboop2025/seiche-release-snapshots"
ARTIFACT_TYPE = "application/vnd.seiche.railway-snapshot-result.v1"
SNAPSHOT_MEDIA_TYPE = "application/vnd.seiche.railway-snapshot-result.v1+json"
INSTALL_COMMAND = "python -m pip install -q ./backend[collectors]"
BUILD_COMMAND = "python -I -B -m seiche.remote_snapshot_build"
RUNNER_IMAGE = (
    "docker.io/library/python:3.12.11-slim-bookworm@"
    "sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7"
)
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES = MAX_PAYLOAD_BYTES + 128 * 1024
SHA1_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
DIGEST_RE = re.compile(r"sha256:([0-9a-f]{64})")
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
REGION_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
REMOTE_KEYS = {
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


def _load_common_verifier():
    path = Path(__file__).with_name("seiche-remote-gate-verify.py")
    spec = importlib.util.spec_from_file_location("seiche_remote_gate_common", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("remote gate verification primitives cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


common = _load_common_verifier()
REGCTL = common.REGCTL
GH = common.GH
RUNUSER = common.RUNUSER
GIT = common.GIT
ENV = common.ENV


def fail(message: str) -> NoReturn:
    print(f"remote snapshot verification: {message}", file=sys.stderr)
    raise SystemExit(1)


def defer(message: str) -> NoReturn:
    print(f"remote snapshot pending: {message}", file=sys.stderr)
    raise SystemExit(75)


def canonical_value(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_json(payload: Mapping[str, object]) -> bytes:
    return canonical_value(payload) + b"\n"


def load_canonical_snapshot(body: bytes) -> dict[str, object]:
    if not body or len(body) > MAX_ARTIFACT_BYTES:
        fail("OCI snapshot is empty or oversized")
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail(f"OCI snapshot JSON is invalid: {exc}")
    if not isinstance(payload, dict) or canonical_json(payload) != body:
        fail("OCI snapshot is not canonical JSON")
    return payload


def _parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        fail(f"remote snapshot {label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail(f"remote snapshot {label} is invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        fail(f"remote snapshot {label} is not timezone-aware")
    return parsed.astimezone(UTC)


def validate_manifest(body: bytes, artifact_digest: str) -> str:
    if f"sha256:{hashlib.sha256(body).hexdigest()}" != artifact_digest:
        fail("OCI manifest bytes do not match the resolved artifact digest")
    try:
        manifest = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail(f"OCI manifest is invalid: {exc}")
    if not isinstance(manifest, dict):
        fail("OCI manifest is not an object")
    if manifest.get("schemaVersion") != 2:
        fail("OCI manifest schema is not v2")
    if manifest.get("mediaType") != "application/vnd.oci.image.manifest.v1+json":
        fail("OCI manifest media type is unexpected")
    if manifest.get("artifactType") != ARTIFACT_TYPE:
        fail("OCI artifact type is unexpected")
    config = manifest.get("config")
    if not isinstance(config, dict) or config.get("mediaType") not in {
        "application/vnd.oci.empty.v1+json",
        "application/vnd.unknown.config.v1+json",
    }:
        fail("OCI artifact config is unexpected")
    if (
        DIGEST_RE.fullmatch(str(config.get("digest", ""))) is None
        or type(config.get("size")) is not int
        or not 0 <= config["size"] <= 4096
    ):
        fail("OCI artifact config descriptor is invalid")
    layers = manifest.get("layers")
    if not isinstance(layers, list) or len(layers) != 1:
        fail("OCI artifact must contain exactly one snapshot layer")
    layer = layers[0]
    if not isinstance(layer, dict) or layer.get("mediaType") != SNAPSHOT_MEDIA_TYPE:
        fail("OCI snapshot layer media type is unexpected")
    annotations = layer.get("annotations")
    if (
        not isinstance(annotations, dict)
        or annotations.get("org.opencontainers.image.title") != "snapshot.json"
    ):
        fail("OCI snapshot layer title is missing")
    layer_digest = str(layer.get("digest", ""))
    if (
        DIGEST_RE.fullmatch(layer_digest) is None
        or type(layer.get("size")) is not int
        or not 1 <= layer["size"] <= MAX_ARTIFACT_BYTES
    ):
        fail("OCI snapshot layer descriptor is invalid")
    return layer_digest


def validate_attestation(body: bytes, artifact_digest: str) -> None:
    try:
        results = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail(f"attestation verifier output is invalid: {exc}")
    if not isinstance(results, list) or not results:
        fail("attestation verifier returned no verified statements")
    for result in results:
        try:
            subjects = result["verificationResult"]["statement"]["subject"]
        except (KeyError, TypeError):
            continue
        if not isinstance(subjects, list):
            continue
        for subject in subjects:
            if (
                isinstance(subject, dict)
                and subject.get("name") == ARTIFACT_REPOSITORY
                and isinstance(subject.get("digest"), dict)
                and f"sha256:{subject['digest'].get('sha256', '')}" == artifact_digest
            ):
                return
    fail("verified attestation does not name the exact OCI artifact digest")


def validate_remote_snapshot(
    payload: object,
    *,
    target: str,
    tree: str,
    source_archive_sha256: str,
) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != REMOTE_KEYS:
        fail("remote snapshot shape is not canonical")
    expected = {
        "schema": REMOTE_SCHEMA,
        "repository": REPOSITORY,
        "workflow": WORKFLOW,
        "source_ref": SOURCE_REF,
        "commit": target,
        "tree": tree,
        "source_archive_sha256": source_archive_sha256,
        "runner_provider": "railway",
        "runner_image": RUNNER_IMAGE,
        "conclusion": "success",
        "install_command": INSTALL_COMMAND,
        "build_command": BUILD_COMMAND,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            fail(f"remote snapshot {key} does not match the reviewed contract")
    for key in (
        "request_id",
        "dependency_snapshot_sha256",
        "payload_sha256",
        "provenance_sha256",
        "faults_sha256",
    ):
        if SHA256_RE.fullmatch(str(payload.get(key, ""))) is None:
            fail(f"remote snapshot {key} is invalid")
    if re.fullmatch(r"3\.12\.[0-9]+", str(payload.get("python_version", ""))) is None:
        fail("remote snapshot Python version is invalid")
    for key in (
        "railway_deployment_id",
        "railway_project_id",
        "railway_environment_id",
        "railway_service_id",
    ):
        if UUID_RE.fullmatch(str(payload.get(key, ""))) is None:
            fail(f"remote snapshot {key} is invalid")
    if REGION_RE.fullmatch(str(payload.get("railway_replica_region", ""))) is None:
        fail("remote snapshot Railway region is invalid")

    started = _parse_timestamp(payload.get("started_at"), "started_at")
    completed = _parse_timestamp(payload.get("completed_at"), "completed_at")
    generated = _parse_timestamp(payload.get("generated_at"), "generated_at")
    if not started <= generated <= completed or completed - started > timedelta(hours=1):
        fail("remote snapshot timestamps are invalid")

    board = payload.get("payload")
    if not isinstance(board, dict) or board.get("generated_at") != payload["generated_at"]:
        fail("remote snapshot payload binding is invalid")
    try:
        board_bytes = canonical_value(board)
    except (TypeError, ValueError) as exc:
        fail(f"remote snapshot payload is not canonical JSON: {exc}")
    if (
        not 1 <= len(board_bytes) <= MAX_PAYLOAD_BYTES
        or type(payload.get("payload_size_bytes")) is not int
        or payload["payload_size_bytes"] != len(board_bytes)
        or hashlib.sha256(board_bytes).hexdigest() != payload["payload_sha256"]
    ):
        fail("remote snapshot payload digest or size is invalid")
    provenance = board.get("provenance")
    faults = board.get("faults")
    if (
        not isinstance(provenance, (dict, list))
        or not all(isinstance(row, dict) for row in (
            provenance.values() if isinstance(provenance, dict) else provenance
        ))
        or not isinstance(faults, list)
        or not all(isinstance(row, dict) for row in faults)
    ):
        fail("remote snapshot evidence shape is invalid")
    if (
        type(payload.get("provenance_count")) is not int
        or payload["provenance_count"] != len(provenance)
        or type(payload.get("fault_count")) is not int
        or payload["fault_count"] != len(faults)
        or hashlib.sha256(canonical_value(provenance)).hexdigest()
        != payload["provenance_sha256"]
        or hashlib.sha256(canonical_value(faults)).hexdigest()
        != payload["faults_sha256"]
    ):
        fail("remote snapshot evidence digest is invalid")
    return payload


def render_local_receipt(
    remote: Mapping[str, object],
    *,
    artifact_digest: str,
    artifact_snapshot_sha256: str,
) -> dict[str, object]:
    return {
        "schema": LOCAL_SCHEMA,
        "kind": "snapshot-prebuild",
        "commit": remote["commit"],
        "tree": remote["tree"],
        "generated_at": remote["generated_at"],
        "started_at": remote["started_at"],
        "completed_at": remote["completed_at"],
        "conclusion": "success",
        "snapshot_provider": "railway",
        "payload_sha256": remote["payload_sha256"],
        "payload_size_bytes": remote["payload_size_bytes"],
        "remote": {
            "repository": REPOSITORY,
            "workflow": WORKFLOW,
            "source_ref": SOURCE_REF,
            "artifact_repository": ARTIFACT_REPOSITORY,
            "artifact_digest": artifact_digest,
            "artifact_snapshot_sha256": artifact_snapshot_sha256,
            "source_archive_sha256": remote["source_archive_sha256"],
            "request_id": remote["request_id"],
            "runner_image": RUNNER_IMAGE,
            "python_version": remote["python_version"],
            "dependency_snapshot_sha256": remote["dependency_snapshot_sha256"],
            "railway_deployment_id": remote["railway_deployment_id"],
            "railway_project_id": remote["railway_project_id"],
            "railway_environment_id": remote["railway_environment_id"],
            "railway_service_id": remote["railway_service_id"],
            "railway_replica_region": remote["railway_replica_region"],
            "provenance_sha256": remote["provenance_sha256"],
            "provenance_count": remote["provenance_count"],
            "faults_sha256": remote["faults_sha256"],
            "fault_count": remote["fault_count"],
        },
    }


def missing_artifact_error(detail: str) -> bool:
    normalized = detail.casefold()
    return any(
        marker in normalized
        for marker in ("manifest unknown", "not found [http 404]", "status code 404")
    )


def resolve_artifact_tag(tag: str, environment: Mapping[str, str]) -> str:
    try:
        result = subprocess.run(
            [str(REGCTL), "image", "digest", tag],
            env=dict(environment),
            check=False,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        fail("OCI tag resolution exceeded its 30s timeout")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if missing_artifact_error(detail):
            defer("the exact-SHA OCI snapshot has not been published yet")
        fail(f"OCI tag resolution failed: {detail}")
    if not result.stdout or len(result.stdout) > 256:
        fail("OCI tag resolution returned empty or oversized output")
    artifact_digest = result.stdout.decode("ascii", errors="strict").strip()
    if DIGEST_RE.fullmatch(artifact_digest) is None:
        fail("OCI tag resolved to an invalid digest")
    return artifact_digest


def write_exclusive(path: Path, body: bytes) -> None:
    if not path.is_absolute():
        fail("artifact output path must be absolute")
    try:
        parent = path.parent.stat(follow_symlinks=False)
    except OSError as exc:
        fail(f"artifact output directory is unavailable: {exc}")
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        fail("artifact output directory metadata is unsafe")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        fail(f"artifact output could not be created safely: {exc}")


def fetch_and_verify(
    *,
    app: Path,
    service_user: str,
    target: str,
    tree: str,
    artifact_output: Path,
) -> dict[str, object]:
    common.validate_executable(REGCTL, "regctl")
    common.validate_executable(GH, "GitHub attestation verifier")
    for executable, label in ((RUNUSER, "runuser"), (GIT, "Git"), (ENV, "env")):
        common.validate_executable(executable, label)
    source_archive_sha256 = common.verify_local_git(app, service_user, target, tree)

    with tempfile.TemporaryDirectory(prefix="seiche-remote-snapshot-") as temporary:
        environment = common.anonymous_environment(Path(temporary))
        tag = f"{ARTIFACT_REPOSITORY}:sha-{target}"
        artifact_digest = resolve_artifact_tag(tag, environment)
        reference = f"{ARTIFACT_REPOSITORY}@{artifact_digest}"
        manifest_body = common.checked_output(
            [str(REGCTL), "manifest", "get", reference, "--format", "raw-body"],
            environment,
            "OCI manifest fetch",
            maximum=128 * 1024,
        )
        layer_digest = validate_manifest(manifest_body, artifact_digest)
        snapshot_body = common.checked_output(
            [str(REGCTL), "artifact", "get", "--file", "snapshot.json", reference],
            environment,
            "OCI snapshot fetch",
            maximum=MAX_ARTIFACT_BYTES,
            timeout_seconds=180,
        )
        if f"sha256:{hashlib.sha256(snapshot_body).hexdigest()}" != layer_digest:
            fail("OCI snapshot bytes do not match the manifest layer digest")
        remote_payload = load_canonical_snapshot(snapshot_body)
        remote = validate_remote_snapshot(
            remote_payload,
            target=target,
            tree=tree,
            source_archive_sha256=source_archive_sha256,
        )
        attestation_body = common.checked_output(
            [
                str(GH),
                "attestation",
                "verify",
                f"oci://{reference}",
                "--bundle-from-oci",
                "--repo",
                REPOSITORY,
                "--signer-workflow",
                WORKFLOW,
                "--signer-digest",
                target,
                "--source-ref",
                SOURCE_REF,
                "--source-digest",
                target,
                "--deny-self-hosted-runners",
                "--format",
                "json",
            ],
            environment,
            "GitHub OIDC attestation verification",
            maximum=2 * 1024 * 1024,
        )
        validate_attestation(attestation_body, artifact_digest)
        write_exclusive(artifact_output, snapshot_body)
        return render_local_receipt(
            remote,
            artifact_digest=artifact_digest,
            artifact_snapshot_sha256=hashlib.sha256(snapshot_body).hexdigest(),
        )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--service-user", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--artifact-output", required=True, type=Path)
    arguments = parser.parse_args()
    if SHA1_RE.fullmatch(arguments.target) is None:
        parser.error("--target must be a canonical 40-character SHA-1")
    if SHA1_RE.fullmatch(arguments.tree) is None:
        parser.error("--tree must be a canonical 40-character SHA-1")
    if re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", arguments.service_user) is None:
        parser.error("--service-user is invalid")
    return arguments


def main() -> None:
    arguments = parse_arguments()
    receipt = fetch_and_verify(
        app=arguments.app,
        service_user=arguments.service_user,
        target=arguments.target,
        tree=arguments.tree,
        artifact_output=arguments.artifact_output,
    )
    sys.stdout.buffer.write(canonical_json(receipt))


if __name__ == "__main__":
    main()
