"""Real signed-index fixtures with independent scheduled provider identity checks."""

import copy
from datetime import timedelta
import json
import types
import unittest
from unittest import mock

import attest_live
import test_attest
import verify


class ScheduledTailTests(unittest.TestCase):
    def setUp(self):
        self.base = test_attest.TailTests()
        self.base.setUp()
        self.policy = self.base.policy
        self.policy["controller_deployment_id"] = self.base.payload[
            "controller_deployment_id"
        ]
        self.payload = copy.deepcopy(self.base.payload)
        self.payload["controller_deployment_id"] = (
            "22222222-2222-4222-8222-222222222222"
        )
        self.payload["controller_replica_id"] = "33333333-3333-4333-8333-333333333333"
        self.provider = {
            "projectToken": {
                name + "Id": self.policy["controller_" + name + "_id"]
                for name in ("project", "environment")
            },
            "deployment": {
                "id": self.payload["controller_deployment_id"],
                "status": "SUCCESS",
                **{
                    name + "Id": self.policy["controller_" + name + "_id"]
                    for name in ("project", "environment", "service")
                },
                "createdAt": (self.base.now - timedelta(hours=1)).isoformat(),
                "meta": {"imageDigest": self.policy["controller_image_digest"]},
                "instances": [
                    {"id": self.payload["controller_replica_id"], "status": "EXITED"}
                ],
            },
        }

    def prepare(self, provider=None, *, verify_signature=lambda body, sig: None):
        storage, live = self.base.transport(self.payload)
        controller = types.SimpleNamespace(
            api=mock.Mock(return_value=provider or self.provider)
        )
        result = attest_live.prepare_subjects(
            self.policy,
            storage,
            live,
            self.base.recovery,
            controller=controller,
            installation=self.base.installation,
            now=self.base.now,
            clock=lambda: self.base.now,
            verify_signature=verify_signature,
        )
        return result, storage, controller

    def test_rotated_deployment_keeps_original_subject_bytes_and_signed_index(self):
        before = verify.canonical(self.payload)
        (subjects, proof), storage, controller = self.prepare()
        self.assertEqual(
            subjects,
            {
                verify.INDEX_OBJECTS[name]: self.base.bodies[name]
                for name in ("recovery_receipt", "offsite_receipt")
            },
        )
        self.assertEqual(verify.canonical(self.payload), before)
        self.assertEqual(
            proof["controller_provider"]["id"], self.payload["controller_deployment_id"]
        )
        self.assertEqual(controller.api.call_count, 2)
        self.assertEqual(storage.head.call_count, 5)
        self.assertFalse(proof["restore_executed_here"])
        self.base.original_validator.assert_called_once()

    def test_removed_successful_execution_retains_its_immutable_identity(self):
        value = copy.deepcopy(self.provider)
        value["deployment"]["status"] = "REMOVED"
        value["deployment"]["instances"][0]["status"] = "REMOVED"
        self.prepare(value)

    def test_unreviewed_provider_scope_image_replica_and_clock_fail(self):
        for failure in (
            "token",
            "deployment",
            "service",
            "image",
            "replica",
            "future",
            "failed",
        ):
            value = copy.deepcopy(self.provider)
            if failure == "token":
                value["projectToken"]["environmentId"] = "other"
            elif failure == "deployment":
                value["deployment"]["id"] = "other"
            elif failure == "service":
                value["deployment"]["serviceId"] = "other"
            elif failure == "image":
                value["deployment"]["meta"]["imageDigest"] = "sha256:" + "f" * 64
            elif failure == "replica":
                value["deployment"]["instances"][0]["id"] = "other"
            elif failure == "future":
                value["deployment"]["createdAt"] = (
                    self.base.now + timedelta(seconds=1)
                ).isoformat()
            else:
                value["deployment"]["status"] = "CRASHED"
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                self.prepare(value)

    def test_provider_change_after_download_releases_no_subjects(self):
        storage, live = self.base.transport(self.payload)
        changed = copy.deepcopy(self.provider)
        changed["deployment"]["meta"]["imageDigest"] = "sha256:" + "f" * 64
        controller = types.SimpleNamespace(
            api=mock.Mock(side_effect=[self.provider, changed])
        )
        with self.assertRaises(ValueError):
            attest_live.prepare_subjects(
                self.policy,
                storage,
                live,
                self.base.recovery,
                controller=controller,
                installation=self.base.installation,
                now=self.base.now,
                clock=lambda: self.base.now,
                verify_signature=lambda body, signature: None,
            )

    def test_provider_identity_does_not_replace_owner_approval(self):
        def reject(body, signature):
            raise ValueError("bad owner signature")

        with self.assertRaisesRegex(ValueError, "bad owner signature"):
            self.prepare(verify_signature=reject)
        value = json.loads(self.base.installation[0])
        value["controller_source"] = "f" * 40
        self.base.installation = verify.canonical(value), self.base.installation[1]
        with self.assertRaises(ValueError):
            self.prepare()


if __name__ == "__main__":
    unittest.main()
