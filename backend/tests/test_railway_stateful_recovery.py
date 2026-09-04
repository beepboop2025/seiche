"""Portable recovery and writer-pause contracts for Railway production."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import textwrap

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from seiche import agent_room
from seiche import stateful_cutover as cutover
from seiche import stateful_migration as migration
from seiche import stateful_recovery as recovery
from seiche import palimpsest_china_activation as palimpsest_activation

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RECOVERY_WORKFLOW = (
    REPOSITORY_ROOT / ".github" / "workflows" / "railway-stateful-recovery.yml"
)
STATEFUL_DOCKERFILE = REPOSITORY_ROOT / "ops" / "railway" / "Dockerfile.stateful"
RECOVERY_RUNBOOK = REPOSITORY_ROOT / "ops" / "deploy" / "RAILWAY-STATEFUL-RECOVERY.md"
OBJECT_LOCK_CLIENT = REPOSITORY_ROOT / "ops" / "deploy" / "seiche-s3-object-lock.sh"


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


def _verified_agent_room_audit() -> dict[str, object]:
    return {
        "schema": migration.AGENT_ROOM_RESTORE_AUDIT_SCHEMA,
        "result": "verified",
        "server_key_id": "8" * 64,
        "participant_count": 1,
        "room_count": 1,
        "event_count": 1,
        "state_sha256": "9" * 64,
        "non_executable": True,
        "execution_authority": "none",
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
            "agent_room_audit": migration.absent_agent_room_audit(),
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
        "filesystem": {
            "agent_room_audit": migration.absent_agent_room_audit(),
        },
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
        "download_bearer_sha256": hashlib.sha256(b"r" * 32).hexdigest(),
        "download_expires_at": _iso(now + timedelta(hours=1)),
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
    different_shadow = copy.deepcopy(shadow)
    different_shadow["filesystem"]["agent_room_audit"] = _verified_agent_room_audit()
    rebound_candidate = copy.deepcopy(candidate)
    rebound_candidate["request"]["source_shadow_receipt_sha256"] = hashlib.sha256(
        migration.canonical_document(different_shadow)
    ).hexdigest()
    with pytest.raises(recovery.RecoveryContractError, match="Agent Room state"):
        recovery.validate_shadow_chain(
            different_shadow,
            candidate_receipt=rebound_candidate,
        )

    different_receipt = copy.deepcopy(receipt)
    different_receipt["filesystem"]["agent_room_audit"] = _verified_agent_room_audit()
    with pytest.raises(
        recovery.RecoveryContractError,
        match="filesystem audit differs from bundle",
    ):
        recovery.validate_receipt(
            different_receipt,
            request=request,
            activation_receipt=activation,
            candidate_receipt=candidate,
            shadow_receipt=shadow,
            railway=_railway(platform),
            bundle_root=bundle_root,
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
    downgraded_shadow["schema"] = "seiche.railway-stateful-shadow-receipt.v3"
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
    downgraded_receipt["schema"] = "seiche.railway-recovery-export-receipt.v3"
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


def test_recovery_snapshots_agent_room_online_and_audits_restored_key(
    tmp_path: Path,
) -> None:
    api = tmp_path / "live-api"
    api.mkdir(mode=0o700)
    api.chmod(0o700)
    with sqlite3.connect(api / "seiche.sqlite") as database:
        database.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        database.execute("INSERT INTO sample(value) VALUES ('ready')")
    server_key = Ed25519PrivateKey.from_private_bytes(bytes([51]) * 32)
    participant_key = Ed25519PrivateKey.from_private_bytes(bytes([52]) * 32)
    attest_root = api / "_attest"
    attest_root.mkdir(mode=0o700)
    attest_root.chmod(0o700)
    operator_key = attest_root / "operator_key.pem"
    operator_key.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    operator_key.chmod(0o600)
    room_root = api / "_agent_room"
    room_root.mkdir(mode=0o700)
    room_root.chmod(0o700)
    source_database = room_root / "agent-room.sqlite"
    store = agent_room.AgentRoomStore(
        source_database,
        server_private_key=server_key,
    )
    public_key = participant_key.public_key().public_bytes_raw().hex()
    store.provision_participant("recovery-agent", public_key)
    created = store.create_room("recovery-room", owner_id="recovery-agent")
    event = agent_room.build_client_event(
        room_id="recovery-room",
        actor_id="recovery-agent",
        client_key_id=agent_room.ed25519_key_id(public_key),
        kind="proposal",
        expected_sequence=0,
        expected_head_hash=str(created["genesis_hash"]),
        nonce="recovery-fixture-000001",
        client_created_at=datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        payload={"purpose": "portable-recovery-proof"},
    )
    store.append_event(
        event,
        client_signature_hex=participant_key.sign(
            agent_room.client_signing_bytes(event)
        ).hex(),
    )
    seal = attest_root / agent_room.AGENT_ROOM_INITIALIZATION_SEAL_FILENAME
    agent_room.create_initialization_seal(
        seal,
        server_private_key=server_key,
    )
    destination = tmp_path / "snapshot-api"

    audit = recovery._snapshot_api(api, destination)

    restored_database = destination / "_agent_room" / "agent-room.sqlite"
    assert audit["result"] == "verified"
    assert audit["participant_count"] == 1
    assert audit["room_count"] == 1
    assert audit["event_count"] == 1
    assert restored_database.stat().st_ino != source_database.stat().st_ino
    assert stat.S_IMODE(restored_database.stat().st_mode) == 0o600
    assert (
        destination / "_attest" / agent_room.AGENT_ROOM_INITIALIZATION_SEAL_FILENAME
    ).is_file()
    assert migration.audit_agent_room_state(destination) == audit


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
        agent_room_audit_out: dict[str, object] | None = None,
    ) -> tuple[str, dict[str, str]]:
        observed.update(
            {
                "bundle": received,
                "staging_parent": staging.parent,
                "runtime_uid": runtime_uid,
                "runtime_gid": runtime_gid,
            }
        )
        if agent_room_audit_out is not None:
            agent_room_audit_out.update(migration.absent_agent_room_audit())
        return "verified_head", {"palimpsest-china": "d" * 64}

    monkeypatch.setattr(migration, "restore_filesystem_generation", restore)
    result = recovery._restored_filesystem_identity(
        bundle,
        scratch_parent=tmp_path,
        runtime_uid=10_001,
        runtime_gid=10_001,
    )

    assert result == (
        "verified_head",
        {"palimpsest-china": "d" * 64},
        migration.absent_agent_room_audit(),
    )
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
    request = {"request_id": "c" * 64, "snapshot_id": "20260902T220000Z"}
    calls: list[str] = []
    initial_writers = [_Child(), _Child()]
    api = _Child()
    replacement_writers = [_Child(), _Child()]
    exported = object()

    monkeypatch.setattr(
        recovery,
        "next_pending_request",
        lambda _environment, **_kwargs: request if "export" not in calls else None,
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
        lambda _environment, **_kwargs: request,
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
    monkeypatch.setattr(
        recovery,
        "next_pending_request",
        lambda _environment, **_kwargs: request,
    )

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
    object_lock_client = OBJECT_LOCK_CLIENT.read_text(encoding="utf-8")

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
    assert text.count("SEICHE_OFFSITE_S3_SSE_C_KEY_B64") == 2
    assert text.count('seiche-s3-object-lock.sh" put-verify') == 4
    assert (
        text.count("d2dc4df7edbd93913606f27c2fef7dd7ed19e4ebf659251dbf83b759dd5e816c")
        == 2
    )
    assert "DownloadedSHA256" in text
    assert "--content-md5" in object_lock_client
    assert '"fileb://$KEY_PATH"' in object_lock_client
    assert "--sse-customer-algorithm AES256" in object_lock_client
    assert "get-object-lock-configuration" in object_lock_client
    assert '--version-id "$version_id"' in object_lock_client
    assert "AWS_REQUEST_CHECKSUM_CALCULATION=when_required" in object_lock_client
    assert "SSECustomerKeyVerified" in text
    assert "SSECustomerKeyMD5" not in text
    assert "api-continuity.failed" in text
    assert "seiche.railway-reverse-restore-proof.v1" in text
    assert "seiche.railway-offsite-recovery-receipt.v3" in text
    assert "seiche.railway-cutover-candidate-receipt.v3" not in text
    assert "from seiche.stateful_control import" in text
    assert "extract_log_result" in text
    assert '"palimpsest_china_state": receipt["palimpsest_china_state"]' in text
    assert '"palimpsest_china_state": recovery["palimpsest_china_state"]' in text
    assert text.count("--candidate candidate-receipt.json") == 2
    assert text.count("--shadow shadow-receipt.json") == 2
    assert "candidate-receipt.json shadow-receipt.json" in text
    assert "seiche.railway-offsite-preflight-receipt.v1" in text
    assert text.count("actions/attest-build-provenance@") == 3
    assert (
        "postgres:17@sha256:"
        "a65e6a841f6c4dbc4abda3d67fa3bc21824e9611064fcd82e87ea67aad60a0c3"
    ) in text
    assert (
        "FROM postgres:17.6-bookworm@sha256:"
        "45cd22f8d32e189d245403954882f88e7a8714301fda80dab6da90f1265b25a3"
    ) in dockerfile
    assert "git config --system --add safe.directory /workspace" in dockerfile
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


def test_recovery_ci_uses_signed_https_and_fixed_members_not_ssh_or_volume_files() -> (
    None
):
    workflow = RECOVERY_WORKFLOW.read_text(encoding="utf-8")

    assert "railway link" not in workflow
    assert "railway ssh" not in workflow
    assert "railway volume files" not in workflow
    assert not any(
        "railway volume " in line and " files " in line
        for line in workflow.replace("\\\n", " ").splitlines()
    )
    assert workflow.count("/api/internal/v1/railway-control/commands") == 2
    assert workflow.count("prepare_unsigned_command") == 2
    assert workflow.count("command_signing_bytes") == 2
    assert "/api/internal/v1/railway-control/recovery/$request_id/$member" in workflow
    assert '--header "X-Seiche-Edge-Token: $RAILWAY_EDGE_TOKEN"' in workflow
    assert '--header "Authorization: Bearer $download_bearer"' in workflow
    members = (
        "activation-receipt.json",
        "candidate-receipt.json",
        "shadow-receipt.json",
        "request.json",
        "recovery-receipt.json",
        "seiche.dump",
        "var-lib-seiche.tgz",
        "palimpsest-china.tgz",
        "palimpsest-china-state.json",
        "api-data.tgz",
        "table-counts.txt",
        "deployed-sha.txt",
        "manifest.env",
        "SHA256SUMS",
    )
    member_loop = workflow[workflow.index("for member in activation-receipt.json") :]
    member_loop = member_loop[: member_loop.index("; do")]
    assert all(name in member_loop for name in members)
    assert len(members) == 14


@pytest.mark.skipif(
    sys.platform != "linux", reason="helper targets GitHub's Linux runner"
)
def test_object_lock_client_pins_versions_and_hides_the_sse_key(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state = tmp_path / "fake-s3"
    state.mkdir()
    fake_aws = fake_bin / "aws"
    fake_aws.write_text(
        textwrap.dedent("""\
            #!/usr/bin/env bash
            set -euo pipefail
            state=${FAKE_AWS_STATE:?}
            log=$state/calls.log
            [ -z "${S3_SSE_C_KEY_B64:-}" ] || exit 89
            if [ "${1:-}" = --version ]; then
                echo 'aws-cli/2.36.35 Python/3.14.6 Linux/fake exe/x86_64.ubuntu.24'
                exit 0
            fi
            for argument in "$@"; do
                [ "$argument" != "$FAKE_RAW_KEY" ] || exit 90
            done
            command_line=" $* "
            if [[ "$command_line" == *' s3api get-object-lock-configuration '* ]]; then
                echo 'lock' >>"$log"
                echo '{"ObjectLockConfiguration":{"ObjectLockEnabled":"Enabled","Rule":{"DefaultRetention":{"Mode":"COMPLIANCE","Days":90}}}}'
                exit 0
            fi
            if [[ "$command_line" == *' s3api get-bucket-versioning '* ]]; then
                echo 'versioning' >>"$log"
                echo '{"Status":"Enabled"}'
                exit 0
            fi
            operation=
            body=
            metadata=
            version=
            key_file=
            output=
            previous=
            for argument in "$@"; do
                case "$previous" in
                    body) body=$argument ;;
                    metadata) metadata=$argument ;;
                    version) version=$argument ;;
                    key) key_file=${argument#fileb://} ;;
                esac
                previous=
                case "$argument" in
                    put-object|head-object|get-object) operation=$argument ;;
                    --body) previous=body ;;
                    --metadata) previous=metadata ;;
                    --version-id) previous=version ;;
                    --sse-customer-key) previous=key ;;
                    --*) ;;
                    *) output=$argument ;;
                esac
            done
            [ -n "$key_file" ]
            [ "$(stat -c '%a:%s' "$key_file")" = 600:32 ]
            case "$operation" in
                put-object)
                    cp -- "$body" "$state/object"
                    printf '%s\n' "$metadata" >"$state/metadata"
                    echo 'put' >>"$log"
                    echo '{}'
                    ;;
                head-object)
                    [ -f "$state/object" ] || exit 1
                    if [ -n "$version" ]; then
                        [ "$version" = version-1 ]
                        echo 'head:pinned:version-1' >>"$log"
                    else
                        echo 'head:current' >>"$log"
                    fi
                    size=$(stat -c %s "$state/object")
                    sha=$(sed 's/^sha256=//' "$state/metadata")
                    key_md5=$(openssl dgst -md5 -binary "$key_file" | base64 -w0)
                    printf '{"ContentLength":%s,"Metadata":{"sha256":"%s"},"SSECustomerAlgorithm":"AES256","SSECustomerKeyMD5":"%s","ObjectLockMode":"COMPLIANCE","ObjectLockRetainUntilDate":"2099-01-01T00:00:00Z","VersionId":"version-1"}\n' "$size" "$sha" "$key_md5"
                    ;;
                get-object)
                    [ "$version" = version-1 ]
                    echo 'get:pinned:version-1' >>"$log"
                    [ "${FAKE_FAIL_GET:-0}" != 1 ] || exit 42
                    cp -- "$state/object" "$output"
                    echo '{}'
                    ;;
                *) exit 91 ;;
            esac
            """),
        encoding="utf-8",
    )
    fake_aws.chmod(0o755)
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir(mode=0o700)
    source = tmp_path / "source.bin"
    source.write_bytes(b"closed recovery object\n")
    encoded_key = base64.b64encode(bytes(range(32))).decode("ascii")
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_AWS_STATE": str(state),
        "FAKE_RAW_KEY": encoded_key,
        "AWS_ACCESS_KEY_ID": "test-access",
        "AWS_SECRET_ACCESS_KEY": "test-secret",
        "AWS_DEFAULT_REGION": "eu-central",
        "S3_ENDPOINT": "https://objects.example.test",
        "S3_BUCKET": "locked-recovery",
        "S3_SSE_C_KEY_B64": encoded_key,
        "RUNNER_TEMP": str(runner_temp),
    }
    bucket_proof = tmp_path / "bucket.json"
    subprocess.run(
        [OBJECT_LOCK_CLIENT, "probe-bucket", bucket_proof],
        check=True,
        env=environment,
    )
    head_proof = tmp_path / "head.json"
    key = "seiche/recovery/request/object.bin"
    subprocess.run(
        [OBJECT_LOCK_CLIENT, "put-verify", source, key, head_proof],
        check=True,
        env=environment,
    )
    expected_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    proof = json.loads(head_proof.read_text(encoding="utf-8"))
    assert proof["VersionId"] == "version-1"
    assert proof["DownloadedSHA256"] == expected_sha
    assert proof["SSECustomerKeyVerified"] is True
    assert "SSECustomerKeyMD5" not in proof
    restore_root = tmp_path / "restore"
    restore_root.mkdir(mode=0o700)
    restored = restore_root / "restored.bin"
    subprocess.run(
        [
            OBJECT_LOCK_CLIENT,
            "get-verify",
            key,
            "version-1",
            expected_sha,
            restored,
        ],
        check=True,
        env=environment,
    )
    assert restored.read_bytes() == source.read_bytes()
    calls = (state / "calls.log").read_text(encoding="utf-8")
    assert "head:pinned:version-1" in calls
    assert calls.count("get:pinned:version-1") == 2
    assert encoded_key not in calls
    failed = subprocess.run(
        [OBJECT_LOCK_CLIENT, "put-verify", source, key, tmp_path / "failed.json"],
        check=False,
        env={**environment, "FAKE_FAIL_GET": "1"},
        timeout=5,
    )
    assert failed.returncode != 0


def test_reverse_restore_heredoc_executes_cleanup_and_passes_password_by_env(
    tmp_path: Path,
) -> None:
    text = RECOVERY_WORKFLOW.read_text(encoding="utf-8")
    start = text.index(
        "- name: Perform an isolated filesystem and PostgreSQL reverse-restore proof"
    )
    end = text.index("- name: Seal the portable export", start)
    step = text[start:end]
    marker = "PYTHONPATH=\"$GITHUB_WORKSPACE/backend\" python -B - <<'PY'\n"
    code_start = step.index(marker) + len(marker)
    code_end = step.index("\n          PY", code_start)
    program = textwrap.dedent(step[code_start:code_end])

    package = tmp_path / "stub" / "seiche"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "stateful_recovery.py").write_text(
        "class Bundle:\n"
        "    palimpsest_china_state_audit = object()\n"
        "\n"
        "def _bundle_identity(*args, **kwargs):\n"
        "    return Bundle()\n",
        encoding="utf-8",
    )
    (package / "stateful_migration.py").write_text(
        "def restore_filesystem_generation(*args, **kwargs):\n"
        "    return 'verified_head', {'state': 'd' * 64}\n"
        "\n"
        "def palimpsest_china_state_from_audit(*args, **kwargs):\n"
        "    return {\n"
        "        'audit_schema': 'seiche.palimpsest-china-activation-state.v1',\n"
        "        'tree_sha256': 'e' * 64,\n"
        "        'active_activation_id': 'f' * 64,\n"
        "        'pending_candidate_activation_id': None,\n"
        "    }\n",
        encoding="utf-8",
    )
    (tmp_path / "bundle").mkdir()
    (tmp_path / "proof").mkdir()
    (tmp_path / "recovery-receipt.json").write_text(
        json.dumps(
            {
                "filesystem": {
                    "nbs_full_store_audit_result": "verified_head",
                    "tree_sha256": {"state": "d" * 64},
                },
                "palimpsest_china_state": {
                    "audit_schema": "seiche.palimpsest-china-activation-state.v1",
                    "tree_sha256": "e" * 64,
                    "active_activation_id": "f" * 64,
                    "pending_candidate_activation_id": None,
                },
            }
        ),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(tmp_path / "stub"),
        "SNAPSHOT_ID": "20260824T000000Z",
        "RELEASE_SHA": "a" * 40,
    }

    completed = subprocess.run(
        [sys.executable, "-B", "-"],
        cwd=tmp_path,
        env=environment,
        input=program,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not (tmp_path / "proof" / "filesystem-restore").exists()
    assert "PGPASSWORD: phase6-restore-only" in step
    assert step.count("--env PGPASSWORD") == 2
    assert "PGPASSWORD=phase6-restore-only docker" not in step
    assert "postgresql://postgres:phase6-restore-only" not in step


def test_scheduled_recovery_environments_do_not_require_per_run_reviewers() -> None:
    workflow = RECOVERY_WORKFLOW.read_text(encoding="utf-8")
    runbook = RECOVERY_RUNBOOK.read_text(encoding="utf-8")

    assert "Require human reviewers on all three environments" not in runbook
    assert "Require human reviewers on `railway-stateful-recovery-admin`" in runbook
    assert "do **not** configure per-run required\nreviewers on either one" in runbook
    assert "26-hour freshness bound" in runbook
    assert (
        "cutover, activation,\nwriter-grant, reverse-transfer, or production-recovery"
        in runbook
    )
    assert workflow.count("environment: railway-stateful-recovery-admin") == 1
    assert workflow.count("environment: railway-stateful-recovery-monitor") == 1
    assert workflow.count("environment: railway-stateful-recovery-export") == 2
