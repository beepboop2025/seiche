"""Dry production-stage admission and real Linux restore-wrapper boundaries."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import attest
import recurring
import verify


class ExportReached(Exception):
    """Fixture sentinel stops before the first real production stage can run."""


class SourceRegistryTests(unittest.TestCase):
    def test_current_source_requires_the_same_control_registry(self):
        registry = "governance/railway-control-signers.json"
        contents = {"backend/seiche/example.py": b"pass\n", registry: b"reviewed signers\n"}
        policy = {"trusted_source_sha256": {name: verify.digest(body) for name, body in contents.items()}}
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            repository = root / "repository"
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            for path, body in contents.items():
                target = repository / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)

            def commit():
                subprocess.run(["git", "-C", str(repository), "add", "-A"], check=True)
                subprocess.run(["git", "-C", str(repository), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], check=True)

            original_run = subprocess.run

            def local_fetch(arguments, **kwargs):
                if "fetch" in arguments:
                    arguments = [str(repository) if arg.startswith("https://github.com/") else "HEAD" if arg == "refs/heads/main" else arg
                                 for arg in arguments]
                return original_run(arguments, **kwargs)

            commit()
            with mock.patch.object(recurring.subprocess, "run", side_effect=local_fetch):
                self.assertRegex(recurring.source_identity(policy, root), r"^[0-9a-f]{40}$")
                (repository / registry).write_bytes(b"unreviewed signers\n")
                commit()
                with self.assertRaisesRegex(ValueError, "reviewed recovery source changed"):
                    recurring.source_identity(policy, root)
                (repository / registry).unlink()
                commit()
                with self.assertRaisesRegex(ValueError, "input path set changed"):
                    recurring.source_identity(policy, root)


class NativeAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="recurring-admission-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.controller = self.root / "controller"
        self.controller.mkdir()
        self.uuid = "11111111-1111-4111-8111-111111111111"
        self.source = "a" * 40
        self.trace = []
        target = {"RAILWAY_PROJECT_ID": self.uuid, "RAILWAY_ENVIRONMENT_ID": self.uuid,
                  "RAILWAY_STATEFUL_SERVICE_ID": self.uuid, "RAILWAY_STATEFUL_VOLUME_ID": self.uuid,
                  "RAILWAY_STATEFUL_ORIGIN": attest.ORIGIN}
        self.policy = {"operation": "export-recurring", "controller_source": "c" * 40,
                       "controller_project_id": self.uuid, "controller_environment_id": self.uuid,
                       "controller_service_id": self.uuid, "execution_public_key": "e" * 64,
                       "production_target": target, "trusted_source_sha256": {},
                       "storage_target": {"S3_ENDPOINT": "https://fixture.invalid", "S3_BUCKET": "fixture-bucket",
                                          "S3_PREFIX": "fixture/recovery", "AWS_DEFAULT_REGION": "fixture"}}
        self.write_policy()
        environment = {"RECOVERY_OPERATION": "export-recurring", "RECOVERY_CONFIRMATION": "EXPORT_WITHOUT_AUTHORITY_CHANGE",
                       "RECOVERY_CONTROLLER_IMAGE_DIGEST": "sha256:" + "f" * 64,
                       "RECOVERY_CONTROL_SIGNING_KEY_PEM": "fixture-control-key", "RECOVERY_EXECUTION_SIGNING_KEY_PEM": "fixture-evidence-key"}
        environment.update({"RAILWAY_" + field + "_ID": self.uuid for field in ("PROJECT", "ENVIRONMENT", "SERVICE", "DEPLOYMENT", "REPLICA")})
        environment.update({variable: self.policy["storage_target"].get(name, "fixture-storage") for name, variable in verify.INPUT_NAMES.items()})
        environment.update({"MONITOR_" + name: "fixture-monitor" for name in recurring.MONITOR_NAMES})
        self.patch(mock.patch.dict(os.environ, environment, clear=True))
        old_umask = os.umask(0o077)
        self.addCleanup(os.umask, old_umask)
        self.patch(mock.patch.object(recurring.os, "geteuid", return_value=0))
        self.patch(mock.patch.object(recurring, "ROOT", self.controller))
        self.patch(mock.patch.object(recurring, "TRUSTED", self.controller / "trusted"))
        self.patch(mock.patch.object(recurring, "Path", side_effect=lambda path: self.root / "evidence" if str(path) == "/evidence/native" else Path(path)))
        self.keys = self.patch(mock.patch.object(recurring, "validate_signers", side_effect=lambda *args: self.trace.append("keys")))
        self.source_check = self.patch(mock.patch.object(recurring, "source_identity", side_effect=lambda *args: self.trace.append("source") or self.source))
        self.monitor = self.patch(mock.patch.object(recurring, "monitor", side_effect=lambda *args: self.trace.append("monitor") or {"deployment_id": self.uuid, "release_sha": self.source}))
        self.runtime = {"deployment_id": self.uuid, "source": self.source, "replica_id": self.uuid, "status": "RUNNING", "observed_at": datetime.now(timezone.utc).isoformat()}
        self.live = self.patch(mock.patch.object(attest, "live_runtime", side_effect=lambda *args: self.trace.append("runtime") or dict(self.runtime)))
        self.storage_module = types.SimpleNamespace(validate_location=mock.Mock(side_effect=lambda env: self.trace.append("location")))
        self.patch(mock.patch.object(verify, "modules", return_value=(object(), self.storage_module)))
        self.bucket = self.patch(mock.patch.object(verify, "run", side_effect=self.probe_bucket))
        self.store = types.SimpleNamespace(head=mock.Mock())
        self.patch(mock.patch.object(attest, "Storage", return_value=self.store))
        self.index = self.patch(mock.patch.object(attest, "fetch_index", side_effect=lambda *args, **kw: self.trace.append("index") or None))
        self.download = self.patch(mock.patch.object(attest, "download_receipts", return_value=({}, {}, {})))
        self.events = self.patch(mock.patch.object(verify, "event"))
        self.stage = self.patch(mock.patch.object(recurring, "run_original_stage", side_effect=self.stop_at_export))

    def patch(self, patch):
        value = patch.start()
        self.addCleanup(patch.stop)
        return value

    def write_policy(self):
        body = verify.canonical(self.policy)
        (self.controller / "policy.json").write_bytes(body)
        (self.controller / "manifest.json").write_bytes(verify.canonical({"policy.json": verify.digest(body)}))

    def probe_bucket(self, arguments, **kwargs):
        self.assertEqual(arguments[2], "probe-bucket")
        self.trace.append("bucket")

    def stop_at_export(self, name, environment, timeout):
        self.assertEqual(name, "export-native.sh")
        self.trace.append("export")
        self.assertEqual(environment["NATIVE_INVOCATION"], "railway")
        self.assertNotIn("RECOVERY_EXECUTION_SIGNING_KEY_PEM", environment)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", environment)
        self.assertEqual(environment["SEICHE_RAILWAY_RECOVERY_SIGNING_KEY_PEM"], "fixture-control-key")
        raise ExportReached()

    def test_absence_admits_first_export_only_after_all_original_preflights(self):
        with self.assertRaises(ExportReached):
            recurring.main()
        self.assertEqual(self.trace, ["keys", "source", "monitor", "location", "bucket", "runtime", "index", "export"])
        self.assertTrue(self.index.call_args.kwargs["allow_missing"])
        self.stage.assert_called_once()

    def test_invalid_key_stops_before_any_source_monitor_storage_or_production_stage(self):
        self.keys.side_effect = ValueError("wrong registered or evidence key")
        with self.assertRaises(ValueError):
            recurring.main()
        self.source_check.assert_not_called()
        self.monitor.assert_not_called()
        self.bucket.assert_not_called()
        self.stage.assert_not_called()

    def test_failed_strict_monitor_never_reaches_bucket_or_export(self):
        self.monitor.side_effect = ValueError("strict monitor rejected")
        with self.assertRaises(ValueError):
            recurring.main()
        self.bucket.assert_not_called()
        self.stage.assert_not_called()

    def test_source_inventory_rejection_never_reaches_monitor_storage_or_export(self):
        self.source_check.side_effect = ValueError("unreviewed current source input")
        with self.assertRaises(ValueError):
            recurring.main()
        self.monitor.assert_not_called()
        self.bucket.assert_not_called()
        self.stage.assert_not_called()

    def test_bad_bucket_preflight_never_uses_absent_index_to_authorize_export(self):
        self.bucket.side_effect = ValueError("bucket lock configuration rejected")
        with self.assertRaises(ValueError):
            recurring.main()
        self.index.assert_not_called()
        self.stage.assert_not_called()

    def test_runtime_movement_after_monitor_never_reaches_index_or_export(self):
        self.live.side_effect = lambda *args: {**self.runtime, "source": "b" * 40}
        with self.assertRaises(ValueError):
            recurring.main()
        self.index.assert_not_called()
        self.stage.assert_not_called()

    def test_access_or_malformed_index_never_becomes_permission_for_a_new_export(self):
        self.index.side_effect = ValueError("index unreadable or malformed")
        with self.assertRaises(ValueError):
            recurring.main()
        self.stage.assert_not_called()

    def test_valid_existing_index_never_runs_export_and_requires_after_checks(self):
        reference = {"key": "fixture/index", "version_id": "fixture-v1"}
        payload = {"controller_deployment_id": self.uuid, "date": "2026-09-08", "snapshot_id": "20260908T150000Z"}
        self.index.side_effect = None
        self.index.return_value = ({"fixture": True}, reference, {})
        self.store.head.return_value = (reference, {})
        validator = self.patch(mock.patch.object(verify, "validate_execution_index", return_value=payload))
        recurring.main()
        self.stage.assert_not_called()
        self.download.assert_called_once()
        self.assertEqual(self.live.call_count, 2)
        self.assertEqual(validator.call_count, 2)
        self.store.head.assert_called_once_with(reference["key"])
        self.assertEqual(self.events.call_args.args[0], "RAILWAY_NATIVE_RECOVERY_ALREADY_VERIFIED")

    def test_existing_index_cannot_hide_production_change_during_receipt_reads(self):
        self.index.side_effect = None
        self.index.return_value = ({"fixture": True}, {"key": "fixture/index", "version_id": "v1"}, {})
        self.patch(mock.patch.object(verify, "validate_execution_index", return_value={"controller_deployment_id": self.uuid}))
        self.live.side_effect = [dict(self.runtime), {**self.runtime, "replica_id": "22222222-2222-4222-8222-222222222222"}]
        with self.assertRaises(ValueError):
            recurring.main()
        self.stage.assert_not_called()
        self.events.assert_not_called()

    def test_existing_index_version_change_cannot_claim_verified_or_start_an_export(self):
        self.index.side_effect = None
        self.index.return_value = ({"fixture": True}, {"key": "fixture/index", "version_id": "v1"}, {})
        self.patch(mock.patch.object(verify, "validate_execution_index", return_value={"controller_deployment_id": self.uuid}))
        self.store.head.return_value = ({"key": "fixture/index", "version_id": "v2"}, {})
        with self.assertRaises(ValueError):
            recurring.main()
        self.stage.assert_not_called()
        self.events.assert_not_called()


class RestoreWrapperTests(unittest.TestCase):
    def test_workspace_parent_is_traversable_under_the_real_private_umask(self):
        with tempfile.TemporaryDirectory() as name:
            before = os.umask(0o077)
            try:
                work = recurring.restore_workspace(Path(name))
            finally:
                os.umask(before)
            self.assertEqual(stat.S_IMODE(work.parent.stat().st_mode), 0o755)
            self.assertFalse(work.exists())

    @unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0 and Path("/controller/restore-native.sh").exists(),
                         "requires the assembled native recovery image")
    def test_actual_image_inputs_are_readable_but_not_writable_by_restore_uid(self):
        # Exercise COPY's actual modes, not a separately chmod'ed fixture tree.
        script = """
import hashlib,json,os,stat,sys
from pathlib import Path
root=Path('/controller')
assert os.geteuid()==65532
manifest=json.loads((root/'manifest.json').read_bytes())
for name,expected in manifest.items():
    path=root/name
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected, name
    assert path.stat().st_uid==0 and not os.access(path,os.W_OK), name
    assert all(not os.access(parent,os.W_OK) for parent in path.parents), name
sys.path.insert(0,str(root/'trusted/backend'))
from seiche import stateful_migration,stateful_recovery
assert os.access(root/'native-bin/docker',os.X_OK)
try:
    (root/'restore-native.sh').open('ab')
except PermissionError:
    pass
else:
    raise AssertionError('restore UID can modify its packaged script')
"""
        environment = verify.environment(Path("/tmp"))
        verify.run([sys.executable, "-I", "-B", "-c", script], env=environment, uid=verify.RESTORE_UID)
        verify.run(["bash", "-n", "/controller/restore-native.sh"], env=environment, uid=verify.RESTORE_UID)

    @unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0 and Path("/usr/lib/postgresql/18/bin/initdb").exists(),
                         "requires isolated Linux root and PostgreSQL18 CI image")
    def test_real_pg18_child_is_unprivileged_private_inputs_stay_sealed_and_server_stops(self):
        old_umask = os.umask(0o077)
        self.addCleanup(os.umask, old_umask)
        with tempfile.TemporaryDirectory(prefix="native-wrapper-test-") as name:
            root = Path(name)
            root.chmod(0o755)
            private, public, controller = root / "private", root / "public", root / "controller"
            private.mkdir(mode=0o700)
            public.mkdir(mode=0o755)
            public.chmod(0o755)
            controller.mkdir(mode=0o755)
            controller.chmod(0o755)
            (private / "fixture-key").write_text("never visible to restore UID")
            work = recurring.restore_workspace(public)
            (work / "proof").mkdir(mode=0o700, parents=True)
            original = work / "recovery-receipt.json"
            original.write_text('{"fixture":true}\n')
            (controller / "restore-native.sh").write_text("""#!/usr/bin/env bash
set -euo pipefail
test "$(id -u)" -eq 65532
/usr/lib/postgresql/18/bin/psql -h 127.0.0.1 -U postgres -v ON_ERROR_STOP=1 -c 'CREATE DATABASE wrapper_fixture'
python -I -S - <<'PYCODE'
import json,os
from pathlib import Path
for key in ['RECOVERY_CONTROL_SIGNING_KEY_PEM','RECOVERY_EXECUTION_SIGNING_KEY_PEM','AWS_SECRET_ACCESS_KEY','RAILWAY_TOKEN']:
    assert key not in os.environ
root=Path(os.environ['EVIDENCE_ROOT'])
try:
    (root.parents[2]/'private/fixture-key').read_text()
except PermissionError:
    pass
else:
    raise AssertionError('private key accessible')
try:
    (root/'recovery-receipt.json').write_text('tamper')
except PermissionError:
    pass
else:
    raise AssertionError('immutable input writable')
(root/'proof/reverse-restore.json').write_text(json.dumps({'fixture':True,'uid':os.geteuid()}))
PYCODE
""")
            (controller / "restore-native.sh").chmod(0o644)
            with mock.patch.object(recurring, "ROOT", controller), mock.patch.object(recurring, "TRUSTED", controller):
                result = recurring.original_restore(work, private, public, {"snapshot_id": "20260908T150000Z", "release_sha": "a" * 40}, timeout=60)
            self.assertEqual(result, {"fixture": True, "uid": 65532})
            self.assertEqual(original.read_text(), '{"fixture":true}\n')
            proof = work / "proof/reverse-restore.json"
            self.assertEqual((proof.stat().st_uid, stat.S_IMODE(proof.stat().st_mode)), (0, 0o444))
            self.assertFalse((public / "postgres/data/postmaster.pid").exists())


if __name__ == "__main__":
    unittest.main()
