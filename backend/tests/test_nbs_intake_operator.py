from __future__ import annotations

import fcntl
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "ops" / "deploy" / "seiche-nbs-intake.py"


def test_launcher_pins_the_system_python_shebang() -> None:
    assert LAUNCHER.read_bytes().splitlines()[0] == b"#!/usr/bin/python3 -I"


def test_launcher_direct_execution_ignores_hostile_pythonpath(tmp_path: Path) -> None:
    executable = tmp_path / "seiche-nbs-intake"
    executable.write_bytes(LAUNCHER.read_bytes())
    executable.chmod(0o700)
    hostile = tmp_path / "hostile-pythonpath"
    hostile.mkdir()
    sentinel = tmp_path / "pythonpath-imported"
    (hostile / "argparse.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(executable), "--help"],
        cwd=hostile,
        env=os.environ | {"PYTHONPATH": str(hostile), "PYTHONHOME": str(hostile)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not sentinel.exists()


@pytest.fixture(scope="module")
def operator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "seiche_nbs_intake_operator", LAUNCHER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def production_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operator: ModuleType,
) -> tuple[Path, Path]:
    lock_parent = tmp_path / "lock"
    lock_parent.mkdir(mode=0o700)
    lock_parent.chmod(0o700)
    production = tmp_path / "production-nbs"
    production.mkdir(mode=0o750)
    production.chmod(0o750)
    production_gid = production.stat().st_gid
    monkeypatch.setattr(operator, "BACKUP_LOCK", lock_parent / "market-backup.lock")
    monkeypatch.setattr(operator, "PRODUCTION_NBS_ROOT", production)
    monkeypatch.setattr(operator, "EXPECTED_ROOT_UID", os.geteuid())
    monkeypatch.setattr(operator, "EXPECTED_ROOT_GID", os.getegid())
    monkeypatch.setattr(
        operator.grp,
        "getgrnam",
        lambda name: SimpleNamespace(
            gr_gid=production_gid,
            gr_name=name,
        ),
    )
    return production, lock_parent


def _install_trusted_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operator: ModuleType,
    *,
    revision: str = "a" * 40,
) -> tuple[Path, Path]:
    runtime_root = tmp_path / "trusted-runtime"
    releases = runtime_root / "releases"
    release = releases / revision
    package = release / "seiche"
    package.mkdir(parents=True)
    for name in operator._RUNTIME_PACKAGE_FILES:
        source = ROOT / "backend" / "seiche" / name
        destination = package / name
        destination.write_bytes(source.read_bytes())
        destination.chmod(0o444)
    pointer = runtime_root / "current-sha"
    pointer.write_text(f"{revision}\n", encoding="ascii")
    pointer.chmod(0o444)
    for directory, mode in (
        (runtime_root, 0o755),
        (releases, 0o555),
        (release, 0o555),
        (package, 0o555),
    ):
        directory.chmod(mode)
    monkeypatch.setattr(operator, "TRUSTED_RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(operator, "TRUSTED_RUNTIME_POINTER", pointer)
    monkeypatch.setattr(operator, "TRUSTED_RUNTIME_RELEASES", releases)
    monkeypatch.setattr(operator, "TRUSTED_RUNTIME_ANCHOR", tmp_path)
    return release, pointer


def test_trusted_runtime_accepts_only_the_sealed_exact_package(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    release, _pointer = _install_trusted_runtime(tmp_path, monkeypatch, operator)

    assert operator._trusted_runtime_path() == ("a" * 40, release)


@pytest.mark.parametrize(
    "mutation",
    ["writable-pointer", "symlink-pointer", "extra-package-file", "writable-source"],
)
def test_trusted_runtime_rejects_mutable_or_ambiguous_assets(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    release, pointer = _install_trusted_runtime(tmp_path, monkeypatch, operator)
    if mutation == "writable-pointer":
        pointer.chmod(0o644)
    elif mutation == "symlink-pointer":
        pointer.unlink()
        other = tmp_path / "other-pointer"
        other.write_text(f"{'a' * 40}\n", encoding="ascii")
        other.chmod(0o444)
        pointer.symlink_to(other)
    elif mutation == "extra-package-file":
        package = release / "seiche"
        package.chmod(0o755)
        extra = package / "unexpected.py"
        extra.write_text("raise RuntimeError\n")
        extra.chmod(0o444)
        package.chmod(0o555)
    else:
        (release / "seiche" / "nbs_intake.py").chmod(0o644)

    with pytest.raises(operator.IntakeLaunchError):
        operator._trusted_runtime_path()


def test_trusted_runtime_child_rejects_pointer_change(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _release, pointer = _install_trusted_runtime(tmp_path, monkeypatch, operator)
    pointer.chmod(0o644)
    pointer.write_text(f"{'b' * 40}\n", encoding="ascii")
    pointer.chmod(0o444)

    with pytest.raises(operator.IntakeLaunchError, match="changed during intake"):
        operator._trusted_runtime_path(expected_sha="a" * 40)


def test_trusted_runtime_is_rechecked_after_the_child_returns(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    production, _lock_parent = production_boundary
    launcher = tmp_path / "seiche-nbs-intake.py"
    launcher.write_text("#!/usr/bin/python3\n")
    launcher.chmod(0o700)
    runtime_sha = "a" * 40
    runtime_calls: list[str | None] = []

    def runtime_path(*, expected_sha: str | None = None) -> tuple[str, Path]:
        runtime_calls.append(expected_sha)
        if expected_sha is not None:
            raise operator.IntakeLaunchError(
                "trusted NBS runtime changed during intake"
            )
        return runtime_sha, tmp_path / "runtime"

    monkeypatch.setattr(operator, "LAUNCHER_PATH", launcher)
    monkeypatch.setattr(operator, "_trusted_runtime_path", runtime_path)
    monkeypatch.setattr(
        operator.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    root_descriptor = os.open(production, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(operator.IntakeLaunchError, match="changed during intake"):
            operator._run_trusted_runtime(
                tmp_path / "manifest.json",
                tmp_path / "signature.json",
                tmp_path / "raw.csv",
                root_descriptor=root_descriptor,
            )
    finally:
        os.close(root_descriptor)

    assert runtime_calls == [None, runtime_sha]


def test_operator_uses_named_seiche_gid_not_the_process_primary_gid(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    production, _lock_parent = production_boundary
    named_gid = production.stat().st_gid
    requested_groups: list[str] = []
    monkeypatch.setattr(operator.os, "getegid", lambda: named_gid + 1)
    monkeypatch.setattr(
        operator.grp,
        "getgrnam",
        lambda name: (
            requested_groups.append(name)
            or SimpleNamespace(gr_gid=named_gid, gr_name=name)
        ),
    )
    monkeypatch.setattr(operator, "_run_preflight", lambda **_kwargs: None)
    monkeypatch.setattr(operator, "_run_trusted_runtime", lambda *_args, **_kwargs: 0)

    assert (
        operator.run_intake(
            tmp_path / "manifest.json",
            tmp_path / "signature.json",
            tmp_path / "raw.csv",
        )
        == 0
    )
    assert requested_groups == ["seiche"]


def test_operator_rejects_a_missing_or_root_seiche_group(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_name: str) -> SimpleNamespace:
        raise KeyError("seiche")

    monkeypatch.setattr(operator.grp, "getgrnam", missing)
    with pytest.raises(operator.IntakeLaunchError, match="group is unavailable"):
        operator._production_nbs_gid()

    monkeypatch.setattr(
        operator.grp,
        "getgrnam",
        lambda name: SimpleNamespace(gr_gid=0, gr_name=name),
    )
    with pytest.raises(operator.IntakeLaunchError, match="invalid gid"):
        operator._production_nbs_gid()


def test_operator_boundary_orders_both_preflights_inside_the_backup_lock(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _production, _lock_parent = production_boundary
    events: list[str] = []

    def preflight(*, phase: str) -> None:
        events.append(phase)

    def child(*_args, root_descriptor: int, **_kwargs) -> int:
        assert stat.S_ISDIR(os.fstat(root_descriptor).st_mode)
        events.append("child")
        return 7

    monkeypatch.setattr(operator, "_run_preflight", preflight)
    monkeypatch.setattr(operator, "_run_trusted_runtime", child)

    status = operator.run_intake(
        tmp_path / "manifest.json",
        tmp_path / "signature.json",
        tmp_path / "raw.csv",
    )

    assert status == 7
    assert events == ["pre-intake", "child", "post-intake"]
    lock_parent_fd, lock_fd = operator._open_backup_lock()
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(lock_fd)
        os.close(lock_parent_fd)


def test_failed_child_still_requires_strict_postflight(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        operator,
        "_run_preflight",
        lambda *, phase: events.append(phase),
    )

    def fail_child(*_args, **_kwargs) -> int:
        events.append("child")
        raise operator.IntakeLaunchError("child could not start")

    monkeypatch.setattr(operator, "_run_trusted_runtime", fail_child)

    with pytest.raises(operator.IntakeLaunchError, match="child could not start"):
        operator.run_intake(
            tmp_path / "manifest.json",
            tmp_path / "signature.json",
            tmp_path / "raw.csv",
        )

    assert events == ["pre-intake", "child", "post-intake"]


def test_first_preflight_failure_never_starts_the_writer(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def reject(*, phase: str) -> None:
        calls.append(phase)
        raise operator.IntakeLaunchError("volume rejected")

    monkeypatch.setattr(operator, "_run_preflight", reject)
    monkeypatch.setattr(
        operator,
        "_run_trusted_runtime",
        lambda *_args, **_kwargs: pytest.fail("writer must not start"),
    )

    with pytest.raises(operator.IntakeLaunchError, match="volume rejected"):
        operator.run_intake(
            tmp_path / "manifest.json",
            tmp_path / "signature.json",
            tmp_path / "raw.csv",
        )

    assert calls == ["pre-intake"]


def test_operator_never_bootstraps_a_missing_production_root(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    production, _lock_parent = production_boundary
    production.rmdir()
    monkeypatch.setattr(
        operator,
        "_run_preflight",
        lambda **_kwargs: pytest.fail("preflight must not run"),
    )

    with pytest.raises(operator.IntakeLaunchError, match="cannot be opened safely"):
        operator.run_intake(
            tmp_path / "manifest.json",
            tmp_path / "signature.json",
            tmp_path / "raw.csv",
        )

    assert not production.exists()


def test_operator_rejects_a_symlinked_production_root(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    production, _lock_parent = production_boundary
    production.rmdir()
    real_root = tmp_path / "real-production-nbs"
    real_root.mkdir(mode=0o750)
    production.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(operator.IntakeLaunchError, match="cannot be opened safely"):
        operator.run_intake(
            tmp_path / "manifest.json",
            tmp_path / "signature.json",
            tmp_path / "raw.csv",
        )


@pytest.mark.parametrize("unsafe", ["mode", "hardlink", "symlink", "file"])
def test_operator_rejects_unsafe_existing_backup_locks(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    unsafe: str,
    tmp_path: Path,
) -> None:
    _production, lock_parent = production_boundary
    lock = lock_parent / "market-backup.lock"
    if unsafe == "symlink":
        target = tmp_path / "lock-target"
        target.write_text("unsafe\n")
        lock.symlink_to(target)
    elif unsafe == "file":
        lock.mkdir()
    else:
        lock.write_text("lock\n")
        lock.chmod(0o600 if unsafe == "hardlink" else 0o640)
        if unsafe == "hardlink":
            os.link(lock, tmp_path / "second-lock-link")

    with pytest.raises(operator.IntakeLaunchError):
        parent_descriptor, lock_descriptor = operator._open_backup_lock()
        os.close(lock_descriptor)
        os.close(parent_descriptor)


def test_operator_rejects_wrong_lock_uid_and_gid_contracts(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production, _lock_parent = production_boundary
    parent_descriptor, lock_descriptor = operator._open_backup_lock()
    os.close(lock_descriptor)
    os.close(parent_descriptor)

    monkeypatch.setattr(operator, "EXPECTED_ROOT_GID", os.getegid() + 1)
    with pytest.raises(operator.IntakeLaunchError, match="root:root 0600"):
        operator._open_backup_lock()
    monkeypatch.setattr(operator, "EXPECTED_ROOT_GID", os.getegid())
    monkeypatch.setattr(operator, "EXPECTED_ROOT_UID", os.geteuid() + 1)
    with pytest.raises(operator.IntakeLaunchError, match="lock parent"):
        operator._open_backup_lock()


def test_group_writable_lock_parent_must_use_the_root_group(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production, lock_parent = production_boundary
    lock_parent.chmod(0o770)
    monkeypatch.setattr(operator, "EXPECTED_ROOT_GID", os.getegid() + 1)

    with pytest.raises(operator.IntakeLaunchError, match="root-grouped"):
        operator._open_backup_lock()


def test_backup_lock_wait_has_a_bounded_timeout(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production, _lock_parent = production_boundary
    first_parent, first_lock = operator._open_backup_lock()
    second_parent = -1
    second_lock = -1
    try:
        fcntl.flock(first_lock, fcntl.LOCK_EX)
        second_parent, second_lock = operator._open_backup_lock()
        monkeypatch.setattr(operator, "LOCK_TIMEOUT_SECONDS", 0.02)
        monkeypatch.setattr(operator, "LOCK_POLL_SECONDS", 0.001)
        with pytest.raises(operator.IntakeLaunchError, match="remained busy"):
            operator._acquire_backup_lock(second_lock)
    finally:
        if second_lock >= 0:
            os.close(second_lock)
        if second_parent >= 0:
            os.close(second_parent)
        os.close(first_lock)
        os.close(first_parent)


def test_deployed_child_receives_exact_one_use_capability(
    operator: ModuleType,
    production_boundary: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    production, _lock_parent = production_boundary
    launcher = tmp_path / "seiche-nbs-intake.py"
    launcher.write_text("#!/usr/bin/python3\n")
    launcher.chmod(0o700)
    runtime_sha = "a" * 40
    runtime_path = tmp_path / "runtime"
    monkeypatch.setattr(operator, "LAUNCHER_PATH", launcher)
    monkeypatch.setattr(
        operator,
        "_trusted_runtime_path",
        lambda **_kwargs: (runtime_sha, runtime_path),
    )
    observed: dict[str, object] = {}

    def run(command, **kwargs):
        environment = kwargs["env"]
        root_fd = int(environment[operator.GUARD_ROOT_FD_ENV])
        token_fd = int(environment[operator.GUARD_TOKEN_FD_ENV])
        token = os.read(token_fd, 33)
        observed.update(
            command=command,
            pass_fds=kwargs["pass_fds"],
            token=token,
            expected_token=bytes.fromhex(environment[operator.GUARD_TOKEN_ENV]),
            root_identity=(os.fstat(root_fd).st_dev, os.fstat(root_fd).st_ino),
            pythonpath=environment.get("PYTHONPATH"),
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(operator.subprocess, "run", run)
    root_descriptor = os.open(production, os.O_RDONLY | os.O_DIRECTORY)
    try:
        status = operator._run_trusted_runtime(
            tmp_path / "manifest.json",
            tmp_path / "signature.json",
            tmp_path / "raw.csv",
            root_descriptor=root_descriptor,
        )
    finally:
        os.close(root_descriptor)

    assert status == 0
    assert observed["token"] == observed["expected_token"]
    assert len(observed["token"]) == 32
    assert root_descriptor in observed["pass_fds"]
    assert observed["pythonpath"] is None
    command = observed["command"]
    assert command[:6] == [
        str(operator.SYSTEM_PYTHON),
        "-I",
        "-B",
        str(launcher),
        "--guarded-child",
        runtime_sha,
    ]
    assert str(production) not in command
    assert command[-3:] == [
        str(tmp_path / "manifest.json"),
        str(tmp_path / "signature.json"),
        str(tmp_path / "raw.csv"),
    ]
    assert "--attest-dir" not in command


def test_preflight_command_is_full_v2_fixed_path_contract(operator: ModuleType) -> None:
    assert operator._preflight_command() == [
        str(operator.SYSTEM_PYTHON),
        "-I",
        str(operator.STORAGE_PREFLIGHT),
        "--config",
        str(operator.STORAGE_CONFIG),
        "--state-path",
        str(operator.STATE_PATH),
        "--nbs-path",
        str(operator.PRODUCTION_NBS_ROOT),
        "--backup-path",
        str(operator.BACKUP_PATH),
    ]


@pytest.mark.parametrize("option", ["--root", "--attest-dir"])
def test_launcher_parser_exposes_no_production_policy_override(
    operator: ModuleType,
    option: str,
) -> None:
    with pytest.raises(SystemExit):
        operator._parser().parse_args(
            ["manifest.json", "signature.json", "raw.csv", option, "/tmp/store"]
        )
