"""Execute the cutover's Git identity and Railway restart proof checks."""

from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import textwrap

import pytest

from seiche import stateful_control as control
from seiche import stateful_migration as migration


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


def _run_candidate_identity(
    repository: Path, source: str, controller: str, candidate: str, **overrides
) -> subprocess.CompletedProcess[str]:
    workflow = WORKFLOW.read_text()
    block = workflow.split('candidate_workflow_sha=$(RUN_JSON=', 1)[1]
    program = textwrap.dedent(
        block.split("<<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    )
    run = {
        "id": 123,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "head_sha": candidate,
        "path": ".github/workflows/railway-stateful-cutover.yml",
        "head_repository": {"full_name": "beepboop2025/seiche"},
        **overrides,
    }
    return subprocess.run(
        ["python3", "-I", "-S", "-c", program],
        cwd=repository, capture_output=True, text=True,
        env={
            **os.environ, "RUN_JSON": json.dumps(run), "EXPECTED_SHA": controller,
            "SOURCE_SHA": source, "CANDIDATE_RUN_ID": "123",
            "GITHUB_WORKSPACE": str(repository),
        },
    )


def test_activation_repair_retains_signed_ancestor_candidate(signed_repository) -> None:
    repository, source, candidate = signed_repository
    _git(repository, "commit", "--allow-empty", "-m", "signed activation repair")
    controller = _git(repository, "rev-parse", "HEAD")
    result = _run_candidate_identity(repository, source, controller, candidate)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == candidate
    workflow = WORKFLOW.read_text().split("Recover and validate the exact candidate chain", 1)[1]
    assert '--signer-digest "$candidate_workflow_sha"' in workflow
    assert '--source-digest "$candidate_workflow_sha"' in workflow


@pytest.mark.parametrize("kind", ["unsigned", "unrelated", "before_source"])
def test_activation_rejects_untrusted_candidate_lineage(signed_repository, kind) -> None:
    repository, source, controller = signed_repository
    if kind == "unsigned":
        _git(repository, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "unsigned")
        candidate = _git(repository, "rev-parse", "HEAD")
        _git(repository, "commit", "--allow-empty", "-m", "signed repair")
        controller = _git(repository, "rev-parse", "HEAD")
    elif kind == "unrelated":
        _git(repository, "checkout", "--detach", source)
        _git(repository, "commit", "--allow-empty", "-m", "different signed lineage")
        candidate = _git(repository, "rev-parse", "HEAD")
    else:
        candidate, source = source, controller
    assert _run_candidate_identity(repository, source, controller, candidate).returncode != 0


@pytest.mark.parametrize("overrides", [
    {"id": 124}, {"conclusion": "failure"}, {"status": "in_progress"},
    {"head_branch": "unreviewed"}, {"event": "pull_request"},
    {"path": ".github/workflows/unrelated.yml"},
    {"head_repository": {"full_name": "other/repository"}},
])
def test_activation_rejects_foreign_or_unsuccessful_candidate_run(signed_repository, overrides) -> None:
    repository, source, controller = signed_repository
    assert _run_candidate_identity(repository, source, controller, controller, **overrides).returncode != 0


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


def _restart_script(marker: str) -> str:
    block = WORKFLOW.read_text().split(marker, 1)[1]
    return textwrap.dedent(
        block.split("<<'PY'", 1)[1].split("\n", 1)[1].split("\n          PY", 1)[0]
    )


@pytest.mark.parametrize("same_replica", [True, False])
def test_restarted_runtime_accepts_retained_or_replaced_uuid(
    tmp_path: Path,
    monkeypatch,
    capsys,
    same_replica: bool,
) -> None:
    deployment = "11111111-1111-4111-8111-111111111111"
    created = "22222222-2222-4222-8222-222222222222"
    reused = created if same_replica else "33333333-3333-4333-8333-333333333333"
    runtime = tmp_path / "runtime.json"
    value = {
        "id": deployment,
        "status": "SUCCESS",
        "instances": [{"id": reused, "status": "RUNNING"}],
    }
    runtime.write_text(json.dumps({"data": {"deployment": value}}))
    monkeypatch.setenv("RUNTIME", str(runtime))
    monkeypatch.setenv("DEPLOYMENT_ID", deployment)
    monkeypatch.setenv("CREATED_REPLICA_ID", created)
    script = _restart_script("reused_replica_id=$(RUNTIME=")
    exec(compile(script, str(WORKFLOW), "exec"), {})
    assert capsys.readouterr().out.strip() == reused
    value["instances"].append({"id": created, "status": "RUNNING"})
    runtime.write_text(json.dumps({"data": {"deployment": value}}))
    with pytest.raises(SystemExit):
        exec(compile(script, str(WORKFLOW), "exec"), {})


@pytest.mark.parametrize("same_replica", [True, False])
@pytest.mark.parametrize("fresh_runtime", [True, False])
def test_restart_proof_requires_new_runtime_even_when_old_log_is_delayed(
    tmp_path: Path,
    monkeypatch,
    same_replica: bool,
    fresh_runtime: bool,
) -> None:
    commit, request = "a" * 40, "b" * 64
    deployment = "11111111-1111-4111-8111-111111111111"
    created = "22222222-2222-4222-8222-222222222222"
    reused = created if same_replica else "33333333-3333-4333-8333-333333333333"
    evidence = {
        "request": {"id": request, "commit": commit},
        "railway": {"deployment_id": deployment},
    }
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(migration.canonical_document(evidence))
    logs = tmp_path / "logs.ndjson"
    rows = []
    for lifecycle, replica, started, logged in (
        ("created", created, "2026-09-05T03:00:00Z", "2026-09-05T03:01:00Z"),
        (
            "reused",
            reused,
            "2026-09-05T03:06:00Z" if fresh_runtime else "2026-09-05T03:00:00Z",
            "2026-09-05T03:07:00Z",
        ),
    ):
        rows.append(
            json.dumps(
                {
                    "timestamp": logged,
                    "message": control.render_log_result(
                        evidence,
                        kind="candidate",
                        lifecycle=lifecycle,
                        request_id=request,
                        environment={
                            "SEICHE_RELEASE_SHA": commit,
                            "RAILWAY_DEPLOYMENT_ID": deployment,
                            "RAILWAY_REPLICA_ID": replica,
                        },
                        runtime_started_at=started,
                    ),
                }
            )
        )
    logs.write_text("\n".join(rows) + "\n")
    for key, value in {
        "SOURCE_SHA": commit,
        "REQUEST_ID": request,
        "DEPLOYMENT_ID": deployment,
        "CREATED_REPLICA_ID": created,
        "REUSED_REPLICA_ID": reused,
        "NOT_BEFORE": "2026-09-05T02:59:00Z",
        "RESTART_NOT_BEFORE": "2026-09-05T03:05:00Z",
        "LOG_PATH": str(logs),
        "RECEIPT_PATH": str(receipt),
    }.items():
        monkeypatch.setenv(key, value)
    script = _restart_script('PYTHONPATH=backend LOG_PATH="$reused_logs"')
    if fresh_runtime:
        exec(compile(script, str(WORKFLOW), "exec"), {})
    else:
        with pytest.raises(SystemExit, match="fresh restart"):
            exec(compile(script, str(WORKFLOW), "exec"), {})
