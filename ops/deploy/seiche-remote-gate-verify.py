#!/usr/bin/env python3
"""Verify an attested Railway gate and render the root-local gate receipt."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import NoReturn


REMOTE_SCHEMA = "seiche.railway-gate-result.v1"
LOCAL_SCHEMA = "seiche.release-receipt.v2"
REPOSITORY = "beepboop2025/seiche"
WORKFLOW = "beepboop2025/seiche/.github/workflows/railway-release-gate.yml"
SOURCE_REF = "refs/heads/main"
ARTIFACT_REPOSITORY = "ghcr.io/beepboop2025/seiche-release-gates"
ARTIFACT_TYPE = "application/vnd.seiche.railway-gate-result.v1"
RECEIPT_MEDIA_TYPE = "application/vnd.seiche.railway-gate-result.v1+json"
PUBLIC_OCI_GH_TOKEN = "public-oci-bundle-verification-no-api"
INSTALL_COMMAND = (
    "python -m pip install -q ./backend[dev,collectors] && "
    "python -m pip install --disable-pip-version-check --only-binary=:all: "
    "--require-hashes -r ops/requirements-social-cards.txt"
)
TEST_COMMAND = (
    "PYTHONPATH=/workspace/backend "
    "SEICHE_RUNTIME_DATA_DIR=/tmp/seiche-railway-gate-runtime/data "
    "SEICHE_VALIDATION_DIR=/tmp/seiche-railway-gate-runtime/data/market-validation "
    "python -P -m pytest backend/tests -q --memray "
    "-o faulthandler_timeout=300 "
    "-o cache_dir=/tmp/seiche-railway-gate-runtime/pytest-cache"
)
RUNNER_IMAGE = (
    "docker.io/library/python:3.12.11-slim-bookworm@"
    "sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7"
)
REGCTL = Path("/usr/local/bin/regctl")
GH = Path("/usr/local/bin/gh")
RUNUSER = Path("/usr/sbin/runuser")
GIT = Path("/usr/bin/git")
ENV = Path("/usr/bin/env")
SHA1_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
DIGEST_RE = re.compile(r"sha256:([0-9a-f]{64})")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
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
    "test_command",
    "python_version",
    "dependency_snapshot_sha256",
    "tests",
}
TEST_KEYS = {"passed", "skipped", "subtests", "duration_seconds"}


def fail(message: str) -> NoReturn:
    print(f"remote gate verification: {message}", file=sys.stderr)
    raise SystemExit(1)


def defer(message: str) -> NoReturn:
    print(f"remote gate pending: {message}", file=sys.stderr)
    raise SystemExit(75)


def canonical_json(payload: Mapping[str, object]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def load_canonical_receipt(body: bytes) -> dict[str, object]:
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail(f"OCI receipt JSON is invalid: {exc}")
    if not isinstance(payload, dict) or canonical_json(payload) != body:
        fail("OCI receipt is not canonical JSON")
    return payload


def validate_executable(path: Path, label: str) -> None:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        fail(f"{label} is unavailable: {exc}")
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        fail(f"{label} metadata is unsafe: {path}")


def service_git(
    app: Path,
    service_user: str,
    arguments: Sequence[str],
    *,
    stdout: int | None = subprocess.PIPE,
) -> subprocess.CompletedProcess[bytes] | subprocess.Popen[bytes]:
    command = [
        str(RUNUSER),
        "-u",
        service_user,
        "--",
        str(ENV),
        "-i",
        f"HOME={app.parent}",
        "LANG=C",
        "LC_ALL=C",
        "PATH=/usr/bin:/bin",
        "GIT_CONFIG_GLOBAL=/dev/null",
        "GIT_CONFIG_NOSYSTEM=1",
        "GIT_NO_LAZY_FETCH=1",
        "GIT_NO_REPLACE_OBJECTS=1",
        "GIT_OPTIONAL_LOCKS=0",
        str(GIT),
        "-C",
        str(app),
        *arguments,
    ]
    if stdout is None:
        return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return subprocess.run(
        command,
        check=False,
        stdout=stdout,
        stderr=subprocess.PIPE,
    )


def verify_local_git(app: Path, service_user: str, target: str, tree: str) -> str:
    if not app.is_absolute() or not app.is_dir() or app.is_symlink():
        fail("canonical checkout path is unsafe")
    result = service_git(app, service_user, ["rev-parse", f"{target}^{{tree}}"])
    assert isinstance(result, subprocess.CompletedProcess)
    if result.returncode != 0:
        fail(
            f"target tree cannot be resolved: {result.stderr.decode(errors='replace')}"
        )
    if result.stdout.strip() != tree.encode("ascii"):
        fail("target tree differs from the controller-selected tree")

    process = service_git(
        app, service_user, ["archive", "--format=tar", target], stdout=None
    )
    assert isinstance(process, subprocess.Popen)
    if process.stdout is None or process.stderr is None:
        fail("git archive streams are unavailable")
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
        size += len(chunk)
        if size > 512 * 1024 * 1024:
            process.kill()
            fail("exact-source archive is unexpectedly large")
        digest.update(chunk)
    stderr = process.stderr.read()
    status = process.wait()
    if status != 0 or size == 0:
        fail(f"exact-source archive failed: {stderr.decode(errors='replace')}")
    return digest.hexdigest()


def anonymous_environment(root: Path) -> dict[str, str]:
    docker_config = root / "docker"
    docker_config.mkdir(mode=0o700)
    (docker_config / "config.json").write_text('{"auths":{}}\n', encoding="ascii")
    return {
        "HOME": str(root),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "DOCKER_CONFIG": str(docker_config),
        # gh requires a non-empty token before any attestation subcommand,
        # including --bundle-from-oci. This fixed non-secret value has no API
        # authority; the empty Docker config still forces public OCI retrieval.
        "GH_TOKEN": PUBLIC_OCI_GH_TOKEN,
    }


def checked_output(
    command: Sequence[str],
    environment: Mapping[str, str],
    label: str,
    *,
    maximum: int,
    timeout_seconds: int = 60,
) -> bytes:
    try:
        result = subprocess.run(
            list(command),
            env=dict(environment),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        fail(f"{label} exceeded its {timeout_seconds}s timeout")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        fail(f"{label} failed: {detail}")
    if not result.stdout or len(result.stdout) > maximum:
        fail(f"{label} returned empty or oversized output")
    return result.stdout


def missing_artifact_error(detail: str) -> bool:
    normalized = detail.casefold()
    return any(
        marker in normalized
        for marker in (
            "manifest unknown",
            "not found [http 404]",
            "status code 404",
        )
    )


def resolve_artifact_tag(
    tag: str,
    environment: Mapping[str, str],
) -> str:
    try:
        result = subprocess.run(
            [str(REGCTL), "image", "digest", tag],
            env=dict(environment),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        fail("OCI tag resolution exceeded its 30s timeout")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if missing_artifact_error(detail):
            defer("the exact-SHA OCI receipt has not been published yet")
        fail(f"OCI tag resolution failed: {detail}")
    if not result.stdout or len(result.stdout) > 256:
        fail("OCI tag resolution returned empty or oversized output")
    artifact_digest = result.stdout.decode("ascii", errors="strict").strip()
    if DIGEST_RE.fullmatch(artifact_digest) is None:
        fail("OCI tag resolved to an invalid digest")
    return artifact_digest


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
        fail("OCI artifact must contain exactly one receipt layer")
    layer = layers[0]
    if not isinstance(layer, dict) or layer.get("mediaType") != RECEIPT_MEDIA_TYPE:
        fail("OCI receipt layer media type is unexpected")
    annotations = layer.get("annotations")
    if (
        not isinstance(annotations, dict)
        or annotations.get("org.opencontainers.image.title") != "gate.json"
    ):
        fail("OCI receipt layer title is missing")
    layer_digest = str(layer.get("digest", ""))
    if (
        DIGEST_RE.fullmatch(layer_digest) is None
        or type(layer.get("size")) is not int
        or not 1 <= layer["size"] <= 64 * 1024
    ):
        fail("OCI receipt layer descriptor is invalid")
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


def validate_remote_receipt(
    payload: object,
    *,
    target: str,
    tree: str,
    source_archive_sha256: str,
) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != REMOTE_KEYS:
        fail("remote receipt shape is not canonical")
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
        "test_command": TEST_COMMAND,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            fail(f"remote receipt {key} does not match the reviewed contract")
    if SHA256_RE.fullmatch(str(payload.get("request_id", ""))) is None:
        fail("remote receipt request id is invalid")
    if SHA256_RE.fullmatch(str(payload.get("dependency_snapshot_sha256", ""))) is None:
        fail("remote receipt dependency digest is invalid")
    if re.fullmatch(r"3\.12\.[0-9]+", str(payload.get("python_version", ""))) is None:
        fail("remote receipt Python version is invalid")
    for key in (
        "railway_deployment_id",
        "railway_project_id",
        "railway_environment_id",
        "railway_service_id",
    ):
        if UUID_RE.fullmatch(str(payload.get(key, ""))) is None:
            fail(f"remote receipt {key} is invalid")
    if REGION_RE.fullmatch(str(payload.get("railway_replica_region", ""))) is None:
        fail("remote receipt Railway region is invalid")
    started = str(payload.get("started_at", ""))
    completed = str(payload.get("completed_at", ""))
    if (
        TIMESTAMP_RE.fullmatch(started) is None
        or TIMESTAMP_RE.fullmatch(completed) is None
        or started > completed
    ):
        fail("remote receipt timestamps are invalid")
    tests = payload.get("tests")
    if not isinstance(tests, dict) or set(tests) != TEST_KEYS:
        fail("remote receipt test summary is invalid")
    for key in ("passed", "skipped", "subtests"):
        if type(tests.get(key)) is not int or tests[key] < 0:
            fail(f"remote receipt test count {key} is invalid")
    if tests["passed"] <= 0:
        fail("remote receipt has no passing tests")
    duration = tests.get("duration_seconds")
    if type(duration) not in {int, float} or not 0 < duration <= 3600:
        fail("remote receipt test duration is invalid")
    return payload


def render_local_receipt(
    remote: Mapping[str, object],
    *,
    artifact_digest: str,
    artifact_receipt_sha256: str,
) -> dict[str, object]:
    return {
        "schema": LOCAL_SCHEMA,
        "kind": "gate",
        "commit": remote["commit"],
        "tree": remote["tree"],
        "started_at": remote["started_at"],
        "completed_at": remote["completed_at"],
        "conclusion": "success",
        "gate_provider": "railway",
        "install_command": INSTALL_COMMAND,
        "test_command": TEST_COMMAND,
        "remote": {
            "repository": REPOSITORY,
            "workflow": WORKFLOW,
            "source_ref": SOURCE_REF,
            "artifact_repository": ARTIFACT_REPOSITORY,
            "artifact_digest": artifact_digest,
            "artifact_receipt_sha256": artifact_receipt_sha256,
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
            "tests": remote["tests"],
        },
    }


def fetch_and_verify(
    *, app: Path, service_user: str, target: str, tree: str
) -> dict[str, object]:
    validate_executable(REGCTL, "regctl")
    validate_executable(GH, "GitHub attestation verifier")
    for executable, label in ((RUNUSER, "runuser"), (GIT, "Git"), (ENV, "env")):
        validate_executable(executable, label)
    source_archive_sha256 = verify_local_git(app, service_user, target, tree)

    with tempfile.TemporaryDirectory(prefix="seiche-remote-gate-") as temporary:
        environment = anonymous_environment(Path(temporary))
        tag = f"{ARTIFACT_REPOSITORY}:sha-{target}"
        artifact_digest = resolve_artifact_tag(tag, environment)
        reference = f"{ARTIFACT_REPOSITORY}@{artifact_digest}"
        manifest_body = checked_output(
            [str(REGCTL), "manifest", "get", reference, "--format", "raw-body"],
            environment,
            "OCI manifest fetch",
            maximum=128 * 1024,
        )
        layer_digest = validate_manifest(manifest_body, artifact_digest)
        receipt_body = checked_output(
            [str(REGCTL), "artifact", "get", "--file", "gate.json", reference],
            environment,
            "OCI receipt fetch",
            maximum=64 * 1024,
        )
        if f"sha256:{hashlib.sha256(receipt_body).hexdigest()}" != layer_digest:
            fail("OCI receipt bytes do not match the manifest layer digest")
        remote_payload = load_canonical_receipt(receipt_body)
        remote = validate_remote_receipt(
            remote_payload,
            target=target,
            tree=tree,
            source_archive_sha256=source_archive_sha256,
        )
        attestation_body = checked_output(
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
        return render_local_receipt(
            remote,
            artifact_digest=artifact_digest,
            artifact_receipt_sha256=hashlib.sha256(receipt_body).hexdigest(),
        )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--service-user", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--tree", required=True)
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
    )
    sys.stdout.buffer.write(canonical_json(receipt))


if __name__ == "__main__":
    main()
