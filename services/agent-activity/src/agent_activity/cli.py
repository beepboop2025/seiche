"""Local operator authority; enrollment and review are never public HTTP tools."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time

from cryptography.fernet import Fernet
import uvicorn

from .analysis import reconcile, dossier, weekly_export, export_status
from .collect import collect
from .store import Store, Rejected, canonical, digest


def write_new(path, value):
    path = Path(path)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(value if isinstance(value, bytes) else canonical(value)+b"\n")


def private_file(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_mode & 0o077 or p.stat().st_uid != os.getuid():
        raise Rejected("file_must_be_owned_private_regular_file")
    return p.read_bytes()


def backup(store, output, keyfile):
    # Consistent online SQLite snapshot; authenticated chunks bound memory use.
    from contextlib import closing
    import shutil
    import struct
    import uuid
    fernet = Fernet(private_file(keyfile).strip())
    if shutil.disk_usage(store.root).free < store.path.stat().st_size*3 + 3*1024**3:
        raise Rejected("backup_disk_reserve")
    fd, name = tempfile.mkstemp(dir=store.root, prefix="backup-", suffix=".sqlite3")
    os.close(fd)
    archive = uuid.uuid4().bytes
    created = False
    try:
        with closing(sqlite3.connect(store.path)) as source, closing(sqlite3.connect(name)) as target:
            source.backup(target)
        with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
            created = True
            out.write(b"AAE2\n")
            def frame(value):
                token = fernet.encrypt(value)
                out.write(struct.pack(">I", len(token)))
                out.write(token)
            frame(b"H"+canonical({"schema":"agent-activity.backup.v2", "archive":archive.hex(), "created_at":time.time(),
                "data_key":private_file(store.root / "private.key").decode()}))
            hashed, count = hashlib.sha256(), 0
            with open(name, "rb") as snapshot:
                while chunk := snapshot.read(1024*1024):
                    hashed.update(chunk)
                    frame(b"D"+archive+struct.pack(">Q", count)+chunk)
                    count += 1
            frame(b"E"+archive+struct.pack(">Q", count)+hashed.digest())
            out.flush()
            os.fsync(out.fileno())
        return {"status":"encrypted_backup_written", "database_sha256":hashed.hexdigest(), "chunks":count}
    except BaseException:
        if created:
            Path(output).unlink(missing_ok=True)
        raise
    finally:
        Path(name).unlink(missing_ok=True)


def restore(bundle_path, destination, keyfile):
    import shutil
    import struct
    destination = Path(destination)
    bundle = Path(bundle_path)
    if destination.exists():
        raise Rejected("restore_destination_must_not_exist")
    if bundle.is_symlink() or not bundle.is_file() or bundle.stat().st_mode & 0o077 or bundle.stat().st_uid != os.getuid():
        raise Rejected("backup_file_must_be_owned_and_private")
    fernet = Fernet(private_file(keyfile).strip())
    destination.mkdir(mode=0o700)
    try:
        with bundle.open("rb") as stream:
            if stream.read(5) != b"AAE2\n":
                raise Rejected("backup_format_unknown")
            def frame():
                size = stream.read(4)
                if len(size) != 4:
                    raise Rejected("backup_truncated")
                length = struct.unpack(">I", size)[0]
                if not 1 <= length <= 2_000_000:
                    raise Rejected("backup_frame_limit")
                token = stream.read(length)
                if len(token) != length:
                    raise Rejected("backup_truncated")
                return fernet.decrypt(token)
            header = frame()
            if header[:1] != b"H":
                raise Rejected("backup_header_invalid")
            header = json.loads(header[1:])
            if header["schema"] != "agent-activity.backup.v2":
                raise Rejected("backup_schema_invalid")
            archive, hashed, count = bytes.fromhex(header["archive"]), hashlib.sha256(), 0
            with os.fdopen(os.open(destination / "activity.sqlite3", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
                while True:
                    value = frame()
                    if len(value) < 25 or value[1:17] != archive or struct.unpack(">Q", value[17:25])[0] != count:
                        raise Rejected("backup_chunk_sequence_invalid")
                    if value[:1] == b"E":
                        if value[25:] != hashed.digest() or stream.read(1):
                            raise Rejected("backup_integrity_failed")
                        break
                    if value[:1] != b"D":
                        raise Rejected("backup_chunk_type_invalid")
                    hashed.update(value[25:]); out.write(value[25:]); count += 1
            write_new(destination / "private.key", header["data_key"].encode())
        store = Store(destination)
        with store.db() as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise Rejected("restored_database_integrity_failed")
        # Offline recovery deliberately cannot resurrect withdrawn access or private records.
        with store.db(write=True) as db:
            db.execute("UPDATE contributors SET revoked=1")
            for table in ["contributions", "identity_claims", "reviews", "applications", "exports"]:
                db.execute(f"DELETE FROM {table}")
        store.expire()
        return {"status":"offline_restore_verified", "private_records_removed":True, "fresh_contributor_enrollment_required":True}
    except BaseException:
        shutil.rmtree(destination)
        raise


def parser():
    p = argparse.ArgumentParser(description="Agent Activity evidence operations")
    p.add_argument("--state", required=True, help="Owned private storage directory")
    s = p.add_subparsers(dest="command", required=True)
    init = s.add_parser("init"); init.add_argument("--operator-output", required=True); init.add_argument("--backup-key-output", required=True)
    server = s.add_parser("serve"); server.add_argument("--host", default="127.0.0.1"); server.add_argument("--port", type=int, default=18804); server.add_argument("--operator-file", required=True)
    poll = s.add_parser("collect"); poll.add_argument("--source", action="append"); poll.add_argument("--force", action="store_true")
    s.add_parser("summary"); s.add_parser("expire"); s.add_parser("reconcile"); s.add_parser("applications")
    enroll = s.add_parser("enroll")
    for arg in ["organization", "authority", "grant-ref", "independent-ref", "output"]: enroll.add_argument("--"+arg, required=True)
    enroll.add_argument("--days", type=int, default=90); enroll.add_argument("--resale-ref")
    review = s.add_parser("review-identity"); review.add_argument("--claim-id", required=True); review.add_argument("--review-ref", required=True); review.add_argument("--publish", action="store_true")
    ds = s.add_parser("dossier"); ds.add_argument("--entity-id", required=True); ds.add_argument("--output", required=True)
    ex = s.add_parser("weekly-export"); ex.add_argument("--week", required=True); ex.add_argument("--output", required=True)
    es = s.add_parser("export-status"); es.add_argument("--id", required=True)
    bk = s.add_parser("backup"); bk.add_argument("--output", required=True); bk.add_argument("--key-file", required=True)
    rs = s.add_parser("restore"); rs.add_argument("--bundle", required=True); rs.add_argument("--key-file", required=True)
    return p


def main():
    args = parser().parse_args()
    try:
        if args.command == "restore":
            result = restore(args.bundle, args.state, args.key_file)
        else:
            store = Store(args.state)
            if args.command == "init":
                if Path(args.operator_output).exists() or Path(args.backup_key_output).exists():
                    raise Rejected("credential_destination_exists")
                write_new(args.operator_output, {"credential": secrets.token_urlsafe(36)})
                write_new(args.backup_key_output, Fernet.generate_key())
                result = {"status": "initialized", "credentials": "written only to requested private files"}
            elif args.command == "serve":
                from .api import create_app
                if args.host not in {"127.0.0.1", "::1"}:
                    raise Rejected("serve_behind_trusted_loopback_proxy")
                credential = json.loads(private_file(args.operator_file))["credential"]
                uvicorn.run(create_app(store, credential), host=args.host, port=args.port, access_log=False, proxy_headers=False)
                return
            elif args.command == "collect":
                result = collect(store, args.source, args.force)
            elif args.command == "summary": result = store.summary()
            elif args.command == "expire": store.expire(); result = {"status": "expired"}
            elif args.command == "reconcile": result = reconcile(store)
            elif args.command == "enroll":
                if Path(args.output).exists(): raise Rejected("credential_destination_exists")
                result = store.enroll(args.organization, args.authority, args.grant_ref, args.independent_ref, args.days, args.resale_ref)
                write_new(args.output, result); result = {"contributor_id": result["contributor_id"], "status": "credential_written_privately"}
            elif args.command == "review-identity":
                store.review_identity(args.claim_id, args.review_ref, args.publish); result = {"status": "review_recorded"}
            elif args.command == "dossier":
                write_new(args.output, dossier(store, args.entity_id)); result = {"status": "dossier_written"}
            elif args.command == "weekly-export":
                result = weekly_export(store, args.week); write_new(args.output, result)
            elif args.command == "export-status": result = export_status(store, args.id)
            elif args.command == "backup": result = backup(store, args.output, args.key_file)
            elif args.command == "applications":
                with store.db() as db:
                    result = [{"id": r["id"], "received_at": r["received"], "application": store.decrypt(r["payload"])}
                              for r in db.execute("SELECT * FROM applications WHERE received>? ORDER BY received DESC", (time.time()-30*86400,))]
        print(json.dumps(result, indent=2))
    except Rejected as exc:
        print(json.dumps({"error": exc.code}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
