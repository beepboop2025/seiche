"""Assemble a reviewed controller context without application runtime credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
FILES = ("ops/release/verify_catalog_publication.py", "ops/release/verify_frontend_publication.py",
         "ops/release/frontend_site_proof.py", "ops/release/verify_public_dataset.py")
parser = argparse.ArgumentParser()
parser.add_argument("output", type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
for name in ("Dockerfile", "publish.py", "test_publish.py", "github-known-hosts"):
    shutil.copyfile(Path(__file__).with_name(name), args.output / name)
shutil.copyfile(ROOT / ".github/workflows/publish-static.yml", args.output / "publish-static.yml")
shutil.copyfile(ROOT / "ops/requirements-social-cards.txt", args.output / "requirements-social-cards.txt")
(args.output / "gate-sha256.json").write_text(json.dumps({
    name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}, indent=2))
(args.output / "controller-source.json").write_text(json.dumps({
    "sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}, indent=2))
print(args.output)
