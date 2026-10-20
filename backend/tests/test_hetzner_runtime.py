"""Authority, restart, producer and process-boundary regressions for Hetzner."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
import pytest

from seiche import (
    api, assemble, hetzner_runtime as runtime, ingest_runtime, market_runtime,
    mcp_server, repository, standby,
)
from seiche.stateful_migration import absent_agent_room_audit
from test_api_caching import _release_receipt

VERIFY_DATABASE = runtime._verify_database


def seal(path, value):
    body = runtime.canonical(value) + b"\n"
    path.write_bytes(body)
    path.chmod(0o640)
    return hashlib.sha256(body).hexdigest()


@pytest.fixture
def accepted(tmp_path, monkeypatch, fake_snap):
    for key in tuple(os.environ):
        if key.startswith(("SEICHE_HETZNER_", "SEICHE_STANDBY_", "SEICHE_RAILWAY_",
                           "SEICHE_PALIMPSEST_CHINA_", "RAILWAY_", "PG")):
            monkeypatch.delenv(key)
    monkeypatch.setattr(standby, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(standby, "_root_controlled_ancestry", lambda path: None)
    monkeypatch.setattr(runtime, "RUNTIME_UID", os.geteuid())
    monkeypatch.setattr(runtime, "RUNTIME_GID", os.getegid())
    monkeypatch.setattr(runtime, "_context", None)
    monkeypatch.setattr(runtime, "_process_started_at", None)
    monkeypatch.setattr(runtime, "_verify_signature", lambda *args: None)
    monkeypatch.setattr(standby, "_verify_source", lambda grant: None)
    monkeypatch.setattr(runtime, "_verify_database", lambda grant, restore: None)
    root = tmp_path.resolve()
    authority = root / "authority"
    authority.mkdir()
    generation = root / "generation"
    generation.mkdir()
    socket = root / "postgres"
    socket.mkdir()
    hostid = root / "machine-id"
    hostid.write_text("a" * 32 + "\n")
    hostid.chmod(0o444)
    token = "private_production_capability_" + "t" * 32
    envelope = assemble._build_handoff(deepcopy(fake_snap), _release_receipt(fake_snap), "a" * 40)
    parent = {"commit": "a" * 40, "handoff_id": envelope["handoff_id"],
              "envelope_sha256": standby.digest(envelope),
              "payload_sha256": envelope["payload_sha256"]}
    grant = {
        "schema": runtime.GRANT_SCHEMA, "provider": "hetzner", "authority": "production",
        "runtime_id": "1" * 64, "issued_at": "2026-10-20T16:00:00Z",
        "expires_at": "2026-10-20T17:00:00Z",
        "code": {"commit": "b" * 40, "tree": "c" * 40},
        "transfer_sha256": "", "restore_sha256": "",
        "host": {"identity": hashlib.sha256(("a" * 32).encode()).hexdigest(),
                 "volume_uuid": "12345678-1234-1234-1234-123456789012",
                 "image_digest": "sha256:" + "d" * 64},
        "data": {"generation_root": str(generation), "database_name": "seiche_runtime_candidate",
                 "postgres_socket": str(socket), "postgres_port": 5432,
                 "postgres_role": "seiche_runtime"},
        "agent_room": {"expected_key_id": "unprovisioned"},
        "edge_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
    }
    restore = {
        "schema": "seiche.hetzner-final-restore.v1", "status": "PASS", "authority_changed": False,
        "writer_grant_issued": False, "public_origin_changed": False, "handover_id": "2" * 64,
        "code": grant["code"], "image_digest": grant["host"]["image_digest"],
        "host_identity": grant["host"]["identity"], "volume_uuid": grant["host"]["volume_uuid"],
        "final_state": {"equal": True, "source_manifest_sha256": "6" * 64,
                        "restored_manifest_sha256": "6" * 64},
        "source_backup": {"source": parent["commit"], "snapshot_id": "20261020T160000Z",
                          "capture_sha256": "7" * 64, "transfer_index_sha256": "9" * 64},
        "filesystem": {"status": "PASS", "generation_root": str(generation),
                       "tree_sha256": {name: "e" * 64 for name in
                                       ("api", "market", "nbs", "palimpsest-china")},
                       "palimpsest_environment": {},
                       "validated_parent_handoff": parent, "agent_room_audit": absent_agent_room_audit()},
        "postgres": {"database_name": grant["data"]["database_name"],
                     "postgres_socket": str(socket), "postgres_port": 5432,
                     "runtime_database_role": "seiche_runtime", "parent": parent,
                     "counts": [4, 3, 2, 1], "count_floor": [4, 3, 2, 1]},
    }
    grant["restore_sha256"] = seal(authority / "restore.json", restore)
    transfer = {
        "schema": "seiche.hetzner-final-transfer.v1", "status": "PASS",
        "authority_transferred": False, "writer_grant_issued": False, "handover_id": "2" * 64,
        "source": {"commit": parent["commit"], "original_pointer_sha256": "3" * 64,
                   "terminal_pointer_sha256": "4" * 64},
        "fence": {"receipt_sha256": "5" * 64, "all_application_writers_absent": True,
                  "database_admissions_closed": True},
        "final_state": {"equal": True, "source_manifest_sha256": "6" * 64,
                        "restored_manifest_sha256": "6" * 64},
        "capture": {"manifest_sha256": "7" * 64, "content_set_sha256": "8" * 64,
                    "snapshot_id": "20261020T160000Z"},
        "destination": {"provider": "hetzner", "host_identity": grant["host"]["identity"],
                        "volume_uuid": grant["host"]["volume_uuid"],
                        "generation_root": str(generation), "database_name": grant["data"]["database_name"],
                        "restore_receipt_sha256": grant["restore_sha256"]},
    }
    grant["transfer_sha256"] = seal(authority / "transfer.json", transfer)
    signed = {"schema": runtime.SIGNED_SCHEMA, "payload": grant, "signature": "test-signature"}
    grant_sha = seal(authority / "grant.json", signed)
    acceptance = {
        "schema": runtime.ACCEPTANCE_SCHEMA, "runtime_id": grant["runtime_id"],
        "grant_sha256": grant_sha, "transfer_sha256": grant["transfer_sha256"],
        "restore_sha256": grant["restore_sha256"], "code": grant["code"],
        "accepted_at": "2026-10-20T16:30:00Z",
    }
    seal(authority / "acceptance.json", acceptance)
    seal(authority / "active.json", runtime.pointer(grant, grant_sha))
    seal(authority / "runtime.json", {
        "schema": runtime.RUNTIME_SCHEMA, "runtime_id": grant["runtime_id"],
        "grant_sha256": grant_sha, "code": grant["code"], "host": grant["host"],
        "data": grant["data"], "http_socket": "/run/seiche-runtime/http.sock",
    })
    environment = {
        "SEICHE_HETZNER_AUTHORITY_DIR": str(authority),
        "SEICHE_HETZNER_GRANT_SHA256": grant_sha, "SEICHE_HETZNER_EDGE_TOKEN": token,
        "SEICHE_HETZNER_HOST_ID_PATH": str(hostid),
        "SEICHE_ENV": "production", "SEICHE_RELEASE_SHA": grant["code"]["commit"],
        "SEICHE_COLLECTOR_HEARTBEAT_REQUIRED": "1", "SEICHE_SOURCE_HEARTBEAT_REQUIRED": "1",
        "SEICHE_BOARD_AUTH": "0",
        "SEICHE_DATABASE_URL": make_conninfo(
            dbname=grant["data"]["database_name"], host=str(socket), port=5432, user="seiche_runtime"),
        **runtime.runtime_paths(grant, restore),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    for key in ("SEICHE_PREBUILT_HANDOFF_ID", "SEICHE_PREBUILT_PAYLOAD_SHA256"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(assemble, "_process_release_sha", None)
    monkeypatch.setattr(assemble, "_cache", {
        "at": 0.0, "payload": None, "source": None, "release_receipt": None,
        "release_handoff_id": None, "producer_sha": None,
    })
    pg = repository.PostgresMarketRepository(environment["SEICHE_DATABASE_URL"])
    monkeypatch.setattr(pg, "load_active_release_handoff_read_only", lambda: deepcopy(envelope))
    monkeypatch.setattr(pg, "load_worker_heartbeat", lambda component: None)
    monkeypatch.setattr(repository, "get_repository", lambda: pg)
    monkeypatch.setattr(api, "get_repository", lambda: pg)
    monkeypatch.setattr(api, "_PROD", True)
    monkeypatch.setattr(mcp_server, "_agent_room_readiness_passed", True)
    runtime.initialize()
    return SimpleNamespace(root=root, authority=authority, grant=grant, signed=signed,
                           grant_sha=grant_sha, restore=restore, transfer=transfer,
                           acceptance=acceptance, envelope=envelope, pg=pg, token=token)


def test_expired_but_durably_accepted_exact_grant_can_restart(accepted):
    assert runtime.initialize() == accepted.grant
    assert runtime.require_authority() == accepted.grant
    assert assemble.capture_process_release_sha() == accepted.grant["code"]["commit"]


@pytest.mark.parametrize("field,replacement", [
    ("provider", "railway"), ("authority", "standby"), ("runtime_id", "malformed"),
    ("expires_at", "2026-10-20T17:00:01Z"), ("issued_at", "2026-10-20T16:00:00"),
    ("transfer_sha256", "f" * 40), ("restore_sha256", "F" * 64),
])
def test_grant_rejects_foreign_authority_bad_digests_and_unbounded_window(
        accepted, field, replacement):
    signed = deepcopy(accepted.signed)
    signed["payload"][field] = replacement
    with pytest.raises(runtime.HetznerRuntimeError):
        runtime.validate_grant(signed)


@pytest.mark.parametrize("filename", ["grant.json", "acceptance.json", "active.json", "transfer.json", "restore.json"])
def test_missing_authority_document_prevents_restart(accepted, filename):
    (accepted.authority / filename).unlink()
    with pytest.raises((OSError, runtime.HetznerRuntimeError)):
        runtime.initialize()
    with pytest.raises(runtime.HetznerRuntimeError, match="startup"):
        runtime.require_authority()


@pytest.mark.parametrize("field,replacement", [
    ("accepted_at", "2026-10-20T17:00:01Z"), ("accepted_at", "2026-10-20T15:59:59Z"),
    ("grant_sha256", "f" * 64), ("runtime_id", "f" * 64), ("transfer_sha256", "f" * 64),
])
def test_restart_rejects_backdated_or_different_acceptance(accepted, field, replacement):
    value = deepcopy(accepted.acceptance)
    value[field] = replacement
    seal(accepted.authority / "acceptance.json", value)
    with pytest.raises(runtime.HetznerRuntimeError):
        runtime.initialize()
    with pytest.raises(runtime.HetznerRuntimeError, match="startup"):
        runtime.require_authority()


@pytest.mark.parametrize("document", ["grant", "acceptance", "runtime", "transfer", "restore"])
def test_each_request_rejects_changed_immutable_authority(accepted, document):
    path = accepted.authority / f"{document}.json"
    value = json.loads(path.read_bytes())
    value["changed"] = True
    seal(path, value)
    with pytest.raises(runtime.HetznerRuntimeError, match="digest"):
        runtime.require_authority()


def test_retired_pointer_immediately_rejects_requests_and_connections(accepted, monkeypatch):
    pointer = runtime.pointer(accepted.grant, accepted.grant_sha)
    pointer["state"] = "retired"
    seal(accepted.authority / "active.json", pointer)
    with pytest.raises(runtime.HetznerRuntimeError, match="retired"):
        runtime.require_authority()
    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: pytest.fail("connection after revocation"))
    with pytest.raises(runtime.HetznerRuntimeError, match="retired"):
        accepted.pg._connect()


@pytest.mark.parametrize("key,value", [
    ("RAILWAY_DEPLOYMENT_ID", "fake"), ("SEICHE_RAILWAY_STATEFUL_MODE", "production"),
    ("SEICHE_STANDBY_RECEIPT_PATH", ""), ("PGHOSTADDR", "127.0.0.1"),
    ("SEICHE_HETZNER_FAKE", "1"), ("SEICHE_SOURCE_HEARTBEAT_REQUIRED", "0"),
    ("SEICHE_RUNTIME_DATA_DIR", "/unrelated"), ("SEICHE_ENV", "development"),
    ("SEICHE_RELEASE_SHA", "f" * 40), ("SEICHE_PREBUILT_HANDOFF_ID", "f" * 64),
    ("SEICHE_HETZNER_EDGE_TOKEN", "other_" + "x" * 40),
    ("SEICHE_DATABASE_URL", "dbname=seiche_prod host=127.0.0.1 user=postgres"),
    ("SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH", "/unrelated/manifest.json"),
    ("SEICHE_PALIMPSEST_CHINA_PUBLICATION_STATUS", "published"),
])
def test_runtime_environment_drift_fails_closed_on_every_request(accepted, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        runtime.require_authority()


def test_actual_host_identity_cannot_be_replaced_by_claimed_environment(accepted):
    hostid = accepted.root / "machine-id"
    hostid.chmod(0o640)
    hostid.write_text("b" * 32)
    with pytest.raises(runtime.HetznerRuntimeError, match="actual host"):
        runtime.initialize()


@pytest.mark.parametrize("section,field,value", [
    ("fence", "all_application_writers_absent", False),
    ("fence", "database_admissions_closed", False),
    ("final_state", "equal", False),
    ("final_state", "restored_manifest_sha256", "f" * 64),
    ("source", "terminal_pointer_sha256", "3" * 64),
    ("destination", "database_name", "production"),
    ("destination", "host_identity", "f" * 64),
    ("destination", "restore_receipt_sha256", "f" * 64),
])
def test_final_transfer_requires_exact_terminal_fence_and_restored_state(accepted, section, field, value):
    transfer = deepcopy(accepted.transfer)
    transfer[section][field] = value
    with pytest.raises(runtime.HetznerRuntimeError):
        runtime.validate_transfer(transfer, accepted.restore, accepted.grant)


def test_final_restore_cannot_reuse_standby_or_escalate_reader(accepted):
    restore = deepcopy(accepted.restore)
    restore["schema"] = "seiche.hetzner-candidate-restore.v1"
    with pytest.raises(runtime.HetznerRuntimeError):
        runtime.validate_transfer(accepted.transfer, restore, accepted.grant)
    restore = deepcopy(accepted.restore)
    restore["postgres"]["runtime_database_role"] = "seiche_reader"
    with pytest.raises(runtime.HetznerRuntimeError):
        runtime.validate_transfer(accepted.transfer, restore, accepted.grant)
    restore = deepcopy(accepted.restore)
    restore["postgres"]["counts"][0] = 0
    with pytest.raises(runtime.HetznerRuntimeError):
        runtime.validate_transfer(accepted.transfer, restore, accepted.grant)


@pytest.mark.parametrize("field,replacement", [
    ("handover_id", "f" * 64), ("code", {"commit": "f" * 40, "tree": "c" * 40}),
    ("image_digest", "sha256:" + "f" * 64), ("host_identity", "f" * 64),
    ("volume_uuid", "12345678-1234-1234-1234-123456789013"),
    ("final_state", {"equal": True, "source_manifest_sha256": "f" * 64,
                     "restored_manifest_sha256": "f" * 64}),
    ("writer_grant_issued", True), ("public_origin_changed", True),
])
def test_final_restore_must_bind_the_same_transfer_source_host_and_state(accepted, field, replacement):
    restore = deepcopy(accepted.restore)
    restore[field] = replacement
    with pytest.raises(runtime.HetznerRuntimeError):
        runtime.validate_transfer(accepted.transfer, restore, accepted.grant)


@pytest.mark.parametrize("section,field,replacement", [
    ("source_backup", "capture_sha256", "f" * 64),
    ("source_backup", "snapshot_id", "20261020T150000Z"),
    ("source_backup", "transfer_index_sha256", None),
    ("filesystem", "status", "FAILED"),
    ("filesystem", "tree_sha256", {"api": "e" * 64}),
])
def test_final_restore_rejects_an_unrelated_capture_or_incomplete_tree_audit(
        accepted, section, field, replacement):
    restore = deepcopy(accepted.restore)
    restore[section][field] = replacement
    with pytest.raises(runtime.HetznerRuntimeError):
        runtime.validate_transfer(accepted.transfer, restore, accepted.grant)


def test_restored_palimpsest_paths_retain_one_complete_audited_bundle(accepted, monkeypatch):
    from seiche.palimpsest_china_activation import _BUNDLE_FILE_SPECS

    restore = deepcopy(accepted.restore)
    bundle = Path(accepted.grant["data"]["generation_root"]) / "palimpsest-china" / ("a" * 64)
    environment = {spec.environment: str(bundle / spec.filename) for spec in _BUNDLE_FILE_SPECS}
    restore["filesystem"]["palimpsest_environment"] = environment
    runtime.validate_transfer(accepted.transfer, restore, accepted.grant)
    paths = runtime.runtime_paths(accepted.grant, restore)
    assert environment.items() <= paths.items()
    assert paths["SEICHE_PALIMPSEST_CHINA_PUBLICATION_STATUS"] == "provisional"
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    runtime.validate_environment(accepted.grant, restore)
    monkeypatch.setenv("SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH", "/unrelated/manifest.json")
    with pytest.raises(runtime.HetznerRuntimeError):
        runtime.validate_environment(accepted.grant, restore)


@pytest.mark.parametrize("change", ["missing", "partial", "foreign", "mixed_bundle", "filename"])
def test_restored_palimpsest_paths_fail_closed_on_missing_or_mixed_binding(accepted, change):
    from seiche.palimpsest_china_activation import _BUNDLE_FILE_SPECS

    restore = deepcopy(accepted.restore)
    bundle = Path(accepted.grant["data"]["generation_root"]) / "palimpsest-china" / ("a" * 64)
    environment = {spec.environment: str(bundle / spec.filename) for spec in _BUNDLE_FILE_SPECS}
    if change == "missing":
        environment = None
    elif change == "partial":
        environment.pop(_BUNDLE_FILE_SPECS[0].environment)
    elif change == "foreign":
        environment[_BUNDLE_FILE_SPECS[0].environment] = "/unrelated/manifest.json"
    elif change == "mixed_bundle":
        environment[_BUNDLE_FILE_SPECS[0].environment] = str(bundle.parent / ("b" * 64) / "manifest.json")
    else:
        environment[_BUNDLE_FILE_SPECS[0].environment] = str(bundle / "wrong.json")
    restore["filesystem"]["palimpsest_environment"] = environment
    with pytest.raises(runtime.HetznerRuntimeError):
        runtime.validate_transfer(accepted.transfer, restore, accepted.grant)


def test_retained_parent_preserves_clock_and_never_satisfies_rebuilt_readiness(accepted):
    assert assemble.restore_cached_snapshot() == "hetzner_retained"
    assert assemble.cached_snapshot() == accepted.envelope["payload"]
    assert not assemble.cached_snapshot_was_rebuilt()
    client = TestClient(api.app)
    headers = {"x-seiche-hetzner-token": accepted.token}
    assert client.get("/api/health").status_code == 404
    response = client.get("/api/health", headers=headers)
    assert response.status_code == 200
    assert response.json()["generated_at"] == accepted.envelope["payload"]["generated_at"]
    assert response.json()["version"] == accepted.envelope["payload"]["version"]
    assert response.json()["snapshot_producer_sha"] == "a" * 40
    assert response.headers["x-seiche-release-sha"] == "b" * 40
    assert response.headers["x-seiche-provider"] == "hetzner"
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/health?require_rebuilt=true", headers=headers).status_code == 503


def test_unrelated_or_tampered_parent_cannot_seed_cache(accepted):
    wrong = deepcopy(accepted.envelope)
    wrong["payload"]["generated_at"] = "2026-10-20T18:00:00Z"
    with pytest.raises(ValueError):
        runtime.restore_snapshot(wrong)
    wrong = assemble._build_handoff(
        accepted.envelope["payload"], accepted.envelope["release_receipt"], "f" * 40)
    with pytest.raises(runtime.HetznerRuntimeError, match="unrelated"):
        runtime.restore_snapshot(wrong)


def test_same_release_restart_still_requires_new_build(accepted, monkeypatch):
    envelope = assemble._build_handoff(
        accepted.envelope["payload"], accepted.envelope["release_receipt"], "b" * 40)
    monkeypatch.setattr(accepted.pg, "load_active_release_handoff_read_only", lambda: envelope)
    assemble.restore_cached_snapshot()
    assert assemble._cache["producer_sha"] == "b" * 40
    assert not assemble.cached_snapshot_was_rebuilt()


def test_strict_health_requires_new_producer_and_both_fresh_worker_heartbeats(accepted, monkeypatch):
    assemble.restore_cached_snapshot()
    assemble._cache.update(source="rebuilt", producer_sha="b" * 40)
    now = datetime.now(UTC).replace(microsecond=0)
    monkeypatch.setattr(runtime, "_process_started_at", now - timedelta(seconds=1))
    fresh = {"heartbeat_at": now.isoformat(), "expected_by": (now + timedelta(minutes=2)).isoformat()}
    old = {"heartbeat_at": (now - timedelta(hours=1)).isoformat(),
           "expected_by": (now + timedelta(minutes=2)).isoformat()}
    monkeypatch.setattr(accepted.pg, "load_worker_heartbeat", lambda component: old)
    client = TestClient(api.app)
    headers = {"x-seiche-hetzner-token": accepted.token}
    result = client.get("/api/health?require_rebuilt=true", headers=headers)
    assert result.status_code == 503 and result.json()["status"] == "hetzner_workers_not_ready"
    monkeypatch.setattr(accepted.pg, "load_worker_heartbeat", lambda component: fresh)
    assert client.get("/api/health?require_rebuilt=true", headers=headers).status_code == 200
    assemble._cache["producer_sha"] = "a" * 40
    result = client.get("/api/health?require_rebuilt=true", headers=headers)
    assert result.status_code == 503 and result.json()["status"] == "hetzner_producer_not_ready"


def test_heartbeat_rejects_future_and_overdue_records(accepted):
    now = datetime.now(UTC)
    for stamp, deadline in ((now + timedelta(minutes=3), now + timedelta(minutes=4)),
                            (now - timedelta(minutes=2), now - timedelta(minutes=1))):
        assert not runtime.worker_heartbeat_ready(
            {"heartbeat_at": stamp.isoformat(), "expected_by": deadline.isoformat()}, now=now)


def test_heartbeat_from_same_second_before_restart_is_not_new_evidence(accepted, monkeypatch):
    started = datetime(2026, 10, 20, 18, 0, 0, 500000, tzinfo=UTC)
    monkeypatch.setattr(runtime, "_process_started_at", started)
    old = {"heartbeat_at": started.replace(microsecond=0).isoformat(),
           "expected_by": (started + timedelta(minutes=2)).isoformat()}
    assert not runtime.worker_heartbeat_ready(old, now=started + timedelta(seconds=2))
    new = {**old, "heartbeat_at": (started + timedelta(seconds=1)).isoformat()}
    assert runtime.worker_heartbeat_ready(new, now=started + timedelta(seconds=2))


def test_agent_room_uses_grant_key_and_does_not_provision_unbound_identity(accepted, monkeypatch):
    assert mcp_server._stateful_agent_room_expected_key_id() == ""
    with pytest.raises(mcp_server.agent_room.AgentRoomIntegrityError, match="not provisioned"):
        mcp_server._agent_room_store()
    grant = deepcopy(accepted.grant)
    grant["agent_room"]["expected_key_id"] = "e" * 64
    monkeypatch.setattr(runtime, "require_authority", lambda: grant)
    assert mcp_server._stateful_agent_room_expected_key_id() == "e" * 64
    with pytest.raises(mcp_server.agent_room.AgentRoomIntegrityError, match="differs"):
        mcp_server._assert_agent_room_expected_key("f" * 64)


def test_agent_room_refuses_after_grant_retirement(accepted):
    seal(accepted.authority / "active.json", {"state": "retired"})
    with pytest.raises(runtime.HetznerRuntimeError, match="retired"):
        mcp_server._stateful_agent_room_expected_key_id()


def test_api_drops_success_if_authority_is_revoked_during_request(accepted, monkeypatch):
    assemble.restore_cached_snapshot()
    original = api._health_response

    def revoke(*args, **kwargs):
        result = original(*args, **kwargs)
        seal(accepted.authority / "active.json", {"state": "retired"})
        return result

    monkeypatch.setattr(api, "_health_response", revoke)
    result = TestClient(api.app).get("/api/health", headers={"x-seiche-hetzner-token": accepted.token})
    assert result.status_code == 503 and result.json()["status"] == "hetzner_authority_unavailable"


def test_private_documents_reject_symlink_group_write_and_noncanonical_json(accepted):
    target = accepted.authority / "active.json"
    linked = accepted.authority / "linked.json"
    linked.symlink_to(target)
    with pytest.raises(ValueError):
        runtime.document(linked)
    target.chmod(0o660)
    with pytest.raises(ValueError):
        runtime.document(target)
    target.chmod(0o640)
    target.write_text('{ "state": "active" }\n')
    with pytest.raises(ValueError):
        runtime.document(target)


def test_real_ssh_signature_rejects_modified_payload_and_wrong_namespace(tmp_path, monkeypatch):
    key = tmp_path / "owner"
    subprocess.run(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                   check=True, capture_output=True)
    public = key.with_suffix(".pub").read_text().strip()
    message = runtime.canonical({"runtime_id": "a" * 64})
    path = tmp_path / "grant"
    path.write_bytes(message)
    subprocess.run(["/usr/bin/ssh-keygen", "-Y", "sign", "-f", str(key),
                    "-n", runtime.SIGNATURE_NAMESPACE, str(path)], check=True, capture_output=True)
    signature = path.with_suffix(".sig").read_text()
    runtime._verify_signature(message, signature, public)
    with pytest.raises(runtime.HetznerRuntimeError, match="signature"):
        runtime._verify_signature(message + b"changed", signature, public)
    path.with_suffix(".sig").unlink()
    subprocess.run(["/usr/bin/ssh-keygen", "-Y", "sign", "-f", str(key),
                    "-n", "seiche-railway-application-v1", str(path)], check=True, capture_output=True)
    with pytest.raises(runtime.HetznerRuntimeError, match="signature"):
        runtime._verify_signature(message, path.with_suffix(".sig").read_text(), public)


def test_supervisor_has_only_two_initialized_workers_and_private_api():
    commands = runtime.commands("/run/private/http.sock")
    assert set(commands) == {"api", "market", "source"}
    for kind in ("market", "source"):
        assert commands[kind][-2:] == ["--worker", kind]
        assert "seiche.hetzner_runtime" in commands[kind]
    assert "--uds" in commands["api"]
    assert "--port" not in commands["api"] and "--host" not in commands["api"]
    assert runtime.POLL_SECONDS == 2


def test_worker_initializes_before_any_collector_runs(monkeypatch):
    monkeypatch.setattr(runtime, "initialize", lambda: (_ for _ in ()).throw(
        runtime.HetznerRuntimeError("revoked")))
    with pytest.raises(runtime.HetznerRuntimeError, match="revoked"):
        runtime.run_worker("source")


def test_supervisor_kills_entire_child_groups_without_restart(accepted, monkeypatch):
    killed = []
    children = [SimpleNamespace(pid=101, poll=lambda: None, wait=lambda **kw: None),
                SimpleNamespace(pid=102, poll=lambda: 1, wait=lambda **kw: None)]
    monkeypatch.setattr(runtime.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    runtime._kill_children(children)
    assert killed == [(101, signal.SIGKILL), (102, signal.SIGKILL)]


def test_supervisor_exclusive_lock_rejects_duplicate_and_releases_on_close(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "RUNTIME_UID", os.geteuid())
    path = str(tmp_path / "http.sock")
    first = runtime._lock_supervisor(path)
    try:
        with pytest.raises(BlockingIOError):
            runtime._lock_supervisor(path)
    finally:
        os.close(first)
    second = runtime._lock_supervisor(path)
    os.close(second)


@pytest.mark.parametrize("role,foreign,counts", [
    ((True, False, False, False, False, True), (False, False, False), (4, 3, 2, 1)),
    ((False, False, False, False, False, False), (False, False, False), (4, 3, 2, 1)),
    ((False, True, False, False, False, True), (False, False, False), (4, 3, 2, 1)),
    ((False, False, False, False, False, True), (True, False, False), (4, 3, 2, 1)),
    ((False, False, False, False, False, True), (False, True, False), (4, 3, 2, 1)),
    ((False, False, False, False, False, True), (False, False, True), (4, 3, 2, 1)),
    ((False, False, False, False, False, True), (False, False, False), (0, 3, 2, 1)),
])
def test_actual_database_qualification_rejects_privilege_or_data_escape(
        accepted, monkeypatch, role, foreign, counts):
    import psycopg

    rows = iter([role, foreign, counts])
    statements = []

    class Connection:
        def execute(self, statement):
            statements.append(statement)
            return self

        def fetchone(self):
            return next(rows)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: Connection())
    with pytest.raises(runtime.HetznerRuntimeError):
        VERIFY_DATABASE(accepted.grant, accepted.restore)
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert all(statement.startswith(("SELECT ", "SET TRANSACTION READ ONLY")) for statement in statements)


def test_supervisor_retires_all_children_on_first_revocation_poll(accepted, monkeypatch):
    launched, stopped, sleeps = [], [], []
    lock_path = accepted.root / "lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    monkeypatch.setattr(runtime, "_lock_supervisor", lambda path: lock_fd)

    def spawn(command, **kwargs):
        child = SimpleNamespace(pid=1000 + len(launched), poll=lambda: None)
        launched.append(child)
        return child

    def sleep(seconds):
        sleeps.append(seconds)
        seal(accepted.authority / "active.json", {"state": "retired"})

    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    monkeypatch.setattr(runtime.time, "sleep", sleep)
    monkeypatch.setattr(runtime, "_kill_children", lambda children: stopped.extend(children))
    with pytest.raises(runtime.HetznerRuntimeError, match="retired"):
        runtime.supervise()
    assert len(launched) == 3
    assert stopped == launched
    assert sleeps == [2]


@pytest.mark.asyncio
async def test_lifespan_checks_authority_and_room_before_starting_rebuild(accepted, monkeypatch):
    calls = []
    original = runtime.initialize
    started = asyncio.Event()
    cancelled = asyncio.Event()

    def initialize():
        result = original()
        calls.append("authority")
        return result

    def audit():
        assert calls == ["authority"]
        calls.append("room")

    async def keep_warm():
        assert calls == ["authority", "room"]
        assert assemble._cache["source"] == "hetzner_retained"
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(runtime, "initialize", initialize)
    monkeypatch.setattr(mcp_server, "initialize_agent_room_readiness", audit)
    monkeypatch.setattr(api, "_keep_warm", keep_warm)
    async with api._lifespan(api.app):
        await asyncio.wait_for(started.wait(), timeout=2)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_lifespan_does_not_start_rebuild_when_authority_is_missing(accepted, monkeypatch):
    seal(accepted.authority / "active.json", {"state": "retired"})
    monkeypatch.setattr(mcp_server, "initialize_agent_room_readiness",
                        lambda: pytest.fail("Agent Room audit before authority"))
    monkeypatch.setattr(api, "_keep_warm", lambda: pytest.fail("rebuild before authority"))
    with pytest.raises(runtime.HetznerRuntimeError):
        async with api._lifespan(api.app):
            pytest.fail("application started without authority")


def test_real_supervised_process_group_is_killed_on_revocation():
    child = subprocess.Popen([sys.executable, "-I", "-c", "import time;time.sleep(60)"],
                             start_new_session=True)
    try:
        runtime._kill_children([child])
        assert child.returncode == -signal.SIGKILL
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()


def test_real_exited_group_leader_does_not_leave_a_collector_process(tmp_path):
    marker = tmp_path / "descendant-stopped"
    source = (
        "import os,signal,sys,time\n"
        "pid=os.fork()\n"
        "if pid:\n"
        "    print(pid,flush=True)\n"
        "    os._exit(0)\n"
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
        "time.sleep(1)\n"
        "open(sys.argv[1],'w').write('escaped')\n"
    )
    leader = subprocess.Popen([sys.executable, "-I", "-c", source, str(marker)],
                              start_new_session=True, stdout=subprocess.PIPE, text=True)
    descendant = None
    try:
        descendant = int(leader.stdout.readline())
        leader.wait(timeout=5)
        assert leader.returncode == 0
        runtime._kill_children([leader])
        # A bounded observation of a real child catches the former branch that
        # skipped killpg whenever the leader had already exited.
        import time

        time.sleep(1.1)
        assert not marker.exists()
    finally:
        leader.stdout.close()
        try:
            os.killpg(leader.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if leader.poll() is None:
            leader.wait(timeout=5)


@pytest.mark.asyncio
async def test_revocation_denies_direct_worker_entry_and_snapshot_publication(accepted):
    seal(accepted.authority / "active.json", {"state": "retired"})
    for operation in (
        ingest_runtime.run_legacy_worker,
        market_runtime.run_worker,
        ingest_runtime.collect_legacy_once,
        assemble._build_snapshot,
    ):
        with pytest.raises(runtime.HetznerRuntimeError, match="retired"):
            await operation()
    with pytest.raises(runtime.HetznerRuntimeError, match="retired"):
        await assemble._publish_rebuilt_snapshot(accepted.envelope["payload"], None)


@pytest.mark.asyncio
async def test_source_summary_cannot_write_after_awaited_collection_loses_authority(
        accepted, monkeypatch):
    writes = []

    async def gather():
        seal(accepted.authority / "active.json", {"state": "retired"})
        return {}, []

    monkeypatch.setattr(assemble, "_gather_sources", gather)
    with pytest.raises(ingest_runtime.LegacySourceSweepError):
        await ingest_runtime.collect_legacy_once(
            summary_writer=lambda *args: writes.append(args))
    assert writes == []


@pytest.mark.asyncio
async def test_source_worker_retirement_is_terminal_before_the_next_sweep(accepted, monkeypatch):
    calls = []

    async def collect():
        calls.append("collect")
        return {"status": "SUCCESS"}

    async def heartbeat(*args, **kwargs):
        pass

    async def parked_heartbeat(*args, **kwargs):
        await asyncio.Event().wait()

    async def retire(_seconds):
        seal(accepted.authority / "active.json", {"state": "retired"})

    monkeypatch.setattr(ingest_runtime, "collect_legacy_once", collect)
    monkeypatch.setattr(ingest_runtime, "_write_worker_heartbeat", heartbeat)
    monkeypatch.setattr(ingest_runtime, "_heartbeat_loop", parked_heartbeat)
    monkeypatch.setattr(ingest_runtime, "_systemd_notify", lambda message: None)
    monkeypatch.setattr(ingest_runtime.asyncio, "sleep", retire)
    with pytest.raises(runtime.HetznerRuntimeError, match="retired"):
        await ingest_runtime.run_legacy_worker(repository=object())
    assert calls == ["collect"]


@pytest.mark.asyncio
async def test_market_worker_retirement_is_terminal_before_the_next_cycle(accepted, monkeypatch):
    calls = []

    class Supervisor:
        async def run_due(self, **kwargs):
            calls.append("collect")
            return []

    async def retire(_seconds):
        seal(accepted.authority / "active.json", {"state": "retired"})

    monkeypatch.setattr(market_runtime, "_recover_completed_funding_export", lambda repo: None)
    monkeypatch.setattr(market_runtime, "build_supervisor", lambda **kwargs: Supervisor())
    monkeypatch.setattr(market_runtime, "_completed_run_handler", lambda **kwargs: None)
    monkeypatch.setattr(market_runtime, "_startup_nyfed_refresh_keys",
                        lambda repo, **kwargs: frozenset())
    monkeypatch.setattr(market_runtime, "_systemd_notify", lambda message: None)
    monkeypatch.setattr(market_runtime.asyncio, "sleep", retire)
    with pytest.raises(runtime.HetznerRuntimeError, match="retired"):
        await market_runtime.run_worker(repository=object(), registry=object())
    assert calls == ["collect"]
