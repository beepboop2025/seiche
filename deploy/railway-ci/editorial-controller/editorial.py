"""Prepare an exact-source Seiche edition without publication credentials."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile

from isolation import WRITER_UID, drop_privileges, git, read_regular, stop_collectors, validate_json


ROOT = Path(__file__).resolve().parent
REPOSITORY = "https://github.com/beepboop2025/seiche.git"
CONTENT_DIRS = ("frontend/public/dispatches", "frontend/public/articles", "backend/seiche/dispatches")
MAX_TREE_BYTES = 128 * 1024 * 1024


def digest(body):
    return hashlib.sha256(body).hexdigest()


def event(name, **fields):
    print(json.dumps({"event": name, **fields}, sort_keys=True), flush=True)


def clean_env(home):
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"}


def admitted_input(path):
    return (path.startswith("backend/") and not path.startswith("backend/seiche/dispatches/")) or path in {
        ".github/workflows/dispatch-daily.yml", ".github/workflows/dispatch-weekly.yml"}


def checkout(mirror, env, source, destination, policy):
    baseline = {}
    inputs = {}
    total = 0
    for entry in git(mirror, env, "ls-tree", "-rz", "--full-tree", source).split(b"\0"):
        if not entry:
            continue
        header, raw_path = entry.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        name = raw_path.decode("utf-8")
        path = Path(name)
        if path.is_absolute() or any(part in {"..", ".git"} for part in path.parts):
            raise ValueError("unsafe source path")
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ValueError("nonregular source object: " + name)
        content = git(mirror, env, "cat-file", "blob", blob)
        total += len(content)
        if total > MAX_TREE_BYTES:
            raise ValueError("source tree exceeds reviewed limit")
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o555 if mode == "100755" else 0o444)
        baseline[name] = digest(content)
        if admitted_input(name):
            inputs[name] = baseline[name]
    if inputs != policy["inputs"]:
        raise ValueError("reviewed editorial source changed; image rebuild required")
    return baseline


def output_allowed(name, lane, date):
    dispatch = rf"frontend/public/dispatches/{date}-{'daily' if lane == 'daily' else 'week-ahead'}\.(?:md|json)"
    desk = rf"backend/seiche/dispatches/{date}-{'daily' if lane == 'daily' else 'week-ahead'}\.desk\.md"
    fixed = {"frontend/public/dispatches/index.json"}
    fixed.add("backend/seiche/dispatches/" + ("state.json" if lane == "daily" else "weekly_state.json"))
    if lane == "daily":
        fixed |= {"backend/seiche/dispatches/odds_ledger.jsonl", "frontend/public/articles/index.json",
                  "frontend/public/articles/learning.json"}
        if re.fullmatch(rf"frontend/public/articles/{date}-[a-z0-9]+(?:-[a-z0-9]+)*\.(?:md|json)", name):
            return True
    return name in fixed or re.fullmatch(dispatch, name) is not None or re.fullmatch(desk, name) is not None


def allow_outputs(work, baseline, lane, date):
    for directory in CONTENT_DIRS:
        path = work / directory
        if not path.is_dir() or path.is_symlink():
            raise ValueError("missing fixed content directory")
        path.chmod(0o1777)
    for name in baseline:
        if output_allowed(name, lane, date):
            path = work / name
            # No force mode: an existing dated article/letter stays root-owned.
            if date in Path(name).name:
                continue
            os.chown(path, WRITER_UID, WRITER_UID)
            path.chmod(0o644)


def seal_outputs(work, baseline, lane, date, *, allow_content):
    for directory in CONTENT_DIRS:
        (work / directory).chmod(0o755)
    seen = set()
    changed = {}
    for path in work.rglob("*"):
        name = path.relative_to(work).as_posix()
        if path.is_dir() and not path.is_symlink():
            if not any(item.startswith(name + "/") for item in baseline):
                raise ValueError("unapproved generated directory: " + name)
            continue
        content = read_regular(path)
        seen.add(name)
        if digest(content) != baseline.get(name):
            if not allow_content or not output_allowed(name, lane, date):
                raise ValueError("unapproved modified path: " + name)
            if name in baseline and date in Path(name).name:
                raise ValueError("existing edition cannot be rewritten")
            if name.endswith((".json", ".jsonl")):
                validate_json(name, content)
            else:
                content.decode("utf-8")
                if not content.strip() or b"\0" in content:
                    raise ValueError("invalid editorial text")
            changed[name] = content
        os.chown(path, 0, 0)
        path.chmod(0o444)
    if not set(baseline).issubset(seen):
        raise ValueError("source or archived edition was removed")
    if sum(len(body) for body in changed.values()) > 16 * 1024 * 1024:
        raise ValueError("edition exceeds publication byte limit")
    return changed


def preserve_archive(work, mirror, env, parent, lane, date):
    indexes = ["frontend/public/dispatches/index.json"]
    if lane == "daily":
        indexes.append("frontend/public/articles/index.json")
    for name in indexes:
        old = json.loads(git(mirror, env, "show", f"{parent}:{name}"))
        current = json.loads(read_regular(work / name))
        if not isinstance(old, list) or not isinstance(current, list):
            raise ValueError("invalid edition index")
        old_by_slug = {row["slug"]: row for row in old}
        now_by_slug = {row["slug"]: row for row in current}
        if len(now_by_slug) != len(current) or len(old_by_slug) != len(old):
            raise ValueError("duplicate edition slug")
        if any(now_by_slug.get(slug) != row for slug, row in old_by_slug.items()):
            raise ValueError("previously published index entry changed")
        additions = [row for slug, row in now_by_slug.items() if slug not in old_by_slug]
        if len(additions) > 1 or any(row.get("date") != date for row in additions):
            raise ValueError("new edition has wrong date or cardinality")
    if lane == "daily":
        name = "backend/seiche/dispatches/odds_ledger.jsonl"
        old = [json.loads(row) for row in git(mirror, env, "show", f"{parent}:{name}").splitlines() if row.strip()]
        current = [json.loads(row) for row in read_regular(work / name).splitlines() if row.strip()]
        preserve_forecasts(old, current, date)


def preserve_forecasts(old, current, date):
    if len(current) < len(old):
        raise ValueError("published forecast was removed")
    for before, after in zip(old, current):
        if ({key: value for key, value in before.items() if key != "realized"} !=
                {key: value for key, value in after.items() if key != "realized"}):
            raise ValueError("published forecast probability or identity changed")
        if before.get("realized") != after.get("realized"):
            if before.get("realized") is not None or type(after.get("realized")) is not bool:
                raise ValueError("published forecast resolution was rewritten")
    if any(row.get("date") != date for row in current[len(old):]):
        raise ValueError("new forecast has an unapproved date")


def run_stage(work, args, scratch, runtime, *, writer=None, deadline=2400):
    scratch.mkdir(mode=0o700)
    os.chown(scratch, WRITER_UID, WRITER_UID)
    env = clean_env(scratch)
    env.update({"TMPDIR": str(scratch), "XDG_CACHE_HOME": str(scratch / "cache"),
                "PYTHONPATH": str(work / "backend"), "PYTHONUNBUFFERED": "1",
                "SEICHE_RUNTIME_DATA_DIR": str(runtime), "OPENBLAS_NUM_THREADS": "2",
                "OMP_NUM_THREADS": "2"})
    if writer:
        env.update(writer)
    process = subprocess.Popen([sys.executable, "-u", *args], cwd=work, env=env,
                               stdin=subprocess.DEVNULL, close_fds=True,
                               preexec_fn=drop_privileges, start_new_session=True)
    try:
        try:
            code = process.wait(timeout=deadline)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise RuntimeError("editorial stage exceeded its deadline") from None
        if code:
            raise RuntimeError("editorial stage failed: " + args[0])
    finally:
        stop_collectors()


def proposal(mirror, env, parent, changed, work, lane, date, evidence):
    current = git(mirror, env, "ls-remote", "origin", "refs/heads/main").decode().split()[0]
    if current != parent:
        raise RuntimeError("source superseded during generation; proposal cannot be promoted")
    if not changed:
        return parent
    index = mirror.parent / "proposal-index"
    commit_env = {**env, "GIT_INDEX_FILE": str(index), "GIT_AUTHOR_NAME": "seiche-desk",
                  "GIT_AUTHOR_EMAIL": "desk@seiche.info", "GIT_COMMITTER_NAME": "seiche-desk",
                  "GIT_COMMITTER_EMAIL": "desk@seiche.info"}
    git(mirror, commit_env, "read-tree", parent)
    for name, content in sorted(changed.items()):
        if not output_allowed(name, lane, date):
            raise ValueError("proposal path outside inert desk boundary")
        blob = git(mirror, env, "hash-object", "-w", "--stdin", data=content).decode().strip()
        git(mirror, commit_env, "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
    rows = json.loads(read_regular(work / "frontend/public/dispatches/index.json"))
    slug = date + ("-daily" if lane == "daily" else "-week-ahead")
    title = next(row["title"] for row in rows if row["slug"] == slug)
    if not isinstance(title, str) or not title or len(title) > 500 or any(ord(c) < 32 for c in title):
        raise ValueError("invalid edition commit title")
    tree = git(mirror, commit_env, "write-tree").decode().strip()
    prefix = "dispatch: " if lane == "daily" else "week ahead: "
    commit = git(mirror, commit_env, "commit-tree", tree, "-p", parent,
                 data=(prefix + title + "\n").encode()).decode().strip()
    paths = git(mirror, env, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).decode().splitlines()
    if set(paths) != set(changed):
        raise ValueError("sealed proposal diff mismatch")
    git(mirror, env, "update-ref", "refs/heads/editorial-proposal", commit)
    git(mirror, env, "bundle", "create", str(evidence / "proposal.bundle"), "refs/heads/editorial-proposal")
    return commit


def main():
    os.umask(0o022)
    if os.geteuid() != 0:
        raise RuntimeError("controller must own source and drop candidate privileges")
    if os.environ.get("EDITORIAL_APPLY", "0") != "0":
        raise RuntimeError("publication is gated; this reviewed controller prepares proposals only")
    manifest = (ROOT / "manifest.json").read_bytes()
    for name, expected in json.loads(manifest).items():
        if digest((ROOT / name).read_bytes()) != expected:
            raise RuntimeError("controller image differs from reviewed manifest")
    policy = json.loads((ROOT / "policy.json").read_text())
    lane = os.environ["EDITORIAL_LANE"]
    if lane not in {"daily", "weekly"}:
        raise ValueError("unknown editorial lane")
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    writer = {name: os.environ.pop(name, "") for name in (
        "EDITORIAL_LLM_API_KEY", "EDITORIAL_LLM_BASE_URL", "EDITORIAL_LLM_MODEL",
        "EDITORIAL_REVIEW_MODEL", "EDITORIAL_REASONING_EFFORT")}
    if writer["EDITORIAL_LLM_API_KEY"]:
        if {k: v for k, v in writer.items() if k != "EDITORIAL_LLM_API_KEY"} != policy["writer"]:
            raise ValueError("writer endpoint/model differs from reviewed repository variables")
    else:
        writer = {}
    evidence = Path("/evidence") / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + lane)
    evidence.mkdir(mode=0o700, parents=True)
    with tempfile.TemporaryDirectory(prefix="editorial-private-") as private, tempfile.TemporaryDirectory(prefix="editorial-candidate-") as public:
        private_root, public_root = Path(private), Path(public)
        public_root.chmod(0o755)
        env = clean_env(private_root)
        mirror = private_root / "repository.git"
        git(None, env, "clone", "--bare", "--single-branch", "--branch", "main", REPOSITORY, str(mirror))
        source = git(mirror, env, "rev-parse", "refs/heads/main").decode().strip()
        work = public_root / "repo"
        work.mkdir(mode=0o755)
        baseline = checkout(mirror, env, source, work, policy)
        runtime = public_root / "runtime"
        runtime.mkdir(mode=0o700)
        os.chown(runtime, WRITER_UID, WRITER_UID)
        board = public_root / "board"
        board.mkdir(mode=0o700)
        os.chown(board, WRITER_UID, WRITER_UID)
        event("editorial_start", source=source, lane=lane, writer_configured=bool(writer), controller=policy["controller_source"])
        run_stage(work, ["backend/scripts/export_public.py", str(board / "public.json"), str(board / "overview.json")], public_root / "scratch-board", runtime)
        seal_outputs(work, baseline, lane, date, allow_content=False)
        overview = read_regular(board / "overview.json")
        snapshot = json.loads(overview)
        generated = datetime.fromisoformat(snapshot["generated_at"].replace("Z", "+00:00"))
        if not 0 <= (datetime.now(timezone.utc) - generated).total_seconds() <= 7200:
            raise ValueError("board clock outside current generation window")
        if not any(isinstance(v, dict) and v.get("ok") for v in snapshot.get("engines", {}).values()):
            raise ValueError("board has no functioning engines")
        for path in board.iterdir():
            read_regular(path)
            os.chown(path, 0, 0)
            path.chmod(0o444)
        os.chown(board, 0, 0)
        board.chmod(0o755)
        allow_outputs(work, baseline, lane, date)
        module = "seiche.dispatch_daily" if lane == "daily" else "seiche.dispatch_weekly"
        run_stage(work, ["-m", module, "--snapshot", str(board / "overview.json")], public_root / "scratch-dispatch", runtime, deadline=600)
        if lane == "daily":
            run_stage(work, ["-m", "seiche.article_daily", "--snapshot", str(board / "overview.json")], public_root / "scratch-article", runtime, writer=writer, deadline=900)
        changed = seal_outputs(work, baseline, lane, date, allow_content=True)
        preserve_archive(work, mirror, env, source, lane, date)
        if read_regular(board / "overview.json") != overview:
            raise ValueError("writer modified the sealed board")
        if datetime.now(timezone.utc).strftime("%Y-%m-%d") != date:
            raise RuntimeError("edition crossed UTC date boundary")
        commit = proposal(mirror, env, source, changed, work, lane, date, evidence)
        (evidence / "overview.json").write_bytes(overview)
        (evidence / "public.json").write_bytes(read_regular(board / "public.json"))
        proof = {"schema": "seiche.railway-editorial-proposal.v1", "source": source, "proposal_commit": commit,
                 "controller_source": policy["controller_source"], "controller_digest": digest(manifest),
                 "lane": lane, "date": date, "board_sha256": digest(overview), "board_generated_at": snapshot["generated_at"],
                 "engine_count": len(snapshot.get("engines", {})), "engines_ok": sum(bool(isinstance(v, dict) and v.get("ok")) for v in snapshot.get("engines", {}).values()),
                 "changed": {name: digest(body) for name, body in changed.items()}, "writer_configured": bool(writer),
                 "published": False, "announced": False, "publication_gate": "original full publisher release gate required",
                 "deployment": os.environ.get("RAILWAY_DEPLOYMENT_ID", "local"), "observed_at": datetime.now(timezone.utc).isoformat()}
        (evidence / "proof.json").write_text(json.dumps(proof, sort_keys=True) + "\n")
    event("RAILWAY_EDITORIAL_PROPOSAL_PASS", **proof)


if __name__ == "__main__":
    main()
