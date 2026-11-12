"""Execute the actual OpenBB release binding steps with offline Git responses."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/publish-openbb.yml"
SHA = "a" * 40
FINGERPRINT = "SHA256:" + "A" * 43
STEPS = (
    "Bind the candidate to the requested release and package versions",
    "Independently authenticate the release and pinned signer",
)


def embedded_script(name: str) -> str:
    document = WORKFLOW.read_text()
    start = document.index(f"      - name: {name}\n")
    block = document[start:].split("\n      - ", 1)[0]
    return textwrap.dedent(block.split("        run: |\n", 1)[1])


FAKE_GIT = r"""
import json, os, sys
args = sys.argv[1:]
with open(os.environ['GIT_CALLS'], 'a') as output:
    output.write(json.dumps(args) + '\n')
fault = os.environ.get('GIT_FAULT', '')
sha = 'a' * 40
if args[:2] == ['cat-file', '-t']:
    print('commit' if fault == 'lightweight' else 'tag')
elif args[:1] == ['rev-parse']:
    print('b' * 40 if fault == 'tag-target' and args[1] != 'HEAD' else sha)
elif args[:1] == ['show']:
    print('foreign@example.com' if fault == 'author' else 'beepboop2025@users.noreply.github.com')
elif args[:1] == ['fetch']:
    sys.exit(1 if fault == 'fetch' else 0)
elif args[:1] == ['merge-base']:
    sys.exit(1 if fault == 'ancestry' else 0)
elif args[:1] == ['-c'] and args[2] in ('verify-commit', 'verify-tag'):
    sys.exit(1 if fault == args[2] else 0)
else:
    raise SystemExit('unexpected Git operation: ' + repr(args))
"""


class OpenBBReleaseIdentity(unittest.TestCase):
    def run_step(
        self, name: str, tag: str, version="0.1.1", fault="", fingerprint=FINGERPRINT
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for parent in ("backend", "integrations/openbb", "ops/deploy", "bin"):
                (root / parent).mkdir(parents=True, exist_ok=True)
            (root / "backend/pyproject.toml").write_text(
                '[project]\nversion="0.13.12"\n'
            )
            (root / "integrations/openbb/pyproject.toml").write_text(
                '[project]\nversion="0.1.1"\n'
            )
            (root / "ops/deploy/release-allowed-signers").write_text("test signer\n")
            interpreter = "#!" + sys.executable + "\n"
            (root / "bin/git").write_text(interpreter + textwrap.dedent(FAKE_GIT))
            (root / "bin/ssh-keygen").write_text(
                interpreter + f"print('256 {FINGERPRINT} test (ED25519)')\n"
            )
            (root / "bin/python").symlink_to(sys.executable)
            for executable in ("git", "ssh-keygen"):
                (root / "bin" / executable).chmod(0o755)
            calls = root / "calls.jsonl"
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
                    "RELEASE_TAG": tag,
                    "OPENBB_VERSION": version,
                    "RELEASE_SIGNING_KEY_FINGERPRINT": fingerprint,
                    "GIT_CALLS": str(calls),
                    "GIT_FAULT": fault,
                }
            )
            result = subprocess.run(
                [
                    shutil.which("bash"),
                    "--noprofile",
                    "--norc",
                    "-eo",
                    "pipefail",
                    "-c",
                    embedded_script(name),
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
            operations = (
                [json.loads(line) for line in calls.read_text().splitlines()]
                if calls.exists()
                else []
            )
            return result, operations

    def test_both_signed_tag_namespaces_retain_exact_source_and_ancestry(self):
        for step in STEPS:
            for tag in ("openbb-seiche-v0.1.1", "v0.13.12"):
                with self.subTest(step=step, tag=tag):
                    result, calls = self.run_step(step, tag)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(["cat-file", "-t", tag], calls)
                    self.assertIn(["rev-parse", tag + "^{commit}"], calls)
                    self.assertIn(
                        [
                            "merge-base",
                            "--is-ancestor",
                            SHA,
                            "refs/remotes/origin/main",
                        ],
                        calls,
                    )
                    if step == STEPS[1]:
                        self.assertTrue(
                            any(call[2:] == ["verify-commit", "HEAD"] for call in calls)
                        )
                        self.assertTrue(
                            any(call[2:] == ["verify-tag", tag] for call in calls)
                        )

    def test_wrong_tag_or_package_version_fails_in_both_steps(self):
        for step in STEPS:
            for tag, version in (
                ("openbb-seiche-v0.1.0", "0.1.1"),
                ("openbb-seiche-v0.1.1-extra", "0.1.1"),
                ("openbb-seiche-v0.1.1", "0.1.0"),
                ("v0.13.11", "0.1.1"),
                ("v0.13.12", "0.1.0"),
                ("other-v0.1.1", "0.1.1"),
            ):
                with self.subTest(step=step, tag=tag, version=version):
                    result, _ = self.run_step(step, tag, version)
                    self.assertNotEqual(result.returncode, 0)

    def test_identity_and_main_failures_remain_closed(self):
        for step in STEPS:
            for fault in ("lightweight", "tag-target", "author", "fetch", "ancestry"):
                with self.subTest(step=step, fault=fault):
                    result, _ = self.run_step(step, "openbb-seiche-v0.1.1", fault=fault)
                    self.assertNotEqual(result.returncode, 0)

    def test_commit_tag_and_pinned_signer_failures_remain_closed(self):
        for fault in ("verify-commit", "verify-tag"):
            with self.subTest(fault=fault):
                result, _ = self.run_step(STEPS[1], "openbb-seiche-v0.1.1", fault=fault)
                self.assertNotEqual(result.returncode, 0)
        for fingerprint in ("malformed", "SHA256:" + "B" * 43):
            with self.subTest(fingerprint=fingerprint):
                result, _ = self.run_step(
                    STEPS[1], "openbb-seiche-v0.1.1", fingerprint=fingerprint
                )
                self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
