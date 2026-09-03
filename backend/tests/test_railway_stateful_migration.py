"""Closed contracts for the Phase-4 Railway stateful shadow restore."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import tarfile
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Response

from seiche import agent_room
from seiche import api
from seiche import attest
from seiche import mcp_server
from seiche import palimpsest_china_activation as activation
from seiche import stateful_migration as migration

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "railway-stateful-shadow.yml"
DOCKERFILE = ROOT / "ops" / "railway" / "Dockerfile.stateful"
RAILWAY_CONFIG = ROOT / "ops" / "railway" / "railway.stateful.json"


def _tar_directory(
    path: Path,
    entries: list[tuple[str, bytes | None, int]],
) -> None:
    with tarfile.open(path, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        for name, body, mode in entries:
            member = tarfile.TarInfo(name)
            member.mode = mode
            member.mtime = 1_700_000_000
            if body is None:
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            else:
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))


def _source_files(tmp_path: Path) -> tuple[Path, Path]:
    archive = tmp_path / "source.tar"
    bundle = tmp_path / "source.bundle"
    archive.write_bytes(b"canonical source archive\n")
    bundle.write_bytes(b"canonical source bundle\n")
    return archive, bundle


def _base_request(source_archive: Path, source_bundle: Path) -> dict[str, object]:
    return {
        "schema": migration.REQUEST_SCHEMA,
        "repository": migration.REPOSITORY,
        "workflow": migration.WORKFLOW,
        "source_ref": migration.SOURCE_REF,
        "commit": "a" * 40,
        "tree": "b" * 40,
        "source_archive_sha256": migration.sha256_file(source_archive),
        "source_bundle_sha256": migration.sha256_file(source_bundle),
        "request_id": "c" * 64,
        "operation": "shadow",
        "snapshot_id": "20260823T010203Z",
        "source_revision": "a" * 40,
        "source_inventory_sha256": "d" * 64,
        "source_content_set_sha256": "e" * 64,
        "source_release_receipt_sha256": "f" * 64,
        "source_recovery_receipt_sha256": "1" * 64,
        "source_writers_frozen": False,
        "public_traffic_enabled": False,
        "requested_at": "2026-08-23T01:10:00Z",
    }


def _bundle_fixture(
    tmp_path: Path,
    *,
    schema: str = migration.BACKUP_SCHEMA,
    with_agent_room: bool = False,
) -> tuple[Path, Path, Path, dict[str, object]]:
    source_archive, source_bundle = _source_files(tmp_path)
    request = _base_request(source_archive, source_bundle)
    root = tmp_path / str(request["snapshot_id"])
    root.mkdir()

    sqlite_path = tmp_path / "seiche.sqlite"
    with sqlite3.connect(sqlite_path) as database:
        database.execute("CREATE TABLE fixture (id INTEGER PRIMARY KEY, value TEXT)")
        database.execute("INSERT INTO fixture(value) VALUES ('verified')")
    sqlite_body = sqlite_path.read_bytes()

    _tar_directory(
        root / "var-lib-seiche.tgz",
        [
            ("seiche", None, 0o750),
            ("seiche/raw", None, 0o750),
            ("seiche/raw/fixture.bin", b"market-state\n", 0o640),
            ("seiche-nbs", None, 0o750),
            ("seiche-nbs/restricted", None, 0o700),
            ("seiche-nbs/public", None, 0o750),
            ("seiche-nbs/public/revisions", None, 0o2750),
        ],
    )
    api_entries: list[tuple[str, bytes | None, int]] = [
        ("api-data", None, 0o750),
        ("api-data/seiche.sqlite", sqlite_body, 0o640),
        ("api-data/fixture.json", b"{}\n", 0o640),
    ]
    if with_agent_room:
        room_fixture = tmp_path / "agent-room-fixture"
        room_fixture.mkdir(mode=0o700)
        room_fixture.chmod(0o700)
        room_root = room_fixture / "_agent_room"
        room_root.mkdir(mode=0o700)
        room_root.chmod(0o700)
        attest_root = room_fixture / "_attest"
        attest_root.mkdir(mode=0o700)
        attest_root.chmod(0o700)
        server_key = Ed25519PrivateKey.from_private_bytes(bytes([41]) * 32)
        participant_key = Ed25519PrivateKey.from_private_bytes(bytes([42]) * 32)
        private_key_path = attest_root / "operator_key.pem"
        private_key_path.write_bytes(
            server_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        private_key_path.chmod(0o600)
        store = agent_room.AgentRoomStore(
            room_root / "agent-room.sqlite",
            server_private_key=server_key,
        )
        participant_public = participant_key.public_key().public_bytes_raw().hex()
        store.provision_participant("fixture-agent", participant_public)
        created = store.create_room("fixture-room", owner_id="fixture-agent")
        client_event = agent_room.build_client_event(
            room_id="fixture-room",
            actor_id="fixture-agent",
            client_key_id=agent_room.ed25519_key_id(participant_public),
            kind="proposal",
            expected_sequence=0,
            expected_head_hash=str(created["genesis_hash"]),
            nonce="migration-fixture-000001",
            client_created_at=datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            payload={"purpose": "restore-proof"},
        )
        store.append_event(
            client_event,
            client_signature_hex=participant_key.sign(
                agent_room.client_signing_bytes(client_event)
            ).hex(),
        )
        seal_path = attest_root / agent_room.AGENT_ROOM_INITIALIZATION_SEAL_FILENAME
        agent_room.create_initialization_seal(
            seal_path,
            server_private_key=server_key,
        )
        api_entries.extend(
            (
                ("api-data/_attest", None, 0o700),
                (
                    "api-data/_attest/operator_key.pem",
                    private_key_path.read_bytes(),
                    0o600,
                ),
                (
                    "api-data/_attest/agent-room-initialized.json",
                    seal_path.read_bytes(),
                    0o600,
                ),
                ("api-data/_agent_room", None, 0o700),
                (
                    "api-data/_agent_room/agent-room.sqlite",
                    (room_root / "agent-room.sqlite").read_bytes(),
                    0o600,
                ),
            )
        )
    _tar_directory(root / "api-data.tgz", api_entries)
    members = migration._BACKUP_MEMBERS
    manifest_fields = [
        f"schema={schema}",
        "created_at=20260823T010203Z",
        "database=seiche",
        "postgres_port=5432",
        "state_root=/var/lib/seiche",
        "nbs_state_root=/var/lib/seiche-nbs",
        "nbs_full_store_audit_contract=seiche.nbs-full-store-audit.v1",
        "nbs_full_store_audit_result=required_at_restore",
        "api_data_root=/home/seiche/app/backend/data",
        "critical_table_count_semantics=pre_dump_lower_bound",
    ]
    if schema == migration.BACKUP_SCHEMA:
        palimpsest = tmp_path / "palimpsest-state"
        palimpsest.mkdir(mode=0o750)
        # mkdir modes are filtered by the process umask.  Set the audited
        # production modes explicitly so this fixture remains exact under the
        # publish service's hardened 0077 umask.
        palimpsest.chmod(0o750)
        receipts = palimpsest / "receipts"
        receipts.mkdir(mode=0o700)
        receipts.chmod(0o700)
        audit = activation.audit_activation_state(
            palimpsest,
            root_uid=os.geteuid(),
            root_gid=os.getegid(),
            api_uid=os.geteuid(),
            api_gid=os.getegid(),
            declared_state_root=Path("/var/lib/seiche-palimpsest-china"),
        )
        _tar_directory(
            root / "palimpsest-china.tgz",
            [
                ("seiche-palimpsest-china", None, 0o750),
                ("seiche-palimpsest-china/receipts", None, 0o700),
            ],
        )
        (root / "palimpsest-china-state.json").write_bytes(
            migration.canonical_document(audit)
        )
        manifest_fields.extend(
            (
                "palimpsest_china_state_root=/var/lib/seiche-palimpsest-china",
                "palimpsest_china_state_audit_contract=seiche.palimpsest-china-activation-state.v1",
                "palimpsest_china_state_audit_result=required_at_restore",
            )
        )
    elif schema == migration.LEGACY_BACKUP_SCHEMA:
        members = migration._LEGACY_BACKUP_MEMBERS
    else:
        raise AssertionError("unsupported fixture schema")
    manifest_fields.extend(
        ("research_only=true", "can_publish=false", "can_execute=false")
    )
    (root / "seiche.dump").write_bytes(b"PGDMP fixture bytes\n")
    (root / "table-counts.txt").write_text("10|20|30|40\n", encoding="ascii")
    (root / "deployed-sha.txt").write_text("a" * 40 + "\n", encoding="ascii")
    (root / "manifest.env").write_text(
        "\n".join(manifest_fields) + "\n",
        encoding="utf-8",
    )
    digests = {name: migration.sha256_file(root / name) for name in members}
    inventory = "".join(f"{digests[name]}  {name}\n" for name in members).encode(
        "ascii"
    )
    (root / "SHA256SUMS").write_bytes(inventory)
    content = hashlib.sha256()
    for name in members:
        size = (root / name).stat().st_size
        content.update(name.encode("ascii") + b"\0")
        content.update(digests[name].encode("ascii") + b"\0")
        content.update(str(size).encode("ascii") + b"\n")
    request["source_inventory_sha256"] = hashlib.sha256(inventory).hexdigest()
    request["source_content_set_sha256"] = content.hexdigest()
    return root, source_archive, source_bundle, request


def _active_palimpsest_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, object]]:
    state = tmp_path / "active-palimpsest-state"
    state.mkdir(mode=0o750)
    state.chmod(0o750)
    receipts = state / "receipts"
    receipts.mkdir(mode=0o700)
    receipts.chmod(0o700)
    config = tmp_path / "active-palimpsest-config"
    config.mkdir(mode=0o750)
    config.chmod(0o750)
    dropin = tmp_path / "active-palimpsest-systemd" / "seiche-api.service.d"
    dropin.mkdir(parents=True)
    dropin.chmod(0o755)
    locks = tmp_path / "active-palimpsest-locks"
    locks.mkdir(mode=0o700)
    locks.chmod(0o700)
    deploy_lock = locks / "deploy.lock"
    deploy_lock.write_bytes(b"lock\n")
    deploy_lock.chmod(0o600)
    runtime = tmp_path / "active-palimpsest-runtime"
    runtime.mkdir()
    sources_root = tmp_path / "active-palimpsest-sources"
    sources_root.mkdir(mode=0o700)
    sources = activation.BundleSources(
        *(sources_root / spec.filename for spec in activation._BUNDLE_FILE_SPECS)
    )
    for name, path in sources.files().items():
        path.write_bytes(f"stateful:{name}\n".encode())
        path.chmod(0o600)
    hashes = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in sources.files().items()
    }
    accepted_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    candidate = {
        "schema": activation.CANDIDATE_SCHEMA,
        "files": hashes,
        "signer_key_id": "c" * 64,
        "accepted_at": accepted_at.isoformat().replace("+00:00", "Z"),
        "rights_expires_at": (accepted_at + timedelta(days=30))
        .isoformat()
        .replace("+00:00", "Z"),
        "producer_repository": "beepboop2025/palimpsest",
        "producer_sha": "b" * 40,
        "producer_workflow_run_id": 100,
    }
    monkeypatch.setattr(
        activation,
        "_candidate_from_context",
        lambda _sources, *, attest_dir=None: candidate,
    )
    result = activation.activate_bundle(
        sources,
        paths=activation.ActivationPaths(
            state_root=state,
            env_file=config / "palimpsest-china.env",
            dropin_file=dropin / "palimpsest-china.conf",
            deploy_lock=deploy_lock,
            activation_lock=locks / "palimpsest-china.lock",
            runtime_release=runtime,
            release_sha="a" * 40,
            root_uid=os.geteuid(),
            root_gid=os.getegid(),
            api_uid=os.geteuid(),
            api_gid=os.getegid(),
            api_url="http://127.0.0.1:18787",
            python=Path(os.sys.executable),
            portable=True,
        ),
    )
    marker = dict(result["active"])
    marker["receipt_path"] = (
        f"/var/lib/seiche-palimpsest-china/receipts/{marker['activation_id']}.json"
    )
    (state / "active.json").chmod(0o600)
    (state / "active.json").write_bytes(activation._canonical(marker))
    (state / "active.json").chmod(0o400)
    result = {**result, "active": marker}
    return state, result


def _replace_bundle_palimpsest_state(
    root: Path,
    state: Path,
    request: dict[str, object],
) -> None:
    with tarfile.open(root / "palimpsest-china.tgz", mode="w:gz") as archive:
        archive.add(state, arcname="seiche-palimpsest-china", recursive=True)
    audit = activation.audit_activation_state(
        state,
        root_uid=os.geteuid(),
        root_gid=os.getegid(),
        api_uid=os.geteuid(),
        api_gid=os.getegid(),
        declared_state_root=Path("/var/lib/seiche-palimpsest-china"),
    )
    (root / "palimpsest-china-state.json").write_bytes(
        migration.canonical_document(audit)
    )
    digests = {
        name: migration.sha256_file(root / name) for name in migration._BACKUP_MEMBERS
    }
    inventory = "".join(
        f"{digests[name]}  {name}\n" for name in migration._BACKUP_MEMBERS
    ).encode("ascii")
    (root / "SHA256SUMS").write_bytes(inventory)
    content = hashlib.sha256()
    for name in migration._BACKUP_MEMBERS:
        size = (root / name).stat().st_size
        content.update(name.encode("ascii") + b"\0")
        content.update(digests[name].encode("ascii") + b"\0")
        content.update(str(size).encode("ascii") + b"\n")
    request["source_inventory_sha256"] = hashlib.sha256(inventory).hexdigest()
    request["source_content_set_sha256"] = content.hexdigest()


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


def _receipted_generation(
    tmp_path: Path,
    *,
    with_agent_room: bool = False,
    bind_absent_agent_room_key: bool = False,
) -> tuple[Path, dict[str, object]]:
    root, _source_archive, _source_bundle, request = _bundle_fixture(
        tmp_path,
        with_agent_room=with_agent_room,
    )
    bundle = migration.validate_bundle(root, request)
    platform = tmp_path / "platform"
    generations = platform / "generations"
    generations.mkdir(parents=True)
    staging = platform / "staging"
    staging.mkdir()
    agent_room_audit: dict[str, object] = {}
    nbs_result, digests = migration.restore_filesystem_generation(
        bundle,
        staging,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
        agent_room_audit_out=agent_room_audit,
    )
    if bind_absent_agent_room_key:
        assert not with_agent_room
        api_data = staging / "generation" / "api"
        attest_root = api_data / "_attest"
        attest_root.mkdir(mode=0o700)
        attest_root.chmod(0o700)
        server_key = Ed25519PrivateKey.from_private_bytes(bytes([61]) * 32)
        private_key_path = attest_root / "operator_key.pem"
        private_key_path.write_bytes(
            server_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        private_key_path.chmod(0o600)
        public_key = server_key.public_key().public_bytes_raw().hex()
        public_key_path = attest_root / "operator_key.pub"
        public_key_path.write_text(public_key + "\n")
        public_key_path.chmod(0o644)
        agent_room_audit.clear()
        agent_room_audit.update(migration.audit_agent_room_state(api_data))
        digests["api"] = migration.hash_tree(api_data)
    generation_name = f"{bundle.snapshot_id}-{bundle.content_set_sha256[:16]}"
    receipt = migration.render_receipt(
        request,
        bundle,
        migration.RestoredDatabase(
            migration.derive_database_name(
                bundle.snapshot_id,
                bundle.content_set_sha256,
            ),
            "postgresql://runtime-only",
            bundle.counts_floor,
        ),
        generation_name=generation_name,
        generation_digests=digests,
        nbs_audit_result=nbs_result,
        agent_room_audit=agent_room_audit,
        railway=_railway_identity(),
        started_at="2026-08-23T02:00:00Z",
        completed_at="2026-08-23T02:03:00Z",
    )
    generation = generations / generation_name
    (staging / "generation").rename(generation)
    return generation, receipt


def test_request_and_backup_v4_bytes_are_bound_exactly(tmp_path: Path) -> None:
    root, source_archive, source_bundle, request = _bundle_fixture(tmp_path)
    request_path = tmp_path / "request.json"
    request_path.write_bytes(migration.canonical_document(request))
    loaded = migration.load_request(
        request_path,
        source_archive,
        source_bundle,
        now=datetime(2026, 8, 23, 2, tzinfo=UTC),
    )
    bundle = migration.validate_bundle(root, loaded)

    assert bundle.source_revision == "a" * 40
    assert bundle.counts_floor == (10, 20, 30, 40)
    assert bundle.inventory_sha256 == request["source_inventory_sha256"]
    assert bundle.content_set_sha256 == request["source_content_set_sha256"]
    assert bundle.schema == migration.BACKUP_SCHEMA
    assert bundle.palimpsest_china_state_audit == {
        "schema": activation.BACKUP_STATE_SCHEMA,
        "state_root": "/var/lib/seiche-palimpsest-china",
        "tree_sha256": bundle.palimpsest_china_state_audit["tree_sha256"],
        "bundles": [],
        "receipts": [],
        "active_activation_id": None,
        "pending_candidate_activation_id": None,
    }


def test_legacy_backup_v3_restores_an_empty_inactive_activation_state(
    tmp_path: Path,
) -> None:
    root, _source_archive, _source_bundle, request = _bundle_fixture(
        tmp_path,
        schema=migration.LEGACY_BACKUP_SCHEMA,
    )
    bundle = migration.validate_bundle(root, request)
    staging = tmp_path / "legacy-staging"
    staging.mkdir()

    result, digests = migration.restore_filesystem_generation(
        bundle,
        staging,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )

    assert result == "not_onboarded"
    assert bundle.schema == migration.LEGACY_BACKUP_SCHEMA
    assert bundle.palimpsest_china_state_audit is None
    assert set(digests) == {"market", "nbs", "api", "palimpsest-china"}
    restored = staging / "generation" / "palimpsest-china"
    assert {entry.name for entry in restored.iterdir()} == {"receipts"}
    assert list((restored / "receipts").iterdir()) == []


def test_bundle_tampering_fails_before_restore(tmp_path: Path) -> None:
    root, _source_archive, _source_bundle, request = _bundle_fixture(tmp_path)
    (root / "api-data.tgz").write_bytes(b"different archive bytes")

    with pytest.raises(migration.MigrationContractError, match="digest mismatch"):
        migration.validate_bundle(root, request)


@pytest.mark.parametrize(
    "kind",
    ("traversal", "symlink", "duplicate", "canonical_duplicate"),
)
def test_tar_contract_rejects_unsafe_topology(tmp_path: Path, kind: str) -> None:
    archive_path = tmp_path / f"{kind}.tgz"
    with tarfile.open(archive_path, mode="w:gz") as archive:
        root = tarfile.TarInfo("seiche")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        if kind == "traversal":
            member = tarfile.TarInfo("seiche/../escaped")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
        elif kind == "symlink":
            member = tarfile.TarInfo("seiche/link")
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/passwd"
            archive.addfile(member)
        elif kind == "duplicate":
            for _ in range(2):
                member = tarfile.TarInfo("seiche/same")
                member.size = 1
                archive.addfile(member, io.BytesIO(b"x"))
        else:
            alias = tarfile.TarInfo("seiche/")
            alias.type = tarfile.DIRTYPE
            archive.addfile(alias)

    with pytest.raises(migration.MigrationContractError):
        migration.validate_tar_contract(
            archive_path,
            expected_roots=frozenset({"seiche"}),
        )


def test_filesystem_restore_runs_sqlite_and_full_nbs_audits(tmp_path: Path) -> None:
    root, _source_archive, _source_bundle, request = _bundle_fixture(tmp_path)
    bundle = migration.validate_bundle(root, request)
    staging = tmp_path / "staging"
    staging.mkdir()

    result, digests = migration.restore_filesystem_generation(
        bundle,
        staging,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )

    assert result == "not_onboarded"
    assert set(digests) == {"market", "nbs", "api", "palimpsest-china"}
    assert all(len(value) == 64 for value in digests.values())
    assert (staging / "generation" / "api" / "seiche.sqlite").is_file()
    assert (staging / "generation" / "market" / "raw" / "fixture.bin").is_file()


@pytest.mark.parametrize("mutation", ("client_signature", "operator_key"))
def test_receipted_generation_reaudits_agent_room_key_and_chain(
    tmp_path: Path,
    mutation: str,
) -> None:
    root, _source_archive, _source_bundle, request = _bundle_fixture(
        tmp_path,
        with_agent_room=True,
    )
    bundle = migration.validate_bundle(root, request)
    staging = tmp_path / "agent-room-staging"
    staging.mkdir()
    audit: dict[str, object] = {}
    nbs_result, digests = migration.restore_filesystem_generation(
        bundle,
        staging,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
        agent_room_audit_out=audit,
    )
    assert audit["result"] == "verified"
    assert audit["participant_count"] == 1
    assert audit["room_count"] == 1
    assert audit["event_count"] == 1
    receipt = migration.render_receipt(
        request,
        bundle,
        migration.RestoredDatabase(
            migration.derive_database_name(
                bundle.snapshot_id,
                bundle.content_set_sha256,
            ),
            "postgresql://runtime-only",
            bundle.counts_floor,
        ),
        generation_name=f"{bundle.snapshot_id}-{bundle.content_set_sha256[:16]}",
        generation_digests=digests,
        nbs_audit_result=nbs_result,
        agent_room_audit=audit,
        railway=_railway_identity(),
        started_at="2026-08-23T02:00:00Z",
        completed_at="2026-08-23T02:03:00Z",
    )
    generation = staging / "generation"
    migration.validate_receipted_generation(
        generation,
        receipt,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )

    if mutation == "client_signature":
        room_database = generation / "api" / "_agent_room" / "agent-room.sqlite"
        with sqlite3.connect(room_database) as database:
            database.execute(
                "UPDATE agent_room_events SET client_signature = ?",
                ("0" * 128,),
            )
            assert database.execute("PRAGMA quick_check").fetchone() == ("ok",)
    else:
        replacement = Ed25519PrivateKey.from_private_bytes(bytes([99]) * 32)
        key_path = generation / "api" / "_attest" / "operator_key.pem"
        key_path.write_bytes(
            replacement.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        key_path.chmod(0o600)
    receipt["filesystem"]["tree_sha256"]["api"] = migration.hash_tree(
        generation / "api"
    )

    with pytest.raises(
        migration.MigrationContractError,
        match="Agent Room cryptographic audit failed",
    ):
        migration.validate_receipted_generation(
            generation,
            receipt,
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )


@pytest.mark.parametrize(
    ("lost_member", "message"),
    (
        ("database", "initialized Agent Room database is unavailable"),
        ("seal", "initialized Agent Room seal is unavailable"),
    ),
)
def test_restore_audit_distinguishes_never_provisioned_from_initialized_loss(
    tmp_path: Path,
    lost_member: str,
    message: str,
) -> None:
    root, _source_archive, _source_bundle, request = _bundle_fixture(
        tmp_path,
        with_agent_room=True,
    )
    bundle = migration.validate_bundle(root, request)
    staging = tmp_path / "agent-room-loss-staging"
    staging.mkdir()
    migration.restore_filesystem_generation(
        bundle,
        staging,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )
    api_data = staging / "generation" / "api"
    member = (
        api_data / "_agent_room" / "agent-room.sqlite"
        if lost_member == "database"
        else api_data / "_attest" / agent_room.AGENT_ROOM_INITIALIZATION_SEAL_FILENAME
    )
    member.rename(tmp_path / f"displaced-{member.name}")

    with pytest.raises(migration.MigrationContractError, match=message):
        migration.audit_agent_room_state(
            api_data,
            expected_owner_uid=os.geteuid(),
        )


def test_active_palimpsest_state_restores_and_renders_exact_runtime_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _source_archive, _source_bundle, request = _bundle_fixture(tmp_path)
    state, activated = _active_palimpsest_state(tmp_path, monkeypatch)
    _replace_bundle_palimpsest_state(root, state, request)
    bundle = migration.validate_bundle(root, request)
    staging = tmp_path / "active-staging"
    staging.mkdir()
    _nbs_result, digests = migration.restore_filesystem_generation(
        bundle,
        staging,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )
    restored_state = staging / "generation" / "palimpsest-china"

    environment = migration.palimpsest_runtime_environment(
        restored_state,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )

    bundle_id = str(activated["active"]["bundle_id"])
    assert set(environment) == {
        spec.environment for spec in activation._BUNDLE_FILE_SPECS
    }
    assert environment == {
        spec.environment: str(restored_state / bundle_id / spec.filename)
        for spec in activation._BUNDLE_FILE_SPECS
    }
    assert len(environment) == 11
    generation = restored_state.parent
    installed_bundle = restored_state / bundle_id
    for directory in (generation, restored_state, installed_bundle):
        metadata = directory.stat()
        assert metadata.st_gid == os.getegid()
        assert stat.S_IMODE(metadata.st_mode) == 0o750
    for value in environment.values():
        metadata = Path(value).stat()
        assert metadata.st_gid == os.getegid()
        assert stat.S_IMODE(metadata.st_mode) == 0o440

    wrong_gid = os.getegid() + 1
    with pytest.raises(
        migration.MigrationContractError,
        match="runtime state is invalid",
    ):
        migration.palimpsest_runtime_environment(
            restored_state,
            runtime_uid=os.geteuid(),
            runtime_gid=wrong_gid,
        )
    assert len(digests) == 4


def test_restore_propagates_production_runtime_identity_to_palimpsest_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _source_archive, _source_bundle, request = _bundle_fixture(tmp_path)
    state, _activated = _active_palimpsest_state(tmp_path, monkeypatch)
    _replace_bundle_palimpsest_state(root, state, request)
    bundle = migration.validate_bundle(root, request)
    staging = tmp_path / "production-identity-staging"
    staging.mkdir()
    real_audit = activation.audit_activation_state
    real_chown = os.chown
    local_uid = os.geteuid()
    local_gid = os.getegid()
    observed: list[tuple[int, int, int, int, bool]] = []

    def bridge_audit(
        state_root: Path,
        *,
        root_uid: int,
        root_gid: int,
        api_uid: int,
        api_gid: int,
        normalize_restored: bool = False,
        declared_state_root: Path | None = None,
    ) -> dict[str, object]:
        observed.append((root_uid, root_gid, api_uid, api_gid, normalize_restored))
        # A non-root test runner cannot chown to Railway's production identity.
        # Preserve the real normalization and full audit under the local IDs
        # after recording the exact production boundary passed by migration.
        return real_audit(
            state_root,
            root_uid=local_uid,
            root_gid=local_gid,
            api_uid=local_uid,
            api_gid=local_gid,
            normalize_restored=normalize_restored,
            declared_state_root=declared_state_root,
        )

    monkeypatch.setattr(activation, "audit_activation_state", bridge_audit)

    def bridge_chown(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        _uid: int,
        _gid: int,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        real_chown(
            path,
            local_uid,
            local_gid,
            follow_symlinks=follow_symlinks,
        )

    # Exercise the production root-supervisor/10001-child call graph without
    # requiring the portable test runner to possess CAP_CHOWN.
    monkeypatch.setattr(migration.os, "geteuid", lambda: 0)
    monkeypatch.setattr(migration.os, "getegid", lambda: 0)
    monkeypatch.setattr(migration.os, "chown", bridge_chown)
    monkeypatch.setattr(migration, "_audit_nbs", lambda _root: "not_onboarded")
    migration.restore_filesystem_generation(
        bundle,
        staging,
        runtime_uid=10_001,
        runtime_gid=10_001,
    )

    assert observed == [(0, 0, 10_001, 10_001, True)]


def test_receipt_is_shadow_only_and_contains_no_database_secret(tmp_path: Path) -> None:
    root, _source_archive, _source_bundle, request = _bundle_fixture(tmp_path)
    bundle = migration.validate_bundle(root, request)
    database = migration.RestoredDatabase(
        name=migration.derive_database_name(
            bundle.snapshot_id,
            bundle.content_set_sha256,
        ),
        dsn="postgresql://user:secret@example.invalid/database",
        counts=(11, 21, 31, 41),
    )
    generation = f"{bundle.snapshot_id}-{bundle.content_set_sha256[:16]}"
    receipt = migration.render_receipt(
        request,
        bundle,
        database,
        generation_name=generation,
        generation_digests={
            "market": "2" * 64,
            "nbs": "3" * 64,
            "api": "4" * 64,
            "palimpsest-china": "5" * 64,
        },
        nbs_audit_result="not_onboarded",
        agent_room_audit=migration.absent_agent_room_audit(),
        railway=_railway_identity(),
        started_at="2026-08-23T02:00:00Z",
        completed_at="2026-08-23T02:03:00Z",
    )

    validated = migration.validate_receipt_document(
        receipt,
        request=request,
        railway=_railway_identity(),
    )
    assert validated["schema"] == "seiche.railway-stateful-shadow-receipt.v4"
    assert validated["filesystem"]["agent_room_audit"] == (
        migration.absent_agent_room_audit()
    )
    assert validated["palimpsest_china_state"] == {
        "audit_schema": migration.PALIMPSEST_CHINA_STATE_AUDIT_SCHEMA,
        "tree_sha256": bundle.palimpsest_china_state_audit["tree_sha256"],
        "active_activation_id": None,
        "pending_candidate_activation_id": None,
    }
    assert validated["authority"]["mode"] == "shadow"
    assert validated["authority"]["workers_started"] is False
    assert "secret" not in migration.canonical_document(validated).decode()

    tampered = json.loads(json.dumps(receipt))
    tampered["authority"]["public_traffic_enabled"] = True
    with pytest.raises(migration.MigrationContractError, match="authority"):
        migration.validate_receipt_document(tampered, request=request)


def test_shared_restore_directory_normalizes_exact_runtime_mode(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o700)
    shared.chmod(0o700)

    migration._prepare_shared_directory(shared, gid=os.getegid())

    metadata = shared.stat()
    assert metadata.st_uid == os.geteuid()
    assert metadata.st_gid == os.getegid()
    assert stat.S_IMODE(metadata.st_mode) == 0o750


def test_shared_restore_directory_translates_mutation_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = tmp_path / "shared"

    def fail_chmod(_descriptor: int, _mode: int) -> None:
        raise OSError("fixture mutation failure")

    monkeypatch.setattr(migration.os, "fchmod", fail_chmod)
    with pytest.raises(migration.MigrationContractError, match="mutation failed"):
        migration._prepare_shared_directory(shared, gid=os.getegid())


@pytest.mark.parametrize("name", ["generations", "receipts"])
def test_shadow_restore_rejects_shared_directory_symlinks_without_mutating_target(
    tmp_path: Path,
    name: str,
) -> None:
    root, _source_archive, _source_bundle, request = _bundle_fixture(tmp_path)
    bundle = migration.validate_bundle(root, request)
    platform = tmp_path / "platform"
    platform.mkdir()
    target = tmp_path / f"outside-{name}"
    target.mkdir(mode=0o700)
    target.chmod(0o700)
    sentinel = target / "sentinel"
    sentinel.write_bytes(b"unchanged\n")
    target_before = target.stat()
    link = platform / name
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(migration.MigrationContractError, match="directory is unsafe"):
        migration.restore_shadow(
            request,
            bundle,
            platform_root=platform,
            base_dsn="postgresql://unused",
            railway=_railway_identity(),
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )

    target_after = target.stat()
    assert (
        target_after.st_dev,
        target_after.st_ino,
        target_after.st_uid,
        target_after.st_gid,
        stat.S_IMODE(target_after.st_mode),
    ) == (
        target_before.st_dev,
        target_before.st_ino,
        target_before.st_uid,
        target_before.st_gid,
        0o700,
    )
    assert link.is_symlink()
    assert link.readlink() == target
    assert sentinel.read_bytes() == b"unchanged\n"


def test_child_environment_uses_one_generation_and_drops_control_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _source_archive, _source_bundle, request = _bundle_fixture(tmp_path)
    bundle = migration.validate_bundle(root, request)
    generation = f"{bundle.snapshot_id}-{bundle.content_set_sha256[:16]}"
    database = migration.RestoredDatabase(
        migration.derive_database_name(bundle.snapshot_id, bundle.content_set_sha256),
        "postgresql://runtime-only",
        (10, 20, 30, 40),
    )
    receipt = migration.render_receipt(
        request,
        bundle,
        database,
        generation_name=generation,
        generation_digests={
            "market": "2" * 64,
            "nbs": "3" * 64,
            "api": "4" * 64,
            "palimpsest-china": "5" * 64,
        },
        nbs_audit_result="not_onboarded",
        agent_room_audit=migration.absent_agent_room_audit(),
        railway=_railway_identity(),
        started_at="2026-08-23T02:00:00Z",
        completed_at="2026-08-23T02:03:00Z",
    )
    platform = tmp_path / "platform"
    monkeypatch.setattr(migration, "PLATFORM_ROOT", platform)
    palimpsest = platform / "generations" / generation / "palimpsest-china"
    palimpsest.mkdir(parents=True, mode=0o750)
    palimpsest.chmod(0o750)
    receipts = palimpsest / "receipts"
    receipts.mkdir(mode=0o700)
    receipts.chmod(0o700)
    receipt_path = platform / "receipts" / "c.json"
    receipt_path.parent.mkdir()
    receipt_path.write_bytes(migration.canonical_document(receipt))
    environment = migration.runtime_environment(
        {
            "PORT": "8080",
            "DATABASE_URL": "postgresql://control-database-secret",
            "RAILWAY_TOKEN": "drop-me",
            "RAILWAY_API_TOKEN": "drop-me-too",
            "SEICHE_RUNTIME_DATA_DIR": "/poison/runtime",
            "SEICHE_AGENT_ROOM_DB_PATH": "/poison/rooms.sqlite",
            "SEICHE_ATTEST_DIR": "/poison/attest",
            "SEICHE_AGENT_ROOM_EXPECTED_KEY_ID": "f" * 64,
        },
        receipt,
        database_dsn=database.dsn,
        receipt_path=receipt_path,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )

    assert "RAILWAY_TOKEN" not in environment
    assert "RAILWAY_API_TOKEN" not in environment
    assert "DATABASE_URL" not in environment
    assert environment["SEICHE_RAILWAY_STATEFUL_MODE"] == "shadow"
    assert environment["SEICHE_COLLECTOR_HEARTBEAT_REQUIRED"] == "0"
    assert environment["SEICHE_SOURCE_HEARTBEAT_REQUIRED"] == "0"
    assert environment["SEICHE_RUNTIME_DATA_DIR"].endswith(f"/{generation}/api")
    assert environment["SEICHE_AGENT_ROOM_DB_PATH"] == (
        environment["SEICHE_RUNTIME_DATA_DIR"] + "/_agent_room/agent-room.sqlite"
    )
    assert environment["SEICHE_ATTEST_DIR"] == (
        environment["SEICHE_RUNTIME_DATA_DIR"] + "/_attest"
    )
    assert environment["SEICHE_AGENT_ROOM_EXPECTED_KEY_ID"] == (
        migration.AGENT_ROOM_UNPROVISIONED_KEY
    )
    assert migration.validate_runtime_receipt(environment) == receipt
    replaced_key = dict(environment)
    replaced_key["SEICHE_AGENT_ROOM_EXPECTED_KEY_ID"] = "f" * 64
    with pytest.raises(migration.MigrationContractError, match="key binding"):
        migration.validate_runtime_receipt(replaced_key)


def test_healthz_requires_receipt_and_keeps_shadow_non_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEICHE_RAILWAY_STATEFUL_MODE", "shadow")
    monkeypatch.setattr(api.mcp_server, "agent_room_release_ready", lambda: True)
    monkeypatch.setattr(
        migration,
        "validate_runtime_receipt",
        lambda _environment: {
            "authority": {
                "source": "hetzner",
                "public_traffic_enabled": False,
                "workers_started": False,
            }
        },
    )
    monkeypatch.setattr(
        api,
        "_health_response",
        lambda *_args, **_kwargs: {
            "version": "0.11.0",
            "generated_at": "2026-08-23T02:00:00Z",
        },
    )

    result = asyncio.run(api.railway_stateful_health(Response()))

    assert result == {
        "status": "ready",
        "mode": "shadow",
        "version": "0.11.0",
        "generated_at": "2026-08-23T02:00:00Z",
    }


def test_healthz_fails_closed_before_receipt_when_agent_room_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEICHE_RAILWAY_STATEFUL_MODE", "shadow")
    monkeypatch.setattr(api.mcp_server, "agent_room_release_ready", lambda: False)
    monkeypatch.setattr(
        migration,
        "validate_runtime_receipt",
        lambda _environment: pytest.fail("receipt must not bypass room readiness"),
    )

    result = asyncio.run(api.railway_stateful_health(Response()))

    assert result.status_code == 503
    assert json.loads(result.body) == {"status": "agent_room_not_ready"}
    assert result.headers["cache-control"] == "no-store"


def test_receipted_generation_rejects_changed_bytes(tmp_path: Path) -> None:
    root, _source_archive, _source_bundle, request = _bundle_fixture(tmp_path)
    bundle = migration.validate_bundle(root, request)
    staging = tmp_path / "staging"
    staging.mkdir()
    agent_room_audit: dict[str, object] = {}
    nbs_result, digests = migration.restore_filesystem_generation(
        bundle,
        staging,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
        agent_room_audit_out=agent_room_audit,
    )
    database = migration.RestoredDatabase(
        migration.derive_database_name(bundle.snapshot_id, bundle.content_set_sha256),
        "postgresql://runtime-only",
        bundle.counts_floor,
    )
    receipt = migration.render_receipt(
        request,
        bundle,
        database,
        generation_name=f"{bundle.snapshot_id}-{bundle.content_set_sha256[:16]}",
        generation_digests=digests,
        nbs_audit_result=nbs_result,
        agent_room_audit=agent_room_audit,
        railway=_railway_identity(),
        started_at="2026-08-23T02:00:00Z",
        completed_at="2026-08-23T02:03:00Z",
    )
    generation = staging / "generation"
    migration.validate_receipted_generation(
        generation,
        receipt,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )
    (generation / "market" / "raw" / "fixture.bin").write_bytes(b"changed\n")

    with pytest.raises(migration.MigrationContractError, match="digest changed"):
        migration.validate_receipted_generation(
            generation,
            receipt,
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )


def _configure_agent_room_runtime(
    monkeypatch: pytest.MonkeyPatch,
    api_data: Path,
    *,
    mode: str,
    expected_key_id: str,
) -> None:
    monkeypatch.setattr(mcp_server, "DATA_DIR", api_data)
    monkeypatch.setattr(attest, "DATA_DIR", api_data)
    monkeypatch.setattr(mcp_server, "_agent_room_store_instance", None)
    monkeypatch.setattr(mcp_server, "_agent_room_readiness_passed", False)
    monkeypatch.setenv("SEICHE_ENV", "production")
    monkeypatch.setenv("SEICHE_RAILWAY_STATEFUL_MODE", mode)
    monkeypatch.setenv("SEICHE_AGENT_ROOM_EXPECTED_KEY_ID", expected_key_id)
    monkeypatch.setenv(
        "SEICHE_AGENT_ROOM_DB_PATH",
        str(api_data / "_agent_room" / "agent-room.sqlite"),
    )
    monkeypatch.setenv("SEICHE_ATTEST_DIR", str(api_data / "_attest"))


def test_active_generation_accepts_absent_room_bootstrap_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation, receipt = _receipted_generation(
        tmp_path,
        bind_absent_agent_room_key=True,
    )
    api_data = generation / "api"
    initial_audit = receipt["filesystem"]["agent_room_audit"]
    assert initial_audit["result"] == "absent_uninitialized"
    assert isinstance(initial_audit["server_key_id"], str)
    migration.validate_receipted_generation(
        generation,
        receipt,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )
    expected_key_id = receipt["filesystem"]["agent_room_audit"]["server_key_id"]
    assert isinstance(expected_key_id, str)
    _configure_agent_room_runtime(
        monkeypatch,
        api_data,
        mode="production",
        expected_key_id=expected_key_id,
    )

    mcp_server.initialize_agent_room_readiness()
    first = migration.validate_active_generation(
        generation,
        receipt,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )
    assert first["result"] == "verified"
    assert first["participant_count"] == 0
    assert first["room_count"] == 0
    assert first["event_count"] == 0

    monkeypatch.setattr(mcp_server, "_agent_room_store_instance", None)
    monkeypatch.setattr(mcp_server, "_agent_room_readiness_passed", False)
    mcp_server.initialize_agent_room_readiness()
    restarted = migration.validate_active_generation(
        generation,
        receipt,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )
    assert restarted == first
    with pytest.raises(migration.MigrationContractError, match="digest changed"):
        migration.validate_receipted_generation(
            generation,
            receipt,
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )


def test_active_generation_accepts_legitimate_agent_room_progress(
    tmp_path: Path,
) -> None:
    generation, receipt = _receipted_generation(
        tmp_path,
        with_agent_room=True,
    )
    api_data = generation / "api"
    private_key, _public_key = attest.load_existing_keypair(
        str(api_data / "_attest"),
        expected_owner_uid=os.geteuid(),
    )
    store = agent_room.AgentRoomStore(
        api_data / "_agent_room" / "agent-room.sqlite",
        server_private_key=private_key,
        require_existing=True,
    )
    participant_key = Ed25519PrivateKey.from_private_bytes(bytes([73]) * 32)
    store.provision_participant(
        "post-activation-agent",
        participant_key.public_key().public_bytes_raw().hex(),
    )

    observed = migration.validate_active_generation(
        generation,
        receipt,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )

    expected = receipt["filesystem"]["agent_room_audit"]
    assert observed["server_key_id"] == expected["server_key_id"]
    assert observed["participant_count"] == expected["participant_count"] + 1
    assert observed["state_sha256"] != expected["state_sha256"]


def test_active_generation_rejects_replacement_first_boot_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation, receipt = _receipted_generation(
        tmp_path,
        bind_absent_agent_room_key=True,
    )
    api_data = generation / "api"
    expected_key_id = receipt["filesystem"]["agent_room_audit"]["server_key_id"]
    assert isinstance(expected_key_id, str)
    _configure_agent_room_runtime(
        monkeypatch,
        api_data,
        mode="production",
        expected_key_id=expected_key_id,
    )
    mcp_server.initialize_agent_room_readiness()

    replacement_key = Ed25519PrivateKey.from_private_bytes(bytes([91]) * 32)
    replacement_private = api_data / "_attest" / "operator_key.pem"
    replacement_private.write_bytes(
        replacement_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    replacement_private.chmod(0o600)
    replacement_public = api_data / "_attest" / "operator_key.pub"
    replacement_public.write_text(
        replacement_key.public_key().public_bytes_raw().hex() + "\n"
    )
    replacement_public.chmod(0o644)
    room_database = api_data / "_agent_room" / "agent-room.sqlite"
    room_database.unlink()
    replacement_store = agent_room.AgentRoomStore(
        room_database,
        server_private_key=replacement_key,
    )
    replacement_store.audit_all_rooms()
    replacement_seal = (
        api_data / "_attest" / agent_room.AGENT_ROOM_INITIALIZATION_SEAL_FILENAME
    )
    replacement_seal.unlink()
    agent_room.create_initialization_seal(
        replacement_seal,
        server_private_key=replacement_key,
    )

    with pytest.raises(
        migration.MigrationContractError,
        match="bootstrap identity is not receipt-bound",
    ):
        migration.validate_active_generation(
            generation,
            receipt,
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )


def test_active_generation_rejects_store_created_for_unprovisioned_candidate(
    tmp_path: Path,
) -> None:
    generation, receipt = _receipted_generation(tmp_path)
    api_data = generation / "api"
    assert receipt["filesystem"]["agent_room_audit"] == (
        migration.absent_agent_room_audit()
    )
    replacement_key = Ed25519PrivateKey.from_private_bytes(bytes([92]) * 32)
    attest_root = api_data / "_attest"
    attest_root.mkdir(mode=0o700)
    private_key_path = attest_root / "operator_key.pem"
    private_key_path.write_bytes(
        replacement_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private_key_path.chmod(0o600)
    public_key_path = attest_root / "operator_key.pub"
    public_key_path.write_text(
        replacement_key.public_key().public_bytes_raw().hex() + "\n"
    )
    public_key_path.chmod(0o644)
    room_root = api_data / "_agent_room"
    room_root.mkdir(mode=0o700)
    store = agent_room.AgentRoomStore(
        room_root / "agent-room.sqlite",
        server_private_key=replacement_key,
    )
    store.audit_all_rooms()
    agent_room.create_initialization_seal(
        attest_root / agent_room.AGENT_ROOM_INITIALIZATION_SEAL_FILENAME,
        server_private_key=replacement_key,
    )

    with pytest.raises(
        migration.MigrationContractError,
        match="bootstrap identity is not receipt-bound",
    ):
        migration.validate_active_generation(
            generation,
            receipt,
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )


def test_active_generation_rejects_valid_truncation_below_candidate_baseline(
    tmp_path: Path,
) -> None:
    generation, receipt = _receipted_generation(tmp_path, with_agent_room=True)
    api_data = generation / "api"
    database_path = api_data / "_agent_room" / "agent-room.sqlite"
    with sqlite3.connect(database_path) as connection:
        genesis_hash = connection.execute(
            "SELECT genesis_hash FROM agent_rooms WHERE room_id='fixture-room'"
        ).fetchone()[0]
        connection.execute("DELETE FROM agent_room_events WHERE room_id='fixture-room'")
        connection.execute(
            "UPDATE agent_rooms SET next_sequence=0, head_hash=?, status='open' "
            "WHERE room_id='fixture-room'",
            (genesis_hash,),
        )
    rolled_back = migration.audit_agent_room_state(
        api_data,
        expected_owner_uid=os.geteuid(),
    )
    assert rolled_back["result"] == "verified"
    assert rolled_back["event_count"] == 0

    with pytest.raises(
        migration.MigrationContractError,
        match="does not extend its candidate state",
    ):
        migration.validate_active_generation(
            generation,
            receipt,
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )


@pytest.mark.parametrize("corruption", ["seal", "event_chain"])
def test_active_generation_rejects_corrupt_agent_room_state(
    tmp_path: Path,
    corruption: str,
) -> None:
    generation, receipt = _receipted_generation(
        tmp_path,
        with_agent_room=True,
    )
    api_data = generation / "api"
    if corruption == "seal":
        seal = api_data / "_attest" / agent_room.AGENT_ROOM_INITIALIZATION_SEAL_FILENAME
        body = bytearray(seal.read_bytes())
        body[-2] = ord("0") if body[-2] != ord("0") else ord("1")
        seal.write_bytes(bytes(body))
        seal.chmod(0o600)
    else:
        database = api_data / "_agent_room" / "agent-room.sqlite"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE agent_room_events SET client_signature=?",
                ("0" * 128,),
            )

    with pytest.raises(
        migration.MigrationContractError,
        match="Agent Room .*failed",
    ):
        migration.validate_active_generation(
            generation,
            receipt,
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )


@pytest.mark.parametrize(
    "tamper",
    ["immutable_bytes", "generation_member", "writable_metadata"],
)
def test_active_generation_rejects_immutable_or_path_tamper(
    tmp_path: Path,
    tamper: str,
) -> None:
    generation, receipt = _receipted_generation(tmp_path)
    if tamper == "immutable_bytes":
        (generation / "nbs" / "unexpected.bin").write_bytes(b"tampered\n")
        message = "immutable generation digest changed"
    else:
        if tamper == "generation_member":
            (generation / "unexpected-component").mkdir()
            message = "members are not closed"
        else:
            (generation / "market" / "raw").chmod(0o777)
            message = "writable generation metadata is unsafe"

    with pytest.raises(migration.MigrationContractError, match=message):
        migration.validate_active_generation(
            generation,
            receipt,
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )


def test_preactivation_exact_receipt_still_rejects_agent_room_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation, receipt = _receipted_generation(
        tmp_path,
        bind_absent_agent_room_key=True,
    )
    api_data = generation / "api"
    expected_key_id = receipt["filesystem"]["agent_room_audit"]["server_key_id"]
    assert isinstance(expected_key_id, str)
    _configure_agent_room_runtime(
        monkeypatch,
        api_data,
        mode="production",
        expected_key_id=expected_key_id,
    )
    mcp_server.initialize_agent_room_readiness()

    with pytest.raises(migration.MigrationContractError, match="digest changed"):
        migration.validate_receipted_generation(
            generation,
            receipt,
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("audit_schema", "seiche.palimpsest-china-activation-state.v0"),
        ("tree_sha256", "0" * 64),
        ("active_activation_id", "0" * 64),
        ("pending_candidate_activation_id", "0" * 64),
    ),
)
def test_shadow_receipt_v4_binds_exact_active_palimpsest_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: str,
) -> None:
    root, _source_archive, _source_bundle, request = _bundle_fixture(tmp_path)
    state, _activated = _active_palimpsest_state(tmp_path, monkeypatch)
    _replace_bundle_palimpsest_state(root, state, request)
    bundle = migration.validate_bundle(root, request)
    staging = tmp_path / "identity-staging"
    staging.mkdir()
    agent_room_audit: dict[str, object] = {}
    nbs_result, digests = migration.restore_filesystem_generation(
        bundle,
        staging,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
        agent_room_audit_out=agent_room_audit,
    )
    receipt = migration.render_receipt(
        request,
        bundle,
        migration.RestoredDatabase(
            migration.derive_database_name(
                bundle.snapshot_id,
                bundle.content_set_sha256,
            ),
            "postgresql://runtime-only",
            bundle.counts_floor,
        ),
        generation_name=f"{bundle.snapshot_id}-{bundle.content_set_sha256[:16]}",
        generation_digests=digests,
        nbs_audit_result=nbs_result,
        agent_room_audit=agent_room_audit,
        railway=_railway_identity(),
        started_at="2026-08-23T02:00:00Z",
        completed_at="2026-08-23T02:03:00Z",
    )
    assert receipt["schema"] == "seiche.railway-stateful-shadow-receipt.v4"
    assert receipt["palimpsest_china_state"]["active_activation_id"] is not None
    assert receipt["palimpsest_china_state"]["pending_candidate_activation_id"] is None

    tampered = json.loads(json.dumps(receipt))
    tampered["palimpsest_china_state"][field] = replacement
    with pytest.raises(migration.MigrationContractError, match="Palimpsest China"):
        migration.validate_receipted_generation(
            staging / "generation",
            tampered,
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )


def test_shadow_receipt_rejects_v3_downgrade(tmp_path: Path) -> None:
    root, _source_archive, _source_bundle, request = _bundle_fixture(tmp_path)
    bundle = migration.validate_bundle(root, request)
    receipt = migration.render_receipt(
        request,
        bundle,
        migration.RestoredDatabase(
            migration.derive_database_name(
                bundle.snapshot_id,
                bundle.content_set_sha256,
            ),
            "postgresql://runtime-only",
            bundle.counts_floor,
        ),
        generation_name=f"{bundle.snapshot_id}-{bundle.content_set_sha256[:16]}",
        generation_digests={
            name: "1" * 64 for name in ("market", "nbs", "api", "palimpsest-china")
        },
        nbs_audit_result="not_onboarded",
        agent_room_audit=migration.absent_agent_room_audit(),
        railway=_railway_identity(),
        started_at="2026-08-23T02:00:00Z",
        completed_at="2026-08-23T02:03:00Z",
    )
    receipt["schema"] = "seiche.railway-stateful-shadow-receipt.v3"

    with pytest.raises(migration.MigrationContractError, match="policy"):
        migration.validate_receipt_document(receipt, request=request)


def test_receipt_writer_handles_partial_os_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "receipt.json"
    real_write = os.write

    def partial_write(descriptor: int, body: bytes) -> int:
        return real_write(descriptor, body[: max(1, len(body) // 2)])

    monkeypatch.setattr(migration.os, "write", partial_write)
    document = {"schema": "fixture", "value": "x" * 4096}
    migration._write_receipt(target, document, gid=os.getegid())

    assert target.read_bytes() == migration.canonical_document(document)


def test_workflow_and_image_cannot_auto_cut_over() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    logical_workflow = " ".join(workflow.replace("\\\n", " ").split())
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    railway = json.loads(RAILWAY_CONFIG.read_text(encoding="utf-8"))

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "environment: railway-stateful-migration" in workflow
    assert "HETZNER_REMAINS_SOLE_WRITER" in workflow
    assert 'source_writers_frozen": False' in workflow
    assert 'public_traffic_enabled": False' in workflow
    assert "railway link" not in workflow
    assert (
        'railway volume --project "$RAILWAY_PROJECT_ID" '
        '--environment "$RAILWAY_ENVIRONMENT_ID" '
        '--service "$RAILWAY_SERVICE_ID" list --json'
    ) in logical_workflow
    assert (
        'railway variable list --project "$RAILWAY_PROJECT_ID" '
        '--service "$RAILWAY_SERVICE_ID" '
        '--environment "$RAILWAY_ENVIRONMENT_ID" --json'
    ) in logical_workflow
    assert "railway domain list" in workflow
    assert "DATABASE_URL" in workflow
    assert "actions/attest-build-provenance@" in workflow
    assert "source.bundle" in dockerfile
    assert '"archive"' in dockerfile
    assert "postgresql-client" in dockerfile
    assert "--uid 10001" in dockerfile
    assert "\nUSER " not in dockerfile
    assert railway["deploy"] == {
        "healthcheckPath": "/healthz",
        "healthcheckTimeout": 3600,
        "restartPolicyType": "NEVER",
    }
