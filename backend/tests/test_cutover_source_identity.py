"""Execute the cutover's real Git identity checks across distinct revisions."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import textwrap

import pytest


WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github/workflows/railway-stateful-cutover.yml"
)
IDENTITY_STEP = (
    "Authenticate the signed application source independently of the workflow"
)


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()


def _identity_script() -> str:
    workflow = WORKFLOW.read_text()
    blocks = workflow.split(f"      - name: {IDENTITY_STEP}\n")[1:]
    assert len(blocks) == 2
    scripts = [
        textwrap.dedent(
            block.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
        )
        for block in blocks
    ]
    assert scripts[0] == scripts[1]
    return scripts[0]


@pytest.fixture
def signed_repository(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "source"
    repository.mkdir()
    key = tmp_path / "signer"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
    )
    _git(repository, "init", "--initial-branch=main")
    for name, value in {
        "user.name": "Release Test",
        "user.email": "release@example.invalid",
        "gpg.format": "ssh",
        "user.signingkey": str(key),
        "commit.gpgsign": "true",
    }.items():
        _git(repository, "config", name, value)
    signer_file = repository / "ops/deploy/release-allowed-signers"
    signer_file.parent.mkdir(parents=True)
    signer_file.write_text(
        "release@example.invalid " + key.with_suffix(".pub").read_text()
    )
    (repository / "payload").write_text("application bytes\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "signed application")
    source = _git(repository, "rev-parse", "HEAD")
    (repository / "workflow").write_text("reviewed orchestration repair\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "signed controller")
    return repository, source, _git(repository, "rev-parse", "HEAD")


def _run_identity(
    repository: Path, source: str, controller: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", _identity_script()],
        cwd=repository,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": controller,
            "SOURCE_SHA": source,
            "GITHUB_WORKSPACE": str(repository),
            "GITHUB_STEP_SUMMARY": str(repository.parent / "summary"),
        },
    )


def test_signed_ancestor_keeps_application_and_controller_distinct(
    signed_repository,
) -> None:
    repository, source, controller = signed_repository
    result = _run_identity(repository, source, controller)
    assert result.returncode == 0, result.stderr
    assert _git(repository, "rev-parse", "HEAD") == source
    summary = (repository.parent / "summary").read_text()
    assert f"Reviewed workflow commit: `{controller}`" in summary
    assert f"Signed application commit: `{source}`" in summary


def test_unsigned_ancestor_cannot_be_selected(signed_repository) -> None:
    repository, _, _ = signed_repository
    _git(
        repository,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--allow-empty",
        "-m",
        "unsigned",
    )
    source = _git(repository, "rev-parse", "HEAD")
    _git(repository, "commit", "--allow-empty", "-m", "signed controller")
    controller = _git(repository, "rev-parse", "HEAD")
    assert _run_identity(repository, source, controller).returncode != 0
    assert _git(repository, "rev-parse", "HEAD") == controller


def test_signed_unrelated_source_cannot_be_selected(signed_repository) -> None:
    repository, source, controller = signed_repository
    _git(repository, "checkout", "--detach", source)
    _git(repository, "commit", "--allow-empty", "-m", "separate lineage")
    other = _git(repository, "rev-parse", "HEAD")
    _git(repository, "checkout", "--detach", controller)
    assert _run_identity(repository, other, controller).returncode != 0
    assert _git(repository, "rev-parse", "HEAD") == controller


def test_tree_check_works_outside_the_checkout(signed_repository) -> None:
    repository, source, _ = signed_repository
    _git(repository, "checkout", "--detach", source)
    workflow = WORKFLOW.read_text()
    start = workflow.index('          test "$(git -C "$GITHUB_WORKSPACE" rev-parse')
    script = textwrap.dedent(
        workflow[start : workflow.index("          railway variable set", start)]
    )
    output = repository.parent / "outputs"
    output.write_text(f"tree={_git(repository, 'rev-parse', 'HEAD^{tree}')}\n")
    environment = {
        **os.environ,
        "GITHUB_WORKSPACE": str(repository),
        "GITHUB_OUTPUT": str(output),
    }
    passed = subprocess.run(
        ["bash", "-ec", script], cwd=repository.parent, env=environment
    )
    assert passed.returncode == 0
    output.write_text("tree=" + "0" * 40 + "\n")
    failed = subprocess.run(
        ["bash", "-ec", script], cwd=repository.parent, env=environment
    )
    assert failed.returncode != 0
