from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    ROOT / ".github/workflows/railway-stateful-shadow.yml",
    ROOT / ".github/workflows/railway-stateful-cutover.yml",
    ROOT / ".github/workflows/railway-telegram.yml",
)


def _git(cwd: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_source_bundle_workflows_pin_the_detached_head() -> None:
    for path in WORKFLOWS:
        workflow = path.read_text(encoding="utf-8")
        source_var = "SOURCE_SHA" if "cutover" in path.name else "GITHUB_SHA"
        assert f'test "$(git rev-parse \'HEAD^{{commit}}\')" = "${source_var}"' in workflow
        assert 'git bundle create "$UPLOAD_ROOT/source.bundle" HEAD' in workflow
        assert (
            'git bundle create "$UPLOAD_ROOT/source.bundle" "$GITHUB_SHA"'
            not in workflow
        )
        assert 'git bundle verify "$UPLOAD_ROOT/source.bundle"' in workflow
        assert 'git bundle list-heads "$UPLOAD_ROOT/source.bundle"' in workflow
        assert f'"${source_var} HEAD"' in workflow


def test_detached_checkout_bundle_advertises_the_exact_commit(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    (repository / "payload.txt").write_text("exact source\n", encoding="utf-8")
    _git(repository, "add", "payload.txt")
    _git(
        repository,
        "-c",
        "user.name=Seiche Test",
        "-c",
        "user.email=seiche-test@example.invalid",
        "commit",
        "-m",
        "exact source",
    )
    expected_commit = _git(repository, "rev-parse", "HEAD^{commit}")
    expected_tree = _git(repository, "rev-parse", "HEAD^{tree}")
    _git(repository, "checkout", "--detach", expected_commit)

    raw_commit_bundle = tmp_path / "raw-commit.bundle"
    failed = subprocess.run(
        [
            "git",
            "bundle",
            "create",
            str(raw_commit_bundle),
            expected_commit,
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    assert "Refusing to create empty bundle" in failed.stderr
    assert not raw_commit_bundle.exists() or raw_commit_bundle.stat().st_size == 0

    bundle = tmp_path / "source.bundle"
    _git(repository, "bundle", "create", str(bundle), "HEAD")
    _git(repository, "bundle", "verify", str(bundle))
    assert _git(repository, "bundle", "list-heads", str(bundle)) == (
        f"{expected_commit} HEAD"
    )

    restored = tmp_path / "restored"
    _git(tmp_path, "clone", "--quiet", str(bundle), str(restored))
    assert _git(restored, "rev-parse", "HEAD^{commit}") == expected_commit
    assert _git(restored, "rev-parse", "HEAD^{tree}") == expected_tree
