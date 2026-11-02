"""Native timing follows the reviewed workflow without changing proof bodies."""

import copy
from datetime import date
import json
from pathlib import Path
import unittest
from unittest import mock

import yaml

import prepare
import verify


def document(minutes=300, readiness=9000, download=200):
    return {"jobs": {"export-recovery": {"timeout-minutes": minutes, "steps": [{
        "name": prepare.RECURRING_STEPS["export-native.sh"],
        "run": (f"download_expires_at=$(date -u -d '+{download} minutes' +%Y-%m-%dT%H:%M:%SZ)\n"
                f"recovery_ready_deadline=$((SECONDS + {readiness}))\n"),
    }]}}}


class TimingTests(unittest.TestCase):
    def test_export_start_date_is_canonical_bounded_and_reproducible_later(self):
        self.assertIsNone(prepare.export_start_date(None, date(2026, 10, 6)))
        for today in (date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8)):
            self.assertEqual(prepare.export_start_date("2026-10-07", today), date(2026, 10, 7))
        for value in (True, 20261007, "", "2026-1-07", "20261007", "2026-02-30", "2026-10-08"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prepare.export_start_date(value, date(2026, 10, 6))

    def test_real_reviewed_workflow_defines_the_native_timing_contract(self):
        path = verify.TRUSTED / prepare.WORKFLOW
        if not path.is_file():
            path = Path(__file__).resolve().parents[3] / prepare.WORKFLOW
        reviewed = yaml.safe_load(path.read_text())
        expected = {"job_seconds": 18000, "readiness_seconds": 9000, "download_seconds": 12000}
        if (verify.ROOT / "policy.json").is_file():
            policy = json.loads((verify.ROOT / "policy.json").read_text())
            expected = policy.get("export_timing", {
                "job_seconds": 5400, "readiness_seconds": 2700, "download_seconds": 6600})
        self.assertEqual(prepare.workflow_timing(reviewed), expected)
        self.assertIn(prepare.WORKFLOW, prepare.admitted_source_paths([prepare.WORKFLOW]))

    def test_invalid_or_unbounded_workflow_timing_is_rejected(self):
        for minutes, readiness, download in ((True, 9000, 200), (301, 9000, 200), (300, 9001, 200),
                                             (150, 9000, 200), (0, 1, 200), (300, 0, 200),
                                             (300, 9000, 179), (300, 9000, 241), (300, 4500, 104)):
            with self.subTest(minutes=minutes, readiness=readiness, download=download), \
                    self.assertRaises(ValueError):
                prepare.workflow_timing(document(minutes, readiness, download))

    def test_credential_must_outlast_readiness_within_the_application_ceiling(self):
        # 2026-11-02: a 3.9 GB capture logged its receipt 85 minutes after the
        # request and is growing; readiness waits 150 and the credential lasts 200.
        self.assertEqual(prepare.workflow_timing(document()),
                         {"job_seconds": 18000, "readiness_seconds": 9000, "download_seconds": 12000})
        self.assertEqual(prepare.workflow_timing(document(300, 9000, 180))["download_seconds"], 10800)
        self.assertEqual(prepare.workflow_timing(document(300, 9000, 240))["download_seconds"], 14400)
        missing = document()
        missing["jobs"]["export-recovery"]["steps"][0]["run"] = "recovery_ready_deadline=$((SECONDS + 9000))\n"
        with self.assertRaisesRegex(ValueError, "credential lifetime is ambiguous"):
            prepare.workflow_timing(missing)

    def test_the_container_outlasts_the_longest_admitted_job(self):
        dockerfile = (Path(__file__).resolve().parent / "Dockerfile").read_text()
        command = json.loads(dockerfile.split("\nCMD ", 1)[1].splitlines()[0])
        self.assertEqual(command[:3], ["timeout", "--signal=TERM", "--kill-after=30"])
        self.assertGreaterEqual(int(command[3]), prepare.MAX_JOB_MINUTES * 60 + 60)

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
        value = document(90, 2700, 110)
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
                "recovery_ready_deadline=$((SECONDS + 9000))").replace(
                "download_expires_at=$(date -u -d '+110 minutes'",
                "download_expires_at=$(date -u -d '+200 minutes'"))
            for key in ("restore-native.sh", "seal-native.sh"):
                self.assertEqual(new[key], old[key])
            changed = copy.deepcopy(value)
            changed["jobs"]["export-recovery"]["steps"][0]["run"] = steps[0]["run"].replace("'+110 minutes'", "'+100 minutes'")
            with self.assertRaisesRegex(ValueError, "original download credential lifetime"):
                prepare.recurring_scripts(changed, timing=prepare.workflow_timing(document()))
            steps[0]["run"] = steps[0]["run"].replace("2700", "3000")
            with self.assertRaisesRegex(ValueError, "original receipt readiness"):
                prepare.recurring_scripts(value, timing=prepare.workflow_timing(document()))
