"""Run the existing strict recovery monitor from a pinned Railway controller."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

import yaml

from prepare import WORKFLOW, digest, selected_steps


ROOT = Path(__file__).resolve().parent
REPOSITORY = "https://github.com/beepboop2025/seiche.git"
SECRETS = ("RAILWAY_TOKEN", "RAILWAY_EDGE_TOKEN", "RAILWAY_RECOVERY_PROBE_SSH_KEY")
TARGET_NAMES = (
    "RAILWAY_PROJECT_ID", "RAILWAY_ENVIRONMENT_ID", "RAILWAY_POSTGRES_SERVICE_ID",
    "RAILWAY_STATEFUL_SERVICE_ID", "RAILWAY_STATEFUL_VOLUME_ID", "RAILWAY_STATEFUL_ORIGIN",
)


def public_env(home):
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"}


def checked(arguments, *, env, cwd=None):
    result = subprocess.run(arguments, env=env, cwd=cwd, capture_output=True,
                            timeout=90, check=False)
    if result.returncode:
        raise RuntimeError("Read-only source identity operation failed")
    return result.stdout


def admit(source, policy, env):
    source.mkdir(mode=0o700)
    checked(["git", "init", "-q", str(source)], env=env)
    checked(["git", "remote", "add", "origin", REPOSITORY], env=env, cwd=source)
    checked(["git", "-c", "core.hooksPath=/dev/null", "fetch", "--depth=1",
             "origin", "refs/heads/main"], env=env, cwd=source)
    sha = checked(["git", "rev-parse", "FETCH_HEAD"], env=env, cwd=source).decode().strip()
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise RuntimeError("Invalid current source identity")
    checked(["git", "update-ref", "HEAD", sha], env=env, cwd=source)
    for path, expected in policy["inputs"].items():
        body = checked(["git", "show", f"{sha}:{path}"], env=env, cwd=source)
        pinned = ROOT / "trusted" / path
        if hashlib.sha256(body).hexdigest() != expected or body != pinned.read_bytes():
            raise RuntimeError("Reviewed recovery helper changed: " + path)
        if path.startswith("backend/"):
            destination = source / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(pinned.read_bytes())
    workflow = checked(["git", "show", f"{sha}:{WORKFLOW}"], env=env, cwd=source)
    if digest(selected_steps(yaml.safe_load(workflow))) != policy["workflow_steps_sha256"]:
        raise RuntimeError("Reviewed monitor semantics changed; controller update required")
    return sha


def read_env_file(path):
    result = {}
    for line in path.read_text().splitlines():
        name, value = line.split("=", 1)
        if name not in {"SSH_AUTH_SOCK", "SSH_AGENT_PID"}:
            raise RuntimeError("Unexpected probe environment output")
        result[name] = value
    return result


def main():
    os.umask(0o077)
    manifest_body = (ROOT / "manifest.json").read_bytes()
    for path, expected in json.loads(manifest_body).items():
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != expected:
            raise RuntimeError("Controller image content differs from reviewed manifest")
    controller_digest = hashlib.sha256(manifest_body).hexdigest()
    policy = json.loads((ROOT / "policy.json").read_text())
    credentials = {name: os.environ.pop("MONITOR_" + name) for name in SECRETS}
    if set(policy["target"]) != set(TARGET_NAMES):
        raise RuntimeError("Recovery target configuration differs from reviewed schema")
    for name, value in credentials.items():
        if not value or (name != "RAILWAY_RECOVERY_PROBE_SSH_KEY" and
                         any(ord(char) < 32 for char in value)):
            raise RuntimeError("Invalid protected monitor input: " + name)
    deployment = os.environ.get("RAILWAY_DEPLOYMENT_ID", "unavailable")
    controller = policy["controller_source"]
    evidence_base = Path("/evidence")
    evidence_base.mkdir(mode=0o700, exist_ok=True)
    evidence = evidence_base / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix="recovery-monitor-") as name:
        temporary = Path(name)
        env = public_env(temporary)
        source = temporary / "source"
        sha = admit(source, policy, env)
        env.update(policy["target"])
        env.update(credentials)
        env.update({
            "RAILWAY_SERVICE_ID": env["RAILWAY_STATEFUL_SERVICE_ID"],
            "RAILWAY_VOLUME_ID": env["RAILWAY_STATEFUL_VOLUME_ID"],
            "RAILWAY_ORIGIN": env["RAILWAY_STATEFUL_ORIGIN"],
            "PROBE_SSH_KEY": env.pop("RAILWAY_RECOVERY_PROBE_SSH_KEY"),
            "RAILWAY_REAL_BIN": "/usr/local/bin/railway-real",
            "RECOVERY_SOURCE_SHA": sha, "REQUESTED_SOURCE_SHA": "",
            "GITHUB_SHA": sha, "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "schedule", "GITHUB_WORKSPACE": str(source),
            "RUNNER_TEMP": str(temporary), "EVIDENCE_ROOT": str(evidence),
            "GITHUB_PATH": str(temporary / "paths"),
            "GITHUB_ENV": str(temporary / "environment"),
            "GITHUB_OUTPUT": str(temporary / "outputs"),
            "GITHUB_STEP_SUMMARY": str(temporary / "summary"),
            "ALLOW_EMPTY_EXPORT_PROOF": "false",
        })
        header = temporary / "edge-header"
        header.write_text("X-Seiche-Edge-Token: " + env["RAILWAY_EDGE_TOKEN"] + "\n")
        try:
            subprocess.run(["bash", str(ROOT / "probe.sh")], env=env, cwd=source,
                           check=True, timeout=120)
            env.pop("PROBE_SSH_KEY")
            env.update(read_env_file(temporary / "environment"))
            env["PATH"] = (temporary / "paths").read_text().strip() + ":" + env["PATH"]
            subprocess.run(["bash", str(ROOT / "proof.sh")], env=env, cwd=source,
                           check=True, timeout=1650)
            identity = dict(line.split("=", 1) for line in
                            (temporary / "outputs").read_text().splitlines())
            pair = json.loads((evidence / "monitor-pair-identity.json").read_text())
            proof = {"schema": "seiche.railway-recovery-monitor-proof.v2",
                     "status": "pass", "repository": "beepboop2025/seiche",
                     "source": sha, "controller_source": controller,
                     "controller_digest": controller_digest,
                     "controller_inputs": policy["workflow_steps_sha256"],
                     "railway_deployment": deployment, "bootstrap": False,
                     "observed_at": datetime.now(timezone.utc).isoformat(),
                     **identity, **pair}
            (evidence / "monitor-proof.json").write_text(json.dumps(proof, sort_keys=True) + "\n")
            print("RAILWAY_RECOVERY_MONITOR_PASS " + json.dumps(proof, sort_keys=True), flush=True)
        finally:
            subprocess.run(["bash", str(ROOT / "cleanup.sh")], env=env, cwd=source,
                           timeout=30, check=False)
    cutoff = datetime.now(timezone.utc).timestamp() - 90 * 86400
    for old in evidence_base.iterdir():
        if (old.is_dir() and not old.is_symlink() and
                re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", old.name) and old.stat().st_mtime < cutoff):
            shutil.rmtree(old)


if __name__ == "__main__":
    main()
