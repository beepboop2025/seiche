"""Candidate-only cold recovery computation, never production write authority.

The root supervisor executes the pinned backend as dedicated UID 32001 with a clean
scratch environment and kernel-enforced write confinement. Only root seals the
result. API readers revalidate the request, candidate, fence, immutable parent
chain, exact bytes and original clocks on every readiness decision.
"""
from __future__ import annotations

import ctypes
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

OPERATION = "application_cold_recovery"
SCHEMA = "seiche.application-cold-snapshot.v1"
CACHE_SOURCE = "application_cold_candidate"
ARTIFACT_ROOT = Path("/tmp/seiche-cold-snapshots")
# RLIMIT_NPROC counts tasks by real UID across containers sharing a user
# namespace. Keep cold computation off the common distroless UID 65532.
CHILD_UID = CHILD_GID = 32001
MAX_BYTES = 64 * 1024 * 1024
MAX_STDERR = 8 * 1024 * 1024
MAX_AGE = timedelta(seconds=900)
MAX_BUILD_SECONDS = 3600
AUTHORITY = {"candidate_only": True, "writers_started": False,
             "can_publish": False, "can_execute": False, "promotes_handoff": False}


class ColdSnapshotError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise ColdSnapshotError(message)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _time(value):
    _require(isinstance(value, str), "cold snapshot clock is missing")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ColdSnapshotError("cold snapshot clock is invalid") from error
    _require(result.tzinfo is not None and result.utcoffset() is not None,
             "cold snapshot clock has no timezone")
    return result.astimezone(UTC)


def _now(value=None):
    result = value if value is not None else datetime.now(UTC)
    _require(result.tzinfo is not None and result.utcoffset() is not None,
             "cold snapshot validation clock has no timezone")
    return result.astimezone(UTC)


def _binding(request, candidate):
    from seiche import stateful_application as app
    return {
        "request_id": request["request_id"], "request_sha256": app.digest(request),
        "candidate_sha256": app.digest(candidate),
        "source": {key: request[key] for key in
                   ("commit", "tree", "source_archive_sha256", "source_bundle_sha256")},
        "railway": candidate["railway"], "parent": request["parent"],
        "source_fence_sha256": app.digest(candidate["source_fence"]),
    }


def _validate_context(request, candidate, *, now=None):
    from seiche import stateful_application as app
    _require(request.get("operation") == OPERATION, "cold operation is not authorized")
    app.validate_request(request, now=now)
    app.validate_candidate(candidate, request=request)
    # Existing validators verify the signed fence and recovered parent chain.
    app.load_parent(request, current=True)


def validate_artifact(artifact, request, candidate, *, now=None):
    """Pure payload/binding validation; callers must also prove root-owned ingress."""
    from seiche import assemble
    current = _now(now)
    _validate_context(request, candidate, now=current)
    expected_keys = {"schema", "binding", "authority", "started_at", "completed_at",
                     "generated_at", "payload_sha256", "payload_size_bytes",
                     "provenance_sha256", "faults_sha256", "payload"}
    _require(isinstance(artifact, dict) and set(artifact) == expected_keys,
             "cold artifact shape differs")
    _require(artifact["schema"] == SCHEMA and artifact["binding"] == _binding(request, candidate),
             "cold artifact candidate binding differs")
    _require(artifact["authority"] == AUTHORITY, "cold artifact claims authority")
    payload = artifact["payload"]
    _require(isinstance(payload, dict), "cold payload is not an object")
    raw = _canonical(payload)
    _require(0 < len(raw) <= MAX_BYTES and type(artifact["payload_size_bytes"]) is int
             and len(raw) == artifact["payload_size_bytes"]
             and hashlib.sha256(raw).hexdigest() == artifact["payload_sha256"],
             "cold payload bytes differ")
    _require(payload.get("generated_at") == artifact["generated_at"],
             "cold payload clock differs")
    provenance, faults = payload.get("provenance"), payload.get("faults")
    _require(isinstance(provenance, list) and bool(provenance)
             and all(isinstance(row, dict) for row in provenance)
             and isinstance(faults, list) and all(isinstance(row, dict) for row in faults),
             "cold evidence is missing")
    _require(_digest(provenance) == artifact["provenance_sha256"]
             and _digest(faults) == artifact["faults_sha256"], "cold evidence digest differs")
    start, generated, end = map(_time, (artifact["started_at"], artifact["generated_at"],
                                       artifact["completed_at"]))
    fence = candidate["source_fence"]["payload"]
    _require(_time(fence["requested_at"]) <= start <= generated <= end <= current,
             "cold build clocks are out of order or future dated")
    _require(current - generated <= MAX_AGE and end - start <= timedelta(seconds=MAX_BUILD_SECONDS),
             "cold snapshot is stale or build exceeded its bound")
    _require(current < min(_time(request["expires_at"]), _time(fence["expires_at"])),
             "cold request or fence expired")
    version = payload.get("version")
    _require(isinstance(version, str) and version.split()
             and version.split(maxsplit=1)[0] == assemble.VERSION, "cold version differs")
    assemble._assert_snapshot_rights(payload)
    _require(assemble._servable_snapshot(payload), "cold payload is not safely servable")
    return {"payload": payload,
            "evidence": {key: value for key, value in artifact.items() if key != "payload"},
            "cache_source": CACHE_SOURCE}


def _directory(path, gid, *, create=False):
    if create:
        try:
            path.mkdir(mode=0o750)
            os.chown(path, 0, gid)
        except FileExistsError:
            pass
    for parent in [*reversed(path.parents), path]:
        info = parent.lstat()
        _require(stat.S_ISDIR(info.st_mode) and not parent.is_symlink() and info.st_uid == 0,
                 "cold artifact path is redirected or not root-owned")
        if parent == Path("/tmp"):
            _require(stat.S_IMODE(info.st_mode) == 0o1777, "unsafe temporary directory")
        else:
            _require(not info.st_mode & 0o022, "cold artifact directory is writable by others")
    info = path.lstat()
    _require(info.st_gid == gid and stat.S_IMODE(info.st_mode) == 0o750,
             "cold artifact directory permissions differ")


def _artifact_path(request):
    from seiche import stateful_application as app
    app.validate_request(request)
    return ARTIFACT_ROOT / (request["request_id"] + ".json")


def _read_artifact(path, gid):
    _directory(path.parent, gid)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == gid
                 and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o440
                 and 0 < info.st_size <= MAX_BYTES + 128 * 1024,
                 "cold artifact ownership, permissions or size differs")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 128 * 1024 + 1)
        visible = path.lstat()
        after = os.fstat(fd)
        _require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) ==
                 (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                 and (visible.st_dev, visible.st_ino) == (info.st_dev, info.st_ino),
                 "cold artifact changed while reading")
    finally:
        os.close(fd)
    value = json.loads(raw)
    _require(raw == _canonical(value) + b"\n", "cold artifact is not canonical")
    return value, hashlib.sha256(raw).hexdigest()


def load_candidate_snapshot(environment, *, now=None):
    """Return payload/evidence/cache_source; ordinary upgrades return None."""
    from seiche import assemble, stateful_application as app, stateful_migration as migration
    if environment.get("SEICHE_RAILWAY_STATEFUL_MODE") != "cutover_candidate":
        return None
    if not environment.get("SEICHE_RAILWAY_APPLICATION_REQUEST_ID"):
        return None
    request = app.validate_request(app.read_document(app.REQUEST_PATH), current=False)
    if request["operation"] != OPERATION:
        return None
    _require(app.load_request() == request, "cold runtime request changed")
    candidate = app.validate_runtime(environment, production=False)
    _require(assemble.capture_process_release_sha() == request["commit"],
             "cold process source differs")
    artifact, raw_sha = _read_artifact(_artifact_path(request), migration.RUNTIME_GID)
    result = validate_artifact(artifact, request, candidate, now=now)
    result["evidence"]["artifact_sha256"] = raw_sha
    return result


def isolated_environment(scratch):
    """No inherited environment, credentials, database URLs or production paths."""
    root = Path(scratch)
    for part in ("home", "data", "cache", "tmp"):
        (root / part).mkdir(mode=0o700)
        os.chown(root / part, CHILD_UID, CHILD_GID)
    return {"HOME": str(root / "home"), "SEICHE_RUNTIME_DATA_DIR": str(root / "data"),
            "XDG_CACHE_HOME": str(root / "cache"), "TMPDIR": str(root / "tmp"),
            "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "TZ": "UTC", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
            "PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}


def confine_child(scratch):
    """Kernel write sandbox, inherited by native libraries and descendants.

    Landlock ABI3 handles truncate as well as create/write/remove/rename. Fail
    closed if the runtime kernel or container policy cannot enforce the rules.
    Reads of the pinned source and system libraries remain available.
    """
    _require(os.getuid() == CHILD_UID and os.getgid() == CHILD_GID and not os.getgroups(),
             "cold child identity differs")
    _require(sys.platform == "linux" and platform.machine() in {"x86_64", "aarch64"},
             "cold isolation requires the reviewed Linux syscall ABI")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    _require(libc.prctl(38, 1, 0, 0, 0) == 0, "cannot set no_new_privs")
    abi = libc.syscall(444, 0, 0, 1)
    _require(abi >= 3, "Landlock ABI3 write confinement is unavailable")
    writes = ((1 << 15) - 1) & ~((1 << 0) | (1 << 2) | (1 << 3))
    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]
    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]
    rules = Ruleset(writes)
    descriptor = libc.syscall(444, ctypes.byref(rules), ctypes.sizeof(rules), 0)
    _require(descriptor >= 0, "cannot create Landlock ruleset")
    try:
        for path, rights in ((Path(scratch), writes), (Path("/dev/null"), (1 << 1) | (1 << 14))):
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                rule = Beneath(rights, fd)
                _require(libc.syscall(445, descriptor, 1, ctypes.byref(rule), 0) == 0,
                         "cannot install Landlock path rule")
            finally:
                os.close(fd)
        _require(libc.syscall(446, descriptor, 0) == 0, "cannot enforce Landlock ruleset")
    finally:
        os.close(descriptor)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (MAX_BUILD_SECONDS, MAX_BUILD_SECONDS + 5))
    resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


def _child(scratch, backend):
    _require(Path(backend).resolve() == Path(__file__).resolve().parent.parent,
             "cold child backend differs")
    confine_child(scratch)
    from seiche import remote_snapshot_build
    return remote_snapshot_build.main()


def _stop_child(process):
    # Descendants inherit the sandbox but must not retain output descriptors
    # after the leader exits. This process group belongs solely to this build.
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _compute(request, *, python, timeout, cancelled=None):
    backend = Path(__file__).resolve().parent.parent
    root = Path(tempfile.mkdtemp(prefix="seiche-cold-build-", dir="/tmp"))
    os.chown(root, CHILD_UID, CHILD_GID)
    environment = isolated_environment(root)
    runner = "import runpy,sys;sys.path.insert(0,sys.argv[1]);sys.argv=sys.argv[2:];runpy.run_path(sys.argv[0],run_name=\"__main__\")"
    command = [python, "-I", "-B", "-c", runner, str(backend), str(Path(__file__).resolve()),
               "--isolated-child", str(root), str(backend)]
    started = datetime.now(UTC)
    process = None
    try:
        # Root-owned unnamed descriptors prevent the child replacing its output.
        with tempfile.TemporaryFile(dir="/tmp") as stdout, tempfile.TemporaryFile(dir="/tmp") as stderr:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                cwd=root, env=environment, user=CHILD_UID, group=CHILD_GID, extra_groups=[],
                start_new_session=True, close_fds=True, umask=0o077)
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                _require(not (cancelled and cancelled()), "cold compute was cancelled")
                _require(time.monotonic() < deadline, "cold compute exceeded request deadline")
                _require(os.fstat(stdout.fileno()).st_size <= MAX_BYTES
                         and os.fstat(stderr.fileno()).st_size <= MAX_STDERR,
                         "cold compute output exceeded bound")
                time.sleep(0.2)
            result_code = process.returncode
            _stop_child(process)
            process = None
            completed = datetime.now(UTC)
            _require(not (cancelled and cancelled()), "cold compute was cancelled")
            _require(os.fstat(stdout.fileno()).st_size <= MAX_BYTES
                     and os.fstat(stderr.fileno()).st_size <= MAX_STDERR,
                     "cold compute output exceeded bound")
            _require(result_code == 0, "isolated cold compute failed")
            stdout.seek(0)
            raw = stdout.read(MAX_BYTES + 1)
            _require(0 < len(raw) <= MAX_BYTES, "cold compute payload size differs")
            payload = json.loads(raw)
            _require(raw == _canonical(payload) + b"\n", "cold compute payload is not canonical")
            return payload, started, completed
    finally:
        if process is not None:
            _stop_child(process)
        shutil.rmtree(root)


def build_candidate_snapshot(request, candidate, *, python=sys.executable, cancelled=None):
    """Root supervisor builds/seals one exact cold result; never grants authority."""
    from seiche import assemble, stateful_application as app, stateful_migration as migration
    _require(os.geteuid() == 0 and os.getegid() == 0, "cold builder must run as root")
    _require(not (cancelled and cancelled()), "cold compute was cancelled")
    _validate_context(request, candidate)
    _require(app.load_request() == request, "cold builder request differs")
    _require(assemble.capture_process_release_sha() == request["commit"],
             "cold builder source differs")
    destination = _artifact_path(request)
    _directory(ARTIFACT_ROOT, migration.RUNTIME_GID, create=True)
    if destination.exists() or destination.is_symlink():
        existing, _ = _read_artifact(destination, migration.RUNTIME_GID)
        validate_artifact(existing, request, candidate)
        return destination
    fence = candidate["source_fence"]["payload"]
    timeout = min(MAX_BUILD_SECONDS, (min(_time(request["expires_at"]),
        _time(fence["expires_at"])) - datetime.now(UTC)).total_seconds())
    _require(timeout > 0, "cold request has expired")
    payload, started, completed = _compute(request, python=python, timeout=timeout, cancelled=cancelled)
    artifact = {"schema": SCHEMA, "binding": _binding(request, candidate), "authority": dict(AUTHORITY),
        "started_at": started.isoformat(), "completed_at": completed.isoformat(),
        "generated_at": payload.get("generated_at"), "payload_sha256": _digest(payload),
        "payload_size_bytes": len(_canonical(payload)),
        "provenance_sha256": _digest(payload.get("provenance")),
        "faults_sha256": _digest(payload.get("faults")), "payload": payload}
    validate_artifact(artifact, request, candidate)
    _require(not (cancelled and cancelled()), "cold compute was cancelled")
    fd, name = tempfile.mkstemp(prefix=".cold-", dir=ARTIFACT_ROOT)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchown(stream.fileno(), 0, migration.RUNTIME_GID)
            stream.write(_canonical(artifact) + b"\n")
            stream.flush()
            os.fchmod(stream.fileno(), 0o440)
            os.fsync(stream.fileno())
        _require(not (cancelled and cancelled()), "cold compute was cancelled")
        os.link(name, destination)  # Exclusive seal; never replace an earlier receipt.
    finally:
        os.unlink(name)
    directory_fd = os.open(ARTIFACT_ROOT, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    actual, _ = _read_artifact(destination, migration.RUNTIME_GID)
    validate_artifact(actual, request, candidate)
    return destination


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--isolated-child":
        raise SystemExit("only the root supervisor may request isolated computation")
    raise SystemExit(_child(sys.argv[2], sys.argv[3]))
