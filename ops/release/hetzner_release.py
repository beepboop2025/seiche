"""Provider-honest successor recovery and publication evidence contracts.

This verifier neither performs operations nor grants writer/source authority.
The root controller supplies protected observed evidence. Degraded publication
also binds its complete digest to an owner's scoped source-health signature.
Railway receipts are never translated.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE_SCHEMA = "seiche.hetzner-catalog-source-health.v1"
NATIVE_PACKAGE_SCHEMA = "seiche.native-migration-catalog-source-health.v1"
NATIVE_EVIDENCE_SCHEMA = "seiche.native-migration-publication-evidence.v1"
CAPTURE_SCHEMA = "seiche.hetzner-successor-capture.v1"
OFFSITE_SCHEMA = "seiche.hetzner-successor-offsite.v1"
RESTORE_SCHEMA = "seiche.hetzner-successor-restore.v1"
MONITOR_SCHEMA = "seiche.hetzner-successor-monitor.v1"
OWNER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBuJV6o8YL2XXR9q4vcwpHuc2z1GEBawSmrJWGrgwzFV"
MEMBERS = frozenset({"seiche.dump", "var-lib-seiche.tgz", "palimpsest-china.tgz",
                    "palimpsest-china-state.json", "api-data.tgz", "table-counts.txt",
                    "deployed-sha.txt", "manifest.env", "SHA256SUMS"})
GIB = 1024 ** 3


class EvidenceError(ValueError):
    pass


def require(condition, label):
    if not condition:
        raise EvidenceError(label)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def hexadecimal(value, length=64):
    return isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{length}}}", value) is not None


def closed(value, keys, label):
    require(isinstance(value, dict) and set(value) == set(keys), label)
    return value


def clock(value):
    require(isinstance(value, str) and len(value) <= 40, "invalid_clock")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise EvidenceError("invalid_clock") from None
    require(result.tzinfo is not None and result.utcoffset() == timedelta(0), "clock_not_utc")
    return result


def verify_grant_signature(payload, signature):
    require(isinstance(signature, str) and 0 < len(signature) <= 4096
            and signature.startswith("-----BEGIN SSH SIGNATURE-----\n")
            and signature.endswith("-----END SSH SIGNATURE-----\n"), "grant_signature_framing")
    with tempfile.TemporaryDirectory(prefix="hetzner-release-") as name:
        root = Path(name)
        (root / "signers").write_text("seiche-hetzner-runtime-release " + OWNER + "\n")
        (root / "signature").write_text(signature)
        result = subprocess.run([
            "/usr/bin/ssh-keygen", "-Y", "verify", "-f", str(root / "signers"),
            "-I", "seiche-hetzner-runtime-release", "-n", "seiche-hetzner-runtime-v1",
            "-s", str(root / "signature")], input=canonical(payload)[:-1], capture_output=True,
            timeout=15, check=False, env={"PATH": "/usr/bin:/bin", "HOME": name})
    require(result.returncode == 0, "grant_signature_invalid")


def validate_grant(envelope, *, source):
    closed(envelope, {"schema", "payload", "signature"}, "grant_envelope_shape")
    require(envelope["schema"] == "seiche.hetzner-runtime-approval.v1", "grant_provider_schema")
    grant = closed(envelope["payload"], {
        "schema", "provider", "authority", "runtime_id", "issued_at", "expires_at", "code",
        "transfer_sha256", "restore_sha256", "host", "data", "agent_room", "edge_token_sha256",
    }, "grant_shape")
    require(grant["schema"] == "seiche.hetzner-runtime-grant.v1" and grant["provider"] == "hetzner"
            and grant["authority"] == "production", "grant_authority")
    code = closed(grant["code"], {"commit", "tree"}, "grant_code_shape")
    require(code["commit"] == source and hexadecimal(source, 40) and hexadecimal(code["tree"], 40),
            "grant_source")
    for key in ("runtime_id", "transfer_sha256", "restore_sha256", "edge_token_sha256"):
        require(hexadecimal(grant[key]), "grant_digest")
    host = closed(grant["host"], {"identity", "volume_uuid", "image_digest"}, "grant_host_shape")
    require(hexadecimal(host["identity"])
            and isinstance(host["volume_uuid"], str)
            and re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", host["volume_uuid"])
            and isinstance(host["image_digest"], str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", host["image_digest"]), "grant_host_identity")
    data = closed(grant["data"], {"generation_root", "database_name", "postgres_socket",
                                  "postgres_port", "postgres_role"}, "grant_data_shape")
    require(all(isinstance(data[key], str) and data[key].startswith("/")
                and ".." not in data[key].split("/") for key in ("generation_root", "postgres_socket"))
            and isinstance(data["database_name"], str)
            and re.fullmatch(r"seiche_[a-z0-9_]{1,55}", data["database_name"])
            and type(data["postgres_port"]) is int and 1 <= data["postgres_port"] <= 65535
            and data["postgres_role"] == "seiche_runtime", "grant_data_identity")
    room = closed(grant["agent_room"], {"expected_key_id"}, "grant_room_shape")
    require(room["expected_key_id"] == "unprovisioned" or hexadecimal(room["expected_key_id"]), "grant_room_key")
    start, end = clock(grant["issued_at"]), clock(grant["expires_at"])
    require(start < end <= start + timedelta(hours=1), "grant_lifetime")
    verify_grant_signature(grant, envelope["signature"])
    return grant


def identity(value, schema, grant, grant_sha):
    require(value.get("schema") == schema and value.get("status") == "PASS"
            and value.get("runtime_id") == grant["runtime_id"]
            and value.get("code") == grant["code"] and value.get("grant_sha256") == grant_sha,
            "receipt_runtime_identity")


def validate_capture(value, grant, release, grant_sha, *, now):
    closed(value, {"schema", "status", "backup_id", "runtime_id", "code", "grant_sha256", "host",
                   "release_sha256", "database_name", "postgres_major", "active_handoff", "members",
                   "database_manifest_sha256", "filesystem_manifest_sha256", "audits", "timing",
                   "writers_drained", "authority_unchanged", "counts"}, "capture_shape")
    identity(value, CAPTURE_SCHEMA, grant, grant_sha)
    require(hexadecimal(value["backup_id"]) and value["host"] == grant["host"]
            and value["release_sha256"] == digest(release)
            and value["database_name"] == grant["data"]["database_name"]
            and value["postgres_major"] == 18 and value["active_handoff"] == release["active_handoff"]
            and value["writers_drained"] is True and value["authority_unchanged"] is True,
            "capture_source_binding")
    require(all(hexadecimal(value[key]) for key in ("database_manifest_sha256", "filesystem_manifest_sha256")),
            "capture_complete_manifest_missing")
    closed(value["members"], MEMBERS, "capture_members_not_closed")
    for item in value["members"].values():
        closed(item, {"sha256", "bytes"}, "capture_member_shape")
        require(hexadecimal(item["sha256"]) and type(item["bytes"]) is int and 0 < item["bytes"] <= 5 * GIB,
                "capture_member_invalid")
    require(sum(item["bytes"] for item in value["members"].values()) <= 30 * GIB, "capture_capacity_exceeded")
    counts = value["counts"]
    require(isinstance(counts, list) and len(counts) == 4 and all(type(n) is int and n >= 0 for n in counts),
            "capture_counts_invalid")
    audits = closed(value["audits"], {"nbs", "agent_room", "palimpsest_china"}, "capture_audits_shape")
    require(audits["nbs"] in {"not_onboarded", "verified_head"}
            and isinstance(audits["agent_room"], dict) and isinstance(audits["palimpsest_china"], dict),
            "capture_audits_missing")
    room = closed(audits["agent_room"], {"schema", "result", "server_key_id", "participant_count",
                  "room_count", "event_count", "state_sha256", "non_executable", "execution_authority"},
                  "agent_room_audit_shape")
    require(room["schema"] == "seiche.agent-room.restore-audit.v1"
            and room["result"] in {"verified", "absent_uninitialized"}
            and room["non_executable"] is True and room["execution_authority"] == "none",
            "agent_room_audit_policy")
    counts = tuple(room[key] for key in ("participant_count", "room_count", "event_count"))
    require(all(type(n) is int and 0 <= n <= 2000000 for n in counts)
            and counts[2] <= counts[1] * 4096 and (not counts[1] or counts[0]), "agent_room_counts")
    if room["result"] == "absent_uninitialized":
        require(counts == (0, 0, 0) and room["state_sha256"] is None
                and (room["server_key_id"] is None or hexadecimal(room["server_key_id"])), "agent_room_absence")
    else:
        require(hexadecimal(room["server_key_id"]) and hexadecimal(room["state_sha256"]), "agent_room_state")
    china = closed(audits["palimpsest_china"], {"audit_schema", "tree_sha256", "active_activation_id",
                   "pending_candidate_activation_id"}, "palimpsest_audit_shape")
    require(china["audit_schema"] == "seiche.palimpsest-china-activation-state.v1"
            and hexadecimal(china["tree_sha256"]) and china["pending_candidate_activation_id"] is None
            and (china["active_activation_id"] is None or hexadecimal(china["active_activation_id"])),
            "palimpsest_audit_state")
    expected_key = grant["agent_room"]["expected_key_id"]
    require((audits["agent_room"].get("server_key_id") or "unprovisioned") == expected_key,
            "capture_agent_room_key_changed")
    timing = closed(value["timing"], {"writers_stopped_at", "started_at", "completed_at", "writers_resumed_at"},
                    "capture_timing_shape")
    stopped, start, end, resumed = (clock(timing[key]) for key in
                                  ("writers_stopped_at", "started_at", "completed_at", "writers_resumed_at"))
    require(clock(release["accepted_at"]) <= stopped <= start <= end <= resumed <= now + timedelta(minutes=5)
            and now - end <= timedelta(hours=26), "capture_timing_invalid_or_stale")


def validate_offsite(value, capture, grant, grant_sha, *, now):
    closed(value, {"schema", "status", "backup_id", "runtime_id", "code", "grant_sha256", "capture_sha256",
                   "object", "encryption", "sealed_at", "authority_unchanged"}, "offsite_shape")
    identity(value, OFFSITE_SCHEMA, grant, grant_sha)
    require(value["backup_id"] == capture["backup_id"] and value["capture_sha256"] == digest(capture)
            and value["authority_unchanged"] is True, "offsite_capture_binding")
    encryption = closed(value["encryption"], {"format", "key_id", "customer_key_verified"}, "offsite_encryption_shape")
    require(encryption["format"] == "SSE-C-AES256" and encryption["customer_key_verified"] is True
            and hexadecimal(encryption["key_id"]), "offsite_encryption")
    item = closed(value["object"], {"endpoint", "bucket", "key", "version_id", "bytes", "sha256",
                                    "downloaded_sha256", "created_at", "retain_until", "object_lock_mode", "head_verified"},
                  "offsite_object_shape")
    require(isinstance(item["endpoint"], str) and re.fullmatch(r"https://[a-z0-9.-]+", item["endpoint"])
            and isinstance(item["bucket"], str) and re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,62}", item["bucket"])
            and item["key"] == f"seiche/hetzner-successor/v1/{grant['runtime_id']}/{capture['backup_id']}/backup.tar"
            and isinstance(item["version_id"], str) and 0 < len(item["version_id"]) <= 1024
            and item["version_id"] != "null" and re.fullmatch(r"[A-Za-z0-9._~+/=-]+", item["version_id"])
            and type(item["bytes"]) is int and 0 < item["bytes"] <= 5 * GIB
            and hexadecimal(item["sha256"]) and item["downloaded_sha256"] == item["sha256"]
            and item["object_lock_mode"] == "COMPLIANCE" and item["head_verified"] is True,
            "offsite_version_or_readback_invalid")
    sealed, retained = clock(value["sealed_at"]), clock(item["retain_until"])
    require(clock(capture["timing"]["writers_resumed_at"]) <= sealed <= now + timedelta(minutes=5)
            and clock(capture["timing"]["writers_resumed_at"]) <= clock(item["created_at"]) <= sealed
            and retained >= clock(item["created_at"]) + timedelta(days=90) and retained > now,
            "offsite_retention_invalid")


def validate_restore(value, capture, offsite, grant, grant_sha, *, now):
    closed(value, {"schema", "status", "backup_id", "runtime_id", "code", "grant_sha256",
                   "capture_sha256", "offsite_sha256", "offsite_version_id", "input_object_sha256",
                   "executor", "isolated", "production_paths_mounted", "network", "comparison", "audits",
                   "active_handoff", "started_at", "completed_at", "authority_unchanged"}, "restore_shape")
    identity(value, RESTORE_SCHEMA, grant, grant_sha)
    require(value["backup_id"] == capture["backup_id"] and value["capture_sha256"] == digest(capture)
            and value["offsite_sha256"] == digest(offsite)
            and value["offsite_version_id"] == offsite["object"]["version_id"]
            and value["input_object_sha256"] == offsite["object"]["sha256"]
            and value["isolated"] is True and value["production_paths_mounted"] is False
            and value["network"] == "none" and value["authority_unchanged"] is True,
            "restore_source_or_isolation_invalid")
    executor = closed(value["executor"], {"provider", "host_identity", "volume_uuid", "runtime_image",
                                          "postgres_image"}, "restore_executor_shape")
    require(executor["provider"] in {"local", "hetzner"} and hexadecimal(executor["host_identity"])
            and isinstance(executor["volume_uuid"], str)
            and re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", executor["volume_uuid"])
            and all(isinstance(executor[key], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", executor[key])
                    for key in ("runtime_image", "postgres_image")), "restore_executor_identity")
    require(executor["host_identity"] != grant["host"]["identity"]
            and executor["runtime_image"] == grant["host"]["image_digest"], "restore_executor_not_independent_or_image_differs")
    require(value["comparison"] == {key: capture[key] for key in
                                    ("database_manifest_sha256", "filesystem_manifest_sha256")}
            and value["audits"] == capture["audits"] and value["active_handoff"] == capture["active_handoff"],
            "restore_complete_comparison_differs")
    require(clock(offsite["sealed_at"]) <= clock(value["started_at"]) <= clock(value["completed_at"])
            <= now + timedelta(minutes=5), "restore_timing_invalid")


def validate_postgres(release, boot, restart, grant, *, native=False):
    database = closed(release.get("postgres"), {"container_id", "image_id", "application_admin_login_denied",
        "memory_bytes", "nano_cpus", "docker_restart", "database_identity", "started_at", "host_pid",
        "postmaster_started_at", "systemd"}, "release_postgres_shape")
    require(hexadecimal(database["container_id"]) and isinstance(database["image_id"], str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", database["image_id"])
            and database["application_admin_login_denied"] is True and database["memory_bytes"] == 3 * GIB // 2
            and database["nano_cpus"] == 1000000000 and database["docker_restart"] == "no",
            "postgres_identity_resources_or_administrator_access")
    identity = closed(database["database_identity"], {"system_identifier", "database_name", "role", "port",
                       "data_directory", "server_version_num"}, "postgres_cluster_identity_shape")
    require(isinstance(identity["system_identifier"], str) and re.fullmatch(r"[0-9]{1,24}", identity["system_identifier"])
            and identity["database_name"] == grant["data"]["database_name"] and identity["role"] == "postgres"
            and identity["port"] == 5432 and identity["data_directory"] == "/restore/data"
            and type(identity["server_version_num"]) is int and 180000 <= identity["server_version_num"] < 190000,
            "postgres_cluster_identity")
    service = database["systemd"]
    require(service.get("unit") == ("seiche-native-postgres.service" if native else
            "seiche-hetzner-" + grant["runtime_id"][:16] + "-postgres.service")
            and hexadecimal(service.get("unit_sha256")) and service.get("active") is True
            and service.get("docker_restart") == "no", "postgres_single_supervisor")
    expected_boot = {key: database[key] for key in ("container_id", "image_id", "database_identity", "docker_restart")}
    expected_boot.update(unit=service["unit"], unit_sha256=service["unit_sha256"])
    require(boot.get("postgres") == expected_boot, "postgres_boot_binding")
    restarted = closed(restart.get("postgres"), {"container_id", "image_id", "database_identity",
        "before_started_at", "after_started_at", "before_host_pid", "after_host_pid",
        "before_postmaster_started_at", "after_postmaster_started_at", "systemd", "restart_proven"},
        "postgres_restart_shape")
    require(all(restarted[key] == database[key] for key in ("container_id", "image_id", "database_identity", "systemd"))
            and restarted["restart_proven"] is True
            and type(restarted["before_host_pid"]) is int and type(restarted["after_host_pid"]) is int
            and 0 < restarted["before_host_pid"] != restarted["after_host_pid"] > 0,
            "postgres_restart_identity")
    require(clock(database["started_at"]) <= clock(database["postmaster_started_at"]) <= clock(release["accepted_at"])
            and clock(restarted["before_started_at"]) < clock(restarted["after_started_at"])
            <= clock(restarted["after_postmaster_started_at"]) <= clock(restart["after_started_at"])
            and clock(restarted["before_postmaster_started_at"]) < clock(restarted["after_postmaster_started_at"]),
            "postgres_restart_order")


def validate_restart_handoff(restart, release, *, source, native=False):
    """An accepted release may refresh its snapshot after the service restarts."""
    if restart.get("schema") == "seiche.hetzner-restart-release.v1":
        require(restart.get("active_handoff") == release["active_handoff"], "restart_handoff_binding")
        return
    require(native and restart.get("schema") == "seiche.hetzner-restart-release.v2",
            "restart_refresh_requires_native_v2")
    require(restart.get("active_handoff_preserved") is False
            and restart.get("release_authority_preserved") is True
            and restart.get("before_active_handoff") == release["active_handoff"]
            and restart.get("active_handoff") != restart["before_active_handoff"], "restart_refresh_transition")
    proof = closed(restart.get("same_release_refresh"), {"schema", "status", "producer_sha",
        "transaction_read_only", "repeatable_read", "operator_promotion_performed", "before", "after"},
        "restart_refresh_proof_shape")
    require(proof["schema"] == "seiche.same-release-refresh-proof.v1" and proof["status"] == "PASS"
            and proof["producer_sha"] == source and proof["transaction_read_only"] is True
            and proof["repeatable_read"] is True and proof["operator_promotion_performed"] is False,
            "restart_refresh_observer")
    for side, expected in (("before", restart["before_active_handoff"]), ("after", restart["active_handoff"])):
        record = closed(proof[side], {"identity", "generated_at", "envelope_sha256", "payload_sha256",
            "products", "full_forward_chain_verified", "canonical_rows_match_validated_staging"},
            "restart_refresh_record_shape")
        handoff = closed(record["identity"], {"producer_sha", "activation_token"}, "restart_refresh_handoff_shape")
        require(handoff == expected and handoff["producer_sha"] == source and hexadecimal(handoff["activation_token"])
                and hexadecimal(record["envelope_sha256"]) and hexadecimal(record["payload_sha256"])
                and record["full_forward_chain_verified"] is True
                and record["canonical_rows_match_validated_staging"] is True, "restart_refresh_chain")
        products = closed(record["products"], {"gauge", "overview"}, "restart_refresh_product_set")
        for value in products.values():
            closed(value, {"forward_record_id", "snapshot_id", "snapshot_row_sha256"}, "restart_refresh_product_shape")
            require(all(hexadecimal(v) for v in value.values()), "restart_refresh_product_hash")
    require(clock(proof["before"]["generated_at"]) < clock(restart["after_started_at"])
            <= clock(proof["after"]["generated_at"]) <= clock(restart["observed_at"]), "restart_refresh_clock")


def validate_runtime_chain(evidence, *, source, now, native=False):
    grant = validate_grant(evidence["grant"], source=source)
    grant_sha = digest(evidence["grant"])
    acceptance = closed(evidence["acceptance"], {"schema", "runtime_id", "grant_sha256", "transfer_sha256",
                                                "restore_sha256", "code", "accepted_at"}, "acceptance_shape")
    require(acceptance["schema"] == "seiche.hetzner-runtime-acceptance.v1"
            and acceptance["runtime_id"] == grant["runtime_id"] and acceptance["grant_sha256"] == grant_sha
            and acceptance["code"] == grant["code"]
            and all(acceptance[key] == grant[key] for key in ("transfer_sha256", "restore_sha256"))
            and clock(grant["issued_at"]) <= clock(acceptance["accepted_at"]) <= clock(grant["expires_at"]),
            "accepted_authority_binding")
    release, boot, restart = (evidence[key] for key in ("release", "boot", "restart"))
    identity(release, "seiche.hetzner-production-release.v1", grant, grant_sha)
    require(clock(acceptance["accepted_at"]) <= clock(release.get("accepted_at")) <= now,
            "release_acceptance_clock")
    require(hexadecimal(release.get("container_id"))
            and release.get("authority") == "production" and release.get("public_origin_changed") is False
            and release.get("active_handoff", {}).get("producer_sha") == source
            and hexadecimal(release["active_handoff"].get("activation_token")), "release_handoff_invalid")
    socket = release.get("socket", {})
    if native:
        require(socket == {"path": "/run/seiche-native/http.sock", "uid": 10001, "gid": 10001,
            "mode": "0666", "directory_acl": "user::rwx,user:999:--x,group::---,mask::--x,other::---",
            "socket_acl": "user::rw-,user:999:rw-,group::---,mask::rw-,other::rw-",
            "caddy_uid": 999, "caddy_connect_verified": True, "unrelated_uid_denied": True},
            "native_release_socket_acl_identity")
    else:
        require(isinstance(socket.get("path"), str) and socket["path"].startswith("/run/seiche-hetzner-")
                and socket["path"].endswith(grant["runtime_id"][:12] + "/api.sock")
                and socket.get("uid") == 10001 and type(socket.get("gid")) is int
                and socket["gid"] not in {0, 10001} and socket.get("mode") == "0660", "release_socket_identity")
    service = release.get("systemd", {})
    require(service.get("unit") == ("seiche-native.service" if native else
            "seiche-hetzner-" + grant["runtime_id"][:16] + ".service")
            and hexadecimal(service.get("unit_sha256")) and service.get("active") is True
            and service.get("docker_restart") == "no", "release_supervisor_identity")
    require(boot.get("schema") == "seiche.hetzner-boot-enabled.v1" and boot.get("status") == "PASS"
            and boot.get("runtime_id") == grant["runtime_id"] and boot.get("container_id") == release["container_id"]
            and boot.get("release_accepted_sha256") == digest(release)
            and boot.get("unit_sha256") == service["unit_sha256"] and boot.get("unit") == service["unit"], "boot_binding")
    restart_schema = restart.get("schema")
    require(restart_schema == "seiche.hetzner-restart-release.v1" or
            (native and restart_schema == "seiche.hetzner-restart-release.v2"), "restart_schema")
    identity(restart, restart_schema, grant, grant_sha)
    validate_restart_handoff(restart, release, source=source, native=native)
    require(restart.get("container_id") == release["container_id"]
            and restart.get("release_accepted_sha256") == digest(release)
            and restart.get("boot_enabled_sha256") == digest(boot)
            and restart.get("restart_rehearsal_proven") is True
            and restart.get("before_started_at") != restart.get("after_started_at")
            and restart.get("before_host_pid") != restart.get("after_host_pid")
            and restart.get("processes", {}).get("supervisor_lock_held") is True
            and restart.get("socket") == socket and restart.get("systemd") == service,
            "restart_binding")
    roles = restart["processes"].get("roles", {})
    if native:
        require(set(roles) == {"supervisor", "api", "market", "source"}
                and all(isinstance(row, dict) and type(row.get("pid")) is int and row["pid"] > 0
                        for row in roles.values())
                and all(roles[key].get("ppid") == roles["supervisor"]["pid"]
                        for key in ("api", "market", "source"))
                and len({row["pid"] for row in roles.values()}) == 4, "native_restart_process_tree")
    else:
        require(set(roles) == {"supervisor", "api", "market", "source"}
                and roles["supervisor"] == [{"pid": 1, "parent": 0}]
                and all(len(roles[key]) == 1 and roles[key][0]["parent"] == 1 for key in ("api", "market", "source")),
                "restart_process_tree")
    heartbeat = restart["processes"].get("heartbeats", {})
    require(set(heartbeat) == {"official-market-collector", "legacy-source-worker"}, "restart_heartbeat_set")
    restart_at = clock(restart.get("observed_at"))
    require(clock(release["accepted_at"]) <= clock(boot["enabled_at"])
            <= clock(restart["after_started_at"]) <= restart_at <= now, "restart_clock")
    for item in heartbeat.values():
        require(clock(restart["after_started_at"]) <= clock(item["heartbeat_at"]) <= restart_at
                <= clock(item["expected_by"]), "restart_heartbeat_clock")
    validate_postgres(release, boot, restart, grant, native=native)
    return grant, grant_sha, release, boot, restart


def validate_evidence(evidence, *, source, now):
    """Check explicit provider evidence; legacy protected retention is unchanged."""
    if isinstance(evidence, dict) and evidence.get("schema") == NATIVE_EVIDENCE_SCHEMA:
        return validate_native_evidence(evidence, source=source, now=now)
    closed(evidence, {"grant", "acceptance", "release", "boot", "restart", "capture", "offsite", "restore", "monitor"},
           "recovery_chain_not_closed")
    require(now.tzinfo is not None and len(canonical(evidence)) <= 1024 * 1024, "recovery_chain_bound")
    grant, grant_sha, release, boot, restart = validate_runtime_chain(evidence, source=source, now=now)
    restart_at = clock(restart["observed_at"])
    capture, offsite, restore, monitor = (evidence[key] for key in ("capture", "offsite", "restore", "monitor"))
    validate_capture(capture, grant, release, grant_sha, now=now)
    require(restart_at <= clock(capture["timing"]["writers_stopped_at"]), "capture_precedes_restart_qualification")
    validate_offsite(offsite, capture, grant, grant_sha, now=now)
    validate_restore(restore, capture, offsite, grant, grant_sha, now=now)
    closed(monitor, {"schema", "status", "runtime_id", "code", "grant_sha256", "capture_sha256", "offsite_sha256",
                     "restore_sha256", "observed_at", "runtime_current", "strict_rebuilt", "worker_heartbeats_current",
                     "single_supervisor", "offsite_retention_current", "source_retired", "health"}, "monitor_shape")
    identity(monitor, MONITOR_SCHEMA, grant, grant_sha)
    require(all(monitor[key + "_sha256"] == digest(evidence[key]) for key in ("capture", "offsite", "restore"))
            and all(monitor[key] is True for key in ("runtime_current", "strict_rebuilt", "worker_heartbeats_current",
                     "single_supervisor", "offsite_retention_current", "source_retired")), "monitor_bindings")
    observed = clock(monitor["observed_at"])
    require(clock(restore["completed_at"]) <= observed <= now
            and now - observed <= timedelta(minutes=15), "monitor_not_fresh")
    closed(monitor["health"], {"origin", "public"}, "monitor_health_pair")
    expected_identity = {"provider": "hetzner", "source_sha": source, "runtime_id": grant["runtime_id"],
                         "authority": "production", "cache_control": "no-store"}
    for health in monitor["health"].values():
        closed(health, {"identity", "body"}, "monitor_health_shape")
        require(health["identity"] == expected_identity
                and health["body"].get("provider") == "hetzner" and health["body"].get("authority") == "production"
                and health["body"].get("release_sha") == source and health["body"].get("snapshot_producer_sha") == source,
                "monitor_current_runtime_or_producer")
        require(timedelta(0) <= now - clock(health["body"].get("generated_at")) <= timedelta(minutes=15),
                "monitor_snapshot_clock")
    require(canonical(monitor["health"]["origin"]) == canonical(monitor["health"]["public"]),
            "monitor_origin_public_differ")
    return {"provider": "hetzner", "runtime_id": grant["runtime_id"], "source": source,
            "target": {"host_identity": grant["host"]["identity"], "volume_uuid": grant["host"]["volume_uuid"],
                       "image_digest": grant["host"]["image_digest"], "grant_sha256": grant_sha,
                       "recovery_sha256": digest(evidence)}, "health": monitor["health"]}


def native_runtime_validator(source):
    """Import only the actual exact-source authority/data validators."""
    root = Path(__file__).resolve().parents[2]
    backend = root / "backend"
    result = subprocess.run(["git", "-C", str(root), "diff", "--quiet", source, "--", "backend/seiche"],
                            timeout=30, capture_output=True)
    require(result.returncode == 0, "native_runtime_validator_source_differs")
    tracked = subprocess.check_output(["git", "-C", str(root), "ls-files", "backend/seiche"], timeout=30).decode().splitlines()
    require({str(p.relative_to(root)) for p in (backend / "seiche").rglob("*.py")} ==
            {p for p in tracked if p.endswith(".py")}, "native_runtime_untracked_python")
    sys.path.insert(0, str(backend))
    from seiche import hetzner_runtime
    require(Path(hetzner_runtime.__file__).resolve() == backend / "seiche/hetzner_runtime.py",
            "native_runtime_validator_import_differs")
    return hetzner_runtime


def validate_native_backup(backup, readback, grant, grant_sha, *, postgres_container, now):
    """Preserve real online/Drive semantics; never manufacture WORM or PITR."""
    require(backup.get("schema") == "seiche.native-backup.v1" and backup.get("kind") == "data"
            and backup.get("status") == "local_readback_verified_cloud_pending"
            and backup.get("runtime_id") == grant["runtime_id"] and backup.get("source") == grant["code"]
            and backup.get("host") == grant["host"] and backup.get("grant_sha256") == grant_sha
            and backup.get("transfer_sha256") == grant["transfer_sha256"]
            and backup.get("restore_sha256") == grant["restore_sha256"], "native_backup_runtime_binding")
    require(timedelta(0) <= now - clock(backup.get("captured_at")) <= timedelta(hours=26), "native_backup_not_fresh")
    require(all(backup.get(k) is False for k in ("cross_store_point_in_time", "final_fenced_capture",
                "source_writers_stopped", "automatic_restore_or_writer_authority", "source_deleted")),
            "native_backup_scope_or_authority")
    database = backup.get("database_snapshot", {})
    require(database.get("database_name") == grant["data"]["database_name"]
            and database.get("container_id") == postgres_container
            and database.get("archive_table_of_contents_verified") is True
            and database.get("disposable_restore_verified") is False,
            "native_backup_restore_claim_must_remain_unqualified")
    require(set(backup.get("components", {})) == {"api", "market", "nbs", "palimpsest-china"},
            "native_backup_component_coverage")
    for component in backup["components"].values():
        require(type(component.get("files")) is int and component["files"] >= 0
                and isinstance(component.get("sqlite_online_snapshots"), list)
                and len(component["sqlite_online_snapshots"]) <= component["files"]
                and component.get("consistency") ==
                "per-file stable reads and per-database SQLite transactions; not a global point in time"
                and clock(component.get("started_at")) <= clock(component.get("finished_at"))
                <= clock(backup["captured_at"]), "native_component_readback_scope")
    require(clock(database.get("started_at")) <= clock(database.get("finished_at"))
            <= clock(backup["captured_at"]), "native_database_snapshot_clock")
    native_runtime_validator(grant["code"]["commit"])
    from seiche.stateful_migration import agent_room_expected_key_binding
    require(set(backup.get("audits", {})) == {"agent_room", "nbs", "palimpsest_china"}
            and hexadecimal(backup.get("scratch_proof_sha256")), "native_backup_full_audit_coverage")
    require(agent_room_expected_key_binding(backup.get("audits", {}).get("agent_room")) ==
            grant["agent_room"]["expected_key_id"], "native_backup_room_key")
    require(readback.get("schema") == "seiche.native-backup-cloud.v1"
            and readback.get("status") == "encrypted_remote_readback_verified"
            and readback.get("runtime_id") == grant["runtime_id"] and readback.get("source") == grant["code"]
            and readback.get("kind") == "data" and readback.get("source_deleted") is False
            and readback.get("automatic_restore_or_writer_authority") is False,
            "native_cloud_readback_binding")
    require(clock(backup["captured_at"]) <= clock(readback.get("verified_at")) <= now, "native_cloud_readback_clock")
    artifacts = backup.get("artifacts", [])
    require(len(artifacts) == 2 and {Path(a.get("path", "")).name for a in artifacts} ==
            {"postgres.dump", "filesystem.tar.gz"}, "native_backup_artifact_coverage")
    observed = readback.get("artifacts", [])
    require(len(observed) == 3 and len({a.get("path") for a in observed}) == 3, "native_cloud_artifact_coverage")
    for item in artifacts:
        require(type(item.get("bytes")) is int and 0 < item["bytes"] <= 18 * GIB
                and hexadecimal(item.get("sha256")), "native_backup_artifact_identity")
        rows = [a for a in observed if a.get("path") == item["path"]]
        require(len(rows) == 1 and all(rows[0].get(k) == v for k, v in item.items()), "native_cloud_artifact_differs")
    manifests = [a for a in observed if Path(a.get("path", "")).name == "manifest.json"]
    require(len(manifests) == 1 and manifests[0].get("sha256") == digest(backup)
            and manifests[0].get("bytes") == len(canonical(backup)), "native_cloud_manifest_binding")
    for item in observed:
        require(item.get("independent_decrypted_readback") is True
                and isinstance(item.get("remote_object_id"), str) and bool(item["remote_object_id"])
                and isinstance(item.get("remote"), str)
                and item["remote"].startswith("gcrypt:railway-migration-20261106/configuration/seiche-native-")
                and "/" not in item["remote"].removeprefix("gcrypt:railway-migration-20261106/configuration/"),
                "native_cloud_independent_encrypted_readback")
    return {"provider": "google-drive-rclone-crypt", "independent_decrypted_readback": True,
            "object_lock": False, "compliance_retention": False, "cross_store_point_in_time": False,
            "online_backup_disposable_restore_verified": False, "final_migration_restore_verified": True}


def validate_native_evidence(evidence, *, source, now):
    """Explicit migration mode: signed final restore plus current native proof.

    The protected nine-document SSE-C/ObjectLock mode remains unchanged. This
    mode has no retention claim and does not promote an online backup to a
    globally consistent capture or a successful independent recovery.
    """
    closed(evidence, {"schema", "grant", "acceptance", "transfer", "final_restore", "release", "boot",
                      "restart", "backup", "backup_readback", "monitor", "source_equivalence", "source_acceptance"}, "native_evidence_shape")
    require(evidence["schema"] == NATIVE_EVIDENCE_SCHEMA and now.tzinfo is not None
            and len(canonical(evidence)) <= 1024 * 1024, "native_evidence_bound")
    publication_source = source
    source = evidence.get("grant", {}).get("payload", {}).get("code", {}).get("commit")
    grant, grant_sha, release, boot, restart = validate_runtime_chain(evidence, source=source, now=now, native=True)
    if publication_source == source:
        require(evidence["source_equivalence"] is None and evidence["source_acceptance"] is None,
                "native_same_source_must_not_claim_bridge")
    else:
        require(isinstance(evidence["source_equivalence"], dict)
                and isinstance(evidence["source_acceptance"], dict), "native_source_bridge_required")
        root = Path(__file__).resolve().parents[2]
        path = root / "ops/release/native_publication_source.py"
        raw = subprocess.check_output(["git", "-C", str(root), "show",
            publication_source + ":ops/release/native_publication_source.py"], timeout=30)
        require(path.read_bytes() == raw and not path.is_symlink(), "native_source_bridge_verifier_differs")
        spec = importlib.util.spec_from_file_location("native_publication_source_bridge", path)
        bridge = importlib.util.module_from_spec(spec)
        exec(compile(raw, str(path), "exec"), bridge.__dict__)
        bridge.validate_acceptance(evidence["source_equivalence"], evidence["source_acceptance"], root=root,
            publication_source=publication_source, runtime_source=source,
            runtime_image_digest=grant["host"]["image_digest"], runtime_grant_sha256=grant_sha, now=now)
    require(digest(evidence["transfer"]) == grant["transfer_sha256"]
            and digest(evidence["final_restore"]) == grant["restore_sha256"], "native_final_receipt_hashes")
    require(not any(item.get("fixture_only", False) for item in
                    (evidence, grant, evidence["transfer"], evidence["final_restore"])), "native_fixture_not_authority")
    runtime = native_runtime_validator(source)
    require(runtime.validate_grant(evidence["grant"]) == grant, "native_actual_grant_validation")
    runtime.validate_transfer(evidence["transfer"], evidence["final_restore"], grant)
    runtime.validate_acceptance(evidence["acceptance"], grant, grant_sha, now=now)
    retention = validate_native_backup(evidence["backup"], evidence["backup_readback"], grant, grant_sha,
                                       postgres_container=release["postgres"]["container_id"], now=now)
    require(clock(restart["observed_at"]) <= clock(evidence["backup"]["captured_at"]), "native_backup_precedes_restart")
    monitor = evidence["monitor"]
    identity(monitor, "seiche.native-monitor.v1", grant, grant_sha)
    require(monitor.get("terminal_fence_receipt_validated") is True
            and monitor.get("source_provider_observed_live") is False
            and monitor.get("observer_mutated_runtime") is False and monitor.get("railway_credentials_used") is False
            and monitor.get("strict_rebuilt") is True
            and monitor.get("transfer_sha256") == grant["transfer_sha256"]
            and monitor.get("restore_sha256") == grant["restore_sha256"], "native_monitor_controls")
    require(monitor.get("app_container_id") == release["container_id"]
            and monitor.get("postgres_container_id") == release["postgres"]["container_id"]
            and monitor.get("docker_restart") == {"app": "no", "postgres": "no"}
            and monitor.get("postgres_identity", {}).get("cluster_system_identifier") ==
                release["postgres"]["database_identity"]["system_identifier"], "native_monitor_current_containers")
    units = monitor.get("units", {})
    require(set(units) == {"seiche-native.service", "seiche-native-postgres.service"}, "native_monitor_unit_set")
    for unit, expected_unit in (("seiche-native.service", release["systemd"]),
                               ("seiche-native-postgres.service", release["postgres"]["systemd"])):
        require(units[unit].get("sha256") == expected_unit["unit_sha256"]
                and units[unit].get("ActiveState") == "active" and units[unit].get("SubState") == "running"
                and units[unit].get("UnitFileState") == "enabled", "native_monitor_unit_state")
    observed = clock(monitor.get("observed_at"))
    require(clock(evidence["backup_readback"]["verified_at"]) <= observed <= now
            and now - observed <= timedelta(minutes=15), "native_monitor_not_fresh")
    roles = monitor.get("processes", {}).get("roles", {})
    require(set(roles) == {"supervisor", "api", "market", "source"}
            and all(type(row.get("pid")) is int and row["pid"] > 0 for row in roles.values())
            and len({row["pid"] for row in roles.values()}) == 4
            and all(roles[k].get("ppid") == roles["supervisor"]["pid"] for k in ("api", "market", "source")),
            "native_monitor_process_tree")
    heartbeats = monitor.get("worker_heartbeats", [])
    require(len(heartbeats) == 2 and {row.get("component_id") for row in heartbeats} ==
            {"official-market-collector", "legacy-source-worker"}, "native_monitor_worker_set")
    for row in heartbeats:
        require(clock(roles["api"].get("started_at")) <= clock(row.get("heartbeat_at")) <= observed
                <= clock(row.get("expected_by")), "native_monitor_worker_clock")
    expected = {"provider": "hetzner", "source_sha": source, "runtime_id": grant["runtime_id"],
                "authority": "production", "cache_control": "no-store"}
    pair = {}
    for kind, transport in (("origin", "private_unix_socket"), ("public", "public_https")):
        observation = monitor.get(kind + "_health", {})
        headers, body = observation.get("headers", {}), observation.get("body", {})
        require(observation.get("http") == 200 and observation.get("transport") == transport
                and observation.get("legacy_railway_headers_absent") is True
                and hexadecimal(observation.get("body_sha256")) and type(observation.get("bytes")) is int
                and 0 < observation["bytes"] <= 2 * 1024**2, "native_monitor_health_observation")
        identity_fields = {"provider": headers.get("x-seiche-provider"), "source_sha": headers.get("x-seiche-release-sha"),
                           "runtime_id": headers.get("x-seiche-runtime"), "authority": headers.get("x-seiche-authority"),
                           "cache_control": headers.get("cache-control")}
        require(not any(k.startswith("x-seiche-railway") or k in {"x-seiche-edge-token", "x-seiche-hetzner-token"}
                        for k in headers), "native_health_ambiguous_provider_or_private_header")
        require(identity_fields == expected and body.get("provider") == "hetzner"
                and body.get("authority") == "production" and body.get("release_sha") == source
                and body.get("snapshot_producer_sha") == source and body.get("faults") == []
                and isinstance(body.get("provenance"), list) and bool(body["provenance"]), "native_health_identity_or_fault")
        require(timedelta(0) <= now - clock(body.get("generated_at")) <= timedelta(minutes=15), "native_health_snapshot_stale")
        pair[kind] = {"identity": identity_fields, "body": body}
    require(canonical(pair["origin"]) == canonical(pair["public"]), "native_health_pair_differs")
    return {"provider": "hetzner", "runtime_id": grant["runtime_id"], "source": source,
            "target": {"host_identity": grant["host"]["identity"], "volume_uuid": grant["host"]["volume_uuid"],
                       "image_digest": grant["host"]["image_digest"], "grant_sha256": grant_sha,
                       "recovery_sha256": digest(evidence)}, "health": pair, "backup_guarantees": retention,
            "publication_source": publication_source,
            "source_equivalence_sha256": digest(evidence["source_equivalence"])}


def capacity_plan(*, free_bytes, free_inodes, reserve_bytes, archive_bytes, filesystem_bytes,
                  database_bytes, api_stage_bytes, application_running, production_database_running):
    """No allocation or optimistic compression assumption; source sizes are observed."""
    values = (free_bytes, free_inodes, reserve_bytes, archive_bytes, filesystem_bytes, database_bytes, api_stage_bytes)
    require(all(type(value) is int and value >= 0 for value in values) and reserve_bytes >= 10 * GIB,
            "capacity_inputs_invalid")
    # Capture keeps sealed files, SQLite staging and transport package; restore
    # keeps downloaded package, member archives, expanded data and PG WAL.
    capture_need = 2 * archive_bytes + api_stage_bytes + GIB
    restore_need = 3 * archive_bytes + filesystem_bytes + 2 * database_bytes + 2 * GIB
    return {"reserve_bytes": reserve_bytes, "capture_work_bytes": capture_need,
            "restore_work_bytes": restore_need, "capture_space_ready": free_bytes >= reserve_bytes + capture_need,
            "restore_space_ready": free_bytes >= reserve_bytes + restore_need,
            "inode_reserve_ready": free_inodes >= 100000,
            "capture_resources_ready": not application_running,
            "restore_resources_ready": not application_running and not production_database_running,
            "helper_cpus": 1, "helper_memory_bytes": 2 * GIB,
            "postgres_cpus": 1, "postgres_memory_bytes": 3 * GIB // 2,
            "paid_capacity_authorized": False}
