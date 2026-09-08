"""Reviewed Railway controller for Seiche's full engine publication path."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import time
import subprocess
import tarfile
import tempfile

import yaml

CONTROLLER = Path(__file__).resolve().parent
SOURCE = "https://github.com/beepboop2025/seiche.git"
MIRROR = "https://github.com/beepboop2025/seiche-site.git"
GATES = (
    "ops/release/verify_catalog_publication.py",
    "ops/release/verify_public_dataset.py",
)


def clean_env(extra=None):
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": "/dev/null",
    }
    env.update(extra or {})
    return env


def quiesce_builder():
    """Terminate every builder process, including children that detached via setsid."""
    for _ in range(100):
        subprocess.run(
            ["pkill", "-KILL", "-u", "10001"], check=False, capture_output=True
        )
        found = subprocess.run(
            ["pgrep", "-u", "10001"], check=False, capture_output=True
        )
        if found.returncode == 1:
            return
        if found.returncode != 0:
            raise RuntimeError("Unable to inspect the builder UID")
        time.sleep(0.05)
    raise RuntimeError("Builder UID did not quiesce before publication")


def run(args, cwd, env, unprivileged=False, capture=False):
    if unprivileged:
        args = [
            "setpriv",
            "--reuid=10001",
            "--regid=10001",
            "--init-groups",
            "--no-new-privs",
            *args,
        ]
    process = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        start_new_session=True,
        stdout=subprocess.PIPE if capture else None,
        text=True,
    )
    try:
        output, _ = process.communicate(timeout=5400)
        if process.returncode:
            raise RuntimeError(
                f"publication command failed: {args[0]} exit={process.returncode}"
            )
        return output.strip() if capture else None
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if unprivileged:
            quiesce_builder()


def git(args, cwd, env=None):
    return run(["git", *args], cwd, env or clean_env(), capture=True)


def current_main():
    # Preserve the workflow's exact-head Git transport check; shared anonymous
    # GitHub REST limits must not prevent an otherwise valid publication.
    value = git(["ls-remote", "--exit-code", SOURCE, "refs/heads/main"], CONTROLLER)
    sha, ref = value.split()
    if ref != "refs/heads/main" or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("Invalid source identity")
    return sha


def assert_plain_path(path, boundary):
    path.relative_to(boundary)
    for parent in [path, *path.parents]:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("Publication directory has a symlink or non-directory ancestor")
        if parent == boundary:
            return
    raise RuntimeError("Publication path escaped its boundary")


def copy_public_tree(source, target):
    assert_plain_path(source, Path(source.anchor))
    total = count = 0
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if ".git" in relative.parts:
            continue
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise RuntimeError("Unsafe publication entry: " + str(relative))
        if path.is_file():
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise RuntimeError(
                        "Publication entry is not a single-link regular file"
                    )
                total += info.st_size
                count += 1
                if total > 500 * 1024 * 1024 or count > 20000:
                    raise RuntimeError("Publication exceeds the reviewed size bound")
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as output:
                    shutil.copyfileobj(stream, output)
    if not (target / "index.html").is_file():
        raise RuntimeError("Publication has no entry point")


def verify_publication(steps, trusted, candidate, env, receipt):
    run(["python", "-I", "-S", str(trusted / "ops/release/verify_public_dataset.py"),
         "--expected-root", str(candidate), "--cache-key", env["PUBLICATION_SOURCE_SHA"]
         + "-" + env["GITHUB_RUN_ATTEMPT"]], trusted, env)


def copy_history(source, destination, builder=False):
    """Retain only bounded JSON data, never a cache-controlled executable or link."""
    assert_plain_path(source.parent, Path(source.anchor))
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 32*1024*1024:
            raise RuntimeError("Unsafe GDELT history")
        data = stream.read(32*1024*1024 + 1)
    if len(data) > 32*1024*1024:
        raise RuntimeError("GDELT history exceeded bound")
    json.loads(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    destination.chmod(0o600)
    if builder:
        shutil.chown(destination.parent, user=10001, group=10001)
        shutil.chown(destination, user=10001, group=10001)
    else:
        with destination.open("rb") as stream:
            os.fsync(stream.fileno())


def main():
    source_sha = current_main()
    expected = os.environ.get("PUBLICATION_SOURCE_SHA", source_sha)
    if expected != source_sha:
        raise RuntimeError("Requested source is no longer current main")
    apply = os.environ.get("PUBLISH_APPLY") == "1"
    evidence = Path("/evidence")
    prior_state = None
    if os.path.ismount(evidence):
        evidence.chmod(0o700)
        state_path = evidence / "current.json"
        if apply and state_path.is_file():
            prior_state = json.loads(state_path.read_text())
    signer = os.environ["RELEASE_SIGNING_KEY_FINGERPRINT"]
    workflow_path = CONTROLLER / "publish.yml"
    workflow = yaml.safe_load(workflow_path.read_text())
    steps = {
        step.get("name"): step for step in workflow["jobs"]["build-and-deploy"]["steps"]
    }
    with tempfile.TemporaryDirectory(prefix="seiche-publication-") as directory:
        root = Path(directory)
        root.chmod(0o755)
        trusted = root / "trusted"
        git(["clone", "--quiet", SOURCE, str(trusted)], root)
        git(["checkout", "--quiet", "--detach", source_sha], trusted)
        if (
            trusted / ".github/workflows/publish.yml"
        ).read_bytes() != workflow_path.read_bytes():
            raise RuntimeError(
                "Publication workflow changed; update the reviewed controller"
            )
        # Execute verification code only when its blob matches the controller's pinned manifest.
        manifest = json.loads((CONTROLLER / "gate-sha256.json").read_text())
        for name in GATES:
            if (
                hashlib.sha256((trusted / name).read_bytes()).hexdigest()
                != manifest[name]
            ):
                raise RuntimeError(
                    "Publication verifier changed; update the reviewed controller"
                )
        receipt = ""  # Full engine output requires the original backend release gate.
        temp = root / "temp"
        temp.mkdir()
        env = clean_env(
            {
                "GITHUB_SHA": source_sha,
                "PUBLICATION_SOURCE_SHA": source_sha,
                "GITHUB_WORKSPACE": str(trusted),
                "RUNNER_TEMP": str(temp),
                "FRONTEND_RECEIPT_TAG": receipt,
                "RELEASE_SIGNING_KEY_FINGERPRINT": signer,
                "GITHUB_RUN_ATTEMPT": str(time.time_ns()),
            }
        )
        for name in (
            "Fetch the exact declared release tag",
            "Gate catalog on the signed release, runtime, and PyPI receipts",
        ):
            print("RAILWAY_FULL_STEP " + name, flush=True)
            command = steps[name]["run"].replace("frontend/dist/.well-known/ai-catalog.json",
                                                   "frontend/public/.well-known/ai-catalog.json")
            run(["bash", "-euo", "pipefail", "-c", command], trusted, env)
        mirror = root / "mirror"
        git(["clone", "--quiet", MIRROR, str(mirror)], root)
        previous_sha = git(["rev-parse", "HEAD"], mirror)
        digest = hashlib.sha256()
        for name in ("publish.py", "Dockerfile", "publish.yml", "gate-sha256.json",
                     "requirements-social-cards.txt"):
            digest.update(name.encode() + b"\0" + (CONTROLLER / name).read_bytes())
        controller_digest = digest.hexdigest()
        reusable = bool(prior_state and prior_state.get("source") == source_sha
                        and prior_state.get("controller_digest") == controller_digest)
        build = root / "build"
        git(["clone", "--quiet", "--no-hardlinks", str(trusted), str(build)], root)
        shutil.chown(build, user=10001, group=10001)
        for path in build.rglob("*"):
            if not path.is_symlink():
                shutil.chown(path, user=10001, group=10001)
        # The builder can read the public source but cannot read controller credentials.
        build_temp = root / "build-temp"
        build_temp.mkdir()
        shutil.chown(build_temp, user=10001, group=10001)
        build_env = clean_env(
            {
                "GITHUB_SHA": source_sha,
                "PUBLICATION_SOURCE_SHA": source_sha,
                "GITHUB_WORKSPACE": str(build),
                "RUNNER_TEMP": str(build_temp),
                "FRONTEND_RECEIPT_TAG": receipt,
                "XDG_CACHE_HOME": str(build_temp / "cache"),
            }
        )
        history = evidence / "gdelt-web-history.json"
        if history.is_file():
            copy_history(history, build / ".cache/gdelt-web-history.json", builder=True)
        run(["python", "-m", "venv", str(build_temp / "venv")], build,
            build_env, unprivileged=True)
        build_env.update({
            "PATH": str(build_temp / "venv/bin") + ":" + build_env["PATH"],
            "HOME": str(build_temp),
            "PUBLICATION_SOURCE_SHA": source_sha,
            "GDELT_WEB_HISTORY_FILE": str(build / ".cache/gdelt-web-history.json"),
            "RUN_FULL_SUITE": "false" if reusable else "true",
        })
        for name in (
            "Install backend",
            "Install hash-locked social-card renderer",
            "Engine tests (publish gates on green)",
            "Seed GDELT WEB-NGRAM baseline when cache is cold",
            "Run engines, export snapshot",
            "Append Book record (hash-chained ledger)",
            "Render dispatch pages", "Render methodology page", "Render skeptic pack",
            "Render ampleness check", "Render referee page",
            "Test and build frontend (snapshot baked into dist/)",
            "Prerender the no-JS home page", "Build contextual social cards",
        ):
            if reusable and name == "Engine tests (publish gates on green)":
                print("RAILWAY_FULL_STEP exact source/controller test proof reused", flush=True)
                continue
            print("RAILWAY_FULL_STEP " + name, flush=True)
            run(["bash", "-euo", "pipefail", "-c", steps[name]["run"]],
                build, build_env, unprivileged=True)
        prepared = build / "frontend/dist"
        quiesce_builder()
        candidate = root / "candidate"
        candidate.mkdir()
        copy_public_tree(prepared, candidate)
        run(["python", "-I", "-S", str(trusted / GATES[0]), "--root", str(trusted),
             "--expected-sha", source_sha, "--signer-fingerprint", signer,
             "--published-catalog", str(candidate / ".well-known/ai-catalog.json")],
            trusted, clean_env())
        if current_main() != source_sha:
            raise RuntimeError("Source main advanced during preparation")
        print(
            f"RAILWAY_FULL_PREPARE_PASS source={source_sha} previous_site={previous_sha} receipt={receipt or 'application'}",
            flush=True,
        )
        if not apply:
            return
        if not os.path.ismount(evidence):
            raise RuntimeError("Durable recovery volume is required before publication")
        recovery = evidence / (source_sha + "-" + root.name)
        recovery.mkdir(parents=True, exist_ok=False)
        if (
            git(["ls-remote", "origin", "refs/heads/main"], mirror).split()[0]
            != previous_sha
        ):
            raise RuntimeError("Site mirror advanced during preparation")
        with tarfile.open(recovery / "previous-site.tar", "w") as archive:
            for path in mirror.iterdir():
                if path.name != ".git":
                    archive.add(path, arcname=path.name)
        (recovery / "identity.json").write_text(
            json.dumps({"source": source_sha, "previous_site": previous_sha})
        )
        for path in recovery.rglob("*"):
            if path.is_file():
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
        key = root / "publisher-key"
        key.write_text(os.environ["SITE_DEPLOY_KEY"] + "\n")
        key.chmod(0o600)
        ssh = f"ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={CONTROLLER / 'github-known-hosts'}"
        publish_env = clean_env({"GIT_SSH_COMMAND": ssh})
        git(
            [
                "remote",
                "set-url",
                "origin",
                "git@github.com:beepboop2025/seiche-site.git",
            ],
            mirror,
        )
        run(
            [
                "rsync",
                "-a",
                "--delete",
                "--exclude=.git",
                str(candidate) + "/",
                str(mirror) + "/",
            ],
            root,
            clean_env(),
        )
        git(["config", "user.name", "seiche-railway-publish"], mirror)
        git(["config", "user.email", "noreply@seiche"], mirror)
        git(["add", "-A"], mirror)
        if git(["status", "--porcelain"], mirror):
            git(
                ["commit", "-m", "Verified Railway full publication " + source_sha],
                mirror,
            )
            if current_main() != source_sha:
                raise RuntimeError("Source main advanced before mirror publication")
            git(["push", "origin", "HEAD:main"], mirror, publish_env)
        site_sha = git(["rev-parse", "HEAD"], mirror)
        if (
            git(
                ["ls-remote", "origin", "refs/heads/main"], mirror, publish_env
            ).split()[0]
            != site_sha
        ):
            raise RuntimeError("Site mirror compare-and-swap lost")
        if current_main() != source_sha:
            raise RuntimeError("Source main advanced before canonical publication")
        run(
            [
                "/opt/node22/bin/node",
                "/opt/publisher/node_modules/wrangler/bin/wrangler.js",
                "pages",
                "deploy",
                str(candidate),
                "--project-name=seiche",
                "--branch=main",
                "--commit-hash",
                source_sha,
            ],
            CONTROLLER,
            clean_env(
                {
                    "CLOUDFLARE_API_TOKEN": os.environ["CLOUDFLARE_API_TOKEN"],
                    "CLOUDFLARE_ACCOUNT_ID": os.environ["CLOUDFLARE_ACCOUNT_ID"],
                }
            ),
        )
        verify_publication(
            steps,
            trusted,
            candidate,
            clean_env({**env, "RUNNER_TEMP": str(build_temp)}),
            receipt,
        )
        # Publish-success commits the history cache; failed generations never replace it.
        generated_history = build / ".cache/gdelt-web-history.json"
        if generated_history.is_file():
            copy_history(generated_history, evidence / "gdelt-web-history.json.tmp")
            (evidence / "gdelt-web-history.json.tmp").replace(history)
        state = evidence / "current.json.tmp"
        state.write_text(
            json.dumps(
                {
                    "source": source_sha,
                    "site": site_sha,
                    "recovery": recovery.name,
                    "verified_at": time.time(),
                    "controller_digest": controller_digest,
                }
            )
        )
        with state.open("rb") as stream:
            os.fsync(stream.fileno())
        state.replace(evidence / "current.json")
        print(
            f"RAILWAY_FULL_PUBLISH_PASS source={source_sha} site={site_sha}",
            flush=True,
        )


if __name__ == "__main__":
    main()
