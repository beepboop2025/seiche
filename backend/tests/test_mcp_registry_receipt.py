"""Execute the workflow's readback with an offline registry and clock."""

from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import textwrap
import unittest
from unittest.mock import patch
import urllib.error


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/publish-mcp.yml").read_text()
STEP = WORKFLOW.split(
    "      - name: Poll and verify the exact public MCP Registry record\n", 1
)[1]
SCRIPT = textwrap.dedent(STEP.split("<<'PY'\n", 1)[1].split("          PY\n", 1)[0])
LOCAL = json.loads((ROOT / "server.json").read_text())


class ReceiptTests(unittest.TestCase):
    def record(self):
        return {
            "server": copy.deepcopy(LOCAL),
            "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}},
        }

    def execute(self, actions):
        clock = [0.0]
        requests = []
        reads = []
        closed = []
        actions = iter(actions)

        class Response:
            def __init__(self, action, timeout):
                self.action, self.timeout = action, timeout

            def __enter__(self):
                return self

            def __exit__(self, *args):
                closed.append(True)

            def read(self, limit):
                reads.append(limit)
                if self.action == "body-timeout":
                    clock[0] += self.timeout
                    raise TimeoutError("body read timed out")
                return self.action

        def open_record(request, *, timeout):
            requests.append((request, timeout))
            action = next(actions)
            if isinstance(action, BaseException):
                if isinstance(action, TimeoutError):
                    clock[0] += timeout
                raise action
            return Response(action, timeout)

        def sleep(seconds):
            self.assertGreaterEqual(seconds, 0)
            clock[0] += seconds

        output = io.StringIO()
        error = None
        with (
            patch("urllib.request.urlopen", side_effect=open_record),
            patch("time.monotonic", side_effect=lambda: clock[0]),
            patch("time.sleep", side_effect=sleep),
            patch.object(Path, "read_text", return_value=json.dumps(LOCAL)),
            contextlib.redirect_stdout(output),
        ):
            try:
                exec(compile(SCRIPT, "<MCP workflow receipt>", "exec"), {})
            except BaseException as exc:
                error = exc
        for request, timeout in requests:
            self.assertEqual(request.get_method(), "GET")
            self.assertTrue(request.full_url.startswith(
                "https://registry.modelcontextprotocol.io/v0.1/servers/"
            ))
            self.assertTrue(request.full_url.endswith("?include_deleted=true"))
            self.assertLessEqual(timeout, 30)
            self.assertGreater(timeout, 0)
        self.assertTrue(all(limit == 2 * 1024 * 1024 + 1 for limit in reads))
        return error, requests, clock[0], output.getvalue(), closed

    def test_connection_timeout_retries_only_get_then_verifies(self):
        result = self.execute([TimeoutError(), json.dumps(self.record()).encode()])
        self.assertIsNone(result[0])
        self.assertEqual(len(result[1]), 2)
        self.assertIn("receipt verified", result[3])

    def test_body_timeout_closes_response_and_retries_get(self):
        result = self.execute(["body-timeout", json.dumps(self.record()).encode()])
        self.assertIsNone(result[0])
        self.assertEqual(len(result[1]), 2)
        self.assertEqual(len(result[4]), 2)

    def test_timeout_exhaustion_stops_at_readback_deadline(self):
        result = self.execute([TimeoutError() for _ in range(24)])
        self.assertIsInstance(result[0], SystemExit)
        self.assertEqual([timeout for _, timeout in result[1]], [30, 30, 30, 15])
        self.assertEqual(result[2], 120)
        self.assertIn("within 120 seconds", str(result[0]))

    def test_not_found_still_retries(self):
        missing = urllib.error.HTTPError("https://example.invalid", 404, "missing", None, None)
        self.addCleanup(missing.close)
        result = self.execute([missing, json.dumps(self.record()).encode()])
        self.assertIsNone(result[0])
        self.assertEqual(len(result[1]), 2)

    def test_permission_error_is_not_retried(self):
        denied = urllib.error.HTTPError("https://example.invalid", 403, "denied", None, None)
        self.addCleanup(denied.close)
        result = self.execute([denied])
        self.assertIs(result[0], denied)
        self.assertEqual(len(result[1]), 1)

    def test_mismatched_manifest_is_not_retried(self):
        record = self.record()
        record["server"]["version"] = "wrong"
        result = self.execute([json.dumps(record).encode()])
        self.assertIsInstance(result[0], SystemExit)
        self.assertIn("differs from signed server.json", str(result[0]))
        self.assertEqual(len(result[1]), 1)

    def test_inactive_record_is_not_retried(self):
        record = self.record()
        record["_meta"]["io.modelcontextprotocol.registry/official"]["status"] = "deleted"
        result = self.execute([json.dumps(record).encode()])
        self.assertIsInstance(result[0], SystemExit)
        self.assertIn("not active", str(result[0]))
        self.assertEqual(len(result[1]), 1)

    def test_malformed_json_is_not_retried(self):
        result = self.execute([b"<html>provider error</html>"])
        self.assertIsInstance(result[0], json.JSONDecodeError)
        self.assertEqual(len(result[1]), 1)

    def test_oversized_body_is_not_retried(self):
        result = self.execute([b"x" * (2 * 1024 * 1024 + 1)])
        self.assertIsInstance(result[0], SystemExit)
        self.assertIn("exceeds 2 MiB", str(result[0]))
        self.assertEqual(len(result[1]), 1)

    def test_unrelated_network_error_is_not_retried(self):
        failed = ConnectionResetError("reset")
        result = self.execute([failed])
        self.assertIs(result[0], failed)
        self.assertEqual(len(result[1]), 1)


if __name__ == "__main__":
    unittest.main()
