"""Transport retries preserve the original availability and credential boundary."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest

import health_probe


class ProbeTests(unittest.TestCase):
    def exercise(self, responses, *, url=health_probe.PUBLIC, header=None):
        now = [0.0]
        calls = []

        def run(arguments, **kwargs):
            elapsed, code, status = responses[len(calls)]
            calls.append((arguments, kwargs))
            self.assertLessEqual(float(arguments[arguments.index("--max-time") + 1]), 5.0)
            self.assertLessEqual(kwargs["timeout"], 15.0 - now[0])
            now[0] += elapsed
            if code == "timeout":
                raise subprocess.TimeoutExpired(arguments, kwargs["timeout"])
            return types.SimpleNamespace(returncode=code, stdout=status.encode())

        result = health_probe.probe(url, header, clock=lambda: now[0], run=run)
        return result, calls

    def test_unanswered_connection_can_recover_inside_the_original_budget(self):
        result, calls = self.exercise([(5.0, 28, "000"), (0.4, 0, "200")])
        self.assertEqual(result["status"], "200")
        self.assertEqual(result["elapsed_seconds"], 5.4)
        self.assertEqual(len(calls), 2)

    def test_repeated_timeouts_exhaust_one_budget_without_extending_it(self):
        result, calls = self.exercise([(5.0, 28, "000")] * 3)
        self.assertEqual(result["status"], "000")
        self.assertEqual(result["elapsed_seconds"], 15.0)
        self.assertEqual(len(calls), 3)

    def test_response_after_deadline_is_never_accepted(self):
        result, calls = self.exercise([(15.1, 0, "200")])
        self.assertEqual(result["status"], "000")
        self.assertEqual(len(calls), 1)

    def test_http_errors_and_redirects_are_not_retried(self):
        for status in ("403", "429", "500", "503", "302"):
            with self.subTest(status=status):
                result, calls = self.exercise([(0.1, 0, status)])
                self.assertEqual(result["status"], status)
                self.assertEqual(len(calls), 1)

    def test_tls_failure_and_malformed_response_are_not_retried(self):
        for code, status in ((60, "000"), (0, "200unexpected"), (0, "000")):
            with self.subTest(code=code, status=status):
                result, calls = self.exercise([(0.1, code, status)])
                self.assertEqual(result["status"], "000")
                self.assertEqual(len(calls), 1)

    def test_deadline_kills_a_stalled_child_without_another_attempt(self):
        result, calls = self.exercise([(15.0, "timeout", "000")])
        self.assertEqual(result["status"], "000")
        self.assertEqual(len(calls), 1)

    def test_credential_is_private_and_cannot_reach_the_public_endpoint(self):
        sentinel = "fixture-edge-token-never-in-child-arguments"
        with tempfile.TemporaryDirectory() as directory:
            header = Path(directory) / "header"
            header.write_text("X-Seiche-Edge-Token: " + sentinel + "\n")
            header.chmod(0o600)
            result, calls = self.exercise([(0.1, 0, "200")], url=health_probe.ORIGIN, header=header)
            self.assertEqual(result["status"], "200")
            self.assertNotIn(sentinel, repr(calls) + json.dumps(result))
            self.assertEqual(calls[0][1]["env"], {"PATH": os.defpath, "LANG": "C"})
            self.assertIn("@" + str(header), calls[0][0])
            with self.assertRaisesRegex(ValueError, "credential scope"):
                health_probe.probe(health_probe.PUBLIC, header)
            for target in (health_probe.ORIGIN + ".attacker.invalid", "http://api.seiche.info", "https://example.invalid"):
                with self.assertRaises(ValueError):
                    health_probe.probe(target, header)

    def test_unsafe_headers_are_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            header = Path(directory) / "header"
            header.write_text("X-Seiche-Edge-Token: fixture\nInjected: other\n")
            header.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "contract"):
                health_probe.validate_header(header)
            header.write_text("X-Seiche-Edge-Token: fixture\n")
            header.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "private"):
                health_probe.validate_header(header)
            header.chmod(0o600)
            link = Path(directory) / "link"
            link.symlink_to(header)
            with self.assertRaises(OSError):
                health_probe.validate_header(link)


if __name__ == "__main__":
    unittest.main()
