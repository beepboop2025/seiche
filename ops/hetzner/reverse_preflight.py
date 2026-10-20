#!/usr/bin/env python3
"""Read-only diagnostic for the exact accepted Railway parent.

Run through the existing root SSH channel, without installing this file:
  railway ssh ... -- python -I -B - < reverse_preflight.py

This is not a fence, export, activation, or authorization. It never sends a
signal, changes authority, opens a server, or executes a database mutation.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

sys.dont_write_bytecode = True
SCHEMA = "seiche.hetzner-reverse-preflight.v1"
PARENT = {
    "SEICHE_RELEASE_SHA": "793dc42b391141c20aee911f6d18f000bd3feb8a",
    "RAILWAY_DEPLOYMENT_ID": "7a38b906-c475-4899-bd7e-ff6d2e1c4a41",
    "RAILWAY_REPLICA_ID": "07060a56-7f03-437f-8f83-320e9e245eb6",
    "RAILWAY_PROJECT_ID": "b49753cb-7b80-4b0d-a3c3-124aecad769d",
    "RAILWAY_ENVIRONMENT_ID": "b99f0925-c7a1-48bc-bb4d-3bdf65c1e8b9",
    "RAILWAY_SERVICE_ID": "b59a35b8-75d6-4f96-bca6-b014df615324",
}
SUPERVISOR_CODE = (
    "import runpy, sys; sys.path.insert(0, '/workspace/backend'); "
    "runpy.run_module('seiche.stateful_entrypoint', run_name='__main__')"
)
MAX_PROCESSES = 4096
MAX_TABLES = 4096
MAX_SESSIONS = 512
MAX_PROC_BYTES = 512 * 1024
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]{0,127}")


class PreflightError(ValueError):
    """Messages are fixed diagnostic codes; never propagate provider text."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise PreflightError(code)


def digest(value: Any) -> str:
    return hashlib.sha256(
        (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
         + "\n").encode()
    ).hexdigest()


def readonly_audit(event: str, arguments: tuple[Any, ...]) -> None:
    """Defence in depth for the executable driver, including imported code."""
    forbidden = {
        "os.kill", "os.killpg", "os.remove", "os.rename", "os.mkdir", "os.rmdir",
        "os.chmod", "os.chown", "os.link", "os.symlink", "os.truncate",
        "os.utime", "os.setxattr", "os.removexattr", "shutil.copyfile",
    }
    if event in forbidden:
        raise PreflightError("mutation_forbidden")
    if event == "open":
        _path, mode, flags = arguments
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & write_flags
        ):
            raise PreflightError("filesystem_write_forbidden")
    if event == "subprocess.Popen":
        executable, argv, _cwd, _env = arguments
        allowed = (
            str(executable) == "/usr/bin/ssh-keygen"
            and len(argv) == 11 and argv[1:3] == ["-Y", "verify"]
        ) or (
            Path(str(executable)).name == "git"
            and list(argv[1:3]) == ["-C", "/workspace"]
            and (
                list(argv[3:]) in (["rev-parse", "HEAD"], ["rev-parse", "HEAD^{tree}"])
                or (len(argv) == 7 and list(argv[3:5]) == ["merge-base", "--is-ancestor"]
                    and all(re.fullmatch(r"[0-9a-f]{40}", x) for x in argv[5:]))
            )
        )
        require(allowed, "subprocess_forbidden")


def read_bounded(path: Path, limit: int = MAX_PROC_BYTES) -> bytes:
    with path.open("rb") as handle:
        body = handle.read(limit + 1)
    require(len(body) <= limit, "read_limit_exceeded")
    return body


def parse_stat(body: str) -> dict[str, Any]:
    """The comm field can contain spaces and parentheses; split after its end."""
    prefix, separator, suffix = body.rpartition(") ")
    require(bool(separator) and "(" in prefix, "process_stat_malformed")
    values = suffix.split()
    require(len(values) >= 20, "process_stat_malformed")
    return {
        "pid": int(prefix.split("(", 1)[0].strip()),
        "state": values[0], "ppid": int(values[1]),
        "pgid": int(values[2]), "session": int(values[3]),
        "start_ticks": int(values[19]),
    }


def process(path: Path) -> dict[str, Any]:
    before = parse_stat(read_bounded(path / "stat", 8192).decode())
    command = read_bounded(path / "cmdline").split(b"\0")
    if command and command[-1] == b"":
        command.pop()
    status = read_bounded(path / "status", 64 * 1024).decode().splitlines()
    ids = {}
    for line in status:
        if line.startswith(("Uid:", "Gid:")):
            name, *values = line.split()
            ids[name[:-1].lower()] = [int(v) for v in values]
    after = parse_stat(read_bounded(path / "stat", 8192).decode())
    identity = ("pid", "ppid", "pgid", "session", "start_ticks")
    require(all(before[k] == after[k] for k in identity), "process_changed_during_read")
    require(set(ids) == {"uid", "gid"}, "process_identity_unavailable")
    return {**after, **ids, "argv": [v.decode() for v in command]}


def inventory(proc_root: Path = Path("/proc")) -> dict[int, dict[str, Any]]:
    entries = sorted((p for p in proc_root.iterdir() if p.name.isdigit()),
                     key=lambda p: int(p.name))
    require(len(entries) <= MAX_PROCESSES, "process_inventory_limit")
    result = {}
    for entry in entries:
        try:
            row = process(entry)
        except FileNotFoundError:
            continue
        result[row["pid"]] = row
    return result


def topology(rows: dict[int, dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    supervisors = [
        p for p in rows.values()
        if p["argv"][1:] == ["-I", "-B", "-c", SUPERVISOR_CODE]
    ]
    require(len(supervisors) == 1, "supervisor_not_unique")
    supervisor = supervisors[0]
    require(supervisor["uid"] == [0] * 4 and supervisor["gid"] == [0] * 4,
            "supervisor_not_root")
    tini = rows.get(supervisor["ppid"])
    require(tini is not None and tini["pid"] == 1
            and Path(tini["argv"][0]).name == "tini"
            and len(tini["argv"]) >= 3 and tini["argv"][1] == "--",
            "tini_parent_not_proven")
    children = [p for p in rows.values() if p["ppid"] == supervisor["pid"]]
    classified: dict[str, Any] = {"supervisor": supervisor, "tini": tini}
    unsupported = []
    for child in children:
        args = child["argv"][1:]
        if args == ["-m", "seiche.cli", "market-worker", "--poll-seconds", "30"]:
            role = "market"
        elif args == ["-m", "seiche.cli", "source-worker", "--poll-seconds", "300"]:
            role = "source"
        elif (len(args) == 8 and args[:4] == ["-m", "uvicorn", "seiche.api:app", "--host"]
              and args[4] == "0.0.0.0" and args[5] == "--port"
              and args[6].isdigit() and args[7] == "--no-access-log"):
            role = "api"
        else:
            unsupported.append("unexpected_supervisor_child")
            continue
        require(role not in classified, "duplicate_runtime_child")
        require(child["uid"] == [10001] * 4 and child["gid"] == [10001] * 4,
                "runtime_child_identity_differs")
        require(child["pgid"] == child["pid"] == child["session"],
                "runtime_child_process_group_differs")
        require(Path(child["argv"][0]).name.startswith("python"), "child_executable_differs")
        classified[role] = child
    require({"api", "market", "source"} <= set(classified), "runtime_children_incomplete")
    protected = {p["pid"] for p in classified.values()}
    for row in rows.values():
        if row["pid"] not in protected and 10001 in row["uid"]:
            unsupported.append("additional_runtime_uid_process")
        if row["pid"] not in protected and row["ppid"] in protected - {1}:
            unsupported.append("runtime_descendant_present")
    return classified, sorted(set(unsupported))


def environment(pid: int, proc_root: Path = Path("/proc")) -> dict[str, str]:
    fields = read_bounded(proc_root / str(pid) / "environ").split(b"\0")
    result = {}
    for field in fields:
        if not field:
            continue
        name, separator, value = field.partition(b"=")
        require(bool(separator), "process_environment_malformed")
        key = name.decode()
        require(key not in result, "duplicate_process_environment")
        result[key] = value.decode()
    return result


def exact_identity(env: dict[str, str]) -> None:
    require(all(env.get(key) == value for key, value in PARENT.items()),
            "accepted_parent_identity_differs")
    require(env.get("SEICHE_RAILWAY_STATEFUL_MODE") == "production",
            "parent_not_production")
    require(bool(re.fullmatch(r"[0-9a-f]{64}",
                              env.get("SEICHE_RAILWAY_APPLICATION_REQUEST_ID", ""))),
            "parent_application_request_absent")


def verify_signature_memory(message: bytes, signature: str, public_key: str) -> None:
    """Use the existing OpenSSH verifier without creating disk files."""
    require(hasattr(os, "memfd_create"), "anonymous_signature_verification_unavailable")
    require(isinstance(signature, str) and len(signature) <= 4096
            and signature.startswith("-----BEGIN SSH SIGNATURE-----\n")
            and signature.endswith("-----END SSH SIGNATURE-----\n"),
            "signature_framing_invalid")
    descriptors = []
    try:
        for name, body in (
            ("allowed-signers", f"seiche-railway-application-release {public_key}\n".encode()),
            ("signature", signature.encode("ascii")),
        ):
            fd = os.memfd_create(name, os.MFD_CLOEXEC)
            descriptors.append(fd)
            offset = 0
            while offset < len(body):
                written = os.write(fd, body[offset:])
                require(written > 0, "anonymous_signature_write_failed")
                offset += written
            os.lseek(fd, 0, os.SEEK_SET)
        result = subprocess.run(
            ["/usr/bin/ssh-keygen", "-Y", "verify",
             "-f", f"/proc/self/fd/{descriptors[0]}",
             "-I", "seiche-railway-application-release",
             "-n", "seiche-railway-application-v1",
             "-s", f"/proc/self/fd/{descriptors[1]}"],
            input=message, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=tuple(descriptors), timeout=10, check=False,
        )
        require(result.returncode == 0, "signature_invalid")
    finally:
        for fd in descriptors:
            os.close(fd)


def validate_authority(env: dict[str, str]) -> tuple[dict[str, Any], str]:
    exact_identity(env)
    sys.path.insert(0, "/workspace/backend")
    from seiche import stateful_application as app
    from seiche import stateful_migration as migration
    from seiche import stateful_recovery as recovery

    original_verifier = app._verify_signature
    app._verify_signature = verify_signature_memory
    try:
        request = app.load_request()
        require(request["commit"] == PARENT["SEICHE_RELEASE_SHA"],
                "request_source_differs")
        activation = app.validate_runtime(env, production=True)
        authority = migration.PLATFORM_ROOT / "authority"
        grant = app.read_document(authority / "application-grants" / f"{request['request_id']}.json")
        require(grant == activation["application"]["grant"], "accepted_grant_differs")
        pointer = app.read_document(authority / "application-active.json")
        generation = recovery.generation_root(env)
        require(generation == migration.PLATFORM_ROOT / "generations"
                / request["parent"]["generation"], "generation_binding_differs")
        output = {
            "source_commit": request["commit"], "source_tree": request["tree"],
            "request_id": request["request_id"], "request_sha256": app.digest(request),
            "activation_sha256": app.digest(activation), "grant_sha256": app.digest(grant),
            "pointer_sha256": app.digest(pointer),
            "railway": migration.railway_identity(env), "replica_id": env["RAILWAY_REPLICA_ID"],
            "generation_root": str(generation), "database": request["parent"]["database"],
            "signature_verification": "openssh_anonymous_memory",
            "files": {name: str(generation / name)
                      for name in ("api", "market", "nbs", "palimpsest-china")},
        }
        return output, env["SEICHE_DATABASE_URL"]
    finally:
        app._verify_signature = original_verifier


IDENTITY_SQL = """
SELECT current_database(), current_setting('server_version_num')::integer,
       current_setting('transaction_read_only'), pg_is_in_recovery(),
       pg_database_size(current_database()), pg_backend_pid(),
       (SELECT rolsuper OR pg_has_role(current_user, 'pg_read_all_stats', 'MEMBER')
          FROM pg_roles WHERE rolname = current_user)
"""
TABLE_SQL = """
SELECT n.nspname, c.relname, c.relkind,
       pg_total_relation_size(c.oid), c.reltuples::bigint, c.relrowsecurity
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p', 'm', 'f')
   AND left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema'
 ORDER BY n.nspname, c.relname LIMIT 4097
"""
SESSION_SQL = """
SELECT pid, pid = pg_backend_pid(), backend_type, state,
       xact_start::text, backend_xid IS NOT NULL, client_addr IS NOT NULL,
       usename = current_user
  FROM pg_stat_activity WHERE datname = current_database()
 ORDER BY pid LIMIT 513
"""


def inspect_database(connection: Any, expected_database: str) -> tuple[dict[str, Any], list[str]]:
    """Metadata only. No query texts, connection strings, usernames or row data."""
    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    identity = connection.execute(IDENTITY_SQL).fetchone()
    require(identity[0] == expected_database and identity[2] == "on"
            and identity[3] is False, "database_identity_or_readonly_differs")
    rows = connection.execute(TABLE_SQL).fetchall()
    sessions = connection.execute(SESSION_SQL).fetchall()
    require(len(rows) <= MAX_TABLES and len(sessions) <= MAX_SESSIONS,
            "database_inventory_limit")
    tables = []
    for schema, name, kind, size, estimate, rls in rows:
        require(bool(IDENTIFIER.fullmatch(schema)) and bool(IDENTIFIER.fullmatch(name)),
                "database_identifier_unsupported")
        require(type(size) is int and size >= 0, "database_size_invalid")
        tables.append({"schema": schema, "table": name, "kind": kind, "total_bytes": size,
                       "estimated_rows": estimate, "row_security": rls})
    safe_sessions = []
    for pid, own, backend, state_value, started, has_xid, remote, same_role in sessions:
        require(backend in {"client backend", "autovacuum worker", "parallel worker",
                            "logical replication worker", "background worker", "walsender"},
                "database_backend_type_unsupported")
        require(state_value in {None, "active", "idle", "idle in transaction",
                                "idle in transaction (aborted)", "fastpath function call",
                                "disabled"}, "database_session_state_unsupported")
        safe_sessions.append({
            "pid": pid, "inspection_session": own, "backend_type": backend,
            "state": state_value, "transaction_started_at": started,
            "has_transaction_id": has_xid, "remote_client": remote, "same_role": same_role,
        })
    unsupported = []
    if not identity[6]:
        unsupported.append("database_session_visibility_incomplete")
    if any(t["kind"] == "f" or t["row_security"] for t in tables):
        unsupported.append("foreign_table_or_row_security_requires_review")
    return {
        "database": identity[0], "server_version_num": identity[1],
        "database_bytes": identity[4], "transaction_read_only": True,
        "session_visibility_complete": bool(identity[6]), "tables": tables,
        "table_inventory_sha256": digest(tables), "sessions": safe_sessions,
        "session_writer_attribution": "UNQUALIFIED",
        "exact_table_rows_read": False, "no_loss_proven": False,
    }, unsupported


def volume(path: Path) -> dict[str, Any]:
    require(path.is_absolute() and path.resolve() == path, "volume_path_not_direct")
    metadata = path.stat()
    require(stat.S_ISDIR(metadata.st_mode), "volume_path_not_directory")
    capacity = os.statvfs(path)
    return {
        "path": str(path), "device": metadata.st_dev,
        "filesystem_bytes": capacity.f_blocks * capacity.f_frsize,
        "available_bytes": capacity.f_bavail * capacity.f_frsize,
        "available_inodes": capacity.f_favail,
        "recursive_size_measured": False,
    }


def recheck_authority(authority: dict[str, Any]) -> None:
    """An observation spanning a changed authority is not a usable preflight."""
    uid = authority["request_id"]
    root = Path("/var/lib/seiche-platform")
    bindings = (
        (Path("/migration/request.json"), "request_sha256"),
        (root / "authority/application-active.json", "pointer_sha256"),
        (root / "authority/application-grants" / f"{uid}.json", "grant_sha256"),
        (root / "cutover-receipts" / f"{uid}.activation.json", "activation_sha256"),
    )
    for path, expected in bindings:
        body = read_bounded(path, 256 * 1024)
        require(hashlib.sha256(body).hexdigest() == authority[expected],
                "authority_changed_during_preflight")


def safe_process(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in ("pid", "ppid", "pgid", "session", "start_ticks", "state",
                               "executable", "cwd") if k in row}


def run() -> dict[str, Any]:
    require(sys.platform == "linux" and os.geteuid() == 0, "linux_root_required")
    rows = inventory()
    selected, unsupported = topology(rows)
    for role, row in selected.items():
        root = Path("/proc") / str(row["pid"])
        row["executable"] = os.readlink(root / "exe")
        row["cwd"] = os.readlink(root / "cwd")
        if role == "tini":
            require(row["executable"] == "/usr/bin/tini", "tini_executable_differs")
        else:
            require(Path(row["executable"]).name.startswith("python")
                    and row["cwd"] == "/workspace", "runtime_executable_or_cwd_differs")
    require(all(selected[role]["executable"] == selected["supervisor"]["executable"]
                for role in ("api", "market", "source")), "runtime_python_differs")
    env = environment(selected["api"]["pid"])
    for role in ("market", "source"):
        child_env = environment(selected[role]["pid"])
        exact_identity(child_env)
        require(child_env.get("SEICHE_DATABASE_URL") == env.get("SEICHE_DATABASE_URL"),
                "worker_database_differs")
    authority, dsn = validate_authority(env)
    import psycopg
    with psycopg.connect(
        dsn, connect_timeout=10, application_name="seiche_reverse_preflight",
        options="-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=3000",
    ) as connection:
        database, db_unsupported = inspect_database(connection, authority["database"])
    unsupported.extend(db_unsupported)
    recheck_authority(authority)
    for role, before in selected.items():
        path = Path("/proc") / str(before["pid"])
        after = process(path)
        require(all(before[k] == after[k] for k in
                    ("pid", "ppid", "pgid", "session", "start_ticks", "argv", "uid", "gid")),
                "process_changed_during_preflight")
        require(os.readlink(path / "exe") == before["executable"]
                and os.readlink(path / "cwd") == before["cwd"],
                "process_changed_during_preflight")
        if role in {"api", "market", "source"}:
            after_env = environment(before["pid"])
            exact_identity(after_env)
            require(after_env.get("SEICHE_DATABASE_URL") == dsn,
                    "process_database_changed_during_preflight")
    return {
        "schema": SCHEMA, "observed_at": datetime.now(UTC).isoformat(),
        "status": "UNSUPPORTED" if unsupported else "PARENT_OBSERVED",
        "operation": "dry_run", "mutation_performed": False,
        "production_transition_authorized": False, "authority": authority,
        "processes": {name: safe_process(row) for name, row in selected.items()},
        "database": database,
        "volumes": [volume(Path("/")), volume(Path("/var/lib/seiche-platform")),
                    volume(Path(authority["generation_root"]))],
        "unsupported": sorted(set(unsupported)),
        "hold": {
            "status": "UNQUALIFIED",
            "source_model": "tini waits for the supervised child; stopping that child does not stop its descendants",
            "runtime_hold_tested": False, "signal_sent": False,
            "requires": ["qualified supervisor hold and crash/restart reconciliation",
                         "API and both worker process groups quiesced",
                         "all external database writers identified and fenced",
                         "durable terminal authority fence and restart refusal proof"],
        },
        "transport": {"status": "UNQUALIFIED", "existing_recovery_after_fence": False,
                      "requires": ["independent bounded authenticated final-byte transfer",
                                   "closed member inventory and exact byte hashes"]},
        "final_transfer": {"status": "NOT_STARTED", "no_loss_proven": False,
                           "requires_new_post_quiescence_snapshot": True},
    }


def main() -> int:
    sys.addaudithook(readonly_audit)
    try:
        require(len(sys.argv) == 1, "arguments_not_supported")
        result = run()
    except BaseException as exc:
        # psycopg/libpq, subprocess and filesystem errors may contain secrets.
        code = str(exc) if isinstance(exc, PreflightError) else "preflight_dependency_failed"
        result = {"schema": SCHEMA, "operation": "dry_run", "status": "BLOCKED",
                  "mutation_performed": False, "production_transition_authorized": False,
                  "error": code, "error_type": type(exc).__name__,
                  "hold": {"status": "UNQUALIFIED"}, "transport": {"status": "UNQUALIFIED"}}
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0 if result["status"] == "PARENT_OBSERVED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
