"""Run the portable distribution workflow jobs with isolated Python environments."""

import itertools
import os
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/distribution-contracts.yml"
JOBS = (
    "offline-distribution", "r-client", "native-dataset-metadata", "seiche-package",
    "openbb-runtime-compatibility", "openbb-provider",
)
SETUP_ACTIONS = {
    "actions/checkout", "actions/setup-python", "actions/setup-node", "r-lib/actions/setup-r",
}


def expand(value, context):
    if not isinstance(value, str):
        raise ValueError("Workflow environment values must be strings")
    value = re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", lambda m: context[m[1].strip()], value)
    if "${{" in value:
        raise ValueError("Unresolved workflow expression")
    return value


def run(command, *, env, cwd=ROOT):
    subprocess.run(command, cwd=cwd, env=env, check=True, timeout=1800)


def run_job(name, job, matrix):
    with tempfile.TemporaryDirectory(prefix="railway-distribution-") as directory:
        temp = Path(directory)
        context = {"github.workspace": str(ROOT), "runner.temp": directory}
        context.update({"matrix." + key: value for key, value in matrix.items()})
        python_version = "3.12.12"
        for step in job["steps"]:
            action = step.get("uses", "").split("@", 1)[0]
            settings = step.get("with", {})
            if action == "actions/setup-python":
                python_version = expand(settings["python-version"], context)
            elif action == "actions/setup-node":
                if settings.get("node-version") != "22":
                    raise ValueError("Node runtime changed; update the Railway image")
            elif action == "r-lib/actions/setup-r":
                if settings.get("r-version") != "release":
                    raise ValueError("R runtime changed; update the Railway image")
            elif action == "actions/checkout" and any(key in settings for key in ("ref", "repository", "path")):
                raise ValueError("Custom checkout requires an explicit Railway adapter")
        env = dict(os.environ)
        for key in ("GITHUB_TOKEN", "GH_TOKEN", "RAILWAY_TOKEN", "RAILWAY_API_TOKEN",
                    "CLOUDFLARE_API_TOKEN", "PYPI_TOKEN"):
            if env.get(key):
                raise RuntimeError(f"Refusing a publishing credential in CI: {key}")
        venv = temp / "venv"
        run(["uv", "venv", "--seed", "--python", python_version, str(venv)], env=env)
        run([str(venv / "bin/python"), "-c",
             "import sys; assert sys.version_info.releaselevel == 'final', sys.version; print(sys.version)"], env=env)
        env.update({"PATH": str(venv / "bin") + ":" + env["PATH"],
                    "VIRTUAL_ENV": str(venv), "RUNNER_TEMP": directory,
                    "GITHUB_WORKSPACE": str(ROOT), "GITHUB_ENV": str(temp / "env"),
                    "GITHUB_OUTPUT": str(temp / "output"), "GITHUB_PATH": str(temp / "path"),
                    "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_ADDOPTS": "--tb=short"})
        env.update({key: expand(value, context) for key, value in job.get("env", {}).items()})
        defaults = job.get("defaults", {}).get("run", {})
        for index, step in enumerate(job["steps"]):
            if "if" in step or "continue-on-error" in step:
                raise ValueError("Conditional workflow steps need an explicit Railway adapter")
            if "uses" in step:
                if step["uses"].split("@", 1)[0] not in SETUP_ACTIONS:
                    raise ValueError("Unexpected action in portable job: " + step["uses"])
                continue
            if "run" not in step:
                raise ValueError("Workflow step has neither run nor setup action")
            for line in (temp / "env").read_text().splitlines() if (temp / "env").exists() else []:
                key, separator, value = line.partition("=")
                if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                    raise ValueError("Unsupported GITHUB_ENV record")
                env[key] = value
            step_env = dict(env)
            step_env.update({key: expand(value, context) for key, value in step.get("env", {}).items()})
            script = temp / f"step-{index}.sh"
            script.write_text("set -euo pipefail\n" + expand(step["run"], context))
            cwd = (ROOT / step.get("working-directory", defaults.get("working-directory", "."))).resolve()
            if not cwd.is_relative_to(ROOT):
                raise ValueError("Workflow working directory escapes repository")
            print(f"RAILWAY_DISTRIBUTION_STEP job={name} python={python_version} name={step.get('name', index)}", flush=True)
            run(["bash", str(script)], env=step_env, cwd=cwd)
        print(f"RAILWAY_DISTRIBUTION_JOB_PASS job={name} python={python_version}", flush=True)


def main():
    source = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise RuntimeError("Missing canonical source identity")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if source != actual:
        raise RuntimeError("Runtime source differs from the verified Git checkout")
    workflow = yaml.safe_load(WORKFLOW.read_text())
    for name in JOBS:
        job = workflow["jobs"][name]
        axes = job.get("strategy", {}).get("matrix", {})
        for values in itertools.product(*axes.values()):
            run_job(name, job, dict(zip(axes, values)))
    print(f"RAILWAY_DISTRIBUTION_PORTABLE_PASS source={source} deployment={os.environ.get('RAILWAY_DEPLOYMENT_ID', 'build')}", flush=True)


if __name__ == "__main__":
    main()
