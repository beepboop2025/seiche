"""Exercise event and owner identity rejection before release jobs get authority."""
import os
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ReleaseIdentityBoundaries(unittest.TestCase):
    def run_guard(self, guard, **values):
        env = {**os.environ, "GITHUB_EVENT_NAME": "release", "GITHUB_REF": "refs/tags/v0.16.6",
               "GITHUB_REF_NAME": "v0.16.6", "GITHUB_REF_TYPE": "tag", "RELEASE_TAG": "v0.16.6",
               "GITHUB_SHA": "a" * 40, "release_sha": "a" * 40, "REVISION": "a" * 40, **values}
        return subprocess.run(["/bin/bash", "-e", "-c", guard], env=env,
                              capture_output=True, check=False).returncode

    def test_only_release_or_signed_workflow_tag_event_shapes_are_admitted(self):
        text = (ROOT / ".github/workflows/publish-container.yml").read_text()
        guards = re.findall(r'          case "\$GITHUB_EVENT_NAME" in.*?\n          esac', text, re.S)
        self.assertEqual(len(guards), 3)
        cases = [
            ({}, True),
            ({"GITHUB_SHA": "b" * 40}, False),
            ({"GITHUB_REF": "refs/tags/v0.16.5"}, False),
            ({"GITHUB_EVENT_NAME": "push"}, False),
            ({"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF_NAME": "v0.16.6-workflow.2"}, True),
            ({"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF_TYPE": "branch",
              "GITHUB_REF_NAME": "v0.16.6-workflow.2"}, False),
            ({"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF_NAME": "v0.16.6"}, False),
        ]
        for guard in guards:
            for values, expected in cases:
                with self.subTest(values=values):
                    self.assertEqual(self.run_guard(guard, **values) == 0, expected)

    def test_both_owner_emails_and_no_foreign_author_are_accepted(self):
        for name in ("publish-pypi.yml", "publish-mcp.yml", "publish-container.yml"):
            text = (ROOT / ".github/workflows" / name).read_text()
            guards = re.findall(r'          case "\$\(git show -s --format=%ae HEAD\)" in.*?\n          esac', text, re.S)
            self.assertTrue(guards)
            for guard in guards:
                for email in ("beepboop2025@users.noreply.github.com", "mrinal091997@gmail.com", "foreign@example.com"):
                    with self.subTest(workflow=name, email=email):
                        script = guard.replace("$(git show -s --format=%ae HEAD)", email)
                        self.assertEqual(self.run_guard(script) == 0, email != "foreign@example.com")


if __name__ == "__main__":
    unittest.main()
