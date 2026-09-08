import base64
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from datetime import datetime, timedelta, timezone


spec = importlib.util.spec_from_file_location("history", Path(__file__).with_name("fetch_recovery_logs.py"))
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)
NOW = datetime(2026, 9, 8, 14, tzinfo=timezone.utc)
DEPLOYMENT = "dea92575-4ffd-47bd-8ffe-b97a017fdd09"
RELEASE = "d" * 40
EXPECTED = {"id": DEPLOYMENT, "projectId": "b49753cb-7b80-4b0d-a3c3-124aecad769d",
            "environmentId": "b99f0925-c7a1-48bc-bb4d-3bdf65c1e8b9",
            "serviceId": "0f7760ea-a490-46cb-b288-cf82bdc31510"}


def row(kind="activation", *, at=None, source=RELEASE):
    envelope = {"kind": kind, "commit": source, "deployment_id": DEPLOYMENT,
                "request_id": "a" * 64}
    return {"message": history.MARKER + base64.b64encode(json.dumps(envelope).encode()).decode(),
            "timestamp": (at or NOW - timedelta(days=2)).isoformat(), "attributes": []}


class FakeAPI:
    def __init__(self):
        self.identity = {**EXPECTED, "status": "SUCCESS", "createdAt": (NOW - timedelta(days=2, hours=1)).isoformat(),
                         "instances": [{"id": "3dc203ef-b9e5-4a09-9faa-52b18235cddf", "status": "RUNNING"}]}
        self.recent = []
        self.older = [row()]
        self.calls = []
        self.identity_reads = 0
        self.change_replica = False

    def __call__(self, query, variables):
        self.calls.append(query)
        if "deploymentLogs(" not in query:
            self.identity_reads += 1
            result = copy.deepcopy(self.identity)
            if self.change_replica and self.identity_reads > 1:
                result["instances"][0]["id"] = "671eee65-8f3a-4bd9-b2d7-c3cac9894916"
            return {"deployment": result}
        return {"deploymentLogs": self.older if self.identity["createdAt"] in query else self.recent}


class HistoryTests(unittest.TestCase):
    def test_strict_monitor_window_never_expands(self):
        api = FakeAPI()
        body, proof = history.fetch(EXPECTED, RELEASE, bootstrap=False, now=NOW, call=api)
        self.assertEqual(body, b"")
        self.assertEqual(proof["mode"], "strict-26h")
        self.assertEqual(sum("deploymentLogs(" in q for q in api.calls), 1)

    def test_explicit_bootstrap_preserves_original_activation_timestamp(self):
        api = FakeAPI()
        body, proof = history.fetch(EXPECTED, RELEASE, bootstrap=True, now=NOW, call=api)
        self.assertEqual(json.loads(body)["timestamp"], api.older[0]["timestamp"])
        self.assertEqual(proof["mode"], "explicit-activation-bootstrap")
        self.assertIn(api.identity["createdAt"], api.calls[2])

    def test_fresh_pair_does_not_expand_history(self):
        api = FakeAPI()
        api.recent = [row("recovery_offsite_paired", at=NOW - timedelta(hours=1))]
        body, proof = history.fetch(EXPECTED, RELEASE, bootstrap=True, now=NOW, call=api)
        self.assertEqual(proof["mode"], "strict-26h")
        self.assertEqual(len(body.splitlines()), 1)

    def test_stale_pair_is_not_represented_as_fresh_recovery(self):
        api = FakeAPI()
        api.older.append(row("recovery_offsite_paired"))
        body, _ = history.fetch(EXPECTED, RELEASE, bootstrap=True, now=NOW, call=api)
        self.assertEqual(len(body.splitlines()), 1)
        envelope = json.loads(base64.b64decode(json.loads(body)["message"].removeprefix(history.MARKER)))
        self.assertEqual(envelope["kind"], "activation")

    def test_rejects_history_beyond_minimum_provider_retention(self):
        api = FakeAPI()
        api.identity["createdAt"] = (NOW - timedelta(days=4)).isoformat()
        with self.assertRaisesRegex(ValueError, "bounded provider retention"):
            history.fetch(EXPECTED, RELEASE, bootstrap=True, now=NOW, call=api)

    def test_rejects_wrong_project(self):
        api = FakeAPI()
        api.identity["projectId"] = EXPECTED["environmentId"]
        with self.assertRaisesRegex(ValueError, "identity differs"):
            history.fetch(EXPECTED, RELEASE, bootstrap=True, now=NOW, call=api)

    def test_rejects_foreign_source_activation(self):
        api = FakeAPI()
        api.older = [row(source="f" * 40)]
        with self.assertRaisesRegex(ValueError, "activation bootstrap is absent"):
            history.fetch(EXPECTED, RELEASE, bootstrap=True, now=NOW, call=api)

    def test_rejects_replica_change_during_read(self):
        api = FakeAPI()
        api.change_replica = True
        with self.assertRaisesRegex(ValueError, "runtime changed"):
            history.fetch(EXPECTED, RELEASE, bootstrap=True, now=NOW, call=api)

    def test_rejects_truncated_result(self):
        api = FakeAPI()
        api.recent = [row(at=NOW)] * history.ROW_LIMIT
        with self.assertRaisesRegex(ValueError, "row limit"):
            history.fetch(EXPECTED, RELEASE, bootstrap=True, now=NOW, call=api)

    def test_rejects_forged_timestamp_before_deployment(self):
        api = FakeAPI()
        api.older = [row(at=NOW - timedelta(days=3))]
        with self.assertRaisesRegex(ValueError, "outside the requested"):
            history.fetch(EXPECTED, RELEASE, bootstrap=True, now=NOW, call=api)

    def test_preserves_structured_message_attribute(self):
        api = FakeAPI()
        value = api.older[0]["message"]
        api.older[0]["message"] = ""
        api.older[0]["attributes"] = [{"key": "message", "value": json.dumps(value)}]
        body, _ = history.fetch(EXPECTED, RELEASE, bootstrap=True, now=NOW, call=api)
        self.assertEqual(json.loads(body)["message"], value)


if __name__ == "__main__":
    unittest.main()
