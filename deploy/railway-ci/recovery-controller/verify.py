"""Read locked S3 versions and repeat the existing isolated recovery restore."""

import ctypes
import base64
import re
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parent
TRUSTED = ROOT / "trusted"
RESTORE_UID = 65532
MAX_DOWNLOAD_BYTES = 3 * 1024**3
INPUT_NAMES = {
    "AWS_ACCESS_KEY_ID": "RECOVERY_S3_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY": "RECOVERY_S3_SECRET_ACCESS_KEY",
    "AWS_DEFAULT_REGION": "RECOVERY_S3_REGION",
    "S3_ENDPOINT": "RECOVERY_S3_ENDPOINT",
    "S3_BUCKET": "RECOVERY_S3_BUCKET",
    "S3_PREFIX": "RECOVERY_S3_PREFIX",
    "S3_SSE_C_KEY_B64": "RECOVERY_S3_SSE_C_KEY_B64",
}


def digest(body):
    return hashlib.sha256(body).hexdigest()


def event(kind, **values):
    print(json.dumps({"event": kind, **values}, sort_keys=True), flush=True)


def environment(home):
    return {"PATH": "/controller/native-bin:/usr/local/bin:/usr/lib/postgresql/18/bin:/usr/bin:/bin",
            "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(TRUSTED / "backend"), "OPENBLAS_NUM_THREADS": "2",
            "OMP_NUM_THREADS": "2", "AWS_EC2_METADATA_DISABLED": "true"}


def drop(uid):
    def apply():
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024**3, 8 * 1024**3))
        os.setgroups([])
        os.setgid(uid)
        os.setuid(uid)
        os.umask(0o022)
    return apply


def stop_uid(uid):
    for _ in range(40):
        subprocess.run(["pkill", "-KILL", "-u", str(uid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
        if subprocess.run(["pgrep", "-u", str(uid)], stdout=subprocess.DEVNULL, check=False).returncode == 1:
            return
        time.sleep(0.25)
    raise RuntimeError("isolated restore processes did not quiesce")


def run(arguments, *, env, uid=None, timeout=120, cwd=None):
    process = subprocess.Popen(arguments, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                               close_fds=True, start_new_session=True,
                               preexec_fn=drop(uid) if uid is not None else None)
    try:
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise RuntimeError("bounded verification command timed out") from None
        if code:
            raise RuntimeError("bounded verification command failed: " + Path(arguments[0]).name)
    finally:
        if uid == RESTORE_UID:
            stop_uid(uid)


def modules():
    sys.path.insert(0, str(TRUSTED / "backend"))
    from seiche import stateful_recovery
    spec = importlib.util.spec_from_file_location("reviewed_storage", TRUSTED / "ops/railway/resume_recovery.py")
    storage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(storage)
    return stateful_recovery, storage


def validate_case(policy, env, recovery, storage):
    case = ROOT / "case"
    offsite = json.loads((case / "offsite-receipt.json").read_bytes())
    original = json.loads((case / "recovery-receipt.json").read_bytes())
    if original["request_id"] != policy["request_id"] or original["commit"] != policy["application_source"]:
        raise ValueError("pinned recovery identity differs")
    if original["railway"]["deployment_id"] != policy["application_deployment"]:
        raise ValueError("pinned recovery deployment differs")
    recovery.validate_offsite_receipt(offsite, recovery_receipt=original, now=datetime.now(timezone.utc))
    if set(offsite["objects"]) != storage.OBJECT_NAMES:
        raise ValueError("pinned object set differs from the original recovery contract")
    if offsite["bucket"] != env["S3_BUCKET"] or offsite["prefix"] != env["S3_PREFIX"]:
        raise ValueError("protected storage location differs from verified receipt")
    storage.validate_location(env)
    key_root = f"{offsite['prefix']}/{offsite['snapshot_id']}/{offsite['request_id']}"
    total = 0
    for name, item in offsite["objects"].items():
        if item["key"] != f"{key_root}/{name}":
            raise ValueError("object key escaped the exact immutable recovery")
        storage.validate_object(item["version_id"], item["sha256"], item["size"])
        total += item["size"]
    if total > MAX_DOWNLOAD_BYTES:
        raise ValueError("immutable download exceeds the reviewed byte bound")
    item = policy["offsite_object"]
    if item["key"] != key_root + "/offsite-receipt.json" or item["sha256"] != digest((case / "offsite-receipt.json").read_bytes()):
        raise ValueError("offsite receipt object differs from its pinned bytes")
    storage.validate_object(item["version_id"], item["sha256"], item["size"], maximum=512 * 1024)
    return offsite, total


def validate_proof_tree(root, object_names):
    expected = {"reverse-restore.json", "railway-reverse-restore.json"}
    expected.update("resume-offsite-heads/" + name.replace("/", "_") + ".json"
                    for name in (*object_names, "offsite-receipt.json"))
    found = set()
    total = 0
    for path in root.rglob("*"):
        metadata = path.lstat()
        name = str(path.relative_to(root))
        if stat.S_ISDIR(metadata.st_mode):
            if name != "resume-offsite-heads":
                raise ValueError("unexpected recovery proof directory")
            continue
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                name not in expected or not 0 < metadata.st_size <= 512 * 1024):
            raise ValueError("recovery proof is not a bounded expected regular file")
        found.add(name)
        total += metadata.st_size
    if found != expected or total > 8 * 1024 * 1024:
        raise ValueError("recovery proof tree is incomplete or exceeds its byte bound")


def start_postgres(root):
    uid = pwd.getpwnam("postgres").pw_uid
    if uid == 0 or uid == RESTORE_UID:
        raise RuntimeError("PostgreSQL identity is not independently isolated")
    root.mkdir(mode=0o700)
    os.chown(root, uid, uid)
    env = environment(root)
    run(["/usr/lib/postgresql/18/bin/initdb", "-D", str(root / "data"), "-U", "postgres", "--auth=trust"], env=env, uid=uid)
    run(["/usr/lib/postgresql/18/bin/pg_ctl", "-D", str(root / "data"), "-l", str(root / "server.log"),
         "-o", "-h 127.0.0.1 -k " + str(root) + " -p 5432 -F", "-w", "start"], env=env, uid=uid)
    return uid, env



INDEX_SCHEMA = "seiche.railway-recovery-execution.v1"
INDEX_DOMAIN = b"seiche.railway-recovery-execution.v1\0"
CONTROL_KEY_IDS = frozenset({
    "2be24b7ea07b1596f9e6bf95c22ee1425532b28e04b3d3cf4eb263fce7142987",
    "cf08c9956205cd0151ca4d71edbf65af6e82ef802a8afbfaef27b6f3be43e4f3",
})
INDEX_FIELDS = frozenset({
    "schema", "execution_platform", "date", "observed_at", "repository", "governance_workflow",
    "controller_source", "controller_manifest_sha256", "controller_image_digest", "controller_deployment_id", "controller_replica_id",
    "controller_project_id", "controller_environment_id", "controller_service_id", "monitor_proof_sha256",
    "application_project_id", "application_environment_id", "application_service_id", "application_deployment_id",
    "application_source", "application_replica_id", "request_id", "snapshot_id", "objects_verified",
    "recovery_receipt", "offsite_receipt", "reverse_restore_proof", "postgres_counts", "postgres_count_floor",
    "authority_changed", "research_only", "can_publish", "can_execute",
    "storage_endpoint", "storage_bucket", "storage_prefix",
})
INDEX_OBJECTS = {
    "recovery_receipt": "recovery-receipt.json",
    "offsite_receipt": "offsite-receipt.json",
    "reverse_restore_proof": "proof/reverse-restore.json",
}


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def index_signing_bytes(payload):
    if not isinstance(payload, dict) or set(payload) != INDEX_FIELDS:
        raise ValueError("native execution metadata fields differ from the closed contract")
    return INDEX_DOMAIN + canonical(payload)


def evidence_signer(pem, public_hex):
    """Validate the independent evidence key before any governed export begins."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("native evidence key must be Ed25519")
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if public.hex() != public_hex:
        raise ValueError("native evidence signer differs from the independently pinned key")
    # No production signer may be reused for this evidence-only purpose.
    if digest(public) in CONTROL_KEY_IDS:
        raise ValueError("production control key cannot sign native execution evidence")
    return key, public


def sign_execution_index(payload, pem, public_hex):
    """This evidence key never signs production control commands or acknowledgments."""
    key, public = evidence_signer(pem, public_hex)
    signature = key.sign(index_signing_bytes(payload))
    return {"payload": payload, "key_id": digest(public), "signature": base64.b64encode(signature).decode()}


def validate_execution_index(envelope, *, policy, current_runtime, now):
    """Verify a fresh exact-target native claim before any GitHub attestation."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "key_id", "signature"}:
        raise ValueError("native execution envelope differs")
    public = bytes.fromhex(policy["execution_public_key"])
    if len(public) != 32 or envelope["key_id"] != digest(public) or digest(public) in CONTROL_KEY_IDS:
        raise ValueError("native evidence key identity differs")
    signature = base64.b64decode(envelope["signature"], validate=True)
    if len(signature) != 64:
        raise ValueError("native evidence signature length differs")
    payload = envelope["payload"]
    Ed25519PublicKey.from_public_bytes(public).verify(signature, index_signing_bytes(payload))
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("native proof verifier needs an aware clock")
    now = now.astimezone(timezone.utc)
    checked = datetime.fromisoformat(current_runtime["observed_at"].replace("Z", "+00:00"))
    if (current_runtime["status"] != "RUNNING" or checked.tzinfo is None or
            checked.utcoffset() is None or not -120 <= (now - checked).total_seconds() <= 300):
        raise ValueError("current production runtime proof is missing, stopped or stale")
    observed = datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00"))
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("native evidence observation has no timezone")
    observed = observed.astimezone(timezone.utc)
    age = (now - observed).total_seconds()
    if (payload["date"] != now.date().isoformat() or observed.date().isoformat() != payload["date"] or
            age < -120 or age > 26 * 3600):
        raise ValueError("native execution day or freshness differs; no stale fallback is allowed")
    fixed = {"schema": INDEX_SCHEMA, "execution_platform": "railway", "repository": "beepboop2025/seiche",
             "governance_workflow": "beepboop2025/seiche/.github/workflows/railway-stateful-recovery.yml",
             "objects_verified": 15, "authority_changed": False, "research_only": True,
             "can_publish": False, "can_execute": False,
             **{name: policy[name] for name in ("controller_source", "controller_manifest_sha256", "controller_image_digest",
                 "storage_endpoint", "storage_bucket", "storage_prefix", "controller_project_id", "controller_environment_id", "controller_service_id",
                 "application_project_id", "application_environment_id", "application_service_id")}}
    if any(type(payload[name]) is not type(value) or payload[name] != value for name, value in fixed.items()):
        raise ValueError("native execution source, target or safety state differs")
    for name in ("controller_deployment_id", "controller_replica_id", "application_deployment_id", "application_replica_id"):
        if not isinstance(payload[name], str) or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", payload[name]) is None:
            raise ValueError("native execution identity is not a fixed UUID")
    for field, runtime_field in (("application_deployment_id", "deployment_id"), ("application_source", "source"), ("application_replica_id", "replica_id")):
        if payload[field] != current_runtime[runtime_field]:
            raise ValueError("native execution differs from the current running production identity")
    for name, length in (("application_source", 40), ("request_id", 64), ("monitor_proof_sha256", 64)):
        if not isinstance(payload[name], str) or re.fullmatch("[0-9a-f]{" + str(length) + "}", payload[name]) is None:
            raise ValueError("native evidence digest or source differs")
    if not isinstance(payload["snapshot_id"], str) or re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", payload["snapshot_id"]) is None:
        raise ValueError("native snapshot identity differs")
    snapshot_time = datetime.strptime(payload["snapshot_id"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    if snapshot_time.date() != observed.date() or snapshot_time > observed:
        raise ValueError("native snapshot is not from the verified UTC execution day")
    prefix = policy["storage_prefix"] + "/" + payload["snapshot_id"] + "/" + payload["request_id"]
    for label, name in INDEX_OBJECTS.items():
        item = payload[label]
        if not isinstance(item, dict) or set(item) != {"key", "version_id", "sha256", "size"}:
            raise ValueError("native receipt reference fields differ")
        if (item["key"] != prefix + "/" + name or not isinstance(item["version_id"], str) or
                item["version_id"] == "null" or re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,1024}", item["version_id"]) is None or
                not isinstance(item["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None or
                type(item["size"]) is not int or not 0 < item["size"] <= 512 * 1024):
            raise ValueError("native receipt reference is not a bounded exact immutable version")
    counts, floor = payload["postgres_counts"], payload["postgres_count_floor"]
    if (not isinstance(counts, list) or not isinstance(floor, list) or len(counts) != 4 or len(floor) != 4 or
            any(type(value) is not int or value < 0 for value in (*counts, *floor)) or
            any(actual < minimum for actual, minimum in zip(counts, floor))):
        raise ValueError("native restore counts do not satisfy the original floors")
    return payload


def validate_index_receipts(payload, *, bodies, heads, recovery, policy, now):
    """Bind the signed claim to exact downloaded original receipt bytes and S3 HEADs."""
    if set(bodies) != set(INDEX_OBJECTS) or set(heads) != set(INDEX_OBJECTS):
        raise ValueError("native index needs all three original immutable receipts")
    documents = {}
    for name in INDEX_OBJECTS:
        body, reference, head = bodies[name], payload[name], heads[name]
        if (not isinstance(body, bytes) or len(body) != reference["size"] or
                digest(body) != reference["sha256"] or head.get("VersionId") != reference["version_id"] or
                head.get("ContentLength") != reference["size"] or
                head.get("Metadata", {}).get("sha256") != reference["sha256"] or
                head.get("ObjectLockMode") != "COMPLIANCE" or head.get("SSECustomerAlgorithm") != "AES256"):
            raise ValueError("native receipt bytes or immutable locked version differs")
        retain = datetime.fromisoformat(head["ObjectLockRetainUntilDate"].replace("Z", "+00:00"))
        if retain.tzinfo is None or (retain - now).total_seconds() < 29 * 86400:
            raise ValueError("native receipt retention is below the original bound")
        documents[name] = json.loads(body)
    original, offsite, proof = (documents[name] for name in INDEX_OBJECTS)
    recovery.validate_offsite_receipt(offsite, recovery_receipt=original, now=now)
    if (offsite["bucket"] != policy["storage_bucket"] or offsite["prefix"] != policy["storage_prefix"] or
            len(offsite["objects"]) != 15 or original["snapshot"]["id"] != payload["snapshot_id"] or
            original["request_id"] != payload["request_id"] or original["commit"] != payload["application_source"]):
        raise ValueError("native execution differs from the original recovery identity")
    for field in ("project", "environment", "service", "deployment"):
        if original["railway"][field + "_id"] != payload["application_" + field + "_id"]:
            raise ValueError("native execution differs from the original application target")
    for label in ("recovery_receipt", "reverse_restore_proof"):
        if offsite["objects"][INDEX_OBJECTS[label]] != payload[label]:
            raise ValueError("native execution reference differs from the closed offsite manifest")
    proof_fixed = {"schema": "seiche.railway-reverse-restore-proof.v1",
                   "repository": payload["repository"], "workflow": payload["governance_workflow"],
                   "commit": payload["application_source"], "request_id": payload["request_id"],
                   "authority_changed": False, "research_only": True, "can_publish": False, "can_execute": False}
    if any(type(proof.get(name)) is not type(value) or proof.get(name) != value for name, value in proof_fixed.items()):
        raise ValueError("original reverse restore identity or authority differs")
    if (proof["filesystem_tree_sha256"] != original["filesystem"]["tree_sha256"] or
            proof["nbs_full_store_audit_result"] != original["filesystem"]["nbs_full_store_audit_result"] or
            proof["palimpsest_china_state"] != original["palimpsest_china_state"]):
        raise ValueError("original reverse restore filesystem audits differ")
    if (offsite["recovery_receipt_sha256"] != payload["recovery_receipt"]["sha256"] or
            offsite["reverse_restore_proof_sha256"] != payload["reverse_restore_proof"]["sha256"] or
            proof["recovery_receipt_sha256"] != payload["recovery_receipt"]["sha256"] or
            proof["postgres_counts"] != payload["postgres_counts"] or
            proof["postgres_count_floor"] != payload["postgres_count_floor"] or
            original["snapshot"]["critical_table_count_floor"] != payload["postgres_count_floor"]):
        raise ValueError("native restore claim differs from the original proof or count floors")
    return documents


def main():
    if os.environ.get("RECOVERY_OPERATION") == "export-recurring":
        import recurring
        recurring.main()
        return
    if os.geteuid() != 0:
        raise RuntimeError("trusted storage controller must own its private credentials")
    if os.environ.get("RECOVERY_OPERATION", "verify-existing") != "verify-existing":
        raise RuntimeError("this reviewed stage permits only immutable recovery verification")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    manifest_body = (ROOT / "manifest.json").read_bytes()
    for name, expected in json.loads(manifest_body).items():
        if digest((ROOT / name).read_bytes()) != expected:
            raise RuntimeError("controller bytes differ from the signed preparation")
    policy = json.loads((ROOT / "policy.json").read_text())
    credentials = {name: os.environ.pop(source_name) for name, source_name in INPUT_NAMES.items()}
    if any(not value or any(ord(c) < 32 for c in value) for value in credentials.values()):
        raise ValueError("invalid protected storage input")
    for name in ("S3_ENDPOINT", "S3_BUCKET", "S3_PREFIX", "AWS_DEFAULT_REGION"):
        if credentials[name] != policy["storage_target"][name]:
            raise ValueError("storage destination differs from the reviewed target")
    recovery, storage = modules()
    offsite, total = validate_case(policy, credentials, recovery, storage)
    if shutil.disk_usage("/tmp").free < total * 5 + 1024**3:
        raise RuntimeError("insufficient bounded scratch space for an isolated restore")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = Path("/evidence") / stamp
    evidence.mkdir(mode=0o700, parents=True)
    with tempfile.TemporaryDirectory(prefix="recovery-storage-") as private, tempfile.TemporaryDirectory(prefix="recovery-work-") as public:
        private_root, public_root = Path(private), Path(public)
        public_root.chmod(0o755)
        base = public_root / "recovery-verification"
        base.mkdir(mode=0o755)
        work = base / "existing"
        work.mkdir(mode=0o700)
        for name in ("bundle", "proof", "proof/resume-offsite-heads"):
            (work / name).mkdir(mode=0o700)
        env = {**environment(private_root), **credentials, "RUNNER_TEMP": str(private_root),
               "GITHUB_WORKSPACE": str(TRUSTED)}
        event("recovery_verification_start", request=offsite["request_id"], object_count=len(offsite["objects"]), bytes=total,
              controller_source=policy["controller_source"], production_export_requested=False)
        storage.download_object(work, "offsite-receipt.json", policy["offsite_object"], env)
        if (work / "offsite-receipt.json").read_bytes() != (ROOT / "case/offsite-receipt.json").read_bytes():
            raise ValueError("versioned offsite receipt differs from pinned evidence")
        for number, (name, item) in enumerate(sorted(offsite["objects"].items()), 1):
            storage.download_object(work, name, item, env)
            event("immutable_object_verified", number=number, name=name, size=item["size"], sha256=item["sha256"])
        for name in ("activation-receipt.json", "candidate-receipt.json", "shadow-receipt.json", "request.json", "recovery-receipt.json"):
            if (work / name).read_bytes() != (ROOT / "case" / name).read_bytes():
                raise ValueError("download changed original recovery metadata: " + name)
        for path in work.rglob("*"):
            if path.is_file():
                if path.is_symlink() or path.stat().st_nlink != 1:
                    raise ValueError("downloaded recovery member is not a private regular file")
                path.chmod(0o444)
            elif path.is_dir() and not path.is_symlink():
                path.chmod(0o755)
        work.chmod(0o755)
        # A sticky root-owned proof directory admits only new restore output.
        (work / "proof").chmod(0o1777)
        uid = pwd.getpwnam("postgres").pw_uid
        if uid in (0, RESTORE_UID):
            raise RuntimeError("PostgreSQL identity is not independently isolated")
        pgroot = public_root / "postgres"
        pgenv = environment(pgroot)
        try:
            uid, pgenv = start_postgres(pgroot)
            scratch = public_root / "restore-home"
            scratch.mkdir(mode=0o700)
            os.chown(scratch, RESTORE_UID, RESTORE_UID)
            child_env = {**environment(scratch), "EVIDENCE_ROOT": str(work), "SNAPSHOT_ID": offsite["snapshot_id"],
                         "RELEASE_SHA": offsite["commit"], "GITHUB_REPOSITORY": "beepboop2025/seiche",
                         "GITHUB_WORKSPACE": str(TRUSTED), "PGPASSWORD": "phase6-restore-only"}
            run(["bash", str(ROOT / "restore.sh")], env=child_env, uid=RESTORE_UID, timeout=2700)
        finally:
            stop_uid(RESTORE_UID)
            if (pgroot / "data/postmaster.pid").exists():
                try:
                    run(["/usr/lib/postgresql/18/bin/pg_ctl", "-D", str(pgroot / "data"), "-m", "fast", "-w", "stop"], env=pgenv, uid=uid)
                finally:
                    stop_uid(uid)
            (work / "proof").chmod(0o755)
        result_path = work / "proof/railway-reverse-restore.json"
        if result_path.is_symlink() or result_path.stat().st_nlink != 1:
            raise ValueError("restore proof is not a regular isolated result")
        restored = json.loads(result_path.read_bytes())
        if (restored["recovery_receipt_sha256"] != policy["recovery_receipt_sha256"] or
                restored["request_id"] != offsite["request_id"] or restored["authority_changed"] is not False or
                len(restored["postgres_count_floor"]) != len(restored["postgres_counts"]) or
                any(actual < floor for actual, floor in zip(restored["postgres_counts"], restored["postgres_count_floor"]))):
            raise ValueError("isolated restore result differs from immutable recovery")
        # Original locked proof is kept untouched; this proof explicitly names Railway execution.
        validate_proof_tree(work / "proof", offsite["objects"])
        shutil.copytree(work / "proof", evidence / "proof")
        for name in ("recovery-receipt.json", "offsite-receipt.json"):
            shutil.copyfile(work / name, evidence / name)
        proof = {"schema": "seiche.railway-existing-recovery-verification.v1", "status": "pass",
                 "execution_platform": "railway", "railway_deployment": os.environ.get("RAILWAY_DEPLOYMENT_ID", "local"),
                 "controller_source": policy["controller_source"], "controller_digest": digest(manifest_body),
                 "application_source": offsite["commit"], "application_deployment": policy["application_deployment"],
                 "original_github_run": policy["original_github_run"], "request_id": offsite["request_id"],
                 "recovery_receipt_sha256": policy["recovery_receipt_sha256"], "offsite_receipt_sha256": policy["offsite_object"]["sha256"],
                 "immutable_objects_verified": len(offsite["objects"]) + 1, "downloaded_bytes": total + policy["offsite_object"]["size"],
                 "postgres_counts": restored["postgres_counts"], "retain_until": offsite["retain_until"],
                 "production_export_requested": False, "s3_objects_written": False, "authority_changed": False,
                 "observed_at": datetime.now(timezone.utc).isoformat()}
        (evidence / "verification.json").write_text(json.dumps(proof, sort_keys=True) + "\n")
    event("RAILWAY_EXISTING_RECOVERY_VERIFY_PASS", **proof)


if __name__ == "__main__":
    main()
