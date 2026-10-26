"""Pure cold operator admission tests; no provider calls, signing, or writes."""
from __future__ import annotations
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("cold_operator", Path(__file__).with_name("cold_recovery_operator.py"))
cold = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cold)


class ColdOperatorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 26, 3, tzinfo=timezone.utc)
        self.request = {"expires_at": (self.now + timedelta(minutes=40)).isoformat()}
        self.fence = {"payload": {"expires_at": self.request["expires_at"]}}
        self.target = {"project_id": "p", "environment_id": "e", "service_id": "s"}
        self.row = {"id": "parent", "status": "REMOVED", "deploymentStopped": True, "projectId": "p",
                    "environmentId": "e", "serviceId": "s",
                    "instances": [{"id": "replica", "status": "EXITED"}]}
        self.parent = {"request_id": "parent-request", "commit": "parent-source",
                       "railway": {"deployment_id": "parent"}, "grant_sha256": "parent-grant"}
        self.authority = {
            "active": {"request_id": "parent-request", "commit": "parent-source",
                       "deployment_id": "parent", "grant_sha256": "parent-grant", "state": "active"},
            "accepted_grants": {"parent-request.json": "parent-grant"},
            "activation_approvals": {}}
        self.basis = {"generated_at": self.now.isoformat(), "version": "0.16.9 estuary",
                      "provenance": [{"source": "fresh", "status": "OK"}], "faults": []}
        self.topology = {"latestDeployment": {"id": "failed-candidate"},
                         "activeDeployments": []}

    def test_deadline_is_original_generated_plus_900(self):
        generated = self.now - timedelta(seconds=500)
        got = cold.deadline(self.request, self.fence, generated.isoformat(), now=self.now)
        self.assertEqual(cold.utc(got), generated + timedelta(seconds=900))

    def test_each_shorter_expiry_caps_grant(self):
        for name in ("request", "fence", "admission"):
            with self.subTest(name=name):
                req, fence = copy.deepcopy(self.request), copy.deepcopy(self.fence)
                short = (self.now + timedelta(seconds=30)).isoformat()
                kwargs = {}
                if name == "request":
                    req["expires_at"] = short
                elif name == "fence":
                    fence["payload"]["expires_at"] = short
                else:
                    kwargs["admission_expiry"] = short
                self.assertEqual(cold.utc(cold.deadline(req, fence, self.now.isoformat(),
                                                       now=self.now, **kwargs)), cold.utc(short))

    def test_stale_future_naive_and_expired_rejected(self):
        for stamp in ((self.now - timedelta(seconds=901)).isoformat(),
                      (self.now + timedelta(seconds=1)).isoformat(), "2026-10-26T03:00:00"):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                cold.deadline(self.request, self.fence, stamp, now=self.now)
        self.request["expires_at"] = self.now.isoformat()
        with self.assertRaises(ValueError):
            cold.deadline(self.request, self.fence, self.now.isoformat(), now=self.now)

    def test_removed_parent_exact_scope_passes(self):
        cold.terminal_deployment(self.row, self.target, parent=True, reviewed_id="parent")

    def test_running_failed_empty_duplicate_and_wrong_scope_rejected(self):
        changes = [{"status": "SUCCESS"}, {"serviceId": "other"}, {"instances": []},
                   {"instances": [{"id": "replica", "status": "RUNNING"}]},
                   {"instances": [{"id": "replica", "status": "CRASHED"}]},
                   {"instances": self.row["instances"] * 2}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                cold.terminal_deployment({**self.row, **change}, self.target, parent=True, reviewed_id="parent")

    def test_reviewed_stopped_failed_successor_with_crashed_or_empty_inventory(self):
        failed = {**self.row, "id": "rollback", "status": "CRASHED",
                  "instances": [{"id": "failed", "status": "CRASHED"}]}
        for rows in (failed["instances"], []):
            cold.terminal_deployment({**failed, "instances": rows}, self.target, reviewed_id="rollback")
        for update in ({"deploymentStopped": False}, {"deploymentStopped": None},
                       {"id": "other"}, {"instances": [{"id": "r", "status": "RUNNING"}]}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                cold.terminal_deployment({**failed, **update}, self.target, reviewed_id="rollback")
        with self.assertRaises(ValueError):
            cold.terminal_deployment(failed, self.target)
        with self.assertRaises(ValueError):
            cold.terminal_deployment(failed, self.target, parent=True, reviewed_id="rollback")

    def test_topology_crashed_instance_requires_prior_stopped_failure_readback(self):
        topology = {"latestDeployment": {"id": "rollback"}, "activeDeployments": [
            {"id": "rollback", "deploymentStopped": True,
             "instances": [{"id": "r", "status": "CRASHED"}]}]}
        with self.assertRaises(ValueError):
            cold.topology_admitted(topology, ["parent", "rollback"])
        cold.topology_admitted(topology, ["parent", "rollback"], crashed_retired={"rollback"})
        topology["activeDeployments"][0]["deploymentStopped"] = False
        with self.assertRaises(ValueError):
            cold.topology_admitted(topology, ["parent", "rollback"], crashed_retired={"rollback"})

    def test_existing_approval_or_proof_blocks_before_signing_or_provider(self):
        for ending in (".activate.json", ".activate.proof.json",
                       ".source-stopped.json", ".source-stopped.proof.json"):
            with self.subTest(ending=ending), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / ("r" + ending)).write_text("{}")
                op = types.SimpleNamespace(ROOT=root, sign=mock.Mock())
                operation = "source-proof" if "source-stopped" in ending else "grant"
                with mock.patch.object(cold, "request_from", return_value={"request_id": "r"}), \
                     mock.patch.object(cold, "preflight") as preflight:
                    with self.assertRaisesRegex(ValueError, "without signing"):
                        cold.approve(op, None, operation, root, "deployment")
                    op.sign.assert_not_called()
                    preflight.assert_not_called()

    def test_provider_requires_authenticated_stopped_state_and_scope(self):
        op = types.SimpleNamespace(PROJECT="p", ENVIRONMENT="e", SERVICE="s",
                                   bindings=types.SimpleNamespace(sha=lambda value, n: len(value) == n))
        row = {**self.row, "meta": {"imageDigest": "sha256:" + "a" * 64}}
        api = mock.Mock(return_value={"deployment": row})
        self.assertTrue(cold.provider(op, types.SimpleNamespace(api=api), "parent")["deploymentStopped"])
        self.assertIn("deploymentStopped", api.call_args.args[0])
        for update in ({"deploymentStopped": None}, {"projectId": "other"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                cold.provider(op, types.SimpleNamespace(api=lambda *a: {"deployment": {**row, **update}}), "parent")

    def test_topology_accepts_only_reviewed_terminal_history(self):
        cold.topology_admitted(self.topology, ["parent", "failed-candidate"])
        self.topology["activeDeployments"] = [
            {"id": "parent", "deploymentStopped": True, "instances": self.row["instances"]}]
        cold.topology_admitted(self.topology, ["parent", "failed-candidate"])

    def test_topology_rejects_unreviewed_latest_live_or_starting_writer(self):
        cases = [
            {"latestDeployment": {"id": "unknown"}, "activeDeployments": []},
            {"latestDeployment": {"id": "parent"}, "activeDeployments": [
                {"id": "parent", "deploymentStopped": False, "instances": self.row["instances"]}]},
            {"latestDeployment": {"id": "parent"}, "activeDeployments": [
                {"id": "parent", "deploymentStopped": True,
                 "instances": [{"id": "r", "status": "INITIALIZING"}]}]}]
        for topology in cases:
            with self.subTest(topology=topology), self.assertRaises(ValueError):
                cold.topology_admitted(topology, ["parent"])

    def test_exact_candidate_allowed_with_terminal_parent(self):
        self.topology["latestDeployment"] = {"id": "new"}
        self.topology["activeDeployments"] = [
            {"id": "new", "deploymentStopped": False, "instances": [{"id": "r", "status": "RUNNING"}]}]
        cold.topology_admitted(self.topology, ["parent"], candidate="new")
        self.topology["activeDeployments"][0]["instances"].append({"id": "r2", "status": "RUNNING"})
        with self.assertRaises(ValueError):
            cold.topology_admitted(self.topology, ["parent"], candidate="new")

    def test_authority_rejects_new_grant_pending_pointer_or_approval(self):
        cold.authority_admitted(self.authority, self.authority, self.parent)
        for field, key, value in (("accepted_grants", "new.json", "new"),
                                  ("activation_approvals", "new.activate.json", "new"),
                                  ("active", "state", "pending")):
            changed = copy.deepcopy(self.authority)
            changed[field][key] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                cold.authority_admitted(changed, self.authority, self.parent)

    def test_even_consistent_readback_cannot_change_parent(self):
        changed = copy.deepcopy(self.authority)
        changed["active"]["deployment_id"] = "old-image-new-deployment"
        with self.assertRaises(ValueError):
            cold.authority_admitted(changed, changed, self.parent)
        changed = copy.deepcopy(self.authority)
        changed["accepted_grants"] = {}
        with self.assertRaises(ValueError):
            cold.authority_admitted(changed, changed, self.parent)

    def test_capture_preserves_real_signed_history_without_fabricated_counts(self):
        actual = copy.deepcopy(self.authority)
        actual["accepted_grants"]["older.json"] = "older-signed-grant"
        actual["activation_approvals"]["older.activate.json"] = "older-signed-grant"
        cold.capture_admitted(actual, self.parent, {"request_id": "new"}, ["failed"])
        self.assertEqual(len(actual["accepted_grants"]), 2)

    def test_capture_rejects_candidate_and_never_authorized_grants(self):
        for identifier in ("new", "failed"):
            for field, suffix in (("accepted_grants", ".json"),
                                  ("activation_approvals", ".activate.json")):
                actual = copy.deepcopy(self.authority)
                actual[field][identifier + suffix] = "unexpected"
                with self.subTest(identifier=identifier, field=field), self.assertRaises(ValueError):
                    cold.capture_admitted(actual, self.parent, {"request_id": "new"}, ["failed"])

    def test_capture_cannot_be_replayed_on_another_request_source_or_deployment(self):
        request = {"request_id": "new", "commit": "new-source",
                   "parent": {"activation_sha256": "parent-hash"}}
        receipt = {"schema": "seiche.cold-authority-capture.v1", "status": "CAPTURED",
                   "request_id": "new", "source": "new-source", "deployment": "new-deployment",
                   "parent_activation_sha256": "parent-hash", "authority": self.authority}
        self.assertEqual(cold.validate_capture(receipt, request, "new-deployment", self.parent),
                         self.authority)
        for field in ("request_id", "source", "deployment", "parent_activation_sha256"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                cold.validate_capture({**receipt, field: "other"}, request, "new-deployment", self.parent)

    def test_exact_cold_health_passes_new_version_only(self):
        observed = {"body": self.basis}
        cold.health_admitted(observed, observed, self.basis, version="0.16.9 estuary")
        with self.assertRaises(ValueError):
            cold.health_admitted(observed, observed, self.basis, version="0.16.8 estuary")

    def test_changed_clock_provenance_faults_or_version_rejected(self):
        for field, value in (("generated_at", "stale"), ("provenance", []),
                             ("faults", [{"source": "other"}]), ("version", "0.16.8 estuary")):
            changed = copy.deepcopy(self.basis)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                cold.health_admitted({"body": self.basis}, {"body": changed},
                                     self.basis, version="0.16.9 estuary")

    def test_upload_identity_requires_single_exact_uuid(self):
        identifier = "898a0407-91d8-45b8-9392-2e555cf318ce"
        self.assertEqual(cold.upload_identity(json.dumps({"deploymentId": identifier}).encode()), identifier)
        for raw in (b"", b'{"deploymentId":"garbage"}', b'{}\n{}',
                    (json.dumps({"deploymentId": identifier}) + '\n{"deploymentId":"e8b714fa-6e8b-46a2-9caa-a2cdad986d7e"}').encode()):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                cold.upload_identity(raw)

    def test_claimed_upload_stops_before_any_provider_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "application-submission-intent.json").write_text("{}")
            op = types.SimpleNamespace(ROOT=root)
            with mock.patch.object(cold, "request_from") as request:
                with self.assertRaisesRegex(ValueError, "reconcile"):
                    cold.submit(op, None, root / "context")
                request.assert_not_called()

    def test_capture_remote_program_compiles_and_keeps_actual_inventory(self):
        request = {"request_id": "new"}
        state = {"request": request, "activation": None, "authority": self.authority}
        def remote(replica, program):
            self.assertEqual(replica, "replica")
            compile(program, "<authenticated-remote-reader>", "exec")
            return state
        op = types.SimpleNamespace(remote=remote)
        with mock.patch.object(cold, "cold_policy",
                               return_value=(["parent"], {"never_authorized_requests": ["failed"]}, self.parent)):
            self.assertEqual(cold.observe_state(op, request, "deployment", "replica", capture=True), state)

    def test_source_proof_state_does_not_require_cold_compute(self):
        request = {"request_id": "r"}
        code = cold.state_code(request, "d")
        compile(code + "print(json.dumps(state))", "<state>", "exec")
        self.assertNotIn("_read_artifact", code)
        cold_code = cold.state_code(request, "d", cold=True)
        compile(cold_code + "print(json.dumps(state))", "<state>", "exec")
        self.assertIn("cold.validate_artifact", cold_code)
        self.assertIn("application-grants", cold_code)
        self.assertIn("*.activate.json", cold_code)
        self.assertIn("app.validate_approval(envelope,\'activate\')", cold_code)
        self.assertIn("payload[\'railway\']", cold_code)


if __name__ == "__main__":
    unittest.main()

