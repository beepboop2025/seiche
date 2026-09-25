"""Reviewed Railway controller for Seiche's full engine publication path."""

import hashlib
import importlib.util
import io
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
from contextlib import contextmanager

import yaml

CONTROLLER = Path(__file__).resolve().parent
SOURCE = "https://github.com/beepboop2025/seiche.git"
MIRROR = "https://github.com/beepboop2025/seiche-site.git"
GATES = (
    "ops/release/verify_catalog_publication.py",
    "ops/release/verify_frontend_publication.py",
    "ops/release/frontend_site_proof.py",
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


def builder_home(path=Path("/home/builder")):
    """Keep release-gate runtime directories below a private, trusted ancestry."""
    if not path.is_absolute():
        raise RuntimeError("Builder home must be absolute")
    for parent in (path, *path.parents):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid not in {0, 10001}
                or (info.st_uid == 10001 and info.st_gid != 10001)
                or stat.S_IMODE(info.st_mode) & 0o022):
            raise RuntimeError("Builder home has unsafe ancestry")
    info = path.lstat()
    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (10001, 10001, 0o700):
        raise RuntimeError("Builder home must be private to the builder")
    return path


def build_step_env(name, environment, history):
    """Match the workflow's step-local history input; tests get no durable state."""
    selected = {key: value for key, value in environment.items()
                if key != "GDELT_WEB_HISTORY_FILE"}
    if name in {"Seed GDELT WEB-NGRAM baseline when cache is cold",
                "Run engines, export snapshot"}:
        selected["GDELT_WEB_HISTORY_FILE"] = str(history)
    return selected


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


def seal_candidate(prepared, trusted):
    """Keep sealed output inside the unchanged release verifier's root boundary."""
    assert_plain_path(trusted, Path(trusted.anchor))
    candidate = Path(tempfile.mkdtemp(prefix=".railway-publication-", dir=trusted))
    copy_public_tree(prepared, candidate)
    return candidate


@contextmanager
def original_frontend_proof(backend):
    """Load only the independently pinned engine's proof code, without bytecode."""
    identity = json.loads((CONTROLLER / "controller-source.json").read_text())
    name = "ops/release/frontend_site_proof.py"
    body = (backend / name).read_bytes()
    if hashlib.sha256(body).hexdigest() != identity["engineGateSha256"].get(name):
        raise RuntimeError("Original frontend proof changed")
    with tempfile.TemporaryDirectory(prefix=".frontend-proof-", dir=backend.parent) as raw:
        path = Path(raw) / "proof.py"
        path.write_bytes(body)
        spec = importlib.util.spec_from_file_location("released_frontend_proof", path)
        module = importlib.util.module_from_spec(spec)
        # Compile the bytes we just authenticated; no import cache can replace them.
        exec(compile(body, str(path), "exec"), module.__dict__)
        yield module


def seal_frontend_overlay(before, output, proof, *, source_sha, retired, editorial):
    """Keep every generated observation while admitting the signed UI exceptions."""
    if retired not in ([], ["funding.json"]) or editorial not in (None, proof.EDITORIAL_ORIGIN):
        raise RuntimeError("Unsupported frontend publication exception")
    after = proof.files(output)
    policy = {}
    if editorial:
        header, value = proof.editorial_policy(output)
        original = header.replace(proof.EDITORIAL_CONNECT_AFTER.encode(),
                                  proof.EDITORIAL_CONNECT_BEFORE.encode())
        if before.get("_headers") not in {hashlib.sha256(original).hexdigest(), after["_headers"]}:
            raise RuntimeError("Frontend changed another engine header")
        policy = {"contentSecurityPolicy": value, "headersSha256": after["_headers"]}
    if any(path in after for path in retired):
        raise RuntimeError("Retired public file was reintroduced")
    changed = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        allowed = path == "index.html" or (path == "_headers" and policy)
        allowed = allowed or (path in retired and new is None)
        card = proof.ROOT_CARD.fullmatch(path)
        allowed = allowed or (old is None and new is not None and
            (proof.ASSET.fullmatch(path) or (card and new.startswith(card.group(1)))))
        if not allowed:
            raise RuntimeError("Frontend changed generated engine evidence: " + path)
        changed.append(path)
    refs = re.findall(r'(?:src|href)=["\']\./(assets/[^"\']+)["\']',
                      (output / "index.html").read_text())
    if not refs or any(not proof.ASSET.fullmatch(path) or path not in after for path in refs):
        raise RuntimeError("Frontend references a missing or invalid asset")
    public = {"index.html", "data/overview.json", ".well-known/ai-catalog.json", *refs,
              *(path for path in changed if path not in retired and path != "_headers")}
    return {"schema": proof.SCHEMA, "artifactKind": "generated_engine_with_signed_frontend",
            "sourceSha": source_sha,
            "engineManifestSha256": hashlib.sha256(json.dumps(before, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest(),
            "changed": changed, "publicFiles": {path: after[path] for path in sorted(public)},
            "absentPublicFiles": retired, **policy}


def extract_frontend_archive(raw, destination):
    """Validate the complete archive before writing any signed frontend files."""
    if not raw or len(raw) > 128 * 1024 * 1024:
        raise RuntimeError("Signed frontend archive exceeds its byte bound")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        members, total, seen = archive.getmembers(), 0, set()
        for entry in members:
            path = Path(entry.name)
            total += entry.size
            if (path.is_absolute() or ".." in path.parts or not path.parts or
                    path.parts[0] != "frontend" or ".git" in path.parts or
                    path.as_posix() != entry.name.rstrip("/") or path.as_posix() in seen or
                    not (entry.isfile() or entry.isdir()) or entry.linkname or
                    entry.size > 16 * 1024 * 1024 or total > 128 * 1024 * 1024 or
                    len(members) > 20000):
                raise RuntimeError("Unsafe signed frontend archive member")
            seen.add(path.as_posix())
        for entry in members:
            target = destination / entry.name
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    shutil.copyfileobj(archive.extractfile(entry), output)


def build_signed_frontend(root, controller, backend, engine_candidate, admission, signer):
    """Build exact signed C in isolation, then overlay it on newly sealed R data."""
    subject = admission["sourceEquivalence"]
    source_sha, engine_sha = subject["controllerSourceSha"], subject["backendReleaseSha"]
    source_proof = json.loads(run([
        "python", "-I", "-S", str(backend / "ops/release/verify_frontend_publication.py"),
        "--root", str(controller), "--expected-sha", source_sha,
        "--signer-fingerprint", signer, "--receipt-tag", "frontend-publication-" + source_sha,
    ], backend, clean_env(), capture=True))
    publication = source_proof.get("frontendPublication", {})
    if publication.get("sourceSha") != source_sha or publication.get("backendReleaseSha") != engine_sha:
        raise RuntimeError("Frontend receipt does not bind the generated engine")
    source_path = root / "signed-frontend-source.json"
    source_path.write_text(json.dumps(source_proof, sort_keys=True) + "\n")
    archive_path = root / "signed-frontend-source.tar"
    with archive_path.open("xb") as archive:
        subprocess.run(["git", "archive", "--format=tar", source_sha, "frontend"],
                       cwd=controller, env=clean_env(), stdout=archive, timeout=90, check=True)
    if archive_path.stat().st_size > 128 * 1024 * 1024:
        raise RuntimeError("Signed frontend archive exceeds its byte bound")
    archive_bytes = archive_path.read_bytes()
    # A second pristine archive prevents tests from altering the published build.
    for stage, commands in (("tests", ("ci", "test")), ("build", ("ci", "run build"))):
        directory = root / ("signed-frontend-" + stage)
        directory.mkdir()
        extract_frontend_archive(archive_bytes, directory)
        for path in (directory, *directory.rglob("*")):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise RuntimeError("Unsafe signed frontend archive")
            shutil.chown(path, user=10001, group=10001)
        home = root / ("signed-frontend-home-" + stage)
        home.mkdir(mode=0o700)
        shutil.chown(home, user=10001, group=10001)
        environment = clean_env({"HOME": str(home), "XDG_CACHE_HOME": str(home / ".cache")})
        for command in commands:
            run(["npm", *command.split()], directory / "frontend", environment, unprivileged=True)
        # Test-created npm configuration and caches cannot enter the build stage.
        for path in (home, directory):
            shutil.chown(path, user=0, group=0)
            path.chmod(0o700)
    quiesce_builder()
    sealed_ui = root / "signed-frontend-output"
    sealed_ui.mkdir()
    copy_public_tree(root / "signed-frontend-build/frontend/dist", sealed_ui)
    output = seal_candidate(engine_candidate, backend)
    with original_frontend_proof(backend) as proof:
        before = proof.files(engine_candidate)
        # Public files from the source archive are never copied over engine data.
        (output / "index.html").write_bytes((sealed_ui / "index.html").read_bytes())
        for path in (sealed_ui / "assets").rglob("*"):
            if path.is_dir():
                continue
            relative = path.relative_to(sealed_ui).as_posix()
            if not proof.ASSET.fullmatch(relative):
                raise RuntimeError("Unexpected signed frontend asset")
            target = output / relative
            if target.exists() and target.read_bytes() != path.read_bytes():
                raise RuntimeError("Frontend asset collides with retained engine output")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        code = ("import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                "from seiche import prerender,social_cards; "
                "size=prerender.build(Path(sys.argv[2])); "
                "assert size>=1000, 'incomplete frontend prerender'; "
                "print(json.dumps(social_cards.refresh_root(Path(sys.argv[2])),sort_keys=True))")
        run(["python", "-I", "-c", code, str(backend / "backend"), str(output)],
            backend, clean_env())
        retired = proof.declared_retirements(source_path, source_sha)
        editorial = proof.declared_editorial_origin(source_path, source_sha)
        proof.retire(output, retired)
        proof.allow_editorial(output, editorial)
        manifest = seal_frontend_overlay(before, output, proof, source_sha=source_sha,
                                         retired=retired, editorial=editorial)
        manifest["frontendSourceArchiveSha256"] = hashlib.sha256(archive_bytes).hexdigest()
    (root / "signed-frontend-output.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    print("RAILWAY_FULL_FRONTEND_PASS " + json.dumps({"frontendSourceSha": source_sha,
        "engineSourceSha": engine_sha, "engineManifestSha256": manifest["engineManifestSha256"]}), flush=True)
    return output, manifest


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


DESK_PATH = re.compile(
    r"(?:frontend/public/(?:dispatches|articles)/[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:md|json)"
    r"|backend/seiche/dispatches/(?:[A-Za-z0-9][A-Za-z0-9_.-]*\.desk\.md"
    r"|state\.json|weekly_state\.json|odds_ledger\.jsonl))"
)


def desk_tree(root, sha):
    entries = []
    for line in git(["ls-tree", "-r", "--full-tree", sha], root).splitlines():
        metadata, path = line.split("\t", 1)
        if DESK_PATH.fullmatch(path) is None:
            continue
        mode, kind, oid = metadata.split()
        if mode != "100644" or kind != "blob" or not re.fullmatch(r"[0-9a-f]{40}", oid):
            raise RuntimeError("Desk overlay is not nonexecutable regular data")
        entries.append({"path": path, "mode": mode, "object": oid})
    return entries


def apply_desk_projection(current, backend, build, admission):
    """Materialize only admitted Git blobs before any builder process starts."""
    subject = admission["sourceEquivalence"]
    current_sha, engine_sha = admission["currentSourceSha"], subject["backendReleaseSha"]
    if git(["rev-parse", "HEAD"], build) != engine_sha:
        raise RuntimeError("Projection base is not the signed engine source")
    if git(["status", "--porcelain", "--untracked-files=all"], build):
        raise RuntimeError("Projection base is not pristine")
    entries = desk_tree(current, current_sha)
    previous = desk_tree(backend, engine_sha)
    paths = {entry["path"] for entry in entries}
    deletions = sorted({entry["path"] for entry in previous} - paths)
    payload = {"entries": entries, "deletePaths": deletions}
    digest = hashlib.sha256((json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ) + "\n").encode()).hexdigest()
    if admission.get("deskOverlay") != {**payload, "sha256": digest}:
        raise RuntimeError("Desk overlay differs from the signed admission and current Git objects")
    if len(entries) > 20000:
        raise RuntimeError("Desk overlay exceeds its file bound")
    # Bound and read every object before mutating even the disposable projection.
    blobs, total = [], 0
    for entry in entries:
        size = int(git(["cat-file", "-s", entry["object"]], current))
        total += size
        if size > 8 * 1024 * 1024 or total > 128 * 1024 * 1024:
            raise RuntimeError("Desk overlay exceeds its byte bound")
        data = subprocess.run(
            ["git", "cat-file", "blob", entry["object"]], cwd=current,
            env=clean_env(), capture_output=True, check=True, timeout=60,
        ).stdout
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if len(data) != size or actual != entry["object"]:
            raise RuntimeError("Desk overlay object changed while reading")
        blobs.append((entry["path"], data))
    for relative in [*deletions, *(path for path, _data in blobs)]:
        destination = build / relative
        ancestor = destination.parent
        while not ancestor.exists() and not ancestor.is_symlink():
            ancestor = ancestor.parent
        assert_plain_path(ancestor, build)
        if destination.exists() or destination.is_symlink():
            info = destination.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o111:
                raise RuntimeError("Projection destination is not nonexecutable regular data")
    for relative in deletions:
        (build / relative).unlink()
    for relative, data in blobs:
        destination = build / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        assert_plain_path(destination.parent, build)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        destination.chmod(0o644)
    return {"schema": "seiche.engine-desk-projection.v1", "engineSourceSha": engine_sha,
            "publicationSourceSha": current_sha, "deskOverlaySha256": digest,
            "fileCount": len(entries), "byteCount": total, "deletedCount": len(deletions)}


def verify_runtime_identity(backend, engine_sha):
    """Run the original release's exact runtime-subject checks, separately from H."""
    code = (
        "import importlib.util,json,pathlib,sys; root=pathlib.Path(sys.argv[1]); "
        "spec=importlib.util.spec_from_file_location('released_frontend_gate',pathlib.Path(sys.argv[3])/'verify_frontend_publication.py'); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "gate=module.gate; version,_=gate.verify_local_identity(root); "
        "entry=gate._market_corpus_catalog_entry(gate._read_json(root/gate.AI_CATALOG_PATH)); "
        "corpus=gate._market_corpus_publication_receipt(entry); "
        "observed=module.RuntimeReceipts(sys.argv[2],version=version,corpus_release_id=corpus['releaseId']); "
        "runtime=gate.verify_public_receipts(version,fetch_json=observed.fetch_json); "
        "corpus_runtime=gate.verify_market_corpus_receipts(entry,fetch_json=observed.fetch_json,post_json=observed.post_json); "
        "print(json.dumps({'backendRuntimeSubject':observed.subject,'corpusRuntimeSubject':observed.corpus_subject,"
        "'runtimeEndpoints':observed.endpoints,'backendPublicReceipts':runtime,'corpusPublicReceipts':corpus_runtime}))"
    )
    with tempfile.TemporaryDirectory(prefix=".runtime-gates-", dir=backend.parent) as fresh:
        for name in ("verify_frontend_publication.py", "verify_catalog_publication.py"):
            (Path(fresh) / name).write_bytes((backend / "ops/release" / name).read_bytes())
        return json.loads(run(
            ["python", "-I", "-S", "-c", code, str(backend), engine_sha, fresh],
            backend, clean_env(), capture=True,
        ))


def main():
    apply = os.environ.get("PUBLISH_APPLY") == "1"
    if apply:
        missing = [name for name in ("SITE_DEPLOY_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")
                   if not os.environ.get(name, "").strip()]
        if missing:
            raise RuntimeError("Publication credentials missing: " + ", ".join(missing))
    source_sha = current_main()
    expected = os.environ.get("PUBLICATION_SOURCE_SHA", source_sha)
    if expected != source_sha:
        raise RuntimeError("Requested source is no longer current main")
    print(f"RAILWAY_FULL_START source={source_sha} deployment={os.environ.get('RAILWAY_DEPLOYMENT_ID', 'local')}", flush=True)
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
        current = trusted
        source_admission = None
        engine_sha = source_sha
        controller_checkout = trusted
        if os.environ.get("PUBLICATION_EQUIVALENCE_TAG"):
            controller_checkout, trusted, source_admission = equivalent_sources(
                root, current, source_sha, signer
            )
            engine_sha = source_admission["sourceEquivalence"]["backendReleaseSha"]
        if (
            trusted / ".github/workflows/publish.yml"
        ).read_bytes() != workflow_path.read_bytes():
            raise RuntimeError(
                "Publication workflow changed; update the reviewed controller"
            )
        # The opt-in path pinned both C and R before its first verifier import.
        if source_admission is None:
            verify_gate_files(trusted, json.loads((CONTROLLER / "gate-sha256.json").read_text()))
        elif (controller_checkout / ".github/workflows/publish.yml").read_bytes() != workflow_path.read_bytes():
            raise RuntimeError("Controller and signed engine workflows differ")
        identity = publication_identity(source_admission, source_sha, engine_sha)
        if identity:
            identity["buildTreeKind"] = "signed_engine_with_validated_desk_overlay"
            identity["deskOverlayApplied"] = True
        receipt = ""  # Full engine output requires the original backend release gate.
        temp = root / "temp"
        temp.mkdir()
        env = clean_env(
            {
                "GITHUB_SHA": engine_sha,
                "PUBLICATION_SOURCE_SHA": engine_sha,
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
        if identity:
            identity["runtimeIdentity"] = verify_runtime_identity(trusted, engine_sha)
        require_current_publication(source_sha)
        mirror = root / "mirror"
        git(["clone", "--quiet", MIRROR, str(mirror)], root)
        previous_sha = git(["rev-parse", "HEAD"], mirror)
        digest = hashlib.sha256()
        for name in ("publish.py", "Dockerfile", "publish.yml", "gate-sha256.json",
                     "requirements-social-cards.txt", "controller-source.json",
                     "github-known-hosts", "test_publish.py"):
            digest.update(name.encode() + b"\0" + (CONTROLLER / name).read_bytes())
        controller_digest = digest.hexdigest()
        reusable = bool(prior_state and prior_state.get("source") == source_sha
                        and prior_state.get("controller_digest") == controller_digest)
        build = root / "build"
        git(["clone", "--quiet", "--no-hardlinks", str(trusted), str(build)], root)
        if source_admission is not None:
            identity["projection"] = apply_desk_projection(current, trusted, build, source_admission)
            print("RAILWAY_FULL_SOURCE_IDENTITY " + json.dumps(identity, sort_keys=True), flush=True)
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
                "GITHUB_SHA": engine_sha,
                "PUBLICATION_SOURCE_SHA": source_sha,
                "GITHUB_WORKSPACE": str(build),
                "RUNNER_TEMP": str(build_temp),
                "FRONTEND_RECEIPT_TAG": receipt,
                "XDG_CACHE_HOME": str(build_temp / "cache"),
                "HOME": str(builder_home()),
            }
        )
        history = evidence / "gdelt-web-history.json"
        if history.is_file():
            copy_history(history, build / ".cache/gdelt-web-history.json", builder=True)
        run(["python", "-m", "venv", str(build_temp / "venv")], build,
            build_env, unprivileged=True)
        build_env.update({
            "PATH": str(build_temp / "venv/bin") + ":" + build_env["PATH"],
            "PUBLICATION_SOURCE_SHA": source_sha,
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
                build, build_step_env(name, build_env, build / ".cache/gdelt-web-history.json"),
                unprivileged=True)
        prepared = build / "frontend/dist"
        quiesce_builder()
        candidate = seal_candidate(prepared, trusted)
        frontend_manifest = None
        if source_admission is not None and source_admission["sourceEquivalence"]["schema"] == "seiche.publication-source-equivalence.v2":
            # UI tests must not alter the engine's pending history cache either.
            for path in (build, build_temp):
                shutil.chown(path, user=0, group=0)
                path.chmod(0o700)
            candidate, frontend_manifest = build_signed_frontend(
                root, controller_checkout, trusted, candidate, source_admission, signer)
            identity["frontendSourceSha"] = source_admission["sourceEquivalence"]["controllerSourceSha"]
        run(["python", "-I", "-S", str(trusted / GATES[0]), "--root", str(trusted),
             "--expected-sha", engine_sha, "--signer-fingerprint", signer,
             "--published-catalog", str(candidate / ".well-known/ai-catalog.json")],
            trusted, clean_env())
        if identity:
            identity["runtimeIdentity"] = verify_runtime_identity(trusted, engine_sha)
        if current_main() != source_sha:
            raise RuntimeError("Source main advanced during preparation")
        require_current_publication(source_sha, mirror, previous_sha)
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
            json.dumps({"source": source_sha, "previous_site": previous_sha, **identity})
        )
        if source_admission is not None:
            (recovery / "source-admission.json").write_text(json.dumps(source_admission, sort_keys=True) + "\n")
        if frontend_manifest is not None:
            for name in ("signed-frontend-source.json", "signed-frontend-output.json"):
                shutil.copyfile(root / name, recovery / name)
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
            require_current_publication(source_sha, mirror, previous_sha, publish_env)
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
        require_current_publication(source_sha, mirror, site_sha, publish_env)
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
            clean_env({**env, "RUNNER_TEMP": str(build_temp), "PUBLICATION_SOURCE_SHA": source_sha}),
            receipt,
        )
        if frontend_manifest is not None:
            with original_frontend_proof(trusted) as proof:
                proof.verify_public(candidate, frontend_manifest,
                                    cache_key=source_sha + "-" + env["GITHUB_RUN_ATTEMPT"])
        require_current_publication(source_sha, mirror, site_sha, publish_env)
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
                    **identity,
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
