#!/usr/bin/env python3
"""Private, authenticated-encrypted database+key backups; offline restores only.

Database URLs are read from a 0600 JSON config or SEICHE_NETWORK_DATABASE, never
from argv. PostgreSQL credentials reach pg_dump/pg_restore through environment
variables, never command arguments. No source database or existing key is deleted.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from seiche_agent_network.cli import read_private_json
from seiche_agent_network.crypto import Crypto

MAGIC = b"SEICHE-BACKUP-1\n"
CHUNK = 1024 * 1024
NAMES = frozenset({"database.dump", "service-keys.json", "manifest.json"})


def _private_directory(path):
    path = Path(path)
    if not path.exists():
        path.mkdir(mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("Backup directories must be private real directories (0700)")
    return path


def _private_file(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("Backup inputs must be private regular files (0600)")
    return path


def _write_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _sha(path):
    value = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            value.update(chunk)
    return value.hexdigest()


def initialize_backup_key(output):
    _write_json(output, {"backup_key": base64.urlsafe_b64encode(os.urandom(32)).decode(), "algorithm": "AES-256-GCM"})
    return {"status": "backup_key_created", "private_file": str(output)}


def _backup_key(path):
    key = base64.urlsafe_b64decode(read_private_json(path)["backup_key"])
    if len(key) != 32:
        raise ValueError("Backup key must contain 256 bits")
    return key


def _url(database):
    if not isinstance(database, str) or not database:
        raise ValueError("Explicit database configuration required")
    if "://" not in database:
        return make_url("sqlite:///" + str(Path(database).resolve()))
    url = make_url(database)
    if url.get_backend_name() == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
        return url if "connect_timeout" in url.query else url.update_query_dict({"connect_timeout": "10"})
    if url.get_backend_name() != "sqlite" or url.database in {None, "", ":memory:"}:
        raise ValueError("Persistent SQLite or PostgreSQL database required")
    return url


def _fingerprint(url):
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as db:
            return db.execute(text("SELECT key_fingerprint FROM network_lock WHERE id = 1")).scalar_one()
    finally:
        engine.dispose()


def _pg_command(program, url, arguments):
    if url.query.get("options"):
        raise ValueError("Backup/restore requires an exclusive whole database, not a search_path override")
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update(PGHOST=url.host or "127.0.0.1", PGPORT=str(url.port or 5432), PGDATABASE=url.database or "",
               PGUSER=url.username or "", PGPASSWORD=url.password or "", PGCONNECT_TIMEOUT="10")
    if "sslmode" in url.query:
        env["PGSSLMODE"] = url.query["sslmode"]
    result = subprocess.run([program, *arguments], env=env, capture_output=True, timeout=300)
    if result.returncode:
        # pg tools can include connection information and database text in errors.
        raise RuntimeError("PostgreSQL backup/restore command failed; inspect configuration privately")


def _snapshot(url, destination):
    if url.get_backend_name() == "sqlite":
        source = _private_file(url.database)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(destination) as copied:
                original.backup(copied)
                if copied.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("SQLite snapshot failed integrity validation")
    else:
        _pg_command("pg_dump", url, ["--format=custom", "--no-owner", "--no-privileges", "--file", str(destination)])
        os.chmod(destination, 0o600)


def _encrypt(source, destination, key):
    nonce = os.urandom(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(MAGIC)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as output, open(source, "rb") as plaintext:
        output.write(MAGIC + nonce)
        for chunk in iter(lambda: plaintext.read(CHUNK), b""):
            output.write(cipher.update(chunk))
        output.write(cipher.finalize())
        output.write(cipher.tag)
        output.flush()
        os.fsync(output.fileno())


def _decrypt(source, destination, key):
    source = _private_file(source)
    with open(source, "rb") as encrypted:
        if encrypted.read(len(MAGIC)) != MAGIC:
            raise ValueError("Unsupported backup envelope")
        nonce = encrypted.read(12)
        size = source.stat().st_size - len(MAGIC) - 12 - 16
        if size <= 0:
            raise ValueError("Incomplete encrypted backup")
        encrypted.seek(-16, os.SEEK_END)
        tag = encrypted.read(16)
        encrypted.seek(len(MAGIC) + 12)
        cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        cipher.authenticate_additional_data(MAGIC)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            while size:
                chunk = encrypted.read(min(CHUNK, size))
                if not chunk:
                    raise ValueError("Truncated encrypted backup")
                output.write(cipher.update(chunk))
                size -= len(chunk)
            output.write(cipher.finalize())
            output.flush()
            os.fsync(output.fileno())
    # No decrypted byte is parsed or restored before GCM authentication succeeds.


def _extract_verified(archive, directory):
    with tarfile.open(archive, "r:") as bundle:
        members = bundle.getmembers()
        if len(members) != 3 or {member.name for member in members} != NAMES or any(not member.isfile() for member in members):
            raise ValueError("Unexpected backup members")
        for member in members:
            if member.name != "database.dump" and member.size > 65536:
                raise ValueError("Backup metadata exceeds its bound")
            fd = os.open(directory / member.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output, bundle.extractfile(member) as source:
                shutil.copyfileobj(source, output, CHUNK)
    manifest = read_private_json(directory / "manifest.json")
    if manifest.get("schema") != "seiche-agent-network.backup.v1" or manifest.get("backend") not in {"sqlite", "postgresql"}:
        raise ValueError("Unsupported backup manifest")
    for name in ("database.dump", "service-keys.json"):
        if _sha(directory / name) != manifest["sha256"][name]:
            raise ValueError("Backup content hash mismatch")
    keys = read_private_json(directory / "service-keys.json")
    if Crypto(keys["encryption_key"], keys["signing_key"]).fingerprint != manifest["key_fingerprint"]:
        raise ValueError("Backup key identity mismatch")
    return manifest, keys


def _prune(directory, retention_days, current):
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    for receipt in directory.glob("backup-*.json"):
        if not re.fullmatch(r"backup-\d{8}T\d{6}Z-[a-f0-9]{12}\.json", receipt.name):
            continue
        try:
            record = read_private_json(receipt)
            if record.get("schema") != "seiche-agent-network.backup-receipt.v1":
                continue
            artifact = directory / (receipt.stem + ".sanbackup")
            if artifact == current or datetime.fromisoformat(record["created_at"]) >= cutoff or not artifact.exists():
                continue
            _private_file(artifact)
            if _sha(artifact) != record["ciphertext_sha256"]:
                continue
            artifact.unlink()
            receipt.unlink()
            removed += 1
        except (OSError, ValueError, TypeError, KeyError):
            # Unrecognized or incomplete artifacts are never eligible for deletion.
            continue
    return removed


def backup(database, keys_file, backup_key_file, destination, *, retention_days=7):
    if not isinstance(retention_days, int) or not 1 <= retention_days <= 30:
        raise ValueError("Backup retention must be 1–30 days")
    directory = _private_directory(destination)
    key = _backup_key(backup_key_file)
    keys = read_private_json(keys_file)
    crypto = Crypto(keys["encryption_key"], keys["signing_key"])
    url = _url(database)
    if url.get_backend_name() == "sqlite":
        _private_file(url.database)
    if _fingerprint(url) != crypto.fingerprint:
        raise ValueError("Service keys do not match this database")
    created = datetime.now(timezone.utc)
    stem = "backup-" + created.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    destination_file = directory / (stem + ".sanbackup")
    with tempfile.TemporaryDirectory(prefix=".backup-", dir=directory) as temporary:
        stage = Path(temporary)
        _snapshot(url, stage / "database.dump")
        _write_json(stage / "service-keys.json", keys)
        manifest = {"schema": "seiche-agent-network.backup.v1", "created_at": created.isoformat(),
                    "backend": url.get_backend_name(), "key_fingerprint": crypto.fingerprint,
                    "sha256": {name: _sha(stage / name) for name in ("database.dump", "service-keys.json")}}
        _write_json(stage / "manifest.json", manifest)
        archive = stage / "archive.tar"
        with tarfile.open(archive, "w:") as bundle:
            for name in sorted(NAMES):
                bundle.add(stage / name, arcname=name, recursive=False)
        os.chmod(archive, 0o600)
        _encrypt(archive, destination_file, key)
    receipt = {"schema": "seiche-agent-network.backup-receipt.v1", "created_at": created.isoformat(),
               "backend": url.get_backend_name(), "algorithm": "AES-256-GCM", "ciphertext_sha256": _sha(destination_file),
               "ciphertext_bytes": destination_file.stat().st_size, "retention_days": retention_days,
               "service_keys_in_encrypted_bundle": True}
    _write_json(directory / (stem + ".json"), receipt)
    removed = _prune(directory, retention_days, destination_file)
    return {"status": "backed_up", "bundle": str(destination_file), "receipt": str(directory / (stem + ".json")),
            "ciphertext_sha256": receipt["ciphertext_sha256"], "expired_owned_backups_removed": removed}


def _require_empty(url):
    if url.get_backend_name() == "sqlite":
        if Path(url.database).exists():
            raise ValueError("SQLite restore requires a nonexistent target file")
        return
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as db:
            found = db.execute(text("SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','S','f'))")).scalar_one()
            if found:
                raise ValueError("PostgreSQL restore requires an empty exclusive database")
    finally:
        engine.dispose()


def restore(database, bundle_file, backup_key_file, restored_keys_file):
    url = _url(database)
    key_target = Path(restored_keys_file)
    if key_target.exists() or key_target.is_symlink():
        raise ValueError("Restore must not overwrite an existing service key file")
    _require_empty(url)
    _private_directory(key_target.parent)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=key_target.parent) as temporary:
        stage = Path(temporary)
        _decrypt(bundle_file, stage / "archive.tar", _backup_key(backup_key_file))
        manifest, keys = _extract_verified(stage / "archive.tar", stage)
        if manifest["backend"] != url.get_backend_name():
            raise ValueError("Cross-database-engine restoration is unsupported")
        _require_empty(url)
        _write_json(key_target, keys)
        try:
            if url.get_backend_name() == "sqlite":
                fd = os.open(url.database, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                os.close(fd)
                with sqlite3.connect(stage / "database.dump") as snapshot, sqlite3.connect(url.database) as destination:
                    snapshot.backup(destination)
                    if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Restored SQLite database failed integrity validation")
            else:
                _pg_command("pg_restore", url, ["--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction", "--dbname", url.database, str(stage / "database.dump")])
            if _fingerprint(url) != manifest["key_fingerprint"]:
                raise ValueError("Restored database does not match the included service keys")
        except BaseException:
            # Only the freshly created key file belongs to this failed restore.
            key_target.unlink(missing_ok=True)
            raise
    return {"status": "restored_offline", "backend": url.get_backend_name(), "service_key_identity_verified": True,
            "activation": "Not activated. Apply expiry and newer deletion/revocation records before serving traffic."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    init = commands.add_parser("init-backup-key")
    init.add_argument("--output", required=True)
    prune = commands.add_parser("prune")
    prune.add_argument("--destination", required=True)
    prune.add_argument("--retention-days", type=int, default=7)
    make = commands.add_parser("backup")
    make.add_argument("--database-config", help="0600 JSON containing database_url; otherwise SEICHE_NETWORK_DATABASE")
    make.add_argument("--keys-file", required=True)
    make.add_argument("--backup-key-file", required=True)
    make.add_argument("--destination", required=True)
    make.add_argument("--retention-days", type=int, default=7)
    load = commands.add_parser("restore")
    load.add_argument("--database-config", help="0600 JSON containing database_url; otherwise SEICHE_NETWORK_DATABASE")
    load.add_argument("--bundle", required=True)
    load.add_argument("--backup-key-file", required=True)
    load.add_argument("--restored-keys-file", required=True)
    try:
        args = parser.parse_args(argv)
        if args.operation == "init-backup-key":
            result = initialize_backup_key(args.output)
        elif args.operation == "prune":
            if not 1 <= args.retention_days <= 30:
                raise ValueError("Backup retention must be 1–30 days")
            result = {"status": "pruned", "expired_owned_backups_removed": _prune(_private_directory(args.destination), args.retention_days, None)}
        else:
            database = read_private_json(args.database_config)["database_url"] if args.database_config else os.environ.get("SEICHE_NETWORK_DATABASE")
            if args.operation == "backup":
                result = backup(database, args.keys_file, args.backup_key_file, args.destination, retention_days=args.retention_days)
            else:
                result = restore(database, args.bundle, args.backup_key_file, args.restored_keys_file)
        print(json.dumps(result))
        return 0
    except Exception:
        print("Backup operation refused or failed. Check private configuration, matching keys, archive authenticity and an empty restore target; no secrets were printed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
