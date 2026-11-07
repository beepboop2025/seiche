"""Signed metadata-source binding; never grants runtime execution or writer authority.

The caller protects and atomically persists an acceptance only after live
verification. An acceptance supplied from an untrusted store is not authority.
This module reads Git objects and verifies signatures; it imports no candidate
source and performs no signing, network, runtime or publication operations.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

SCHEMA = "seiche.native-publication-source.v1"
APPROVAL_SCHEMA = "seiche.native-publication-source-approval.v1"
ACCEPTANCE_SCHEMA = "seiche.native-publication-source-acceptance.v1"
SIGNATURE_NAMESPACE = "seiche-native-publication-source-v1"
SIGNER_PRINCIPAL = "seiche-native-publication-release"
OWNER_PUBLIC = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBuJV6o8YL2XXR9q4vcwpHuc2z1GEBawSmrJWGrgwzFV"
MAX_LIFETIME = timedelta(hours=4)
MAX_BYTES = 2 * 1024 * 1024
RUNTIME_DIFFERENCE_SOURCE = "795ace8fa691674de022e1f83e5545769a388c6c"
RUNTIME_DIFFERENCE_TREE = "d6bd512e5c87812de62e547fecd1c59ec14e7533"
# These exact five immutable blob pairs are eligible for explicit owner review.
# No directory-wide test exception exists. Modes must remain 100644.
REVIEWABLE_DIFFERENCES = {
    "backend/tests/fixtures/liquilens_failure_radar_live_2026-11-03.json":
        (None, "b1969e025363756732d93e916e0acab6b34ae0f1"),
    "backend/tests/fixtures/liquilens_failure_radar_paused_pr201.json":
        (None, "47be89308e91fd8b8e88e5be3697d68f5d141c0d"),
    "backend/tests/test_catalog_publication_gate.py":
        ("80b6e0057a2e5503b49d6bc2a0e72d47df3b0bcf", "0b80749dc27f502cd130553e7cd46742e1916d53"),
    "backend/tests/test_distribution_artifacts.py":
        ("137e81556e8cd59f3a055049689b676548f48ead", "2ed2cfd3e04769fed2faeedc295bea6138861a72"),
    "backend/tests/test_rissaga_radar_tier_pause.py":
        (None, "128757e23d47347106504b7250897fd2364fcb84"),
}
CONTROLLER_PATHS = ("ops", ".github/workflows/publish.yml", ".github/release-signers.pub")
LIMITS = {"runtime_execution_authority": False, "writer_authority": False,
          "raw_data_publication": False}


class SourceBindingError(ValueError):
    pass


def require(value, message):
    if not value:
        raise SourceBindingError(message)


def canonical(value):
    try:
        body = (json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError, RecursionError):
        raise SourceBindingError("source binding is not canonical JSON") from None
    require(len(body) <= MAX_BYTES, "source binding is oversized")
    return body


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _time(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise SourceBindingError("source binding timestamp is malformed") from None
    require(isinstance(value, datetime) and value.tzinfo is not None
            and value.utcoffset() == timedelta(0), "source binding timestamp must use UTC")
    return value.astimezone(timezone.utc)


def _hex(value, length):
    require(isinstance(value, str) and re.fullmatch("[0-9a-f]{" + str(length) + "}", value),
            "source binding identity is malformed")
    return value


def _git(root, *args):
    environment = {"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0",
                   "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0"}
    command = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
               "-C", str(root), *args]
    try:
        result = subprocess.run(command, capture_output=True, timeout=30, env=environment)
    except (OSError, subprocess.TimeoutExpired):
        raise SourceBindingError("source Git observation failed") from None
    require(result.returncode == 0 and len(result.stdout) <= 16 * MAX_BYTES,
            "source Git observation failed")
    return result.stdout


def _identity(root, source):
    _hex(source, 40)
    require(_git(root, "cat-file", "-t", source).strip() == b"commit", "source is not a commit")
    tree = _git(root, "rev-parse", source + "^{tree}").decode().strip()
    return {"commit": source, "tree": _hex(tree, 40)}


def _inventory(root, source, paths):
    rows = _git(root, "ls-tree", "-r", "-z", "--full-tree", source, "--", *paths)
    result = []
    for row in rows.split(b"\0"):
        if not row:
            continue
        metadata, raw_path = row.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8")
        parts = PurePosixPath(path)
        require(not parts.is_absolute() and ".." not in parts.parts and str(parts) == path
                and not any(ord(c) < 32 for c in path), "inventory path is malformed")
        require(kind == "blob" and mode in {"100644", "100755"},
                "inventory contains a symlink, gitlink or unsupported mode")
        result.append({"path": path, "mode": mode, "type": kind, "oid": _hex(oid, 40)})
    result.sort(key=lambda item: item["path"])
    require(len({item["path"] for item in result}) == len(result), "inventory has duplicate paths")
    return result


def _manifest(root, publication_source, runtime_source):
    root = Path(root).resolve(strict=True)
    publication, runtime = _identity(root, publication_source), _identity(root, runtime_source)
    require(_git(root, "rev-parse", "HEAD").decode().strip() == publication_source,
            "checkout is not the publication source")
    require(not _git(root, "status", "--porcelain=v1", "--untracked-files=all"),
            "publication checkout is not clean")
    publication_backend = _inventory(root, publication_source, ("backend",))
    runtime_backend = _inventory(root, runtime_source, ("backend",))
    require(publication_backend and runtime_backend, "complete backend inventory is missing")
    before = {item["path"]: item for item in runtime_backend}
    after = {item["path"]: item for item in publication_backend}
    differences = [{"path": path, "runtime": before.get(path), "publication": after.get(path)}
                   for path in sorted(before.keys() | after.keys()) if before.get(path) != after.get(path)]
    if differences:
        require(runtime == {"commit": RUNTIME_DIFFERENCE_SOURCE, "tree": RUNTIME_DIFFERENCE_TREE},
                "backend differences are outside the reviewed runtime")
        require({item["path"] for item in differences} == set(REVIEWABLE_DIFFERENCES),
                "backend difference set is not the exact five-path review")
        for difference in differences:
            expected = REVIEWABLE_DIFFERENCES[difference["path"]]
            for side, oid in zip(("runtime", "publication"), expected, strict=True):
                observed = difference[side]
                require(observed is None if oid is None else observed == {
                    "path": difference["path"], "mode": "100644", "type": "blob", "oid": oid},
                    "backend difference blob or mode is not explicitly reviewed")
    controllers = _inventory(root, publication_source, CONTROLLER_PATHS)
    require(any(item["path"].startswith("ops/") for item in controllers),
            "controller inventory is missing")
    return {"schema": "seiche.native-publication-source-manifest.v1",
            "publication": publication, "runtime": runtime,
            "publication_backend": publication_backend, "runtime_backend": runtime_backend,
            "publication_controller": controllers, "differences": differences,
            "publication_backend_sha256": digest(publication_backend),
            "runtime_backend_sha256": digest(runtime_backend),
            "controller_manifest_sha256": digest(controllers)}


def prepare(root, publication_source, runtime_source, issued_at, expires_at, *,
            runtime_image_digest, runtime_grant_sha256):
    require(isinstance(runtime_image_digest, str) and runtime_image_digest.startswith("sha256:"),
            "runtime image digest is malformed")
    _hex(runtime_image_digest[7:], 64); _hex(runtime_grant_sha256, 64)
    issued, expires = _time(issued_at), _time(expires_at)
    require(timedelta(0) < expires - issued <= MAX_LIFETIME, "source binding lifetime exceeds four hours")
    manifest = _manifest(root, publication_source, runtime_source)
    payload = {"schema": SCHEMA, "issued_at": issued.isoformat(), "expires_at": expires.isoformat(),
               "publication": manifest["publication"], "runtime": manifest["runtime"],
               "runtime_image_digest": runtime_image_digest, "runtime_grant_sha256": runtime_grant_sha256,
               "manifest": manifest, "manifest_sha256": digest(manifest), "limits": dict(LIMITS)}
    canonical(payload)
    return payload, deepcopy(manifest)


def _signature(payload, signature):
    require(isinstance(signature, str) and 0 < len(signature) <= 8192
            and signature.startswith("-----BEGIN SSH SIGNATURE-----"), "owner signature is malformed")
    try:
        with tempfile.TemporaryDirectory(prefix="native-source-signature-") as name:
            directory = Path(name)
            (directory / "signers").write_text(SIGNER_PRINCIPAL + " " + OWNER_PUBLIC + "\n")
            (directory / "signature").write_text(signature)
            result = subprocess.run(
                ["ssh-keygen", "-Y", "verify", "-f", str(directory / "signers"),
                 "-I", SIGNER_PRINCIPAL, "-n", SIGNATURE_NAMESPACE, "-s", str(directory / "signature")],
                input=canonical(payload), capture_output=True, timeout=15,
                env={"PATH": os.defpath})
    except (OSError, subprocess.TimeoutExpired):
        raise SourceBindingError("owner source signature verification failed") from None
    require(result.returncode == 0, "owner source signature is invalid")


def _verified(envelope, *, root, publication_source, runtime_source,
              runtime_image_digest, runtime_grant_sha256):
    require(isinstance(envelope, dict) and set(envelope) == {"schema", "payload", "signature"}
            and envelope["schema"] == APPROVAL_SCHEMA, "source approval schema differs")
    payload = envelope["payload"]
    require(isinstance(payload, dict) and "issued_at" in payload and "expires_at" in payload,
            "source approval payload is malformed")
    _signature(payload, envelope["signature"])
    expected, _ = prepare(root, publication_source, runtime_source,
                          payload["issued_at"], payload["expires_at"],
                          runtime_image_digest=runtime_image_digest,
                          runtime_grant_sha256=runtime_grant_sha256)
    require(canonical(payload) == canonical(expected), "source approval identity or complete inventory differs")
    return deepcopy(payload)


def verify(envelope, *, root, publication_source, runtime_source,
           runtime_image_digest, runtime_grant_sha256, now):
    payload = _verified(envelope, root=root, publication_source=publication_source,
                        runtime_source=runtime_source, runtime_image_digest=runtime_image_digest,
                        runtime_grant_sha256=runtime_grant_sha256)
    require(_time(payload["issued_at"]) <= _time(now) < _time(payload["expires_at"]),
            "source approval is future dated or expired")
    return payload


def validate_acceptance(envelope, acceptance, *, root, publication_source, runtime_source,
                        runtime_image_digest, runtime_grant_sha256, now):
    payload = _verified(envelope, root=root, publication_source=publication_source,
                        runtime_source=runtime_source, runtime_image_digest=runtime_image_digest,
                        runtime_grant_sha256=runtime_grant_sha256)
    require(isinstance(acceptance, dict) and set(acceptance) == {
        "schema", "approval_sha256", "publication", "runtime", "runtime_image_digest",
        "runtime_grant_sha256", "accepted_at"}, "source acceptance schema differs")
    require(acceptance["schema"] == ACCEPTANCE_SCHEMA and acceptance["approval_sha256"] == digest(envelope)
            and all(acceptance[key] == payload[key] for key in (
                "publication", "runtime", "runtime_image_digest", "runtime_grant_sha256")),
            "source acceptance is for a different exact source pair or runtime")
    accepted, observed = _time(acceptance["accepted_at"]), _time(now)
    require(_time(payload["issued_at"]) <= accepted < _time(payload["expires_at"])
            and accepted <= observed, "source approval was not accepted within its signed window")
    return payload
