"""Assemble independently pinned static/full controllers from exact Git blobs."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
FILES = (
    "ops/release/verify_catalog_publication.py",
    "ops/release/verify_frontend_publication.py",
    "ops/release/frontend_site_proof.py",
    "ops/release/verify_public_dataset.py",
)

def assemble_context(root, output, *, source="HEAD", kind="static", engine_source=None):
    if kind not in ("static", "full"):
        raise ValueError("Unknown publication controller kind")

    def resolve(revision):
        sha = subprocess.check_output(
            ["git", "rev-parse", "--verify", "--end-of-options", revision + "^{commit}"],
            cwd=root, text=True,
        ).strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Controller source is not an exact commit")
        return sha

    def blob(sha, name):
        entry = subprocess.check_output(
            ["git", "ls-tree", sha, "--", name], cwd=root, text=True
        ).strip()
        if not re.fullmatch(r"100644 blob [0-9a-f]{40}\t" + re.escape(name), entry):
            raise ValueError("Bundle input is not a tracked nonexecutable regular file: " + name)
        return subprocess.check_output(["git", "show", f"{sha}:{name}"], cwd=root)

    sha = resolve(source)
    prefix = "ops/railway-automation/" + ("publisher" if kind == "static" else "full-publisher")
    workflow = "publish-static.yml" if kind == "static" else "publish.yml"
    contents = {
        name: blob(sha, prefix + "/" + name)
        for name in ("Dockerfile", "publish.py", "test_publish.py")
    }
    contents["github-known-hosts"] = blob(sha, "ops/railway-automation/publisher/github-known-hosts")
    contents[workflow] = blob(sha, ".github/workflows/" + workflow)
    contents["requirements-social-cards.txt"] = blob(sha, "ops/requirements-social-cards.txt")
    contents["gate-sha256.json"] = json.dumps(
        {name: hashlib.sha256(blob(sha, name)).hexdigest() for name in FILES}, indent=2
    ).encode()
    identity = {"sha": sha}
    if engine_source is not None:
        engine_sha = resolve(engine_source)
        identity.update({
            "engineSourceSha": engine_sha,
            "engineGateSha256": {
                name: hashlib.sha256(blob(engine_sha, name)).hexdigest() for name in FILES
            },
        })
    contents["controller-source.json"] = json.dumps(identity, indent=2).encode()
    output.mkdir(parents=True, exist_ok=False)
    for name, data in contents.items():
        (output / name).write_bytes(data)
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source", default="HEAD")
    parser.add_argument("--kind", choices=("static", "full"), default="static")
    parser.add_argument("--engine-source", help="Pin the separate signed engine subject for source equivalence")
    args = parser.parse_args()
    assemble_context(ROOT, args.output, source=args.source, kind=args.kind, engine_source=args.engine_source)
    print(args.output)


if __name__ == "__main__":
    main()
