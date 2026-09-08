"""Materialize the reviewed monitor controller without including repository secrets."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import yaml


WORKFLOW = ".github/workflows/railway-stateful-recovery.yml"
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


def prepare(repository, revision, output, target):
    def show(path):
        return subprocess.check_output(["git", "-C", str(repository), "show",
                                        f"{revision}:{path}"])

    here = Path(__file__).parent
    controller_source = subprocess.check_output(["git", "-C", str(here), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(here), "status", "--porcelain", "--", "."], text=True)
    if dirty:
        raise ValueError("Commit the reviewed controller before preparing an image")
    output.mkdir(parents=True, exist_ok=False)
    policy = {"source": revision, "controller_source": controller_source,
              "inputs": {}, "target": target}
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
        shutil.copyfile(here / name, output / name)
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
    args = parser.parse_args()
    prepare(args.repository, args.revision, args.output,
            json.loads(args.target.read_text()))
