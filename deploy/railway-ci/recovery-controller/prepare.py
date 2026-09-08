"""Assemble owner-signed verification code and the attested immutable recovery."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

import yaml


WORKFLOW = ".github/workflows/railway-stateful-recovery.yml"
ORIGINAL_SOURCE = "fff0eabb26088292451edaef10bdd203c074a992"
ORIGINAL_RUN = 34241275921
FINGERPRINT = "SHA256:yhoa/PIDMM6M/ZennILp8jtRJy5pArncJRARbQssTMI"
RECOVERY_HASH = "f10919a2dc77d6a73cff45ecfc00941a7aff529b115a9588474811e185d930a2"
OFFSITE_HASH = "0c0093b0afcc5c8e7233fdbb6f8e916d3d4600160286e21bfacca1ac86bdda68"
METADATA = ("activation-receipt.json", "candidate-receipt.json", "shadow-receipt.json", "request.json", "recovery-receipt.json", "offsite-receipt.json")
FILES = ("Dockerfile", "verify.py", "native_docker.py", "restore.sh", "prepare.py", "test_verify.py", "requirements.lock", "README.md", "recurring.py", "attest.py", "test_attest.py", "test_recurring.py")


def digest(body):
    return hashlib.sha256(body).hexdigest()


RECURRING_STEPS = {
    "export-native.sh": "Request and download an activation-bound portable export",
    "restore-native.sh": "Perform an isolated filesystem and PostgreSQL reverse-restore proof",
    "seal-native.sh": "Seal the portable export in external S3 Object Lock compliance mode",
}
NATIVE_NAMES = {
    "GITHUB_WORKSPACE": "TRUSTED_SOURCE", "GITHUB_REPOSITORY": "GOVERNANCE_REPOSITORY",
    "GITHUB_RUN_ID": "NATIVE_DEPLOYMENT_ID", "GITHUB_RUN_ATTEMPT": "NATIVE_REPLICA_ID",
    "GITHUB_EVENT_NAME": "NATIVE_INVOCATION", "GITHUB_OUTPUT": "NATIVE_OUTPUT",
    "GITHUB_STEP_SUMMARY": "NATIVE_SUMMARY", "RUNNER_TEMP": "PRIVATE_TEMP",
}


def private_download_transport(body):
    """GitHub mask commands do not redact native logs; keep bearer bytes private."""
    mask = 'echo "::add-mask::$download_bearer"'
    header = '--header "Authorization: Bearer $download_bearer"'
    if body.count(mask) != 1 or body.count(header) != 1:
        raise ValueError("original recovery bearer transport changed")
    body = body.replace(mask, "printf 'Authorization: Bearer %s\\n' \"$download_bearer\" >\"$PRIVATE_TEMP/download-header\"\nunset download_bearer")
    return body.replace(header, '--header "@$PRIVATE_TEMP/download-header"')


def recurring_scripts(document):
    """Only transport names and private curl-header files differ from original scripts."""
    steps = {step["name"]: step["run"] for step in document["jobs"]["export-recovery"]["steps"] if "run" in step}
    result = {}
    for filename, title in RECURRING_STEPS.items():
        body = steps[title]
        for old, new in NATIVE_NAMES.items():
            body = body.replace(old, new)
        header = '--header "X-Seiche-Edge-Token: $RAILWAY_EDGE_TOKEN"'
        if filename != "restore-native.sh":
            if header not in body:
                raise ValueError("original native edge-header transport changed")
            body = body.replace(header, '--header "@$PRIVATE_TEMP/edge-header"')
        if filename == "export-native.sh":
            body = private_download_transport(body)
        if "GITHUB_" in body or "${{" in body:
            raise ValueError("unmapped GitHub execution input in the native recovery body")
        result[filename] = body
    return result


def prepare(repository, output, case, target, public_key):
    here = Path(__file__).resolve().parent
    controller = subprocess.check_output(["git", "-C", str(here), "rev-parse", "HEAD"], text=True).strip()
    if re.fullmatch(r"[0-9a-f]{40}", controller) is None:
        raise ValueError("controller source is not immutable")
    fingerprint = subprocess.check_output(["ssh-keygen", "-lf", str(public_key)], text=True).split()[1]
    if fingerprint != FINGERPRINT:
        raise ValueError("signer differs from established owner key")
    with tempfile.TemporaryDirectory(prefix="recovery-signature-") as name:
        allowed = Path(name) / "signers"
        allowed.write_text("owner " + public_key.read_text().strip() + "\n")
        subprocess.run(["git", "-C", str(here), "-c", "gpg.format=ssh", "-c", "gpg.ssh.allowedSignersFile=" + str(allowed), "verify-commit", controller], check=True)
    if set(target) != {"S3_ENDPOINT", "S3_BUCKET", "S3_PREFIX", "AWS_DEFAULT_REGION"}:
        raise ValueError("storage target schema differs")
    def show(revision, path):
        return subprocess.check_output(["git", "-C", str(repository), "show", revision + ":" + path])
    recovery_body = (case / "recovery-receipt.json").read_bytes()
    offsite_body = (case / "offsite-receipt.json").read_bytes()
    if digest(recovery_body) != RECOVERY_HASH or digest(offsite_body) != OFFSITE_HASH:
        raise ValueError("case differs from the independently verified governed recovery")
    for name in ("recovery-receipt.json", "offsite-receipt.json"):
        subprocess.run(["gh", "attestation", "verify", str(case / name), "--repo", "beepboop2025/seiche", "--signer-workflow", "beepboop2025/seiche/" + WORKFLOW, "--source-ref", "refs/heads/main", "--source-digest", ORIGINAL_SOURCE], check=True)
    original, offsite = json.loads(recovery_body), json.loads(offsite_body)
    head = json.loads((case / "proof/offsite-receipt.head.json").read_text())
    if head["Metadata"]["sha256"] != OFFSITE_HASH or head["ContentLength"] != len(offsite_body):
        raise ValueError("offsite receipt version proof differs")
    if target["S3_BUCKET"] != offsite["bucket"] or target["S3_PREFIX"] != offsite["prefix"]:
        raise ValueError("storage target differs from attested immutable receipt")
    output.mkdir(parents=True, exist_ok=False)
    for name in FILES:
        (output / name).write_bytes(show(controller, "deploy/railway-ci/recovery-controller/" + name))
    steps = yaml.safe_load(show(ORIGINAL_SOURCE, WORKFLOW))["jobs"]["export-recovery"]["steps"]
    source_restore = next(step["run"] for step in steps if step["name"] == "Perform an isolated filesystem and PostgreSQL reverse-restore proof")
    if source_restore.count('Path("proof/reverse-restore.json")') != 1:
        raise ValueError("original restore output contract changed")
    expected_restore = source_restore.replace('Path("proof/reverse-restore.json")', 'Path("proof/railway-reverse-restore.json")')
    if (output / "restore.sh").read_text() != expected_restore:
        raise ValueError("native restore differs from the original body beyond its separate output name")
    names = subprocess.check_output(["git", "-C", str(repository), "ls-tree", "-r", "--name-only", ORIGINAL_SOURCE, "backend"], text=True).splitlines()
    names += ["ops/railway/resume_recovery.py", "ops/deploy/seiche-s3-object-lock.sh"]
    for name in names:
        destination = output / "trusted" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(show(ORIGINAL_SOURCE, name))
    (output / "case").mkdir()
    for name in METADATA:
        (output / "case" / name).write_bytes((case / name).read_bytes())
    root = f"{offsite['prefix']}/{offsite['snapshot_id']}/{offsite['request_id']}"
    policy = {"controller_source": controller, "controller_signer": fingerprint, "source": ORIGINAL_SOURCE,
              "original_github_run": ORIGINAL_RUN, "application_source": original["commit"],
              "application_deployment": original["railway"]["deployment_id"], "request_id": original["request_id"],
              "recovery_receipt_sha256": RECOVERY_HASH, "storage_target": target,
              "offsite_object": {"key": root + "/offsite-receipt.json", "version_id": head["VersionId"], "sha256": OFFSITE_HASH, "size": len(offsite_body)}}
    (output / "policy.json").write_text(json.dumps(policy, sort_keys=True) + "\n")
    manifest = {str(path.relative_to(output)): digest(path.read_bytes()) for path in sorted(output.rglob("*")) if path.is_file()}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    print(json.dumps({"prepared": str(output), "controller_source": controller, "controller_digest": digest((output / "manifest.json").read_bytes()), "files": len(manifest)}))


RECOVERY_HELPERS = ("ops/railway/resume_recovery.py", "ops/railway/retry_read.py", "ops/railway/fetch_recovery_logs.py",
                    "ops/railway/wait_production_ready.py", "ops/deploy/seiche-s3-object-lock.sh")


def admitted_source_paths(names):
    return {name for name in names if name in RECOVERY_HELPERS or
            (name.startswith("backend/") and not name.startswith("backend/tests/")
             and not name.startswith("backend/seiche/dispatches/"))}


def add_recurring_assembly(repository, output, revision, target_path, public_key_path, signer_public_key):
    """Add a disarmed native job around the original scripts and strict monitor."""
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("recurring source must be one immutable commit")
    public_hex = public_key_path.read_text().strip()
    if re.fullmatch(r"[0-9a-f]{64}", public_hex) is None:
        raise ValueError("native evidence public key must be one raw Ed25519 key")
    source = lambda path: subprocess.check_output(["git", "-C", str(repository), "show", revision + ":" + path])
    original = yaml.safe_load(subprocess.check_output(["git", "-C", str(repository), "show", ORIGINAL_SOURCE + ":" + WORKFLOW]))
    for name, body in recurring_scripts(original).items():
        (output / name).write_text(body)
    target = json.loads(target_path.read_text())
    # The reused monitor validates the fixed production IDs/origin and controller signature.
    subprocess.run(["python", str(Path(__file__).parent.parent / "recovery-monitor/prepare.py"),
                    "--repository", str(repository), "--revision", revision,
                    "--output", str(output / "monitor"), "--target", str(target_path),
                    "--signer-public-key", str(signer_public_key)], check=True)
    names = subprocess.check_output(["git", "-C", str(repository), "ls-tree", "-r", "--name-only", revision, "backend"], text=True).splitlines()
    names += list(RECOVERY_HELPERS)
    for name in names:
        destination = output / "trusted" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source(name))
    policy = json.loads((output / "policy.json").read_text())
    policy.update(operation="export-recurring", source=revision, production_target=target,
                  execution_public_key=public_hex,
                  controller_project_id="9c094747-8662-4ba7-8d6b-5a4fa7ca27eb",
                  controller_environment_id="e16a2d28-22b0-4028-9a6e-e67710ecbe5e",
                  controller_service_id="1edc46a6-25e8-4497-941f-0903eb8f6e5d")
    policy["recurring_steps_sha256"] = digest(json.dumps(
        [{name: step[name] for name in ("name", "run", "env") if name in step}
         for step in original["jobs"]["export-recovery"]["steps"] if step.get("name") in RECURRING_STEPS.values()],
        sort_keys=True, separators=(",", ":")).encode())
    policy["trusted_source_sha256"] = {name: digest(source(name)) for name in admitted_source_paths(names)}
    (output / "policy.json").write_text(json.dumps(policy, sort_keys=True) + "\n")
    manifest = {str(path.relative_to(output)): digest(path.read_bytes()) for path in sorted(output.rglob("*"))
                if path.is_file() and path != output / "manifest.json"}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    print(json.dumps({"prepared_recurring": str(output), "controller_source": policy["controller_source"],
                      "manifest_sha256": digest((output / "manifest.json").read_bytes()), "activation": "disarmed"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repository", "output", "case", "target", "public-key"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--recurring-source")
    parser.add_argument("--production-target", type=Path)
    parser.add_argument("--execution-public-key", type=Path)
    args = parser.parse_args()
    prepare(args.repository, args.output, args.case, json.loads(args.target.read_text()), args.public_key)

    if args.recurring_source:
        if not args.production_target or not args.execution_public_key:
            parser.error("recurring preparation requires fixed production target and evidence public key")
        add_recurring_assembly(args.repository, args.output, args.recurring_source,
                               args.production_target, args.execution_public_key, args.public_key)
