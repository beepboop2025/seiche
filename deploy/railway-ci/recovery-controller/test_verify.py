"""Meaningful identity, locked-object and Linux privilege-boundary checks."""

import copy
import base64
from datetime import datetime, timedelta, timezone
import json
import hashlib
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
        # Includes the six receipt/proof members in addition to the bundle.
        self.assertEqual(size, 1609428065)

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

    @unittest.skipUnless(sys.platform == "linux", "uses original Linux storage helper")
    def test_original_storage_helper_downloads_only_into_private_parent(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            work, binary = root / "work", root / "bin"
            work.mkdir(mode=0o700)
            binary.mkdir(mode=0o700)
            (work / "proof/resume-offsite-heads").mkdir(parents=True, mode=0o700)
            payload = b'{"fixture":true}\n'
            key = b'F' * 32
            head = {"VersionId": "fixture-v1", "ContentLength": len(payload), "Metadata": {"sha256": verify.digest(payload)},
                    "ObjectLockMode": "COMPLIANCE", "SSECustomerAlgorithm": "AES256",
                    "SSECustomerKeyMD5": base64.b64encode(hashlib.md5(key, usedforsecurity=False).digest()).decode(),
                    "ObjectLockRetainUntilDate": (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()}
            shim = binary / "aws"
            shim.write_text("#!/usr/local/bin/python\nimport sys,json\nfrom pathlib import Path\n"
                            "args=sys.argv[1:]\n"
                            "if args == ['--version']: print('aws-cli/2.36.35 fixture')\n"
                            "elif 'head-object' in args: print(" + repr(json.dumps(head)) + ")\n"
                            "elif 'get-object' in args: Path(args[-1]).write_bytes(" + repr(payload) + ")\n"
                            "else: raise SystemExit('unexpected fake-provider operation')\n")
            shim.chmod(0o700)
            env = {**verify.environment(root), "PATH": str(binary) + ":" + verify.environment(root)["PATH"],
                   "AWS_ACCESS_KEY_ID": "fixture", "AWS_SECRET_ACCESS_KEY": "fixture", "AWS_DEFAULT_REGION": "fixture",
                   "S3_ENDPOINT": "https://storage.invalid", "S3_BUCKET": "fixture-bucket", "S3_PREFIX": "fixture",
                   "S3_SSE_C_KEY_B64": base64.b64encode(key).decode(), "RUNNER_TEMP": str(root), "GITHUB_WORKSPACE": str(verify.TRUSTED)}
            item = {"key": "fixture/offsite-receipt.json", "version_id": "fixture-v1", "size": len(payload), "sha256": verify.digest(payload)}
            self.storage.download_object(work, "offsite-receipt.json", item, env)
            self.assertEqual((work / "offsite-receipt.json").read_bytes(), payload)
            (work / "offsite-receipt.json").unlink()
            work.chmod(0o755)
            with self.assertRaises(subprocess.CalledProcessError):
                self.storage.download_object(work, "offsite-receipt.json", item, env)

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

    def test_proof_tree_rejects_symlinks_hardlinks_unexpected_or_oversized_output(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "resume-offsite-heads").mkdir()
            for path in ("reverse-restore.json", "railway-reverse-restore.json", "resume-offsite-heads/offsite-receipt.json.json"):
                (root / path).write_text("{}")
            verify.validate_proof_tree(root, ())
            output = root / "railway-reverse-restore.json"
            for form in ("symlink", "hardlink", "oversize"):
                output.unlink()
                if form == "symlink":
                    output.symlink_to(root / "reverse-restore.json")
                elif form == "hardlink":
                    os.link(root / "reverse-restore.json", output)
                else:
                    output.write_bytes(b"x" * (512 * 1024 + 1))
                with self.assertRaises(ValueError):
                    verify.validate_proof_tree(root, ())
            output.unlink()
            output.write_text("{}")
            (root / "unexpected").write_text("{}")
            with self.assertRaises(ValueError):
                verify.validate_proof_tree(root, ())

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



class NativeOrchestrationTests(unittest.TestCase):
    def test_sealed_restore_rejects_links_and_unexpected_output(self):
        import recurring
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "proof").mkdir()
            item = root / "input"
            item.write_text("immutable")
            link = root / "escape"
            link.symlink_to(item)
            with self.assertRaises(ValueError):
                recurring.seal_restore_inputs(root)
            link.unlink()
            os.link(item, link)
            with self.assertRaises(ValueError):
                recurring.seal_restore_inputs(root)
            link.unlink()
            recurring.seal_restore_inputs(root)
            self.assertEqual(item.stat().st_mode & 0o777, 0o444)
            before = {str(p.relative_to(root)) for p in root.rglob("*")}
            (root / "proof/reverse-restore.json").write_text("{}")
            (root / "proof/unexpected").write_text("not admitted")
            with self.assertRaises(ValueError):
                recurring.accepted_restore_output(root, before)

    @unittest.skipUnless(sys.platform == "linux", "requires original Linux storage helper")
    def test_actual_storage_constructor_and_nonexecutably_copied_bucket_helper(self):
        import attest
        import recurring
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            root.chmod(0o700)
            binary = root / "bin"
            binary.mkdir(mode=0o700)
            provider = binary / "aws"
            provider.write_text("#!/usr/local/bin/python\nimport sys,json\n"
                                "args=sys.argv[1:]\n"
                                "if args == ['--version']: print('aws-cli/2.36.35 fixture')\n"
                                "elif 'get-object-lock-configuration' in args: print(json.dumps({'ObjectLockConfiguration': {'ObjectLockEnabled':'Enabled','Rule':{'DefaultRetention':{'Mode':'COMPLIANCE','Days':90}}}}))\n"
                                "elif 'get-bucket-versioning' in args: print(json.dumps({'Status':'Enabled'}))\n"
                                "else: raise SystemExit('unexpected fixture provider operation')\n")
            provider.chmod(0o700)
            environment = {**verify.environment(root), "PATH": str(binary) + ":" + verify.environment(root)["PATH"],
                           "AWS_ACCESS_KEY_ID": "fixture", "AWS_SECRET_ACCESS_KEY": "fixture", "AWS_DEFAULT_REGION": "fixture",
                           "S3_ENDPOINT": "https://fixture.invalid", "S3_BUCKET": "fixture-bucket", "S3_PREFIX": "fixture",
                           "S3_SSE_C_KEY_B64": base64.b64encode(b'F' * 32).decode(), "RUNNER_TEMP": str(root),
                           "GITHUB_WORKSPACE": str(verify.TRUSTED)}
            original = verify.modules()[1]
            fetch_root = root / "index-fetch"
            fetch_root.mkdir(mode=0o700)
            store = attest.Storage(fetch_root, environment, original)
            self.assertEqual(store.key_path.stat().st_mode & 0o777, 0o600)
            helper = root / "copied-helper.sh"
            helper.write_bytes((verify.TRUSTED / "ops/deploy/seiche-s3-object-lock.sh").read_bytes())
            helper.chmod(0o644)
            output = root / "bucket.json"
            verify.run(["bash", str(helper), "probe-bucket", str(output)], env=environment)
            self.assertEqual(json.loads(output.read_text())["default_days"], 90)


class ExecutionIndexTests(unittest.TestCase):
    def setUp(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()  # Ephemeral fixture, never provisioned.
        self.pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        self.now = datetime(2026, 9, 8, 16, 0, tzinfo=timezone.utc)
        uuid = "11111111-1111-4111-8111-111111111111"
        self.policy = {"execution_public_key": public, "controller_source": "c" * 40,
                       "controller_manifest_sha256": "d" * 64, "controller_image_digest": "sha256:" + "e" * 64,
                       "storage_prefix": "seiche/recovery/v1", "storage_bucket": "fixture-bucket",
                       "storage_endpoint": "https://fixture.invalid"}
        self.policy.update({role + "_" + field + "_id": uuid for role in ("controller", "application") for field in ("project", "environment", "service")})
        self.runtime = {"deployment_id": uuid, "source": "a" * 40, "replica_id": uuid, "status": "RUNNING", "observed_at": self.now.isoformat()}
        prefix = "seiche/recovery/v1/20260908T150000Z/" + "b" * 64
        self.payload = {"schema": verify.INDEX_SCHEMA, "execution_platform": "railway", "date": "2026-09-08",
                        "observed_at": self.now.isoformat(), "repository": "beepboop2025/seiche",
                        "governance_workflow": "beepboop2025/seiche/.github/workflows/railway-stateful-recovery.yml",
                        "controller_source": "c" * 40, "controller_manifest_sha256": "d" * 64,
                        "controller_deployment_id": uuid, "controller_replica_id": uuid,
                        "monitor_proof_sha256": "e" * 64, "application_deployment_id": uuid,
                        "application_source": "a" * 40, "application_replica_id": uuid,
                        "request_id": "b" * 64, "snapshot_id": "20260908T150000Z", "objects_verified": 15,
                        "postgres_counts": [11, 22, 33, 44], "postgres_count_floor": [10, 20, 30, 40],
                        "authority_changed": False, "research_only": True, "can_publish": False, "can_execute": False}
        self.payload.update({name: value for name, value in self.policy.items() if name.endswith("_id") or name.startswith("storage_") or name == "controller_image_digest"})
        self.payload.update({label: {"key": prefix + "/" + name, "version_id": "fixture-v1", "sha256": "f" * 64, "size": 123}
                             for label, name in verify.INDEX_OBJECTS.items()})

    def signed(self, payload=None):
        return verify.sign_execution_index(payload or self.payload, self.pem, self.policy["execution_public_key"])

    def validate(self, envelope, **changes):
        return verify.validate_execution_index(envelope, policy=changes.get("policy", self.policy),
                                              current_runtime=changes.get("runtime", self.runtime), now=changes.get("now", self.now))

    def test_valid_native_index_preserves_original_15_member_contract(self):
        self.assertEqual(self.validate(self.signed())["objects_verified"], 15)
        self.assertTrue(verify.index_signing_bytes(self.payload).startswith(verify.INDEX_DOMAIN))

    def test_tampered_index_or_wrong_evidence_key_fails(self):
        from cryptography.exceptions import InvalidSignature
        signed = self.signed()
        signed["payload"] = {**signed["payload"], "controller_source": "f" * 40}
        with self.assertRaises(InvalidSignature):
            self.validate(signed)
        with self.assertRaises(ValueError):
            self.validate(self.signed(), policy={**self.policy, "execution_public_key": "f" * 64})

    def test_even_validly_signed_wrong_day_source_target_or_safety_state_fails(self):
        cases = (("date", "2026-09-07"), ("controller_source", "f" * 40),
                 ("controller_manifest_sha256", "f" * 64), ("controller_service_id", "other-service"),
                 ("application_deployment_id", "22222222-2222-4222-8222-222222222222"),
                 ("application_replica_id", "22222222-2222-4222-8222-222222222222"),
                 ("objects_verified", 16), ("authority_changed", True), ("can_publish", True))
        for name, value in cases:
            with self.subTest(field=name), self.assertRaises(ValueError):
                self.validate(self.signed({**self.payload, name: value}))

    def test_stale_future_or_stopped_runtime_cannot_attest(self):
        for change in ({"status": "STOPPED"}, {"observed_at": (self.now - timedelta(minutes=6)).isoformat()}):
            with self.assertRaises(ValueError):
                self.validate(self.signed(), runtime={**self.runtime, **change})
        for observed in (self.now - timedelta(hours=27), self.now + timedelta(minutes=3)):
            with self.assertRaises(ValueError):
                self.validate(self.signed({**self.payload, "observed_at": observed.isoformat()}))

    def test_sender_offset_cannot_relabel_a_prior_utc_day(self):
        with self.assertRaises(ValueError):
            self.validate(self.signed({**self.payload, "observed_at": "2026-09-08T04:01:00+14:00"}))
        for snapshot in ("20260931T150000Z", "20260908T250000Z", "20260907T150000Z"):
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                self.validate(self.signed({**self.payload, "snapshot_id": snapshot}))

    def test_receipt_references_and_restore_floors_fail_closed(self):
        for name, value in (("version_id", "null"), ("key", "unrelated/receipt.json"), ("sha256", "invalid"), ("size", 524289)):
            payload = copy.deepcopy(self.payload)
            payload["offsite_receipt"][name] = value
            with self.subTest(field=name), self.assertRaises(ValueError):
                self.validate(self.signed(payload))
        with self.assertRaises(ValueError):
            self.validate(self.signed({**self.payload, "postgres_counts": [1, 2, 3, 4]}))

    def test_original_receipt_cross_binding_rejects_forged_counts_versions_and_bodies(self):
        import types
        # A minimal receipt fixture isolates cross-binding from the separately pinned original validator.
        original = {"snapshot": {"id": self.payload["snapshot_id"], "critical_table_count_floor": self.payload["postgres_count_floor"]},
                    "railway": {field + "_id": self.payload["application_" + field + "_id"] for field in ("project", "environment", "service", "deployment")},
                    "request_id": self.payload["request_id"], "commit": self.payload["application_source"],
                    "filesystem": {"tree_sha256": {"api": "f" * 64}, "nbs_full_store_audit_result": "not_onboarded"},
                    "palimpsest_china_state": {"fixture": True}}
        proof = {"schema": "seiche.railway-reverse-restore-proof.v1", "repository": self.payload["repository"],
                 "workflow": self.payload["governance_workflow"], "commit": self.payload["application_source"],
                 "request_id": self.payload["request_id"], "authority_changed": False, "research_only": True,
                 "can_publish": False, "can_execute": False, "postgres_counts": self.payload["postgres_counts"],
                 "postgres_count_floor": self.payload["postgres_count_floor"], "filesystem_tree_sha256": original["filesystem"]["tree_sha256"],
                 "nbs_full_store_audit_result": "not_onboarded", "palimpsest_china_state": original["palimpsest_china_state"],
                 "recovery_receipt_sha256": verify.digest(verify.canonical(original))}
        bodies = {"recovery_receipt": verify.canonical(original), "reverse_restore_proof": verify.canonical(proof)}
        payload = copy.deepcopy(self.payload)
        for name, body in bodies.items():
            payload[name].update(sha256=verify.digest(body), size=len(body))
        offsite = {"bucket": self.policy["storage_bucket"], "prefix": self.policy["storage_prefix"],
                   "objects": {**{str(i): {} for i in range(13)},
                               **{verify.INDEX_OBJECTS[name]: payload[name] for name in bodies}},
                   "recovery_receipt_sha256": payload["recovery_receipt"]["sha256"],
                   "reverse_restore_proof_sha256": payload["reverse_restore_proof"]["sha256"]}
        bodies["offsite_receipt"] = verify.canonical(offsite)
        payload["offsite_receipt"].update(sha256=verify.digest(bodies["offsite_receipt"]), size=len(bodies["offsite_receipt"]))
        heads = {name: {"VersionId": payload[name]["version_id"], "ContentLength": len(body),
                        "Metadata": {"sha256": verify.digest(body)}, "ObjectLockMode": "COMPLIANCE",
                        "SSECustomerAlgorithm": "AES256", "ObjectLockRetainUntilDate": (self.now + timedelta(days=90)).isoformat()}
                 for name, body in bodies.items()}
        calls = []
        original_validator = types.SimpleNamespace(validate_offsite_receipt=lambda *a, **kw: calls.append((a, kw)))
        def check(candidate, **kw):
            return verify.validate_index_receipts(candidate, bodies=kw.get("bodies", bodies), heads=kw.get("heads", heads),
                                                  recovery=original_validator, policy=self.policy, now=self.now)
        check(payload)
        self.assertEqual(len(calls), 1)
        with self.assertRaises(ValueError):
            check({**payload, "postgres_counts": [0] * 4, "postgres_count_floor": [0] * 4})
        bad = copy.deepcopy(payload)
        bad["recovery_receipt"]["version_id"] = "different-version"
        with self.assertRaises(ValueError):
            check(bad)
        with self.assertRaises(ValueError):
            check(payload, bodies={**bodies, "reverse_restore_proof": b"{}"})
        bad_heads = copy.deepcopy(heads)
        bad_heads["offsite_receipt"]["ObjectLockMode"] = "GOVERNANCE"
        with self.assertRaises(ValueError):
            check(payload, heads=bad_heads)

    def test_production_control_public_key_is_never_an_evidence_key(self):
        public = "9acdfc2b5c1852fb47608912183b5d28d943da126e3c448b66bbd46c7c31a844"
        signed = self.signed()
        signed["key_id"] = verify.digest(bytes.fromhex(public))
        with self.assertRaises(ValueError):
            self.validate(signed, policy={**self.policy, "execution_public_key": public})

if __name__ == "__main__":
    unittest.main()
