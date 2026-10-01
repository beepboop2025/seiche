"""Native timing follows the reviewed workflow without changing proof bodies."""

import copy
import json
from pathlib import Path
import unittest
from unittest import mock

import yaml

import prepare
import verify


def document(minutes=120, readiness=4500):
    return {"jobs": {"export-recovery": {"timeout-minutes": minutes, "steps": [{
        "name": prepare.RECURRING_STEPS["export-native.sh"],
        "run": f"recovery_ready_deadline=$((SECONDS + {readiness}))\n",
    }]}}}


class TimingTests(unittest.TestCase):
    def test_real_reviewed_workflow_defines_the_native_timing_contract(self):
        path = verify.TRUSTED / prepare.WORKFLOW
        if not path.is_file():
            path = Path(__file__).resolve().parents[3] / prepare.WORKFLOW
        reviewed = yaml.safe_load(path.read_text())
        expected = {"job_seconds": 7200, "readiness_seconds": 4500}
        if (verify.ROOT / "policy.json").is_file():
            policy = json.loads((verify.ROOT / "policy.json").read_text())
            expected = policy.get("export_timing", {
                "job_seconds": 5400, "readiness_seconds": 2700})
        self.assertEqual(prepare.workflow_timing(reviewed), expected)
        self.assertIn(prepare.WORKFLOW, prepare.admitted_source_paths([prepare.WORKFLOW]))

    def test_invalid_or_unbounded_workflow_timing_is_rejected(self):
        for minutes, readiness in ((True, 4500), (121, 4500), (120, 4501),
                                   (75, 4500), (0, 1), (120, 0)):
            with self.subTest(minutes=minutes, readiness=readiness), self.assertRaises(ValueError):
                prepare.workflow_timing(document(minutes, readiness))

    def test_duplicate_readiness_or_export_steps_are_rejected(self):
        for duplicate_step in (True, False):
            value = document()
            steps = value["jobs"]["export-recovery"]["steps"]
            if duplicate_step:
                steps.append(copy.deepcopy(steps[0]))
            else:
                steps[0]["run"] *= 2
            with self.subTest(duplicate_step=duplicate_step), self.assertRaises(ValueError):
                prepare.workflow_timing(value)

    def test_adaptation_changes_only_reviewed_readiness_literal(self):
        # The existing private-header and continuity adaptations have their own
        # tests. Isolate timing so removal of a proof command cannot go unnoticed.
        header = '--header "X-Seiche-Edge-Token: $RAILWAY_EDGE_TOKEN"'
        value = document(90, 2700)
        steps = value["jobs"]["export-recovery"]["steps"]
        steps[0]["run"] += header + "\nvalidate_receipt\nverify_all_members\n"
        steps.extend({"name": title, "run": header + "\nvalidate_proof\n"}
                     for key, title in prepare.RECURRING_STEPS.items()
                     if key != "export-native.sh")
        with mock.patch.object(prepare, "private_download_transport", side_effect=lambda body: body), \
                mock.patch.object(prepare, "continuity_transport", side_effect=lambda body: body):
            old = prepare.recurring_scripts(value)
            new = prepare.recurring_scripts(value, timing=prepare.workflow_timing(document()))
            self.assertEqual(new["export-native.sh"], old["export-native.sh"].replace(
                "recovery_ready_deadline=$((SECONDS + 2700))",
                "recovery_ready_deadline=$((SECONDS + 4500))"))
            for key in ("restore-native.sh", "seal-native.sh"):
                self.assertEqual(new[key], old[key])
            steps[0]["run"] = steps[0]["run"].replace("2700", "3000")
            with self.assertRaisesRegex(ValueError, "original receipt readiness"):
                prepare.recurring_scripts(value, timing=prepare.workflow_timing(document()))
