"""Receipt-bound Hetzner standby: existing evidence, with no writer authority.

The receipt is an operator assertion about an independently verified restore.
It never authorizes collection, publication, database migration, or activation.
Run the process with read-only data mounts, a SELECT-only PostgreSQL role, and
network isolation as well as the application guards below.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
from typing import Any, Mapping

SCHEMA = "seiche.hetzner-standby.v1"
ROOT_UID = 0
MAX_RECEIPT_BYTES = 256 * 1024
READ_PATHS = frozenset(
    {"/api/health", "/api/overview", "/api/public", "/api/money-markets",
     "/api/oil-funding", "/api/estuary"}
)
_initialized_digest: str | None = None
_network_guard_installed = False


class StandbyError(ValueError):
    pass


def enabled() -> bool:
    # A partial configuration is a failed standby, never ordinary production.
    return any(key.startswith("SEICHE_STANDBY_") for key in os.environ)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _closed(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise StandbyError(f"standby {label} fields differ")
    return value


def _hex(value: object, length: int, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(f"[0-9a-f]{{{length}}}", value) is None:
        raise StandbyError(f"standby {label} is invalid")
    return value


def _absolute(value: object, label: str) -> Path:
    if (
        not isinstance(value, str) or "\x00" in value
        or not Path(value).is_absolute() or os.path.normpath(value) != value
        or Path(value).resolve() != Path(value)
    ):
        raise StandbyError(f"standby {label} path is invalid")
    return Path(value)


def _root_controlled_ancestry(path: Path) -> None:
    for parent in path.parents:
        metadata = parent.stat()
        if metadata.st_uid not in {0, ROOT_UID} or metadata.st_mode & 0o022:
            raise StandbyError("standby receipt ancestry is not root-controlled")


def _document(path: Path, expected_digest: str) -> dict[str, Any]:
    """Read canonical, root-controlled evidence without following symlinks."""
    _absolute(str(path), "receipt")
    _root_controlled_ancestry(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != ROOT_UID
            or metadata.st_mode & 0o022 or metadata.st_nlink != 1
            or metadata.st_size > MAX_RECEIPT_BYTES
        ):
            raise StandbyError("standby receipt metadata is unsafe")
        body = handle.read(MAX_RECEIPT_BYTES + 1)
    if not hmac.compare_digest(hashlib.sha256(body).hexdigest(), expected_digest):
        raise StandbyError("standby receipt digest differs")
    try:
        value = json.loads(body)
        if body != canonical(value) + b"\n" or not isinstance(value, dict):
            raise ValueError
    except (ValueError, TypeError, UnicodeError) as exc:
        raise StandbyError("standby receipt is not canonical JSON") from exc
    return value


def validate_receipt(value: object) -> dict[str, Any]:
    receipt = _closed(value, {
        "schema", "provider", "authority", "candidate_id", "code", "restore",
        "parent", "data", "agent_room", "edge_token_sha256",
    }, "receipt")
    if (
        receipt["schema"] != SCHEMA or receipt["provider"] != "hetzner"
        or receipt["authority"] != "standby"
    ):
        raise StandbyError("standby identity or authority differs")
    _hex(receipt["candidate_id"], 64, "candidate ID")
    _hex(receipt["edge_token_sha256"], 64, "edge token hash")
    code = _closed(receipt["code"], {"commit", "tree"}, "code")
    for field in code:
        _hex(code[field], 40, f"code {field}")
    restore = _closed(receipt["restore"], {"receipt_path", "receipt_sha256"}, "restore")
    _absolute(restore["receipt_path"], "restore receipt")
    _hex(restore["receipt_sha256"], 64, "restore hash")
    parent = _closed(receipt["parent"], {
        "commit", "handoff_id", "envelope_sha256", "payload_sha256",
    }, "parent")
    for field in parent:
        _hex(parent[field], 40 if field == "commit" else 64, f"parent {field}")
    data = _closed(receipt["data"], {
        "generation_root", "database_name", "postgres_socket",
        "postgres_port", "postgres_role",
    }, "data")
    for field in ("generation_root", "postgres_socket"):
        _absolute(data[field], field)
    for field in ("database_name", "postgres_role"):
        if not isinstance(data[field], str) or re.fullmatch(
            r"[a-z][a-z0-9_]{0,62}", data[field]
        ) is None:
            raise StandbyError(f"standby {field} is invalid")
    if type(data["postgres_port"]) is not int or not 1 <= data["postgres_port"] <= 65535:
        raise StandbyError("standby PostgreSQL port is invalid")
    room = _closed(receipt["agent_room"], {"expected_key_id"}, "Agent Room")
    if room["expected_key_id"] != "unprovisioned":
        _hex(room["expected_key_id"], 64, "Agent Room key")
    return receipt


def runtime_paths(receipt: Mapping[str, Any]) -> dict[str, str]:
    root = Path(receipt["data"]["generation_root"])
    return {
        "SEICHE_RUNTIME_DATA_DIR": str(root / "api"),
        "SEICHE_AGENT_ROOM_DB_PATH": str(root / "api/_agent_room/agent-room.sqlite"),
        "SEICHE_ATTEST_DIR": str(root / "api/_attest"),
        "SEICHE_RAW_CAPTURE_DIR": str(root / "market/raw"),
        "SEICHE_NORMALIZED_DIR": str(root / "market/normalized"),
        "SEICHE_BACKFILL_STATE_DIR": str(root / "market/backfill"),
        "SEICHE_VALIDATION_DIR": str(root / "market/validation"),
        "SEICHE_USD_FUNDING_CORE_EXPORT_DIR": str(root / "market/exports/us-usd-funding-core-v1"),
        "SEICHE_NBS_ROOT": str(root / "nbs"),
        "SEICHE_NBS_PUBLIC_DIR": str(root / "nbs/public"),
        "SEICHE_AGENT_ROOM_EXPECTED_KEY_ID": receipt["agent_room"]["expected_key_id"],
    }


def validate_database(dsn: str, receipt: Mapping[str, Any]) -> None:
    from psycopg.conninfo import conninfo_to_dict

    data = receipt["data"]
    try:
        observed = conninfo_to_dict(dsn)
    except Exception:
        raise StandbyError("standby PostgreSQL connection is invalid") from None
    expected = {
        "dbname": data["database_name"], "host": data["postgres_socket"],
        "port": str(data["postgres_port"]), "user": data["postgres_role"],
        "options": "-c default_transaction_read_only=on",
    }
    if observed != expected:
        raise StandbyError("standby PostgreSQL connection differs")


def load_receipt() -> dict[str, Any]:
    if not enabled():
        raise StandbyError("standby configuration is absent")
    expected = _hex(os.environ.get("SEICHE_STANDBY_RECEIPT_SHA256"), 64, "receipt hash")
    path = _absolute(os.environ.get("SEICHE_STANDBY_RECEIPT_PATH"), "receipt")
    receipt = validate_receipt(_document(path, expected))
    if (
        os.environ.get("SEICHE_ENV") != "production"
        or os.environ.get("SEICHE_RELEASE_SHA") != receipt["code"]["commit"]
        or any(os.environ.get(key) for key in os.environ if key.startswith("RAILWAY_"))
        or any(os.environ.get(key) for key in os.environ if key.startswith("SEICHE_RAILWAY_"))
        or any(os.environ.get(key) for key in os.environ if key.startswith("PG"))
        or os.environ.get("SEICHE_PREBUILT_HANDOFF_ID")
        or os.environ.get("SEICHE_PREBUILT_PAYLOAD_SHA256")
        or os.environ.get("SEICHE_COLLECTOR_HEARTBEAT_REQUIRED") != "0"
        or os.environ.get("SEICHE_SOURCE_HEARTBEAT_REQUIRED") != "0"
        or os.environ.get("SEICHE_BOARD_AUTH") != "0"
    ):
        raise StandbyError("standby runtime conflicts with production authority")
    if any(os.environ.get(key) != value for key, value in runtime_paths(receipt).items()):
        raise StandbyError("standby runtime paths differ")
    token = os.environ.get("SEICHE_STANDBY_EDGE_TOKEN", "")
    if (
        re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token) is None
        or not hmac.compare_digest(
            hashlib.sha256(token.encode("ascii")).hexdigest(), receipt["edge_token_sha256"]
        )
    ):
        raise StandbyError("standby edge token differs")
    validate_database(os.environ.get("SEICHE_DATABASE_URL", ""), receipt)
    return receipt


def _verify_source(receipt: Mapping[str, Any]) -> None:
    checkout = Path(__file__).resolve().parents[2]
    command = ["git", "-c", f"safe.directory={checkout}", "-C", str(checkout)]
    environment = {
        "PATH": os.defpath, "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    for ref, field in (("HEAD", "commit"), ("HEAD^{tree}", "tree")):
        result = subprocess.run(
            [*command, "rev-parse", ref], env=environment, check=True,
            capture_output=True, text=True, timeout=10,
        )
        if result.stdout.strip() != receipt["code"][field]:
            raise StandbyError("standby checkout identity differs")
    result = subprocess.run(
        [*command, "status", "--porcelain", "--untracked-files=all"],
        env=environment, check=True, capture_output=True, text=True, timeout=10,
    )
    if result.stdout:
        raise StandbyError("standby checkout is not clean")


def _install_network_guard(receipt: Mapping[str, Any]) -> None:
    global _network_guard_installed
    if _network_guard_installed:
        return
    allowed_socket = str(
        Path(receipt["data"]["postgres_socket"])
        / f".s.PGSQL.{receipt['data']['postgres_port']}"
    )

    def guard(event: str, args: tuple[Any, ...]) -> None:
        if event == "socket.connect":
            sock, address = args
            if sock.family != socket.AF_UNIX or address != allowed_socket:
                raise StandbyError("standby outbound connection is forbidden")
        elif event in {"socket.getaddrinfo", "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"}:
            raise StandbyError("standby outbound resolution or child process is forbidden")

    sys.addaudithook(guard)
    _network_guard_installed = True


def _verify_database(receipt: Mapping[str, Any]) -> None:
    import psycopg

    validate_database(os.environ["SEICHE_DATABASE_URL"], receipt)
    with psycopg.connect(os.environ["SEICHE_DATABASE_URL"], connect_timeout=10) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        row = connection.execute(
            "SELECT current_setting('transaction_read_only'), "
            "rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
            "FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if row != ("on", False, False, False, False, False):
            raise StandbyError("standby PostgreSQL role is not restricted")
        writable = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname<>'information_schema' "
            "AND n.nspname NOT LIKE 'pg_%' "
            "AND c.relkind IN ('r','p','v','m','f') "
            "AND has_table_privilege(current_user,c.oid,"
            "'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')), "
            "EXISTS (SELECT 1 FROM pg_namespace WHERE nspname<>'information_schema' "
            "AND nspname NOT LIKE 'pg_%' AND has_schema_privilege(current_user,oid,'CREATE')), "
            "has_database_privilege(current_user,current_database(),'CREATE')"
        ).fetchone()
        if writable != (False, False, False):
            raise StandbyError("standby PostgreSQL role can change restored data")


def initialize() -> dict[str, Any]:
    global _initialized_digest
    receipt = load_receipt()
    restore = _document(
        Path(receipt["restore"]["receipt_path"]), receipt["restore"]["receipt_sha256"]
    )
    if (
        restore.get("schema") != "seiche.hetzner-candidate-restore.v1"
        or restore.get("status") != "PASS" or restore.get("candidate_only") is not True
        or restore.get("authority_changed") is not False
        or restore.get("filesystem", {}).get("generation_root")
        != receipt["data"]["generation_root"]
    ):
        raise StandbyError("standby restore acceptance differs")
    for field in ("database_name", "postgres_socket", "postgres_port", "postgres_role"):
        if restore.get("postgres", {}).get(field) != receipt["data"][field]:
            raise StandbyError("standby restored database binding differs")
    if (
        restore.get("postgres", {}).get("parent") != receipt["parent"]
        or restore.get("filesystem", {}).get("validated_parent_handoff") != receipt["parent"]
        or restore.get("source_backup", {}).get("source") != receipt["parent"]["commit"]
    ):
        raise StandbyError("standby restored parent binding differs")
    from seiche.stateful_migration import agent_room_expected_key_binding

    try:
        expected_key = agent_room_expected_key_binding(
            restore.get("filesystem", {}).get("agent_room_audit")
        )
    except ValueError:
        raise StandbyError("standby restored Agent Room audit is invalid") from None
    if expected_key != receipt["agent_room"]["expected_key_id"]:
        raise StandbyError("standby restored Agent Room key differs")
    _verify_source(receipt)
    _verify_database(receipt)
    _initialized_digest = digest(receipt)
    _install_network_guard(receipt)
    return receipt


def require_initialized() -> dict[str, Any]:
    receipt = load_receipt()
    if _initialized_digest != digest(receipt):
        raise StandbyError("standby startup acceptance is absent or changed")
    return receipt


def validate_parent(envelope: object, receipt: Mapping[str, Any]) -> tuple[dict, dict, str, str]:
    from seiche import assemble

    parent = receipt["parent"]
    if not hmac.compare_digest(digest(envelope), parent["envelope_sha256"]):
        raise StandbyError("standby parent envelope differs")
    validated = assemble._validated_handoff(
        envelope, expected_release_sha=parent["commit"],
        expected_handoff_id=parent["handoff_id"],
    )
    if not hmac.compare_digest(assemble._snapshot_digest(validated[0]), parent["payload_sha256"]):
        raise StandbyError("standby parent payload differs")
    return validated
