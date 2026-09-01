"""Focused contracts for the off-host Railway release gate."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import subprocess
import tarfile
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "ops" / "railway" / "run-gate.py"
VERIFIER_PATH = ROOT / "ops" / "deploy" / "seiche-remote-gate-verify.py"
WORKFLOW = ROOT / ".github" / "workflows" / "railway-release-gate.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "market-platform-ci.yml"
POLLER = ROOT / "ops" / "deploy" / "seiche-release-poll.sh"
RAILWAY_CONFIG = ROOT / "ops" / "railway" / "railway.gate.json"
RAILWAY_DOCKERFILE = ROOT / "ops" / "railway" / "Dockerfile.gate"
PINNED_PYTHON_DOCKERFILES = tuple(
    ROOT / "ops" / "railway" / name
    for name in (
        "Dockerfile.gate",
        "Dockerfile.snapshot",
        "Dockerfile.stateful",
        "Dockerfile.telegram",
    )
)
PREFLIGHT_NAME = "Preflight required Railway configuration"


def _workflow_step(workflow: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    start = workflow.index(marker)
    end = workflow.find("\n      - ", start + len(marker))
    assert end != -1
    return workflow[start:end]


def _step_script(step: str) -> str:
    marker = "        run: |\n"
    body = step.split(marker, maxsplit=1)[1]
    return "\n".join(line[10:] if line else "" for line in body.splitlines())


def _expected_preflight_script(required: tuple[str, ...]) -> str:
    return "\n".join(
        (
            "set -euo pipefail",
            "required=(",
            *(f"  {name}" for name in required),
            ")",
            "missing=()",
            'for name in "${required[@]}"; do',
            '  if [[ -z "${!name:-}" ]]; then',
            '    missing+=("$name")',
            "  fi",
            "done",
            "if ((${#missing[@]})); then",
            "  printf '%s\\n' 'Missing required Railway configuration:' >&2",
            "  printf '%s\\n' \"${missing[@]}\" >&2",
            "  exit 1",
            "fi",
        )
    )


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner():
    return _load(RUNNER_PATH, "seiche_railway_gate_runner")


@pytest.fixture(scope="module")
def verifier():
    return _load(VERIFIER_PATH, "seiche_remote_gate_verifier")


@pytest.fixture
def safe_runtime_root():
    account_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=True)
    with tempfile.TemporaryDirectory(
        prefix=".seiche-railway-gate-test-",
        dir=account_home,
    ) as raw_root:
        root = Path(raw_root)
        root.chmod(0o700)
        yield root


def _request(runner, archive: Path) -> dict[str, str]:
    return {
        "schema": runner.REQUEST_SCHEMA,
        "repository": runner.REPOSITORY,
        "workflow": runner.WORKFLOW,
        "source_ref": runner.SOURCE_REF,
        "commit": "a" * 40,
        "tree": "b" * 40,
        "source_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "request_id": "c" * 64,
        "runner_image": runner.RUNNER_IMAGE,
        "install_command": runner.INSTALL_COMMAND,
        "test_command": runner.TEST_COMMAND,
    }


def _railway_environment() -> dict[str, str]:
    return {
        "RAILWAY_DEPLOYMENT_ID": "11111111-1111-4111-8111-111111111111",
        "RAILWAY_PROJECT_ID": "22222222-2222-4222-8222-222222222222",
        "RAILWAY_ENVIRONMENT_ID": "33333333-3333-4333-8333-333333333333",
        "RAILWAY_SERVICE_ID": "44444444-4444-4444-8444-444444444444",
        "RAILWAY_REPLICA_REGION": "asia-southeast1",
    }


def _source_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    source_root = tmp_path / "workspace"
    tracked = source_root / "backend" / "tests" / "test_exact.py"
    tracked.parent.mkdir(parents=True)
    body = b"def test_exact():\n    assert True\n"
    tracked.write_bytes(body)
    tracked.chmod(0o444)
    tracked.parent.chmod(0o555)
    tracked.parent.parent.chmod(0o555)
    source_root.chmod(0o555)
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, mode="w") as bundle:
        member = tarfile.TarInfo("backend/tests/test_exact.py")
        member.mode = 0o644
        member.size = len(body)
        bundle.addfile(member, io.BytesIO(body))
    return archive, source_root, tracked


def _remote_result(runner, monkeypatch, tmp_path: Path) -> dict[str, object]:
    archive = tmp_path / "source.tar"
    archive.write_bytes(b"exact git archive fixture\n")
    request = _request(runner, archive)
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    loaded = runner.load_request(request_path, archive)
    monkeypatch.setattr(runner, "dependency_snapshot_sha256", lambda: "d" * 64)
    monkeypatch.setattr(runner.platform, "python_version", lambda: "3.12.11")
    return runner.build_result(
        loaded,
        {
            "passed": 2785,
            "skipped": 1,
            "subtests": 118,
            "duration_seconds": 1097.4,
        },
        "2026-08-23T01:02:03Z",
        "2026-08-23T01:20:20Z",
        _railway_environment(),
    )


def test_runner_binds_request_archive_and_railway_identity(
    runner, monkeypatch, tmp_path
):
    result = _remote_result(runner, monkeypatch, tmp_path)

    assert result["schema"] == "seiche.railway-gate-result.v1"
    assert result["commit"] == "a" * 40
    assert result["tree"] == "b" * 40
    assert (
        result["railway_deployment_id"]
        == _railway_environment()["RAILWAY_DEPLOYMENT_ID"]
    )
    assert result["tests"] == {
        "passed": 2785,
        "skipped": 1,
        "subtests": 118,
        "duration_seconds": 1097.4,
    }
    canonical = runner.canonical_json(result)
    assert canonical.endswith(b"\n")
    assert json.loads(canonical) == result


def test_runner_rejects_source_bytes_that_do_not_match_request(runner, tmp_path):
    archive = tmp_path / "source.tar"
    archive.write_bytes(b"reviewed bytes\n")
    request = _request(runner, archive)
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    archive.write_bytes(b"different bytes\n")

    with pytest.raises(SystemExit):
        runner.load_request(request_path, archive)


def test_runner_verifies_every_extracted_tracked_file(runner, tmp_path):
    archive, source_root, _tracked = _source_fixture(tmp_path)

    runner.verify_extracted_source(
        archive,
        source_root,
        expected_uid=runner.os.getuid(),
    )


def test_runner_binds_read_only_git_commit_and_tree(runner, tmp_path):
    source_root = tmp_path / "workspace"
    source_root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(source_root)], check=True)
    subprocess.run(
        ["git", "-C", str(source_root), "config", "user.name", "Gate Fixture"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(source_root),
            "config",
            "user.email",
            "gate-fixture@example.invalid",
        ],
        check=True,
    )
    tracked = source_root / "backend" / "tests" / "test_exact.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("def test_exact():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source_root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(source_root), "commit", "--quiet", "-m", "fixture"],
        check=True,
    )
    tracked.write_text("def test_exact():\n    assert 1 == 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source_root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(source_root), "commit", "--quiet", "-m", "second"],
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "-C", str(source_root), "rev-parse", "HEAD^{tree}"], text=True
    ).strip()
    parent = subprocess.check_output(
        ["git", "-C", str(source_root), "rev-parse", "HEAD^"], text=True
    ).strip()
    subprocess.run(["chmod", "-R", "a-w", str(source_root)], check=True)
    try:
        runner.verify_git_identity(
            source_root,
            {"commit": commit, "tree": tree},
            expected_uid=runner.os.getuid(),
        )
        with pytest.raises(SystemExit):
            runner.verify_git_identity(
                source_root,
                {"commit": commit, "tree": "f" * 40},
                expected_uid=runner.os.getuid(),
            )
        subprocess.run(["chmod", "-R", "u+w", str(source_root)], check=True)
        parent_object = source_root / ".git" / "objects" / parent[:2] / parent[2:]
        parent_object.unlink()
        subprocess.run(["chmod", "-R", "a-w", str(source_root)], check=True)
        with pytest.raises(SystemExit):
            runner.verify_git_identity(
                source_root,
                {"commit": commit, "tree": tree},
                expected_uid=runner.os.getuid(),
            )
    finally:
        subprocess.run(["chmod", "-R", "u+w", str(source_root)], check=True)


def test_runner_rechecks_tracked_bytes_after_tests(runner, tmp_path):
    archive, source_root, tracked = _source_fixture(tmp_path)
    tracked.chmod(0o644)
    tracked.write_bytes(b"def test_exact():\n    assert None\n")
    tracked.chmod(0o444)

    with pytest.raises(SystemExit):
        runner.verify_extracted_source(
            archive,
            source_root,
            expected_uid=runner.os.getuid(),
        )


def test_runner_parses_pytest_subtests_success_suffix(runner):
    assert runner.parse_pytest_summary(
        "2785 passed, 1 skipped, 118 subtests passed in 1097.40s\n"
    ) == {
        "passed": 2785,
        "skipped": 1,
        "subtests": 118,
        "duration_seconds": 1097.4,
    }


def test_runner_refuses_root_or_service_environment_pytest_overrides(
    runner, monkeypatch, tmp_path, safe_runtime_root
):
    actual_uid = runner.os.getuid()
    actual_gid = runner.os.getgid()
    monkeypatch.setattr(runner.os, "getuid", lambda: 0)
    monkeypatch.setattr(runner.os, "getgid", lambda: 0)
    with pytest.raises(SystemExit):
        runner.validate_runtime_identity()
    monkeypatch.setattr(runner.os, "getuid", lambda: runner.RUN_UID)
    monkeypatch.setattr(runner.os, "getgid", lambda: runner.RUN_GID)
    runner.validate_runtime_identity()
    monkeypatch.setattr(runner.os, "getuid", lambda: actual_uid)
    monkeypatch.setattr(runner.os, "getgid", lambda: actual_gid)

    monkeypatch.setenv("PYTEST_ADDOPTS", "-k one_test")
    monkeypatch.setenv("PYTEST_PLUGINS", "hostile_plugin")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "ambient-pythonpath"))
    source_root = tmp_path / "workspace"
    source_package = source_root / "backend" / "seiche"
    source_package.mkdir(parents=True)
    (source_package / "__init__.py").write_text("", encoding="utf-8")
    (source_package / "config.py").write_text(
        "import os\nfrom pathlib import Path\n"
        'DATA_DIR = Path(os.environ["SEICHE_RUNTIME_DATA_DIR"])\n',
        encoding="utf-8",
    )
    runtime_root = safe_runtime_root / "runtime"
    runtime_root.mkdir(mode=0o700)

    environment = runner.build_test_environment(
        source_root,
        runtime_root=runtime_root,
    )
    assert "PYTEST_ADDOPTS" not in environment
    assert "PYTEST_PLUGINS" not in environment
    assert environment["PATH"] == "/usr/local/bin:/usr/bin:/bin"
    assert environment["PYTHONPATH"] == str(source_root / "backend")
    assert environment["PYTHONSAFEPATH"] == "1"
    assert environment["HOME"] == str(runtime_root)
    assert environment["TMPDIR"] == str(runtime_root / "tmp")
    assert environment["XDG_CACHE_HOME"] == str(runtime_root / "xdg-cache")
    assert environment["SEICHE_RUNTIME_DATA_DIR"] == str(runtime_root / "data")
    assert environment["SEICHE_VALIDATION_DIR"] == str(
        runtime_root / "data" / "market-validation"
    )
    assert runtime_root.stat().st_mode & 0o777 == 0o700
    assert (runtime_root / "pytest-cache").stat().st_mode & 0o777 == 0o700
    assert (runtime_root / "data").stat().st_mode & 0o777 == 0o700
    assert (runtime_root / "tmp").stat().st_mode & 0o777 == 0o700
    assert (runtime_root / "xdg-cache").stat().st_mode & 0o777 == 0o700
    runner.verify_test_import(source_root, environment)

    poison = tmp_path / "poison" / "seiche"
    poison.mkdir(parents=True)
    (poison / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        runner.verify_test_import(
            source_root,
            environment | {"PYTHONPATH": str(poison.parent)},
        )
    with pytest.raises(SystemExit):
        runner.verify_test_import(
            source_root,
            environment | {"TMPDIR": "/tmp"},
        )

    unsafe_parent = safe_runtime_root / "unsafe-parent"
    unsafe_parent.mkdir(mode=0o777)
    unsafe_parent.chmod(0o777)
    try:
        with pytest.raises(SystemExit):
            runner.build_test_environment(
                source_root,
                runtime_root=unsafe_parent / "runtime",
            )
    finally:
        unsafe_parent.chmod(0o700)


def test_runner_records_the_exact_source_and_external_runtime_contract(
    runner, verifier
):
    expected = (
        "HOME=/var/lib/seiche-railway-gate-runtime "
        "TMPDIR=/var/lib/seiche-railway-gate-runtime/tmp "
        "PYTHONPATH=/workspace/backend "
        "SEICHE_RUNTIME_DATA_DIR=/var/lib/seiche-railway-gate-runtime/data "
        "SEICHE_VALIDATION_DIR=/var/lib/seiche-railway-gate-runtime/data/market-validation "
        "python -P -m pytest backend/tests -q "
        "--memray -o faulthandler_timeout=300 "
        "-o cache_dir=/var/lib/seiche-railway-gate-runtime/pytest-cache"
    )

    assert runner.TEST_COMMAND == expected
    assert verifier.TEST_COMMAND == expected
    assert tuple(runner.PYTEST_ARGUMENTS) == (
        "-P",
        "-m",
        "pytest",
        "backend/tests",
        "-q",
        "--memray",
        "-o",
        "faulthandler_timeout=300",
        "-o",
        "cache_dir=/var/lib/seiche-railway-gate-runtime/pytest-cache",
    )


def test_host_wraps_only_exact_remote_result(runner, verifier, monkeypatch, tmp_path):
    remote = _remote_result(runner, monkeypatch, tmp_path)
    source_digest = str(remote["source_archive_sha256"])

    validated = verifier.validate_remote_receipt(
        remote,
        target="a" * 40,
        tree="b" * 40,
        source_archive_sha256=source_digest,
    )
    local = verifier.render_local_receipt(
        validated,
        artifact_digest="sha256:" + "e" * 64,
        artifact_receipt_sha256="f" * 64,
    )

    assert local["schema"] == "seiche.release-receipt.v2"
    assert local["gate_provider"] == "railway"
    assert local["remote"]["artifact_digest"] == "sha256:" + "e" * 64
    assert local["remote"]["railway_deployment_id"] == remote["railway_deployment_id"]


@pytest.mark.parametrize("tamper", ("commit", "tree", "source", "extra"))
def test_host_fails_closed_on_remote_receipt_tampering(
    runner, verifier, monkeypatch, tmp_path, tamper
):
    remote = _remote_result(runner, monkeypatch, tmp_path)
    expected_source = str(remote["source_archive_sha256"])
    if tamper == "commit":
        remote["commit"] = "9" * 40
    elif tamper == "tree":
        remote["tree"] = "8" * 40
    elif tamper == "source":
        remote["source_archive_sha256"] = "7" * 64
    else:
        remote["unreviewed"] = True

    with pytest.raises(SystemExit):
        verifier.validate_remote_receipt(
            remote,
            target="a" * 40,
            tree="b" * 40,
            source_archive_sha256=expected_source,
        )


def test_host_defers_only_a_recognized_missing_exact_sha_artifact(verifier):
    assert verifier.missing_artifact_error("request failed: not found [http 404]:")
    assert verifier.missing_artifact_error("registry: manifest unknown")
    assert not verifier.missing_artifact_error("unauthorized")
    assert not verifier.missing_artifact_error("dial tcp: network unreachable")


def test_host_registry_timeout_is_a_hard_failure(verifier, monkeypatch):
    def timeout(*_args, **_kwargs):
        raise verifier.subprocess.TimeoutExpired(["regctl"], 30)

    monkeypatch.setattr(verifier.subprocess, "run", timeout)
    with pytest.raises(SystemExit) as failure:
        verifier.resolve_artifact_tag("ghcr.io/example/gate:sha-deadbeef", {})
    assert failure.value.code == 1


def test_host_requires_exact_canonical_receipt_bytes(verifier):
    payload = {"schema": "fixture", "value": 1}
    assert verifier.load_canonical_receipt(verifier.canonical_json(payload)) == payload
    with pytest.raises(SystemExit):
        verifier.load_canonical_receipt(b'{"value": 1, "schema": "fixture"}\n')


def test_host_public_verifier_has_no_registry_or_api_credential(verifier, tmp_path):
    environment = verifier.anonymous_environment(tmp_path)

    assert environment["GH_TOKEN"] == verifier.PUBLIC_OCI_GH_TOKEN
    assert environment["GH_TOKEN"] == "public-oci-bundle-verification-no-api"
    assert "GITHUB_TOKEN" not in environment
    assert json.loads(
        (Path(environment["DOCKER_CONFIG"]) / "config.json").read_text()
    ) == {"auths": {}}


def test_railway_configuration_preflight_is_first_and_fails_with_names_only():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    preflight = _workflow_step(workflow, PREFLIGHT_NAME)
    checkout = _workflow_step(workflow, "Check out the exact event identity")

    steps_start = workflow.index("    steps:\n")
    assert workflow.index("      - ", steps_start) == workflow.index(preflight)
    assert workflow.index(preflight) < workflow.index(checkout)
    assert "on:\n  push:\n    branches: [main]" in workflow
    required = (
        "RAILWAY_TOKEN",
        "RAILWAY_PROJECT_ID",
        "RAILWAY_ENVIRONMENT_ID",
        "RAILWAY_SERVICE_ID",
    )
    assert preflight.count("${{ secrets.") == len(required)
    for name in required:
        assert f"          {name}: ${{{{ secrets.{name} }}}}" in preflight
    script = _step_script(preflight)
    assert script == _expected_preflight_script(required)
    assert "if:" not in preflight
    assert "uses:" not in preflight
    assert "continue-on-error" not in preflight

    fake_configuration = {
        "RAILWAY_TOKEN": "fake-token-must-not-be-printed",
        "RAILWAY_PROJECT_ID": "fake-project-must-not-be-printed",
        "RAILWAY_ENVIRONMENT_ID": "fake-environment-must-not-be-printed",
        "RAILWAY_SERVICE_ID": "fake-service-must-not-be-printed",
    }
    missing = ("RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID")
    environment = {"PATH": "/usr/bin:/bin", **fake_configuration}
    for name in missing:
        environment.pop(name)

    failure = subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert failure.returncode == 1
    assert failure.stdout == ""
    assert failure.stderr.splitlines() == [
        "Missing required Railway configuration:",
        *missing,
    ]
    output = failure.stdout + failure.stderr
    assert all(value not in output for value in fake_configuration.values())

    success = subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", **fake_configuration},
    )
    assert success.returncode == 0
    assert success.stdout == success.stderr == ""

    ci_workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert '"integrations/hermes/**"' in ci_workflow
    assert '".github/workflows/railway-release-gate.yml"' in ci_workflow
    assert "backend/tests/test_cfets_rights_boundary.py" in ci_workflow
    assert "backend/tests/test_railway_release_gate.py" in ci_workflow


def test_controller_defaults_remote_and_never_falls_back_automatically():
    poller = POLLER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    runner_source = RUNNER_PATH.read_text(encoding="utf-8")
    deployment_wait = _workflow_step(
        workflow, "Wait for Railway to finish the full gate"
    )
    result_extraction = _workflow_step(
        workflow, "Extract and independently validate the exact Railway result"
    )
    dockerfile = RAILWAY_DOCKERFILE.read_text(encoding="utf-8")
    config = json.loads(RAILWAY_CONFIG.read_text(encoding="utf-8"))

    assert (
        'LOCAL_GATE_BREAK_GLASS="${SEICHE_CONTROL_LOCAL_GATE_BREAK_GLASS:-0}"' in poller
    )
    assert 'if [ "$LOCAL_GATE_BREAK_GLASS" = 1 ]; then' in poller
    assert 'install_remote_gate_receipt "$GATE_RECEIPT"' in poller
    assert "local gate was not run automatically" in poller
    assert "is still pending; production unchanged" in poller
    assert "REMOTE_GATE_PENDING_MAX_SECONDS" in poller
    assert "SEICHE_CONTROL_LOCAL_GATE_BREAK_GLASS=1" in poller
    remote_test_command = (
        "HOME=/var/lib/seiche-railway-gate-runtime "
        "TMPDIR=/var/lib/seiche-railway-gate-runtime/tmp "
        "PYTHONPATH=/workspace/backend "
        "SEICHE_RUNTIME_DATA_DIR=/var/lib/seiche-railway-gate-runtime/data "
        "SEICHE_VALIDATION_DIR=/var/lib/seiche-railway-gate-runtime/data/market-validation "
        "python -P -m pytest backend/tests -q "
        "--memray -o faulthandler_timeout=300 "
        "-o cache_dir=/var/lib/seiche-railway-gate-runtime/pytest-cache"
    )
    assert workflow.count(remote_test_command) >= 1
    assert f'REMOTE_GATE_TEST_COMMAND="{remote_test_command}"' in poller
    assert (
        'TEST_COMMAND="python -m pytest backend/tests -q --memray '
        '-o faulthandler_timeout=300"' in poller
    )
    assert "actions/attest-build-provenance@" in workflow
    assert 'railway up "$UPLOAD_ROOT" --path-as-root --detach --json' in workflow
    assert 'set(payload) != {"deploymentId", "logsUrl"}' in workflow
    assert '--source-digest "$TARGET"' in workflow
    assert '[[ "$EXPECTED_ACTIONS_DIGEST" =~ ^[0-9a-f]{64}$ ]]' in workflow
    assert "GH_TOKEN=public-oci-bundle-verification-no-api" in workflow
    assert "env -u GH_TOKEN -u GITHUB_TOKEN" not in workflow
    assert "${{ github.token }}" not in workflow
    assert 'EXPECTED_ACTIONS_DIGEST" =~ ^sha256:' not in workflow
    assert "snapshot.debian.org/archive/debian/20250929T000000Z" in dockerfile
    assert "snapshot.debian.org/archive/debian/20250814T000000Z" not in dockerfile
    assert "serviceInstanceUpdate" not in workflow
    assert "serviceInstance(serviceId:" in workflow
    assert '"healthcheckPath": "/healthz"' in workflow
    assert '"restartPolicyType": "NEVER"' in workflow
    assert '"domains": {"customDomains": [], "serviceDomains": []}' in workflow
    assert "exact Railway deployment ignored the runtime contract" in workflow
    assert 'git bundle create "$UPLOAD_ROOT/source.bundle" HEAD' in workflow
    assert 'git bundle verify "$UPLOAD_ROOT/source.bundle"' in workflow
    assert "fetch-depth: 0" in workflow
    assert 'test "$(find "$UPLOAD_ROOT" -maxdepth 1 -type f | wc -l)" -eq 6' in workflow
    assert 'REQUEST_PATH="$UPLOAD_ROOT/gate-request.json"' in workflow
    assert "COPY source.tar source.bundle /gate/" in dockerfile
    assert "COPY gate-request.json /gate/request.json" in dockerfile
    assert "COPY run-gate.py /gate/run-gate.py" in dockerfile
    assert "COPY source.tar source.bundle gate-request.json run-gate.py /gate/" not in dockerfile
    assert "install -d -o 65532 -g 65532 -m 0700" in dockerfile
    assert "/var/lib/seiche-railway-gate-runtime" in dockerfile
    assert 'SEICHE_GATE_REQUEST", "/gate/request.json"' in runner_source
    assert "git clone --quiet /gate/source.bundle /workspace" in dockerfile
    assert "git config --system --add safe.directory /workspace" in dockerfile
    assert "ADD source.tar /workspace/" not in dockerfile
    assert "if ! railway deployment list" in deployment_wait
    assert "Railway status poll $_attempt/360 failed; retrying" in deployment_wait
    assert "if ! railway logs" in result_extraction
    assert "Railway log poll $_attempt/60 failed; retrying" in result_extraction
    assert "marker_count=$(grep -c '^SEICHE_RAILWAY_GATE_RESULT_V1='" in workflow
    assert "caddy_${CADDY_VERSION}_linux_amd64.tar.gz" in dockerfile
    assert 'test "$(uname -m)" = x86_64' in dockerfile
    assert 'python -m pip install -q "./backend[dev,collectors]"' in dockerfile
    assert "chmod -R a-w /workspace" in dockerfile
    assert "pip install -q -e" not in dockerfile
    assert config["deploy"] == {
        "healthcheckPath": "/healthz",
        "healthcheckTimeout": 3600,
        "restartPolicyType": "NEVER",
    }


@pytest.mark.parametrize("dockerfile_path", PINNED_PYTHON_DOCKERFILES)
def test_python_railway_images_share_the_base_image_package_epoch(
    dockerfile_path: Path,
) -> None:
    dockerfile = dockerfile_path.read_text(encoding="utf-8")
    assert dockerfile.count("20250929T000000Z") == 2
    assert "20250814T000000Z" not in dockerfile
