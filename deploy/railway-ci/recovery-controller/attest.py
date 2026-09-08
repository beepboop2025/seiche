"""Prepare exact native recovery subjects for the original protected GitHub OIDC tail.

This program does not restore, sign production commands, or issue attestations.
Its two output receipt files are inputs to the original GitHub attestation actions.
"""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

import verify


REPOSITORY = "beepboop2025/seiche"
WORKFLOW = REPOSITORY + "/.github/workflows/railway-stateful-recovery.yml"
ORIGIN = "https://seiche-stateful-core-production.up.railway.app"
PUBLIC = "https://api.seiche.info"
MAX_BYTES = 512 * 1024
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
SHA = re.compile(r"[0-9a-f]{40}")
OWNER_PUBLIC = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBuJV6o8YL2XXR9q4vcwpHuc2z1GEBawSmrJWGrgwzFV"
INSTALLATION_DOMAIN = "seiche-railway-recovery-installation-v1"
INSTALLATION_BINDINGS = ("controller_source", "controller_manifest_sha256", "controller_image_digest",
                         "controller_deployment_id", "controller_project_id", "controller_environment_id",
                         "controller_service_id", "execution_public_key")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def document(body):
    require(isinstance(body, bytes) and 0 < len(body) <= MAX_BYTES, "bounded JSON body required")
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, "duplicate JSON field")
            value[key] = item
        return value
    value = json.loads(body, object_pairs_hook=unique)
    require(isinstance(value, dict), "JSON document must be an object")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MissingObject(ValueError):
    """Only an explicit provider HeadObject missing response has this meaning."""


class Live:
    def __init__(self, token, edge_token, home):
        self.token, self.edge_token, self.home = token, edge_token, home

    def api(self, query, variables):
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.home), "RAILWAY_TOKEN": self.token}
        result = subprocess.run(["/usr/local/bin/railway-real", "api", query, "--variables", json.dumps(variables)],
                                env=env, capture_output=True, timeout=90, check=False)
        require(result.returncode == 0, "bounded Railway identity read failed")
        value = document(result.stdout)
        require(not value.get("errors") and isinstance(value.get("data"), dict), "Railway identity query returned errors")
        return value["data"]

    def health(self, url):
        require(url in {ORIGIN + "/healthz", ORIGIN + "/api/health", PUBLIC + "/api/health"}, "unreviewed health URL")
        headers = {"User-Agent": "Seiche-Recovery-Attestation-Tail/1.0"}
        if url == ORIGIN + "/api/health":
            headers["X-Seiche-Edge-Token"] = self.edge_token
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
                require(response.status == 200, "production health is unavailable")
                body, response_headers = response.read(MAX_BYTES + 1), dict(response.headers.items())
        except urllib.error.HTTPError:
            raise ValueError("production health failed or redirected") from None
        return document(body), {name.lower(): value for name, value in response_headers.items()}


def runtime_identity(live, policy, now):
    health, _ = live.health(ORIGIN + "/healthz")
    require(health.get("status") == "ready" and health.get("mode") == "production", "production runtime is not ready")
    identities = []
    for url in (ORIGIN + "/api/health", PUBLIC + "/api/health"):
        body, headers = live.health(url)
        generated = datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00"))
        require(generated.tzinfo is not None and 0 <= (now - generated).total_seconds() <= 900,
                "production API evidence is stale or future dated")
        require(isinstance(body.get("version"), str) and bool(body["version"]) and body.get("faults") == []
                and isinstance(body.get("provenance"), list) and bool(body["provenance"]), "production API evidence is unhealthy")
        require(headers.get("x-seiche-railway-authority") == "production", "production edge authority differs")
        identities.append((headers.get("x-seiche-railway-deployment"), headers.get("x-seiche-release-sha")))
    require(identities[0] == identities[1], "public and origin identities differ")
    deployment, source = identities[0]
    require(UUID.fullmatch(deployment or "") and SHA.fullmatch(source or ""), "production edge identity is malformed")
    value = live.api("query($id:String!){deployment(id:$id){id projectId environmentId serviceId status instances{id status}}}", {"id": deployment})["deployment"]
    expected = {"id": deployment, "status": "SUCCESS", **{field + "Id": policy["application_" + field + "_id"] for field in ("project", "environment", "service")}}
    require(isinstance(value, dict) and all(value.get(k) == v for k, v in expected.items()), "live deployment target differs")
    instances = value.get("instances")
    require(isinstance(instances, list) and len(instances) == 1 and isinstance(instances[0], dict)
            and instances[0].get("status") == "RUNNING" and isinstance(instances[0].get("id"), str)
            and UUID.fullmatch(instances[0]["id"]), "production deployment is not singly RUNNING")
    return {"deployment_id": deployment, "source": source, "replica_id": instances[0]["id"],
            "status": "RUNNING", "observed_at": now.isoformat()}


def live_runtime(policy, environment, now):
    """Shared fixed-target transport; no environment is inherited by the API child."""
    return runtime_identity(Live(environment["RAILWAY_TOKEN"], environment["RAILWAY_EDGE_TOKEN"],
                                 Path(environment["HOME"])), policy, now)


def owner_signature(body, signature):
    require(isinstance(signature, bytes) and 0 < len(signature) <= 8192, "installation signature is missing or oversized")
    with tempfile.TemporaryDirectory(prefix="installation-signature-") as name:
        root = Path(name)
        (root / "signers").write_text("owner " + OWNER_PUBLIC + "\n")
        (root / "signature").write_bytes(signature)
        result = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(root / "signers"), "-I", "owner",
                                 "-n", INSTALLATION_DOMAIN, "-s", str(root / "signature")],
                                input=body, capture_output=True, timeout=30,
                                env={"PATH": "/usr/bin:/bin", "HOME": name}, check=False)
        require(result.returncode == 0, "owner installation signature verification failed")


def installation_identity(body, signature, payload, policy, now, *, verify_signature=owner_signature):
    """Authenticate installation-time API proof, explicitly not a live CI-project query."""
    require(verify.digest(body) == policy["installation_sha256"] and
            verify.digest(signature) == policy["installation_signature_sha256"], "installation receipt differs from independent review")
    value = document(body)
    fields = set(INSTALLATION_BINDINGS) | {"schema", "purpose", "observed_at", "api_proof_sha256", "authority_changed", "can_publish", "can_execute"}
    require(set(value) == fields and body == verify.canonical(value), "installation receipt is not the canonical closed contract")
    verify_signature(body, signature)
    require(value["schema"] == "seiche.railway-recovery-installation.v1" and value["purpose"] == "native_recovery_evidence_only"
            and value["authority_changed"] is False and value["can_publish"] is False and value["can_execute"] is False,
            "installation purpose or authority differs")
    require(re.fullmatch(r"[0-9a-f]{64}", value["api_proof_sha256"] or ""), "installation API evidence hash is missing")
    observed = datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
    execution = datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00"))
    require(observed.tzinfo is not None and observed <= execution and (observed - now).total_seconds() <= 120,
            "installation receipt is future dated or postdates execution")
    for name in INSTALLATION_BINDINGS:
        expected = policy["execution_public_key"] if name == "execution_public_key" else payload[name]
        require(value[name] == expected, "execution differs from the independently verified installation")
        if name != "controller_deployment_id":
            require(value[name] == policy[name], "installation differs from reviewed policy")
    return value


class Storage:
    """Use the original versioned-download and Object Lock validators with clean credentials."""
    def __init__(self, root, environment, original):
        self.root, self.env, self.original = root, environment, original
        key = base64.b64decode(environment["S3_SSE_C_KEY_B64"], validate=True)
        require(len(key) == 32 and base64.b64encode(key).decode() == environment["S3_SSE_C_KEY_B64"], "invalid SSE-C key")
        self.key_md5 = base64.b64encode(hashlib.md5(key, usedforsecurity=False).digest()).decode()
        self.key_path = root / "sse-key"
        self.key_path.write_bytes(key)
        self.key_path.chmod(0o600)
        (root / "proof/resume-offsite-heads").mkdir(mode=0o700, parents=True)

    def head(self, key, version=None):
        arguments = ["aws", "--endpoint-url", self.env["S3_ENDPOINT"], "--no-cli-pager", "s3api", "head-object",
                     "--bucket", self.env["S3_BUCKET"], "--key", key, "--sse-customer-algorithm", "AES256",
                     "--sse-customer-key", "fileb://" + str(self.key_path)]
        if version is not None:
            arguments.append("--version-id=" + version)
        env = {name: self.env[name] for name in ("PATH", "HOME", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION", "AWS_EC2_METADATA_DISABLED")}
        result = subprocess.run(arguments, env=env, capture_output=True, timeout=90, check=False)
        if result.returncode and re.search(rb"An error occurred \((?:404|NoSuchKey|NotFound)\) when calling the HeadObject operation:", result.stderr):
            raise MissingObject("required immutable recovery object is absent")
        require(result.returncode == 0, "required immutable recovery object is missing or unreadable")
        value = document(result.stdout)
        reference = {"key": key, "version_id": value.get("VersionId"), "sha256": value.get("Metadata", {}).get("sha256"), "size": value.get("ContentLength")}
        self.original.validate_object(reference["version_id"], reference["sha256"], reference["size"], maximum=MAX_BYTES)
        self.original.validate_head(value, version=reference["version_id"], sha256=reference["sha256"], size=reference["size"], key_md5=self.key_md5, now=datetime.now(timezone.utc))
        return reference, value

    def fetch(self, name, reference):
        self.original.validate_object(reference["version_id"], reference["sha256"], reference["size"], maximum=MAX_BYTES)
        self.original.download_object(self.root, name, reference, self.env)
        path = self.root / name
        metadata = path.lstat()
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and metadata.st_size == reference["size"], "receipt output is not bounded and regular")
        body = path.read_bytes()
        require(verify.digest(body) == reference["sha256"], "immutable receipt bytes changed")
        return body


def fetch_index(storage, policy, now, *, allow_missing=False):
    """Caller must prove bucket configuration before using absence to start an export."""
    key = policy["storage_prefix"] + "/native-executions/" + now.astimezone(timezone.utc).date().isoformat() + "/index.json"
    try:
        reference, head = storage.head(key)
    except MissingObject:
        if allow_missing:
            return None
        raise
    return document(storage.fetch("proof/native-index.json", reference)), reference, head


def download_receipts(storage, payload, recovery, policy, now):
    bodies, heads = {}, {}
    for label, name in verify.INDEX_OBJECTS.items():
        reference, heads[label] = storage.head(payload[label]["key"], payload[label]["version_id"])
        require(reference == payload[label], "referenced immutable version differs")
        bodies[label] = storage.fetch(name, reference)
    documents = verify.validate_index_receipts(payload, bodies=bodies, heads=heads, recovery=recovery, policy=policy, now=now)
    return bodies, heads, documents


def prepare_subjects(policy, storage, live, recovery, *, installation, now, clock=lambda: datetime.now(timezone.utc), verify_signature=owner_signature):
    """No output is released until all independent before/after checks pass."""
    before = runtime_identity(live, policy, now)
    envelope, index_ref, _ = fetch_index(storage, policy, now)
    key = index_ref["key"]
    payload = verify.validate_execution_index(envelope, policy=policy, current_runtime=before, now=now)
    installation_identity(*installation, payload, policy, now, verify_signature=verify_signature)
    bodies, _heads, _documents = download_receipts(storage, payload, recovery, policy, now)
    finished = clock()
    after = runtime_identity(live, policy, finished)
    require({k: v for k, v in before.items() if k != "observed_at"} == {k: v for k, v in after.items() if k != "observed_at"}, "production runtime changed during attestation preparation")
    verify.validate_execution_index(envelope, policy=policy, current_runtime=after, now=finished)
    installation_identity(*installation, payload, policy, finished, verify_signature=verify_signature)
    require(storage.head(key)[0] == index_ref, "daily index changed during attestation preparation")
    proof = {"schema": "seiche.native-recovery-attestation-preparation.v1", "status": "pass",
             "execution_platform": "railway", "attestation_platform": "github", "restore_executed_here": False,
             "controller_identity_proof": "owner-signed installation-time Railway API receipt; no live CI-project query",
             "installation_sha256": policy["installation_sha256"],
             "observed_at": finished.isoformat(), "index": index_ref, "runtime": after,
             "controller_source": payload["controller_source"], "controller_image_digest": payload["controller_image_digest"],
             "subjects": {verify.INDEX_OBJECTS[name]: {"sha256": verify.digest(bodies[name]), "size": len(bodies[name])}
                          for name in ("recovery_receipt", "offsite_receipt")}}
    return {verify.INDEX_OBJECTS[name]: bodies[name] for name in ("recovery_receipt", "offsite_receipt")}, proof


def admit_github(environment, policy, checkout):
    require(environment.get("GITHUB_REPOSITORY") == REPOSITORY and environment.get("GITHUB_REF") == "refs/heads/main"
            and environment.get("GITHUB_WORKFLOW_REF") == WORKFLOW + "@refs/heads/main"
            and environment.get("GITHUB_EVENT_NAME") in {"schedule", "workflow_dispatch"}
            and SHA.fullmatch(environment.get("GITHUB_SHA", "")), "tail requires the original protected main workflow identity")
    require(checkout == policy["tail_source"] and SHA.fullmatch(checkout), "tail checkout differs from reviewed source")


def validate_tail_inputs(root, inputs, tracked_backend):
    """Pin the complete backend tree and the root control signer registry."""
    root = root.resolve()
    required = {"deploy/railway-ci/recovery-controller/attest.py", "deploy/railway-ci/recovery-controller/verify.py",
                "deploy/railway-ci/recovery-controller/requirements.lock", "ops/railway/resume_recovery.py",
                "ops/deploy/seiche-s3-object-lock.sh", "governance/railway-control-signers.json", *tracked_backend}
    require(tracked_backend and isinstance(inputs, dict) and required <= set(inputs), "tail input manifest omits local module or governance inputs")
    for name, expected in inputs.items():
        require(isinstance(name, str) and isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected), "tail input manifest entry is malformed")
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts, "tail input path escapes checkout")
        path = root / relative
        require(path.resolve().is_relative_to(root) and path.is_file() and not path.is_symlink()
                and verify.digest(path.read_bytes()) == expected, "reviewed tail input differs")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--policy-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--installation", required=True, type=Path)
    parser.add_argument("--installation-signature", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    policy_body = args.policy.read_bytes()
    require(verify.digest(policy_body) == args.policy_sha256, "tail policy differs from independent review")
    policy = document(policy_body)
    checkout_root = Path(__file__).resolve().parents[3]
    git_env = {"PATH": "/usr/bin:/bin", "HOME": str(checkout_root), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout_root, env=git_env, text=True, timeout=30).strip()
    admit_github(os.environ, policy, checkout)
    require(not args.output.exists(), "attestation output must be a new private directory")
    tracked_backend = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "HEAD", "backend"],
                                              cwd=checkout_root, env=git_env, text=True, timeout=30).splitlines()
    validate_tail_inputs(checkout_root, policy["tail_inputs"], tracked_backend)
    sys.path.insert(0, str(checkout_root / "backend"))
    from seiche import stateful_recovery
    spec = importlib.util.spec_from_file_location("reviewed_storage", checkout_root / "ops/railway/resume_recovery.py")
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    with tempfile.TemporaryDirectory(prefix="native-attestation-") as temporary:
        private = Path(temporary)
        environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": temporary, "RUNNER_TEMP": temporary,
                       "GITHUB_WORKSPACE": str(checkout_root), "AWS_EC2_METADATA_DISABLED": "true"}
        environment.update({name: os.environ.pop("ATTEST_" + name) for name in
                            ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION", "S3_SSE_C_KEY_B64")})
        environment.update({name: policy[key] for name, key in (("S3_ENDPOINT", "storage_endpoint"), ("S3_BUCKET", "storage_bucket"), ("S3_PREFIX", "storage_prefix"))})
        live = Live(os.environ.pop("ATTEST_RAILWAY_TOKEN"), os.environ.pop("ATTEST_RAILWAY_EDGE_TOKEN"), private)
        storage = Storage(private, environment, original)
        subjects, proof = prepare_subjects(policy, storage, live, stateful_recovery,
                                          installation=(args.installation.read_bytes(), args.installation_signature.read_bytes()),
                                          now=datetime.now(timezone.utc))
        args.output.mkdir(mode=0o700)
        for name, body in subjects.items():
            (args.output / name).write_bytes(body)
        (args.output / "native-attestation-proof.json").write_bytes(verify.canonical(proof))
    public_proof = {name: proof[name] for name in ("status", "execution_platform", "attestation_platform", "restore_executed_here",
                    "observed_at", "controller_identity_proof", "installation_sha256", "controller_source", "controller_image_digest", "subjects")}
    print("RAILWAY_RECOVERY_ATTESTATION_SUBJECTS_PASS " + json.dumps(public_proof, sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Provider subprocess exceptions can contain protected object keys in argv.
        print("RAILWAY_RECOVERY_ATTESTATION_SUBJECTS_FAIL error_type=" + type(error).__name__, file=sys.stderr, flush=True)
        raise SystemExit(1) from None
