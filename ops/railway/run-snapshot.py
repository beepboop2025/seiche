#!/usr/bin/env python3
"""Build one exact Seiche snapshot and expose its canonical Railway result."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import NoReturn

REQUEST_SCHEMA = "seiche.railway-snapshot-request.v1"
RESULT_SCHEMA = "seiche.railway-snapshot-result.v1"
REPOSITORY = "beepboop2025/seiche"
WORKFLOW = "beepboop2025/seiche/.github/workflows/railway-snapshot-prebuild.yml"
SOURCE_REF = "refs/heads/main"
INSTALL_COMMAND = "python -m pip install -q ./backend[collectors]"
BUILD_COMMAND = "python -I -B -m seiche.remote_snapshot_build"
RUNNER_IMAGE = (
    "docker.io/library/python:3.12.11-slim-bookworm@"
    "sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7"
)
RUN_UID = 65532
RUN_GID = 65532
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
SHA1_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
REGION_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
REQUEST_KEYS = {
    "schema",
    "repository",
    "workflow",
    "source_ref",
    "commit",
    "tree",
    "source_archive_sha256",
    "request_id",
    "runner_image",
    "install_command",
    "build_command",
}


def fail(message: str) -> NoReturn:
    print(f"railway snapshot: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_value(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_document(payload: Mapping[str, object]) -> bytes:
    return canonical_value(payload) + b"\n"


def load_request(path: Path, source_archive: Path) -> dict[str, str]:
    try:
        raw = path.read_bytes()
        if len(raw) > 16 * 1024:
            fail("request is oversized")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"request cannot be read: {exc}")
    if not isinstance(payload, dict) or set(payload) != REQUEST_KEYS:
        fail("request shape is not canonical")
    if not all(isinstance(value, str) for value in payload.values()):
        fail("request values must be strings")
    expected = {
        "schema": REQUEST_SCHEMA,
        "repository": REPOSITORY,
        "workflow": WORKFLOW,
        "source_ref": SOURCE_REF,
        "runner_image": RUNNER_IMAGE,
        "install_command": INSTALL_COMMAND,
        "build_command": BUILD_COMMAND,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            fail(f"request {key} does not match the reviewed contract")
    if SHA1_RE.fullmatch(payload["commit"]) is None:
        fail("request commit is invalid")
    if SHA1_RE.fullmatch(payload["tree"]) is None:
        fail("request tree is invalid")
    if SHA256_RE.fullmatch(payload["source_archive_sha256"]) is None:
        fail("request source archive digest is invalid")
    if SHA256_RE.fullmatch(payload["request_id"]) is None:
        fail("request id is invalid")
    if file_sha256(source_archive) != payload["source_archive_sha256"]:
        fail("source archive bytes do not match the request")
    return payload


def verify_extracted_source(
    source_archive: Path,
    source_root: Path,
    *,
    expected_uid: int = 0,
) -> None:
    """Prove the root-owned read-only extraction still matches git archive."""

    seen: set[PurePosixPath] = set()
    try:
        root_info = source_root.lstat()
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != expected_uid
            or stat.S_IMODE(root_info.st_mode) & 0o222
        ):
            fail("extracted exact-source root is writable or has the wrong owner")
        with tarfile.open(source_archive, mode="r:") as bundle:
            for member in bundle:
                relative = PurePosixPath(member.name)
                if (
                    relative.is_absolute()
                    or not relative.parts
                    or any(part in {"", ".", ".."} for part in relative.parts)
                    or relative in seen
                ):
                    fail("source archive contains an unsafe or repeated path")
                seen.add(relative)
                target = source_root.joinpath(*relative.parts)
                info = target.lstat()
                if member.isdir():
                    if (
                        not stat.S_ISDIR(info.st_mode)
                        or info.st_uid != expected_uid
                        or stat.S_IMODE(info.st_mode) & 0o222
                    ):
                        fail(f"extracted tracked directory is writable: {relative}")
                    continue
                if not member.isfile():
                    fail("source archive contains a non-regular tracked entry")
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != expected_uid
                    or stat.S_IMODE(info.st_mode) & 0o222
                    or info.st_size != member.size
                    or stat.S_IMODE(info.st_mode) & 0o111 != member.mode & 0o111
                ):
                    fail(f"extracted tracked metadata differs: {relative}")
                archived = bundle.extractfile(member)
                if archived is None:
                    fail(f"source archive entry is unreadable: {relative}")
                archive_digest = hashlib.sha256()
                extracted_digest = hashlib.sha256()
                with target.open("rb") as extracted:
                    while True:
                        archived_chunk = archived.read(1024 * 1024)
                        extracted_chunk = extracted.read(1024 * 1024)
                        if not archived_chunk and not extracted_chunk:
                            break
                        archive_digest.update(archived_chunk)
                        extracted_digest.update(extracted_chunk)
                if archive_digest.digest() != extracted_digest.digest():
                    fail(f"extracted tracked bytes differ: {relative}")
    except (OSError, tarfile.TarError) as exc:
        fail(f"source archive/tree verification failed: {exc}")
    if PurePosixPath("backend/seiche") not in seen and not any(
        path.parts[:2] == ("backend", "seiche") for path in seen
    ):
        fail("source archive contains no Seiche package")


def validate_runtime_identity() -> None:
    if os.getuid() != RUN_UID or os.getgid() != RUN_GID:
        fail(
            "runner must execute as the reviewed unprivileged "
            f"identity {RUN_UID}:{RUN_GID}"
        )


def dependency_snapshot_sha256() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--all"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(f"dependency snapshot failed: {result.stderr.strip()}")
    lines = result.stdout.splitlines()
    if not lines or any("\x00" in line or "\r" in line for line in lines):
        fail("dependency snapshot is empty or malformed")
    canonical = "\n".join(sorted(lines)) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_environment(runtime_root: Path) -> dict[str, str]:
    private_home = runtime_root / "home"
    data_dir = runtime_root / "data"
    cache_dir = private_home / ".cache"
    for directory in (private_home, data_dir, cache_dir):
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    return {
        "HOME": str(private_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONUNBUFFERED": "1",
        "SEICHE_RUNTIME_DATA_DIR": str(data_dir),
        "TMPDIR": str(runtime_root),
        "TZ": "UTC",
        "XDG_CACHE_HOME": str(cache_dir),
    }


def run_builder() -> tuple[dict[str, object], str, str]:
    started_at = utc_now()
    with tempfile.TemporaryDirectory(prefix="seiche-snapshot-", dir="/tmp") as root:
        runtime_root = Path(root)
        environment = build_environment(runtime_root)
        try:
            process = subprocess.run(
                [sys.executable, "-I", "-B", "-m", "seiche.remote_snapshot_build"],
                env=environment,
                check=False,
                stdout=subprocess.PIPE,
                stderr=None,
                timeout=3600,
            )
        except subprocess.TimeoutExpired:
            fail("snapshot builder exceeded its 3600s timeout")
    completed_at = utc_now()
    if process.returncode != 0:
        fail(f"{BUILD_COMMAND} exited {process.returncode}")
    if not process.stdout or len(process.stdout) > MAX_PAYLOAD_BYTES:
        fail("snapshot builder returned empty or oversized output")
    try:
        payload = json.loads(process.stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail(f"snapshot builder returned invalid JSON: {exc}")
    if not isinstance(payload, dict) or process.stdout != canonical_value(payload) + b"\n":
        fail("snapshot builder output is not one canonical JSON document")
    return payload, started_at, completed_at


def railway_identity(environment: Mapping[str, str]) -> dict[str, str]:
    values = {
        "railway_deployment_id": environment.get("RAILWAY_DEPLOYMENT_ID", ""),
        "railway_project_id": environment.get("RAILWAY_PROJECT_ID", ""),
        "railway_environment_id": environment.get("RAILWAY_ENVIRONMENT_ID", ""),
        "railway_service_id": environment.get("RAILWAY_SERVICE_ID", ""),
        "railway_replica_region": environment.get("RAILWAY_REPLICA_REGION", ""),
    }
    for key in (
        "railway_deployment_id",
        "railway_project_id",
        "railway_environment_id",
        "railway_service_id",
    ):
        if UUID_RE.fullmatch(values[key]) is None:
            fail(f"{key} is missing or invalid")
    if REGION_RE.fullmatch(values["railway_replica_region"]) is None:
        fail("railway_replica_region is missing or invalid")
    return values


def validate_payload(payload: Mapping[str, object]) -> dict[str, object]:
    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str) or TIMESTAMP_RE.fullmatch(generated_at) is None:
        fail("snapshot generated_at is invalid")
    provenance = payload.get("provenance")
    if isinstance(provenance, (list, dict)):
        provenance_count = len(provenance)
    else:
        fail("snapshot provenance is invalid")
    faults = payload.get("faults")
    if not isinstance(faults, list) or not all(isinstance(row, dict) for row in faults):
        fail("snapshot faults are invalid")
    payload_bytes = canonical_value(payload)
    return {
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "payload_size_bytes": len(payload_bytes),
        "generated_at": generated_at,
        "provenance_sha256": hashlib.sha256(canonical_value(provenance)).hexdigest(),
        "provenance_count": provenance_count,
        "faults_sha256": hashlib.sha256(canonical_value(faults)).hexdigest(),
        "fault_count": len(faults),
    }


def build_result(
    request: Mapping[str, str],
    payload: dict[str, object],
    started_at: str,
    completed_at: str,
    environment: Mapping[str, str],
) -> dict[str, object]:
    python_version = platform.python_version()
    if re.fullmatch(r"3\.12\.[0-9]+", python_version) is None:
        fail(f"runner Python is not the reviewed 3.12 line: {python_version}")
    return {
        "schema": RESULT_SCHEMA,
        "repository": REPOSITORY,
        "workflow": WORKFLOW,
        "source_ref": SOURCE_REF,
        "commit": request["commit"],
        "tree": request["tree"],
        "source_archive_sha256": request["source_archive_sha256"],
        "request_id": request["request_id"],
        "runner_provider": "railway",
        "runner_image": RUNNER_IMAGE,
        **railway_identity(environment),
        "started_at": started_at,
        "completed_at": completed_at,
        "conclusion": "success",
        "install_command": INSTALL_COMMAND,
        "build_command": BUILD_COMMAND,
        "python_version": python_version,
        "dependency_snapshot_sha256": dependency_snapshot_sha256(),
        **validate_payload(payload),
        "payload": payload,
    }


def serve(result: bytes) -> NoReturn:
    try:
        port = int(os.environ.get("PORT", "8080"))
    except ValueError:
        fail("PORT is invalid")
    if not 1 <= port <= 65535:
        fail("PORT is outside the valid range")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/healthz":
                body = b'{"status":"snapshot_complete"}\n'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            else:
                body = b'{"error":"not_found"}\n'
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_arguments: object) -> None:
            return

    print(f"railway snapshot: complete; serving health on port {port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
    raise AssertionError("HTTP server returned unexpectedly")


def main() -> NoReturn:
    request_path = Path(os.environ.get("SEICHE_SNAPSHOT_REQUEST", "/gate/request.json"))
    archive_path = Path(os.environ.get("SEICHE_SNAPSHOT_ARCHIVE", "/gate/source.tar"))
    source_root = Path(os.environ.get("SEICHE_SNAPSHOT_SOURCE_ROOT", "/workspace"))
    result_path = Path(
        os.environ.get("SEICHE_SNAPSHOT_RESULT", "/result/snapshot-result.json")
    )
    if not source_root.is_dir() or not (source_root / "backend" / "seiche").is_dir():
        fail("extracted exact-source tree is missing")
    validate_runtime_identity()
    request = load_request(request_path, archive_path)
    verify_extracted_source(archive_path, source_root)
    payload, started_at, completed_at = run_builder()
    verify_extracted_source(archive_path, source_root)
    result = canonical_document(
        build_result(request, payload, started_at, completed_at, os.environ)
    )
    if len(result) > MAX_PAYLOAD_BYTES + 128 * 1024:
        fail("canonical snapshot result is oversized")
    result_path.write_bytes(result)
    result_path.chmod(0o444)
    print(
        "SEICHE_RAILWAY_SNAPSHOT_RESULT_SHA256="
        f"{hashlib.sha256(result).hexdigest()}",
        flush=True,
    )
    serve(result)


if __name__ == "__main__":
    main()
