"""Release-boundary contracts, exercised without a host or external network."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import pwd
from datetime import UTC, datetime
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]
CADDY_INSTALLER = ROOT / "ops" / "deploy" / "install-caddy.sh"
EXTERNAL_SMOKE = ROOT / "ops" / "deploy" / "external-route-smoke.sh"
CADDYFILE = ROOT / "ops" / "Caddyfile"
EXTERNAL_ROUTES = ROOT / "ops" / "deploy" / "external-smoke-routes.txt"
LEGACY_INSTALLER = ROOT / "ops" / "deploy" / "install.sh"
WORLD_MODEL_DELIVERY_INSTALLER = (
    ROOT / "ops" / "deploy" / "install-world-model-delivery-relay.sh"
)
FORCED_DEPLOY = ROOT / "ops" / "deploy" / "trigger-forced-deploy.sh"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-hetzner.yml"
BOX_UPDATE = ROOT / "ops" / "deploy" / "box-update.sh"
DEPLOY_WRAPPER = ROOT / "ops" / "deploy" / "seiche-deploy-wrapper.sh"
RELEASE_POLLER = ROOT / "ops" / "deploy" / "seiche-release-poll.sh"
RELEASE_POLLER_INSTALLER = ROOT / "ops" / "deploy" / "install-release-poller.sh"
RELEASE_POLLER_SERVICE = ROOT / "ops" / "deploy" / "seiche-release-poll.service"
RELEASE_POLLER_TIMER = ROOT / "ops" / "deploy" / "seiche-release-poll.timer"
RELEASE_ALLOWED_SIGNERS = ROOT / "ops" / "deploy" / "release-allowed-signers"
MARKET_INSTALLER = ROOT / "ops" / "deploy" / "install-market-platform.sh"
MARKET_WORKER = ROOT / "ops" / "deploy" / "seiche-market-worker.service"
SOURCE_WORKER = ROOT / "ops" / "deploy" / "seiche-source-worker.service"
DATA_READINESS_SERVICE = ROOT / "ops" / "deploy" / "seiche-data-readiness.service"
DATA_READINESS_TIMER = ROOT / "ops" / "deploy" / "seiche-data-readiness.timer"
RECOVERY_SEAL = ROOT / "ops" / "deploy" / "seiche-release-recovery-seal.sh"
RECOVERY_SEAL_SERVICE = ROOT / "ops" / "deploy" / "seiche-release-recovery-seal.service"
PULL_UNIT = ROOT / "ops" / "deploy" / "seiche-pull.service"
PROMOTION_UNIT = ROOT / "ops" / "deploy" / "seiche-snapshot-promote.service"
LEGACY_UPDATE_RETIRER = ROOT / "ops" / "deploy" / "retire-legacy-update-units.sh"
PYPROJECT = ROOT / "backend" / "pyproject.toml"


def _safe_privileged_fixture_ancestry(path: Path) -> bool:
    expected_owners = {(0, 0), (os.getuid(), os.getgid())}
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = os.lstat(current)
        except OSError:
            return False
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or (metadata.st_uid, metadata.st_gid) not in expected_owners
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            return False
    return os.access(path, os.W_OK | os.X_OK)


def _make_privileged_fixture_tree_removable(root: Path) -> None:
    """Restore directory write access without following adversarial symlinks."""

    for current, directories, _files in os.walk(root, topdown=False, followlinks=False):
        for directory in directories:
            child = Path(current) / directory
            if not child.is_symlink():
                child.chmod(0o700)
        Path(current).chmod(0o700)


@pytest.fixture
def secure_privileged_tmp_path():
    candidates = (
        Path(tempfile.gettempdir()).resolve(strict=True),
        Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=True),
    )
    base = next(
        (
            candidate
            for candidate in candidates
            if _safe_privileged_fixture_ancestry(candidate)
        ),
        None,
    )
    if base is None:
        pytest.fail("no safe owned ancestry is available for privileged-path tests")
    root = Path(tempfile.mkdtemp(prefix=".seiche-privileged-test-", dir=base))
    root.chmod(0o700)
    assert _safe_privileged_fixture_ancestry(root)
    try:
        yield root
    finally:
        _make_privileged_fixture_tree_removable(root)
        shutil.rmtree(root)


def _executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\nset -u\n" + body)
    path.chmod(0o755)
    return path


def _git(*arguments: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _materialized_privileged_assets(tmp_path: Path) -> tuple[Path, str, Path]:
    """Publish the current privileged source set through the real materializer."""

    wrapper_text = DEPLOY_WRAPPER.read_text(encoding="utf-8")
    match = re.search(r"REQUIRED_MODES = (\{.*?\n\})\n\n", wrapper_text, re.DOTALL)
    assert match is not None
    required_modes = ast.literal_eval(match.group(1))
    assert isinstance(required_modes, dict)

    repository = tmp_path / "privileged-asset-repository"
    _git("init", "-b", "main", str(repository), cwd=tmp_path)
    _git("config", "user.name", "Asset Fixture", cwd=repository)
    _git("config", "user.email", "asset-fixture@example.invalid", cwd=repository)
    for relative, git_mode in required_modes.items():
        source = ROOT / relative
        assert source.is_file(), relative
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o755 if git_mode == "100755" else 0o644)
    _git("add", "--all", cwd=repository)
    _git("commit", "-m", "fixture: privileged assets", cwd=repository)
    target = _git("rev-parse", "HEAD", cwd=repository)

    parent = tmp_path / "signed-asset-parent"
    parent.mkdir(mode=0o700)
    parent.chmod(0o700)
    destination = parent / f"release-assets-{target}"
    result = subprocess.run(
        ["bash", str(DEPLOY_WRAPPER)],
        env=os.environ
        | {
            "SEICHE_DEPLOY_ASSET_TEST_ONLY": "1",
            "SEICHE_ALLOW_NON_ROOT_ASSET_TEST": "1",
            "SEICHE_ASSET_TEST_REPO": str(repository),
            "SEICHE_ASSET_TEST_TARGET": target,
            "SEICHE_ASSET_TEST_PARENT": str(parent),
            "SEICHE_ASSET_TEST_DESTINATION": str(destination),
            "SEICHE_ASSET_TEST_PYTHON": sys.executable,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == str(destination)
    return destination, target, repository


def _isolated_nbs_runtime_test_python() -> str:
    probe = (
        "import sys\n"
        "supported = sys.version_info >= (3, 11)\n"
        "isolated = not any(\n"
        "    path == '/home' or path.startswith('/home/') for path in sys.path\n"
        ")\n"
        "raise SystemExit(0 if supported and isolated else 1)\n"
    )
    for candidate in (Path(sys.executable), Path("/usr/bin/python3")):
        if (
            not candidate.is_absolute()
            or not candidate.is_file()
            or not os.access(candidate, os.X_OK)
        ):
            continue
        result = subprocess.run(
            [str(candidate), "-I", "-B", "-c", probe],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return str(candidate)
    pytest.fail("no supported Python with an isolated non-/home import path exists")


def _nbs_runtime_test_fixture(
    tmp_path: Path, *, target: str = "a" * 40
) -> tuple[Path, Path, dict[str, str]]:
    asset_root = tmp_path / "runtime-assets"
    package_source = asset_root / "backend" / "seiche"
    package_source.mkdir(parents=True)
    for name in ("__init__.py", "nbs_intake.py", "nbs_trust.py"):
        shutil.copyfile(ROOT / "backend" / "seiche" / name, package_source / name)
    asset_root.chmod(0o700)
    runtime_root = tmp_path / "nbs-runtime"
    runtime_root.mkdir(mode=0o755)
    runtime_root.chmod(0o755)
    environment = os.environ | {
        "SEICHE_PRIVILEGED_ASSET_ROOT": str(asset_root),
        "SEICHE_RELEASE_TARGET_SHA": target,
        "SEICHE_NBS_RUNTIME_ROOT": str(runtime_root),
        "SEICHE_NBS_RUNTIME_TEST_ONLY": "1",
        "SEICHE_ALLOW_NON_ROOT_INSTALL_TEST": "1",
        "SEICHE_NBS_RUNTIME_TEST_PYTHON": _isolated_nbs_runtime_test_python(),
    }
    return asset_root, runtime_root, environment


def _run_nbs_runtime_test(
    environment: dict[str, str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(MARKET_INSTALLER)],
        env=environment,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _nbs_evidence_tree_test_fixture(
    tmp_path: Path,
) -> tuple[Path, int, dict[str, str]]:
    primary_gid = os.getgid()
    supplementary_gid = next(
        (group for group in os.getgroups() if group != primary_gid), None
    )
    if supplementary_gid is None:
        pytest.skip("portable evidence-tree test needs a supplementary group")

    asset_root = tmp_path / "evidence-assets"
    asset_root.mkdir(mode=0o700)
    runtime_root = tmp_path / "unused-runtime"
    evidence_root = tmp_path / "nbs-evidence"
    evidence_root.mkdir(mode=0o750)
    os.chown(evidence_root, -1, supplementary_gid)
    evidence_root.chmod(0o750)
    environment = os.environ | {
        "SEICHE_PRIVILEGED_ASSET_ROOT": str(asset_root),
        "SEICHE_RELEASE_TARGET_SHA": "a" * 40,
        "SEICHE_NBS_RUNTIME_ROOT": str(runtime_root),
        "SEICHE_NBS_STATE_DIR": str(evidence_root),
        "SEICHE_NBS_EVIDENCE_TREE_TEST_ONLY": "1",
        "SEICHE_ALLOW_NON_ROOT_INSTALL_TEST": "1",
        "SEICHE_NBS_EVIDENCE_TEST_PYTHON": sys.executable,
        "SEICHE_NBS_EVIDENCE_TEST_GID": str(supplementary_gid),
    }
    return evidence_root, supplementary_gid, environment


def _run_nbs_evidence_tree_test(
    environment: dict[str, str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(MARKET_INSTALLER)],
        env=environment,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _directory_identity(path: Path) -> tuple[int, int, int, int, int]:
    metadata = path.lstat()
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        metadata.st_gid,
        stat.S_IMODE(metadata.st_mode),
    )


def test_nbs_evidence_tree_publisher_is_fresh_idempotent_and_isolated(
    tmp_path: Path,
):
    evidence_root, seiche_gid, environment = _nbs_evidence_tree_test_fixture(tmp_path)
    assert seiche_gid != os.getgid()
    hostile = tmp_path / "hostile-evidence-cwd"
    hostile.mkdir()
    sentinel = tmp_path / "evidence-shadow-imported"
    (hostile / "secrets.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    environment |= {"PYTHONPATH": str(hostile), "PYTHONHOME": str(hostile)}

    first = _run_nbs_evidence_tree_test(environment, cwd=hostile)
    assert first.returncode == 0, first.stdout + first.stderr
    assert not sentinel.exists()
    restricted = evidence_root / "restricted"
    public = evidence_root / "public"
    revisions = public / "revisions"
    assert set(path.name for path in evidence_root.iterdir()) == {
        "public",
        "restricted",
    }
    assert set(path.name for path in public.iterdir()) == {"revisions"}
    assert _directory_identity(evidence_root)[2:] == (
        os.getuid(),
        seiche_gid,
        0o750,
    )
    assert _directory_identity(restricted)[2:] == (
        os.getuid(),
        os.getgid(),
        0o700,
    )
    assert _directory_identity(public)[2:] == (
        os.getuid(),
        seiche_gid,
        0o750,
    )
    assert _directory_identity(revisions)[2:] == (
        os.getuid(),
        seiche_gid,
        0o2750,
    )
    before = {
        path: _directory_identity(path)
        for path in (evidence_root, restricted, public, revisions)
    }

    retry = _run_nbs_evidence_tree_test(environment, cwd=hostile)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert not sentinel.exists()
    assert {
        path: _directory_identity(path)
        for path in (evidence_root, restricted, public, revisions)
    } == before
    assert not tuple(evidence_root.rglob(".seiche-nbs-stage-*"))


@pytest.mark.parametrize(
    "mutation, expected_fragment",
    (
        ("root-gid", "NBS evidence root metadata is unsafe"),
        ("root-mode", "NBS evidence root metadata is unsafe"),
        ("restricted-file", "NBS restricted root is unsafe"),
        ("restricted-symlink", "NBS restricted root is unsafe"),
        ("restricted-mode", "NBS restricted root metadata is unsafe"),
        ("public-mode", "NBS public root metadata is unsafe"),
        ("revisions-mode", "NBS public revisions root metadata is unsafe"),
        ("root-orphan", "contains interrupted stage"),
        ("public-orphan", "contains interrupted stage"),
    ),
)
def test_nbs_evidence_tree_rejects_unsafe_existing_content_without_mutating_it(
    tmp_path: Path,
    mutation: str,
    expected_fragment: str,
):
    evidence_root, seiche_gid, environment = _nbs_evidence_tree_test_fixture(tmp_path)
    watched: Path
    if mutation == "root-gid":
        os.chown(evidence_root, -1, os.getgid())
        watched = evidence_root
    elif mutation == "root-mode":
        evidence_root.chmod(0o700)
        watched = evidence_root
    elif mutation == "restricted-file":
        watched = evidence_root / "restricted"
        watched.write_text("unsafe\n", encoding="ascii")
    elif mutation == "restricted-symlink":
        target = tmp_path / "restricted-link-target"
        target.mkdir(mode=0o700)
        watched = evidence_root / "restricted"
        watched.symlink_to(target, target_is_directory=True)
    elif mutation == "restricted-mode":
        watched = evidence_root / "restricted"
        watched.mkdir(mode=0o755)
        watched.chmod(0o755)
    elif mutation == "public-mode":
        watched = evidence_root / "public"
        watched.mkdir(mode=0o700)
        os.chown(watched, -1, seiche_gid)
        watched.chmod(0o700)
    elif mutation == "revisions-mode":
        public = evidence_root / "public"
        public.mkdir(mode=0o750)
        os.chown(public, -1, seiche_gid)
        public.chmod(0o750)
        watched = public / "revisions"
        watched.mkdir(mode=0o750)
        os.chown(watched, -1, seiche_gid)
        watched.chmod(0o750)
    elif mutation == "root-orphan":
        watched = evidence_root / ".seiche-nbs-stage-public-interrupted"
        watched.mkdir(mode=0o700)
    else:
        public = evidence_root / "public"
        public.mkdir(mode=0o750)
        os.chown(public, -1, seiche_gid)
        public.chmod(0o750)
        watched = public / ".seiche-nbs-stage-revisions-interrupted"
        watched.mkdir(mode=0o700)

    if watched.is_symlink() or watched.is_file():
        before = watched.lstat()
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_uid,
            before.st_gid,
            stat.S_IMODE(before.st_mode),
        )
    else:
        before_identity = _directory_identity(watched)
    before_members = tuple(sorted(path.name for path in evidence_root.iterdir()))

    result = _run_nbs_evidence_tree_test(environment)

    assert result.returncode != 0
    assert expected_fragment in result.stderr
    assert (
        tuple(sorted(path.name for path in evidence_root.iterdir())) == before_members
    )
    assert not (evidence_root / "restricted").exists() or mutation.startswith(
        "restricted"
    )
    if watched.is_symlink() or watched.is_file():
        after = watched.lstat()
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_uid,
            after.st_gid,
            stat.S_IMODE(after.st_mode),
        )
    else:
        after_identity = _directory_identity(watched)
    assert after_identity == before_identity


def test_nbs_evidence_tree_test_mode_rejects_ambient_or_wrong_group_authority(
    tmp_path: Path,
):
    evidence_root, _seiche_gid, environment = _nbs_evidence_tree_test_fixture(tmp_path)
    for mutation in (
        {"SSH_ORIGINAL_COMMAND": "forced-command"},
        {"SEICHE_NBS_EVIDENCE_TEST_GID": str(os.getgid())},
        {"SEICHE_ALLOW_NON_ROOT_INSTALL_TEST": "0"},
    ):
        result = _run_nbs_evidence_tree_test(environment | mutation)
        assert result.returncode != 0
        assert not tuple(evidence_root.iterdir())


def test_nbs_runtime_publisher_is_fresh_idempotent_and_isolated(
    secure_privileged_tmp_path: Path,
):
    tmp_path = secure_privileged_tmp_path
    asset_root, runtime_root, environment = _nbs_runtime_test_fixture(tmp_path)
    hostile = tmp_path / "hostile-pythonpath"
    hostile.mkdir()
    sentinel = tmp_path / "runtime-pythonpath-imported"
    (hostile / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    environment |= {"PYTHONPATH": str(hostile), "PYTHONHOME": str(hostile)}

    first = _run_nbs_runtime_test(environment, cwd=hostile)
    assert first.returncode == 0, first.stdout + first.stderr
    assert not sentinel.exists()
    target = environment["SEICHE_RELEASE_TARGET_SHA"]
    release = runtime_root / "releases" / target
    package = release / "seiche"
    assert (runtime_root / "current-sha").read_text(encoding="ascii") == f"{target}\n"
    assert set(path.name for path in runtime_root.iterdir()) == {
        "current-sha",
        "releases",
    }
    assert set(path.name for path in release.iterdir()) == {"seiche"}
    assert set(path.name for path in package.iterdir()) == {
        "__init__.py",
        "nbs_intake.py",
        "nbs_trust.py",
    }
    assert runtime_root.stat().st_mode & 0o777 == 0o755
    assert (runtime_root / "releases").stat().st_mode & 0o777 == 0o555
    assert release.stat().st_mode & 0o777 == 0o555
    assert package.stat().st_mode & 0o777 == 0o555
    for module in package.iterdir():
        assert module.stat().st_mode & 0o777 == 0o444
        assert module.stat().st_nlink == 1
        assert (
            module.read_bytes()
            == (asset_root / "backend" / "seiche" / module.name).read_bytes()
        )

    retry = _run_nbs_runtime_test(environment, cwd=hostile)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert (runtime_root / "current-sha").read_text(encoding="ascii") == f"{target}\n"


@pytest.mark.parametrize(
    "mutation",
    (
        "anchor-mode",
        "anchor-symlink",
        "extra-anchor-member",
        "interrupted-stage",
        "release-mode",
        "release-extra-member",
        "release-byte-mismatch",
        "release-symlink",
        "pointer-hardlink",
    ),
)
def test_nbs_runtime_publisher_rejects_unsafe_existing_state(
    secure_privileged_tmp_path: Path, mutation: str
):
    tmp_path = secure_privileged_tmp_path
    _asset_root, runtime_root, environment = _nbs_runtime_test_fixture(tmp_path)
    initial = _run_nbs_runtime_test(environment)
    assert initial.returncode == 0, initial.stdout + initial.stderr
    target = environment["SEICHE_RELEASE_TARGET_SHA"]
    pointer = runtime_root / "current-sha"
    release = runtime_root / "releases" / target
    package = release / "seiche"

    if mutation == "anchor-mode":
        runtime_root.chmod(0o775)
    elif mutation == "anchor-symlink":
        real_runtime = tmp_path / "real-nbs-runtime"
        runtime_root.rename(real_runtime)
        runtime_root.symlink_to(real_runtime, target_is_directory=True)
    elif mutation == "extra-anchor-member":
        (runtime_root / "unexpected").write_text("unsafe\n", encoding="utf-8")
    elif mutation == "interrupted-stage":
        (runtime_root / ".current-sha-interrupted").write_text(
            "unsafe\n", encoding="utf-8"
        )
    elif mutation == "release-mode":
        release.chmod(0o755)
    elif mutation == "release-extra-member":
        release.chmod(0o755)
        (release / "unexpected").write_text("unsafe\n", encoding="utf-8")
        release.chmod(0o555)
    elif mutation == "release-byte-mismatch":
        package.chmod(0o755)
        module = package / "nbs_intake.py"
        module.chmod(0o644)
        module.write_text("raise RuntimeError('changed')\n", encoding="utf-8")
        module.chmod(0o444)
        package.chmod(0o555)
    elif mutation == "release-symlink":
        package.chmod(0o755)
        module = package / "nbs_intake.py"
        module.unlink()
        module.symlink_to(package / "nbs_trust.py")
        package.chmod(0o555)
    else:
        os.link(pointer, tmp_path / "current-sha-second-link")

    retry = _run_nbs_runtime_test(environment)
    assert retry.returncode != 0
    assert pointer.read_text(encoding="ascii") == f"{target}\n"


def test_nbs_runtime_import_failure_leaves_old_pointer_unchanged(
    secure_privileged_tmp_path: Path,
):
    tmp_path = secure_privileged_tmp_path
    asset_root, runtime_root, environment = _nbs_runtime_test_fixture(tmp_path)
    initial = _run_nbs_runtime_test(environment)
    assert initial.returncode == 0, initial.stdout + initial.stderr
    old_target = environment["SEICHE_RELEASE_TARGET_SHA"]
    new_target = "b" * 40
    source = asset_root / "backend" / "seiche" / "nbs_intake.py"
    source.write_text(
        "raise RuntimeError('candidate import failed')\n", encoding="utf-8"
    )

    candidate = _run_nbs_runtime_test(
        environment | {"SEICHE_RELEASE_TARGET_SHA": new_target}
    )

    assert candidate.returncode != 0
    assert "candidate package import failed" in candidate.stderr
    assert (runtime_root / "current-sha").read_text(encoding="ascii") == (
        f"{old_target}\n"
    )
    assert (runtime_root / "releases" / new_target).is_dir()


def _run_offsite_canary_validator(
    tmp_path: Path, status: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    installer = MARKET_INSTALLER.read_text(encoding="utf-8")
    function_start = installer.index("offsite_canary_receipt_is_valid() {")
    code_start = installer.index("<<'PY'\n", function_start) + len("<<'PY'\n")
    code_end = installer.index("\nPY", code_start)
    validator = installer[code_start:code_end]
    env_path = tmp_path / "offsite.env"
    env_path.write_text(
        "SEICHE_OFFSITE_BACKUP_BUCKET=seiche-backups\n"
        "SEICHE_OFFSITE_BACKUP_PREFIX=production\n"
        "SEICHE_OFFSITE_BACKUP_KEY_ID=key-v1\n"
        "SEICHE_OFFSITE_BACKUP_DESTINATION_ID=primary\n",
        encoding="utf-8",
    )
    status_path = tmp_path / "offsite-status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-I", "-B", "-", str(env_path), str(status_path)],
        input=validator,
        text=True,
        capture_output=True,
        check=False,
    )


def _valid_offsite_v3_canary() -> dict[str, object]:
    source_contract = {
        "source_backup_schema": "seiche.market-backup.v3",
        "nbs_state_root": "/var/lib/seiche-nbs",
        "nbs_full_store_audit_contract": "seiche.nbs-full-store-audit.v1",
        "nbs_full_store_audit_result": "required_at_restore",
    }
    destination = {
        "bucket": "seiche-backups",
        "prefix": "production",
        "key_id": "key-v1",
        "destination": {"id": "primary"},
        "object_lock": {"days": 90, "mode": "COMPLIANCE"},
    }
    return {
        "schema": "seiche.market-offsite-backup-status.v2",
        "status": "success",
        **source_contract,
        **destination,
        "last_success": {
            **source_contract,
            **destination,
            "restore_verified": True,
            "remote_receipt_key": "production/canary/v1/RECEIPT.json",
            "ciphertext_version_id": "ciphertext-version",
            "remote_receipt_version_id": "receipt-version",
        },
    }


def test_offsite_timer_accepts_only_a_fully_bound_v3_canary(tmp_path: Path):
    result = _run_offsite_canary_validator(tmp_path, _valid_offsite_v3_canary())
    assert result.returncode == 0, result.stdout + result.stderr


def test_offsite_timer_rejects_fully_populated_legacy_v1_status(tmp_path: Path):
    status = _valid_offsite_v3_canary()
    status["schema"] = "seiche.market-offsite-backup-status.v1"

    result = _run_offsite_canary_validator(tmp_path, status)

    assert result.returncode != 0


def test_offsite_v1_to_v2_runbook_breaks_the_canary_namespace_safely():
    runbook = (ROOT / "ops" / "deploy" / "MARKET-BACKUPS.md").read_text()
    section = runbook[
        runbook.index("### Production v1-to-v2 namespace cutover") : runbook.index(
            "### Controlled first write and recurring schedule"
        )
    ]

    timer_stop = section.index(
        "systemctl disable --now seiche-market-offsite-backup.timer"
    )
    transition = section.index("seiche/market-backups/v1 seiche/market-backups/v2 0 1")
    assert timer_stop < transition
    assert 'values["SEICHE_OFFSITE_BACKUP_KEY_ID"] != "market-key-2026-08-v1"' in (
        section
    )
    assert (
        'values["SEICHE_OFFSITE_BACKUP_DESTINATION_ID"]\n'
        '        != "hetzner-primary-v1"' in section
    )
    assert "os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC" in section
    assert "os.fchmod(stage_fd, 0o600)" in section
    assert "os.fsync(stage_fd)" in section
    assert "os.rename(" in section
    assert "os.fsync(parent_fd)" in section
    assert "Do not manually run the old status-v1 service" in section

    recurring = runbook[runbook.index("### Controlled first write") :]
    assert "seiche/market-backups/v2 seiche/market-backups/v2 1 0" in recurring
    assert '"$ASSET_ROOT/ops/deploy/install-market-platform.sh"' in recurring


@pytest.mark.parametrize(
    "scope, field",
    tuple(
        (scope, field)
        for scope in ("top", "success")
        for field in (
            "source_backup_schema",
            "nbs_state_root",
            "nbs_full_store_audit_contract",
            "nbs_full_store_audit_result",
        )
    ),
)
def test_offsite_timer_rejects_legacy_or_partially_bound_canaries(
    tmp_path: Path, scope: str, field: str
):
    status = _valid_offsite_v3_canary()
    if scope == "top":
        status.pop(field)
    else:
        success = status["last_success"]
        assert isinstance(success, dict)
        success.pop(field)

    result = _run_offsite_canary_validator(tmp_path, status)

    assert result.returncode != 0


def _release_signature_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen is None:
        pytest.skip("OpenSSH is required for the release-signature contract")

    repository = tmp_path / "signed-repository"
    _git("init", "-b", "main", str(repository), cwd=tmp_path)
    _git("config", "user.name", "Seiche Release", cwd=repository)
    _git("config", "user.email", "release@example.invalid", cwd=repository)
    signing_key = tmp_path / "release-signing-key"
    subprocess.run(
        [ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(signing_key)],
        check=True,
    )
    _git("config", "gpg.format", "ssh", cwd=repository)
    _git("config", "user.signingkey", str(signing_key), cwd=repository)
    _git("config", "commit.gpgsign", "true", cwd=repository)

    public_key = signing_key.with_suffix(".pub").read_text(encoding="ascii").split()
    allowed_signers = tmp_path / "allowed-signers"
    allowed_signers.write_text(
        f"release@example.invalid {public_key[0]} {public_key[1]}\n",
        encoding="ascii",
    )
    allowed_signers.chmod(0o444)
    runuser = _executable(
        tmp_path / "runuser",
        'if [ "$1" = -u ]; then shift 2; fi\n'
        'if [ "${1:-}" = -- ]; then shift; fi\n'
        'exec "$@"\n',
    )
    env = os.environ | {
        "SEICHE_CONTROL_LIBRARY_ONLY": "1",
        "SEICHE_CONTROL_APP_DIR": str(repository),
        "SEICHE_CONTROL_USER": "release-test",
        "SEICHE_CONTROL_RUNUSER": str(runuser),
        "SEICHE_CONTROL_PYTHON": sys.executable,
        "SEICHE_CONTROL_ALLOWED_SIGNERS": str(allowed_signers),
        "SEICHE_CONTROL_SIGNING_PRINCIPAL": "release@example.invalid",
        "SEICHE_CONTROL_SIGNER_UID": str(os.getuid()),
        "SEICHE_CONTROL_SIGNER_GID": str(os.getgid()),
        "SEICHE_CONTROL_SIGNER_MODE": "444",
        "SEICHE_CONTROL_SSH_KEYGEN": ssh_keygen,
    }
    return repository, env


def _commit_release(repository: Path, message: str, *, signed: bool = True) -> str:
    (repository / "release-marker.txt").write_text(f"{message}\n", encoding="utf-8")
    _git("add", "release-marker.txt", cwd=repository)
    command = ["git"]
    if not signed:
        command.extend(["-c", "commit.gpgsign=false"])
    command.extend(["commit", "-m", message])
    subprocess.run(command, cwd=repository, check=True, capture_output=True, text=True)
    return _git("rev-parse", "HEAD", cwd=repository)


def _verify_release_signature(
    environment: dict[str, str], target: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$SEICHE_POLLER"; verify_target_signature "$SEICHE_TARGET"',
        ],
        env=environment
        | {"SEICHE_POLLER": str(RELEASE_POLLER), "SEICHE_TARGET": target},
        text=True,
        capture_output=True,
        check=False,
    )


def _commit_automation_content(
    repository: Path,
    files: dict[str, str],
    *,
    message: str = "dispatch: generated edition",
    author: str = "desk@seiche.info",
) -> str:
    _git("config", "user.email", author, cwd=repository)
    for relative, body in files.items():
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body, encoding="utf-8")
    _git("add", "--all", cwd=repository)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", message],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return _git("rev-parse", "HEAD", cwd=repository)


def _classify_automation_content(
    environment: dict[str, str], target: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$SEICHE_POLLER"; is_inert_automation_content_commit "$SEICHE_TARGET"',
        ],
        env=environment
        | {"SEICHE_POLLER": str(RELEASE_POLLER), "SEICHE_TARGET": target},
        text=True,
        capture_output=True,
        check=False,
    )


def _legacy_retirement_fixture(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    systemd_dir = tmp_path / "systemd"
    state_dir = tmp_path / "deploy-state"
    fake_state = tmp_path / "fake-systemctl"
    systemd_dir.mkdir()
    state_dir.mkdir()
    fake_state.mkdir()
    (systemd_dir / "timers.target.wants").mkdir()
    (systemd_dir / "multi-user.target.wants").mkdir()

    service = systemd_dir / "seiche-update.service"
    timer = systemd_dir / "seiche-update.timer"
    service.write_text("[Service]\nExecStart=/home/seiche/update.sh\n")
    timer.write_text("[Timer]\nOnCalendar=*-*-* 05:30:00 UTC\n")
    service.chmod(0o644)
    timer.chmod(0o644)
    (fake_state / "seiche-update.timer.active").touch()
    (fake_state / "seiche-update.timer.enabled").touch()
    (fake_state / "seiche-update.service.enabled").touch()
    (systemd_dir / "timers.target.wants" / timer.name).symlink_to(timer)
    (systemd_dir / "multi-user.target.wants" / service.name).symlink_to(service)

    fake_systemctl = _executable(
        tmp_path / "systemctl",
        """
state=${FAKE_SYSTEMCTL_STATE:?}
units=${SEICHE_SYSTEMD_DIR:?}
printf '%s\n' "$*" >>"$state/calls.log"
command=${1:?}
shift
case "$command" in
  is-active)
    unit=${1:?}
    if [ -f "$state/$unit.state" ]; then
      unit_state=$(cat "$state/$unit.state")
      echo "$unit_state"
      case "$unit_state" in
        active|activating|reloading|deactivating|maintenance|refreshing) exit 0 ;;
        *) exit 3 ;;
      esac
    fi
    if [ -f "$state/$unit.active" ]; then
      echo active
      exit 0
    fi
    echo inactive
    exit 3
    ;;
  is-enabled)
    unit=${1:?}
    if [ -L "$units/$unit" ] && [ "$(readlink "$units/$unit")" = /dev/null ]; then
      echo masked
      exit 1
    fi
    if [ -f "$state/$unit.enabled" ]; then
      echo enabled
      exit 0
    fi
    echo disabled
    exit 1
    ;;
  disable)
    for argument in "$@"; do
      case "$argument" in
        --*) ;;
        *)
          if [ -L "$units/$argument" ] \
              && [ "$(readlink "$units/$argument")" = /dev/null ]; then
            exit 1
          fi
          ;;
      esac
    done
    for argument in "$@"; do
      case "$argument" in
        --*) ;;
        *)
          rm -f -- "$state/$argument.active" "$state/$argument.enabled"
          rm -f -- "$state/$argument.state"
          rm -f -- "$units/timers.target.wants/$argument"
          rm -f -- "$units/multi-user.target.wants/$argument"
          ;;
      esac
    done
    ;;
  stop)
    rm -f -- "$state/${1:?}.active" "$state/${1:?}.state"
    ;;
  mask)
    for unit in "$@"; do
      case "$unit" in --*) continue ;; esac
      rm -f -- "$units/$unit"
      ln -s /dev/null "$units/$unit"
      rm -f -- "$state/$unit.active" "$state/$unit.enabled" "$state/$unit.state"
    done
    ;;
  daemon-reload) ;;
  *) exit 64 ;;
esac
""",
    )
    fake_stat = tmp_path / "stat"
    fake_stat.write_text(
        """#!/usr/bin/env python3
import os
import stat
import sys

if len(sys.argv) != 4 or sys.argv[1] != "-c":
    raise SystemExit(64)
fmt, path = sys.argv[2:]
value = os.stat(path, follow_symlinks=False)
rendered = (
    fmt.replace("%u", str(value.st_uid))
    .replace("%g", str(value.st_gid))
    .replace("%a", format(stat.S_IMODE(value.st_mode), "o"))
    .replace("%Y", str(int(value.st_mtime)))
)
print(rendered)
"""
    )
    fake_stat.chmod(0o755)
    env = os.environ | {
        "FAKE_SYSTEMCTL_STATE": str(fake_state),
        "SEICHE_ALLOW_NON_ROOT_RETIRE_TEST": "1",
        "SEICHE_SYSTEMD_DIR": str(systemd_dir),
        "SEICHE_DEPLOY_STATE_DIR": str(state_dir),
        "SEICHE_SYSTEMCTL_BIN": str(fake_systemctl),
        "SEICHE_SYNC_BIN": shutil.which("true") or "/usr/bin/true",
        "SEICHE_CP_BIN": shutil.which("cp") or "/bin/cp",
        "SEICHE_STAT_BIN": str(fake_stat),
        "SEICHE_SHA256SUM_BIN": shutil.which("sha256sum") or "/usr/bin/sha256sum",
    }
    return env, systemd_dir, state_dir


def _run_legacy_retirement(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(LEGACY_UPDATE_RETIRER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _caddy_env(
    tmp_path: Path, *, reject_new_reload: bool = False
) -> tuple[dict, Path, Path]:
    source = tmp_path / "repo.Caddyfile"
    installed = tmp_path / "installed.Caddyfile"
    calls = tmp_path / "calls.log"
    source.write_text("NEW\n")
    installed.write_text("OLD\n")

    caddy = _executable(
        tmp_path / "caddy",
        f'''config=""
want_config=0
for arg in "$@"; do
    if [ "$want_config" = 1 ]; then config="$arg"; want_config=0; continue; fi
    if [ "$arg" = --config ]; then want_config=1; fi
done
content=MISSING
[ -z "$config" ] || [ ! -f "$config" ] || content=$(tr -d '\\n' < "$config")
echo "caddy $1 config=$config content=$content" >> "{calls}"
if [ "$1" = validate ]; then exit 0; fi
if [ "${{REJECT_NEW_RELOAD:-0}}" = 1 ] && [ "$content" = NEW ]; then exit 1; fi
exit 0
''',
    )
    systemctl = _executable(
        tmp_path / "systemctl",
        f'''echo "systemctl $* $(tr -d '\\n' < "${{SEICHE_CADDY_DEST}}")" >> "{calls}"
if [ "${{REJECT_NEW_RELOAD:-0}}" = 1 ] && grep -q NEW "${{SEICHE_CADDY_DEST}}"; then exit 1; fi
exit 0
''',
    )
    _executable(
        tmp_path / "mv",
        f'''printf 'mv' >> "{calls}"
for arg in "$@"; do printf ' <%s>' "$arg" >> "{calls}"; done
printf '\\n' >> "{calls}"
exec /bin/mv "$@"
''',
    )
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        "SEICHE_CADDY_SOURCE": str(source),
        "SEICHE_CADDY_DEST": str(installed),
        "SEICHE_CADDY_BIN": str(caddy),
        "SEICHE_SYSTEMCTL_BIN": str(systemctl),
        "SEICHE_CADDY_ENV_FILE": str(tmp_path / "railway-edge.env"),
        "REJECT_NEW_RELOAD": "1" if reject_new_reload else "0",
    }
    return env, installed, calls


def test_caddy_installer_validates_backs_up_installs_and_reloads(tmp_path):
    env, installed, calls = _caddy_env(tmp_path)
    result = subprocess.run(
        ["bash", str(CADDY_INSTALLER)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert installed.read_text() == "NEW\n"
    assert list(tmp_path.glob("installed.Caddyfile.bak-*"))[0].read_text() == "OLD\n"
    log = calls.read_text()
    validation = next(
        line for line in log.splitlines() if line.startswith("caddy validate")
    )
    assert "content=NEW" in validation
    assert str(tmp_path / ".installed.Caddyfile.new.") in validation
    assert str(tmp_path / "repo.Caddyfile") not in validation
    assert f"mv <-f> <{tmp_path}/.installed.Caddyfile.new." in log
    assert f"<{installed}>" in log
    assert f"caddy reload config={installed} content=NEW" in log
    assert not list(tmp_path.glob(".installed.Caddyfile.*"))


def test_caddy_railway_origin_is_secret_injected_and_route_bounded():
    caddy = CADDYFILE.read_text()
    snippet = caddy[
        caddy.index("(seiche_stateful_upstream)") : caddy.index("api.seiche.info {")
    ]
    assert "{$SEICHE_API_UPSTREAM:127.0.0.1:8787}" in snippet
    assert "{$SEICHE_RAILWAY_EDGE_TOKEN:local-edge-token-unused}" in snippet
    assert "header_up Host {upstream_hostport}" in snippet
    assert caddy.count("import seiche_stateful_upstream") == 4
    private_delivery = caddy[
        caddy.index("@world_model_delivery {") : caddy.index(
            "@world_model_delivery_non_get"
        )
    ]
    assert "reverse_proxy 127.0.0.1:8787" in private_delivery
    assert "seiche_stateful_upstream" not in private_delivery


def test_caddy_installer_loads_edge_file_as_data_without_sourcing(tmp_path):
    env, installed, calls = _caddy_env(tmp_path)
    edge = Path(env["SEICHE_CADDY_ENV_FILE"])
    token = "x" * 40
    edge.write_text(
        "SEICHE_API_UPSTREAM=https://fixture.up.railway.app\n"
        f"SEICHE_RAILWAY_EDGE_TOKEN={token}\n",
        encoding="utf-8",
    )
    edge.chmod(0o600)
    result = subprocess.run(
        ["bash", str(CADDY_INSTALLER)],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert installed.read_text() == "NEW\n"
    assert "fixture.up.railway.app" not in calls.read_text()

    edge.write_text("SEICHE_API_UPSTREAM=$(touch /tmp/never-run)\n", encoding="utf-8")
    rejected = subprocess.run(
        ["bash", str(CADDY_INSTALLER)],
        env=env,
        text=True,
        capture_output=True,
    )
    assert rejected.returncode != 0
    assert "Railway edge environment is invalid" in rejected.stderr


def test_caddy_reload_failure_restores_previous_config_and_stays_red(tmp_path):
    env, installed, calls = _caddy_env(tmp_path, reject_new_reload=True)
    result = subprocess.run(
        ["bash", str(CADDY_INSTALLER)], env=env, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert installed.read_text() == "OLD\n"
    log = calls.read_text()
    assert f"caddy reload config={installed} content=NEW" in log
    assert "systemctl reload caddy NEW" in log
    assert f"caddy reload config={installed} content=OLD" in log
    assert f"mv <-f> <{tmp_path}/.installed.Caddyfile.restore." in log
    assert not list(tmp_path.glob(".installed.Caddyfile.*"))
    assert "previous Caddyfile restored and reloaded" in result.stdout


def test_equal_caddyfile_is_validated_and_reloaded_to_heal_runtime(tmp_path):
    env, installed, calls = _caddy_env(tmp_path)
    Path(env["SEICHE_CADDY_SOURCE"]).write_text(installed.read_text())
    result = subprocess.run(
        ["bash", str(CADDY_INSTALLER)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    log = calls.read_text()
    assert f"caddy validate config={installed} content=OLD" in log
    assert f"caddy reload config={installed} content=OLD" in log
    assert "mv " not in log
    assert not list(tmp_path.glob("installed.Caddyfile.bak-*"))


def test_caddy_access_log_redacts_credential_query_values():
    caddy = CADDYFILE.read_text()
    access_log = caddy[caddy.index("(accesslog) {") : caddy.index("api.seiche.info {")]

    assert "format filter {" in access_log
    assert "wrap json" in access_log
    assert "request>uri query {" in access_log
    for name in ("api_key", "api-key", "access_token", "token"):
        assert f"replace {name} [REDACTED]" in access_log
    assert "format json" not in access_log


def test_openai_domain_challenge_is_runtime_gated_and_fail_closed():
    caddy = CADDYFILE.read_text(encoding="utf-8")
    marker = "# OpenAI plugin domain verification is deliberately dark"
    block = caddy[
        caddy.index(marker) : caddy.index("    @public {", caddy.index(marker))
    ]
    challenge_path = "/.well-known/openai-apps-challenge"
    token_placeholder = "{env.OPENAI_APPS_CHALLENGE_TOKEN}"
    token_pattern = r"^[A-Za-z0-9][A-Za-z0-9._~=-]{15,511}$"

    enabled = block.index("@openai_apps_challenge_enabled {")
    enabled_handler = block.index("handle @openai_apps_challenge_enabled {")
    fallback = block.index("@openai_apps_challenge_unavailable path")
    fallback_handler = block.index("handle @openai_apps_challenge_unavailable {")

    assert enabled < enabled_handler < fallback < fallback_handler
    assert block.count(f"path {challenge_path}") == 2
    assert "method GET HEAD" in block
    assert f"vars_regexp openai_apps_token {token_placeholder} {token_pattern}" in block
    assert f'respond "{token_placeholder}" 200' in block
    assert 'header Cache-Control "no-store, no-transform"' in block
    assert 'header Content-Type "text/plain; charset=utf-8"' in block
    assert 'respond "not here" 404' in block[fallback_handler:]

    # Runtime placeholders cannot change Caddyfile syntax. Parse-time
    # substitution, file serving, and proxying would all weaken that boundary.
    assert "{$OPENAI_APPS_CHALLENGE_TOKEN" not in block
    assert "file_server" not in block
    assert "reverse_proxy" not in block
    assert "handle_path" not in block

    runbook = (ROOT / "integrations" / "openai" / "SUBMISSION.md").read_text(
        encoding="utf-8"
    )
    assert token_pattern in runbook
    assert "systemctl restart caddy" in runbook
    assert "cmp -s" in runbook
    assert 'test "$status" = 404' in runbook
    assert "Never reuse an old value" in runbook


def test_caddy_exposes_only_the_sanitized_editorial_memory_projection():
    caddy = CADDYFILE.read_text(encoding="utf-8")
    marker = "@editorial_memory path /editorial/memory.json"
    start = caddy.index(marker)
    end = caddy.index("\n    }", start)
    block = caddy[start:end]

    assert "root * /var/lib/myquant-editorial-public" in block
    assert "uri strip_prefix /editorial" in block
    assert "file_server" in block
    assert 'Cache-Control "public, max-age=300, no-transform"' in block
    assert "handle_path /editorial/*" not in caddy
    assert "/mnt/HC_Volume_106588294/myquant-intelligence" not in block


def test_api_dropin_disables_unredacted_uvicorn_access_log():
    installer = MARKET_INSTALLER.read_text()
    api_dropin = installer[
        installer.index('cat >"$DROPIN"') : installer.index(
            'mv -f "$DROPIN"', installer.index('cat >"$DROPIN"')
        )
    ]

    assert "Environment=UVICORN_ACCESS_LOG=false" in api_dropin
    assert "ExecStart=" not in api_dropin


def _smoke_env(tmp_path: Path, scenario: str = "success") -> tuple[dict, Path]:
    calls = tmp_path / "curl.log"
    _executable(
        tmp_path / "curl",
        f'''out=""
url=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output) out="$2"; shift 2 ;;
        http://*|https://*) url="$1"; shift ;;
        *) shift ;;
    esac
done
echo "$url $*" >> "{calls}"
status=200
case "$url" in
    */api/health)
        type=application/json; body='{{"generated_at":"2026-08-10T00:00:00Z"}}'
        ;;
    */api/public) type=application/json; body='{{"conclusion":"CLEAR"}}' ;;
    */api/money-markets)
        type=application/json
        body='{{"ok":true,"schema":"seiche.money-market-desk.v1","sections":[{{"id":"policy_corridor"}},{{"id":"secured_distributions"}},{{"id":"repo_segments"}},{{"id":"unsecured_funding"}},{{"id":"bills_cash_curve"}},{{"id":"liquidity_buffers"}},{{"id":"mmf_plumbing"}}]}}'
        ;;
    */api/oil-funding)
        type=application/json; body='{{"schema":"seiche.oil-funding.v1"}}' ;;
    */api/estuary)
        type=application/json; body='{{"schema":"seiche.estuary.v1"}}' ;;
    */api/v2/markets)
        type=application/json; body='{{"schema":"seiche.markets.v2"}}' ;;
    */api/v2/money-markets)
        type=application/json
        body='{{"ok":true,"schema":"seiche.global-money-markets.v1","coverage":{{"declared_markets":11,"expansion_markets":52,"global_discovery_universe":63}},"expansion_ledger":[],"read_faults":[]}}'
        ;;
    */api/v2/world-markets[?]section=china_macro)
        type=application/json
        body='{{"ok":true,"schema":"seiche.world-markets.v1","status":"structural","selection":"china_macro","as_of":null,"context_only":true,"generated_at":null,"china_macro":{{"values_published":false,"raw_evidence_included":false,"history_included":false,"scoring_eligible":false,"cn_cny_gauge_eligible":false}},"citation":{{"topic_url":"https://seiche.info/markets/china-macro/"}}}}'
        ;;
    */api/v2/world-markets)
        type=application/json
        body='{{"ok":true,"schema":"seiche.world-markets.v1","scope":{{"coverage_claim":"curated_partial_non_exhaustive"}}}}'
        ;;
    */api/v2/coverage)
        type=application/json; body='{{"schema":"seiche.coverage.v2"}}' ;;
    */api/v2/global/tide)
        type=application/json; body='{{"schema":"seiche.global-tide.v2"}}' ;;
    */api/subscribe) type=application/json; body='{{"gates_nothing":true}}' ;;
    */.well-known/mcp.json)
        type=application/json
        body='{{"canonicalCatalog":"https://seiche.info/.well-known/ai-catalog.json","servers":[{{"name":"io.github.beepboop2025/seiche","url":"https://api.seiche.info/mcp"}}]}}'
        ;;
    */mcp) type='text/event-stream; charset=utf-8'; body=': stateless transport' ;;
    */riptide/) type=application/json; body='{{"name": "riptide"}}' ;;
    */riptide/openapi.json)
        type=application/json; body='{{"title": "Riptide Public API"}}'
        ;;
    */palimpsest/osint/osint-china.json)
        type=application/json
        body='{{"schema": "palimpsest-nemesis.public-snapshot"}}'
        ;;
    */palimpsest/baike-public-snapshot/baike-public-snapshot-latest.json)
        type=application/json; body='{{"method_version": 1}}'
        ;;
    */palimpsest/peer-context/peer-context-latest.json)
        type=application/json
        body='{{"schema_version": "palimpsest-peer-context.v1"}}'
        ;;
    */palimpsest/greatfire-context/greatfire-context-latest.json)
        type=application/json
        body='{{"schema_version": "palimpsest-greatfire-context/v1"}}'
        ;;
    */palimpsest/public-deletion-ledgers/public-deletion-ledgers-latest.json)
        type=application/json; body='{{"method_version": 1}}'
        ;;
    *) type=text/plain; body='generic' ;;
esac
if [ "${{SMOKE_SCENARIO:-success}}" = redirect ] && [[ "$url" = */api/subscribe ]]; then
    status=302; type=text/html; body='redirecting'
fi
if [ "${{SMOKE_SCENARIO:-success}}" = generic ] && [[ "$url" = */api/subscribe ]]; then
    status=200; type=application/json; body='{{"ok":true}}'
fi
if [ "${{SMOKE_SCENARIO:-success}}" = usd_partial ] && [[ "$url" = */api/money-markets ]]; then
    body='{{"ok":false,"schema":"seiche.money-market-desk.v1","sections":[]}}'
fi
if [ "${{SMOKE_SCENARIO:-success}}" = atlas_read_fault ] && [[ "$url" = */api/v2/money-markets ]]; then
    body='{{"ok":true,"schema":"seiche.global-money-markets.v1","coverage":{{"declared_markets":11,"expansion_markets":52,"global_discovery_universe":63}},"expansion_ledger":[],"read_faults":[{{"source":"canonical_repository"}}]}}'
fi
printf '%s' "$body" > "$out"
printf '%s|%s' "$status" "$type"
''',
    )
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        "SEICHE_EXTERNAL_BASE_URL": "https://edge.invalid",
        "SEICHE_EXTERNAL_ROUTES_FILE": str(EXTERNAL_ROUTES),
        "SMOKE_SCENARIO": scenario,
    }
    return env, calls


def test_external_smoke_checks_subscribe_identity_without_following_redirects(tmp_path):
    definitions = EXTERNAL_ROUTES.read_text()
    assert 'GET|/api/health|200|application/json|"generated_at"' in definitions
    assert (
        "GET|/api/money-markets|200|application/json|"
        '"schema":"seiche.money-market-desk.v1"'
    ) in definitions
    for identity in (
        '"ok":true',
        '"id":"policy_corridor"',
        '"id":"secured_distributions"',
        '"id":"repo_segments"',
        '"id":"unsecured_funding"',
        '"id":"bills_cash_curve"',
        '"id":"liquidity_buffers"',
        '"id":"mmf_plumbing"',
    ):
        assert f"GET|/api/money-markets|200|application/json|{identity}" in definitions
    assert (
        'GET|/api/oil-funding|200|application/json|"schema":"seiche.oil-funding.v1"'
    ) in definitions
    assert (
        'GET|/api/estuary|200|application/json|"schema":"seiche.estuary.v1"'
    ) in definitions
    assert (
        'GET|/api/v2/markets|200|application/json|"schema":"seiche.markets.v2"'
    ) in definitions
    assert (
        "GET|/api/v2/money-markets|200|application/json|"
        '"schema":"seiche.global-money-markets.v1"'
    ) in definitions
    for identity in (
        '"ok":true',
        '"declared_markets":11',
        '"expansion_markets":52',
        '"global_discovery_universe":63',
        '"expansion_ledger":[',
        '"read_faults":[]',
    ):
        assert (
            f"GET|/api/v2/money-markets|200|application/json|{identity}" in definitions
        )
    for identity in (
        '"schema":"seiche.world-markets.v1"',
        '"coverage_claim":"curated_partial_non_exhaustive"',
    ):
        assert (
            f"GET|/api/v2/world-markets|200|application/json|{identity}" in definitions
        )
    assert (
        'GET|/api/v2/coverage|200|application/json|"schema":"seiche.coverage.v2"'
    ) in definitions
    assert (
        'GET|/api/v2/global/tide|200|application/json|"schema":"seiche.global-tide.v2"'
    ) in definitions
    assert 'GET|/api/subscribe|200|application/json|"gates_nothing":true' in definitions
    for identity in (
        '"canonicalCatalog":"https://seiche.info/.well-known/ai-catalog.json"',
        '"name":"io.github.beepboop2025/seiche"',
        '"url":"https://api.seiche.info/mcp"',
    ):
        assert (
            f"GET|/.well-known/mcp.json|200|application/json|{identity}" in definitions
        )
    assert ('GET|/riptide/|200|application/json|"name": "riptide"') in definitions
    assert (
        'GET|/riptide/openapi.json|200|application/json|"title": "Riptide Public API"'
    ) in definitions
    assert (
        "GET|/palimpsest/osint/osint-china.json|200|application/json|"
        '"schema": "palimpsest-nemesis.public-snapshot"'
    ) in definitions
    palimpsest_host_routes = {
        "/palimpsest/baike-public-snapshot/baike-public-snapshot-latest.json": (
            '"method_version": 1'
        ),
        "/palimpsest/peer-context/peer-context-latest.json": (
            '"schema_version": "palimpsest-peer-context.v1"'
        ),
        "/palimpsest/greatfire-context/greatfire-context-latest.json": (
            '"schema_version": "palimpsest-greatfire-context/v1"'
        ),
        "/palimpsest/public-deletion-ledgers/"
        "public-deletion-ledgers-latest.json": '"method_version": 1',
    }
    for route, identity in palimpsest_host_routes.items():
        assert f"GET|{route}|200|application/json|{identity}" in definitions
    env, calls = _smoke_env(tmp_path)
    result = subprocess.run(
        ["bash", str(EXTERNAL_SMOKE)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "https://edge.invalid/api/subscribe" in calls.read_text()
    assert "https://edge.invalid/.well-known/mcp.json" in calls.read_text()
    for route in palimpsest_host_routes:
        assert f"https://edge.invalid{route}" in calls.read_text()
    assert "--location" not in EXTERNAL_SMOKE.read_text()


def test_public_deploy_docs_retire_the_incompatible_legacy_installer():
    readme = (ROOT / "README.md").read_text()
    assert "/opt/seiche" not in readme
    assert "host release poller" in readme
    assert "Auto-deploy on every merge to main" not in readme
    workflow = DEPLOY_WORKFLOW.read_text()
    assert "workflow_dispatch:" in workflow
    assert "\n  push:" not in workflow
    result = subprocess.run(
        ["bash", str(LEGACY_INSTALLER)], text=True, capture_output=True
    )
    assert result.returncode != 0
    assert "retired" in result.stderr
    assert "RELEASE-POLLER.md" in result.stderr


@pytest.mark.parametrize("scenario", ("usd_partial", "atlas_read_fault"))
def test_external_smoke_rejects_incomplete_money_market_contracts(tmp_path, scenario):
    env, _ = _smoke_env(tmp_path, scenario)

    result = subprocess.run(
        ["bash", str(EXTERNAL_SMOKE)], env=env, text=True, capture_output=True
    )

    assert result.returncode != 0
    assert "FAIL:" in result.stderr


def test_riptide_edge_strips_only_its_product_prefix_and_proxies_all_transports():
    caddy = CADDYFILE.read_text()
    assert "@riptide_root path /riptide" in caddy
    assert "handle_path /riptide/*" in caddy
    block = caddy[caddy.index("@riptide_root path") : caddy.index("# AnakE-Nyx")]
    assert block.count("reverse_proxy 127.0.0.1:8797") == 2
    assert "rewrite * /" in block


def test_external_smoke_rejects_redirect(tmp_path):
    env, _ = _smoke_env(tmp_path, "redirect")
    result = subprocess.run(
        ["bash", str(EXTERNAL_SMOKE)], env=env, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert "/api/subscribe returned 302" in result.stderr


def test_external_smoke_rejects_generic_json_200(tmp_path):
    env, _ = _smoke_env(tmp_path, "generic")
    result = subprocess.run(
        ["bash", str(EXTERNAL_SMOKE)], env=env, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert "not its route identity" in result.stderr


def test_forced_command_bootstrap_converges_in_one_workflow_run(tmp_path):
    calls = tmp_path / "ssh.log"
    ssh = _executable(
        tmp_path / "ssh",
        f'''for arg in "$@"; do printf '<%s>' "$arg" >> "{calls}"; done
printf '\\n' >> "{calls}"
''',
    )
    key = tmp_path / "key"
    known = tmp_path / "known_hosts"
    key.write_text("test-only")
    known.write_text("test-only")
    env = {
        **os.environ,
        "SEICHE_DEPLOY_HOST": "192.0.2.10",
        "SEICHE_DEPLOY_KEY_FILE": str(key),
        "SEICHE_KNOWN_HOSTS_FILE": str(known),
        "SEICHE_EXPECTED_TARGET_SHA": "a" * 40,
        "SEICHE_SSH_BIN": str(ssh),
    }
    result = subprocess.run(
        ["bash", str(FORCED_DEPLOY)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = calls.read_text().splitlines()
    assert len(lines) == 2
    assert all(line.endswith(f"<root@192.0.2.10><deploy {'a' * 40}>") for line in lines)
    workflow = DEPLOY_WORKFLOW.read_text()
    assert "target_sha:" in workflow
    assert 'SEICHE_EXPECTED_TARGET_SHA="$TARGET_SHA"' in workflow
    assert "SEICHE_DEPLOY_DEFER_WAIT_SECONDS=600" in workflow
    assert "SEICHE_DEPLOY_DEFER_RETRY_SECONDS=30" in workflow
    assert workflow.index("trigger-forced-deploy.sh") < workflow.index(
        "external-route-smoke.sh"
    )


def _forced_deploy_result(
    tmp_path: Path,
    ssh_body: str,
    *,
    wait_seconds: int,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    calls = tmp_path / "ssh-calls"
    ssh = _executable(
        tmp_path / "ssh",
        f'printf "call\\n" >>"{calls}"\n{ssh_body}',
    )
    key = tmp_path / "key"
    known = tmp_path / "known_hosts"
    key.write_text("test-only")
    known.write_text("test-only")
    env = os.environ | {
        "SEICHE_DEPLOY_HOST": "192.0.2.10",
        "SEICHE_DEPLOY_KEY_FILE": str(key),
        "SEICHE_KNOWN_HOSTS_FILE": str(known),
        "SEICHE_EXPECTED_TARGET_SHA": "a" * 40,
        "SEICHE_SSH_BIN": str(ssh),
        "SEICHE_DEPLOY_SLEEP_BIN": str(Path(shutil.which("true") or "/usr/bin/true")),
        "SEICHE_DEPLOY_DEFER_WAIT_SECONDS": str(wait_seconds),
        "SEICHE_DEPLOY_DEFER_RETRY_SECONDS": "1",
    }
    return (
        subprocess.run(
            ["bash", str(FORCED_DEPLOY)],
            env=env,
            text=True,
            capture_output=True,
        ),
        calls,
    )


def test_forced_command_retries_only_a_safe_defer(tmp_path):
    counter = tmp_path / "counter"
    counter.write_text("0\n")
    result, calls = _forced_deploy_result(
        tmp_path,
        (
            f'count=$(cat "{counter}")\n'
            "count=$((count + 1))\n"
            f'printf "%s\\n" "$count" >"{counter}"\n'
            '[ "$count" -gt 1 ] || exit 75\n'
        ),
        wait_seconds=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.read_text().splitlines() == ["call", "call", "call"]
    assert "safely deferred; retrying" in result.stdout


def test_forced_command_gives_each_pass_its_own_defer_window(tmp_path):
    counter = tmp_path / "counter"
    counter.write_text("0\n")
    result, calls = _forced_deploy_result(
        tmp_path,
        (
            f'count=$(cat "{counter}")\n'
            "count=$((count + 1))\n"
            f'printf "%s\\n" "$count" >"{counter}"\n'
            'if [ "$count" -eq 1 ]; then sleep 2; exit 0; fi\n'
            '[ "$count" -gt 2 ] || exit 75\n'
        ),
        wait_seconds=2,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.read_text().splitlines() == ["call", "call", "call"]
    assert "pass 2/2 safely deferred; retrying" in result.stdout


@pytest.mark.parametrize("status", [1, 42, 255])
def test_forced_command_preserves_real_failures(tmp_path, status):
    result, calls = _forced_deploy_result(
        tmp_path,
        f"exit {status}\n",
        wait_seconds=10,
    )

    assert result.returncode == status
    assert calls.read_text().splitlines() == ["call"]
    assert "retrying" not in result.stdout


def test_forced_command_returns_deferred_at_its_bound(tmp_path):
    result, calls = _forced_deploy_result(
        tmp_path,
        "exit 75\n",
        wait_seconds=0,
    )

    assert result.returncode == 75
    assert calls.read_text().splitlines() == ["call"]
    assert "remained safely deferred after 0s" in result.stderr


def test_forced_command_refuses_an_unbound_target(tmp_path):
    key = tmp_path / "key"
    known = tmp_path / "known_hosts"
    key.write_text("test-only")
    known.write_text("test-only")
    env = {
        **os.environ,
        "SEICHE_DEPLOY_HOST": "192.0.2.10",
        "SEICHE_DEPLOY_KEY_FILE": str(key),
        "SEICHE_KNOWN_HOSTS_FILE": str(known),
        "SEICHE_SSH_BIN": "/usr/bin/false",
    }
    env.pop("SEICHE_EXPECTED_TARGET_SHA", None)

    result = subprocess.run(
        ["bash", str(FORCED_DEPLOY)], env=env, text=True, capture_output=True
    )

    assert result.returncode != 0
    assert "SEICHE_EXPECTED_TARGET_SHA is required" in result.stderr


def test_box_smoke_installs_its_declared_async_test_plugin():
    optional = tomllib.loads(PYPROJECT.read_text())["project"]["optional-dependencies"]
    deploy_dependencies = optional["deploy-test"]
    box_update = BOX_UPDATE.read_text()

    assert any(item.startswith("pytest-asyncio") for item in deploy_dependencies)
    assert "./backend[deploy-test,notary,collectors,postgres]" in box_update
    assert "TARGET=${SEICHE_UPDATE_TARGET_SHA:-}" in box_update
    assert 'git reset -q --hard "$TARGET"' in box_update
    assert "git reset -q --hard origin/main" not in box_update


def test_wrapper_runs_edge_sync_on_new_and_already_running_release():
    wrapper = (ROOT / "ops" / "deploy" / "seiche-deploy-wrapper.sh").read_text()
    assert wrapper.count("deploy_caddy ||") == 2
    assert (
        "already running ${AFTER:0:7} — checking candidate rebuild and edge config"
        in wrapper
    )
    assert "/api/v2/coverage" in wrapper
    assert "systemctl is-active --quiet postgresql" in wrapper
    assert wrapper.index("systemctl stop seiche-market-worker.service") < wrapper.index(
        "bash /home/seiche/update.sh"
    )
    target = wrapper.index("TARGET=$LATEST")
    quiesce = wrapper.index("systemctl stop seiche-api", target)
    update = wrapper.index("bash /home/seiche/update.sh", quiesce)
    assert target < quiesce < update
    assert 'SEICHE_UPDATE_TARGET_SHA="$TARGET"' in wrapper[quiesce:update]
    assert 'if [ "$BEFORE" != "$TARGET" ] || [ "$DEPLOYED" != "$TARGET" ]' in wrapper
    update_failure = wrapper[update : wrapper.index('AFTER=""', update)]
    assert "restore_pre_restart_services" in update_failure
    assert "application update gate failed; recovery was attempted" in wrapper
    recovery = wrapper[
        wrapper.index("restore_pre_restart_services()") : wrapper.index(
            "systemctl stop seiche-market-worker.service"
        )
    ]
    assert recovery.index("restore_quiesced_api") < recovery.index(
        "restore_market_services"
    )
    assert "market writers remain stopped because api recovery failed" in recovery
    assert (
        "healthy candidate code remains running and no rollback was attempted"
        in wrapper
    )
    market_installer = wrapper[
        wrapper.index("deploy_market_platform()") : wrapper.index(
            "deploy_market_platform ||"
        )
    ]
    caddy_installer = wrapper[
        wrapper.index("deploy_caddy()") : wrapper.index("deploy_market_platform()")
    ]
    assert "/usr/bin/env -i" in market_installer
    assert "SEICHE_DEFER_MARKET_START=1" in market_installer
    assert 'SEICHE_PRIVILEGED_ASSET_ROOT="$SIGNED_ASSET_ROOT"' in market_installer
    assert 'SEICHE_RELEASE_TARGET_SHA="$TARGET"' in market_installer
    assert '/usr/bin/bash "$installer"' in market_installer
    assert "SEICHE_DEFER_MARKET_START" not in caddy_installer
    healthy_release = wrapper[wrapper.index('if [ -n "$HEALTHY" ]') :]
    assert healthy_release.index("start_market_services") < healthy_release.index(
        "deploy_caddy ||"
    )


def test_wrapper_quiesces_and_restores_source_worker_and_readiness_timer():
    wrapper = DEPLOY_WRAPPER.read_text()
    admission = wrapper.index("if ! admit_shared_host; then")
    source_capture = wrapper.index('SOURCE_WORKER_WAS_ACTIVE=""', admission)
    source_enabled_capture = wrapper.index(
        'SOURCE_WORKER_WAS_ENABLED=""', source_capture
    )
    timer_capture = wrapper.index(
        'READINESS_TIMER_WAS_ACTIVE=""', source_enabled_capture
    )
    enabled_capture = wrapper.index('READINESS_TIMER_WAS_ENABLED=""', timer_capture)
    unit_capture = wrapper.index(
        "if ! capture_preupdate_data_units; then", enabled_capture
    )
    recovery_stop = wrapper.index(
        "systemctl stop seiche-release-recovery-seal.service", unit_capture
    )
    timer_stop = wrapper.index("seiche-data-readiness.timer", recovery_stop)
    writer_stop = wrapper.index(
        "systemctl stop seiche-market-worker.service seiche-market-backfill.service",
        timer_stop,
    )
    update = wrapper.index("bash /home/seiche/update.sh", writer_stop)

    assert (
        source_capture
        < source_enabled_capture
        < timer_capture
        < enabled_capture
        < unit_capture
        < recovery_stop
        < timer_stop
        < writer_stop
        < update
    )
    assert "seiche-source-worker.service" in wrapper[writer_stop:update]

    restore = wrapper[
        wrapper.index("restore_market_services() {") : wrapper.index(
            "start_market_services() {"
        )
    ]
    assert 'SOURCE_WORKER_WAS_ACTIVE" ]' in restore
    assert 'READINESS_TIMER_WAS_ACTIVE" ]' in restore
    restore_source = restore.index("systemctl start seiche-source-worker.service")
    restore_timer = restore.index(
        "systemctl start --no-block seiche-data-readiness.timer"
    )
    assert restore_source < restore_timer
    assert "systemctl start --no-block seiche-source-worker.service" not in restore

    start = wrapper[
        wrapper.index("start_market_services() {") : wrapper.index(
            'MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=""'
        )
    ]
    assert "systemctl reset-failed" in start
    assert "seiche-market-worker.service seiche-source-worker.service" in start
    assert "seiche-market-backfill.service seiche-market-worker.service" in start
    assert "systemctl start --no-block" in start
    assert "seiche-source-worker.service" in start
    assert "activate_data_readiness_after_proof" not in start
    assert "ensure_source_worker_ready" not in start
    assert "seiche-source-worker.service seiche-data-readiness.timer" not in start

    rollback = wrapper[wrapper.index("# A red warm-up") :]
    rollback_recovery_stop = rollback.index(
        "systemctl stop seiche-release-recovery-seal.service"
    )
    rollback_timer_stop = rollback.index(
        "seiche-data-readiness.timer", rollback_recovery_stop
    )
    rollback_writer_stop = rollback.index(
        "systemctl stop seiche-market-worker.service seiche-market-backfill.service"
    )
    reset = rollback.index('reset -q --hard "$DEPLOYED"')
    restored = rollback.index("restore_market_services", reset)
    assert rollback_timer_stop < rollback_writer_stop < reset < restored
    assert "seiche-source-worker.service" in rollback[rollback_writer_stop:reset]


def test_wrapper_queues_workers_without_waiting_for_recovery(tmp_path: Path):
    wrapper = DEPLOY_WRAPPER.read_text()
    helper = wrapper[
        wrapper.index("start_market_services() {") : wrapper.index(
            'MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=""'
        )
    ]
    state = tmp_path / "state"
    state.mkdir()
    fake_systemctl = _executable(
        tmp_path / "systemctl",
        """
state=${FAKE_DATA_STATE:?}
printf 'systemctl %s\n' "$*" >>"$state/calls.log"
case "$*" in
  "reset-failed seiche-market-worker.service seiche-source-worker.service")
    exit 0
    ;;
  "start --no-block seiche-market-backfill.service seiche-market-worker.service seiche-source-worker.service")
    exit 0
    ;;
  *)
    exit 92
    ;;
esac
""",
    )
    harness = f"""
{helper}
start_market_services
"""

    result = subprocess.run(
        ["bash", "-c", harness],
        env=os.environ
        | {
            "FAKE_DATA_STATE": str(state),
            "PATH": f"{fake_systemctl.parent}:{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "calls.log").read_text().splitlines() == [
        "systemctl reset-failed seiche-market-worker.service seiche-source-worker.service",
        "systemctl start --no-block seiche-market-backfill.service seiche-market-worker.service seiche-source-worker.service",
    ]


def test_wrapper_fails_if_recovery_workers_cannot_be_queued(
    tmp_path: Path,
) -> None:
    wrapper = DEPLOY_WRAPPER.read_text()
    helper = wrapper[
        wrapper.index("start_market_services() {") : wrapper.index(
            'MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=""'
        )
    ]
    fake_systemctl = _executable(
        tmp_path / "systemctl",
        """
case "$*" in
  "reset-failed seiche-market-worker.service seiche-source-worker.service") exit 0 ;;
  "start --no-block seiche-market-backfill.service seiche-market-worker.service seiche-source-worker.service") exit 92 ;;
  *) exit 92 ;;
esac
""",
    )
    harness = f"""
{helper}
start_market_services
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        env=os.environ
        | {
            "PATH": f"{fake_systemctl.parent}:{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "could not be queued" in result.stdout


@pytest.mark.parametrize(
    ("entry_mode", "expected_calls"),
    [
        ("local", []),
        (
            "forced",
            [
                "systemctl reset-failed seiche-release-recovery-seal.service",
                "systemctl start --no-block seiche-release-recovery-seal.service",
            ],
        ),
    ],
)
def test_forced_wrapper_queues_recovery_after_edge_convergence(
    tmp_path: Path,
    entry_mode: str,
    expected_calls: list[str],
) -> None:
    wrapper = DEPLOY_WRAPPER.read_text()
    helper = wrapper[
        wrapper.index("queue_forced_recovery_seal() {") : wrapper.index(
            'MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=""'
        )
    ]
    state = tmp_path / "state"
    state.mkdir()
    fake_systemctl = _executable(
        tmp_path / "systemctl",
        'printf "systemctl %s\\n" "$*" >>"${FAKE_DATA_STATE:?}/calls.log"\n',
    )
    result = subprocess.run(
        ["bash", "-c", f"{helper}\nqueue_forced_recovery_seal"],
        env=os.environ
        | {
            "FAKE_DATA_STATE": str(state),
            "PATH": f"{fake_systemctl.parent}:{os.environ['PATH']}",
            "SEICHE_DEPLOY_ENTRY_MODE": entry_mode,
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = state / "calls.log"
    assert (calls.read_text().splitlines() if calls.exists() else []) == expected_calls
    for branch_start in (
        wrapper.index('if [ "$BEFORE" = "$AFTER" ] && [ "$DEPLOYED" = "$AFTER" ]'),
        wrapper.index('if [ -n "$HEALTHY" ]'),
    ):
        edge = wrapper.index("deploy_caddy ||", branch_start)
        verdict = wrapper.index("sync_verdict", edge)
        queued = wrapper.index("queue_forced_recovery_seal", verdict)
        assert edge < verdict < queued


def test_wrapper_defers_source_worker_until_after_strict_candidate_health():
    wrapper = DEPLOY_WRAPPER.read_text()

    accepted_branch = wrapper.index(
        'if [ "$BEFORE" = "$AFTER" ] && [ "$DEPLOYED" = "$AFTER" ]'
    )
    normal_branch = wrapper.index('HEALTHY=""', accepted_branch)
    accepted_body = wrapper[accepted_branch:normal_branch]
    assert "ensure_source_worker_ready" not in accepted_body
    assert (
        'candidate_health_wait "$API_FULL_REBUILD_WAIT_SECONDS" "$AFTER"'
        in accepted_body
    )

    normal_body = wrapper[normal_branch:]
    before_cutover = normal_body[: normal_body.index('if [ -n "$HEALTHY" ]')]
    assert "ensure_source_worker_ready" not in before_cutover
    assert (
        'candidate_health_wait "$API_FULL_REBUILD_WAIT_SECONDS" "$AFTER"'
        in before_cutover
    )


def test_deploy_wrapper_warmup_timeout_contract():
    wrapper = DEPLOY_WRAPPER.read_text()
    full_wait = 'candidate_health_wait "$API_FULL_REBUILD_WAIT_SECONDS" "$AFTER"'

    assert wrapper.count("API_FULL_REBUILD_WAIT_SECONDS=1800") == 1

    restore = wrapper[
        wrapper.index("restore_preupdate_api() {") : wrapper.index(
            "restore_quiesced_api() {"
        )
    ]
    assert restore.count("deadline=$((SECONDS + API_FULL_REBUILD_WAIT_SECONDS))") == 1

    accepted_start = wrapper.index(
        'if [ "$BEFORE" = "$AFTER" ] && [ "$DEPLOYED" = "$AFTER" ]'
    )
    normal_start = wrapper.index('HEALTHY=""', accepted_start)
    accepted = wrapper[accepted_start:normal_start]
    normal = wrapper[normal_start : wrapper.index('if [ -n "$HEALTHY" ]', normal_start)]
    assert accepted.count(full_wait) == 1
    assert normal.count(full_wait) == 1

    rollback = wrapper[wrapper.index("# A red warm-up") :]
    assert rollback.count('rollback_health_wait "$API_FULL_REBUILD_WAIT_SECONDS"') == 1

    promotion = wrapper[
        wrapper.index("promote_snapshot_handoff() {") : wrapper.index(
            "MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=1"
        )
    ]
    assert 'candidate_health_wait 120 "$AFTER"' in promotion
    recovery = RECOVERY_SEAL.read_text()
    assert "MAX_FRESH_WAIT_SECONDS=900" in recovery
    assert "systemctl restart seiche-api" not in recovery


def test_wrapper_restores_the_worker_unit_when_candidate_code_rolls_back():
    wrapper = DEPLOY_WRAPPER.read_text()
    helper = wrapper[
        wrapper.index("restore_preupdate_market_worker_unit()") : wrapper.index(
            "restore_preupdate_api()"
        )
    ]

    assert 'git -C "$APP" show' not in helper
    assert "restored by restore_preupdate_data_units" in helper

    captured = wrapper[
        wrapper.index("DATA_UNIT_NAMES=(") : wrapper.index(
            "cleanup_preupdate_data_units()"
        )
    ]
    assert "seiche-market-worker.service" in captured
    assert "/opt/seiche-nbs-intake/current-sha" in captured

    deploy = wrapper.index("MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=1")
    provision = wrapper.index("deploy_market_platform ||", deploy)
    assert deploy < provision

    recovery = wrapper[
        wrapper.index("restore_pre_restart_services()") : wrapper.index(
            "systemctl stop seiche-market-worker.service"
        )
    ]
    assert (
        recovery.index("restore_preupdate_market_worker_unit")
        < recovery.index("restore_preupdate_data_units")
        < recovery.index("restore_quiesced_api")
        < recovery.index("restore_market_services")
    )

    rollback = wrapper[wrapper.index("rolling the service back to") :]
    assert (
        rollback.index("restore_preupdate_market_worker_unit")
        < rollback.index("restore_preupdate_data_units")
        < rollback.index("systemctl restart seiche-api")
        < rollback.index("restore_market_services")
    )


def test_wrapper_restores_exact_predeploy_data_units_and_readiness_timer_state():
    wrapper = DEPLOY_WRAPPER.read_text()
    capture = wrapper[
        wrapper.index("capture_preupdate_data_units() {") : wrapper.index(
            "cleanup_data_unit_restore_stage() {"
        )
    ]
    restore = wrapper[
        wrapper.index("restore_preupdate_data_units() {") : wrapper.index(
            "trap 'release_market_mutation_lock || true; "
            "cleanup_preupdate_data_units || true; "
            "cleanup_signed_release_assets || true' EXIT"
        )
    ]
    unit_names_start = wrapper.index("DATA_UNIT_NAMES=(")
    unit_names = wrapper[
        unit_names_start : wrapper.index("DATA_UNIT_ROLLBACK_DIR", unit_names_start)
    ]

    for unit in (
        "seiche-market-backfill.service",
        "seiche-source-worker.service",
        "seiche-release-recovery-seal.service",
        "seiche-data-readiness.service",
        "seiche-data-readiness.timer",
        "seiche-market-offsite-backup.service",
        "seiche-market-offsite-backup.timer",
    ):
        assert unit in unit_names
    for artifact in (
        "nbs-intake-launcher",
        "data-readiness-helper",
        "release-recovery-helper",
        "market-offsite-backup-helper",
        "market-backup-helper",
        "market-restore-check-helper",
        "nbs-runtime-current-sha",
        "/etc/seiche/libexec/seiche-nbs-intake.py",
        "/etc/seiche/libexec/seiche-data-readiness.sh",
        "/etc/seiche/libexec/seiche-release-recovery-seal.sh",
        "/etc/seiche/libexec/seiche-market-offsite-backup.sh",
        "/etc/seiche/libexec/seiche-market-backup.sh",
        "/etc/seiche/libexec/seiche-market-restore-check.sh",
        "/opt/seiche-nbs-intake/current-sha",
    ):
        assert artifact in unit_names
    assert 'mktemp -d "$DEPLOY_RUNTIME_DIR/.data-units.XXXXXX"' in capture
    assert '[ -L "$destination" ] || [ ! -f "$destination" ]' in capture
    assert 'cp -p -- "$destination"' in capture
    assert '"$DATA_UNIT_ROLLBACK_DIR/$unit.present"' in capture
    assert '"$DATA_UNIT_ROLLBACK_DIR/$unit.absent"' in capture

    assert 'systemd-analyze verify "${candidates[@]}"' in restore
    assert 'mv -f "$stage/$unit" "$destination"' in restore
    assert 'rm -f -- "$destination"' in restore
    assert restore.index('mv -f "$stage/$unit" "$destination"') < restore.index(
        "systemctl daemon-reload"
    )
    assert "SOURCE_WORKER_WAS_ENABLED" in restore
    assert "READINESS_TIMER_WAS_ENABLED" in restore
    assert "OFFSITE_TIMER_WAS_ENABLED" in restore
    assert "systemctl enable seiche-source-worker.service" in restore
    assert "systemctl disable seiche-source-worker.service" in restore
    assert "systemctl is-enabled --quiet seiche-source-worker.service" in restore
    assert "systemctl enable seiche-data-readiness.timer" in restore
    assert "systemctl disable seiche-data-readiness.timer" in restore
    assert "systemctl is-enabled --quiet seiche-data-readiness.timer" in restore
    assert "systemctl enable seiche-market-offsite-backup.timer" in restore
    assert "systemctl disable seiche-market-offsite-backup.timer" in restore
    assert "systemctl is-enabled --quiet seiche-market-offsite-backup.timer" in restore

    capture_call = wrapper.index("if ! capture_preupdate_data_units; then")
    quiesce = wrapper.index(
        "systemctl stop seiche-release-recovery-seal.service", capture_call
    )
    provision_flag = wrapper.index("DATA_UNITS_MAY_HAVE_CHANGED=1", quiesce)
    provision = wrapper.index("deploy_market_platform ||", provision_flag)
    assert capture_call < quiesce < provision_flag < provision

    recovery = wrapper[wrapper.index("restore_pre_restart_services() {") : quiesce]
    assert (
        recovery.index("restore_preupdate_market_worker_unit")
        < recovery.index("restore_preupdate_data_units")
        < recovery.index("restore_quiesced_api")
        < recovery.index("restore_market_services")
    )

    rollback = wrapper[wrapper.index("rolling the service back to") :]
    assert (
        rollback.index("restore_preupdate_market_worker_unit")
        < rollback.index("restore_preupdate_data_units")
        < rollback.index("systemctl restart seiche-api")
        < rollback.index('rollback_health_wait "$API_FULL_REBUILD_WAIT_SECONDS"')
        < rollback.index("restore_market_services")
    )

    restore_api = wrapper[
        wrapper.index("restore_preupdate_api()") : wrapper.index(
            "restore_quiesced_api()"
        )
    ]
    assert "deadline=$((SECONDS + API_FULL_REBUILD_WAIT_SECONDS))" in restore_api


def _run_readiness_activation_helper(
    script_path: Path,
    tmp_path: Path,
    *,
    readiness_mode: str,
    fail_command: str = "",
    curl_mode: str = "success",
    nudge_curl_mode: str = "success",
    api_mode: str = "active",
    candidate_health_mode: str = "success",
    candidate_health_wait_mode: str = "success",
    convergence_wait_seconds: str = "900",
    log_candidate_health: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    script = script_path.read_text()
    helper_start = script.index("DATA_READINESS_PREFLIGHT_REQUIRED_UNITS=")
    if script_path == DEPLOY_WRAPPER:
        helper_end = script.index("start_market_services() {", helper_start)
    else:
        helper_end = script.index(
            'if [ "${SEICHE_DEFER_MARKET_START:-0}" != "1" ]; then',
            helper_start,
        )
    helper = script[helper_start:helper_end]
    local_bash = shutil.which("bash")
    assert local_bash is not None
    helper = helper.replace("/usr/bin/bash", local_bash)

    def privileged_test_executable(path: Path, body: str) -> Path:
        path.write_text(f"#!{local_bash} -p\nset -u\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return path

    app = tmp_path / "app"
    readiness = app / "ops" / "deploy" / "seiche-data-readiness.sh"
    readiness.parent.mkdir(parents=True)
    privileged_test_executable(
        readiness,
        """
state=${FAKE_DATA_STATE:?}
count_file=$state/readiness-count
count=0
[ ! -f "$count_file" ] || count=$(cat "$count_file")
count=$((count + 1))
printf '%s\n' "$count" >"$count_file"
kind=full
[ "${SEICHE_DATA_READINESS_PROOF_ONLY:-0}" != "1" ] || kind=proof
printf 'readiness %s %s\n' "$kind" "${SEICHE_DATA_READINESS_REQUIRED_UNITS:-}" >>"$state/calls.log"
if [ "$kind" = full ]; then
  full_count_file=$state/readiness-full-count
  full_count=0
  [ ! -f "$full_count_file" ] || full_count=$(cat "$full_count_file")
  full_count=$((full_count + 1))
  printf '%s\n' "$full_count" >"$full_count_file"
fi
case "${FAKE_READINESS_MODE:?}" in
  current)
    printf 'seiche data readiness: ready\n'
    exit 0
    ;;
  fresh)
    if [ "$kind" = proof ] && [ "$count" -eq 1 ]; then
      printf 'seiche data readiness: restore receipt missing or invalid\n' >&2
      exit 1
    fi
    printf 'seiche data readiness: ready\n'
    exit 0
    ;;
  operational-fail)
    if [ "$kind" = proof ]; then
      printf 'seiche data readiness: ready\n'
      exit 0
    fi
    printf 'seiche data readiness: API health reports critical faults\n' >&2
    exit 1
    ;;
  always-fail)
    printf 'seiche data readiness: restore receipt missing or invalid\n' >&2
    exit 1
    ;;
  stale-then-fresh)
    if [ "$kind" = full ] && [ "$full_count" -eq 1 ]; then
      printf 'seiche data readiness: API snapshot stale\n' >&2
      exit 1
    fi
    printf 'seiche data readiness: ready\n'
    exit 0
    ;;
  stale-always)
    if [ "$kind" = proof ]; then
      printf 'seiche data readiness: ready\n'
      exit 0
    fi
    printf 'seiche data readiness: API snapshot stale\n' >&2
    exit 1
    ;;
  stale-wrong-status)
    if [ "$kind" = proof ]; then
      printf 'seiche data readiness: ready\n'
      exit 0
    fi
    printf 'seiche data readiness: API snapshot stale\n' >&2
    exit 2
    ;;
  stale-then-other)
    if [ "$kind" = proof ]; then
      printf 'seiche data readiness: ready\n'
      exit 0
    elif [ "$full_count" -eq 1 ]; then
      printf 'seiche data readiness: API snapshot stale\n' >&2
    else
      printf 'seiche data readiness: collector heartbeat unhealthy\n' >&2
    fi
    exit 1
    ;;
  unexpected-success)
    printf 'unexpected success output\n'
    exit 0
    ;;
  *) exit 64 ;;
esac
""",
    )
    runtime_readiness = f'DATA_READINESS_SCRIPT="{readiness}"'
    helper = helper.replace(
        "DATA_READINESS_SCRIPT=/etc/seiche/libexec/seiche-data-readiness.sh",
        runtime_readiness,
    ).replace(
        'DATA_READINESS_SCRIPT="$READINESS_SCRIPT_INSTALLED"',
        runtime_readiness,
    )
    helper = helper.replace(
        "HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \\",
        "HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \\\n"
        '    FAKE_DATA_STATE="$FAKE_DATA_STATE" \\\n'
        '    FAKE_READINESS_MODE="$FAKE_READINESS_MODE" \\',
    )
    state = tmp_path / "state"
    state.mkdir()
    bash_startup_sentinel = tmp_path / "ambient-bash-startup-ran"
    bash_env = tmp_path / "hostile-bash-env"
    bash_env.write_text(
        f"touch {str(bash_startup_sentinel)!r}\n",
        encoding="utf-8",
    )
    privileged_test_executable(tmp_path / "sleep", "exit 0\n")
    fake_systemctl = privileged_test_executable(
        tmp_path / "systemctl",
        """
state=${FAKE_DATA_STATE:?}
printf 'systemctl %s\n' "$*" >>"$state/calls.log"
if [ "$*" = "${FAKE_FAIL_COMMAND:-}" ]; then
  exit 1
fi
if [ "$*" = "is-active --quiet seiche-api.service" ]; then
  case "${FAKE_API_MODE:?}" in
    active) exit 0 ;;
    dead) exit 1 ;;
    dies-after-trigger)
      [ ! -f "$state/refresh-triggered" ]
      exit
      ;;
    *) exit 64 ;;
  esac
fi
if [ "$*" = "enable --now seiche-data-readiness.timer" ]; then
  touch "$state/readiness-timer.enabled"
fi
""",
    )
    fake_curl = privileged_test_executable(
        tmp_path / "curl",
        """
state=${FAKE_DATA_STATE:?}
printf 'curl %s\n' "$*" >>"$state/calls.log"
if [ "$1" = -sf ]; then
  [ "${FAKE_NUDGE_CURL_MODE:?}" != fail ]
  exit
fi
[ "${FAKE_CURL_MODE:?}" != fail ] || exit 1
touch "$state/refresh-triggered"
""",
    )
    helper = helper.replace("/usr/bin/curl", str(fake_curl))
    environment = os.environ | {
        "APP": str(app),
        "APP_DIR": str(app),
        "FAKE_DATA_STATE": str(state),
        "FAKE_READINESS_MODE": readiness_mode,
        "FAKE_FAIL_COMMAND": fail_command,
        "FAKE_CURL_MODE": curl_mode,
        "FAKE_NUDGE_CURL_MODE": nudge_curl_mode,
        "FAKE_API_MODE": api_mode,
        "FAKE_CANDIDATE_HEALTH_MODE": candidate_health_mode,
        "FAKE_CANDIDATE_HEALTH_WAIT_MODE": candidate_health_wait_mode,
        "FAKE_LOG_CANDIDATE_HEALTH": "1" if log_candidate_health else "0",
        "SEICHE_DATA_READINESS_CONVERGENCE_WAIT_SECONDS": convergence_wait_seconds,
        "SEICHE_DATA_READINESS_PROOF_ONLY": "1",
        "SEICHE_DATA_READINESS_REQUIRED_UNITS": "attacker-controlled.service",
        "BASH_ENV": str(bash_env),
        "PATH": f"{fake_systemctl.parent}:{os.environ['PATH']}",
    }
    result = subprocess.run(
        [
            local_bash,
            "-p",
            "-c",
            f"""
{helper}
candidate_health_wait() {{
  printf 'candidate-health-wait %s\n' "$*" >>"$FAKE_DATA_STATE/calls.log"
  wait_count_file=$FAKE_DATA_STATE/candidate-health-wait-count
  wait_count=0
  [ ! -f "$wait_count_file" ] || wait_count=$(cat "$wait_count_file")
  wait_count=$((wait_count + 1))
  printf '%s\n' "$wait_count" >"$wait_count_file"
  case "${{FAKE_CANDIDATE_HEALTH_WAIT_MODE:?}}" in
    success) return 0 ;;
    fail) return 1 ;;
    fail-once) [ "$wait_count" -ne 1 ] ;;
    fail-after-first) [ "$wait_count" -eq 1 ] ;;
    *) return 64 ;;
  esac
}}
candidate_health_once() {{
  [ "${{FAKE_LOG_CANDIDATE_HEALTH:?}}" != 1 ] \\
    || printf 'candidate-health %s\n' "$*" >>"$FAKE_DATA_STATE/calls.log"
  [ "${{FAKE_CANDIDATE_HEALTH_MODE:?}}" != fail ]
}}
AFTER={"a" * 40}
activate_data_readiness_after_proof
""",
        ],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    calls_path = state / "calls.log"
    calls = calls_path.read_text().splitlines() if calls_path.exists() else []
    assert not bash_startup_sentinel.exists()
    return result, calls, state


def _run_candidate_health_wait_helper(
    tmp_path: Path,
    *,
    response_mode: str,
    window: int,
    max_generated_age: int = 900,
    api_active: bool = True,
    report_elapsed: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    wrapper = DEPLOY_WRAPPER.read_text()
    helper_start = wrapper.index("parse_candidate_health()")
    helper = wrapper[
        helper_start : wrapper.index("# A rollback target can predate", helper_start)
    ]
    helper = helper.replace('"$APP/backend/.venv/bin/python"', f'"{sys.executable}"')
    state = tmp_path / "candidate-health-state"
    state.mkdir()
    fake_curl = _executable(
        tmp_path / "curl",
        """
state=${FAKE_CANDIDATE_STATE:?}
count_file=$state/curl-count
count=0
[ ! -f "$count_file" ] || count=$(cat "$count_file")
count=$((count + 1))
printf '%s\n' "$count" >"$count_file"
printf 'curl %s\n' "$*" >>"$state/calls.log"
case "${FAKE_CANDIDATE_RESPONSE_MODE:?}" in
  reseals)
    if [ "$count" -eq 1 ]; then
      exit 22
    else
      printf '{"generated_at":"%s","release_candidate":{"producer_sha":"%s","activation_token":"%s"}}\n' \
        "${FAKE_GENERATED_AT:?}" "${FAKE_EXPECTED_SHA:?}" \
        "${FAKE_ACTIVATION_TOKEN:?}"
    fi
    ;;
  reseals-after-900)
    if [ "$count" -le 91 ]; then
      printf 'http-503\n' >>"$state/calls.log"
      exit 22
    else
      printf '{"generated_at":"%s","release_candidate":{"producer_sha":"%s","activation_token":"%s"}}\n' \
        "${FAKE_GENERATED_AT:?}" "${FAKE_EXPECTED_SHA:?}" \
        "${FAKE_ACTIVATION_TOKEN:?}"
    fi
    ;;
  unavailable|dies-after-900)
    printf 'http-503\n' >>"$state/calls.log"
    exit 22
    ;;
  drift)
    printf '{"generated_at":"%s","release_candidate":{"producer_sha":"%s","activation_token":"%s"}}\n' \
      "${FAKE_GENERATED_AT:?}" "${FAKE_DRIFT_SHA:?}" \
      "${FAKE_ACTIVATION_TOKEN:?}"
    ;;
  missing)
    printf '{"status":"rebuilt_without_market_evidence"}\n'
    ;;
  stale)
    printf '{"generated_at":"2000-01-01T00:00:00+00:00","release_candidate":{"producer_sha":"%s","activation_token":"%s"}}\n' \
      "${FAKE_EXPECTED_SHA:?}" "${FAKE_ACTIVATION_TOKEN:?}"
    ;;
  *) exit 64 ;;
esac
""",
    )
    _executable(
        tmp_path / "systemctl",
        """
printf 'systemctl %s\n' "$*" >>"${FAKE_CANDIDATE_STATE:?}/calls.log"
[ "$*" = "is-active --quiet seiche-api" ] || exit 64
[ "${FAKE_CANDIDATE_RESPONSE_MODE:?}" != dies-after-900 ] || {
  count=$(cat "${FAKE_CANDIDATE_STATE:?}/curl-count")
  [ "$count" -le 91 ]
  exit
}
[ "${FAKE_API_ACTIVE:?}" = 1 ]
""",
    )
    expected_sha = "a" * 40
    activation_token = "c" * 64
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"""
sleep() {{
  printf 'sleep %s\n' "$1" >>"$FAKE_CANDIDATE_STATE/calls.log"
  SECONDS=$((SECONDS + $1))
}}
{helper}
SECONDS=0
candidate_health_wait {window} {expected_sha} {max_generated_age}
status=$?
[ "$status" -ne 0 ] || printf 'activation-token=%s\n' "$ACTIVATION_TOKEN"
[ "${{FAKE_REPORT_ELAPSED:?}}" != 1 ] || printf 'elapsed-seconds=%s\n' "$SECONDS"
exit "$status"
""",
        ],
        env=os.environ
        | {
            "APP": str(tmp_path / "app"),
            "FAKE_CANDIDATE_STATE": str(state),
            "FAKE_CANDIDATE_RESPONSE_MODE": response_mode,
            "FAKE_EXPECTED_SHA": expected_sha,
            "FAKE_DRIFT_SHA": "b" * 40,
            "FAKE_ACTIVATION_TOKEN": activation_token,
            "FAKE_GENERATED_AT": datetime.now(UTC).isoformat(),
            "FAKE_API_ACTIVE": "1" if api_active else "0",
            "FAKE_REPORT_ELAPSED": "1" if report_elapsed else "0",
            "PATH": f"{fake_curl.parent}:{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    calls_path = state / "calls.log"
    calls = calls_path.read_text().splitlines() if calls_path.exists() else []
    return result, calls


def test_candidate_health_wait_recovers_when_exact_evidence_reseals_after_refresh(
    tmp_path: Path,
) -> None:
    result, calls = _run_candidate_health_wait_helper(
        tmp_path,
        response_mode="reseals",
        window=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert [call.split()[0] for call in calls] == [
        "curl",
        "systemctl",
        "sleep",
        "curl",
    ]
    assert calls[1] == "systemctl is-active --quiet seiche-api"
    assert calls[2] == "sleep 10"
    assert result.stdout == f"activation-token={'c' * 64}\n"


def test_candidate_health_wait_allows_503_past_900_before_full_rebuild_bound(
    tmp_path: Path,
) -> None:
    result, calls = _run_candidate_health_wait_helper(
        tmp_path,
        response_mode="reseals-after-900",
        window=1800,
        report_elapsed=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.count("http-503") == 91
    assert calls.count("systemctl is-active --quiet seiche-api") == 91
    assert calls.count("sleep 10") == 91
    assert f"activation-token={'c' * 64}" in result.stdout
    elapsed = int(result.stdout.split("elapsed-seconds=", 1)[1].splitlines()[0])
    assert 900 < elapsed < 1800


def test_candidate_health_wait_rejects_exact_sha_drift_immediately(
    tmp_path: Path,
) -> None:
    result, calls = _run_candidate_health_wait_helper(
        tmp_path,
        response_mode="drift",
        window=1800,
    )

    assert result.returncode != 0
    assert [call.split()[0] for call in calls] == ["curl"]
    assert "invalid exact-release evidence" in result.stdout


@pytest.mark.parametrize("response_mode", ["missing", "stale"])
def test_candidate_health_wait_rejects_invalid_2xx_contract_immediately(
    tmp_path: Path,
    response_mode: str,
) -> None:
    result, calls = _run_candidate_health_wait_helper(
        tmp_path,
        response_mode=response_mode,
        window=1800,
    )

    assert result.returncode != 0
    assert [call.split()[0] for call in calls] == ["curl"]
    assert "invalid exact-release evidence" in result.stdout


def test_candidate_health_wait_bounds_continuous_503_at_1800_seconds(
    tmp_path: Path,
) -> None:
    result, calls = _run_candidate_health_wait_helper(
        tmp_path,
        response_mode="unavailable",
        window=1800,
        report_elapsed=True,
    )

    assert result.returncode != 0
    failed_probes = calls.count("http-503")
    service_checks = calls.count("systemctl is-active --quiet seiche-api")
    sleeps = calls.count("sleep 10")
    # Bash's special SECONDS counter includes both the synthetic ten-second
    # sleeps and real subprocess overhead, so the exact probe count can vary
    # by one across hosts. The final failed probe must be the only operation
    # after the deadline; no additional service check or sleep is permitted.
    assert 179 <= failed_probes <= 181
    assert service_checks == failed_probes - 1
    assert sleeps == service_checks
    assert "exact release after 30min warm-up window" in result.stdout
    elapsed = int(result.stdout.split("elapsed-seconds=", 1)[1].splitlines()[0])
    assert 1800 <= elapsed < 1820


def test_candidate_health_wait_detects_api_death_after_900_seconds(
    tmp_path: Path,
) -> None:
    result, calls = _run_candidate_health_wait_helper(
        tmp_path,
        response_mode="dies-after-900",
        window=1800,
        report_elapsed=True,
    )

    assert result.returncode != 0
    assert calls.count("http-503") == 92
    assert calls.count("systemctl is-active --quiet seiche-api") == 92
    assert calls.count("sleep 10") == 91
    assert "seiche-api died during warm-up" in result.stdout
    elapsed = int(result.stdout.split("elapsed-seconds=", 1)[1].splitlines()[0])
    assert 910 <= elapsed < 930


def test_candidate_health_wait_fails_immediately_if_api_dies_during_reseal(
    tmp_path: Path,
) -> None:
    result, calls = _run_candidate_health_wait_helper(
        tmp_path,
        response_mode="unavailable",
        window=120,
        api_active=False,
    )

    assert result.returncode != 0
    assert [call.split()[0] for call in calls] == ["curl", "http-503", "systemctl"]
    assert "seiche-api died during warm-up" in result.stdout


@pytest.mark.parametrize("script_path", [MARKET_INSTALLER])
def test_release_readiness_preflights_have_scoped_offsite_repair_bypass(
    script_path: Path,
) -> None:
    script = script_path.read_text()
    helper_start = script.index("DATA_READINESS_PREFLIGHT_REQUIRED_UNITS=")
    if script_path == DEPLOY_WRAPPER:
        helper_end = script.index("start_market_services() {", helper_start)
    else:
        helper_end = script.index(
            'if [ "${SEICHE_DEFER_MARKET_START:-0}" != "1" ]; then',
            helper_start,
        )
    helper = script[helper_start:helper_end]

    assert helper.count("SEICHE_DATA_READINESS_SKIP_OFFSITE=1") == 2


@pytest.mark.parametrize("script_path", [MARKET_INSTALLER])
def test_fresh_v2_host_proves_backup_restore_and_readiness_before_timer(
    script_path: Path, tmp_path: Path
):
    result, calls, state = _run_readiness_activation_helper(
        script_path, tmp_path, readiness_mode="fresh"
    )

    assert result.returncode == 0, result.stderr
    expected_kinds = [
        "readiness",
        "systemctl",
        "systemctl",
        "readiness",
    ]
    expected_calls = [
        calls[0],
        "systemctl start seiche-market-backup.service",
        "systemctl start seiche-market-restore-check.service",
        calls[3],
    ]
    if script_path == DEPLOY_WRAPPER:
        expected_kinds += ["curl", "candidate-health-wait"]
        expected_calls += [
            "curl -sf -m 20 http://127.0.0.1:8787/api/gauge",
            "candidate-health-wait 1800 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 900",
        ]
    final_readiness = calls[-3] if script_path == DEPLOY_WRAPPER else calls[-2]
    expected_kinds.append("readiness")
    expected_calls.append(final_readiness)
    if script_path == DEPLOY_WRAPPER:
        expected_kinds.append("candidate-health-wait")
        expected_calls.append(f"candidate-health-wait 120 {'a' * 40} 900")
    expected_kinds.append("systemctl")
    expected_calls.append("systemctl enable --now seiche-data-readiness.timer")
    assert [call.split()[0] for call in calls] == expected_kinds
    assert calls == expected_calls
    assert calls[0].startswith("readiness proof ")
    assert calls[0] == calls[3]
    assert final_readiness.startswith("readiness full ")
    assert "seiche-data-readiness.timer" not in final_readiness
    assert (state / "readiness-timer.enabled").is_file()


@pytest.mark.parametrize("script_path", [MARKET_INSTALLER])
def test_current_v2_proof_activates_timer_without_redundant_restore_drill(
    script_path: Path, tmp_path: Path
):
    result, calls, state = _run_readiness_activation_helper(
        script_path, tmp_path, readiness_mode="current"
    )

    assert result.returncode == 0, result.stderr
    expected_calls = [calls[0]]
    if script_path == DEPLOY_WRAPPER:
        expected_calls += [
            "curl -sf -m 20 http://127.0.0.1:8787/api/gauge",
            "candidate-health-wait 1800 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 900",
        ]
    final_readiness = calls[-3] if script_path == DEPLOY_WRAPPER else calls[-2]
    expected_calls.append(final_readiness)
    if script_path == DEPLOY_WRAPPER:
        expected_calls.append(f"candidate-health-wait 120 {'a' * 40} 900")
    expected_calls.append("systemctl enable --now seiche-data-readiness.timer")
    assert calls == expected_calls
    assert calls[0].startswith("readiness proof ")
    assert final_readiness.startswith("readiness full ")
    assert calls[-1] == "systemctl enable --now seiche-data-readiness.timer"
    assert (state / "readiness-timer.enabled").is_file()


@pytest.mark.parametrize("script_path", [MARKET_INSTALLER])
def test_stale_snapshot_triggers_one_refresh_then_proves_full_readiness(
    script_path: Path, tmp_path: Path
) -> None:
    result, calls, state = _run_readiness_activation_helper(
        script_path,
        tmp_path,
        readiness_mode="stale-then-fresh",
        log_candidate_health=True,
    )

    assert result.returncode == 0, result.stderr
    expected_kinds = ["readiness"]
    if script_path == DEPLOY_WRAPPER:
        expected_kinds += ["curl", "candidate-health-wait"]
    expected_kinds += ["readiness", "systemctl", "curl", "systemctl", "readiness"]
    if script_path == DEPLOY_WRAPPER:
        expected_kinds.append("candidate-health-wait")
    expected_kinds.append("systemctl")
    assert [call.split()[0] for call in calls] == expected_kinds
    assert calls[0].startswith("readiness proof ")
    readiness_index = 3 if script_path == DEPLOY_WRAPPER else 1
    assert calls[readiness_index].startswith("readiness full ")
    assert calls[readiness_index + 1] == (
        "systemctl is-active --quiet seiche-api.service"
    )
    assert calls[readiness_index + 2].startswith(
        "curl --fail --silent --show-error --proto =http "
    )
    assert calls[readiness_index + 2].endswith("http://127.0.0.1:8787/api/gauge")
    assert calls[readiness_index + 3] == (
        "systemctl is-active --quiet seiche-api.service"
    )
    assert calls[readiness_index + 4] == calls[readiness_index]
    assert sum("--proto =http" in call for call in calls) == 1
    assert calls[-1] == "systemctl enable --now seiche-data-readiness.timer"
    if script_path == DEPLOY_WRAPPER:
        assert calls[-2] == f"candidate-health-wait 120 {'a' * 40} 900"
    else:
        assert not any(call.startswith("candidate-health-wait ") for call in calls)
    assert (state / "readiness-timer.enabled").is_file()


@pytest.mark.parametrize("script_path", [MARKET_INSTALLER])
def test_stale_snapshot_refresh_trigger_failure_leaves_timer_disabled(
    script_path: Path, tmp_path: Path
) -> None:
    result, calls, state = _run_readiness_activation_helper(
        script_path,
        tmp_path,
        readiness_mode="stale-then-fresh",
        curl_mode="fail",
    )

    assert result.returncode != 0
    expected_kinds = ["readiness"]
    if script_path == DEPLOY_WRAPPER:
        expected_kinds += ["curl", "candidate-health-wait"]
    expected_kinds += ["readiness", "systemctl", "curl"]
    assert [call.split()[0] for call in calls] == expected_kinds
    assert "refresh trigger failed" in (result.stdout + result.stderr)
    assert not (state / "readiness-timer.enabled").exists()


@pytest.mark.parametrize("script_path", [MARKET_INSTALLER])
def test_stale_snapshot_convergence_fails_if_api_dies_after_trigger(
    script_path: Path, tmp_path: Path
) -> None:
    result, calls, state = _run_readiness_activation_helper(
        script_path,
        tmp_path,
        readiness_mode="stale-then-fresh",
        api_mode="dies-after-trigger",
    )

    assert result.returncode != 0
    expected_kinds = ["readiness"]
    if script_path == DEPLOY_WRAPPER:
        expected_kinds += ["curl", "candidate-health-wait"]
    expected_kinds += ["readiness", "systemctl", "curl", "systemctl"]
    assert [call.split()[0] for call in calls] == expected_kinds
    assert "died during stale snapshot convergence" in (result.stdout + result.stderr)
    assert not (state / "readiness-timer.enabled").exists()


@pytest.mark.parametrize("script_path", [MARKET_INSTALLER])
def test_stale_snapshot_convergence_timeout_is_bounded_and_disables_timer(
    script_path: Path, tmp_path: Path
) -> None:
    result, calls, state = _run_readiness_activation_helper(
        script_path,
        tmp_path,
        readiness_mode="stale-always",
        convergence_wait_seconds="0",
    )

    assert result.returncode != 0
    expected_kinds = ["readiness"]
    if script_path == DEPLOY_WRAPPER:
        expected_kinds += ["curl", "candidate-health-wait"]
    expected_kinds += [
        "readiness",
        "systemctl",
        "curl",
        "systemctl",
        "readiness",
    ]
    assert [call.split()[0] for call in calls] == expected_kinds
    assert "API snapshot remained stale after 0s" in (result.stdout + result.stderr)
    assert not (state / "readiness-timer.enabled").exists()


@pytest.mark.parametrize("script_path", [MARKET_INSTALLER])
@pytest.mark.parametrize(
    "readiness_mode",
    ["operational-fail", "stale-wrong-status", "unexpected-success"],
)
def test_non_stale_or_malformed_readiness_results_fail_without_refresh(
    script_path: Path, tmp_path: Path, readiness_mode: str
) -> None:
    result, calls, state = _run_readiness_activation_helper(
        script_path, tmp_path, readiness_mode=readiness_mode
    )

    assert result.returncode != 0
    if script_path == DEPLOY_WRAPPER:
        assert calls[1] == "curl -sf -m 20 http://127.0.0.1:8787/api/gauge"
        assert not any("--proto =http" in call for call in calls)
    else:
        assert not any(call.startswith("curl ") for call in calls)
    assert not any(
        call == "systemctl is-active --quiet seiche-api.service" for call in calls
    )
    assert not (state / "readiness-timer.enabled").exists()


@pytest.mark.parametrize("script_path", [MARKET_INSTALLER])
def test_new_readiness_failure_after_refresh_fails_immediately(
    script_path: Path, tmp_path: Path
) -> None:
    result, calls, state = _run_readiness_activation_helper(
        script_path, tmp_path, readiness_mode="stale-then-other"
    )

    assert result.returncode != 0
    assert sum("--proto =http" in call for call in calls) == 1
    assert sum(call.startswith("readiness full ") for call in calls) == 2
    assert "collector heartbeat unhealthy" in result.stderr
    assert not (state / "readiness-timer.enabled").exists()


@pytest.mark.parametrize("script_path", [MARKET_INSTALLER])
@pytest.mark.parametrize("wait_seconds", ["901", "not-a-number", "-1"])
def test_readiness_convergence_wait_is_strictly_bounded(
    script_path: Path, tmp_path: Path, wait_seconds: str
) -> None:
    result, calls, state = _run_readiness_activation_helper(
        script_path,
        tmp_path,
        readiness_mode="current",
        convergence_wait_seconds=wait_seconds,
    )

    assert result.returncode != 0
    assert calls == []
    assert "integer from 0 to 900 seconds" in (result.stdout + result.stderr)
    assert not (state / "readiness-timer.enabled").exists()


def test_recovery_seal_proves_health_before_activating_readiness_timer() -> None:
    recovery = RECOVERY_SEAL.read_text()

    worker_start = recovery.index(
        "seiche-market-backfill.service seiche-market-worker.service"
    )
    proof = recovery.index("if ! run_recovery_proof_preflight", worker_start)
    freshness = recovery.index("wait_for_fresh_candidate", proof)
    readiness = recovery.index("converge_operational_readiness", freshness)
    timer = recovery.index(
        '"$SYSTEMCTL" enable --now seiche-data-readiness.timer', readiness
    )
    final_identity = recovery.index("FINAL_IDENTITY=$(load_release_identity)", timer)
    receipt = recovery.index('"schema": "seiche.release-recovery-receipt.v1"')

    assert (
        worker_start < proof < freshness < readiness < timer < final_identity < receipt
    )
    assert "candidate_health_once" in recovery[freshness:timer]


def test_recovery_seal_restores_rails_before_waiting_for_controller_receipt() -> None:
    recovery = RECOVERY_SEAL.read_text()
    absent_receipt = recovery.index('print(target, "-", "-", "awaiting-receipt")')
    worker_start = recovery.index(
        '"$SYSTEMCTL" start \\\n    seiche-market-backfill.service seiche-market-worker.service'
    )
    backup = recovery.index('"$SYSTEMCTL" start seiche-market-backup.service')
    readiness_timer = recovery.index(
        '"$SYSTEMCTL" enable --now seiche-data-readiness.timer'
    )
    final_identity = recovery.index("FINAL_IDENTITY=$(load_release_identity)")

    assert absent_receipt < worker_start < backup < readiness_timer < final_identity
    assert (
        "recovery proof is ready but the immutable release receipt is pending"
        in recovery
    )


@pytest.mark.parametrize("script_path", [MARKET_INSTALLER])
def test_readiness_stale_convergence_preserves_freshness_and_poll_bounds(
    script_path: Path,
) -> None:
    script = script_path.read_text()
    helper_start = script.index("DATA_READINESS_PREFLIGHT_REQUIRED_UNITS=")
    if script_path == DEPLOY_WRAPPER:
        helper_end = script.index("start_market_services() {", helper_start)
    else:
        helper_end = script.index(
            'if [ "${SEICHE_DEFER_MARKET_START:-0}" != "1" ]; then',
            helper_start,
        )
    helper = script[helper_start:helper_end]
    readiness_script = (
        ROOT / "ops" / "deploy" / "seiche-data-readiness.sh"
    ).read_text()

    assert (
        'MAX_GENERATED_AGE="${SEICHE_DATA_READINESS_MAX_GENERATED_AGE_SECONDS:-900}"'
        in readiness_script
    )
    assert "SEICHE_DATA_READINESS_CONVERGENCE_WAIT_SECONDS:-900" in helper
    assert "http://127.0.0.1:8787/api/gauge" in helper
    assert "sleep 10" in helper
    assert "SEICHE_DATA_READINESS_MAX_GENERATED_AGE_SECONDS=" not in helper


@pytest.mark.parametrize("script_path", [MARKET_INSTALLER])
@pytest.mark.parametrize(
    ("readiness_mode", "fail_command"),
    [
        ("fresh", "start seiche-market-backup.service"),
        ("fresh", "start seiche-market-restore-check.service"),
        ("always-fail", ""),
        ("operational-fail", ""),
        ("current", "enable --now seiche-data-readiness.timer"),
    ],
)
def test_readiness_bootstrap_failures_leave_timer_disabled(
    script_path: Path,
    tmp_path: Path,
    readiness_mode: str,
    fail_command: str,
):
    result, calls, state = _run_readiness_activation_helper(
        script_path,
        tmp_path,
        readiness_mode=readiness_mode,
        fail_command=fail_command,
    )

    assert result.returncode != 0
    assert calls[0].startswith("readiness proof ")
    assert not (state / "readiness-timer.enabled").exists()
    if readiness_mode == "operational-fail":
        if script_path == DEPLOY_WRAPPER:
            assert len(calls) == 4
            assert calls[1].startswith("curl ")
            assert calls[2].startswith("candidate-health-wait ")
        else:
            assert len(calls) == 2
        assert calls[-1].startswith("readiness full ")
        assert not any(
            call.startswith("systemctl start seiche-market-backup") for call in calls
        )
    if fail_command != "enable --now seiche-data-readiness.timer":
        assert "systemctl enable --now seiche-data-readiness.timer" not in calls


def test_async_recovery_failure_never_restarts_the_live_api() -> None:
    recovery = RECOVERY_SEAL.read_text()

    assert '"$REFRESH_URL"' in recovery
    assert "Restart=on-failure" in RECOVERY_SEAL_SERVICE.read_text()
    assert "systemctl restart seiche-api" not in recovery
    assert "exact candidate did not become fresh without an API restart" in recovery


def test_recovery_refresh_does_not_rewrite_or_repromote_the_release() -> None:
    refresh = RECOVERY_SEAL.read_text()

    assert "/api/gauge" in refresh
    assert "wait_for_fresh_candidate" in refresh
    assert "write_deployed_state" not in refresh
    assert "write_release_env" not in refresh
    assert "promote_snapshot_handoff" not in refresh


def test_market_platform_units_are_independent_and_postgres_backed():
    installer = (ROOT / "ops" / "deploy" / "install-market-platform.sh").read_text()
    worker = MARKET_WORKER.read_text()
    source_worker = SOURCE_WORKER.read_text()
    readiness_service = DATA_READINESS_SERVICE.read_text()
    readiness_timer = DATA_READINESS_TIMER.read_text()
    backfill = (ROOT / "ops" / "deploy" / "seiche-market-backfill.service").read_text()
    validation = (
        ROOT / "ops" / "deploy" / "seiche-market-validation.service"
    ).read_text()
    validation_timer = (
        ROOT / "ops" / "deploy" / "seiche-market-validation.timer"
    ).read_text()
    backup = (ROOT / "ops" / "deploy" / "seiche-market-backup.service").read_text()
    backup_script = (ROOT / "ops" / "deploy" / "seiche-market-backup.sh").read_text()
    backup_timer = (ROOT / "ops" / "deploy" / "seiche-market-backup.timer").read_text()
    restore = (
        ROOT / "ops" / "deploy" / "seiche-market-restore-check.service"
    ).read_text()
    restore_timer = (
        ROOT / "ops" / "deploy" / "seiche-market-restore-check.timer"
    ).read_text()
    caddy = CADDYFILE.read_text()

    assert 'psql -tAc "SHOW port"' in installer
    assert '"SHOW server_version_num"' in installer
    assert '"$POSTGRES_VERSION_NUM" -lt 110000' in installer
    assert "host=/var/run/postgresql&port=$POSTGRES_PORT" in installer
    assert "could not resolve the PostgreSQL cluster port" in installer
    assert 'connection.execute("SELECT 1")' in installer
    assert "get_repository().forward_record_count()" in installer
    assert 'FORWARD_MIGRATION_MARKERS" != "1|1|1' in installer
    assert "seiche-api.service seiche-market-worker.service" in installer
    assert "must be inactive before the forward-chain migration" in installer
    assert "duplicate forward children exist outside" in installer
    assert "SEICHE_RAW_CAPTURE_DIR=$STATE_DIR/raw" in installer
    assert 'NBS_STATE_DIR="${SEICHE_NBS_STATE_DIR:-/var/lib/seiche-nbs}"' in installer
    assert "ensure_nbs_evidence_tree" in installer
    assert 'grp.getgrnam("seiche").gr_gid' in installer
    assert '"$NBS_STATE_DIR" "$SEICHE_NBS_GID"' in installer
    assert 'install -d -o root -g seiche -m 0750 "$NBS_STATE_DIR"' not in installer
    assert 'install -d -o root -g root -m 0700 "$NBS_RESTRICTED_DIR"' not in installer
    assert "Environment=SEICHE_NBS_PUBLIC_DIR=$NBS_PUBLIC_DIR" in installer
    assert "ReadOnlyPaths=$NBS_PUBLIC_DIR" in installer
    assert "InaccessiblePaths=$NBS_RESTRICTED_DIR" in installer
    assert (
        installer.count("RequiresMountsFor=$STATE_DIR $NBS_STATE_DIR $BACKUP_DIR") == 2
    )
    assert '"$STATE_DIR/validation"' in installer
    assert "SEICHE_VALIDATION_DIR=$STATE_DIR/validation" in installer
    assert "seiche-market-validation.service" in installer
    assert "seiche-market-validation.timer" in installer
    assert "seiche-source-worker.service" in installer
    assert "seiche-data-readiness.service" in installer
    assert "seiche-data-readiness.timer" in installer
    assert (
        "READINESS_SCRIPT_INSTALLED=/etc/seiche/libexec/seiche-data-readiness.sh"
        in installer
    )
    assert "install_runtime_shell_helper()" in installer
    assert "install_runtime_python_helper()" in installer
    assert 'install -o root -g root -m 0755 "$source" "$stage"' in installer
    assert '/usr/bin/bash -n "$stage"' in installer
    assert '/usr/bin/python3 -I "$stage" --help' in installer
    assert '/usr/bin/sync -f "$stage"' in installer
    assert (
        "install_runtime_python_helper \\\n"
        '    "$NBS_INTAKE_LAUNCHER_SOURCE" "$NBS_INTAKE_LAUNCHER_INSTALLED"'
        in installer
    )
    assert (
        'NBS_INTAKE_LAUNCHER_INSTALLED="$STORAGE_PREFLIGHT_INSTALL_DIR/'
        'seiche-nbs-intake.py"' in installer
    )
    assert (
        "install_runtime_shell_helper \\\n"
        '    "$READINESS_SCRIPT_SOURCE" "$READINESS_SCRIPT_INSTALLED"' in installer
    )
    assert 'DATA_READINESS_SCRIPT="$READINESS_SCRIPT_INSTALLED"' in installer
    assert "ReadWritePaths=$RECOVERY_PROOF_DIR" in installer
    assert "systemctl enable --now seiche-market-validation.timer" in installer
    readiness_boundary = installer.index("DATA_READINESS_PREFLIGHT_REQUIRED_UNITS=")
    activation_reload = installer.rindex(
        "systemctl daemon-reload", 0, readiness_boundary
    )
    early_enable = installer[activation_reload:readiness_boundary]
    assert (
        "seiche-data-readiness.timer"
        not in early_enable.split(
            "systemctl enable --now seiche-market-validation.timer", 1
        )[0]
    )
    assert "SEICHE_DEFER_MARKET_START:-0}" in installer
    worker_verify = installer.index("worker unit failed verification")
    worker_install = installer.index(
        'mv -f "$WORKER_UNIT_STAGE_DIR/seiche-market-worker.service"'
    )
    assert installer.index("systemd-analyze verify", 0, worker_verify) < worker_install
    data_verify = installer.index("data-plane units failed verification")
    source_install = installer.index(
        'mv -f "$DATA_UNIT_STAGE_DIR/seiche-source-worker.service"'
    )
    readiness_install = installer.index(
        'mv -f "$DATA_UNIT_STAGE_DIR/seiche-data-readiness.timer"'
    )
    backfill_install = installer.index(
        'mv -f "$DATA_UNIT_STAGE_DIR/seiche-market-backfill.service"'
    )
    assert (
        '"$DATA_UNIT_STAGE_DIR/seiche-market-backfill.service"'
        in installer[installer.index("cleanup() {") : data_verify]
    )
    assert installer.index("systemd-analyze verify", worker_install, data_verify) < (
        source_install
    )
    assert data_verify < source_install < readiness_install < backfill_install
    assert "SEICHE_FUNDING_EXPORT_READER_GROUP" in installer
    assert 'groupadd --system "$EXPORT_READER_GROUP"' in installer
    assert 'setfacl -m "g:$EXPORT_READER_GROUP:--x"' in installer
    assert 'chmod 2750 "$FUNDING_EXPORT_DIR"' in installer
    assert 'chmod 0640 "$FUNDING_EXPORT_FILE"' in installer
    assert "setfacl -R" not in installer
    assert 'find "$FUNDING_EXPORT_DIR"' not in installer
    funding_acl = installer[: installer.index("ENV_STAGE=")]
    assert "usermod" not in funding_acl
    assert 'FUNDING_EXPORT_DIR="$STATE_DIR/exports/us-usd-funding-core-v1"' in installer
    assert "SEICHE_USD_FUNDING_CORE_EXPORT_DIR=$FUNDING_EXPORT_DIR" in installer
    assert "seiche-market-backfill.service seiche-market-worker.service" in installer
    installer_start = installer[
        installer.index('if [ "${SEICHE_DEFER_MARKET_START:-0}" != "1" ]; then') :
    ]
    installer_source = installer_start.index(
        "systemctl start seiche-source-worker.service"
    )
    installer_timer = installer_start.index("activate_data_readiness_after_proof")
    assert installer_source < installer_timer
    assert "systemctl start --no-block" not in installer_start
    assert (
        "systemctl start --no-block seiche-source-worker.service" not in installer_start
    )
    assert (
        "seiche-source-worker.service seiche-data-readiness.timer"
        not in installer_start
    )
    assert "ExecStart=/home/seiche/app/backend/.venv/bin/seiche market-worker" in worker
    assert "EnvironmentFile=-/etc/seiche/rbnz-access.env" in worker
    assert "EnvironmentFile=-/etc/seiche/bok-ecos.env" in worker
    assert "Restart=always" in worker
    assert "OnFailure=undertow-failure-alert@%n.service" in worker
    assert "StartLimitIntervalSec=15min" in worker
    assert "StartLimitBurst=5" in worker
    assert (
        "ExecStart=/home/seiche/app/backend/.venv/bin/seiche "
        "source-worker --poll-seconds 300"
    ) in source_worker
    assert "Type=notify" in source_worker
    assert "NotifyAccess=main" in source_worker
    assert "WatchdogSec=180" in source_worker
    assert "TimeoutStartSec=15min" in source_worker
    assert "Restart=always" in source_worker
    assert "OnFailure=undertow-failure-alert@%n.service" in source_worker
    assert "StartLimitIntervalSec=15min" in source_worker
    assert "StartLimitBurst=5" in source_worker
    assert "CapabilityBoundingSet=\n" in source_worker
    assert "ProtectSystem=strict" in source_worker
    assert "ProtectHome=read-only" in source_worker
    assert "MemoryMax=2G" in source_worker
    assert "TasksMax=128" in source_worker
    assert "ReadWritePaths=/home/seiche/app/backend/data" in source_worker
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in source_worker
    assert "OnFailure=undertow-failure-alert@%n.service" in readiness_service
    assert (
        "ExecStart=/usr/bin/env -i HOME=/root LANG=C LC_ALL=C "
        "PATH=/usr/bin:/bin /usr/bin/bash -p "
        "/etc/seiche/libexec/seiche-data-readiness.sh" in readiness_service
    )
    readiness_unset = {
        name
        for line in readiness_service.splitlines()
        if line.startswith("UnsetEnvironment=")
        for name in line.removeprefix("UnsetEnvironment=").split()
    }
    assert {
        "GCONV_PATH",
        "GLIBC_TUNABLES",
        "LD_AUDIT",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "LOCPATH",
    } <= readiness_unset
    assert "EnvironmentFile=" not in readiness_service
    assert (
        "After=seiche-market-worker.service seiche-source-worker.service"
        in readiness_timer
    )
    assert "Type=oneshot" in backfill
    assert "EnvironmentFile=-/etc/seiche/rbnz-access.env" in backfill
    assert "EnvironmentFile=-/etc/seiche/bok-ecos.env" in backfill
    assert "TimeoutStartSec=2h" in backfill
    assert "CPUQuota=100%" in backfill
    assert "CPUWeight=10" in backfill
    assert "IOWeight=10" in backfill
    assert "Nice=10" in backfill
    assert (
        "ExecStart=/home/seiche/app/backend/.venv/bin/seiche market-validate"
        in validation
    )
    assert "--evidence-dir" not in validation
    assert "SuccessExitStatus=2" in validation
    assert "After=network-online.target postgresql.service" in validation
    assert "seiche-market-worker.service" not in validation
    assert "seiche-api.service" not in validation
    assert "OnCalendar=*-*-* 03:15:00 UTC" in validation_timer
    assert "Persistent=true" in validation_timer
    assert "Unit=seiche-market-validation.service" in validation_timer
    assert "seiche-market-backup.service" in installer
    assert "seiche-market-backup.timer" in installer
    assert "seiche-market-restore-check.service" in installer
    assert "seiche-market-restore-check.timer" in installer
    assert "PACKAGES+=(util-linux)" in installer
    assert "/usr/bin/setpriv is required" in installer
    assert "ReadWritePaths=$BACKUP_DIR" in installer
    assert "ReadWritePaths=$STATE_DIR/validation" in installer
    assert (
        "ExecStart=/etc/seiche/libexec/seiche-palimpsest-china-activate.py "
        "--run-market-locked backup" in backup
    )
    assert "ExecStart=/usr/bin/flock" not in backup
    assert "mountpoint -q" in backup_script
    assert '"$CP_BIN" -R -- "$API_DATA_DIR/." "$API_STAGE/"' in backup_script
    assert "cp -a --" not in backup_script
    assert "CPUQuota=50%" in backup
    assert "MemoryMax=1G" in backup
    assert "ProtectSystem=strict" in backup
    assert "RestrictAddressFamilies=AF_UNIX" in backup
    assert "NoNewPrivileges=true" in backup
    assert "RestrictSUIDSGID=true" in backup
    assert (
        "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_READ_SEARCH CAP_SETGID CAP_SETUID"
        in backup
    )
    assert "AmbientCapabilities=CAP_SETGID CAP_SETUID" in backup
    backup_capabilities = next(
        line
        for line in backup.splitlines()
        if line.startswith("CapabilityBoundingSet=")
    )
    assert "CAP_CHOWN" in backup_capabilities
    assert (
        "ReadWritePaths=/var/backups/seiche-market /run/lock "
        "/run/seiche-deploy" in backup
    )
    assert "RuntimeDirectory=seiche-deploy" in backup
    assert "RuntimeDirectoryMode=0700" in backup
    assert "RuntimeDirectoryPreserve=yes" in backup
    assert (
        "ReadOnlyPaths=/home/seiche/app /var/lib/seiche /var/lib/seiche-nbs "
        "/var/lib/seiche-palimpsest-china /var/lib/seiche-deploy" in backup
    )
    assert "/var/lib/seiche-deploy/deployed-sha" in backup_script
    assert "OnCalendar=*-*-* 02:00:00 UTC" in backup_timer
    assert "RandomizedDelaySec=10m" in backup_timer
    assert "Persistent=true" in backup_timer
    assert (
        "ExecStart=/etc/seiche/libexec/seiche-palimpsest-china-activate.py "
        "--run-market-locked restore" in restore
    )
    assert "ExecStart=/usr/bin/flock" not in restore
    assert "ReadOnlyPaths=/home/seiche/app /var/backups/seiche-market" in restore
    assert (
        "ReadWritePaths=/var/lib/seiche-recovery-proof /run/lock "
        "/run/seiche-deploy" in restore
    )
    assert "RuntimeDirectory=seiche-deploy" in restore
    assert "RuntimeDirectoryMode=0700" in restore
    assert "RuntimeDirectoryPreserve=yes" in restore
    assert "CAP_CHOWN" in restore
    assert "CAP_DAC_OVERRIDE" in restore
    assert "NoNewPrivileges=true" in restore
    assert "RestrictSUIDSGID=false" in restore
    assert "AmbientCapabilities=CAP_SETGID CAP_SETUID" in restore
    assert "OnCalendar=Sun *-*-* 07:30:00 UTC" in restore_timer
    assert "RandomizedDelaySec=15m" in restore_timer
    assert "Persistent=true" in restore_timer
    assert "/api/v2/*" in caddy
    assert "RBNZ_ACCESS_ENV_FILE=/etc/seiche/rbnz-access.env" in installer
    assert "SEICHE_RBNZ_ACCESS_ENV_FILE" not in installer
    assert "RBNZ access env ownership/mode is unsafe" in installer
    assert "SEICHE_RBNZ_ACCESS_APPROVAL_SHA256=[0-9a-f]{64}" in installer
    assert "SEICHE_RBNZ_ACCESS_APPROVAL_VALID_UNTIL=[0-9]{4}" in installer
    assert "BOK_ECOS_ENV_FILE=/etc/seiche/bok-ecos.env" in installer
    assert "SEICHE_BOK_ECOS_ENV_FILE" not in installer
    assert "BOK ECOS env ownership/mode is unsafe" in installer
    assert "SEICHE_BOK_ECOS_API_KEY=[A-Za-z0-9]{8,128}" in installer
    assert 'wc -l <"$BOK_ECOS_ENV_FILE"' in installer


def test_restore_check_limits_setgid_recovery_to_its_private_write_boundary():
    service = (
        ROOT / "ops" / "deploy" / "seiche-market-restore-check.service"
    ).read_text()
    script = (ROOT / "ops" / "deploy" / "seiche-market-restore-check.sh").read_text()
    writable_directives = [
        line for line in service.splitlines() if line.startswith("ReadWritePaths=")
    ]

    assert script.count("0o2750") == 1
    assert "directories.append((revisions, 0o2750))" in script
    assert "RestrictSUIDSGID=false" in service
    assert "RestrictSUIDSGID=true" not in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert writable_directives == [
        "ReadWritePaths=/var/lib/seiche-recovery-proof /run/lock /run/seiche-deploy"
    ]


def test_cfets_approval_artifact_is_validated_and_wired_to_both_collectors():
    installer = MARKET_INSTALLER.read_text()
    worker = MARKET_WORKER.read_text()
    backfill = (ROOT / "ops" / "deploy" / "seiche-market-backfill.service").read_text()
    source_worker = SOURCE_WORKER.read_text()
    runbook = (ROOT / "docs" / "CFETS_ACCESS_BOUNDARY.md").read_text()

    for unit in (worker, backfill):
        assert "EnvironmentFile=-/etc/seiche/cfets-access.env" in unit
        assert "ReadOnlyPaths=-/etc/seiche/cfets-approval.conf" in unit
        assert "-/etc/seiche/cfets-licence-evidence.pdf" in unit
    assert "cfets-access.env" not in source_worker
    assert "cfets-approval.conf" not in source_worker

    assert "CFETS_ACCESS_ENV_FILE=/etc/seiche/cfets-access.env" in installer
    assert "CFETS_APPROVAL_FILE=/etc/seiche/cfets-approval.conf" in installer
    assert (
        "CFETS_LICENCE_EVIDENCE_FILE=/etc/seiche/cfets-licence-evidence.pdf"
        in installer
    )
    assert "SEICHE_CFETS_ACCESS_ENV_FILE" not in installer
    assert "SEICHE_CFETS_APPROVAL_FILE" not in installer
    assert "CFETS access env ownership/mode is unsafe" in installer
    assert "CFETS approval artifact ownership/mode is unsafe" in installer
    assert "CFETS approval artifact size is unsafe" in installer
    assert "CFETS approval artifact contract is invalid" in installer
    assert "CFETS approval artifact digest mismatch" in installer
    assert "CFETS licence evidence ownership/mode is unsafe" in installer
    assert "CFETS licence evidence size is unsafe" in installer
    assert "CFETS licence evidence digest mismatch" in installer
    assert "CFETS approval review window is unsafe" in installer
    assert "CFETS approval artifacts have no access env pin" in installer
    assert "SEICHE_CFETS_APPROVAL_PATH=$CFETS_APPROVAL_FILE" in installer
    assert "SEICHE_CFETS_APPROVAL_SHA256=[0-9a-f]{64}" in installer
    assert "stat -c '%U:%G:%a:%h' \"$CFETS_APPROVAL_FILE\"" in installer
    assert '/usr/bin/sha256sum "$CFETS_APPROVAL_FILE"' in installer
    assert "schema=seiche.cfets-approval.v2" in installer
    assert "upstream_products=FDR007,SHIBOR_ON" in installer
    assert "canonical_outputs=CN.CFETS.FDR007,CN.CFETS.SHIBOR_ON" in installer
    assert (
        "collection_scope=automated_bounded_fdr007_and_shibor_on_history" in installer
    )
    assert "permitted_use=internal_research_only" in installer
    assert "publication=prohibited" in installer
    assert "raw_response_retention=prohibited" in installer
    assert "retained_projection=event_date,value" in installer
    assert "licence_evidence_path=$CFETS_LICENCE_EVIDENCE_FILE" in installer
    assert "/usr/bin/sha256sum" in installer
    assert '"$CFETS_LICENCE_EVIDENCE_FILE" | cut' in installer
    assert "CFETS_REVIEW_DAYS" in installer
    assert '"$CFETS_REVIEW_DAYS" -gt 366' in installer
    assert installer.index("CFETS approval artifact digest mismatch") < installer.index(
        "WORKER_UNIT_STAGE_DIR=$(mktemp"
    )

    for contract in (
        "root:seiche",
        "0640",
        "internal_research_only",
        "publication=prohibited",
        "no more than 366 days",
        "before every",
    ):
        assert contract in runbook


def test_legacy_updater_is_retired_before_other_host_services_change():
    installer = MARKET_INSTALLER.read_text()
    retirer = LEGACY_UPDATE_RETIRER.read_text()

    retirement = installer.index('/usr/bin/bash "$LEGACY_UPDATE_RETIRER"')
    assert retirement < installer.index("systemctl enable --now postgresql")
    assert retirement < installer.index("systemctl daemon-reload")
    assert "seiche-update.service" in retirer
    assert "seiche-update.timer" in retirer
    assert '"$SYSTEMCTL_BIN" disable --now "$TIMER_NAME"' in retirer
    assert '"$SYSTEMCTL_BIN" mask --now "$SERVICE_NAME" "$TIMER_NAME"' in retirer
    assert "ExecStartPost" not in retirer
    assert "GIT_SSH_COMMAND" not in retirer


def test_legacy_updater_retirement_archives_exact_units_and_masks_both(tmp_path):
    env, systemd_dir, state_dir = _legacy_retirement_fixture(tmp_path)
    original_service = (systemd_dir / "seiche-update.service").read_bytes()
    original_timer = (systemd_dir / "seiche-update.timer").read_bytes()

    result = _run_legacy_retirement(env)

    assert result.returncode == 0, result.stderr
    archive = state_dir / "retired-units" / "seiche-update-v1"
    assert (archive / "seiche-update.service").read_bytes() == original_service
    assert (archive / "seiche-update.timer").read_bytes() == original_timer
    assert (archive / "seiche-update.service").stat().st_mode & 0o777 == 0o644
    assert (archive / "seiche-update.timer").stat().st_mode & 0o777 == 0o644
    prestate = (archive / "pre-retirement-state.env").read_text()
    assert "timer_enabled=enabled" in prestate
    assert "timer_active=active" in prestate
    assert (archive / "SHA256SUMS").is_file()
    assert (archive / "STAT").is_file()
    assert (systemd_dir / "seiche-update.service").readlink() == Path("/dev/null")
    assert (systemd_dir / "seiche-update.timer").readlink() == Path("/dev/null")
    assert not (systemd_dir / "timers.target.wants" / "seiche-update.timer").exists()
    assert not (
        systemd_dir / "multi-user.target.wants" / "seiche-update.service"
    ).exists()


def test_legacy_updater_retirement_is_idempotent(tmp_path):
    env, _systemd_dir, state_dir = _legacy_retirement_fixture(tmp_path)
    first = _run_legacy_retirement(env)
    assert first.returncode == 0, first.stderr
    archive = state_dir / "retired-units" / "seiche-update-v1"
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in archive.iterdir()
        if path.is_file()
    }

    second = _run_legacy_retirement(env)

    assert second.returncode == 0, second.stderr
    after = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in archive.iterdir()
        if path.is_file()
    }
    assert after == before
    calls = Path(env["FAKE_SYSTEMCTL_STATE"], "calls.log").read_text()
    assert calls.count("disable --now seiche-update.timer\n") == 1
    assert calls.count("disable seiche-update.service\n") == 1


def test_legacy_updater_retirement_records_never_present_units(tmp_path):
    env, systemd_dir, state_dir = _legacy_retirement_fixture(tmp_path)
    fake_state = Path(env["FAKE_SYSTEMCTL_STATE"])
    for unit_name in ("seiche-update.service", "seiche-update.timer"):
        (systemd_dir / unit_name).unlink()
        (fake_state / f"{unit_name}.active").unlink(missing_ok=True)
        (fake_state / f"{unit_name}.enabled").unlink(missing_ok=True)
    for wants_dir in ("timers.target.wants", "multi-user.target.wants"):
        for wants_link in (systemd_dir / wants_dir).iterdir():
            wants_link.unlink()

    first = _run_legacy_retirement(env)
    second = _run_legacy_retirement(env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    archive = state_dir / "retired-units" / "seiche-update-v1"
    assert (archive / "seiche-update.service.absent").is_file()
    assert (archive / "seiche-update.timer.absent").is_file()
    assert "seiche-update.service.absent" in (archive / "SHA256SUMS").read_text()
    assert "seiche-update.timer.absent" in (archive / "SHA256SUMS").read_text()


@pytest.mark.parametrize(
    "masked_unit", ("seiche-update.service", "seiche-update.timer")
)
def test_legacy_updater_retirement_rejects_unproven_premasked_units(
    tmp_path, masked_unit
):
    env, systemd_dir, state_dir = _legacy_retirement_fixture(tmp_path)
    fake_state = Path(env["FAKE_SYSTEMCTL_STATE"])
    (systemd_dir / masked_unit).unlink()
    (systemd_dir / masked_unit).symlink_to("/dev/null")
    for unit_name in ("seiche-update.service", "seiche-update.timer"):
        (fake_state / f"{unit_name}.active").unlink(missing_ok=True)
        (fake_state / f"{unit_name}.enabled").unlink(missing_ok=True)
    for wants_dir in ("timers.target.wants", "multi-user.target.wants"):
        for wants_link in (systemd_dir / wants_dir).iterdir():
            wants_link.unlink()

    result = _run_legacy_retirement(env)

    assert result.returncode != 0
    assert "no verified retirement evidence" in result.stderr
    archive = state_dir / "retired-units" / "seiche-update-v1"
    assert not (archive / masked_unit).exists()
    assert not (archive / f"{masked_unit}.absent").exists()
    assert not (archive / "SHA256SUMS").exists()
    assert not (archive / "STAT").exists()
    assert (systemd_dir / masked_unit).readlink() == Path("/dev/null")


def test_legacy_updater_retirement_accepts_failed_state_and_partial_stage(tmp_path):
    env, _systemd_dir, state_dir = _legacy_retirement_fixture(tmp_path)
    fake_state = Path(env["FAKE_SYSTEMCTL_STATE"])
    (fake_state / "seiche-update.service.state").write_text("failed\n")
    archive = state_dir / "retired-units" / "seiche-update-v1"
    archive.mkdir(parents=True)
    interrupted = archive / ".seiche-update.service.archive.partial"
    interrupted.write_text("partial\n")

    result = _run_legacy_retirement(env)

    assert result.returncode == 0, result.stderr
    assert (archive / "seiche-update.service").is_file()
    assert (archive / "seiche-update.timer").is_file()
    assert not interrupted.exists()


def test_legacy_updater_retirement_rejects_collision_and_unsafe_symlink(tmp_path):
    env, systemd_dir, state_dir = _legacy_retirement_fixture(tmp_path)
    archive = state_dir / "retired-units" / "seiche-update-v1"
    archive.mkdir(parents=True)
    (archive / "seiche-update.service").write_text("different\n")

    collision = _run_legacy_retirement(env)

    assert collision.returncode != 0
    assert "archive differs" in collision.stderr
    assert (systemd_dir / "seiche-update.service").is_file()

    (archive / "seiche-update.service").unlink()
    (systemd_dir / "seiche-update.service").unlink()
    (systemd_dir / "seiche-update.service").symlink_to(tmp_path / "unexpected")
    unsafe = _run_legacy_retirement(env)

    assert unsafe.returncode != 0
    assert "unexpected symlink" in unsafe.stderr


def test_forward_incident_runbook_loads_systemd_environment_without_sourcing():
    runbook = (ROOT / "docs" / "FORWARD_CHAIN_INCIDENT_2026-08-11.md").read_text()

    assert "mapfile -t MARKET_ENV" in runbook
    assert 'env "${MARKET_ENV[@]}"' in runbook
    assert "never `source` this file" in runbook
    assert ". /etc/seiche/market.env" not in runbook


def test_private_world_model_delivery_has_an_exact_least_privilege_seam():
    installer = (ROOT / "ops" / "deploy" / "install-market-platform.sh").read_text()
    relay_installer = WORLD_MODEL_DELIVERY_INSTALLER.read_text()
    caddy = CADDYFILE.read_text()
    delivery_docs = (ROOT / "ops" / "deploy" / "WORLD-MODEL-DELIVERY.md").read_text()
    route = "/api/internal/v1/world-model/us-usd-funding-core-v2"
    exact_file = "/var/lib/liquilens-world-model/export/us-usd-funding-core-v2.json"

    assert f"path {route}" in caddy
    private_edge = caddy[
        caddy.index("@world_model_delivery {") : caddy.index("@public {")
    ]
    assert 'header Cache-Control "no-store, no-transform"' in private_edge
    assert "reverse_proxy 127.0.0.1:8787" in private_edge
    assert "@world_model_delivery_non_get path" in private_edge
    assert 'respond "not here" 404' in private_edge
    public_edge = caddy[caddy.index("@public {") : caddy.index("@login {")]
    assert route not in public_edge
    assert route not in EXTERNAL_ROUTES.read_text()
    assert f"https://api.seiche.info{route}" in delivery_docs
    assert f"https://seiche.info{route}" not in delivery_docs

    assert "EnvironmentFile=-$DELIVERY_ENV_FILE" in installer
    assert exact_file in installer
    assert "liquilens-world-model-readers" in installer
    assert 'usermod -a -G "$DELIVERY_READER_GROUP" seiche' in installer
    assert 'runuser -u seiche -- test -r "$DELIVERY_PATH"' in installer
    assert exact_file in relay_installer
    assert "SEICHE_WORLD_MODEL_DELIVERY_BEARER_TOKEN=$TOKEN" in relay_installer
    assert "HARD_MAX_BYTES=5242880" in relay_installer
    assert "liquilens-world-model-readers" in relay_installer
    assert "/archive" not in relay_installer
    assert "/latest" not in relay_installer
    assert 'echo "$TOKEN"' not in relay_installer
    assert "setfacl" not in relay_installer


def test_release_health_capability_is_loopback_only():
    caddy = CADDYFILE.read_text()
    route = "/api/internal/v1/release-health"
    private_edge = caddy[caddy.index("@release_health path") : caddy.index("@public {")]

    assert f"@release_health path {route}" in private_edge
    assert 'respond "not here" 404' in private_edge
    assert "reverse_proxy" not in private_edge
    public_edge = caddy[caddy.index("@public {") : caddy.index("@login {")]
    assert route not in public_edge


def test_event_analysis_edge_is_post_only_and_excluded_from_public_get():
    caddy = CADDYFILE.read_text()
    route = "/api/event-analysis"
    public_matcher = caddy[caddy.index("@public {") : caddy.index("@event_analysis {")]
    event_handler = caddy[caddy.index("@event_analysis {") : caddy.index("@login {")]
    other_post_matcher = caddy[
        caddy.index("@login {") : caddy.index("handle @public {")
    ]

    assert "method GET HEAD" in public_matcher
    assert route not in public_matcher
    assert "method POST" in event_handler
    assert route in event_handler
    assert "request_body" in event_handler
    assert "max_size 8KiB" in event_handler
    assert "max_size 8KB" not in event_handler
    assert "import seiche_stateful_upstream" in event_handler
    assert "/api/auth/login" not in event_handler
    assert route not in other_post_matcher
    assert "/api/auth/login" in other_post_matcher
    assert caddy.count(route) == 1


def test_deploy_smoke_runs_private_delivery_contracts():
    update = BOX_UPDATE.read_text()
    workflow = (ROOT / ".github" / "workflows" / "market-platform-ci.yml").read_text()

    assert "tests/test_world_model_delivery.py" in update
    assert "backend/tests/test_world_model_delivery.py" in workflow


def test_deploy_smoke_runs_cache_only_health_contracts():
    update = BOX_UPDATE.read_text()

    assert "tests/test_api_caching.py" in update


def test_pull_unit_reads_the_api_cache_without_owning_snapshot_refresh():
    unit = PULL_UNIT.read_text()

    assert "Requires=seiche-api.service" in unit
    assert "After=network-online.target seiche-api.service" in unit
    assert "WorkingDirectory=/home/seiche/app/backend" in unit
    assert (
        "ExecStart=/home/seiche/app/backend/.venv/bin/seiche alert "
        "--api-url http://127.0.0.1:8787/api/overview "
        "--max-snapshot-age-seconds 3600"
    ) in unit
    assert "--force" not in unit
    assert "SuccessExitStatus=0 2" in unit
    assert "TimeoutStartSec=1200" in unit


def test_deploy_wrapper_converges_pull_unit_only_after_candidate_health():
    wrapper = DEPLOY_WRAPPER.read_text()
    readiness = wrapper[
        wrapper.index("parse_candidate_health()") : wrapper.index("market_health()")
    ]
    candidate_once = readiness[
        readiness.index("candidate_health_once()") : readiness.index(
            "candidate_health_wait()"
        )
    ]
    assert "/api/internal/v1/release-health" in readiness
    assert "require_rebuilt=true" not in candidate_once
    assert "/api/public" not in readiness
    assert 'set(candidate) != {"producer_sha", "activation_token"}' in readiness
    assert 'candidate.get("producer_sha") != expected_sha' in readiness
    assert 're.fullmatch(r"[0-9a-f]{64}"' in readiness
    assert 'sys.stdout.write(candidate["activation_token"])' in readiness
    function = wrapper[
        wrapper.index("deploy_pull_unit()") : wrapper.index("deploy_market_platform ||")
    ]

    assert "systemd-analyze verify" in function
    assert function.index('cp -p "$destination" "$previous"') < function.index(
        'mv -f "$candidate" "$destination"'
    )
    assert "daemon-reload rejected the pull unit; restoring" in function
    assert 'mv -f "$previous" "$destination"' in function
    assert "systemctl start seiche-pull" not in function
    assert "systemctl restart seiche-pull" not in function
    assert "for attempt in 1 2 3" in function
    assert 'candidate_health_once "$AFTER"' in function
    assert 'write_promotion_request "$AFTER" "$ACTIVATION_TOKEN"' in function
    assert 'systemctl start "$PROMOTION_UNIT"' in function
    assert "runuser -u seiche" not in function[function.index("POINT_OF_NO_RETURN") :]

    health = wrapper[
        wrapper.index('HEALTHY=""') : wrapper.index('if [ -n "$HEALTHY" ]')
    ]
    assert "if systemctl restart seiche-api; then" in health
    assert "RESTARTED=1" in health
    assert 'if [ -n "$RESTARTED" ] && systemctl is-active' in health
    assert health.index("systemctl restart seiche-api") < health.index(
        "candidate_health_wait"
    )
    assert health.index("market_health") < health.index("deploy_pull_unit")
    assert health.index("deploy_pull_unit") < health.index("promote_snapshot_handoff")
    assert health.index("promote_snapshot_handoff") < health.index("HEALTHY=1")
    already = wrapper[
        wrapper.index('if [ "$BEFORE" = "$AFTER" ] &&') : wrapper.index(
            'if [ "$BEFORE" = "$AFTER" ]; then'
        )
    ]
    assert "if ! systemctl is-active --quiet seiche-api; then" in already
    assert already.index("systemctl restart seiche-api") < already.index(
        'candidate_health_wait "$API_FULL_REBUILD_WAIT_SECONDS" "$AFTER"'
    )
    assert "without moving the checkout" in already
    assert "market writers remain stopped" in already
    assert 'candidate_health_wait "$API_FULL_REBUILD_WAIT_SECONDS" "$AFTER"' in already
    assert "market_health" in already
    assert "deploy_pull_unit" in already
    assert "promote_snapshot_handoff" in already
    assert already.index("candidate_health_wait") < already.index("market_health")
    assert already.index("market_health") < already.index("deploy_pull_unit")
    assert already.index("deploy_pull_unit") < already.index("promote_snapshot_handoff")
    promotion_failure = already[already.index("promote_snapshot_handoff ||") :]
    assert "restore_market_services" in promotion_failure
    assert "healthy running candidate kept in place" in promotion_failure
    assert "accepted release did not recover strict health" in already


@pytest.mark.parametrize(
    ("generated_at", "max_age", "accepted"),
    [
        ("2026-08-22T07:45:00+00:00", 900, True),
        ("2026-08-22T07:44:59+00:00", 900, False),
        ("2026-08-22T08:05:00+00:00", 900, True),
        ("2026-08-22T08:05:01+00:00", 900, False),
        ("2026-08-22T08:00:00", 900, False),
        ("not-a-timestamp", 900, False),
        (None, 0, True),
    ],
)
def test_candidate_health_parser_enforces_fresh_aware_generation_time(
    tmp_path: Path,
    generated_at: str | None,
    max_age: int,
    accepted: bool,
) -> None:
    wrapper = DEPLOY_WRAPPER.read_text()
    parser_start = wrapper.index("parse_candidate_health() {")
    parser = wrapper[
        parser_start : wrapper.index("ACTIVATION_TOKEN=", parser_start)
    ].replace('"$APP/backend/.venv/bin/python"', f'"{sys.executable}"')
    expected_sha = "a" * 40
    token = "b" * 64
    payload: dict[str, object] = {
        "release_candidate": {
            "producer_sha": expected_sha,
            "activation_token": token,
        }
    }
    if generated_at is not None:
        payload["generated_at"] = generated_at
    body = tmp_path / "health.json"
    body.write_text(json.dumps(payload))
    now = int(datetime(2026, 8, 22, 8, 0, tzinfo=UTC).timestamp())

    result = subprocess.run(
        [
            "bash",
            "-c",
            f'{parser}\nparse_candidate_health "$1" "$2" "$3" "$4"',
            "candidate-health-parser",
            str(body),
            expected_sha,
            str(max_age),
            str(now),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert (result.returncode == 0) is accepted, result.stderr
    assert result.stdout == (token if accepted else "")


def test_market_health_matches_the_candidate_registry_without_a_count_literal():
    wrapper = DEPLOY_WRAPPER.read_text()
    health = wrapper[
        wrapper.index("market_health()") : wrapper.index("promote_snapshot_handoff()")
    ]

    assert "from seiche.markets.registry import default_registry" in health
    assert "expected={pack.market_id for pack in default_registry().list()}" in health
    assert 'actual=[market["market_id"] for market in p["markets"]]' in health
    assert "len(actual) == len(expected) and set(actual) == expected" in health
    assert 'len(p["markets"]) ==' not in health


def test_market_health_grants_only_group_read_before_unprivileged_validation():
    wrapper = DEPLOY_WRAPPER.read_text()
    health = wrapper[
        wrapper.index("market_health()") : wrapper.index("promote_snapshot_handoff()")
    ]

    mktemp_at = health.index("body=$(mktemp)")
    curl_at = health.index("/api/v2/coverage")
    permission_at = health.index('if ! /usr/bin/chown root:seiche "$body"')
    validator_at = health.index('if ! "$RUNUSER" -u seiche -- /usr/bin/env -i')
    permission_failure = health[permission_at:validator_at]

    assert mktemp_at < curl_at < permission_at < validator_at
    assert permission_failure.index('chown root:seiche "$body"') < (
        permission_failure.index('chmod 0640 "$body"')
    )
    assert 'chmod 0644 "$body"' not in permission_failure
    assert "chmod 066" not in permission_failure
    assert 'rm -f -- "$body"' in permission_failure
    assert "return 1" in permission_failure


def test_snapshot_promotion_unit_and_installer_are_fixed_and_sandboxed():
    installer = MARKET_INSTALLER.read_text()
    unit = PROMOTION_UNIT.read_text()

    assert "Type=oneshot" in unit
    assert "User=seiche" in unit
    assert "Group=seiche" in unit
    assert "WorkingDirectory=/home/seiche/app/backend" in unit
    assert "EnvironmentFile=/etc/seiche/market.env" in unit
    assert "EnvironmentFile=/etc/seiche/release.env" in unit
    assert "EnvironmentFile=-/etc/seiche/market.env" not in unit
    assert "EnvironmentFile=-/etc/seiche/release.env" not in unit
    assert (
        "ExecStart=/home/seiche/app/backend/.venv/bin/python -m seiche.release_promote"
    ) in unit
    assert (
        "ExecStopPost=+/usr/bin/rm -f /run/seiche-release/promotion-request.json"
    ) in unit
    assert "CapabilityBoundingSet=" in unit
    assert "MemoryMax=1G" in unit
    assert "TasksMax=64" in unit
    assert "OnFailure=undertow-failure-alert@%n.service" in unit
    assert "ProtectSystem=strict" in unit
    assert "RestrictAddressFamilies=AF_UNIX" in unit
    assert unit.count("ReadWritePaths=") == 1
    assert "ReadWritePaths=/home/seiche/app/backend/data" in unit

    assert 'install -d -o root -g seiche -m 0750 "$PROMOTION_REQUEST_DIR"' in installer
    assert 'install -d -o root -g root -m 0700 "$DEPLOY_STATE_DIR"' in installer
    assert "coreutils" in installer
    assert 'dpkg --compare-versions "$SYNC_VERSION" ge 8.24' in installer
    assert "systemd-analyze verify" in installer
    assert "seiche-snapshot-promote.service" in installer
    assert (
        'mv -f "$PROMOTION_UNIT_STAGE_DIR/seiche-snapshot-promote.service"' in installer
    )
    assert "systemctl enable seiche-snapshot-promote.service" not in installer
    api_dropin = installer[installer.index('cat >"$DROPIN"') :]
    assert "EnvironmentFile=-$ENV_DIR/release.env" in api_dropin


def test_deploy_controller_writes_only_atomic_root_owned_fixed_requests():
    wrapper = DEPLOY_WRAPPER.read_text()
    release_writer = wrapper[
        wrapper.index("write_release_env()") : wrapper.index(
            "write_promotion_request()"
        )
    ]
    request_writer = wrapper[
        wrapper.index("write_promotion_request()") : wrapper.index("# The sha whose")
    ]

    assert "^ [0-9a-f]" not in release_writer
    assert "^[0-9a-f]{40}$" in wrapper
    assert "^[0-9a-f]{64}$" in wrapper
    assert "printf 'SEICHE_RELEASE_SHA=%s\\n'" in release_writer
    assert "chown root:seiche" in release_writer
    assert "chmod 0640" in release_writer
    assert 'mv -f "$stage" "$RELEASE_ENV"' in release_writer
    assert (
        'printf \'{"expected_sha":"%s","activation_token":"%s"}\\n\'' in request_writer
    )
    assert "chown root:seiche" in request_writer
    assert "chmod 0640" in request_writer
    assert 'mv -f "$stage" "$PROMOTION_REQUEST"' in request_writer
    assert "/etc/seiche/market.env" not in wrapper
    assert "source /etc/seiche" not in wrapper
    assert "eval " not in wrapper
    assert 'git -C "$APP" diff-index --quiet "$AFTER" --' in wrapper
    assert "--others --exclude-standard -- backend" in wrapper
    assert "--others --ignored --exclude-standard -- backend" in wrapper
    assert "$0 !~ /^backend\\/\\.venv\\//" in wrapper
    assert "$0 !~ /\\/__pycache__\\//" in wrapper
    assert (
        'if ! AFTER=$("$RUNUSER" -u seiche -- git -C "$APP" rev-parse HEAD)' in wrapper
    )
    unresolved = wrapper[
        wrapper.index('if ! AFTER=$("$RUNUSER"') : wrapper.index(
            'if [ "$AFTER" != "$TARGET" ]'
        )
    ]
    assert "restore_pre_restart_services" in unresolved
    assert "STATE=$DEPLOY_STATE_DIR/deployed-sha" in wrapper
    assert 'install -d -o root -g root -m 0700 "$DEPLOY_STATE_DIR"' in wrapper
    assert "root:root:700" in wrapper
    assert "root:root:600" in wrapper
    assert 'mktemp "$DEPLOY_STATE_DIR/.deployed-sha.XXXXXX"' in wrapper
    assert 'mv -f "$stage" "$STATE"' in wrapper
    assert '/usr/bin/sync -f "$stage"' in wrapper
    assert '/usr/bin/sync "$DEPLOY_STATE_DIR"' in wrapper
    assert "DEPLOYED_STATE_RENAMED=1" in wrapper
    state_writer = wrapper[
        wrapper.index("write_deployed_state()") : wrapper.index("write_release_env()")
    ]
    assert (
        state_writer.index('/usr/bin/sync -f "$stage"')
        < state_writer.index('mv -f "$stage" "$STATE"')
        < state_writer.index('/usr/bin/sync "$DEPLOY_STATE_DIR"')
    )
    assert 'SEICHE_DEPLOYED_SHA="$DEPLOYED"' in wrapper
    assert "/home/seiche/.seiche-deployed-sha" not in wrapper
    assert "DEPLOYED=${SEICHE_DEPLOYED_SHA:-}" in BOX_UPDATE.read_text()
    deploy_lock = wrapper.index("flock --nonblock 9")
    assert "DEPLOY_RUNTIME_DIR=/run/seiche-deploy" in wrapper[:deploy_lock]
    assert (
        'install -d -o root -g root -m 0700 "$DEPLOY_RUNTIME_DIR"'
        in wrapper[:deploy_lock]
    )
    assert 'exec 9>"$DEPLOY_LOCK"' in wrapper[:deploy_lock]
    assert "another seiche deployment is still running" in wrapper
    assert deploy_lock < wrapper.index("# The sha whose code is actually RUNNING")


def test_deploy_controller_pins_a_locally_tested_target_before_quiescing():
    wrapper = DEPLOY_WRAPPER.read_text()
    resolved = wrapper.index(
        'LATEST=$("$RUNUSER" -u seiche -- git -C "$APP" rev-parse origin/main)'
    )
    constrained = wrapper.index("EXPECTED_TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}")
    stopped = wrapper.index(
        "systemctl stop seiche-market-worker.service seiche-market-backfill.service"
    )
    checked = wrapper[constrained:stopped]

    assert resolved < constrained < stopped
    assert 'valid_release_sha "$EXPECTED_TARGET"' in checked
    assert '"$EXPECTED_TARGET" "$LATEST"' in checked
    assert "reviewed target is not a fetched commit on main" in checked
    assert "TARGET=$EXPECTED_TARGET" in checked
    assert "SSH_ORIGINAL_COMMAND" in checked
    assert "exit 1" in checked


def test_deploy_requires_a_stable_quiet_host_before_quiescing_services():
    wrapper = DEPLOY_WRAPPER.read_text()
    helper_start = wrapper.index("admit_shared_host() {")
    helper_end = wrapper.index("write_deployed_state()", helper_start)
    helper = wrapper[helper_start:helper_end]
    target = wrapper.index("TARGET=$LATEST", helper_end)
    admission = wrapper.index("if ! admit_shared_host; then", target)
    capture = wrapper.index('MARKET_WORKER_WAS_ACTIVE=""', admission)
    stop = wrapper.index(
        "systemctl stop seiche-market-worker.service seiche-market-backfill.service",
        capture,
    )

    for marker in (
        "/usr/bin/getconf _NPROCESSORS_ONLN",
        "cpus * 0.75",
        "sample <= 3",
        "</proc/loadavg",
        "load_five",
        '-v observed="$load_one"',
        '-v observed="$load_five"',
        "observed <= limit",
        "sleep 10",
        "production unchanged",
    ):
        assert marker in helper
    assert "SEICHE_DEPLOY_MAX" not in helper
    assert "one-minute load" in helper
    assert "five-minute load" in helper
    assert target < admission < capture < stop
    assert "exit 75" in wrapper[admission:capture]
    admission_case = wrapper.index(
        'case "${SEICHE_DEPLOY_ADMISSION_ONLY:-0}" in', helper_start
    )
    admission_only = wrapper[
        admission_case : wrapper.index('DEPLOYED_STATE_RENAMED=""', helper_end)
    ]
    assert "SEICHE_DEPLOY_ADMISSION_ONLY" in admission_only
    assert "forced deploy cannot request admission-only mode" in admission_only
    forced_request_rejected = admission_only.index(
        "forced deploy cannot request admission-only mode"
    )
    admission_call = admission_only.index("if admit_shared_host; then")
    admitted = admission_only.index("exit 0", admission_call)
    deferred = admission_only.index("exit 75", admitted)
    assert forced_request_rejected < admission_call < admitted < deferred

    comparator = "BEGIN { exit !(observed <= limit) }"
    for observed, expected in (("11.99", 0), ("12.00", 0), ("12.01", 1)):
        result = subprocess.run(
            [
                "/usr/bin/awk",
                "-v",
                f"observed={observed}",
                "-v",
                "limit=12.00",
                comparator,
            ],
            check=False,
        )
        assert result.returncode == expected


def test_release_poller_prefers_remote_gate_and_retains_local_break_glass():
    poller = RELEASE_POLLER.read_text()
    wrapper_handoff = poller[
        poller.index("run_deploy_wrapper() {") : poller.index(
            "wait_for_post_gate_admission() {"
        )
    ]
    selected = poller.index(
        'TARGET=$(as_service git -C "$APP_DIR" rev-parse origin/main)'
    )
    inert_content = poller.index(
        'if is_inert_automation_content_commit "$TARGET"', selected
    )
    signature = poller.index('verify_target_signature "$TARGET"', inert_content)
    receipt_pair = poller.index("receipt_pair_status", signature)
    admission = poller.index("run_deploy_wrapper admission", receipt_pair)
    detached = poller.index(
        'as_service git -C "$APP_DIR" worktree add --detach "$CANDIDATE_DIR" "$TARGET"'
    )
    full_gate = poller.index('"$VENV/bin/python" -m pytest backend/tests -q', detached)
    remote_gate = poller.index('install_remote_gate_receipt "$GATE_RECEIPT"', full_gate)
    refetched = poller.index(
        'as_service git -C "$APP_DIR" fetch -q origin main', remote_gate
    )
    superseded = poller.index('if [ "$LATEST" != "$TARGET" ]', refetched)
    gate_receipt = poller.index('write_receipt gate "$GATE_RECEIPT"', superseded)
    gate_digest = poller.index('GATE_DIGEST=$("$SHA256SUM"', superseded)
    gate_only = poller.index('if [ "$GATE_ONLY" = 1 ]', gate_digest)
    post_gate_admission = poller.index("wait_for_post_gate_admission", gate_only)
    post_gate_refetch = poller.index(
        'as_service git -C "$APP_DIR" fetch -q origin main', post_gate_admission
    )
    post_gate_superseded = poller.index(
        'if [ "$LATEST" != "$TARGET" ]', post_gate_refetch
    )
    timer_activation = poller.index(
        "activate_release_timer_for_deploy", post_gate_superseded
    )
    deploy_status = poller.index("DEPLOY_STATUS=0", timer_activation)
    handoff_started = poller.index("DEPLOY_WRAPPER_HANDOFF_STARTED=1", deploy_status)
    deployed = poller.index('run_deploy_wrapper deploy "$TARGET"', handoff_started)

    assert (
        selected
        < inert_content
        < signature
        < receipt_pair
        < admission
        < detached
        < full_gate
        < remote_gate
        < refetched
        < superseded
        < gate_receipt
        < gate_digest
        < gate_only
        < post_gate_admission
        < post_gate_refetch
        < post_gate_superseded
        < timer_activation
        < deploy_status
        < handoff_started
        < deployed
    )
    assert (
        'LOCAL_GATE_BREAK_GLASS="${SEICHE_CONTROL_LOCAL_GATE_BREAK_GLASS:-0}"' in poller
    )
    assert 'if [ "$LOCAL_GATE_BREAK_GLASS" = 1 ]; then' in poller
    assert "local gate was not run automatically" in poller
    assert 'validate_gate_provider "$GATE_RECEIPT" railway' in poller
    assert "seiche.release-receipt.v2" in poller
    assert "gate_provider" in poller
    assert 'CANDIDATE_PARENT="$STATE_DIR/candidates"' in poller
    assert 'install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700' in poller
    assert 'exec 8>"$CONTROL_LOCK"' in poller
    assert "flock --nonblock 8" in poller
    assert "ADMISSION_STATUS=0" in poller[signature:detached]
    assert 'case "$ADMISSION_STATUS"' in poller[signature:detached]
    assert "deferred with production unchanged" in poller[signature:detached]
    assert wrapper_handoff.count("/usr/bin/env -i") == 2
    assert wrapper_handoff.count("HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin") == 2
    assert wrapper_handoff.count('/usr/bin/bash -p "$DEPLOY_WRAPPER"') == 2
    assert "SEICHE_DEPLOY_ADMISSION_ONLY=1" in wrapper_handoff
    assert 'SEICHE_EXPECTED_TARGET_SHA="$target"' in wrapper_handoff
    assert '"$CANDIDATE_DIR/backend[dev,collectors]"' in poller
    gate_slice = poller[detached:gate_receipt]
    assert gate_slice.count("run_candidate_gate_stage") == 3
    assert "-o faulthandler_timeout=300" in gate_slice
    assert "--pystack-threshold" not in gate_slice
    assert "EnvironmentFile" not in gate_slice
    assert "EnvironmentFile" not in poller[remote_gate:refetched]
    monitor = poller[
        poller.index("resolve_advertised_main() {") : poller.index(
            "is_inert_automation_content_commit() {"
        )
    ]
    assert "ls-remote --exit-code --refs" in monitor
    assert "refs/heads/main" in monitor
    assert "os.setsid()" in monitor
    assert 'gate_process_group_is_ready "$GATE_PROCESS_PID"' in monitor
    assert '"$KILL" -TERM -- "-$pid"' in monitor
    assert '"$KILL" -KILL -- "-$pid"' in monitor
    assert "pkill" not in monitor
    assert "killall" not in monitor
    assert "production unchanged" in poller[superseded:gate_receipt]
    assert "gate-only success" in poller[gate_only:deployed]
    post_gate_slice = poller[post_gate_admission:deploy_status]
    assert "POST_GATE_ADMISSION_STATUS" in post_gate_slice
    assert "bounded post-gate wait" in post_gate_slice
    assert "after post-gate admission" in post_gate_slice
    assert "during post-gate admission" in post_gate_slice
    assert "production unchanged" in post_gate_slice
    after_deploy = poller[deploy_status:]
    assert "DEPLOY_STATUS=0" in after_deploy
    assert 'case "$DEPLOY_STATUS"' in after_deploy
    assert "shared host became busy" in after_deploy
    assert "TARGET_DURABLY_DEPLOYED=1" in after_deploy
    assert "release_timer_is_ready" in after_deploy
    early_exit = poller[
        poller.index('if [ "$GATE_ONLY" != 1 ]') : poller.index(
            'CANDIDATE_TREE="$TARGET_TREE"'
        )
    ]
    assert '[ "$RECEIPT_PAIR_STATUS" = 0 ]' in early_exit
    assert "live, strictly healthy, and recovery sealed" in early_exit
    assert (
        "live cutover is complete; recovery sealing continues asynchronously"
        in early_exit
    )
    assert "queue_recovery_seal" in early_exit
    assert "existing recovery receipt evidence is invalid" in early_exit
    receipt_decision = poller[receipt_pair:admission]
    assert "0|1) ;;" in receipt_decision
    assert "existing release receipt evidence is invalid" in receipt_decision
    cleanup = poller[poller.index("cleanup() {") : poller.index("# Regression tests")]
    assert "restore_release_timer_state" in cleanup
    assert cleanup.index("load_deployed_state") < cleanup.index(
        "restore_release_timer_state"
    )
    assert '[ "$DEPLOY_WRAPPER_HANDOFF_STARTED" = 1 ]' in cleanup
    assert '[ "${DEPLOYED:-}" != "${TARGET:-}" ]' in cleanup
    assert 'DEPLOYED_STATE_VALUE" = "$TARGET' in cleanup


def _release_gate_monitor_environment(tmp_path: Path) -> dict[str, str]:
    runuser = _executable(
        tmp_path / "runuser",
        'if [ "$1" = -u ]; then shift 2; fi\n'
        'if [ "${1:-}" = -- ]; then shift; fi\n'
        'exec "$@"\n',
    )
    return os.environ | {
        "SEICHE_CONTROL_LIBRARY_ONLY": "1",
        "SEICHE_CONTROL_RUNUSER": str(runuser),
        "SEICHE_CONTROL_USER": "release-test",
        "SEICHE_CONTROL_PYTHON": sys.executable,
        "SEICHE_CONTROL_SLEEP": "/bin/sleep",
        "SEICHE_CONTROL_PS": "/bin/ps",
        "SEICHE_CONTROL_KILL": "/bin/kill",
        "SEICHE_CONTROL_SUPERSESSION_POLL_SECONDS": "1",
    }


def test_release_gate_aborts_only_its_process_group_when_main_advances(tmp_path):
    target = "a" * 40
    successor = "b" * 40
    pid_file = tmp_path / "gate.pids"
    completed = tmp_path / "gate.completed"
    gate = _executable(
        tmp_path / "slow-gate",
        "trap 'exit 143' TERM\n"
        "/bin/sleep 30 &\n"
        "child=$!\n"
        'printf \'%s %s\\n\' "$$" "$child" >"$GATE_PID_FILE"\n'
        'wait "$child"\n'
        'printf complete >"$GATE_COMPLETED_FILE"\n',
    )
    command = r"""
source "$1"
checks=0
resolve_advertised_main() {
  checks=$((checks + 1))
  if [ "$checks" -eq 1 ]; then
    REMOTE_MAIN_SHA=$TARGET
  else
    REMOTE_MAIN_SHA=$SUCCESSOR
  fi
}
status=0
run_monitored_candidate_step "$2" || status=$?
printf '%s\n' "$GATE_SUPERSEDED_SHA"
exit "$status"
"""
    result = subprocess.run(
        ["bash", "-c", command, "seiche-gate-monitor", str(RELEASE_POLLER), str(gate)],
        env=_release_gate_monitor_environment(tmp_path)
        | {
            "TARGET": target,
            "SUCCESSOR": successor,
            "GATE_PID_FILE": str(pid_file),
            "GATE_COMPLETED_FILE": str(completed),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=8,
    )

    assert result.returncode == 75, result.stdout + result.stderr
    assert result.stdout.strip() == successor
    assert pid_file.is_file()
    assert not completed.exists()
    for pid in pid_file.read_text(encoding="ascii").split():
        observed = subprocess.run(
            ["/bin/ps", "-p", pid, "-o", "stat="],
            text=True,
            capture_output=True,
            check=False,
        )
        assert not observed.stdout.strip() or observed.stdout.lstrip().startswith("Z")


def test_release_gate_completes_when_advertised_main_stays_exact(tmp_path):
    target = "c" * 40
    command = r"""
source "$1"
resolve_advertised_main() { REMOTE_MAIN_SHA=$TARGET; }
run_monitored_candidate_step /bin/bash -c 'exit 0'
"""
    result = subprocess.run(
        ["bash", "-c", command, "seiche-gate-monitor", str(RELEASE_POLLER)],
        env=_release_gate_monitor_environment(tmp_path) | {"TARGET": target},
        text=True,
        capture_output=True,
        check=False,
        timeout=8,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def _post_gate_admission(
    tmp_path: Path,
    wrapper_body: str,
    *,
    wait_seconds: int,
    sleep: Path | None = None,
    ambient: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    wrapper = _executable(tmp_path / "admission-wrapper", wrapper_body)
    local_bash = shutil.which("bash")
    assert local_bash is not None
    portable_poller = tmp_path / "portable-release-poll.sh"
    portable_poller.write_text(
        RELEASE_POLLER.read_text(encoding="utf-8").replace("/usr/bin/bash", local_bash),
        encoding="utf-8",
    )
    env = os.environ | {
        "SEICHE_CONTROL_LIBRARY_ONLY": "1",
        "SEICHE_CONTROL_DEPLOY_WRAPPER": str(wrapper),
        "SEICHE_CONTROL_ADMISSION_WAIT_SECONDS": str(wait_seconds),
        "SEICHE_CONTROL_ADMISSION_RETRY_SECONDS": "1",
    }
    if sleep is not None:
        env["SEICHE_CONTROL_SLEEP"] = str(sleep)
    if ambient is not None:
        env |= ambient
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; wait_for_post_gate_admission',
            "seiche-admission-test",
            str(portable_poller),
        ],
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )


def test_post_gate_admission_retries_a_safe_deferral(tmp_path):
    counter = tmp_path / "counter"
    counter.write_text("0\n")
    true = Path(shutil.which("true") or "/usr/bin/true")
    result = _post_gate_admission(
        tmp_path,
        (
            f'count=$(cat "{counter}")\n'
            "count=$((count + 1))\n"
            f'printf "%s\\n" "$count" >"{counter}"\n'
            '[ "$count" -gt 1 ] || exit 75\n'
        ),
        wait_seconds=10,
        sleep=true,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert counter.read_text() == "2\n"
    assert "retrying admission" in result.stdout


def test_post_gate_admission_handoff_drops_ambient_deploy_controls(tmp_path: Path):
    observed = tmp_path / "wrapper-environment"
    result = _post_gate_admission(
        tmp_path,
        (
            f'/usr/bin/env >"{observed}"\n'
            '[ "${SEICHE_DEPLOY_ADMISSION_ONLY:-}" = 1 ]\n'
            '[ -z "${SEICHE_EXPECTED_TARGET_SHA:-}" ]\n'
            '[ "$HOME:$LANG:$LC_ALL:$PATH" = '
            '"/root:C:C:/usr/bin:/bin" ]\n'
        ),
        wait_seconds=0,
        ambient={
            "SEICHE_DEPLOY_ADMISSION_ONLY": "0",
            "SEICHE_EXPECTED_TARGET_SHA": "b" * 40,
            "SEICHE_DEPLOY_BOOTSTRAP_ASSETS_ONLY": "1",
            "PYTHONPATH": str(tmp_path / "hostile-pythonpath"),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    child_environment = observed.read_text(encoding="utf-8")
    assert "SEICHE_DEPLOY_ADMISSION_ONLY=1\n" in child_environment
    for name in (
        "PYTHONPATH",
        "SEICHE_DEPLOY_BOOTSTRAP_ASSETS_ONLY",
        "SEICHE_EXPECTED_TARGET_SHA",
    ):
        assert f"{name}=" not in child_environment


@pytest.mark.parametrize("wrapper_status", [1, 42])
def test_post_gate_admission_preserves_real_probe_failures(tmp_path, wrapper_status):
    result = _post_gate_admission(
        tmp_path,
        f"exit {wrapper_status}\n",
        wait_seconds=0,
    )

    assert result.returncode == wrapper_status


def test_post_gate_admission_returns_deferred_at_its_bound(tmp_path):
    result = _post_gate_admission(
        tmp_path,
        "exit 75\n",
        wait_seconds=0,
    )

    assert result.returncode == 75


def _release_receipt_pair(
    tmp_path: Path,
    *,
    tamper: str | None = None,
) -> tuple[Path, Path, str, str]:
    commit = "a" * 40
    tree = "b" * 40
    started = "2026-08-22T01:02:03Z"
    completed = "2026-08-22T01:03:04Z"
    gate_payload = {
        "schema": "seiche.release-receipt.v1",
        "kind": "gate",
        "commit": commit,
        "tree": tree,
        "started_at": started,
        "completed_at": completed,
        "conclusion": "success",
        "install_command": "python -m pip install -q -e ./backend[dev,collectors]",
        "test_command": (
            "python -m pytest backend/tests -q --memray -o faulthandler_timeout=300"
        ),
    }
    if tamper == "commit":
        gate_payload["commit"] = "c" * 40
    elif tamper == "tree":
        gate_payload["tree"] = "d" * 40
    elif tamper == "install_command":
        gate_payload["install_command"] = "python -m pip install unreviewed"
    elif tamper == "test_command":
        gate_payload["test_command"] = "python -m pytest -q"

    gate = tmp_path / f"{commit}.gate.json"
    gate.write_text(
        json.dumps(gate_payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    gate.chmod(0o400)
    gate_digest = hashlib.sha256(gate.read_bytes()).hexdigest()
    release_payload = {
        "schema": "seiche.release-receipt.v1",
        "kind": "release",
        "commit": commit,
        "tree": tree,
        "started_at": started,
        "completed_at": completed,
        "conclusion": "success",
        "gate_receipt_sha256": gate_digest,
    }
    if tamper == "gate_digest":
        release_payload["gate_receipt_sha256"] = "e" * 64
    release = tmp_path / f"{commit}.release.json"
    release.write_text(
        json.dumps(release_payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    release.chmod(0o400)
    if tamper == "unsafe_mode":
        gate.chmod(0o600)
    elif tamper == "extra_link":
        os.link(gate, tmp_path / "gate-alias.json")
    return gate, release, commit, tree


def _receipt_pair_result(
    gate: Path,
    release: Path,
    commit: str,
    tree: str,
) -> subprocess.CompletedProcess[str]:
    sha256sum = shutil.which("sha256sum")
    if sha256sum is None:
        pytest.skip("sha256sum is required for the release-receipt contract")
    env = os.environ | {
        "SEICHE_CONTROL_LIBRARY_ONLY": "1",
        "SEICHE_CONTROL_PYTHON": sys.executable,
        "SEICHE_CONTROL_SHA256SUM": sha256sum,
        "SEICHE_CONTROL_RECEIPT_UID": str(os.getuid()),
        "SEICHE_CONTROL_RECEIPT_GID": str(os.getgid()),
        "SEICHE_CONTROL_RECEIPT_MODE": "400",
    }
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; receipt_pair_status "$2" "$3" "$4" "$5"',
            "seiche-receipt-test",
            str(RELEASE_POLLER),
            commit,
            tree,
            str(gate),
            str(release),
        ],
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )


def test_complete_release_receipt_pair_is_accepted(tmp_path):
    gate, release, commit, tree = _release_receipt_pair(tmp_path)

    result = _receipt_pair_result(gate, release, commit, tree)

    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_release_receipt_requests_full_gate_convergence(tmp_path):
    gate, release, commit, tree = _release_receipt_pair(tmp_path)
    release.unlink()

    result = _receipt_pair_result(gate, release, commit, tree)

    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize(
    "tamper",
    (
        "commit",
        "tree",
        "install_command",
        "test_command",
        "gate_digest",
        "unsafe_mode",
        "extra_link",
    ),
)
def test_invalid_existing_release_receipt_evidence_fails_closed(tmp_path, tamper):
    gate, release, commit, tree = _release_receipt_pair(tmp_path, tamper=tamper)

    result = _receipt_pair_result(gate, release, commit, tree)

    assert result.returncode == 2, result.stdout + result.stderr


def _release_recovery_receipt(
    tmp_path: Path,
    *,
    tamper: str | None = None,
) -> tuple[Path, Path, str, str]:
    commit = "a" * 40
    tree = "b" * 40
    release_payload = {
        "schema": "seiche.release-receipt.v3",
        "kind": "release",
        "commit": commit,
        "tree": tree,
        "started_at": "2026-08-22T01:02:03Z",
        "completed_at": "2026-08-22T01:03:04Z",
        "conclusion": "success",
        "gate_receipt_sha256": "c" * 64,
        "snapshot_receipt_sha256": "d" * 64,
    }
    release = tmp_path / f"{commit}.release.json"
    release.write_text(
        json.dumps(release_payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    release.chmod(0o400)
    recovery_payload = {
        "schema": "seiche.release-recovery-receipt.v1",
        "kind": "recovery",
        "commit": commit,
        "tree": tree,
        "release_receipt_sha256": hashlib.sha256(release.read_bytes()).hexdigest(),
        "backup_snapshot": "20260822T010400Z",
        "backup_inventory_sha256": "e" * 64,
        "restore_checked_at": "2026-08-22T01:05:06Z",
        "restore_receipt_sha256": "f" * 64,
        "worker_startup": "ready",
        "data_readiness": "ready",
        "offsite_schedule": "active",
        "completed_at": "2026-08-22T01:06:07Z",
        "conclusion": "success",
    }
    if tamper == "commit":
        recovery_payload["commit"] = "9" * 40
    elif tamper == "tree":
        recovery_payload["tree"] = "8" * 40
    elif tamper == "release_digest":
        recovery_payload["release_receipt_sha256"] = "7" * 64
    elif tamper == "worker_startup":
        recovery_payload["worker_startup"] = "pending"
    elif tamper == "completed_before_release":
        recovery_payload["completed_at"] = "2026-08-22T01:01:01Z"
    elif tamper == "restore_after_completion":
        recovery_payload["restore_checked_at"] = "2026-08-22T01:07:08Z"
    elif tamper == "backup_after_restore":
        recovery_payload["backup_snapshot"] = "20260822T010607Z"

    recovery = tmp_path / f"{commit}.recovery.json"
    recovery_body = json.dumps(
        recovery_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    if tamper != "noncanonical":
        recovery_body += "\n"
    recovery.write_text(recovery_body, encoding="utf-8")
    recovery.chmod(0o400)
    if tamper == "unsafe_mode":
        recovery.chmod(0o600)
    elif tamper == "extra_link":
        os.link(recovery, tmp_path / "recovery-alias.json")
    return release, recovery, commit, tree


def _recovery_receipt_result(
    release: Path,
    recovery: Path,
    commit: str,
    tree: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ | {
        "SEICHE_CONTROL_LIBRARY_ONLY": "1",
        "SEICHE_CONTROL_PYTHON": sys.executable,
        "SEICHE_CONTROL_RECEIPT_UID": str(os.getuid()),
        "SEICHE_CONTROL_RECEIPT_GID": str(os.getgid()),
        "SEICHE_CONTROL_RECEIPT_MODE": "400",
    }
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; recovery_receipt_status "$2" "$3" "$4" "$5"',
            "seiche-recovery-receipt-test",
            str(RELEASE_POLLER),
            str(recovery),
            str(release),
            commit,
            tree,
        ],
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )


def test_complete_release_recovery_receipt_is_accepted(tmp_path):
    release, recovery, commit, tree = _release_recovery_receipt(tmp_path)

    result = _recovery_receipt_result(release, recovery, commit, tree)

    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_release_recovery_receipt_reports_pending(tmp_path):
    release, recovery, commit, tree = _release_recovery_receipt(tmp_path)
    recovery.unlink()

    result = _recovery_receipt_result(release, recovery, commit, tree)

    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize(
    "tamper",
    (
        "commit",
        "tree",
        "release_digest",
        "worker_startup",
        "completed_before_release",
        "restore_after_completion",
        "backup_after_restore",
        "noncanonical",
        "unsafe_mode",
        "extra_link",
    ),
)
def test_invalid_existing_recovery_receipt_evidence_fails_closed(tmp_path, tamper):
    release, recovery, commit, tree = _release_recovery_receipt(
        tmp_path,
        tamper=tamper,
    )

    result = _recovery_receipt_result(release, recovery, commit, tree)

    assert result.returncode == 2, result.stdout + result.stderr


def _fake_release_timer_systemctl(tmp_path: Path, *, enabled: bool, active: bool):
    state = tmp_path / "timer-state"
    state.mkdir()
    (state / "enabled").write_text(f"{int(enabled)}\n", encoding="ascii")
    (state / "active").write_text(f"{int(active)}\n", encoding="ascii")
    systemctl = _executable(
        tmp_path / "systemctl",
        "state=${FAKE_TIMER_STATE:?}\n"
        "command=${1:?}\n"
        "shift\n"
        'printf "%s %s\\n" "$command" "$*" >>"$state/calls"\n'
        'if [ "${FAKE_TIMER_QUERY_ERROR:-0}" = 1 ] && [ "$command" = show ]; then\n'
        "  exit 69\n"
        "fi\n"
        'if [ -n "${FAKE_TIMER_FAIL_COMMAND:-}" ] '
        '&& [ "$command" = "$FAKE_TIMER_FAIL_COMMAND" ]; then\n'
        "  exit 70\n"
        "fi\n"
        'case "$command" in\n'
        "  show)\n"
        '    case "$*" in\n'
        '      "--property=UnitFileState --value "*)\n'
        '        if [ "$(cat "$state/enabled")" = 1 ]; then '
        "echo enabled; else echo disabled; fi ;;\n"
        '      "--property=ActiveState --value "*)\n'
        '        if [ "$(cat "$state/active")" = 1 ]; then '
        "echo active; else echo inactive; fi ;;\n"
        "      *) exit 65 ;;\n"
        "    esac ;;\n"
        '  is-enabled) [ "$(cat "$state/enabled")" = 1 ] ;;\n'
        '  is-active) [ "$(cat "$state/active")" = 1 ] ;;\n'
        '  enable) printf "1\\n" >"$state/enabled" ;;\n'
        '  disable) printf "0\\n" >"$state/enabled" ;;\n'
        '  start) printf "1\\n" >"$state/active" ;;\n'
        '  stop) printf "0\\n" >"$state/active" ;;\n'
        "  *) exit 64 ;;\n"
        "esac\n",
    )
    return state, systemctl


@pytest.mark.parametrize(
    ("enabled", "active"),
    ((False, False), (True, False), (False, True), (True, True)),
)
def test_release_timer_activation_and_restoration_preserve_prior_state(
    tmp_path, enabled, active
):
    state, systemctl = _fake_release_timer_systemctl(
        tmp_path, enabled=enabled, active=active
    )
    env = os.environ | {
        "SEICHE_CONTROL_LIBRARY_ONLY": "1",
        "SEICHE_CONTROL_SYSTEMCTL": str(systemctl),
        "FAKE_TIMER_STATE": str(state),
    }

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; activate_release_timer_for_deploy; '
            "release_timer_is_ready; restore_release_timer_state",
            "seiche-timer-test",
            str(RELEASE_POLLER),
        ],
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "enabled").read_text(encoding="ascii") == f"{int(enabled)}\n"
    assert (state / "active").read_text(encoding="ascii") == f"{int(active)}\n"
    calls = (state / "calls").read_text(encoding="utf-8").splitlines()
    capture_enabled = calls.index(
        "show --property=UnitFileState --value seiche-release-poll.timer"
    )
    capture_active = calls.index(
        "show --property=ActiveState --value seiche-release-poll.timer"
    )
    enable_timer = calls.index("enable seiche-release-poll.timer")
    start_timer = calls.index("start seiche-release-poll.timer")
    assert capture_enabled < capture_active < enable_timer < start_timer


def test_durable_deployment_keeps_release_timer_active_for_recovery(tmp_path):
    state, systemctl = _fake_release_timer_systemctl(
        tmp_path, enabled=False, active=False
    )
    env = os.environ | {
        "SEICHE_CONTROL_LIBRARY_ONLY": "1",
        "SEICHE_CONTROL_SYSTEMCTL": str(systemctl),
        "FAKE_TIMER_STATE": str(state),
    }

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; activate_release_timer_for_deploy; '
            'printf "0\\n" >"$FAKE_TIMER_STATE/enabled"; '
            'printf "0\\n" >"$FAKE_TIMER_STATE/active"; '
            "TARGET_DURABLY_DEPLOYED=1; restore_release_timer_state",
            "seiche-timer-test",
            str(RELEASE_POLLER),
        ],
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "enabled").read_text(encoding="ascii") == "1\n"
    assert (state / "active").read_text(encoding="ascii") == "1\n"


def test_indeterminate_release_timer_state_fails_before_mutation(tmp_path):
    state, systemctl = _fake_release_timer_systemctl(
        tmp_path, enabled=True, active=True
    )
    env = os.environ | {
        "SEICHE_CONTROL_LIBRARY_ONLY": "1",
        "SEICHE_CONTROL_SYSTEMCTL": str(systemctl),
        "FAKE_TIMER_STATE": str(state),
        "FAKE_TIMER_QUERY_ERROR": "1",
    }

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; activate_release_timer_for_deploy',
            "seiche-timer-test",
            str(RELEASE_POLLER),
        ],
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert (state / "enabled").read_text(encoding="ascii") == "1\n"
    assert (state / "active").read_text(encoding="ascii") == "1\n"
    calls = (state / "calls").read_text(encoding="utf-8")
    assert "enable seiche-release-poll.timer" not in calls
    assert "start seiche-release-poll.timer" not in calls


@pytest.mark.parametrize("failed_command", ("enable", "start"))
def test_partial_release_timer_activation_restores_prior_state(
    tmp_path, failed_command
):
    state, systemctl = _fake_release_timer_systemctl(
        tmp_path, enabled=False, active=False
    )
    env = os.environ | {
        "SEICHE_CONTROL_LIBRARY_ONLY": "1",
        "SEICHE_CONTROL_SYSTEMCTL": str(systemctl),
        "FAKE_TIMER_STATE": str(state),
        "FAKE_TIMER_FAIL_COMMAND": failed_command,
    }

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; status=0; '
            "activate_release_timer_for_deploy || status=$?; "
            'restore_release_timer_state; exit "$status"',
            "seiche-timer-test",
            str(RELEASE_POLLER),
        ],
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert (state / "enabled").read_text(encoding="ascii") == "0\n"
    assert (state / "active").read_text(encoding="ascii") == "0\n"


def test_preexisting_target_marker_cannot_fake_a_handoff_transition(tmp_path):
    state, systemctl = _fake_release_timer_systemctl(
        tmp_path, enabled=False, active=False
    )
    env = os.environ | {
        "SEICHE_CONTROL_LIBRARY_ONLY": "1",
        "SEICHE_CONTROL_SYSTEMCTL": str(systemctl),
        "FAKE_TIMER_STATE": str(state),
        "FAKE_TIMER_FAIL_COMMAND": "start",
    }

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; '
            "TARGET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; "
            'DEPLOYED="$TARGET"; DEPLOY_WRAPPER_HANDOFF_STARTED=1; '
            'load_deployed_state() { DEPLOYED_STATE_VALUE="$TARGET"; return 0; }; '
            "activate_release_timer_for_deploy || true; cleanup",
            "seiche-timer-test",
            str(RELEASE_POLLER),
        ],
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "enabled").read_text(encoding="ascii") == "0\n"
    assert (state / "active").read_text(encoding="ascii") == "0\n"


def test_release_signature_boundary_accepts_only_the_pinned_signed_identity(tmp_path):
    repository, env = _release_signature_fixture(tmp_path)
    target = _commit_release(repository, "signed release")

    result = _verify_release_signature(env, target)

    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_desk_content_is_inert_only_within_closed_paths(tmp_path):
    repository, env = _release_signature_fixture(tmp_path)
    _commit_release(repository, "signed base")
    target = _commit_automation_content(
        repository,
        {
            "frontend/public/dispatches/edition.md": "public dispatch\n",
            "frontend/public/articles/edition.md": "public article\n",
            "backend/seiche/dispatches/edition.desk.md": "continuation\n",
        },
    )

    result = _classify_automation_content(env, target)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("files", "message", "author"),
    (
        (
            {
                "frontend/public/dispatches/edition.md": "dispatch\n",
                "ops/Caddyfile": "mixed executable configuration\n",
            },
            "dispatch: mixed paths",
            "desk@seiche.info",
        ),
        (
            {"frontend/public/dispatches/edition.md": "dispatch\n"},
            "dispatch: wrong author",
            "intruder@example.invalid",
        ),
        (
            {"frontend/public/dispatches/edition.md": "dispatch\n"},
            "feat: misleading content commit",
            "desk@seiche.info",
        ),
    ),
)
def test_generated_content_never_grants_broader_release_authority(
    tmp_path, files, message, author
):
    repository, env = _release_signature_fixture(tmp_path)
    _commit_release(repository, "signed base")
    target = _commit_automation_content(
        repository,
        files,
        message=message,
        author=author,
    )

    result = _classify_automation_content(env, target)

    assert result.returncode != 0


def test_unsigned_release_target_is_rejected_before_candidate_execution(tmp_path):
    repository, env = _release_signature_fixture(tmp_path)
    target = _commit_release(repository, "unsigned release", signed=False)

    result = _verify_release_signature(env, target)

    assert result.returncode != 0
    assert "does not carry a valid pinned SSH signature" in result.stderr


def test_wrong_principal_release_target_is_rejected_before_candidate_execution(
    tmp_path,
):
    repository, env = _release_signature_fixture(tmp_path)
    _git("config", "user.email", "intruder@example.invalid", cwd=repository)
    target = _commit_release(repository, "wrong author release")

    result = _verify_release_signature(env, target)

    assert result.returncode != 0
    assert (
        "target commit author is not the pinned release principal: "
        "intruder@example.invalid"
    ) in result.stderr


def test_release_signature_policy_is_fixed_to_one_ed25519_identity():
    signer = RELEASE_ALLOWED_SIGNERS.read_text(encoding="ascii")
    poller = RELEASE_POLLER.read_text()

    assert signer.count("\n") == 1
    assert signer.startswith("beepboop2025@users.noreply.github.com ssh-ed25519 ")
    assert "validate_allowed_signers" in poller
    assert "stat.S_IMODE(info.st_mode) != int(mode, 8)" in poller
    assert "info.st_nlink != 1" in poller
    assert '-c "gpg.ssh.program=$SSH_KEYGEN"' in poller


def test_release_receipts_are_no_clobber_and_follow_the_rollback_boundary():
    poller = RELEASE_POLLER.read_text()
    writer = poller[poller.index("write_receipt()") :]
    gate = writer.index('write_receipt gate "$GATE_RECEIPT"')
    deploy = writer.index('run_deploy_wrapper deploy "$TARGET"', gate)
    exact_health = writer.index('health_matches "$TARGET"', deploy)
    timer_ready = writer.index("release_timer_is_ready", exact_health)
    release = writer.index('write_receipt release "$RELEASE_RECEIPT"', timer_ready)
    timer_accepted = writer.index("RELEASE_TIMER_RESTORE_REQUIRED=0", release)

    assert 'chmod 0400 "$stage"' in writer
    assert 'ln "$stage" "$path"' in writer
    assert 'mv -n "$stage" "$path"' not in writer
    assert '"conclusion": "success"' in writer
    assert '"gate_receipt_sha256"' in writer
    assert gate < deploy < exact_health < timer_ready < release < timer_accepted
    assert (
        "wrapper failure never writes"
        in (ROOT / "ops" / "deploy" / "RELEASE-POLLER.md").read_text()
    )


def _bootstrap_asset_fixture(
    tmp_path: Path,
    *,
    signed: bool = True,
    author: str = "beepboop2025@users.noreply.github.com",
    alter_wrapper: bool = False,
) -> tuple[Path, Path, str, dict[str, str]]:
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen is None:
        pytest.skip("OpenSSH is required for the bootstrap contract")

    wrapper_text = DEPLOY_WRAPPER.read_text(encoding="utf-8")
    match = re.search(r"REQUIRED_MODES = (\{.*?\n\})\n\n", wrapper_text, re.DOTALL)
    assert match is not None
    required_modes = ast.literal_eval(match.group(1))
    assert isinstance(required_modes, dict)

    repository = tmp_path / "bootstrap-repository"
    _git("init", "-b", "main", str(repository), cwd=tmp_path)
    _git("config", "user.name", "Seiche Release", cwd=repository)
    _git("config", "user.email", author, cwd=repository)
    signing_key = tmp_path / "bootstrap-signing-key"
    subprocess.run(
        [ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(signing_key)],
        check=True,
    )
    _git("config", "gpg.format", "ssh", cwd=repository)
    _git("config", "user.signingkey", str(signing_key), cwd=repository)
    _git("config", "commit.gpgsign", "true" if signed else "false", cwd=repository)
    for relative, git_mode in required_modes.items():
        source = ROOT / relative
        assert source.is_file(), relative
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o755 if git_mode == "100755" else 0o644)
    _git("add", "--all", cwd=repository)
    _git("commit", "-m", "release: bootstrap fixture", cwd=repository)
    target = _git("rev-parse", "HEAD", cwd=repository)
    _git("update-ref", "refs/remotes/origin/main", target, cwd=repository)

    public_key = signing_key.with_suffix(".pub").read_text(encoding="ascii").split()
    allowed_signers = tmp_path / "bootstrap-allowed-signers"
    allowed_signers.write_text(
        f"beepboop2025@users.noreply.github.com {public_key[0]} {public_key[1]}\n",
        encoding="ascii",
    )
    allowed_signers.chmod(0o444)
    git_home = tmp_path / "bootstrap-git-home"
    git_home.mkdir(mode=0o700)
    runtime = tmp_path / "bootstrap-runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    bootstrap_wrapper = runtime / f"bootstrap-wrapper-{target}"
    shutil.copyfile(
        repository / "ops/deploy/seiche-deploy-wrapper.sh", bootstrap_wrapper
    )
    if alter_wrapper:
        with bootstrap_wrapper.open("a", encoding="utf-8") as handle:
            handle.write("# altered after signed extraction\n")
    bootstrap_wrapper.chmod(0o500)
    environment = os.environ | {
        "SEICHE_DEPLOY_BOOTSTRAP_TEST_ONLY": "1",
        "SEICHE_ALLOW_NON_ROOT_BOOTSTRAP_TEST": "1",
        "SEICHE_EXPECTED_TARGET_SHA": target,
        "SEICHE_BOOTSTRAP_TEST_REPO": str(repository),
        "SEICHE_BOOTSTRAP_TEST_RUNTIME": str(runtime),
        "SEICHE_BOOTSTRAP_TEST_ALLOWED_SIGNERS": str(allowed_signers),
        "SEICHE_BOOTSTRAP_TEST_GIT_HOME": str(git_home),
        "SEICHE_BOOTSTRAP_TEST_PYTHON": sys.executable,
    }
    return bootstrap_wrapper, runtime, target, environment


def _run_bootstrap_asset_fixture(
    wrapper: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(wrapper)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_bootstrap_assets_portable_harness_retains_one_exact_signed_root(
    secure_privileged_tmp_path: Path,
):
    tmp_path = secure_privileged_tmp_path
    wrapper, runtime, target, environment = _bootstrap_asset_fixture(tmp_path)

    result = _run_bootstrap_asset_fixture(wrapper, environment)

    assert result.returncode == 0, result.stdout + result.stderr
    retained_lines = [
        line
        for line in result.stdout.splitlines()
        if "retained exact signed target" in line
    ]
    assert len(retained_lines) == 1
    retained = Path(retained_lines[0].rsplit(" ", 1)[1])
    assert retained.parent == runtime
    assert retained.name.startswith(f"release-assets-{target}-")
    assert retained.is_dir() and not retained.is_symlink()
    assert stat.S_IMODE(retained.stat().st_mode) == 0o700
    assert (retained / ".target-sha").read_text(encoding="ascii") == f"{target}\n"
    manifest = json.loads(
        (retained / ".seiche-release-assets.json").read_text(encoding="ascii")
    )
    assert manifest["target_sha"] == target
    assert (retained / "ops/deploy/seiche-deploy-wrapper.sh").read_bytes() == (
        wrapper.read_bytes()
    )
    assert tuple(runtime.glob(".release-assets-*")) == ()
    assert tuple(runtime.glob(f"release-assets-{target}-*")) == (retained,)


def test_deploy_wrapper_entry_drops_hostile_shell_and_import_environment(
    secure_privileged_tmp_path: Path,
) -> None:
    tmp_path = secure_privileged_tmp_path
    wrapper, _runtime, _target, environment = _bootstrap_asset_fixture(tmp_path)
    sentinel = tmp_path / "hostile-entry-ran"
    bash_env = tmp_path / "hostile-bash-env"
    bash_env.write_text(
        f"printf compromised >{shlex.quote(str(sentinel))}\n",
        encoding="utf-8",
    )
    hostile_python = tmp_path / "hostile-python"
    hostile_python.mkdir()
    (hostile_python / "hashlib.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('python')\n",
        encoding="utf-8",
    )
    hostile_bin = tmp_path / "hostile-bin"
    hostile_bin.mkdir()
    _executable(
        hostile_bin / "git",
        f"printf path >{shlex.quote(str(sentinel))}\nexit 97\n",
    )
    environment |= {
        "BASH_ENV": str(bash_env),
        "ENV": str(bash_env),
        "PYTHONPATH": str(hostile_python),
        "PATH": f"{hostile_bin}:{environment['PATH']}",
    }

    result = _run_bootstrap_asset_fixture(wrapper, environment)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not sentinel.exists()


@pytest.mark.parametrize(
    "failure",
    ("wrong-target", "altered-wrapper", "unsigned-target", "wrong-author"),
)
def test_bootstrap_assets_portable_failures_retain_no_asset_root(
    secure_privileged_tmp_path: Path, failure: str
):
    tmp_path = secure_privileged_tmp_path
    wrapper, runtime, target, environment = _bootstrap_asset_fixture(
        tmp_path,
        signed=failure != "unsigned-target",
        author=(
            "intruder@example.invalid"
            if failure == "wrong-author"
            else "beepboop2025@users.noreply.github.com"
        ),
        alter_wrapper=failure == "altered-wrapper",
    )
    if failure == "wrong-target":
        environment["SEICHE_EXPECTED_TARGET_SHA"] = "b" * 40

    result = _run_bootstrap_asset_fixture(wrapper, environment)

    assert result.returncode != 0
    assert not tuple(runtime.glob("release-assets-*"))
    assert not tuple(runtime.glob(".release-assets-*"))
    output = result.stdout + result.stderr
    if failure == "wrong-target":
        assert "must equal the fetched canonical origin/main" in output
    elif failure == "altered-wrapper":
        assert "executing bootstrap wrapper is not the exact target blob" in output
    elif failure == "unsigned-target":
        assert "target commit lacks the pinned SSH signature" in output
    else:
        assert "signed target author is not the pinned release principal" in output
    assert environment["SEICHE_EXPECTED_TARGET_SHA"] in {target, "b" * 40}


def test_bootstrap_assets_mode_rejects_nonroot_and_forced_ssh(tmp_path: Path):
    target = "a" * 40
    nonroot = subprocess.run(
        ["bash", str(DEPLOY_WRAPPER)],
        env=os.environ
        | {
            "SEICHE_DEPLOY_BOOTSTRAP_ASSETS_ONLY": "1",
            "SEICHE_EXPECTED_TARGET_SHA": target,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert nonroot.returncode != 0
    assert "bootstrap-assets mode is root-only and unavailable over SSH" in (
        nonroot.stderr
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "id",
        'if [ "${1:-}" = -u ]; then printf "0\\n"; else exec /usr/bin/id "$@"; fi\n',
    )
    forced = subprocess.run(
        ["/bin/bash", "-p", str(DEPLOY_WRAPPER), "--seiche-forced-entry-v1"],
        env=os.environ
        | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "SEICHE_DEPLOY_BOOTSTRAP_ASSETS_ONLY": "1",
            "SEICHE_EXPECTED_TARGET_SHA": target,
            "SSH_ORIGINAL_COMMAND": f"deploy {target}",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert forced.returncode != 0
    assert "forced deployment did not enter through the canonical" in forced.stderr


def test_bootstrap_assets_mode_proves_exact_tip_self_and_objects_before_retaining():
    wrapper = DEPLOY_WRAPPER.read_text(encoding="utf-8")
    start = wrapper.index(
        'if [ "$BOOTSTRAP_ASSETS_ONLY" = 1 ] || [ "$BOOTSTRAP_TEST_ONLY" = 1 ]; then',
        wrapper.index("verify_release_object_graph()"),
    )
    end = wrapper.index("# Snapshot assembly needs", start)
    bootstrap = wrapper[start:end]
    self_verifier = wrapper[
        wrapper.index("verify_bootstrap_wrapper_blob()") : wrapper.index(
            "verify_release_object_graph()"
        )
    ]

    assert "refs/remotes/origin/main^{commit}" in bootstrap
    assert '[ "$TARGET" = "$BOOTSTRAP_MAIN" ]' in bootstrap
    assert (
        bootstrap.index('verify_release_object_graph "$TARGET"')
        < bootstrap.index('verify_bootstrap_wrapper_blob "$TARGET"')
        < bootstrap.index('verify_release_target_signature "$TARGET" "$BOOTSTRAP_MAIN"')
        < bootstrap.index("materialize_privileged_release_assets")
    )
    assert "fsck --strict --no-reflogs --no-dangling" in wrapper
    assert "metadata.st_nlink != 1" in self_verifier
    assert "stat.S_IMODE(metadata.st_mode) != 0o500" in self_verifier
    assert '"$(dirname -- "$self_path")" != "$DEPLOY_RUNTIME_DIR"' in self_verifier
    assert 'getattr(os, "O_NOFOLLOW", 0)' in self_verifier
    assert "stat.S_IMODE(final.st_mode) != 0o700" in self_verifier
    assert "git hash-object --stdin" in self_verifier
    assert "materialized deploy wrapper does not match" in bootstrap
    assert 'SIGNED_ASSET_ROOT=""' in bootstrap
    assert "trap - EXIT" in bootstrap
    assert "retained exact signed target" in bootstrap
    assert "systemctl" not in bootstrap
    assert "runuser" not in bootstrap
    assert " fetch " not in bootstrap
    materializer = wrapper[
        wrapper.index("materialize_privileged_release_assets()") : wrapper.index(
            "# Host-free tests exercise"
        )
    ]
    assert "local materializer_python=/usr/bin/python3" in materializer
    assert "${MATERIALIZER_PYTHON:-" not in materializer
    test_guard = wrapper[
        wrapper.index('if [ "$BOOTSTRAP_TEST_ONLY" = 1 ]; then') : wrapper.index(
            'elif [ "$BOOTSTRAP_ASSETS_ONLY" = 1 ]; then'
        )
    ]
    assert '"$(/usr/bin/id -u)" -eq 0' in test_guard
    assert "SEICHE_ALLOW_NON_ROOT_BOOTSTRAP_TEST" in test_guard
    assert 'SEICHE_DEPLOY_ENTRY_MODE" = forced' in test_guard
    assert "bootstrap tests must isolate every production path" in test_guard


def test_release_poller_installer_restores_files_and_timer_on_reload_failure(
    secure_privileged_tmp_path: Path,
):
    tmp_path = secure_privileged_tmp_path
    asset_root, release_target, _repository = _materialized_privileged_assets(tmp_path)

    systemd = tmp_path / "systemd"
    binary_dir = tmp_path / "sbin"
    runtime = tmp_path / "run"
    systemd.mkdir()
    binary_dir.mkdir()
    wrapper_dir = tmp_path / "deploy" / "bin"
    wrapper_dir.mkdir(parents=True, mode=0o700)
    wrapper = _executable(
        wrapper_dir / "seiche-deploy-wrapper.sh",
        "EXPECTED_TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}\nexit 0\n",
    )
    old_wrapper = wrapper.read_text()
    installed = {
        wrapper: old_wrapper,
        binary_dir / "seiche-release-poll": "old script\n",
        systemd / "seiche-release-poll.service": "old service\n",
        systemd / "seiche-release-poll.timer": "old timer\n",
    }
    for path, body in installed.items():
        path.write_text(body)

    calls = tmp_path / "systemctl.calls"
    reload_count = tmp_path / "reload.count"
    systemctl = _executable(
        tmp_path / "systemctl",
        f'''
printf '%s\n' "$*" >>"{calls}"
case "$1" in
  is-enabled|is-active) exit 0 ;;
  daemon-reload)
    count=0
    [ ! -f "{reload_count}" ] || count=$(cat "{reload_count}")
    count=$((count + 1))
    printf '%s\n' "$count" >"{reload_count}"
    [ "$count" -gt 1 ]
    ;;
  enable|start|disable|stop) exit 0 ;;
  *) exit 64 ;;
esac
''',
    )
    always_ok = _executable(tmp_path / "always-ok", "exit 0\n")
    installed_signer = tmp_path / "seiche-release.allowed-signers"
    nbs_state = tmp_path / "nbs-state"
    nbs_state.mkdir(mode=0o750)
    nbs_state.chmod(0o750)
    nbs_runtime = tmp_path / "nbs-runtime"
    env = os.environ | {
        "SEICHE_ALLOW_NON_ROOT_INSTALL_TEST": "1",
        "SEICHE_PRIVILEGED_ASSET_ROOT": str(asset_root),
        "SEICHE_RELEASE_TARGET_SHA": release_target,
        "SEICHE_SYSTEMD_DIR": str(systemd),
        "SEICHE_RELEASE_POLLER_DEST": str(binary_dir / "seiche-release-poll"),
        "SEICHE_DEPLOY_WRAPPER": str(wrapper),
        "SEICHE_CONTROL_RUNTIME_DIR": str(runtime),
        "SEICHE_NBS_STATE_DIR": str(nbs_state),
        "SEICHE_NBS_RUNTIME_ROOT": str(nbs_runtime),
        "SEICHE_SYSTEMCTL_BIN": str(systemctl),
        "SEICHE_SYSTEMD_ANALYZE_BIN": str(always_ok),
        "SEICHE_SYNC_BIN": str(always_ok),
        "SEICHE_FLOCK_BIN": str(always_ok),
        "SEICHE_RELEASE_ALLOWED_SIGNERS_DEST": str(installed_signer),
        "SEICHE_CONTROL_PYTHON": sys.executable,
    }
    result = subprocess.run(
        ["bash", str(RELEASE_POLLER_INSTALLER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert (
        "restoring the previous release-controller files and timer state"
        in result.stderr
    )
    for path, body in installed.items():
        assert path.read_text() == body
    systemctl_calls = calls.read_text().splitlines()
    assert systemctl_calls.count("daemon-reload") == 2
    assert "enable seiche-release-poll.timer" in systemctl_calls
    assert "start seiche-release-poll.timer" in systemctl_calls
    assert not list(systemd.glob(".seiche-release-poll.*"))
    assert installed_signer.read_text(encoding="ascii") == (
        RELEASE_ALLOWED_SIGNERS.read_text(encoding="ascii")
    )
    assert installed_signer.stat().st_mode & 0o777 == 0o444
    assert installed_signer.stat().st_nlink == 1
    assert not nbs_runtime.exists()


def test_release_poller_installer_never_replaces_an_existing_signer_pin(
    secure_privileged_tmp_path: Path,
):
    tmp_path = secure_privileged_tmp_path
    asset_root, release_target, _repository = _materialized_privileged_assets(tmp_path)

    systemd = tmp_path / "systemd"
    binary_dir = tmp_path / "sbin"
    systemd.mkdir()
    binary_dir.mkdir()
    wrapper = tmp_path / "deploy" / "bin" / "seiche-deploy-wrapper.sh"
    installed_signer = tmp_path / "seiche-release.allowed-signers"
    wrong_pin = (
        "beepboop2025@users.noreply.github.com ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIGX2PaWkr0977OLNJdYgi6QJnX/LBHS7OT+Ea8uzY8/x\n"
    )
    installed_signer.write_text(wrong_pin, encoding="ascii")
    installed_signer.chmod(0o444)
    nbs_state = tmp_path / "nbs-state"
    nbs_state.mkdir(mode=0o750)
    nbs_state.chmod(0o750)
    nbs_runtime = tmp_path / "nbs-runtime"
    env = os.environ | {
        "SEICHE_ALLOW_NON_ROOT_INSTALL_TEST": "1",
        "SEICHE_PRIVILEGED_ASSET_ROOT": str(asset_root),
        "SEICHE_RELEASE_TARGET_SHA": release_target,
        "SEICHE_SYSTEMD_DIR": str(systemd),
        "SEICHE_RELEASE_POLLER_DEST": str(binary_dir / "seiche-release-poll"),
        "SEICHE_DEPLOY_WRAPPER": str(wrapper),
        "SEICHE_NBS_STATE_DIR": str(nbs_state),
        "SEICHE_NBS_RUNTIME_ROOT": str(nbs_runtime),
        "SEICHE_RELEASE_ALLOWED_SIGNERS_DEST": str(installed_signer),
        "SEICHE_CONTROL_PYTHON": sys.executable,
    }
    result = subprocess.run(
        ["bash", str(RELEASE_POLLER_INSTALLER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "refusing to replace the pinned Seiche release signer" in result.stderr
    assert installed_signer.read_text(encoding="ascii") == wrong_pin
    assert not nbs_runtime.exists()


@pytest.mark.parametrize(
    "mutation",
    (
        "marker-target",
        "manifest-target",
        "duplicate-entry",
        "asset-bytes",
        "asset-mode",
    ),
)
def test_release_poller_installer_rejects_tampered_assets_before_anchor_creation(
    tmp_path: Path, mutation: str
):
    asset_root, release_target, _repository = _materialized_privileged_assets(tmp_path)
    marker = asset_root / ".target-sha"
    manifest_path = asset_root / ".seiche-release-assets.json"
    service = asset_root / "ops" / "deploy" / "seiche-release-poll.service"
    if mutation == "marker-target":
        marker.write_text(f"{'b' * 40}\n", encoding="ascii")
    elif mutation == "manifest-target":
        manifest = json.loads(manifest_path.read_text(encoding="ascii"))
        manifest["target_sha"] = "b" * 40
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="ascii",
        )
    elif mutation == "duplicate-entry":
        manifest = json.loads(manifest_path.read_text(encoding="ascii"))
        manifest["entries"].append(dict(manifest["entries"][0]))
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="ascii",
        )
    elif mutation == "asset-bytes":
        service.write_text(service.read_text() + "# tampered\n", encoding="utf-8")
    else:
        service.chmod(0o600)

    systemd = tmp_path / "systemd"
    binary_dir = tmp_path / "sbin"
    nbs_state = tmp_path / "nbs-state"
    nbs_runtime = tmp_path / "nbs-runtime"
    systemd.mkdir()
    binary_dir.mkdir()
    nbs_state.mkdir(mode=0o750)
    nbs_state.chmod(0o750)
    result = subprocess.run(
        ["bash", str(RELEASE_POLLER_INSTALLER)],
        env=os.environ
        | {
            "SEICHE_ALLOW_NON_ROOT_INSTALL_TEST": "1",
            "SEICHE_PRIVILEGED_ASSET_ROOT": str(asset_root),
            "SEICHE_RELEASE_TARGET_SHA": release_target,
            "SEICHE_SYSTEMD_DIR": str(systemd),
            "SEICHE_RELEASE_POLLER_DEST": str(binary_dir / "seiche-release-poll"),
            "SEICHE_DEPLOY_WRAPPER": str(
                tmp_path / "deploy" / "bin" / "seiche-deploy-wrapper.sh"
            ),
            "SEICHE_NBS_STATE_DIR": str(nbs_state),
            "SEICHE_NBS_RUNTIME_ROOT": str(nbs_runtime),
            "SEICHE_CONTROL_PYTHON": sys.executable,
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "signed privileged controller assets are invalid" in result.stderr
    assert not nbs_runtime.exists()


def test_release_poller_installer_bootstraps_private_control_wrapper(
    secure_privileged_tmp_path: Path,
):
    tmp_path = secure_privileged_tmp_path
    asset_root, release_target, _repository = _materialized_privileged_assets(tmp_path)

    systemd = tmp_path / "systemd"
    binary_dir = tmp_path / "sbin"
    runtime = tmp_path / "run"
    wrapper = tmp_path / "deploy" / "bin" / "seiche-deploy-wrapper.sh"
    systemd.mkdir()
    binary_dir.mkdir()
    calls = tmp_path / "systemctl.calls"
    systemctl = _executable(
        tmp_path / "systemctl",
        f'''printf '%s\n' "$*" >>"{calls}"
case "$1" in
  is-enabled|is-active) exit 1 ;;
  daemon-reload|enable|start|disable|stop) exit 0 ;;
  *) exit 64 ;;
esac
''',
    )
    always_ok = _executable(tmp_path / "always-ok", "exit 0\n")
    installed_signer = tmp_path / "seiche-release.allowed-signers"
    nbs_state = tmp_path / "nbs-state"
    nbs_state.mkdir(mode=0o750)
    nbs_state.chmod(0o750)
    nbs_runtime = tmp_path / "nbs-runtime"
    env = os.environ | {
        "SEICHE_ALLOW_NON_ROOT_INSTALL_TEST": "1",
        "SEICHE_PRIVILEGED_ASSET_ROOT": str(asset_root),
        "SEICHE_RELEASE_TARGET_SHA": release_target,
        "SEICHE_SYSTEMD_DIR": str(systemd),
        "SEICHE_RELEASE_POLLER_DEST": str(binary_dir / "seiche-release-poll"),
        "SEICHE_DEPLOY_WRAPPER": str(wrapper),
        "SEICHE_CONTROL_RUNTIME_DIR": str(runtime),
        "SEICHE_NBS_STATE_DIR": str(nbs_state),
        "SEICHE_NBS_RUNTIME_ROOT": str(nbs_runtime),
        "SEICHE_SYSTEMCTL_BIN": str(systemctl),
        "SEICHE_SYSTEMD_ANALYZE_BIN": str(always_ok),
        "SEICHE_SYNC_BIN": str(always_ok),
        "SEICHE_FLOCK_BIN": str(always_ok),
        "SEICHE_RELEASE_ALLOWED_SIGNERS_DEST": str(installed_signer),
        "SEICHE_CONTROL_PYTHON": sys.executable,
    }
    hostile_cwd = tmp_path / "hostile-controller-cwd"
    hostile_cwd.mkdir()
    hostile_sentinel = tmp_path / "controller-shadow-imported"
    (hostile_cwd / "json.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(hostile_sentinel)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    env |= {"PYTHONPATH": str(hostile_cwd), "PYTHONHOME": str(hostile_cwd)}

    result = subprocess.run(
        ["bash", str(RELEASE_POLLER_INSTALLER)],
        env=env,
        cwd=hostile_cwd,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not hostile_sentinel.exists()
    assert wrapper.read_bytes() == DEPLOY_WRAPPER.read_bytes()
    assert wrapper.stat().st_mode & 0o777 == 0o700
    assert wrapper.parent.stat().st_mode & 0o777 == 0o700
    assert nbs_state.is_dir()
    assert not nbs_state.is_symlink()
    assert nbs_state.stat().st_mode & 0o777 == 0o750
    assert nbs_runtime.is_dir()
    assert not nbs_runtime.is_symlink()
    assert nbs_runtime.stat().st_mode & 0o777 == 0o755
    assert (binary_dir / "seiche-release-poll").read_bytes() == (
        RELEASE_POLLER.read_bytes()
    )
    assert "disable --now seiche-release-poll.timer" in calls.read_text().splitlines()

    valid_retry = subprocess.run(
        ["bash", str(RELEASE_POLLER_INSTALLER)],
        env=env,
        cwd=hostile_cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    assert valid_retry.returncode == 0, valid_retry.stdout + valid_retry.stderr
    assert not hostile_sentinel.exists()
    assert nbs_state.stat().st_mode & 0o777 == 0o750

    for identity_flag in ("-u", "-g"):
        fake_bin = tmp_path / f"fake-id-{identity_flag[1:]}"
        fake_bin.mkdir()
        identity_getter = os.getuid if identity_flag == "-u" else os.getgid
        wrong_identity = identity_getter() + 1
        fake_id = _executable(
            fake_bin / "id",
            f"""case "$1" in
  {identity_flag}) printf '%s\n' {wrong_identity} ;;
  *) exec /usr/bin/id "$@" ;;
esac
""",
        )
        assert fake_id.is_file()
        identity_env = env | {"PATH": f"{fake_bin}:{env['PATH']}"}
        identity_retry = subprocess.run(
            ["bash", str(RELEASE_POLLER_INSTALLER)],
            env=identity_env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert identity_retry.returncode != 0
        assert (
            "signed privileged controller assets are invalid" in identity_retry.stderr
        )

    nbs_state.chmod(0o777)
    unsafe_retry = subprocess.run(
        ["bash", str(RELEASE_POLLER_INSTALLER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert unsafe_retry.returncode != 0
    assert "NBS evidence root is absent or has unsafe metadata" in unsafe_retry.stderr

    nbs_state.chmod(0o750)
    nbs_state.rmdir()
    symlink_target = tmp_path / "nbs-symlink-target"
    symlink_target.mkdir(mode=0o750)
    symlink_target.chmod(0o750)
    nbs_state.symlink_to(symlink_target, target_is_directory=True)
    symlink_retry = subprocess.run(
        ["bash", str(RELEASE_POLLER_INSTALLER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert symlink_retry.returncode != 0
    assert "NBS evidence root is absent or has unsafe metadata" in symlink_retry.stderr

    nbs_state.unlink()
    nbs_state.write_text("not a directory\n")
    file_retry = subprocess.run(
        ["bash", str(RELEASE_POLLER_INSTALLER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert file_retry.returncode != 0
    assert "NBS evidence root is absent or has unsafe metadata" in file_retry.stderr

    nbs_state.unlink()
    missing_retry = subprocess.run(
        ["bash", str(RELEASE_POLLER_INSTALLER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing_retry.returncode != 0
    assert "NBS evidence root is absent or has unsafe metadata" in missing_retry.stderr

    real_parent = tmp_path / "real-nbs-parent"
    real_parent.mkdir()
    ancestor_nbs = real_parent / "nbs"
    ancestor_nbs.mkdir(mode=0o750)
    ancestor_nbs.chmod(0o750)
    linked_parent = tmp_path / "linked-nbs-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    ancestor_env = env | {"SEICHE_NBS_STATE_DIR": str(linked_parent / "nbs")}
    ancestor_retry = subprocess.run(
        ["bash", str(RELEASE_POLLER_INSTALLER)],
        env=ancestor_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert ancestor_retry.returncode != 0
    assert "NBS evidence root is absent or has unsafe metadata" in ancestor_retry.stderr


def test_release_poller_units_are_inert_until_an_explicit_handoff():
    installer = RELEASE_POLLER_INSTALLER.read_text()
    service = RELEASE_POLLER_SERVICE.read_text()
    timer = RELEASE_POLLER_TIMER.read_text()
    runbook = (ROOT / "ops" / "deploy" / "RELEASE-POLLER.md").read_text()

    assert "expected-target-SHA safety pin" in installer
    assert 'grp.getgrnam("seiche").gr_gid' in installer
    assert "/var/lib/seiche-deploy/bin/seiche-deploy-wrapper.sh" in installer
    assert 'mv -f -- "$WRAPPER_NEW" "$DEPLOY_WRAPPER"' in installer
    assert 'exec 9>"$CONTROL_LOCK"' in installer
    assert '"$FLOCK" --nonblock 9' in installer
    assert installer.index('mv -f -- "$SCRIPT_NEW" "$SCRIPT_DEST"') < installer.index(
        '"$SYSTEMD_ANALYZE" verify'
    )
    assert "rollback_install" in installer
    assert '"$SYSTEMCTL" disable --now seiche-release-poll.timer' in installer
    assert 'ENABLE="${SEICHE_ENABLE_RELEASE_POLLER:-0}"' in installer
    assert "refusing to replace the pinned Seiche release signer" in installer
    assert (
        "production requires the out-of-band Seiche release signer trust anchor"
        in installer
    )
    assert 'ln "$SIGNER_STAGE" "$ALLOWED_SIGNERS"' in installer
    assert (
        "SEICHE_CONTROL_ALLOWED_SIGNERS=/etc/seiche-release.allowed-signers" in service
    )
    assert "SEICHE_CONTROL_SIGNING_PRINCIPAL=" in service
    assert "ReadOnlyPaths=/etc/seiche-release.allowed-signers" in service
    assert (
        "ExecStart=/usr/bin/env -i HOME=/root LANG=C LC_ALL=C "
        "PATH=/usr/bin:/bin "
        "SEICHE_CONTROL_ALLOWED_SIGNERS=/etc/seiche-release.allowed-signers "
        "SEICHE_CONTROL_SIGNING_PRINCIPAL="
        "beepboop2025@users.noreply.github.com "
        "/usr/bin/bash -p /usr/local/sbin/seiche-release-poll" in service
    )
    poller_unset = {
        name
        for line in service.splitlines()
        if line.startswith("UnsetEnvironment=")
        for name in line.removeprefix("UnsetEnvironment=").split()
    }
    assert {
        "GCONV_PATH",
        "GLIBC_TUNABLES",
        "LD_AUDIT",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "LOCPATH",
    } <= poller_unset
    assert not any(line.startswith("Environment=") for line in service.splitlines())
    assert "ConditionPathExists" not in service
    assert "TimeoutStartSec=3h" in service
    assert "RequiresMountsFor=/var/lib/seiche /var/lib/seiche-nbs " in service
    assert "OnUnitInactiveSec=5min" in timer
    assert "WantedBy=timers.target" in timer
    assert "bash /home/seiche/app/ops/deploy/install-release-poller.sh" not in runbook
    assert '"$ASSET_ROOT/ops/deploy/install-release-poller.sh"' in runbook
    assert "SEICHE_DEPLOY_BOOTSTRAP_ASSETS_ONLY=1" in runbook
    assert "fsck --strict --no-reflogs --no-dangling" in runbook
    assert "ops/deploy/storage-volume.env.example" in DEPLOY_WRAPPER.read_text()


def test_first_controller_runbook_has_one_way_trust_and_storage_order():
    runbook = (ROOT / "ops" / "deploy" / "RELEASE-POLLER.md").read_text(
        encoding="utf-8"
    )
    trust_publish = runbook.index('ln "$SIGNER_STAGE" "$SIGNERS"')
    fetch = runbook.index("fetch --no-tags origin main")
    bootstrap = runbook.index("SEICHE_DEPLOY_BOOTSTRAP_ASSETS_ONLY=1")
    retained_root = runbook.index("ASSET_ROOT=${BOOTSTRAP_OUTPUT##* }")
    preflight_source = runbook.index(
        '"$ASSET_ROOT/ops/deploy/seiche-storage-preflight.py"'
    )
    preflight_start = runbook.index("systemctl start seiche-storage-preflight.service")
    controller_install = runbook.index(
        '"$ASSET_ROOT/ops/deploy/install-release-poller.sh"'
    )

    assert (
        trust_publish
        < fetch
        < bootstrap
        < retained_root
        < preflight_source
        < preflight_start
        < controller_install
    )
    assert "OWNER_PUBKEY=/root/seiche-owner-release-key.pub" in runbook
    assert "SHA256:yhoa/PIDMM6M/ZennILp8jtRJy5pArncJRARbQssTMI" in runbook
    assert "never\nreplaces a pin" in runbook
    assert "Never read or copy the key or expected fingerprint from the" in runbook
    assert '[ "$(stat -c \'%U:%G:%a:%h\' "$SIGNERS")" = root:root:444:1 ]' in runbook
    assert "ops/deploy/storage-volume.env.example" in runbook
    assert "ROLLBACK_ROOT=/root/seiche-storage-v1-before-$TARGET" in runbook
    assert "restore all\nthree members as one compatible set" in runbook
    assert "Once a v2 candidate accepts or new NBS evidence is ingested" in runbook
    assert "/home/seiche/app/ops/deploy/seiche-storage-preflight" not in runbook

    bash_blocks = re.findall(r"```bash\n(.*?)```", runbook, re.DOTALL)
    assert bash_blocks
    for index, block in enumerate(bash_blocks):
        syntax = subprocess.run(
            ["bash", "-n"],
            input=block,
            text=True,
            capture_output=True,
            check=False,
        )
        assert syntax.returncode == 0, f"bash block {index}: {syntax.stderr}"


def test_official_readiness_preflights_start_with_a_clean_privileged_shell():
    for script_path in (RECOVERY_SEAL, MARKET_INSTALLER):
        script = script_path.read_text(encoding="utf-8")
        helper_start = script.index("run_recovery_proof_preflight() {")
        if script_path == RECOVERY_SEAL:
            helper_end = script.index("candidate_health_once() {", helper_start)
            readiness_variable = "READINESS_SCRIPT"
        else:
            helper_end = script.index(
                "validate_data_readiness_convergence_wait() {", helper_start
            )
            readiness_variable = "DATA_READINESS_SCRIPT"
        helpers = script[helper_start:helper_end]

        assert helpers.count("/usr/bin/env -i") == 2
        assert helpers.count("HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin") == 2
        assert helpers.count(f'/usr/bin/bash -p "${readiness_variable}"') == 2
        assert f'/usr/bin/bash "${readiness_variable}"' not in helpers
        assert "SEICHE_DATA_READINESS_PROOF_ONLY=1" in helpers
        assert "SEICHE_DATA_READINESS_SKIP_OFFSITE=1" in helpers
        assert "SEICHE_DATA_READINESS_REQUIRED_UNITS=" in helpers


def test_release_poller_allows_only_the_reviewed_setgid_export_boundary():
    service = RELEASE_POLLER_SERVICE.read_text()
    market_installer = MARKET_INSTALLER.read_text()
    writable_paths = {
        path
        for line in service.splitlines()
        if line.startswith("ReadWritePaths=")
        for path in line.removeprefix("ReadWritePaths=").split()
    }
    capabilities = next(
        line.removeprefix("CapabilityBoundingSet=").split()
        for line in service.splitlines()
        if line.startswith("CapabilityBoundingSet=")
    )

    # The production failure this contract guards: systemd must not reject the
    # installer's reviewed 2750 export chmod before candidate health can run.
    assert 'chmod 2750 "$FUNDING_EXPORT_DIR"' in market_installer
    assert "RestrictSUIDSGID=false" in service
    assert "CAP_FSETID" in capabilities
    assert "/var/lib/seiche" in writable_paths
    assert "/var/lib/seiche-nbs" in writable_paths
    assert "/var/lib/seiche-deploy" in writable_paths
    assert "/opt/seiche-nbs-intake" in writable_paths

    # Allowing that one setgid collaboration directory does not reopen the
    # controller's host namespace or privilege-escalation surfaces.
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "ProtectHome=read-only" in service
    assert "AmbientCapabilities=" in service
    assert capabilities == [
        "CAP_AUDIT_WRITE",
        "CAP_CHOWN",
        "CAP_DAC_OVERRIDE",
        "CAP_DAC_READ_SEARCH",
        "CAP_FOWNER",
        "CAP_FSETID",
        "CAP_KILL",
        "CAP_SETGID",
        "CAP_SETUID",
    ]
    assert "/etc/seiche" in writable_paths
    assert "/etc/systemd/system" in writable_paths
    assert "/etc/caddy" in writable_paths
    assert "/" not in writable_paths
    assert "/opt" not in writable_paths
    assert "/usr" not in writable_paths
    assert "/usr/local" not in writable_paths
    assert "/root" not in writable_paths


def test_release_controller_wrapper_stays_outside_protected_homes():
    canonical = "/var/lib/seiche-deploy/bin/seiche-deploy-wrapper.sh"
    poller = RELEASE_POLLER.read_text()
    installer = RELEASE_POLLER_INSTALLER.read_text()
    wrapper = DEPLOY_WRAPPER.read_text()
    update = (ROOT / "ops" / "deploy" / "update.sh").read_text()
    service = RELEASE_POLLER_SERVICE.read_text()

    for source in (poller, installer, wrapper, update):
        assert canonical in source
        assert "/root/seiche-deploy-wrapper.sh" not in source
    assert "ProtectHome=read-only" in service
    assert "ReadWritePaths=/home/seiche /root" not in service


def test_privileged_controllers_pin_runuser_outside_the_minimal_path(tmp_path: Path):
    poller = RELEASE_POLLER.read_text()
    wrapper = DEPLOY_WRAPPER.read_text()
    service = RELEASE_POLLER_SERVICE.read_text()

    assert "PATH=/usr/bin:/bin" in service
    assert "RUNUSER=/usr/sbin/runuser" in poller
    assert "RUNUSER=/usr/sbin/runuser" in wrapper
    assert "os.execv(runner," in poller
    assert "os.execvp(runner," not in poller
    bare_runuser = r"(?m)(^|[;&|()])[ \t]*(?:if[ \t]+![ \t]+)?runuser(?=[ \t])"
    assert not re.search(bare_runuser, poller)
    assert not re.search(bare_runuser, wrapper)

    rejected = subprocess.run(
        ["bash", "-c", 'source "$1"', "poller-override", str(RELEASE_POLLER)],
        env=os.environ | {"SEICHE_CONTROL_RUNUSER": str(tmp_path / "runuser")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert "unavailable in production" in rejected.stderr


def test_forced_ssh_wrapper_has_a_clean_canonical_entry_contract():
    wrapper = DEPLOY_WRAPPER.read_text()
    prelude = wrapper[: wrapper.index("materialize_privileged_release_assets()")]
    runbook = (ROOT / "ops" / "deploy" / "RELEASE-POLLER.md").read_text()
    workflow = DEPLOY_WORKFLOW.read_text()

    assert wrapper.startswith("#!/bin/bash -p\n")
    assert "/usr/bin/env -i" in prelude
    assert (
        'HOME="$SEICHE_DEPLOY_ENTRY_HOME" LANG=C LC_ALL=C PATH=/usr/bin:/bin' in prelude
    )
    assert "SEICHE_DEPLOY_FORCED_MARKER=--seiche-forced-entry-v1" in prelude
    assert '"$SEICHE_DEPLOY_ISOLATED_MARKER" "$SEICHE_DEPLOY_ENTRY_MODE"' in prelude
    assert "CANONICAL_DEPLOY_WRAPPER=/var/lib/seiche-deploy/bin/" in prelude
    assert 'if [ "$SEICHE_DEPLOY_ENTRY_MODE" = forced ]; then' in prelude
    assert "SSH deployment entry is missing the forced-command marker" in prelude
    assert "root:root:700:1:regular file" in prelude
    assert "forced deployment did not enter through the canonical" in prelude

    request_gate = wrapper[wrapper.index("EXPECTED_TARGET=") :]
    assert 'if [ "$SEICHE_DEPLOY_ENTRY_MODE" = forced ]; then' in request_gate
    assert (
        "forced deployment command must be deploy plus one commit SHA" in request_gate
    )
    assert "restrict + env -i + bash -p" in workflow
    assert 'restrict,command="/usr/bin/env -i HOME=/root LANG=C LC_ALL=C ' in runbook
    assert 'PATH=/usr/bin:/bin SSH_ORIGINAL_COMMAND=\\\\"' in runbook
    assert "/usr/bin/bash -p /var/lib/seiche-deploy/bin/" in runbook
    assert "--seiche-forced-entry-v1" in runbook
    assert "permituserenvironment no" in runbook
    assert '[ "$root_shell" = /bin/bash ]' in runbook
    assert "active sshd Match blocks are forbidden" in runbook
    assert 'acceptenv != ["LANG", "LC_*"]' in runbook
    assert "sshd SetEnv must remain empty" in runbook
    assert "authorized_keys.seiche-forced-v1-before-$TARGET" in runbook
    assert "trigger-forced-deploy.sh" in runbook
    assert "seiche-deploy-wrapper.retired-$TARGET" in runbook


def test_empty_or_unmarked_ssh_request_never_becomes_local_maintenance():
    empty_forced = subprocess.run(
        ["/bin/bash", "-p", str(DEPLOY_WRAPPER), "--seiche-forced-entry-v1"],
        env=os.environ | {"SSH_ORIGINAL_COMMAND": ""},
        text=True,
        capture_output=True,
        check=False,
    )
    assert empty_forced.returncode != 0
    assert "canonical root-owned wrapper" in empty_forced.stderr

    unmarked = subprocess.run(
        ["/bin/bash", "-p", str(DEPLOY_WRAPPER)],
        env=os.environ
        | {
            "SSH_CONNECTION": "192.0.2.10 4242 192.0.2.20 22",
            "SSH_ORIGINAL_COMMAND": "",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert unmarked.returncode != 0
    assert "missing the forced-command marker" in unmarked.stderr


@pytest.mark.parametrize(
    ("directive", "message"),
    [
        ("Match\tAddress 192.0.2.1", "active sshd Match blocks are forbidden"),
        ("Include\t/tmp/unreviewed/*.conf", "unreviewed sshd Include path"),
    ],
)
def test_forced_ssh_config_audit_rejects_tab_separated_policy_bypasses(
    tmp_path: Path,
    directive: str,
    message: str,
) -> None:
    runbook = (ROOT / "ops" / "deploy" / "RELEASE-POLLER.md").read_text()
    marker = (
        "/usr/bin/python3 -I -B - \\\n"
        "  /etc/ssh/sshd_config /etc/ssh/sshd_config.d 0 0 \\\n"
        "  '/etc/ssh/sshd_config.d/*.conf' <<'PY'\n"
    )
    script_start = runbook.index(marker) + len(marker)
    script = runbook[script_start : runbook.index("\nPY", script_start)]

    fragments = tmp_path / "sshd_config.d"
    fragments.mkdir(mode=0o755)
    config = tmp_path / "sshd_config"
    expected_include = f"{fragments}/*.conf"
    config.write_text(f"Include {expected_include}\n{directive}\n", encoding="utf-8")
    config.chmod(0o644)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-",
            str(config),
            str(fragments),
            str(os.getuid()),
            str(os.getgid()),
            expected_include,
        ],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert message in result.stderr


def test_forced_ssh_migration_reloads_the_audited_live_daemon_before_key_rewrite():
    runbook = (ROOT / "ops" / "deploy" / "RELEASE-POLLER.md").read_text()
    migration = runbook[runbook.index("### Migrate the forced SSH fallback") :]

    assert "SSHD_UNIT=/usr/lib/systemd/system/ssh.service" in migration
    assert "SSHD_DEFAULTS=/etc/default/ssh" in migration
    assert "--property=DropInPaths --value" in migration
    assert '"$SSHD_DEFAULTS (ignore_errors=yes)"' in migration
    assert '[ "$SSHD_ACTIVE_DEFAULTS" = SSHD_OPTS= ]' in migration
    assert "ssh.service ExecStart is not the reviewed default" in migration
    assert "ssh.service ExecReload is not the reviewed reload path" in migration
    assert "main process was launched with custom SSHD_OPTS" in migration
    assert '[ "$SSHD_MAIN_PID_AFTER" = "$SSHD_MAIN_PID_BEFORE" ]' in migration

    source_audit = migration.index("active sshd Match blocks are forbidden")
    service_audit = migration.index("--property=FragmentPath --value")
    launch_environment = migration.index("/proc/$SSHD_MAIN_PID_BEFORE/environ")
    syntax_check = migration.index("\n/usr/sbin/sshd -t\n")
    reload_service = migration.index('/usr/bin/systemctl reload "$SSHD_SERVICE"')
    active_after_reload = migration.index(
        '/usr/bin/systemctl is-active --quiet "$SSHD_SERVICE"', reload_service
    )
    same_pid = migration.index('[ "$SSHD_MAIN_PID_AFTER" = "$SSHD_MAIN_PID_BEFORE" ]')
    effective_policy = migration.index("/usr/sbin/sshd -T", same_pid)
    key_rewrite = migration.index("Then atomically transform")

    assert (
        source_audit
        < service_audit
        < launch_environment
        < syntax_check
        < reload_service
        < active_after_reload
        < same_pid
        < effective_policy
        < key_rewrite
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"PATH=/usr/bin\0SSHD_OPTS=-f /tmp/unreviewed-sshd_config\0",
        b"SSHD_OPTS=\0SSHD_OPTS=\0",
    ],
)
def test_forced_ssh_launch_environment_audit_rejects_custom_or_duplicate_opts(
    tmp_path: Path,
    payload: bytes,
) -> None:
    runbook = (ROOT / "ops" / "deploy" / "RELEASE-POLLER.md").read_text()
    marker = "/usr/bin/python3 -I -B - \"/proc/$SSHD_MAIN_PID_BEFORE/environ\" <<'PY'\n"
    script_start = runbook.index(marker) + len(marker)
    script = runbook[script_start : runbook.index("\nPY", script_start)]
    environment = tmp_path / "environ"
    environment.write_bytes(payload)

    result = subprocess.run(
        [sys.executable, "-I", "-B", "-", str(environment)],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "SSHD_OPTS" in result.stderr


@pytest.mark.parametrize(
    "payload",
    [b"PATH=/usr/bin\0", b"PATH=/usr/bin\0SSHD_OPTS=\0"],
)
def test_forced_ssh_launch_environment_audit_accepts_absent_or_empty_opts(
    tmp_path: Path,
    payload: bytes,
) -> None:
    runbook = (ROOT / "ops" / "deploy" / "RELEASE-POLLER.md").read_text()
    marker = "/usr/bin/python3 -I -B - \"/proc/$SSHD_MAIN_PID_BEFORE/environ\" <<'PY'\n"
    script_start = runbook.index(marker) + len(marker)
    script = runbook[script_start : runbook.index("\nPY", script_start)]
    environment = tmp_path / "environ"
    environment.write_bytes(payload)

    result = subprocess.run(
        [sys.executable, "-I", "-B", "-", str(environment)],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_signed_storage_template_matches_the_existing_production_namespace():
    template = (ROOT / "ops" / "deploy" / "storage-volume.env.example").read_text()
    runbook = (ROOT / "ops" / "deploy" / "HETZNER-VOLUME.md").read_text()
    expected_roots = {
        "SEICHE_STORAGE_EXPECTED_STATE_FSROOT": "/seiche/runtime/var-lib-seiche",
        "SEICHE_STORAGE_EXPECTED_NBS_FSROOT": "/seiche/evidence/seiche-nbs",
        "SEICHE_STORAGE_EXPECTED_BACKUP_FSROOT": "/seiche/backups/seiche-market",
    }

    for key, root in expected_roots.items():
        assert f"{key}={root}\n" in template
        assert root in runbook


def test_wrapper_installer_and_self_sync_preserve_canonical_mode_0700():
    wrapper = DEPLOY_WRAPPER.read_text()
    installer = RELEASE_POLLER_INSTALLER.read_text()
    sync = wrapper[wrapper.index("# Self-sync the deploy chain") :]

    assert 'install -m 0700 "$STAGE_DIR/seiche-deploy-wrapper.sh"' in installer
    assert 'if [ "$dst" = "$CANONICAL_DEPLOY_WRAPPER" ]; then' in sync
    assert "sync_mode=0700" in sync
    assert 'install -o root -g root -m "$sync_mode"' in sync


def test_promotion_is_point_of_no_return_and_rollback_stops_before_reset():
    wrapper = DEPLOY_WRAPPER.read_text()
    promotion = wrapper[
        wrapper.index("promote_snapshot_handoff()") : wrapper.index(
            "deploy_market_platform ||"
        )
    ]
    assert promotion.index('write_deployed_state "$AFTER"') < promotion.index(
        "POINT_OF_NO_RETURN=1"
    )
    assert 'if [ -n "$DEPLOYED_STATE_RENAMED" ]; then' in promotion
    assert promotion.index("POINT_OF_NO_RETURN=1") < promotion.index(
        'systemctl start "$PROMOTION_UNIT"'
    )
    assert promotion.index('systemctl start "$PROMOTION_UNIT"') < promotion.index(
        'candidate_health_wait 120 "$AFTER"'
    )
    assert 'rm -f -- "$PROMOTION_REQUEST"' in promotion

    assert wrapper.index("market_health", wrapper.index('HEALTHY=""')) < wrapper.index(
        "promote_snapshot_handoff", wrapper.index('HEALTHY=""')
    )
    no_rollback = wrapper[
        wrapper.index('if [ -n "$POINT_OF_NO_RETURN" ]') : wrapper.index(
            "# A red warm-up"
        )
    ]
    assert "restore_market_services" in no_rollback
    assert "exit 1" in no_rollback

    rollback = wrapper[wrapper.index("# A red warm-up") :]
    validate = rollback.index('valid_release_sha "$DEPLOYED"')
    verify_commit = rollback.index('rev-parse --verify --quiet "$DEPLOYED^{commit}"')
    stop_api = rollback.index("systemctl stop seiche-api")
    rewrite_release = rollback.index('write_release_env "$DEPLOYED"')
    reset = rollback.index('reset -q --hard "$DEPLOYED"')
    restart = rollback.index("systemctl restart seiche-api")
    assert validate < verify_commit < stop_api < rewrite_release < reset < restart
    assert "systemctl stop seiche-api 2>/dev/null || true" not in rollback
    assert 'rollback_health_wait "$API_FULL_REBUILD_WAIT_SECONDS"' in rollback
    rollback_health = wrapper[
        wrapper.index("rollback_health_wait()") : wrapper.index("market_health()")
    ]
    assert "require_rebuilt=true" in rollback_health
    assert "candidate_health_once" not in rollback_health


def test_palimpest_osint_edge_is_an_exact_static_allowlist():
    caddy = CADDYFILE.read_text()
    assert "handle_path /palimpsest/osint/*" not in caddy
    assert (
        "@palimpsest_osint path /palimpsest/osint/osint-china.json "
        "/palimpsest/osint/osint-china.json.hmac-sha256"
    ) in caddy
    assert "root * /var/lib/palimpsest-nemesis/public" in caddy
    osint_block = caddy[
        caddy.index("@palimpsest_osint path") : caddy.index("# Palimpsest BLEEDTHROUGH")
    ]
    assert 'header Cache-Control "no-store"' in osint_block
    assert "stale-if-error" not in osint_block
    assert "uri strip_prefix /palimpsest/osint" in osint_block
    assert "reverse_proxy" not in osint_block


def test_palimpsest_bleedthrough_edge_is_an_exact_sanitized_allowlist():
    caddy = CADDYFILE.read_text()
    assert "handle_path /palimpsest/bleedthrough/*" not in caddy
    assert (
        "@palimpsest_bleedthrough path "
        "/palimpsest/bleedthrough/bleedthrough-latest.json "
        "/palimpsest/bleedthrough/bleedthrough-history.jsonl"
    ) in caddy
    block = caddy[
        caddy.index("@palimpsest_bleedthrough path") : caddy.index("# Palimpsest MCP")
    ]
    assert 'header Access-Control-Allow-Origin "https://palimpsest.info"' in block
    assert 'header Cache-Control "no-store, no-transform"' in block
    assert 'header Content-Disposition "inline"' in block
    assert "uri strip_prefix /palimpsest/bleedthrough" in block
    assert "root * /var/lib/palimpsest/readings" in block
    assert "file_server" in block
    assert "reverse_proxy" not in block


def test_palimpsest_host_readings_edge_preserves_exact_static_allowlist():
    caddy = CADDYFILE.read_text()
    block = caddy[
        caddy.index("# Palimpsest exposes four additional") : caddy.index(
            "# ScamShield publishes one atomic"
        )
    ]
    routes = {
        "baike-public-snapshot": "baike-public-snapshot-latest.json",
        "peer-context": "peer-context-latest.json",
        "greatfire-context": "greatfire-context-latest.json",
        "public-deletion-ledgers": "public-deletion-ledgers-latest.json",
    }

    for prefix, filename in routes.items():
        assert f"path \\\n        /palimpsest/{prefix}/{filename}" in block
        assert f"uri strip_prefix /palimpsest/{prefix}" in block
    assert (
        block.count('header Access-Control-Allow-Origin "https://palimpsest.info"') == 4
    )
    assert block.count('header Cache-Control "no-store, no-transform"') == 4
    assert block.count('header Content-Disposition "inline"') == 4
    assert block.count("root * /var/lib/palimpsest/readings") == 4
    assert block.count("file_server") == 4
    assert "handle_path /palimpsest/" not in block
    assert "reverse_proxy" not in block


def test_palimpsest_social_observations_edge_is_an_exact_static_allowlist():
    caddy = CADDYFILE.read_text()
    block = caddy[
        caddy.index("# ScamShield publishes one atomic") : caddy.index(
            "# Palimpsest MCP"
        )
    ]

    fallback = block.index("@palimpsest_social_other path")
    for name in ("latest.json", "versions.jsonl", "hmac.json"):
        route = f"path /palimpsest/social-observations/{name}"
        assert route in block
        assert block.index(route) < fallback
    assert block.count("method GET HEAD") == 3
    assert "handle_path /palimpsest/social-observations/*" not in block
    assert (
        "@palimpsest_social_other path /palimpsest/social-observations "
        "/palimpsest/social-observations/ "
        "/palimpsest/social-observations/*"
    ) in block
    assert 'respond "not here" 404' in block
    assert (
        block.count('header Access-Control-Allow-Origin "https://palimpsest.info"') == 3
    )
    assert block.count('header Cache-Control "no-store, no-transform"') == 3
    assert 'header Content-Type "application/x-ndjson"' in block
    assert block.count("uri strip_prefix /palimpsest/social-observations") == 3
    assert block.count("root * /var/lib/scamshield/social-export/current") == 3
    assert block.count("file_server") == 3
    assert "reverse_proxy" not in block


def test_adapted_social_routes_are_reachable_before_the_site_catch_all():
    caddy = shutil.which("caddy")
    assert caddy is not None, "Caddy is required to validate adapted route reachability"
    result = subprocess.run(  # noqa: S603 - fixed argv invokes the pinned adapter
        [caddy, "adapt", "--config", str(CADDYFILE), "--adapter", "caddyfile"],
        check=True,
        text=True,
        capture_output=True,
    )
    document = json.loads(result.stdout)
    servers = document["apps"]["http"]["servers"]
    api_route = next(
        route
        for server in servers.values()
        for route in server["routes"]
        if any(
            "api.seiche.info" in matcher.get("host", [])
            for matcher in route.get("match", [])
        )
    )
    api_subroute = next(
        handler for handler in api_route["handle"] if handler["handler"] == "subroute"
    )
    routes = api_subroute["routes"]

    def paths(route: dict) -> set[str]:
        return {
            path
            for matcher in route.get("match", [])
            for path in matcher.get("path", [])
        }

    expected = {
        f"/palimpsest/social-observations/{name}"
        for name in ("latest.json", "versions.jsonl", "hmac.json")
    }
    exact_indexes = {
        path: next(index for index, route in enumerate(routes) if path in paths(route))
        for path in expected
    }
    deny_index = next(
        index
        for index, route in enumerate(routes)
        if "/palimpsest/social-observations/*" in paths(route)
    )
    group = routes[deny_index]["group"]
    catch_all_index = next(
        index
        for index, route in enumerate(routes)
        if index > deny_index and route.get("group") == group and not route.get("match")
    )

    assert max(exact_indexes.values()) < deny_index < catch_all_index
    for index in exact_indexes.values():
        assert routes[index]["group"] == group
        matcher = routes[index]["match"]
        assert any(set(item.get("method", [])) == {"GET", "HEAD"} for item in matcher)
