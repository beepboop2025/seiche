"""Boundary tests for the protected consumer; all keys and storage are fixtures."""

import copy
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import attest
import test_verify
import verify


class TailTests(unittest.TestCase):
    def setUp(self):
        fixture = test_verify.ExecutionIndexTests()
        fixture.setUp()
        self.fixture = fixture
        self.policy, self.payload, self.now = fixture.policy, copy.deepcopy(fixture.payload), fixture.now
        self.policy["tail_source"] = "1" * 40
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
        for name, body in bodies.items():
            self.payload[name].update(sha256=verify.digest(body), size=len(body))
        offsite = {"bucket": self.policy["storage_bucket"], "prefix": self.policy["storage_prefix"],
                   "objects": {**{str(i): {} for i in range(13)}, **{verify.INDEX_OBJECTS[name]: self.payload[name] for name in bodies}},
                   "recovery_receipt_sha256": self.payload["recovery_receipt"]["sha256"],
                   "reverse_restore_proof_sha256": self.payload["reverse_restore_proof"]["sha256"]}
        bodies["offsite_receipt"] = verify.canonical(offsite)
        self.payload["offsite_receipt"].update(sha256=verify.digest(bodies["offsite_receipt"]), size=len(bodies["offsite_receipt"]))
        self.bodies = bodies
        installation = {name: self.policy[name] if name == "execution_public_key" else self.payload[name] for name in attest.INSTALLATION_BINDINGS}
        installation.update(schema="seiche.railway-recovery-installation.v1", purpose="native_recovery_evidence_only",
                            observed_at=(self.now - timedelta(hours=2)).isoformat(), api_proof_sha256="f" * 64,
                            authority_changed=False, can_publish=False, can_execute=False)
        self.installation = verify.canonical(installation), b"fixture-signature"
        self.policy.update(installation_sha256=verify.digest(self.installation[0]), installation_signature_sha256=verify.digest(self.installation[1]))
        self.index_key = self.policy["storage_prefix"] + "/native-executions/2026-09-08/index.json"
        self.original_validator = mock.Mock()
        self.recovery = types.SimpleNamespace(validate_offsite_receipt=self.original_validator)

    def transport(self, payload=None, *, tampered=False):
        payload = payload or self.payload
        envelope = self.fixture.signed(payload)
        if tampered:
            envelope["payload"] = {**envelope["payload"], "controller_source": "f" * 40}
        index = verify.canonical(envelope)
        references = {value["key"]: value for name, value in self.payload.items() if name in verify.INDEX_OBJECTS}
        references[self.index_key] = {"key": self.index_key, "version_id": "index-v1", "sha256": verify.digest(index), "size": len(index)}
        bodies = {verify.INDEX_OBJECTS[name]: body for name, body in self.bodies.items()}
        bodies["proof/native-index.json"] = index
        calls = []
        def head(key, version=None):
            calls.append((key, version))
            if key not in references:
                raise ValueError("required object absent")
            item = references[key]
            return dict(item), {"VersionId": item["version_id"], "ContentLength": item["size"],
                                "Metadata": {"sha256": item["sha256"]}, "ObjectLockMode": "COMPLIANCE",
                                "SSECustomerAlgorithm": "AES256", "ObjectLockRetainUntilDate": (self.now + timedelta(days=90)).isoformat()}
        storage = types.SimpleNamespace(head=mock.Mock(side_effect=head), fetch=mock.Mock(side_effect=lambda name, ref: bodies[name]),
                                        references=references, bodies=bodies, calls=calls)
        health_body = {"status": "ready", "mode": "production", "version": "fixture", "faults": [], "provenance": [{}], "generated_at": self.now.isoformat()}
        headers = {"x-seiche-railway-authority": "production", "x-seiche-railway-deployment": payload["application_deployment_id"], "x-seiche-release-sha": payload["application_source"]}
        deployment = {"id": payload["application_deployment_id"], "status": "SUCCESS",
                      **{field + "Id": self.policy["application_" + field + "_id"] for field in ("project", "environment", "service")},
                      "instances": [{"id": payload["application_replica_id"], "status": "RUNNING"}]}
        live = types.SimpleNamespace(health=mock.Mock(return_value=(health_body, headers)), api=mock.Mock(return_value={"deployment": deployment}))
        return storage, live

    def prepare(self, storage, live, **changes):
        return attest.prepare_subjects(self.policy, storage, live, self.recovery, installation=changes.get("installation", self.installation),
                                       now=self.now, clock=changes.get("clock", lambda: self.now), verify_signature=lambda body, signature: None)

    def test_only_two_original_subject_bytes_are_released_after_full_validation(self):
        storage, live = self.transport()
        subjects, proof = self.prepare(storage, live)
        self.assertEqual(subjects, {verify.INDEX_OBJECTS[name]: self.bodies[name] for name in ("recovery_receipt", "offsite_receipt")})
        self.assertFalse(proof["restore_executed_here"])
        self.assertIn("installation-time", proof["controller_identity_proof"])
        self.original_validator.assert_called_once()
        self.assertEqual(len(live.api.call_args_list), 2)
        self.assertEqual([key for key, version in storage.calls if version is None], [self.index_key, self.index_key])

    def test_tampered_and_validly_signed_stale_indexes_release_no_subjects(self):
        for options in ({"tampered": True}, {"payload": {**self.payload, "date": "2026-09-07"}}):
            storage, live = self.transport(**options)
            with self.assertRaises(Exception):
                self.prepare(storage, live)
            self.assertEqual(storage.fetch.call_count, 1)

    def test_missing_object_or_wrong_immutable_version_fails_without_fallback(self):
        for missing in (True, False):
            storage, live = self.transport()
            key = self.payload["offsite_receipt"]["key"]
            if missing:
                del storage.references[key]
            else:
                storage.references[key] = {**storage.references[key], "version_id": "other-version"}
            with self.assertRaises(ValueError):
                self.prepare(storage, live)
            self.assertFalse(any("2026-09-07" in key for key, _ in storage.calls))

    def test_changed_daily_index_or_midnight_or_runtime_is_rejected(self):
        storage, live = self.transport()
        original_head = storage.head.side_effect
        def changing_head(key, version=None):
            ref, head = original_head(key, version)
            if key == self.index_key and len(storage.calls) > 1:
                ref["version_id"] = "replacement-index"
            return ref, head
        storage.head.side_effect = changing_head
        with self.assertRaises(ValueError):
            self.prepare(storage, live)
        storage, live = self.transport()
        with self.assertRaises(ValueError):
            self.prepare(storage, live, clock=lambda: self.now + timedelta(hours=8))
        storage, live = self.transport()
        before = live.api.return_value
        after = copy.deepcopy(before)
        after["deployment"]["instances"][0]["id"] = "22222222-2222-4222-8222-222222222222"
        live.api.side_effect = [before, after]
        with self.assertRaises(ValueError):
            self.prepare(storage, live)

    def test_missing_or_wrong_installation_binding_rejects_signed_index(self):
        for name in ("controller_deployment_id", "controller_image_digest", "execution_public_key"):
            value = json.loads(self.installation[0])
            value[name] = "wrong"
            body = verify.canonical(value)
            # Even a separately approved digest still needs the exact tuple and signature.
            self.policy["installation_sha256"] = verify.digest(body)
            storage, live = self.transport()
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.prepare(storage, live, installation=(body, self.installation[1]))
        with self.assertRaises(ValueError):
            self.prepare(*self.transport(), installation=(b"{}", b""))

    def test_original_workflow_main_and_reviewed_checkout_are_required(self):
        environment = {"GITHUB_REPOSITORY": attest.REPOSITORY, "GITHUB_REF": "refs/heads/main",
                       "GITHUB_WORKFLOW_REF": attest.WORKFLOW + "@refs/heads/main", "GITHUB_EVENT_NAME": "schedule", "GITHUB_SHA": "2" * 40}
        attest.admit_github(environment, self.policy, "1" * 40)
        for name, value in (("GITHUB_REF", "refs/pull/1/merge"), ("GITHUB_EVENT_NAME", "pull_request_target"),
                            ("GITHUB_REPOSITORY", "fork/seiche"), ("GITHUB_WORKFLOW_REF", "other/workflow@refs/heads/main")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                attest.admit_github({**environment, name: value}, self.policy, "1" * 40)
        with self.assertRaises(ValueError):
            attest.admit_github(environment, self.policy, "3" * 40)

    def test_malformed_deployment_shapes_and_multiple_replicas_fail_closed(self):
        for deployment in (None, [], {}, {"id": "wrong"}):
            storage, live = self.transport()
            live.api.return_value = {"deployment": deployment}
            with self.subTest(deployment=deployment), self.assertRaises(ValueError):
                self.prepare(storage, live)
            storage.fetch.assert_not_called()
        for instances in (None, {}, [], [None], [{"id": {}, "status": "RUNNING"}],
                          [{"id": self.payload["application_replica_id"], "status": "RUNNING"}] * 2):
            storage, live = self.transport()
            live.api.return_value["deployment"]["instances"] = instances
            with self.subTest(instances=instances), self.assertRaises(ValueError):
                self.prepare(storage, live)
            storage.fetch.assert_not_called()

    def test_complete_local_module_and_governance_manifest_is_required(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            backend = ["backend/seiche/deferred.py", "backend/governance/authority.json"]
            paths = ["deploy/railway-ci/recovery-controller/attest.py", "deploy/railway-ci/recovery-controller/verify.py",
                     "deploy/railway-ci/recovery-controller/requirements.lock", "ops/railway/resume_recovery.py",
                     "ops/deploy/seiche-s3-object-lock.sh", *backend]
            for item in paths:
                path = root / item
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            inputs = {path: verify.digest(b"fixture") for path in paths}
            attest.validate_tail_inputs(root, inputs, backend)
            with self.assertRaises(ValueError):
                attest.validate_tail_inputs(root, {k: v for k, v in inputs.items() if k != backend[0]}, backend)
            (root / backend[1]).write_bytes(b"changed authority")
            with self.assertRaises(ValueError):
                attest.validate_tail_inputs(root, inputs, backend)

    def test_redirect_never_constructs_a_credentialed_followup(self):
        request = attest.urllib.request.Request(attest.ORIGIN + "/api/health", headers={"X-Seiche-Edge-Token": "fixture"})
        self.assertIsNone(attest.NoRedirect().redirect_request(request, None, 302, "redirect", {}, "https://other.invalid"))

    def test_optional_missing_index_never_hides_access_network_or_parse_errors(self):
        for error in (ValueError("access denied"), TimeoutError("network"), json.JSONDecodeError("bad", "x", 0)):
            storage = types.SimpleNamespace(head=mock.Mock(side_effect=error))
            with self.subTest(error=type(error)), self.assertRaises(type(error)):
                attest.fetch_index(storage, self.policy, self.now, allow_missing=True)
        storage = types.SimpleNamespace(head=mock.Mock(side_effect=attest.MissingObject("404")))
        self.assertIsNone(attest.fetch_index(storage, self.policy, self.now, allow_missing=True))
        with self.assertRaises(attest.MissingObject):
            attest.fetch_index(storage, self.policy, self.now)

    def test_aws_missing_classifier_is_narrow_and_credentials_are_separated(self):
        storage = attest.Storage.__new__(attest.Storage)
        storage.key_path = Path("/tmp/fixture-key")
        storage.env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "AWS_ACCESS_KEY_ID": "fixture",
                       "AWS_SECRET_ACCESS_KEY": "fixture", "AWS_DEFAULT_REGION": "fixture", "AWS_EC2_METADATA_DISABLED": "true",
                       "S3_ENDPOINT": "https://fixture.invalid", "S3_BUCKET": "fixture-bucket",
                       "RAILWAY_TOKEN": "must-not-pass", "GITHUB_TOKEN": "must-not-pass", "S3_SSE_C_KEY_B64": "must-not-pass"}
        for message, expected in ((b"An error occurred (404) when calling the HeadObject operation: Not Found", attest.MissingObject),
                                  (b"An error occurred (NoSuchKey) when calling the HeadObject operation: Missing", attest.MissingObject),
                                  (b"An error occurred (403) when calling the HeadObject operation: AccessDenied", ValueError),
                                  (b"An error occurred (NoSuchBucket) when calling the HeadObject operation: Missing", ValueError),
                                  (b"Connection error HTTP 404", ValueError)):
            with self.subTest(message=message), mock.patch.object(attest.subprocess, "run", return_value=types.SimpleNamespace(returncode=1, stdout=b"", stderr=message)) as run:
                with self.assertRaises(expected) as caught:
                    storage.head(self.index_key)
                if expected is ValueError:
                    self.assertNotIsInstance(caught.exception, attest.MissingObject)
                self.assertFalse({"RAILWAY_TOKEN", "GITHUB_TOKEN", "S3_SSE_C_KEY_B64"} & set(run.call_args.kwargs["env"]))

    def test_railway_reader_never_inherits_storage_or_github_secrets(self):
        live = attest.Live("fixture-production-token", "fixture-edge-token", Path("/tmp"))
        with mock.patch.dict(attest.os.environ, {"GITHUB_TOKEN": "must-not-pass", "AWS_SECRET_ACCESS_KEY": "must-not-pass"}), \
                mock.patch.object(attest.subprocess, "run", return_value=types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"denied")) as run:
            with self.assertRaises(ValueError):
                live.api("query{__typename}", {})
            self.assertEqual(set(run.call_args.kwargs["env"]), {"PATH", "HOME", "RAILWAY_TOKEN"})

    def test_real_ssh_installation_signature_has_exact_owner_key_and_domain(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / "key")], check=True, capture_output=True)
            body = root / "receipt"
            body.write_bytes(self.installation[0])
            subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(root / "key"), "-n", attest.INSTALLATION_DOMAIN, str(body)], check=True, capture_output=True)
            signature = (root / "receipt.sig").read_bytes()
            with mock.patch.object(attest, "OWNER_PUBLIC", (root / "key.pub").read_text().strip()):
                attest.owner_signature(body.read_bytes(), signature)
                with self.assertRaises(ValueError):
                    attest.owner_signature(body.read_bytes() + b" ", signature)
                with mock.patch.object(attest, "INSTALLATION_DOMAIN", "production-control"), self.assertRaises(ValueError):
                    attest.owner_signature(body.read_bytes(), signature)
            with self.assertRaises(ValueError):
                attest.owner_signature(body.read_bytes(), signature)


if __name__ == "__main__":
    unittest.main()
