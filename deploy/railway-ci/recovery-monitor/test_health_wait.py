"""Separate image qualification of bounded health transport and strict admission."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import health_wait as health
import prepare


ROOT = Path(__file__).resolve().parent
SOURCE = "a" * 40
DEPLOYMENT = "671eee65-8f3a-4bd9-b2d7-c3cac9894916"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def sample(age=0, **changes):
    body = {"generated_at": (NOW - timedelta(seconds=age)).isoformat(),
            "version": "0.14.1", "faults": [], "provenance": [{"private": "not-for-logs"}]}
    body.update(changes)
    headers = ("x-seiche-railway-authority: production\n"
               f"x-seiche-railway-deployment: {DEPLOYMENT}\n"
               f"x-seiche-release-sha: {SOURCE}\n").encode()
    return json.dumps(body).encode(), headers


class HealthWaitTests(unittest.TestCase):
    def exercise(self, pairs, *, frozen=False, fetch_seconds=0):
        elapsed = [0]
        calls, sleeps, logs = [], [], []
        def read(side, remaining):
            self.assertGreater(remaining, 0)
            calls.append((side, remaining))
            index = min((len(calls) - 1) // 2, len(pairs) - 1)
            value = pairs[index][health.SIDES.index(side)]
            elapsed[0] += fetch_seconds
            if isinstance(value, Exception):
                raise value
            return value
        def sleep(seconds):
            sleeps.append(seconds)
            if not frozen:
                elapsed[0] += seconds
        def run():
            return health.wait_for_health(SOURCE, read=read, sleep=sleep,
                monotonic=lambda: elapsed[0], utcnow=lambda: NOW + timedelta(seconds=elapsed[0]),
                report=logs.append)
        return run, elapsed, calls, sleeps, logs

    def test_fresh_pair_finishes_without_retry(self):
        run, _, calls, sleeps, logs = self.exercise([(sample(1), sample(1))])
        run()
        self.assertEqual([x[0] for x in calls], ["origin", "public"])
        self.assertEqual(sleeps, [])
        self.assertTrue(all(row["reason"] == "fresh" for row in logs))

    def test_only_old_healthy_pair_waits_for_real_new_bodies(self):
        run, _, calls, sleeps, logs = self.exercise([
            (sample(901), sample(901)), (sample(), sample())])
        run()
        self.assertEqual(len(calls), 4)
        self.assertEqual(sleeps, [10])
        self.assertEqual([row["reason"] for row in logs], ["old_snapshot"] * 2 + ["fresh"] * 2)
        self.assertNotEqual(logs[0]["body_sha256"], logs[2]["body_sha256"])

    def test_one_old_edge_requires_refetching_both(self):
        run, _, calls, sleeps, _ = self.exercise([
            (sample(), sample(901)), (sample(), sample())])
        run()
        self.assertEqual([x[0] for x in calls], ["origin", "public"] * 2)
        self.assertEqual(sleeps, [10])

    def test_original_age_boundaries_and_future_clock(self):
        for age, reason in ((0, "fresh"), (900, "fresh"), (900.001, "old_snapshot"), (-0.001, "future_generated_at")):
            with self.subTest(age=age):
                value, _ = health.assess("origin", *sample(age), SOURCE, NOW)
                self.assertEqual(value["reason"], reason)

    def test_invalid_health_never_waits_and_diagnostics_redact_values(self):
        bad = [{"faults": ["secret-fault"]}, {"faults": None}, {"faults": {}},
               {"version": ""}, {"version": 1}, {"provenance": []}, {"provenance": {}},
               {"generated_at": "secret-clock"}, {"generated_at": None},
               {"generated_at": "2026-10-04T11:00:00"},
               {"generated_at": (NOW + timedelta(seconds=1)).isoformat()}]
        for changes in bad:
            with self.subTest(changes=changes):
                run, _, calls, sleeps, logs = self.exercise([(sample(1000, **changes), sample())])
                with self.assertRaises(health.HealthError):
                    run()
                self.assertEqual(len(calls), 1)
                self.assertEqual(sleeps, [])
                encoded = json.dumps(logs)
                for private in ("secret-fault", "secret-clock", "not-for-logs"):
                    self.assertNotIn(private, encoded)

    def test_malformed_or_nonobject_bodies_fail_immediately(self):
        for raw in (b"", b"[]", b"null", b"private malformed body", b"\xff"):
            with self.subTest(raw=raw):
                run, _, calls, sleeps, logs = self.exercise([((raw, sample()[1]), sample())])
                with self.assertRaisesRegex(health.HealthError, "malformed_body"):
                    run()
                self.assertEqual((len(calls), sleeps), (1, []))
                self.assertEqual(logs[0]["body_sha256"], hashlib.sha256(raw).hexdigest())

    def test_fault_during_wait_fails_on_that_attempt(self):
        run, _, calls, sleeps, _ = self.exercise([
            (sample(901), sample(901)), (sample(901, faults=["held"]), sample())])
        with self.assertRaisesRegex(health.HealthError, "faults_present"):
            run()
        self.assertEqual((len(calls), sleeps), (3, [10]))

    def test_transport_failure_never_retries(self):
        run, _, calls, sleeps, logs = self.exercise([
            (sample(901), health.HealthError("transport_failure"))])
        with self.assertRaisesRegex(health.HealthError, "transport_failure"):
            run()
        self.assertEqual((len(calls), sleeps), (2, []))
        self.assertEqual(logs[-1]["endpoint"], "public")

    def test_source_failure_is_not_hidden_by_matching_edges_or_old_health(self):
        raw, headers = sample(901)
        foreign = (raw, headers.replace(SOURCE.encode(), b"b" * 40))
        run, _, calls, sleeps, _ = self.exercise([(foreign, foreign)])
        with self.assertRaisesRegex(health.HealthError, "source_identity_mismatch"):
            run()
        self.assertEqual((len(calls), sleeps), (1, []))

    def test_split_deployment_stops_without_wait(self):
        raw, headers = sample(901)
        public = (raw, headers.replace(DEPLOYMENT.encode(), b"671eee65-8f3a-4bd9-b2d7-c3cac9894917"))
        run, _, _, sleeps, logs = self.exercise([(sample(901), public)])
        with self.assertRaisesRegex(health.HealthError, "health_pair_invalid"):
            run()
        self.assertEqual(sleeps, [])
        self.assertTrue(all(row["reason"] == "split_deployment_identity" for row in logs))

    def test_deadline_bounds_wait_and_request_remaining_budget(self):
        run, elapsed, calls, sleeps, _ = self.exercise([(sample(1000), sample(1000))], fetch_seconds=3)
        with self.assertRaisesRegex(health.HealthError, "freshness_wait_expired"):
            run()
        self.assertLessEqual(elapsed[0], 120)
        self.assertLessEqual(len(calls), 26)
        self.assertTrue(all(0 < remaining <= 120 for _, remaining in calls))
        self.assertLessEqual(sum(sleeps), 120)

    def test_attempt_limit_survives_nonadvancing_test_clock(self):
        run, _, calls, sleeps, _ = self.exercise([(sample(1000), sample(1000))], frozen=True)
        with self.assertRaisesRegex(health.HealthError, "freshness_wait_expired"):
            run()
        self.assertEqual((len(calls), len(sleeps)), (26, 12))

    def test_origin_age_is_rechecked_after_slow_public_read(self):
        run, _, calls, sleeps, logs = self.exercise([
            (sample(899), sample()), (sample(), sample())], fetch_seconds=1)
        run()
        self.assertEqual(len(calls), 4)
        self.assertEqual(sleeps, [10])
        self.assertEqual(logs[0]["reason"], "old_snapshot")

    def test_curl_uses_original_https_targets_and_private_header_without_retries(self):
        with tempfile.TemporaryDirectory() as name, mock.patch.dict(os.environ,
                {"RUNNER_TEMP": name, "RAILWAY_ORIGIN": "https://fixture.up.railway.app",
                 "RAILWAY_EDGE_TOKEN": "secret-token"}):
            commands = []
            def run(arguments, **kwargs):
                commands.append((arguments, kwargs))
                Path(arguments[arguments.index("--output") + 1]).write_bytes(sample()[0])
                Path(arguments[arguments.index("--dump-header") + 1]).write_bytes(sample()[1])
                return subprocess.CompletedProcess(arguments, 0, b"200", b"private stderr")
            previous = Path.cwd()
            try:
                os.chdir(name)
                with mock.patch.object(health.subprocess, "run", side_effect=run):
                    health.fetch("origin", 2)
                    health.fetch("public", 30)
            finally:
                os.chdir(previous)
            for args, options in commands:
                self.assertEqual(args[:7], ["curl", "--silent", "--show-error", "--proto", "=https", "--tlsv1.2", "--connect-timeout"])
                self.assertNotIn("secret-token", repr((args, options)))
                self.assertNotIn("--location", args)
                self.assertNotIn("--retry", args)
            self.assertEqual(commands[0][0][-1], "https://fixture.up.railway.app/api/health")
            self.assertIn("@" + name + "/edge-header", commands[0][0])
            self.assertEqual(commands[0][1]["timeout"], 2)
            self.assertEqual(commands[1][0][-1], "https://api.seiche.info/api/health")
            self.assertNotIn("--header", commands[1][0])

    def test_curl_failure_and_non200_never_expose_stderr_or_body(self):
        with tempfile.TemporaryDirectory() as name:
            previous = Path.cwd()
            try:
                os.chdir(name)
                for returncode, stdout in ((0, b"503"), (1, b"200")):
                    result = subprocess.CompletedProcess([], returncode, stdout, b"secret transport response")
                    with mock.patch.object(health.subprocess, "run", return_value=result):
                        with self.assertRaises(health.HealthError) as error:
                            health.fetch("public", 1)
                    self.assertNotIn("secret", str(error.exception))
                for failure in (subprocess.TimeoutExpired("private-command", 1), OSError("private-path")):
                    with mock.patch.object(health.subprocess, "run", side_effect=failure):
                        with self.assertRaisesRegex(health.HealthError, "^transport_failure$"):
                            health.fetch("public", 1)
            finally:
                os.chdir(previous)

    def test_assembled_fetch_change_preserves_entire_original_final_validator(self):
        # Assembled validator is copied directly from the protected P workflow.
        proof = (ROOT / "proof.sh").read_text()
        self.assertIn('/health_wait.py"', proof)
        validator = proof.split('OUTPUT="$GITHUB_OUTPUT"', 1)[1].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        self.assertEqual(validator + "\n", (ROOT / "validator.py").read_text())
        self.assertIn('not timedelta(0) <= now - generated <= timedelta(minutes=15)', validator)

    def test_changed_fetch_semantics_or_duplicate_boundary_rejected(self):
        for body in ('origin_status=$(curl --silent --show-error --proto changed\ntest "$public_status" = 200',
                     'test "$public_status" = 200', ''):
            with self.subTest(body=body), self.assertRaises(ValueError):
                prepare.health_wait_proof(body)


def run_health_tests():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(HealthWaitTests)
    inventory = [test.id() for test in suite]
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = stream.getvalue().encode()
    policy = json.loads((ROOT / "policy.json").read_bytes())
    passed = (result.wasSuccessful() and not result.skipped and not result.expectedFailures and
              not result.unexpectedSuccesses and result.testsRun == len(inventory) == len(set(inventory)))
    proof = {"schema": "seiche.recovery-health-wait-tests.v1", "status": "PASS" if passed else "FAIL",
             "passed": result.testsRun - len(result.errors) - len(result.failures) - len(result.skipped),
             "skipped": len(result.skipped), "test_ids": inventory,
             "controller_source": policy["controller_source"], "application_source": policy["source"],
             "manifest_sha256": hashlib.sha256((ROOT / "manifest.json").read_bytes()).hexdigest(),
             "log_sha256": hashlib.sha256(log).hexdigest(),
             "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                               for name in ("health_wait.py", "test_health_wait.py", "prepare.py")}}
    receipt = (json.dumps(proof, sort_keys=True) + "\n").encode()
    for name, raw in (("monitor-health-wait-tests.log", log), ("monitor-health-wait-tests.json", receipt)):
        with (ROOT / name).open("xb") as output:
            output.write(raw)
    if not passed:
        sys.stderr.write(stream.getvalue())
        raise SystemExit("Separate monitor health wait checks failed")
    record = {"schema": "seiche.recovery-health-wait-log.v1", "encoding": "base64",
              "byte_length": len(log), "sha256": hashlib.sha256(log).hexdigest(),
              "data_base64": base64.b64encode(log).decode("ascii"),
              "health_receipt_sha256": hashlib.sha256(receipt).hexdigest(),
              "controller_source": proof["controller_source"], "application_source": proof["application_source"],
              "manifest_sha256": proof["manifest_sha256"]}
    encoded = "RAILWAY_RECOVERY_HEALTH_WAIT_LOG " + json.dumps(record, sort_keys=True)
    if len(encoded.encode()) + 1 > 8192:
        raise ValueError("Detailed health wait test log exceeds build-record bound")
    print("RAILWAY_RECOVERY_HEALTH_WAIT_TESTS_PASS " + receipt.decode().strip(), flush=True)
    print(encoded, flush=True)


if __name__ == "__main__":
    run_health_tests()
