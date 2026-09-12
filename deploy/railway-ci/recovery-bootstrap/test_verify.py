"""Reject mismatched or expired durable anchors before a new export is possible."""

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("durable_bootstrap", Path(__file__).with_name("verify.py"))
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
        self.source = "a" * 40
        self.deployment = "11111111-1111-4111-8111-111111111111"
        self.policy = {"schema": "seiche.durable-recovery-bootstrap.v1", "source": self.source,
                       "deployment": self.deployment, "created_at": self.now.isoformat(),
                       "expires_at": (self.now + timedelta(hours=1)).isoformat(), "run_id": 123,
                       "run_attempt": 2, "offsite": {"key": "fixture/20260910T120000Z/" + "b" * 64 + "/offsite-receipt.json",
                       "version_id": "immutable", "sha256": "c" * 64, "size": 1234}}

    def validate(self, value):
        body = json.dumps(value).encode()
        return bootstrap.manifest(body, bootstrap.digest(body), source=self.source,
                                  deployment=self.deployment, prefix="fixture", now=self.now)

    def test_current_reviewed_manifest_accepts_exact_immutable_reference(self):
        self.assertEqual(self.validate(self.policy), self.policy)

    def test_digest_and_duplicate_fields_fail_before_storage(self):
        with self.assertRaisesRegex(ValueError, "digest differs"):
            bootstrap.manifest(b"{}", "0" * 64, source=self.source, deployment=self.deployment,
                               prefix="fixture", now=self.now)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            bootstrap.document(b'{"source":"one","source":"two"}')

    def test_wrong_source_deployment_or_extra_field_cannot_anchor_export(self):
        for update in ({"source": "d" * 40}, {"deployment": "22222222-2222-4222-8222-222222222222"},
                       {"can_execute": True}, {"run_id": True}, {"run_attempt": 0}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.validate({**self.policy, **update})

    def test_expired_future_or_unbounded_review_window_is_rejected(self):
        for update in ({"expires_at": self.now.isoformat()},
                       {"created_at": (self.now + timedelta(seconds=1)).isoformat()},
                       {"expires_at": (self.now + timedelta(hours=5)).isoformat()},
                       {"expires_at": "2026-09-12T13:00:00"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.validate({**self.policy, **update})

    def test_prefix_version_digest_and_size_are_closed(self):
        for update in ({"key": "other/receipt.json"}, {"key": "fixture/../receipt.json"},
                       {"version_id": "null"}, {"version_id": "bad\nversion"},
                       {"sha256": "f" * 63}, {"size": True}, {"size": bootstrap.MAX_BYTES + 1}):
            value = copy.deepcopy(self.policy)
            value["offsite"].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.validate(value)

    def verified(self):
        invocation = "https://github.com/beepboop2025/seiche/actions/runs/123/attempts/2"
        certificate = {"issuer": "https://token.actions.githubusercontent.com",
                       "subjectAlternativeName": f"https://github.com/{bootstrap.REPOSITORY}/{bootstrap.WORKFLOW}@refs/heads/main",
                       "runnerEnvironment": "github-hosted", "sourceRepositoryDigest": self.source,
                       "sourceRepositoryURI": "https://github.com/beepboop2025/seiche",
                       "sourceRepositoryRef": "refs/heads/main", "runInvocationURI": invocation}
        statement = {"subject": [{"name": "offsite-receipt.json", "digest": {"sha256": "c" * 64}}],
                     "predicate": {"runDetails": {"metadata": {"invocationId": invocation}}}}
        return [{"verificationResult": {"signature": {"certificate": certificate}, "statement": statement}}]

    def attest(self, result):
        bootstrap.attestation_identity(result, name="offsite-receipt.json", sha256="c" * 64, policy=self.policy)

    def test_attestation_must_bind_exact_subject_source_workflow_and_invocation(self):
        self.attest(self.verified())
        for field in ("issuer", "subjectAlternativeName", "sourceRepositoryDigest", "sourceRepositoryURI",
                      "sourceRepositoryRef", "runInvocationURI", "runnerEnvironment"):
            result = self.verified()
            result[0]["verificationResult"]["signature"]["certificate"][field] = "different"
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.attest(result)
        for field in ("subject", "predicate"):
            result = self.verified()
            result[0]["verificationResult"]["statement"][field] = [] if field == "subject" else {}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.attest(result)

    def test_wrong_locked_version_releases_no_activation_output(self):
        class WrongVersion:
            def head(inner, key, version):
                return {**self.policy["offsite"], "version_id": "another-version"}, {}
            def fetch(inner, *args):
                raise AssertionError("mismatched version reached download")
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "proof"
            with self.assertRaisesRegex(ValueError, "reference differs"):
                bootstrap.recover(self.policy, storage=WrongVersion(), recovery=None,
                                  verify_attestation=None, environment={}, output=output, now=self.now)
            self.assertFalse(output.exists())

    def test_manual_scope_is_required_before_credentials_or_storage(self):
        result = subprocess.run([__import__("sys").executable, str(Path(__file__).with_name("verify.py"))],
                                env={}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr.strip(), "DURABLE_RECOVERY_BOOTSTRAP_FAIL error_type=ValueError")


if __name__ == "__main__":
    unittest.main()
