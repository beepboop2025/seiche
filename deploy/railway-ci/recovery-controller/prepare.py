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
FILES = ("Dockerfile", "verify.py", "native_docker.py", "restore.sh", "prepare.py", "test_verify.py", "requirements.lock", "README.md")


def digest(body):
    return hashlib.sha256(body).hexdigest()


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repository", "output", "case", "target", "public-key"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    prepare(args.repository, args.output, args.case, json.loads(args.target.read_text()), args.public_key)
