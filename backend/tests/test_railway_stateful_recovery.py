"""Portable recovery and writer-pause contracts for Railway production."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from seiche import stateful_cutover as cutover
from seiche import stateful_migration as migration
from seiche import stateful_recovery as recovery
from seiche import palimpsest_china_activation as palimpsest_activation


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RECOVERY_WORKFLOW = (
    REPOSITORY_ROOT / ".github" / "workflows" / "railway-stateful-recovery.yml"
)
STATEFUL_DOCKERFILE = REPOSITORY_ROOT / "ops" / "railway" / "Dockerfile.stateful"


def _iso(moment: datetime) -> str:
    return (
        moment.astimezone(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


def _railway(platform: Path) -> dict[str, str]:
    return {
        "deployment_id": "11111111-1111-4111-8111-111111111111",
        "project_id": "22222222-2222-4222-8222-222222222222",
        "environment_id": "33333333-3333-4333-8333-333333333333",
        "service_id": "44444444-4444-4444-8444-444444444444",
        "volume_id": "55555555-5555-4555-8555-555555555555",
        "volume_name": "seiche-stateful-data",
        "volume_mount_path": str(platform),
        "region": "asia-southeast1",
    }


def _activation_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, str], dict[str, object]]:
    platform = tmp_path / "platform"
    monkeypatch.setattr(migration, "PLATFORM_ROOT", platform)
    now = datetime.now(UTC).replace(microsecond=0)
    snapshot_id = now.strftime("%Y%m%dT%H%M%SZ")
    request = {
        "request_id": "5" * 64,
        "commit": "a" * 40,
        "snapshot_id": snapshot_id,
        "source_revision": "a" * 40,
        "source_content_set_sha256": "d" * 64,
    }
    candidate = {
        "schema": cutover.CANDIDATE_RECEIPT_SCHEMA,
        "request": {
            "id": request["request_id"],
            "sha256": "3" * 64,
            "commit": request["commit"],
            "tree": "b" * 40,
            "source_shadow_receipt_sha256": "2" * 64,
        },
        "fence": {
            "sha256": "4" * 64,
            "frozen_at": _iso(now - timedelta(minutes=20)),
            "expires_at": _iso(now + timedelta(hours=3)),
        },
        "bundle": {
            "schema": migration.BACKUP_SCHEMA,
            "snapshot_id": snapshot_id,
            "source_revision": request["commit"],
            "source_inventory_sha256": "6" * 64,
            "source_content_set_sha256": request["source_content_set_sha256"],
            "member_sha256": {name: "7" * 64 for name in migration._BACKUP_MEMBERS},
            "total_bytes": 12345,
        },
        "database": {
            "name": migration.derive_database_name(
                snapshot_id,
                str(request["source_content_set_sha256"]),
            ),
            "critical_table_counts": [11, 21, 31, 41],
            "critical_table_count_floor": [10, 20, 30, 40],
            "restore": "pass",
        },
        "filesystem": {
            "generation": (
                f"cutover-{snapshot_id}-"
                f"{str(request['source_content_set_sha256'])[:16]}"
            ),
            "tree_sha256": {
                "market": "7" * 64,
                "nbs": "8" * 64,
                "api": "9" * 64,
                "palimpsest-china": "6" * 64,
            },
            "api_sqlite_quick_check": "pass",
            "nbs_full_store_audit_contract": "seiche.nbs-full-store-audit.v1",
            "nbs_full_store_audit_result": "verified_head",
            "palimpsest_china_state_audit_contract": (
                "seiche.palimpsest-china-activation-state.v1"
            ),
            "palimpsest_china_state_audit_result": "verified",
        },
        "railway": _railway(platform),
        "authority": {
            "mode": "cutover_candidate",
            "source": "none",
            "hetzner_writers_frozen": True,
            "railway_writers_started": False,
            "public_traffic_enabled": False,
        },
        "timing": {
            "started_at": _iso(now - timedelta(minutes=10)),
            "completed_at": _iso(now - timedelta(minutes=5)),
        },
        "research_only": True,
        "can_publish": False,
        "can_execute": False,
    }
    generation = platform / "generations" / str(candidate["filesystem"]["generation"])
    for name in (
        "market/raw",
        "nbs/public/revisions",
        "api",
        "palimpsest-china/receipts",
    ):
        (generation / name).mkdir(parents=True, exist_ok=True)
    (generation / "palimpsest-china").chmod(0o750)
    (generation / "palimpsest-china" / "receipts").chmod(0o700)
    (generation / "market" / "raw" / "sample.json").write_text("{}\n")
    (generation / "nbs" / "public" / "README.txt").write_text("verified\n")
    with sqlite3.connect(generation / "api" / "seiche.sqlite") as database:
        database.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        database.execute("INSERT INTO sample(value) VALUES ('ready')")
    palimpsest_audit = palimpsest_activation.audit_activation_state(
        generation / "palimpsest-china",
        root_uid=os.geteuid(),
        root_gid=os.getegid(),
        api_uid=os.geteuid(),
        api_gid=os.getegid(),
        declared_state_root=Path("/var/lib/seiche-palimpsest-china"),
    )
    candidate["palimpsest_china_state"] = migration.palimpsest_china_state_from_audit(
        palimpsest_audit
    )
    shadow = {
        "schema": migration.RECEIPT_SCHEMA,
        "request": {
            "id": "1" * 64,
            "sha256": "2" * 64,
            "commit": request["commit"],
            "tree": "b" * 40,
            "source_archive_sha256": "3" * 64,
            "source_bundle_sha256": "4" * 64,
            "source_release_receipt_sha256": "5" * 64,
            "source_recovery_receipt_sha256": "6" * 64,
        },
        "authority": {
            "mode": "shadow",
            "source": "hetzner",
            "source_writers_frozen": False,
            "public_traffic_enabled": False,
            "workers_started": False,
        },
        "bundle": {},
        "database": {},
        "filesystem": {},
        "palimpsest_china_state": candidate["palimpsest_china_state"],
        "railway": {},
        "timing": {},
        "research_only": True,
        "can_publish": False,
        "can_execute": False,
    }
    shadow_body = migration.canonical_document(shadow)
    candidate["request"]["source_shadow_receipt_sha256"] = hashlib.sha256(
        shadow_body
    ).hexdigest()
    shadow_receipts = platform / "receipts"
    shadow_receipts.mkdir(parents=True)
    (shadow_receipts / ("e" * 64 + ".json")).write_bytes(shadow_body)
    token = "edge-token-" + "x" * 32
    grant = {
        "public_base_url": "https://api.seiche.info",
        "public_probe_sha256": "1" * 64,
        "activated_at": _iso(now - timedelta(minutes=4)),
    }
    activation = cutover.render_activation_receipt(
        candidate,
        grant,
        worker_commands=cutover.worker_commands(),
        workers_started_at=_iso(now - timedelta(minutes=3)),
    )
    activation_path = (
        platform / "cutover-receipts" / (f"{request['request_id']}.activation.json")
    )
    activation_path.parent.mkdir(parents=True)
    candidate_path = (
        platform / "cutover-receipts" / (f"{request['request_id']}.candidate.json")
    )
    candidate_path.write_bytes(migration.canonical_document(candidate))
    activation_path.write_bytes(migration.canonical_document(activation))
    restore = cutover.CutoverRestore(
        candidate,
        "postgresql://generation-only",
        candidate_path,
        generation,
    )
    base = {
        "PORT": "8080",
        "RAILWAY_DEPLOYMENT_ID": _railway(platform)["deployment_id"],
        "RAILWAY_PROJECT_ID": _railway(platform)["project_id"],
        "RAILWAY_ENVIRONMENT_ID": _railway(platform)["environment_id"],
        "RAILWAY_SERVICE_ID": _railway(platform)["service_id"],
        "SEICHE_RAILWAY_VOLUME_ID": _railway(platform)["volume_id"],
        "RAILWAY_VOLUME_NAME": _railway(platform)["volume_name"],
        "RAILWAY_VOLUME_MOUNT_PATH": _railway(platform)["volume_mount_path"],
        "RAILWAY_REPLICA_REGION": _railway(platform)["region"],
    }
    candidate_environment = cutover.candidate_environment(
        base,
        restore,
        edge_token=token,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )
    environment = cutover.production_environment(
        candidate_environment,
        activation,
        receipt_path=activation_path,
    )
    return platform, environment, activation


def _request(activation: dict[str, object], *, now: datetime) -> dict[str, object]:
    return {
        "schema": recovery.REQUEST_SCHEMA,
        "repository": migration.REPOSITORY,
        "workflow": recovery.WORKFLOW,
        "commit": activation["commit"],
        "deployment_id": activation["railway"]["deployment_id"],
        "activation_receipt_sha256": hashlib.sha256(
            migration.canonical_document(activation)
        ).hexdigest(),
        "request_id": "c" * 64,
        "snapshot_id": now.strftime("%Y%m%dT%H%M%SZ"),
        "requested_at": _iso(now),
        "confirmation": recovery.CONFIRMATION,
    }


def test_recovery_request_is_activation_bound_and_published_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform, environment, activation = _activation_context(tmp_path, monkeypatch)
    now = datetime.now(UTC).replace(microsecond=0)
    request = _request(activation, now=now)
    body = migration.canonical_document(request)

    published = recovery.publish_request(
        body,
        environment,
        platform_root=platform,
        now=now,
        runtime_gid=os.getgid(),
    )
    assert published == request
    assert (
        recovery.publish_request(
            body,
            environment,
            platform_root=platform,
            now=now,
            runtime_gid=os.getgid(),
        )
        == request
    )
    path = platform / "recovery-requests" / f"{request['request_id']}.json"
    assert path.read_bytes() == body
    assert path.stat().st_mode & 0o777 == 0o440

    changed = dict(request)
    changed["snapshot_id"] = (now + timedelta(seconds=1)).strftime("%Y%m%dT%H%M%SZ")
    changed["requested_at"] = _iso(now + timedelta(seconds=1))
    with pytest.raises(cutover.CutoverContractError, match="immutable.*differs"):
        recovery.publish_request(
            migration.canonical_document(changed),
            environment,
            platform_root=platform,
            now=now + timedelta(seconds=1),
            runtime_gid=os.getgid(),
        )


def test_export_emits_backup_v4_and_seals_only_after_writer_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform, environment, activation = _activation_context(tmp_path, monkeypatch)
    now = datetime.now(UTC).replace(microsecond=0)
    request = _request(activation, now=now)

    monkeypatch.setattr(
        migration,
        "inspect_postgres_counts",
        lambda _dsn: (10, 20, 30, 40),
    )
    monkeypatch.setattr(migration, "_audit_nbs", lambda _root: "verified_head")

    def dump(destination: Path, _dsn: str) -> None:
        destination.write_bytes(b"PGDMP" + b"x" * 2048)

    monkeypatch.setattr(recovery, "_dump_postgres", dump)
    exported = recovery.export_snapshot(
        environment,
        request,
        platform_root=platform,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )
    bundle_root = platform / "recovery-snapshots" / str(request["snapshot_id"])
    assert {
        item.name for item in bundle_root.iterdir()
    } == migration._ALL_BACKUP_MEMBERS
    assert exported.bundle.root == bundle_root
    assert exported.bundle.counts_floor == (10, 20, 30, 40)
    assert exported.nbs_audit_result == "verified_head"
    migration.validate_tar_contract(
        bundle_root / "var-lib-seiche.tgz",
        expected_roots=frozenset({"seiche", "seiche-nbs"}),
    )
    migration.validate_tar_contract(
        bundle_root / "api-data.tgz",
        expected_roots=frozenset({"api-data"}),
    )
    migration.validate_tar_contract(
        bundle_root / "palimpsest-china.tgz",
        expected_roots=frozenset({"seiche-palimpsest-china"}),
    )
    audit = migration._decode_canonical_json(
        (bundle_root / "palimpsest-china-state.json").read_bytes(),
        label="test Palimpsest China audit",
    )
    assert audit["state_root"] == "/var/lib/seiche-palimpsest-china"
    assert audit["active_activation_id"] is None
    assert audit["pending_candidate_activation_id"] is None

    stopped_at = exported.started_at
    restarted_at = _iso(datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=1))
    receipt_path, receipt = recovery.finalize_receipt(
        environment,
        request,
        exported,
        writers_stopped_at=stopped_at,
        writers_restarted_at=restarted_at,
        worker_commands=cutover.worker_commands(),
        platform_root=platform,
        runtime_gid=os.getgid(),
    )
    assert receipt_path.is_file()
    assert receipt_path.name == (
        f"{request['snapshot_id']}-{request['request_id']}.json"
    )
    assert receipt["authority"] == {
        "source": "railway",
        "authority_changed": False,
        "public_api_remained_online": True,
        "writers_paused_for_export": True,
        "writers_restarted": True,
    }
    candidate = json.loads(
        Path(environment["SEICHE_RAILWAY_CANDIDATE_RECEIPT_PATH"]).read_bytes()
    )
    shadow = json.loads(next((platform / "receipts").iterdir()).read_bytes())
    validated = recovery.validate_receipt(
        receipt,
        request=request,
        activation_receipt=activation,
        candidate_receipt=candidate,
        shadow_receipt=shadow,
        railway=_railway(platform),
        bundle_root=bundle_root,
    )
    assert validated["snapshot"]["backup_schema"] == migration.BACKUP_SCHEMA
    assert validated["filesystem"]["palimpsest_china_state_audit_result"] == (
        "verified"
    )
    replacements = {
        "audit_schema": "seiche.palimpsest-china-activation-state.v0",
        "tree_sha256": "0" * 64,
        "active_activation_id": "0" * 64,
        "pending_candidate_activation_id": "0" * 64,
    }
    for field, replacement in replacements.items():
        tampered_shadow = copy.deepcopy(shadow)
        tampered_shadow["palimpsest_china_state"][field] = replacement
        rebound_candidate = copy.deepcopy(candidate)
        rebound_candidate["request"]["source_shadow_receipt_sha256"] = hashlib.sha256(
            migration.canonical_document(tampered_shadow)
        ).hexdigest()
        with pytest.raises(
            recovery.RecoveryContractError,
            match="Palimpsest China",
        ):
            recovery.validate_shadow_chain(
                tampered_shadow,
                candidate_receipt=rebound_candidate,
            )
    downgraded_shadow = copy.deepcopy(shadow)
    downgraded_shadow["schema"] = "seiche.railway-stateful-shadow-receipt.v2"
    rebound_candidate = copy.deepcopy(candidate)
    rebound_candidate["request"]["source_shadow_receipt_sha256"] = hashlib.sha256(
        migration.canonical_document(downgraded_shadow)
    ).hexdigest()
    with pytest.raises(recovery.RecoveryContractError, match="binding"):
        recovery.validate_shadow_chain(
            downgraded_shadow,
            candidate_receipt=rebound_candidate,
        )
    for field, replacement in replacements.items():
        tampered_receipt = copy.deepcopy(receipt)
        tampered_receipt["palimpsest_china_state"][field] = replacement
        with pytest.raises(
            recovery.RecoveryContractError,
            match="Palimpsest China",
        ):
            recovery.validate_receipt(
                tampered_receipt,
                request=request,
                activation_receipt=activation,
                candidate_receipt=candidate,
                shadow_receipt=shadow,
                railway=_railway(platform),
                bundle_root=bundle_root,
            )
    downgraded_receipt = copy.deepcopy(receipt)
    downgraded_receipt["schema"] = "seiche.railway-recovery-export-receipt.v2"
    with pytest.raises(recovery.RecoveryContractError, match="binding"):
        recovery.validate_receipt(
            downgraded_receipt,
            request=request,
            activation_receipt=activation,
            candidate_receipt=candidate,
            shadow_receipt=shadow,
            railway=_railway(platform),
            bundle_root=bundle_root,
        )

    sealed_at = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=3)
    offsite_digests = {
        "activation-receipt.json": receipt["activation_receipt_sha256"],
        "candidate-receipt.json": receipt["candidate_receipt_sha256"],
        "shadow-receipt.json": receipt["shadow_receipt_sha256"],
        "request.json": receipt["request_sha256"],
        "recovery-receipt.json": hashlib.sha256(
            migration.canonical_document(receipt)
        ).hexdigest(),
        "SHA256SUMS": receipt["snapshot"]["inventory_sha256"],
        "proof/reverse-restore.json": "e" * 64,
        **receipt["snapshot"]["member_sha256"],
    }
    key_root = f"seiche/recovery/{request['snapshot_id']}/{request['request_id']}"
    offsite = {
        "schema": recovery.OFFSITE_RECEIPT_SCHEMA,
        "repository": migration.REPOSITORY,
        "workflow": recovery.WORKFLOW,
        "commit": receipt["commit"],
        "request_id": request["request_id"],
        "snapshot_id": request["snapshot_id"],
        "recovery_receipt_sha256": offsite_digests["recovery-receipt.json"],
        "reverse_restore_proof_sha256": offsite_digests["proof/reverse-restore.json"],
        "palimpsest_china_state": receipt["palimpsest_china_state"],
        "bucket": "seiche-recovery-evidence",
        "prefix": "seiche/recovery",
        "object_lock_mode": "COMPLIANCE",
        "retain_until": _iso(sealed_at + timedelta(days=30)),
        "objects": {
            name: {
                "key": f"{key_root}/{name}",
                "sha256": digest,
                "size": 1024,
                "version_id": f"version-{index}",
            }
            for index, (name, digest) in enumerate(offsite_digests.items())
        },
        "sealed_at": _iso(sealed_at),
        "authority_changed": False,
        "research_only": True,
        "can_publish": False,
        "can_execute": False,
    }
    assert (
        recovery.validate_offsite_receipt(
            offsite,
            recovery_receipt=receipt,
            now=sealed_at,
        )
        == offsite
    )
    for field, replacement in replacements.items():
        tampered_state = copy.deepcopy(offsite)
        tampered_state["palimpsest_china_state"][field] = replacement
        with pytest.raises(
            recovery.RecoveryContractError,
            match="Palimpsest China",
        ):
            recovery.validate_offsite_receipt(
                tampered_state,
                recovery_receipt=receipt,
                now=sealed_at,
            )
    downgraded_offsite = copy.deepcopy(offsite)
    downgraded_offsite["schema"] = "seiche.railway-offsite-recovery-receipt.v2"
    with pytest.raises(recovery.RecoveryContractError, match="binding"):
        recovery.validate_offsite_receipt(
            downgraded_offsite,
            recovery_receipt=receipt,
            now=sealed_at,
        )
    tampered = copy.deepcopy(offsite)
    tampered["objects"]["seiche.dump"]["version_id"] = ""
    with pytest.raises(recovery.RecoveryContractError, match="object proof"):
        recovery.validate_offsite_receipt(
            tampered,
            recovery_receipt=receipt,
            now=sealed_at,
        )

    resumed = recovery.export_snapshot(
        environment,
        request,
        platform_root=platform,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )
    resumed_path, resumed_receipt = recovery.finalize_receipt(
        environment,
        request,
        resumed,
        writers_stopped_at=resumed.started_at,
        writers_restarted_at=_iso(
            datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=2)
        ),
        worker_commands=cutover.worker_commands(),
        platform_root=platform,
        runtime_gid=os.getgid(),
    )
    assert resumed_path == receipt_path
    assert resumed_receipt == receipt


def test_recovery_export_rejects_active_candidate_state_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform, environment, activation = _activation_context(tmp_path, monkeypatch)
    candidate_path = Path(environment["SEICHE_RAILWAY_CANDIDATE_RECEIPT_PATH"])
    candidate = json.loads(candidate_path.read_bytes())
    candidate["palimpsest_china_state"]["active_activation_id"] = "a" * 64
    shadow_path = next((platform / "receipts").iterdir())
    shadow = json.loads(shadow_path.read_bytes())
    shadow["palimpsest_china_state"]["active_activation_id"] = "a" * 64
    shadow_body = migration.canonical_document(shadow)
    shadow_path.write_bytes(shadow_body)
    candidate["request"]["source_shadow_receipt_sha256"] = hashlib.sha256(
        shadow_body
    ).hexdigest()
    candidate_body = migration.canonical_document(candidate)
    candidate_path.write_bytes(candidate_body)
    candidate_digest = hashlib.sha256(candidate_body).hexdigest()
    environment["SEICHE_RAILWAY_CANDIDATE_RECEIPT_SHA256"] = candidate_digest
    activation["candidate_receipt_sha256"] = candidate_digest
    activation_path = Path(environment["SEICHE_RAILWAY_ACTIVATION_RECEIPT_PATH"])
    activation_body = migration.canonical_document(activation)
    activation_path.write_bytes(activation_body)
    environment["SEICHE_RAILWAY_ACTIVATION_RECEIPT_SHA256"] = hashlib.sha256(
        activation_body
    ).hexdigest()
    request = _request(activation, now=datetime.now(UTC).replace(microsecond=0))

    monkeypatch.setattr(migration, "_audit_nbs", lambda _root: "verified_head")
    with pytest.raises(
        recovery.RecoveryContractError,
        match="differs from cutover candidate",
    ):
        recovery.export_snapshot(
            environment,
            request,
            platform_root=platform,
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )


def test_recovery_restore_probe_propagates_production_runtime_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = migration.BackupBundle(
        root=tmp_path / "bundle",
        snapshot_id="20260824T010203Z",
        source_revision="a" * 40,
        inventory_sha256="b" * 64,
        content_set_sha256="c" * 64,
        member_sha256={},
        counts_floor=(1, 2, 3, 4),
        total_bytes=1,
        schema=migration.BACKUP_SCHEMA,
        palimpsest_china_state_audit={},
    )
    observed: dict[str, object] = {}

    def restore(
        received: migration.BackupBundle,
        staging: Path,
        *,
        runtime_uid: int,
        runtime_gid: int,
    ) -> tuple[str, dict[str, str]]:
        observed.update(
            {
                "bundle": received,
                "staging_parent": staging.parent,
                "runtime_uid": runtime_uid,
                "runtime_gid": runtime_gid,
            }
        )
        return "verified_head", {"palimpsest-china": "d" * 64}

    monkeypatch.setattr(migration, "restore_filesystem_generation", restore)
    result = recovery._restored_filesystem_identity(
        bundle,
        scratch_parent=tmp_path,
        runtime_uid=10_001,
        runtime_gid=10_001,
    )

    assert result == ("verified_head", {"palimpsest-china": "d" * 64})
    assert observed == {
        "bundle": bundle,
        "staging_parent": tmp_path,
        "runtime_uid": 10_001,
        "runtime_gid": 10_001,
    }


class _Child:
    def __init__(self, code: int | None = None) -> None:
        self.code = code
        self.returncode = code

    def poll(self) -> int | None:
        self.returncode = self.code
        return self.code


def test_production_supervisor_orders_pause_export_restart_and_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = {"request_id": "c" * 64}
    calls: list[str] = []
    initial_writers = [_Child(), _Child()]
    api = _Child()
    replacement_writers = [_Child(), _Child()]
    exported = object()

    monkeypatch.setattr(
        recovery,
        "next_pending_request",
        lambda _environment: request if "export" not in calls else None,
    )

    def export(
        _environment: dict[str, str],
        _request: dict[str, str],
        **_kwargs: object,
    ) -> object:
        calls.append("export")
        return exported

    monkeypatch.setattr(recovery, "export_snapshot", export)

    def restart(*_args: object, **_kwargs: object) -> list[_Child]:
        calls.append("restart")
        return replacement_writers

    monkeypatch.setattr(cutover, "_start_writer_children", restart)

    def terminate(children: list[_Child]) -> None:
        if children == initial_writers:
            calls.append("stop-writers")

    monkeypatch.setattr(cutover, "_terminate_children", terminate)

    def finalize(*_args: object, **_kwargs: object) -> tuple[Path, dict[str, bool]]:
        calls.append("receipt")
        api.code = 71
        return Path("/receipt.json"), {"sealed": True}

    monkeypatch.setattr(recovery, "finalize_receipt", finalize)

    result = cutover._serve_production(
        {"MODE": "production"},
        writers=initial_writers,  # type: ignore[arg-type]
        api=api,  # type: ignore[arg-type]
        commands=cutover.worker_commands(),
        poll_seconds=1,
    )
    assert result == 71
    assert calls[:4] == ["stop-writers", "export", "restart", "receipt"]


def test_production_supervisor_does_not_receipt_after_api_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = {"request_id": "d" * 64}
    calls: list[str] = []
    writers = [_Child(), _Child()]
    api = _Child()
    monkeypatch.setattr(
        recovery,
        "next_pending_request",
        lambda _environment: request,
    )

    def export(
        _environment: dict[str, str],
        _request: dict[str, str],
        **_kwargs: object,
    ) -> object:
        calls.append("export")
        api.code = 73
        return object()

    monkeypatch.setattr(recovery, "export_snapshot", export)
    monkeypatch.setattr(
        cutover,
        "_terminate_children",
        lambda _children: calls.append("stop"),
    )
    monkeypatch.setattr(
        cutover,
        "_start_writer_children",
        lambda *_args, **_kwargs: calls.append("restart"),
    )
    monkeypatch.setattr(
        recovery,
        "finalize_receipt",
        lambda *_args, **_kwargs: calls.append("receipt"),
    )

    result = cutover._serve_production(
        {"MODE": "production"},
        writers=writers,  # type: ignore[arg-type]
        api=api,  # type: ignore[arg-type]
        commands=cutover.worker_commands(),
        poll_seconds=1,
    )
    assert result == 73
    assert "export" in calls
    assert "restart" not in calls
    assert "receipt" not in calls


def test_production_supervisor_does_not_restart_writers_after_export_and_api_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = {"request_id": "e" * 64}
    calls: list[str] = []
    writers = [_Child(), _Child()]
    api = _Child()
    monkeypatch.setattr(recovery, "next_pending_request", lambda _environment: request)

    def export(
        _environment: dict[str, str],
        _request: dict[str, str],
        **_kwargs: object,
    ) -> object:
        calls.append("export")
        api.code = 79
        raise RuntimeError("simultaneous export and API failure")

    monkeypatch.setattr(recovery, "export_snapshot", export)
    monkeypatch.setattr(
        cutover, "_terminate_children", lambda _children: calls.append("stop")
    )
    monkeypatch.setattr(
        cutover,
        "_start_writer_children",
        lambda *_args, **_kwargs: calls.append("restart"),
    )
    monkeypatch.setattr(
        recovery,
        "finalize_receipt",
        lambda *_args, **_kwargs: calls.append("receipt"),
    )

    result = cutover._serve_production(
        {"MODE": "production"},
        writers=writers,  # type: ignore[arg-type]
        api=api,  # type: ignore[arg-type]
        commands=cutover.worker_commands(),
        poll_seconds=1,
    )
    assert result == 79
    assert "export" in calls
    assert "restart" not in calls
    assert "receipt" not in calls


def test_recovery_workflow_is_gated_portable_and_non_authoritative() -> None:
    text = RECOVERY_WORKFLOW.read_text(encoding="utf-8")
    dockerfile = STATEFUL_DOCKERFILE.read_text(encoding="utf-8")

    assert 'cron: "17 */6 * * *"' in text
    assert 'cron: "31 2 * * *"' in text
    assert "vars.RAILWAY_STATEFUL_PHASE6_ENABLED == 'true'" in text
    for environment in (
        "railway-stateful-recovery-admin",
        "railway-stateful-recovery-monitor",
        "railway-stateful-recovery-export",
    ):
        assert f"environment: {environment}" in text
    assert "ENABLE_NATIVE_BACKUPS_AND_LOCK_CANARIES" in text
    assert "PROVE_EXTERNAL_OBJECT_LOCK_ONLY" in text
    assert "EXPORT_WITHOUT_AUTHORITY_CHANGE" in text
    assert "volumeInstanceBackupScheduleUpdate" in text
    assert "volumeInstanceBackupCreate" in text
    assert "volumeInstanceBackupLock" in text
    assert "postgres pitr enable" in text
    assert "postgres pitr schedule set --daily --weekly --monthly" in text
    assert "postgres pitr backup lock" in text
    assert "--object-lock-mode COMPLIANCE" in text
    assert "--checksum-algorithm SHA256" in text
    assert "--checksum-mode ENABLED" in text
    assert "api-continuity.failed" in text
    assert "seiche.railway-reverse-restore-proof.v1" in text
    assert "seiche.railway-offsite-recovery-receipt.v3" in text
    assert '"palimpsest_china_state": receipt["palimpsest_china_state"]' in text
    assert '"palimpsest_china_state": recovery["palimpsest_china_state"]' in text
    assert text.count("--candidate candidate-receipt.json") == 3
    assert text.count("--shadow shadow-receipt.json") == 3
    assert "candidate-receipt.json shadow-receipt.json" in text
    assert "seiche.railway-offsite-preflight-receipt.v1" in text
    assert text.count("actions/attest-build-provenance@") == 3
    assert (
        "postgres:17@sha256:"
        "a65e6a841f6c4dbc4abda3d67fa3bc21824e9611064fcd82e87ea67aad60a0c3"
    ) in text
    assert (
        "FROM postgres:17@sha256:"
        "a65e6a841f6c4dbc4abda3d67fa3bc21824e9611064fcd82e87ea67aad60a0c3"
    ) in dockerfile
    assert "PATH=/opt/postgresql/17/bin:$PATH" in dockerfile
    assert "^pg_dump \\(PostgreSQL\\) 17\\." in dockerfile
    for forbidden in (
        "volume files delete",
        "volumeInstanceBackupDelete",
        "volumeInstanceBackupRestore",
        "postgres pitr restore",
        "postgres pitr disable",
        "--overwrite",
        "seiche-railway-edge-mode.sh",
        "RAILWAY_BECOMES_SOLE_WRITER",
    ):
        assert forbidden not in text
