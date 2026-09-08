"""Materialize the reviewed monitor controller without including repository secrets."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

import yaml


WORKFLOW = ".github/workflows/railway-stateful-recovery.yml"
SIGNER_FINGERPRINT = "SHA256:yhoa/PIDMM6M/ZennILp8jtRJy5pArncJRARbQssTMI"
TARGET_NAMES = {
    "RAILWAY_PROJECT_ID", "RAILWAY_ENVIRONMENT_ID", "RAILWAY_POSTGRES_SERVICE_ID",
    "RAILWAY_STATEFUL_SERVICE_ID", "RAILWAY_STATEFUL_VOLUME_ID", "RAILWAY_STATEFUL_ORIGIN",
}
INPUTS = [
    "backend/seiche/__init__.py",
    "backend/seiche/stateful_control.py",
    "backend/seiche/stateful_migration.py",
    "ops/railway/retry_read.py",
    "ops/railway/wait_production_ready.py",
]


def selected_steps(document):
    """Ignore scheduling, preserving every source script and environment mapping."""
    job = document["jobs"]["monitor"]
    return [{key: step[key] for key in ("name", "run", "env") if key in step}
            for step in job["steps"] if "run" in step]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def prepare(repository, revision, output, target, signer_public_key):
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Selected source must be one full immutable commit SHA")
    if set(target) != TARGET_NAMES:
        raise ValueError("Target fields differ from the reviewed monitor schema")
    for name, value in target.items():
        pattern = (r"https://[a-z0-9][a-z0-9.-]{1,251}\.up\.railway\.app"
                   if name.endswith("ORIGIN") else
                   r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
        if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
            raise ValueError("Invalid fixed monitor target: " + name)
    def show(path):
        return subprocess.check_output(["git", "-C", str(repository), "show",
                                        f"{revision}:{path}"])

    here = Path(__file__).parent
    controller_source = subprocess.check_output(["git", "-C", str(here), "rev-parse", "HEAD"], text=True).strip()
    repository_root = Path(subprocess.check_output(["git", "-C", str(here), "rev-parse", "--show-toplevel"], text=True).strip())
    fingerprint = subprocess.check_output(["ssh-keygen", "-lf", str(signer_public_key)], text=True).split()[1]
    if fingerprint != SIGNER_FINGERPRINT:
        raise ValueError("Controller signer differs from the established owner key")
    with tempfile.TemporaryDirectory(prefix="monitor-signature-") as name:
        allowed = Path(name) / "allowed-signers"
        allowed.write_text("owner " + signer_public_key.read_text().strip() + "\n")
        subprocess.run(["git", "-C", str(here), "-c", "gpg.format=ssh", "-c",
                        "gpg.ssh.allowedSignersFile=" + str(allowed),
                        "verify-commit", controller_source], check=True)
    output.mkdir(parents=True, exist_ok=False)
    policy = {"source": revision, "controller_source": controller_source,
              "controller_signer": fingerprint, "inputs": {}, "target": target}
    for path in INPUTS:
        body = show(path)
        destination = output / "trusted" / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
        policy["inputs"][path] = hashlib.sha256(body).hexdigest()
    document = yaml.safe_load(show(WORKFLOW))
    steps = selected_steps(document)
    policy["workflow_steps_sha256"] = digest(steps)
    by_name = {step["name"]: step for step in steps}
    scripts = {
        "probe.sh": "Install the bounded PostgreSQL health probe transport",
        "proof.sh": "Prove native backups, PITR coverage, volume headroom, and both edges",
        "cleanup.sh": "Remove the private PostgreSQL probe transport",
    }
    for name, title in scripts.items():
        body = by_name[title]["run"]
        if name == "proof.sh":
            old = '--header "X-Seiche-Edge-Token: $RAILWAY_EDGE_TOKEN"'
            assert body.count(old) == 1
            body = body.replace(old, '--header "@$RUNNER_TEMP/edge-header"')
        (output / name).write_text("#!/usr/bin/env bash\n" + body)
    proof = by_name[scripts["proof.sh"]]["run"]
    validator = proof.split('OUTPUT="$GITHUB_OUTPUT"', 1)[1].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    (output / "validator.py").write_text(validator + "\n")
    (output / "policy.json").write_text(json.dumps(policy, indent=2) + "\n")
    for name in ("Dockerfile", "monitor.py", "prepare.py", "test_monitor.py", "requirements.lock"):
        path = (here / name).relative_to(repository_root)
        body = subprocess.check_output(["git", "-C", str(here), "show", f"{controller_source}:{path}"])
        (output / name).write_bytes(body)
    manifest = {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(output.rglob("*")) if path.is_file()}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    print(json.dumps({"prepared": str(output), "source": revision,
                      "workflow_steps_sha256": policy["workflow_steps_sha256"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--signer-public-key", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.repository, args.revision, args.output,
            json.loads(args.target.read_text()), args.signer_public_key)
