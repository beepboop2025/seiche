"""Run the original governed recovery stages from an isolated native controller."""

from datetime import datetime, timedelta, timezone
import json
import fcntl
import os
from pathlib import Path
import re
import resource
import shutil
import stat
import subprocess
import tempfile
import time

from prepare import admitted_source_paths

import verify


ROOT = verify.ROOT
TRUSTED = verify.TRUSTED
MONITOR_NAMES = ("RAILWAY_TOKEN", "RAILWAY_EDGE_TOKEN", "RAILWAY_RECOVERY_PROBE_SSH_KEY")


def source_identity(policy, private):
    """Fetched source is read as data; only manifest-pinned controller bytes execute."""
    env = verify.environment(private)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0")
    repo = private / "source-identity"
    subprocess.run(["git", "init", "-q", str(repo)], env=env, check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "fetch", "--depth=1",
                    "https://github.com/beepboop2025/seiche.git", "refs/heads/main"], env=env, check=True, timeout=120)
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], env=env, timeout=120)
    source = git("rev-parse", "FETCH_HEAD").decode().strip()
    if re.fullmatch(r"[0-9a-f]{40}", source) is None:
        raise ValueError("current source is not an immutable Git identity")
    current_paths = admitted_source_paths(git("ls-tree", "-r", "--name-only", source, "backend", "ops", "governance").decode().splitlines())
    if current_paths != set(policy["trusted_source_sha256"]):
        raise ValueError("current recovery source input path set changed")
    for name, expected in policy["trusted_source_sha256"].items():
        if verify.digest(git("show", source + ":" + name)) != expected:
            raise ValueError("reviewed recovery source changed: " + name)
    return source


def restore_workspace(public):
    """Create the root-owned traverse path despite the controller's private umask."""
    base = public / "recovery-verification"
    base.mkdir(mode=0o755)
    base.chmod(0o755)
    return base / "export"


def seal_restore_inputs(work):
    """Root-owned immutable inputs and one sticky output directory isolate restore code."""
    total = 0
    for path in work.rglob("*"):
        item = path.lstat()
        if stat.S_ISDIR(item.st_mode):
            path.chmod(0o755)
        elif stat.S_ISREG(item.st_mode) and item.st_nlink == 1:
            total += item.st_size
            path.chmod(0o444)
        else:
            raise ValueError("export inputs contain a link or non-regular member")
    if total > verify.MAX_DOWNLOAD_BYTES + 32 * 1024**2:
        raise ValueError("export inputs exceed the bounded restore size")
    work.chmod(0o755)
    (work / "proof").chmod(0o1777)


def accepted_restore_output(work, previous):
    """After UID quiescence accept only the new bounded original proof file."""
    current = set(str(p.relative_to(work)) for p in work.rglob("*"))
    if current - previous != {"proof/reverse-restore.json"} or previous - current:
        raise ValueError("isolated restore changed the allowed output set")
    proof = work / "proof/reverse-restore.json"
    item = proof.lstat()
    if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1 or not 0 < item.st_size <= 512 * 1024:
        raise ValueError("isolated restore proof is not one bounded regular file")
    os.chown(proof, 0, 0)
    proof.chmod(0o444)
    (work / "proof").chmod(0o755)
    return json.loads(proof.read_bytes())


def original_restore(work, private, public, identity, timeout=2700):
    seal_restore_inputs(work)
    before = set(str(p.relative_to(work)) for p in work.rglob("*"))
    pgroot = public / "postgres"
    uid = None
    try:
        uid, pgenv = verify.start_postgres(pgroot)
        home = public / "restore-home"
        home.mkdir(mode=0o700)
        os.chown(home, verify.RESTORE_UID, verify.RESTORE_UID)
        env = {**verify.environment(home), "EVIDENCE_ROOT": str(work), "SNAPSHOT_ID": identity["snapshot_id"],
               "RELEASE_SHA": identity["release_sha"], "GOVERNANCE_REPOSITORY": "beepboop2025/seiche",
               "TRUSTED_SOURCE": str(TRUSTED), "PGPASSWORD": "phase6-restore-only"}
        verify.run(["bash", str(ROOT / "restore-native.sh")], env=env, uid=verify.RESTORE_UID, timeout=timeout)
    finally:
        verify.stop_uid(verify.RESTORE_UID)
        if uid is not None:
            try:
                if (pgroot / "data/postmaster.pid").exists():
                    verify.run(["/usr/lib/postgresql/18/bin/pg_ctl", "-D", str(pgroot / "data"),
                                "-m", "fast", "-w", "stop"], env=pgenv, uid=uid)
            finally:
                verify.stop_uid(uid)
    return accepted_restore_output(work, before)


def monitor(policy, credentials, private, native_environment):
    output = private / "monitor.log"
    env = {**verify.environment(private), **native_environment,
           **{"MONITOR_" + name: value for name, value in credentials.items()}}
    # This trusted subcontroller receives only its three existing read/probe inputs.
    with output.open("wb") as stream:
        subprocess.run(["python", str(ROOT / "monitor/monitor.py")], env=env, stdout=stream,
                       stderr=subprocess.STDOUT, check=True, timeout=1800, close_fds=True)
    if output.stat().st_size > 8 * 1024**2:
        raise ValueError("monitor diagnostics exceeded the reviewed bound")
    marker = "RAILWAY_RECOVERY_MONITOR_PASS "
    proofs = [json.loads(line.removeprefix(marker)) for line in output.read_text().splitlines() if line.startswith(marker)]
    if len(proofs) != 1 or proofs[0]["status"] != "pass" or proofs[0]["bootstrap"] is not False:
        raise ValueError("strict original monitor did not provide one accepted proof")
    proof = proofs[0]
    if proof["controller_source"] != policy["controller_source"]:
        raise ValueError("monitor controller source differs from signed native assembly")
    return proof


def outputs(path, expected):
    lines = path.read_text().splitlines()
    values = dict(line.split("=", 1) for line in lines)
    if len(lines) != len(values) or set(values) != expected:
        raise ValueError("native stage output identity differs")
    return values


def run_original_stage(name, env, timeout):
    verify.event("native_recovery_stage_start", stage=name)
    verify.run(["bash", str(ROOT / name)], env=env, timeout=timeout)
    verify.event("native_recovery_stage_pass", stage=name)


def validate_signers(control_pem, evidence_pem, public_hex):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    verify.evidence_signer(evidence_pem, public_hex)
    key = serialization.load_pem_private_key(control_pem.encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("registered recovery signer must be Ed25519")
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if verify.digest(public) != "2be24b7ea07b1596f9e6bf95c22ee1425532b28e04b3d3cf4eb263fce7142987":
        raise ValueError("recovery control signer differs from the existing registered key")


def stage_budget(deadline, maximum):
    seconds = int(deadline - time.monotonic())
    if seconds <= 0:
        raise RuntimeError("original aggregate export job deadline expired")
    return min(maximum, seconds)


def main():
    import attest
    if os.geteuid() != 0 or os.environ.get("RECOVERY_OPERATION") != "export-recurring":
        raise RuntimeError("native recurring recovery is not explicitly armed")
    if os.environ.get("RECOVERY_CONFIRMATION") != "EXPORT_WITHOUT_AUTHORITY_CHANGE":
        raise RuntimeError("native governed export confirmation is missing")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.umask(0o077)
    lock_root = Path("/evidence/native")
    lock_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    with (lock_root / ".execution.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return run_locked()


def run_locked():
    import attest
    manifest_body = (ROOT / "manifest.json").read_bytes()
    for name, expected in json.loads(manifest_body).items():
        if verify.digest((ROOT / name).read_bytes()) != expected:
            raise ValueError("native recovery image differs from its signed assembly")
    policy = json.loads((ROOT / "policy.json").read_text())
    if policy.get("operation") != "export-recurring":
        raise ValueError("this assembly does not admit recurring production export")
    native = {name: os.environ[name] for name in ("RAILWAY_PROJECT_ID", "RAILWAY_ENVIRONMENT_ID", "RAILWAY_SERVICE_ID",
                                                "RAILWAY_DEPLOYMENT_ID", "RAILWAY_REPLICA_ID")}
    for field in ("project", "environment", "service"):
        if native["RAILWAY_" + field.upper() + "_ID"] != policy["controller_" + field + "_id"]:
            raise ValueError("native controller installation target differs")
    for field in ("RAILWAY_DEPLOYMENT_ID", "RAILWAY_REPLICA_ID"):
        if attest.UUID.fullmatch(native[field]) is None:
            raise ValueError("native controller execution identity is missing")
    image_digest = os.environ.pop("RECOVERY_CONTROLLER_IMAGE_DIGEST")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) is None:
        raise ValueError("reviewed native image identity is missing")
    storage_inputs = {name: os.environ.pop(variable) for name, variable in verify.INPUT_NAMES.items()}
    monitor_inputs = {name: os.environ.pop("MONITOR_" + name) for name in MONITOR_NAMES}
    control_key = os.environ.pop("RECOVERY_CONTROL_SIGNING_KEY_PEM")
    evidence_key = os.environ.pop("RECOVERY_EXECUTION_SIGNING_KEY_PEM")
    if any(not value for value in (*storage_inputs.values(), *monitor_inputs.values(), control_key, evidence_key)):
        raise ValueError("a required isolated recovery input is unavailable")
    validate_signers(control_key, evidence_key, policy["execution_public_key"])
    for name, expected in policy["storage_target"].items():
        if storage_inputs[name] != expected:
            raise ValueError("native storage target differs from the fixed reviewed destination")
    now = datetime.now(timezone.utc)
    evidence = Path("/evidence/native") / (now.strftime("%Y%m%dT%H%M%SZ") + "-" + native["RAILWAY_REPLICA_ID"])
    evidence.mkdir(parents=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="native-recovery-private-") as private_name, tempfile.TemporaryDirectory(prefix="native-recovery-work-") as public_name:
        private, public = Path(private_name), Path(public_name)
        public.chmod(0o755)
        current_source = source_identity(policy, private)
        monitor_proof = monitor(policy, monitor_inputs, private, native)
        (evidence / "monitor-proof.json").write_bytes(verify.canonical(monitor_proof))
        target = policy["production_target"]
        flat = {name: policy[name] for name in ("controller_source", "controller_project_id", "controller_environment_id", "controller_service_id", "execution_public_key")}
        flat.update(controller_manifest_sha256=verify.digest(manifest_body), controller_image_digest=image_digest,
                    controller_deployment_id=native["RAILWAY_DEPLOYMENT_ID"], storage_endpoint=storage_inputs["S3_ENDPOINT"],
                    storage_bucket=storage_inputs["S3_BUCKET"], storage_prefix=storage_inputs["S3_PREFIX"],
                    application_project_id=target["RAILWAY_PROJECT_ID"], application_environment_id=target["RAILWAY_ENVIRONMENT_ID"],
                    application_service_id=target["RAILWAY_STATEFUL_SERVICE_ID"])
        clean = verify.environment(private)
        production = {**clean, "RAILWAY_TOKEN": monitor_inputs["RAILWAY_TOKEN"], "RAILWAY_EDGE_TOKEN": monitor_inputs["RAILWAY_EDGE_TOKEN"]}
        storage_env = {**clean, **storage_inputs, "RUNNER_TEMP": str(private), "GITHUB_WORKSPACE": str(TRUSTED)}
        recovery, storage_module = verify.modules()
        storage_module.validate_location(storage_env)
        helper = TRUSTED / "ops/deploy/seiche-s3-object-lock.sh"
        verify.run(["bash", str(helper), "probe-bucket", str(evidence / "bucket-preflight.json")], env=storage_env, timeout=120)
        fetch_root = private / "index-fetch"
        fetch_root.mkdir(mode=0o700)
        store = attest.Storage(fetch_root, storage_env, storage_module)
        observed = datetime.now(timezone.utc)
        runtime = attest.live_runtime(flat, production, observed)
        if (runtime["deployment_id"] != monitor_proof["deployment_id"] or runtime["source"] != monitor_proof["release_sha"]):
            raise ValueError("current production identity moved after strict monitor")
        existing = attest.fetch_index(store, flat, observed, allow_missing=True)
        if existing is not None:
            envelope, reference, head = existing
            payload = verify.validate_execution_index(envelope, policy=flat, current_runtime=runtime, now=observed)
            if payload["controller_deployment_id"] != native["RAILWAY_DEPLOYMENT_ID"]:
                raise ValueError("today's immutable execution belongs to another reviewed installation")
            attest.download_receipts(store, payload, recovery, flat, observed)
            finished = datetime.now(timezone.utc)
            after = attest.live_runtime(flat, production, finished)
            if any(runtime[field] != after[field] for field in ("deployment_id", "source", "replica_id")):
                raise ValueError("production changed while verifying today's existing recovery")
            verify.validate_execution_index(envelope, policy=flat, current_runtime=after, now=finished)
            if store.head(reference["key"])[0] != reference:
                raise ValueError("today's immutable index changed during verification")
            (evidence / "existing-index.json").write_bytes(verify.canonical(envelope))
            verify.event("RAILWAY_NATIVE_RECOVERY_ALREADY_VERIFIED", date=payload["date"], deployment=native["RAILWAY_DEPLOYMENT_ID"],
                         snapshot=payload["snapshot_id"], production_export_requested=False, index_version=reference["version_id"])
            return
        work = restore_workspace(public)
        stage_env = {**production, **target, "RAILWAY_SERVICE_ID": target["RAILWAY_STATEFUL_SERVICE_ID"],
                     "RAILWAY_VOLUME_ID": target["RAILWAY_STATEFUL_VOLUME_ID"], "RAILWAY_ORIGIN": target["RAILWAY_STATEFUL_ORIGIN"],
                     "RAILWAY_REAL_BIN": "/usr/local/bin/railway-real", "SEICHE_RAILWAY_RECOVERY_SIGNING_KEY_PEM": control_key,
                     "DEPLOYMENT_ID": runtime["deployment_id"], "RELEASE_SHA": runtime["source"],
                     "NATIVE_INVOCATION": "railway", "NATIVE_DEPLOYMENT_ID": native["RAILWAY_DEPLOYMENT_ID"],
                     "NATIVE_REPLICA_ID": native["RAILWAY_REPLICA_ID"], "GOVERNANCE_REPOSITORY": "beepboop2025/seiche",
                     "TRUSTED_SOURCE": str(TRUSTED), "PRIVATE_TEMP": str(private), "RUNNER_TEMP": str(private),
                     "GITHUB_WORKSPACE": str(TRUSTED), "NATIVE_OUTPUT": str(private / "export.outputs"),
                     "NATIVE_SUMMARY": str(private / "summary"), "EVIDENCE_ROOT": str(work)}
        (private / "edge-header").write_text("X-Seiche-Edge-Token: " + monitor_inputs["RAILWAY_EDGE_TOKEN"] + "\n")
        export_deadline = time.monotonic() + 5400
        run_original_stage("export-native.sh", stage_env, stage_budget(export_deadline, 2700))
        identity = outputs(private / "export.outputs", {"snapshot_id", "request_id", "receipt_sha256", "receipt_path", "evidence_root"})
        if identity["evidence_root"] != str(work) or identity["receipt_path"] != str(work / "recovery-receipt.json"):
            raise ValueError("native export output path escaped its private stage")
        identity["release_sha"] = runtime["source"]
        proof = original_restore(work, private, public, identity, stage_budget(export_deadline, 2700))
        stage_env.update(storage_inputs)
        stage_env.update(SNAPSHOT_ID=identity["snapshot_id"], REQUEST_ID=identity["request_id"], NATIVE_OUTPUT=str(private / "seal.outputs"))
        run_original_stage("seal-native.sh", stage_env, stage_budget(export_deadline, 1800))
        result = outputs(private / "seal.outputs", {"receipt_path"})
        if result["receipt_path"] != str(work / "offsite-receipt.json"):
            raise ValueError("native offsite output path differs")
        offsite_body = (work / "offsite-receipt.json").read_bytes()
        offsite = json.loads(offsite_body)
        offsite_head = json.loads((work / "proof/offsite-receipt.head.json").read_bytes())
        observed = datetime.now(timezone.utc)
        current_runtime = attest.live_runtime(flat, production, observed)
        if any(runtime[field] != current_runtime[field] for field in ("deployment_id", "source", "replica_id")):
            raise ValueError("production identity changed before native execution sealing")
        payload = {name: flat[name] for name in verify.INDEX_FIELDS if name in flat}
        payload.update(schema=verify.INDEX_SCHEMA, execution_platform="railway", date=observed.date().isoformat(),
                       observed_at=observed.isoformat(), repository="beepboop2025/seiche",
                       governance_workflow="beepboop2025/seiche/.github/workflows/railway-stateful-recovery.yml",
                       controller_replica_id=native["RAILWAY_REPLICA_ID"], monitor_proof_sha256=verify.digest(verify.canonical(monitor_proof)),
                       application_deployment_id=runtime["deployment_id"], application_source=runtime["source"],
                       application_replica_id=runtime["replica_id"], request_id=identity["request_id"], snapshot_id=identity["snapshot_id"],
                       objects_verified=15, recovery_receipt=offsite["objects"]["recovery-receipt.json"],
                       reverse_restore_proof=offsite["objects"]["proof/reverse-restore.json"],
                       offsite_receipt={"key": f"{offsite['prefix']}/{offsite['snapshot_id']}/{offsite['request_id']}/offsite-receipt.json",
                                        "version_id": offsite_head["VersionId"], "sha256": verify.digest(offsite_body), "size": len(offsite_body)},
                       postgres_counts=proof["postgres_counts"], postgres_count_floor=proof["postgres_count_floor"],
                       authority_changed=False, research_only=True, can_publish=False, can_execute=False)
        envelope = verify.sign_execution_index(payload, evidence_key, policy["execution_public_key"])
        verify.validate_execution_index(envelope, policy=flat, current_runtime=current_runtime, now=observed)
        # Read each exact immutable receipt version back through the same strict tail boundary.
        bodies, heads, documents = attest.download_receipts(store, payload, recovery, flat, observed)
        index = evidence / "index.json"
        index.write_bytes(verify.canonical(envelope))
        key = storage_inputs["S3_PREFIX"] + "/native-executions/" + observed.date().isoformat() + "/index.json"
        verify.run(["bash", str(helper), "put-verify", str(index), key, str(evidence / "index.head.json")], env=storage_env, timeout=180)
        for name, body in bodies.items():
            (evidence / (name + ".json")).write_bytes(body)
        verify.event("RAILWAY_NATIVE_RECOVERY_PASS", deployment=native["RAILWAY_DEPLOYMENT_ID"],
                     source=current_source, controller_source=policy["controller_source"], manifest=verify.digest(manifest_body),
                     request=identity["request_id"], snapshot=identity["snapshot_id"], index_sha256=verify.digest(index.read_bytes()),
                     index_version=json.loads((evidence / "index.head.json").read_text())["VersionId"],
                     postgres_counts=proof["postgres_counts"], authority_changed=False)


if __name__ == "__main__":
    main()
