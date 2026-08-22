"""Host-free executable tests for privileged release-asset materialization."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_WRAPPER = ROOT / "ops" / "deploy" / "seiche-deploy-wrapper.sh"
SYSTEM_GIT = Path("/usr/bin/git")


@dataclass(frozen=True)
class AssetFixture:
    repository: Path
    target: str
    parent: Path
    destination: Path
    required_modes: dict[str, str]
    committed_bytes: dict[str, bytes]
    tree_entries: dict[str, tuple[str, str]]


def _required_modes() -> dict[str, str]:
    wrapper = DEPLOY_WRAPPER.read_text(encoding="utf-8")
    match = re.search(r"REQUIRED_MODES = (\{.*?\n\})\n\n", wrapper, re.DOTALL)
    assert match is not None
    parsed = ast.literal_eval(match.group(1))
    assert isinstance(parsed, dict)
    assert parsed
    assert all(
        isinstance(path, str) and mode in {"100644", "100755"}
        for path, mode in parsed.items()
    )
    return parsed


def _git_environment(home: Path) -> dict[str, str]:
    home.mkdir(exist_ok=True)
    return {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }


def _git(cwd: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        [str(SYSTEM_GIT), *arguments],
        cwd=cwd,
        env=_git_environment(cwd.parent / ".asset-git-home"),
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout


def _make_fixture(
    tmp_path: Path,
    *,
    omit: frozenset[str] = frozenset(),
    mode_overrides: dict[str, str] | None = None,
) -> AssetFixture:
    required_modes = _required_modes()
    modes = required_modes | (mode_overrides or {})
    repository = tmp_path / "asset-repository"
    _git(
        tmp_path,
        "init",
        "--object-format=sha1",
        "--initial-branch=main",
        str(repository),
    )
    _git(repository, "config", "user.name", "Privileged Asset Fixture")
    _git(repository, "config", "user.email", "assets@example.invalid")

    committed_bytes: dict[str, bytes] = {}
    for relative, git_mode in modes.items():
        if relative in omit:
            continue
        source = ROOT / relative
        assert source.is_file(), relative
        body = source.read_bytes()
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
        destination.chmod(0o755 if git_mode == "100755" else 0o644)
        committed_bytes[relative] = body

    _git(repository, "add", "--all")
    _git(
        repository,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "fixture: exact privileged release assets",
    )
    target = _git(repository, "rev-parse", "HEAD").decode("ascii").strip()
    assert re.fullmatch(r"[0-9a-f]{40}", target)
    assert (
        _git(repository, "rev-parse", "--show-object-format").decode("ascii").strip()
        == "sha1"
    )

    tree_entries: dict[str, tuple[str, str]] = {}
    raw_tree = _git(repository, "ls-tree", "-r", "-z", "--full-tree", target)
    for record in raw_tree.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_type, oid = metadata.split(b" ", 2)
        assert object_type == b"blob"
        tree_entries[raw_path.decode("ascii")] = (
            mode.decode("ascii"),
            oid.decode("ascii"),
        )
    assert set(tree_entries) == set(committed_bytes)

    parent = tmp_path / "asset-parent"
    parent.mkdir(mode=0o700)
    parent.chmod(0o700)
    destination = parent / f"release-assets-{target}"
    return AssetFixture(
        repository=repository,
        target=target,
        parent=parent,
        destination=destination,
        required_modes=required_modes,
        committed_bytes=committed_bytes,
        tree_entries=tree_entries,
    )


def _run_materializer(
    fixture: AssetFixture,
    *,
    target: str | None = None,
    parent: Path | None = None,
    destination: Path | None = None,
    python: Path | None = None,
    cwd: Path | None = None,
    extra_environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in ("BASH_ENV", "CDPATH", "ENV", "SSH_ORIGINAL_COMMAND"):
        environment.pop(name, None)
    if extra_environment:
        environment.update(extra_environment)
    environment.update(
        {
            "SEICHE_ALLOW_NON_ROOT_ASSET_TEST": "1",
            "SEICHE_ASSET_TEST_DESTINATION": str(destination or fixture.destination),
            "SEICHE_ASSET_TEST_PARENT": str(parent or fixture.parent),
            "SEICHE_ASSET_TEST_PYTHON": str(python or Path(sys.executable)),
            "SEICHE_ASSET_TEST_REPO": str(fixture.repository),
            "SEICHE_ASSET_TEST_TARGET": target or fixture.target,
            "SEICHE_DEPLOY_ASSET_TEST_ONLY": "1",
        }
    )
    return subprocess.run(
        ["/bin/bash", str(DEPLOY_WRAPPER)],
        cwd=cwd or Path("/"),
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def _assert_no_stage(parent: Path) -> None:
    assert not list(parent.glob(".release-assets.*"))


def _assert_unpublished(
    result: subprocess.CompletedProcess[str],
    fixture: AssetFixture,
    *,
    destination: Path | None = None,
    stage_parent: Path | None = None,
) -> None:
    selected_destination = destination or fixture.destination
    assert result.returncode != 0, result.stdout + result.stderr
    assert not os.path.lexists(selected_destination)
    _assert_no_stage(stage_parent or fixture.parent)


def _expected_manifest(fixture: AssetFixture) -> dict[str, object]:
    entries = []
    for path in sorted(fixture.committed_bytes):
        git_mode, oid = fixture.tree_entries[path]
        body = fixture.committed_bytes[path]
        entries.append(
            {
                "blob_oid": oid,
                "git_mode": git_mode,
                "path": path,
                "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body),
            }
        )
    return {
        "entries": entries,
        "git_object_format": "sha1",
        "schema": "seiche.signed-privileged-assets.v1",
        "target_sha": fixture.target,
    }


def _assert_exact_materialization(fixture: AssetFixture) -> None:
    destination = fixture.destination
    expected_files = set(fixture.committed_bytes) | {
        ".seiche-release-assets.json",
        ".target-sha",
    }
    observed_files = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }
    assert observed_files == expected_files
    assert not any(path.is_symlink() for path in destination.rglob("*"))

    expected_manifest = _expected_manifest(fixture)
    expected_manifest_bytes = (
        json.dumps(expected_manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    assert (destination / ".seiche-release-assets.json").read_bytes() == (
        expected_manifest_bytes
    )
    assert json.loads(expected_manifest_bytes) == expected_manifest
    assert (destination / ".target-sha").read_bytes() == (fixture.target + "\n").encode(
        "ascii"
    )

    for relative, body in fixture.committed_bytes.items():
        output = destination / relative
        assert output.read_bytes() == body
        metadata = output.stat()
        expected_mode = (
            0o755 if fixture.tree_entries[relative][0] == "100755" else 0o644
        )
        assert stat.S_ISREG(metadata.st_mode)
        assert metadata.st_nlink == 1
        assert metadata.st_uid == os.geteuid()
        assert metadata.st_gid == os.getegid()
        assert stat.S_IMODE(metadata.st_mode) == expected_mode

    for name in (".seiche-release-assets.json", ".target-sha"):
        metadata = (destination / name).stat()
        assert stat.S_ISREG(metadata.st_mode)
        assert metadata.st_nlink == 1
        assert metadata.st_uid == os.geteuid()
        assert metadata.st_gid == os.getegid()
        assert stat.S_IMODE(metadata.st_mode) == 0o600

    directories = [destination]
    directories.extend(path for path in destination.rglob("*") if path.is_dir())
    for directory in directories:
        metadata = directory.stat()
        assert stat.S_ISDIR(metadata.st_mode)
        assert metadata.st_uid == os.geteuid()
        assert metadata.st_gid == os.getegid()
        assert stat.S_IMODE(metadata.st_mode) == 0o700
    _assert_no_stage(fixture.parent)


def _python_probe(tmp_path: Path) -> tuple[Path, Path]:
    probe = tmp_path / "asset-test-python"
    argument_log = tmp_path / "asset-test-python.arguments"
    probe.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" >{shlex.quote(str(argument_log))}\n"
        f'exec {shlex.quote(sys.executable)} "$@"\n',
        encoding="utf-8",
    )
    probe.chmod(0o755)
    return probe, argument_log


@pytest.mark.skipif(
    os.geteuid() == 0, reason="asset-test mode intentionally rejects root"
)
def test_materializes_exact_git_tree_provenance_bytes_and_modes(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    probe, argument_log = _python_probe(tmp_path)

    result = _run_materializer(fixture, python=probe)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == f"{fixture.destination}\n"
    assert result.stderr == ""
    assert argument_log.read_text(encoding="utf-8").splitlines()[:3] == [
        "-I",
        "-B",
        "-",
    ]
    _assert_exact_materialization(fixture)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="asset-test mode intentionally rejects root"
)
def test_materializes_target_blobs_not_dirty_or_untracked_worktree(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    dirty_relative = "backend/seiche/nbs_trust.py"
    dirty = fixture.repository / dirty_relative
    dirty.write_bytes(b"dirty worktree bytes must never become root assets\n")
    untracked = fixture.repository / "ops" / "deploy" / "untracked-helper.sh"
    untracked.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    untracked.chmod(0o755)

    result = _run_materializer(fixture)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (fixture.destination / dirty_relative).read_bytes() == (
        fixture.committed_bytes[dirty_relative]
    )
    assert not (
        fixture.destination / untracked.relative_to(fixture.repository)
    ).exists()
    manifest = json.loads(
        (fixture.destination / ".seiche-release-assets.json").read_text(
            encoding="ascii"
        )
    )
    assert {entry["path"] for entry in manifest["entries"]} == set(
        fixture.committed_bytes
    )
    assert _git(fixture.repository, "status", "--porcelain")
    _assert_exact_materialization(fixture)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="asset-test mode intentionally rejects root"
)
def test_hostile_cwd_pythonpath_and_git_environment_cannot_execute_poison(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    poison = tmp_path / "poison"
    poison.mkdir()
    python_marker = tmp_path / "python-poison-ran"
    git_marker = tmp_path / "git-poison-ran"
    poison_body = f"open({str(python_marker)!r}, 'w').write('ran')\n"
    (poison / "sitecustomize.py").write_text(poison_body, encoding="utf-8")
    (poison / "subprocess.py").write_text(
        poison_body + "raise RuntimeError('poison subprocess imported')\n",
        encoding="utf-8",
    )
    poison_git = poison / "git"
    poison_git.write_text(
        '#!/bin/sh\n: >"$SEICHE_TEST_GIT_POISON_MARKER"\nexit 97\n',
        encoding="utf-8",
    )
    poison_git.chmod(0o755)
    poison_config = poison / "gitconfig"
    poison_config.write_text(
        f"[core]\n\tfsmonitor = {poison_git}\n",
        encoding="utf-8",
    )
    hostile_environment = {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(poison),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_GLOBAL": str(poison_config),
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": str(poison_git),
        "GIT_DIR": str(poison),
        "GIT_EXEC_PATH": str(poison),
        "GIT_OBJECT_DIRECTORY": str(poison),
        "GIT_TRACE": str(git_marker),
        "GIT_WORK_TREE": str(poison),
        "HOME": str(poison),
        "PATH": f"{poison}:/usr/bin:/bin",
        "PYTHONHOME": str(poison),
        "PYTHONPATH": str(poison),
        "SEICHE_TEST_GIT_POISON_MARKER": str(git_marker),
    }

    result = _run_materializer(
        fixture,
        cwd=poison,
        extra_environment=hostile_environment,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not python_marker.exists()
    assert not git_marker.exists()
    _assert_exact_materialization(fixture)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="asset-test mode intentionally rejects root"
)
def test_retry_never_replaces_an_existing_materialization(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    first = _run_materializer(fixture)
    assert first.returncode == 0, first.stdout + first.stderr
    destination_metadata = fixture.destination.stat()
    target_metadata = (fixture.destination / ".target-sha").stat()

    retry = _run_materializer(fixture)

    assert retry.returncode != 0
    assert "destination already exists; replacement is forbidden" in retry.stderr
    assert fixture.destination.stat().st_ino == destination_metadata.st_ino
    assert (fixture.destination / ".target-sha").stat().st_ino == target_metadata.st_ino
    _assert_exact_materialization(fixture)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="asset-test mode intentionally rejects root"
)
@pytest.mark.parametrize("target", ["not-a-commit", "f" * 40])
def test_rejects_invalid_or_nonexistent_target(tmp_path: Path, target: str) -> None:
    fixture = _make_fixture(tmp_path)

    result = _run_materializer(fixture, target=target)

    _assert_unpublished(result, fixture)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="asset-test mode intentionally rejects root"
)
def test_rejects_destination_outside_fixed_parent(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    outside = tmp_path / "outside-release-assets"

    result = _run_materializer(fixture, destination=outside)

    _assert_unpublished(result, fixture, destination=outside)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="asset-test mode intentionally rejects root"
)
def test_rejects_asset_parent_with_unsafe_mode(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    fixture.parent.chmod(0o755)

    result = _run_materializer(fixture)

    _assert_unpublished(result, fixture)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="asset-test mode intentionally rejects root"
)
def test_rejects_symlinked_asset_parent(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    real_parent = tmp_path / "real-asset-parent"
    fixture.parent.rename(real_parent)
    fixture.parent.symlink_to(real_parent, target_is_directory=True)

    result = _run_materializer(fixture)

    _assert_unpublished(result, fixture, stage_parent=real_parent)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="asset-test mode intentionally rejects root"
)
def test_rejects_required_executable_with_wrong_git_mode(tmp_path: Path) -> None:
    fixture = _make_fixture(
        tmp_path,
        mode_overrides={"ops/deploy/install-caddy.sh": "100644"},
    )
    assert fixture.required_modes["ops/deploy/install-caddy.sh"] == "100755"
    assert fixture.tree_entries["ops/deploy/install-caddy.sh"][0] == "100644"

    result = _run_materializer(fixture)

    _assert_unpublished(result, fixture)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="asset-test mode intentionally rejects root"
)
def test_rejects_missing_required_blob(tmp_path: Path) -> None:
    missing = "backend/seiche/nbs_trust.py"
    fixture = _make_fixture(tmp_path, omit=frozenset({missing}))
    assert missing in fixture.required_modes
    assert missing not in fixture.tree_entries

    result = _run_materializer(fixture)

    _assert_unpublished(result, fixture)
