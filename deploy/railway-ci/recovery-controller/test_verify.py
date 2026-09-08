"""Meaningful identity, locked-object and Linux privilege-boundary checks."""

import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import native_docker
import verify


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recovery, cls.storage = verify.modules()
        cls.policy = json.loads((verify.ROOT / "policy.json").read_text())
        cls.env = cls.policy["storage_target"]

    def test_attested_case_retains_exact_original_identity(self):
        receipt, size = verify.validate_case(self.policy, self.env, self.recovery, self.storage)
        self.assertEqual(receipt["request_id"], self.policy["request_id"])
        self.assertEqual(len(receipt["objects"]), 15)
        self.assertEqual(size, 1609404901)

    def test_wrong_source_deployment_or_bucket_is_rejected(self):
        for key in ("application_source", "application_deployment", "request_id"):
            policy = {**self.policy, key: "invalid"}
            with self.assertRaises(ValueError):
                verify.validate_case(policy, self.env, self.recovery, self.storage)
        with self.assertRaises(ValueError):
            verify.validate_case(self.policy, {**self.env, "S3_BUCKET": "other-bucket"}, self.recovery, self.storage)

    def test_offsite_receipt_version_and_digest_are_bounded(self):
        for key, value in (("version_id", "null"), ("sha256", "0" * 64), ("size", 512 * 1024 + 1), ("key", "other/prefix")):
            policy = copy.deepcopy(self.policy)
            policy["offsite_object"][key] = value
            with self.assertRaises(ValueError):
                verify.validate_case(policy, self.env, self.recovery, self.storage)

    def test_storage_rejects_wrong_version_key_hash_size_or_retention(self):
        now = datetime.now(timezone.utc)
        head = {"VersionId": "immutable", "ContentLength": 42, "Metadata": {"sha256": "a" * 64},
                "ObjectLockMode": "COMPLIANCE", "SSECustomerAlgorithm": "AES256", "SSECustomerKeyMD5": "fixture",
                "ObjectLockRetainUntilDate": (now + timedelta(days=90)).isoformat()}
        args = {"version": "immutable", "sha256": "a" * 64, "size": 42, "key_md5": "fixture", "now": now}
        self.storage.validate_head(head, **args)
        for key, value in (("VersionId", "newer"), ("ContentLength", 43), ("Metadata", {"sha256": "b" * 64}),
                           ("ObjectLockMode", "GOVERNANCE"), ("SSECustomerKeyMD5", "other"),
                           ("ObjectLockRetainUntilDate", (now + timedelta(days=28)).isoformat())):
            with self.assertRaises(ValueError):
                self.storage.validate_head({**head, key: value}, **args)

    def test_downloaded_bytes_must_match(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "object"
            path.write_bytes(b"original")
            self.storage.verify_download(path, sha256=verify.digest(b"original"), size=8)
            path.write_bytes(b"modified")
            with self.assertRaises(ValueError):
                self.storage.verify_download(path, sha256=verify.digest(b"original"), size=8)

    def test_native_adapter_denies_any_other_target_or_operation(self):
        prefix = ["run", "--rm", "--network", "host", "--env", "PGPASSWORD", native_docker.IMAGE]
        args = [*prefix, "psql", "--host", "127.0.0.1", "--username", "postgres", "--dbname", "postgres",
                "--set", "ON_ERROR_STOP=1", "--command", "CREATE DATABASE seiche_phase6_restore TEMPLATE template0 ENCODING 'UTF8';"]
        result = native_docker.command(args, "/tmp/check/recovery-verification/existing")
        self.assertEqual(result[0], "/usr/lib/postgresql/18/bin/psql")
        for wrong in ([*args, "--file", "/tmp/arbitrary.sql"], ["exec", "production", "sh"],
                      ["db.internal" if value == "127.0.0.1" else value for value in args]):
            with self.assertRaises(ValueError):
                native_docker.command(wrong, "/tmp/check/recovery-verification/existing")
        with self.assertRaises(ValueError):
            native_docker.command(args, "/production")

    def test_child_environment_has_no_storage_or_production_access(self):
        with mock.patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "fixture", "DATABASE_URL": "production", "GITHUB_TOKEN": "fixture"}):
            env = verify.environment(Path("/tmp/home"))
        for key in (*verify.INPUT_NAMES, *verify.INPUT_NAMES.values(), "GITHUB_TOKEN", "DATABASE_URL", "RAILWAY_TOKEN"):
            self.assertNotIn(key, env)

    @unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0, "requires actual root Linux container")
    def test_candidate_cannot_read_keys_fds_proc_or_replace_controller_receipts(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            root.chmod(0o755)
            private = root / "private"
            private.mkdir(mode=0o700)
            secret = private / "key"
            secret.write_text("adversarial-fixture-secret")
            secret.chmod(0o600)
            receipt = root / "proof"
            receipt.mkdir(mode=0o1777)
            original = receipt / "original"
            original.write_text("immutable")
            original.chmod(0o444)
            fd = os.open(secret, os.O_RDONLY)
            os.set_inheritable(fd, True)
            script = root / "attempt.py"
            script.write_text("""import os
from pathlib import Path
blocked = 0
for path in (os.environ['FIXTURE_KEY'], '/proc/' + os.environ['CONTROLLER_PID'] + '/environ'):
    try: Path(path).read_bytes()
    except PermissionError: blocked += 1
try: os.read(int(os.environ['FIXTURE_FD']), 100)
except OSError: blocked += 1
for path in (os.environ['FIXTURE_RECEIPT'], '/controller/verify.py'):
    try: Path(path).unlink()
    except PermissionError: blocked += 1
assert blocked == 5, blocked
assert 'AWS_SECRET_ACCESS_KEY' not in os.environ
assert 'NoNewPrivs:\\t1' in Path('/proc/self/status').read_text()
""")
            env = {**verify.environment(root), "FIXTURE_KEY": str(secret), "CONTROLLER_PID": str(os.getpid()),
                   "FIXTURE_FD": str(fd), "FIXTURE_RECEIPT": str(original)}
            try:
                verify.run([sys.executable, str(script)], env=env, uid=verify.RESTORE_UID)
            finally:
                os.close(fd)
            self.assertEqual(original.read_text(), "immutable")

    @unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0, "requires actual PG18 container")
    def test_actual_postgres18_isolated_bootstrap_and_native_adapter(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            root.chmod(0o755)
            pgroot = root / "postgres"
            uid = None
            try:
                uid, env = verify.start_postgres(pgroot)
                version = subprocess.check_output(["/usr/lib/postgresql/18/bin/psql", "-h", "127.0.0.1", "-U", "postgres", "-Atc", "SHOW server_version"], env=env, text=True)
                self.assertTrue(version.startswith("18."), version)
                verify.run(["/usr/lib/postgresql/18/bin/psql", "-h", "127.0.0.1", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-c", "CREATE DATABASE isolated_fixture"], env=env, uid=verify.RESTORE_UID)
            finally:
                if uid is not None:
                    verify.run(["/usr/lib/postgresql/18/bin/pg_ctl", "-D", str(pgroot / "data"), "-m", "fast", "-w", "stop"], env=env, uid=uid)
                    verify.stop_uid(uid)


if __name__ == "__main__":
    unittest.main()
