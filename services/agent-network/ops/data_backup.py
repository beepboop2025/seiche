#!/usr/bin/env python3
"""Encrypted, consistent snapshots for the separate learning and commerce stores.

Restoration creates an offline directory and never overwrites or activates a
service. Reuse the network's streaming authenticated encryption implementation.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile
import uuid

from backup import (_backup_key, _decrypt, _encrypt, _private_directory, _private_file,
                    _sha, _write_json)
from seiche_agent_network.cli import read_private_json

DATABASES = {"learning", "wake"}
FILES = {"wake-config"}
SCHEMA = "agent-platform.data-backup.v1"


def backup(config_file, key_file, destination):
    config = read_private_json(config_file)
    if set(config) != {"databases", "files"} or not config["databases"] or set(config["databases"]) - DATABASES or set(config["files"]) - FILES:
        raise ValueError("explicit learning/wake database mapping required")
    if ("wake" in config["databases"]) != ("wake-config" in config["files"]):
        raise ValueError("Wake snapshots require the matching private configuration")
    directory = _private_directory(destination)
    key = _backup_key(key_file)
    created = datetime.now(timezone.utc).isoformat()
    stem = "data-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    target = directory / (stem + ".sanbackup")
    with tempfile.TemporaryDirectory(prefix=".data-backup-", dir=directory) as temporary:
        stage = Path(temporary)
        hashes = {}
        for name, path in sorted(config["databases"].items()):
            path = _private_file(path)
            out = stage / (name + ".sqlite")
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(out) as snapshot:
                source.backup(snapshot)
                if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("snapshot integrity failed")
            hashes[out.name] = _sha(out)
        for name, path in sorted(config["files"].items()):
            value = read_private_json(path)
            if name == "wake-config":
                if Path(value["database"]).resolve() != Path(config["databases"]["wake"]).resolve():
                    raise ValueError("Wake configuration points at a different database")
                with sqlite3.connect(stage / "wake.sqlite") as db:
                    import hashlib
                    expected = hashlib.sha256(value["identity_secret"].encode()).hexdigest()
                    if db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()[0] != expected:
                        raise ValueError("Wake identity secret does not match snapshot")
            out = stage / (name + ".json")
            _write_json(out, value)
            hashes[out.name] = _sha(out)
        manifest = {"schema": SCHEMA, "created_at": created, "sha256": hashes,
                    "snapshot_consistency": "individual SQLite online backups; no cross-database atomicity claimed"}
        _write_json(stage / "manifest.json", manifest)
        archive = stage / "data.tar"
        with tarfile.open(archive, "w:") as bundle:
            for name in sorted([*hashes, "manifest.json"]):
                bundle.add(stage / name, arcname=name, recursive=False)
        archive.chmod(0o600)
        _encrypt(archive, target, key)
    receipt = {"schema": SCHEMA, "created_at": created, "ciphertext_sha256": _sha(target),
               "stores": sorted(config["databases"]), "encrypted": True}
    _write_json(target.with_suffix(".json"), receipt)
    return {"status": "backed_up", "bundle": str(target), **receipt}


def restore(bundle, key_file, destination):
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("restore requires a new offline directory")
    parent = _private_directory(destination.parent)
    allowed = {"learning.sqlite", "wake.sqlite", "wake-config.json", "manifest.json"}
    with tempfile.TemporaryDirectory(prefix=".data-restore-", dir=parent) as temporary:
        stage = Path(temporary)
        _decrypt(bundle, stage / "data.tar", _backup_key(key_file))
        extracted = stage / "verified"
        extracted.mkdir(mode=0o700)
        with tarfile.open(stage / "data.tar", "r:") as archive:
            members = archive.getmembers()
            names = [m.name for m in members]
            if (len(names) != len(set(names)) or not set(names) <= allowed or "manifest.json" not in names
                    or any(not m.isfile() or m.size > 10 * 1024**3 for m in members)):
                raise ValueError("invalid backup members")
            for member in members:
                if member.name.endswith(".json") and member.size > 65536:
                    raise ValueError("backup metadata exceeds bound")
                fd = os.open(extracted / member.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as output, archive.extractfile(member) as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
        manifest = read_private_json(extracted / "manifest.json")
        if manifest.get("schema") != SCHEMA or set(manifest["sha256"]) != set(names) - {"manifest.json"}:
            raise ValueError("invalid backup manifest")
        for name, sha in manifest["sha256"].items():
            if _sha(extracted / name) != sha:
                raise ValueError("backup content hash mismatch")
            if name.endswith(".sqlite"):
                with sqlite3.connect(extracted / name) as db:
                    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("restored database integrity failed")
        if ("wake.sqlite" in names) != ("wake-config.json" in names):
            raise ValueError("Wake identity configuration is missing")
        if "wake-config.json" in names:
            value = read_private_json(extracted / "wake-config.json")
            value["database"] = str(destination / "wake.sqlite")
            (extracted / "wake-config.json").write_text(json.dumps(value, indent=2) + "\n")
        # Reserve a new offline destination only after authentication and all
        # hashes/integrity checks pass. Never replace an existing directory.
        destination.mkdir(mode=0o700, exist_ok=False)
        for source in extracted.iterdir():
            descriptor = os.open(destination / source.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
                shutil.copyfileobj(input_file, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
    return {"status": "restored_offline", "stores": sorted(n[:-7] for n in names if n.endswith(".sqlite")),
            "activation": "not activated; expire data and reapply later withdrawals before serving"}


def prune(directory, *, retention_days=7):
    if type(retention_days) is not int or not 1 <= retention_days <= 30:
        raise ValueError("retention must be 1..30 days")
    directory = _private_directory(directory)
    cutoff = datetime.now(timezone.utc).timestamp() - retention_days * 86400
    removed = 0
    for receipt in directory.glob("data-*.json"):
        record = read_private_json(receipt)
        bundle = receipt.with_suffix(".sanbackup")
        if record.get("schema") != SCHEMA or datetime.fromisoformat(record["created_at"]).timestamp() >= cutoff:
            continue
        if not bundle.exists() or _sha(_private_file(bundle)) != record["ciphertext_sha256"]:
            continue
        bundle.unlink()
        receipt.unlink()
        removed += 1
    return {"expired_owned_backups_removed": removed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["backup", "restore", "prune"])
    parser.add_argument("--config")
    parser.add_argument("--key-file")
    parser.add_argument("--bundle")
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    if args.operation == "backup":
        result = backup(args.config, args.key_file, args.destination)
        result.update(prune(args.destination))
    elif args.operation == "restore":
        result = restore(args.bundle, args.key_file, args.destination)
    else:
        result = prune(args.destination)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
