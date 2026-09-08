"""Exercise actual workflow validation against valid and adversarial evidence."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

import monitor


ROOT = Path(__file__).parent
DEPLOYMENT = "671eee65-8f3a-4bd9-b2d7-c3cac9894916"
RELEASE = "a" * 40


def fixtures():
    now = datetime.now(timezone.utc).isoformat()
    backup = {"createdAt": now, "name": "seiche-phase6-bootstrap-fixture", "expiresAt": None}
    schedules = [{"kind": kind} for kind in ("DAILY", "WEEKLY", "MONTHLY")]
    instance = {"volumeId": "volume", "environmentId": "environment", "serviceId": "service",
                "mountPath": "/var/lib/seiche-platform", "deletedAt": None,
                "isPendingDeletion": False, "sizeMB": 1000, "currentSizeMB": 100}
    health = {"generated_at": now, "version": "0.12.0", "faults": [], "provenance": [{}]}
    headers = ("x-seiche-railway-authority: production\n"
               f"x-seiche-railway-deployment: {DEPLOYMENT}\n"
               f"x-seiche-release-sha: {RELEASE}\n")
    return {
        "native-proof.json": {"data": {"environment": {"id": "environment", "projectId": "project",
              "volumeInstances": {"edges": [{"node": instance}]}},
              "volumeInstanceBackupScheduleList": schedules,
              "volumeInstanceBackupList": [copy.deepcopy(backup)]}},
        "pitr-status.json": {"service": {"id": "postgres"}, "enabled": True, "bucketWired": True,
              "blockers": [], "live": {"available": True, "backupCoverageError": None,
              "backupSetCount": 1, "archiverError": None, "archiverHealthy": True,
              "latestBackupAt": now}},
        "pitr-schedules.json": schedules,
        "pitr-backups.json": [copy.deepcopy(backup)],
        "origin.headers": headers, "public.headers": headers,
        "origin.json": health, "public.json": copy.deepcopy(health),
        "runtime.json": {"status": "ready", "mode": "production"},
    }


class MonitorTests(unittest.TestCase):
    def validate(self, evidence):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for path, value in evidence.items():
                (root / path).write_text(value if isinstance(value, str) else json.dumps(value))
            env = monitor.public_env(root)
            env.update({"EXPECTED_VOLUME_ID": "volume", "EXPECTED_ENVIRONMENT_ID": "environment",
                        "EXPECTED_SERVICE_ID": "service", "EXPECTED_POSTGRES_ID": "postgres",
                        "RAILWAY_PROJECT_ID": "project", "OUTPUT": str(root / "outputs")})
            return subprocess.run([sys.executable, "-I", "-S", str(ROOT / "validator.py")],
                                  env=env, cwd=root, capture_output=True, text=True)

    def test_original_validator_accepts_complete_fresh_proof(self):
        result = self.validate(fixtures())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_original_validator_rejects_stale_native_backup(self):
        data = fixtures()
        data["native-proof.json"]["data"]["volumeInstanceBackupList"][0]["createdAt"] = (
            datetime.now(timezone.utc) - timedelta(hours=27)).isoformat()
        result = self.validate(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("latest state volume backup is stale", result.stderr)

    def test_original_validator_rejects_unhealthy_pitr(self):
        data = fixtures()
        data["pitr-status.json"]["live"]["archiverHealthy"] = False
        result = self.validate(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PITR coverage is unhealthy", result.stderr)

    def test_original_validator_rejects_split_public_identity(self):
        data = fixtures()
        data["public.headers"] = data["public.headers"].replace(RELEASE, "b" * 40)
        result = self.validate(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("production identities differ", result.stderr)

    def test_original_validator_rejects_insufficient_headroom(self):
        data = fixtures()
        data["native-proof.json"]["data"]["environment"]["volumeInstances"]["edges"][0]["node"]["currentSizeMB"] = 801
        result = self.validate(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("twenty percent headroom", result.stderr)

    def test_git_environment_contains_no_controller_credentials(self):
        env = monitor.public_env(Path("/tmp/fixture"))
        self.assertTrue(set(monitor.SECRETS).isdisjoint(env))
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")

    def test_probe_environment_cannot_inject_code_or_tokens(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "env"
            path.write_text("SSH_AUTH_SOCK=/tmp/agent\nPYTHONPATH=/tmp/untrusted\n")
            with self.assertRaisesRegex(RuntimeError, "Unexpected probe"):
                monitor.read_env_file(path)

    def test_modified_source_helper_is_rejected_before_probe(self):
        policy = json.loads((ROOT / "policy.json").read_text())
        def operation(arguments, **kwargs):
            if arguments[:2] == ["git", "rev-parse"]:
                return RELEASE.encode() + b"\n"
            if arguments[:2] == ["git", "show"]:
                return b"raise RuntimeError('unreviewed source must never execute')\n"
            return b""
        with tempfile.TemporaryDirectory() as name, mock.patch.object(monitor, "checked", side_effect=operation):
            with self.assertRaisesRegex(RuntimeError, "Reviewed recovery helper changed"):
                monitor.admit(Path(name) / "source", policy, monitor.public_env(Path(name)))

    def test_real_pair_parser_rejects_missing_proof(self):
        sys.path.insert(0, str(ROOT / "trusted" / "backend"))
        from seiche.stateful_control import ControlContractError, extract_latest_recovery_pair
        with self.assertRaises(ControlContractError):
            extract_latest_recovery_pair(b"", expected_commit=RELEASE,
                                         expected_deployment_id=DEPLOYMENT,
                                         now=datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
