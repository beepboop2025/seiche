"""Build the proposal controller only from an owner-signed Git commit."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from editorial import admitted_input


SIGNER = "SHA256:yhoa/PIDMM6M/ZennILp8jtRJy5pArncJRARbQssTMI"
DIRECTORY = "deploy/railway-ci/editorial-controller"
FILES = ("Dockerfile", "editorial.py", "isolation.py", "prepare.py", "test_editorial.py", "requirements.lock")


def prepare(repository, source, output, signer_key):
    if re.fullmatch(r"[0-9a-f]{40}", source) is None:
        raise ValueError("source must be a full immutable Git SHA")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repository), *args])
    controller = git("rev-parse", "HEAD").decode().strip()
    if subprocess.check_output(["ssh-keygen", "-lf", str(signer_key)], text=True).split()[1] != SIGNER:
        raise ValueError("unexpected controller signer")
    with tempfile.TemporaryDirectory(prefix="editorial-signature-") as name:
        allowed = Path(name) / "allowed-signers"
        allowed.write_text("owner " + signer_key.read_text().strip() + "\n")
        git("-c", "gpg.format=ssh", "-c", "gpg.ssh.allowedSignersFile=" + str(allowed), "verify-commit", controller)
    output.mkdir(parents=True, exist_ok=False)
    policy = {"source": source, "controller_source": controller, "controller_signer": SIGNER,
              "publication_enabled": False, "inputs": {}, "writer": {
                  "EDITORIAL_LLM_BASE_URL": "https://openrouter.ai/api/v1",
                  "EDITORIAL_LLM_MODEL": "openai/gpt-5.6-terra",
                  "EDITORIAL_REVIEW_MODEL": "openai/gpt-5.6-terra",
                  "EDITORIAL_REASONING_EFFORT": "low"}}
    for raw in git("ls-tree", "-rz", "--full-tree", source).split(b"\0"):
        if not raw:
            continue
        header, raw_path = raw.split(b"\t", 1)
        path = raw_path.decode()
        if admitted_input(path):
            content = git("show", f"{source}:{path}")
            policy["inputs"][path] = hashlib.sha256(content).hexdigest()
    for name in FILES:
        (output / name).write_bytes(git("show", f"{controller}:{DIRECTORY}/{name}"))
    (output / "policy.json").write_text(json.dumps(policy, sort_keys=True) + "\n")
    manifest = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(output.iterdir())}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output), "source": source, "controller": controller,
                      "manifest_sha256": hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest()}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--signer-public-key", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.repository, args.source, args.output, args.signer_public_key)
