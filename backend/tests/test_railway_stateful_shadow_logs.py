"""Focused contracts for the Phase-4 project-token log evidence path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seiche import stateful_migration as migration

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "railway-stateful-shadow.yml"


def _railway_identity() -> dict[str, str]:
    return {
        "deployment_id": "11111111-1111-4111-8111-111111111111",
        "project_id": "22222222-2222-4222-8222-222222222222",
        "environment_id": "33333333-3333-4333-8333-333333333333",
        "service_id": "44444444-4444-4444-8444-444444444444",
        "volume_id": "55555555-5555-4555-8555-555555555555",
        "volume_name": "seiche-stateful-data",
        "volume_mount_path": str(migration.PLATFORM_ROOT),
        "region": "asia-southeast1",
    }


def _receipt(
    *,
    request_id: str = "c" * 64,
    deployment_id: str = "11111111-1111-4111-8111-111111111111",
) -> dict[str, object]:
    return {
        "schema": migration.RECEIPT_SCHEMA,
        "request": {"id": request_id},
        "railway": {"deployment_id": deployment_id},
        "fixture": "non-secret canonical receipt",
    }


def _record(marker: str) -> bytes:
    return (
        json.dumps(
            {"timestamp": "2026-08-23T02:04:01Z", "message": marker},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


@pytest.mark.parametrize(
    "reused_replica_id",
    (
        "66666666-6666-4666-8666-666666666666",
        "77777777-7777-4777-8777-777777777777",
    ),
)
def test_reconstructs_identical_created_and_reused_receipt(
    reused_replica_id: str,
) -> None:
    deployment_id = "11111111-1111-4111-8111-111111111111"
    replica_id = "66666666-6666-4666-8666-666666666666"
    receipt = _receipt(deployment_id=deployment_id)
    environment = {
        "RAILWAY_DEPLOYMENT_ID": deployment_id,
        "RAILWAY_REPLICA_ID": replica_id,
    }
    created = migration.render_log_result(
        receipt,
        lifecycle="created",
        environment=environment,
        runtime_started_at="2026-08-23T02:04:00Z",
    )
    reused = migration.render_log_result(
        receipt,
        lifecycle="reused",
        environment={
            "RAILWAY_DEPLOYMENT_ID": deployment_id,
            "RAILWAY_REPLICA_ID": reused_replica_id,
        },
        runtime_started_at="2026-08-23T02:05:00Z",
    )
    logs = _record(created) + _record(reused).replace(
        b"2026-08-23T02:04:01Z",
        b"2026-08-23T02:05:00.123456789Z",
    )

    results = migration.extract_log_results(
        logs,
        expected_request_id="c" * 64,
        expected_deployment_id=deployment_id,
        expected_replicas={
            "created": replica_id,
            "reused": reused_replica_id,
        },
    )

    expected_body = migration.canonical_document(receipt)
    assert results["created"].receipt_body == expected_body
    assert results["reused"].receipt_body == expected_body
    assert results["created"].receipt_sha256 == results["reused"].receipt_sha256
    assert results["reused"].logged_at == "2026-08-23T02:05:00.123456789Z"
    assert results["reused"].logged_at_unix_ns == 1_787_450_700_123_456_789


@pytest.mark.parametrize(
    "case",
    (
        "truncated",
        "malformed",
        "duplicate",
        "wrong-framing",
        "wrong-lifecycle",
        "wrong-request",
        "wrong-deployment",
        "wrong-replica",
        "wrong-log-time",
    ),
)
def test_rejects_non_exact_evidence(case: str) -> None:
    request_id = "c" * 64
    deployment_id = "11111111-1111-4111-8111-111111111111"
    replica_id = "66666666-6666-4666-8666-666666666666"
    marker_deployment_id = (
        "77777777-7777-4777-8777-777777777777"
        if case == "wrong-deployment"
        else deployment_id
    )
    marker = migration.render_log_result(
        _receipt(request_id=request_id, deployment_id=marker_deployment_id),
        lifecycle="reused" if case == "wrong-lifecycle" else "created",
        environment={
            "RAILWAY_DEPLOYMENT_ID": marker_deployment_id,
            "RAILWAY_REPLICA_ID": replica_id,
        },
        runtime_started_at="2026-08-23T02:04:00Z",
    )
    if case == "wrong-request":
        marker = migration.render_log_result(
            _receipt(request_id="d" * 64, deployment_id=deployment_id),
            lifecycle="created",
            environment={
                "RAILWAY_DEPLOYMENT_ID": deployment_id,
                "RAILWAY_REPLICA_ID": replica_id,
            },
            runtime_started_at="2026-08-23T02:04:00Z",
        )
    if case == "truncated":
        marker = marker[:-1]
    elif case == "malformed":
        marker += "!"
    elif case == "wrong-framing":
        marker = "prefix:" + marker
    body = _record(marker)
    if case == "duplicate":
        body += body
    elif case == "wrong-log-time":
        body = body.replace(
            b"2026-08-23T02:04:01Z",
            b"2026-08-23T02:04:01.123456789+00:00",
        )

    with pytest.raises(migration.MigrationContractError):
        migration.extract_log_results(
            body,
            expected_request_id=request_id,
            expected_deployment_id=deployment_id,
            expected_replicas={
                "created": (
                    "88888888-8888-4888-8888-888888888888"
                    if case == "wrong-replica"
                    else replica_id
                )
            },
        )


def test_log_transport_bounds_receipt_and_deployment_logs() -> None:
    deployment_id = "11111111-1111-4111-8111-111111111111"
    replica_id = "66666666-6666-4666-8666-666666666666"
    receipt = _receipt(deployment_id=deployment_id)
    receipt["fixture"] = "x" * migration.MAX_LOG_RECEIPT_BYTES

    with pytest.raises(migration.MigrationContractError, match="transport bound"):
        migration.render_log_result(
            receipt,
            lifecycle="created",
            environment={
                "RAILWAY_DEPLOYMENT_ID": deployment_id,
                "RAILWAY_REPLICA_ID": replica_id,
            },
            runtime_started_at="2026-08-23T02:04:00Z",
        )
    with pytest.raises(migration.MigrationContractError, match="log size"):
        migration.extract_log_results(
            b"x" * (migration.MAX_DEPLOYMENT_LOG_BYTES + 1),
            expected_request_id="c" * 64,
            expected_deployment_id=deployment_id,
            expected_replicas={"created": replica_id},
        )


def test_emits_only_after_runtime_validation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    receipt = _receipt()
    for name, value in {
        "DATABASE_URL": "postgresql://runtime-only",
        "RAILWAY_DEPLOYMENT_ID": "11111111-1111-4111-8111-111111111111",
        "RAILWAY_REPLICA_ID": "66666666-6666-4666-8666-666666666666",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(migration.os, "geteuid", lambda: 0)
    monkeypatch.setattr(migration.os, "getegid", lambda: 0)
    monkeypatch.setattr(
        migration,
        "load_request",
        lambda *_args: {
            "snapshot_id": "20260823T010203Z",
            "request_id": "c" * 64,
        },
    )
    monkeypatch.setattr(migration, "railway_identity", lambda _env: _railway_identity())
    monkeypatch.setattr(
        migration, "validate_bundle", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        migration,
        "restore_shadow",
        lambda *_args, **_kwargs: (receipt, "postgresql://generation", "created"),
    )
    monkeypatch.setattr(
        migration,
        "runtime_environment",
        lambda *_args, **_kwargs: {"runtime": "environment"},
    )

    def validate(_environment: object) -> dict[str, object]:
        calls.append("validate")
        return receipt

    def render(*_args: object, **_kwargs: object) -> str:
        calls.append("render")
        return migration.LOG_RESULT_MARKER + "fixture"

    def supervise(_environment: object) -> int:
        calls.append("supervise")
        return 0

    monkeypatch.setattr(migration, "validate_runtime_receipt", validate)
    monkeypatch.setattr(migration, "render_log_result", render)
    monkeypatch.setattr(migration, "supervise_shadow", supervise)

    assert migration.run_shadow() == 0
    assert calls == ["validate", "render", "supervise"]
    assert capsys.readouterr().out == migration.LOG_RESULT_MARKER + "fixture\n"


def test_workflow_uses_only_project_token_control_plane_evidence() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "railway ssh" not in workflow
    assert "railway volume files" not in workflow
    assert "RAILWAY_STATEFUL_SSH_PRIVATE_KEY" not in workflow
    assert "if ! railway deployment list" in workflow
    assert "Railway deployment poll $_attempt/360 failed; retrying" in workflow
    assert 'railway logs "$RAILWAY_DEPLOYMENT_ID"' in workflow
    assert "--deployment --json --lines 1000" in workflow
    assert "railway restart --project" in workflow
    assert 'value != {"id": os.environ["RAILWAY_DEPLOYMENT_ID"]}' in workflow
    assert "SEICHE_RAILWAY_STATEFUL_SHADOW_RESULT_V1=" in workflow
    assert '"healthcheckPath": "/healthz"' in workflow
    assert '"domains": {"customDomains": [], "serviceDomains": []}' in workflow
    assert 'candidate["instances"][0].get("status") != "RUNNING"' in workflow
    assert 'results["reused"].logged_at_unix_ns <= boundary' in workflow
    assert "actions/attest-build-provenance@" in workflow
