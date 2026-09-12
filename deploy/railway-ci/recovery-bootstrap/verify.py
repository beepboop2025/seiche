"""Recover an activation anchor from explicitly reviewed, attested locked receipts.

This manual-only path supplies identity for a new governed export. It never
claims that a historical receipt satisfies the current backup freshness check.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zlib

REPOSITORY = "beepboop2025/seiche"
WORKFLOW = ".github/workflows/railway-stateful-recovery.yml"
MAX_BYTES = 512 * 1024
MEMBERS = ("recovery-receipt.json", "activation-receipt.json",
           "candidate-receipt.json", "shadow-receipt.json", "request.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def document(body):
    require(0 < len(body) <= MAX_BYTES, "document exceeds its bound")
    def unique(pairs):
        value = {}
        for name, item in pairs:
            require(name not in value, "duplicate JSON field")
            value[name] = item
        return value
    value = json.loads(body, object_pairs_hook=unique)
    require(isinstance(value, dict), "document must be an object")
    return value


def stamp(value):
    require(isinstance(value, str), "timestamp is absent")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "timestamp lacks timezone")
    return result.astimezone(timezone.utc)


def manifest(body, expected_digest, *, source, deployment, prefix, now):
    require(re.fullmatch(r"[0-9a-f]{64}", expected_digest or ""), "manifest digest is absent")
    require(digest(body) == expected_digest, "protected manifest digest differs")
    value = document(body)
    require(set(value) == {"schema", "source", "deployment", "created_at", "expires_at",
                            "run_id", "run_attempt", "offsite"}, "manifest fields differ")
    require(value["schema"] == "seiche.durable-recovery-bootstrap.v1", "manifest schema differs")
    require(re.fullmatch(r"[0-9a-f]{40}", source or "") and value["source"] == source,
            "manifest source differs from current production")
    require(re.fullmatch(r"[0-9a-f-]{36}", deployment or "") and value["deployment"] == deployment,
            "manifest deployment differs from current production")
    created, expires = stamp(value["created_at"]), stamp(value["expires_at"])
    require(created <= now < expires <= created + timedelta(hours=4), "reviewed bootstrap window expired or is future")
    require(all(type(value[k]) is int and value[k] > 0 for k in ("run_id", "run_attempt")),
            "attestation invocation is invalid")
    ref = value["offsite"]
    require(isinstance(ref, dict) and set(ref) == {"key", "version_id", "sha256", "size"},
            "immutable off-site reference is invalid")
    require(isinstance(ref["key"], str) and re.fullmatch(
        re.escape(prefix) + r"/[0-9]{8}T[0-9]{6}Z/[0-9a-f]{64}/offsite-receipt.json", ref["key"]),
        "off-site reference is outside the reviewed prefix")
    require(isinstance(ref["version_id"], str) and ref["version_id"] != "null" and
            re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,1024}", ref["version_id"]), "immutable version is invalid")
    require(re.fullmatch(r"[0-9a-f]{64}", str(ref["sha256"])), "off-site digest is invalid")
    require(type(ref["size"]) is int and 0 < ref["size"] <= MAX_BYTES, "off-site size is invalid")
    return value


def attestation_identity(results, *, name, sha256, policy):
    invocation = f"https://github.com/{REPOSITORY}/actions/runs/{policy['run_id']}/attempts/{policy['run_attempt']}"
    for result in results:
        verified = result.get("verificationResult", {})
        certificate = verified.get("signature", {}).get("certificate", {})
        statement = verified.get("statement", {})
        predicate = statement.get("predicate", {})
        if (certificate.get("issuer") == "https://token.actions.githubusercontent.com" and
                certificate.get("subjectAlternativeName") == f"https://github.com/{REPOSITORY}/{WORKFLOW}@refs/heads/main" and
                certificate.get("runnerEnvironment") == "github-hosted" and
                certificate.get("sourceRepositoryDigest") == policy["source"] and
                certificate.get("sourceRepositoryURI") == "https://github.com/" + REPOSITORY and
                certificate.get("sourceRepositoryRef") == "refs/heads/main" and
                certificate.get("runInvocationURI") == invocation and
                statement.get("subject") == [{"name": name, "digest": {"sha256": sha256}}] and
                predicate.get("runDetails", {}).get("metadata", {}).get("invocationId") == invocation):
            return
    raise ValueError("verified attestation does not bind the exact original subject and invocation")


def recover(policy, *, storage, recovery, verify_attestation, environment, output, now,
            clock=lambda: datetime.now(timezone.utc)):
    def fetch(name, reference):
        actual, _head = storage.head(reference["key"], reference["version_id"])
        require(actual == reference, "locked immutable reference differs")
        body = storage.fetch(name, reference)
        require(len(body) == reference["size"] and digest(body) == reference["sha256"],
                "downloaded immutable bytes differ")
        return body

    raw = fetch("offsite-receipt.json", policy["offsite"])
    offsite = document(raw)
    verify_attestation("offsite-receipt.json", raw, policy)
    require(offsite.get("bucket") == environment["S3_BUCKET"] and
            offsite.get("prefix") == environment["S3_PREFIX"] and
            offsite.get("commit") == policy["source"], "historical receipt location or source differs")
    key_root = f"{environment['S3_PREFIX']}/{offsite.get('snapshot_id')}/{offsite.get('request_id')}"
    require(policy["offsite"]["key"] == key_root + "/offsite-receipt.json", "off-site key is not self-consistent")
    bodies, records = {}, {}
    for name in MEMBERS:
        reference = offsite["objects"][name]
        require(reference["key"] == key_root + "/" + name, "receipt member escapes its exact backup")
        require(type(reference["size"]) is int and 0 < reference["size"] <= MAX_BYTES,
                "receipt member exceeds its bound")
        bodies[name] = fetch(name, reference)
        records[name] = document(bodies[name])
    original = records["recovery-receipt.json"]
    verify_attestation("recovery-receipt.json", bodies["recovery-receipt.json"], policy)
    require(original.get("commit") == policy["source"] and
            original.get("railway", {}).get("deployment_id") == policy["deployment"],
            "historical backup belongs to a different current deployment")
    recovery.validate_receipt(original, request=records["request.json"],
                              activation_receipt=records["activation-receipt.json"],
                              candidate_receipt=records["candidate-receipt.json"],
                              shadow_receipt=records["shadow-receipt.json"])
    recovery.validate_offsite_receipt(offsite, recovery_receipt=original, now=now, require_fresh=False)
    activation_sha = digest(bodies["activation-receipt.json"])
    require(activation_sha == original["activation_receipt_sha256"], "historical activation hash differs")
    finished = clock()
    require(now <= finished < stamp(policy["expires_at"]), "reviewed bootstrap expired during verification")
    output.mkdir(parents=True, exist_ok=True)
    for name, body in {"bootstrap-activation-receipt.json": bodies["activation-receipt.json"],
                       "activation.sha256": (activation_sha + "\n").encode(),
                       "durable-bootstrap-proof.json": (json.dumps({
                           "status": "pass", "purpose": "historical_activation_anchor",
                           "source": policy["source"], "deployment": policy["deployment"],
                           "observed_at": finished.isoformat(), "run_id": policy["run_id"],
                           "run_attempt": policy["run_attempt"], "activation_sha256": activation_sha,
                           "current_backup_proven": False, "s3_objects_written": False,
                           "immutable_metadata_objects_verified": 6,
                       }, sort_keys=True) + "\n").encode()}.items():
        with (output / name).open("xb") as stream:
            stream.write(body)


def main():
    os.umask(0o077)
    require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and
            os.environ.get("GITHUB_REF") == "refs/heads/main" and
            os.environ.get("CONFIRMATION") == "EXPORT_WITHOUT_AUTHORITY_CHANGE", "manual recovery scope is absent")
    repo = Path(os.environ["GITHUB_WORKSPACE"])
    sys.path.insert(0, str(repo / "backend"))
    sys.path.insert(0, str(repo / "deploy/railway-ci/recovery-controller"))
    import attest
    from seiche import stateful_recovery
    spec = importlib.util.spec_from_file_location("bootstrap_storage", repo / "ops/railway/resume_recovery.py")
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    encoded = os.environ.pop("DURABLE_BOOTSTRAP_ZLIB_BASE64")
    require(0 < len(encoded) <= 64 * 1024, "protected bootstrap transport exceeds its bound")
    decoder = zlib.decompressobj()
    body = decoder.decompress(base64.b64decode(encoded, validate=True), MAX_BYTES + 1)
    require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail, "bootstrap transport is incomplete")
    policy = manifest(body, os.environ["DURABLE_BOOTSTRAP_SHA256"], source=os.environ["RELEASE_SHA"],
                      deployment=os.environ["DEPLOYMENT_ID"], prefix=os.environ["S3_PREFIX"], now=datetime.now(timezone.utc))
    public = {k: os.environ[k] for k in ("PATH", "HOME")}
    storage_env = {**public, **{k: os.environ[k] for k in (
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION", "S3_ENDPOINT", "S3_BUCKET", "S3_PREFIX", "S3_SSE_C_KEY_B64")},
        "AWS_EC2_METADATA_DISABLED": "true", "RUNNER_TEMP": os.environ["RUNNER_TEMP"], "GITHUB_WORKSPACE": str(repo)}
    original.validate_location(storage_env)
    with tempfile.TemporaryDirectory(prefix="durable-bootstrap-", dir=os.environ["RUNNER_TEMP"]) as temporary:
        root = Path(temporary)
        storage = attest.Storage(root, storage_env, original)
        def verify_attestation(name, raw, selected):
            result = subprocess.run([
                "gh", "attestation", "verify", str(root / name), "--repo", REPOSITORY,
                "--signer-workflow", REPOSITORY + "/" + WORKFLOW, "--source-ref", "refs/heads/main",
                "--source-digest", selected["source"], "--format", "json"],
                env={**public, "GH_TOKEN": os.environ["GH_TOKEN"]}, capture_output=True, timeout=120, check=False)
            require(result.returncode == 0 and 0 < len(result.stdout) <= 2 * 1024 * 1024,
                    "original GitHub attestation verification failed")
            attestation_identity(json.loads(result.stdout), name=name, sha256=digest(raw), policy=selected)
        recover(policy, storage=storage, recovery=stateful_recovery, verify_attestation=verify_attestation,
                environment=storage_env, output=Path(os.environ["EVIDENCE_ROOT"]) / "proof", now=datetime.now(timezone.utc))
    print("DURABLE_RECOVERY_BOOTSTRAP_VERIFIED historical_anchor_only=true new_export_required=true")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("DURABLE_RECOVERY_BOOTSTRAP_FAIL error_type=" + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
