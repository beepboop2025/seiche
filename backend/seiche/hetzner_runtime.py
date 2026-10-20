"""Explicit, signed Hetzner writer authority and fail-closed supervision.

The root transfer controller owns grants, acceptance and the active pointer.
This module cannot create authority, restore data, move traffic, or restart a
failed child. The API and two workers run as the same unprivileged application
user. Retiring the root pointer stops the whole process group within one poll.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping

from seiche import standby

GRANT_SCHEMA = "seiche.hetzner-runtime-grant.v1"
SIGNED_SCHEMA = "seiche.hetzner-runtime-approval.v1"
ACCEPTANCE_SCHEMA = "seiche.hetzner-runtime-acceptance.v1"
POINTER_SCHEMA = "seiche.hetzner-runtime-active.v1"
RUNTIME_SCHEMA = "seiche.hetzner-runtime-observation.v1"
SIGNATURE_NAMESPACE = "seiche-hetzner-runtime-v1"
SIGNER_PRINCIPAL = "seiche-hetzner-runtime-release"
OWNER_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBuJV6o8YL2XXR9q4vcwpHuc2z1GEBawSmrJWGrgwzFV"
)
MAX_DOCUMENT_BYTES = 256 * 1024
POLL_SECONDS = 2
RUNTIME_UID = 10001
RUNTIME_GID = 10001
ENV_KEYS = {
    "SEICHE_HETZNER_AUTHORITY_DIR", "SEICHE_HETZNER_GRANT_SHA256",
    "SEICHE_HETZNER_EDGE_TOKEN", "SEICHE_HETZNER_HOST_ID_PATH",
}
_context: dict[str, Any] | None = None
_process_started_at: datetime | None = None


class HetznerRuntimeError(ValueError):
    """The current process has no verified Hetzner writer authority."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise HetznerRuntimeError(message)


def enabled() -> bool:
    return any(key.startswith("SEICHE_HETZNER_") for key in os.environ)


def canonical(value: object) -> bytes:
    return standby.canonical(value)


def file_digest(value: object) -> str:
    return hashlib.sha256(canonical(value) + b"\n").hexdigest()


def _closed(value: object, fields: set[str], label: str) -> dict[str, Any]:
    require(isinstance(value, dict) and set(value) == fields, f"Hetzner {label} fields differ")
    return value


def _hex(value: object, length: int = 64) -> str:
    require(isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{length}}}", value) is not None,
            "Hetzner digest is invalid")
    return value


def _absolute(value: object) -> Path:
    try:
        return standby._absolute(value, "Hetzner runtime")
    except ValueError as error:
        raise HetznerRuntimeError("Hetzner runtime path is invalid") from error


def _read_bytes(path: Path) -> bytes:
    _absolute(str(path))
    standby._root_controlled_ancestry(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == standby.ROOT_UID
                and info.st_nlink == 1 and not info.st_mode & 0o022
                and info.st_size <= MAX_DOCUMENT_BYTES,
                "Hetzner authority document is not root-controlled")
        body = stream.read(MAX_DOCUMENT_BYTES + 1)
        require(len(body) <= MAX_DOCUMENT_BYTES, "Hetzner authority document is too large")
        return body


def document(path: Path, expected: str | None = None) -> dict[str, Any]:
    body = _read_bytes(path)
    if expected is not None:
        require(hmac.compare_digest(hashlib.sha256(body).hexdigest(), _hex(expected)),
                "Hetzner authority document digest differs")
    try:
        value = json.loads(body)
        require(isinstance(value, dict) and body == canonical(value) + b"\n",
                "Hetzner authority document is not canonical")
    except (ValueError, TypeError, UnicodeError) as error:
        raise HetznerRuntimeError("Hetzner authority document is invalid") from error
    return value


def _utc(value: object) -> datetime:
    require(isinstance(value, str), "Hetzner timestamp is invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(result.tzinfo is not None and result.utcoffset() == timedelta(0),
                "Hetzner timestamp must be UTC")
        return result
    except (ValueError, OverflowError) as error:
        raise HetznerRuntimeError("Hetzner timestamp is invalid") from error


@lru_cache(maxsize=32)
def _verify_signature(message: bytes, signature: str, public_key: str) -> None:
    require(isinstance(signature, str) and len(signature) <= 4096
            and signature.startswith("-----BEGIN SSH SIGNATURE-----\n")
            and signature.endswith("-----END SSH SIGNATURE-----\n"),
            "Hetzner grant signature framing differs")
    with tempfile.TemporaryDirectory(prefix="seiche-hetzner-signature-") as temporary:
        root = Path(temporary)
        (root / "allowed").write_text(f"{SIGNER_PRINCIPAL} {public_key}\n", encoding="ascii")
        (root / "signature").write_text(signature, encoding="ascii")
        result = subprocess.run(
            ["/usr/bin/ssh-keygen", "-Y", "verify", "-f", str(root / "allowed"),
             "-I", SIGNER_PRINCIPAL, "-n", SIGNATURE_NAMESPACE, "-s", str(root / "signature")],
            input=message, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
    require(result.returncode == 0, "Hetzner grant signature is invalid")


def validate_grant(envelope: object) -> dict[str, Any]:
    signed = _closed(envelope, {"schema", "payload", "signature"}, "signed grant")
    require(signed["schema"] == SIGNED_SCHEMA, "Hetzner signature schema differs")
    value = _closed(signed["payload"], {
        "schema", "provider", "authority", "runtime_id", "issued_at", "expires_at",
        "code", "transfer_sha256", "restore_sha256", "host", "data", "agent_room",
        "edge_token_sha256",
    }, "grant")
    require(value["schema"] == GRANT_SCHEMA and value["provider"] == "hetzner"
            and value["authority"] == "production", "Hetzner grant authority differs")
    for field in ("runtime_id", "transfer_sha256", "restore_sha256", "edge_token_sha256"):
        _hex(value[field])
    code = _closed(value["code"], {"commit", "tree"}, "source")
    _hex(code["commit"], 40)
    _hex(code["tree"], 40)
    host = _closed(value["host"], {"identity", "volume_uuid", "image_digest"}, "host")
    _hex(host["identity"])
    require(isinstance(host["volume_uuid"], str)
            and re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", host["volume_uuid"]) is not None
            and isinstance(host["image_digest"], str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", host["image_digest"]) is not None,
            "Hetzner host volume or image binding differs")
    data = _closed(value["data"], {"generation_root", "database_name", "postgres_socket",
                                  "postgres_port", "postgres_role"}, "data")
    _absolute(data["generation_root"])
    _absolute(data["postgres_socket"])
    require(isinstance(data["database_name"], str)
            and re.fullmatch(r"seiche_[a-z0-9_]{1,55}", data["database_name"]) is not None
            and data["postgres_role"] == "seiche_runtime"
            and type(data["postgres_port"]) is int and 1 <= data["postgres_port"] <= 65535,
            "Hetzner isolated database differs")
    room = _closed(value["agent_room"], {"expected_key_id"}, "Agent Room")
    if room["expected_key_id"] != "unprovisioned":
        _hex(room["expected_key_id"])
    start, end = _utc(value["issued_at"]), _utc(value["expires_at"])
    require(start < end <= start + timedelta(hours=1), "Hetzner grant lifetime differs")
    _verify_signature(canonical(value), signed["signature"], OWNER_PUBLIC_KEY)
    return value


def validate_transfer(transfer: Mapping[str, Any], restore: Mapping[str, Any],
                      grant: Mapping[str, Any]) -> None:
    require(transfer.get("schema") == "seiche.hetzner-final-transfer.v1"
            and transfer.get("status") == "PASS"
            and transfer.get("authority_transferred") is False
            and transfer.get("writer_grant_issued") is False,
            "Hetzner final transfer is not accepted")
    _hex(transfer.get("handover_id"))
    fence, state, source = transfer.get("fence", {}), transfer.get("final_state", {}), transfer.get("source", {})
    require(fence.get("all_application_writers_absent") is True
            and fence.get("database_admissions_closed") is True,
            "Hetzner parent writers are not terminally fenced")
    _hex(fence.get("receipt_sha256"))
    _hex(source.get("commit"), 40)
    _hex(source.get("terminal_pointer_sha256"))
    _hex(source.get("original_pointer_sha256"))
    require(source["terminal_pointer_sha256"] != source["original_pointer_sha256"],
            "Hetzner parent authority pointer was not retired")
    require(state.get("equal") is True
            and state.get("source_manifest_sha256") == state.get("restored_manifest_sha256"),
            "Hetzner final state differs")
    _hex(state.get("source_manifest_sha256"))
    for field in ("manifest_sha256", "content_set_sha256"):
        _hex(transfer.get("capture", {}).get(field))
    destination = transfer.get("destination", {})
    expected = {
        "provider": "hetzner", "host_identity": grant["host"]["identity"],
        "volume_uuid": grant["host"]["volume_uuid"],
        "generation_root": grant["data"]["generation_root"],
        "database_name": grant["data"]["database_name"],
        "restore_receipt_sha256": grant["restore_sha256"],
    }
    require(all(destination.get(key) == item for key, item in expected.items()),
            "Hetzner final transfer destination differs")
    require(restore.get("schema") == "seiche.hetzner-final-restore.v1"
            and restore.get("status") == "PASS" and restore.get("authority_changed") is False
            and restore.get("writer_grant_issued") is False
            and restore.get("public_origin_changed") is False
            and restore.get("handover_id") == transfer["handover_id"]
            and restore.get("code") == grant["code"]
            and restore.get("image_digest") == grant["host"]["image_digest"]
            and restore.get("host_identity") == grant["host"]["identity"]
            and restore.get("volume_uuid") == grant["host"]["volume_uuid"]
            and restore.get("final_state") == state
            and restore.get("source_backup", {}).get("capture_sha256")
                == transfer["capture"]["manifest_sha256"]
            and restore.get("source_backup", {}).get("snapshot_id")
                == transfer["capture"].get("snapshot_id")
            and restore.get("source_backup", {}).get("source") == source["commit"],
            "Hetzner final restore is not accepted")
    snapshot_id = transfer["capture"].get("snapshot_id")
    require(isinstance(snapshot_id, str)
            and re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", snapshot_id) is not None,
            "Hetzner final capture identity differs")
    _hex(restore["source_backup"].get("transfer_index_sha256"))
    fs, pg = restore.get("filesystem", {}), restore.get("postgres", {})
    require(fs.get("status") == "PASS"
            and fs.get("generation_root") == grant["data"]["generation_root"]
            and fs.get("validated_parent_handoff") == pg.get("parent")
            and pg.get("parent", {}).get("commit") == source["commit"],
            "Hetzner final restored parent differs")
    trees = _closed(fs.get("tree_sha256"), {"api", "market", "nbs", "palimpsest-china"},
                    "restored trees")
    for tree_sha in trees.values():
        _hex(tree_sha)
    parent = _closed(pg["parent"], {"commit", "handoff_id", "envelope_sha256", "payload_sha256"}, "parent")
    for field in ("handoff_id", "envelope_sha256", "payload_sha256"):
        _hex(parent[field])
    require(all(pg.get(key) == grant["data"][key] for key in
                ("database_name", "postgres_socket", "postgres_port"))
            and pg.get("runtime_database_role") == grant["data"]["postgres_role"],
            "Hetzner final restored database differs")
    counts, floor = pg.get("counts"), pg.get("count_floor")
    require(isinstance(counts, list) and isinstance(floor, list) and len(counts) == len(floor) == 4
            and all(type(v) is int and v >= 0 for v in counts + floor)
            and all(a >= b for a, b in zip(counts, floor, strict=True)),
            "Hetzner final restored count floors differ")
    from seiche.stateful_migration import agent_room_expected_key_binding

    require(agent_room_expected_key_binding(fs.get("agent_room_audit")) ==
            grant["agent_room"]["expected_key_id"], "Hetzner restored Agent Room key differs")
    palimpsest_environment(grant, restore)


def pointer(grant: Mapping[str, Any], grant_sha: str) -> dict[str, Any]:
    return {"schema": POINTER_SCHEMA, "state": "active",
            "runtime_id": grant["runtime_id"], "grant_sha256": grant_sha}


def validate_acceptance(value: object, grant: Mapping[str, Any], grant_sha: str,
                        *, now: datetime | None = None) -> dict[str, Any]:
    receipt = _closed(value, {"schema", "runtime_id", "grant_sha256", "transfer_sha256",
                              "restore_sha256", "code", "accepted_at"}, "acceptance")
    require(receipt["schema"] == ACCEPTANCE_SCHEMA
            and receipt["runtime_id"] == grant["runtime_id"]
            and receipt["grant_sha256"] == grant_sha
            and receipt["code"] == grant["code"]
            and all(receipt[key] == grant[key] for key in ("transfer_sha256", "restore_sha256")),
            "Hetzner accepted grant binding differs")
    accepted = _utc(receipt["accepted_at"])
    observed = now or datetime.now(UTC)
    require(_utc(grant["issued_at"]) <= accepted <= _utc(grant["expires_at"])
            and accepted <= observed + timedelta(minutes=1),
            "Hetzner grant was not accepted in its authorized window")
    return receipt


def validate_database(dsn: str, grant: Mapping[str, Any]) -> None:
    from psycopg.conninfo import conninfo_to_dict

    data = grant["data"]
    expected = {"dbname": data["database_name"], "host": data["postgres_socket"],
                "port": str(data["postgres_port"]), "user": data["postgres_role"]}
    require(conninfo_to_dict(dsn) == expected, "Hetzner PostgreSQL connection differs")


def palimpsest_environment(grant: Mapping[str, Any],
                          restore: Mapping[str, Any]) -> dict[str, str]:
    """Preserve the root-audited active bundle without importing old paths."""
    from seiche.palimpsest_china_activation import _BUNDLE_FILE_SPECS

    environment = restore.get("filesystem", {}).get("palimpsest_environment")
    require(isinstance(environment, dict), "Hetzner restored Palimpsest environment is absent")
    if not environment:
        return {}
    require(set(environment) == {spec.environment for spec in _BUNDLE_FILE_SPECS},
            "Hetzner restored Palimpsest environment is incomplete")
    root = Path(grant["data"]["generation_root"]) / "palimpsest-china"
    bundle_names = set()
    for spec in _BUNDLE_FILE_SPECS:
        member = _absolute(environment[spec.environment])
        require(member.name == spec.filename and member.parent.parent == root,
                "Hetzner restored Palimpsest bundle path differs")
        bundle_names.add(_hex(member.parent.name))
    require(len(bundle_names) == 1, "Hetzner restored Palimpsest bundle identities differ")
    return dict(environment)


def runtime_paths(grant: Mapping[str, Any],
                  restore: Mapping[str, Any] | None = None) -> dict[str, str]:
    result = {
        **standby.runtime_paths(grant),
        "SEICHE_PALIMPSEST_CHINA_PUBLICATION_STATUS": "provisional",
    }
    if restore is not None:
        result.update(palimpsest_environment(grant, restore))
    return result


def validate_environment(grant: Mapping[str, Any], restore: Mapping[str, Any]) -> None:
    require({key for key in os.environ if key.startswith("SEICHE_HETZNER_")} == ENV_KEYS,
            "Hetzner runtime environment is incomplete or unknown")
    require(not any(key.startswith(("RAILWAY_", "SEICHE_RAILWAY_", "SEICHE_STANDBY_", "PG"))
                    for key in os.environ), "Hetzner runtime contains foreign provider or PG overrides")
    require(not any(os.getenv(key) for key in ("SEICHE_PREBUILT_HANDOFF_ID", "SEICHE_PREBUILT_PAYLOAD_SHA256")),
            "Hetzner cannot import a prebuilt producer identity")
    expected = {**runtime_paths(grant, restore), "SEICHE_ENV": "production",
                "SEICHE_RELEASE_SHA": grant["code"]["commit"],
                "SEICHE_COLLECTOR_HEARTBEAT_REQUIRED": "1",
                "SEICHE_SOURCE_HEARTBEAT_REQUIRED": "1"}
    prefix = "SEICHE_PALIMPSEST_CHINA_"
    require({key for key in os.environ if key.startswith(prefix)} ==
            {key for key in expected if key.startswith(prefix)},
            "Hetzner Palimpsest runtime settings differ")
    require(all(os.getenv(key) == item for key, item in expected.items()),
            "Hetzner runtime paths or production settings differ")
    token = os.environ["SEICHE_HETZNER_EDGE_TOKEN"]
    require(re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token) is not None
            and hashlib.sha256(token.encode("ascii")).hexdigest() == grant["edge_token_sha256"],
            "Hetzner edge credential binding differs")
    validate_database(os.environ.get("SEICHE_DATABASE_URL", ""), grant)


def _verify_database(grant: Mapping[str, Any], restore: Mapping[str, Any]) -> None:
    import psycopg

    with psycopg.connect(os.environ["SEICHE_DATABASE_URL"], connect_timeout=10) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        role = connection.execute(
            "SELECT r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolreplication,r.rolbypassrls,"
            "d.datdba=r.oid FROM pg_roles r JOIN pg_database d ON d.datname=current_database() "
            "WHERE r.rolname=current_user").fetchone()
        require(role == (False, False, False, False, False, True),
                "Hetzner database role is not an isolated non-superuser owner")
        foreign = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname<>current_database() "
            "AND has_database_privilege(current_user,oid,'CREATE')), "
            "EXISTS(SELECT 1 FROM pg_auth_members WHERE member="
            "(SELECT oid FROM pg_roles WHERE rolname=current_user)), "
            "EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname<>'information_schema' AND n.nspname NOT LIKE 'pg_%' "
            "AND c.relkind IN ('r','p','S','v','m','f') "
            "AND c.relowner<>(SELECT oid FROM pg_roles WHERE rolname=current_user))"
        ).fetchone()
        require(foreign == (False, False, False), "Hetzner database ownership or role membership differs")
        counts = connection.execute(
            "SELECT (SELECT count(*) FROM canonical_observations),"
            "(SELECT count(*) FROM collector_runs),(SELECT count(*) FROM forward_validation_records),"
            "(SELECT count(*) FROM market_snapshots)").fetchone()
        require(all(actual >= minimum for actual, minimum in
                    zip(counts, restore["postgres"]["count_floor"], strict=True)),
                "Hetzner current database regressed below final restore")


def initialize() -> dict[str, Any]:
    global _context, _process_started_at
    _context = None
    _process_started_at = None
    require(os.geteuid() == RUNTIME_UID and os.getegid() == RUNTIME_GID,
            "Hetzner runtime must use the application UID and GID")
    authority = _absolute(os.environ.get("SEICHE_HETZNER_AUTHORITY_DIR"))
    grant_sha = _hex(os.environ.get("SEICHE_HETZNER_GRANT_SHA256"))
    grant = validate_grant(document(authority / "grant.json", grant_sha))
    transfer = document(authority / "transfer.json", grant["transfer_sha256"])
    restore = document(authority / "restore.json", grant["restore_sha256"])
    validate_transfer(transfer, restore, grant)
    validate_environment(grant, restore)
    acceptance = validate_acceptance(document(authority / "acceptance.json"), grant, grant_sha)
    require(document(authority / "active.json") == pointer(grant, grant_sha),
            "Hetzner runtime is not the current writer authority")
    observed = _closed(document(authority / "runtime.json"),
                       {"schema", "runtime_id", "grant_sha256", "code", "host", "data", "http_socket"},
                       "runtime observation")
    require(observed["schema"] == RUNTIME_SCHEMA and observed["runtime_id"] == grant["runtime_id"]
            and observed["grant_sha256"] == grant_sha
            and all(observed[key] == grant[key] for key in ("code", "host", "data")),
            "Hetzner observed deployment differs")
    http_socket = _absolute(observed["http_socket"])
    require(len(os.fsencode(http_socket)) < 104, "Hetzner HTTP Unix socket path is too long")
    host_body = _read_bytes(_absolute(os.environ["SEICHE_HETZNER_HOST_ID_PATH"])).strip()
    require(re.fullmatch(rb"[0-9a-f]{32}", host_body) is not None
            and hashlib.sha256(host_body).hexdigest() == grant["host"]["identity"],
            "Hetzner actual host identity differs")
    standby._verify_source(grant)
    _verify_database(grant, restore)
    _context = {"grant": grant, "restore": restore, "authority": authority,
                "grant_sha": grant_sha, "acceptance_sha": file_digest(acceptance),
                "runtime_sha": file_digest(observed), "http_socket": str(http_socket),
                "host_id_path": os.environ["SEICHE_HETZNER_HOST_ID_PATH"]}
    # Worker heartbeats have second precision. Retain this startup's fractional
    # second so a heartbeat from immediately before a fast restart cannot pass.
    _process_started_at = datetime.now(UTC)
    require_authority()
    return grant


def require_authority() -> dict[str, Any]:
    require(_context is not None, "Hetzner runtime startup has not been verified")
    ctx = _context
    grant = ctx["grant"]
    require(os.environ.get("SEICHE_HETZNER_GRANT_SHA256") == ctx["grant_sha"],
            "Hetzner process grant changed")
    require(os.environ.get("SEICHE_HETZNER_AUTHORITY_DIR") == str(ctx["authority"])
            and os.environ.get("SEICHE_HETZNER_HOST_ID_PATH") == ctx["host_id_path"],
            "Hetzner authority paths changed")
    validate_environment(grant, ctx["restore"])
    authority = ctx["authority"]
    require(document(authority / "active.json") == pointer(grant, ctx["grant_sha"]),
            "Hetzner writer authority has been retired")
    document(authority / "grant.json", ctx["grant_sha"])
    document(authority / "acceptance.json", ctx["acceptance_sha"])
    document(authority / "runtime.json", ctx["runtime_sha"])
    document(authority / "transfer.json", grant["transfer_sha256"])
    document(authority / "restore.json", grant["restore_sha256"])
    return grant


def restore_snapshot(envelope: object) -> tuple[dict, dict, str, str]:
    """Only the final restored parent or this exact release can seed the cache."""
    from seiche import assemble

    grant = require_authority()
    require(isinstance(envelope, dict), "Hetzner active handoff is absent")
    parent = _context["restore"]["postgres"]["parent"]
    if envelope.get("producer_sha") == parent["commit"]:
        return standby.validate_parent(envelope, {"parent": parent})
    require(envelope.get("producer_sha") == grant["code"]["commit"],
            "Hetzner active handoff belongs to an unrelated release")
    return assemble._validated_handoff(envelope, expected_release_sha=grant["code"]["commit"])


def worker_heartbeat_ready(heartbeat: object, *, now: datetime | None = None) -> bool:
    try:
        require_authority()
        require(isinstance(heartbeat, dict) and _process_started_at is not None,
                "Hetzner worker heartbeat is absent")
        observed = now or datetime.now(UTC)
        stamp, deadline = _utc(heartbeat["heartbeat_at"]), _utc(heartbeat["expected_by"])
        return _process_started_at <= stamp <= observed + timedelta(minutes=1) and stamp <= observed <= deadline
    except (ValueError, KeyError, TypeError, OSError):
        return False


def commands(http_socket: str) -> dict[str, list[str]]:
    return {
        kind: [sys.executable, "-m", "seiche.hetzner_runtime", "--worker", kind]
        for kind in ("market", "source")
    } | {"api": [
        sys.executable, "-m", "uvicorn", "seiche.api:app", "--uds", http_socket,
        "--loop", "asyncio", "--http", "h11", "--no-access-log",
    ]}


def run_worker(kind: str) -> int:
    """Initialize authority before a worker imports or starts its collector."""
    import asyncio

    require(kind in {"market", "source"}, "Hetzner worker name differs")
    initialize()
    if kind == "market":
        from seiche.market_runtime import run_worker as collect

        asyncio.run(collect(poll_seconds=30))
    else:
        from seiche.ingest_runtime import run_legacy_worker as collect

        asyncio.run(collect(poll_seconds=300))
    return 0


def _lock_supervisor(socket_path: str) -> int:
    path = Path(socket_path).with_suffix(".supervisor.lock")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == RUNTIME_UID
                and info.st_nlink == 1 and not info.st_mode & 0o077,
                "Hetzner supervisor lock differs")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _kill_children(children: list[subprocess.Popen]) -> None:
    # Revocation cannot wait for a 30s graceful writer shutdown. The transfer
    # controller must independently drain/close PostgreSQL admissions before
    # treating this process-exit observation as a terminal source fence.
    for child in children:
        # An exited group leader can leave collector subprocesses alive.
        # Retire the group even when Popen already observed the leader exit.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for child in children:
        child.wait(timeout=10)


def supervise() -> int:
    require(os.geteuid() == RUNTIME_UID and os.getegid() == RUNTIME_GID,
            "Hetzner supervisor must be the unprivileged application user")
    initialize()
    lock = _lock_supervisor(_context["http_socket"])
    children = []
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        for command in commands(_context["http_socket"]).values():
            require_authority()
            children.append(subprocess.Popen(
                command, cwd=Path(__file__).resolve().parents[2], env=dict(os.environ),
                stdin=subprocess.DEVNULL, start_new_session=True,
            ))
        while not stopping:
            require_authority()
            require(all(child.poll() is None for child in children),
                    "Hetzner supervised process exited")
            time.sleep(POLL_SECONDS)
        return 0
    finally:
        try:
            _kill_children(children)
        finally:
            os.close(lock)
            for sig, handler in previous.items():
                signal.signal(sig, handler)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 1:
            raise SystemExit(supervise())
        require(len(sys.argv) == 3 and sys.argv[1] == "--worker",
                "Hetzner runtime command differs")
        raise SystemExit(run_worker(sys.argv[2]))
    except Exception:
        # Child tracebacks belong in private bounded service logs, never in a
        # response or source receipt. No automatic child restart is attempted.
        print("Hetzner runtime stopped: authority or supervised process failed", file=sys.stderr)
        raise SystemExit(1) from None
