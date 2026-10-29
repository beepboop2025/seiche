"""Cold candidate evidence is fresh, bound, non-authoritative and isolated."""
from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

from seiche import assemble, stateful_application as app, stateful_cold_snapshot as cold
from test_stateful_application import signed, transition  # noqa: F401


@pytest.fixture
def cold_case(transition, signed, fake_snap, monkeypatch):  # noqa: F811
    _, _, request, candidate, _, parent = transition
    request = deepcopy(request)
    request["operation"] = cold.OPERATION
    request.pop("request_id")
    request["request_id"] = app.digest(request)
    candidate = deepcopy(candidate)
    candidate["request"].update(id=request["request_id"], sha256=app.digest(request))
    fence = deepcopy(candidate["source_fence"]["payload"])
    fence["request_id"] = request["request_id"]
    candidate["source_fence"] = signed("source_stopped", fence)
    # Parent chain signatures have independent existing tests; isolate the
    # artifact boundary only after that prerequisite has passed.
    monkeypatch.setattr(app, "load_parent", lambda *a, **kw: parent)
    start = cold._time(fence["requested_at"])
    generated = start + timedelta(milliseconds=100)
    completed = start + timedelta(milliseconds=200)
    now = start + timedelta(milliseconds=300)
    payload = deepcopy(fake_snap)
    payload.update(generated_at=generated.isoformat(), version=assemble.VERSION_LABEL,
                   provenance=[{"source": "fixture", "asof": "2026-10-25"}], faults=[])
    artifact = {
        "schema": cold.SCHEMA, "binding": cold._binding(request, candidate),
        "authority": dict(cold.AUTHORITY), "started_at": start.isoformat(),
        "completed_at": completed.isoformat(), "generated_at": payload["generated_at"],
        "payload_sha256": cold._digest(payload),
        "payload_size_bytes": len(cold._canonical(payload)),
        "provenance_sha256": cold._digest(payload["provenance"]),
        "faults_sha256": cold._digest(payload["faults"]), "payload": payload,
    }
    return request, candidate, artifact, now


def test_cold_receipt_preserves_original_clocks_and_no_authority(cold_case):
    request, candidate, artifact, now = cold_case
    before = cold._canonical(artifact)
    result = cold.validate_artifact(artifact, request, candidate, now=now)
    assert cold._canonical(artifact) == before
    assert result["payload"]["generated_at"] == artifact["generated_at"]
    assert result["cache_source"] == "application_cold_candidate"
    assert result["evidence"]["authority"] == cold.AUTHORITY
    assert not result["evidence"]["authority"]["writers_started"]
    assert not result["evidence"]["authority"]["can_publish"]
    assert "release_candidate" not in result
    assert "release_handoff_id" not in result


@pytest.mark.parametrize("field", ["request_sha256", "candidate_sha256", "source_fence_sha256"])
def test_receipt_rejects_changed_binding(cold_case, field):
    request, candidate, artifact, now = cold_case
    artifact["binding"][field] = "0" * 64
    with pytest.raises(cold.ColdSnapshotError, match="binding"):
        cold.validate_artifact(artifact, request, candidate, now=now)


@pytest.mark.parametrize("field", ["source", "railway", "parent"])
def test_receipt_rejects_rebound_source_provider_parent(cold_case, field):
    request, candidate, artifact, now = cold_case
    artifact["binding"] = deepcopy(artifact["binding"])
    artifact["binding"][field] = {}
    with pytest.raises(cold.ColdSnapshotError, match="binding"):
        cold.validate_artifact(artifact, request, candidate, now=now)


@pytest.mark.parametrize("change", ["payload", "faults", "provenance", "authority", "clock"])
def test_receipt_rejects_tampering(cold_case, change):
    request, candidate, artifact, now = cold_case
    if change == "payload":
        artifact["payload"]["version"] = "different"
    elif change == "authority":
        artifact["authority"]["writers_started"] = True
    elif change == "clock":
        artifact["generated_at"] = now.isoformat()
    else:
        artifact[change + "_sha256"] = "0" * 64
    with pytest.raises(cold.ColdSnapshotError):
        cold.validate_artifact(artifact, request, candidate, now=now)


def test_receipt_rejects_stale_and_future_evidence(cold_case):
    request, candidate, artifact, now = cold_case
    with pytest.raises(cold.ColdSnapshotError, match="stale"):
        cold.validate_artifact(artifact, request, candidate, now=now + timedelta(seconds=901))
    with pytest.raises(cold.ColdSnapshotError, match="future"):
        cold.validate_artifact(artifact, request, candidate, now=now - timedelta(seconds=1))


def test_receipt_rechecks_rights(cold_case, monkeypatch):
    request, candidate, artifact, now = cold_case
    def refuse(_payload):
        raise ValueError("restricted source")
    monkeypatch.setattr(assemble, "_assert_snapshot_rights", refuse)
    with pytest.raises(ValueError, match="restricted"):
        cold.validate_artifact(artifact, request, candidate, now=now)


def test_normal_upgrade_does_not_read_cold_source_or_artifact(transition, monkeypatch):  # noqa: F811
    _, environment, request, _, _, _ = transition
    monkeypatch.setattr(app, "read_document", lambda _: request)
    monkeypatch.setattr(app, "load_request", lambda: pytest.fail("normal mode must not load cold source"))
    monkeypatch.setattr(cold, "_read_artifact", lambda *a: pytest.fail("normal mode must not load artifact"))
    environment = {**environment, "SEICHE_RAILWAY_STATEFUL_MODE": "cutover_candidate",
                   "SEICHE_RAILWAY_APPLICATION_REQUEST_ID": request["request_id"]}
    assert cold.load_candidate_snapshot(environment) is None


def test_environment_excludes_inherited_credentials(tmp_path, monkeypatch):
    if os.geteuid() != 0:
        pytest.skip("root ownership setup is separately checked by native root isolation proof")
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret@production/db")
    monkeypatch.setenv("SEICHE_DATABASE_URL", "postgresql://secret@production/db")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "never-in-child")
    environment = cold.isolated_environment(tmp_path)
    assert not any(key in environment for key in
                   ("DATABASE_URL", "SEICHE_DATABASE_URL", "AWS_SECRET_ACCESS_KEY", "SEICHE_RELEASE_SHA"))
    assert environment["SEICHE_RUNTIME_DATA_DIR"] == str(tmp_path / "data")
    assert environment["OMP_NUM_THREADS"] == "1"


def test_real_linux_kernel_denies_native_and_descendant_production_writes():
    """Run explicitly as root in the qualified image; a skip is no proof."""
    if sys.platform != "linux" or os.geteuid() != 0:
        pytest.skip("requires separate native Linux root-to-dedicated-UID isolation acceptance")
    root = Path(tempfile.mkdtemp(prefix="cold-isolation-test-", dir="/tmp"))
    scratch = root / "scratch"
    protected = root / "production-canary"
    try:
        root.chmod(0o755)
        scratch.mkdir(mode=0o700)
        os.chown(scratch, cold.CHILD_UID, cold.CHILD_GID)
        protected.write_bytes(b"immutable production canary")
        protected.chmod(0o666)  # BSD mode alone deliberately permits this write.
        before = protected.stat()
        environment = cold.isolated_environment(scratch)
        backend = str(Path(cold.__file__).resolve().parent.parent)
        program = r"""
import ctypes,json,os,pathlib,resource,subprocess,sys
sys.path.insert(0,sys.argv[1])
from seiche.stateful_cold_snapshot import confine_child
scratch=pathlib.Path(sys.argv[2]); protected=pathlib.Path(sys.argv[3])
confine_child(scratch)
(scratch/"allowed").write_text("private scratch works")
libc=ctypes.CDLL(None,use_errno=True)
fd=libc.open(os.fsencode(protected),os.O_WRONLY|os.O_TRUNC)
native_denied=fd<0 and ctypes.get_errno() in (1,13)
if fd>=0:os.close(fd)
(scratch/"escape").symlink_to(protected)
try:(scratch/"escape").write_text("forbidden"); symlink_denied=False
except PermissionError:symlink_denied=True
child=subprocess.run([sys.executable,"-I","-B","-c","import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(\"forbidden\")",str(protected)],capture_output=True)
print(json.dumps({"uid":os.getuid(),"gid":os.getgid(),"groups":os.getgroups(),
 "no_new_privs":libc.prctl(39,0,0,0,0),"native_denied":native_denied,
 "process_limit":list(resource.getrlimit(resource.RLIMIT_NPROC)),
 "symlink_denied":symlink_denied,"descendant_denied":child.returncode!=0,
 "credentials_absent":not any(k in os.environ for k in ("DATABASE_URL","SEICHE_DATABASE_URL","AWS_SECRET_ACCESS_KEY"))}))
"""
        result = subprocess.run([sys.executable, "-I", "-B", "-c", program, backend,
                                 str(scratch), str(protected)], cwd=scratch, env=environment,
            user=cold.CHILD_UID, group=cold.CHILD_GID, extra_groups=[], start_new_session=True,
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        observed = json.loads(result.stdout)
        assert observed == {"uid": 32001, "gid": 32001, "groups": [], "no_new_privs": 1,
                            "process_limit": [64, 64],
                            "native_denied": True, "symlink_denied": True,
                            "descendant_denied": True, "credentials_absent": True}
        assert (scratch / "allowed").read_text() == "private scratch works"
        after = protected.stat()
        assert protected.read_bytes() == b"immutable production canary"
        assert (before.st_ino, before.st_size, before.st_mtime_ns) == (
            after.st_ino, after.st_size, after.st_mtime_ns)
    finally:
        shutil.rmtree(root)


def test_cancelled_builder_never_creates_an_artifact(tmp_path, monkeypatch):
    if os.geteuid() != 0:
        pytest.skip("root supervisor cancellation has separate native root acceptance")
    destination = tmp_path / "artifacts"
    monkeypatch.setattr(cold, "ARTIFACT_ROOT", destination)
    monkeypatch.setattr(cold, "_compute", lambda *a, **kw: pytest.fail("cancelled build must not run"))
    with pytest.raises(cold.ColdSnapshotError, match="cancelled"):
        cold.build_candidate_snapshot({}, {}, cancelled=lambda: True)
    assert not destination.exists()


@pytest.mark.parametrize("cancelled,timeout,message", [(True, 10, "cancelled"), (False, 0, "deadline")])
def test_compute_cancellation_and_timeout_clean_owned_scratch(cancelled, timeout, message):
    if os.geteuid() != 0 or sys.platform != "linux":
        pytest.skip("requires native root child supervision acceptance")
    before = set(Path("/tmp").glob("seiche-cold-build-*"))
    with pytest.raises(cold.ColdSnapshotError, match=message):
        cold._compute({}, python=sys.executable, timeout=timeout, cancelled=lambda: cancelled)
    assert set(Path("/tmp").glob("seiche-cold-build-*")) == before


def test_terminal_stderr_bound_rejects_fast_exited_child(tmp_path, monkeypatch):
    if os.geteuid() != 0:
        pytest.skip("requires native root scratch ownership acceptance")
    monkeypatch.setattr(cold, "MAX_STDERR", 16)
    class Exited:
        returncode = 0
        pid = 999999
        def __init__(self, *args, **kwargs):
            kwargs["stderr"].write(b"x" * 17)
            kwargs["stderr"].flush()
        def poll(self):
            return 0
    monkeypatch.setattr(cold.subprocess, "Popen", Exited)
    monkeypatch.setattr(cold, "_stop_child", lambda process: None)
    with pytest.raises(cold.ColdSnapshotError, match="output exceeded"):
        cold._compute({}, python=sys.executable, timeout=10)


@pytest.mark.parametrize("violation", ["symlink", "hardlink", "writable", "wrong_owner"])
def test_reader_rejects_untrusted_ingress(tmp_path, violation):
    if os.geteuid() != 0:
        pytest.skip("root artifact metadata acceptance executed separately")
    root = tmp_path / "root-artifacts"
    root.mkdir(mode=0o750)
    path = root / "artifact.json"
    path.write_bytes(b"{}\n")
    path.chmod(0o440)
    if violation == "symlink":
        target = root / "target.json"
        path.rename(target)
        path.symlink_to(target)
    elif violation == "hardlink":
        os.link(path, root / "another.json")
    elif violation == "writable":
        path.chmod(0o640)
    else:
        os.chown(path, cold.CHILD_UID, cold.CHILD_GID)
    with pytest.raises((cold.ColdSnapshotError, OSError)):
        cold._read_artifact(path, os.getegid())


def test_failed_compute_diagnostic_is_bounded_escaped_and_reports_exit_code():
    import io

    stream = io.BytesIO(b"discarded-prefix" + b"x" * 5000 + b"\nMemoryError\x1b[31m\xff")
    message = cold._compute_failure(-6, stream)
    assert "exit_code=-6" in message
    assert "stderr_bytes=" in message
    assert "discarded-prefix" not in message
    assert "MemoryError" in message
    assert "\n" not in message
    assert "\x1b" not in message
    encoded_tail = message.split("stderr_tail=", 1)[1][:-1]
    assert len(json.loads(encoded_tail)) <= 4096
    assert len(message) < 5000


def test_fast_failed_worker_keeps_error_before_cleanup(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(cold.tempfile, "mkdtemp", lambda **kwargs: str(scratch))
    monkeypatch.setattr(cold.os, "chown", lambda *args: None)
    monkeypatch.setattr(cold, "isolated_environment", lambda path: {})
    monkeypatch.setattr(cold, "_stop_child", lambda process: None)

    class FailedChild:
        returncode = 17

        def __init__(self, *args, **kwargs):
            kwargs["stderr"].write(b"RuntimeError: cannot start worker\n")
            kwargs["stderr"].flush()
            kwargs["stdout"].write(b"payload-must-not-be-a-diagnostic")

        def poll(self):
            return self.returncode

    monkeypatch.setattr(cold.subprocess, "Popen", FailedChild)
    with pytest.raises(cold.ColdSnapshotError, match="exit_code=17") as result:
        cold._compute({}, python=sys.executable, timeout=10)
    assert "cannot start worker" in str(result.value)
    assert "payload-must-not-be-a-diagnostic" not in str(result.value)
    assert not scratch.exists()


def test_native_numerical_workers_fit_cold_address_space_limit():
    """Exercise native allocations near the allowed task bound, not a mock."""
    if sys.platform != "linux" or os.geteuid() != 0:
        pytest.skip("requires native Linux root-to-dedicated-UID allocation proof")
    root = Path(tempfile.mkdtemp(prefix="cold-numerics-test-", dir="/tmp"))
    os.chown(root, cold.CHILD_UID, cold.CHILD_GID)
    try:
        environment = cold.isolated_environment(root)
        backend = str(Path(cold.__file__).resolve().parent.parent)
        program = r"""
import json,pathlib,resource,sys
sys.path.insert(0,sys.argv[1])
from seiche.stateful_cold_snapshot import confine_child
assert pathlib.Path(sys.modules["seiche.stateful_cold_snapshot"].__file__).resolve().parent.parent == pathlib.Path(sys.argv[1]).resolve()
confine_child(pathlib.Path(sys.argv[2]))
from seiche import assemble
import numpy as np
from scipy.linalg.blas import dgemm
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
barrier=Barrier(56)
def work(_index):
    matrix=np.ones((128,128))
    barrier.wait(timeout=30)
    numpy_result=matrix@matrix
    scipy_result=dgemm(1.0,matrix,matrix)
    barrier.wait(timeout=30)
    return float(numpy_result[0,0]+scipy_result[0,0])
with ThreadPoolExecutor(max_workers=56) as pool:
    results=list(pool.map(work,range(56)))
print(json.dumps({"results":results,
    "address_limit":list(resource.getrlimit(resource.RLIMIT_AS)),
    "task_limit":list(resource.getrlimit(resource.RLIMIT_NPROC))}))
"""
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", program, backend, str(root)],
            cwd=root, env=environment, user=cold.CHILD_UID, group=cold.CHILD_GID,
            extra_groups=[], start_new_session=True,
            capture_output=True, text=True, timeout=90,
        )
        assert result.returncode == 0, result.stderr
        observed = json.loads(result.stdout)
        assert observed == {
            "results": [256.0] * 56,
            "address_limit": [4 * 1024**3, 4 * 1024**3],
            "task_limit": [64, 64],
        }
    finally:
        shutil.rmtree(root)
