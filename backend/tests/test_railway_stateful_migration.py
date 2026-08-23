"""Closed contracts for the Phase-4 Railway stateful shadow restore."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tarfile
from datetime import UTC, datetime

import pytest
from fastapi import Response

from seiche import api
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
    _tar_directory(
        root / "api-data.tgz",
        [
            ("api-data", None, 0o750),
            ("api-data/seiche.sqlite", sqlite_body, 0o640),
            ("api-data/fixture.json", b"{}\n", 0o640),
        ],
    )
    (root / "seiche.dump").write_bytes(b"PGDMP fixture bytes\n")
    (root / "table-counts.txt").write_text("10|20|30|40\n", encoding="ascii")
    (root / "deployed-sha.txt").write_text("a" * 40 + "\n", encoding="ascii")
    (root / "manifest.env").write_text(
        "\n".join(
            (
                "schema=seiche.market-backup.v3",
                "created_at=20260823T010203Z",
                "database=seiche",
                "postgres_port=5432",
                "state_root=/var/lib/seiche",
                "nbs_state_root=/var/lib/seiche-nbs",
                "nbs_full_store_audit_contract=seiche.nbs-full-store-audit.v1",
                "nbs_full_store_audit_result=required_at_restore",
                "api_data_root=/home/seiche/app/backend/data",
                "critical_table_count_semantics=pre_dump_lower_bound",
                "research_only=true",
                "can_publish=false",
                "can_execute=false",
            )
        )
        + "\n",
        encoding="utf-8",
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
    return root, source_archive, source_bundle, request


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


def test_request_and_backup_v3_bytes_are_bound_exactly(tmp_path: Path) -> None:
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
    assert set(digests) == {"market", "nbs", "api"}
    assert all(len(value) == 64 for value in digests.values())
    assert (staging / "generation" / "api" / "seiche.sqlite").is_file()
    assert (staging / "generation" / "market" / "raw" / "fixture.bin").is_file()


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
        generation_digests={"market": "2" * 64, "nbs": "3" * 64, "api": "4" * 64},
        nbs_audit_result="not_onboarded",
        railway=_railway_identity(),
        started_at="2026-08-23T02:00:00Z",
        completed_at="2026-08-23T02:03:00Z",
    )

    validated = migration.validate_receipt_document(
        receipt,
        request=request,
        railway=_railway_identity(),
    )
    assert validated["authority"]["mode"] == "shadow"
    assert validated["authority"]["workers_started"] is False
    assert "secret" not in migration.canonical_document(validated).decode()

    tampered = json.loads(json.dumps(receipt))
    tampered["authority"]["public_traffic_enabled"] = True
    with pytest.raises(migration.MigrationContractError, match="authority"):
        migration.validate_receipt_document(tampered, request=request)


def test_child_environment_uses_one_generation_and_drops_control_tokens(
    tmp_path: Path,
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
        generation_digests={"market": "2" * 64, "nbs": "3" * 64, "api": "4" * 64},
        nbs_audit_result="not_onboarded",
        railway=_railway_identity(),
        started_at="2026-08-23T02:00:00Z",
        completed_at="2026-08-23T02:03:00Z",
    )
    environment = migration.runtime_environment(
        {
            "PORT": "8080",
            "DATABASE_URL": "postgresql://control-database-secret",
            "RAILWAY_TOKEN": "drop-me",
            "RAILWAY_API_TOKEN": "drop-me-too",
        },
        receipt,
        database_dsn=database.dsn,
        receipt_path=migration.PLATFORM_ROOT / "receipts" / "c.json",
    )

    assert "RAILWAY_TOKEN" not in environment
    assert "RAILWAY_API_TOKEN" not in environment
    assert "DATABASE_URL" not in environment
    assert environment["SEICHE_RAILWAY_STATEFUL_MODE"] == "shadow"
    assert environment["SEICHE_COLLECTOR_HEARTBEAT_REQUIRED"] == "0"
    assert environment["SEICHE_SOURCE_HEARTBEAT_REQUIRED"] == "0"
    assert environment["SEICHE_RUNTIME_DATA_DIR"].endswith(f"/{generation}/api")


def test_healthz_requires_receipt_and_keeps_shadow_non_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEICHE_RAILWAY_STATEFUL_MODE", "shadow")
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


def test_receipted_generation_rejects_changed_bytes(tmp_path: Path) -> None:
    root, _source_archive, _source_bundle, request = _bundle_fixture(tmp_path)
    bundle = migration.validate_bundle(root, request)
    staging = tmp_path / "staging"
    staging.mkdir()
    nbs_result, digests = migration.restore_filesystem_generation(
        bundle,
        staging,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
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
        railway=_railway_identity(),
        started_at="2026-08-23T02:00:00Z",
        completed_at="2026-08-23T02:03:00Z",
    )
    generation = staging / "generation"
    migration.validate_receipted_generation(generation, receipt)
    (generation / "market" / "raw" / "fixture.bin").write_bytes(b"changed\n")

    with pytest.raises(migration.MigrationContractError, match="digest changed"):
        migration.validate_receipted_generation(generation, receipt)


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
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    railway = json.loads(RAILWAY_CONFIG.read_text(encoding="utf-8"))

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "environment: railway-stateful-migration" in workflow
    assert "HETZNER_REMAINS_SOLE_WRITER" in workflow
    assert 'source_writers_frozen": False' in workflow
    assert 'public_traffic_enabled": False' in workflow
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
