"""Build admission distinguishes historical and recurring image assemblies."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import verify_monitor_roles as roles


class EmbeddedMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def policy(self, **values):
        (self.root / "policy.json").write_text(json.dumps(values))

    def test_historical_only_assembly_needs_no_monitor(self):
        self.policy(source="a" * 40)
        with patch.object(roles.subprocess, "run") as run:
            self.assertFalse(roles.qualify(self.root))
        run.assert_not_called()

    def test_recurring_assembly_runs_the_required_suite(self):
        self.policy(operation="export-recurring")
        monitor = self.root / "monitor"
        monitor.mkdir()
        # Exercise a real child process and propagate its result.
        (monitor / "test_monitor_roles.py").write_text("raise SystemExit(0)\n")
        self.assertTrue(roles.qualify(self.root))
        (monitor / "test_monitor_roles.py").write_text("raise SystemExit(1)\n")
        with self.assertRaises(subprocess.CalledProcessError):
            roles.qualify(self.root)

    def test_missing_recurring_monitor_fails_instead_of_skipping(self):
        self.policy(operation="export-recurring")
        with self.assertRaisesRegex(ValueError, "lacks embedded"):
            roles.qualify(self.root)

    def test_unknown_image_mode_fails(self):
        for operation in (None, "unexpected", False):
            with self.subTest(operation=operation):
                self.policy(operation=operation)
                with self.assertRaisesRegex(ValueError, "Unknown"):
                    roles.qualify(self.root)


if __name__ == "__main__":
    unittest.main()
