"""Separately prove pinned application/current workflow identity admission."""

import contextlib
import copy
import base64
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

import monitor
import prepare
import test_monitor as original


ROOT = Path(__file__).resolve().parent
RUNTIME = "a" * 40
WORKFLOW = "b" * 40
CONTROLLER = "c" * 40
REAL_RUN = subprocess.run
LOG_MARKER = "RAILWAY_RECOVERY_MONITOR_ROLE_LOG "
LOG_RECORD_LIMIT = 8192


class MonitorRoleTests(unittest.TestCase):
    def run_monitor(self, runtime=RUNTIME, workflow=WORKFLOW, edges=None, malformed=False):
        """Stub only external transports; execute the original workflow validator."""
        edges = edges or (runtime, runtime)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = root / "controller"
            controller.mkdir()
            evidence_base = root / "evidence"
            policy = json.loads((ROOT / "policy.json").read_bytes())
            policy.update(source=runtime, controller_source=CONTROLLER)
            if malformed:
                policy.pop("source")
            policy_body = json.dumps(policy).encode()
            (controller / "policy.json").write_bytes(policy_body)
            (controller / "manifest.json").write_text(json.dumps({
                "policy.json": hashlib.sha256(policy_body).hexdigest()}))
            calls = []

            def run(arguments, *, env, cwd, check, timeout):
                calls.append((arguments, dict(env)))
                temporary = Path(env["RUNNER_TEMP"])
                if Path(arguments[1]).name == "probe.sh":
                    (temporary / "environment").write_text("SSH_AUTH_SOCK=/fixture/agent\n")
                    (temporary / "paths").write_text("/fixture/bin\n")
                elif Path(arguments[1]).name == "proof.sh":
                    evidence = Path(env["EVIDENCE_ROOT"])
                    data = original.fixtures()
                    for side, source in zip(("origin", "public"), edges):
                        data[side + ".headers"] = data[side + ".headers"].replace(original.RELEASE, source)
                    for path, value in data.items():
                        (evidence / path).write_text(value if isinstance(value, str) else json.dumps(value))
                    validator_env = dict(env, EXPECTED_VOLUME_ID="volume",
                                         EXPECTED_ENVIRONMENT_ID="environment",
                                         EXPECTED_SERVICE_ID="service", EXPECTED_POSTGRES_ID="postgres",
                                         RAILWAY_PROJECT_ID="project", OUTPUT=env["GITHUB_OUTPUT"])
                    result = REAL_RUN([sys.executable, "-I", "-S", str(ROOT / "validator.py")],
                                      env=validator_env, cwd=evidence, capture_output=True, text=True)
                    if result.returncode:
                        raise RuntimeError(result.stderr.strip())
                    (evidence / "monitor-pair-identity.json").write_text(json.dumps({
                        "paired_request_id": "d" * 64,
                        "recovery_receipt_sha256": "e" * 64,
                        "offsite_receipt_sha256": "f" * 64}))
                else:
                    raise AssertionError("Unexpected external transport")
                return subprocess.CompletedProcess(arguments, 0)

            def path(value):
                return evidence_base if value == "/evidence" else Path(value)

            credentials = {"MONITOR_" + key: "fixture" for key in monitor.SECRETS}
            with mock.patch.object(monitor, "ROOT", controller), \
                 mock.patch.object(monitor, "Path", side_effect=path), \
                 mock.patch.object(monitor.os, "umask"), \
                 mock.patch.dict(os.environ, credentials, clear=True), \
                 mock.patch.object(monitor, "admit", return_value=workflow) as admission, \
                 mock.patch.object(monitor.subprocess, "run", side_effect=run), \
                 contextlib.redirect_stdout(io.StringIO()) as stdout:
                if malformed:
                    with self.assertRaisesRegex(RuntimeError, "Invalid reviewed application"):
                        monitor.main()
                    admission.assert_not_called()
                    self.assertEqual(calls, [])
                    return
                monitor.main()
            lines = stdout.getvalue().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith("RAILWAY_RECOVERY_MONITOR_PASS "))
            proof = json.loads(lines[0].split(" ", 1)[1])
            retained = list(evidence_base.glob("*/monitor-proof.json"))
            self.assertEqual(len(retained), 1)
            self.assertEqual(json.loads(retained[0].read_bytes()), proof)
            return proof, calls

    def test_pinned_runtime_and_newer_workflow_are_separate(self):
        proof, calls = self.run_monitor()
        self.assertEqual(proof["source"], RUNTIME)
        self.assertEqual(proof["release_sha"], RUNTIME)
        self.assertEqual(proof["workflow_source"], WORKFLOW)
        self.assertEqual(proof["controller_source"], CONTROLLER)
        self.assertEqual(proof["deployment_id"], original.DEPLOYMENT)
        self.assertEqual(len(calls), 2)
        for _, env in calls:
            self.assertEqual(env["RECOVERY_SOURCE_SHA"], RUNTIME)
            self.assertEqual(env["GITHUB_SHA"], WORKFLOW)
            self.assertEqual(env["REQUESTED_SOURCE_SHA"], "")

    def test_same_runtime_and_workflow_remain_supported(self):
        proof, calls = self.run_monitor(runtime=WORKFLOW)
        self.assertEqual(proof["source"], WORKFLOW)
        self.assertEqual(proof["release_sha"], WORKFLOW)
        self.assertEqual(proof["workflow_source"], WORKFLOW)
        self.assertTrue(all(env["RECOVERY_SOURCE_SHA"] == env["GITHUB_SHA"] for _, env in calls))

    def test_two_edges_cannot_substitute_workflow_for_runtime(self):
        with self.assertRaisesRegex(RuntimeError, "origin and public production identities differ"):
            self.run_monitor(edges=(WORKFLOW, WORKFLOW))

    def test_two_edges_cannot_substitute_controller_for_runtime(self):
        with self.assertRaisesRegex(RuntimeError, "origin and public production identities differ"):
            self.run_monitor(edges=(CONTROLLER, CONTROLLER))

    def test_split_edges_are_rejected(self):
        for edges in ((RUNTIME, WORKFLOW), (WORKFLOW, RUNTIME)):
            with self.subTest(edges=edges), self.assertRaisesRegex(
                    RuntimeError, "origin and public production identities differ"):
                self.run_monitor(edges=edges)

    def test_missing_runtime_stops_before_admission_or_probe(self):
        self.run_monitor(malformed=True)

    def test_malformed_runtime_source_is_rejected(self):
        for source in (None, 7, [], {}, "", "A" * 40, "g" * 40,
                       "a" * 39, "a" * 41, RUNTIME + "\n", "refs/heads/main"):
            with self.subTest(source=source), self.assertRaisesRegex(RuntimeError, "Invalid reviewed application"):
                monitor.application_source({"source": source, "controller_source": CONTROLLER})

    def test_runtime_is_never_taken_from_controller(self):
        self.assertEqual(monitor.application_source({"source": RUNTIME, "controller_source": CONTROLLER}), RUNTIME)
        with self.assertRaisesRegex(RuntimeError, "Invalid reviewed application"):
            monitor.application_source({"controller_source": CONTROLLER})

    def admit(self, *, changed_helper=False, changed_workflow=False, changed_script=False,
              current=WORKFLOW):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            trusted = root / "trusted" / "backend" / "seiche" / "helper.py"
            trusted.parent.mkdir(parents=True)
            body = b"# reviewed helper\n"
            trusted.write_bytes(body)
            document = {"jobs": {"monitor": {"steps": [
                {"name": "proof", "run": "true", "env": {"EXPECTED": "pinned"}}]}}}
            policy = {"source": RUNTIME, "controller_source": CONTROLLER,
                      "inputs": {"backend/seiche/helper.py": hashlib.sha256(body).hexdigest()},
                      "workflow_steps_sha256": prepare.digest(prepare.selected_steps(document))}
            observed = copy.deepcopy(document)
            if changed_workflow:
                observed["jobs"]["monitor"]["steps"][0]["env"]["EXPECTED"] = "changed"
            if changed_script:
                observed["jobs"]["monitor"]["steps"][0]["run"] = "false"
            def operation(arguments, **kwargs):
                self.assertTrue(set(monitor.SECRETS).isdisjoint(kwargs["env"]))
                if arguments[:2] == ["git", "rev-parse"]:
                    return (current + "\n").encode()
                if arguments[:2] == ["git", "show"]:
                    if arguments[2] == current + ":" + prepare.WORKFLOW:
                        return json.dumps(observed).encode()
                    self.assertEqual(arguments[2], current + ":backend/seiche/helper.py")
                    return b"changed" if changed_helper else body
                return b""
            with mock.patch.object(monitor, "ROOT", root), \
                 mock.patch.object(monitor, "checked", side_effect=operation):
                return monitor.admit(root / "source", policy, monitor.public_env(root))

    def test_compatible_newer_workflow_is_admitted(self):
        self.assertEqual(self.admit(), WORKFLOW)

    def test_helper_drift_is_rejected_with_separate_runtime(self):
        with self.assertRaisesRegex(RuntimeError, "Reviewed recovery helper changed"):
            self.admit(changed_helper=True)

    def test_workflow_environment_drift_is_rejected_with_separate_runtime(self):
        with self.assertRaisesRegex(RuntimeError, "Reviewed monitor semantics changed"):
            self.admit(changed_workflow=True)

    def test_workflow_script_drift_is_rejected_with_separate_runtime(self):
        with self.assertRaisesRegex(RuntimeError, "Reviewed monitor semantics changed"):
            self.admit(changed_script=True)

    def test_malformed_current_workflow_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Invalid current source identity"):
            self.admit(current="refs/heads/main")


def role_log_record(log, receipt):
    """Carry the complete success log in one bounded, receipt-linked build record."""
    if len(log) > LOG_RECORD_LIMIT:
        raise ValueError("Detailed role log exceeds the build-record bound")
    proof = json.loads(receipt)
    if receipt != (json.dumps(proof, sort_keys=True) + "\n").encode():
        raise ValueError("Role receipt is not canonical")
    if (proof["schema"] != "seiche.recovery-monitor-role-tests.v1" or proof["status"] != "PASS" or
            type(proof["passed"]) is not int or proof["passed"] != 13 or
            type(proof["skipped"]) is not int or proof["skipped"] != 0 or
            proof["log_sha256"] != hashlib.sha256(log).hexdigest()):
        raise ValueError("Role receipt does not match the successful detailed log")
    record = {
        "schema": "seiche.recovery-monitor-role-log.v1", "encoding": "base64",
        "byte_length": len(log), "sha256": hashlib.sha256(log).hexdigest(),
        "data_base64": base64.b64encode(log).decode("ascii"),
        "role_receipt_sha256": hashlib.sha256(receipt).hexdigest(),
        "controller_source": proof["controller_source"],
        "application_source": proof["application_source"],
        "manifest_sha256": proof["manifest_sha256"],
    }
    if len((LOG_MARKER + json.dumps(record, sort_keys=True) + "\n").encode()) > LOG_RECORD_LIMIT:
        raise ValueError("Encoded role log exceeds the build-record bound")
    return record


def run_role_tests():
    """Keep the original ten-test build vertex unambiguous and retain this suite."""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MonitorRoleTests)
    inventory = [test.id() for test in suite]
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = stream.getvalue().encode()
    with (ROOT / "monitor-role-tests.log").open("xb") as output:
        output.write(log)
    policy = json.loads((ROOT / "policy.json").read_bytes())
    passed = (result.wasSuccessful() and not result.skipped and
              not result.expectedFailures and not result.unexpectedSuccesses and
              result.testsRun == len(inventory) and len(inventory) == len(set(inventory)))
    proof = {
        "schema": "seiche.recovery-monitor-role-tests.v1", "status": "PASS" if passed else "FAIL",
        "passed": result.testsRun - len(result.errors) - len(result.failures) - len(result.skipped),
        "skipped": len(result.skipped), "test_ids": inventory,
        "application_source": policy["source"], "controller_source": policy["controller_source"],
        "manifest_sha256": hashlib.sha256((ROOT / "manifest.json").read_bytes()).hexdigest(),
        "log_sha256": hashlib.sha256(log).hexdigest(),
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                          for name in ("monitor.py", "prepare.py", "test_monitor.py", "test_monitor_roles.py")},
    }
    encoded = json.dumps(proof, sort_keys=True)
    with (ROOT / "monitor-role-tests.json").open("x") as output:
        output.write(encoded + "\n")
    if not passed:
        sys.stderr.write(stream.getvalue())
        raise SystemExit("Separate monitor role checks failed")
    record = role_log_record(log, (encoded + "\n").encode())
    print("RAILWAY_RECOVERY_MONITOR_ROLE_TESTS_PASS " + encoded, flush=True)
    print(LOG_MARKER + json.dumps(record, sort_keys=True), flush=True)


if __name__ == "__main__":
    run_role_tests()
