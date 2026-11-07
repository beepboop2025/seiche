"""Root-owned acceptance/package preparation; never sign, deploy or publish."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import stat


def module(name):
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location("native_prepare_" + name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


release = module("hetzner_release")
bridge = module("native_publication_source")


def private_document(path):
    path = Path(path)
    release.require(path.is_absolute() and path.resolve() == path, "private path must not redirect")
    for parent in path.parents:
        info = parent.stat()
        release.require(info.st_uid == 0 and not info.st_mode & 0o022, "private path ancestry is not root controlled")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        release.require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                        and not info.st_mode & 0o077 and 0 < info.st_size <= 1024**2,
                        "private evidence is not bounded single-link root data")
        body = stream.read(1024**2 + 1)
    value = json.loads(body)
    release.require(body == release.canonical(value), "private evidence is not canonical")
    return value


def write_exclusive(path, value):
    path = Path(path)
    release.require(path.is_absolute() and path.parent.resolve() == path.parent, "output ancestry redirects")
    for parent in [path.parent, *path.parent.parents]:
        info = parent.stat()
        release.require(info.st_uid == 0 and not info.st_mode & 0o022, "output ancestry is not root controlled")
    body = release.canonical(value)
    release.require(len(body) <= 1024**2, "output package exceeds bound")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
        stream.write(body); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def accept_source(evidence, source, *, root, now):
    """Return acceptance only after every actual publication prerequisite passes."""
    candidate = deepcopy(evidence)
    grant = candidate["grant"]["payload"]
    bindings = dict(root=root, publication_source=source, runtime_source=grant["code"]["commit"],
                    runtime_image_digest=grant["host"]["image_digest"], runtime_grant_sha256=release.digest(candidate["grant"]))
    payload = bridge.verify(candidate["source_equivalence"], **bindings, now=now)
    acceptance = {"schema": bridge.ACCEPTANCE_SCHEMA,
                  "approval_sha256": bridge.digest(candidate["source_equivalence"]),
                  **{k: payload[k] for k in ("publication", "runtime", "runtime_image_digest", "runtime_grant_sha256")},
                  "accepted_at": now.isoformat()}
    release.require(candidate["source_acceptance"] is None, "source acceptance already exists")
    candidate["source_acceptance"] = acceptance
    release.validate_evidence(candidate, source=source, now=now)
    return acceptance


def health_package(evidence, source, intent, request, *, now):
    release.require(all(isinstance(v, str) and re.fullmatch("[0-9a-f]{64}", v) for v in (intent, request)),
                    "publication intent/request must be exact identities")
    admitted = release.validate_evidence(evidence, source=source, now=now)
    return {"schema": release.NATIVE_PACKAGE_SCHEMA, "intent_id": intent, "publication_request_id": request,
            "body_b64": None, "signature_b64": None, "health_pair": admitted["health"], "runtime_evidence": evidence}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "accept-source", "prepare-package"))
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--intent")
    parser.add_argument("--request")
    args = parser.parse_args()
    release.require(os.geteuid() == 0, "private publication preparation requires root")
    os.umask(0o077)
    now = datetime.now(timezone.utc)
    evidence = private_document(args.evidence)
    root = Path(__file__).resolve().parents[2]
    if args.command == "check":
        result = release.validate_evidence(evidence, source=args.source, now=now)
        print(json.dumps({"status": "NATIVE_PUBLICATION_EVIDENCE_QUALIFIED", "runtime_source": result["source"],
                          "publication_source": result["publication_source"], "backup_guarantees": result["backup_guarantees"]}))
        return
    release.require(args.output is not None, "explicit output required")
    if args.command == "accept-source":
        result = accept_source(evidence, args.source, root=root, now=now)
        # Recheck the short admission window immediately before durable acceptance.
        grant = evidence["grant"]["payload"]
        bridge.verify(evidence["source_equivalence"], root=root, publication_source=args.source,
                      runtime_source=grant["code"]["commit"], runtime_image_digest=grant["host"]["image_digest"],
                      runtime_grant_sha256=release.digest(evidence["grant"]), now=datetime.now(timezone.utc))
    else:
        result = health_package(evidence, args.source, args.intent, args.request, now=now)
    write_exclusive(args.output, result)
    print(json.dumps({"status": "PRIVATE_PREPARATION_WRITTEN", "command": args.command,
                      "sha256": release.digest(result), "public_write": False, "runtime_authority_changed": False}))


if __name__ == "__main__":
    main()
