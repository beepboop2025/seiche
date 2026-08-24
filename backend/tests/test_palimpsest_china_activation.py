from __future__ import annotations

from datetime import UTC, datetime, timedelta
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from types import SimpleNamespace

import pytest

from seiche import palimpsest_china_activation as activation


RELEASE_SHA = "a" * 40
PRODUCER_SHA = "b" * 40
SIGNER = "c" * 64


def _load_launcher() -> object:
    path = (
        Path(__file__).resolve().parents[2]
        / "ops/deploy/seiche-palimpsest-china-activate.py"
    )
    spec = importlib.util.spec_from_file_location("palimpsest_launcher_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, body: bytes) -> Path:
    path.write_bytes(body)
    path.chmod(0o600)
    return path


def test_launcher_recreates_boot_lost_deploy_lock_and_holds_it(
    tmp_path: Path,
) -> None:
    launcher = _load_launcher()
    lock_root = tmp_path / "seiche-deploy"
    lock_root.mkdir(mode=0o700)
    launcher.DEPLOY_LOCK = lock_root / "deploy.lock"  # type: ignore[attr-defined]

    descriptor = launcher._open_deploy_lock(  # type: ignore[attr-defined]
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
    )
    contender = -1
    try:
        metadata = (lock_root / "deploy.lock").stat()
        assert stat.S_IMODE(metadata.st_mode) == 0o600
        assert metadata.st_uid == os.getuid()
        assert metadata.st_gid == os.getgid()
        assert metadata.st_nlink == 1
        contender = os.open(lock_root / "deploy.lock", os.O_RDWR | os.O_NOFOLLOW)
        with pytest.raises(OSError) as locked:
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert locked.value.errno in {errno.EACCES, errno.EAGAIN}
    finally:
        if contender >= 0:
            os.close(contender)
        os.close(descriptor)


def test_launcher_tolerates_safe_concurrent_deploy_lock_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _load_launcher()
    lock_root = tmp_path / "seiche-deploy"
    lock_root.mkdir(mode=0o700)
    launcher.DEPLOY_LOCK = lock_root / "deploy.lock"  # type: ignore[attr-defined]
    real_open = os.open
    raced = False

    def racing_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal raced
        if path == "deploy.lock" and flags & os.O_EXCL and not raced:
            competing = real_open(
                path,
                os.O_RDWR | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=dir_fd,
            )
            os.fsync(competing)
            os.close(competing)
            raced = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(launcher.os, "open", racing_open)  # type: ignore[attr-defined]
    descriptor = launcher._open_deploy_lock(  # type: ignore[attr-defined]
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
    )
    try:
        assert raced is True
        metadata = (lock_root / "deploy.lock").stat()
        assert stat.S_IMODE(metadata.st_mode) == 0o600
        assert metadata.st_nlink == 1
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("unsafe", ("writable", "symlink"))
def test_launcher_rejects_unsafe_recreated_deploy_lock(
    tmp_path: Path,
    unsafe: str,
) -> None:
    launcher = _load_launcher()
    lock_root = tmp_path / "seiche-deploy"
    lock_root.mkdir(mode=0o700)
    target = lock_root / "deploy.lock"
    if unsafe == "writable":
        target.write_bytes(b"")
        target.chmod(0o644)
    else:
        target.symlink_to(tmp_path / "attacker-lock")
    launcher.DEPLOY_LOCK = target  # type: ignore[attr-defined]

    with pytest.raises(launcher.LaunchError):  # type: ignore[attr-defined]
        launcher._open_deploy_lock(  # type: ignore[attr-defined]
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
        )


def _sources(root: Path, *, generation: int = 1) -> activation.BundleSources:
    root.mkdir(mode=0o700)
    paths = [
        _write(root / spec.filename, f"{generation}:{spec.filename}\n".encode())
        for spec in activation._BUNDLE_FILE_SPECS
    ]
    return activation.BundleSources(*paths)


def _paths(tmp_path: Path) -> activation.ActivationPaths:
    uid, gid = os.getuid(), os.getgid()
    state = tmp_path / "state"
    state.mkdir(mode=0o750)
    (state / "receipts").mkdir(mode=0o700)
    environment = tmp_path / "etc"
    environment.mkdir(mode=0o750)
    dropin = tmp_path / "systemd" / "seiche-api.service.d"
    dropin.mkdir(parents=True, mode=0o755)
    lock_root = tmp_path / "locks"
    lock_root.mkdir(mode=0o700)
    deploy_lock = _write(lock_root / "deploy.lock", b"lock\n")
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o755)
    return activation.ActivationPaths(
        state_root=state,
        env_file=environment / "palimpsest-china.env",
        dropin_file=dropin / "palimpsest-china.conf",
        deploy_lock=deploy_lock,
        activation_lock=lock_root / "palimpsest-china.lock",
        runtime_release=runtime,
        release_sha=RELEASE_SHA,
        root_uid=uid,
        root_gid=gid,
        api_uid=uid,
        api_gid=gid,
        api_url="http://127.0.0.1:18787",
        python=Path(os.sys.executable),
        portable=True,
    )


def _hashes(sources: activation.BundleSources) -> dict[str, str]:
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in sources.files().items()
    }


def _candidate(
    sources: activation.BundleSources,
    *,
    accepted_at: datetime,
    run_id: int,
) -> dict[str, object]:
    return {
        "schema": activation.CANDIDATE_SCHEMA,
        "files": _hashes(sources),
        "signer_key_id": SIGNER,
        "accepted_at": accepted_at.isoformat().replace("+00:00", "Z"),
        "rights_expires_at": (accepted_at + timedelta(days=30))
        .isoformat()
        .replace("+00:00", "Z"),
        "producer_repository": "beepboop2025/palimpsest",
        "producer_sha": PRODUCER_SHA,
        "producer_workflow_run_id": run_id,
    }


def _fake_verifier(
    monkeypatch: pytest.MonkeyPatch,
    *,
    accepted_by_generation: dict[int, datetime],
    run_by_generation: dict[int, int],
) -> None:
    def verify(
        sources: activation.BundleSources,
        *,
        attest_dir: Path | None = None,
    ) -> dict[str, object]:
        del attest_dir
        generation = int(sources.manifest.read_text().split(":", 1)[0])
        return _candidate(
            sources,
            accepted_at=accepted_by_generation[generation],
            run_id=run_by_generation[generation],
        )

    monkeypatch.setattr(activation, "_candidate_from_context", verify)


def test_activation_installs_all_eleven_files_and_durable_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    sources = _sources(tmp_path / "operator")
    accepted_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    _fake_verifier(
        monkeypatch,
        accepted_by_generation={1: accepted_at},
        run_by_generation={1: 100},
    )

    result = activation.activate_bundle(sources, paths=paths)

    assert result["status"] == "activated"
    marker = result["active"]
    receipt = result["receipt"]
    assert marker["files"] == _hashes(sources)
    assert receipt["files"] == _hashes(sources)
    assert receipt["producer_sha"] == PRODUCER_SHA
    assert receipt["signer_key_id"] == SIGNER
    assert receipt["accepted_at"] == accepted_at.isoformat().replace("+00:00", "Z")
    assert receipt["runtime_proof"] == {
        "schema": activation.RUNTIME_PROOF_SCHEMA,
        "api_url": paths.api_url,
        "rest_path": "/api/v2/world-markets?section=china_macro",
        "mcp_path": "/mcp",
        "rest_files": _hashes(sources),
        "rest_signer_key_id": SIGNER,
        "mcp_files": _hashes(sources),
        "mcp_signer_key_id": SIGNER,
        "verified_at": receipt["runtime_proof"]["verified_at"],
    }
    assert activation._timestamp(
        receipt["runtime_proof"]["verified_at"],
        label="test proof time",
    ) <= activation._timestamp(receipt["recorded_at"], label="test receipt time")

    bundle = paths.state_root / marker["bundle_id"]
    assert {entry.name for entry in bundle.iterdir()} == set(sources.files())
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o750
    for name, source in sources.files().items():
        installed = bundle / name
        metadata = installed.stat()
        assert installed.read_bytes() == source.read_bytes()
        assert metadata.st_nlink == 1
        assert metadata.st_uid == paths.root_uid
        assert metadata.st_gid == paths.api_gid
        assert stat.S_IMODE(metadata.st_mode) == 0o440

    env_lines = paths.env_file.read_text().splitlines()
    assert env_lines == [
        f"{spec.environment}={bundle / spec.filename}"
        for spec in activation._BUNDLE_FILE_SPECS
    ]
    assert stat.S_IMODE(paths.env_file.stat().st_mode) == 0o640
    dropin = paths.dropin_file.read_text()
    assert f"EnvironmentFile={paths.env_file}" in dropin
    assert "EnvironmentFile=-" not in dropin
    assert f"ReadOnlyPaths={bundle} {paths.env_file}" in dropin
    assert stat.S_IMODE(paths.dropin_file.stat().st_mode) == 0o644
    assert stat.S_IMODE(paths.active_marker.stat().st_mode) == 0o400
    assert not paths.pending_marker.exists()
    receipt_path = Path(marker["receipt_path"])
    assert receipt_path.name == f"{marker['activation_id']}.json"
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o400
    assert receipt_path.read_bytes().endswith(b"\n")

    again = activation.activate_bundle(sources, paths=paths)
    assert again["status"] == "already_active"
    assert len(list(paths.receipts_dir.iterdir())) == 1
    assert not paths.pending_marker.exists()


def test_activation_state_backup_audit_round_trips_active_tree_and_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    sources = _sources(tmp_path / "operator")
    accepted_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    _fake_verifier(
        monkeypatch,
        accepted_by_generation={1: accepted_at},
        run_by_generation={1: 100},
    )
    result = activation.activate_bundle(sources, paths=paths)

    live = activation.audit_activation_state(
        paths.state_root,
        root_uid=paths.root_uid,
        root_gid=paths.root_gid,
        api_uid=paths.api_uid,
        api_gid=paths.api_gid,
    )
    assert live == {
        "schema": activation.BACKUP_STATE_SCHEMA,
        "state_root": str(paths.state_root),
        "tree_sha256": live["tree_sha256"],
        "bundles": [result["active"]["bundle_id"]],
        "receipts": [result["active"]["activation_id"]],
        "active_activation_id": result["active"]["activation_id"],
        "pending_candidate_activation_id": None,
    }

    restored = tmp_path / "restore" / paths.state_root.name
    shutil.copytree(paths.state_root, restored)
    restored.chmod(0o700)
    (restored / "receipts").chmod(0o755)
    (restored / "active.json").chmod(0o600)
    bundle = restored / result["active"]["bundle_id"]
    bundle.chmod(0o700)
    for member in bundle.iterdir():
        member.chmod(0o600)
    for member in (restored / "receipts").iterdir():
        member.chmod(0o600)

    normalized = activation.audit_activation_state(
        restored,
        root_uid=paths.root_uid,
        root_gid=paths.root_gid,
        api_uid=paths.api_uid,
        api_gid=paths.api_gid,
        normalize_restored=True,
        declared_state_root=paths.state_root,
    )

    assert normalized == live
    assert stat.S_IMODE(restored.stat().st_mode) == 0o750
    assert stat.S_IMODE((restored / "receipts").stat().st_mode) == 0o700
    assert stat.S_IMODE((restored / "active.json").stat().st_mode) == 0o400
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o750
    assert all(
        stat.S_IMODE(member.stat().st_mode) == 0o440 for member in bundle.iterdir()
    )
    assert all(
        stat.S_IMODE(member.stat().st_mode) == 0o400
        for member in (restored / "receipts").iterdir()
    )


def test_activation_state_backup_audit_supports_empty_inactive_state(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)

    result = activation.audit_activation_state(
        paths.state_root,
        root_uid=paths.root_uid,
        root_gid=paths.root_gid,
        api_uid=paths.api_uid,
        api_gid=paths.api_gid,
    )

    assert result["schema"] == activation.BACKUP_STATE_SCHEMA
    assert result["bundles"] == []
    assert result["receipts"] == []
    assert result["active_activation_id"] is None
    assert result["pending_candidate_activation_id"] is None


@pytest.mark.parametrize("unsafe", ["extra", "symlink", "hardlink"])
def test_activation_state_backup_audit_rejects_unexpected_or_linked_members(
    tmp_path: Path,
    unsafe: str,
) -> None:
    paths = _paths(tmp_path)
    if unsafe == "extra":
        _write(paths.state_root / "unexpected", b"unexpected\n")
    elif unsafe == "symlink":
        target = _write(tmp_path / "outside", b"outside\n")
        (paths.state_root / "active.json").symlink_to(target)
    else:
        receipt = _write(paths.receipts_dir / ("d" * 64 + ".json"), b"{}\n")
        os.link(receipt, tmp_path / "receipt-hardlink")

    with pytest.raises(
        activation.PalimpsestChinaActivationError,
        match="unexpected member|control file is unsafe|receipt is unsafe",
    ):
        activation.audit_activation_state(
            paths.state_root,
            root_uid=paths.root_uid,
            root_gid=paths.root_gid,
            api_uid=paths.api_uid,
            api_gid=paths.api_gid,
        )


def test_next_locked_run_recovers_an_interrupted_partial_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    first_sources = _sources(tmp_path / "operator-one", generation=1)
    second_sources = _sources(tmp_path / "operator-two", generation=2)
    accepted_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    _fake_verifier(
        monkeypatch,
        accepted_by_generation={
            1: accepted_at,
            2: accepted_at + timedelta(hours=1),
        },
        run_by_generation={1: 100, 2: 101},
    )
    first = activation.activate_bundle(first_sources, paths=paths)
    second_bundle, second_hashes = activation._install_bundle(
        second_sources.files(), paths=paths
    )
    pending = {
        "schema": activation.PENDING_SCHEMA,
        "candidate_activation_id": activation._activation_id(
            bundle_id=second_bundle.name,
            release_sha=paths.release_sha,
        ),
        "candidate_bundle_id": second_bundle.name,
        "candidate_files": second_hashes,
        "previous_activation_id": first["active"]["activation_id"],
        "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    activation._atomic_write(
        paths.pending_marker,
        activation._canonical(pending),
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o400,
    )
    activation._atomic_write(
        paths.env_file,
        activation._render_env(second_bundle),
        uid=paths.root_uid,
        gid=paths.api_gid,
        mode=0o640,
    )
    paths.dropin_file.unlink()
    proofs: list[dict[str, str]] = []

    def prove(
        *,
        paths: activation.ActivationPaths,
        expected: dict[str, object] | None,
    ) -> None:
        del paths
        assert expected is not None
        proofs.append(expected["files"])  # type: ignore[arg-type]

    monkeypatch.setattr(activation, "_restart_and_probe", prove)
    result = activation.activate_bundle(first_sources, paths=paths)

    assert result["status"] == "already_active"
    assert proofs == [_hashes(first_sources), _hashes(first_sources)]
    first_bundle = paths.state_root / first["active"]["bundle_id"]
    assert paths.env_file.read_bytes() == activation._render_env(first_bundle)
    assert paths.dropin_file.read_bytes() == activation._render_dropin(
        first_bundle, env_file=paths.env_file
    )
    assert json.loads(paths.active_marker.read_text()) == first["active"]
    assert not paths.pending_marker.exists()


def test_next_locked_run_finishes_a_proved_switch_interrupted_before_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    sources = _sources(tmp_path / "operator")
    accepted_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    _fake_verifier(
        monkeypatch,
        accepted_by_generation={1: accepted_at},
        run_by_generation={1: 100},
    )
    first = activation.activate_bundle(sources, paths=paths)
    pending = {
        "schema": activation.PENDING_SCHEMA,
        "candidate_activation_id": first["active"]["activation_id"],
        "candidate_bundle_id": first["active"]["bundle_id"],
        "candidate_files": first["active"]["files"],
        "previous_activation_id": None,
        "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    activation._atomic_write(
        paths.pending_marker,
        activation._canonical(pending),
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o400,
    )
    proofs: list[dict[str, str]] = []

    def prove(
        *,
        paths: activation.ActivationPaths,
        expected: dict[str, object] | None,
    ) -> None:
        del paths
        assert expected is not None
        proofs.append(expected["files"])  # type: ignore[arg-type]

    monkeypatch.setattr(activation, "_restart_and_probe", prove)
    result = activation.activate_bundle(sources, paths=paths)

    assert result["status"] == "already_active"
    assert proofs == [_hashes(sources), _hashes(sources)]
    assert not paths.pending_marker.exists()


def test_local_receipts_recompute_bundle_identity_and_rights_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    sources = _sources(tmp_path / "operator")
    accepted_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    _fake_verifier(
        monkeypatch,
        accepted_by_generation={1: accepted_at},
        run_by_generation={1: 100},
    )
    result = activation.activate_bundle(sources, paths=paths)

    changed_files = dict(result["receipt"])
    changed_files["files"] = dict(changed_files["files"])
    changed_files["files"]["manifest.json"] = "0" * 64
    with pytest.raises(
        activation.PalimpsestChinaActivationError,
        match="bundle identity changed",
    ):
        activation._validate_receipt(changed_files)

    expired_at_commit = dict(result["receipt"])
    expired_at_commit["rights_expires_at"] = expired_at_commit["recorded_at"]
    with pytest.raises(
        activation.PalimpsestChinaActivationError,
        match="clocks are inconsistent",
    ):
        activation._validate_receipt(expired_at_commit)


def test_immutable_receipt_is_fully_staged_before_atomic_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    destination = paths.receipts_dir / ("d" * 64 + ".json")
    body = b'{"complete":true}\n'
    observed: dict[str, object] = {}

    def publish(source: Path, target: Path, *, portable: bool = False) -> None:
        observed["source"] = source
        observed["portable"] = portable
        assert target == destination
        assert not destination.exists()
        assert source.read_bytes() == body
        assert stat.S_IMODE(source.stat().st_mode) == 0o400
        source.rename(target)

    monkeypatch.setattr(activation, "_rename_noreplace", publish)
    activation._publish_immutable_atomic(
        destination,
        body,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o400,
        portable=True,
    )

    assert destination.read_bytes() == body
    assert observed["portable"] is True
    assert not Path(observed["source"]).exists()  # type: ignore[arg-type]


def test_interrupted_stages_are_cleaned_without_lowering_receipt_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    first_sources = _sources(tmp_path / "operator-one", generation=1)
    second_sources = _sources(tmp_path / "operator-two", generation=2)
    accepted_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    _fake_verifier(
        monkeypatch,
        accepted_by_generation={
            1: accepted_at,
            2: accepted_at - timedelta(minutes=1),
        },
        run_by_generation={1: 100, 2: 99},
    )
    first = activation.activate_bundle(first_sources, paths=paths)
    receipt_stage = paths.receipts_dir / (
        f".receipt-stage-{first['active']['activation_id']}-{'e' * 32}"
    )
    receipt_stage.write_bytes(b'{"truncated":')
    receipt_stage.chmod(0o600)
    bundle_stage = paths.state_root / f".bundle-stage-{'f' * 32}"
    bundle_stage.mkdir(mode=0o700)
    partial = bundle_stage / "manifest.json"
    partial.write_bytes(b"partial")
    partial.chmod(0o440)

    with pytest.raises(
        activation.PalimpsestChinaActivationError,
        match="acceptance clock would roll back",
    ):
        activation.activate_bundle(second_sources, paths=paths)

    assert not receipt_stage.exists()
    assert not bundle_stage.exists()
    retained = list(paths.receipts_dir.iterdir())
    assert len(retained) == 1
    assert json.loads(retained[0].read_bytes()) == first["receipt"]
    assert json.loads(paths.active_marker.read_bytes()) == first["active"]


def test_activation_rejects_wall_clock_regression_before_marker_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    sources = _sources(tmp_path / "operator")
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    _fake_verifier(
        monkeypatch,
        accepted_by_generation={1: base},
        run_by_generation={1: 100},
    )
    verified_at = (base + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")

    def prove(
        *,
        paths: activation.ActivationPaths,
        expected: dict[str, object] | None,
    ) -> dict[str, object] | None:
        if expected is None:
            return None
        return {
            "schema": activation.RUNTIME_PROOF_SCHEMA,
            "api_url": paths.api_url,
            "rest_path": "/api/v2/world-markets?section=china_macro",
            "mcp_path": "/mcp",
            "rest_files": expected["files"],
            "rest_signer_key_id": expected["signer_key_id"],
            "mcp_files": expected["files"],
            "mcp_signer_key_id": expected["signer_key_id"],
            "verified_at": verified_at,
        }

    times = iter(
        (
            base + timedelta(minutes=1),
            base + timedelta(minutes=3),
            base + timedelta(minutes=2),
        )
    )
    monkeypatch.setattr(activation, "_restart_and_probe", prove)
    monkeypatch.setattr(
        activation,
        "_now_text",
        lambda: next(times).isoformat().replace("+00:00", "Z"),
    )

    with pytest.raises(
        activation.PalimpsestChinaActivationError,
        match="prior API configuration restored: system clock regressed",
    ):
        activation.activate_bundle(sources, paths=paths)

    assert not paths.active_marker.exists()
    assert not paths.pending_marker.exists()
    assert len(list(paths.receipts_dir.iterdir())) == 1


def test_activation_rolls_back_when_published_marker_cannot_be_read_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    sources = _sources(tmp_path / "operator")
    accepted_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    _fake_verifier(
        monkeypatch,
        accepted_by_generation={1: accepted_at},
        run_by_generation={1: 100},
    )
    original_atomic_write = activation._atomic_write
    corrupted = False

    def corrupt_first_marker(
        path: Path,
        body: bytes,
        *,
        uid: int,
        gid: int,
        mode: int,
    ) -> None:
        nonlocal corrupted
        original_atomic_write(path, body, uid=uid, gid=gid, mode=mode)
        if path == paths.active_marker and not corrupted:
            corrupted = True
            original_atomic_write(path, b"{}\n", uid=uid, gid=gid, mode=mode)

    monkeypatch.setattr(activation, "_atomic_write", corrupt_first_marker)

    with pytest.raises(
        activation.PalimpsestChinaActivationError,
        match="prior API configuration restored: active marker fields changed",
    ):
        activation.activate_bundle(sources, paths=paths)

    assert corrupted is True
    assert not paths.active_marker.exists()
    assert not paths.pending_marker.exists()
    assert len(list(paths.receipts_dir.iterdir())) == 1


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink"])
def test_activation_rejects_linked_operator_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
) -> None:
    paths = _paths(tmp_path)
    sources = _sources(tmp_path / "operator")
    manifest = sources.manifest
    if unsafe == "symlink":
        original = manifest.with_suffix(".original")
        manifest.rename(original)
        manifest.symlink_to(original)
    else:
        os.link(manifest, manifest.with_suffix(".extra-link"))
    accepted_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    _fake_verifier(
        monkeypatch,
        accepted_by_generation={1: accepted_at},
        run_by_generation={1: 100},
    )

    with pytest.raises(
        activation.PalimpsestChinaActivationError,
        match="metadata is unsafe|cannot be read safely",
    ):
        activation.activate_bundle(sources, paths=paths)


def test_activation_rejects_extra_member_in_existing_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    sources = _sources(tmp_path / "operator")
    accepted_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    _fake_verifier(
        monkeypatch,
        accepted_by_generation={1: accepted_at},
        run_by_generation={1: 100},
    )
    first = activation.activate_bundle(sources, paths=paths)
    bundle = paths.state_root / first["active"]["bundle_id"]
    _write(bundle / "unexpected", b"unexpected\n").chmod(0o440)

    with pytest.raises(
        activation.PalimpsestChinaActivationError,
        match="bundle members changed",
    ):
        activation.activate_bundle(sources, paths=paths)


def test_failed_activation_restores_prior_env_dropin_marker_and_runtime_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    first_sources = _sources(tmp_path / "operator-one", generation=1)
    second_sources = _sources(tmp_path / "operator-two", generation=2)
    accepted_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    _fake_verifier(
        monkeypatch,
        accepted_by_generation={
            1: accepted_at,
            2: accepted_at + timedelta(hours=1),
        },
        run_by_generation={1: 100, 2: 101},
    )
    first = activation.activate_bundle(first_sources, paths=paths)
    before = {
        path: path.read_bytes()
        for path in (paths.env_file, paths.dropin_file, paths.active_marker)
    }
    first_hashes = _hashes(first_sources)
    second_hashes = _hashes(second_sources)
    proofs: list[dict[str, str] | None] = []

    def fail_candidate(
        *,
        paths: activation.ActivationPaths,
        expected: dict[str, object] | None,
    ) -> None:
        del paths
        files = None if expected is None else expected["files"]
        proofs.append(files)  # type: ignore[arg-type]
        if files == second_hashes:
            raise activation.PalimpsestChinaActivationError("synthetic REST failure")

    monkeypatch.setattr(activation, "_restart_and_probe", fail_candidate)
    with pytest.raises(
        activation.PalimpsestChinaActivationError,
        match="prior API configuration restored",
    ):
        activation.activate_bundle(second_sources, paths=paths)

    assert proofs == [second_hashes, first_hashes]
    assert {
        path: path.read_bytes()
        for path in (paths.env_file, paths.dropin_file, paths.active_marker)
    } == before
    assert json.loads(paths.active_marker.read_text()) == first["active"]
    assert len(list(paths.receipts_dir.iterdir())) == 1


@pytest.mark.parametrize(
    ("accepted_delta", "run_id", "message"),
    [
        (timedelta(0), 101, "acceptance clock"),
        (timedelta(hours=1), 100, "producer run id"),
    ],
)
def test_retained_receipts_prevent_bundle_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    accepted_delta: timedelta,
    run_id: int,
    message: str,
) -> None:
    paths = _paths(tmp_path)
    first_sources = _sources(tmp_path / "operator-one", generation=1)
    second_sources = _sources(tmp_path / "operator-two", generation=2)
    accepted_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    _fake_verifier(
        monkeypatch,
        accepted_by_generation={1: accepted_at, 2: accepted_at + accepted_delta},
        run_by_generation={1: 100, 2: run_id},
    )
    first = activation.activate_bundle(first_sources, paths=paths)

    with pytest.raises(activation.PalimpsestChinaActivationError, match=message):
        activation.activate_bundle(second_sources, paths=paths)
    assert json.loads(paths.active_marker.read_text()) == first["active"]


def test_candidate_context_binds_all_loader_paths_and_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = _sources(tmp_path / "operator")
    now = datetime.now(UTC).replace(microsecond=0)
    observed: dict[str, object] = {}
    file_hashes = _hashes(sources)
    context = SimpleNamespace(
        owner_attested=True,
        producer={
            "repository": "beepboop2025/palimpsest",
            "commit_sha": PRODUCER_SHA,
            "workflow_run": {"run_id": 77},
        },
        producer_commit_evidence={"sha256": file_hashes["github-commit.json"]},
        producer_main_evidence={"sha256": file_hashes["github-main-branch.json"]},
        handoff_receipt={"sha256": file_hashes["handoff-receipt.json"]},
        checksum_subject={"sha256": file_hashes["SHA256SUMS"]},
        governed_lineage={
            "sha256": file_hashes["china-econ-wdi-lineage-chain.jsonl"],
            "evidence": {"sha256": file_hashes["github-commit-lineage-evidence.jsonl"]},
        },
        source_decision={
            "expires_at": (now + timedelta(days=30)).isoformat().replace("+00:00", "Z")
        },
        manifest_sha256=file_hashes["manifest.json"],
        artifact_sha256=file_hashes["artifact.jsonl"],
        input_ledger_sha256=file_hashes["input-ledger.jsonl"],
        availability_receipt_sha256=file_hashes["availability.json"],
        acceptance_sha256=file_hashes["acceptance.json"],
        acceptance_signer_key_id=SIGNER,
        accepted_at=(now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    )

    def load(manifest: Path, artifact: Path, acceptance: Path, **kwargs: object):
        observed.update(
            manifest=manifest,
            artifact=artifact,
            acceptance=acceptance,
            **kwargs,
        )
        return context

    monkeypatch.setattr(activation, "load_accepted_export", load)
    candidate = activation._candidate_from_context(sources)

    assert candidate["files"] == file_hashes
    assert observed == {
        "manifest": sources.manifest,
        "artifact": sources.artifact,
        "acceptance": sources.acceptance,
        "input_ledger_path": sources.input_ledger,
        "availability_path": sources.availability,
        "producer_commit_evidence_path": sources.producer_commit_evidence,
        "producer_main_evidence_path": sources.producer_main_evidence,
        "handoff_path": sources.handoff,
        "checksums_path": sources.checksums,
        "lineage_chain_path": sources.lineage_chain,
        "lineage_evidence_path": sources.lineage_evidence,
        "attest_dir": None,
        "now": observed["now"],
    }


def _projection(files: dict[str, str], *, signer: str = SIGNER) -> dict[str, object]:
    provenance = {
        "manifest_sha256": files["manifest.json"],
        "artifact_sha256": files["artifact.jsonl"],
        "input_ledger_sha256": files["input-ledger.jsonl"],
        "availability_receipt_sha256": files["availability.json"],
        "producer_commit_evidence": {"sha256": files["github-commit.json"]},
        "producer_main_evidence": {"sha256": files["github-main-branch.json"]},
        "handoff_receipt": {"sha256": files["handoff-receipt.json"]},
        "checksum_subject": {"sha256": files["SHA256SUMS"]},
        "governed_lineage": {
            "sha256": files["china-econ-wdi-lineage-chain.jsonl"],
            "evidence": {"sha256": files["github-commit-lineage-evidence.jsonl"]},
        },
        "acceptance_sha256": files["acceptance.json"],
        "acceptance_signer_key_id": signer,
        "owner_attestation": "ed25519",
    }
    return {
        "china_macro": {
            "economic_context": {
                "schema": "seiche.palimpsest-china-economic-context.v1",
                "context_only": True,
                "scoring_eligible": False,
                "cn_cny_gauge_eligible": False,
                "provenance": provenance,
            }
        }
    }


def test_rest_mcp_projection_proof_binds_every_installed_file() -> None:
    files = {name: hashlib.sha256(name.encode()).hexdigest() for name in _hash_names()}
    payload = _projection(files)
    expected = {"files": files, "signer_key_id": SIGNER}
    activation._assert_projection(payload, expected)

    payload["china_macro"]["economic_context"]["provenance"][  # type: ignore[index]
        "producer_main_evidence"
    ]["sha256"] = "0" * 64
    with pytest.raises(
        activation.PalimpsestChinaActivationError,
        match="wrong China economic authority",
    ):
        activation._assert_projection(payload, expected)


def _hash_names() -> set[str]:
    return {spec.filename for spec in activation._BUNDLE_FILE_SPECS}


def test_guarded_verifier_requires_exact_eleven_paths() -> None:
    with pytest.raises(
        activation.PalimpsestChinaActivationError,
        match="exactly eleven paths",
    ):
        activation.guarded_verify_main(["/tmp/a"] * 10)


def test_trusted_runtime_allows_only_the_deliberately_empty_package_marker(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "__init__.py"
    marker.write_bytes(b"")
    marker.chmod(0o444)

    with pytest.raises(
        activation.PalimpsestChinaActivationError,
        match="metadata is unsafe",
    ):
        activation._stable_read(
            marker,
            label="nonempty authority input",
            maximum=1024,
            uid=os.getuid(),
            gid=os.getgid(),
            modes=frozenset({0o444}),
        )
    assert (
        activation._stable_read(
            marker,
            label="empty trusted package marker",
            maximum=1024,
            uid=os.getuid(),
            gid=os.getgid(),
            modes=frozenset({0o444}),
            minimum=0,
        )
        == b""
    )


def test_production_paths_reject_command_path_substitution() -> None:
    paths = activation.ActivationPaths(
        state_root=activation.PRODUCTION_STATE_ROOT,
        env_file=activation.PRODUCTION_ENV_FILE,
        dropin_file=activation.PRODUCTION_DROPIN_FILE,
        deploy_lock=activation.PRODUCTION_DEPLOY_LOCK,
        activation_lock=activation.PRODUCTION_ACTIVATION_LOCK,
        runtime_release=(activation.PRODUCTION_RUNTIME_ROOT / "releases" / RELEASE_SHA),
        release_sha=RELEASE_SHA,
        root_uid=0,
        root_gid=0,
        api_uid=1,
        api_gid=1,
        runuser=Path("/tmp/substituted-runuser"),
    )

    with pytest.raises(
        activation.PalimpsestChinaActivationError,
        match="paths or identities changed",
    ):
        activation._validate_activation_paths(paths)


def test_release_git_checks_disable_repository_controlled_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout=b"ok\n", stderr=b"")

    monkeypatch.setattr(activation.subprocess, "run", run)
    result = activation._runtime_command("status", label="test Git command")

    assert result.stdout == b"ok\n"
    assert observed["command"] == [
        str(activation.PRODUCTION_GIT),
        "-c",
        f"safe.directory={activation.PRODUCTION_REPOSITORY}",
        "status",
    ]
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_NO_LAZY_FETCH"] == "1"
    assert environment["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"


def test_production_candidate_verifier_runs_as_seiche_under_empty_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    sources = _sources(tmp_path / "bundle")
    hashes = _hashes(sources)
    accepted_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    candidate = _candidate(
        sources,
        accepted_at=accepted_at,
        run_id=100,
    )
    paths = activation.ActivationPaths(
        **{
            field: getattr(paths, field)
            for field in activation.ActivationPaths.__dataclass_fields__
            if field not in {"portable"}
        },
        portable=False,
    )
    observed: dict[str, object] = {}

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=activation._canonical(candidate),
            stderr=b"",
        )

    monkeypatch.setattr(activation.subprocess, "run", run)
    verified = activation._verify_candidate(
        tmp_path / "bundle",
        hashes=hashes,
        paths=paths,
    )

    assert verified == candidate
    command = observed["command"]
    assert isinstance(command, list)
    assert command[:6] == [
        str(paths.runuser),
        "-u",
        paths.api_user,
        "--",
        str(paths.env_program),
        "-i",
    ]
    assert "PYTHONNOUSERSITE=1" in command
    assert command.count("-I") == 1
    assert command.count("-B") == 1
    assert command[-11:] == [str(path) for path in sources.files().values()]
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["env"] == {
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PYTHONNOUSERSITE": "1",
    }


def test_installer_and_launcher_are_inert_and_release_addressed() -> None:
    root = Path(__file__).resolve().parents[2]
    installer = (root / "ops/deploy/install-palimpsest-china-activation.sh").read_text()
    launcher = (root / "ops/deploy/seiche-palimpsest-china-activate.py").read_text()
    market = (root / "ops/deploy/install-market-platform.sh").read_text()
    wrapper = (root / "ops/deploy/seiche-deploy-wrapper.sh").read_text()

    assert "market.env" not in installer
    assert "/var/lib/seiche-palimpsest-china" in installer
    assert "/var/lib/seiche/palimpsest-china" not in installer
    assert "/opt/seiche-palimpsest-china" in installer
    assert 'validate_directory "$STATE_ROOT" root seiche 750' in installer
    assert 'validate_directory "$RECEIPTS_ROOT" root root 700' in installer
    assert "root:root:600:1" in installer
    assert "os.replace(pointer_stage, pointer)" in installer
    assert "fsync_directory(runtime)" in installer
    assert "SEICHE_PRIVILEGED_ASSET_ROOT" in installer
    assert "SEICHE_RELEASE_TARGET_SHA" in installer
    assert "validate_root_traversal" in installer
    assert 'validate_root_traversal "$ASSET_ROOT"' in installer
    assert 'validate_root_traversal "$STATE_ROOT"' in installer
    assert "EnvironmentFile" not in installer

    assert launcher.startswith("#!/usr/bin/python3 -I\n")
    assert 'DEPLOY_LOCK = Path("/run/seiche-deploy/deploy.lock")' in launcher
    assert "_validate_runtime(release_sha)" in launcher
    assert 'minimum=0 if name == "__init__.py" else 1' in launcher
    assert "deploy_lock_descriptor=lock" in launcher
    assert "BundleSources(*sources)" in launcher
    assert "len(arguments) != len(_SOURCE_LABELS)" in launcher

    assert "install-palimpsest-china-activation.sh" in market
    assert "No Palimpsest" in market
    assert "palimpsest-china-activation-launcher" in wrapper
    assert "palimpsest-china-runtime-current-sha" in wrapper
    assert "backend/seiche/palimpsest_china_activation.py" in wrapper
