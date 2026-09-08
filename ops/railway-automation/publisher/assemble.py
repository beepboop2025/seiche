"""Assemble a reviewed controller context without application runtime credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
FILES = ("ops/release/verify_catalog_publication.py", "ops/release/verify_frontend_publication.py",
         "ops/release/frontend_site_proof.py", "ops/release/verify_public_dataset.py")
parser = argparse.ArgumentParser()
parser.add_argument("output", type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()

def blob(name):
    return subprocess.check_output(["git", "show", f"{sha}:{name}"], cwd=ROOT)

for name in ("Dockerfile", "publish.py", "test_publish.py", "github-known-hosts"):
    (args.output / name).write_bytes(blob("ops/railway-automation/publisher/" + name))
(args.output / "publish-static.yml").write_bytes(blob(".github/workflows/publish-static.yml"))
(args.output / "requirements-social-cards.txt").write_bytes(blob("ops/requirements-social-cards.txt"))
(args.output / "gate-sha256.json").write_text(json.dumps({
    name: hashlib.sha256(blob(name)).hexdigest() for name in FILES}, indent=2))
(args.output / "controller-source.json").write_text(json.dumps({"sha": sha}, indent=2))
print(args.output)
