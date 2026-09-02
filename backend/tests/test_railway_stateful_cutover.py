"""Closed authority transitions for the Phase-5 Railway cutover."""

from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi import Response
from starlette.requests import Request

from seiche import api
from seiche import palimpsest_china_activation as palimpsest_activation
from seiche import stateful_cutover as cutover
from seiche import stateful_entrypoint
from seiche import stateful_migration as migration

ROOT = Path(__file__).resolve().parents[2]
CUTOVER_WORKFLOW = ROOT / ".github" / "workflows" / "railway-stateful-cutover.yml"
CUTOVER_RUNBOOK = ROOT / "ops" / "deploy" / "RAILWAY-STATEFUL-CUTOVER.md"
CUTOVER_FENCE = ROOT / "ops" / "deploy" / "seiche-railway-cutover-fence.sh"
CUTOVER_EDGE = ROOT / "ops" / "deploy" / "seiche-railway-edge-mode.sh"
STATEFUL_DOCKERFILE = ROOT / "ops" / "railway" / "Dockerfile.stateful"
CUTOVER_CONFIG = ROOT / "ops" / "railway" / "railway.cutover.json"


def _railway() -> dict[str, str]:
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


def _fence() -> dict[str, object]:
    return {
        "schema": cutover.FENCE_SCHEMA,
        "repository": migration.REPOSITORY,
        "commit": "a" * 40,
        "tree": "b" * 40,
        "authority": {
            "source": "hetzner",
            "state": "frozen",
            "writers_frozen": True,
            "api_stopped": True,
            "frozen_at": "2026-08-23T03:00:00Z",
            "expires_at": "2026-08-23T07:00:00Z",
        },
        "snapshot": {
            "id": "20260823T030100Z",
            "source_revision": "a" * 40,
            "inventory_sha256": "c" * 64,
            "content_set_sha256": "d" * 64,
            "restore_receipt_sha256": "e" * 64,
        },
        "receipts": {
            "release_sha256": "f" * 64,
            "recovery_sha256": "1" * 64,
            "latest_shadow_sha256": "2" * 64,
        },
        "units": {
            name: {"active": False, "enabled": False, "runtime_masked": True}
            for name in cutover.FENCED_UNITS
        },
        "can_activate_railway": True,
        "can_resume_hetzner_before_activation": True,
    }


def _request(fence: dict[str, object]) -> dict[str, object]:
    snapshot = fence["snapshot"]
    receipts = fence["receipts"]
    assert isinstance(snapshot, dict)
    assert isinstance(receipts, dict)
    return {
        "schema": cutover.REQUEST_SCHEMA,
        "repository": migration.REPOSITORY,
        "workflow": cutover.WORKFLOW,
        "source_ref": migration.SOURCE_REF,
        "commit": "a" * 40,
        "tree": "b" * 40,
        "source_archive_sha256": "3" * 64,
        "source_bundle_sha256": "4" * 64,
        "request_id": "5" * 64,
        "operation": "cutover_candidate",
        "snapshot_id": snapshot["id"],
        "source_revision": snapshot["source_revision"],
        "source_inventory_sha256": snapshot["inventory_sha256"],
        "source_content_set_sha256": snapshot["content_set_sha256"],
        "source_release_receipt_sha256": receipts["release_sha256"],
        "source_recovery_receipt_sha256": receipts["recovery_sha256"],
        "source_shadow_receipt_sha256": receipts["latest_shadow_sha256"],
        "source_fence_sha256": hashlib.sha256(
            migration.canonical_document(fence)
        ).hexdigest(),
        "source_writers_frozen": True,
        "public_traffic_enabled": False,
        "requested_at": "2026-08-23T03:05:00Z",
    }


def _bundle(tmp_path: Path, request: dict[str, object]) -> migration.BackupBundle:
    return migration.BackupBundle(
        root=tmp_path,
        snapshot_id=str(request["snapshot_id"]),
        source_revision=str(request["source_revision"]),
        inventory_sha256=str(request["source_inventory_sha256"]),
        content_set_sha256=str(request["source_content_set_sha256"]),
        member_sha256={name: "6" * 64 for name in migration._BACKUP_MEMBERS},
        counts_floor=(10, 20, 30, 40),
        total_bytes=12345,
        schema=migration.BACKUP_SCHEMA,
        palimpsest_china_state_audit={
            "schema": "seiche.palimpsest-china-activation-state.v1",
            "state_root": "/var/lib/seiche-palimpsest-china",
            "tree_sha256": "5" * 64,
            "bundles": [],
            "receipts": [],
            "active_activation_id": None,
            "pending_candidate_activation_id": None,
        },
    )


def _empty_palimpsest_state(generation: Path) -> None:
    state = generation / "palimpsest-china"
    state.mkdir(parents=True, mode=0o750)
    (state / "receipts").mkdir(mode=0o700)


def _verified_agent_room_audit() -> dict[str, object]:
    return {
        "schema": migration.AGENT_ROOM_RESTORE_AUDIT_SCHEMA,
        "result": "verified",
        "server_key_id": "7" * 64,
        "participant_count": 1,
        "room_count": 1,
        "event_count": 1,
        "state_sha256": "8" * 64,
        "non_executable": True,
        "execution_authority": "none",
    }


def _candidate(
    tmp_path: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    fence = _fence()
    request = _request(fence)
    receipt = cutover.render_candidate_receipt(
        request,
        fence,
        _bundle(tmp_path, request),
        migration.RestoredDatabase(
            migration.derive_database_name(
                str(request["snapshot_id"]),
                str(request["source_content_set_sha256"]),
            ),
            "postgresql://runtime-only",
            (11, 21, 31, 41),
        ),
        railway=_railway(),
        generation_digests={
            "market": "7" * 64,
            "nbs": "8" * 64,
            "api": "9" * 64,
            "palimpsest-china": "6" * 64,
        },
        nbs_audit_result="verified_head",
        agent_room_audit=migration.absent_agent_room_audit(),
        started_at="2026-08-23T03:06:00Z",
        completed_at="2026-08-23T03:09:00Z",
    )
    return fence, request, receipt


def test_cutover_rejects_legacy_backup_before_creating_restore_state(
    tmp_path: Path,
) -> None:
    fence = _fence()
    request = _request(fence)
    legacy = _bundle(tmp_path, request)._replace(
        schema=migration.LEGACY_BACKUP_SCHEMA,
        member_sha256={name: "6" * 64 for name in migration._LEGACY_BACKUP_MEMBERS},
        palimpsest_china_state_audit=None,
    )
    platform = tmp_path / "platform"

    with pytest.raises(
        cutover.CutoverContractError,
        match="current Palimpsest-state backup contract",
    ):
        cutover.restore_candidate(
            request,
            fence,
            legacy,
            platform_root=platform,
            base_dsn="postgresql://unused",
            railway=_railway(),
        )

    assert not platform.exists()


@pytest.mark.parametrize("name", ["generations", "cutover-receipts"])
def test_cutover_rejects_shared_directory_symlinks_without_mutating_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    fence = _fence()
    request = _request(fence)
    platform = tmp_path / "platform"
    platform.mkdir()
    target = tmp_path / f"outside-{name}"
    target.mkdir(mode=0o700)
    target.chmod(0o700)
    target_before = target.stat()
    link = platform / name
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(
        cutover,
        "load_source_shadow_receipt",
        lambda *_a, **_k: {
            "filesystem": {
                "agent_room_audit": migration.absent_agent_room_audit(),
            }
        },
    )

    with pytest.raises(cutover.CutoverContractError, match="directory is unsafe"):
        cutover.restore_candidate(
            request,
            fence,
            _bundle(tmp_path, request),
            platform_root=platform,
            base_dsn="postgresql://unused",
            railway=_railway(),
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )

    target_after = target.stat()
    assert (
        target_after.st_dev,
        target_after.st_ino,
        target_after.st_uid,
        target_after.st_gid,
        target_after.st_mode,
    ) == (
        target_before.st_dev,
        target_before.st_ino,
        target_before.st_uid,
        target_before.st_gid,
        target_before.st_mode,
    )
    assert link.is_symlink()
    assert link.readlink() == target


def test_cutover_rejects_agent_room_state_different_from_shadow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fence = _fence()
    request = _request(fence)
    platform = tmp_path / "platform"
    source_audit = migration.absent_agent_room_audit()
    restored_audit = _verified_agent_room_audit()
    monkeypatch.setattr(
        cutover,
        "load_source_shadow_receipt",
        lambda *_args, **_kwargs: {"filesystem": {"agent_room_audit": source_audit}},
    )

    def restore(
        _bundle_value: migration.BackupBundle,
        _staging: Path,
        *,
        runtime_uid: int,
        runtime_gid: int,
        agent_room_audit_out: dict[str, object] | None = None,
    ) -> tuple[str, dict[str, str]]:
        assert runtime_uid == os.geteuid()
        assert runtime_gid == os.getegid()
        assert agent_room_audit_out is not None
        agent_room_audit_out.update(restored_audit)
        return "verified_head", {
            name: "9" * 64 for name in ("market", "nbs", "api", "palimpsest-china")
        }

    monkeypatch.setattr(migration, "restore_filesystem_generation", restore)
    monkeypatch.setattr(
        migration,
        "restore_postgres",
        lambda *_args, **_kwargs: pytest.fail(
            "PostgreSQL restore must not run after Agent Room mismatch"
        ),
    )

    with pytest.raises(cutover.CutoverContractError, match="Agent Room state differs"):
        cutover.restore_candidate(
            request,
            fence,
            _bundle(tmp_path, request),
            platform_root=platform,
            base_dsn="postgresql://unused",
            railway=_railway(),
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )


@pytest.mark.parametrize(
    ("counts", "error"),
    (((12, 22, 32, 42), None), ((10, 22, 32, 42), "counts regressed")),
)
def test_activated_candidate_resume_uses_semantic_generation_and_count_floors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    counts: tuple[int, int, int, int],
    error: str | None,
) -> None:
    fence, request, receipt = _candidate(tmp_path)
    platform = tmp_path / "platform"
    candidate_receipts = platform / "cutover-receipts"
    candidate_receipts.mkdir(parents=True)
    receipt_path = candidate_receipts / f"{request['request_id']}.candidate.json"
    receipt_path.write_bytes(migration.canonical_document(receipt))
    generation = platform / "generations" / str(receipt["filesystem"]["generation"])
    generation.mkdir(parents=True)
    semantic_calls: list[Path] = []
    monkeypatch.setattr(
        cutover,
        "load_source_shadow_receipt",
        lambda *_args, **_kwargs: {
            "filesystem": {
                "agent_room_audit": migration.absent_agent_room_audit(),
            }
        },
    )
    monkeypatch.setattr(
        migration,
        "validate_active_generation",
        lambda path, *_args, **_kwargs: semantic_calls.append(path) or {},
    )
    monkeypatch.setattr(
        migration,
        "validate_receipted_generation",
        lambda *_args, **_kwargs: pytest.fail(
            "activated restart must not compare mutable bytes to candidate receipt"
        ),
    )
    monkeypatch.setattr(
        migration,
        "_target_dsn",
        lambda _base, _name: "postgresql://generation-only",
    )
    monkeypatch.setattr(migration, "inspect_postgres_counts", lambda _dsn: counts)

    def operation():
        return cutover.restore_candidate(
            request,
            fence,
            _bundle(tmp_path, request),
            platform_root=platform,
            base_dsn="postgresql://control/seiche",
            railway=_railway(),
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
            active_resume=True,
        )

    if error is not None:
        with pytest.raises(cutover.CutoverContractError, match=error):
            operation()
    else:
        restored = operation()
        assert restored.receipt == receipt
        assert restored.generation_path == generation
    assert semantic_calls == [generation]


def test_activated_candidate_resume_requires_immutable_candidate_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fence, request, _receipt = _candidate(tmp_path)
    platform = tmp_path / "platform"
    monkeypatch.setattr(
        cutover,
        "load_source_shadow_receipt",
        lambda *_args, **_kwargs: {
            "filesystem": {
                "agent_room_audit": migration.absent_agent_room_audit(),
            }
        },
    )

    with pytest.raises(
        cutover.CutoverContractError,
        match="activation exists without its immutable candidate receipt",
    ):
        cutover.restore_candidate(
            request,
            fence,
            _bundle(tmp_path, request),
            platform_root=platform,
            base_dsn="postgresql://control/seiche",
            railway=_railway(),
            runtime_uid=os.geteuid(),
            runtime_gid=os.getegid(),
            active_resume=True,
        )


def test_fence_requires_every_writer_inactive_disabled_and_masked() -> None:
    fence = _fence()
    validated = cutover.validate_fence(
        fence,
        now=datetime(2026, 8, 23, 3, 30, tzinfo=UTC),
    )
    assert validated["authority"]["state"] == "frozen"

    broken = copy.deepcopy(fence)
    broken["units"]["seiche-source-worker.service"]["runtime_masked"] = False
    with pytest.raises(cutover.CutoverContractError, match="not fenced"):
        cutover.validate_fence(
            broken,
            now=datetime(2026, 8, 23, 3, 30, tzinfo=UTC),
        )


def test_fence_expires_and_cannot_authorize_a_late_cutover() -> None:
    with pytest.raises(cutover.CutoverContractError, match="not currently valid"):
        cutover.validate_fence(
            _fence(),
            now=datetime(2026, 8, 23, 7, 0, 1, tzinfo=UTC),
        )


def test_request_is_bound_to_fence_snapshot_and_receipt_chain() -> None:
    fence = cutover.validate_fence(
        _fence(),
        now=datetime(2026, 8, 23, 3, 30, tzinfo=UTC),
    )
    request = _request(fence)
    validated = cutover.validate_request(
        request,
        fence=fence,
        now=datetime(2026, 8, 23, 3, 30, tzinfo=UTC),
    )
    assert validated["source_writers_frozen"] is True

    request["source_recovery_receipt_sha256"] = "0" * 64
    with pytest.raises(cutover.CutoverContractError, match="differs"):
        cutover.validate_request(
            request,
            fence=fence,
            now=datetime(2026, 8, 23, 3, 30, tzinfo=UTC),
        )

    request = _request(fence)
    request["source_fence_sha256"] = "0" * 64
    with pytest.raises(cutover.CutoverContractError, match="differs"):
        cutover.validate_request(
            request,
            fence=fence,
            now=datetime(2026, 8, 23, 3, 30, tzinfo=UTC),
        )


def test_candidate_receipt_has_no_writer_and_cannot_publish(tmp_path: Path) -> None:
    fence, request, receipt = _candidate(tmp_path)
    validated = cutover.validate_candidate_receipt(
        receipt,
        request=request,
        fence=fence,
        railway=_railway(),
    )
    assert validated["schema"] == "seiche.railway-cutover-candidate-receipt.v4"
    assert validated["palimpsest_china_state"] == {
        "audit_schema": migration.PALIMPSEST_CHINA_STATE_AUDIT_SCHEMA,
        "tree_sha256": "5" * 64,
        "active_activation_id": None,
        "pending_candidate_activation_id": None,
    }
    assert validated["authority"] == {
        "mode": "cutover_candidate",
        "source": "none",
        "hetzner_writers_frozen": True,
        "railway_writers_started": False,
        "public_traffic_enabled": False,
    }
    assert validated["can_publish"] is False


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("audit_schema", "seiche.palimpsest-china-activation-state.v0"),
        ("tree_sha256", "0" * 64),
        ("active_activation_id", "0" * 64),
        ("pending_candidate_activation_id", "0" * 64),
    ),
)
def test_candidate_receipt_v4_binds_exact_active_palimpsest_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: str,
) -> None:
    _fence_value, _request_value, receipt = _candidate(tmp_path)
    audit = {
        "schema": migration.PALIMPSEST_CHINA_STATE_AUDIT_SCHEMA,
        "state_root": "/var/lib/seiche-palimpsest-china",
        "tree_sha256": "5" * 64,
        "bundles": ["b" * 64],
        "receipts": ["a" * 64],
        "active_activation_id": "a" * 64,
        "pending_candidate_activation_id": None,
    }
    receipt["palimpsest_china_state"] = migration.palimpsest_china_state_from_audit(
        audit
    )
    generation = tmp_path / "candidate-generation"
    for name in ("market", "nbs", "api", "palimpsest-china"):
        (generation / name).mkdir(parents=True)
    monkeypatch.setattr(
        migration,
        "hash_tree",
        lambda path: receipt["filesystem"]["tree_sha256"][path.name],
    )
    monkeypatch.setattr(migration, "_validate_sqlite", lambda _path: None)
    monkeypatch.setattr(migration, "_audit_nbs", lambda _path: "verified_head")
    monkeypatch.setattr(
        palimpsest_activation,
        "audit_activation_state",
        lambda *_args, **_kwargs: audit,
    )
    migration.validate_receipted_generation(
        generation,
        receipt,
        runtime_uid=10_001,
        runtime_gid=10_001,
    )

    tampered = copy.deepcopy(receipt)
    tampered["palimpsest_china_state"][field] = replacement
    with pytest.raises(migration.MigrationContractError, match="Palimpsest China"):
        migration.validate_receipted_generation(
            generation,
            tampered,
            runtime_uid=10_001,
            runtime_gid=10_001,
        )


def test_candidate_receipt_rejects_v3_downgrade(tmp_path: Path) -> None:
    fence, request, receipt = _candidate(tmp_path)
    receipt["schema"] = "seiche.railway-cutover-candidate-receipt.v3"

    with pytest.raises(cutover.CutoverContractError, match="authority"):
        cutover.validate_candidate_receipt(
            receipt,
            request=request,
            fence=fence,
        )


def test_cutover_requires_exact_v4_shadow_state_before_restore(tmp_path: Path) -> None:
    _fence_value, request, candidate = _candidate(tmp_path)
    state = copy.deepcopy(candidate["palimpsest_china_state"])
    state["active_activation_id"] = "a" * 64
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
        "palimpsest_china_state": state,
        "railway": {},
        "timing": {},
        "research_only": True,
        "can_publish": False,
        "can_execute": False,
    }
    body = migration.canonical_document(shadow)
    request["source_shadow_receipt_sha256"] = hashlib.sha256(body).hexdigest()
    receipts = tmp_path / "platform" / "receipts"
    receipts.mkdir(parents=True)
    (receipts / ("f" * 64 + ".json")).write_bytes(body)

    validated = cutover.load_source_shadow_receipt(
        tmp_path / "platform",
        request=request,
        expected_palimpsest_china_state=state,
    )
    assert validated["palimpsest_china_state"] == state

    replacements = {
        "audit_schema": "seiche.palimpsest-china-activation-state.v0",
        "tree_sha256": "0" * 64,
        "active_activation_id": "0" * 64,
        "pending_candidate_activation_id": "0" * 64,
    }
    for field, replacement in replacements.items():
        tampered = copy.deepcopy(shadow)
        tampered["palimpsest_china_state"][field] = replacement
        rebound = dict(request)
        rebound["source_shadow_receipt_sha256"] = hashlib.sha256(
            migration.canonical_document(tampered)
        ).hexdigest()
        with pytest.raises(cutover.CutoverContractError, match="Palimpsest China"):
            cutover.validate_source_shadow_receipt(
                tampered,
                request=rebound,
                expected_palimpsest_china_state=state,
            )

    downgraded = copy.deepcopy(shadow)
    downgraded["schema"] = "seiche.railway-stateful-shadow-receipt.v3"
    rebound = dict(request)
    rebound["source_shadow_receipt_sha256"] = hashlib.sha256(
        migration.canonical_document(downgraded)
    ).hexdigest()
    with pytest.raises(cutover.CutoverContractError, match="binding"):
        cutover.validate_source_shadow_receipt(
            downgraded,
            request=rebound,
            expected_palimpsest_china_state=state,
        )


def test_edge_token_is_constant_time_bound_and_candidate_posts_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "edge-token-" + "x" * 32
    assert cutover.edge_request_allowed(token, token)
    assert not cutover.edge_request_allowed("wrong", token)
    monkeypatch.setenv("SEICHE_RAILWAY_STATEFUL_MODE", "cutover_candidate")
    monkeypatch.setenv("SEICHE_RAILWAY_EDGE_TOKEN", token)
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", _railway()["deployment_id"])
    monkeypatch.setenv("SEICHE_RELEASE_SHA", "a" * 40)

    async def call_next(_request: Request) -> Response:
        return Response(content=b"ok", status_code=200)

    unauthorized = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/health",
            "headers": [],
            "query_string": b"",
            "scheme": "https",
            "server": ("api.seiche.info", 443),
            "client": ("127.0.0.1", 1),
        }
    )
    denied = asyncio.run(api._railway_cutover_edge_guard(unauthorized, call_next))
    assert denied.status_code == 404

    post = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/subscribe",
            "headers": [(cutover.EDGE_HEADER.encode(), token.encode())],
            "query_string": b"",
            "scheme": "https",
            "server": ("api.seiche.info", 443),
            "client": ("127.0.0.1", 1),
        }
    )
    read_only = asyncio.run(api._railway_cutover_edge_guard(post, call_next))
    assert read_only.status_code == 503

    authorized = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/health",
            "headers": [(cutover.EDGE_HEADER.encode(), token.encode())],
            "query_string": b"",
            "scheme": "https",
            "server": ("api.seiche.info", 443),
            "client": ("127.0.0.1", 1),
        }
    )
    allowed = asyncio.run(api._railway_cutover_edge_guard(authorized, call_next))
    assert allowed.headers["X-Seiche-Railway-Authority"] == "candidate"
    assert allowed.headers["X-Seiche-Railway-Deployment"] == _railway()["deployment_id"]
    assert allowed.headers["X-Seiche-Release-SHA"] == "a" * 40


def test_shadow_posts_fail_closed_before_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEICHE_RAILWAY_STATEFUL_MODE", "shadow")
    routed = False

    async def call_next(_request: Request) -> Response:
        nonlocal routed
        routed = True
        return Response(content=b"unexpected", status_code=200)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/agent-room/participants/self",
            "headers": [],
            "query_string": b"",
            "scheme": "https",
            "server": ("shadow.invalid", 443),
            "client": ("127.0.0.1", 1),
        }
    )

    response = asyncio.run(api._railway_cutover_edge_guard(request, call_next))

    assert response.status_code == 503
    assert json.loads(response.body) == {"status": "shadow_read_only"}
    assert response.headers["Retry-After"] == "10"
    assert routed is False


def test_activation_grant_binds_public_probe_edge_token_and_deployment(
    tmp_path: Path,
) -> None:
    _fence_value, _request_value, candidate = _candidate(tmp_path)
    token_digest = cutover.edge_token_sha256("edge-token-" + "x" * 32)
    grant = {
        "schema": cutover.GRANT_SCHEMA,
        "repository": migration.REPOSITORY,
        "workflow": cutover.WORKFLOW,
        "commit": candidate["request"]["commit"],
        "request_id": candidate["request"]["id"],
        "candidate_receipt_sha256": hashlib.sha256(
            migration.canonical_document(candidate)
        ).hexdigest(),
        "fence_sha256": candidate["fence"]["sha256"],
        "deployment_id": candidate["railway"]["deployment_id"],
        "edge_token_sha256": token_digest,
        "public_base_url": "https://api.seiche.info",
        "public_probe_sha256": "1" * 64,
        "activated_at": "2026-08-23T03:15:00Z",
        "confirmation": "RAILWAY_BECOMES_SOLE_WRITER",
    }
    validated = cutover.validate_grant(
        grant,
        candidate_receipt=candidate,
        edge_token_digest=token_digest,
        now=datetime(2026, 8, 23, 3, 16, tzinfo=UTC),
    )
    assert validated["deployment_id"] == _railway()["deployment_id"]

    grant["deployment_id"] = "99999999-9999-4999-8999-999999999999"
    with pytest.raises(cutover.CutoverContractError, match="binding"):
        cutover.validate_grant(
            grant,
            candidate_receipt=candidate,
            edge_token_digest=token_digest,
            now=datetime(2026, 8, 23, 3, 16, tzinfo=UTC),
        )


def test_authority_publication_is_atomic_idempotent_and_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform = tmp_path / "platform"
    monkeypatch.setattr(migration, "PLATFORM_ROOT", platform)
    fence = _fence()
    archive = tmp_path / "source.tar"
    bundle_path = tmp_path / "source.bundle"
    archive.write_bytes(b"source archive\n")
    bundle_path.write_bytes(b"source bundle\n")
    request = _request(fence)
    request["source_archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    request["source_bundle_sha256"] = hashlib.sha256(
        bundle_path.read_bytes()
    ).hexdigest()
    request_path = tmp_path / "request.json"
    request_path.write_bytes(migration.canonical_document(request))
    fence_digest = str(request["source_fence_sha256"])
    fence_path = platform / "authority-fences" / f"{fence_digest}.json"
    fence_path.parent.mkdir(parents=True)
    fence_path.write_bytes(migration.canonical_document(fence))
    railway = _railway()
    railway["volume_mount_path"] = str(platform)
    candidate = cutover.render_candidate_receipt(
        request,
        fence,
        _bundle(tmp_path, request),
        migration.RestoredDatabase(
            migration.derive_database_name(
                str(request["snapshot_id"]),
                str(request["source_content_set_sha256"]),
            ),
            "postgresql://generation-only",
            (11, 21, 31, 41),
        ),
        railway=railway,
        generation_digests={
            "market": "7" * 64,
            "nbs": "8" * 64,
            "api": "9" * 64,
            "palimpsest-china": "6" * 64,
        },
        nbs_audit_result="verified_head",
        agent_room_audit=migration.absent_agent_room_audit(),
        started_at="2026-08-23T03:06:00Z",
        completed_at="2026-08-23T03:09:00Z",
    )
    candidate_path = (
        platform / "cutover-receipts" / (f"{request['request_id']}.candidate.json")
    )
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_bytes(migration.canonical_document(candidate))
    probe = {
        "schema": cutover.PUBLIC_PROBE_SCHEMA,
        "url": "https://api.seiche.info/api/health",
        "status": 200,
        "authority": "candidate",
        "deployment_id": railway["deployment_id"],
        "commit": request["commit"],
        "body_sha256": "1" * 64,
        "observed_at": "2026-08-23T03:14:00Z",
    }
    probe_body = migration.canonical_document(probe)
    token = "edge-token-" + "x" * 32
    grant = {
        "schema": cutover.GRANT_SCHEMA,
        "repository": migration.REPOSITORY,
        "workflow": cutover.WORKFLOW,
        "commit": request["commit"],
        "request_id": request["request_id"],
        "candidate_receipt_sha256": hashlib.sha256(
            migration.canonical_document(candidate)
        ).hexdigest(),
        "fence_sha256": fence_digest,
        "deployment_id": railway["deployment_id"],
        "edge_token_sha256": cutover.edge_token_sha256(token),
        "public_base_url": "https://api.seiche.info",
        "public_probe_sha256": hashlib.sha256(probe_body).hexdigest(),
        "activated_at": "2026-08-23T03:15:00Z",
        "confirmation": "RAILWAY_BECOMES_SOLE_WRITER",
    }
    grant_body = migration.canonical_document(grant)
    arguments = {
        "platform_root": platform,
        "request_path": request_path,
        "source_archive": archive,
        "source_bundle": bundle_path,
        "edge_token": token,
        "railway": railway,
        "now": datetime(2026, 8, 23, 3, 16, tzinfo=UTC),
        "runtime_gid": os.getgid(),
    }

    digests = cutover.publish_authority_documents(
        str(request["request_id"]),
        probe_body,
        grant_body,
        **arguments,
    )
    authority = platform / "authority"
    published_probe = authority / f"{request['request_id']}.public-probe.json"
    published_grant = authority / "activation-grant.json"
    assert digests == (
        hashlib.sha256(probe_body).hexdigest(),
        hashlib.sha256(grant_body).hexdigest(),
    )
    assert published_probe.read_bytes() == probe_body
    assert published_grant.read_bytes() == grant_body
    assert published_probe.stat().st_mode & 0o777 == 0o440
    assert published_grant.stat().st_mode & 0o777 == 0o440

    assert (
        cutover.publish_authority_documents(
            str(request["request_id"]),
            probe_body,
            grant_body,
            **arguments,
        )
        == digests
    )

    changed_grant = dict(grant)
    changed_grant["activated_at"] = "2026-08-23T03:16:00Z"
    changed_arguments = dict(arguments)
    changed_arguments["now"] = datetime(2026, 8, 23, 3, 17, tzinfo=UTC)
    with pytest.raises(cutover.CutoverContractError, match="immutable.*differs"):
        cutover.publish_authority_documents(
            str(request["request_id"]),
            probe_body,
            migration.canonical_document(changed_grant),
            **changed_arguments,
        )
    assert published_grant.read_bytes() == grant_body


def test_production_receipt_preserves_irreversible_authority_boundary(
    tmp_path: Path,
) -> None:
    _fence_value, _request_value, candidate = _candidate(tmp_path)
    grant = {
        "public_base_url": "https://api.seiche.info",
        "public_probe_sha256": "1" * 64,
        "activated_at": "2026-08-23T03:14:00Z",
    }
    commands = cutover.worker_commands()
    activation = cutover.render_activation_receipt(
        candidate,
        grant,
        worker_commands=commands,
        workers_started_at="2026-08-23T03:15:00Z",
    )
    assert activation["authority"] == {
        "mode": "production",
        "source": "railway",
        "hetzner_writers_frozen": True,
        "railway_writers_started": True,
        "public_traffic_enabled": True,
    }
    assert set(activation["workers"]) == {"market", "source"}
    assert all(row["process_started"] is True for row in activation["workers"].values())
    assert all("shell" not in row for row in activation["workers"].values())
    validated = cutover.validate_activation_receipt(
        activation,
        candidate_receipt=candidate,
        grant=grant,
    )
    assert validated["authority"]["source"] == "railway"
    assert validated["activated_at"] == grant["activated_at"]
    assert validated["workers_started_at"] == "2026-08-23T03:15:00Z"

    tampered = copy.deepcopy(activation)
    tampered["candidate_receipt_sha256"] = "0" * 64
    with pytest.raises(cutover.CutoverContractError, match="binding is invalid"):
        cutover.validate_activation_receipt(
            tampered,
            candidate_receipt=candidate,
            grant=grant,
        )


def test_candidate_environment_drops_control_database_and_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fence_value, _request_value, receipt = _candidate(tmp_path)
    generation = str(receipt["filesystem"]["generation"])
    generation_path = tmp_path / "generations" / generation
    _empty_palimpsest_state(generation_path)
    restore = cutover.CutoverRestore(
        receipt=receipt,
        database_dsn="postgresql://generation-only",
        receipt_path=migration.PLATFORM_ROOT / "cutover-receipts" / "candidate.json",
        generation_path=generation_path,
    )
    observed: dict[str, object] = {}

    def palimpsest_environment(
        state_root: Path,
        *,
        runtime_uid: int,
        runtime_gid: int,
    ) -> dict[str, str]:
        observed.update(
            {
                "state_root": state_root,
                "runtime_uid": runtime_uid,
                "runtime_gid": runtime_gid,
            }
        )
        return {}

    monkeypatch.setattr(
        migration,
        "palimpsest_runtime_environment",
        palimpsest_environment,
    )
    environment = cutover.candidate_environment(
        {
            "PORT": "8080",
            "DATABASE_URL": "postgresql://control-secret",
            "RAILWAY_TOKEN": "drop-me",
            "SEICHE_RUNTIME_DATA_DIR": "/poison/runtime",
            "SEICHE_AGENT_ROOM_DB_PATH": "/poison/rooms.sqlite",
            "SEICHE_ATTEST_DIR": "/poison/attest",
            "SEICHE_AGENT_ROOM_EXPECTED_KEY_ID": "f" * 64,
        },
        restore,
        edge_token="edge-token-" + "x" * 32,
    )
    assert "DATABASE_URL" not in environment
    assert "RAILWAY_TOKEN" not in environment
    assert environment["SEICHE_DATABASE_URL"] == "postgresql://generation-only"
    assert environment["SEICHE_RAILWAY_STATEFUL_MODE"] == "cutover_candidate"
    assert environment["SEICHE_RUNTIME_DATA_DIR"] == str(generation_path / "api")
    assert environment["SEICHE_AGENT_ROOM_DB_PATH"] == str(
        generation_path / "api" / "_agent_room" / "agent-room.sqlite"
    )
    assert environment["SEICHE_ATTEST_DIR"] == str(generation_path / "api" / "_attest")
    assert environment["SEICHE_AGENT_ROOM_EXPECTED_KEY_ID"] == (
        migration.AGENT_ROOM_UNPROVISIONED_KEY
    )
    assert observed == {
        "state_root": generation_path / "palimpsest-china",
        "runtime_uid": 10_001,
        "runtime_gid": 10_001,
    }


def test_activation_runtime_binds_exact_candidate_and_agent_room_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform = tmp_path / "platform"
    monkeypatch.setattr(migration, "PLATFORM_ROOT", platform)
    _fence_value, _request_value, candidate = _candidate(tmp_path)
    generation = str(candidate["filesystem"]["generation"])
    generation_path = platform / "generations" / generation
    generation_path.mkdir(parents=True)
    candidate_path = (
        platform / "cutover-receipts" / f"{candidate['request']['id']}.candidate.json"
    )
    candidate_path.parent.mkdir()
    candidate_path.write_bytes(migration.canonical_document(candidate))
    monkeypatch.setattr(
        migration,
        "palimpsest_runtime_environment",
        lambda _root, **_kwargs: {},
    )
    candidate_environment = cutover.candidate_environment(
        {"PORT": "8080"},
        cutover.CutoverRestore(
            receipt=candidate,
            database_dsn="postgresql://generation-only",
            receipt_path=candidate_path,
            generation_path=generation_path,
        ),
        edge_token="edge-token-" + "x" * 32,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )
    assert cutover.validate_candidate_runtime(candidate_environment) == candidate
    validated_paths: list[Path] = []
    monkeypatch.setattr(
        migration,
        "validate_receipted_generation",
        lambda path, *_args, **_kwargs: validated_paths.append(path),
    )
    monkeypatch.setattr(
        migration,
        "inspect_postgres_counts",
        lambda _dsn: tuple(candidate["database"]["critical_table_counts"]),
    )
    cutover._validate_candidate_activation_boundary(
        candidate_environment,
        candidate,
    )
    assert validated_paths == [generation_path]

    monkeypatch.setattr(
        migration,
        "inspect_postgres_counts",
        lambda _dsn: (0, 0, 0, 0),
    )
    with pytest.raises(cutover.CutoverContractError, match="PostgreSQL counts changed"):
        cutover._validate_candidate_activation_boundary(
            candidate_environment,
            candidate,
        )

    grant = {
        "public_base_url": "https://api.seiche.info",
        "public_probe_sha256": "1" * 64,
        "activated_at": "2026-08-23T03:14:00Z",
    }
    activation = cutover.render_activation_receipt(
        candidate,
        grant,
        worker_commands=cutover.worker_commands(),
        workers_started_at="2026-08-23T03:15:00Z",
    )
    activation_path = (
        platform / "cutover-receipts" / f"{candidate['request']['id']}.activation.json"
    )
    activation_path.write_bytes(migration.canonical_document(activation))
    production = cutover.production_environment(
        candidate_environment,
        activation,
        receipt_path=activation_path,
    )
    assert cutover.validate_activation_runtime(production) == activation

    replaced_key = dict(production)
    replaced_key["SEICHE_AGENT_ROOM_EXPECTED_KEY_ID"] = "f" * 64
    with pytest.raises(cutover.CutoverContractError, match="key binding"):
        cutover.validate_activation_runtime(replaced_key)

    rebound = copy.deepcopy(activation)
    rebound["candidate_receipt_sha256"] = "e" * 64
    activation_path.write_bytes(migration.canonical_document(rebound))
    rebound_environment = dict(production)
    rebound_environment["SEICHE_RAILWAY_ACTIVATION_RECEIPT_SHA256"] = hashlib.sha256(
        migration.canonical_document(rebound)
    ).hexdigest()
    with pytest.raises(
        cutover.CutoverContractError, match="activation receipt binding"
    ):
        cutover.validate_activation_runtime(rebound_environment)


def test_healthz_distinguishes_candidate_from_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEICHE_RAILWAY_STATEFUL_MODE", "cutover_candidate")
    monkeypatch.setattr(api.mcp_server, "agent_room_release_ready", lambda: True)
    monkeypatch.setattr(
        cutover,
        "validate_candidate_runtime",
        lambda _environment: {
            "authority": {
                "source": "none",
                "hetzner_writers_frozen": True,
                "railway_writers_started": False,
            }
        },
    )
    monkeypatch.setattr(
        api,
        "_health_response",
        lambda *_args, **_kwargs: {
            "version": "0.11.0",
            "generated_at": "2026-08-23T03:00:00Z",
        },
    )
    result = asyncio.run(api.railway_stateful_health(Response()))
    assert result["status"] == "ready"
    assert result["mode"] == "cutover_candidate"


def test_worker_commands_are_argument_vectors_without_shell() -> None:
    commands = cutover.worker_commands()
    assert commands["market"][-2:] == ["--poll-seconds", "30"]
    assert commands["source"][-2:] == ["--poll-seconds", "300"]
    assert all(isinstance(command, list) for command in commands.values())
    assert all("sh" not in command[:3] for command in commands.values())


def test_stateful_entrypoint_dispatches_only_closed_request_schemas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_bytes(
        migration.canonical_document({"schema": migration.REQUEST_SCHEMA})
    )
    monkeypatch.setattr(migration, "run_shadow", lambda: 51)
    assert stateful_entrypoint.run(request_path) == 51

    request_path.write_bytes(
        migration.canonical_document({"schema": cutover.REQUEST_SCHEMA})
    )
    monkeypatch.setattr(cutover, "run", lambda: 52)
    assert stateful_entrypoint.run(request_path) == 52

    request_path.write_bytes(migration.canonical_document({"schema": "unknown"}))
    with pytest.raises(cutover.CutoverContractError, match="unsupported"):
        stateful_entrypoint.run(request_path)


def test_cutover_workflow_and_host_tools_cannot_auto_move_authority() -> None:
    workflow = CUTOVER_WORKFLOW.read_text(encoding="utf-8")
    fence = CUTOVER_FENCE.read_text(encoding="utf-8")
    edge = CUTOVER_EDGE.read_text(encoding="utf-8")
    dockerfile = STATEFUL_DOCKERFILE.read_text(encoding="utf-8")
    railway = json.loads(CUTOVER_CONFIG.read_text(encoding="utf-8"))

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "schedule:" not in workflow
    assert "environment: railway-stateful-cutover-candidate" in workflow
    assert "environment: railway-stateful-activation" in workflow
    assert "HETZNER_FROZEN_RAILWAY_READ_ONLY" in workflow
    assert "PUBLIC_EDGE_PROVES_CANDIDATE_ACTIVATE_RAILWAY" in workflow
    assert "RAILWAY_BECOMES_SOLE_WRITER" in workflow
    assert "railway variable set SEICHE_RAILWAY_EDGE_TOKEN --stdin" in workflow
    assert "publish-authority" in workflow
    assert "validate_public_candidate_probe" in workflow
    assert "remote_writer" not in workflow
    assert workflow.count("actions/attest-build-provenance@") == 2
    assert "secrets.HETZNER" not in workflow
    assert '"HETZNER_DATABASE_URL"' in workflow
    assert "/authority-fences/$FENCE_SHA256.json" in workflow
    assert '"x-seiche-railway-deployment"' in workflow
    assert "stateful_entrypoint.py" in dockerfile
    assert railway["deploy"] == {
        "healthcheckPath": "/healthz",
        "healthcheckTimeout": 3600,
        "restartPolicyType": "ON_FAILURE",
        "restartPolicyMaxRetries": 3,
    }
    assert "railway.cutover.json" in workflow

    assert "RAILWAY_CANDIDATE_STOPPED_NO_WRITERS" in fence
    assert "stale-state rollback is forbidden after Railway activation" in fence
    assert "RAILWAY_CANDIDATE_RECEIPTED_READ_ONLY" in edge
    assert "local rollback is forbidden after Railway activation" in edge
    assert "SEICHE_RAILWAY_EDGE_TOKEN=%s" in edge
    assert "edge_token_sha256" in edge

    wrapper = (ROOT / "ops" / "deploy" / "seiche-deploy-wrapper.sh").read_text()
    installer = (ROOT / "ops" / "deploy" / "install-market-platform.sh").read_text()
    for name in (
        "seiche-railway-cutover-fence.sh",
        "seiche-railway-edge-mode.sh",
    ):
        assert f'"ops/deploy/{name}": "100755"' in wrapper
        assert f"/etc/seiche/libexec/{name}" in wrapper
        assert f"/etc/seiche/libexec/{name}" in installer


def test_cutover_volume_file_commands_use_pinned_cli_ordering() -> None:
    workflow = CUTOVER_WORKFLOW.read_text(encoding="utf-8")
    runbook = CUTOVER_RUNBOOK.read_text(encoding="utf-8")

    logical_workflow = workflow.replace("\\\n", " ")
    workflow_commands = [
        " ".join(line[line.index("railway volume ") :].split())
        for line in logical_workflow.splitlines()
        if "railway volume " in line and " files " in line
    ]
    workflow_prefix = (
        'railway volume --project "$RAILWAY_PROJECT_ID" '
        '--environment "$RAILWAY_ENVIRONMENT_ID" '
        '--service "$RAILWAY_SERVICE_ID" files '
        '--volume "$RAILWAY_VOLUME_ID" '
    )
    assert len(workflow_commands) == 4
    assert all(command.startswith(workflow_prefix) for command in workflow_commands)
    workflow_operations = [
        command.removeprefix(workflow_prefix).split()[0]
        for command in workflow_commands
    ]
    assert workflow_operations.count("list") == 1
    assert workflow_operations.count("download") == 3
    assert "railway volume files" not in workflow

    logical_runbook = runbook.replace("\\\n", " ")
    runbook_commands = [
        " ".join(line[line.index("railway volume ") :].split())
        for line in logical_runbook.splitlines()
        if "railway volume " in line and " files " in line
    ]
    runbook_prefix = (
        "railway volume --project REVIEWED_PROJECT_ID "
        "--environment REVIEWED_ENVIRONMENT_ID "
        "--service REVIEWED_SERVICE_ID files "
        "--volume REVIEWED_VOLUME_ID upload "
    )
    assert len(runbook_commands) == 2
    assert all(command.startswith(runbook_prefix) for command in runbook_commands)
    assert "railway volume files" not in runbook


def test_historical_authority_requires_a_grant_inside_the_original_fence(
    tmp_path: Path,
) -> None:
    fence, request, candidate = _candidate(tmp_path)
    observed = datetime(2026, 8, 24, 3, 30, tzinfo=UTC)
    with pytest.raises(cutover.CutoverContractError, match="currently valid"):
        cutover.validate_fence(fence, now=observed)
    cutover.validate_fence(fence, now=observed, require_current=False)
    with pytest.raises(cutover.CutoverContractError, match="not fresh"):
        cutover.validate_request(request, fence=fence, now=observed)
    cutover.validate_request(
        request,
        fence=fence,
        now=observed,
        require_fresh=False,
    )

    token_digest = cutover.edge_token_sha256("edge-token-" + "x" * 32)
    grant = {
        "schema": cutover.GRANT_SCHEMA,
        "repository": migration.REPOSITORY,
        "workflow": cutover.WORKFLOW,
        "commit": candidate["request"]["commit"],
        "request_id": candidate["request"]["id"],
        "candidate_receipt_sha256": hashlib.sha256(
            migration.canonical_document(candidate)
        ).hexdigest(),
        "fence_sha256": candidate["fence"]["sha256"],
        "deployment_id": candidate["railway"]["deployment_id"],
        "edge_token_sha256": token_digest,
        "public_base_url": "https://api.seiche.info",
        "public_probe_sha256": "1" * 64,
        "activated_at": "2026-08-23T03:15:00Z",
        "confirmation": "RAILWAY_BECOMES_SOLE_WRITER",
    }
    with pytest.raises(cutover.CutoverContractError, match="not fresh"):
        cutover.validate_grant(
            grant,
            candidate_receipt=candidate,
            edge_token_digest=token_digest,
            now=observed,
        )
    validated = cutover.validate_grant(
        grant,
        candidate_receipt=candidate,
        edge_token_digest=token_digest,
        now=observed,
        require_fresh=False,
    )
    assert validated["confirmation"] == "RAILWAY_BECOMES_SOLE_WRITER"


@pytest.mark.parametrize("with_activation", (False, True))
def test_expired_cutover_restart_uses_durable_grant_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_activation: bool,
) -> None:
    platform = tmp_path / "platform"
    monkeypatch.setattr(migration, "PLATFORM_ROOT", platform)
    fence = _fence()
    archive = tmp_path / "source.tar"
    bundle_path = tmp_path / "source.bundle"
    archive.write_bytes(b"source archive\n")
    bundle_path.write_bytes(b"source bundle\n")
    request = _request(fence)
    request["source_archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    request["source_bundle_sha256"] = hashlib.sha256(
        bundle_path.read_bytes()
    ).hexdigest()
    request_path = tmp_path / "request.json"
    request_path.write_bytes(migration.canonical_document(request))
    fence_digest = str(request["source_fence_sha256"])
    fence_path = platform / "authority-fences" / f"{fence_digest}.json"
    fence_path.parent.mkdir(parents=True)
    fence_path.write_bytes(migration.canonical_document(fence))

    railway = _railway()
    railway["volume_mount_path"] = str(platform)
    candidate = cutover.render_candidate_receipt(
        request,
        fence,
        _bundle(tmp_path, request),
        migration.RestoredDatabase(
            migration.derive_database_name(
                str(request["snapshot_id"]),
                str(request["source_content_set_sha256"]),
            ),
            "postgresql://generation-only",
            (11, 21, 31, 41),
        ),
        railway=railway,
        generation_digests={
            "market": "7" * 64,
            "nbs": "8" * 64,
            "api": "9" * 64,
            "palimpsest-china": "6" * 64,
        },
        nbs_audit_result="verified_head",
        agent_room_audit=migration.absent_agent_room_audit(),
        started_at="2026-08-23T03:06:00Z",
        completed_at="2026-08-23T03:09:00Z",
    )
    receipt_path = (
        platform / "cutover-receipts" / (f"{request['request_id']}.candidate.json")
    )
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_bytes(migration.canonical_document(candidate))
    generation_path = platform / "generations" / candidate["filesystem"]["generation"]
    generation_path.parent.mkdir()
    generation_path.mkdir()
    _empty_palimpsest_state(generation_path)
    restore = cutover.CutoverRestore(
        candidate,
        "postgresql://generation-only",
        receipt_path,
        generation_path,
    )
    token = "edge-token-" + "x" * 32
    token_digest = cutover.edge_token_sha256(token)
    grant = {
        "schema": cutover.GRANT_SCHEMA,
        "repository": migration.REPOSITORY,
        "workflow": cutover.WORKFLOW,
        "commit": request["commit"],
        "request_id": request["request_id"],
        "candidate_receipt_sha256": hashlib.sha256(
            migration.canonical_document(candidate)
        ).hexdigest(),
        "fence_sha256": fence_digest,
        "deployment_id": railway["deployment_id"],
        "edge_token_sha256": token_digest,
        "public_base_url": "https://api.seiche.info",
        "public_probe_sha256": "1" * 64,
        "activated_at": "2026-08-23T03:15:00Z",
        "confirmation": "RAILWAY_BECOMES_SOLE_WRITER",
    }
    grant_path = platform / "authority" / "activation-grant.json"
    grant_path.parent.mkdir()
    grant_path.write_bytes(migration.canonical_document(grant))
    activation_path = (
        platform / "cutover-receipts" / (f"{request['request_id']}.activation.json")
    )
    if with_activation:
        activation = cutover.render_activation_receipt(
            candidate,
            grant,
            worker_commands=cutover.worker_commands(),
            workers_started_at="2026-08-23T03:15:01Z",
        )
        activation_path.write_bytes(migration.canonical_document(activation))

    for name, value in {
        "SEICHE_RAILWAY_AUTHORITY_FENCE_SHA256": fence_digest,
        "DATABASE_URL": "postgresql://control",
        "SEICHE_RAILWAY_EDGE_TOKEN": token,
        "PORT": "8080",
        "RAILWAY_DEPLOYMENT_ID": railway["deployment_id"],
        "RAILWAY_PROJECT_ID": railway["project_id"],
        "RAILWAY_ENVIRONMENT_ID": railway["environment_id"],
        "RAILWAY_SERVICE_ID": railway["service_id"],
        "SEICHE_RAILWAY_VOLUME_ID": railway["volume_id"],
        "RAILWAY_VOLUME_NAME": railway["volume_name"],
        "RAILWAY_VOLUME_MOUNT_PATH": railway["volume_mount_path"],
        "RAILWAY_REPLICA_REGION": railway["region"],
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cutover.os, "geteuid", lambda: 0)
    monkeypatch.setattr(cutover.os, "getegid", lambda: 0)
    monkeypatch.setattr(
        migration, "validate_bundle", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        migration,
        "palimpsest_runtime_environment",
        lambda _root, **_kwargs: {},
    )
    restore_modes: list[bool] = []

    def restore_for_restart(*_args, **kwargs):
        restore_modes.append(kwargs["active_resume"])
        return restore

    monkeypatch.setattr(cutover, "restore_candidate", restore_for_restart)
    monkeypatch.setattr(cutover, "_prepare_authority_directory", lambda _path: None)
    calls: list[str] = []
    monkeypatch.setattr(
        cutover,
        "supervise_production",
        lambda _environment: calls.append("production") or 71,
    )
    monkeypatch.setattr(
        cutover,
        "activate_and_supervise",
        lambda *_args, **_kwargs: calls.append("activation") or 72,
    )

    result = cutover.run(
        request_path=request_path,
        source_archive=archive,
        source_bundle=bundle_path,
        platform_root=platform,
    )
    assert result == (71 if with_activation else 72)
    assert calls == (["production"] if with_activation else ["activation"])
    assert restore_modes == [with_activation]
