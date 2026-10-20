"""Hetzner publication needs its own identity and complete successor recovery."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import base64
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


release = module("hetzner_release_test", "ops/release/hetzner_release.py")
health = module("hetzner_health_test", "ops/release/source_degradation.py")
catalog = module("hetzner_catalog_test", "ops/release/verify_catalog_publication.py")
full = module("hetzner_full_test", "ops/railway-automation/full-publisher/publish.py")
NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
SOURCE = "a" * 40
CODE = {"commit": SOURCE, "tree": "b" * 40}
RUNTIME = "1" * 64
UUID = "12345678-1234-1234-1234-123456789012"


def at(minutes):
    return (NOW + timedelta(minutes=minutes)).isoformat()


def chain():
    grant = {
        "schema": "seiche.hetzner-runtime-grant.v1", "provider": "hetzner", "authority": "production",
        "runtime_id": RUNTIME, "issued_at": at(-240), "expires_at": at(-180), "code": CODE,
        "transfer_sha256": "2" * 64, "restore_sha256": "3" * 64, "edge_token_sha256": "4" * 64,
        "host": {"identity": "5" * 64, "volume_uuid": UUID, "image_digest": "sha256:" + "6" * 64},
        "data": {"generation_root": "/volume/final/generation", "database_name": "seiche_final",
                 "postgres_socket": "/volume/final/postgres/socket", "postgres_port": 5432,
                 "postgres_role": "seiche_runtime"}, "agent_room": {"expected_key_id": "unprovisioned"},
    }
    envelope = {"schema": "seiche.hetzner-runtime-approval.v1", "payload": grant, "signature": "fixture"}
    grant_sha = release.digest(envelope)
    common = {"status": "PASS", "runtime_id": RUNTIME, "code": CODE, "grant_sha256": grant_sha}
    authority = {"schema": "seiche.hetzner-runtime-acceptance.v1", "runtime_id": RUNTIME,
                 "grant_sha256": grant_sha, "transfer_sha256": grant["transfer_sha256"],
                 "restore_sha256": grant["restore_sha256"], "code": CODE, "accepted_at": at(-210)}
    handoff = {"producer_sha": SOURCE, "activation_token": "7" * 64}
    socket = {"path": "/run/seiche-hetzner-aaaaaaaa-" + RUNTIME[:12] + "/api.sock",
              "uid": 10001, "gid": 987, "mode": "0660"}
    service = {"unit": "seiche-hetzner-" + RUNTIME[:16] + ".service", "unit_sha256": "8" * 64,
               "active": True, "docker_restart": "no"}
    accepted = {**common, "schema": "seiche.hetzner-production-release.v1", "container_id": "9" * 64,
                "authority": "production", "public_origin_changed": False, "active_handoff": handoff,
                "accepted_at": at(-180), "socket": socket, "systemd": service}
    pg = {"container_id": "a" * 64, "image_id": "sha256:" + "b" * 64,
          "application_admin_login_denied": True, "memory_bytes": 3 * release.GIB // 2,
          "nano_cpus": 1000000000, "docker_restart": "no", "host_pid": 99,
          "started_at": at(-220), "postmaster_started_at": at(-219),
          "database_identity": {"system_identifier": "123456789", "database_name": "seiche_final",
                                "role": "postgres", "port": 5432, "data_directory": "/restore/data", "server_version_num": 180000},
          "systemd": {"unit": "seiche-hetzner-" + RUNTIME[:16] + "-postgres.service",
                      "unit_sha256": "c" * 64, "active": True, "docker_restart": "no"}}
    accepted["postgres"] = pg
    boot = {"schema": "seiche.hetzner-boot-enabled.v1", "status": "PASS", "runtime_id": RUNTIME,
            "container_id": accepted["container_id"], "release_accepted_sha256": release.digest(accepted),
            "unit": service["unit"], "unit_sha256": service["unit_sha256"], "enabled_at": at(-179)}
    boot["postgres"] = {key: pg[key] for key in ("container_id", "image_id", "database_identity", "docker_restart")}
    boot["postgres"].update(unit=pg["systemd"]["unit"], unit_sha256=pg["systemd"]["unit_sha256"])
    restart = {**common, "schema": "seiche.hetzner-restart-release.v1", "container_id": accepted["container_id"],
               "active_handoff": handoff, "release_accepted_sha256": release.digest(accepted),
               "boot_enabled_sha256": release.digest(boot), "restart_rehearsal_proven": True,
               "before_started_at": at(-185), "after_started_at": at(-178),
               "before_host_pid": 100, "after_host_pid": 200, "socket": socket, "systemd": service,
               "observed_at": at(-170), "processes": {"supervisor_lock_held": True,
               "roles": {"supervisor": [{"pid": 1, "parent": 0}],
                         **{key: [{"pid": pid, "parent": 1}]
                            for pid, key in enumerate(("api", "source", "market"), start=2)}},
               "heartbeats": {key: {"heartbeat_at": at(-172), "expected_by": at(-160)}
                              for key in ("official-market-collector", "legacy-source-worker")}}}
    restart["postgres"] = {key: pg[key] for key in ("container_id", "image_id", "database_identity", "systemd")}
    restart["postgres"].update(before_started_at=at(-220), after_started_at=at(-178.5), before_host_pid=99,
                              after_host_pid=199, before_postmaster_started_at=at(-219),
                              after_postmaster_started_at=at(-178.4), restart_proven=True)
    audits = {"nbs": "not_onboarded", "agent_room": {
        "schema": "seiche.agent-room.restore-audit.v1", "result": "absent_uninitialized",
        "server_key_id": None, "participant_count": 0, "room_count": 0, "event_count": 0,
        "state_sha256": None, "non_executable": True, "execution_authority": "none"},
        "palimpsest_china": {"audit_schema": "seiche.palimpsest-china-activation-state.v1",
                            "tree_sha256": "a" * 64, "active_activation_id": None,
                            "pending_candidate_activation_id": None}}
    capture = {**common, "schema": release.CAPTURE_SCHEMA, "backup_id": "b" * 64,
               "host": grant["host"], "release_sha256": release.digest(accepted), "database_name": "seiche_final",
               "postgres_major": 18, "active_handoff": handoff,
               "members": {name: {"bytes": 1024, "sha256": "c" * 64} for name in release.MEMBERS},
               "database_manifest_sha256": "d" * 64, "filesystem_manifest_sha256": "e" * 64,
               "audits": audits, "timing": {"writers_stopped_at": at(-165), "started_at": at(-160),
                                           "completed_at": at(-150), "writers_resumed_at": at(-145)},
               "writers_drained": True, "authority_unchanged": True, "counts": [1, 2, 3, 4]}
    offsite = {**common, "schema": release.OFFSITE_SCHEMA, "backup_id": capture["backup_id"],
               "capture_sha256": release.digest(capture), "sealed_at": at(-120), "authority_unchanged": True,
               "encryption": {"format": "SSE-C-AES256", "key_id": "f" * 64, "customer_key_verified": True},
               "object": {"endpoint": "https://example.invalid", "bucket": "fixture-bucket",
                          "key": f"seiche/hetzner-successor/v1/{RUNTIME}/{capture['backup_id']}/backup.tar",
                          "version_id": "observed-version-1", "bytes": 10000, "sha256": "f" * 64,
                          "downloaded_sha256": "f" * 64, "created_at": at(-121),
                          "retain_until": at(90 * 24 * 60),
                          "object_lock_mode": "COMPLIANCE", "head_verified": True}}
    restore = {**common, "schema": release.RESTORE_SCHEMA, "backup_id": capture["backup_id"],
               "capture_sha256": release.digest(capture), "offsite_sha256": release.digest(offsite),
               "offsite_version_id": offsite["object"]["version_id"], "input_object_sha256": "f" * 64,
               "executor": {"provider": "local", "host_identity": "a" * 64, "volume_uuid": UUID,
                            "runtime_image": grant["host"]["image_digest"], "postgres_image": "sha256:" + "c" * 64},
               "isolated": True, "production_paths_mounted": False, "network": "none",
               "comparison": {key: capture[key] for key in ("database_manifest_sha256", "filesystem_manifest_sha256")},
               "audits": audits, "active_handoff": handoff, "started_at": at(-110), "completed_at": at(-10),
               "authority_unchanged": True}
    observation = {"identity": {"provider": "hetzner", "source_sha": SOURCE, "runtime_id": RUNTIME,
                                "authority": "production", "cache_control": "no-store"},
                   "body": {"provider": "hetzner", "authority": "production", "release_sha": SOURCE,
                            "snapshot_producer_sha": SOURCE, "generated_at": at(-2), "version": "0.16.6 estuary",
                            "faults": [{"source": "cbuae_donia", "status": "FAILED", "category": "HTTP_ERROR",
                                        "detail": health.HTTP_DETAIL}],
                            "provenance": [{"source": "cbuae_donia", "observed_at": at(-180)}]}}
    monitor = {**common, "schema": release.MONITOR_SCHEMA,
               "capture_sha256": release.digest(capture), "offsite_sha256": release.digest(offsite),
               "restore_sha256": release.digest(restore), "observed_at": at(-1), "runtime_current": True,
               "strict_rebuilt": True, "worker_heartbeats_current": True, "single_supervisor": True,
               "offsite_retention_current": True, "source_retired": True,
               "health": {"origin": deepcopy(observation), "public": deepcopy(observation)}}
    return dict(grant=envelope, acceptance=authority, release=accepted, boot=boot, restart=restart,
                capture=capture, offsite=offsite, restore=restore, monitor=monitor)


@pytest.fixture
def verified(monkeypatch):
    monkeypatch.setattr(release, "verify_grant_signature", lambda *args: None)
    monkeypatch.setattr(health, "verify_owner_signature", lambda *args, **kwargs: None)
    monkeypatch.setattr(catalog, "_source_health_gate", lambda: health)
    monkeypatch.setattr(catalog, "_hetzner_release_gate", lambda: release)


def package_for(evidence):
    accepted = release.validate_evidence(evidence, source=SOURCE, now=NOW)
    pair = evidence["monitor"]["health"]
    context = {"purpose": "catalog_metadata", "intent_id": "2" * 64,
               "controller_source_sha": SOURCE, "runtime_source_sha": SOURCE,
               "provider": "hetzner", "runtime_id": RUNTIME, "target": accepted["target"],
               "expected_version": "0.16.6", "expected_authority": "production",
               "operation": {"publication_request_id": "3" * 64, "catalog_sha256": "4" * 64,
                             "health_pair_sha256": health.digest(health.canonical(pair))}}
    faults = health.normalized_faults(pair["origin"]["body"]["faults"])
    admission = {"schema": health.HETZNER_SCHEMA, "context": context, "issued_at": at(-1), "expires_at": at(60),
                 "faults": faults, "faults_sha256": health.digest(health.canonical(faults)), "limits": health.LIMITS}
    package = {"schema": release.PACKAGE_SCHEMA, "intent_id": context["intent_id"],
               "publication_request_id": context["operation"]["publication_request_id"],
               "body_b64": base64.b64encode(health.canonical(admission)).decode(),
               "signature_b64": base64.b64encode(b"fixture-signature").decode(),
               "health_pair": pair, "runtime_evidence": evidence}
    return context, admission, package


def test_real_hetzner_identity_survives_without_railway_fields(verified):
    evidence = chain()
    before = deepcopy(evidence)
    context, admission, package = package_for(evidence)
    result = catalog.verify_catalog_source_health(package, expected_context=context,
            current_public=evidence["monitor"]["health"]["public"], now=NOW)
    assert result["evidence_status"] == "degraded" and result["source_health_admitted"]
    assert result["claim_document"]["schema"] == health.HETZNER_CLAIM_SCHEMA
    assert result["context"]["provider"] == "hetzner" and "deployment_id" not in result["context"]
    assert evidence == before


def test_healthy_hetzner_still_consumes_complete_recovery_claim_without_fault_exception(verified):
    evidence = chain()
    for observation in evidence["monitor"]["health"].values():
        observation["body"]["faults"] = []
    # Construct context from the completed recovery first, then a no-exception
    # package; no unsigned degradation may use this path.
    accepted = release.validate_evidence(evidence, source=SOURCE, now=NOW)
    with_fault = chain()
    context, _, package = package_for(with_fault)
    context["target"] = accepted["target"]
    package.update(runtime_evidence=evidence, health_pair=evidence["monitor"]["health"],
                   body_b64=None, signature_b64=None)
    context["operation"]["health_pair_sha256"] = health.digest(health.canonical(package["health_pair"]))
    result = catalog.verify_catalog_source_health(package, expected_context=context,
        current_public=package["health_pair"]["public"], now=NOW)
    assert result["evidence_status"] == "healthy" and result["source_health_admitted"] is False
    assert result["claim_document"]["schema"] == "seiche.hetzner-healthy-publication-claim.v1"
    for observation in evidence["monitor"]["health"].values():
        observation["body"]["faults"] = with_fault["monitor"]["health"]["origin"]["body"]["faults"]
    with pytest.raises(catalog.PublicationGateError):
        catalog.verify_catalog_source_health(package, expected_context=context,
            current_public=package["health_pair"]["public"], now=NOW)


@pytest.mark.parametrize("document,field,value", [
    ("capture", "schema", "seiche.railway-recovery-export-receipt.v4"),
    ("capture", "writers_drained", False), ("capture", "authority_unchanged", False),
    ("capture", "database_manifest_sha256", None),
    ("restore", "production_paths_mounted", True), ("restore", "network", "bridge"),
    ("restore", "offsite_version_id", "different-version"),
    ("offsite", "capture_sha256", "0" * 64),
    ("monitor", "source_retired", False), ("monitor", "strict_rebuilt", False),
    ("monitor", "observed_at", at(-20)), ("restart", "restart_rehearsal_proven", False),
    ("boot", "unit_sha256", "0" * 64),
])
def test_missing_or_foreign_recovery_stage_cannot_pass(verified, document, field, value):
    evidence = chain()
    evidence[document][field] = value
    with pytest.raises(release.EvidenceError):
        release.validate_evidence(evidence, source=SOURCE, now=NOW)


def test_offsite_requires_exact_immutable_version_download_and_ninety_days(verified):
    for key, value in (("downloaded_sha256", "0" * 64), ("object_lock_mode", "GOVERNANCE"),
                       ("version_id", ""), ("retain_until", at(30 * 24 * 60)), ("head_verified", False)):
        evidence = chain()
        evidence["offsite"]["object"][key] = value
        with pytest.raises(release.EvidenceError):
            release.validate_evidence(evidence, source=SOURCE, now=NOW)


def test_capture_requires_closed_members_agent_room_and_palimpsest(verified):
    evidence = chain()
    del evidence["capture"]["members"]["api-data.tgz"]
    with pytest.raises(release.EvidenceError, match="members"):
        release.validate_evidence(evidence, source=SOURCE, now=NOW)
    evidence = chain()
    evidence["capture"]["audits"]["agent_room"]["execution_authority"] = "trading"
    with pytest.raises(release.EvidenceError, match="agent_room"):
        release.validate_evidence(evidence, source=SOURCE, now=NOW)
    evidence = chain()
    evidence["capture"]["audits"]["palimpsest_china"]["pending_candidate_activation_id"] = "0" * 64
    with pytest.raises(release.EvidenceError, match="palimpsest"):
        release.validate_evidence(evidence, source=SOURCE, now=NOW)


@pytest.mark.parametrize("document,field,value", [
    ("release", "memory_bytes", 4 * release.GIB),
    ("release", "docker_restart", "always"),
    ("boot", "unit_sha256", "0" * 64),
    ("restart", "restart_proven", False),
    ("restart", "after_started_at", at(-221)),
    ("restart", "after_postmaster_started_at", at(-177)),
])
def test_postgres_owner_cluster_resources_and_restart_are_required(verified, document, field, value):
    evidence = chain()
    evidence[document]["postgres"][field] = value
    with pytest.raises(release.EvidenceError):
        release.validate_evidence(evidence, source=SOURCE, now=NOW)


def test_full_package_is_private_healthy_and_never_accepts_fault_admission(tmp_path):
    path = tmp_path / "health.json"
    value = {"schema": release.PACKAGE_SCHEMA, "body_b64": None, "signature_b64": None,
             "health_pair": {key: {"body": {"faults": []}} for key in ("origin", "public")}}
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    assert full.healthy_hetzner_package(str(path)) == path.read_bytes()
    for mutate in (lambda v: v.update(body_b64="signed-exception"),
                   lambda v: v["health_pair"]["origin"]["body"].update(faults=[{"source": "fred"}]),
                   lambda v: v.update(schema="seiche.catalog-source-health.v1")):
        changed = deepcopy(value)
        mutate(changed)
        path.write_text(json.dumps(changed))
        with pytest.raises(RuntimeError):
            full.healthy_hetzner_package(str(path))
    path.write_text(json.dumps(value))
    path.chmod(0o644)
    with pytest.raises(RuntimeError, match="private"):
        full.healthy_hetzner_package(str(path))


def test_full_claim_is_durable_exact_and_rechecks_expiry(tmp_path):
    current = datetime.now(timezone.utc)
    assessment = {"claim_document": {"schema": "seiche.hetzner-healthy-publication-claim.v1",
                    "context": {"intent_id": "a" * 64}, "execution": {"publication_request_id": "b" * 64}},
                  "expires_at": (current + timedelta(minutes=5)).isoformat(),
                  "health_valid_until": (current + timedelta(minutes=4)).isoformat()}
    full.claim_healthy_hetzner(tmp_path, assessment)
    full.claim_healthy_hetzner(tmp_path, assessment)
    different = deepcopy(assessment)
    different["claim_document"]["execution"]["publication_request_id"] = "c" * 64
    with pytest.raises(RuntimeError, match="another publication"):
        full.claim_healthy_hetzner(tmp_path, different)
    assessment["expires_at"] = (current - timedelta(seconds=1)).isoformat()
    with pytest.raises(RuntimeError, match="expired"):
        full.claim_healthy_hetzner(tmp_path, assessment)


def test_hetzner_catalog_refuses_provider_mixing_equivalence_and_stale_workers(verified):
    evidence = chain()
    context, admission, package = package_for(evidence)
    for field, value in (("provider", "railway"), ("controller_source_sha", "b" * 40),
                         ("purpose", "application_handoff"), ("runtime_id", UUID)):
        with pytest.raises(health.AdmissionError):
            health.validate_context({**context, field: value})
    evidence["restart"]["processes"]["heartbeats"]["legacy-source-worker"]["heartbeat_at"] = at(-180)
    with pytest.raises(release.EvidenceError, match="heartbeat"):
        release.validate_evidence(evidence, source=SOURCE, now=NOW)


def test_health_identity_never_maps_hetzner_to_railway():
    headers = {"x-seiche-provider": "hetzner", "x-seiche-runtime": RUNTIME,
               "x-seiche-release-sha": SOURCE, "x-seiche-authority": "production", "cache-control": "no-store"}
    observed = catalog._health_identity(headers)
    assert observed["provider"] == "hetzner" and "deployment_id" not in observed
    headers["x-seiche-railway-deployment"] = UUID
    with pytest.raises(catalog.PublicationGateError, match="ambiguous"):
        catalog._health_identity(headers)


def test_capacity_requires_actual_scratch_reserve_and_serial_resources():
    plan = release.capacity_plan(free_bytes=21 * release.GIB, free_inodes=200000,
        reserve_bytes=10 * release.GIB, archive_bytes=3444304039, filesystem_bytes=11809160469,
        database_bytes=3546363583, api_stage_bytes=release.GIB,
        application_running=True, production_database_running=True)
    assert plan["capture_space_ready"] and not plan["restore_space_ready"]
    assert not plan["capture_resources_ready"] and not plan["restore_resources_ready"]
    assert plan["helper_cpus"] + plan["postgres_cpus"] == 2
    assert plan["helper_memory_bytes"] + plan["postgres_memory_bytes"] <= 4 * release.GIB


def test_hetzner_owner_namespace_cannot_replay_railway_admission(tmp_path, monkeypatch):
    key = tmp_path / "key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    monkeypatch.setattr(health, "OWNER_PUBLIC", key.with_suffix(".pub").read_text().strip())
    message = b"provider-specific-proof\n"
    signed = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", health.HETZNER_SIGNATURE_NAMESPACE],
                            input=message, capture_output=True, check=True).stdout
    health.verify_owner_signature(message, signed, namespace=health.HETZNER_SIGNATURE_NAMESPACE)
    with pytest.raises(health.AdmissionError, match="signature"):
        health.verify_owner_signature(message, signed)
    with pytest.raises(health.AdmissionError, match="signature"):
        health.verify_owner_signature(message + b"changed", signed, namespace=health.HETZNER_SIGNATURE_NAMESPACE)
