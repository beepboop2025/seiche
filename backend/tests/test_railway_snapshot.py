"""Focused contracts for the off-host Railway snapshot prebuilder."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import subprocess
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "ops" / "railway" / "run-snapshot.py"
VERIFIER_PATH = ROOT / "ops" / "deploy" / "seiche-remote-snapshot-verify.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "railway-snapshot-prebuild.yml"
CI_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "market-platform-ci.yml"
POLLER_PATH = ROOT / "ops" / "deploy" / "seiche-release-poll.sh"
WRAPPER_PATH = ROOT / "ops" / "deploy" / "seiche-deploy-wrapper.sh"
INSTALLER_PATH = ROOT / "ops" / "deploy" / "install-market-platform.sh"
IMPORT_UNIT_PATH = ROOT / "ops" / "deploy" / "seiche-snapshot-import.service"
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


@contextmanager
def _local_http_server(
    handler: type[BaseHTTPRequestHandler],
) -> Iterator[tuple[str, int]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield host, port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _http_get(
    address: tuple[str, int],
    path: str,
    *,
    authorization: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    headers = {} if authorization is None else {"Authorization": authorization}
    connection = HTTPConnection(*address, timeout=5)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        response_headers = {
            name.lower(): value for name, value in response.getheaders()
        }
        return response.status, response_headers, response.read()
    finally:
        connection.close()


@pytest.fixture(scope="module")
def runner():
    return _load(RUNNER_PATH, "seiche_railway_snapshot_runner")


@pytest.fixture(scope="module")
def verifier():
    return _load(VERIFIER_PATH, "seiche_remote_snapshot_verifier")


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
        "result_token_expires_at": "2026-08-23T02:30:00Z",
        "result_token_sha256": "d" * 64,
        "runner_image": runner.RUNNER_IMAGE,
        "install_command": runner.INSTALL_COMMAND,
        "build_command": runner.BUILD_COMMAND,
    }


def _railway_environment() -> dict[str, str]:
    return {
        "RAILWAY_DEPLOYMENT_ID": "11111111-1111-4111-8111-111111111111",
        "RAILWAY_PROJECT_ID": "22222222-2222-4222-8222-222222222222",
        "RAILWAY_ENVIRONMENT_ID": "33333333-3333-4333-8333-333333333333",
        "RAILWAY_SERVICE_ID": "44444444-4444-4444-8444-444444444444",
        "RAILWAY_REPLICA_REGION": "europe-west4-drams3a",
    }


def test_runner_binds_payload_digests_and_exact_identity(
    runner, monkeypatch, tmp_path: Path, fake_snap: dict
) -> None:
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

    result = runner.build_result(
        loaded,
        fake_snap,
        "2026-08-23T01:02:03Z",
        "2026-08-23T01:08:03Z",
        _railway_environment(),
    )

    payload_bytes = runner.canonical_value(fake_snap)
    assert result["schema"] == runner.RESULT_SCHEMA
    assert result["commit"] == "a" * 40
    assert result["payload"] is fake_snap
    assert result["payload_sha256"] == hashlib.sha256(payload_bytes).hexdigest()
    assert result["payload_size_bytes"] == len(payload_bytes)
    assert result["fault_count"] == len(fake_snap["faults"])
    assert result["railway_service_id"] == _railway_environment()["RAILWAY_SERVICE_ID"]
    assert runner.canonical_document(result).endswith(b"\n")


def test_runner_drops_ambient_credentials_from_builder_environment(
    runner, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RAILWAY_TOKEN", "must-not-cross")
    monkeypatch.setenv("DATABASE_URL", "must-not-cross")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    environment = runner.build_environment(runtime)

    assert "RAILWAY_TOKEN" not in environment
    assert "DATABASE_URL" not in environment
    assert Path(environment["SEICHE_RUNTIME_DATA_DIR"]).is_absolute()
    assert environment["PATH"] == "/usr/local/bin:/usr/bin:/bin"


def test_runner_rejects_payload_digest_inputs_with_non_finite_values(
    runner,
) -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        runner.canonical_value({"bad": float("nan")})


def test_runner_accepts_the_board_utc_clock_and_bounds_result_access(runner) -> None:
    payload = {
        "generated_at": "2026-08-23T01:02:03+00:00",
        "provenance": [],
        "faults": [],
    }
    assert runner.validate_payload(payload)["generated_at"] == payload["generated_at"]

    token = "e" * 64
    token_sha256 = hashlib.sha256(token.encode("ascii")).hexdigest()
    now = datetime(2026, 8, 23, 1, 0, tzinfo=UTC)
    expires_at = (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    assert runner.result_request_authorized(
        "/snapshot-result.json",
        f"Bearer {token}",
        token_sha256,
        expires_at,
        now=now,
    )
    assert not runner.result_request_authorized(
        "/snapshot-result.json",
        f"Bearer {'f' * 64}",
        token_sha256,
        expires_at,
        now=now,
    )
    assert not runner.result_request_authorized(
        "/healthz",
        f"Bearer {token}",
        token_sha256,
        expires_at,
        now=now,
    )
    assert not runner.result_request_authorized(
        "/snapshot-result.json",
        f"Bearer {token}",
        token_sha256,
        now.isoformat().replace("+00:00", "Z"),
        now=now,
    )
    assert not runner.result_request_authorized(
        "/snapshot-result.json",
        f"Bearer {token}",
        token_sha256,
        "2026-08-23T06:35:00+05:30",
        now=now,
    )


def test_runner_rejects_non_utc_payload_and_result_expiry(
    runner, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        runner.validate_payload(
            {
                "generated_at": "2026-08-23T06:32:03+05:30",
                "provenance": [],
                "faults": [],
            }
        )
    assert "snapshot generated_at is invalid" in capsys.readouterr().err

    archive = tmp_path / "source.tar"
    archive.write_bytes(b"exact git archive fixture\n")
    request = _request(runner, archive)
    request["result_token_expires_at"] = "2026-08-23T08:00:00+05:30"
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        runner.load_request(request_path, archive)
    assert "request result token expiry is invalid" in capsys.readouterr().err


def test_snapshot_http_handler_serves_only_exact_deployment_bound_proof(runner) -> None:
    result = b'{"railway_deployment_id":"exact-deployment","result":"ok"}\n'
    health = (
        b'{"railway_deployment_id":"exact-deployment","status":"snapshot_complete"}\n'
    )
    token = "e" * 64
    token_sha256 = hashlib.sha256(token.encode("ascii")).hexdigest()
    expires_at = (
        (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    )
    handler = runner.build_http_handler(
        result,
        token_sha256,
        expires_at,
        health,
    )

    with _local_http_server(handler) as address:
        status, headers, body = _http_get(address, "/healthz")
        assert status == 200
        assert body == health
        assert headers["content-type"] == "application/json"
        assert headers["cache-control"] == "no-store"
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["content-length"] == str(len(health))

        for path, authorization in (
            ("/snapshot-result.json", None),
            ("/snapshot-result.json", f"Bearer {'f' * 64}"),
            ("/not-the-result", f"Bearer {token}"),
        ):
            status, headers, body = _http_get(
                address,
                path,
                authorization=authorization,
            )
            assert status == 404
            assert body == b'{"error":"not_found"}\n'
            assert headers["content-type"] == "application/json"
            assert headers["cache-control"] == "no-store"
            assert headers["x-content-type-options"] == "nosniff"
            assert headers["content-length"] == str(len(body))

        status, headers, body = _http_get(
            address,
            "/snapshot-result.json",
            authorization=f"Bearer {token}",
        )
        assert status == 200
        assert body == result
        expected_headers = {
            "content-type": ("application/vnd.seiche.railway-snapshot-result.v1+json"),
            "content-encoding": "identity",
            "etag": f'"sha256:{hashlib.sha256(result).hexdigest()}"',
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
            "content-length": str(len(result)),
        }
        assert {name: headers.get(name) for name in expected_headers} == (
            expected_headers
        )

    expired_handler = runner.build_http_handler(
        result,
        token_sha256,
        "2000-01-01T00:00:00Z",
        health,
    )
    with _local_http_server(expired_handler) as address:
        status, headers, body = _http_get(
            address,
            "/snapshot-result.json",
            authorization=f"Bearer {token}",
        )
        assert status == 404
        assert body == b'{"error":"not_found"}\n'
        assert headers["content-type"] == "application/json"


def test_host_accepts_only_the_exact_payload_and_source_binding(
    runner, verifier, monkeypatch, tmp_path: Path, fake_snap: dict
) -> None:
    archive = tmp_path / "source.tar"
    archive.write_bytes(b"exact git archive fixture\n")
    request = _request(runner, archive)
    monkeypatch.setattr(runner, "dependency_snapshot_sha256", lambda: "d" * 64)
    monkeypatch.setattr(runner.platform, "python_version", lambda: "3.12.11")
    remote = runner.build_result(
        request,
        fake_snap,
        "2026-07-10T00:00:00Z",
        "2026-07-10T00:02:00Z",
        _railway_environment(),
    )

    validated = verifier.validate_remote_snapshot(
        remote,
        target="a" * 40,
        tree="b" * 40,
        source_archive_sha256=request["source_archive_sha256"],
    )
    receipt = verifier.render_local_receipt(
        validated,
        artifact_digest="sha256:" + "e" * 64,
        artifact_snapshot_sha256="f" * 64,
    )
    assert receipt["schema"] == verifier.LOCAL_SCHEMA
    assert receipt["payload_sha256"] == remote["payload_sha256"]
    assert receipt["remote"]["railway_deployment_id"] == remote["railway_deployment_id"]

    tampered = json.loads(json.dumps(remote))
    tampered["payload"]["version"] = "tampered"
    with pytest.raises(SystemExit):
        verifier.validate_remote_snapshot(
            tampered,
            target="a" * 40,
            tree="b" * 40,
            source_archive_sha256=request["source_archive_sha256"],
        )


def test_host_defers_only_recognized_missing_snapshot_artifacts(verifier) -> None:
    assert verifier.missing_artifact_error("manifest unknown")
    assert verifier.missing_artifact_error("not found [http 404]")
    assert not verifier.missing_artifact_error("unauthorized")
    assert not verifier.missing_artifact_error("network unreachable")


def test_railway_configuration_preflight_is_first_and_fails_with_names_only() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
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
        "RAILWAY_SNAPSHOT_SERVICE_ID",
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
        "RAILWAY_SNAPSHOT_SERVICE_ID": "fake-service-must-not-be-printed",
    }
    missing = ("RAILWAY_TOKEN", "RAILWAY_SNAPSHOT_SERVICE_ID")
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

    ci_workflow = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert '".github/workflows/railway-snapshot-prebuild.yml"' in ci_workflow
    assert "backend/tests/test_railway_snapshot.py" in ci_workflow


def test_phase_two_controller_uses_parallel_attested_prebuild_and_local_seal() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    runtime_contract = _workflow_step(workflow, "Verify the Railway runtime contract")
    deployment_wait = _workflow_step(
        workflow, "Wait for Railway to finish the snapshot"
    )
    extraction = _workflow_step(
        workflow, "Extract and independently validate the exact Railway snapshot"
    )
    poller = POLLER_PATH.read_text(encoding="utf-8")
    wrapper = WRAPPER_PATH.read_text(encoding="utf-8")
    installer = INSTALLER_PATH.read_text(encoding="utf-8")
    unit = IMPORT_UNIT_PATH.read_text(encoding="utf-8")

    assert "group: railway-snapshot-prebuild" in workflow
    assert "RAILWAY_SNAPSHOT_SERVICE_ID" in workflow
    assert "actions/attest-build-provenance@" in workflow
    assert "railway ssh" not in workflow
    assert "serviceInstanceUpdate" not in workflow
    assert "serviceInstance(serviceId:" in workflow
    assert '"healthcheckPath": "/healthz"' in workflow
    assert '"restartPolicyType": "NEVER"' in workflow
    assert "exact Railway deployment ignored the runtime contract" in workflow
    assert "result_token_sha256" in workflow
    assert "result_token_expires_at" in workflow
    assert "RAILWAY_SNAPSHOT_ORIGIN" in workflow
    assert "${{ vars.RAILWAY_SNAPSHOT_ORIGIN }}" not in workflow
    assert "serviceDomains" in runtime_contract
    assert "snapshot_origin=https://" in runtime_contract
    assert "if ! railway deployment list" in deployment_wait
    assert "Railway status poll $_attempt/360 failed; retrying" in deployment_wait
    assert (
        "RAILWAY_SNAPSHOT_ORIGIN: ${{ steps.runtime.outputs.snapshot_origin }}"
        in extraction
    )
    assert "railway domain" not in workflow
    assert "snapshot result is not closed without its bearer" in workflow
    assert "printf 'Authorization: Bearer %s\\n'" in workflow
    assert '--header "@$auth_header"' in workflow
    assert "--max-filesize 67239936" in workflow
    assert '"content-encoding": "identity"' in workflow
    assert '"cache-control": "no-store"' in workflow
    assert '"etag": f\'"sha256:' in workflow
    assert "000|404|502|503)" in extraction
    assert "curl_status=$?" in extraction
    assert "result_ready=1" in extraction
    assert 'snapshot-result.json" || true' not in extraction
    assert "snapshot-result.json" in workflow
    assert (
        "--signer-workflow beepboop2025/seiche/.github/workflows/railway-snapshot-prebuild.yml"
        in workflow
    )
    assert "install_remote_snapshot_receipt" in poller
    assert "seiche.release-receipt.v3" in poller
    assert "snapshot_receipt_sha256" in poller
    assert 'SEICHE_PREBUILT_SNAPSHOT_ARTIFACT="$SNAPSHOT_ARTIFACT"' in poller
    assert "import_prebuilt_snapshot" in wrapper
    assert "SEICHE_PREBUILT_PAYLOAD_SHA256" in wrapper
    assert "/etc/seiche/market.env" not in wrapper
    assert "seiche-snapshot-import.service" in installer
    assert "EnvironmentFile=/etc/seiche/market.env" in unit
    assert "User=seiche" in unit
    assert "NoNewPrivileges=true" in unit
