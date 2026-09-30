"""Recovery controller updates must not misidentify or impersonate a runtime."""

import importlib.util
import os
from pathlib import Path
import subprocess
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "verify_recovery_source", ROOT / "ops/railway/verify_recovery_source.py"
)
assert spec and spec.loader
binding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(binding)


@pytest.fixture
def repository(tmp_path):
    key = tmp_path / "signer"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
    )
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True
        ).strip()

    git("config", "user.name", "Recovery test")
    git("config", "user.email", "recovery@example.test")
    git("config", "gpg.format", "ssh")
    git("config", "user.signingkey", str(key))
    policy = root / "ops/deploy/release-allowed-signers"
    policy.parent.mkdir(parents=True)
    policy.write_text("recovery@example.test " + key.with_suffix(".pub").read_text())
    git("add", ".")
    git("commit", "-q", "-S", "-m", "runtime")
    runtime = git("rev-parse", "HEAD")
    git("commit", "-q", "-S", "--allow-empty", "-m", "controller")
    controller = git("rev-parse", "HEAD")
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(root), str(remote)], check=True)
    git("remote", "add", "origin", str(remote))
    environment = {
        "GITHUB_REPOSITORY": "beepboop2025/seiche",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_SHA": controller,
        "RECOVERY_SOURCE_SHA": runtime,
        "REQUESTED_SOURCE_SHA": runtime,
    }
    return root, git, environment


def test_signed_previous_runtime_and_current_controller_are_distinct(repository):
    root, _, environment = repository
    assert binding.verify(root, environment) == {
        "runtime_source": environment["RECOVERY_SOURCE_SHA"],
        "workflow_source": environment["GITHUB_SHA"],
    }


def test_current_runtime_needs_no_override(repository):
    root, _, environment = repository
    environment.update(
        RECOVERY_SOURCE_SHA=environment["GITHUB_SHA"], REQUESTED_SOURCE_SHA=""
    )
    assert (
        binding.verify(root, environment)["runtime_source"] == environment["GITHUB_SHA"]
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("GITHUB_REF", "refs/heads/repair"),
        ("GITHUB_REPOSITORY", "untrusted/seiche"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_SHA", "main"),
        ("RECOVERY_SOURCE_SHA", "main"),
        ("REQUESTED_SOURCE_SHA", ""),
        ("REQUESTED_SOURCE_SHA", "f" * 40),
    ],
)
def test_unreviewed_or_ambiguous_sources_are_refused(repository, field, value):
    root, _, environment = repository
    environment[field] = value
    with pytest.raises(ValueError):
        binding.verify(root, environment)


def test_moved_main_and_dirty_checkout_are_refused(repository):
    root, git, environment = repository
    git("push", "-q", "origin", environment["RECOVERY_SOURCE_SHA"] + ":main", "--force")
    with pytest.raises(ValueError, match="main changed"):
        binding.verify(root, environment)
    git("push", "-q", "origin", "HEAD:main")
    (root / "ops/deploy/release-allowed-signers").write_text("different\n")
    with pytest.raises(ValueError, match="tracked changes"):
        binding.verify(root, environment)


def test_unsigned_controller_is_refused(repository):
    root, git, environment = repository
    git("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "unsigned")
    environment["GITHUB_SHA"] = git("rev-parse", "HEAD")
    git("push", "-q", "origin", "HEAD:main")
    with pytest.raises(subprocess.CalledProcessError):
        binding.verify(root, environment)


@pytest.mark.parametrize("signed", [False, True])
def test_unsigned_ancestor_and_signed_nonancestor_are_refused(repository, signed):
    root, git, environment = repository
    controller = environment["GITHUB_SHA"]
    git("checkout", "-q", "--detach", environment["RECOVERY_SOURCE_SHA"])
    args = [
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "other",
    ]
    if signed:
        args.append("-S")
    git(*args)
    runtime = git("rev-parse", "HEAD")
    if not signed:
        git("commit", "-q", "-S", "--allow-empty", "-m", "signed descendant")
        controller = git("rev-parse", "HEAD")
        git("push", "-q", "origin", "HEAD:main", "--force")
    git("checkout", "-q", "--detach", controller)
    environment.update(
        GITHUB_SHA=controller, RECOVERY_SOURCE_SHA=runtime, REQUESTED_SOURCE_SHA=runtime
    )
    with pytest.raises(subprocess.CalledProcessError):
        binding.verify(root, environment)


@pytest.mark.parametrize("observed", ["a" * 40, "b" * 40])
def test_monitor_binds_both_edges_to_the_requested_runtime(monkeypatch, observed):
    workflow = (ROOT / ".github/workflows/railway-stateful-recovery.yml").read_text()
    start = workflow.index("          required = {\n")
    end = workflow.index("          deployment = required[", start)
    program = textwrap.dedent(workflow[start:end])
    monkeypatch.setenv("RECOVERY_SOURCE_SHA", "a" * 40)
    headers = {
        "x-seiche-release-sha": observed,
        "x-seiche-railway-deployment": "deployment",
        "x-seiche-railway-authority": "production",
    }
    namespace = {"os": os, "origin": headers, "public": dict(headers)}
    if observed == "a" * 40:
        exec(compile(program, "monitor-identity", "exec"), namespace)
    else:
        with pytest.raises(SystemExit, match="identities differ"):
            exec(compile(program, "monitor-identity", "exec"), namespace)
