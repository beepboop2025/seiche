from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import textwrap
from datetime import UTC, datetime, timedelta

import pytest

from seiche import telegram_migration as migration
from seiche import telegram_runtime as runtime
from seiche import telegram_worker as worker


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPOSITORY_ROOT / ".github/workflows/railway-telegram.yml"
CONTROLLER = REPOSITORY_ROOT / "ops/deploy/seiche-telegram-migration-controller.sh"
STATUS_CONTROLLER = REPOSITORY_ROOT / "ops/deploy/seiche-telegram-status-controller.sh"
DOCKERFILE = REPOSITORY_ROOT / "ops/railway/Dockerfile.telegram"
RAILWAY_CONFIG = REPOSITORY_ROOT / "ops/railway/railway.telegram.json"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _state(root: Path) -> dict[str, object]:
    root.mkdir(mode=0o700)
    _write_json(root / "offset.json", 43001)
    _write_json(
        root / "subscribers.json",
        {"123": {"started": "2026-08-20"}, "-456": {"started": "2026-08-21"}},
    )
    _write_json(
        root / "alert_state.json",
        {"seen": {"regime": "CALM", "index": 10}, "alerted": {}, "pending": {}},
    )
    (root / "leads.jsonl").write_text('{"chat":123,"ref":"launch"}\n', encoding="utf-8")
    return migration.inspect_state(root)


def _image(now: datetime) -> dict[str, object]:
    return {
        "schema": migration.IMAGE_SCHEMA,
        "repository": migration.REPOSITORY,
        "workflow": migration.WORKFLOW,
        "source_ref": migration.SOURCE_REF,
        "commit": "a" * 40,
        "tree": "b" * 40,
        "source_archive_sha256": "c" * 64,
        "source_bundle_sha256": "d" * 64,
        "request_id": "e" * 64,
        "requested_at": migration.iso_now(),
        "confirmation": migration.PREPARE_CONFIRMATION,
    }


def _railway() -> dict[str, str]:
    return {
        "deployment_id": "11111111-1111-4111-8111-111111111111",
        "project_id": "22222222-2222-4222-8222-222222222222",
        "environment_id": "33333333-3333-4333-8333-333333333333",
        "service_id": "44444444-4444-4444-8444-444444444444",
        "volume_id": "55555555-5555-4555-8555-555555555555",
        "volume_name": "seiche-telegram",
        "volume_mount_path": str(migration.ROOT),
        "region": "asia-southeast1-eqsg3a",
    }


def _environment(railway: dict[str, str]) -> dict[str, str]:
    return {
        "SEICHE_RELEASE_SHA": "a" * 40,
        "RAILWAY_DEPLOYMENT_ID": railway["deployment_id"],
        "RAILWAY_PROJECT_ID": railway["project_id"],
        "RAILWAY_ENVIRONMENT_ID": railway["environment_id"],
        "RAILWAY_SERVICE_ID": railway["service_id"],
        "SEICHE_RAILWAY_TELEGRAM_VOLUME_ID": railway["volume_id"],
        "RAILWAY_VOLUME_NAME": railway["volume_name"],
        "RAILWAY_VOLUME_MOUNT_PATH": railway["volume_mount_path"],
        "RAILWAY_REPLICA_REGION": railway["region"],
        "LAB_CHANNEL_ID": "-1004297805949",
    }


def _transfer(
    *,
    now: datetime,
    state: dict[str, object],
    archive_sha256: str,
    railway: dict[str, str],
) -> dict[str, object]:
    frozen = now - timedelta(minutes=3)
    snapshot = now - timedelta(minutes=2)
    settled = now - timedelta(minutes=2)
    requested = now - timedelta(minutes=1)
    return {
        "schema": migration.TRANSFER_SCHEMA,
        "repository": migration.REPOSITORY,
        "workflow": migration.WORKFLOW,
        "commit": "a" * 40,
        "image_request_id": "e" * 64,
        "request_id": "f" * 64,
        "snapshot_id": snapshot.strftime("%Y%m%dT%H%M%SZ"),
        "requested_at": requested.isoformat().replace("+00:00", "Z"),
        "archive_sha256": archive_sha256,
        "bot_token_sha256": hashlib.sha256(b"test-token").hexdigest(),
        "state": state,
        "railway": railway,
        "fence": {
            "source": "hetzner",
            "state": "frozen",
            "state_root": "/var/lib/seiche-bot",
            "units": list(migration.BOT_UNITS),
            "poller_stopped": True,
            "timers_stopped": True,
            "timers_disabled": True,
            "active_processes": [],
            "lab_channel_id": "-1004297805949",
            "frozen_at": frozen.isoformat().replace("+00:00", "Z"),
            "settled_at": settled.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(minutes=20))
            .isoformat()
            .replace("+00:00", "Z"),
            "poller_settle_seconds": 60,
        },
        "confirmation": migration.TRANSFER_CONFIRMATION,
    }


def test_state_archive_is_closed_and_preserves_offset_and_subscribers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    identity = _state(source)
    archive = tmp_path / "seiche-bot.tgz"
    assert migration.create_archive(source, archive) == identity
    migration.validate_archive(archive)
    restored = migration.extract_archive(archive, tmp_path / "restore")
    assert migration.inspect_state(restored) == identity
    assert identity["offset"] == 43001
    assert identity["subscriber_count"] == 2

    linked_archive = tmp_path / "linked-seiche-bot.tgz"
    os.link(archive, linked_archive)
    with pytest.raises(migration.TelegramMigrationError, match="unsafe"):
        migration.validate_archive(linked_archive)
    linked_archive.unlink()

    (source / "unsafe.tmp").write_text("partial", encoding="utf-8")
    with pytest.raises(migration.TelegramMigrationError, match="filename"):
        migration.inspect_state(source)


def test_candidate_grant_worker_proof_and_activation_are_one_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    source = tmp_path / "source"
    state = _state(source)
    archive = tmp_path / "seiche-bot.tgz"
    migration.create_archive(source, archive)
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    image = _image(now)
    image_path = tmp_path / "image-request.json"
    image_path.write_bytes(migration.canonical(image))
    railway = _railway()
    environment = _environment(railway)
    request = _transfer(
        now=now,
        state=state,
        archive_sha256=archive_sha,
        railway=railway,
    )
    with pytest.raises(migration.TelegramMigrationError, match="channel identity"):
        migration.validate_transfer(
            request,
            image_request=image,
            railway=railway,
            expected_lab_channel_id="-1009999999999",
        )
    root = tmp_path / "platform"
    monkeypatch.setattr(migration, "IMAGE_REQUEST_PATH", image_path)
    monkeypatch.setattr(migration, "RUNTIME_UID", os.geteuid())
    monkeypatch.setattr(migration, "RUNTIME_GID", os.getegid())

    candidate = migration.restore_candidate(request, archive, environment, root=root)
    resumed = migration.restore_candidate(request, archive, environment, root=root)
    assert resumed.receipt == candidate.receipt
    assert migration.inspect_state(candidate.state_root) == state
    assert candidate.state_root.parent.stat().st_mode & 0o777 == 0o710
    assert (root / "staging").stat().st_mode & 0o777 == 0o700

    grant = {
        "schema": migration.GRANT_SCHEMA,
        "repository": migration.REPOSITORY,
        "workflow": migration.WORKFLOW,
        "commit": request["commit"],
        "request_id": request["request_id"],
        "candidate_receipt_sha256": migration.digest(
            migration.canonical(candidate.receipt)
        ),
        "bot_token_sha256": request["bot_token_sha256"],
        "activated_at": now.isoformat().replace("+00:00", "Z"),
        "confirmation": migration.GRANT_CONFIRMATION,
    }
    migration.validate_grant(
        grant,
        request=request,
        candidate=candidate.receipt,
        now=now,
    )
    (root / "transfers" / f"{request['request_id']}.json").write_bytes(
        migration.canonical(request)
    )
    encoded_grant = base64.b64encode(migration.canonical(grant)).decode("ascii")
    assert migration.publish_grant(encoded_grant, environment, root=root) == grant
    assert migration.publish_grant(encoded_grant, environment, root=root) == grant
    (root / "grants" / f"{'9' * 64}.json").write_bytes(migration.canonical(grant))
    with pytest.raises(migration.TelegramMigrationError, match="different"):
        migration.publish_grant(encoded_grant, environment, root=root)
    baseline = worker.scheduler_baseline(
        migration._utc(request["fence"]["frozen_at"], label="frozen_at")
    )
    proof = {
        "schema": migration.WORKER_PROOF_SCHEMA,
        "repository": migration.REPOSITORY,
        "commit": request["commit"],
        "request_id": request["request_id"],
        "candidate_receipt_sha256": migration.digest(
            migration.canonical(candidate.receipt)
        ),
        "grant_sha256": migration.digest(migration.canonical(grant)),
        "railway": railway,
        "bot": {"id": 987654, "username": "seiche_desk_bot"},
        "initial_offset": state["offset"],
        "observed_offset": state["offset"],
        "first_poll_at": now.isoformat().replace("+00:00", "Z"),
        "scheduler_baseline": baseline,
        "get_updates_ok": True,
        "conflict_observed": False,
    }
    migration.validate_worker_proof(
        proof,
        request=request,
        candidate=candidate.receipt,
        grant=grant,
        railway=railway,
    )
    activation = migration.render_activation(request, candidate.receipt, grant, proof)
    assert (
        migration.validate_activation(
            activation,
            request=request,
            candidate=candidate.receipt,
            grant=grant,
            proof=proof,
        )
        == activation
    )
    assert activation["authority"]["sole_get_updates_consumer"] is True

    tampered = dict(proof)
    tampered["conflict_observed"] = True
    with pytest.raises(migration.TelegramMigrationError, match="worker proof"):
        migration.validate_worker_proof(
            tampered,
            request=request,
            candidate=candidate.receipt,
            grant=grant,
            railway=railway,
        )


def test_scheduler_marks_delivery_inflight_before_call_and_then_completes(
    tmp_path: Path,
) -> None:
    frozen = datetime(2026, 8, 23, 10, 0, tzinfo=UTC)
    schedule_path = tmp_path / "railway_schedule.json"
    schedule = worker.load_schedule(schedule_path, frozen)
    observed: list[dict[str, object]] = []

    def alert() -> None:
        observed.append(json.loads(schedule_path.read_text(encoding="utf-8")))

    now = datetime(2026, 8, 23, 10, 36, tzinfo=UTC)
    worker.run_due_jobs(
        schedule,
        schedule_path=schedule_path,
        now=now,
        jobs={"alert": alert, "letter": lambda: None, "tandem": lambda: None},
    )
    assert observed[0]["inflight"] == {
        "job": "alert",
        "slot": "2026-08-23T10:35:00Z",
        "started_at": observed[0]["inflight"]["started_at"],
    }
    final = json.loads(schedule_path.read_text(encoding="utf-8"))
    assert final["inflight"] is None
    assert final["completed"]["alert"] == "2026-08-23T10:35:00Z"
    assert final["last_outcome"]["alert"] == "completed"


def test_scheduler_refuses_an_uncertain_previous_delivery(tmp_path: Path) -> None:
    path = tmp_path / "railway_schedule.json"
    value = {
        "schema": worker.SCHEDULE_SCHEMA,
        "completed": {
            "alert": "2026-08-23T10:05:00Z",
            "letter": "2026-08-22T11:30:00Z",
            "tandem": "2026-08-23T07:10:00Z",
        },
        "inflight": {
            "job": "letter",
            "slot": "2026-08-23T11:30:00Z",
            "started_at": "2026-08-23T11:30:01Z",
        },
        "last_outcome": {
            "alert": "completed",
            "letter": "source-baseline",
            "tandem": "completed",
        },
    }
    path.write_bytes(migration.canonical(value))
    with pytest.raises(worker.TelegramWorkerError, match="uncertain"):
        worker.load_schedule(path, datetime(2026, 8, 23, 10, 0, tzinfo=UTC))


def test_live_state_may_evolve_but_offset_cannot_move_backwards(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    baseline = _state(state_root)
    _write_json(
        state_root / "railway_schedule.json", {"schema": worker.SCHEDULE_SCHEMA}
    )
    _write_json(state_root / "offset.json", baseline["offset"] + 7)
    (state_root / "offset.json.tmp").write_text("partial", encoding="utf-8")
    migration.recover_live_state_temps(state_root)
    assert not (state_root / "offset.json.tmp").exists()
    observed = migration.validate_live_state(state_root, baseline=baseline)
    assert observed["offset"] == baseline["offset"] + 7
    assert observed["tree_sha256"] != baseline["tree_sha256"]
    runtime._require_state_offset({"state_root": state_root}, baseline["offset"] + 7)
    with pytest.raises(runtime.TelegramRuntimeError, match="proven offset"):
        runtime._require_state_offset(
            {"state_root": state_root}, baseline["offset"] + 8
        )

    _write_json(state_root / "offset.json", baseline["offset"] - 1)
    with pytest.raises(migration.TelegramMigrationError, match="backwards"):
        migration.validate_live_state(state_root, baseline=baseline)


def test_poll_conflict_is_fatal_before_scheduled_delivery() -> None:
    class ConflictBot:
        @staticmethod
        def tg_call(_method: str, _payload: dict[str, object]) -> dict[str, object]:
            return {"ok": False, "error_code": 409}

    class TransientBot:
        @staticmethod
        def tg_call(_method: str, _payload: dict[str, object]) -> None:
            return None

    with pytest.raises(worker.TelegramWorkerError, match="another Telegram"):
        worker._get_updates(ConflictBot(), 43001)  # type: ignore[arg-type]
    assert worker._get_updates(TransientBot(), 43001) is None  # type: ignore[arg-type]


def test_supervisor_requires_a_current_worker_heartbeat(tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    railway = _railway()
    request = _transfer(
        now=now,
        state={
            "offset": 43001,
            "subscriber_count": 0,
            "subscribers_sha256": "1" * 64,
            "file_sha256": {
                "offset.json": "2" * 64,
                "subscribers.json": "3" * 64,
            },
            "file_size": {"offset.json": 6, "subscribers.json": 3},
            "tree_sha256": "4" * 64,
            "total_bytes": 9,
        },
        archive_sha256="5" * 64,
        railway=railway,
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    heartbeat_path = runtime_dir / f"{request['request_id']}.heartbeat.json"
    heartbeat = {
        "schema": runtime.HEARTBEAT_SCHEMA,
        "commit": request["commit"],
        "deployment_id": railway["deployment_id"],
        "request_id": request["request_id"],
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "offset": request["state"]["offset"],
        "mode": "production",
        "faults": [],
    }
    heartbeat_path.write_bytes(migration.canonical(heartbeat))
    context = {"request": request}
    assert runtime._has_fresh_worker_heartbeat(
        context,
        railway=railway,
        root=tmp_path,
        not_before=now - timedelta(seconds=1),
    )
    assert not runtime._has_fresh_worker_heartbeat(
        context,
        railway=railway,
        root=tmp_path,
        not_before=now + timedelta(seconds=1),
    )


def test_health_is_not_ready_before_reconciliation() -> None:
    health = runtime.Health(
        commit="a" * 40,
        deployment_id="11111111-1111-4111-8111-111111111111",
    )
    status, body = health.response()
    assert status == 503
    assert json.loads(body) == {
        "schema": runtime.HEALTH_SCHEMA,
        "status": "starting",
        "mode": "initializing",
        "commit": "a" * 40,
        "deployment_id": "11111111-1111-4111-8111-111111111111",
        "request_id": None,
        "faults": ["initializing"],
    }
    health.update(status="ready", faults=[])
    assert health.response()[0] == 200


def test_phase7_workflow_is_fail_closed_and_recovery_bound() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    controller = CONTROLLER.read_text(encoding="utf-8")
    status_controller = STATUS_CONTROLLER.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    railway = json.loads(RAILWAY_CONFIG.read_text(encoding="utf-8"))

    for job in (
        "prepare-service",
        "restore-candidate",
        "activate",
        "rollback",
        "monitor",
    ):
        assert re.search(rf"^  {job}:$", workflow, flags=re.MULTILINE)
    for environment in (
        "railway-telegram-admin",
        "railway-telegram-cutover",
        "railway-telegram-activation",
        "railway-telegram-rollback",
        "railway-telegram-monitor",
    ):
        assert f"environment: {environment}" in workflow
    for confirmation in (
        migration.PREPARE_CONFIRMATION,
        "FREEZE_HETZNER_TELEGRAM_FOR_CANDIDATE",
        migration.TRANSFER_CONFIRMATION,
        migration.GRANT_CONFIRMATION,
        "RESTORE_HETZNER_TELEGRAM_BEFORE_GRANT",
    ):
        assert confirmation in workflow
    for recovery_control in (
        "volumeInstanceBackupScheduleUpdate",
        "volumeInstanceBackupScheduleList",
        "volumeInstanceBackupCreate",
        "volumeInstanceBackupLock",
        "seiche-phase7-candidate-",
        "Telegram restored candidate backup is not locked",
        "latest Telegram native backup is stale",
        "--object-lock-mode COMPLIANCE",
        "--checksum-algorithm SHA256",
        "ObjectLockMode",
    ):
        assert recovery_control in workflow
    assert workflow.count("actions/attest-build-provenance@") == 4
    assert "vars.RAILWAY_TELEGRAM_PHASE7_ENABLED == 'true'" in workflow
    assert "A Railway Telegram grant exists; rollback is forbidden." in workflow
    assert "assert_source_authoritative" in controller
    assert "trap restore_failed_freeze EXIT" in controller
    assert "seiche.hetzner-telegram-status.v1" in controller
    assert "-links +1" in controller
    assert "512 * 1024**2" in controller
    assert "LAB_CHANNEL_ID" in controller
    assert "Telegram persisted fence is invalid" in controller
    assert "activation_sha256" in controller
    assert "saved activation differs from the retry" in controller
    assert 'mv "$temporary" "$root/activation.json"' in controller
    assert "only status is allowed" in status_controller
    assert "id -g" in status_controller
    assert "unset SSH_ORIGINAL_COMMAND" in status_controller
    assert "HETZNER_TELEGRAM_MONITOR_SSH_KEY" in workflow
    assert "Telegram release:" in workflow
    assert "Hetzner Telegram acknowledgement differs" in workflow
    assert "hashlib.sha256(fence_body).hexdigest()" in workflow
    assert "secrets.SEICHE_LAB_CHANNEL_ID" in workflow
    assert "railway variable set LAB_CHANNEL_ID --stdin" in workflow
    assert "volume files list --volume" in workflow
    assert "rows != []" in workflow
    assert 'heartbeat["offset"] < minimum_offset' in workflow
    assert "FROM python:3.12.11-slim-bookworm@sha256:" in dockerfile
    assert "source archive bytes differ" in dockerfile
    assert railway["deploy"] == {
        "healthcheckPath": "/healthz",
        "healthcheckTimeout": 600,
        "restartPolicyType": "ON_FAILURE",
        "restartPolicyMaxRetries": 3,
    }
    for forbidden in (
        "volume files delete",
        "volumeInstanceBackupDelete",
        "volumeInstanceBackupRestore",
        "postgres pitr restore",
        "RAILWAY_BECOMES_SOLE_WRITER",
    ):
        assert forbidden not in workflow


def test_workflow_embedded_python_is_syntactically_valid() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    blocks = re.findall(
        r"<<'PY'\n(?P<body>.*?)^ {10}PY$",
        workflow,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert len(blocks) >= 20
    for number, body in enumerate(blocks, start=1):
        compile(textwrap.dedent(body), f"railway-telegram.yml:{number}", "exec")


def test_controller_embedded_python_is_syntactically_valid() -> None:
    controller = CONTROLLER.read_text(encoding="utf-8")
    blocks = re.findall(
        r"<<'PY'[^\n]*\n(?P<body>.*?)^PY$",
        controller,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert len(blocks) >= 6
    for number, body in enumerate(blocks, start=1):
        compile(textwrap.dedent(body), f"controller.sh:{number}", "exec")
