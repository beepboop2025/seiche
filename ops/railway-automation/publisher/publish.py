"""Reviewed Railway controller for Seiche's bounded static publication path."""

import argparse
from datetime import datetime, timezone
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
    "ops/release/verify_frontend_publication.py",
    "ops/release/frontend_site_proof.py",
    "ops/release/verify_public_dataset.py",
    "ops/release/hetzner_release.py",
)
SOURCE_HEALTH_HELPER = "ops/release/source_degradation.py"
MAX_SOURCE_HEALTH_PACKAGE_BYTES = 1024 * 1024


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
        output, _ = process.communicate(timeout=600)
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
        process.wait(timeout=5)
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



def verify_gate_files(checkout, manifest):
    """Pin every privileged verifier before importing code from a checkout."""
    for name in GATES:
        path = checkout / name
        assert_plain_path(path.parent, checkout)
        if (not stat.S_ISREG(path.lstat().st_mode)
                or hashlib.sha256(path.read_bytes()).hexdigest() != manifest.get(name)):
            raise RuntimeError("Publication verifier changed; update the reviewed controller")


def equivalent_sources(root, current, current_sha, signer):
    """Authenticate H/D using pinned C and pristine R, never code from H."""
    tag = os.environ.get("PUBLICATION_EQUIVALENCE_TAG", "")
    if not re.fullmatch(r"publication-source-equivalence-[0-9a-f]{40}", tag):
        raise RuntimeError("Invalid publication source-equivalence receipt")
    identity = json.loads((CONTROLLER / "controller-source.json").read_text())
    controller_sha, engine_sha = identity.get("sha"), identity.get("engineSourceSha")
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value)
           for value in (current_sha, controller_sha, engine_sha)):
        raise RuntimeError("Source equivalence requires independently pinned controller and engine sources")
    controller = root / "controller-source"
    backend = root / "engine-source"
    for checkout, sha in ((controller, controller_sha), (backend, engine_sha)):
        git(["clone", "--quiet", "--no-hardlinks", str(current), str(checkout)], root)
        git(["checkout", "--quiet", "--detach", sha], checkout)
    manifest = json.loads((CONTROLLER / "gate-sha256.json").read_text())
    verify_gate_files(controller, manifest)
    verify_gate_files(backend, identity.get("engineGateSha256", {}))
    code = (
        "import importlib.util,json,pathlib,sys; "
        "controller=pathlib.Path(sys.argv[2]); "
        "spec=importlib.util.spec_from_file_location('frontend_gate',pathlib.Path(sys.argv[7])/'verify_frontend_publication.py'); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "print(json.dumps(module.verify_source_equivalence("
        "pathlib.Path(sys.argv[1]),expected_sha=sys.argv[4],receipt_tag=sys.argv[5],"
        "controller_root=controller,backend_root=pathlib.Path(sys.argv[3]),signer_fingerprint=sys.argv[6])))"
    )
    with tempfile.TemporaryDirectory(prefix=".admission-gates-", dir=root) as fresh:
        for name in ("verify_frontend_publication.py", "verify_catalog_publication.py"):
            (Path(fresh) / name).write_bytes((controller / "ops/release" / name).read_bytes())
        admission = json.loads(run(
            ["python", "-I", "-S", "-c", code, str(current), str(controller),
             str(backend), current_sha, tag, signer, fresh],
            controller, clean_env(), capture=True,
        ))
    subject = admission.get("sourceEquivalence", {})
    if (admission.get("schema") != "seiche.publication-source-admission.v1"
            or admission.get("currentSourceSha") != current_sha
            or subject.get("sourceSha") != tag.removeprefix("publication-source-equivalence-")
            or subject.get("controllerSourceSha") != controller_sha
            or subject.get("backendReleaseSha") != engine_sha):
        raise RuntimeError("Source-equivalence admission differs from the pinned source identities")
    if current_main() != current_sha:
        raise RuntimeError("Current main advanced during source-equivalence admission")
    return controller, backend, admission


def publication_identity(admission, current_sha, source_sha):
    if admission is None or admission.get("schema") != "seiche.publication-source-admission.v1":
        return {}
    subject = admission["sourceEquivalence"]
    return {
        "publicationSourceSha": current_sha,
        "controllerSourceSha": subject["controllerSourceSha"],
        "engineSourceSha": subject["backendReleaseSha"],
        "rendererSourceSha": subject["backendReleaseSha"],
        "sourceEquivalenceReceipt": "publication-source-equivalence-" + subject["sourceSha"],
        "sourceEquivalence": subject,
        "deskOverlaySha256": admission["deskOverlay"]["sha256"],
        "equivalentInputManifestSha256": admission["equivalentInputManifest"]["sha256"],
        "buildSourceSha": source_sha,
    }


def require_current_publication(current_sha, mirror=None, mirror_sha=None, env=None):
    if current_main() != current_sha:
        raise RuntimeError("Source main advanced during publication")
    if mirror is not None:
        observed = git(["ls-remote", "origin", "refs/heads/main"], mirror, env).split()
        if len(observed) != 2 or observed != [mirror_sha, "refs/heads/main"]:
            raise RuntimeError("Site mirror compare-and-swap lost")


def select_publication_source(trusted, current_sha, explicit_receipt):
    """Choose an exact receipt subject and separately check current desk ancestry."""
    current_tag = "frontend-publication-" + current_sha
    if explicit_receipt:
        if explicit_receipt != current_tag:
            raise RuntimeError(
                "Explicit frontend receipt must name current main exactly"
            )
        return current_sha, explicit_receipt, None
    if git(["tag", "--list", current_tag], trusted) == current_tag:
        return current_sha, current_tag, None
    ancestor = json.loads((CONTROLLER / "controller-source.json").read_text())["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", ancestor):
        raise RuntimeError("Invalid immutable controller source identity")
    if ancestor == current_sha:
        return current_sha, "", None  # Preserve the original full-release gate.
    ancestor_tag = "frontend-publication-" + ancestor
    if git(["tag", "--list", ancestor_tag], trusted) != ancestor_tag:
        # An application publisher may follow only the existing linear desk
        # lane. This is source selection, not release acceptance: the complete
        # signed application, runtime, package and corpus gates still run below.
        expected = {
            "schema": "seiche.application-desk-descendant.v1",
            "controllerSourceSha": ancestor,
            "currentSourceSha": current_sha,
            "purpose": "complete_application_publication",
        }
        code = (
            "import importlib.util,json,pathlib,sys; "
            "root=pathlib.Path(sys.argv[1]); "
            "spec=importlib.util.spec_from_file_location('catalog_gate',root/'ops/release/verify_catalog_publication.py'); "
            "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
            "module._verify_generated_content_descendants(root,release=sys.argv[2],head=sys.argv[3]); "
            "print(sys.argv[4])"
        )
        admission = json.loads(
            run(
                [
                    "python",
                    "-I",
                    "-S",
                    "-c",
                    code,
                    str(trusted),
                    ancestor,
                    current_sha,
                    json.dumps(expected, sort_keys=True),
                ],
                trusted,
                clean_env(),
                capture=True,
            )
        )
        if admission != expected:
            raise RuntimeError("Application desk admission differs from current source")
        return current_sha, "", admission
    # This verifier was checked against the controller manifest before selection.
    code = (
        "import importlib.util,json,pathlib,sys; "
        "root=pathlib.Path(sys.argv[1]); "
        "spec=importlib.util.spec_from_file_location('frontend_gate',root/'ops/release/verify_frontend_publication.py'); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "print(json.dumps(module.verify_desk_only_descendant(root,receipt_source_sha=sys.argv[2],current_source_sha=sys.argv[3])))"
    )
    admission = json.loads(
        run(
            ["python", "-I", "-S", "-c", code, str(trusted), ancestor, current_sha],
            trusted,
            clean_env(),
            capture=True,
        )
    )
    expected = {
        "schema": "seiche.frontend-desk-descendant.v1",
        "receiptSourceSha": ancestor,
        "currentSourceSha": current_sha,
        "purpose": "unchanged_frontend_only_no_desk_publication",
    }
    if admission != expected:
        raise RuntimeError(
            "Desk admission does not bind the exact current and receipt sources"
        )
    git(["checkout", "--quiet", "--detach", ancestor], trusted)
    # The unchanged workflow now verifies the exact ancestor tag and all original
    # live release gates. The descriptor above alone is not receipt authority.
    return ancestor, ancestor_tag, admission


def retain_source_admission(evidence, admission):
    if admission is not None and os.path.ismount(evidence):
        pending = evidence / "last-source-admission.json.tmp"
        pending.write_text(json.dumps(admission, sort_keys=True) + "\n")
        with pending.open("rb") as stream:
            os.fsync(stream.fileno())
        pending.replace(evidence / "last-source-admission.json")


def assert_plain_path(path, trusted_root):
    """Inspect each directory without following builder-controlled symlinks."""
    path, trusted_root = Path(path), Path(trusted_root)
    relative = path.relative_to(trusted_root)
    current = trusted_root
    for part in (None, *relative.parts):
        if part is not None:
            current = current / part
        if not stat.S_ISDIR(current.lstat().st_mode):
            raise RuntimeError("Publication directory is not a plain directory")


def copy_public_tree(source, target, trusted_root=None):
    assert_plain_path(source, trusted_root or Path(source.anchor))
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
    for name in (
        "Prove the canonical dataset landing and DCAT catalog are live",
        "Prove exact frontend shell, assets, catalog and sealed data bytes",
    ):
        step = steps[name]
        if step.get("if") and not receipt:
            continue
        command = (
            step["run"]
            .replace("/tmp/cloudflare-site", str(candidate))
            .replace("/tmp/site", str(candidate))
        )
        if command.startswith("python ops/"):
            command = command.replace("python ops/", "python -I -S ops/", 1)
        run(["bash", "-euo", "pipefail", "-c", command], trusted, env)


def read_source_health_package(path):
    """Capture one private regular file; it is never exposed to the builder."""
    path = Path(path)
    if not path.is_absolute():
        raise RuntimeError("Manual source health package requires an absolute path")
    assert_plain_path(path.parent, Path(path.anchor))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise RuntimeError("Manual source health package is not private owner data")
        raw = stream.read(MAX_SOURCE_HEALTH_PACKAGE_BYTES + 1)
    if not 0 < len(raw) <= MAX_SOURCE_HEALTH_PACKAGE_BYTES:
        raise RuntimeError("Manual source health package is missing or oversized")
    return raw.decode("utf-8")


def verify_source_health_helper(trusted):
    identity = json.loads((CONTROLLER / "controller-source.json").read_text())
    expected = identity.get("sourceHealthHelperSha256")
    path = trusted / SOURCE_HEALTH_HELPER
    assert_plain_path(path.parent, trusted)
    if (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)
            or not stat.S_ISREG(path.lstat().st_mode)
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
        raise RuntimeError("Manual source health helper differs from the reviewed controller")


def private_gate_temp(root):
    temp = root / "temp"
    temp.mkdir(mode=0o700)
    return temp


def capture_source_health_package(temp, value):
    """Keep the complete proof in a privileged file, never a process argument."""
    if not value:
        return {"SOURCE_HEALTH_PACKAGE": "", "SOURCE_HEALTH_PACKAGE_FILE": ""}
    raw = value.encode("utf-8")
    if not 0 < len(raw) <= MAX_SOURCE_HEALTH_PACKAGE_BYTES:
        raise RuntimeError("Manual source health package is missing or oversized")
    destination = temp / "catalog-source-health.json"
    with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o600), "wb") as stream:
        stream.write(raw)
    return {"SOURCE_HEALTH_PACKAGE": "", "SOURCE_HEALTH_PACKAGE_FILE": str(destination)}


def recheck_source_health(trusted, env):
    if not env.get("SOURCE_HEALTH_PACKAGE_FILE"):
        return None
    value = json.loads(run([
        "python", "-I", "-S", str(Path(env["RUNNER_TEMP"]) / "verify_catalog_publication.py"),
        "--root", str(trusted), "--expected-sha", env["GITHUB_SHA"],
        "--signer-fingerprint", env["RELEASE_SIGNING_KEY_FINGERPRINT"],
        "--source-health-package", str(Path(env["RUNNER_TEMP"]) / "catalog-source-health.json"),
        "--recheck-source-health-only",
    ], trusted, env, capture=True))
    if value.get("scope") != "source_health_recheck_only":
        raise RuntimeError("Source health recheck scope differs")
    return value["assessment"]


def claim_source_health(evidence, assessment):
    """Atomic durable intent binding for this original metadata publication."""
    intent = assessment["context"]["intent_id"]
    if not re.fullmatch(r"[0-9a-f]{64}", intent):
        raise RuntimeError("Source health publication intent is malformed")
    raw = (json.dumps(assessment["claim_document"], sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    parent = evidence / "source-health-claims"
    parent.mkdir(mode=0o700, exist_ok=True)
    assert_plain_path(parent, evidence)
    info = parent.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RuntimeError("Source health claim directory is not private owner data")
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    name = intent + ".json"
    try:
        try:
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
        except FileExistsError:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                        info.st_uid != os.geteuid() or info.st_mode & 0o077 or
                        stream.read(16385) != raw):
                    raise RuntimeError("Source health intent was claimed by another publication")
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        os.fsync(directory)
    finally:
        os.close(directory)
    descriptor = os.open(evidence, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def claim_current_source_health(evidence, assessment):
    claim_source_health(evidence, assessment)
    # fsync can stall. Recheck both signed expiry and the observed snapshot
    # deadline after durable I/O, immediately before the public write.
    now = datetime.now(timezone.utc)
    for field in ("expires_at", "health_valid_until"):
        try:
            deadline = datetime.fromisoformat(assessment[field].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("Source health deadline is invalid") from None
        if deadline.tzinfo is None or not now < deadline:
            raise RuntimeError("Source health admission or snapshot expired during durable claim")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-health-package", type=Path)
    args = parser.parse_args(argv)
    source_health_package = read_source_health_package(args.source_health_package) if args.source_health_package else ""
    if source_health_package and (os.environ.get("FRONTEND_RECEIPT_TAG") or os.environ.get("PUBLICATION_EQUIVALENCE_TAG")):
        raise RuntimeError("Manual source health publication requires the exact application source")
    current_source_sha = current_main()
    source_sha = current_source_sha
    expected = os.environ.get("PUBLICATION_SOURCE_SHA", current_source_sha)
    if expected != current_source_sha:
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
    workflow_path = CONTROLLER / "publish-static.yml"
    workflow = yaml.safe_load(workflow_path.read_text())
    steps = {
        step.get("name"): step for step in workflow["jobs"]["copy-static"]["steps"]
    }
    with tempfile.TemporaryDirectory(prefix="seiche-publication-") as directory:
        root = Path(directory)
        root.chmod(0o755)
        trusted = root / "trusted"
        git(["clone", "--quiet", SOURCE, str(trusted)], root)
        git(["checkout", "--quiet", "--detach", source_sha], trusted)
        source_admission = None
        if os.environ.get("PUBLICATION_EQUIVALENCE_TAG"):
            trusted, _backend, source_admission = equivalent_sources(
                root, trusted, current_source_sha, signer
            )
            source_sha = source_admission["sourceEquivalence"]["controllerSourceSha"]
            receipt = "frontend-publication-" + source_sha
            explicit_receipt = os.environ.get("FRONTEND_RECEIPT_TAG", "")
            if explicit_receipt and explicit_receipt != receipt:
                raise RuntimeError("Source equivalence requires the exact pinned controller frontend receipt")
        if (
            trusted / ".github/workflows/publish-static.yml"
        ).read_bytes() != workflow_path.read_bytes():
            raise RuntimeError(
                "Publication workflow changed; update the reviewed controller"
            )
        # Execute verification code only when its blob matches the controller's pinned manifest.
        manifest = json.loads((CONTROLLER / "gate-sha256.json").read_text())
        verify_gate_files(trusted, manifest)
        if source_admission is None:
            source_sha, receipt, source_admission = select_publication_source(
                trusted, current_source_sha, os.environ.get("FRONTEND_RECEIPT_TAG", "")
            )
        if source_health_package:
            if receipt or source_sha != current_source_sha:
                raise RuntimeError("Manual source health publication cannot select a frontend or ancestor receipt")
            verify_source_health_helper(trusted)
        identity = publication_identity(source_admission, current_source_sha, source_sha)
        if identity:
            identity["buildTreeKind"] = "signed_frontend_with_sealed_mirror_data"
            identity["deskOverlayApplied"] = False
        temp = private_gate_temp(root)
        env = clean_env(
            {
                "GITHUB_SHA": source_sha,
                "GITHUB_WORKSPACE": str(trusted),
                "RUNNER_TEMP": str(temp),
                "FRONTEND_RECEIPT_TAG": receipt,
                "RELEASE_SIGNING_KEY_FINGERPRINT": signer,
                "GITHUB_RUN_ATTEMPT": str(time.time_ns()),
                "GITHUB_EVENT_NAME": "native_manual" if source_health_package else "native_controller",
                **capture_source_health_package(temp, source_health_package),
            }
        )
        for name in (
            "Fetch the exact declared release tag",
            "Gate catalog on the signed release, runtime, and PyPI receipts",
        ):
            print("RAILWAY_STATIC_STEP " + name, flush=True)
            run(["bash", "-euo", "pipefail", "-c", steps[name]["run"]], trusted, env)
        if identity:
            proof = json.loads((temp / "frontend-publication-proof/source-and-runtime.json").read_text())
            identity["runtimeIdentity"] = {
                name: proof[name] for name in
                ("backendRuntimeSubject", "corpusRuntimeSubject", "runtimeEndpoints")
            }
            print("RAILWAY_STATIC_SOURCE_IDENTITY " + json.dumps(identity, sort_keys=True), flush=True)
        if source_admission is not None and not identity:
            print(
                "RAILWAY_STATIC_DESK_DESCENDANT_VERIFIED "
                + json.dumps(source_admission, sort_keys=True),
                flush=True,
            )
        if current_main() != current_source_sha:
            raise RuntimeError("Current main advanced during source admission")
        mirror = root / "mirror"
        git(["clone", "--quiet", MIRROR, str(mirror)], root)
        previous_sha = git(["rev-parse", "HEAD"], mirror)
        if (
            prior_state
            and prior_state.get("source") == source_sha
            and prior_state.get("site") == previous_sha
            and (not identity or prior_state.get("publicationSourceSha") == current_source_sha)
        ):
            recovery_name = prior_state.get("recovery", "")
            if "/" in recovery_name or not recovery_name.startswith(
                source_sha + "-seiche-publication-"
            ):
                raise RuntimeError("Invalid retained recovery identity")
            verify_publication(
                steps,
                trusted,
                mirror,
                clean_env({**env, "RUNNER_TEMP": str(evidence / recovery_name)}),
                receipt,
            )
            if current_main() != current_source_sha:
                raise RuntimeError(
                    "Current main advanced during unchanged public proof"
                )
            require_current_publication(current_source_sha, mirror, previous_sha)
            retain_source_admission(evidence, source_admission)
            print(
                f"RAILWAY_STATIC_UNCHANGED_VERIFIED source={source_sha} current_main={current_source_sha} site={previous_sha}",
                flush=True,
            )
            return
        if receipt:
            proof = run(
                [
                    "python",
                    "-I",
                    "-S",
                    str(trusted / "ops/release/frontend_site_proof.py"),
                    "snapshot",
                    "--site-root",
                    str(mirror),
                    "--archive",
                    str(root / "previous-site.tar"),
                ],
                trusted,
                clean_env(),
                capture=True,
            )
            (root / "previous-site.json").write_text(proof)
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
        if (temp / "frontend-publication-proof").exists():
            shutil.copytree(
                temp / "frontend-publication-proof",
                build_temp / "frontend-publication-proof",
            )
            for path in (build_temp / "frontend-publication-proof").rglob("*"):
                shutil.chown(path, user=10001, group=10001)
            shutil.chown(
                build_temp / "frontend-publication-proof", user=10001, group=10001
            )
        build_env = clean_env(
            {
                "GITHUB_SHA": source_sha,
                "GITHUB_WORKSPACE": str(build),
                "RUNNER_TEMP": str(build_temp),
                "FRONTEND_RECEIPT_TAG": receipt,
                "XDG_CACHE_HOME": str(build_temp / "cache"),
            }
        )
        print("RAILWAY_STATIC_STEP test and build frontend", flush=True)
        run(
            [
                "bash",
                "-euo",
                "pipefail",
                "-c",
                steps["Test and build exact-head frontend"]["run"],
            ],
            build / "frontend",
            build_env,
            unprivileged=True,
        )
        prepare = steps["Prepare static files from the recoverable site mirror"]["run"]
        start = prepare.index("mkdir -p ~/.ssh")
        end = prepare.index('if [ -n "$FRONTEND_RECEIPT_TAG" ]; then')
        prepared = build_temp / "site"
        git(["clone", "--quiet", "--no-hardlinks", str(mirror), str(prepared)], root)
        shutil.chown(prepared, user=10001, group=10001)
        for path in prepared.rglob("*"):
            if not path.is_symlink():
                shutil.chown(path, user=10001, group=10001)
        prepare = prepare[:start] + prepare[end:]
        prepare = prepare.replace("/tmp/site", str(prepared))
        print("RAILWAY_STATIC_STEP prepare sealed mirror", flush=True)
        run(
            ["bash", "-euo", "pipefail", "-c", prepare],
            build,
            build_env,
            unprivileged=True,
        )
        quiesce_builder()
        candidate = root / "candidate"
        candidate.mkdir()
        copy_public_tree(prepared, candidate, root)
        if receipt:
            proof = run(
                [
                    "python",
                    "-I",
                    "-S",
                    str(trusted / "ops/release/frontend_site_proof.py"),
                    "seal",
                    "--site-root",
                    str(candidate),
                    "--source-sha",
                    source_sha,
                    "--source-proof",
                    str(temp / "frontend-publication-proof/source-and-runtime.json"),
                    "--manifest",
                    str(root / "previous-site.json"),
                ],
                trusted,
                clean_env(),
                capture=True,
            )
            # Use the independent privileged seal for public proof, never the builder's claim.
            proof_directory = temp / "frontend-publication-proof"
            assert_plain_path(proof_directory, root)
            (proof_directory / "prepared-site.json").write_text(proof)
        if current_main() != current_source_sha:
            raise RuntimeError("Source main advanced during preparation")
        require_current_publication(current_source_sha, mirror, previous_sha)
        source_health_assessment = recheck_source_health(trusted, env)
        print(
            f"RAILWAY_STATIC_PREPARE_PASS source={source_sha} current_main={current_source_sha} previous_site={previous_sha} receipt={receipt or 'application'}",
            flush=True,
        )
        if not apply:
            return
        if not os.path.ismount(evidence):
            raise RuntimeError("Durable recovery volume is required before publication")
        recovery = evidence / (source_sha + "-" + root.name)
        recovery.mkdir(mode=0o700, parents=True, exist_ok=False)
        if source_health_assessment is not None:
            (recovery / "source-health-package.json").write_text(source_health_package)
            (recovery / "source-health-package.json").chmod(0o600)
            (recovery / "source-health-assessment.json").write_text(json.dumps(source_health_assessment, sort_keys=True) + "\n")
        if (
            git(["ls-remote", "origin", "refs/heads/main"], mirror).split()[0]
            != previous_sha
        ):
            raise RuntimeError("Site mirror advanced during preparation")
        with tarfile.open(recovery / "previous-site.tar", "w") as archive:
            for path in mirror.iterdir():
                if path.name != ".git":
                    archive.add(path, arcname=path.name)
        if receipt:
            # Retain only the signed gate's controller-owned proof and independent seal.
            # Builder cache paths are never traversed or read with publishing privileges.
            assert_plain_path(temp / "frontend-publication-proof", root)
            shutil.copytree(
                temp / "frontend-publication-proof",
                recovery / "frontend-publication-proof",
            )
        (recovery / "identity.json").write_text(
            json.dumps(
                {
                    "source": source_sha,
                    "current_main": current_source_sha,
                    "source_admission": source_admission,
                    "previous_site": previous_sha,
                    **identity,
                }
            )
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
                ["commit", "-m", "Verified Railway static publication " + source_sha],
                mirror,
            )
            if current_main() != current_source_sha:
                raise RuntimeError("Source main advanced before mirror publication")
            require_current_publication(current_source_sha, mirror, previous_sha, publish_env)
            current_health = recheck_source_health(trusted, env)
            if current_health is not None:
                claim_current_source_health(evidence, current_health)
            git(["push", "origin", "HEAD:main"], mirror, publish_env)
        site_sha = git(["rev-parse", "HEAD"], mirror)
        if (
            git(
                ["ls-remote", "origin", "refs/heads/main"], mirror, publish_env
            ).split()[0]
            != site_sha
        ):
            raise RuntimeError("Site mirror compare-and-swap lost")
        if current_main() != current_source_sha:
            raise RuntimeError("Source main advanced before canonical publication")
        require_current_publication(current_source_sha, mirror, site_sha, publish_env)
        current_health = recheck_source_health(trusted, env)
        if current_health is not None:
            claim_current_source_health(evidence, current_health)
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
                current_source_sha if identity else source_sha,
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
            clean_env({**env, "RUNNER_TEMP": str(temp)}),
            receipt,
        )
        require_current_publication(current_source_sha, mirror, site_sha, publish_env)
        state = evidence / "current.json.tmp"
        state.write_text(
            json.dumps(
                {
                    "source": source_sha,
                    "current_main": current_source_sha,
                    "source_admission": source_admission,
                    "site": site_sha,
                    "recovery": recovery.name,
                    "verified_at": time.time(),
                    **identity,
                }
            )
        )
        with state.open("rb") as stream:
            os.fsync(stream.fileno())
        state.replace(evidence / "current.json")
        retain_source_admission(evidence, source_admission)
        print(
            f"RAILWAY_STATIC_PUBLISH_PASS source={source_sha} current_main={current_source_sha} site={site_sha}",
            flush=True,
        )


if __name__ == "__main__":
    main()
