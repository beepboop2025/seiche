#!/usr/bin/env python3
"""Run one exact Seiche release gate and expose its canonical Railway result."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import platform
import re
import stat
import subprocess
import sys
import tarfile
from typing import NoReturn


REQUEST_SCHEMA = "seiche.railway-gate-request.v1"
RESULT_SCHEMA = "seiche.railway-gate-result.v1"
REPOSITORY = "beepboop2025/seiche"
WORKFLOW = "beepboop2025/seiche/.github/workflows/railway-release-gate.yml"
SOURCE_REF = "refs/heads/main"
INSTALL_COMMAND = (
    "python -m pip install -q ./backend[dev,collectors] && "
    "python -m pip install --disable-pip-version-check --only-binary=:all: "
    "--require-hashes -r ops/requirements-social-cards.txt"
)
SOURCE_ROOT = Path("/workspace")
SOURCE_BACKEND = SOURCE_ROOT / "backend"
RUNTIME_ROOT = Path("/var/lib/seiche-railway-gate-runtime")
PYTEST_CACHE_DIR = RUNTIME_ROOT / "pytest-cache"
RUNTIME_DATA_DIR = RUNTIME_ROOT / "data"
RUNTIME_TMP_DIR = RUNTIME_ROOT / "tmp"
VALIDATION_DIR = RUNTIME_DATA_DIR / "market-validation"
XDG_CACHE_DIR = RUNTIME_ROOT / "xdg-cache"
GIT = Path("/usr/bin/git")
PYTEST_ARGUMENTS = (
    "-P",
    "-m",
    "pytest",
    "backend/tests",
    "-q",
    "--memray",
    "-o",
    "faulthandler_timeout=300",
    "-o",
    f"cache_dir={PYTEST_CACHE_DIR}",
)
TEST_COMMAND = (
    f"HOME={RUNTIME_ROOT} "
    f"TMPDIR={RUNTIME_TMP_DIR} "
    f"PYTHONPATH={SOURCE_BACKEND} "
    f"SEICHE_RUNTIME_DATA_DIR={RUNTIME_DATA_DIR} "
    f"SEICHE_VALIDATION_DIR={VALIDATION_DIR} "
    f"python {' '.join(PYTEST_ARGUMENTS)}"
)
RUNNER_IMAGE = (
    "docker.io/library/python:3.12.11-slim-bookworm@"
    "sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7"
)
SHA1_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
REGION_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
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
    "test_command",
}
RUN_UID = 65532
RUN_GID = 65532
FAILURE_MARKER = "SEICHE_RAILWAY_GATE_FAILURE_V1=1"


def fail(message: str) -> NoReturn:
    print(FAILURE_MARKER, file=sys.stderr, flush=True)
    print(f"railway gate: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        "test_command": TEST_COMMAND,
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
    """Prove every tracked archive file still has its reviewed bytes and mode."""

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
    if PurePosixPath("backend/tests") not in seen:
        # Git archives contain explicit directory entries today; also accept the
        # first test file as proof if a future archive omits directory entries.
        if not any(path.parts[:2] == ("backend", "tests") for path in seen):
            fail("source archive contains no backend test suite")


def verify_git_identity(
    source_root: Path,
    request: Mapping[str, str],
    *,
    expected_uid: int = 0,
) -> None:
    """Bind the read-only worktree's real commit and tree to the request."""

    try:
        canonical_source = source_root.resolve(strict=True)
        git_metadata = canonical_source / ".git"
        for candidate in (git_metadata, *git_metadata.rglob("*")):
            info = candidate.lstat()
            if (
                (not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode))
                or info.st_uid != expected_uid
                or stat.S_IMODE(info.st_mode) & 0o222
            ):
                fail("verified Git metadata is writable or has unsafe metadata")
    except OSError as exc:
        fail(f"verified Git metadata cannot be inspected safely: {exc}")

    integrity = subprocess.run(
        [
            str(GIT),
            "-c",
            f"safe.directory={canonical_source}",
            "-C",
            str(canonical_source),
            "fsck",
            "--full",
            "--strict",
            "--no-progress",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if integrity.returncode != 0:
        fail("verified Git object graph is incomplete or corrupt")

    observed: dict[str, str] = {}
    for key, revision in (("commit", "HEAD^{commit}"), ("tree", "HEAD^{tree}")):
        result = subprocess.run(
            [
                str(GIT),
                "-c",
                f"safe.directory={canonical_source}",
                "-C",
                str(canonical_source),
                "rev-parse",
                "--verify",
                revision,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        value = result.stdout.strip()
        if result.returncode != 0 or SHA1_RE.fullmatch(value) is None:
            fail(f"verified Git {key} cannot be resolved")
        observed[key] = value
    if observed != {"commit": request["commit"], "tree": request["tree"]}:
        fail("verified Git commit/tree differs from the canonical request")


def validate_runtime_identity() -> None:
    if os.getuid() != RUN_UID or os.getgid() != RUN_GID:
        fail(
            "runner must execute as the reviewed unprivileged "
            f"identity {RUN_UID}:{RUN_GID}"
        )


def validate_runtime_ancestry(path: Path) -> None:
    """Require every runtime path component to have a trusted owner and mode."""

    if not path.is_absolute():
        fail(f"test runtime path is not absolute: {path}")
    runner_identity = (os.getuid(), os.getgid())
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            fail(f"test runtime ancestry cannot be inspected safely: {exc}")
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or not (
                metadata.st_uid == 0
                or (metadata.st_uid, metadata.st_gid) == runner_identity
            )
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            fail(f"test runtime ancestry has unsafe metadata: {current}")


def build_test_environment(
    source_root: Path,
    *,
    runtime_root: Path = RUNTIME_ROOT,
) -> dict[str, str]:
    try:
        canonical_source = source_root.resolve(strict=True)
        source_backend = (canonical_source / "backend").resolve(strict=True)
        if source_backend.parent != canonical_source or not source_backend.is_dir():
            fail("verified source backend is not a canonical directory")
        runtime_root.mkdir(mode=0o700, exist_ok=True)
        pytest_cache = runtime_root / "pytest-cache"
        runtime_data = runtime_root / "data"
        runtime_tmp = runtime_root / "tmp"
        xdg_cache = runtime_root / "xdg-cache"
        for path in (pytest_cache, runtime_data, runtime_tmp, xdg_cache):
            path.mkdir(mode=0o700, exist_ok=True)
        for path in (runtime_root, pytest_cache, runtime_data, runtime_tmp, xdg_cache):
            validate_runtime_ancestry(path)
            info = path.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                fail(f"test runtime directory has unsafe metadata: {path}")
    except OSError as exc:
        fail(f"test runtime directory cannot be prepared safely: {exc}")
    return {
        "HOME": str(runtime_root),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": str(source_backend),
        "PYTHONSAFEPATH": "1",
        "PYTHONUNBUFFERED": "1",
        "SEICHE_RUNTIME_DATA_DIR": str(runtime_data),
        "SEICHE_VALIDATION_DIR": str(runtime_data / "market-validation"),
        "TMPDIR": str(runtime_tmp),
        "TZ": "UTC",
        "XDG_CACHE_HOME": str(xdg_cache),
    }


def verify_test_import(source_root: Path, environment: Mapping[str, str]) -> None:
    """Fail before the long suite unless `seiche` comes from the verified tree."""

    expected_module = (source_root / "backend" / "seiche" / "__init__.py").resolve(
        strict=True
    )
    expected_data = Path(environment["SEICHE_RUNTIME_DATA_DIR"]).resolve(strict=True)
    expected_home = Path(environment["HOME"]).resolve(strict=True)
    expected_tmp = Path(environment["TMPDIR"]).resolve(strict=True)
    if expected_tmp != expected_home / "tmp":
        fail("test interpreter temporary directory is outside the runtime root")
    result = subprocess.run(
        [
            sys.executable,
            "-P",
            "-c",
            (
                "import json; from pathlib import Path; import tempfile; import seiche; "
                "from seiche.config import DATA_DIR; "
                "print(json.dumps({'data_dir': str(DATA_DIR.resolve()), "
                "'module': str(Path(seiche.__file__).resolve()), "
                "'temp_dir': str(Path(tempfile.gettempdir()).resolve())}, "
                "sort_keys=True, separators=(',', ':')))"
            ),
        ],
        check=False,
        cwd=source_root,
        env=dict(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        observed = json.loads(result.stdout) if result.returncode == 0 else None
    except json.JSONDecodeError:
        observed = None
    if observed != {
        "data_dir": str(expected_data),
        "module": str(expected_module),
        "temp_dir": str(expected_tmp),
    }:
        fail("test interpreter did not bind the verified source and runtime data")


def dependency_snapshot_sha256() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--all"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        fail(f"dependency snapshot failed: {result.stderr.strip()}")
    lines = result.stdout.splitlines()
    if not lines or any("\x00" in line or "\r" in line for line in lines):
        fail("dependency snapshot is empty or malformed")
    canonical = "\n".join(sorted(lines)) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_pytest_summary(output: str) -> dict[str, int | float]:
    summary = ANSI_RE.sub("", output)
    candidates = [
        line.strip()
        for line in summary.splitlines()
        if " passed" in line and " in " in line
    ]
    if not candidates:
        fail("successful pytest output has no canonical pass summary")
    final = candidates[-1]
    passed = re.search(r"(?:^|, )(\d+) passed(?:,| in )", final)
    duration = re.search(r" in ([0-9]+(?:\.[0-9]+)?)s(?: |$)", final)
    if passed is None or duration is None:
        fail("pytest pass summary is malformed")

    def optional_count(label: str, *, success_suffix: bool = False) -> int:
        suffix = r"(?: passed)?" if success_suffix else ""
        match = re.search(
            rf"(?:^|, )(\d+) {label}{suffix}(?:,| in )",
            final,
        )
        return 0 if match is None else int(match.group(1))

    tests: dict[str, int | float] = {
        "passed": int(passed.group(1)),
        "skipped": optional_count("skipped"),
        "subtests": optional_count("subtests", success_suffix=True),
        "duration_seconds": float(duration.group(1)),
    }
    if tests["passed"] <= 0 or tests["duration_seconds"] <= 0:
        fail("pytest summary contains impossible counts")
    return tests


def run_tests(source_root: Path) -> tuple[dict[str, int | float], str, str]:
    if source_root != SOURCE_ROOT:
        fail("test source root does not match the reviewed runtime contract")
    started_at = utc_now()
    environment = build_test_environment(source_root)
    verify_test_import(source_root, environment)
    command = [sys.executable, *PYTEST_ARGUMENTS]
    process = subprocess.Popen(
        command,
        cwd=source_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if process.stdout is None:
        fail("test process has no output stream")
    tail = bytearray()
    while True:
        chunk = process.stdout.read(64 * 1024)
        if not chunk:
            break
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        tail.extend(chunk)
        if len(tail) > 2 * 1024 * 1024:
            del tail[: len(tail) - 2 * 1024 * 1024]
    status = process.wait()
    completed_at = utc_now()
    if status != 0:
        fail(f"{TEST_COMMAND} exited {status}")

    tests = parse_pytest_summary(tail.decode("utf-8", errors="replace"))
    return tests, started_at, completed_at


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


def build_result(
    request: Mapping[str, str],
    tests: Mapping[str, int | float],
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
        "test_command": TEST_COMMAND,
        "python_version": python_version,
        "dependency_snapshot_sha256": dependency_snapshot_sha256(),
        "tests": dict(tests),
    }


def canonical_json(payload: Mapping[str, object]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def serve(result: bytes) -> NoReturn:
    try:
        port = int(os.environ.get("PORT", "8080"))
    except ValueError:
        fail("PORT is invalid")
    if not 1 <= port <= 65535:
        fail("PORT is outside the valid range")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            if self.path == "/healthz":
                body = b'{"status":"gate_complete"}\n'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            elif self.path == "/gate.json":
                body = result
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "application/vnd.seiche.railway-gate-result.v1+json",
                )
            else:
                body = b'{"error":"not_found"}\n'
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_arguments: object) -> None:
            return

    print(f"railway gate: complete; serving proof on port {port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
    raise AssertionError("HTTP server returned unexpectedly")


def main() -> NoReturn:
    request_path = Path(os.environ.get("SEICHE_GATE_REQUEST", "/gate/request.json"))
    archive_path = Path(os.environ.get("SEICHE_GATE_ARCHIVE", "/gate/source.tar"))
    source_root = SOURCE_ROOT
    if not source_root.is_dir() or not (source_root / "backend" / "tests").is_dir():
        fail("extracted exact-source tree is missing")
    validate_runtime_identity()
    request = load_request(request_path, archive_path)
    verify_git_identity(source_root, request)
    verify_extracted_source(archive_path, source_root)
    tests, started_at, completed_at = run_tests(source_root)
    verify_extracted_source(archive_path, source_root)
    verify_git_identity(source_root, request)
    payload = build_result(request, tests, started_at, completed_at, os.environ)
    result = canonical_json(payload)
    encoded = base64.b64encode(result).decode("ascii")
    print(f"SEICHE_RAILWAY_GATE_RESULT_V1={encoded}", flush=True)
    serve(result)


if __name__ == "__main__":
    main()
