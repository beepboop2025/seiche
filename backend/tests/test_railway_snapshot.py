"""Focused contracts for the off-host Railway snapshot prebuilder."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "ops" / "railway" / "run-snapshot.py"
VERIFIER_PATH = ROOT / "ops" / "deploy" / "seiche-remote-snapshot-verify.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "railway-snapshot-prebuild.yml"
POLLER_PATH = ROOT / "ops" / "deploy" / "seiche-release-poll.sh"
WRAPPER_PATH = ROOT / "ops" / "deploy" / "seiche-deploy-wrapper.sh"
INSTALLER_PATH = ROOT / "ops" / "deploy" / "install-market-platform.sh"
IMPORT_UNIT_PATH = ROOT / "ops" / "deploy" / "seiche-snapshot-import.service"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    assert result["railway_service_id"] == _railway_environment()[
        "RAILWAY_SERVICE_ID"
    ]
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
    assert receipt["remote"]["railway_deployment_id"] == remote[
        "railway_deployment_id"
    ]

    remote["payload"]["version"] = "tampered"
    with pytest.raises(SystemExit):
        verifier.validate_remote_snapshot(
            remote,
            target="a" * 40,
            tree="b" * 40,
            source_archive_sha256=request["source_archive_sha256"],
        )


def test_host_defers_only_recognized_missing_snapshot_artifacts(verifier) -> None:
    assert verifier.missing_artifact_error("manifest unknown")
    assert verifier.missing_artifact_error("not found [http 404]")
    assert not verifier.missing_artifact_error("unauthorized")
    assert not verifier.missing_artifact_error("network unreachable")


def test_phase_two_controller_uses_parallel_attested_prebuild_and_local_seal() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    poller = POLLER_PATH.read_text(encoding="utf-8")
    wrapper = WRAPPER_PATH.read_text(encoding="utf-8")
    installer = INSTALLER_PATH.read_text(encoding="utf-8")
    unit = IMPORT_UNIT_PATH.read_text(encoding="utf-8")

    assert "group: railway-snapshot-prebuild" in workflow
    assert "RAILWAY_SNAPSHOT_SERVICE_ID" in workflow
    assert "actions/attest-build-provenance@" in workflow
    assert "railway ssh" in workflow
    assert "snapshot-result.json" in workflow
    assert "--signer-workflow beepboop2025/seiche/.github/workflows/railway-snapshot-prebuild.yml" in workflow
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
